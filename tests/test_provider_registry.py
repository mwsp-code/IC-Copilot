from __future__ import annotations

from equity_research.infrastructure.provider_registry import (
    ProviderResolutionRequest,
    resolve_research_providers,
)
from equity_research.llm_vault import build_llm_profile


class Secrets:
    def __init__(self, values):
        self.values = values

    def get(self, key):
        return self.values.get(key, "")


def _request(primary, secondary, secrets, **overrides):
    values = {
        "ticker": "AAPL",
        "research_profile": "adaptive_ic",
        "primary_profile_id": primary.profile_id,
        "primary_profile": primary,
        "secondary_profile": secondary,
        "secrets_manager": secrets,
        "enable_llm": True,
        "enable_secondary_review": True,
        "enable_independent_qa": True,
        "auditor_backend_id": "configured_secondary",
        "configured_secondary_available": True,
        "local_codex_status": "not_installed",
        "primary_credential_source": "local provider vault",
        "auditor_credential_source": "selected secondary provider profile",
    }
    values.update(overrides)
    return ProviderResolutionRequest(**values)


def test_registry_resolves_flexible_distinct_primary_and_auditor_profiles() -> None:
    primary = build_llm_profile(
        display_name="DeepSeek primary",
        provider_preset="deepseek",
        profile_id="primary",
    )
    secondary = build_llm_profile(
        display_name="OpenAI auditor",
        provider_preset="openai_sol",
        profile_id="secondary",
    )
    secrets = Secrets({
        primary.secret_ref: "deepseek-key",
        secondary.secret_ref: "openai-key",
    })

    resolved = resolve_research_providers(
        _request(primary, secondary, secrets)
    )

    assert resolved.primary_research.provider_name == "deepseek"
    assert resolved.auditor.provider_name == "openai_sol"
    assert resolved.preflight.status == "ready"
    assert resolved.qa_relationship == "independent"


def test_duplicate_provider_endpoint_and_model_is_same_model_second_pass() -> None:
    primary = build_llm_profile(
        display_name="Primary row",
        provider_preset="deepseek",
        profile_id="primary",
    )
    secondary = build_llm_profile(
        display_name="Second row",
        provider_preset="deepseek",
        profile_id="secondary",
    )
    secrets = Secrets({
        primary.secret_ref: "first-key",
        secondary.secret_ref: "second-key",
    })

    resolved = resolve_research_providers(
        _request(primary, secondary, secrets)
    )

    assert resolved.preflight.status == "ready"
    assert resolved.qa_relationship == "same-model second pass"


def test_missing_optional_auditor_does_not_block_primary_research() -> None:
    primary = build_llm_profile(
        display_name="DeepSeek primary",
        provider_preset="deepseek",
        profile_id="primary",
    )
    secondary = build_llm_profile(
        display_name="Unconfigured auditor",
        provider_preset="openai_sol",
        profile_id="secondary",
    )
    secrets = Secrets({primary.secret_ref: "deepseek-key"})

    resolved = resolve_research_providers(
        _request(
            primary,
            secondary,
            secrets,
            configured_secondary_available=False,
            auditor_fallback_key="",
        )
    )

    assert resolved.preflight.status == "ready"
    assert resolved.preflight.sol.status == "missing"
    assert resolved.qa_relationship == "not_run"
