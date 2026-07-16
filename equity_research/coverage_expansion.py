from __future__ import annotations

from .adr_profiles import adr_profile_for
from .models import (
    CompanyEconomics,
    CompanyIdentity,
    ConsensusPackage,
    CoverageExpansionAction,
    CoverageExpansionDiagnostics,
    EntityResolution,
    FinancialCoverage,
    ResearchSourcePlan,
    ThesisCluster,
    TradeIdea,
    ValuationResult,
)


REGISTRATION_STATUSES = {"registration_only", "facts_unmapped"}


def build_coverage_expansion_diagnostics(
    identity: CompanyIdentity,
    entity_resolution: EntityResolution,
    financial_coverage: FinancialCoverage,
    company_economics: CompanyEconomics,
    consensus: ConsensusPackage | None,
    valuation: ValuationResult | None,
    ideas: list[TradeIdea],
    thesis_clusters: list[ThesisCluster],
    source_plan: ResearchSourcePlan | None,
) -> CoverageExpansionDiagnostics:
    profile = _coverage_profile(identity, entity_resolution, financial_coverage)
    research_ready_blockers = _gate_blockers(ideas, "research_ready_failed")
    high_conviction_blockers = _gate_blockers(ideas, "high_conviction_failed")
    reasons = _why_no_convincing_thesis(
        profile,
        financial_coverage,
        company_economics,
        consensus,
        valuation,
        ideas,
        thesis_clusters,
        research_ready_blockers,
        high_conviction_blockers,
    )
    actions = _recommended_expansions(
        identity,
        profile,
        financial_coverage,
        company_economics,
        consensus,
        valuation,
        ideas,
        source_plan,
    )
    status = _status(ideas, reasons)
    return CoverageExpansionDiagnostics(
        ticker=identity.ticker.upper(),
        status=status,
        coverage_profile=profile,
        summary=_summary(status, profile, reasons, actions),
        why_no_convincing_thesis=reasons,
        research_ready_blockers=research_ready_blockers[:8],
        high_conviction_blockers=high_conviction_blockers[:8],
        recommended_expansions=actions[:12],
        latency_policy=[
            "Run SEC filings, XBRL/company facts, local snapshots, and cached price bars synchronously.",
            "Fetch issuer-IR pages, transcripts, PDFs, and external research with cache/cooldown controls so slow sources do not block SEC research.",
            "Promote only after deterministic evidence, source citations, valuation/payoff inputs, and monitor criteria pass the same gates.",
        ],
        integrity_notes=[
            "Do not infer dilution, consensus revisions, or valuation from missing data.",
            "Treat ADR/FPI benchmarks, macro, and external research as attribution context unless corroborated by primary issuer or SEC evidence.",
            "Use point-in-time consensus/imported snapshots observed on or before the event date for market-capture claims.",
        ],
    )


def _coverage_profile(
    identity: CompanyIdentity,
    resolution: EntityResolution,
    coverage: FinancialCoverage,
) -> str:
    forms = {form.upper() for form in resolution.reporting_forms}
    if coverage.status in REGISTRATION_STATUSES and forms & {"S-1", "S-1/A", "F-1", "F-1/A", "424B4"}:
        return "IPO / registration-stage prospectus workflow"
    if adr_profile_for(identity.ticker, tuple(forms)) or forms & {"20-F", "40-F", "6-K"}:
        return "ADR/FPI overlay workflow"
    if coverage.status == "available":
        return "Standard U.S. operating-company workflow"
    return "Coverage-gap workflow"


def _status(ideas: list[TradeIdea], reasons: list[str]) -> str:
    if any(idea.stage in {"High-Conviction", "Investable"} for idea in ideas):
        return "High-Conviction thesis exists"
    if any(idea.stage == "Research-Ready" for idea in ideas):
        return "Research-Ready only"
    if reasons:
        return "No convincing thesis yet"
    return "Early research"


def _summary(
    status: str,
    profile: str,
    reasons: list[str],
    actions: list[CoverageExpansionAction],
) -> str:
    if status == "High-Conviction thesis exists":
        return f"{profile}: at least one idea passed the strict High-Conviction gate."
    if status == "Research-Ready only":
        return f"{profile}: at least one idea is research-ready, but high-conviction blockers remain."
    if actions:
        return f"{profile}: no convincing thesis yet; next step is {actions[0].action}"
    if reasons:
        return f"{profile}: no convincing thesis yet because {reasons[0]}"
    return f"{profile}: early research diagnostics are available."


