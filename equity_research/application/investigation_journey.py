from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

from .investigation_action_canonicalization import (
    canonicalize_investigation_actions,
)
from .research_request_outcomes import ResearchIntentAnswerResolver


class InvestigationStage(str, Enum):
    SELECTED = "investigation_selected"
    EVIDENCE = "evidence_retrieval_and_admission"
    PRIMARY_ANALYSIS = "primary_analysis"
    VALIDATION = "deterministic_validation"
    INDEPENDENT_QA = "independent_qa_and_scouting"
    REVISION_CLOSURE = "revision_and_closure"
    RESULT_READY = "result_ready_and_persistence"

    @property
    def label(self) -> str:
        return {
            self.SELECTED: "Investigation selected",
            self.EVIDENCE: "Sources checked and evidence admitted",
            self.PRIMARY_ANALYSIS: "Primary analysis",
            self.VALIDATION: "Deterministic validation",
            self.INDEPENDENT_QA: "Independent QA and scouting",
            self.REVISION_CLOSURE: "Revision and closure",
            self.RESULT_READY: "Result ready and restart recovery",
        }[self]


INVESTIGATION_STAGE_ORDER = tuple(InvestigationStage)


def resolve_investigation_stage(*values: str) -> InvestigationStage:
    """Map detailed provider activity to one stable, readable journey stage."""

    normalized = " ".join(str(value or "") for value in values).casefold()
    if any(token in normalized for token in ("saving", "persist", "result ready")):
        return InvestigationStage.RESULT_READY
    if any(
        token in normalized
        for token in ("closure", "revision", "revise", "adjudicat")
    ):
        return InvestigationStage.REVISION_CLOSURE
    if any(
        token in normalized
        for token in ("audit", "auditor", "scout", "independent qa", "checker")
    ):
        return InvestigationStage.INDEPENDENT_QA
    if any(token in normalized for token in ("validat", "preflight")):
        return InvestigationStage.VALIDATION
    if any(
        token in normalized
        for token in (
            "analysis", "synthesis", "primary", "research", "planner", "plan",
        )
    ):
        return InvestigationStage.PRIMARY_ANALYSIS
    if any(
        token in normalized
        for token in ("source", "retriev", "evidence", "admission", "bootstrap")
    ):
        return InvestigationStage.EVIDENCE
    return InvestigationStage.SELECTED


def advance_investigation_stage(
    current: InvestigationStage | str,
    proposed: InvestigationStage | str,
) -> InvestigationStage:
    """Keep coarse progress monotonic when providers emit noisy callbacks."""

    current_stage = (
        current
        if isinstance(current, InvestigationStage)
        else resolve_investigation_stage(str(current or ""))
    )
    proposed_stage = (
        proposed
        if isinstance(proposed, InvestigationStage)
        else resolve_investigation_stage(str(proposed or ""))
    )
    return INVESTIGATION_STAGE_ORDER[max(
        INVESTIGATION_STAGE_ORDER.index(current_stage),
        INVESTIGATION_STAGE_ORDER.index(proposed_stage),
    )]


@dataclass(frozen=True)
class ResearchOutcomeDiff:
    admitted_evidence_delta: int = 0
    thesis_changed: bool = False
    valuation_changed: bool = False
    confidence_changed: bool = False
    action_status_changed: bool = False
    change_summaries: tuple[str, ...] = ()


@dataclass(frozen=True)
class InvestigationOutcomeSummary:
    operation_label: str
    objective: str
    outcome: str
    admitted_evidence_count: int
    changes: ResearchOutcomeDiff
    remaining_actions: tuple[str, ...] = ()
    qa_status: str = "not_run"
    closure_status: str = "not_run"
    persistence_status: str = "not_saved"
    answer_status: str = ""
    direct_answer: str = ""
    confidence: str = "Unknown"
    key_evidence: tuple[str, ...] = ()
    strongest_limitation: str = ""
    run_status: str = "completed"
    qa_state: str = "not_run"
    answer_basis: str = ""
    evidence_hash: str = ""
    data_cutoff: str = ""
    analysis_version: str = ""


@dataclass(frozen=True)
class InvestigationJourney:
    ticker: str
    operation_kind: str
    operation_label: str
    objective: str
    stages: tuple[tuple[InvestigationStage, str], ...]
    current_stage: InvestigationStage
    elapsed_seconds: float = 0.0
    independent_qa_running: bool = False
    outcome_summary: InvestigationOutcomeSummary | None = None


