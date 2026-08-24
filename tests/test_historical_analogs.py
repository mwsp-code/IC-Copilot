from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from equity_research.historical_analogs import (
    _link_source_to_candidate,
    build_historical_analog_intelligence,
    retrieve_registered_historical_sources,
)
from equity_research.models import (
    AnalogSourceObservation,
    FilingRecord,
    HistoricalAnalogCandidate,
    NewsArticleObservation,
    NewsIntelligenceReport,
    NewsResearchPlan,
)
from equity_research.sample_data import demo_result
from equity_research.research_store import ResearchStore


def _article(
    observation_id: str,
    origin: str,
    *,
    contemporaneous: bool,
) -> NewsArticleObservation:
    return NewsArticleObservation(
        observation_id=observation_id,
        ticker="ORCL",
        provider=origin,
        source_id="fixture",
        publisher=origin,
        source_origin_group=origin,
        article_type="reporting" if contemporaneous else "analysis",
        headline="Oracle prior strategic capital allocation cycle and free cash flow",
        url=f"https://{origin}.example/oracle-cycle",
        published_at="2020-01-01",
        observed_at="2026-07-26T00:00:00+00:00",
        source_tier=3,
        licensing_policy="metadata_and_excerpt_only",
        excerpt="Oracle historical annual report capital allocation, capacity, free cash flow, and valuation cycle.",
        event_window_match=True,
        full_text_status="permitted_excerpt",
        evidence_id=f"evidence-{observation_id}",
    )


def _news(articles: list[NewsArticleObservation]) -> NewsIntelligenceReport:
    return NewsIntelligenceReport(
        ticker="ORCL",
        status="Available" if articles else "Unavailable",
        coverage_status="Fixture coverage.",
        summary="Fixture historical-source observations.",
        plan=NewsResearchPlan(
            ticker="ORCL",
            status="Available",
            generated_at="2026-07-26T00:00:00+00:00",
        ),
        articles=articles,
    )


def _oracle_candidate_payload() -> dict:
    return {
        "analog_class": "Same-company prior cycle",
        "episode": "Oracle cloud and capital-allocation cycle, FY2017-FY2020",
        "companies": ["Oracle Corporation"],
        "selection_rationale": (
            "Tests cloud investment, cash conversion, leverage, and capital return."
        ),
        "search_terms": [
            "Oracle", "2017", "2020", "cloud", "capital allocation",
            "free cash flow",
        ],
        "mechanism": "cloud investment and capital allocation",
        "period_start": "FY2017",
        "period_end": "FY2020",
        "period_precision": "fiscal_year",
        "expected_outcomes": [
            "operating", "financing", "valuation", "price",
        ],
    }


def _build(articles: list[NewsArticleObservation], llm_provider=None):
    result = demo_result("ORCL")
    return build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news(articles),
        llm_provider=llm_provider,
        use_llm=llm_provider is not None,
    )


def test_registered_historical_retrieval_adds_primary_and_price_sources() -> None:
    result = demo_result("AAPL")
    candidate = HistoricalAnalogCandidate(
        analog_id="aapl-cycle",
        analog_class="Same-company prior cycle",
        episode="Apple Services and capital-return cycle, FY2018-FY2020",
        companies=["Apple Inc."],
        selection_rationale="Test mix, cash conversion, and repurchases.",
        search_terms=[
            "Apple", "Services", "wearables", "margin", "cash flow",
            "repurchases",
        ],
        mechanism="services mix cash generation capital return",
        period_start="FY2018",
        period_end="FY2020",
        period_precision="fiscal_year",
        reference_tickers=["AAPL"],
    )
    filing = FilingRecord(
        form="10-K",
        accession="0000320193-20-000096",
        filing_date="2020-10-30",
        report_date="2020-09-26",
        primary_doc="aapl-20200926.htm",
        description="Annual report",
        url="https://www.sec.gov/Archives/aapl-2020.htm",
    )

    class Sec:
        def get_comparable_filings(self, cik, form, limit):
            return [filing] if form == "10-K" else []

        def get_filing_text(self, selected):
            return (
                "Apple Services net sales and gross margin increased. "
                "Operating cash flow supported share repurchases, while "
                "purchase obligations and capital expenditures were disclosed."
            )

    class Price:
        def _cached_daily_rows(self, ticker):
            return [
                {"date": date(2018, 1, 2), "close": 40.0},
                {"date": date(2020, 12, 31), "close": 130.0},
            ]

        def _price_source(self, ticker):
            return SimpleNamespace(
                provider="Fixture adjusted prices",
                source_url="https://prices.example/AAPL",
                adjusted=True,
            )

    sources = retrieve_registered_historical_sources(
        result.identity,
        [candidate],
        sec_client=Sec(),
        price_client=Price(),
    )

    assert {item.source_class for item in sources} == {
        "historical_filing",
        "quantitative_market_data",
    }
    assert {item.source_origin_group for item in sources} == {
        "sec_edgar",
        "fixture_adjusted_prices",
    }
    assert all(item.evidence_id for item in sources)


