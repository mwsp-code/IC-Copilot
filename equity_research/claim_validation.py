from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from .analysis import extract_numeric_guidance, normalize_text
from .models import (
    ChangeEvent,
    ClaimValidationResult,
    CompanyIdentity,
    LlmExtractionManifest,
    ValidatedClaim,
)


PROMPT_VERSION = "claim-validator-v1"
THESIS_GRADE = "Thesis-grade"
WATCH_ITEM = "Watch Item"
NOT_THESIS_GRADE = "Not thesis-grade"
TEXT_DIAGNOSTIC_METHODS = {"keyword_count", "disclosure_change_engine"}
COMPARABLE_DISCLOSURE_STATUSES = {"period_aligned", "comparable_imperfect"}


ACCOUNTING_ONLY_TERMS = (
    "deferred revenue",
    "remaining performance obligation",
    "performance obligations",
    "revenue to be realized",
    "recognized as revenue",
    "contract liabilities",
    "unearned revenue",
    "note 3",
    "earnings per share",
)

SAFE_HARBOR_TERMS = (
    "forward-looking statements",
    "safe harbor",
    "undue reliance",
    "words or phrases such as",
    "other similar expressions",
)

NEGATIVE_TERMS = (
    "decline", "decrease", "lower", "headwind", "pressure", "adverse",
    "weak", "soft", "slowdown", "uncertain", "risk", "reduced",
)

POSITIVE_TERMS = (
    "improve", "increase", "raise", "growth", "tailwind", "expand",
    "strong", "accelerate", "higher", "benefit",
)


def validate_events(
    identity: CompanyIdentity,
    events: list[ChangeEvent],
    *,
    llm_provider: Any | None = None,
    use_llm: bool = False,
) -> tuple[ClaimValidationResult, LlmExtractionManifest]:
    deterministic = [_deterministic_claim(identity, event) for event in events if event.severity >= 2]
    manifest = _empty_manifest()
    claims = deterministic
    if use_llm and llm_provider is not None and deterministic:
        llm_claims, manifest = _llm_claims(identity, events, deterministic, llm_provider)
        claims = _merge_claims(deterministic, llm_claims)
    claims_by_event = {
        (claim.event_title, claim.event_category): claim
        for claim in claims
    }
    for event in events:
        claim = claims_by_event.get((event.title, event.category))
        if not claim:
            continue
        _attach_claim_to_event(event, claim)
    gaps = []
    if not any(claim.status == THESIS_GRADE for claim in claims):
        gaps.append("No thesis-grade validated claim was produced from the current source excerpts.")
    status = "Available" if claims else "Unavailable"
    if claims and not any(claim.status == THESIS_GRADE for claim in claims):
        status = "Watch-only"
    return (
        ClaimValidationResult(
            ticker=identity.ticker,
            status=status,
            claims=claims,
            data_gaps=gaps,
            llm_used=use_llm and manifest.status not in {"Disabled", "Skipped"},
            provider=manifest.provider if use_llm else "deterministic",
        ),
        manifest,
    )


