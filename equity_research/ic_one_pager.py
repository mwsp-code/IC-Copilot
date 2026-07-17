from __future__ import annotations

from .idea_engine import expected_value
from .models import (
    ActionPlan,
    CompanyEconomics,
    CompanyIdentity,
    CreditLens,
    EvidenceSufficiency,
    EvidenceWorkOrder,
    ICOnePager,
    ThesisBrief,
    ThesisCluster,
    ThesisCritique,
    ThesisValidationReport,
    TradeIdea,
    ValuationResult,
)


def build_ic_one_pager(
    identity: CompanyIdentity,
    thesis_brief: ThesisBrief,
    thesis_critique: ThesisCritique,
    evidence_sufficiency: EvidenceSufficiency,
    ideas: list[TradeIdea],
    valuation: ValuationResult,
    thesis_validation: ThesisValidationReport | None,
    evidence_work_order: EvidenceWorkOrder | None,
    company_economics: CompanyEconomics | None,
    credit_lens: CreditLens | None,
    thesis_clusters: list[ThesisCluster] | None,
    action_plan: list[ActionPlan],
) -> ICOnePager:
    top = ideas[0] if ideas else None
    cluster = _cluster_for_top(top, thesis_clusters)
    status = _status(thesis_brief, evidence_sufficiency, evidence_work_order, top)
    title = thesis_brief.title or (top.title if top else f"{identity.ticker}: no thesis")
    gaps = _evidence_gaps(thesis_brief, thesis_critique, thesis_validation, evidence_work_order)
    work_actions = _work_order_actions(evidence_work_order)
    monitor_actions = _monitor_actions(top, action_plan)
    return ICOnePager(
        ticker=identity.ticker.upper(),
        status=status,
        verdict=thesis_brief.verdict,
        title=title,
        stage=thesis_brief.stage,
        direction=thesis_brief.direction,
        thesis=thesis_brief.thesis,
        variant_perception=thesis_brief.variant_perception,
        causal_bridge=_causal_bridge(top, cluster),
        price_move=_price_move(top),
        market_capture=_market_capture(top),
        valuation=_valuation(top, valuation),
        equity_lens=_equity_lens(top, company_economics),
        credit_lens=_credit_lens(top, credit_lens),
        counter_thesis=thesis_critique.strongest_counter_thesis,
        monitor_actions=monitor_actions,
        evidence_gaps=gaps,
        work_order_actions=work_actions,
        source=thesis_brief.source,
        decision=_decision(status, thesis_brief, top),
        decision_reason=_decision_reason(status, thesis_brief, evidence_sufficiency, top, gaps),
        why_now=_why_now(top, cluster),
        blocking_issue=_blocking_issue(status, top, gaps),
        next_best_action=_next_best_action(work_actions, monitor_actions, thesis_validation),
        rank_eligibility=_rank_eligibility(top),
        go_no_go_reason=_go_no_go_reason(status, thesis_brief, top, gaps),
    )


def _status(
    brief: ThesisBrief,
    sufficiency: EvidenceSufficiency,
    work_order: EvidenceWorkOrder | None,
    top: TradeIdea | None,
) -> str:
    if brief.verdict == "No convincing thesis yet":
        return "No thesis yet"
    # Deterministic promotion gates are authoritative. Work orders explain the
    # remaining diligence but must not retroactively downgrade a passed stage.
    if top and top.stage == "High-Conviction":
        return "IC-ready draft"
    if top and top.stage == "Research-Ready":
        return "Researchable, not high conviction"
    if work_order and any(
        item.blocks_research_ready and _work_item_is_open(item)
        for item in work_order.items
    ):
        return "Needs Research-Ready evidence"
    if work_order and any(
        item.blocks_high_conviction and _work_item_is_open(item)
        for item in work_order.items
    ):
        return "Researchable, not high conviction"
    if sufficiency.status == "Convincing":
        return "IC-ready draft"
    return "Promising but incomplete"


def _cluster_for_top(
    top: TradeIdea | None,
    clusters: list[ThesisCluster] | None,
) -> ThesisCluster | None:
    if not top or not clusters:
        return None
    return next((cluster for cluster in clusters if top.idea_id in cluster.idea_ids), clusters[0])


def _causal_bridge(top: TradeIdea | None, cluster: ThesisCluster | None) -> str:
    if not top:
        return "No source-linked idea exists yet."
    if top.driver_analysis:
        parts = [
            top.driver_analysis.primary_driver or "Driver unmapped",
            top.driver_analysis.bridge_status or top.causal_bridge_status or "bridge status unknown",
            top.driver_analysis.mechanism,
        ]
        return " | ".join(part for part in parts if part)
    if cluster and cluster.conviction_chain_status:
        return f"{cluster.driver_name}: {cluster.conviction_chain_status}."
    return top.causal_bridge_status or "Causal bridge has not been built."


