from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from html import unescape
from io import BytesIO
import json
import logging
import re
import zlib
from datetime import date, datetime, timezone
from io import StringIO
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from . import config
from .adr_profiles import issuer_ir_sources_for
from .analysis import extract_numeric_guidance, html_to_text, keyword_snippets, normalize_text, snippet
from .models import (
    ChangeEvent,
    Citation,
    EarningsSurprise,
    FilingRecord,
    FinancialMetric,
    ManagementClaim,
    ManagementCrossCheck,
    ManagementDocument,
    ManagementSourcePackage,
    MeetingEvent,
    ProviderStatus,
    TranscriptTurn,
)
from .sentiment import score_text


MANAGEMENT_SIGNAL_FAMILIES = {
    "guidance_shift",
    "management_credibility",
    "qa_evasion",
    "strategic_priority_change",
    "capital_allocation_change",
    "governance_change",
    "incentive_alignment",
    "shareholder_vote_signal",
    "tone_shift",
    "guidance_specificity_change",
}

MANAGEMENT_FORMS = {
    "8-K", "6-K", "DEF 14A", "DEFA14A", "PRE 14A", "DEF 14C", "SC 13D",
    "SC 13D/A", "SC 13G", "SC 13G/A", "3", "4", "5",
}

TRANSCRIPT_CSV_DIR = config.TRANSCRIPT_CSV_DIR

ISSUER_IR_ARTIFACT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "earnings_call_transcript": ("transcript", "earnings call", "conference call", "prepared remarks"),
    "earnings_presentation": ("presentation", "slides", "results presentation", "investor presentation"),
    "earnings_release": ("press release", "results announcement", "quarterly results", "annual results"),
    "investor_day": ("investor day", "analyst day", "strategy day"),
    "agm_egm_material": ("annual general meeting", "agm", "egm", "extraordinary general meeting", "special meeting"),
    "proxy_statement": ("proxy", "notice of annual general meeting", "voting results", "poll results"),
    "sec_hkex_filing": ("6-k", "20-f", "hkex", "sec filing", "annual report"),
}

CLAIM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "strategic_priority_change": (
        "artificial intelligence", "ai", "cloud", "market share", "international",
        "pricing", "new product", "platform", "strategic priority",
    ),
    "capital_allocation_change": (
        "buyback", "share repurchase", "dividend", "capital return", "debt reduction",
        "capital allocation", "acquisition", "m&a",
    ),
    "cost_actions": (
        "cost reduction", "restructuring", "efficiency", "headcount", "layoff",
        "operating expense", "expense discipline",
    ),
    "margin_commentary": (
        "gross margin", "operating margin", "margin expansion", "margin pressure",
    ),
    "demand_commentary": (
        "demand", "backlog", "orders", "customer demand", "inventory", "sell-through",
    ),
    "regulatory_litigation": (
        "regulatory", "investigation", "litigation", "antitrust", "settlement",
    ),
    "dilution_customer_concentration": (
        "dilution", "share count", "customer concentration", "major customer",
    ),
}

PROXY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "governance_change": (
        "board", "director", "governance", "classified board", "independent",
        "auditor", "ratification",
    ),
    "incentive_alignment": (
        "compensation", "performance share", "restricted stock", "say-on-pay",
        "annual incentive", "long-term incentive",
    ),
    "shareholder_vote_signal": (
        "shareholder proposal", "vote", "annual meeting", "special meeting",
        "adjournment", "approved", "not approved",
    ),
}


class ManagementSourceAdapter:
    provider_name = "Management source stack"

    def fetch_documents(self, ticker: str, history_limit: int | None = None) -> tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]:
        return [], [], []


class AlphaVantageTranscriptProvider(ManagementSourceAdapter):
    provider_name = "Alpha Vantage transcripts"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        quarters: list[str] | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.ALPHAVANTAGE_API_KEY
        self.base_url = base_url or config.ALPHAVANTAGE_BASE_URL
        self.timeout_seconds = timeout_seconds or config.REQUEST_TIMEOUT_SECONDS
        self.quarters = quarters

    def fetch_documents(self, ticker: str, history_limit: int | None = None) -> tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]:
        observed_at = _utc_now()
        if not self.api_key:
            return [], [], [_provider_status(
                self.provider_name, "Unavailable", observed_at,
                "No Alpha Vantage key configured for earnings-call transcripts.",
                "missing_key", True,
            )]
        documents: list[ManagementDocument] = []
        turns: list[TranscriptTurn] = []
        statuses: list[ProviderStatus] = []
        for quarter in self.quarters or _recent_quarters(limit=history_limit or 4):
            query = urlencode({
                "function": "EARNINGS_CALL_TRANSCRIPT",
                "symbol": ticker.upper(),
                "quarter": quarter,
                "apikey": self.api_key,
            })
            url = f"{self.base_url}?{query}"
            try:
                with urlopen(Request(url, headers={"User-Agent": config.APP_NAME}), timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
            except TimeoutError:
                statuses.append(_provider_status(self.provider_name, "Unavailable", observed_at, f"{quarter}: request timed out.", "timeout", True))
                continue
            except (HTTPError, URLError, json.JSONDecodeError) as exc:
                statuses.append(_provider_status(self.provider_name, "Unavailable", observed_at, f"{quarter}: {exc}", "provider_error", True))
                continue
            if payload.get("Note") or payload.get("Information") or payload.get("Error Message"):
                statuses.append(_provider_status(
                    self.provider_name, "Unavailable", observed_at,
                    str(payload.get("Note") or payload.get("Information") or payload.get("Error Message")),
                    "rate_limit_or_entitlement", True,
                ))
                continue
            document, parsed_turns = transcript_document_from_payload(
                ticker, payload, self.provider_name, url, observed_at, official=True,
            )
            if document and parsed_turns:
                documents.append(document)
                turns.extend(parsed_turns)
                statuses.append(_provider_status(self.provider_name, "Available", observed_at, f"{quarter}: transcript available."))
        return documents, turns, statuses


class FmpTranscriptProvider(ManagementSourceAdapter):
    provider_name = "FMP transcripts"

    def __init__(self, api_key: str | None = None, timeout_seconds: int | None = None) -> None:
        self.api_key = api_key if api_key is not None else config.FMP_API_KEY
        self.timeout_seconds = timeout_seconds or config.REQUEST_TIMEOUT_SECONDS

    def fetch_documents(self, ticker: str, history_limit: int | None = None) -> tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]:
        observed_at = _utc_now()
        if not self.api_key:
            return [], [], [_provider_status(
                self.provider_name, "Unavailable", observed_at,
                "No FMP key configured for earnings-call transcripts.",
                "missing_key", True,
            )]
        documents: list[ManagementDocument] = []
        turns: list[TranscriptTurn] = []
        statuses: list[ProviderStatus] = []
        for quarter in _recent_quarters(limit=history_limit or 4):
            year = quarter[:4]
            qnum = quarter[-1]
            url = f"{config.FMP_BASE_URL}/earning-call-transcript?symbol={ticker.upper()}&year={year}&quarter={qnum}&apikey={self.api_key}"
            try:
                with urlopen(Request(url, headers={"User-Agent": config.APP_NAME}), timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8", errors="replace"))
            except TimeoutError:
                statuses.append(_provider_status(self.provider_name, "Unavailable", observed_at, f"{quarter}: request timed out.", "timeout", True))
                continue
            except (HTTPError, URLError, json.JSONDecodeError) as exc:
                statuses.append(_provider_status(self.provider_name, "Unavailable", observed_at, f"{quarter}: {exc}", "provider_error", True))
                continue
            document, parsed_turns = transcript_document_from_payload(
                ticker, payload, self.provider_name, url, observed_at, official=True,
            )
            if document and parsed_turns:
                documents.append(document)
                turns.extend(parsed_turns)
                statuses.append(_provider_status(self.provider_name, "Available", observed_at, f"{quarter}: transcript available."))
        return documents, turns, statuses


class CsvTranscriptProvider(ManagementSourceAdapter):
    provider_name = "CSV transcripts"

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or TRANSCRIPT_CSV_DIR

    def fetch_documents(self, ticker: str) -> tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]:
        observed_at = _utc_now()
        csv_path = self.directory / f"{ticker.upper()}.csv"
        json_path = self.directory / f"{ticker.upper()}.json"
        if csv_path.exists():
            try:
                text = csv_path.read_text(encoding="utf-8-sig")
            except OSError as exc:
                return [], [], [_provider_status(self.provider_name, "Unavailable", observed_at, str(exc), "file_error", False)]
            return transcript_documents_from_csv(ticker, text, csv_path.as_posix(), observed_at)
        if json_path.exists():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                return [], [], [_provider_status(self.provider_name, "Unavailable", observed_at, str(exc), "file_error", False)]
            document, turns = transcript_document_from_payload(
                ticker, payload, self.provider_name, json_path.as_posix(), observed_at, official=False,
            )
            if document and turns:
                return [document], turns, [_provider_status(self.provider_name, "Available", observed_at, "CSV/JSON transcript import available.", official=False)]
        return [], [], [_provider_status(
            self.provider_name, "Unavailable", observed_at,
            f"No transcript import found under {self.directory}.",
            "missing_file", False,
        )]


