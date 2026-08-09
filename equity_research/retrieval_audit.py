from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from . import config
from .analysis import build_financial_metrics
from .auditable_retrieval import (
    AuditableEvidenceRetriever,
    audit_artifact_claims,
    extract_contractual_exposures,
    normalize_catalog_source_records,
    semantic_evidence_hash,
)
from .claim_policies import apply_claim_evidence_policies
from .models import (
    AuditRepairQueue,
    ClaimEvidenceAudit,
    CompanyIdentity,
    FilingRecord,
    FinancialMetric,
    NormalizedSourceRecord,
    RegulatoryMomentumAssessment,
    ResearchRoundArtifact,
    RetrievalAuditReport,
    SourceRetrievalLifecycle,
)
from .providers import StooqPriceClient
from .research_profiles import resolve_research_profile
from .regulatory_radar import build_regulatory_radar
from .sec_client import SecClient
from .source_planner import SOURCE_REGISTRY_VERSION


AUDIT_VERSION = "retrieval-audit-v1"
AUDIT_FORMS = {
    "10-K",
    "10-Q",
    "8-K",
    "20-F",
    "40-F",
    "6-K",
    "DEF 14A",
    "DEFA14A",
    "3",
    "4",
    "5",
}


def build_runtime_retrieval_audit(
    identity: CompanyIdentity,
    *,
    profile: str,
    filings: Iterable[FilingRecord],
    normalized_sources: Iterable[NormalizedSourceRecord],
    retrieval_lifecycles: Iterable[SourceRetrievalLifecycle],
    claim_audits: Iterable[ClaimEvidenceAudit],
    repair_queue: AuditRepairQueue | None,
    regulatory_momentum: RegulatoryMomentumAssessment | None = None,
    no_llm_calls: bool = True,
) -> RetrievalAuditReport:
    """Summarize an ordinary research run without starting new retrieval."""
    filing_rows = list(filings)
    sources = _dedupe_records(list(normalized_sources))
    lifecycles = list(retrieval_lifecycles)
    audits = list(claim_audits)
    origins = {
        item.origin_group
        for item in sources
        if item.origin_group and item.validation_status == "validated"
    }
    passed = sum(item.status == "passed" for item in audits)
    blocked = sum(item.status != "passed" for item in audits)
    score = _readiness_score(
        identity, filing_rows, sources, audits, origins
    )
    blockers = _critical_blockers(
        identity, filing_rows, sources, audits, origins
    )
    if not audits:
        blockers.append(
            "Claim-level audit has not run; coverage alone cannot establish "
            "benchmark readiness."
        )
    blockers = list(dict.fromkeys(blockers))
    manual = list(dict.fromkeys(
        item.message
        for item in lifecycles
        if item.status == "licensed_manual_required" and item.message
    ))
    ready = score >= 80 and not blockers
    evidence_hash, _ = semantic_evidence_hash(
        [asdict(item) for item in sources],
        prompt_version=AUDIT_VERSION,
        source_registry_version=SOURCE_REGISTRY_VERSION,
    )
    return RetrievalAuditReport(
        ticker=identity.ticker.upper(),
        profile=profile,
        status="benchmark_ready" if ready else "blocked",
        benchmark_ready=ready,
        generated_at=_now(),
        summary=(
            f"{passed} material claim(s) passed claim-level evidence audit; "
            f"{blocked} remain unresolved."
        ),
        entity_complete=bool(
            identity.ticker and identity.cik and identity.name
        ),
        input_sufficiency_score=score,
        claim_ready_count=passed,
        claim_blocked_count=blocked,
        source_status_counts=_status_counts(lifecycles, sources),
        source_origin_count=len(origins),
        critical_blockers=blockers,
        manual_requirements=manual,
        next_actions=_next_actions(lifecycles, audits, blockers),
        retrieval_lifecycles=lifecycles,
        normalized_sources=sources,
        claim_audits=audits,
        repair_queue=repair_queue,
        regulatory_momentum=regulatory_momentum,
        semantic_evidence_hash=evidence_hash,
        no_llm_calls=no_llm_calls,
        data_gaps=[],
    )


