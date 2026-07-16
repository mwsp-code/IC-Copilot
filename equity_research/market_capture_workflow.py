from __future__ import annotations

from .models import (
    BringYourOwnDataStatus,
    ConsensusCoverageAdvisor,
    ConsensusPackage,
    MarketCaptureAutofillPlan,
    MarketCaptureAction,
    MarketCaptureImportPlan,
    MarketCaptureReadiness,
    MarketCaptureSnapshotNeed,
    ResearchSourcePlan,
    TradeIdea,
)


def build_market_capture_readiness(
    ticker: str,
    ideas: list[TradeIdea],
    consensus: ConsensusPackage,
    manual_data: BringYourOwnDataStatus | None = None,
    source_plan: ResearchSourcePlan | None = None,
) -> MarketCaptureReadiness:
    ticker = ticker.upper()
    if not ideas:
        return MarketCaptureReadiness(
            ticker=ticker,
            status="Unavailable",
            summary="No generated ideas are available, so market capture cannot be evaluated.",
            total_ideas=0,
            classified_ideas=0,
            unknown_ideas=0,
            price_coverage="Unavailable",
            consensus_coverage="Unavailable",
            official_consensus_available=_has_official_consensus(consensus),
            actions=[],
            data_gaps=["Generate at least one source-linked idea before evaluating market capture."],
        )

    total = len(ideas)
    captures = [idea.market_capture for idea in ideas if idea.market_capture]
    classified = sum(1 for capture in captures if capture.category != "Unknown")
    price_only = sum(1 for capture in captures if getattr(capture, "capture_mode", "") == "Price-only")
    unknown = total - classified
    price_available = sum(1 for capture in captures if capture.price_status == "available")
    consensus_available = sum(1 for capture in captures if capture.consensus_status == "available")
    revision_windows = sum(
        1 for revision in consensus.revisions
        if revision.change_pct is not None and (revision.status or "Available") == "Available"
    )
    official_consensus = _has_official_consensus(consensus)
    price_coverage = _coverage_label(price_available, total)
    consensus_coverage = _coverage_label(consensus_available, total)
    actions: list[MarketCaptureAction] = []
    data_gaps: list[str] = []
    snapshot_needs = _snapshot_needs(ticker, ideas)
    import_plan = _import_plan(ticker, snapshot_needs, official_consensus, revision_windows)
    consensus_advisor = _consensus_advisor(
        official_consensus=official_consensus,
        revision_windows=revision_windows,
        price_available=price_available,
        total=total,
        import_plan=import_plan,
    )
    autofill_plan = _autofill_plan_from_import(import_plan)

    missing_price_ids = [
        idea.idea_id for idea in ideas
        if not idea.market_capture or idea.market_capture.price_status != "available"
    ]
    if missing_price_ids:
        actions.append(MarketCaptureAction(
            area="Event price windows",
            priority="High",
            status="Missing" if price_available == 0 else "Partial",
            action=(
                "Load adjusted daily prices for the ticker, benchmark, sector ETF, and relevant peers; "
                "recompute 1/5/20-trading-day event windows."
            ),
            why_it_matters="Without event-specific price windows, the app cannot tell whether evidence was ignored or already reflected in price.",
            source_type="price_csv_or_provider",
            related_idea_ids=missing_price_ids[:8],
        ))
        data_gaps.append("Some ideas lack event-specific adjusted price reactions.")

    if not official_consensus:
        actions.append(MarketCaptureAction(
            area="Official consensus",
            priority="High",
            status="Missing",
            action=(
                "Configure an official/licensed consensus source or import CSV snapshots for targets, EPS, revenue, "
                "recommendations, and provider metadata."
            ),
            why_it_matters="Unofficial targets or estimates can support context, but cannot establish high-confidence market capture.",
            source_type="consensus_provider_or_csv",
            related_idea_ids=[idea.idea_id for idea in ideas[:8]],
        ))
        data_gaps.append("Official or licensed/manual point-in-time consensus is unavailable.")

    if revision_windows == 0:
        actions.append(MarketCaptureAction(
            area="Point-in-time revisions",
            priority="High",
            status="Missing",
            action=(
                "Seed at least two snapshots around each event date: one observed before the event and one after it, "
                "for the affected metric family."
            ),
            why_it_matters="Current consensus alone cannot prove whether the market revised expectations after the evidence arrived.",
            source_type="targets_estimates_recommendations_csv",
            related_idea_ids=[idea.idea_id for idea in ideas if idea.market_capture and idea.market_capture.consensus_status != "available"][:8],
        ))
        data_gaps.append("No usable 7/30/90-day consensus revision window is available.")
    elif consensus_available < total:
        actions.append(MarketCaptureAction(
            area="Metric-specific revisions",
            priority="Medium",
            status="Partial",
            action="Add snapshots for the specific metrics named in each idea, such as revenue, EPS, margin, target, or recommendation mix.",
            why_it_matters="A target-price revision may not capture a revenue, margin, or dilution thesis unless the affected metric is also tracked.",
            source_type="metric_specific_consensus_csv",
            related_idea_ids=[
                idea.idea_id for idea in ideas
                if idea.market_capture and idea.market_capture.consensus_status != "available"
            ][:8],
        ))

    manual_consensus = _manual_source_status(manual_data, "consensus")
    if manual_consensus and manual_consensus not in {"Available", "Partial"}:
        actions.append(MarketCaptureAction(
            area="CSV import hygiene",
            priority="Medium",
            status=manual_consensus,
            action="Use the built-in consensus CSV templates and import them before rerunning thesis synthesis.",
            why_it_matters="CSV imports are the open-source fallback for first-time users who lack paid consensus history.",
            source_type="consensus_csv_templates",
            related_idea_ids=[],
        ))

    if source_plan and any(request.source_type == "consensus_manual" for request in source_plan.requests):
        actions.append(MarketCaptureAction(
            area="Source-plan follow-up",
            priority="Medium",
            status="Planned",
            action="Complete the existing consensus/manual source-plan request and rerun research.",
            why_it_matters="The source planner already identified consensus history as a gate blocker.",
            source_type="consensus_manual",
            related_idea_ids=[],
        ))

    actions = _dedupe_actions(actions)
    if classified == total and total:
        status = "Ready"
        summary = f"All {total} idea(s) have both event price reaction and usable point-in-time consensus revision."
    elif price_available and revision_windows:
        status = "Partial"
        summary = (
            f"{classified}/{total} idea(s) have classifiable market capture. "
            "Use the actions below to close remaining price or consensus-history gaps."
        )
    elif price_available:
        status = "Price-only ready"
        summary = (
            "Event price reaction is available, so the app can discuss price-only market capture. "
            "Point-in-time consensus revisions are missing, so it cannot claim expectations changed or failed to react."
        )
    elif official_consensus or revision_windows:
        status = "Blocked - price windows"
        summary = (
            "Consensus history is present, but event-specific price windows are missing. "
            "The app cannot classify market capture without adjusted price reactions."
        )
    else:
        status = "Not ready"
        summary = "Market capture needs both event price windows and point-in-time consensus revision history."

    return MarketCaptureReadiness(
        ticker=ticker,
        status=status,
        summary=summary,
        total_ideas=total,
        classified_ideas=classified,
        unknown_ideas=max(0, unknown - price_only),
        price_coverage=price_coverage,
        consensus_coverage=consensus_coverage,
        official_consensus_available=official_consensus,
        revision_windows_available=revision_windows,
        price_only_ideas=price_only,
        actions=actions,
        snapshot_needs=snapshot_needs,
        import_plan=import_plan,
        consensus_advisor=consensus_advisor,
        autofill_plan=autofill_plan,
        data_gaps=_dedupe_strings(data_gaps),
    )


