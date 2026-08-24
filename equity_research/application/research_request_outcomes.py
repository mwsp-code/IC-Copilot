from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Protocol

from ..domain.financial_facts import CashConversionRecurrenceBridge
from .cash_conversion import validate_cash_conversion_recurrence_bridge


class ResearchRequestAnswerStatus(str, Enum):
    ANSWERED = "answered"
    PARTIALLY_ANSWERED = "partially_answered"
    UNANSWERED_AFTER_ATTEMPT = "unanswered_after_attempt"
    PLANNED = "planned"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ResearchRequestOutcome:
    request_id: str
    status: str
    answer: str
    evidence_found: tuple[str, ...] = ()
    evidence_missing: tuple[str, ...] = ()
    next_step: str = ""
    attempted_connectors: tuple[str, ...] = ()
    admitted_evidence_count: int = 0
    handler_id: str = "generic"


@dataclass(frozen=True)
class ResearchIntentAnswer:
    intent_id: str
    question: str
    status: str
    direct_answer: str
    confidence: str = "Unknown"
    coverage: str = "Unknown"
    evidence_found: tuple[str, ...] = ()
    evidence_missing: tuple[str, ...] = ()
    next_step: str = ""
    request_ids: tuple[str, ...] = ()
    attempted_connectors: tuple[str, ...] = ()
    admitted_evidence_count: int = 0
    closure_state: str = "not_reviewed"
    answer_basis: str = "research_request_outcomes"
    bridge: CashConversionRecurrenceBridge | None = None


@dataclass(frozen=True)
class InvestigationAnswerState:
    """The one truthful answer state for a displayed research intent.

    Run completion is intentionally separate from question coverage and QA.
    A completed worker may still have an unavailable answer or QA pending.
    """

    intent_id: str
    question: str
    answer_status: str
    run_status: str
    qa_state: str
    direct_answer: str
    confidence: str = "Unknown"
    coverage: str = "Unknown"
    answer_basis: str = ""
    evidence_hash: str = ""
    data_cutoff: str = ""
    analysis_version: str = ""
    remaining_action: str = ""
    evidence_found: tuple[str, ...] = ()
    evidence_missing: tuple[str, ...] = ()
    request_ids: tuple[str, ...] = ()
    attempted_connectors: tuple[str, ...] = ()
    admitted_evidence_count: int = 0
    bridge: CashConversionRecurrenceBridge | None = None


