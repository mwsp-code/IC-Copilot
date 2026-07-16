from __future__ import annotations

from .models import (
    CompanyEconomics,
    ConsensusPackage,
    ConvictionChainStep,
    ThesisConvictionChain,
    TradeIdea,
    ValuationResult,
)


def build_conviction_chains(
    ideas: list[TradeIdea],
    economics: CompanyEconomics,
    valuation: ValuationResult,
    consensus: ConsensusPackage | None = None,
) -> list[ThesisConvictionChain]:
    chains: list[ThesisConvictionChain] = []
    for idea in ideas:
        chain = build_conviction_chain(idea, economics, valuation, consensus)
        idea.conviction_chain = chain
        chains.append(chain)
    return chains


def build_conviction_chain(
    idea: TradeIdea,
    economics: CompanyEconomics,
    valuation: ValuationResult,
    consensus: ConsensusPackage | None = None,
) -> ThesisConvictionChain:
    event = idea.source_events[0] if idea.source_events else None
    driver = str((event.metrics or {}).get("economic_driver") or "Unmapped") if event else "Unmapped"
    materiality = str((event.metrics or {}).get("driver_materiality") or "Unknown") if event else "Unknown"
    steps = [
        _source_change_step(idea),
        _driver_step(driver, materiality, economics),
        _kpi_impact_step(idea, driver),
        _valuation_step(idea, valuation),
        _expectation_gap_step(idea, consensus),
        _catalyst_step(idea),
        _falsification_step(idea),
    ]
    data_gaps = []
    for step in steps:
        if step.status != "Complete":
            data_gaps.extend(step.data_gaps or [f"{step.label} is {step.status.lower()}."])
    complete = sum(1 for step in steps if step.status == "Complete")
    missing = sum(1 for step in steps if step.status == "Missing")
    critical_complete = all(
        step.status == "Complete"
        for step in steps
        if step.label in {"Source change", "Business driver", "Catalyst and timing", "Falsification tests"}
    )
    if idea.stage == "High-Conviction" and missing == 0 and critical_complete:
        status = "Convincing"
        confidence = "High"
    elif complete >= 5 and critical_complete:
        status = "Promising but incomplete"
        confidence = "Medium"
    elif complete >= 3:
        status = "Early research"
        confidence = "Low"
    else:
        status = "Weak"
        confidence = "Low"
    summary = _summary(idea, driver, status, complete, len(steps))
    return ThesisConvictionChain(
        idea_id=idea.idea_id,
        status=status,
        confidence=confidence,
        summary=summary,
        steps=steps,
        what_must_be_true=_what_must_be_true(idea, driver),
        what_would_falsify=_what_would_falsify(idea),
        next_research_actions=_next_actions(steps),
        data_gaps=list(dict.fromkeys(data_gaps))[:10],
    )


def _source_change_step(idea: TradeIdea) -> ConvictionChainStep:
    event = idea.source_events[0] if idea.source_events else None
    if not event:
        return ConvictionChainStep(
            "Source change",
            "Missing",
            "No source event is attached to this idea.",
            data_gaps=["Attach at least one SEC, issuer, transcript, consensus, or price-attribution event."],
        )
    has_citation = any(citation.url for citation in event.citations)
    status = "Complete" if has_citation else "Partial"
    return ConvictionChainStep(
        "Source change",
        status,
        f"{event.title}: {event.summary}",
        evidence=[
            f"{citation.source}: {citation.snippet or citation.section or citation.form or citation.url}"
            for citation in event.citations[:3]
        ],
        data_gaps=[] if has_citation else ["The detected change needs a source URL and excerpt before conviction can rise."],
    )


def _driver_step(
    driver: str,
    materiality: str,
    economics: CompanyEconomics,
) -> ConvictionChainStep:
    if not driver or driver == "Unmapped":
        return ConvictionChainStep(
            "Business driver",
            "Missing",
            "The signal is not yet tied to the company economics or industry playbook.",
            evidence=[economics.business_model],
            data_gaps=["Map the signal to a revenue, margin, capital, risk, governance, or management-quality driver."],
        )
    status = "Complete" if materiality in {"High", "Medium"} else "Partial"
    return ConvictionChainStep(
        "Business driver",
        status,
        f"The signal maps to {driver} with {materiality.lower()} materiality in {economics.industry_playbook.industry_label}.",
        evidence=[economics.business_model],
        data_gaps=[] if status == "Complete" else ["Driver materiality is low or unquantified."],
    )


