from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from equity_research.application.repair_stage_execution import (
    DeterministicSafeRevisionProvider,
    InMemoryRepairStageCheckpointRepository,
    RepairExecutionDeadlineExceeded,
    RepairExecutionError,
    RepairExecutionPolicy,
    RepairStage,
    RepairStageCheckpoint,
    RepairStageState,
    RepairStageSupervisor,
    SupervisedRepairProvider,
)
from equity_research.application.codex_execution import (
    CodexExecutionEvent,
    CodexExecutionFailure,
)
from equity_research.infrastructure.repair_checkpoint_repository import (
    SqliteRepairStageCheckpointRepository,
)


class _Provider:
    provider_name = "test_provider"
    model = "test-model"
    enabled = True

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.cancels = 0
        self.timeout_seconds = 999
        self.last_usage = {}
        self.last_cost_usd = None

    def complete_json(self, prompt):
        self.calls += 1
        value = self.outcomes.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def cancel(self):
        self.cancels += 1


class _ObservableProvider(_Provider):
    provider_name = "codex_local"

    def __init__(self, outcomes):
        super().__init__(outcomes)
        self.execution_callback = None

    def set_execution_event_callback(self, callback):
        previous = self.execution_callback
        self.execution_callback = callback
        return previous

    def complete_json(self, prompt):
        if self.execution_callback is not None:
            self.execution_callback(CodexExecutionEvent(
                state="active",
                event_type="turn.started",
                occurred_at="2026-08-23T00:00:01+00:00",
                thread_id="technical-thread",
            ))
            self.execution_callback(CodexExecutionEvent(
                state="validating_output",
                event_type="turn.completed",
                occurred_at="2026-08-23T00:00:02+00:00",
                thread_id="technical-thread",
                usage={"input_tokens": 10, "output_tokens": 3},
            ))
        return super().complete_json(prompt)


def _supervisor(repository, *, progress=None):
    return RepairStageSupervisor(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        repository=repository,
        policy=RepairExecutionPolicy(
            revision_timeout_seconds=30,
            closure_timeout_seconds=40,
            total_timeout_seconds=60,
            max_transient_retries=1,
        ),
        progress_callback=progress,
    )


def test_completed_provider_stage_is_reused_without_a_second_call():
    repository = InMemoryRepairStageCheckpointRepository()
    provider = _Provider([{"summary": "revised"}])
    prompt = {
        "role": "primary_revision",
        "claims": [],
        "idempotency_key": "stable-revision-key",
    }
    wrapped = SupervisedRepairProvider(provider, _supervisor(repository))

    first = wrapped.complete_json(prompt)
    second = wrapped.complete_json({
        **prompt,
        "volatile_generated_at": "a later process restart",
    })

    assert first == second == {"summary": "revised"}
    assert provider.calls == 1
    checkpoint = repository.get("attempt-1", "primary_revision")
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.COMPLETED.value
    assert checkpoint.result_payload == first


def test_transient_failure_retries_once_and_restores_provider_timeout():
    repository = InMemoryRepairStageCheckpointRepository()
    provider = _Provider([TimeoutError("temporary timeout"), {"summary": "closed"}])
    wrapped = SupervisedRepairProvider(provider, _supervisor(repository))

    result = wrapped.complete_json({"role": "closure_checker"})

    assert result == {"summary": "closed"}
    assert provider.calls == 2
    assert provider.cancels == 1
    assert provider.timeout_seconds == 999
    checkpoint = repository.get("attempt-1", "closure_checker")
    assert checkpoint is not None
    assert checkpoint.attempt_count == 2
    assert checkpoint.state == RepairStageState.COMPLETED.value


def test_local_codex_closure_timeout_requires_explicit_resume() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    provider = _Provider([
        TimeoutError("Local Codex run exceeded 400 seconds."),
        {"summary": "must not run automatically"},
    ])
    provider.provider_name = "codex_local"
    wrapped = SupervisedRepairProvider(provider, _supervisor(repository))

    with pytest.raises(TimeoutError, match="Local Codex"):
        wrapped.complete_json({"role": "closure_checker"})

    assert provider.calls == 1
    checkpoint = repository.get("attempt-1", "closure_checker")
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.DEADLINE_EXCEEDED.value
    assert checkpoint.retryable is False


