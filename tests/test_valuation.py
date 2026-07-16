from __future__ import annotations

import unittest
from datetime import date

from equity_research.models import CompanyIdentity, ConsensusPackage, EstimatePoint, FinancialMetric, TargetConsensus
from equity_research.valuation import build_valuation, classify_template


class ValuationTests(unittest.TestCase):
    def test_non_financial_uses_forward_pe(self) -> None:
        result = build_valuation(_identity("AAPL"), [], _consensus("AAPL", [ _estimate("AAPL", "EPS", 10) ]), 180)
        self.assertEqual(result.template, "Non-financial")
        self.assertEqual(result.status, "Available")
        self.assertIn("forward P/E", result.cases[1].method)

    def test_non_financial_bridge_explains_revenue_method_when_available(self) -> None:
        metrics = [
            _metric("Shares", 100, "shares"),
            _metric("Cash", 100, "USD"),
            _metric("Long-term Debt", 200, "USD"),
        ]
        result = build_valuation(
            _identity("TEST"),
            metrics,
            _consensus("TEST", [_estimate("TEST", "Revenue", 1_000)]),
            10,
        )

        self.assertEqual(result.status, "Available")
        self.assertIn("EV/revenue", result.cases[1].method)
        self.assertTrue(any(step.metric == "Revenue" and "EV / revenue" in step.formula for step in result.bridge))

    def test_bank_uses_book_roe_and_pe(self) -> None:
        identity = CompanyIdentity("TESTBANK", "1", "Test Bank", sic="6021", sic_description="National bank")
        metrics = [_metric("Shares", 100, "shares"), _metric("Stockholders' Equity", 2_000, "USD"), _metric("Net Income", 240, "USD")]
        result = build_valuation(identity, metrics, _consensus("TESTBANK", [_estimate("TESTBANK", "EPS", 2)]), 20)
        self.assertEqual(result.template, "Bank")
        self.assertIn("ROE-supported P/B", result.cases[1].method)

    def test_insurer_template_is_distinct(self) -> None:
        identity = CompanyIdentity("AIG", "2", "AIG", sic="6331", sic_description="Fire insurance")
        metrics = [_metric("Shares", 100, "shares"), _metric("Stockholders' Equity", 3_000, "USD"), _metric("Net Income", 300, "USD")]
        result = build_valuation(identity, metrics, _consensus("AIG", [_estimate("AIG", "EPS", 6)]), 70)
        self.assertEqual(classify_template(identity), "Insurer")
        self.assertEqual(result.status, "Available")

    def test_reit_requires_ffo_or_nav_and_never_substitutes_eps(self) -> None:
        identity = CompanyIdentity("O", "3", "Realty Income", sic="6798", sic_description="REIT")
        insufficient = build_valuation(identity, [], _consensus("O", [_estimate("O", "EPS", 2)]), 55)
        self.assertEqual(insufficient.status, "Insufficient data")
        available = build_valuation(identity, [], _consensus("O", [_estimate("O", "FFO", 4)]), 55)
        self.assertEqual(available.status, "Available")
        self.assertIn("price-to-FFO", available.cases[1].method)

    def test_baba_does_not_mix_cny_company_facts_with_usd_adr_price(self) -> None:
        identity = _identity("BABA")
        metrics = [
            _metric("Shares", 2_500, "shares"), _metric("Revenue", 1_000, "CNY"),
            _metric("Cash", 200, "CNY"), _metric("Long-term Debt", 100, "CNY"),
            _metric("Operating Cash Flow", 150, "CNY"), _metric("Capital Expenditure", 50, "CNY"),
        ]
        estimates = [_estimate("BABA", "EPS", 10, "USD"), _estimate("BABA", "Revenue", 1_100, "CNY")]
        result = build_valuation(identity, metrics, _consensus("BABA", estimates), 120)
        self.assertEqual(result.status, "Available")
        self.assertEqual(result.cases[1].method, "forward P/E")
        self.assertTrue(any("8 ordinary shares" in note for note in result.normalization_notes))
        self.assertTrue(any("currency normalization" in gap for gap in result.missing_data))


def _identity(ticker: str) -> CompanyIdentity:
    return CompanyIdentity(ticker, "1", f"{ticker} Inc.", sic="7372")


def _estimate(ticker: str, metric: str, value: float, currency: str = "USD") -> EstimatePoint:
    period = date(date.today().year + 1, 12, 31).isoformat()
    return EstimatePoint(ticker, date.today().isoformat(), metric, period, "annual", value, currency=currency)


def _consensus(ticker: str, estimates: list[EstimatePoint]) -> ConsensusPackage:
    return ConsensusPackage(
        ticker, "Test", "Available",
        target=TargetConsensus(ticker, date.today().isoformat(), currency="USD", target_mean=150),
        estimates=estimates,
    )


def _metric(name: str, value: float, unit: str) -> FinancialMetric:
    return FinancialMetric(name, value, unit, date.today().isoformat())


if __name__ == "__main__":
    unittest.main()
