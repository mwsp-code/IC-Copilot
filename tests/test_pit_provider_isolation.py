"""Historical revision tests: never mix providers or fiscal period types."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from equity_research.models import ConsensusPackage, EstimatePoint, TargetConsensus
from equity_research.research_store import ResearchStore


class PointInTimeRevisionIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ResearchStore(Path(self.temporary.name) / "research.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def save_target(self, as_of: str, provider: str, value: float) -> None:
        self.store.save_consensus_package(ConsensusPackage(
            ticker="AAPL", provider=provider, status="Available",
            target=TargetConsensus("AAPL", as_of, target_mean=value, source=provider),
        ))

    def save_estimate(
        self, as_of: str, provider: str, value: float, period_type: str = "annual"
    ) -> None:
        self.store.save_consensus_package(ConsensusPackage(
            ticker="AAPL", provider=provider, status="Available",
            estimates=[EstimatePoint(
                "AAPL", as_of, "Revenue", "2026-12-31", period_type,
                average=value, source=provider,
            )],
        ))

    def test_provider_free_target_revision_pins_latest_provider(self) -> None:
        self.save_target("2026-11-10", "Alpha", 100)
        self.save_target("2026-11-12", "Beta", 900)
        self.save_target("2026-11-20", "Alpha", 130)
        item = self.store.revisions("AAPL", windows=(7,))[0]
        self.assertEqual(item.provider, "Alpha")
        self.assertEqual(item.status, "available")
        self.assertEqual((item.start_date, item.end_date), ("2026-11-10", "2026-11-20"))
        self.assertEqual((item.start_value, item.end_value), (100, 130))
        self.assertAlmostEqual(item.change_pct, 30.0)
        explicit = self.store.revisions("AAPL", windows=(7,), provider="Alpha")[0]
        self.assertEqual(item.change_pct, explicit.change_pct)

    def test_provider_free_estimate_revision_pins_provider_and_period(self) -> None:
        self.save_estimate("2026-11-10", "Alpha", 100)
        self.save_estimate("2026-11-12", "Beta", 700)
        self.save_estimate("2026-11-13", "Alpha", 999, "quarterly")
        self.save_estimate("2026-11-20", "Alpha", 130)
        item = self.store.estimate_revision(
            "AAPL", "Revenue", "2026-12-31", "2026-11-14", "2026-11-20"
        )
        self.assertEqual(item.provider, "Alpha")
        self.assertEqual((item.start_value, item.end_value), (100, 130))
        self.assertAlmostEqual(item.change_pct, 30.0)
        annual = self.store.estimate_revision(
            "AAPL", "Revenue", "2026-12-31", "2026-11-14", "2026-11-20",
            provider="Alpha", period_type="annual",
        )
        self.assertEqual(item.change_pct, annual.change_pct)
        quarterly = self.store.estimate_revision(
            "AAPL", "Revenue", "2026-12-31", "2026-11-14", "2026-11-20",
            provider="Alpha", period_type="quarterly",
        )
        self.assertEqual((quarterly.start_value, quarterly.end_value), (999, 999))
        self.assertEqual(quarterly.change_pct, 0)

    def test_missing_target_history_never_borrows_another_provider(self) -> None:
        self.save_target("2026-11-10", "Beta", 100)
        self.save_target("2026-11-20", "Alpha", 130)
        item = self.store.revisions("AAPL", windows=(7,))[0]
        self.assertEqual(item.provider, "Alpha")
        self.assertEqual(item.status, "insufficient_history")
        self.assertIsNone(item.start_value)
        self.assertIsNone(item.change_pct)
        self.assertIn("Alpha", item.reason)

    def test_missing_estimate_history_never_borrows_another_period(self) -> None:
        self.save_estimate("2026-11-10", "Beta", 111)
        self.save_estimate("2026-11-12", "Alpha", 900, "quarterly")
        self.save_estimate("2026-11-20", "Alpha", 130, "annual")
        item = self.store.estimate_revision(
            "AAPL", "Revenue", "2026-12-31", "2026-11-14", "2026-11-20"
        )
        self.assertEqual(item.provider, "Alpha")
        self.assertEqual(item.status, "insufficient_history")
        self.assertIsNone(item.start_value)
        self.assertIsNone(item.change_pct)

    def test_future_target_snapshot_cannot_supply_historical_window(self) -> None:
        self.save_target("2026-11-18", "Alpha", 100)
        self.save_target("2026-11-20", "Alpha", 130)
        self.save_target("2026-11-25", "Beta", 800)
        item = self.store.revisions("AAPL", windows=(7,), provider="Alpha")[0]
        self.assertEqual(item.status, "insufficient_history")
        self.assertIsNone(item.start_value)
        self.assertEqual(item.end_date, "2026-11-20")

    def test_same_day_provider_tie_deterministic_and_isolated(self) -> None:
        self.save_target("2026-11-10", "Alpha", 100)
        self.save_target("2026-11-10", "Beta", 500)
        self.save_target("2026-11-20", "Beta", 600)
        self.save_target("2026-11-20", "Alpha", 130)
        item = self.store.revisions("AAPL", windows=(7,))[0]
        self.assertEqual(item.provider, "Alpha")
        self.assertEqual((item.start_value, item.end_value), (100, 130))


if __name__ == "__main__":
    unittest.main()
