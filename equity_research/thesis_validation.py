from __future__ import annotations

from statistics import mean

from .idea_engine import expected_value
from .models import (
    ConsensusPackage,
    EvidenceActionItem,
    EvidenceLedger,
    ExternalEvidenceBundle,
    HistoricalReferenceSet,
    ManagementSourcePackage,
    ThesisValidationCheck,
    ThesisValidationReport,
    TradeIdea,
    ValuationResult,
)


def build_thesis_validation_report(
    ideas: list[TradeIdea],
    evidence: EvidenceLedger,
    consensus: ConsensusPackage,
    valuation: ValuationResult,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
    historical_references: HistoricalReferenceSet,
) -> ThesisValidationReport:
    top = _top_idea(ideas)
    if not top:
        return ThesisValidationReport(
            status="No thesis",
            score=0,
            summary="No generated idea exists to validate across evidence channels.",
            top_idea_id=None,
            top_idea_title="No thesis",
            required_next_evidence=["Generate a source-linked idea before running thesis validation."],
        )
    checks = [
        _filing_check(top, evidence),
        _management_check(top, management_sources),
        _consensus_check(top, consensus),
        _market_reaction_check(top),
        _peer_check(top),
        _valuation_check(top, valuation),
        _historical_check(historical_references),
        _external_context_check(external_evidence),
    ]
    score = round(mean(check.score for check in checks)) if checks else 0
    contradictions = [check for check in checks if check.status == "Contradicts"]
    supports = [check for check in checks if check.status == "Supports"]
    if contradictions:
        status = "Contested"
    elif score >= 75 and len(supports) >= 4:
        status = "Validated"
    elif score >= 50 and supports:
        status = "Partially validated"
    else:
        status = "Weakly validated"
    next_evidence = []
    for check in checks:
        if check.status in {"Missing", "Mixed", "Contradicts"}:
            next_evidence.extend(check.gaps or [f"Resolve {check.channel.lower()} evidence."])
    actions = _evidence_actions(checks)
    summary = (
        f"{status}: {len(supports)} of {len(checks)} evidence channels support the top thesis; "
        f"{len(contradictions)} channel(s) contradict it. Validation score {score}/100."
    )
    return ThesisValidationReport(
        status=status,
        score=score,
        summary=summary,
        top_idea_id=top.idea_id,
        top_idea_title=top.title,
        checks=checks,
        strongest_supports=[f"{check.channel}: {check.evidence}" for check in supports[:4]],
        strongest_contradictions=[f"{check.channel}: {check.evidence}" for check in contradictions[:4]],
        required_next_evidence=_dedupe(next_evidence)[:8],
        next_evidence_actions=actions[:8],
    )


def _filing_check(top: TradeIdea, evidence: EvidenceLedger) -> ThesisValidationCheck:
    claim = next((item for item in evidence.claims if item.idea_id == top.idea_id), None)
    support_ids = set(claim.supporting_evidence_ids if claim else [])
    counter_ids = set(claim.contradicting_evidence_ids if claim else [])
    supports = [item for item in evidence.items if item.evidence_id in support_ids]
    counters = [item for item in evidence.items if item.evidence_id in counter_ids]
    tier1 = [item for item in supports if item.source_tier == 1]
    if counters:
        strongest = max(counters, key=lambda item: item.materiality)
        return _check(
            "SEC / issuer filings",
            "Contradicts" if strongest.materiality >= 3 else "Mixed",
            f"{len(counters)} contradicting evidence item(s); strongest: {strongest.statement}",
            "Primary-source contradiction weakens the thesis until resolved.",
            ["Resolve or downgrade the top thesis before promotion."],
            source_tier=strongest.source_tier,
            citation_count=sum(1 for item in counters if item.citation),
        )
    if tier1:
        return _check(
            "SEC / issuer filings",
            "Supports",
            f"{len(tier1)} Tier 1 supporting evidence item(s) linked to the top thesis.",
            "The thesis has filing/issuer evidence rather than model-only reasoning.",
            [],
            source_tier=1,
            citation_count=sum(1 for item in tier1 if item.citation),
        )
    if supports:
        return _check(
            "SEC / issuer filings",
            "Mixed",
            f"{len(supports)} support item(s), but none are Tier 1.",
            "The thesis has evidence, but lacks the primary-source backbone required for high conviction.",
            ["Add or verify SEC/issuer citations for the top claim."],
            source_tier=min(item.source_tier for item in supports),
            citation_count=sum(1 for item in supports if item.citation),
        )
    return _check(
        "SEC / issuer filings",
        "Missing",
        "No evidence-ledger support is linked to the top thesis.",
        "A chatbot can invent a narrative here; this workflow should not.",
        ["Add source-linked filing, issuer, or XBRL evidence for the top thesis."],
    )


