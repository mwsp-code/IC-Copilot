from __future__ import annotations

from types import SimpleNamespace
import json
from pathlib import Path

from equity_research.application.publication_readiness import (
    build_source_lineage_graph,
    canonicalize_artifact_for_publication,
    record_analog_candidate_revision,
    invalidate_derived_artifacts,
    PublicationGateEvaluator,
    RepairExecutionResult,
    RepairStrategyRegistry,
    ResearchRepairController,
    compile_repair_actions,
)
from equity_research.domain.publication_readiness import (
    DerivedArtifactDependency,
    PublicationFailureCode,
    PublicationGateEvaluation,
    PublicationGateFailure,
    PublicationQualificationCode,
    RepairKind,
    RepairStatus,
    ResearchRepairAction,
    SemanticQualityEvaluation,
)
from equity_research.infrastructure.repair_repository import (
    SqliteRepairJobRepository,
)
from equity_research.models import (
    ClaimEvidenceAudit,
    DemoQualityPolicy,
    ResearchRoundArtifact,
)
from equity_research.presentation.view_models import (
    build_publication_readiness_view_model,
)


def _failure(code: PublicationFailureCode, kind: RepairKind) -> PublicationGateFailure:
    return PublicationGateFailure(
        failure_id=f"failure-{code.value}",
        code=code.value,
        run_id="run-1",
        summary=f"Readable {code.value} failure.",
        technical_diagnosis="Technical diagnosis.",
        blocking_rule="The publication gate must pass.",
        recommended_repair_kind=kind.value,
        dependency_fingerprint=f"dependency-{code.value}",
    )


def test_every_typed_blocker_compiles_to_a_readable_repair() -> None:
    mapping = {
        PublicationFailureCode.RUN_INCOMPLETE: RepairKind.COMPLETE_DETERMINISTIC_VALIDATION,
        PublicationFailureCode.DETERMINISTIC_VALIDATION_NOT_RUN: RepairKind.COMPLETE_DETERMINISTIC_VALIDATION,
        PublicationFailureCode.DETERMINISTIC_VALIDATION_PARTIAL: RepairKind.COMPLETE_DETERMINISTIC_VALIDATION,
        PublicationFailureCode.MATERIAL_CLAIM_VALIDATION_FAILED: RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE,
        PublicationFailureCode.MATERIAL_AUDIT_FINDING_UNADDRESSED: RepairKind.RESPOND_TO_AUDIT_FINDING,
        PublicationFailureCode.FINAL_CLOSURE_NOT_PASSED: RepairKind.RERUN_CLOSURE,
        PublicationFailureCode.SEMANTIC_QUALITY_BELOW_THRESHOLD: RepairKind.RECOMPUTE_SEMANTIC_QUALITY,
        PublicationFailureCode.SEMANTIC_CRITICAL_FAILURE: RepairKind.NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION,
        PublicationFailureCode.ANALOG_INDEPENDENT_PROVENANCE_INSUFFICIENT: RepairKind.RETRIEVE_INDEPENDENT_ANALOG_ORIGIN,
        PublicationFailureCode.REQUIRED_FINANCIAL_PATH_MISSING: RepairKind.COMPLETE_REQUIRED_FINANCIAL_PATH,
        PublicationFailureCode.MAPPING_RESOLUTION_FAILED: RepairKind.RESOLVE_NUMERIC_MAPPING,
        PublicationFailureCode.QUARANTINED_CANDIDATE_PROMOTED: RepairKind.NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION,
        PublicationFailureCode.CREDENTIAL_EXPIRED: RepairKind.RECONNECT_CREDENTIAL,
        PublicationFailureCode.PROVIDER_OR_CONNECTOR_UNAVAILABLE: RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE,
        PublicationFailureCode.ARTIFACT_SCHEMA_INCOMPATIBLE: RepairKind.MIGRATE_ARTIFACT_SCHEMA,
        PublicationFailureCode.PUBLICATION_POLICY_CHANGED: RepairKind.RECOMPUTE_SEMANTIC_QUALITY,
    }
    failures = [_failure(code, kind) for code, kind in mapping.items()]

    actions = compile_repair_actions(failures)

    assert {failure_id for action in actions for failure_id in action.failure_ids} == {
        item.failure_id for item in failures
    }
    assert all(action.objective and action.completion_criteria for action in actions)


