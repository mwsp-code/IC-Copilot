from __future__ import annotations

from .models import (
    ChangeEvent,
    EvidenceClosureReport,
    FinancialMetric,
    ManagementSourcePackage,
    ResearchModeDefinition,
    ResearchModeResult,
    ResearchModeSuite,
    TradeIdea,
)


MODE_DEFINITIONS = (
    ResearchModeDefinition(
        "earnings_review", "Earnings review",
        "Reconcile reported results, guidance, expectations, price reaction, and earnings quality.",
        ["Revenue", "Gross Profit", "Operating Income", "Net Income", "EPS", "Operating Cash Flow"],
        ["sec_filing", "issuer_ir", "earnings_transcript", "consensus_manual", "macro_market"],
        ["Reported periods align", "Earnings bridge separates operating and below-the-line drivers"],
        ["One-time items explain the apparent change", "Cash conversion contradicts reported earnings"],
    ),
    ResearchModeDefinition(
        "margin_investigation", "Margin investigation",
        "Explain gross and operating margin change through price, volume, mix, and cost drivers.",
        ["Revenue", "Gross Profit", "Cost of Revenue", "Operating Income", "R&D Expense", "Sales and Marketing Expense"],
        ["sec_filing", "issuer_ir", "earnings_transcript", "industry_official_dataset", "peer"],
        ["Revenue and cost periods align", "At least one driver is quantified or source-corroborated"],
        ["Mix or temporary credits reverse", "Peers contradict claimed durability"],
    ),
    ResearchModeDefinition(
        "capital_allocation", "Capital-allocation review",
        "Trace cash generation into reinvestment, acquisitions, buybacks, dividends, debt, and dilution.",
        ["Operating Cash Flow", "Capital Expenditure", "Cash", "Share Repurchases", "Dividends Paid", "Shares", "Long-term Debt"],
        ["sec_filing", "issuer_ir", "agm_egm_proxy", "presentation"],
        ["Cash-flow uses reconcile", "Share-count basis is normalized"],
        ["Buybacks merely offset compensation dilution", "Liquidity needs constrain distributions"],
    ),
    ResearchModeDefinition(
        "credit_deterioration", "Credit deterioration",
        "Test liquidity, leverage, refinancing, interest coverage, covenants, and credit-cost pressure.",
        ["Cash", "Current Debt", "Long-term Debt", "Interest Expense", "Operating Cash Flow", "EBITDA"],
        ["sec_filing", "issuer_ir", "macro_market", "paid_market_data"],
        ["Debt and cash share period/currency", "Interest burden connects to cash generation"],
        ["Refinancing extends maturities", "Cash generation offsets leverage"],
    ),
    ResearchModeDefinition(
        "regulatory_event", "Regulatory event",
        "Verify legal or policy developments and connect them to operations, cash flow, and valuation.",
        ["Revenue", "Operating Income", "Cash"],
        ["regulator_court", "sec_filing", "issuer_ir", "news_metadata"],
        ["Official regulator/court or issuer corroboration exists", "Affected business driver is explicit"],
        ["Official source contradicts news framing", "Exposure is immaterial"],
    ),
    ResearchModeDefinition(
        "product_cycle", "Product-cycle change",
        "Connect launches, approvals, demand, inventory, capacity, and mix to financial outcomes.",
        ["Revenue", "Gross Profit", "Inventory", "R&D Expense", "Capital Expenditure"],
        ["issuer_ir", "earnings_transcript", "product_safety", "patent_ip", "industry_official_dataset"],
        ["Product milestone is source-linked", "Demand/capacity KPI is period-aligned"],
        ["Channel inventory rises", "Launch timing or adoption slips"],
    ),
    ResearchModeDefinition(
        "management_credibility", "Management credibility",
        "Compare guidance, promises, capital-allocation commitments, and outcomes over time.",
        ["Revenue", "Operating Income", "Capital Expenditure", "Share Repurchases"],
        ["earnings_transcript", "issuer_ir", "sec_filing", "agm_egm_proxy"],
        ["Promise has speaker, date, metric, period, and citation", "Outcome is observed later"],
        ["Promise remains vague or unresolved", "Subsequent filing contradicts management language"],
    ),
    ResearchModeDefinition(
        "relative_value", "Relative-value comparison",
        "Compare the focal company and curated peers on aligned operating, valuation, and catalyst dimensions.",
        ["Revenue", "Gross Profit", "Operating Income", "Net Income", "Operating Cash Flow", "Shares"],
        ["peer", "global_peer_official_document", "macro_market", "paid_market_data"],
        ["Peer metrics share family and fiscal alignment", "Price hedge/benchmark is explicit"],
        ["Peer differences reflect unrelated business mix", "Hedge ratio is unstable"],
    ),
)