class IssuerIrArtifactProvider(ManagementSourceAdapter):
    provider_name = "Issuer IR artifacts"

    def __init__(
        self,
        sources_csv: Path | None = None,
        timeout_seconds: int | None = None,
        max_documents_per_seed: int | None = None,
        metadata_limit_per_seed: int | None = None,
        fetcher=None,
    ) -> None:
        self.sources_csv = sources_csv or config.ISSUER_IR_SOURCES_CSV
        self.timeout_seconds = min(timeout_seconds or config.REQUEST_TIMEOUT_SECONDS, 8)
        self.max_documents_per_seed = max_documents_per_seed if max_documents_per_seed is not None else config.ISSUER_IR_MAX_DOCUMENTS_PER_SEED
        self.metadata_limit_per_seed = metadata_limit_per_seed if metadata_limit_per_seed is not None else config.ISSUER_IR_METADATA_LIMIT_PER_SEED
        self.fetcher = fetcher or self._fetch_text

    def fetch_documents(self, ticker: str) -> tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]:
        observed_at = _utc_now()
        seeds = _issuer_ir_sources(ticker, self.sources_csv)
        if not seeds:
            return [], [], [_provider_status(
                self.provider_name, "Unavailable", observed_at,
                (
                    f"No issuer IR seed configured for {ticker.upper()}. Add rows to "
                    f"{self.sources_csv} with ticker,source_type,url."
                ),
                "unconfigured", True,
            )]
        documents: list[ManagementDocument] = []
        turns: list[TranscriptTurn] = []
        statuses: list[ProviderStatus] = []
        seen_urls: set[str] = set()
        for seed_type, seed_url in seeds:
            try:
                index_text = self.fetcher(seed_url)
            except Exception as exc:  # pragma: no cover - network boundary
                statuses.append(_provider_status(
                    self.provider_name, "Unavailable", observed_at,
                    f"{seed_url}: {exc}", "provider_error", True,
                ))
                continue
            candidates = _issuer_ir_candidates(seed_url, index_text, seed_type)
            if not candidates:
                statuses.append(_provider_status(
                    self.provider_name, "Unavailable", observed_at,
                    f"{seed_url}: no transcript, presentation, meeting, proxy, or filing links discovered.",
                    "no_artifacts", True,
                ))
                continue
            added = 0
            metadata_added = 0
            for source_type, title, url in candidates:
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                if added >= self.max_documents_per_seed:
                    if metadata_added < self.metadata_limit_per_seed:
                        documents.append(_issuer_ir_metadata_document(
                            ticker, source_type, title, url, observed_at,
                        ))
                        metadata_added += 1
                    continue
                body = ""
                if _should_fetch_artifact_body(url):
                    try:
                        body = self.fetcher(url)
                    except Exception:
                        body = ""
                document, parsed_turns = _issuer_ir_document_from_artifact(
                    ticker, source_type, title, url, body, observed_at,
                )
                documents.append(document)
                turns.extend(parsed_turns)
                added += 1
            statuses.append(_provider_status(
                self.provider_name,
                "Available" if added else "Unavailable",
                observed_at,
                (
                    f"{seed_url}: parsed {added} issuer IR artifact(s); "
                    f"retained {metadata_added} metadata-only artifact(s) for follow-up triage."
                ),
            ))
        return documents, turns, statuses

    def _fetch_text(self, url: str) -> str | bytes:
        request = Request(url, headers={"User-Agent": config.APP_NAME})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type", "").lower()
            if "pdf" in content_type or urlparse(url).path.lower().endswith(".pdf"):
                return data
            return data.decode("utf-8", errors="replace")


