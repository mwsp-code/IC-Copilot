from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Iterable
from urllib.parse import urlsplit

from .models import (
    DiscoveredSourceCandidate,
    NormalizedSourceRecord,
    PrimarySourceObservation,
    RegulatoryMomentumAssessment,
    RegulatoryProspectObservation,
    SourceAdmissionDecision,
)


_REGULATORY_TERMS = {
    "regulation",
    "regulatory",
    "regulator",
    "antitrust",
    "competition",
    "privacy",
    "export control",
    "sanction",
    "tariff",
    "court",
    "lawsuit",
    "enforcement",
    "investigation",
    "legislation",
    "bill",
    "rule",
    "consultation",
    "hearing",
    "compliance",
    "approval",
    "ban",
    "restriction",
}

_STAGES = (
    ("repealed", ("repealed", "rescinded", "withdrawn rule")),
    ("challenged_or_stayed", ("stayed", "injunction", "appeal", "challenged")),
    ("effective_or_enforced", ("effective", "enforced", "penalty", "fined", "settlement")),
    ("final_rule_or_law", ("final rule", "signed into law", "enacted", "final order")),
    ("proposal_or_bill", ("proposed rule", "proposal", "introduced bill", "draft law", "bill")),
    ("consultation_or_draft", ("consultation", "comment period", "draft rule", "white paper")),
    ("official_agenda_or_speech", ("agenda", "testimony", "hearing", "official speech", "staff statement")),
    ("policy_discussion", ("considering", "may regulate", "policy discussion", "reviewing")),
    ("rumor_or_reporting", ("reportedly", "sources said", "may", "could", "rumor")),
)

_STAGE_RANK = {
    "none": 0,
    "rumor_or_reporting": 1,
    "policy_discussion": 2,
    "official_agenda_or_speech": 3,
    "consultation_or_draft": 4,
    "proposal_or_bill": 5,
    "final_rule_or_law": 6,
    "effective_or_enforced": 7,
    "challenged_or_stayed": 7,
    "repealed": 7,
}

_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "company",
    "regulatory",
    "regulation",
    "rule",
}


def build_regulatory_radar(
    ticker: str,
    candidates: Iterable[DiscoveredSourceCandidate],
    admissions: Iterable[SourceAdmissionDecision],
    *,
    primary_observations: Iterable[PrimarySourceObservation] = (),
    normalized_sources: Iterable[NormalizedSourceRecord] = (),
) -> RegulatoryMomentumAssessment:
    candidate_map = {item.candidate_id: item for item in candidates}
    admissions_by_candidate = {
        item.candidate_id: item for item in admissions
    }
    signals: list[dict] = []
    seen_candidate_origins: set[str] = set()
    for candidate_id, candidate in candidate_map.items():
        dedupe_key = (
            candidate.syndication_fingerprint
            or _stable_id(
                candidate.ownership_group,
                candidate.url,
                candidate.title,
            )
        )
        if dedupe_key in seen_candidate_origins:
            continue
        seen_candidate_origins.add(dedupe_key)
        text = " ".join((
            candidate.title,
            candidate.excerpt,
            candidate.claimed_relevance,
            candidate.claim_to_test,
        ))
        if not _is_regulatory(text):
            continue
        decision = admissions_by_candidate.get(candidate_id)
        signals.append({
            "id": (
                decision.evidence_id
                if decision and decision.evidence_id else candidate_id
            ),
            "candidate_id": candidate_id,
            "headline": candidate.title,
            "text": text,
            "publisher": candidate.publisher,
            "origin": candidate.ownership_group,
            "date": candidate.publication_date,
            "url": candidate.url,
            "tier": decision.source_tier if decision else None,
            "source_type": (
                decision.source_type if decision
                else candidate.proposed_source_type
            ),
            "admitted_as": decision.admitted_as if decision else "none",
            "official": bool(
                decision
                and decision.source_tier == 1
                and decision.source_type == "regulator_court"
            ),
            "semi_official": bool(
                decision
                and decision.source_tier in {1, 2}
                and _stage(text) == "official_agenda_or_speech"
            ),
        })
    for item in primary_observations:
        text = f"{item.title} {item.summary} {item.driver_family}"
        if not _is_regulatory(text):
            continue
        signals.append({
            "id": item.observation_id,
            "headline": item.title,
            "text": text,
            "publisher": item.provider,
            "origin": item.provider,
            "date": item.source_as_of or item.observed_at,
            "url": item.url,
            "tier": item.source_tier,
            "source_type": item.source_type,
            "admitted_as": "evidence" if item.citation else "discovery_lead",
            "official": bool(item.official),
            "semi_official": bool(
                item.official and _stage(text) == "official_agenda_or_speech"
            ),
        })
    for item in normalized_sources:
        text = f"{item.metric_or_claim} {item.section} {item.exact_excerpt}"
        if item.source_type != "regulator_court" and not _is_regulatory(text):
            continue
        signals.append({
            "id": item.source_id,
            "headline": item.metric_or_claim or item.section,
            "text": text,
            "publisher": item.origin_group,
            "origin": item.origin_group,
            "date": item.publication_date,
            "url": item.source_url,
            "tier": item.source_tier,
            "source_type": item.source_type,
            "admitted_as": (
                "evidence"
                if item.validation_status == "validated"
                and item.retrieval_status != "metadata_only"
                else "discovery_lead"
            ),
            "official": bool(
                item.source_type == "regulator_court"
                and item.source_tier == 1
            ),
            "semi_official": bool(
                item.source_tier in {1, 2}
                and _stage(text) == "official_agenda_or_speech"
            ),
        })
    clusters = _cluster_signals(signals)
    prospects = [
        _prospect_from_cluster(ticker, items)
        for items in clusters
        if items
    ]
    prospects.sort(
        key=lambda item: (
            _STAGE_RANK.get(item.procedural_stage, 0),
            len(item.source_origin_groups),
            item.event_date,
        ),
        reverse=True,
    )
    official_count = sum(bool(item.official_source_ids) for item in prospects)
    official_origins = {
        _origin_for_source_id(source_id, signals).casefold()
        for item in prospects
        for source_id in [
            *item.official_source_ids,
            *item.semi_official_source_ids,
        ]
        if _origin_for_source_id(source_id, signals)
    }
    external_origins = {
        origin
        for item in prospects
        for origin in item.source_origin_groups
        if origin
        and origin.casefold() not in official_origins
    }
    highest = (
        prospects[0].procedural_stage if prospects else "none"
    )
    coverage = (
        "Available"
        if prospects else
        "No relevant claims"
        if signals else
        "No configured or admitted regulatory observations"
    )
    status = (
        prospects[0].classification if prospects else "No prospect detected"
    )
    return RegulatoryMomentumAssessment(
        ticker=ticker.upper(),
        status=status,
        summary=(
            f"{len(prospects)} regulatory prospect(s) detected; highest "
            f"procedural stage: {highest.replace('_', ' ')}."
            if prospects else
            "No decision-useful regulatory prospect was established from the "
            "current admitted evidence."
        ),
        highest_stage=highest,
        prospect_count=len(prospects),
        official_milestone_count=official_count,
        independent_external_origin_count=len(external_origins),
        next_milestones=list(dict.fromkeys(
            item.expected_milestone for item in prospects
            if item.expected_milestone
        ))[:5],
        prospects=prospects,
        coverage_status=coverage,
        data_gaps=(
            []
            if prospects else [
                "Continue official-feed and reputable reporting monitoring; "
                "absence of a detected prospect is not evidence that policy risk is absent."
            ]
        ),
    )


