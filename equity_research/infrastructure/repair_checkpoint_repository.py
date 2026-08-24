from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from ..application.repair_stage_execution import RepairStageCheckpoint


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class SqliteRepairStageCheckpointRepository:
    """Small durable ledger: one current row for each repair attempt/stage."""

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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_stage_checkpoints (
                    attempt_id TEXT NOT NULL,
                    parent_run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deadline_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    prompt_hash TEXT NOT NULL DEFAULT '',
                    result_hash TEXT NOT NULL DEFAULT '',
                    result_payload_json TEXT NOT NULL DEFAULT '{}',
                    failure_class TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    retryable INTEGER NOT NULL DEFAULT 0,
                    execution_id TEXT NOT NULL DEFAULT '',
                    execution_state TEXT NOT NULL DEFAULT '',
                    last_activity_at TEXT NOT NULL DEFAULT '',
                    hard_deadline_at TEXT NOT NULL DEFAULT '',
                    transport_version TEXT NOT NULL DEFAULT '',
                    execution_telemetry_json TEXT NOT NULL DEFAULT '{}',
                    policy_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    PRIMARY KEY (attempt_id, stage)
                )
                """
            )
            existing_columns = {
                str(row[1])
                for row in db.execute(
                    "PRAGMA table_info(repair_stage_checkpoints)"
                ).fetchall()
            }
            for name, declaration in (
                ("execution_id", "TEXT NOT NULL DEFAULT ''"),
                ("execution_state", "TEXT NOT NULL DEFAULT ''"),
                ("last_activity_at", "TEXT NOT NULL DEFAULT ''"),
                ("hard_deadline_at", "TEXT NOT NULL DEFAULT ''"),
                ("transport_version", "TEXT NOT NULL DEFAULT ''"),
                (
                    "execution_telemetry_json",
                    "TEXT NOT NULL DEFAULT '{}'",
                ),
            ):
                if name not in existing_columns:
                    db.execute(
                        f"ALTER TABLE repair_stage_checkpoints "
                        f"ADD COLUMN {name} {declaration}"
                    )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_repair_checkpoint_parent
                ON repair_stage_checkpoints(parent_run_id, updated_at DESC)
                """
            )
            # Non-destructive compatibility migration for checkpoints written
            # before provider funding/authentication became first-class repair
            # states. Replace raw provider responses with controlled copy and
            # keep the attempt resumable after explicit user action.
            db.execute(
                """
                UPDATE repair_stage_checkpoints
                SET state = 'waiting_for_provider',
                    failure_class = 'provider_credit_required',
                    summary = ?,
                    retryable = 0
                WHERE state = 'failed_terminal'
                  AND (
                    lower(summary) LIKE '%http 402%'
                    OR lower(summary) LIKE '%insufficient balance%'
                    OR lower(summary) LIKE '%insufficient_quota%'
                  )
                """,
                (
                    "The selected model provider has insufficient credit. Add "
                    "provider credit or select another eligible primary provider, "
                    "then explicitly resume this repair; completed stages will "
                    "be reused.",
                ),
            )
            db.execute(
                """
                UPDATE repair_stage_checkpoints
                SET state = 'waiting_for_provider',
                    failure_class = 'provider_authentication_required',
                    summary = ?,
                    retryable = 0
                WHERE state = 'failed_terminal'
                  AND (
                    lower(summary) LIKE '%http 401%'
                    OR lower(summary) LIKE '%invalid api key%'
                    OR lower(summary) LIKE '%invalid_api_key%'
                  )
                """,
                (
                    "The selected model provider needs authentication. Reconnect "
                    "or select another eligible provider, then explicitly resume "
                    "this repair; completed stages will be reused.",
                ),
            )

    def get(self, attempt_id: str, stage: str) -> RepairStageCheckpoint | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM repair_stage_checkpoints
                WHERE attempt_id = ? AND stage = ?
                """,
                (attempt_id, stage),
            ).fetchone()
        return _from_row(row) if row else None

    def save(self, checkpoint: RepairStageCheckpoint) -> None:
        values = asdict(checkpoint)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO repair_stage_checkpoints (
                    attempt_id, parent_run_id, ticker, evidence_hash, stage,
                    state, attempt_count, started_at, updated_at, deadline_at,
                    completed_at, provider, model, prompt_hash, result_hash,
                    result_payload_json, failure_class, summary, retryable,
                    execution_id, execution_state, last_activity_at,
                    hard_deadline_at, transport_version,
                    execution_telemetry_json, policy_version, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id, stage) DO UPDATE SET
                    parent_run_id = excluded.parent_run_id,
                    ticker = excluded.ticker,
                    evidence_hash = excluded.evidence_hash,
                    state = excluded.state,
                    attempt_count = excluded.attempt_count,
                    started_at = excluded.started_at,
                    updated_at = excluded.updated_at,
                    deadline_at = excluded.deadline_at,
                    completed_at = excluded.completed_at,
                    provider = excluded.provider,
                    model = excluded.model,
                    prompt_hash = excluded.prompt_hash,
                    result_hash = excluded.result_hash,
                    result_payload_json = excluded.result_payload_json,
                    failure_class = excluded.failure_class,
                    summary = excluded.summary,
                    retryable = excluded.retryable,
                    execution_id = excluded.execution_id,
                    execution_state = excluded.execution_state,
                    last_activity_at = excluded.last_activity_at,
                    hard_deadline_at = excluded.hard_deadline_at,
                    transport_version = excluded.transport_version,
                    execution_telemetry_json = excluded.execution_telemetry_json,
                    policy_version = excluded.policy_version,
                    schema_version = excluded.schema_version
                """,
                (
                    values["attempt_id"], values["parent_run_id"],
                    values["ticker"], values["evidence_hash"], values["stage"],
                    values["state"], values["attempt_count"],
                    values["started_at"], values["updated_at"],
                    values["deadline_at"], values["completed_at"],
                    values["provider"], values["model"], values["prompt_hash"],
                    values["result_hash"], json.dumps(
                        values["result_payload"], sort_keys=True, default=str
                    ), values["failure_class"], values["summary"],
                    int(bool(values["retryable"])), values["execution_id"],
                    values["execution_state"], values["last_activity_at"],
                    values["hard_deadline_at"], values["transport_version"],
                    json.dumps(
                        values["execution_telemetry"],
                        sort_keys=True,
                        default=str,
                    ), values["policy_version"], values["schema_version"],
                ),
            )

    def claim(
        self,
        checkpoint: RepairStageCheckpoint,
        *,
        expected_states: tuple[str, ...],
    ) -> bool:
        """Atomically claim one stage so duplicate processes cannot call it."""

        values = asdict(checkpoint)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT state FROM repair_stage_checkpoints
                WHERE attempt_id = ? AND stage = ?
                """,
                (checkpoint.attempt_id, checkpoint.stage),
            ).fetchone()
            current_state = str(row["state"]) if row else "__missing__"
            if current_state not in expected_states:
                return False
            db.execute(
                """
                INSERT INTO repair_stage_checkpoints (
                    attempt_id, parent_run_id, ticker, evidence_hash, stage,
                    state, attempt_count, started_at, updated_at, deadline_at,
                    completed_at, provider, model, prompt_hash, result_hash,
                    result_payload_json, failure_class, summary, retryable,
                    execution_id, execution_state, last_activity_at,
                    hard_deadline_at, transport_version,
                    execution_telemetry_json, policy_version, schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id, stage) DO UPDATE SET
                    parent_run_id = excluded.parent_run_id,
                    ticker = excluded.ticker,
                    evidence_hash = excluded.evidence_hash,
                    state = excluded.state,
                    attempt_count = excluded.attempt_count,
                    started_at = excluded.started_at,
                    updated_at = excluded.updated_at,
                    deadline_at = excluded.deadline_at,
                    completed_at = excluded.completed_at,
                    provider = excluded.provider,
                    model = excluded.model,
                    prompt_hash = excluded.prompt_hash,
                    result_hash = excluded.result_hash,
                    result_payload_json = excluded.result_payload_json,
                    failure_class = excluded.failure_class,
                    summary = excluded.summary,
                    retryable = excluded.retryable,
                    execution_id = excluded.execution_id,
                    execution_state = excluded.execution_state,
                    last_activity_at = excluded.last_activity_at,
                    hard_deadline_at = excluded.hard_deadline_at,
                    transport_version = excluded.transport_version,
                    execution_telemetry_json = excluded.execution_telemetry_json,
                    policy_version = excluded.policy_version,
                    schema_version = excluded.schema_version
                """,
                (
                    values["attempt_id"], values["parent_run_id"],
                    values["ticker"], values["evidence_hash"], values["stage"],
                    values["state"], values["attempt_count"],
                    values["started_at"], values["updated_at"],
                    values["deadline_at"], values["completed_at"],
                    values["provider"], values["model"], values["prompt_hash"],
                    values["result_hash"], json.dumps(
                        values["result_payload"], sort_keys=True, default=str
                    ), values["failure_class"], values["summary"],
                    int(bool(values["retryable"])), values["execution_id"],
                    values["execution_state"], values["last_activity_at"],
                    values["hard_deadline_at"], values["transport_version"],
                    json.dumps(
                        values["execution_telemetry"],
                        sort_keys=True,
                        default=str,
                    ), values["policy_version"], values["schema_version"],
                ),
            )
        return True

    def list_for_attempt(self, attempt_id: str) -> list[RepairStageCheckpoint]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM repair_stage_checkpoints
                WHERE attempt_id = ?
                ORDER BY CASE stage
                    WHEN 'primary_revision' THEN 1
                    WHEN 'deterministic_validation' THEN 2
                    WHEN 'closure_checker' THEN 3
                    WHEN 'publication_evaluation' THEN 4
                    ELSE 5 END
                """,
                (attempt_id,),
            ).fetchall()
        return [_from_row(row) for row in rows]


