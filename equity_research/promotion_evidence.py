from __future__ import annotations

import hashlib
import re
from datetime import date

from .models import (
    EvidenceLedger,
    ExternalEvidenceBundle,
    NewsClaim,
    PromotionEvidenceBundle,
    PromotionGateDecision,
    PromotionSourceCheck,
    ResearchSourcePlan,
    TradeIdea,
    WisburgResearchLens,
)


EXCLUDED_PROVIDERS = {"gdelt", "llm", "language model"}


def build_promotion_evidence_bundles(
    ideas: list[TradeIdea],
    evidence_ledger: EvidenceLedger,
    external: ExternalEvidenceBundle,
    wisburg: WisburgResearchLens,
    news_claims: list[NewsClaim],
    source_plan: ResearchSourcePlan,
    primary_adapter_attempted: bool = True,
) -> dict[str, PromotionEvidenceBundle]:
    items_by_claim = {
        claim.idea_id: [item for item in evidence_ledger.items if item.claim_id == claim.claim_id]
        for claim in evidence_ledger.claims
    }
    bundles: dict[str, PromotionEvidenceBundle] = {}
    for idea in ideas:
        event = idea.source_events[0] if idea.source_events else None
        driver = str(
            (event.metrics.get("economic_driver") if event else "")
            or (event.metrics.get("driver_family") if event else "")
            or idea.signal_family
        )
        period = _event_period(idea)
        candidates: list[PromotionSourceCheck] = []
        for observation in external.evidence:
            candidates.append(_external_check(observation, idea, driver, period))
        for excerpt in wisburg.excerpts:
            candidates.append(_wisburg_check(excerpt, idea, driver, period))
        for claim in news_claims:
            candidates.append(_news_check(claim, idea, driver, period))
        eligible = _dedupe_independent([item for item in candidates if item.eligible])
        ineligible = [item for item in candidates if not item.eligible]
        ledger_items = items_by_claim.get(idea.idea_id, [])
        tier1_contradiction = any(
            item.source_tier == 1 and item.stance == "Contradicts" and item.materiality >= 3
            for item in ledger_items
        )
        quantitative_bridge = bool(
            idea.driver_analysis
            and idea.driver_analysis.bridge_status not in {"Unknown", "Incomplete causal bridge", "Watch / needs validation"}
            and any(item.confidence in {"High", "Medium"} for item in idea.driver_analysis.factors)
        )
        primary_reason = ""
        if not any(item.source_tier == 1 and item.stance == "Supports" for item in ledger_items):
            primary_reason = _primary_unavailable_reason(source_plan)
        bundle = PromotionEvidenceBundle(
            idea_id=idea.idea_id,
            status="Eligible exception" if len(eligible) >= 2 else "Insufficient secondary evidence",
            primary_adapter_attempted=primary_adapter_attempted,
            primary_unavailable_reason=primary_reason,
            eligible_tier3_sources=eligible,
            ineligible_sources=ineligible[:20],
            independent_origin_count=len({item.origin_group for item in eligible}),
            tier1_contradiction=tier1_contradiction,
            quantitative_bridge_supported=quantitative_bridge,
            substituted_gate="Tier 1 primary support" if len(eligible) >= 2 else None,
        )
        bundle.data_gaps = _bundle_gaps(bundle)
        if bundle.data_gaps:
            bundle.status = "Ineligible"
            bundle.substituted_gate = None
        bundles[idea.idea_id] = bundle
    return bundles


def decide_promotion(bundle: PromotionEvidenceBundle | None) -> PromotionGateDecision:
    if bundle is None:
        return PromotionGateDecision("", "Not evaluated", "Primary evidence required", False)
    checks = []
    failed = []
    _check(bundle.primary_adapter_attempted, "Registered primary adapters were attempted", "Primary adapters were not attempted", checks, failed)
    _check(bool(bundle.primary_unavailable_reason), "Primary fact is unavailable or undisclosed", "No auditable reason for missing primary evidence", checks, failed)
    _check(bundle.independent_origin_count >= 2, "Two independent Tier 3 origins qualify", "Fewer than two independent Tier 3 origins qualify", checks, failed)
    _check(not bundle.tier1_contradiction, "No Tier 1 contradiction", "Tier 1 evidence contradicts the secondary claim", checks, failed)
    _check(bundle.quantitative_bridge_supported, "Quantitative causal bridge supports the claim", "Quantitative causal bridge is incomplete", checks, failed)
    eligible = not failed
    return PromotionGateDecision(
        idea_id=bundle.idea_id,
        status="Eligible" if eligible else "Ineligible",
        label="High-Conviction: secondary-supported" if eligible else "Primary evidence required",
        eligible=eligible,
        substituted_gate="Tier 1 primary support" if eligible else None,
        score_cap=75 if eligible else None,
        checks=checks,
        failed_checks=failed,
        source_ids=[item.source_id for item in bundle.eligible_tier3_sources] if eligible else [],
    )


def _external_check(item, idea: TradeIdea, driver: str, period: str | None) -> PromotionSourceCheck:
    citation = item.citation
    provider = item.provider or "Unknown"
    source_id = _stable_id(provider, item.title, item.source_as_of or item.observed_at)
    claim_match = _claim_matches(driver, f"{item.title} {item.summary} {' '.join(item.tags)}")
    period_match = _period_matches(period, item.source_as_of or item.event_date)
    excluded = _excluded(provider, item.source_type) or item.source_tier != 3
    citation_complete = bool(citation and citation.url and (citation.snippet or item.summary))
    eligible = bool(not excluded and claim_match and period_match and citation_complete and item.lookahead_safe)
    return PromotionSourceCheck(
        source_id, provider, item.source_tier, _origin(provider),
        _fingerprint(item.title, item.summary), item.source_as_of or item.observed_at,
        claim_match, period_match, citation_complete, eligible,
        _reason(excluded, claim_match, period_match, citation_complete, item.lookahead_safe),
    )