def run_retrieval_audit(
    ticker: str,
    *,
    profile: str = "deep_initiation",
    refresh: bool = False,
    offline: bool = False,
    output_dir: Path | str | None = None,
    sec_client: SecClient | None = None,
    price_client: Any | None = None,
    registered_source_catalog: Iterable[NormalizedSourceRecord] | None = None,
    write_reports: bool = True,
) -> RetrievalAuditReport:
    selected_profile = resolve_research_profile(profile)
    sec = sec_client or SecClient(
        offline=offline,
        force_refresh=refresh,
    )
    identity = sec.map_ticker(ticker)
    filings: list[FilingRecord] = []
    metrics: list[FinancialMetric] = []
    data_gaps: list[str] = []
    try:
        submissions = sec.get_submissions(identity.cik)
        identity = CompanyIdentity(
            ticker=identity.ticker,
            cik=identity.cik,
            name=identity.name,
            exchange=identity.exchange,
            sic=str(submissions.get("sic") or "") or None,
            sic_description=submissions.get("sicDescription") or None,
        )
    except Exception as exc:
        data_gaps.append(f"SEC submissions: {_safe_error(exc)}")
    try:
        filings = sec.get_recent_filings(
            identity.cik,
            forms=AUDIT_FORMS,
            limit=_filing_limit(selected_profile.profile_id),
        )
    except Exception as exc:
        data_gaps.append(f"SEC filing history: {_safe_error(exc)}")
    try:
        metrics = build_financial_metrics(sec.get_company_facts(identity.cik))
    except Exception as exc:
        data_gaps.append(f"SEC company facts: {_safe_error(exc)}")

    catalog = _bootstrap_catalog(identity, filings, metrics)
    normalized = normalize_catalog_source_records(identity, catalog)
    retriever = AuditableEvidenceRetriever(
        identity,
        sec_client=sec,
        price_client=(
            None
            if offline else price_client or StooqPriceClient()
        ),
        filings=filings,
        metrics=metrics,
        catalog=catalog,
        fetch_text=(
            None
            if offline else
            lambda url: sec.get_text(url, ttl_seconds=7 * 24 * 60 * 60)
        ),
        registered_source_catalog=registered_source_catalog,
    )
    lifecycles = []
    retrieved = []
    for request in _audit_requests(
        identity,
        filings,
        selected_profile.profile_id,
    ):
        records, lifecycle = retriever.retrieve(**request)
        retrieved.extend(records)
        lifecycles.append(lifecycle)
    normalized = _dedupe_records([*normalized, *retrieved, *retriever.records])

    artifact = _metric_claim_artifact(identity, metrics)
    claim_audits, repair_queue = audit_artifact_claims(
        identity.ticker,
        artifact,
        normalized,
    )
    claim_audits = apply_claim_evidence_policies(claim_audits, normalized)
    repair_queue = _sync_repair_queue(repair_queue, claim_audits)
    contractual = extract_contractual_exposures(
        identity.ticker, normalized
    )
    hash_value, _ = semantic_evidence_hash(
        [asdict(item) for item in normalized],
        prompt_version=AUDIT_VERSION,
        source_registry_version=SOURCE_REGISTRY_VERSION,
    )
    status_counts = _status_counts(lifecycles, normalized)
    origins = {
        item.origin_group
        for item in normalized
        if item.origin_group and item.validation_status == "validated"
    }
    passed = sum(item.status == "passed" for item in claim_audits)
    blocked = sum(item.status != "passed" for item in claim_audits)
    score = _readiness_score(
        identity,
        filings,
        normalized,
        claim_audits,
        origins,
    )
    critical_blockers = _critical_blockers(
        identity,
        filings,
        normalized,
        claim_audits,
        origins,
    )
    manual = list(dict.fromkeys(
        lifecycle.message
        for lifecycle in lifecycles
        if lifecycle.status == "licensed_manual_required"
    ))
    if not contractual:
        data_gaps.append(
            "No validated contractual-exposure observation was extracted; "
            "absence of disclosure is not evidence that no obligation exists."
        )
    benchmark_ready = score >= 80 and not critical_blockers
    status = "benchmark_ready" if benchmark_ready else "blocked"
    next_actions = _next_actions(
        lifecycles, claim_audits, critical_blockers
    )
    regulatory_momentum = build_regulatory_radar(
        identity.ticker,
        [],
        [],
        normalized_sources=normalized,
    )
    report = RetrievalAuditReport(
        ticker=identity.ticker.upper(),
        profile=selected_profile.profile_id,
        status=status,
        benchmark_ready=benchmark_ready,
        generated_at=_now(),
        summary=(
            f"{identity.ticker.upper()} retrieval preflight is "
            f"{'benchmark-ready' if benchmark_ready else 'blocked'}: "
            f"{passed} claim(s) passed and {blocked} remain unresolved."
        ),
        entity_complete=bool(
            identity.ticker and identity.cik and identity.name
        ),
        input_sufficiency_score=score,
        claim_ready_count=passed,
        claim_blocked_count=blocked,
        source_status_counts=status_counts,
        source_origin_count=len(origins),
        critical_blockers=critical_blockers,
        manual_requirements=manual,
        next_actions=next_actions,
        retrieval_lifecycles=lifecycles,
        normalized_sources=normalized,
        claim_audits=claim_audits,
        repair_queue=repair_queue,
        regulatory_momentum=regulatory_momentum,
        semantic_evidence_hash=hash_value,
        no_llm_calls=True,
        data_gaps=list(dict.fromkeys(data_gaps)),
    )
    if write_reports:
        destination = Path(output_dir or "data/retrieval_audits")
        json_path, markdown_path = write_retrieval_audit(report, destination)
        report.output_json = str(json_path)
        report.output_markdown = str(markdown_path)
    return report