def test_local_codex_startup_failure_retries_before_turn_begins() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    provider = _Provider([
        CodexExecutionFailure(
            "Local Codex did not report startup activity.",
            failure_class="startup_timeout",
            retryable=True,
            turn_started=False,
        ),
        {"summary": "closed after clean startup"},
    ])
    provider.provider_name = "codex_local"

    result = SupervisedRepairProvider(
        provider,
        _supervisor(repository),
    ).complete_json({"role": "closure_checker"})

    assert result == {"summary": "closed after clean startup"}
    assert provider.calls == 2
    checkpoint = repository.get("attempt-1", "closure_checker")
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.COMPLETED.value
    assert checkpoint.attempt_count == 2


def test_explicit_hosted_closure_retry_reuses_timed_out_checkpoint_once() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    prompt = {
        "role": "closure_checker",
        "idempotency_key": "closure-parent-1-evidence-1",
    }
    local = _Provider([TimeoutError("Local Codex run exceeded 40 seconds.")])
    local.provider_name = "codex_local"
    with pytest.raises(TimeoutError, match="Local Codex"):
        SupervisedRepairProvider(
            local, _supervisor(repository)
        ).complete_json(prompt)

    hosted = _Provider([{"summary": "closed"}])
    hosted.provider_name = "openai_responses"
    resumed = RepairStageSupervisor(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        repository=repository,
        policy=RepairExecutionPolicy(
            closure_timeout_seconds=90,
            total_timeout_seconds=120,
            max_transient_retries=1,
            policy_version="repair-execution-policy-v2-closure-retry",
        ),
        authorized_retry_stages=("closure_checker",),
    )

    result = SupervisedRepairProvider(hosted, resumed).complete_json(prompt)

    assert result == {"summary": "closed"}
    assert local.calls == 1
    assert hosted.calls == 1
    checkpoint = repository.get("attempt-1", "closure_checker")
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.COMPLETED.value
    assert checkpoint.attempt_count == 2
    assert checkpoint.provider == "openai_responses"


def test_identical_timed_out_closure_cannot_retry_without_execution_change() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    prompt = {"role": "closure_checker"}
    local = _Provider([TimeoutError("Local Codex run exceeded 40 seconds.")])
    local.provider_name = "codex_local"
    with pytest.raises(TimeoutError):
        SupervisedRepairProvider(
            local, _supervisor(repository)
        ).complete_json(prompt)

    identical = _Provider([{"summary": "must not run"}])
    identical.provider_name = "codex_local"
    resumed = RepairStageSupervisor(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        repository=repository,
        policy=RepairExecutionPolicy(
            closure_timeout_seconds=40,
            total_timeout_seconds=60,
            policy_version="repair-execution-policy-v2-copy-only",
        ),
        authorized_retry_stages=("closure_checker",),
    )

    with pytest.raises(RepairExecutionError, match="Local Codex"):
        SupervisedRepairProvider(identical, resumed).complete_json(prompt)
    assert identical.calls == 0


def test_running_stage_blocks_duplicate_provider_execution() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    now = datetime.now(timezone.utc)
    repository.save(RepairStageCheckpoint(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        stage="closure_checker",
        state=RepairStageState.RUNNING.value,
        attempt_count=1,
        started_at=now.isoformat(),
        updated_at=now.isoformat(),
        deadline_at=(now + timedelta(seconds=40)).isoformat(),
        provider="openai_responses",
        model="test-model",
        prompt_hash="in-progress",
    ))
    provider = _Provider([{"summary": "must not run"}])

    with pytest.raises(RepairExecutionError, match="already active"):
        SupervisedRepairProvider(
            provider, _supervisor(repository)
        ).complete_json({"role": "closure_checker"})
    assert provider.calls == 0


def test_non_transient_failure_is_terminal_and_not_retried():
    repository = InMemoryRepairStageCheckpointRepository()
    provider = _Provider([ValueError("invalid closure response")])
    wrapped = SupervisedRepairProvider(provider, _supervisor(repository))

    with pytest.raises(ValueError, match="invalid closure"):
        wrapped.complete_json({"role": "closure_checker"})
    with pytest.raises(RepairExecutionError, match="invalid closure"):
        wrapped.complete_json({"role": "closure_checker"})

    assert provider.calls == 1
    checkpoint = repository.get("attempt-1", "closure_checker")
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.FAILED_TERMINAL.value
    assert not checkpoint.retryable