class ResearchIntentAnswerResolver:
    """Resolve one investment question across every contributing subsystem."""

    _RANK = {
        ResearchRequestAnswerStatus.ANSWERED.value: 5,
        ResearchRequestAnswerStatus.PARTIALLY_ANSWERED.value: 4,
        ResearchRequestAnswerStatus.UNANSWERED_AFTER_ATTEMPT.value: 3,
        ResearchRequestAnswerStatus.BLOCKED.value: 2,
        ResearchRequestAnswerStatus.PLANNED.value: 1,
    }

    def resolve(
        self,
        result: Any,
        requests: Iterable[Any],
        *,
        intent_id: str = "",
        question: str = "",
    ) -> ResearchIntentAnswer:
        state = self.resolve_state(
            result,
            requests,
            intent_id=intent_id,
            question=question,
        )
        return ResearchIntentAnswer(
            intent_id=state.intent_id,
            question=state.question,
            status=state.answer_status,
            direct_answer=state.direct_answer,
            confidence=state.confidence,
            coverage=state.coverage,
            evidence_found=state.evidence_found,
            evidence_missing=state.evidence_missing,
            next_step=state.remaining_action,
            request_ids=state.request_ids,
            attempted_connectors=state.attempted_connectors,
            admitted_evidence_count=state.admitted_evidence_count,
            closure_state=state.qa_state,
            answer_basis=state.answer_basis,
            bridge=state.bridge,
        )

    def resolve_state(
        self,
        result: Any,
        requests: Iterable[Any],
        *,
        intent_id: str = "",
        question: str = "",
        run_status: str = "completed",
        expected_cutoff: str = "",
    ) -> InvestigationAnswerState:
        """Resolve only records explicitly linked to the chosen intent.

        Legacy request wording may help a broad inventory, but it must never
        silently become the answer for a focused investigation.  This removes
        the historical ``trail_requests[0]`` / generic-request fallback.
        """
        rows = tuple(requests)
        related = tuple(
            item for item in rows
            if (
                not intent_id
                or str(getattr(item, "agenda_item_id", "") or "") == intent_id
                or str(getattr(item, "intent_id", "") or "") == intent_id
            )
        )
        bridge = _current_cash_conversion_recurrence(result)
        bridge_intent_id = str(getattr(bridge, "intent_id", "") or "")
        cash_intent = _is_cash_conversion_intent(intent_id, question, related)
        outcomes = tuple(
            ResearchRequestOutcomeService().evaluate(result, item)
            for item in related
        )
        connectors = tuple(dict.fromkeys(
            value for outcome in outcomes for value in outcome.attempted_connectors
        ))
        request_ids = tuple(dict.fromkeys(
            outcome.request_id for outcome in outcomes if outcome.request_id
        ))
        admitted_count = sum(outcome.admitted_evidence_count for outcome in outcomes)
        if cash_intent and bridge is not None and (
            not intent_id
            or bridge_intent_id == intent_id
            or intent_id == "cash_generation_capital_allocation:recurring_cash_conversion"
        ):
            validation = validate_cash_conversion_recurrence_bridge(
                bridge,
                expected_cutoff=expected_cutoff,
            )
            assessment = bridge.assessment
            qa_state = _qa_state(bridge.qa_freshness)
            missing = tuple(dict.fromkeys((*assessment.material_gaps, *validation.reasons)))
            if qa_state == "pending_due_to_new_evidence":
                missing = (*missing, "Independent QA is pending because the evidence changed.")
            elif qa_state == "not_run":
                missing = (*missing, "Independent QA has not yet closed the refreshed answer.")
            status = "answered" if validation.valid else "partially_answered"
            return InvestigationAnswerState(
                intent_id=bridge.intent_id or intent_id,
                question=question,
                answer_status=status,
                run_status=run_status,
                qa_state=qa_state,
                direct_answer=assessment.direct_answer,
                confidence=assessment.confidence,
                coverage=(
                    f"{assessment.comparison_periods} comparable TTM period(s); "
                    f"{assessment.coverage_ratio * 100:.0f}% material component coverage"
                ),
                evidence_found=(assessment.strongest_support,),
                evidence_missing=tuple(dict.fromkeys(missing)),
                remaining_action=(
                    "Run Independent Research QA for the refreshed answer."
                    if qa_state == "pending_due_to_new_evidence"
                    else (
                        "Review the named bridge limitation before retrying."
                        if not validation.valid else ""
                    )
                ),
                request_ids=request_ids,
                attempted_connectors=connectors,
                admitted_evidence_count=max(
                    admitted_count,
                    len({value for period in bridge.periods for value in period.evidence_ids}),
                ),
                answer_basis=bridge.answer_basis,
                evidence_hash=str(getattr(bridge, "evidence_hash", "") or ""),
                data_cutoff=str(getattr(bridge, "data_cutoff", "") or ""),
                analysis_version=str(getattr(bridge, "analysis_version", "") or ""),
                bridge=bridge,
            )
        if not outcomes:
            reason = (
                "No validated answer is linked to this exact investigation intent."
                if intent_id else
                "No Research Request could be linked to this investigation."
            )
            return InvestigationAnswerState(
                intent_id=intent_id,
                question=question,
                answer_status="not_researched",
                run_status=run_status,
                qa_state="not_run",
                direct_answer=reason,
                remaining_action="Open Evidence to review the recorded source limitation.",
            )
        best = max(outcomes, key=lambda item: (
            self._RANK.get(item.status, 0),
            item.admitted_evidence_count,
            len(item.evidence_found),
        ))
        qa_state = _qa_state(_result_qa_freshness(result))
        answer_status = {
            ResearchRequestAnswerStatus.ANSWERED.value: "answered",
            ResearchRequestAnswerStatus.PARTIALLY_ANSWERED.value: "partially_answered",
            ResearchRequestAnswerStatus.BLOCKED.value: "blocked",
            ResearchRequestAnswerStatus.UNANSWERED_AFTER_ATTEMPT.value: "blocked",
            ResearchRequestAnswerStatus.PLANNED.value: "not_researched",
        }.get(best.status, "not_researched")
        return InvestigationAnswerState(
            intent_id=intent_id,
            question=question,
            answer_status=answer_status,
            run_status=run_status,
            qa_state=qa_state,
            direct_answer=best.answer,
            confidence="Unknown",
            coverage=f"{len(related)} related Research Request(s)",
            evidence_found=tuple(dict.fromkeys(
                value for outcome in outcomes for value in outcome.evidence_found
            )),
            evidence_missing=tuple(dict.fromkeys(
                value for outcome in outcomes for value in outcome.evidence_missing
            )),
            remaining_action=best.next_step,
            request_ids=request_ids,
            attempted_connectors=connectors,
            admitted_evidence_count=admitted_count,
        )


