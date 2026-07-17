from __future__ import annotations

import tempfile
import threading
import unittest
import json
from datetime import date, timedelta
from pathlib import Path

from equity_research.alerts import generate_consensus_alerts
from equity_research.expectations import build_expectations_bridge
from equity_research.models import (
    ChangeEvent,
    ConsensusPackage,
    EstimatePoint,
    FinancialMetric,
    IdeaGateResult,
    MonitorItem,
    RecommendationConsensus,
    TargetConsensus,
    TradeIdea,
)
from equity_research.research_store import ResearchStore
from equity_research.sample_data import demo_result
from scripts.snapshot_consensus import _stored_snapshot_count


class ResearchStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ResearchStore(Path(self.temporary.name) / "research.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_snapshot_is_idempotent_and_revision_windows_are_point_in_time(self) -> None:
        today = date.today()
        for days, value in ((100, 100), (90, 110), (30, 120), (7, 125), (0, 130)):
            package = _package(today - timedelta(days=days), target=value)
            self.store.save_consensus_package(package)
            self.store.save_consensus_package(package)
        with self.store.connect() as db:
            count = db.execute("SELECT COUNT(*) FROM consensus_snapshots").fetchone()[0]
        self.assertEqual(count, 5)
        revisions = {item.window_days: item for item in self.store.revisions("AAPL", provider="Test")}
        self.assertAlmostEqual(revisions[7].change_pct, 4.0)
        self.assertAlmostEqual(revisions[30].change_pct, 8.333333, places=4)
        self.assertAlmostEqual(revisions[90].change_pct, 18.181818, places=4)
        historical = self.store.target_at_or_before("AAPL", (today - timedelta(days=40)).isoformat())
        self.assertEqual(historical.target_mean, 110)

    def test_snapshot_count_tracks_point_in_time_rows_without_backfill_implication(self) -> None:
        today = date.today()
        package = _package(today, target=130)
        package.estimates = [EstimatePoint(
            "AAPL", today.isoformat(), "EPS", "2027-09-30", "annual", 8.5, currency="USD", source="FMP",
        )]
        package.recommendations = RecommendationConsensus(
            "AAPL", today.isoformat(), strong_buy=10, buy=20, hold=8, sell=1, strong_sell=0, source="FMP",
        )
        self.assertEqual(_stored_snapshot_count(self.store, "AAPL"), 0)

        self.store.save_consensus_package(package)
        first_count = _stored_snapshot_count(self.store, "AAPL")
        self.store.save_consensus_package(package)

        self.assertEqual(first_count, 3)
        self.assertEqual(_stored_snapshot_count(self.store, "AAPL"), first_count)
        old_event_target = self.store.target_at_or_before("AAPL", (today - timedelta(days=1)).isoformat(), "Test")
        self.assertIsNone(old_event_target)

    def test_revision_windows_explain_insufficient_local_history(self) -> None:
        today = date.today()
        self.store.save_consensus_package(_package(today - timedelta(days=3), target=120))
        self.store.save_consensus_package(_package(today, target=121))
        revisions = {item.window_days: item for item in self.store.revisions("AAPL", provider="Test")}
        self.assertEqual(revisions[7].status, "insufficient_history")
        self.assertIn("earliest local snapshot", revisions[7].reason)
        self.assertEqual(revisions[7].end_date, today.isoformat())
        self.assertIsNone(revisions[7].start_date)

    def test_categorical_thesis_monitor_values_round_trip(self) -> None:
        event = ChangeEvent(
            "financial_kpi", "Cash changed", "Cash changed.", 4, "neutral",
            date.today().isoformat(), "SEC",
        )
        idea = TradeIdea(
            "idea-categorical", "Watch AAPL", "Watch", "Watch", "Thesis", "1 quarter",
            "Next filing", "Unknown", [event],
            monitor_items=[MonitorItem(
                "Claim validation", "SEC", "Each refresh", "Validated", "Rejected",
                metric="thesis_grade_status", operator="==",
                confirm_value="Thesis-grade", break_value="Not thesis-grade",
                deadline=(date.today() + timedelta(days=90)).isoformat(),
                source_field="validated_claims.status",
            )],
        )

        self.store.save_thesis_checks("AAPL", [idea])
        row = self.store.list_thesis_checks("AAPL")[0]

        self.assertEqual(row["confirm_value"], "Thesis-grade")
        self.assertEqual(row["break_value"], "Not thesis-grade")

    def test_future_estimate_cannot_leak_into_historical_actual(self) -> None:
        prior = _package(date(2026, 2, 20), target=100)
        prior.estimates = [EstimatePoint(
            "AAPL", "2026-02-20", "Revenue", "2025-12-31", "annual", 100, currency="USD", source="Test",
        )]
        future = _package(date(2026, 3, 2), target=110)
        future.estimates = [EstimatePoint(
            "AAPL", "2026-03-02", "Revenue", "2025-12-31", "annual", 130, currency="USD", source="Test",
        )]
        self.store.save_consensus_package(prior)
        self.store.save_consensus_package(future)
        actual = FinancialMetric(
            "Revenue", 110, "USD", "2025-12-31", filed="2026-03-01",
        )
        bridge = build_expectations_bridge("AAPL", future, [actual], self.store)
        comparison = next(item for item in bridge.comparisons if item.metric == "Revenue")
        self.assertEqual(comparison.expected, 100)
        self.assertAlmostEqual(comparison.surprise_pct, 10)

    def test_watchlist_alert_dedupe_and_severity_escalation(self) -> None:
        self.store.add_watchlist("baba")
        self.store.add_watchlist("BABA")
        self.assertEqual(len(self.store.list_watchlist()), 1)
        first = self.store.create_alert("BABA", "test", "One", "message", 2, "BABA:test")
        duplicate = self.store.create_alert("BABA", "test", "Two", "message", 2, "BABA:test")
        escalated = self.store.create_alert("BABA", "test", "Three", "message", 4, "BABA:test")
        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertEqual(escalated.severity, 4)
        self.assertTrue(self.store.update_alert_status(escalated.alert_id, "read"))

    def test_recommendation_change_and_analyst_drop_generate_alerts(self) -> None:
        old = _package(date(2026, 6, 20), target=100, analysts=20, label="Buy")
        new = _package(date(2026, 6, 21), target=100, analysts=12, label="Hold")
        self.store.save_consensus_package(old)
        self.store.save_consensus_package(new)
        alerts = generate_consensus_alerts(new, self.store)
        kinds = {alert.alert_type for alert in alerts}
        self.assertIn("recommendation_change", kinds)
        self.assertIn("analyst_count_drop", kinds)

    def test_two_store_connections_can_write_concurrently(self) -> None:
        errors = []

        def write(index: int) -> None:
            try:
                store = ResearchStore(self.store.path)
                store.add_watchlist(f"T{index}")
            except Exception as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.list_watchlist()), 8)

    def test_legacy_idea_memory_migrates_once_and_is_preserved(self) -> None:
        legacy = Path(self.temporary.name) / "idea_memory.json"
        records = [{"idea_id": "abc", "ticker": "BABA", "status": "Open", "title": "Test"}]
        legacy.write_text(json.dumps(records), encoding="utf-8")
        self.assertEqual(self.store.migrate_idea_memory(legacy), 1)
        self.assertEqual(self.store.migrate_idea_memory(legacy), 0)
        self.assertEqual(self.store.list_idea_records()[0]["idea_id"], "abc")
        self.assertTrue(legacy.exists())

    def test_high_conviction_promotion_requires_research_ready_gate(self) -> None:
        idea = demo_result("AAPL").ideas[0]
        idea.idea_id = "unsafe-promotion-fixture"
        idea.stage = "Candidate"
        idea.gate_result = IdeaGateResult(
            "Candidate",
            eligible=True,
            research_ready=False,
            high_conviction=True,
            research_ready_failed=["Signal is not mapped to a material driver"],
            high_conviction_failed=[],
        )
        self.store.save_idea_versions("AAPL", "unsafe-run", [idea])

        result = self.store.promote_idea_with_audit(idea.idea_id)
        audit = self.store.idea_audit(idea.idea_id)

        self.assertFalse(result["promoted"])
        self.assertIn("Research-Ready", result["reason"])
        self.assertEqual(audit["versions"][0]["stage"], "Candidate")
        self.assertFalse(self.store.promote_idea(idea.idea_id))


def _package(day: date, target: float, analysts: int = 20, label: str = "Buy") -> ConsensusPackage:
    as_of = day.isoformat()
    return ConsensusPackage(
        ticker="AAPL", provider="Test", status="Available",
        target=TargetConsensus(
            "AAPL", as_of, target_mean=target, target_median=target,
            target_high=target * 1.2, target_low=target * 0.8,
            analyst_count=analysts, source="Test",
        ),
        recommendations=RecommendationConsensus(
            "AAPL", as_of, buy=analysts, consensus_label=label, source="Test",
        ),
    )


if __name__ == "__main__":
    unittest.main()
