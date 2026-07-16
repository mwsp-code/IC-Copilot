from __future__ import annotations

import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from equity_research.analysis import (
    build_periodic_inline_xbrl_financial_metrics,
    build_registration_financial_metrics,
)
from equity_research.coverage import assess_financial_coverage, resolve_entity
from equity_research.idea_engine import (
    build_payoff_model,
    calculate_pair_return,
    expected_value,
    finalize_idea_research,
    generate_trade_ideas,
)
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyIdentity,
    FilingRecord,
    FinancialCoverage,
    PeerUniverse,
    PriceProviderStatus,
    ScoreBreakdown,
    TradeIdea,
    ValuationBridgeStep,
    ValuationCase,
    ValuationResult,
)
from equity_research.peers import FINANCIAL_METRICS, peer_universe_for
from equity_research.providers import ConsensusAdapter, PriceReaction, StooqPriceClient, _DailyRowsResult
from equity_research.research_store import ResearchStore
from equity_research.rigor import build_calibration_report, build_evidence_ledger


class _OfficialConsensus(ConsensusAdapter):
    official_for_conviction = True

    def revision_since(self, ticker, event_date):
        return 0.0


class RigorousResearchTests(unittest.TestCase):
    def test_spcx_and_spxc_resolve_as_distinct_entities(self) -> None:
        filings = [_filing("S-1", "2026-06-01")]
        spcx = resolve_entity(
            CompanyIdentity("SPCX", "0001181412", "Space Exploration Technologies Corp"),
            {"exchanges": ["Nasdaq"]},
            filings,
        )
        spxc = resolve_entity(
            CompanyIdentity("SPXC", "0000088205", "SPX Technologies, Inc."),
            {"exchanges": ["NYSE"]},
            [_filing("10-K", "2026-02-20")],
        )
        self.assertNotEqual(spcx.cik, spxc.cik)
        self.assertEqual(spcx.similar_tickers, ["SPXC"])
        self.assertIn("Registration-stage", spcx.listing_status)
        self.assertIn("SPCX", spxc.similar_tickers)

    def test_fee_only_companyfacts_explains_unmapped_coverage(self) -> None:
        facts = {"facts": {"ffd": {"NetFeeAmt": {"units": {"USD": []}}}}}
        coverage = assess_financial_coverage(
            [], facts, [], [_filing("424B4", "2026-06-20")],
        )
        self.assertEqual(coverage.status, "facts_unmapped")
        self.assertIn("NetFeeAmt", coverage.concepts_found)

    def test_registration_inline_xbrl_extracts_only_tagged_facts(self) -> None:
        filing = _filing("S-1", "2026-06-20")
        html = """
        <html><body>
          <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          <xbrli:context id="fy24"><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:context id="fy25"><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <ix:nonFraction name="us-gaap:Revenues" contextRef="fy24" unitRef="usd">100</ix:nonFraction>
          <ix:nonFraction name="us-gaap:Revenues" contextRef="fy25" unitRef="usd">120</ix:nonFraction>
          <table><tr><td>Untagged revenue 9999</td></tr></table>
        </body></html>
        """
        metrics = build_registration_financial_metrics(html, filing)
        revenue = next(item for item in metrics if item.name == "Revenue")
        self.assertEqual(revenue.value, 120)
        self.assertEqual(revenue.previous_value, 100)
        self.assertAlmostEqual(revenue.yoy_change_pct, 20.0)
        self.assertEqual(revenue.source_kind, "registration_inline_xbrl")
        self.assertEqual(revenue.accession, filing.accession)

    def test_periodic_inline_xbrl_prefers_reporting_currency_annual_consolidated_facts(self) -> None:
        filing = _filing("20-F", "2026-04-10")
        html = """
        <html><body>
          <xbrli:unit id="twd"><xbrli:measure>iso4217:TWD</xbrli:measure></xbrli:unit>
          <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          <xbrli:context id="fy24"><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:context id="fy25"><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:context id="q425"><xbrli:period><xbrli:startDate>2025-10-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <xbrli:context id="segment25"><xbrli:entity><xbrli:segment><xbrldi:explicitMember dimension="fixture:SegmentAxis">fixture:FoundryMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
          <ix:nonFraction name="ifrs-full:Revenue" contextRef="fy24" unitRef="twd">100</ix:nonFraction>
          <ix:nonFraction name="ifrs-full:GrossProfit" contextRef="fy24" unitRef="twd">50</ix:nonFraction>
          <ix:nonFraction name="ifrs-full:Revenue" contextRef="fy25" unitRef="twd">120</ix:nonFraction>
          <ix:nonFraction name="ifrs-full:GrossProfit" contextRef="fy25" unitRef="twd">60</ix:nonFraction>
          <ix:nonFraction name="ifrs-full:Revenue" contextRef="q425" unitRef="twd">30</ix:nonFraction>
          <ix:nonFraction name="ifrs-full:Revenue" contextRef="segment25" unitRef="twd">999</ix:nonFraction>
          <ix:nonFraction name="ifrs-full:Revenue" contextRef="fy25" unitRef="usd">4</ix:nonFraction>
        </body></html>
        """

        metrics = build_periodic_inline_xbrl_financial_metrics(html, filing, preferred_currency="TWD")
        by_name = {metric.name: metric for metric in metrics}

        self.assertEqual(by_name["Revenue"].value, 120)
        self.assertEqual(by_name["Revenue"].previous_value, 100)
        self.assertEqual(by_name["Revenue"].unit, "TWD")
        self.assertEqual(by_name["Gross Profit"].value, 60)
        self.assertAlmostEqual(by_name["Gross Margin"].value, 50.0)
        self.assertEqual(by_name["Revenue"].source_kind, "periodic_inline_xbrl")

    def test_gs_has_financial_sector_peer_universe(self) -> None:
        universe = peer_universe_for("GS")
        self.assertEqual(universe.status, "Configured")
        self.assertEqual(
            [peer.ticker for peer in universe.peers],
            ["MS", "JPM", "BAC", "C", "JEF"],
        )
        self.assertEqual(universe.key_metrics, FINANCIAL_METRICS)

    def test_stooq_html_challenge_is_provider_blocked_not_unsupported_symbol(self) -> None:
        class HtmlResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b"<!doctype html><html><body>This site requires JavaScript to verify your browser.</body></html>"

        with patch("equity_research.providers.urlopen", return_value=HtmlResponse()):
            client = StooqPriceClient(enable_yahoo_price=False)
            result = client._fetch_stooq_rows("MSFT")
        self.assertEqual(result.status, "provider_blocked")
        self.assertIn("browser-verification", result.message)

    def test_tiingo_keyed_prices_are_used_before_free_fallbacks_and_redact_token(self) -> None:
        class JsonResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'[{"date":"2026-01-02T00:00:00.000Z","adjClose":100.0,"adjVolume":1000},{"date":"2026-01-05T00:00:00.000Z","adjClose":105.0,"adjVolume":1200}]'

        with patch("equity_research.providers.urlopen", return_value=JsonResponse()) as mocked_urlopen:
            client = StooqPriceClient(tiingo_key="secret-tiingo-token", enable_yahoo_price=False)
            result = client._fetch_daily_rows_result("AAPL")

        self.assertEqual(result.provider, "Tiingo EOD prices")
        self.assertEqual(result.status, "available")
        self.assertTrue(result.adjusted)
        self.assertEqual(result.rows[-1]["close"], 105.0)
        self.assertNotIn("secret-tiingo-token", result.source_url or "")
        request_url = mocked_urlopen.call_args.args[0].full_url
        self.assertIn("startDate=", request_url)
        self.assertIn("endDate=", request_url)
        self.assertIn("resampleFreq=daily", request_url)

    def test_tiingo_latest_quote_only_is_insufficient_history(self) -> None:
        class JsonResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'[{"date":"2026-01-05T00:00:00.000Z","adjClose":105.0,"adjVolume":1200}]'

        with patch("equity_research.providers.urlopen", return_value=JsonResponse()):
            client = StooqPriceClient(tiingo_key="secret-tiingo-token", enable_yahoo_price=False)
            result = client._fetch_tiingo_rows("AAPL")

        self.assertEqual(result.status, "insufficient_history")
        self.assertEqual(result.rows, [])

    def test_csv_fallback_produces_event_window_when_stooq_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            csv_dir = Path(temporary)
            rows = ["Date,Close,Volume"]
            for index, trading_day in enumerate(_trading_dates(date(2026, 1, 2), 40)):
                rows.append(f"{trading_day.isoformat()},{100 + index},1000")
            (csv_dir / "MSFT.csv").write_text("\n".join(rows), encoding="utf-8")

            class CsvFallbackClient(StooqPriceClient):
                def _fetch_stooq_rows(self, ticker):
                    return _DailyRowsResult("Stooq daily prices", "provider_blocked", "HTML challenge", [])

                def _fetch_alpha_vantage_rows(self, ticker):
                    return _DailyRowsResult("Alpha Vantage daily prices", "disabled", "No key", [])

                def _fetch_yahoo_rows(self, ticker):
                    return _DailyRowsResult("Yahoo chart prices (unofficial)", "disabled", "Disabled", [])

            client = CsvFallbackClient(csv_price_dir=csv_dir, enable_yahoo_price=False, tiingo_key="", eodhd_key="")
            event_day = _trading_dates(date(2026, 1, 2), 40)[10].isoformat()
            reaction = client.event_window_reaction("MSFT", "event", event_day, f"{event_day}T15:00:00")
        self.assertEqual(reaction.source, "CSV daily prices")
        self.assertEqual(reaction.status, "available")
        self.assertIsNotNone(reaction.raw_returns["1d"])

    def test_all_no_data_price_providers_become_unsupported_symbol(self) -> None:
        class EmptyClient(StooqPriceClient):
            def _fetch_stooq_rows(self, ticker):
                return _DailyRowsResult("Stooq daily prices", "no_data", "No rows", [])

            def _fetch_alpha_vantage_rows(self, ticker):
                return _DailyRowsResult("Alpha Vantage daily prices", "disabled", "No key", [])

            def _fetch_csv_rows(self, ticker):
                return _DailyRowsResult("CSV daily prices", "no_data", "No file", [])

            def _fetch_yahoo_rows(self, ticker):
                return _DailyRowsResult("Yahoo chart prices (unofficial)", "no_data", "No rows", [], official=False)

        reaction = EmptyClient(tiingo_key="", eodhd_key="").event_window_reaction("BADTICKER", "event", "2026-01-01")
        self.assertEqual(reaction.status, "unsupported_symbol")

    def test_eodhd_free_eod_is_used_and_secret_is_not_retained_in_source_url(self) -> None:
        class JsonResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'[{"date":"2026-01-02","close":100.0,"adjusted_close":99.0,"volume":1000},{"date":"2026-01-05","close":106.0,"adjusted_close":105.0,"volume":1200}]'

        with patch("equity_research.providers.urlopen", return_value=JsonResponse()) as mocked_urlopen:
            client = StooqPriceClient(tiingo_key="", eodhd_key="secret-eodhd-token", enable_yahoo_price=False)
            result = client._fetch_eodhd_rows("AAPL")

        self.assertEqual(result.status, "available")
        self.assertEqual(result.provider, "EODHD EOD prices")
        self.assertEqual(result.rows[-1]["close"], 105.0)
        self.assertTrue(result.adjusted)
        self.assertEqual(result.source_url, "https://eodhd.com/api/eod/AAPL.US")
        self.assertNotIn("secret-eodhd-token", result.source_url or "")
        self.assertIn("api_token=secret-eodhd-token", mocked_urlopen.call_args.args[0].full_url)

    def test_recent_market_context_calculates_return_volume_and_drawdown_without_claiming_causality(self) -> None:
        client = StooqPriceClient(tiingo_key="", eodhd_key="")
        dates = _trading_dates(date(2025, 1, 2), 260)
        stock = _price_rows(dates, 100.0, 0.12)
        market = _price_rows(dates, 200.0, 0.05)
        client._rows_cache = {"TEST": stock, "SPY": market}
        client._result_cache = {
            "TEST": _DailyRowsResult("EODHD EOD prices", "available", "fixture", stock, adjusted=True),
            "SPY": _DailyRowsResult("EODHD EOD prices", "available", "fixture", market, adjusted=True),
        }

        context = client.recent_market_context("TEST")

        self.assertEqual(context.status, "Available")
        self.assertEqual(context.source, "EODHD EOD prices")
        self.assertEqual(len(context.windows), 5)
        self.assertIsNotNone(context.windows[2].relative_return_pct)
        self.assertIn("price context", context.summary.lower())

    def test_eodhd_per_run_call_budget_preserves_fallback_capacity(self) -> None:
        client = StooqPriceClient(tiingo_key="", eodhd_key="configured", eodhd_max_calls=0)

        result = client._fetch_eodhd_rows("AAPL")

        self.assertEqual(result.status, "local_budget_exhausted")
        self.assertIn("fallback", result.message.lower())

    def test_event_windows_use_prior_close_and_fixed_sessions(self) -> None:
        client = StooqPriceClient()
        dates = _trading_dates(date(2024, 1, 2), 340)
        stock = _price_rows(dates, 100.0, 0.08)
        market = _price_rows(dates, 200.0, 0.05)
        sector = _price_rows(dates, 150.0, 0.06)
        event_index = 300
        stock[event_index - 1]["close"] = 100.0
        stock[event_index]["close"] = 102.0
        stock[event_index + 4]["close"] = 105.0
        stock[event_index + 19]["close"] = 110.0
        client._rows_cache = {"TEST": stock, "SPY": market, "XLF": sector}
        event_day = dates[event_index].isoformat()
        reaction = client.event_window_reaction(
            "TEST", "event-1", event_day, f"{event_day}T15:30:00", "SPY", "XLF",
        )
        self.assertEqual(reaction.anchor_date, event_day)
        self.assertAlmostEqual(reaction.raw_returns["1d"], 2.0)
        self.assertAlmostEqual(reaction.raw_returns["5d"], 5.0)
        self.assertAlmostEqual(reaction.raw_returns["20d"], 10.0)
        self.assertIsNotNone(reaction.beta)
        self.assertFalse(reaction.corporate_action_adjusted)

    def test_event_window_reuses_same_ticker_date_benchmark_calculation(self) -> None:
        class CountingClient(StooqPriceClient):
            def __init__(self):
                super().__init__()
                self.beta_calls = 0

            def _cached_strict_pre_event_beta(self, *args, **kwargs):
                self.beta_calls += 1
                return super()._cached_strict_pre_event_beta(*args, **kwargs)

        client = CountingClient()
        dates = _trading_dates(date(2024, 1, 2), 340)
        rows = _price_rows(dates, 100.0, 0.08)
        market = _price_rows(dates, 200.0, 0.05)
        client._rows_cache = {"TEST": rows, "SPY": market, "XLF": market}
        event_day = dates[300].isoformat()
        first = client.event_window_reaction("TEST", "event-1", event_day, f"{event_day}T15:30:00", "SPY", "XLF")
        second = client.event_window_reaction("TEST", "event-2", event_day, f"{event_day}T15:30:00", "SPY", "XLF")
        self.assertEqual(first.raw_returns, second.raw_returns)
        self.assertEqual(second.event_id, "event-2")
        self.assertEqual(client.beta_calls, 1)

    def test_after_close_and_missing_timestamps_move_anchor_forward(self) -> None:
        client = StooqPriceClient()
        dates = _trading_dates(date(2026, 1, 2), 40)
        rows = _price_rows(dates, 100.0, 0.1)
        client._rows_cache = {"TEST": rows, "SPY": rows, "XLF": rows}
        event_index = 20
        event_day = dates[event_index].isoformat()
        after_close = client.event_window_reaction(
            "TEST", "late", event_day, f"{event_day}T16:30:00", "SPY", "XLF",
        )
        missing_time = client.event_window_reaction(
            "TEST", "unknown", event_day, None, "SPY", "XLF",
        )
        self.assertGreater(after_close.anchor_date, event_day)
        self.assertEqual(missing_time.anchor_date, after_close.anchor_date)
        self.assertEqual(missing_time.confidence, "Medium")

    def test_partial_event_windows_are_pending_not_failed(self) -> None:
        client = StooqPriceClient()
        dates = _trading_dates(date(2026, 1, 2), 8)
        rows = _price_rows(dates, 100.0, 0.1)
        client._rows_cache = {"TEST": rows, "SPY": rows}
        event_day = dates[5].isoformat()
        reaction = client.event_window_reaction("TEST", "recent", event_day, f"{event_day}T15:30:00")
        self.assertEqual(reaction.status, "window_pending")
        self.assertIsNotNone(reaction.raw_returns["1d"])
        self.assertIsNone(reaction.raw_returns["5d"])

    def test_payoffs_use_entry_exit_and_direction_not_quality_score(self) -> None:
        valuation = _valuation()
        idea = _idea("Long")
        first = build_payoff_model(idea, valuation, 100)
        idea.score.total = 1
        second = build_payoff_model(idea, valuation, 100)
        self.assertEqual(
            [item.net_return_pct for item in first.scenarios],
            [item.net_return_pct for item in second.scenarios],
        )
        self.assertAlmostEqual(first.scenarios[0].net_return_pct, -20.1)
        self.assertAlmostEqual(first.scenarios[2].net_return_pct, 29.9)
        self.assertFalse(first.rank_eligible)

    def test_short_requires_borrow_cost_and_uses_correct_sign(self) -> None:
        idea = _idea("Short")
        default = build_payoff_model(idea, _valuation(), 100)
        self.assertIsNotNone(expected_value(default.scenarios))
        self.assertEqual(default.borrow_cost_pct, 1.0)
        self.assertTrue(any(item.field == "borrow_cost_pct" for item in default.assumption_provenance))
        complete = build_payoff_model(idea, _valuation(), 100, borrow_cost_pct=3.0)
        self.assertAlmostEqual(complete.scenarios[0].net_return_pct, 16.9)
        self.assertAlmostEqual(complete.scenarios[2].net_return_pct, -33.1)
        self.assertAlmostEqual(calculate_pair_return(12, 5, 0.8, 0.2), 7.8)

    def test_user_scenario_probabilities_are_normalized_and_unranked(self) -> None:
        idea = _idea("Long")
        payoff = build_payoff_model(
            idea,
            _valuation(),
            100,
            scenario_probabilities={"Bear": 20, "Base": 50, "Bull": 30},
        )

        self.assertEqual(payoff.probability_provenance.source, "user_assigned")
        self.assertEqual(payoff.probability_provenance.status, "Uncalibrated")
        self.assertAlmostEqual(sum(item.probability for item in payoff.scenarios), 1.0)
        self.assertEqual([round(item.probability, 2) for item in payoff.scenarios], [0.2, 0.5, 0.3])
        self.assertFalse(payoff.rank_eligible)

    def test_high_conviction_gate_requires_complete_primary_evidence_and_payoff(self) -> None:
        event = ChangeEvent(
            category="margin",
            title="Margin expanded",
            summary="Operating margin expanded.",
            severity=5,
            direction="positive",
            event_date=date.today().isoformat(),
            source="10-Q",
            citations=[Citation(
                source="10-Q", url="https://www.sec.gov/test", filed=date.today().isoformat(),
                form="10-Q", section="MD&A", snippet="Operating margin expanded.",
                accession="0001", period_end="2026-03-31", source_tier=1,
            )],
        )
        ideas = generate_trade_ideas(
            CompanyIdentity("TEST", "0001", "Test Inc."),
            [event],
            PriceReaction("TEST", event.event_date, 100, 101, 1.0, "Test"),
            _OfficialConsensus(),
        )
        evidence = build_evidence_ledger("TEST", ideas, [event])
        gates = finalize_idea_research(ideas, _valuation(), evidence, 100)
        self.assertTrue(gates[0].eligible)
        self.assertTrue(gates[0].high_conviction)
        self.assertEqual(ideas[0].stage, "High-Conviction")
        self.assertGreaterEqual(ideas[0].score.total, 70)

    def test_research_ready_allows_payoff_envelope_without_high_conviction(self) -> None:
        event = ChangeEvent(
            category="margin",
            title="Margin expanded",
            summary="Operating margin expanded.",
            severity=5,
            direction="positive",
            event_date=date.today().isoformat(),
            source="10-Q",
            citations=[Citation(
                source="10-Q", url="https://www.sec.gov/test", filed=date.today().isoformat(),
                form="10-Q", section="MD&A", snippet="Operating margin expanded.",
                accession="0001", period_end="2026-03-31", source_tier=1,
            )],
        )
        ideas = generate_trade_ideas(
            CompanyIdentity("TEST", "0001", "Test Inc."),
            [event],
            PriceReaction("TEST", event.event_date, 100, 101, 1.0, "Test"),
            _OfficialConsensus(),
        )
        evidence = build_evidence_ledger("TEST", ideas, [event])
        insufficient = ValuationResult(
            template="Non-financial",
            status="Insufficient data",
            currency="USD",
            missing_data=["No valuation fields."],
        )
        gates = finalize_idea_research(ideas, insufficient, evidence, 100)
        self.assertFalse(gates[0].eligible)
        self.assertTrue(gates[0].research_ready)
        self.assertEqual(ideas[0].stage, "Research-Ready")
        self.assertEqual(ideas[0].payoff_model.status, "Envelope")
        self.assertIsNotNone(expected_value(ideas[0].scenarios))

    def test_user_assumptions_complete_payoff_envelope_when_entry_price_is_missing(self) -> None:
        event = ChangeEvent(
            category="margin",
            title="Margin expanded",
            summary="Operating margin expanded.",
            severity=5,
            direction="positive",
            event_date=date.today().isoformat(),
            source="10-Q",
            citations=[Citation(
                source="10-Q", url="https://www.sec.gov/test", filed=date.today().isoformat(),
                form="10-Q", section="MD&A", snippet="Operating margin expanded.",
                accession="0001", period_end="2026-03-31", source_tier=1,
            )],
        )
        ideas = generate_trade_ideas(
            CompanyIdentity("TEST", "0001", "Test Inc."),
            [event],
            PriceReaction("TEST", event.event_date, 100, 101, 1.0, "Test"),
            _OfficialConsensus(),
        )
        idea = ideas[0]
        idea.user_assumptions = {
            "entry_price": 100.0,
            "bear_exit": 80.0,
            "base_exit": 105.0,
            "bull_exit": 140.0,
            "transaction_cost_pct": 0.2,
            "dividend_return_pct": 0.5,
        }
        evidence = build_evidence_ledger("TEST", ideas, [event])
        insufficient = ValuationResult(
            template="Non-financial",
            status="Insufficient data",
            currency="USD",
            missing_data=["No valuation fields."],
        )

        finalize_idea_research(ideas, insufficient, evidence, None)

        self.assertEqual(idea.payoff_model.payoff_completeness.status, "Complete")
        self.assertEqual(idea.payoff_model.entry_price, 100.0)
        self.assertEqual([scenario.exit_value for scenario in idea.scenarios], [80.0, 105.0, 140.0])
        self.assertIsNotNone(idea.payoff_model.expected_value_pct)
        self.assertTrue(any(item.field == "bear_exit" for item in idea.payoff_model.assumption_provenance))

    def test_storage_dedupes_reactions_and_calibrates_at_thirty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            status = PriceProviderStatus(
                "TEST", "CSV daily prices", "available", "2026-06-25T00:00:00+00:00",
                "Fixture", official=False, adjusted=True,
            )
            store.save_price_provider_status(status)
            store.save_price_bars(
                "TEST", "CSV daily prices",
                [{"date": date(2026, 1, 2), "close": 100.0, "volume": 1000}],
                adjusted=True, official=False, source_url="fixture.csv",
            )
            self.assertEqual(store.list_price_provider_statuses("TEST")[0]["status"], "available")
            reaction = StooqPriceClient().event_window_reaction("NONE", "event", "2026-01-01")
            store.save_event_reactions([reaction, reaction])
            with store.connect() as db:
                count = db.execute("SELECT COUNT(*) FROM event_reactions").fetchone()[0]
            self.assertEqual(count, 1)
            for index in range(30):
                store.record_event_signal(
                    signal_id=f"signal-{index}", ticker="TEST", signal_type="margin",
                    event_date=f"2025-01-{(index % 28) + 1:02d}", direction="positive",
                    expected_return_pct=5.0, predicted_success_probability=0.6,
                    realized_return_pct=2.0 if index < 18 else -1.0,
                    abnormal_return_pct=None, stage="High-Conviction", horizon_label="1-2 quarters",
                )
            probability, sample = store.calibrated_probability("margin", "1-2 quarters")
            self.assertEqual(sample, 30)
            self.assertAlmostEqual(probability, 0.6)
            self.assertEqual(build_calibration_report(store).status, "Calibrated")


