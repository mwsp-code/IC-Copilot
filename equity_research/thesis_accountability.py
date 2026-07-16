from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .models import (
    ConsensusPackage,
    EventWorkflow,
    EventWorkflowItem,
    FilingRecord,
    ResearchSourcePlan,
    ThesisAuditChain,
    ThesisAuditStep,
    TradeIdea,
    ValuationResult,
)


def attach_thesis_audit_chains(
    ideas: list[TradeIdea],
    valuation: ValuationResult,
) -> None:
    for idea in ideas:
        idea.thesis_audit_chain = build_thesis_audit_chain(idea, valuation)


def build_thesis_audit_chain(
    idea: TradeIdea,
    valuation: ValuationResult,
) -> ThesisAuditChain:
    event = idea.source_events[0] if idea.source_events else None
    steps = [
        _source_step(idea),
        _claim_step(idea),
        _driver_step(idea),
        _valuation_step(idea, valuation),
        _market_capture_step(idea),
        _counter_thesis_step(idea),
        _monitor_step(idea),
    ]
    broken = [
        step.step for step in steps
        if step.status in {"Missing", "Weak", "Blocked", "Unknown"}
    ]
    if not event:
        summary = "No convincing thesis yet; the idea has no source event."
    elif broken:
        summary = "No convincing thesis yet; missing or weak links: " + ", ".join(broken) + "."
    elif idea.market_capture and idea.market_capture.capture_mode == "Price-only":
        summary = "Thesis chain is complete enough for IC review; market capture is price-only because point-in-time analyst expectation revisions are unavailable."
    else:
        summary = "Thesis chain is complete enough for IC review, subject to scenario and monitoring discipline."
    next_actions = []
    if idea.next_source_to_check:
        next_actions.append(idea.next_source_to_check)
    for step in steps:
        next_actions.extend(step.data_gaps[:2])
    return ThesisAuditChain(
        idea_id=idea.idea_id,
        status="Complete" if not broken else "Incomplete",
        summary=summary,
        steps=steps,
        broken_links=broken,
        next_actions=list(dict.fromkeys(next_actions))[:8],
    )


