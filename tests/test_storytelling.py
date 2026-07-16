from __future__ import annotations

import tempfile
from pathlib import Path

from equity_research.contributor_tools import build_contribution_pack, save_contribution_pack
from equity_research.models import ChangeEvent, MetricResolutionAudit, ProbabilityProvenance
from equity_research.sample_data import demo_result
from equity_research.storytelling import _event_drawers, build_bull_bear_judge, demo_cases


def test_demo_gallery_cases_are_keyless_and_story_focused() -> None:
    cases = demo_cases()
    tickers = {case.ticker for case in cases}

    assert {"AAPL", "NVDA", "BABA", "TSLA", "GS", "SPCX"}.issubset(tickers)
    assert all(case.network_required is False for case in cases)
    assert all(case.lesson for case in cases)
    assert all(case.screenshot_focus for case in cases)
    current_cases = [case for case in cases if case.ticker in {"AAPL", "NVDA", "BABA", "TSLA", "GS"}]
    assert all(case.content_version == "Deep Initiation Premium 2026.07.16" for case in current_cases)
    assert all(case.research_profile.startswith("Deep Initiation") for case in current_cases)
    assert all(case.budget_mode == "Premium" for case in current_cases)
    assert all("Wisburg research lens" in case.enabled_layers or "Wisburg bilingual research lens" in case.enabled_layers for case in current_cases)
    assert all(case.refreshed_at == "2026-07-16" for case in cases)


def test_nvda_demo_is_current_ticker_specific_and_has_peer_operating_evidence() -> None:
    result = demo_result("NVDA")

    assert result.demo_case is not None
    assert result.demo_case.demo_id == "nvda-neutral-first-investment-cycle"
    assert result.identity.name == "NVIDIA Corporation"
    assert result.entity_resolution.cik == "0001045810"
    metric_names = {metric.name for metric in result.metrics}
    assert {"Revenue", "Goodwill", "Capital Expenditure", "Inventory"}.issubset(metric_names)
    assert any(event.title == "Goodwill changed +280.0%" and event.direction == "neutral" for event in result.events)
    assert any(event.title == "Capital Expenditure changed +43.2%" and event.direction == "neutral" for event in result.events)
    assert result.peer_metric_readthrough
    readthroughs = [row for rows in result.peer_metric_readthrough.values() for row in rows]
    assert {row.peer_ticker for row in readthroughs} == {"TSM", "AMD"}
    assert all(row.status == "available" and row.observations for row in readthroughs)


def test_latest_showcase_demos_use_deep_premium_macro_and_wisburg_fixtures() -> None:
    for ticker in ["NVDA", "BABA", "AAPL", "TSLA", "GS"]:
        result = demo_result(ticker)

        assert result.research_profile.profile_id == "deep_initiation", ticker
        assert result.historical_research.status == "Available", ticker
        assert result.historical_research.analyzed_quarters == 20, ticker
        assert result.historical_research.analyzed_annual_reports == 5, ticker
        assert result.historical_research.analyzed_calls == 20, ticker
        assert result.budget_policy.mode == "Premium", ticker
        assert result.budget_policy.allow_paid_data is True, ticker
        assert result.budget_policy.allow_llm is True, ticker
        assert any(item.official and item.source_type == "official_macro" for item in result.external_evidence.evidence), ticker
        assert any(item.provider.startswith("Wisburg") for item in result.external_evidence.evidence), ticker
        assert result.wisburg_lens.status.startswith("Available"), ticker
        assert result.wisburg_lens.coverage_audit is not None, ticker
        assert result.wisburg_lens.structured_claims, ticker
        assert result.wisburg_lens.research_tasks, ticker
        assert result.llm_research_manifest.status.startswith("Available"), ticker
        assert result.llm_run_manifest.status != "Disabled", ticker
        assert result.market_implied_expectations is not None, ticker
        assert any(card.title == "What Price Appears to Assume" for card in result.story_cards), ticker


