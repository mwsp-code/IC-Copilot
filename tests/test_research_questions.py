from __future__ import annotations

import unittest

from equity_research.models import (
    ChangeEvent,
    CompanyEconomics,
    IndustryPlaybook,
    MarketCapture,
    ResearchSourcePlan,
    ResearchSourceRequest,
    ScoreBreakdown,
    ThesisAuditChain,
    ThesisAuditStep,
    ThesisCluster,
    TradeIdea,
)
from equity_research.research_questions import build_research_questions


class ResearchQuestionTests(unittest.TestCase):
    def test_incomplete_thesis_chain_becomes_research_question(self) -> None:
        economics = _economics()
        idea = _idea(
            "Research-Ready",
            MarketCapture(
                "Unknown",
                price_reaction_pct=4.0,
                consensus_revision_pct=None,
                narrative_saturation="Unknown",
                explanation="Price reaction exists, but consensus history is missing.",
                data_gaps=["Official point-in-time consensus revisions are unavailable."],
                consensus_official=False,
            ),
        )
        cluster = ThesisCluster(
            "long-revenue-growth-demand",
            "Long thesis cluster: Revenue growth / demand",
            "Promising but incomplete",
            "Research-Ready",
            "Long",
            68,
            idea_ids=[idea.idea_id],
            driver_name="Revenue growth / demand",
            evidence_gaps=["Market capture is unresolved."],
        )
        source_plan = ResearchSourcePlan(
            "AAPL",
            "Available",
            "2026-07-09T00:00:00+00:00",
            "test",
            [
                ResearchSourceRequest(
                    "req-consensus",
                    "consensus_manual",
                    "Seed point-in-time consensus revisions",
                    "Revenue thesis needs market-capture evidence.",
                    "EPS/revenue/target snapshots",
                    "High",
                    "Manual / free",
                    "Confirms whether the revenue signal was priced in.",
                )
            ],
        )

        questions = build_research_questions([idea], [cluster], economics, source_plan)

        self.assertEqual(len(questions), 1)
        question = questions[0]
        self.assertEqual(question.priority, "High")
        self.assertEqual(question.answerability_status, "Answerable after market-capture inputs")
        self.assertGreaterEqual(question.answerability_score, 70)
        self.assertTrue(any("market-capture" in item.lower() for item in question.answerability_gaps))
        self.assertIn("Answer yes only if", question.decision_rule)
        self.assertIn("Test whether", question.hypothesis)
        self.assertTrue(question.minimum_evidence_package)
        self.assertTrue(any("Market-capture input" in item for item in question.minimum_evidence_package))
        self.assertIn("yes/no/insufficient", question.answer_format)
        self.assertIn("Stop", question.stop_condition)
        self.assertIn("Market capture", question.missing_links)
        self.assertTrue(any("Demand bridge" in item for item in question.required_evidence))
        self.assertTrue(any("Issuer segment revenue" in item for item in question.primary_source_types))
        self.assertTrue(any("metric, period, unit" in item for item in question.acceptance_criteria))
        self.assertTrue(any("Industry demand" in item for item in question.falsification_tests))
        self.assertTrue(any("point-in-time market-capture" in item for item in question.workplan_steps))
        self.assertTrue(any("consensus" in item.lower() for item in question.market_capture_needs))
        self.assertTrue(any("Seed point-in-time" in item for item in question.next_sources))
        self.assertIn("Validated source claim", question.promotion_criteria[0])

    def test_margin_question_includes_driver_specific_source_and_falsification_tests(self) -> None:
        economics = _economics()
        idea = _idea(
            "Candidate",
            MarketCapture(
                "Unknown",
                price_reaction_pct=None,
                consensus_revision_pct=None,
                narrative_saturation="Unknown",
                explanation="Price reaction and consensus history are missing.",
                data_gaps=["Event price reaction is unavailable."],
            ),
            metric_name="Gross Profit",
            driver_name="Gross margin / mix",
            title="Gross Profit changed +50%",
        )
        cluster = ThesisCluster(
            "long-gross-margin-mix",
            "Long thesis cluster: Gross margin / mix",
            "Early research",
            "Candidate",
            "Long",
            58,
            idea_ids=[idea.idea_id],
            driver_name="Gross margin / mix",
            evidence_gaps=["Causal bridge is incomplete."],
        )

        question = build_research_questions([idea], [cluster], economics)[0]

        self.assertTrue(any("Margin bridge" in item for item in question.required_evidence))
        self.assertTrue(any("Driver evidence" in item for item in question.minimum_evidence_package))
        self.assertIn("Gross margin / mix", question.answer_format)
        self.assertIn(question.answerability_status, {"Answerable after market-capture inputs", "Answerable with listed workplan"})
        self.assertGreaterEqual(question.answerability_score, 65)
        self.assertTrue(any("Issuer MD&A margin discussion" in item for item in question.primary_source_types))
        self.assertTrue(any("COGS" in item for item in question.falsification_tests))
        self.assertTrue(any("causal bridge" in item.lower() for item in question.workplan_steps))

    def test_unmapped_question_requires_driver_mapping_before_answering(self) -> None:
        economics = _economics()
        idea = _idea(
            "Candidate",
            MarketCapture(
                "Unknown",
                price_reaction_pct=None,
                consensus_revision_pct=None,
                narrative_saturation="Unknown",
                explanation="Price reaction and consensus history are missing.",
            ),
            metric_name="Keyword Count",
            driver_name="Unmapped",
            title="Keyword signal changed",
        )
        cluster = ThesisCluster(
            "watch-unmapped",
            "Unmapped signal: keyword count",
            "Needs driver mapping",
            "Candidate",
            "Watch",
            35,
            idea_ids=[idea.idea_id],
            driver_name="Unmapped",
            evidence_gaps=["Signal is not mapped to a material company or industry driver."],
        )

        question = build_research_questions([idea], [cluster], economics)[0]

        self.assertEqual(question.answerability_status, "Driver mapping needed")
        self.assertIn("Unmapped", question.hypothesis)
        self.assertIn("Primary source", question.stop_condition)
        self.assertTrue(any("driver" in item.lower() for item in question.answerability_gaps))
        self.assertIn("Otherwise keep the idea as Watch/Candidate", question.decision_rule)

    def test_complete_high_conviction_cluster_is_not_question(self) -> None:
        economics = _economics()
        idea = _idea(
            "High-Conviction",
            MarketCapture(
                "Uncaptured",
                price_reaction_pct=1.0,
                consensus_revision_pct=5.0,
                narrative_saturation="Low",
                explanation="Consensus moved more than price.",
                consensus_official=True,
            ),
            audit_status="Complete",
            broken_links=[],
        )
        cluster = ThesisCluster(
            "long-revenue-growth-demand",
            "Long thesis cluster: Revenue growth / demand",
            "IC-ready",
            "High-Conviction",
            "Long",
            82,
            idea_ids=[idea.idea_id],
            driver_name="Revenue growth / demand",
        )

        self.assertEqual(build_research_questions([idea], [cluster], economics), [])