def test_incomplete_run_is_a_truthful_draft_with_typed_repairs() -> None:
    run = SimpleNamespace(
        run_id="draft-run",
        status="investigated_draft",
        lifecycle_state="",
        deterministic_validation_status="not_run",
        evidence_hash="draft-evidence",
        variants=[],
        final_artifact=None,
        claim_evidence_audits=[],
        audit_findings=[],
        normalized_sources=[],
        source_candidates=[],
        source_admission_decisions=[],
        historical_financial_paths=[],
        data_gaps=[],
        publication_gate_evaluations=[],
        repair_actions=[],
        semantic_quality_history=[],
        publishable_at="",
    )
    policy = DemoQualityPolicy(
        require_audited_analog=False,
        require_closed_qa=False,
        ticker_required_paths={},
        ticker_minimum_scores={"AAPL": 0},
    )

    evaluation = PublicationGateEvaluator().evaluate(
        "AAPL", run=run, audited_dossiers=[], policy=policy
    )

    assert evaluation.publishable is False
    assert PublicationFailureCode.RUN_INCOMPLETE.value in {
        item.code for item in evaluation.failures
    }
    assert PublicationFailureCode.DETERMINISTIC_VALIDATION_NOT_RUN.value in {
        item.code for item in evaluation.failures
    }
    assert len(evaluation.repair_actions) >= 2
    assert run.publishable_at == ""


class _CountingStrategy:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, action, context) -> RepairExecutionResult:
        self.calls += 1
        return RepairExecutionResult(
            action_id=action.action_id,
            status=RepairStatus.COMPLETED.value,
            summary="Registered evidence was admitted.",
            admitted_evidence_ids=("evidence-1",),
            cost_usd=0.25,
        )


class _AlwaysRepairEvaluator:
    def __init__(self, action: ResearchRepairAction) -> None:
        self.action = action

    def evaluate(self, ticker, *, run, audited_dossiers, policy, triggered_by):
        return PublicationGateEvaluation(
            evaluation_id=f"evaluation-{triggered_by}",
            run_id=run.run_id,
            artifact_version="artifact-1",
            evidence_hash=run.evidence_hash,
            publication_policy_version=policy.policy_version,
            validation_policy_version=policy.validation_policy_version,
            schema_version=policy.artifact_schema_version,
            parser_version=policy.parser_version,
            evaluated_at="2026-08-09T00:00:00+00:00",
            decision="repair_required",
            repair_actions=(self.action,),
        )


def test_repair_execution_is_durable_and_idempotent(tmp_path) -> None:
    failure = _failure(
        PublicationFailureCode.MATERIAL_CLAIM_VALIDATION_FAILED,
        RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE,
    )
    action = compile_repair_actions([failure])[0]
    strategy = _CountingStrategy()
    registry = RepairStrategyRegistry()
    registry.register(action.repair_kind, strategy)
    repository = SqliteRepairJobRepository(tmp_path / "repairs.sqlite3")
    controller = ResearchRepairController(
        evaluator=_AlwaysRepairEvaluator(action),
        registry=registry,
        repository=repository,
    )
    run = SimpleNamespace(run_id="run-1", evidence_hash="evidence-hash")
    policy = DemoQualityPolicy()

    first = controller.repair_and_recheck(
        "AAPL",
        run=run,
        audited_dossiers=[],
        policy=policy,
        max_depth=1,
    )
    second = controller.repair_and_recheck(
        "AAPL",
        run=run,
        audited_dossiers=[],
        policy=policy,
        max_depth=1,
    )

    assert strategy.calls == 1
    assert first.total_cost_usd == 0.25
    assert second.execution_results[0].admitted_evidence_ids == ("evidence-1",)
    assert repository.get("run-1", action.idempotency_key) is not None


