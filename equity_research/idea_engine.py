from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from .analysis import format_number
from .models import (
    AssumptionProvenance,
    ChangeEvent,
    Citation,
    CompanyIdentity,
    DriverAnalysis,
    DriverFactor,
    EvidenceLedger,
    FinancialMetric,
    IdeaGateResult,
    MarketCapture,
    MonitorItem,
    PayoffCompleteness,
    PayoffModel,
    PeerReadthrough,
    ProbabilityProvenance,
    PromotionEvidenceBundle,
    Scenario,
    ScenarioAssumption,
    ScoreBreakdown,
    TradeIdea,
    ValuationCase,
    ValuationResult,
    ResearchSourcePlan,
    ShareReconciliation,
)
from .peers import PEER_MAP, peer_universe_for
from .providers import ConsensusAdapter, PriceReaction
from .driver_templates import attach_source_plan_to_ideas, template_for_event
from .metric_intelligence import metric_policy_for
from .promotion_evidence import decide_promotion


STAGE_CANDIDATE = "Candidate"
STAGE_RESEARCH_READY = "Research-Ready"
STAGE_HIGH_CONVICTION = "High-Conviction"
LEGACY_INVESTABLE = "Investable"
MANAGEMENT_SIGNAL_FAMILIES = {
    "guidance_shift",
    "management_credibility",
    "qa_evasion",
    "strategic_priority_change",
    "capital_allocation_change",
    "governance_change",
    "incentive_alignment",
    "shareholder_vote_signal",
    "tone_shift",
    "guidance_specificity_change",
}


def generate_trade_ideas(
    identity: CompanyIdentity,
    events: list[ChangeEvent],
    price_reaction: PriceReaction | None = None,
    consensus: ConsensusAdapter | None = None,
    metrics: list[FinancialMetric] | None = None,
    price_reactions: dict[str, PriceReaction] | None = None,
    source_plan: ResearchSourcePlan | None = None,
) -> list[TradeIdea]:
    ideas: list[TradeIdea] = []
    material_events = [event for event in events if event.severity >= 3]
    if not material_events:
        material_events = events[:3]

    for event in material_events[:8]:
        direction = _idea_direction(event)
        structure = _idea_structure(direction)
        event_reaction = (price_reactions or {}).get(event.event_date or "", price_reaction)
        market_capture = evaluate_market_capture(event, event_reaction, consensus, identity.ticker)
        idea = TradeIdea(
            idea_id=_idea_id(identity.ticker, event),
            title=_idea_title(identity.ticker, event, direction),
            direction=direction,
            structure=structure,
            thesis=_idea_thesis(identity, event, direction),
            horizon=_horizon(event),
            catalyst=_catalyst(event),
            variant_perception=build_variant_perception(event, market_capture),
            source_events=[event],
            citations=list(event.citations),
            market_capture=market_capture,
            validated_claim_ids=([str(event.metrics["validated_claim_id"])] if event.metrics.get("validated_claim_id") else []),
            thesis_grade_status=str(event.metrics.get("thesis_grade_status") or "Unvalidated"),
            direction_rationale=_direction_rationale(event, direction),
            driver_template_summary=_driver_template_summary(event),
            normalization_status=str(event.metrics.get("normalization_status") or event.metrics.get("share_reconciliation_status") or ""),
            share_reconciliation=_share_reconciliation_from_metrics(event),
        )
        idea.driver_analysis = build_driver_analysis(event, metrics or [])
        idea.score = score_idea(idea)
        idea.monitor_items = build_monitor_items(idea)
        idea.peer_readthrough = build_peer_readthrough(identity.ticker, event)
        idea.signal_family = event.category
        ideas.append(idea)

    ideas.sort(key=lambda item: item.score.total if item.score else 0, reverse=True)
    attach_source_plan_to_ideas(ideas, source_plan)
    return ideas


def evaluate_market_capture(
    event: ChangeEvent,
    price_reaction: PriceReaction | None,
    consensus: ConsensusAdapter | None,
    ticker: str,
) -> MarketCapture:
    consensus_official = bool(consensus and getattr(consensus, "official_for_conviction", False))
    consensus_revision = (
        consensus.revision_since(ticker, event.event_date)
        if consensus and consensus_official
        else None
    )
    raw_price_move = price_reaction.reaction_pct if price_reaction else None
    price_move = (
        price_reaction.abnormal_reaction_pct
        if price_reaction and price_reaction.abnormal_reaction_pct is not None
        else raw_price_move
    )
    price_status = "available" if price_move is not None else "missing_price_reaction"
    if price_reaction and getattr(price_reaction, "status", "") not in {"", "available"}:
        price_status = str(price_reaction.status)
    if consensus is None:
        consensus_status = "missing_consensus_provider"
    elif not consensus_official:
        consensus_status = "unofficial_or_not_conviction_eligible"
    elif consensus_revision is None:
        consensus_status = "missing_point_in_time_revision"
    else:
        consensus_status = "available"
    gaps: list[str] = []
    if price_move is None:
        gaps.append(
            "Price reaction unavailable; load adjusted daily prices and event anchor windows for the ticker and benchmark."
        )
    if consensus_revision is None:
        if consensus is None:
            gaps.append("No consensus provider or CSV/manual consensus import is connected for this run.")
        elif not consensus_official:
            gaps.append("Consensus source is unofficial-only or not eligible for conviction; it cannot establish market capture.")
        else:
            gaps.append(
                "Official point-in-time consensus revisions are unavailable. A current FMP/Alpha Vantage snapshot can start the history, but market capture needs pre-event and post-event snapshots or imported historical rows."
            )

    if price_move is None and consensus_revision is None:
        category = "Unknown"
        explanation = (
            "Market capture is unavailable because event-specific price reaction and official "
            "point-in-time consensus revision are both missing."
        )
    elif consensus_revision is None:
        category = "Unknown"
        price_label = f"{price_move:+.1f}%" if price_move is not None else "available"
        explanation = (
            f"Price reaction is available ({price_label}), but official point-in-time consensus revision is missing. "
            "To judge whether this is priced in, use snapshots observed before and after the event; "
            "today's FMP/Alpha Vantage data can start future tracking but cannot be backfilled into prior event dates."
        )
    elif price_move is None:
        category = "Unknown"
        explanation = (
            f"Consensus revision is available ({consensus_revision:+.1f}%), but event-specific price reaction is missing. "
            "To judge whether this is priced in, connect daily prices with an event anchor date and 1/5/20 trading-day windows."
        )
    elif abs(price_move) <= 2 and abs(consensus_revision) <= 0.5:
        category = "Uncaptured"
        explanation = "Evidence changed, but neither price nor consensus has materially reacted."
    elif abs(price_move) <= 6 and abs(consensus_revision) <= 2:
        category = "Partially captured"
        explanation = "The stock moved, but the reaction does not look large relative to the evidence."
    elif event.direction == "negative" and price_move > 5:
        category = "Possibly overcaptured"
        explanation = "The stock rallied despite negative evidence; check whether another catalyst dominates."
    else:
        category = "Mostly captured"
        explanation = "The market reaction appears meaningful relative to the detected evidence."

    required_inputs = _market_capture_required_inputs(price_status, consensus_status, event)
    diagnosis = _market_capture_diagnosis(price_status, consensus_status, category)
    capture_mode = _capture_mode(price_status, consensus_status, category)

    return MarketCapture(
        category=category,
        price_reaction_pct=raw_price_move,
        consensus_revision_pct=consensus_revision,
        narrative_saturation="Not connected",
        explanation=explanation,
        data_gaps=gaps,
        benchmark_ticker=price_reaction.benchmark_ticker if price_reaction else None,
        benchmark_reaction_pct=price_reaction.benchmark_reaction_pct if price_reaction else None,
        abnormal_reaction_pct=price_reaction.abnormal_reaction_pct if price_reaction else None,
        volatility_adjusted_move=price_reaction.volatility_adjusted_move if price_reaction else None,
        volume_ratio=price_reaction.volume_ratio if price_reaction else None,
        beta=price_reaction.beta if price_reaction else None,
        consensus_official=consensus_official,
        price_status=price_status,
        consensus_status=consensus_status,
        diagnosis=diagnosis,
        required_inputs=required_inputs,
        point_in_time_note=(
            "Market-capture claims require event-specific price windows and consensus snapshots "
            "observed on or before the event/post-event dates; current consensus is not backfilled."
        ),
        capture_mode=capture_mode,
    )


def _market_capture_required_inputs(
    price_status: str,
    consensus_status: str,
    event: ChangeEvent,
) -> list[str]:
    rows: list[str] = []
    if price_status != "available":
        rows.append(
            f"Adjusted daily price bars for {event.event_date or 'the event date'} with 1/5/20-trading-day windows."
        )
    if consensus_status == "missing_consensus_provider":
        rows.append(
            "Connect Alpha Vantage/FMP/Finnhub where available or use CSV/manual consensus import for target, estimate, and recommendation snapshots."
        )
    elif consensus_status == "unofficial_or_not_conviction_eligible":
        rows.append(
            "Add official or licensed/manual point-in-time consensus snapshots; unofficial data can only be supporting context."
        )
    elif consensus_status == "missing_point_in_time_revision":
        rows.append(
            "Create or import official point-in-time snapshots around the event: one pre-event and one post-event for target/EPS/revenue/recommendations. Current provider data can seed future history but cannot prove prior market capture."
        )
    if event.category in {"financial_kpi", "margin"}:
        rows.append("Match the consensus metric to the affected driver, such as revenue, EPS, margin, or target price.")
    return _dedupe_strings(rows)


def _market_capture_diagnosis(price_status: str, consensus_status: str, category: str) -> str:
    if category != "Unknown":
        return f"Classified as {category}; price status {price_status}, consensus status {consensus_status}."
    if price_status != "available" and consensus_status != "available":
        return f"Cannot classify: price status {price_status}; consensus status {consensus_status}."
    if price_status != "available":
        return f"Cannot classify: consensus is available but price status is {price_status}."
    if consensus_status != "available":
        if consensus_status == "missing_point_in_time_revision":
            return "Cannot classify: price reaction is available, but consensus history is missing for the event window."
        return f"Cannot classify: price reaction is available but consensus status is {consensus_status}."
    return "Cannot classify from available inputs."


def _capture_mode(price_status: str, consensus_status: str, category: str) -> str:
    if category != "Unknown" and price_status == "available" and consensus_status == "available":
        return "Consensus-confirmed"
    if price_status == "available" and consensus_status != "available":
        return "Price-only"
    if price_status != "available" and consensus_status != "available":
        return "Unavailable"
    return "Unclassified"


