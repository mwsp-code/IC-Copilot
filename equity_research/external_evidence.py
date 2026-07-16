from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from . import config
from .adr_profiles import adr_profile_for
from .models import (
    ChangeEvent,
    Citation,
    CompanyIdentity,
    ExternalEvidence,
    ExternalEvidenceBundle,
    ProviderStatus,
)
from .wisburg_intelligence import extract_wisburg_report, listing_only_report


JsonFetcher = Callable[[str, int], Any]
BytesFetcher = Callable[[str, int], bytes]


class ExternalEvidenceProvider(Protocol):
    provider_name: str

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        ...


@dataclass(frozen=True)
class MacroSeries:
    series_id: str
    label: str
    tag: str
    unit: str
    frequency: str


@dataclass(frozen=True)
class MacroPoint:
    value: float
    source_as_of: str
    previous_value: float | None = None
    previous_as_of: str | None = None
    release_date: str | None = None
    vintage_date: str | None = None
    source_url: str | None = None


FRED_SERIES = (
    MacroSeries("DGS10", "10-year Treasury yield", "rates", "percent", "daily"),
    MacroSeries("T10YIE", "10-year breakeven inflation", "inflation", "percent", "daily"),
    MacroSeries("BAA10Y", "BAA corporate spread over 10-year Treasury", "credit", "percent", "daily"),
    MacroSeries("DTWEXBGS", "Trade-weighted US dollar", "fx", "index", "daily"),
)

BLS_SERIES = (
    MacroSeries("CUUR0000SA0", "US CPI all urban consumers", "inflation", "index", "monthly"),
    MacroSeries("CES0500000003", "Average hourly earnings, private employees", "wages", "USD/hour", "monthly"),
    MacroSeries("LNS14000000", "US unemployment rate", "labor", "percent", "monthly"),
)

MACRO_EQUITY_DRIVER_MAP = {
    "rates": "discount-rate pressure and valuation multiples",
    "inflation": "input-cost pressure, pricing power, and rates",
    "credit": "financing conditions and risk appetite",
    "fx": "translation, import costs, and ADR currency sensitivity",
    "wages": "margin pressure and consumer income",
    "labor": "demand and wage-cycle context",
    "growth": "top-line demand and operating leverage",
    "demand": "end-market demand",
    "liquidity": "funding and risk appetite",
    "financial_stress": "risk appetite and funding stress",
}

MACRO_PROVIDER_NAMES = {
    "FRED/ALFRED macro",
    "BLS macro",
    "BEA macro",
    "Census macro",
    "Treasury macro",
    "OFR macro",
    "World Bank macro",
    "IMF macro",
}

KEN_FRENCH_DAILY_FACTORS_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
KEN_FRENCH_MOMENTUM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
KEN_FRENCH_REVERSAL_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_ST_Reversal_Factor_daily_CSV.zip"
WISBURG_MCP_ENDPOINT = "https://mcp.wisburg.com/mcp"
WISBURG_PROTOCOL_VERSION = "2025-03-26"
WISBURG_TOOL_CONFIG = {
    "list-company-reports": ("company", "external_analyst_context", 3, "company report"),
    "list-institutional-reports": ("ib", "external_analyst_context", 3, "institutional report"),
    "list-am-reports": ("am", "external_analyst_context", 3, "asset management report"),
    "list-archive-reports": ("archive", "external_analyst_context", 3, "public institutional document"),
    "list-earning-calls": ("ec", "management_transcript_context", 3, "earnings call summary"),
    "list-feed": ("feed", "external_analyst_context", 4, "research feed item"),
    "list-articles": ("article", "external_analyst_context", 4, "research article"),
    "list-market-daily": ("market_daily", "external_market_context", 4, "market daily"),
}
WISBURG_QUERY_ALIASES = {
    "BABA": ("BABA US", "BABA.N", "9988 HK", "阿里巴巴", "Alibaba Group"),
    "JD": ("JD US", "9618 HK", "京东", "JD.com"),
    "PDD": ("PDD US", "拼多多", "Pinduoduo", "Temu"),
    "BIDU": ("BIDU US", "9888 HK", "百度", "Baidu"),
    "NTES": ("NTES US", "9999 HK", "网易", "NetEase"),
    "TCOM": ("TCOM US", "9961 HK", "携程", "Trip.com"),
}


