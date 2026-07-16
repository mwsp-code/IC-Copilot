from __future__ import annotations

import csv
import hashlib
import html
import re
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

from . import config
from .analysis import format_number
from .adr_profiles import AdrProfile, adr_profile_for
from .models import (
    Citation,
    FinancialMetric,
    GlobalPeerCoverage,
    GlobalPeerDocument,
    GlobalPeerIdentity,
    GlobalPeerMetricObservation,
    LlmDocumentTriageResult,
    LlmMetricExtractionDraft,
    LlmResearchAgentManifest,
    LlmTrendAnalysis,
    OfficialDocumentParseStatus,
)


PROMPT_VERSION = "global-peer-research-agent-v1"
MAX_DISCOVERED_DOCUMENTS_PER_SOURCE = 2

ZH_BYD_SIMPLIFIED = "\u6bd4\u4e9a\u8fea"
ZH_BYD_TRADITIONAL = "\u6bd4\u4e9e\u8fea"

HOME_TICKER_OVERRIDES = {
    "BABA": "9988.HK",
    "JD": "9618.HK",
    "BIDU": "9888.HK",
    "NTES": "9999.HK",
    "TCOM": "9961.HK",
    "TCEHY": "0700.HK",
    "BYDDF": "1211.HK",
    "BYDDY": "1211.HK",
}

ISSUER_NAME_OVERRIDES = {
    "BABA": "Alibaba Group Holding Limited",
    "JD": "JD.com, Inc.",
    "BIDU": "Baidu, Inc.",
    "NTES": "NetEase, Inc.",
    "TCOM": "Trip.com Group Limited",
    "TCEHY": "Tencent Holdings Limited",
    "BYDDF": "BYD Company Limited",
    "BYDDY": "BYD Company Limited",
}


BUILT_IN_GLOBAL_PEERS: dict[str, GlobalPeerIdentity] = {
    "BYDDF": GlobalPeerIdentity(
        ticker="BYDDF",
        issuer_name="BYD Company Limited",
        home_ticker="1211.HK",
        home_exchange="HKEX",
        reporting_currency="CNY",
        aliases=["BYDDY", "1211.HK", "002594.SZ", "BYD", ZH_BYD_SIMPLIFIED, ZH_BYD_TRADITIONAL],
        source_priority=["hkex_document", "cninfo_document", "issuer_ir_report"],
        source_urls={
            "hkex_document": "https://www.hkexnews.hk/index.htm",
            "cninfo_document": "https://www.cninfo.com.cn/new/index",
            "issuer_ir_report": "https://www.bydglobal.com/",
        },
    ),
    "BYDDY": GlobalPeerIdentity(
        ticker="BYDDY",
        issuer_name="BYD Company Limited",
        home_ticker="1211.HK",
        home_exchange="HKEX",
        reporting_currency="CNY",
        aliases=["BYDDF", "1211.HK", "002594.SZ", "BYD", ZH_BYD_SIMPLIFIED, ZH_BYD_TRADITIONAL],
        source_priority=["hkex_document", "cninfo_document", "issuer_ir_report"],
        source_urls={
            "hkex_document": "https://www.hkexnews.hk/index.htm",
            "cninfo_document": "https://www.cninfo.com.cn/new/index",
            "issuer_ir_report": "https://www.bydglobal.com/",
        },
    ),
}


METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "Revenue": ("revenue", "total revenue", "\u8425\u4e1a\u6536\u5165", "\u71df\u696d\u6536\u5165"),
    "Gross Profit": (
        "gross profit",
        "gross income",
        "\u6bdb\u5229",
        "\u6bdb\u5229\u6da6",
        "\u6bdb\u5229\u6f64",
    ),
    "Cost of Revenue": ("cost of revenue", "cost of sales", "\u8425\u4e1a\u6210\u672c", "\u71df\u696d\u6210\u672c"),
    "Operating Income": ("operating income", "operating profit", "profit from operations", "\u7ecf\u8425\u5229\u6da6", "\u7d93\u71df\u5229\u6f64"),
    "Net Income": ("net income", "profit attributable to equity holders", "profit attributable", "\u51c0\u5229\u6da6", "\u6de8\u5229\u6f64"),
    "Cash": ("cash and cash equivalents", "cash equivalents", "cash balance", "\u73b0\u91d1\u53ca\u73b0\u91d1\u7b49\u4ef7\u7269", "\u73fe\u91d1\u53ca\u73fe\u91d1\u7b49\u50f9\u7269"),
    "Operating Cash Flow": ("net cash generated from operating activities", "cash generated from operating activities", "operating cash flow", "\u7ecf\u8425\u6d3b\u52a8\u4ea7\u751f\u7684\u73b0\u91d1\u6d41", "\u7d93\u71df\u6d3b\u52d5\u7522\u751f\u7684\u73fe\u91d1\u6d41"),
    "Capital Expenditure": ("capital expenditures", "capital expenditure", "payments for property and equipment", "purchase of property and equipment", "capex", "\u8d44\u672c\u5f00\u652f", "\u8cc7\u672c\u958b\u652f"),
    "Long-term Debt": ("long-term debt", "non-current borrowings", "long term borrowings", "\u957f\u671f\u501f\u6b3e", "\u9577\u671f\u501f\u6b3e"),
    "Current Debt": ("current borrowings", "short-term borrowings", "current portion of long-term debt", "short term debt", "\u77ed\u671f\u501f\u6b3e", "\u77ed\u671f\u501f\u6b3e"),
    "Interest Expense": ("interest expense", "finance costs", "financing costs", "\u5229\u606f\u8d39\u7528", "\u878d\u8d44\u6210\u672c"),
    "Dividends Paid": ("dividends paid", "dividend paid", "payment of dividends", "\u5df2\u4ed8\u80a1\u606f"),
    "Share Repurchases": ("share repurchases", "repurchase of shares", "shares repurchased", "buyback", "\u56de\u8d2d\u80a1\u4efd", "\u56de\u8cfc\u80a1\u4efd"),
    "Deliveries": ("deliveries", "vehicle deliveries", "sales volume", "\u9500\u91cf", "\u92b7\u91cf"),
}


def global_peer_identity_for(
    ticker: str,
    csv_path: Path | None = None,
) -> GlobalPeerIdentity | None:
    symbol = ticker.upper().strip()
    csv_identity = _csv_identity(symbol, csv_path or config.GLOBAL_PEER_PROFILE_CSV)
    if csv_identity:
        return csv_identity
    if symbol in BUILT_IN_GLOBAL_PEERS:
        return BUILT_IN_GLOBAL_PEERS[symbol]
    for identity in BUILT_IN_GLOBAL_PEERS.values():
        if symbol in {alias.upper() for alias in identity.aliases}:
            return identity
    profile_identity = _adr_profile_identity(symbol)
    if profile_identity:
        return profile_identity
    return None