def score_idea(
    idea: TradeIdea,
    valuation: ValuationResult | None = None,
    evidence: EvidenceLedger | None = None,
) -> ScoreBreakdown:
    event = idea.source_events[0]
    capture = idea.market_capture
    evidence_items = []
    if evidence:
        claim = next((item for item in evidence.claims if item.idea_id == idea.idea_id), None)
        if claim:
            evidence_items = [item for item in evidence.items if item.claim_id == claim.claim_id]
    has_primary = any(item.stance == "Supports" and item.source_tier == 1 for item in evidence_items)
    secondary_supported = bool(idea.promotion_decision and idea.promotion_decision.eligible)
    unresolved_counter_item = next((
        item for item in evidence_items
        if item.stance == "Contradicts" and item.materiality >= 3 and item.unresolved
    ), None)
    unresolved_counter = bool(unresolved_counter_item)
    complete_citations = sum(
        1 for citation in event.citations
        if citation.url and citation.snippet and (citation.accession or citation.section)
    )
    evidence_strength = min(
        25,
        (20 if has_primary else 18 if secondary_supported else 8 if event.citations else 0)
        + min(5, complete_citations * 2),
    )
    valuation_payoff = 0
    if valuation and valuation.status == "Available":
        available_cases = sum(case.fair_value is not None for case in valuation.cases)
        valuation_payoff = min(20, available_cases * 5 + min(5, len(valuation.bridge)))
    thesis_specificity = sum((
        3 if idea.direction else 0,
        3 if idea.catalyst else 0,
        3 if idea.horizon else 0,
        3 if idea.variant_perception else 0,
        3 if any(item.metric and item.deadline for item in idea.monitor_items) else 0,
    ))
    novelty = {
        "Uncaptured": 15,
        "Partially captured": 12,
        "Mostly captured": 5,
        "Possibly overcaptured": 4,
        "Unknown": 6,
    }.get(capture.category if capture else "Unknown", 6)
    catalyst_timing = round(_timing_score(event.event_date) / 10)
    market_capture = _market_capture_score(capture)
    reproducibility = min(5, complete_citations + (1 if event.event_date else 0))
    total = (
        evidence_strength + valuation_payoff + thesis_specificity + novelty
        + catalyst_timing + market_capture + reproducibility
    )
    rationale = [
        f"Evidence: {evidence_strength}/25; "
        + ("Tier 1 support present." if has_primary else "conditional Tier 3 substitution qualified." if secondary_supported else "Tier 1 support not yet established."),
        f"Valuation and payoff support: {valuation_payoff}/20.",
        f"Thesis specificity and contradiction handling: {thesis_specificity}/15.",
        f"Novelty {novelty}/15; catalyst timing {catalyst_timing}/10; market capture {market_capture}/10.",
        f"Reproducibility: {reproducibility}/5.",
    ]
    breakdown = ScoreBreakdown(
        total=max(0, min(100, total)),
        evidence_strength=evidence_strength,
        novelty=novelty,
        magnitude=valuation_payoff,
        timing=catalyst_timing,
        market_capture=market_capture,
        data_confidence=reproducibility,
        rationale=rationale,
        thesis_specificity=thesis_specificity,
        valuation_payoff=valuation_payoff,
        catalyst_timing=catalyst_timing,
        reproducibility=reproducibility,
    )
    caps: list[tuple[int, str]] = []
    if secondary_supported:
        caps.append((75, "High-Conviction is secondary-supported pending Tier 1 confirmation."))
    elif evidence is not None and not has_primary:
        caps.append((55, "No Tier 1 supporting evidence."))
    if unresolved_counter:
        statement = getattr(unresolved_counter_item, "statement", "")
        caps.append((70, (
            f"Unresolved contradiction: {statement}"
            if statement else "A material contradiction remains unresolved."
        )))
    if caps:
        cap, cap_reason = min(caps, key=lambda item: item[0])
        breakdown.total = min(breakdown.total, cap)
        breakdown.score_cap = cap
        breakdown.score_cap_reason = cap_reason
        breakdown.rationale.append(f"Score capped at {cap}: {cap_reason}")
    if capture and not capture.consensus_official:
        breakdown.rationale.append(
            "Official consensus is unavailable, so market-capture confidence is limited even when the idea remains research-ready."
        )
    if capture and capture.capture_mode == "Price-only":
        breakdown.rationale.append(
            "Market capture is price-only: event price reaction is available, but point-in-time analyst expectation revision is not."
        )
    breakdown.research_quality = breakdown.total
    breakdown.valuation_completeness = round(valuation_payoff / 20 * 100) if valuation_payoff else 0
    breakdown.evidence_strength_score = round(evidence_strength / 25 * 100) if evidence_strength else 0
    breakdown.market_capture_confidence = round(market_capture / 10 * 100) if market_capture else 0
    monitor_quality = 100 if any(item.metric and item.operator and item.deadline for item in idea.monitor_items) else 40 if idea.monitor_items else 0
    payoff_quality = 100 if idea.payoff_model and idea.payoff_model.status == "Available" else 40 if idea.scenarios else 0
    breakdown.actionability = round((thesis_specificity / 15 * 40) + (catalyst_timing / 10 * 20) + (monitor_quality * 0.25) + (payoff_quality * 0.15))
    return breakdown


def _market_capture_score(capture: MarketCapture | None) -> int:
    if not capture:
        return 2
    if capture.capture_mode == "Price-only":
        return 5
    if capture.capture_mode == "Unavailable":
        return 2
    return {
        "Uncaptured": 10,
        "Partially captured": 8,
        "Mostly captured": 4,
        "Possibly overcaptured": 3,
        "Unknown": 3,
    }.get(capture.category, 3)


def apply_valuation_context(ideas: list[TradeIdea], valuation: ValuationResult) -> None:
    for idea in ideas:
        if not idea.score:
            continue
        if valuation.status != "Available":
            idea.score.rationale.append("Valuation is insufficient; no payoff was inferred from the quality score.")
            continue
        idea.score.rationale.append(
            f"Internal {valuation.template.lower()} valuation supplies explicit scenario exit values; "
            "it does not mechanically adjust the quality score."
        )
        if valuation.disagreement_pct is not None:
            idea.score.rationale.append(
                f"Internal probability-weighted value differs from analyst consensus by "
                f"{valuation.disagreement_pct:+.1f}%."
            )


def build_variant_perception(event: ChangeEvent, capture: MarketCapture) -> str:
    if event.metrics.get("thesis_grade_status") in {"Watch Item", "Not thesis-grade"}:
        return (
            "No variant perception should be stated yet; the source claim is watch-only or not thesis-grade "
            "until exact directional evidence is validated."
        )
    direction = "positive" if event.direction == "positive" else "negative"
    if capture.category == "Uncaptured":
        return (
            f"The market may still be treating this as old information, while the filing evidence "
            f"shows a fresh {direction} change in {event.category.replace('_', ' ')}."
        )
    if capture.category == "Partially captured":
        return (
            "The market noticed the signal, but the move may not fully reflect the size, durability, "
            "or second-order impact of the evidence."
        )
    if capture.category == "Mostly captured":
        return (
            "The obvious version of the idea may already be reflected in price. Look for a sharper "
            "angle, such as duration, peer read-through, or consensus line-item lag."
        )
    return "Variant view needs price, consensus, and narrative data before it can be stated strongly."


def build_payoff_model(
    idea: TradeIdea,
    valuation: ValuationResult,
    entry_price: float | None,
    borrow_cost_pct: float | None = None,
    transaction_cost_pct: float = 0.10,
    dividend_return_pct: float = 0.0,
    hedge_ratio: float | None = None,
    scenario_exit_values: dict[str, float] | None = None,
    scenario_probabilities: dict[str, float] | None = None,
    calibrated_probability: float | None = None,
    calibration_sample_size: int = 0,
) -> PayoffModel:
    if idea.direction == "Short" and borrow_cost_pct is None:
        borrow_cost_pct = 1.0
    calibrated = calibrated_probability is not None and calibration_sample_size >= 30
    user_probabilities = _normalized_scenario_probabilities(scenario_probabilities)
    probability_source = "calibrated_model" if calibrated else "user_assigned" if user_probabilities else "illustrative_default"
    provenance = ProbabilityProvenance(
        source=probability_source,
        status="Calibrated" if calibrated else "Uncalibrated",
        sample_size=calibration_sample_size,
        minimum_sample_size=30,
        note=(
            "Probabilities use resolved outcomes from the same signal family and horizon."
            if calibrated
            else "User-assigned probabilities are normalized to 100% and excluded from ranking."
            if user_probabilities
            else "Illustrative 25%/50%/25% probabilities are editable and excluded from ranking."
        ),
    )
    gaps: list[str] = []
    if entry_price is None or entry_price <= 0:
        gaps.append("A current entry price is required.")
    if idea.direction == "Relative Value" and hedge_ratio is None:
        gaps.append("A hedge ratio and explicit peer-leg scenario values are required for pair expected value.")

    case_probabilities = user_probabilities or {"Bear": 0.25, "Base": 0.50, "Bull": 0.25}
    scenarios: list[Scenario] = []
    valuation_available = valuation.status == "Available"
    valuation_cases = valuation.cases if valuation_available else _payoff_envelope_cases(idea, entry_price)
    if not valuation_available:
        gaps.append("Internal valuation does not provide scenario fair values; using labelled payoff-envelope assumptions only.")
    for valuation_case in valuation_cases:
        override_exit = _scenario_exit_override(scenario_exit_values, valuation_case.name)
        exit_value = override_exit if override_exit is not None else valuation_case.fair_value
        assumptions_for_case = list(valuation_case.assumptions)
        if override_exit is not None:
            assumptions_for_case.append(
                f"User supplied {valuation_case.name} exit anchor: {override_exit:.2f}."
            )
        gross = _directional_return(idea.direction, entry_price, exit_value)
        net = None
        if gross is not None and idea.direction == "Long":
            net = gross + dividend_return_pct - transaction_cost_pct
        elif gross is not None and idea.direction == "Short" and borrow_cost_pct is not None:
            net = gross - borrow_cost_pct - dividend_return_pct - transaction_cost_pct
        scenarios.append(Scenario(
            name=valuation_case.name,
            probability=case_probabilities.get(valuation_case.name, valuation_case.probability),
            upside_downside_pct=net if net is not None else gross if gross is not None else 0.0,
            assumptions=assumptions_for_case,
            probability_status=provenance.status,
            exit_value=exit_value,
            entry_value=entry_price,
            gross_return_pct=gross,
            net_return_pct=net,
            currency=valuation.currency,
        ))
    if calibrated:
        _apply_calibrated_probability(scenarios, calibrated_probability)
    assumptions = [
        ScenarioAssumption(
            step.case, step.metric, step.value, step.unit, step.source, step.formula,
        )
        for step in valuation.bridge
    ]
    assumption_provenance = [
        AssumptionProvenance(
            "transaction_cost_pct", transaction_cost_pct, "Default app assumption",
            note="Editable estimate of entry/exit transaction friction.",
        ),
        AssumptionProvenance(
            "dividend_return_pct", dividend_return_pct, "Default app assumption",
            note="Set to zero until dividend timing is explicitly modelled.",
        ),
        AssumptionProvenance(
            "scenario_probabilities",
            "/".join(f"{case_probabilities[name] * 100:.1f}" for name in ("Bear", "Base", "Bull")),
            provenance.source,
            status=provenance.status,
            note=provenance.note,
        ),
    ]
    if scenario_exit_values:
        for case_name, exit_value in scenario_exit_values.items():
            assumption_provenance.append(AssumptionProvenance(
                f"{case_name}_exit", exit_value, "User supplied assumption",
                note="Editable exit anchor used to compute illustrative scenario net return.",
            ))
    if idea.direction == "Short":
        assumption_provenance.append(AssumptionProvenance(
            "borrow_cost_pct", borrow_cost_pct, "Default app assumption",
            note="Editable annualized borrow-cost placeholder for liquid US large caps.",
        ))
    if idea.direction == "Relative Value":
        assumption_provenance.append(AssumptionProvenance(
            "hedge_ratio", hedge_ratio, "User override or pre-event return estimate required",
            status="Missing" if hedge_ratio is None else "Estimated",
        ))
    ev = expected_value(scenarios)
    complete = bool(scenarios and ev is not None and not any(scenario.net_return_pct is None for scenario in scenarios))
    if valuation_available and complete and not gaps:
        status = "Available"
    elif complete and entry_price and entry_price > 0 and idea.direction in {"Long", "Short"}:
        status = "Envelope"
    else:
        status = "Insufficient data"
    payoff_completeness = PayoffCompleteness(
        "Complete" if complete else "Incomplete",
        missing_inputs=list(gaps) if not complete else [],
        note=(
            "Scenario returns are valuation-anchored."
            if valuation_available and complete
            else "Scenario returns use a labelled payoff envelope, not internal fair value."
            if complete else "Expected value is unavailable until scenario net returns are complete."
        ),
    )
    return PayoffModel(
        status=status,
        structure=idea.direction,
        entry_price=entry_price,
        currency=valuation.currency,
        scenarios=scenarios,
        assumptions=assumptions,
        expected_value_pct=ev,
        probability_provenance=provenance,
        transaction_cost_pct=transaction_cost_pct,
        dividend_return_pct=dividend_return_pct,
        borrow_cost_pct=borrow_cost_pct,
        hedge_ratio=hedge_ratio,
        rank_eligible=bool(calibrated and status == "Available"),
        data_gaps=gaps,
        payoff_completeness=payoff_completeness,
        assumption_provenance=assumption_provenance,
    )


def build_scenarios(
    idea: TradeIdea,
    valuation: ValuationResult | None = None,
    entry_price: float | None = None,
) -> list[Scenario]:
    if valuation is None:
        return []
    return build_payoff_model(idea, valuation, entry_price).scenarios


