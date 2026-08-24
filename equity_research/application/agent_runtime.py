from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Callable

from ..domain.agent_research import (
    AgentResearchBudget,
    AgentResearchSession,
    EvidenceCoveragePlan,
    ResearchAgenda,
    ResearchAgendaItem,
    ResearchAnswer,
    ResearchNotebookEntry,
    ResearchObservation,
    ResearchQuestionStatus,
    ResearchSessionStatus,
    ResearchToolCall,
)
from ..domain.research_intents import ResearchIntentResolver
from ..secret_safety import redact_sensitive_text
from .research_agenda import agenda_from_payload
from .research_tools import ResearchToolGateway


AGENT_RUNTIME_VERSION = "research-agent-runtime-v1"


def budget_for_research(
    *,
    depth: str,
    focused: bool,
) -> AgentResearchBudget:
    normalized = str(depth or "answer_oriented").casefold().replace("-", "_")
    maximum = normalized in {"maximum", "maximum_depth", "deep_initiation_maximum"}
    if maximum and focused:
        return AgentResearchBudget(
            budget_id="maximum-focused-v1",
            label="Maximum depth · focused investigation",
            max_elapsed_seconds=1_800,
            max_tool_actions=100,
            max_model_turns=20,
        )
    if maximum:
        return AgentResearchBudget(
            budget_id="maximum-full-v1",
            label="Maximum depth · full research",
            max_elapsed_seconds=5_400,
            max_tool_actions=300,
            max_model_turns=40,
        )
    if focused:
        return AgentResearchBudget(
            budget_id="answer-focused-v1",
            label="Answer-oriented · focused investigation",
            max_elapsed_seconds=900,
            max_tool_actions=40,
            max_model_turns=10,
        )
    return AgentResearchBudget(
        budget_id="answer-full-v1",
        label="Answer-oriented · full research",
        max_elapsed_seconds=2_700,
        max_tool_actions=120,
        max_model_turns=20,
    )