def _deterministic_claim(identity: CompanyIdentity, event: ChangeEvent) -> ValidatedClaim:
    citation = event.citations[0] if event.citations else None
    quote = citation.snippet if citation and citation.snippet else event.summary
    normalized_quote = normalize_text(quote)
    disclosure = event.metrics.get("disclosure_comparison")
    disclosure = disclosure if isinstance(disclosure, dict) else {}
    contextual = event.metrics.get("contextual_disclosure_comparison")
    contextual = contextual if isinstance(contextual, dict) else {}
    driver = str(
        event.metrics.get("economic_driver")
        or disclosure.get("affected_driver")
        or contextual.get("affected_driver")
        or "Unmapped"
    )
    metric = _metric_for_event(event, normalized_quote)
    direction = _direction_for_event(event, normalized_quote)
    status, confidence, reason, not_grade = _grade_event(event, normalized_quote, driver, direction, metric)
    current_excerpt = normalize_text(str(disclosure.get("current_excerpt") or contextual.get("current_excerpt") or normalized_quote))
    prior_excerpt = normalize_text(str(disclosure.get("prior_excerpt") or contextual.get("prior_excerpt") or ""))
    changed_phrases = disclosure.get("changed_phrases") if isinstance(disclosure.get("changed_phrases"), list) else []
    changed_text = normalize_text(str(changed_phrases[0])) if changed_phrases else current_excerpt
    comparison_type = str(disclosure.get("alignment_type") or contextual.get("comparison_type") or "")
    required_confirmation = disclosure.get("required_confirmation") or contextual.get("required_confirmation") or []
    citation_ids = contextual.get("citations_used") if isinstance(contextual.get("citations_used"), list) else [
        _citation_id(item) for item in event.citations
    ]
    claim = ValidatedClaim(
        claim_id=_claim_id(identity.ticker, event, normalized_quote),
        ticker=identity.ticker,
        event_title=event.title,
        event_category=event.category,
        status=status,
        is_substantive=status == THESIS_GRADE,
        claim_type=event.category,
        direction=direction,
        metric=metric,
        period=str(event.metrics.get("guidance_period") or disclosure.get("current_period") or event.metrics.get("period_end") or "") or None,
        business_driver=driver,
        changed_text=changed_text,
        prior_text=prior_excerpt or str(event.metrics.get("prior_text") or event.metrics.get("previous_excerpt") or ""),
        supporting_quote=current_excerpt,
        counter_quote="",
        confidence=confidence,
        reason=reason,
        not_thesis_grade_reason=not_grade,
        citation=citation,
        source="deterministic",
        source_tier=citation.source_tier if citation else None,
        created_at=_utc_now(),
        comparison_type=comparison_type,
        semantic_shift=str(contextual.get("semantic_shift") or (changed_phrases[0] if changed_phrases else "")),
        required_confirmation=[str(item) for item in required_confirmation],
        citation_ids_used=[str(item) for item in citation_ids],
    )
    return claim