def test_scope_invalidation_marks_only_dependent_artifacts_stale() -> None:
    affected = DerivedArtifactDependency(
        artifact_id="event-attribution",
        artifact_type="event_attribution",
        evidence_hash="before",
        evidence_ids=("price-old",),
    )
    unaffected = DerivedArtifactDependency(
        artifact_id="company-description",
        artifact_type="company_model",
        evidence_hash="",
        evidence_ids=("filing-1",),
    )

    result = invalidate_derived_artifacts(
        [affected, unaffected],
        changed_evidence_ids=("price-new",),
        new_evidence_hash="after",
    )

    assert result.invalidated_artifact_ids == ("event-attribution",)
    assert result.unchanged_artifact_ids == ("company-description",)
    assert affected.status == "stale"
    assert unaffected.status == "current"


def test_source_lineage_counts_origin_groups_not_urls() -> None:
    run = SimpleNamespace(normalized_sources=[
        SimpleNamespace(
            source_id="url-a",
            origin_group="issuer-filing",
            ownership_group="issuer",
            syndication_fingerprint="same-content",
            upstream_issuer="Apple",
            dataset_lineage="SEC filing",
            derived_from_source_ids=(),
        ),
        SimpleNamespace(
            source_id="url-b",
            origin_group="issuer-filing",
            ownership_group="syndicator",
            syndication_fingerprint="same-content",
            upstream_issuer="Apple",
            dataset_lineage="SEC filing",
            derived_from_source_ids=("url-a",),
        ),
    ])

    nodes = build_source_lineage_graph(run)

    assert len(nodes) == 2
    assert {node.origin_group for node in nodes} == {"issuer-filing"}


def test_publication_readiness_view_model_is_readable_and_schema_safe() -> None:
    failure = _failure(
        PublicationFailureCode.ANALOG_INDEPENDENT_PROVENANCE_INSUFFICIENT,
        RepairKind.RETRIEVE_INDEPENDENT_ANALOG_ORIGIN,
    )
    action = compile_repair_actions([failure])[0]
    evaluation = PublicationGateEvaluation(
        evaluation_id="evaluation-technical-id",
        run_id="run-technical-id",
        artifact_version="artifact-technical-id",
        evidence_hash="deadbeefdeadbeefdeadbeef",
        publication_policy_version="policy-v1",
        validation_policy_version="validation-v1",
        schema_version="schema-v1",
        parser_version="parser-v1",
        evaluated_at="2026-08-09T00:00:00+00:00",
        decision="repair_required",
        failures=(failure,),
        repair_actions=(action,),
    )
    result = SimpleNamespace(
        hybrid_research=SimpleNamespace(
            lifecycle_state="completed_not_publishable",
            status="completed",
            deterministic_validation_status="passed",
            publication_gate_evaluations=[evaluation],
            repair_actions=[action],
            semantic_quality_history=[],
            demo_freeze_decision=None,
        ),
        independent_research_qa=SimpleNamespace(
            status="completed",
            closure_result=SimpleNamespace(state="closed_with_residual_risk"),
        ),
    )

    model = build_publication_readiness_view_model(result)

    assert model.publication_eligibility == "Research draft · validation or QA incomplete"
    assert model.repairs_remaining == 1
    assert "not ready for publication" in model.summary
    assert "deadbeef" not in " ".join(
        (model.summary, model.repair_cards[0].title, model.repair_cards[0].why_it_matters)
    )


