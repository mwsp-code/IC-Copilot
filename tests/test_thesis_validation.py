from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equity_research import config
from equity_research.models import (
    Citation,
    ConsensusPackage,
    EvidenceClaim,
    EvidenceItem,
    EvidenceLedger,
    ExternalEvidenceBundle,
    HistoricalReferenceSet,
    ManagementSourcePackage,
    ThesisValidationReport,
    TradeIdea,
    ValuationResult,
)
from equity_research.sample_data import demo_result
from equity_research.thesis_validation import build_thesis_validation_report


class ThesisValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_db = config.RESEARCH_DB_PATH
        config.RESEARCH_DB_PATH = Path(self.temporary.name) / "research.db"

    def tearDown(self) -> None:
        config.RESEARCH_DB_PATH = self.original_db
        self.temporary.cleanup()

    def test_demo_result_includes_validation_matrix(self) -> None:
        result = demo_result("AAPL")
        validation = result.thesis_validation

        self.assertIsInstance(validation, ThesisValidationReport)
        self.assertIn(validation.status, {"Validated", "Partially validated", "Weakly validated", "Contested"})
        self.assertGreaterEqual(len(validation.checks), 6)
        self.assertTrue(any(check.channel == "SEC / issuer filings" for check in validation.checks))
        self.assertTrue(any(check.channel == "Valuation / payoff" for check in validation.checks))
        self.assertTrue(validation.next_evidence_actions)
        self.assertNotIn("api_key", str(validation).lower())

    def test_primary_source_contradiction_makes_report_contested(self) -> None:
        result = demo_result("AAPL")
        top = result.ideas[0]
        top.idea_id = "contradicted"
        citation = Citation(
            "SEC filing",
            "https://www.sec.gov/test",
            section="Risk Factors",
            snippet="Demand materially weakened.",
            source_tier=1,
        )
        ledger = EvidenceLedger(
            claims=[
                EvidenceClaim(
                    "claim-1",
                    top.idea_id,
                    top.thesis,
                    "Contradicted",
                    supporting_evidence_ids=[],
                    contradicting_evidence_ids=["counter-1"],
                    strongest_counter="Demand materially weakened.",
                )
            ],
            items=[
                EvidenceItem(
                    "counter-1",
                    "claim-1",
                    "AAPL",
                    "Contradicts",
                    "Demand materially weakened.",
                    1,
                    "SEC filing",
                    4,
                    citation=citation,
                    unresolved=True,
                )
            ],
            unresolved_material_contradictions=1,
        )

        validation = build_thesis_validation_report(
            [top],
            ledger,
            ConsensusPackage("AAPL", "Fixture", "Unavailable"),
            ValuationResult("Non-financial", "Insufficient data"),
            ManagementSourcePackage("AAPL", "Unavailable"),
            ExternalEvidenceBundle("AAPL", "Unavailable"),
            HistoricalReferenceSet("Unavailable", "none", 0, 5),
        )

        self.assertEqual(validation.status, "Contested")
        filing_check = next(check for check in validation.checks if check.channel == "SEC / issuer filings")
        self.assertEqual(filing_check.status, "Contradicts")
        self.assertTrue(validation.strongest_contradictions)
        self.assertTrue(validation.next_evidence_actions)
        self.assertEqual(validation.next_evidence_actions[0].priority, "High")
        self.assertTrue(validation.next_evidence_actions[0].blocks_high_conviction)

    def test_zero_hit_historical_analogs_are_contradictory_not_supportive(self) -> None:
        result = demo_result("BABA")
        validation = build_thesis_validation_report(
            result.ideas,
            result.evidence_ledger,
            result.consensus,
            result.valuation,
            result.management_sources,
            result.external_evidence,
            HistoricalReferenceSet(
                "Supported",
                "financial_kpi / Short / 1-2 quarters",
                8,
                5,
                references=result.historical_references.references,
                hit_rate_pct=0.0,
                summary="Found 8 similar prior idea(s), including 8 with resolved outcomes; hit rate 0.0%.",
            ),
        )

        historical = next(check for check in validation.checks if check.channel == "Historical analogs")
        self.assertEqual(historical.status, "Contradicts")
        self.assertEqual(historical.score, 20)
        self.assertIn("poor outcomes", historical.implication)


if __name__ == "__main__":
    unittest.main()