def _snapshot_needs(ticker: str, ideas: list[TradeIdea]) -> list[MarketCaptureSnapshotNeed]:
    rows: list[MarketCaptureSnapshotNeed] = []
    for idea in ideas:
        capture = idea.market_capture
        if capture and capture.consensus_status == "available":
            continue
        event = idea.source_events[0] if idea.source_events else None
        event_date = event.event_date if event else None
        metric = _metric_family(idea, event)
        rows.append(MarketCaptureSnapshotNeed(
            idea_id=idea.idea_id,
            event_date=event_date,
            metric_family=metric,
            pre_event_snapshot=(
                f"Latest official/manual snapshot observed on or before {event_date}"
                if event_date else "Latest official/manual snapshot observed before the source event"
            ),
            post_event_snapshot=(
                f"First official/manual snapshot observed after {event_date}, plus 7/30/90-day follow-up"
                if event_date else "First official/manual snapshot observed after the source event, plus 7/30/90-day follow-up"
            ),
            accepted_sources=_accepted_sources(metric),
            csv_row_hints=_csv_row_hints(ticker, metric, event_date),
            reason=_snapshot_reason(idea, metric),
            status="Needed" if not capture or capture.consensus_revision_pct is None else "Partial",
        ))
    return rows[:12]


def _metric_family(idea: TradeIdea, event) -> str:
    metric = ""
    if event:
        metric = str((event.metrics or {}).get("metric_name") or "")
    text = " ".join([metric, idea.signal_family or "", idea.title, idea.thesis]).lower()
    if any(token in text for token in ("gross", "margin", "cogs", "mix")):
        return "Margin / mix"
    if any(token in text for token in ("target", "valuation", "price target")):
        return "Target price"
    if any(token in text for token in ("recommendation", "rating", "upgrade", "downgrade")):
        return "Recommendation mix"
    if any(token in text for token in ("eps", "earnings per share", "net income", "profit")):
        return "EPS / earnings"
    if any(token in text for token in ("revenue", "sales", "demand", "gmv")):
        return "Revenue / demand"
    if any(token in text for token in ("share", "dilution", "buyback")):
        return "Share count / dilution"
    return "Target, EPS, revenue, and recommendation"