def test_historical_result_without_new_fields_never_breaks_readiness_view() -> None:
    result = SimpleNamespace(
        hybrid_research=SimpleNamespace(
            status="completed",
            deterministic_validation_status="passed",
        ),
        independent_research_qa=None,
    )

    model = build_publication_readiness_view_model(result)

    assert model.publication_eligibility == "Research draft · validation or QA incomplete"
    assert model.repair_cards == ()
    assert "historical result" in model.summary


def _coverage_run(profile_id: str, paths=()):
    return SimpleNamespace(
        run_id=f"fixture-{profile_id}",
        status="completed",
        lifecycle_state="",
        deterministic_validation_status="passed",
        evidence_hash="sanitized-evidence",
        variants=[],
        final_artifact=SimpleNamespace(
            artifact_id="artifact",
            payload={"closure_state": "closed"},
        ),
        claim_evidence_audits=[],
        audit_findings=[],
        normalized_sources=[],
        source_candidates=[],
        source_admission_decisions=[],
        historical_financial_paths=[
            SimpleNamespace(metric=item, status="available") for item in paths
        ],
        data_gaps=[],
        generation_provenance={"security_archetype": profile_id},
        publication_gate_evaluations=[],
        repair_actions=[],
        semantic_quality_history=[],
        derived_artifact_dependencies=[],
        canonical_publication_revisions=[],
        publishable_at="",
    )


def test_cross_security_coverage_profiles_generate_systematic_repairs() -> None:
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "publication_readiness"
            / "security_profiles.json"
        ).read_text(encoding="utf-8")
    )
    assert fixture["sanitization_status"] == "passed"
    policy = DemoQualityPolicy(
        require_closed_qa=False,
        require_audited_analog=False,
        ticker_required_paths={},
        ticker_minimum_scores={},
        enforce_coverage_profile_paths=True,
    )
    evaluator = PublicationGateEvaluator()

    for profile in fixture["profiles"]:
        run = _coverage_run(profile["profile_id"])
        evaluation = evaluator.evaluate(
            profile["ticker"],
            run=run,
            audited_dossiers=[],
            policy=policy,
        )
        optional = {
            item.summary.removeprefix("Optional financial path unavailable: ").removesuffix(".")
            for item in evaluation.qualifications
            if item.code == PublicationQualificationCode.OPTIONAL_FINANCIAL_PATH_UNAVAILABLE.value
        }
        assert optional == set(profile["required_paths"])


class _FixedScore:
    def __init__(self, score: int, critical=()) -> None:
        self.score_value = score
        self.critical = tuple(critical)

    def score(self, run, dossiers, *, threshold, triggered_by):
        return SemanticQualityEvaluation(
            evaluation_id=f"score-{self.score_value}",
            run_id=run.run_id,
            artifact_version="artifact",
            evidence_hash=run.evidence_hash,
            total_score=self.score_value,
            threshold=threshold,
            status="failed" if self.critical or self.score_value < threshold else "passed",
            critical_failures=self.critical,
            triggered_by=triggered_by,
        )


def _single_version_policy_run(closure_state="closed"):
    run = _coverage_run("event_only")
    run.final_artifact.payload["closure_state"] = closure_state
    return run


def test_single_version_score_outcomes_and_critical_failure_policy() -> None:
    policy = DemoQualityPolicy(
        require_audited_analog=False,
        ticker_required_paths={},
        enforce_coverage_profile_paths=False,
    )
    below = PublicationGateEvaluator(_FixedScore(74)).evaluate(
        "AAPL", run=_single_version_policy_run(), audited_dossiers=[], policy=policy
    )
    residual = PublicationGateEvaluator(_FixedScore(80)).evaluate(
        "AAPL", run=_single_version_policy_run(), audited_dossiers=[], policy=policy
    )
    quality = PublicationGateEvaluator(_FixedScore(85)).evaluate(
        "AAPL", run=_single_version_policy_run(), audited_dossiers=[], policy=policy
    )
    critical = PublicationGateEvaluator(_FixedScore(90, ("critical",))).evaluate(
        "AAPL", run=_single_version_policy_run(), audited_dossiers=[], policy=policy
    )

    assert below.decision == "repair_required"
    assert residual.decision == "publishable_with_residual_diligence"
    assert residual.publishable is True
    assert quality.decision == "publishable"
    assert critical.decision == "repair_required"


