from __future__ import annotations

from .models import ChangeEvent, DriverExplanationTemplate, ResearchSourcePlan, TradeIdea


TEMPLATES: dict[str, DriverExplanationTemplate] = {
    "revenue": DriverExplanationTemplate(
        "revenue",
        "Revenue / demand",
        "Revenue is the first bridge from source evidence to EPS, FCF, and multiple durability.",
        "Segment revenue, volume/price, customer demand, consensus revenue revisions, or management commentary confirms the direction.",
        "Revenue revisions, peer read-through, or segment KPIs fail to move in the thesis direction.",
        "Segment KPI table, earnings release, transcript Q&A, or consensus revenue snapshots.",
    ),
    "margin": DriverExplanationTemplate(
        "margin",
        "Margin / mix",
        "Margins can reprice operating leverage when the change is durable rather than one-time.",
        "Gross/operating margin bridge, mix disclosure, cost line detail, or guidance confirms the change.",
        "Margin reverses, management calls it temporary, or cost/mix data contradicts the source signal.",
        "MD&A margin discussion, earnings slides, transcript, and segment margin table.",
    ),
    "opex": DriverExplanationTemplate(
        "opex",
        "Operating expense leverage",
        "Opex growing faster than revenue can pressure operating income even when demand is stable.",
        "Expense line items, hiring/cost actions, and operating margin bridge explain the change.",
        "Opex growth is one-time, reclassified, or paired with accelerating revenue and margin expansion.",
        "Expense footnote, cost action disclosure, segment operating income bridge, or transcript Q&A.",
    ),
    "share_count": DriverExplanationTemplate(
        "share_count",
        "Share count / capital return",
        "Share-count changes affect per-share value only after the security basis is reconciled.",
        "Ordinary/ADS reconciliation, buyback table, split history, and weighted-average share basis are consistent.",
        "The move is caused by ADR ratio, split, XBRL concept, or weighted-average/period-end mismatch.",
        "20-F/10-K share reconciliation, 6-K results deck, buyback table, and ADS ratio source.",
    ),
    "debt": DriverExplanationTemplate(
        "debt",
        "Debt / liquidity",
        "Leverage and liquidity can change downside risk, refinancing risk, and equity optionality.",
        "Debt maturity, cash, covenant, interest cost, and rating/credit spread evidence support the claim.",
        "Cash generation, refinancing, or debt reduction neutralizes the balance-sheet risk.",
        "Debt footnote, liquidity section, maturity table, rating action, or credit spread data.",
    ),
    "guidance": DriverExplanationTemplate(
        "guidance",
        "Guidance / expectations",
        "Guidance matters when it changes consensus expectations or narrows the operating range.",
        "Exact metric, period, range, and management quote are supported by estimate revisions.",
        "The language is boilerplate, accounting-only, vague, or not followed by estimate revisions.",
        "Earnings call prepared remarks/Q&A, issuer release, and point-in-time consensus snapshots.",
    ),
    "regulation": DriverExplanationTemplate(
        "regulation",
        "Regulation / policy risk",
        "Regulatory changes can alter risk premium, growth assumptions, and required return.",
        "Regulator/issuer filings quantify timing, scope, probability, or financial exposure.",
        "The risk language is generic, unchanged, or not material to operations/valuation.",
        "Risk factor diff, regulator release, 8-K/6-K, legal disclosure, or official policy source.",
    ),
    "management": DriverExplanationTemplate(
        "management",
        "Management credibility / execution",
        "Management language is useful only when cross-checked against filings, facts, and later outcomes.",
        "Promises are specific, measurable, repeated, and later corroborated by KPIs or filings.",
        "Claims are vague, contradicted, evasive, or not observable in subsequent results.",
        "Prior calls, current transcript, proxy/AGM material, and management promise tracker.",
    ),
}


def template_for_event(event: ChangeEvent) -> DriverExplanationTemplate:
    key = _template_key(event)
    return TEMPLATES.get(key, TEMPLATES["revenue"])


def attach_source_plan_to_ideas(ideas: list[TradeIdea], source_plan: ResearchSourcePlan | None) -> None:
    for idea in ideas:
        if not idea.driver_template_summary and idea.source_events:
            template = template_for_event(idea.source_events[0])
            idea.driver_template_summary = _summary(template)
        request = _best_request_for_idea(idea, source_plan)
        if request:
            idea.next_source_to_check = (
                f"{request.title} [{request.source_type}]: {request.reason_to_inspect}"
            )
        elif idea.source_events:
            template = template_for_event(idea.source_events[0])
            idea.next_source_to_check = template.next_source


def _best_request_for_idea(idea: TradeIdea, source_plan: ResearchSourcePlan | None):
    if not source_plan or not source_plan.requests:
        return None
    event = idea.source_events[0] if idea.source_events else None
    if event and event.metrics.get("normalization_required"):
        for request in source_plan.requests:
            if request.source_type in {"sec_filing", "presentation", "issuer_ir"}:
                return request
    if idea.thesis_grade_status in {"Watch Item", "Not thesis-grade"}:
        return source_plan.requests[0]
    driver = str((event.metrics or {}).get("economic_driver") or "").lower() if event else ""
    for request in source_plan.requests:
        haystack = f"{request.title} {request.reason_to_inspect} {request.expected_evidence_type}".lower()
        if any(token in haystack for token in driver.split(" / ")[0:1] if token):
            return request
    return source_plan.requests[0]


def _template_key(event: ChangeEvent) -> str:
    text = f"{event.category} {event.title} {event.metrics.get('metric_name', '')} {event.metrics.get('economic_driver', '')}".lower()
    if any(token in text for token in ("share", "buyback", "dilution")):
        return "share_count"
    if any(token in text for token in ("guidance", "outlook", "expectation")):
        return "guidance"
    if any(token in text for token in ("margin", "gross")):
        return "margin"
    if any(token in text for token in ("expense", "opex", "sga", "r&d", "marketing", "operating leverage")):
        return "opex"
    if any(token in text for token in ("debt", "liquidity", "cash", "borrow", "leverage")):
        return "debt"
    if any(token in text for token in ("risk", "litigation", "regulation", "policy", "legal")):
        return "regulation"
    if any(token in text for token in ("management", "tone", "credibility", "evasion", "governance")):
        return "management"
    return "revenue"


def _summary(template: DriverExplanationTemplate) -> str:
    return (
        f"{template.label}: {template.why_it_matters} "
        f"Confirm with: {template.confirm_evidence} "
        f"Falsify if: {template.falsify_evidence}"
    )