class ResearchRequestOutcomeHandler(Protocol):
    handler_id: str

    def matches(self, request: Any) -> bool: ...

    def evaluate(self, result: Any, request: Any) -> ResearchRequestOutcome: ...


class CashConversionOutcomeHandler:
    handler_id = "cash_conversion_v1"

    def matches(self, request: Any) -> bool:
        objective = str(
            getattr(request, "objective", "")
            or getattr(request, "question", "")
            or ""
        )
        return research_request_topic(objective) == "cash_conversion_analysis"

    def evaluate(self, result: Any, request: Any) -> ResearchRequestOutcome:
        recurrence = _current_cash_conversion_recurrence(result)
        if recurrence is not None:
            assessment = recurrence.assessment
            missing = tuple(assessment.material_gaps)
            if recurrence.qa_freshness in {"pending", "stale", "not_reviewed"}:
                missing = (*missing, "Independent QA has not yet closed the refreshed answer.")
            return ResearchRequestOutcome(
                request_id=_request_id(request),
                status=(
                    ResearchRequestAnswerStatus.ANSWERED.value
                    if recurrence.status == "answered" and not assessment.material_gaps
                    else ResearchRequestAnswerStatus.PARTIALLY_ANSWERED.value
                ),
                answer=assessment.direct_answer,
                evidence_found=(assessment.strongest_support,),
                evidence_missing=missing,
                next_step=(
                    "Run Independent Research QA for the refreshed answer."
                    if recurrence.qa_freshness in {"pending", "stale", "not_reviewed"}
                    else "No material deterministic cash-conversion work remains."
                ),
                attempted_connectors=_attempted_connectors(request),
                admitted_evidence_count=len({
                    value for period in recurrence.periods for value in period.evidence_ids
                }),
                handler_id="cash_conversion_recurrence_v1",
            )
        bridge = _cash_conversion_bridge(result)
        components = dict(bridge.get("components", {}))
        gaps = list(bridge.get("data_gaps", ()))
        period = str(bridge.get("period", "") or "")
        unit = str(bridge.get("unit", "USD") or "USD")
        period_aligned = bool(bridge.get("period_aligned", True))
        ocf = _number(components.get("operating_cash_flow"))
        net_income = _number(components.get("net_income"))
        capex = _number(components.get("capital_expenditure"))
        dividends = _number(components.get("dividends_paid"))
        repurchases = _number(components.get("share_repurchases"))
        fcf = _number(bridge.get("free_cash_flow"))
        if fcf is None and period_aligned and ocf is not None and capex is not None:
            fcf = ocf - abs(capex)
        conversion = _number(bridge.get("operating_cash_flow_to_net_income"))
        if conversion is None and period_aligned and ocf is not None and net_income:
            conversion = ocf / net_income
        residual = _number(bridge.get("cash_after_dividends_and_repurchases"))
        if (
            residual is None
            and period_aligned
            and fcf is not None
            and dividends is not None
            and repurchases is not None
        ):
            residual = fcf - abs(dividends) - abs(repurchases)

        evidence_found: list[str] = []
        suffix = f" through {period}" if period else ""
        if ocf is not None:
            evidence_found.append(
                f"Trailing operating cash flow{suffix}: {_format_amount(ocf, unit)}."
            )
        if net_income is not None:
            evidence_found.append(
                f"Trailing net income{suffix}: {_format_amount(net_income, unit)}."
            )
        if fcf is not None:
            evidence_found.append(
                f"Free cash flow after capex{suffix}: {_format_amount(fcf, unit)}."
            )
        if conversion is not None:
            evidence_found.append(
                "Operating cash flow was "
                f"{conversion * 100:.1f}% of trailing net income."
            )
        if residual is not None:
            evidence_found.append(
                "Cash remaining after dividends and repurchases: "
                f"{_format_amount(residual, unit)}."
            )

        if (
            period_aligned
            and ocf is not None
            and net_income is not None
            and capex is not None
        ):
            if not gaps:
                status = ResearchRequestAnswerStatus.ANSWERED.value
                answer = (
                    "Answered. The period-aligned cash-conversion bridge and "
                    "comparable history satisfy the request's completion rule."
                )
            else:
                status = ResearchRequestAnswerStatus.PARTIALLY_ANSWERED.value
                strength = (
                    "strong"
                    if conversion is not None and conversion >= 1 and fcf is not None and fcf > 0
                    else "mixed"
                )
                answer = (
                    f"Partially answered. Current trailing cash conversion appears {strength}: "
                    f"operating cash flow was {_format_amount(ocf, unit)}"
                    + (
                        f", or {conversion * 100:.1f}% of net income"
                        if conversion is not None else ""
                    )
                    + (
                        f", and free cash flow after capex was {_format_amount(fcf, unit)}"
                        if fcf is not None else ""
                    )
                    + ". Recurrence is not yet established because the complete "
                    "same-duration comparison and component bridge are incomplete."
                )
        else:
            attempted = bool(_attempted_connectors(request))
            status = (
                ResearchRequestAnswerStatus.UNANSWERED_AFTER_ATTEMPT.value
                if attempted else ResearchRequestAnswerStatus.PLANNED.value
            )
            answer = (
                "The connector attempt did not produce a complete period-aligned "
                "cash-conversion bridge. No definitive cash-conversion conclusion "
                "was admitted."
                if attempted else
                "This cash-conversion request is planned but has not yet produced "
                "a period-aligned accounting bridge."
            )
            for label, value in (
                ("operating cash flow", ocf),
                ("net income", net_income),
                ("capital expenditure", capex),
            ):
                if value is None:
                    gaps.append(f"Validated trailing {label} is missing.")
            if not period_aligned:
                gaps.append(
                    "Operating cash flow, net income, and capex do not share the same trailing period."
                )

        gaps = list(dict.fromkeys(gaps))
        return ResearchRequestOutcome(
            request_id=_request_id(request),
            status=status,
            answer=answer,
            evidence_found=tuple(evidence_found),
            evidence_missing=tuple(gaps),
            next_step=(
                "Retrieve a prior same-duration cash-flow statement and align "
                "working-capital movements, cash taxes, debt cash flows, dividends, "
                "and repurchases; then revalidate the bridge and rerun QA closure."
                if gaps else "No additional cash-conversion work is required."
            ),
            attempted_connectors=_attempted_connectors(request),
            admitted_evidence_count=len(
                list(getattr(request, "admitted_evidence_ids", ()) or ())
            ),
            handler_id=self.handler_id,
        )


