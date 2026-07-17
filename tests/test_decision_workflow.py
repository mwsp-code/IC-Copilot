from pathlib import Path

from equity_research.analysis import build_financial_metrics
from equity_research.causal_thesis_graph import build_causal_thesis_graphs
from equity_research.company_model import build_company_model_workspace
from equity_research.evidence_closure import execute_evidence_work_order
from equity_research.market_implied import build_market_implied_expectations
from equity_research.earnings_surprise_proxy import build_earnings_surprise_proxy
from equity_research.models import (
    ChangeEvent,
    Citation,
    ClaimValidationResult,
    CompanyIdentity,
    ConsensusPackage,
    DriverAnalysis,
    EvidenceWorkOrder,
    EvidenceWorkOrderItem,
    ExternalEvidenceBundle,
    ExpectationComparison,
    ExpectationEventAudit,
    ExpectationsBridge,
    FinancialMetric,
    ManagementSourcePackage,
    MonitorItem,
    TradeIdea,
    ValidatedClaim,
    ValuationCase,
    ValuationResult,
)
from equity_research.research_modes import build_research_mode_suite
from equity_research.research_store import ResearchStore


def _metric(name, value, unit="USD", previous=None, yoy=None, fiscal_period="FY", form="10-K"):
    return FinancialMetric(
        name=name,
        value=value,
        unit=unit,
        period_end="2026-03-31",
        filed="2026-05-01",
        previous_value=previous,
        yoy_change_pct=yoy,
        source_url="https://www.sec.gov/example",
        source_kind="companyfacts",
        fiscal_period=fiscal_period,
        form=form,
    )


def _metrics():
    return [
        _metric("Revenue", 1_000.0, previous=900.0, yoy=11.1),
        _metric("Gross Profit", 500.0),
        _metric("Operating Income", 250.0),
        _metric("Net Income", 180.0),
        _metric("Operating Cash Flow", 220.0),
        _metric("Capital Expenditure", 70.0),
        _metric("Cash", 300.0),
        _metric("Current Debt", 50.0),
        _metric("Long-term Debt", 150.0),
        _metric("Interest Expense", 12.0),
        _metric("Shares", 100.0, "shares"),
    ]


def _fact_row(start, end, value, form, fiscal_period, fiscal_year, filed):
    return {
        "start": start,
        "end": end,
        "val": value,
        "form": form,
        "fp": fiscal_period,
        "fy": fiscal_year,
        "filed": filed,
    }


def _identity():
    return CompanyIdentity("TEST", "0000000001", "Test Company", sic="3571", sic_description="Technology")


def _valuation():
    return ValuationResult(
        template="Non-financial",
        status="Available",
        currency="USD",
        cases=[
            ValuationCase("Bear", 0.25, 80.0, "DCF"),
            ValuationCase("Base", 0.50, 110.0, "DCF"),
            ValuationCase("Bull", 0.25, 150.0, "DCF"),
        ],
    )


def _empty_context():
    return {
        "filings": [],
        "validated_claims": ClaimValidationResult("TEST", "Available"),
        "management_sources": ManagementSourcePackage("TEST", "Unavailable"),
        "external_evidence": ExternalEvidenceBundle("TEST", "Unavailable"),
        "consensus": ConsensusPackage("TEST", "none", "Unavailable"),
        "ideas": [],
        "peer_metric_readthrough": {},
        "primary_observations": [],
        "corroboration_results": [],
    }


def test_evidence_closure_resolves_normalized_metric_and_classifies_consensus_boundary():
    work_order = EvidenceWorkOrder(
        "Open",
        "Two tasks",
        items=[
            EvidenceWorkOrderItem(
                "metric", "High", "Credit", "Confirm Interest Expense", "sec_filing",
                "Interest Expense with period and source", "Tests interest burden", "test",
            ),
            EvidenceWorkOrderItem(
                "history", "Medium", "Market capture", "Load historical estimates", "consensus_manual",
                "Point-in-time estimate revisions", "Tests expectations", "test",
            ),
        ],
    )
    report = execute_evidence_work_order(
        "TEST", work_order, metrics=_metrics(), **_empty_context(),
    )

    assert report.resolved_count == 1
    assert report.licensed_or_manual_count == 1
    assert report.outcomes[0].status == "resolved"
    assert "Interest Expense" in report.outcomes[0].matched_evidence[0]
    assert work_order.items[1].status == "licensed_or_manual_required"


