from __future__ import annotations

from datetime import date, datetime

from .models import AlertRecord, ChangeEvent, ConsensusPackage, FilingRecord, TradeIdea
from .research_store import ResearchStore


def generate_consensus_alerts(
    package: ConsensusPackage,
    store: ResearchStore,
    events: list[ChangeEvent] | None = None,
    filings: list[FilingRecord] | None = None,
    ideas: list[TradeIdea] | None = None,
) -> list[AlertRecord]:
    queued: list[dict] = []
    ticker = package.ticker.upper()

    for revision in package.revisions:
        if revision.change_pct is None:
            continue
        threshold = 5 if revision.metric.startswith("price_target_") else 3
        if abs(revision.change_pct) < threshold:
            continue
        direction = "raised" if revision.change_pct > 0 else "cut"
        period = revision.end_date or "current"
        _queue_alert(
            queued,
            ticker=ticker,
            alert_type="target_revision" if revision.metric.startswith("price_target_") else "estimate_revision",
            title=f"{revision.metric.replace('_', ' ').title()} {direction} {abs(revision.change_pct):.1f}%",
            message=(
                f"{revision.metric} moved from {revision.start_value} to {revision.end_value} "
                f"over {revision.window_days} days."
            ),
            severity=4 if abs(revision.change_pct) >= 10 else 3,
            dedupe_key=f"{ticker}:{revision.metric}:{period}:{revision.window_days}",
            fiscal_period=period,
        )

    if package.target:
        target = package.target
        previous_target = store.previous_target(ticker, target.as_of, target.source or package.provider)
        if previous_target:
            median_change = _pct_change(previous_target.target_median, target.target_median)
            if median_change is not None and abs(median_change) >= 5:
                direction = "raised" if median_change > 0 else "cut"
                _queue_alert(
                    queued, ticker, "target_median_revision",
                    f"Median price target {direction} {abs(median_change):.1f}%",
                    f"Median target moved from {previous_target.target_median} to {target.target_median}.",
                    4 if abs(median_change) >= 10 else 3,
                    f"{ticker}:target_median_revision:current:daily", "current",
                )
            dispersion_change = _point_change(previous_target.dispersion_pct, target.dispersion_pct)
            if dispersion_change is not None and abs(dispersion_change) >= 10:
                _queue_alert(
                    queued, ticker, "target_dispersion_change", "Analyst target dispersion changed materially",
                    f"Dispersion moved {dispersion_change:+.1f} percentage points.", 2,
                    f"{ticker}:target_dispersion_change:current:daily", "current",
                )
            count_drop = _pct_change(previous_target.analyst_count, target.analyst_count)
            if count_drop is not None and count_drop <= -20:
                _queue_alert(
                    queued, ticker, "analyst_count_drop", "Analyst coverage count dropped",
                    f"Target-contributing analysts fell from {previous_target.analyst_count} to {target.analyst_count}.",
                    3, f"{ticker}:analyst_count_drop:current:daily", "current",
                )
        if target.dispersion_pct is not None and target.dispersion_pct >= 40:
            _queue_alert(
                queued, ticker, "target_dispersion", "Analyst target dispersion is high",
                f"The high-low target range equals {target.dispersion_pct:.1f}% of the mean target.",
                2, f"{ticker}:target_dispersion:current:level", "current",
            )
        timestamp = target.source_as_of or target.provider_timestamp
        age = _age_days(timestamp)
        if age is not None and age > 45:
            _queue_alert(
                queued, ticker, "stale_consensus", "Consensus data is stale",
                f"The provider timestamp is {age} days old.", 2,
                f"{ticker}:stale_consensus:current:45d", "current",
            )

    if package.recommendations:
        current_rec = package.recommendations
        previous_rec = store.recommendation_before(
            ticker, current_rec.as_of, current_rec.source or package.provider,
        )
        current_label = _recommendation_label(current_rec)
        previous_label = _recommendation_label(previous_rec) if previous_rec else None
        if previous_label and current_label != previous_label:
            _queue_alert(
                queued, ticker, "recommendation_change", "Recommendation consensus changed",
                f"Recommendation category moved from {previous_label} to {current_label}.",
                3, f"{ticker}:recommendation_change:current:daily", "current",
            )

    for surprise in package.surprises[:1]:
        if surprise.surprise_pct is None or abs(surprise.surprise_pct) < 3:
            continue
        _queue_alert(
            queued, ticker, "earnings_surprise", "Material earnings surprise",
            f"EPS surprise was {surprise.surprise_pct:+.1f}% for {surprise.period_end}.",
            4 if abs(surprise.surprise_pct) >= 10 else 3,
            f"{ticker}:earnings_surprise:{surprise.period_end}", surprise.period_end,
        )

    for event in (events or []):
        if event.severity < 4:
            continue
        _queue_alert(
            queued, ticker, "research_event", event.title, event.summary, event.severity,
            f"{ticker}:research_event:{event.category}:{event.event_date or 'unknown'}",
            event.event_date,
        )
    for filing in (filings or [])[:5]:
        _queue_alert(
            queued, ticker, "new_filing", f"New {filing.form} filing",
            f"{filing.description or filing.form} was filed on {filing.filing_date}.",
            3 if filing.form in {"10-K", "10-Q", "20-F", "40-F"} else 2,
            f"{ticker}:new_filing:{filing.accession}", filing.report_date or filing.filing_date,
        )
    for idea in (ideas or []):
        if not idea.source_events or idea.source_events[0].severity < 4:
            continue
        event = idea.source_events[0]
        _queue_alert(
            queued, ticker, "thesis_confirmation", f"Evidence confirms: {idea.title}",
            f"{event.summary} Review the idea's exact confirm and break criteria.",
            event.severity, f"{ticker}:thesis_confirmation:{idea.idea_id}", event.event_date,
        )
    return store.create_alerts(queued)


def _queue_alert(
    alerts: list[dict],
    ticker: str,
    alert_type: str,
    title: str,
    message: str,
    severity: int,
    dedupe_key: str,
    fiscal_period: str | None = None,
    **kwargs,
) -> None:
    alerts.append({
        "ticker": kwargs.get("ticker", ticker),
        "alert_type": kwargs.get("alert_type", alert_type),
        "title": kwargs.get("title", title),
        "message": kwargs.get("message", message),
        "severity": kwargs.get("severity", severity),
        "dedupe_key": kwargs.get("dedupe_key", dedupe_key),
        "fiscal_period": kwargs.get("fiscal_period", fiscal_period),
    })


def _age_days(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        try:
            parsed = date.fromisoformat(value[:10])
        except (TypeError, ValueError):
            return None
    return (date.today() - parsed).days


def _pct_change(start: float | int | None, end: float | int | None) -> float | None:
    if start in (None, 0) or end is None:
        return None
    return (float(end) / float(start) - 1) * 100


def _point_change(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return end - start


def _recommendation_label(recommendation) -> str:
    if recommendation.consensus_label:
        return recommendation.consensus_label
    buckets = {
        "Strong Buy": recommendation.strong_buy,
        "Buy": recommendation.buy,
        "Hold": recommendation.hold,
        "Sell": recommendation.sell,
        "Strong Sell": recommendation.strong_sell,
    }
    return max(buckets, key=buckets.get)
