from __future__ import annotations

from collections import defaultdict

from equity_research.application.codex_quality_supervisor import (
    AuditBundleKind,
    CodexQualityRole,
    CodexQualitySupervisor,
    FindingCategory,
    InMemoryCodexQualityRepository,
    QualityAuditBundleReference,
    QualityRunStatus,
    QualityTrigger,
    RoleCheckpointStatus,
    RoleReportStatus,
)
from equity_research.infrastructure.codex_quality_executor import (
    CodexQualityExecutor,
)


NOW = "2026-08-13T14:00:00+00:00"


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
    )


class RecordingBundleLoader:
    def __init__(self) -> None:
        self.loaded = []

    def load_bundle(self, reference: QualityAuditBundleReference) -> dict:
        self.loaded.append(reference.bundle_id)
        return {
            "bundle_id": reference.bundle_id,
            "readable": f"Sanitized content for {reference.kind.value}",
            "api_key": "raw-secret-must-not-cross-boundary",
            "source_url": "https://example.com/?token=raw-query-secret",
        }


class RecordingProvider:
    provider_name = "fixture-json-provider"
    model = "fixture-model"

    def __init__(self, owner, role: CodexQualityRole, outcome) -> None:
        self.owner = owner
        self.role = role
        self.outcome = outcome
        self.instance_id = len(owner.instances) + 1
        owner.instances.append(self)

    def complete_json(self, prompt_pack: dict) -> dict:
        self.owner.calls.append((self.instance_id, self.role, prompt_pack))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class RecordingProviderFactory:
    def __init__(self, outcomes) -> None:
        self.outcomes = {
            role: list(values) if isinstance(values, list) else [values]
            for role, values in outcomes.items()
        }
        self.calls = []
        self.instances = []
        self.created_by_role = defaultdict(int)

    def __call__(self, role_input):
        role = role_input.role
        index = self.created_by_role[role]
        self.created_by_role[role] += 1
        values = self.outcomes[role]
        outcome = values[min(index, len(values) - 1)]
        return RecordingProvider(self, role, outcome)


def _passed(summary: str) -> dict:
    return {
        "status": "passed",
        "summary": summary,
        "checks_passed": 3,
        "checks_total": 3,
        "findings": [],
    }