def _why_no_convincing_thesis(
    profile: str,
    coverage: FinancialCoverage,
    economics: CompanyEconomics,
    consensus: ConsensusPackage | None,
    valuation: ValuationResult | None,
    ideas: list[TradeIdea],
    clusters: list[ThesisCluster],
    research_ready_blockers: list[str],
    high_conviction_blockers: list[str],
) -> list[str]:
    reasons: list[str] = []
    if not any(idea.stage in {"Research-Ready", "High-Conviction", "Investable"} for idea in ideas):
        reasons.append("No idea currently passes the Research-Ready gate.")
    if not any(idea.stage in {"High-Conviction", "Investable"} for idea in ideas):
        reasons.append("No idea currently passes the High-Conviction gate.")
    if profile.startswith("IPO / registration"):
        reasons.append(
            "The issuer has registration/prospectus-style coverage; operating history, capitalization, dilution, lock-up, and use-of-proceeds data must be parsed before valuation."
        )
    if coverage.status in {"facts_unmapped", "registration_only", "no_periodic_xbrl"}:
        reasons.append(f"Financial coverage is {coverage.status}: {coverage.reason}")
    if economics.material_driver_count == 0:
        reasons.append("No material company or industry driver has been validated from structured facts or source-linked disclosures.")
    if any(cluster.status == "Needs driver mapping" for cluster in clusters):
        reasons.append("At least one thesis cluster is unmapped, so it cannot be tested against company economics.")
    if _has_share_normalization_gap(ideas):
        reasons.append("A share-count signal needs ordinary-share/ADS/weighted-average/buyback reconciliation before it can support dilution or buyback claims.")
    if consensus and consensus.status != "Available":
        reasons.append("Point-in-time consensus snapshots are unavailable, so market capture and priced-in status cannot be classified.")
    elif consensus and _revision_count(consensus) == 0:
        reasons.append("Consensus is available, but no 7/30/90-day revision windows exist yet; seed historical snapshots or keep monitoring.")
    if valuation and valuation.status != "Available":
        reasons.append("Internal valuation does not yet provide bull/base/bear fair values.")
    if research_ready_blockers:
        reasons.append(f"Research-Ready blocker: {research_ready_blockers[0]}")
    if high_conviction_blockers:
        reasons.append(f"High-Conviction blocker: {high_conviction_blockers[0]}")
    return _dedupe(reasons)[:10]


def _recommended_expansions(
    identity: CompanyIdentity,
    profile: str,
    coverage: FinancialCoverage,
    economics: CompanyEconomics,
    consensus: ConsensusPackage | None,
    valuation: ValuationResult | None,
    ideas: list[TradeIdea],
    source_plan: ResearchSourcePlan | None,
) -> list[CoverageExpansionAction]:
    actions: list[CoverageExpansionAction] = []
    if profile.startswith("IPO / registration"):
        actions.extend([
            _action(
                "Prospectus operating model",
                "High",
                "sec_filing",
                "Parse latest S-1/F-1/424B4 tagged facts and prospectus sections for revenue, loss, cash flow, capex, use of proceeds, capitalization, and dilution.",
                "SPCX-like names often lack mature Company Facts; the prospectus is the primary operating and security-basis source.",
                "Only use tagged Inline XBRL or cited prospectus sections; keep untagged tables as excerpts until manually verified.",
                "IPO/prospectus driver map and valuation inputs.",
            ),
            _action(
                "IPO security mechanics",
                "High",
                "sec_filing",
                "Extract offering price, shares offered, float, lock-up terms, voting control, warrants/options, and insider ownership.",
                "Early public trading can be driven by float, lock-up, and dilution mechanics rather than operating KPIs.",
                "Do not infer tradable float or dilution without exact prospectus citations.",
                "IPO mechanics checklist and monitor dates.",
            ),
        ])
    if profile.startswith("ADR/FPI"):
        playbook_drivers = ", ".join(economics.industry_playbook.leading_indicators[:6])
        actions.extend([
            _action(
                "ADR/FPI segment evidence",
                "High",
                "issuer_ir",
                "Pull issuer results decks, 6-K exhibits, annual reports, AGM/EGM materials, and transcript sections for segment KPIs.",
                "ADR/FPI filings are less standardized, so segment economics often live in issuer artifacts outside Company Facts.",
                "Issuer decks/transcripts can support Research-Ready only after citation, source tiering, and cross-checking against 20-F/6-K facts.",
                f"Segment driver table for {playbook_drivers or 'home-market demand, FX, policy risk, and capital return'}.",
            ),
            _action(
                "ADR share reconciliation",
                "High",
                "sec_filing",
                "Reconcile ordinary shares, ADS ratio, weighted-average shares, period-end shares, buybacks, and split/corporate-action history.",
                "A large share-count move can be ordinary-vs-ADS basis or buyback accounting, not true dilution.",
                "If reconciliation is incomplete, keep the idea as Watch / Needs normalization.",
                "ShareReconciliation with basis, gaps, and citations.",
            ),
            _action(
                "ADR attribution bundle",
                "Medium",
                "macro_market",
                "Add KWEB, MCHI, HSTECH when supported, CNH/RMB FX, and China retail-sales CSV to event attribution.",
                "China ADR moves can be driven by sector, FX, and home-market macro rather than company-specific evidence.",
                "Use this as attribution context only; never as standalone High-Conviction evidence.",
                "China ADR attribution waterfall.",
            ),
        ])
    if _consensus_history_gap(consensus):
        actions.append(_action(
            "Consensus history seeding",
            "High",
            "consensus_manual",
            "Import CSV snapshots for targets, estimates, recommendations, surprises, target revisions, estimate revisions, and provider metadata.",
            "Market-capture claims need point-in-time pre/post expectations, not only today’s consensus.",
            "Historical events may use only snapshots observed on or before the event date.",
            "7/30/90-day revision windows and clearer priced-in diagnostics.",
        ))
    if valuation and valuation.status != "Available":
        actions.append(_action(
            "Valuation bridge",
            "High",
            "presentation",
            "Build bull/base/bear operating assumptions from revenue, margin, FCF/EPS/book value, share count, net debt, and peer/history multiples.",
            "EV should be based on explicit exit values, not idea scores or raw signal severity.",
            "External analyst targets remain comparison benchmarks, not fair-value inputs.",
            "Scenario fair values and payoff table.",
        ))
    if any(_is_weak_or_unmapped(idea) for idea in ideas):
        actions.append(_action(
            "Claim validation follow-up",
            "Medium",
            "sec_filing",
            "For each weak idea, capture exact current excerpt, prior excerpt, changed phrase, direction, affected driver, and counter-evidence.",
            "Keyword deltas and vague management language should produce watch items, not trade theses.",
            "If exact negative or positive language cannot be pinned down, say No convincing thesis yet.",
            "ValidatedClaim records and thesis-grade decisions.",
        ))
    if source_plan and source_plan.requests:
        request = source_plan.requests[0]
        actions.append(_action(
            "Next planned source",
            request.priority,
            request.source_type,
            request.title,
            request.reason_to_inspect,
            "Use only registered source types and source-linked citations; deterministic adapters perform fetching.",
            request.expected_evidence_type,
            request.cost_latency,
        ))
    return _dedupe_actions(actions)


