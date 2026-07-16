from __future__ import annotations

import hashlib

from .models import (
    ChangeEvent,
    CoverageCase,
    CoverageExpansionDiagnostics,
    EvidenceWorkOrder,
    EvidenceWorkOrderItem,
    MarketCaptureReadiness,
    MetricResolutionAudit,
    ResearchQuestion,
    ResearchSourcePlan,
    SourceCoverageMatrix,
    ThesisValidationReport,
)
from .global_coverage import global_coverage_work_order_items


def build_evidence_work_order(
    thesis_validation: ThesisValidationReport | None,
    market_capture_readiness: MarketCaptureReadiness | None,
    research_questions: list[ResearchQuestion] | None,
    source_plan: ResearchSourcePlan | None,
    coverage_expansion: CoverageExpansionDiagnostics | None,
    coverage_case: CoverageCase | None = None,
    source_coverage_matrix: SourceCoverageMatrix | None = None,
    metric_resolution_audit: MetricResolutionAudit | None = None,
    events: list[ChangeEvent] | None = None,
) -> EvidenceWorkOrder:
    """Build a prioritized analyst work order from existing deterministic diagnostics."""
    items: list[EvidenceWorkOrderItem] = []
    if thesis_validation:
        items.extend(_from_thesis_validation(thesis_validation))
    if market_capture_readiness:
        items.extend(_from_market_capture(market_capture_readiness))
    if research_questions:
        items.extend(_from_research_questions(research_questions))
    if coverage_expansion:
        items.extend(_from_coverage_expansion(coverage_expansion))
    if source_plan:
        items.extend(_from_source_plan(source_plan))
    if coverage_case and source_coverage_matrix and metric_resolution_audit:
        items.extend(_from_global_coverage(coverage_case, source_coverage_matrix, metric_resolution_audit))
    if events:
        items.extend(_from_disclosure_events(events))

    items = _dedupe_items(items)
    items.sort(key=_sort_key)
    status = _status(items)
    gaps = []
    if not items:
        gaps.append("No evidence work order was generated from the current diagnostics.")
    summary = _summary(status, items)
    return EvidenceWorkOrder(status=status, summary=summary, items=items[:20], data_gaps=gaps)


def _from_thesis_validation(validation: ThesisValidationReport) -> list[EvidenceWorkOrderItem]:
    rows: list[EvidenceWorkOrderItem] = []
    for action in validation.next_evidence_actions:
        rows.append(_item(
            action.priority,
            action.channel,
            action.action,
            action.source,
            "Evidence that resolves the thesis-validation gap.",
            action.why_it_matters,
            "thesis_validation",
            blocks_high_conviction=action.blocks_high_conviction,
            blocks_research_ready=action.blocks_high_conviction and action.priority == "High",
        ))
    for evidence in validation.required_next_evidence:
        rows.append(_item(
            "High",
            "Thesis validation",
            evidence,
            "registered_source",
            "Source-linked evidence or contradiction resolution.",
            "Required next evidence blocks a cleaner IC conclusion.",
            "thesis_validation_gap",
            blocks_high_conviction=True,
        ))
    return rows


def _from_market_capture(readiness: MarketCaptureReadiness) -> list[EvidenceWorkOrderItem]:
    rows: list[EvidenceWorkOrderItem] = []
    for action in readiness.actions:
        rows.append(_item(
            action.priority,
            f"Market capture: {action.area}",
            action.action,
            action.source_type,
            "Point-in-time price, consensus, estimate, target, or recommendation evidence.",
            action.why_it_matters,
            "market_capture_readiness",
            related_idea_ids=action.related_idea_ids,
            blocks_high_conviction=False,
        ))
    for need in readiness.snapshot_needs:
        action = (
            f"Seed pre/post consensus snapshots for {need.metric_family} around "
            f"{need.event_date or 'the event date'}."
        )
        rows.append(_item(
            "High",
            "Market capture snapshots",
            action,
            "consensus_manual",
            (
                f"{need.pre_event_snapshot}; {need.post_event_snapshot}. "
                f"Accepted sources: {', '.join(need.accepted_sources[:4]) or 'official or CSV consensus snapshots'}. "
                "The app will use configured official providers or local snapshots first; CSV/manual import is the "
                f"fallback when historical provider data is unavailable. CSV row hints: {' | '.join(need.csv_row_hints[:3]) or 'use built-in consensus templates'}."
            ),
            need.reason or "Market capture cannot be classified without point-in-time expectations.",
            "market_capture_snapshot_need",
            related_idea_ids=[need.idea_id],
            blocks_high_conviction=False,
            cost_latency="Provider history if licensed; otherwise CSV/manual immediately; daily snapshots build future history.",
            acceptance_criteria=[
                "Observation timestamp is on or before the relevant event or post-event date.",
                "Metric, fiscal period, source, analyst count/freshness when available, and provider semantics are preserved.",
            ],
            falsification_tests=[
                "Only today's consensus is available for a historical event.",
                "Unofficial data is the sole evidence for a definitive market-capture claim.",
            ],
        ))
    return rows