def _price_move(top: TradeIdea | None) -> str:
    if not top:
        return "No price move attached."
    attribution = top.driver_attribution
    if attribution and attribution.status == "Available":
        if attribution.attribution_summary:
            return (
                f"{attribution.attribution_readiness}: "
                + " ".join(attribution.attribution_summary[:4])
            )
        return (
            f"{attribution.classification} ({attribution.confidence}); "
            f"raw {_pct(attribution.raw_return_pct)} over {attribution.return_window or 'event window'}; "
            f"beta-adjusted {_pct(attribution.beta_adjusted_pct)}."
        )
    capture = top.market_capture
    if capture and capture.price_reaction_pct is not None:
        return f"Raw event reaction {capture.price_reaction_pct:+.1f}%; attribution unavailable."
    return "Price reaction unavailable."


def _pct(value: float | None) -> str:
    return f"{value:+.1f}%" if value is not None else "n/a"


def _market_capture(top: TradeIdea | None) -> str:
    capture = top.market_capture if top else None
    if not capture:
        return "Market capture unavailable."
    if capture.capture_mode == "Price-only":
        return (
            f"Price-only; price status {capture.price_status}; consensus status {capture.consensus_status}. "
            "Event price reaction is available, but point-in-time analyst expectation revision is not. "
            "Do not treat this as an uncaptured/not-priced-in claim."
        )
    detail = capture.diagnosis or capture.explanation or capture.category
    return (
        f"{capture.capture_mode}; {capture.category}; price status {capture.price_status}; "
        f"consensus status {capture.consensus_status}. {detail}"
    )


def _decision(status: str, brief: ThesisBrief, top: TradeIdea | None) -> str:
    if brief.verdict == "No convincing thesis yet" or status == "No thesis yet":
        return "Do not pitch yet"
    if status == "Needs Research-Ready evidence":
        return "Research next"
    if top and top.stage == "High-Conviction" and status == "IC-ready draft":
        return "Pitch as high-conviction candidate"
    if top and top.stage == "Research-Ready":
        return "Discuss as research-ready, not final recommendation"
    return "Investigate before IC discussion"


def _decision_reason(
    status: str,
    brief: ThesisBrief,
    sufficiency: EvidenceSufficiency,
    top: TradeIdea | None,
    gaps: list[str],
) -> str:
    if brief.verdict == "No convincing thesis yet":
        reason = gaps[0] if gaps else "Evidence chain is not yet strong enough for an investable thesis."
        return f"No convincing thesis: {reason}"
    if top and top.gate_result and top.gate_result.research_ready_failed:
        return "Research-Ready blocker: " + top.gate_result.research_ready_failed[0]
    if top and top.gate_result and top.gate_result.high_conviction_failed:
        return "High-Conviction blocker: " + top.gate_result.high_conviction_failed[0]
    if status != "IC-ready draft":
        reason = gaps[0] if gaps else sufficiency.rationale
        return f"{status}: {reason}"
    return f"{sufficiency.status} evidence base with source-linked thesis and monitoring plan."


def _why_now(top: TradeIdea | None, cluster: ThesisCluster | None) -> str:
    if cluster and cluster.why_now:
        return cluster.why_now
    if top and top.catalyst:
        return top.catalyst
    if top and top.driver_attribution and top.driver_attribution.status == "Available":
        return top.driver_attribution.headline or top.driver_attribution.classification
    return "No immediate timing edge identified yet."


def _blocking_issue(status: str, top: TradeIdea | None, gaps: list[str]) -> str:
    if (
        top and top.market_capture and top.market_capture.category == "Unknown"
        and top.market_capture.capture_mode != "Price-only"
    ):
        return top.market_capture.diagnosis or top.market_capture.explanation or "Market capture is unknown."
    if top and top.gate_result and top.gate_result.research_ready_failed:
        return top.gate_result.research_ready_failed[0]
    if top and top.gate_result and top.gate_result.high_conviction_failed:
        return top.gate_result.high_conviction_failed[0]
    if gaps:
        return gaps[0]
    if status == "IC-ready draft":
        return "No blocking issue identified by the deterministic checklist."
    return "Evidence sufficiency remains incomplete."


def _next_best_action(
    work_actions: list[str],
    monitor_actions: list[str],
    validation: ThesisValidationReport | None,
) -> str:
    if work_actions:
        return work_actions[0]
    if validation and validation.next_evidence_actions:
        action = validation.next_evidence_actions[0]
        return f"[{action.priority}] {action.channel}: {action.action} ({action.source})"
    if monitor_actions:
        return monitor_actions[0]
    return "No next action generated; rerun after adding source evidence or monitor criteria."


