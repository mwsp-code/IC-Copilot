from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Tuple
from urllib.error import URLError

from ..application.codex_quality_supervisor import (
    CodexQualityRole,
    CodexQualityRun,
    CodexQualitySupervisor,
    CodexRoleCheckpoint,
    CodexRoleInput,
    CodexRoleReport,
    FindingCategory,
    FindingSeverity,
    FindingStatus,
    QualityAuditBundleReference,
    QualityFinding,
    RoleCheckpointStatus,
    RoleReportStatus,
    build_role_report,
)
from ..secret_safety import redact_sensitive_text, redact_sensitive_value


QUALITY_EXECUTOR_VERSION = "codex-quality-executor-v1"


class JsonQualityProvider(Protocol):
    provider_name: str
    model: str

    def complete_json(self, prompt_pack: dict) -> dict: ...


class SanitizedQualityBundleLoader(Protocol):
    def load_bundle(
        self,
        reference: QualityAuditBundleReference,
    ) -> Mapping[str, Any]: ...


class QualityProviderFactory(Protocol):
    def __call__(self, role_input: CodexRoleInput) -> JsonQualityProvider: ...


class CodexQualityOutputError(ValueError):
    pass


@dataclass(frozen=True)
class CodexQualityExecutionResult:
    run: CodexQualityRun
    completed_checkpoint_ids: Tuple[str, ...] = ()
    completed_report_ids: Tuple[str, ...] = ()
    failed_checkpoint_ids: Tuple[str, ...] = ()
    stopped_reason: str = ""
    executor_version: str = QUALITY_EXECUTOR_VERSION


