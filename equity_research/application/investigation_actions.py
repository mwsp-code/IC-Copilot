from __future__ import annotations

import csv
import hashlib
import io
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Protocol


_ACTION_KINDS = {
    "consensus_revision_capture",
    "primary_source_validation",
    "cash_conversion_analysis",
    "operating_driver_analysis",
    "market_event_attribution",
    "valuation_scenarios",
    "external_theme_validation",
}


@dataclass(frozen=True)
class InvestigationActionCommand:
    parent_run_id: str
    parent_evidence_hash: str
    action_id: str
    ticker: str
    action_kind: str
    event_id: str = ""
    registered_connector_ids: tuple[str, ...] = ()
    source_policy_fingerprint: str = ""
    data_cutoff: str = ""
    input_fingerprint: str = ""
    idempotency_key: str = ""
    command_version: str = "investigation-action-command-v1"

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        if not ticker or not self.action_id.strip():
            raise ValueError("Ticker and action ID are required.")
        if self.action_kind not in _ACTION_KINDS:
            raise ValueError(f"Unsupported investigation action: {self.action_kind}.")
        object.__setattr__(self, "ticker", ticker)
        if not self.idempotency_key:
            payload = "|".join((
                self.parent_run_id,
                self.parent_evidence_hash,
                self.action_id,
                ticker,
                self.action_kind,
                self.event_id,
                ",".join(self.registered_connector_ids),
                self.input_fingerprint,
            ))
            object.__setattr__(
                self,
                "idempotency_key",
                hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            )


@dataclass(frozen=True)
class InvestigationActionResult:
    command: InvestigationActionCommand
    status: str
    summary: str
    attempted_connectors: tuple[str, ...] = ()
    admitted_evidence_ids: tuple[str, ...] = ()
    result_payload: dict[str, Any] = field(default_factory=dict)
    qa_closure_state: str = "not_required"
    completed_at: str = ""
    failure_class: str = ""


class InvestigationActionExecutor(Protocol):
    def __call__(
        self,
        command: InvestigationActionCommand,
    ) -> InvestigationActionResult: ...


class InvestigationActionService:
    """Idempotent application boundary for action-card execution."""

    def __init__(
        self,
        *,
        event_recorder: Callable[[InvestigationActionResult], None] | None = None,
        result_loader: Callable[
            [InvestigationActionCommand], InvestigationActionResult | None
        ] | None = None,
    ) -> None:
        self._results: dict[str, InvestigationActionResult] = {}
        self._lock = threading.RLock()
        self._event_recorder = event_recorder
        self._result_loader = result_loader

    def execute(
        self,
        command: InvestigationActionCommand,
        executor: InvestigationActionExecutor,
    ) -> InvestigationActionResult:
        with self._lock:
            cached = self._results.get(command.idempotency_key)
        if cached is not None:
            return cached
        if self._result_loader is not None:
            persisted = self._result_loader(command)
            if persisted is not None:
                with self._lock:
                    self._results[command.idempotency_key] = persisted
                return persisted
        result = executor(command)
        if result.command.idempotency_key != command.idempotency_key:
            raise ValueError("Executor returned a result for a different command.")
        if not result.completed_at:
            result = InvestigationActionResult(
                **{
                    **result.__dict__,
                    "completed_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                }
            )
        with self._lock:
            existing = self._results.setdefault(command.idempotency_key, result)
        if existing is result and self._event_recorder is not None:
            self._event_recorder(result)
        return existing


def parse_event_price_csv(
    payload: bytes,
    *,
    ticker: str,
    event_date: str,
    data_cutoff: str = "",
    sector_ticker: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Validate a manual adjusted-price package before infrastructure storage."""
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Price CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text))
    required = {"date", "ticker", "adjusted_close"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError(
            "Price CSV requires date, ticker, and adjusted_close columns."
        )
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, date]] = set()
    cutoff = _optional_date(data_cutoff)
    for index, raw in enumerate(reader, start=2):
        symbol = str(raw.get("ticker") or "").strip().upper()
        try:
            observed = date.fromisoformat(str(raw.get("date") or "")[:10])
            close = float(str(raw.get("adjusted_close") or ""))
            volume = float(str(raw.get("volume") or "0") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid price row {index}.") from exc
        if not symbol or close <= 0 or volume < 0:
            raise ValueError(f"Invalid ticker, adjusted close, or volume at row {index}.")
        if cutoff and observed > cutoff:
            raise ValueError("Price CSV contains observations after the active data cutoff.")
        key = (symbol, observed)
        if key in seen:
            raise ValueError(f"Duplicate price observation for {symbol} on {observed}.")
        seen.add(key)
        by_ticker.setdefault(symbol, []).append({
            "date": observed,
            "close": close,
            "volume": volume,
        })
    required_tickers = {ticker.upper(), "SPY"}
    missing = sorted(required_tickers - set(by_ticker))
    if missing:
        raise ValueError("Price CSV is missing required ticker(s): " + ", ".join(missing))
    for rows in by_ticker.values():
        rows.sort(key=lambda item: item["date"])
    event_day = date.fromisoformat(event_date[:10])
    for symbol in required_tickers:
        rows = by_ticker[symbol]
        prior = [item for item in rows if item["date"] < event_day]
        forward = [item for item in rows if item["date"] >= event_day]
        if len(prior) < 148:
            raise ValueError(
                f"{symbol} requires at least 148 pre-event observations for beta."
            )
        if len(forward) < 21:
            raise ValueError(
                f"{symbol} requires a prior close and at least 20 post-event sessions."
            )
    if sector_ticker and sector_ticker.upper() in by_ticker:
        sector_rows = by_ticker[sector_ticker.upper()]
        sector_prior = [item for item in sector_rows if item["date"] < event_day]
        sector_forward = [item for item in sector_rows if item["date"] >= event_day]
        if len(sector_prior) < 148 or len(sector_forward) < 21:
            raise ValueError(
                f"{sector_ticker.upper()} requires 148 pre-event observations and "
                "20 post-event sessions for complete sector attribution."
            )
    if sector_ticker and sector_ticker.upper() not in by_ticker:
        by_ticker["_coverage_note"] = [{
            "message": (
                f"{sector_ticker.upper()} is absent; sector-relative attribution "
                "will remain incomplete."
            )
        }]
    return by_ticker


def price_csv_template(ticker: str, sector_ticker: str = "") -> bytes:
    symbols = [ticker.upper(), "SPY"]
    if sector_ticker:
        symbols.append(sector_ticker.upper())
    rows = ["date,ticker,adjusted_close,volume"]
    rows.extend(f"YYYY-MM-DD,{symbol},," for symbol in symbols)
    return ("\n".join(rows) + "\n").encode("utf-8")


def _optional_date(value: str) -> date | None:
    return date.fromisoformat(value[:10]) if str(value or "").strip() else None