class ResearchAgentRuntime:
    """Provider-independent, bounded research loop over app-owned tools."""

    def __init__(
        self,
        gateway: ResearchToolGateway,
        *,
        budget: AgentResearchBudget,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.gateway = gateway
        self.budget = budget
        self.progress_callback = progress_callback
        self.cancel_event = cancel_event

    def run(
        self,
        *,
        provider: Any,
        agenda: ResearchAgenda,
        role: str,
        context_summary: dict[str, Any] | None = None,
        prior_artifact: dict[str, Any] | None = None,
    ) -> AgentResearchSession:
        context_summary = context_summary or {}
        prior_artifact = prior_artifact or {}
        started_at = _utc_now()
        started_monotonic = time.monotonic()
        session = AgentResearchSession(
            session_id=_stable_id("agent-session", agenda.agenda_id, role, started_at),
            ticker=agenda.ticker,
            role=role,
            provider=str(getattr(provider, "provider_name", "") or "model"),
            model=str(getattr(provider, "model", "") or "configured model"),
            status=ResearchSessionStatus.RUNNING.value,
            agenda=copy.deepcopy(agenda),
            budget=self.budget,
            started_at=started_at,
            current_stage="building research plan",
        )
        no_progress_turns = 0
        known_evidence_ids: set[str] = _context_evidence_ids(context_summary)
        previous_usage: dict[str, int] = {}
        accumulated_cost = 0.0

        for turn_number in range(1, self.budget.max_model_turns + 1):
            if self._cancelled():
                session.status = ResearchSessionStatus.CANCELLED.value
                session.stopping_reason = "cancelled"
                _cancel_provider(provider)
                break
            elapsed = time.monotonic() - started_monotonic
            if elapsed >= self.budget.max_elapsed_seconds:
                session.status = ResearchSessionStatus.PARTIAL.value
                session.stopping_reason = "time_limit"
                break
            if session.tool_actions >= self.budget.max_tool_actions:
                session.status = ResearchSessionStatus.PARTIAL.value
                session.stopping_reason = "tool_action_limit"
                break
            if (
                self.budget.max_cost_usd is not None
                and accumulated_cost >= self.budget.max_cost_usd
            ):
                session.status = ResearchSessionStatus.PARTIAL.value
                session.stopping_reason = "cost_limit"
                break

            session.elapsed_seconds = round(elapsed, 3)
            active_items = _active_items(session.agenda)
            if not active_items:
                session.status = ResearchSessionStatus.COMPLETED.value
                session.stopping_reason = "all_selected_questions_answered"
                break
            session.model_turns = turn_number
            session.current_stage = _current_activity(
                role, active_items[0], provider_name=session.provider
            )
            self._notify(session, active_items[0])
            prompt = self._prompt_pack(
                session,
                active_items=active_items,
                context_summary=context_summary,
                prior_artifact=prior_artifact,
            )
            try:
                response = provider.complete_json(prompt)
            except Exception as exc:
                session.status = (
                    ResearchSessionStatus.PARTIAL.value
                    if session.observations or session.answers
                    else ResearchSessionStatus.FAILED.value
                )
                session.stopping_reason = "provider_failure"
                session.data_gaps.append(
                    f"{role.replace('_', ' ').title()} provider failed: "
                    f"{_safe_text(redact_sensitive_text(exc), 500)}"
                )
                break

            current_usage = _usage(provider)
            previous_usage = _merge_usage(previous_usage, current_usage)
            cost = _optional_float(getattr(provider, "last_cost_usd", None))
            if cost is not None:
                accumulated_cost += cost

            updates = _list_of_dicts(response.get("question_updates"))
            notes = _list_of_dicts(response.get("notebook_entries"))
            actions = _list_of_dicts(response.get("next_actions"))
            additions = _list_of_dicts(response.get("agenda_additions"))
            progress_made = False

            for addition in additions[:6]:
                if _add_agenda_item(
                    session.agenda,
                    addition,
                    role=role,
                ):
                    progress_made = True

            for item in notes[:12]:
                entry = ResearchNotebookEntry(
                    entry_id=_stable_id(
                        "model-note", session.session_id, turn_number, item
                    ),
                    question_id=str(item.get("question_id") or active_items[0].question_id),
                    entry_type=str(item.get("entry_type") or "interpretation"),
                    summary=_safe_text(item.get("summary"), 2_000),
                    source_observation_ids=tuple(
                        _string_list(item.get("observation_ids"))
                    ),
                    created_at=_utc_now(),
                )
                if entry.summary:
                    session.notebook.append(entry)
                    progress_made = True

            for update in updates:
                if self._apply_question_update(
                    session,
                    update,
                    known_evidence_ids=known_evidence_ids,
                ):
                    progress_made = True

            remaining_actions = self.budget.max_tool_actions - session.tool_actions
            for action_index, action in enumerate(actions[: min(6, remaining_actions)]):
                question_id = str(
                    action.get("question_id")
                    or active_items[0].question_id
                )
                tool_name = str(action.get("tool_name") or "")
                if tool_name not in {
                    item["name"] for item in self.gateway.describe_tools()
                }:
                    session.data_gaps.append(
                        f"The model requested an unregistered research tool: {tool_name or 'missing tool name'}."
                    )
                    continue
                call = ResearchToolCall(
                    call_id=_stable_id(
                        "tool-call", session.session_id, turn_number,
                        action_index, tool_name, action.get("arguments") or {},
                    ),
                    tool_name=tool_name,
                    question_id=question_id,
                    arguments=dict(action.get("arguments") or {}),
                    objective=_safe_text(action.get("objective"), 1_000),
                    reason=_safe_text(action.get("reason"), 800),
                    created_at=_utc_now(),
                )
                observation = self.gateway.execute(call)
                session.observations.append(observation)
                session.tool_actions += 1
                known_evidence_ids.update(observation.evidence_ids)
                if observation.status in {"completed", "partial"}:
                    progress_made = True
                self._apply_gateway_answer(session, question_id)
                session.elapsed_seconds = round(
                    time.monotonic() - started_monotonic, 3
                )
                current_item = next((
                    item for item in session.agenda.items
                    if item.question_id == question_id
                ), active_items[0])
                self._notify(session, current_item)
                if self._cancelled():
                    break

            session.token_usage = previous_usage
            session.estimated_cost_usd = accumulated_cost or None
            if progress_made:
                no_progress_turns = 0
            else:
                no_progress_turns += 1

            completed = bool(response.get("completed"))
            if completed:
                unresolved = _active_items(session.agenda)
                session.status = (
                    ResearchSessionStatus.COMPLETED.value
                    if not unresolved else ResearchSessionStatus.PARTIAL.value
                )
                session.stopping_reason = str(
                    response.get("stopping_reason")
                    or "model_completed_research"
                )
                break
            if no_progress_turns >= 2:
                session.status = ResearchSessionStatus.PARTIAL.value
                session.stopping_reason = "no_material_progress"
                break
        else:
            session.status = ResearchSessionStatus.PARTIAL.value
            session.stopping_reason = "model_turn_limit"

        session.answers = [
            item.answer for item in session.agenda.items if item.answer is not None
        ]
        if session.status == ResearchSessionStatus.RUNNING.value:
            session.status = (
                ResearchSessionStatus.COMPLETED.value
                if not _active_items(session.agenda)
                else ResearchSessionStatus.PARTIAL.value
            )
            session.stopping_reason = (
                "all_selected_questions_answered"
                if session.status == ResearchSessionStatus.COMPLETED.value
                else "bounded_research_complete"
            )
        session.completed_at = _utc_now()
        session.elapsed_seconds = round(time.monotonic() - started_monotonic, 3)
        session.current_stage = "research answers ready"
        self._notify(session, None)
        return session

    def _prompt_pack(
        self,
        session: AgentResearchSession,
        *,
        active_items: list[ResearchAgendaItem],
        context_summary: dict[str, Any],
        prior_artifact: dict[str, Any],
    ) -> dict[str, Any]:
        observations = [
            {
                "observation_id": item.observation_id,
                "question_id": item.question_id,
                "tool": item.tool_name,
                "status": item.status,
                "summary": item.summary,
                "source_family": item.source_family,
                "evidence_ids": item.evidence_ids,
                "decision_eligible": item.decision_eligible,
                "content": _compact_content(item.content),
                "failure_code": item.failure_code,
            }
            for item in session.observations[-30:]
        ]
        return {
            "_output_schema": _agent_turn_schema(),
            "runtime_version": AGENT_RUNTIME_VERSION,
            "role": session.role,
            "ticker": session.ticker,
            "research_contract": {
                "objective": (
                    "Answer decision-relevant questions through iterative tool use. "
                    "Replan after source failure and actively seek contrary evidence."
                ),
                "evidence_rule": (
                    "Exploration may guide searches, but only IDs returned as admitted "
                    "and decision-eligible may support an answer. Never invent an ID."
                ),
                "blocking_rule": (
                    "Do not mark a question blocked after one failed connector. Change "
                    "the query, source family, document, or structured tool first."
                ),
                "reasoning_privacy": (
                    "Return concise decisions, evidence needs, and action rationale; "
                    "do not return private chain-of-thought."
                ),
                "completion_rule": (
                    "Use submit_research_answer for direct answers. Calibrate confidence "
                    "and identify the strongest limitation and remaining work."
                ),
            },
            "budget": asdict(session.budget),
            "usage": {
                "model_turns": session.model_turns,
                "tool_actions": session.tool_actions,
            },
            "source_capabilities": (
                self.gateway.execute(ResearchToolCall(
                    call_id=_stable_id("capabilities", session.session_id),
                    tool_name="list_source_capabilities",
                    question_id=active_items[0].question_id,
                    created_at=_utc_now(),
                )).content
                if session.model_turns == 1 else
                "Use list_source_capabilities when source availability affects the plan."
            ),
            "research_tools": self.gateway.describe_tools(),
            "agenda": [
                {
                    "question_id": item.question_id,
                    "domain": item.domain,
                    "title": item.title,
                    "objective": item.objective,
                    "decision_relevance": item.decision_relevance,
                    "priority": item.priority,
                    "materiality": item.materiality,
                    "status": item.status,
                    "confirming_evidence": item.confirming_evidence,
                    "contradicting_evidence": item.contradicting_evidence,
                    "suggested_source_families": item.source_families,
                    "analytical_tools": item.analytical_tools,
                    "dependencies": item.dependency_question_ids,
                    "answer": asdict(item.answer) if item.answer else None,
                }
                for item in session.agenda.items
            ],
            "active_questions": [item.question_id for item in active_items[:5]],
            "context_summary": _safe_mapping(context_summary, 25_000),
            "prior_artifact": _safe_mapping(prior_artifact, 15_000),
            "recent_observations": observations,
            "notebook_summary": [
                {
                    "question_id": item.question_id,
                    "entry_type": item.entry_type,
                    "summary": item.summary,
                    "observation_ids": list(item.source_observation_ids),
                }
                for item in session.notebook[-20:]
            ],
            "required_output": {
                "session_summary": "short progress summary",
                "next_actions": (
                    "0-6 registered tool calls. Use multiple source families or a "
                    "different tool after an unsuccessful attempt."
                ),
                "question_updates": "direct answer updates supported by admitted evidence",
                "agenda_additions": (
                    "material omitted questions only, each with a recorded reason"
                ),
                "notebook_entries": "provisional hypotheses or search decisions",
                "completed": "true only when the selected agenda is adequately answered or bounded",
                "stopping_reason": "controlled readable reason",
            },
        }

    def _apply_question_update(
        self,
        session: AgentResearchSession,
        update: dict[str, Any],
        *,
        known_evidence_ids: set[str],
    ) -> bool:
        question_id = str(update.get("question_id") or "")
        item = next(
            (value for value in session.agenda.items if value.question_id == question_id),
            None,
        )
        if item is None:
            return False
        status = str(update.get("status") or item.status)
        allowed = {value.value for value in ResearchQuestionStatus}
        if status not in allowed:
            status = ResearchQuestionStatus.PARTIALLY_ANSWERED.value
        evidence_ids = [
            value for value in _string_list(update.get("evidence_ids"))
            if value in known_evidence_ids
        ]
        contrary_ids = [
            value for value in _string_list(update.get("contrary_evidence_ids"))
            if value in known_evidence_ids
        ]
        answer_text = _safe_text(update.get("answer"), 5_000)
        if status == ResearchQuestionStatus.ANSWERED.value and not evidence_ids:
            status = ResearchQuestionStatus.PARTIALLY_ANSWERED.value
        if status in {
            ResearchQuestionStatus.BLOCKED_BY_SOURCE_ACCESS.value,
            ResearchQuestionStatus.BLOCKED_BY_PROVIDER_FAILURE.value,
        } and not _sufficient_failed_attempts(session, question_id):
            status = ResearchQuestionStatus.RESEARCHING.value
        item.status = status
        if answer_text or status not in {
            ResearchQuestionStatus.PLANNED.value,
            ResearchQuestionStatus.RESEARCHING.value,
        }:
            item.answer = ResearchAnswer(
                question_id=question_id,
                status=status,
                answer=answer_text or "Research remains incomplete.",
                confidence=str(update.get("confidence") or "Unknown"),
                confidence_reason=_safe_text(update.get("confidence_reason"), 1_000),
                mechanism=_safe_text(update.get("mechanism"), 2_000),
                evidence_ids=evidence_ids,
                contrary_evidence_ids=contrary_ids,
                key_findings=_string_list(update.get("key_findings"))[:10],
                strongest_limitation=_safe_text(update.get("strongest_limitation"), 1_500),
                remaining_work=_string_list(update.get("remaining_work"))[:10],
                changed_conclusions=_string_list(update.get("changed_conclusions"))[:10],
                intent_id=item.intent_id,
                evidence_hash=_stable_id(
                    "answer-evidence", *sorted((*evidence_ids, *contrary_ids))
                ) if evidence_ids or contrary_ids else "",
            )
        _update_coverage(item, evidence_ids, contrary_ids)
        return True

    def _apply_gateway_answer(
        self,
        session: AgentResearchSession,
        question_id: str,
    ) -> None:
        getter = getattr(self.gateway, "submitted_answer", None)
        if not callable(getter):
            return
        answer = getter(question_id)
        if not answer:
            return
        known = {
            evidence_id for observation in session.observations
            for evidence_id in observation.evidence_ids
        }
        self._apply_question_update(session, answer, known_evidence_ids=known)

    def _cancelled(self) -> bool:
        return bool(self.cancel_event and self.cancel_event.is_set())

    def _notify(
        self,
        session: AgentResearchSession,
        item: ResearchAgendaItem | None,
    ) -> None:
        if self.progress_callback is None:
            return
        answered = sum(
            value.status in {
                ResearchQuestionStatus.ANSWERED.value,
                ResearchQuestionStatus.PARTIALLY_ANSWERED.value,
            }
            for value in session.agenda.items
        )
        payload = {
            "status": "running" if session.status == ResearchSessionStatus.RUNNING.value else session.status,
            "current_stage": session.current_stage,
            "current_role": session.role,
            "research_question": item.title if item else "Research answers ready",
            "message": (
                f"{session.provider} is researching {item.title}"
                if item else
                f"{answered} of {len(session.agenda.items)} agenda questions have direct answers."
            ),
            "source_capabilities_used": sorted({
                observation.source_family
                for observation in session.observations if observation.source_family
            }),
            "evidence_admitted": len({
                evidence_id for observation in session.observations
                for evidence_id in observation.evidence_ids
            }),
            "questions_answered": answered,
            "questions_total": len(session.agenda.items),
            "tool_actions": session.tool_actions,
            "model_turns": session.model_turns,
            "elapsed_seconds": session.elapsed_seconds,
        }
        try:
            self.progress_callback(payload)
        except Exception:
            return


def agent_session_artifact_payload(session: AgentResearchSession) -> dict[str, Any]:
    return {
        "agent_runtime_version": AGENT_RUNTIME_VERSION,
        "agent_research_session": asdict(session),
        "research_agenda": asdict(session.agenda),
        "research_answers": [asdict(item) for item in session.answers],
        "summary": _session_summary(session),
        "evidence_ids": sorted({
            evidence_id for answer in session.answers
            for evidence_id in [*answer.evidence_ids, *answer.contrary_evidence_ids]
        }),
        "source_requests": [],
        "research_stopping_reason": session.stopping_reason,
    }


def session_from_payload(payload: dict[str, Any] | AgentResearchSession) -> AgentResearchSession:
    if isinstance(payload, AgentResearchSession):
        return payload
    agenda = agenda_from_payload(dict(payload.get("agenda") or {}))
    budget_payload = dict(payload.get("budget") or {})
    observations = [
        ResearchObservation(**dict(item))
        for item in payload.get("observations", [])
        if isinstance(item, dict)
    ]
    notebook = [
        ResearchNotebookEntry(
            **{
                **dict(item),
                "source_observation_ids": tuple(item.get("source_observation_ids") or ()),
            }
        )
        for item in payload.get("notebook", [])
        if isinstance(item, dict)
    ]
    answers = [
        ResearchAnswer(**dict(item))
        for item in payload.get("answers", [])
        if isinstance(item, dict)
    ]
    return AgentResearchSession(
        **{
            **{
                key: value for key, value in payload.items()
                if key not in {"agenda", "budget", "observations", "notebook", "answers"}
            },
            "agenda": agenda,
            "budget": AgentResearchBudget(**budget_payload),
            "observations": observations,
            "notebook": notebook,
            "answers": answers,
        }
    )


def _agent_turn_schema() -> dict[str, Any]:
    string_list = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "session_summary": {"type": "string"},
            "completed": {"type": "boolean"},
            "stopping_reason": {"type": "string"},
            "next_actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "question_id": {"type": "string"},
                        "arguments": {"type": "object", "additionalProperties": True},
                        "objective": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["tool_name", "question_id", "arguments"],
                    "additionalProperties": False,
                },
            },
            "question_updates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string"},
                        "status": {"type": "string"},
                        "answer": {"type": "string"},
                        "confidence": {"type": "string"},
                        "confidence_reason": {"type": "string"},
                        "mechanism": {"type": "string"},
                        "evidence_ids": string_list,
                        "contrary_evidence_ids": string_list,
                        "key_findings": string_list,
                        "strongest_limitation": {"type": "string"},
                        "remaining_work": string_list,
                        "changed_conclusions": string_list,
                    },
                    "required": ["question_id", "status", "answer", "confidence", "evidence_ids"],
                    "additionalProperties": False,
                },
            },
            "agenda_additions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "objective": {"type": "string"},
                        "domain": {"type": "string"},
                        "decision_relevance": {"type": "string"},
                        "materiality": {"type": "string"},
                        "priority": {"type": "integer"},
                        "source_families": string_list,
                        "analytical_tools": string_list,
                        "confirming_evidence": string_list,
                        "contradicting_evidence": string_list,
                        "selection_reason": {"type": "string"},
                    },
                    "required": [
                        "title", "objective", "decision_relevance",
                        "materiality", "selection_reason"
                    ],
                    "additionalProperties": False,
                },
            },
            "notebook_entries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_id": {"type": "string"},
                        "entry_type": {"type": "string"},
                        "summary": {"type": "string"},
                        "observation_ids": string_list,
                    },
                    "required": ["question_id", "entry_type", "summary"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["session_summary", "completed", "stopping_reason", "next_actions", "question_updates", "notebook_entries"],
        "additionalProperties": False,
    }


