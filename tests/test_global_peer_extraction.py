from __future__ import annotations

import unittest

from equity_research.global_peers import GlobalPeerFinancialProvider, global_peer_identity_for
from equity_research.idea_engine import build_driver_analysis
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyIdentity,
    EventWindowReaction,
    FilingRecord,
    FinancialMetric,
    PeerDefinition,
    PeerUniverse,
    TradeIdea,
)
from equity_research.pipeline import (
    _PeerSnapshot,
    _build_peer_snapshots,
    _equity_credit_lens_for_idea,
    _peer_metric_summary,
    _peer_readthrough_for_event,
)


def _metric(name: str, value: float, previous: float, yoy: float, period: str = "2026-03-31") -> FinancialMetric:
    return FinancialMetric(
        name=name,
        value=value,
        unit="USD",
        period_end=period,
        previous_value=previous,
        yoy_change_pct=yoy,
    )


def _gross_profit_event(period: str = "2026-03-31") -> ChangeEvent:
    return ChangeEvent(
        category="financial_kpi",
        title="Gross Profit changed +49.7%",
        summary="Gross Profit was higher.",
        severity=5,
        direction="positive",
        event_date="2026-04-24",
        source="SEC Companyfacts",
        citations=[
            Citation(
                "SEC XBRL Companyfacts",
                "https://data.sec.gov/example.json",
                section="Gross Profit",
                snippet="Gross Profit: 4.7B USD",
                period_end=period,
            )
        ],
        metrics={"metric_name": "Gross Profit", "yoy_change_pct": 49.7},
    )


