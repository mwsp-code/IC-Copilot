from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


SAVED_RESEARCH_ARTIFACT_SCHEMA_VERSION = "saved-research-artifact-v2"
SAVED_RESEARCH_RESULT_SCHEMA_VERSION = "research-result-json-v1"
SAVED_RESEARCH_CODEC_VERSION = "research-result-codec-v2"


class SavedResearchStatus(str, Enum):
    COMPLETED = "completed"
    RESIDUAL_DILIGENCE = "residual_diligence"
    DRAFT = "draft"
    PARTIAL = "partial"
    FAILED = "failed"


class SavedResearchOrigin(str, Enum):
    LIVE_RESEARCH = "live_research"
    TARGETED_INVESTIGATION = "targeted_investigation"
    REPAIR_RECHECK = "repair_recheck"
    LEGACY_ADOPTED = "legacy_adopted"


class SavedResearchScope(str, Enum):
    LOCAL_WORKSPACE = "local_workspace"
    HOSTED_SESSION = "hosted_session"


class SavedArtifactHealth(str, Enum):
    UNCHECKED = "unchecked"
    VERIFIED = "verified"
    RUNTIME_INCOMPATIBLE = "runtime_incompatible"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    PAYLOAD_CORRUPT = "payload_corrupt"
    PAYLOAD_MISSING = "payload_missing"
    VERIFICATION_FAILED = "verification_failed"


@dataclass(frozen=True)
class ArtifactVerificationResult:
    status: SavedArtifactHealth
    verified_at: str = ""
    codec_version: str = SAVED_RESEARCH_CODEC_VERSION
    writer_runtime: str = ""
    reason: str = ""
    failure_path: str = ""
    annotation: str = ""

    @property
    def verified(self) -> bool:
        return self.status is SavedArtifactHealth.VERIFIED


@dataclass(frozen=True)
class SavedResearchArtifact:
    artifact_id: str
    run_id: str
    security_key: str
    ticker: str
    company_name: str
    exchange: str
    cik: str
    ticker_aliases: tuple[str, ...]
    research_profile: str
    source_policy_summary: str
    operation_kind: str
    operation_label: str
    lifecycle_state: str
    validation_status: str
    independent_qa_status: str
    closure_status: str
    publication_status: str
    outcome: str
    status: SavedResearchStatus
    origin: SavedResearchOrigin
    created_at: str
    completed_at: str
    saved_at: str
    data_cutoff: str
    source_freshness: str
    evidence_hash: str
    result_version: str
    preferred_route: str
    setup_summary: dict[str, Any]
    payload_reference: str
    payload_sha256: str
    payload_size_bytes: int
    compressed_size_bytes: int
    storage_scope: SavedResearchScope
    scope_id: str
    analysis_capabilities: tuple[str, ...] = ()
    parent_artifact_id: str = ""
    supersedes_artifact_id: str = ""
    pinned: bool = False
    sanitization_status: str = "passed"
    health: SavedArtifactHealth = SavedArtifactHealth.UNCHECKED
    verified_at: str = ""
    codec_version: str = SAVED_RESEARCH_CODEC_VERSION
    writer_runtime: str = ""
    last_verification_reason: str = ""
    last_verification_path: str = ""
    artifact_schema_version: str = SAVED_RESEARCH_ARTIFACT_SCHEMA_VERSION
    result_schema_version: str = SAVED_RESEARCH_RESULT_SCHEMA_VERSION

    @property
    def is_completed(self) -> bool:
        return self.status in {
            SavedResearchStatus.COMPLETED,
            SavedResearchStatus.RESIDUAL_DILIGENCE,
        }


SavedResearchSummary = SavedResearchArtifact


@dataclass(frozen=True)
class SavedResearchQuery:
    scope_id: str
    ticker: str = ""
    security_key: str = ""
    include_drafts: bool = True
    completed_only: bool = False
    limit: int = 25
    offset: int = 0