class ExternalEvidenceStack:
    provider_name = "External evidence stack"

    def __init__(
        self,
        providers: list[ExternalEvidenceProvider] | None = None,
        store: Any | None = None,
        use_cache: bool = True,
        refresh_cache: bool = False,
    ) -> None:
        self.store = store
        self.use_cache = use_cache
        self.refresh_cache = refresh_cache
        self.providers = providers or [
            SecOwnershipEvidenceProvider(),
            KenFrenchFactorProvider(),
            FredMacroProvider(),
            BlsMacroProvider(),
            BeaMacroProvider(),
            CensusMacroProvider(),
            TreasuryMacroProvider(),
            OfrMacroProvider(),
            WorldBankMacroProvider(),
            ImfMacroProvider(),
            GdeltNarrativeProvider(),
            FinraShortSaleProvider(),
            SecFailsToDeliverProvider(),
            CftcCotProvider(),
            OptionsExpectationPlaceholder(),
            WisburgEvidenceProvider(),
            PaidMarketDataPlaceholder(),
        ]

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        if not self.providers:
            return ExternalEvidenceBundle(identity.ticker.upper(), "Unavailable", [], [], [])

        def fetch_one(provider: ExternalEvidenceProvider) -> ExternalEvidenceBundle:
            if self.store and self.use_cache and not self.refresh_cache and _is_macro_provider(provider):
                cached = self.store.cached_macro_evidence(identity.ticker, provider.provider_name)
                if cached:
                    return cached
            try:
                package = provider.fetch(identity, events)
            except Exception as exc:  # pragma: no cover - provider boundary
                observed_at = _utc_now()
                package = _bundle(
                    identity.ticker, provider.provider_name, "Unavailable", False,
                    observed_at, f"{provider.provider_name} failed: {exc}", "provider_error",
                )
            if self.store and package.evidence and _is_macro_provider(provider):
                try:
                    self.store.save_external_evidence(package)
                except Exception:
                    pass
            return package

        packages: dict[int, ExternalEvidenceBundle] = {}
        workers = min(config.EXTERNAL_EVIDENCE_WORKERS, len(self.providers))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="external-evidence") as executor:
            futures = {
                executor.submit(fetch_one, provider): index
                for index, provider in enumerate(self.providers)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    packages[index] = future.result()
                except Exception as exc:  # pragma: no cover - defensive boundary
                    provider = self.providers[index]
                    packages[index] = _bundle(
                        identity.ticker, provider.provider_name, "Unavailable", False,
                        _utc_now(), f"{provider.provider_name} failed: {exc}", "provider_error",
                    )

        evidence: list[ExternalEvidence] = []
        statuses: list[ProviderStatus] = []
        gaps: list[str] = []
        for index in range(len(self.providers)):
            package = packages[index]
            evidence.extend(package.evidence)
            statuses.extend(package.provider_statuses)
            gaps.extend(package.data_gaps)
        status = "Available" if evidence else "Unavailable"
        if evidence and any(gap for gap in gaps):
            status = "Partial"
        return ExternalEvidenceBundle(identity.ticker.upper(), status, evidence, statuses, gaps)


def external_evidence_stack_from_config(
    *,
    fred_api_key: str | None = None,
    enable_fred: bool | None = None,
    bls_api_key: str | None = None,
    enable_bls: bool | None = None,
    bea_api_key: str | None = None,
    enable_bea: bool | None = None,
    enable_census: bool | None = None,
    enable_treasury: bool | None = None,
    enable_ofr: bool | None = None,
    enable_world_bank: bool | None = None,
    enable_imf: bool | None = None,
    enable_gdelt: bool | None = None,
    wisburg_api_key: str | None = None,
    enable_wisburg: bool | None = None,
    census_api_key: str | None = None,
    enable_default_macro: bool | None = None,
    global_macro_mode: bool | None = None,
    store: Any | None = None,
    use_cache: bool = True,
    refresh_cache: bool = False,
) -> ExternalEvidenceStack:
    return ExternalEvidenceStack([
        SecOwnershipEvidenceProvider(),
        KenFrenchFactorProvider(),
        FredMacroProvider(api_key=fred_api_key, enabled=enable_fred, enable_default_macro=enable_default_macro),
        BlsMacroProvider(api_key=bls_api_key, enabled=enable_bls, enable_default_macro=enable_default_macro),
        BeaMacroProvider(api_key=bea_api_key, enabled=enable_bea, enable_default_macro=enable_default_macro),
        CensusMacroProvider(api_key=census_api_key, enabled=enable_census, enable_default_macro=enable_default_macro),
        TreasuryMacroProvider(enabled=enable_treasury, enable_default_macro=enable_default_macro),
        OfrMacroProvider(enabled=enable_ofr, enable_default_macro=enable_default_macro),
        WorldBankMacroProvider(enabled=enable_world_bank, enable_default_macro=enable_default_macro, global_macro_mode=global_macro_mode),
        ImfMacroProvider(enabled=enable_imf, enable_default_macro=enable_default_macro, global_macro_mode=global_macro_mode),
        GdeltNarrativeProvider(enabled=enable_gdelt),
        FinraShortSaleProvider(),
        SecFailsToDeliverProvider(),
        CftcCotProvider(),
        OptionsExpectationPlaceholder(),
        WisburgEvidenceProvider(api_key=wisburg_api_key, enabled=enable_wisburg),
        PaidMarketDataPlaceholder(),
    ], store=store, use_cache=use_cache, refresh_cache=refresh_cache)


def default_macro_source_settings(
    ticker: str,
    *,
    fred_api_key: str | None = None,
    bea_api_key: str | None = None,
    census_api_key: str | None = None,
    enable_default_macro: bool | None = None,
    global_macro_mode: bool | None = None,
) -> dict[str, bool]:
    default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
    global_mode = config.GLOBAL_MACRO_MODE if global_macro_mode is None else global_macro_mode
    is_adr = adr_profile_for(ticker) is not None
    return {
        "fred": _default_enabled(config.FRED_MACRO_OVERRIDE, default_macro and bool(fred_api_key if fred_api_key is not None else config.FRED_API_KEY)),
        "bls": _default_enabled(config.BLS_MACRO_OVERRIDE, default_macro),
        "bea": _default_enabled(config.BEA_MACRO_OVERRIDE, default_macro and bool(bea_api_key if bea_api_key is not None else config.BEA_API_KEY)),
        "census": _default_enabled(config.CENSUS_MACRO_OVERRIDE, default_macro and bool(census_api_key if census_api_key is not None else config.CENSUS_API_KEY)),
        "treasury": _default_enabled(config.TREASURY_MACRO_OVERRIDE, default_macro),
        "ofr": _default_enabled(config.OFR_MACRO_OVERRIDE, False),
        "world_bank": _default_enabled(config.WORLD_BANK_MACRO_OVERRIDE, default_macro and (global_mode or is_adr)),
        "imf": _default_enabled(config.IMF_MACRO_OVERRIDE, default_macro and (global_mode or is_adr)),
        "gdelt": config.ENABLE_GDELT,
    }


class KenFrenchFactorProvider:
    provider_name = "Ken French factors"

    def __init__(
        self,
        enabled: bool | None = None,
        timeout_seconds: int | None = None,
        fetch_bytes: BytesFetcher | None = None,
    ) -> None:
        self.enabled = True if enabled is None else enabled
        self.timeout_seconds = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 20)
        self.fetch_bytes = fetch_bytes or _fetch_bytes

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", False, observed_at, "Ken French factor context is disabled.", "disabled")
        event_date = _earliest_event_date(events) or date.today().isoformat()
        try:
            factor_values = self._latest_factor_values(event_date)
        except Exception as exc:  # pragma: no cover - endpoint boundary
            return _bundle(identity.ticker, self.provider_name, "Unavailable", False, observed_at, f"Ken French factor download failed: {exc}", "provider_error")
        if not factor_values:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", False, observed_at, "Ken French returned no factor row at or before the event date.", "no_data")
        evidence = [
            ExternalEvidence(
                provider=self.provider_name,
                source_type="factor_return",
                title=f"{name} daily factor return",
                summary=(
                    f"{name} daily factor return was {value:+.2f}% as of {as_of}. "
                    "This is style-factor context; contribution requires pre-event factor beta."
                ),
                observed_at=observed_at,
                source_as_of=as_of,
                source_tier=3,
                official=False,
                confidence="Low",
                metric_name=name,
                metric_value=value,
                unit="percent",
                frequency="daily",
                release_date=as_of,
                vintage_date=as_of,
                lookahead_safe=as_of <= event_date[:10],
                direction="positive" if value > 0 else "negative" if value < 0 else "neutral",
                event_date=event_date,
                citation=Citation(
                    source="Kenneth French Data Library",
                    url=source_url,
                    filed=as_of,
                    section="Daily factors",
                    snippet=f"{name}: {value:+.2f}%",
                    source_tier=3,
                ),
                tags=["factor", name.lower().replace("-", "_")],
            )
            for name, value, as_of, source_url in factor_values
        ]
        return ExternalEvidenceBundle(
            identity.ticker.upper(),
            "Available",
            evidence,
            [_status(self.provider_name, "Available", False, observed_at, f"{len(evidence)} factor-return item(s) available.")],
            [],
        )

    def _latest_factor_values(self, event_date: str) -> list[tuple[str, float, str, str]]:
        rows = []
        rows.extend(self._latest_rows_from_zip(KEN_FRENCH_DAILY_FACTORS_URL, event_date, {
            "Mkt-RF": "Market excess return",
            "SMB": "Size",
            "HML": "Value",
            "RMW": "Profitability",
            "CMA": "Investment",
        }))
        rows.extend(self._latest_rows_from_zip(KEN_FRENCH_MOMENTUM_URL, event_date, {"Mom": "Momentum"}))
        rows.extend(self._latest_rows_from_zip(KEN_FRENCH_REVERSAL_URL, event_date, {"ST_Rev": "Short-term reversal"}))
        return rows

    def _latest_rows_from_zip(
        self,
        url: str,
        event_date: str,
        column_labels: dict[str, str],
    ) -> list[tuple[str, float, str, str]]:
        payload = self.fetch_bytes(url, self.timeout_seconds)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            name = archive.namelist()[0]
            text = archive.read(name).decode("utf-8", errors="replace")
        rows = _parse_ken_french_csv(text, event_date, column_labels)
        return [(label, value, as_of, url) for label, value, as_of in rows]


