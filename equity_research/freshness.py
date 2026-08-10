from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from .models import ResearchFreshnessSummary, SourceFreshnessStatus
from .source_context import (
    latest_validated_record,
    record_source_label,
    validated_source_records,
)


def build_research_freshness(
    *,
    run_manifest: Any,
    filings: Iterable[Any],
    recent_market_context: Any | None,
    consensus: Any | None,
    news_intelligence: Any | None,
    external_evidence: Any | None,
    llm_run_manifest: Any | None,
    dual_model_research: Any | None,
    hybrid_research: Any | None = None,
    ticker: str = "",
    demo_case: Any | None = None,
    completed_at: str = "",
    now: datetime | None = None,
) -> ResearchFreshnessSummary:
    """Build source-specific freshness without confusing retrieval with as-of dates."""
    now = _as_utc(now or datetime.now(timezone.utc))
    generated_at = str(getattr(run_manifest, "generated_at", "") or "")
    completed_at = str(
        completed_at
        or getattr(dual_model_research, "completed_at", "")
        or generated_at
    )
    result_type = "frozen_demo" if demo_case is not None else "live"

    filing_rows = list(filings or [])
    latest_filing = max(
        filing_rows,
        key=lambda item: _date_sort_key(
            getattr(item, "filing_date", None)
            or getattr(item, "accepted_at", None)
        ),
        default=None,
    )
    filing_as_of = str(
        getattr(latest_filing, "filing_date", "") or ""
    )
    filing_period = str(
        getattr(latest_filing, "report_date", "") or ""
    )
    filing_label = (
        f"{getattr(latest_filing, 'form', 'filing')} filed {filing_as_of}; "
        f"reporting period {filing_period or 'unknown'}"
        if latest_filing else
        "No filing date was available."
    )

    price_as_of = str(
        getattr(recent_market_context, "price_as_of", "") or ""
    )
    price_source = str(
        getattr(recent_market_context, "source", "") or ""
    )
    consensus_dates = _consensus_dates(consensus)
    consensus_as_of = _latest_date_value(consensus_dates)
    consensus_source = str(getattr(consensus, "provider", "") or "")
    news_dates = [
        getattr(item, "published_at", None)
        or getattr(item, "observed_at", None)
        for item in getattr(news_intelligence, "articles", []) or []
    ]
    news_as_of = _latest_date_value(news_dates)
    macro_dates = [
        getattr(item, "source_as_of", None)
        or getattr(item, "release_date", None)
        or getattr(item, "observed_at", None)
        for item in getattr(external_evidence, "evidence", []) or []
    ]
    macro_as_of = _latest_date_value(macro_dates)
    llm_as_of = str(
        getattr(dual_model_research, "completed_at", "") or
        getattr(llm_run_manifest, "generated_at", "") or ""
    )

    if demo_case is not None:
        if hybrid_research is not None and getattr(
            hybrid_research, "normalized_sources", None
        ):
            return _published_demo_freshness(
                hybrid_research=hybrid_research,
                ticker=ticker or str(getattr(hybrid_research, "ticker", "") or ""),
                fallback_generated_at=generated_at,
                fallback_filing_as_of=filing_as_of,
                fallback_filing_message=filing_label,
                fallback_price_as_of=price_as_of,
                fallback_price_source=price_source,
                fallback_consensus_as_of=consensus_as_of,
                fallback_consensus_source=consensus_source,
                fallback_news_as_of=news_as_of,
                fallback_macro_as_of=macro_as_of,
                demo_case=demo_case,
            )
        frozen_at = str(
            getattr(demo_case, "refreshed_at", "") or generated_at
        )
        components = [
            SourceFreshnessStatus(
                "Research run", "Frozen demo", frozen_at, generated_at,
                "Sanitized fixture",
                "Frozen illustrative evidence; no live refresh occurred while loading.",
            ),
            _frozen_component("Filings", filing_as_of, filing_label),
            _frozen_component(
                "Market price", price_as_of,
                f"{price_source or 'Fixture'} price context."
            ),
            _frozen_component(
                "Consensus", consensus_as_of,
                f"{consensus_source or 'Fixture'} consensus context."
            ),
            _frozen_component("News / external research", news_as_of),
            _frozen_component("Macro", macro_as_of),
            _frozen_component("LLM research", llm_as_of),
        ]
        return ResearchFreshnessSummary(
            overall_status="Frozen demo",
            summary=(
                f"Frozen demo refreshed {frozen_at or 'on an unknown date'}; "
                "it is reproducible research, not current live market data."
            ),
            run_generated_at=generated_at,
            run_completed_at=completed_at,
            result_type=result_type,
            components=components,
            unavailable_components=[
                item.component for item in components
                if item.status == "Unavailable"
            ],
        )

    run_status = _age_status(completed_at, now, 1, 7)
    filing_status = _age_status(filing_as_of, now, 150, 220)
    price_status = _market_age_status(price_as_of, now)
    consensus_status = (
        "Unavailable"
        if not consensus_as_of
        or str(getattr(consensus, "status", "")).casefold() == "unavailable"
        else _age_status(consensus_as_of, now, 7, 35)
    )
    news_status = _age_status(news_as_of, now, 3, 14)
    macro_status = _age_status(macro_as_of, now, 45, 120)
    llm_status = _age_status(llm_as_of, now, 30, 90)

    components = [
        SourceFreshnessStatus(
            "Research run", run_status, completed_at, generated_at,
            "IC Copilot", "Time when the current research result completed.",
        ),
        SourceFreshnessStatus(
            "Filings", filing_status, filing_as_of, generated_at,
            "SEC / issuer", filing_label,
        ),
        SourceFreshnessStatus(
            "Market price", price_status, price_as_of, generated_at,
            price_source,
            "Freshness is measured in US market sessions and accounts for "
            "weekends and major exchange holidays.",
        ),
        SourceFreshnessStatus(
            "Consensus", consensus_status, consensus_as_of, generated_at,
            consensus_source,
            "Consensus freshness describes the source snapshot, not historical "
            "point-in-time eligibility.",
        ),
        SourceFreshnessStatus(
            "News / external research", news_status, news_as_of, generated_at,
            "Registered news sources",
            "Latest permitted article publication or observation date.",
        ),
        SourceFreshnessStatus(
            "Macro", macro_status, macro_as_of, generated_at,
            "Registered macro sources",
            "Macro series use a slower source-specific freshness window.",
        ),
        SourceFreshnessStatus(
            "LLM research", llm_status, llm_as_of, generated_at,
            str(getattr(llm_run_manifest, "provider", "") or ""),
            _llm_freshness_message(dual_model_research, llm_run_manifest),
        ),
    ]
    stale = [
        item.component for item in components if item.status == "Stale"
    ]
    unavailable = [
        item.component for item in components if item.status == "Unavailable"
    ]
    critical = {
        item.component: item.status for item in components
        if item.component in {"Research run", "Filings", "Market price"}
    }
    if any(value == "Stale" for value in critical.values()):
        overall = "Stale"
    elif any(
        item.status in {"Partial", "Stale", "Unavailable"}
        for item in components
    ):
        overall = "Partial"
    else:
        overall = "Current"
    summary_parts = [
        f"research completed {completed_at or 'at an unknown time'}",
        f"price through {price_as_of or 'unavailable'}",
        (
            f"filings through {filing_period or filing_as_of}"
            if filing_as_of else "filing cutoff unavailable"
        ),
        (
            f"consensus through {consensus_as_of}"
            if consensus_as_of else "consensus unavailable"
        ),
    ]
    if stale:
        summary_parts.append(f"stale: {', '.join(stale)}")
    return ResearchFreshnessSummary(
        overall_status=overall,
        summary="; ".join(summary_parts) + ".",
        run_generated_at=generated_at,
        run_completed_at=completed_at,
        result_type=result_type,
        components=components,
        stale_components=stale,
        unavailable_components=unavailable,
    )