def _active_items(agenda: ResearchAgenda) -> list[ResearchAgendaItem]:
    terminal = {
        ResearchQuestionStatus.ANSWERED.value,
        ResearchQuestionStatus.PARTIALLY_ANSWERED.value,
        ResearchQuestionStatus.BLOCKED_BY_SOURCE_ACCESS.value,
        ResearchQuestionStatus.BLOCKED_BY_PROVIDER_FAILURE.value,
        ResearchQuestionStatus.CANCELLED.value,
    }
    return sorted(
        [item for item in agenda.items if item.status not in terminal],
        key=lambda item: (-item.priority, item.question_id),
    )


def _context_evidence_ids(context_summary: dict[str, Any]) -> set[str]:
    evidence_ids: set[str] = set()
    for key in ("evidence_metadata", "evidence_catalog"):
        values = context_summary.get(key) or []
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            evidence_id = str(
                item.get("evidence_id") or item.get("source_id") or ""
            )
            # Catalog metadata is useful for planning, but it is not itself an
            # admission decision.  Only an explicit eligibility marker may
            # promote an ID from these compact records into the set that a
            # model answer is allowed to cite.
            if evidence_id and item.get("claim_eligible") is True:
                evidence_ids.add(evidence_id)
    evidence_ids.update(
        _string_list(context_summary.get("claim_eligible_evidence_ids"))
    )
    return evidence_ids


