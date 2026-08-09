from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import re
from typing import Any, Callable, Iterable, Protocol

from ..domain.publication_readiness import (
    DEFAULT_COVERAGE_PROFILES,
    DerivedArtifactDependency,
    IndependentOriginRequirement,
    PublicationFailureCode,
    PublicationGateEvaluation,
    PublicationGateFailure,
    RepairKind,
    RepairStatus,
    ResearchRepairAction,
    ScopeInvalidationResult,
    SemanticQualityDimension,
    SemanticQualityEvaluation,
    SourceLineageNode,
    deduplicate_repairs,
    stable_contract_id,
)
from ..domain.research_integrity import (
    CandidateDiscoveryItem,
    EvidenceAdmissionPolicy,
    ResearchRequest,
)
from ..models import DemoQualityPolicy
from .research_requests import ResearchRequestDispatcher


_NUMBER_RE = re.compile(r"(?:\$|\b)\d+(?:\.\d+)?(?:%|x|\b)", re.I)
_MATERIAL = {"critical", "high", "material"}


@dataclass(frozen=True)
class RepairExecutionResult:
    action_id: str
    status: str
    summary: str
    admitted_evidence_ids: tuple[str, ...] = ()
    conclusion_changes: tuple[str, ...] = ()
    cost_usd: float = 0.0
    provider_failure: bool = False
    evidence_unavailable: bool = False
    credential_expired: bool = False


@dataclass(frozen=True)
class RepairExecutionContext:
    run: Any
    policy: DemoQualityPolicy
    audited_dossiers: tuple[Any, ...]
    evidence_hash: str
    recursion_depth: int
    event_recorder: Callable[[str, dict[str, Any]], None] | None = None


class RepairStrategy(Protocol):
    def execute(
        self,
        action: ResearchRepairAction,
        context: RepairExecutionContext,
    ) -> RepairExecutionResult: ...


class DurableRepairJobRepository(Protocol):
    def get(self, run_id: str, idempotency_key: str) -> RepairExecutionResult | None: ...

    def save(
        self,
        run_id: str,
        idempotency_key: str,
        result: RepairExecutionResult,
    ) -> None: ...


class InMemoryRepairJobRepository:
    def __init__(self) -> None:
        self._results: dict[tuple[str, str], RepairExecutionResult] = {}

    def get(self, run_id: str, idempotency_key: str) -> RepairExecutionResult | None:
        return self._results.get((run_id, idempotency_key))

    def save(
        self,
        run_id: str,
        idempotency_key: str,
        result: RepairExecutionResult,
    ) -> None:
        self._results[(run_id, idempotency_key)] = result


class RepairStrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, RepairStrategy] = {}

    def register(self, repair_kind: RepairKind | str, strategy: RepairStrategy) -> None:
        self._strategies[RepairKind(repair_kind).value] = strategy

    def resolve(self, repair_kind: RepairKind | str) -> RepairStrategy | None:
        return self._strategies.get(RepairKind(repair_kind).value)


class CallableRepairStrategy:
    """Small adapter for deterministic, revision, and closure application services."""

    def __init__(
        self,
        execute: Callable[[ResearchRepairAction, RepairExecutionContext], RepairExecutionResult],
    ) -> None:
        self._execute = execute

    def execute(
        self,
        action: ResearchRepairAction,
        context: RepairExecutionContext,
    ) -> RepairExecutionResult:
        return self._execute(action, context)


class ResearchRequestRepairStrategy:
    """Dispatch a repair through registered retrieval and the common gateway."""

    def __init__(
        self,
        dispatcher: ResearchRequestDispatcher,
        *,
        request_builder: Callable[[ResearchRepairAction, RepairExecutionContext], ResearchRequest],
        candidate_builder: Callable[
            [ResearchRepairAction, RepairExecutionContext],
            list[CandidateDiscoveryItem],
        ],
        policy_builder: Callable[
            [ResearchRepairAction, RepairExecutionContext],
            EvidenceAdmissionPolicy,
        ],
        admitted_evidence_sink: Callable[[Any, tuple[dict[str, Any], ...]], None],
    ) -> None:
        self.dispatcher = dispatcher
        self.request_builder = request_builder
        self.candidate_builder = candidate_builder
        self.policy_builder = policy_builder
        self.admitted_evidence_sink = admitted_evidence_sink

    def execute(
        self,
        action: ResearchRepairAction,
        context: RepairExecutionContext,
    ) -> RepairExecutionResult:
        request = self.request_builder(action, context)
        candidates = self.candidate_builder(action, context)
        execution = self.dispatcher.dispatch(
            request,
            candidates,
            self.policy_builder(action, context),
        )
        admitted = tuple(
            result.evidence
            for result in execution.admission_results
            if result.decision.evidence_id and result.evidence is not None
        )
        admitted_ids = tuple(
            str(result.decision.evidence_id)
            for result in execution.admission_results
            if result.decision.evidence_id
        )
        if admitted:
            self.admitted_evidence_sink(context.run, admitted)
        completed = bool(admitted_ids)
        return RepairExecutionResult(
            action_id=action.action_id,
            status=(
                RepairStatus.COMPLETED.value
                if completed else RepairStatus.BLOCKED.value
            ),
            summary=(
                request.resolution_summary
                or "Registered retrieval produced no admissible evidence."
            ),
            admitted_evidence_ids=admitted_ids,
            evidence_unavailable=not completed,
        )