def test_missing_financial_path_blocks_when_a_displayed_valuation_depends_on_it() -> None:
    run = _single_version_policy_run()
    run.derived_artifact_dependencies = [DerivedArtifactDependency(
        artifact_id="displayed-valuation",
        artifact_type="valuation",
        financial_paths=("Free Cash Flow TTM",),
    )]
    policy = DemoQualityPolicy(
        require_audited_analog=False,
        ticker_required_paths={"AAPL": ("Free Cash Flow TTM",)},
        enforce_coverage_profile_paths=False,
    )

    evaluation = PublicationGateEvaluator(_FixedScore(85)).evaluate(
        "AAPL", run=run, audited_dossiers=[], policy=policy
    )

    assert PublicationFailureCode.REQUIRED_FINANCIAL_PATH_MISSING.value in {
        item.code for item in evaluation.failures
    }
    assert PublicationQualificationCode.OPTIONAL_FINANCIAL_PATH_UNAVAILABLE.value not in {
        item.code for item in evaluation.qualifications
    }


def test_missing_provenance_analog_is_downgraded_and_excluded_from_scoring() -> None:
    run = _single_version_policy_run()
    dossier = SimpleNamespace(
        analog_id="analog-1",
        status="Audited Research Analog",
        source_ids=[],
        operating_outcome="Revenue rose 20%.",
        financing_outcome="Unknown",
        valuation_outcome="Unknown",
        price_outcome="Shares rose 30%.",
    )
    policy = DemoQualityPolicy(
        require_audited_analog=False,
        ticker_required_paths={},
        enforce_coverage_profile_paths=False,
    )

    evaluation = PublicationGateEvaluator(_FixedScore(85)).evaluate(
        "AAPL", run=run, audited_dossiers=[dossier], policy=policy
    )
    revision = record_analog_candidate_revision(run, ("analog-1",))

    assert evaluation.decision == "publishable_with_residual_diligence"
    assert PublicationFailureCode.ANALOG_INDEPENDENT_PROVENANCE_INSUFFICIENT.value not in {
        item.code for item in evaluation.failures
    }
    analog_qualification = next(
        item for item in evaluation.qualifications
        if item.code == PublicationQualificationCode.ANALOG_DOWNGRADED_TO_CANDIDATE.value
    )
    assert analog_qualification.affected_analog_ids == ("analog-1",)
    assert revision is not None
    assert revision.downgraded_analog_ids == ("analog-1",)


def test_residual_closure_is_eligible_and_missing_closure_is_not() -> None:
    policy = DemoQualityPolicy(
        require_audited_analog=False,
        ticker_required_paths={},
        enforce_coverage_profile_paths=False,
    )
    residual = PublicationGateEvaluator(_FixedScore(85)).evaluate(
        "AAPL",
        run=_single_version_policy_run("closed_with_residual_risk"),
        audited_dossiers=[],
        policy=policy,
    )
    missing = PublicationGateEvaluator(_FixedScore(85)).evaluate(
        "AAPL",
        run=_single_version_policy_run("indeterminate_provider_or_evidence_failure"),
        audited_dossiers=[],
        policy=policy,
    )

    assert residual.decision == "publishable_with_residual_diligence"
    assert PublicationQualificationCode.CLOSURE_WITH_RESIDUAL_RISK.value in {
        item.code for item in residual.qualifications
    }
    assert missing.decision == "repair_required"


