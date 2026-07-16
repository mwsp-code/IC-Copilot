from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .expectations import attach_revision_history
from .models import ConsensusPackage, ProviderObservation
from .providers import CsvConsensusProvider
from .research_store import ResearchStore


CONSENSUS_CSV_FILES = (
    "targets.csv",
    "target_revisions.csv",
    "estimates.csv",
    "estimate_revisions.csv",
    "recommendations.csv",
    "surprises.csv",
    "provider_metadata.csv",
)

CONSENSUS_CSV_SCHEMAS: dict[str, list[str]] = {
    "targets.csv": [
        "ticker", "as_of", "observed_at", "source", "target_aggregate", "target_mean",
        "target_median", "target_high", "target_low", "currency", "analyst_count",
        "current_price", "official", "target_label", "target_kind",
    ],
    "target_revisions.csv": [
        "ticker", "metric", "window_days", "start_date", "end_date", "start_value",
        "end_value", "change_pct", "provider", "official", "source_kind",
    ],
    "estimates.csv": [
        "ticker", "as_of", "observed_at", "source", "metric", "period_end",
        "period_type", "average", "high", "low", "analyst_count", "currency",
        "official", "period_precision", "revisions_up", "revisions_down",
    ],
    "estimate_revisions.csv": [
        "ticker", "metric", "window_days", "start_date", "end_date", "start_value",
        "end_value", "change_pct", "provider", "official", "source_kind",
    ],
    "recommendations.csv": [
        "ticker", "as_of", "observed_at", "source", "strong_buy", "buy", "hold",
        "sell", "strong_sell", "consensus_label", "official",
    ],
    "surprises.csv": [
        "ticker", "period_end", "observed_at", "source", "actual_eps",
        "estimated_eps", "surprise_pct", "official",
    ],
    "provider_metadata.csv": [
        "ticker", "provider", "field", "observed_at", "source_as_of",
        "entitlement_status", "provenance", "official", "notes",
    ],
}


@dataclass
class ConsensusImportResult:
    directory: str
    tickers: list[str] = field(default_factory=list)
    imported: int = 0
    skipped: int = 0
    rows_by_file: dict[str, int] = field(default_factory=dict)
    rows_by_ticker: dict[str, dict[str, int]] = field(default_factory=dict)
    metadata_observations: int = 0
    revision_windows_available: int = 0
    revision_windows_incomplete: int = 0
    messages: list[str] = field(default_factory=list)


@dataclass
class ConsensusTemplateResult:
    directory: str
    files_written: list[str] = field(default_factory=list)
    files_existing: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)


def import_consensus_csv(
    directory: Path | None = None,
    tickers: list[str] | None = None,
    store: ResearchStore | None = None,
) -> ConsensusImportResult:
    csv_dir = directory or config.CONSENSUS_CSV_DIR
    target_tickers = sorted({ticker.upper().strip() for ticker in (tickers or []) if ticker.strip()})
    if not target_tickers:
        target_tickers = _tickers_from_csv(csv_dir)
    result = ConsensusImportResult(directory=str(csv_dir), tickers=target_tickers)
    if not target_tickers:
        result.messages.append(f"No ticker rows found in {csv_dir}.")
        return result
    result.rows_by_file = _rows_by_file(csv_dir)
    result.rows_by_ticker = _rows_by_ticker(csv_dir, target_tickers)
    own_store = store is None
    active_store = store or ResearchStore()
    provider = CsvConsensusProvider(csv_dir, active_store)
    for ticker in target_tickers:
        metadata = _metadata_observations(csv_dir, ticker)
        if metadata:
            active_store.save_consensus_package(ConsensusPackage(
                ticker=ticker,
                provider="CSV metadata",
                status="Available",
                observations=metadata,
            ))
            result.metadata_observations += len(metadata)
        package = provider.fetch_package(ticker)
        if package.status == "Unavailable":
            result.skipped += 1
            result.messages.extend(package.data_gaps)
            if metadata:
                result.messages.append(
                    f"Imported {ticker} provider metadata, but no numeric consensus rows were found."
                )
            continue
        for target in provider.fetch_target_history(ticker):
            active_store.save_consensus_package(ConsensusPackage(
                ticker=ticker,
                provider=target.source or provider.provider_name,
                status="Available",
                target=target,
            ))
        for recommendation in provider.fetch_recommendation_history(ticker):
            active_store.save_consensus_package(ConsensusPackage(
                ticker=ticker,
                provider=recommendation.source or provider.provider_name,
                status="Available",
                recommendations=recommendation,
            ))
        active_store.save_consensus_package(package)
        attach_revision_history(package, active_store)
        result.imported += 1
        revision_count = sum(1 for item in package.revisions if item.status == "available")
        incomplete_count = sum(1 for item in package.revisions if item.status != "available")
        result.revision_windows_available += revision_count
        result.revision_windows_incomplete += incomplete_count
        row_counts = result.rows_by_ticker.get(ticker, {})
        result.messages.append(
            f"Imported {ticker}: {sum(row_counts.values())} CSV row(s), "
            f"{len(metadata)} metadata observation(s), {revision_count} available revision window(s), "
            f"{incomplete_count} incomplete."
        )
        result.messages.extend(_revision_diagnostic_messages(package)[:4])
    if own_store:
        # ResearchStore opens short-lived connections per operation; no close hook is required.
        pass
    return result


