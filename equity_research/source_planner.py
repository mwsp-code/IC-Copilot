from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .adr_profiles import adr_profile_for
from .models import (
    ChangeEvent,
    ClaimValidationResult,
    CompanyIdentity,
    ManagementSourcePackage,
    ResearchSourcePlan,
    ResearchSourceRequest,
    SourceRegistryEntry,
)


SOURCE_REGISTRY_VERSION = "source-registry-v1"
PROMPT_VERSION = "source-agent-v1"

SOURCE_REGISTRY: tuple[SourceRegistryEntry, ...] = (
    SourceRegistryEntry("sec_filing", "SEC filing or exhibit", 1, notes="10-K, 10-Q, 8-K, 20-F, 40-F, 6-K, exhibits.", source_family="primary_company"),
    SourceRegistryEntry("issuer_ir", "Issuer IR page", 1, notes="Company investor-relations pages discovered from configured seeds.", source_family="primary_company"),
    SourceRegistryEntry("earnings_transcript", "Earnings-call transcript", 2, notes="Alpha Vantage/FMP/CSV/manual transcript adapters.", source_family="management_context", allowed_stage="Research-Ready"),
    SourceRegistryEntry("agm_egm_proxy", "AGM/EGM/proxy/meeting material", 1, notes="DEF 14A, 6-K meeting materials, voting results.", source_family="primary_company"),
    SourceRegistryEntry("presentation", "Issuer presentation or investor-day deck", 2, notes="Issuer slide decks and filed exhibits.", source_family="issuer_context", allowed_stage="Research-Ready"),
    SourceRegistryEntry("consensus_manual", "Consensus/manual import", 3, notes="CSV/manual import or configured licensed consensus.", source_family="market_capture", allowed_stage="Research-Ready"),
    SourceRegistryEntry("macro_market", "Approved macro/market provider", 2, notes="Official macro, price, factor, and positioning providers.", source_family="market_context", allowed_stage="Research-Ready"),
    SourceRegistryEntry("regulator_court", "Regulator/court official source", 1, notes="Regulator releases, enforcement records, court/docket references, official agency records.", source_family="primary_official"),
    SourceRegistryEntry("product_safety", "Product/safety official source", 1, notes="FDA, CPSC, NHTSA, FAA, FCC, EPA, recalls, approvals, and official notices.", source_family="primary_official"),
    SourceRegistryEntry("patent_ip", "Patent/IP or product-pipeline source", 1, notes="USPTO, FDA/clinical trial, IP dispute, or official product-pipeline database.", source_family="primary_official"),
    SourceRegistryEntry("government_contract", "Government contract source", 1, notes="USAspending, SAM.gov, agency award, modification, or official procurement source.", source_family="primary_official"),
    SourceRegistryEntry("trade_import_export", "Trade/import/export official source", 2, notes="USITC, Census trade, BIS/export-control, tariff, customs, import/export source.", source_family="primary_official", allowed_stage="Research-Ready"),
    SourceRegistryEntry("labor_employment", "Labor/employment official source", 2, notes="WARN, BLS, OSHA, NLRB, or state-level official labor source.", source_family="primary_official", allowed_stage="Research-Ready"),
    SourceRegistryEntry("industry_official_dataset", "Industry-specific official dataset", 2, notes="Official industry volume, pricing, utilization, safety, or demand dataset.", source_family="primary_official", allowed_stage="Research-Ready"),
    SourceRegistryEntry("hkex_document", "HKEX official issuer document", 1, notes="Hong Kong issuer annual/interim/results announcements and circulars.", source_family="global_peer_official", allowed_stage="Research-Ready"),
    SourceRegistryEntry("cninfo_document", "CNInfo official disclosure", 1, notes="China A-share annual, interim, quarterly, and results disclosures.", source_family="global_peer_official", allowed_stage="Research-Ready"),
    SourceRegistryEntry("issuer_ir_report", "Issuer IR official report", 1, notes="Issuer-hosted annual/interim/results reports and presentations from registered IR seeds.", source_family="global_peer_official", allowed_stage="Research-Ready"),
    SourceRegistryEntry("global_peer_official_document", "Global peer official document", 1, notes="Registered non-US peer official filing/report source used for peer metric read-through.", source_family="global_peer_official", allowed_stage="Research-Ready"),
    SourceRegistryEntry("licensed_newswire", "Licensed newswire", 3, notes="Reuters/AP/Dow Jones/Bloomberg-style licensed metadata or excerpt; context/source lead only.", source_family="news", allowed_stage="Candidate", licensing_policy="licensed_metadata_excerpt_only"),
    SourceRegistryEntry("reputable_publisher", "Reputable publisher API/manual import", 3, notes="FT/WSJ/Nikkei/Caixin/SCMP/NYT-style source; context/source lead only.", source_family="news", allowed_stage="Candidate", licensing_policy="licensed_metadata_excerpt_only"),
    SourceRegistryEntry("public_news_metadata", "Public news metadata", 4, notes="Headline/URL discovery services; metadata only unless terms permit more.", source_family="news", allowed_stage="Candidate"),
    SourceRegistryEntry("gdelt_narrative", "GDELT/narrative saturation", 4, notes="Narrative volume and topic crowding only; not factual corroboration.", source_family="narrative", allowed_stage="Candidate"),
    SourceRegistryEntry("external_research", "External research context", 4, notes="Wisburg/licensed external research metadata and capped excerpts; context only.", source_family="external_research", allowed_stage="Candidate", licensing_policy="licensed_metadata_excerpt_only"),
)