def test_one_current_source_does_not_auto_qualify_a_historical_analog() -> None:
    report = _build([_article("one", "research_house_a", contemporaneous=True)])

    assert report.assessments
    assert all(item.status == "Exploratory Candidate" for item in report.assessments)
    assert all(item.similarity_score is None for item in report.assessments)
    assert any(
        link.rejection_reasons
        for item in report.assessments for link in item.rejected_sources
    )


def test_two_independent_generic_sources_do_not_bypass_episode_relevance() -> None:
    report = _build([
        _article("one", "research_house_a", contemporaneous=True),
        _article("two", "institution_b", contemporaneous=False),
    ])

    assert all(item.status != "Audited Research Analog" for item in report.assessments)
    assert all(item.similarity_score is None for item in report.assessments)


def test_unrelated_market_articles_are_rejected_with_exact_reasons() -> None:
    articles = []
    for index, headline in enumerate((
        "Housing affordability debate",
        "Verizon dividend outlook",
        "Cruise line booking activity",
        "Growth ETF comparison",
    )):
        article = _article(
            f"irrelevant-{index}",
            f"publisher-{index}",
            contemporaneous=True,
        )
        article.headline = headline
        article.excerpt = "Current market activity with no Oracle episode evidence."
        article.published_at = "2026-07-01"
        articles.append(article)

    report = _build(articles)

    assert all(not item.sources for item in report.assessments)
    rejected = [
        reason
        for item in report.assessments
        for link in item.rejected_sources
        for reason in link.rejection_reasons
    ]
    assert "current_feed_does_not_analyze_historical_episode" in rejected
    assert "source_relevance_below_60" in rejected
    for assessment in report.assessments:
        source_ids = [item.source_id for item in assessment.rejected_sources]
        assert len(source_ids) == len(set(source_ids))
        assert all(
            item.source_title and item.rejection_summary
            for item in assessment.rejected_sources
        )
    assert report.work_orders
    assert any(
        item.status == "search_returned_no_relevant_episode_evidence"
        for item in report.work_orders
    )


def test_reference_company_episode_rejects_wrong_issuer_source() -> None:
    candidate = HistoricalAnalogCandidate(
        analog_id="aws-cycle",
        analog_class="Same economic or financial mechanism",
        episode="AWS capacity conversion and operating leverage, 2017-2019",
        companies=["Amazon Web Services"],
        selection_rationale="Test utilization and operating leverage.",
        search_terms=["AWS", "capacity", "utilization", "operating leverage"],
        mechanism="cloud capacity utilization operating leverage",
        period_start="2017",
        period_end="2019",
        period_precision="year",
        reference_tickers=["AMZN"],
    )
    apple_source = AnalogSourceObservation(
        source_id="apple-source",
        provider="SEC EDGAR",
        title="Apple 2019 annual report",
        source_class="historical_filing",
        source_origin_group="sec_edgar",
        published_at="2019-10-31",
        url="https://www.sec.gov/example/apple",
        evidence_id="apple-evidence",
        source_tier=1,
        licensing_policy="public_primary_source_excerpt",
        excerpt=(
            "Apple Services capacity and operating leverage improved during 2019."
        ),
    )

    link = _link_source_to_candidate(candidate, apple_source)

    assert link.status == "rejected"
    assert "reference_entity_mismatch" in link.rejection_reasons


def test_aapl_benchmark_splits_exact_same_company_episodes() -> None:
    report = demo_result("AAPL").historical_analogs
    episodes = [item.episode for item in report.assessments]

    assert any("FY2013-FY2016" in item for item in episodes)
    assert any("FY2018-FY2020" in item for item in episodes)
    assert all(
        item.similarity_score is None
        for item in report.assessments
        if item.status != "Audited Research Analog"
    )