def _from_research_questions(questions: list[ResearchQuestion]) -> list[EvidenceWorkOrderItem]:
    rows: list[EvidenceWorkOrderItem] = []
    for question in questions[:8]:
        source = question.primary_source_types[0] if question.primary_source_types else "registered_source"
        expected = "; ".join(question.required_evidence[:3]) or "Evidence that answers the open research question."
        rows.append(_item(
            question.priority,
            f"Research question: {question.driver_name}",
            question.title,
            source,
            expected,
            question.why_it_matters,
            "research_question",
            related_idea_ids=question.related_idea_ids,
            blocks_research_ready=question.priority == "High",
            blocks_high_conviction=True,
            acceptance_criteria=question.acceptance_criteria[:4],
            falsification_tests=question.falsification_tests[:4],
        ))
    return rows


def _from_coverage_expansion(diagnostics: CoverageExpansionDiagnostics) -> list[EvidenceWorkOrderItem]:
    rows: list[EvidenceWorkOrderItem] = []
    for action in diagnostics.recommended_expansions[:10]:
        rows.append(_item(
            action.priority,
            f"Coverage expansion: {action.area}",
            action.action,
            action.source_type,
            action.expected_output or "Coverage-expansion evidence.",
            action.why_it_matters,
            "coverage_expansion",
            blocks_research_ready=action.priority == "High" and diagnostics.status == "No convincing thesis yet",
            blocks_high_conviction=action.priority in {"High", "Medium"},
            cost_latency=action.cost_latency,
            acceptance_criteria=[action.integrity_rule] if action.integrity_rule else [],
        ))
    return rows


def _from_source_plan(plan: ResearchSourcePlan) -> list[EvidenceWorkOrderItem]:
    rows: list[EvidenceWorkOrderItem] = []
    for request in plan.requests[:10]:
        rows.append(_item(
            request.priority,
            "Source plan",
            request.title,
            request.source_type,
            request.expected_evidence_type,
            request.reason_to_inspect,
            "source_plan",
            blocks_high_conviction=request.priority == "High",
            cost_latency=(
                f"{request.cost_latency}. Automatic registered-source fetch should be attempted before asking for manual work."
                if request.cost_latency else
                "Automatic registered-source fetch should be attempted before asking for manual work."
            ),
            acceptance_criteria=[request.confirms_or_disproves] if request.confirms_or_disproves else [],
        ))
    return rows


def _from_disclosure_events(events: list[ChangeEvent]) -> list[EvidenceWorkOrderItem]:
    rows: list[EvidenceWorkOrderItem] = []
    for event in events:
        metrics = event.metrics or {}
        if metrics.get("signal_method") != "disclosure_change_engine":
            continue
        action = str(metrics.get("research_work_order") or "")
        if not action:
            continue
        comparison_status = str(metrics.get("comparison_status") or "")
        reason = str(metrics.get("comparison_reason_code") or metrics.get("reason_code") or "")
        event_type = str(metrics.get("disclosure_event_type") or "")
        blocks_research = comparison_status not in {"period_aligned", "comparable_imperfect"}
        rows.append(_item(
            "High" if blocks_research else "Medium",
            "Disclosure comparison",
            action,
            "sec_filing_comparison",
            (
                "Aligned current/prior filing sections with form, accession, filing date, "
                "period, section heading, normalized mention rate, sentence diff, semantic drift, "
                "and reason code."
            ),
            (
                "Disclosure observations should become research work orders until they prove a "
                "section-aligned change and map to a material business, credit, KPI, or valuation driver."
            ),
            "disclosure_change_engine",
            blocks_research_ready=blocks_research,
            blocks_high_conviction=True,
            acceptance_criteria=[
                "Current and prior accessions are distinct and same issuer/form/cadence.",
                "Current and prior sections or topic windows are explicitly named.",
                "Reason code is not a missing-data or invalid-comparison state.",
            ],
            falsification_tests=[
                "Prior text is missing because retrieval, filing selection, or parser alignment failed.",
                "The apparent change is driven only by raw mention counts or boilerplate movement.",
                f"Current diagnostic state: {comparison_status or 'unknown'} / {reason or event_type or 'unknown'}.",
            ],
        ))
    return rows