def _origin_for_source_id(source_id: str, signals: list[dict]) -> str:
    for item in signals:
        if str(item.get("id") or "") == source_id:
            return str(item.get("origin") or item.get("publisher") or "")
    return ""


def _prospect_from_cluster(
    ticker: str,
    signals: list[dict],
) -> RegulatoryProspectObservation:
    stages = [_stage(str(item.get("text") or "")) for item in signals]
    stage = max(stages, key=lambda item: _STAGE_RANK.get(item, 0))
    origins = list(dict.fromkeys(
        str(item.get("origin") or item.get("publisher") or "")
        for item in signals
        if item.get("origin") or item.get("publisher")
    ))
    official_ids = [
        str(item["id"]) for item in signals
        if item.get("official") and item.get("admitted_as") == "evidence"
    ]
    semi_ids = [
        str(item["id"]) for item in signals
        if item.get("semi_official") and item.get("admitted_as") == "evidence"
    ]
    external_ids = [
        str(item["id"]) for item in signals
        if not item.get("official") and not item.get("semi_official")
    ]
    classification, allowed_stage, confidence = _classification(
        stage,
        len(set(origins)),
        bool(official_ids),
        bool(semi_ids),
    )
    latest = max(
        signals,
        key=lambda item: str(item.get("date") or ""),
    )
    text = " ".join(str(item.get("text") or "") for item in signals)
    gaps = []
    if not official_ids and not semi_ids:
        gaps.append(
            "No official or semi-official procedural milestone is validated."
        )
    if not _financial_mechanisms(text):
        gaps.append("Issuer exposure and financial transmission remain unquantified.")
    return RegulatoryProspectObservation(
        prospect_id=_stable_id(
            "regulatory-prospect",
            ticker,
            _topic_signature(text),
            stage,
        ),
        ticker=ticker.upper(),
        headline=str(latest.get("headline") or "Regulatory prospect"),
        jurisdiction=_jurisdiction(text, str(latest.get("url") or "")),
        authority=str(latest.get("publisher") or "Unknown authority"),
        procedural_stage=stage,
        classification=classification,
        affected_business=_affected_business(text),
        affected_geography=_jurisdiction(text, str(latest.get("url") or "")),
        expected_milestone=_next_milestone(stage),
        event_date=str(latest.get("date") or "")[:10],
        financial_mechanisms=_financial_mechanisms(text),
        source_ids=[str(item["id"]) for item in signals],
        source_origin_groups=origins,
        official_source_ids=official_ids,
        semi_official_source_ids=semi_ids,
        external_source_ids=external_ids,
        allowed_stage=allowed_stage,
        confidence=confidence,
        status=(
            "Primary milestone checked"
            if official_ids or semi_ids else "External prospect only"
        ),
        data_gaps=gaps,
    )


