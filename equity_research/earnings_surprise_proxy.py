from __future__ import annotations

from .models import (
    EarningsSurpriseProxy,
    EarningsSurpriseProxyItem,
    ExpectationsBridge,
    FinancialMetric,
)


def build_earnings_surprise_proxy(
    ticker: str,
    expectations: ExpectationsBridge,
    metrics: list[FinancialMetric],
) -> EarningsSurpriseProxy:
    """Build an auditable surprise measure without implying revision follow-through."""
    metric_map = _metric_map(metrics)
    items: list[EarningsSurpriseProxyItem] = []
    for comparison in expectations.comparisons:
        audit = next((
            row for row in expectations.event_audits
            if row.reporting_period and comparison.period_end
            and row.reporting_period[:10] == comparison.period_end[:10]
        ), None)
        actual_metric = metric_map.get((comparison.metric, (comparison.period_end or "")[:10]))
        unit = actual_metric.unit if actual_metric else ("per share" if comparison.metric == "EPS" else "Unknown")
        eligibility = comparison.estimate_eligibility or "Unknown"
        primary_actual = bool(actual_metric and (
            "sec.gov" in (actual_metric.source_url or "").lower()
            or actual_metric.source_kind in {"companyfacts", "periodic_inline_xbrl", "registration_inline_xbrl"}
        ))
        point_in_time = "point-in-time" in eligibility.lower() or "historical surprise" in eligibility.lower()
        confidence = "High" if primary_actual and point_in_time else "Medium" if point_in_time else "Low"
        limitations: list[str] = []
        if not primary_actual:
            limitations.append("The reported actual is not independently tied to a normalized primary filing fact in this comparison.")
        if comparison.post_event_revision_pct is None:
            limitations.append("Subsequent analyst revision follow-through is unavailable; this measures surprise only.")
        if not comparison.estimate_as_of:
            limitations.append("The estimate source does not expose a precise collection timestamp in the normalized record.")
        items.append(EarningsSurpriseProxyItem(
            metric=comparison.metric,
            reporting_period=comparison.period_end,
            event_label=audit.event_label if audit else f"Reported results for {comparison.period_end or 'unknown period'}",
            event_date=audit.filing_date if audit else None,
            actual=comparison.actual,
            estimate=comparison.expected,
            surprise_pct=comparison.surprise_pct,
            unit=unit,
            actual_source=comparison.actual_source or "Unknown",
            estimate_source=comparison.estimate_source or "Unknown",
            estimate_as_of=comparison.estimate_as_of,
            eligibility=eligibility,
            confidence=confidence,
            interpretation=comparison.interpretation,
            drivers=dict(comparison.drivers),
            limitations=limitations,
        ))

    usable = [item for item in items if item.surprise_pct is not None]
    follow_through = any(
        comparison.post_event_revision_pct is not None
        for comparison in expectations.comparisons
    )
    if usable:
        strongest = max(usable, key=lambda item: abs(item.surprise_pct or 0.0))
        direction = "above" if (strongest.surprise_pct or 0.0) > 0 else "below"
        headline = (
            f"{strongest.metric} for {strongest.reporting_period or 'the reported period'} was "
            f"{abs(strongest.surprise_pct or 0.0):.1f}% {direction} the eligible estimate."
        )
        status = "Available" if all(item.confidence in {"High", "Medium"} for item in usable) else "Partial"
    else:
        headline = "No reported actual could be matched to a contemporaneous estimate."
        status = "Unavailable"

    gaps = [] if usable else [
        "A reported actual and an estimate observed before the event are both required.",
        "Use provider surprise history, point-in-time estimate snapshots, or a licensed/manual estimate import.",
    ]
    if usable and not follow_through:
        gaps.append(
            "Estimate revision follow-through is not available. Do not describe the surprise as a change in subsequent analyst expectations."
        )
    return EarningsSurpriseProxy(
        ticker=ticker.upper(),
        status=status,
        headline=headline,
        methodology=(
            "Compare a reported actual with an estimate whose normalized record is eligible for the event. "
            "This is an earnings-surprise proxy, not evidence of post-event analyst revisions."
        ),
        items=items,
        data_gaps=gaps,
        revision_follow_through_available=follow_through,
    )


def _metric_map(metrics: list[FinancialMetric]) -> dict[tuple[str, str], FinancialMetric]:
    result: dict[tuple[str, str], FinancialMetric] = {}
    for metric in sorted(metrics, key=lambda row: (row.period_end or "", row.filed or ""), reverse=True):
        result.setdefault((metric.name, (metric.period_end or "")[:10]), metric)
    return result
