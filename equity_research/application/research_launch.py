from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any
import uuid

from .research_runs import ResearchRunCommand, ResearchRunService, ResearchRunSnapshot


class ResearchOperationKind(str, Enum):
    FULL_RESEARCH = "full_research"
    EVENT_INVESTIGATION = "event_investigation"
    ACTION_INVESTIGATION = "action_investigation"
    ANALOG_INVESTIGATION = "analog_investigation"
    REPAIR_RECHECK = "repair_recheck"


class ResearchLaunchIntentStatus(str, Enum):
    SELECTED = "selected"
    NEEDS_SOURCE_SETUP = "needs_source_setup"
    READY = "ready"
    LAUNCHING = "launching"
    ACTIVE = "active"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ResearchLaunchIntent:
    intent_id: str
    ticker: str
    operation_kind: ResearchOperationKind
    operation_label: str
    objective: str
    launch_origin: str
    research_profile: str
    preferred_result_route: str
    status: ResearchLaunchIntentStatus
    operation_fingerprint: str
    created_at: str
    action_id: str = ""
    action_kind: str = ""
    action_semantic_key: str = ""
    research_intent_id: str = ""
    compatible_operation_fingerprints: tuple[str, ...] = ()
    event_id: str = ""
    analog_id: str = ""
    repair_action_ids: tuple[str, ...] = ()
    repair_retry_stages: tuple[str, ...] = ()
    parent_run_id: str = ""
    parent_artifact_id: str = ""
    parent_evidence_hash: str = ""
    required_source_families: tuple[str, ...] = ()
    preferred_connectors: tuple[str, ...] = ()
    qa_enabled: bool = False
    workspace_scope: str = "local"
    blocking_reason: str = ""
    intent_version: str = "research-launch-intent-v1"

    def __post_init__(self) -> None:
        ticker = str(self.ticker or "").strip().upper()
        if not ticker:
            raise ValueError("A research launch intent requires a ticker.")
        if not str(self.operation_label or "").strip():
            raise ValueError("A research launch intent requires a readable label.")
        if self.preferred_result_route not in {
            "Decision",
            "Research",
            "Evidence",
            "Audit",
        }:
            raise ValueError("A research launch intent has an invalid result route.")
        object.__setattr__(self, "ticker", ticker)


@dataclass(frozen=True)
class ResearchLaunchResult:
    state: str
    intent: ResearchLaunchIntent
    snapshot: ResearchRunSnapshot | None = None
    created: bool = False
    confirmation_required: bool = False
    message: str = ""


@dataclass(frozen=True)
class ResearchParentReference:
    """Stable lineage for an investigation, repair, or QA continuation."""

    run_id: str = ""
    artifact_id: str = ""
    evidence_hash: str = ""

    @property
    def resumable(self) -> bool:
        return bool(self.run_id)


def resolve_research_parent_reference(
    result: Any | None,
    *,
    saved_artifact: Any | None = None,
    session_run_id: str = "",
    artifact_id: str = "",
    evidence_hash: str = "",
) -> ResearchParentReference:
    """Resolve lineage without depending on presentation-session key order.

    A model run ID is preferred because the research store can use it for a
    bounded resume. Saved-artifact metadata remains a compatible fallback for
    results produced before the hybrid run ID was serialized on the root.
    """

    hybrid = getattr(result, "hybrid_research", None) if result is not None else None
    qa = (
        getattr(result, "independent_research_qa", None)
        if result is not None else None
    )
    manifest = getattr(result, "run_manifest", None) if result is not None else None
    resolved_run_id = str(
        getattr(hybrid, "run_id", "")
        or getattr(qa, "run_id", "")
        or getattr(manifest, "run_id", "")
        or getattr(saved_artifact, "run_id", "")
        or session_run_id
        or ""
    )
    resolved_artifact_id = str(
        artifact_id
        or getattr(saved_artifact, "artifact_id", "")
        or ""
    )
    resolved_evidence_hash = str(
        evidence_hash
        or getattr(qa, "evidence_hash", "")
        or getattr(hybrid, "evidence_hash", "")
        or getattr(manifest, "admitted_evidence_hash", "")
        or getattr(saved_artifact, "evidence_hash", "")
        or ""
    )
    return ResearchParentReference(
        run_id=resolved_run_id,
        artifact_id=resolved_artifact_id,
        evidence_hash=resolved_evidence_hash,
    )