def _rank_eligibility(top: TradeIdea | None) -> str:
    if not top:
        return "No idea to rank."
    model = top.payoff_model
    if model:
        if model.rank_eligible:
            return "Rank eligible by calibrated EV."
        source = model.probability_provenance.source if model.probability_provenance else "uncalibrated"
        return f"Not EV-rank eligible; probability source is {source}."
    if top.probability_provenance:
        return f"Not EV-rank eligible; probability source is {top.probability_provenance.source}."
    return "Not EV-rank eligible; payoff model or calibrated probabilities are missing."


def _go_no_go_reason(
    status: str,
    brief: ThesisBrief,
    top: TradeIdea | None,
    gaps: list[str],
) -> str:
    if brief.verdict == "No convincing thesis yet":
        return "No-go for IC pitch until the source-backed thesis chain is complete."
    if top and top.stage == "High-Conviction" and status == "IC-ready draft":
        return "Go for IC review, subject to current monitor rules and falsification tests."
    if top and top.stage == "Research-Ready":
        return "Go for analyst debate, but not for final sizing until High-Conviction blockers are cleared."
    if gaps:
        return f"No-go for recommendation: {gaps[0]}"
    return "Proceed cautiously; deterministic checklist has not classified this as High-Conviction."


def _valuation(top: TradeIdea | None, valuation: ValuationResult) -> str:
    ev = expected_value(top.scenarios) if top else None
    if valuation.status == "Available":
        suffix = f"; illustrative EV {ev:+.1f}%" if ev is not None else ""
        return f"{valuation.template}: {valuation.status}{suffix}."
    if ev is not None:
        return f"Valuation {valuation.status}; payoff envelope illustrative EV {ev:+.1f}%."
    missing = "; ".join(valuation.missing_data[:3]) if valuation.missing_data else "scenario fair values missing"
    return f"Valuation {valuation.status}: {missing}."


def _equity_lens(top: TradeIdea | None, economics: CompanyEconomics | None) -> str:
    if top and top.equity_credit_lens.get("equity"):
        return top.equity_credit_lens["equity"]
    if economics:
        kpis = ", ".join(economics.industry_playbook.key_kpis[:4]) or "driver KPIs"
        return f"Equity lens: tie the thesis to {kpis}, valuation, FCF, and capital allocation."
    return "Equity lens unavailable until company economics are built."


def _credit_lens(top: TradeIdea | None, credit_lens: CreditLens | None) -> str:
    if top and top.equity_credit_lens.get("credit"):
        return top.equity_credit_lens["credit"]
    if credit_lens:
        return f"Credit lens: {credit_lens.summary}"
    return "Credit lens unavailable."


def _monitor_actions(top: TradeIdea | None, action_plan: list[ActionPlan]) -> list[str]:
    rows = [
        f"{item.criterion}: watch {item.metric or item.source_field}; confirm if {item.confirm_trigger}; break if {item.break_trigger}."
        for item in action_plan[:4]
    ]
    if rows:
        return rows
    if top:
        return [
            f"{item.criterion}: confirm if {item.confirm_trigger}; break if {item.break_trigger}."
            for item in top.monitor_items[:4]
        ]
    return []


def _evidence_gaps(
    brief: ThesisBrief,
    critique: ThesisCritique,
    validation: ThesisValidationReport | None,
    work_order: EvidenceWorkOrder | None,
) -> list[str]:
    rows: list[str] = []
    rows.extend(brief.data_gaps[:4])
    rows.extend(critique.missing_evidence[:4])
    if validation:
        rows.extend(validation.required_next_evidence[:4])
    if work_order:
        rows.extend(
            item.action
            for item in work_order.items[:6]
            if (item.blocks_research_ready or item.blocks_high_conviction)
            and _work_item_is_open(item)
        )
    return _dedupe(rows)[:10]


def _work_order_actions(work_order: EvidenceWorkOrder | None) -> list[str]:
    if not work_order:
        return []
    return [
        f"[{item.priority}] {item.channel}: {item.action} ({item.source_type})"
        for item in work_order.items[:6]
        if _work_item_is_open(item)
    ]


def _work_item_is_open(item) -> bool:
    return str(getattr(item, "status", "Open") or "Open").lower() not in {
        "resolved", "contradicted", "closed",
    }


def _dedupe(rows: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for row in rows:
        if not row or row in seen:
            continue
        seen.add(row)
        output.append(row)
    return output
