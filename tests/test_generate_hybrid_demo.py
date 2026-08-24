from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

from equity_research.domain.publication_readiness import (
    PublicationGateEvaluation,
    PublicationGateFailure,
    PublicationQualification,
    ResearchRepairAction,
)
from equity_research.application.repair_stage_execution import (
    InMemoryRepairStageCheckpointRepository,
    RepairStageCheckpoint,
)
from equity_research.models import DemoFreezeDecision, LlmProviderProfile
import scripts.generate_hybrid_demo as demo_generator
from scripts.generate_hybrid_demo import (
    _assert_license_safe,
    _bounded_freeze_report,
    _freeze_saved_candidates,
    _public_presentation_blockers,
    _repair_attempt_id,
    _resolve_deepseek_credentials,
    _resolve_openai_auditor_credentials,
    _write_hosted_auditor_setup_required,
    _write_terminal_generator_failure,
)


class _Store:
    def __init__(
        self,
        profiles: list[LlmProviderProfile],
        primary_profile_id: str = "",
        secondary_profile_id: str = "",
    ) -> None:
        self.profiles = profiles
        self.primary_profile_id = primary_profile_id
        self.secondary_profile_id = secondary_profile_id

    def get_llm_selection(self) -> dict:
        return {
            "primary_profile_id": self.primary_profile_id,
            "secondary_profile_id": self.secondary_profile_id,
        }

    def get_llm_profile(self, profile_id: str) -> LlmProviderProfile | None:
        return next(
            (item for item in self.profiles if item.profile_id == profile_id),
            None,
        )

    def list_llm_profiles(self) -> list[LlmProviderProfile]:
        return self.profiles


class _Manager:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = secrets

    def get(self, key: str) -> str | None:
        return self.secrets.get(key)


def _profile(
    profile_id: str,
    *,
    preset: str = "deepseek",
) -> LlmProviderProfile:
    return LlmProviderProfile(
        profile_id=profile_id,
        display_name=profile_id,
        provider_preset=preset,
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com",
        secret_ref=f"LLM_PROFILE_API_KEY::{profile_id}",
    )


def test_demo_generator_uses_selected_deepseek_vault_profile() -> None:
    profile = _profile("deepseek-primary")
    key, model, base_url, source = _resolve_deepseek_credentials(
        _Store([profile], primary_profile_id=profile.profile_id),
        _Manager({profile.secret_ref: "vault-test-key"}),
    )

    assert key == "vault-test-key"
    assert model == "deepseek-v4-pro"
    assert base_url == "https://api.deepseek.com"
    assert source == "provider_vault"


def test_repair_attempt_identity_is_stable_for_saved_parent() -> None:
    payload = {"run_id": "parent-1", "evidence_hash": "evidence-1"}

    assert _repair_attempt_id(payload) == _repair_attempt_id(dict(payload))
    assert len(_repair_attempt_id(payload)) == 24


def test_repair_attempt_identity_changes_with_revision_strategy(
    monkeypatch,
) -> None:
    payload = {"run_id": "parent-1", "evidence_hash": "evidence-1"}
    current = _repair_attempt_id(payload)

    monkeypatch.setattr(
        demo_generator,
        "DETERMINISTIC_SAFE_REVISION_VERSION",
        "deterministic-safe-revision-next",
    )

    assert _repair_attempt_id(payload) != current


def test_demo_generator_finds_deepseek_profile_when_another_is_selected() -> None:
    openai = _profile("openai-primary", preset="openai")
    deepseek = replace(_profile("deepseek-audited"), model="deepseek-research")
    key, model, _, source = _resolve_deepseek_credentials(
        _Store([openai, deepseek], primary_profile_id=openai.profile_id),
        _Manager({deepseek.secret_ref: "vault-deepseek-key"}),
    )

    assert key == "vault-deepseek-key"
    assert model == "deepseek-research"
    assert source == "provider_vault"


def test_demo_generator_resolves_saved_openai_sol_auditor_profile() -> None:
    deepseek = _profile("deepseek-primary")
    sol = replace(
        _profile("sol-auditor", preset="openai_sol"),
        model="gpt-5.6-sol",
        base_url="https://api.openai.com/v1",
        role_eligibility="checker critic",
    )

    key, source = _resolve_openai_auditor_credentials(
        _Store(
            [deepseek, sol],
            primary_profile_id=deepseek.profile_id,
            secondary_profile_id=sol.profile_id,
        ),
        _Manager({sol.secret_ref: "vault-openai-key"}),
    )

    assert key == "vault-openai-key"
    assert source == "provider_vault"