def _grade_event(
    event: ChangeEvent,
    quote: str,
    driver: str,
    direction: str,
    metric: str | None,
) -> tuple[str, str, str, str]:
    lower = quote.lower()
    signal_method = str(event.metrics.get("signal_method") or "")
    comparison_status = str(event.metrics.get("comparison_status") or "")
    if any(term in lower for term in SAFE_HARBOR_TERMS):
        return NOT_THESIS_GRADE, "High", "Safe-harbor or forward-looking boilerplate was detected.", "Safe-harbor boilerplate is not thesis-grade evidence."
    if signal_method in TEXT_DIAGNOSTIC_METHODS and comparison_status == "invalid_same_source":
        return (
            NOT_THESIS_GRADE,
            "High",
            "The apparent language change compares the filing to the same source/content.",
            "Disclosure movement is invalid when current and prior sources are the same.",
        )
    if event.category == "guidance":
        if any(term in lower for term in ACCOUNTING_ONLY_TERMS):
            return NOT_THESIS_GRADE, "High", "Accounting/deferred-revenue schedule detected.", "Accounting-only deferred-revenue language is not management guidance."
        numeric = extract_numeric_guidance(quote)
        if numeric and driver != "Unmapped" and direction in {"positive", "negative"}:
            return THESIS_GRADE, "High", "Citation-backed numeric guidance maps to a business driver.", ""
        if direction in {"positive", "negative"} and metric and driver != "Unmapped":
            return WATCH_ITEM, "Medium", "Directional guidance-like language needs a prior quote or numeric bridge.", "Non-numeric guidance language needs before/after text before it can be thesis-grade."
        return WATCH_ITEM, "Low", "Guidance-like language is vague or lacks a mapped driver.", "Keyword-count guidance movement is watch-only without exact directional, driver-linked evidence."
    if event.category in {"financial_kpi", "margin"}:
        if event.metrics.get("normalization_required"):
            return (
                NOT_THESIS_GRADE,
                "High",
                "Structured numeric fact requires share/security-basis normalization before interpretation.",
                str(event.metrics.get("normalization_reason") or "Share-count basis is not comparable enough for thesis-grade evidence."),
            )
        if event.metrics.get("current_value") is not None or event.metrics.get("gross_margin") is not None:
            return THESIS_GRADE, "High", "Structured numeric financial fact maps to operating performance.", ""
        return WATCH_ITEM, "Medium", "Financial event lacks a complete numeric bridge.", "Financial event needs current/previous values before thesis-grade promotion."
    if event.category in {"risk_factors", "litigation", "debt_liquidity", "dilution", "customer_concentration"}:
        if signal_method in TEXT_DIAGNOSTIC_METHODS:
            event_type = str(event.metrics.get("disclosure_event_type") or "").lower()
            if event_type == "observation" or comparison_status not in COMPARABLE_DISCLOSURE_STATUSES:
                return (
                    WATCH_ITEM,
                    "Medium",
                    "Disclosure language was detected, but no valid section-aligned prior change is proven.",
                    "Disclosure observations and mention diagnostics are not thesis-grade evidence without a comparable prior section and a business-driver bridge.",
                )
            disclosure = event.metrics.get("disclosure_comparison")
            disclosure = disclosure if isinstance(disclosure, dict) else {}
            exact_delta = bool(disclosure.get("changed_phrases") and disclosure.get("current_excerpt") and disclosure.get("prior_excerpt"))
            materiality = float(disclosure.get("materiality_score") or 0)
            if (
                exact_delta
                and materiality >= 45
                and driver != "Unmapped"
                and direction in {"positive", "negative"}
                and len(event.citations) >= 2
            ):
                return (
                    THESIS_GRADE,
                    "Medium",
                    "Exact current/prior primary-source disclosure changed directionally and maps to a material business driver.",
                    "",
                )
            return (
                WATCH_ITEM,
                "Medium",
                "Comparable disclosure changed, but its exact economic impact still needs a KPI, valuation, credit, or operating bridge.",
                "Exact changed language is available; confirm the affected driver and required evidence before thesis-grade promotion.",
            )
        if direction == "negative" and driver != "Unmapped" and event.citations:
            return THESIS_GRADE, "Medium", "Primary-source risk disclosure maps to a material negative driver.", ""
        return WATCH_ITEM, "Low", "Disclosure is relevant but not yet mapped or directional enough.", "Risk language must alter probability, severity, timing, or valuation impact."
    if event.category in {
        "guidance_shift", "management_credibility", "qa_evasion",
        "strategic_priority_change", "capital_allocation_change",
        "governance_change", "incentive_alignment", "shareholder_vote_signal",
        "tone_shift", "guidance_specificity_change",
    }:
        if event.metrics.get("cross_check_status") == "Confirmed" and event.metrics.get("machine_readable", True):
            return THESIS_GRADE, "Medium", "Management claim is cross-checked and machine-readable.", ""
        return WATCH_ITEM, "Medium", "Management signal requires source cross-check before promotion.", "Management language alone is supporting evidence, not high-conviction proof."
    if event.citations and driver != "Unmapped" and direction in {"positive", "negative"}:
        return WATCH_ITEM, "Medium", "Source-linked event maps to a driver but lacks a quantified thesis bridge.", "Add KPI, valuation, or expectations evidence before thesis-grade promotion."
    return WATCH_ITEM, "Low", "Event is source-linked but not thesis-grade.", "The evidence is not precise enough to support a trade thesis."


