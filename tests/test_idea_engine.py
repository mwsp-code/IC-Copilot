from __future__ import annotations

import unittest

from equity_research.idea_engine import (
    build_driver_analysis,
    build_peer_readthrough,
    evaluate_market_capture,
    generate_trade_ideas,
    ideas_with_changed_evidence_not_price_or_consensus,
)
from equity_research.models import ChangeEvent, Citation, CompanyIdentity, FinancialMetric
from equity_research.providers import ConsensusAdapter, PriceReaction


class FlatConsensus(ConsensusAdapter):
    official_for_conviction = True

    def revision_since(self, ticker, event_date):
        return 0.0


class IdeaEngineTests(unittest.TestCase):
    def test_market_capture_classifies_uncaptured_price_move(self) -> None:
        event = ChangeEvent(
            category="margin",
            title="Gross margin moved +3 pts",
            summary="Margin expanded.",
            severity=4,
            direction="positive",
            event_date="2026-06-01",
            source="SEC Companyfacts",
        )
        reaction = PriceReaction("AAPL", "2026-06-01", 100, 101, 1.0, "Test")
        capture = evaluate_market_capture(event, reaction, FlatConsensus(), "AAPL")
        self.assertEqual(capture.category, "Uncaptured")

    def test_market_capture_is_unknown_without_point_in_time_consensus(self) -> None:
        event = ChangeEvent(
            category="margin", title="Margin moved", summary="Margin expanded.",
            severity=4, direction="positive", event_date="2026-06-01", source="SEC",
        )
        reaction = PriceReaction("AAPL", "2026-06-01", 100, 101, 1.0, "Test")
        capture = evaluate_market_capture(event, reaction, None, "AAPL")
        self.assertEqual(capture.category, "Unknown")
        self.assertLess(capture.consensus_revision_pct or 0, 0.1)
        self.assertIn("Price reaction is available", capture.explanation)
        self.assertIn("official point-in-time consensus revision is missing", capture.explanation)
        self.assertEqual(capture.price_status, "available")
        self.assertEqual(capture.consensus_status, "missing_consensus_provider")
        self.assertEqual(capture.capture_mode, "Price-only")
        self.assertIn("Cannot classify", capture.diagnosis)
        self.assertTrue(any("CSV/manual consensus import" in item for item in capture.required_inputs))
        self.assertIn("current consensus is not backfilled", capture.point_in_time_note)

    def test_wow_filter_keeps_material_uncaptured_ideas(self) -> None:
        identity = CompanyIdentity(ticker="AAPL", cik="0000320193", name="Apple Inc.")
        event = ChangeEvent(
            category="guidance",
            title="Guidance language moved",
            summary="Outlook language improved.",
            severity=4,
            direction="positive",
            event_date="2026-06-01",
            source="10-Q",
            citations=[
                Citation(
                    source="10-Q",
                    url="https://example.com",
                    snippet="Management expects demand to improve.",
                )
            ],
        )
        reaction = PriceReaction("AAPL", "2026-06-01", 100, 101, 1.0, "Test")
        ideas = generate_trade_ideas(identity, [event], reaction, FlatConsensus())
        wow = ideas_with_changed_evidence_not_price_or_consensus(ideas)
        self.assertEqual(len(wow), 1)
        self.assertEqual(wow[0].direction, "Long")

    def test_baba_peer_map_is_configured(self) -> None:
        event = ChangeEvent(
            category="margin",
            title="Margin moved",
            summary="Margin moved.",
            severity=3,
            direction="positive",
            event_date="2026-06-01",
            source="20-F",
        )
        readthroughs = build_peer_readthrough("BABA", event)
        self.assertTrue(any(item.peer_ticker == "JD" for item in readthroughs))
        self.assertEqual(readthroughs[0].evidence_status, "Pending direct check")

    def test_operating_income_decline_attributed_to_revenue_pressure(self) -> None:
        event = _operating_income_event()
        metrics = [
            _metric("Revenue", 90, 100, -10),
            _metric("Operating Income", 12, 20, -40),
        ]
        analysis = build_driver_analysis(event, metrics)
        self.assertIn("Revenue pressure", [factor.cause for factor in analysis.factors])

    def test_operating_income_decline_attributed_to_gross_margin_pressure(self) -> None:
        event = _operating_income_event()
        metrics = [
            _metric("Revenue", 120, 100, 20),
            _metric("Gross Profit", 36, 40, -10),
            _metric("Operating Income", 12, 20, -40),
        ]
        analysis = build_driver_analysis(event, metrics)
        self.assertIn("Gross margin compression", [factor.cause for factor in analysis.factors])

    def test_operating_income_decline_attributed_to_opex_deleverage(self) -> None:
        event = _operating_income_event()
        metrics = [
            _metric("Revenue", 105, 100, 5),
            _metric("Gross Profit", 42, 40, 5),
            _metric("SG&A Expense", 35, 25, 40),
            _metric("Operating Income", 12, 20, -40),
        ]
        analysis = build_driver_analysis(event, metrics)
        self.assertTrue(any("SG&A Expense deleverage" == factor.cause for factor in analysis.factors))

    def test_share_count_basis_mismatch_does_not_use_operating_driver_factors(self) -> None:
        event = ChangeEvent(
            category="financial_kpi",
            title="Shares basis requires normalization",
            summary="Shares moved sharply but basis needs normalization.",
            severity=3,
            direction="neutral",
            event_date="2026-06-30",
            source="SEC Companyfacts",
            metrics={
                "metric_name": "Shares",
                "yoy_change_pct": -89.7,
                "normalization_required": True,
            },
        )
        metrics = [
            _metric("Revenue", 108, 100, 8),
            _metric("Sales and Marketing Expense", 180, 100, 80),
            _metric("R&D Expense", 125, 100, 25),
            _metric("Shares", 1_900_000_000, 18_500_000_000, -89.7),
        ]

        analysis = build_driver_analysis(event, metrics)

        self.assertIn("Share-count evidence needs security-basis normalization", analysis.headline)
        self.assertEqual(analysis.factors[0].cause, "Share-count basis mismatch risk")
        self.assertFalse(any("deleverage" in factor.cause for factor in analysis.factors))

    def test_gross_profit_bridge_excludes_below_the_line_causes(self) -> None:
        event = ChangeEvent(
            category="financial_kpi",
            title="Gross Profit changed +49.7%",
            summary="Gross profit increased.",
            severity=4,
            direction="positive",
            event_date="2026-06-30",
            source="SEC Companyfacts",
            metrics={"metric_name": "Gross Profit", "yoy_change_pct": 49.7},
        )
        metrics = [
            _metric("Revenue", 120, 100, 20),
            _metric("Gross Profit", 48, 32, 50),
            _metric("Interest Expense", 30, 10, 200),
            _metric("Income Tax Expense", 38, 20, 90),
            _metric("Shares", 120, 100, 20),
        ]

        analysis = build_driver_analysis(event, metrics)
        causes = [factor.cause for factor in analysis.factors]

        self.assertIn("Gross profit moved with revenue and margin", causes)
        self.assertEqual(analysis.primary_driver, "Gross margin / mix")
        self.assertTrue(analysis.peer_metric_checks)
        self.assertFalse(any("Interest" in cause or "tax" in cause.lower() or "Dilution" in cause for cause in causes))

    def test_cash_bridge_uses_cash_flow_and_financing_sources(self) -> None:
        event = ChangeEvent(
            category="financial_kpi",
            title="Cash changed +61.8%",
            summary="Cash increased.",
            severity=4,
            direction="positive",
            event_date="2026-06-30",
            source="SEC Companyfacts",
            metrics={"metric_name": "Cash", "yoy_change_pct": 61.8},
        )
        metrics = [
            _metric("Cash", 162, 100, 61.8),
            _metric("Operating Cash Flow", 150, 100, 50),
            _metric("Capital Expenditure", 80, 100, -20),
            _metric("Long-term Debt", 110, 100, 10),
            _metric("Interest Expense", 35, 10, 250),
            _metric("Income Tax Expense", 40, 20, 100),
        ]

        analysis = build_driver_analysis(event, metrics)
        causes = [factor.cause for factor in analysis.factors]

        self.assertEqual(analysis.primary_driver, "Cash generation / liquidity")
        self.assertIn("Operating cash flow improved", causes)
        self.assertTrue(any("Debt financing" in cause for cause in causes))
        self.assertFalse(any("Higher financing cost" == cause or "Higher tax expense" == cause for cause in causes))
        self.assertIn("Cash balance changes should reconcile", analysis.mechanism)


def _operating_income_event() -> ChangeEvent:
    return ChangeEvent(
        category="financial_kpi",
        title="Operating Income changed -40.0%",
        summary="Operating income declined.",
        severity=5,
        direction="negative",
        event_date="2026-06-01",
        source="SEC Companyfacts",
        metrics={
            "metric_name": "Operating Income",
            "yoy_change_pct": -40.0,
            "current_value": 12,
            "previous_value": 20,
        },
    )


def _metric(name: str, value: float, previous: float, yoy: float) -> FinancialMetric:
    return FinancialMetric(
        name=name,
        value=value,
        previous_value=previous,
        yoy_change_pct=yoy,
        unit="USD",
        period_end="2026-06-30",
        filed="2026-08-01",
    )


if __name__ == "__main__":
    unittest.main()
