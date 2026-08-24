from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from equity_research.application.codex_execution import (
    CodexExecutionFailure,
    CodexExecutionPolicy,
    CodexExecutionResult,
)
from equity_research.hybrid_research import CodexLocalProvider
from equity_research.infrastructure.codex_event_stream import (
    JsonlCodexExecutionRunner,
)


def _python_process(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def test_jsonl_runner_reports_lifecycle_and_usage(tmp_path: Path) -> None:
    events = []
    source = """
import json, sys
sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'thread-safe'}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
print(json.dumps({'type':'item.started','item':{'type':'reasoning'}}), flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':12,'output_tokens':4}}), flush=True)
"""

    result = JsonlCodexExecutionRunner().run(
        _python_process(source),
        prompt="bounded closure input",
        cwd=str(tmp_path),
        policy=CodexExecutionPolicy(
            startup_timeout_seconds=2,
            quiet_after_seconds=2,
            hard_timeout_seconds=5,
        ),
        on_event=events.append,
    )

    assert result.returncode == 0
    assert result.thread_id == "thread-safe"
    assert result.turn_completed is True
    assert result.usage == {"input_tokens": 12, "output_tokens": 4}
    assert [event.event_type for event in events] == [
        "process.started",
        "thread.started",
        "turn.started",
        "item.started",
        "turn.completed",
    ]
    assert all("bounded closure input" not in str(event) for event in events)


def test_quiet_period_is_visible_but_does_not_terminate(tmp_path: Path) -> None:
    events = []
    source = """
import json, sys, time
sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'thread-quiet'}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
time.sleep(1.3)
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}), flush=True)
"""

    result = JsonlCodexExecutionRunner().run(
        _python_process(source),
        prompt="input",
        cwd=str(tmp_path),
        policy=CodexExecutionPolicy(
            startup_timeout_seconds=1,
            quiet_after_seconds=1,
            hard_timeout_seconds=4,
        ),
        on_event=events.append,
    )

    assert result.turn_completed is True
    states = [event.state for event in events]
    assert "quiet_but_running" in states
    assert states[-1] == "validating_output"


def test_startup_timeout_is_distinct_from_hard_deadline(tmp_path: Path) -> None:
    source = "import sys, time; sys.stdin.read(); time.sleep(3)"

    with pytest.raises(CodexExecutionFailure) as raised:
        JsonlCodexExecutionRunner().run(
            _python_process(source),
            prompt="input",
            cwd=str(tmp_path),
            policy=CodexExecutionPolicy(
                startup_timeout_seconds=1,
                quiet_after_seconds=2,
                hard_timeout_seconds=3,
            ),
        )

    assert raised.value.failure_class == "startup_timeout"
    assert raised.value.retryable is True


def test_hard_deadline_preserves_completed_turn_boundary(tmp_path: Path) -> None:
    source = """
import json, sys, time
sys.stdin.read()
print(json.dumps({'type':'thread.started','thread_id':'thread-hard'}), flush=True)
print(json.dumps({'type':'turn.started'}), flush=True)
time.sleep(3)
"""

    with pytest.raises(CodexExecutionFailure) as raised:
        JsonlCodexExecutionRunner().run(
            _python_process(source),
            prompt="input",
            cwd=str(tmp_path),
            policy=CodexExecutionPolicy(
                startup_timeout_seconds=1,
                quiet_after_seconds=1,
                hard_timeout_seconds=2,
            ),
        )

    assert raised.value.failure_class == "hard_deadline_exceeded"
    assert raised.value.turn_started is True
    assert raised.value.retryable is False


class _ProviderStreamRunner:
    def __init__(self) -> None:
        self.command = []

    def run(self, command, *, prompt, cwd, policy, on_event=None, **kwargs):
        self.command = list(command)
        output_path = Path(command[command.index("-o") + 1])
        output_path.write_text(
            json.dumps({"summary": "Structured closure"}),
            encoding="utf-8",
        )
        return CodexExecutionResult(
            returncode=0,
            thread_id="thread-provider",
            turn_completed=True,
            usage={"input_tokens": 5, "output_tokens": 2},
        )

    def cancel(self):
        return None


def test_local_provider_uses_jsonl_without_exposing_prompt(tmp_path: Path) -> None:
    stream_runner = _ProviderStreamRunner()
    events = []
    provider = CodexLocalProvider(
        enabled=True,
        executable=sys.executable,
        event_stream_runner=stream_runner,
        event_callback=events.append,
    )

    result = provider.complete_json({"summary": "secret-free fixture"})

    assert result == {"summary": "Structured closure"}
    assert "--json" in stream_runner.command
    assert "--ignore-user-config" in stream_runner.command
    assert "--ignore-rules" in stream_runner.command
    assert provider.last_usage == {"input_tokens": 5, "output_tokens": 2}
    assert events[-1].state == "completed"