class FredMacroProvider:
    provider_name = "FRED/ALFRED macro"

    def __init__(
        self,
        api_key: str | None = None,
        enabled: bool | None = None,
        enable_default_macro: bool | None = None,
        timeout_seconds: int | None = None,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.FRED_API_KEY
        default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
        self.enabled = _default_enabled(config.FRED_MACRO_OVERRIDE, default_macro and bool(self.api_key)) if enabled is None else enabled
        self.timeout_seconds = timeout_seconds or config.REQUEST_TIMEOUT_SECONDS
        self.fetch_json = fetch_json or _fetch_json

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", True,
                observed_at, "FRED/ALFRED macro context is disabled.", "disabled",
            )
        if not self.api_key:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", True,
                observed_at, "FRED_API_KEY is not configured.", "missing_key",
            )
        event_date = _earliest_event_date(events) or date.today().isoformat()
        start = _safe_date(event_date) - timedelta(days=45)
        evidence: list[ExternalEvidence] = []
        statuses: list[ProviderStatus] = []
        gaps: list[str] = []
        for series in FRED_SERIES:
            try:
                point = self._series_point(series.series_id, start.isoformat(), event_date)
            except Exception as exc:  # pragma: no cover - network boundary
                gaps.append(f"{series.series_id}: {exc}")
                statuses.append(_status(self.provider_name, "Unavailable", True, observed_at, str(exc), "provider_error"))
                continue
            if point is None:
                gaps.append(f"{series.series_id}: no observation at or before {event_date}.")
                continue
            evidence.append(_macro_evidence_item(identity, self.provider_name, "FRED", series, point, observed_at, event_date))
        if evidence:
            statuses.append(_status(self.provider_name, "Available", True, observed_at, "FRED/ALFRED macro context available."))
        return ExternalEvidenceBundle(identity.ticker.upper(), "Available" if evidence else "Unavailable", evidence, statuses, gaps)

    def _series_point(self, series_id: str, start_date: str, end_date: str) -> MacroPoint | None:
        params = urlencode({
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": start_date,
            "observation_end": end_date,
            "sort_order": "asc",
        })
        url = f"https://api.stlouisfed.org/fred/series/observations?{params}"
        payload = self.fetch_json(url, self.timeout_seconds)
        rows = [
            (parsed, row["date"])
            for row in payload.get("observations", [])
            if (parsed := _parse_numeric(row.get("value"))) is not None
        ]
        if not rows:
            return None
        current = rows[-1]
        previous = rows[0] if len(rows) > 1 else None
        return MacroPoint(
            value=current[0],
            source_as_of=current[1],
            previous_value=previous[0] if previous else None,
            previous_as_of=previous[1] if previous else None,
            release_date=current[1],
            vintage_date=current[1],
            source_url=f"https://fred.stlouisfed.org/series/{series_id}",
        )


class BlsMacroProvider:
    provider_name = "BLS macro"

    def __init__(
        self,
        api_key: str | None = None,
        enabled: bool | None = None,
        enable_default_macro: bool | None = None,
        timeout_seconds: int | None = None,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.BLS_API_KEY
        default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
        self.enabled = _default_enabled(config.BLS_MACRO_OVERRIDE, default_macro) if enabled is None else enabled
        self.timeout_seconds = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 20)
        self.fetch_json = fetch_json or _fetch_json

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "BLS macro context is disabled.", "disabled")
        event_date = _earliest_event_date(events) or date.today().isoformat()
        event = _safe_date(event_date)
        evidence: list[ExternalEvidence] = []
        gaps: list[str] = []
        for series in BLS_SERIES:
            try:
                point = self._series_point(series.series_id, event.year - 1, event.year, event_date)
            except Exception as exc:  # pragma: no cover - network boundary
                gaps.append(f"{series.series_id}: {exc}")
                continue
            if not point:
                gaps.append(f"{series.series_id}: no observation at or before {event_date}.")
                continue
            evidence.append(_macro_evidence_item(identity, self.provider_name, "BLS", series, point, observed_at, event_date))
        status = "Available" if evidence else "Unavailable"
        statuses = [_status(self.provider_name, status, True, observed_at, "BLS macro context available." if evidence else "BLS returned no usable macro rows.")]
        return ExternalEvidenceBundle(identity.ticker.upper(), status, evidence, statuses, gaps)

    def _series_point(self, series_id: str, start_year: int, end_year: int, event_date: str) -> MacroPoint | None:
        params = {"startyear": str(start_year), "endyear": str(end_year)}
        if self.api_key:
            params["registrationkey"] = self.api_key
        url = f"https://api.bls.gov/publicAPI/v2/timeseries/data/{quote(series_id)}?{urlencode(params)}"
        payload = self.fetch_json(url, self.timeout_seconds)
        data = (((payload.get("Results") or {}).get("series") or [{}])[0].get("data") or [])
        rows: list[tuple[float, str]] = []
        cutoff = _safe_date(event_date)
        for row in data:
            period = str(row.get("period") or "")
            if not period.startswith("M") or period == "M13":
                continue
            as_of = _month_date(int(row["year"]), int(period[1:]))
            if as_of <= cutoff and row.get("value") not in (None, ""):
                value = _parse_numeric(row.get("value"))
                if value is None:
                    continue
                rows.append((value, as_of.isoformat()))
        rows.sort(key=lambda item: item[1])
        if not rows:
            return None
        current = rows[-1]
        previous = rows[0] if len(rows) > 1 else None
        return MacroPoint(
            value=current[0],
            source_as_of=current[1],
            previous_value=previous[0] if previous else None,
            previous_as_of=previous[1] if previous else None,
            release_date=current[1],
            vintage_date=current[1],
            source_url=url,
        )


class BeaMacroProvider:
    provider_name = "BEA macro"

    def __init__(
        self,
        api_key: str | None = None,
        enabled: bool | None = None,
        enable_default_macro: bool | None = None,
        timeout_seconds: int | None = None,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.BEA_API_KEY
        default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
        self.enabled = _default_enabled(config.BEA_MACRO_OVERRIDE, default_macro and bool(self.api_key)) if enabled is None else enabled
        self.timeout_seconds = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 20)
        self.fetch_json = fetch_json or _fetch_json

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "BEA macro context is disabled.", "disabled")
        if not self.api_key:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "BEA_API_KEY is not configured.", "missing_key")
        event_date = _earliest_event_date(events) or date.today().isoformat()
        series = MacroSeries("NIPA_T10101_L1", "US GDP level", "growth", "billions USD", "quarterly")
        try:
            point = self._gdp_point(event_date)
        except Exception as exc:  # pragma: no cover - network boundary
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, f"BEA request failed: {exc}", "provider_error")
        if not point:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "BEA returned no GDP row at or before the event date.", "no_data")
        evidence = [_macro_evidence_item(identity, self.provider_name, "BEA", series, point, observed_at, event_date)]
        return ExternalEvidenceBundle(identity.ticker.upper(), "Available", evidence, [_status(self.provider_name, "Available", True, observed_at, "BEA macro context available.")], [])

    def _gdp_point(self, event_date: str) -> MacroPoint | None:
        event = _safe_date(event_date)
        years = ",".join(str(year) for year in range(event.year - 2, event.year + 1))
        params = urlencode({
            "UserID": self.api_key,
            "method": "GetData",
            "datasetname": "NIPA",
            "TableName": "T10101",
            "LineNumber": "1",
            "Frequency": "Q",
            "Year": years,
            "ResultFormat": "JSON",
        })
        url = f"https://apps.bea.gov/api/data?{params}"
        payload = self.fetch_json(url, self.timeout_seconds)
        rows: list[tuple[float, str]] = []
        for row in ((payload.get("BEAAPI") or {}).get("Results") or {}).get("Data", []):
            period = row.get("TimePeriod")
            if not period or row.get("DataValue") in (None, ""):
                continue
            as_of = _quarter_end(str(period))
            if as_of and as_of <= event:
                value = _parse_numeric(row.get("DataValue"))
                if value is None:
                    continue
                rows.append((value, as_of.isoformat()))
        rows.sort(key=lambda item: item[1])
        if not rows:
            return None
        current = rows[-1]
        previous = rows[0] if len(rows) > 1 else None
        return MacroPoint(current[0], current[1], previous[0] if previous else None, previous[1] if previous else None, current[1], current[1], url)


