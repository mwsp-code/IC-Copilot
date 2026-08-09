from __future__ import annotations

from equity_research.decision_intelligence import (
    _dedupe_catalysts,
    build_decision_delta,
    build_decision_snapshot,
    build_ic_implied_expectations_snapshot,
    build_prioritized_research_queue,
)
from equity_research.company_model import build_company_model_workspace
from equity_research.market_implied import build_market_implied_expectations
from equity_research.models import (
    CatalystClockItem,
    CompanyIdentity,
    FinancialMetric,
    ValuationResult,
)
from equity_research.sample_data import demo_result


def test_ic_implied_snapshot_projects_existing_market_model() -> None:
    result = demo_result("AAPL")
    snapshot = build_ic_implied_expectations_snapshot(
        result.market_implied_expectations,
        result.company_model,
        result.valuation,
    )
    solved_rows = [
        row for row in result.market_implied_expectations.expectations
        if row.metric == snapshot.implied_variable_label
    ]

    assert snapshot.title == "What the Market Must Believe"
    assert snapshot.current_price == result.market_implied_expectations.current_price
    assert solved_rows
    assert snapshot.implied_variable_value.startswith(
        f"{solved_rows[0].implied_value:,.2f}"
    )
    assert snapshot.assumption_keys


def test_financial_template_uses_sector_appropriate_snapshot() -> None:
    result = demo_result("GS")
    snapshot = result.ic_implied_snapshot

    assert snapshot.template == result.market_implied_expectations.template
    assert snapshot.title == "What the Market Must Believe"
    assert snapshot.implied_variable_label
    assert "FCF growth" not in snapshot.implied_variable_label


def test_commodity_producer_snapshot_prioritizes_break_even_over_generic_dcf() -> None:
    identity = CompanyIdentity(
        "OILX",
        "0000000004",
        "Test Oil Producer",
        sic="1311",
        sic_description="Crude petroleum and natural gas",
    )
    metrics = [
        FinancialMetric(
            name=name,
            value=value,
            unit=unit,
            period_end="2026-03-31",
            filed="2026-05-01",
            source_url="https://www.sec.gov/example",
            source_kind="companyfacts",
            fiscal_period="FY",
            form="10-K",
        )
        for name, value, unit in (
            ("Revenue", 10_000.0, "USD"),
            ("Operating Income", 2_000.0, "USD"),
            ("Operating Cash Flow", 2_500.0, "USD"),
            ("Capital Expenditure", 1_000.0, "USD"),
            ("Cash", 500.0, "USD"),
            ("Long-term Debt", 2_000.0, "USD"),
            ("Shares", 100.0, "shares"),
            ("Oil Production", 200.0, "barrels"),
        )
    ]
    valuation = ValuationResult(
        template="Non-financial",
        status="Insufficient data",
        currency="USD",
    )
    model = build_company_model_workspace(identity, metrics, valuation)
    implied = build_market_implied_expectations(
        identity,
        metrics,
        50.0,
        valuation,
        model,
    )
    snapshot = build_ic_implied_expectations_snapshot(implied, model, valuation)

    assert snapshot.implied_variable_label == "Implied commodity break-even proxy"
    assert snapshot.implied_variable_value != "Unknown"


def test_reit_snapshot_exposes_ffo_base_and_reverse_growth() -> None:
    identity = CompanyIdentity(
        "REITX",
        "0000000005",
        "Test REIT",
        sic="6798",
        sic_description="Real estate investment trust",
    )
    metrics = [
        FinancialMetric(
            name=name,
            value=value,
            unit=unit,
            period_end="2026-03-31",
            filed="2026-05-01",
            source_url="https://www.sec.gov/example",
            source_kind="companyfacts",
            fiscal_period="FY",
            form="10-K",
        )
        for name, value, unit in (
            ("Funds From Operations", 500.0, "USD"),
            ("Net Operating Income", 700.0, "USD"),
            ("Cash", 100.0, "USD"),
            ("Long-term Debt", 1_000.0, "USD"),
            ("Shares", 100.0, "shares"),
        )
    ]
    valuation = ValuationResult(
        template="REIT",
        status="Insufficient data",
        currency="USD",
    )
    model = build_company_model_workspace(identity, metrics, valuation)
    implied = build_market_implied_expectations(
        identity,
        metrics,
        100.0,
        valuation,
        model,
    )
    snapshot = build_ic_implied_expectations_snapshot(implied, model, valuation)

    assert snapshot.operating_base_label == "FFO"
    assert snapshot.implied_variable_label == "Implied FFO growth"


