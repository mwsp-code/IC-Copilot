from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
import unicodedata
from typing import Any, Iterable

from ..application.investigation_journey import InvestigationJourney
from ..application.investigation_action_canonicalization import (
    canonicalize_investigation_actions,
)
from ..application.research_request_outcomes import (
    ResearchIntentAnswerResolver,
    build_research_request_outcome,
    research_request_topic,
)
from ..application.cash_conversion import validate_cash_conversion_recurrence_bridge
from ..decision_intelligence import build_decision_recommendation
from ..domain.research_ontology import (
    AnalogResearchState,
    SecurityResearchProfileResolver,
    build_research_assessments,
)


_RAW_ID = re.compile(r"\b[a-f0-9]{10,64}\b", re.I)
_COUNTER_GAP_PHRASES = (
    "no material counter-evidence identified",
    "no material counter evidence identified",
    "no counter-thesis",
    "none found",
)


@dataclass(frozen=True)
class EvidenceReferenceViewModel:
    label: str
    date: str = ""
    source_type: str = ""
    url: str = ""
    technical_id: str = ""


@dataclass(frozen=True)
class ValuationCaseViewModel:
    name: str
    probability: str
    fair_value: str
    method: str


@dataclass(frozen=True)
class MarketImpliedAssumptionViewModel:
    key: str
    name: str
    value: float | None
    unit: str
    provenance: str
    source: str
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


@dataclass(frozen=True)
class MarketExpectationsViewModel:
    status: str
    methodology: str
    price_label: str
    current_price: str
    price_as_of: str
    price_source: str
    operating_base_label: str
    operating_base_value: str
    valuation_anchor_label: str
    valuation_anchor_value: str
    implied_variable_label: str
    implied_variable_value: str
    internal_base_label: str
    internal_base_value: str
    disagreement: str
    interpretation: str
    confidence: str
    weakest_input: str
    assumptions: tuple[MarketImpliedAssumptionViewModel, ...] = ()


@dataclass(frozen=True)
class HistoricalAnalogCardViewModel:
    analog_id: str
    episode: str
    kind: str
    status: str
    similarity: str
    insight: str
    material_difference: str
    monitor: str
    limitation: str
    source_quality: str


@dataclass(frozen=True)
class StoryLensViewModel:
    title: str
    status: str
    insight: str
    next_action: str = ""
    evidence_count: int = 0


@dataclass(frozen=True)
class DebateViewModel:
    status: str
    bull_case: str
    bear_case: str
    judge_accepts: tuple[str, ...] = ()
    still_unproven: tuple[str, ...] = ()
    resolution_actions: tuple[str, ...] = ()
    action_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvestigationActionViewModel:
    rank: int
    action_id: str
    action_kind: str
    title: str
    objective: str
    status: str
    materiality: str
    why_it_matters: str
    execution_mode: str
    auto_resolvable: bool
    event_id: str = ""
    attempt_summary: str = ""
    blocking_reason: str = ""
    completion_criteria: tuple[str, ...] = ()
    source_families: tuple[str, ...] = ()
    setup_steps: tuple[str, ...] = ()
    primary_cta: str = ""
    intent_type: str = "configure_source"
    allow_price_upload: bool = False


@dataclass(frozen=True)
class DecisionPageIntent:
    action_id: str
    intent_type: str
    event_id: str = ""
    uploaded_csv: bytes = b""


@dataclass(frozen=True)
class ResearchLaunchIntentViewModel:
    ticker: str
    operation_kind: str
    operation_type_label: str
    operation_label: str
    objective: str
    status: str
    research_profile: str
    source_summary: str
    qa_summary: str
    preferred_result_route: str
    primary_cta: str
    can_launch: bool
    needs_source_setup: bool
    blocking_reason: str = ""


def build_research_launch_intent_view_model(
    intent: Any,
) -> ResearchLaunchIntentViewModel:
    operation_kind = str(
        getattr(getattr(intent, "operation_kind", ""), "value", None)
        or getattr(intent, "operation_kind", "")
        or "full_research"
    )
    status = str(
        getattr(getattr(intent, "status", ""), "value", None)
        or getattr(intent, "status", "")
        or "selected"
    )
    type_label = {
        "full_research": "Full research refresh",
        "event_investigation": "Focused event investigation",
        "action_investigation": "Action investigation",
        "targeted_investigation": "Action investigation",
        "analog_investigation": "Historical analog investigation",
        "repair_recheck": "Repair and recheck",
    }.get(operation_kind, operation_kind.replace("_", " ").title())
    primary_cta = {
        "full_research": "Run full research",
        "event_investigation": "Run selected event investigation",
        "action_investigation": "Run selected investigation",
        "targeted_investigation": "Run selected investigation",
        "analog_investigation": "Run selected analog investigation",
        "repair_recheck": "Run repair and recheck",
    }.get(operation_kind, "Run selected research task")
    source_families = tuple(
        str(item).replace("_", " ")
        for item in (getattr(intent, "required_source_families", ()) or ())
    )
    needs_source_setup = status == "needs_source_setup"
    return ResearchLaunchIntentViewModel(
        ticker=str(getattr(intent, "ticker", "") or ""),
        operation_kind=operation_kind,
        operation_type_label=type_label,
        operation_label=str(getattr(intent, "operation_label", "") or type_label),
        objective=str(getattr(intent, "objective", "") or ""),
        status=status,
        research_profile=str(
            getattr(intent, "research_profile", "") or "adaptive_ic"
        ),
        source_summary=(
            ", ".join(source_families)
            if source_families
            else "Configured and entitled sources"
        ),
        qa_summary=(
            "Independent QA enabled"
            if bool(getattr(intent, "qa_enabled", False))
            else "Independent QA not requested"
        ),
        preferred_result_route=str(
            getattr(intent, "preferred_result_route", "") or "Decision"
        ),
        primary_cta=primary_cta,
        can_launch=status in {"selected", "ready"},
        needs_source_setup=needs_source_setup,
        blocking_reason=str(getattr(intent, "blocking_reason", "") or ""),
    )


class ResearchSubroute(str, Enum):
    THESIS_DRIVERS = "Thesis & Drivers"
    MODEL_VALUATION = "Model & Valuation"
    EXPECTATIONS_REACTION = "Expectations & Reaction"
    ANALOGS_PEERS = "Analogs & Peers"


@dataclass(frozen=True)
class ResearchRouteViewModel:
    route: ResearchSubroute
    title: str
    metrics: tuple[tuple[str, str], ...]
    what_matters: tuple[str, ...]
    strongest_limitation: str
    action_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValuationInputResolutionViewModel:
    input_name: str
    status: str
    value: str
    source: str
    formula: str
    reason: str


@dataclass(frozen=True)
class ThesisDriversViewModel(ResearchRouteViewModel):
    pass


@dataclass(frozen=True)
class ModelValuationViewModel(ResearchRouteViewModel):
    input_resolutions: tuple[ValuationInputResolutionViewModel, ...] = ()


@dataclass(frozen=True)
class ExpectationsReactionViewModel(ResearchRouteViewModel):
    pass


@dataclass(frozen=True)
class AnalogsPeersViewModel(ResearchRouteViewModel):
    pass


@dataclass(frozen=True)
class ResearchQuestionAnswerViewModel:
    question_id: str
    domain: str
    title: str
    status: str
    answer: str
    confidence: str
    evidence_count: int
    strongest_limitation: str = ""
    remaining_work: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchSessionViewModel:
    session_status: str
    role: str
    provider_label: str
    budget_label: str
    answered_count: int
    partial_count: int
    total_count: int
    stopping_reason: str
    questions: tuple[ResearchQuestionAnswerViewModel, ...] = ()


@dataclass(frozen=True)
class DecisionBriefViewModel:
    ticker: str
    company_name: str
    run_status: str
    freshness: str
    evidence_quality: str
    verdict: str
    stage: str
    direction: str
    candidate_direction: str
    recommendation_reason: str
    confidence: str
    thesis: str
    why_now: str
    why_now_evidence: tuple[EvidenceReferenceViewModel, ...]
    priced_in: str
    valuation_range: str
    probability_weighted_value: str
    expected_return: str
    valuation_confidence: str
    valuation_cases: tuple[ValuationCaseViewModel, ...]
    counter_thesis: str
    counter_thesis_status: str
    next_actions: tuple[str, ...]
    independent_qa_status: str
    market_expectations: MarketExpectationsViewModel | None = None
    historical_analogs: tuple[HistoricalAnalogCardViewModel, ...] = ()
    story_lenses: tuple[StoryLensViewModel, ...] = ()
    debate: DebateViewModel | None = None
    technical_evidence_ids: tuple[str, ...] = ()
    investigation_actions: tuple[InvestigationActionViewModel, ...] = ()
    publication_label: str = "Research draft · validation or QA incomplete"
    publication_qualifications: tuple[str, ...] = ()


@dataclass(frozen=True)
class IntegrityTimelineEventViewModel:
    label: str
    status: str
    summary: str
    timestamp: str = ""
    origin: str = ""
    technical_details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntegrityTimelineViewModel:
    status: str
    events: tuple[IntegrityTimelineEventViewModel, ...]
    stopping_reason: str
    recursion_depth: int
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class RepairActionCardViewModel:
    action_id: str
    title: str
    status: str
    why_it_matters: str
    what_the_app_will_do: str
    source_families: tuple[str, ...] = ()
    attempts: int = 0
    result_or_blocker: str = ""


@dataclass(frozen=True)
class RepairProgressStageViewModel:
    label: str
    status: str


@dataclass(frozen=True)
class PublicationReadinessViewModel:
    research_status: str
    validation_status: str
    independent_qa_status: str
    semantic_quality: str
    publication_eligibility: str
    summary: str
    repairs_remaining: int
    progress_label: str
    repair_cards: tuple[RepairActionCardViewModel, ...] = ()
    can_repair: bool = False
    publication_eligible: bool = False
    qualification_summaries: tuple[str, ...] = ()
    repair_progress: tuple[RepairProgressStageViewModel, ...] = ()
    primary_action_label: str = "Repair and recheck"
    execution_note: str = ""


@dataclass(frozen=True)
class PublicationRepairIntent:
    intent_type: str
    action_ids: tuple[str, ...] = ()
    retry_stages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchRequestCardViewModel:
    request_id: str
    objective: str
    status: str
    priority: str
    source_families: tuple[str, ...] = ()
    confirming_evidence: tuple[str, ...] = ()
    contradicting_evidence: tuple[str, ...] = ()
    attempted_connectors: tuple[str, ...] = ()
    stopping_rule: str = ""
    resolution_summary: str = ""
    answer_status: str = "planned"
    answer: str = ""
    evidence_found: tuple[str, ...] = ()
    evidence_missing: tuple[str, ...] = ()
    next_step: str = ""
    admitted_evidence_count: int = 0
    confidence: str = "Unknown"
    confidence_reason: str = ""
    blocking_reason: str = ""
    search_summary: str = ""
    reading_summary: str = ""
    extraction_summary: str = ""
    calculation_summary: str = ""


@dataclass(frozen=True)
class CandidateStatusViewModel:
    candidate_id: str
    candidate_type: str
    title: str
    status: str
    origin: str
    materiality: str
    source_family: str = ""
    url: str = ""
    decision_reason: str = ""
    research_request_id: str = ""
    scope_status: str = "unverified"
    relationship_to_subject: str = "primary"
    relevant: bool = True


@dataclass(frozen=True)
class InvestigationJourneyStageViewModel:
    label: str
    status: str


