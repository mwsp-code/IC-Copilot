from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Iterable


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
    primary_cta: str = ""
    intent_type: str = "configure_source"
    allow_price_upload: bool = False


@dataclass(frozen=True)
class DecisionPageIntent:
    action_id: str
    intent_type: str
    event_id: str = ""
    uploaded_csv: bytes = b""


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
class ThesisDriversViewModel(ResearchRouteViewModel):
    pass


@dataclass(frozen=True)
class ModelValuationViewModel(ResearchRouteViewModel):
    pass


@dataclass(frozen=True)
class ExpectationsReactionViewModel(ResearchRouteViewModel):
    pass


@dataclass(frozen=True)
class AnalogsPeersViewModel(ResearchRouteViewModel):
    pass


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


@dataclass(frozen=True)
class PublicationRepairIntent:
    intent_type: str
    action_ids: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class EvidencePageViewModel:
    admitted_count: int
    excluded_count: int
    contradiction_count: int
    evidence_hash: str
    requests: tuple[ResearchRequestCardViewModel, ...] = ()
    candidates: tuple[CandidateStatusViewModel, ...] = ()


def build_evidence_page_view_model(result: Any) -> EvidencePageViewModel:
    graph = getattr(result, "evidence_graph", None)
    qa = getattr(result, "independent_research_qa", None)
    requests = tuple(
        ResearchRequestCardViewModel(
            request_id=str(getattr(item, "request_id", "") or ""),
            objective=str(
                getattr(item, "objective", "")
                or getattr(item, "question", "")
                or "Targeted investigation"
            ),
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
            stopping_rule=str(getattr(item, "stopping_rule", "") or ""),
        )
        for item in list(
            getattr(qa, "research_requests", [])
            or getattr(qa, "evidence_requests", [])
            or []
        )[:20]
    )
    decisions = {
        str(getattr(item, "candidate_id", "")): item
        for item in list(getattr(graph, "admission_decisions", []) or [])
    }
    candidates = []
    for item in list(getattr(qa, "candidate_discovery_items", []) or [])[:40]:
        candidate_id = str(getattr(item, "candidate_id", "") or "")
        decision = decisions.get(candidate_id)
        candidates.append(CandidateStatusViewModel(
            candidate_id=candidate_id,
            candidate_type=str(getattr(item, "candidate_type", "source_candidate") or "source_candidate"),
            title=str(getattr(item, "title", "") or getattr(item, "objective", "") or "Research candidate"),
            status=str(getattr(item, "status", "quarantined") or "quarantined"),
            origin=" / ".join(
                value for value in (
                    str(getattr(item, "origin_role", "") or ""),
                    str(getattr(item, "origin_provider", "") or ""),
                ) if value
            ),
            materiality=str(getattr(item, "materiality", "Medium") or "Medium"),
            source_family=str(getattr(item, "proposed_source_family", "") or ""),
            url=str(getattr(item, "url", "") or ""),
            decision_reason=str(getattr(decision, "reason", "") or ""),
        ))
        if not candidates[-1].decision_reason:
            history = list(getattr(item, "status_history", ()) or ())
            if history:
                candidates[-1] = CandidateStatusViewModel(
                    **{
                        **candidates[-1].__dict__,
                        "decision_reason": str(
                            getattr(history[-1], "reason", "") or ""
                        ),
                    }
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
        candidates=tuple(candidates),
    )


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


def build_decision_brief_view_model(result: Any) -> DecisionBriefViewModel:
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
    return DecisionBriefViewModel(
        ticker=str(identity.ticker),
        company_name=str(identity.name),
        run_status="Frozen demo" if getattr(result, "demo_case", None) else "Completed",
        freshness=overall_freshness,
        evidence_quality=evidence_quality,
        verdict=_without_raw_ids(str(getattr(brief, "verdict", "") or "Research result available")),
        stage=str(getattr(brief, "stage", "") or getattr(top_idea, "stage", "Unclassified")),
        direction=str(getattr(brief, "direction", "") or getattr(top_idea, "direction", "Watch")),
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
                str(getattr(getattr(result, "market_implied_expectations", None), "summary", "") or ""),
                str(getattr(getattr(result, "earnings_surprise_proxy", None), "headline", "") or ""),
            ]),
            strongest_limitation=_research_limitation(result, "valuation"),
            action_references=action_refs,
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