class CodexQualityExecutor:
    """Execute isolated quality checkpoints through one JSON call per role.

    Provider and storage implementations are injected. The executor exposes
    only sanitized bundle payloads and prior structured reports; it never
    grants model-side evidence admission, repository writes, an unrestricted
    shell, or unrestricted network access.
    """

    def __init__(
        self,
        supervisor: CodexQualitySupervisor,
        *,
        provider_factory: QualityProviderFactory,
        bundle_loader: SanitizedQualityBundleLoader,
        now: Optional[Callable[[], str]] = None,
    ) -> None:
        self.supervisor = supervisor
        self.provider_factory = provider_factory
        self.bundle_loader = bundle_loader
        self._now = now or _utc_now

    def execute_ready(
        self,
        run_id: str,
        *,
        retry_failed: bool = False,
        max_checkpoints: Optional[int] = None,
    ) -> CodexQualityExecutionResult:
        if max_checkpoints is not None and max_checkpoints < 1:
            raise ValueError("max_checkpoints must be positive when provided.")
        run = self.supervisor.inspect(run_id)
        if retry_failed:
            for checkpoint in run.checkpoints:
                if (
                    checkpoint.status is RoleCheckpointStatus.FAILED
                    and checkpoint.retryable
                ):
                    run = self.supervisor.restart_checkpoint(
                        run_id,
                        checkpoint.checkpoint_id,
                    )

        completed_checkpoints = []  # type: list[str]
        completed_reports = []  # type: list[str]
        failed_checkpoints = []  # type: list[str]
        stopped_reason = ""

        while max_checkpoints is None or len(completed_checkpoints) < max_checkpoints:
            run = self.supervisor.inspect(run_id)
            checkpoint = next(
                (
                    item for item in run.checkpoints
                    if item.status is RoleCheckpointStatus.RUNNING
                ),
                None,
            )
            if checkpoint is None:
                ready = self.supervisor.ready_checkpoints(run_id)
                if not ready:
                    stopped_reason = _stopping_reason(run)
                    break
                checkpoint = self.supervisor.claim_checkpoint(
                    run_id,
                    ready[0].checkpoint_id,
                )

            try:
                report = self._execute_checkpoint(run_id, checkpoint)
                run = self.supervisor.complete_checkpoint(run_id, report)
                completed_checkpoints.append(checkpoint.checkpoint_id)
                completed_reports.append(report.report_id)
            except Exception as exc:
                retryable = _is_retryable_provider_failure(exc)
                run = self.supervisor.fail_checkpoint(
                    run_id,
                    checkpoint.checkpoint_id,
                    error_summary=_safe_text(exc, 1_000),
                    retryable=retryable,
                )
                failed_checkpoints.append(checkpoint.checkpoint_id)
                stopped_reason = (
                    "retryable_provider_failure"
                    if retryable else "role_execution_failed"
                )
                break

        return CodexQualityExecutionResult(
            run=self.supervisor.inspect(run_id),
            completed_checkpoint_ids=tuple(completed_checkpoints),
            completed_report_ids=tuple(completed_reports),
            failed_checkpoint_ids=tuple(failed_checkpoints),
            stopped_reason=stopped_reason,
        )

    def _execute_checkpoint(
        self,
        run_id: str,
        checkpoint: CodexRoleCheckpoint,
    ) -> CodexRoleReport:
        role_input = self.supervisor.role_input(run_id, checkpoint.checkpoint_id)
        prompt = self._prompt_pack(role_input, checkpoint)
        provider = self.provider_factory(role_input)
        response = provider.complete_json(prompt)
        return _report_from_payload(
            response,
            role_input=role_input,
            checkpoint=checkpoint,
            started_at=checkpoint.started_at or self._now(),
            completed_at=self._now(),
        )

    def _prompt_pack(
        self,
        role_input: CodexRoleInput,
        checkpoint: CodexRoleCheckpoint,
    ) -> dict:
        bundles = []
        for reference in role_input.bundle_references:
            payload = self.bundle_loader.load_bundle(reference)
            if not isinstance(payload, Mapping):
                raise CodexQualityOutputError(
                    "The sanitized bundle loader returned a non-mapping payload."
                )
            bundles.append({
                "bundle": _bundle_metadata(reference),
                "content": _safe_payload(payload),
            })
        prior_reports = []
        for report_id in role_input.prior_report_ids:
            report = self.supervisor.repository.get_report(report_id)
            if report is None:
                raise CodexQualityOutputError(
                    "A required prior quality report is unavailable."
                )
            prior_reports.append(_safe_payload(asdict(report)))

        call_id = _stable_id("codex-quality-call", {
            "checkpoint_id": checkpoint.checkpoint_id,
            "attempt": checkpoint.attempt,
            "bundle_ids": [item.bundle_id for item in role_input.bundle_references],
        })
        prompt = {
            "_output_schema": _output_schema(),
            "executor_version": QUALITY_EXECUTOR_VERSION,
            "idempotency_key": call_id,
            "role": role_input.role.value,
            "role_key": role_input.role_key,
            "specialist_scope": checkpoint.specialist_scope,
            "scoped_finding_ids": list(role_input.scoped_finding_ids),
            "quality_contract": {
                "objective": _role_objective(role_input.role),
                "fresh_context": True,
                "concise_reasoning_only": True,
                "candidate_rule": (
                    "Candidate metadata may be proposed but cannot support a conclusion."
                    if role_input.may_propose_candidates
                    else "Do not propose or rely on candidate metadata in this role."
                ),
                "evidence_rule": (
                    "You may reference existing admitted evidence IDs present in the input. "
                    "You cannot create or admit evidence IDs."
                ),
                "fixture_rule": (
                    "Recommend a sanitized deterministic fixture for every reproducible failure."
                ),
            },
            "permissions": {
                "tool_profile": role_input.tool_profile,
                "may_view_candidate_metadata": role_input.may_view_candidate_metadata,
                "may_propose_candidates": role_input.may_propose_candidates,
                "may_admit_evidence": False,
                "repository_write_access": False,
                "unrestricted_shell_access": False,
                "unrestricted_network_access": False,
            },
            "frozen_audit_independent": role_input.frozen_audit_independent,
            "bundles": bundles,
            "prior_reports": prior_reports,
        }
        if role_input.role is CodexQualityRole.FROZEN_RESEARCH_QUALITY:
            if len(bundles) != 1 or prior_reports:
                raise CodexQualityOutputError(
                    "Frozen research-quality input independence was violated."
                )
        return prompt