def build_monitor_items(idea: TradeIdea) -> list[MonitorItem]:
    event = idea.source_events[0]
    category = event.category
    deadline = _monitor_deadline(event.event_date)
    if idea.direction == "Watch":
        return [
            MonitorItem(
                criterion="Claim validation",
                data_source="SEC/issuer source excerpts",
                cadence="Each refresh",
                confirm_trigger="Exact current/prior text proves a substantive, directional, driver-linked claim.",
                break_trigger="The excerpt remains boilerplate, accounting-only, vague, or unmapped.",
                metric="thesis_grade_status",
                operator="==",
                confirm_threshold=None,
                break_threshold=None,
                deadline=deadline,
                source_field="validated_claims.status",
            ),
            MonitorItem(
                criterion="Next source check",
                data_source="Source plan",
                cadence="After source-plan refresh",
                confirm_trigger="Transcript, issuer release, consensus revision, or filing exhibit corroborates the claim.",
                break_trigger="Follow-up sources contradict the claim or show no material impact.",
                metric="source_plan_outcome",
                operator="==",
                confirm_threshold=None,
                break_threshold=None,
                deadline=deadline,
                source_field="source_plan.requests",
            ),
        ]
    direction_sign = 1 if idea.direction == "Long" else -1
    items = [
        MonitorItem(
            criterion="Consensus reaction",
            data_source="Consensus estimates provider",
            cadence="Daily after filings and earnings",
            confirm_trigger="Revenue/EPS or line-item estimates revise in the thesis direction.",
            break_trigger="Consensus revises against the thesis or ignores a supposedly material signal.",
            metric="consensus_revision_pct",
            operator=">=" if direction_sign > 0 else "<=",
            confirm_threshold=3.0 * direction_sign,
            break_threshold=-3.0 * direction_sign,
            deadline=deadline,
            source_field="consensus.revisions",
        ),
        MonitorItem(
            criterion="Market reaction",
            data_source="Price/volume provider",
            cadence="Daily",
            confirm_trigger="Price starts to move in the thesis direction without full multiple exhaustion.",
            break_trigger="Adverse price move on high volume without new contradictory evidence.",
            metric="abnormal_reaction_pct",
            operator=">=" if direction_sign > 0 else "<=",
            confirm_threshold=5.0 * direction_sign,
            break_threshold=-5.0 * direction_sign,
            deadline=deadline,
            source_field="market_capture.abnormal_reaction_pct",
        ),
    ]
    if category in {"risk_factors", "litigation", "debt_liquidity"}:
        items.append(
            MonitorItem(
                criterion=f"{category.replace('_', ' ').title()} language",
                data_source="Next 10-Q/10-K/8-K",
                cadence="Each filing",
                confirm_trigger="Language escalates, quantifies impact, or adds new constraints.",
                break_trigger="Language disappears, is narrowed, or impact is immaterial.",
                metric="filing_language_severity",
                operator=">=" if idea.direction == "Short" else "<=",
                confirm_threshold=4.0 if idea.direction == "Short" else 2.0,
                break_threshold=2.0 if idea.direction == "Short" else 4.0,
                deadline=deadline,
                source_field=f"events.{category}",
            )
        )
    elif category in {"margin", "financial_kpi"}:
        items.append(
            MonitorItem(
                criterion="KPI follow-through",
                data_source="SEC companyfacts + earnings call",
                cadence="Quarterly",
                confirm_trigger="Margins, revenue, cash flow, or debt metrics keep moving in the thesis direction.",
                break_trigger="Metric mean-reverts or management calls the move temporary.",
                metric=str(event.metrics.get("metric_name") or "kpi_follow_through"),
                operator=">=" if direction_sign > 0 else "<=",
                confirm_threshold=0.0,
                break_threshold=0.0,
                deadline=deadline,
                source_field="financial_metrics.yoy_change_pct",
            )
        )
    elif category in {"tone_shift", "qa_evasion", "guidance_specificity_change"}:
        if category == "qa_evasion":
            criterion = "Q&A evasiveness"
            metric = "transcript_evasion_score"
            confirm = "Evasive language declines and management quantifies the previously avoided topic."
            broken = "Management repeats the evasion or removes disclosure detail."
            confirm_threshold = 0.0
            break_threshold = 1.0
        elif category == "guidance_specificity_change":
            criterion = "Guidance specificity"
            metric = "transcript_specificity_score"
            confirm = "Next call includes numeric metric, period, range, and citation-backed guidance."
            broken = "Management keeps the thesis vague or walks back quantified guidance."
            confirm_threshold = 4.0
            break_threshold = 1.0
        else:
            criterion = "Management tone shift"
            metric = "transcript_sentiment_shift"
            confirm = "Tone shift persists and is supported by filing language, KPIs, or consensus revisions."
            broken = "Tone reverts or filings/facts contradict the stated shift."
            confirm_threshold = 0.2 * direction_sign
            break_threshold = -0.2 * direction_sign
        items.append(
            MonitorItem(
                criterion=criterion,
                data_source="Issuer transcript + filing cross-check",
                cadence="Quarterly",
                confirm_trigger=confirm,
                break_trigger=broken,
                metric=metric,
                operator=">=" if direction_sign > 0 or category != "qa_evasion" else "<=",
                confirm_threshold=confirm_threshold,
                break_threshold=break_threshold,
                deadline=deadline,
                source_field="management_credibility.transcript_comparison",
            )
        )
    else:
        items.append(
            MonitorItem(
                criterion="Management commentary",
                data_source="Earnings transcript provider",
                cadence="Quarterly",
                confirm_trigger="Management repeats or sharpens the detected signal.",
                break_trigger="Management walks back the signal or gives offsetting detail.",
                metric="management_commentary_direction",
                operator="==",
                confirm_threshold=1.0 if direction_sign > 0 else -1.0,
                break_threshold=-1.0 if direction_sign > 0 else 1.0,
                deadline=deadline,
                source_field="management_credibility.transcript_comparison",
            )
        )
    return items


def _monitor_deadline(event_date: str | None) -> str | None:
    if not event_date:
        return None
    try:
        return (date.fromisoformat(event_date[:10]) + timedelta(days=120)).isoformat()
    except ValueError:
        return None


def build_driver_analysis(event: ChangeEvent, metrics: list[FinancialMetric]) -> DriverAnalysis:
    template = template_for_event(event)
    if event.category not in {"financial_kpi", "margin"}:
        return _text_event_driver_analysis(event, metrics)

    metric_name = str(event.metrics.get("metric_name") or _metric_name_from_title(event.title))
    if metric_name == "Shares":
        factors = _share_count_driver_factors(event, metrics)
        headline = (
            "Share-count evidence needs security-basis normalization before it can explain dilution or buyback."
            if event.metrics.get("normalization_required")
            else "Possible share-count drivers: " + "; ".join(factor.cause for factor in factors[:3]) + "."
        )
        return _driver_analysis(headline, factors[:5], template, event, metric_name)
    direction_word = "decline" if event.direction == "negative" else "increase"
    factors = _financial_driver_factors(metric_name, event, metrics)
    if not factors:
        missing_note = _missing_bridge_note(metric_name)
        factors = [
            DriverFactor(
                cause="Insufficient line-item detail",
                direction="unknown",
                confidence="Low",
                magnitude_hint="No decomposing line item was available",
                explanation=missing_note,
                citations=list(event.citations),
                missing_data_notes=[
                    "Connect segment-level company data, transcripts, or consensus detail for stronger attribution."
                ],
            )
        ]
    headline = (
        f"Possible drivers of the {metric_name.lower()} {direction_word}: "
        + "; ".join(factor.cause for factor in factors[:3])
        + "."
    )
    return _driver_analysis(headline, factors[:5], template, event, metric_name)


def _driver_analysis(
    headline: str,
    factors: list[DriverFactor],
    template: object,
    event: ChangeEvent,
    metric_name: str,
) -> DriverAnalysis:
    bridge = _causal_bridge_payload(metric_name, event, factors)
    return DriverAnalysis(
        headline=headline,
        factors=factors,
        template=template,
        bridge_status=bridge["bridge_status"],
        primary_driver=bridge["primary_driver"],
        mechanism=bridge["mechanism"],
        evidence_needed=bridge["evidence_needed"],
        peer_metric_checks=bridge["peer_metric_checks"],
        valuation_implication=bridge["valuation_implication"],
        credit_implication=bridge["credit_implication"],
        falsification_tests=bridge["falsification_tests"],
        data_gaps=bridge["data_gaps"],
    )


