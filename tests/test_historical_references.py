from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from equity_research import config
from equity_research.historical_references import (
    build_historical_references,
    build_historical_references_for_ticker,
)
from equity_research.research_store import ResearchStore
from equity_research.sample_data import demo_result


class HistoricalReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_db = config.RESEARCH_DB_PATH
        config.RESEARCH_DB_PATH = Path(self.temporary.name) / "research.db"
        self.store = ResearchStore()

    def tearDown(self) -> None:
        config.RESEARCH_DB_PATH = self.original_db
        self.temporary.cleanup()

    def test_no_history_returns_explicit_gap(self) -> None:
        current = deepcopy(demo_result("AAPL").ideas[0])
        current.idea_id = "current-no-history"

        references = build_historical_references([current], self.store)

        self.assertEqual(references.status, "Unavailable")
        self.assertIn("No sufficiently similar", references.data_gaps[0])
        self.assertEqual(references.references, [])

    def test_similar_resolved_prior_idea_is_referenceable(self) -> None:
        current = deepcopy(demo_result("AAPL").ideas[0])
        current.idea_id = "current-margin"
        current.stage = "Research-Ready"
        current.signal_family = current.signal_family or current.source_events[0].category

        prior = deepcopy(current)
        prior.idea_id = "prior-margin"
        prior.title = "Prior margin expansion setup"
        prior.stage = "High-Conviction"
        self.store.save_idea_versions("MSFT", "prior-run", [prior])
        self.store.record_realized_outcome(
            prior.idea_id,
            1,
            prior.horizon,
            8.5,
            -2.0,
            11.0,
            "hit",
            "Fixture resolved outcome.",
        )

        references = build_historical_references([current], self.store)

        self.assertIn(references.status, {"Sparse", "Supported"})
        self.assertEqual(references.sample_size, 1)
        self.assertEqual(references.references[0].idea_title, "Prior margin expansion setup")
        self.assertIn("same signal family", references.references[0].match_reasons)
        self.assertEqual(references.hit_rate_pct, 100.0)

    def test_ticker_api_builder_uses_latest_stored_target(self) -> None:
        current = deepcopy(demo_result("AAPL").ideas[0])
        current.idea_id = "current-aapl"
        current.stage = "Research-Ready"
        prior = deepcopy(current)
        prior.idea_id = "prior-aapl"
        prior.title = "Older AAPL setup"
        prior.stage = "High-Conviction"
        self.store.save_idea_versions("AAPL", "older-run", [prior])
        self.store.save_idea_versions("AAPL", "latest-run", [current])

        references = build_historical_references_for_ticker("AAPL", self.store)

        self.assertIn(references.status, {"Sparse", "Referenceable"})
        self.assertTrue(references.references)
        self.assertEqual(references.references[0].idea_title, "Older AAPL setup")


if __name__ == "__main__":
    unittest.main()
