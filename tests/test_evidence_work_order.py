from __future__ import annotations

import unittest

from equity_research.evidence_work_order import build_evidence_work_order
from equity_research.models import (
    ChangeEvent,
    CoverageExpansionAction,
    CoverageExpansionDiagnostics,
    EvidenceActionItem,
    MarketCaptureReadiness,
    MarketCaptureSnapshotNeed,
    ResearchQuestion,
    ResearchSourcePlan,
    ResearchSourceRequest,
    ThesisValidationReport,
)
from equity_research.sample_data import demo_result


class EvidenceWorkOrderTests(unittest.TestCase):
    def test_disclosure_observation_generates_alignment_work_order(self) -> None:
        event = ChangeEvent(
            "debt_liquidity",
            "Debt Liquidity discussion detected",
            "Detected without comparable prior section.",
            3,
            "neutral",
            "2026-05-20",
            "20-F",
            metrics={
                "signal_method": "disclosure_change_engine",
                "comparison_status": "no_comparable_prior",
                "comparison_reason_code": "prior_section_missing",
                "disclosure_event_type": "observation",
                "research_work_order": "Retrieve and align the prior 20-F debt liquidity section.",
            },
        )

        order = build_evidence_work_order(None, None, [], None, None, events=[event])

        self.assertEqual(order.status, "Blocks Research-Ready")
        self.assertTrue(any(item.origin == "disclosure_change_engine" for item in order.items))
        self.assertTrue(order.items[0].blocks_research_ready)

    def test_work_order_keeps_market_capture_snapshot_need_as_follow_up(self) -> None:
        readiness = MarketCaptureReadiness(
            ticker="BABA",
            status="Partial",
            summary="Consensus history missing.",
            total_ideas=1,
            classified_ideas=0,
            unknown_ideas=1,
            price_coverage="available",
            consensus_coverage="missing",
            official_consensus_available=False,
            snapshot_needs=[
                MarketCaptureSnapshotNeed(
                    idea_id="idea-1",
                    event_date="2026-05-01",
                    metric_family="revenue",
                    pre_event_snapshot="Need pre-event EPS/revenue snapshot.",
                    post_event_snapshot="Need post-event EPS/revenue snapshot.",
                    accepted_sources=["CSV", "Alpha Vantage", "FMP"],
                    reason="Market capture cannot be classified without point-in-time expectations.",
                )
            ],
        )

        order = build_evidence_work_order(None, readiness, [], None, None)

        self.assertEqual(order.status, "Follow-up available")
        self.assertTrue(order.items)
        self.assertEqual(order.items[0].priority, "High")
        self.assertFalse(order.items[0].blocks_high_conviction)
        self.assertEqual(order.items[0].source_type, "consensus_manual")
        self.assertIn("pre/post consensus snapshots", order.items[0].action)
        self.assertTrue(order.items[0].acceptance_criteria)
        self.assertTrue(order.items[0].falsification_tests)

    def test_work_order_combines_research_questions_validation_and_coverage_actions(self) -> None:
        validation = ThesisValidationReport(
            status="Weakly validated",
            score=35,
            summary="Top thesis needs corroboration.",
            top_idea_id="idea-1",
            top_idea_title="Long TEST",
            next_evidence_actions=[
                EvidenceActionItem(
                    channel="Valuation / payoff",
                    priority="High",
                    action="Build scenario fair values.",
                    source="presentation",
                    why_it_matters="The idea has no payoff anchor.",
                    blocks_high_conviction=True,
                )
            ],
        )
        question = ResearchQuestion(
            question_id="rq-1",
            title="Does the source signal map to durable segment demand?",
            priority="High",
            status="Open",
            driver_name="Revenue / demand",
            source_signal="Revenue moved.",
            why_it_matters="Revenue growth must be tied to volume, price, mix, or FX.",
            required_evidence=["Segment revenue bridge"],
            primary_source_types=["issuer_ir"],
            acceptance_criteria=["Segment revenue and KPI table are period aligned."],
            falsification_tests=["Growth is FX-only or one-time."],
            related_idea_ids=["idea-1"],
        )
        coverage = CoverageExpansionDiagnostics(
            ticker="TEST",
            status="No convincing thesis yet",
            coverage_profile="Standard U.S. operating-company workflow",
            summary="Need better source coverage.",
            recommended_expansions=[
                CoverageExpansionAction(
                    "Valuation bridge",
                    "High",
                    "presentation",
                    "Build scenario fair values.",
                    "EV should be based on explicit exits.",
                    "Use cited operating assumptions only.",
                    "Scenario fair values.",
                )
            ],
        )
        source_plan = ResearchSourcePlan(
            ticker="TEST",
            status="Available",
            generated_at="2026-07-10T00:00:00+00:00",
            registry_version="test",
            requests=[
                ResearchSourceRequest(
                    "req-1",
                    "issuer_ir",
                    "Check segment KPI bridge",
                    "Segment evidence is missing.",
                    "Segment KPI table",
                    "Medium",
                    "Free / variable latency",
                    "Confirms or disproves the driver bridge.",
                )
            ],
        )

        order = build_evidence_work_order(validation, None, [question], source_plan, coverage)

        self.assertEqual(order.status, "Blocks Research-Ready")
        self.assertGreaterEqual(len(order.items), 3)
        self.assertTrue(any(item.origin == "research_question" and item.blocks_research_ready for item in order.items))
        self.assertTrue(any("thesis_validation" in item.origin and item.blocks_high_conviction for item in order.items))
        self.assertTrue(any("explicit exits" in item.why_it_matters for item in order.items))

    def test_demo_payload_exposes_evidence_work_order(self) -> None:
        result = demo_result("AAPL")

        self.assertIsNotNone(result.evidence_work_order)
        self.assertIn(result.evidence_work_order.status, {"Blocks Research-Ready", "Blocks High-Conviction", "Follow-up available", "No work order"})
        self.assertIn("Evidence Work Order", result.memo_markdown)


if __name__ == "__main__":
    unittest.main()
