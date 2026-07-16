from __future__ import annotations

from dataclasses import asdict
from datetime import date
import hashlib
import re

from .models import (
    ChangeEvent,
    CompanyEconomics,
    ContextualDisclosureComparison,
    ManagementClaim,
    ManagementSourcePackage,
)


TOPIC_TERMS: dict[str, tuple[str, ...]] = {
    "guidance": ("guidance", "outlook", "expect", "target", "forecast", "headwind", "tailwind"),
    "debt_liquidity": ("debt", "liquidity", "maturity", "refinanc", "covenant", "credit facility", "cash"),
    "margin": ("margin", "pricing", "cost", "mix", "gross profit", "operating leverage"),
    "litigation": ("litigation", "lawsuit", "investigation", "regulatory", "settlement", "antitrust"),
    "dilution": ("dilution", "share count", "buyback", "repurchase", "stock-based compensation", "convertible"),
    "customer_concentration": ("customer", "concentration", "major customer", "accounts for", "churn", "renewal"),
    "risk_factors": ("risk", "uncertain", "adverse", "exposure", "material"),
}


def attach_disclosure_intelligence(
    ticker: str,
    events: list[ChangeEvent],
    management: ManagementSourcePackage,
    economics: CompanyEconomics,
) -> list[ContextualDisclosureComparison]:
    comparisons: list[ContextualDisclosureComparison] = []
    for event in events:
        if event.metrics.get("signal_method") != "disclosure_change_engine":
            continue
        strict = event.metrics.get("disclosure_comparison")
        strict = strict if isinstance(strict, dict) else {}
        if str(strict.get("comparison_status") or "") in {"period_aligned", "comparable_imperfect"}:
            comparison = _strict_comparison(ticker, event, strict)
        else:
            comparison = _cross_source_comparison(ticker, event, strict, management.claims)
        event.metrics["contextual_disclosure_comparison"] = asdict(comparison)
        event.metrics["disclosure_intelligence"] = _research_connections(event, economics, comparison)
        comparisons.append(comparison)
    return comparisons


def _strict_comparison(ticker: str, event: ChangeEvent, strict: dict) -> ContextualDisclosureComparison:
    prior_citation = next(
        (citation for citation in event.citations if citation.accession and citation.accession == strict.get("prior_accession")),
        None,
    )
    changed = [str(item) for item in strict.get("changed_phrases", []) if item]
    intent_shift, intent_direction = _intent_shift(
        str(strict.get("prior_excerpt") or ""),
        str(strict.get("current_excerpt") or _current_excerpt(event)),
        event.category,
    )
    semantic_shift = f"{intent_shift} Exact delta: {changed[0]}" if changed else intent_shift
    return ContextualDisclosureComparison(
        comparison_id=_stable_id(ticker, event.title, strict.get("prior_accession"), "strict"),
        ticker=ticker.upper(), event_title=event.title, topic=event.category,
        status="Available", comparison_type=str(strict.get("alignment_type") or "same_topic"),
        current_source=str(strict.get("current_form") or event.source),
        current_period=str(strict.get("current_period") or event.event_date or "") or None,
        current_excerpt=str(strict.get("current_excerpt") or _current_excerpt(event)),
        current_citation=event.citations[0] if event.citations else None,
        prior_source=str(strict.get("prior_form") or ""),
        prior_period=str(strict.get("prior_period") or "") or None,
        prior_excerpt=str(strict.get("prior_excerpt") or ""), prior_citation=prior_citation,
        semantic_shift=semantic_shift,
        affected_driver=str(strict.get("affected_driver") or event.metrics.get("economic_driver") or "Unmapped"),
        direction=(
            intent_direction if intent_direction in {"positive", "negative"}
            else str(strict.get("semantic_direction") or event.direction or "neutral")
        ),
        confidence=str(strict.get("confidence") or "Medium"),
        required_confirmation=[str(item) for item in strict.get("required_confirmation", [])],
        citations_used=_citation_ids(event, prior_citation),
        provider="deterministic_disclosure_engine",
        llm_status="ready" if strict.get("prior_context_audit", {}).get("llm_comparison_ready") else "not_ready",
    )