def test_demo_generator_does_not_use_primary_only_profile_for_hosted_qa(
    monkeypatch,
) -> None:
    primary_only = replace(
        _profile("openai-primary", preset="openai_sol"),
        role_eligibility="planner investigator",
    )
    monkeypatch.setattr(demo_generator.config, "OPENAI_API_KEY", "")

    key, source = _resolve_openai_auditor_credentials(
        _Store([primary_only], primary_profile_id=primary_only.profile_id),
        _Manager({primary_only.secret_ref: "must-not-resolve"}),
    )

    assert key == ""
    assert source == "missing"


def test_demo_generator_rejects_raw_licensed_payload_fields() -> None:
    _assert_license_safe({"summary": "sanitized paraphrase"})

    import pytest

    with pytest.raises(RuntimeError, match="licensed content"):
        _assert_license_safe({"licensed_payload": {"body": "private report"}})


def test_demo_secret_check_distinguishes_prose_from_credential_tokens() -> None:
    from scripts.generate_hybrid_demo import _assert_no_secrets

    _assert_no_secrets("Risk-factor language changed.")

    import pytest

    with pytest.raises(RuntimeError, match="credential-like"):
        _assert_no_secrets("temporary sk-abcdefghijklmnopqrstuv")


def test_aapl_demo_requires_its_cash_conversion_showcase() -> None:
    blockers = _public_presentation_blockers(
        "AAPL",
        SimpleNamespace(cash_conversion_recurrence=None),
    )

    assert len(blockers) == 1
    assert "validated two-period cash-conversion bridge" in blockers[0]
    assert _public_presentation_blockers(
        "MSFT",
        SimpleNamespace(cash_conversion_recurrence=None),
    ) == []


def test_freeze_report_is_bounded_and_retains_actionable_gate_metadata(
    tmp_path: Path,
) -> None:
    failure = PublicationGateFailure(
        failure_id="failure-1",
        code="final_closure_not_passed",
        run_id="run-1",
        summary="Closure is required.",
        technical_diagnosis="No closure exists.",
        blocking_rule="Closure must pass.",
        recommended_repair_kind="rerun_closure",
    )
    qualification = PublicationQualification(
        qualification_id="qualification-1",
        code="unsupported_claim_removed",
        run_id="run-1",
        summary="Unsupported claim removed.",
    )
    repair = ResearchRepairAction(
        action_id="repair-1",
        idempotency_key="repair-key-1",
        failure_ids=(failure.failure_id,),
        repair_kind="rerun_closure",
        objective="Rerun closure.",
        readable_explanation="Closure is required.",
        completion_criteria=("Closure passes.",),
    )
    evaluation = PublicationGateEvaluation(
        evaluation_id="evaluation-1",
        run_id="run-1",
        artifact_version="artifact-1",
        evidence_hash="evidence-1",
        publication_policy_version="demo-quality-policy-v3",
        validation_policy_version="validation-v1",
        schema_version="artifact-v2",
        parser_version="parser-v1",
        evaluated_at="2026-08-10T00:00:00+00:00",
        decision="repair_required",
        failures=(failure,),
        qualifications=(qualification,),
        repair_actions=(repair,),
    )
    decision = DemoFreezeDecision(
        ticker="AAPL",
        status="blocked",
        allowed=False,
        evaluated_at="2026-08-10T00:00:00+00:00",
        blockers=["Closure is required."],
        next_actions=["Rerun closure."],
        source_run_id="run-1",
        policy_version="demo-quality-policy-v3",
        publication_gate_evaluation=evaluation,
        repair_actions=[repair],
        publication_label="Not publication-ready",
    )

    report = _bounded_freeze_report(
        "AAPL",
        [decision],
        output_path=tmp_path / "AAPL.json",
        selection_policy="test",
    )

    candidate = report["candidates"][0]
    assert candidate["failure_codes"] == ["final_closure_not_passed"]
    assert candidate["qualification_codes"] == ["unsupported_claim_removed"]
    assert candidate["repair_kinds"] == ["rerun_closure"]
    assert "publication_gate_evaluation" not in candidate


def test_freeze_report_surfaces_provider_action_without_raw_response(
    tmp_path: Path,
) -> None:
    decision = DemoFreezeDecision(
        ticker="BABA",
        status="blocked",
        allowed=False,
        evaluated_at="2026-08-20T00:00:00+00:00",
        blockers=["Independent closure has not passed."],
        source_run_id="run-1",
        policy_version="demo-quality-policy-v3",
        publication_label="Not publication-ready",
    )
    checkpoint = RepairStageCheckpoint(
        attempt_id="repair-1",
        parent_run_id="run-1",
        ticker="BABA",
        evidence_hash="evidence-1",
        stage="primary_revision",
        state="waiting_for_provider",
        attempt_count=1,
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        deadline_at="2026-08-20T00:05:00+00:00",
        failure_class="provider_credit_required",
        summary="The selected model provider has insufficient credit.",
    )

    report = _bounded_freeze_report(
        "BABA",
        [decision],
        output_path=tmp_path / "BABA.json",
        selection_policy="test",
        repair_checkpoints=[checkpoint],
    )

    assert report["provider_action_required"]["stage"] == "primary_revision"
    assert "Add credit" in report["provider_action_required"]["next_action"]
    assert "HTTP 402" not in json.dumps(report)