def _report_from_payload(
    payload: object,
    *,
    role_input: CodexRoleInput,
    checkpoint: CodexRoleCheckpoint,
    started_at: str,
    completed_at: str,
) -> CodexRoleReport:
    if not isinstance(payload, dict):
        raise CodexQualityOutputError("The quality provider returned invalid JSON output.")
    try:
        status = RoleReportStatus(str(payload.get("status") or ""))
    except ValueError as exc:
        raise CodexQualityOutputError("The quality report status is unsupported.") from exc

    raw_findings = payload.get("findings") or []
    if not isinstance(raw_findings, list):
        raise CodexQualityOutputError("Quality report findings must be a list.")
    findings = tuple(
        _finding_from_payload(
            item,
            role_input=role_input,
            index=index,
        )
        for index, item in enumerate(raw_findings[:50], start=1)
    )
    if status is RoleReportStatus.PASSED and any(
        finding.status is FindingStatus.OPEN
        and finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.HIGH}
        for finding in findings
    ):
        status = RoleReportStatus.ATTENTION_REQUIRED

    checks_total = _nonnegative_int(payload.get("checks_total"), len(findings))
    checks_passed = _nonnegative_int(payload.get("checks_passed"), 0)
    if checks_passed > checks_total:
        checks_total = checks_passed
    summary = _safe_text(payload.get("summary"), 4_000)
    if not summary:
        raise CodexQualityOutputError("The quality provider omitted its report summary.")

    return build_role_report(
        role_input=role_input,
        status=status,
        summary=summary,
        started_at=started_at,
        completed_at=completed_at,
        findings=findings,
        specialist_scope=checkpoint.specialist_scope,
        checks_passed=checks_passed,
        checks_total=checks_total,
        fixture_recommendations=_string_tuple(
            payload.get("fixture_recommendations"),
            limit=20,
            max_chars=1_000,
        ),
        blocking_reason=_safe_text(payload.get("blocking_reason"), 2_000),
    )


def _finding_from_payload(
    payload: object,
    *,
    role_input: CodexRoleInput,
    index: int,
) -> QualityFinding:
    if not isinstance(payload, dict):
        raise CodexQualityOutputError("Each quality finding must be an object.")
    try:
        severity = FindingSeverity(str(payload.get("severity") or "medium"))
        category = FindingCategory(
            str(payload.get("category") or "research_quality")
        )
        finding_status = FindingStatus(str(payload.get("status") or "open"))
    except ValueError as exc:
        raise CodexQualityOutputError(
            "A quality finding contains an unsupported controlled value."
        ) from exc

    code = _safe_code(payload.get("code"), "quality_issue")
    title = _safe_text(payload.get("title"), 500)
    summary = _safe_text(payload.get("summary"), 3_000)
    user_impact = _safe_text(payload.get("user_impact"), 2_000)
    if not title or not summary or not user_impact:
        raise CodexQualityOutputError(
            "Every quality finding requires title, summary, and user impact."
        )
    allowed_bundle_ids = {
        reference.bundle_id for reference in role_input.bundle_references
    }
    affected_bundle_ids = _string_tuple(
        payload.get("affected_bundle_ids"),
        limit=20,
        max_chars=200,
    )
    if not set(affected_bundle_ids).issubset(allowed_bundle_ids):
        raise CodexQualityOutputError(
            "A quality finding referenced a bundle hidden from its role."
        )
    candidate_ids = _string_tuple(
        payload.get("candidate_ids"),
        limit=50,
        max_chars=200,
    )
    if candidate_ids and not role_input.may_propose_candidates:
        raise CodexQualityOutputError(
            "This quality role is not permitted to propose candidate metadata."
        )

    finding_id = _stable_id("quality-finding", {
        "checkpoint_id": role_input.checkpoint_id,
        "index": index,
        "code": code,
        "route": _safe_text(payload.get("route"), 100),
        "title": title,
        "summary": summary,
    })
    return QualityFinding(
        finding_id=finding_id,
        role=role_input.role,
        severity=severity,
        category=category,
        code=code,
        title=title,
        summary=summary,
        user_impact=user_impact,
        status=finding_status,
        route=_safe_text(payload.get("route"), 100),
        reproduction_steps=_string_tuple(
            payload.get("reproduction_steps"),
            limit=20,
            max_chars=1_000,
        ),
        acceptance_criteria=_string_tuple(
            payload.get("acceptance_criteria"),
            limit=20,
            max_chars=1_000,
        ),
        affected_bundle_ids=affected_bundle_ids,
        related_claim_ids=_string_tuple(
            payload.get("related_claim_ids"),
            limit=100,
            max_chars=200,
        ),
        admitted_evidence_ids=_string_tuple(
            payload.get("admitted_evidence_ids"),
            limit=100,
            max_chars=200,
        ),
        candidate_ids=candidate_ids,
        fixture_hint=_safe_text(payload.get("fixture_hint"), 1_000),
    )


def _bundle_metadata(reference: QualityAuditBundleReference) -> dict:
    return {
        "bundle_id": reference.bundle_id,
        "kind": reference.kind.value,
        "run_id": reference.run_id,
        "artifact_id": reference.artifact_id,
        "evidence_hash": reference.evidence_hash,
        "schema_version": reference.schema_version,
        "result_version": reference.result_version,
        "source_policy_fingerprint": reference.source_policy_fingerprint,
        "parent_bundle_id": reference.parent_bundle_id,
        "sanitized": True,
        "secret_scan_passed": True,
        "licensed_content_policy_passed": True,
    }