def _management_check(
    top: TradeIdea,
    management_sources: ManagementSourcePackage,
) -> ThesisValidationCheck:
    claim_id = ""
    if top.source_events:
        claim_id = str(top.source_events[0].metrics.get("management_claim_id") or "")
    linked_checks = [
        item for item in management_sources.cross_checks
        if not claim_id or item.claim_id == claim_id
    ]
    confirmed = [item for item in linked_checks if item.status == "Confirmed"]
    contradicted = [item for item in linked_checks if item.status == "Contradicted"]
    if contradicted:
        return _check(
            "Management statements",
            "Contradicts",
            f"{len(contradicted)} management claim cross-check(s) contradict the thesis.",
            "Management language conflicts with facts or other source evidence.",
            ["Resolve the contradicted management claim before using it as support."],
            source_tier=min(item.source_tier for item in contradicted),
        )
    if confirmed:
        return _check(
            "Management statements",
            "Supports",
            f"{len(confirmed)} management claim cross-check(s) confirm the claim.",
            "Management commentary is corroborated rather than accepted at face value.",
            [],
            source_tier=min(item.source_tier for item in confirmed),
        )
    if management_sources.claims:
        return _check(
            "Management statements",
            "Mixed",
            f"{len(management_sources.claims)} management claim(s) found, but none confirmed for the top thesis.",
            "Management language is useful context but not decisive evidence yet.",
            ["Cross-check management claims against filings, facts, consensus revisions, or price reaction."],
            source_tier=min((claim.source_tier for claim in management_sources.claims), default=2),
        )
    return _check(
        "Management statements",
        "Missing",
        "No management-source claims are available.",
        "Transcript/proxy/meeting evidence is absent from thesis validation.",
        ["Add earnings calls, 8-K/6-K exhibits, investor presentations, or manual transcript imports."],
    )


def _consensus_check(top: TradeIdea, consensus: ConsensusPackage) -> ThesisValidationCheck:
    revision = top.market_capture.consensus_revision_pct if top.market_capture else None
    has_official = (consensus.target and consensus.target.official) or any(
        status.official and status.status != "Unavailable" for status in consensus.provider_statuses
    )
    has_expectations = bool(consensus.target or consensus.estimates or consensus.recommendations or consensus.surprises)
    if revision is not None and has_official:
        return _check(
            "Consensus / expectations",
            "Supports" if abs(revision) >= 1 else "Mixed",
            f"Official consensus context available; top-idea revision {revision:+.1f}%.",
            "The thesis can be compared with what analysts were already changing.",
            [] if abs(revision) >= 1 else ["Consensus revision is small; market-capture claim needs more evidence."],
            source_tier=3,
        )
    if has_expectations:
        return _check(
            "Consensus / expectations",
            "Mixed",
            f"Expectations package is {consensus.status}, but revision history is incomplete.",
            "Useful for context, but not enough to prove the idea is uncaptured.",
            consensus.data_gaps[:4] or ["Record more point-in-time consensus snapshots."],
            source_tier=3,
        )
    return _check(
        "Consensus / expectations",
        "Missing",
        "No usable consensus, estimate, recommendation, or surprise evidence is available.",
        "The app cannot judge whether expectations already moved.",
        consensus.data_gaps[:4] or ["Configure official/free consensus sources or CSV snapshots."],
    )