def _from_row(row: sqlite3.Row) -> RepairStageCheckpoint:
    try:
        payload = json.loads(str(row["result_payload_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    try:
        telemetry = json.loads(
            str(_row_value(row, "execution_telemetry_json", "{}") or "{}")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        telemetry = {}
    return RepairStageCheckpoint(
        attempt_id=str(row["attempt_id"]),
        parent_run_id=str(row["parent_run_id"]),
        ticker=str(row["ticker"]),
        evidence_hash=str(row["evidence_hash"]),
        stage=str(row["stage"]),
        state=str(row["state"]),
        attempt_count=int(row["attempt_count"]),
        started_at=str(row["started_at"]),
        updated_at=str(row["updated_at"]),
        deadline_at=str(row["deadline_at"]),
        completed_at=str(row["completed_at"]),
        provider=str(row["provider"]),
        model=str(row["model"]),
        prompt_hash=str(row["prompt_hash"]),
        result_hash=str(row["result_hash"]),
        result_payload=payload if isinstance(payload, dict) else {},
        failure_class=str(row["failure_class"]),
        summary=str(row["summary"]),
        retryable=bool(row["retryable"]),
        execution_id=str(_row_value(row, "execution_id", "")),
        execution_state=str(_row_value(row, "execution_state", "")),
        last_activity_at=str(_row_value(row, "last_activity_at", "")),
        hard_deadline_at=str(_row_value(row, "hard_deadline_at", "")),
        transport_version=str(_row_value(row, "transport_version", "")),
        execution_telemetry=(
            telemetry if isinstance(telemetry, dict) else {}
        ),
        policy_version=str(row["policy_version"]),
        schema_version=str(row["schema_version"]),
    )


def _row_value(row: sqlite3.Row, name: str, default: object) -> object:
    return row[name] if name in row.keys() else default