@dataclass(frozen=True)
class InvestigationJourneyViewModel:
    operation_label: str
    objective: str
    ticker: str
    current_stage: str
    stages: tuple[InvestigationJourneyStageViewModel, ...]
    active: bool
    elapsed_label: str = ""
    qa_label: str = "Independent QA not active"
    progress_percent: int = 0
    completed_stage_count: int = 0
    progress_label: str = ""
    outcome: str = ""
    persistence_status: str = ""
    admitted_evidence_count: int = 0
    change_summaries: tuple[str, ...] = ()
    remaining_actions: tuple[str, ...] = ()
    answer_status: str = ""
    direct_answer: str = ""
    confidence: str = "Unknown"
    key_evidence: tuple[str, ...] = ()
    strongest_limitation: str = ""
    run_status: str = ""
    qa_state: str = "not_run"
    execution_state: str = ""
    execution_last_activity_at: str = ""
    execution_hard_deadline_at: str = ""


@dataclass(frozen=True)
class EvidenceTrailStepViewModel:
    label: str
    status: str
    summary: str


@dataclass(frozen=True)
class EvidenceInvestigationTrailViewModel:
    question: str
    outcome: str
    steps: tuple[EvidenceTrailStepViewModel, ...]
    answer_status: str = "unanswered"
    answer: str = ""
    evidence_found: tuple[str, ...] = ()
    evidence_missing: tuple[str, ...] = ()
    next_step: str = ""
    relevant_candidate_count: int = 0
    hidden_candidate_count: int = 0
    confidence: str = "Unknown"
    coverage: str = "Unknown"
    answer_basis: str = ""
    qa_freshness: str = "not_reviewed"
    period_comparison: tuple[dict[str, str], ...] = ()
    component_sections: tuple[tuple[str, tuple[dict[str, str], ...]], ...] = ()


@dataclass(frozen=True)
class EvidencePageViewModel:
    admitted_count: int
    excluded_count: int
    contradiction_count: int
    evidence_hash: str
    requests: tuple[ResearchRequestCardViewModel, ...] = ()
    candidates: tuple[CandidateStatusViewModel, ...] = ()
    request_groups: tuple[tuple[str, tuple[ResearchRequestCardViewModel, ...]], ...] = ()
    investigation_trail: EvidenceInvestigationTrailViewModel | None = None
    relevant_candidate_count: int = 0
    hidden_candidate_count: int = 0


@dataclass(frozen=True)
class SavedResearchItemViewModel:
    artifact_id: str
    ticker: str
    company_name: str
    operation_label: str
    research_profile: str
    status: str
    outcome: str
    completed_at: str
    qa_status: str
    publication_status: str
    preferred_route: str
    pinned: bool = False
    health: str = "unchecked"


@dataclass(frozen=True)
class SavedResearchLibraryViewModel:
    completed: tuple[SavedResearchItemViewModel, ...]
    drafts: tuple[SavedResearchItemViewModel, ...]
    current_ticker: str
    session_only: bool


def build_saved_research_library_view_model(
    artifacts: list[Any] | tuple[Any, ...],
    *,
    current_ticker: str,
    session_only: bool,
) -> SavedResearchLibraryViewModel:
    items = tuple(
        SavedResearchItemViewModel(
            artifact_id=str(getattr(item, "artifact_id", "") or ""),
            ticker=str(getattr(item, "ticker", "") or ""),
            company_name=str(getattr(item, "company_name", "") or ""),
            operation_label=str(getattr(item, "operation_label", "") or "Research"),
            research_profile=str(getattr(item, "research_profile", "") or "Standard"),
            status=str(
                getattr(getattr(item, "status", ""), "value", None)
                or getattr(item, "status", "")
                or "draft"
            ),
            outcome=str(getattr(item, "outcome", "") or "unknown"),
            completed_at=str(getattr(item, "completed_at", "") or ""),
            qa_status=str(getattr(item, "independent_qa_status", "") or "not_run"),
            publication_status=str(getattr(item, "publication_status", "") or "not_evaluated"),
            preferred_route=str(getattr(item, "preferred_route", "") or "Decision"),
            pinned=bool(getattr(item, "pinned", False)),
            health=str(
                getattr(getattr(item, "health", ""), "value", None)
                or getattr(item, "health", "")
                or "unchecked"
            ),
        )
        for item in artifacts
    )
    completed = tuple(
        item for item in items
        if item.status in {"completed", "residual_diligence"}
    )
    drafts = tuple(item for item in items if item not in completed)
    return SavedResearchLibraryViewModel(
        completed=completed,
        drafts=drafts,
        current_ticker=current_ticker.upper(),
        session_only=session_only,
    )


def build_evidence_page_view_model(
    result: Any,
    *,
    displayed_context: Any | None = None,
    operation_kind: str = "",
    operation_label: str = "",
    operation_action_id: str = "",
    outcome: str = "",
) -> EvidencePageViewModel:
    if displayed_context is not None:
        operation_kind = str(
            getattr(displayed_context, "operation_kind", "") or operation_kind
        )
        operation_label = str(
            getattr(displayed_context, "operation_label", "") or operation_label
        )
        operation_action_id = str(
            getattr(displayed_context, "operation_action_id", "")
            or operation_action_id
        )
        outcome = str(getattr(displayed_context, "outcome", "") or outcome)
    displayed_intent_id = str(
        getattr(displayed_context, "research_intent_id", "") or ""
    )
    graph = getattr(result, "evidence_graph", None)
    qa = getattr(result, "independent_research_qa", None)
    request_items = list(
        getattr(qa, "research_requests", [])
        or getattr(qa, "evidence_requests", [])
        or []
    )[:20]
    request_models = []
    for item in request_items:
        request_outcome = build_research_request_outcome(result, item)
        request_models.append(ResearchRequestCardViewModel(
            request_id=str(getattr(item, "request_id", "") or ""),
            objective=_normalize_display_text(str(
                getattr(item, "objective", "")
                or getattr(item, "question", "")
                or "Targeted investigation"
            )),
            status=str(getattr(item, "status", "planned") or "planned"),
            priority=str(getattr(item, "priority", "Medium") or "Medium"),
            source_families=tuple(
                getattr(item, "required_source_families", ())
                or getattr(item, "source_types", ())
                or ()
            ),
            confirming_evidence=tuple(
                getattr(item, "desired_confirming_evidence", ()) or ()
            ),
            contradicting_evidence=tuple(
                getattr(item, "desired_contradicting_evidence", ()) or ()
            ),
            attempted_connectors=tuple(
                getattr(item, "attempted_connector_ids", ()) or ()
            ),
            stopping_rule=_normalize_display_text(str(getattr(item, "stopping_rule", "") or "")),
            resolution_summary=_normalize_display_text(str(
                getattr(item, "resolution_summary", "") or ""
            )),
            answer_status=request_outcome.status,
            answer=_normalize_display_text(request_outcome.answer),
            evidence_found=tuple(
                _normalize_display_text(value)
                for value in request_outcome.evidence_found
            ),
            evidence_missing=tuple(
                _normalize_display_text(value)
                for value in request_outcome.evidence_missing
            ),
            next_step=_normalize_display_text(request_outcome.next_step),
            admitted_evidence_count=request_outcome.admitted_evidence_count,
            confidence=str(getattr(item, "confidence", "Unknown") or "Unknown"),
            confidence_reason=_normalize_display_text(str(
                getattr(item, "confidence_reason", "") or ""
            )),
            blocking_reason=_normalize_display_text(str(
                getattr(item, "blocking_reason", "") or ""
            )),
            search_summary=_normalize_display_text(str(
                getattr(item, "search_summary", "") or ""
            )),
            reading_summary=_normalize_display_text(str(
                getattr(item, "reading_summary", "") or ""
            )),
            extraction_summary=_normalize_display_text(str(
                getattr(item, "extraction_summary", "") or ""
            )),
            calculation_summary=_normalize_display_text(str(
                getattr(item, "calculation_summary", "") or ""
            )),
        ))
    requests = tuple(request_models)
    decisions = {
        str(getattr(item, "candidate_id", "")): item
        for item in list(getattr(graph, "admission_decisions", []) or [])
    }
    candidates = []
    for item in list(getattr(qa, "candidate_discovery_items", []) or [])[:40]:
        candidate_id = str(getattr(item, "candidate_id", "") or "")
        decision = decisions.get(candidate_id)
        decision_reason = _normalize_display_text(str(getattr(decision, "reason", "") or ""))
        scope_status = str(getattr(item, "scope_status", "unverified") or "unverified")
        if _is_scope_mismatch(scope_status, decision_reason):
            scope_status = "scope_mismatch"
        candidate_status = str(
            getattr(item, "status", "quarantined") or "quarantined"
        )
        request_id = str(getattr(item, "research_request_id", "") or "")
        relationship = str(
            getattr(item, "relationship_to_subject", "primary") or "primary"
        )
        routine_relevant = (
            scope_status != "scope_mismatch"
            and (
                candidate_status.casefold()
                not in {"rejected", "expired", "retrieval_failed"}
                or bool(request_id)
            )
        )
        candidates.append(CandidateStatusViewModel(
            candidate_id=candidate_id,
            candidate_type=str(getattr(item, "candidate_type", "source_candidate") or "source_candidate"),
            title=_normalize_display_text(str(
                getattr(item, "title", "")
                or getattr(item, "objective", "")
                or "Research candidate"
            )),
            status=candidate_status,
            origin=" / ".join(
                value for value in (
                    str(getattr(item, "origin_role", "") or ""),
                    str(getattr(item, "origin_provider", "") or ""),
                ) if value
            ),
            materiality=str(getattr(item, "materiality", "Medium") or "Medium"),
            source_family=str(getattr(item, "proposed_source_family", "") or ""),
            url=str(getattr(item, "url", "") or ""),
            decision_reason=decision_reason,
            research_request_id=request_id,
            scope_status=scope_status,
            relationship_to_subject=relationship,
            relevant=routine_relevant,
        ))
        if not candidates[-1].decision_reason:
            history = list(getattr(item, "status_history", ()) or ())
            if history:
                candidates[-1] = CandidateStatusViewModel(
                    **{
                        **candidates[-1].__dict__,
                        "decision_reason": _normalize_display_text(str(
                            getattr(history[-1], "reason", "") or ""
                        )),
                    }
                )
                if _is_scope_mismatch(
                    candidates[-1].scope_status,
                    candidates[-1].decision_reason,
                ):
                    candidates[-1] = CandidateStatusViewModel(
                        **{
                            **candidates[-1].__dict__,
                            "scope_status": "scope_mismatch",
                            "relevant": False,
                        }
                    )
    canonical_actions = _canonical_investigation_actions(result)
    queue = list(canonical_actions.actions)
    canonical_operation_action_id = (
        canonical_actions.resolve_action_id(operation_action_id)
        or operation_action_id
    )
    selected_action = next((
        item for item in queue
        if canonical_operation_action_id and (
            str(getattr(item, "action_id", "") or "")
            == canonical_operation_action_id
            or operation_action_id in list(
                getattr(item, "related_research_request_ids", ()) or ()
            )
        )
    ), None)
    focused_request_ids = set(
        getattr(selected_action, "related_research_request_ids", ()) or ()
    )
    if operation_action_id:
        focused_request_ids.add(operation_action_id)
    focused_requests = tuple(
        item for item in requests if item.request_id in focused_request_ids
    )
    if not focused_requests and (
        operation_action_id or operation_label or selected_action is not None
    ):
        focused_requests = _infer_focused_research_requests(
            requests,
            operation_label=operation_label,
            action_kind=str(
                getattr(selected_action, "action_kind", "") or ""
            ),
            action_objective=str(
                getattr(selected_action, "objective", "")
                or getattr(selected_action, "title", "")
                or ""
            ),
        )
        focused_request_ids.update(
            item.request_id for item in focused_requests if item.request_id
        )
    focused_operation = bool(
        operation_action_id or operation_label or selected_action is not None
    )
    focused_candidates = tuple(
        item for item in candidates
        if item.relevant and (
            not focused_operation
            or item.research_request_id in focused_request_ids
        )
    )
    hidden_candidates = tuple(
        item for item in candidates if item not in focused_candidates
    )
    request_groups = _group_research_requests(requests)
    investigation_trail = None
    if operation_kind in {
        "targeted_investigation", "action_investigation", "event_investigation",
        "analog_investigation", "repair_recheck", "repair_and_recheck",
    } or operation_action_id:
        trail_requests = focused_requests
        trail_candidates = focused_candidates
        candidate_admitted = sum(
            item.status == "admitted" for item in trail_candidates
        )
        rejected = sum(item.status == "rejected" for item in trail_candidates)
        attempted_connectors = sorted({
            connector
            for request in trail_requests
            for connector in request.attempted_connectors
            if connector
        })
        action_status = str(getattr(selected_action, "status", "") or outcome or "completed")
        original_focused_requests = tuple(
            item for item in request_items
            if str(getattr(item, "request_id", "") or "") in focused_request_ids
        )
        answer_state = ResearchIntentAnswerResolver().resolve_state(
            result,
            original_focused_requests,
            intent_id=(
                displayed_intent_id
                or _cash_conversion_intent_id(result)
            ),
            question=(
                operation_label
                or str(getattr(selected_action, "objective", "") or "")
            ),
            run_status=(
                "completed" if action_status.casefold() in {"completed", "resolved", "closed"}
                else "partial" if action_status.casefold() in {"partial", "unresolved"}
                else "failed" if action_status.casefold() in {"failed", "cancelled"}
                else "completed"
            ),
            expected_cutoff=str(
                getattr(displayed_context, "data_cutoff", "") or ""
            ),
        )
        admitted = sum(
            item.admitted_evidence_count for item in trail_requests
        ) + candidate_admitted
        evidence_found = answer_state.evidence_found
        evidence_missing = answer_state.evidence_missing
        answer_status = answer_state.answer_status
        answer = answer_state.direct_answer
        next_step = answer_state.remaining_action
        period_comparison, component_sections = _cash_conversion_answer_views(
            answer_state.bridge,
            expected_cutoff=str(
                getattr(displayed_context, "data_cutoff", "") or ""
            ),
        )
        investigation_trail = EvidenceInvestigationTrailViewModel(
            question=_normalize_display_text(
                operation_label
                or str(getattr(selected_action, "objective", "") or "Focused investigation")
            ),
            outcome=action_status.replace("_", " ").title(),
            steps=(
                EvidenceTrailStepViewModel(
                    "Question", "completed",
                    _normalize_display_text(operation_label or "Focused investigation"),
                ),
                EvidenceTrailStepViewModel(
                    "Research Requests",
                    "completed" if trail_requests else "not attempted",
                    (
                        f"{len(trail_requests)} targeted request(s) linked to this investigation."
                        if trail_requests else
                        "No request link was stored or could be inferred for this investigation."
                    ),
                ),
                EvidenceTrailStepViewModel(
                    "Connectors attempted",
                    "completed" if attempted_connectors else "not attempted",
                    ", ".join(attempted_connectors) if attempted_connectors else "No connector attempt recorded.",
                ),
                EvidenceTrailStepViewModel(
                    "Candidate discovery",
                    "completed" if trail_candidates else "not needed",
                    f"{len(trail_candidates)} relevant candidate(s); {len(hidden_candidates)} rejected or hidden.",
                ),
                EvidenceTrailStepViewModel(
                    "Evidence admission",
                    "completed" if admitted or evidence_found else "unresolved",
                    (
                        f"{admitted} request-linked admission(s); "
                        f"{len(evidence_found)} validated financial fact(s) reused; "
                        f"{rejected} candidate(s) rejected."
                    ),
                ),
                EvidenceTrailStepViewModel(
                    "Conclusion or action change",
                    "completed" if action_status.casefold() in {"resolved", "completed", "closed"} else "unresolved",
                    str(
                        getattr(selected_action, "resolution_summary", "")
                        or f"Current action status: {action_status.replace('_', ' ')}."
                    ),
                ),
            ),
            answer_status=answer_status,
            answer=answer,
            evidence_found=evidence_found,
            evidence_missing=evidence_missing,
            next_step=next_step,
            relevant_candidate_count=len(focused_candidates),
            hidden_candidate_count=len(hidden_candidates),
            confidence=answer_state.confidence,
            coverage=answer_state.coverage,
            answer_basis=answer_state.answer_basis,
            # Preserve the legacy Evidence label while the canonical answer
            # state retains the more explicit ``not_run`` value.
            qa_freshness=(
                "not_reviewed"
                if answer_state.qa_state == "not_run"
                else "pending"
                if answer_state.qa_state == "pending_due_to_new_evidence"
                else answer_state.qa_state
            ),
            period_comparison=period_comparison,
            component_sections=component_sections,
        )
    relationships = list(getattr(graph, "relationships", []) or [])
    return EvidencePageViewModel(
        admitted_count=len(getattr(graph, "admitted_evidence_ids", []) or []),
        excluded_count=len(getattr(graph, "excluded_evidence_ids", []) or []),
        contradiction_count=sum(
            str(getattr(item, "relationship_type", "")) == "contradicts"
            for item in relationships
        ),
        evidence_hash=str(getattr(graph, "evidence_hash", "") or ""),
        requests=requests,
        candidates=tuple(item for item in candidates if item.relevant),
        request_groups=request_groups,
        investigation_trail=investigation_trail,
        relevant_candidate_count=sum(item.relevant for item in candidates),
        hidden_candidate_count=sum(not item.relevant for item in candidates),
    )