def _action(
    area: str,
    priority: str,
    source_type: str,
    action: str,
    why: str,
    integrity: str,
    expected: str = "",
    cost_latency: str = "Free / variable latency",
) -> CoverageExpansionAction:
    return CoverageExpansionAction(
        area=area,
        priority=priority if priority in {"High", "Medium", "Low"} else "Medium",
        source_type=source_type,
        action=action,
        why_it_matters=why,
        integrity_rule=integrity,
        expected_output=expected,
        cost_latency=cost_latency,
    )


def _gate_blockers(ideas: list[TradeIdea], field: str) -> list[str]:
    blockers: list[str] = []
    for idea in ideas:
        gate = idea.gate_result
        if not gate:
            continue
        blockers.extend(str(item) for item in getattr(gate, field, []) if item)
    return _dedupe(blockers)


def _has_share_normalization_gap(ideas: list[TradeIdea]) -> bool:
    for idea in ideas:
        if idea.share_reconciliation and idea.share_reconciliation.status != "Reconciled":
            return True
        for event in idea.source_events:
            if event.metrics.get("normalization_required"):
                return True
    return False


def _consensus_history_gap(consensus: ConsensusPackage | None) -> bool:
    if consensus is None:
        return True
    if consensus.status != "Available":
        return True
    return _revision_count(consensus) == 0


def _revision_count(consensus: ConsensusPackage) -> int:
    return sum(1 for revision in consensus.revisions if revision.status == "available")


def _is_weak_or_unmapped(idea: TradeIdea) -> bool:
    if idea.stage not in {"Research-Ready", "High-Conviction", "Investable"}:
        return True
    if idea.thesis_grade_status in {"Watch Item", "Not thesis-grade", "Unvalidated"}:
        return True
    return bool(idea.source_events and idea.source_events[0].metrics.get("economic_driver") == "Unmapped")


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _dedupe_actions(actions: list[CoverageExpansionAction]) -> list[CoverageExpansionAction]:
    deduped: dict[tuple[str, str], CoverageExpansionAction] = {}
    for action in actions:
        deduped.setdefault((action.area, action.source_type), action)
    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(deduped.values(), key=lambda item: (order.get(item.priority, 1), item.area))