class ManagementSourceStack(ManagementSourceAdapter):
    def __init__(self, providers: list[ManagementSourceAdapter] | None = None) -> None:
        self.providers = providers or [
            IssuerIrArtifactProvider(),
            AlphaVantageTranscriptProvider(),
            FmpTranscriptProvider(),
            CsvTranscriptProvider(),
        ]

    def fetch_documents(self, ticker: str, history_limit: int | None = None) -> tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]:
        if not self.providers:
            return [], [], []

        def fetch_one(provider: ManagementSourceAdapter):
            try:
                return provider.fetch_documents(
                    ticker, history_limit=history_limit,
                )
            except TypeError:
                return provider.fetch_documents(ticker)

        rows: dict[int, tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]] = {}
        workers = min(config.RESEARCH_IO_WORKERS, len(self.providers))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="management-source") as executor:
            futures = {
                executor.submit(fetch_one, provider): index
                for index, provider in enumerate(self.providers)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    rows[index] = future.result()
                except Exception as exc:  # pragma: no cover - provider boundary
                    provider = self.providers[index]
                    rows[index] = (
                        [], [], [ProviderStatus(
                            provider=getattr(provider, "provider_name", provider.__class__.__name__),
                            status="Unavailable",
                            official=False,
                            entitlement_status="provider_error",
                            observed_at=_utc_now(),
                            message=str(exc),
                        )],
                    )

        documents: list[ManagementDocument] = []
        turns: list[TranscriptTurn] = []
        statuses: list[ProviderStatus] = []
        for index in range(len(self.providers)):
            docs, parsed_turns, provider_statuses = rows.get(index, ([], [], []))
            documents.extend(docs)
            turns.extend(parsed_turns)
            statuses.extend(provider_statuses)
        return documents, turns, statuses


def build_management_source_package(
    ticker: str,
    filings: list[FilingRecord],
    filing_texts: dict[str, str],
    transcript_documents: list[ManagementDocument],
    transcript_turns: list[TranscriptTurn],
    provider_statuses: list[ProviderStatus],
    sec_events: list[ChangeEvent],
    metrics: list[FinancialMetric],
    surprises: list[EarningsSurprise] | None = None,
) -> ManagementSourcePackage:
    sec_documents, sec_turns = management_documents_from_filings(ticker, filings, filing_texts)
    documents = sec_documents + transcript_documents
    turns = sec_turns + transcript_turns
    if config.RULE_SENTIMENT_ENABLED:
        _enrich_turn_sentiment(turns)
    claims = extract_management_claims(ticker, documents, turns)
    if config.RULE_SENTIMENT_ENABLED:
        _enrich_claim_sentiment(claims)
    meeting_events = extract_meeting_events(ticker, documents)
    cross_checks = cross_check_management_claims(ticker, claims, sec_events, metrics, turns, surprises or [])
    status_by_claim = {check.claim_id: check.status for check in cross_checks}
    for claim in claims:
        claim.status = status_by_claim.get(claim.claim_id, "Unverified")
    status = "Available" if documents or claims or meeting_events else "Unavailable"
    gaps = []
    if not transcript_documents:
        gaps.append("No external earnings-call transcript provider returned normalized content.")
    if not sec_documents:
        gaps.append("No SEC/issuer management documents were normalized.")
    return ManagementSourcePackage(
        ticker=ticker.upper(), status=status, documents=documents,
        transcript_turns=turns, claims=claims, meeting_events=meeting_events,
        cross_checks=cross_checks, provider_statuses=provider_statuses,
        data_gaps=gaps,
    )


def management_events_from_package(package: ManagementSourcePackage) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []
    checked_claim_ids = {check.claim_id for check in package.cross_checks}
    for claim in package.claims:
        if not claim.statement or claim.claim_type not in MANAGEMENT_SIGNAL_FAMILIES | set(CLAIM_KEYWORDS):
            continue
        category = _event_category_for_claim(claim)
        severity = _claim_severity(claim)
        events.append(ChangeEvent(
            category=category,
            title=f"Management signal: {claim.claim_type.replace('_', ' ')}",
            summary=claim.statement,
            severity=severity,
            direction=claim.direction,
            event_date=claim.event_date,
            source=claim.source_type,
            citations=[claim.citation],
            metrics={
                "management_claim_id": claim.claim_id,
                "claim_type": claim.claim_type,
                "cross_check_status": claim.status,
                "machine_readable": claim.machine_readable,
                "cross_checked": claim.claim_id in checked_claim_ids,
                "speaker": claim.speaker,
                "metric_name": claim.metric,
            },
        ))
    for meeting in package.meeting_events:
        events.append(ChangeEvent(
            category=meeting.event_type,
            title=f"Meeting signal: {meeting.event_type.replace('_', ' ')}",
            summary=meeting.description,
            severity=3 if meeting.source_tier == 1 else 2,
            direction="neutral",
            event_date=meeting.event_date,
            source="issuer meeting/proxy",
            citations=[meeting.citation],
            metrics={"meeting_event_id": meeting.event_id, "cross_check_status": "Confirmed"},
        ))
    events.extend(_sentiment_events_from_package(package))
    return _dedupe_events(events)


def _sentiment_events_from_package(package: ManagementSourcePackage) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []
    tone_claims = [
        claim for claim in package.claims
        if claim.sentiment_label in {"Evasive", "Negative", "Promotional"}
        or (claim.specificity_score is not None and claim.specificity_score >= 4)
    ]
    for claim in tone_claims[:8]:
        category = (
            "qa_evasion" if claim.sentiment_label == "Evasive"
            else "guidance_specificity_change" if (claim.specificity_score or 0) >= 4
            else "tone_shift"
        )
        events.append(ChangeEvent(
            category=category,
            title=f"Management signal: {claim.sentiment_label or 'Tone'} language",
            summary=claim.statement,
            severity=3 if category != "qa_evasion" else 4,
            direction="negative" if claim.sentiment_label in {"Evasive", "Negative"} else "positive",
            event_date=claim.event_date,
            source=claim.source_type,
            citations=[claim.citation],
            metrics={
                "management_claim_id": claim.claim_id,
                "claim_type": claim.claim_type,
                "sentiment_label": claim.sentiment_label,
                "sentiment_score": claim.sentiment_score,
                "specificity_score": claim.specificity_score,
                "evasion_terms": claim.evasion_terms,
                "uncertainty_terms": claim.uncertainty_terms,
                "cross_check_status": claim.status,
                "cross_checked": True,
                "machine_readable": True,
            },
        ))
    return events


def management_documents_from_filings(
    ticker: str,
    filings: list[FilingRecord],
    filing_texts: dict[str, str],
) -> tuple[list[ManagementDocument], list[TranscriptTurn]]:
    documents: list[ManagementDocument] = []
    turns: list[TranscriptTurn] = []
    observed_at = _utc_now()
    for filing in filings:
        raw_text = filing_texts.get(filing.accession, "")
        text = html_to_text(raw_text) if "<" in raw_text[:500] else normalize_text(raw_text)
        if not text:
            continue
        source_type = _source_type_for_filing(filing)
        doc_id = _stable_id("mgmt-doc", ticker, filing.accession, source_type)
        documents.append(ManagementDocument(
            document_id=doc_id, ticker=ticker.upper(), source_type=source_type,
            provider="SEC EDGAR", title=filing.description or filing.form,
            url=filing.url, event_date=filing.filing_date, fiscal_period=filing.report_date,
            source_tier=1, observed_at=observed_at, excerpt=snippet(text, 1200),
        ))
        if _looks_like_prepared_remarks(text):
            for index, paragraph in enumerate(_paragraphs(text)[:20]):
                if len(paragraph) < 80:
                    continue
                turns.append(TranscriptTurn(
                    turn_id=_stable_id("turn", doc_id, index, paragraph[:80]),
                    document_id=doc_id, speaker="Issuer", role=None,
                    section="prepared_remarks", text=snippet(paragraph, 900),
                    turn_index=index,
                ))
    return documents, turns


def transcript_document_from_payload(
    ticker: str,
    payload: object,
    provider: str,
    source_url: str,
    observed_at: str,
    official: bool,
) -> tuple[ManagementDocument | None, list[TranscriptTurn]]:
    rows = _payload_rows(payload)
    if not rows and isinstance(payload, dict):
        text = str(payload.get("transcript") or payload.get("content") or payload.get("text") or "")
        if text:
            rows = [{"speaker": "Unknown", "content": text}]
    if not rows:
        return None, []
    period = _first_string(payload if isinstance(payload, dict) else {}, "quarter", "fiscalQuarter", "period")
    event_date = _first_string(payload if isinstance(payload, dict) else {}, "date", "publishedDate", "fiscalDateEnding")
    title = f"{ticker.upper()} earnings call transcript {period or ''}".strip()
    doc_id = _stable_id("transcript", ticker, provider, period or source_url, event_date or "")
    text_preview = " ".join(_turn_text(row) for row in rows[:6])
    document = ManagementDocument(
        doc_id, ticker.upper(), "earnings_call_transcript", provider, title,
        source_url, event_date, period, 2, observed_at,
        entitlement_status="available", raw_payload_policy="normalized_excerpt_only",
        excerpt=snippet(text_preview, 1200),
    )
    turns: list[TranscriptTurn] = []
    for index, row in enumerate(rows):
        text = _turn_text(row)
        if not text:
            continue
        turns.append(TranscriptTurn(
            turn_id=_stable_id("turn", doc_id, index, text[:80]),
            document_id=doc_id,
            speaker=_first_string(row, "speaker", "name", "person") or "Unknown",
            role=_first_string(row, "title", "role"),
            section=_section_from_row(row, index),
            text=snippet(text, 1000),
            turn_index=index,
            sentiment=_first_string(row, "sentiment"),
        ))
    return document, turns


def transcript_documents_from_csv(
    ticker: str,
    text: str,
    source_url: str,
    observed_at: str,
) -> tuple[list[ManagementDocument], list[TranscriptTurn], list[ProviderStatus]]:
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        return [], [], [_provider_status("CSV transcripts", "Unavailable", observed_at, "Transcript CSV has no header row.", "malformed", False)]
    rows = [row for row in reader if row]
    if not rows:
        return [], [], [_provider_status("CSV transcripts", "Unavailable", observed_at, "Transcript CSV has no data rows.", "empty", False)]
    by_period: dict[str, list[dict]] = {}
    for row in rows:
        period = _first_string(row, "fiscal_period", "quarter", "period", "date") or "unknown"
        by_period.setdefault(period, []).append(row)
    documents: list[ManagementDocument] = []
    turns: list[TranscriptTurn] = []
    for period, grouped in by_period.items():
        document, parsed_turns = transcript_document_from_payload(
            ticker, {"quarter": period, "transcript": grouped}, "CSV transcripts",
            source_url, observed_at, official=False,
        )
        if document:
            documents.append(document)
            turns.extend(parsed_turns)
    return documents, turns, [_provider_status("CSV transcripts", "Available", observed_at, "Transcript CSV import available.", official=False)]


def extract_management_claims(
    ticker: str,
    documents: list[ManagementDocument],
    turns: list[TranscriptTurn],
) -> list[ManagementClaim]:
    turns_by_doc: dict[str, list[TranscriptTurn]] = {}
    for turn in turns:
        turns_by_doc.setdefault(turn.document_id, []).append(turn)
    claims: list[ManagementClaim] = []
    for document in documents:
        text_blocks: list[tuple[str, str | None, str]] = []
        for turn in turns_by_doc.get(document.document_id, []):
            text_blocks.append((turn.text, turn.speaker, turn.section))
        if not text_blocks and document.excerpt:
            text_blocks.append((document.excerpt, None, "document_excerpt"))
        for text, speaker, section in text_blocks:
            claims.extend(_claims_from_text(ticker, document, text, speaker, section))
    return _dedupe_claims(claims)


def _enrich_turn_sentiment(turns: list[TranscriptTurn]) -> None:
    for turn in turns:
        if not turn.text:
            continue
        result = score_text(turn.text)
        turn.sentiment_label = result.label
        turn.sentiment_score = result.score
        turn.sentiment_confidence = result.confidence
        turn.sentiment_source = "provider+rules_based" if turn.sentiment else result.source
        turn.positive_terms = result.positive_terms
        turn.negative_terms = result.negative_terms
        turn.uncertainty_terms = result.uncertainty_terms
        turn.evasion_terms = result.evasion_terms
        turn.specificity_score = result.specificity_score
        if not turn.sentiment:
            turn.sentiment = result.label


def _enrich_claim_sentiment(claims: list[ManagementClaim]) -> None:
    for claim in claims:
        result = score_text(claim.statement)
        claim.sentiment_label = result.label
        claim.sentiment_score = result.score
        claim.sentiment_confidence = result.confidence
        claim.specificity_score = result.specificity_score
        claim.uncertainty_terms = result.uncertainty_terms
        claim.evasion_terms = result.evasion_terms


def extract_meeting_events(ticker: str, documents: list[ManagementDocument]) -> list[MeetingEvent]:
    events: list[MeetingEvent] = []
    for document in documents:
        if document.source_type not in {
            "proxy_statement", "current_report", "ownership_report",
            "agm_egm_material", "sec_hkex_filing",
        }:
            continue
        text = normalize_text(f"{document.title}. {document.excerpt}")
        for event_type, keywords in PROXY_KEYWORDS.items():
            snippets = keyword_snippets(text, list(keywords), max_items=2)
            for item in snippets:
                citation = _citation(document, item, section=event_type)
                events.append(MeetingEvent(
                    event_id=_stable_id("meeting", document.document_id, event_type, item[:80]),
                    ticker=ticker.upper(), document_id=document.document_id,
                    event_type=event_type, description=item,
                    event_date=document.event_date, citation=citation,
                    source_tier=document.source_tier,
                ))
        if "item 5.07" in text.lower() or "submission of matters to a vote" in text.lower():
            item = keyword_snippets(text, ["item 5.07", "submission of matters to a vote"], max_items=1)
            excerpt = item[0] if item else snippet(text, 420)
            events.append(MeetingEvent(
                _stable_id("meeting", document.document_id, "shareholder_vote_signal", excerpt[:80]),
                ticker.upper(), document.document_id, "shareholder_vote_signal",
                excerpt, document.event_date, _citation(document, excerpt, "8-K Item 5.07"),
                document.source_tier,
            ))
    return _dedupe_meetings(events)


def cross_check_management_claims(
    ticker: str,
    claims: list[ManagementClaim],
    sec_events: list[ChangeEvent],
    metrics: list[FinancialMetric],
    transcript_turns: list[TranscriptTurn],
    surprises: list[EarningsSurprise],
) -> list[ManagementCrossCheck]:
    checks: list[ManagementCrossCheck] = []
    event_text = " ".join(
        [event.summary for event in sec_events]
        + [citation.snippet or "" for event in sec_events for citation in event.citations]
    ).lower()
    for claim in claims:
        status = "Unverified"
        check_type = "source_cross_check"
        summary = "No corroborating source was found in filings, facts, surprises, or prior calls."
        citation: Citation | None = None
        source_type = "cross-check"
        source_tier = 3
        materiality = 2
        metric_check = _metric_cross_check(claim, metrics)
        if metric_check:
            status, summary, citation, materiality = metric_check
            check_type = "financial_fact"
            source_type = "SEC companyfacts"
            source_tier = 1
        elif _too_vague(claim.statement):
            status = "Too vague"
            summary = "The claim is too generic to test against filings or financial facts."
            materiality = 1
        elif _is_stale(claim.event_date):
            status = "Stale"
            summary = "The management statement is stale relative to the current research date."
        else:
            if claim.metric and surprises:
                status = "Confirmed"
                summary = "The claim has earnings-surprise context, but line-item attribution remains incomplete."
                check_type = "earnings_surprise"
                source_type = "consensus/surprise"
                source_tier = 3
            elif _shared_keyword_confirmed(claim, event_text):
                status = "Confirmed"
                summary = "Related wording or same-topic evidence appears in SEC/issuer filings."
                check_type = "filing_language"
                source_type = "SEC/issuer filing"
                source_tier = 1
                materiality = max(3, claim.citation.source_tier or 1)
                citation = _matching_event_citation(claim, sec_events)
            elif claim.claim_type == "qa_evasion":
                status = "Contradicted"
                summary = "Management avoided direct disclosure; treat the statement as counter-evidence until quantified."
                check_type = "qa_evasion"
                source_type = claim.source_type
                source_tier = claim.source_tier
                materiality = 3
        checks.append(ManagementCrossCheck(
            check_id=_stable_id("cross", claim.claim_id, status, summary[:80]),
            claim_id=claim.claim_id, ticker=ticker.upper(), status=status,
            check_type=check_type, summary=summary, source_type=source_type,
            source_tier=source_tier, materiality=materiality, citation=citation,
        ))
    return checks


def _claims_from_text(
    ticker: str,
    document: ManagementDocument,
    text: str,
    speaker: str | None,
    section: str,
) -> list[ManagementClaim]:
    claims: list[ManagementClaim] = []
    normalized = normalize_text(text)
    guidance = extract_numeric_guidance(normalized)
    if guidance:
        statement = snippet(normalized, 420)
        metric = str(guidance.get("guidance_metric") or "Guidance")
        citation = _citation(document, statement, section="numeric guidance")
        claims.append(ManagementClaim(
            claim_id=_stable_id("mgmt-claim", document.document_id, "guidance", statement[:100]),
            ticker=ticker.upper(), document_id=document.document_id, claim_type="guidance_shift",
            statement=statement, source_type=document.source_type,
            source_tier=document.source_tier, event_date=document.event_date,
            citation=citation, speaker=speaker, metric=metric,
            period_end=guidance.get("guidance_period"), low=_float(guidance.get("guidance_low")),
            high=_float(guidance.get("guidance_high")),
            currency=guidance.get("guidance_currency"), direction=_direction_from_text(statement),
            machine_readable=True,
        ))
    if _is_evasive(normalized):
        statement = _first_matching_sentence(normalized, ("we do not disclose", "we don't disclose", "not going to comment", "cannot provide", "too early"))
        citation = _citation(document, statement, section="Q&A")
        claims.append(ManagementClaim(
            _stable_id("mgmt-claim", document.document_id, "qa_evasion", statement[:100]),
            ticker.upper(), document.document_id, "qa_evasion", statement,
            document.source_type, document.source_tier, document.event_date,
            citation, speaker=speaker, direction="negative", machine_readable=True,
        ))
    keyword_groups = dict(CLAIM_KEYWORDS)
    if document.source_type == "proxy_statement":
        keyword_groups.update(PROXY_KEYWORDS)
    for claim_type, keywords in keyword_groups.items():
        snippets = keyword_snippets(normalized, list(keywords), max_items=2)
        for item in snippets:
            citation = _citation(document, item, section=claim_type)
            claims.append(ManagementClaim(
                claim_id=_stable_id("mgmt-claim", document.document_id, claim_type, item[:100]),
                ticker=ticker.upper(), document_id=document.document_id,
                claim_type=_normalized_claim_type(claim_type),
                statement=item, source_type=document.source_type,
                source_tier=document.source_tier, event_date=document.event_date,
                citation=citation, speaker=speaker,
                metric=_metric_from_claim_type(claim_type),
                direction=_direction_from_text(item),
                machine_readable=True,
            ))
    return claims


def _metric_cross_check(
    claim: ManagementClaim,
    metrics: list[FinancialMetric],
) -> tuple[str, str, Citation | None, int] | None:
    related = _related_metrics(claim, metrics)
    if not related:
        return None
    metric = related[0]
    if metric.yoy_change_pct is None:
        return "Confirmed", f"SEC companyfacts has related metric {metric.name}, but no comparable-period change.", _metric_citation(metric), 2
    if claim.direction == "positive" and metric.yoy_change_pct < -3:
        return "Contradicted", f"Management tone is positive, but {metric.name} declined {metric.yoy_change_pct:.1f}% year over year.", _metric_citation(metric), 4
    if claim.direction == "negative" and metric.yoy_change_pct > 3:
        return "Contradicted", f"Management tone is negative, but {metric.name} improved {metric.yoy_change_pct:.1f}% year over year.", _metric_citation(metric), 4
    return "Confirmed", f"Related SEC fact {metric.name} moved {metric.yoy_change_pct:.1f}% year over year.", _metric_citation(metric), 3


def _related_metrics(claim: ManagementClaim, metrics: list[FinancialMetric]) -> list[FinancialMetric]:
    terms = {
        "guidance_shift": ("Revenue", "EPS", "Operating Income", "Net Income"),
        "margin_commentary": ("Gross Profit", "Operating Income", "Net Income"),
        "cost_actions": ("Operating Expenses", "Selling General Administrative", "Research and Development"),
        "capital_allocation_change": ("Shares", "Debt", "Cash", "Dividends"),
        "demand_commentary": ("Revenue",),
        "dilution_customer_concentration": ("Shares", "Revenue"),
    }.get(claim.claim_type, ())
    if claim.metric:
        terms = (claim.metric,) + terms
    lowered = [term.lower() for term in terms]
    return [
        metric for metric in metrics
        if any(term in metric.name.lower() for term in lowered)
    ]


def _metric_citation(metric: FinancialMetric) -> Citation | None:
    if not metric.source_url:
        return None
    return Citation(
        source=metric.source_kind,
        url=metric.source_url,
        filed=metric.filed,
        form=metric.form,
        section=metric.name,
        snippet=f"{metric.name}: {metric.value} {metric.unit} for {metric.period_end}.",
        accession=metric.accession,
        period_end=metric.period_end,
        source_tier=1,
    )


def _source_type_for_filing(filing: FilingRecord) -> str:
    form = filing.form.upper()
    if form in {"DEF 14A", "DEFA14A", "PRE 14A", "DEF 14C"}:
        return "proxy_statement"
    if form in {"3", "4", "5", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}:
        return "ownership_report"
    if form in {"8-K", "6-K"}:
        return "current_report"
    return "issuer_filing"


def _event_category_for_claim(claim: ManagementClaim) -> str:
    mapping = {
        "margin_commentary": "guidance_shift",
        "cost_actions": "strategic_priority_change",
        "demand_commentary": "strategic_priority_change",
        "regulatory_litigation": "strategic_priority_change",
        "dilution_customer_concentration": "strategic_priority_change",
    }
    return mapping.get(claim.claim_type, claim.claim_type)


def _claim_severity(claim: ManagementClaim) -> int:
    if claim.status == "Contradicted":
        return 5
    if claim.status == "Confirmed":
        return 4
    if claim.machine_readable:
        return 3
    return 2


def _direction_from_text(text: str) -> str:
    lower = text.lower()
    negative = ("decline", "pressure", "weak", "headwind", "risk", "challenge", "missed", "litigation", "investigation")
    positive = ("growth", "improve", "strong", "tailwind", "expanded", "increase", "opportunity", "exceeded")
    if any(token in lower for token in negative):
        return "negative"
    if any(token in lower for token in positive):
        return "positive"
    return "neutral"


def _normalized_claim_type(claim_type: str) -> str:
    if claim_type in MANAGEMENT_SIGNAL_FAMILIES:
        return claim_type
    if claim_type in {"margin_commentary", "demand_commentary", "cost_actions", "regulatory_litigation", "dilution_customer_concentration"}:
        return claim_type
    return "strategic_priority_change"


def _metric_from_claim_type(claim_type: str) -> str | None:
    return {
        "margin_commentary": "margin",
        "cost_actions": "operating_expense",
        "capital_allocation_change": "capital_allocation",
        "demand_commentary": "revenue",
        "dilution_customer_concentration": "share_count",
    }.get(claim_type)


def _shared_keyword_confirmed(claim: ManagementClaim, event_text: str) -> bool:
    keywords = CLAIM_KEYWORDS.get(claim.claim_type, ()) + PROXY_KEYWORDS.get(claim.claim_type, ())
    return any(keyword.lower() in event_text for keyword in keywords if len(keyword) > 3)


def _matching_event_citation(claim: ManagementClaim, events: list[ChangeEvent]) -> Citation | None:
    keywords = CLAIM_KEYWORDS.get(claim.claim_type, ()) + PROXY_KEYWORDS.get(claim.claim_type, ())
    for event in events:
        haystack = (event.summary + " " + " ".join(citation.snippet or "" for citation in event.citations)).lower()
        if any(keyword.lower() in haystack for keyword in keywords):
            return event.citations[0] if event.citations else None
    return None


def _too_vague(statement: str) -> bool:
    words = re.findall(r"[A-Za-z]{3,}", statement)
    return len(words) < 8 or statement.lower().strip() in {"we are excited", "we are optimistic"}


def _is_stale(event_date: str | None) -> bool:
    if not event_date:
        return False
    try:
        value = date.fromisoformat(event_date[:10])
    except ValueError:
        return False
    return (date.today() - value).days > 540


def _is_evasive(text: str) -> bool:
    return any(token in text.lower() for token in ("we do not disclose", "we don't disclose", "not going to comment", "cannot provide", "too early"))


def _first_matching_sentence(text: str, tokens: tuple[str, ...]) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for sentence in sentences:
        if any(token in sentence.lower() for token in tokens):
            return snippet(sentence, 420)
    return snippet(text, 420)


def _looks_like_prepared_remarks(text: str) -> bool:
    lower = text.lower()
    return any(token in lower for token in ("prepared remarks", "earnings", "results", "outlook", "guidance", "presentation"))


def _paragraphs(text: str) -> list[str]:
    return [normalize_text(item) for item in re.split(r"\n{2,}|(?<=[.!?])\s{2,}", text) if normalize_text(item)]


def _section_from_row(row: dict, index: int) -> str:
    value = _first_string(row, "section", "type")
    if value:
        return value
    speaker = (_first_string(row, "speaker", "name") or "").lower()
    if "analyst" in speaker or "operator" in speaker:
        return "qa"
    return "prepared_remarks" if index < 12 else "qa"


def _payload_rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        if payload and all(isinstance(item, dict) for item in payload):
            if any("transcript" in item and isinstance(item.get("transcript"), list) for item in payload):
                rows: list[dict] = []
                for item in payload:
                    rows.extend(_payload_rows(item.get("transcript")))
                return rows
            return [item for item in payload if isinstance(item, dict)]
        return []
    if isinstance(payload, dict):
        for key in ("transcript", "data", "results", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        text = payload.get("transcript") or payload.get("content") or payload.get("text")
        if isinstance(text, str):
            return [{"speaker": "Unknown", "content": text}]
    return []


def _turn_text(row: dict) -> str:
    return normalize_text(str(row.get("content") or row.get("text") or row.get("speech") or row.get("transcript") or ""))


def _first_string(row: dict, *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _citation(document: ManagementDocument, excerpt: str, section: str | None = None) -> Citation:
    return Citation(
        source=document.provider,
        url=document.url or "",
        filed=document.event_date,
        form=document.source_type,
        section=section,
        snippet=snippet(excerpt, 420),
        period_end=document.fiscal_period,
        retrieved_at=document.observed_at,
        source_tier=document.source_tier,
    )


def _dedupe_claims(claims: list[ManagementClaim]) -> list[ManagementClaim]:
    seen: set[str] = set()
    result: list[ManagementClaim] = []
    for claim in claims:
        key = claim.claim_id
        if key in seen:
            continue
        seen.add(key)
        result.append(claim)
    return result


def _dedupe_meetings(events: list[MeetingEvent]) -> list[MeetingEvent]:
    seen: set[str] = set()
    result: list[MeetingEvent] = []
    for event in events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        result.append(event)
    return result


def _dedupe_events(events: list[ChangeEvent]) -> list[ChangeEvent]:
    seen: set[tuple[str, str | None, str]] = set()
    result: list[ChangeEvent] = []
    for event in events:
        key = (event.category, event.event_date, event.summary[:120])
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def _issuer_ir_sources(ticker: str, csv_path: Path) -> list[tuple[str, str]]:
    ticker = ticker.upper()
    sources = list(issuer_ir_sources_for(ticker, config.ADR_PROFILE_CSV))
    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if (row.get("ticker") or "").strip().upper() != ticker:
                        continue
                    url = (row.get("url") or "").strip()
                    if not url:
                        continue
                    sources.append(((row.get("source_type") or "issuer_ir").strip(), url))
        except OSError:
            pass
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source_type, url in sources:
        if url in seen:
            continue
        seen.add(url)
        deduped.append((source_type, url))
    return deduped


def _issuer_ir_candidates(seed_url: str, html: str | bytes, seed_type: str) -> list[tuple[str, str, str]]:
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    links = _links_from_html(seed_url, html) + _structured_artifact_links(seed_url, html)
    scored: list[tuple[int, str, str, str]] = []
    for title, url in links:
        if not _is_candidate_artifact_url(url):
            continue
        source_type, score = _classify_issuer_artifact(title, url, seed_type)
        if score <= 0:
            continue
        scored.append((score, source_type, title, url))
    scored.sort(key=lambda item: (
        -_artifact_priority(item[1]),
        -item[0],
        _artifact_format_cost(item[3]),
        item[2].lower(),
    ))
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for _, source_type, title, url in scored:
        if url in seen:
            continue
        seen.add(url)
        result.append((source_type, title, url))
    return result


def _links_from_html(base_url: str, html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for match in re.finditer(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html, flags=re.I | re.S):
        href = unescape(match.group(1)).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        title = normalize_text(html_to_text(match.group(2)) or unescape(match.group(2)))
        if not title:
            title = href.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        links.append((title, urljoin(base_url, href)))
    return links


def _structured_artifact_links(base_url: str, html: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for field, suffix in (
        ("transcriptUrl", "Transcript"),
        ("presentationUrl", "Presentation"),
        ("presentationLink", "Presentation"),
        ("pressReleaseUrl", "Press Release"),
        ("webcastUrl", "Webcast"),
        ("pdf", "PDF"),
        ("url", "Document"),
    ):
        pattern = rf'"{field}"\s*:\s*"([^"]+)"'
        for match in re.finditer(pattern, html):
            raw_url = _json_unescape(match.group(1)).strip()
            if not raw_url or raw_url.startswith(("data:", "javascript:", "mailto:")):
                continue
            context = html[max(0, match.start() - 1800):match.start()]
            title = _nearest_json_value(context, "documentTitle") or _nearest_json_value(context, "name") or suffix
            links.append((f"{title} {suffix}".strip(), urljoin(base_url, raw_url)))
    return links


def _nearest_json_value(context: str, key: str) -> str | None:
    matches = list(re.finditer(rf'"{key}"\s*:\s*"([^"]+)"', context))
    if not matches:
        return None
    return normalize_text(_json_unescape(matches[-1].group(1)))


def _json_unescape(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace("\\/", "/")


def _classify_issuer_artifact(title: str, url: str, seed_type: str) -> tuple[str, int]:
    haystack = f"{title} {url}".lower()
    best_type = ""
    best_score = 0
    for source_type, keywords in ISSUER_IR_ARTIFACT_KEYWORDS.items():
        score = sum(2 for keyword in keywords if _keyword_hit(haystack, keyword))
        if source_type in {"earnings_call_transcript", "agm_egm_material", "proxy_statement"}:
            score += int(any(_keyword_hit(haystack, keyword) for keyword in keywords))
        if score > best_score:
            best_type, best_score = source_type, score
    return best_type, best_score


def _keyword_hit(haystack: str, keyword: str) -> bool:
    keyword = keyword.lower()
    if len(keyword) <= 4 and keyword.isalnum():
        return bool(re.search(rf"\b{re.escape(keyword)}\b", haystack))
    return keyword in haystack


def _artifact_priority(source_type: str) -> int:
    return {
        "earnings_call_transcript": 100,
        "earnings_release": 90,
        "earnings_presentation": 80,
        "agm_egm_material": 70,
        "proxy_statement": 65,
        "investor_day": 60,
        "sec_hkex_filing": 50,
    }.get(source_type, 0)


def _artifact_format_cost(url: str) -> int:
    path = urlparse(url).path.lower()
    if path.endswith((".html", ".htm", ".txt")):
        return 0
    if path.endswith(".pdf"):
        return 2
    return 1


def _is_candidate_artifact_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if any(domain in host for domain in (
        "twitter.com", "x.com", "linkedin.com", "instagram.com",
        "facebook.com", "youtube.com", "weibo.com",
    )):
        return False
    if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")):
        return False
    return True


def _should_fetch_artifact_body(url: str) -> bool:
    path = urlparse(url).path.lower()
    return not path.endswith((".zip", ".xls", ".xlsx", ".ppt", ".pptx"))


def _issuer_ir_document_from_artifact(
    ticker: str,
    source_type: str,
    title: str,
    url: str,
    body: object,
    observed_at: str,
) -> tuple[ManagementDocument, list[TranscriptTurn]]:
    text = _artifact_text(url, body)
    event_date = _date_from_text(title) or _date_from_text(url)
    fiscal_period = _fiscal_period_from_text(title) or _fiscal_period_from_text(url)
    excerpt_source = text or title
    doc_id = _stable_id("issuer-ir", ticker, source_type, url)
    document = ManagementDocument(
        document_id=doc_id,
        ticker=ticker.upper(),
        source_type=source_type,
        provider="Issuer IR",
        title=title or source_type.replace("_", " ").title(),
        url=url,
        event_date=event_date,
        fiscal_period=fiscal_period,
        source_tier=1,
        observed_at=observed_at,
        entitlement_status="available",
        raw_payload_policy="normalized_excerpt_only",
        excerpt=snippet(excerpt_source, 1200),
    )
    turns: list[TranscriptTurn] = []
    if source_type == "earnings_call_transcript" and text:
        for index, paragraph in enumerate(_paragraphs(text)[:60]):
            if len(paragraph) < 80:
                continue
            speaker, content = _speaker_and_content(paragraph)
            turns.append(TranscriptTurn(
                turn_id=_stable_id("turn", doc_id, index, content[:80]),
                document_id=doc_id,
                speaker=speaker,
                role=None,
                section="issuer_ir_transcript",
                text=snippet(content, 1000),
                turn_index=index,
            ))
    return document, turns


def _issuer_ir_metadata_document(
    ticker: str,
    source_type: str,
    title: str,
    url: str,
    observed_at: str,
) -> ManagementDocument:
    return ManagementDocument(
        document_id=_stable_id("issuer-ir-metadata", ticker, source_type, url),
        ticker=ticker.upper(),
        source_type=source_type,
        provider="Issuer IR",
        title=title or source_type.replace("_", " ").title(),
        url=url,
        event_date=_date_from_text(title) or _date_from_text(url),
        fiscal_period=_fiscal_period_from_text(title) or _fiscal_period_from_text(url),
        source_tier=1,
        observed_at=observed_at,
        entitlement_status="triaged_metadata_only",
        raw_payload_policy="metadata_only_latency_triaged",
        excerpt=snippet(title or url, 500),
    )


def _artifact_text(url: str, body: object) -> str:
    if isinstance(body, bytes):
        if urlparse(url).path.lower().endswith(".pdf"):
            return _cached_pdf_text(url, body)
        return normalize_text(body.decode("utf-8", errors="replace"))
    text = str(body or "")
    if not text:
        return ""
    if urlparse(url).path.lower().endswith(".pdf") and "%PDF" in text[:20]:
        return _cached_pdf_text(url, text.encode("latin-1", errors="ignore"))
    if "<" in text[:500]:
        converted = html_to_text(text)
        if "<" in converted[:500]:
            converted = re.sub(r"<[^>]+>", " ", converted)
        return normalize_text(converted)
    return normalize_text(text)


def _cached_pdf_text(url: str, data: bytes) -> str:
    cache_dir = config.CACHE_DIR / "issuer_ir_text"
    digest = hashlib.sha256(url.encode("utf-8", errors="ignore") + b"\0" + data[:2048] + str(len(data)).encode()).hexdigest()
    cache_path = cache_dir / f"{digest}.txt"
    try:
        if cache_path.exists():
            cached = cache_path.read_text(encoding="utf-8")
            if cached:
                return cached
    except OSError:
        pass
    text = _pdf_text_from_bytes(data)
    if text:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(text[:config.ISSUER_IR_TEXT_CACHE_CHARS], encoding="utf-8")
        except OSError:
            pass
    return text


def _pdf_text_from_bytes(data: bytes) -> str:
    for module_name in ("pypdf", "PyPDF2"):
        try:
            module = __import__(module_name)
            logging.getLogger(module_name).setLevel(logging.ERROR)
            reader = module.PdfReader(BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:40])
            if normalize_text(text):
                return normalize_text(text)
        except Exception:
            continue
    return _basic_pdf_text(data)


def _basic_pdf_text(data: bytes) -> str:
    chunks: list[bytes] = []
    for match in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, flags=re.S):
        stream = match.group(1).strip(b"\r\n")
        header = data[max(0, match.start() - 300):match.start()]
        if b"FlateDecode" in header:
            try:
                stream = zlib.decompress(stream)
            except zlib.error:
                continue
        chunks.append(stream)
    extracted: list[str] = []
    for chunk in chunks:
        content = chunk.decode("latin-1", errors="ignore")
        extracted.extend(_pdf_strings_from_content(content))
    return _repair_fragmented_pdf_text(normalize_text(" ".join(extracted)))


def _pdf_strings_from_content(content: str) -> list[str]:
    values: list[str] = []
    for block in re.findall(r"BT(.*?)ET", content, flags=re.S):
        for array in re.findall(r"\[(.*?)\]\s*TJ", block, flags=re.S):
            for value in re.findall(r"\((?:\\.|[^\\)])*\)", array, flags=re.S):
                values.append(_decode_pdf_string(value[1:-1]))
        for value in re.findall(r"\((?:\\.|[^\\)])*\)\s*T[j']", block, flags=re.S):
            values.append(_decode_pdf_string(value[1:value.rfind(")")]))
        for hex_value in re.findall(r"<([0-9A-Fa-f\s]+)>\s*T[j']", block, flags=re.S):
            try:
                values.append(bytes.fromhex("".join(hex_value.split())).decode("utf-8", errors="ignore"))
            except ValueError:
                continue
    return [normalize_text(value) for value in values if normalize_text(value)]


def _decode_pdf_string(value: str) -> str:
    value = re.sub(r"\\([\\()])", r"\1", value)
    value = value.replace("\\n", "\n").replace("\\r", "\r").replace("\\t", "\t")
    value = re.sub(
        r"\\([0-7]{1,3})",
        lambda match: chr(int(match.group(1), 8)),
        value,
    )
    return value


def _repair_fragmented_pdf_text(text: str) -> str:
    tokens = text.split()
    if not tokens:
        return ""
    repaired: list[str] = []
    buffer: list[str] = []
    buffer_kind = ""

    def flush() -> None:
        nonlocal buffer, buffer_kind
        if not buffer:
            return
        if len(buffer) >= 3:
            repaired.append("".join(buffer))
        else:
            repaired.extend(buffer)
        buffer = []
        buffer_kind = ""

    for token in tokens:
        kind = "alpha" if token.isalpha() else "digit" if token.isdigit() else ""
        if kind and len(token) <= 2:
            starts_new_word = (
                kind == "alpha"
                and buffer
                and token[:1].isupper()
                and not buffer[-1][:1].isupper()
            )
            if buffer_kind and buffer_kind != kind:
                flush()
            if starts_new_word:
                flush()
            buffer_kind = kind
            buffer.append(token)
            continue
        flush()
        repaired.append(token)
    flush()
    return normalize_text(" ".join(repaired))


def _speaker_and_content(paragraph: str) -> tuple[str, str]:
    match = re.match(r"^([A-Z][A-Za-z .'-]{2,60})[:\-]\s+(.+)$", paragraph)
    if match:
        return normalize_text(match.group(1)), normalize_text(match.group(2))
    return "Issuer", paragraph


def _date_from_text(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b", text)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def _fiscal_period_from_text(text: str) -> str | None:
    quarter = re.search(r"\b(20\d{2})\s*Q([1-4])\b|\bQ([1-4])\s*(20\d{2})\b", text, flags=re.I)
    if quarter:
        if quarter.group(1):
            return f"{quarter.group(1)}Q{quarter.group(2)}"
        return f"{quarter.group(4)}Q{quarter.group(3)}"
    month = re.search(r"\b(March|June|September|December)\s+Quarter\s+(20\d{2})\b", text, flags=re.I)
    if month:
        return f"{month.group(2)} {month.group(1).title()} Quarter"
    return None


def _recent_quarters(limit: int = 4) -> list[str]:
    today = date.today()
    quarter = (today.month - 1) // 3 + 1
    year = today.year
    result: list[str] = []
    for _ in range(limit):
        result.append(f"{year}Q{quarter}")
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return result


def _provider_status(
    provider: str,
    status: str,
    observed_at: str,
    message: str,
    entitlement_status: str = "available",
    official: bool = True,
) -> ProviderStatus:
    return ProviderStatus(
        provider=provider,
        status=status,
        official=official,
        entitlement_status=entitlement_status,
        observed_at=observed_at,
        message=_redact_provider_message(message),
    )


def _redact_provider_message(message: str) -> str:
    redacted = str(message or "")
    for secret in (
        config.ALPHAVANTAGE_API_KEY,
        config.FMP_API_KEY,
        config.FINNHUB_API_KEY,
        config.FRED_API_KEY,
        config.BEA_API_KEY,
        config.CENSUS_API_KEY,
        config.WISBURG_API_KEY,
    ):
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1[redacted]", redacted)
    redacted = re.sub(r"(?i)(api key (?:as|is)\s+)[A-Za-z0-9._-]+", r"\1[redacted]", redacted)
    return redacted


def _stable_id(*parts: object) -> str:
    digest = hashlib.sha1("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:16]
    return digest


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
