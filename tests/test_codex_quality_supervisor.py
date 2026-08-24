from __future__ import annotations

from dataclasses import replace

import pytest

from equity_research.application.codex_quality_supervisor import (
    AuditBundleKind,
    CodexQualityRole,
    CodexQualitySupervisor,
    CodexQualityTriggerPolicy,
    ConditionalSpecialistRequest,
    FindingCategory,
    FindingSeverity,
    InMemoryCodexQualityRepository,
    QualityAuditBundleReference,
    QualityFinding,
    QualityRunStatus,
    QualityTrigger,
    QualityTriggerMode,
    RoleCheckpointStatus,
    RoleReportStatus,
    build_role_report,
)


NOW = "2026-08-13T12:00:00+00:00"


def _bundle(
    kind: AuditBundleKind = AuditBundleKind.FROZEN_RESEARCH_ARTIFACT,
    suffix: str = "frozen",
) -> QualityAuditBundleReference:
    return QualityAuditBundleReference(
        bundle_id=f"bundle-{suffix}",
        kind=kind,
        run_id="research-run-1",
        artifact_id=f"artifact-{suffix}",
        evidence_hash=f"evidence-{suffix}",
        payload_reference=f"sanitized://{suffix}",
        created_at=NOW,
        schema_version="research-result-v1",
        sanitized=True,
        secret_scan_passed=True,
        licensed_content_policy_passed=True,
    )


def _complete(
    supervisor: CodexQualitySupervisor,
    run_id: str,
    checkpoint_id: str,
    *,
    finding: QualityFinding | None = None,
) -> None:
    checkpoint = supervisor.claim_checkpoint(run_id, checkpoint_id)
    role_input = supervisor.role_input(run_id, checkpoint.checkpoint_id)
    report = build_role_report(
        role_input=role_input,
        status=(
            RoleReportStatus.ATTENTION_REQUIRED
            if finding else RoleReportStatus.PASSED
        ),
        summary="Structured quality review completed.",
        started_at=NOW,
        completed_at=NOW,
        findings=(finding,) if finding else (),
        checks_passed=1,
        checks_total=1,
    )
    supervisor.complete_checkpoint(run_id, report)


def test_bundle_boundary_rejects_unsanitized_or_unlicensed_inputs() -> None:
    with pytest.raises(ValueError, match="sanitized"):
        replace(_bundle(), sanitized=False)
    with pytest.raises(ValueError, match="licensed-content"):
        replace(_bundle(), licensed_content_policy_passed=False)


def test_hybrid_trigger_policy_selects_bounded_roles() -> None:
    policy = CodexQualityTriggerPolicy()

    research_roles = policy.roles_for(QualityTrigger.RESEARCH_COMPLETED)
    assert research_roles == (
        CodexQualityRole.FROZEN_RESEARCH_QUALITY,
        CodexQualityRole.ADVERSARIAL_SCOUT,
        CodexQualityRole.CLOSURE_CHECKER,
    )
    assert policy.roles_for(QualityTrigger.UI_CHANGED) == (
        CodexQualityRole.UI_JOURNEY,
    )
    assert policy.roles_for(QualityTrigger.TEST_FAILURE) == (
        CodexQualityRole.TECHNICAL_DEBUGGER,
    )
    release_roles = policy.roles_for(QualityTrigger.RELEASE_CANDIDATE)
    assert CodexQualityRole.UI_JOURNEY in release_roles
    assert CodexQualityRole.TECHNICAL_DEBUGGER in release_roles

    release_only = CodexQualityTriggerPolicy(
        mode=QualityTriggerMode.RELEASE_ONLY
    )
    assert release_only.roles_for(QualityTrigger.RESEARCH_COMPLETED) == ()