def _causal_bridge_payload(
    metric_name: str,
    event: ChangeEvent,
    factors: list[DriverFactor],
) -> dict[str, object]:
    family = _driver_family(metric_name, event)
    specs = {
        "revenue": {
            "primary_driver": "Revenue / demand",
            "mechanism": "Revenue changes must be decomposed into volume, price/ASP, mix, FX, segment demand, and customer/channel evidence before they become a thesis.",
            "evidence_needed": [
                "Segment revenue and KPI table.",
                "Volume/price, ASP, units, bookings, traffic, or customer demand evidence.",
                "Management commentary and consensus revenue revisions tied to the same fiscal period.",
            ],
            "peer_metric_checks": ["Peer revenue growth", "Peer volume/ASP or demand KPI", "Sector demand data"],
            "valuation_implication": "Revenue bridge affects growth assumptions, operating leverage, FCF, and sales/multiple durability.",
            "credit_implication": "Revenue weakness can reduce interest coverage and liquidity runway; revenue strength supports scale and refinancing capacity.",
            "falsification_tests": [
                "Revenue growth is isolated to FX, accounting, one-time items, or non-core segment.",
                "Peer/industry demand data contradicts the reported direction.",
            ],
        },
        "margin": {
            "primary_driver": "Gross margin / mix",
            "mechanism": "Gross profit or margin changes should bridge through revenue, COGS, pricing, mix, incentives, warranty, logistics, input costs, and segment margins.",
            "evidence_needed": [
                "Revenue and gross profit for the same fiscal period.",
                "COGS/input-cost, ASP/pricing, product mix, warranty, incentive, or segment margin detail.",
                "MD&A margin discussion, earnings deck, transcript Q&A, and peer margin comparison.",
            ],
            "peer_metric_checks": ["Peer gross margin %", "Peer COGS/revenue", "Peer ASP/mix/volume where available"],
            "valuation_implication": "Durable margin changes affect EPS/FCF conversion and valuation multiple support.",
            "credit_implication": "Margin durability affects cash conversion, covenant cushion, and refinancing confidence.",
            "falsification_tests": [
                "Management describes the margin move as temporary or one-time.",
                "COGS, mix, pricing, warranty, or peer margin data does not support the change.",
            ],
        },
        "operating": {
            "primary_driver": "Operating leverage",
            "mechanism": "Operating income changes should bridge from revenue and gross margin through SG&A, R&D, sales/marketing, restructuring, and segment operating income.",
            "evidence_needed": [
                "Revenue, gross profit, and major opex lines for the same period.",
                "Segment operating income or cost-action bridge.",
                "Management explanation of whether opex growth is investment, one-time, or deleverage.",
            ],
            "peer_metric_checks": ["Peer operating margin", "Peer opex/revenue", "Peer segment operating income"],
            "valuation_implication": "Operating leverage changes flow directly into EBIT/EBITDA, EPS, FCF, and multiple durability.",
            "credit_implication": "Operating-income pressure can reduce interest coverage and debt capacity.",
            "falsification_tests": [
                "Opex growth is one-time, reclassified, or explicitly investment-led with evidence of future returns.",
                "Operating margin trend reverses or peers show the opposite pattern.",
            ],
        },
        "net_income": {
            "primary_driver": "Net income / EPS bridge",
            "mechanism": "Net income and EPS changes should bridge from operating profit through interest, tax, other below-the-line items, and share count.",
            "evidence_needed": [
                "Operating income, interest expense, tax expense, and other income/expense bridge.",
                "Share-count reconciliation for EPS or per-share claims.",
                "Management/filing explanation of recurring versus one-time below-the-line items.",
            ],
            "peer_metric_checks": ["Peer net margin", "Peer interest burden", "Peer tax rate", "Peer share-count trend"],
            "valuation_implication": "Net income bridge affects EPS, P/E, buyback capacity, and quality-of-earnings assessment.",
            "credit_implication": "Below-the-line pressure matters for retained earnings, coverage, and equity cushion but should not be confused with operating deterioration.",
            "falsification_tests": [
                "The change is driven by one-time tax, FX, investment, or accounting items.",
                "Operating profit and cash flow do not corroborate the net-income direction.",
            ],
        },
        "liquidity": {
            "primary_driver": "Cash generation / liquidity",
            "mechanism": "Cash balance changes should reconcile beginning cash plus operating cash flow, capex, dividends, buybacks, debt issuance/repayment, M&A, and FX to ending cash.",
            "evidence_needed": [
                "Cash-flow statement with operating cash flow, capex, dividends, repurchases, and financing flows.",
                "Debt footnote, maturity table, restricted cash, and liquidity disclosure.",
                "Capital allocation commentary and rating/spread evidence when relevant.",
            ],
            "peer_metric_checks": ["Peer free cash flow", "Peer net cash/debt", "Peer capital return and liquidity metrics"],
            "valuation_implication": "Cash generation affects FCF yield, buyback capacity, downside support, and net-cash valuation adjustments.",
            "credit_implication": "Liquidity directly affects refinancing risk, covenant cushion, and credit downside.",
            "falsification_tests": [
                "Cash increase came from debt issuance, asset sales, working-capital timing, or restricted cash rather than durable FCF.",
                "Free cash flow, debt maturity, or capital return evidence contradicts the liquidity thesis.",
            ],
        },
        "share_count": {
            "primary_driver": "Share count / capital return",
            "mechanism": "Share-count changes only affect per-share value after reconciling ordinary shares, ADS ratio, split history, weighted-average basis, repurchases, and issuance.",
            "evidence_needed": [
                "Share reconciliation table and ADS/ordinary-share ratio.",
                "Buyback and issuance table.",
                "Split/corporate-action history and weighted-average versus period-end basis.",
            ],
            "peer_metric_checks": ["Peer share-count change", "Peer buyback yield", "Peer SBC/issuance trend"],
            "valuation_implication": "Comparable share-count reduction can lift EPS/FCF per share; unreconciled basis changes should not alter valuation.",
            "credit_implication": "Buybacks can reduce liquidity and creditor protection if funded by debt or excess cash drawdown.",
            "falsification_tests": [
                "The change is caused by ADS ratio, split, XBRL concept, or weighted-average/period-end mismatch.",
                "Buyback table and diluted share count do not corroborate the per-share claim.",
            ],
        },
        "debt": {
            "primary_driver": "Debt / liquidity",
            "mechanism": "Debt changes should bridge through issuance, repayment, maturity schedule, interest-rate mix, covenants, cash balance, and refinancing needs.",
            "evidence_needed": [
                "Debt footnote and maturity schedule.",
                "Cash/liquidity disclosure and interest expense trend.",
                "Rating action, spread data, or covenant/refinancing commentary.",
            ],
            "peer_metric_checks": ["Peer net debt", "Peer interest coverage", "Peer debt maturity/refinancing risk"],
            "valuation_implication": "Debt changes affect EV, equity optionality, FCF to equity, and downside risk.",
            "credit_implication": "Debt bridge is core credit evidence: leverage, liquidity, coverage, and maturity wall.",
            "falsification_tests": [
                "Debt increase is matched by cash, working-capital seasonality, or low-risk refinancing.",
                "Coverage, maturity, and liquidity evidence show risk is neutralized.",
            ],
        },
        "guidance": {
            "primary_driver": "Guidance / expectations",
            "mechanism": "Guidance matters when exact metric, period, range, and management quote change investor expectations and later consensus revisions.",
            "evidence_needed": [
                "Exact current and prior guidance quote with metric, period, range/value, currency, and speaker/source.",
                "Post-event EPS/revenue/target/recommendation revisions observed point-in-time.",
                "Transcript Q&A and issuer release to separate boilerplate from real outlook change.",
            ],
            "peer_metric_checks": ["Peer guidance changes", "Peer consensus revisions", "Peer price reaction around guidance events"],
            "valuation_implication": "Guidance changes should alter near-term revenue, margin, EPS, or FCF assumptions before they affect fair value.",
            "credit_implication": "Guidance can matter for credit only when it changes cash flow, liquidity, leverage, or refinancing outlook.",
            "falsification_tests": [
                "The language is boilerplate, accounting-only, vague, or not different from prior filing/call.",
                "Consensus and management Q&A do not corroborate the alleged guidance change.",
            ],
        },
        "regulation": {
            "primary_driver": "Regulation / legal risk",
            "mechanism": "Regulatory or legal signals need official scope, timing, probability, and financial-exposure evidence before they can explain valuation or risk premium.",
            "evidence_needed": [
                "Regulator, court, issuer, or SEC/6-K/8-K confirmation.",
                "Estimated exposure, affected product/region, timing, and remediation costs.",
                "Management response and peer/industry read-through.",
            ],
            "peer_metric_checks": ["Peer regulatory exposure", "Peer legal reserve/disclosure changes", "Sector risk-premium reaction"],
            "valuation_implication": "Regulatory/legal bridge affects required return, probability-weighted liability, growth runway, and multiple.",
            "credit_implication": "Material legal exposure can affect liquidity, covenants, leverage, and rating outlook.",
            "falsification_tests": [
                "The disclosure is unchanged boilerplate or immaterial to operations.",
                "Official records or issuer filings contradict the alleged exposure.",
            ],
        },
        "management": {
            "primary_driver": "Management credibility / execution",
            "mechanism": "Management-language signals need specificity, repeated commitments, later KPI outcomes, and filing cross-checks before they can support a thesis.",
            "evidence_needed": [
                "Speaker-level transcript quote or meeting/proxy item.",
                "Prior promise and current outcome evidence.",
                "Cross-check against filings, XBRL facts, consensus revisions, and price reaction.",
            ],
            "peer_metric_checks": ["Peer management tone", "Peer guidance specificity", "Peer KPI delivery versus promises"],
            "valuation_implication": "Credibility affects confidence in assumptions, not the assumptions themselves unless KPIs or guidance are quantified.",
            "credit_implication": "Management credibility matters for capital allocation, refinancing discipline, leverage targets, and covenant communication.",
            "falsification_tests": [
                "The claim is vague, promotional, contradicted by facts, or not observable in later outcomes.",
                "Filings or KPI results fail to corroborate the promise.",
            ],
        },
        "acquisition_accounting": {
            "primary_driver": "Acquisition accounting / capital allocation",
            "mechanism": "Goodwill changes must bridge through an identified transaction, consideration, purchase-price allocation, acquired economics, integration, and impairment risk; the balance increase is not intrinsically bullish or bearish.",
            "evidence_needed": [
                "Acquisition agreement, closing announcement, and purchase-price allocation.",
                "Consideration paid, acquired revenue/profit, identifiable intangibles, and goodwill by segment.",
                "Synergy targets, integration costs, funding, ROIC, and impairment indicators.",
            ],
            "peer_metric_checks": ["Peer acquisition multiples", "Peer goodwill/assets", "Peer post-deal ROIC and impairment history"],
            "valuation_implication": "The thesis depends on acquired cash flows and ROIC versus the purchase premium, not on goodwill growth itself.",
            "credit_implication": "Acquisition funding, liquidity use, leverage, and rating headroom determine the credit effect.",
            "falsification_tests": [
                "Acquired growth or synergies do not materialize.",
                "Purchase economics imply overpayment or later impairment risk.",
            ],
        },
        "investment_cycle": {
            "primary_driver": "Investment cycle / capital intensity",
            "mechanism": "Capex changes must bridge through project purpose, capacity, utilization, demand, depreciation, and incremental returns; higher spending is not intrinsically positive or negative.",
            "evidence_needed": [
                "Capex purpose, capacity, geography, and project timing.",
                "Capex/revenue, depreciation, utilization, operating cash flow, and FCF history.",
                "Management return targets and aligned peer investment plans.",
            ],
            "peer_metric_checks": ["Peer capex/revenue", "Peer capacity and utilization", "Peer FCF conversion"],
            "valuation_implication": "Capex reduces near-term FCF but can raise future revenue or margins if incremental returns exceed the cost of capital.",
            "credit_implication": "Funding needs, liquidity consumption, and coverage headroom determine the credit effect.",
            "falsification_tests": [
                "Demand, utilization, or incremental margins fail to support the investment.",
                "Capex remains elevated without a credible revenue, cost, or strategic payoff.",
            ],
        },
        "unmapped": {
            "primary_driver": "Unmapped metric",
            "mechanism": "The metric must be defined and mapped to an operating or accounting mechanism before directional interpretation.",
            "evidence_needed": ["Metric definition and source table.", "Comparable period and accounting basis.", "Related operating KPI or footnote."],
            "peer_metric_checks": ["Comparable peer metric only after canonical mapping"],
            "valuation_implication": "Unknown until the metric is mapped.",
            "credit_implication": "Unknown until the metric is mapped.",
            "falsification_tests": ["The metric cannot be reconciled to a comparable operating or accounting basis."],
        },
    }
    spec = specs.get(family, specs["unmapped"])
    gaps = _bridge_data_gaps(family, factors, event)
    status = _bridge_status(event, factors, gaps)
    return {
        "bridge_status": status,
        "primary_driver": spec["primary_driver"],
        "mechanism": spec["mechanism"],
        "evidence_needed": spec["evidence_needed"],
        "peer_metric_checks": spec["peer_metric_checks"],
        "valuation_implication": spec["valuation_implication"],
        "credit_implication": spec["credit_implication"],
        "falsification_tests": spec["falsification_tests"],
        "data_gaps": gaps,
    }


def _bridge_status(event: ChangeEvent, factors: list[DriverFactor], gaps: list[str]) -> str:
    status = str(event.metrics.get("thesis_grade_status") or "")
    if status in {"Watch Item", "Not thesis-grade"} or event.metrics.get("normalization_required"):
        return "Watch / needs validation"
    if not factors or all(factor.confidence == "Low" for factor in factors):
        return "Incomplete causal bridge"
    if gaps:
        return "Provisional causal bridge"
    return "Substantive causal bridge"


def _bridge_data_gaps(family: str, factors: list[DriverFactor], event: ChangeEvent) -> list[str]:
    gaps: list[str] = []
    for factor in factors:
        gaps.extend(factor.missing_data_notes)
    if not factors:
        gaps.append("No driver-specific bridge factors were identified.")
    if family == "margin" and not any(
        token in factor.cause.lower()
        for factor in factors
        for token in ("margin", "cost of revenue", "gross profit")
    ):
        gaps.append("Gross-margin bridge needs COGS, mix, price/ASP, volume, warranty, or segment-margin evidence.")
    if family == "liquidity" and not any(
        token in factor.cause.lower()
        for factor in factors
        for token in ("operating cash flow", "free cash flow", "capex", "dividend", "debt", "cash")
    ):
        gaps.append("Cash bridge needs cash-flow statement and financing/capital-return detail.")
    if family == "share_count" and event.metrics.get("normalization_required"):
        gaps.append("Share/security basis must be normalized before interpreting dilution or buybacks.")
    return _dedupe_strings(gaps)


def _driver_family(metric_name: str, event: ChangeEvent) -> str:
    explicit_family = str(event.metrics.get("driver_family") or "").strip()
    if explicit_family:
        return explicit_family
    policy_family = metric_policy_for(metric_name).driver_family
    if policy_family != "unmapped":
        return policy_family
    text = f"{metric_name} {event.category} {event.title} {event.metrics.get('economic_driver', '')}".lower()
    if any(token in text for token in ("share", "buyback", "dilution", "per share", "eps")):
        return "share_count" if "eps" not in text and "per share" not in text else "net_income"
    if any(token in text for token in ("guidance", "outlook", "expectation", "forecast")):
        return "guidance"
    if any(token in text for token in ("regulation", "regulatory", "litigation", "legal", "policy")):
        return "regulation"
    if any(token in text for token in ("management", "tone", "credibility", "evasion", "governance", "proxy")):
        return "management"
    if any(token in text for token in ("cash", "liquidity", "free cash flow", "operating cash flow")):
        return "liquidity"
    if any(token in text for token in ("debt", "borrow", "leverage")):
        return "debt"
    if "revenue" in text and not any(token in text for token in ("cost of revenue", "gross profit")):
        return "revenue"
    if any(token in text for token in ("gross profit", "gross margin", "margin", "cost of revenue", "mix")):
        return "margin"
    if any(token in text for token in ("operating income", "operating margin", "operating profit", "income from operations")):
        return "operating"
    if any(token in text for token in ("net income", "earnings", "profit attributable", "profit loss")):
        return "net_income"
    return "unmapped"


