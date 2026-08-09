from __future__ import annotations

from equity_research.application.research_runs import (
    ResearchRunCommand,
    ResearchRunService,
    RunStatus,
)
from equity_research.domain.publication_readiness import ResearchLifecycleState
from equity_research.infrastructure.job_repository import SqliteResearchJobRepository


class RecordingRunner:
    def __init__(self) -> None:
        self.submitted = []
        self.cancelled = []

    def submit(self, run_id, command) -> None:
        self.submitted.append((run_id, command))

    def cancel(self, run_id) -> None:
        self.cancelled.append(run_id)


def test_sqlite_run_lifecycle_is_durable_resumable_and_idempotent(tmp_path) -> None:
    repository = SqliteResearchJobRepository(tmp_path / "research-runs.sqlite3")
    runner = RecordingRunner()
    service = ResearchRunService(repository, runner)
    command = ResearchRunCommand(
        ticker="aapl",
        research_depth="deep_initiation",
        primary_provider_id="deepseek-profile",
        auditor_provider_id="local-codex",
        independent_qa_enabled=True,
        enabled_source_families=("sec", "wisburg"),
    )

    started = service.start(command)
    assert started.command.ticker == "AAPL"
    assert started.status == RunStatus.QUEUED.value
    assert started.lifecycle_state == ResearchLifecycleState.RESEARCH_RUNNING.value
    assert started.event_count == 1
    assert runner.submitted == [(started.run_id, command)]

    first = service.record_event(
        started.run_id,
        stage="deterministic_bootstrap",
        status="completed",
        summary="Initial evidence graph frozen.",
        idempotency_key="bootstrap-complete",
    )
    duplicate = service.record_event(
        started.run_id,
        stage="deterministic_bootstrap",
        status="completed",
        summary="Initial evidence graph frozen.",
        idempotency_key="bootstrap-complete",
    )
    assert duplicate.event_id == first.event_id
    assert len(service.timeline(started.run_id)) == 2

    reloaded = ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "research-runs.sqlite3"),
        runner,
    )
    assert reloaded.inspect(started.run_id).command == command
    reloaded.resume(started.run_id)
    assert len(runner.submitted) == 2

    cancelled = reloaded.cancel(started.run_id)
    assert cancelled.status == RunStatus.CANCELLING.value
    assert cancelled.stopping_reason == "user_cancellation"
    assert runner.cancelled == [started.run_id]


def test_validation_and_closure_are_required_before_publication(tmp_path) -> None:
    service = ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "publication-lifecycle.sqlite3")
    )
    started = service.start(ResearchRunCommand(ticker="AAPL"))

    try:
        service.mark_closure_completed(started.run_id, passed=True)
    except ValueError as exc:
        assert "deterministic validation" in str(exc)
    else:
        raise AssertionError("Closure must not pass before validation.")

    service.mark_research_attempt_finished(started.run_id)
    service.mark_validation_completed(
        started.run_id,
        passed=True,
        evidence_hash="validated-evidence",
    )
    closed = service.mark_closure_completed(started.run_id, passed=True)
    assert closed.lifecycle_state == ResearchLifecycleState.PUBLISHABLE.value
    assert closed.validation_completed_at
    assert closed.closure_completed_at
    assert closed.publishable_at
    assert not closed.completed_at

    published = service.mark_published(started.run_id)
    assert published.lifecycle_state == ResearchLifecycleState.PUBLISHED.value
    assert published.published_at
    assert published.completed_at


def test_finish_preserves_result_reference_and_terminal_runs_do_not_resume(tmp_path) -> None:
    runner = RecordingRunner()
    service = ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "runs.sqlite3"),
        runner,
    )
    started = service.start(ResearchRunCommand(ticker="MSFT"))
    finished = service.finish(
        started.run_id,
        status=RunStatus.COMPLETED,
        result_reference="result:msft:1",
        evidence_hash="evidence-hash",
        stopping_reason="all_material_findings_resolved",
    )

    assert finished.result_reference == "result:msft:1"
    assert finished.evidence_hash == "evidence-hash"
    before = len(runner.submitted)
    assert service.resume(started.run_id).status == RunStatus.COMPLETED.value
    assert len(runner.submitted) == before