def test_freeze_report_explains_local_codex_user_context(tmp_path: Path) -> None:
    decision = DemoFreezeDecision(
        ticker="BABA",
        status="blocked",
        allowed=False,
        evaluated_at="2026-08-20T00:00:00+00:00",
        blockers=["Independent closure has not passed."],
        source_run_id="run-1",
        publication_label="Not publication-ready",
    )
    checkpoint = RepairStageCheckpoint(
        attempt_id="repair-1",
        parent_run_id="run-1",
        ticker="BABA",
        evidence_hash="evidence-1",
        stage="closure_checker",
        state="failed_terminal",
        attempt_count=1,
        started_at="2026-08-20T00:00:00+00:00",
        updated_at="2026-08-20T00:01:00+00:00",
        deadline_at="2026-08-20T00:05:00+00:00",
        failure_class="HybridResearchError",
        summary="Error finding codex home: Could not find home directory",
    )

    report = _bounded_freeze_report(
        "BABA",
        [decision],
        output_path=tmp_path / "BABA.json",
        selection_policy="test",
        repair_checkpoints=[checkpoint],
    )

    action = report["provider_action_required"]
    assert action["stage"] == "closure_checker"
    assert "signed-in desktop user context" in action["reason"]
    assert "no DeepSeek call is required" in action["next_action"]
    assert "--resume-run-id run-1" in action["resume_command"]


def test_freeze_report_labels_timeout_and_offers_hosted_closure_only_retry(
    tmp_path: Path,
) -> None:
    decision = DemoFreezeDecision(
        ticker="BLK",
        status="blocked",
        allowed=False,
        evaluated_at="2026-08-22T00:00:00+00:00",
        blockers=["Independent closure has not passed."],
        source_run_id="blk-run",
        publication_label="Not publication-ready",
    )
    checkpoint = RepairStageCheckpoint(
        attempt_id="repair-blk",
        parent_run_id="blk-run",
        ticker="BLK",
        evidence_hash="blk-evidence",
        stage="closure_checker",
        state="deadline_exceeded",
        attempt_count=1,
        started_at="2026-08-22T00:00:00+00:00",
        updated_at="2026-08-22T00:08:00+00:00",
        deadline_at="2026-08-22T00:08:00+00:00",
        provider="codex_local",
        model="gpt-5.6-sol",
        summary="Local Codex run exceeded 480 seconds.",
    )

    report = _bounded_freeze_report(
        "BLK",
        [decision],
        output_path=tmp_path / "BLK.json",
        selection_policy="test",
        repair_checkpoints=[checkpoint],
    )

    action = report["provider_action_required"]
    assert "timed out" in action["reason"]
    assert "no DeepSeek research call" in action["next_action"]
    assert "--executor codex_local" in action["resume_command"]
    assert "--retry-stage closure_checker" in action["resume_command"]