def _kpi_impact_step(idea: TradeIdea, driver: str) -> ConvictionChainStep:
    event = idea.source_events[0] if idea.source_events else None
    metrics = event.metrics if event else {}
    numeric_fields = [
        f"{key}={value}"
        for key, value in metrics.items()
        if isinstance(value, (int, float))
    ]
    metric_name = metrics.get("metric_name") if event else None
    if numeric_fields:
        return ConvictionChainStep(
            "KPI or forecast impact",
            "Complete",
            f"The evidence has numeric fields tied to {metric_name or driver}: {', '.join(numeric_fields[:4])}.",
            evidence=numeric_fields[:4],
        )
    if event and event.summary:
        return ConvictionChainStep(
            "KPI or forecast impact",
            "Partial",
            f"The qualitative signal may affect {driver}, but the operating bridge is not fully quantified.",
            evidence=[event.summary],
            data_gaps=["Add segment KPI, consensus line item, or model assumption showing EPS/FCF/book-value impact."],
        )
    return ConvictionChainStep(
        "KPI or forecast impact",
        "Missing",
        "No operating KPI or forecast impact is available.",
        data_gaps=["Quantify which KPI changes, by how much, and over what fiscal period."],
    )


def _valuation_step(idea: TradeIdea, valuation: ValuationResult) -> ConvictionChainStep:
    payoff = idea.payoff_model
    if payoff and payoff.payoff_completeness and payoff.payoff_completeness.status == "Complete":
        label = "valuation-anchored" if payoff.status == "Available" else "payoff-envelope"
        ev = (
            f"; illustrative EV {payoff.expected_value_pct:+.1f}%"
            if payoff.expected_value_pct is not None else ""
        )
        return ConvictionChainStep(
            "Valuation or payoff bridge",
            "Complete" if payoff.status == "Available" else "Partial",
            f"The idea has a {label} scenario table with entry {payoff.entry_price or 'n/a'} {payoff.currency}{ev}.",
            evidence=[
                f"{scenario.name}: exit {scenario.exit_value}, net return {scenario.net_return_pct}"
                for scenario in payoff.scenarios[:3]
            ],
            data_gaps=[] if payoff.status == "Available" else ["Payoff is an envelope, not internally calculated fair value."],
        )
    missing = payoff.payoff_completeness.missing_inputs if payoff and payoff.payoff_completeness else []
    return ConvictionChainStep(
        "Valuation or payoff bridge",
        "Missing",
        f"Valuation status is {valuation.status}; payoff is incomplete.",
        data_gaps=missing or valuation.missing_data or ["Add entry price and bull/base/bear exit-value assumptions."],
    )


def _expectation_gap_step(
    idea: TradeIdea,
    consensus: ConsensusPackage | None,
) -> ConvictionChainStep:
    capture = idea.market_capture
    if not capture:
        return ConvictionChainStep(
            "Expectation gap",
            "Missing",
            "No market-capture or expectation-gap analysis is attached.",
            data_gaps=["Add event-specific price reaction and point-in-time expectation data."],
        )
    if capture.category != "Unknown":
        status = "Complete" if capture.consensus_official else "Partial"
        gap = [] if capture.consensus_official else ["Consensus is missing or unofficial, so the priced-in claim is capped."]
        return ConvictionChainStep(
            "Expectation gap",
            status,
            f"Market capture is {capture.category}. {capture.explanation}",
            evidence=[
                f"Price reaction: {capture.price_reaction_pct if capture.price_reaction_pct is not None else 'n/a'}%",
                f"Consensus revision: {capture.consensus_revision_pct if capture.consensus_revision_pct is not None else 'n/a'}%",
                f"Consensus status: {consensus.status if consensus else 'Unknown'}",
            ],
            data_gaps=gap,
        )
    return ConvictionChainStep(
        "Expectation gap",
        "Partial",
        capture.explanation,
        data_gaps=capture.data_gaps or ["Price and consensus reactions are incomplete."],
    )