def test_provider_credit_failure_waits_without_automatic_retry():
    repository = InMemoryRepairStageCheckpointRepository()
    provider = _Provider([
        RuntimeError('LLM provider HTTP 402: {"error":"Insufficient Balance"}'),
        {"summary": "must not run automatically"},
    ])
    wrapped = SupervisedRepairProvider(provider, _supervisor(repository))

    with pytest.raises(RuntimeError, match="Insufficient Balance"):
        wrapped.complete_json({"role": "primary_revision"})
    with pytest.raises(RepairExecutionError, match="insufficient credit"):
        wrapped.complete_json({"role": "primary_revision"})

    assert provider.calls == 1
    checkpoint = repository.get("attempt-1", "primary_revision")
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.WAITING_FOR_PROVIDER.value
    assert checkpoint.failure_class == "provider_credit_required"
    assert "HTTP 402" not in checkpoint.summary


def test_bounded_safe_revision_handles_credit_failure_without_hiding_it():
    provider = _Provider([
        RuntimeError('LLM provider HTTP 402: {"error":"Insufficient Balance"}')
    ])
    wrapped = DeterministicSafeRevisionProvider(
        provider,
        revision_builder=lambda prompt, *, failure_class: {
            "summary": "Unsupported conclusions were removed.",
            "deterministic_safe_revision": {
                "applied": True,
                "failure_class": failure_class,
            },
        },
    )

    result = wrapped.complete_json({"role": "primary_revision"})

    assert provider.calls == 1
    assert wrapped.fallback_used is True
    assert result["deterministic_safe_revision"]["failure_class"] == (
        "provider_credit_required"
    )
    assert wrapped.last_cost_usd == 0.0


def test_bounded_safe_revision_handles_malformed_provider_json() -> None:
    provider = _Provider([
        json.JSONDecodeError("Unterminated string", '\"partial', 1)
    ])
    wrapped = DeterministicSafeRevisionProvider(
        provider,
        revision_builder=lambda prompt, *, failure_class: {
            "summary": "Unsupported conclusions were deterministically narrowed.",
            "deterministic_safe_revision": {
                "applied": True,
                "failure_class": failure_class,
            },
        },
        allowed_failure_classes=("invalid_response",),
    )

    result = wrapped.complete_json({"role": "primary_revision"})

    assert provider.calls == 1
    assert wrapped.fallback_used is True
    assert result["deterministic_safe_revision"]["failure_class"] == "invalid_response"


def test_bounded_safe_revision_never_replaces_initial_research():
    wrapped = DeterministicSafeRevisionProvider(
        None,
        revision_builder=lambda prompt, *, failure_class: {"summary": "safe"},
        allowed_failure_classes=("provider_unavailable",),
    )

    with pytest.raises(RepairExecutionError, match="outside bounded safe revision"):
        wrapped.complete_json({"role": "primary_investment_analyst"})


def test_explicit_new_supervisor_can_retry_provider_action_checkpoint():
    repository = InMemoryRepairStageCheckpointRepository()
    prompt = {"role": "primary_revision"}
    first = _Provider([
        RuntimeError('LLM provider HTTP 402: {"error":"Insufficient Balance"}')
    ])
    with pytest.raises(RuntimeError):
        SupervisedRepairProvider(
            first, _supervisor(repository)
        ).complete_json(prompt)

    resumed_provider = _Provider([{"summary": "revised after top-up"}])
    resumed = RepairStageSupervisor(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        repository=repository,
        policy=RepairExecutionPolicy(total_timeout_seconds=60),
        allow_provider_action_retry=True,
    )

    result = SupervisedRepairProvider(
        resumed_provider, resumed
    ).complete_json(prompt)

    assert result == {"summary": "revised after top-up"}
    assert resumed_provider.calls == 1
    checkpoint = repository.get("attempt-1", "primary_revision")
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.COMPLETED.value
    assert checkpoint.attempt_count == 1


def test_deterministic_stages_reexecute_but_keep_one_current_checkpoint():
    repository = InMemoryRepairStageCheckpointRepository()
    supervisor = _supervisor(repository)
    calls = []

    assert supervisor.record_deterministic_stage(
        RepairStage.DETERMINISTIC_VALIDATION,
        operation=lambda: calls.append("validated") or True,
    )
    assert supervisor.record_deterministic_stage(
        RepairStage.DETERMINISTIC_VALIDATION,
        operation=lambda: calls.append("validated") or True,
    )

    assert calls == ["validated", "validated"]
    assert len(repository.list_for_attempt("attempt-1")) == 1


