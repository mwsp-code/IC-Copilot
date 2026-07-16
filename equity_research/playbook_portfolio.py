from __future__ import annotations

from .models import (
    ChangeEvent,
    CompanyEconomics,
    CompanyIdentity,
    CompanyPlaybookPortfolio,
    ManagementSourcePackage,
    PlaybookAssignment,
)


_SECONDARY_RULES = {
    "ai_infrastructure": (
        "AI infrastructure and accelerated computing",
        {"ai", "accelerated computing", "gpu", "data center", "inference", "training"},
    ),
    "cloud_platform": (
        "Cloud platform and AI services",
        {"cloud", "cloud computing", "infrastructure as a service", "ai services"},
    ),
    "digital_commerce": (
        "Digital commerce and marketplace",
        {"commerce", "marketplace", "gmv", "ecommerce", "take rate", "merchant"},
    ),
    "networking_software": (
        "Networking, systems, and software ecosystem",
        {"networking", "software", "platform", "subscription", "systems"},
    ),
    "logistics_local_services": (
        "Logistics and local services",
        {"logistics", "delivery", "local services", "fulfillment"},
    ),
    "consumer_devices": (
        "Consumer devices and installed-base monetization",
        {"devices", "installed base", "services", "smartphone", "hardware"},
    ),
}


def build_playbook_portfolio(
    identity: CompanyIdentity,
    economics: CompanyEconomics,
    events: list[ChangeEvent],
    management: ManagementSourcePackage,
) -> CompanyPlaybookPortfolio:
    primary = PlaybookAssignment(
        playbook_id=_slug(economics.industry_playbook.sector_template),
        label=economics.industry_playbook.industry_label,
        role="Primary",
        status="Validated",
        rationale=f"Selected by the existing company/sector playbook mapping for {identity.ticker}.",
        evidence_ids=[
            citation.accession or citation.url
            for event in events[:5] for citation in event.citations[:1]
            if citation.accession or citation.url
        ],
    )
    corpus_parts = [economics.business_model]
    corpus_parts.extend(f"{item.title} {item.summary}" for item in events)
    corpus_parts.extend(item.statement for item in management.claims)
    corpus = " ".join(corpus_parts).lower()
    secondary: list[PlaybookAssignment] = []
    primary_text = f"{primary.label} {primary.playbook_id}".lower()
    for playbook_id, (label, tokens) in _SECONDARY_RULES.items():
        matches = sorted(token for token in tokens if token in corpus)
        if not matches or any(token in primary_text for token in matches):
            continue
        evidence_ids = _matching_evidence_ids(events, management, matches)
        secondary.append(PlaybookAssignment(
            playbook_id=playbook_id,
            label=label,
            role="Secondary",
            status="Validated" if evidence_ids else "Provisional",
            rationale=f"Matched source-backed business terms: {', '.join(matches[:4])}.",
            evidence_ids=evidence_ids,
        ))
    validated = [item for item in secondary if item.status == "Validated"][:2]
    gaps = []
    if secondary and not validated:
        gaps.append("Secondary playbook terms were detected, but no source-linked event or management claim validated them.")
    return CompanyPlaybookPortfolio(identity.ticker, primary, validated, gaps)


def _matching_evidence_ids(
    events: list[ChangeEvent], management: ManagementSourcePackage, tokens: list[str],
) -> list[str]:
    rows: list[str] = []
    for event in events:
        text = f"{event.title} {event.summary}".lower()
        if not any(token in text for token in tokens):
            continue
        for citation in event.citations[:1]:
            rows.append(citation.accession or citation.url)
    for claim in management.claims:
        if any(token in claim.statement.lower() for token in tokens):
            rows.append(claim.claim_id)
    return list(dict.fromkeys(item for item in rows if item))


def _slug(value: str) -> str:
    return "_".join(value.lower().replace("/", " ").split()) or "unmapped"
