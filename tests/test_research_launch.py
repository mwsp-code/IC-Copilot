from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

from equity_research.application.research_launch import (
    ResearchLaunchIntentStatus,
    ResearchLaunchService,
    ResearchOperationKind,
    build_operation_fingerprint,
    build_research_launch_intent,
    resolve_research_parent_reference,
)
from equity_research.application.research_runs import (
    ResearchRunCommand,
    ResearchRunService,
)
from equity_research.infrastructure.job_repository import (
    SqliteResearchJobRepository,
)
from equity_research.presentation.view_models import (
    build_research_launch_intent_view_model,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.submitted = []

    def submit(self, run_id, command) -> None:
        self.submitted.append((run_id, command))

    def cancel(self, run_id) -> None:
        return None


def _action_intent():
    return build_research_launch_intent(
        ticker="AAPL",
        operation_kind=ResearchOperationKind.ACTION_INVESTIGATION,
        operation_label="Test AAPL cash conversion",
        objective="Test recurring cash conversion after working capital and capex.",
        launch_origin="decision_investigate_next",
        research_profile="deep_initiation",
        preferred_result_route="Evidence",
        action_id="action-cash-conversion",
        action_kind="cash_conversion_analysis",
        action_semantic_key="cash_conversion:AAPL:operating_cash_flow",
        parent_evidence_hash="parent-evidence-v1",
        required_source_families=("sec_filings",),
        preferred_connectors=("sec_companyfacts",),
        qa_enabled=True,
    )


def _command(intent):
    return ResearchRunCommand(
        ticker=intent.ticker,
        research_depth=intent.research_profile,
        operation_kind=intent.operation_kind.value,
        operation_label=intent.operation_label,
        investigation_action_id=intent.action_id,
        investigation_action_kind=intent.action_kind,
        investigation_semantic_key=intent.action_semantic_key,
        result_route=intent.preferred_result_route,
    )


def test_parent_reference_prefers_result_lineage_and_uses_saved_fallback() -> None:
    saved = SimpleNamespace(
        artifact_id="saved-derived-revision",
        run_id="saved-parent-run",
        evidence_hash="saved-evidence",
    )
    result = SimpleNamespace(
        hybrid_research=SimpleNamespace(
            run_id="hybrid-parent-run",
            evidence_hash="hybrid-evidence",
        ),
        independent_research_qa=None,
        run_manifest=None,
    )

    resolved = resolve_research_parent_reference(
        result,
        saved_artifact=saved,
        artifact_id=saved.artifact_id,
    )
    fallback = resolve_research_parent_reference(
        SimpleNamespace(
            hybrid_research=None,
            independent_research_qa=None,
            run_manifest=None,
        ),
        saved_artifact=saved,
        artifact_id=saved.artifact_id,
    )

    assert resolved.run_id == "hybrid-parent-run"
    assert resolved.evidence_hash == "hybrid-evidence"
    assert resolved.artifact_id == "saved-derived-revision"
    assert fallback.run_id == "saved-parent-run"
    assert fallback.resumable is True


def test_selecting_intent_is_typed_and_does_not_launch() -> None:
    intent = _action_intent()
    model = build_research_launch_intent_view_model(intent)

    assert intent.status is ResearchLaunchIntentStatus.READY
    assert model.operation_type_label == "Action investigation"
    assert model.primary_cta == "Run selected investigation"
    assert model.preferred_result_route == "Evidence"


def test_source_setup_intent_cannot_launch_until_marked_ready(tmp_path) -> None:
    runner = RecordingRunner()
    service = ResearchLaunchService(ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "source-setup.sqlite3"),
        runner,
    ))
    intent = build_research_launch_intent(
        ticker="AAPL",
        operation_kind="action_investigation",
        operation_label="Capture point-in-time estimates",
        objective="Capture an entitled estimate history.",
        launch_origin="decision_investigate_next",
        research_profile="adaptive_ic",
        preferred_result_route="Evidence",
        action_semantic_key="consensus:AAPL:event-1",
        parent_evidence_hash="evidence-v1",
        required_source_families=("consensus",),
        needs_source_setup=True,
    )

    result = service.launch(intent, _command(intent))

    assert result.state == "source_setup_required"
    assert result.snapshot is None
    assert not runner.submitted


