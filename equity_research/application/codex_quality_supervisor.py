from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from threading import RLock
from typing import Callable, Optional, Protocol, Sequence, Tuple


class CodexQualityRole(str, Enum):
    FROZEN_RESEARCH_QUALITY = "frozen_research_quality"
    ADVERSARIAL_SCOUT = "adversarial_scout"
    CONDITIONAL_SPECIALIST = "conditional_specialist"
    CLOSURE_CHECKER = "closure_checker"
    UI_JOURNEY = "ui_journey"
    TECHNICAL_DEBUGGER = "technical_debugger"


class QualityTrigger(str, Enum):
    RESEARCH_COMPLETED = "research_completed"
    RESEARCH_ARTIFACT_CHANGED = "research_artifact_changed"
    UI_CHANGED = "ui_changed"
    TEST_FAILURE = "test_failure"
    SAVED_RESULT_COMPATIBILITY_FAILURE = "saved_result_compatibility_failure"
    DEMO_CANDIDATE = "demo_candidate"
    RELEASE_CANDIDATE = "release_candidate"
    MANUAL = "manual"


class QualityTriggerMode(str, Enum):
    HYBRID = "hybrid"
    RELEASE_ONLY = "release_only"
    ALWAYS = "always"
    MANUAL = "manual"


class AuditBundleKind(str, Enum):
    FROZEN_RESEARCH_ARTIFACT = "frozen_research_artifact"
    REVISED_RESEARCH_ARTIFACT = "revised_research_artifact"
    ADMITTED_EVIDENCE_DELTA = "admitted_evidence_delta"
    PRIMARY_RESPONSES = "primary_responses"
    UI_PRESENTATION_SNAPSHOT = "ui_presentation_snapshot"
    TECHNICAL_DIAGNOSTICS = "technical_diagnostics"


class FindingSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(str, Enum):
    RESEARCH_QUALITY = "research_quality"
    EVIDENCE_INTEGRITY = "evidence_integrity"
    UI_READABILITY = "ui_readability"
    ACCESSIBILITY = "accessibility"
    WORKFLOW = "workflow"
    COMPATIBILITY = "compatibility"
    RUNTIME = "runtime"
    SECURITY = "security"
    PERFORMANCE = "performance"


class FindingStatus(str, Enum):
    OPEN = "open"
    FIXED = "fixed"
    DEFERRED = "deferred"
    FALSE_POSITIVE = "false_positive"


class RoleReportStatus(str, Enum):
    PASSED = "passed"
    ATTENTION_REQUIRED = "attention_required"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class RoleCheckpointStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class QualityRunStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class QualityAuditBundleReference:
    bundle_id: str
    kind: AuditBundleKind
    run_id: str
    artifact_id: str
    evidence_hash: str
    payload_reference: str
    created_at: str
    schema_version: str = ""
    result_version: str = ""
    source_policy_fingerprint: str = ""
    parent_bundle_id: str = ""
    sanitized: bool = True
    secret_scan_passed: bool = True
    licensed_content_policy_passed: bool = True
    bundle_version: str = "quality-audit-bundle-v1"

    def __post_init__(self) -> None:
        if not self.bundle_id.strip():
            raise ValueError("An audit bundle reference requires a bundle ID.")
        if not self.run_id.strip():
            raise ValueError("An audit bundle reference requires a run ID.")
        if not self.payload_reference.strip():
            raise ValueError("An audit bundle reference requires an opaque payload reference.")
        if not self.sanitized or not self.secret_scan_passed:
            raise ValueError("Codex quality roles accept only sanitized, secret-scanned bundles.")
        if not self.licensed_content_policy_passed:
            raise ValueError("The audit bundle did not pass licensed-content policy.")


@dataclass(frozen=True)
class ConditionalSpecialistRequest:
    scope: str
    finding_ids: Tuple[str, ...] = ()
    materiality: FindingSeverity = FindingSeverity.HIGH
    permitted: bool = True

    def __post_init__(self) -> None:
        if not self.scope.strip():
            raise ValueError("A conditional specialist requires a bounded scope.")

    @property
    def eligible(self) -> bool:
        return self.permitted and self.materiality in {
            FindingSeverity.CRITICAL,
            FindingSeverity.HIGH,
        }


