from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equity_research import config
from equity_research.models import (
    ChangeEvent,
    CompanyDriver,
    CompanyEconomics,
    CompanyIdentity,
    EvidenceWorkOrder,
    EvidenceWorkOrderItem,
    IndustryPlaybook,
    PeerDefinition,
    PeerUniverse,
    ResearchSourcePlan,
    ResearchSourceRequest,
    TradeIdea,
)
from equity_research.peers import peer_universe_for
from equity_research.research_scout import build_research_scout_report
from equity_research.sample_data import demo_result
from equity_research.thesis_synthesis import build_prompt_pack


class ResearchScoutTests(unittest.TestCase):
    def test_china_adr_report_includes_segment_geography_and_source_questions(self) -> None:
        identity = CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Ltd", sic_description="Retail-catalog and mail-order houses")
        idea = TradeIdea(
            "idea-baba-margin",
            "Gross margin / mix changed",
            "Long",
            "Long equity",
            "Margin signal needs China segment confirmation.",
            "1-2 quarters",
            "Next earnings",
            "Market may miss segment mix.",
            [
                ChangeEvent(
                    "financial_kpi",
                    "Gross Profit changed",
                    "Gross profit increased.",
                    3,
                    "Long",
                    "2026-03-31",
                    "SEC XBRL Companyfacts",
                    metrics={"economic_driver": "Gross margin / mix", "metric_name": "Gross Profit"},
                )
            ],
            signal_family="financial_kpi",
        )
        report = build_research_scout_report(
            identity,
            [idea],
            _economics("BABA"),
            PeerUniverse(
                "BABA",
                "Configured",
                "China consumer internet",
                "test",
                "2026-07-12",
                [PeerDefinition("JD", "China retail peer"), PeerDefinition("PDD", "Marketplace peer")],
                ["Revenue", "Gross margin", "Segment EBITA"],
            ),
            ResearchSourcePlan(
                "BABA",
                "Available",
                "2026-07-12T00:00:00+00:00",
                "test",
                [
                    ResearchSourceRequest(
                        "req-segment",
                        "issuer_ir",
                        "Check segment margin bridge",
                        "Segment economics must explain the margin signal.",
                        "Segment revenue and EBITA",
                        "High",
                        "Free / moderate latency",
                        "Confirm or disprove China commerce and cloud margin durability.",
                    )
                ],
            ),
            EvidenceWorkOrder(
                "Open",
                "One action",
                [
                    EvidenceWorkOrderItem(
                        "wo-1",
                        "High",
                        "company",
                        "Extract segment table from results deck",
                        "issuer_ir",
                        "Segment KPI table",
                        "Needed for causal bridge.",
                        "test",
                        related_idea_ids=["idea-baba-margin"],
                    )
                ],
            ),
        )

        self.assertEqual(report.status, "Available")
        question_text = " ".join(question.question for question in report.questions)
        self.assertIn("China ADR margins", question_text)
        self.assertTrue(any("ADR/FPI profile" in axis for axis in report.geography_story_axes))
        self.assertTrue(any("KWEB" in axis or "MCHI" in axis for axis in report.geography_story_axes))
        self.assertTrue(any("segment" in question.expected_evidence.lower() for question in report.questions))
        self.assertTrue(any("JD" in axis and "PDD" in axis for axis in report.peer_story_axes))

    def test_peer_universe_can_be_extended_from_csv(self) -> None:
        original = config.PEER_UNIVERSE_CSV
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "peer_universes.csv"
            path.write_text(
                "ticker,sector_template,key_metrics,peers,provenance,effective_date,reason\n"
                "XYZ,Global software,Revenue|ARR|FCF,ABC:Cloud peer|DEF:ERP peer,Test CSV,2026-07-12,Configured by test\n",
                encoding="utf-8",
            )
            config.PEER_UNIVERSE_CSV = path
            try:
                universe = peer_universe_for("XYZ")
            finally:
                config.PEER_UNIVERSE_CSV = original

        self.assertEqual(universe.status, "Configured")
        self.assertEqual(universe.sector_template, "Global software")
        self.assertEqual([peer.ticker for peer in universe.peers], ["ABC", "DEF"])
        self.assertIn("ARR", universe.key_metrics)
        self.assertEqual(universe.provenance, "Test CSV")

    def test_prompt_pack_includes_research_scout_context(self) -> None:
        result = demo_result("BABA")
        prompt = build_prompt_pack(
            result.identity,
            result.ideas,
            result.evidence_ledger,
            result.valuation,
            result.data_quality,
            result.management_credibility,
            result.expectations_bridge,
            result.management_sources,
            result.external_evidence,
            result.calibration,
            company_economics=result.company_economics,
            thesis_clusters=result.thesis_clusters,
            research_questions=result.research_questions,
            research_scout=result.research_scout,
            source_plan=result.source_plan,
            evidence_work_order=result.evidence_work_order,
        )

        self.assertIn("research_scout", prompt)
        self.assertGreaterEqual(len(prompt["research_scout"]["questions"]), 1)
        self.assertTrue(any("research_scout" in rule for rule in prompt["rules"]))


def _economics(ticker: str) -> CompanyEconomics:
    return CompanyEconomics(
        ticker,
        "Available",
        "Company generates revenue through commerce, cloud, and services.",
        IndustryPlaybook(
            "China internet",
            "china_internet",
            key_kpis=["Revenue", "Gross margin", "Segment EBITA"],
            leading_indicators=["China retail sales", "Cloud revenue", "RMB/USD"],
            valuation_methods=["Forward P/E", "EV/Revenue", "FCF yield"],
            macro_sensitivities=["China consumption", "Policy risk", "RMB/USD"],
            playbook_source="test",
        ),
        drivers=[
            CompanyDriver(
                "Gross margin / mix",
                "margin",
                "High",
                "Gross profit increased.",
                why_it_matters="Segment mix can change margin durability.",
                source="test",
            )
        ],
        playbook_quality_score=80,
    )


if __name__ == "__main__":
    unittest.main()
