from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass(frozen=True)
class AdrProfile:
    ticker: str
    home_exchange: str
    ordinary_share_ratio: float
    reporting_currency: str
    fiscal_year_end: str | None
    issuer_ir_sources: tuple[tuple[str, str], ...] = ()
    primary_forms: tuple[str, ...] = ("20-F", "6-K")
    source: str = "built_in"
    segment_drivers: tuple[str, ...] = ()
    benchmark_tickers: tuple[str, ...] = ()
    fx_proxy: str | None = None
    source_priority: tuple[str, ...] = ("20-F", "6-K", "issuer_ir", "presentation")


BUILT_IN_ADR_PROFILES: dict[str, AdrProfile] = {
    "BABA": AdrProfile(
        "BABA", "HKEX", 8.0, "CNY", "03-31",
        (
            ("quarterly_results", "https://www.alibabagroup.com/en-US/ir-financial-reports-quarterly-results"),
            ("sec_hkex_filings", "https://www.alibabagroup.com/en-US/ir-filings-sec"),
            ("annual_general_meetings", "https://www.alibabagroup.com/en-US/ir-annual-general-meetings"),
        ),
        segment_drivers=(
            "China commerce", "Cloud", "International commerce", "Local services",
            "Logistics", "Buybacks", "RMB/USD", "Policy risk",
        ),
        benchmark_tickers=("KWEB", "MCHI", "HSTECH", "CNH"),
        fx_proxy="CNH",
    ),
    "JD": AdrProfile(
        "JD", "HKEX", 2.0, "CNY", "12-31",
        segment_drivers=("China retail", "Marketplace", "Logistics", "Buybacks", "RMB/USD", "Policy risk"),
        benchmark_tickers=("KWEB", "MCHI", "HSTECH", "CNH"),
        fx_proxy="CNH",
    ),
    "PDD": AdrProfile(
        "PDD", "NASDAQ", 4.0, "CNY", "12-31",
        segment_drivers=("Marketplace", "Temu/international commerce", "Advertising take rate", "RMB/USD", "Policy risk"),
        benchmark_tickers=("KWEB", "MCHI", "CNH"),
        fx_proxy="CNH",
    ),
    "BIDU": AdrProfile(
        "BIDU", "HKEX", 8.0, "CNY", "12-31",
        segment_drivers=("Search advertising", "AI cloud", "Autonomous driving", "RMB/USD", "Policy risk"),
        benchmark_tickers=("KWEB", "MCHI", "HSTECH", "CNH"),
        fx_proxy="CNH",
    ),
    "NTES": AdrProfile(
        "NTES", "HKEX", 5.0, "CNY", "12-31",
        segment_drivers=("Online games", "Cloud music", "Education/other", "RMB/USD", "Policy risk"),
        benchmark_tickers=("KWEB", "MCHI", "HSTECH", "CNH"),
        fx_proxy="CNH",
    ),
    "TCOM": AdrProfile(
        "TCOM", "HKEX", 1.0, "CNY", "12-31",
        segment_drivers=("Travel demand", "Accommodation", "Transportation", "Outbound travel", "RMB/USD", "Policy risk"),
        benchmark_tickers=("KWEB", "MCHI", "HSTECH", "CNH"),
        fx_proxy="CNH",
    ),
}


def adr_profile_for(
    ticker: str,
    forms: list[str] | tuple[str, ...] | None = None,
    csv_path: Path | None = None,
) -> AdrProfile | None:
    ticker = ticker.upper()
    csv_profile = _csv_profile(ticker, csv_path or config.ADR_PROFILE_CSV)
    if csv_profile:
        return csv_profile
    if ticker in BUILT_IN_ADR_PROFILES:
        return BUILT_IN_ADR_PROFILES[ticker]
    form_set = {form.upper() for form in (forms or [])}
    if form_set & {"20-F", "40-F", "6-K"}:
        return AdrProfile(
            ticker=ticker,
            home_exchange="Unknown",
            ordinary_share_ratio=1.0,
            reporting_currency="Unknown",
            fiscal_year_end=None,
            primary_forms=tuple(sorted(form_set & {"20-F", "40-F", "6-K"})) or ("20-F", "6-K"),
            source="generic_fpi",
            segment_drivers=("Revenue", "Margin", "Share count", "Home-market demand", "FX", "Local policy risk"),
            benchmark_tickers=("KWEB", "MCHI"),
            fx_proxy=None,
        )
    return None


def issuer_ir_sources_for(ticker: str, csv_path: Path | None = None) -> tuple[tuple[str, str], ...]:
    profile = adr_profile_for(ticker, csv_path=csv_path)
    return profile.issuer_ir_sources if profile else ()


def _csv_profile(ticker: str, csv_path: Path) -> AdrProfile | None:
    if not csv_path.exists():
        return None
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if (row.get("ticker") or "").strip().upper() != ticker:
                    continue
                sources = _parse_sources(row.get("issuer_ir_sources") or "")
                return AdrProfile(
                    ticker=ticker,
                    home_exchange=(row.get("home_exchange") or "Unknown").strip(),
                    ordinary_share_ratio=_float(row.get("ordinary_share_ratio")) or 1.0,
                    reporting_currency=(row.get("reporting_currency") or "Unknown").strip(),
                    fiscal_year_end=(row.get("fiscal_year_end") or "").strip() or None,
                    issuer_ir_sources=sources,
                    primary_forms=tuple(
                        item.strip().upper()
                        for item in (row.get("primary_forms") or "20-F,6-K").split(",")
                        if item.strip()
                    ),
                    source="csv",
                    segment_drivers=tuple(
                        item.strip() for item in (row.get("segment_drivers") or "").split("|") if item.strip()
                    ),
                    benchmark_tickers=tuple(
                        item.strip().upper() for item in (row.get("benchmark_tickers") or "").split("|") if item.strip()
                    ),
                    fx_proxy=(row.get("fx_proxy") or "").strip().upper() or None,
                    source_priority=tuple(
                        item.strip() for item in (row.get("source_priority") or "20-F,6-K,issuer_ir,presentation").split(",") if item.strip()
                    ),
                )
    except OSError:
        return None
    return None


def _parse_sources(value: str) -> tuple[tuple[str, str], ...]:
    sources: list[tuple[str, str]] = []
    for item in value.split("|"):
        if not item.strip():
            continue
        if "=" in item:
            source_type, url = item.split("=", 1)
        else:
            source_type, url = "issuer_ir", item
        if url.strip():
            sources.append((source_type.strip(), url.strip()))
    return tuple(sources)


def _float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