@dataclass(frozen=True)
class SaveResearchResultCommand:
    result: Any
    run_id: str
    operation_kind: str = "full_research"
    operation_label: str = ""
    lifecycle_state: str = "completed_not_publishable"
    outcome: str = "completed"
    status: SavedResearchStatus = SavedResearchStatus.COMPLETED
    origin: SavedResearchOrigin = SavedResearchOrigin.LIVE_RESEARCH
    created_at: str = ""
    completed_at: str = ""
    preferred_route: str = "Decision"
    setup_summary: dict[str, Any] = field(default_factory=dict)
    storage_scope: SavedResearchScope = SavedResearchScope.LOCAL_WORKSPACE
    scope_id: str = "local"
    parent_artifact_id: str = ""
    supersedes_artifact_id: str = ""


@dataclass(frozen=True)
class LoadSavedResearchResult:
    artifact: SavedResearchArtifact
    result: Any


@dataclass(frozen=True)
class DisplayedResearchContext:
    """Authoritative, non-secret context for the result currently on screen.

    Presentation code must never reconstruct a focused investigation from a
    mixture of session-state wording and legacy labels.  This object is built
    from saved-artifact metadata (with narrow compatibility aliases) and is
    passed to every route that needs to explain the displayed result.
    """

    artifact_id: str = ""
    run_id: str = ""
    operation_kind: str = ""
    operation_label: str = ""
    operation_action_id: str = ""
    research_intent_id: str = ""
    parent_artifact_id: str = ""
    evidence_hash: str = ""
    data_cutoff: str = ""
    is_derived_revision: bool = False
    outcome: str = ""


@dataclass(frozen=True)
class DisplayedInvestigationReference:
    """Small, artifact-backed reference used to rebuild the journey UI."""

    artifact_id: str = ""
    run_id: str = ""
    operation_kind: str = ""
    operation_label: str = ""
    action_id: str = ""
    intent_id: str = ""
    parent_artifact_id: str = ""
    evidence_hash: str = ""
    data_cutoff: str = ""


@dataclass(frozen=True)
class DerivedEvidenceRecoveryAttempt:
    """Durable one-time deterministic recovery for a derived analysis."""

    recovery_key: str
    scope_id: str
    parent_artifact_id: str
    intent_id: str
    data_cutoff: str
    analysis_version: str
    status: str
    attempted_at: str = ""
    source_snapshot_hash: str = ""
    derived_artifact_id: str = ""
    readable_reason: str = ""


@dataclass(frozen=True)
class SavedResearchRetentionPolicy:
    keep_until_deleted: bool = True
    hosted_ttl_seconds: int = 14_400


class SavedResearchRepository(Protocol):
    def save(self, artifact: SavedResearchArtifact) -> SavedResearchArtifact: ...

    def get(self, artifact_id: str, *, scope_id: str) -> SavedResearchArtifact | None: ...

    def list(self, query: SavedResearchQuery) -> list[SavedResearchArtifact]: ...

    def delete(self, artifact_id: str, *, scope_id: str) -> bool: ...

    def set_pinned(self, artifact_id: str, *, scope_id: str, pinned: bool) -> bool: ...

    def payload_reference_count(self, payload_reference: str) -> int: ...

    def set_last_displayed(self, *, scope_id: str, artifact_id: str) -> None: ...

    def get_last_displayed(self, *, scope_id: str) -> str: ...

    def update_health(
        self,
        artifact_id: str,
        *,
        scope_id: str,
        verification: ArtifactVerificationResult,
    ) -> SavedResearchArtifact | None: ...

    def get_recovery_attempt(
        self, recovery_key: str, *, scope_id: str
    ) -> DerivedEvidenceRecoveryAttempt | None: ...

    def save_recovery_attempt(
        self, attempt: DerivedEvidenceRecoveryAttempt
    ) -> DerivedEvidenceRecoveryAttempt: ...


class SavedResearchBlobStore(Protocol):
    def put(self, payload: bytes) -> tuple[str, str, int]: ...

    def get(self, reference: str, *, expected_sha256: str) -> bytes: ...

    def delete(self, reference: str) -> None: ...


class SavedResearchScopeProvider(Protocol):
    def scope(self) -> tuple[SavedResearchScope, str]: ...


class ResearchResultArtifactCodec(Protocol):
    def encode(self, result: Any) -> bytes: ...

    def decode(self, payload: bytes) -> Any: ...

    def verify(self, payload: bytes) -> tuple[Any, ArtifactVerificationResult]: ...