def _catalyst_step(idea: TradeIdea) -> ConvictionChainStep:
    if idea.catalyst and idea.horizon:
        return ConvictionChainStep(
            "Catalyst and timing",
            "Complete",
            f"{idea.catalyst}; expected horizon {idea.horizon}.",
        )
    return ConvictionChainStep(
        "Catalyst and timing",
        "Missing",
        "The idea does not yet state a catalyst and horizon.",
        data_gaps=["Add the expected catalyst and time horizon."],
    )


def _falsification_step(idea: TradeIdea) -> ConvictionChainStep:
    machine_readable = [
        item for item in idea.monitor_items
        if item.metric and item.operator and item.deadline
    ]
    if machine_readable:
        return ConvictionChainStep(
            "Falsification tests",
            "Complete",
            "The idea has machine-readable confirmation and break criteria.",
            evidence=[
                f"{item.criterion}: confirm if {item.confirm_trigger}; break if {item.break_trigger}"
                for item in machine_readable[:3]
            ],
        )
    if idea.monitor_items:
        return ConvictionChainStep(
            "Falsification tests",
            "Partial",
            "The idea has monitor items, but they are not fully machine-readable.",
            evidence=[item.criterion for item in idea.monitor_items[:3]],
            data_gaps=["Add metric, operator, thresholds, deadline, and source field for each key criterion."],
        )
    return ConvictionChainStep(
        "Falsification tests",
        "Missing",
        "No monitor criteria are attached.",
        data_gaps=["Add explicit confirmation and break conditions."],
    )


def _summary(
    idea: TradeIdea,
    driver: str,
    status: str,
    complete: int,
    total: int,
) -> str:
    return (
        f"{status}: {complete}/{total} conviction-chain links are complete. "
        f"The thesis is anchored to {driver or 'an unmapped driver'} and remains at stage {idea.stage}."
    )


def _what_must_be_true(idea: TradeIdea, driver: str) -> list[str]:
    items = [
        f"The detected evidence must prove durable enough to affect {driver if driver != 'Unmapped' else 'a material business driver'}.",
        "The next reporting cycle must not reveal an offsetting line item or one-time explanation.",
    ]
    if idea.payoff_model and idea.payoff_model.status in {"Available", "Envelope"}:
        items.append("The bull/base/bear scenario assumptions must remain plausible at the current entry price.")
    if idea.market_capture and idea.market_capture.category in {"Uncaptured", "Partially captured"}:
        items.append("Consensus, price, or narrative attention must still lag the evidence rather than already discount it.")
    return items


def _what_would_falsify(idea: TradeIdea) -> list[str]:
    falsifiers = [item.break_trigger for item in idea.monitor_items[:4] if item.break_trigger]
    if idea.strongest_counter_thesis:
        falsifiers.append(idea.strongest_counter_thesis)
    return list(dict.fromkeys(falsifiers))[:6] or ["Future source evidence fails to confirm the detected change."]


def _next_actions(steps: list[ConvictionChainStep]) -> list[str]:
    actions: list[str] = []
    for step in steps:
        if step.status == "Complete":
            continue
        if step.label == "Business driver":
            actions.append("Map the signal to a material company or industry driver.")
        elif step.label == "KPI or forecast impact":
            actions.append("Quantify the KPI, segment, margin, EPS, FCF, or book-value bridge.")
        elif step.label == "Valuation or payoff bridge":
            actions.append("Add current entry price and explicit bull/base/bear exit assumptions.")
        elif step.label == "Expectation gap":
            actions.append("Fetch event-specific price reaction and point-in-time consensus or narrative context.")
        elif step.label == "Falsification tests":
            actions.append("Convert monitor notes into metric/operator/threshold/deadline checks.")
        else:
            actions.append(f"Complete the {step.label.lower()} link.")
    return list(dict.fromkeys(actions))[:8]