def _from_global_coverage(
    coverage_case: CoverageCase,
    source_coverage_matrix: SourceCoverageMatrix,
    metric_resolution_audit: MetricResolutionAudit,
) -> list[EvidenceWorkOrderItem]:
    rows: list[EvidenceWorkOrderItem] = []
    for (
        priority,
        channel,
        action,
        source_type,
        expected_output,
        why_it_matters,
        acceptance_criteria,
        falsification_tests,
    ) in global_coverage_work_order_items(coverage_case, source_coverage_matrix, metric_resolution_audit):
        rows.append(_item(
            priority,
            channel,
            action,
            source_type,
            expected_output,
            why_it_matters,
            "global_coverage",
            blocks_high_conviction=priority in {"High", "Medium"},
            cost_latency="Depends on jurisdiction adapter or manual official-source import.",
            acceptance_criteria=acceptance_criteria,
            falsification_tests=falsification_tests,
        ))
    return rows


def _item(
    priority: str,
    channel: str,
    action: str,
    source_type: str,
    expected_output: str,
    why_it_matters: str,
    origin: str,
    *,
    related_idea_ids: list[str] | None = None,
    blocks_research_ready: bool = False,
    blocks_high_conviction: bool = False,
    cost_latency: str = "",
    acceptance_criteria: list[str] | None = None,
    falsification_tests: list[str] | None = None,
) -> EvidenceWorkOrderItem:
    normalized_priority = priority if priority in {"High", "Medium", "Low"} else "Medium"
    raw_id = "|".join([origin, channel, action, source_type])
    return EvidenceWorkOrderItem(
        work_id=hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:12],
        priority=normalized_priority,
        channel=channel,
        action=action,
        source_type=source_type,
        expected_output=expected_output,
        why_it_matters=why_it_matters,
        origin=origin,
        related_idea_ids=list(related_idea_ids or []),
        blocks_research_ready=blocks_research_ready,
        blocks_high_conviction=blocks_high_conviction,
        cost_latency=cost_latency,
        acceptance_criteria=list(acceptance_criteria or []),
        falsification_tests=list(falsification_tests or []),
    )


def _dedupe_items(items: list[EvidenceWorkOrderItem]) -> list[EvidenceWorkOrderItem]:
    best: dict[tuple[str, str], EvidenceWorkOrderItem] = {}
    for item in items:
        key = (_normalize(item.action), item.source_type)
        existing = best.get(key)
        if existing is None or _sort_key(item) < _sort_key(existing):
            if existing is not None:
                item.origin = _merge_origin(item.origin, existing.origin)
                item.related_idea_ids = _dedupe(item.related_idea_ids + existing.related_idea_ids)
                item.acceptance_criteria = _dedupe(item.acceptance_criteria + existing.acceptance_criteria)
                item.falsification_tests = _dedupe(item.falsification_tests + existing.falsification_tests)
                item.blocks_research_ready = item.blocks_research_ready or existing.blocks_research_ready
                item.blocks_high_conviction = item.blocks_high_conviction or existing.blocks_high_conviction
            best[key] = item
            continue
        if existing:
            existing.origin = _merge_origin(existing.origin, item.origin)
            existing.related_idea_ids = _dedupe(existing.related_idea_ids + item.related_idea_ids)
            existing.acceptance_criteria = _dedupe(existing.acceptance_criteria + item.acceptance_criteria)
            existing.falsification_tests = _dedupe(existing.falsification_tests + item.falsification_tests)
            existing.blocks_research_ready = existing.blocks_research_ready or item.blocks_research_ready
            existing.blocks_high_conviction = existing.blocks_high_conviction or item.blocks_high_conviction
    return list(best.values())


def _sort_key(item: EvidenceWorkOrderItem) -> tuple[int, int, int, str]:
    priority = {"High": 0, "Medium": 1, "Low": 2}.get(item.priority, 1)
    research_blocker = 0 if item.blocks_research_ready else 1
    conviction_blocker = 0 if item.blocks_high_conviction else 1
    return (priority, research_blocker, conviction_blocker, item.channel)


def _status(items: list[EvidenceWorkOrderItem]) -> str:
    if not items:
        return "No work order"
    if any(item.blocks_research_ready for item in items):
        return "Blocks Research-Ready"
    if any(item.blocks_high_conviction for item in items):
        return "Blocks High-Conviction"
    return "Follow-up available"


def _summary(status: str, items: list[EvidenceWorkOrderItem]) -> str:
    if not items:
        return "No open evidence actions were generated from validation, source planning, or coverage diagnostics."
    high = sum(1 for item in items if item.priority == "High")
    research = sum(1 for item in items if item.blocks_research_ready)
    conviction = sum(1 for item in items if item.blocks_high_conviction)
    return (
        f"{status}: {len(items)} open evidence action(s), including {high} high-priority item(s), "
        f"{research} Research-Ready blocker(s), and {conviction} High-Conviction blocker(s)."
    )


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())


def _merge_origin(left: str, right: str) -> str:
    return "+".join(_dedupe([item for item in (left, right) if item]))


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return rows
