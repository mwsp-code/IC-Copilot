from __future__ import annotations

import unittest

from equity_research.ic_one_pager import build_ic_one_pager
from equity_research.sample_data import demo_result


class ICOnePagerTests(unittest.TestCase):
    def test_demo_result_exposes_structured_ic_one_pager(self) -> None:
        result = demo_result("AAPL")
        one_pager = result.ic_one_pager

        self.assertEqual(one_pager.ticker, "AAPL")
        self.assertEqual(one_pager.verdict, result.thesis_brief.verdict)
        self.assertTrue(one_pager.thesis)
        self.assertTrue(one_pager.causal_bridge)
        self.assertTrue(one_pager.price_move)
        self.assertTrue(one_pager.market_capture)
        self.assertTrue(one_pager.valuation)
        self.assertTrue(one_pager.equity_lens)
        self.assertTrue(one_pager.credit_lens)
        self.assertTrue(one_pager.decision)
        self.assertTrue(one_pager.decision_reason)
        self.assertTrue(one_pager.why_now)
        self.assertTrue(one_pager.next_best_action)
        self.assertTrue(one_pager.rank_eligibility)
        self.assertTrue(one_pager.go_no_go_reason)
        self.assertIn("IC one-pager", result.memo_markdown)
        self.assertIn("Decision:", result.memo_markdown)
        self.assertIn("LLM research assistant", result.memo_markdown)
        self.assertIn("Assistant prohibited actions", result.memo_markdown)

    def test_one_pager_surfaces_work_order_as_gap_not_support(self) -> None:
        result = demo_result("BABA")
        one_pager = build_ic_one_pager(
            result.identity,
            result.thesis_brief,
            result.thesis_critique,
            result.evidence_sufficiency,
            result.ideas,
            result.valuation,
            result.thesis_validation,
            result.evidence_work_order,
            result.company_economics,
            result.credit_lens,
            result.thesis_clusters,
            result.action_plan,
        )

        self.assertTrue(one_pager.work_order_actions)
        self.assertTrue(one_pager.evidence_gaps)
        self.assertNotEqual(one_pager.decision, "Pitch as high-conviction candidate")
        self.assertTrue(one_pager.blocking_issue)
        self.assertTrue(one_pager.next_best_action)
        self.assertTrue(any("[" in item and "]" in item for item in one_pager.work_order_actions))
        self.assertNotIn("High-Conviction proof", " ".join(one_pager.work_order_actions))


if __name__ == "__main__":
    unittest.main()
