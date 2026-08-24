from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SourceCapabilityStatus(str, Enum):
    READY = "ready"
    CACHED_ONLY = "cached_only"
    ENTITLEMENT_REQUIRED = "entitlement_required"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"


class ResearchQuestionStatus(str, Enum):
    PLANNED = "planned"
    RESEARCHING = "researching"
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    BLOCKED_BY_SOURCE_ACCESS = "blocked_by_source_access"
    BLOCKED_BY_PROVIDER_FAILURE = "blocked_by_provider_failure"
    CANCELLED = "cancelled"
    INVALIDATED_BY_NEW_EVIDENCE = "invalidated_by_new_evidence"


class ResearchSessionStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class CredentialReference:
    """Opaque application-only reference; never include it in model input."""

    reference_id: str
    source_label: str
    scope: str = "local"
    expires_at: str = ""


@dataclass(frozen=True)
class SourceCapability:
    capability_id: str
    label: str
    source_families: tuple[str, ...]
    operations: tuple[str, ...]
    status: str
    provider: str = ""
    freshness: str = "unknown"
    available_date_range: str = ""
    licensing_policy: str = ""
    storage_policy: str = ""
    cost_note: str = ""
    rate_limit_note: str = ""
    entitlement_note: str = ""
    credential_reference: CredentialReference | None = None

    def __post_init__(self) -> None:
        SourceCapabilityStatus(self.status)

    def as_model_capability(self) -> dict[str, Any]:
        """Return a secret-free description suitable for model prompts."""

        return {
            "capability_id": self.capability_id,
            "label": self.label,
            "source_families": list(self.source_families),
            "operations": list(self.operations),
            "status": self.status,
            "provider": self.provider,
            "freshness": self.freshness,
            "available_date_range": self.available_date_range,
            "licensing_policy": self.licensing_policy,
            "storage_policy": self.storage_policy,
            "cost_note": self.cost_note,
            "rate_limit_note": self.rate_limit_note,
            "entitlement_note": self.entitlement_note,
        }


@dataclass(frozen=True)
class ResearchQuestionDependency:
    question_key: str
    dependency_type: str = "supports"
    material: bool = True


@dataclass(frozen=True)
class ResearchQuestionTemplate:
    question_key: str
    domain: str
    title: str
    objective: str
    decision_relevance: str
    confirming_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    suggested_source_families: tuple[str, ...] = ()
    analytical_tools: tuple[str, ...] = ()
    dependencies: tuple[ResearchQuestionDependency, ...] = ()
    applicable_archetypes: tuple[str, ...] = ("all",)
    expected_answer_shape: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    default_materiality: str = "Medium"
    template_version: str = "research-question-template-v1"


@dataclass
class EvidenceCoveragePlan:
    confirming_needed: list[str] = field(default_factory=list)
    contradicting_needed: list[str] = field(default_factory=list)
    confirming_covered: list[str] = field(default_factory=list)
    contradicting_covered: list[str] = field(default_factory=list)
    unresolved_items: list[str] = field(default_factory=list)
    coverage_score: int = 0
    material_gaps: list[str] = field(default_factory=list)


@dataclass
class ResearchAnswer:
    question_id: str
    status: str
    answer: str
    confidence: str = "Unknown"
    confidence_reason: str = ""
    mechanism: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    contrary_evidence_ids: list[str] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)
    strongest_limitation: str = ""
    remaining_work: list[str] = field(default_factory=list)
    changed_conclusions: list[str] = field(default_factory=list)
    intent_id: str = ""
    evidence_hash: str = ""
    answer_version: str = "research-answer-v2"

    def __post_init__(self) -> None:
        ResearchQuestionStatus(self.status)


@dataclass
class ResearchAgendaItem:
    question_id: str
    question_key: str
    domain: str
    title: str
    objective: str
    decision_relevance: str
    materiality: str = "Medium"
    priority: int = 50
    status: str = ResearchQuestionStatus.PLANNED.value
    source_families: list[str] = field(default_factory=list)
    analytical_tools: list[str] = field(default_factory=list)
    confirming_evidence: list[str] = field(default_factory=list)
    contradicting_evidence: list[str] = field(default_factory=list)
    dependency_question_ids: list[str] = field(default_factory=list)
    expected_answer_shape: list[str] = field(default_factory=list)
    selection_reason: str = ""
    coverage: EvidenceCoveragePlan = field(default_factory=EvidenceCoveragePlan)
    answer: ResearchAnswer | None = None
    intent_id: str = ""
    intent_identity_version: str = "research-intent-v1"
    mechanism_key: str = ""
    target_type: str = "company"
    target_id: str = ""
    period_scope: str = ""
    event_id: str = ""
    decision_dependency: str = ""

    def __post_init__(self) -> None:
        ResearchQuestionStatus(self.status)


@dataclass
class ResearchAgenda:
    agenda_id: str
    ticker: str
    company_name: str
    research_profile: str
    items: list[ResearchAgendaItem] = field(default_factory=list)
    selected_question_id: str = ""
    security_archetype: str = "operating_company"
    library_version: str = "comprehensive-research-agenda-v1"
    generated_at: str = ""
    selection_summary: str = ""
    security_key: str = ""


@dataclass(frozen=True)
class AgentResearchBudget:
    budget_id: str
    label: str
    max_elapsed_seconds: int
    max_tool_actions: int
    max_model_turns: int
    max_cost_usd: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ResearchToolCall:
    call_id: str
    tool_name: str
    question_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    objective: str = ""
    reason: str = ""
    created_at: str = ""


@dataclass
class ResearchObservation:
    observation_id: str
    call_id: str
    question_id: str
    tool_name: str
    status: str
    summary: str
    source_family: str = ""
    source_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    content: list[dict[str, Any]] = field(default_factory=list)
    failure_code: str = ""
    decision_eligible: bool = False
    observed_at: str = ""


@dataclass(frozen=True)
class ResearchNotebookEntry:
    entry_id: str
    question_id: str
    entry_type: str
    summary: str
    source_observation_ids: tuple[str, ...] = ()
    created_at: str = ""


@dataclass
class AgentResearchSession:
    session_id: str
    ticker: str
    role: str
    provider: str
    model: str
    status: str
    agenda: ResearchAgenda
    budget: AgentResearchBudget
    started_at: str
    completed_at: str = ""
    current_stage: str = "planning"
    model_turns: int = 0
    tool_actions: int = 0
    elapsed_seconds: float = 0.0
    estimated_cost_usd: float | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    observations: list[ResearchObservation] = field(default_factory=list)
    notebook: list[ResearchNotebookEntry] = field(default_factory=list)
    answers: list[ResearchAnswer] = field(default_factory=list)
    stopping_reason: str = ""
    data_gaps: list[str] = field(default_factory=list)
    session_version: str = "agent-research-session-v1"

    def __post_init__(self) -> None:
        ResearchSessionStatus(self.status)
