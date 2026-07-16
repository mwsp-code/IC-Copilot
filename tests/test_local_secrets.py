from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from equity_research.local_secrets import (
    DpapiFileBackend,
    LLM_PROFILE_SECRET_PREFIX,
    LocalSecretsManager,
    ValidationResult,
    save_validated_keys,
    validate_provider_keys,
)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password

    def delete_password(self, service_name: str, username: str) -> None:
        self.values.pop((service_name, username), None)


class LocalSecretsTests(unittest.TestCase):
    def tearDown(self) -> None:
        for key in (
            "ALPHAVANTAGE_API_KEY",
            "FINNHUB_API_KEY",
            "FMP_API_KEY",
            "FRED_API_KEY",
            "TIINGO_API_KEY",
            "EODHD_API_KEY",
            "WISBURG_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "QWEN_API_KEY",
            "KIMI_API_KEY",
            "DEEPSEEK_API_KEY",
            "LOCAL_SECRET_TEST",
        ):
            os.environ.pop(key, None)

    def test_keyring_set_get_status_and_delete_are_redacted(self) -> None:
        backend = FakeKeyring()
        manager = LocalSecretsManager(backend=backend)
        manager.set("ALPHAVANTAGE_API_KEY", "secret-alpha")
        manager.set("FMP_API_KEY", "secret-fmp")
        manager.set("WISBURG_API_KEY", "secret-wisburg")
        manager.set("TIINGO_API_KEY", "secret-tiingo")
        manager.set("EODHD_API_KEY", "secret-eodhd")
        manager.set("OPENAI_API_KEY", "secret-openai")
        self.assertEqual(manager.get("ALPHAVANTAGE_API_KEY"), "secret-alpha")
        self.assertEqual(manager.get("FMP_API_KEY"), "secret-fmp")
        self.assertEqual(manager.get("WISBURG_API_KEY"), "secret-wisburg")
        self.assertEqual(manager.get("TIINGO_API_KEY"), "secret-tiingo")
        self.assertEqual(manager.get("EODHD_API_KEY"), "secret-eodhd")
        self.assertEqual(manager.get("OPENAI_API_KEY"), "secret-openai")
        status = manager.redacted_status()
        self.assertTrue(any(item["key"] == "ALPHAVANTAGE_API_KEY" and item["configured"] for item in status))
        self.assertTrue(any(item["key"] == "FMP_API_KEY" and item["configured"] for item in status))
        self.assertTrue(any(item["key"] == "WISBURG_API_KEY" and item["configured"] for item in status))
        self.assertTrue(any(item["key"] == "TIINGO_API_KEY" and item["configured"] for item in status))
        self.assertTrue(any(item["key"] == "EODHD_API_KEY" and item["configured"] for item in status))
        self.assertTrue(any(item["key"] == "OPENAI_API_KEY" and item["configured"] for item in status))
        self.assertNotIn("secret-alpha", str(status))
        self.assertNotIn("secret-fmp", str(status))
        self.assertNotIn("secret-wisburg", str(status))
        self.assertNotIn("secret-tiingo", str(status))
        self.assertNotIn("secret-eodhd", str(status))
        self.assertNotIn("secret-openai", str(status))
        manager.delete("ALPHAVANTAGE_API_KEY")
        manager.delete("FMP_API_KEY")
        manager.delete("WISBURG_API_KEY")
        manager.delete("TIINGO_API_KEY")
        manager.delete("EODHD_API_KEY")
        manager.delete("OPENAI_API_KEY")
        self.assertIsNone(manager.get("ALPHAVANTAGE_API_KEY"))
        self.assertIsNone(manager.get("FMP_API_KEY"))
        self.assertIsNone(manager.get("WISBURG_API_KEY"))
        self.assertIsNone(manager.get("TIINGO_API_KEY"))
        self.assertIsNone(manager.get("EODHD_API_KEY"))
        self.assertIsNone(manager.get("OPENAI_API_KEY"))

    def test_environment_precedence_beats_keychain(self) -> None:
        backend = FakeKeyring()
        manager = LocalSecretsManager(backend=backend)
        manager.set("FRED_API_KEY", "keychain-fred")
        os.environ["FRED_API_KEY"] = "env-fred"
        loaded = manager.load_into_environment({"FRED_API_KEY"})
        self.assertEqual(loaded, {})
        self.assertEqual(os.environ["FRED_API_KEY"], "env-fred")
        status = manager.redacted_status({"FRED_API_KEY"})
        fred = next(item for item in status if item["key"] == "FRED_API_KEY")
        self.assertEqual(fred["source"], "environment")

    def test_invalid_key_name_is_rejected(self) -> None:
        manager = LocalSecretsManager(backend=FakeKeyring())
        with self.assertRaises(ValueError):
            manager.set("UNSUPPORTED_KEY", "value")

    def test_profile_scoped_llm_secret_refs_are_allowed(self) -> None:
        manager = LocalSecretsManager(backend=FakeKeyring())
        secret_ref = f"{LLM_PROFILE_SECRET_PREFIX}profile-1"
        manager.set(secret_ref, "secret-profile-key")
        self.assertEqual(manager.get(secret_ref), "secret-profile-key")
        self.assertFalse(any(item["key"] == secret_ref for item in manager.redacted_status()))
        manager.delete(secret_ref)
        self.assertIsNone(manager.get(secret_ref))

    def test_missing_backend_reports_unavailable(self) -> None:
        manager = LocalSecretsManager(backend=None)
        self.assertFalse(manager.backend_available)
        status = manager.redacted_status()
        self.assertTrue(any(not item["backend_available"] for item in status))

    def test_dpapi_file_backend_round_trips_without_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            vault_path = Path(tmp) / "vault.json"
            backend = DpapiFileBackend(
                vault_path,
                protect=lambda data: data[::-1],
                unprotect=lambda data: data[::-1],
            )
            manager = LocalSecretsManager(backend=backend)
            manager.set("FINNHUB_API_KEY", "secret-finnhub")
            self.assertEqual(manager.get("FINNHUB_API_KEY"), "secret-finnhub")
            self.assertNotIn("secret-finnhub", vault_path.read_text(encoding="utf-8"))
            status = manager.redacted_status()
            finnhub = next(item for item in status if item["key"] == "FINNHUB_API_KEY")
            self.assertEqual(finnhub["backend"], "windows_dpapi")
            manager.delete("FINNHUB_API_KEY")
            self.assertIsNone(manager.get("FINNHUB_API_KEY"))

    def test_save_validated_keys_only_saves_valid_provider_keys(self) -> None:
        backend = FakeKeyring()
        manager = LocalSecretsManager(backend=backend)
        outcome = save_validated_keys(
            manager,
            {
                "ALPHAVANTAGE_API_KEY": "valid-alpha",
                "FMP_API_KEY": "valid-fmp",
                "FRED_API_KEY": "bad-fred",
                "WISBURG_API_KEY": "local-wisburg",
                "TIINGO_API_KEY": "valid-tiingo",
                "EODHD_API_KEY": "valid-eodhd",
                "OPENAI_API_KEY": "local-openai",
            },
            [
                ValidationResult("ALPHAVANTAGE_API_KEY", "Alpha Vantage", "valid", "ok"),
                ValidationResult("FMP_API_KEY", "FMP", "valid", "ok"),
                ValidationResult("FRED_API_KEY", "FRED", "invalid_key", "bad"),
                ValidationResult("TIINGO_API_KEY", "Tiingo", "valid", "ok"),
                ValidationResult("EODHD_API_KEY", "EODHD", "valid", "ok"),
            ],
        )
        self.assertEqual(outcome["saved"], ["ALPHAVANTAGE_API_KEY", "FMP_API_KEY", "WISBURG_API_KEY", "TIINGO_API_KEY", "EODHD_API_KEY", "OPENAI_API_KEY"])
        self.assertEqual(outcome["skipped"], ["FRED_API_KEY"])
        self.assertEqual(manager.get("ALPHAVANTAGE_API_KEY"), "valid-alpha")
        self.assertEqual(manager.get("FMP_API_KEY"), "valid-fmp")
        self.assertEqual(manager.get("WISBURG_API_KEY"), "local-wisburg")
        self.assertEqual(manager.get("TIINGO_API_KEY"), "valid-tiingo")
        self.assertEqual(manager.get("EODHD_API_KEY"), "valid-eodhd")
        self.assertEqual(manager.get("OPENAI_API_KEY"), "local-openai")
        self.assertIsNone(manager.get("FRED_API_KEY"))

    def test_validation_redacts_secret_on_provider_errors(self) -> None:
        def fetch(url, timeout):
            raise OSError("failed with secret-alpha in URL")

        results = validate_provider_keys({"ALPHAVANTAGE_API_KEY": "secret-alpha"}, fetch_json=fetch)
        self.assertEqual(results[0].status, "network_error")
        self.assertNotIn("secret-alpha", results[0].message)

    def test_validation_fixture_responses(self) -> None:
        def fetch(url, timeout):
            if "alphavantage" in url:
                return {"Symbol": "AAPL"}
            if "finnhub" in url:
                return []
            if "financialmodelingprep" in url:
                return [{"symbol": "AAPL", "targetConsensus": 225}]
            if "stlouisfed" in url:
                return {"observations": []}
            if "bea.gov" in url:
                return {"BEAAPI": {"Results": {}}}
            if "census.gov" in url:
                return [["cell_value", "time"], ["1", "2026-01"]]
            if "tiingo.com" in url:
                return [{"date": "2026-01-02T00:00:00.000Z", "adjClose": 100.0}]
            if "eodhd.com" in url:
                return [{"date": "2026-01-02", "adjusted_close": 100.0}]
            return {}

        results = validate_provider_keys(
            {
                "ALPHAVANTAGE_API_KEY": "a",
                "FINNHUB_API_KEY": "f",
                "FMP_API_KEY": "m",
                "FRED_API_KEY": "r",
                "BEA_API_KEY": "b",
                "CENSUS_API_KEY": "c",
                "TIINGO_API_KEY": "t",
                "EODHD_API_KEY": "e",
            },
            fetch_json=fetch,
        )
        self.assertEqual({item.status for item in results}, {"valid"})


if __name__ == "__main__":
    unittest.main()