def _rows_by_file(directory: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for filename in CONSENSUS_CSV_FILES:
        path = directory / filename
        if not path.exists():
            counts[filename] = 0
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                counts[filename] = sum(1 for _ in csv.DictReader(handle))
        except OSError:
            counts[filename] = 0
    return counts


def _rows_by_ticker(directory: Path, tickers: list[str]) -> dict[str, dict[str, int]]:
    result = {ticker: {filename: 0 for filename in CONSENSUS_CSV_FILES} for ticker in tickers}
    ticker_set = set(tickers)
    for filename in CONSENSUS_CSV_FILES:
        path = directory / filename
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
                    if ticker in ticker_set:
                        result[ticker][filename] += 1
        except OSError:
            continue
    return result


def _metadata_observations(directory: Path, ticker: str) -> list[ProviderObservation]:
    path = directory / "provider_metadata.csv"
    if not path.exists():
        return []
    observations: list[ProviderObservation] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                row_ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
                if row_ticker != ticker.upper():
                    continue
                provider = str(row.get("provider") or "Manual").strip() or "Manual"
                field_name = str(row.get("field") or "metadata").strip() or "metadata"
                observations.append(ProviderObservation(
                    ticker=ticker.upper(),
                    provider=provider,
                    field=field_name,
                    observed_at=str(row.get("observed_at") or now),
                    source_as_of=str(row.get("source_as_of") or "").strip() or None,
                    value_numeric=_float(row.get("value_numeric") or row.get("value")),
                    value_text=str(row.get("value_text") or row.get("notes") or "").strip() or None,
                    currency=str(row.get("currency") or "").strip() or None,
                    analyst_count=_int(row.get("analyst_count")),
                    entitlement_status=str(row.get("entitlement_status") or "available"),
                    provenance=str(row.get("provenance") or "CSV/manual provider metadata"),
                    official=_bool(row.get("official"), True),
                    confidence=str(row.get("confidence") or "Medium"),
                ))
    except OSError:
        return observations
    return observations


def _revision_diagnostic_messages(package: ConsensusPackage) -> list[str]:
    if not package.revisions:
        return [
            f"{package.ticker}: no revision windows were built. Add at least two dated target or estimate snapshots."
        ]
    rows: list[str] = []
    incomplete = [item for item in package.revisions if item.status != "available"]
    for item in incomplete[:6]:
        rows.append(
            f"{package.ticker} {item.metric} {item.window_days}d revision incomplete: "
            f"{item.reason or 'missing start or end snapshot'}"
        )
    if not incomplete:
        rows.append(f"{package.ticker}: revision history is usable for available windows.")
    return rows


def _float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _bool(value: object, default: bool) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "official"}