def build_research_freshness_from_result(
    result: Any,
    *,
    now: datetime | None = None,
) -> ResearchFreshnessSummary:
    return build_research_freshness(
        run_manifest=result.run_manifest,
        filings=result.filings,
        recent_market_context=getattr(result, "recent_market_context", None),
        consensus=getattr(result, "consensus", None),
        news_intelligence=getattr(result, "news_intelligence", None),
        external_evidence=getattr(result, "external_evidence", None),
        llm_run_manifest=getattr(result, "llm_run_manifest", None),
        dual_model_research=getattr(result, "dual_model_research", None),
        hybrid_research=getattr(result, "hybrid_research", None),
        ticker=str(getattr(getattr(result, "identity", None), "ticker", "") or ""),
        demo_case=getattr(result, "demo_case", None),
        now=now,
    )


def _frozen_component(
    component: str,
    source_as_of: str,
    message: str = "",
) -> SourceFreshnessStatus:
    return SourceFreshnessStatus(
        component=component,
        status="Frozen demo" if source_as_of else "Unavailable",
        source_as_of=source_as_of,
        message=message or "Frozen fixture component.",
    )


def _published_demo_freshness(
    *,
    hybrid_research: Any,
    ticker: str,
    fallback_generated_at: str,
    fallback_filing_as_of: str,
    fallback_filing_message: str,
    fallback_price_as_of: str,
    fallback_price_source: str,
    fallback_consensus_as_of: str,
    fallback_consensus_source: str,
    fallback_news_as_of: str,
    fallback_macro_as_of: str,
    demo_case: Any,
) -> ResearchFreshnessSummary:
    """Describe the sources actually used by a published frozen artifact.

    The base sample still supplies a few illustrative presentation inputs.  We
    label those separately instead of making them look admitted to the QA run.
    """

    generated_at = str(
        getattr(hybrid_research, "generated_at", "") or fallback_generated_at
    )
    completed_at = str(
        getattr(hybrid_research, "completed_at", "") or generated_at
    )
    published_at = str(
        getattr(hybrid_research, "published_at", "")
        or getattr(hybrid_research, "publishable_at", "")
        or completed_at
    )
    records = validated_source_records(hybrid_research, ticker=ticker)
    illustrative_at = str(
        getattr(demo_case, "refreshed_at", "") or fallback_generated_at
    )

    filing = latest_validated_record(
        records,
        lambda item: _record_has_date(item)
        and str(getattr(item, "source_type", "") or "").casefold()
        in {"sec_filing", "historical_filing", "hkex_document"},
    )
    price = latest_validated_record(
        records,
        lambda item: _record_has_date(item)
        and str(getattr(item, "source_type", "") or "").casefold()
        == "quantitative_historical_price"
        and str(getattr(item, "metric_or_claim", "") or "").casefold()
        in {"adjusted close", "adjusted_close"},
    )
    consensus = latest_validated_record(records, _is_consensus_record)
    external = latest_validated_record(records, _is_external_record)
    macro = latest_validated_record(records, _is_macro_record)
    execution_backend = getattr(hybrid_research, "execution_backend", None)
    llm_source = str(
        getattr(execution_backend, "label", "")
        or getattr(hybrid_research, "profile", "")
        or "Recorded model workflow"
    ).replace("_", " ")

    filing_message = (
        f"{getattr(filing, 'form', '') or 'Filing'} filed "
        f"{getattr(filing, 'publication_date', '')}; reporting period "
        f"{getattr(filing, 'reporting_period', '') or 'not recorded'}."
        if filing is not None
        else fallback_filing_message
    )
    components = [
        SourceFreshnessStatus(
            "Research run",
            "Frozen demo",
            completed_at,
            published_at,
            "Published research artifact",
            "Research completion and publication times from the immutable demo manifest.",
        ),
        _artifact_or_illustrative_component(
            "Filings",
            filing,
            generated_at=generated_at,
            fallback_observed_at=illustrative_at,
            fallback_as_of=fallback_filing_as_of,
            fallback_source="Illustrative filing fixture",
            admitted_message=filing_message,
        ),
        _artifact_or_illustrative_component(
            "Market price",
            price,
            generated_at=generated_at,
            fallback_observed_at=illustrative_at,
            fallback_as_of=fallback_price_as_of,
            fallback_source=fallback_price_source or "Illustrative price fixture",
            admitted_message=(
                "Validated adjusted close used as the research-cutoff price; "
                "this is not a live quote."
            ),
        ),
        _artifact_or_illustrative_component(
            "Consensus",
            consensus,
            generated_at=generated_at,
            fallback_observed_at=illustrative_at,
            fallback_as_of=fallback_consensus_as_of,
            fallback_source=fallback_consensus_source or "Illustrative consensus fixture",
            admitted_message="Latest admitted point-in-time consensus observation.",
        ),
        _artifact_or_illustrative_component(
            "News / external research",
            external,
            generated_at=generated_at,
            fallback_observed_at=illustrative_at,
            fallback_as_of=fallback_news_as_of,
            fallback_source="Illustrative external-research fixture",
            admitted_message="Latest admitted issuer, transcript, news, or external-research record.",
        ),
        _artifact_or_illustrative_component(
            "Macro",
            macro,
            generated_at=generated_at,
            fallback_observed_at=illustrative_at,
            fallback_as_of=fallback_macro_as_of,
            fallback_source="Illustrative macro fixture",
            admitted_message="Latest admitted macro observation used by the research artifact.",
        ),
        SourceFreshnessStatus(
            "LLM research",
            "Frozen demo",
            completed_at,
            published_at,
            llm_source,
            "Model research and Independent QA timestamps come from the published run manifest.",
        ),
    ]
    price_cutoff = str(getattr(price, "publication_date", "") or "")
    filing_cutoff = str(getattr(filing, "publication_date", "") or "")
    unavailable = [
        item.component for item in components if item.status == "Unavailable"
    ]
    return ResearchFreshnessSummary(
        overall_status="Frozen demo",
        summary=(
            f"Published frozen research completed {completed_at or 'at an unknown time'}; "
            f"admitted price through {price_cutoff or 'unavailable'}; filings through "
            f"{filing_cutoff or 'unavailable'}. No live refresh occurred while loading."
        ),
        run_generated_at=generated_at,
        run_completed_at=completed_at,
        result_type="frozen_demo",
        components=components,
        unavailable_components=unavailable,
    )