def _classification(
    stage: str,
    origin_count: int,
    official: bool,
    semi_official: bool,
) -> tuple[str, str, str]:
    if official and stage == "repealed":
        return "Repealed", "Research-Ready", "High"
    if official and stage == "challenged_or_stayed":
        return "Challenged or stayed", "Research-Ready", "High"
    if official and stage == "effective_or_enforced":
        return "Implemented", "High-Conviction", "High"
    if official and stage == "final_rule_or_law":
        return "Enacted", "High-Conviction", "High"
    if official and _STAGE_RANK.get(stage, 0) >= 4:
        return "Primary-confirmed proposal", "Research-Ready", "High"
    if semi_official and origin_count >= 2:
        return "Developing regulatory prospect", "Research-Ready", "Medium"
    if origin_count >= 2:
        return "Reported regulatory prospect", "Research-Ready", "Medium"
    return "Regulatory watch", "Candidate", "Low"


def _cluster_signals(signals: list[dict]) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for signal in signals:
        terms = _topic_terms(str(signal.get("text") or ""))
        matched = None
        for cluster in clusters:
            cluster_terms = _topic_terms(" ".join(
                str(item.get("text") or "") for item in cluster
            ))
            if _jaccard(terms, cluster_terms) >= 0.25:
                matched = cluster
                break
        if matched is None:
            clusters.append([signal])
        else:
            matched.append(signal)
    return clusters


def _stage(text: str) -> str:
    lowered = text.casefold()
    for stage, terms in _STAGES:
        if any(term in lowered for term in terms):
            return stage
    return "policy_discussion"


def _is_regulatory(text: str) -> bool:
    lowered = text.casefold()
    return any(term in lowered for term in _REGULATORY_TERMS)


def _jurisdiction(text: str, url: str) -> str:
    lowered = f"{text} {url}".casefold()
    rules = (
        ("China", ("china", "beijing", ".gov.cn", "samr", "cac")),
        ("European Union", ("european commission", "eu ", "ec.europa.eu")),
        ("United Kingdom", ("united kingdom", "uk ", "gov.uk", "cma")),
        ("Hong Kong", ("hong kong", "hkma", "sfc", "hkex")),
        ("United States", ("united states", "federal", ".gov", "congress")),
    )
    for jurisdiction, terms in rules:
        if any(term in lowered for term in terms):
            return jurisdiction
    return "Unknown"


def _financial_mechanisms(text: str) -> list[str]:
    lowered = text.casefold()
    mechanisms = []
    rules = (
        ("Revenue / market access", ("ban", "market access", "approval", "restriction")),
        ("Margin / compliance cost", ("compliance", "fine", "penalty", "remedy")),
        ("Capital expenditure", ("investment requirement", "capacity", "infrastructure")),
        ("Competition / pricing", ("antitrust", "competition", "pricing")),
        ("Credit / liquidity", ("capital requirement", "liquidity", "settlement")),
        ("Tax / cash flow", ("tax", "tariff", "duty")),
    )
    for label, terms in rules:
        if any(term in lowered for term in terms):
            mechanisms.append(label)
    return mechanisms


def _affected_business(text: str) -> str:
    lowered = text.casefold()
    for label, terms in (
        ("Artificial intelligence", ("artificial intelligence", " ai ", "model")),
        ("Cloud", ("cloud", "data center")),
        ("Commerce", ("commerce", "marketplace", "retail")),
        ("Financial services", ("bank", "lending", "payments")),
        ("Healthcare", ("drug", "clinical", "medical", "fda")),
        ("Energy", ("oil", "gas", "emissions", "energy")),
    ):
        if any(term in lowered for term in terms):
            return label
    return "Company-wide or unresolved"


def _next_milestone(stage: str) -> str:
    return {
        "rumor_or_reporting": "Official agenda, speech, draft, or denial",
        "policy_discussion": "Formal agenda, consultation, or draft",
        "official_agenda_or_speech": "Consultation, draft, hearing, or proposal",
        "consultation_or_draft": "Comment deadline and final proposal",
        "proposal_or_bill": "Vote, final rule, order, or enactment",
        "final_rule_or_law": "Effective date and implementation guidance",
        "effective_or_enforced": "Issuer response, remedy, appeal, and financial impact",
        "challenged_or_stayed": "Court decision, revised rule, settlement, or repeal",
        "repealed": "Replacement policy, renewed proposal, or issuer response",
    }.get(stage, "Next official procedural milestone")


def _topic_terms(text: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-z0-9][a-z0-9-]{2,}", text.casefold())
        if item not in _STOPWORDS
    }


def _topic_signature(text: str) -> str:
    return "|".join(sorted(_topic_terms(text))[:12])


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _stable_id(*parts: object) -> str:
    return hashlib.sha256(
        "|".join(str(item or "") for item in parts).encode("utf-8")
    ).hexdigest()[:20]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
