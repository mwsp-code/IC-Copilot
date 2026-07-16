from __future__ import annotations

from dataclasses import asdict
import unittest

from equity_research.models import ExternalEvidence, ExternalEvidenceBundle, PrimarySourceObservation
from equity_research.news_intelligence import (
    build_corroboration_results,
    build_news_intelligence,
    claim_from_observation,
    enrich_source_plan_with_news,
    generate_news_candidate_ideas,
    observation_from_payload,
    source_needs_for_claim,
)
from equity_research.source_planner import SOURCE_REGISTRY, build_source_plan
from equity_research.models import ClaimValidationResult, CompanyIdentity, ResearchSourcePlan


class NewsIntelligenceTests(unittest.TestCase):
    def test_licensed_news_import_caps_excerpt_and_requires_primary_sources(self) -> None:
        observation = observation_from_payload({
            "ticker": "AAPL",
            "provider": "Reuters",
            "headline": "Apple faces new antitrust investigation",
            "url": "https://reuters.example/aapl-antitrust",
            "published_at": "2026-07-01",
            "full_text": "Do not store this full licensed body.",
            "excerpt": "Regulators opened an antitrust investigation into Apple services practices.",
        })
        claim = claim_from_observation(observation, company="Apple Inc.")

        self.assertEqual(observation.source_family, "licensed_newswire")
        self.assertFalse(observation.may_store_full_text)
        self.assertNotIn("Do not store this full licensed body", str(asdict(observation)))
        self.assertEqual(claim.allowed_stage, "Candidate")
        self.assertEqual(claim.affected_driver, "regulation_legal")
        self.assertIn("regulator_court", [need.source_type for need in source_needs_for_claim(claim)])

    def test_news_only_claim_generates_candidate_watch_idea(self) -> None:
        observation = observation_from_payload({
            "ticker": "AAPL",
            "provider": "AP",
            "headline": "Apple supplier disruption may affect product volume",
            "url": "https://ap.example/apple-supplier",
            "claimed_fact": "A supplier disruption may affect product volume.",
        })
        claim = claim_from_observation(observation, company="Apple Inc.")
        ideas = generate_news_candidate_ideas("AAPL", "Apple Inc.", [claim])

        self.assertEqual(ideas[0].stage, "Candidate")
        self.assertEqual(ideas[0].signal_family, "news_intelligence")
        self.assertEqual(ideas[0].primary_source_status, "Primary corroboration missing")
        self.assertIn("News-only", ideas[0].bridge_direction_rationale)

    def test_gdelt_narrative_becomes_context_only_claim(self) -> None:
        bundle = ExternalEvidenceBundle(
            "AAPL",
            "Available",
            [ExternalEvidence(
                "GDELT narrative",
                "narrative_saturation",
                "News volume elevated",
                "GDELT news-volume signal is elevated.",
                "2026-07-02T00:00:00+00:00",
                "2026-07-01",
                4,
                False,
                "Low",
                tags=["gdelt", "narrative"],
            )],
        )
        claims = build_news_intelligence("AAPL", "Apple Inc.", bundle)

        self.assertEqual(claims[0].status, "Narrative context only")
        self.assertEqual(claims[0].source_tier, 4)
        self.assertEqual(claims[0].allowed_stage, "Candidate")

    def test_primary_observation_changes_corroboration_status(self) -> None:
        observation = observation_from_payload({
            "ticker": "AAPL",
            "provider": "Reuters",
            "headline": "Apple wins government cloud contract",
            "url": "https://reuters.example/apple-contract",
            "claimed_fact": "Apple won a government cloud contract.",
        })
        claim = claim_from_observation(observation, company="Apple Inc.")
        missing = build_corroboration_results("AAPL", [claim], [])
        primary = PrimarySourceObservation(
            "award-1",
            "AAPL",
            "government_contract",
            "USAspending",
            "Official award notice",
            "https://usaspending.example/award",
            "2026-07-02T00:00:00+00:00",
            "2026-07-01",
            1,
            True,
            claim.affected_driver,
            "Official award confirms the contract.",
            corroborates_claim_ids=[claim.claim_id],
        )
        checked = build_corroboration_results("AAPL", [claim], [primary])

        self.assertEqual(missing[0].status, "Primary corroboration missing")
        self.assertEqual(checked[0].status, "Primary source checked")

    def test_source_registry_and_llm_plan_reject_unregistered_source_types(self) -> None:
        registry_types = {entry.source_type for entry in SOURCE_REGISTRY}
        self.assertIn("regulator_court", registry_types)
        self.assertIn("licensed_newswire", registry_types)
        self.assertIn("gdelt_narrative", registry_types)

        class FakeProvider:
            provider_name = "fake"

            def complete_json(self, prompt):
                return {"requests": [
                    {"source_type": "random_blog", "title": "Bad source"},
                    {"source_type": "regulator_court", "title": "Check regulator"},
                ]}

        plan = build_source_plan(
            CompanyIdentity("AAPL", "320193", "Apple Inc."),
            [],
            ClaimValidationResult("AAPL", "Available"),
            llm_provider=FakeProvider(),
            use_llm=True,
        )
        self.assertTrue(any(item.source_type == "regulator_court" for item in plan.requests))
        self.assertFalse(any(item.source_type == "random_blog" for item in plan.requests))

    def test_news_enriches_source_plan_with_primary_source_needs(self) -> None:
        observation = observation_from_payload({
            "ticker": "AAPL",
            "provider": "Reuters",
            "headline": "Apple faces new antitrust investigation",
            "url": "https://reuters.example/aapl-antitrust",
            "claimed_fact": "Regulators opened an antitrust investigation.",
        })
        claim = claim_from_observation(observation, company="Apple Inc.")
        plan = ResearchSourcePlan("AAPL", "Unavailable", "2026-07-02T00:00:00+00:00", "test")
        enriched = enrich_source_plan_with_news(plan, [claim])

        self.assertEqual(enriched.status, "Available")
        self.assertTrue(any(item.provider == "news_intelligence" for item in enriched.requests))
        self.assertIn("News claims require primary-source corroboration before promotion.", enriched.data_gaps)


if __name__ == "__main__":
    unittest.main()