def _missing_bridge_note(metric_name: str) -> str:
    lower = metric_name.lower()
    if "gross" in lower or "margin" in lower:
        return (
            "The app detected the KPI change, but it still needs revenue, COGS, pricing, mix, volume, "
            "warranty, input-cost, or segment-margin evidence before attributing a gross-profit driver."
        )
    if "operating" in lower:
        return (
            "The app detected the KPI change, but it still needs revenue, gross-margin, and opex bridge "
            "evidence before attributing an operating-profit driver."
        )
    if any(token in lower for token in ("net income", "earnings", "eps", "per share")):
        return (
            "The app detected the KPI change, but it still needs operating profit, interest, tax, and "
            "share-count bridge evidence before attributing an EPS or net-income driver."
        )
    return (
        "The app detected the KPI change, but related line-item detail is insufficient to attribute a causal driver."
    )


def _share_count_driver_factors(event: ChangeEvent, metrics: list[FinancialMetric]) -> list[DriverFactor]:
    shares = next((metric for metric in metrics if metric.name == "Shares"), None)
    yoy = _metric_yoy(shares)
    if event.metrics.get("normalization_required"):
        return [
            DriverFactor(
                cause="Share-count basis mismatch risk",
                direction="unknown",
                confidence="High",
                magnitude_hint=(
                    f"Shares {float(event.metrics.get('yoy_change_pct')):+.1f}%"
                    if isinstance(event.metrics.get("yoy_change_pct"), (int, float))
                    else "Large share-count move"
                ),
                explanation=(
                    "The share-count change is too large to treat as ordinary dilution or repurchase evidence "
                    "without verifying ADR ratio, ordinary-share basis, split/corporate-action history, and the exact XBRL concept."
                ),
                citations=list(event.citations),
                missing_data_notes=[
                    "Check issuer annual report share reconciliation, buyback table, ADR ratio, and per-ADS basis before forming a trade thesis."
                ],
            )
        ]
    if yoy is None:
        return [
            DriverFactor(
                cause="Share-count comparison unavailable",
                direction="unknown",
                confidence="Low",
                magnitude_hint="No comparable share-count history",
                explanation="The app found a share-count figure but not enough comparable history to identify dilution or buyback.",
                citations=list(event.citations),
                missing_data_notes=["Add split-adjusted share count or manual share reconciliation data."],
            )
        ]
    return [
        _factor(
            "Dilution / share-count growth" if yoy > 0 else "Buyback / share-count reduction",
            "negative" if yoy > 0 else "positive",
            "Medium",
            f"Shares {yoy:+.1f}%",
            (
                "Share-count growth can dilute per-share results if the basis is comparable."
                if yoy > 0
                else "Share-count reduction can support per-share value if driven by repurchases rather than security-basis changes."
            ),
            event,
        )
    ]


def build_peer_readthrough(ticker: str, event: ChangeEvent) -> list[PeerReadthrough]:
    universe = peer_universe_for(ticker)
    if universe.status != "Configured":
        return [
            PeerReadthrough(
                peer_ticker="Unconfigured",
                evidence_status="Unconfigured",
                relation="No direct evidence",
                conclusion=universe.reason,
                failure_status="unconfigured",
                failure_reason=universe.reason,
            )
        ]
    return [
        PeerReadthrough(
            peer_ticker=peer.ticker,
            evidence_status="Pending direct check",
            relation=peer.relationship,
            conclusion=(
                f"Direct peer check has not run yet for {peer.ticker}; the pipeline will replace this "
                "placeholder when SEC peer facts are available."
            ),
        )
        for peer in universe.peers
    ]


def ideas_with_changed_evidence_not_price_or_consensus(ideas: list[TradeIdea]) -> list[TradeIdea]:
    return [
        idea
        for idea in ideas
        if idea.market_capture
        and idea.market_capture.category in {"Uncaptured", "Partially captured"}
        and idea.source_events
        and idea.source_events[0].severity >= 3
    ]


def expected_value(scenarios: list[Scenario]) -> float | None:
    if not scenarios:
        return None
    is_auditable_model = any(scenario.exit_value is not None for scenario in scenarios)
    if is_auditable_model and any(scenario.net_return_pct is None for scenario in scenarios):
        return None
    total_probability = sum(scenario.probability for scenario in scenarios)
    if total_probability <= 0:
        return None
    return sum(
        scenario.probability
        * (scenario.net_return_pct if scenario.net_return_pct is not None else scenario.upside_downside_pct)
        for scenario in scenarios
    ) / total_probability


def calculate_pair_return(
    long_return_pct: float,
    short_return_pct: float,
    hedge_ratio: float,
    transaction_cost_pct: float,
) -> float:
    return long_return_pct - hedge_ratio * short_return_pct - transaction_cost_pct


def finalize_idea_research(
    ideas: list[TradeIdea],
    valuation: ValuationResult,
    evidence: EvidenceLedger,
    entry_price: float | None,
    calibration_lookup: Callable[[str, str], tuple[float | None, int]] | None = None,
    promotion_bundles: dict[str, PromotionEvidenceBundle] | None = None,
) -> list[IdeaGateResult]:
    claims_by_idea = {claim.idea_id: claim for claim in evidence.claims}
    items_by_claim = {
        claim.claim_id: [item for item in evidence.items if item.claim_id == claim.claim_id]
        for claim in evidence.claims
    }
    gate_results: list[IdeaGateResult] = []
    for idea in ideas:
        claim = claims_by_idea.get(idea.idea_id)
        items = items_by_claim.get(claim.claim_id, []) if claim else []
        idea.strongest_counter_thesis = (
            claim.strongest_counter if claim and claim.strongest_counter
            else "No material counter-evidence identified in the current run."
        )
        idea.promotion_decision = decide_promotion((promotion_bundles or {}).get(idea.idea_id))
        idea.promotion_label = idea.promotion_decision.label if idea.promotion_decision.eligible else ""
        probability, sample_size = (
            calibration_lookup(idea.signal_family, idea.horizon)
            if calibration_lookup else (None, 0)
        )
        user_assumptions = getattr(idea, "user_assumptions", {}) or {}
        assumed_entry_price = _assumption_float(user_assumptions, "entry_price", entry_price)
        assumed_transaction_cost = _assumption_float(user_assumptions, "transaction_cost_pct", 0.10)
        assumed_dividend_return = _assumption_float(user_assumptions, "dividend_return_pct", 0.0)
        idea.payoff_model = build_payoff_model(
            idea,
            valuation,
            assumed_entry_price,
            borrow_cost_pct=_assumption_float(user_assumptions, "borrow_cost_pct", None),
            transaction_cost_pct=0.10 if assumed_transaction_cost is None else assumed_transaction_cost,
            dividend_return_pct=0.0 if assumed_dividend_return is None else assumed_dividend_return,
            hedge_ratio=_assumption_float(user_assumptions, "hedge_ratio", None),
            scenario_exit_values=_assumption_exit_values(user_assumptions),
            scenario_probabilities=_assumption_probabilities(user_assumptions),
            calibrated_probability=probability,
            calibration_sample_size=sample_size,
        )
        idea.scenarios = idea.payoff_model.scenarios
        idea.probability_provenance = idea.payoff_model.probability_provenance
        idea.score = score_idea(idea, valuation, evidence)
        high_passed: list[str] = []
        high_failed: list[str] = []
        ready_passed: list[str] = []
        ready_failed: list[str] = []

        event = idea.source_events[0] if idea.source_events else None
        source_linked = bool(
            event and any(citation.url for citation in event.citations)
        )
        current_price_available = assumed_entry_price is not None and assumed_entry_price > 0
        payoff_complete = bool(
            idea.payoff_model.payoff_completeness
            and idea.payoff_model.payoff_completeness.status == "Complete"
        )
        checks_complete = any(
            item.metric and item.operator and item.deadline
            and item.confirm_threshold is not None and item.break_threshold is not None
            for item in idea.monitor_items
        )
        economic_context_present = bool(event and "economic_driver" in event.metrics)
        economic_driver_mapped = bool(
            not economic_context_present
            or (
                event
                and event.metrics.get("economic_driver")
                and event.metrics.get("economic_driver") != "Unmapped"
                and event.metrics.get("driver_materiality") in {"High", "Medium"}
            )
        )
        validation_present = bool(event and "thesis_grade_status" in event.metrics)
        thesis_grade = bool(
            not validation_present
            or (event and event.metrics.get("thesis_grade_status") == "Thesis-grade")
        )
        normalization_complete = bool(
            not event
            or not event.metrics.get("normalization_required")
        )
        normalization_reason = (
            str(event.metrics.get("normalization_reason") or "")
            if event else ""
        ) or "Security-basis normalization is required before interpreting this signal"
        counter_documented = bool(
            not validation_present
            or (
                idea.strongest_counter_thesis
                and "no material counter-evidence identified" not in idea.strongest_counter_thesis.lower()
                and idea.strongest_counter_thesis != "Not yet evaluated."
            )
        )
        score_total = idea.score.total if idea.score else 0
        counter_work_order_ready = bool(
            source_linked
            and thesis_grade
            and economic_driver_mapped
            and normalization_complete
            and checks_complete
            and payoff_complete
            and score_total >= 65
        )
        _gate(source_linked, "Source-linked thesis is present", "Source-linked thesis is required", ready_passed, ready_failed)
        _gate(
            thesis_grade,
            "Source claim is thesis-grade",
            event.metrics.get("not_thesis_grade_reason") or "Source claim is not thesis-grade",
            ready_passed,
            ready_failed,
        )
        _gate(
            economic_driver_mapped,
            "Signal maps to a material company or industry economic driver",
            "Signal is not mapped to a material company or industry economic driver",
            ready_passed,
            ready_failed,
        )
        _gate(
            normalization_complete,
            "Security-basis normalization is complete or not required",
            normalization_reason,
            ready_passed,
            ready_failed,
        )
        _gate(current_price_available, "Current entry price is available", "Current entry price is unavailable", ready_passed, ready_failed)
        _gate(bool(idea.direction), "Direction is explicit", "Direction is required", ready_passed, ready_failed)
        _gate(bool(idea.catalyst), "Catalyst is explicit", "Catalyst is required", ready_passed, ready_failed)
        _gate(
            counter_documented or counter_work_order_ready,
            "Counter-thesis is documented or explicitly assigned as a diligence work order",
            "A real counter-thesis is required; none found is a gap",
            ready_passed,
            ready_failed,
        )
        _gate(checks_complete, "Machine-readable confirmation and break checks are present", "Machine-readable confirmation and break checks are required", ready_passed, ready_failed)
        _gate(payoff_complete, "Scenario payoff assumptions are complete", "Scenario payoff assumptions are incomplete", ready_passed, ready_failed)
        _gate(score_total >= 55, "Idea Quality Score is at least 55", "Idea Quality Score is below 55", ready_passed, ready_failed)
        _gate(
            _short_direction_supported(idea),
            "Short direction is supported by explicit negative validated evidence or bearish payoff",
            "Short direction requires explicit negative validated evidence or bearish valuation/payoff support",
            ready_passed,
            ready_failed,
        )
        _gate(
            not _sector_positive_short_conflict(idea),
            "Price attribution does not contradict the proposed Short setup",
            "Sector-driven positive price reaction cannot support a company-specific Short without negative residual evidence",
            ready_passed,
            ready_failed,
        )
        management_signal = bool(
            event and (
                event.category in MANAGEMENT_SIGNAL_FAMILIES
                or event.metrics.get("management_claim_id")
                or event.metrics.get("meeting_event_id")
            )
        )
        if management_signal:
            _gate(
                bool(event.metrics.get("machine_readable", True)),
                "Management claim is machine-readable",
                "Management claim must be machine-readable",
                ready_passed,
                ready_failed,
            )
            _gate(
                bool(event.metrics.get("cross_checked", True)),
                "Management claim has been cross-checked",
                "Management claim requires at least one cross-check",
                ready_passed,
                ready_failed,
            )

        news_only_signal = bool(
            idea.signal_family == "news_intelligence"
            or (event and event.category == "news_claim")
        )
        primary_corroborated = idea.primary_source_status == "Primary source checked"
        promotion_bundle = (promotion_bundles or {}).get(idea.idea_id)
        secondary_research_ready = bool(
            promotion_bundle and promotion_bundle.eligible_tier3_sources
        )
        _gate(
            not news_only_signal or primary_corroborated or secondary_research_ready,
            "News claim is corroborated by primary evidence or identifiable Tier 3 support",
            "News-only claims require primary corroboration or an eligible identifiable Tier 3 source before Research-Ready",
            ready_passed,
            ready_failed,
        )

        tier1_primary = any(item.stance == "Supports" and item.source_tier == 1 for item in items)
        conditional_secondary = bool(idea.promotion_decision and idea.promotion_decision.eligible)
        _gate(
            tier1_primary or conditional_secondary,
            (
                "Tier 1 primary support is present"
                if tier1_primary else "Two-source Tier 3 exception substitutes for the missing Tier 1 gate"
            ),
            "Tier 1 primary support or a qualifying two-source Tier 3 exception is required",
            high_passed, high_failed,
        )
        aligned = bool(
            event and event.event_date and event.citations
            and all(citation.filed or citation.period_end for citation in event.citations)
        )
        _gate(aligned, "Event and fiscal-period alignment is complete", "Event or fiscal-period alignment is incomplete", high_passed, high_failed)
        _gate(current_price_available, "Current entry price is available", "Current entry price is unavailable", high_passed, high_failed)
        _gate(
            thesis_grade,
            "Source claim is thesis-grade",
            event.metrics.get("not_thesis_grade_reason") or "Source claim is not thesis-grade",
            high_passed,
            high_failed,
        )
        _gate(
            economic_driver_mapped,
            "Signal maps to a material company or industry economic driver",
            "Signal is not mapped to a material company or industry economic driver",
            high_passed,
            high_failed,
        )
        _gate(
            normalization_complete,
            "Security-basis normalization is complete or not required",
            normalization_reason,
            high_passed,
            high_failed,
        )
        _gate(
            idea.payoff_model.status == "Available",
            "Auditable valuation payoff is complete",
            "; ".join(idea.payoff_model.data_gaps) or "Auditable valuation payoff is incomplete",
            high_passed, high_failed,
        )
        _gate(
            len(idea.scenarios) == 3 and all(scenario.exit_value is not None for scenario in idea.scenarios),
            "Bull, base, and bear operational cases are complete",
            "Three valuation-anchored operational cases are required", high_passed, high_failed,
        )
        _gate(checks_complete, "Machine-readable confirmation and break checks are present", "Machine-readable confirmation and break checks are required", high_passed, high_failed)
        _gate(counter_documented, "Counter-thesis is documented", "A real counter-thesis is required; none found is a gap", high_passed, high_failed)
        market_capture_claim = bool(idea.market_capture and idea.market_capture.category != "Unknown")
        _gate(
            not market_capture_claim or bool(idea.market_capture and idea.market_capture.consensus_official),
            "Market-capture claim uses official consensus or is not asserted",
            "Official consensus is required for a High-Conviction market-capture claim",
            high_passed, high_failed,
        )
        unresolved_contradiction = bool(
            idea.score and idea.score.score_cap_reason
            and "contradiction" in idea.score.score_cap_reason.lower()
        )
        _gate(
            not unresolved_contradiction,
            "No unresolved material contradiction remains",
            idea.score.score_cap_reason if unresolved_contradiction and idea.score else "A material contradiction remains unresolved",
            high_passed, high_failed,
        )
        _gate(score_total >= 70, "Idea Quality Score is at least 70", "Idea Quality Score is below 70", high_passed, high_failed)
        _gate(
            _short_direction_supported(idea),
            "Short direction is supported by explicit negative validated evidence or bearish payoff",
            "Short direction requires explicit negative validated evidence or bearish valuation/payoff support",
            high_passed,
            high_failed,
        )
        _gate(
            not _sector_positive_short_conflict(idea),
            "Price attribution does not contradict the proposed Short setup",
            "Sector-driven positive price reaction cannot support a company-specific Short without negative residual evidence",
            high_passed,
            high_failed,
        )
        if management_signal:
            _gate(
                event.metrics.get("cross_check_status") == "Confirmed",
                "Management claim is corroborated",
                "High-Conviction management signals require confirmed corroboration",
                high_passed,
                high_failed,
            )
        _gate(
            not news_only_signal or primary_corroborated or conditional_secondary,
            "News claim meets primary or conditional two-source corroboration policy",
            "News-only claims cannot independently create High-Conviction ideas",
            high_passed,
            high_failed,
        )
        research_ready = not ready_failed
        if research_ready and not counter_documented:
            _attach_counter_thesis_work_order(idea)
        if not research_ready:
            high_failed.append("High-Conviction requires all Research-Ready gates to pass first")
        high_conviction = research_ready and not high_failed
        if high_conviction:
            stage = STAGE_HIGH_CONVICTION
            if conditional_secondary and not tier1_primary:
                idea.promotion_label = "High-Conviction: secondary-supported"
        elif research_ready:
            stage = STAGE_RESEARCH_READY
        else:
            stage = STAGE_CANDIDATE
        result = IdeaGateResult(
            stage=stage,
            eligible=high_conviction,
            passed=high_passed,
            failed=high_failed,
            evaluated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            research_ready=research_ready,
            high_conviction=high_conviction,
            research_ready_passed=ready_passed,
            research_ready_failed=ready_failed,
            high_conviction_passed=high_passed,
            high_conviction_failed=high_failed,
        )
        idea.stage = stage
        idea.gate_result = result
        _attach_gate_next_source(idea, result)
        gate_results.append(result)
    return gate_results


