from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import queue
import subprocess
import threading
import time
from typing import Any, Callable

from ..application.codex_execution import (
    CODEX_EXECUTION_TRANSPORT_VERSION,
    CodexExecutionEvent,
    CodexExecutionFailure,
    CodexExecutionPolicy,
    CodexExecutionResult,
    CodexExecutionState,
)


class JsonlCodexExecutionRunner:
    """Run ``codex exec --json`` while retaining only coarse lifecycle data."""

    def __init__(
        self,
        *,
        popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._popen = popen
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._process: subprocess.Popen[str] | None = None
        self._cancelled = threading.Event()

    def run(
        self,
        command: list[str],
        *,
        prompt: str,
        cwd: str,
        policy: CodexExecutionPolicy,
        on_event: Callable[[CodexExecutionEvent], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> CodexExecutionResult:
        self._cancelled.clear()
        popen_options: dict[str, Any] = {}
        if os.name == "nt":
            popen_options["creationflags"] = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )
        else:
            popen_options["start_new_session"] = True
        try:
            self._process = self._popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=cwd,
                bufsize=1,
                **popen_options,
            )
        except OSError as exc:
            raise CodexExecutionFailure(
                "Local Codex could not be started.",
                failure_class="startup_failed",
                retryable=True,
            ) from exc

        process = self._process
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        stream_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
        readers = (
            threading.Thread(
                target=_read_stream,
                args=(process.stdout, "stdout", stream_queue),
                daemon=True,
            ),
            threading.Thread(
                target=_read_stream,
                args=(process.stderr, "stderr", stream_queue),
                daemon=True,
            ),
        )
        for reader in readers:
            reader.start()
        try:
            process.stdin.write(prompt)
            process.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            _terminate_process_tree(process)
            raise CodexExecutionFailure(
                "Local Codex closed before accepting the research input.",
                failure_class="startup_failed",
                retryable=True,
            ) from exc

        started = self._monotonic()
        last_activity = started
        last_heartbeat = started
        first_activity = False
        quiet_reported = False
        stream_closed: set[str] = set()
        thread_id = ""
        turn_started = False
        turn_completed = False
        terminal_event_failure = False
        usage: dict[str, int] = {}
        unknown_event_count = 0
        stderr_lines: list[str] = []
        self._emit(
            on_event,
            CodexExecutionState.STARTING,
            "process.started",
        )

        try:
            while True:
                now = self._monotonic()
                if self._cancelled.is_set() or (
                    cancel_requested is not None and cancel_requested()
                ):
                    _terminate_process_tree(process)
                    self._emit(
                        on_event,
                        CodexExecutionState.CANCELLED,
                        "process.cancelled",
                        thread_id=thread_id,
                    )
                    raise CodexExecutionFailure(
                        "Local Codex execution was cancelled.",
                        failure_class="cancelled",
                        turn_started=turn_started,
                    )
                if not first_activity and (
                    now - started >= policy.startup_timeout_seconds
                ):
                    _terminate_process_tree(process)
                    raise CodexExecutionFailure(
                        "Local Codex did not report startup activity before the deadline.",
                        failure_class="startup_timeout",
                        retryable=True,
                        turn_started=turn_started,
                    )
                if now - started >= policy.hard_timeout_seconds:
                    _terminate_process_tree(process)
                    self._emit(
                        on_event,
                        CodexExecutionState.HARD_DEADLINE_EXCEEDED,
                        "process.deadline_exceeded",
                        thread_id=thread_id,
                    )
                    raise CodexExecutionFailure(
                        "Local Codex reached its hard execution deadline; completed stages were preserved.",
                        failure_class="hard_deadline_exceeded",
                        turn_started=turn_started,
                    )
                if (
                    first_activity
                    and not quiet_reported
                    and now - last_activity >= policy.quiet_after_seconds
                    and process.poll() is None
                ):
                    quiet_reported = True
                    self._emit(
                        on_event,
                        CodexExecutionState.QUIET,
                        "process.quiet",
                        thread_id=thread_id,
                    )
                if (
                    first_activity
                    and now - last_heartbeat
                    >= policy.checkpoint_interval_seconds
                    and process.poll() is None
                ):
                    last_heartbeat = now
                    self._emit(
                        on_event,
                        (
                            CodexExecutionState.QUIET
                            if quiet_reported
                            else CodexExecutionState.ACTIVE
                        ),
                        "process.heartbeat",
                        thread_id=thread_id,
                    )

                try:
                    stream_name, line = stream_queue.get(timeout=0.2)
                except queue.Empty:
                    if process.poll() is not None and len(stream_closed) == 2:
                        break
                    continue
                if line is None:
                    stream_closed.add(stream_name)
                    if process.poll() is not None and len(stream_closed) == 2:
                        break
                    continue

                first_activity = True
                last_activity = self._monotonic()
                last_heartbeat = last_activity
                if quiet_reported:
                    quiet_reported = False
                    self._emit(
                        on_event,
                        CodexExecutionState.ACTIVE,
                        "process.activity_resumed",
                        thread_id=thread_id,
                    )
                if stream_name == "stderr":
                    if line.strip():
                        stderr_lines.append(line.strip())
                        stderr_lines = stderr_lines[-20:]
                        self._emit(
                            on_event,
                            CodexExecutionState.ACTIVE,
                            "stderr.activity",
                            thread_id=thread_id,
                        )
                    continue

                try:
                    payload = json.loads(line)
                except (TypeError, ValueError, json.JSONDecodeError):
                    unknown_event_count += 1
                    continue
                if not isinstance(payload, dict):
                    unknown_event_count += 1
                    continue
                event_type = str(payload.get("type") or "")
                if event_type == "thread.started":
                    thread_id = str(payload.get("thread_id") or "")
                elif event_type == "turn.started":
                    turn_started = True
                elif event_type == "turn.completed":
                    turn_completed = True
                    usage = _normalized_usage(payload.get("usage"))
                elif event_type in {"turn.failed", "error"}:
                    terminal_event_failure = True
                    self._emit(
                        on_event,
                        CodexExecutionState.PROVIDER_UNAVAILABLE,
                        event_type,
                        thread_id=thread_id,
                    )
                elif not (
                    event_type.startswith("item.")
                    or event_type in {"thread.started", "turn.started"}
                ):
                    unknown_event_count += 1
                item = payload.get("item")
                item_type = (
                    str(item.get("type") or "")
                    if isinstance(item, dict) else ""
                )
                state = (
                    CodexExecutionState.VALIDATING_OUTPUT
                    if event_type == "turn.completed"
                    else CodexExecutionState.ACTIVE
                )
                self._emit(
                    on_event,
                    state,
                    event_type or "unknown",
                    thread_id=thread_id,
                    item_type=item_type,
                    usage=usage,
                    unknown_event_count=unknown_event_count,
                )

            returncode = int(process.wait())
            stderr_summary = _classify_stderr(stderr_lines)
            if returncode != 0:
                failure_class, retryable, message = _classify_process_failure(
                    stderr_summary
                )
                self._emit(
                    on_event,
                    (
                        CodexExecutionState.AUTHENTICATION_REQUIRED
                        if failure_class == "authentication_required"
                        else CodexExecutionState.PROCESS_EXIT_FAILED
                    ),
                    "process.failed",
                    thread_id=thread_id,
                )
                raise CodexExecutionFailure(
                    message,
                    failure_class=failure_class,
                    retryable=retryable,
                    turn_started=turn_started,
                )
            if terminal_event_failure and not turn_completed:
                raise CodexExecutionFailure(
                    "Local Codex reported a provider or turn failure; completed stages were preserved.",
                    failure_class="provider_unavailable",
                    retryable=True,
                    turn_started=turn_started,
                )
            return CodexExecutionResult(
                returncode=returncode,
                thread_id=thread_id,
                turn_completed=turn_completed,
                usage=usage,
                stderr_summary=stderr_summary,
                unknown_event_count=unknown_event_count,
            )
        finally:
            self._process = None

    def cancel(self) -> None:
        self._cancelled.set()
        process = self._process
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)

    def _emit(
        self,
        callback: Callable[[CodexExecutionEvent], None] | None,
        state: CodexExecutionState,
        event_type: str,
        *,
        thread_id: str = "",
        item_type: str = "",
        usage: dict[str, int] | None = None,
        unknown_event_count: int = 0,
    ) -> None:
        if callback is None:
            return
        event = CodexExecutionEvent(
            state=state.value,
            event_type=event_type,
            occurred_at=self._wall_clock().isoformat(),
            thread_id=thread_id,
            item_type=item_type,
            usage=dict(usage or {}),
            unknown_event_count=unknown_event_count,
        )
        try:
            callback(event)
        except Exception:
            return