def _wisburg_check(item, idea: TradeIdea, driver: str, period: str | None) -> PromotionSourceCheck:
    provider = item.provider or "Wisburg"
    claim_match = _claim_matches(driver, f"{item.title} {item.generated_summary} {' '.join(item.theme_tags)}")
    period_match = _period_matches(period, item.source_as_of)
    citation_complete = bool(item.citation and item.citation.url and item.original_excerpt)
    excluded = item.source_tier != 3
    eligible = bool(not excluded and claim_match and period_match and citation_complete)
    return PromotionSourceCheck(
        item.excerpt_id, provider, item.source_tier, _origin(provider),
        _fingerprint(item.title, item.original_excerpt), item.source_as_of,
        claim_match, period_match, citation_complete, eligible,
        _reason(excluded, claim_match, period_match, citation_complete, True),
    )


def _news_check(item: NewsClaim, idea: TradeIdea, driver: str, period: str | None) -> PromotionSourceCheck:
    provider = item.citation.source if item.citation else item.source_family
    claim_match = _claim_matches(driver, f"{item.affected_driver} {item.claimed_fact}")
    period_match = _period_matches(period, item.event_date)
    citation_complete = bool(item.citation and item.citation.url and item.claimed_fact)
    excluded = item.source_tier != 3 or _excluded(provider, item.source_family)
    eligible = bool(not excluded and claim_match and period_match and citation_complete)
    return PromotionSourceCheck(
        item.claim_id, provider, item.source_tier, _origin(provider),
        _fingerprint(item.claimed_fact, item.citation.url if item.citation else ""), item.event_date,
        claim_match, period_match, citation_complete, eligible,
        _reason(excluded, claim_match, period_match, citation_complete, True),
    )


def _primary_unavailable_reason(plan: ResearchSourcePlan) -> str:
    failed = [
        outcome for outcome in plan.outcomes
        if outcome.status.lower() in {"unavailable", "failed", "not_disclosed", "inaccessible", "parse_failed"}
    ]
    if failed:
        return failed[0].message or failed[0].status
    if plan.requests:
        return "Registered SEC/issuer adapters were run, but the current claim lacks a Tier 1 disclosure."
    return ""


def _bundle_gaps(bundle: PromotionEvidenceBundle) -> list[str]:
    gaps = []
    if not bundle.primary_adapter_attempted:
        gaps.append("Registered primary adapters were not attempted.")
    if not bundle.primary_unavailable_reason:
        gaps.append("No auditable reason explains the missing Tier 1 fact.")
    if bundle.independent_origin_count < 2:
        gaps.append("Two independent Tier 3 source origins are required.")
    if bundle.tier1_contradiction:
        gaps.append("Tier 1 evidence contradicts the reported claim.")
    if not bundle.quantitative_bridge_supported:
        gaps.append("The quantitative causal bridge does not independently support the claim.")
    return gaps


def _event_period(idea: TradeIdea) -> str | None:
    if not idea.source_events:
        return None
    event = idea.source_events[0]
    for citation in event.citations:
        if citation.period_end:
            return citation.period_end
    return str(event.metrics.get("period_end") or event.event_date or "") or None


def _claim_matches(driver: str, text: str) -> bool:
    driver_tokens = set(re.findall(r"[a-z0-9]+", driver.lower())) - {"and", "the", "change", "generation"}
    text_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    return bool(driver_tokens and driver_tokens & text_tokens)


def _period_matches(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    try:
        a, b = date.fromisoformat(left[:10]), date.fromisoformat(right[:10])
        return abs((a - b).days) <= 120
    except ValueError:
        return left[:7] == right[:7] or left[:4] == right[:4]


def _dedupe_independent(items: list[PromotionSourceCheck]) -> list[PromotionSourceCheck]:
    rows = []
    seen_origins = set()
    seen_fingerprints = set()
    for item in items:
        if item.origin_group in seen_origins or item.syndication_fingerprint in seen_fingerprints:
            continue
        seen_origins.add(item.origin_group)
        seen_fingerprints.add(item.syndication_fingerprint)
        rows.append(item)
    return rows


def _excluded(provider: str, source_type: str) -> bool:
    text = f"{provider} {source_type}".lower()
    return any(token in text for token in EXCLUDED_PROVIDERS) or "tier 4" in text


def _origin(provider: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", provider.lower()).strip("_") or "unknown"


def _fingerprint(title: str, text: str) -> str:
    normalized = re.sub(r"\W+", " ", f"{title} {text}".lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _reason(excluded: bool, claim: bool, period: bool, citation: bool, lookahead: bool) -> str:
    if excluded:
        return "Source tier/provider is not eligible for promotion."
    if not claim:
        return "Source does not address the same driver claim."
    if not period:
        return "Source does not match the event/reporting period."
    if not citation:
        return "Source metadata or retained citation excerpt is incomplete."
    if not lookahead:
        return "Source fails the no-lookahead check."
    return "Eligible Tier 3 corroboration candidate."


def _check(condition: bool, passed: str, failed: str, pass_rows: list[str], fail_rows: list[str]) -> None:
    (pass_rows if condition else fail_rows).append(passed if condition else failed)