def test_progress_reports_four_bounded_repair_stages():
    updates = []
    provider = _Provider([{"summary": "revised"}])
    wrapped = SupervisedRepairProvider(
        provider,
        _supervisor(
            InMemoryRepairStageCheckpointRepository(),
            progress=updates.append,
        ),
    )

    wrapped.complete_json({"role": "primary_revision"})

    assert updates[0]["repair_stage_index"] == 1
    assert updates[0]["repair_stage_count"] == 4
    assert updates[-1]["status"] == "completed"
    assert "Primary response" in updates[-1]["current_stage"]


def test_codex_execution_telemetry_is_durable_and_coarse() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    updates = []
    provider = _ObservableProvider([{"summary": "closed"}])
    wrapped = SupervisedRepairProvider(
        provider,
        _supervisor(repository, progress=updates.append),
    )

    wrapped.complete_json({"role": "closure_checker"})

    checkpoint = repository.get("attempt-1", "closure_checker")
    assert checkpoint is not None
    assert checkpoint.execution_state == "completed"
    assert checkpoint.execution_id
    assert checkpoint.execution_telemetry["event_type"] == "turn.completed"
    assert checkpoint.execution_telemetry["usage"] == {
        "input_tokens": 10,
        "output_tokens": 3,
    }
    assert "technical-thread" not in checkpoint.summary
    assert any(update["execution_state"] == "active" for update in updates)


def test_sqlite_repository_survives_reopen(tmp_path: Path):
    path = tmp_path / "research.db"
    first_repository = SqliteRepairStageCheckpointRepository(path)
    provider = _Provider([{"summary": "closed"}])
    prompt = {"role": "closure_checker", "evidence_hash": "evidence-1"}
    SupervisedRepairProvider(
        provider,
        _supervisor(first_repository),
    ).complete_json(prompt)

    reopened = SqliteRepairStageCheckpointRepository(path)
    second_provider = _Provider([{"summary": "should not run"}])
    result = SupervisedRepairProvider(
        second_provider,
        _supervisor(reopened),
    ).complete_json(prompt)

    assert result == {"summary": "closed"}
    assert second_provider.calls == 0
    assert reopened.list_for_attempt("attempt-1")[0].result_hash


def test_sqlite_repository_migrates_legacy_insufficient_balance_checkpoint(
    tmp_path: Path,
):
    path = tmp_path / "research.db"
    repository = SqliteRepairStageCheckpointRepository(path)
    repository.save(RepairStageCheckpoint(
        attempt_id="legacy-credit",
        parent_run_id="parent-1",
        ticker="BABA",
        evidence_hash="evidence-1",
        stage=RepairStage.PRIMARY_REVISION.value,
        state=RepairStageState.FAILED_TERMINAL.value,
        attempt_count=1,
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        deadline_at="2026-08-20T00:05:00+00:00",
        failure_class="RuntimeError",
        summary=(
            'LLM provider HTTP 402: {"error":{"message":'
            '"Insufficient Balance"}}'
        ),
    ))

    migrated = SqliteRepairStageCheckpointRepository(path).get(
        "legacy-credit", RepairStage.PRIMARY_REVISION.value
    )

    assert migrated is not None
    assert migrated.state == RepairStageState.WAITING_FOR_PROVIDER.value
    assert migrated.failure_class == "provider_credit_required"
    assert "HTTP 402" not in migrated.summary


def test_total_repair_deadline_survives_process_restart():
    repository = InMemoryRepairStageCheckpointRepository()
    now = datetime.now(timezone.utc)
    repository.save(RepairStageCheckpoint(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        stage=RepairStage.PRIMARY_REVISION.value,
        state=RepairStageState.FAILED_RETRYABLE.value,
        attempt_count=1,
        started_at=(now - timedelta(seconds=120)).isoformat(),
        updated_at=(now - timedelta(seconds=90)).isoformat(),
        deadline_at=(now - timedelta(seconds=60)).isoformat(),
        retryable=True,
    ))
    supervisor = RepairStageSupervisor(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        repository=repository,
        policy=RepairExecutionPolicy(total_timeout_seconds=60),
        wall_clock=lambda: now,
    )

    with pytest.raises(
        RepairExecutionDeadlineExceeded,
        match="bounded repair deadline",
    ):
        supervisor.record_deterministic_stage(
            RepairStage.DETERMINISTIC_VALIDATION,
            operation=lambda: True,
        )