def write_retrieval_audit(
    report: RetrievalAuditReport,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{report.ticker}.json"
    markdown_path = output_dir / f"{report.ticker}.md"
    payload = asdict(report)
    payload["output_json"] = str(json_path)
    payload["output_markdown"] = str(markdown_path)
    payload = _redact_payload(payload)
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True, default=str),
        encoding="utf-8",
    )
    markdown_path.write_text(
        str(_redact_payload(retrieval_audit_markdown(report))),
        encoding="utf-8",
    )
    return json_path, markdown_path


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if any(
                    token in key.casefold()
                    for token in ("api_key", "apikey", "secret", "password")
                )
                else _redact_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if not isinstance(value, str):
        return value
    redacted = value
    for secret in (
        config.ALPHAVANTAGE_API_KEY,
        config.FINNHUB_API_KEY,
        config.FMP_API_KEY,
        config.FRED_API_KEY,
        config.BEA_API_KEY,
        config.CENSUS_API_KEY,
        config.WISBURG_API_KEY,
        config.TIINGO_API_KEY,
        config.EODHD_API_KEY,
        config.OPENAI_API_KEY,
        config.DEEPSEEK_API_KEY,
        config.SEC_USER_AGENT,
    ):
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    return re.sub(
        r"(?i)(api[_-]?key|apikey|token|access[_-]?key)=([^&\s]+)",
        r"\1=[redacted]",
        redacted,
    )


def retrieval_audit_markdown(report: RetrievalAuditReport) -> str:
    lines = [
        f"# {report.ticker} Retrieval Audit",
        "",
        f"- Status: **{report.status}**",
        f"- Benchmark ready: **{'Yes' if report.benchmark_ready else 'No'}**",
        f"- Input sufficiency: **{report.input_sufficiency_score}/100**",
        f"- Claim audit: **{report.claim_ready_count} passed / "
        f"{report.claim_blocked_count} blocked**",
        f"- Independent validated origins: **{report.source_origin_count}**",
        f"- Semantic evidence hash: `{report.semantic_evidence_hash}`",
        f"- LLM calls: **{'None' if report.no_llm_calls else 'Unexpected'}**",
        "",
        "## Critical Blockers",
    ]
    lines.extend(
        [f"- {item}" for item in report.critical_blockers]
        or ["- None."]
    )
    lines.extend(["", "## Retrieval Lifecycle"])
    for item in report.retrieval_lifecycles:
        lines.append(
            f"- `{item.source_type}`: **{item.status}**"
            + (f" - {item.message}" if item.message else "")
        )
    lines.extend(["", "## Claim Audit"])
    for item in report.claim_audits:
        lines.append(
            f"- **{item.status}** `{item.claim_id}`: {item.claim_text}"
        )
        for blocker in item.blockers:
            lines.append(f"  - {blocker}")
    lines.extend(["", "## Next Actions"])
    lines.extend(
        [f"- {item}" for item in report.next_actions]
        or ["- No automatic action remains."]
    )
    if report.manual_requirements:
        lines.extend(["", "## Licensed Or Manual Requirements"])
        lines.extend(f"- {item}" for item in report.manual_requirements)
    return "\n".join(lines) + "\n"


