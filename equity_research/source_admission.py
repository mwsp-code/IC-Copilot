from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from .models import (
    DiscoveredSourceCandidate,
    NewsArticleObservation,
    SourceAdmissionDecision,
)
from .domain.research_integrity import (
    CandidateDiscoveryItem,
    CandidateStatus,
    CandidateType,
    EvidenceAdmissionGateway,
    EvidenceAdmissionPolicy,
    RetrievedCandidateDocument,
)


_OFFICIAL_DOMAIN_RULES = {
    "sec.gov": ("primary_official", "sec_filing", 1),
    "federalregister.gov": ("primary_official", "regulator_court", 1),
    "regulations.gov": ("primary_official", "regulator_court", 1),
    "congress.gov": ("primary_official", "regulator_court", 1),
    "justice.gov": ("primary_official", "regulator_court", 1),
    "ftc.gov": ("primary_official", "regulator_court", 1),
    "fda.gov": ("primary_official", "product_safety", 1),
    "cftc.gov": ("primary_official", "regulator_court", 1),
    "federalreserve.gov": ("primary_official", "regulator_court", 1),
    "ec.europa.eu": ("primary_official", "regulator_court", 1),
    "gov.uk": ("primary_official", "regulator_court", 1),
    "hkexnews.hk": ("global_peer_official", "hkex_document", 1),
    "cninfo.com.cn": ("global_peer_official", "cninfo_document", 1),
    "bis.org": ("historical_context", "institutional_history", 2),
    "imf.org": ("historical_context", "institutional_history", 2),
    "worldbank.org": ("historical_context", "institutional_history", 2),
    "oecd.org": ("historical_context", "institutional_history", 2),
    "nber.org": ("historical_context", "institutional_history", 2),
}

_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I),
    re.compile(r"system\s+prompt", re.I),
    re.compile(r"developer\s+message", re.I),
    re.compile(r"reveal\s+(?:the\s+)?(?:api\s+key|secret|credentials)", re.I),
    re.compile(r"execute\s+(?:this\s+)?(?:command|code)", re.I),
)


def candidates_from_news(
    ticker: str,
    articles: Iterable[NewsArticleObservation],
) -> list[DiscoveredSourceCandidate]:
    candidates = []
    for article in articles:
        candidates.append(DiscoveredSourceCandidate(
            candidate_id=_stable_id(
                "source-candidate", ticker, article.url, article.headline
            ),
            ticker=ticker.upper(),
            url=_canonical_url(article.url),
            title=article.headline,
            publisher=article.publisher,
            ownership_group=article.source_origin_group,
            proposed_source_family=article.article_type,
            proposed_source_type=_candidate_source_type(article),
            publication_date=str(article.published_at or ""),
            language=article.language,
            discovery_provider=article.provider,
            discovery_method="registered_news_adapter",
            claimed_relevance=article.causal_driver,
            claim_to_test=article.headline,
            excerpt=article.excerpt,
            licensing_policy=article.licensing_policy,
            full_text_status=article.full_text_status,
            syndication_fingerprint=article.syndication_fingerprint,
            evidence_ids=[article.evidence_id] if article.evidence_id else [],
        ))
    return _dedupe_candidates(candidates)


def admit_source_candidates(
    candidates: Iterable[DiscoveredSourceCandidate],
) -> list[SourceAdmissionDecision]:
    return [admit_source_candidate(item) for item in _dedupe_candidates(candidates)]


