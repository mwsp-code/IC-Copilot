from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Protocol
import uuid

from ..domain.publication_readiness import ResearchLifecycleState


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    WAITING_FOR_CREDENTIAL = "waiting_for_credential"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    RESEARCH_ATTEMPT_FINISHED = "research_attempt_finished"
    VALIDATION_PENDING = "validation_pending"
    REPAIR_REQUIRED = "repair_required"
    REPAIR_RUNNING = "repair_running"
    REVISION_PENDING = "revision_pending"
    AUDIT_PENDING = "audit_pending"
    CLOSURE_PENDING = "closure_pending"
    PUBLISHABLE = "publishable"
    PUBLISHED = "published"
    COMPLETED_NOT_PUBLISHABLE = "completed_not_publishable"
    FAILED_PROVIDER_OR_CONNECTOR = "failed_provider_or_connector"


TERMINAL_RUN_STATUSES = {
    RunStatus.CANCELLED.value,
    RunStatus.COMPLETED.value,
    RunStatus.PARTIAL.value,
    RunStatus.FAILED.value,
    RunStatus.PUBLISHED.value,
    RunStatus.COMPLETED_NOT_PUBLISHABLE.value,
    RunStatus.FAILED_PROVIDER_OR_CONNECTOR.value,
}


@dataclass(frozen=True)
class ResearchRunCommand:
    ticker: str
    research_depth: str = "adaptive_ic"
    primary_provider_id: str = ""
    auditor_provider_id: str = ""
    independent_qa_enabled: bool = False
    adaptive_macro: bool = True
    wisburg_enabled: bool = True
    enabled_source_families: tuple[str, ...] = ()
    data_cutoff: str = ""
    max_model_spend_usd: float | None = None
    cache_bypass: bool = False
    investigate_event_id: str = ""
    investigation_action_id: str = ""
    investigation_action_kind: str = ""
    investigation_objective: str = ""
    investigation_semantic_key: str = ""
    research_intent_id: str = ""
    investigation_source_families: tuple[str, ...] = ()
    investigation_preferred_connectors: tuple[str, ...] = ()
    operation_kind: str = "full_research"
    operation_label: str = ""
    result_route: str = "Decision"
    execution_mode: str = "research"
    parent_run_id: str = ""
    parent_evidence_hash: str = ""
    repair_action_ids: tuple[str, ...] = ()
    repair_retry_stages: tuple[str, ...] = ()
    workspace_scope: str = "local"
    credential_lease_ids: tuple[str, ...] = ()
    operation_fingerprint: str = ""
    launch_attempt_id: str = ""
    launch_origin: str = ""
    parent_artifact_id: str = ""
    agent_research_effort: str = "answer_oriented"
    command_version: str = "research-run-command-v5"

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        if not ticker:
            raise ValueError("A ticker is required.")
        if self.research_depth not in {
            "fast_screening",
            "adaptive_ic",
            "deep_initiation",
            "investigate_event",
        }:
            raise ValueError(f"Unsupported research depth: {self.research_depth}.")
        if self.max_model_spend_usd is not None and self.max_model_spend_usd < 0:
            raise ValueError("The model spend limit cannot be negative.")
        if self.execution_mode not in {"research", "repair_and_recheck"}:
            raise ValueError(f"Unsupported execution mode: {self.execution_mode}.")
        if self.result_route not in {"Decision", "Research", "Evidence", "Audit"}:
            raise ValueError(f"Unsupported result route: {self.result_route}.")
        if self.agent_research_effort not in {"answer_oriented", "maximum"}:
            raise ValueError(
                f"Unsupported agent research effort: {self.agent_research_effort}."
            )
        if self.execution_mode == "repair_and_recheck" and not self.parent_run_id:
            raise ValueError("Repair and recheck requires a parent run ID.")
        object.__setattr__(self, "ticker", ticker)