def _attach_gate_next_source(idea: TradeIdea, result: IdeaGateResult) -> None:
    blockers = result.research_ready_failed or result.high_conviction_failed
    if not blockers:
        return
    action = _next_source_for_gate_failure(idea, blockers[0])
    if not action:
        return
    existing = idea.next_source_to_check.strip()
    if existing and action in existing:
        return
    label = "Research-Ready gate blocker" if result.research_ready_failed else "High-Conviction blocker"
    idea.next_source_to_check = (
        f"{label}: {action}"
        + (f" Existing source plan: {existing}" if existing else "")
    )


def _attach_counter_thesis_work_order(idea: TradeIdea) -> None:
    action = "Build a source-backed counter-thesis from peer metrics, segment KPIs, management commentary, valuation sensitivity, or primary-source contradictions."
    existing = idea.next_source_to_check.strip()
    if action in existing:
        return
    idea.next_source_to_check = f"{existing} Counter-thesis work order: {action}".strip()


def _next_source_for_gate_failure(idea: TradeIdea, failure: str) -> str:
    event = idea.source_events[0] if idea.source_events else None
    lower = failure.lower()
    template = template_for_event(event) if event else None
    if "source-linked" in lower:
        return "Attach the exact SEC/issuer excerpt with URL, accession or section, filing date, and period."
    if "not thesis-grade" in lower or "source claim" in lower:
        return template.next_source if template else "Pull prior/current source excerpts and validate the exact changed claim."
    if "economic driver" in lower:
        return "Map the signal to a material company or industry driver using segment KPIs, management commentary, or manual industry data."
    if "normalization" in lower or "security-basis" in lower:
        return "Complete share/security-basis reconciliation: ordinary shares versus ADS, ADR ratio, split, buyback, and weighted-average basis."
    if "entry price" in lower or "price reaction" in lower:
        return "Load adjusted daily prices for the ticker, sector benchmark, and peer basket around the event date."
    if "counter-thesis" in lower:
        return "Find the strongest contrary filing, KPI, management, peer, or consensus evidence before promotion."
    if "machine-readable" in lower or "monitor" in lower or "confirmation" in lower:
        return "Define metric, operator, confirm/break thresholds, deadline, and source field for the thesis monitor."
    if "payoff" in lower or "valuation" in lower or "operational cases" in lower:
        return "Complete bull/base/bear operating assumptions and valuation exit anchors tied to the affected driver."
    if "quality score" in lower or "tier 1" in lower:
        return "Add higher-tier source evidence and a quantified KPI or valuation bridge for the core claim."
    if "short direction" in lower:
        return "Find explicit negative validated evidence or bearish valuation support before allowing a Short thesis."
    if "official consensus" in lower:
        return "Seed official or CSV point-in-time consensus snapshots before making a market-capture claim."
    return template.next_source if template else "Run the source plan for the highest-priority missing evidence."


def _short_direction_supported(idea: TradeIdea) -> bool:
    if idea.direction != "Short":
        return True
    event = idea.source_events[0] if idea.source_events else None
    if not event or "thesis_grade_status" not in event.metrics:
        return True
    validated_negative = bool(
        event
        and event.metrics.get("thesis_grade_status") == "Thesis-grade"
        and event.metrics.get("validated_direction") == "negative"
    )
    bearish_payoff = bool(
        idea.payoff_model
        and idea.payoff_model.expected_value_pct is not None
        and idea.payoff_model.expected_value_pct > 0
    )
    return validated_negative or bearish_payoff


def _sector_positive_short_conflict(idea: TradeIdea) -> bool:
    if idea.direction != "Short" or not idea.driver_attribution:
        return False
    attribution = idea.driver_attribution
    if "sector" not in (attribution.classification or "").lower():
        return False
    raw_positive = (attribution.raw_return_pct or 0) > 0
    residual_negative = attribution.residual_pct is not None and attribution.residual_pct <= -1.0
    return raw_positive and not residual_negative


def _gate(
    condition: bool,
    pass_message: str,
    fail_message: str,
    passed: list[str],
    failed: list[str],
) -> None:
    (passed if condition else failed).append(pass_message if condition else fail_message)


def _directional_return(
    direction: str,
    entry_price: float | None,
    exit_price: float | None,
) -> float | None:
    if entry_price is None or entry_price <= 0 or exit_price is None:
        return None
    if direction == "Long":
        return (exit_price - entry_price) / entry_price * 100
    if direction == "Short":
        return (entry_price - exit_price) / entry_price * 100
    return None


def _assumption_float(
    assumptions: dict,
    key: str,
    default: float | None,
) -> float | None:
    value = assumptions.get(key)
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if key in {"transaction_cost_pct", "dividend_return_pct", "borrow_cost_pct", "hedge_ratio"}:
        return parsed if parsed >= 0 else default
    return parsed if parsed > 0 else default


def _assumption_exit_values(assumptions: dict) -> dict[str, float]:
    exits: dict[str, float] = {}
    for case_name in ("bear", "base", "bull"):
        value = _assumption_float(assumptions, f"{case_name}_exit", None)
        if value is not None and value > 0:
            exits[case_name] = value
    return exits


def _assumption_probabilities(assumptions: dict) -> dict[str, float]:
    values: dict[str, float] = {}
    for case_name in ("bear", "base", "bull"):
        raw_value = assumptions.get(f"{case_name}_probability_pct")
        if raw_value in (None, ""):
            continue
        try:
            values[case_name.title()] = max(0.0, float(raw_value)) / 100
        except (TypeError, ValueError):
            continue
    return values if len(values) == 3 else {}


def _normalized_scenario_probabilities(values: dict[str, float] | None) -> dict[str, float]:
    if not values:
        return {}
    parsed: dict[str, float] = {}
    for case_name in ("Bear", "Base", "Bull"):
        raw_value = values.get(case_name, values.get(case_name.lower()))
        try:
            parsed[case_name] = max(0.0, float(raw_value))
        except (TypeError, ValueError):
            return {}
    total = sum(parsed.values())
    if total <= 0:
        return {}
    return {name: value / total for name, value in parsed.items()}


def _scenario_exit_override(
    scenario_exit_values: dict[str, float] | None,
    case_name: str,
) -> float | None:
    if not scenario_exit_values:
        return None
    normalized = case_name.lower()
    return scenario_exit_values.get(normalized) or scenario_exit_values.get(case_name)


