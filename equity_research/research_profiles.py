from __future__ import annotations

import hashlib
from dataclasses import asdict

from .models import (
    ChangeEvent,
    FilingRecord,
    HistoricalResearchPack,
    ManagementSourcePackage,
    ResearchProfile,
)


PROFILE_FAST = "fast_screening"
PROFILE_ADAPTIVE = "adaptive_ic"
PROFILE_DEEP = "deep_initiation"
PROFILE_EVENT = "investigate_event"


_PROFILES = {
    PROFILE_FAST: ResearchProfile(
        PROFILE_FAST,
        "Fast Screening",
        "Compact anomaly screen with the highest-priority investigation.",
        quarter_depth=4,
        annual_depth=2,
        call_depth=4,
        anomaly_limit=1,
    ),
    PROFILE_ADAPTIVE: ResearchProfile(
        PROFILE_ADAPTIVE,
        "Adaptive IC Research",
        "Default analyst workflow with adaptive five-year deepening for long-cycle issues.",
        quarter_depth=12,
        annual_depth=4,
        call_depth=12,
        anomaly_limit=5,
        adaptive_deepening=True,
    ),
    PROFILE_DEEP: ResearchProfile(
        PROFILE_DEEP,
        "Deep Initiation",
        "Full historical, segment, management, peer, and valuation investigation.",
        quarter_depth=20,
        annual_depth=5,
        call_depth=20,
        anomaly_limit=None,
        adaptive_deepening=True,
    ),
    PROFILE_EVENT: ResearchProfile(
        PROFILE_EVENT,
        "Investigate This Event",
        "Event-scoped history, causal hypotheses, corroboration, and monitor design.",
        quarter_depth=12,
        annual_depth=4,
        call_depth=12,
        anomaly_limit=1,
        adaptive_deepening=True,
        event_scoped=True,
    ),
}


def research_profiles() -> list[ResearchProfile]:
    return list(_PROFILES.values())


def resolve_research_profile(value: str | ResearchProfile | None) -> ResearchProfile:
    if isinstance(value, ResearchProfile):
        return value
    key = str(value or PROFILE_ADAPTIVE).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "fast": PROFILE_FAST,
        "screen": PROFILE_FAST,
        "adaptive": PROFILE_ADAPTIVE,
        "default": PROFILE_ADAPTIVE,
        "deep": PROFILE_DEEP,
        "initiation": PROFILE_DEEP,
        "event": PROFILE_EVENT,
    }
    return _PROFILES.get(aliases.get(key, key), _PROFILES[PROFILE_ADAPTIVE])