def _accepted_sources(metric: str) -> list[str]:
    base = ["FMP/Alpha Vantage/Finnhub where licensed", "CSV/manual point-in-time import"]
    if metric in {"EPS / earnings", "Revenue / demand", "Margin / mix"}:
        return ["estimate_snapshots.csv", "estimate_revisions.csv", "provider_metadata.csv"] + base
    if metric == "Target price":
        return ["targets.csv", "target_revisions.csv", "provider_metadata.csv"] + base
    if metric == "Recommendation mix":
        return ["recommendations.csv", "provider_metadata.csv"] + base
    return [
        "targets.csv",
        "estimates.csv",
        "recommendations.csv",
        "provider_metadata.csv",
    ] + base


def _csv_row_hints(ticker: str, metric: str, event_date: str | None) -> list[str]:
    symbol = ticker.upper()
    pre_date = f"<snapshot observed on/before {event_date}>" if event_date else "<pre-event observed_at>"
    post_date = f"<snapshot observed after {event_date}>" if event_date else "<post-event observed_at>"
    period = "<affected fiscal period>"
    hints: list[str]
    if metric in {"EPS / earnings", "Revenue / demand", "Margin / mix"}:
        estimate_metric = "Gross Margin" if metric == "Margin / mix" else "Revenue" if metric == "Revenue / demand" else "EPS"
        hints = [
            (
                "estimates.csv pre: "
                f"ticker={symbol}, as_of={pre_date}, observed_at={pre_date}, metric={estimate_metric}, "
                f"period_end={period}, average=<pre-event consensus>, official=true"
            ),
            (
                "estimates.csv post: "
                f"ticker={symbol}, as_of={post_date}, observed_at={post_date}, metric={estimate_metric}, "
                f"period_end={period}, average=<post-event consensus>, official=true"
            ),
            (
                "estimate_revisions.csv optional: "
                f"ticker={symbol}, metric={estimate_metric}, window_days=7/30/90, "
                "start_value=<pre>, end_value=<post>, change_pct=<computed or source>, official=true"
            ),
        ]
    elif metric == "Target price":
        hints = [
            (
                "targets.csv pre: "
                f"ticker={symbol}, as_of={pre_date}, observed_at={pre_date}, "
                "target_mean=<pre-event target>, currency=<currency>, official=true"
            ),
            (
                "targets.csv post: "
                f"ticker={symbol}, as_of={post_date}, observed_at={post_date}, "
                "target_mean=<post-event target>, currency=<currency>, official=true"
            ),
            (
                "target_revisions.csv optional: "
                f"ticker={symbol}, metric=target_mean, window_days=7/30/90, "
                "start_value=<pre>, end_value=<post>, change_pct=<computed or source>, official=true"
            ),
        ]
    elif metric == "Recommendation mix":
        hints = [
            (
                "recommendations.csv pre: "
                f"ticker={symbol}, as_of={pre_date}, observed_at={pre_date}, "
                "strong_buy=<n>, buy=<n>, hold=<n>, sell=<n>, strong_sell=<n>, official=true"
            ),
            (
                "recommendations.csv post: "
                f"ticker={symbol}, as_of={post_date}, observed_at={post_date}, "
                "strong_buy=<n>, buy=<n>, hold=<n>, sell=<n>, strong_sell=<n>, official=true"
            ),
        ]
    else:
        hints = [
            (
                "targets.csv pre/post or estimates.csv pre/post: "
                f"ticker={symbol}, observed_at=<pre and post event timestamps>, "
                "value=<consensus snapshot>, official=true"
            ),
            (
                "recommendations.csv pre/post: "
                f"ticker={symbol}, observed_at=<pre and post event timestamps>, "
                "rating counts=<source values>, official=true"
            ),
        ]
    hints.append(
        "provider_metadata.csv: "
        f"ticker={symbol}, provider=<source name>, observed_at=<retrieval time>, "
        "source_as_of=<vendor/source date>, entitlement_status=available, provenance=<licensed/manual>, official=true"
    )
    return hints