class CensusMacroProvider:
    provider_name = "Census macro"

    def __init__(
        self,
        api_key: str | None = None,
        enabled: bool | None = None,
        enable_default_macro: bool | None = None,
        timeout_seconds: int | None = None,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.CENSUS_API_KEY
        default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
        self.enabled = _default_enabled(config.CENSUS_MACRO_OVERRIDE, default_macro and bool(self.api_key)) if enabled is None else enabled
        self.timeout_seconds = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 20)
        self.fetch_json = fetch_json or _fetch_json

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "Census macro context is disabled.", "disabled")
        if not self.api_key:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "CENSUS_API_KEY is not configured.", "missing_key")
        event_date = _earliest_event_date(events) or date.today().isoformat()
        series = MacroSeries("MARTS_44X72", "US retail and food services sales", "demand", "millions USD", "monthly")
        try:
            point = self._retail_sales_point(event_date)
        except Exception as exc:  # pragma: no cover - endpoint boundary
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, f"Census request failed: {exc}", "provider_error")
        if not point:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "Census returned no usable retail-sales row.", "no_data")
        evidence = [_macro_evidence_item(identity, self.provider_name, "Census", series, point, observed_at, event_date)]
        return ExternalEvidenceBundle(identity.ticker.upper(), "Available", evidence, [_status(self.provider_name, "Available", True, observed_at, "Census macro context available.")], [])

    def _retail_sales_point(self, event_date: str) -> MacroPoint | None:
        event = _safe_date(event_date)
        start = f"{event.year - 1}-01"
        params = urlencode({
            "get": "cell_value,time,category_code",
            "time": f"from {start}",
            "category_code": "44X72",
            "seasonally_adj": "yes",
            "key": self.api_key,
        })
        url = f"https://api.census.gov/data/timeseries/eits/marts?{params}"
        payload = self.fetch_json(url, self.timeout_seconds)
        if not isinstance(payload, list) or not payload:
            return None
        header = payload[0]
        rows: list[tuple[float, str]] = []
        for raw in payload[1:]:
            row = dict(zip(header, raw))
            period = row.get("time")
            value = row.get("cell_value")
            if not period or value in (None, ""):
                continue
            as_of = _month_from_text(period)
            if as_of and as_of <= event:
                parsed_value = _parse_numeric(value)
                if parsed_value is None:
                    continue
                rows.append((parsed_value, as_of.isoformat()))
        rows.sort(key=lambda item: item[1])
        if not rows:
            return None
        current = rows[-1]
        previous = rows[0] if len(rows) > 1 else None
        return MacroPoint(current[0], current[1], previous[0] if previous else None, previous[1] if previous else None, current[1], current[1], url)


class TreasuryMacroProvider:
    provider_name = "Treasury macro"

    def __init__(self, enabled: bool | None = None, enable_default_macro: bool | None = None, timeout_seconds: int | None = None, fetch_json: JsonFetcher | None = None) -> None:
        default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
        self.enabled = _default_enabled(config.TREASURY_MACRO_OVERRIDE, default_macro) if enabled is None else enabled
        self.timeout_seconds = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 20)
        self.fetch_json = fetch_json or _fetch_json

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "Treasury/Fiscal Data macro context is disabled.", "disabled")
        event_date = _earliest_event_date(events) or date.today().isoformat()
        series = MacroSeries("AVG_INTEREST_RATE_AMT", "Average interest rate on US Treasury debt", "rates", "percent", "monthly")
        try:
            point = self._interest_rate_point(event_date)
        except Exception as exc:  # pragma: no cover - endpoint boundary
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, f"Treasury Fiscal Data request failed: {exc}", "provider_error")
        if not point:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "Treasury returned no usable interest-rate row.", "no_data")
        evidence = [_macro_evidence_item(identity, self.provider_name, "Treasury Fiscal Data", series, point, observed_at, event_date)]
        return ExternalEvidenceBundle(identity.ticker.upper(), "Available", evidence, [_status(self.provider_name, "Available", True, observed_at, "Treasury macro context available.")], [])

    def _interest_rate_point(self, event_date: str) -> MacroPoint | None:
        url = (
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates"
            f"?filter=record_date:lte:{quote(event_date[:10])}&sort=-record_date&page[size]=35"
        )
        payload = self.fetch_json(url, self.timeout_seconds)
        rows: list[tuple[float, str]] = []
        for row in payload.get("data", []):
            value = row.get("avg_interest_rate_amt")
            as_of = row.get("record_date")
            if value in (None, "") or not as_of:
                continue
            parsed_value = _parse_numeric(value)
            if parsed_value is None:
                continue
            rows.append((parsed_value, str(as_of)[:10]))
        rows.sort(key=lambda item: item[1])
        if not rows:
            return None
        current = rows[-1]
        previous = rows[0] if len(rows) > 1 else None
        return MacroPoint(current[0], current[1], previous[0] if previous else None, previous[1] if previous else None, current[1], current[1], url)


class OfrMacroProvider:
    provider_name = "OFR macro"

    def __init__(self, enabled: bool | None = None, enable_default_macro: bool | None = None) -> None:
        self.enabled = _default_enabled(config.OFR_MACRO_OVERRIDE, False) if enabled is None else enabled

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "OFR financial-stress context is disabled.", "disabled")
        return _bundle(
            identity.ticker, self.provider_name, "Unavailable", True, observed_at,
            "OFR source is enabled, but the public financial-stress time-series mapping is not activated in this prototype.",
            "series_not_mapped",
        )


class WorldBankMacroProvider:
    provider_name = "World Bank macro"

    def __init__(
        self,
        enabled: bool | None = None,
        enable_default_macro: bool | None = None,
        global_macro_mode: bool | None = None,
        timeout_seconds: int | None = None,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        self.enabled = enabled
        self.enable_default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
        self.global_macro_mode = config.GLOBAL_MACRO_MODE if global_macro_mode is None else global_macro_mode
        self.timeout_seconds = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 20)
        self.fetch_json = fetch_json or _fetch_json

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        enabled = self.enabled
        if enabled is None:
            enabled = _default_enabled(
                config.WORLD_BANK_MACRO_OVERRIDE,
                self.enable_default_macro and _global_macro_relevant(identity, events, self.global_macro_mode),
            )
        if not enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "World Bank macro context is disabled or not relevant for this ticker.", "not_relevant")
        event_date = _earliest_event_date(events) or date.today().isoformat()
        series = MacroSeries("NY.GDP.MKTP.KD.ZG", "US real GDP growth", "growth", "percent", "annual")
        try:
            point = self._gdp_growth_point(event_date)
        except Exception as exc:  # pragma: no cover - endpoint boundary
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, f"World Bank request failed: {exc}", "provider_error")
        if not point:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "World Bank returned no GDP-growth row at or before the event date.", "no_data")
        evidence = [_macro_evidence_item(identity, self.provider_name, "World Bank", series, point, observed_at, event_date)]
        return ExternalEvidenceBundle(identity.ticker.upper(), "Available", evidence, [_status(self.provider_name, "Available", True, observed_at, "World Bank macro context available.")], [])

    def _gdp_growth_point(self, event_date: str) -> MacroPoint | None:
        event = _safe_date(event_date)
        url = "https://api.worldbank.org/v2/country/USA/indicator/NY.GDP.MKTP.KD.ZG?format=json&per_page=10"
        payload = self.fetch_json(url, self.timeout_seconds)
        rows: list[tuple[float, str]] = []
        data = payload[1] if isinstance(payload, list) and len(payload) > 1 else []
        for row in data:
            if row.get("value") is None or row.get("date") is None:
                continue
            as_of = date(int(row["date"]), 12, 31)
            if as_of <= event:
                value = _parse_numeric(row.get("value"))
                if value is None:
                    continue
                rows.append((value, as_of.isoformat()))
        rows.sort(key=lambda item: item[1])
        if not rows:
            return None
        current = rows[-1]
        previous = rows[0] if len(rows) > 1 else None
        return MacroPoint(current[0], current[1], previous[0] if previous else None, previous[1] if previous else None, current[1], current[1], url)


