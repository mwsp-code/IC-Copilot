from __future__ import annotations

from types import SimpleNamespace

from equity_research.domain.research_ontology import (
    AnalogScopePolicy,
    BusinessArchetype,
    LensInputAssembler,
    SecurityResearchProfileResolver,
    phrase_matches_field,
)
from equity_research.presentation.view_models import build_decision_brief_view_model
from equity_research.sample_data import demo_result


def test_security_profiles_are_explicit_and_unknown_stays_unknown() -> None:
    resolver = SecurityResearchProfileResolver()

    blk = resolver.resolve(demo_result("BLK").identity)
    unknown = resolver.resolve(SimpleNamespace(
        ticker="ZZZZ", cik="", name="Unclassified Issuer", exchange="NYSE",
        sic="", sic_description="",
    ))

    assert blk.business_archetype == BusinessArchetype.ASSET_MANAGER
    assert blk.profile_id == "asset-manager-v1"
    assert unknown.business_archetype == BusinessArchetype.UNKNOWN
    assert unknown.resolution_source == "unresolved"


def test_blk_aum_mapping_survives_generic_metric_policy_and_lenses_are_typed() -> None:
    result = demo_result("BLK")
    aum_event = next(item for item in result.events if "AUM" in item.title)
    decision = build_decision_brief_view_model(result)

    assert aum_event.metrics["research_mechanism_id"] == "aum_fee_earnings_conversion"
    assert aum_event.metrics["driver_family"] == "aum_fee_earnings_conversion"
    assert {item.title for item in decision.story_lenses} == {
        "Causal Bridge", "Equity Lens", "Credit Lens",
    }
    assert all(item.status in {
        "Supported", "Partial", "Contradicted", "Unavailable", "Not material",
    } for item in decision.story_lenses)
    assert all(item.status != "Available" for item in decision.story_lenses)
    assert result.valuation.template == "Asset manager"
    assert "fee-earnings P/E" in result.valuation.cases[0].method
    assert all("EV/EBITDA" not in gap for gap in result.valuation.missing_data)


def test_asset_manager_credit_inputs_exclude_client_and_fund_balances() -> None:
    profile = SecurityResearchProfileResolver().resolve(demo_result("BLK").identity)
    metrics = [
        SimpleNamespace(name="Corporate Cash"),
        SimpleNamespace(name="Long-Term Debt"),
        SimpleNamespace(name="Client Cash"),
        SimpleNamespace(name="Fund Debt"),
        SimpleNamespace(name="Assets Under Management"),
        SimpleNamespace(name="Consolidated VIE Assets"),
    ]

    snapshot = LensInputAssembler().assemble(profile, metrics, [])

    assert set(snapshot.corporate_credit_metric_names) == {
        "Corporate Cash", "Long-Term Debt",
    }


def test_compound_concepts_require_ordered_bounded_phrase_matches() -> None:
    assert phrase_matches_field("asset management", "asset management")
    assert phrase_matches_field("assets under management", "assets under management")
    assert not phrase_matches_field(
        "asset-light commerce with customer management tools",
        "asset management",
    )
    assert not phrase_matches_field(
        "cloud investment and customer management",
        "investment management",
    )


def test_same_issuer_scope_rejects_blk_episode_for_baba_but_accepts_blk() -> None:
    resolver = SecurityResearchProfileResolver()
    baba = demo_result("BABA")
    blk = demo_result("BLK")
    policy = AnalogScopePolicy()

    rejected = policy.evaluate(
        current_profile=resolver.resolve(baba.identity),
        current_mechanism_id="cloud_ai_investment_cycle",
        current_ticker="BABA",
        reference_tickers=("BLK",),
        transfer_role="same_issuer_cycle",
        allowed_archetypes=("asset_manager",),
        episode_mechanisms=("aum_fee_earnings_conversion",),
        transfer_dimension="AUM and fee realization",
    )
    accepted = policy.evaluate(
        current_profile=resolver.resolve(blk.identity),
        current_mechanism_id="aum_fee_earnings_conversion",
        current_ticker="BLK",
        reference_tickers=("BLK",),
        transfer_role="same_issuer_cycle",
        allowed_archetypes=("asset_manager",),
        episode_mechanisms=("aum_fee_earnings_conversion",),
        transfer_dimension="AUM and fee realization",
    )

    assert not rejected.eligible
    assert rejected.code == "issuer_scope_violation"
    assert accepted.eligible
    assert accepted.code == "same_issuer"


def test_baba_decision_never_displays_blackrock_and_uses_cloud_monitors() -> None:
    result = demo_result("BABA")
    decision = build_decision_brief_view_model(result)

    assert result.security_research_profile.business_archetype == BusinessArchetype.CHINA_INTERNET_PLATFORM
    assert all("BlackRock" not in item.episode for item in decision.historical_analogs)
    assert decision.historical_analogs
    assert all("not thesis evidence" in item.status for item in decision.historical_analogs)
    assert any(
        any(term in item.monitor.casefold() for term in ("cloud", "utilization", "ebita", "capex"))
        for item in decision.historical_analogs
    )
    blackrock_rows = [
        item for item in result.historical_analogs.assessments
        if "BlackRock" in item.episode
    ]
    assert blackrock_rows
    assert all(item.eligibility_code == "issuer_scope_violation" for item in blackrock_rows)


def test_nvda_capacity_cycle_uses_explicit_mechanism_scope() -> None:
    result = demo_result("NVDA")
    decision = build_decision_brief_view_model(result)

    assert any(
        event.metrics.get("research_mechanism_id") == "accelerated_compute_capacity_cycle"
        for event in result.events
    )
    assert any("Cisco supplier commitments" in item.episode for item in decision.historical_analogs)
    assert all("Apple" not in item.episode for item in decision.historical_analogs)
