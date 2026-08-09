from __future__ import annotations

import json
from pathlib import Path

from scripts.export_codex_to_obsidian import (
    SecretRedactor,
    audit_vault,
    parse_session,
    prepare_vault,
    write_archive_manifest,
    write_conversation_index,
    write_conversation_note,
    write_home,
    write_import_report,
    write_project_notes,
)


def _line(kind: str, payload: dict, timestamp: str) -> str:
    return json.dumps({"timestamp": timestamp, "type": kind, "payload": payload})


def test_codex_export_preserves_messages_and_excludes_raw_tool_payloads(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    transcript = sessions / "rollout-test.jsonl"
    secret = "sk-live-secret-value-123456"
    transcript.write_text(
        "\n".join([
            _line("session_meta", {
                "id": "session-test-1234",
                "timestamp": "2026-07-24T12:00:00Z",
                "cwd": str(tmp_path / "project"),
                "source": "codex-app",
            }, "2026-07-24T12:00:00Z"),
            _line("response_item", {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"Build the importer. API key={secret}"}],
            }, "2026-07-24T12:01:00Z"),
            _line("response_item", {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"authorization": secret}),
            }, "2026-07-24T12:02:00Z"),
            _line("response_item", {
                "type": "function_call_output",
                "output": f"raw tool output containing {secret}",
            }, "2026-07-24T12:03:00Z"),
            _line("response_item", {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Implemented and verified."}],
            }, "2026-07-24T12:04:00Z"),
        ]),
        encoding="utf-8",
    )
    (tmp_path / "project").mkdir()
    redactor = SecretRedactor([secret])
    exported = parse_session(transcript, redactor)
    vault = tmp_path / "vault"
    prepare_vault(vault)
    exported.note_path = write_conversation_note(vault, exported)
    exports = [exported]
    write_conversation_index(vault, exports)
    write_project_notes(vault, exports, redactor)
    write_archive_manifest(vault, sessions, exports)
    write_home(vault, sessions, exports)
    audit = audit_vault(vault, redactor)
    write_import_report(vault, exports, audit)

    note = exported.note_path.read_text(encoding="utf-8")
    assert "Build the importer" in note
    assert "Implemented and verified" in note
    assert "[REDACTED]" in note
    assert secret not in note
    assert "raw tool output" not in note
    assert "`exec_command`: 1" in note
    assert audit["suspicious_matches"] == 0
    assert (vault / "Conversations" / "Index.md").exists()
    assert (vault / "_Raw Archive" / "Manifest.md").exists()


def test_redaction_audit_does_not_flag_its_own_placeholders() -> None:
    redactor = SecretRedactor()
    source = (
        'OPENAI_API_KEY="secret-value-123456"\n'
        "https://example.com/data?api_key=url-secret-123456"
    )

    redacted, replacements = redactor.redact(source)

    assert replacements >= 2
    assert redacted.count("[REDACTED]") == 2
    assert redactor.suspicious_count(redacted) == 0