def _cross_source_comparison(
    ticker: str,
    event: ChangeEvent,
    strict: dict,
    claims: list[ManagementClaim],
) -> ContextualDisclosureComparison:
    current_excerpt = str(strict.get("current_excerpt") or _current_excerpt(event))
    prior_claim = _best_prior_claim(event, current_excerpt, claims)
    requirements = [str(item) for item in strict.get("required_confirmation", [])]
    if prior_claim is None:
        return ContextualDisclosureComparison(
            comparison_id=_stable_id(ticker, event.title, event.event_date, "planned"),
            ticker=ticker.upper(), event_title=event.title, topic=event.category,
            status="Source recovery required", comparison_type="cross_source_context",
            current_source=event.source, current_period=event.event_date,
            current_excerpt=current_excerpt,
            current_citation=event.citations[0] if event.citations else None,
            affected_driver=str(strict.get("affected_driver") or event.metrics.get("economic_driver") or "Unmapped"),
            direction="neutral", confidence="Low", required_confirmation=requirements,
            citations_used=_citation_ids(event, None), provider="deterministic_context_matcher",
            llm_status="not_ready",
            data_gaps=[
                "No earlier cited same-topic management excerpt was found in retrieved transcripts, 6-Ks, issuer IR documents, or meeting materials.",
                "Retrieve the prior source before asking an LLM to infer a management-intent shift.",
            ],
        )
    semantic_shift, direction = _intent_shift(prior_claim.statement, current_excerpt, event.category)
    return ContextualDisclosureComparison(
        comparison_id=_stable_id(ticker, event.title, prior_claim.claim_id, "context"),
        ticker=ticker.upper(), event_title=event.title, topic=event.category,
        status="Provisional", comparison_type="cross_source_context",
        current_source=event.source, current_period=event.event_date,
        current_excerpt=current_excerpt,
        current_citation=event.citations[0] if event.citations else None,
        prior_source=prior_claim.source_type, prior_period=prior_claim.event_date,
        prior_excerpt=prior_claim.statement, prior_citation=prior_claim.citation,
        semantic_shift=semantic_shift,
        affected_driver=str(strict.get("affected_driver") or event.metrics.get("economic_driver") or "Unmapped"),
        direction=direction, confidence="Medium", required_confirmation=requirements,
        citations_used=_citation_ids(event, prior_claim.citation),
        provider="deterministic_context_matcher", llm_status="ready",
        data_gaps=["Cross-source context is not a same-section filing diff; validate the affected KPI and economic mechanism before promotion."],
    )


def _best_prior_claim(event: ChangeEvent, current_excerpt: str, claims: list[ManagementClaim]) -> ManagementClaim | None:
    event_date = _date(event.event_date)
    terms = TOPIC_TERMS.get(event.category, ())
    current_lower = current_excerpt.lower()
    ranked: list[tuple[float, str, ManagementClaim]] = []
    for claim in claims:
        claim_date = _date(claim.event_date)
        if not claim.statement or not claim.citation or not claim_date:
            continue
        if event_date and claim_date >= event_date:
            continue
        claim_lower = claim.statement.lower()
        topic_hits = sum(1 for term in terms if term in claim_lower)
        if topic_hits == 0:
            continue
        ranked.append((topic_hits * 3 + _token_overlap(current_lower, claim_lower), claim.event_date or "", claim))
    return max(ranked, key=lambda item: (item[0], item[1]))[2] if ranked else None