@dataclass(frozen=True)
class RepairControllerResult:
    evaluation: PublicationGateEvaluation
    actions: tuple[ResearchRepairAction, ...]
    execution_results: tuple[RepairExecutionResult, ...]
    recursion_depth: int
    total_cost_usd: float
    stopping_reason: str


class SemanticQualityScorer:
    """Deterministic, dependency-addressable quality scoring service."""

    version = "semantic-quality-scorer-v1"

    def score(
        self,
        run: Any,
        audited_dossiers: Iterable[Any],
        *,
        threshold: int,
        triggered_by: str = "initial_evaluation",
    ) -> SemanticQualityEvaluation:
        audits = list(getattr(run, "claim_evidence_audits", []) or [])
        passed = sum(str(getattr(item, "status", "")) == "passed" for item in audits)
        evidence_score = 10 if not audits else round(10 * passed / len(audits))
        blockers = " ".join(
            str(blocker)
            for item in audits
            for blocker in getattr(item, "blockers", []) or []
        ).casefold()
        consistency_score = 10
        if any(token in blockers for token in ("period", "duration", "unit", "currency")):
            consistency_score = 4
        artifact = getattr(run, "final_artifact", None)
        payload = dict(getattr(artifact, "payload", {}) or {})
        summary = str(getattr(artifact, "summary", "") or "")
        mechanism_text = " ".join((summary, str(payload))).casefold()
        mechanism_score = 10 if any(
            token in mechanism_text for token in ("because", "driver", "mechanism", "margin", "cash")
        ) else 5 if summary else 0
        counter_score = 10 if any(
            token in mechanism_text for token in ("counter", "bear", "disprove", "risk")
        ) else 3
        dossiers = list(audited_dossiers)
        audited = [
            item for item in dossiers
            if str(getattr(item, "status", "")) in {
                "Audited Research Analog", "Frozen Demo Dossier"
            }
        ]
        analog_score = 10 if audited else 2 if dossiers else 0
        requirements = independent_origin_requirements(run, audited, required=2)
        provenance_score = 10 if requirements and all(item.satisfied for item in requirements) else 4 if audited else 0
        valuation_rows = list(getattr(run, "historical_valuation_observations", []) or [])
        valuation_score = 10 if any(
            getattr(item, "fcf_ttm", None) is not None
            and getattr(item, "enterprise_value", None) is not None
            for item in valuation_rows
        ) else 4 if valuation_rows else 0
        monitor_score = 10 if any(
            token in mechanism_text for token in ("monitor", "falsif", "trigger", "watch")
        ) else 3
        mapping_score = 4 if "unmapped metric" in mechanism_text else 10
        dimensions = (
            SemanticQualityDimension("evidence_completeness", evidence_score, 10, _dimension_status(evidence_score)),
            SemanticQualityDimension("citation_relevance", evidence_score, 10, _dimension_status(evidence_score)),
            SemanticQualityDimension("period_unit_consistency", consistency_score, 10, _dimension_status(consistency_score)),
            SemanticQualityDimension("mechanism_clarity", mechanism_score, 10, _dimension_status(mechanism_score)),
            SemanticQualityDimension("counter_thesis_quality", counter_score, 10, _dimension_status(counter_score)),
            SemanticQualityDimension("analog_usefulness", analog_score, 10, _dimension_status(analog_score)),
            SemanticQualityDimension("provenance_independence", provenance_score, 10, _dimension_status(provenance_score)),
            SemanticQualityDimension("valuation_completeness", valuation_score, 10, _dimension_status(valuation_score)),
            SemanticQualityDimension("monitoring_falsification", monitor_score, 10, _dimension_status(monitor_score)),
            SemanticQualityDimension("mapping_and_classification", mapping_score, 10, _dimension_status(mapping_score)),
        )
        total = sum(item.score for item in dimensions)
        critical = tuple(
            str(getattr(item, "claim_id", "material_claim"))
            for item in audits
            if str(getattr(item, "status", "")) == "failed"
            and str(getattr(item, "materiality", "high")).casefold() in _MATERIAL
        )
        previous = list(getattr(run, "semantic_quality_history", []) or [])
        evaluated_at = _utc_now()
        return SemanticQualityEvaluation(
            evaluation_id=stable_contract_id(
                "semantic-quality",
                getattr(run, "run_id", ""),
                getattr(run, "evidence_hash", ""),
                total,
                triggered_by,
                len(previous),
            ),
            run_id=str(getattr(run, "run_id", "") or ""),
            artifact_version=str(getattr(artifact, "artifact_id", "") or ""),
            evidence_hash=str(getattr(run, "evidence_hash", "") or ""),
            total_score=total,
            threshold=threshold,
            status="passed" if total >= threshold and not critical else "failed",
            dimensions=dimensions,
            critical_failures=critical,
            triggered_by=triggered_by,
            evaluated_at=evaluated_at,
            previous_evaluation_id=(previous[-1].evaluation_id if previous else ""),
        )


