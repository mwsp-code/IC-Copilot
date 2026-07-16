from equity_research.credit_lens import build_credit_lens
from equity_research.models import CompanyIdentity, FinancialMetric


def _identity(ticker: str = "TEST") -> CompanyIdentity:
    return CompanyIdentity(ticker=ticker, cik="0000000000", name="Test Corp")


def _metric(name: str, value: float, unit: str = "USD") -> FinancialMetric:
    return FinancialMetric(
        name=name,
        value=value,
        unit=unit,
        period_end="2026-03-31",
        form="10-Q",
        source_kind="fixture",
    )


def test_credit_lens_flags_strong_liquidity_and_cash_flow() -> None:
    lens = build_credit_lens(
        _identity(),
        [
            _metric("Cash", 10_000_000_000),
            _metric("Long-term Debt", 2_000_000_000),
            _metric("Revenue", 20_000_000_000),
            _metric("Operating Income", 4_000_000_000),
            _metric("Interest Expense", 400_000_000),
            _metric("Operating Cash Flow", 5_000_000_000),
            _metric("Capital Expenditures", 1_000_000_000),
            _metric("Current Assets", 12_000_000_000),
            _metric("Current Liabilities", 6_000_000_000),
        ],
    )

    assert lens.status == "Available"
    assert lens.risk_level == "Low"
    assert any("Net cash" in item for item in lens.positives)
    assert any("Free cash flow was positive" in item for item in lens.positives)
    assert any(metric.name == "Total debt" and "fixture" in metric.source for metric in lens.metrics)
    assert any(row.area == "Liquidity runway" and "Supported" in row.status for row in lens.credit_bridge)
    assert any(row.area == "Debt maturity and refinancing" for row in lens.credit_bridge)
    market_row = next(row for row in lens.credit_bridge if row.area == "Rating and spread confirmation")
    assert market_row.status == "Unavailable, do not infer"
    assert any("Bond spread" in item for item in market_row.required_evidence)
    assert any("interest_coverage_x" in item for item in lens.monitor_rules)
    assert any("Capital return" in item for item in lens.credit_catalysts)
    assert any("cash is restricted" in item for item in lens.falsification_tests)


def test_credit_lens_flags_stressed_credit_metrics() -> None:
    lens = build_credit_lens(
        _identity(),
        [
            _metric("Cash", 1_000_000_000),
            _metric("Long-term Debt", 12_000_000_000),
            _metric("Current Debt", 4_000_000_000),
            _metric("Revenue", 8_000_000_000),
            _metric("Operating Income", 500_000_000),
            _metric("Interest Expense", 400_000_000),
            _metric("Operating Cash Flow", 300_000_000),
            _metric("Capital Expenditures", 800_000_000),
            _metric("Current Assets", 2_000_000_000),
            _metric("Current Liabilities", 4_000_000_000),
        ],
    )

    assert lens.risk_level == "High"
    assert any("Debt/revenue is elevated" in item for item in lens.risks)
    assert any("Interest coverage is thin" in item for item in lens.risks)
    assert any(metric.name == "Free cash flow" and metric.value < 0 for metric in lens.metrics)
    refinancing_row = next(row for row in lens.credit_bridge if row.area == "Debt maturity and refinancing")
    assert refinancing_row.status == "Refinancing risk needs primary evidence"
    assert any("Debt maturity schedule" in item for item in refinancing_row.missing_evidence)
    assert "covenant" in refinancing_row.next_source.lower()
    coverage_row = next(row for row in lens.credit_bridge if row.area == "Cash-flow debt service")
    assert coverage_row.status == "Coverage pressure"
    assert any("rating downgrade" in item for item in lens.monitor_rules)
    assert any("bank facility amendment" in item for item in lens.credit_catalysts)
    assert any("maturities are long-dated" in item for item in lens.falsification_tests)


def test_credit_lens_reports_sparse_data_gaps() -> None:
    lens = build_credit_lens(_identity(), [_metric("Revenue", 1_000_000_000)])

    assert lens.status == "Unavailable"
    assert lens.risk_level == "Unknown"
    assert "Cash balance is unavailable." in lens.data_gaps
    assert "Current debt and long-term debt are unavailable." in lens.data_gaps
    assert lens.required_evidence
    assert any(row.status.startswith("Missing") for row in lens.credit_bridge)
    assert any("No structured rating" in row.current_evidence for row in lens.credit_bridge)
    assert any("interest_coverage_x must be calculated" in item for item in lens.monitor_rules)
    assert any("missing debt maturity" in item for item in lens.credit_catalysts)
    assert any("not thesis-grade" in item for item in lens.falsification_tests)


def test_credit_bridge_never_fabricates_rating_or_spread_confirmation() -> None:
    lens = build_credit_lens(
        _identity(),
        [
            _metric("Cash", 5_000_000_000),
            _metric("Long-term Debt", 3_000_000_000),
            _metric("Revenue", 10_000_000_000),
            _metric("Operating Income", 1_000_000_000),
            _metric("Interest Expense", 100_000_000),
            _metric("Operating Cash Flow", 1_500_000_000),
            _metric("Capital Expenditures", 500_000_000),
        ],
    )

    market_row = next(row for row in lens.credit_bridge if row.area == "Rating and spread confirmation")
    assert market_row.status == "Unavailable, do not infer"
    assert "No structured rating" in market_row.current_evidence
    assert any("Rating action" in item for item in market_row.missing_evidence)
    assert "cannot be inferred from accounting facts alone" in market_row.credit_implication
