from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from equity_research import config
from equity_research.presentation.view_models import (
    build_publication_readiness_view_model,
)


@pytest.fixture(autouse=True)
def _isolated_app_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_root = tmp_path / "saved-research"
    monkeypatch.setattr(config, "SAVED_RESEARCH_ROOT", saved_root)
    monkeypatch.setattr(
        config,
        "SAVED_RESEARCH_DB_PATH",
        saved_root / "saved-research.db",
    )
    monkeypatch.setattr(config, "RESEARCH_DB_PATH", tmp_path / "runs.db")


def test_audit_route_renders_publication_readiness_without_route_crash() -> None:
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=45).run()

    assert not app.exception
    app.radio[0].set_value("Audit")
    app = app.run()

    assert not app.exception
    assert any(
        "Publication readiness" in str(item.value)
        for item in app.markdown
    )
    assert not any(
        "Technical evidence references" in str(item.value)
        for item in app.markdown
    )


def test_aapl_model_valuation_resolves_inputs_without_generic_gap() -> None:
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=45).run()

    assert not app.exception
    app.radio[0].set_value("Research")
    app = app.run()
    route = next(item for item in app.radio if item.label == "Research section")
    route.set_value("Model & Valuation")
    app = app.run()

    assert not app.exception
    assert any(
        item.label == "How valuation inputs were resolved"
        for item in app.expander
    )
    rendered = "\n".join(
        str(item.value) for item in [*app.markdown, *app.warning, *app.caption]
    )
    assert "EBITDA, net debt, shares, or currency normalization" not in rendered
    assert "Forward EBITDA proxy" in rendered


def test_timed_out_closure_is_presented_as_retryable_not_rejected() -> None:
    action = SimpleNamespace(
        action_id="repair-closure",
        failure_ids=(),
        repair_kind="rerun_closure",
        objective="Rerun independent closure.",
        status="planned",
        required_source_families=(),
        attempts=1,
        resolution_summary="",
        stopping_reason="",
        dependency_action_ids=(),
    )
    evaluation = SimpleNamespace(
        repair_actions=(action,),
        failures=(),
        qualifications=(),
        publishable=False,
        decision="repair_required",
    )
    hybrid = SimpleNamespace(
        demo_freeze_decision=SimpleNamespace(
            publication_gate_evaluation=evaluation,
            repair_actions=(action,),
        ),
        publication_gate_evaluations=(),
        repair_actions=(action,),
        deterministic_validation_status="passed",
        semantic_quality_history=(),
        lifecycle_state="repair_required",
        rounds=(SimpleNamespace(
            role="closure_checker",
            status="timeout",
            message="Local Codex run exceeded 480 seconds.",
        ),),
    )
    model = build_publication_readiness_view_model(SimpleNamespace(
        hybrid_research=hybrid,
        independent_research_qa=None,
    ))

    assert model.independent_qa_status == "Closure Timed Out · Ready To Retry"
    assert model.primary_action_label == "Retry Independent QA closure"
    assert [item.status for item in model.repair_progress] == [
        "Reused", "Reused", "Ready to retry", "Pending",
    ]
    assert "will not rerun primary research" in model.summary