def _bootstrap_catalog(
    identity: CompanyIdentity,
    filings: list[FilingRecord],
    metrics: list[FinancialMetric],
) -> list[dict[str, Any]]:
    catalog = []
    for filing in filings:
        catalog.append({
            "evidence_id": _stable_id("audit-filing", filing.accession),
            "kind": "historical_source",
            "source_type": "historical_filing",
            "source_family": "historical_primary",
            "source_tier": 1,
            "period": filing.report_date,
            "publication_date": filing.filing_date,
            "source_url": filing.url,
            "document_id": filing.accession,
            "accession": filing.accession,
            "form": filing.form,
            "summary": f"{filing.form} filed {filing.filing_date}.",
            "metadata_only": True,
            "origin_group": "sec.gov",
            "licensing_policy": "public_filing_excerpt",
        })
    for metric in metrics:
        current_id = _metric_id(identity.ticker, metric)
        catalog.append(_metric_catalog_item(
            current_id,
            metric,
            value=metric.value,
            period=metric.period_end,
            kind="financial_metric",
        ))
        if metric.previous_value is not None and metric.previous_period_end:
            catalog.append(_metric_catalog_item(
                f"{current_id}:prior",
                metric,
                value=metric.previous_value,
                period=metric.previous_period_end,
                kind="financial_metric_prior",
            ))
    return catalog


def _metric_catalog_item(
    evidence_id: str,
    metric: FinancialMetric,
    *,
    value: float,
    period: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "citation_id": _stable_id(
            "audit-metric-citation", metric.accession, metric.name, period
        ),
        "kind": kind,
        "source_type": "sec_filing",
        "source_family": "primary_company",
        "source_tier": 1,
        "period": period,
        "period_start": metric.period_start or "",
        "duration_days": metric.duration_days,
        "fiscal_period": metric.fiscal_period or "",
        "fiscal_year": metric.fiscal_year,
        "fiscal_frame": metric.fiscal_frame or "",
        "metric": metric.name,
        "value": value,
        "unit": metric.unit,
        "currency": metric.unit if len(metric.unit) == 3 else "",
        "summary": f"{metric.name}: {value:g} {metric.unit}.",
        "exact_excerpt": f"{metric.name}: {value:g} {metric.unit}.",
        "publication_date": metric.filed or "",
        "source_url": metric.source_url or "",
        "document_id": metric.accession or evidence_id,
        "accession": metric.accession or "",
        "form": metric.form or "",
        "origin_group": "sec.gov",
        "licensing_policy": "public_filing_fact",
        "retrieval_status": "cached_document",
        "parser_status": "parsed",
        "validation_status": "validated",
    }


def _metric_claim_artifact(
    identity: CompanyIdentity,
    metrics: list[FinancialMetric],
) -> ResearchRoundArtifact:
    claims = []
    for metric in metrics:
        if metric.previous_value is None or not metric.previous_period_end:
            continue
        current_id = _metric_id(identity.ticker, metric)
        claims.append({
            "claim_id": _stable_id(
                "audit-metric-claim",
                identity.ticker,
                metric.name,
                metric.period_end,
            ),
            "claim_type": "financial_metric_change",
            "claim_text": (
                f"{metric.name} changed from {metric.previous_value:g} "
                f"{metric.unit} in {metric.previous_period_end} to "
                f"{metric.value:g} {metric.unit} in {metric.period_end}."
            ),
            "evidence_ids": [current_id, f"{current_id}:prior"],
            "materiality": "Material",
        })
    return ResearchRoundArtifact(
        artifact_id=_stable_id("retrieval-audit-artifact", identity.ticker),
        artifact_type="deterministic_retrieval_preflight",
        status="completed",
        summary="Deterministic current/prior metric claims for retrieval audit.",
        payload={"claims": claims[:40]},
        evidence_ids=[
            evidence_id
            for item in claims[:40]
            for evidence_id in item["evidence_ids"]
        ],
    )


