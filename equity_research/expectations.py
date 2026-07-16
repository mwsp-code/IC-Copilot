from __future__ import annotations

from datetime import date, timedelta

from .models import (
    ConsensusPackage,
    ChangeEvent,
    ExpectationComparison,
    ExpectationEventAudit,
    ExpectationsBridge,
    FinancialMetric,
    GuidancePoint,
)
from .providers import PriceReaction
from .research_store import ResearchStore
from .research_profiles import event_identifier


def attach_revision_history(
    package: ConsensusPackage,
    store: ResearchStore,
    windows: tuple[int, ...] = (7, 30, 90),
) -> None:
    target_provider = package.target.source if package.target else package.provider
    package.revisions = store.revisions(package.ticker, windows, target_provider)
    today = date.today()
    future_estimates = sorted(
        [item for item in package.estimates if _safe_date(item.period_end) >= today],
        key=lambda item: (item.period_end, item.metric),
    )
    for estimate in future_estimates[:8]:
        for days in (30, 90):
            package.revisions.append(
                store.estimate_revision(
                    package.ticker,
                    estimate.metric,
                    estimate.period_end,
                    (today - timedelta(days=days)).isoformat(),
                    today.isoformat(),
                    estimate.source,
                )
            )


def build_expectations_bridge(
    ticker: str,
    package: ConsensusPackage,
    metrics: list[FinancialMetric],
    store: ResearchStore,
    events: list[ChangeEvent] | None = None,
    price_reaction: PriceReaction | None = None,
) -> ExpectationsBridge:
    event_audits = _build_event_audits(ticker, package, metrics, store, events or [])
    if package.status == "Unavailable":
        return ExpectationsBridge(
            status="Unavailable",
            headline="Consensus expectations are not connected.",
            point_in_time_note="No point-in-time expectations comparison was attempted.",
            price_reaction_pct=price_reaction.reaction_pct if price_reaction else None,
            price_source=price_reaction.source if price_reaction else None,
            data_gaps=list(package.data_gaps),
            event_audits=event_audits,
        )

    comparisons: list[ExpectationComparison] = []
    latest_surprise = package.surprises[0] if package.surprises else None
    if latest_surprise:
        actual_metric = next((
            item for item in metrics
            if item.name == "EPS" and item.period_end[:10] == latest_surprise.period_end[:10]
        ), None)
        comparisons.append(
            ExpectationComparison(
                metric="EPS",
                period_end=latest_surprise.period_end,
                expected=latest_surprise.estimated_eps,
                actual=latest_surprise.actual_eps,
                surprise_pct=latest_surprise.surprise_pct,
                post_event_revision_pct=_matching_revision(
                    package,
                    "EPS",
                    latest_surprise.period_end,
                ),
                interpretation=_surprise_interpretation(latest_surprise.surprise_pct),
                drivers=_surprise_drivers(metrics, latest_surprise.period_end),
                actual_source=(
                    actual_metric.source_url or actual_metric.source_kind
                    if actual_metric else f"{latest_surprise.source} reported earnings history"
                ),
                estimate_source=latest_surprise.source,
                estimate_as_of=latest_surprise.source_as_of or latest_surprise.observed_at,
                estimate_eligibility="Provider historical surprise record",
            )
        )

    by_metric = {metric.name: metric for metric in metrics}
    for metric_name in ("Revenue", "Net Income"):
        actual = by_metric.get(metric_name)
        if not actual or not actual.filed:
            continue
        estimate_provider = next(
            (item.source for item in package.estimates if item.metric == metric_name),
            None,
        )
        estimate = store.estimate_at_or_before(
            ticker,
            metric_name,
            actual.period_end,
            _day_before(actual.filed),
            estimate_provider,
        )
        if not estimate or estimate.average in (None, 0):
            continue
        surprise = (actual.value / estimate.average - 1) * 100
        comparisons.append(
            ExpectationComparison(
                metric=metric_name,
                period_end=actual.period_end,
                expected=estimate.average,
                actual=actual.value,
                surprise_pct=surprise,
                post_event_revision_pct=_matching_revision(package, metric_name, estimate.period_end),
                interpretation=_surprise_interpretation(surprise),
                drivers=_surprise_drivers(metrics, actual.period_end),
                actual_source=actual.source_url or actual.source_kind,
                estimate_source=estimate.source,
                estimate_as_of=estimate.as_of,
                estimate_eligibility="Point-in-time estimate observed before the reporting event",
            )
        )

    data_gaps = list(package.data_gaps)
    guidance = _citation_backed_guidance(events or [])
    if not guidance:
        data_gaps.append("No citation-backed numeric guidance was found; guidance was not inferred from prose.")
    if not comparisons:
        data_gaps.append(
            "No reported period could be matched to a point-in-time estimate; no surprise was inferred."
        )
    if not any(revision.change_pct is not None for revision in package.revisions):
        data_gaps.append("Revision history is still accumulating in the local snapshot store.")
    for revision in package.revisions:
        if revision.status != "available" and revision.reason:
            data_gaps.append(f"Revision history [{revision.metric} {revision.window_days}d]: {revision.reason}")
    if any(
        estimate.revisions_up is not None or estimate.revisions_down is not None
        for estimate in package.estimates
    ):
        data_gaps.append(
            "Some providers expose revision counts without historical estimate magnitudes; "
            "magnitude is shown only when point-in-time snapshots exist."
        )

    material = [item for item in comparisons if item.surprise_pct is not None and abs(item.surprise_pct) >= 3]
    if material:
        strongest = max(material, key=lambda item: abs(item.surprise_pct or 0))
        headline = (
            f"{strongest.metric} {'beat' if (strongest.surprise_pct or 0) > 0 else 'missed'} "
            f"expectations by {abs(strongest.surprise_pct or 0):.1f}%."
        )
    elif comparisons:
        headline = "Reported results were broadly close to available expectations."
    else:
        headline = _event_specific_headline(event_audits)

    timeline = _expectations_timeline(comparisons, guidance, price_reaction)
    return ExpectationsBridge(
        status="Available" if comparisons else "Partial",
        headline=headline,
        comparisons=comparisons,
        target_revisions=[item for item in package.revisions if item.metric.startswith("price_target_")],
        numeric_guidance=guidance,
        price_reaction_pct=price_reaction.reaction_pct if price_reaction else None,
        price_source=price_reaction.source if price_reaction else None,
        point_in_time_note=(
            "Only snapshots recorded on or before an event are eligible for historical comparisons; "
            "current consensus is never substituted for a missing historical snapshot."
        ),
        data_gaps=data_gaps,
        timeline=timeline,
        event_audits=event_audits,
    )


