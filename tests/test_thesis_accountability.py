from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from equity_research.idea_engine import finalize_idea_research, generate_trade_ideas
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyIdentity,
    ConsensusPackage,
    FilingRecord,
    ResearchSourcePlan,
    ResearchSourceRequest,
    ValuationBridgeStep,
    ValuationCase,
    ValuationResult,
)
from equity_research.providers import ConsensusAdapter, PriceReaction
from equity_research.research_store import ResearchStore
from equity_research.rigor import build_calibration_report, build_evidence_ledger
from equity_research.sample_data import demo_result
from equity_research.thesis_accountability import attach_thesis_audit_chains, build_event_workflow


class _FlatConsensus(ConsensusAdapter):
    official_for_conviction = True

    def revision_since(self, ticker, event_date):
        return 0.0


class ThesisAccountabilityTests(unittest.TestCase):
    def test_audit_chain_and_score_dimensions_are_attached_to_idea(self) -> None:
        identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.")
        event = ChangeEvent(
            "financial_kpi",
            "Revenue changed +12%",
            "Revenue was 112B USD, versus 100B previously.",
            4,
            "positive",
            "2026-05-01",
            "SEC Companyfacts",
            citations=[
                Citation(
                    "10-Q",
                    "https://example.test/aapl-10q",
                    filed="2026-05-01",
                    section="MD&A",
                    snippet="Revenue increased due to higher Services demand.",
                    accession="0000320193-26-000001",
                )
            ],
            metrics={
                "metric_name": "Revenue",
                "economic_driver": "Revenue growth / demand",
                "driver_materiality": "High",
                "thesis_grade_status": "Thesis-grade",
                "validated_claim_id": "claim-1",
                "changed_text": "Revenue increased due to higher Services demand.",
            },
        )
        ideas = generate_trade_ideas(
            identity,
            [event],
            PriceReaction("AAPL", "2026-05-01", 100, 101, 1, "Fixture"),
            _FlatConsensus(),
        )
        evidence = build_evidence_ledger("AAPL", ideas, [event])
        valuation = _valuation()
        finalize_idea_research(ideas, valuation, evidence, 100)
        attach_thesis_audit_chains(ideas, valuation)

        idea = ideas[0]
        self.assertIsNotNone(idea.thesis_audit_chain)
        self.assertEqual([step.step for step in idea.thesis_audit_chain.steps], [
            "Source excerpt",
            "Validated claim",
            "Business driver",
            "Valuation / payoff impact",
            "Market capture",
            "Counter-thesis",
            "Monitor rule",
        ])
        counter_step = next(step for step in idea.thesis_audit_chain.steps if step.step == "Counter-thesis")
        self.assertIn(counter_step.status, {"Passed", "Weak"})
        self.assertGreaterEqual(idea.score.research_quality, 0)
        self.assertGreater(idea.score.evidence_strength_score, 0)
        self.assertGreaterEqual(idea.score.actionability, 0)
        self.assertEqual(idea.payoff_model.status, "Available")
        self.assertGreaterEqual(idea.score.actionability, 65)
        self.assertTrue(
            idea.next_source_to_check.startswith(("Research-Ready gate blocker:", "High-Conviction blocker:"))
        )

    def test_price_only_market_capture_does_not_break_thesis_audit_chain(self) -> None:
        identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.")
        event = ChangeEvent(
            "financial_kpi",
            "Revenue changed +12%",
            "Revenue was 112B USD, versus 100B previously.",
            4,
            "positive",
            "2026-05-01",
            "SEC Companyfacts",
            citations=[
                Citation(
                    "10-Q",
                    "https://example.test/aapl-10q",
                    filed="2026-05-01",
                    section="MD&A",
                    snippet="Revenue increased due to higher Services demand.",
                    accession="0000320193-26-000001",
                    period_end="2026-03-31",
                    source_tier=1,
                )
            ],
            metrics={
                "metric_name": "Revenue",
                "economic_driver": "Revenue growth / demand",
                "driver_materiality": "High",
                "thesis_grade_status": "Thesis-grade",
                "changed_text": "Revenue increased due to higher Services demand.",
            },
        )
        ideas = generate_trade_ideas(
            identity,
            [event],
            PriceReaction("AAPL", "2026-05-01", 100, 101, 1, "Fixture"),
            None,
        )
        evidence = build_evidence_ledger("AAPL", ideas, [event])
        valuation = _valuation()
        finalize_idea_research(ideas, valuation, evidence, 100)
        attach_thesis_audit_chains(ideas, valuation)

        idea = ideas[0]
        capture_step = next(step for step in idea.thesis_audit_chain.steps if step.step == "Market capture")
        self.assertEqual(idea.market_capture.capture_mode, "Price-only")
        self.assertEqual(capture_step.status, "Price-only")
        self.assertNotIn("Market capture", idea.thesis_audit_chain.broken_links)
        self.assertIn("Counter-thesis", idea.thesis_audit_chain.broken_links)
        if idea.gate_result:
            self.assertFalse(
                any("Official consensus is required" in gap for gap in idea.gate_result.high_conviction_failed)
            )
        idea.strongest_counter_thesis = "Services demand may normalize next quarter."
        attach_thesis_audit_chains(ideas, valuation)
        self.assertEqual(idea.thesis_audit_chain.status, "Complete")
        self.assertIn("market capture is price-only", idea.thesis_audit_chain.summary)

    def test_counter_thesis_audit_treats_none_found_as_gap(self) -> None:
        idea = deepcopy(demo_result("AAPL").ideas[0])
        idea.strongest_counter_thesis = "No material counter-evidence identified in the current run."
        valuation = _valuation()

        attach_thesis_audit_chains([idea], valuation)
        counter_step = next(step for step in idea.thesis_audit_chain.steps if step.step == "Counter-thesis")

        self.assertEqual(counter_step.status, "Weak")
        self.assertIn("diligence gap", counter_step.summary)
        self.assertIn("Counter-thesis", idea.thesis_audit_chain.broken_links)

    def test_event_workflow_combines_filing_consensus_source_plan_and_monitors(self) -> None:
        identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.")
        event = ChangeEvent(
            "guidance",
            "Guidance changed",
            "Management quantified outlook.",
            4,
            "positive",
            "2026-05-01",
            "8-K",
            metrics={"economic_driver": "Guidance / expectations", "driver_materiality": "High"},
        )
        ideas = generate_trade_ideas(identity, [event])
        plan = ResearchSourcePlan(
            "AAPL",
            "Available",
            "2026-05-01T00:00:00+00:00",
            "test-registry",
            [
                ResearchSourceRequest(
                    "req-1",
                    "earnings_transcript",
                    "Inspect Q&A",
                    "Guidance needs Q&A corroboration.",
                    "Speaker-turn evidence",
                    "High",
                    "Free",
                    "Confirms or disproves management guidance.",
                )
            ],
        )
        workflow = build_event_workflow(
            "AAPL",
            [_filing()],
            ideas,
            plan,
            ConsensusPackage("AAPL", "CSV", "Unavailable"),
        )

        item_types = {item.item_type for item in workflow.items}
        self.assertIn("filing_window", item_types)
        self.assertIn("consensus_history", item_types)
        self.assertIn("source_plan", item_types)

    def test_research_ready_outcomes_are_stored_for_future_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            for index in range(30):
                store.record_event_signal(
                    signal_id=f"rr-{index}",
                    ticker="AAPL",
                    signal_type="margin",
                    event_date=f"2025-02-{(index % 28) + 1:02d}",
                    direction="positive",
                    expected_return_pct=3.0,
                    predicted_success_probability=0.5,
                    realized_return_pct=1.0,
                    abnormal_return_pct=None,
                    stage="Research-Ready",
                    horizon_label="1-2 quarters",
                )
            probability, sample = store.calibrated_probability("margin", "1-2 quarters")
            report = build_calibration_report(store)

        self.assertEqual(sample, 30)
        self.assertAlmostEqual(probability, 1.0)
        self.assertEqual(report.status, "Calibrated")
        self.assertTrue(report.rank_by_ev_allowed)
        self.assertEqual(report.outcomes_needed_for_calibration, 0)
        self.assertEqual(report.nearest_calibration_sample_size, 30)
        self.assertEqual(report.slices[0].status, "Calibrated")
        self.assertTrue(report.slices[0].rank_by_ev_allowed)
        self.assertEqual(report.slices[0].outcomes_needed_for_calibration, 0)
        self.assertIn("EV ranking", report.slices[0].next_action)
        self.assertTrue(report.calibration_actions)
        self.assertGreaterEqual(report.readiness_score, 50)
        checks = {item.area: item for item in report.readiness_checks}
        self.assertEqual(checks["Comparable outcome sample"].status, "Passed")
        self.assertEqual(checks["Comparable outcome sample"].score, 100)
        self.assertEqual(checks["Post-mortem quality"].status, "Unavailable")
        self.assertTrue(report.rank_by_ev_allowed)

    def test_post_mortem_records_outcome_and_calibration_signal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            idea = deepcopy(demo_result("AAPL").ideas[0])
            idea.idea_id = "post-mortem-fixture"
            idea.stage = "Research-Ready"
            idea.signal_family = idea.signal_family or idea.source_events[0].category
            idea.horizon = "1-2 quarters"
            store.save_idea_versions("AAPL", "post-run", [idea])

            outcome = store.record_idea_post_mortem(
                idea.idea_id,
                {
                    "realized_return_pct": 4.2,
                    "max_adverse_excursion_pct": -1.4,
                    "max_favorable_excursion_pct": 6.1,
                    "thesis_outcome": "confirmed",
                    "closure_reason": "Fixture close.",
                    "evidence_valid": "yes",
                    "what_worked": "Primary evidence mapped to the driver.",
                    "what_failed": "Consensus history was incomplete.",
                    "lessons": "Seed point-in-time expectations before ranking.",
                    "next_process_change": "Require consensus import in review checklist.",
                },
            )
            audit = store.idea_audit(idea.idea_id)
            signals = store.event_signal_rows("AAPL")
            report = build_calibration_report(store, "AAPL")

        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["evidence_valid"], "yes")
        self.assertEqual(audit["outcomes"][0]["what_worked"], "Primary evidence mapped to the driver.")
        self.assertEqual(audit["outcomes"][0]["lessons"], "Seed point-in-time expectations before ranking.")
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["stage"], "Research-Ready")
        self.assertEqual(signals[0]["horizon_label"], "1-2 quarters")
        self.assertEqual(report.sample_size, 1)
        self.assertEqual(report.status, "Uncalibrated")
        self.assertFalse(report.rank_by_ev_allowed)
        self.assertEqual(report.outcomes_needed_for_calibration, 29)
        self.assertEqual(report.nearest_calibration_sample_size, 1)
        self.assertEqual(report.slices[0].status, "Building sample")
        self.assertFalse(report.slices[0].rank_by_ev_allowed)
        self.assertEqual(report.slices[0].outcomes_needed_for_calibration, 29)
        self.assertIn("Record 29 more resolved outcome", report.slices[0].next_action)
        self.assertIn("realized_return_pct", report.required_outcome_fields)
        self.assertTrue(any("Record 29 more resolved outcome" in item for item in report.calibration_actions))
        self.assertIn("Need", report.data_gaps[0])
        self.assertEqual(report.post_mortem_count, 1)
        self.assertEqual(report.post_mortem_coverage_pct, 100)
        self.assertEqual(report.complete_post_mortem_count, 1)
        self.assertEqual(report.complete_post_mortem_coverage_pct, 100)
        self.assertEqual(report.incomplete_post_mortem_count, 0)
        self.assertEqual(report.post_mortem_quality_status, "Complete")
        self.assertEqual(report.post_mortem_quality_gaps, [])
        self.assertEqual(report.evidence_valid_rate_pct, 100)
        self.assertIn("Seed point-in-time expectations before ranking.", report.recurring_lessons)
        self.assertIn("Consensus history was incomplete.", report.recurring_failure_modes)
        self.assertIn("Require consensus import in review checklist.", report.process_improvement_actions)
        self.assertGreater(report.readiness_score, 0)
        checks = {item.area: item for item in report.readiness_checks}
        self.assertEqual(checks["Comparable outcome sample"].status, "Partial")
        self.assertIn("29 more", checks["Comparable outcome sample"].gaps[0])
        self.assertEqual(checks["Post-mortem quality"].status, "Complete")
        self.assertEqual(checks["Learning loop"].status, "Passed")

    def test_calibration_process_stats_handle_invalid_evidence_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            idea = deepcopy(demo_result("AAPL").ideas[0])
            idea.idea_id = "invalid-evidence-fixture"
            idea.stage = "Research-Ready"
            idea.signal_family = idea.signal_family or idea.source_events[0].category
            store.save_idea_versions("AAPL", "post-run-invalid", [idea])

            store.record_idea_post_mortem(
                idea.idea_id,
                {
                    "realized_return_pct": -3.0,
                    "thesis_outcome": "contradicted",
                    "evidence_valid": "no",
                    "what_failed": "Source evidence was not tied to the business driver.",
                    "lessons": "Reject unmapped source signals earlier.",
                    "next_process_change": "Require driver acceptance tests before Research-Ready.",
                },
            )
            report = build_calibration_report(store, "AAPL")

        self.assertEqual(report.post_mortem_count, 1)
        self.assertEqual(report.complete_post_mortem_count, 0)
        self.assertEqual(report.incomplete_post_mortem_count, 1)
        self.assertEqual(report.post_mortem_quality_status, "Incomplete")
        self.assertTrue(any("closure_reason" in item for item in report.post_mortem_quality_gaps))
        self.assertEqual(report.evidence_valid_rate_pct, 0)
        self.assertIn("Source evidence was not tied to the business driver.", report.recurring_failure_modes)
        self.assertIn("Require driver acceptance tests before Research-Ready.", report.process_improvement_actions)
        checks = {item.area: item for item in report.readiness_checks}
        self.assertEqual(checks["Post-mortem quality"].status, "Incomplete")
        self.assertTrue(any("closure_reason" in gap for gap in checks["Post-mortem quality"].gaps))


def _filing() -> FilingRecord:
    return FilingRecord(
        "10-Q",
        "0000320193-26-000001",
        "2026-05-01",
        "2026-03-31",
        "aapl-10q.htm",
        "Quarterly report",
        "https://example.test/aapl-10q",
    )


def _valuation() -> ValuationResult:
    cases = [
        ValuationCase("Bear", 0.25, 90, "Fixture", ["Bear case"]),
        ValuationCase("Base", 0.50, 110, "Fixture", ["Base case"]),
        ValuationCase("Bull", 0.25, 130, "Fixture", ["Bull case"]),
    ]
    return ValuationResult(
        "Non-financial",
        "Available",
        cases=cases,
        bridge=[ValuationBridgeStep(case.name, "Fair value", case.fair_value, "USD", "Fixture", "Fixture") for case in cases],
    )


if __name__ == "__main__":
    unittest.main()
