from __future__ import annotations

from datetime import date

from .adr_profiles import adr_profile_for
from .models import (
    CompanyIdentity,
    EntityResolution,
    FilingRecord,
    FinancialCoverage,
    FinancialMetric,
)


PERIODIC_FORMS = {"10-K", "10-Q", "20-F", "40-F"}
REGISTRATION_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A", "424B4"}
KNOWN_SIMILAR_TICKERS = {
    "SPCX": ["SPXC"],
    "SPXC": ["SPCX"],
}


def resolve_entity(
    identity: CompanyIdentity,
    submissions: dict,
    filings: list[FilingRecord],
) -> EntityResolution:
    forms = sorted({filing.form for filing in filings})
    adr_profile = adr_profile_for(identity.ticker, forms)
    exchanges = [str(value) for value in submissions.get("exchanges", []) if value]
    exchange = adr_profile.home_exchange if adr_profile and adr_profile.home_exchange != "Unknown" else (
        exchanges[0] if exchanges else identity.exchange
    )
    has_periodic = any(form in PERIODIC_FORMS for form in forms)
    has_registration = any(form in REGISTRATION_FORMS for form in forms)
    foreign_reporter = any(form in {"20-F", "40-F", "6-K"} for form in forms)
    if foreign_reporter:
        listing_status = "US-listed ADR or foreign private issuer"
    elif has_registration and not has_periodic:
        listing_status = "Registration-stage or newly listed issuer"
    elif has_periodic:
        listing_status = "Active US reporting issuer"
    else:
        listing_status = "Reporting history not established"

    similar = KNOWN_SIMILAR_TICKERS.get(identity.ticker.upper(), [])
    warning = None
    if similar:
        warning = (
            f"Confirm the entity before research: {identity.ticker.upper()} is distinct from "
            f"{', '.join(similar)}."
        )
    return EntityResolution(
        ticker=identity.ticker.upper(),
        name=identity.name,
        cik=identity.cik,
        exchange=exchange,
        sic=identity.sic,
        sic_description=identity.sic_description,
        listing_status=listing_status,
        reporting_forms=forms,
        adr_ratio=adr_profile.ordinary_share_ratio if adr_profile else 1.0,
        similar_tickers=similar,
        warning=warning,
    )


def assess_financial_coverage(
    metrics: list[FinancialMetric],
    company_facts: dict | None,
    filings: list[FilingRecord],
    registration_filings: list[FilingRecord],
    provider_error: str | None = None,
    registration_attempts: list[str] | None = None,
) -> FinancialCoverage:
    periodic = sorted({item.form for item in filings if item.form in PERIODIC_FORMS})
    registration = sorted({item.form for item in registration_filings})
    concepts = _company_fact_concepts(company_facts)
    attempted = list(registration_attempts or [])
    if metrics:
        registration_metrics = any(item.source_kind == "registration_inline_xbrl" for item in metrics)
        source = "registration_inline_xbrl" if registration_metrics else "SEC companyfacts"
        reason = (
            "Structured financial metrics were extracted from a registration statement."
            if registration_metrics
            else "Structured financial metrics were extracted from SEC Company Facts."
        )
        return FinancialCoverage(
            "available", reason, source, periodic, registration, concepts,
            len(metrics), attempted,
        )
    if provider_error:
        return FinancialCoverage(
            "provider_failed",
            f"SEC Company Facts could not be retrieved: {provider_error}",
            "SEC",
            periodic,
            registration,
            concepts,
            0,
            attempted,
            ["Retry the SEC request; filing research remains available."],
        )
    if concepts:
        return FinancialCoverage(
            "facts_unmapped",
            "SEC Company Facts responded, but its concepts do not map to the supported operating metrics.",
            "SEC companyfacts",
            periodic,
            registration,
            concepts,
            0,
            attempted,
            ["Filing-fee and issuer-specific concepts are not treated as operating financials."],
        )
    if registration and not periodic:
        return FinancialCoverage(
            "registration_only",
            "The issuer has registration filings but no supported periodic XBRL history; no tagged operating metrics were found.",
            "SEC registration filings",
            periodic,
            registration,
            concepts,
            0,
            attempted,
            ["Untagged filing tables are not converted into financial facts."],
        )
    if periodic and not concepts:
        return FinancialCoverage(
            "no_periodic_xbrl",
            "Periodic filings exist, but SEC Company Facts contains no usable taxonomy concepts.",
            "SEC companyfacts",
            periodic,
            registration,
            concepts,
            0,
            attempted,
        )
    return FinancialCoverage(
        "unsupported_entity",
        "No supported periodic, registration, or structured Company Facts coverage was found.",
        "SEC",
        periodic,
        registration,
        concepts,
        0,
        attempted,
    )


def _company_fact_concepts(company_facts: dict | None) -> list[str]:
    concepts: set[str] = set()
    for taxonomy in (company_facts or {}).get("facts", {}).values():
        if isinstance(taxonomy, dict):
            concepts.update(str(name) for name in taxonomy)
    return sorted(concepts)
