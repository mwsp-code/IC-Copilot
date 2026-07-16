from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from .models import HistoricalReference, HistoricalReferenceSet, TradeIdea
from .research_store import ResearchStore


MIN_ANALOG_SAMPLE = 5


def build_historical_references(
    ideas: list[TradeIdea],
    store: ResearchStore,
    *,
    minimum_sample_size: int = MIN_ANALOG_SAMPLE,
    limit: int = 8,
) -> HistoricalReferenceSet:
    top = _reference_target(ideas)
    if not top:
        return HistoricalReferenceSet(
            status="Unavailable",
            scope="No idea",
            sample_size=0,
            minimum_sample_size=minimum_sample_size,
            data_gaps=["No generated idea is available for historical reference matching."],
            summary="No historical reference search was run because no idea exists.",
        )
    rows = store.historical_idea_rows()
    return _build_reference_set(top, rows, minimum_sample_size, limit)


def build_historical_references_for_ticker(
    ticker: str,
    store: ResearchStore,
    *,
    minimum_sample_size: int = MIN_ANALOG_SAMPLE,
    limit: int = 8,
) -> HistoricalReferenceSet:
    rows = store.historical_idea_rows()
    ticker_rows = [row for row in rows if str(row.get("ticker") or "").upper() == ticker.upper()]
    if not ticker_rows:
        return HistoricalReferenceSet(
            status="Unavailable",
            scope=f"{ticker.upper()} / no stored ideas",
            sample_size=0,
            minimum_sample_size=minimum_sample_size,
            data_gaps=["No stored idea versions exist for this ticker. Run research first."],
            summary="No historical reference search was run because no stored idea exists.",
        )
    top_row = sorted(
        ticker_rows,
        key=lambda row: (
            row.get("created_at") or "",
            int(row.get("storage_order") or 0),
            _stage_rank((row.get("payload") or {}).get("stage") or row.get("stage")),
            _score_total(row.get("payload") or {}),
        ),
        reverse=True,
    )[0]
    return _build_reference_set(
        _target_from_row(top_row), rows, minimum_sample_size, limit,
        exclude_run_id=top_row.get("run_id"),
    )


def _build_reference_set(
    top: TradeIdea | "_StoredIdeaTarget",
    rows: list[dict],
    minimum_sample_size: int,
    limit: int,
    exclude_run_id: str | None = None,
) -> HistoricalReferenceSet:
    references = []
    for row in rows:
        if row["idea_id"] == top.idea_id:
            continue
        if exclude_run_id and row.get("run_id") == exclude_run_id:
            continue
        reference = _reference_from_row(top, row)
        if reference and reference.similarity_score >= 45:
            references.append(reference)
    references.sort(
        key=lambda item: (
            item.stage in {"High-Conviction", "Investable"},
            item.outcome_status == "resolved",
            item.similarity_score,
        ),
        reverse=True,
    )
    selected = references[:limit]
    resolved = [item for item in selected if item.realized_return_pct is not None]
    hit_rate = _hit_rate(resolved)
    mean_return = mean(item.realized_return_pct for item in resolved) if resolved else None
    gaps = []
    if not selected:
        gaps.append("No sufficiently similar prior idea versions are stored locally.")
    if len(resolved) < minimum_sample_size:
        gaps.append(
            f"Only {len(resolved)} resolved historical references are available; "
            f"{minimum_sample_size} are needed before analogs can support calibrated conviction."
        )
    status = "Referenceable" if selected else "Unavailable"
    if selected and len(resolved) < minimum_sample_size:
        status = "Sparse"
    if selected and len(resolved) >= minimum_sample_size:
        status = "Supported"
    summary = _summary(top, selected, resolved, hit_rate)
    return HistoricalReferenceSet(
        status=status,
        scope=f"{top.signal_family or 'general'} / {top.direction} / {top.horizon}",
        sample_size=len(resolved),
        minimum_sample_size=minimum_sample_size,
        references=selected,
        hit_rate_pct=hit_rate,
        mean_realized_return_pct=mean_return,
        data_gaps=gaps,
        summary=summary,
    )


@dataclass
class _StoredScore:
    total: int = 0


@dataclass
class _StoredMarketCapture:
    category: str = "Unknown"


@dataclass
class _StoredEvent:
    category: str = ""
    event_date: str | None = None


@dataclass
class _StoredIdeaTarget:
    idea_id: str
    signal_family: str
    direction: str
    horizon: str
    stage: str
    score: _StoredScore
    citations: list
    market_capture: _StoredMarketCapture
    source_events: list[_StoredEvent]


def _target_from_row(row: dict) -> _StoredIdeaTarget:
    payload = row.get("payload") or {}
    capture = payload.get("market_capture") or {}
    source_events = payload.get("source_events") or []
    first_event = source_events[0] if source_events else {}
    return _StoredIdeaTarget(
        idea_id=row.get("idea_id") or payload.get("idea_id") or "",
        signal_family=row.get("signal_family") or payload.get("signal_family") or "",
        direction=payload.get("direction") or "",
        horizon=row.get("horizon") or payload.get("horizon") or "",
        stage=payload.get("stage") or row.get("stage") or "",
        score=_StoredScore(_score_total(payload)),
        citations=[],
        market_capture=_StoredMarketCapture(capture.get("category") or "Unknown"),
        source_events=[
            _StoredEvent(
                category=str(first_event.get("category") or ""),
                event_date=first_event.get("event_date"),
            )
        ] if first_event else [],
    )


