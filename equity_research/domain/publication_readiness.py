from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Iterable


class ResearchLifecycleState(str, Enum):
    RESEARCH_RUNNING = "research_running"
    RESEARCH_ATTEMPT_FINISHED = "research_attempt_finished"
    VALIDATION_PENDING = "validation_pending"
    REPAIR_REQUIRED = "repair_required"
    REPAIR_RUNNING = "repair_running"
    REVISION_PENDING = "revision_pending"
    AUDIT_PENDING = "audit_pending"
    CLOSURE_PENDING = "closure_pending"
    PUBLISHABLE = "publishable"
    PUBLISHED = "published"
    COMPLETED_NOT_PUBLISHABLE = "completed_not_publishable"
    WAITING_FOR_CREDENTIAL = "waiting_for_credential"
    CANCELLED = "cancelled"
    FAILED_PROVIDER_OR_CONNECTOR = "failed_provider_or_connector"


TERMINAL_LIFECYCLE_STATES = {
    ResearchLifecycleState.PUBLISHED.value,
    ResearchLifecycleState.COMPLETED_NOT_PUBLISHABLE.value,
    ResearchLifecycleState.CANCELLED.value,
    ResearchLifecycleState.FAILED_PROVIDER_OR_CONNECTOR.value,
}


class PublicationFailureCode(str, Enum):
    RUN_INCOMPLETE = "run_incomplete"
    DETERMINISTIC_VALIDATION_NOT_RUN = "deterministic_validation_not_run"
    DETERMINISTIC_VALIDATION_PARTIAL = "deterministic_validation_partial"
    MATERIAL_CLAIM_VALIDATION_FAILED = "material_claim_validation_failed"
    MATERIAL_AUDIT_FINDING_UNADDRESSED = "material_audit_finding_unaddressed"
    FINAL_CLOSURE_NOT_PASSED = "final_closure_not_passed"
    SEMANTIC_QUALITY_BELOW_THRESHOLD = "semantic_quality_below_threshold"
    SEMANTIC_CRITICAL_FAILURE = "semantic_critical_failure"
    ANALOG_INDEPENDENT_PROVENANCE_INSUFFICIENT = (
        "analog_independent_provenance_insufficient"
    )
    REQUIRED_FINANCIAL_PATH_MISSING = "required_financial_path_missing"
    MAPPING_RESOLUTION_FAILED = "mapping_resolution_failed"
    QUARANTINED_CANDIDATE_PROMOTED = "quarantined_candidate_promoted"
    CREDENTIAL_EXPIRED = "credential_expired"
    PROVIDER_OR_CONNECTOR_UNAVAILABLE = "provider_or_connector_unavailable"
    ARTIFACT_SCHEMA_INCOMPATIBLE = "artifact_schema_incompatible"
    PUBLICATION_POLICY_CHANGED = "publication_policy_changed"


class RepairKind(str, Enum):
    COMPLETE_DETERMINISTIC_VALIDATION = "complete_deterministic_validation"
    RETRIEVE_MISSING_PRIMARY_EVIDENCE = "retrieve_missing_primary_evidence"
    RETRIEVE_INDEPENDENT_ANALOG_ORIGIN = "retrieve_independent_analog_origin"
    RESOLVE_NUMERIC_MAPPING = "resolve_numeric_mapping"
    RESOLVE_DISCLOSURE_CLASSIFICATION = "resolve_disclosure_classification"
    COMPLETE_REQUIRED_FINANCIAL_PATH = "complete_required_financial_path"
    RESPOND_TO_AUDIT_FINDING = "respond_to_audit_finding"
    REVISE_PRIMARY_ARTIFACT = "revise_primary_artifact"
    NARROW_OR_REMOVE_UNSUPPORTED_CONCLUSION = (
        "narrow_or_remove_unsupported_conclusion"
    )
    RECOMPUTE_SEMANTIC_QUALITY = "recompute_semantic_quality"
    RERUN_INDEPENDENT_AUDIT = "rerun_independent_audit"
    RERUN_CLOSURE = "rerun_closure"
    RECONNECT_CREDENTIAL = "reconnect_credential"
    MIGRATE_ARTIFACT_SCHEMA = "migrate_artifact_schema"


class RepairStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class RunLifecycleTimestamps:
    research_attempt_finished_at: str = ""
    validation_completed_at: str = ""
    audit_completed_at: str = ""
    revision_completed_at: str = ""
    closure_completed_at: str = ""
    publishable_at: str = ""
    published_at: str = ""
    terminal_at: str = ""

    def validate(self, state: str) -> None:
        lifecycle = ResearchLifecycleState(state)
        if self.published_at and not self.publishable_at:
            raise ValueError("published_at requires publishable_at.")
        if lifecycle == ResearchLifecycleState.PUBLISHED and not self.published_at:
            raise ValueError("The published lifecycle state requires published_at.")
        if lifecycle in {
            ResearchLifecycleState.PUBLISHABLE,
            ResearchLifecycleState.PUBLISHED,
        } and not self.publishable_at:
            raise ValueError("A publication-eligible state requires publishable_at.")
        if lifecycle.value in TERMINAL_LIFECYCLE_STATES and not self.terminal_at:
            raise ValueError("A terminal lifecycle state requires terminal_at.")


@dataclass(frozen=True)
class PublicationGateFailure:
    failure_id: str
    code: str
    run_id: str
    summary: str
    technical_diagnosis: str
    materiality: str = "High"
    scope_type: str = "artifact"
    scope_id: str = ""
    retryable: bool = True
    required_source_families: tuple[str, ...] = ()
    blocking_rule: str = ""
    recommended_repair_kind: str = ""
    related_evidence_ids: tuple[str, ...] = ()
    related_finding_ids: tuple[str, ...] = ()
    related_claim_ids: tuple[str, ...] = ()
    related_action_ids: tuple[str, ...] = ()
    dependency_fingerprint: str = ""
    schema_version: str = "publication-gate-failure-v1"

    def __post_init__(self) -> None:
        PublicationFailureCode(self.code)
        if self.recommended_repair_kind:
            RepairKind(self.recommended_repair_kind)
        if not self.summary.strip() or not self.blocking_rule.strip():
            raise ValueError("A publication failure requires readable summary and rule.")


@dataclass
class ResearchRepairAction:
    action_id: str
    idempotency_key: str
    failure_ids: tuple[str, ...]
    repair_kind: str
    objective: str
    readable_explanation: str
    materiality: str = "High"
    priority: str = "High"
    execution_mode: str = "application_service"
    required_connector_ids: tuple[str, ...] = ()
    required_source_families: tuple[str, ...] = ()
    expected_information_gain: str = "material"
    cost_allowance_usd: float | None = None
    depth_allowance: int = 1
    dependency_action_ids: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    status: str = RepairStatus.PLANNED.value
    attempts: int = 0
    admitted_evidence_ids: tuple[str, ...] = ()
    conclusion_changes: tuple[str, ...] = ()
    resolution_summary: str = ""
    stopping_reason: str = ""
    schema_version: str = "research-repair-action-v1"

    def __post_init__(self) -> None:
        RepairKind(self.repair_kind)
        RepairStatus(self.status)
        if not self.failure_ids:
            raise ValueError("A repair action must address at least one failure.")
        if not self.objective.strip() or not self.completion_criteria:
            raise ValueError("A repair action requires an objective and completion criteria.")
        if self.depth_allowance < 0 or self.attempts < 0:
            raise ValueError("Repair depth and attempts cannot be negative.")


@dataclass(frozen=True)
class IndependentOriginRequirement:
    requirement_id: str
    analog_id: str
    quantified_claim: str
    required_origin_groups: int
    observed_origin_groups: tuple[str, ...] = ()
    missing_origin_classes: tuple[str, ...] = ()
    status: str = "open"
    resolution: str = ""
    schema_version: str = "independent-origin-requirement-v1"

    @property
    def satisfied(self) -> bool:
        return len(set(self.observed_origin_groups)) >= self.required_origin_groups


@dataclass(frozen=True)
class SemanticQualityDimension:
    name: str
    score: int
    maximum: int
    status: str
    explanation: str = ""


@dataclass(frozen=True)
class SemanticQualityEvaluation:
    evaluation_id: str
    run_id: str
    artifact_version: str
    evidence_hash: str
    total_score: int
    threshold: int
    status: str
    dimensions: tuple[SemanticQualityDimension, ...] = ()
    critical_failures: tuple[str, ...] = ()
    triggered_by: str = "initial_evaluation"
    evaluated_at: str = ""
    previous_evaluation_id: str = ""
    schema_version: str = "semantic-quality-evaluation-v1"


@dataclass(frozen=True)
class PublicationGateEvaluation:
    evaluation_id: str
    run_id: str
    artifact_version: str
    evidence_hash: str
    publication_policy_version: str
    validation_policy_version: str
    schema_version: str
    parser_version: str
    evaluated_at: str
    decision: str
    failures: tuple[PublicationGateFailure, ...] = ()
    repair_actions: tuple[ResearchRepairAction, ...] = ()
    applicable_gate_codes: tuple[str, ...] = ()
    passed_gate_codes: tuple[str, ...] = ()
    skipped_gate_codes: tuple[str, ...] = ()
    supersedes_evaluation_id: str = ""

    @property
    def publishable(self) -> bool:
        return self.decision == "publishable" and not self.failures