def test_executor_runs_fresh_audit_scout_and_closure_calls_in_order() -> None:
    repository = InMemoryCodexQualityRepository()
    supervisor = CodexQualitySupervisor(repository, now=lambda: NOW)
    frozen = _bundle()
    delta = _bundle(AuditBundleKind.ADMITTED_EVIDENCE_DELTA, "delta")
    revised = _bundle(AuditBundleKind.REVISED_RESEARCH_ARTIFACT, "revised")
    responses = _bundle(AuditBundleKind.PRIMARY_RESPONSES, "responses")
    run, _ = supervisor.start_or_resume(
        frozen,
        trigger=QualityTrigger.RESEARCH_COMPLETED,
        supplementary_bundles=(delta, revised, responses),
    )
    factory = RecordingProviderFactory({
        CodexQualityRole.FROZEN_RESEARCH_QUALITY: {
            "status": "passed",
            "summary": "Frozen research-quality review found a material gap.",
            "checks_passed": 4,
            "checks_total": 5,
            "findings": [{
                "severity": "high",
                "category": "research_quality",
                "code": "cash_bridge_incomplete",
                "title": "Cash bridge remains incomplete",
                "summary": "Working-capital effects are not reconciled.",
                "user_impact": "Recurring cash generation may be overstated.",
                "affected_bundle_ids": [frozen.bundle_id],
                "acceptance_criteria": ["Reconcile aligned filing periods."],
            }],
            "fixture_recommendations": ["Sanitized incomplete cash-bridge fixture"],
        },
        CodexQualityRole.ADVERSARIAL_SCOUT: _passed(
            "Adversarial scouting completed without direct admission."
        ),
        CodexQualityRole.CLOSURE_CHECKER: _passed(
            "Closure verified the structured responses."
        ),
    })
    loader = RecordingBundleLoader()
    executor = CodexQualityExecutor(
        supervisor,
        provider_factory=factory,
        bundle_loader=loader,
        now=lambda: NOW,
    )

    executed = executor.execute_ready(run.run_id)

    assert executed.run.status is QualityRunStatus.COMPLETED
    assert len(executed.completed_checkpoint_ids) == 3
    assert [item[1] for item in factory.calls] == [
        CodexQualityRole.FROZEN_RESEARCH_QUALITY,
        CodexQualityRole.ADVERSARIAL_SCOUT,
        CodexQualityRole.CLOSURE_CHECKER,
    ]
    assert len({item[0] for item in factory.calls}) == 3

    audit_prompt = factory.calls[0][2]
    assert audit_prompt["frozen_audit_independent"] is True
    assert len(audit_prompt["bundles"]) == 1
    assert audit_prompt["prior_reports"] == []
    assert audit_prompt["bundles"][0]["bundle"]["bundle_id"] == frozen.bundle_id
    assert "raw-secret-must-not-cross-boundary" not in str(audit_prompt)
    assert "raw-query-secret" not in str(audit_prompt)
    assert audit_prompt["permissions"] == {
        "tool_profile": "read_only_frozen_artifact",
        "may_view_candidate_metadata": False,
        "may_propose_candidates": False,
        "may_admit_evidence": False,
        "repository_write_access": False,
        "unrestricted_shell_access": False,
        "unrestricted_network_access": False,
    }

    scout_prompt = factory.calls[1][2]
    assert len(scout_prompt["prior_reports"]) == 1
    assert scout_prompt["permissions"]["may_propose_candidates"] is True
    assert {item["bundle"]["kind"] for item in scout_prompt["bundles"]} == {
        AuditBundleKind.FROZEN_RESEARCH_ARTIFACT.value,
        AuditBundleKind.ADMITTED_EVIDENCE_DELTA.value,
    }

    closure_prompt = factory.calls[2][2]
    assert len(closure_prompt["prior_reports"]) == 2
    assert {item["bundle"]["kind"] for item in closure_prompt["bundles"]} == {
        AuditBundleKind.FROZEN_RESEARCH_ARTIFACT.value,
        AuditBundleKind.ADMITTED_EVIDENCE_DELTA.value,
        AuditBundleKind.REVISED_RESEARCH_ARTIFACT.value,
        AuditBundleKind.PRIMARY_RESPONSES.value,
    }
    audit_report = repository.reports_for_run(run.run_id)[0]
    assert audit_report.status is RoleReportStatus.ATTENTION_REQUIRED
    assert audit_report.findings[0].code == "cash_bridge_incomplete"

    no_repeat = executor.execute_ready(run.run_id)
    assert no_repeat.completed_checkpoint_ids == ()
    assert len(factory.calls) == 3


