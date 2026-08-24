from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import threading
import time
from typing import Any, Callable, Protocol

from .codex_execution import execution_event_to_safe_dict


REPAIR_STAGE_SCHEMA_VERSION = "repair-stage-checkpoint-v2"


class RepairStage(str, Enum):
    PRIMARY_REVISION = "primary_revision"
    DETERMINISTIC_VALIDATION = "deterministic_validation"
    INDEPENDENT_CLOSURE = "closure_checker"
    PUBLICATION_EVALUATION = "publication_evaluation"

    @property
    def label(self) -> str:
        return {
            self.PRIMARY_REVISION: "Primary response and revision",
            self.DETERMINISTIC_VALIDATION: "Deterministic validation",
            self.INDEPENDENT_CLOSURE: "Independent QA closure",
            self.PUBLICATION_EVALUATION: "Publication evaluation",
        }[self]


REPAIR_STAGE_ORDER = tuple(RepairStage)


class RepairStageState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    WAITING_FOR_PROVIDER = "waiting_for_provider"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class RepairExecutionPolicy:
    revision_timeout_seconds: int = 300
    closure_timeout_seconds: int = 480
    validation_timeout_seconds: int = 120
    publication_timeout_seconds: int = 60
    total_timeout_seconds: int = 900
    max_transient_retries: int = 1
    stale_worker_seconds: int = 120
    policy_version: str = "repair-execution-policy-v1"

    def timeout_for(self, stage: RepairStage) -> int:
        return {
            RepairStage.PRIMARY_REVISION: self.revision_timeout_seconds,
            RepairStage.DETERMINISTIC_VALIDATION: self.validation_timeout_seconds,
            RepairStage.INDEPENDENT_CLOSURE: self.closure_timeout_seconds,
            RepairStage.PUBLICATION_EVALUATION: self.publication_timeout_seconds,
        }[stage]


@dataclass(frozen=True)
class RepairStageCheckpoint:
    attempt_id: str
    parent_run_id: str
    ticker: str
    evidence_hash: str
    stage: str
    state: str
    attempt_count: int
    started_at: str
    updated_at: str
    deadline_at: str
    completed_at: str = ""
    provider: str = ""
    model: str = ""
    prompt_hash: str = ""
    result_hash: str = ""
    result_payload: dict[str, Any] = field(default_factory=dict)
    failure_class: str = ""
    summary: str = ""
    retryable: bool = False
    execution_id: str = ""
    execution_state: str = ""
    last_activity_at: str = ""
    hard_deadline_at: str = ""
    transport_version: str = ""
    execution_telemetry: dict[str, Any] = field(default_factory=dict)
    policy_version: str = "repair-execution-policy-v1"
    schema_version: str = REPAIR_STAGE_SCHEMA_VERSION


class RepairStageCheckpointRepository(Protocol):
    def get(self, attempt_id: str, stage: str) -> RepairStageCheckpoint | None: ...

    def save(self, checkpoint: RepairStageCheckpoint) -> None: ...

    def claim(
        self,
        checkpoint: RepairStageCheckpoint,
        *,
        expected_states: tuple[str, ...],
    ) -> bool: ...

    def list_for_attempt(self, attempt_id: str) -> list[RepairStageCheckpoint]: ...


class InMemoryRepairStageCheckpointRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], RepairStageCheckpoint] = {}
        self._lock = threading.Lock()

    def get(self, attempt_id: str, stage: str) -> RepairStageCheckpoint | None:
        with self._lock:
            return self._items.get((attempt_id, stage))

    def save(self, checkpoint: RepairStageCheckpoint) -> None:
        with self._lock:
            self._items[(checkpoint.attempt_id, checkpoint.stage)] = checkpoint

    def claim(
        self,
        checkpoint: RepairStageCheckpoint,
        *,
        expected_states: tuple[str, ...],
    ) -> bool:
        key = (checkpoint.attempt_id, checkpoint.stage)
        with self._lock:
            current = self._items.get(key)
            current_state = current.state if current is not None else "__missing__"
            if current_state not in expected_states:
                return False
            self._items[key] = checkpoint
            return True

    def list_for_attempt(self, attempt_id: str) -> list[RepairStageCheckpoint]:
        with self._lock:
            items = [
                value for (stored_attempt, _), value in self._items.items()
                if stored_attempt == attempt_id
            ]
        order = {stage.value: index for index, stage in enumerate(REPAIR_STAGE_ORDER)}
        return sorted(items, key=lambda item: order.get(item.stage, len(order)))