def _snapshot_reason(idea: TradeIdea, metric: str) -> str:
    capture = idea.market_capture
    if not capture:
        return f"{metric} market capture cannot be assessed because the idea has no market-capture object."
    if capture.consensus_status == "missing_point_in_time_revision":
        return f"{metric} needs pre/post event snapshots; current data has price reaction but no point-in-time revision."
    if not capture.consensus_official:
        return f"{metric} uses unofficial or unverified consensus context; official/manual snapshots are required for conviction."
    return f"{metric} needs source-specific snapshots before the app can classify priced-in status."


def _import_plan(
    ticker: str,
    snapshot_needs: list[MarketCaptureSnapshotNeed],
    official_consensus: bool,
    revision_windows: int,
) -> MarketCaptureImportPlan:
    symbol = ticker.upper()
    if not snapshot_needs and official_consensus and revision_windows:
        return MarketCaptureImportPlan(
            status="Ready",
            summary="Point-in-time consensus revisions are already available for the generated ideas.",
            minimum_required_rows=0,
            minimum_viable_rows=0,
            full_revision_rows=0,
            import_command=f"python scripts/import_consensus_csv.py --tickers {symbol}",
            practical_next_step="No consensus import is required for the current classified ideas.",
        )
    if not snapshot_needs:
        return MarketCaptureImportPlan(
            status="No immediate import needed",
            summary="No idea-specific consensus import rows were requested by the current run.",
            minimum_required_rows=0,
            minimum_viable_rows=0,
            full_revision_rows=0,
            import_command=f"python scripts/import_consensus_csv.py --tickers {symbol}",
            practical_next_step="Generate ideas first, then seed only the event-specific snapshots that remain unknown.",
        )

    families = _dedupe_strings([need.metric_family for need in snapshot_needs])
    dates = _dedupe_strings([need.event_date or "unknown" for need in snapshot_needs])
    required_files = _required_files_for(families)
    optional_files = _optional_files_for(families)
    minimum_rows = _minimum_rows_for(families, len(dates))
    minimum_viable_rows = _minimum_viable_rows_for(len(snapshot_needs))
    full_revision_rows = minimum_rows
    missing_current = "" if official_consensus else "Official/current consensus is missing. "
    missing_history = "" if revision_windows else "No 7/30/90-day revision window is available. "
    return MarketCaptureImportPlan(
        status="Needs import",
        summary=(
            f"Minimum viable capture needs {minimum_viable_rows} row(s): one pre-event and one post-event "
            f"snapshot for each unknown idea. Full 7/30/90-day coverage is about {full_revision_rows} row(s) "
            f"for {symbol} across {len(families)} metric family/families and {len(dates)} event date(s)."
        ),
        minimum_required_rows=minimum_rows,
        minimum_viable_rows=minimum_viable_rows,
        full_revision_rows=full_revision_rows,
        required_files=required_files,
        optional_files=optional_files,
        metric_families=families,
        event_dates=dates,
        template_command=f"python scripts/import_consensus_csv.py --write-templates --ticker {symbol}",
        import_command=f"python scripts/import_consensus_csv.py --tickers {symbol}",
        blocking_reason=(
            missing_current + missing_history
            + "Market capture remains Unknown until pre-event and post-event consensus snapshots exist."
        ).strip(),
        next_steps=[
            "Generate or copy the CSV templates under data/consensus_import/.",
            "For a quick first pass, enter one official/manual snapshot observed on or before each event date and one after it.",
            "For full revision history, add 7/30/90-day follow-up snapshots or source-provided revision rows.",
            "Run the import command, then rerun research so revisions are calculated point-in-time.",
        ],
        provider_options=_provider_options_for_consensus(),
        practical_next_step=(
            f"Start with {minimum_viable_rows} CSV rows or an FMP-style licensed consensus endpoint; "
            "do not use today's consensus to backfill historical capture."
        ),
    )