def _build_event_audits(
    ticker: str,
    package: ConsensusPackage,
    metrics: list[FinancialMetric],
    store: ResearchStore,
    events: list[ChangeEvent],
) -> list[ExpectationEventAudit]:
    metrics_by_name = {item.name: item for item in metrics}
    audits: list[ExpectationEventAudit] = []
    material_events = sorted(
        [item for item in events if item.severity >= 3],
        key=lambda item: (item.event_date or "", item.severity),
        reverse=True,
    )[:12]
    for event in material_events:
        citation = event.citations[0] if event.citations else None
        event_date = event.event_date or (citation.filed if citation else None)
        reporting_period = (
            str(event.metrics.get("guidance_period") or event.metrics.get("period_end") or "")
            or (citation.period_end if citation else None)
        )
        requested_metric = str(event.metrics.get("metric_name") or "")
        actual_candidates = [requested_metric] if requested_metric else []
        if event.category in {"earnings", "financial_kpi", "margin"}:
            actual_candidates.extend(["Revenue", "Net Income", "EPS"])
        actual_candidates = list(dict.fromkeys(item for item in actual_candidates if item))
        matched_metrics = [
            item for item in actual_candidates
            if item == "EPS" or item in metrics_by_name
        ]
        pre_count = 0
        post_count = 0
        providers: list[str] = []
        if event_date and reporting_period:
            for metric_name in matched_metrics:
                provider = next(
                    (item.source for item in package.estimates if item.metric == metric_name),
                    None,
                )
                if provider:
                    providers.append(provider)
                if store.estimate_at_or_before(
                    ticker, metric_name, reporting_period, _day_before(event_date), provider,
                ):
                    pre_count += 1
                post_rows = [
                    item for item in package.estimates
                    if item.metric == metric_name
                    and item.period_end[:10] == reporting_period[:10]
                    and _safe_date(item.as_of) >= _safe_date(event_date)
                ]
                post_count += len(post_rows)
        surprise_match = next((
            item for item in package.surprises
            if reporting_period and item.period_end[:10] == reporting_period[:10]
        ), None)
        if surprise_match:
            matched_metrics.append("EPS actual/surprise")
            providers.append(surprise_match.source)
        reason_code, status, reason = _event_audit_reason(
            event, event_date, reporting_period, matched_metrics, pre_count, post_count, package.status,
        )
        audits.append(ExpectationEventAudit(
            event_id=event_identifier(event),
            event_label=(
                f"{event.source} {citation.accession if citation and citation.accession else ''} "
                f"filed {event_date or 'date unknown'}, reporting period {reporting_period or 'unknown'}"
            ).replace("  ", " ").strip(),
            form=citation.form if citation else event.source,
            accession=citation.accession if citation else None,
            filing_date=event_date,
            reporting_period=reporting_period,
            actual_metrics_checked=list(dict.fromkeys(matched_metrics)),
            eligible_pre_event_snapshots=pre_count,
            eligible_post_event_snapshots=post_count,
            status=status,
            reason_code=reason_code,
            reason=reason,
            providers=list(dict.fromkeys(item for item in providers if item)),
            point_in_time_note=(
                "Only snapshots observed before the event qualify as pre-event expectations; "
                "today's consensus cannot backfill this event."
            ),
        ))
    return audits