class GlobalPeerExtractionTests(unittest.TestCase):
    def test_byd_identity_maps_otc_to_hk_and_cn_sources(self) -> None:
        identity = global_peer_identity_for("BYDDF")

        self.assertIsNotNone(identity)
        self.assertEqual(identity.issuer_name, "BYD Company Limited")
        self.assertEqual(identity.home_ticker, "1211.HK")
        self.assertIn("002594.SZ", identity.aliases)
        self.assertIn("cninfo_document", identity.source_priority)

    def test_tcehy_adr_profile_maps_to_tencent_hk_official_sources(self) -> None:
        identity = global_peer_identity_for("TCEHY")

        self.assertIsNotNone(identity)
        self.assertEqual(identity.issuer_name, "Tencent Holdings Limited")
        self.assertEqual(identity.home_ticker, "0700.HK")
        self.assertEqual(identity.home_exchange, "HKEX")
        self.assertEqual(identity.reporting_currency, "HKD")
        self.assertIn("issuer_ir_report", identity.source_priority)
        self.assertIn("issuer_ir_report", identity.source_urls)

    def test_fixture_official_document_extracts_tencent_cash_credit_metrics(self) -> None:
        text = """
        Tencent Holdings Limited results announcement.
        Period ended 2026-03-31.
        Revenue HKD 180.0 billion HKD 160.0 billion
        Operating profit HKD 55.0 billion HKD 48.0 billion
        Cash and cash equivalents HKD 320.0 billion HKD 300.0 billion
        Net cash generated from operating activities HKD 80.0 billion HKD 70.0 billion
        Capital expenditures HKD 18.0 billion HKD 14.0 billion
        Finance costs HKD 2.5 billion HKD 2.0 billion
        """
        provider = GlobalPeerFinancialProvider(
            fixture_documents={
                "TCEHY": [
                    (
                        "issuer_ir_report",
                        "Tencent fixture results announcement",
                        "https://tencent.test/results.pdf",
                        text,
                    )
                ]
            },
            enable_live=False,
        )

        coverage = provider.fetch("TCEHY")
        metrics = {item.metric: item for item in coverage.metrics}

        self.assertEqual(coverage.status, "available")
        self.assertIn("Revenue", metrics)
        self.assertIn("Operating Income", metrics)
        self.assertIn("Cash", metrics)
        self.assertIn("Operating Cash Flow", metrics)
        self.assertIn("Capital Expenditure", metrics)
        self.assertIn("Interest Expense", metrics)
        self.assertEqual(metrics["Cash"].currency, "HKD")

    def test_fixture_official_document_extracts_byd_margin_metrics(self) -> None:
        text = """
        Period ended 2026-03-31.
        Revenue CNY 777.1 billion CNY 602.3 billion
        Gross Profit CNY 156.4 billion CNY 120.0 billion
        """
        provider = GlobalPeerFinancialProvider(
            fixture_documents={
                "BYDDF": [("cninfo_document", "BYD fixture annual report", "https://cninfo.test/byd.pdf", text)]
            },
            enable_live=False,
        )

        coverage = provider.fetch("BYDDF")
        metrics = {item.metric: item for item in coverage.metrics}

        self.assertEqual(coverage.status, "available")
        self.assertIn("Revenue", metrics)
        self.assertIn("Gross Profit", metrics)
        self.assertIn("Gross Margin", metrics)
        self.assertEqual(metrics["Revenue"].currency, "CNY")
        self.assertAlmostEqual(metrics["Gross Margin"].value, 156.4 / 777.1 * 100)

    def test_tsm_peer_recovers_current_metrics_from_sec_periodic_inline_xbrl(self) -> None:
        filing = FilingRecord(
            "20-F", "0001", "2026-04-10", "2025-12-31", "tsm.htm", "20-F", "https://sec.test/tsm.htm",
        )
        filing_html = """
        <xbrli:unit id="twd"><xbrli:measure>iso4217:TWD</xbrli:measure></xbrli:unit>
        <xbrli:context id="fy24"><xbrli:period><xbrli:startDate>2024-01-01</xbrli:startDate><xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
        <xbrli:context id="fy25"><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
        <ix:nonFraction name="ifrs-full:Revenue" contextRef="fy24" unitRef="twd">100</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:GrossProfit" contextRef="fy24" unitRef="twd">50</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:Revenue" contextRef="fy25" unitRef="twd">130</ix:nonFraction>
        <ix:nonFraction name="ifrs-full:GrossProfit" contextRef="fy25" unitRef="twd">70</ix:nonFraction>
        """

        class FixtureSec:
            def map_ticker(self, ticker):
                return CompanyIdentity(ticker, "0001046179", "Taiwan Semiconductor Manufacturing Co Ltd")

            def get_recent_filings(self, cik, forms, limit):
                return [filing]

            def get_company_facts(self, cik):
                return {
                    "facts": {
                        "ifrs-full": {
                            "Revenue": {"units": {"TWD": [
                                {"val": 80, "end": "2024-12-31", "filed": "2025-04-10", "form": "20-F", "fp": "FY", "fy": 2024},
                            ]}},
                            "GrossProfit": {"units": {"TWD": [
                                {"val": 40, "end": "2024-12-31", "filed": "2025-04-10", "form": "20-F", "fp": "FY", "fy": 2024},
                            ]}},
                            "Assets": {"units": {"TWD": [
                                {"val": 999, "end": "2026-03-31", "filed": "2026-05-01", "form": "6-K", "fp": "Q1", "fy": 2026},
                            ]}},
                        }
                    }
                }

            def get_filing_text(self, filing_record):
                return filing_html

        class FixturePrices:
            def event_window_reaction(self, ticker, event_id, event_date, event_timestamp, market, sector):
                return EventWindowReaction(
                    ticker, event_id, event_date, event_timestamp, event_date, 100.0,
                    "Fixture prices", "available", raw_returns={"1d": 1.0, "5d": 2.0, "20d": 3.0},
                )

        event = ChangeEvent(
            "financial_kpi", "Revenue changed +20%", "Revenue increased.", 4, "positive",
            "2026-04-24", "SEC", metrics={"metric_name": "Revenue", "economic_driver": "Revenue / demand"},
        )
        universe = PeerUniverse(
            "NVDA", "Configured", "Semiconductor", "Fixture", "2026-01-01",
            peers=[PeerDefinition("TSM", "Foundry peer")],
        )

        snapshots = _build_peer_snapshots(universe, FixtureSec(), FixturePrices(), [event])
        by_name = {metric.name: metric for metric in snapshots["TSM"].metrics}

        self.assertEqual(by_name["Revenue"].value, 130)
        self.assertEqual(by_name["Gross Profit"].value, 70)
        self.assertAlmostEqual(by_name["Gross Margin"].value, 70 / 130 * 100)
        self.assertEqual(by_name["Revenue"].unit, "TWD")
        self.assertEqual(by_name["Revenue"].source_kind, "periodic_inline_xbrl")

    def test_live_registered_page_discovers_official_report_link(self) -> None:
        seed_url = "https://www.cninfo.com.cn/new/index"
        report_url = "https://www.cninfo.com.cn/new/disclosure/byd-2026-report.html"
        responses = {
            "https://www.hkexnews.hk/index.htm": "",
            seed_url: f'<a href="{report_url}">BYD quarterly financial results report</a>',
            report_url: """
            Period ended 2026-03-31.
            Revenue CNY 777.1 billion CNY 602.3 billion
            Gross Profit CNY 156.4 billion CNY 120.0 billion
            """,
            "https://www.bydglobal.com/": "",
        }

        provider = GlobalPeerFinancialProvider(
            fetch_text=lambda url: responses[url],
            enable_live=True,
        )
        coverage = provider.fetch("BYDDF")

        self.assertEqual(coverage.status, "available")
        self.assertTrue(any(document.url == report_url for document in coverage.documents))
        self.assertTrue(any(metric.metric == "Gross Margin" for metric in coverage.metrics))

    def test_stale_peer_fact_is_not_direct_readthrough(self) -> None:
        source = _gross_profit_event("2026-03-31")
        old_peer = ChangeEvent(
            category="financial_kpi",
            title="Gross Profit changed -170.0%",
            summary="Gross Profit was stale.",
            severity=5,
            direction="negative",
            event_date="2013-02-01",
            source="SEC Companyfacts",
            citations=[
                Citation(
                    "SEC XBRL Companyfacts",
                    "https://data.sec.gov/gm.json",
                    section="Gross Profit",
                    snippet="Gross Profit: -3.1B USD",
                    period_end="2012-12-31",
                )
            ],
            metrics={"metric_name": "Gross Profit", "yoy_change_pct": -170.0},
        )
        snapshot = _PeerSnapshot("GM", [], [old_peer])

        readthrough = _peer_readthrough_for_event(source, snapshot)

        self.assertEqual(readthrough.evidence_status, "Stale peer metric")
        self.assertEqual(readthrough.failure_status, "stale_period")
        self.assertNotEqual(readthrough.relation, "Contradicting read-through")
        self.assertIsNotNone(readthrough.metric_readthrough)
        self.assertIn("Gross Profit", readthrough.metric_readthrough.required_metrics)
        self.assertIn("Read-through weakens", readthrough.metric_readthrough.falsification_tests[0])
        summary = _peer_metric_summary([readthrough], [readthrough.metric_readthrough])
        self.assertEqual(summary.status, "Weak - missing/stale")
        self.assertEqual(summary.stale_metric_peers, 1)
        self.assertTrue(any("stale" in gap for gap in summary.data_gaps))
        self.assertTrue(any("same fiscal period" in action for action in summary.next_actions))

    def test_peer_metric_readthrough_reports_present_missing_and_acceptance_tests(self) -> None:
        source = _gross_profit_event("2026-03-31")
        snapshot = _PeerSnapshot(
            "RIVN",
            [
                _metric("Revenue", 1_000_000_000, 800_000_000, 25),
                _metric("Gross Profit", 180_000_000, 100_000_000, 80),
            ],
            [],
        )

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.metric_family, "gross_margin_mix")
        self.assertEqual(metric.status, "available")
        self.assertIn("Revenue", metric.present_metrics)
        self.assertIn("Gross Profit", metric.present_metrics)
        self.assertIn("Gross Margin", metric.missing_metrics)
        self.assertIn("Deliveries", metric.required_metrics)
        self.assertTrue(any("operating metrics" in item for item in metric.acceptance_criteria))
        self.assertTrue(any("peer period is stale" in item for item in metric.falsification_tests))
        summary = _peer_metric_summary([readthrough], [metric])
        self.assertEqual(summary.status, "Partial")
        self.assertEqual(summary.operating_metric_peers, 1)
        self.assertIn("gross_margin_mix", summary.metric_families)
        self.assertIn("mostly confirms", summary.summary)
        self.assertTrue(summary.confirmations)
        self.assertIn("support a candidate", summary.stage_impact)

    def test_missing_peer_metric_family_reports_required_metrics(self) -> None:
        source = _gross_profit_event("2026-03-31")
        snapshot = _PeerSnapshot("F", [_metric("Net Income", 100, 90, 11)], [])

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.status, "missing_metric_family")
        self.assertIn("Revenue", metric.required_metrics)
        self.assertEqual(metric.present_metrics, [])
        self.assertIn("Gross Profit", metric.missing_metrics)
        self.assertTrue(metric.acceptance_criteria)
        self.assertTrue(metric.falsification_tests)
        summary = _peer_metric_summary([readthrough], [metric])
        self.assertEqual(summary.status, "Unavailable")
        self.assertEqual(summary.operating_metric_peers, 0)
        self.assertTrue(any("price-sympathy" in gap for gap in summary.data_gaps))

    def test_revenue_demand_peer_readthrough_accepts_revenue_without_deliveries(self) -> None:
        source = ChangeEvent(
            category="financial_kpi",
            title="Revenue changed +15.0%",
            summary="Revenue improved.",
            severity=4,
            direction="positive",
            event_date="2026-03-31",
            source="SEC Companyfacts",
            citations=[Citation("SEC XBRL", "https://data.sec.gov", period_end="2026-03-31")],
            metrics={"metric_name": "Revenue", "yoy_change_pct": 15.0, "economic_driver": "Revenue / demand"},
        )
        snapshot = _PeerSnapshot(
            "NVDA",
            [_metric("Revenue", 26_000_000_000, 20_000_000_000, 30)],
            [],
        )

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.status, "available")
        self.assertIn("Revenue", metric.present_metrics)
        self.assertIn("Deliveries", metric.missing_metrics)
        self.assertFalse(any("Missing blocking" in gap for gap in metric.data_gaps))
        self.assertTrue(any("Optional metrics not found" in gap for gap in metric.data_gaps))

    def test_cash_credit_readthrough_distinguishes_stale_optional_metrics_from_missing(self) -> None:
        source = ChangeEvent(
            category="financial_kpi",
            title="Cash changed +61.8%",
            summary="Cash improved.",
            severity=4,
            direction="positive",
            event_date="2026-05-01",
            source="SEC Companyfacts",
            citations=[Citation("SEC XBRL", "https://data.sec.gov", period_end="2026-03-31")],
            metrics={"metric_name": "Cash", "yoy_change_pct": 61.8, "economic_driver": "Cash generation"},
        )
        snapshot = _PeerSnapshot(
            "MSFT",
            [
                _metric("Cash", 32_000_000_000, 28_000_000_000, 14),
                _metric("Operating Cash Flow", 46_000_000_000, 36_000_000_000, 28),
                _metric("Long-term Debt", 31_000_000_000, 40_000_000_000, -22),
                _metric("Current Debt", 0, 1_000_000_000, -100, period="2018-06-30"),
                _metric("Interest Expense", 2_900_000_000, 2_600_000_000, 12, period="2024-06-30"),
            ],
            [],
        )

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.status, "available")
        self.assertIn("Cash", metric.present_metrics)
        self.assertNotIn("Current Debt", metric.missing_metrics)
        self.assertIn("Interest Expense", metric.missing_metrics)
        self.assertTrue(any("stale or period-misaligned" in gap for gap in metric.data_gaps))
        self.assertTrue(any("Current debt is not separately disclosed" in gap for gap in metric.data_gaps))
        self.assertFalse(any("Optional metrics not found" in gap and "Interest Expense" in gap for gap in metric.data_gaps))

    def test_cash_credit_readthrough_treats_capital_return_lines_as_optional_context(self) -> None:
        source = ChangeEvent(
            category="financial_kpi",
            title="Cash changed +61.8%",
            summary="Cash improved.",
            severity=4,
            direction="positive",
            event_date="2026-05-01",
            source="SEC Companyfacts",
            citations=[Citation("SEC XBRL", "https://data.sec.gov", period_end="2026-03-31")],
            metrics={"metric_name": "Cash", "yoy_change_pct": 61.8, "economic_driver": "Cash generation"},
        )
        snapshot = _PeerSnapshot(
            "AMZN",
            [
                _metric("Cash", 101_000_000_000, 66_000_000_000, 53),
                _metric("Operating Cash Flow", 26_000_000_000, 17_000_000_000, 53),
                _metric("Capital Expenditure", 22_000_000_000, 18_000_000_000, 22),
                _metric("Long-term Debt", 119_000_000_000, 53_000_000_000, 123),
            ],
            [],
        )

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.status, "available")
        self.assertNotIn("Dividends Paid", metric.missing_metrics)
        self.assertNotIn("Share Repurchases", metric.missing_metrics)
        self.assertTrue(any("Capital-return sub-lines" in gap for gap in metric.data_gaps))

    def test_operating_expense_readthrough_uses_sga_as_broader_sales_marketing_coverage(self) -> None:
        source = ChangeEvent(
            category="financial_kpi",
            title="Operating Income changed +20.0%",
            summary="Operating income improved.",
            severity=4,
            direction="positive",
            event_date="2026-05-01",
            source="SEC Companyfacts",
            citations=[Citation("SEC XBRL", "https://data.sec.gov", period_end="2026-03-31")],
            metrics={"metric_name": "Operating Income", "yoy_change_pct": 20.0, "economic_driver": "Operating leverage"},
        )
        snapshot = _PeerSnapshot(
            "NVDA",
            [
                _metric("Revenue", 100, 70, 42),
                _metric("SG&A Expense", 9, 8, 12),
                _metric("R&D Expense", 14, 10, 40),
                _metric("Operating Income", 53, 30, 77),
            ],
            [],
        )

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.status, "available")
        self.assertIn("SG&A Expense", metric.present_metrics)
        self.assertNotIn("Sales and Marketing Expense", metric.missing_metrics)
        self.assertFalse(any("Missing blocking" in gap for gap in metric.data_gaps))
        self.assertTrue(any("broader SG&A" in gap for gap in metric.data_gaps))

    def test_operating_expense_readthrough_uses_operating_income_when_rd_is_not_separate(self) -> None:
        source = ChangeEvent(
            category="financial_kpi",
            title="Operating Income changed +20.0%",
            summary="Operating income improved.",
            severity=4,
            direction="positive",
            event_date="2026-05-01",
            source="SEC Companyfacts",
            citations=[Citation("SEC XBRL", "https://data.sec.gov", period_end="2026-03-31")],
            metrics={"metric_name": "Operating Income", "yoy_change_pct": 20.0, "economic_driver": "Operating leverage"},
        )
        snapshot = _PeerSnapshot(
            "AMZN",
            [
                _metric("Revenue", 181, 156, 16),
                _metric("Operating Income", 24, 18, 30),
                _metric("SG&A Expense", 12, 10, 20),
            ],
            [],
        )

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.status, "available")
        self.assertNotIn("R&D Expense", metric.missing_metrics)
        self.assertTrue(any("R&D is not separately disclosed" in gap for gap in metric.data_gaps))

    def test_missing_peer_metric_family_explains_available_metrics(self) -> None:
        source = _gross_profit_event("2026-03-31")
        snapshot = _PeerSnapshot("MSFT", [_metric("Cash", 10, 8, 25)], [])

        readthrough = _peer_readthrough_for_event(source, snapshot)
        metric = readthrough.metric_readthrough

        self.assertIsNotNone(metric)
        self.assertEqual(metric.status, "missing_metric_family")
        self.assertTrue(any("Available peer metrics include: Cash" in gap for gap in metric.data_gaps))

    def test_gross_profit_bridge_excludes_below_the_line_causes(self) -> None:
        event = _gross_profit_event()
        analysis = build_driver_analysis(
            event,
            [
                _metric("Revenue", 100, 80, 25),
                _metric("Gross Profit", 45, 30, 50),
                _metric("Interest Expense", 8, 2, 300),
                _metric("Income Tax Expense", 9, 3, 200),
                _metric("Shares", 120, 100, 20),
                _metric("SG&A Expense", 30, 20, 50),
            ],
        )
        causes = [factor.cause for factor in analysis.factors]

        self.assertIn("Gross profit moved with revenue and margin", causes)
        self.assertNotIn("Higher financing cost", causes)
        self.assertNotIn("Higher tax expense", causes)
        self.assertNotIn("Dilution / share-count growth", causes)
        self.assertNotIn("SG&A Expense deleverage", causes)

    def test_equity_credit_lens_attempts_bridge_before_showing_gaps(self) -> None:
        event = ChangeEvent(
            category="financial_kpi",
            title="Cash changed +61.8%",
            summary="Cash increased.",
            severity=4,
            direction="positive",
            event_date="2026-03-31",
            source="SEC Companyfacts",
            metrics={"metric_name": "Cash", "economic_driver": "Cash generation"},
        )
        idea = TradeIdea(
            "idea-cash",
            "Long TEST: Cash generation",
            "Long",
            "Long equity",
            "Cash increased.",
            "1-2 quarters",
            "Next report",
            "Unknown",
            [event],
        )
        idea.driver_analysis = build_driver_analysis(
            event,
            [
                _metric("Cash", 162, 100, 61.8),
                _metric("Operating Cash Flow", 150, 100, 50),
                _metric("Capital Expenditure", 80, 100, -20),
            ],
        )

        lens = _equity_credit_lens_for_idea(idea)

        self.assertIn("the app links", lens["equity"])
        self.assertIn("Operating cash flow improved", lens["equity"])
        self.assertNotIn("before acting", lens["equity"])

    def test_debt_liquidity_text_lens_uses_extracted_cash_credit_facts_before_manual_gaps(self) -> None:
        event = ChangeEvent(
            category="debt_liquidity",
            title="Debt Liquidity disclosure changed",
            summary="Liquidity language changed.",
            severity=4,
            direction="positive",
            event_date="2026-03-31",
            source="6-K results announcement",
            citations=[Citation("Issuer 6-K", "https://issuer.test/6k.htm", period_end="2026-03-31")],
            metrics={"economic_driver": "Cash generation"},
        )
        idea = TradeIdea(
            "idea-liquidity",
            "Long TEST: Cash generation",
            "Long",
            "Long equity",
            "Liquidity disclosure improved.",
            "1-2 quarters",
            "Next report",
            "Unknown",
            [event],
        )
        idea.driver_analysis = build_driver_analysis(
            event,
            [
                _metric("Cash", 162, 100, 61.8),
                _metric("Operating Cash Flow", 150, 100, 50),
                _metric("Capital Expenditure", 80, 100, -20),
                _metric("Share Repurchases", 25, 15, 66.7),
                _metric("Long-term Debt", 60, 75, -20),
            ],
        )

        lens = _equity_credit_lens_for_idea(idea)

        self.assertIn("Operating cash flow improved", lens["equity"])
        self.assertIn("Covered by extracted facts", lens["credit"])
        self.assertIn("operating cash flow", lens["credit"])
        self.assertIn("capex", lens["credit"])
        self.assertIn("debt movement", lens["credit"])
        self.assertNotIn("Still needs extracted cash-flow statement facts", lens["credit"])


if __name__ == "__main__":
    unittest.main()
