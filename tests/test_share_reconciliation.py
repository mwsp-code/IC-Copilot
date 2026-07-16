from __future__ import annotations

import unittest

from equity_research.idea_engine import generate_trade_ideas
from equity_research.models import ChangeEvent, Citation, CompanyIdentity
from equity_research.share_reconciliation import extract_share_reconciliation_text, reconcile_share_event


class ShareReconciliationTests(unittest.TestCase):
    def test_extracts_ads_ordinary_buyback_and_split_signals(self) -> None:
        reconciliation = extract_share_reconciliation_text(
            "BABA",
            "The company had 19.2 billion ordinary shares and 2.4 billion ADS outstanding. "
            "Repurchased US$12.5 billion of shares. A share subdivision was completed.",
            adr_ratio=8,
        )

        self.assertEqual(reconciliation.status, "Reconciled")
        self.assertEqual(reconciliation.basis, "ordinary_vs_ads")
        self.assertEqual(reconciliation.ordinary_share_count, 19_200_000_000)
        self.assertEqual(reconciliation.ads_share_count, 2_400_000_000)
        self.assertEqual(reconciliation.buyback_amount, 12_500_000_000)
        self.assertTrue(reconciliation.split_or_corporate_action)

    def test_baba_large_share_move_requires_normalization_before_thesis(self) -> None:
        identity = CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Ltd")
        event = ChangeEvent(
            "financial_kpi",
            "Shares changed -89.9%",
            "Shares was 1.9B shares for 2026-03-31, versus 18.5B previously.",
            5,
            "negative",
            "2026-06-01",
            "SEC Companyfacts",
            citations=[Citation("20-F", "https://example.test/20f", form="20-F", snippet="Shares outstanding table")],
            metrics={"metric_name": "Shares", "yoy_change_pct": -89.9},
        )

        reconciliation = reconcile_share_event(identity, event)
        self.assertIsNotNone(reconciliation)
        self.assertEqual(reconciliation.status, "Needs normalization")
        self.assertTrue(any("ADR/FPI share-count signal" in gap for gap in reconciliation.data_gaps))

        event.metrics["normalization_required"] = True
        event.metrics["normalization_reason"] = reconciliation.data_gaps[0]
        event.metrics["share_reconciliation_status"] = reconciliation.status
        ideas = generate_trade_ideas(identity, [event])
        self.assertEqual(ideas[0].direction, "Watch")
        self.assertIn("needs", ideas[0].normalization_status.lower())


if __name__ == "__main__":
    unittest.main()