def test_terminal_generator_failure_preserves_artifact_and_writes_bounded_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "NVDA.json"
    output.write_text('{"existing": true}\n', encoding="utf-8")
    repository = InMemoryRepairStageCheckpointRepository()
    repository.save(RepairStageCheckpoint(
        attempt_id="repair-1",
        parent_run_id="parent-1",
        ticker="NVDA",
        evidence_hash="evidence-1",
        stage="primary_revision",
        state="failed_terminal",
        attempt_count=1,
        started_at="2026-08-10T00:00:00+00:00",
        updated_at="2026-08-10T00:01:00+00:00",
        deadline_at="2026-08-10T00:05:00+00:00",
        failure_class="RuntimeError",
        summary="Provider failed safely.",
    ))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value")
    report_path = tmp_path / "NVDA.freeze-report.json"

    _write_terminal_generator_failure(
        report_path,
        ticker="NVDA",
        output_path=output,
        exc=RuntimeError("provider rejected secret-value"),
        repair_attempt_id="repair-1",
        repair_repository=repository,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert output.read_text(encoding="utf-8") == '{"existing": true}\n'
    assert report["existing_public_artifact_preserved"] is True
    assert report["repair_stages"][0]["stage"] == "primary_revision"
    assert "secret-value" not in report_path.read_text(encoding="utf-8")


def test_missing_hosted_auditor_preserves_closure_retry_and_public_demo(
    tmp_path: Path,
) -> None:
    output = tmp_path / "BLK.json"
    output.write_text('{"published":"previous"}\n', encoding="utf-8")
    report_path = tmp_path / "BLK.freeze-report.json"
    report_path.write_text(
        json.dumps({"candidates": [{"source_run_id": "blk-parent"}]}) + "\n",
        encoding="utf-8",
    )
    repository = InMemoryRepairStageCheckpointRepository()

    _write_hosted_auditor_setup_required(
        report_path,
        ticker="BLK",
        output_path=output,
        resume_run_id="blk-parent",
        repair_attempt_id="repair-blk",
        repair_repository=repository,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert output.read_text(encoding="utf-8") == '{"published":"previous"}\n'
    assert report["candidates"] == [{"source_run_id": "blk-parent"}]
    assert report["provider_action_required"]["status"] == "Needs setup"
    assert "--retry-stage closure_checker" in (
        report["provider_action_required"]["resume_command"]
    )


def _freeze_decision(*, allowed: bool) -> SimpleNamespace:
    return SimpleNamespace(
        ticker="BABA",
        status="eligible" if allowed else "blocked",
        allowed=allowed,
        evaluated_at="2026-08-21T00:00:00+00:00",
        blockers=[] if allowed else ["Independent closure has not passed."],
        next_actions=[] if allowed else ["Rerun closure."],
        source_run_id="baba-run",
        source_run_completed_at="2026-08-21T00:00:00+00:00",
        policy_version="demo-quality-policy-v3",
        publication_gate_evaluation=None,
        repair_actions=[],
        publication_label=(
            "Publication-quality research"
            if allowed else "Not publication-ready"
        ),
        canonical_revision_id="revision-1",
        validation_status="passed",
        qa_closure_state="closed" if allowed else "failed",
        evidence_hash="baba-evidence",
        output_path="",
        sanitization_status="",
    )


def _patch_saved_candidate_dependencies(monkeypatch, *, allowed: bool):
    run = SimpleNamespace(
        ticker="BABA",
        run_id="baba-run",
        completed_at="2026-08-21T00:00:00+00:00",
        publishable_at="",
        published_at="",
        lifecycle_state="closure_pending",
        terminal_at="",
        demo_freeze_decision=None,
        research_ontology_version="research-ontology-v1",
        security_profile_id="china-internet-platform-v1",
        core_mechanism_ids=["cloud_ai_investment_cycle"],
    )
    decision = _freeze_decision(allowed=allowed)
    monkeypatch.setattr(
        demo_generator,
        "hybrid_run_from_payload",
        lambda _payload: run,
    )
    monkeypatch.setattr(
        demo_generator,
        "demo_result",
        lambda _ticker: SimpleNamespace(
            historical_analogs=SimpleNamespace(dossiers=[])
        ),
    )
    monkeypatch.setattr(
        demo_generator,
        "apply_hybrid_analog_results",
        lambda _report, _run: SimpleNamespace(dossiers=[]),
    )
    monkeypatch.setattr(
        demo_generator,
        "_evaluate_with_canonical_analog_downgrade",
        lambda *_args, **_kwargs: decision,
    )
    return run, decision


def test_failed_baba_closure_preserves_existing_public_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "BABA.json"
    original = b'{"published":"previous"}\n'
    output.write_bytes(original)
    _patch_saved_candidate_dependencies(monkeypatch, allowed=False)

    import pytest

    with pytest.raises(SystemExit, match="existing public artifact was preserved"):
        _freeze_saved_candidates(
            "BABA",
            [{"ticker": "BABA", "run_id": "baba-run"}],
            output_dir=tmp_path,
        )

    assert output.read_bytes() == original
    report = json.loads(
        (tmp_path / "BABA.freeze-report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "blocked"
    assert report["published_artifact_preserved"] is True


def test_eligible_baba_closure_atomically_replaces_public_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    output = tmp_path / "BABA.json"
    output.write_text('{"published":"previous"}\n', encoding="utf-8")
    run, _decision = _patch_saved_candidate_dependencies(
        monkeypatch, allowed=True
    )
    monkeypatch.setattr(
        demo_generator,
        "asdict",
        lambda _run: {
            "ticker": "BABA",
            "run_id": "baba-run",
            "lifecycle_state": run.lifecycle_state,
            "published_at": run.published_at,
        },
    )

    result = _freeze_saved_candidates(
        "BABA",
        [{"ticker": "BABA", "run_id": "baba-run"}],
        output_dir=tmp_path,
    )

    published = json.loads(output.read_text(encoding="utf-8"))
    report = json.loads(
        (tmp_path / "BABA.freeze-report.json").read_text(encoding="utf-8")
    )
    assert result == 0
    assert published["run_id"] == "baba-run"
    assert published["lifecycle_state"] == "published"
    assert report["status"] == "published"
    assert not (tmp_path / "BABA.json.tmp").exists()