def test_same_active_and_completed_operation_are_reused_by_default(tmp_path) -> None:
    runner = RecordingRunner()
    run_service = ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "runs.sqlite3"),
        runner,
    )
    launch_service = ResearchLaunchService(run_service)
    intent = _action_intent()

    first = launch_service.launch(intent, _command(intent))
    active_duplicate = launch_service.launch(intent, _command(intent))

    assert first.created
    assert active_duplicate.state == "reused_active"
    assert active_duplicate.snapshot.run_id == first.snapshot.run_id
    assert len(runner.submitted) == 1

    run_service.finish(
        first.snapshot.run_id,
        status="completed",
        result_reference="saved-artifact-aapl",
    )
    completed_duplicate = launch_service.launch(intent, _command(intent))

    assert completed_duplicate.confirmation_required
    assert completed_duplicate.snapshot.result_reference == "saved-artifact-aapl"
    assert len(runner.submitted) == 1

    rerun = launch_service.launch(
        intent,
        _command(intent),
        confirm_repeat=True,
        repeat_attempt_id="confirmed-rerun-1",
    )
    assert rerun.created
    assert rerun.snapshot.run_id != first.snapshot.run_id
    assert len(runner.submitted) == 2


def test_rapid_confirmed_rerun_uses_one_confirmation_attempt_id(tmp_path) -> None:
    runner = RecordingRunner()
    run_service = ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "confirmed.sqlite3"),
        runner,
    )
    service = ResearchLaunchService(run_service)
    intent = _action_intent()
    first = service.launch(intent, _command(intent))
    run_service.finish(first.snapshot.run_id, status="completed")

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(
            lambda _index: service.launch(
                intent,
                _command(intent),
                confirm_repeat=True,
                repeat_attempt_id="one-inline-confirmation",
            ),
            range(16),
        ))

    rerun_ids = {result.snapshot.run_id for result in results}
    assert len(rerun_ids) == 1
    assert sum(result.created for result in results) == 1
    assert len(runner.submitted) == 2


def test_rapid_duplicate_launches_create_one_run_and_worker_submission(tmp_path) -> None:
    runner = RecordingRunner()
    service = ResearchLaunchService(ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "concurrent.sqlite3"),
        runner,
    ))
    intent = _action_intent()

    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(
            workers.map(
                lambda _index: service.launch(intent, _command(intent)),
                range(16),
            )
        )

    assert len({result.snapshot.run_id for result in results}) == 1
    assert sum(result.created for result in results) == 1
    assert len(runner.submitted) == 1


def test_operation_fingerprint_is_scope_specific_and_redacts_credentials() -> None:
    full = build_operation_fingerprint(
        ticker="AAPL",
        operation_kind="full_research",
        research_profile="adaptive_ic",
        context={
            "provider": "deepseek",
            "api_key": "must-not-affect-fingerprint",
            "sources": {"sec", "wisburg"},
        },
    )
    same_without_secret = build_operation_fingerprint(
        ticker="AAPL",
        operation_kind="full_research",
        research_profile="adaptive_ic",
        context={
            "provider": "deepseek",
            "sources": {"wisburg", "sec"},
        },
    )
    event = build_operation_fingerprint(
        ticker="AAPL",
        operation_kind="event_investigation",
        research_profile="investigate_event",
        event_id="event-1",
        parent_evidence_hash="evidence-v1",
    )

    assert full == same_without_secret
    assert event != full