@dataclass(frozen=True)
class QualityFinding:
    finding_id: str
    role: CodexQualityRole
    severity: FindingSeverity
    category: FindingCategory
    code: str
    title: str
    summary: str
    user_impact: str
    status: FindingStatus = FindingStatus.OPEN
    route: str = ""
    reproduction_steps: Tuple[str, ...] = ()
    acceptance_criteria: Tuple[str, ...] = ()
    affected_bundle_ids: Tuple[str, ...] = ()
    related_claim_ids: Tuple[str, ...] = ()
    admitted_evidence_ids: Tuple[str, ...] = ()
    candidate_ids: Tuple[str, ...] = ()
    fixture_hint: str = ""
    finding_version: str = "quality-finding-v1"

    def __post_init__(self) -> None:
        if not self.finding_id.strip() or not self.code.strip():
            raise ValueError("A quality finding requires stable finding and code values.")
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("A quality finding requires a readable title and summary.")
        if not self.user_impact.strip():
            raise ValueError("A quality finding requires a readable user impact.")


@dataclass(frozen=True)
class CodexRoleReport:
    report_id: str
    supervision_run_id: str
    checkpoint_id: str
    role_key: str
    role: CodexQualityRole
    status: RoleReportStatus
    summary: str
    started_at: str
    completed_at: str
    bundle_ids: Tuple[str, ...]
    findings: Tuple[QualityFinding, ...] = ()
    specialist_scope: str = ""
    checks_passed: int = 0
    checks_total: int = 0
    fixture_recommendations: Tuple[str, ...] = ()
    blocking_reason: str = ""
    report_version: str = "codex-role-report-v1"

    def __post_init__(self) -> None:
        if not self.report_id.strip() or not self.supervision_run_id.strip():
            raise ValueError("A Codex role report requires stable report and run IDs.")
        if not self.checkpoint_id.strip() or not self.role_key.strip():
            raise ValueError("A Codex role report requires a checkpoint and role key.")
        if not self.summary.strip():
            raise ValueError("A Codex role report requires a readable summary.")
        if self.checks_passed < 0 or self.checks_total < self.checks_passed:
            raise ValueError("Codex role report check counts are inconsistent.")
        for finding in self.findings:
            if finding.role is not self.role:
                raise ValueError("Every finding must be attributed to the report's role.")


@dataclass(frozen=True)
class CodexRoleCheckpoint:
    checkpoint_id: str
    role_key: str
    role: CodexQualityRole
    status: RoleCheckpointStatus
    dependency_checkpoint_ids: Tuple[str, ...] = ()
    specialist_scope: str = ""
    scoped_finding_ids: Tuple[str, ...] = ()
    attempt: int = 1
    started_at: str = ""
    completed_at: str = ""
    report_id: str = ""
    error_summary: str = ""
    retryable: bool = False


@dataclass(frozen=True)
class CodexQualityRun:
    run_id: str
    trigger: QualityTrigger
    trigger_policy: QualityTriggerMode
    status: QualityRunStatus
    primary_bundle_id: str
    bundle_references: Tuple[QualityAuditBundleReference, ...]
    checkpoints: Tuple[CodexRoleCheckpoint, ...]
    created_at: str
    updated_at: str
    policy_version: str = "codex-quality-trigger-policy-v1"
    run_version: str = "codex-quality-run-v1"