def _consensus_advisor(
    *,
    official_consensus: bool,
    revision_windows: int,
    price_available: int,
    total: int,
    import_plan: MarketCaptureImportPlan,
) -> ConsensusCoverageAdvisor:
    if price_available == 0 and total:
        blocker = "missing_price"
        required_fix = "Load event-specific adjusted price windows before classifying market capture."
    elif not official_consensus:
        blocker = "missing_official_consensus"
        required_fix = "Configure an official/licensed source or import manual point-in-time consensus snapshots."
    elif revision_windows == 0:
        blocker = "no_revision_window"
        required_fix = "Seed pre-event and post-event snapshots so the app can calculate expectation changes."
    elif import_plan.status == "Ready":
        blocker = "none"
        required_fix = "No immediate market-capture autofill is required."
    else:
        blocker = "partial_consensus_history"
        required_fix = "Add metric-specific snapshots for the idea families still marked Unknown."
    return ConsensusCoverageAdvisor(
        status="Ready" if blocker == "none" else "Blocked" if blocker in {"missing_price", "missing_official_consensus", "no_revision_window"} else "Partial",
        blocker=blocker,
        summary=_advisor_summary(blocker),
        required_fix=required_fix,
        no_lookahead_rule=(
            "Historical capture can only use snapshots observed on or before the event or post-event date; "
            "today's consensus cannot backfill an old event."
        ),
        provider_options=list(import_plan.provider_options),
        data_gaps=[] if blocker == "none" else [required_fix],
    )


def _advisor_summary(blocker: str) -> str:
    summaries = {
        "missing_price": "Price windows are missing, so the app cannot tell whether the market reacted to the evidence.",
        "missing_official_consensus": "Price reaction may be available, but official point-in-time consensus is missing.",
        "no_revision_window": "Current consensus is not enough; the app needs before/after snapshots around event dates.",
        "partial_consensus_history": "Some ideas have usable capture inputs, while others still need metric-specific snapshots.",
        "none": "Market-capture inputs are sufficient for the current classified ideas.",
    }
    return summaries.get(blocker, "Market-capture inputs are incomplete.")