class ResearchRequestOutcomeService:
    """Build decision-readable outcomes from admitted deterministic artifacts."""

    def __init__(
        self,
        handlers: Iterable[ResearchRequestOutcomeHandler] = (),
    ) -> None:
        self.handlers = tuple(handlers) or (CashConversionOutcomeHandler(),)

    def evaluate(self, result: Any, request: Any) -> ResearchRequestOutcome:
        explicit = _agent_answer_outcome(request)
        if explicit is not None:
            return explicit
        for handler in self.handlers:
            if handler.matches(request):
                return handler.evaluate(result, request)
        return _generic_outcome(request)


def build_research_request_outcome(
    result: Any,
    request: Any,
) -> ResearchRequestOutcome:
    return ResearchRequestOutcomeService().evaluate(result, request)


def _current_cash_conversion_recurrence(
    result: Any,
) -> CashConversionRecurrenceBridge | None:
    value = getattr(result, "cash_conversion_recurrence", None)
    if isinstance(value, CashConversionRecurrenceBridge):
        return value
    hybrid = getattr(result, "hybrid_research", None)
    value = getattr(hybrid, "cash_conversion_recurrence", None)
    return value if isinstance(value, CashConversionRecurrenceBridge) else None


def _is_cash_conversion_intent(
    intent_id: str,
    question: str,
    related: Iterable[Any],
) -> bool:
    """Allow only a stable intent or a clear focused cash question."""

    if intent_id == "cash_generation_capital_allocation:recurring_cash_conversion":
        return True
    if intent_id:
        return (
            "cash_generation_capital_allocation" in intent_id
            and "cash_conversion" in intent_id
        ) or research_request_topic(question) == "cash_conversion_analysis"
    if research_request_topic(question) == "cash_conversion_analysis":
        return True
    return any(
        research_request_topic(str(getattr(item, "objective", "") or ""))
        == "cash_conversion_analysis"
        for item in related
    )