@dataclass(frozen=True)
class CodexRoleInput:
    supervision_run_id: str
    checkpoint_id: str
    role: CodexQualityRole
    role_key: str
    bundle_references: Tuple[QualityAuditBundleReference, ...]
    prior_report_ids: Tuple[str, ...]
    scoped_finding_ids: Tuple[str, ...] = ()
    tool_profile: str = "read_only"
    frozen_audit_independent: bool = False
    may_view_candidate_metadata: bool = False
    may_propose_candidates: bool = False
    may_admit_evidence: bool = False
    repository_write_access: bool = False
    unrestricted_shell_access: bool = False
    unrestricted_network_access: bool = False
    input_version: str = "codex-role-input-v1"

    def __post_init__(self) -> None:
        if self.may_admit_evidence or self.repository_write_access:
            raise ValueError("Codex quality roles cannot admit evidence or write repositories.")
        if self.unrestricted_shell_access or self.unrestricted_network_access:
            raise ValueError("Codex quality roles must use bounded application-owned tools.")
        if self.role is CodexQualityRole.FROZEN_RESEARCH_QUALITY:
            if self.prior_report_ids or len(self.bundle_references) != 1:
                raise ValueError("The frozen research-quality audit must remain independent.")
            if self.bundle_references[0].kind is not AuditBundleKind.FROZEN_RESEARCH_ARTIFACT:
                raise ValueError("The frozen audit requires exactly the frozen research artifact.")


@dataclass(frozen=True)
class CodexQualityTriggerPolicy:
    mode: QualityTriggerMode = QualityTriggerMode.HYBRID
    include_adversarial_scout: bool = True
    include_closure_checker: bool = True
    policy_version: str = "codex-quality-trigger-policy-v1"

    def roles_for(
        self,
        trigger: QualityTrigger,
        explicit_roles: Sequence[CodexQualityRole] = (),
    ) -> Tuple[CodexQualityRole, ...]:
        explicit = tuple(dict.fromkeys(explicit_roles))
        if trigger is QualityTrigger.MANUAL or self.mode is QualityTriggerMode.MANUAL:
            return explicit
        if self.mode is QualityTriggerMode.RELEASE_ONLY and trigger not in {
            QualityTrigger.DEMO_CANDIDATE,
            QualityTrigger.RELEASE_CANDIDATE,
        }:
            return ()

        core = [CodexQualityRole.FROZEN_RESEARCH_QUALITY]
        if self.include_adversarial_scout:
            core.append(CodexQualityRole.ADVERSARIAL_SCOUT)
        if self.include_closure_checker:
            core.append(CodexQualityRole.CLOSURE_CHECKER)

        if self.mode is QualityTriggerMode.ALWAYS:
            return tuple(dict.fromkeys([
                *core,
                CodexQualityRole.UI_JOURNEY,
                CodexQualityRole.TECHNICAL_DEBUGGER,
                *explicit,
            ]))
        if trigger in {
            QualityTrigger.RESEARCH_COMPLETED,
            QualityTrigger.RESEARCH_ARTIFACT_CHANGED,
        }:
            return tuple(dict.fromkeys([*core, *explicit]))
        if trigger is QualityTrigger.UI_CHANGED:
            return (CodexQualityRole.UI_JOURNEY,)
        if trigger in {
            QualityTrigger.TEST_FAILURE,
            QualityTrigger.SAVED_RESULT_COMPATIBILITY_FAILURE,
        }:
            return (CodexQualityRole.TECHNICAL_DEBUGGER,)
        if trigger in {
            QualityTrigger.DEMO_CANDIDATE,
            QualityTrigger.RELEASE_CANDIDATE,
        }:
            return tuple(dict.fromkeys([
                *core,
                CodexQualityRole.UI_JOURNEY,
                CodexQualityRole.TECHNICAL_DEBUGGER,
                *explicit,
            ]))
        return explicit


class CodexQualityRepository(Protocol):
    def create_if_absent(
        self,
        run: CodexQualityRun,
    ) -> Tuple[CodexQualityRun, bool]: ...

    def get_run(self, run_id: str) -> Optional[CodexQualityRun]: ...

    def save_run(self, run: CodexQualityRun) -> None: ...

    def save_report(self, report: CodexRoleReport) -> CodexRoleReport: ...

    def get_report(self, report_id: str) -> Optional[CodexRoleReport]: ...

    def reports_for_run(self, run_id: str) -> Tuple[CodexRoleReport, ...]: ...