def _filing(form: str, filing_date: str) -> FilingRecord:
    return FilingRecord(
        form=form,
        accession=f"0000000000-26-{form.replace('-', '')}",
        filing_date=filing_date,
        report_date="2025-12-31",
        primary_doc="filing.htm",
        description="Test filing",
        url="https://www.sec.gov/Archives/test.htm",
    )


def _valuation() -> ValuationResult:
    cases = [
        ValuationCase("Bear", 0.25, 80.0, "DCF", ["Bear assumptions"]),
        ValuationCase("Base", 0.50, 100.0, "DCF", ["Base assumptions"]),
        ValuationCase("Bull", 0.25, 130.0, "DCF", ["Bull assumptions"]),
    ]
    return ValuationResult(
        template="Non-financial",
        status="Available",
        cases=cases,
        currency="USD",
        bridge=[
            ValuationBridgeStep(case.name, "Fair value", case.fair_value, "USD", "DCF", "Fixture")
            for case in cases
        ],
    )


def _idea(direction: str) -> TradeIdea:
    event = ChangeEvent(
        "margin", "Margin changed", "Margin changed.", 4, "positive",
        date.today().isoformat(), "10-Q",
    )
    return TradeIdea(
        "idea", f"{direction} TEST", direction, direction, "Thesis", "1-2 quarters",
        "Next earnings", "Variant", [event],
        score=ScoreBreakdown(90, 25, 15, 20, 10, 10, 5),
        signal_family="margin",
    )


def _trading_dates(start: date, count: int) -> list[date]:
    dates: list[date] = []
    current = start
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _price_rows(dates: list[date], base: float, slope: float) -> list[dict]:
    return [
        {
            "date": day,
            "close": base + index * slope + math.sin(index / 4) * 0.5,
            "volume": 1_000_000 + index * 100,
        }
        for index, day in enumerate(dates)
    ]