def test_market_implied_reverse_dcf_and_company_model_are_auditable():
    model = build_company_model_workspace(_identity(), _metrics(), _valuation())
    implied = build_market_implied_expectations(_identity(), _metrics(), 100.0, _valuation(), model)

    reverse_dcf = next(row for row in implied.expectations if row.metric == "Reverse DCF: implied five-year FCF growth")
    base_fcf = next(row for row in implied.expectations if row.metric == "Reverse DCF base FCF")
    assert implied.status == "Available"
    assert reverse_dcf.implied_value is not None
    assert "Solve EV" in reverse_dcf.formula
    assert base_fcf.implied_value == 150.0
    assert base_fcf.status == "Observed"
    assert all(row.source for row in model.historicals)
    assert all(assumption.provenance in {"source", "formula", "user_override", "illustrative_default"} for assumption in model.assumptions)
    assert {case.name for case in model.cases} == {"Bear", "Base", "Bull"}
    assert implied.financial_basis == "Annual normalized filing facts"
    assert implied.financial_period == "2026-03-31"


def test_market_implied_quarterly_flow_basis_is_disclosed_and_confidence_reduced():
    metrics = [
        _metric(row.name, row.value, row.unit, row.previous_value, row.yoy_change_pct, "Q2", "10-Q")
        for row in _metrics()
    ]
    model = build_company_model_workspace(_identity(), metrics, _valuation())

    implied = build_market_implied_expectations(_identity(), metrics, 100.0, _valuation(), model)

    reverse_dcf = next(row for row in implied.expectations if row.metric == "Reverse DCF: implied five-year FCF growth")
    base_fcf = next(row for row in implied.expectations if row.metric == "Reverse DCF base FCF")
    assert "not verified as TTM" in implied.financial_basis
    assert reverse_dcf.confidence == "Low"
    assert base_fcf.implied_value == 300.0
    assert base_fcf.status == "Annualized screening estimate"
    assert "x 2" in base_fcf.formula
    assert any("trailing-twelve-month" in gap for gap in implied.data_gaps)


def test_market_implied_prefers_sec_ttm_bridge_over_interim_annualization():
    facts = {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _fact_row("2024-09-29", "2025-09-27", 133.0, "10-K", "FY", 2025, "2025-10-31"),
            _fact_row("2024-09-29", "2025-03-29", 53.0, "10-Q", "Q2", 2025, "2025-05-02"),
            _fact_row("2025-09-28", "2026-03-28", 81.0, "10-Q", "Q2", 2026, "2026-05-01"),
        ]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            _fact_row("2024-09-29", "2025-09-27", 12.7, "10-K", "FY", 2025, "2025-10-31"),
            _fact_row("2024-09-29", "2025-03-29", 6.1, "10-Q", "Q2", 2025, "2025-05-02"),
            _fact_row("2025-09-28", "2026-03-28", 5.1, "10-Q", "Q2", 2026, "2026-05-01"),
        ]}},
    }}}
    ttm_metrics = build_financial_metrics(facts)
    metrics = [
        metric for metric in _metrics()
        if metric.name not in {"Operating Cash Flow", "Capital Expenditure"}
    ] + ttm_metrics
    model = build_company_model_workspace(_identity(), metrics, _valuation())

    implied = build_market_implied_expectations(_identity(), metrics, 100.0, _valuation(), model)

    base_fcf = next(row for row in implied.expectations if row.metric == "Reverse DCF base FCF")
    cash_flow = next(row for row in metrics if row.name == "Operating Cash Flow")
    capex = next(row for row in metrics if row.name == "Capital Expenditure")
    assert cash_flow.trailing_twelve_month_value == 161.0
    assert round(capex.trailing_twelve_month_value, 6) == 11.7
    assert round(base_fcf.implied_value, 6) == 149.3
    assert base_fcf.status == "TTM normalized"
    assert "Trailing-twelve-month" in base_fcf.formula
    assert "latest fiscal year plus current YTD" in base_fcf.interpretation
    assert implied.financial_basis == "Trailing-twelve-month filing cash-flow facts"
    assert not any("annualized screening run-rate" in gap for gap in implied.data_gaps)