def event_identifier(event: ChangeEvent) -> str:
    citation = event.citations[0] if event.citations else None
    raw = "|".join((
        event.category,
        event.title,
        event.event_date or "",
        citation.accession if citation and citation.accession else "",
        citation.period_end if citation and citation.period_end else "",
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def select_profile_events(
    events: list[ChangeEvent],
    profile: ResearchProfile,
    investigate_event_id: str | None = None,
) -> list[ChangeEvent]:
    ranked = sorted(
        events,
        key=lambda item: (
            item.severity,
            bool(item.metrics.get("thesis_grade_status") == "Thesis-grade"),
            item.event_date or "",
        ),
        reverse=True,
    )
    if profile.event_scoped:
        selected = [item for item in ranked if event_identifier(item) == investigate_event_id]
        return selected[:1]
    material = [item for item in ranked if item.severity >= 3]
    if profile.anomaly_limit is None:
        return material
    return material[: profile.anomaly_limit]


def build_historical_research_pack(
    ticker: str,
    profile: ResearchProfile,
    filings: list[FilingRecord],
    management: ManagementSourcePackage,
    events: list[ChangeEvent],
    investigate_event_id: str | None = None,
    parsed_filing_accessions: set[str] | None = None,
    historical_trend_summaries: list[str] | None = None,
) -> HistoricalResearchPack:
    selected = select_profile_events(events, profile, investigate_event_id)
    quarter_filings = [
        item for item in filings
        if item.form == "10-Q"
        or (
            item.form == "6-K"
            and any(token in (item.description or "").lower() for token in ("result", "earn", "interim", "quarter"))
        )
    ]
    annual_filings = [item for item in filings if item.form in {"10-K", "20-F", "40-F"}]
    call_ids = list(dict.fromkeys(
        turn.document_id for turn in management.transcript_turns if turn.document_id
    ))
    parsed_accessions = set(parsed_filing_accessions or ())
    parsed_quarters = [item for item in quarter_filings if item.accession in parsed_accessions]
    parsed_annuals = [item for item in annual_filings if item.accession in parsed_accessions]
    deepening_reasons = _adaptive_deepening_reasons(selected) if profile.adaptive_deepening else []
    requested_annuals = max(profile.annual_depth, 5 if deepening_reasons else profile.annual_depth)
    pack = HistoricalResearchPack(
        ticker=ticker,
        profile_id=profile.profile_id,
        status="Available",
        requested_quarters=profile.quarter_depth,
        requested_annual_reports=requested_annuals,
        requested_calls=profile.call_depth,
        discovered_quarters=min(len(quarter_filings), profile.quarter_depth),
        discovered_annual_reports=min(len(annual_filings), requested_annuals),
        discovered_calls=min(len(call_ids), profile.call_depth),
        analyzed_quarters=min(len(parsed_quarters), profile.quarter_depth),
        analyzed_annual_reports=min(len(parsed_annuals), requested_annuals),
        analyzed_calls=min(len(call_ids), profile.call_depth),
        selected_event_ids=[event_identifier(item) for item in selected],
        adaptive_deepening_reasons=deepening_reasons,
        filing_accessions=[
            item.accession
            for item in (quarter_filings[: profile.quarter_depth] + annual_filings[:requested_annuals])
        ],
        call_document_ids=call_ids[: profile.call_depth],
        trend_summaries=list(historical_trend_summaries or _trend_summaries(selected)),
    )
    if pack.analyzed_quarters < profile.quarter_depth:
        pack.data_gaps.append(
            f"Quarterly history parsed {pack.analyzed_quarters}/{profile.quarter_depth} requested periods "
            f"({pack.discovered_quarters} discovered)."
        )
    if pack.analyzed_annual_reports < requested_annuals:
        pack.data_gaps.append(
            f"Annual history parsed {pack.analyzed_annual_reports}/{requested_annuals} requested reports "
            f"({pack.discovered_annual_reports} discovered)."
        )
    if pack.analyzed_calls < profile.call_depth:
        pack.data_gaps.append(
            f"Call history covers {pack.analyzed_calls}/{profile.call_depth} requested calls."
        )
    if profile.event_scoped and not selected:
        pack.status = "Event unavailable"
        pack.data_gaps.append("The selected event identifier was not found in this run.")
    elif pack.data_gaps:
        pack.status = "Partial"
    return pack


def profile_manifest_payload(profile: ResearchProfile, pack: HistoricalResearchPack) -> tuple[dict, dict]:
    return asdict(profile), {
        "requested": {
            "quarters": pack.requested_quarters,
            "annual_reports": pack.requested_annual_reports,
            "calls": pack.requested_calls,
        },
        "analyzed": {
            "quarters": pack.analyzed_quarters,
            "annual_reports": pack.analyzed_annual_reports,
            "calls": pack.analyzed_calls,
        },
        "selected_event_ids": list(pack.selected_event_ids),
        "adaptive_deepening_reasons": list(pack.adaptive_deepening_reasons),
    }


def _adaptive_deepening_reasons(events: list[ChangeEvent]) -> list[str]:
    reasons: list[str] = []
    triggers = {
        "acquisition": "acquisition or goodwill",
        "goodwill": "acquisition or goodwill",
        "restructur": "restructuring",
        "segment": "segment change",
        "debt": "debt or refinancing",
        "regulat": "regulation",
        "promise": "management promise",
        "credibility": "management promise",
    }
    for event in events:
        text = f"{event.category} {event.title} {event.summary}".lower()
        for token, label in triggers.items():
            if token in text and label not in reasons:
                reasons.append(label)
    return reasons


def _trend_summaries(events: list[ChangeEvent]) -> list[str]:
    return [
        f"{item.title} ({item.event_date or 'date unknown'}): {item.summary}"
        for item in events[:5]
    ]