def _payoff_envelope_cases(idea: TradeIdea, entry_price: float | None) -> list[ValuationCase]:
    if entry_price is None or entry_price <= 0 or idea.direction not in {"Long", "Short"}:
        return []
    # Scenario labels always describe the stock outcome, independent of
    # whether the researched position is long or short.
    moves = {"Bear": -15.0, "Base": 0.0, "Bull": 20.0}
    cases: list[ValuationCase] = []
    for name, move in moves.items():
        exit_value = entry_price * (1 + move / 100)
        cases.append(ValuationCase(
            name=name,
            probability={"Bear": 0.25, "Base": 0.50, "Bull": 0.25}[name],
            fair_value=exit_value,
            method="Payoff envelope",
            assumptions=[
                "Illustrative payoff envelope only; this is not an internally calculated fair value.",
                f"{name} exit uses {move:+.1f}% move from current entry price.",
            ],
        ))
    return cases


def _apply_calibrated_probability(
    scenarios: list[Scenario],
    success_probability: float,
) -> None:
    positive = [scenario for scenario in scenarios if (scenario.net_return_pct or 0) > 0]
    negative = [scenario for scenario in scenarios if (scenario.net_return_pct or 0) <= 0]
    if not positive or not negative:
        return
    positive_weight = sum(scenario.probability for scenario in positive)
    negative_weight = sum(scenario.probability for scenario in negative)
    for scenario in positive:
        scenario.probability = success_probability * scenario.probability / positive_weight
    for scenario in negative:
        scenario.probability = (1.0 - success_probability) * scenario.probability / negative_weight


def _idea_direction(event: ChangeEvent) -> str:
    if event.metrics.get("normalization_required") or event.metrics.get("direction_validation_required"):
        return "Watch"
    if event.metrics.get("thesis_grade_status") in {"Watch Item", "Not thesis-grade"}:
        return "Watch"
    if event.direction == "positive":
        return "Long"
    if event.direction == "negative":
        return "Short"
    if event.category in {"qa_evasion", "governance_change", "incentive_alignment", "shareholder_vote_signal"}:
        return "Relative Value"
    if event.category in {"risk_factors", "litigation", "debt_liquidity", "dilution"}:
        return "Short"
    return "Relative Value"


def _idea_structure(direction: str) -> str:
    if direction == "Watch":
        return "Watch item / evidence-gathering candidate"
    if direction == "Long":
        return "Long equity or call-spread research candidate"
    if direction == "Short":
        return "Short equity or put-spread research candidate"
    return "Pair trade / peer spread research candidate"


def _idea_title(ticker: str, event: ChangeEvent, direction: str) -> str:
    driver = event.metrics.get("economic_driver")
    if driver and driver != "Unmapped":
        return f"{direction} {ticker}: {driver} - {event.title}"
    return f"{direction} {ticker}: {event.title}"


def _idea_thesis(identity: CompanyIdentity, event: ChangeEvent, direction: str) -> str:
    company = identity.name.title()
    contextual = event.metrics.get("contextual_disclosure_comparison")
    contextual = contextual if isinstance(contextual, dict) else {}
    comparison_sentence = ""
    if contextual:
        comparison_sentence = (
            f"Compared with {contextual.get('prior_source') or 'the prior source'} "
            f"for {contextual.get('prior_period') or 'an earlier period'}, "
            f"the {contextual.get('comparison_type') or 'contextual'} comparison indicates: "
            f"{contextual.get('semantic_shift') or 'a change requiring further validation'} "
        )
    if direction == "Watch":
        reason = (
            event.metrics.get("normalization_reason")
            or event.metrics.get("not_thesis_grade_reason")
            or event.metrics.get("direction_rationale")
        )
        quote = event.metrics.get("supporting_quote") or event.summary
        return (
            f"{company} has a source signal that is not yet thesis-grade. "
            f"{reason or 'The app cannot pin down a precise directional claim.'} "
            f"{comparison_sentence}"
            f"Exact source text: {quote}"
        )
    action = "benefit from" if direction == "Long" else "be pressured by"
    if direction == "Relative Value":
        action = "create a relative-value setup around"
    driver = event.metrics.get("economic_driver")
    driver_sentence = (
        f"The signal maps to the {driver} economic driver. "
        if driver and driver != "Unmapped" else
        "The signal is not yet tied to a material economic driver, so it remains a candidate. "
    )
    return (
        f"{company} may {action} a newly detected {event.category.replace('_', ' ')} signal. "
        f"{driver_sentence}"
        f"{comparison_sentence}"
        f"{event.summary}"
    )


def _horizon(event: ChangeEvent) -> str:
    if event.category in MANAGEMENT_SIGNAL_FAMILIES:
        return "1-3 quarters"
    if event.category in {"litigation", "debt_liquidity"}:
        return "3-12 months"
    if event.category in {"financial_kpi", "margin", "guidance"}:
        return "1-2 quarters"
    return "1-3 quarters"


def _catalyst(event: ChangeEvent) -> str:
    if event.metrics.get("thesis_grade_status") in {"Watch Item", "Not thesis-grade"}:
        return "Source-plan follow-up, exact prior/current text validation, or next management/filing corroboration"
    if event.category in MANAGEMENT_SIGNAL_FAMILIES:
        return "Next earnings call, proxy update, filing cross-check, or management promise outcome"
    if event.category in {"financial_kpi", "margin", "guidance"}:
        return "Next earnings report, guidance update, or consensus revision cycle"
    if event.category in {"risk_factors", "litigation", "debt_liquidity"}:
        return "Next 10-Q/10-K/8-K disclosure or management Q&A"
    return "Next filing, earnings call, or peer read-through"


def _idea_id(ticker: str, event: ChangeEvent) -> str:
    raw = f"{ticker}:{event.title}:{event.event_date}:{event.summary}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def _direction_rationale(event: ChangeEvent, direction: str) -> str:
    if direction == "Watch":
        reason = event.metrics.get("not_thesis_grade_reason") or event.metrics.get("direction_rationale")
        return (
            "No trade direction is assigned because the claim is not thesis-grade; "
            f"{reason or 'exact evidence must be validated first.'}"
        )
    if event.metrics.get("direction_rationale"):
        return str(event.metrics["direction_rationale"])
    return f"Direction follows validated event direction: {event.direction}."


def _novelty_score(capture: MarketCapture | None) -> int:
    if not capture:
        return 50
    if capture.category == "Uncaptured":
        return 92
    if capture.category == "Partially captured":
        return 72
    if capture.category == "Mostly captured":
        return 40
    return 55


def _metric_bonus(event: ChangeEvent) -> int:
    bonus = 0
    for value in event.metrics.values():
        if isinstance(value, (int, float)):
            bonus += min(15, int(abs(value)))
    return min(25, bonus)


def _text_event_driver_analysis(event: ChangeEvent, metrics: list[FinancialMetric] | None = None) -> DriverAnalysis:
    label = event.category.replace("_", " ")
    template = template_for_event(event)
    contextual = event.metrics.get("contextual_disclosure_comparison")
    contextual = contextual if isinstance(contextual, dict) else {}
    intelligence = event.metrics.get("disclosure_intelligence")
    intelligence = intelligence if isinstance(intelligence, dict) else {}
    semantic_shift = str(contextual.get("semantic_shift") or event.metrics.get("semantic_shift") or "")
    comparison_type = str(contextual.get("comparison_type") or event.metrics.get("comparison_type") or "unclassified")
    affected_driver = str(contextual.get("affected_driver") or intelligence.get("affected_driver") or event.metrics.get("economic_driver") or "Unmapped")
    confirmation = [str(item) for item in contextual.get("required_confirmation", [])]
    factors = [
        DriverFactor(
            cause=semantic_shift or f"{label.title()} disclosure change",
            direction=event.direction,
            confidence=str(contextual.get("confidence") or ("Medium" if event.citations else "Low")),
            magnitude_hint=f"Severity {event.severity}/5",
            explanation=(
                f"The app classified this as {comparison_type} evidence affecting {affected_driver}. "
                f"{semantic_shift or 'The exact semantic shift still needs validation.'}"
            ),
            citations=list(event.citations),
            missing_data_notes=(confirmation or ["No citation snippet was attached to this event."]) if not event.citations or confirmation else [],
        )
    ]
    if event.category == "debt_liquidity" and metrics:
        bridge_factors = _financial_driver_factors("Cash", event, metrics)
        if bridge_factors:
            factors = bridge_factors[:4] + factors
    return _driver_analysis(
        f"Disclosure bridge: {semantic_shift or f'{label} language changed'}",
        factors,
        template,
        event,
        str(event.metrics.get("metric_name") or event.category),
    )


def _driver_template_summary(event: ChangeEvent) -> str:
    template = template_for_event(event)
    return (
        f"{template.label}: {template.why_it_matters} "
        f"Confirm with: {template.confirm_evidence} "
        f"Falsify if: {template.falsify_evidence}"
    )


def _share_reconciliation_from_metrics(event: ChangeEvent) -> ShareReconciliation | None:
    payload = event.metrics.get("share_reconciliation")
    if not isinstance(payload, dict):
        return None
    fields = getattr(ShareReconciliation, "__dataclass_fields__", {})
    normalized = {key: value for key, value in payload.items() if key in fields}
    citations: list = []
    for citation in normalized.get("citations") or []:
        if isinstance(citation, dict):
            citations.append(Citation(**{key: value for key, value in citation.items() if key in Citation.__dataclass_fields__}))
        elif isinstance(citation, Citation):
            citations.append(citation)
    normalized["citations"] = citations
    try:
        return ShareReconciliation(**normalized)
    except TypeError:
        return None


