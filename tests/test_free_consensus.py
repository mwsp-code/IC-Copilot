from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from equity_research.idea_engine import generate_trade_ideas
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyIdentity,
    ConsensusPackage,
    ProviderObservation,
    TargetConsensus,
)
from equity_research.providers import (
    AlphaVantageConsensusProvider,
    CachedConsensusProvider,
    ConsensusAdapter,
    FinnhubConsensusProvider,
    MultiSourceConsensusProvider,
    NasdaqConsensusProvider,
    PriceReaction,
    ProviderError,
    TradingViewConsensusProvider,
    YahooConsensusProvider,
)
from equity_research.research_store import ResearchStore
from equity_research.rigor import apply_evidence_score_caps, build_evidence_ledger


class FixtureAlphaProvider(AlphaVantageConsensusProvider):
    def __init__(self, payload):
        super().__init__(api_key="test-key")
        self.payload = payload

    def _get(self, ticker):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FixtureFinnhubProvider(FinnhubConsensusProvider):
    def __init__(self, payload):
        super().__init__(api_key="test-key")
        self.payload = payload

    def _get(self, ticker):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FixtureNasdaqProvider(NasdaqConsensusProvider):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    def _get(self, ticker):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FixtureTradingViewProvider(TradingViewConsensusProvider):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    def _post(self, ticker):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class CountingProvider(ConsensusAdapter):
    provider_name = "Counting"
    official_for_conviction = True

    def __init__(self):
        super().__init__()
        self.api_key = "must-never-be-persisted"
        self.calls = 0

    def fetch_package(self, ticker, current_price=None):
        self.calls += 1
        target = TargetConsensus(
            ticker, "2026-06-24", target_mean=200, current_price=current_price,
            source=self.provider_name, observed_at="2026-06-24T01:00:00+00:00",
        )
        return ConsensusPackage(
            ticker, self.provider_name, "Available", target=target,
            observations=[ProviderObservation(
                ticker, self.provider_name, "target_mean",
                "2026-06-24T01:00:00+00:00", None, value_numeric=200,
                provenance="Fixture", official=True,
            )],
        )