class InMemoryCodexQualityRepository:
    def __init__(self) -> None:
        self._runs = {}  # type: dict[str, CodexQualityRun]
        self._reports = {}  # type: dict[str, CodexRoleReport]
        self._lock = RLock()

    def create_if_absent(
        self,
        run: CodexQualityRun,
    ) -> Tuple[CodexQualityRun, bool]:
        with self._lock:
            existing = self._runs.get(run.run_id)
            if existing is not None:
                return existing, False
            self._runs[run.run_id] = run
            return run, True

    def get_run(self, run_id: str) -> Optional[CodexQualityRun]:
        with self._lock:
            return self._runs.get(run_id)

    def save_run(self, run: CodexQualityRun) -> None:
        with self._lock:
            if run.run_id not in self._runs:
                raise KeyError("Unknown Codex quality run: " + run.run_id)
            self._runs[run.run_id] = run

    def save_report(self, report: CodexRoleReport) -> CodexRoleReport:
        with self._lock:
            existing = self._reports.get(report.report_id)
            if existing is not None and existing != report:
                raise ValueError("A report ID cannot be reused for different content.")
            self._reports.setdefault(report.report_id, report)
            return self._reports[report.report_id]

    def get_report(self, report_id: str) -> Optional[CodexRoleReport]:
        with self._lock:
            return self._reports.get(report_id)

    def reports_for_run(self, run_id: str) -> Tuple[CodexRoleReport, ...]:
        with self._lock:
            return tuple(
                report for report in self._reports.values()
                if report.supervision_run_id == run_id
            )