def _metric_for_event(event: ChangeEvent, quote: str) -> str | None:
    for key in ("guidance_metric", "metric_name"):
        value = event.metrics.get(key)
        if value:
            return str(value)
    match = re.search(
        r"\b(revenue|sales|gross margin|operating margin|eps|earnings per share|free cash flow|debt|buyback|share repurchase)\b",
        quote,
        re.IGNORECASE,
    )
    return match.group(1).title() if match else None


def _direction_for_event(event: ChangeEvent, quote: str) -> str:
    lower = quote.lower()
    positive = sum(1 for term in POSITIVE_TERMS if term in lower)
    negative = sum(1 for term in NEGATIVE_TERMS if term in lower)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return event.direction if event.direction in {"positive", "negative"} else "neutral"


def _attach_claim_to_event(event: ChangeEvent, claim: ValidatedClaim) -> None:
    event.metrics["validated_claim_id"] = claim.claim_id
    event.metrics["thesis_grade_status"] = claim.status
    event.metrics["claim_validation_confidence"] = claim.confidence
    event.metrics["validated_direction"] = claim.direction
    event.metrics["direction_rationale"] = claim.reason
    event.metrics["changed_text"] = claim.changed_text
    event.metrics["supporting_quote"] = claim.supporting_quote
    event.metrics["counter_quote"] = claim.counter_quote
    event.metrics["not_thesis_grade_reason"] = claim.not_thesis_grade_reason
    event.metrics["business_driver"] = claim.business_driver
    event.metrics["validated_metric"] = claim.metric
    event.metrics["comparison_type"] = claim.comparison_type
    event.metrics["semantic_shift"] = claim.semantic_shift
    event.metrics["required_confirmation"] = claim.required_confirmation
    event.metrics["validated_citation_ids"] = claim.citation_ids_used
    if claim.status == NOT_THESIS_GRADE:
        event.direction = "neutral"
    elif claim.status == WATCH_ITEM and event.category == "guidance":
        event.direction = "neutral"