def _artifact_or_illustrative_component(
    component: str,
    record: Any | None,
    *,
    generated_at: str,
    fallback_observed_at: str,
    fallback_as_of: str,
    fallback_source: str,
    admitted_message: str,
) -> SourceFreshnessStatus:
    if record is not None:
        observed_at = str(getattr(record, "observed_at", "") or generated_at)
        message = admitted_message
        if not getattr(record, "observed_at", ""):
            message += " Source-level retrieval time was not retained; artifact generation time is shown."
        return SourceFreshnessStatus(
            component=component,
            status="Frozen demo",
            source_as_of=str(getattr(record, "publication_date", "") or ""),
            observed_at=observed_at,
            source=record_source_label(record, fallback_source),
            message=message,
        )
    if fallback_as_of:
        return SourceFreshnessStatus(
            component=component,
            status="Illustrative only",
            source_as_of=fallback_as_of,
            observed_at=fallback_observed_at or fallback_as_of,
            source=fallback_source,
            message=(
                "Displayed as an illustrative screening input; it was not admitted "
                "to the published Independent QA evidence graph."
            ),
        )
    return SourceFreshnessStatus(
        component=component,
        status="Unavailable",
        source="Not present in published artifact",
        message="No dated admitted or illustrative source is available for this component.",
    )


