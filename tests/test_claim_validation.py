from __future__ import annotations

import unittest

from equity_research.claim_validation import (
    NOT_THESIS_GRADE,
    THESIS_GRADE,
    WATCH_ITEM,
    validate_events,
)
from equity_research.idea_engine import generate_trade_ideas
from equity_research.models import ChangeEvent, Citation, CompanyIdentity
from equity_research.source_planner import build_source_plan


class FakeLlm:
    provider_name = "fake-llm"
    model = "fixture"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete_json(self, prompt: dict) -> dict:
        return self.payload


class ClaimValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.")

    def test_deferred_revenue_expects_language_is_not_thesis_grade(self) -> None:
        event = ChangeEvent(
            "guidance",
            "Guidance disclosure change",
            "Guidance language changed in filing.",
            4,
            "negative",
            "2026-05-01",
            "SEC filing",
            citations=[
                Citation(
                    "10-Q 2026-05-01",
                    "https://www.sec.gov/example/aapl-10q.htm",
                    form="10-Q",
                    section="Note 3",
                    accession="0000320193-26-000013",
                    snippet=(
                        "As of March 28, 2026, the Company expects 64% of total deferred "
                        "revenue to be realized in less than a year."
                    ),
                    source_tier=1,
                )
            ],
            metrics={"economic_driver": "Services revenue", "driver_materiality": "High"},
        )

        validation, _manifest = validate_events(self.identity, [event])

        self.assertEqual(validation.claims[0].status, NOT_THESIS_GRADE)
        self.assertEqual(event.direction, "neutral")
        self.assertIn("deferred-revenue", validation.claims[0].not_thesis_grade_reason)
        ideas = generate_trade_ideas(self.identity, [event])
        self.assertEqual(ideas[0].direction, "Watch")
        self.assertEqual(ideas[0].thesis_grade_status, NOT_THESIS_GRADE)
        self.assertIn("No trade direction", ideas[0].direction_rationale)

    def test_numeric_directional_guidance_can_be_thesis_grade(self) -> None:
        event = ChangeEvent(
            "guidance",
            "Revenue outlook raised",
            "Management raised fiscal-year revenue guidance.",
            4,
            "positive",
            "2026-05-01",
            "Earnings release",
            citations=[
                Citation(
                    "Earnings release",
                    "https://issuer.test/release",
                    section="Outlook",
                    snippet="The Company now expects fiscal 2026 revenue to increase 8% to 10%.",
                    source_tier=1,
                )
            ],
            metrics={"economic_driver": "Revenue growth", "driver_materiality": "High"},
        )

        validation, _manifest = validate_events(self.identity, [event])

        self.assertEqual(validation.claims[0].status, THESIS_GRADE)
        self.assertEqual(event.metrics["thesis_grade_status"], THESIS_GRADE)
        self.assertEqual(event.metrics["validated_direction"], "positive")

    def test_share_count_basis_mismatch_is_not_thesis_grade(self) -> None:
        event = ChangeEvent(
            "financial_kpi",
            "Shares basis requires normalization",
            "Shares moved sharply, but basis needs normalization.",
            3,
            "neutral",
            "2026-06-30",
            "SEC Companyfacts",
            citations=[
                Citation(
                    "SEC XBRL Companyfacts",
                    "https://data.sec.gov/companyfacts/example.json",
                    section="Shares",
                    snippet="Shares: 1.9B shares",
                    source_tier=1,
                )
            ],
            metrics={
                "metric_name": "Shares",
                "current_value": 1_900_000_000,
                "previous_value": 18_500_000_000,
                "yoy_change_pct": -89.7,
                "economic_driver": "Share count / dilution",
                "driver_materiality": "High",
                "normalization_required": True,
                "normalization_reason": "Share-count change exceeds 30%; verify ADR ratio and ordinary-share basis.",
            },
        )

        validation, _manifest = validate_events(self.identity, [event])
        ideas = generate_trade_ideas(self.identity, [event])

        self.assertEqual(validation.claims[0].status, NOT_THESIS_GRADE)
        self.assertEqual(event.direction, "neutral")
        self.assertEqual(ideas[0].direction, "Watch")
        self.assertIn("verify ADR ratio", ideas[0].direction_rationale)

    def test_llm_cannot_upgrade_accounting_hard_rejection(self) -> None:
        event = ChangeEvent(
            "guidance",
            "Guidance disclosure change",
            "Guidance language changed in filing.",
            4,
            "negative",
            "2026-05-01",
            "SEC filing",
            citations=[
                Citation(
                    "10-Q",
                    "https://www.sec.gov/example",
                    section="Note 3",
                    snippet="The Company expects deferred revenue to be realized over future periods.",
                    source_tier=1,
                )
            ],
            metrics={"economic_driver": "Revenue growth", "driver_materiality": "High"},
        )
        fake_llm = FakeLlm({
            "claims": [{
                "event_title": "Guidance disclosure change",
                "status": THESIS_GRADE,
                "is_substantive": True,
                "claim_type": "guidance",
                "direction": "negative",
                "metric": "Revenue",
                "period": "future periods",
                "business_driver": "Revenue growth",
                "changed_text": "The Company expects deferred revenue to be realized over future periods.",
                "supporting_quote": "The Company expects deferred revenue to be realized over future periods.",
                "confidence": "High",
                "reason": "LLM attempted to upgrade the footnote.",
            }]
        })

        validation, manifest = validate_events(
            self.identity,
            [event],
            llm_provider=fake_llm,
            use_llm=True,
        )

        self.assertEqual(manifest.status, "Available")
        self.assertEqual(validation.claims[0].status, NOT_THESIS_GRADE)
        self.assertEqual(event.metrics["thesis_grade_status"], NOT_THESIS_GRADE)

    def test_keyword_count_debt_signal_is_watch_without_valid_prior_bridge(self) -> None:
        event = ChangeEvent(
            "debt_liquidity",
            "Debt Liquidity language detected",
            "Debt language was detected without a comparable prior filing.",
            4,
            "negative",
            "2026-05-20",
            "20-F",
            citations=[
                Citation(
                    "20-F 2026-05-20",
                    "https://www.sec.gov/example/baba-20f.htm",
                    form="20-F",
                    section="Debt Liquidity",
                    accession="0000000000-26-000001",
                    period_end="2026-03-31",
                    snippet="Debt maturity liquidity refinancing covenant interest expense risk.",
                    source_tier=1,
                )
            ],
            metrics={
                "signal_method": "disclosure_change_engine",
                "comparison_status": "no_comparable_prior",
                "comparison_reason_code": "prior_filing_missing",
                "disclosure_event_type": "observation",
                "current_mentions": 131,
                "previous_mentions": None,
                "economic_driver": "Debt / liquidity",
                "driver_materiality": "High",
            },
        )

        validation, _manifest = validate_events(self.identity, [event])

        self.assertEqual(validation.claims[0].status, WATCH_ITEM)
        self.assertIn("comparable prior section", validation.claims[0].not_thesis_grade_reason)

    def test_source_plan_filters_llm_to_allowed_registry(self) -> None:
        event = ChangeEvent(
            "guidance",
            "Vague outlook mention",
            "Outlook language changed.",
            3,
            "neutral",
            "2026-05-01",
            "SEC filing",
            citations=[Citation("10-Q", "https://www.sec.gov/example", snippet="Outlook remains uncertain.")],
            metrics={"economic_driver": "Unmapped"},
        )
        validation, _manifest = validate_events(self.identity, [event])
        fake_llm = FakeLlm({
            "requests": [
                {
                    "source_type": "random_blog",
                    "title": "Search random blog",
                    "reason_to_inspect": "Not allowed",
                    "expected_evidence_type": "Rumor",
                    "priority": "High",
                    "cost_latency": "Free",
                    "confirms_or_disproves": "Nothing reliable",
                },
                {
                    "source_type": "earnings_transcript",
                    "title": "Check Q&A",
                    "reason_to_inspect": "Validate vague outlook language.",
                    "expected_evidence_type": "Speaker turn with metric and period.",
                    "priority": "High",
                    "cost_latency": "Free/paid",
                    "confirms_or_disproves": "Confirms management actually guided.",
                },
            ]
        })

        plan = build_source_plan(
            self.identity,
            [event],
            validation,
            llm_provider=fake_llm,
            use_llm=True,
        )

        self.assertTrue(plan.requests)
        self.assertNotIn("random_blog", {request.source_type for request in plan.requests})
        self.assertIn("earnings_transcript", {request.source_type for request in plan.requests})

    def test_adr_missing_prior_generates_primary_and_contextual_source_requests(self) -> None:
        identity = CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Limited")
        event = ChangeEvent(
            "debt_liquidity",
            "Debt Liquidity discussion detected",
            "Current debt disclosure was found without a validated prior section.",
            3,
            "neutral",
            "2026-05-20",
            "20-F",
            citations=[Citation("20-F", "https://www.sec.gov/example", form="20-F")],
            metrics={
                "signal_method": "disclosure_change_engine",
                "comparison_status": "no_comparable_prior",
                "comparison_reason_code": "prior_filing_missing",
                "disclosure_event_type": "observation",
                "current_form": "20-F",
                "economic_driver": "Debt / liquidity",
            },
        )
        validation, _manifest = validate_events(identity, [event])

        plan = build_source_plan(identity, [event], validation)
        source_types = {request.source_type for request in plan.requests}

        self.assertIn("sec_filing", source_types)
        self.assertIn("issuer_ir_report", source_types)
        self.assertIn("earnings_transcript", source_types)
        self.assertIn("hkex_document", source_types)
        self.assertIn("presentation", source_types)
        self.assertIn("agm_egm_proxy", source_types)

    def test_llm_cannot_promote_cross_source_context_to_thesis_grade(self) -> None:
        current_quote = "Liquidity pressure became persistent and debt maturities moved closer."
        prior_quote = "Management described the liquidity pressure as temporary."
        event = ChangeEvent(
            "debt_liquidity", "Debt Liquidity discussion detected", current_quote,
            4, "negative", "2026-05-20", "20-F",
            citations=[Citation("20-F", "https://www.sec.gov/current", snippet=current_quote, source_tier=1)],
            metrics={
                "signal_method": "disclosure_change_engine",
                "comparison_status": "no_comparable_prior",
                "disclosure_event_type": "observation",
                "economic_driver": "Debt / liquidity",
                "contextual_disclosure_comparison": {
                    "comparison_type": "cross_source_context",
                    "current_excerpt": current_quote,
                    "prior_excerpt": prior_quote,
                    "semantic_shift": "A temporary issue became persistent.",
                    "affected_driver": "Debt / liquidity",
                    "required_confirmation": ["Debt maturity table"],
                    "citations_used": [],
                },
            },
        )
        deterministic, _ = validate_events(self.identity, [event])
        base_id = deterministic.claims[0].claim_id
        fake_llm = FakeLlm({"claims": [{
            "event_title": event.title,
            "status": THESIS_GRADE,
            "is_substantive": True,
            "claim_type": "debt_liquidity",
            "direction": "negative",
            "business_driver": "Debt / liquidity",
            "changed_text": current_quote,
            "prior_text": prior_quote,
            "supporting_quote": current_quote,
            "comparison_type": "cross_source_context",
            "semantic_shift": "A temporary issue became persistent.",
            "required_confirmation": ["Debt maturity table"],
            "citation_ids_used": [],
        }]})

        validation, manifest = validate_events(self.identity, [event], llm_provider=fake_llm, use_llm=True)

        self.assertEqual(validation.claims[0].claim_id, base_id)
        self.assertEqual(validation.claims[0].status, WATCH_ITEM)
        self.assertEqual(validation.claims[0].comparison_type, "cross_source_context")
        self.assertEqual(manifest.status, "Available")


if __name__ == "__main__":
    unittest.main()
