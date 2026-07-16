from __future__ import annotations

import hashlib

from .models import (
    CausalThesisEdge,
    CausalThesisGraph,
    CausalThesisNode,
    ClaimValidationResult,
    CompanyModelWorkspace,
    EvidenceClosureReport,
    MarketImpliedExpectations,
    TradeIdea,
    ValuationResult,
    WisburgResearchLens,
    WisburgStructuredClaim,
)


def build_causal_thesis_graphs(
    ticker: str,
    ideas: list[TradeIdea],
    claims: ClaimValidationResult,
    company_model: CompanyModelWorkspace,
    valuation: ValuationResult,
    market_implied: MarketImpliedExpectations,
    closure: EvidenceClosureReport,
    limit: int = 8,
    wisburg_lens: WisburgResearchLens | None = None,
) -> list[CausalThesisGraph]:
    claim_by_id = {claim.claim_id: claim for claim in claims.claims}
    return [
        _build_graph(
            ticker, idea, claim_by_id, company_model, valuation, market_implied, closure,
            wisburg_lens,
        )
        for idea in ideas[:limit]
    ]


def _build_graph(
    ticker, idea, claim_by_id, company_model, valuation, market_implied, closure,
    wisburg_lens=None,
):
    source_claims = [claim_by_id[claim_id] for claim_id in idea.validated_claim_ids if claim_id in claim_by_id]
    source_evidence = [
        claim.supporting_quote or claim.changed_text or claim.reason
        for claim in source_claims
        if claim.supporting_quote or claim.changed_text or claim.reason
    ]
    primary_citation_ids = [_citation_id(claim.citation) for claim in source_claims if claim.citation]
    external_claims = _matching_wisburg_claims(idea, wisburg_lens)
    external_evidence = [
        (
            f"[{claim.evidence_label}; Tier {claim.source_tier}; {claim.corroboration_status}] "
            f"{claim.statement}"
        )
        for claim in external_claims
    ]
    external_citation_ids = [
        _citation_id(claim.citation) for claim in external_claims if claim.citation
    ]
    source_evidence.extend(external_evidence)
    citation_ids = primary_citation_ids + external_citation_ids
    source_score = 95 if source_claims and primary_citation_ids else 70 if idea.citations else 30
    if idea.signal_family == "wisburg_external_theme" and not source_claims:
        source_score = min(source_score, 45)
    source_gap = [] if source_score >= 70 else ["A citation-bound validated source claim is missing."]
    if any(claim.corroboration_status == "Contradicted by primary evidence" for claim in external_claims):
        source_gap.append("Primary evidence contradicts related Wisburg external context.")
    if (
        idea.signal_family == "wisburg_external_theme"
        and external_claims
        and not any(claim.corroboration_status == "Primary context corroborated" for claim in external_claims)
    ):
        source_gap.append("Wisburg-only context still needs primary-source corroboration.")

    driver = _driver_label(idea)
    driver_score = 90 if driver and driver.lower() not in {"unmapped", "unknown"} else 25
    driver_gap = [] if driver_score >= 70 else ["Map the source claim to a material company or industry driver."]

    kpi_names = _relevant_kpis(idea, company_model)
    kpi_score = min(95, 35 + len(kpi_names) * 15) if kpi_names else 25
    kpi_gap = [] if kpi_names else ["No period-aligned operating KPI was found for this driver."]

    earnings_evidence = _earnings_evidence(company_model)
    earnings_score = 85 if earnings_evidence else 35
    earnings_gap = [] if earnings_evidence else ["Connect the KPI change to operating income, net income, or free cash flow."]

    fair_values = [case.fair_value for case in valuation.cases if case.fair_value is not None]
    implied = [row for row in market_implied.expectations if row.implied_value is not None]
    valuation_score = 90 if fair_values else 65 if implied else 25
    valuation_gap = [] if valuation_score >= 65 else ["Internal fair values and reverse-implied expectations are both unavailable."]

    catalyst_specific = bool(idea.catalyst and idea.catalyst.lower() not in {
        "next earnings report, guidance update, or consensus revision cycle", "unknown", "n/a",
    })
    monitored = bool(idea.monitor_items)
    catalyst_score = 90 if catalyst_specific and monitored else 65 if idea.catalyst and monitored else 35
    catalyst_gap = [] if catalyst_score >= 65 else ["Add a dated catalyst and machine-readable confirmation/break monitor."]

    nodes = [
        _node(idea.idea_id, "source_event", "Source event", source_score, source_evidence or [event.title for event in idea.source_events], citation_ids),
        _node(idea.idea_id, "business_driver", driver or "Unmapped driver", driver_score, [idea.direction_rationale] if idea.direction_rationale else []),
        _node(idea.idea_id, "operating_kpi", ", ".join(kpi_names[:4]) or "Operating KPI missing", kpi_score, kpi_names),
        _node(idea.idea_id, "earnings_fcf", "Earnings / FCF bridge", earnings_score, earnings_evidence),
        _node(idea.idea_id, "valuation", "Valuation / market-implied expectations", valuation_score, _valuation_evidence(valuation, implied)),
        _node(idea.idea_id, "price_catalyst", idea.catalyst or "Catalyst missing", catalyst_score, [item.criterion for item in idea.monitor_items]),
    ]
    edge_specs = [
        ("source_event", "business_driver", "Source event changes the driver", min(source_score, driver_score), source_evidence, source_gap + driver_gap, "Run registered claim validation and driver mapping."),
        ("business_driver", "operating_kpi", "Driver changes an operating KPI", min(driver_score, kpi_score), kpi_names, driver_gap + kpi_gap, "Execute driver-specific metric and peer work orders."),
        ("operating_kpi", "earnings_fcf", "KPI flows into earnings or FCF", min(kpi_score, earnings_score), earnings_evidence, kpi_gap + earnings_gap, "Build the operating bridge in the company model workspace."),
        ("earnings_fcf", "valuation", "Earnings / FCF changes value", min(earnings_score, valuation_score), _valuation_evidence(valuation, implied), earnings_gap + valuation_gap, "Complete internal valuation or reverse-implied assumptions."),
        ("valuation", "price_catalyst", "Catalyst can close the valuation gap", min(valuation_score, catalyst_score), [idea.catalyst] if idea.catalyst else [], valuation_gap + catalyst_gap, "Attach a dated catalyst and monitor thresholds."),
    ]
    edges = [
        _edge(idea.idea_id, *spec)
        for spec in edge_specs
    ]
    weakest = min(edges, key=lambda edge: edge.score)
    overall = round(sum(edge.score for edge in edges) / len(edges))
    closure_gap = _related_closure_gap(idea, closure)
    if closure_gap and weakest.score >= 70:
        weakest.missing_evidence.append(closure_gap)
        weakest.score = min(weakest.score, 65)
        weakest.status = "Weak"
        overall = round(sum(edge.score for edge in edges) / len(edges))
    status = "Complete" if all(edge.score >= 70 for edge in edges) else "Incomplete"
    gaps = list(dict.fromkeys(gap for edge in edges for gap in edge.missing_evidence))
    return CausalThesisGraph(
        idea_id=idea.idea_id,
        ticker=ticker,
        status=status,
        overall_score=overall,
        weakest_link=weakest.label,
        summary=(
            f"{status}: weakest connection is '{weakest.label}' at {weakest.score}/100. "
            f"{weakest.missing_evidence[0] if weakest.missing_evidence else weakest.explanation}"
        ),
        nodes=nodes,
        edges=edges,
        data_gaps=gaps,
    )


