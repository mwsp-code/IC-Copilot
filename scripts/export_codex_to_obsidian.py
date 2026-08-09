from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable, Optional
import unicodedata


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SECRET_PATTERNS = (
    re.compile(r"(?i)\b(sk-(?:proj-)?[A-Za-z0-9_-]{10,})\b"),
    re.compile(
        r"""(?ix)
        (
            \b(?:api[\s_-]*key|access[\s_-]*token|auth(?:orization)?|password|secret|token)\b
            \s*["']?\s*[:=]\s*["']?\s*
            (?:bearer\s+)?
        )
        ([A-Za-z0-9._/+:-]{10,})
        """
    ),
    re.compile(
        r"""(?ix)
        (
            \b[A-Z][A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET|PASSWORD)
            \s*=\s*["']?
        )
        ([^"'\s;]{8,})
        """
    ),
    re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/-]{10,})"),
    re.compile(
        r"(?i)([?&](?:api[_-]?key|apikey|access[_-]?token|token)=)([^&#\s]{8,})"
    ),
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)

WINDOWS_ATTACHMENT_PATTERN = re.compile(
    r"""(?ix)
    (
        [A-Z]:\\
        [^<>"|?*\r\n`]+?
        \.(?:png|jpe?g|gif|webp|bmp|pdf|docx|xlsx|csv|txt)
    )
    """
)

TOOL_PAYLOAD_TYPES = {
    "function_call",
    "custom_tool_call",
    "web_search_call",
    "tool_search_call",
    "mcp_tool_call",
}


@dataclass
class Message:
    role: str
    text: str
    timestamp: str = ""


@dataclass
class SessionExport:
    source_path: Path
    session_id: str
    created_at: str
    updated_at: str
    cwd: str
    source: str
    title: str
    messages: list[Message] = field(default_factory=list)
    tools: Counter[str] = field(default_factory=Counter)
    attachments: list[Path] = field(default_factory=list)
    redaction_count: int = 0
    source_sha256: str = ""
    source_bytes: int = 0
    note_path: Optional[Path] = None


class SecretRedactor:
    def __init__(self, exact_secrets: Iterable[str] = ()) -> None:
        self.exact_secrets = sorted(
            {secret.strip() for secret in exact_secrets if secret and len(secret.strip()) >= 8},
            key=len,
            reverse=True,
        )

    def redact(self, text: str) -> tuple[str, int]:
        value = text
        count = 0
        for secret in self.exact_secrets:
            occurrences = value.count(secret)
            if occurrences:
                value = value.replace(secret, "[REDACTED]")
                count += occurrences
        for index, pattern in enumerate(SECRET_PATTERNS):
            if index in {0, 5}:
                value, replacements = pattern.subn("[REDACTED]", value)
            else:
                value, replacements = pattern.subn(r"\1[REDACTED]", value)
            count += replacements
        return value, count

    def suspicious_count(self, text: str) -> int:
        total = 0
        for secret in self.exact_secrets:
            total += text.count(secret)
        for pattern in SECRET_PATTERNS:
            total += sum(
                1
                for match in pattern.finditer(text)
                if "[REDACTED]" not in match.group(0)
            )
        return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert local Codex JSONL sessions into a redacted Obsidian Markdown vault.",
    )
    parser.add_argument(
        "--sessions",
        type=Path,
        default=Path.home() / ".codex" / "sessions",
    )
    parser.add_argument("--vault", type=Path, required=True)
    parser.add_argument("--copy-attachments", action="store_true")
    parser.add_argument("--max-attachment-mb", type=float, default=25.0)
    args = parser.parse_args()

    session_root = args.sessions.expanduser().resolve()
    vault = args.vault.expanduser().resolve()
    if not session_root.is_dir():
        raise SystemExit(f"Codex sessions directory not found: {session_root}")

    redactor = SecretRedactor(load_local_secrets())
    session_files = sorted(session_root.rglob("*.jsonl"))
    if not session_files:
        raise SystemExit(f"No JSONL session transcripts found under {session_root}")

    prepare_vault(vault)
    exports: list[SessionExport] = []
    for index, session_file in enumerate(session_files, start=1):
        exported = parse_session(session_file, redactor)
        if args.copy_attachments:
            exported.attachments = copy_attachments(
                exported,
                vault / "Attachments",
                max_bytes=int(args.max_attachment_mb * 1024 * 1024),
            )
        exported.note_path = write_conversation_note(vault, exported)
        exports.append(exported)
        print(f"Converted session {index}/{len(session_files)}: {exported.session_id[:12]}")

    write_conversation_index(vault, exports)
    write_project_notes(vault, exports, redactor)
    write_archive_manifest(vault, session_root, exports)
    write_home(vault, session_root, exports)
    write_templates(vault)
    audit = audit_vault(vault, redactor)
    write_import_report(vault, exports, audit)
    if audit["suspicious_matches"]:
        raise SystemExit(
            f"Security audit failed: {audit['suspicious_matches']} suspicious credential pattern(s) remain."
        )

    print(json.dumps({
        "vault": str(vault),
        "sessions_converted": len(exports),
        "messages_exported": sum(len(item.messages) for item in exports),
        "redactions": sum(item.redaction_count for item in exports),
        "attachments_copied": sum(len(item.attachments) for item in exports),
        "projects": len({item.cwd for item in exports if item.cwd}),
        "suspicious_matches": audit["suspicious_matches"],
        "markdown_files": audit["markdown_files"],
        "markdown_bytes": audit["markdown_bytes"],
    }, indent=2))


