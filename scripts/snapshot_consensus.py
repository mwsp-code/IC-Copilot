from __future__ import annotations

import argparse
import calendar
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from equity_research.alerts import generate_consensus_alerts
from equity_research import config
from equity_research.expectations import attach_revision_history
from equity_research.external_evidence import WisburgEvidenceProvider
from equity_research.models import CompanyIdentity, DailySnapshotStatus
from equity_research.providers import StooqPriceClient, consensus_provider_from_env
from equity_research.research_store import ResearchStore
from equity_research.sec_client import SecClient
from equity_research.wisburg_lens import build_wisburg_lens
from equity_research.wisburg_monitor import compare_wisburg_lenses, generate_wisburg_alerts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Snapshot point-in-time consensus, prices, and optional Wisburg context for a local watchlist."
    )
    parser.add_argument("--watchlist", default="default")
    parser.add_argument("--tickers", help="Comma-separated tickers instead of the saved watchlist.")
    parser.add_argument("--db", type=Path, help="Optional SQLite path override.")
    parser.add_argument("--force", action="store_true", help="Run on weekends as well.")
    parser.add_argument(
        "--wisburg",
        choices=("auto", "on", "off"),
        default="auto",
        help="auto uses Wisburg when its key is configured; on records missing-key as a gap; off skips it.",
    )
    parser.add_argument(
        "--refresh-wisburg",
        action="store_true",
        help="Ignore today's Wisburg cache and fetch a new capped research lens.",
    )
    args = parser.parse_args()

    if not is_us_trading_day(date.today()) and not args.force:
        print("US market holiday/weekend: no snapshot collected. Pass --force to override.")
        return 0

    config.refresh_runtime_secrets()
    store = ResearchStore(args.db) if args.db else ResearchStore()
    provider = consensus_provider_from_env(store)
    price_client = StooqPriceClient(store=store)
    if args.tickers:
        tickers = sorted({item.strip().upper() for item in args.tickers.split(",") if item.strip()})
    else:
        tickers = [item.ticker for item in store.list_watchlist(args.watchlist)]
    if not tickers:
        print("No tickers found. Add tickers in the app or pass --tickers AAPL,MSFT.")
        return 1

    wisburg_enabled = args.wisburg == "on" or (args.wisburg == "auto" and bool(config.WISBURG_API_KEY))
    wisburg_provider = (
        WisburgEvidenceProvider(api_key=config.WISBURG_API_KEY, enabled=True)
        if wisburg_enabled else None
    )
    statuses = collect_daily_snapshots(
        store=store,
        tickers=tickers,
        consensus_provider=provider,
        price_client=price_client,
        wisburg_provider=wisburg_provider,
        watchlist=args.watchlist,
        wisburg_mode=args.wisburg,
        refresh_wisburg=args.refresh_wisburg,
    )
    for status in statuses:
        row_change = status.consensus_rows_after - status.consensus_rows_before
        print(
            f"{status.ticker}: {status.overall_status}; consensus={status.consensus_status}; "
            f"price={status.price_status}; wisburg={status.wisburg_status}; "
            f"consensus_row_change={row_change:+d}; alerts={status.alerts_created}; "
            f"wisburg_cache={'reused' if status.used_same_day_wisburg_cache else 'not_reused'}"
        )
        for gap in status.data_gaps[:4]:
            print(f"  gap: {gap}")
    print(
        "Point-in-time note: snapshots collected today can support future 7/30/90-day revisions; "
        "they cannot prove pre-event consensus for events that occurred before the snapshot date."
    )
    return 1 if statuses and all(item.overall_status == "Unavailable" for item in statuses) else 0