def _cash_conversion_intent_id(result: Any) -> str:
    """Read the bridge from either current root or hybrid result storage."""

    bridge = getattr(result, "cash_conversion_recurrence", None)
    if bridge is None:
        bridge = getattr(
            getattr(result, "hybrid_research", None),
            "cash_conversion_recurrence",
            None,
        )
    return str(getattr(bridge, "intent_id", "") or "")


def build_investigation_journey_view_model(
    journey: InvestigationJourney,
) -> InvestigationJourneyViewModel:
    outcome = journey.outcome_summary
    stage_rows = tuple(
        InvestigationJourneyStageViewModel(stage.label, status)
        for stage, status in journey.stages
    )
    completed_count = sum(
        stage.status == "completed" for stage in stage_rows
    )
    active_count = sum(stage.status == "active" for stage in stage_rows)
    total_stages = max(1, len(stage_rows))
    progress_percent = (
        100
        if outcome is not None
        else min(
            99,
            round(
                100
                * (completed_count + (0.5 if active_count else 0))
                / total_stages
            ),
        )
    )
    progress_label = (
        "Research result ready"
        if outcome is not None
        else (
            f"Stage {min(completed_count + 1, total_stages)} of "
            f"{total_stages} · approximately {progress_percent}%"
        )
    )
    return InvestigationJourneyViewModel(
        operation_label=_normalize_display_text(journey.operation_label),
        objective=_normalize_display_text(journey.objective),
        ticker=journey.ticker,
        current_stage=journey.current_stage.label,
        stages=stage_rows,
        active=outcome is None,
        elapsed_label=_duration_label(journey.elapsed_seconds),
        qa_label=(
            "Independent QA is active"
            if journey.independent_qa_running
            else _qa_state_label(getattr(outcome, "qa_state", "not_run"))
        ),
        progress_percent=progress_percent,
        completed_stage_count=completed_count,
        progress_label=progress_label,
        outcome=(outcome.outcome.replace("_", " ").title() if outcome else ""),
        persistence_status=(
            outcome.persistence_status.replace("_", " ").title()
            if outcome else ""
        ),
        admitted_evidence_count=(outcome.admitted_evidence_count if outcome else 0),
        change_summaries=(outcome.changes.change_summaries if outcome else ()),
        remaining_actions=(outcome.remaining_actions if outcome else ()),
        answer_status=(outcome.answer_status if outcome else ""),
        direct_answer=(outcome.direct_answer if outcome else ""),
        confidence=(outcome.confidence if outcome else "Unknown"),
        key_evidence=(outcome.key_evidence if outcome else ()),
        strongest_limitation=(
            outcome.strongest_limitation if outcome else ""
        ),
        run_status=("active" if outcome is None else outcome.run_status),
        qa_state=("in_progress" if journey.independent_qa_running else getattr(outcome, "qa_state", "not_run")),
    )


def _qa_state_label(value: str) -> str:
    labels = {
        "not_requested": "Not requested",
        "not_run": "Not run",
        "pending_due_to_new_evidence": "Pending for refreshed evidence",
        "in_progress": "In progress",
        "closed": "Closed",
        "closed_with_residual_risk": "Closed with residual diligence",
        "failed": "Failed",
        "indeterminate": "Indeterminate",
    }
    return labels.get(str(value or "").casefold(), "Not run")


def _infer_focused_research_requests(
    requests: tuple[ResearchRequestCardViewModel, ...],
    *,
    operation_label: str,
    action_kind: str,
    action_objective: str,
) -> tuple[ResearchRequestCardViewModel, ...]:
    """Repair missing historical links without mutating the saved artifact."""

    focus_text = " ".join(
        value for value in (operation_label, action_objective) if value
    )
    inferred_topic = research_request_topic(focus_text)
    target_topic = (
        inferred_topic
        if inferred_topic != "primary_source_validation"
        else action_kind or inferred_topic
    )
    topic_matches = [
        item for item in requests
        if research_request_topic(item.objective) == target_topic
    ]
    if topic_matches:
        answer_rank = {
            "answered": 0,
            "partially_answered": 1,
            "unanswered_after_attempt": 2,
            "blocked": 3,
            "planned": 4,
        }
        topic_matches.sort(key=lambda item: (
            answer_rank.get(item.answer_status, 5),
            item.priority.casefold() != "critical",
            item.request_id,
        ))
        return tuple(topic_matches[:3])

    focus_tokens = _semantic_text_tokens(focus_text)
    scored = []
    for item in requests:
        request_tokens = _semantic_text_tokens(item.objective)
        shared = focus_tokens.intersection(request_tokens)
        denominator = max(1, min(len(focus_tokens), len(request_tokens)))
        score = len(shared) / denominator
        if len(shared) >= 2 and score >= 0.2:
            scored.append((score, item))
    scored.sort(key=lambda row: (-row[0], row[1].request_id))
    return tuple(item for _score, item in scored[:1])


def _semantic_text_tokens(value: str) -> set[str]:
    stopwords = {
        "a", "an", "and", "around", "build", "determine", "for", "from",
        "in", "is", "of", "or", "the", "this", "to", "whether", "with",
    }
    return {
        token for token in re.findall(r"[a-z0-9]+", str(value).casefold())
        if len(token) > 2 and token not in stopwords
    }


def _group_research_requests(
    requests: tuple[ResearchRequestCardViewModel, ...],
) -> tuple[tuple[str, tuple[ResearchRequestCardViewModel, ...]], ...]:
    buckets: dict[str, list[ResearchRequestCardViewModel]] = {
        "Answered": [],
        "Partially answered": [],
        "Attempted but unanswered": [],
        "Planned": [],
        "Blocked": [],
    }
    for request in requests:
        answer_status = request.answer_status.casefold().replace(" ", "_")
        status = request.status.casefold().replace(" ", "_")
        if answer_status == "answered":
            bucket = "Answered"
        elif answer_status == "partially_answered":
            bucket = "Partially answered"
        elif answer_status == "unanswered_after_attempt":
            bucket = "Attempted but unanswered"
        elif answer_status == "blocked":
            bucket = "Blocked"
        elif answer_status == "planned":
            bucket = "Planned"
        elif status in {"resolved", "completed", "closed", "admitted"}:
            bucket = "Answered"
        elif status in {"blocked", "failed", "unavailable", "retrieval_failed"}:
            bucket = "Blocked"
        elif request.attempted_connectors or status in {
            "attempted", "attempted_unresolved", "in_progress", "retrieval_requested",
        }:
            bucket = "Attempted but unanswered"
        else:
            bucket = "Planned"
        buckets[bucket].append(request)
    return tuple(
        (label, tuple(items))
        for label, items in buckets.items()
        if items
    )