def build_source_plan(
    identity: CompanyIdentity,
    events: list[ChangeEvent],
    validation: ClaimValidationResult,
    management_sources: ManagementSourcePackage | None = None,
    *,
    llm_provider: Any | None = None,
    use_llm: bool = False,
) -> ResearchSourcePlan:
    requests = _deterministic_requests(identity, events, validation, management_sources)
    provider_name = "deterministic"
    data_gaps: list[str] = []
    if use_llm and llm_provider is not None:
        provider_name = getattr(llm_provider, "provider_name", "llm")
        llm_requests, message = _llm_requests(identity, events, validation, requests, llm_provider)
        if llm_requests:
            requests = _merge_requests(requests, llm_requests)
        elif message:
            data_gaps.append(message)
    if not requests:
        data_gaps.append("No follow-up source requests were generated.")
    return ResearchSourcePlan(
        ticker=identity.ticker,
        status="Available" if requests else "Unavailable",
        generated_at=_utc_now(),
        registry_version=SOURCE_REGISTRY_VERSION,
        requests=requests[:12],
        data_gaps=data_gaps,
        provider=provider_name,
    )


def allowed_source_types() -> set[str]:
    return {entry.source_type for entry in SOURCE_REGISTRY if entry.allowed}


def _deterministic_requests(
    identity: CompanyIdentity,
    events: list[ChangeEvent],
    validation: ClaimValidationResult,
    management_sources: ManagementSourcePackage | None,
) -> list[ResearchSourceRequest]:
    requests: list[ResearchSourceRequest] = []
    weak_claims = [claim for claim in validation.claims if claim.status != "Thesis-grade"]
    missing_prior_events = [
        event for event in events
        if event.metrics.get("signal_method") == "disclosure_change_engine"
        and str(event.metrics.get("comparison_status") or "") not in {"period_aligned", "comparable_imperfect"}
    ]
    if missing_prior_events:
        forms = sorted({str(event.metrics.get("current_form") or event.source) for event in missing_prior_events})
        requests.append(_request(
            identity.ticker,
            "sec_filing",
            "Recover prior comparable disclosure context",
            "A current disclosure was found, but a distinct earlier same-form, earlier-period section was not validated.",
            f"Prior {'/'.join(forms)} accession, period, aligned section, parser status, and exact before/after excerpts.",
            "High",
            "Free / medium latency",
            "Confirms whether the disclosure truly expanded, contracted, changed direction, or was merely missing from the parser.",
        ))
        if _is_adr_or_fpi(identity, events):
            adr_profile = adr_profile_for(
                identity.ticker,
                tuple(
                    sorted({
                        str(citation.form)
                        for event in events for citation in event.citations
                        if citation.form
                    })
                ),
            )
            requests.extend([
                _request(
                    identity.ticker,
                    "sec_filing",
                    "Compare prior 6-K results announcement",
                    "Foreign private issuers often communicate quarterly outlook, segment trends, and capital allocation through 6-K exhibits rather than a 10-Q.",
                    "Prior/current 6-K results excerpts with event date, fiscal period, exhibit, section, and exact changed language.",
                    "High",
                    "Free / medium latency",
                    "Confirms whether the annual-report disclosure shift was foreshadowed or contradicted by interim issuer reporting.",
                ),
                _request(
                    identity.ticker,
                    "issuer_ir_report",
                    "Recover prior ADR/FPI annual or interim report",
                    "Foreign issuers may publish the comparable discussion through issuer IR or home-market filings when SEC section extraction is incomplete.",
                    "Prior annual/interim report with period, section heading, exact excerpt, and source URL.",
                    "High",
                    "Free / variable latency",
                    "Confirms or disproves the apparent disclosure change using a primary issuer document.",
                ),
                _request(
                    identity.ticker,
                    "earnings_transcript",
                    "Compare management intent across prior call and current disclosure",
                    "Cross-source comparison can reveal a change in management emphasis even when filing headings differ.",
                    "Current and prior cited excerpts, speaker, event date, affected driver, and explicit changed language.",
                    "Medium",
                    "Free/paid depending on transcript provider",
                    "Tests whether management intent, outlook, or risk emphasis changed across reporting channels.",
                ),
                _request(
                    identity.ticker,
                    "presentation",
                    "Compare prior results deck and current disclosure",
                    "Issuer decks often preserve segment KPIs, capital allocation, and management emphasis omitted from standardized filing sections.",
                    "Prior/current segment KPI, guidance, buyback, margin, and strategy excerpts with periods and slide/page references.",
                    "Medium",
                    "Free / variable latency",
                    "Confirms whether filing-language movement reflects an operating change or a presentation-format difference.",
                ),
                _request(
                    identity.ticker,
                    "agm_egm_proxy",
                    "Check AGM/EGM and governance materials for intent changes",
                    "Capital allocation, governance, ownership, and strategic commitments may appear in meeting materials rather than periodic filing sections.",
                    "Meeting item, vote result, commitment, date, and exact issuer citation.",
                    "Low",
                    "Free / medium latency",
                    "Confirms or disproves governance and capital-allocation interpretations.",
                ),
            ])
            if adr_profile and adr_profile.home_exchange.upper() == "HKEX":
                requests.append(_request(
                    identity.ticker,
                    "hkex_document",
                    "Recover prior HKEX annual/interim disclosure",
                    "The ADR has an HKEX-linked home listing, so the prior local annual/interim report may provide the missing comparable section.",
                    "HKEX announcement/report with issuer, local ticker, reporting period, section, page, and exact excerpt.",
                    "High",
                    "Free / medium latency",
                    "Confirms whether the apparent SEC disclosure shift also exists in the home-market primary filing.",
                ))
    if _has_share_normalization_gap(events):
        requests.append(_request(
            identity.ticker,
            "sec_filing",
            "Reconcile share-count basis before interpreting dilution",
            "A share-count signal can reflect ordinary shares, ADS ratio, weighted-average basis, splits, or buybacks rather than true dilution.",
            "Ordinary share count, ADS count, ADR ratio, weighted-average shares, period-end shares, split/corporate-action flags, and buyback table.",
            "High",
            "Free / medium latency",
            "Confirms whether the share-count move is true dilution, capital return, or a security-basis mismatch.",
        ))
        if adr_profile_for(identity.ticker):
            requests.append(_request(
                identity.ticker,
                "presentation",
                "Check ADR/FPI results deck for buyback and ADS reconciliation",
                "Cross-border issuers often explain repurchases and ADS/ordinary-share basis in results decks or 6-K exhibits.",
                "Buyback authorization, shares repurchased, ADS ratio, ordinary shares outstanding, and per-ADS basis.",
                "High",
                "Free / variable latency",
                "Disproves dilution if the apparent move is caused by ADR ratio, buyback disclosure, or basis changes.",
            ))
    if weak_claims:
        requests.append(_request(
            identity.ticker,
            "sec_filing",
            "Re-check exact filing section around weak claim",
            "Weak or watch-only claims need before/after source text before becoming an idea.",
            "Exact current and prior excerpt with accession, section, and period.",
            "High",
            "Free / medium latency",
            "Confirms whether the detected language is substantive or boilerplate.",
        ))
    if any(event.category == "guidance" for event in events):
        requests.append(_request(
            identity.ticker,
            "earnings_transcript",
            "Inspect prepared remarks and Q&A for guidance corroboration",
            "Guidance-like filing language needs management context and Q&A follow-through.",
            "Speaker-turn guidance, metric, period, and exact quote.",
            "High",
            "Free/paid depending on transcript provider",
            "Confirms or disproves that management guided a material KPI.",
        ))
        requests.append(_request(
            identity.ticker,
            "consensus_manual",
            "Check point-in-time consensus and estimate revisions",
            "Market-capture claims need evidence that expectations did or did not move.",
            "Pre/post revenue, EPS, margin, and target revisions.",
            "Medium",
            "Free if keyed/manual; paid for richer providers",
            "Tests whether the thesis is already reflected in estimates.",
        ))
    if management_sources and management_sources.status != "Available":
        requests.append(_request(
            identity.ticker,
            "issuer_ir",
            "Find issuer IR source seeds and artifacts",
            "Management-source coverage is incomplete.",
            "Issuer releases, call transcripts, presentations, and investor-day decks.",
            "Medium",
            "Free / variable latency",
            "Adds primary issuer context for claims not captured in SEC text.",
        ))
    if _is_adr_or_fpi(identity, events):
        requests.append(_request(
            identity.ticker,
            "agm_egm_proxy",
            "Inspect ADR/FPI AGM, EGM, and 6-K meeting materials",
            "Foreign private issuers often disclose governance and shareholder materials outside 10-Q style filings.",
            "Meeting notice, voting results, shareholder proposals, and governance changes.",
            "Medium",
            "Free / medium latency",
            "Confirms governance or management-quality claims for ADR/HK-style issuers.",
        ))
    if any(claim.business_driver == "Unmapped" for claim in validation.claims):
        requests.append(_request(
            identity.ticker,
            "presentation",
            "Find segment/KPI bridge in issuer presentation",
            "Unmapped claims need a business-driver bridge before promotion.",
            "Segment KPI, margin bridge, capital allocation detail, or product-cycle context.",
            "Medium",
            "Free/paid depending on source",
            "Connects source language to company economics and valuation.",
        ))
    if any(event.event_date for event in events):
        requests.append(_request(
            identity.ticker,
            "macro_market",
            "Validate event-window price attribution",
            "Ideas need company-specific residual, sector, market, and macro context.",
            "Raw, market-relative, sector-relative, beta-adjusted, and residual returns.",
            "Medium",
            "Free/paid depending on provider",
            "Disproves thesis if the move was sector/macro-only.",
        ))
    return _dedupe_requests(requests)