def _safe_payload(value: object) -> object:
    sanitized = redact_sensitive_value(value)
    try:
        return json.loads(json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ))
    except (TypeError, ValueError) as exc:
        raise CodexQualityOutputError(
            "A sanitized quality payload is not JSON serializable."
        ) from exc


def _role_objective(role: CodexQualityRole) -> str:
    return {
        CodexQualityRole.FROZEN_RESEARCH_QUALITY: (
            "Independently evaluate the frozen artifact's decision usefulness, "
            "support, contradictions, and agenda coverage."
        ),
        CodexQualityRole.ADVERSARIAL_SCOUT: (
            "Challenge the frozen audit through bounded contrary-evidence and "
            "gap discovery; propose candidates only."
        ),
        CodexQualityRole.CONDITIONAL_SPECIALIST: (
            "Evaluate only the permitted material specialist scope and report "
            "what evidence or correction is required."
        ),
        CodexQualityRole.CLOSURE_CHECKER: (
            "Verify that material findings and admitted contrary evidence were "
            "addressed in the revised artifact."
        ),
        CodexQualityRole.UI_JOURNEY: (
            "Test the user journey for correctness, readability, accessibility, "
            "responsive behavior, and non-duplicated decision content."
        ),
        CodexQualityRole.TECHNICAL_DEBUGGER: (
            "Diagnose the sanitized failure, isolate its root cause, and specify "
            "a reproducible regression fixture."
        ),
    }[role]


def _output_schema() -> dict:
    return {
        "status": [item.value for item in RoleReportStatus],
        "summary": "readable string",
        "checks_passed": "non-negative integer",
        "checks_total": "non-negative integer",
        "blocking_reason": "optional readable string",
        "fixture_recommendations": ["sanitized fixture description"],
        "findings": [{
            "severity": [item.value for item in FindingSeverity],
            "category": [item.value for item in FindingCategory],
            "status": [item.value for item in FindingStatus],
            "code": "stable_snake_case_code",
            "title": "readable title",
            "summary": "evidence-backed concise diagnosis",
            "user_impact": "readable user impact",
            "route": "optional UI route",
            "reproduction_steps": ["step"],
            "acceptance_criteria": ["criterion"],
            "affected_bundle_ids": ["existing visible bundle ID"],
            "related_claim_ids": ["existing claim ID"],
            "admitted_evidence_ids": ["existing admitted evidence ID"],
            "candidate_ids": ["candidate metadata ID when permitted"],
            "fixture_hint": "sanitized deterministic fixture",
        }],
    }


def _is_retryable_provider_failure(exc: Exception) -> bool:
    explicit = getattr(exc, "retryable", None)
    if explicit is not None:
        return bool(explicit)
    return isinstance(exc, (TimeoutError, ConnectionError, URLError))


def _stopping_reason(run: CodexQualityRun) -> str:
    if any(
        item.status is RoleCheckpointStatus.FAILED and item.retryable
        for item in run.checkpoints
    ):
        return "retryable_checkpoint_waiting"
    if any(item.status is RoleCheckpointStatus.FAILED for item in run.checkpoints):
        return "non_retryable_checkpoint_failed"
    if all(
        item.status in {
            RoleCheckpointStatus.COMPLETED,
            RoleCheckpointStatus.SKIPPED,
        }
        for item in run.checkpoints
    ):
        return "all_selected_roles_completed"
    return "no_checkpoint_ready"


def _string_tuple(
    value: object,
    *,
    limit: int,
    max_chars: int,
) -> Tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, (list, tuple, set)) else (value,)
    result = []
    for item in values:
        text = _safe_text(item, max_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


def _safe_text(value: object, max_chars: int) -> str:
    return redact_sensitive_text(value).strip()[:max_chars]


def _safe_code(value: object, fallback: str) -> str:
    text = _safe_text(value, 120).casefold()
    normalized = "_".join(part for part in _split_code(text) if part)
    return normalized or fallback


def _split_code(value: str) -> Sequence[str]:
    current = ""
    parts = []
    for char in value:
        if char.isalnum():
            current += char
        elif current:
            parts.append(current)
            current = ""
    if current:
        parts.append(current)
    return parts


def _nonnegative_int(value: object, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


def _stable_id(prefix: str, payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return prefix + "-" + hashlib.sha256(encoded).hexdigest()[:20]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