def build_research_mode_suite(
    ticker: str,
    metrics: list[FinancialMetric],
    events: list[ChangeEvent],
    ideas: list[TradeIdea],
    management_sources: ManagementSourcePackage,
    evidence_closure: EvidenceClosureReport,
) -> ResearchModeSuite:
    available = {metric.name for metric in metrics}
    event_text = " ".join(
        f"{event.category} {event.title} {event.summary}" for event in events
    ).lower()
    idea_text = " ".join(
        f"{idea.signal_family} {idea.title} {getattr(idea.driver_analysis, 'primary_driver', '')}"
        for idea in ideas
    ).lower()
    recommended = _recommended_modes(event_text + " " + idea_text)
    results = []
    for definition in MODE_DEFINITIONS:
        present = [name for name in definition.required_metrics if name in available]
        missing = [name for name in definition.required_metrics if name not in available]
        metric_score = round(70 * len(present) / max(1, len(definition.required_metrics)))
        source_score, sources = _source_score(definition, management_sources, ideas, evidence_closure)
        score = min(100, metric_score + source_score)
        if score >= 75:
            status = "Ready"
        elif score >= 45:
            status = "Partial"
        else:
            status = "Blocked"
        next_actions = []
        if missing:
            next_actions.append("Resolve normalized metrics: " + ", ".join(missing[:5]) + ".")
        unresolved = [
            row for row in evidence_closure.outcomes
            if row.status in {"genuinely_unavailable", "licensed_or_manual_required"}
            and any(source in " ".join(attempt.adapter for attempt in row.attempted_adapters).lower()
                    for source in _source_keywords(definition.required_source_types))
        ]
        if unresolved:
            next_actions.append(f"Execute or import {len(unresolved)} unresolved source task(s) for this mode.")
        results.append(ResearchModeResult(
            mode_id=definition.mode_id,
            label=definition.label,
            status=status,
            score=score,
            summary=(
                f"{len(present)}/{len(definition.required_metrics)} core metrics and "
                f"{len(sources)} evidence source lane(s) are available. {definition.purpose}"
            ),
            available_metrics=present,
            missing_metrics=missing,
            evidence_sources=sources,
            next_actions=next_actions,
            recommended=definition.mode_id in recommended,
        ))
    suite_status = "Ready" if any(row.status == "Ready" for row in results) else "Partial"
    return ResearchModeSuite(
        ticker=ticker,
        status=suite_status,
        recommended_mode_ids=[row.mode_id for row in results if row.recommended],
        modes=results,
        data_gaps=[] if results else ["No research modes were evaluated."],
    )


def definitions() -> tuple[ResearchModeDefinition, ...]:
    return MODE_DEFINITIONS


def _recommended_modes(text):
    modes = {"earnings_review", "relative_value"}
    rules = {
        "margin_investigation": ("margin", "gross profit", "cost", "opex"),
        "capital_allocation": ("cash", "buyback", "dividend", "share", "capex", "debt"),
        "credit_deterioration": ("debt", "liquidity", "interest", "credit", "refinanc"),
        "regulatory_event": ("regulat", "litigation", "court", "policy", "antitrust"),
        "product_cycle": ("product", "launch", "inventory", "capacity", "approval", "demand"),
        "management_credibility": ("guidance", "management", "promise", "transcript", "evasion"),
    }
    for mode_id, terms in rules.items():
        if any(term in text for term in terms):
            modes.add(mode_id)
    return modes


def _source_score(definition, management_sources, ideas, closure):
    sources = []
    if any(source in definition.required_source_types for source in ("earnings_transcript", "issuer_ir", "agm_egm_proxy")):
        if management_sources.documents or management_sources.transcript_turns:
            sources.append("Management sources")
    if "peer" in definition.required_source_types and any(idea.peer_metric_readthrough for idea in ideas):
        sources.append("Peer operating metrics")
    if "macro_market" in definition.required_source_types and any(
        idea.driver_attribution and idea.driver_attribution.raw_return_pct is not None for idea in ideas
    ):
        sources.append("Event price/market attribution")
    if any(outcome.status in {"resolved", "contradicted"} for outcome in closure.outcomes):
        sources.append("Closed evidence work orders")
    return min(30, len(sources) * 10), list(dict.fromkeys(sources))


def _source_keywords(source_types):
    keywords = []
    for source in source_types:
        keywords.extend(source.replace("_", " ").split())
    return list(dict.fromkeys(keywords))