def _financial_driver_factors(
    metric_name: str,
    event: ChangeEvent,
    metrics: list[FinancialMetric],
) -> list[DriverFactor]:
    by_name = {metric.name: metric for metric in metrics}
    revenue = by_name.get("Revenue")
    gross_profit = by_name.get("Gross Profit")
    cost_of_revenue = by_name.get("Cost of Revenue")
    operating_income = by_name.get("Operating Income")
    sga = by_name.get("SG&A Expense")
    rnd = by_name.get("R&D Expense")
    sales_marketing = by_name.get("Sales and Marketing Expense")
    interest = by_name.get("Interest Expense")
    taxes = by_name.get("Income Tax Expense")
    shares = by_name.get("Shares")
    factors: list[DriverFactor] = []

    lower_metric = metric_name.lower()
    family = _driver_family(metric_name, event)
    is_net_income_bridge = family == "net_income"
    is_operating_bridge = family == "operating"
    is_margin_bridge = family == "margin"
    is_liquidity_bridge = family == "liquidity"
    is_debt_bridge = family == "debt"
    is_per_share_bridge = any(token in lower_metric for token in ("eps", "per share"))

    if family in {"acquisition_accounting", "investment_cycle", "unmapped"}:
        policy = metric_policy_for(metric_name)
        for side, mechanisms in (
            ("constructive", policy.constructive_mechanisms),
            ("adverse", policy.adverse_mechanisms),
        ):
            if not mechanisms:
                continue
            factors.append(
                _factor(
                    f"{side.title()} hypothesis",
                    "neutral",
                    "Low",
                    event.summary,
                    mechanisms[0],
                    event,
                )
            )
        return factors

    if family == "revenue" and revenue and _metric_yoy(revenue) is not None:
        factors.append(
            _factor(
                "Revenue growth" if _metric_yoy(revenue) > 0 else "Revenue pressure",
                "positive" if _metric_yoy(revenue) > 0 else "negative",
                "High",
                f"Revenue {_metric_yoy(revenue):+.1f}%",
                "The primary KPI moved with reported revenue, so demand, pricing, volume, product mix, or currency should be checked next.",
                event,
            )
        )

    margin_delta = _gross_margin_delta(revenue, gross_profit)
    if is_margin_bridge and margin_delta is not None:
        factors.append(
            _factor(
                "Gross margin expansion" if margin_delta > 0 else "Gross margin compression",
                "positive" if margin_delta > 0 else "negative",
                "High",
                f"Gross margin {margin_delta:+.1f} pts",
                (
                    "Gross margin expansion points to possible pricing power, favorable mix, services contribution, or cost efficiency."
                    if margin_delta > 0
                    else "Gross margin compression points to possible cost, mix, pricing, logistics, or promotion pressure."
                ),
                event,
            )
        )

    if is_margin_bridge and revenue and gross_profit and _metric_yoy(gross_profit) is not None:
        factors.append(
            _factor(
                "Gross profit moved with revenue and margin",
                "positive" if _metric_yoy(gross_profit) > 0 else "negative",
                "Medium",
                f"Gross profit {_metric_yoy(gross_profit):+.1f}%",
                "Gross profit changes can reflect both revenue growth and gross margin movement.",
                event,
            )
        )
        revenue_yoy = _metric_yoy(revenue)
        gross_yoy = _metric_yoy(gross_profit)
        if revenue_yoy is not None and gross_yoy is not None:
            if gross_yoy > revenue_yoy + 5:
                factors.append(
                    _factor(
                        "Gross margin/mix improved versus revenue",
                        "positive",
                        "Medium",
                        f"Gross profit {gross_yoy:+.1f}% vs revenue {revenue_yoy:+.1f}%",
                        "Gross profit outgrew revenue, which points to margin, mix, pricing, or cost-efficiency support.",
                        event,
                    )
                )
            elif gross_yoy < revenue_yoy - 5:
                factors.append(
                    _factor(
                        "Gross margin/mix lagged revenue",
                        "negative",
                        "Medium",
                        f"Gross profit {gross_yoy:+.1f}% vs revenue {revenue_yoy:+.1f}%",
                        "Gross profit lagged revenue, which points to margin, mix, price/incentive, or cost pressure.",
                        event,
                    )
                )
        if (
            cost_of_revenue
            and revenue
            and _metric_yoy(cost_of_revenue) is not None
            and _metric_yoy(revenue) is not None
            and _metric_yoy(cost_of_revenue) > _metric_yoy(revenue) + 5
        ):
            factors.append(
                _factor(
                    "Cost of revenue outgrew revenue",
                    "negative",
                    "Medium",
                    f"Cost of revenue {_metric_yoy(cost_of_revenue):+.1f}% vs revenue {_metric_yoy(revenue):+.1f}%",
                    "Costs rising faster than revenue can pressure gross profit and gross margin.",
                    event,
                )
            )

    if is_operating_bridge or is_net_income_bridge:
        if revenue and _metric_yoy(revenue) is not None and _metric_yoy(revenue) < -2:
            factors.append(
                _factor(
                    "Revenue pressure",
                    "negative",
                    "High",
                    f"Revenue {_metric_yoy(revenue):+.1f}%",
                    "Operating income can fall when the revenue base contracts and fixed costs do not fall as quickly.",
                    event,
                )
            )

        if margin_delta is not None and margin_delta < -1:
            factors.append(
                _factor(
                    "Gross margin compression",
                    "negative",
                    "High",
                    f"Gross margin {margin_delta:+.1f} pts",
                    "Gross margin compression points to cost, mix, pricing, logistics, or promotion pressure as a possible operating-income driver.",
                    event,
                )
            )
        elif (
            cost_of_revenue
            and revenue
            and _metric_yoy(cost_of_revenue) is not None
            and _metric_yoy(revenue) is not None
            and _metric_yoy(cost_of_revenue) > _metric_yoy(revenue) + 5
        ):
            factors.append(
                _factor(
                    "Cost of revenue outgrew revenue",
                    "negative",
                    "Medium",
                    f"Cost of revenue {_metric_yoy(cost_of_revenue):+.1f}% vs revenue {_metric_yoy(revenue):+.1f}%",
                    "Costs rising faster than revenue can pressure gross profit and operating income.",
                    event,
                )
            )

        for expense in (sga, sales_marketing, rnd):
            if not expense or _metric_yoy(expense) is None:
                continue
            revenue_yoy = _metric_yoy(revenue) if revenue else None
            if revenue_yoy is None or _metric_yoy(expense) > revenue_yoy + 5:
                factors.append(
                    _factor(
                        f"{expense.name} deleverage",
                        "negative",
                        "Medium",
                        (
                            f"{expense.name} {_metric_yoy(expense):+.1f}%"
                            + (f" vs revenue {revenue_yoy:+.1f}%" if revenue_yoy is not None else "")
                        ),
                        "Operating expenses growing faster than revenue are a possible source of operating-income pressure.",
                        event,
                )
            )

    if is_liquidity_bridge:
        operating_cash_flow = by_name.get("Operating Cash Flow")
        capex = by_name.get("Capital Expenditure")
        dividends = by_name.get("Dividends Paid")
        repurchases = by_name.get("Share Repurchases")
        long_term_debt = by_name.get("Long-term Debt")
        current_debt = by_name.get("Current Debt")
        cash = by_name.get("Cash")
        if operating_cash_flow and _metric_yoy(operating_cash_flow) is not None:
            factors.append(
                _factor(
                    "Operating cash flow improved" if _metric_yoy(operating_cash_flow) > 0 else "Operating cash flow weakened",
                    "positive" if _metric_yoy(operating_cash_flow) > 0 else "negative",
                    "High",
                    f"Operating cash flow {_metric_yoy(operating_cash_flow):+.1f}%",
                    "Operating cash flow is the first source-of-cash check for a liquidity thesis.",
                    event,
                )
            )
        if capex and _metric_yoy(capex) is not None:
            capex_yoy = _metric_yoy(capex)
            factors.append(
                _factor(
                    "Capex absorbed more cash" if capex_yoy > 0 else "Capex absorbed less cash",
                    "negative" if capex_yoy > 0 else "positive",
                    "Medium",
                    f"Capital expenditure {capex_yoy:+.1f}%",
                    "Capex changes help reconcile whether cash movement came from reinvestment needs or free-cash-flow conversion.",
                    event,
                )
            )
        if dividends and _metric_yoy(dividends) is not None and abs(_metric_yoy(dividends)) >= 10:
            factors.append(
                _factor(
                    "Dividend cash use changed",
                    "negative" if _metric_yoy(dividends) > 0 else "positive",
                    "Low",
                    f"Dividends paid {_metric_yoy(dividends):+.1f}%",
                    "Dividend cash use can explain part of the cash bridge but is not by itself an operating thesis.",
                    event,
                )
            )
        if repurchases and _metric_yoy(repurchases) is not None and abs(_metric_yoy(repurchases)) >= 10:
            factors.append(
                _factor(
                    "Buyback cash use changed",
                    "negative" if _metric_yoy(repurchases) > 0 else "positive",
                    "Medium",
                    f"Share repurchases {_metric_yoy(repurchases):+.1f}%",
                    "Repurchases connect liquidity directly to capital return and per-share value, but should be reconciled to ADS/ordinary-share basis.",
                    event,
                )
            )
        debt_yoys = [
            _metric_yoy(item)
            for item in (long_term_debt, current_debt)
            if item and _metric_yoy(item) is not None
        ]
        if debt_yoys:
            avg_debt_yoy = sum(debt_yoys) / len(debt_yoys)
            factors.append(
                _factor(
                    "Debt financing or repayment affected cash",
                    "positive" if avg_debt_yoy > 0 else "negative",
                    "Medium",
                    f"Debt lines average {avg_debt_yoy:+.1f}%",
                    "Debt issuance can lift cash while repayment can reduce it; the financing section must be checked before calling cash generation durable.",
                    event,
                )
            )
        if cash and _metric_yoy(cash) is not None and not factors:
            factors.append(
                _factor(
                    "Cash moved but bridge is not decomposed",
                    event.direction,
                    "Low",
                    f"Cash {_metric_yoy(cash):+.1f}%",
                    "The cash balance changed, but the app still needs cash-flow and financing-detail evidence before attributing why.",
                    event,
                )
            )

    if is_debt_bridge:
        debt_metrics = [metric for metric in (by_name.get("Long-term Debt"), by_name.get("Current Debt")) if metric]
        for debt_metric in debt_metrics:
            if _metric_yoy(debt_metric) is None:
                continue
            factors.append(
                _factor(
                    f"{debt_metric.name} {'increased' if _metric_yoy(debt_metric) > 0 else 'declined'}",
                    "negative" if _metric_yoy(debt_metric) > 0 else "positive",
                    "High",
                    f"{debt_metric.name} {_metric_yoy(debt_metric):+.1f}%",
                    "Debt movement must be tied to maturity, refinancing, cash balance, and interest-cost evidence before becoming a credit or equity thesis.",
                    event,
                )
            )

    if is_net_income_bridge and interest and _metric_yoy(interest) is not None and _metric_yoy(interest) > 10:
        factors.append(
            _factor(
                "Higher financing cost",
                "negative",
                "Medium",
                f"Interest expense {_metric_yoy(interest):+.1f}%",
                "Higher interest expense can pressure net income and investor perception even when operating metrics are stable.",
                event,
            )
        )

    if is_net_income_bridge and taxes and _metric_yoy(taxes) is not None and _metric_yoy(taxes) > 15:
        factors.append(
            _factor(
                "Higher tax expense",
                "negative",
                "Low",
                f"Income tax expense {_metric_yoy(taxes):+.1f}%",
                "A higher tax burden may explain part of net-income pressure, though it does not usually explain operating-income moves.",
                event,
            )
        )

    if is_per_share_bridge and shares and _metric_yoy(shares) is not None and _metric_yoy(shares) > 2:
        factors.append(
            _factor(
                "Dilution / share-count growth",
                "negative",
                "Low",
                f"Shares {_metric_yoy(shares):+.1f}%",
                "Share-count growth can dilute per-share results even if company-level earnings are stable.",
                event,
            )
        )

    if not factors and (is_operating_bridge or is_net_income_bridge) and operating_income and _metric_yoy(operating_income) is not None:
        factors.append(
            _factor(
                "KPI moved but drivers are not decomposed",
                event.direction,
                "Low",
                f"Operating income {_metric_yoy(operating_income):+.1f}%",
                "The operating-income change is visible, but related line items did not identify a clear cause.",
                event,
            )
        )

    factors.sort(key=_factor_rank, reverse=True)
    return factors


def _factor(
    cause: str,
    direction: str,
    confidence: str,
    magnitude_hint: str,
    explanation: str,
    event: ChangeEvent,
) -> DriverFactor:
    return DriverFactor(
        cause=cause,
        direction=direction,
        confidence=confidence,
        magnitude_hint=magnitude_hint,
        explanation=explanation,
        citations=list(event.citations),
    )


def _metric_yoy(metric: FinancialMetric | None) -> float | None:
    return metric.yoy_change_pct if metric else None


def _dedupe_strings(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            rows.append(text)
            seen.add(text)
    return rows


def _gross_margin_delta(
    revenue: FinancialMetric | None,
    gross_profit: FinancialMetric | None,
) -> float | None:
    if (
        not revenue
        or not gross_profit
        or not revenue.value
        or not revenue.previous_value
        or not gross_profit.previous_value
    ):
        return None
    current_margin = gross_profit.value / revenue.value * 100
    previous_margin = gross_profit.previous_value / revenue.previous_value * 100
    return current_margin - previous_margin


def _factor_rank(factor: DriverFactor) -> tuple[int, float]:
    confidence_rank = {"High": 3, "Medium": 2, "Low": 1}.get(factor.confidence, 0)
    magnitude = 0.0
    for token in factor.magnitude_hint.replace("%", " ").replace("pts", " ").split():
        try:
            magnitude = max(magnitude, abs(float(token)))
        except ValueError:
            continue
    return confidence_rank, magnitude


def _metric_name_from_title(title: str) -> str:
    title_lower = title.lower()
    for metric_name in (
        "Operating Income",
        "Operating Margin",
        "Gross Margin",
        "Revenue",
        "Gross Profit",
        "Cash",
        "Operating Cash Flow",
        "Free Cash Flow",
        "Net Income",
        "Long-term Debt",
        "Current Debt",
        "Shares",
    ):
        if metric_name.lower() in title_lower:
            return metric_name
    return "KPI"


def _timing_score(event_date: str | None) -> int:
    if not event_date:
        return 50
    try:
        event_dt = datetime.strptime(event_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return 50
    days_old = (date.today() - event_dt).days
    if days_old <= 14:
        return 90
    if days_old <= 45:
        return 75
    if days_old <= 120:
        return 60
    return 40