def test_canonical_revision_removes_failed_claim_and_creates_investigation() -> None:
    claim = "Revenue increased 20% to $120 billion."
    run = _single_version_policy_run()
    run.ticker = "AAPL"
    run.claim_evidence_audits = [ClaimEvidenceAudit(
        claim_id="claim-1",
        claim_text=claim,
        classification="factual",
        materiality="Material",
        status="failed",
        blockers=["The numerical claim is not present in admitted evidence."],
    )]
    artifact = ResearchRoundArtifact(
        artifact_id="primary-1",
        artifact_type="primary_research",
        status="accepted_with_gaps",
        summary=claim,
        payload={"summary": claim, "claims": [{"claim": claim, "evidence_ids": ["e1"]}]},
        evidence_ids=["e1"],
    )

    revised, revision = canonicalize_artifact_for_publication(run, artifact)

    assert revised.payload["claims"] == []
    assert revised.evidence_ids == []
    assert revised.payload["research_leads"][0]["cannot_support_conclusions"] is True
    assert revised.payload["source_requests"][0]["status"] == "open"
    assert revision.removed_claim_ids == ("claim-1",)


def test_policy_upgrade_creates_new_snapshot_without_rewriting_history() -> None:
    run = _coverage_run("event_only")
    evaluator = PublicationGateEvaluator()
    old_policy = DemoQualityPolicy(
        policy_version="policy-v1",
        require_closed_qa=False,
        require_audited_analog=False,
        ticker_required_paths={},
        ticker_minimum_scores={"AAPL": 0},
    )
    new_policy = DemoQualityPolicy(
        policy_version="policy-v2",
        require_closed_qa=False,
        require_audited_analog=False,
        ticker_required_paths={},
        ticker_minimum_scores={"AAPL": 100},
    )

    historical = evaluator.evaluate(
        "AAPL", run=run, audited_dossiers=[], policy=old_policy
    )
    current = evaluator.evaluate(
        "AAPL", run=run, audited_dossiers=[], policy=new_policy
    )

    assert historical.publishable is True
    assert historical.publication_policy_version == "policy-v1"
    assert current.publishable is False
    assert current.supersedes_evaluation_id == historical.evaluation_id
    assert run.publication_gate_evaluations[0] is historical


class _StoppingStrategy:
    def __init__(self, *, credential=False, provider=False) -> None:
        self.credential = credential
        self.provider = provider

    def execute(self, action, context) -> RepairExecutionResult:
        return RepairExecutionResult(
            action_id=action.action_id,
            status=RepairStatus.BLOCKED.value,
            summary="Repair could not continue.",
            credential_expired=self.credential,
            provider_failure=self.provider,
        )


def test_repair_stops_truthfully_on_credential_and_provider_failure() -> None:
    failure = _failure(
        PublicationFailureCode.MATERIAL_CLAIM_VALIDATION_FAILED,
        RepairKind.RETRIEVE_MISSING_PRIMARY_EVIDENCE,
    )
    action = compile_repair_actions([failure])[0]
    policy = DemoQualityPolicy()
    for expected_reason, expected_state, strategy in (
        (
            "credential_expired",
            "waiting_for_credential",
            _StoppingStrategy(credential=True),
        ),
        (
            "provider_or_connector_failure",
            "failed_provider_or_connector",
            _StoppingStrategy(provider=True),
        ),
    ):
        registry = RepairStrategyRegistry()
        registry.register(action.repair_kind, strategy)
        run = SimpleNamespace(
            run_id=f"run-{expected_reason}",
            evidence_hash="evidence",
            lifecycle_state="repair_required",
            terminal_at="",
            completed_at="",
        )
        result = ResearchRepairController(
            evaluator=_AlwaysRepairEvaluator(action),
            registry=registry,
        ).repair_and_recheck(
            "AAPL",
            run=run,
            audited_dossiers=[],
            policy=policy,
            max_depth=1,
        )
        assert result.stopping_reason == expected_reason
        assert run.lifecycle_state == expected_state
        assert bool(run.terminal_at) is (expected_state == "failed_provider_or_connector")