def _audit_requests(
    identity: CompanyIdentity,
    filings: list[FilingRecord],
    profile_id: str,
) -> list[dict[str, Any]]:
    period = _filing_period_range(filings)
    requests = [
        {
            "request_id": _stable_id("audit-request", identity.ticker, "financials"),
            "source_type": "historical_filing",
            "query": (
                "revenue gross profit operating income operating cash flow "
                "capital expenditure free cash flow"
            ),
            "period": period,
            "result_limit": 10,
        },
        {
            "request_id": _stable_id("audit-request", identity.ticker, "debt"),
            "source_type": "historical_filing",
            "query": (
                "debt maturity interest expense leases refinancing current "
                "debt long-term debt"
            ),
            "period": period,
            "result_limit": 8,
        },
        {
            "request_id": _stable_id("audit-request", identity.ticker, "capital"),
            "source_type": "historical_filing",
            "query": (
                "repurchases dividends acquisitions capital allocation research "
                "and development"
            ),
            "period": period,
            "result_limit": 8,
        },
        {
            "request_id": _stable_id("audit-request", identity.ticker, "price"),
            "source_type": "quantitative_historical_price",
            "query": f"{identity.ticker} historical adjusted price",
            "period": period,
            "result_limit": 12,
        },
    ]
    if profile_id in {"adaptive_ic", "deep_initiation"}:
        requests.extend([
            {
                "request_id": _stable_id(
                    "audit-request", identity.ticker, "contracts"
                ),
                "source_type": "historical_filing",
                "query": (
                    "purchase obligations supplier commitments guarantees "
                    "prepayments take-or-pay capacity commitments cancellation terms"
                ),
                "period": period,
                "result_limit": 8,
            },
            {
                "request_id": _stable_id(
                    "audit-request", identity.ticker, "issuer"
                ),
                "source_type": "issuer_announcement",
                "query": (
                    "official issuer announcement strategy supplier commitment "
                    "capital return product"
                ),
                "period": period,
                "result_limit": 8,
            },
            {
                "request_id": _stable_id(
                    "audit-request", identity.ticker, "regulation"
                ),
                "source_type": "regulator_court",
                "query": (
                    "regulatory proceeding proposal enforcement consultation "
                    "remedy affected business financial impact"
                ),
                "period": period,
                "result_limit": 8,
            },
        ])
    return requests


def _readiness_score(
    identity: CompanyIdentity,
    filings: list[FilingRecord],
    records: list[NormalizedSourceRecord],
    audits: list,
    origins: set[str],
) -> int:
    passed = sum(item.status == "passed" for item in audits)
    claim_score = round(35 * passed / max(1, len(audits)))
    validated = [
        item for item in records if item.validation_status == "validated"
    ]
    periodized = [item for item in validated if item.reporting_period]
    cited = [
        item for item in validated
        if item.source_url and item.citation_id
    ]
    score = 0
    score += 10 if identity.ticker and identity.cik and identity.name else 0
    score += 15 if filings else 0
    score += claim_score
    score += 10 if len(periodized) >= 4 else round(10 * len(periodized) / 4)
    score += 10 if len(cited) >= 4 else round(10 * len(cited) / 4)
    score += 10 if len(origins) >= 2 else 5 if origins else 0
    score += 10 if any(
        item.source_type == "quantitative_historical_price"
        and item.validation_status == "validated"
        for item in records
    ) else 0
    return min(100, score)