def _reference_target(ideas: list[TradeIdea]) -> TradeIdea | None:
    if not ideas:
        return None
    return sorted(
        ideas,
        key=lambda idea: (
            _stage_rank(idea.stage),
            idea.score.total if idea.score else 0,
            len(idea.citations),
        ),
        reverse=True,
    )[0]


def _stage_rank(stage: str | None) -> int:
    return {"High-Conviction": 4, "Investable": 4, "Research-Ready": 3, "Candidate": 2}.get(stage or "", 0)


def _score_total(payload: dict) -> int:
    score = payload.get("score") or {}
    try:
        return int(score.get("total") or 0)
    except (TypeError, ValueError):
        return 0


def _reference_from_row(target: TradeIdea, row: dict) -> HistoricalReference | None:
    payload = row.get("payload") or {}
    score = 0
    reasons = []
    if row.get("signal_family") and row.get("signal_family") == target.signal_family:
        score += 35
        reasons.append("same signal family")
    if payload.get("direction") == target.direction:
        score += 20
        reasons.append("same direction")
    if row.get("horizon") == target.horizon:
        score += 15
        reasons.append("same horizon")
    if payload.get("stage") in {"High-Conviction", "Investable"}:
        score += 15
        reasons.append("promoted historical idea")
    if _market_capture(payload) == _market_capture(_idea_payload(target)) and _market_capture(payload) != "Unknown":
        score += 10
        reasons.append("similar market-capture classification")
    if _source_category(payload) and _source_category(payload) == _source_category(_idea_payload(target)):
        score += 10
        reasons.append("same source-event category")
    if score <= 0:
        return None
    signal = _best_signal(row.get("event_signals") or [])
    outcome = row.get("outcome") or {}
    realized = _first_number(
        outcome.get("realized_return_pct"),
        signal.get("realized_return_pct") if signal else None,
    )
    adverse = _first_number(
        outcome.get("max_adverse_excursion_pct"),
        signal.get("max_adverse_excursion_pct") if signal else None,
    )
    favorable = _first_number(
        outcome.get("max_favorable_excursion_pct"),
        signal.get("max_favorable_excursion_pct") if signal else None,
    )
    abnormal = _first_number(signal.get("abnormal_return_pct") if signal else None)
    return HistoricalReference(
        reference_id=f"{row.get('idea_id')}:{row.get('version')}",
        ticker=row.get("ticker") or "",
        idea_title=payload.get("title") or row.get("idea_id") or "Historical idea",
        signal_family=row.get("signal_family") or payload.get("signal_family") or "",
        direction=payload.get("direction") or "",
        stage=payload.get("stage") or row.get("stage") or "",
        event_date=_source_event_date(payload) or (signal.get("event_date") if signal else None),
        horizon=row.get("horizon") or payload.get("horizon") or "",
        similarity_score=min(100, score),
        match_reasons=reasons,
        realized_return_pct=realized,
        abnormal_return_pct=abnormal,
        max_adverse_excursion_pct=adverse,
        max_favorable_excursion_pct=favorable,
        outcome_status="resolved" if realized is not None else "unresolved",
        confidence="Medium" if realized is not None and score >= 70 else "Low",
    )


def _idea_payload(idea: TradeIdea) -> dict:
    return {
        "direction": idea.direction,
        "stage": idea.stage,
        "market_capture": {"category": idea.market_capture.category if idea.market_capture else "Unknown"},
        "source_events": [
            {"category": event.category, "event_date": event.event_date}
            for event in idea.source_events[:1]
        ],
    }


def _market_capture(payload: dict) -> str:
    capture = payload.get("market_capture") or {}
    return capture.get("category") or "Unknown"


def _source_category(payload: dict) -> str:
    events = payload.get("source_events") or []
    return str((events[0] if events else {}).get("category") or "")


def _source_event_date(payload: dict) -> str | None:
    events = payload.get("source_events") or []
    value = (events[0] if events else {}).get("event_date")
    return str(value) if value else None


def _best_signal(signals: list[dict]) -> dict:
    resolved = [signal for signal in signals if signal.get("realized_return_pct") is not None]
    return (resolved or signals or [{}])[0]


def _first_number(*values) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _hit_rate(references: list[HistoricalReference]) -> float | None:
    if not references:
        return None
    hits = []
    for item in references:
        realized = item.realized_return_pct
        if realized is None:
            continue
        hits.append(1 if (realized > 0 if item.direction != "Short" else realized < 0) else 0)
    return sum(hits) / len(hits) * 100 if hits else None


def _summary(
    target: TradeIdea,
    references: list[HistoricalReference],
    resolved: list[HistoricalReference],
    hit_rate: float | None,
) -> str:
    if not references:
        return (
            f"No local historical analogs were found for the top {target.signal_family or 'general'} "
            f"{target.direction} setup. Treat this as evidence-gathering, not historical validation."
        )
    if not resolved:
        return (
            f"Found {len(references)} similar prior idea(s), but none have resolved outcomes yet. "
            "Use them as checklist references, not probability evidence."
        )
    hit = f"; hit rate {hit_rate:.1f}%" if hit_rate is not None else ""
    if hit_rate is not None and hit_rate <= 35:
        outcome_note = (
            " Resolved analogs have a weak hit rate, so this should be treated as a warning "
            "or counter-evidence rather than support."
        )
    elif hit_rate is not None and hit_rate < 55:
        outcome_note = (
            " Resolved analogs are mixed, so they should be used as a checklist rather than "
            "supporting evidence."
        )
    else:
        outcome_note = " Historical references are supporting context and do not replace current source evidence."
    return (
        f"Found {len(references)} similar prior idea(s), including {len(resolved)} with resolved outcomes{hit}. "
        f"{outcome_note}"
    )