def test_start_is_idempotent_and_specialists_are_material_and_permitted() -> None:
    repository = InMemoryCodexQualityRepository()
    supervisor = CodexQualitySupervisor(repository, now=lambda: NOW)
    requests = (
        ConditionalSpecialistRequest(
            scope="valuation",
            finding_ids=("finding-valuation",),
            materiality=FindingSeverity.HIGH,
        ),
        ConditionalSpecialistRequest(
            scope="minor-copy",
            materiality=FindingSeverity.LOW,
        ),
        ConditionalSpecialistRequest(
            scope="private-source",
            materiality=FindingSeverity.CRITICAL,
            permitted=False,
        ),
    )

    first, created = supervisor.start_or_resume(
        _bundle(),
        trigger=QualityTrigger.RESEARCH_COMPLETED,
        specialist_requests=requests,
    )
    resumed, created_again = supervisor.start_or_resume(
        _bundle(),
        trigger=QualityTrigger.RESEARCH_COMPLETED,
        specialist_requests=requests,
    )

    assert created is True
    assert created_again is False
    assert resumed == first
    specialists = [
        item for item in first.checkpoints
        if item.role is CodexQualityRole.CONDITIONAL_SPECIALIST
    ]
    assert len(specialists) == 1
    assert specialists[0].specialist_scope == "valuation"
    assert specialists[0].scoped_finding_ids == ("finding-valuation",)


def test_frozen_audit_is_independent_then_scout_specialist_and_closure_run() -> None:
    repository = InMemoryCodexQualityRepository()
    supervisor = CodexQualitySupervisor(repository, now=lambda: NOW)
    frozen = _bundle()
    evidence_delta = _bundle(
        AuditBundleKind.ADMITTED_EVIDENCE_DELTA,
        "admitted-delta",
    )
    revised = _bundle(
        AuditBundleKind.REVISED_RESEARCH_ARTIFACT,
        "revised",
    )
    responses = _bundle(AuditBundleKind.PRIMARY_RESPONSES, "responses")
    run, _ = supervisor.start_or_resume(
        frozen,
        trigger=QualityTrigger.RESEARCH_COMPLETED,
        supplementary_bundles=(evidence_delta, revised, responses),
        specialist_requests=(ConditionalSpecialistRequest(
            scope="cash-conversion",
            finding_ids=("finding-cash",),
        ),),
    )
    audit = next(
        item for item in run.checkpoints
        if item.role is CodexQualityRole.FROZEN_RESEARCH_QUALITY
    )
    scout = next(
        item for item in run.checkpoints
        if item.role is CodexQualityRole.ADVERSARIAL_SCOUT
    )
    specialist = next(
        item for item in run.checkpoints
        if item.role is CodexQualityRole.CONDITIONAL_SPECIALIST
    )
    closure = next(
        item for item in run.checkpoints
        if item.role is CodexQualityRole.CLOSURE_CHECKER
    )

    assert supervisor.ready_checkpoints(run.run_id) == (audit,)
    audit_input = supervisor.role_input(run.run_id, audit.checkpoint_id)
    assert audit_input.frozen_audit_independent is True
    assert audit_input.bundle_references == (frozen,)
    assert audit_input.prior_report_ids == ()
    assert audit_input.may_view_candidate_metadata is False
    assert audit_input.may_admit_evidence is False
    assert audit_input.repository_write_access is False

    finding = QualityFinding(
        finding_id="finding-cash",
        role=CodexQualityRole.FROZEN_RESEARCH_QUALITY,
        severity=FindingSeverity.HIGH,
        category=FindingCategory.RESEARCH_QUALITY,
        code="cash_bridge_incomplete",
        title="Cash conversion bridge is incomplete",
        summary="Working-capital and capital-return effects are not reconciled.",
        user_impact="The conclusion may overstate recurring free cash flow.",
        acceptance_criteria=("Reconcile aligned periods from admitted evidence.",),
    )
    _complete(supervisor, run.run_id, audit.checkpoint_id, finding=finding)

    assert supervisor.ready_checkpoints(run.run_id) == (scout,)
    scout_input = supervisor.role_input(run.run_id, scout.checkpoint_id)
    assert scout_input.prior_report_ids
    assert scout_input.may_propose_candidates is True
    assert scout_input.may_admit_evidence is False
    assert {item.kind for item in scout_input.bundle_references} == {
        AuditBundleKind.FROZEN_RESEARCH_ARTIFACT,
        AuditBundleKind.ADMITTED_EVIDENCE_DELTA,
    }
    _complete(supervisor, run.run_id, scout.checkpoint_id)

    assert supervisor.ready_checkpoints(run.run_id) == (specialist,)
    specialist_input = supervisor.role_input(run.run_id, specialist.checkpoint_id)
    assert specialist_input.scoped_finding_ids == ("finding-cash",)
    assert len(specialist_input.prior_report_ids) == 2
    _complete(supervisor, run.run_id, specialist.checkpoint_id)

    assert supervisor.ready_checkpoints(run.run_id) == (closure,)
    closure_input = supervisor.role_input(run.run_id, closure.checkpoint_id)
    assert len(closure_input.prior_report_ids) == 3
    assert {item.kind for item in closure_input.bundle_references} == {
        AuditBundleKind.FROZEN_RESEARCH_ARTIFACT,
        AuditBundleKind.REVISED_RESEARCH_ARTIFACT,
        AuditBundleKind.ADMITTED_EVIDENCE_DELTA,
        AuditBundleKind.PRIMARY_RESPONSES,
    }
    _complete(supervisor, run.run_id, closure.checkpoint_id)

    completed = supervisor.inspect(run.run_id)
    assert completed.status is QualityRunStatus.COMPLETED
    assert len(repository.reports_for_run(run.run_id)) == 4


