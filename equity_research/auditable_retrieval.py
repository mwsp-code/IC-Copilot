from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

from .analysis import html_to_text
from .document_normalization import normalize_document_content
from .evidence_inputs import evidence_is_claim_eligible, search_evidence_catalog
from .models import (
    AuditRepairItem,
    AuditRepairQueue,
    ClaimEvidenceAudit,
    CompanyIdentity,
    ContractualExposureObservation,
    DemoFreezeDecision,
    DemoQualityPolicy,
    FilingRecord,
    FinancialMetric,
    HistoricalFinancialPath,
    HistoricalValuationObservation,
    NormalizedSourceRecord,
    RegulatoryEventObservation,
    ResearchRoundArtifact,
    SourceRetrievalLifecycle,
)
from .application.publication_readiness import PublicationGateEvaluator


SEMANTIC_EVIDENCE_HASH_VERSION = "semantic-evidence-v1"
SOURCE_CONTRACT_VERSION = "normalized-source-v1"

RETRIEVAL_STATES = {
    "planned",
    "discovered",
    "catalog_match",
    "fetched_live",
    "cached_document",
    "metadata_only",
    "parsed",
    "validated",
    "contradicted",
    "unavailable",
    "licensed_manual_required",
    "failed",
}

_RETRIEVAL_STOPWORDS = {
    "audit",
    "claim",
    "company",
    "confirm",
    "disprove",
    "document",
    "evidence",
    "historical",
    "history",
    "inspect",
    "issuer",
    "missing",
    "outcome",
    "repair",
    "research",
    "source",
}

SOURCE_TYPE_ALIASES = {
    "regulator_release": "regulator_court",
    "regulatory_release": "regulator_court",
    "court_record": "regulator_court",
    "agency_release": "regulator_court",
    "issuer_newsroom": "issuer_announcement",
    "issuer_release": "issuer_announcement",
    "company_announcement": "issuer_announcement",
    "sec_archive": "historical_filing",
    "sec_historical_filing": "historical_filing",
    "historical_sec_filing": "historical_filing",
    "historical_price": "quantitative_historical_price",
    "valuation_history": "quantitative_historical_price",
    "supplier_announcement": "supplier_customer_announcement",
    "customer_announcement": "supplier_customer_announcement",
}

_VOLATILE_EVIDENCE_FIELDS = {
    "observed_at",
    "retrieved_at",
    "request_timing",
    "latency_ms",
    "cache_hit",
    "cache_status",
    "selection_score",
    "selection_components",
    "untrusted_source_content",
    "prompt_budget",
}

_CONTRACTUAL_PATTERNS = {
    "purchase_obligation": (
        "purchase obligation",
        "minimum purchase",
        "non-cancelable purchase",
    ),
    "supplier_prepayment": ("supplier prepayment", "prepayment to supplier"),
    "inventory_commitment": ("inventory commitment", "inventory purchase commitment"),
    "guarantee": ("guarantee", "guaranty"),
    "lease": ("operating lease", "finance lease", "lease obligation"),
    "take_or_pay": ("take-or-pay", "take or pay"),
    "capacity_commitment": (
        "capacity commitment",
        "manufacturing commitment",
        "committed capacity",
    ),
    "contract_manufacturing": (
        "contract manufacturer",
        "manufacturing obligation",
    ),
}

_MONEY_RE = re.compile(
    r"(?P<currency>US\$|\$|USD|CNY|RMB|EUR|GBP)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<scale>billion|million|thousand|bn|mm|m|b)?",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.%/-]*", re.IGNORECASE)


def canonical_source_type(source_type: str) -> str:
    normalized = str(source_type or "").strip().casefold().replace(" ", "_")
    return SOURCE_TYPE_ALIASES.get(normalized, normalized)