class RepairExecutionError(RuntimeError):
    pass


class RepairExecutionDeadlineExceeded(RepairExecutionError):
    pass


class RepairStageSupervisor:
    """Bounded, resumable execution for the small live portion of a repair.

    Provider prompts are never persisted. A completed model payload is retained
    so a process restart can resume at the next stage without paying for the
    same response twice.
    """

    def __init__(
        self,
        *,
        attempt_id: str,
        parent_run_id: str,
        ticker: str,
        evidence_hash: str,
        repository: RepairStageCheckpointRepository,
        policy: RepairExecutionPolicy | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
        allow_provider_action_retry: bool = False,
        authorized_retry_stages: tuple[str, ...] = (),
    ) -> None:
        if not attempt_id:
            raise ValueError("A repair attempt ID is required.")
        if not parent_run_id:
            raise ValueError("A parent run ID is required for bounded repair.")
        self.attempt_id = attempt_id
        self.parent_run_id = parent_run_id
        self.ticker = ticker.strip().upper()
        self.evidence_hash = evidence_hash
        self.repository = repository
        self.policy = policy or RepairExecutionPolicy()
        self.progress_callback = progress_callback
        self.cancel_requested = cancel_requested
        self.monotonic = monotonic
        self._started = monotonic()
        self._last_execution_persisted: dict[str, float] = {}
        self.wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._recover_stale_running_checkpoints()
        existing = repository.list_for_attempt(attempt_id)
        self.allow_provider_action_retry = allow_provider_action_retry
        self.authorized_retry_stages = frozenset(
            str(item) for item in authorized_retry_stages
            if str(item) in {stage.value for stage in RepairStage}
        )
        self._provider_action_stages_at_start = {
            item.stage for item in existing if _checkpoint_requires_provider_action(item)
        }
        self._authorized_retry_stages_at_start = {
            item.stage for item in existing
            if item.stage in self.authorized_retry_stages
            and item.state in {
                RepairStageState.DEADLINE_EXCEEDED.value,
                RepairStageState.INTERRUPTED.value,
            }
        }
        parsed_starts = [
            _parse_utc(item.started_at) for item in existing if item.started_at
        ]
        self._attempt_started_at = (
            self.wall_clock()
            if (
                self.allow_provider_action_retry
                and self._provider_action_stages_at_start
            ) or self._authorized_retry_stages_at_start
            else min(
                (value for value in parsed_starts if value is not None),
                default=self.wall_clock(),
            )
        )

    def execute_model_stage(
        self,
        stage: RepairStage,
        *,
        provider: Any,
        prompt: dict[str, Any],
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        cached = self.repository.get(self.attempt_id, stage.value)
        prompt_hash = _stable_hash(
            {
                "idempotency_key": prompt.get("idempotency_key"),
                "role": prompt.get("role"),
                "evidence_hash": self.evidence_hash,
                "parent_run_id": self.parent_run_id,
            }
            if prompt.get("idempotency_key")
            else prompt
        )
        if (
            cached is not None
            and cached.state == RepairStageState.COMPLETED.value
            and cached.prompt_hash == prompt_hash
            and cached.result_payload
        ):
            self._notify(stage, cached, "Reused the verified repair-stage result.")
            return dict(cached.result_payload)
        if cached is not None and cached.state == RepairStageState.RUNNING.value:
            raise RepairExecutionError(
                f"{stage.label} is already active for this repair attempt."
            )
        explicit_provider_retry = bool(
            cached is not None
            and stage.value in self._provider_action_stages_at_start
            and self.allow_provider_action_retry
        )
        explicit_stage_retry = bool(
            cached is not None
            and stage.value in self._authorized_retry_stages_at_start
            and cached.prompt_hash == prompt_hash
            and (
                cached.state == RepairStageState.INTERRUPTED.value
                or _retry_changes_execution_boundary(
                    cached,
                    provider=provider,
                    policy=self.policy,
                    stage=stage,
                )
            )
        )
        if (
            cached is not None
            and cached.state in {
                RepairStageState.FAILED_TERMINAL.value,
                RepairStageState.WAITING_FOR_PROVIDER.value,
                RepairStageState.DEADLINE_EXCEEDED.value,
                RepairStageState.CANCELLED.value,
                RepairStageState.INTERRUPTED.value,
            }
            and not explicit_provider_retry
            and not explicit_stage_retry
        ):
            raise RepairExecutionError(
                cached.summary
                or f"{stage.label} previously stopped with a terminal failure."
            )

        cached_attempt_count = int(getattr(cached, "attempt_count", 0) or 0)
        max_attempts = 1 + max(0, self.policy.max_transient_retries)
        attempt_count = 0 if explicit_provider_retry else cached_attempt_count
        if explicit_stage_retry:
            # One user-authorized retry means exactly one new provider call.
            # Another timeout remains durable and requires another explicit
            # action rather than multiplying calls automatically.
            max_attempts = cached_attempt_count + 1
        expected_claim_state = (
            cached.state if cached is not None else "__missing__"
        )
        last_error: Exception | None = None
        while attempt_count < max_attempts:
            self._check_cancelled(stage, provider)
            timeout_seconds = self._remaining_timeout(stage)
            attempt_count += 1
            now = _utc_now()
            checkpoint = RepairStageCheckpoint(
                attempt_id=self.attempt_id,
                parent_run_id=self.parent_run_id,
                ticker=self.ticker,
                evidence_hash=self.evidence_hash,
                stage=stage.value,
                state=RepairStageState.RUNNING.value,
                attempt_count=attempt_count,
                started_at=(cached.started_at if cached and cached.started_at else now),
                updated_at=now,
                deadline_at=_deadline_at(timeout_seconds),
                provider=str(getattr(provider, "provider_name", "") or ""),
                model=str(getattr(provider, "model", "") or ""),
                prompt_hash=prompt_hash,
                summary=f"{stage.label} is running.",
                execution_id=_stable_hash({
                    "attempt_id": self.attempt_id,
                    "stage": stage.value,
                    "attempt_count": attempt_count,
                    "prompt_hash": prompt_hash,
                    "provider": str(
                        getattr(provider, "provider_name", "") or ""
                    ),
                })[:24],
                execution_state="starting",
                last_activity_at=now,
                hard_deadline_at=_deadline_at(timeout_seconds),
                policy_version=self.policy.policy_version,
            )
            claim = getattr(self.repository, "claim", None)
            claimed = (
                claim(checkpoint, expected_states=(expected_claim_state,))
                if callable(claim)
                else True
            )
            if not claimed:
                current = self.repository.get(self.attempt_id, stage.value)
                if (
                    current is not None
                    and current.state == RepairStageState.COMPLETED.value
                    and current.prompt_hash == prompt_hash
                    and current.result_payload
                ):
                    return dict(current.result_payload)
                raise RepairExecutionError(
                    f"{stage.label} is already active for this repair attempt."
                )
            if not callable(claim):
                self.repository.save(checkpoint)
            self._notify(stage, checkpoint, checkpoint.summary)
            prior_timeouts = _set_provider_timeout(provider, timeout_seconds)
            previous_event_callbacks = _set_provider_execution_callback(
                provider,
                lambda event: self._record_provider_execution_event(
                    stage,
                    event,
                ),
            )
            try:
                payload = operation()
                if not isinstance(payload, dict):
                    raise RepairExecutionError(
                        f"{stage.label} returned a non-object payload."
                    )
                completed_at = _utc_now()
                latest_checkpoint = (
                    self.repository.get(self.attempt_id, stage.value)
                    or checkpoint
                )
                completed = replace(
                    latest_checkpoint,
                    state=RepairStageState.COMPLETED.value,
                    updated_at=completed_at,
                    completed_at=completed_at,
                    result_hash=_stable_hash(payload),
                    result_payload=dict(payload),
                    summary=f"{stage.label} completed.",
                    retryable=False,
                    execution_state="completed",
                    last_activity_at=completed_at,
                )
                self.repository.save(completed)
                self._notify(stage, completed, completed.summary)
                return payload
            except Exception as exc:
                last_error = exc
                retryable = (
                    _is_transient_failure(exc)
                    and not _non_retryable_stage_timeout(stage, provider, exc)
                )
                provider_action = _provider_action_requirement(exc)
                state = (
                    RepairStageState.WAITING_FOR_PROVIDER.value
                    if provider_action is not None
                    else RepairStageState.DEADLINE_EXCEEDED.value
                    if isinstance(exc, (TimeoutError, RepairExecutionDeadlineExceeded))
                    else RepairStageState.FAILED_RETRYABLE.value
                    if retryable and attempt_count < max_attempts
                    else RepairStageState.FAILED_TERMINAL.value
                )
                failed_at = _utc_now()
                latest_checkpoint = (
                    self.repository.get(self.attempt_id, stage.value)
                    or checkpoint
                )
                failed = replace(
                    latest_checkpoint,
                    state=state,
                    updated_at=failed_at,
                    completed_at=(
                        failed_at
                        if state != RepairStageState.FAILED_RETRYABLE.value else ""
                    ),
                    failure_class=(
                        provider_action[0]
                        if provider_action is not None else type(exc).__name__
                    ),
                    summary=(
                        provider_action[1]
                        if provider_action is not None else _safe_summary(exc)
                    ),
                    retryable=retryable and attempt_count < max_attempts,
                    execution_state=(
                        "hard_deadline_exceeded"
                        if isinstance(
                            exc,
                            (TimeoutError, RepairExecutionDeadlineExceeded),
                        )
                        else str(
                            getattr(exc, "failure_class", "") or "failed"
                        )
                    ),
                    last_activity_at=failed_at,
                )
                self.repository.save(failed)
                expected_claim_state = failed.state
                self._notify(stage, failed, failed.summary)
                _cancel_provider(provider)
                if not failed.retryable:
                    raise
            finally:
                _restore_provider_execution_callbacks(
                    previous_event_callbacks
                )
                _restore_provider_timeouts(prior_timeouts)
        assert last_error is not None
        raise last_error

    def record_deterministic_stage(
        self,
        stage: RepairStage,
        *,
        operation: Callable[[], Any],
        result_payload: Callable[[Any], dict[str, Any]] | None = None,
    ) -> Any:
        # Deterministic stages are cheap and may rebuild in-memory validation
        # side effects after a process restart. Record them idempotently, but
        # execute them again rather than treating the stored scalar as state.
        cached = self.repository.get(self.attempt_id, stage.value)
        self._check_cancelled(stage, None)
        try:
            timeout_seconds = self._remaining_timeout(stage)
        except RepairExecutionDeadlineExceeded:
            # Publication evaluation is the terminal truth-telling step. It
            # must still run after a model timeout so the run becomes a
            # readable non-publishable draft instead of escaping as a
            # traceback. It is deterministic and cannot call a provider.
            # Other deterministic replay is allowed only when both paid model
            # stages are durably complete.
            if (
                stage is not RepairStage.PUBLICATION_EVALUATION
                and not self._completed_model_replay_available()
            ):
                raise
            timeout_seconds = self.policy.timeout_for(stage)
        now = _utc_now()
        checkpoint = RepairStageCheckpoint(
            attempt_id=self.attempt_id,
            parent_run_id=self.parent_run_id,
            ticker=self.ticker,
            evidence_hash=self.evidence_hash,
            stage=stage.value,
            state=RepairStageState.RUNNING.value,
            attempt_count=int(getattr(cached, "attempt_count", 0) or 0) + 1,
            started_at=(cached.started_at if cached and cached.started_at else now),
            updated_at=now,
            deadline_at=_deadline_at(timeout_seconds),
            provider="deterministic",
            summary=f"{stage.label} is running.",
            policy_version=self.policy.policy_version,
        )
        self.repository.save(checkpoint)
        self._notify(stage, checkpoint, checkpoint.summary)
        try:
            value = operation()
        except Exception as exc:
            failed_at = _utc_now()
            failed = replace(
                checkpoint,
                state=RepairStageState.FAILED_TERMINAL.value,
                updated_at=failed_at,
                completed_at=failed_at,
                failure_class=type(exc).__name__,
                summary=_safe_summary(exc),
            )
            self.repository.save(failed)
            self._notify(stage, failed, failed.summary)
            raise
        payload = result_payload(value) if result_payload else {"value": value}
        completed_at = _utc_now()
        completed = replace(
            checkpoint,
            state=RepairStageState.COMPLETED.value,
            updated_at=completed_at,
            completed_at=completed_at,
            result_hash=_stable_hash(payload),
            result_payload=payload,
            summary=f"{stage.label} completed.",
        )
        self.repository.save(completed)
        self._notify(stage, completed, completed.summary)
        return value

    def _completed_model_replay_available(self) -> bool:
        return all(
            (
                checkpoint := self.repository.get(
                    self.attempt_id,
                    stage.value,
                )
            ) is not None
            and checkpoint.state == RepairStageState.COMPLETED.value
            and bool(checkpoint.result_payload)
            for stage in (
                RepairStage.PRIMARY_REVISION,
                RepairStage.INDEPENDENT_CLOSURE,
            )
        )

    def _recover_stale_running_checkpoints(self) -> None:
        now = self.wall_clock()
        for checkpoint in self.repository.list_for_attempt(self.attempt_id):
            if checkpoint.state != RepairStageState.RUNNING.value:
                continue
            deadline = _parse_utc(
                checkpoint.hard_deadline_at or checkpoint.deadline_at
            )
            last_seen = _parse_utc(
                checkpoint.last_activity_at or checkpoint.updated_at
            )
            hard_deadline_passed = deadline is not None and deadline <= now
            worker_stale = (
                last_seen is not None
                and (now - last_seen).total_seconds()
                >= max(30, self.policy.stale_worker_seconds)
            )
            if not hard_deadline_passed and not worker_stale:
                continue
            recovered_at = now.isoformat()
            self.repository.save(replace(
                checkpoint,
                state=(
                    RepairStageState.DEADLINE_EXCEEDED.value
                    if hard_deadline_passed
                    else RepairStageState.INTERRUPTED.value
                ),
                updated_at=recovered_at,
                completed_at=recovered_at,
                failure_class=(
                    "hard_deadline_exceeded"
                    if hard_deadline_passed
                    else "interrupted_by_restart"
                ),
                summary=(
                    "The prior local execution exceeded its hard deadline; "
                    "completed stages were preserved."
                    if hard_deadline_passed
                    else "The prior local execution was interrupted; completed "
                    "stages were preserved and this stage can be retried."
                ),
                retryable=False,
                execution_state=(
                    "hard_deadline_exceeded"
                    if hard_deadline_passed
                    else "interrupted_by_restart"
                ),
            ))

    def _remaining_timeout(self, stage: RepairStage) -> int:
        durable_elapsed = max(
            0,
            int((self.wall_clock() - self._attempt_started_at).total_seconds()),
        )
        process_elapsed = max(0, int(self.monotonic() - self._started))
        total_remaining = self.policy.total_timeout_seconds - max(
            durable_elapsed,
            process_elapsed,
        )
        if total_remaining <= 0:
            raise RepairExecutionDeadlineExceeded(
                "The bounded repair deadline was reached. Completed stages were preserved."
            )
        return max(1, min(self.policy.timeout_for(stage), total_remaining))

    def _check_cancelled(self, stage: RepairStage, provider: Any) -> None:
        if self.cancel_requested is None or not self.cancel_requested():
            return
        _cancel_provider(provider)
        now = _utc_now()
        cancelled = RepairStageCheckpoint(
            attempt_id=self.attempt_id,
            parent_run_id=self.parent_run_id,
            ticker=self.ticker,
            evidence_hash=self.evidence_hash,
            stage=stage.value,
            state=RepairStageState.CANCELLED.value,
            attempt_count=0,
            started_at=now,
            updated_at=now,
            deadline_at=now,
            completed_at=now,
            summary="Repair and recheck was cancelled; completed stages were preserved.",
            policy_version=self.policy.policy_version,
        )
        self.repository.save(cancelled)
        self._notify(stage, cancelled, cancelled.summary)
        raise RepairExecutionError(cancelled.summary)

    def _notify(
        self,
        stage: RepairStage,
        checkpoint: RepairStageCheckpoint,
        message: str,
    ) -> None:
        if self.progress_callback is None:
            return
        index = REPAIR_STAGE_ORDER.index(stage) + 1
        try:
            self.progress_callback({
                "status": (
                    "running"
                    if checkpoint.state == RepairStageState.RUNNING.value
                    else checkpoint.state
                ),
                "current_stage": stage.label,
                "current_role": stage.value,
                "message": message,
                "provider": checkpoint.provider,
                "model": checkpoint.model,
                "retry_count": max(0, checkpoint.attempt_count - 1),
                "failure_class": checkpoint.failure_class,
                "repair_stage_index": index,
                "repair_stage_count": len(REPAIR_STAGE_ORDER),
                "repair_deadline_at": checkpoint.deadline_at,
                "execution_state": checkpoint.execution_state,
                "execution_last_activity_at": checkpoint.last_activity_at,
                "execution_hard_deadline_at": checkpoint.hard_deadline_at,
            })
        except Exception:
            return

    def _record_provider_execution_event(
        self,
        stage: RepairStage,
        event: Any,
    ) -> None:
        checkpoint = self.repository.get(self.attempt_id, stage.value)
        if (
            checkpoint is None
            or checkpoint.state != RepairStageState.RUNNING.value
        ):
            return
        telemetry = execution_event_to_safe_dict(event)
        event_state = str(telemetry.get("state") or "active")
        occurred_at = str(telemetry.get("occurred_at") or _utc_now())
        now = self.monotonic()
        last_persisted = self._last_execution_persisted.get(stage.value, 0.0)
        state_changed = event_state != checkpoint.execution_state
        final_state = event_state in {
            "completed", "authentication_required", "provider_unavailable",
            "hard_deadline_exceeded", "process_exit_failed",
            "output_schema_failed", "cancelled",
        }
        if (
            not state_changed
            and not final_state
            and now - last_persisted < 30
        ):
            return
        updated = replace(
            checkpoint,
            updated_at=occurred_at,
            execution_state=event_state,
            last_activity_at=occurred_at,
            transport_version=str(
                telemetry.get("transport_version") or ""
            ),
            execution_telemetry=telemetry,
            summary=_execution_summary(stage, event_state),
        )
        self.repository.save(updated)
        self._last_execution_persisted[stage.value] = now
        self._notify(stage, updated, updated.summary)


class SupervisedRepairProvider:
    """Delegate provider that checkpoints only primary revision and closure."""

    def __init__(self, delegate: Any, supervisor: RepairStageSupervisor) -> None:
        self.delegate = delegate
        self.supervisor = supervisor
        self.provider_name = str(getattr(delegate, "provider_name", "") or "")
        self.model = str(getattr(delegate, "model", "") or "")
        self.enabled = bool(getattr(delegate, "enabled", True))
        self.last_usage: dict[str, Any] = {}
        self.last_cost_usd: float | None = None

    def complete_json(self, prompt: dict[str, Any]) -> dict[str, Any]:
        role = str(prompt.get("role") or "")
        stage = {
            RepairStage.PRIMARY_REVISION.value: RepairStage.PRIMARY_REVISION,
            RepairStage.INDEPENDENT_CLOSURE.value: RepairStage.INDEPENDENT_CLOSURE,
        }.get(role)
        if stage is None:
            payload = self.delegate.complete_json(prompt)
        else:
            payload = self.supervisor.execute_model_stage(
                stage,
                provider=self.delegate,
                prompt=prompt,
                operation=lambda: self.delegate.complete_json(prompt),
            )
        self.last_usage = dict(getattr(self.delegate, "last_usage", {}) or {})
        self.last_cost_usd = getattr(self.delegate, "last_cost_usd", None)
        return payload

    def cancel(self) -> None:
        _cancel_provider(self.delegate)

    def set_execution_event_callback(
        self,
        callback: Callable[[Any], None] | None,
    ) -> Any:
        setter = getattr(self.delegate, "set_execution_event_callback", None)
        return setter(callback) if callable(setter) else None


class DeterministicSafeRevisionProvider:
    """Use a conservative revision only for an explicitly permitted stop.

    The wrapper is intended for bounded repair replay after the primary
    research and frozen audit already exist. It never substitutes for initial
    research and never supplies final closure.
    """

    def __init__(
        self,
        delegate: Any | None,
        *,
        revision_builder: Callable[..., dict[str, Any]],
        allowed_failure_classes: tuple[str, ...] = (
            "provider_credit_required",
        ),
    ) -> None:
        self.delegate = delegate
        self.revision_builder = revision_builder
        self.allowed_failure_classes = frozenset(allowed_failure_classes)
        self.provider_name = str(
            getattr(delegate, "provider_name", "")
            or "deterministic_safe_revision"
        )
        self.model = str(
            getattr(delegate, "model", "")
            or "deterministic_safe_revision_v1"
        )
        self.enabled = True
        self.last_usage: dict[str, Any] = {}
        self.last_cost_usd: float | None = 0.0
        self.fallback_used = False
        self.fallback_failure_class = ""

    def complete_json(self, prompt: dict[str, Any]) -> dict[str, Any]:
        role = str(prompt.get("role") or "")
        if role != RepairStage.PRIMARY_REVISION.value:
            if self.delegate is None:
                raise RepairExecutionError(
                    "A primary provider is required outside bounded safe revision."
                )
            return self.delegate.complete_json(prompt)

        failure_class = "provider_unavailable"
        if self.delegate is not None:
            try:
                payload = self.delegate.complete_json(prompt)
                self.last_usage = dict(
                    getattr(self.delegate, "last_usage", {}) or {}
                )
                self.last_cost_usd = getattr(
                    self.delegate, "last_cost_usd", None
                )
                return payload
            except Exception as exc:
                requirement = _provider_action_requirement(exc)
                if isinstance(exc, json.JSONDecodeError):
                    # The provider responded, but the bounded structured
                    # revision was truncated or malformed. Safe narrowing is
                    # preferable to repeating research or accepting prose.
                    failure_class = "invalid_response"
                elif requirement is not None:
                    failure_class = requirement[0]
                else:
                    raise
                if failure_class not in self.allowed_failure_classes:
                    raise
        elif failure_class not in self.allowed_failure_classes:
            raise RepairExecutionError(
                "Deterministic safe revision was not authorized for a missing provider."
            )

        payload = self.revision_builder(
            prompt,
            failure_class=failure_class,
        )
        if not isinstance(payload, dict):
            raise RepairExecutionError(
                "Deterministic safe revision returned a non-object payload."
            )
        self.fallback_used = True
        self.fallback_failure_class = failure_class
        self.last_usage = {}
        self.last_cost_usd = 0.0
        return payload

    def cancel(self) -> None:
        if self.delegate is not None:
            _cancel_provider(self.delegate)


def checkpoint_to_dict(checkpoint: RepairStageCheckpoint) -> dict[str, Any]:
    return asdict(checkpoint)


def _set_provider_timeout(provider: Any, timeout_seconds: int) -> list[tuple[Any, Any]]:
    changed: list[tuple[Any, Any]] = []
    current = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if hasattr(current, "timeout_seconds"):
            original = getattr(current, "timeout_seconds")
            changed.append((current, original))
            try:
                setattr(current, "timeout_seconds", min(int(original), timeout_seconds))
            except (TypeError, ValueError):
                setattr(current, "timeout_seconds", timeout_seconds)
        current = getattr(current, "delegate", None)
    return changed


def _restore_provider_timeouts(changed: list[tuple[Any, Any]]) -> None:
    for provider, timeout in reversed(changed):
        try:
            setattr(provider, "timeout_seconds", timeout)
        except Exception:
            continue


def _set_provider_execution_callback(
    provider: Any,
    callback: Callable[[Any], None],
) -> list[tuple[Any, Any]]:
    changed: list[tuple[Any, Any]] = []
    current = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        setter = getattr(current, "set_execution_event_callback", None)
        if callable(setter):
            previous = setter(None)
            def combined(event: Any, prior=previous) -> None:
                if callable(prior):
                    prior(event)
                callback(event)
            setter(combined)
            changed.append((current, previous))
            break
        current = getattr(current, "delegate", None)
    return changed


def _restore_provider_execution_callbacks(
    changed: list[tuple[Any, Any]],
) -> None:
    for provider, callback in reversed(changed):
        setter = getattr(provider, "set_execution_event_callback", None)
        if callable(setter):
            try:
                setter(callback)
            except Exception:
                continue


def _execution_summary(stage: RepairStage, state: str) -> str:
    return {
        "starting": f"{stage.label}: starting local Codex.",
        "active": f"{stage.label}: Codex is reviewing the scoped evidence.",
        "quiet_but_running": (
            f"{stage.label}: Codex is still running; no new progress event "
            "has arrived recently."
        ),
        "validating_output": (
            f"{stage.label}: validating the structured Codex response."
        ),
        "completed": f"{stage.label} completed.",
        "authentication_required": (
            f"{stage.label}: local Codex needs authentication."
        ),
        "provider_unavailable": (
            f"{stage.label}: local Codex is temporarily unavailable."
        ),
        "hard_deadline_exceeded": (
            f"{stage.label}: the hard deadline was reached; completed stages "
            "were preserved."
        ),
        "process_exit_failed": (
            f"{stage.label}: local Codex exited without a valid result."
        ),
        "output_schema_failed": (
            f"{stage.label}: the Codex response did not pass its schema."
        ),
        "cancelled": f"{stage.label} was cancelled.",
    }.get(state, f"{stage.label} is running.")


def _cancel_provider(provider: Any) -> None:
    current = provider
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        cancel = getattr(current, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                pass
        current = getattr(current, "delegate", None)


def _is_transient_failure(exc: Exception) -> bool:
    explicit = getattr(exc, "retryable", None)
    if explicit is not None:
        return bool(explicit)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    message = str(exc).casefold()
    return any(token in message for token in (
        "connection reset", "temporarily unavailable", "temporary failure",
        "http 502", "http 503", "http 504", "timed out", "timeout",
        "remote disconnected", "winerror 10054",
    ))


def _non_retryable_stage_timeout(
    stage: RepairStage, provider: Any, exc: Exception
) -> bool:
    """Do not repeat a full local closure computation in one user action."""

    return (
        stage is RepairStage.INDEPENDENT_CLOSURE
        and str(getattr(provider, "provider_name", "")).casefold()
        == "codex_local"
        and isinstance(exc, TimeoutError)
    )


def _provider_action_requirement(exc: Exception) -> tuple[str, str] | None:
    """Return a controlled, non-secret provider action for user-fixable stops."""

    failure_class = str(getattr(exc, "failure_class", "") or "")
    message = str(exc).casefold()
    if failure_class == "authentication_required":
        return (
            "provider_authentication_required",
            "Local Codex needs authentication. Sign in once, then explicitly "
            "resume this stage; completed research will be reused.",
        )
    if (
        "http 402" in message
        or "insufficient balance" in message
        or "insufficient_balance" in message
        or "insufficient quota" in message
        or "insufficient_quota" in message
    ):
        return (
            "provider_credit_required",
            "The selected model provider has insufficient credit. Add provider "
            "credit or select another eligible primary provider, then explicitly "
            "resume this repair; completed stages will be reused.",
        )
    if any(token in message for token in (
        "http 401", "invalid api key", "invalid_api_key", "authentication failed",
        "needs authentication", "login required", "run codex login",
    )):
        return (
            "provider_authentication_required",
            "The selected model provider needs authentication. Reconnect or "
            "select another eligible provider, then explicitly resume this "
            "repair; completed stages will be reused.",
        )
    return None


def _checkpoint_requires_provider_action(
    checkpoint: RepairStageCheckpoint,
) -> bool:
    if checkpoint.state == RepairStageState.WAITING_FOR_PROVIDER.value:
        return True
    if checkpoint.failure_class in {
        "provider_credit_required", "provider_authentication_required"
    }:
        return True
    # Compatibility for checkpoints written before provider-action states were
    # introduced. The original raw summary remains local and is replaced with
    # controlled copy on the next explicit retry.
    return _provider_action_requirement(RuntimeError(checkpoint.summary)) is not None


def _retry_changes_execution_boundary(
    checkpoint: RepairStageCheckpoint,
    *,
    provider: Any,
    policy: RepairExecutionPolicy,
    stage: RepairStage,
) -> bool:
    """Allow a terminal retry only after a material bounded-execution change."""

    provider_changed = any((
        str(checkpoint.provider or "")
        != str(getattr(provider, "provider_name", "") or ""),
        str(checkpoint.model or "")
        != str(getattr(provider, "model", "") or ""),
    ))
    previous_timeout = _checkpoint_timeout_seconds(checkpoint)
    timeout_increased = policy.timeout_for(stage) > previous_timeout
    return provider_changed or timeout_increased


def _checkpoint_timeout_seconds(checkpoint: RepairStageCheckpoint) -> int:
    started = _parse_utc(checkpoint.started_at)
    deadline = _parse_utc(checkpoint.deadline_at)
    if started is None or deadline is None:
        return 0
    return max(0, int((deadline - started).total_seconds()))


def _safe_summary(exc: Exception) -> str:
    text = " ".join(str(exc or type(exc).__name__).split())
    return text[:800] or type(exc).__name__


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _deadline_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _parse_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