def test_decision_delta_separates_strengthening_and_weakening_changes() -> None:
    current = {
        "ticker": "AAPL",
        "run_id": "new",
        "stage": "Research-Ready",
        "direction": "Long",
        "score": 72,
        "open_action_count": 3,
        "implied_variable_value": "8.0%",
        "completed_at": "2026-07-30T23:30:00+00:00",
        "result_type": "live",
        "research_profile": "deep_initiation",
    }
    previous = {
        "run_id": "old",
        "stage": "Candidate",
        "direction": "Long",
        "score": 65,
        "open_action_count": 5,
        "implied_variable_value": "7.0%",
        "completed_at": "2026-07-29T23:32:00+00:00",
        "result_type": "live",
        "research_profile": "deep_initiation",
    }

    delta = build_decision_delta(current, previous)

    assert delta.status == "Strengthened"
    assert delta.compared_with_run_id == "old"
    assert any("increased" in item for item in delta.strengthening_changes)
    assert any("fell" in item for item in delta.strengthening_changes)
    assert delta.current_completed_at == "2026-07-30T23:30:00+00:00"
    assert delta.compared_with_completed_at == "2026-07-29T23:32:00+00:00"
    assert delta.compared_with_profile == "deep_initiation"


def test_decision_delta_flags_incomparable_profile_change() -> None:
    current = {
        "ticker": "AAPL",
        "run_id": "new",
        "stage": "Research-Ready",
        "direction": "Long",
        "score": 72,
        "open_action_count": 3,
        "implied_variable_value": "8.0%",
        "completed_at": "2026-07-30T23:30:00+00:00",
        "result_type": "live",
        "research_profile": "deep_initiation",
    }
    previous = {
        "run_id": "old",
        "stage": "Candidate",
        "direction": "Long",
        "score": 65,
        "open_action_count": 5,
        "implied_variable_value": "7.0%",
        "completed_at": "2026-07-29T23:32:00+00:00",
        "result_type": "live",
        "research_profile": "fast_screening",
    }

    delta = build_decision_delta(current, previous)

    assert delta.status == "Incomparable profile change"
    assert "different research depths" in delta.summary
    assert any("not directly comparable" in item for item in delta.neutral_changes)
    assert delta.current_completed_at == "2026-07-30T23:30:00+00:00"
    assert delta.compared_with_completed_at == "2026-07-29T23:32:00+00:00"
    assert delta.compared_with_profile == "fast_screening"


def test_prioritized_research_queue_is_capped_at_three() -> None:
    result = demo_result("AAPL")
    queue = build_prioritized_research_queue(result.evidence_work_order, limit=3)

    assert len(queue) <= 3
    assert [item.rank for item in queue] == list(range(1, len(queue) + 1))


def test_demo_decision_snapshot_matches_result_top_idea() -> None:
    result = demo_result("AAPL")
    snapshot = build_decision_snapshot(
        "AAPL",
        result.ideas,
        result.ic_implied_snapshot,
        result.evidence_work_order,
        result.run_manifest.run_id,
    )

    assert snapshot["idea_id"] == result.ideas[0].idea_id
    assert snapshot["stage"] == result.ideas[0].stage


def test_catalyst_clock_deduplicates_across_related_ideas() -> None:
    rows = _dedupe_catalysts([
        CatalystClockItem(
            catalyst_type="monitor_rule",
            label="Consensus reaction",
            due_date="2026-08-29",
            status="Pending",
            source="consensus.revisions",
            related_idea_id="idea-1",
            related_idea_ids=["idea-1"],
        ),
        CatalystClockItem(
            catalyst_type="monitor_deadline",
            label="Consensus reaction",
            due_date="2026-08-29",
            status="Watching",
            source="Consensus estimates provider",
            related_idea_id="idea-2",
            related_idea_ids=["idea-2"],
        ),
    ])

    assert len(rows) == 1
    assert rows[0].related_idea_ids == ["idea-1", "idea-2"]