def test_financial_metric_ttm_can_sum_four_non_overlapping_quarters():
    facts = {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _fact_row("2025-04-01", "2025-06-30", 20.0, "10-Q", "Q2", 2025, "2025-08-01"),
            _fact_row("2025-07-01", "2025-09-30", 25.0, "10-Q", "Q3", 2025, "2025-11-01"),
            _fact_row("2025-10-01", "2025-12-31", 30.0, "10-Q", "Q4", 2025, "2026-02-01"),
            _fact_row("2026-01-01", "2026-03-31", 35.0, "10-Q", "Q1", 2026, "2026-05-01"),
        ]}},
    }}}

    metric = build_financial_metrics(facts)[0]

    assert metric.trailing_twelve_month_value == 110.0
    assert metric.trailing_method == "sum_of_four_standalone_quarters"
    assert metric.trailing_period_start == "2025-04-01"
    assert metric.trailing_period_end == "2026-03-31"


def test_market_implied_user_assumptions_recalculate_and_preserve_provenance():
    model = build_company_model_workspace(_identity(), _metrics(), _valuation())
    default = build_market_implied_expectations(_identity(), _metrics(), 100.0, _valuation(), model)
    edited = build_market_implied_expectations(
        _identity(), _metrics(), 100.0, _valuation(), model,
        assumption_overrides={
            "discount_rate_pct": 12.0,
            "terminal_growth_pct": 2.0,
            "forecast_years": 7,
            "revenue_growth_pct": 3.0,
        },
    )

    default_growth = next(row for row in default.expectations if "FCF growth" in row.metric)
    edited_growth = next(row for row in edited.expectations if "FCF growth" in row.metric)
    assumptions = {item.key: item for item in edited.assumptions}
    assert edited_growth.metric == "Reverse DCF: implied 7-year FCF growth"
    assert edited_growth.implied_value != default_growth.implied_value
    assert assumptions["discount_rate_pct"].provenance == "user_override"
    assert assumptions["forecast_years"].value == 7


def test_market_implied_assumptions_persist_per_ticker(tmp_path: Path):
    store = ResearchStore(tmp_path / "research.db")
    saved = store.save_market_implied_assumptions(
        "aapl", {"discount_rate_pct": 11, "terminal_growth_pct": 2.5, "unknown": 99},
    )

    assert saved == {"discount_rate_pct": 11.0, "terminal_growth_pct": 2.5}
    assert store.latest_market_implied_assumptions("AAPL") == saved
    assert store.clear_market_implied_assumptions("AAPL") is True
    assert store.latest_market_implied_assumptions("AAPL") == {}


def test_earnings_surprise_proxy_separates_surprise_from_revision_follow_through():
    comparison = ExpectationComparison(
        metric="Revenue",
        period_end="2026-03-31",
        expected=950.0,
        actual=1_000.0,
        surprise_pct=(1_000.0 / 950.0 - 1.0) * 100,
        post_event_revision_pct=None,
        interpretation="Revenue exceeded the eligible pre-event estimate.",
        actual_source="SEC Companyfacts",
        estimate_source="FMP historical surprise",
        estimate_as_of="2026-04-29",
        estimate_eligibility="Point-in-time estimate observed before event",
    )
    bridge = ExpectationsBridge(
        status="Available",
        headline="Revenue surprise available",
        comparisons=[comparison],
        event_audits=[ExpectationEventAudit(
            event_id="event-1",
            event_label="Q1 2026 10-Q filed",
            form="10-Q",
            accession="0001",
            filing_date="2026-05-01",
            reporting_period="2026-03-31",
        )],
    )

    proxy = build_earnings_surprise_proxy("TEST", bridge, _metrics())

    assert proxy.status == "Available"
    assert proxy.items[0].event_label == "Q1 2026 10-Q filed"
    assert proxy.items[0].confidence == "High"
    assert proxy.revision_follow_through_available is False
    assert any("revision follow-through" in gap.lower() for gap in proxy.data_gaps)


