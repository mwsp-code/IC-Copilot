from __future__ import annotations

import csv
import json
import math
import re
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from statistics import mean, median, pstdev
from threading import Lock
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import config
from .models import (
    ConsensusPackage,
    EarningsSurprise,
    EventWindowReaction,
    EstimatePoint,
    ProviderComparison,
    ProviderObservation,
    RecentMarketContext,
    MarketWindowPerformance,
    PriceProviderStatus,
    ProviderStatus,
    RecommendationConsensus,
    RevisionWindow,
    TargetConsensus,
)
from .network_diagnostics import network_message_hint

if TYPE_CHECKING:
    from .research_store import ResearchStore


@dataclass(frozen=True)
class PriceReaction:
    ticker: str
    event_date: str | None
    start_price: float | None
    latest_price: float | None
    reaction_pct: float | None
    source: str
    note: str = ""
    benchmark_ticker: str | None = None
    benchmark_reaction_pct: float | None = None
    abnormal_reaction_pct: float | None = None
    volatility_adjusted_move: float | None = None
    volume_ratio: float | None = None
    beta: float | None = None
    return_1d_pct: float | None = None
    return_5d_pct: float | None = None
    return_20d_pct: float | None = None
    abnormal_20d_pct: float | None = None
    path_min_20d_pct: float | None = None
    path_max_20d_pct: float | None = None


@dataclass(frozen=True)
class NarrativeSignal:
    status: str
    label: str
    recent_average: float | None
    baseline_average: float | None
    ratio: float | None
    source: str
    note: str = ""


@dataclass(frozen=True)
class _DailyRowsResult:
    provider: str
    status: str
    message: str
    rows: list[dict]
    official: bool = True
    adjusted: bool = False
    source_url: str | None = None