class PublicationGateEvaluator:
    version = "publication-gate-evaluator-v1"

    def __init__(self, scorer: SemanticQualityScorer | None = None) -> None:
        self.scorer = scorer or SemanticQualityScorer()

    def evaluate(
        self,
        ticker: str,
        *,
        run: Any,
        audited_dossiers: Iterable[Any],
        policy: DemoQualityPolicy,
        triggered_by: str = "publication_gate_evaluation",
    ) -> PublicationGateEvaluation:
        ticker = ticker.upper()
        run_id = str(getattr(run, "run_id", "") or "")
        failures: list[PublicationGateFailure] = []
        dossiers = list(audited_dossiers)
        status = str(getattr(run, "status", "") or "")
        lifecycle_state = str(getattr(run, "lifecycle_state", "") or "")
        execution_finished = status in {
            "completed", "publishable", "published", "completed_not_publishable"
        } or lifecycle_state in {
            "research_attempt_finished", "validation_pending", "repair_required",
            "repair_running", "revision_pending", "audit_pending", "closure_pending",
            "publishable", "published", "completed_not_publishable",
        }
        if policy.require_completed_run and not execution_finished:
            failures.append(_failure(
                PublicationFailureCode.RUN_INCOMPLETE,
                run_id,
                "Research execution has not reached a completed lifecycle state.",
                f"Stored run status is {status or 'missing'}.",
                RepairKind.COMPLETE_DETERMINISTIC_VALIDATION,
                "Only completed research attempts may enter publication review.",
            ))
        validation = str(getattr(run, "deterministic_validation_status", "not_run") or "not_run")
        if policy.require_deterministic_validation and validation != "passed":
            code = (
                PublicationFailureCode.DETERMINISTIC_VALIDATION_NOT_RUN
                if validation == "not_run"
                else PublicationFailureCode.DETERMINISTIC_VALIDATION_PARTIAL
            )
            failures.append(_failure(
                code,
                run_id,
                "Deterministic evidence validation is incomplete.",
                f"Validation status is {validation}.",
                RepairKind.COMPLETE_DETERMINISTIC_VALIDATION,
                "Publication requires passed deterministic validation before final audit.",
            ))
        primary = next(
            (item for item in getattr(run, "variants", []) or [] if getattr(item, "variant_id", "") == "deepseek_led"),
            None,
        )
        audit_decision = getattr(primary, "audit_decision", None)
        closure_state = _closure_state(run, audit_decision)
        if policy.require_closed_qa and closure_state not in {
            "closed", "closed_with_residual_risk"
        }:
            failures.append(_failure(
                PublicationFailureCode.FINAL_CLOSURE_NOT_PASSED,
                run_id,
                "Independent closure has not passed.",
                str(getattr(audit_decision, "decision", "No passing closure artifact exists.")),
                RepairKind.RERUN_CLOSURE,
                "Publication requires an eligible independent closure result.",
            ))
        failed_claims = [
            item for item in getattr(run, "claim_evidence_audits", []) or []
            if str(getattr(item, "status", "")) == "failed"
        ]
        if policy.require_no_failed_material_claims and failed_claims:
            failures.append(_failure(
                PublicationFailureCode.MATERIAL_CLAIM_VALIDATION_FAILED,
                run_id,
                f"{len(failed_claims)} material claim(s) failed evidence validation.",
                "; ".join(
                    str(blocker)
                    for item in failed_claims[:5]
                    for blocker in (getattr(item, "blockers", []) or [])[:2]
                ) or "Claim-level validation did not pass.",
                RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE,
                "Every material published claim must pass claim-to-evidence validation.",
                claim_ids=tuple(str(getattr(item, "claim_id", "")) for item in failed_claims),
                source_families=("historical_filing",),
            ))
        unresolved = [
            item for item in getattr(run, "audit_findings", []) or []
            if str(getattr(item, "severity", "")).casefold() in _MATERIAL
            and not _finding_has_valid_response(item)
        ]
        if policy.require_closed_qa and unresolved:
            failures.append(_failure(
                PublicationFailureCode.MATERIAL_AUDIT_FINDING_UNADDRESSED,
                run_id,
                f"{len(unresolved)} material QA finding(s) remain unresolved.",
                "; ".join(str(getattr(item, "summary", "")) for item in unresolved[:5]),
                RepairKind.RESPOND_TO_AUDIT_FINDING,
                "Every material audit finding requires an accepted, evidenced rejection, or unresolved response.",
                finding_ids=tuple(str(getattr(item, "finding_id", "")) for item in unresolved),
            ))
        records = list(getattr(run, "normalized_sources", []) or [])
        if policy.require_no_promoted_quarantined_metadata and _promoted_quarantine_count(run, records):
            failures.append(_failure(
                PublicationFailureCode.QUARANTINED_CANDIDATE_PROMOTED,
                run_id,
                "Quarantined candidate metadata was promoted without registered retrieval.",
                "An admission record references metadata-only content without a completed retrieval pipeline.",
                RepairKind.NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION,
                "Only the common deterministic admission gateway may create evidence IDs.",
                retryable=False,
            ))
        eligible_dossiers = [
            item for item in dossiers
            if str(getattr(item, "status", "")) in {"Audited Research Analog", "Frozen Demo Dossier"}
        ]
        if policy.require_audited_analog and not eligible_dossiers:
            failures.append(_failure(
                PublicationFailureCode.ANALOG_INDEPENDENT_PROVENANCE_INSUFFICIENT,
                run_id,
                "No audited, decision-useful historical analog is available.",
                "The current artifact does not contain an eligible audited dossier.",
                RepairKind.RETRIEVE_INDEPENDENT_ANALOG_ORIGIN,
                "Publication policy requires at least one audited decision-useful analog.",
                source_families=("historical_filing", "quantitative_historical_price"),
            ))
        for requirement in independent_origin_requirements(
            run,
            eligible_dossiers,
            required=policy.require_independent_quantified_origins,
        ):
            if requirement.satisfied:
                continue
            failures.append(_failure(
                PublicationFailureCode.ANALOG_INDEPENDENT_PROVENANCE_INSUFFICIENT,
                run_id,
                "A quantified analog conclusion lacks independent provenance.",
                (
                    f"Analog {requirement.analog_id or 'unknown'} has "
                    f"{len(requirement.observed_origin_groups)} independent origin group(s); "
                    f"{requirement.required_origin_groups} required."
                ),
                RepairKind.RETRIEVE_INDEPENDENT_ANALOG_ORIGIN,
                "Quantified analog conclusions require independently originated support.",
                scope_type="analog",
                scope_id=requirement.analog_id,
                source_families=("historical_filing", "quantitative_historical_price", "industry_archive"),
            ))
        required_paths = _required_paths(ticker, run, policy)
        available_paths = {
            str(getattr(item, "metric", ""))
            for item in getattr(run, "historical_financial_paths", []) or []
            if str(getattr(item, "status", "")) == "available"
        }
        for missing_path in sorted(required_paths - available_paths):
            failures.append(_failure(
                PublicationFailureCode.REQUIRED_FINANCIAL_PATH_MISSING,
                run_id,
                f"Required financial path unavailable: {missing_path}.",
                f"The active coverage profile requires {missing_path}, but no available validated path exists.",
                RepairKind.COMPLETE_REQUIRED_FINANCIAL_PATH,
                "Publication requires the decision-critical financial paths in the active coverage profile.",
                scope_type="financial_path",
                scope_id=missing_path,
                source_families=("historical_filing",),
            ))
        gaps = " ".join(str(item) for item in getattr(run, "data_gaps", []) or []).casefold()
        if "mapping" in gaps and ("unresolved" in gaps or "failed" in gaps):
            failures.append(_failure(
                PublicationFailureCode.MAPPING_RESOLUTION_FAILED,
                run_id,
                "A material numeric mapping remains unresolved.",
                "The automated mapping ladder ended without a validated numeric mapping.",
                RepairKind.RESOLVE_NUMERIC_MAPPING,
                "Material numeric observations require a validated concept, period, unit, and source mapping.",
                source_families=("historical_filing",),
            ))
        threshold = int(policy.ticker_minimum_scores.get(ticker, 85))
        quality = self.scorer.score(
            run,
            eligible_dossiers,
            threshold=threshold,
            triggered_by=triggered_by,
        )
        provider_scorecard = getattr(primary, "quality_scorecard", None)
        provider_score = int(
            getattr(provider_scorecard, "total_score", quality.total_score)
        )
        has_versioned_quality_dependencies = bool(
            getattr(run, "semantic_quality_history", None)
            or getattr(run, "lifecycle_state", "")
            or getattr(run, "validation_completed_at", "")
        )
        # Historical artifacts predate the dependency-aware scorer and often do
        # not contain enough structured dimensions to reproduce their score.
        # Preserve their audited score until a repair/revision creates a
        # versioned quality snapshot; current runs remain constrained by both.
        effective_score = (
            min(quality.total_score, provider_score)
            if has_versioned_quality_dependencies
            else provider_score
        )
        provider_critical = tuple(getattr(provider_scorecard, "critical_failures", []) or [])
        if effective_score < threshold:
            failures.append(_failure(
                PublicationFailureCode.SEMANTIC_QUALITY_BELOW_THRESHOLD,
                run_id,
                f"Semantic quality is below the {threshold}/100 publication threshold.",
                f"Current deterministic/provider-constrained score is {effective_score}/100.",
                RepairKind.RECOMPUTE_SEMANTIC_QUALITY,
                "Publication requires the configured semantic-quality threshold.",
            ))
        if quality.critical_failures or provider_critical:
            failures.append(_failure(
                PublicationFailureCode.SEMANTIC_CRITICAL_FAILURE,
                run_id,
                "Semantic review contains one or more critical failures.",
                "; ".join((*quality.critical_failures, *provider_critical))[:1000],
                RepairKind.NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION,
                "Critical semantic failures must be repaired or the affected conclusion removed.",
            ))
        failures = _deduplicate_failures(failures)
        actions = compile_repair_actions(failures)
        prior = list(getattr(run, "publication_gate_evaluations", []) or [])
        evaluated_at = _utc_now()
        decision = "publishable" if not failures else "repair_required"
        evaluation = PublicationGateEvaluation(
            evaluation_id=stable_contract_id(
                "publication-evaluation",
                run_id,
                getattr(run, "evidence_hash", ""),
                policy.policy_version,
                tuple(item.failure_id for item in failures),
            ),
            run_id=run_id,
            artifact_version=str(getattr(getattr(run, "final_artifact", None), "artifact_id", "") or ""),
            evidence_hash=str(getattr(run, "evidence_hash", "") or ""),
            publication_policy_version=policy.policy_version,
            validation_policy_version=policy.validation_policy_version,
            schema_version=policy.artifact_schema_version,
            parser_version=policy.parser_version,
            evaluated_at=evaluated_at,
            decision=decision,
            failures=tuple(failures),
            repair_actions=actions,
            applicable_gate_codes=tuple(sorted({item.code for item in failures})),
            passed_gate_codes=() if failures else ("all_material_publication_gates",),
            supersedes_evaluation_id=(prior[-1].evaluation_id if prior else ""),
        )
        if hasattr(run, "semantic_quality_history"):
            run.semantic_quality_history.append(quality)
        if hasattr(run, "publication_gate_evaluations"):
            run.publication_gate_evaluations.append(evaluation)
        if hasattr(run, "repair_actions"):
            run.repair_actions = list(actions)
        if hasattr(run, "lifecycle_state"):
            if evaluation.publishable:
                run.lifecycle_state = "publishable"
                run.publishable_at = evaluated_at
            else:
                run.lifecycle_state = "repair_required"
        return evaluation


