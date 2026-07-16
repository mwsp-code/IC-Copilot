from __future__ import annotations

import json
import unittest
from dataclasses import asdict

from equity_research.models import Citation, EvidenceClaim, EvidenceItem, EvidenceLedger, ExternalEvidence
from equity_research.sample_data import demo_result
from equity_research.thesis_synthesis import (
    AnthropicProvider,
    OpenAICompatibleProvider,
    build_language_audit,
    build_prompt_pack,
    provider_from_config,
    synthesize_ic_thesis,
    _evidence_sufficiency,
)
from equity_research.wisburg_lens import build_wisburg_lens


class FixtureProvider:
    provider_name = "fixture_llm"
    model = "fixture-model"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete_json(self, prompt_pack: dict) -> dict:
        return self.payload


class TimeoutProvider:
    provider_name = "fixture_timeout"
    model = "slow-model"
    timeout_seconds = 7

    def complete_json(self, prompt_pack: dict) -> dict:
        raise TimeoutError("The read operation timed out")


def make_top_idea_research_ready(result):
    if result.ideas:
        result.ideas[0].stage = "Research-Ready"
        if result.ideas[0].gate_result:
            result.ideas[0].gate_result.research_ready_failed = []
    return result


def citation_id_containing(prompt: dict, token: str) -> str:
    needle = token.lower()
    for citation_id, payload in prompt["citations"].items():
        haystack = " ".join(
            str(payload.get(key) or "")
            for key in ("snippet", "original_excerpt", "section", "source")
        ).lower()
        if needle in haystack:
            return citation_id
    return next(iter(prompt["citations"]))


