from pathlib import Path
from contextlib import closing

import pytest
from streamlit.testing.v1 import AppTest

from equity_research import config


def test_sidebar_distinguishes_frozen_demo_and_saved_research_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SAVED_RESEARCH_ROOT", tmp_path / "saved-research")
    monkeypatch.setattr(
        config,
        "SAVED_RESEARCH_DB_PATH",
        tmp_path / "saved-research" / "saved-research.db",
    )
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=45).run()

    assert not app.exception
    rendered = "\n".join(
        str(item.value) for item in [*app.markdown, *app.info, *app.caption]
    )
    assert "Displayed research" in rendered
    assert "Frozen demo" in rendered
    assert any(item.label == "Saved research" for item in app.expander)
    assert any(button.label == "Load latest completed" for button in app.button)
    assert "No providers were called" not in rendered


def test_investigate_next_stages_exact_task_without_launching_research(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SAVED_RESEARCH_ROOT", tmp_path / "saved-research")
    monkeypatch.setattr(
        config,
        "SAVED_RESEARCH_DB_PATH",
        tmp_path / "saved-research" / "saved-research.db",
    )
    monkeypatch.setattr(config, "RESEARCH_DB_PATH", tmp_path / "runs.db")
    app_path = Path(__file__).parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=45).run()

    select_button = next(
        button for button in app.button
        if button.label == "Select investigation"
    )
    app = select_button.click().run()

    rendered = "\n".join(
        str(item.value) for item in [*app.markdown, *app.info, *app.caption]
    )
    assert not app.exception
    assert "Next research task" in rendered
    assert "No providers were called" in rendered
    assert any(
        button.label == "Run selected investigation"
        for button in app.button
    )
    assert not any(
        button.label == "Run / Refresh Research"
        for button in app.button
    )
    if (tmp_path / "runs.db").exists():
        import sqlite3

        with closing(sqlite3.connect(tmp_path / "runs.db")) as database:
            row = database.execute(
                "SELECT COUNT(*) FROM research_job_snapshots"
            ).fetchone()
        assert row[0] == 0


def test_event_investigation_uses_same_stage_then_confirm_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "SAVED_RESEARCH_ROOT", tmp_path / "saved-research")
    monkeypatch.setattr(
        config,
        "SAVED_RESEARCH_DB_PATH",
        tmp_path / "saved-research" / "saved-research.db",
    )
    monkeypatch.setattr(config, "RESEARCH_DB_PATH", tmp_path / "runs.db")
    app = AppTest.from_file(
        str(Path(__file__).parents[1] / "app.py"),
        default_timeout=45,
    ).run()

    event_button = next(
        button for button in app.button
        if button.label == "Select event investigation"
    )
    app = event_button.click().run()
    rendered = "\n".join(
        str(item.value) for item in [*app.markdown, *app.info, *app.caption]
    )

    assert not app.exception
    assert "Focused event investigation" in rendered
    assert "No providers were called" in rendered
    assert any(
        button.label == "Run selected event investigation"
        for button in app.button
    )