def _node(idea_id, node_type, label, score, evidence, citation_ids=None):
    return CausalThesisNode(
        node_id=f"{idea_id}:{node_type}",
        node_type=node_type,
        label=label,
        status="Supported" if score >= 70 else "Partial" if score >= 45 else "Weak",
        evidence=list(evidence or [])[:8],
        citation_ids=list(citation_ids or []),
    )


def _edge(idea_id, from_type, to_type, label, score, evidence, gaps, next_action):
    status = "Strong" if score >= 80 else "Adequate" if score >= 70 else "Partial" if score >= 45 else "Weak"
    return CausalThesisEdge(
        edge_id=f"{idea_id}:{from_type}:{to_type}",
        from_node=f"{idea_id}:{from_type}",
        to_node=f"{idea_id}:{to_type}",
        label=label,
        score=score,
        status=status,
        explanation=(
            "Connection is supported by normalized evidence."
            if score >= 70 else
            "Connection remains provisional until the named evidence gap is closed."
        ),
        evidence=list(evidence or [])[:8],
        missing_evidence=list(dict.fromkeys(gaps)),
        next_automatic_action=next_action,
    )


def _driver_label(idea):
    if idea.driver_analysis and idea.driver_analysis.primary_driver:
        return idea.driver_analysis.primary_driver
    if idea.causal_bridge and idea.causal_bridge.driver_family:
        return idea.causal_bridge.driver_family
    return idea.thesis_cluster_label or "Unmapped"