def _economics() -> CompanyEconomics:
    return CompanyEconomics(
        "AAPL",
        "Available",
        "Device and services business model.",
        IndustryPlaybook(
            "Large-cap consumer technology / devices and services",
            "technology",
            key_kpis=["Revenue", "Gross margin", "Services growth", "FCF"],
        ),
    )


def _idea(
    stage: str,
    capture: MarketCapture,
    *,
    audit_status: str = "Incomplete",
    broken_links: list[str] | None = None,
    metric_name: str = "Revenue",
    driver_name: str = "Revenue growth / demand",
    title: str = "Revenue changed +12%",
) -> TradeIdea:
    broken = broken_links if broken_links is not None else ["Market capture"]
    event = ChangeEvent(
        "financial_kpi",
        title,
        f"{metric_name} was 112B USD, versus 100B previously.",
        4,
        "positive",
        "2026-05-01",
        "SEC Companyfacts",
        metrics={
            "metric_name": metric_name,
            "economic_driver": driver_name,
            "driver_materiality": "High",
            "driver_why_it_matters": f"{driver_name} drives the economic bridge.",
        },
    )
    idea = TradeIdea(
        "idea-1",
        f"Long AAPL: {driver_name}",
        "Long",
        "Long equity",
        "Revenue growth may support the thesis if demand quality is confirmed.",
        "1-2 quarters",
        "Next earnings",
        "Variant view needs consensus history.",
        [event],
        market_capture=capture,
        score=ScoreBreakdown(68, 20, 10, 10, 8, 0, 5),
        stage=stage,
        signal_family="financial_kpi",
        equity_credit_lens={
            "equity": "Equity lens: demand quality matters through growth durability.",
            "credit": "Credit lens: revenue growth helps only if cash conversion improves.",
        },
    )
    idea.thesis_audit_chain = ThesisAuditChain(
        idea.idea_id,
        audit_status,
        "No convincing thesis yet." if broken else "Thesis chain complete.",
        steps=[
            ThesisAuditStep("Market capture", "Unknown" if broken else "Passed", "Consensus history missing.", [], list(capture.data_gaps)),
        ],
        broken_links=broken,
        next_actions=["Seed point-in-time consensus history."],
    )
    return idea


if __name__ == "__main__":
    unittest.main()