def _llm_requests(
    identity: CompanyIdentity,
    events: list[ChangeEvent],
    validation: ClaimValidationResult,
    existing: list[ResearchSourceRequest],
    provider: Any,
) -> tuple[list[ResearchSourceRequest], str]:
    prompt = {
        "prompt_version": PROMPT_VERSION,
        "task": "Suggest follow-up research sources using only allowed source_type values.",
        "allowed_source_types": sorted(allowed_source_types()),
        "rules": [
            "Do not invent evidence or treat suggested URLs as trusted.",
            "Do not browse. Recommend source types and why deterministic adapters should inspect them.",
            "Each request must confirm or disprove a specific claim gap.",
        ],
        "company": {"ticker": identity.ticker, "name": identity.name, "sic": identity.sic, "sic_description": identity.sic_description},
        "claims": [
            {
                "claim_id": claim.claim_id,
                "status": claim.status,
                "category": claim.event_category,
                "direction": claim.direction,
                "business_driver": claim.business_driver,
                "reason": claim.reason,
                "gap": claim.not_thesis_grade_reason,
            }
            for claim in validation.claims[:12]
        ],
        "current_requests": [
            {
                "source_type": request.source_type,
                "title": request.title,
                "reason_to_inspect": request.reason_to_inspect,
            }
            for request in existing[:8]
        ],
        "output_schema": {
            "requests": [{
                "source_type": "one allowed source_type",
                "title": "string",
                "reason_to_inspect": "string",
                "expected_evidence_type": "string",
                "priority": "High | Medium | Low",
                "cost_latency": "string",
                "confirms_or_disproves": "string",
                "suggested_url": "string or null"
            }]
        },
    }
    try:
        payload = provider.complete_json(prompt)
    except Exception as exc:
        return [], f"LLM source planner failed: {exc}"
    allowed = allowed_source_types()
    requests: list[ResearchSourceRequest] = []
    for raw in payload.get("requests", []) if isinstance(payload, dict) else []:
        source_type = str(raw.get("source_type") or "")
        if source_type not in allowed:
            continue
        requests.append(_request(
            identity.ticker,
            source_type,
            str(raw.get("title") or "LLM-proposed source check"),
            str(raw.get("reason_to_inspect") or "LLM proposed this source from the allowed registry."),
            str(raw.get("expected_evidence_type") or "Source-linked evidence"),
            str(raw.get("priority") or "Medium"),
            str(raw.get("cost_latency") or "Unknown"),
            str(raw.get("confirms_or_disproves") or "Confirms or disproves a claim gap."),
            suggested_url=str(raw.get("suggested_url") or "") or None,
            provider=f"llm:{getattr(provider, 'provider_name', 'llm')}",
        ))
    return requests, "" if requests else "LLM source planner returned no allowed registry requests."