class ResearchLaunchService:
    """Create or reuse one durable attempt for a selected research task."""

    def __init__(self, run_service: ResearchRunService) -> None:
        self.run_service = run_service

    def inspect(self, intent: ResearchLaunchIntent) -> ResearchRunSnapshot | None:
        for fingerprint in (
            intent.operation_fingerprint,
            *intent.compatible_operation_fingerprints,
        ):
            snapshot = self.run_service.find_latest_by_operation_fingerprint(
                fingerprint,
                workspace_scope=intent.workspace_scope,
            )
            if snapshot is not None:
                return snapshot
        return None

    def launch(
        self,
        intent: ResearchLaunchIntent,
        command: ResearchRunCommand,
        *,
        confirm_repeat: bool = False,
        repeat_attempt_id: str = "",
    ) -> ResearchLaunchResult:
        if intent.status is ResearchLaunchIntentStatus.NEEDS_SOURCE_SETUP:
            return ResearchLaunchResult(
                state="source_setup_required",
                intent=intent,
                message=(
                    "Required source setup is incomplete; no research task was created."
                ),
            )
        existing = self.inspect(intent)
        if existing is not None:
            if existing.status in {
                "queued",
                "running",
                "cancelling",
                "waiting_for_credential",
                "research_attempt_finished",
                "validation_pending",
                "repair_required",
                "repair_running",
                "revision_pending",
                "audit_pending",
                "closure_pending",
            }:
                return ResearchLaunchResult(
                    state="reused_active",
                    intent=replace(intent, status=ResearchLaunchIntentStatus.ACTIVE),
                    snapshot=existing,
                    message="The same research task is already active.",
                )
            if not confirm_repeat:
                return ResearchLaunchResult(
                    state="repeat_confirmation_required",
                    intent=intent,
                    snapshot=existing,
                    confirmation_required=True,
                    message="The same research task already has a saved attempt.",
                )
        attempt_id = (
            str(repeat_attempt_id or uuid.uuid4().hex)
            if confirm_repeat
            else intent.intent_id
        )
        frozen_command = replace(
            command,
            operation_fingerprint=intent.operation_fingerprint,
            launch_attempt_id=attempt_id,
            launch_origin=intent.launch_origin,
            parent_artifact_id=intent.parent_artifact_id,
            research_intent_id=intent.research_intent_id,
        )
        snapshot, created = self.run_service.start_or_reuse(frozen_command)
        return ResearchLaunchResult(
            state="created" if created else "reused_active",
            intent=replace(intent, status=ResearchLaunchIntentStatus.LAUNCHING),
            snapshot=snapshot,
            created=created,
            message=(
                "Research task created."
                if created
                else "The same research task is already active."
            ),
        )