def test_named_demos_do_not_reuse_aapl_financial_payload() -> None:
    expected_markers = {
        "BABA": "Share Repurchases",
        "TSLA": "Deliveries",
        "GS": "Investment Banking Fees",
    }

    for ticker, marker in expected_markers.items():
        result = demo_result(ticker)
        assert result.identity.ticker == ticker
        assert marker in {metric.name for metric in result.metrics}
        assert all(f"{ticker.lower()}-" in filing.primary_doc for filing in result.filings)


def test_demo_result_exposes_story_first_presentation_fields() -> None:
    result = demo_result("AAPL")

    assert result.demo_case is not None
    assert result.run_progress.stages
    assert [stage.label for stage in result.run_progress.stages] == [
        "Entity",
        "Sources",
        "Claims",
        "Drivers",
        "Peer Read-through",
        "Valuation",
        "Attribution",
        "Thesis",
        "Monitor",
    ]
    assert result.story_cards
    assert {"What Changed", "Why It Matters", "Causal Bridge", "Counter-Thesis", "Next Action"}.issubset(
        {card.title for card in result.story_cards}
    )
    assert result.bull_bear_judge.status == "Available"
    assert result.bull_bear_judge.judge_accepts
    assert result.formula_traces


def test_judge_separates_auto_resolution_from_uncalibrated_ranking() -> None:
    result = demo_result("AAPL")
    top = result.ideas[0]
    top.probability_provenance = ProbabilityProvenance("illustrative_default", "Uncalibrated")
    panel = build_bull_bear_judge(
        top,
        result.thesis_brief,
        result.thesis_critique,
        result.thesis_validation,
        result.validated_claims,
        result.evidence_work_order,
    )
    plan = panel.resolution_plan

    assert plan
    ranking = [item for item in plan if item.issue_type == "Ranking limitation"]
    assert ranking
    assert all(item.blocking_scope == "EV ranking only" for item in ranking)
    assert all(item.status == "Informational" for item in ranking)
    assert all("unresolved" not in item.issue.lower() for item in ranking)
    assert not any(
        item == "Probability and EV ranking remain uncalibrated."
        for item in panel.still_unproven
    )


def test_app_builds_reviewable_contribution_pack_without_mutating_config() -> None:
    result = demo_result("AAPL")
    pack = build_contribution_pack(result)

    assert pack["review_required"] is True
    assert pack["sector_playbook"]["target_file"].endswith("sector_kpi_playbooks.csv")
    assert "source_adapter_specs" in pack
    assert pack["demo_case_draft"]["network_required"] is False

    with tempfile.TemporaryDirectory() as directory:
        saved = save_contribution_pack(pack, Path(directory))
        assert saved.exists()
        assert saved.read_text(encoding="utf-8").find('"review_required": true') >= 0


def test_story_cards_have_evidence_drawers_with_source_metadata() -> None:
    result = demo_result("AAPL")
    cards_with_evidence = [card for card in result.story_cards if card.evidence]

    assert cards_with_evidence
    drawer = cards_with_evidence[0].evidence[0]
    assert drawer.claim
    assert drawer.source or drawer.metric
    assert drawer.parser_status


def test_spcx_demo_surfaces_entity_resolution_warning() -> None:
    result = demo_result("SPCX")

    assert result.demo_case is not None
    assert result.demo_case.demo_id == "spcx-spxc-entity-resolution"
    assert result.entity_resolution.warning
    entity_stage = next(stage for stage in result.run_progress.stages if stage.stage_id == "entity")
    assert entity_stage.status == "Partial"
    assert entity_stage.blockers