def write_consensus_csv_templates(
    directory: Path | None = None,
    *,
    ticker: str = "",
    overwrite: bool = False,
) -> ConsensusTemplateResult:
    csv_dir = directory or config.CONSENSUS_CSV_DIR
    csv_dir.mkdir(parents=True, exist_ok=True)
    result = ConsensusTemplateResult(directory=str(csv_dir))
    sample = _sample_row(ticker.upper().strip() or "TICKER")
    for filename, columns in CONSENSUS_CSV_SCHEMAS.items():
        path = csv_dir / filename
        if path.exists() and not overwrite:
            result.files_existing.append(filename)
            continue
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            if filename in sample:
                row = {column: sample[filename].get(column, "") for column in columns}
                writer.writerow(row)
        result.files_written.append(filename)
    result.messages.append(
        "Templates are local-only. Delete sample rows or replace them with point-in-time snapshots before import."
    )
    result.messages.append(
        "Use observed_at/source_as_of dates carefully: historical research may only use snapshots observed on or before the event date."
    )
    return result


def _tickers_from_csv(directory: Path) -> list[str]:
    tickers: set[str] = set()
    for filename in CONSENSUS_CSV_FILES:
        path = directory / filename
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
                    if ticker:
                        tickers.add(ticker)
        except OSError:
            continue
    return sorted(tickers)


def _sample_row(ticker: str) -> dict[str, dict[str, object]]:
    return {
        "targets.csv": {
            "ticker": ticker,
            "as_of": "2026-06-30",
            "observed_at": "2026-06-30T08:00:00+00:00",
            "source": "Manual",
            "target_mean": "100.00",
            "currency": "USD",
            "analyst_count": "10",
            "official": "true",
            "target_label": "Mean target",
            "target_kind": "mean",
        },
        "target_revisions.csv": {
            "ticker": ticker,
            "metric": "target_mean",
            "window_days": "30",
            "start_date": "2026-05-31",
            "end_date": "2026-06-30",
            "start_value": "95.00",
            "end_value": "100.00",
            "change_pct": "5.26",
            "provider": "Manual",
            "official": "true",
            "source_kind": "manual_snapshot",
        },
        "estimates.csv": {
            "ticker": ticker,
            "as_of": "2026-06-30",
            "observed_at": "2026-06-30T08:00:00+00:00",
            "source": "Manual",
            "metric": "EPS",
            "period_end": "2027-03-31",
            "period_type": "annual",
            "average": "5.00",
            "currency": "USD",
            "analyst_count": "10",
            "official": "true",
            "period_precision": "day",
        },
        "estimate_revisions.csv": {
            "ticker": ticker,
            "metric": "EPS",
            "window_days": "30",
            "start_date": "2026-05-31",
            "end_date": "2026-06-30",
            "start_value": "4.80",
            "end_value": "5.00",
            "change_pct": "4.17",
            "provider": "Manual",
            "official": "true",
            "source_kind": "manual_snapshot",
        },
        "recommendations.csv": {
            "ticker": ticker,
            "as_of": "2026-06-30",
            "observed_at": "2026-06-30T08:00:00+00:00",
            "source": "Manual",
            "strong_buy": "3",
            "buy": "5",
            "hold": "2",
            "sell": "0",
            "strong_sell": "0",
            "consensus_label": "Buy",
            "official": "true",
        },
        "surprises.csv": {
            "ticker": ticker,
            "period_end": "2026-03-31",
            "observed_at": "2026-05-01T08:00:00+00:00",
            "source": "Manual",
            "actual_eps": "1.10",
            "estimated_eps": "1.00",
            "surprise_pct": "10.0",
            "official": "true",
        },
        "provider_metadata.csv": {
            "ticker": ticker,
            "provider": "Manual",
            "field": "target_mean",
            "observed_at": "2026-06-30T08:00:00+00:00",
            "source_as_of": "2026-06-30",
            "entitlement_status": "available",
            "provenance": "user_import",
            "official": "true",
            "notes": "Replace sample metadata with your source notes.",
        },
    }