class ResearchRepairController:
    """Bounded, idempotent repair orchestration over registered strategies."""

    def __init__(
        self,
        evaluator: PublicationGateEvaluator | None = None,
        registry: RepairStrategyRegistry | None = None,
        repository: DurableRepairJobRepository | None = None,
    ) -> None:
        self.evaluator = evaluator or PublicationGateEvaluator()
        self.registry = registry or RepairStrategyRegistry()
        self.repository = repository or InMemoryRepairJobRepository()

    def repair_and_recheck(
        self,
        ticker: str,
        *,
        run: Any,
        audited_dossiers: Iterable[Any],
        policy: DemoQualityPolicy,
        max_depth: int,
        max_cost_usd: float | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        event_recorder: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> RepairControllerResult:
        dossiers = tuple(audited_dossiers)
        evaluation = self.evaluator.evaluate(
            ticker,
            run=run,
            audited_dossiers=dossiers,
            policy=policy,
            triggered_by="repair_controller_start",
        )
        if evaluation.publishable:
            return RepairControllerResult(evaluation, (), (), 0, 0.0, "all_material_gates_passed")
        if hasattr(run, "lifecycle_state"):
            run.lifecycle_state = "repair_running"
        results: list[RepairExecutionResult] = []
        actions = [replace(item) for item in evaluation.repair_actions]
        total_cost = 0.0
        stopping_reason = "configured_depth_limit"
        for depth in range(max(0, max_depth)):
            admitted_this_cycle: set[str] = set()
            progressed = False
            for action in _ordered_actions(actions):
                if cancel_requested and cancel_requested():
                    stopping_reason = "user_cancelled"
                    _apply_repair_stopping_state(run, stopping_reason)
                    return RepairControllerResult(evaluation, tuple(actions), tuple(results), depth, total_cost, stopping_reason)
                cached = self.repository.get(str(getattr(run, "run_id", "")), action.idempotency_key)
                if cached is not None:
                    results.append(cached)
                    admitted_this_cycle.update(cached.admitted_evidence_ids)
                    progressed = progressed or cached.status == RepairStatus.COMPLETED.value
                    continue
                if max_cost_usd is not None and total_cost >= max_cost_usd:
                    stopping_reason = "configured_cost_limit"
                    _apply_repair_stopping_state(run, stopping_reason)
                    return RepairControllerResult(evaluation, tuple(actions), tuple(results), depth, total_cost, stopping_reason)
                strategy = self.registry.resolve(action.repair_kind)
                if strategy is None:
                    action.status = RepairStatus.BLOCKED.value
                    action.stopping_reason = "repair_strategy_unavailable"
                    continue
                action.status = RepairStatus.RUNNING.value
                action.attempts += 1
                context = RepairExecutionContext(
                    run=run,
                    policy=policy,
                    audited_dossiers=dossiers,
                    evidence_hash=str(getattr(run, "evidence_hash", "") or ""),
                    recursion_depth=depth + 1,
                    event_recorder=event_recorder,
                )
                result = strategy.execute(action, context)
                self.repository.save(str(getattr(run, "run_id", "")), action.idempotency_key, result)
                results.append(result)
                total_cost += result.cost_usd
                action.status = result.status
                action.admitted_evidence_ids = result.admitted_evidence_ids
                action.conclusion_changes = result.conclusion_changes
                action.resolution_summary = result.summary
                admitted_this_cycle.update(result.admitted_evidence_ids)
                progressed = progressed or result.status == RepairStatus.COMPLETED.value
                if result.credential_expired:
                    stopping_reason = "credential_expired"
                    _apply_repair_stopping_state(run, stopping_reason)
                    return RepairControllerResult(evaluation, tuple(actions), tuple(results), depth + 1, total_cost, stopping_reason)
                if result.provider_failure:
                    stopping_reason = "provider_or_connector_failure"
                    _apply_repair_stopping_state(run, stopping_reason)
                    return RepairControllerResult(evaluation, tuple(actions), tuple(results), depth + 1, total_cost, stopping_reason)
            evaluation = self.evaluator.evaluate(
                ticker,
                run=run,
                audited_dossiers=dossiers,
                policy=policy,
                triggered_by="repair_cycle_completed",
            )
            if evaluation.publishable:
                stopping_reason = "all_material_gates_passed"
                return RepairControllerResult(evaluation, tuple(actions), tuple(results), depth + 1, total_cost, stopping_reason)
            if not progressed:
                stopping_reason = "no_eligible_repair_strategy"
                break
            if not admitted_this_cycle and not any(item.conclusion_changes for item in results):
                stopping_reason = "no_new_admitted_evidence"
                break
            actions = [replace(item) for item in evaluation.repair_actions]
        _apply_repair_stopping_state(run, stopping_reason)
        return RepairControllerResult(evaluation, tuple(actions), tuple(results), min(max_depth, len(results) or max_depth), total_cost, stopping_reason)


def independent_origin_requirements(
    run: Any,
    audited_dossiers: Iterable[Any],
    *,
    required: int,
) -> tuple[IndependentOriginRequirement, ...]:
    records = {
        str(getattr(record, "source_id", "")): record
        for record in getattr(run, "normalized_sources", []) or []
    }
    requirements: list[IndependentOriginRequirement] = []
    for dossier in audited_dossiers:
        quantified = " ".join(
            str(getattr(dossier, field, "") or "")
            for field in ("operating_outcome", "financing_outcome", "valuation_outcome", "price_outcome")
        )
        if not _NUMBER_RE.search(quantified):
            continue
        origin_groups = sorted({
            _origin_group(records[source_id])
            for source_id in getattr(dossier, "source_ids", []) or []
            if source_id in records
            and str(getattr(records[source_id], "validation_status", "")) == "validated"
            and _origin_group(records[source_id])
        })
        analog_id = str(getattr(dossier, "analog_id", "") or "")
        requirements.append(IndependentOriginRequirement(
            requirement_id=stable_contract_id("independent-origin", analog_id, quantified, required),
            analog_id=analog_id,
            quantified_claim=quantified[:1000],
            required_origin_groups=required,
            observed_origin_groups=tuple(origin_groups),
            missing_origin_classes=("independent_primary_or_market_origin",) if len(origin_groups) < required else (),
            status="satisfied" if len(origin_groups) >= required else "open",
        ))
    return tuple(requirements)


def build_source_lineage_graph(run: Any) -> tuple[SourceLineageNode, ...]:
    nodes: list[SourceLineageNode] = []
    for record in getattr(run, "normalized_sources", []) or []:
        source_id = str(getattr(record, "source_id", "") or "")
        if not source_id:
            continue
        nodes.append(SourceLineageNode(
            source_id=source_id,
            origin_group=_origin_group(record),
            ownership_group=str(getattr(record, "ownership_group", "") or ""),
            syndication_fingerprint=str(
                getattr(record, "syndication_fingerprint", "") or ""
            ),
            upstream_issuer=str(getattr(record, "upstream_issuer", "") or ""),
            dataset_lineage=str(getattr(record, "dataset_lineage", "") or ""),
            derived_from_source_ids=tuple(
                getattr(record, "derived_from_source_ids", ()) or ()
            ),
        ))
    return tuple(nodes)


def invalidate_derived_artifacts(
    dependencies: Iterable[DerivedArtifactDependency],
    *,
    changed_evidence_ids: Iterable[str] = (),
    new_evidence_hash: str = "",
    changed_claim_ids: Iterable[str] = (),
    changed_mapping_versions: Iterable[str] = (),
    changed_financial_paths: Iterable[str] = (),
    changed_analog_ids: Iterable[str] = (),
    changed_source_policy_version: str = "",
    changed_parser_versions: Iterable[str] = (),
) -> ScopeInvalidationResult:
    changed = {
        "evidence": set(changed_evidence_ids),
        "claim": set(changed_claim_ids),
        "mapping": set(changed_mapping_versions),
        "financial_path": set(changed_financial_paths),
        "analog": set(changed_analog_ids),
        "parser": set(changed_parser_versions),
    }
    invalidated: list[str] = []
    unchanged: list[str] = []
    reasons_by_artifact: dict[str, tuple[str, ...]] = {}
    for dependency in dependencies:
        reasons: list[str] = []
        if (
            new_evidence_hash
            and dependency.evidence_hash
            and dependency.evidence_hash != new_evidence_hash
        ):
            reasons.append("evidence_graph_changed")
        for label, values in (
            ("evidence", dependency.evidence_ids),
            ("claim", dependency.claim_ids),
            ("mapping", dependency.mapping_versions),
            ("financial_path", dependency.financial_paths),
            ("analog", dependency.analog_ids),
            ("parser", dependency.parser_versions),
        ):
            if set(values) & changed[label]:
                reasons.append(f"{label}_dependency_changed")
        if (
            changed_source_policy_version
            and dependency.source_policy_version
            and dependency.source_policy_version != changed_source_policy_version
        ):
            reasons.append("source_policy_changed")
        if reasons:
            dependency.status = "stale"
            dependency.invalidation_reasons = tuple(reasons)
            invalidated.append(dependency.artifact_id)
            reasons_by_artifact[dependency.artifact_id] = tuple(reasons)
        else:
            unchanged.append(dependency.artifact_id)
    return ScopeInvalidationResult(
        invalidated_artifact_ids=tuple(invalidated),
        unchanged_artifact_ids=tuple(unchanged),
        reasons_by_artifact=reasons_by_artifact,
    )


def compile_repair_actions(
    failures: Iterable[PublicationGateFailure],
) -> tuple[ResearchRepairAction, ...]:
    actions: list[ResearchRepairAction] = []
    for failure in failures:
        kind = RepairKind(failure.recommended_repair_kind)
        objective, criteria, mode = _repair_copy(kind, failure)
        key = stable_contract_id(
            "repair-key",
            kind.value,
            failure.run_id,
            failure.scope_type,
            failure.scope_id,
            failure.required_source_families,
            failure.dependency_fingerprint,
        )
        actions.append(ResearchRepairAction(
            action_id=stable_contract_id("repair", failure.failure_id, kind.value),
            idempotency_key=key,
            failure_ids=(failure.failure_id,),
            repair_kind=kind.value,
            objective=objective,
            readable_explanation=failure.summary,
            materiality=failure.materiality,
            priority="Critical" if failure.materiality.casefold() == "critical" else "High",
            execution_mode=mode,
            required_source_families=failure.required_source_families,
            completion_criteria=criteria,
            depth_allowance=2 if kind in {
                RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE,
                RepairKind.RETRIEVE_INDEPENDENT_ANALOG_ORIGIN,
            } else 1,
        ))
    deduped = list(deduplicate_repairs(actions))
    kind_to_ids: dict[str, list[str]] = {}
    for action in deduped:
        kind_to_ids.setdefault(action.repair_kind, []).append(action.action_id)
    retrieval_ids = tuple(
        action.action_id for action in deduped
        if action.repair_kind in {
            RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE.value,
            RepairKind.RETRIEVE_INDEPENDENT_ANALOG_ORIGIN.value,
            RepairKind.COMPLETE_REQUIRED_FINANCIAL_PATH.value,
            RepairKind.RESOLVE_NUMERIC_MAPPING.value,
        }
    )
    validation_ids = tuple(kind_to_ids.get(RepairKind.COMPLETE_DETERMINISTIC_VALIDATION.value, []))
    revision_ids = tuple(
        action.action_id for action in deduped
        if action.repair_kind in {
            RepairKind.RESPOND_TO_AUDIT_FINDING.value,
            RepairKind.REVISE_PRIMARY_ARTIFACT.value,
            RepairKind.NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION.value,
        }
    )
    for action in deduped:
        if action.repair_kind == RepairKind.COMPLETE_DETERMINISTIC_VALIDATION.value:
            action.dependency_action_ids = retrieval_ids
        elif action.repair_kind in {
            RepairKind.RESPOND_TO_AUDIT_FINDING.value,
            RepairKind.REVISE_PRIMARY_ARTIFACT.value,
            RepairKind.NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION.value,
        }:
            action.dependency_action_ids = tuple(sorted(set(retrieval_ids + validation_ids)))
        elif action.repair_kind == RepairKind.RECOMPUTE_SEMANTIC_QUALITY.value:
            action.dependency_action_ids = tuple(sorted(set(validation_ids + revision_ids)))
        elif action.repair_kind == RepairKind.RERUN_CLOSURE.value:
            action.dependency_action_ids = tuple(sorted(set(validation_ids + revision_ids)))
    return tuple(deduped)


def _failure(
    code: PublicationFailureCode,
    run_id: str,
    summary: str,
    diagnosis: str,
    repair_kind: RepairKind,
    blocking_rule: str,
    *,
    scope_type: str = "artifact",
    scope_id: str = "",
    retryable: bool = True,
    claim_ids: tuple[str, ...] = (),
    finding_ids: tuple[str, ...] = (),
    source_families: tuple[str, ...] = (),
) -> PublicationGateFailure:
    fingerprint = stable_contract_id("dependency", run_id, code.value, scope_type, scope_id, claim_ids, finding_ids)
    return PublicationGateFailure(
        failure_id=stable_contract_id("gate-failure", run_id, code.value, scope_type, scope_id, fingerprint),
        code=code.value,
        run_id=run_id,
        summary=summary,
        technical_diagnosis=diagnosis or summary,
        retryable=retryable,
        required_source_families=source_families,
        blocking_rule=blocking_rule,
        recommended_repair_kind=repair_kind.value,
        related_finding_ids=finding_ids,
        related_claim_ids=claim_ids,
        dependency_fingerprint=fingerprint,
        scope_type=scope_type,
        scope_id=scope_id,
    )


def _repair_copy(
    kind: RepairKind,
    failure: PublicationGateFailure,
) -> tuple[str, tuple[str, ...], str]:
    scope = failure.scope_id or "the affected artifact"
    if failure.code == PublicationFailureCode.RUN_INCOMPLETE.value:
        return (
            "Resume the unfinished research attempt before validation and audit.",
            (
                "The research attempt reaches a resumable finished state.",
                "Deterministic validation is scheduled before final audit.",
            ),
            "application_service",
        )
    copies = {
        RepairKind.COMPLETE_DETERMINISTIC_VALIDATION: (
            "Complete deterministic validation for the affected research artifact.",
            ("All material validation gates pass.",),
            "deterministic",
        ),
        RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE: (
            "Retrieve and admit primary evidence for the failed material claims.",
            ("Registered retrieval completes.", "Affected claims pass revalidation."),
            "registered_connector",
        ),
        RepairKind.RETRIEVE_INDEPENDENT_ANALOG_ORIGIN: (
            f"Add an independent origin for {scope}, or narrow its quantified conclusion.",
            ("Origin requirement is satisfied, or the unsupported quantified claim is narrowed."),
            "registered_connector",
        ),
        RepairKind.COMPLETE_REQUIRED_FINANCIAL_PATH: (
            f"Complete the validated financial path for {scope}.",
            ("The required path is available with admitted provenance."),
            "registered_connector",
        ),
        RepairKind.RESPOND_TO_AUDIT_FINDING: (
            "Respond to every material audit finding using a valid response state.",
            ("Each finding is accepted, rejected with admitted evidence, or explicitly unresolved."),
            "primary_revision_provider",
        ),
        RepairKind.RERUN_CLOSURE: (
            "Rerun independent closure against the revised evidence snapshot.",
            ("Closure covers every material response and newly admitted contrary item."),
            "closure_auditor",
        ),
        RepairKind.RECOMPUTE_SEMANTIC_QUALITY: (
            "Recompute semantic quality after the material artifact changes.",
            ("A versioned quality score reflects the current evidence and conclusions."),
            "deterministic",
        ),
        RepairKind.NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION: (
            "Narrow or remove the unsupported material conclusion.",
            ("The unsupported conclusion no longer appears as established research."),
            "primary_revision_provider",
        ),
        RepairKind.RESOLVE_NUMERIC_MAPPING: (
            "Resolve the numeric mapping through the automated escalation ladder.",
            ("Concept, period, unit, and source mapping validate, or an explicit unresolved reason remains."),
            "registered_connector",
        ),
    }
    return copies.get(kind, (
        f"Complete {kind.value.replace('_', ' ')} for {scope}.",
        ("The blocking gate passes or records an explicit non-repairable outcome.",),
        "application_service",
    ))


def _required_paths(ticker: str, run: Any, policy: DemoQualityPolicy) -> set[str]:
    explicit = set(policy.ticker_required_paths.get(ticker, ()))
    if explicit:
        return explicit
    if not policy.enforce_coverage_profile_paths:
        return set()
    provenance = dict(getattr(run, "generation_provenance", {}) or {})
    profile_id = str(provenance.get("security_archetype") or policy.default_coverage_profile_id)
    profile = DEFAULT_COVERAGE_PROFILES.get(profile_id, DEFAULT_COVERAGE_PROFILES["operating_company"])
    return set(profile.required_financial_paths)


def _promoted_quarantine_count(run: Any, records: list[Any]) -> int:
    count = sum(
        str(getattr(record, "retrieval_status", "")) == "metadata_only"
        and str(getattr(record, "validation_status", "")) == "validated"
        for record in records
    )
    candidates = {
        str(getattr(item, "candidate_id", "")): item
        for item in getattr(run, "source_candidates", []) or []
    }
    count += sum(
        str(getattr(decision, "status", "")).casefold() == "admitted"
        and str(getattr(candidates.get(str(getattr(decision, "candidate_id", ""))), "full_text_status", "")).casefold() == "metadata_only"
        and str(getattr(decision, "retrieval_status", "")).casefold() != "retrieved"
        for decision in getattr(run, "source_admission_decisions", []) or []
    )
    return count


def _closure_state(run: Any, audit_decision: Any) -> str:
    final_artifact = getattr(run, "final_artifact", None)
    payload = getattr(final_artifact, "payload", None)
    if isinstance(payload, dict):
        state = str(payload.get("closure_state") or "")
        if state:
            return state
        closure = payload.get("closure_audit")
        if isinstance(closure, dict) and closure.get("closure_state"):
            return str(closure["closure_state"])
    if audit_decision is not None and str(
        getattr(audit_decision, "status", "")
    ) == "passed":
        return "closed"
    return "failed_material_findings_unaddressed"


def _finding_has_valid_response(finding: Any) -> bool:
    state = str(getattr(finding, "primary_response_state", "") or "")
    if state == "unresolved":
        return False
    if state in {"accepted", "rejected_with_evidence"}:
        return True
    return bool(getattr(finding, "resolved", False)) or str(
        getattr(finding, "status", "")
    ).casefold() == "resolved"


def _apply_repair_stopping_state(run: Any, reason: str) -> None:
    if not hasattr(run, "lifecycle_state"):
        return
    now = _utc_now()
    if reason == "credential_expired":
        run.lifecycle_state = "waiting_for_credential"
        return
    if reason == "provider_or_connector_failure":
        run.lifecycle_state = "failed_provider_or_connector"
    elif reason == "user_cancelled":
        run.lifecycle_state = "cancelled"
    else:
        run.lifecycle_state = "completed_not_publishable"
    if hasattr(run, "terminal_at"):
        run.terminal_at = getattr(run, "terminal_at", "") or now
    if hasattr(run, "completed_at"):
        run.completed_at = getattr(run, "completed_at", "") or now


def _origin_group(record: Any) -> str:
    explicit = str(getattr(record, "origin_group", "") or "").strip()
    if explicit:
        return explicit
    source_family = str(getattr(record, "source_family", "") or "unknown")
    document_id = str(getattr(record, "document_id", "") or "unknown")
    derived = tuple(sorted(getattr(record, "derived_from_source_ids", []) or []))
    return stable_contract_id("origin", source_family, document_id, derived)


def _ordered_actions(actions: Iterable[ResearchRepairAction]) -> list[ResearchRepairAction]:
    remaining = {item.action_id: item for item in actions}
    ordered: list[ResearchRepairAction] = []
    while remaining:
        ready = [
            item for item in remaining.values()
            if all(dep not in remaining for dep in item.dependency_action_ids)
        ]
        if not ready:
            ready = list(remaining.values())
        for item in sorted(ready, key=lambda value: (value.priority != "Critical", value.action_id)):
            ordered.append(item)
            remaining.pop(item.action_id, None)
    return ordered


def _deduplicate_failures(failures: Iterable[PublicationGateFailure]) -> list[PublicationGateFailure]:
    by_key: dict[tuple[str, str, str], PublicationGateFailure] = {}
    for failure in failures:
        by_key[(failure.code, failure.scope_type, failure.scope_id)] = failure
    return list(by_key.values())


def _dimension_status(score: int) -> str:
    return "strong" if score >= 8 else "needs_attention" if score >= 5 else "blocking"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