def test_ui_and_debugger_roles_receive_only_their_sanitized_bundle_types() -> None:
    repository = InMemoryCodexQualityRepository()
    supervisor = CodexQualitySupervisor(repository, now=lambda: NOW)
    frozen = _bundle()
    ui = _bundle(AuditBundleKind.UI_PRESENTATION_SNAPSHOT, "ui")
    diagnostics = _bundle(AuditBundleKind.TECHNICAL_DIAGNOSTICS, "diagnostics")
    loader = RecordingBundleLoader()

    ui_run, _ = supervisor.start_or_resume(
        frozen,
        trigger=QualityTrigger.UI_CHANGED,
        supplementary_bundles=(ui, diagnostics),
    )
    ui_factory = RecordingProviderFactory({
        CodexQualityRole.UI_JOURNEY: {
            "status": "attention_required",
            "summary": "The Decision journey contains duplicate action cards.",
            "checks_passed": 5,
            "checks_total": 6,
            "findings": [{
                "severity": "medium",
                "category": "ui_readability",
                "code": "duplicate_investigate_next_action",
                "title": "Duplicate Investigate next action",
                "summary": "Action 1 and Action 3 repeat the same objective.",
                "user_impact": "Users cannot distinguish the next research tasks.",
                "route": "Decision",
                "affected_bundle_ids": [ui.bundle_id],
                "reproduction_steps": ["Load AAPL", "Open Decision"],
                "acceptance_criteria": ["Visible action objectives are unique."],
                "fixture_hint": "Saved AAPL queue with stale semantic keys",
            }],
        },
    })
    ui_executor = CodexQualityExecutor(
        supervisor,
        provider_factory=ui_factory,
        bundle_loader=loader,
        now=lambda: NOW,
    )
    ui_result = ui_executor.execute_ready(ui_run.run_id)
    ui_prompt = ui_factory.calls[0][2]
    assert ui_result.run.status is QualityRunStatus.COMPLETED
    assert [item["bundle"]["kind"] for item in ui_prompt["bundles"]] == [
        AuditBundleKind.UI_PRESENTATION_SNAPSHOT.value
    ]
    assert ui_prompt["permissions"]["tool_profile"] == "read_only_ui_browser"
    ui_report = repository.reports_for_run(ui_run.run_id)[0]
    assert ui_report.findings[0].category is FindingCategory.UI_READABILITY

    debug_run, _ = supervisor.start_or_resume(
        frozen,
        trigger=QualityTrigger.TEST_FAILURE,
        supplementary_bundles=(ui, diagnostics),
    )
    debug_factory = RecordingProviderFactory({
        CodexQualityRole.TECHNICAL_DEBUGGER: _passed(
            "The sanitized traceback was diagnosed."
        ),
    })
    debug_executor = CodexQualityExecutor(
        supervisor,
        provider_factory=debug_factory,
        bundle_loader=loader,
        now=lambda: NOW,
    )
    debug_result = debug_executor.execute_ready(debug_run.run_id)
    debug_prompt = debug_factory.calls[0][2]
    assert debug_result.run.status is QualityRunStatus.COMPLETED
    assert [item["bundle"]["kind"] for item in debug_prompt["bundles"]] == [
        AuditBundleKind.TECHNICAL_DIAGNOSTICS.value
    ]
    assert debug_prompt["permissions"]["tool_profile"] == "sanitized_diagnostics"
    assert all(
        value is False
        for key, value in debug_prompt["permissions"].items()
        if key != "tool_profile"
    )


def test_retryable_provider_failure_restarts_once_and_completes_idempotently() -> None:
    repository = InMemoryCodexQualityRepository()
    supervisor = CodexQualitySupervisor(repository, now=lambda: NOW)
    diagnostics = _bundle(AuditBundleKind.TECHNICAL_DIAGNOSTICS, "diagnostics")
    run, _ = supervisor.start_or_resume(
        _bundle(),
        trigger=QualityTrigger.TEST_FAILURE,
        supplementary_bundles=(diagnostics,),
    )
    factory = RecordingProviderFactory({
        CodexQualityRole.TECHNICAL_DEBUGGER: [
            TimeoutError("temporary provider timeout"),
            _passed("Retry completed the technical diagnosis."),
        ],
    })
    executor = CodexQualityExecutor(
        supervisor,
        provider_factory=factory,
        bundle_loader=RecordingBundleLoader(),
        now=lambda: NOW,
    )

    first = executor.execute_ready(run.run_id)
    assert first.run.status is QualityRunStatus.PAUSED
    assert first.stopped_reason == "retryable_provider_failure"
    assert first.run.checkpoints[0].status is RoleCheckpointStatus.FAILED
    assert first.run.checkpoints[0].attempt == 1

    second = executor.execute_ready(run.run_id, retry_failed=True)
    assert second.run.status is QualityRunStatus.COMPLETED
    assert second.run.checkpoints[0].attempt == 2
    assert len(repository.reports_for_run(run.run_id)) == 1
    assert len(factory.calls) == 2
    assert factory.calls[0][2]["idempotency_key"] != factory.calls[1][2]["idempotency_key"]

    third = executor.execute_ready(run.run_id, retry_failed=True)
    assert third.completed_checkpoint_ids == ()
    assert len(factory.calls) == 2