def _add_agenda_item(
    agenda: ResearchAgenda,
    payload: dict[str, Any],
    *,
    role: str,
) -> bool:
    objective = _safe_text(payload.get("objective"), 2_000)
    reason = _safe_text(payload.get("selection_reason"), 1_000)
    if not objective or not reason:
        return False
    resolver = ResearchIntentResolver()
    agenda_security_key = str(getattr(agenda, "security_key", "") or "")
    agenda_cik = (
        agenda_security_key.split(":", 1)[1]
        if agenda_security_key.startswith("cik:") else ""
    )
    agenda_exchange = (
        agenda_security_key.split(":", 2)[1]
        if agenda_security_key.startswith("listing:") else ""
    )
    mapped = resolver.resolve_legacy(
        ticker=agenda.ticker,
        cik=agenda_cik,
        exchange=agenda_exchange,
        question_key=str(payload.get("question_key") or ""),
        mechanism_key=str(payload.get("mechanism_key") or ""),
        target_type=str(payload.get("target_type") or ""),
        target_id=str(payload.get("target_id") or ""),
        period_scope=str(payload.get("period_scope") or ""),
        event_id=str(payload.get("event_id") or ""),
        decision_dependency=str(payload.get("decision_dependency") or ""),
        title=str(payload.get("title") or objective),
        objective=objective,
        materiality=str(payload.get("materiality") or "High"),
        origin_refs=(f"{role}:model_addition",),
    )
    research_intent = mapped.intent
    semantic = " ".join(objective.casefold().split())
    if any(
        (research_intent is not None and item.intent_id == research_intent.intent_id)
        or " ".join(item.objective.casefold().split()) == semantic
        for item in agenda.items
    ):
        return False
    question_id = _stable_id(
        "model-added-question", agenda.agenda_id, role, objective
    )
    agenda.items.append(ResearchAgendaItem(
        question_id=question_id,
        question_key=(
            research_intent.question_key
            if research_intent is not None
            else f"model_{question_id}"
        ),
        domain=str(payload.get("domain") or "Model-added research"),
        title=_safe_text(payload.get("title") or objective, 500),
        objective=objective,
        decision_relevance=_safe_text(
            payload.get("decision_relevance"), 1_000
        ),
        materiality=str(payload.get("materiality") or "High"),
        priority=max(1, min(100, _safe_integer(payload.get("priority"), 75))),
        source_families=_string_list(payload.get("source_families")),
        analytical_tools=_string_list(payload.get("analytical_tools")),
        confirming_evidence=_string_list(payload.get("confirming_evidence")),
        contradicting_evidence=_string_list(
            payload.get("contradicting_evidence")
        ),
        selection_reason=f"{role}: {reason}",
        coverage=EvidenceCoveragePlan(
            confirming_needed=_string_list(payload.get("confirming_evidence")),
            contradicting_needed=_string_list(
                payload.get("contradicting_evidence")
            ),
        ),
        intent_id=(research_intent.intent_id if research_intent else ""),
        mechanism_key=(research_intent.mechanism_key if research_intent else ""),
        target_type=(research_intent.target_type if research_intent else "issuer"),
        target_id=(research_intent.target_id if research_intent else agenda.ticker),
        period_scope=(research_intent.period_scope if research_intent else "current_research_cutoff"),
        event_id=(research_intent.event_id if research_intent else ""),
        decision_dependency=(research_intent.decision_dependency if research_intent else "decision_quality"),
    ))
    return True