def _market_reaction_check(top: TradeIdea) -> ThesisValidationCheck:
    attribution = top.driver_attribution
    capture = top.market_capture
    reaction = capture.price_reaction_pct if capture else None
    if attribution and attribution.status == "Available":
        directional = _directional_return_supports(top.direction, attribution.beta_adjusted_pct)
        if directional is False:
            return _check(
                "Market reaction / attribution",
                "Contradicts",
                f"Beta-adjusted move {attribution.beta_adjusted_pct:+.1f}% conflicts with {top.direction} thesis.",
                "The event-window move does not agree with the proposed direction.",
                ["Explain why the market reaction is misleading or wait for corroborating evidence."],
                source_tier=3,
            )
        return _check(
            "Market reaction / attribution",
            "Supports" if directional is True else "Mixed",
            f"{attribution.classification}; raw {attribution.raw_return_pct if attribution.raw_return_pct is not None else 'n/a'}%, beta-adjusted {attribution.beta_adjusted_pct if attribution.beta_adjusted_pct is not None else 'n/a'}%.",
            "The event move is decomposed instead of guessed from headlines.",
            attribution.data_gaps[:4],
            source_tier=3,
        )
    if reaction is not None:
        directional = _directional_return_supports(top.direction, reaction)
        return _check(
            "Market reaction / attribution",
            "Supports" if directional is True else "Mixed" if directional is None else "Contradicts",
            f"Raw price reaction is {reaction:+.1f}%.",
            "Raw reaction exists but attribution details are limited.",
            ["Add market/sector/beta attribution for stronger causal explanation."],
            source_tier=3,
        )
    return _check(
        "Market reaction / attribution",
        "Missing",
        "No event-date price reaction is available.",
        "The idea cannot be tied to observed market behavior yet.",
        ["Load price bars and event-window reactions for the source event."],
    )


def _peer_check(top: TradeIdea) -> ThesisValidationCheck:
    if not top.peer_readthrough:
        return _check(
            "Peer / sector read-through",
            "Missing",
            "No peer read-through checks are attached to the top thesis.",
            "Sector sympathy or contradiction has not been tested.",
            ["Configure a curated peer basket or rerun with peer coverage."],
        )
    confirming = [item for item in top.peer_readthrough if item.relation == "Confirming read-through"]
    contradicting = [item for item in top.peer_readthrough if item.relation == "Contradicting read-through"]
    price_ready = [
        item for item in top.peer_readthrough
        if item.sympathy_reaction and item.sympathy_reaction.status == "available"
    ]
    if contradicting and len(contradicting) >= len(confirming):
        return _check(
            "Peer / sector read-through",
            "Contradicts",
            f"{len(contradicting)} peer(s) contradict versus {len(confirming)} confirming.",
            "The setup may be company-specific or the sector thesis may be wrong.",
            ["Review contradicting peers before treating this as a sector/relative-value setup."],
            source_tier=1,
            citation_count=sum(len(item.citations) for item in contradicting),
        )
    if confirming:
        return _check(
            "Peer / sector read-through",
            "Supports" if price_ready else "Mixed",
            f"{len(confirming)} confirming peer(s); {len(price_ready)} peer price reaction(s) available.",
            "Peer evidence tests whether the signal is isolated or sector-relevant.",
            [] if price_ready else ["Peer SEC evidence exists, but price-reaction windows are incomplete."],
            source_tier=1,
            citation_count=sum(len(item.citations) for item in confirming),
        )
    return _check(
        "Peer / sector read-through",
        "Mixed",
        "Peer checks ran but found no direct confirming evidence.",
        "The setup may be company-specific or peer data may be sparse.",
        ["Add peer-own-event checks or industry-specific KPIs."],
    )


def _valuation_check(top: TradeIdea, valuation: ValuationResult) -> ThesisValidationCheck:
    ev = expected_value(top.scenarios)
    if valuation.status == "Available" and ev is not None:
        directional = _directional_return_supports(top.direction, ev)
        return _check(
            "Valuation / payoff",
            "Supports" if directional is True else "Contradicts" if directional is False else "Mixed",
            f"Internal valuation is available; illustrative EV {ev:+.1f}%.",
            "The thesis has a payoff anchor rather than only narrative appeal.",
            [],
            source_tier=3,
        )
    if valuation.status == "Available" or ev is not None:
        return _check(
            "Valuation / payoff",
            "Mixed",
            f"Valuation status {valuation.status}; illustrative EV {'available' if ev is not None else 'unavailable'}.",
            "The payoff bridge is incomplete.",
            valuation.missing_data[:4] or ["Complete scenario exits, entry price, and valuation bridge."],
            source_tier=3,
        )
    return _check(
        "Valuation / payoff",
        "Missing",
        f"Valuation status {valuation.status}.",
        "Without valuation or payoff, the idea is hard to size or compare.",
        valuation.missing_data[:4] or ["Add explicit bull/base/bear valuation assumptions."],
    )