def build_investigation_journey(
    result: Any,
    *,
    operation_kind: str,
    operation_label: str,
    objective: str = "",
    outcome: str = "",
    persistence_status: str = "not_saved",
    parent_result: Any | None = None,
    active_stage: str = "",
    active: bool = False,
    elapsed_seconds: float = 0.0,
    events: Iterable[Any] = (),
    research_intent_id: str = "",
    data_cutoff: str = "",
    evidence_hash: str = "",
) -> InvestigationJourney:
    ticker = str(getattr(getattr(result, "identity", None), "ticker", "") or "")
    mapped_current = _stage_from_text(active_stage)
    completed_stages = _completed_stages(events)
    if not active:
        completed_stages = set(InvestigationStage)
        mapped_current = InvestigationStage.RESULT_READY
    elif mapped_current is InvestigationStage.SELECTED:
        completed_stages.add(InvestigationStage.SELECTED)
    if active:
        current_index = INVESTIGATION_STAGE_ORDER.index(mapped_current)
        completed_stages.update(INVESTIGATION_STAGE_ORDER[:current_index])

    stage_rows = tuple(
        (
            stage,
            "active" if stage is mapped_current and active
            else "completed" if stage in completed_stages
            else "pending",
        )
        for stage in InvestigationStage
    )
    summary = None
    if not active:
        summary = _outcome_summary(
            result,
            operation_label=operation_label,
            objective=objective,
            outcome=outcome,
            persistence_status=persistence_status,
            parent_result=parent_result,
            research_intent_id=research_intent_id,
            data_cutoff=data_cutoff,
            evidence_hash=evidence_hash,
        )
    return InvestigationJourney(
        ticker=ticker,
        operation_kind=operation_kind,
        operation_label=operation_label or f"Research for {ticker}",
        objective=objective or operation_label,
        stages=stage_rows,
        current_stage=mapped_current,
        elapsed_seconds=max(0.0, float(elapsed_seconds or 0.0)),
        independent_qa_running=(
            active and mapped_current in {
                InvestigationStage.INDEPENDENT_QA,
                InvestigationStage.REVISION_CLOSURE,
            }
        ),
        outcome_summary=summary,
    )


def _outcome_summary(
    result: Any,
    *,
    operation_label: str,
    objective: str,
    outcome: str,
    persistence_status: str,
    parent_result: Any | None,
    research_intent_id: str,
    data_cutoff: str,
    evidence_hash: str,
) -> InvestigationOutcomeSummary:
    qa = getattr(result, "independent_research_qa", None)
    closure = getattr(qa, "closure_result", None)
    evidence_count = len(
        getattr(getattr(result, "evidence_graph", None), "admitted_evidence_ids", ())
        or ()
    )
    queue = list(_canonical_actions(result))
    remaining = tuple(
        str(getattr(item, "objective", "") or getattr(item, "title", "") or "")
        for item in queue
        if str(getattr(item, "status", "") or "").casefold()
        not in {"resolved", "completed", "closed", "admitted"}
    )[:3]
    derived_outcome = str(outcome or "").replace("_", " ").strip().casefold()
    if not derived_outcome:
        derived_outcome = "unresolved" if remaining else "resolved"
    question = objective or operation_label
    request_items = tuple(
        getattr(qa, "research_requests", ())
        or getattr(qa, "evidence_requests", ())
        or ()
    )
    answer_state = ResearchIntentAnswerResolver().resolve_state(
        result,
        request_items,
        intent_id=research_intent_id,
        question=question,
        run_status="completed",
        expected_cutoff=data_cutoff,
    )
    answer_status = answer_state.answer_status
    direct_answer = answer_state.direct_answer
    confidence = answer_state.confidence
    key_evidence = tuple(value for value in answer_state.evidence_found if value)[:5]
    limitations = tuple(value for value in answer_state.evidence_missing if value)
    strongest_limitation = limitations[0] if limitations else ""
    remaining = tuple(dict.fromkeys(
        ([answer_state.remaining_action] if answer_state.remaining_action else [])
        + list(limitations)
        + ([] if answer_status == "answered" else list(remaining))
    ))[:3]
    if answer_status == "answered" and answer_state.qa_state in {
        "closed", "closed_with_residual_risk",
    }:
        derived_outcome = "resolved"
    elif answer_status == "answered" and answer_state.qa_state in {
        "pending_due_to_new_evidence", "not_run",
    }:
        derived_outcome = "answered · qa pending"
    elif answer_status in {"partially_answered", "blocked", "not_researched"}:
        derived_outcome = "partially resolved"
    return InvestigationOutcomeSummary(
        operation_label=operation_label,
        objective=objective or operation_label,
        outcome=derived_outcome,
        admitted_evidence_count=evidence_count,
        changes=_research_outcome_diff(parent_result, result),
        remaining_actions=remaining,
        qa_status=str(getattr(qa, "status", "") or "not_run"),
        closure_status=str(getattr(closure, "state", "") or "not_run"),
        persistence_status=persistence_status,
        answer_status=answer_status,
        direct_answer=direct_answer,
        confidence=confidence,
        key_evidence=key_evidence,
        strongest_limitation=strongest_limitation,
        run_status="completed",
        qa_state=answer_state.qa_state,
        answer_basis=answer_state.answer_basis,
        evidence_hash=(answer_state.evidence_hash or evidence_hash),
        data_cutoff=(answer_state.data_cutoff or data_cutoff),
        analysis_version=answer_state.analysis_version,
    )