def build_event_workflow(
    ticker: str,
    filings: list[FilingRecord],
    ideas: list[TradeIdea],
    source_plan: ResearchSourcePlan,
    consensus: ConsensusPackage,
) -> EventWorkflow:
    items: list[EventWorkflowItem] = []
    latest_filing = _latest_filing(filings)
    if latest_filing:
        due_date = _next_periodic_due_date(latest_filing)
        items.append(EventWorkflowItem(
            "filing_window",
            f"Next SEC periodic filing check after {latest_filing.form}",
            due_date,
            "Medium",
            "SEC EDGAR submissions",
            "Refresh filing/change radar near the next expected 10-Q/10-K/20-F/6-K reporting window.",
        ))
    else:
        items.append(EventWorkflowItem(
            "filing_window",
            "Find latest SEC filing calendar",
            None,
            "High",
            "SEC EDGAR submissions",
            "No filing record was loaded, so the next filing workflow cannot be scheduled.",
            status="Needs setup",
        ))

    if consensus.status == "Unavailable" or not consensus.revisions:
        items.append(EventWorkflowItem(
            "consensus_history",
            "Seed point-in-time consensus history",
            None,
            "High",
            "CSV/manual consensus import",
            "Market-capture claims need pre-event and post-event consensus snapshots.",
        ))
    else:
        items.append(EventWorkflowItem(
            "consensus_history",
            "Refresh consensus snapshot after next catalyst",
            None,
            "Medium",
            consensus.provider,
            "Keep 7/30/90-day revisions current for market-capture analysis.",
        ))

    for request in source_plan.requests[:8]:
        items.append(EventWorkflowItem(
            "source_plan",
            request.title,
            None,
            request.priority,
            request.source_type,
            f"{request.reason_to_inspect} Expected evidence: {request.expected_evidence_type}",
            status=request.status,
        ))

    for idea in ideas:
        if idea.stage not in {"Research-Ready", "High-Conviction", "Investable"}:
            continue
        for monitor in idea.monitor_items[:3]:
            items.append(EventWorkflowItem(
                "monitor_rule",
                monitor.criterion,
                monitor.deadline,
                "High" if idea.stage in {"High-Conviction", "Investable"} else "Medium",
                monitor.source_field or monitor.data_source,
                f"Confirm if {monitor.confirm_trigger}; break if {monitor.break_trigger}",
                related_idea_id=idea.idea_id,
            ))

    deduped: list[EventWorkflowItem] = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in items:
        key = (item.item_type, item.title, item.related_idea_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return EventWorkflow(
        ticker=ticker.upper(),
        status="Available" if deduped else "Unavailable",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        items=deduped[:20],
        data_gaps=[] if deduped else ["No event workflow items could be generated."],
    )


def _source_step(idea: TradeIdea) -> ThesisAuditStep:
    event = idea.source_events[0] if idea.source_events else None
    citations = event.citations if event else []
    evidence = [
        (citation.snippet or citation.source or citation.url)
        for citation in citations[:3]
        if citation.snippet or citation.source or citation.url
    ]
    if any(citation.url and citation.snippet for citation in citations):
        status = "Passed"
        summary = "Source excerpt is linked with citation text."
    elif citations:
        status = "Weak"
        summary = "Citation exists, but exact excerpt or URL is incomplete."
    else:
        status = "Missing"
        summary = "No source-linked excerpt is attached."
    return ThesisAuditStep("Source excerpt", status, summary, evidence, [] if status == "Passed" else ["Attach exact excerpt, source URL, accession/section, and period."])


def _claim_step(idea: TradeIdea) -> ThesisAuditStep:
    status = idea.thesis_grade_status or "Unvalidated"
    event = idea.source_events[0] if idea.source_events else None
    reason = str((event.metrics or {}).get("not_thesis_grade_reason") or "") if event else ""
    if status == "Thesis-grade":
        mapped_status = "Passed"
        summary = "Claim validation marked this source signal thesis-grade."
    elif status in {"Watch Item", "Not thesis-grade"}:
        mapped_status = "Blocked"
        summary = reason or "Claim validation says the signal is not thesis-grade."
    else:
        mapped_status = "Unknown"
        summary = "Claim validation has not established thesis-grade status."
    evidence = list(idea.validated_claim_ids)
    return ThesisAuditStep("Validated claim", mapped_status, summary, evidence, [] if mapped_status == "Passed" else [summary])


def _driver_step(idea: TradeIdea) -> ThesisAuditStep:
    event = idea.source_events[0] if idea.source_events else None
    metrics = event.metrics if event else {}
    driver = str(metrics.get("economic_driver") or "Unmapped")
    materiality = str(metrics.get("driver_materiality") or "Unknown")
    if driver != "Unmapped" and materiality in {"High", "Medium"} and not metrics.get("normalization_required"):
        status = "Passed"
    elif metrics.get("normalization_required"):
        status = "Blocked"
    else:
        status = "Missing"
    summary = (
        f"Driver: {driver}; materiality: {materiality}. "
        f"{metrics.get('driver_why_it_matters') or idea.driver_template_summary or ''}"
    ).strip()
    gaps = []
    if driver == "Unmapped":
        gaps.append("Map the source signal to a material company or industry driver.")
    if metrics.get("normalization_required"):
        gaps.append(str(metrics.get("normalization_reason") or "Complete security-basis normalization."))
    return ThesisAuditStep("Business driver", status, summary, [driver] if driver != "Unmapped" else [], gaps)


def _valuation_step(idea: TradeIdea, valuation: ValuationResult) -> ThesisAuditStep:
    model = idea.payoff_model
    if model and model.status == "Available" and model.payoff_completeness and model.payoff_completeness.status == "Complete":
        return ThesisAuditStep(
            "Valuation / payoff impact",
            "Passed",
            f"Payoff model complete; illustrative EV {model.expected_value_pct:+.1f}%." if model.expected_value_pct is not None else "Payoff model complete.",
            [f"{scenario.name}: exit {scenario.exit_value}, return {scenario.net_return_pct}" for scenario in model.scenarios[:3]],
            [],
        )
    gaps = list(model.data_gaps if model else []) or list(valuation.missing_data[:3])
    return ThesisAuditStep(
        "Valuation / payoff impact",
        "Weak" if valuation.status == "Available" else "Missing",
        "Valuation/payoff bridge is incomplete or uses labelled payoff-envelope assumptions.",
        [_valuation_bridge_label(step) for step in valuation.bridge[:3]],
        gaps or ["Add explicit bull/base/bear operating assumptions and exit anchors."],
    )


def _market_capture_step(idea: TradeIdea) -> ThesisAuditStep:
    capture = idea.market_capture
    if not capture:
        return ThesisAuditStep("Market capture", "Missing", "No price/consensus capture analysis is attached.", [], ["Add event-window price reaction and point-in-time consensus snapshots."])
    if capture.category != "Unknown":
        status = "Passed"
    elif capture.capture_mode == "Price-only":
        status = "Price-only"
    else:
        status = "Unknown"
    gaps = list(capture.data_gaps)
    if capture.capture_mode == "Price-only":
        gaps = [
            "Analyst expectation revision is unavailable; do not claim uncaptured/not-priced-in without point-in-time consensus snapshots."
        ]
    return ThesisAuditStep(
        "Market capture",
        status,
        f"{capture.capture_mode}: {capture.explanation}",
        [
            f"Price reaction: {capture.price_reaction_pct}",
            f"Consensus revision: {capture.consensus_revision_pct}",
        ],
        gaps,
    )


def _counter_thesis_step(idea: TradeIdea) -> ThesisAuditStep:
    counter = (idea.strongest_counter_thesis or "").strip()
    lower = counter.lower()
    if not counter or counter == "Not yet evaluated.":
        return ThesisAuditStep(
            "Counter-thesis",
            "Missing",
            "No strongest counter-thesis has been documented.",
            [],
            ["Document the strongest source-backed counter-thesis before treating the idea as Research-Ready."],
        )
    if "no material counter-evidence identified" in lower or "none found" in lower:
        return ThesisAuditStep(
            "Counter-thesis",
            "Weak",
            "The current run did not identify a material counter-thesis; this is a diligence gap, not positive evidence.",
            [counter],
            ["Actively search for contradictory filing language, peer metrics, valuation disagreement, and management or consensus evidence."],
        )
    return ThesisAuditStep(
        "Counter-thesis",
        "Passed",
        "A specific counter-thesis is documented for IC debate.",
        [counter],
        [],
    )


def _monitor_step(idea: TradeIdea) -> ThesisAuditStep:
    complete = [
        item for item in idea.monitor_items
        if item.metric and item.operator and item.deadline
        and item.confirm_threshold is not None and item.break_threshold is not None
    ]
    if complete:
        status = "Passed"
        summary = f"{len(complete)} machine-readable monitor rule(s) are attached."
    elif idea.monitor_items:
        status = "Weak"
        summary = "Monitor items exist but are not fully machine-readable."
    else:
        status = "Missing"
        summary = "No monitor rule is attached."
    return ThesisAuditStep(
        "Monitor rule",
        status,
        summary,
        [f"{item.criterion}: {item.metric} {item.operator} {item.confirm_threshold}" for item in complete[:3]],
        [] if status == "Passed" else ["Add metric, operator, threshold, deadline, and source field."],
    )


def _latest_filing(filings: list[FilingRecord]) -> FilingRecord | None:
    return max(filings, key=lambda item: item.filing_date or "", default=None)


def _next_periodic_due_date(filing: FilingRecord) -> str | None:
    base = filing.report_date or filing.filing_date
    try:
        day = date.fromisoformat(base[:10])
    except (TypeError, ValueError):
        return None
    if filing.form in {"10-K", "20-F", "40-F"}:
        return (day + timedelta(days=365)).isoformat()
    if filing.form in {"10-Q", "6-K"}:
        return (day + timedelta(days=95)).isoformat()
    return (day + timedelta(days=90)).isoformat()


def _valuation_bridge_label(step) -> str:
    case = getattr(step, "case", getattr(step, "label", "Valuation bridge"))
    metric = getattr(step, "metric", "")
    value = getattr(step, "value", None)
    unit = getattr(step, "unit", "")
    return f"{case}: {metric} {value if value is not None else 'n/a'} {unit}".strip()