class ImfMacroProvider:
    provider_name = "IMF macro"

    def __init__(
        self,
        enabled: bool | None = None,
        enable_default_macro: bool | None = None,
        global_macro_mode: bool | None = None,
    ) -> None:
        self.enabled = enabled
        self.enable_default_macro = config.ENABLE_DEFAULT_MACRO if enable_default_macro is None else enable_default_macro
        self.global_macro_mode = config.GLOBAL_MACRO_MODE if global_macro_mode is None else global_macro_mode

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        enabled = self.enabled
        if enabled is None:
            enabled = _default_enabled(
                config.IMF_MACRO_OVERRIDE,
                self.enable_default_macro and _global_macro_relevant(identity, events, self.global_macro_mode),
            )
        if not enabled:
            return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, "IMF macro context is disabled or not relevant for this ticker.", "not_relevant")
        return _bundle(
            identity.ticker, self.provider_name, "Unavailable", True, observed_at,
            "IMF source is enabled, but no default IMF SDMX series is mapped for equity driver attribution yet.",
            "series_not_mapped",
        )


class SecOwnershipEvidenceProvider:
    provider_name = "SEC ownership evidence"

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        ownership_events = [
            event for event in events
            if event.category in {"ownership_change", "insider_transaction"}
        ]
        evidence = [
            ExternalEvidence(
                provider=self.provider_name,
                source_type="ownership",
                title=event.title,
                summary=event.summary,
                observed_at=observed_at,
                source_as_of=event.event_date,
                source_tier=1,
                official=True,
                confidence="High",
                metric_name=str(event.metrics.get("form") or event.category),
                metric_value=None,
                direction=event.direction,
                event_date=event.event_date,
                citation=event.citations[0] if event.citations else None,
                tags=["ownership", event.category],
                disqualifies_high_conviction=False,
            )
            for event in ownership_events
        ]
        if not evidence:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", True,
                observed_at, "No recent SEC ownership event was detected in the current filing set.", "no_data",
            )
        return ExternalEvidenceBundle(
            identity.ticker.upper(),
            "Available",
            evidence,
            [_status(self.provider_name, "Available", True, observed_at, f"{len(evidence)} ownership evidence item(s) available.")],
            [],
        )


class GdeltNarrativeProvider:
    provider_name = "GDELT narrative"

    def __init__(self, enabled: bool | None = None, timeout_seconds: int | None = None) -> None:
        self.enabled = config.ENABLE_GDELT if enabled is None else enabled
        self.timeout_seconds = timeout_seconds or min(config.REQUEST_TIMEOUT_SECONDS, 12)

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", False,
                observed_at, "GDELT narrative saturation is disabled.", "disabled",
            )
        event_date = _earliest_event_date(events) or date.today().isoformat()
        start = (_safe_date(event_date) - timedelta(days=30)).strftime("%Y%m%d%H%M%S")
        end = _safe_date(event_date).strftime("%Y%m%d%H%M%S")
        query = quote(f'"{identity.name}" OR "{identity.ticker}"')
        url = (
            "https://api.gdeltproject.org/api/v2/doc/doc"
            f"?query={query}&mode=TimelineVolInfo&format=json&STARTDATETIME={start}&ENDDATETIME={end}"
        )
        try:
            with urlopen(Request(url, headers={"User-Agent": config.APP_NAME}), timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", False,
                observed_at, f"GDELT request failed: {exc}", "provider_error",
            )
        timeline = payload.get("timeline") or payload.get("Timeline") or []
        values = [
            float(item.get("value") or item.get("norm") or 0)
            for item in timeline if isinstance(item, dict)
        ]
        score = sum(values) / len(values) if values else None
        if score is None:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", False,
                observed_at, "GDELT returned no timeline volume rows.", "no_data",
            )
        label = "Crowded" if score >= 5 else "Active" if score >= 1 else "Quiet"
        evidence = [ExternalEvidence(
            provider=self.provider_name,
            source_type="narrative_saturation",
            title=f"{identity.ticker} narrative saturation",
            summary=f"GDELT news-volume signal is {label.lower()} for the pre-event window.",
            observed_at=observed_at,
            source_as_of=event_date,
            source_tier=4,
            official=False,
            confidence="Low",
            metric_name="gdelt_average_volume",
            metric_value=score,
            direction="neutral",
            event_date=event_date,
            citation=Citation(
                source="GDELT",
                url=url,
                filed=event_date,
                section="TimelineVolInfo",
                snippet=f"Average pre-event volume score: {score:.2f}",
                source_tier=4,
            ),
            tags=["narrative", label.lower()],
        )]
        return ExternalEvidenceBundle(identity.ticker.upper(), "Available", evidence, [
            _status(self.provider_name, "Available", False, observed_at, "Narrative saturation available.")
        ], [])


class FinraShortSaleProvider:
    provider_name = "FINRA short-sale volume"

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        return _bundle(
            identity.ticker,
            self.provider_name,
            "Unavailable",
            False,
            observed_at,
            "FINRA short-sale volume is registered as a positioning source, but the daily file/query parser is not activated in v1.",
            "series_not_mapped",
        )


class SecFailsToDeliverProvider:
    provider_name = "SEC fails-to-deliver"

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        return _bundle(
            identity.ticker,
            self.provider_name,
            "Unavailable",
            True,
            observed_at,
            "SEC fails-to-deliver is registered as a liquidity source, but settlement-date file selection is not activated in v1.",
            "series_not_mapped",
        )


class CftcCotProvider:
    provider_name = "CFTC COT positioning"

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        return _bundle(
            identity.ticker,
            self.provider_name,
            "Unavailable",
            True,
            observed_at,
            "CFTC COT is registered for macro positioning, but ticker-to-futures mapping is not activated in v1.",
            "series_not_mapped",
        )


class OptionsExpectationPlaceholder:
    provider_name = "Options expectations"

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        return _bundle(
            identity.ticker,
            self.provider_name,
            "Unavailable",
            False,
            observed_at,
            "No options provider is configured; implied move, skew, and volatility signals are unavailable.",
            "missing_key",
        )


class WisburgMcpError(RuntimeError):
    def __init__(self, message: str, entitlement_status: str = "provider_error") -> None:
        super().__init__(message)
        self.entitlement_status = entitlement_status


class WisburgMcpClient:
    def __init__(
        self,
        api_key: str,
        endpoint: str = WISBURG_MCP_ENDPOINT,
        timeout_seconds: int | None = None,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds or config.REQUEST_TIMEOUT_SECONDS
        self.session_id: str | None = None
        self._next_id = 1
        self.initialized = False

    def initialize(self) -> None:
        if self.initialized:
            return
        self.request(
            "initialize",
            {
                "protocolVersion": WISBURG_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "equity-research-radar", "version": config.APP_VERSION},
            },
        )
        try:
            self.notify("notifications/initialized", {})
        except WisburgMcpError:
            pass
        self.initialized = True

    def list_tool(self, tool_name: str, query: str, first: int) -> dict:
        return self.call_tool(tool_name, {"query": query, "first": first})

    def list_tools(self) -> list[dict]:
        self.initialize()
        return list(self.request("tools/list").get("tools", []))

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        self.initialize()
        return self.request("tools/call", {"name": tool_name, "arguments": arguments})

    def get_report_detail(self, report_id: str, category: str) -> dict:
        return self.call_tool("get-report-detail", {"id": int(report_id), "category": category})

    def get_article_detail(self, report_id: str) -> dict:
        return self.call_tool("get-article-detail", {"id": int(report_id)})

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        response = self._post(payload)
        if response.get("error"):
            raise WisburgMcpError(f"MCP error for {method}: {response['error']}")
        return response.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}}, allow_empty=True)

    def _post(self, payload: dict, allow_empty: bool = False) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"{config.APP_NAME}/WisburgProvider",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                body = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {401, 403}:
                raise WisburgMcpError(
                    f"Wisburg rejected authentication with HTTP {exc.code}: {_redact_secret(body[:300], self.api_key)}",
                    "unauthorized",
                ) from exc
            raise WisburgMcpError(
                f"Wisburg returned HTTP {exc.code}: {_redact_secret(body[:300], self.api_key)}",
                "provider_error",
            ) from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise WisburgMcpError(
                f"Wisburg request failed: {_redact_secret(str(exc), self.api_key)}",
                "network_error",
            ) from exc

        if not body.strip() and allow_empty:
            return {}
        return _decode_mcp_body(body)