def test_llm_cannot_add_url_as_analog_episode() -> None:
    class Provider:
        provider_name = "fixture-llm"

        def complete_json(self, _prompt):
            return {
                "candidates": [
                    {
                        "analog_class": "Broad historical case study",
                        "episode": "https://unregistered.example/invented",
                        "companies": ["Invented Co"],
                        "selection_rationale": "Attempt to inject an arbitrary source.",
                        "search_terms": ["invented"],
                    }
                ]
            }

    report = _build([], llm_provider=Provider())

    assert not any("unregistered.example" in item.episode for item in report.assessments)
    assert report.llm_status == "validation_failed"


def test_analogs_do_not_change_trade_direction_or_stage() -> None:
    result = demo_result("ORCL")
    original = [(idea.idea_id, idea.direction, idea.stage) for idea in result.ideas]
    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([
            _article("one", "research_house_a", contemporaneous=True),
            _article("two", "institution_b", contemporaneous=False),
        ]),
    )

    assert report.assessments
    assert original == [(idea.idea_id, idea.direction, idea.stage) for idea in result.ideas]


def test_aapl_demo_has_frozen_source_backed_episode() -> None:
    report = demo_result("AAPL").historical_analogs

    assert report.llm_status == "frozen_fixture"
    assert report.investigation_status.code == "frozen_fixture"
    assert len(report.dossiers) >= 2
    assert any("FY2016-FY2020" in item.episode for item in report.dossiers)
    assert any("FY2013-FY2016" in item.episode for item in report.dossiers)
    for dossier in report.dossiers:
        assert dossier.status in {
            "Frozen Demo Dossier",
            "Historical analog candidate",
        }
        assert "Source retrieval must" not in dossier.operating_outcome
        assert len({source.source_origin_group for source in dossier.sources}) >= 2
        assert dossier.linked_monitor_rules
        if dossier.status == "Frozen Demo Dossier":
            assert dossier.quality_scorecard is not None
            assert dossier.quality_scorecard.total_score >= 85
        else:
            assert dossier.generation_provenance.get("publication_status") == (
                "non_concluding_candidate"
            )
        assert dossier.generation_provenance["live_model"] == "no"


def test_aapl_benchmark_includes_mechanism_boundaries_and_asset_light_baseline() -> None:
    report = demo_result("AAPL").historical_analogs
    episodes = " | ".join(item.episode for item in report.assessments)

    assert "Cisco supplier commitments" in episodes
    assert "Intel owned-fab" in episodes
    assert "Meta and Google smart-glasses" in episodes
    assert "Telecom and fiber capacity overbuild" in episodes
    assert "AWS capacity conversion" in episodes
    assert "asset-light" in report.signature.current_baseline.owned_capacity
    assert "purchase obligations" in (
        report.signature.current_baseline.purchase_obligations.lower()
    )
    assert all(
        item.similarity_score is None
        for item in report.assessments
        if item.status != "Audited Research Analog"
    )


@pytest.mark.parametrize("ticker", ["BABA", "NVDA", "ORCL", "TSLA", "GS"])
def test_mechanism_candidates_remain_discoverable_without_fake_scores(
    ticker: str,
) -> None:
    report = demo_result(ticker).historical_analogs

    assert report.assessments
    assert all(item.selection_rationale for item in report.assessments)
    assert all(
        item.similarity_score is None
        for item in report.assessments
        if item.status != "Audited Research Analog"
    )


def test_nvda_candidates_are_ranked_by_issuer_mechanism_not_catalog_order() -> None:
    report = demo_result("NVDA").historical_analogs
    visible = [
        item for item in report.assessments
        if item.scope_status == "in_scope" and item.applicability_score >= 60
    ]

    assert visible
    assert all("Apple" not in item.episode for item in visible)
    assert "Cisco supplier commitments" in visible[0].episode
    assert any("Intel owned-fab" in item.episode for item in visible[:4])
    assert any("AWS capacity conversion" in item.episode for item in visible[:4])
    assert [item.applicability_score for item in visible] == sorted(
        [item.applicability_score for item in visible],
        reverse=True,
    )
    assert all(item.applicability_reasons for item in visible)


def test_unmatched_catalog_uses_same_issuer_discovery_instead_of_tech_filler() -> None:
    report = demo_result("GS").historical_analogs
    visible = [
        item for item in report.assessments
        if item.scope_status == "in_scope" and item.applicability_score >= 60
    ]

    assert visible
    assert visible[0].companies == ["The Goldman Sachs Group, Inc."]
    assert visible[0].transfer_role == "same_issuer_discovery"
    assert all("AWS" not in item.episode for item in visible)