def _llm_claims(
    identity: CompanyIdentity,
    events: list[ChangeEvent],
    deterministic: list[ValidatedClaim],
    provider: Any,
) -> tuple[list[ValidatedClaim], LlmExtractionManifest]:
    prompt = {
        "prompt_version": PROMPT_VERSION,
        "task": "Validate whether source excerpts support thesis-grade investment claims. Return JSON only.",
        "rules": [
            "Use only supplied excerpts.",
            "Do not invent citations, price targets, probabilities, or recommendations.",
            "If an excerpt is accounting-only, boilerplate, or vague, mark it Not thesis-grade or Watch Item.",
            "A Short direction requires explicit negative language or a quantified bearish bridge.",
        ],
        "company": {"ticker": identity.ticker, "name": identity.name},
        "events": [
            {
                "event_title": event.title,
                "event_category": event.category,
                "direction": event.direction,
                "summary": event.summary,
                "economic_driver": event.metrics.get("economic_driver"),
                "driver_materiality": event.metrics.get("driver_materiality"),
                "disclosure_comparison": _llm_disclosure_payload(event),
                "citations": [
                    {
                        "citation_id": _citation_id(citation),
                        "source": citation.source,
                        "url": citation.url,
                        "snippet": citation.snippet,
                    }
                    for citation in event.citations[:3]
                ],
            }
            for event in events[:16]
        ],
        "output_schema": {
            "claims": [{
                "event_title": "string",
                "status": "Thesis-grade | Watch Item | Not thesis-grade",
                "is_substantive": "boolean",
                "claim_type": "string",
                "direction": "positive | negative | neutral | mixed",
                "metric": "string or null",
                "period": "string or null",
                "business_driver": "string",
                "changed_text": "string",
                "prior_text": "string",
                "supporting_quote": "string",
                "counter_quote": "string",
                "confidence": "High | Medium | Low",
                "reason": "string",
                "not_thesis_grade_reason": "string",
                "comparison_type": "same_section | same_topic | cross_source_context | empty",
                "semantic_shift": "string",
                "affected_driver": "string",
                "required_confirmation": ["string"],
                "citation_ids_used": ["citation id from supplied evidence"],
            }]
        },
    }
    generated_at = _utc_now()
    try:
        payload = provider.complete_json(prompt)
    except Exception as exc:
        return deterministic, LlmExtractionManifest(
            provider=getattr(provider, "provider_name", "llm"),
            model=getattr(provider, "model", ""),
            prompt_version=PROMPT_VERSION,
            generated_at=generated_at,
            status="Failed",
            validated_claim_ids=[claim.claim_id for claim in deterministic],
            redacted_config={"enabled": "true"},
            messages=[f"LLM claim validation failed: {exc}"],
        )
    parsed = []
    by_title = {claim.event_title: claim for claim in deterministic}
    for raw in payload.get("claims", []) if isinstance(payload, dict) else []:
        title = str(raw.get("event_title") or "")
        base = by_title.get(title)
        if not base:
            continue
        supporting = str(raw.get("supporting_quote") or "")
        if supporting and supporting not in base.supporting_quote and base.supporting_quote not in supporting:
            # LLM evidence must be traceable to the supplied excerpt.
            continue
        prior_text = str(raw.get("prior_text") or "")
        if prior_text and base.prior_text and prior_text not in base.prior_text and base.prior_text not in prior_text:
            continue
        requested_citation_ids = [str(item) for item in raw.get("citation_ids_used", [])]
        if requested_citation_ids and not set(requested_citation_ids).issubset(set(base.citation_ids_used)):
            continue
        status = _clean_status(raw.get("status"), base.status)
        comparison_type = str(raw.get("comparison_type") or base.comparison_type)
        if comparison_type == "cross_source_context" and status == THESIS_GRADE:
            status = WATCH_ITEM
        parsed.append(ValidatedClaim(
            claim_id=base.claim_id,
            ticker=base.ticker,
            event_title=base.event_title,
            event_category=base.event_category,
            status=status,
            is_substantive=status == THESIS_GRADE and bool(raw.get("is_substantive", True)),
            claim_type=str(raw.get("claim_type") or base.claim_type),
            direction=_clean_direction(raw.get("direction"), base.direction),
            metric=str(raw.get("metric") or base.metric or "") or None,
            period=str(raw.get("period") or base.period or "") or None,
            business_driver=str(raw.get("business_driver") or base.business_driver),
            changed_text=str(raw.get("changed_text") or base.changed_text),
            prior_text=prior_text or base.prior_text,
            supporting_quote=supporting or base.supporting_quote,
            counter_quote=str(raw.get("counter_quote") or ""),
            confidence=str(raw.get("confidence") or base.confidence),
            reason=str(raw.get("reason") or base.reason),
            not_thesis_grade_reason=str(raw.get("not_thesis_grade_reason") or base.not_thesis_grade_reason),
            citation=base.citation,
            source=f"llm:{getattr(provider, 'provider_name', 'llm')}",
            source_tier=base.source_tier,
            created_at=generated_at,
            comparison_type=comparison_type,
            semantic_shift=str(raw.get("semantic_shift") or base.semantic_shift),
            required_confirmation=[str(item) for item in raw.get("required_confirmation", base.required_confirmation)],
            citation_ids_used=requested_citation_ids or base.citation_ids_used,
        ))
    manifest = LlmExtractionManifest(
        provider=getattr(provider, "provider_name", "llm"),
        model=getattr(provider, "model", ""),
        prompt_version=PROMPT_VERSION,
        generated_at=generated_at,
        status="Available" if parsed else "No usable LLM claims",
        validated_claim_ids=[claim.claim_id for claim in parsed or deterministic],
        redacted_config={"enabled": "true"},
        messages=[] if parsed else ["LLM returned no traceable claim validations; deterministic validation was used."],
    )
    return parsed or deterministic, manifest