class CodexQualitySupervisor:
    """Idempotent orchestration for isolated Codex quality roles.

    This service schedules and records roles; a separate adapter may invoke
    Codex. The service never grants evidence-admission or repository-write
    authority to a role.
    """

    def __init__(
        self,
        repository: CodexQualityRepository,
        policy: Optional[CodexQualityTriggerPolicy] = None,
        now: Optional[Callable[[], str]] = None,
    ) -> None:
        self.repository = repository
        self.policy = policy or CodexQualityTriggerPolicy()
        self._now = now or _utc_now

    def start_or_resume(
        self,
        primary_bundle: QualityAuditBundleReference,
        *,
        trigger: QualityTrigger,
        supplementary_bundles: Sequence[QualityAuditBundleReference] = (),
        specialist_requests: Sequence[ConditionalSpecialistRequest] = (),
        explicit_roles: Sequence[CodexQualityRole] = (),
    ) -> Tuple[CodexQualityRun, bool]:
        if primary_bundle.kind is not AuditBundleKind.FROZEN_RESEARCH_ARTIFACT:
            raise ValueError("The primary Codex quality bundle must be a frozen artifact.")
        bundles = tuple(dict.fromkeys((primary_bundle, *supplementary_bundles)))
        if any(bundle.run_id != primary_bundle.run_id for bundle in bundles):
            raise ValueError("All quality bundles must belong to the same research run.")
        roles = self.policy.roles_for(trigger, explicit_roles)
        eligible_specialists = tuple(
            item for item in specialist_requests if item.eligible
        )
        run_id = _stable_id("codex-quality-run", {
            "primary_bundle_id": primary_bundle.bundle_id,
            "primary_evidence_hash": primary_bundle.evidence_hash,
            "trigger": trigger.value,
            "policy_version": self.policy.policy_version,
            "roles": [role.value for role in roles],
            "specialists": [
                {
                    "scope": item.scope,
                    "finding_ids": sorted(item.finding_ids),
                    "materiality": item.materiality.value,
                }
                for item in eligible_specialists
            ],
        })
        now = self._now()
        checkpoints = _build_checkpoints(run_id, roles, eligible_specialists)
        run = CodexQualityRun(
            run_id=run_id,
            trigger=trigger,
            trigger_policy=self.policy.mode,
            status=(
                QualityRunStatus.COMPLETED
                if not checkpoints else QualityRunStatus.PENDING
            ),
            primary_bundle_id=primary_bundle.bundle_id,
            bundle_references=bundles,
            checkpoints=checkpoints,
            created_at=now,
            updated_at=now,
            policy_version=self.policy.policy_version,
        )
        return self.repository.create_if_absent(run)

    def inspect(self, run_id: str) -> CodexQualityRun:
        run = self.repository.get_run(run_id)
        if run is None:
            raise KeyError("Unknown Codex quality run: " + run_id)
        return run

    def ready_checkpoints(self, run_id: str) -> Tuple[CodexRoleCheckpoint, ...]:
        run = self.inspect(run_id)
        completed = {
            item.checkpoint_id for item in run.checkpoints
            if item.status in {
                RoleCheckpointStatus.COMPLETED,
                RoleCheckpointStatus.SKIPPED,
            }
        }
        return tuple(
            item for item in run.checkpoints
            if item.status is RoleCheckpointStatus.PENDING
            and set(item.dependency_checkpoint_ids).issubset(completed)
        )

    def claim_checkpoint(
        self,
        run_id: str,
        checkpoint_id: str = "",
    ) -> CodexRoleCheckpoint:
        run = self.inspect(run_id)
        ready = self.ready_checkpoints(run_id)
        selected = next(
            (
                item for item in ready
                if not checkpoint_id or item.checkpoint_id == checkpoint_id
            ),
            None,
        )
        if selected is None:
            existing = next(
                (item for item in run.checkpoints if item.checkpoint_id == checkpoint_id),
                None,
            )
            if existing is not None and existing.status is RoleCheckpointStatus.RUNNING:
                return existing
            raise ValueError("The requested Codex quality checkpoint is not ready.")
        updated = replace(
            selected,
            status=RoleCheckpointStatus.RUNNING,
            started_at=selected.started_at or self._now(),
            error_summary="",
            retryable=False,
        )
        self._save_checkpoint(run, updated)
        return updated

    def role_input(self, run_id: str, checkpoint_id: str) -> CodexRoleInput:
        run = self.inspect(run_id)
        checkpoint = _checkpoint(run, checkpoint_id)
        reports = {
            report.checkpoint_id: report
            for report in self.repository.reports_for_run(run_id)
        }
        dependency_ids = _transitive_dependency_ids(run, checkpoint)
        prior_report_ids = tuple(
            reports[item].report_id
            for item in dependency_ids
            if item in reports
        )
        bundles = _bundles_for_role(run, checkpoint.role)
        profile = {
            CodexQualityRole.FROZEN_RESEARCH_QUALITY: "read_only_frozen_artifact",
            CodexQualityRole.ADVERSARIAL_SCOUT: "app_owned_research_tools",
            CodexQualityRole.CONDITIONAL_SPECIALIST: "bounded_specialist_tools",
            CodexQualityRole.CLOSURE_CHECKER: "read_only_closure",
            CodexQualityRole.UI_JOURNEY: "read_only_ui_browser",
            CodexQualityRole.TECHNICAL_DEBUGGER: "sanitized_diagnostics",
        }[checkpoint.role]
        may_propose = checkpoint.role in {
            CodexQualityRole.ADVERSARIAL_SCOUT,
            CodexQualityRole.CONDITIONAL_SPECIALIST,
        }
        return CodexRoleInput(
            supervision_run_id=run_id,
            checkpoint_id=checkpoint_id,
            role=checkpoint.role,
            role_key=checkpoint.role_key,
            bundle_references=bundles,
            prior_report_ids=prior_report_ids,
            scoped_finding_ids=checkpoint.scoped_finding_ids,
            tool_profile=profile,
            frozen_audit_independent=(
                checkpoint.role is CodexQualityRole.FROZEN_RESEARCH_QUALITY
            ),
            may_view_candidate_metadata=may_propose,
            may_propose_candidates=may_propose,
        )

    def complete_checkpoint(
        self,
        run_id: str,
        report: CodexRoleReport,
    ) -> CodexQualityRun:
        run = self.inspect(run_id)
        checkpoint = _checkpoint(run, report.checkpoint_id)
        if report.supervision_run_id != run_id:
            raise ValueError("The role report belongs to a different supervision run.")
        if report.role is not checkpoint.role or report.role_key != checkpoint.role_key:
            raise ValueError("The role report does not match its checkpoint.")
        if checkpoint.status is RoleCheckpointStatus.COMPLETED:
            if checkpoint.report_id == report.report_id:
                return run
            raise ValueError("A completed checkpoint cannot be replaced by another report.")
        if checkpoint.status is not RoleCheckpointStatus.RUNNING:
            raise ValueError("A checkpoint must be running before it can complete.")
        allowed_bundle_ids = {
            item.bundle_id for item in self.role_input(run_id, checkpoint.checkpoint_id).bundle_references
        }
        if not set(report.bundle_ids).issubset(allowed_bundle_ids):
            raise ValueError("The report references a bundle unavailable to this role.")
        self.repository.save_report(report)
        completed = replace(
            checkpoint,
            status=RoleCheckpointStatus.COMPLETED,
            completed_at=report.completed_at or self._now(),
            report_id=report.report_id,
            error_summary="",
            retryable=False,
        )
        return self._save_checkpoint(run, completed)

    def fail_checkpoint(
        self,
        run_id: str,
        checkpoint_id: str,
        *,
        error_summary: str,
        retryable: bool,
    ) -> CodexQualityRun:
        run = self.inspect(run_id)
        checkpoint = _checkpoint(run, checkpoint_id)
        if checkpoint.status is RoleCheckpointStatus.FAILED:
            return run
        if checkpoint.status is not RoleCheckpointStatus.RUNNING:
            raise ValueError("Only a running checkpoint may fail.")
        failed = replace(
            checkpoint,
            status=RoleCheckpointStatus.FAILED,
            completed_at=self._now(),
            error_summary=error_summary.strip() or "Codex quality role failed.",
            retryable=retryable,
        )
        return self._save_checkpoint(run, failed)

    def restart_checkpoint(
        self,
        run_id: str,
        checkpoint_id: str,
    ) -> CodexQualityRun:
        run = self.inspect(run_id)
        checkpoint = _checkpoint(run, checkpoint_id)
        if checkpoint.status is RoleCheckpointStatus.COMPLETED:
            return run
        if checkpoint.status is not RoleCheckpointStatus.FAILED or not checkpoint.retryable:
            raise ValueError("Only a retryable failed checkpoint may restart.")
        restarted = replace(
            checkpoint,
            status=RoleCheckpointStatus.PENDING,
            attempt=checkpoint.attempt + 1,
            started_at="",
            completed_at="",
            report_id="",
            error_summary="",
            retryable=False,
        )
        return self._save_checkpoint(run, restarted)

    def _save_checkpoint(
        self,
        run: CodexQualityRun,
        checkpoint: CodexRoleCheckpoint,
    ) -> CodexQualityRun:
        checkpoints = tuple(
            checkpoint if item.checkpoint_id == checkpoint.checkpoint_id else item
            for item in run.checkpoints
        )
        updated = replace(
            run,
            checkpoints=checkpoints,
            status=_run_status(checkpoints),
            updated_at=self._now(),
        )
        self.repository.save_run(updated)
        return updated