def admit_source_candidate(
    candidate: DiscoveredSourceCandidate,
) -> SourceAdmissionDecision:
    reasons: list[str] = []
    canonical_url = _canonical_url(candidate.url)
    domain = _domain(canonical_url)
    family, source_type, source_tier = _classify_source(candidate, domain)
    injection = _contains_prompt_injection(
        f"{candidate.title}\n{candidate.excerpt}"
    )
    if not canonical_url or not domain:
        reasons.append("A canonical HTTP(S) source URL is required.")
    if injection:
        reasons.append(
            "Source text contains instruction-like content and is quarantined."
        )
    if not candidate.publisher and not _official_rule(domain):
        reasons.append("Publisher identity is unresolved.")
    if not candidate.ownership_group:
        reasons.append("Source ownership/origin group is unresolved.")
    if not candidate.publication_date:
        reasons.append("Publication date is missing.")
    if candidate.full_text_status in {
        "metadata_only",
        "inaccessible",
        "paywalled_metadata_only",
        "",
    }:
        reasons.append(
            "Underlying content is unavailable; metadata cannot support a factual claim."
        )
    citation_bound = bool(
        candidate.excerpt
        and candidate.publication_date
        and canonical_url
    )
    if not citation_bound:
        reasons.append("Exact excerpt and evidence binding are incomplete.")

    hard_reject = not canonical_url or injection
    evidence_ready = (
        not hard_reject
        and citation_bound
        and candidate.full_text_status not in {
            "metadata_only",
            "inaccessible",
            "paywalled_metadata_only",
            "",
        }
        and source_tier in {1, 2, 3}
        and family != "unknown"
    )
    gateway_decision = None
    if evidence_ready:
        sandbox_candidate = CandidateDiscoveryItem(
            candidate_id=candidate.candidate_id,
            candidate_type=CandidateType.SOURCE.value,
            status=CandidateStatus.RETRIEVAL_REQUESTED.value,
            ticker=candidate.ticker.upper(),
            objective=candidate.claim_to_test or candidate.claimed_relevance or candidate.title,
            origin_role="registered_connector" if candidate.discovery_method.startswith("registered_") else "model_or_search",
            origin_provider=candidate.discovery_provider,
            url=canonical_url,
            title=candidate.title,
            snippet=candidate.excerpt,
            proposed_source_family=family,
            materiality="Medium",
            discovered_at=candidate.publication_date,
            connector_id=candidate.discovery_method,
        )
        gateway_decision = EvidenceAdmissionGateway().admit(
            sandbox_candidate,
            RetrievedCandidateDocument(
                candidate_id=candidate.candidate_id,
                connector_id=candidate.discovery_method or "registered_source_adapter",
                canonical_url=canonical_url,
                content=candidate.excerpt,
                source_family=family,
                source_type=source_type,
                publisher=candidate.publisher or candidate.ownership_group,
                publication_timestamp=candidate.publication_date,
                retrieval_timestamp=_now(),
                licensing_policy=candidate.licensing_policy or "metadata_only",
                provenance=candidate.ownership_group or domain,
                entitled=True,
                parser_status="parsed",
            ),
            EvidenceAdmissionPolicy(),
        ).decision
        if gateway_decision.status != "admitted":
            reasons.extend(
                item.strip()
                for item in gateway_decision.reason.split(";")
                if item.strip()
            )

    if hard_reject or (gateway_decision is not None and gateway_decision.status != "admitted"):
        status = "rejected"
        admitted_as = "none"
        validation_status = "rejected"
    elif gateway_decision is not None:
        status = "admitted"
        admitted_as = "evidence"
        validation_status = "validated"
    else:
        status = "admitted_as_lead"
        admitted_as = "discovery_lead"
        validation_status = "metadata_only"
    return SourceAdmissionDecision(
        decision_id=_stable_id(
            "source-admission", candidate.candidate_id, status, canonical_url
        ),
        candidate_id=candidate.candidate_id,
        ticker=candidate.ticker.upper(),
        status=status,
        admitted_as=admitted_as,
        canonical_url=canonical_url,
        source_family=family,
        source_type=source_type,
        source_tier=source_tier,
        allowed_stage=_allowed_stage(source_tier, evidence_ready),
        ownership_group=candidate.ownership_group or domain,
        licensing_policy=candidate.licensing_policy or "metadata_only",
        retrieval_status=(
            "fetched_live"
            if evidence_ready else candidate.full_text_status or "metadata_only"
        ),
        parser_status="parsed" if evidence_ready else "not_parsed",
        validation_status=validation_status,
        citation_bound=citation_bound,
        prompt_injection_quarantined=injection,
        reasons=list(dict.fromkeys(reasons)),
        evidence_id=(gateway_decision.evidence_id if gateway_decision else ""),
        decided_at=_now(),
    )


def _classify_source(
    candidate: DiscoveredSourceCandidate,
    domain: str,
) -> tuple[str, str, int | None]:
    official = _official_rule(domain)
    if official:
        return official
    proposed = candidate.proposed_source_type
    if proposed in {
        "licensed_newswire",
        "reputable_publisher",
        "management_press_interview",
    }:
        return "news", proposed, 3
    if proposed in {"public_news_metadata", "gdelt_narrative"}:
        return "news", proposed, 4
    if proposed in {
        "institutional_history",
        "industry_official_dataset",
    }:
        return "historical_context", proposed, 2
    return (
        candidate.proposed_source_family or "unknown",
        proposed or "web_source_candidate",
        4,
    )


def _candidate_source_type(article: NewsArticleObservation) -> str:
    if article.source_tier <= 3:
        return (
            "reputable_publisher"
            if article.article_type in {"analysis", "opinion"}
            else "licensed_newswire"
        )
    return "public_news_metadata"


def _official_rule(domain: str) -> tuple[str, str, int] | None:
    for suffix, rule in _OFFICIAL_DOMAIN_RULES.items():
        if domain == suffix or domain.endswith(f".{suffix}"):
            return rule
    if domain.endswith(".gov") or domain.endswith(".gov.cn"):
        return "primary_official", "regulator_court", 1
    return None


def _allowed_stage(source_tier: int | None, evidence_ready: bool) -> str:
    if not evidence_ready:
        return "Discovery"
    if source_tier in {1, 2}:
        return "High-Conviction"
    if source_tier == 3:
        return "Research-Ready"
    return "Candidate"


def _contains_prompt_injection(text: str) -> bool:
    return any(pattern.search(text or "") for pattern in _INJECTION_PATTERNS)


def _canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(str(value or "").strip())
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        parsed.query,
        "",
    ))


def _domain(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").casefold()
    except ValueError:
        return ""


def _dedupe_candidates(
    candidates: Iterable[DiscoveredSourceCandidate],
) -> list[DiscoveredSourceCandidate]:
    by_key: dict[str, DiscoveredSourceCandidate] = {}
    for candidate in candidates:
        key = (
            candidate.syndication_fingerprint
            or _stable_id(
                _canonical_url(candidate.url),
                candidate.ownership_group,
                candidate.title.casefold(),
            )
        )
        existing = by_key.get(key)
        if existing is None or len(candidate.excerpt) > len(existing.excerpt):
            by_key[key] = candidate
    return list(by_key.values())


def _stable_id(*parts: object) -> str:
    return hashlib.sha256(
        "|".join(str(item or "") for item in parts).encode("utf-8")
    ).hexdigest()[:20]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