def _qa_state(value: str) -> str:
    normalized = str(value or "").casefold()
    if normalized in {"closed", "passed"}:
        return "closed"
    if normalized in {"closed_with_residual_risk", "residual_risk"}:
        return "closed_with_residual_risk"
    if normalized in {"pending", "stale", "evidence_refreshed_qa_pending"}:
        return "pending_due_to_new_evidence"
    if normalized in {"running", "in_progress", "active"}:
        return "in_progress"
    if normalized in {"failed", "failed_material_findings_unaddressed"}:
        return "failed"
    if normalized in {"indeterminate", "indeterminate_provider_or_evidence_failure"}:
        return "indeterminate"
    if normalized in {"not_requested", "disabled", "optional"}:
        return "not_requested"
    return "not_run"


def _result_qa_freshness(result: Any) -> str:
    qa = getattr(result, "independent_research_qa", None)
    closure = getattr(qa, "closure_result", None)
    return str(
        getattr(closure, "state", "")
        or getattr(qa, "status", "")
        or "not_run"
    )


def research_request_topic(objective: str) -> str:
    text = str(objective or "").casefold().replace("-", " ")
    if any(token in text for token in (
        "cash conversion",
        "free cash flow",
        "operating cash flow",
        "cash generation",
    )):
        return "cash_conversion_analysis"
    cash_components = sum(token in text for token in (
        "working capital",
        "capital expenditure",
        "capex",
        "cash taxes",
        "financing",
        "capital returns",
        "repurchases",
        "dividends",
    ))
    if ("cash flow" in text or "cash generation" in text) and cash_components >= 2:
        return "cash_conversion_analysis"
    if any(token in text for token in ("consensus", "estimate revision")):
        return "consensus_revision_capture"
    if any(token in text for token in ("gross margin", "pricing", "volume", "mix")):
        return "operating_driver_analysis"
    if any(token in text for token in ("event window", "beta adjusted", "market reaction")):
        return "market_event_attribution"
    return "primary_source_validation"


