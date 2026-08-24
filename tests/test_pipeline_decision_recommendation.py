from __future__ import annotations

from pathlib import Path


def test_pipeline_resolves_story_ic_brief_before_public_recommendation() -> None:
    """Guard the live-result assembly order without running providers."""

    source = (Path(__file__).parents[1] / "equity_research" / "pipeline.py").read_text(
        encoding="utf-8"
    )
    story_assignment = source.index("story_presentation = build_story_presentation(")
    brief_assignment = source.index(
        'ic_decision_brief = story_presentation.get("ic_decision_brief")',
        story_assignment,
    )
    recommendation = source.index("decision_recommendation = build_decision_recommendation(")

    assert story_assignment < brief_assignment < recommendation