def build_role_report(
    *,
    role_input: CodexRoleInput,
    status: RoleReportStatus,
    summary: str,
    started_at: str,
    completed_at: str,
    findings: Sequence[QualityFinding] = (),
    specialist_scope: str = "",
    checks_passed: int = 0,
    checks_total: int = 0,
    fixture_recommendations: Sequence[str] = (),
    blocking_reason: str = "",
) -> CodexRoleReport:
    payload = {
        "run_id": role_input.supervision_run_id,
        "checkpoint_id": role_input.checkpoint_id,
        "role_key": role_input.role_key,
        "bundle_ids": [item.bundle_id for item in role_input.bundle_references],
        "finding_ids": [item.finding_id for item in findings],
        "status": status.value,
    }
    return CodexRoleReport(
        report_id=_stable_id("codex-role-report", payload),
        supervision_run_id=role_input.supervision_run_id,
        checkpoint_id=role_input.checkpoint_id,
        role_key=role_input.role_key,
        role=role_input.role,
        status=status,
        summary=summary,
        started_at=started_at,
        completed_at=completed_at,
        bundle_ids=tuple(item.bundle_id for item in role_input.bundle_references),
        findings=tuple(findings),
        specialist_scope=specialist_scope,
        checks_passed=checks_passed,
        checks_total=checks_total,
        fixture_recommendations=tuple(fixture_recommendations),
        blocking_reason=blocking_reason,
    )