def _record_has_date(record: Any) -> bool:
    return bool(str(getattr(record, "publication_date", "") or "").strip())


def _record_text(record: Any) -> str:
    return " ".join(
        str(getattr(record, field, "") or "").casefold()
        for field in ("source_family", "source_type", "metric_or_claim", "section")
    )


def _is_consensus_record(record: Any) -> bool:
    text = _record_text(record)
    return _record_has_date(record) and any(
        token in text for token in ("consensus", "estimate", "recommendation", "price target")
    )


def _is_macro_record(record: Any) -> bool:
    text = _record_text(record)
    return _record_has_date(record) and any(
        token in text
        for token in (
            "macro", "fred", "alfred", "census", "world bank", "treasury",
            "bureau of labor", "bureau of economic", "financial stress", "imf",
        )
    )


def _is_external_record(record: Any) -> bool:
    source_type = str(getattr(record, "source_type", "") or "").casefold()
    return _record_has_date(record) and source_type in {
        "issuer_ir",
        "earnings_transcript",
        "news_article",
        "external_research",
        "normalized_research_record",
    }


def _consensus_dates(consensus: Any | None) -> list[str]:
    if consensus is None:
        return []
    rows = []
    for item in [
        getattr(consensus, "target", None),
        getattr(consensus, "recommendations", None),
        *(getattr(consensus, "estimates", []) or []),
        *(getattr(consensus, "surprises", []) or []),
        *(getattr(consensus, "observations", []) or []),
    ]:
        if item is None:
            continue
        value = (
            getattr(item, "source_as_of", None)
            or getattr(item, "provider_timestamp", None)
            or getattr(item, "observed_at", None)
            or getattr(item, "as_of", None)
        )
        if value:
            rows.append(str(value))
    return rows