class WisburgEvidenceProvider:
    provider_name = "Wisburg research"

    def __init__(
        self,
        api_key: str | None = None,
        enabled: bool | None = None,
        client: Any | None = None,
        first: int = 2,
        max_items: int = 10,
        max_detail_items: int = 6,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.WISBURG_API_KEY
        self.enabled = config.ENABLE_WISBURG if enabled is None else enabled
        self.client = client
        self.first = first
        self.max_items = max_items
        self.max_detail_items = max_detail_items

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        if not self.enabled:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", False,
                observed_at, "Wisburg external research is disabled.", "disabled",
            )
        if not self.api_key:
            return _bundle(
                identity.ticker, self.provider_name, "Unavailable", False,
                observed_at, "WISBURG_API_KEY is not configured.", "missing_key",
            )
        client = self.client or WisburgMcpClient(self.api_key)
        evidence: list[ExternalEvidence] = []
        gaps: list[str] = []
        attempted = 0
        detailed = 0
        queries = _wisburg_queries(identity, events)
        tool_catalog: list[dict] = []
        discovery_status = "assumed_legacy_client"
        if hasattr(client, "list_tools"):
            try:
                tool_catalog = client.list_tools()
                discovery_status = "confirmed"
            except Exception as exc:
                gaps.append(f"Wisburg tool discovery failed: {_redact_secret(str(exc), self.api_key)}")
        available_tools = {str(item.get("name") or "") for item in tool_catalog}
        searchable_tools = [
            tool_name for tool_name in WISBURG_TOOL_CONFIG
            if not available_tools or tool_name in available_tools
        ]
        tool_audit: dict[str, dict[str, Any]] = {
            tool_name: {
                "tool_name": tool_name,
                "status": "available" if tool_name in searchable_tools else "not_entitled",
                "source_category": WISBURG_TOOL_CONFIG[tool_name][0],
                "detail_tool": _detail_tool_for_category(WISBURG_TOOL_CONFIG[tool_name][0], available_tools),
                "query_count": 0,
                "item_count": 0,
                "detail_success_count": 0,
                "message": "",
            }
            for tool_name in WISBURG_TOOL_CONFIG
        }
        for tool_name in searchable_tools:
            for query in queries:
                if len(evidence) >= self.max_items:
                    break
                attempted += 1
                tool_audit[tool_name]["query_count"] += 1
                try:
                    result = client.list_tool(tool_name, query, self.first)
                    text = _wisburg_result_text(result)
                    parsed = _parse_wisburg_listing(text, tool_name, identity, observed_at)
                    tool_audit[tool_name]["item_count"] += len(parsed)
                    for item in parsed:
                        report, claims, revisions, detail_gap = _enrich_wisburg_item(
                            client,
                            item,
                            identity,
                            observed_at,
                            available_tools,
                            allow_detail=detailed < self.max_detail_items,
                        )
                        item.metadata["wisburg_report"] = asdict(report)
                        item.metadata["wisburg_claims"] = [asdict(claim) for claim in claims]
                        item.metadata["wisburg_revisions"] = [asdict(revision) for revision in revisions]
                        if report.detail_status == "structured_extract_available":
                            detailed += 1
                            tool_audit[tool_name]["detail_success_count"] += 1
                            item.licensing_policy = "capped_structured_extract_no_full_payload"
                            if report.capped_excerpt:
                                item.summary = report.capped_excerpt[:900]
                        if detail_gap:
                            gaps.append(detail_gap)
                    evidence.extend(_dedupe_wisburg_items(parsed, evidence))
                    if parsed:
                        break
                    if text:
                        gaps.append(f"{tool_name} returned no matching reports for {query}.")
                except WisburgMcpError as exc:
                    if exc.entitlement_status in {"unauthorized", "network_error", "malformed_response"}:
                        if not evidence:
                            return _bundle(
                                identity.ticker, self.provider_name, "Unavailable", False,
                                observed_at, str(exc), exc.entitlement_status,
                            )
                        gaps.append(f"{tool_name} stopped early: {exc}")
                        break
                    gaps.append(f"{tool_name} failed for {query}: {exc}")
                    break
                except Exception as exc:
                    gaps.append(f"{tool_name} failed for {query}: {_redact_secret(str(exc), self.api_key)}")
                    break
            if len(evidence) >= self.max_items:
                break
        evidence = evidence[:self.max_items]
        coverage_audit = {
            "ticker": identity.ticker.upper(),
            "status": "Available" if discovery_status == "confirmed" else "Partial",
            "observed_at": observed_at,
            "endpoint": WISBURG_MCP_ENDPOINT,
            "authentication_status": "authenticated",
            "tool_discovery_status": discovery_status,
            "tools": list(tool_audit.values()),
            "query_variants": queries,
            "total_items": len(evidence),
            "detailed_items": detailed,
            "source_classes_covered": sorted({
                WISBURG_TOOL_CONFIG[name][0]
                for name, row in tool_audit.items() if row["item_count"]
            }),
            "data_gaps": gaps[:8],
            "licensing_policy": "capped_structured_extract_no_full_payload",
        }
        coverage_item = ExternalEvidence(
            provider="Wisburg research",
            source_type="external_provider_coverage",
            title=f"{identity.ticker.upper()} Wisburg entitlement and coverage audit",
            summary=(
                f"Wisburg tool discovery is {discovery_status}; {len(searchable_tools)} search tool(s) are usable, "
                f"{len(evidence)} item(s) were listed, and {detailed} detail(s) were normalized."
            ),
            observed_at=observed_at,
            source_as_of=observed_at[:10],
            source_tier=4,
            official=False,
            confidence="High" if discovery_status == "confirmed" else "Medium",
            licensing_policy="metadata_only",
            tags=["wisburg", "coverage_audit"],
            disqualifies_high_conviction=True,
            metadata={"wisburg_coverage_audit": coverage_audit},
        )
        if evidence:
            research_item_count = len(evidence)
            narrative = _wisburg_narrative_summary(identity, evidence, observed_at)
            evidence.extend([narrative, coverage_item])
            status = "Partial" if gaps else "Available"
            message = (
                f"Wisburg returned {research_item_count} external research item(s) "
                f"from {attempted} search attempt(s)."
            )
            return ExternalEvidenceBundle(
                identity.ticker.upper(),
                status,
                evidence,
                [_status(self.provider_name, status, False, observed_at, message, "available")],
                gaps[:8],
            )
        message = "Wisburg authenticated but returned no relevant external research items."
        if gaps:
            message += " " + "; ".join(gaps[:3])
        return ExternalEvidenceBundle(
            identity.ticker.upper(),
            "Partial",
            [coverage_item],
            [_status(self.provider_name, "Partial", False, observed_at, message, "no_data")],
            gaps[:8] or [message],
        )


class PaidMarketDataPlaceholder:
    provider_name = "Paid market data"

    def fetch(self, identity: CompanyIdentity, events: list[ChangeEvent]) -> ExternalEvidenceBundle:
        observed_at = _utc_now()
        providers = []
        if config.POLYGON_API_KEY:
            providers.append("Polygon/Massive")
        if config.TIINGO_API_KEY:
            providers.append("Tiingo")
        if providers:
            message = f"{', '.join(providers)} keys are configured, but paid adapters are not enabled in this pass."
            entitlement = "configured_not_enabled"
        else:
            message = "No paid market-data provider is configured; free price stack remains active."
            entitlement = "missing_key"
        return _bundle(identity.ticker, self.provider_name, "Unavailable", True, observed_at, message, entitlement)