def _build_checkpoints(
    run_id: str,
    roles: Sequence[CodexQualityRole],
    specialist_requests: Sequence[ConditionalSpecialistRequest],
) -> Tuple[CodexRoleCheckpoint, ...]:
    rows = []  # type: list[CodexRoleCheckpoint]
    by_role = {}  # type: dict[CodexQualityRole, str]

    for role in roles:
        if role is CodexQualityRole.CONDITIONAL_SPECIALIST:
            continue
        role_key = role.value
        dependencies = ()  # type: Tuple[str, ...]
        if role is CodexQualityRole.ADVERSARIAL_SCOUT:
            audit_id = by_role.get(CodexQualityRole.FROZEN_RESEARCH_QUALITY, "")
            dependencies = (audit_id,) if audit_id else ()
        elif role is CodexQualityRole.CLOSURE_CHECKER:
            dependencies = tuple(
                item.checkpoint_id for item in rows
                if item.role in {
                    CodexQualityRole.FROZEN_RESEARCH_QUALITY,
                    CodexQualityRole.ADVERSARIAL_SCOUT,
                    CodexQualityRole.CONDITIONAL_SPECIALIST,
                }
            )
        checkpoint_id = _stable_id("codex-role-checkpoint", {
            "run_id": run_id,
            "role_key": role_key,
        })
        checkpoint = CodexRoleCheckpoint(
            checkpoint_id=checkpoint_id,
            role_key=role_key,
            role=role,
            status=RoleCheckpointStatus.PENDING,
            dependency_checkpoint_ids=dependencies,
        )
        rows.append(checkpoint)
        by_role[role] = checkpoint_id

        if role is CodexQualityRole.ADVERSARIAL_SCOUT:
            for request in specialist_requests:
                specialist_key = (
                    CodexQualityRole.CONDITIONAL_SPECIALIST.value
                    + ":" + _slug(request.scope)
                )
                specialist_id = _stable_id("codex-role-checkpoint", {
                    "run_id": run_id,
                    "role_key": specialist_key,
                    "finding_ids": sorted(request.finding_ids),
                })
                rows.append(CodexRoleCheckpoint(
                    checkpoint_id=specialist_id,
                    role_key=specialist_key,
                    role=CodexQualityRole.CONDITIONAL_SPECIALIST,
                    status=RoleCheckpointStatus.PENDING,
                    dependency_checkpoint_ids=(checkpoint_id,),
                    specialist_scope=request.scope,
                    scoped_finding_ids=tuple(request.finding_ids),
                ))

    closure_index = next(
        (
            index for index, item in enumerate(rows)
            if item.role is CodexQualityRole.CLOSURE_CHECKER
        ),
        -1,
    )
    if closure_index >= 0:
        closure = rows[closure_index]
        dependencies = tuple(
            item.checkpoint_id for item in rows
            if item.role in {
                CodexQualityRole.FROZEN_RESEARCH_QUALITY,
                CodexQualityRole.ADVERSARIAL_SCOUT,
                CodexQualityRole.CONDITIONAL_SPECIALIST,
            }
        )
        rows[closure_index] = replace(
            closure,
            dependency_checkpoint_ids=dependencies,
        )
    return tuple(rows)