def _event_audit_reason(
    event: ChangeEvent,
    event_date: str | None,
    reporting_period: str | None,
    metrics: list[str],
    pre_count: int,
    post_count: int,
    package_status: str,
) -> tuple[str, str, str]:
    label = f"{event.source} event dated {event_date or 'unknown'} for period {reporting_period or 'unknown'}"
    if package_status == "Unavailable":
        return "provider_unavailable", "Unavailable", f"{label}: expectations provider data is unavailable."
    if not reporting_period:
        return "period_missing", "Unaligned", f"{label}: the source event has no reporting period for fiscal alignment."
    if not metrics:
        return "actual_missing", "Unaligned", f"{label}: no comparable reported actual metric was identified."
    if pre_count == 0:
        return (
            "pre_event_snapshot_missing",
            "Missing pre-event snapshot",
            f"{label}: checked {', '.join(metrics)}, but found no eligible estimate snapshot observed before the event.",
        )
    if post_count == 0:
        return (
            "post_event_snapshot_missing",
            "Missing post-event snapshot",
            f"{label}: pre-event expectations exist, but no aligned post-event estimate snapshot is available yet.",
        )
    return (
        "aligned",
        "Aligned",
        f"{label}: found {pre_count} pre-event and {post_count} post-event aligned snapshot(s).",
    )


def _event_specific_headline(audits: list[ExpectationEventAudit]) -> str:
    if not audits:
        return "No material source event was available for an expectations audit."
    audit = audits[0]
    return f"Expectations audit for {audit.event_label}: {audit.reason}"


def _matching_estimate(
    package: ConsensusPackage,
    metric: str,
    period_end: str | None,
):
    if not period_end:
        return None
    matches = [
        item for item in package.estimates
        if item.metric == metric and item.period_end[:10] == period_end[:10]
    ]
    return matches[0] if matches else None


