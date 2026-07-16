from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from equity_research import config
from equity_research.llm_vault import (
    build_llm_profile,
    delete_llm_profile_with_secret,
    list_llm_presets,
    profile_to_provider,
    save_llm_profile_with_secret,
    test_llm_profile as validate_llm_profile,
)
from equity_research.local_secrets import LLM_PROFILE_SECRET_PREFIX, LocalSecretsManager
from equity_research.research_store import ResearchStore
from equity_research.thesis_synthesis import OpenAICompatibleProvider


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


class LlmVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_db = config.RESEARCH_DB_PATH
        config.RESEARCH_DB_PATH = Path(self.temporary.name) / "research.db"
        self.store = ResearchStore()
        self.manager = LocalSecretsManager(backend=FakeKeyring())

    def tearDown(self) -> None:
        config.RESEARCH_DB_PATH = self.original_db
        self.temporary.cleanup()

    def test_deepseek_preset_builds_openai_compatible_provider(self) -> None:
        profile = save_llm_profile_with_secret(
            self.store,
            self.manager,
            display_name="DeepSeek cheap read",
            provider_preset="deepseek",
            api_key="secret-deepseek",
        )
        self.assertEqual(profile.base_url, "https://api.deepseek.com")
        self.assertEqual(profile.model, "deepseek-v4-pro")
        self.assertTrue(profile.secret_ref.startswith(LLM_PROFILE_SECRET_PREFIX))
        self.assertNotIn("secret-deepseek", str(self.store.list_llm_profiles()))
        provider = profile_to_provider(profile, self.manager)
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.provider_name, "deepseek")
        self.assertEqual(provider.base_url, "https://api.deepseek.com")

    def test_custom_provider_requires_base_url(self) -> None:
        with self.assertRaises(ValueError):
            save_llm_profile_with_secret(
                self.store,
                self.manager,
                display_name="Custom",
                provider_preset="custom_openai_compatible",
                model="model",
                api_key="secret",
            )

    def test_profile_test_uses_mocked_provider_and_redacts_key(self) -> None:
        profile = build_llm_profile(
            display_name="DeepSeek",
            provider_preset="deepseek",
            key_configured=True,
        )
        status = validate_llm_profile(
            profile,
            api_key="secret-deepseek",
            fetch_json=lambda url, payload, headers, timeout: {
                "choices": [{"message": {"content": "{\"verdict\":\"ok\",\"evidence_chain\":[]}"}}]
            },
        )
        self.assertEqual(status.status, "valid")
        self.assertNotIn("secret-deepseek", status.message)

    def test_profile_delete_removes_secret(self) -> None:
        profile = save_llm_profile_with_secret(
            self.store,
            self.manager,
            display_name="DeepSeek",
            provider_preset="deepseek",
            api_key="secret",
        )
        self.assertEqual(self.manager.get(profile.secret_ref), "secret")
        self.assertTrue(delete_llm_profile_with_secret(self.store, self.manager, profile.profile_id))
        self.assertIsNone(self.manager.get(profile.secret_ref))
        self.assertEqual(self.store.list_llm_profiles(), [])

    def test_presets_include_deepseek(self) -> None:
        ids = {preset.preset_id for preset in list_llm_presets()}
        self.assertIn("deepseek", ids)


if __name__ == "__main__":
    unittest.main()