class StooqPriceClient:
    """No-key daily price provider used for MVP market-capture checks."""

    def __init__(
        self,
        timeout_seconds: int = 6,
        store: ResearchStore | None = None,
        alpha_vantage_key: str | None = None,
        tiingo_key: str | None = None,
        eodhd_key: str | None = None,
        eodhd_max_calls: int | None = None,
        enable_yahoo_price: bool | None = None,
        csv_price_dir: Path | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.store = store
        self.alpha_vantage_key = alpha_vantage_key if alpha_vantage_key is not None else config.ALPHAVANTAGE_API_KEY
        self.tiingo_key = tiingo_key if tiingo_key is not None else config.TIINGO_API_KEY
        self.eodhd_key = eodhd_key if eodhd_key is not None else config.EODHD_API_KEY
        self.eodhd_max_calls = max(
            0,
            eodhd_max_calls if eodhd_max_calls is not None else config.EODHD_MAX_CALLS_PER_RUN,
        )
        self._eodhd_calls = 0
        self._eodhd_call_lock = Lock()
        self.enable_yahoo_price = config.ENABLE_YAHOO_PRICE_FALLBACK if enable_yahoo_price is None else enable_yahoo_price
        self.csv_price_dir = csv_price_dir or config.PRICE_CSV_DIR
        self._rows_cache: dict[str, list[dict]] = {}
        self._result_cache: dict[str, _DailyRowsResult] = {}
        self._status_history: dict[str, list[PriceProviderStatus]] = {}
        self._event_window_cache: dict[tuple[str, str, str, str, str], EventWindowReaction] = {}
        self._benchmark_window_cache: dict[tuple[str, str], dict[str, float | None]] = {}
        self._beta_cache: dict[tuple[str, str, str], float | None] = {}
        self._ticker_lock_guard = Lock()
        self._ticker_locks: dict[str, Lock] = {}

    def event_window_reaction(
        self,
        ticker: str,
        event_id: str,
        event_date: str | None,
        event_timestamp: str | None = None,
        market_benchmark: str = "SPY",
        sector_benchmark: str | None = None,
    ) -> EventWindowReaction:
        normalized = ticker.upper()
        if not event_date:
            return EventWindowReaction(
                normalized, event_id, None, event_timestamp, None, None,
                "Stooq daily prices", "event_date_missing", "The event has no usable date.",
                confidence="Low", benchmark_ticker=market_benchmark,
                sector_benchmark_ticker=sector_benchmark,
            )
        cache_key = (
            normalized,
            event_date[:10],
            event_timestamp or "",
            market_benchmark.upper() if market_benchmark else "",
            sector_benchmark.upper() if sector_benchmark else "",
        )
        cached_reaction = self._event_window_cache.get(cache_key)
        if cached_reaction is not None:
            return replace(cached_reaction, event_id=event_id)
        event_day = _event_trading_target(event_date, event_timestamp)
        rows = self._cached_daily_rows(normalized)
        if not _rows_cover_event(rows, event_day):
            refreshed = self._fetch_daily_rows_result(normalized, bypass_cache=True)
            self._rows_cache[normalized] = refreshed.rows
            self._result_cache[normalized] = refreshed
            rows = refreshed.rows
        source = self._price_source(normalized)
        if not rows:
            failure = self._price_failure(normalized)
            return EventWindowReaction(
                normalized, event_id, event_date, event_timestamp, None, None,
                source.provider, failure.status,
                failure.message, confidence="Low",
                benchmark_ticker=market_benchmark, sector_benchmark_ticker=sector_benchmark,
                corporate_action_adjusted=source.adjusted,
            )
        anchor_index = next(
            (index for index, row in enumerate(rows) if row["date"] >= event_day),
            None,
        )
        if anchor_index is None:
            return EventWindowReaction(
                normalized, event_id, event_date, event_timestamp, None, rows[-1]["close"],
                source.provider, "window_pending",
                "The event trading day is later than the latest cached price bar.", confidence="Low",
                benchmark_ticker=market_benchmark, sector_benchmark_ticker=sector_benchmark,
                corporate_action_adjusted=source.adjusted,
            )
        if anchor_index < 1:
            return EventWindowReaction(
                normalized, event_id, event_date, event_timestamp, None, None,
                source.provider, "insufficient_history",
                "No prior close and event trading day are available.", confidence="Low",
                benchmark_ticker=market_benchmark, sector_benchmark_ticker=sector_benchmark,
                corporate_action_adjusted=source.adjusted,
            )
        anchor_date = rows[anchor_index]["date"]
        prior_close = rows[anchor_index - 1]["close"]
        raw = _fixed_window_returns(rows, anchor_index, prior_close)
        market_rows = self._cached_daily_rows(market_benchmark)
        market = self._cached_benchmark_window_returns(market_benchmark, market_rows, anchor_date)
        sector_rows = self._cached_daily_rows(sector_benchmark) if sector_benchmark else []
        sector = self._cached_benchmark_window_returns(sector_benchmark, sector_rows, anchor_date) if sector_rows and sector_benchmark else {}
        beta = self._cached_strict_pre_event_beta(normalized, market_benchmark, rows, market_rows, anchor_index, anchor_date)
        market_relative = _subtract_windows(raw, market)
        sector_relative = _subtract_windows(raw, sector)
        beta_adjusted = {
            key: (value - beta * market[key])
            if value is not None and beta is not None and market.get(key) is not None
            else None
            for key, value in raw.items()
        }
        prior_volumes = [
            row["volume"] for row in rows[max(0, anchor_index - 60):anchor_index]
            if row["volume"] > 0
        ]
        volume_ratio = (
            rows[anchor_index]["volume"] / mean(prior_volumes)
            if prior_volumes and rows[anchor_index]["volume"] > 0 else None
        )
        path = _path_from_prior_close(rows, anchor_index, prior_close, 20)
        complete = all(raw.get(window) is not None for window in ("1d", "5d", "20d"))
        pending_windows = [
            window for window in ("1d", "5d", "20d")
            if raw.get(window) is None
        ]
        confidence = "High" if event_timestamp else "Medium"
        reason = "" if complete else f"Forward event windows pending: {', '.join(pending_windows)}."
        reaction = EventWindowReaction(
            ticker=normalized,
            event_id=event_id,
            event_date=event_date,
            event_timestamp=event_timestamp,
            anchor_date=anchor_date.isoformat(),
            prior_close=prior_close,
            source=source.provider,
            status="available" if complete else "window_pending",
            reason=reason,
            confidence=confidence,
            benchmark_ticker=market_benchmark,
            sector_benchmark_ticker=sector_benchmark,
            beta=beta,
            volume_ratio=volume_ratio,
            raw_returns=raw,
            market_relative_returns=market_relative,
            sector_relative_returns=sector_relative,
            beta_adjusted_returns=beta_adjusted,
            path_min_20d_pct=min(path) if path else None,
            path_max_20d_pct=max(path) if path else None,
            corporate_action_adjusted=source.adjusted,
        )
        self._event_window_cache[cache_key] = reaction
        return reaction

    def _cached_benchmark_window_returns(
        self,
        ticker: str | None,
        rows: list[dict],
        anchor_date: date,
    ) -> dict[str, float | None]:
        if not ticker:
            return {"1d": None, "5d": None, "20d": None}
        key = (ticker.upper(), anchor_date.isoformat())
        if key not in self._benchmark_window_cache:
            self._benchmark_window_cache[key] = _benchmark_window_returns(rows, anchor_date)
        return self._benchmark_window_cache[key]

    def _cached_strict_pre_event_beta(
        self,
        ticker: str,
        benchmark_ticker: str,
        rows: list[dict],
        benchmark_rows: list[dict],
        anchor_index: int,
        anchor_date: date,
    ) -> float | None:
        key = (ticker.upper(), benchmark_ticker.upper(), anchor_date.isoformat())
        if key not in self._beta_cache:
            self._beta_cache[key] = _strict_pre_event_beta(rows, benchmark_rows, anchor_index)
        return self._beta_cache[key]

    def price_reaction_since(
        self,
        ticker: str,
        event_date: str | None,
        max_lookback_days: int = 7,
        benchmark_ticker: str | None = "SPY",
    ) -> PriceReaction:
        if not event_date:
            return PriceReaction(ticker, None, None, None, None, "Daily price stack", "No event date.")
        rows = self._cached_daily_rows(ticker)
        if not rows:
            failure = self._price_failure(ticker)
            return PriceReaction(ticker, event_date, None, None, None, failure.provider, failure.message)
        source = self._price_source(ticker)

        event_dt = _parse_date(event_date)
        start_row = None
        for lookback in range(max_lookback_days + 1):
            candidate = event_dt - timedelta(days=lookback)
            start_row = next((row for row in rows if row["date"] == candidate), None)
            if start_row:
                break
        latest = rows[-1]
        if not start_row:
            return PriceReaction(
                ticker,
                event_date,
                None,
                latest["close"],
                None,
                source.provider,
                "No trading day near event date.",
            )

        start_index = rows.index(start_row)
        reaction = (latest["close"] / start_row["close"] - 1.0) * 100
        benchmark_reaction = None
        benchmark_rows: list[dict] = []
        benchmark_start = None
        if benchmark_ticker and benchmark_ticker.upper() != ticker.upper():
            benchmark_rows = self._cached_daily_rows(benchmark_ticker)
            benchmark_start = _row_near_date(benchmark_rows, event_dt, max_lookback_days)
            if benchmark_start and benchmark_rows:
                benchmark_reaction = (
                    benchmark_rows[-1]["close"] / benchmark_start["close"] - 1.0
                ) * 100
        beta = _pre_event_beta(rows, benchmark_rows, start_index) if benchmark_rows else None
        benchmark_factor = beta if beta is not None else 1.0
        abnormal = (
            reaction - benchmark_factor * benchmark_reaction
            if benchmark_reaction is not None else None
        )
        daily_returns = _returns_before(rows, start_index, 60)
        volatility = pstdev(daily_returns) if len(daily_returns) >= 10 else None
        horizon_days = max(1, len(rows) - 1 - start_index)
        adjusted = (
            abnormal / (volatility * math.sqrt(horizon_days))
            if abnormal is not None and volatility not in (None, 0) else None
        )
        recent_volumes = [row["volume"] for row in rows[max(0, start_index - 20):start_index] if row["volume"] > 0]
        volume_ratio = start_row["volume"] / mean(recent_volumes) if recent_volumes and start_row["volume"] else None
        return_1d = _window_return(rows, start_index, 1)
        return_5d = _window_return(rows, start_index, 5)
        return_20d = _window_return(rows, start_index, 20)
        benchmark_20d = None
        if benchmark_start and benchmark_rows:
            benchmark_20d = _window_return(benchmark_rows, benchmark_rows.index(benchmark_start), 20)
        abnormal_20d = (
            return_20d - benchmark_factor * benchmark_20d
            if return_20d is not None and benchmark_20d is not None else None
        )
        path = _window_path(rows, start_index, 20)
        return PriceReaction(
            ticker=ticker.upper(),
            event_date=event_date,
            start_price=start_row["close"],
            latest_price=latest["close"],
            reaction_pct=reaction,
            source=source.provider,
            benchmark_ticker=benchmark_ticker,
            benchmark_reaction_pct=benchmark_reaction,
            abnormal_reaction_pct=abnormal,
            volatility_adjusted_move=adjusted,
            volume_ratio=volume_ratio,
            beta=beta,
            return_1d_pct=return_1d,
            return_5d_pct=return_5d,
            return_20d_pct=return_20d,
            abnormal_20d_pct=abnormal_20d,
            path_min_20d_pct=min(path) if path else None,
            path_max_20d_pct=max(path) if path else None,
        )

    def _cached_daily_rows(self, ticker: str) -> list[dict]:
        key = ticker.upper()
        if key in self._rows_cache and key in self._result_cache:
            return self._rows_cache[key]
        with self._ticker_lock(key):
            if key not in self._rows_cache:
                result = self._fetch_daily_rows_result(key)
                self._rows_cache[key] = result.rows
                self._result_cache[key] = result
            elif key not in self._result_cache:
                self._result_cache[key] = _DailyRowsResult(
                    "Fixture daily prices", "available", "Rows injected by caller.",
                    self._rows_cache[key], official=True, adjusted=False,
                )
        return self._rows_cache[key]

    def _ticker_lock(self, ticker: str) -> Lock:
        with self._ticker_lock_guard:
            lock = self._ticker_locks.get(ticker)
            if lock is None:
                lock = Lock()
                self._ticker_locks[ticker] = lock
            return lock

    def _fetch_daily_rows(self, ticker: str) -> list[dict]:
        return self._fetch_daily_rows_result(ticker).rows

    def _fetch_daily_rows_result(self, ticker: str, bypass_cache: bool = False) -> _DailyRowsResult:
        key = ticker.upper()
        if not bypass_cache:
            cached = self._load_cached_rows(key)
            if cached.rows:
                self._record_price_status(key, cached)
                return cached

        attempts: list[_DailyRowsResult] = []
        for fetcher in (
            self._fetch_tiingo_rows,
            self._fetch_eodhd_rows,
            self._fetch_stooq_rows,
            self._fetch_alpha_vantage_rows,
            self._fetch_csv_rows,
            self._fetch_yahoo_rows,
        ):
            result = fetcher(key)
            attempts.append(result)
            self._record_price_status(key, result)
            if result.rows:
                self._save_cached_rows(key, result)
                return result
        failure = self._collapsed_failure(key, attempts)
        self._record_price_status(key, failure)
        return failure

    def _load_cached_rows(self, ticker: str) -> _DailyRowsResult:
        if not self.store:
            return _DailyRowsResult("SQLite price cache", "disabled", "No price cache store configured.", [])
        try:
            cached = self.store.load_price_bars(ticker)
        except Exception:
            return _DailyRowsResult("SQLite price cache", "malformed_response", "Cached price bars could not be read.", [])
        if not cached:
            return _DailyRowsResult("SQLite price cache", "no_data", "No same-day cached price bars were found.", [])
        rows = [
            {"date": _parse_date(row["price_date"]), "close": row["close"], "volume": row.get("volume") or 0}
            for row in cached.get("rows", [])
            if row.get("price_date") and row.get("close") is not None
        ]
        if not rows:
            return _DailyRowsResult("SQLite price cache", "malformed_response", "Cached price bars were empty or malformed.", [])
        rows.sort(key=lambda row: row["date"])
        return _DailyRowsResult(
            cached.get("provider") or "SQLite price cache",
            "available",
            "Loaded daily prices from same-day SQLite cache.",
            rows,
            official=bool(cached.get("official", True)),
            adjusted=bool(cached.get("adjusted", False)),
            source_url=cached.get("source_url"),
        )

    def _save_cached_rows(self, ticker: str, result: _DailyRowsResult) -> None:
        if not self.store or not result.rows:
            return
        try:
            self.store.save_price_bars(
                ticker, result.provider, result.rows,
                adjusted=result.adjusted, official=result.official,
                source_url=result.source_url,
            )
        except Exception:
            return

    def _record_price_status(self, ticker: str, result: _DailyRowsResult) -> None:
        status = PriceProviderStatus(
            ticker=ticker.upper(),
            provider=result.provider,
            status=result.status,
            observed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            message=result.message,
            official=result.official,
            adjusted=result.adjusted,
            source_url=result.source_url,
        )
        self._status_history.setdefault(ticker.upper(), []).append(status)
        if not self.store:
            return
        try:
            self.store.save_price_provider_status(status)
        except Exception:
            return

    def _price_source(self, ticker: str) -> _DailyRowsResult:
        key = ticker.upper()
        return self._result_cache.get(key) or _DailyRowsResult(
            "Daily price stack", "unknown", "Price source has not been resolved.", [],
        )

    def _price_failure(self, ticker: str) -> PriceProviderStatus:
        key = ticker.upper()
        history = self._status_history.get(key) or []
        if history:
            return history[-1]
        result = self._result_cache.get(key)
        if result:
            return PriceProviderStatus(
                key, result.provider, result.status,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                result.message, result.official, result.adjusted, result.source_url,
            )
        return PriceProviderStatus(
            key, "Daily price stack", "no_data",
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "No daily price history was returned for the symbol.",
        )

    def _collapsed_failure(self, ticker: str, attempts: list[_DailyRowsResult]) -> _DailyRowsResult:
        active = [item for item in attempts if item.status != "disabled"]
        if active and all(item.status == "no_data" for item in active):
            return _DailyRowsResult(
                "Daily price stack", "unsupported_symbol",
                "No configured daily price provider returned data for the symbol.", [],
                official=True,
            )
        for status in (
            "invalid_key", "entitlement_error", "rate_limited", "network_error",
            "local_budget_exhausted", "provider_blocked", "timeout", "malformed_response",
            "insufficient_history", "no_data",
        ):
            item = next((attempt for attempt in active if attempt.status == status), None)
            if item:
                return _DailyRowsResult(
                    item.provider, item.status, item.message, [],
                    official=item.official, adjusted=item.adjusted, source_url=item.source_url,
                )
        return _DailyRowsResult(
            "Daily price stack", "no_data",
            "No configured daily price provider returned usable prices.", [],
        )

    def price_provider_statuses(self, ticker: str) -> list[PriceProviderStatus]:
        key = ticker.upper()
        self._cached_daily_rows(key)
        return list(self._status_history.get(key, []))

    def recent_market_context(
        self,
        ticker: str,
        benchmark_ticker: str = "SPY",
    ) -> RecentMarketContext:
        key = ticker.upper()
        rows = self._cached_daily_rows(key)
        source = self._price_source(key)
        if not rows:
            failure = self._price_failure(key)
            return RecentMarketContext(
                ticker=key,
                status="Unavailable",
                source=failure.provider,
                summary=failure.message,
                data_gaps=[failure.message],
            )
        benchmark_rows = (
            self._cached_daily_rows(benchmark_ticker)
            if benchmark_ticker and benchmark_ticker.upper() != key else []
        )
        benchmark_by_date = {row["date"]: row for row in benchmark_rows}
        windows: list[MarketWindowPerformance] = []
        for label, sessions in (("1 week", 5), ("1 month", 21), ("3 months", 63), ("6 months", 126), ("1 year", 252)):
            stock_return = _trailing_session_return(rows, sessions)
            benchmark_return = _aligned_trailing_return(rows, benchmark_by_date, sessions)
            relative = (
                stock_return - benchmark_return
                if stock_return is not None and benchmark_return is not None else None
            )
            windows.append(MarketWindowPerformance(
                label=label,
                sessions=sessions,
                return_pct=stock_return,
                benchmark_return_pct=benchmark_return,
                relative_return_pct=relative,
                status="available" if stock_return is not None else "insufficient_history",
            ))
        returns = [
            (rows[index]["close"] / rows[index - 1]["close"] - 1.0)
            for index in range(max(1, len(rows) - 60), len(rows))
            if rows[index - 1]["close"] not in (None, 0)
        ]
        volatility = pstdev(returns) * math.sqrt(252) * 100 if len(returns) >= 20 else None
        peak = rows[0]["close"]
        drawdowns: list[float] = []
        for row in rows:
            peak = max(peak, row["close"])
            drawdowns.append((row["close"] / peak - 1.0) * 100 if peak else 0.0)
        recent_volumes = [row["volume"] for row in rows[-20:] if row.get("volume", 0) > 0]
        baseline_volumes = [row["volume"] for row in rows[-80:-20] if row.get("volume", 0) > 0]
        volume_ratio = (
            mean(recent_volumes) / mean(baseline_volumes)
            if recent_volumes and baseline_volumes and mean(baseline_volumes) else None
        )
        three_month = next((item for item in windows if item.sessions == 63), None)
        implications: list[str] = []
        if three_month and three_month.relative_return_pct is not None:
            if three_month.relative_return_pct >= 10:
                implications.append(
                    "The shares have materially outperformed the broad market over three months; a constructive thesis may already be partly reflected in price."
                )
            elif three_month.relative_return_pct <= -10:
                implications.append(
                    "The shares have materially underperformed the broad market over three months; test whether fundamentals justify the discount or signal deterioration."
                )
            else:
                implications.append(
                    "Three-month relative performance is within a moderate range; price alone does not establish strong market capture."
                )
        if volume_ratio is not None and volume_ratio >= 1.5:
            implications.append("Recent trading volume is elevated versus the preceding baseline, increasing the relevance of event and positioning checks.")
        if drawdowns and min(drawdowns) <= -25:
            implications.append("The trailing price history contains a drawdown of at least 25%; scenario downside and catalyst timing deserve explicit treatment.")
        gaps = []
        if len(rows) < 253:
            gaps.append(
                "Less than 252 trading sessions are available; one-year return and long-window beta evidence may be incomplete under the free entitlement."
            )
        if not benchmark_rows:
            gaps.append(f"{benchmark_ticker} benchmark history is unavailable; relative returns are incomplete.")
        return RecentMarketContext(
            ticker=key,
            status="Available" if any(item.return_pct is not None for item in windows) else "Partial",
            source=source.provider,
            summary=(
                f"Recent market behavior is calculated from {source.provider} daily bars and compared with {benchmark_ticker}. "
                "It is price context, not evidence of the fundamental cause."
            ),
            price_as_of=rows[-1]["date"].isoformat(),
            current_price=rows[-1]["close"],
            adjusted=source.adjusted,
            windows=windows,
            annualized_volatility_pct=volatility,
            max_drawdown_pct=min(drawdowns) if drawdowns else None,
            recent_volume_ratio=volume_ratio,
            thesis_implications=implications,
            data_gaps=gaps,
        )

    def _fetch_stooq_rows(self, ticker: str) -> _DailyRowsResult:
        symbol = f"{ticker.lower().replace('.', '-')}.us"
        url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
        req = Request(url, headers={"User-Agent": "US Equity Research Radar/0.1"})
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                text = response.read().decode("utf-8", errors="replace")
        except TimeoutError:
            return _DailyRowsResult("Stooq daily prices", "timeout", "Stooq request timed out.", [], source_url=url)
        except (HTTPError, URLError) as exc:
            return _DailyRowsResult("Stooq daily prices", "provider_blocked", str(exc), [], source_url=url)
        if _looks_like_html_challenge(text):
            return _DailyRowsResult(
                "Stooq daily prices", "provider_blocked",
                "Stooq returned an HTML browser-verification page instead of CSV.",
                [], source_url=url,
            )
        rows = _csv_daily_rows(text, date_field="Date", close_fields=("Close",), volume_field="Volume")
        if rows is None:
            return _DailyRowsResult("Stooq daily prices", "malformed_response", "Stooq response was not valid daily-price CSV.", [], source_url=url)
        if not rows:
            return _DailyRowsResult("Stooq daily prices", "no_data", "Stooq returned no daily price rows.", [], source_url=url)
        return _DailyRowsResult("Stooq daily prices", "available", "Stooq daily prices available.", rows, official=True, adjusted=False, source_url=url)

    def _fetch_alpha_vantage_rows(self, ticker: str) -> _DailyRowsResult:
        if not self.alpha_vantage_key:
            return _DailyRowsResult("Alpha Vantage daily prices", "disabled", "No Alpha Vantage key configured.", [])
        query = urlencode({
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": ticker.upper(),
            "outputsize": "full",
            "apikey": self.alpha_vantage_key,
        })
        url = f"{config.ALPHAVANTAGE_BASE_URL}?{query}"
        req = Request(url, headers={"User-Agent": "US Equity Research Radar/0.1"})
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except TimeoutError:
            return _DailyRowsResult("Alpha Vantage daily prices", "timeout", "Alpha Vantage request timed out.", [], source_url=url)
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            return _DailyRowsResult("Alpha Vantage daily prices", "malformed_response", str(exc), [], source_url=url)
        if payload.get("Note") or payload.get("Information"):
            return _DailyRowsResult(
                "Alpha Vantage daily prices", "provider_blocked",
                str(payload.get("Note") or payload.get("Information")),
                [], source_url=url,
            )
        series = payload.get("Time Series (Daily)") or {}
        rows: list[dict] = []
        for price_date, values in series.items():
            try:
                close = values.get("5. adjusted close") or values.get("4. close")
                rows.append({
                    "date": _parse_date(price_date),
                    "close": float(close),
                    "volume": float(values.get("6. volume") or values.get("5. volume") or 0),
                })
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda row: row["date"])
        if not rows:
            return _DailyRowsResult("Alpha Vantage daily prices", "no_data", "Alpha Vantage returned no daily price rows.", [], source_url=url)
        return _DailyRowsResult("Alpha Vantage daily prices", "available", "Alpha Vantage adjusted daily prices available.", rows, official=True, adjusted=True, source_url=url)

    def _fetch_tiingo_rows(self, ticker: str) -> _DailyRowsResult:
        if not self.tiingo_key:
            return _DailyRowsResult("Tiingo EOD prices", "disabled", "No Tiingo key configured.", [])
        symbol = _tiingo_symbol(ticker)
        public_url = f"https://api.tiingo.com/tiingo/daily/{symbol}/prices"
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=5 * 366)
        query = urlencode({
            "token": self.tiingo_key,
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "resampleFreq": "daily",
        })
        url = f"{public_url}?{query}"
        req = Request(url, headers={"User-Agent": "US Equity Research Radar/0.1"})
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except TimeoutError:
            return _DailyRowsResult("Tiingo EOD prices", "timeout", "Tiingo request timed out.", [], source_url=public_url)
        except HTTPError as exc:
            status = "invalid_key" if exc.code in {401, 403} else "provider_blocked"
            return _DailyRowsResult("Tiingo EOD prices", status, f"Tiingo returned HTTP {exc.code}.", [], source_url=public_url)
        except (URLError, json.JSONDecodeError) as exc:
            return _DailyRowsResult("Tiingo EOD prices", "malformed_response", _redact_provider_message(str(exc)), [], source_url=public_url)
        if isinstance(payload, dict) and payload.get("error"):
            return _DailyRowsResult("Tiingo EOD prices", "invalid_key", _redact_provider_message(str(payload.get("error"))), [], source_url=public_url)
        if not isinstance(payload, list):
            return _DailyRowsResult("Tiingo EOD prices", "malformed_response", "Tiingo returned an unexpected response shape.", [], source_url=public_url)
        rows: list[dict] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                close = item.get("adjClose")
                if close is None:
                    close = item.get("close")
                volume = item.get("adjVolume")
                if volume is None:
                    volume = item.get("volume") or 0
                rows.append({
                    "date": _parse_date(str(item.get("date") or "")[:10]),
                    "close": float(close),
                    "volume": float(volume or 0),
                })
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda row: row["date"])
        if not rows:
            return _DailyRowsResult("Tiingo EOD prices", "no_data", "Tiingo returned no usable daily price rows.", [], source_url=public_url)
        if len(rows) < 2:
            return _DailyRowsResult(
                "Tiingo EOD prices",
                "insufficient_history",
                "Tiingo returned a latest quote but not enough daily history for an event window.",
                [],
                source_url=public_url,
            )
        return _DailyRowsResult("Tiingo EOD prices", "available", "Tiingo adjusted EOD prices available.", rows, official=True, adjusted=True, source_url=public_url)

    def _fetch_eodhd_rows(self, ticker: str) -> _DailyRowsResult:
        if not self.eodhd_key:
            return _DailyRowsResult("EODHD EOD prices", "disabled", "No EODHD key configured.", [])
        with self._eodhd_call_lock:
            if self._eodhd_calls >= self.eodhd_max_calls:
                return _DailyRowsResult(
                    "EODHD EOD prices",
                    "local_budget_exhausted",
                    "The per-run EODHD call budget is exhausted; cached and fallback price providers remain available.",
                    [],
                )
            self._eodhd_calls += 1
        symbol = _eodhd_symbol(ticker)
        public_url = f"https://eodhd.com/api/eod/{symbol}"
        end_date = datetime.now(timezone.utc).date()
        # The free entitlement currently exposes the trailing year. Paid plans can
        # return more history, but the same bounded request keeps research latency low.
        start_date = end_date - timedelta(days=370)
        query = urlencode({
            "api_token": self.eodhd_key,
            "fmt": "json",
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "period": "d",
            "order": "a",
        })
        request = Request(
            f"{public_url}?{query}",
            headers={"User-Agent": "US Equity Research Radar/0.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except TimeoutError:
            return _DailyRowsResult(
                "EODHD EOD prices", "timeout", "EODHD request timed out.", [],
                source_url=public_url,
            )
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            if exc.code == 401:
                status = "invalid_key"
            elif exc.code == 403:
                status = "entitlement_error"
            elif exc.code == 429:
                status = "rate_limited"
            else:
                status = "provider_blocked"
            message = _safe_provider_message(body) or f"EODHD returned HTTP {exc.code}."
            return _DailyRowsResult(
                "EODHD EOD prices", status, message, [], source_url=public_url,
            )
        except URLError as exc:
            return _DailyRowsResult(
                "EODHD EOD prices", "network_error", _redact_provider_message(str(exc)), [],
                source_url=public_url,
            )
        except json.JSONDecodeError:
            return _DailyRowsResult(
                "EODHD EOD prices", "malformed_response", "EODHD returned malformed JSON.", [],
                source_url=public_url,
            )
        if isinstance(payload, dict):
            message = payload.get("error") or payload.get("message") or payload.get("errors")
            status = "rate_limited" if "limit" in str(message).lower() else "malformed_response"
            return _DailyRowsResult(
                "EODHD EOD prices", status,
                _redact_provider_message(str(message or "EODHD returned an unexpected response shape.")),
                [], source_url=public_url,
            )
        if not isinstance(payload, list):
            return _DailyRowsResult(
                "EODHD EOD prices", "malformed_response", "EODHD returned an unexpected response shape.", [],
                source_url=public_url,
            )
        rows: list[dict] = []
        used_adjusted = False
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                adjusted_close = item.get("adjusted_close")
                close = adjusted_close if adjusted_close not in (None, "") else item.get("close")
                if close in (None, "") or not item.get("date"):
                    continue
                used_adjusted = used_adjusted or adjusted_close not in (None, "")
                rows.append({
                    "date": _parse_date(str(item["date"])),
                    "close": float(close),
                    "volume": float(item.get("volume") or 0),
                })
            except (TypeError, ValueError):
                continue
        rows.sort(key=lambda row: row["date"])
        if not rows:
            return _DailyRowsResult(
                "EODHD EOD prices", "no_data", "EODHD returned no usable daily price rows.", [],
                source_url=public_url,
            )
        if len(rows) < 2:
            return _DailyRowsResult(
                "EODHD EOD prices", "insufficient_history",
                "EODHD returned a latest price but not enough history for an event window.", [],
                source_url=public_url,
            )
        return _DailyRowsResult(
            "EODHD EOD prices", "available",
            "EODHD daily prices are available; free-plan history may be limited to the trailing year.",
            rows, official=True, adjusted=used_adjusted, source_url=public_url,
        )

    def _fetch_csv_rows(self, ticker: str) -> _DailyRowsResult:
        path = self.csv_price_dir / f"{ticker.upper()}.csv"
        if not path.exists():
            return _DailyRowsResult("CSV daily prices", "no_data", f"No CSV price import found at {path}.", [], source_url=str(path))
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return _DailyRowsResult("CSV daily prices", "malformed_response", str(exc), [], source_url=str(path))
        rows = _csv_daily_rows(
            text,
            date_field="Date",
            close_fields=("Adj Close", "Adjusted Close", "Close", "close"),
            volume_field="Volume",
        )
        if rows is None:
            return _DailyRowsResult("CSV daily prices", "malformed_response", "CSV import is missing Date and Close columns.", [], source_url=str(path))
        if not rows:
            return _DailyRowsResult("CSV daily prices", "no_data", "CSV import contained no usable rows.", [], source_url=str(path))
        return _DailyRowsResult("CSV daily prices", "available", "CSV daily prices available.", rows, official=False, adjusted=True, source_url=str(path))

    def _fetch_yahoo_rows(self, ticker: str) -> _DailyRowsResult:
        if not self.enable_yahoo_price:
            return _DailyRowsResult("Yahoo chart prices (unofficial)", "disabled", "Yahoo price fallback is disabled.", [], official=False)
        symbol = ticker.upper().replace(".", "-")
        period2 = int(datetime.now(timezone.utc).timestamp()) + 86400
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{symbol}?period1=0&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
        )
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 US Equity Research Radar/0.1"})
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except TimeoutError:
            return _DailyRowsResult("Yahoo chart prices (unofficial)", "timeout", "Yahoo chart request timed out.", [], official=False, source_url=url)
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            return _DailyRowsResult("Yahoo chart prices (unofficial)", "provider_blocked", str(exc), [], official=False, source_url=url)
        error = (payload.get("chart") or {}).get("error")
        if error:
            return _DailyRowsResult("Yahoo chart prices (unofficial)", "no_data", str(error), [], official=False, source_url=url)
        result = ((payload.get("chart") or {}).get("result") or [None])[0] or {}
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        adjusted = ((result.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
        closes = adjusted or quote.get("close") or []
        volumes = quote.get("volume") or []
        rows: list[dict] = []
        for index, timestamp in enumerate(timestamps):
            close = closes[index] if index < len(closes) else None
            if close is None:
                continue
            rows.append({
                "date": datetime.fromtimestamp(timestamp, timezone.utc).date(),
                "close": float(close),
                "volume": float(volumes[index] if index < len(volumes) and volumes[index] else 0),
            })
        rows.sort(key=lambda row: row["date"])
        if not rows:
            return _DailyRowsResult("Yahoo chart prices (unofficial)", "no_data", "Yahoo chart returned no daily price rows.", [], official=False, source_url=url)
        return _DailyRowsResult("Yahoo chart prices (unofficial)", "available", "Yahoo chart adjusted daily prices available for personal research.", rows, official=False, adjusted=bool(adjusted), source_url=url)


class GdeltNarrativeClient:
    """Optional secondary narrative-volume signal; never treated as thesis evidence."""

    base_url = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, timeout_seconds: int = 12) -> None:
        self.timeout_seconds = timeout_seconds

    def saturation(self, company_name: str, ticker: str) -> NarrativeSignal:
        query = f'"{company_name}" OR "{ticker.upper()}"'
        url = f"{self.base_url}?{urlencode({'query': query, 'mode': 'TimelineVolRaw', 'format': 'json', 'timespan': '3months'})}"
        request = Request(url, headers={"User-Agent": "EquityResearchRadar/0.3"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            return NarrativeSignal(
                "Unavailable", "Unavailable", None, None, None, "GDELT DOC 2.0",
                f"GDELT narrative volume unavailable: {exc}",
            )
        values = _gdelt_timeline_values(payload)
        if len(values) < 14:
            return NarrativeSignal(
                "Unavailable", "Unavailable", None, None, None, "GDELT DOC 2.0",
                "GDELT returned insufficient timeline history.",
            )
        recent = mean(values[-7:])
        baseline = median(values[:-7])
        ratio = recent / baseline if baseline else None
        label = (
            "High narrative saturation" if ratio is not None and ratio >= 1.75
            else "Low narrative saturation" if ratio is not None and ratio <= 0.6
            else "Normal narrative saturation"
        )
        return NarrativeSignal(
            "Available", label, recent, baseline, ratio, "GDELT DOC 2.0",
            "Secondary news-volume context only; not primary thesis evidence.",
        )


class ConsensusAdapter:
    """Provider-neutral consensus boundary with point-in-time store revisions."""

    provider_name = "Not connected"
    official_for_conviction = False

    def __init__(self, store: "ResearchStore | None" = None) -> None:
        self.store = store

    def fetch_targets(self, ticker: str) -> TargetConsensus | None:
        return None

    def fetch_estimates(self, ticker: str) -> list[EstimatePoint]:
        return []

    def fetch_recommendations(self, ticker: str) -> RecommendationConsensus | None:
        return None

    def fetch_surprises(self, ticker: str) -> list[EarningsSurprise]:
        return []

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        return ConsensusPackage(
            ticker=ticker.upper(),
            provider=self.provider_name,
            status="Unavailable",
            data_gaps=[
                "Consensus provider is not configured. Add an Alpha Vantage/Finnhub key, "
                "enable an unofficial fallback, or import CSV data."
            ],
        )

    def fetch_packages(self, ticker: str, current_price: float | None = None) -> list[ConsensusPackage]:
        return [self.fetch_package(ticker, current_price)]

    def revision_between(
        self,
        ticker: str,
        metric: str,
        start: str,
        end: str,
        period_end: str | None = None,
    ) -> RevisionWindow:
        if not self.store:
            return RevisionWindow(metric, 0, None, None, None, None, None)
        if metric.startswith("price_target_"):
            start_target = self.store.target_at_or_before(ticker, start, self.provider_name)
            end_target = self.store.target_at_or_before(ticker, end, self.provider_name)
            start_value = _target_primary_value(start_target)
            end_value = _target_primary_value(end_target)
            return RevisionWindow(
                metric=metric,
                window_days=max(0, (_parse_date(end) - _parse_date(start)).days),
                start_date=start_target.as_of if start_target else None,
                end_date=end_target.as_of if end_target else None,
                start_value=start_value,
                end_value=end_value,
                change_pct=_pct_change(start_value, end_value),
            )
        if not period_end:
            return RevisionWindow(metric, 0, None, None, None, None, None)
        return self.store.estimate_revision(
            ticker, metric, period_end, start, end, self.provider_name,
        )

    def revision_since(self, ticker: str, event_date: str | None) -> float | None:
        if not event_date:
            return None
        revision = self.revision_between(
            ticker,
            "price_target_mean",
            event_date,
            date.today().isoformat(),
        )
        return revision.change_pct


class ProviderError(RuntimeError):
    pass


class FmpConsensusProvider(ConsensusAdapter):
    provider_name = "FMP"
    official_for_conviction = True

    def __init__(
        self,
        api_key: str | None = None,
        store: "ResearchStore | None" = None,
        base_url: str | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        super().__init__(store)
        self.api_key = (api_key if api_key is not None else config.FMP_API_KEY).strip()
        self.base_url = (base_url or config.FMP_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._estimate_period_statuses: dict[str, tuple[str, str]] = {}

    def fetch_targets(self, ticker: str) -> TargetConsensus | None:
        rows = self._get("price-target-consensus", {"symbol": ticker.upper()})
        row = _first_row(rows)
        if not row:
            return None
        target = TargetConsensus(
            ticker=ticker.upper(),
            as_of=_today(),
            currency=str(row.get("currency") or "USD"),
            target_mean=_number(row, "targetConsensus", "targetMean", "priceTargetAverage"),
            target_median=_number(row, "targetMedian", "priceTargetMedian"),
            target_high=_number(row, "targetHigh", "priceTargetHigh"),
            target_low=_number(row, "targetLow", "priceTargetLow"),
            analyst_count=_integer(row, "analystCount", "numberAnalysts", "numberOfAnalysts"),
            provider_timestamp=_string(row, "lastUpdated", "date", "updatedAt"),
            source=self.provider_name,
        )
        if all(
            value is None
            for value in (target.target_mean, target.target_median, target.target_high, target.target_low)
        ):
            return None
        _enrich_target(target)
        return target

    def fetch_estimates(self, ticker: str) -> list[EstimatePoint]:
        estimates: list[EstimatePoint] = []
        self._estimate_period_statuses = {}
        periods = ("annual", "quarter")
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(
                    self._get,
                    "analyst-estimates",
                    {"symbol": ticker.upper(), "period": period, "page": 0, "limit": 8},
                ): period
                for period in periods
            }
            for future in as_completed(futures):
                period_type = futures[future]
                try:
                    rows = future.result()
                except ProviderError as exc:
                    message = _redact_provider_message(str(exc))
                    self._estimate_period_statuses[period_type] = (
                        _provider_failure_class(message), message,
                    )
                    continue
                normalized_rows = _rows(rows)
                before = len(estimates)
                for row in normalized_rows:
                    period_end = _string(row, "date", "periodEndDate", "fiscalDateEnding")
                    if period_end:
                        estimates.extend(_fmp_estimate_points(ticker, row, period_end, period_type))
                if len(estimates) > before:
                    self._estimate_period_statuses[period_type] = (
                        "available", f"Normalized {len(estimates) - before} {period_type} estimate point(s).",
                    )
                elif normalized_rows:
                    self._estimate_period_statuses[period_type] = (
                        "malformed_response", "Rows were returned but no supported estimate fields could be normalized.",
                    )
                else:
                    self._estimate_period_statuses[period_type] = (
                        "no_data", f"FMP returned no {period_type} analyst estimates.",
                    )
        estimates.sort(key=lambda item: (item.period_end, item.period_type, item.metric))
        return estimates

    def fetch_recommendations(self, ticker: str) -> RecommendationConsensus | None:
        rows = self._get("grades-consensus", {"symbol": ticker.upper()})
        row = _first_row(rows)
        if not row:
            return None
        recommendation = RecommendationConsensus(
            ticker=ticker.upper(),
            as_of=_today(),
            strong_buy=_integer(row, "strongBuy", "strongBuyCount") or 0,
            buy=_integer(row, "buy", "buyCount") or 0,
            hold=_integer(row, "hold", "holdCount") or 0,
            sell=_integer(row, "sell", "sellCount") or 0,
            strong_sell=_integer(row, "strongSell", "strongSellCount") or 0,
            consensus_label=_string(row, "consensus", "rating", "consensusLabel"),
            source=self.provider_name,
        )
        if not recommendation.consensus_label and not any(
            (recommendation.strong_buy, recommendation.buy, recommendation.hold,
             recommendation.sell, recommendation.strong_sell)
        ):
            return None
        return recommendation

    def fetch_surprises(self, ticker: str) -> list[EarningsSurprise]:
        # The stable API exposes ticker-level actual/estimate history through
        # /earnings. Keep the legacy route only as a compatibility fallback for
        # older installations and fixtures.
        stable_error: ProviderError | None = None
        try:
            rows = self._get("earnings", {"symbol": ticker.upper()})
        except ProviderError as exc:
            stable_error = exc
            rows = []
        if stable_error is not None:
            try:
                legacy_rows = self._get("earnings-surprises", {"symbol": ticker.upper()})
            except ProviderError as legacy_error:
                if stable_error:
                    raise ProviderError(
                        f"stable earnings endpoint: {stable_error}; legacy fallback: {legacy_error}"
                    ) from legacy_error
                raise
            rows = legacy_rows
        surprises: list[EarningsSurprise] = []
        for row in _rows(rows):
            period_end = _string(row, "fiscalDateEnding", "date", "periodEndDate")
            if not period_end:
                continue
            actual = _number(row, "epsActual", "actualEarningResult", "actualEps", "actualEPS")
            estimate = _number(row, "epsEstimated", "estimatedEarning", "estimatedEps", "estimatedEPS")
            surprise = _number(row, "surprisePercentage", "surprisePct")
            if surprise is None:
                surprise = _pct_change(estimate, actual)
            if actual is None and estimate is None:
                continue
            surprises.append(
                EarningsSurprise(ticker.upper(), period_end, actual, estimate, surprise, self.provider_name)
            )
        return surprises

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        if not self.api_key:
            package = ConsensusPackage(
                ticker=ticker.upper(), provider=self.provider_name, status="Unavailable",
                data_gaps=["FMP_API_KEY is not configured."],
            )
            package.provider_statuses = [ProviderStatus(
                self.provider_name, "Unavailable", True, "not_configured", _now(),
                "FMP_API_KEY is not configured.",
            )]
            return package
        gaps: list[str] = []
        endpoint_statuses: list[ProviderStatus] = []
        self._estimate_period_statuses = {}
        target = None
        recommendations = None
        estimates: list[EstimatePoint] = []
        surprises: list[EarningsSurprise] = []
        fetchers = (
            ("price targets", self.fetch_targets),
            ("recommendations", self.fetch_recommendations),
            ("analyst estimates", self.fetch_estimates),
            ("earnings surprises", self.fetch_surprises),
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(fetcher, ticker): label for label, fetcher in fetchers}
            for future in as_completed(futures):
                label = futures[future]
                try:
                    value = future.result()
                except ProviderError as exc:
                    message = _redact_provider_message(str(exc))
                    failure_class = _provider_failure_class(message)
                    fallback = _fmp_endpoint_fallback(label, failure_class)
                    detail = f"{label}: {message} {fallback}".strip()
                    gaps.append(detail)
                    endpoint_statuses.append(ProviderStatus(
                        f"FMP {label}", "Unavailable", True, failure_class, _now(), detail,
                    ))
                    continue
                except Exception as exc:  # Defensive: vendor schemas can drift independently by endpoint.
                    detail = (
                        f"{label}: malformed response ({_safe_provider_message(str(exc))}). "
                        f"{_fmp_endpoint_fallback(label, 'malformed_response')}"
                    )
                    gaps.append(detail)
                    endpoint_statuses.append(ProviderStatus(
                        f"FMP {label}", "Unavailable", True, "malformed_response", _now(), detail,
                    ))
                    continue
                if label == "price targets":
                    target = value
                elif label == "recommendations":
                    recommendations = value
                elif label == "analyst estimates":
                    estimates = value or []
                    for period, (period_status, period_message) in sorted(self._estimate_period_statuses.items()):
                        provider_label = f"FMP analyst estimates {period}"
                        detail = period_message
                        if period_status != "available":
                            detail = (
                                f"{period_message} "
                                f"{_fmp_endpoint_fallback('analyst estimates', period_status)}"
                            ).strip()
                            gaps.append(f"analyst estimates {period}: {detail}")
                        endpoint_statuses.append(ProviderStatus(
                            provider_label,
                            "Available" if period_status == "available" else "Unavailable",
                            True,
                            period_status,
                            _now(),
                            detail,
                        ))
                else:
                    surprises = value or []
                if value in (None, []):
                    if label == "analyst estimates" and self._estimate_period_statuses:
                        continue
                    detail = f"FMP returned no {label}. {_fmp_endpoint_fallback(label, 'no_data')}"
                    gaps.append(detail)
                    endpoint_statuses.append(ProviderStatus(
                        f"FMP {label}", "Unavailable", True, "no_data", _now(), detail,
                    ))
                elif not (label == "analyst estimates" and self._estimate_period_statuses):
                    endpoint_statuses.append(ProviderStatus(
                        f"FMP {label}", "Available", True, "available", _now(),
                        f"FMP {label} endpoint returned normalized data.",
                    ))
        if target:
            target.current_price = current_price
            target.observed_at = _now()
            target.source_as_of = target.provider_timestamp
            target.provenance = "Financial Modeling Prep stable API"
            _enrich_target(target)
        has_data = bool(target or recommendations or estimates or surprises)
        package = ConsensusPackage(
            ticker=ticker.upper(), provider=self.provider_name,
            status="Available" if has_data and not gaps else "Partial" if has_data else "Unavailable",
            target=target, recommendations=recommendations, estimates=estimates,
            surprises=surprises, data_gaps=gaps,
        )
        package.observations = _package_observations(package)
        overall_status = _provider_status(package, official=True)
        if package.status == "Partial":
            overall_status.message = (
                "FMP returned usable official data, with one or more endpoint or period-level gaps. "
                "See the FMP endpoint rows for the exact entitlement and fallback."
            )
        package.provider_statuses = [overall_status] + endpoint_statuses
        return package

    def _get(self, endpoint: str, params: dict) -> object:
        if not self.api_key:
            raise ProviderError("FMP_API_KEY is not configured")
        query = urlencode({**params, "apikey": self.api_key})
        request = Request(
            f"{self.base_url}/{endpoint}?{query}",
            headers={"Accept": "application/json", "User-Agent": "EquityResearchRadar/0.2"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(f"HTTP {exc.code}: {_safe_provider_message(body)}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"request failed: {exc}") from exc
        if isinstance(payload, dict) and (payload.get("Error Message") or payload.get("error")):
            raise ProviderError(str(payload.get("Error Message") or payload.get("error"))[:300])
        return payload


class AlphaVantageConsensusProvider(ConsensusAdapter):
    provider_name = "Alpha Vantage"
    official_for_conviction = True

    def __init__(
        self,
        api_key: str | None = None,
        store: "ResearchStore | None" = None,
        base_url: str | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        super().__init__(store)
        self.api_key = (api_key if api_key is not None else config.ALPHAVANTAGE_API_KEY).strip()
        self.base_url = base_url or config.ALPHAVANTAGE_BASE_URL
        self.timeout_seconds = timeout_seconds

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        if not self.api_key:
            return _unavailable_package(ticker, self.provider_name, "ALPHAVANTAGE_API_KEY is not configured.", True)
        try:
            row = self._get(ticker)
        except ProviderError as exc:
            return _unavailable_package(ticker, self.provider_name, str(exc), True)
        observed = _now()
        as_of = _today()
        rating_values = {
            "strong_buy": _integer(row, "AnalystRatingStrongBuy") or 0,
            "buy": _integer(row, "AnalystRatingBuy") or 0,
            "hold": _integer(row, "AnalystRatingHold") or 0,
            "sell": _integer(row, "AnalystRatingSell") or 0,
            "strong_sell": _integer(row, "AnalystRatingStrongSell") or 0,
        }
        analyst_count = sum(rating_values.values()) or None
        aggregate_target = _number(row, "AnalystTargetPrice")
        target = None
        if aggregate_target is not None:
            target = TargetConsensus(
                ticker=ticker.upper(), as_of=as_of, currency=str(row.get("Currency") or "USD"),
                target_aggregate=aggregate_target, analyst_count=analyst_count,
                current_price=current_price, source=self.provider_name,
                observed_at=observed, source_as_of=None,
                provenance="Alpha Vantage COMPANY_OVERVIEW",
                target_label="Aggregate Target", target_kind="aggregate",
            )
            _enrich_target(target)
        recommendation = None
        if analyst_count:
            recommendation = RecommendationConsensus(
                ticker=ticker.upper(), as_of=as_of, source=self.provider_name,
                observed_at=observed, source_as_of=None,
                provenance="Alpha Vantage COMPANY_OVERVIEW",
                consensus_label=_dominant_recommendation(rating_values),
                **rating_values,
            )
        observations = []
        for field, key in (
            ("forward_pe", "ForwardPE"), ("ev_to_revenue", "EVToRevenue"),
            ("ev_to_ebitda", "EVToEBITDA"), ("market_cap", "MarketCapitalization"),
            ("shares_outstanding", "SharesOutstanding"),
        ):
            value = _number(row, key)
            if value is not None:
                observations.append(ProviderObservation(
                    ticker.upper(), self.provider_name, field, observed, None,
                    value_numeric=value, currency=str(row.get("Currency") or "USD"),
                    provenance="Alpha Vantage COMPANY_OVERVIEW", official=True,
                ))
        package = ConsensusPackage(
            ticker.upper(), self.provider_name,
            "Available" if target or recommendation else "Unavailable",
            target=target, recommendations=recommendation,
            data_gaps=[] if target or recommendation else ["Alpha Vantage returned no analyst fields."],
        )
        package.observations = _package_observations(package) + observations
        package.provider_statuses = [_provider_status(package, official=True)]
        return package

    def _get(self, ticker: str) -> dict:
        query = urlencode({"function": "OVERVIEW", "symbol": ticker.upper(), "apikey": self.api_key})
        request = Request(f"{self.base_url}?{query}", headers={"User-Agent": "EquityResearchRadar/0.3"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Alpha Vantage request failed: {exc}") from exc
        message = payload.get("Note") or payload.get("Information") or payload.get("Error Message")
        if message:
            raise ProviderError(str(message)[:300])
        return payload


class FinnhubConsensusProvider(ConsensusAdapter):
    provider_name = "Finnhub"
    official_for_conviction = True

    def __init__(
        self,
        api_key: str | None = None,
        store: "ResearchStore | None" = None,
        base_url: str | None = None,
        timeout_seconds: int = 20,
    ) -> None:
        super().__init__(store)
        self.api_key = (api_key if api_key is not None else config.FINNHUB_API_KEY).strip()
        self.base_url = (base_url or config.FINNHUB_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        if not self.api_key:
            return _unavailable_package(ticker, self.provider_name, "FINNHUB_API_KEY is not configured.", True)
        try:
            payload = self._get(ticker)
        except ProviderError as exc:
            return _unavailable_package(ticker, self.provider_name, str(exc), True)
        row = _first_row(payload)
        if not row:
            return _unavailable_package(ticker, self.provider_name, "Finnhub returned no recommendation trend.", True)
        observed = _now()
        values = {
            "strong_buy": _integer(row, "strongBuy") or 0,
            "buy": _integer(row, "buy") or 0,
            "hold": _integer(row, "hold") or 0,
            "sell": _integer(row, "sell") or 0,
            "strong_sell": _integer(row, "strongSell") or 0,
        }
        source_as_of = _string(row, "period")
        recommendation = RecommendationConsensus(
            ticker.upper(), source_as_of or _today(), source=self.provider_name,
            observed_at=observed, source_as_of=source_as_of,
            provenance="Finnhub recommendation trends API",
            consensus_label=_dominant_recommendation(values), **values,
        )
        package = ConsensusPackage(
            ticker.upper(), self.provider_name, "Available", recommendations=recommendation,
            data_gaps=[
                "Finnhub recommendation trends provide historical rating-count direction, not price-target revision magnitude."
            ],
        )
        package.observations = _package_observations(package) + _recommendation_trend_observations(
            ticker, self.provider_name, payload, observed, "Finnhub recommendation trends API",
        )
        package.provider_statuses = [_provider_status(package, official=True)]
        return package

    def _get(self, ticker: str) -> object:
        query = urlencode({"symbol": ticker.upper(), "token": self.api_key})
        request = Request(
            f"{self.base_url}/stock/recommendation?{query}",
            headers={"User-Agent": "EquityResearchRadar/0.3"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            raise ProviderError(
                f"HTTP {exc.code}: recommendation endpoint unavailable."
            ) from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Finnhub request failed: {exc}") from exc


class NasdaqConsensusProvider(ConsensusAdapter):
    provider_name = "Nasdaq (unofficial)"
    official_for_conviction = False
    base_url = "https://api.nasdaq.com/api/analyst"

    def __init__(self, store: "ResearchStore | None" = None, timeout_seconds: int = 15) -> None:
        super().__init__(store)
        self.timeout_seconds = timeout_seconds

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        try:
            payload = self._get(ticker)
        except ProviderError as exc:
            return _unavailable_package(ticker, self.provider_name, str(exc), False)
        observed = _now()
        data = payload.get("data") if isinstance(payload, dict) else None
        estimates: list[EstimatePoint] = []
        if isinstance(data, dict):
            for key, period_type in (("quarterlyForecast", "quarter"), ("yearlyForecast", "annual")):
                section = data.get(key) or {}
                for row in section.get("rows", []) if isinstance(section, dict) else []:
                    period_end = _month_end(_string(row, "fiscalEnd"))
                    average = _number(row, "consensusEPSForecast")
                    if not period_end or average is None:
                        continue
                    estimates.append(EstimatePoint(
                        ticker=ticker.upper(), as_of=_today(), metric="EPS",
                        period_end=period_end, period_type=period_type,
                        average=average, high=_number(row, "highEPSForecast"),
                        low=_number(row, "lowEPSForecast"),
                        analyst_count=_integer(row, "noOfEstimates"),
                        currency="Unknown", source=self.provider_name,
                        observed_at=observed, source_as_of=None,
                        provenance=(
                            "Nasdaq public earnings-forecast page; unofficial collection. "
                            "Provider source timestamp and currency are unavailable."
                        ),
                        official=False, period_precision="month",
                        revisions_up=_integer(row, "up"),
                        revisions_down=_integer(row, "down"),
                    ))
        package = ConsensusPackage(
            ticker=ticker.upper(), provider=self.provider_name,
            status="Partial - unofficial only" if estimates else "Unavailable",
            estimates=estimates, unofficial_only=True,
            data_gaps=(
                [
                    "Nasdaq source timestamp is unavailable; observed_at is the collection time.",
                    "Nasdaq EPS currency is unavailable and remains Unknown.",
                ]
                if estimates else ["Nasdaq returned no usable EPS forecasts."]
            ),
        )
        package.observations = _package_observations(package)
        for estimate in estimates:
            for field, value in (
                ("revisions_up", estimate.revisions_up),
                ("revisions_down", estimate.revisions_down),
            ):
                if value is not None:
                    package.observations.append(ProviderObservation(
                        ticker.upper(), self.provider_name,
                        f"estimate_EPS_{estimate.period_end}_{estimate.period_type}_{field}",
                        observed, None, value_numeric=float(value),
                        analyst_count=estimate.analyst_count,
                        provenance=estimate.provenance, official=False, confidence="Low",
                    ))
        package.provider_statuses = [_provider_status(package, official=False)]
        return package

    def _get(self, ticker: str) -> dict:
        request = Request(
            f"{self.base_url}/{ticker.upper()}/earnings-forecast",
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
                "User-Agent": "Mozilla/5.0 EquityResearchRadar/0.3",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            raise ProviderError(f"Nasdaq public endpoint returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Nasdaq public endpoint failed: {exc}") from exc


class TradingViewConsensusProvider(ConsensusAdapter):
    provider_name = "TradingView (unofficial)"
    official_for_conviction = False
    base_url = "https://scanner.tradingview.com/america/scan"

    def __init__(self, store: "ResearchStore | None" = None, timeout_seconds: int = 15) -> None:
        super().__init__(store)
        self.timeout_seconds = timeout_seconds

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        try:
            payload = self._post(ticker)
        except ProviderError as exc:
            return _unavailable_package(ticker, self.provider_name, str(exc), False)
        rows = payload.get("data") if isinstance(payload, dict) else None
        row = next((item for item in rows or [] if isinstance(item, dict) and item.get("d")), None)
        values = row.get("d") if row else []
        if len(values) < 7:
            return _unavailable_package(
                ticker, self.provider_name,
                "TradingView returned no usable target distribution.", False,
            )
        observed = _now()
        scanner_price = _list_number(values, 1)
        target = TargetConsensus(
            ticker=ticker.upper(), as_of=_today(), currency=str(values[6] or "Unknown"),
            target_median=_list_number(values, 4), target_high=_list_number(values, 2),
            target_low=_list_number(values, 3), analyst_count=None,
            current_price=current_price if current_price is not None else scanner_price,
            source=self.provider_name, observed_at=observed, source_as_of=None,
            provenance=(
                "TradingView public scanner; unofficial personal-research fallback. "
                "Provider source timestamp and analyst count are unavailable."
            ),
            official=False, target_label="Median Target", target_kind="median",
        )
        if all(value is None for value in (target.target_median, target.target_high, target.target_low)):
            return _unavailable_package(
                ticker, self.provider_name,
                "TradingView returned no usable target distribution.", False,
            )
        _enrich_target(target)
        package = ConsensusPackage(
            ticker=ticker.upper(), provider=self.provider_name, status="Partial - unofficial only",
            target=target, unofficial_only=True,
            data_gaps=[
                "TradingView source timestamp is unavailable; observed_at is the collection time.",
                "TradingView analyst count is unavailable and remains Unknown.",
            ],
        )
        package.observations = _package_observations(package)
        if scanner_price is not None:
            package.observations.append(ProviderObservation(
                ticker.upper(), self.provider_name, "scanner_close", observed, None,
                value_numeric=scanner_price, currency=target.currency,
                provenance=target.provenance, official=False, confidence="Low",
            ))
        package.provider_statuses = [_provider_status(package, official=False)]
        return package

    def _post(self, ticker: str) -> dict:
        symbols = [
            f"NASDAQ:{ticker.upper()}", f"NYSE:{ticker.upper()}",
            f"AMEX:{ticker.upper()}",
        ]
        body = json.dumps({
            "symbols": {"tickers": symbols, "query": {"types": []}},
            "columns": [
                "name", "close", "price_target_high", "price_target_low",
                "price_target_median", "Recommend.All", "currency",
            ],
        }).encode("utf-8")
        request = Request(
            self.base_url, data=body, method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 EquityResearchRadar/0.3",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8", errors="replace"))
        except HTTPError as exc:
            raise ProviderError(f"TradingView scanner returned HTTP {exc.code}.") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ProviderError(f"TradingView scanner failed: {exc}") from exc


class YahooConsensusProvider(ConsensusAdapter):
    provider_name = "Yahoo (unofficial)"
    official_for_conviction = False

    def __init__(self, store: "ResearchStore | None" = None, timeout_seconds: int = 12) -> None:
        super().__init__(store)
        self.timeout_seconds = timeout_seconds

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        modules = "financialData,recommendationTrend"
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker.upper()}?{urlencode({'modules': modules})}"
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 EquityResearchRadar/0.3"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            result = payload.get("quoteSummary", {}).get("result") or []
            row = result[0] if result else {}
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, IndexError) as exc:
            return _unavailable_package(ticker, self.provider_name, f"Unofficial Yahoo endpoint unavailable: {exc}", False)
        financial = row.get("financialData") or {}
        observed = _now()
        target = TargetConsensus(
            ticker.upper(), _today(), currency="USD",
            target_mean=_raw_number(financial.get("targetMeanPrice")),
            target_median=_raw_number(financial.get("targetMedianPrice")),
            target_high=_raw_number(financial.get("targetHighPrice")),
            target_low=_raw_number(financial.get("targetLowPrice")),
            analyst_count=_raw_integer(financial.get("numberOfAnalystOpinions")),
            current_price=current_price, source=self.provider_name, observed_at=observed,
            provenance="Unofficial Yahoo Finance web endpoint; personal research only",
            official=False,
        )
        if all(value is None for value in (target.target_mean, target.target_median, target.target_high, target.target_low)):
            target = None
        elif target:
            _enrich_target(target)
        trend_rows = (row.get("recommendationTrend") or {}).get("trend") or []
        latest = trend_rows[0] if trend_rows else None
        recommendation = None
        if latest:
            values = {
                "strong_buy": _integer(latest, "strongBuy") or 0,
                "buy": _integer(latest, "buy") or 0, "hold": _integer(latest, "hold") or 0,
                "sell": _integer(latest, "sell") or 0, "strong_sell": _integer(latest, "strongSell") or 0,
            }
            recommendation = RecommendationConsensus(
                ticker.upper(), _today(), source=self.provider_name, observed_at=observed,
                provenance="Unofficial Yahoo Finance web endpoint; personal research only",
                official=False, consensus_label=_dominant_recommendation(values), **values,
            )
        package = ConsensusPackage(
            ticker.upper(), self.provider_name,
            "Partial - unofficial only" if target or recommendation else "Unavailable",
            target=target, recommendations=recommendation, unofficial_only=True,
            data_gaps=[] if target or recommendation else ["Yahoo returned no usable analyst fields."],
        )
        package.observations = _package_observations(package)
        package.provider_statuses = [_provider_status(package, official=False)]
        return package


class CachedConsensusProvider(ConsensusAdapter):
    """Caches normalized successful packages once per UTC day; credentials never enter SQLite."""

    def __init__(self, provider: ConsensusAdapter, store: "ResearchStore") -> None:
        super().__init__(store)
        self.provider = provider
        self.provider_name = provider.provider_name
        self.official_for_conviction = provider.official_for_conviction
        self.provider.store = store

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        cached = self.store.load_provider_package(ticker, self.provider_name) if self.store else None
        if cached:
            if cached.target and current_price is not None:
                cached.target.current_price = current_price
                _enrich_target(cached.target)
            return cached
        package = self.provider.fetch_package(ticker, current_price)
        if self.store and package.status != "Unavailable":
            self.store.save_provider_package(package)
        return package

    def revision_since(self, ticker: str, event_date: str | None) -> float | None:
        return self.provider.revision_since(ticker, event_date)


class MultiSourceConsensusProvider(ConsensusAdapter):
    provider_name = "Multi-source consensus"

    def __init__(self, providers: list[ConsensusAdapter], store: "ResearchStore | None" = None) -> None:
        super().__init__(store)
        self.providers = providers
        self.official_for_conviction = any(provider.official_for_conviction for provider in providers)
        self.last_primary_target_source: str | None = None

    def fetch_packages(self, ticker: str, current_price: float | None = None) -> list[ConsensusPackage]:
        packages: list[ConsensusPackage] = []
        with ThreadPoolExecutor(max_workers=min(5, len(self.providers))) as executor:
            futures = {executor.submit(provider.fetch_package, ticker, current_price): provider for provider in self.providers}
            for future in as_completed(futures):
                provider = futures[future]
                try:
                    packages.append(future.result())
                except Exception as exc:
                    packages.append(_unavailable_package(ticker, provider.provider_name, str(exc), provider.official_for_conviction))
        order = {provider.provider_name: index for index, provider in enumerate(self.providers)}
        return sorted(packages, key=lambda package: order.get(package.provider, 999))

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        packages = self.fetch_packages(ticker, current_price)
        available = [package for package in packages if package.status != "Unavailable"]
        target = next((package.target for package in available if package.target and package.target.official), None)
        if not target:
            target = next((package.target for package in available if package.target), None)
        recommendation = next(
            (package.recommendations for package in available if package.provider == "Finnhub" and package.recommendations),
            None,
        )
        if not recommendation:
            recommendation = next((package.recommendations for package in available if package.recommendations and package.recommendations.official), None)
        if not recommendation:
            recommendation = next((package.recommendations for package in available if package.recommendations), None)
        estimate_package = next((package for package in available if package.estimates), None)
        surprise_package = next((package for package in available if package.surprises), None)
        official_available = any(_package_has_official_data(item) for item in packages)
        provider_targets = [item.target for item in packages if item.target]
        package = ConsensusPackage(
            ticker.upper(), self.provider_name,
            "Available" if official_available else "Partial - unofficial only" if available else "Unavailable",
            target=target, recommendations=recommendation,
            estimates=list(estimate_package.estimates) if estimate_package else [],
            surprises=list(surprise_package.surprises) if surprise_package else [],
            observations=[observation for item in packages for observation in item.observations],
            provider_statuses=[status for item in packages for status in item.provider_statuses],
            unofficial_only=bool(available) and not official_available,
            provider_targets=provider_targets,
        )
        package.comparisons = _provider_comparisons(packages)
        package.data_gaps = [
            f"{item.provider}: {gap}" for item in packages for gap in item.data_gaps
        ]
        self.last_primary_target_source = target.source if target else None
        self.official_for_conviction = bool(target and target.official)
        return package

    def revision_since(self, ticker: str, event_date: str | None) -> float | None:
        if not event_date or not self.store or not self.official_for_conviction:
            return None
        source = self.last_primary_target_source
        start_target = self.store.target_at_or_before(ticker, event_date, source) if source else None
        end_target = self.store.latest_target(ticker, source) if source else None
        return _pct_change(
            _target_primary_value(start_target),
            _target_primary_value(end_target),
        )


class CsvConsensusProvider(ConsensusAdapter):
    provider_name = "CSV"
    official_for_conviction = True

    def __init__(self, directory: Path | None = None, store: "ResearchStore | None" = None) -> None:
        super().__init__(store)
        self.directory = directory or config.CONSENSUS_CSV_DIR

    def fetch_targets(self, ticker: str) -> TargetConsensus | None:
        targets = self.fetch_target_history(ticker)
        if not targets:
            return None
        return sorted(targets, key=lambda item: item.as_of)[-1]

    def fetch_target_history(self, ticker: str) -> list[TargetConsensus]:
        rows = self._read("targets.csv", ticker) + self._read("target_revisions.csv", ticker)
        targets: list[TargetConsensus] = []
        for row in rows:
            target = self._target_from_row(ticker, row)
            if target:
                targets.append(target)
        return targets

    def _target_from_row(self, ticker: str, row: dict[str, str]) -> TargetConsensus | None:
        target = TargetConsensus(
            ticker=ticker.upper(), as_of=row.get("as_of") or _today(),
            currency=row.get("currency") or "USD",
            target_aggregate=_csv_float(row.get("target_aggregate")),
            target_mean=_csv_float(row.get("target_mean")),
            target_median=_csv_float(row.get("target_median")),
            target_high=_csv_float(row.get("target_high")),
            target_low=_csv_float(row.get("target_low")),
            analyst_count=_csv_int(row.get("analyst_count")),
            source=row.get("provider") or self.provider_name,
            observed_at=row.get("observed_at") or None,
            source_as_of=row.get("source_as_of") or row.get("provider_timestamp") or None,
            provenance=row.get("provenance") or "CSV/manual consensus import",
            official=_csv_bool(row.get("official"), True),
            target_kind=row.get("target_kind") or ("aggregate" if row.get("target_aggregate") else "mean"),
            target_label=row.get("target_label") or ("Aggregate Target" if row.get("target_aggregate") else "Mean target"),
        )
        target.provider_timestamp = row.get("provider_timestamp") or target.as_of
        if all(
            value is None
            for value in (target.target_aggregate, target.target_mean, target.target_median, target.target_high, target.target_low)
        ):
            return None
        _enrich_target(target)
        return target

    def fetch_estimates(self, ticker: str) -> list[EstimatePoint]:
        return [
            EstimatePoint(
                ticker=ticker.upper(), as_of=row.get("as_of") or _today(),
                metric=row.get("metric") or "unknown", period_end=row.get("period_end") or "",
                period_type=row.get("period_type") or "annual",
                average=_csv_float(row.get("average")), high=_csv_float(row.get("high")),
                low=_csv_float(row.get("low")), analyst_count=_csv_int(row.get("analyst_count")),
                currency=row.get("currency") or "USD",
                source=row.get("provider") or self.provider_name,
                observed_at=row.get("observed_at") or None,
                source_as_of=row.get("source_as_of") or None,
                provenance=row.get("provenance") or "CSV/manual consensus import",
                official=_csv_bool(row.get("official"), True),
                period_precision=row.get("period_precision") or "day",
                revisions_up=_csv_int(row.get("revisions_up")),
                revisions_down=_csv_int(row.get("revisions_down")),
            )
            for row in self._read("estimates.csv", ticker) + self._read("estimate_revisions.csv", ticker)
            if row.get("period_end")
        ]

    def fetch_recommendations(self, ticker: str) -> RecommendationConsensus | None:
        rows = self.fetch_recommendation_history(ticker)
        if not rows:
            return None
        return sorted(rows, key=lambda item: item.as_of)[-1]

    def fetch_recommendation_history(self, ticker: str) -> list[RecommendationConsensus]:
        rows = self._read("recommendations.csv", ticker)
        recommendations: list[RecommendationConsensus] = []
        for row in rows:
            recommendations.append(self._recommendation_from_row(ticker, row))
        return recommendations

    def _recommendation_from_row(self, ticker: str, row: dict[str, str]) -> RecommendationConsensus:
        return RecommendationConsensus(
            ticker=ticker.upper(), as_of=row.get("as_of") or _today(),
            strong_buy=_csv_int(row.get("strong_buy")) or 0,
            buy=_csv_int(row.get("buy")) or 0, hold=_csv_int(row.get("hold")) or 0,
            sell=_csv_int(row.get("sell")) or 0,
            strong_sell=_csv_int(row.get("strong_sell")) or 0,
            consensus_label=row.get("consensus_label"),
            source=row.get("provider") or self.provider_name,
            observed_at=row.get("observed_at") or None,
            source_as_of=row.get("source_as_of") or None,
            provenance=row.get("provenance") or "CSV/manual consensus import",
            official=_csv_bool(row.get("official"), True),
        )

    def fetch_surprises(self, ticker: str) -> list[EarningsSurprise]:
        return [
            EarningsSurprise(
                ticker.upper(), row.get("period_end") or "",
                _csv_float(row.get("actual_eps")), _csv_float(row.get("estimated_eps")),
                _csv_float(row.get("surprise_pct")), row.get("provider") or self.provider_name,
                observed_at=row.get("observed_at") or None,
                source_as_of=row.get("source_as_of") or None,
                provenance=row.get("provenance") or "CSV/manual consensus import",
                official=_csv_bool(row.get("official"), True),
            )
            for row in self._read("surprises.csv", ticker)
            if row.get("period_end")
        ]

    def fetch_package(self, ticker: str, current_price: float | None = None) -> ConsensusPackage:
        target = self.fetch_targets(ticker)
        if target:
            target.current_price = current_price
            _enrich_target(target)
        estimates = self.fetch_estimates(ticker)
        recommendations = self.fetch_recommendations(ticker)
        surprises = self.fetch_surprises(ticker)
        has_data = bool(target or estimates or recommendations or surprises)
        package = ConsensusPackage(
            ticker=ticker.upper(), provider=self.provider_name,
            status="Available" if has_data else "Unavailable", target=target,
            estimates=estimates, recommendations=recommendations, surprises=surprises,
            data_gaps=[] if has_data else [f"No CSV consensus rows found in {self.directory}."],
        )
        package.observations = _package_observations(package)
        package.provider_statuses = [_provider_status(package, official=True)]
        return package

    def _read(self, filename: str, ticker: str) -> list[dict[str, str]]:
        path = self.directory / filename
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [row for row in csv.DictReader(handle) if row.get("ticker", "").upper() == ticker.upper()]


def build_consensus_provider(
    store: "ResearchStore | None" = None,
    alpha_vantage_key: str | None = None,
    finnhub_key: str | None = None,
    fmp_key: str | None = None,
    enable_nasdaq: bool | None = None,
    enable_tradingview: bool | None = None,
    enable_yahoo: bool | None = None,
) -> ConsensusAdapter:
    alpha_vantage_key = config.ALPHAVANTAGE_API_KEY if alpha_vantage_key is None else alpha_vantage_key
    finnhub_key = config.FINNHUB_API_KEY if finnhub_key is None else finnhub_key
    fmp_key = config.FMP_API_KEY if fmp_key is None else fmp_key
    enable_nasdaq = config.ENABLE_NASDAQ_CONSENSUS if enable_nasdaq is None else enable_nasdaq
    enable_tradingview = (
        config.ENABLE_TRADINGVIEW_CONSENSUS
        if enable_tradingview is None else enable_tradingview
    )
    enable_yahoo = config.ENABLE_YAHOO_CONSENSUS if enable_yahoo is None else enable_yahoo
    providers: list[ConsensusAdapter] = []
    if alpha_vantage_key:
        providers.append(AlphaVantageConsensusProvider(api_key=alpha_vantage_key, store=store))
    if finnhub_key:
        providers.append(FinnhubConsensusProvider(api_key=finnhub_key, store=store))
    if fmp_key:
        providers.append(FmpConsensusProvider(api_key=fmp_key, store=store))
    csv_dir = config.CONSENSUS_CSV_DIR
    if csv_dir.exists() and any(csv_dir.glob("*.csv")):
        providers.append(CsvConsensusProvider(store=store))
    if enable_nasdaq:
        providers.append(NasdaqConsensusProvider(store=store))
    if enable_tradingview:
        providers.append(TradingViewConsensusProvider(store=store))
    if enable_yahoo:
        providers.append(YahooConsensusProvider(store=store))
    if not providers:
        return ConsensusAdapter(store=store)
    if store:
        providers = [CachedConsensusProvider(provider, store) for provider in providers]
    if len(providers) == 1:
        return providers[0]
    return MultiSourceConsensusProvider(providers, store=store)


def consensus_provider_from_env(store: "ResearchStore | None" = None) -> ConsensusAdapter:
    return build_consensus_provider(store=store)


class TranscriptAdapter:
    """Placeholder boundary for Quartr, FactSet, AlphaSense, FMP, or Alpha Vantage."""

    provider_name = "Not connected"

    def recent_transcripts(self, ticker: str) -> list[dict]:
        return []


def _parse_date(value: str) -> date:
    normalized = value[:10]
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return datetime.strptime(normalized, "%Y-%m-%d").date()


def _tiingo_symbol(ticker: str) -> str:
    symbol = ticker.upper().strip()
    if "." in symbol:
        head, tail = symbol.rsplit(".", 1)
        if len(tail) <= 2 and tail.isalpha():
            return f"{head}-{tail}"
    return symbol


def _eodhd_symbol(ticker: str) -> str:
    symbol = ticker.upper().strip()
    known_suffixes = {
        "US", "HK", "LSE", "PA", "SW", "TO", "V", "TSE", "AU", "AS",
        "BR", "MI", "MC", "F", "BE", "HE", "ST", "CO", "OL", "IC",
        "WAR", "PR", "BUD", "AT", "TA", "JSE", "KQ", "KO", "TW", "TWO",
        "SHG", "SHE", "NSE", "BSE", "SA", "MX", "FOREX", "CC", "INDX",
    }
    if "." in symbol:
        head, suffix = symbol.rsplit(".", 1)
        if suffix in known_suffixes:
            return symbol
        if len(suffix) == 1 and suffix.isalpha():
            return f"{head}-{suffix}.US"
    return f"{symbol}.US"


def _trailing_session_return(rows: list[dict], sessions: int) -> float | None:
    if len(rows) <= sessions or rows[-sessions - 1]["close"] in (None, 0):
        return None
    return (rows[-1]["close"] / rows[-sessions - 1]["close"] - 1.0) * 100


def _aligned_trailing_return(
    stock_rows: list[dict],
    benchmark_by_date: dict[date, dict],
    sessions: int,
) -> float | None:
    if len(stock_rows) <= sessions:
        return None
    start_date = stock_rows[-sessions - 1]["date"]
    end_date = stock_rows[-1]["date"]
    start = benchmark_by_date.get(start_date)
    end = benchmark_by_date.get(end_date)
    if not start or not end or start["close"] in (None, 0):
        return None
    return (end["close"] / start["close"] - 1.0) * 100


def _rows(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "estimates", "historical"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [payload]
    return []


def _first_row(payload: object) -> dict | None:
    rows = _rows(payload)
    return rows[0] if rows else None


def _number(row: dict, *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        try:
            if value not in (None, ""):
                return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _integer(row: dict, *keys: str) -> int | None:
    value = _number(row, *keys)
    return int(value) if value is not None else None


def _string(row: dict, *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _fmp_estimate_points(ticker: str, row: dict, period_end: str, period_type: str) -> list[EstimatePoint]:
    definitions = (
        ("EPS", ("epsAvg", "estimatedEpsAvg", "estimatedEPSAvg"), ("epsHigh", "estimatedEpsHigh"), ("epsLow", "estimatedEpsLow"), ("numAnalystsEps", "numberAnalystEstimatedEps", "numberAnalystsEstimatedEps")),
        ("Revenue", ("revenueAvg", "estimatedRevenueAvg"), ("revenueHigh", "estimatedRevenueHigh"), ("revenueLow", "estimatedRevenueLow"), ("numAnalystsRevenue", "numberAnalystEstimatedRevenue", "numberAnalystsEstimatedRevenue")),
        ("EBITDA", ("ebitdaAvg", "estimatedEbitdaAvg"), ("ebitdaHigh", "estimatedEbitdaHigh"), ("ebitdaLow", "estimatedEbitdaLow"), ("numAnalystsEbitda", "numberAnalystEstimatedEbitda")),
        ("EBIT", ("ebitAvg", "estimatedEbitAvg"), ("ebitHigh", "estimatedEbitHigh"), ("ebitLow", "estimatedEbitLow"), ("numAnalystsEbit", "numberAnalystEstimatedEbit")),
        ("Net Income", ("netIncomeAvg", "estimatedNetIncomeAvg"), ("netIncomeHigh", "estimatedNetIncomeHigh"), ("netIncomeLow", "estimatedNetIncomeLow"), ("numAnalystsNetIncome", "numberAnalystEstimatedNetIncome")),
        ("SG&A Expense", ("sgaExpenseAvg", "estimatedSgaExpenseAvg"), ("sgaExpenseHigh", "estimatedSgaExpenseHigh"), ("sgaExpenseLow", "estimatedSgaExpenseLow"), ("numAnalystsSgaExpense", "numberAnalystEstimatedSgaExpense")),
    )
    points: list[EstimatePoint] = []
    for metric, average_keys, high_keys, low_keys, analyst_keys in definitions:
        average = _number(row, *average_keys)
        if average is None:
            continue
        points.append(
            EstimatePoint(
                ticker=ticker.upper(), as_of=_today(), metric=metric, period_end=period_end,
                period_type=period_type, average=average, high=_number(row, *high_keys),
                low=_number(row, *low_keys), analyst_count=_integer(row, *analyst_keys),
                currency=str(row.get("currency") or "USD"), source="FMP",
            )
        )
    return points


def _enrich_target(target: TargetConsensus) -> None:
    primary = _target_primary_value(target)
    if target.current_price and primary:
        target.implied_upside_pct = (primary / target.current_price - 1) * 100
    if primary and target.target_high is not None and target.target_low is not None:
        target.dispersion_pct = (target.target_high - target.target_low) / abs(primary) * 100
    timestamp = target.source_as_of or target.provider_timestamp
    if not timestamp:
        target.freshness_days = None
        return
    try:
        observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
        target.freshness_days = max(0, (date.today() - observed).days)
    except (TypeError, ValueError):
        target.freshness_days = None


def _safe_provider_message(body: str) -> str:
    try:
        payload = json.loads(body)
        message = None
        if isinstance(payload, dict):
            message = payload.get("Error Message") or payload.get("error") or payload.get("message")
        elif isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    message = item.get("Error Message") or item.get("error") or item.get("message")
                    if message:
                        break
                elif isinstance(item, str) and item.strip():
                    message = item
                    break
        if message:
            return _redact_provider_message(str(message))[:300]
    except (json.JSONDecodeError, TypeError):
        pass
    return _redact_provider_message(body)[:300]


def _provider_failure_class(message: str) -> str:
    lowered = str(message or "").lower()
    if "http 404" in lowered and ("[]" in lowered or "no data" in lowered or "not found" in lowered):
        return "no_data"
    if any(token in lowered for token in ("http 401", "invalid api", "invalid key", "api key is invalid")):
        return "invalid_key"
    if any(token in lowered for token in (
        "http 402", "http 403", "subscription", "not available under your current",
        "upgrade your plan", "entitlement", "payment required",
    )):
        return "entitlement_error"
    if any(token in lowered for token in ("http 429", "rate limit", "too many requests", "limit reach")):
        return "rate_limited"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if any(token in lowered for token in ("urlopen error", "connection", "network", "dns", "ssl")):
        return "network_error"
    if any(token in lowered for token in ("malformed", "json", "list' object", "schema")):
        return "malformed_response"
    return "unavailable"


def _fmp_endpoint_fallback(label: str, failure_class: str) -> str:
    options = {
        "price targets": "Fallback: Alpha Vantage aggregate target, TradingView distribution (unofficial), or target CSV import.",
        "recommendations": "Fallback: Finnhub recommendation trends, Alpha Vantage rating counts, or recommendation CSV import.",
        "analyst estimates": "Fallback: Nasdaq estimates (unofficial) or point-in-time estimates CSV import.",
        "earnings surprises": "Fallback: SEC/issuer reported actuals plus a stored pre-event estimate, or surprises CSV import.",
    }
    prefix = (
        "The configured FMP plan does not entitle this endpoint. "
        if failure_class == "entitlement_error"
        else ""
    )
    return prefix + options.get(label, "Fallback: use a registered provider or point-in-time CSV import.")


def _redact_provider_message(message: str) -> str:
    redacted = str(message or "")
    for secret in (
        config.FMP_API_KEY,
        config.ALPHAVANTAGE_API_KEY,
        config.FINNHUB_API_KEY,
        config.FRED_API_KEY,
        config.BEA_API_KEY,
        config.CENSUS_API_KEY,
        config.WISBURG_API_KEY,
        config.TIINGO_API_KEY,
        config.EODHD_API_KEY,
    ):
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1[redacted]", redacted)
    redacted = re.sub(r"(?i)(token=)[^&\s]+", r"\1[redacted]", redacted)
    redacted = re.sub(r"(?i)(api key (?:as|is)\s+)[A-Za-z0-9._-]+", r"\1[redacted]", redacted)
    return redacted


def _csv_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _csv_int(value: str | None) -> int | None:
    parsed = _csv_float(value)
    return int(parsed) if parsed is not None else None


def _csv_bool(value: str | None, default: bool) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "official"}


def _pct_change(start: float | None, end: float | None) -> float | None:
    if start in (None, 0) or end is None:
        return None
    return (end / start - 1) * 100


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_near_date(rows: list[dict], event_date: date, max_lookback_days: int) -> dict | None:
    for lookback in range(max_lookback_days + 1):
        candidate = event_date - timedelta(days=lookback)
        row = next((item for item in rows if item["date"] == candidate), None)
        if row:
            return row
    return None


def _returns_before(rows: list[dict], end_index: int, count: int) -> list[float]:
    return [
        (rows[index]["close"] / rows[index - 1]["close"] - 1.0) * 100
        for index in range(max(1, end_index - count), end_index)
        if rows[index - 1]["close"]
    ]


def _pre_event_beta(
    stock_rows: list[dict],
    benchmark_rows: list[dict],
    event_index: int,
) -> float | None:
    benchmark_by_date = {row["date"]: row for row in benchmark_rows}
    benchmark_previous = {
        benchmark_rows[index]["date"]: benchmark_rows[index - 1]["close"]
        for index in range(1, len(benchmark_rows))
    }
    pairs: list[tuple[float, float]] = []
    for index in range(max(1, event_index - 60), event_index):
        row = stock_rows[index]
        benchmark = benchmark_by_date.get(row["date"])
        previous_benchmark = benchmark_previous.get(row["date"])
        if not benchmark or not previous_benchmark or not stock_rows[index - 1]["close"]:
            continue
        stock_return = stock_rows[index]["close"] / stock_rows[index - 1]["close"] - 1.0
        benchmark_return = benchmark["close"] / previous_benchmark - 1.0
        pairs.append((stock_return, benchmark_return))
    if len(pairs) < 20:
        return None
    stock_mean = mean(pair[0] for pair in pairs)
    benchmark_mean = mean(pair[1] for pair in pairs)
    covariance = mean(
        (stock - stock_mean) * (benchmark - benchmark_mean)
        for stock, benchmark in pairs
    )
    variance = mean((benchmark - benchmark_mean) ** 2 for _, benchmark in pairs)
    return covariance / variance if variance else None


def _event_trading_target(event_date: str, event_timestamp: str | None) -> date:
    event_day = _parse_date(event_date)
    if not event_timestamp:
        return event_day + timedelta(days=1)
    digits = "".join(character for character in event_timestamp if character.isdigit())
    hour = None
    if len(digits) >= 10:
        try:
            hour = int(digits[8:10])
        except ValueError:
            hour = None
    return event_day + timedelta(days=1) if hour is None or hour >= 16 else event_day


def _rows_cover_event(rows: list[dict], event_day: date) -> bool:
    if len(rows) < 2:
        return False
    return rows[0]["date"] < event_day <= rows[-1]["date"] + timedelta(days=7)


def _looks_like_html_challenge(text: str) -> bool:
    head = text[:500].lower()
    return "<html" in head or "<!doctype html" in head or "requires javascript" in head


def _csv_daily_rows(
    text: str,
    date_field: str,
    close_fields: tuple[str, ...],
    volume_field: str,
) -> list[dict] | None:
    reader = csv.DictReader(StringIO(text))
    if not reader.fieldnames:
        return None
    field_lookup = {field.lower(): field for field in reader.fieldnames if field}
    date_key = field_lookup.get(date_field.lower())
    close_key = next((field_lookup.get(field.lower()) for field in close_fields if field_lookup.get(field.lower())), None)
    volume_key = field_lookup.get(volume_field.lower())
    if not date_key or not close_key:
        return None
    rows: list[dict] = []
    for row in reader:
        try:
            rows.append({
                "date": _parse_date(row[date_key]),
                "close": float(row[close_key]),
                "volume": float(row.get(volume_key or "") or 0),
            })
        except (KeyError, TypeError, ValueError):
            continue
    rows.sort(key=lambda row: row["date"])
    return rows


def _fixed_window_returns(
    rows: list[dict],
    anchor_index: int,
    prior_close: float,
) -> dict[str, float | None]:
    values: dict[str, float | None] = {}
    for label, sessions in (("1d", 1), ("5d", 5), ("20d", 20)):
        end_index = anchor_index + sessions - 1
        values[label] = (
            (rows[end_index]["close"] / prior_close - 1.0) * 100
            if prior_close and end_index < len(rows) else None
        )
    return values


def _benchmark_window_returns(rows: list[dict], anchor_date: date) -> dict[str, float | None]:
    anchor_index = next(
        (index for index, row in enumerate(rows) if row["date"] >= anchor_date),
        None,
    )
    if anchor_index is None or anchor_index < 1:
        return {"1d": None, "5d": None, "20d": None}
    return _fixed_window_returns(rows, anchor_index, rows[anchor_index - 1]["close"])


def _subtract_windows(
    primary: dict[str, float | None],
    benchmark: dict[str, float | None],
) -> dict[str, float | None]:
    return {
        key: value - benchmark[key]
        if value is not None and benchmark.get(key) is not None else None
        for key, value in primary.items()
    }


def _strict_pre_event_beta(
    stock_rows: list[dict],
    benchmark_rows: list[dict],
    event_index: int,
) -> float | None:
    estimation_end = event_index - 21
    if estimation_end < 2:
        return None
    benchmark_by_date = {row["date"]: row for row in benchmark_rows}
    benchmark_previous = {
        benchmark_rows[index]["date"]: benchmark_rows[index - 1]["close"]
        for index in range(1, len(benchmark_rows))
    }
    pairs: list[tuple[float, float]] = []
    for index in range(max(1, estimation_end - 252), estimation_end):
        row = stock_rows[index]
        benchmark = benchmark_by_date.get(row["date"])
        previous_benchmark = benchmark_previous.get(row["date"])
        if not benchmark or not previous_benchmark or not stock_rows[index - 1]["close"]:
            continue
        pairs.append((
            stock_rows[index]["close"] / stock_rows[index - 1]["close"] - 1.0,
            benchmark["close"] / previous_benchmark - 1.0,
        ))
    if len(pairs) < 126:
        return None
    stock_mean = mean(item[0] for item in pairs)
    market_mean = mean(item[1] for item in pairs)
    covariance = mean((stock - stock_mean) * (market - market_mean) for stock, market in pairs)
    variance = mean((market - market_mean) ** 2 for _, market in pairs)
    return covariance / variance if variance else None


def _path_from_prior_close(
    rows: list[dict], anchor_index: int, prior_close: float, sessions: int,
) -> list[float]:
    return [
        (rows[index]["close"] / prior_close - 1.0) * 100
        for index in range(anchor_index, min(len(rows), anchor_index + sessions))
        if prior_close
    ]


def _window_return(rows: list[dict], start_index: int, trading_days: int) -> float | None:
    end_index = start_index + trading_days
    if end_index >= len(rows) or not rows[start_index]["close"]:
        return None
    return (rows[end_index]["close"] / rows[start_index]["close"] - 1.0) * 100


def _window_path(rows: list[dict], start_index: int, trading_days: int) -> list[float]:
    if start_index + trading_days >= len(rows) or not rows[start_index]["close"]:
        return []
    start = rows[start_index]["close"]
    return [
        (rows[index]["close"] / start - 1.0) * 100
        for index in range(start_index + 1, start_index + trading_days + 1)
    ]


def _dominant_recommendation(values: dict[str, int]) -> str:
    labels = {
        "strong_buy": "Strong Buy", "buy": "Buy", "hold": "Hold",
        "sell": "Sell", "strong_sell": "Strong Sell",
    }
    return labels[max(values, key=values.get)]


def _gdelt_timeline_values(payload: object) -> list[float]:
    if not isinstance(payload, dict):
        return []
    timeline = payload.get("timeline") or []
    values: list[float] = []
    for series in timeline if isinstance(timeline, list) else []:
        for point in series.get("data", []) if isinstance(series, dict) else []:
            try:
                values.append(float(point.get("value")))
            except (AttributeError, TypeError, ValueError):
                continue
    return values


def _raw_number(value: object) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _raw_integer(value: object) -> int | None:
    parsed = _raw_number(value)
    return int(parsed) if parsed is not None else None


def _unavailable_package(
    ticker: str,
    provider: str,
    message: str,
    official: bool,
) -> ConsensusPackage:
    message = _redact_provider_message(message)
    hint = network_message_hint(message)
    if hint:
        failure_class, hint_message = hint
        entitlement = "network_error"
        message = f"{message} Network diagnosis: {failure_class}. {hint_message}"
    else:
        entitlement = "rate_limited" if "limit" in message.lower() else "unavailable"
    package = ConsensusPackage(
        ticker.upper(), provider, "Unavailable", data_gaps=[message], unofficial_only=not official,
    )
    package.provider_statuses = [ProviderStatus(
        provider, "Unavailable", official, entitlement, _now(), message,
    )]
    return package


def _provider_status(package: ConsensusPackage, official: bool) -> ProviderStatus:
    entitlement = "available" if package.status != "Unavailable" else "unavailable"
    return ProviderStatus(
        package.provider, package.status, official, entitlement, _now(),
        _redact_provider_message("; ".join(package.data_gaps)),
    )


def _package_observations(package: ConsensusPackage) -> list[ProviderObservation]:
    observations: list[ProviderObservation] = []
    if package.target:
        target = package.target
        observed = target.observed_at or _now()
        fields = {
            "target_aggregate": target.target_aggregate,
            "target_mean": target.target_mean, "target_median": target.target_median,
            "target_high": target.target_high, "target_low": target.target_low,
            "analyst_count": float(target.analyst_count) if target.analyst_count is not None else None,
        }
        for field, value in fields.items():
            if value is None:
                continue
            observations.append(ProviderObservation(
                target.ticker, target.source or package.provider, field, observed,
                target.source_as_of or target.provider_timestamp,
                value_numeric=value, currency=target.currency,
                analyst_count=target.analyst_count,
                entitlement_status=target.entitlement_status,
                provenance=target.provenance or target.source or package.provider,
                official=target.official,
                confidence="High" if target.official else "Low",
            ))
    if package.recommendations:
        rec = package.recommendations
        observed = rec.observed_at or _now()
        for field in ("strong_buy", "buy", "hold", "sell", "strong_sell"):
            observations.append(ProviderObservation(
                rec.ticker, rec.source or package.provider, f"recommendation_{field}", observed,
                rec.source_as_of, value_numeric=float(getattr(rec, field)),
                analyst_count=sum((rec.strong_buy, rec.buy, rec.hold, rec.sell, rec.strong_sell)),
                entitlement_status=rec.entitlement_status,
                provenance=rec.provenance or rec.source or package.provider,
                official=rec.official, confidence="High" if rec.official else "Low",
            ))
    for estimate in package.estimates:
        if estimate.average is None:
            continue
        observations.append(ProviderObservation(
            estimate.ticker, estimate.source or package.provider,
            f"estimate_{estimate.metric}_{estimate.period_end}_{estimate.period_type}",
            estimate.observed_at or _now(), estimate.source_as_of,
            value_numeric=estimate.average, currency=estimate.currency,
            analyst_count=estimate.analyst_count,
            entitlement_status=estimate.entitlement_status,
            provenance=estimate.provenance or estimate.source or package.provider,
            official=estimate.official, confidence="High" if estimate.official else "Low",
        ))
    return observations


def _recommendation_trend_observations(
    ticker: str,
    provider: str,
    payload: object,
    observed_at: str,
    provenance: str,
) -> list[ProviderObservation]:
    rows = payload if isinstance(payload, list) else []
    observations: list[ProviderObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_as_of = _string(row, "period")
        values = {
            "strong_buy": _integer(row, "strongBuy") or 0,
            "buy": _integer(row, "buy") or 0,
            "hold": _integer(row, "hold") or 0,
            "sell": _integer(row, "sell") or 0,
            "strong_sell": _integer(row, "strongSell") or 0,
        }
        analyst_count = sum(values.values()) or None
        for field, value in values.items():
            observations.append(ProviderObservation(
                ticker.upper(), provider, f"recommendation_trend_{field}",
                observed_at, source_as_of, value_numeric=float(value),
                analyst_count=analyst_count,
                provenance=provenance, official=True, confidence="Medium",
            ))
        observations.append(ProviderObservation(
            ticker.upper(), provider, "recommendation_trend_consensus",
            observed_at, source_as_of, value_text=_dominant_recommendation(values),
            analyst_count=analyst_count, provenance=provenance,
            official=True, confidence="Medium",
        ))
    return observations


def _provider_comparisons(packages: list[ConsensusPackage]) -> list[ProviderComparison]:
    fields: dict[str, dict[str, float | str | None]] = {}
    for package in packages:
        for observation in package.observations:
            is_estimate = (
                observation.field.startswith("estimate_")
                and not observation.field.endswith(("_revisions_up", "_revisions_down"))
            )
            is_recommendation = (
                observation.field.startswith("recommendation_")
                and not observation.field.startswith("recommendation_trend_")
            )
            if observation.field not in {
                "target_aggregate", "target_mean", "target_median",
                "target_high", "target_low", "analyst_count",
            } and not is_estimate and not is_recommendation:
                continue
            fields.setdefault(observation.field, {})[observation.provider] = (
                observation.value_numeric if observation.value_numeric is not None else observation.value_text
            )
    comparisons: list[ProviderComparison] = []
    for field, values in fields.items():
        numeric = [float(value) for value in values.values() if isinstance(value, (int, float))]
        spread = None
        if len(numeric) >= 2 and mean(numeric) != 0:
            spread = (max(numeric) - min(numeric)) / abs(mean(numeric)) * 100
        interpretation = (
            "Material provider disagreement; inspect definitions and timestamps."
            if spread is not None and spread >= 10
            else "Providers are broadly aligned." if spread is not None
            else "Only one provider currently supplies this field."
        )
        comparisons.append(ProviderComparison(field, values, spread, interpretation))
    return comparisons


def _target_primary_value(target: TargetConsensus | None) -> float | None:
    if not target:
        return None
    if target.target_kind == "aggregate":
        return target.target_aggregate
    if target.target_kind == "median":
        return target.target_median
    return target.target_mean


def _package_has_official_data(package: ConsensusPackage) -> bool:
    if package.status == "Unavailable":
        return False
    if package.target and package.target.official:
        return True
    if package.recommendations and package.recommendations.official:
        return True
    if any(item.official for item in package.estimates):
        return True
    if any(item.official for item in package.surprises):
        return True
    return any(item.official for item in package.observations)


def _month_end(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value.strip(), "%b %Y")
    except ValueError:
        return None
    return date(parsed.year, parsed.month, monthrange(parsed.year, parsed.month)[1]).isoformat()


def _list_number(values: list, index: int) -> float | None:
    if index >= len(values):
        return None
    try:
        return float(values[index]) if values[index] not in (None, "") else None
    except (TypeError, ValueError):
        return None
