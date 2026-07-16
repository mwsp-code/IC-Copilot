from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from equity_research.models import (
    Citation,
    CompanyIdentity,
    ExternalEvidence,
    ExternalEvidenceBundle,
    ResearchSourcePlan,
    ResearchSourceRequest,
)
from equity_research.research_store import ResearchStore
from equity_research.wisburg_lens import (
    build_wisburg_lens,
    enrich_source_plan_with_wisburg,
    generate_wisburg_candidate_ideas,
    lens_to_prompt_payload,
)
from equity_research.wisburg_monitor import compare_wisburg_lenses, generate_wisburg_alerts


class WisburgLensTests(unittest.TestCase):
    def test_baba_wisburg_lens_preserves_chinese_context_and_marks_targets_non_consensus(self) -> None:
        lens = build_wisburg_lens(_identity(), _wisburg_bundle())

        self.assertEqual(lens.status, "Available")
        self.assertTrue(any(item.source_language == "zh" for item in lens.excerpts))
        self.assertTrue(any(item.mentions_target_or_rating for item in lens.excerpts))
        labels = {theme.label for theme in lens.themes}
        self.assertIn("AI / cloud monetization", labels)
        self.assertIn("China commerce demand", labels)
        self.assertIsNotNone(lens.debate_map)
        self.assertIn(lens.narrative_score.label, {"Emerging", "Active", "Crowded"})
        target_excerpt = next(item for item in lens.excerpts if item.mentions_target_or_rating)
        self.assertIn("not official consensus", target_excerpt.non_consensus_label)
        self.assertLessEqual(len(target_excerpt.original_excerpt), 1400)

    def test_wisburg_source_plan_and_candidates_remain_context_only(self) -> None:
        lens = build_wisburg_lens(_identity(), _wisburg_bundle())
        plan = ResearchSourcePlan(
            "BABA",
            "Available",
            "2026-07-06T00:00:00+00:00",
            "fixture",
            requests=[
                ResearchSourceRequest(
                    "base-1",
                    "sec_filing",
                    "Review latest 6-K",
                    "Base source plan.",
                    "Issuer filing evidence.",
                    "High",
                    "Free",
                    "Confirms primary source facts.",
                )
            ],
        )
        enriched = enrich_source_plan_with_wisburg(plan, lens)
        self.assertGreater(len(enriched.requests), 1)
        self.assertTrue(all(request.source_type != "arbitrary_url" for request in enriched.requests))
        self.assertTrue(any(request.provider == "Wisburg research lens" for request in enriched.requests))

        ideas = generate_wisburg_candidate_ideas(_identity(), lens)
        self.assertTrue(ideas)
        for idea in ideas:
            self.assertEqual(idea.stage, "Candidate")
            self.assertEqual(idea.direction, "Watch")
            self.assertEqual(idea.thesis_grade_status, "Watch Item")
            self.assertLessEqual(idea.score.score_cap or 0, 55)
            self.assertLessEqual(idea.score.total, 40)
            self.assertIn("Wisburg", idea.source_events[0].metrics["not_thesis_grade_reason"])

    def test_lens_cache_stores_capped_excerpts_without_raw_keys(self) -> None:
        lens = build_wisburg_lens(_identity(), _wisburg_bundle())
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            store.save_wisburg_lens(lens)
            excerpts = store.list_external_research_excerpts("baba")
            themes = store.list_wisburg_themes("BABA")
            suggestions = store.list_wisburg_source_suggestions("BABA")

        self.assertEqual(len(excerpts), len(lens.excerpts))
        self.assertTrue(themes)
        self.assertTrue(suggestions)
        serialized = str({"excerpts": excerpts, "themes": themes, "suggestions": suggestions})
        self.assertNotIn("sk-", serialized)
        self.assertNotIn("api_key", serialized.lower())
        self.assertLessEqual(max(len(item["original_excerpt"]) for item in excerpts), 1400)

    def test_prompt_payload_contains_only_capped_context_fields(self) -> None:
        lens = build_wisburg_lens(_identity(), _wisburg_bundle())
        payload = lens_to_prompt_payload(lens)
        self.assertEqual(payload["status"], "Available")
        self.assertTrue(payload["excerpts"])
        self.assertTrue(payload["themes"])
        self.assertTrue(payload["source_suggestions"])
        self.assertTrue(all("raw_payload" not in item for item in payload["excerpts"]))
        self.assertTrue(any(item["mentions_target_or_rating"] for item in payload["excerpts"]))

    def test_point_in_time_delta_detects_new_reports_and_dedupes_alerts(self) -> None:
        prior = build_wisburg_lens(_identity(), _wisburg_bundle())
        current_bundle = _wisburg_bundle()
        current_bundle.evidence.append(ExternalEvidence(
            "Wisburg research",
            "external_analyst_context",
            "Alibaba buyback debate",
            "Outside analysts discuss whether the latest buyback is accretive or only offsets dilution.",
            "2026-07-07T00:00:00+00:00",
            "2026-07-07",
            source_tier=3,
            official=False,
            confidence="Medium",
            citation=Citation(
                "Wisburg company report 300",
                "https://mcp.wisburg.com/mcp",
                filed="2026-07-07",
                section="company:300",
                snippet="Buyback and dilution debate.",
                source_tier=3,
            ),
            tags=["wisburg", "en", "BABA"],
            disqualifies_high_conviction=True,
        ))
        current = build_wisburg_lens(_identity(), current_bundle)

        delta = compare_wisburg_lenses(current, prior)
        self.assertEqual(delta.status, "Changed")
        self.assertIn("300", delta.new_report_ids)
        self.assertFalse(any("removed" in caveat.lower() for caveat in delta.caveats))

        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            first = generate_wisburg_alerts(delta, store)
            duplicate = generate_wisburg_alerts(delta, store)
        self.assertTrue(first)
        self.assertEqual(duplicate, [])
        self.assertTrue(all(alert.severity <= 3 for alert in first))

    def test_point_in_time_delta_detects_stance_change_without_claiming_removal(self) -> None:
        prior = asdict(build_wisburg_lens(_identity(), _wisburg_bundle()))
        current = dict(prior)
        current["observed_at"] = "2026-07-08T00:00:00+00:00"
        current["themes"] = [dict(item) for item in prior["themes"]]
        current["themes"][0]["stance"] = (
            "bearish" if current["themes"][0]["stance"] != "bearish" else "bullish"
        )

        delta = compare_wisburg_lenses(current, prior)
        self.assertEqual(delta.status, "Changed")
        self.assertEqual(len(delta.theme_stance_changes), 1)
        self.assertEqual(delta.new_report_ids, [])


