from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from equity_research.providers import (
    CsvConsensusProvider,
    FmpConsensusProvider,
    ProviderError,
    _safe_provider_message,
)


class FixtureFmpProvider(FmpConsensusProvider):
    def __init__(self, fixtures):
        super().__init__(api_key="secret-test-key")
        self.fixtures = fixtures

    def _get(self, endpoint, params):
        key = (endpoint, params.get("period"))
        value = self.fixtures.get(key, self.fixtures.get(endpoint, []))
        if isinstance(value, Exception):
            raise value
        return value


class ConsensusProviderTests(unittest.TestCase):
    def test_fmp_normalizes_all_response_types(self) -> None:
        provider = FixtureFmpProvider({
            "price-target-consensus": [{
                "symbol": "AAPL", "targetHigh": 260, "targetLow": 180,
                "targetConsensus": 225, "targetMedian": 230,
                "analystCount": 42, "lastUpdated": "2026-06-20",
            }],
            ("analyst-estimates", "annual"): [{
                "date": "2027-09-30", "estimatedEpsAvg": 8.5,
                "estimatedRevenueAvg": 500_000_000_000,
                "estimatedEbitdaAvg": 180_000_000_000,
                "numberAnalystEstimatedEps": 35,
            }],
            ("analyst-estimates", "quarter"): [{
                "date": "2026-09-30", "estimatedEpsAvg": 2.1,
            }],
            "grades-consensus": [{
                "strongBuy": 10, "buy": 20, "hold": 8, "sell": 2,
                "strongSell": 1, "consensus": "Buy",
            }],
            "earnings": [{
                "fiscalDateEnding": "2026-03-31", "epsActual": 2.0,
                "epsEstimated": 1.8,
            }],
        })
        package = provider.fetch_package("AAPL", current_price=200)
        self.assertEqual(package.status, "Available")
        self.assertEqual(package.target.target_mean, 225)
        self.assertAlmostEqual(package.target.implied_upside_pct, 12.5)
        self.assertEqual(package.recommendations.consensus_label, "Buy")
        self.assertTrue(any(item.metric == "Revenue" for item in package.estimates))
        self.assertAlmostEqual(package.surprises[0].surprise_pct, 11.111111, places=4)

    def test_fmp_entitlement_errors_are_data_gaps_not_pipeline_errors(self) -> None:
        provider = FixtureFmpProvider({
            "price-target-consensus": ProviderError("HTTP 403: endpoint not included"),
            "grades-consensus": ProviderError("HTTP 429: rate limit"),
            ("analyst-estimates", "annual"): ProviderError("HTTP 403: endpoint not included"),
            "earnings-surprises": ProviderError("HTTP 403: endpoint not included"),
        })
        package = provider.fetch_package("AAPL")
        self.assertEqual(package.status, "Unavailable")
        self.assertTrue(any("rate limit" in gap for gap in package.data_gaps))
        statuses = {row.provider: row for row in package.provider_statuses}
        self.assertEqual(statuses["FMP price targets"].entitlement_status, "entitlement_error")
        self.assertEqual(statuses["FMP recommendations"].entitlement_status, "rate_limited")
        self.assertTrue(any("Fallback:" in gap for gap in package.data_gaps))

    def test_fmp_list_shaped_error_body_is_parsed_without_attribute_error(self) -> None:
        message = _safe_provider_message('[{"message":"Endpoint requires a paid subscription"}]')
        self.assertEqual(message, "Endpoint requires a paid subscription")

    def test_fmp_keeps_annual_estimates_when_quarterly_period_is_not_entitled(self) -> None:
        provider = FixtureFmpProvider({
            ("analyst-estimates", "annual"): [{
                "date": "2027-09-30", "estimatedEpsAvg": 8.5,
                "estimatedRevenueAvg": 500_000_000_000,
            }],
            ("analyst-estimates", "quarter"): ProviderError(
                "HTTP 402: period quarter is not available under the current subscription"
            ),
            "earnings": [],
        })

        package = provider.fetch_package("AAPL")
        statuses = {row.provider: row for row in package.provider_statuses}

        self.assertEqual(package.status, "Partial")
        self.assertTrue(any(item.period_type == "annual" for item in package.estimates))
        self.assertEqual(statuses["FMP analyst estimates annual"].status, "Available")
        self.assertEqual(
            statuses["FMP analyst estimates quarter"].entitlement_status,
            "entitlement_error",
        )
        self.assertIn("Nasdaq estimates", statuses["FMP analyst estimates quarter"].message)

    def test_fmp_normalizes_current_stable_estimate_field_names(self) -> None:
        provider = FixtureFmpProvider({
            ("analyst-estimates", "annual"): [{
                "date": "2027-09-30",
                "epsAvg": 8.5,
                "epsHigh": 9.0,
                "epsLow": 8.0,
                "numAnalystsEps": 35,
                "revenueAvg": 500_000_000_000,
                "numAnalystsRevenue": 30,
                "ebitdaAvg": 180_000_000_000,
                "ebitAvg": 160_000_000_000,
                "netIncomeAvg": 140_000_000_000,
                "sgaExpenseAvg": 30_000_000_000,
            }],
            ("analyst-estimates", "quarter"): [],
        })

        estimates = provider.fetch_estimates("AAPL")
        by_metric = {item.metric: item for item in estimates}

        self.assertEqual(by_metric["EPS"].analyst_count, 35)
        self.assertEqual(by_metric["Revenue"].analyst_count, 30)
        self.assertIn("EBIT", by_metric)
        self.assertIn("SG&A Expense", by_metric)

    def test_fmp_endpoint_schema_drift_is_a_provider_gap_not_network_failure(self) -> None:
        provider = FixtureFmpProvider({
            "price-target-consensus": [{
                "symbol": "AAPL", "targetConsensus": 225, "targetMedian": 230,
            }],
            "grades-consensus": AttributeError("'list' object has no attribute 'get'"),
            ("analyst-estimates", "annual"): [],
            ("analyst-estimates", "quarter"): [],
            "earnings-surprises": [],
        })
        package = provider.fetch_package("AAPL", current_price=200)

        self.assertEqual(package.status, "Partial")
        self.assertEqual(package.target.target_mean, 225)
        self.assertTrue(any("malformed response" in gap for gap in package.data_gaps))
        self.assertFalse(any("network_error" in gap for gap in package.data_gaps))

    def test_missing_target_fields_do_not_create_false_available_data(self) -> None:
        provider = FixtureFmpProvider({"price-target-consensus": [{"symbol": "AAPL"}]})
        self.assertIsNone(provider.fetch_targets("AAPL"))

    def test_csv_provider_matches_interface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            self._write_csv(directory / "targets.csv", [{
                "ticker": "BABA", "as_of": "2026-06-20", "currency": "USD",
                "target_mean": "150", "target_median": "145", "target_high": "190",
                "target_low": "100", "analyst_count": "30",
            }])
            self._write_csv(directory / "estimates.csv", [{
                "ticker": "BABA", "as_of": "2026-06-20", "metric": "EPS",
                "period_end": "2027-03-31", "period_type": "annual", "average": "10",
                "currency": "USD", "analyst_count": "20",
            }])
            provider = CsvConsensusProvider(directory)
            package = provider.fetch_package("BABA", 120)
            self.assertEqual(package.status, "Available")
            self.assertEqual(package.target.analyst_count, 30)
            self.assertAlmostEqual(package.target.implied_upside_pct, 25)
            self.assertEqual(package.estimates[0].metric, "EPS")

    @staticmethod
    def _write_csv(path: Path, rows: list[dict]) -> None:
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


if __name__ == "__main__":
    unittest.main()