def _read_stream(
    stream: Any,
    name: str,
    destination: queue.Queue[tuple[str, str | None]],
) -> None:
    try:
        for line in iter(stream.readline, ""):
            destination.put((name, line.rstrip("\r\n")))
    finally:
        destination.put((name, None))


def _normalized_usage(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): max(0, int(item or 0))
        for key, item in value.items()
        if isinstance(item, (int, float))
    }


def _classify_stderr(lines: list[str]) -> str:
    text = " ".join(" ".join(line.split()) for line in lines if line).strip()
    lowered = text.casefold()
    if any(token in lowered for token in (
        "unexpected argument '--json'",
        "unrecognized option '--json'",
        "unknown option --json",
        "unexpected argument '--ignore-user-config'",
    )):
        return "cli_update_required"
    if any(token in lowered for token in (
        "not logged in", "login required", "authentication required",
        "please run codex login", "unauthorized",
    )):
        return "authentication_required"
    if any(token in lowered for token in (
        "rate limit", "temporarily unavailable", "service unavailable",
        "connection reset", "timed out", "timeout",
    )):
        return "provider_temporarily_unavailable"
    return "process_error" if text else ""


def _classify_process_failure(summary: str) -> tuple[str, bool, str]:
    if summary == "cli_update_required":
        return (
            "cli_update_required",
            False,
            "The installed Codex CLI does not support observable execution. Update Codex and retry this stage.",
        )
    if summary == "authentication_required":
        return (
            "authentication_required",
            False,
            "Local Codex needs authentication. Run Codex login and retry this stage.",
        )
    if summary == "provider_temporarily_unavailable":
        return (
            "provider_unavailable",
            True,
            "Local Codex was temporarily unavailable; completed stages were preserved.",
        )
    return (
        "process_exit_failed",
        False,
        "Local Codex exited before producing a valid result.",
    )


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            process.kill()
    else:
        try:
            import signal

            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