class ThesisSynthesisTests(unittest.TestCase):
    def test_contradiction_on_another_idea_does_not_block_top_thesis(self) -> None:
        result = demo_result("AAPL")
        top = result.ideas[0]
        foreign_evidence = EvidenceItem(
            evidence_id="other-contradiction",
            claim_id="other-claim",
            ticker="AAPL",
            stance="Contradicts",
            statement="This contradiction belongs to a different candidate.",
            source_tier=1,
            source_type="sec_filing",
            materiality=5,
            unresolved=True,
        )
        ledger = EvidenceLedger(
            claims=[
                EvidenceClaim("top-claim", top.idea_id, top.thesis, "Supported"),
                EvidenceClaim(
                    "other-claim", "different-idea", "Different candidate", "Contradicted",
                    contradicting_evidence_ids=[foreign_evidence.evidence_id],
                ),
            ],
            items=[foreign_evidence],
            unresolved_material_contradictions=1,
        )

        sufficiency = _evidence_sufficiency(
            top,
            ledger,
            result.valuation,
            result.data_quality,
            result.management_credibility,
            result.calibration,
        )

        self.assertFalse(any("different candidate" in gap.lower() for gap in sufficiency.data_gaps))

    def test_disabled_mode_returns_deterministic_ic_brief(self) -> None:
        result = demo_result("AAPL")
        synthesized = synthesize_ic_thesis(
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
            provider=None,
        )
        self.assertEqual(synthesized.thesis_brief.source, "deterministic")
        self.assertIn(synthesized.evidence_sufficiency.status, {
            "Convincing", "Promising but incomplete", "Weak", "No thesis",
        })
        self.assertEqual(synthesized.llm_manifest.status, "Disabled")
        self.assertTrue(synthesized.llm_manifest.guardrail_checks)
        self.assertGreater(synthesized.llm_manifest.guardrail_score, 0)
        self.assertTrue(synthesized.action_plan)

    def test_weak_candidate_is_presented_as_no_convincing_thesis(self) -> None:
        result = demo_result("AAPL")
        for idea in result.ideas:
            idea.stage = "Candidate"
        synthesized = synthesize_ic_thesis(
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
            provider=None,
        )
        self.assertEqual(synthesized.thesis_brief.verdict, "No convincing thesis yet")
        self.assertIn("investigation item", synthesized.thesis_brief.thesis)
        self.assertEqual(synthesized.thesis_brief.direction, "Neutral")

    def test_llm_cannot_upgrade_weak_candidate(self) -> None:
        result = demo_result("AAPL")
        for idea in result.ideas:
            idea.stage = "Candidate"
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
        )
        citation_id = next(iter(prompt["citations"]))
        synthesized = synthesize_ic_thesis(
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
            FixtureProvider({
                "verdict": "Convincing thesis",
                "thesis": "This should be upgraded.",
                "variant_perception": "This should not be accepted.",
                "evidence_chain": [{"claim": "Margin improved.", "citation_ids": [citation_id]}],
            }),
        )
        self.assertEqual(synthesized.thesis_brief.verdict, "No convincing thesis yet")
        self.assertIn("investigation item", synthesized.thesis_brief.thesis)
        self.assertEqual(synthesized.llm_manifest.status, "Skipped")
        checks = {item.area: item for item in synthesized.llm_manifest.guardrail_checks}
        self.assertEqual(checks["Weak-evidence skip"].status, "Passed")
        self.assertIn("weak", checks["Weak-evidence skip"].summary.lower())
        self.assertIn("Research-Ready", " ".join(synthesized.thesis_brief.data_gaps))

    def test_prompt_pack_contains_curated_excerpts_not_raw_secrets(self) -> None:
        result = demo_result("AAPL")
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
            evidence_work_order=result.evidence_work_order,
        )
        serialized = json.dumps(prompt)
        self.assertIn("evidence", prompt)
        self.assertIn("citations", prompt)
        self.assertIn("historical_references", prompt)
        self.assertIn("thesis_validation", prompt)
        self.assertIn("next_evidence_actions", prompt["thesis_validation"])
        self.assertIn("conviction_inputs", prompt)
        self.assertIn("budget_policy", prompt)
        self.assertIn("company_economics", prompt)
        self.assertIn("thesis_clusters", prompt)
        self.assertIn("evidence_work_order", prompt)
        self.assertTrue(prompt["evidence_work_order"]["items"])
        self.assertTrue(any("evidence_work_order" in rule for rule in prompt["rules"]))
        self.assertIn("manual_data_status", prompt)
        self.assertIn("primary_support_count", prompt["conviction_inputs"])
        self.assertNotIn("sk-", serialized)
        self.assertNotIn("api_key", serialized.lower())

    def test_prompt_pack_redacts_provider_credentials_before_guardrails(self) -> None:
        result = demo_result("AAPL")
        raw_alpha = "sk-live-alpha-secret12345"
        raw_bls = "bls-registration-secret"
        raw_bea = "bea-user-secret"
        raw_census = "census-key-secret"
        raw_bearer = "sk-bearer-secret67890"
        result.external_evidence.evidence.insert(0, ExternalEvidence(
            provider="Fixture provider",
            source_type="macro_context",
            title="Credential-bearing source metadata",
            summary=(
                "Provider retry used Authorization: Bearer "
                f"{raw_bearer} after a timeout."
            ),
            observed_at="2026-06-01T00:00:00+00:00",
            source_as_of="2026-05-30",
            source_tier=3,
            official=False,
            confidence="Low",
            citation=Citation(
                "Fixture API",
                (
                    "https://api.example.com/data?"
                    f"apikey={raw_alpha}&registrationkey={raw_bls}&"
                    f"UserID={raw_bea}&key={raw_census}&symbol=AAPL"
                ),
                filed="2026-05-30",
                section="provider-health",
                snippet=f"Retry URL carried token={raw_alpha}.",
                source_tier=3,
            ),
            tags=["provider_health", "en"],
            disqualifies_high_conviction=True,
        ))
        result.external_evidence.data_gaps.append(
            f"FMP_API_KEY missing; Authorization: Bearer {raw_bearer}"
        )

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
            evidence_work_order=result.evidence_work_order,
        )
        serialized = json.dumps(prompt)
        self.assertIn("symbol=AAPL", serialized)
        for raw in (raw_alpha, raw_bls, raw_bea, raw_census, raw_bearer):
            self.assertNotIn(raw, serialized)
        self.assertNotIn("Authorization: Bearer sk-", serialized)
        self.assertNotIn("token=sk-", serialized)

        synthesized = synthesize_ic_thesis(
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
            provider=None,
            evidence_work_order=result.evidence_work_order,
        )
        checks = {item.area: item for item in synthesized.llm_manifest.guardrail_checks}
        self.assertEqual(checks["Curated evidence boundary"].status, "Passed")
        self.assertIn("secret_findings=0", checks["Curated evidence boundary"].evidence)

    def test_llm_manifest_records_redacted_prompt_audit(self) -> None:
        result = demo_result("AAPL")
        synthesized = synthesize_ic_thesis(
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
            provider=None,
            thesis_clusters=result.thesis_clusters,
            research_questions=result.research_questions,
            source_plan=result.source_plan,
            event_workflow=result.event_workflow,
            evidence_work_order=result.evidence_work_order,
        )
        manifest = synthesized.llm_manifest
        serialized_manifest = json.dumps(asdict(manifest))

        self.assertEqual(manifest.status, "Disabled")
        self.assertEqual(len(manifest.prompt_hash), 64)
        self.assertGreater(manifest.prompt_context_counts["ideas"], 0)
        self.assertGreater(manifest.prompt_context_counts["evidence"], 0)
        self.assertGreater(manifest.prompt_context_counts["evidence_work_order_items"], 0)
        self.assertIn("citation_ids_required", manifest.guardrail_policy)
        self.assertIn("citation_claim_support_overlap_required", manifest.guardrail_policy)
        self.assertIn("evidence_work_order_items_are_gaps_not_proof", manifest.guardrail_policy)
        self.assertIn("weak_evidence_skips_primary_synthesis", manifest.guardrail_policy)
        self.assertNotIn("sk-", serialized_manifest)
        self.assertNotIn("api_key", serialized_manifest.lower())
        self.assertTrue(manifest.guardrail_checks)
        self.assertGreater(manifest.guardrail_score, 0)
        checks = {item.area: item for item in manifest.guardrail_checks}
        self.assertEqual(checks["Curated evidence boundary"].status, "Passed")
        self.assertEqual(checks["Citation-constrained output"].status, "Passed")
        self.assertEqual(checks["No promotion authority"].status, "Passed")

    def test_prompt_pack_includes_capped_external_context_with_citations(self) -> None:
        result = demo_result("AAPL")
        result.external_evidence.evidence.append(ExternalEvidence(
            provider="Wisburg research",
            source_type="external_analyst_context",
            title="Company Report: Apple services debate",
            summary="Outside analysts are focused on services durability and China demand.",
            observed_at="2026-06-01T00:00:00+00:00",
            source_as_of="2026-05-30",
            source_tier=3,
            official=False,
            confidence="Medium",
            citation=Citation(
                "Wisburg company report 123",
                "https://mcp.wisburg.com/mcp",
                filed="2026-05-30",
                section="company:123",
                snippet="Services durability and China demand.",
                source_tier=3,
            ),
            tags=["wisburg", "en", "AAPL"],
            disqualifies_high_conviction=True,
        ))
        wisburg_lens = build_wisburg_lens(result.identity, result.external_evidence)
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
            wisburg_lens=wisburg_lens,
        )
        rows = prompt["external_evidence_items"]
        self.assertTrue(any(row["provider"] == "Wisburg research" for row in rows))
        wisburg = next(row for row in rows if row["provider"] == "Wisburg research")
        self.assertEqual(wisburg["high_conviction_role"], "context_only")
        self.assertEqual(wisburg["source_language"], "en")
        self.assertIn(wisburg["citation_id"], prompt["citations"])
        self.assertEqual(prompt["wisburg_lens"]["status"], "Available")
        self.assertTrue(prompt["wisburg_lens"]["excerpts"])
        self.assertTrue(prompt["wisburg_lens"]["source_suggestions"])
        self.assertTrue(any("wisburg_lens" in rule for rule in prompt["rules"]))

    def test_prompt_pack_can_include_event_workflow(self) -> None:
        result = demo_result("AAPL")
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
            event_workflow=result.event_workflow,
        )

        self.assertEqual(prompt["event_workflow"]["status"], result.event_workflow.status)
        self.assertTrue(prompt["event_workflow"]["items"])
        self.assertTrue(any("event_workflow" in rule for rule in prompt["rules"]))

    def test_llm_output_with_known_citations_is_accepted(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
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
        )
        citation_id = citation_id_containing(prompt, "margin")
        payload = {
            "verdict": "Promising but incomplete thesis",
            "thesis": "Margin evidence supports further research.",
            "variant_perception": "The market may underweight margin durability.",
            "evidence_chain": [{"claim": "Margin improved.", "citation_ids": [citation_id]}],
            "strongest_counter_thesis": "The margin change may be temporary.",
            "key_uncertainties": ["Durability"],
            "missing_evidence": ["More segment detail"],
            "what_would_falsify": ["Margins reverse next quarter"],
            "action_plan": [{
                "criterion": "Margin follow-through",
                "source_field": "metrics.gross_margin",
                "metric": "gross_margin",
                "operator": ">=",
                "threshold": 45,
                "deadline": "2026-09-30",
                "confirm_trigger": "Gross margin stays elevated.",
                "break_trigger": "Gross margin reverses.",
            }],
        }
        synthesized = synthesize_ic_thesis(
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
            FixtureProvider(payload),
        )
        self.assertEqual(synthesized.thesis_brief.source, "llm")
        self.assertEqual(synthesized.llm_manifest.status, "Available")
        self.assertEqual(synthesized.action_plan[0].metric, "gross_margin")

    def test_llm_output_accepts_common_citation_field_variants(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
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
        )
        citation_id = citation_id_containing(prompt, "margin")
        evidence_id = next(
            key for key, value in prompt["evidence"].items()
            if value.get("citation_id") == citation_id
        )
        for field, value in (
            ("citation_id", citation_id),
            ("citations", [{"id": citation_id}]),
            ("evidence_ids", [evidence_id]),
        ):
            synthesized = synthesize_ic_thesis(
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
                FixtureProvider({
                    "verdict": "Promising but incomplete thesis",
                    "thesis": f"Variant field {field} is accepted.",
                    "variant_perception": "Citation fields may use common aliases.",
                    "evidence_chain": [{"claim": "Margin improved.", field: value}],
                }),
            )
            self.assertEqual(synthesized.llm_manifest.status, "Available")

    def test_llm_output_with_unknown_or_missing_citations_is_rejected(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
        synthesized = synthesize_ic_thesis(
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
            FixtureProvider({
                "verdict": "Buy",
                "thesis": "Unsupported.",
                "variant_perception": "Unsupported.",
                "evidence_chain": [{"claim": "Invented claim", "citation_ids": ["unknown"]}],
            }),
        )
        self.assertEqual(synthesized.thesis_brief.source, "deterministic")
        self.assertEqual(synthesized.llm_manifest.status, "Rejected")
        self.assertEqual(synthesized.llm_manifest.llm_execution_status, "available")
        self.assertEqual(synthesized.llm_manifest.llm_guardrail_status, "rejected")

    def test_llm_timeout_is_provider_health_not_guardrail_rejection(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
        synthesized = synthesize_ic_thesis(
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
            TimeoutProvider(),
        )

        self.assertEqual(synthesized.thesis_brief.source, "deterministic")
        self.assertEqual(synthesized.llm_manifest.status, "Provider timeout")
        self.assertEqual(synthesized.llm_manifest.llm_execution_status, "timeout")
        self.assertEqual(synthesized.llm_manifest.llm_guardrail_status, "not_run")
        self.assertEqual(synthesized.llm_manifest.failure_class, "timeout")
        self.assertTrue(synthesized.llm_manifest.retryable)
        self.assertEqual(synthesized.llm_manifest.provider_health, "retryable_timeout")
        self.assertEqual(synthesized.llm_manifest.timeout_seconds, 7)
        checks = {item.area: item for item in synthesized.llm_manifest.guardrail_checks}
        self.assertEqual(checks["Weak-evidence skip"].status, "Passed")
        self.assertEqual(checks["Provider health boundary"].status, "Retryable")

    def test_llm_output_with_unrelated_claim_despite_valid_citation_is_rejected(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
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
        )
        citation_id = citation_id_containing(prompt, "margin")
        synthesized = synthesize_ic_thesis(
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
            FixtureProvider({
                "verdict": "Promising but incomplete thesis",
                "thesis": "Unrelated cited claim.",
                "variant_perception": "Unsupported.",
                "evidence_chain": [{
                    "claim": "FDA approval eliminated antitrust litigation risk.",
                    "citation_ids": [citation_id],
                }],
            }),
        )
        self.assertEqual(synthesized.thesis_brief.source, "deterministic")
        self.assertEqual(synthesized.llm_manifest.status, "Rejected")
        self.assertIn("cited excerpt", synthesized.llm_manifest.message)

    def test_llm_output_with_invented_target_or_probability_is_rejected(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
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
        )
        citation_id = citation_id_containing(prompt, "margin")
        synthesized = synthesize_ic_thesis(
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
            FixtureProvider({
                "verdict": "90% probability of success",
                "thesis": "The price target is 300.",
                "variant_perception": "Unsupported.",
                "evidence_chain": [{"claim": "Margin improved.", "citation_ids": [citation_id]}],
            }),
        )
        self.assertEqual(synthesized.llm_manifest.status, "Rejected")

    def test_openai_compatible_provider_parses_mocked_response(self) -> None:
        provider = OpenAICompatibleProvider(
            api_key="secret",
            model="model",
            base_url="https://example.test/v1",
            provider_name="qwen",
            fetch_json=lambda url, payload, headers, timeout: {
                "choices": [{"message": {"content": "{\"verdict\":\"ok\",\"evidence_chain\":[]}"}}]
            },
        )
        self.assertEqual(provider.complete_json({})["verdict"], "ok")
        self.assertEqual(provider.provider_name, "qwen")

    def test_anthropic_provider_parses_mocked_response(self) -> None:
        provider = AnthropicProvider(
            api_key="secret",
            model="claude",
            fetch_json=lambda url, payload, headers, timeout: {
                "content": [{"type": "text", "text": "{\"verdict\":\"ok\",\"evidence_chain\":[]}"}]
            },
        )
        self.assertEqual(provider.complete_json({})["verdict"], "ok")

    def test_provider_factory_supports_local_openai_compatible_without_key(self) -> None:
        provider = provider_from_config(
            enabled=True,
            provider="ollama",
            api_key="",
            base_url="http://localhost:11434/v1",
            model="llama3.1",
        )
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.model, "llama3.1")

    def test_provider_factory_requires_base_url_for_named_compatible_providers(self) -> None:
        provider = provider_from_config(
            enabled=True,
            provider="qwen",
            api_key="secret",
            base_url="",
            model="qwen-model",
        )
        self.assertIsNone(provider)

    def test_provider_factory_supports_deepseek_preset(self) -> None:
        provider = provider_from_config(
            enabled=True,
            provider="deepseek",
            api_key="secret",
            base_url="",
            model="",
        )
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.provider_name, "deepseek")
        self.assertEqual(provider.base_url, "https://api.deepseek.com")
        self.assertEqual(provider.model, "deepseek-v4-pro")

    def test_secondary_review_runs_for_research_ready_or_better(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
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
        )
        citation_id = citation_id_containing(prompt, "margin")
        primary = FixtureProvider({
            "verdict": "Promising but incomplete thesis",
            "thesis": "Margin evidence supports further research.",
            "variant_perception": "The market may underweight margin durability.",
            "evidence_chain": [{"claim": "Margin improved.", "citation_ids": [citation_id]}],
        })
        secondary = FixtureProvider({
            "summary": "Primary thesis is readable but needs margin durability evidence.",
            "disagreements": ["Clarify whether margin gains are one-time."],
            "missed_counter_thesis": ["Demand weakness could offset margins."],
            "unsupported_claims": [],
            "language_quality_issues": [],
            "readability_suggestions": ["Lead with the monitor condition."],
            "verdict": "Needs one more corroborating source.",
        })
        synthesized = synthesize_ic_thesis(
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
            primary,
            secondary,
            enable_secondary=True,
            secondary_min_stage="Candidate",
        )
        self.assertEqual(synthesized.llm_reviews[0].status, "Available")
        self.assertEqual(synthesized.llm_comparison.status, "Compared")
        self.assertEqual(synthesized.llm_comparison.agreement, "Needs review")

    def test_secondary_review_can_critique_deterministic_research_ready_thesis(self) -> None:
        result = make_top_idea_research_ready(demo_result("AAPL"))
        secondary = FixtureProvider({
            "summary": "Deterministic thesis is plausible but needs source follow-up.",
            "disagreements": ["Explain valuation sensitivity more explicitly."],
            "missed_counter_thesis": ["Consensus may already reflect the margin signal."],
            "unsupported_claims": [],
            "language_quality_issues": [],
            "readability_suggestions": ["Put the falsification test earlier."],
            "verdict": "Secondary critique accepted.",
        })

        synthesized = synthesize_ic_thesis(
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
            provider=None,
            secondary_provider=secondary,
            enable_secondary=True,
            secondary_min_stage="Research-Ready",
        )

        self.assertEqual(synthesized.llm_manifest.status, "Disabled")
        self.assertEqual(synthesized.llm_reviews[0].status, "Available")
        self.assertEqual(synthesized.llm_comparison.status, "Compared")

    def test_language_audit_preserves_chinese_excerpts(self) -> None:
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
            language_policy="bilingual_audit",
        )
        citation_id = next(iter(prompt["citations"]))
        prompt["citations"][citation_id]["original_excerpt"] = "管理层表示收入增长和利润率改善。"
        prompt["citations"][citation_id]["source_language"] = "zh-Hans"
        audit = build_language_audit(prompt, "bilingual_audit")
        self.assertIn("zh-Hans", audit.source_languages)
        self.assertTrue(audit.chinese_source_notes)
        self.assertEqual(audit.excerpts[0].translated_summary, None)


if __name__ == "__main__":
    unittest.main()