def build_integrity_timeline_view_model(result: Any) -> IntegrityTimelineViewModel:
    qa = getattr(result, "independent_research_qa", None)
    graph = getattr(result, "evidence_graph", None)
    hybrid = getattr(result, "hybrid_research", None)
    events: list[IntegrityTimelineEventViewModel] = []
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
        cards.append(RepairActionCardViewModel(
            action_id=str(getattr(action, "action_id", "") or ""),
            title=_without_raw_ids(str(
                getattr(failure, "summary", "")
                or getattr(action, "objective", "Repair publication blocker")
            )),
            status=str(getattr(action, "status", "planned") or "planned").replace("_", " ").title(),
            why_it_matters=_without_raw_ids(str(
                getattr(failure, "blocking_rule", "")
                or "The result cannot be presented as publication-quality until this is resolved."
            )),
            what_the_app_will_do=_without_raw_ids(str(
                getattr(action, "objective", "Run the registered repair workflow and recheck the affected gates.")
            )),
            source_families=tuple(getattr(action, "required_source_families", ()) or ()),
            attempts=int(getattr(action, "attempts", 0) or 0),
            result_or_blocker=_without_raw_ids(str(
                getattr(action, "resolution_summary", "")
                or getattr(action, "stopping_reason", "")
                or ""
            )),
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
    )


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
    for item in list(getattr(report, "dossiers", []) or []):
        analog_id = str(getattr(item, "analog_id", "") or "")
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
            kind="Researched analog",
            status=str(getattr(item, "status", "Researched Episode") or "Researched Episode"),
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
                "An analogy does not independently establish the current thesis.",
            ),
            source_quality=str(getattr(item, "source_quality", "") or "Unknown source quality"),
        ))
        used_ids.add(analog_id)
        if len(cards) >= 2:
            break

    for item in list(getattr(report, "assessments", []) or []):
        analog_id = str(getattr(item, "analog_id", "") or "")
        if analog_id in used_ids:
            continue
        score = getattr(item, "similarity_score", None)
        similarity = (
            f"{score}/100 similarity"
            if score is not None
            else f"{getattr(item, 'similarity_band', 'Unscored')}; score withheld pending audit"
        )
        cards.append(HistoricalAnalogCardViewModel(
            analog_id=analog_id,
            episode=str(getattr(item, "episode", "Candidate episode") or "Candidate episode"),
            kind="Candidate analog",
            status=str(getattr(item, "status", "Exploratory Candidate") or "Exploratory Candidate"),
            similarity=similarity,
            insight=_without_raw_ids(str(
                getattr(item, "what_it_suggests", "")
                or "This candidate is a research lead, not admitted thesis evidence."
            )),
            material_difference=_first_nonempty(
                list(getattr(item, "material_differences", []) or []),
                "Material differences have not been fully researched.",
            ),
            monitor=_first_nonempty(
                list(getattr(item, "confirm_kpis", []) or []),
                list(getattr(item, "reject_kpis", []) or []),
                "No current-case test has been defined.",
            ),
            limitation=str(
                getattr(item, "what_it_cannot_establish", "")
                or "Candidate metadata cannot support the current conclusion until admitted."
            ),
            source_quality=str(getattr(item, "source_quality", "") or "Exploratory"),
        ))
        if len(cards) >= 3:
            break
    return tuple(cards)


def _story_lens_cards(result: Any) -> tuple[StoryLensViewModel, ...]:
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
            evidence_count=len(getattr(item, "evidence", []) or []),
        )
        for item in selected[:4]
    )


def _debate_view_model(result: Any) -> DebateViewModel | None:
    panel = getattr(result, "bull_bear_judge", None)
    if panel is None:
        return None
    queue = list(getattr(result, "prioritized_research_queue", []) or [])
    rank_by_id = {
        str(getattr(item, "action_id", "") or ""): int(
            getattr(item, "rank", 0) or 0
        )
        for item in queue
        if getattr(item, "action_id", "")
    }
    action_references = []
    for item in list(getattr(panel, "resolution_plan", []) or []):
        rank = rank_by_id.get(str(getattr(item, "action_id", "") or ""), 0)
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
    for item in list(getattr(result, "prioritized_research_queue", []) or []):
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
            else "configure_source"
            if readable_status in {"Unavailable", "Needs setup"}
            else "execute_action" if auto_resolvable
            else "configure_source"
        )
        primary_cta = (
            "Retry attribution" if action_kind == "market_event_attribution"
            else "Open source setup"
            if intent_type == "configure_source"
            else "Run investigation" if auto_resolvable
            else "Open source setup"
        )
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
            primary_cta=primary_cta,
            intent_type=intent_type,
            allow_price_upload=action_kind == "market_event_attribution",
        ))
        if limit is not None and len(rows) >= limit:
            break
    return tuple(rows)


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
    for item in list(getattr(result, "prioritized_research_queue", []) or []):
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
