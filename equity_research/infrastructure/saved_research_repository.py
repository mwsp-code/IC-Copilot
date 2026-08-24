from __future__ import annotations

from dataclasses import asdict, fields, replace
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from ..domain.saved_research import (
    ArtifactVerificationResult,
    DerivedEvidenceRecoveryAttempt,
    SavedArtifactHealth,
    SavedResearchArtifact,
    SavedResearchQuery,
    SavedResearchOrigin,
    SavedResearchScope,
    SavedResearchStatus,
)


class SavedResearchStorageError(RuntimeError):
    pass


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class ContentAddressedGzipBlobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()

    def put(self, payload: bytes) -> tuple[str, str, int]:
        digest = hashlib.sha256(payload).hexdigest()
        reference = f"sha256/{digest[:2]}/{digest}.json.gz"
        target = self.root / Path(reference)
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=6, mtime=0) as stream:
            stream.write(payload)
        with self._write_lock:
            if target.exists():
                return reference, digest, target.stat().st_size
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(
                target.suffix + f".{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                temporary.write_bytes(buffer.getvalue())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return reference, digest, target.stat().st_size

    def get(self, reference: str, *, expected_sha256: str) -> bytes:
        target = self._safe_path(reference)
        if not target.exists():
            raise SavedResearchStorageError("The saved research payload is missing.")
        try:
            payload = gzip.decompress(target.read_bytes())
        except (OSError, EOFError) as exc:
            raise SavedResearchStorageError("The saved research payload is corrupt.") from exc
        digest = hashlib.sha256(payload).hexdigest()
        if digest != expected_sha256:
            raise SavedResearchStorageError("The saved research integrity check failed.")
        return payload

    def delete(self, reference: str) -> None:
        target = self._safe_path(reference)
        if target.exists():
            target.unlink()

    def _safe_path(self, reference: str) -> Path:
        target = (self.root / Path(reference)).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise SavedResearchStorageError("Invalid saved research payload reference.")
        return target


class SqliteSavedResearchRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
        except (OSError, sqlite3.Error) as exc:
            raise SavedResearchStorageError(
                "Saved research metadata storage could not be initialized."
            ) from exc

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
                CREATE TABLE IF NOT EXISTS saved_research_artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    security_key TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    company_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    operation_kind TEXT NOT NULL,
                    operation_label TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    payload_reference TEXT NOT NULL,
                    storage_scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    artifact_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_saved_research_ticker_time
                    ON saved_research_artifacts(scope_id, ticker, saved_at DESC);
                CREATE INDEX IF NOT EXISTS idx_saved_research_security_time
                    ON saved_research_artifacts(scope_id, security_key, saved_at DESC);
                CREATE TABLE IF NOT EXISTS saved_research_preferences (
                    scope_id TEXT PRIMARY KEY,
                    last_artifact_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS saved_research_ticker_aliases (
                    artifact_id TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    ticker_alias TEXT NOT NULL,
                    PRIMARY KEY (artifact_id, ticker_alias)
                );
                CREATE INDEX IF NOT EXISTS idx_saved_research_alias
                    ON saved_research_ticker_aliases(scope_id, ticker_alias);
                CREATE TABLE IF NOT EXISTS derived_evidence_recovery_attempts (
                    recovery_key TEXT PRIMARY KEY,
                    scope_id TEXT NOT NULL,
                    parent_artifact_id TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    data_cutoff TEXT NOT NULL,
                    analysis_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    source_snapshot_hash TEXT NOT NULL DEFAULT '',
                    derived_artifact_id TEXT NOT NULL DEFAULT '',
                    readable_reason TEXT NOT NULL DEFAULT '',
                    attempt_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_recovery_parent
                    ON derived_evidence_recovery_attempts(scope_id, parent_artifact_id);
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(saved_research_artifacts)")
            }
            if "health" not in columns:
                db.execute(
                    "ALTER TABLE saved_research_artifacts "
                    "ADD COLUMN health TEXT NOT NULL DEFAULT 'unchecked'"
                )
            if "codec_version" not in columns:
                db.execute(
                    "ALTER TABLE saved_research_artifacts "
                    "ADD COLUMN codec_version TEXT NOT NULL DEFAULT ''"
                )
            if "verified_at" not in columns:
                db.execute(
                    "ALTER TABLE saved_research_artifacts "
                    "ADD COLUMN verified_at TEXT NOT NULL DEFAULT ''"
                )
            db.execute("DROP INDEX IF EXISTS idx_saved_research_idempotency")
            db.execute(
                """
                CREATE UNIQUE INDEX idx_saved_research_idempotency
                ON saved_research_artifacts(
                    scope_id, run_id, evidence_hash, operation_kind, outcome,
                    payload_reference
                )
                """
            )

    def save(self, artifact: SavedResearchArtifact) -> SavedResearchArtifact:
        existing = self.get(artifact.artifact_id, scope_id=artifact.scope_id)
        if existing is not None:
            return existing
        payload = asdict(artifact)
        payload["status"] = artifact.status.value
        payload["origin"] = artifact.origin.value
        payload["storage_scope"] = artifact.storage_scope.value
        payload["health"] = artifact.health.value
        payload["ticker_aliases"] = list(artifact.ticker_aliases)
        with self._connect() as db:
            try:
                db.execute(
                    """
                    INSERT INTO saved_research_artifacts (
                        artifact_id, run_id, security_key, ticker, company_name,
                        status, outcome, operation_kind, operation_label,
                        completed_at, saved_at, evidence_hash, payload_reference,
                        storage_scope, scope_id, pinned, artifact_json,
                        health, codec_version, verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact.artifact_id,
                        artifact.run_id,
                        artifact.security_key,
                        artifact.ticker,
                        artifact.company_name,
                        artifact.status.value,
                        artifact.outcome,
                        artifact.operation_kind,
                        artifact.operation_label,
                        artifact.completed_at,
                        artifact.saved_at,
                        artifact.evidence_hash,
                        artifact.payload_reference,
                        artifact.storage_scope.value,
                        artifact.scope_id,
                        int(artifact.pinned),
                        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                        artifact.health.value,
                        artifact.codec_version,
                        artifact.verified_at,
                    ),
                )
                db.executemany(
                    """
                    INSERT OR IGNORE INTO saved_research_ticker_aliases(
                        artifact_id, scope_id, ticker_alias
                    ) VALUES (?, ?, ?)
                    """,
                    [
                        (artifact.artifact_id, artifact.scope_id, alias.upper())
                        for alias in artifact.ticker_aliases
                    ],
                )
            except sqlite3.IntegrityError:
                row = db.execute(
                    """
                    SELECT artifact_json FROM saved_research_artifacts
                    WHERE scope_id=? AND run_id=? AND evidence_hash=?
                      AND operation_kind=? AND outcome=? AND payload_reference=?
                    """,
                    (
                        artifact.scope_id,
                        artifact.run_id,
                        artifact.evidence_hash,
                        artifact.operation_kind,
                        artifact.outcome,
                        artifact.payload_reference,
                    ),
                ).fetchone()
                if row:
                    return self._from_json(row["artifact_json"])
                raise
        return artifact

    def get(self, artifact_id: str, *, scope_id: str) -> SavedResearchArtifact | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT artifact_json FROM saved_research_artifacts WHERE artifact_id=? AND scope_id=?",
                (artifact_id, scope_id),
            ).fetchone()
        return self._from_json(row["artifact_json"]) if row else None

    def list(self, query: SavedResearchQuery) -> list[SavedResearchArtifact]:
        clauses = ["scope_id = ?"]
        values: list[object] = [query.scope_id]
        if query.ticker:
            clauses.append(
                """(
                    ticker = ? OR artifact_id IN (
                        SELECT artifact_id FROM saved_research_ticker_aliases
                        WHERE scope_id = ? AND ticker_alias = ?
                    )
                )"""
            )
            values.extend([
                query.ticker.upper(),
                query.scope_id,
                query.ticker.upper(),
            ])
        if query.security_key:
            clauses.append("security_key = ?")
            values.append(query.security_key)
        if query.completed_only:
            clauses.append("status IN ('completed', 'residual_diligence')")
        elif not query.include_drafts:
            clauses.append("status NOT IN ('draft', 'partial', 'failed')")
        values.extend([max(1, int(query.limit)), max(0, int(query.offset))])
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT artifact_json FROM saved_research_artifacts
                WHERE {' AND '.join(clauses)}
                ORDER BY COALESCE(NULLIF(completed_at, ''), saved_at) DESC, saved_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple(values),
            ).fetchall()
        return [self._from_json(row["artifact_json"]) for row in rows]

    def delete(self, artifact_id: str, *, scope_id: str) -> bool:
        with self._connect() as db:
            db.execute(
                "DELETE FROM saved_research_ticker_aliases WHERE artifact_id=? AND scope_id=?",
                (artifact_id, scope_id),
            )
            cursor = db.execute(
                "DELETE FROM saved_research_artifacts WHERE artifact_id=? AND scope_id=?",
                (artifact_id, scope_id),
            )
        return cursor.rowcount == 1

    def set_pinned(self, artifact_id: str, *, scope_id: str, pinned: bool) -> bool:
        artifact = self.get(artifact_id, scope_id=scope_id)
        if artifact is None:
            return False
        payload = asdict(artifact)
        payload["status"] = artifact.status.value
        payload["origin"] = artifact.origin.value
        payload["storage_scope"] = artifact.storage_scope.value
        payload["health"] = artifact.health.value
        payload["ticker_aliases"] = list(artifact.ticker_aliases)
        payload["pinned"] = bool(pinned)
        with self._connect() as db:
            cursor = db.execute(
                """
                UPDATE saved_research_artifacts
                SET pinned=?, artifact_json=? WHERE artifact_id=? AND scope_id=?
                """,
                (
                    int(pinned),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                    artifact_id,
                    scope_id,
                ),
            )
        return cursor.rowcount == 1

    def payload_reference_count(self, payload_reference: str) -> int:
        with self._connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM saved_research_artifacts WHERE payload_reference=?",
                (payload_reference,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def set_last_displayed(self, *, scope_id: str, artifact_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO saved_research_preferences(scope_id, last_artifact_id)
                VALUES (?, ?)
                ON CONFLICT(scope_id) DO UPDATE SET last_artifact_id=excluded.last_artifact_id
                """,
                (scope_id, artifact_id),
            )

    def get_last_displayed(self, *, scope_id: str) -> str:
        with self._connect() as db:
            row = db.execute(
                "SELECT last_artifact_id FROM saved_research_preferences WHERE scope_id=?",
                (scope_id,),
            ).fetchone()
        return str(row["last_artifact_id"] if row else "")

    def update_health(
        self,
        artifact_id: str,
        *,
        scope_id: str,
        verification: ArtifactVerificationResult,
    ) -> SavedResearchArtifact | None:
        artifact = self.get(artifact_id, scope_id=scope_id)
        if artifact is None:
            return None
        updated = replace(
            artifact,
            health=verification.status,
            verified_at=verification.verified_at,
            codec_version=verification.codec_version or artifact.codec_version,
            writer_runtime=verification.writer_runtime or artifact.writer_runtime,
            last_verification_reason=verification.reason,
            last_verification_path=verification.failure_path,
        )
        payload = asdict(updated)
        payload["status"] = updated.status.value
        payload["origin"] = updated.origin.value
        payload["storage_scope"] = updated.storage_scope.value
        payload["health"] = updated.health.value
        payload["ticker_aliases"] = list(updated.ticker_aliases)
        with self._connect() as db:
            db.execute(
                """
                UPDATE saved_research_artifacts
                   SET artifact_json=?, health=?, codec_version=?, verified_at=?
                 WHERE artifact_id=? AND scope_id=?
                """,
                (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
                    updated.health.value,
                    updated.codec_version,
                    updated.verified_at,
                    artifact_id,
                    scope_id,
                ),
            )
        return updated

    def get_recovery_attempt(
        self, recovery_key: str, *, scope_id: str
    ) -> DerivedEvidenceRecoveryAttempt | None:
        with self._connect() as db:
            row = db.execute(
                """SELECT attempt_json FROM derived_evidence_recovery_attempts
                   WHERE recovery_key=? AND scope_id=?""",
                (recovery_key, scope_id),
            ).fetchone()
        return self._recovery_from_json(row["attempt_json"]) if row else None

    def save_recovery_attempt(
        self, attempt: DerivedEvidenceRecoveryAttempt,
    ) -> DerivedEvidenceRecoveryAttempt:
        existing = self.get_recovery_attempt(
            attempt.recovery_key,
            scope_id=attempt.scope_id,
        )
        if existing is not None and existing.status in {"recovered", "blocked"}:
            return existing
        payload = json.dumps(asdict(attempt), ensure_ascii=False, sort_keys=True)
        with self._connect() as db:
            db.execute(
                """INSERT INTO derived_evidence_recovery_attempts (
                    recovery_key, scope_id, parent_artifact_id, intent_id,
                    data_cutoff, analysis_version, status, attempted_at,
                    source_snapshot_hash, derived_artifact_id, readable_reason,
                    attempt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(recovery_key) DO UPDATE SET
                    status=excluded.status,
                    attempted_at=excluded.attempted_at,
                    source_snapshot_hash=excluded.source_snapshot_hash,
                    derived_artifact_id=excluded.derived_artifact_id,
                    readable_reason=excluded.readable_reason,
                    attempt_json=excluded.attempt_json
                """,
                (
                    attempt.recovery_key, attempt.scope_id,
                    attempt.parent_artifact_id, attempt.intent_id,
                    attempt.data_cutoff, attempt.analysis_version,
                    attempt.status, attempt.attempted_at,
                    attempt.source_snapshot_hash, attempt.derived_artifact_id,
                    attempt.readable_reason, payload,
                ),
            )
        return attempt

    @staticmethod
    def _from_json(payload_json: str) -> SavedResearchArtifact:
        payload = json.loads(payload_json)
        known_fields = {item.name for item in fields(SavedResearchArtifact)}
        payload = {
            key: value
            for key, value in payload.items()
            if key in known_fields
        }
        payload["status"] = SavedResearchStatus(payload["status"])
        payload["origin"] = SavedResearchOrigin(
            payload.get("origin") or SavedResearchOrigin.LIVE_RESEARCH.value
        )
        payload["storage_scope"] = SavedResearchScope(payload["storage_scope"])
        payload["health"] = SavedArtifactHealth(
            payload.get("health") or SavedArtifactHealth.UNCHECKED.value
        )
        payload.setdefault("codec_version", "")
        payload.setdefault("writer_runtime", "")
        payload["ticker_aliases"] = tuple(payload.get("ticker_aliases") or ())
        payload["analysis_capabilities"] = tuple(
            payload.get("analysis_capabilities") or ()
        )
        return SavedResearchArtifact(**payload)

    @staticmethod
    def _recovery_from_json(payload_json: str) -> DerivedEvidenceRecoveryAttempt:
        return DerivedEvidenceRecoveryAttempt(**json.loads(payload_json))


class InMemorySavedResearchBlobStore:
    def __init__(self, *, ttl_seconds: int = 14_400) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.payloads: dict[str, bytes] = {}
        self.expires_at: dict[str, float] = {}

    def put(self, payload: bytes) -> tuple[str, str, int]:
        self._purge_expired()
        digest = hashlib.sha256(payload).hexdigest()
        reference = f"memory:{digest}"
        self.payloads.setdefault(reference, payload)
        self.expires_at[reference] = time.time() + self.ttl_seconds
        return reference, digest, len(payload)

    def get(self, reference: str, *, expected_sha256: str) -> bytes:
        self._purge_expired()
        payload = self.payloads.get(reference)
        if payload is None:
            raise SavedResearchStorageError("The saved research session has expired.")
        if hashlib.sha256(payload).hexdigest() != expected_sha256:
            raise SavedResearchStorageError("The saved research integrity check failed.")
        return payload

    def delete(self, reference: str) -> None:
        self.payloads.pop(reference, None)
        self.expires_at.pop(reference, None)

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            reference for reference, expires_at in self.expires_at.items()
            if expires_at <= now
        ]
        for reference in expired:
            self.payloads.pop(reference, None)
            self.expires_at.pop(reference, None)


class InMemorySavedResearchRepository:
    def __init__(self, *, ttl_seconds: int = 14_400) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self.artifacts: dict[str, SavedResearchArtifact] = {}
        self.expires_at: dict[str, float] = {}
        self.last_displayed: dict[str, str] = {}
        self.recovery_attempts: dict[str, DerivedEvidenceRecoveryAttempt] = {}

    def save(self, artifact: SavedResearchArtifact) -> SavedResearchArtifact:
        self._purge_expired()
        existing = self.artifacts.get(artifact.artifact_id)
        if existing is not None:
            return existing
        self.artifacts[artifact.artifact_id] = artifact
        self.expires_at[artifact.artifact_id] = time.time() + self.ttl_seconds
        return artifact

    def get(self, artifact_id: str, *, scope_id: str) -> SavedResearchArtifact | None:
        self._purge_expired()
        artifact = self.artifacts.get(artifact_id)
        if artifact is None or artifact.scope_id != scope_id:
            return None
        return artifact

    def list(self, query: SavedResearchQuery) -> list[SavedResearchArtifact]:
        self._purge_expired()
        matches = [
            item for item in self.artifacts.values()
            if item.scope_id == query.scope_id
            and (
                not query.ticker
                or item.ticker == query.ticker.upper()
                or query.ticker.upper() in item.ticker_aliases
            )
            and (not query.security_key or item.security_key == query.security_key)
            and (not query.completed_only or item.is_completed)
            and (
                query.include_drafts
                or item.status not in {
                    SavedResearchStatus.DRAFT,
                    SavedResearchStatus.PARTIAL,
                    SavedResearchStatus.FAILED,
                }
            )
        ]
        matches.sort(key=lambda item: (item.completed_at or item.saved_at, item.saved_at), reverse=True)
        start = max(0, int(query.offset))
        return matches[start:start + max(1, int(query.limit))]

    def delete(self, artifact_id: str, *, scope_id: str) -> bool:
        artifact = self.get(artifact_id, scope_id=scope_id)
        if artifact is None:
            return False
        self.artifacts.pop(artifact_id, None)
        self.expires_at.pop(artifact_id, None)
        return True

    def set_pinned(self, artifact_id: str, *, scope_id: str, pinned: bool) -> bool:
        artifact = self.get(artifact_id, scope_id=scope_id)
        if artifact is None:
            return False
        updated = SavedResearchArtifact(
            **{
                **asdict(artifact),
                "status": artifact.status,
                "origin": artifact.origin,
                "storage_scope": artifact.storage_scope,
                "pinned": pinned,
            }
        )
        self.artifacts[artifact_id] = updated
        return True

    def payload_reference_count(self, payload_reference: str) -> int:
        self._purge_expired()
        return sum(
            item.payload_reference == payload_reference
            for item in self.artifacts.values()
        )

    def set_last_displayed(self, *, scope_id: str, artifact_id: str) -> None:
        self.last_displayed[scope_id] = artifact_id

    def get_last_displayed(self, *, scope_id: str) -> str:
        return self.last_displayed.get(scope_id, "")

    def update_health(
        self,
        artifact_id: str,
        *,
        scope_id: str,
        verification: ArtifactVerificationResult,
    ) -> SavedResearchArtifact | None:
        artifact = self.get(artifact_id, scope_id=scope_id)
        if artifact is None:
            return None
        updated = replace(
            artifact,
            health=verification.status,
            verified_at=verification.verified_at,
            codec_version=verification.codec_version or artifact.codec_version,
            writer_runtime=verification.writer_runtime or artifact.writer_runtime,
            last_verification_reason=verification.reason,
            last_verification_path=verification.failure_path,
        )
        self.artifacts[artifact_id] = updated
        return updated

    def get_recovery_attempt(
        self, recovery_key: str, *, scope_id: str
    ) -> DerivedEvidenceRecoveryAttempt | None:
        item = self.recovery_attempts.get(recovery_key)
        return item if item is not None and item.scope_id == scope_id else None

    def save_recovery_attempt(
        self, attempt: DerivedEvidenceRecoveryAttempt,
    ) -> DerivedEvidenceRecoveryAttempt:
        existing = self.get_recovery_attempt(
            attempt.recovery_key,
            scope_id=attempt.scope_id,
        )
        if existing is not None and existing.status in {"recovered", "blocked"}:
            return existing
        self.recovery_attempts[attempt.recovery_key] = attempt
        return attempt

    def _purge_expired(self) -> None:
        now = time.time()
        expired = [
            artifact_id for artifact_id, expires_at in self.expires_at.items()
            if expires_at <= now
        ]
        for artifact_id in expired:
            self.artifacts.pop(artifact_id, None)
            self.expires_at.pop(artifact_id, None)