def test_canonical_intent_reuses_a_completed_or_active_legacy_alias(tmp_path) -> None:
    runner = RecordingRunner()
    run_service = ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "aliases.sqlite3"), runner
    )
    launch_service = ResearchLaunchService(run_service)
    legacy = _action_intent()
    launched = launch_service.launch(legacy, _command(legacy))
    assert launched.created

    canonical = build_research_launch_intent(
        ticker="AAPL",
        operation_kind=ResearchOperationKind.ACTION_INVESTIGATION,
        operation_label="Test AAPL cash conversion",
        objective=legacy.objective,
        launch_origin="decision_investigate_next",
        research_profile="deep_initiation",
        preferred_result_route="Evidence",
        action_id="canonical-action",
        action_kind="cash_conversion_analysis",
        action_semantic_key="investigation-action-v2:intent:AAPL:cash",
        research_intent_id="intent-cash-conversion",
        action_semantic_aliases=(legacy.action_semantic_key,),
        parent_evidence_hash="parent-evidence-v1",
    )

    assert launch_service.inspect(canonical).run_id == launched.snapshot.run_id
    result = launch_service.launch(canonical, _command(canonical))
    assert result.state == "reused_active"
    assert len(runner.submitted) == 1


def test_historical_command_json_gets_launch_defaults(tmp_path) -> None:
    path = tmp_path / "migration.sqlite3"
    repository = SqliteResearchJobRepository(path)
    service = ResearchRunService(repository)
    started = service.start(ResearchRunCommand(ticker="MSFT"))

    with closing(sqlite3.connect(path)) as database:
        payload = json.loads(database.execute(
            "SELECT command_json FROM research_job_snapshots WHERE run_id=?",
            (started.run_id,),
        ).fetchone()[0])
        for field in (
            "operation_fingerprint",
            "launch_attempt_id",
            "launch_origin",
            "parent_artifact_id",
            "research_intent_id",
        ):
            payload.pop(field, None)
        database.execute(
            "UPDATE research_job_snapshots SET command_json=? WHERE run_id=?",
            (json.dumps(payload), started.run_id),
        )

    restored = SqliteResearchJobRepository(path).get(started.run_id)
    assert restored.command.operation_fingerprint == ""
    assert restored.command.launch_attempt_id == ""
    assert restored.command.launch_origin == ""
    assert restored.command.parent_artifact_id == ""


def test_existing_sqlite_repository_adds_launch_identity_columns(tmp_path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as database:
        database.execute(
            """
            CREATE TABLE research_job_snapshots (
                run_id TEXT PRIMARY KEY,
                command_json TEXT NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                event_count INTEGER NOT NULL DEFAULT 0,
                evidence_hash TEXT NOT NULL DEFAULT '',
                result_reference TEXT NOT NULL DEFAULT '',
                stopping_reason TEXT NOT NULL DEFAULT '',
                errors_json TEXT NOT NULL DEFAULT '[]',
                lifecycle_state TEXT NOT NULL DEFAULT 'research_running',
                research_attempt_finished_at TEXT NOT NULL DEFAULT '',
                validation_completed_at TEXT NOT NULL DEFAULT '',
                audit_completed_at TEXT NOT NULL DEFAULT '',
                revision_completed_at TEXT NOT NULL DEFAULT '',
                closure_completed_at TEXT NOT NULL DEFAULT '',
                publishable_at TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                terminal_at TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                snapshot_version TEXT NOT NULL
            )
            """
        )

    SqliteResearchJobRepository(path)

    with closing(sqlite3.connect(path)) as database:
        columns = {
            row[1] for row in database.execute(
                "PRAGMA table_info(research_job_snapshots)"
            )
        }
        indexes = {
            row[1] for row in database.execute(
                "PRAGMA index_list(research_job_snapshots)"
            )
        }
    assert {"operation_fingerprint", "launch_attempt_id", "workspace_scope"} <= columns
    assert "idx_research_operation_latest" in indexes
    assert "idx_research_launch_attempt" in indexes
