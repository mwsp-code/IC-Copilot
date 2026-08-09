from __future__ import annotations

from types import SimpleNamespace

import app


def test_provider_vault_key_reader_is_not_shadowed_by_form_input() -> None:
    assert callable(app.saved_profile_api_key)


def test_local_codex_is_preselected_before_one_time_sign_in() -> None:
    assert app._preferred_auditor_backend(
        local_codex_status="not_authenticated",
        primary_research_ready=True,
        configured_secondary_available=False,
    ) == "codex_local"


def test_unavailable_local_codex_falls_back_to_configured_secondary() -> None:
    assert app._preferred_auditor_backend(
        local_codex_status="not_installed",
        primary_research_ready=True,
        configured_secondary_available=True,
    ) == "configured_secondary"


def test_legacy_demo_case_metadata_is_safe_during_streamlit_hot_reload() -> None:
    legacy_case = SimpleNamespace(
        ticker="AAPL",
        title="Legacy demo",
        lesson="Legacy lesson",
        badge="No API keys",
        expected_runtime="Instant",
    )

    metadata = app._demo_case_runtime_metadata(legacy_case)

    assert metadata == {
        "content_version": "",
        "refreshed_at": "",
        "research_profile": "",
        "budget_mode": "",
        "enabled_layers": (),
    }


def test_current_demo_case_metadata_preserves_preset_values() -> None:
    current_case = SimpleNamespace(
        content_version="Deep Initiation Premium 2026.07.16",
        refreshed_at="2026-07-16",
        research_profile="Deep Initiation",
        budget_mode="Premium",
        enabled_layers=("Official macro", "Wisburg"),
    )

    metadata = app._demo_case_runtime_metadata(current_case)

    assert metadata["research_profile"] == "Deep Initiation"
    assert metadata["budget_mode"] == "Premium"
    assert metadata["enabled_layers"] == ("Official macro", "Wisburg")


def test_legacy_decision_delta_without_timestamp_has_readable_fallback() -> None:
    legacy_delta = SimpleNamespace(
        compared_with_result_type="live",
        compared_with_profile=None,
    )

    label = app._human_decision_comparison(legacy_delta)

    assert label == "Prior live run saved timestamp unavailable"


def test_research_setup_summary_makes_optional_layer_scope_explicit() -> None:
    setup = app._research_setup_snapshot(
        ticker="AAPL",
        profile="adaptive_ic",
        budget_mode="Lean",
        macro_policy="Off",
        macro_sources=[],
        wisburg_enabled=False,
        llm_enabled=True,
        hybrid_enabled=False,
        consensus_source_count=2,
        market_source_count=1,
    )

    assert setup["macro_sources"] == ()
    assert app._format_research_setup(setup) == (
        "Adaptive IC Research | Data: Best available | Macro: Off | Wisburg: Off | "
        "Research QA: Off"
    )


def test_next_run_setup_distinguishes_requested_from_resolved_qa() -> None:
    setup = app._research_setup_snapshot(
        ticker="AAPL",
        profile="deep_initiation",
        budget_mode="Premium",
        macro_policy="Adaptive",
        macro_sources=["FRED"],
        wisburg_enabled=True,
        llm_enabled=True,
        hybrid_enabled=False,
        consensus_source_count=2,
        market_source_count=1,
        qa_requested=True,
        qa_resolved=False,
    )

    assert "Research QA: Requested - auditor unavailable" in (
        app._format_research_setup(setup)
    )


def test_resolved_qa_setup_names_the_auditor() -> None:
    setup = app._research_setup_snapshot(
        ticker="AAPL",
        profile="deep_initiation",
        budget_mode="Premium",
        macro_policy="Adaptive",
        macro_sources=["FRED"],
        wisburg_enabled=True,
        llm_enabled=True,
        hybrid_enabled=True,
        consensus_source_count=2,
        market_source_count=1,
        qa_requested=True,
        qa_resolved=True,
        qa_provider="Local Codex / gpt-5.6-sol",
    )

    assert "Research QA: Ready (Local Codex / gpt-5.6-sol)" in (
        app._format_research_setup(setup)
    )


def test_demo_setup_is_distinct_from_next_live_run_setup() -> None:
    demo_case = SimpleNamespace(
        ticker="AAPL",
        research_profile="deep_initiation",
        budget_mode="Premium",
        enabled_layers=("Official macro", "Wisburg", "Dual LLM"),
    )

    setup = app._demo_research_setup(demo_case)

    assert setup["macro_policy"] == "Frozen fixture"
    assert setup["wisburg_enabled"] is True
    assert setup["hybrid_enabled"] is True


def test_freshness_summary_remains_callable_after_setup_helpers() -> None:
    freshness = SimpleNamespace(
        overall_status="Current",
        result_type="live",
        run_completed_at="2026-07-30T23:30:00+00:00",
        components=[
            SimpleNamespace(
                component="Research run",
                source_as_of="2026-07-30T23:30:00+00:00",
            ),
            SimpleNamespace(component="Market price", source_as_of="2026-07-30"),
            SimpleNamespace(component="Filings", source_as_of="2026-07-29"),
            SimpleNamespace(component="Consensus", source_as_of=None),
        ],
    )

    summary = app._localized_freshness_summary(freshness)

    assert isinstance(summary, str)
    assert summary.startswith("Current. Research completed")
    assert "consensus unavailable" in summary