def _historical_check(historical_references: HistoricalReferenceSet) -> ThesisValidationCheck:
    if historical_references.status == "Supported":
        hit_rate = historical_references.hit_rate_pct
        if hit_rate is not None and hit_rate <= 35:
            return _check(
                "Historical analogs",
                "Contradicts",
                historical_references.summary,
                (
                    "Similar resolved local ideas had poor outcomes. Treat this as a Tier 4 warning "
                    "and investigate why the current thesis is different before relying on it."
                ),
                [
                    "Explain why current evidence, valuation, catalyst timing, or market capture differs from failed analogs.",
                    "Do not use historical analogs as support until resolved hit rate improves.",
                ],
                source_tier=4,
            )
        if hit_rate is None or hit_rate < 55:
            return _check(
                "Historical analogs",
                "Mixed",
                historical_references.summary,
                (
                    "Similar local ideas exist, but resolved outcomes are not strong enough to support conviction."
                ),
                [
                    "Use analogs as a checklist only; require current primary evidence, valuation, and monitoring gates.",
                ],
                source_tier=4,
            )
        return _check(
            "Historical analogs",
            "Supports",
            historical_references.summary,
            "Similar past frozen ideas provide context beyond model memory.",
            [],
            source_tier=4,
        )
    if historical_references.references:
        return _check(
            "Historical analogs",
            "Mixed",
            historical_references.summary,
            "Analogs exist, but the sample is sparse.",
            historical_references.data_gaps[:4],
            source_tier=4,
        )
    return _check(
        "Historical analogs",
        "Missing",
        historical_references.summary,
        "There is no local analog base yet.",
        historical_references.data_gaps[:4],
    )


def _external_context_check(external_evidence: ExternalEvidenceBundle) -> ThesisValidationCheck:
    official = [item for item in external_evidence.evidence if item.official]
    unofficial = [item for item in external_evidence.evidence if not item.official]
    if official:
        return _check(
            "Macro / external context",
            "Supports",
            f"{len(official)} official external context item(s) collected.",
            "Macro and external data can explain market-wide drivers without replacing company evidence.",
            [],
            source_tier=min(item.source_tier for item in official),
        )
    if unofficial:
        return _check(
            "Macro / external context",
            "Mixed",
            f"{len(unofficial)} unofficial external context item(s) collected.",
            "Narrative context is useful but cannot independently validate the thesis.",
            external_evidence.data_gaps[:4],
            source_tier=min(item.source_tier for item in unofficial),
        )
    return _check(
        "Macro / external context",
        "Missing",
        f"External evidence status {external_evidence.status}.",
        "Macro/sector/narrative drivers have not been ruled in or out.",
        external_evidence.data_gaps[:4] or ["Enable official macro/default external providers where relevant."],
    )


def _check(
    channel: str,
    status: str,
    evidence: str,
    implication: str,
    gaps: list[str],
    *,
    source_tier: int | None = None,
    citation_count: int = 0,
) -> ThesisValidationCheck:
    scores = {"Supports": 100, "Mixed": 55, "Contradicts": 20, "Missing": 15}
    return ThesisValidationCheck(
        channel=channel,
        status=status,
        score=scores.get(status, 40),
        evidence=evidence,
        implication=implication,
        gaps=_dedupe(gaps),
        source_tier=source_tier,
        citation_count=citation_count,
    )


def _evidence_actions(checks: list[ThesisValidationCheck]) -> list[EvidenceActionItem]:
    actions = []
    for check in checks:
        if check.status == "Supports":
            continue
        spec = _action_spec(check.channel, check.status)
        priority = "High" if check.status == "Contradicts" or spec["blocks"] else "Medium"
        if check.status == "Mixed" and not spec["blocks"]:
            priority = "Medium"
        actions.append(EvidenceActionItem(
            channel=check.channel,
            priority=priority,
            action=spec["action"],
            source=spec["source"],
            why_it_matters=check.gaps[0] if check.gaps else spec["why"],
            blocks_high_conviction=bool(spec["blocks"] or check.status == "Contradicts"),
        ))
    priority_rank = {"High": 2, "Medium": 1, "Low": 0}
    return sorted(
        actions,
        key=lambda item: (
            priority_rank.get(item.priority, 0),
            item.blocks_high_conviction,
            _channel_rank(item.channel),
        ),
        reverse=True,
    )