def build_research_launch_intent(
    *,
    ticker: str,
    operation_kind: ResearchOperationKind | str,
    operation_label: str,
    objective: str,
    launch_origin: str,
    research_profile: str,
    preferred_result_route: str,
    action_id: str = "",
    action_kind: str = "",
    action_semantic_key: str = "",
    research_intent_id: str = "",
    action_semantic_aliases: tuple[str, ...] = (),
    event_id: str = "",
    analog_id: str = "",
    repair_action_ids: tuple[str, ...] = (),
    repair_retry_stages: tuple[str, ...] = (),
    parent_run_id: str = "",
    parent_artifact_id: str = "",
    parent_evidence_hash: str = "",
    required_source_families: tuple[str, ...] = (),
    preferred_connectors: tuple[str, ...] = (),
    qa_enabled: bool = False,
    workspace_scope: str = "local",
    needs_source_setup: bool = False,
    fingerprint_context: dict[str, Any] | None = None,
) -> ResearchLaunchIntent:
    kind = (
        operation_kind
        if isinstance(operation_kind, ResearchOperationKind)
        else ResearchOperationKind(str(operation_kind))
    )
    fingerprint = build_operation_fingerprint(
        ticker=ticker,
        operation_kind=kind,
        research_profile=research_profile,
        action_semantic_key=action_semantic_key,
        research_intent_id=research_intent_id,
        event_id=event_id,
        analog_id=analog_id,
        repair_action_ids=repair_action_ids,
        repair_retry_stages=repair_retry_stages,
        parent_artifact_id=parent_artifact_id,
        parent_evidence_hash=parent_evidence_hash,
        context=fingerprint_context or {},
    )
    intent_id = "intent-" + hashlib.sha256(
        f"{fingerprint}:{launch_origin}".encode("utf-8")
    ).hexdigest()[:20]
    compatible_fingerprints = tuple(dict.fromkeys(
        build_operation_fingerprint(
            ticker=ticker,
            operation_kind=kind,
            research_profile=research_profile,
            action_semantic_key=alias,
            event_id=event_id,
            analog_id=analog_id,
            repair_action_ids=repair_action_ids,
            repair_retry_stages=repair_retry_stages,
            parent_artifact_id=parent_artifact_id,
            parent_evidence_hash=parent_evidence_hash,
            context=fingerprint_context or {},
        )
        for alias in action_semantic_aliases
        if alias and alias != action_semantic_key
    ))
    return ResearchLaunchIntent(
        intent_id=intent_id,
        ticker=ticker,
        operation_kind=kind,
        operation_label=operation_label,
        objective=objective or operation_label,
        launch_origin=launch_origin,
        research_profile=research_profile,
        preferred_result_route=preferred_result_route,
        status=(
            ResearchLaunchIntentStatus.NEEDS_SOURCE_SETUP
            if needs_source_setup
            else ResearchLaunchIntentStatus.READY
        ),
        operation_fingerprint=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        action_id=action_id,
        action_kind=action_kind,
        action_semantic_key=action_semantic_key,
        research_intent_id=research_intent_id,
        compatible_operation_fingerprints=compatible_fingerprints,
        event_id=event_id,
        analog_id=analog_id,
        repair_action_ids=tuple(repair_action_ids),
        repair_retry_stages=tuple(repair_retry_stages),
        parent_run_id=parent_run_id,
        parent_artifact_id=parent_artifact_id,
        parent_evidence_hash=parent_evidence_hash,
        required_source_families=tuple(required_source_families),
        preferred_connectors=tuple(preferred_connectors),
        qa_enabled=qa_enabled,
        workspace_scope=workspace_scope,
    )


def build_operation_fingerprint(
    *,
    ticker: str,
    operation_kind: ResearchOperationKind | str,
    research_profile: str,
    action_semantic_key: str = "",
    research_intent_id: str = "",
    event_id: str = "",
    analog_id: str = "",
    repair_action_ids: tuple[str, ...] = (),
    repair_retry_stages: tuple[str, ...] = (),
    parent_artifact_id: str = "",
    parent_evidence_hash: str = "",
    context: dict[str, Any] | None = None,
) -> str:
    kind = (
        operation_kind.value
        if isinstance(operation_kind, ResearchOperationKind)
        else str(operation_kind)
    )
    payload: dict[str, Any] = {
        "ticker": str(ticker or "").upper(),
        "operation_kind": kind,
    }
    if kind == ResearchOperationKind.FULL_RESEARCH.value:
        payload.update({
            "research_profile": research_profile,
            "context": _stable_context(context or {}),
        })
    elif kind == ResearchOperationKind.ACTION_INVESTIGATION.value:
        payload.update({
            "research_intent_id": research_intent_id,
            "action_semantic_key": (
                "" if research_intent_id else action_semantic_key
            ),
            "parent_evidence_hash": parent_evidence_hash,
        })
    elif kind == ResearchOperationKind.EVENT_INVESTIGATION.value:
        payload.update({
            "event_id": event_id,
            "parent_evidence_hash": parent_evidence_hash,
        })
    elif kind == ResearchOperationKind.ANALOG_INVESTIGATION.value:
        payload.update({
            "analog_id": analog_id,
            "parent_evidence_hash": parent_evidence_hash,
        })
    elif kind == ResearchOperationKind.REPAIR_RECHECK.value:
        payload.update({
            "parent_artifact_id": parent_artifact_id,
            "repair_action_ids": sorted(str(item) for item in repair_action_ids),
            "repair_retry_stages": sorted(
                str(item) for item in repair_retry_stages
            ),
        })
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "operation-" + hashlib.sha256(encoded).hexdigest()


def _stable_context(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable_context(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if not any(
                token in str(key).casefold()
                for token in ("key", "secret", "token", "credential", "lease")
            )
        }
    if isinstance(value, (set, frozenset)):
        return sorted(
            (_stable_context(item) for item in value),
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    if isinstance(value, (list, tuple)):
        return [_stable_context(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