def load_local_secrets() -> tuple[str, ...]:
    values = {
        value
        for key, value in os.environ.items()
        if _secret_env_name(key) and value and len(value.strip()) >= 8
    }
    try:
        from equity_research.local_secrets import LocalSecretsManager, MANAGED_SECRET_KEYS

        manager = LocalSecretsManager()
        for key in MANAGED_SECRET_KEYS:
            value = manager.get(key)
            if value:
                values.add(value)
        try:
            from equity_research.research_store import ResearchStore

            store = ResearchStore()
            for profile in store.list_llm_profiles():
                value = manager.get(profile.secret_ref)
                if value:
                    values.add(value)
        except Exception:
            pass
    except Exception:
        pass
    return tuple(values)


def _secret_env_name(name: str) -> bool:
    upper = name.upper()
    return (
        upper.endswith("API_KEY")
        or upper.endswith("ACCESS_TOKEN")
        or upper.endswith("AUTH_TOKEN")
        or upper.endswith("_SECRET")
        or upper.endswith("_PASSWORD")
    )


def prepare_vault(vault: Path) -> None:
    for relative in (
        ".obsidian",
        "Attachments",
        "Conversations",
        "Projects",
        "Decisions",
        "Lessons Learned",
        "_Raw Archive",
        "_Import",
    ):
        (vault / relative).mkdir(parents=True, exist_ok=True)
    app_config = vault / ".obsidian" / "app.json"
    if not app_config.exists():
        app_config.write_text(
            json.dumps({
                "attachmentFolderPath": "Attachments",
                "newFileLocation": "folder",
                "newFileFolderPath": "Inbox",
                "showLineNumber": True,
                "strictLineBreaks": False,
            }, indent=2),
            encoding="utf-8",
        )