class GlobalPeerFinancialProvider:
    provider_name = "official_global_peer"

    def __init__(
        self,
        *,
        fetch_text: Callable[[str], str] | None = None,
        fixture_documents: dict[str, list[tuple[str, str, str, str]]] | None = None,
        enable_live: bool | None = None,
    ) -> None:
        self.fetch_text = fetch_text or _fetch_text
        self.fixture_documents = fixture_documents or {}
        self.enable_live = config.ENABLE_GLOBAL_PEER_LIVE_EXTRACTION if enable_live is None else enable_live

    def fetch(self, ticker: str) -> GlobalPeerCoverage:
        observed_at = _utc_now()
        identity = global_peer_identity_for(ticker)
        if not identity:
            return GlobalPeerCoverage(
                ticker=ticker.upper(),
                status="unsupported_global_peer",
                observed_at=observed_at,
                data_gaps=[f"No global peer profile is configured for {ticker.upper()}."],
            )

        documents: list[GlobalPeerDocument] = []
        parse_statuses: list[OfficialDocumentParseStatus] = []
        metrics: list[GlobalPeerMetricObservation] = []
        texts = self._document_texts(identity)
        if not texts:
            return GlobalPeerCoverage(
                ticker=ticker.upper(),
                status="official_document_not_found",
                observed_at=observed_at,
                identity=identity,
                parse_statuses=[
                    OfficialDocumentParseStatus(
                        source_type="global_peer_official_document",
                        status="official_document_not_found",
                        message="No registered official document URL was available or reachable.",
                        observed_at=observed_at,
                    )
                ],
                data_gaps=[
                    f"{identity.issuer_name} is mapped to {identity.home_ticker}, but no official document text was fetched."
                ],
            )

        for source_type, title, url, raw_text in texts:
            document_id = _stable_id("global-peer-doc", identity.ticker, source_type, title, url)
            text = _plain_text_preserving_tables(raw_text)
            document = GlobalPeerDocument(
                document_id=document_id,
                peer_ticker=ticker.upper(),
                source_type=source_type,
                title=title,
                url=url,
                published_at=_extract_date(text),
                fiscal_period=_extract_period(text),
                reporting_currency=identity.reporting_currency,
                observed_at=observed_at,
                status="available",
                parse_status="parsed" if text else "table_parse_failed",
                language=_detect_language(text),
                excerpt=text[:900],
            )
            documents.append(document)
            parsed = _parse_metric_observations(identity, ticker.upper(), document, text, observed_at)
            metrics.extend(parsed)
            parse_statuses.append(
                OfficialDocumentParseStatus(
                    source_type=source_type,
                    status="parsed" if parsed else "document_found_table_parse_failed",
                    message=(
                        f"Extracted {len(parsed)} metric observation(s)."
                        if parsed else
                        "Official document text was fetched, but required peer KPI fields were not parsed reliably."
                    ),
                    url=url,
                    observed_at=observed_at,
                    confidence="Medium" if parsed else "Low",
                )
            )

        status = "available" if metrics else "document_found_table_parse_failed"
        gaps = [] if metrics else [
            "Official peer documents were found, but revenue/gross-profit/gross-margin fields were not parsed."
        ]
        return GlobalPeerCoverage(
            ticker=ticker.upper(),
            status=status,
            observed_at=observed_at,
            identity=identity,
            documents=documents,
            metrics=metrics,
            parse_statuses=parse_statuses,
            data_gaps=gaps,
        )

    def research_manifest(self, coverage: GlobalPeerCoverage) -> LlmResearchAgentManifest:
        return LlmResearchAgentManifest(
            provider="deterministic",
            model="none",
            prompt_version=PROMPT_VERSION,
            generated_at=_utc_now(),
            status="Available" if coverage.identity else "Unavailable",
            document_ids=[document.document_id for document in coverage.documents],
            messages=[
                "LLM research-agent lane is available but deterministic official-source fetching performed this run."
            ],
            redacted_config={"llm_used": "false"},
        )

    def trend_analysis(self, coverage: GlobalPeerCoverage) -> LlmTrendAnalysis:
        if not coverage.metrics:
            return LlmTrendAnalysis(
                status="Unavailable",
                summary="No validated global peer metrics were available for trend analysis.",
                data_gaps=list(coverage.data_gaps),
            )
        patterns = [
            f"{item.peer_ticker} {item.metric}: {format_number(item.value)} {item.unit}"
            + (f", {item.yoy_change_pct:+.1f}% YoY" if item.yoy_change_pct is not None else "")
            for item in coverage.metrics[:6]
        ]
        return LlmTrendAnalysis(
            status="Available",
            summary="Validated global peer metrics are available for peer trend context.",
            peer_patterns=patterns,
        )

    def triage_results(self, coverage: GlobalPeerCoverage) -> list[LlmDocumentTriageResult]:
        return [
            LlmDocumentTriageResult(
                document_id=document.document_id,
                status="deterministic",
                relevant_sections=["financial statements", "management discussion"],
                table_hints=["revenue", "gross profit", "cost of revenue"],
                confidence="Medium" if document.parse_status == "parsed" else "Low",
                message="Deterministic triage used; LLM triage can refine table/page selection when enabled.",
            )
            for document in coverage.documents
        ]

    def extraction_drafts(self, coverage: GlobalPeerCoverage) -> list[LlmMetricExtractionDraft]:
        return [
            LlmMetricExtractionDraft(
                draft_id=_stable_id("global-peer-draft", metric.observation_id),
                document_id=metric.source_document_id,
                metric=metric.metric,
                value=metric.value,
                unit=metric.unit,
                currency=metric.currency,
                period_end=metric.period_end,
                quote=metric.citation.snippet if metric.citation else "",
                table_or_page="deterministic text/table parse",
                confidence=metric.confidence,
                validation_status="accepted_by_deterministic_parser",
            )
            for metric in coverage.metrics
        ]

    def _document_texts(self, identity: GlobalPeerIdentity) -> list[tuple[str, str, str, str]]:
        fixture_key = identity.ticker.upper()
        fixture_rows = self.fixture_documents.get(fixture_key) or self.fixture_documents.get(identity.home_ticker.upper())
        if fixture_rows:
            return fixture_rows
        if not self.enable_live:
            return []
        rows: list[tuple[str, str, str, str]] = []
        seen_urls: set[str] = set()
        for source_type in identity.source_priority:
            url = identity.source_urls.get(source_type)
            if not url or url in seen_urls:
                continue
            try:
                text = self.fetch_text(url)
            except Exception:
                continue
            seen_urls.add(url)
            rows.append((source_type, f"{identity.issuer_name} official source", url, text))
            added = 0
            for title, document_url in _discover_document_links(text, url, identity):
                if document_url in seen_urls or added >= MAX_DISCOVERED_DOCUMENTS_PER_SOURCE:
                    continue
                try:
                    document_text = self.fetch_text(document_url)
                except Exception:
                    continue
                seen_urls.add(document_url)
                rows.append((source_type, title, document_url, document_text))
                added += 1
        return rows