def _is_scope_mismatch(scope_status: str, reason: str) -> bool:
    normalized = f"{scope_status} {reason}".casefold()
    return any(token in normalized for token in (
        "scope_mismatch",
        "scope mismatch",
        "outside the security",
        "off-target",
        "off target",
        "unrelated to the issuer",
        "wrong company",
        "targets another security",
    ))


def _normalize_display_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", str(value or ""))
    replacements = {
        "â€™": "’",
        "â€˜": "‘",
        "â€œ": "“",
        "â€�": "”",
        "â€“": "–",
        "â€”": "—",
        "â€¦": "…",
        "Â·": "·",
        "鈥?": "—",
        "鈥�": "—",
    }
    for broken, repaired in replacements.items():
        normalized = normalized.replace(broken, repaired)
    return " ".join(normalized.split())


def _duration_label(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    minutes, remainder = divmod(total, 60)
    if minutes:
        return f"{minutes}m {remainder:02d}s"
    return f"{remainder}s"


def build_durable_run_timeline_view_model(
    snapshot: Any,
    events: Iterable[Any],
) -> IntegrityTimelineViewModel:
    return IntegrityTimelineViewModel(
        status=str(getattr(snapshot, "status", "Unknown")).replace("_", " ").title(),
        events=tuple(
            IntegrityTimelineEventViewModel(
                label=str(getattr(event, "stage", "Research stage"))
                .replace("_", " ").title(),
                status=str(getattr(event, "status", "Unknown"))
                .replace("_", " ").title(),
                summary=str(getattr(event, "summary", "") or "Run event recorded."),
                timestamp=str(getattr(event, "created_at", "") or ""),
                origin=" / ".join(
                    value for value in (
                        str(getattr(event, "provider", "") or ""),
                        str(getattr(event, "model", "") or ""),
                    ) if value
                ) or "Application service",
                technical_details={
                    "event_id": str(getattr(event, "event_id", "") or ""),
                    "sequence": int(getattr(event, "sequence", 0) or 0),
                    "evidence_hash": str(getattr(event, "evidence_hash", "") or ""),
                    "details": dict(getattr(event, "details", {}) or {}),
                },
            )
            for event in events
        ),
        stopping_reason=str(
            getattr(snapshot, "stopping_reason", "") or "in_progress"
        ),
        recursion_depth=0,
        estimated_cost_usd=sum(
            float(getattr(event, "estimated_cost_usd", 0) or 0)
            for event in events
        ) or None,
    )


def build_decision_brief_view_model(
    result: Any,
    *,
    minimum_expected_return_pct: float = 10.0,
    minimum_evidence_score: int = 60,
) -> DecisionBriefViewModel:
    identity = result.identity
    brief = getattr(result, "ic_decision_brief", None)
    valuation = getattr(result, "valuation", None)
    top_idea = result.ideas[0] if getattr(result, "ideas", None) else None
    event = _priority_source_event(top_idea)
    qa = getattr(result, "independent_research_qa", None)
    qa_report = getattr(qa, "qa_report", None)
    freshness = getattr(result, "research_freshness", None)
    data_quality = getattr(result, "data_quality", None)
    hybrid = getattr(result, "hybrid_research", None)
    freeze_decision = getattr(hybrid, "demo_freeze_decision", None) if hybrid else None
    publication_evaluation = (
        getattr(freeze_decision, "publication_gate_evaluation", None)
        or (
            list(getattr(hybrid, "publication_gate_evaluations", []) or [])[-1]
            if hybrid and getattr(hybrid, "publication_gate_evaluations", None)
            else None
        )
    )
    publication_label = str(
        getattr(freeze_decision, "publication_label", "")
        or _publication_label(publication_evaluation)
    )
    publication_qualifications = tuple(
        _without_raw_ids(str(getattr(item, "summary", "") or ""))
        for item in getattr(publication_evaluation, "qualifications", ()) or ()
        if str(getattr(item, "summary", "") or "").strip()
    )

    cases = tuple(
        ValuationCaseViewModel(
            name=str(case.name),
            probability=_percent_probability(case.probability),
            fair_value=_money(case.fair_value, getattr(valuation, "currency", "USD")),
            method=str(case.method or "Method not established"),
        )
        for case in list(getattr(valuation, "cases", []) or [])[:3]
    )
    fair_values = [
        case.fair_value for case in list(getattr(valuation, "cases", []) or [])
        if isinstance(case.fair_value, (int, float))
    ]
    valuation_range = (
        f"{_money(min(fair_values), valuation.currency)} – {_money(max(fair_values), valuation.currency)}"
        if fair_values else "Not established"
    )
    principal_risk = str(
        getattr(brief, "principal_risk", "")
        or getattr(top_idea, "strongest_counter_thesis", "")
        or ""
    ).strip()
    counter_gap = _is_counter_thesis_gap(principal_risk)
    if counter_gap:
        principal_risk = "Counter-thesis not established — diligence gap."

    why_now = _without_raw_ids(str(getattr(brief, "why_now", "") or "No validated urgency."))
    references = _evidence_references(event)
    technical_ids = tuple(
        str(value) for value in getattr(brief, "why_now_evidence_ids", []) or [] if value
    )
    investigation_actions = _investigation_action_view_models(result)
    actions = tuple(item.title for item in investigation_actions)
    if not actions:
        actions = _next_actions(result, brief)
    priced_in = _priced_in_summary(result)
    qa_status = (
        str(getattr(qa_report, "status", "") or getattr(qa, "status", ""))
        if qa else "Independent QA not run"
    )
    overall_freshness = str(getattr(freshness, "overall_status", "") or getattr(brief, "freshness", "Unknown"))
    evidence_quality = (
        f"{getattr(data_quality, 'status', 'Unknown')} ({getattr(data_quality, 'score', 0)}/100)"
        if data_quality else str(getattr(brief, "evidence_status", "Unknown"))
    )
    # Re-evaluate the public action using the explicit advanced policy.  The
    # candidate hypothesis stored in an older artifact remains immutable, but
    # it must never bypass a stricter actionability threshold selected here.
    recommendation = build_decision_recommendation(
        brief,
        top_idea,
        valuation,
        data_quality,
        getattr(result, "thesis_validation", None),
        hybrid,
        minimum_expected_return_pct=minimum_expected_return_pct,
        minimum_evidence_score=minimum_evidence_score,
    )
    verdict = _without_raw_ids(
        str(getattr(brief, "verdict", "") or "Research result available")
    )
    raw_stage = str(
        getattr(brief, "stage", "")
        or getattr(top_idea, "stage", "Unclassified")
    )
    public_stage = (
        "Thesis not established"
        if "no convincing thesis" in verdict.casefold()
        else raw_stage
    )
    return DecisionBriefViewModel(
        ticker=str(identity.ticker),
        company_name=str(identity.name),
        run_status="Frozen demo" if getattr(result, "demo_case", None) else "Completed",
        freshness=overall_freshness,
        evidence_quality=evidence_quality,
        verdict=verdict,
        stage=public_stage,
        direction=str(getattr(recommendation, "recommendation", "No position / Watch")),
        candidate_direction=str(getattr(recommendation, "candidate_direction", "Watch")),
        recommendation_reason=_without_raw_ids(
            str(getattr(recommendation, "rationale", "") or "")
        ),
        confidence=str(getattr(brief, "confidence", "") or "Low"),
        thesis=_limit_paragraphs(_without_raw_ids(str(getattr(brief, "thesis", "") or "No thesis established."))),
        why_now=why_now,
        why_now_evidence=references,
        priced_in=priced_in,
        valuation_range=valuation_range,
        probability_weighted_value=_money(
            getattr(valuation, "probability_weighted_value", None),
            getattr(valuation, "currency", "USD"),
        ),
        expected_return=_percent(getattr(valuation, "expected_return_pct", None)),
        valuation_confidence=str(getattr(valuation, "confidence", "") or "Low"),
        valuation_cases=cases,
        counter_thesis=_without_raw_ids(principal_risk),
        counter_thesis_status="Diligence gap" if counter_gap else "Established",
        next_actions=actions,
        independent_qa_status=qa_status or "Independent QA not run",
        market_expectations=_market_expectations_view_model(result),
        historical_analogs=_historical_analog_cards(result),
        story_lenses=_story_lens_cards(result),
        debate=_debate_view_model(result),
        technical_evidence_ids=technical_ids,
        investigation_actions=investigation_actions,
        publication_label=publication_label,
        publication_qualifications=publication_qualifications,
    )


def build_research_route_view_model(
    result: Any,
    route: ResearchSubroute | str,
) -> ResearchRouteViewModel:
    selected = ResearchSubroute(route)
    actions = _investigation_action_view_models(result)
    action_refs = tuple(f"Action {item.rank}" for item in actions[:3])
    ideas = list(getattr(result, "ideas", []) or [])
    top = ideas[0] if ideas else None
    if selected == ResearchSubroute.THESIS_DRIVERS:
        graphs = list(getattr(result, "causal_thesis_graphs", []) or [])
        matters = _clean_summary_items([
            str(getattr(top, "title", "") or getattr(top, "thesis", "") or ""),
            str(getattr(getattr(result, "thesis_brief", None), "summary", "") or ""),
            *list(getattr(getattr(result, "thesis_validation", None), "strongest_confirmations", []) or []),
        ])
        return ThesisDriversViewModel(
            route=selected,
            title=selected.value,
            metrics=(("Ideas", str(len(ideas))), ("Causal graphs", str(len(graphs))), ("Top stage", str(getattr(top, "stage", "Unknown") or "Unknown"))),
            what_matters=matters,
            strongest_limitation=_research_limitation(result, "thesis"),
            action_references=action_refs,
        )
    if selected == ResearchSubroute.MODEL_VALUATION:
        valuation = getattr(result, "valuation", None)
        cases = list(getattr(valuation, "cases", []) or [])
        model = getattr(result, "company_model", None)
        return ModelValuationViewModel(
            route=selected,
            title=selected.value,
            metrics=(("Cases", str(len(cases))), ("Model status", str(getattr(model, "status", "Unknown") or "Unknown")), ("Valuation confidence", str(getattr(valuation, "confidence", "Unknown") or "Unknown"))),
            what_matters=_clean_summary_items([
                str(getattr(model, "summary", "") or ""),
                _valuation_input_summary(valuation),
                str(getattr(getattr(result, "market_implied_expectations", None), "summary", "") or ""),
                str(getattr(getattr(result, "earnings_surprise_proxy", None), "headline", "") or ""),
            ]),
            strongest_limitation=_research_limitation(result, "valuation"),
            action_references=action_refs,
            input_resolutions=tuple(
                ValuationInputResolutionViewModel(
                    input_name=str(getattr(item, "input_name", "Input") or "Input"),
                    status=str(getattr(item, "status", "unknown") or "unknown").replace("_", " ").title(),
                    value=_formatted_resolution_value(item),
                    source=_without_raw_ids(str(getattr(item, "source", "") or "Not recorded")),
                    formula=_without_raw_ids(str(getattr(item, "formula", "") or "")),
                    reason=_without_raw_ids(str(getattr(item, "reason", "") or "")),
                )
                for item in list(getattr(valuation, "input_resolutions", []) or [])
            ),
        )
    if selected == ResearchSubroute.EXPECTATIONS_REACTION:
        reactions = list(getattr(result, "event_window_reactions", {}).values())
        available = sum(str(getattr(item, "status", "")) == "available" for item in reactions)
        context = getattr(result, "recent_market_context", None)
        capture = getattr(result, "market_capture_readiness", None)
        attribution = getattr(top, "driver_attribution", None) if top else None
        return ExpectationsReactionViewModel(
            route=selected,
            title=selected.value,
            metrics=(("Event windows", str(len(reactions))), ("Complete", str(available)), ("Capture status", str(getattr(capture, "status", "Unknown") or "Unknown")), ("Price as of", str(getattr(context, "price_as_of", "Unavailable") or "Unavailable"))),
            what_matters=_clean_summary_items([
                str(getattr(context, "summary", "") or ""),
                str(getattr(capture, "summary", "") or ""),
                *[
                    str(value)
                    for value in list(
                        getattr(attribution, "attribution_summary", []) or []
                    )
                ],
            ]),
            strongest_limitation=_research_limitation(result, "market"),
            action_references=action_refs,
        )
    analogs = getattr(result, "historical_analogs", None)
    dossiers = list(getattr(analogs, "dossiers", []) or [])
    peer_groups = dict(getattr(result, "peer_metric_readthrough", {}) or {})
    peers = [item for group in peer_groups.values() for item in list(group or [])]
    return AnalogsPeersViewModel(
        route=selected,
        title=selected.value,
        metrics=(("Analog dossiers", str(len(dossiers))), ("Audited", str(sum(str(getattr(item, "status", "")) == "Audited Research Analog" for item in dossiers))), ("Operating peers", str(len(peers)))),
        what_matters=_clean_summary_items([
            *[
                str((list(getattr(item, "lessons", []) or []) or [""])[0])
                for item in dossiers[:3]
            ],
            *[str(getattr(item, "summary", "") or "") for item in peers[:2]],
        ]),
        strongest_limitation=_research_limitation(result, "analogs"),
        action_references=action_refs,
    )


def build_research_session_view_model(
    result: Any,
    route: ResearchSubroute | str,
) -> ResearchSessionViewModel | None:
    hybrid = getattr(result, "hybrid_research", None)
    sessions = list(
        getattr(hybrid, "agent_research_sessions", []) or []
    )
    if not sessions and getattr(hybrid, "agent_research_session", None):
        sessions = [hybrid.agent_research_session]
    session = next((
        item for item in sessions
        if str(getattr(item, "role", "")) == "primary_research_agent"
    ), sessions[0] if sessions else None)
    if session is None:
        return None
    selected = ResearchSubroute(route)
    domain_tokens = {
        ResearchSubroute.THESIS_DRIVERS: {
            "business", "operating", "catalyst", "counter", "management",
            "monitoring", "macro", "external",
        },
        ResearchSubroute.MODEL_VALUATION: {
            "financial", "cash", "balance", "credit", "valuation", "fcf", "dcf",
            "capital", "contracts",
        },
        ResearchSubroute.EXPECTATIONS_REACTION: {
            "market", "expectation", "consensus", "catalyst", "reaction", "priced",
            "regulatory", "legal",
        },
        ResearchSubroute.ANALOGS_PEERS: {
            "analog", "playbook", "peer", "read-through", "history",
        },
    }[selected]
    agenda_items = list(getattr(getattr(session, "agenda", None), "items", []) or [])
    matched = [
        item for item in agenda_items
        if any(
            token in f"{getattr(item, 'domain', '')} {getattr(item, 'title', '')}".casefold()
            for token in domain_tokens
        )
    ]
    if not matched:
        matched = agenda_items
    selected_question_id = str(
        getattr(getattr(session, "agenda", None), "selected_question_id", "")
        or ""
    )
    matched.sort(key=lambda item: (
        str(getattr(item, "question_id", "")) != selected_question_id,
        str(getattr(item, "status", "")) == "planned",
        -int(getattr(item, "priority", 0) or 0),
    ))
    questions = []
    for item in matched:
        answer = getattr(item, "answer", None)
        questions.append(ResearchQuestionAnswerViewModel(
            question_id=str(getattr(item, "question_id", "") or ""),
            domain=_normalize_display_text(str(getattr(item, "domain", "") or "Research")),
            title=_normalize_display_text(str(getattr(item, "title", "") or getattr(item, "objective", "") or "Research question")),
            status=str(getattr(item, "status", "planned") or "planned"),
            answer=_normalize_display_text(str(getattr(answer, "answer", "") or "")),
            confidence=str(getattr(answer, "confidence", "Unknown") or "Unknown"),
            evidence_count=len({
                *list(getattr(answer, "evidence_ids", []) or []),
                *list(getattr(answer, "contrary_evidence_ids", []) or []),
            }) if answer is not None else 0,
            strongest_limitation=_normalize_display_text(str(
                getattr(answer, "strongest_limitation", "") or ""
            )),
            remaining_work=tuple(
                _normalize_display_text(str(value))
                for value in list(getattr(answer, "remaining_work", []) or [])
                if value
            ),
        ))
    return ResearchSessionViewModel(
        session_status=str(getattr(session, "status", "unknown") or "unknown"),
        role=str(getattr(session, "role", "primary_research_agent") or "primary_research_agent"),
        provider_label=" · ".join(
            value for value in (
                str(getattr(session, "provider", "") or ""),
                str(getattr(session, "model", "") or ""),
            ) if value
        ),
        budget_label=str(getattr(getattr(session, "budget", None), "label", "Bounded research") or "Bounded research"),
        answered_count=sum(item.status == "answered" for item in questions),
        partial_count=sum(item.status == "partially_answered" for item in questions),
        total_count=len(questions),
        stopping_reason=str(getattr(session, "stopping_reason", "") or ""),
        questions=tuple(questions),
    )


def build_integrity_timeline_view_model(
    result: Any,
    *,
    displayed_context: Any | None = None,
) -> IntegrityTimelineViewModel:
    qa = getattr(result, "independent_research_qa", None)
    graph = getattr(result, "evidence_graph", None)
    hybrid = getattr(result, "hybrid_research", None)
    events: list[IntegrityTimelineEventViewModel] = []
    if displayed_context is not None and str(
        getattr(displayed_context, "operation_label", "") or ""
    ):
        events.append(IntegrityTimelineEventViewModel(
            label="Displayed investigation",
            status="Derived revision" if bool(
                getattr(displayed_context, "is_derived_revision", False)
            ) else "Saved result",
            summary=str(getattr(displayed_context, "operation_label", "") or ""),
            origin="Saved research context",
        ))
    events.append(IntegrityTimelineEventViewModel(
        label="Deterministic preflight",
        status="Completed" if graph else "Unavailable",
        summary=(
            f"{len(getattr(graph, 'admitted_evidence_ids', []) or [])} evidence items admitted under the active source policy."
            if graph else "No evidence graph was packaged for this result."
        ),
        timestamp=str(getattr(graph, "generated_at", "") or ""),
        origin="Deterministic",
        technical_details={
            "evidence_hash": str(getattr(graph, "evidence_hash", "") or ""),
            "source_policy_fingerprint": str(getattr(graph, "source_policy_fingerprint", "") or ""),
        },
    ))
    role_labels = {
        "research_planner": "Research planning",
        "primary_investment_analyst": "Primary research",
        "independent_research_auditor": "Independent frozen-artifact audit",
        "adversarial_evidence_scout": "Adversarial evidence scouting",
        "evidence_gap_resolver": "Registered investigation resolution",
        "closure_checker": "Final closure check",
        "investment_committee_writer": "IC packaging",
    }
    for record in list(getattr(qa, "model_executions", []) or []):
        role = str(getattr(record, "role", "") or "model_stage")
        provider = str(getattr(record, "provider", "") or "")
        model = str(getattr(record, "model", "") or "")
        status = str(getattr(record, "status", "Unknown") or "Unknown")
        technical_details = {
            "prompt_hash": str(getattr(record, "prompt_hash", "") or ""),
            "evidence_hash": str(getattr(record, "evidence_hash", "") or ""),
            "failure_class": str(getattr(record, "failure_class", "") or ""),
            "token_usage": dict(getattr(record, "token_usage", {}) or {}),
            "provider_cost_usd": getattr(record, "provider_cost_usd", None),
        }
        events.append(IntegrityTimelineEventViewModel(
            label=role_labels.get(role, role.replace("_", " ").title()),
            status=status.replace("_", " ").title(),
            summary=_model_execution_summary(record),
            timestamp=str(
                getattr(record, "completed_at", "")
                or getattr(record, "started_at", "")
                or ""
            ),
            origin=" / ".join(value for value in (provider, model) if value),
            technical_details=technical_details,
        ))
    requests = list(getattr(qa, "research_requests", []) or getattr(qa, "evidence_requests", []) or [])
    if requests:
        events.append(IntegrityTimelineEventViewModel(
            label="Investigation requests",
            status="Generated",
            summary=f"{len(requests)} targeted request(s) were generated for unresolved findings.",
            origin="Independent Research QA",
        ))
    candidates = list(getattr(qa, "candidate_discovery_items", []) or [])
    if candidates:
        admitted = sum(1 for item in candidates if str(item.status) == "admitted")
        events.append(IntegrityTimelineEventViewModel(
            label="Research Sandbox",
            status="Completed",
            summary=f"{len(candidates)} candidate(s) discovered; {admitted} admitted after registered retrieval.",
            origin="Candidate discovery and admission gateway",
        ))
    closure = getattr(qa, "closure_result", None)
    if closure:
        events.append(IntegrityTimelineEventViewModel(
            label="Final closure check",
            status=str(getattr(closure, "state", "Unknown") or "Unknown").replace("_", " ").title(),
            summary=str(getattr(closure, "summary", "") or "Closure state recorded."),
            timestamp=str(getattr(closure, "completed_at", "") or ""),
            origin=" / ".join(
                value for value in (
                    str(getattr(closure, "provider", "") or ""),
                    str(getattr(closure, "model", "") or ""),
                ) if value
            ),
        ))
    elif qa:
        events.append(IntegrityTimelineEventViewModel(
            label="Final closure check",
            status="Not completed",
            summary="The result does not contain a final closure decision.",
            origin="Independent Research QA",
        ))
    if qa is None:
        events.append(IntegrityTimelineEventViewModel(
            label="Independent Research QA",
            status="Not run",
            summary=(
                "No independent auditor artifact is attached to this result. "
                "Deterministic and primary-research artifacts remain available."
            ),
            origin="Application readiness",
        ))
    stopping_reason = str(
        getattr(qa, "stopping_reason", "")
        or getattr(hybrid, "stopping_reason", "")
        or ("completed" if qa and getattr(qa, "status", "") == "completed" else "Independent QA not run")
    )
    recursion_depth = int(getattr(qa, "recursion_depth", 0) or 0)
    cost = getattr(qa, "estimated_cost_usd", None)
    return IntegrityTimelineViewModel(
        status=str(getattr(qa, "status", "") or "Independent QA not run"),
        events=tuple(events),
        stopping_reason=stopping_reason,
        recursion_depth=recursion_depth,
        estimated_cost_usd=cost,
    )


def build_publication_readiness_view_model(
    result: Any,
) -> PublicationReadinessViewModel:
    hybrid = getattr(result, "hybrid_research", None)
    qa = getattr(result, "independent_research_qa", None)
    decision = getattr(hybrid, "demo_freeze_decision", None) if hybrid else None
    evaluation = (
        getattr(decision, "publication_gate_evaluation", None)
        or (
            list(getattr(hybrid, "publication_gate_evaluations", []) or [])[-1]
            if hybrid and getattr(hybrid, "publication_gate_evaluations", None)
            else None
        )
    )
    actions = list(
        getattr(evaluation, "repair_actions", ())
        or getattr(decision, "repair_actions", ())
        or getattr(hybrid, "repair_actions", ())
        or ()
    )
    actions = _repair_actions_in_dependency_order(actions)
    closure_round = next((
        item for item in reversed(list(getattr(hybrid, "rounds", []) or []))
        if str(getattr(item, "role", "") or "") == "closure_checker"
    ), None)
    closure_message = str(getattr(closure_round, "message", "") or "")
    closure_round_status = str(
        getattr(closure_round, "status", "") or ""
    ).casefold()
    closure_timed_out = bool(
        closure_round is not None
        and (
            closure_round_status in {"timeout", "timed_out", "deadline_exceeded"}
            or "timed out" in closure_message.casefold()
            or "exceeded" in closure_message.casefold()
        )
    )
    failures_by_id = {
        str(getattr(item, "failure_id", "")): item
        for item in getattr(evaluation, "failures", ()) or ()
    }
    cards: list[RepairActionCardViewModel] = []
    for action in actions:
        failure = next(
            (
                failures_by_id.get(str(failure_id))
                for failure_id in getattr(action, "failure_ids", ()) or ()
                if failures_by_id.get(str(failure_id)) is not None
            ),
            None,
        )
        repair_kind = str(getattr(action, "repair_kind", "") or "")
        is_closure_retry = (
            closure_timed_out and repair_kind == "rerun_closure"
        )
        cards.append(RepairActionCardViewModel(
            action_id=str(getattr(action, "action_id", "") or ""),
            title=_without_raw_ids(str(
                getattr(failure, "summary", "")
                or getattr(action, "objective", "Repair publication blocker")
            )),
            status=(
                "Ready to retry"
                if is_closure_retry else
                str(getattr(action, "status", "planned") or "planned").replace("_", " ").title()
            ),
            why_it_matters=_without_raw_ids(str(
                getattr(failure, "blocking_rule", "")
                or "The result cannot be presented as publication-quality until this is resolved."
            )),
            what_the_app_will_do=_without_raw_ids(str(
                getattr(action, "objective", "Run the registered repair workflow and recheck the affected gates.")
            )),
            source_families=tuple(getattr(action, "required_source_families", ()) or ()),
            attempts=int(getattr(action, "attempts", 0) or 0),
            result_or_blocker=(
                "Independent QA closure timed out before returning a decision."
                if is_closure_retry else
                _without_raw_ids(str(
                    getattr(action, "resolution_summary", "")
                    or getattr(action, "stopping_reason", "")
                    or ""
                ))
            ),
        ))
    validation = str(
        getattr(hybrid, "deterministic_validation_status", "")
        or ("passed" if getattr(qa, "deterministic_validation_passed", False) else "not run")
    )
    closure = getattr(qa, "closure_result", None)
    qa_status = str(
        getattr(closure, "state", "")
        or getattr(qa, "status", "")
        or "Independent QA not run"
    )
    if closure_timed_out:
        qa_status = "Closure timed out · ready to retry"
    quality_history = list(getattr(hybrid, "semantic_quality_history", []) or [])
    quality = quality_history[-1] if quality_history else None
    quality_label = (
        f"{getattr(quality, 'total_score', 0)}/{getattr(quality, 'threshold', 100)}"
        if quality is not None else "Not recomputed"
    )
    eligible = bool(
        getattr(evaluation, "publishable", False)
        or str(getattr(hybrid, "lifecycle_state", "")) in {"publishable", "published"}
    )
    publication_label = _publication_label(evaluation)
    qualifications = tuple(
        _without_raw_ids(str(getattr(item, "summary", "") or ""))
        for item in getattr(evaluation, "qualifications", ()) or ()
        if str(getattr(item, "summary", "") or "").strip()
    )
    research_status = str(
        getattr(hybrid, "lifecycle_state", "")
        or getattr(hybrid, "status", "")
        or "Historical result"
    ).replace("_", " ").title()
    if eligible and qualifications:
        summary = (
            "This result passed the active integrity gates with explicitly recorded residual diligence."
        )
    elif eligible:
        summary = "This result passed the active validation, Independent QA, and publication-quality gates."
    elif closure_timed_out:
        summary = (
            "Research revision and deterministic validation are complete. "
            "Independent QA closure timed out before returning a decision; "
            "retrying closure will not rerun primary research."
        )
    elif cards:
        summary = (
            "Research is available, but this result is not ready for publication. "
            f"{len(cards)} material repair{'s' if len(cards) != 1 else ''} remain."
        )
    else:
        summary = (
            "Publication readiness was not evaluated for this historical result. "
            "Its research remains inspectable without implying that current gates passed."
        )
    completed = sum(
        str(getattr(item, "status", "")).casefold() in {"completed", "resolved"}
        for item in actions
    )
    repair_progress = (
        (
            RepairProgressStageViewModel(
                "Research revision", "Reused"
            ),
            RepairProgressStageViewModel(
                "Deterministic validation", "Reused"
            ),
            RepairProgressStageViewModel(
                "Independent QA closure", "Ready to retry"
            ),
            RepairProgressStageViewModel(
                "Publication evaluation", "Pending"
            ),
        )
        if closure_timed_out else ()
    )
    return PublicationReadinessViewModel(
        research_status=research_status,
        validation_status=validation.replace("_", " ").title(),
        independent_qa_status=qa_status.replace("_", " ").title(),
        semantic_quality=quality_label,
        publication_eligibility=publication_label,
        summary=summary,
        repairs_remaining=max(0, len(actions) - completed),
        progress_label=f"{completed} of {len(actions)} repairs completed" if actions else "No repair plan recorded",
        repair_cards=tuple(cards),
        can_repair=bool(cards and hybrid is not None),
        publication_eligible=eligible,
        qualification_summaries=qualifications,
        repair_progress=repair_progress,
        primary_action_label=(
            "Retry Independent QA closure"
            if closure_timed_out else "Repair and recheck"
        ),
        execution_note=(
            "Hosted GPT-5.6 Sol may take up to about 15 minutes. Primary "
            "research, retrieval, and deterministic validation will be reused."
            if closure_timed_out else ""
        ),
    )


def _repair_actions_in_dependency_order(actions: list[Any]) -> list[Any]:
    """Return a stable, readable repair sequence from the repair DAG."""

    pending = list(actions)
    ordered: list[Any] = []
    emitted: set[str] = set()
    all_ids = {
        str(getattr(item, "action_id", "") or "")
        for item in pending
        if str(getattr(item, "action_id", "") or "")
    }
    while pending:
        ready = [
            item for item in pending
            if (
                set(getattr(item, "dependency_action_ids", ()) or ())
                & all_ids
            ) <= emitted
        ]
        if not ready:
            ordered.extend(pending)
            break
        for item in ready:
            pending.remove(item)
            ordered.append(item)
            action_id = str(getattr(item, "action_id", "") or "")
            if action_id:
                emitted.add(action_id)
    return ordered


def _publication_label(evaluation: Any) -> str:
    decision = str(getattr(evaluation, "decision", "") or "")
    if decision == "publishable":
        return "Publication-quality research"
    if decision == "publishable_with_residual_diligence":
        return "Research completed with residual diligence"
    return "Research draft · validation or QA incomplete"


def _model_execution_summary(record: Any) -> str:
    message = _without_raw_ids(str(getattr(record, "message", "") or "").strip())
    if message:
        return message
    failure_class = str(getattr(record, "failure_class", "") or "").strip()
    status = str(getattr(record, "status", "Unknown") or "Unknown").replace("_", " ")
    if failure_class == "provider_credit_required":
        return (
            "Model stage paused because the selected provider needs additional "
            "credit. Completed stages remain available for an explicit resume."
        )
    if failure_class == "provider_authentication_required":
        return (
            "Model stage paused because the selected provider needs "
            "authentication. Completed stages remain available for an explicit resume."
        )
    if failure_class:
        return f"Model stage {status}; failure class: {failure_class}."
    return f"Model stage {status}; no additional execution message was recorded."


def _market_expectations_view_model(result: Any) -> MarketExpectationsViewModel | None:
    implied = getattr(result, "market_implied_expectations", None)
    snapshot = getattr(result, "ic_implied_snapshot", None)
    if snapshot is None and implied is not None:
        company_model = getattr(result, "company_model", None)
        valuation = getattr(result, "valuation", None)
        if company_model is not None and valuation is not None:
            try:
                from ..decision_intelligence import (
                    build_ic_implied_expectations_snapshot,
                )

                snapshot = build_ic_implied_expectations_snapshot(
                    implied,
                    company_model,
                    valuation,
                )
            except (AttributeError, TypeError, ValueError):
                snapshot = None
    if implied is None and snapshot is None:
        return None

    currency = str(
        getattr(snapshot, "currency", "")
        or getattr(implied, "currency", "USD")
        or "USD"
    )
    current_price = getattr(snapshot, "current_price", None)
    if current_price is None:
        current_price = getattr(implied, "current_price", None)
    assumptions = tuple(
        MarketImpliedAssumptionViewModel(
            key=str(
                getattr(item, "key", "")
                or str(getattr(item, "name", "assumption")).casefold().replace(" ", "_")
            ),
            name=str(getattr(item, "name", "Assumption") or "Assumption"),
            value=getattr(item, "value", None),
            unit=str(getattr(item, "unit", "") or ""),
            provenance=str(getattr(item, "provenance", "") or "User-adjustable scenario input"),
            source=str(getattr(item, "source", "") or "Screening default"),
            minimum=getattr(item, "minimum", None),
            maximum=getattr(item, "maximum", None),
            step=getattr(item, "step", None),
        )
        for item in list(getattr(implied, "assumptions", []) or [])
        if bool(getattr(item, "editable", True))
    )
    expectation = next(
        (
            item for item in list(getattr(implied, "expectations", []) or [])
            if getattr(item, "implied_value", None) is not None
        ),
        None,
    )
    fallback_implied_label = str(getattr(expectation, "metric", "") or "Implied operating outcome")
    fallback_implied_value = (
        f"{getattr(expectation, 'implied_value'):,.2f} {getattr(expectation, 'unit', '')}".strip()
        if expectation is not None else "Not established"
    )
    return MarketExpectationsViewModel(
        status=str(
            getattr(snapshot, "status", "")
            or getattr(implied, "status", "")
            or "Unavailable"
        ),
        methodology=str(
            getattr(snapshot, "title", "")
            or getattr(snapshot, "template", "")
            or getattr(implied, "template", "")
            or "Market-implied expectations"
        ).replace("_", " ").title(),
        price_label=(
            "Price at research cutoff"
            if getattr(result, "demo_case", None)
            else "Current price"
        ),
        current_price=_money(current_price, currency),
        price_as_of=str(
            getattr(snapshot, "price_as_of", "")
            or getattr(implied, "price_as_of", "")
            or "Unknown"
        ),
        price_source=str(
            getattr(implied, "price_source", "")
            or getattr(getattr(result, "recent_market_context", None), "source", "")
            or "Source not recorded"
        ),
        operating_base_label=str(getattr(snapshot, "operating_base_label", "") or "Operating base"),
        operating_base_value=str(getattr(snapshot, "operating_base_value", "") or "Not established"),
        valuation_anchor_label=str(getattr(snapshot, "valuation_anchor_label", "") or "Valuation anchor"),
        valuation_anchor_value=str(getattr(snapshot, "valuation_anchor_value", "") or "Not established"),
        implied_variable_label=str(getattr(snapshot, "implied_variable_label", "") or fallback_implied_label),
        implied_variable_value=str(getattr(snapshot, "implied_variable_value", "") or fallback_implied_value),
        internal_base_label=str(getattr(snapshot, "internal_base_label", "") or "Research base"),
        internal_base_value=str(getattr(snapshot, "internal_base_value", "") or "Not established"),
        disagreement=_without_raw_ids(str(getattr(snapshot, "disagreement", "") or "Unknown")),
        interpretation=_without_raw_ids(str(
            getattr(snapshot, "interpretation", "")
            or getattr(implied, "summary", "")
            or "Market-implied expectations are not established."
        )),
        confidence=str(
            getattr(snapshot, "confidence", "")
            or getattr(expectation, "confidence", "")
            or "Unknown"
        ),
        weakest_input=str(getattr(snapshot, "weakest_input", "") or "None identified"),
        assumptions=assumptions,
    )


def _historical_analog_cards(result: Any) -> tuple[HistoricalAnalogCardViewModel, ...]:
    report = getattr(result, "historical_analogs", None)
    if report is None:
        return ()
    cards: list[HistoricalAnalogCardViewModel] = []
    used_ids: set[str] = set()
    researched_statuses = {
        "Audited Research Analog",
        "Frozen Demo Dossier",
        "Researched Episode",
    }
    assessments_by_id = {
        str(getattr(item, "analog_id", "") or ""): item
        for item in list(getattr(report, "assessments", []) or [])
    }
    for item in list(getattr(report, "dossiers", []) or []):
        item_status = str(getattr(item, "status", "") or "")
        candidate_dossier = item_status == "Historical analog candidate"
        if item_status not in researched_statuses and not candidate_dossier:
            continue
        analog_id = str(getattr(item, "analog_id", "") or "")
        if not _dossier_scope_eligible(result, item, assessments_by_id.get(analog_id)):
            continue
        lessons = list(getattr(item, "lessons", []) or [])
        insight = _first_nonempty(
            lessons,
            getattr(item, "operating_outcome", ""),
            getattr(item, "valuation_outcome", ""),
            "The historical outcome requires further synthesis.",
        )
        score = getattr(item, "audited_similarity_score", None)
        similarity = (
            f"{score}/100 audited similarity"
            if score is not None
            else str(getattr(item, "similarity_band", "") or "Similarity not scored")
        )
        cards.append(HistoricalAnalogCardViewModel(
            analog_id=analog_id,
            episode=str(getattr(item, "episode", "Historical episode") or "Historical episode"),
            kind="Candidate analog" if candidate_dossier else "Researched analog",
            status=(
                "Candidate only · not thesis evidence"
                if candidate_dossier else
                str(getattr(item, "status", "Researched Episode") or "Researched Episode")
            ),
            similarity=similarity,
            insight=_without_raw_ids(str(insight)),
            material_difference=_first_nonempty(
                list(getattr(item, "material_differences", []) or []),
                "No material difference has been established.",
            ),
            monitor=_first_nonempty(
                list(getattr(item, "linked_monitor_rules", []) or []),
                list(getattr(item, "confirm_kpis", []) or []),
                "No monitor has been linked yet.",
            ),
            limitation=_first_nonempty(
                list(getattr(item, "limitations", []) or []),
                (
                    "This sourced episode remains quarantined from the thesis and valuation "
                    "until its transferability and independent provenance pass audit."
                    if candidate_dossier else
                    "An analogy does not independently establish the current thesis."
                ),
            ),
            source_quality=str(getattr(item, "source_quality", "") or "Unknown source quality"),
        ))
        used_ids.add(analog_id)
        if len(cards) >= 2:
            break

    assessments = sorted(
        list(getattr(report, "assessments", []) or []),
        key=lambda item: (
            bool(
                getattr(item, "source_relevance_score", 0) >= 60
                and getattr(item, "sources", None)
            ),
            int(getattr(item, "applicability_score", 0) or 0),
            int(getattr(item, "source_relevance_score", 0) or 0),
        ),
        reverse=True,
    )
    for item in assessments:
        analog_id = str(getattr(item, "analog_id", "") or "")
        if analog_id in used_ids:
            continue
        applicability = int(
            getattr(item, "discovery_relevance_score", 0)
            or getattr(item, "applicability_score", 0)
            or 0
        )
        scope_status = str(getattr(item, "scope_status", "") or "")
        eligibility_code = str(getattr(item, "eligibility_code", "legacy_unchecked") or "legacy_unchecked")
        research_state = str(getattr(item, "research_state", "") or "")
        if (
            applicability < 60
            or scope_status not in {"", "in_scope"}
            or research_state == AnalogResearchState.SCOPE_REJECTED.value
            or eligibility_code in {
                "issuer_scope_violation", "archetype_scope_violation",
                "mechanism_scope_violation", "core_mechanism_unresolved",
                "security_profile_unresolved", "scope_not_evaluated",
            }
        ):
            continue
        source_backed = bool(
            int(getattr(item, "source_relevance_score", 0) or 0) >= 60
            and list(getattr(item, "sources", []) or [])
        )
        score = getattr(item, "similarity_score", None)
        similarity = (
            f"{score}/100 audited similarity"
            if score is not None
            else "Mechanism-scoped candidate · audit pending"
        )
        cards.append(HistoricalAnalogCardViewModel(
            analog_id=analog_id,
            episode=str(getattr(item, "episode", "Candidate episode") or "Candidate episode"),
            kind="Investigated candidate" if source_backed else "Candidate to investigate",
            status="Research lead · not thesis evidence",
            similarity=similarity,
            insight=_without_raw_ids(str(
                getattr(item, "what_it_suggests", "")
                or "This candidate is a research lead, not admitted thesis evidence."
            )),
            material_difference=_first_nonempty(
                list(getattr(item, "material_differences", []) or []),
                "Material differences have not been fully researched.",
            ),
            monitor=_analog_monitor(item),
            limitation=str(
                getattr(item, "what_it_cannot_establish", "")
                or "Candidate metadata cannot support the current conclusion until admitted."
            ),
            source_quality=(
                str(getattr(item, "source_quality", "") or "Source-backed research lead")
                if source_backed
                else "Episode evidence not admitted"
            ),
        ))
        if len(cards) >= 3:
            break
    return tuple(cards)


def _story_lens_cards(result: Any) -> tuple[StoryLensViewModel, ...]:
    profile = getattr(result, "security_research_profile", None)
    if profile is None and getattr(result, "identity", None) is not None:
        profile = SecurityResearchProfileResolver().resolve(result.identity)
    if profile is not None:
        assessments = (
            getattr(result, "causal_bridge_assessment", None),
            getattr(result, "equity_lens_assessment", None),
            getattr(result, "credit_lens_assessment", None),
        )
        if not all(item is not None for item in assessments):
            assessments = build_research_assessments(
                profile,
                list(getattr(result, "metrics", []) or []),
                list(getattr(result, "events", []) or []),
                one_pager=getattr(result, "ic_one_pager", None),
                credit_lens=getattr(result, "credit_lens", None),
            )
        causal, equity, credit = assessments
        return tuple(
            StoryLensViewModel(
                title=title,
                status=assessment.status.value,
                insight=_without_raw_ids(
                    assessment.direct_conclusion
                    + (f" Limitation: {assessment.strongest_limitation}" if assessment.strongest_limitation else "")
                ),
                next_action=assessment.next_action,
                evidence_count=len(assessment.evidence_ids),
            )
            for title, assessment in (
                ("Causal Bridge", causal),
                ("Equity Lens", equity),
                ("Credit Lens", credit),
            )
        )
    cards = list(getattr(result, "story_cards", []) or [])
    if not cards:
        return ()
    selected: list[Any] = []
    for phrase in ("what changed", "causal", "equity", "credit"):
        match = next(
            (
                item for item in cards
                if item not in selected
                and phrase in str(getattr(item, "title", "")).casefold()
            ),
            None,
        )
        if match is not None:
            selected.append(match)
    for item in cards:
        if item not in selected:
            selected.append(item)
        if len(selected) >= 4:
            break
    return tuple(
        StoryLensViewModel(
            title=str(getattr(item, "title", "Research lens") or "Research lens"),
            status=str(getattr(item, "status", "Unknown") or "Unknown"),
            insight=_without_raw_ids(str(
                getattr(item, "body", "")
                or getattr(item, "summary", "")
                or "No decision-relevant synthesis was produced."
            )),
            next_action=_without_raw_ids(str(getattr(item, "next_action", "") or "")),
            evidence_count=(
                int(getattr(item, "evidence_count"))
                if getattr(item, "evidence_count", None) is not None
                else len(getattr(item, "evidence", []) or [])
            ),
        )
        for item in selected[:4]
    )


def _dossier_scope_eligible(result: Any, dossier: Any, assessment: Any | None) -> bool:
    if assessment is not None:
        scope = str(getattr(assessment, "scope_status", "") or "")
        code = str(getattr(assessment, "eligibility_code", "") or "")
        if scope not in {"", "in_scope"} or code.endswith("scope_violation"):
            return False
    status = str(getattr(dossier, "status", "") or "")
    if status != "Historical analog candidate":
        return True
    identity = getattr(result, "identity", None)
    current_name_tokens = _meaningful_company_tokens(str(getattr(identity, "name", "") or ""))
    company_tokens = set().union(*(
        _meaningful_company_tokens(str(value))
        for value in list(getattr(dossier, "companies", []) or [])
    )) if list(getattr(dossier, "companies", []) or []) else set()
    if current_name_tokens and company_tokens and not (current_name_tokens & company_tokens):
        return bool(
            assessment is not None
            and str(getattr(assessment, "current_mechanism_id", "") or "")
            and str(getattr(assessment, "transfer_dimension", "") or "")
        )
    return True


def _meaningful_company_tokens(value: str) -> set[str]:
    ignored = {"inc", "corp", "corporation", "company", "limited", "holdings", "group", "plc"}
    return {
        token for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in ignored
    }


def _analog_monitor(item: Any) -> str:
    mechanism = str(getattr(item, "current_mechanism_id", "") or "")
    if mechanism == "cloud_ai_investment_cycle":
        return (
            "Cloud revenue, capacity utilization, segment EBITA, capex intensity, "
            "depreciation, commitments, and free cash flow."
        )
    if mechanism == "aum_fee_earnings_conversion":
        return (
            "Net flows, market/FX AUM effects, organic base-fee growth, fee rate, "
            "adjusted operating margin, and diluted shares."
        )
    return _first_nonempty(
        list(getattr(item, "confirm_kpis", []) or []),
        list(getattr(item, "reject_kpis", []) or []),
        "No mechanism-specific monitor has been defined.",
    )


def _debate_view_model(result: Any) -> DebateViewModel | None:
    panel = getattr(result, "bull_bear_judge", None)
    if panel is None:
        return None
    canonical = _canonical_investigation_actions(result)
    queue = list(canonical.actions)
    rank_by_id = {
        str(getattr(item, "action_id", "") or ""): int(
            getattr(item, "rank", 0) or 0
        )
        for item in queue
        if getattr(item, "action_id", "")
    }
    action_references = []
    for item in list(getattr(panel, "resolution_plan", []) or []):
        reference = str(getattr(item, "action_id", "") or "")
        canonical_id = canonical.resolve_action_id(reference) or reference
        rank = rank_by_id.get(canonical_id, 0)
        if rank:
            action_references.append(f"Action {rank}")
    return DebateViewModel(
        status=str(getattr(panel, "status", "Unknown") or "Unknown"),
        bull_case=_without_raw_ids(str(getattr(panel, "bull_case", "") or "Bull case not established.")),
        bear_case=_without_raw_ids(str(getattr(panel, "bear_case", "") or "Bear case not established.")),
        judge_accepts=tuple(
            _without_raw_ids(str(value))
            for value in list(getattr(panel, "judge_accepts", []) or [])[:5]
        ),
        still_unproven=tuple(
            _finding_only(_without_raw_ids(str(value)))
            for value in list(getattr(panel, "still_unproven", []) or [])[:5]
        ),
        resolution_actions=(),
        action_references=tuple(_dedupe(action_references)[:3]),
    )


def _investigation_action_view_models(
    result: Any,
    *,
    limit: int | None = 3,
) -> tuple[InvestigationActionViewModel, ...]:
    rows = []
    for item in _canonical_investigation_actions(result).actions:
        status = str(getattr(item, "status", "Open") or "Open")
        if status.casefold() in {"resolved", "closed", "not_needed"}:
            continue
        readable_status = _action_status_label(status)
        action_kind = str(
            getattr(item, "action_kind", "primary_source_validation")
            or "primary_source_validation"
        )
        auto_resolvable = bool(getattr(item, "auto_resolvable", False))
        intent_type = (
            "retry_attribution" if action_kind == "market_event_attribution"
            else "execute_action" if auto_resolvable
            else "configure_source"
        )
        primary_cta = "Select investigation"
        rows.append(InvestigationActionViewModel(
            rank=int(getattr(item, "rank", len(rows) + 1) or len(rows) + 1),
            action_id=str(getattr(item, "action_id", "") or f"action-{len(rows) + 1}"),
            action_kind=action_kind,
            title=_without_raw_ids(str(getattr(item, "title", "") or "Investigation")),
            objective=_without_raw_ids(str(
                getattr(item, "objective", "")
                or getattr(item, "title", "")
                or "Complete the investigation"
            )),
            status=readable_status,
            materiality=str(getattr(item, "materiality", "Medium") or "Medium"),
            why_it_matters=_without_raw_ids(str(getattr(item, "why_it_matters", "") or "Decision-relevant evidence remains unresolved.")),
            execution_mode=str(getattr(item, "execution_mode", "automatic") or "automatic"),
            auto_resolvable=auto_resolvable,
            event_id=str(getattr(item, "event_id", "") or ""),
            attempt_summary=_without_raw_ids(str(getattr(item, "attempt_summary", "") or "Not attempted")),
            blocking_reason=_without_raw_ids(str(
                getattr(item, "blocking_reason", "") or ""
            )),
            completion_criteria=tuple(
                _without_raw_ids(str(value))
                for value in list(getattr(item, "completion_criteria", []) or [])
                if value
            ),
            source_families=tuple(
                str(value)
                for value in list(getattr(item, "required_source_families", []) or [])
                if value
            ),
            setup_steps=(
                "Set up an eligible source.",
                "Confirm the selected investigation in the sidebar.",
            ) if intent_type == "configure_source" else (),
            primary_cta=primary_cta,
            intent_type=intent_type,
            allow_price_upload=action_kind == "market_event_attribution",
        ))
        if limit is not None and len(rows) >= limit:
            break
    return tuple(rows)


def _canonical_investigation_actions(result: Any):
    identity = getattr(result, "identity", None)
    ticker = str(getattr(identity, "ticker", "") or "")
    return canonicalize_investigation_actions(
        ticker,
        list(getattr(result, "prioritized_research_queue", []) or []),
    )


def build_investigation_action_view_models(
    result: Any,
) -> tuple[InvestigationActionViewModel, ...]:
    """Return the complete canonical queue for Evidence and Audit routes."""

    return _investigation_action_view_models(result, limit=None)


def _finding_only(value: str) -> str:
    return re.split(r"\s+Next:\s*", value, maxsplit=1, flags=re.I)[0].strip()


def _action_status_label(value: str) -> str:
    normalized = str(value or "").strip().casefold().replace(" ", "_")
    return {
        "genuinely_unavailable": "Unavailable",
        "unavailable": "Unavailable",
        "licensed_or_manual_required": "Needs setup",
        "retrieval_failed": "Needs attention",
        "failed": "Needs attention",
        "attempted_unresolved": "Attempted · unresolved",
        "not_attempted_missing_connector": "Needs setup",
        "planned": "Open",
        "pending": "Open",
        "open": "Open",
    }.get(normalized, normalized.replace("_", " ").title() or "Open")


def _clean_summary_items(values: Iterable[str]) -> tuple[str, ...]:
    cleaned = []
    for value in values:
        item = _without_raw_ids(str(value or "").strip())
        if not item or item.casefold() in {existing.casefold() for existing in cleaned}:
            continue
        cleaned.append(item)
        if len(cleaned) >= 5:
            break
    return tuple(cleaned) or ("No decision-relevant summary was available.",)


def _research_limitation(result: Any, route: str) -> str:
    if route == "valuation":
        values = list(getattr(getattr(result, "valuation", None), "missing_data", []) or [])
    elif route == "market":
        values = list(getattr(getattr(result, "market_capture_readiness", None), "data_gaps", []) or [])
    elif route == "analogs":
        values = list(getattr(getattr(result, "historical_analogs", None), "data_gaps", []) or [])
    else:
        values = list(getattr(getattr(result, "thesis_validation", None), "required_next_evidence", []) or [])
    return _without_raw_ids(str(values[0])) if values else "No material limitation was separately reported."


def _valuation_input_summary(valuation: Any) -> str:
    resolutions = list(getattr(valuation, "input_resolutions", []) or [])
    if not resolutions:
        return "Valuation input resolution was not recorded for this historical result."
    resolved = sum(
        str(getattr(item, "status", "")).casefold() in {"available", "derived"}
        for item in resolutions
    )
    derived = [
        str(getattr(item, "input_name", "input") or "input")
        for item in resolutions
        if str(getattr(item, "status", "")).casefold() == "derived"
    ]
    summary = f"{resolved} of {len(resolutions)} recorded valuation inputs are resolved."
    if derived:
        summary += " Deterministic derivations: " + ", ".join(derived[:3]) + "."
    return summary


def _formatted_resolution_value(item: Any) -> str:
    value = getattr(item, "value", None)
    unit = str(getattr(item, "unit", "") or "")
    if value is None:
        return "Not available"
    if unit.casefold() in {"shares", "share", "usd", "eur", "gbp", "cny", "jpy"}:
        return f"{float(value):,.0f} {unit}".strip()
    return f"{float(value):,.2f} {unit}".strip()


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if isinstance(value, (list, tuple)):
            for item in value:
                if str(item or "").strip():
                    return str(item).strip()
        elif str(value or "").strip():
            return str(value).strip()
    return ""


def _priority_source_event(idea: Any) -> Any:
    events = list(getattr(idea, "source_events", []) or [])
    if not events:
        return None

    def priority(event: Any) -> tuple[int, str, int]:
        metrics = event.metrics if isinstance(getattr(event, "metrics", None), dict) else {}
        grade = str(metrics.get("thesis_grade_status") or "").casefold()
        validated = int(
            grade not in {"watch item", "not thesis-grade", "unmapped"}
            and not metrics.get("normalization_required")
        )
        return validated, str(getattr(event, "event_date", "") or ""), int(getattr(event, "severity", 0) or 0)

    return max(events, key=priority)


def _evidence_references(event: Any) -> tuple[EvidenceReferenceViewModel, ...]:
    if event is None:
        return ()
    references: list[EvidenceReferenceViewModel] = []
    for citation in list(getattr(event, "citations", []) or [])[:3]:
        label_parts = [
            str(getattr(citation, "source", "") or getattr(event, "source", "") or "Source"),
            str(getattr(citation, "form", "") or ""),
            str(getattr(citation, "section", "") or ""),
        ]
        references.append(EvidenceReferenceViewModel(
            label=" — ".join(value for value in label_parts if value),
            date=str(getattr(citation, "filed", "") or getattr(event, "event_date", "") or ""),
            source_type=str(getattr(citation, "form", "") or getattr(event, "category", "") or ""),
            url=str(getattr(citation, "url", "") or ""),
            technical_id=str(getattr(citation, "accession", "") or ""),
        ))
    if not references:
        readable_label = _without_raw_ids(str(
            getattr(event, "source", "")
            or getattr(event, "title", "")
            or ""
        )).strip()
        references.append(EvidenceReferenceViewModel(
            label=(
                readable_label
                or "Readable source reference unavailable · review Evidence"
            ),
            date=str(getattr(event, "event_date", "") or ""),
            source_type=str(getattr(event, "category", "") or ""),
        ))
    return tuple(references)


def _next_actions(result: Any, brief: Any) -> tuple[str, ...]:
    values: list[str] = []
    for item in _canonical_investigation_actions(result).actions:
        title = str(getattr(item, "title", "") or "").strip()
        if title:
            values.append(title)
    work_order = getattr(result, "evidence_work_order", None)
    for item in list(getattr(work_order, "items", []) or []):
        action = str(getattr(item, "action", "") or "").strip()
        if action:
            values.append(action)
    for value in (
        str(getattr(brief, "why_research_next", "") or ""),
        str(getattr(brief, "next_decision", "") or ""),
    ):
        if value.strip():
            values.append(value.strip())
    return tuple(_dedupe(_without_raw_ids(value) for value in values if value)[:3]) or (
        "Re-run after the next material filing, earnings call, or monitor trigger.",
    )


def _priced_in_summary(result: Any) -> str:
    snapshot = getattr(result, "ic_implied_snapshot", None)
    if snapshot and getattr(snapshot, "interpretation", ""):
        return _without_raw_ids(str(snapshot.interpretation))
    market = getattr(result, "market_implied_expectations", None)
    expectations = list(getattr(market, "expectations", []) or [])
    available = [item for item in expectations if getattr(item, "implied_value", None) is not None]
    if available:
        item = available[0]
        return f"{item.metric}: {item.implied_value:,.2f} {item.unit} ({item.confidence} confidence)."
    return "Market-implied expectations are not established from the current admitted evidence."


def _is_counter_thesis_gap(value: str) -> bool:
    lowered = value.casefold().strip()
    return not lowered or any(phrase in lowered for phrase in _COUNTER_GAP_PHRASES)


def _limit_paragraphs(value: str) -> str:
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", value) if item.strip()]
    return "\n\n".join(paragraphs[:3])


def _without_raw_ids(value: str) -> str:
    cleaned = re.sub(r"\s*\[citations?\s*:[^\]]+\]", "", value or "", flags=re.I)
    cleaned = _RAW_ID.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip(" ,;|")


def _money(value: float | None, currency: str) -> str:
    if value is None:
        return "Not established"
    symbol = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    return f"{symbol}{value:,.2f}"


def _money_compact(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "Not disclosed"
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        scaled, suffix = value / 1_000_000_000, "B"
    elif absolute >= 1_000_000:
        scaled, suffix = value / 1_000_000, "M"
    else:
        scaled, suffix = value, ""
    symbol = "$" if currency.upper() == "USD" else f"{currency.upper()} "
    return f"{symbol}{scaled:,.1f}{suffix}"


def _cash_conversion_answer_views(
    bridge: Any,
    *,
    expected_cutoff: str = "",
) -> tuple[
    tuple[dict[str, str], ...],
    tuple[tuple[str, tuple[dict[str, str], ...]], ...],
]:
    if not validate_cash_conversion_recurrence_bridge(
        bridge,
        expected_cutoff=expected_cutoff,
    ).valid:
        return (), ()
    periods = tuple(getattr(bridge, "periods", ()) or ())
    comparison = tuple({
        "Period": str(getattr(item, "period_end", "") or "Unknown"),
        "Net income": _money_compact(getattr(item, "net_income", None)),
        "Operating cash flow": _money_compact(
            getattr(item, "operating_cash_flow", None)
        ),
        "OCF / NI": (
            f"{getattr(item, 'operating_cash_flow_to_net_income') * 100:.1f}%"
            if getattr(item, "operating_cash_flow_to_net_income", None) is not None
            else "Not established"
        ),
        "Capex": _money_compact(getattr(item, "capital_expenditure", None)),
        "Free cash flow": _money_compact(getattr(item, "free_cash_flow", None)),
    } for item in periods)
    current = periods[0] if periods else None
    if current is None:
        return comparison, ()

    def row(label: str, field: str) -> dict[str, str]:
        value = getattr(current, field, None)
        return {
            "Component": label,
            "Current TTM": _money_compact(value),
            "Status": "Available" if value is not None else "Not disclosed",
        }

    sections = (
        ("Earnings-to-cash drivers", (
            row("Net income", "net_income"),
            row("Working-capital contribution", "working_capital_change"),
            row("Cash taxes", "cash_taxes"),
            row("Operating cash flow", "operating_cash_flow"),
        )),
        ("Reinvestment and free cash flow", (
            row("Capital expenditure", "capital_expenditure"),
            row("Free cash flow", "free_cash_flow"),
        )),
        ("Financing and capital returns", (
            row("Debt issued", "debt_issued"),
            row("Debt repaid", "debt_repaid"),
            row("Dividends", "dividends"),
            row("Repurchases", "repurchases"),
            row("Cash after dividends and repurchases", "cash_after_dividends_and_repurchases"),
        )),
    )
    return comparison, sections


def _percent(value: float | None) -> str:
    return "Not established" if value is None else f"{value:,.1f}%"


def _percent_probability(value: float | None) -> str:
    if value is None:
        return "Uncalibrated"
    percent = value * 100 if value <= 1 else value
    return f"{percent:,.0f}%"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold().strip()
        if key and key not in seen:
            seen.add(key)
            result.append(value.strip())
    return result
