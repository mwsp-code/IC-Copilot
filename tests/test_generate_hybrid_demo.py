from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from equity_research.domain.publication_readiness import (
    PublicationGateEvaluation,
    PublicationGateFailure,
    PublicationQualification,
    ResearchRepairAction,
)
from equity_research.models import DemoFreezeDecision, LlmProviderProfile
from scripts.generate_hybrid_demo import (
    _assert_license_safe,
    _bounded_freeze_report,
    _resolve_deepseek_credentials,
)


class _Store:
    def __init__(
        self,
        profiles: list[LlmProviderProfile],
        primary_profile_id: str = "",
    ) -> None:
        self.profiles = profiles
        self.primary_profile_id = primary_profile_id

    def get_llm_selection(self) -> dict:
        return {"primary_profile_id": self.primary_profile_id}

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


def test_demo_generator_rejects_raw_licensed_payload_fields() -> None:
    _assert_license_safe({"summary": "sanitized paraphrase"})

    import pytest

    with pytest.raises(RuntimeError, match="licensed content"):
        _assert_license_safe({"licensed_payload": {"body": "private report"}})


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