def _current_activity(
    role: str,
    item: ResearchAgendaItem,
    *,
    provider_name: str = "",
) -> str:
    provider = str(provider_name or "").casefold()
    actor = (
        "Codex"
        if "codex" in provider
        else "DeepSeek"
        if "deepseek" in provider
        else str(provider_name).replace("_", " ").title()
        if provider_name and provider_name != "model"
        else "Independent reviewer"
        if "audit" in role or "scout" in role or "closure" in role
        else "Primary researcher"
    )
    return f"{actor} · {item.domain}"


def _safe_integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _sufficient_failed_attempts(
    session: AgentResearchSession,
    question_id: str,
) -> bool:
    failures = [
        item for item in session.observations
        if item.question_id == question_id
        and item.status in {"failed", "no_result", "partial"}
    ]
    source_families = {
        family.strip() for item in failures
        for family in item.source_family.split(",") if family.strip()
    }
    tools = {item.tool_name for item in failures}
    return len(source_families) >= 2 or len(tools) >= 2 or len(failures) >= 3


def _update_coverage(
    item: ResearchAgendaItem,
    evidence_ids: list[str],
    contrary_ids: list[str],
) -> None:
    item.coverage.confirming_covered = list(dict.fromkeys([
        *item.coverage.confirming_covered, *evidence_ids,
    ]))
    item.coverage.contradicting_covered = list(dict.fromkeys([
        *item.coverage.contradicting_covered, *contrary_ids,
    ]))
    total_needs = max(
        1,
        len(item.coverage.confirming_needed)
        + len(item.coverage.contradicting_needed),
    )
    covered = min(
        total_needs,
        len(item.coverage.confirming_covered)
        + len(item.coverage.contradicting_covered),
    )
    item.coverage.coverage_score = round(100 * covered / total_needs)