def _focused_agent_answer(result: Any, objective: str) -> Any | None:
    hybrid = getattr(result, "hybrid_research", None)
    sessions = list(getattr(hybrid, "agent_research_sessions", ()) or ())
    if not sessions and getattr(hybrid, "agent_research_session", None):
        sessions = [hybrid.agent_research_session]
    primary = next((
        item for item in sessions
        if str(getattr(item, "role", "")) == "primary_research_agent"
    ), sessions[0] if sessions else None)
    agenda = getattr(primary, "agenda", None)
    items = list(getattr(agenda, "items", ()) or ())
    selected_id = str(getattr(agenda, "selected_question_id", "") or "")
    selected = next((
        item for item in items
        if selected_id and str(getattr(item, "question_id", "")) == selected_id
    ), None)
    if selected is None:
        objective_tokens = _tokens(objective)
        ranked = sorted(
            items,
            key=lambda item: -len(
                objective_tokens
                & _tokens(
                    f"{getattr(item, 'title', '')} {getattr(item, 'objective', '')}"
                )
            ),
        )
        selected = ranked[0] if ranked and objective_tokens else None
    return getattr(selected, "answer", None) if selected is not None else None


def _tokens(value: str) -> set[str]:
    return {
        token for token in "".join(
            character if character.isalnum() else " "
            for character in str(value or "").casefold()
        ).split()
        if len(token) > 2
    }


def _research_outcome_diff(parent: Any | None, result: Any) -> ResearchOutcomeDiff:
    if parent is None:
        # A focused result can be loaded independently of its parent.  That is
        # not a decision change and should not crowd the compact result card.
        return ResearchOutcomeDiff(admitted_evidence_delta=0)
    before_evidence = set(
        getattr(getattr(parent, "evidence_graph", None), "admitted_evidence_ids", ()) or ()
    )
    after_evidence = set(
        getattr(getattr(result, "evidence_graph", None), "admitted_evidence_ids", ()) or ()
    )
    thesis_changed = _thesis_text(parent) != _thesis_text(result)
    valuation_changed = _valuation_value(parent) != _valuation_value(result)
    confidence_changed = _confidence(parent) != _confidence(result)
    action_status_changed = _action_statuses(parent) != _action_statuses(result)
    summaries: list[str] = []
    delta = len(after_evidence - before_evidence)
    if delta:
        summaries.append(f"{delta} newly admitted evidence item(s).")
    if thesis_changed:
        summaries.append("The thesis or decision summary changed.")
    if valuation_changed:
        summaries.append("The valuation output changed.")
    if confidence_changed:
        summaries.append("Research confidence changed.")
    if action_status_changed:
        summaries.append("At least one investigation action changed status.")
    if not summaries:
        summaries.append("No decision-facing conclusion changed in this pass.")
    return ResearchOutcomeDiff(
        admitted_evidence_delta=delta,
        thesis_changed=thesis_changed,
        valuation_changed=valuation_changed,
        confidence_changed=confidence_changed,
        action_status_changed=action_status_changed,
        change_summaries=tuple(summaries),
    )


def _stage_from_text(value: str) -> InvestigationStage:
    return resolve_investigation_stage(value)


def _completed_stages(events: Iterable[Any]) -> set[InvestigationStage]:
    completed = {InvestigationStage.SELECTED}
    for event in events:
        if str(getattr(event, "status", "") or "").casefold() not in {
            "completed", "passed", "closed", "admitted", "resolved",
        }:
            continue
        stage = _stage_from_text(
            str(getattr(event, "stage", "") or getattr(event, "summary", "") or "")
        )
        completed.add(stage)
    return completed


def _thesis_text(result: Any) -> str:
    brief = getattr(result, "ic_decision_brief", None)
    return str(
        getattr(brief, "thesis", "")
        or getattr(brief, "summary", "")
        or getattr(result, "executive_summary", "")
        or ""
    )


def _valuation_value(result: Any) -> str:
    valuation = getattr(result, "valuation", None)
    return repr((
        getattr(valuation, "probability_weighted_value", None),
        getattr(valuation, "expected_return", None),
        getattr(valuation, "base_case_value", None),
    ))


def _confidence(result: Any) -> str:
    brief = getattr(result, "ic_decision_brief", None)
    return str(
        getattr(brief, "confidence", "")
        or getattr(getattr(result, "valuation", None), "confidence", "")
        or ""
    )


def _action_statuses(result: Any) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (
            str(getattr(item, "semantic_key", "") or getattr(item, "action_id", "") or ""),
            str(getattr(item, "status", "") or ""),
        )
        for item in _canonical_actions(result)
    ))


def _canonical_actions(result: Any) -> tuple[Any, ...]:
    ticker = str(
        getattr(getattr(result, "identity", None), "ticker", "") or ""
    )
    return canonicalize_investigation_actions(
        ticker,
        list(getattr(result, "prioritized_research_queue", ()) or ()),
    ).actions