def _generic_outcome(request: Any) -> ResearchRequestOutcome:
    attempted = _attempted_connectors(request)
    admitted = tuple(getattr(request, "admitted_evidence_ids", ()) or ())
    request_status = str(getattr(request, "status", "planned") or "planned").casefold()
    resolution = str(getattr(request, "resolution_summary", "") or "").strip()
    if request_status in {"resolved", "closed", "completed"} and admitted:
        status = ResearchRequestAnswerStatus.ANSWERED.value
        answer = resolution or "Admitted evidence satisfied this Research Request."
    elif admitted:
        status = ResearchRequestAnswerStatus.PARTIALLY_ANSWERED.value
        answer = resolution or (
            "Admitted evidence addressed part of this request, but its stopping rule "
            "was not fully satisfied."
        )
    elif attempted:
        status = ResearchRequestAnswerStatus.UNANSWERED_AFTER_ATTEMPT.value
        answer = (
            "Registered sources were attempted, but no evidence was admitted against "
            "this request. The question remains unanswered."
        )
    elif request_status in {"blocked", "failed", "unavailable", "retrieval_failed"}:
        status = ResearchRequestAnswerStatus.BLOCKED.value
        answer = resolution or "The requested evidence is currently unavailable."
    else:
        status = ResearchRequestAnswerStatus.PLANNED.value
        answer = "This request is planned and has not been attempted."
    return ResearchRequestOutcome(
        request_id=_request_id(request),
        status=status,
        answer=answer,
        evidence_found=(
            (f"{len(admitted)} evidence item(s) were admitted for this request.",)
            if admitted else ()
        ),
        evidence_missing=(
            ("No request-linked evidence passed admission.",)
            if attempted and not admitted else ()
        ),
        next_step=(
            "Review the stopping rule and retry an eligible registered source."
            if attempted and not admitted else ""
        ),
        attempted_connectors=attempted,
        admitted_evidence_count=len(admitted),
    )


def _agent_answer_outcome(request: Any) -> ResearchRequestOutcome | None:
    answer = str(getattr(request, "answer", "") or "").strip()
    status = str(
        getattr(request, "answer_status", "")
        or getattr(request, "status", "")
        or ""
    ).casefold()
    if not answer and status in {"", "planned", "researching"}:
        return None
    normalized_status = {
        "answered": ResearchRequestAnswerStatus.ANSWERED.value,
        "partially_answered": ResearchRequestAnswerStatus.PARTIALLY_ANSWERED.value,
        "blocked_by_source_access": ResearchRequestAnswerStatus.BLOCKED.value,
        "blocked_by_provider_failure": ResearchRequestAnswerStatus.BLOCKED.value,
        "cancelled": ResearchRequestAnswerStatus.BLOCKED.value,
    }.get(status)
    if normalized_status is None:
        return None
    admitted = tuple(getattr(request, "admitted_evidence_ids", ()) or ())
    coverage = dict(getattr(request, "evidence_coverage", {}) or {})
    key_findings = tuple(
        str(value) for value in (
            getattr(request, "key_findings", ()) or ()
        ) if value
    )
    remaining_work = tuple(
        str(value) for value in (
            getattr(request, "remaining_work", ()) or ()
        ) if value
    )
    gaps = tuple(
        str(value) for value in (
            coverage.get("material_gaps")
            or coverage.get("unresolved_items")
            or ()
        ) if value
    )
    attempted = _attempted_connectors(request)
    blocking_reason = str(
        getattr(request, "blocking_reason", "") or ""
    ).strip()
    return ResearchRequestOutcome(
        request_id=_request_id(request),
        status=normalized_status,
        answer=answer or (
            blocking_reason
            or "The research question was blocked before a direct answer could be established."
        ),
        evidence_found=(
            key_findings
            or (
                (f"{len(admitted)} admitted evidence item(s) support this answer.",)
                if admitted else ()
            )
        ),
        evidence_missing=(
            remaining_work
            or gaps
            or ((blocking_reason,) if blocking_reason else ())
        ),
        next_step=(
            "No material follow-up is required for this research question."
            if normalized_status == ResearchRequestAnswerStatus.ANSWERED.value
            and not gaps and not remaining_work else
            "Continue with the highest-information-gain unresolved item shown below."
            if gaps or remaining_work else
            "Review the named source limitation before retrying."
        ),
        attempted_connectors=attempted,
        admitted_evidence_count=len(admitted),
        handler_id="agent_research_answer_v1",
    )


