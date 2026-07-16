from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equity_research import config
from equity_research.conviction_audit import build_conviction_audit
from equity_research.models import LLMRunManifest, LlmComparison
from equity_research.sample_data import demo_result


class ConvictionAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_db = config.RESEARCH_DB_PATH
        config.RESEARCH_DB_PATH = Path(self.temporary.name) / "research.db"

    def tearDown(self) -> None:
        config.RESEARCH_DB_PATH = self.original_db
        self.temporary.cleanup()

    def test_demo_result_includes_process_conviction_audit(self) -> None:
        result = demo_result("AAPL")
        audit = result.conviction_audit

        self.assertIn(audit.status, {"Robust", "Researchable", "Needs work"})
        self.assertGreater(len(audit.items), 5)
        self.assertTrue(audit.differentiators)
        self.assertTrue(any(item.name == "Primary evidence" for item in audit.items))
        self.assertTrue(any(item.name == "Historical references" for item in audit.items))
        self.assertNotIn("api_key", str(audit).lower())

    def test_audit_exposes_ten_pillar_research_quality_scorecard(self) -> None:
        result = demo_result("AAPL")
        names = {item.name for item in result.conviction_audit.items}

        expected = {
            "Thesis quality gates",
            "Company / industry playbook",
            "Price move attribution",
            "Market-capture workflow",
            "Peer metric read-through",
            "LLM synthesis guardrails",
            "LLM research assistant lanes",
            "IC one-pager",
            "Research Question mode",
            "Credit analyst lens",
            "Outcome calibration",
        }
        self.assertTrue(expected.issubset(names))
        self.assertTrue(any(
            item.name == "Market-capture workflow" and "classified" in item.evidence.lower()
            for item in result.conviction_audit.items
        ))

    def test_audit_discloses_demo_llm_guardrail_and_uncalibrated_history(self) -> None:
        result = demo_result("AAPL")
        items = {item.name: item for item in result.conviction_audit.items}

        self.assertEqual(items["LLM synthesis guardrails"].status, "Pass")
        self.assertIn(result.llm_run_manifest.status, items["LLM synthesis guardrails"].evidence)
        self.assertIn(items["Outcome calibration"].status, {"Fail", "Partial"})
        self.assertTrue(result.conviction_audit.data_gaps)

    def test_skipped_llm_due_weak_evidence_is_guardrail_pass_not_provider_failure(self) -> None:
        result = demo_result("BABA")
        audit = build_conviction_audit(
            result.ideas,
            result.evidence_ledger,
            result.data_quality,
            result.consensus,
            result.valuation,
            result.management_sources,
            result.external_evidence,
            result.historical_references,
            result.calibration,
            LLMRunManifest(
                provider="deepseek",
                model="deepseek-v4-pro",
                prompt_version="fixture",
                generated_at="2026-07-04T00:00:00+00:00",
                status="Skipped",
                message=(
                    "LLM synthesis skipped because deterministic evidence is weak. "
                    "The app does not use an LLM to polish weak evidence into an investment thesis."
                ),
            ),
            [],
            LlmComparison("Deterministic only"),
        )
        item = next(row for row in audit.items if row.name == "LLM synthesis guardrails")

        self.assertEqual(item.status, "Pass")
        self.assertIn("guardrail prevented", item.why_it_matters)
        self.assertIn("deepseek", item.evidence)

    def test_llm_provider_timeout_is_partial_not_guardrail_failure(self) -> None:
        result = demo_result("AAPL")
        audit = build_conviction_audit(
            result.ideas,
            result.evidence_ledger,
            result.data_quality,
            result.consensus,
            result.valuation,
            result.management_sources,
            result.external_evidence,
            result.historical_references,
            result.calibration,
            LLMRunManifest(
                provider="deepseek",
                model="deepseek-v4-pro",
                prompt_version="fixture",
                generated_at="2026-07-12T00:00:00+00:00",
                status="Provider timeout",
                message="LLM provider timed out before synthesis was accepted.",
                failure_class="timeout",
                retryable=True,
                provider_health="retryable_timeout",
            ),
            [],
            LlmComparison("Deterministic only"),
        )
        item = next(row for row in audit.items if row.name == "LLM synthesis guardrails")

        self.assertEqual(item.status, "Partial")
        self.assertIn("provider failed", item.why_it_matters)
        self.assertIn("timeout", item.evidence)


if __name__ == "__main__":
    unittest.main()
