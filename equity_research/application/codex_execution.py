from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol


CODEX_EXECUTION_TRANSPORT_VERSION = "codex-jsonl-execution-v1"


class CodexExecutionState(str, Enum):
    STARTING = "starting"
    ACTIVE = "active"
    QUIET = "quiet_but_running"
    VALIDATING_OUTPUT = "validating_output"
    COMPLETED = "completed"
    AUTHENTICATION_REQUIRED = "authentication_required"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    HARD_DEADLINE_EXCEEDED = "hard_deadline_exceeded"
    PROCESS_EXIT_FAILED = "process_exit_failed"
    OUTPUT_SCHEMA_FAILED = "output_schema_failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted_by_restart"


@dataclass(frozen=True)
class CodexExecutionPolicy:
    """Observable process limits; quiet periods are informational, not fatal."""

    startup_timeout_seconds: int = 60
    quiet_after_seconds: int = 180
    hard_timeout_seconds: int = 1200
    checkpoint_interval_seconds: int = 30
    policy_version: str = "codex-execution-policy-v1"

    def __post_init__(self) -> None:
        if self.startup_timeout_seconds < 1:
            raise ValueError("Codex startup timeout must be positive.")
        if self.quiet_after_seconds < 1:
            raise ValueError("Codex quiet threshold must be positive.")
        if self.hard_timeout_seconds < self.startup_timeout_seconds:
            raise ValueError(
                "Codex hard timeout cannot be shorter than its startup timeout."
            )
        if self.checkpoint_interval_seconds < 1:
            raise ValueError("Codex checkpoint interval must be positive.")


@dataclass(frozen=True)
class CodexExecutionEvent:
    state: str
    event_type: str
    occurred_at: str
    thread_id: str = ""
    item_type: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    message: str = ""
    unknown_event_count: int = 0
    transport_version: str = CODEX_EXECUTION_TRANSPORT_VERSION


@dataclass(frozen=True)
class CodexExecutionResult:
    returncode: int
    thread_id: str = ""
    turn_completed: bool = False
    usage: dict[str, int] = field(default_factory=dict)
    stderr_summary: str = ""
    unknown_event_count: int = 0
    transport_version: str = CODEX_EXECUTION_TRANSPORT_VERSION


class CodexExecutionFailure(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        retryable: bool = False,
        turn_started: bool = False,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.retryable = retryable
        self.turn_started = turn_started


class CodexExecutionRunner(Protocol):
    def run(
        self,
        command: list[str],
        *,
        prompt: str,
        cwd: str,
        policy: CodexExecutionPolicy,
        on_event: Callable[[CodexExecutionEvent], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> CodexExecutionResult: ...

    def cancel(self) -> None: ...


def execution_event_to_safe_dict(event: Any) -> dict[str, Any]:
    """Return the small lifecycle projection permitted in durable records."""

    usage = getattr(event, "usage", {}) or {}
    return {
        "state": str(getattr(event, "state", "") or ""),
        "event_type": str(getattr(event, "event_type", "") or ""),
        "occurred_at": str(getattr(event, "occurred_at", "") or ""),
        "thread_id": str(getattr(event, "thread_id", "") or ""),
        "item_type": str(getattr(event, "item_type", "") or ""),
        "usage": {
            str(key): max(0, int(value or 0))
            for key, value in dict(usage).items()
            if isinstance(value, (int, float))
        },
        "unknown_event_count": max(
            0,
            int(getattr(event, "unknown_event_count", 0) or 0),
        ),
        "transport_version": str(
            getattr(event, "transport_version", "")
            or CODEX_EXECUTION_TRANSPORT_VERSION
        ),
    }