def _macro_evidence_item(
    identity: CompanyIdentity,
    provider: str,
    source: str,
    series: MacroSeries,
    point: MacroPoint,
    observed_at: str,
    event_date: str,
) -> ExternalEvidence:
    change = point.value - point.previous_value if point.previous_value is not None else None
    direction = _macro_direction(series.tag, change)
    source_url = point.source_url or ""
    lookahead_safe = (
        bool(point.source_as_of)
        and point.source_as_of[:10] <= event_date[:10]
        and (not point.release_date or point.release_date[:10] <= event_date[:10])
    )
    change_text = f", changed {change:+.2f} over the available lookback." if change is not None else "."
    driver_context = MACRO_EQUITY_DRIVER_MAP.get(series.tag, "macro context")
    return ExternalEvidence(
        provider=provider,
        source_type="macro_factor",
        title=series.label,
        summary=f"{series.label} was {point.value:.2f} {series.unit}{change_text} Equity driver context: {driver_context}.",
        observed_at=observed_at,
        source_as_of=point.source_as_of,
        source_tier=2,
        official=True,
        confidence="Medium" if change is not None and lookahead_safe else "Low",
        metric_name=series.series_id,
        metric_value=change,
        unit=series.unit,
        frequency=series.frequency,
        release_date=point.release_date,
        vintage_date=point.vintage_date,
        lookahead_safe=lookahead_safe,
        direction=direction,
        event_date=event_date,
        citation=Citation(
            source=source,
            url=source_url,
            filed=point.source_as_of,
            section=series.label,
            snippet=f"{series.series_id}: {point.value:.2f} {series.unit}",
            source_tier=2,
        ),
        tags=["macro", series.tag],
        disqualifies_high_conviction=False,
    )


def _macro_direction(tag: str, change: float | None) -> str:
    if change is None or abs(change) < 1e-9:
        return "neutral"
    negative_when_rising = {"rates", "inflation", "credit", "fx", "wages", "financial_stress"}
    if tag in negative_when_rising:
        return "negative" if change > 0 else "positive"
    return "positive" if change > 0 else "negative"


def _default_enabled(override: bool | None, default: bool) -> bool:
    return override if override is not None else default


def _is_macro_provider(provider: ExternalEvidenceProvider) -> bool:
    return provider.provider_name in MACRO_PROVIDER_NAMES


def _global_macro_relevant(
    identity: CompanyIdentity,
    events: list[ChangeEvent],
    global_macro_mode: bool,
) -> bool:
    if global_macro_mode or adr_profile_for(identity.ticker) is not None:
        return True
    fpi_forms = {"20-F", "40-F", "6-K"}
    for event in events:
        if str(event.source).upper() in fpi_forms:
            return True
        for citation in event.citations:
            if citation.form and citation.form.upper() in fpi_forms:
                return True
    return False


def _decode_mcp_body(body: str) -> dict:
    stripped = body.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        return json.loads(stripped)
    messages: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        messages.append(json.loads(data))
    if not messages:
        raise WisburgMcpError(f"Unrecognized Wisburg MCP response: {body[:300]}", "malformed_response")
    return messages[-1]


def _wisburg_queries(identity: CompanyIdentity, events: list[ChangeEvent] | None = None) -> list[str]:
    ticker = identity.ticker.upper()
    candidates = [ticker]
    if identity.name:
        candidates.append(identity.name)
    candidates.extend(WISBURG_QUERY_ALIASES.get(ticker, ()))
    for event in sorted(events or [], key=lambda item: item.severity, reverse=True)[:3]:
        driver = str(event.metrics.get("economic_driver") or "").strip()
        if driver and driver.lower() not in {"unmapped", "unknown"}:
            candidates.append(f"{ticker} {driver[:80]}")
    compact: list[str] = []
    for item in candidates:
        clean = " ".join(str(item or "").split())
        if clean and clean not in compact:
            compact.append(clean)
    return compact[:8]


def _wisburg_result_text(result: dict) -> str:
    texts: list[str] = []
    for item in result.get("content", []):
        if item.get("type") == "text":
            texts.append(str(item.get("text") or ""))
    return "\n".join(texts).strip()


def _parse_wisburg_listing(
    text: str,
    tool_name: str,
    identity: CompanyIdentity,
    observed_at: str,
) -> list[ExternalEvidence]:
    if not text or "No " in text[:80]:
        return []
    category, source_type, tier, label = WISBURG_TOOL_CONFIG[tool_name]
    rows = _wisburg_listing_rows(text)
    evidence: list[ExternalEvidence] = []
    for report_id, title, source_as_of, summary in rows:
        if not report_id:
            continue
        clean_summary = _compact_text(summary or title)
        source_language = _detect_source_language(f"{title} {clean_summary}")
        citation = Citation(
            source=f"Wisburg {label} {report_id}",
            url=WISBURG_MCP_ENDPOINT,
            filed=source_as_of,
            section=f"{category}:{report_id}",
            snippet=clean_summary[:700],
            retrieved_at=observed_at,
            source_tier=tier,
        )
        age_days = _source_age_days(source_as_of, observed_at)
        stale = age_days is None or age_days > 180 or age_days < 0
        evidence.append(ExternalEvidence(
            provider="Wisburg research",
            source_type=source_type,
            title=f"{label.title()}: {title}",
            summary=clean_summary[:900] or "Wisburg returned a matching external research item.",
            observed_at=observed_at,
            source_as_of=source_as_of,
            source_tier=tier,
            official=False,
            confidence="Low" if stale else "Medium",
            licensing_policy="metadata_and_excerpt_only",
            citation=citation,
            tags=[
                "wisburg", category, source_language, identity.ticker.upper(),
                "metadata_excerpt_only",
                *(("stale_or_undated",) if stale else ()),
            ],
            disqualifies_high_conviction=True,
        ))
    return evidence


def _detail_tool_for_category(category: str, available_tools: set[str]) -> str | None:
    if category == "article":
        return "get-article-detail" if not available_tools or "get-article-detail" in available_tools else None
    if category in {"company", "ib", "am", "archive", "ec"}:
        return "get-report-detail" if not available_tools or "get-report-detail" in available_tools else None
    return None


def _enrich_wisburg_item(
    client: Any,
    item: ExternalEvidence,
    identity: CompanyIdentity,
    observed_at: str,
    available_tools: set[str],
    *,
    allow_detail: bool,
) -> tuple[Any, list[Any], list[Any], str]:
    section = item.citation.section if item.citation else ""
    category, report_id = section.split(":", 1) if ":" in section else (item.source_type, "")
    report = listing_only_report(
        identity,
        report_id=report_id,
        category=category,
        title=item.title,
        published_at=item.source_as_of,
        observed_at=observed_at,
        source_tier=item.source_tier,
        excerpt=item.summary,
        citation=item.citation,
    )
    detail_tool = _detail_tool_for_category(category, available_tools)
    if not allow_detail or not report_id or not detail_tool:
        return report, [], [], ""
    if detail_tool == "get-report-detail" and not hasattr(client, "get_report_detail"):
        return report, [], [], ""
    if detail_tool == "get-article-detail" and not hasattr(client, "get_article_detail"):
        return report, [], [], ""
    try:
        result = (
            client.get_article_detail(report_id)
            if detail_tool == "get-article-detail"
            else client.get_report_detail(report_id, category)
        )
        text = _wisburg_result_text(result)
        if result.get("isError") or text.lower().startswith("mcp error"):
            report.detail_status = "detail_error"
            return report, [], [], f"Wisburg detail failed for {category}:{report_id}: {text[:240]}"
        detailed_report, claims, revisions = extract_wisburg_report(
            identity,
            report_id=report_id,
            category=category,
            title=item.title,
            published_at=item.source_as_of,
            observed_at=observed_at,
            source_tier=item.source_tier,
            detail_text=text,
            endpoint=WISBURG_MCP_ENDPOINT,
        )
        if detailed_report.citation:
            item.citation = detailed_report.citation
        return detailed_report, claims, revisions, ""
    except Exception as exc:
        report.detail_status = "detail_error"
        return (
            report,
            [],
            [],
            f"Wisburg detail failed for {category}:{report_id}: {_redact_secret(str(exc), getattr(client, 'api_key', ''))}",
        )