def _cash_conversion_bridge(result: Any) -> dict[str, Any]:
    path = next((
        item for item in list(
            getattr(result, "historical_financial_paths", ()) or ()
        )
        if str(getattr(item, "metric", "") or "").casefold()
        == "cash conversion bridge ttm"
    ), None)
    if path is not None:
        observation = next(iter(list(getattr(path, "observations", ()) or ())), {})
        return {
            **dict(observation or {}),
            "data_gaps": list(getattr(path, "data_gaps", ()) or ()),
        }

    metrics = list(getattr(result, "metrics", ()) or ())
    values = {
        "operating_cash_flow": _latest_metric(metrics, (
            "operating cash flow", "net cash provided by operating activities",
        )),
        "net_income": _latest_metric(metrics, ("net income", "net earnings")),
        "capital_expenditure": _latest_metric(metrics, (
            "capital expenditure", "payments to acquire property plant and equipment",
        )),
        "dividends_paid": _latest_metric(metrics, ("dividends paid", "payments of dividends")),
        "share_repurchases": _latest_metric(metrics, ("repurchases", "repurchase of common stock")),
        "cash_taxes": _latest_metric(metrics, ("cash paid for income taxes", "income taxes paid")),
        "debt_issued": _latest_metric(metrics, ("proceeds from issuance of debt",)),
        "debt_repaid": _latest_metric(metrics, ("repayments of debt", "repayments of long term debt")),
        "working_capital_change": _latest_metric(metrics, (
            "change in working capital", "changes in operating assets and liabilities",
        )),
    }
    available = {key: value for key, value in values.items() if value is not None}
    required = {"operating_cash_flow", "net_income", "capital_expenditure"}
    core_periods = {
        available[key][1] for key in required if key in available
    }
    period_aligned = len(core_periods) <= 1
    period = next(iter(core_periods), "") if period_aligned else ""
    aligned_available = {
        key: value for key, value in available.items()
        if key in required or not period or value[1] == period
    }
    components = {key: value[0] for key, value in aligned_available.items()}
    units = [value[2] for value in available.values() if value[2]]
    gaps = [
        "A prior same-duration TTM bridge is required to establish recurrence.",
    ]
    if not period_aligned:
        gaps.insert(
            0,
            "Operating cash flow, net income, and capex do not share the same trailing period.",
        )
    if "working_capital_change" not in available:
        gaps.append("The working-capital contribution was not separately aligned.")
    if "cash_taxes" not in available:
        gaps.append("Cash taxes were not aligned; tax expense is not cash paid.")
    if not {"debt_issued", "debt_repaid"}.intersection(available):
        gaps.append("Debt issuance and repayment cash flows were not aligned.")
    if not {"dividends_paid", "share_repurchases"}.issubset(available):
        gaps.append("Dividends and repurchases were not both aligned.")
    return {
        "components": components,
        "period": period if period_aligned else "",
        "unit": units[0] if units else "USD",
        "data_gaps": gaps,
        "period_aligned": period_aligned,
    }


def _latest_metric(
    metrics: list[Any],
    aliases: tuple[str, ...],
) -> tuple[float, str, str] | None:
    matches = [
        item for item in metrics
        if any(alias in str(getattr(item, "name", "") or "").casefold() for alias in aliases)
    ]
    if not matches:
        return None
    item = max(matches, key=lambda value: str(
        getattr(value, "trailing_period_end", "")
        or getattr(value, "period_end", "")
        or ""
    ))
    trailing = _number(getattr(item, "trailing_twelve_month_value", None))
    value = trailing if trailing is not None else _number(getattr(item, "value", None))
    if value is None:
        return None
    period = str(
        getattr(item, "trailing_period_end", "")
        or getattr(item, "period_end", "")
        or ""
    )
    return value, period, str(getattr(item, "unit", "USD") or "USD")


def _format_amount(value: float, unit: str) -> str:
    prefix = "$" if str(unit).upper() == "USD" else ""
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{prefix}{value / 1_000_000_000:,.1f}B"
    if absolute >= 1_000_000:
        return f"{prefix}{value / 1_000_000:,.1f}M"
    return f"{prefix}{value:,.1f}" + ("" if prefix else f" {unit}".rstrip())


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _request_id(request: Any) -> str:
    return str(getattr(request, "request_id", "") or "")


def _attempted_connectors(request: Any) -> tuple[str, ...]:
    return tuple(
        str(value) for value in
        list(getattr(request, "attempted_connector_ids", ()) or ())
        if value
    )