def test_retryable_failure_resumes_without_repeating_completed_roles() -> None:
    repository = InMemoryCodexQualityRepository()
    supervisor = CodexQualitySupervisor(repository, now=lambda: NOW)
    diagnostics = _bundle(
        AuditBundleKind.TECHNICAL_DIAGNOSTICS,
        "diagnostics",
    )
    run, _ = supervisor.start_or_resume(
        _bundle(),
        trigger=QualityTrigger.TEST_FAILURE,
        supplementary_bundles=(diagnostics,),
    )
    debugger = run.checkpoints[0]
    claimed = supervisor.claim_checkpoint(run.run_id, debugger.checkpoint_id)
    assert claimed.attempt == 1

    failed = supervisor.fail_checkpoint(
        run.run_id,
        debugger.checkpoint_id,
        error_summary="Transient Codex provider failure.",
        retryable=True,
    )
    assert failed.status is QualityRunStatus.PAUSED

    after_restart = supervisor.restart_checkpoint(
        run.run_id,
        debugger.checkpoint_id,
    )
    restarted = after_restart.checkpoints[0]
    assert restarted.status is RoleCheckpointStatus.PENDING
    assert restarted.attempt == 2

    second_process = CodexQualitySupervisor(repository, now=lambda: NOW)
    resumed, created = second_process.start_or_resume(
        _bundle(),
        trigger=QualityTrigger.TEST_FAILURE,
        supplementary_bundles=(diagnostics,),
    )
    assert created is False
    assert resumed.checkpoints[0].attempt == 2
    _complete(second_process, run.run_id, debugger.checkpoint_id)
    completed = second_process.inspect(run.run_id)
    assert completed.status is QualityRunStatus.COMPLETED

    unchanged = second_process.restart_checkpoint(run.run_id, debugger.checkpoint_id)
    assert unchanged == completed
    assert len(repository.reports_for_run(run.run_id)) == 1


def test_role_report_cannot_reference_hidden_bundles_or_misattributed_findings() -> None:
    repository = InMemoryCodexQualityRepository()
    supervisor = CodexQualitySupervisor(repository, now=lambda: NOW)
    diagnostics = _bundle(
        AuditBundleKind.TECHNICAL_DIAGNOSTICS,
        "diagnostics",
    )
    run, _ = supervisor.start_or_resume(
        _bundle(),
        trigger=QualityTrigger.RESEARCH_COMPLETED,
        supplementary_bundles=(diagnostics,),
    )
    audit = supervisor.claim_checkpoint(run.run_id)
    role_input = supervisor.role_input(run.run_id, audit.checkpoint_id)
    report = build_role_report(
        role_input=role_input,
        status=RoleReportStatus.PASSED,
        summary="Frozen audit passed.",
        started_at=NOW,
        completed_at=NOW,
        checks_passed=1,
        checks_total=1,
    )
    with pytest.raises(ValueError, match="unavailable"):
        supervisor.complete_checkpoint(
            run.run_id,
            replace(report, bundle_ids=(diagnostics.bundle_id,)),
        )

    wrong_role = QualityFinding(
        finding_id="finding-wrong-role",
        role=CodexQualityRole.UI_JOURNEY,
        severity=FindingSeverity.MEDIUM,
        category=FindingCategory.UI_READABILITY,
        code="duplicate_action",
        title="Duplicate action",
        summary="Two action cards repeat the same objective.",
        user_impact="Users cannot distinguish the next investigations.",
    )
    with pytest.raises(ValueError, match="attributed"):
        replace(report, findings=(wrong_role,))