def test_text_signal_drawer_distinguishes_missing_prior_from_zero_mentions() -> None:
    event = ChangeEvent(
        category="debt_liquidity",
        title="Debt Liquidity discussion detected",
        summary="Debt language was detected.",
        severity=4,
        direction="neutral",
        event_date="2026-05-20",
        source="20-F",
        metrics={
            "signal_method": "disclosure_change_engine",
            "comparison_status": "no_comparable_prior",
            "comparison_reason_code": "prior_filing_missing",
            "disclosure_event_type": "observation",
            "current_mentions": 131,
            "previous_mentions": None,
            "current_period": "2026-03-31",
            "current_form": "20-F",
            "current_filing_date": "2026-05-20",
            "current_accession": "0001",
            "current_section": "MD&A",
            "current_mentions_per_1000_words": 5.2,
            "materiality_score": 44.0,
            "investment_relevance": "Medium",
            "interpretation": "Debt liquidity discussion was detected, but no comparable prior section was established.",
            "research_work_order": "Retrieve the previous comparable 20-F before making a directional debt liquidity claim.",
        },
    )

    drawers = _event_drawers(event, MetricResolutionAudit("BABA", "Available", "Fixture"))
    prior = next(drawer for drawer in drawers if drawer.label == "Prior comparison source")
    work_order = next(drawer for drawer in drawers if drawer.label == "Disclosure work order")

    assert prior.value == "No comparable prior rate"
    assert "do not treat missing prior text as zero" in prior.claim
    assert work_order.claim.startswith("Retrieve the previous comparable 20-F")
    assert not any(drawer.metric == "previous_mentions" for drawer in drawers)


def test_disclosure_drawer_exposes_prior_context_audit() -> None:
    event = ChangeEvent(
        category="debt_liquidity",
        title="Debt Liquidity discussion detected",
        summary="Debt language was detected.",
        severity=3,
        direction="neutral",
        event_date="2026-05-20",
        source="20-F",
        metrics={
            "signal_method": "disclosure_change_engine",
            "comparison_status": "no_comparable_prior",
            "disclosure_comparison": {
                "comparison_status": "no_comparable_prior",
                "reason_code": "prior_filing_missing",
                "comparison_type": "Observation",
                "current_form": "20-F",
                "current_accession": "0001",
                "current_period": "2026-03-31",
                "current_section": "Operating and Financial Review",
                "current_mentions_per_1000_words": 4.2,
                "prior_mentions_per_1000_words": None,
                "research_work_order": "Recover prior context.",
                "prior_context_audit": {
                    "status": "prior_not_found",
                    "zero_mentions_is_valid": False,
                    "sources_attempted": ["SEC recent submissions", "SEC historical submissions archive"],
                    "fallback_source_types": ["sec_filing", "issuer_ir_report"],
                    "llm_comparison_ready": False,
                    "blocker": "No prior filing selected.",
                },
            },
        },
    )

    drawers = _event_drawers(event, MetricResolutionAudit("BABA", "Available", "Fixture"))
    audit = next(drawer for drawer in drawers if drawer.label == "Prior context audit")

    assert audit.value == "prior_not_found"
    assert "Zero mentions valid: no" in audit.claim
    assert "issuer_ir_report" in audit.formula


def test_disclosure_drawers_show_exact_delta_and_research_bridge() -> None:
    event = ChangeEvent(
        "debt_liquidity", "Debt Liquidity disclosure changed", "Comparable language changed.",
        4, "negative", "2026-05-20", "20-F",
        metrics={
            "signal_method": "disclosure_change_engine",
            "comparison_status": "period_aligned",
            "disclosure_comparison": {
                "comparison_status": "period_aligned",
                "reason_code": "same_section_period_aligned",
                "comparison_type": "Change",
                "alignment_type": "same_section",
                "current_form": "20-F",
                "current_period": "2026-03-31",
                "prior_form": "20-F",
                "prior_period": "2025-03-31",
                "current_excerpt": "Liquidity pressure is persistent.",
                "prior_excerpt": "Liquidity pressure is temporary.",
                "changed_phrases": ["Prior: temporary | Current: persistent"],
                "confidence": "High",
            },
            "disclosure_intelligence": {
                "comparison_type": "same_section",
                "affected_driver": "Debt / liquidity",
                "industry_kpis": ["Free cash flow", "Net debt"],
                "capital_allocation_checks": ["Buybacks and dividends"],
                "credit_liquidity_checks": ["Debt maturity schedule"],
                "peer_operating_checks": ["Peer net debt"],
            },
        },
    )

    drawers = _event_drawers(event, MetricResolutionAudit("BABA", "Available", "Fixture"))
    labels = {drawer.label for drawer in drawers}

    assert "Current disclosure excerpt" in labels
    assert "Prior disclosure excerpt" in labels
    assert "Changed language" in labels
    assert "Disclosure research bridge" in labels