def _merge_claims(
    deterministic: list[ValidatedClaim],
    llm_claims: list[ValidatedClaim],
) -> list[ValidatedClaim]:
    by_id = {claim.claim_id: claim for claim in deterministic}
    for claim in llm_claims:
        base = by_id.get(claim.claim_id)
        if not base:
            continue
        if base.status == NOT_THESIS_GRADE and claim.status == THESIS_GRADE:
            # Do not let the LLM upgrade deterministic hard rejections.
            continue
        by_id[claim.claim_id] = claim
    return list(by_id.values())


def _empty_manifest() -> LlmExtractionManifest:
    return LlmExtractionManifest(
        provider="deterministic",
        model="none",
        prompt_version=PROMPT_VERSION,
        generated_at=_utc_now(),
        status="Disabled",
        redacted_config={"enabled": "false"},
    )


def _claim_id(ticker: str, event: ChangeEvent, quote: str) -> str:
    raw = f"{ticker}:{event.title}:{event.event_date}:{quote[:200]}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _citation_id(citation) -> str:
    raw = f"{citation.url}:{citation.accession}:{citation.section}:{citation.snippet}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _llm_disclosure_payload(event: ChangeEvent) -> dict[str, object]:
    strict = event.metrics.get("disclosure_comparison")
    strict = strict if isinstance(strict, dict) else {}
    contextual = event.metrics.get("contextual_disclosure_comparison")
    contextual = contextual if isinstance(contextual, dict) else {}
    current_citation = contextual.get("current_citation") if isinstance(contextual.get("current_citation"), dict) else {}
    prior_citation = contextual.get("prior_citation") if isinstance(contextual.get("prior_citation"), dict) else {}
    return {
        "comparison_type": strict.get("alignment_type") or contextual.get("comparison_type"),
        "current_source": strict.get("current_form") or contextual.get("current_source"),
        "prior_source": strict.get("prior_form") or contextual.get("prior_source"),
        "current_period": strict.get("current_period") or contextual.get("current_period"),
        "prior_period": strict.get("prior_period") or contextual.get("prior_period"),
        "current_excerpt": strict.get("current_excerpt") or contextual.get("current_excerpt"),
        "prior_excerpt": strict.get("prior_excerpt") or contextual.get("prior_excerpt"),
        "added_sentences": strict.get("added_sentences", []),
        "removed_sentences": strict.get("removed_sentences", []),
        "changed_phrases": strict.get("changed_phrases", []),
        "affected_driver": strict.get("affected_driver") or contextual.get("affected_driver"),
        "required_confirmation": strict.get("required_confirmation") or contextual.get("required_confirmation", []),
        "allowed_citation_ids": contextual.get("citations_used") or [_citation_id(item) for item in event.citations],
        "source_citations": [
            {
                "role": "current",
                "source": current_citation.get("source") or (event.citations[0].source if event.citations else ""),
                "url": current_citation.get("url") or (event.citations[0].url if event.citations else ""),
                "accession": current_citation.get("accession") or strict.get("current_accession"),
                "period": current_citation.get("period_end") or strict.get("current_period") or contextual.get("current_period"),
            },
            {
                "role": "prior",
                "source": prior_citation.get("source") or strict.get("prior_form") or contextual.get("prior_source"),
                "url": prior_citation.get("url") or strict.get("prior_url"),
                "accession": prior_citation.get("accession") or strict.get("prior_accession"),
                "period": prior_citation.get("period_end") or strict.get("prior_period") or contextual.get("prior_period"),
            },
        ],
        "rules": [
            "Use only these retrieved excerpts.",
            "Do not treat cross-source context as a same-section filing change.",
            "Do not invent a prior claim, citation, metric, target, probability, or recommendation.",
        ],
    }


def _clean_status(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text in {THESIS_GRADE, WATCH_ITEM, NOT_THESIS_GRADE} else default


def _clean_direction(value: Any, default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"positive", "negative", "neutral", "mixed"} else default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