def coverage_metrics_as_financial_metrics(coverage: GlobalPeerCoverage) -> list[FinancialMetric]:
    return [
        FinancialMetric(
            name=metric.metric,
            value=metric.value,
            unit=metric.unit,
            period_end=metric.period_end or "",
            fiscal_period=metric.fiscal_period,
            previous_value=metric.previous_value,
            yoy_change_pct=metric.yoy_change_pct,
            source_url=metric.source_url,
            source_kind=metric.source_type,
        )
        for metric in coverage.metrics
    ]


def _parse_metric_observations(
    identity: GlobalPeerIdentity,
    peer_ticker: str,
    document: GlobalPeerDocument,
    text: str,
    observed_at: str,
) -> list[GlobalPeerMetricObservation]:
    observations: list[GlobalPeerMetricObservation] = []
    for metric, aliases in METRIC_ALIASES.items():
        parsed = _extract_metric_pair(text, aliases)
        if not parsed:
            continue
        value, previous, unit, quote = parsed
        yoy = ((value / previous - 1.0) * 100) if previous not in (None, 0) else None
        citation = Citation(
            source=f"{document.source_type} official document",
            url=document.url or "",
            filed=document.published_at,
            section=metric,
            snippet=quote[:500],
            period_end=document.fiscal_period,
            retrieved_at=observed_at,
            source_tier=document.source_tier,
        )
        observations.append(
            GlobalPeerMetricObservation(
                observation_id=_stable_id("global-peer-metric", peer_ticker, metric, document.document_id),
                peer_ticker=peer_ticker,
                metric=metric,
                value=value,
                unit=unit,
                currency=identity.reporting_currency,
                period_end=document.fiscal_period or document.published_at,
                fiscal_period=document.fiscal_period,
                source_document_id=document.document_id,
                source_url=document.url,
                source_type=document.source_type,
                observed_at=observed_at,
                previous_value=previous,
                yoy_change_pct=yoy,
                citation=citation,
            )
        )
    observations.extend(_derived_gross_margin(peer_ticker, document, observed_at, observations))
    return observations


def _derived_gross_margin(
    peer_ticker: str,
    document: GlobalPeerDocument,
    observed_at: str,
    observations: list[GlobalPeerMetricObservation],
) -> list[GlobalPeerMetricObservation]:
    by_metric = {item.metric: item for item in observations}
    revenue = by_metric.get("Revenue")
    gross_profit = by_metric.get("Gross Profit")
    if not revenue or not gross_profit or revenue.value == 0:
        return []
    current_margin = gross_profit.value / revenue.value * 100
    previous_margin = None
    yoy = None
    if revenue.previous_value not in (None, 0) and gross_profit.previous_value is not None:
        previous_margin = gross_profit.previous_value / revenue.previous_value * 100
        yoy = current_margin - previous_margin
    citation = gross_profit.citation or revenue.citation
    return [
        GlobalPeerMetricObservation(
            observation_id=_stable_id("global-peer-metric", peer_ticker, "Gross Margin", document.document_id),
            peer_ticker=peer_ticker,
            metric="Gross Margin",
            value=current_margin,
            unit="percent",
            currency="",
            period_end=gross_profit.period_end or revenue.period_end,
            fiscal_period=gross_profit.fiscal_period or revenue.fiscal_period,
            source_document_id=document.document_id,
            source_url=document.url,
            source_type=document.source_type,
            observed_at=observed_at,
            confidence="Medium",
            previous_value=previous_margin,
            yoy_change_pct=yoy,
            citation=citation,
        )
    ]