@dataclass(frozen=True)
class ResearchReadiness:
    run_allowed: bool
    deterministic_status: str
    primary_status: str
    independent_qa_status: str
    evidence_status: str
    execution_status: str
    primary_provider: str = ""
    primary_model: str = ""
    auditor_provider: str = ""
    auditor_model: str = ""
    qa_requested: bool = False
    qa_resolved: bool = False
    qa_relationship: str = "not_run"
    qa_sequence: str = "Frozen audit -> adversarial scout -> closure check"
    notices: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchRunEvent:
    event_id: str
    run_id: str
    sequence: int
    stage: str
    status: str
    summary: str
    created_at: str
    idempotency_key: str
    details: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    evidence_hash: str = ""
    estimated_cost_usd: float | None = None


@dataclass(frozen=True)
class ResearchRunSnapshot:
    run_id: str
    command: ResearchRunCommand
    status: str
    current_stage: str
    created_at: str
    updated_at: str
    event_count: int = 0
    evidence_hash: str = ""
    result_reference: str = ""
    stopping_reason: str = ""
    errors: tuple[str, ...] = ()
    lifecycle_state: str = "research_running"
    research_attempt_finished_at: str = ""
    validation_completed_at: str = ""
    audit_completed_at: str = ""
    revision_completed_at: str = ""
    closure_completed_at: str = ""
    publishable_at: str = ""
    published_at: str = ""
    terminal_at: str = ""
    completed_at: str = ""
    snapshot_version: str = "research-run-snapshot-v2"


class ResearchJobRepository(Protocol):
    def create(self, snapshot: ResearchRunSnapshot) -> None: ...

    def get(self, run_id: str) -> ResearchRunSnapshot | None: ...

    def save(self, snapshot: ResearchRunSnapshot) -> None: ...

    def append_event(self, event: ResearchRunEvent) -> ResearchRunEvent: ...

    def events(self, run_id: str) -> list[ResearchRunEvent]: ...

    def create_if_absent(
        self,
        snapshot: ResearchRunSnapshot,
    ) -> tuple[ResearchRunSnapshot, bool]: ...

    def find_latest_by_operation_fingerprint(
        self,
        operation_fingerprint: str,
        *,
        workspace_scope: str,
    ) -> ResearchRunSnapshot | None: ...


class ResearchRunner(Protocol):
    def submit(self, run_id: str, command: ResearchRunCommand) -> None: ...

    def cancel(self, run_id: str) -> None: ...