def _critical_blockers(
    identity: CompanyIdentity,
    filings: list[FilingRecord],
    records: list[NormalizedSourceRecord],
    audits: list,
    origins: set[str],
) -> list[str]:
    blockers = []
    if not identity.ticker or not identity.cik or not identity.name:
        blockers.append("Critical entity/security identity is incomplete.")
    if not filings:
        blockers.append("No SEC filing history was retrieved.")
    if not audits:
        blockers.append("No current/prior metric claim could be constructed.")
    if any(item.status == "failed" for item in audits):
        blockers.append(
            "At least one material current/prior claim failed claim-level validation."
        )
    if len(origins) < 2:
        blockers.append(
            "Fewer than two independent validated source origins are available."
        )
    if not any(
        item.source_type == "quantitative_historical_price"
        and item.validation_status == "validated"
        for item in records
    ):
        blockers.append(
            "No validated corporate-action-aware historical price observation is available."
        )
    return blockers


def _sync_repair_queue(
    queue: AuditRepairQueue,
    audits: list,
) -> AuditRepairQueue:
    status_by_claim = {item.claim_id: item for item in audits}
    for repair in queue.items:
        audit = status_by_claim.get(repair.claim_id)
        if audit and audit.status == "passed":
            repair.resolution = "validated"
            repair.final_audit_status = "passed"
    queue.items = [
        item for item in queue.items
        if status_by_claim.get(item.claim_id, None) is None
        or status_by_claim[item.claim_id].status != "passed"
    ]
    queue.status = "open" if queue.items else "closed"
    queue.critical_open_count = sum(
        item.status == "failed" and item.materiality == "Critical"
        for item in audits
    )
    return queue


def _status_counts(lifecycles: list, records: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in lifecycles:
        counts[item.status] = counts.get(item.status, 0) + 1
    for item in records:
        key = item.validation_status or item.retrieval_status
        counts[key] = counts.get(key, 0) + 1
    return counts


def _next_actions(lifecycles, audits, blockers) -> list[str]:
    actions = []
    for item in lifecycles:
        if item.status not in {"validated", "parsed", "catalog_match"}:
            actions.append(
                f"Retry or replace {item.source_type}: "
                f"{item.failure_code or item.message or item.status}."
            )
    for audit in audits:
        if audit.status != "passed":
            actions.append(
                f"Repair {audit.claim_id}: "
                f"{'; '.join(audit.blockers[:2])}."
            )
    actions.extend(blockers)
    return list(dict.fromkeys(actions))[:12]


def _filing_limit(profile_id: str) -> int:
    return {
        "fast_screening": 24,
        "adaptive_ic": 60,
        "deep_initiation": 100,
        "investigate_event": 40,
    }.get(profile_id, 60)


def _filing_period_range(filings: list[FilingRecord]) -> str:
    dates = sorted(
        item.report_date or item.filing_date
        for item in filings
        if item.report_date or item.filing_date
    )
    if not dates:
        return ""
    return dates[0] if len(dates) == 1 else f"{dates[0]} to {dates[-1]}"


def _metric_id(ticker: str, metric: FinancialMetric) -> str:
    return _stable_id(
        "audit-metric",
        ticker.upper(),
        metric.name,
        metric.period_end,
        metric.fiscal_period,
        metric.form,
    )


def _dedupe_records(
    records: Iterable[NormalizedSourceRecord],
) -> list[NormalizedSourceRecord]:
    by_id: dict[str, NormalizedSourceRecord] = {}
    for item in records:
        existing = by_id.get(item.source_id)
        if existing is None or _record_score(item) > _record_score(existing):
            by_id[item.source_id] = item
    return list(by_id.values())


def _record_score(item: NormalizedSourceRecord) -> tuple[int, int]:
    return (
        int(item.validation_status == "validated")
        + int(item.parser_status == "parsed")
        + int(item.retrieval_status not in {"metadata_only", "catalog_match"}),
        len(item.exact_excerpt or ""),
    )


def _stable_id(*parts: object) -> str:
    return hashlib.sha256(
        "|".join(str(item or "") for item in parts).encode("utf-8")
    ).hexdigest()[:20]


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")
    return text[:400]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