def _extract_metric_pair(text: str, aliases: tuple[str, ...]) -> tuple[float, float | None, str, str] | None:
    for line in _candidate_lines(text, aliases):
        numbers = _numbers_with_units(line)
        if numbers:
            value, unit = numbers[0]
            previous = numbers[1][0] if len(numbers) > 1 else None
            return value, previous, unit, line.strip()
    return None


def _candidate_lines(text: str, aliases: tuple[str, ...]) -> list[str]:
    lines = []
    normalized = html.unescape(text)
    for raw in re.split(r"[\r\n]+|</tr>|</p>|<br\s*/?>", normalized, flags=re.I):
        line = re.sub(r"<[^>]+>", " ", raw)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        lowered = line.lower()
        if any(alias.lower() in lowered for alias in aliases) or any(alias in line for alias in aliases):
            lines.append(line)
    return lines


def _plain_text_preserving_tables(raw_text: str) -> str:
    text = html.unescape(raw_text)
    text = re.sub(r"</(tr|p|div|li|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<(br|tr|p|div|li|h[1-6])\b[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"</t[dh]>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


def _discover_document_links(
    seed_text: str,
    base_url: str,
    identity: GlobalPeerIdentity,
) -> list[tuple[str, str]]:
    base_host = urlparse(base_url).netloc.lower()
    rows: list[tuple[str, str]] = []
    aliases = [identity.issuer_name, identity.home_ticker, *identity.aliases]
    for match in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", seed_text, flags=re.I | re.S):
        href = html.unescape(match.group(1)).strip()
        label = _plain_text_preserving_tables(match.group(2))
        candidate = urljoin(base_url, href)
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            continue
        candidate_host = parsed.netloc.lower()
        if base_host and not (candidate_host == base_host or candidate_host.endswith("." + base_host)):
            continue
        haystack = f"{href} {label}".lower()
        if not _looks_like_financial_report_link(haystack, aliases):
            continue
        title = label or parsed.path.rsplit("/", 1)[-1] or "official financial report"
        rows.append((title[:140], candidate))
    return rows


def _looks_like_financial_report_link(text: str, aliases: list[str]) -> bool:
    report_terms = (
        "annual", "interim", "quarter", "quarterly", "result", "results",
        "financial", "report", "announcement", "presentation", "pdf",
        "20-f", "6-k", "ar", "ir",
    )
    if not any(term in text for term in report_terms):
        return False
    alias_terms = [alias.lower() for alias in aliases if alias and len(alias) >= 3]
    return not alias_terms or any(alias in text for alias in alias_terms) or any(
        term in text for term in ("annual", "interim", "quarter", "results", "financial")
    )


def _numbers_with_units(line: str) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    pattern = (
        r"(?P<currency>CNY|RMB|HKD|USD|CN\$|CN¥|¥)?\s*"
        r"(?P<number>-?\d[\d,]*(?:\.\d+)?)\s*"
        r"(?P<unit>billion|bn|million|m|yi|hundred million|percent|pct|%)?"
    )
    pattern = (
        r"(?P<currency>CNY|RMB|HKD|USD|CN\$)?\s*"
        r"(?P<number>-?\d[\d,]*(?:\.\d+)?)\s*"
        r"(?P<unit>billion|bn|million|m|yi|hundred million|percent|pct|%)?"
    )
    for match in re.finditer(pattern, line, flags=re.I):
        raw = match.group("number").replace(",", "")
        try:
            value = float(raw)
        except ValueError:
            continue
        unit_label = (match.group("unit") or "").lower()
        currency = _normalize_currency(match.group("currency"))
        if unit_label in {"billion", "bn"}:
            value *= 1_000_000_000
            unit = currency or "value"
        elif unit_label in {"million", "m"}:
            value *= 1_000_000
            unit = currency or "value"
        elif unit_label in {"yi", "hundred million"}:
            value *= 100_000_000
            unit = currency or "CNY"
        elif unit_label in {"percent", "pct", "%"}:
            unit = "percent"
        else:
            unit = currency or "value"
        rows.append((value, unit))
    return rows


def _normalize_currency(value: str | None) -> str | None:
    if not value:
        return None
    upper = value.upper().replace("$", "")
    if upper in {"RMB", "CN"}:
        return "CNY"
    if upper in {"CNY", "HKD", "USD"}:
        return upper
    return None


def _csv_identity(ticker: str, path: Path) -> GlobalPeerIdentity | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if (row.get("ticker") or "").strip().upper() != ticker:
                    continue
                source_urls = {}
                for item in (row.get("source_urls") or "").split("|"):
                    if "=" in item:
                        key, url = item.split("=", 1)
                        source_urls[key.strip()] = url.strip()
                return GlobalPeerIdentity(
                    ticker=ticker,
                    issuer_name=(row.get("issuer_name") or ticker).strip(),
                    home_ticker=(row.get("home_ticker") or ticker).strip(),
                    home_exchange=(row.get("home_exchange") or "Unknown").strip(),
                    reporting_currency=(row.get("reporting_currency") or "Unknown").strip(),
                    aliases=[item.strip() for item in (row.get("aliases") or "").split("|") if item.strip()],
                    source_priority=[
                        item.strip()
                        for item in (row.get("source_priority") or "hkex_document,cninfo_document,issuer_ir_report").split(",")
                        if item.strip()
                    ],
                    source_urls=source_urls,
                    profile_source="csv",
                )
    except OSError:
            return None
    return None


def _adr_profile_identity(ticker: str) -> GlobalPeerIdentity | None:
    profile = adr_profile_for(ticker)
    if not profile or not profile.issuer_ir_sources:
        return None
    source_urls: dict[str, str] = {}
    for source_type, url in profile.issuer_ir_sources:
        normalized = _global_source_type(source_type)
        source_urls.setdefault(normalized, url)
    priority = [
        _global_source_type(source_type)
        for source_type in profile.source_priority
        if _global_source_type(source_type) in source_urls
    ]
    if not priority:
        priority = list(source_urls)
    return GlobalPeerIdentity(
        ticker=ticker.upper(),
        issuer_name=ISSUER_NAME_OVERRIDES.get(ticker.upper(), ticker.upper()),
        home_ticker=HOME_TICKER_OVERRIDES.get(ticker.upper(), ticker.upper()),
        home_exchange=profile.home_exchange,
        reporting_currency=profile.reporting_currency,
        aliases=[
            ticker.upper(),
            HOME_TICKER_OVERRIDES.get(ticker.upper(), ticker.upper()),
            *profile.segment_drivers,
        ],
        source_priority=_dedupe(priority),
        source_urls=source_urls,
        profile_source=f"adr_profile:{profile.source}",
    )


def _global_source_type(source_type: str) -> str:
    normalized = (source_type or "").strip().lower()
    if normalized in {"hkex_document", "cninfo_document", "global_peer_official_document", "issuer_ir_report"}:
        return normalized
    if normalized in {"issuer_ir", "presentation", "quarterly_results", "annual_report", "results", "financial_reports"}:
        return "issuer_ir_report"
    return "issuer_ir_report"


def _dedupe(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            rows.append(value)
            seen.add(value)
    return rows


def _fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": config.SEC_USER_AGENT or "EquityResearchRadar/1.0"})
    with urlopen(request, timeout=config.REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read(2_000_000)
        content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" in content_type or urlparse(url).path.lower().endswith(".pdf"):
        return _pdf_text_from_bytes(raw)
    return raw.decode("utf-8", errors="replace")


def _pdf_text_from_bytes(data: bytes) -> str:
    try:
        import pypdf

        reader = pypdf.PdfReader(BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages[:40])
    except Exception:
        return data.decode("utf-8", errors="replace")


def _extract_date(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


def _extract_period(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


def _detect_language(text: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if text.strip():
        return "en"
    return "unknown"


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
