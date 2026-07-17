from __future__ import annotations

from types import SimpleNamespace

import app


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
