from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equity_research.analysis import financial_change_events
from equity_research.expectations import build_expectations_bridge
from equity_research.idea_engine import generate_trade_ideas, score_idea
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyEconomics,
    CompanyIdentity,
    EvidenceLedger,
    ExpectationEventAudit,
    ExternalEvidenceBundle,
    FinancialMetric,
    IndustryPlaybook,
    ManagementSourcePackage,
    PromotionEvidenceBundle,
    PromotionGateDecision,
    PromotionSourceCheck,
    ResearchSourcePlan,
    WisburgResearchLens,
    ConsensusPackage,
    EstimatePoint,
)
from equity_research.playbook_portfolio import build_playbook_portfolio
from equity_research.promotion_evidence import decide_promotion
from equity_research.research_profiles import (
    PROFILE_ADAPTIVE,
    PROFILE_DEEP,
    PROFILE_EVENT,
    event_identifier,
    resolve_research_profile,
    select_profile_events,
)
from equity_research.research_store import ResearchStore


class AdaptiveIcResearchTests(unittest.TestCase):
    def test_adaptive_is_default_with_requested_history(self) -> None:
        profile = resolve_research_profile(None)
        self.assertEqual(profile.profile_id, PROFILE_ADAPTIVE)
        self.assertEqual((profile.quarter_depth, profile.annual_depth, profile.call_depth), (12, 4, 12))
        deep = resolve_research_profile(PROFILE_DEEP)
        self.assertEqual((deep.quarter_depth, deep.annual_depth, deep.call_depth), (20, 5, 20))

    def test_event_profile_selects_only_requested_event(self) -> None:
        events = [_event("Revenue changed", 5), _event("Goodwill changed", 4)]
        selected_id = event_identifier(events[1])
        selected = select_profile_events(events, resolve_research_profile(PROFILE_EVENT), selected_id)
        self.assertEqual([event_identifier(item) for item in selected], [selected_id])
        self.assertEqual(
            select_profile_events(events, resolve_research_profile(PROFILE_EVENT), "missing-event"),
            [],
        )

    def test_goodwill_and_capex_are_neutral_dual_hypothesis_watch_items(self) -> None:
        metrics = [
            _metric("Goodwill", 20.9, 5.5, 280.0),
            _metric("Capital Expenditure", 1.8, 1.2, 50.0),
        ]
        events = financial_change_events(metrics, "https://data.sec.gov/companyfacts")
        by_metric = {item.metrics["metric_name"]: item for item in events}
        self.assertEqual(by_metric["Goodwill"].direction, "neutral")
        self.assertEqual(by_metric["Goodwill"].metrics["driver_family"], "acquisition_accounting")
        self.assertIn("acquisition", by_metric["Goodwill"].metrics["metric_interpretation"].lower())
        self.assertEqual(by_metric["Capital Expenditure"].direction, "neutral")
        self.assertEqual(by_metric["Capital Expenditure"].metrics["driver_family"], "investment_cycle")
        ideas = generate_trade_ideas(CompanyIdentity("NVDA", "1045810", "NVIDIA"), events, metrics=metrics)
        self.assertTrue(all(item.direction == "Watch" for item in ideas))
        goodwill = next(item for item in ideas if "Goodwill" in item.title)
        self.assertNotIn("Revenue growth", goodwill.driver_analysis.headline)
        self.assertEqual(len(goodwill.driver_analysis.factors), 2)

    def test_expectation_gap_names_exact_event_period_and_metric(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory) / "research.db")
            package = ConsensusPackage(
                "NVDA",
                "Fixture",
                "Available",
                estimates=[EstimatePoint(
                    "NVDA", "2026-07-01", "Revenue", "2026-04-26", "quarter",
                    average=45.0, source="Fixture", official=True,
                )],
            )
            event = ChangeEvent(
                "financial_kpi", "Capital Expenditure changed +43.2%", "Capex changed.", 5,
                "neutral", "2026-05-20", "10-Q",
                [Citation(
                    "10-Q", "https://example.com/nvda-10q", filed="2026-05-20", form="10-Q",
                    accession="0001045810-26-000001", period_end="2026-04-26", snippet="Capital expenditure increased.",
                )],
                {"metric_name": "Capital Expenditure"},
            )
            bridge = build_expectations_bridge("NVDA", package, [_metric("Capital Expenditure", 1.8, 1.2, 50)], store, [event])
            audit = bridge.event_audits[0]
            self.assertIn("0001045810-26-000001", audit.event_label)
            self.assertEqual(audit.reporting_period, "2026-04-26")
            self.assertIn("Capital Expenditure", audit.actual_metrics_checked)
            self.assertEqual(audit.reason_code, "pre_event_snapshot_missing")
            self.assertIn("Capital Expenditure", audit.reason)

    def test_two_independent_tier3_sources_can_substitute_only_primary_gate(self) -> None:
        sources = [
            _promotion_source("s1", "Reuters", "reuters", "fp1"),
            _promotion_source("s2", "Institutional report", "institution_b", "fp2"),
        ]
        bundle = PromotionEvidenceBundle(
            "idea-1", "Eligible exception", True, "Primary filing does not disclose the fact.",
            eligible_tier3_sources=sources,
            independent_origin_count=2,
            tier1_contradiction=False,
            quantitative_bridge_supported=True,
            substituted_gate="Tier 1 primary support",
        )
        decision = decide_promotion(bundle)
        self.assertTrue(decision.eligible)
        self.assertEqual(decision.substituted_gate, "Tier 1 primary support")
        self.assertEqual(decision.score_cap, 75)
        self.assertEqual(decision.label, "High-Conviction: secondary-supported")

    def test_single_or_syndicated_tier3_source_cannot_qualify(self) -> None:
        bundle = PromotionEvidenceBundle(
            "idea-1", "Ineligible", True, "Primary source inaccessible.",
            eligible_tier3_sources=[_promotion_source("s1", "Newswire", "same_owner", "same")],
            independent_origin_count=1,
            quantitative_bridge_supported=True,
        )
        decision = decide_promotion(bundle)
        self.assertFalse(decision.eligible)
        self.assertTrue(any("Fewer than two" in item for item in decision.failed_checks))

    def test_secondary_supported_score_is_capped_at_75(self) -> None:
        event = _event("Revenue changed +30%", 5)
        idea = generate_trade_ideas(
            CompanyIdentity("TEST", "1", "Test Company"), [event], metrics=[_metric("Revenue", 130, 100, 30)],
        )[0]
        idea.promotion_decision = PromotionGateDecision(
            idea.idea_id, "Eligible", "High-Conviction: secondary-supported", True,
            "Tier 1 primary support", 75,
        )
        score = score_idea(idea, evidence=EvidenceLedger())
        self.assertLessEqual(score.total, 75)
        self.assertEqual(score.score_cap, 75)

    def test_company_can_have_validated_secondary_playbooks(self) -> None:
        identity = CompanyIdentity("BABA", "1577552", "Alibaba Group")
        economics = CompanyEconomics(
            "BABA", "Available", "Digital commerce company with cloud operations.",
            IndustryPlaybook("Internet retail", "Ecommerce", key_kpis=["GMV"]),
        )
        events = [ChangeEvent(
            "financial_kpi", "Cloud revenue growth", "Cloud computing and AI services revenue increased.",
            4, "positive", "2026-05-20", "20-F",
            [Citation("20-F", "https://example.com/baba", accession="a1", snippet="Cloud and AI revenue grew.")],
        )]
        portfolio = build_playbook_portfolio(
            identity, economics, events, ManagementSourcePackage("BABA", "Unavailable"),
        )
        self.assertEqual(portfolio.primary.role, "Primary")
        self.assertTrue(any(item.playbook_id == "cloud_platform" for item in portfolio.secondary))
        self.assertLessEqual(len(portfolio.secondary), 2)


def _event(title: str, severity: int) -> ChangeEvent:
    return ChangeEvent(
        "financial_kpi", title, title, severity, "positive", "2026-05-20", "10-Q",
        [Citation(
            "10-Q", "https://example.com/filing", filed="2026-05-20", form="10-Q",
            accession=title.replace(" ", "-").lower(), period_end="2026-04-26", snippet=title,
        )],
        {"metric_name": "Revenue", "economic_driver": "Revenue growth / demand", "driver_materiality": "High"},
    )


def _metric(name: str, value: float, previous: float, change: float) -> FinancialMetric:
    return FinancialMetric(
        name, value, "USD", "2026-04-26", "Q1", 2026, "10-Q", "2026-05-20",
        previous, change, "https://example.com/facts", "a1", "companyfacts",
    )


def _promotion_source(
    source_id: str, provider: str, origin: str, fingerprint: str,
) -> PromotionSourceCheck:
    return PromotionSourceCheck(
        source_id, provider, 3, origin, fingerprint, "2026-05-20",
        True, True, True, True, "Eligible Tier 3 corroboration candidate.",
    )


if __name__ == "__main__":
    unittest.main()
