from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from ..application.publication_readiness import RepairExecutionResult


class SqliteRepairJobRepository:
    """Durable idempotency store for bounded repair execution results."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS research_repair_results (
                    run_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, idempotency_key)
                )
                """
            )

    def get(
        self,
        run_id: str,
        idempotency_key: str,
    ) -> RepairExecutionResult | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT result_json FROM research_repair_results
                WHERE run_id = ? AND idempotency_key = ?
                """,
                (run_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["result_json"])
        for field_name in ("admitted_evidence_ids", "conclusion_changes"):
            payload[field_name] = tuple(payload.get(field_name, ()))
        return RepairExecutionResult(**payload)

    def save(
        self,
        run_id: str,
        idempotency_key: str,
        result: RepairExecutionResult,
    ) -> None:
        payload = json.dumps(asdict(result), sort_keys=True, default=str)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO research_repair_results (
                    run_id, idempotency_key, action_id, status, result_json
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id, idempotency_key) DO NOTHING
                """,
                (
                    run_id,
                    idempotency_key,
                    result.action_id,
                    result.status,
                    payload,
                ),
            )
