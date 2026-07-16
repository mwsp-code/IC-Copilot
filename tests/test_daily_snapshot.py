from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from equity_research.models import (
    CompanyIdentity,
    ConsensusPackage,
    RecentMarketContext,
    TargetConsensus,
)
from equity_research.research_store import ResearchStore
from scripts.snapshot_consensus import collect_daily_snapshots
from tests.test_wisburg_lens import _wisburg_bundle


class DailySnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ResearchStore(Path(self.temporary.name) / "research.db")
        self.store.add_watchlist("BABA")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_daily_snapshot_reuses_same_day_wisburg_cache(self) -> None:
        wisburg = _FakeWisburgProvider()
        kwargs = dict(
            store=self.store,
            tickers=["BABA"],
            consensus_provider=_FakeConsensusProvider(),
            price_client=_FakePriceClient(),
            wisburg_provider=wisburg,
            watchlist="default",
            wisburg_mode="auto",
            snapshot_day=date.today(),
            sec_client=_FakeSecClient(),
        )
        first = collect_daily_snapshots(**kwargs)[0]
        second = collect_daily_snapshots(**kwargs)[0]

        self.assertEqual(wisburg.calls, 1)
        self.assertIn(first.wisburg_status, {"Available", "Partial"})
        self.assertEqual(second.wisburg_status, "Cached")
        self.assertTrue(second.used_same_day_wisburg_cache)
        self.assertEqual(first.overall_status, "Available")
        stored = self.store.latest_daily_snapshot_status("BABA")
        self.assertEqual(stored["wisburg_status"], "Cached")
        self.assertIsNotNone(self.store.latest_wisburg_delta("BABA"))

    def test_consensus_failure_does_not_block_price_or_wisburg_snapshot(self) -> None:
        status = collect_daily_snapshots(
            store=self.store,
            tickers=["BABA"],
            consensus_provider=_FailingConsensusProvider(),
            price_client=_FakePriceClient(),
            wisburg_provider=_FakeWisburgProvider(),
            watchlist="default",
            wisburg_mode="on",
            snapshot_day=date.today(),
            sec_client=_FakeSecClient(),
        )[0]

        self.assertEqual(status.consensus_status, "Unavailable")
        self.assertEqual(status.price_status, "Available")
        self.assertIn(status.wisburg_status, {"Available", "Partial"})
        self.assertEqual(status.overall_status, "Partial")
        self.assertTrue(any("Consensus snapshot failed" in gap for gap in status.data_gaps))


class _FakeConsensusProvider:
    def fetch_package(self, ticker: str, current_price: float | None) -> ConsensusPackage:
        as_of = date.today().isoformat()
        return ConsensusPackage(
            ticker,
            "Fixture consensus",
            "Available",
            target=TargetConsensus(
                ticker,
                as_of,
                target_mean=125.0,
                current_price=current_price,
                source="Fixture consensus",
            ),
        )


class _FailingConsensusProvider:
    def fetch_package(self, ticker: str, current_price: float | None) -> ConsensusPackage:
        raise RuntimeError("fixture consensus outage")


class _FakePriceClient:
    def recent_market_context(self, ticker: str) -> RecentMarketContext:
        return RecentMarketContext(
            ticker=ticker,
            status="Available",
            source="Fixture prices",
            summary="Fixture daily close.",
            price_as_of=date.today().isoformat(),
            current_price=100.0,
        )


class _FakeWisburgProvider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, identity: CompanyIdentity, events: list) -> object:
        self.calls += 1
        return _wisburg_bundle()


class _FakeSecClient:
    def map_ticker(self, ticker: str) -> CompanyIdentity:
        return CompanyIdentity(ticker, "0001577552", "Alibaba Group Holding Ltd")


if __name__ == "__main__":
    unittest.main()