@dataclass(frozen=True)
class ResearchCoverageProfile:
    profile_id: str
    label: str
    required_financial_paths: tuple[str, ...]
    applicable_source_families: tuple[str, ...] = ()
    mapping_expectation: str = "automated_then_explicit_unresolved"
    schema_version: str = "research-coverage-profile-v1"


@dataclass
class DerivedArtifactDependency:
    artifact_id: str
    artifact_type: str
    evidence_hash: str = ""
    evidence_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    mapping_versions: tuple[str, ...] = ()
    financial_paths: tuple[str, ...] = ()
    analog_ids: tuple[str, ...] = ()
    audit_snapshot_id: str = ""
    source_policy_version: str = ""
    parser_versions: tuple[str, ...] = ()
    semantic_score_version: str = ""
    status: str = "current"
    invalidation_reasons: tuple[str, ...] = ()
    schema_version: str = "derived-artifact-dependency-v1"


@dataclass(frozen=True)
class SourceLineageNode:
    source_id: str
    origin_group: str
    ownership_group: str = ""
    syndication_fingerprint: str = ""
    upstream_issuer: str = ""
    dataset_lineage: str = ""
    derived_from_source_ids: tuple[str, ...] = ()
    schema_version: str = "source-lineage-node-v1"


@dataclass(frozen=True)
class ScopeInvalidationResult:
    invalidated_artifact_ids: tuple[str, ...]
    unchanged_artifact_ids: tuple[str, ...]
    reasons_by_artifact: dict[str, tuple[str, ...]]
    schema_version: str = "scope-invalidation-result-v1"


DEFAULT_COVERAGE_PROFILES = {
    "operating_company": ResearchCoverageProfile(
        profile_id="operating_company",
        label="Operating company",
        required_financial_paths=(
            "Free Cash Flow TTM",
            "Cash and Liquidity",
            "Long-term Debt",
            "Share Repurchases",
            "Capital Expenditure",
            "Working Capital",
            "Revenue and Margin Bridge",
        ),
    ),
    "bank_or_insurer": ResearchCoverageProfile(
        profile_id="bank_or_insurer",
        label="Bank or insurer",
        required_financial_paths=(
            "Capital Adequacy",
            "Credit Quality",
            "Net Interest Income",
            "Liquidity",
        ),
    ),
    "reit": ResearchCoverageProfile(
        profile_id="reit",
        label="REIT",
        required_financial_paths=("FFO/AFFO", "Leverage", "Occupancy", "Property NOI"),
    ),
    "commodity_producer": ResearchCoverageProfile(
        profile_id="commodity_producer",
        label="Commodity producer",
        required_financial_paths=("Production Volume", "Realized Price", "Unit Cost", "Reserves"),
    ),
    "adr_fpi": ResearchCoverageProfile(
        profile_id="adr_fpi",
        label="ADR or foreign private issuer",
        required_financial_paths=("Free Cash Flow TTM", "Debt", "Currency Translation"),
    ),
    "pre_revenue": ResearchCoverageProfile(
        profile_id="pre_revenue",
        label="Pre-revenue company",
        required_financial_paths=("Cash Runway", "Capital Needs", "Dilution"),
    ),
    "event_only": ResearchCoverageProfile(
        profile_id="event_only",
        label="Event investigation",
        required_financial_paths=(),
    ),
}


def stable_contract_id(prefix: str, *values: Any) -> str:
    payload = json.dumps(values, sort_keys=True, default=str, separators=(",", ":"))
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def deduplicate_repairs(
    actions: Iterable[ResearchRepairAction],
) -> tuple[ResearchRepairAction, ...]:
    by_key: dict[str, ResearchRepairAction] = {}
    for action in actions:
        existing = by_key.get(action.idempotency_key)
        if existing is None:
            by_key[action.idempotency_key] = action
            continue
        existing.failure_ids = tuple(sorted(set(existing.failure_ids + action.failure_ids)))
        existing.required_connector_ids = tuple(sorted(set(
            existing.required_connector_ids + action.required_connector_ids
        )))
        existing.required_source_families = tuple(sorted(set(
            existing.required_source_families + action.required_source_families
        )))
        existing.depth_allowance = max(existing.depth_allowance, action.depth_allowance)
    return tuple(by_key.values())
