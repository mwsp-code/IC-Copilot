from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .models import BringYourOwnDataStatus, Citation, ExternalEvidence, ManualDataSourceStatus


SOURCE_DEFINITIONS = (
    ("consensus", config.CONSENSUS_CSV_DIR, ["ticker", "metric", "value"]),
    ("prices", config.PRICE_CSV_DIR, ["date", "close"]),
    ("transcripts", config.TRANSCRIPT_CSV_DIR, ["ticker", "text"]),
    ("segment_kpis", config.BYOD_DATA_DIR / "segment_kpis.csv", ["ticker", "segment", "metric", "value"]),
    ("industry_data", config.BYOD_DATA_DIR / "industry_data.csv", ["ticker", "industry", "metric", "value"]),
    ("china_macro", config.BYOD_DATA_DIR / "china_macro.csv", ["ticker", "as_of", "metric", "value", "unit"]),
    ("paid_report_excerpts", config.BYOD_DATA_DIR / "report_excerpts.csv", ["ticker", "source", "excerpt"]),
)


def scan_manual_data_sources(ticker: str, base_dir: Path | None = None) -> BringYourOwnDataStatus:
    symbol = ticker.upper().strip()
    sources: list[ManualDataSourceStatus] = []
    for source_type, path, columns in SOURCE_DEFINITIONS:
        effective_path = _with_base_dir(path, base_dir)
        sources.append(_scan_source(source_type, effective_path, symbol, columns))
    active = [source for source in sources if source.rows_loaded > 0]
    missing = [source for source in sources if source.status == "Missing"]
    data_gaps = [
        f"{source.source_type} manual import not found at {source.path}."
        for source in missing
    ]
    if active:
        status = "Available"
    elif any(source.status == "Present - no ticker rows" for source in sources):
        status = "Present - no ticker rows"
    else:
        status = "Unavailable"
    return BringYourOwnDataStatus(
        status=status,
        base_dir=str((base_dir or config.BYOD_DATA_DIR).as_posix()),
        sources=sources,
        data_gaps=data_gaps,
    )


def _with_base_dir(path: Path, base_dir: Path | None) -> Path:
    if base_dir is None or path.is_dir() or path.parent != config.BYOD_DATA_DIR:
        return path
    return base_dir / path.name


def _scan_source(
    source_type: str,
    path: Path,
    ticker: str,
    required_columns: list[str],
) -> ManualDataSourceStatus:
    files = _source_files(path)
    if not files:
        return ManualDataSourceStatus(
            source_type,
            str(path),
            "Missing",
            0,
            "No CSV file or import directory is present.",
            required_columns,
        )
    rows = 0
    malformed: list[str] = []
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                headers = {header.strip().lower() for header in (reader.fieldnames or [])}
                if not headers:
                    malformed.append(file_path.name)
                    continue
                has_ticker = "ticker" in headers or "symbol" in headers
                for row in reader:
                    if has_ticker:
                        row_ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
                        if row_ticker != ticker:
                            continue
                    rows += 1
        except OSError:
            malformed.append(file_path.name)
    if rows:
        return ManualDataSourceStatus(
            source_type,
            str(path),
            "Available",
            rows,
            f"Loaded {rows} row(s) for {ticker}.",
            required_columns,
        )
    status = "Malformed" if malformed and len(malformed) == len(files) else "Present - no ticker rows"
    message = (
        f"Could not read CSV files: {', '.join(malformed)}."
        if status == "Malformed"
        else f"CSV files are present but no rows matched {ticker}."
    )
    return ManualDataSourceStatus(source_type, str(path), status, 0, message, required_columns)


def _source_files(path: Path) -> list[Path]:
    if path.is_dir():
        return sorted(item for item in path.glob("*.csv") if item.is_file())
    if path.is_file() and path.suffix.lower() == ".csv":
        return [path]
    return []


def load_china_macro_evidence(
    ticker: str,
    event_date: str | None = None,
    base_dir: Path | None = None,
) -> list[ExternalEvidence]:
    path = _with_base_dir(config.BYOD_DATA_DIR / "china_macro.csv", base_dir)
    rows: list[ExternalEvidence] = []
    symbol = ticker.upper().strip()
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for file_path in _source_files(path):
        try:
            with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    row_ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip()
                    if row_ticker not in {symbol, "ALL", "CHINA"}:
                        continue
                    as_of = str(row.get("as_of") or row.get("release_date") or "").strip()
                    if event_date and as_of and as_of[:10] > event_date[:10]:
                        continue
                    metric = str(row.get("metric") or "China macro indicator").strip()
                    value = _float(row.get("value"))
                    unit = str(row.get("unit") or "").strip() or None
                    source = str(row.get("source") or "Manual China macro CSV").strip()
                    source_url = str(row.get("source_url") or "").strip()
                    summary = str(row.get("summary") or "").strip()
                    if not summary:
                        summary = f"{metric} was {row.get('value')} {unit or ''} as of {as_of or 'unknown date'}.".strip()
                    rows.append(ExternalEvidence(
                        provider="Manual China macro CSV",
                        source_type="china_macro",
                        title=metric,
                        summary=summary,
                        observed_at=observed_at,
                        source_as_of=as_of or None,
                        source_tier=2 if source_url else 3,
                        official=bool(source_url),
                        confidence=str(row.get("confidence") or "Medium"),
                        licensing_policy="user_supplied_metadata_and_excerpt",
                        metric_name=metric,
                        metric_value=value,
                        unit=unit,
                        frequency=str(row.get("frequency") or "").strip() or None,
                        release_date=str(row.get("release_date") or as_of or "").strip() or None,
                        vintage_date=str(row.get("vintage_date") or as_of or "").strip() or None,
                        lookahead_safe=True,
                        direction=str(row.get("direction") or "neutral").strip() or "neutral",
                        event_date=event_date,
                        citation=Citation(
                            source=source,
                            url=source_url or str(file_path),
                            filed=as_of or None,
                            section="manual_china_macro",
                            snippet=summary,
                            source_tier=2 if source_url else 3,
                        ),
                        tags=["china_macro", "manual_import"],
                        disqualifies_high_conviction=False,
                    ))
        except OSError:
            continue
    return rows


def _float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