def _identity() -> CompanyIdentity:
    return CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Ltd")


def _wisburg_bundle() -> ExternalEvidenceBundle:
    return ExternalEvidenceBundle(
        "BABA",
        "Available",
        [
            ExternalEvidence(
                "Wisburg research",
                "external_analyst_context",
                "Alibaba cloud and AI debate",
                (
                    "\u963f\u91cc\u5df4\u5df4\u4e91\u4e1a\u52a1 AI \u5546\u4e1a\u5316"
                    "\u53ef\u80fd\u51fa\u73b0\u62d0\u70b9\uff0c\u76ee\u6807\u4ef7"
                    "\u4e0a\u8c03\uff0c\u4f46\u6dd8\u5b9d\u5929\u732b\u7535\u5546"
                    "\u589e\u957f\u4ecd\u9762\u4e34\u7ade\u4e89\u538b\u529b\u3002"
                ),
                "2026-07-06T00:00:00+00:00",
                "2026-07-05",
                source_tier=3,
                official=False,
                confidence="Medium",
                citation=Citation(
                    "Wisburg company report 100",
                    "https://mcp.wisburg.com/mcp",
                    filed="2026-07-05",
                    section="company:100",
                    snippet="Cloud AI monetization and China commerce competition.",
                    source_tier=3,
                ),
                tags=["wisburg", "zh", "BABA"],
                disqualifies_high_conviction=True,
            ),
            ExternalEvidence(
                "Wisburg research",
                "external_analyst_context",
                "Alibaba ecommerce margin pressure",
                "Outside analysts remain cautious on ecommerce competition and margin pressure.",
                "2026-07-06T00:00:00+00:00",
                "2026-07-05",
                source_tier=4,
                official=False,
                confidence="Low",
                citation=Citation(
                    "Wisburg feed 200",
                    "https://mcp.wisburg.com/mcp",
                    filed="2026-07-05",
                    section="feed:200",
                    snippet="Ecommerce competition and margin pressure.",
                    source_tier=4,
                ),
                tags=["wisburg", "en", "BABA"],
                disqualifies_high_conviction=True,
            ),
        ],
        [],
        [],
    )


if __name__ == "__main__":
    unittest.main()