def test_fast_profile_discovers_without_calling_llm() -> None:
    class Provider:
        provider_name = "fixture-llm"
        calls = 0

        def complete_json(self, _prompt):
            self.calls += 1
            return {}

    result = demo_result("ORCL")
    provider = Provider()
    result.research_profile = type(
        "Profile",
        (),
        {"profile_id": "fast_screening"},
    )()
    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([]),
        llm_provider=provider,
        use_llm=True,
        research_profile=result.research_profile,
    )

    assert provider.calls == 0
    assert report.llm_status == "not_selected_by_profile"
    assert report.research_manifest.selected_analog_ids == []


def test_adaptive_profile_selects_one_analog_for_investigation() -> None:
    class Provider:
        provider_name = "fixture-llm"
        model = "fixture-model"

        def complete_json(self, prompt):
            if str(prompt.get("task", "")).startswith("Propose"):
                return {"candidates": []}
            return {"dossiers": []}

    result = demo_result("ORCL")
    profile = type("Profile", (), {"profile_id": "adaptive_ic"})()
    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([
            _article("one", "research_house_a", contemporaneous=True),
            _article("two", "institution_b", contemporaneous=False),
        ]),
        llm_provider=Provider(),
        use_llm=True,
        research_profile=profile,
    )

    assert len(report.research_manifest.selected_analog_ids) == 1


def test_deep_profile_selects_three_analogs_for_investigation() -> None:
    class Provider:
        provider_name = "fixture-llm"
        model = "fixture-model"

        def complete_json(self, prompt):
            if str(prompt.get("task", "")).startswith("Propose"):
                return {"candidates": []}
            return {"dossiers": []}

    result = demo_result("ORCL")
    profile = type("Profile", (), {"profile_id": "deep_initiation"})()
    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([
            _article("one", "research_house_a", contemporaneous=True),
            _article("two", "institution_b", contemporaneous=False),
        ]),
        llm_provider=Provider(),
        use_llm=True,
        research_profile=profile,
    )

    assert len(report.research_manifest.selected_analog_ids) == 3
    assert report.investigation_status.requested_count == 3


def test_explicit_event_investigation_selects_only_requested_analog() -> None:
    result = demo_result("ORCL")
    initial = _build([])
    requested_id = initial.assessments[-1].analog_id
    profile = type("Profile", (), {"profile_id": "investigate_event"})()

    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([]),
        llm_provider=None,
        use_llm=False,
        llm_enabled=True,
        research_profile=profile,
        investigate_analog_id=requested_id,
    )

    assert report.research_manifest.selected_analog_ids == [requested_id]
    assert report.investigation_status.requested_count == 1


def test_missing_provider_is_explicit_for_adaptive_profile() -> None:
    result = demo_result("ORCL")
    profile = type("Profile", (), {"profile_id": "adaptive_ic"})()
    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([]),
        llm_provider=None,
        use_llm=False,
        llm_enabled=True,
        research_profile=profile,
    )

    assert report.llm_status == "no_provider"
    assert "no configured LLM provider" in report.investigation_status.reason


def test_analog_timeout_preserves_deterministic_candidates() -> None:
    class Provider:
        provider_name = "fixture-llm"

        def complete_json(self, prompt):
            if str(prompt.get("task", "")).startswith("Propose"):
                return {"candidates": [_oracle_candidate_payload()]}
            raise TimeoutError("read operation timed out")

    result = demo_result("ORCL")
    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([
            _article("one", "research_house_a", contemporaneous=True),
            _article("two", "institution_b", contemporaneous=False),
        ]),
        llm_provider=Provider(),
        use_llm=True,
        research_profile=type("Profile", (), {"profile_id": "adaptive_ic"})(),
    )

    assert report.llm_status == "timeout"
    assert report.assessments
    assert report.dossiers == []
    assert "preserved" in report.investigation_status.reason