def _bundles_for_role(
    run: CodexQualityRun,
    role: CodexQualityRole,
) -> Tuple[QualityAuditBundleReference, ...]:
    primary = next(
        item for item in run.bundle_references
        if item.bundle_id == run.primary_bundle_id
    )
    if role is CodexQualityRole.FROZEN_RESEARCH_QUALITY:
        return (primary,)
    allowed = {
        CodexQualityRole.ADVERSARIAL_SCOUT: {
            AuditBundleKind.FROZEN_RESEARCH_ARTIFACT,
            AuditBundleKind.ADMITTED_EVIDENCE_DELTA,
        },
        CodexQualityRole.CONDITIONAL_SPECIALIST: {
            AuditBundleKind.FROZEN_RESEARCH_ARTIFACT,
            AuditBundleKind.ADMITTED_EVIDENCE_DELTA,
        },
        CodexQualityRole.CLOSURE_CHECKER: {
            AuditBundleKind.FROZEN_RESEARCH_ARTIFACT,
            AuditBundleKind.REVISED_RESEARCH_ARTIFACT,
            AuditBundleKind.ADMITTED_EVIDENCE_DELTA,
            AuditBundleKind.PRIMARY_RESPONSES,
        },
        CodexQualityRole.UI_JOURNEY: {
            AuditBundleKind.UI_PRESENTATION_SNAPSHOT,
        },
        CodexQualityRole.TECHNICAL_DEBUGGER: {
            AuditBundleKind.TECHNICAL_DIAGNOSTICS,
        },
    }[role]
    selected = tuple(
        item for item in run.bundle_references if item.kind in allowed
    )
    return selected or (primary,)


def _checkpoint(run: CodexQualityRun, checkpoint_id: str) -> CodexRoleCheckpoint:
    checkpoint = next(
        (item for item in run.checkpoints if item.checkpoint_id == checkpoint_id),
        None,
    )
    if checkpoint is None:
        raise KeyError("Unknown Codex quality checkpoint: " + checkpoint_id)
    return checkpoint


def _transitive_dependency_ids(
    run: CodexQualityRun,
    checkpoint: CodexRoleCheckpoint,
) -> Tuple[str, ...]:
    by_id = {item.checkpoint_id: item for item in run.checkpoints}
    required = set()  # type: set[str]

    def collect(checkpoint_id: str) -> None:
        if checkpoint_id in required:
            return
        required.add(checkpoint_id)
        dependency = by_id.get(checkpoint_id)
        if dependency is not None:
            for parent_id in dependency.dependency_checkpoint_ids:
                collect(parent_id)

    for dependency_id in checkpoint.dependency_checkpoint_ids:
        collect(dependency_id)
    return tuple(
        item.checkpoint_id for item in run.checkpoints
        if item.checkpoint_id in required
    )


def _run_status(
    checkpoints: Sequence[CodexRoleCheckpoint],
) -> QualityRunStatus:
    if not checkpoints:
        return QualityRunStatus.COMPLETED
    if any(
        item.status is RoleCheckpointStatus.FAILED and not item.retryable
        for item in checkpoints
    ):
        return QualityRunStatus.FAILED
    if any(
        item.status is RoleCheckpointStatus.FAILED and item.retryable
        for item in checkpoints
    ):
        return QualityRunStatus.PAUSED
    if all(
        item.status in {
            RoleCheckpointStatus.COMPLETED,
            RoleCheckpointStatus.SKIPPED,
        }
        for item in checkpoints
    ):
        return QualityRunStatus.COMPLETED
    if any(
        item.status in {
            RoleCheckpointStatus.RUNNING,
            RoleCheckpointStatus.COMPLETED,
            RoleCheckpointStatus.SKIPPED,
        }
        for item in checkpoints
    ):
        return QualityRunStatus.ACTIVE
    return QualityRunStatus.PENDING


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return prefix + "-" + hashlib.sha256(encoded).hexdigest()[:20]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "specialist"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
