from __future__ import annotations

from types import SimpleNamespace
from copy import deepcopy

from equity_research.decision_intelligence import build_decision_recommendation
from equity_research.presentation.view_models import build_decision_brief_view_model
from equity_research.sample_data import demo_result


def _top_idea(direction: str, *, eligible: bool = True):
    return SimpleNamespace(
        direction=direction,
        gate_result=SimpleNamespace(eligible=eligible),
        source_events=[SimpleNamespace(metrics={})],
    )


def test_legacy_weak_short_is_normalized_to_no_position_watch() -> None:
    recommendation = build_decision_recommendation(
        SimpleNamespace(verdict="No convincing thesis yet", direction="Short"),
        _top_idea("Short"),
        SimpleNamespace(expected_return_pct=-1.8),
        SimpleNamespace(score=29),
        SimpleNamespace(status="Partially validated"),
        SimpleNamespace(deterministic_validation_status="partial"),
    )

    assert recommendation.recommendation == "No position / Watch"
    assert recommendation.candidate_direction == "Short"
    assert not recommendation.actionable
    assert any("No validated directional thesis" in item for item in recommendation.reasons)
    assert any("minimum material downside" in item for item in recommendation.reasons)


def test_validated_material_direction_remains_actionable() -> None:
    recommendation = build_decision_recommendation(
        SimpleNamespace(verdict="Validated positive thesis", direction="Long"),
        _top_idea("Long"),
        SimpleNamespace(expected_return_pct=18.0),
        SimpleNamespace(score=88),
        SimpleNamespace(status="Validated"),
        SimpleNamespace(deterministic_validation_status="passed"),
    )

    assert recommendation.recommendation == "Long"
    assert recommendation.actionable
    assert recommendation.recommendation != "Hold"


def test_legacy_disclosure_lead_cannot_become_a_short_recommendation() -> None:
    top = _top_idea("Short")
    top.source_events[0].metrics = {
        "thesis_grade_status": "Disclosure risk / research lead",
        "direction_validation_required": True,
    }
    recommendation = build_decision_recommendation(
        SimpleNamespace(verdict="Potential filing change", direction="Short"),
        top,
        SimpleNamespace(expected_return_pct=-22.0),
        SimpleNamespace(score=90),
        SimpleNamespace(status="Validated"),
        SimpleNamespace(deterministic_validation_status="passed"),
    )

    assert recommendation.recommendation == "No position / Watch"
    assert not recommendation.actionable


def test_decision_view_model_normalizes_an_immutable_legacy_short_label() -> None:
    result = deepcopy(demo_result("AAPL"))
    result.ic_decision_brief.verdict = "No convincing thesis yet"
    result.ic_decision_brief.direction = "Short"
    result.data_quality.score = 29
    result.valuation.expected_return_pct = -1.8
    result.thesis_validation.status = "Partially validated"
    result.hybrid_research.deterministic_validation_status = "partial"

    model = build_decision_brief_view_model(result)

    assert model.direction == "No position / Watch"
    assert model.candidate_direction == "Short"
    assert "minimum material downside" in model.recommendation_reason