def _session_summary(session: AgentResearchSession) -> str:
    answered = sum(
        item.status == ResearchQuestionStatus.ANSWERED.value
        for item in session.agenda.items
    )
    partial = sum(
        item.status == ResearchQuestionStatus.PARTIALLY_ANSWERED.value
        for item in session.agenda.items
    )
    return (
        f"Agent-led research answered {answered} question(s) and partially "
        f"answered {partial} of {len(session.agenda.items)} selected agenda items."
    )


def _usage(provider: Any) -> dict[str, int]:
    value = getattr(provider, "last_usage", {}) or {}
    return {
        str(key): int(amount)
        for key, amount in dict(value).items()
        if isinstance(amount, (int, float))
    }


def _merge_usage(current: dict[str, int], latest: dict[str, int]) -> dict[str, int]:
    return {
        key: current.get(key, 0) + value
        for key, value in latest.items()
    } | {
        key: value for key, value in current.items() if key not in latest
    }


def _cancel_provider(provider: Any) -> None:
    cancel = getattr(provider, "cancel", None)
    if callable(cancel):
        try:
            cancel()
        except Exception:
            pass


def _compact_content(content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    used = 0
    for item in content[:20]:
        safe = _safe_mapping(item, 4_000)
        encoded = json.dumps(safe, ensure_ascii=True, default=str)
        if used + len(encoded) > 20_000:
            break
        output.append(safe)
        used += len(encoded)
    return output


def _safe_mapping(value: Any, limit: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    encoded = json.dumps(value, ensure_ascii=True, default=str)
    if len(encoded) <= limit:
        return dict(value)
    return {"summary": encoded[:limit], "truncated": True}


def _safe_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _stable_id(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