class FreeConsensusProviderTests(unittest.TestCase):
    def test_alpha_vantage_preserves_aggregate_target_semantics(self) -> None:
        package = FixtureAlphaProvider({
            "Symbol": "AAPL", "Currency": "USD", "AnalystTargetPrice": "250.50",
            "AnalystRatingStrongBuy": "10", "AnalystRatingBuy": "20",
            "AnalystRatingHold": "8", "AnalystRatingSell": "1",
            "AnalystRatingStrongSell": "0",
        }).fetch_package("AAPL", 200)
        self.assertEqual(package.status, "Available")
        self.assertEqual(package.target.target_kind, "aggregate")
        self.assertEqual(package.target.target_label, "Aggregate Target")
        self.assertEqual(package.target.target_aggregate, 250.5)
        self.assertIsNone(package.target.target_mean)
        fields = {item.field for item in package.observations}
        self.assertIn("target_aggregate", fields)
        self.assertNotIn("target_mean", fields)
        self.assertIsNone(package.target.source_as_of)
        self.assertIsNone(package.target.freshness_days)

    def test_alpha_rate_limit_and_finnhub_entitlement_fail_softly(self) -> None:
        alpha = FixtureAlphaProvider(ProviderError("rate limit: 25 requests per day"))
        finnhub = FixtureFinnhubProvider(ProviderError("HTTP 403: unavailable"))
        self.assertEqual(alpha.fetch_package("AAPL").status, "Unavailable")
        self.assertEqual(finnhub.fetch_package("AAPL").status, "Unavailable")

    def test_consensus_provider_status_redacts_vendor_echoed_keys(self) -> None:
        secret = "secret-alpha-key"
        provider = FixtureAlphaProvider(ProviderError(
            f"We have detected your API key as {secret}; retry url apikey={secret}&symbol=AAPL."
        ))
        provider.api_key = secret
        package = provider.fetch_package("AAPL")
        serialized = str(package)
        self.assertNotIn(secret, serialized)
        self.assertIn("[redacted]", serialized)

    def test_finnhub_normalizes_recommendation_validation(self) -> None:
        package = FixtureFinnhubProvider([
            {
                "period": "2026-06-01", "strongBuy": 12, "buy": 18,
                "hold": 7, "sell": 2, "strongSell": 1,
            },
            {
                "period": "2026-05-01", "strongBuy": 10, "buy": 15,
                "hold": 9, "sell": 3, "strongSell": 1,
            },
        ]).fetch_package("AAPL")
        self.assertEqual(package.status, "Available")
        self.assertEqual(package.recommendations.source_as_of, "2026-06-01")
        self.assertEqual(package.recommendations.consensus_label, "Buy")
        trend_periods = {
            item.source_as_of for item in package.observations
            if item.field.startswith("recommendation_trend_")
        }
        self.assertEqual(trend_periods, {"2026-06-01", "2026-05-01"})

    def test_nasdaq_aapl_and_baba_use_month_precision_without_inventing_currency(self) -> None:
        for ticker, annual_month in (("AAPL", "Sep 2027"), ("BABA", "Mar 2027")):
            payload = {"data": {
                "quarterlyForecast": {"rows": [{
                    "fiscalEnd": "Jun 2026", "consensusEPSForecast": 2.1,
                    "highEPSForecast": 2.4, "lowEPSForecast": 1.8,
                    "noOfEstimates": 4, "up": 1, "down": 0,
                }]},
                "yearlyForecast": {"rows": [{
                    "fiscalEnd": annual_month, "consensusEPSForecast": 8.2,
                    "highEPSForecast": 9.0, "lowEPSForecast": 7.1,
                    "noOfEstimates": 6, "up": 2, "down": 1,
                }]},
            }}
            package = FixtureNasdaqProvider(payload).fetch_package(ticker)
            self.assertEqual(package.status, "Partial - unofficial only")
            self.assertTrue(package.unofficial_only)
            self.assertTrue(all(item.period_precision == "month" for item in package.estimates))
            self.assertTrue(all(item.currency == "Unknown" for item in package.estimates))
            self.assertTrue(all(item.source_as_of is None for item in package.estimates))
            self.assertEqual(package.estimates[0].period_end, "2026-06-30")

    def test_nasdaq_malformed_payload_is_unavailable(self) -> None:
        package = FixtureNasdaqProvider({"data": {"yearlyForecast": {"rows": [{}]}}}).fetch_package("AAPL")
        self.assertEqual(package.status, "Unavailable")

    def test_tradingview_keeps_distribution_separate_and_analyst_count_unknown(self) -> None:
        for ticker, exchange in (("AAPL", "NASDAQ"), ("BABA", "NYSE")):
            package = FixtureTradingViewProvider({
                "data": [{"s": f"{exchange}:{ticker}", "d": [
                    ticker, 100.0, 180.0, 80.0, 140.0, 0.2, "USD",
                ]}],
            }).fetch_package(ticker, 105)
            self.assertEqual(package.status, "Partial - unofficial only")
            self.assertEqual(package.target.target_kind, "median")
            self.assertEqual(package.target.target_median, 140)
            self.assertIsNone(package.target.target_mean)
            self.assertIsNone(package.target.analyst_count)
            self.assertIsNone(package.target.source_as_of)

    def test_multi_source_never_compares_aggregate_as_median(self) -> None:
        alpha = FixtureAlphaProvider({
            "Symbol": "AAPL", "Currency": "USD", "AnalystTargetPrice": "250",
            "AnalystRatingBuy": "10",
        })
        trading = FixtureTradingViewProvider({
            "data": [{"s": "NASDAQ:AAPL", "d": ["AAPL", 200, 300, 180, 240, 0.1, "USD"]}],
        })
        provider = MultiSourceConsensusProvider([alpha, trading])
        package = provider.fetch_package("AAPL", 200)
        self.assertEqual(package.status, "Available")
        self.assertEqual(package.target.target_kind, "aggregate")
        self.assertEqual(len(package.provider_targets), 2)
        comparisons = {item.field: item.values for item in package.comparisons}
        self.assertEqual(set(comparisons["target_aggregate"]), {"Alpha Vantage"})
        self.assertEqual(set(comparisons["target_median"]), {"TradingView (unofficial)"})

    def test_unofficial_only_is_partial_and_cannot_create_high_conviction(self) -> None:
        nasdaq = FixtureNasdaqProvider({"data": {"yearlyForecast": {"rows": [{
            "fiscalEnd": "Sep 2027", "consensusEPSForecast": 8.2,
        }]}}})
        trading = FixtureTradingViewProvider({
            "data": [{"s": "NASDAQ:AAPL", "d": ["AAPL", 200, 300, 180, 240, 0.1, "USD"]}],
        })
        provider = MultiSourceConsensusProvider([nasdaq, trading])
        package = provider.fetch_package("AAPL", 200)
        self.assertEqual(package.status, "Partial - unofficial only")
        self.assertTrue(package.unofficial_only)
        self.assertFalse(provider.official_for_conviction)

        event = ChangeEvent(
            "margin", "Margin expanded", "Margin improved.", 5, "positive",
            "2026-06-01", "10-Q", [Citation(
                "10-Q", "https://www.sec.gov/example", section="MD&A",
                snippet="Gross margin expanded.", source_tier=1,
            )],
        )
        ideas = generate_trade_ideas(
            CompanyIdentity("AAPL", "1", "Apple Inc."), [event],
            PriceReaction("AAPL", "2026-06-01", 100, 101, 1, "Fixture"),
            provider,
        )
        ledger = build_evidence_ledger("AAPL", ideas, [event])
        apply_evidence_score_caps(ideas, ledger)
        self.assertNotEqual(
            ideas[0].score.score_cap_reason,
            "Official consensus support is unavailable; unofficial data cannot establish high conviction.",
        )
        self.assertEqual(ideas[0].market_capture.category, "Unknown")

    def test_successful_package_is_cached_daily_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            source = CountingProvider()
            provider = CachedConsensusProvider(source, store)
            first = provider.fetch_package("AAPL", 100)
            second = provider.fetch_package("AAPL", 101)
            self.assertEqual(source.calls, 1)
            self.assertEqual(first.target.target_mean, second.target.target_mean)
            with store.connect() as db:
                payload = db.execute(
                    "SELECT payload_json FROM provider_package_cache"
                ).fetchone()[0]
            self.assertNotIn(source.api_key, payload)

    def test_yahoo_401_is_unavailable_and_does_not_raise(self) -> None:
        error = HTTPError("https://query1.finance.yahoo.com", 401, "Unauthorized", None, None)
        with patch("equity_research.providers.urlopen", side_effect=error):
            package = YahooConsensusProvider().fetch_package("AAPL")
        self.assertEqual(package.status, "Unavailable")

    def test_consensus_network_refusal_is_classified_without_exposing_key(self) -> None:
        package = FixtureAlphaProvider(
            ProviderError(
                "Alpha Vantage request failed: <urlopen error [WinError 10061] "
                "No connection could be made because the target machine actively refused it>; "
                "apikey=test-key"
            )
        ).fetch_package("AAPL")

        self.assertEqual(package.status, "Unavailable")
        self.assertEqual(package.provider_statuses[0].entitlement_status, "network_error")
        self.assertIn("Network diagnosis: connection_refused", package.data_gaps[0])
        self.assertNotIn("test-key", package.data_gaps[0])


if __name__ == "__main__":
    unittest.main()