def _request(
    ticker: str,
    source_type: str,
    title: str,
    reason: str,
    expected: str,
    priority: str,
    cost_latency: str,
    confirms: str,
    *,
    suggested_url: str | None = None,
    provider: str = "deterministic",
) -> ResearchSourceRequest:
    request_id = hashlib.sha1(f"{ticker}:{source_type}:{title}:{reason}".encode("utf-8")).hexdigest()[:12]
    return ResearchSourceRequest(
        request_id=request_id,
        source_type=source_type,
        title=title,
        reason_to_inspect=reason,
        expected_evidence_type=expected,
        priority=priority if priority in {"High", "Medium", "Low"} else "Medium",
        cost_latency=cost_latency,
        confirms_or_disproves=confirms,
        suggested_url=suggested_url,
        provider=provider,
    )


def _merge_requests(
    existing: list[ResearchSourceRequest],
    proposed: list[ResearchSourceRequest],
) -> list[ResearchSourceRequest]:
    by_key = {(item.source_type, item.title.lower()): item for item in existing}
    for item in proposed:
        by_key.setdefault((item.source_type, item.title.lower()), item)
    order = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(by_key.values(), key=lambda item: (order.get(item.priority, 1), item.source_type, item.title))


def _dedupe_requests(requests: list[ResearchSourceRequest]) -> list[ResearchSourceRequest]:
    return _merge_requests([], requests)


def _is_adr_or_fpi(identity: CompanyIdentity, events: list[ChangeEvent]) -> bool:
    forms = {citation.form for event in events for citation in event.citations if citation.form}
    return (
        adr_profile_for(identity.ticker, tuple(forms)) is not None
        or bool(forms & {"20-F", "40-F", "6-K"})
        or identity.exchange.upper() not in {"NYSE", "NASDAQ", "US", ""}
    )


def _has_share_normalization_gap(events: list[ChangeEvent]) -> bool:
    for event in events:
        text = f"{event.title} {event.metrics.get('metric_name', '')}".lower()
        if "share" not in text:
            continue
        if event.metrics.get("normalization_required"):
            return True
        status = str(event.metrics.get("share_reconciliation_status") or event.metrics.get("normalization_status") or "")
        if status and status != "Reconciled":
            return True
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