def _llm_freshness_message(
    dual_model_research: Any | None,
    llm_run_manifest: Any | None,
) -> str:
    if dual_model_research is not None:
        return (
            f"Paired research status: "
            f"{getattr(dual_model_research, 'status', 'Unknown')}; "
            f"cache: {getattr(dual_model_research, 'cache_status', 'unknown')}."
        )
    return (
        f"Thesis synthesis status: "
        f"{getattr(llm_run_manifest, 'llm_execution_status', '') or getattr(llm_run_manifest, 'status', 'Unknown')}."
    )


def _latest_date_value(values: Iterable[Any]) -> str:
    parsed = [
        (parsed_value, str(value))
        for value in values
        if value and (parsed_value := _parse_datetime(value)) is not None
    ]
    if not parsed:
        return ""
    parsed.sort(key=lambda item: item[0])
    return parsed[-1][1]


def _date_sort_key(value: Any) -> datetime:
    return _parse_datetime(value) or datetime.min.replace(tzinfo=timezone.utc)


def _age_status(
    value: Any,
    now: datetime,
    current_days: int,
    partial_days: int,
) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "Unavailable"
    age = max(0, (now.date() - parsed.date()).days)
    if age <= current_days:
        return "Current"
    if age <= partial_days:
        return "Partial"
    return "Stale"


def _market_age_status(value: Any, now: datetime) -> str:
    parsed = _parse_datetime(value)
    if parsed is None:
        return "Unavailable"
    sessions = _trading_sessions_between(parsed.date(), now.date())
    if sessions <= 1:
        return "Current"
    if sessions <= 3:
        return "Partial"
    return "Stale"


def _trading_sessions_between(start: date, end: date) -> int:
    if start >= end:
        return 0
    sessions = 0
    current = start + timedelta(days=1)
    holidays = _us_market_holidays(range(start.year, end.year + 1))
    while current <= end:
        if current.weekday() < 5 and current not in holidays:
            sessions += 1
        current += timedelta(days=1)
    return sessions


def _us_market_holidays(years: Iterable[int]) -> set[date]:
    holidays: set[date] = set()
    for year in years:
        holidays.update({
            _observed(date(year, 1, 1)),
            _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3),
            _last_weekday(year, 5, 0),
            _observed(date(year, 6, 19)),
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 11, 3, 4),
            _observed(date(year, 12, 25)),
            _easter_sunday(year) - timedelta(days=2),
        })
    return holidays


def _observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(
    year: int,
    month: int,
    weekday: int,
    occurrence: int,
) -> date:
    value = date(year, month, 1)
    offset = (weekday - value.weekday()) % 7
    return value + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    value = (
        date(year + 1, 1, 1) - timedelta(days=1)
        if month == 12 else
        date(year, month + 1, 1) - timedelta(days=1)
    )
    return value - timedelta(days=(value.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