def test_invalid_episode_dates_fail_validation_without_erasing_candidate() -> None:
    class Provider:
        provider_name = "fixture-llm"

        def complete_json(self, prompt):
            if str(prompt.get("task", "")).startswith("Propose"):
                return {"candidates": [_oracle_candidate_payload()]}
            candidate = prompt["candidates"][0]
            source_ids = list(prompt["sources"])[:2]
            return {
                "dossiers": [{
                    "analog_id": candidate["analog_id"],
                    "episode": "Oracle capacity cycle",
                    "start_date": "not-a-date",
                    "end_date": "2020-12-31",
                    "operating_outcome": "Qualitative operating outcome.",
                    "financing_outcome": "Qualitative financing outcome.",
                    "valuation_outcome": "Qualitative valuation outcome.",
                    "price_outcome": "Qualitative price outcome.",
                    "source_ids": source_ids,
                }]
            }

    result = demo_result("ORCL")
    report = build_historical_analog_intelligence(
        result.identity,
        result.ideas,
        result.metrics,
        result.company_economics,
        result.external_evidence,
        _news([
            _article("one", "research_house_a", contemporaneous=True),
            _article("two", "institution_b", contemporaneous=False),
        ]),
        llm_provider=Provider(),
        use_llm=True,
        research_profile=type("Profile", (), {"profile_id": "adaptive_ic"})(),
    )

    assert report.llm_status == "validation_failed"
    assert report.assessments
    assert any("dates" in item for item in report.research_manifest.validation_failures)


def test_episode_dossier_cache_round_trip(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research.db")
    dossier = demo_result("AAPL").historical_analogs.dossiers[0]

    store.save_historical_analog_dossier("AAPL", dossier, "signature-v1")
    payload = store.load_historical_analog_dossier(
        "AAPL",
        dossier.analog_id,
        "signature-v1",
    )

    assert payload is not None
    assert payload["episode"] == dossier.episode
    assert payload["sources"][0]["citation"]["source_tier"] == 1


def test_completed_episode_dossier_is_reused_from_signature_cache(tmp_path) -> None:
    class InitialProvider:
        provider_name = "fixture-llm"
        model = "fixture-model"

        def complete_json(self, prompt):
            if str(prompt.get("task", "")).startswith("Propose"):
                return {"candidates": [_oracle_candidate_payload()]}
            candidate = prompt["candidates"][0]
            source_ids = list(prompt["sources"])[:2]
            return {
                "dossiers": [{
                    "analog_id": candidate["analog_id"],
                    "episode": "Oracle FY2017-FY2020 capital-allocation episode",
                    "start_date": "2017-06-01",
                    "end_date": "2020-05-31",
                    "companies": ["Oracle Corporation"],
                    "securities": ["ORCL"],
                    "triggering_decisions": ["Cloud investment and capital returns changed."],
                    "operating_outcome": "Revenue and cash conversion were documented.",
                    "financing_outcome": "Capital return and leverage were documented.",
                    "valuation_outcome": "Valuation remained contextual, not predictive.",
                    "price_outcome": "Historical price context was qualitative.",
                    "macro_context": "The macro regime differed.",
                    "competitive_context": "Cloud competition intensified.",
                    "lessons": ["Test cash conversion against investment intensity."],
                    "limitations": ["The analogy cannot establish direction."],
                    "confirm_kpis": ["Cloud revenue conversion"],
                    "reject_kpis": ["Capex rises without cash conversion"],
                    "linked_monitor_rules": ["operating_cash_flow_yoy_pct > 0"],
                    "source_ids": source_ids,
                }]
            }

    class CachedProvider:
        provider_name = "fixture-llm"
        model = "fixture-model"
        dossier_calls = 0

        def complete_json(self, prompt):
            if str(prompt.get("task", "")).startswith("Propose"):
                return {"candidates": [_oracle_candidate_payload()]}
            self.dossier_calls += 1
            raise AssertionError("cached dossier should prevent a second synthesis")

    result = demo_result("ORCL")
    store = ResearchStore(tmp_path / "research.db")
    kwargs = dict(
        identity=result.identity,
        ideas=result.ideas,
        metrics=result.metrics,
        economics=result.company_economics,
        external_evidence=result.external_evidence,
        news=_news([
            _article("one", "research_house_a", contemporaneous=True),
            _article("two", "institution_b", contemporaneous=False),
        ]),
        use_llm=True,
        research_profile=type("Profile", (), {"profile_id": "adaptive_ic"})(),
        store=store,
    )
    first = build_historical_analog_intelligence(
        llm_provider=InitialProvider(),
        **kwargs,
    )
    cached_provider = CachedProvider()
    second = build_historical_analog_intelligence(
        llm_provider=cached_provider,
        **kwargs,
    )

    assert first.dossiers
    assert second.investigation_status.cache_hits == 1
    assert second.llm_status == "completed"
    assert cached_provider.dossier_calls == 0