def test_stale_running_codex_checkpoint_becomes_retryable_interruption() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    now = datetime.now(timezone.utc)
    repository.save(RepairStageCheckpoint(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        stage=RepairStage.INDEPENDENT_CLOSURE.value,
        state=RepairStageState.RUNNING.value,
        attempt_count=1,
        started_at=(now - timedelta(minutes=5)).isoformat(),
        updated_at=(now - timedelta(minutes=3)).isoformat(),
        deadline_at=(now + timedelta(minutes=10)).isoformat(),
        last_activity_at=(now - timedelta(minutes=3)).isoformat(),
        hard_deadline_at=(now + timedelta(minutes=10)).isoformat(),
        execution_state="active",
    ))

    RepairStageSupervisor(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        repository=repository,
        policy=RepairExecutionPolicy(
            total_timeout_seconds=1500,
            stale_worker_seconds=120,
        ),
        wall_clock=lambda: now,
        authorized_retry_stages=("closure_checker",),
    )

    recovered = repository.get("attempt-1", "closure_checker")
    assert recovered is not None
    assert recovered.state == RepairStageState.INTERRUPTED.value
    assert recovered.execution_state == "interrupted_by_restart"
    assert "completed stages were preserved" in recovered.summary


def test_expired_attempt_allows_zero_call_deterministic_recheck() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    now = datetime.now(timezone.utc)
    for stage in (
        RepairStage.PRIMARY_REVISION,
        RepairStage.INDEPENDENT_CLOSURE,
    ):
        repository.save(RepairStageCheckpoint(
            attempt_id="attempt-1",
            parent_run_id="parent-1",
            ticker="NVDA",
            evidence_hash="evidence-1",
            stage=stage.value,
            state=RepairStageState.COMPLETED.value,
            attempt_count=1,
            started_at=(now - timedelta(seconds=120)).isoformat(),
            updated_at=(now - timedelta(seconds=90)).isoformat(),
            deadline_at=(now - timedelta(seconds=60)).isoformat(),
            completed_at=(now - timedelta(seconds=90)).isoformat(),
            result_payload={"summary": f"{stage.value} complete"},
        ))
    supervisor = RepairStageSupervisor(
        attempt_id="attempt-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        repository=repository,
        policy=RepairExecutionPolicy(total_timeout_seconds=60),
        wall_clock=lambda: now,
    )

    assert supervisor.record_deterministic_stage(
        RepairStage.DETERMINISTIC_VALIDATION,
        operation=lambda: True,
    ) is True


def test_publication_evaluation_runs_after_deadline_to_record_blockers() -> None:
    repository = InMemoryRepairStageCheckpointRepository()
    now = datetime.now(timezone.utc)
    repository.save(RepairStageCheckpoint(
        attempt_id="attempt-publication-timeout",
        parent_run_id="parent-blk",
        ticker="BLK",
        evidence_hash="evidence-blk",
        stage=RepairStage.PRIMARY_REVISION.value,
        state=RepairStageState.DEADLINE_EXCEEDED.value,
        attempt_count=1,
        started_at=(now - timedelta(seconds=120)).isoformat(),
        updated_at=(now - timedelta(seconds=90)).isoformat(),
        deadline_at=(now - timedelta(seconds=60)).isoformat(),
        completed_at=(now - timedelta(seconds=90)).isoformat(),
        result_payload={},
    ))
    supervisor = RepairStageSupervisor(
        attempt_id="attempt-publication-timeout",
        parent_run_id="parent-blk",
        ticker="BLK",
        evidence_hash="evidence-blk",
        repository=repository,
        policy=RepairExecutionPolicy(total_timeout_seconds=60),
        wall_clock=lambda: now,
    )

    decision = supervisor.record_deterministic_stage(
        RepairStage.PUBLICATION_EVALUATION,
        operation=lambda: {
            "allowed": False,
            "blocker": "Independent closure is incomplete.",
        },
    )

    assert decision["allowed"] is False
    checkpoint = repository.get(
        "attempt-publication-timeout",
        RepairStage.PUBLICATION_EVALUATION.value,
    )
    assert checkpoint is not None
    assert checkpoint.state == RepairStageState.COMPLETED.value