def _autofill_plan_from_import(import_plan: MarketCaptureImportPlan) -> MarketCaptureAutofillPlan:
    return MarketCaptureAutofillPlan(
        status=import_plan.status,
        minimum_viable_rows=import_plan.minimum_viable_rows,
        full_revision_rows=import_plan.full_revision_rows,
        summary=import_plan.summary,
        next_steps=list(import_plan.next_steps),
        required_files=list(import_plan.required_files),
        optional_files=list(import_plan.optional_files),
    )


def _required_files_for(families: list[str]) -> list[str]:
    files: list[str] = ["provider_metadata.csv"]
    for family in families:
        if family in {"EPS / earnings", "Revenue / demand", "Margin / mix"}:
            files.append("estimates.csv")
        elif family == "Target price":
            files.append("targets.csv")
        elif family == "Recommendation mix":
            files.append("recommendations.csv")
        else:
            files.extend(["targets.csv", "estimates.csv", "recommendations.csv"])
    return _dedupe_strings(files)


def _optional_files_for(families: list[str]) -> list[str]:
    files: list[str] = []
    for family in families:
        if family in {"EPS / earnings", "Revenue / demand", "Margin / mix"}:
            files.append("estimate_revisions.csv")
        elif family == "Target price":
            files.append("target_revisions.csv")
    files.append("surprises.csv")
    return _dedupe_strings(files)


def _minimum_rows_for(families: list[str], event_date_count: int) -> int:
    event_count = max(1, event_date_count)
    rows_per_event = 0
    for family in families:
        if family in {
            "EPS / earnings",
            "Revenue / demand",
            "Margin / mix",
            "Target price",
            "Recommendation mix",
        }:
            rows_per_event += 2
        else:
            rows_per_event += 6
    return rows_per_event * event_count + 1


def _minimum_viable_rows_for(snapshot_need_count: int) -> int:
    return max(0, snapshot_need_count * 2 + 1)


def _provider_options_for_consensus() -> list[str]:
    return [
        "Free: accumulate daily local snapshots going forward; first-time historical capture still needs CSV/manual rows.",
        "Lean paid: FMP targets/estimates/recommendations/surprises if the selected plan includes the needed endpoints.",
        "Alpha Vantage: useful current aggregate/company overview data, but not a complete historical revision source by itself.",
        "Finnhub: useful recommendation-trend validation; do not rely on it alone for historical price-target revisions.",
        "CSV/manual: lowest-cost point-in-time seed path for broker notes, exported consensus sheets, or user-owned data.",
    ]


def _has_official_consensus(consensus: ConsensusPackage) -> bool:
    if consensus.target and consensus.target.official:
        return True
    if consensus.recommendations and consensus.recommendations.official:
        return True
    if any(item.official for item in consensus.estimates):
        return True
    if any(status.official and status.status not in {"Unavailable", "Disabled"} for status in consensus.provider_statuses):
        return True
    return False


def _coverage_label(available: int, total: int) -> str:
    if not total:
        return "Unavailable"
    if available == total:
        return "Complete"
    if available:
        return f"Partial ({available}/{total})"
    return "Missing"


def _manual_source_status(manual_data: BringYourOwnDataStatus | None, source_type: str) -> str:
    if not manual_data:
        return ""
    for source in manual_data.sources:
        if source.source_type == source_type:
            return source.status
    return ""


def _dedupe_actions(actions: list[MarketCaptureAction]) -> list[MarketCaptureAction]:
    seen: set[tuple[str, str, str]] = set()
    result: list[MarketCaptureAction] = []
    for action in actions:
        key = (action.area, action.status, action.action)
        if key in seen:
            continue
        seen.add(key)
        result.append(action)
    priority_rank = {"High": 0, "Medium": 1, "Low": 2}
    return sorted(result, key=lambda item: (priority_rank.get(item.priority, 9), item.area))


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
