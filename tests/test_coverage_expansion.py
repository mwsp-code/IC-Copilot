from __future__ import annotations

import unittest

from equity_research.coverage_expansion import build_coverage_expansion_diagnostics
from equity_research.company_economics import build_company_economics
from equity_research.models import (
    ChangeEvent,
    CompanyIdentity,
    ConsensusPackage,
    EntityResolution,
    FinancialCoverage,
    IdeaGateResult,
    ScoreBreakdown,
    ThesisCluster,
    TradeIdea,
    ValuationResult,
)
from equity_research.peers import peer_universe_for


class CoverageExpansionTests(unittest.TestCase):
    def test_registration_stage_issuer_gets_prospectus_expansion_without_weakening_gates(self) -> None:
        identity = CompanyIdentity("SPCX", "0001181412", "Space Exploration Technologies Corp", exchange="NASDAQ")
        resolution = EntityResolution(
            ticker="SPCX",
            name=identity.name,
            cik=identity.cik,
            exchange="NASDAQ",
            sic="7370",
            sic_description="Services-computer programming",
            listing_status="Registration-stage or newly listed issuer",
            reporting_forms=["424B4", "S-1", "S-1/A"],
            similar_tickers=["SPXC"],
            warning="Confirm the entity before research: SPCX is distinct from SPXC.",
        )
        coverage = FinancialCoverage(
            status="facts_unmapped",
            reason="SEC Company Facts responded, but only filing-fee concepts were found.",
            source="SEC companyfacts",
            registration_forms=["424B4", "S-1"],
            concepts_found=["NetFeeAmt", "TtlOfferingAmt"],
        )
        economics = build_company_economics(identity, [], [], peer_universe_for("SPCX"))
        idea = _idea("Watch", "Candidate")
        diagnostics = build_coverage_expansion_diagnostics(
            identity,
            resolution,
            coverage,
            economics,
            ConsensusPackage("SPCX", "Test", "Unavailable"),
            ValuationResult("Non-financial", "Insufficient data", missing_data=["No operating metrics."]),
            [idea],
            [_cluster("Unmapped")],
            None,
        )

        self.assertEqual(diagnostics.status, "No convincing thesis yet")
        self.assertEqual(diagnostics.coverage_profile, "IPO / registration-stage prospectus workflow")
        self.assertTrue(any(action.area == "Prospectus operating model" for action in diagnostics.recommended_expansions))
        self.assertTrue(any("Only use tagged Inline XBRL" in action.integrity_rule for action in diagnostics.recommended_expansions))
        self.assertTrue(any("registration/prospectus-style coverage" in reason for reason in diagnostics.why_no_convincing_thesis))

    def test_adr_fpi_gets_segment_share_reconciliation_and_attribution_expansions(self) -> None:
        identity = CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Ltd")
        resolution = EntityResolution(
            ticker="BABA",
            name=identity.name,
            cik=identity.cik,
            exchange="HKEX",
            sic=None,
            sic_description=None,
            listing_status="US-listed ADR or foreign private issuer",
            reporting_forms=["20-F", "6-K"],
            adr_ratio=8.0,
        )
        coverage = FinancialCoverage("available", "Company facts available.", "SEC companyfacts", periodic_forms=["20-F"], metrics_count=4)
        economics = build_company_economics(identity, [], [], peer_universe_for("BABA"))
        idea = _idea("Short", "Research-Ready")
        idea.gate_result = IdeaGateResult(
            "Research-Ready",
            eligible=False,
            research_ready=True,
            research_ready_failed=[],
            high_conviction_failed=["Internal valuation does not provide scenario fair values."],
        )
        diagnostics = build_coverage_expansion_diagnostics(
            identity,
            resolution,
            coverage,
            economics,
            ConsensusPackage("BABA", "Test", "Available"),
            ValuationResult("Non-financial", "Insufficient data", missing_data=["No scenario fair values."]),
            [idea],
            [_cluster("Share count / dilution")],
            None,
        )

        areas = {action.area for action in diagnostics.recommended_expansions}
        self.assertEqual(diagnostics.coverage_profile, "ADR/FPI overlay workflow")
        self.assertIn("ADR/FPI segment evidence", areas)
        self.assertIn("ADR share reconciliation", areas)
        self.assertIn("ADR attribution bundle", areas)
        self.assertIn("Valuation bridge", areas)
        self.assertTrue(any("point-in-time" in note.lower() for note in diagnostics.integrity_notes))


def _idea(direction: str, stage: str) -> TradeIdea:
    event = ChangeEvent(
        "financial_kpi",
        "Shares changed",
        "Shares changed materially.",
        4,
        "negative",
        "2026-05-01",
        "SEC",
        metrics={"economic_driver": "Unmapped" if direction == "Watch" else "Share count / dilution"},
    )
    return TradeIdea(
        "idea-1",
        f"{direction} TEST",
        direction,
        direction,
        "Test thesis",
        "1-2 quarters",
        "Next filing",
        "Variant view unavailable",
        [event],
        score=ScoreBreakdown(60, 10, 10, 10, 10, 10, 10),
        stage=stage,
        thesis_grade_status="Watch Item" if direction == "Watch" else "Thesis-grade",
    )


def _cluster(driver: str) -> ThesisCluster:
    return ThesisCluster(
        cluster_id="cluster",
        label=f"Cluster {driver}",
        status="Needs driver mapping" if driver == "Unmapped" else "Promising but incomplete",
        stage="Candidate",
        direction="Watch",
        score=40,
        driver_name=driver,
    )


if __name__ == "__main__":
    unittest.main()