def _intent_shift(prior: str, current: str, topic: str) -> tuple[str, str]:
    prior_lower = prior.lower()
    current_lower = current.lower()
    numeric_pattern = r"(?:\d+(?:\.\d+)?%?|[$¥€£]\s*\d+)"
    if re.search(numeric_pattern, current) and not re.search(numeric_pattern, prior):
        return "Management language moved from qualitative discussion to a quantified commitment or exposure.", "mixed"
    if any(term in prior_lower for term in ("cost control", "efficiency", "discipline")) and any(term in current_lower for term in ("reinvest", "investment", "accelerate spending")):
        return "Management emphasis moved from cost control toward reinvestment.", "mixed"
    if "temporary" in prior_lower and any(term in current_lower for term in ("persistent", "continued", "ongoing")):
        return "A previously temporary issue is now described as persistent or ongoing.", "negative"
    if any(term in prior_lower for term in ("cautious", "uncertain", "preserve liquidity", "measured")) and any(
        term in current_lower for term in ("accelerate investment", "increase repurchases", "return capital", "aggressive investment")
    ):
        return "Management moved from a cautious posture to more aggressive investment or capital return.", "mixed"
    if any(term in current_lower for term in ("buyback", "repurchase", "dividend", "capital return")) and not any(term in prior_lower for term in ("buyback", "repurchase", "dividend", "capital return")):
        return "Capital-return language became a new management emphasis.", "positive"
    topic_terms = TOPIC_TERMS.get(topic, ())
    prior_hits = sum(1 for term in topic_terms if term in prior_lower)
    current_hits = sum(1 for term in topic_terms if term in current_lower)
    if prior_hits == 0 and current_hits >= 2:
        return "A previously absent topic became a repeated new disclosure emphasis.", "negative" if topic in {"debt_liquidity", "litigation", "risk_factors"} else "mixed"
    added = sorted(_material_terms(current_lower) - _material_terms(prior_lower))[:8]
    removed = sorted(_material_terms(prior_lower) - _material_terms(current_lower))[:8]
    summary = f"New emphasis: {', '.join(added) or 'none isolated'}; reduced emphasis: {', '.join(removed) or 'none isolated'}."
    direction = "negative" if topic in {"debt_liquidity", "litigation", "risk_factors"} and added else "mixed"
    return summary, direction


def _research_connections(event: ChangeEvent, economics: CompanyEconomics, comparison: ContextualDisclosureComparison) -> dict[str, object]:
    driver_names = [driver.name for driver in economics.drivers if driver.materiality in {"High", "Medium"}]
    playbook = economics.industry_playbook
    topic = event.category
    capital_checks = [
        "Buybacks, dividends, SBC, issuance, and diluted share count",
        "ADS/ordinary-share ratio, weighted-average basis, and corporate actions",
        "Net cash, debt issuance/repayment, and reinvestment commitments",
    ] if topic in {"dilution", "debt_liquidity", "guidance"} else []
    credit_checks = [
        "Cash and restricted cash",
        "Debt maturity schedule and refinancing sources",
        "Interest expense, operating cash flow, capex, and coverage",
        "Rating action, covenant, or credit-spread evidence when available",
    ] if topic in {"debt_liquidity", "guidance", "risk_factors", "litigation"} else []
    return {
        "comparison_type": comparison.comparison_type,
        "affected_driver": comparison.affected_driver,
        "segment_driver_candidates": driver_names[:8],
        "industry_kpis": list(playbook.key_kpis[:8]),
        "capital_allocation_checks": capital_checks,
        "credit_liquidity_checks": credit_checks,
        "peer_operating_checks": list(playbook.key_kpis[:5]),
        "confirm": comparison.required_confirmation,
        "disprove": [
            "The prior and current excerpts are not period-comparable or refer to different scopes.",
            "Aligned KPIs, peer evidence, or later management outcomes contradict the inferred shift.",
        ],
        "research_ready_rule": "Strict same-section evidence may support validation after driver/KPI corroboration. Cross-source context remains supporting evidence until primary operating or financial evidence confirms it.",
    }


def _current_excerpt(event: ChangeEvent) -> str:
    return next((citation.snippet for citation in event.citations if citation.snippet), event.summary)


def _citation_ids(event: ChangeEvent, prior_citation) -> list[str]:
    citations = list(event.citations[:2]) + ([prior_citation] if prior_citation else [])
    return [hashlib.sha1(f"{citation.url}:{citation.accession}:{citation.section}:{citation.snippet}".encode("utf-8")).hexdigest()[:12] for citation in citations if citation]


def _token_overlap(left: str, right: str) -> float:
    a = _material_terms(left)
    b = _material_terms(right)
    return len(a & b) / max(len(a | b), 1)


def _material_terms(text: str) -> set[str]:
    stop = {"company", "business", "during", "which", "their", "there", "about", "would", "could", "from", "with", "this", "that"}
    return {token for token in re.findall(r"\b[a-z][a-z0-9-]{3,}\b", text.lower()) if token not in stop}


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _stable_id(*parts: object) -> str:
    return hashlib.sha1(":".join(str(part or "") for part in parts).encode("utf-8")).hexdigest()[:12]