def parse_session(path: Path, redactor: SecretRedactor) -> SessionExport:
    metadata: dict[str, Any] = {}
    messages: list[Message] = []
    fallback_messages: list[Message] = []
    tools: Counter[str] = Counter()
    earliest = ""
    latest = ""

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            timestamp = _extract_json_timestamp(raw_line)
            earliest = min(filter(None, (earliest, timestamp)), default=timestamp)
            latest = max(latest, timestamp)
            if any(
                marker in raw_line
                for marker in (
                    '"function_call_output"',
                    '"custom_tool_call_output"',
                    '"tool_search_output"',
                    '"web_search_output"',
                    '"reasoning"',
                    '"encrypted_content"',
                )
            ):
                continue
            try:
                row = json.loads(raw_line)
            except (json.JSONDecodeError, MemoryError):
                continue
            row_type = str(row.get("type") or "")
            payload = row.get("payload") or {}
            if row_type == "session_meta":
                for key in (
                    "id",
                    "session_id",
                    "timestamp",
                    "cwd",
                    "source",
                    "originator",
                    "parent_thread_id",
                ):
                    if payload.get(key) not in (None, ""):
                        metadata.setdefault(key, payload[key])
                continue
            if row_type == "response_item":
                payload_type = str(payload.get("type") or "")
                if payload_type == "message":
                    role = str(payload.get("role") or "").lower()
                    if role not in {"user", "assistant"}:
                        continue
                    text = _message_text(payload.get("content"))
                    if text.strip():
                        redacted, replacements = redactor.redact(text)
                        messages.append(Message(role, redacted.strip(), timestamp))
                        metadata["redactions"] = int(metadata.get("redactions", 0)) + replacements
                elif payload_type in TOOL_PAYLOAD_TYPES:
                    tool_name = str(payload.get("name") or payload_type).strip()
                    tools[tool_name] += 1
                continue
            if row_type == "event_msg":
                event_type = str(payload.get("type") or "")
                if event_type in {"user_message", "agent_message"}:
                    text = str(payload.get("message") or "")
                    if text.strip():
                        role = "user" if event_type == "user_message" else "assistant"
                        redacted, replacements = redactor.redact(text)
                        fallback_messages.append(Message(role, redacted.strip(), timestamp))
                        metadata["redactions"] = int(metadata.get("redactions", 0)) + replacements

    selected_messages = _dedupe_messages(messages or fallback_messages)
    session_id = str(
        metadata.get("id")
        or metadata.get("session_id")
        or path.stem.removeprefix("rollout-")
    )
    created = str(metadata.get("timestamp") or earliest or _iso_from_mtime(path))
    updated = latest or _iso_from_mtime(path)
    cwd = str(metadata.get("cwd") or "")
    title = derive_title(selected_messages, session_id)
    return SessionExport(
        source_path=path,
        session_id=session_id,
        created_at=created,
        updated_at=updated,
        cwd=cwd,
        source=str(metadata.get("source") or metadata.get("originator") or "Codex"),
        title=title,
        messages=selected_messages,
        tools=tools,
        redaction_count=int(metadata.get("redactions", 0)),
        source_sha256=_sha256(path),
        source_bytes=path.stat().st_size,
    )


def _extract_json_timestamp(raw_line: str) -> str:
    match = re.search(r'"timestamp"\s*:\s*"([^"]+)"', raw_line[:500])
    return match.group(1) if match else ""


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if not isinstance(content, list):
        return ""
    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") in {
            "input_text",
            "output_text",
            "text",
        }:
            parts.append(str(item.get("text") or ""))
    return "\n\n".join(part for part in parts if part)


def _dedupe_messages(messages: list[Message]) -> list[Message]:
    rows = []
    seen_adjacent: Optional[tuple[str, str]] = None
    for item in messages:
        signature = (item.role, item.text)
        if signature == seen_adjacent:
            continue
        rows.append(item)
        seen_adjacent = signature
    return rows


