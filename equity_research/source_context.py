from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class AdmittedMarketPrice:
    """Point-in-time market price admitted to a completed research artifact."""

    ticker: str
    value: float
    currency: str
    source_as_of: str
    observed_at: str
    source: str
    source_url: str
    source_id: str


def validated_source_records(
    research: Any | None,
    *,
    ticker: str = "",
) -> tuple[Any, ...]:
    """Return admitted records without depending on a concrete artifact schema."""

    selected_ticker = ticker.upper().strip()
    return tuple(
        record
        for record in list(getattr(research, "normalized_sources", ()) or ())
        if str(getattr(record, "validation_status", "") or "").casefold()
        == "validated"
        and (
            not selected_ticker
            or str(getattr(record, "ticker", "") or "").upper()
            == selected_ticker
        )
    )


def latest_validated_record(
    records: Iterable[Any],
    predicate: Callable[[Any], bool],
) -> Any | None:
    eligible = [record for record in records if predicate(record)]
    return max(eligible, key=_record_sort_key, default=None)


def latest_admitted_market_price(
    research: Any | None,
    ticker: str,
) -> AdmittedMarketPrice | None:
    """Resolve the newest validated adjusted close for the selected security.

    Historical analog and benchmark prices are deliberately excluded by the
    ticker filter.  The returned value is a research-cutoff observation, never
    an assertion that the quote is live when the artifact is rendered later.
    """

    records = validated_source_records(research, ticker=ticker)
    record = latest_validated_record(
        records,
        lambda item: (
            str(getattr(item, "source_type", "") or "").casefold()
            == "quantitative_historical_price"
            and str(getattr(item, "metric_or_claim", "") or "").casefold()
            in {"adjusted close", "adjusted_close"}
            and _positive_number(getattr(item, "value", None)) is not None
            and bool(str(getattr(item, "publication_date", "") or "").strip())
        ),
    )
    if record is None:
        return None
    return AdmittedMarketPrice(
        ticker=ticker.upper(),
        value=float(getattr(record, "value")),
        currency=str(getattr(record, "currency", "") or "USD"),
        source_as_of=str(getattr(record, "publication_date", "") or ""),
        observed_at=str(getattr(record, "observed_at", "") or ""),
        source=str(
            getattr(record, "origin_group", "")
            or getattr(record, "source_family", "")
            or "Registered market-data connector"
        ),
        source_url=str(getattr(record, "source_url", "") or ""),
        source_id=str(getattr(record, "source_id", "") or ""),
    )


def record_source_label(record: Any | None, fallback: str) -> str:
    if record is None:
        return fallback
    return str(
        getattr(record, "origin_group", "")
        or getattr(record, "source_family", "")
        or fallback
    )


def _record_sort_key(record: Any) -> tuple[datetime, datetime, str]:
    return (
        _parse_datetime(getattr(record, "publication_date", "")),
        _parse_datetime(getattr(record, "observed_at", "")),
        str(getattr(record, "source_id", "") or ""),
    )


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
