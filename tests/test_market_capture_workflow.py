from equity_research.market_capture_workflow import build_market_capture_readiness
from equity_research.models import (
    ChangeEvent,
    ConsensusPackage,
    MarketCapture,
    RevisionWindow,
    TargetConsensus,
    TradeIdea,
)


def test_readiness_is_price_only_when_consensus_history_is_missing() -> None:
    idea = _idea(
        MarketCapture(
            "Unknown",
            4.7,
            None,
            "Not connected",
            "Price is available but consensus revision is missing.",
            price_status="available",
            consensus_status="missing_point_in_time_revision",
            capture_mode="Price-only",
        )
    )

    readiness = build_market_capture_readiness(
        "AAPL",
        [idea],
        ConsensusPackage(
            "AAPL",
            "CSV",
            "Unavailable",
            target=TargetConsensus("AAPL", "2026-06-24", official=True),
        ),
    )

    assert readiness.status == "Price-only ready"
    assert readiness.price_only_ideas == 1
    assert readiness.unknown_ideas == 0
    assert readiness.price_coverage == "Complete"
    assert readiness.consensus_coverage == "Missing"
    assert any(action.area == "Point-in-time revisions" for action in readiness.actions)
    assert any("consensus revision window" in gap for gap in readiness.data_gaps)
    assert len(readiness.snapshot_needs) == 1
    need = readiness.snapshot_needs[0]
    assert need.idea_id == "idea-1"
    assert need.event_date == "2026-06-24"
    assert need.metric_family == "Revenue / demand"
    assert "on or before 2026-06-24" in need.pre_event_snapshot
    assert "after 2026-06-24" in need.post_event_snapshot
    assert "estimate_snapshots.csv" in need.accepted_sources
    assert "pre/post event snapshots" in need.reason
    assert any("estimates.csv pre" in hint and "ticker=AAPL" in hint for hint in need.csv_row_hints)
    assert any("provider_metadata.csv" in hint for hint in need.csv_row_hints)
    assert readiness.import_plan is not None
    assert readiness.import_plan.status == "Needs import"
    assert readiness.import_plan.minimum_required_rows == 3
    assert readiness.import_plan.minimum_viable_rows == 3
    assert readiness.import_plan.full_revision_rows == 3
    assert readiness.import_plan.metric_families == ["Revenue / demand"]
    assert readiness.import_plan.event_dates == ["2026-06-24"]
    assert "estimates.csv" in readiness.import_plan.required_files
    assert "provider_metadata.csv" in readiness.import_plan.required_files
    assert "python scripts/import_consensus_csv.py --write-templates --ticker AAPL" == readiness.import_plan.template_command
    assert "python scripts/import_consensus_csv.py --tickers AAPL" == readiness.import_plan.import_command
    assert "revision window" in readiness.import_plan.blocking_reason
    assert "FMP" in " ".join(readiness.import_plan.provider_options)
    assert "Minimum viable capture" in readiness.import_plan.summary
    assert "do not use today's consensus" in readiness.import_plan.practical_next_step
    assert readiness.consensus_advisor is not None
    assert readiness.consensus_advisor.blocker == "no_revision_window"
    assert "today's consensus cannot backfill" in readiness.consensus_advisor.no_lookahead_rule
    assert readiness.autofill_plan is not None
    assert readiness.autofill_plan.minimum_viable_rows == 3
    assert readiness.autofill_plan.full_revision_rows == 3


def test_readiness_is_ready_when_price_and_revision_are_available() -> None:
    idea = _idea(
        MarketCapture(
            "Partially captured",
            4.7,
            2.2,
            "Not connected",
            "Price and consensus both reacted.",
            price_status="available",
            consensus_status="available",
        )
    )

    readiness = build_market_capture_readiness(
        "AAPL",
        [idea],
        ConsensusPackage(
            "AAPL",
            "CSV",
            "Available",
            target=TargetConsensus("AAPL", "2026-06-24", official=True),
            revisions=[
                RevisionWindow("eps", 30, "2026-06-01", "2026-06-24", 1.0, 1.1, 10.0, status="Available")
            ],
        ),
    )

    assert readiness.status == "Ready"
    assert readiness.classified_ideas == 1
    assert readiness.revision_windows_available == 1
    assert readiness.actions == []
    assert readiness.snapshot_needs == []
    assert readiness.import_plan is not None
    assert readiness.import_plan.status == "Ready"
    assert readiness.import_plan.minimum_required_rows == 0
    assert readiness.import_plan.minimum_viable_rows == 0
    assert readiness.consensus_advisor is not None
    assert readiness.consensus_advisor.blocker == "none"
    assert readiness.autofill_plan is not None
    assert readiness.autofill_plan.status == "Ready"


def test_snapshot_needs_are_driver_specific_for_margin_and_recommendations() -> None:
    margin = _idea(
        MarketCapture(
            "Unknown",
            2.0,
            None,
            "Unknown",
            "Margin idea lacks consensus revision.",
            price_status="available",
            consensus_status="missing_point_in_time_revision",
        ),
        metric_name="Gross Profit",
        title="Long TEST: Gross margin",
    )
    rating = _idea(
        MarketCapture(
            "Unknown",
            None,
            None,
            "Unknown",
            "Rating idea lacks price and consensus.",
            price_status="missing_price_window",
            consensus_status="missing_point_in_time_revision",
        ),
        metric_name="Recommendation",
        title="Long TEST: rating upgrade",
    )

    readiness = build_market_capture_readiness(
        "TEST",
        [margin, rating],
        ConsensusPackage("TEST", "CSV", "Unavailable"),
    )

    families = {item.idea_id: item.metric_family for item in readiness.snapshot_needs}
    sources = {item.idea_id: item.accepted_sources for item in readiness.snapshot_needs}

    assert families["idea-1"] == "Margin / mix"
    assert "estimate_revisions.csv" in sources["idea-1"]
    assert families["idea-2"] == "Recommendation mix"
    assert "recommendations.csv" in sources["idea-2"]
    hints = {item.idea_id: item.csv_row_hints for item in readiness.snapshot_needs}
    assert any("metric=Gross Margin" in hint for hint in hints["idea-1"])
    assert any("recommendations.csv pre" in hint for hint in hints["idea-2"])
    assert readiness.import_plan is not None
    assert readiness.import_plan.minimum_required_rows == 5
    assert "estimates.csv" in readiness.import_plan.required_files
    assert "recommendations.csv" in readiness.import_plan.required_files
    assert "estimate_revisions.csv" in readiness.import_plan.optional_files


def _idea(
    capture: MarketCapture,
    *,
    metric_name: str = "Revenue",
    title: str = "Long TEST: Revenue",
) -> TradeIdea:
    event = ChangeEvent(
        "financial_kpi",
        "Revenue changed",
        "Revenue was higher.",
        4,
        "positive",
        "2026-06-24",
        "SEC Companyfacts",
        metrics={"metric_name": metric_name},
    )
    return TradeIdea(
        "idea-1" if metric_name != "Recommendation" else "idea-2",
        title,
        "Long",
        "Long equity",
        "Revenue may support estimates.",
        "1-2 quarters",
        "Next earnings",
        "Variant view pending market capture.",
        [event],
        market_capture=capture,
        signal_family="financial_kpi",
    )
