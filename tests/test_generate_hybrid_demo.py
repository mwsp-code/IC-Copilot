from __future__ import annotations

from dataclasses import replace

from equity_research.models import LlmProviderProfile
from scripts.generate_hybrid_demo import (
    _assert_license_safe,
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