def _source_age_days(source_as_of: str | None, observed_at: str) -> int | None:
    if not source_as_of:
        return None
    try:
        source_day = datetime.fromisoformat(source_as_of.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            source_day = date.fromisoformat(source_as_of[:10])
        except ValueError:
            return None
    try:
        observed_day = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).date()
    except ValueError:
        observed_day = date.today()
    return (observed_day - source_day).days


def _wisburg_listing_rows(text: str) -> list[tuple[str, str, str | None, str]]:
    rows: list[tuple[str, str, str | None, str]] = []
    current_id = ""
    current_title = ""
    current_date: str | None = None
    current_summary: list[str] = []
    row_re = re.compile(r"^\[(?P<id>[^\]]+)\]\s*(?P<title>.+?)\s*$")
    date_re = re.compile(r"^\s*date:\s*(?P<date>.+?)\s*$", re.IGNORECASE)

    def flush() -> None:
        if current_id:
            rows.append((current_id, current_title, current_date, _compact_text(" ".join(current_summary))))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("Found ") or line.startswith("--- Page Info"):
            continue
        match = row_re.match(line)
        if match:
            flush()
            current_id = match.group("id").strip()
            current_title = _strip_markdown(match.group("title").strip())
            current_date = None
            current_summary = []
            continue
        date_match = date_re.match(raw_line)
        if date_match and current_id:
            current_date = date_match.group("date").strip()
            continue
        if current_id and "Next cursor:" not in line and "Use \"after\"" not in line:
            current_summary.append(_strip_markdown(line))
    flush()
    return rows


def _dedupe_wisburg_items(
    candidates: list[ExternalEvidence],
    existing: list[ExternalEvidence],
) -> list[ExternalEvidence]:
    seen = {
        (item.citation.section if item.citation else item.title)
        for item in existing
    }
    rows: list[ExternalEvidence] = []
    for item in candidates:
        key = item.citation.section if item.citation else item.title
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def _wisburg_narrative_summary(
    identity: CompanyIdentity,
    items: list[ExternalEvidence],
    observed_at: str,
) -> ExternalEvidence:
    themes = _wisburg_theme_counts(items)
    theme_text = ", ".join(f"{name} ({count})" for name, count in themes[:5]) or "No repeated theme extracted."
    latest_as_of = max((item.source_as_of or "" for item in items), default="") or None
    return ExternalEvidence(
        provider="Wisburg research",
        source_type="narrative_saturation",
        title=f"{identity.ticker.upper()} Wisburg narrative context",
        summary=(
            f"Wisburg found {len(items)} relevant external research item(s). "
            f"Repeated themes: {theme_text}. Treat this as narrative context, not primary evidence."
        ),
        observed_at=observed_at,
        source_as_of=latest_as_of,
        source_tier=4,
        official=False,
        confidence="Low" if len(items) < 3 else "Medium",
        licensing_policy="metadata_and_excerpt_only",
        metric_name="wisburg_research_item_count",
        metric_value=float(len(items)),
        unit="items",
        frequency="run",
        direction="neutral",
        citation=Citation(
            source="Wisburg MCP search",
            url=WISBURG_MCP_ENDPOINT,
            filed=latest_as_of,
            section="aggregate:narrative",
            snippet=theme_text[:700],
            retrieved_at=observed_at,
            source_tier=4,
        ),
        tags=["wisburg", "narrative", identity.ticker.upper()],
        disqualifies_high_conviction=True,
    )


def _wisburg_theme_counts(items: list[ExternalEvidence]) -> list[tuple[str, int]]:
    labels = {
        "AI / cloud": ("ai", "cloud", "云", "人工智能"),
        "commerce demand": ("commerce", "ecommerce", "retail", "电商", "零售"),
        "margin / EBITA": ("margin", "ebita", "profit", "利润", "利润率"),
        "target / rating": ("target", "rating", "目标价", "评级", "买入"),
        "policy / macro": ("policy", "regulation", "macro", "政策", "监管", "宏观"),
        "buyback / capital return": ("buyback", "repurchase", "回购", "股东回报"),
    }
    counts: dict[str, int] = {}
    text = "\n".join(f"{item.title} {item.summary}" for item in items).lower()
    for label, tokens in labels.items():
        count = sum(text.count(token.lower()) for token in tokens)
        if count:
            counts[label] = count
    return sorted(counts.items(), key=lambda pair: pair[1], reverse=True)


def _detect_source_language(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    return "en"


def _strip_markdown(text: str) -> str:
    return re.sub(r"[*_`#>]+", "", text).strip()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_markdown(text)).strip()


def _redact_secret(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text


def _bundle(
    ticker: str,
    provider: str,
    status: str,
    official: bool,
    observed_at: str,
    message: str,
    entitlement_status: str,
) -> ExternalEvidenceBundle:
    return ExternalEvidenceBundle(
        ticker.upper(),
        status,
        [],
        [_status(provider, status, official, observed_at, message, entitlement_status)],
        [message],
    )


def _status(
    provider: str,
    status: str,
    official: bool,
    observed_at: str,
    message: str,
    entitlement_status: str = "available",
) -> ProviderStatus:
    return ProviderStatus(provider, status, official, entitlement_status, observed_at, message)


def _fetch_json(url: str, timeout_seconds: int) -> Any:
    with urlopen(Request(url, headers={"User-Agent": config.APP_NAME}), timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _fetch_bytes(url: str, timeout_seconds: int) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": config.APP_NAME}), timeout=timeout_seconds) as response:
        return response.read()


def _parse_ken_french_csv(
    text: str,
    event_date: str,
    column_labels: dict[str, str],
) -> list[tuple[str, float, str]]:
    lines = text.splitlines()
    header_index = next(
        (
            index for index, line in enumerate(lines)
            if line.strip().startswith(",") and any(column in line for column in column_labels)
        ),
        None,
    )
    if header_index is None:
        return []
    reader = csv.DictReader(lines[header_index:])
    cutoff = _safe_date(event_date)
    latest: dict[str, str] | None = None
    latest_date: date | None = None
    for row in reader:
        raw_date = (row.get("") or row.get("Date") or "").strip()
        if not raw_date or not raw_date.isdigit():
            break
        try:
            as_of = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError:
            continue
        if as_of <= cutoff:
            latest = row
            latest_date = as_of
        elif as_of > cutoff:
            break
    if not latest or not latest_date:
        return []
    parsed: list[tuple[str, float, str]] = []
    for raw_name, label in column_labels.items():
        value = latest.get(raw_name)
        if value in (None, "", "-99.99", "-999"):
            continue
        try:
            parsed.append((label, float(value), latest_date.isoformat()))
        except ValueError:
            continue
    return parsed


def _earliest_event_date(events: list[ChangeEvent]) -> str | None:
    dates = sorted(event.event_date[:10] for event in events if event.event_date)
    return dates[0] if dates else None


def _parse_numeric(value) -> float | None:
    if value in (None, "", ".", "-", "--", "NA", "N/A"):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _safe_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return date.today()


def _month_date(year: int, month: int) -> date:
    return date(year, max(1, min(month, 12)), 1)


def _month_from_text(value: str) -> date | None:
    try:
        year, month = value[:7].split("-")
        return _month_date(int(year), int(month))
    except (ValueError, TypeError):
        return None


def _quarter_end(period: str) -> date | None:
    if "Q" not in period:
        return None
    try:
        year, quarter = period.split("Q", 1)
        month = {"1": 3, "2": 6, "3": 9, "4": 12}[quarter[:1]]
        day = 31 if month in {3, 12} else 30
        return date(int(year), month, day)
    except (KeyError, ValueError):
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