class ResearchRunService:
    """API-ready lifecycle boundary used by Streamlit and future clients."""

    def __init__(
        self,
        repository: ResearchJobRepository,
        runner: ResearchRunner | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner

    def start(self, command: ResearchRunCommand) -> ResearchRunSnapshot:
        snapshot, _ = self.start_or_reuse(command)
        return snapshot

    def start_or_reuse(
        self,
        command: ResearchRunCommand,
    ) -> tuple[ResearchRunSnapshot, bool]:
        now = _utc_now()
        run_id = (
            str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "ic-copilot:"
                    + command.workspace_scope
                    + ":"
                    + command.operation_fingerprint
                    + ":"
                    + command.launch_attempt_id,
                )
            )
            if command.operation_fingerprint and command.launch_attempt_id
            else str(uuid.uuid4())
        )
        snapshot = ResearchRunSnapshot(
            run_id=run_id,
            command=command,
            status=RunStatus.QUEUED.value,
            current_stage="preflight",
            created_at=now,
            updated_at=now,
            lifecycle_state=ResearchLifecycleState.RESEARCH_RUNNING.value,
        )
        create_if_absent = getattr(self.repository, "create_if_absent", None)
        if callable(create_if_absent):
            snapshot, created = create_if_absent(snapshot)
        else:
            existing = self.repository.get(run_id)
            if existing is not None:
                return existing, False
            self.repository.create(snapshot)
            created = True
        if not created:
            return snapshot, False
        self.record_event(
            run_id,
            stage="preflight",
            status="queued",
            summary="Research command frozen and queued for deterministic preflight.",
            details={
                "command_version": command.command_version,
                "operation_kind": command.operation_kind,
                "operation_label": command.operation_label,
                "investigation_action_id": command.investigation_action_id,
                "investigation_action_kind": command.investigation_action_kind,
                "investigation_semantic_key": command.investigation_semantic_key,
                "research_intent_id": command.research_intent_id,
                "result_route": command.result_route,
                "operation_fingerprint": command.operation_fingerprint,
                "launch_attempt_id": command.launch_attempt_id,
                "launch_origin": command.launch_origin,
            },
        )
        if self.runner is not None:
            self.runner.submit(run_id, command)
        return self.inspect(run_id) or snapshot, True

    def find_latest_by_operation_fingerprint(
        self,
        operation_fingerprint: str,
        *,
        workspace_scope: str,
    ) -> ResearchRunSnapshot | None:
        if not operation_fingerprint:
            return None
        finder = getattr(
            self.repository,
            "find_latest_by_operation_fingerprint",
            None,
        )
        if not callable(finder):
            return None
        return finder(
            operation_fingerprint,
            workspace_scope=workspace_scope,
        )

    def inspect(self, run_id: str) -> ResearchRunSnapshot | None:
        return self.repository.get(run_id)

    def timeline(self, run_id: str) -> list[ResearchRunEvent]:
        return self.repository.events(run_id)

    def resume(self, run_id: str) -> ResearchRunSnapshot:
        snapshot = self._required(run_id)
        if snapshot.status in TERMINAL_RUN_STATUSES:
            return snapshot
        if self.runner is not None:
            self.runner.submit(run_id, snapshot.command)
        self.record_event(
            run_id,
            stage=snapshot.current_stage,
            status="resume_requested",
            summary="A resumable research run was submitted to the configured worker.",
        )
        return self._required(run_id)

    def cancel(self, run_id: str) -> ResearchRunSnapshot:
        snapshot = self._required(run_id)
        if snapshot.status in TERMINAL_RUN_STATUSES:
            return snapshot
        if self.runner is not None:
            self.runner.cancel(run_id)
        updated = replace(
            snapshot,
            status=RunStatus.CANCELLING.value,
            updated_at=_utc_now(),
            stopping_reason="user_cancellation",
            lifecycle_state=snapshot.lifecycle_state,
        )
        self.repository.save(updated)
        self.record_event(
            run_id,
            stage=snapshot.current_stage,
            status="cancelling",
            summary="Cancellation requested; completed artifacts remain available.",
        )
        return self._required(run_id)

    def mark_credential_expired(
        self,
        run_id: str,
        *,
        stage: str,
        provider_id: str,
    ) -> ResearchRunSnapshot:
        """Preserve completed artifacts while a session credential is renewed."""

        snapshot = self._required(run_id)
        message = f"Credential expired for {provider_id}; reconnect to resume."
        updated = replace(
            snapshot,
            status=RunStatus.WAITING_FOR_CREDENTIAL.value,
            current_stage=stage,
            updated_at=_utc_now(),
            stopping_reason="credential_expired",
            errors=tuple(dict.fromkeys((*snapshot.errors, message))),
            lifecycle_state=RunStatus.WAITING_FOR_CREDENTIAL.value,
        )
        self.repository.save(updated)
        self.record_event(
            run_id,
            stage=stage,
            status="credential_expired",
            summary=(
                "The session credential expired. Completed artifacts were "
                "preserved and Independent QA has not been marked as passed."
            ),
            details={"provider_id": provider_id},
            idempotency_key=f"credential-expired:{stage}:{provider_id}",
        )
        return self._required(run_id)

    def reconnect_credentials(
        self,
        run_id: str,
        credential_lease_ids: tuple[str, ...],
    ) -> ResearchRunSnapshot:
        """Attach replacement opaque leases and resume the same idempotent run."""

        if not credential_lease_ids:
            raise ValueError("At least one replacement credential lease is required.")
        snapshot = self._required(run_id)
        updated = replace(
            snapshot,
            command=replace(
                snapshot.command,
                credential_lease_ids=tuple(credential_lease_ids),
            ),
            status=RunStatus.QUEUED.value,
            updated_at=_utc_now(),
            stopping_reason="",
            lifecycle_state=ResearchLifecycleState.RESEARCH_RUNNING.value,
        )
        self.repository.save(updated)
        self.record_event(
            run_id,
            stage=snapshot.current_stage,
            status="credential_reconnected",
            summary="A replacement session credential was connected; the run may resume.",
            details={"lease_count": len(credential_lease_ids)},
            idempotency_key=(
                "credential-reconnected:"
                + _stable_hash(sorted(credential_lease_ids))[:16]
            ),
        )
        return self.resume(run_id)

    def mark_running(self, run_id: str, stage: str, summary: str) -> ResearchRunSnapshot:
        snapshot = self._required(run_id)
        updated = replace(
            snapshot,
            status=RunStatus.RUNNING.value,
            current_stage=stage,
            updated_at=_utc_now(),
            lifecycle_state=ResearchLifecycleState.RESEARCH_RUNNING.value,
        )
        self.repository.save(updated)
        self.record_event(run_id, stage=stage, status="running", summary=summary)
        return self._required(run_id)

    def mark_research_attempt_finished(self, run_id: str) -> ResearchRunSnapshot:
        return self._transition(
            run_id,
            status=RunStatus.RESEARCH_ATTEMPT_FINISHED,
            stage="research_attempt_finished",
            summary="Research gathering and initial analysis finished; validation is next.",
            timestamp_field="research_attempt_finished_at",
        )

    def mark_validation_pending(self, run_id: str) -> ResearchRunSnapshot:
        return self._transition(
            run_id,
            status=RunStatus.VALIDATION_PENDING,
            stage="deterministic_validation",
            summary="Deterministic validation is pending; no final audit pass is asserted.",
        )

    def mark_validation_completed(
        self,
        run_id: str,
        *,
        passed: bool,
        evidence_hash: str = "",
    ) -> ResearchRunSnapshot:
        return self._transition(
            run_id,
            status=RunStatus.AUDIT_PENDING if passed else RunStatus.REPAIR_REQUIRED,
            stage="audit_pending" if passed else "repair_required",
            summary=(
                "Deterministic validation passed; the validated artifact may be frozen for audit."
                if passed else
                "Deterministic validation found material blockers and generated repair work."
            ),
            timestamp_field="validation_completed_at",
            evidence_hash=evidence_hash,
        )

    def mark_revision_pending(self, run_id: str) -> ResearchRunSnapshot:
        return self._transition(
            run_id,
            status=RunStatus.REVISION_PENDING,
            stage="revision_pending",
            summary="Primary responses and revision are pending for material audit findings.",
        )

    def mark_closure_pending(self, run_id: str) -> ResearchRunSnapshot:
        return self._transition(
            run_id,
            status=RunStatus.CLOSURE_PENDING,
            stage="closure_pending",
            summary="Final independent closure is pending and must not be displayed as passed.",
        )

    def mark_publishable(self, run_id: str) -> ResearchRunSnapshot:
        snapshot = self._required(run_id)
        if not snapshot.validation_completed_at or not snapshot.closure_completed_at:
            raise ValueError("Publishable requires completed validation and closure timestamps.")
        return self._transition(
            run_id,
            status=RunStatus.PUBLISHABLE,
            stage="publishable",
            summary="All active publication gates passed.",
            timestamp_field="publishable_at",
        )

    def mark_published(self, run_id: str) -> ResearchRunSnapshot:
        snapshot = self._required(run_id)
        if not snapshot.publishable_at:
            raise ValueError("A run cannot be published before it is publishable.")
        return self._transition(
            run_id,
            status=RunStatus.PUBLISHED,
            stage="published",
            summary="The validated and closed artifact was published.",
            timestamp_field="published_at",
            terminal=True,
        )

    def mark_closure_completed(self, run_id: str, *, passed: bool) -> ResearchRunSnapshot:
        snapshot = self._required(run_id)
        if passed and not snapshot.validation_completed_at:
            raise ValueError(
                "Passing closure requires completed deterministic validation."
            )
        return self._transition(
            run_id,
            status=RunStatus.PUBLISHABLE if passed else RunStatus.REPAIR_REQUIRED,
            stage="publishable" if passed else "repair_required",
            summary=(
                "Independent closure passed."
                if passed else
                "Independent closure did not pass; the artifact remains a research draft."
            ),
            timestamp_field="closure_completed_at",
        )

    def finish(
        self,
        run_id: str,
        *,
        status: RunStatus | str,
        result_reference: str = "",
        evidence_hash: str = "",
        stopping_reason: str = "completed",
        errors: tuple[str, ...] = (),
    ) -> ResearchRunSnapshot:
        final_status = RunStatus(status)
        if final_status.value not in TERMINAL_RUN_STATUSES:
            raise ValueError("finish requires a terminal run status.")
        snapshot = self._required(run_id)
        now = _utc_now()
        lifecycle_state = (
            final_status.value
            if final_status in {
                RunStatus.CANCELLED,
                RunStatus.PUBLISHED,
                RunStatus.COMPLETED_NOT_PUBLISHABLE,
                RunStatus.FAILED_PROVIDER_OR_CONNECTOR,
            }
            else RunStatus.COMPLETED_NOT_PUBLISHABLE.value
        )
        updated = replace(
            snapshot,
            status=final_status.value,
            current_stage="complete",
            updated_at=now,
            result_reference=result_reference,
            evidence_hash=evidence_hash,
            stopping_reason=stopping_reason,
            errors=tuple(errors),
            lifecycle_state=lifecycle_state,
            research_attempt_finished_at=(
                snapshot.research_attempt_finished_at or now
            ),
            terminal_at=now,
            completed_at=now,
        )
        self.repository.save(updated)
        self.record_event(
            run_id,
            stage="complete",
            status=final_status.value,
            summary=(
                "Research completed and the final artifact was packaged."
                if final_status == RunStatus.COMPLETED
                else "Research stopped with validated partial artifacts preserved."
            ),
            evidence_hash=evidence_hash,
            details={"stopping_reason": stopping_reason, "errors": list(errors)},
        )
        return self._required(run_id)

    def _transition(
        self,
        run_id: str,
        *,
        status: RunStatus,
        stage: str,
        summary: str,
        timestamp_field: str = "",
        evidence_hash: str = "",
        terminal: bool = False,
    ) -> ResearchRunSnapshot:
        snapshot = self._required(run_id)
        now = _utc_now()
        changes: dict[str, Any] = {
            "status": status.value,
            "lifecycle_state": status.value,
            "current_stage": stage,
            "updated_at": now,
        }
        if evidence_hash:
            changes["evidence_hash"] = evidence_hash
        if timestamp_field:
            changes[timestamp_field] = now
        if status == RunStatus.PUBLISHABLE:
            changes["publishable_at"] = now
        if terminal:
            changes["terminal_at"] = now
            changes["completed_at"] = now
        self.repository.save(replace(snapshot, **changes))
        self.record_event(
            run_id,
            stage=stage,
            status=status.value,
            summary=summary,
            evidence_hash=evidence_hash,
            idempotency_key=f"lifecycle:{stage}:{evidence_hash}",
        )
        return self._required(run_id)

    def record_event(
        self,
        run_id: str,
        *,
        stage: str,
        status: str,
        summary: str,
        details: dict[str, Any] | None = None,
        provider: str = "",
        model: str = "",
        evidence_hash: str = "",
        estimated_cost_usd: float | None = None,
        idempotency_key: str = "",
    ) -> ResearchRunEvent:
        snapshot = self._required(run_id)
        key = idempotency_key or _stable_hash({
            "run_id": run_id,
            "stage": stage,
            "status": status,
            "summary": summary,
            "details": details or {},
            "evidence_hash": evidence_hash,
        })[:24]
        event = ResearchRunEvent(
            event_id=str(uuid.uuid4()),
            run_id=run_id,
            sequence=snapshot.event_count + 1,
            stage=stage,
            status=status,
            summary=summary,
            created_at=_utc_now(),
            idempotency_key=key,
            details=dict(details or {}),
            provider=provider,
            model=model,
            evidence_hash=evidence_hash,
            estimated_cost_usd=estimated_cost_usd,
        )
        stored = self.repository.append_event(event)
        latest = self._required(run_id)
        if latest.event_count < stored.sequence:
            self.repository.save(replace(
                latest,
                event_count=stored.sequence,
                updated_at=stored.created_at,
            ))
        return stored

    def _required(self, run_id: str) -> ResearchRunSnapshot:
        snapshot = self.repository.get(run_id)
        if snapshot is None:
            raise KeyError(f"Unknown research run: {run_id}")
        return snapshot


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
