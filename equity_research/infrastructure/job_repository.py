from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from ..application.research_runs import (
    ResearchRunCommand,
    ResearchRunEvent,
    ResearchRunSnapshot,
)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class SqliteResearchJobRepository:
    """Durable, local-first run metadata and append-only event storage."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=15,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_job_snapshots (
                    run_id TEXT PRIMARY KEY,
                    command_json TEXT NOT NULL,
                    operation_fingerprint TEXT NOT NULL DEFAULT '',
                    launch_attempt_id TEXT NOT NULL DEFAULT '',
                    workspace_scope TEXT NOT NULL DEFAULT 'local',
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
                );
                CREATE TABLE IF NOT EXISTS research_job_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    evidence_hash TEXT NOT NULL DEFAULT '',
                    estimated_cost_usd REAL,
                    PRIMARY KEY (run_id, sequence),
                    UNIQUE (run_id, idempotency_key),
                    FOREIGN KEY (run_id) REFERENCES research_job_snapshots(run_id)
                );
                """
            )
            existing = {
                row["name"]
                for row in db.execute("PRAGMA table_info(research_job_snapshots)")
            }
            for column in (
                "lifecycle_state TEXT NOT NULL DEFAULT 'research_running'",
                "research_attempt_finished_at TEXT NOT NULL DEFAULT ''",
                "validation_completed_at TEXT NOT NULL DEFAULT ''",
                "audit_completed_at TEXT NOT NULL DEFAULT ''",
                "revision_completed_at TEXT NOT NULL DEFAULT ''",
                "closure_completed_at TEXT NOT NULL DEFAULT ''",
                "publishable_at TEXT NOT NULL DEFAULT ''",
                "published_at TEXT NOT NULL DEFAULT ''",
                "terminal_at TEXT NOT NULL DEFAULT ''",
                "completed_at TEXT NOT NULL DEFAULT ''",
                "operation_fingerprint TEXT NOT NULL DEFAULT ''",
                "launch_attempt_id TEXT NOT NULL DEFAULT ''",
                "workspace_scope TEXT NOT NULL DEFAULT 'local'",
            ):
                name = column.split()[0]
                if name not in existing:
                    db.execute(
                        f"ALTER TABLE research_job_snapshots ADD COLUMN {column}"
                    )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_research_operation_latest
                ON research_job_snapshots(
                    workspace_scope, operation_fingerprint, created_at DESC
                )
                """
            )
            db.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_launch_attempt
                ON research_job_snapshots(
                    workspace_scope, operation_fingerprint, launch_attempt_id
                )
                WHERE operation_fingerprint <> '' AND launch_attempt_id <> ''
                """
            )

    def create(self, snapshot: ResearchRunSnapshot) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO research_job_snapshots (
                    run_id, command_json, operation_fingerprint,
                    launch_attempt_id, workspace_scope, status, current_stage, created_at,
                    updated_at, event_count, evidence_hash, result_reference,
                    stopping_reason, errors_json, lifecycle_state,
                    research_attempt_finished_at, validation_completed_at,
                    audit_completed_at, revision_completed_at,
                    closure_completed_at, publishable_at, published_at,
                    terminal_at, completed_at, snapshot_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._snapshot_values(snapshot),
            )

    def create_if_absent(
        self,
        snapshot: ResearchRunSnapshot,
    ) -> tuple[ResearchRunSnapshot, bool]:
        try:
            self.create(snapshot)
            return snapshot, True
        except sqlite3.IntegrityError:
            existing = self.get(snapshot.run_id)
            if existing is None and snapshot.command.operation_fingerprint:
                existing = self._find_attempt(
                    snapshot.command.operation_fingerprint,
                    snapshot.command.launch_attempt_id,
                    workspace_scope=snapshot.command.workspace_scope,
                )
            if existing is None:
                raise
            return existing, False

    def get(self, run_id: str) -> ResearchRunSnapshot | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM research_job_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return self._snapshot_from_row(row) if row else None

    def save(self, snapshot: ResearchRunSnapshot) -> None:
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE research_job_snapshots SET
                    command_json = ?, operation_fingerprint = ?,
                    launch_attempt_id = ?, workspace_scope = ?,
                    status = ?, current_stage = ?,
                    created_at = ?, updated_at = ?, event_count = ?,
                    evidence_hash = ?, result_reference = ?, stopping_reason = ?,
                    errors_json = ?, lifecycle_state = ?,
                    research_attempt_finished_at = ?, validation_completed_at = ?,
                    audit_completed_at = ?, revision_completed_at = ?,
                    closure_completed_at = ?, publishable_at = ?, published_at = ?,
                    terminal_at = ?, completed_at = ?, snapshot_version = ?
                WHERE run_id = ?
                """,
                (*self._snapshot_values(snapshot)[1:], snapshot.run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"Unknown research run: {snapshot.run_id}")

    def append_event(self, event: ResearchRunEvent) -> ResearchRunEvent:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                """
                SELECT * FROM research_job_events
                WHERE run_id = ? AND idempotency_key = ?
                """,
                (event.run_id, event.idempotency_key),
            ).fetchone()
            if existing:
                return self._event_from_row(existing)
            row = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM research_job_events WHERE run_id = ?",
                (event.run_id,),
            ).fetchone()
            sequence = int(row["sequence"]) + 1
            stored = ResearchRunEvent(
                **{**asdict(event), "sequence": sequence}
            )
            db.execute(
                """
                INSERT INTO research_job_events (
                    run_id, sequence, event_id, stage, status, summary,
                    created_at, idempotency_key, details_json, provider, model,
                    evidence_hash, estimated_cost_usd
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.run_id,
                    stored.sequence,
                    stored.event_id,
                    stored.stage,
                    stored.status,
                    stored.summary,
                    stored.created_at,
                    stored.idempotency_key,
                    json.dumps(stored.details, sort_keys=True, default=str),
                    stored.provider,
                    stored.model,
                    stored.evidence_hash,
                    stored.estimated_cost_usd,
                ),
            )
            db.execute(
                """
                UPDATE research_job_snapshots
                SET event_count = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (sequence, stored.created_at, stored.run_id),
            )
            return stored

    def events(self, run_id: str) -> list[ResearchRunEvent]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM research_job_events WHERE run_id = ? ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def find_latest_by_operation_fingerprint(
        self,
        operation_fingerprint: str,
        *,
        workspace_scope: str,
    ) -> ResearchRunSnapshot | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM research_job_snapshots
                WHERE workspace_scope = ? AND operation_fingerprint = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (workspace_scope, operation_fingerprint),
            ).fetchone()
        return self._snapshot_from_row(row) if row else None

    def _find_attempt(
        self,
        operation_fingerprint: str,
        launch_attempt_id: str,
        *,
        workspace_scope: str,
    ) -> ResearchRunSnapshot | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM research_job_snapshots
                WHERE workspace_scope = ? AND operation_fingerprint = ?
                  AND launch_attempt_id = ?
                LIMIT 1
                """,
                (
                    workspace_scope,
                    operation_fingerprint,
                    launch_attempt_id,
                ),
            ).fetchone()
        return self._snapshot_from_row(row) if row else None

    @staticmethod
    def _snapshot_values(snapshot: ResearchRunSnapshot) -> tuple:
        command = asdict(snapshot.command)
        return (
            snapshot.run_id,
            json.dumps(command, sort_keys=True, default=str),
            snapshot.command.operation_fingerprint,
            snapshot.command.launch_attempt_id,
            snapshot.command.workspace_scope,
            snapshot.status,
            snapshot.current_stage,
            snapshot.created_at,
            snapshot.updated_at,
            snapshot.event_count,
            snapshot.evidence_hash,
            snapshot.result_reference,
            snapshot.stopping_reason,
            json.dumps(list(snapshot.errors)),
            snapshot.lifecycle_state,
            snapshot.research_attempt_finished_at,
            snapshot.validation_completed_at,
            snapshot.audit_completed_at,
            snapshot.revision_completed_at,
            snapshot.closure_completed_at,
            snapshot.publishable_at,
            snapshot.published_at,
            snapshot.terminal_at,
            snapshot.completed_at,
            snapshot.snapshot_version,
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> ResearchRunSnapshot:
        payload = json.loads(row["command_json"])
        payload["enabled_source_families"] = tuple(payload.get("enabled_source_families", ()))
        payload["investigation_source_families"] = tuple(
            payload.get("investigation_source_families", ())
        )
        payload["investigation_preferred_connectors"] = tuple(
            payload.get("investigation_preferred_connectors", ())
        )
        payload["credential_lease_ids"] = tuple(
            payload.get("credential_lease_ids", ())
        )
        payload["repair_action_ids"] = tuple(
            payload.get("repair_action_ids", ())
        )
        payload["repair_retry_stages"] = tuple(
            payload.get("repair_retry_stages", ())
        )
        return ResearchRunSnapshot(
            run_id=row["run_id"],
            command=ResearchRunCommand(**payload),
            status=row["status"],
            current_stage=row["current_stage"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            event_count=int(row["event_count"]),
            evidence_hash=row["evidence_hash"],
            result_reference=row["result_reference"],
            stopping_reason=row["stopping_reason"],
            errors=tuple(json.loads(row["errors_json"] or "[]")),
            lifecycle_state=row["lifecycle_state"],
            research_attempt_finished_at=row["research_attempt_finished_at"],
            validation_completed_at=row["validation_completed_at"],
            audit_completed_at=row["audit_completed_at"],
            revision_completed_at=row["revision_completed_at"],
            closure_completed_at=row["closure_completed_at"],
            publishable_at=row["publishable_at"],
            published_at=row["published_at"],
            terminal_at=row["terminal_at"],
            completed_at=row["completed_at"],
            snapshot_version=row["snapshot_version"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> ResearchRunEvent:
        return ResearchRunEvent(
            event_id=row["event_id"],
            run_id=row["run_id"],
            sequence=int(row["sequence"]),
            stage=row["stage"],
            status=row["status"],
            summary=row["summary"],
            created_at=row["created_at"],
            idempotency_key=row["idempotency_key"],
            details=json.loads(row["details_json"] or "{}"),
            provider=row["provider"],
            model=row["model"],
            evidence_hash=row["evidence_hash"],
            estimated_cost_usd=row["estimated_cost_usd"],
        )