def test_causal_graph_identifies_exact_weak_connection():
    event = ChangeEvent(
        category="financial_kpi",
        title="Gross margin expanded",
        summary="Gross Profit improved.",
        severity=4,
        direction="positive",
        event_date="2026-05-01",
        source="10-Q",
        citations=[],
        metrics={"Gross Profit": 500},
    )
    citation = Citation("10-Q", "https://www.sec.gov/example", accession="0001", section="MD&A")
    claim = ValidatedClaim(
        "claim-1", "TEST", event.title, event.category, "Validated", True,
        "margin", "positive", metric="Gross Profit", business_driver="Gross margin / mix",
        supporting_quote="Gross margin expanded due to mix.", confidence="High", citation=citation,
    )
    idea = TradeIdea(
        idea_id="idea-1",
        title="Margin expansion",
        direction="Long",
        structure="Equity",
        thesis="Margin expansion can lift earnings.",
        horizon="1-2 quarters",
        catalyst="Unknown",
        variant_perception="Unknown",
        source_events=[event],
        citations=[citation],
        validated_claim_ids=["claim-1"],
        direction_rationale="Validated positive margin claim.",
        driver_analysis=DriverAnalysis("Margin", primary_driver="Gross margin / mix"),
        monitor_items=[],
    )
    model = build_company_model_workspace(_identity(), _metrics(), _valuation())
    implied = build_market_implied_expectations(_identity(), _metrics(), 100.0, _valuation(), model)
    closure = execute_evidence_work_order(
        "TEST", EvidenceWorkOrder("No work", "None"), metrics=_metrics(), **_empty_context(),
    )
    graphs = build_causal_thesis_graphs(
        "TEST", [idea], ClaimValidationResult("TEST", "Available", [claim]),
        model, _valuation(), implied, closure,
    )

    assert len(graphs[0].edges) == 5
    assert graphs[0].status == "Incomplete"
    assert graphs[0].weakest_link == "Catalyst can close the valuation gap"
    assert "dated catalyst" in graphs[0].summary.lower()


def test_all_driver_specific_modes_are_evaluated_and_recommended_from_signal():
    event = ChangeEvent(
        "guidance_shift", "Margin guidance changed", "Management changed margin guidance.",
        4, "negative", "2026-05-01", "8-K",
    )
    closure = execute_evidence_work_order(
        "TEST", EvidenceWorkOrder("No work", "None"), metrics=_metrics(), **_empty_context(),
    )
    suite = build_research_mode_suite(
        "TEST", _metrics(), [event], [], ManagementSourcePackage("TEST", "Unavailable"), closure,
    )

    assert len(suite.modes) == 8
    assert "earnings_review" in suite.recommended_mode_ids
    assert "margin_investigation" in suite.recommended_mode_ids
    assert "management_credibility" in suite.recommended_mode_ids


def test_decision_artifacts_round_trip_without_raw_provider_payloads(tmp_path: Path):
    store = ResearchStore(tmp_path / "research.db")
    closure = execute_evidence_work_order(
        "TEST", EvidenceWorkOrder("No work", "None"), metrics=_metrics(), **_empty_context(),
    )
    model = build_company_model_workspace(_identity(), _metrics(), _valuation())
    implied = build_market_implied_expectations(_identity(), _metrics(), 100.0, _valuation(), model)
    modes = build_research_mode_suite(
        "TEST", _metrics(), [], [], ManagementSourcePackage("TEST", "Unavailable"), closure,
    )
    store.save_decision_artifacts("run-1", "TEST", closure, [], implied, model, modes)

    assert store.latest_decision_artifact("TEST", "evidence_closure")["ticker"] == "TEST"
    assert store.latest_decision_artifact("TEST", "company_model")["status"] in {"Auditable model available", "Partial model"}
    assert len(store.latest_decision_artifact("TEST", "research_modes")["modes"]) == 8