def derive_title(messages: list[Message], session_id: str) -> str:
    first_user = next((item.text for item in messages if item.role == "user"), "")
    candidates = []
    for raw_line in first_user.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("<", "# Files mentioned", "# Response annotations")):
            continue
        if line.lower() in {"## my request for codex:", "my request for codex:"}:
            continue
        line = re.sub(r"^[#>*`\-\s]+", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if len(line) >= 5:
            candidates.append(line)
    title = candidates[0] if candidates else f"Codex session {session_id[:8]}"
    return title[:100].rstrip(" .,:;-")


def write_conversation_note(vault: Path, item: SessionExport) -> Path:
    year = _year(item.created_at)
    folder = vault / "Conversations" / year
    folder.mkdir(parents=True, exist_ok=True)
    date_label = _date(item.created_at)
    filename = f"{date_label} - {item.session_id[:8]} - {_slug(item.title, 72)}.md"
    path = folder / filename
    project_name = Path(item.cwd).name if item.cwd else "Unknown workspace"
    project_slug = _slug(project_name, 70)
    final_response = next(
        (message.text for message in reversed(item.messages) if message.role == "assistant"),
        "No final Codex response was recorded in the local transcript.",
    )
    lines = [
        "---",
        f"title: {json.dumps(item.title, ensure_ascii=False)}",
        f"created: {json.dumps(item.created_at)}",
        f"updated: {json.dumps(item.updated_at)}",
        f"codex_session_id: {json.dumps(item.session_id)}",
        f"workspace: {json.dumps(item.cwd, ensure_ascii=False)}",
        f"source: {json.dumps(item.source)}",
        "tags:",
        "  - codex",
        "  - conversation",
        f"  - {_tag(project_name)}",
        "---",
        "",
        f"# {item.title}",
        "",
        f"> [!info] Imported Codex task",
        f"> Session `{item.session_id}` · {len(item.messages)} retained messages · "
        f"{item.redaction_count} credential redaction(s).",
        f"> Project: [[Projects/{project_slug}|{project_name}]]",
        "",
        "## Final Codex Response",
        "",
        final_response,
        "",
        "## Tool Activity",
        "",
    ]
    if item.tools:
        lines.extend(
            f"- `{name}`: {count}"
            for name, count in item.tools.most_common()
        )
    else:
        lines.append("- No callable tool activity was retained.")
    if item.attachments:
        lines.extend(["", "## Imported Attachments", ""])
        for attachment in item.attachments:
            relative = attachment.relative_to(vault).as_posix()
            if attachment.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
                lines.append(f"![[{relative}]]")
            else:
                lines.append(f"- [[{relative}|{attachment.name}]]")
    lines.extend([
        "",
        "<details>",
        "<summary>Full redacted conversation</summary>",
        "",
    ])
    for message in item.messages:
        role = "User" if message.role == "user" else "Codex"
        timestamp = f" · {message.timestamp}" if message.timestamp else ""
        lines.extend([
            f"### {role}{timestamp}",
            "",
            message.text,
            "",
        ])
    lines.extend(["</details>", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def copy_attachments(
    item: SessionExport,
    target_dir: Path,
    *,
    max_bytes: int,
) -> list[Path]:
    candidates: list[Path] = []
    for message in item.messages:
        if message.role != "user":
            continue
        for match in WINDOWS_ATTACHMENT_PATTERN.finditer(message.text):
            raw = match.group(1).strip().rstrip(".,);]")
            source = Path(raw)
            try:
                if (
                    source.is_file()
                    and source.stat().st_size <= max_bytes
                    and ".codex\\sessions" not in str(source).lower()
                ):
                    candidates.append(source)
            except OSError:
                continue
    copied = []
    for source in dict.fromkeys(candidates):
        filename = f"{item.session_id[:8]}-{_safe_filename(source.name)}"
        target = _unique_target(target_dir / filename, source)
        if not target.exists():
            shutil.copy2(source, target)
        copied.append(target)
    return copied


def _unique_target(target: Path, source: Path) -> Path:
    if not target.exists():
        return target
    if target.stat().st_size == source.stat().st_size and _sha256(target) == _sha256(source):
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(2, 1000):
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not allocate an attachment filename for {source}")


def write_conversation_index(vault: Path, exports: list[SessionExport]) -> None:
    by_year: dict[str, list[SessionExport]] = defaultdict(list)
    for item in exports:
        by_year[_year(item.created_at)].append(item)
    lines = [
        "---",
        'title: "Codex Conversation Index"',
        "tags:",
        "  - codex",
        "  - index",
        "---",
        "",
        "# Codex Conversation Index",
        "",
        f"{len(exports)} local Codex sessions converted to redacted Markdown.",
        "",
    ]
    for year in sorted(by_year, reverse=True):
        lines.extend([f"## {year}", ""])
        for item in sorted(by_year[year], key=lambda row: row.created_at, reverse=True):
            link = _vault_link(vault, item.note_path)
            project = Path(item.cwd).name if item.cwd else "Unknown workspace"
            lines.append(
                f"- {_date(item.created_at)} · [[{link}|{item.title}]] · `{project}` "
                f"· {len(item.messages)} messages"
            )
        lines.append("")
    (vault / "Conversations" / "Index.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def write_project_notes(
    vault: Path,
    exports: list[SessionExport],
    redactor: SecretRedactor,
) -> None:
    grouped: dict[str, list[SessionExport]] = defaultdict(list)
    for item in exports:
        grouped[item.cwd or "Unknown workspace"].append(item)
    for cwd, sessions in grouped.items():
        project_name = Path(cwd).name if cwd != "Unknown workspace" else cwd
        project_slug = _slug(project_name, 70)
        git = git_metadata(Path(cwd), redactor) if cwd != "Unknown workspace" else {}
        lines = [
            "---",
            f"title: {json.dumps(project_name, ensure_ascii=False)}",
            f"workspace: {json.dumps(cwd, ensure_ascii=False)}",
            "tags:",
            "  - codex",
            "  - project",
            "---",
            "",
            f"# {project_name}",
            "",
            f"- **Workspace:** `{cwd}`",
            f"- **Codex sessions:** {len(sessions)}",
        ]
        if git.get("remote"):
            lines.append(f"- **Repository:** {git['remote']}")
        if git.get("branch"):
            lines.append(f"- **Branch:** `{git['branch']}`")
        if git.get("commits"):
            lines.extend(["", "## Recent Git Work", ""])
            lines.extend(f"- `{row}`" for row in git["commits"])
        lines.extend(["", "## Related Codex Tasks", ""])
        for item in sorted(sessions, key=lambda row: row.created_at, reverse=True):
            lines.append(
                f"- [[{_vault_link(vault, item.note_path)}|{item.title}]] "
                f"· {_date(item.created_at)}"
            )
        lines.extend([
            "",
            "## Working Notes",
            "",
            "- Decisions:",
            "- Open questions:",
            "- Next actions:",
            "",
        ])
        (vault / "Projects" / f"{project_slug}.md").write_text(
            "\n".join(lines),
            encoding="utf-8",
        )


def git_metadata(path: Path, redactor: SecretRedactor) -> dict[str, Any]:
    if not path.is_dir() or not (path / ".git").exists():
        return {}

    def run(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-c", f"safe.directory={path}", "-C", str(path), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        text, _ = redactor.redact(result.stdout.strip())
        return text if result.returncode == 0 else ""

    remote = run("remote", "get-url", "origin")
    branch = run("branch", "--show-current")
    commits = run(
        "log",
        "-20",
        "--date=short",
        "--pretty=format:%h | %ad | %s",
    ).splitlines()
    return {"remote": remote, "branch": branch, "commits": commits}


def write_archive_manifest(
    vault: Path,
    session_root: Path,
    exports: list[SessionExport],
) -> None:
    lines = [
        "---",
        'title: "Codex Raw Archive Manifest"',
        "tags:",
        "  - codex",
        "  - archive",
        "---",
        "",
        "# Codex Raw Archive Manifest",
        "",
        "> [!warning]",
        "> Raw JSONL transcripts were not copied into this vault because they can contain credentials, "
        "tool payloads, and unstable internal structures. The untouched originals remain under "
        f"> `{session_root}`.",
        "",
        "| Session | Bytes | SHA-256 | Source file |",
        "|---|---:|---|---|",
    ]
    for item in exports:
        lines.append(
            f"| `{item.session_id}` | {item.source_bytes:,} | `{item.source_sha256}` | "
            f"`{item.source_path.name}` |"
        )
    (vault / "_Raw Archive" / "Manifest.md").write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


def write_home(vault: Path, session_root: Path, exports: list[SessionExport]) -> None:
    project_count = len({item.cwd for item in exports if item.cwd})
    lines = [
        "---",
        'title: "Codex Knowledge Base"',
        "tags:",
        "  - codex",
        "  - home",
        "---",
        "",
        "# Codex Knowledge Base",
        "",
        "> [!summary]",
        f"> {len(exports)} Codex sessions, {sum(len(item.messages) for item in exports)} retained messages, "
        f"{project_count} workspace(s), and {sum(item.redaction_count for item in exports)} credential redaction(s).",
        "",
        "## Start Here",
        "",
        "- [[Conversations/Index|Conversation Index]]",
        "- Browse `Projects/` for workspace and Git context.",
        "- Use `Decisions/` and `Lessons Learned/` to turn transcripts into durable knowledge.",
        "- [[_Import/Import Report|Import and Security Report]]",
        "- [[_Raw Archive/Manifest|Raw Archive Manifest]]",
        "",
        "## Security Boundary",
        "",
        "This vault contains redacted Markdown, not raw Codex JSONL. Tool outputs and reasoning traces "
        "were excluded. Obsidian vaults are plaintext on the local disk, so do not place API keys or "
        "other credentials in these notes.",
        "",
        f"Original local session archive: `{session_root}`.",
        "",
    ]
    (vault / "Home.md").write_text("\n".join(lines), encoding="utf-8")


def write_templates(vault: Path) -> None:
    decision = vault / "Decisions" / "README.md"
    if not decision.exists():
        decision.write_text(
            "# Decisions\n\n"
            "Create one note per durable decision.\n\n"
            "## Suggested fields\n\n"
            "- Decision\n- Context\n- Evidence\n- Alternatives\n- Consequences\n- Related Codex tasks\n",
            encoding="utf-8",
        )
    lessons = vault / "Lessons Learned" / "README.md"
    if not lessons.exists():
        lessons.write_text(
            "# Lessons Learned\n\n"
            "Distill reusable methods, failures, fixes, and research principles from imported tasks.\n",
            encoding="utf-8",
        )


def audit_vault(vault: Path, redactor: SecretRedactor) -> dict[str, int]:
    markdown_files = list(vault.rglob("*.md"))
    suspicious = 0
    total_bytes = 0
    for path in markdown_files:
        text = path.read_text(encoding="utf-8", errors="replace")
        suspicious += redactor.suspicious_count(text)
        total_bytes += path.stat().st_size
    return {
        "markdown_files": len(markdown_files),
        "markdown_bytes": total_bytes,
        "suspicious_matches": suspicious,
    }


def write_import_report(
    vault: Path,
    exports: list[SessionExport],
    audit: dict[str, int],
) -> None:
    report = [
        "---",
        'title: "Codex Import and Security Report"',
        "tags:",
        "  - codex",
        "  - security",
        "---",
        "",
        "# Codex Import and Security Report",
        "",
        f"- **Imported sessions:** {len(exports)}",
        f"- **Retained user/Codex messages:** {sum(len(item.messages) for item in exports)}",
        f"- **Credential redactions:** {sum(item.redaction_count for item in exports)}",
        f"- **Attachments copied:** {sum(len(item.attachments) for item in exports)}",
        f"- **Markdown files audited:** {audit['markdown_files']}",
        f"- **Markdown bytes:** {audit['markdown_bytes']:,}",
        f"- **Suspicious credential patterns remaining:** {audit['suspicious_matches']}",
        "",
        "## Excluded by Design",
        "",
        "- Raw function/tool outputs",
        "- Internal reasoning records",
        "- Encrypted internal content",
        "- Developer and system instructions",
        "- Raw JSONL transcript copies",
        "",
        "The original JSONL files remain the archival source. This vault is the readable, redacted knowledge layer.",
        "",
    ]
    (vault / "_Import" / "Import Report.md").write_text(
        "\n".join(report),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iso_from_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat()


def _year(value: str) -> str:
    match = re.match(r"(\d{4})", value or "")
    return match.group(1) if match else "Unknown"


def _date(value: str) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value or "")
    return match.group(1) if match else "Unknown-date"


def _slug(value: str, maximum: int = 80) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_value).strip("-").lower()
    return (slug or "codex-session")[:maximum].rstrip("-")


def _tag(value: str) -> str:
    return f"project/{_slug(value, 50)}"


def _safe_filename(value: str) -> str:
    stem = re.sub(r'[<>:"/\\|?*]+', "-", value).strip(" .")
    return stem[:150] or "attachment"


def _vault_link(vault: Path, path: Optional[Path]) -> str:
    if path is None:
        return "Conversations/Index"
    return path.relative_to(vault).with_suffix("").as_posix()


if __name__ == "__main__":
    main()