def collect_daily_snapshots(
    store: ResearchStore,
    tickers: list[str],
    consensus_provider: Any,
    price_client: StooqPriceClient,
    wisburg_provider: WisburgEvidenceProvider | Any | None = None,
    watchlist: str = "default",
    wisburg_mode: str = "auto",
    refresh_wisburg: bool = False,
    snapshot_day: date | None = None,
    sec_client: SecClient | Any | None = None,
) -> list[DailySnapshotStatus]:
    run_day = snapshot_day or date.today()
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sec = sec_client or SecClient()
    statuses: list[DailySnapshotStatus] = []

    for raw_ticker in tickers:
        ticker = raw_ticker.upper()
        gaps: list[str] = []
        alert_count = 0
        before_count = _stored_snapshot_count(store, ticker)

        price_status = "Unavailable"
        latest_price: float | None = None
        try:
            market_context = price_client.recent_market_context(ticker)
            price_status = market_context.status
            latest_price = market_context.current_price
            gaps.extend(f"Price: {item}" for item in market_context.data_gaps[:2])
        except Exception as exc:
            gaps.append(f"Price snapshot failed: {_safe_error(exc)}")

        consensus_status = "Unavailable"
        try:
            package = consensus_provider.fetch_package(ticker, latest_price)
            store.save_consensus_package(package)
            attach_revision_history(package, store)
            alerts = generate_consensus_alerts(package, store)
            alert_count += len(alerts)
            consensus_status = package.status
            gaps.extend(f"Consensus: {item}" for item in package.data_gaps[:3])
            store.set_provider_health(
                package.provider,
                package.status,
                "; ".join(package.data_gaps) or "Daily point-in-time snapshot succeeded.",
            )
        except Exception as exc:
            gaps.append(f"Consensus snapshot failed: {_safe_error(exc)}")
            store.set_provider_health("Consensus daily snapshot", "Unavailable", gaps[-1])

        wisburg_status = "Disabled"
        used_same_day_cache = False
        if wisburg_provider is None:
            if wisburg_mode == "on":
                wisburg_status = "Missing key"
                gaps.append("Wisburg: requested but WISBURG_API_KEY is not configured.")
            elif wisburg_mode == "auto":
                wisburg_status = "Not configured"
        else:
            cached = None if refresh_wisburg else store.wisburg_lens_on_date(ticker, run_day.isoformat())
            if cached is None and not refresh_wisburg:
                prior_run = store.latest_daily_snapshot_status(ticker)
                if (
                    prior_run
                    and prior_run.get("run_date") == run_day.isoformat()
                    and _usable_status(str(prior_run.get("wisburg_status") or ""))
                ):
                    cached = store.latest_wisburg_lens(ticker)
            if cached and _usable_status(str(cached.get("status") or "")):
                wisburg_status = "Cached"
                used_same_day_cache = True
                cached_observed_at = str(cached.get("observed_at") or observed_at)
                latest_delta = store.latest_wisburg_delta(ticker)
                if not latest_delta or str(latest_delta.get("observed_at") or "") != cached_observed_at:
                    prior = store.latest_wisburg_lens(ticker, before=cached_observed_at)
                    delta = compare_wisburg_lenses(cached, prior)
                    store.save_wisburg_delta(delta)
                    alert_count += len(generate_wisburg_alerts(delta, store))
            else:
                try:
                    prior = store.latest_wisburg_lens(ticker)
                    identity = _daily_identity(ticker, sec)
                    evidence = wisburg_provider.fetch(identity, [])
                    lens = build_wisburg_lens(identity, evidence)
                    store.save_wisburg_lens(lens)
                    delta = compare_wisburg_lenses(lens, prior)
                    store.save_wisburg_delta(delta)
                    wisburg_alerts = generate_wisburg_alerts(delta, store)
                    alert_count += len(wisburg_alerts)
                    wisburg_status = lens.status
                    gaps.extend(f"Wisburg: {item}" for item in evidence.data_gaps[:3])
                    store.set_provider_health(
                        "Wisburg research",
                        lens.status,
                        f"{delta.summary} Listing metadata/excerpts only; not official consensus.",
                    )
                except Exception as exc:
                    wisburg_status = "Unavailable"
                    gaps.append(f"Wisburg snapshot failed: {_safe_error(exc)}")
                    store.set_provider_health("Wisburg research", "Unavailable", gaps[-1])

        after_count = _stored_snapshot_count(store, ticker)
        store.touch_watchlist(ticker, watchlist)
        core_available = any(_usable_status(value) for value in (consensus_status, price_status))
        any_available = core_available or _usable_status(wisburg_status)
        provider_gap = any(
            value.lower().startswith(("unavailable", "missing key"))
            for value in (consensus_status, price_status, wisburg_status)
        )
        overall = "Partial" if any_available and provider_gap else "Available" if any_available else "Unavailable"
        status = DailySnapshotStatus(
            ticker=ticker,
            run_date=run_day.isoformat(),
            observed_at=observed_at,
            overall_status=overall,
            consensus_status=consensus_status,
            price_status=price_status,
            wisburg_status=wisburg_status,
            consensus_rows_before=before_count,
            consensus_rows_after=after_count,
            alerts_created=alert_count,
            used_same_day_wisburg_cache=used_same_day_cache,
            data_gaps=_dedupe(gaps)[:12],
        )
        store.save_daily_snapshot_status(status)
        statuses.append(status)
    return statuses


def _daily_identity(ticker: str, sec_client: Any) -> CompanyIdentity:
    try:
        return sec_client.map_ticker(ticker)
    except Exception:
        return CompanyIdentity(ticker=ticker.upper(), cik="", name=ticker.upper())


def _safe_error(exc: Exception) -> str:
    message = " ".join(str(exc).split())[:500]
    return message.replace("api_token=", "api_token=[redacted]").replace("apikey=", "apikey=[redacted]")


def _dedupe(values: list[str]) -> list[str]:
    rows: list[str] = []
    for value in values:
        if value and value not in rows:
            rows.append(value)
    return rows


def _usable_status(value: str) -> bool:
    normalized = value.lower()
    return normalized.startswith(("available", "partial", "cached"))


def _stored_snapshot_count(store: ResearchStore, ticker: str) -> int:
    ticker = ticker.upper()
    tables = (
        "consensus_snapshots",
        "estimate_snapshots",
        "recommendation_snapshots",
        "earnings_surprises",
    )
    total = 0
    with store.connect() as db:
        for table in tables:
            row = db.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE ticker=?", (ticker,)).fetchone()
            total += int(row["count"] if row else 0)
    return total


def is_us_trading_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    year = day.year
    holidays = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, calendar.MONDAY, 3),
        _nth_weekday(year, 2, calendar.MONDAY, 3),
        _easter_sunday(year).fromordinal(_easter_sunday(year).toordinal() - 2),
        _last_weekday(year, 5, calendar.MONDAY),
        _observed(date(year, 6, 19)),
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, calendar.MONDAY, 1),
        _nth_weekday(year, 11, calendar.THURSDAY, 4),
        _observed(date(year, 12, 25)),
    }
    return day not in holidays


def _observed(day: date) -> date:
    if day.weekday() == calendar.SATURDAY:
        return day.fromordinal(day.toordinal() - 1)
    if day.weekday() == calendar.SUNDAY:
        return day.fromordinal(day.toordinal() + 1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return date(year, month, 1 + offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last_day = calendar.monthrange(year, month)[1]
    candidate = date(year, month, last_day)
    return candidate.fromordinal(candidate.toordinal() - (candidate.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    month_adjustment = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * month_adjustment) // 451
    month = (h + month_adjustment - 7 * m + 114) // 31
    day = (h + month_adjustment - 7 * m + 114) % 31 + 1
    return date(year, month, day)


if __name__ == "__main__":
    raise SystemExit(main())