def _matching_revision(package: ConsensusPackage, metric: str, period_end: str) -> float | None:
    matches = [
        item for item in package.revisions
        if item.metric == metric and item.change_pct is not None
    ]
    return matches[0].change_pct if matches else None


def _surprise_interpretation(value: float | None) -> str:
    if value is None:
        return "Surprise unavailable."
    if value >= 3:
        return "Positive surprise; check whether forward estimates and price reacted proportionately."
    if value <= -3:
        return "Negative surprise; check whether the miss is temporary or thesis-breaking."
    return "Result was within a 3% band of consensus."


def _safe_date(value: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return date.min


def _day_before(value: str) -> str:
    parsed = _safe_date(value)
    return (parsed - timedelta(days=1)).isoformat() if parsed != date.min else value


def _citation_backed_guidance(events: list[ChangeEvent]) -> list[GuidancePoint]:
    points: list[GuidancePoint] = []
    for event in events:
        if event.category != "guidance" or not event.citations:
            continue
        low = _numeric(event.metrics.get("guidance_low"))
        high = _numeric(event.metrics.get("guidance_high"))
        value = _numeric(event.metrics.get("guidance_value"))
        if low is None and high is None and value is None:
            continue
        points.append(GuidancePoint(
            metric=str(event.metrics.get("guidance_metric") or "Guidance"),
            period_end=event.metrics.get("guidance_period"),
            low=low if low is not None else value,
            high=high if high is not None else value,
            currency=str(event.metrics.get("guidance_currency")) if event.metrics.get("guidance_currency") else None,
            citation=event.citations[0],
        ))
    return points


def _numeric(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _surprise_drivers(
    metrics: list[FinancialMetric],
    period_end: str,
) -> dict[str, float | None]:
    period = {metric.name: metric for metric in metrics if metric.period_end[:10] == period_end[:10]}
    drivers: dict[str, float | None] = {}
    for name in (
        "Revenue", "Operating Expenses", "Operating Income", "Net Income",
        "Income Tax Expense", "Shares",
    ):
        metric = period.get(name)
        if metric:
            drivers[f"{name} comparable change pct"] = metric.yoy_change_pct
    revenue = period.get("Revenue")
    gross_profit = period.get("Gross Profit")
    if (
        revenue and gross_profit and revenue.value
        and revenue.previous_value and gross_profit.previous_value
    ):
        current_margin = gross_profit.value / revenue.value * 100
        prior_margin = gross_profit.previous_value / revenue.previous_value * 100
        drivers["Gross margin change pts"] = current_margin - prior_margin
    return drivers


def _expectations_timeline(
    comparisons: list[ExpectationComparison],
    guidance: list[GuidancePoint],
    price_reaction: PriceReaction | None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for comparison in comparisons:
        rows.extend([
            {
                "date": comparison.period_end,
                "stage": "Pre-event expectation",
                "metric": comparison.metric,
                "value": comparison.expected,
            },
            {
                "date": comparison.period_end,
                "stage": "Reported actual",
                "metric": comparison.metric,
                "value": comparison.actual,
            },
            {
                "date": comparison.period_end,
                "stage": "Post-event revision",
                "metric": comparison.metric,
                "value": comparison.post_event_revision_pct,
            },
        ])
    for point in guidance:
        rows.append({
            "date": point.period_end,
            "stage": "Management guidance",
            "metric": point.metric,
            "value": point.low if point.low == point.high else [point.low, point.high],
        })
    if price_reaction:
        rows.append({
            "date": price_reaction.event_date,
            "stage": "Price reaction",
            "metric": "Abnormal return pct",
            "value": (
                price_reaction.abnormal_reaction_pct
                if price_reaction.abnormal_reaction_pct is not None
                else price_reaction.reaction_pct
            ),
        })
    return rows