def semantic_evidence_hash(
    catalog: Iterable[dict[str, Any]],
    *,
    prompt_version: str,
    source_registry_version: str,
) -> tuple[str, dict[str, str]]:
    """Hash normalized factual content, excluding timing and presentation noise."""
    fingerprints: dict[str, str] = {}
    for item in catalog:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        normalized = _semantic_value({
            key: value
            for key, value in item.items()
            if key not in _VOLATILE_EVIDENCE_FIELDS
        })
        fingerprints[evidence_id] = hashlib.sha256(
            json.dumps(
                normalized,
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    payload = {
        "version": SEMANTIC_EVIDENCE_HASH_VERSION,
        "prompt_version": prompt_version,
        "source_registry_version": source_registry_version,
        "evidence": sorted(fingerprints.items()),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return digest, fingerprints


def normalize_catalog_source_records(
    identity: CompanyIdentity,
    catalog: Iterable[dict[str, Any]],
) -> list[NormalizedSourceRecord]:
    records: list[NormalizedSourceRecord] = []
    for item in catalog:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id:
            continue
        source_type = canonical_source_type(str(item.get("source_type") or ""))
        source_url = str(item.get("source_url") or "")
        excerpt = str(
            item.get("exact_excerpt")
            or item.get("excerpt")
            or item.get("summary")
            or ""
        )
        kind = str(item.get("kind") or "")
        metadata_only = bool(
            item.get("metadata_only")
            or (
                kind in {"news_metadata", "historical_source"}
                and not item.get("exact_excerpt")
                and not item.get("value")
                and not item.get("formula")
            )
        )
        has_exact_content = bool(
            item.get("exact_excerpt")
            or item.get("value") is not None
            or item.get("formula")
            or (
                kind in {
                    "validated_claim",
                    "financial_metric",
                    "financial_metric_prior",
                    "source_event",
                }
                and excerpt
            )
        )
        retrieval_status = str(item.get("retrieval_status") or "")
        parser_status = str(item.get("parser_status") or "")
        validation_status = str(item.get("validation_status") or "")
        if not retrieval_status:
            retrieval_status = (
                "metadata_only"
                if metadata_only
                else "cached_document"
                if source_url and has_exact_content
                else "parsed"
                if has_exact_content
                else "metadata_only"
            )
        if not parser_status:
            parser_status = "parsed" if has_exact_content else "not_parsed"
        if not validation_status:
            validation_status = (
                "validated"
                if has_exact_content
                and evidence_is_claim_eligible(item)
                and not metadata_only
                else "metadata_only"
                if metadata_only
                else "unvalidated"
            )
        if source_type == "normalized_research_record":
            validation_status = "unvalidated"
        records.append(NormalizedSourceRecord(
            source_id=evidence_id,
            ticker=identity.ticker.upper(),
            entity_name=identity.name,
            security_id=identity.ticker.upper(),
            source_family=str(item.get("source_family") or source_type or "unknown"),
            source_type=source_type or "unknown",
            document_id=str(
                item.get("document_id")
                or item.get("accession")
                or item.get("citation_id")
                or evidence_id
            ),
            source_url=source_url,
            publication_date=str(
                item.get("publication_date")
                or item.get("filed")
                or item.get("observed_at")
                or ""
            ),
            reporting_period=str(item.get("period") or ""),
            section=str(item.get("section") or ""),
            page_or_table=str(
                item.get("page_or_table")
                or item.get("page")
                or item.get("table")
                or ""
            ),
            exact_excerpt=excerpt[:4_000],
            metric_or_claim=str(
                item.get("metric")
                or item.get("driver")
                or item.get("claim_type")
                or kind
            ),
            value=item.get("value"),
            unit=str(item.get("unit") or ""),
            currency=str(item.get("currency") or ""),
            source_tier=_optional_int(item.get("source_tier")),
            origin_group=str(
                item.get("origin_group")
                or _origin_group(source_url)
                or source_type
                or "unknown"
            ),
            licensing_policy=str(
                item.get("licensing_policy") or "metadata_and_excerpt_only"
            ),
            retrieval_status=retrieval_status,
            parser_status=parser_status,
            validation_status=validation_status,
            citation_id=str(item.get("citation_id") or ""),
            accession=str(item.get("accession") or ""),
            form=str(item.get("form") or ""),
            content_hash=str(item.get("content_hash") or ""),
            formula=str(item.get("formula") or ""),
            derived_from_source_ids=[
                str(value)
                for value in item.get("derived_from_source_ids", [])
                if value
            ],
            observed_at=str(item.get("observed_at") or ""),
            period_start=str(item.get("period_start") or ""),
            duration_days=_optional_int(item.get("duration_days")),
            fiscal_period=str(item.get("fiscal_period") or ""),
            fiscal_year=_optional_int(item.get("fiscal_year")),
            fiscal_frame=str(item.get("fiscal_frame") or ""),
        ))
    return _dedupe_records(records)


def source_record_to_evidence(
    record: NormalizedSourceRecord,
) -> dict[str, Any]:
    kind = (
        "historical_source"
        if record.source_type in {
            "historical_filing",
            "quantitative_historical_price",
            "institutional_history",
            "supplier_customer_announcement",
            "regulator_court",
        }
        or "historical" in record.source_family
        else "retrieved_source"
    )
    return {
        "evidence_id": record.source_id,
        "citation_id": record.citation_id,
        "kind": kind,
        "source_type": record.source_type,
        "source_family": record.source_family,
        "source_tier": record.source_tier,
        "period": record.reporting_period,
        "period_start": record.period_start,
        "duration_days": record.duration_days,
        "fiscal_period": record.fiscal_period,
        "fiscal_year": record.fiscal_year,
        "fiscal_frame": record.fiscal_frame,
        "publication_date": record.publication_date,
        "section": record.section,
        "page_or_table": record.page_or_table,
        "summary": record.exact_excerpt,
        "exact_excerpt": record.exact_excerpt,
        "metric": record.metric_or_claim,
        "value": record.value,
        "unit": record.unit,
        "currency": record.currency,
        "source_url": record.source_url,
        "document_id": record.document_id,
        "accession": record.accession,
        "form": record.form,
        "origin_group": record.origin_group,
        "licensing_policy": record.licensing_policy,
        "retrieval_status": record.retrieval_status,
        "parser_status": record.parser_status,
        "validation_status": record.validation_status,
        "content_hash": record.content_hash,
        "formula": record.formula,
        "derived_from_source_ids": list(record.derived_from_source_ids),
    }


class AuditableEvidenceRetriever:
    """Executes registered source requests without granting models web access."""

    def __init__(
        self,
        identity: CompanyIdentity,
        *,
        sec_client: Any | None = None,
        price_client: Any | None = None,
        filings: Iterable[FilingRecord] = (),
        metrics: Iterable[FinancialMetric] = (),
        catalog: Iterable[dict[str, Any]] = (),
        fetch_text: Callable[[str], str | bytes] | None = None,
        registered_source_catalog: Iterable[
            NormalizedSourceRecord
        ] | None = None,
    ) -> None:
        self.identity = identity
        self.sec_client = sec_client
        self.price_client = price_client
        self.filings = list(filings)
        self.metrics = list(metrics)
        self.catalog = [dict(item) for item in catalog]
        self.records = normalize_catalog_source_records(identity, self.catalog)
        self.fetch_text = fetch_text
        self.registered_source_catalog = list(
            registered_source_catalog
            if registered_source_catalog is not None
            else _load_registered_source_catalog(identity)
        )
        self._seen_content_hashes: set[str] = {
            record.content_hash for record in self.records
            if record.content_hash
        }

    def retrieve(
        self,
        *,
        request_id: str,
        source_type: str,
        query: str,
        period: str = "",
        result_limit: int = 12,
    ) -> tuple[list[NormalizedSourceRecord], SourceRetrievalLifecycle]:
        canonical = canonical_source_type(source_type)
        lifecycle = SourceRetrievalLifecycle(
            request_id=request_id,
            source_id="",
            status="planned",
            source_type=canonical,
            history=["planned"],
            attempted_at=_utc_now(),
        )
        try:
            if canonical in {"sec_filing", "historical_filing"}:
                records, status, failure = self._retrieve_sec(
                    canonical, query, period, result_limit
                )
            elif canonical in {
                "issuer_ir",
                "issuer_announcement",
                "issuer_management_remarks",
                "presentation",
                "agm_egm_proxy",
                "supplier_customer_announcement",
                "regulator_court",
                "institutional_history",
                "management_press_interview",
            }:
                records, status, failure = self._retrieve_registered_documents(
                    canonical, query, period, result_limit
                )
            elif canonical in {
                "quantitative_historical_price",
                "macro_market",
            }:
                records, status, failure = self._retrieve_prices(
                    query, period, result_limit
                )
            else:
                records = self._catalog_records(
                    canonical, query, period, result_limit
                )
                status = "catalog_match" if records else (
                    "licensed_manual_required"
                    if canonical in {
                        "licensed_newswire",
                        "reputable_publisher",
                        "external_research",
                        "consensus_manual",
                    }
                    else "unavailable"
                )
                failure = (
                    "licensed_manual_required"
                    if status == "licensed_manual_required"
                    else "source_unavailable"
                    if not records else ""
                )
        except Exception as exc:
            records = []
            status = "failed"
            failure = _failure_code(exc)
            lifecycle.message = _safe_error(str(exc))
        lifecycle.status = status
        lifecycle.failure_code = failure
        lifecycle.completed_at = _utc_now()
        lifecycle.history.extend(_lifecycle_history(status))
        if records:
            lifecycle.source_id = records[0].source_id
            lifecycle.message = (
                f"{len(records)} source record(s) reached {status}."
            )
        elif not lifecycle.message:
            lifecycle.message = _failure_message(failure, canonical)
        self.records = _dedupe_records([*self.records, *records])
        return records, lifecycle

    def _retrieve_sec(
        self,
        source_type: str,
        query: str,
        period: str,
        limit: int,
    ) -> tuple[list[NormalizedSourceRecord], str, str]:
        if self.sec_client is None or not self.identity.cik:
            return [], "unavailable", "sec_client_unavailable"
        forms = _forms_for_query(query)
        candidates = list(self.filings)
        fetcher = getattr(self.sec_client, "get_comparable_filings", None)
        if callable(fetcher):
            for form in forms:
                try:
                    candidates.extend(
                        fetcher(self.identity.cik, form, limit=max(4, limit))
                    )
                except Exception:
                    continue
        candidates = _dedupe_filings(candidates)
        candidates = _rank_filings(candidates, forms, period)[: max(2, limit)]
        if not candidates:
            return [], "unavailable", "document_not_found"
        records: list[NormalizedSourceRecord] = []
        parsed_without_claim = False
        for filing in candidates:
            was_cached = bool(
                getattr(self.sec_client, "is_text_cached", lambda _url: False)(
                    filing.url
                )
            )
            try:
                raw = self.sec_client.get_filing_text(filing)
            except Exception:
                continue
            content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            text = html_to_text(raw)
            excerpt = _extract_relevant_excerpt(text, query)
            parser_status = "parsed" if text else "table_parse_failed"
            validation_status = (
                "validated" if excerpt else "claim_not_disclosed"
            )
            parsed_without_claim = parsed_without_claim or bool(text and not excerpt)
            source_id = _stable_id(
                "retrieved-sec",
                filing.accession,
                filing.report_date,
                query,
                excerpt,
            )
            records.append(NormalizedSourceRecord(
                source_id=source_id,
                ticker=self.identity.ticker.upper(),
                entity_name=self.identity.name,
                security_id=self.identity.ticker.upper(),
                source_family=(
                    "historical_primary"
                    if source_type == "historical_filing"
                    else "primary_company"
                ),
                source_type=source_type,
                document_id=filing.accession,
                source_url=filing.url,
                publication_date=filing.filing_date,
                reporting_period=filing.report_date,
                section=_section_hint(query),
                page_or_table="",
                exact_excerpt=excerpt,
                metric_or_claim=query[:240],
                value=None,
                unit="",
                currency="",
                source_tier=1,
                origin_group="sec.gov",
                licensing_policy="public_filing_excerpt",
                retrieval_status=(
                    "cached_document" if was_cached else "fetched_live"
                ),
                parser_status=parser_status,
                validation_status=validation_status,
                citation_id=_stable_id("citation", filing.accession, excerpt),
                accession=filing.accession,
                form=filing.form,
                content_hash=content_hash,
                observed_at=_utc_now(),
                failure_code=(
                    "" if excerpt else "claim_not_disclosed"
                ),
                message=(
                    "Exact query-relevant excerpt was parsed and citation-bound."
                    if excerpt
                    else "Document was parsed, but the requested claim was not disclosed."
                ),
            ))
            document_fetcher = getattr(
                self.sec_client, "get_filing_documents", None
            )
            if (
                callable(document_fetcher)
                and len(records) < limit
                and _query_needs_exhibits(query)
            ):
                try:
                    documents = document_fetcher(filing)
                except Exception:
                    documents = []
                for document in _relevant_sec_documents(documents)[:3]:
                    document_url = str(document.get("url") or "")
                    if not document_url:
                        continue
                    was_document_cached = bool(
                        getattr(
                            self.sec_client,
                            "is_text_cached",
                            lambda _url: False,
                        )(document_url)
                    )
                    try:
                        document_raw = self.sec_client.get_text(
                            document_url,
                            ttl_seconds=7 * 24 * 60 * 60,
                        )
                    except Exception:
                        continue
                    document_text = html_to_text(document_raw)
                    document_excerpt = _extract_relevant_excerpt(
                        document_text, query
                    )
                    if not document_excerpt:
                        continue
                    document_name = str(document.get("name") or "")
                    records.append(NormalizedSourceRecord(
                        source_id=_stable_id(
                            "retrieved-sec-exhibit",
                            filing.accession,
                            document_name,
                            query,
                            document_excerpt,
                        ),
                        ticker=self.identity.ticker.upper(),
                        entity_name=self.identity.name,
                        security_id=self.identity.ticker.upper(),
                        source_family="historical_primary",
                        source_type=source_type,
                        document_id=(
                            f"{filing.accession}:{document_name}"
                        ),
                        source_url=document_url,
                        publication_date=filing.filing_date,
                        reporting_period=filing.report_date,
                        section=str(
                            document.get("type")
                            or "Filed exhibit"
                        ),
                        page_or_table=document_name,
                        exact_excerpt=document_excerpt,
                        metric_or_claim=query[:240],
                        value=None,
                        unit="",
                        currency="",
                        source_tier=1,
                        origin_group="sec.gov",
                        licensing_policy="public_filing_excerpt",
                        retrieval_status=(
                            "cached_document"
                            if was_document_cached else "fetched_live"
                        ),
                        parser_status="parsed",
                        validation_status="validated",
                        citation_id=_stable_id(
                            "citation",
                            filing.accession,
                            document_name,
                            document_excerpt,
                        ),
                        accession=filing.accession,
                        form=filing.form,
                        content_hash=hashlib.sha256(
                            document_raw.encode("utf-8")
                        ).hexdigest(),
                        observed_at=_utc_now(),
                    ))
                    if len(records) >= limit:
                        break
            if len(records) >= limit:
                break
        validated = [
            record for record in records
            if record.validation_status == "validated"
        ]
        if validated:
            return validated, "validated", ""
        if records and parsed_without_claim:
            return records, "parsed", "claim_not_disclosed"
        return [], "failed", "document_fetch_failed"

    def _retrieve_registered_documents(
        self,
        source_type: str,
        query: str,
        period: str,
        limit: int,
    ) -> tuple[list[NormalizedSourceRecord], str, str]:
        matched = self._catalog_records(source_type, query, period, limit)
        if not matched:
            matched = self._registered_static_records(
                source_type,
                query,
                period,
                limit,
            )
        if not matched:
            return [], (
                "licensed_manual_required"
                if source_type == "management_press_interview"
                else "unavailable"
            ), (
                "licensed_manual_required"
                if source_type == "management_press_interview"
                else "document_not_found"
            )
        validated = [
            item for item in matched
            if item.validation_status == "validated"
        ]
        if validated:
            return validated, "catalog_match", ""
        if self.fetch_text is None:
            return matched, "metadata_only", "underlying_content_not_fetched"
        fetched: list[NormalizedSourceRecord] = []
        for record in matched:
            if not record.source_url or not _registered_url(
                record.source_url, matched
            ):
                continue
            try:
                raw = self.fetch_text(record.source_url)
            except Exception:
                continue
            raw_bytes = (
                raw if isinstance(raw, bytes)
                else raw.encode("utf-8", errors="ignore")
            )
            content_hash = hashlib.sha256(raw_bytes).hexdigest()
            normalized_document = normalize_document_content(
                raw,
                source_url=record.source_url,
            )
            text = normalized_document.text
            excerpt = _extract_relevant_excerpt(text, query)
            fetched.append(NormalizedSourceRecord(
                **{
                    **asdict(record),
                    "source_id": _stable_id(
                        "retrieved-registered",
                        record.document_id,
                        query,
                        excerpt,
                    ),
                    "exact_excerpt": excerpt,
                    "content_hash": content_hash,
                    "retrieval_status": "fetched_live",
                    "parser_status": normalized_document.parser_status,
                    "validation_status": (
                        "validated" if excerpt else "claim_not_disclosed"
                    ),
                    "observed_at": _utc_now(),
                    "failure_code": (
                        ""
                        if excerpt else
                        "ocr_required"
                        if normalized_document.parser_status == "ocr_required"
                        else "claim_not_disclosed"
                    ),
                    "message": (
                        "Exact query-relevant excerpt was parsed and citation-bound."
                        if excerpt else
                        "; ".join(normalized_document.data_gaps)
                        or "Document parsed, but requested claim was not disclosed."
                    ),
                }
            ))
        validated_fetched = [
            item for item in fetched
            if item.validation_status == "validated"
        ]
        if validated_fetched:
            return validated_fetched, "validated", ""
        return fetched or matched, "parsed", "claim_not_disclosed"

    def _registered_static_records(
        self,
        source_type: str,
        query: str,
        period: str,
        limit: int,
    ) -> list[NormalizedSourceRecord]:
        compatible = set(_compatible_source_types(source_type))
        query_terms = {
            term for term in _tokens(query)
            if len(term) >= 4 and term not in _RETRIEVAL_STOPWORDS
        }
        candidates: list[tuple[int, str, NormalizedSourceRecord]] = []
        for record in self.registered_source_catalog:
            if record.source_type not in compatible:
                continue
            if period and not _periods_overlap(
                period,
                " ".join((
                    record.reporting_period,
                    record.publication_date,
                    record.exact_excerpt,
                )),
            ):
                continue
            record_terms = set(_tokens(" ".join((
                record.metric_or_claim,
                record.section,
                record.exact_excerpt,
            ))))
            overlap = len(query_terms & record_terms)
            if query_terms and overlap == 0:
                continue
            candidates.append((
                overlap,
                record.publication_date,
                record,
            ))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in candidates[:limit]]

    def _retrieve_prices(
        self,
        query: str,
        period: str,
        limit: int,
    ) -> tuple[list[NormalizedSourceRecord], str, str]:
        if self.price_client is None:
            return [], "unavailable", "price_client_unavailable"
        fetcher = getattr(self.price_client, "historical_daily_rows", None)
        if not callable(fetcher):
            return [], "unavailable", "historical_price_adapter_unavailable"
        start, end = _period_bounds(period or query)
        result = fetcher(self.identity.ticker, start=start, end=end)
        rows = list(result.get("rows") or []) if isinstance(result, dict) else []
        if not rows:
            return [], "unavailable", str(
                result.get("status") if isinstance(result, dict) else "no_data"
            )
        selected = _sample_price_rows(rows, limit=max(2, limit))
        records = []
        for row in selected:
            row_date = str(row.get("date") or "")
            close = _optional_float(row.get("close"))
            if not row_date or close is None:
                continue
            records.append(NormalizedSourceRecord(
                source_id=_stable_id(
                    "historical-price",
                    self.identity.ticker,
                    row_date,
                    close,
                ),
                ticker=self.identity.ticker.upper(),
                entity_name=self.identity.name,
                security_id=self.identity.ticker.upper(),
                source_family="historical_quantitative",
                source_type="quantitative_historical_price",
                document_id=f"{self.identity.ticker}:{row_date}",
                source_url=str(result.get("source_url") or ""),
                publication_date=row_date,
                reporting_period=row_date,
                section="Adjusted daily price",
                page_or_table="daily_bar",
                exact_excerpt=(
                    f"Adjusted close {close:g}; volume "
                    f"{_optional_float(row.get('volume')) or 0:g}."
                ),
                metric_or_claim="Adjusted Close",
                value=close,
                unit="price",
                currency="",
                source_tier=2 if result.get("official", True) else 3,
                origin_group=str(result.get("provider") or "price_provider"),
                licensing_policy="derived_observation_only",
                retrieval_status=str(
                    result.get("retrieval_status") or "cached_document"
                ),
                parser_status="parsed",
                validation_status="validated",
                citation_id=_stable_id(
                    "price-citation", self.identity.ticker, row_date
                ),
                content_hash=_stable_id("price-row", row),
                observed_at=_utc_now(),
            ))
        return records, "validated" if records else "unavailable", (
            "" if records else "no_data"
        )

    def _catalog_records(
        self,
        source_type: str,
        query: str,
        period: str,
        limit: int,
    ) -> list[NormalizedSourceRecord]:
        by_id = {record.source_id: record for record in self.records}
        compatible_types = _compatible_source_types(source_type)
        matched_evidence: list[dict[str, Any]] = []
        for compatible_type in compatible_types:
            matched_evidence.extend(search_evidence_catalog(
                self.catalog,
                query,
                source_type=compatible_type,
                period=period,
                limit=limit,
            ))
        matched_evidence = _dedupe_evidence_items(
            matched_evidence
        )[:limit]
        ids = [str(item.get("evidence_id") or "") for item in matched_evidence]
        if not ids and not query.strip():
            ids = [
                record.source_id for record in self.records
                if record.source_type in compatible_types
                and (not period or period in record.reporting_period)
            ][:limit]
        return [by_id[item] for item in ids if item in by_id]


def build_historical_financial_paths(
    ticker: str,
    metrics: Iterable[FinancialMetric],
) -> list[HistoricalFinancialPath]:
    grouped: dict[str, list[FinancialMetric]] = {}
    for metric in metrics:
        grouped.setdefault(metric.name, []).append(metric)
    paths: list[HistoricalFinancialPath] = []
    for name, rows in sorted(grouped.items()):
        observations: list[dict[str, Any]] = []
        for metric in sorted(rows, key=lambda item: item.period_end):
            evidence_id = _financial_metric_evidence_id(ticker, metric)
            observations.append(_metric_observation(
                metric,
                "reported",
                source_id=evidence_id,
            ))
            if metric.previous_value is not None and metric.previous_period_end:
                observations.append({
                    "period": metric.previous_period_end,
                    "value": metric.previous_value,
                    "unit": metric.unit,
                    "filed": "",
                    "source_url": metric.source_url or "",
                    "source_kind": metric.source_kind,
                    "basis": "reported_prior_comparable",
                    "no_lookahead_safe": True,
                    "source_id": f"{evidence_id}:prior",
                })
        observations = _dedupe_observations(observations)
        paths.append(HistoricalFinancialPath(
            ticker=ticker.upper(),
            metric=name,
            status="available" if observations else "unavailable",
            observations=observations,
            source_ids=[
                str(item.get("source_id") or "")
                for item in observations
                if item.get("source_id")
            ],
            no_lookahead_safe=all(
                item.get("no_lookahead_safe", False)
                for item in observations
            ),
        ))
    fcf_path = _build_ttm_fcf_path(ticker, grouped)
    if fcf_path:
        paths.append(fcf_path)
    return paths


def build_historical_valuation_observations(
    ticker: str,
    paths: Iterable[HistoricalFinancialPath],
    price_records: Iterable[NormalizedSourceRecord],
) -> list[HistoricalValuationObservation]:
    path_map = {path.metric.casefold(): path for path in paths}
    prices = sorted(
        (
            record for record in price_records
            if record.metric_or_claim.casefold() == "adjusted close"
            and isinstance(record.value, (int, float))
        ),
        key=lambda item: item.reporting_period,
    )
    observations: list[HistoricalValuationObservation] = []
    for price_record in prices:
        as_of = price_record.reporting_period
        shares = _path_value_as_of(path_map, ("diluted shares", "shares"), as_of)
        revenue = _path_value_as_of(path_map, ("revenue",), as_of)
        earnings = _path_value_as_of(path_map, ("net income",), as_of)
        fcf = _path_value_as_of(path_map, ("free cash flow ttm",), as_of)
        cash = _path_value_as_of(path_map, ("cash",), as_of)
        debt = _path_value_as_of(
            path_map, ("long-term debt", "current debt", "debt"), as_of
        )
        price = float(price_record.value)
        market_cap = price * shares if shares is not None else None
        enterprise_value = (
            market_cap + (debt or 0) - (cash or 0)
            if market_cap is not None else None
        )
        gaps = []
        if shares is None:
            gaps.append("Diluted share count was unavailable as of the price date.")
        if fcf is None:
            gaps.append("Validated TTM FCF was unavailable as of the price date.")
        observations.append(HistoricalValuationObservation(
            ticker=ticker.upper(),
            observation_date=as_of,
            price=price,
            diluted_shares=shares,
            market_cap=market_cap,
            enterprise_value=enterprise_value,
            revenue_ttm=revenue,
            earnings_ttm=earnings,
            fcf_ttm=fcf,
            cash=cash,
            debt=debt,
            currency=price_record.currency,
            formula_traces={
                "market_cap": "adjusted close x diluted shares",
                "enterprise_value": "market cap + debt - cash",
                "fcf_yield": "TTM FCF / market cap",
            },
            source_ids=[price_record.source_id],
            confidence="Medium" if not gaps else "Low",
            no_lookahead_safe=True,
            data_gaps=gaps,
        ))
    return observations


def extract_contractual_exposures(
    ticker: str,
    records: Iterable[NormalizedSourceRecord],
) -> list[ContractualExposureObservation]:
    observations: list[ContractualExposureObservation] = []
    for record in records:
        if record.validation_status != "validated" or not record.exact_excerpt:
            continue
        lowered = record.exact_excerpt.casefold()
        for exposure_type, terms in _CONTRACTUAL_PATTERNS.items():
            matched_term = next((term for term in terms if term in lowered), None)
            if not matched_term:
                continue
            amount, currency, unit = _extract_amount(record.exact_excerpt)
            observations.append(ContractualExposureObservation(
                exposure_id=_stable_id(
                    "contractual-exposure",
                    ticker,
                    record.source_id,
                    exposure_type,
                ),
                ticker=ticker.upper(),
                exposure_type=exposure_type,
                status="disclosed",
                description=record.exact_excerpt[:1_000],
                amount=amount,
                unit=unit,
                currency=currency or record.currency,
                period=record.reporting_period,
                source_ids=[record.source_id],
                confidence="High" if record.source_tier == 1 else "Medium",
            ))
    return _dedupe_dataclasses(observations, "exposure_id")


def build_regulatory_observations(
    ticker: str,
    records: Iterable[NormalizedSourceRecord],
) -> list[RegulatoryEventObservation]:
    observations = []
    for record in records:
        if (
            canonical_source_type(record.source_type) != "regulator_court"
            or record.validation_status != "validated"
        ):
            continue
        observations.append(RegulatoryEventObservation(
            event_id=_stable_id(
                "regulatory-event", ticker, record.source_id
            ),
            ticker=ticker.upper(),
            proceeding=record.metric_or_claim or record.section,
            event_date=record.publication_date,
            jurisdiction=_jurisdiction_from_url(record.source_url),
            status="reported_officially",
            affected_business=record.section,
            financial_mechanism="Not established from announcement alone.",
            source_ids=[record.source_id],
            confidence="High" if record.source_tier == 1 else "Medium",
            data_gaps=[
                "Quantified economic impact requires issuer, remedy, or financial evidence."
            ],
        ))
    return observations


def audit_artifact_claims(
    ticker: str,
    artifact: ResearchRoundArtifact | None,
    records: Iterable[NormalizedSourceRecord],
) -> tuple[list[ClaimEvidenceAudit], AuditRepairQueue]:
    record_map = {
        record.source_id: record for record in records
    }
    claims = _material_claims(
        artifact.payload if artifact is not None else {}
    )
    audits: list[ClaimEvidenceAudit] = []
    repairs: list[AuditRepairItem] = []
    for index, claim in enumerate(claims):
        claim_text = str(claim.get("text") or "").strip()
        if not claim_text:
            continue
        evidence_ids = [
            str(value)
            for value in claim.get("evidence_ids", [])
            if value
        ]
        supporting = [
            record_map[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id in record_map
            and record_map[evidence_id].validation_status == "validated"
            and record_map[evidence_id].retrieval_status != "metadata_only"
        ]
        classification = _claim_classification(
            str(claim.get("field") or ""), claim_text
        )
        materiality = str(claim.get("materiality") or "Material")
        blockers: list[str] = []
        if not supporting:
            blockers.append(
                "No fetched, parsed, citation-bound, validated evidence supports the claim."
            )
        period_alignment = _claim_period_alignment(claim_text, supporting)
        if supporting and period_alignment == "mismatch":
            blockers.append("Claim and supporting evidence periods do not align.")
        duration_alignment = _claim_duration_alignment(
            claim_text, supporting
        )
        if duration_alignment == "mismatch":
            blockers.append(
                "Claim and supporting evidence fiscal durations do not align."
            )
        elif duration_alignment == "unknown":
            blockers.append(
                "Claim states a fiscal duration that the cited evidence does "
                "not establish."
            )
        entity_alignment = (
            "aligned"
            if supporting and all(
                record.ticker.upper() == ticker.upper() for record in supporting
            )
            else "mismatch" if supporting else "unknown"
        )
        if entity_alignment == "mismatch":
            blockers.append("Claim and supporting evidence entities do not align.")
        origins = {
            record.origin_group for record in supporting
            if record.origin_group
        }
        numeric_status = _numeric_reconciliation(claim_text, supporting)
        if numeric_status == "failed":
            blockers.append(
                "A numerical or dated claim is not present in the cited evidence."
            )
        requires_two_origins = (
            str(claim.get("field") or "") in {
                "operating_outcome",
                "financing_outcome",
                "valuation_outcome",
                "price_outcome",
            }
            and classification == "factual"
        )
        if requires_two_origins and len(origins) < 2:
            blockers.append(
                "Material historical outcome has fewer than two independent source origins."
            )
        status = "passed" if not blockers else (
            "qualitative_required"
            if supporting and classification in {"inference", "hypothesis"}
            else "failed"
        )
        claim_id = str(
            claim.get("claim_id")
            or _stable_id("claim-audit", ticker, index, claim_text)
        )
        audit = ClaimEvidenceAudit(
            claim_id=claim_id,
            claim_text=claim_text,
            classification=classification,
            materiality=materiality,
            claim_type=str(claim.get("field") or ""),
            required_evidence_fields=_required_claim_fields(
                str(claim.get("field") or ""), classification
            ),
            supporting_evidence_ids=[
                record.source_id for record in supporting
            ],
            contradicting_evidence_ids=[],
            period_alignment=period_alignment,
            entity_alignment=entity_alignment,
            independent_origin_count=len(origins),
            numerical_reconciliation=numeric_status,
            status=status,
            blockers=blockers,
        )
        audits.append(audit)
        if blockers:
            source_type, metric = _repair_need(
                str(claim.get("field") or ""), claim_text
            )
            repairs.append(AuditRepairItem(
                repair_id=_stable_id("audit-repair", claim_id, source_type),
                claim_id=claim_id,
                rejection_reason="; ".join(blockers),
                missing_source_type=source_type,
                missing_metric=metric,
                retrieval_request={
                    "request_id": _stable_id(
                        "audit-repair-request", claim_id, source_type
                    ),
                    "source_type": source_type,
                    "fallback_source_types": _repair_source_fallbacks(
                        source_type
                    ),
                    "title": f"Repair evidence for: {claim_text[:120]}",
                    "reason_to_inspect": "; ".join(blockers),
                    "expected_evidence_type": (
                        "Exact period-aligned excerpt or quantitative observation "
                        "with source, date, unit, and citation."
                    ),
                    "period": _claim_request_period(claim_text),
                    "priority": "High",
                    "confirms_or_disproves": claim_text[:500],
                },
            ))
    queue = AuditRepairQueue(
        ticker=ticker.upper(),
        status="open" if repairs else "closed",
        items=repairs,
        critical_open_count=sum(
            audit.status == "failed" and audit.materiality == "Critical"
            for audit in audits
        ),
        generated_at=_utc_now(),
        data_gaps=(
            ["No model-authored material claims were available to audit."]
            if artifact is None else []
        ),
    )
    return audits, queue


def _claim_request_period(claim_text: str) -> str:
    dates = re.findall(
        r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b",
        str(claim_text or ""),
    )
    if dates:
        return (
            dates[0] if len(dates) == 1
            else f"{min(dates)} to {max(dates)}"
        )
    years = sorted(set(_YEAR_RE.findall(str(claim_text or ""))))
    if not years:
        return ""
    return years[0] if len(years) == 1 else f"{years[0]} to {years[-1]}"


def execute_audit_repair_queue(
    queue: AuditRepairQueue,
    retriever: AuditableEvidenceRetriever,
) -> tuple[list[NormalizedSourceRecord], list[SourceRetrievalLifecycle]]:
    """Execute repair work orders through registered deterministic adapters."""
    records: list[NormalizedSourceRecord] = []
    lifecycles: list[SourceRetrievalLifecycle] = []
    for item in queue.items:
        request = item.retrieval_request
        if request.get("repair_action") == "rewrite_or_remove":
            item.retrieval_status = "not_applicable"
            item.resolution = "rewrite_or_remove"
            continue
        source_types = [
            str(request.get("source_type") or item.missing_source_type),
            *[
                str(value)
                for value in request.get("fallback_source_types", [])
                if value
            ],
        ]
        source_types = list(dict.fromkeys(
            canonical_source_type(value) for value in source_types if value
        ))
        item_records: list[NormalizedSourceRecord] = []
        item_lifecycles: list[SourceRetrievalLifecycle] = []
        for source_index, source_type in enumerate(source_types):
            retrieved, lifecycle = retriever.retrieve(
                request_id=(
                    str(request.get("request_id") or item.repair_id)
                    if source_index == 0
                    else (
                        f"{request.get('request_id') or item.repair_id}"
                        f":fallback:{source_type}"
                    )
                ),
                source_type=source_type,
                query=" ".join((
                    str(request.get("title") or ""),
                    str(request.get("reason_to_inspect") or ""),
                    str(request.get("confirms_or_disproves") or ""),
                )),
                period=str(request.get("period") or ""),
                result_limit=int(request.get("result_limit") or 12),
            )
            item_records.extend(retrieved)
            item_lifecycles.append(lifecycle)
            if any(
                record.validation_status == "validated"
                for record in retrieved
            ):
                break
        records.extend(item_records)
        lifecycles.extend(item_lifecycles)
        item.retrieval_status = (
            "validated"
            if any(
                record.validation_status == "validated"
                for record in item_records
            )
            else item_lifecycles[-1].status
            if item_lifecycles else "unavailable"
        )
        item.retrieval_outcome_ids = [
            record.source_id for record in item_records
        ]
        item.resolution = (
            "evidence_added"
            if any(
                record.validation_status == "validated"
                for record in item_records
            )
            else "licensed_manual_required"
            if any(
                lifecycle.status == "licensed_manual_required"
                for lifecycle in item_lifecycles
            )
            else "genuinely_unavailable"
            if item_lifecycles and all(
                lifecycle.status == "unavailable"
                for lifecycle in item_lifecycles
            )
            else "unresolved"
        )
    queue.status = (
        "evidence_added"
        if any(item.resolution == "evidence_added" for item in queue.items)
        else "manual_required"
        if any(
            item.resolution == "licensed_manual_required"
            for item in queue.items
        )
        else "unresolved"
    )
    return _dedupe_records(records), lifecycles


def evaluate_demo_freeze(
    ticker: str,
    *,
    run: Any,
    audited_dossiers: Iterable[Any],
    output_path: str,
    policy: DemoQualityPolicy | None = None,
) -> DemoFreezeDecision:
    policy = policy or DemoQualityPolicy()
    dossiers = list(audited_dossiers)
    evaluation = PublicationGateEvaluator().evaluate(
        ticker,
        run=run,
        audited_dossiers=dossiers,
        policy=policy,
    )
    blockers = [
        _legacy_publication_blocker(failure, ticker.upper(), policy)
        for failure in evaluation.failures
    ]
    allowed = evaluation.publishable
    failed_claim_ids = [
        item.claim_id for item in getattr(run, "claim_evidence_audits", [])
        if item.status == "failed"
    ]
    next_actions = [action.objective for action in evaluation.repair_actions]
    queue = getattr(run, "audit_repair_queue", None)
    if queue:
        next_actions.extend(
            (
                f"{item.missing_source_type}: {item.rejection_reason}"
            )
            for item in queue.items[:10]
            if f"{item.missing_source_type}: {item.rejection_reason}" not in next_actions
        )
    return DemoFreezeDecision(
        ticker=ticker.upper(),
        status="pass" if allowed else "blocked",
        allowed=allowed,
        evaluated_at=_utc_now(),
        failed_claim_ids=failed_claim_ids,
        blockers=blockers,
        next_actions=next_actions,
        output_path=output_path,
        evidence_hash=str(getattr(run, "evidence_hash", "")),
        validation_status=str(
            getattr(run, "deterministic_validation_status", "not_run")
        ),
        source_run_id=str(getattr(run, "run_id", "") or ""),
        source_run_completed_at=str(getattr(run, "completed_at", "") or ""),
        qa_closure_state=_resolved_qa_closure_state(run),
        policy_version=policy.policy_version,
        artifact_schema_version=policy.artifact_schema_version,
        sanitization_status="pending",
        research_profile=str(
            getattr(getattr(run, "profile", None), "profile_id", "") or ""
        ),
        source_policy_summary=(
            f"registry={getattr(run, 'source_registry_version', '')}; "
            f"evidence={getattr(run, 'evidence_hash', '')}"
        ),
        publication_gate_evaluation=evaluation,
        repair_actions=list(evaluation.repair_actions),
    )


def _resolved_qa_closure_state(run: Any) -> str:
    """Read both current and historical closure representations safely."""
    final_artifact = getattr(run, "final_artifact", None)
    payload = getattr(final_artifact, "payload", None)
    if isinstance(payload, dict):
        state = str(payload.get("closure_state") or "")
        if state:
            return state
        closure = payload.get("closure_audit")
        if isinstance(closure, dict) and closure.get("closure_state"):
            return str(closure["closure_state"])
    for variant in getattr(run, "variants", []) or []:
        decision = getattr(variant, "audit_decision", None)
        if decision is not None and getattr(decision, "status", "") == "passed":
            return "closed"
    return "failed_material_findings_unaddressed"


def _legacy_publication_blocker(
    failure: Any,
    ticker: str,
    policy: DemoQualityPolicy,
) -> str:
    """Keep stored/demo compatibility while typed failures power new UI."""

    code = str(getattr(failure, "code", ""))
    if code == "run_incomplete":
        return "Hybrid research run is not completed."
    if code in {"deterministic_validation_not_run", "deterministic_validation_partial"}:
        return "Deterministic validation did not pass."
    if code == "final_closure_not_passed":
        return "DeepSeek-led dossier did not pass the final Sol audit."
    if code == "semantic_quality_below_threshold":
        threshold = policy.ticker_minimum_scores.get(ticker, 85)
        return (
            f"{ticker} DeepSeek-led dossier did not pass the {threshold}/100 "
            "semantic quality gate without critical failures."
        )
    if code == "material_claim_validation_failed":
        count = len(getattr(failure, "related_claim_ids", ()) or ())
        return f"{count} material claim(s) failed claim-level evidence audit."
    if code == "material_audit_finding_unaddressed":
        count = len(getattr(failure, "related_finding_ids", ()) or ())
        return f"{count} material QA finding(s) remain unresolved."
    if code == "analog_independent_provenance_insufficient":
        analog_id = str(getattr(failure, "scope_id", "") or "")
        if analog_id:
            return (
                f"Quantitative dossier {analog_id} has fewer than two "
                "independent validated source origins."
            )
    if code == "required_financial_path_missing":
        return (
            f"Required {ticker} historical paths are incomplete: "
            f"{getattr(failure, 'scope_id', '')}."
        )
    return str(getattr(failure, "summary", "Publication gate did not pass."))


def partial_rerun_eligible(
    parent_fingerprints: dict[str, str],
    current_fingerprints: dict[str, str],
) -> tuple[bool, list[str]]:
    if not parent_fingerprints:
        return False, []
    for evidence_id, fingerprint in parent_fingerprints.items():
        if current_fingerprints.get(evidence_id) != fingerprint:
            return False, []
    additions = sorted(set(current_fingerprints) - set(parent_fingerprints))
    # An unchanged evidence snapshot may still rerun reconciliation after an
    # approved citation/schema rewrite. Changed or removed facts remain
    # ineligible; additive validated evidence is reported explicitly.
    return True, additions


def _build_ttm_fcf_path(
    ticker: str,
    grouped: dict[str, list[FinancialMetric]],
) -> HistoricalFinancialPath | None:
    operating = _metric_group(
        grouped, "operating cash flow", "net cash provided by operating activities"
    )
    capex = _metric_group(
        grouped, "capital expenditure", "payments to acquire property plant and equipment"
    )
    ocf_ttm = _latest_validated_ttm(operating)
    capex_ttm = _latest_validated_ttm(capex)
    if ocf_ttm is None or capex_ttm is None:
        return HistoricalFinancialPath(
            ticker=ticker.upper(),
            metric="Free Cash Flow TTM",
            status="unavailable",
            formula="TTM operating cash flow - TTM capital expenditure",
            data_gaps=[
                "Four aligned quarters or validated TTM values for operating cash flow and capex were not both available."
            ],
        )
    period = max(ocf_ttm[0], capex_ttm[0])
    filed = max(ocf_ttm[4], capex_ttm[4])
    value = ocf_ttm[1] - abs(capex_ttm[1])
    return HistoricalFinancialPath(
        ticker=ticker.upper(),
        metric="Free Cash Flow TTM",
        status="available",
        observations=[{
            "period": period,
            "value": value,
            "unit": ocf_ttm[2],
            "filed": filed,
            "basis": "derived_ttm",
            "components": {
                "operating_cash_flow_ttm": ocf_ttm[1],
                "capital_expenditure_ttm": capex_ttm[1],
            },
            "no_lookahead_safe": True,
        }],
        formula="TTM operating cash flow - abs(TTM capital expenditure)",
        source_ids=[ocf_ttm[3], capex_ttm[3]],
        no_lookahead_safe=True,
    )


def _latest_validated_ttm(
    metrics: list[FinancialMetric],
) -> tuple[str, float, str, str, str] | None:
    candidates = [
        metric for metric in metrics
        if metric.trailing_twelve_month_value is not None
        and metric.trailing_period_end
    ]
    if not candidates:
        return None
    metric = max(candidates, key=lambda item: item.trailing_period_end or "")
    return (
        str(metric.trailing_period_end),
        float(metric.trailing_twelve_month_value),
        metric.unit,
        _stable_id(
            "ttm-component",
            metric.name,
            metric.trailing_period_end,
            metric.trailing_twelve_month_value,
        ),
        str(metric.filed or metric.trailing_period_end),
    )


def _metric_group(
    grouped: dict[str, list[FinancialMetric]],
    *aliases: str,
) -> list[FinancialMetric]:
    result = []
    for name, values in grouped.items():
        normalized = name.casefold()
        if any(alias in normalized for alias in aliases):
            result.extend(values)
    return result


def _metric_observation(
    metric: FinancialMetric,
    basis: str,
    *,
    source_id: str = "",
) -> dict[str, Any]:
    return {
        "period": metric.period_end,
        "value": metric.value,
        "unit": metric.unit,
        "filed": metric.filed or "",
        "source_url": metric.source_url or "",
        "accession": metric.accession or "",
        "source_kind": metric.source_kind,
        "basis": basis,
        "no_lookahead_safe": True,
        "source_id": source_id,
    }


def _financial_metric_evidence_id(
    ticker: str,
    metric: FinancialMetric,
) -> str:
    return _stable_id(
        "metric",
        ticker.upper(),
        metric.name,
        metric.period_end,
        metric.fiscal_period,
        metric.form,
    )


def _path_value_as_of(
    paths: dict[str, HistoricalFinancialPath],
    aliases: tuple[str, ...],
    as_of: str,
) -> float | None:
    values = []
    for name, path in paths.items():
        if not any(alias in name for alias in aliases):
            continue
        for item in path.observations:
            period = str(item.get("period") or "")
            filed = str(item.get("filed") or "")
            value = _optional_float(item.get("value"))
            available_date = filed or (
                ""
                if item.get("basis") == "reported_prior_comparable"
                else period
            )
            if (
                period
                and available_date
                and available_date <= as_of
                and value is not None
            ):
                values.append((period, value))
    return max(values, default=(None, None))[1]


def _material_claims(payload: Any) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    dossiers = payload.get("historical_analog_dossiers", []) if isinstance(payload, dict) else []
    for dossier_index, dossier in enumerate(dossiers if isinstance(dossiers, list) else []):
        if not isinstance(dossier, dict):
            continue
        source_ids = _string_list(
            dossier.get("source_ids") or dossier.get("evidence_ids")
        )
        field_evidence: dict[str, list[tuple[str, list[str]]]] = {}
        for claim_item in dossier.get("claim_evidence", []) or []:
            if not isinstance(claim_item, dict):
                continue
            field = str(claim_item.get("field") or "").strip()
            if not field:
                continue
            field_evidence.setdefault(field, []).append((
                str(
                    claim_item.get("claim_text")
                    or claim_item.get("claim")
                    or ""
                ).strip(),
                _string_list(claim_item.get("evidence_ids")),
            ))

        def evidence_for(field: str, text: str) -> list[str]:
            candidates = field_evidence.get(field, [])
            for claim_text, evidence_ids in candidates:
                if claim_text and claim_text == text:
                    return list(dict.fromkeys(evidence_ids))
            if len(candidates) == 1:
                return list(dict.fromkeys(candidates[0][1]))
            return source_ids

        for field in (
            "operating_outcome",
            "financing_outcome",
            "valuation_outcome",
            "price_outcome",
            "macro_context",
            "competitive_context",
        ):
            text = str(dossier.get(field) or "").strip()
            if text and not text.lower().startswith("not established"):
                claims.append({
                    "claim_id": _stable_id(
                        "dossier-claim", dossier_index, field, text
                    ),
                    "field": field,
                    "text": text,
                    "evidence_ids": evidence_for(field, text),
                    "materiality": (
                        "Critical"
                        if field in {
                            "financing_outcome",
                            "valuation_outcome",
                            "price_outcome",
                        }
                        else "Material"
                    ),
                })
        for field in (
            "triggering_decisions",
            "hidden_risks",
            "material_differences",
        ):
            for item_index, text in enumerate(_string_list(dossier.get(field))):
                claims.append({
                    "claim_id": _stable_id(
                        "dossier-list-claim",
                        dossier_index,
                        field,
                        item_index,
                        text,
                    ),
                    "field": field,
                    "text": text,
                    "evidence_ids": evidence_for(field, text),
                    "materiality": (
                        "Critical" if field == "hidden_risks" else "Material"
                    ),
                })
    if isinstance(payload, dict):
        for index, item in enumerate(payload.get("claims", []) or []):
            if not isinstance(item, dict):
                continue
            text = str(
                item.get("claim_text")
                or item.get("claim")
                or item.get("statement")
                or item.get("summary")
                or ""
            ).strip()
            if text:
                claims.append({
                    "claim_id": str(
                        item.get("claim_id")
                        or _stable_id("model-claim", index, text)
                    ),
                    "field": str(item.get("claim_type") or "claim"),
                    "text": text,
                    "evidence_ids": sorted({
                        evidence_id
                        for evidence_id in _collect_nested_string_values(
                            item, "evidence_ids"
                        )
                        if evidence_id
                    }),
                    "materiality": str(item.get("materiality") or "Material"),
                })
        for index, item in enumerate(
            payload.get("rejected_claims", []) or []
        ):
            if not isinstance(item, dict):
                continue
            text = str(item.get("claim") or "").strip()
            if not text:
                continue
            claims.append({
                "claim_id": _stable_id(
                    "rejected-model-claim", index, text
                ),
                "field": "rejected_unsupported_claim",
                "text": text,
                "evidence_ids": [],
                "materiality": "Critical",
            })
    return claims


def _claim_classification(field: str, text: str) -> str:
    lowered = text.casefold()
    if any(token in lowered for token in ("may ", "could ", "suggests", "appears")):
        return "inference"
    if field in {"lessons", "what_it_suggests"}:
        return "hypothesis"
    if _NUMBER_RE.search(text) or _YEAR_RE.search(text):
        return "factual"
    return "factual" if field.endswith("_outcome") else "inference"


def _claim_period_alignment(
    text: str,
    records: list[NormalizedSourceRecord],
) -> str:
    if not records:
        return "unknown"
    claim_dates = set(re.findall(
        r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b", text
    ))
    for value in re.findall(
        (
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
            r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
            r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
            r"\d{1,2},?\s+(?:19|20)\d{2}\b"
        ),
        text,
        flags=re.IGNORECASE,
    ):
        for pattern in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
            try:
                claim_dates.add(
                    datetime.strptime(value.replace(",", ","), pattern)
                    .date()
                    .isoformat()
                )
                break
            except ValueError:
                continue
    if claim_dates:
        source_periods = {
            value
            for record in records
            for value in (
                record.reporting_period,
                record.period_start,
            )
            if value
        }
        return (
            "aligned"
            if claim_dates & source_periods
            else "mismatch"
        )
    years = set(_YEAR_RE.findall(text))
    if not years:
        return "aligned" if any(record.reporting_period for record in records) else "unknown"
    source_years = {
        year
        for record in records
        for year in _YEAR_RE.findall(
            " ".join((
                record.reporting_period,
                record.publication_date,
                record.exact_excerpt,
            ))
        )
    }
    return "aligned" if years & source_years else "mismatch"


def _numeric_reconciliation(
    text: str,
    records: list[NormalizedSourceRecord],
) -> str:
    financial_text = _strip_period_tokens(text)
    numbers = _scaled_numeric_values(financial_text)
    if not numbers:
        years = set(_YEAR_RE.findall(text))
        if not years:
            return "not_applicable"
        source_text = " ".join(
            f"{record.publication_date} {record.reporting_period} "
            f"{record.exact_excerpt}"
            for record in records
        )
        return "passed" if any(year in source_text for year in years) else "failed"
    source_text = " ".join(
        f"{record.value} {record.exact_excerpt}"
        for record in records
    )
    source_values = _scaled_numeric_values(
        _strip_period_tokens(source_text)
    )
    percentage_values = {
        float(value.replace(",", ""))
        for value in re.findall(
            r"(?<![A-Za-z])(\d+(?:\.\d+)?)\s*%",
            financial_text,
        )
    }
    for number in numbers:
        if any(_numbers_close(number, source) for source in source_values):
            continue
        if (
            number in percentage_values
            and _matches_derived_percentage(number, source_values)
        ):
            continue
        return "failed"
    return "passed"


def _strip_period_tokens(text: str) -> str:
    cleaned = re.sub(
        r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b",
        " ",
        str(text or ""),
    )
    cleaned = re.sub(
        (
            r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
            r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|"
            r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+"
            r"\d{1,2},?\s+(?:19|20)\d{2}\b"
        ),
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bQ[1-4]\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\b(?:10-[KQ]|20-F|40-F|6-K|8-K)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bFY(?:19|20)?\d{2}\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:19|20)\d{2}\b", " ", cleaned)
    return cleaned


def _numbers_close(left: float, right: float) -> bool:
    tolerance = max(1e-6, abs(left) * 5e-4)
    return abs(left - right) <= tolerance


def _scaled_numeric_values(text: str) -> list[float]:
    values = []
    for match in _NUMBER_RE.finditer(str(text or "")):
        raw = match.group(0)
        value = float(raw.rstrip("%").replace(",", ""))
        suffix = str(text or "")[match.end():match.end() + 16]
        unit_match = re.match(
            r"\s*(trillion|billion|million|thousand|[TBMK])\b",
            suffix,
            flags=re.IGNORECASE,
        )
        if unit_match and not raw.endswith("%"):
            unit = unit_match.group(1).casefold()
            value *= {
                "trillion": 1e12,
                "t": 1e12,
                "billion": 1e9,
                "b": 1e9,
                "million": 1e6,
                "m": 1e6,
                "thousand": 1e3,
                "k": 1e3,
            }[unit]
        values.append(value)
    return values


def _claim_duration_alignment(
    text: str,
    records: list[NormalizedSourceRecord],
) -> str:
    lowered = str(text or "").casefold()
    target = None
    for phrase, days in (
        ("three months", 91),
        ("six months", 182),
        ("nine months", 273),
        ("twelve months", 365),
        ("year ended", 365),
    ):
        if phrase in lowered:
            target = days
            break
    if target is None:
        return "not_applicable"
    durations = [
        int(record.duration_days)
        for record in records
        if record.duration_days is not None
    ]
    if not durations:
        return "unknown"
    return (
        "aligned"
        if any(abs(duration - target) <= 20 for duration in durations)
        else "mismatch"
    )


def _matches_derived_percentage(
    target: float,
    source_values: list[float],
) -> bool:
    for numerator in source_values:
        for denominator in source_values:
            if denominator == 0 or numerator == denominator:
                continue
            derived = numerator / denominator * 100.0
            if abs(derived - target) <= 0.15:
                return True
    return False


def _required_claim_fields(field: str, classification: str) -> list[str]:
    required = ["entity", "source URL", "publication date", "exact excerpt"]
    if classification == "factual":
        required.extend(["reporting period", "citation", "validation status"])
    if field in {"valuation_outcome", "price_outcome"}:
        required.extend(["adjusted price path", "formula or return calculation"])
    if field == "financing_outcome":
        required.extend(["debt/capital-allocation metric", "unit", "currency"])
    return required


def _repair_need(field: str, text: str) -> tuple[str, str]:
    lowered = f"{field} {text}".casefold()
    if any(term in lowered for term in ("price", "valuation", "multiple", "drawdown")):
        return "quantitative_historical_price", "historical price/valuation path"
    if any(term in lowered for term in ("supplier", "commitment", "obligation", "lease")):
        return "supplier_customer_announcement", "contractual exposure"
    if any(term in lowered for term in ("regulat", "antitrust", "court", "privacy")):
        return "regulator_court", "regulatory proceeding and financial mechanism"
    if any(term in lowered for term in ("debt", "financing", "fcf", "cash", "repurchase")):
        return "historical_filing", "period-aligned financing/FCF/capital allocation"
    return "historical_filing", "exact historical claim evidence"


def _repair_source_fallbacks(source_type: str) -> list[str]:
    """Keep claim repair broad enough to find primary corroboration."""
    canonical = canonical_source_type(source_type)
    if canonical == "supplier_customer_announcement":
        return [
            "historical_filing",
            "issuer_announcement",
            "issuer_ir",
        ]
    if canonical == "regulator_court":
        return [
            "historical_filing",
            "issuer_announcement",
        ]
    if canonical == "quantitative_historical_price":
        return [
            "historical_filing",
        ]
    return []


def _collect_nested_string_values(
    value: Any,
    target_key: str,
) -> list[str]:
    values: list[str] = []
    if isinstance(value, list):
        for item in value:
            values.extend(_collect_nested_string_values(item, target_key))
    elif isinstance(value, dict):
        for key, item in value.items():
            if key == target_key:
                values.extend(_string_list(item))
            else:
                values.extend(_collect_nested_string_values(item, target_key))
    return list(dict.fromkeys(values))


def _compatible_source_types(source_type: str) -> list[str]:
    canonical = canonical_source_type(source_type)
    compatible = {
        "issuer_announcement": [
            "issuer_announcement",
            "issuer_ir",
            "issuer_management_remarks",
            "presentation",
        ],
        "issuer_ir": [
            "issuer_ir",
            "issuer_announcement",
            "issuer_management_remarks",
            "presentation",
        ],
        "institutional_history": [
            "institutional_history",
            "external_research",
        ],
        "supplier_customer_announcement": [
            "supplier_customer_announcement",
            "issuer_announcement",
            "issuer_ir",
        ],
        "regulator_court": [
            "regulator_court",
        ],
    }.get(canonical, [canonical])
    return list(dict.fromkeys(
        canonical_source_type(item) for item in compatible
    ))


def _dedupe_evidence_items(
    items: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        evidence_id = str(item.get("evidence_id") or "")
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        result.append(item)
    return result


def _load_registered_source_catalog(
    identity: CompanyIdentity,
) -> list[NormalizedSourceRecord]:
    path = Path(os.getenv(
        "INSTITUTIONAL_HISTORY_SOURCES_CSV",
        "data/institutional_history_sources.csv",
    ))
    if not path.exists():
        return []
    records: list[NormalizedSourceRecord] = []
    try:
        with path.open(
            "r",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return []
    for row in rows:
        source_url = str(row.get("url") or "").strip()
        source_id = str(row.get("source_id") or "").strip()
        if not source_url or not source_id:
            continue
        topics = str(row.get("topics") or "").strip()
        records.append(NormalizedSourceRecord(
            source_id=source_id,
            ticker=identity.ticker.upper(),
            entity_name=identity.name,
            security_id=identity.ticker.upper(),
            source_family="historical_context",
            source_type=canonical_source_type(
                str(row.get("source_type") or "institutional_history")
            ),
            document_id=source_id,
            source_url=source_url,
            publication_date=str(
                row.get("publication_date") or ""
            ).strip(),
            reporting_period=str(row.get("period") or "").strip(),
            section=str(row.get("title") or "").strip(),
            page_or_table="",
            exact_excerpt=topics,
            metric_or_claim=topics,
            value=None,
            unit="",
            currency="",
            source_tier=_optional_int(row.get("source_tier")) or 2,
            origin_group=str(
                row.get("origin_group")
                or _origin_group(source_url)
            ).strip(),
            licensing_policy=str(
                row.get("licensing_policy")
                or "public_institutional_excerpt"
            ).strip(),
            retrieval_status="metadata_only",
            parser_status="not_parsed",
            validation_status="metadata_only",
            citation_id="",
            observed_at="",
        ))
    return records


def _periods_overlap(requested: str, available: str) -> bool:
    requested_years = {
        int(value) for value in _YEAR_RE.findall(requested)
    }
    available_years = {
        int(value) for value in _YEAR_RE.findall(available)
    }
    if not requested_years or not available_years:
        return True
    requested_start = min(requested_years)
    requested_end = max(requested_years)
    available_start = min(available_years)
    available_end = max(available_years)
    return not (
        requested_end < available_start
        or available_end < requested_start
    )


def _forms_for_query(query: str) -> set[str]:
    upper = query.upper()
    forms = {
        form for form in (
            "10-K",
            "10-Q",
            "8-K",
            "20-F",
            "40-F",
            "6-K",
            "DEF 14A",
        )
        if form in upper
    }
    return forms or {"10-K", "10-Q", "8-K", "20-F", "6-K", "DEF 14A"}


def _query_needs_exhibits(query: str) -> bool:
    lowered = query.casefold()
    return any(term in lowered for term in (
        "announcement",
        "commitment",
        "contract",
        "debt",
        "earnings release",
        "financing",
        "product",
        "purchase obligation",
        "supplier",
    ))


def _relevant_sec_documents(
    documents: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    result = []
    for item in documents:
        name = str(item.get("name") or "").casefold()
        document_type = str(item.get("type") or "").upper()
        if not name.endswith((".htm", ".html", ".txt")):
            continue
        if (
            document_type.startswith(("EX-10", "EX-4", "EX-99", "EX-101"))
            or any(token in name for token in (
                "exhibit",
                "earnings",
                "release",
                "agreement",
                "presentation",
            ))
        ):
            result.append(item)
    return result


def _rank_filings(
    filings: Iterable[FilingRecord],
    forms: set[str],
    period: str,
) -> list[FilingRecord]:
    start, end = _period_bounds(period)
    return sorted(
        (
            filing for filing in filings
            if not forms or filing.form in forms
        ),
        key=lambda filing: (
            0
            if _date_in_range(
                filing.report_date or filing.filing_date, start, end
            )
            else 1,
            -(int((filing.report_date or filing.filing_date or "0")[:4] or 0)),
            filing.accession,
        ),
    )


def _extract_relevant_excerpt(text: str, query: str) -> str:
    if not text:
        return ""
    terms = [
        term for term in _tokens(query)
        if len(term) >= 4
        and term not in {
            "historical",
            "source",
            "document",
            "filing",
            "evidence",
            "company",
            "issuer",
        }
    ]
    paragraphs = [
        " ".join(block.split())
        for block in re.split(r"\n\s*\n|\r\n\s*\r\n", text)
        if len(block.split()) >= 8
    ]
    if not paragraphs:
        paragraphs = [" ".join(text.split())]
    scored = []
    for paragraph in paragraphs:
        lowered = paragraph.casefold()
        score = sum(term in lowered for term in terms)
        if score:
            scored.append((score, len(paragraph), paragraph))
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2][:4_000]


def _sample_price_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return rows
    indices = sorted({
        round(index * (len(rows) - 1) / (limit - 1))
        for index in range(limit)
    })
    return [rows[index] for index in indices]


def _period_bounds(value: str) -> tuple[str, str]:
    years = sorted({int(year) for year in _YEAR_RE.findall(value or "")})
    if not years:
        return "", ""
    return f"{years[0]}-01-01", f"{years[-1]}-12-31"


def _date_in_range(value: str, start: str, end: str) -> bool:
    if not start or not value:
        return False
    return start <= value[:10] <= end


def _section_hint(query: str) -> str:
    terms = [term for term in _tokens(query) if len(term) >= 5]
    return " ".join(terms[:8]).title() or "Relevant filing excerpt"


def _lifecycle_history(status: str) -> list[str]:
    if status == "catalog_match":
        return ["discovered", "catalog_match"]
    if status == "validated":
        return ["discovered", "fetched_live", "parsed", "validated"]
    if status == "parsed":
        return ["discovered", "fetched_live", "parsed"]
    if status == "metadata_only":
        return ["discovered", "metadata_only"]
    return [status] if status in RETRIEVAL_STATES else ["failed"]


def _failure_message(code: str, source_type: str) -> str:
    messages = {
        "document_not_found": "No registered document matched the requested entity, period, and source family.",
        "claim_not_disclosed": "A document was parsed, but it did not disclose the requested claim.",
        "licensed_manual_required": "The source requires licensed access or a permitted manual excerpt.",
        "underlying_content_not_fetched": "Only metadata is available; the underlying document was not fetched.",
        "source_unavailable": "The registered source family returned no usable evidence.",
        "no_data": "The provider returned no usable observations.",
    }
    return messages.get(
        code,
        f"{source_type or 'Source'} retrieval did not produce validated evidence.",
    )


def _failure_code(exc: Exception) -> str:
    message = str(exc).casefold()
    if "403" in message or "forbidden" in message:
        return "access_blocked"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    if "parse" in message:
        return "table_parse_failed"
    return "retrieval_failed"


def _registered_url(
    url: str,
    records: Iterable[NormalizedSourceRecord],
) -> bool:
    return any(record.source_url == url for record in records)


def _origin_group(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _jurisdiction_from_url(url: str) -> str:
    host = _origin_group(url)
    if host.endswith(".gov") or host in {"sec.gov", "ftc.gov", "justice.gov"}:
        return "United States"
    if host.endswith(".eu"):
        return "European Union"
    return host or "Unknown"


def _extract_amount(text: str) -> tuple[float | None, str, str]:
    match = _MONEY_RE.search(text)
    if not match:
        return None, "", ""
    value = _optional_float(match.group("value"))
    scale = str(match.group("scale") or "").casefold()
    multiplier = {
        "thousand": 1_000,
        "million": 1_000_000,
        "mm": 1_000_000,
        "m": 1_000_000,
        "billion": 1_000_000_000,
        "bn": 1_000_000_000,
        "b": 1_000_000_000,
    }.get(scale, 1)
    currency_token = str(match.group("currency") or "")
    currency = (
        "USD" if currency_token in {"$", "US$", "USD"} else currency_token
    )
    return (
        value * multiplier if value is not None else None,
        currency,
        scale or "units",
    )


def _dedupe_records(
    records: Iterable[NormalizedSourceRecord],
) -> list[NormalizedSourceRecord]:
    by_id: dict[str, NormalizedSourceRecord] = {}
    for record in records:
        existing = by_id.get(record.source_id)
        if existing is None or _record_richness(record) > _record_richness(existing):
            by_id[record.source_id] = record
    return sorted(by_id.values(), key=lambda item: item.source_id)


def _record_richness(record: NormalizedSourceRecord) -> tuple[int, int]:
    return (
        sum(bool(value) for value in (
            record.exact_excerpt,
            record.reporting_period,
            record.source_url,
            record.citation_id,
            record.content_hash,
            record.validation_status == "validated",
        )),
        len(record.exact_excerpt),
    )


def _dedupe_filings(
    filings: Iterable[FilingRecord],
) -> list[FilingRecord]:
    return list({
        (filing.accession, filing.form): filing for filing in filings
    }.values())


def _dedupe_observations(
    observations: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {}
    for item in observations:
        key = (
            str(item.get("period") or ""),
            str(item.get("basis") or ""),
            item.get("value"),
        )
        by_key[key] = item
    return sorted(by_key.values(), key=lambda item: str(item.get("period") or ""))


def _dedupe_dataclasses(items: Iterable[Any], key: str) -> list[Any]:
    return list({getattr(item, key): item for item in items}.values())


def _semantic_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _semantic_value(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
            if str(key) not in _VOLATILE_EVIDENCE_FIELDS
        }
    if isinstance(value, list):
        normalized = [_semantic_value(item) for item in value]
        if all(isinstance(item, (str, int, float, bool, type(None))) for item in normalized):
            return sorted(normalized, key=lambda item: str(item))
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, sort_keys=True, ensure_ascii=True
            ),
        )
    if isinstance(value, float):
        return round(value, 10)
    return value


def _tokens(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN_RE.findall(value or "")]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _stable_id(*parts: Any) -> str:
    return hashlib.sha1(
        "|".join(str(part) for part in parts).encode("utf-8")
    ).hexdigest()[:16]


def _safe_error(message: str) -> str:
    return re.sub(
        r"(?i)(api[_ -]?key|authorization|token|bearer)\s*[:=]\s*\S+",
        r"\1=[redacted]",
        message,
    )[:1_000]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