def _action_spec(channel: str, status: str) -> dict:
    if channel == "SEC / issuer filings":
        return {
            "action": "Find or resolve primary-source filing/issuer evidence for the top claim.",
            "source": "SEC EDGAR filing sections, XBRL facts, issuer release, or filed exhibit",
            "why": "High-conviction ideas need primary evidence, not model narrative.",
            "blocks": True,
        }
    if channel == "Management statements":
        return {
            "action": "Cross-check management language against filings, facts, and prior calls.",
            "source": "Earnings call transcript, 8-K/6-K exhibit, investor presentation, proxy/meeting filing",
            "why": "Management commentary should be corroborated before supporting a thesis.",
            "blocks": status == "Contradicts",
        }
    if channel == "Consensus / expectations":
        return {
            "action": "Capture point-in-time consensus or estimate revisions around the source event.",
            "source": "Alpha Vantage/FMP/Finnhub/CSV consensus snapshots and earnings surprise data",
            "why": "Market-capture claims need evidence that expectations did or did not move.",
            "blocks": True,
        }
    if channel == "Market reaction / attribution":
        return {
            "action": "Load event-window price bars and run market/sector/beta attribution.",
            "source": "Daily adjusted prices, SPY/sector ETF, peer basket, factor/macro context",
            "why": "The thesis should be tied to observed market behavior and residual move.",
            "blocks": True,
        }
    if channel == "Peer / sector read-through":
        return {
            "action": "Run curated peer checks and peer-own-event reactions for the same signal family.",
            "source": "Curated peer universe, peer SEC facts/filings, peer price windows",
            "why": "Peer evidence separates company-specific setups from sector-wide or relative-value ideas.",
            "blocks": False,
        }
    if channel == "Valuation / payoff":
        return {
            "action": "Build or complete bull/base/bear valuation and scenario payoff assumptions.",
            "source": "Internal valuation model, financial facts, consensus estimates, entry price",
            "why": "Ideas need payoff anchors before ranking or sizing.",
            "blocks": True,
        }
    if channel == "Historical analogs":
        return {
            "action": "Record outcomes for similar saved ideas or find more local analogs.",
            "source": "Local idea history, realized outcomes, post-mortems, event-signal table",
            "why": "Historical references become useful only after resolved outcomes accumulate.",
            "blocks": False,
        }
    if channel == "Macro / external context":
        return {
            "action": "Fetch official macro/external context relevant to the event window.",
            "source": "FRED/ALFRED, BLS, BEA, Treasury/Fiscal Data, GDELT only as optional narrative context",
            "why": "Macro context can explain market-wide moves without replacing company evidence.",
            "blocks": False,
        }
    return {
        "action": f"Resolve {channel.lower()} evidence.",
        "source": channel,
        "why": "This evidence channel is incomplete.",
        "blocks": False,
    }


def _channel_rank(channel: str) -> int:
    order = {
        "SEC / issuer filings": 8,
        "Valuation / payoff": 7,
        "Market reaction / attribution": 6,
        "Consensus / expectations": 5,
        "Management statements": 4,
        "Peer / sector read-through": 3,
        "Historical analogs": 2,
        "Macro / external context": 1,
    }
    return order.get(channel, 0)


def _top_idea(ideas: list[TradeIdea]) -> TradeIdea | None:
    if not ideas:
        return None
    rank = {"High-Conviction": 3, "Investable": 3, "Research-Ready": 2, "Candidate": 1}
    return sorted(
        ideas,
        key=lambda item: (
            rank.get(item.stage, 0),
            item.score.total if item.score else 0,
            len(item.citations),
        ),
        reverse=True,
    )[0]


def _directional_return_supports(direction: str, value: float | None) -> bool | None:
    if value is None:
        return None
    direction_lower = (direction or "").lower()
    if "short" in direction_lower:
        return value < 0
    if "long" in direction_lower:
        return value > 0
    return None


def _dedupe(rows: list[str]) -> list[str]:
    seen = set()
    output = []
    for row in rows:
        clean = str(row or "").strip()
        if clean and clean not in seen:
            output.append(clean)
            seen.add(clean)
    return output