def _relevant_kpis(idea, model):
    driver = _driver_label(idea).lower()
    families = {
        "margin": ("Revenue", "Gross Profit", "Operating Income"),
        "cash": ("Operating Cash Flow", "Capital Expenditure", "Cash"),
        "debt": ("Cash", "Current Debt", "Long-term Debt", "Interest Expense"),
        "revenue": ("Revenue", "Gross Profit"),
        "share": ("Shares", "Share Repurchases", "Dividends Paid"),
        "capital": ("Operating Cash Flow", "Capital Expenditure", "Share Repurchases"),
        "earnings": ("Revenue", "Operating Income", "Net Income", "EPS"),
    }
    requested = next((names for key, names in families.items() if key in driver), ())
    available = {row.metric for row in model.historicals}
    names = [name for name in requested if name in available]
    if not names:
        event_text = " ".join(
            f"{event.title} {event.summary} {' '.join(str(key) for key in (event.metrics or {}))}"
            for event in idea.source_events
        ).lower()
        names = [name for name in available if name.lower() in event_text]
    return names


def _earnings_evidence(model):
    rows = []
    for case in model.cases:
        if case.net_income is not None:
            rows.append(f"{case.name} net income {case.net_income:,.0f}")
        if case.free_cash_flow is not None:
            rows.append(f"{case.name} FCF {case.free_cash_flow:,.0f}")
    return rows[:8]


def _valuation_evidence(valuation, implied):
    rows = [
        f"{case.name} fair value {case.fair_value:,.2f} {valuation.currency}"
        for case in valuation.cases if case.fair_value is not None
    ]
    rows.extend(
        f"{row.metric}: {row.implied_value:,.2f} {row.unit}"
        for row in implied[:3]
    )
    return rows


def _related_closure_gap(idea, closure):
    for outcome in closure.outcomes:
        if outcome.status in {"genuinely_unavailable", "licensed_or_manual_required"}:
            return outcome.summary
    return ""


def _citation_id(citation):
    raw = "|".join(filter(None, [citation.source, citation.url, citation.accession, citation.section]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _matching_wisburg_claims(
    idea: TradeIdea,
    lens: WisburgResearchLens | None,
) -> list[WisburgStructuredClaim]:
    if not lens:
        return []
    driver = _driver_label(idea).strip().lower()
    event_text = " ".join(
        f"{event.title} {event.summary} {event.metrics.get('economic_driver', '')}"
        for event in idea.source_events
    ).lower()
    matches = []
    for claim in lens.structured_claims:
        claim_driver = claim.driver.strip().lower()
        claim_metric = (claim.metric or "").strip().lower()
        if (
            driver not in {"", "unknown", "unmapped"}
            and (driver in claim_driver or claim_driver in driver)
        ) or (claim_metric and claim_metric in event_text):
            matches.append(claim)
    return matches[:6]
