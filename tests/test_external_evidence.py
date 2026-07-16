from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from equity_research.external_evidence import (
    BlsMacroProvider,
    CensusMacroProvider,
    ExternalEvidenceStack,
    FredMacroProvider,
    KenFrenchFactorProvider,
    OfrMacroProvider,
    TreasuryMacroProvider,
    WisburgEvidenceProvider,
    WisburgMcpError,
    WorldBankMacroProvider,
    default_macro_source_settings,
)
from equity_research import config
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyIdentity,
    ExternalEvidence,
    ExternalEvidenceBundle,
)
from equity_research.research_store import ResearchStore


class ExternalEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_overrides = {
            "FRED_MACRO_OVERRIDE": config.FRED_MACRO_OVERRIDE,
            "BLS_MACRO_OVERRIDE": config.BLS_MACRO_OVERRIDE,
            "BEA_MACRO_OVERRIDE": config.BEA_MACRO_OVERRIDE,
            "CENSUS_MACRO_OVERRIDE": config.CENSUS_MACRO_OVERRIDE,
            "TREASURY_MACRO_OVERRIDE": config.TREASURY_MACRO_OVERRIDE,
            "OFR_MACRO_OVERRIDE": config.OFR_MACRO_OVERRIDE,
            "WORLD_BANK_MACRO_OVERRIDE": config.WORLD_BANK_MACRO_OVERRIDE,
            "IMF_MACRO_OVERRIDE": config.IMF_MACRO_OVERRIDE,
            "ENABLE_GDELT": config.ENABLE_GDELT,
        }
        for name in self.original_overrides:
            setattr(config, name, None if name != "ENABLE_GDELT" else False)

    def tearDown(self) -> None:
        for name, value in self.original_overrides.items():
            setattr(config, name, value)

    def test_default_policy_for_us_ticker_uses_safe_macro_sources(self) -> None:
        defaults = default_macro_source_settings(
            "AAPL",
            fred_api_key="",
            bea_api_key="",
            census_api_key="",
            enable_default_macro=True,
            global_macro_mode=False,
        )
        self.assertFalse(defaults["fred"])
        self.assertTrue(defaults["bls"])
        self.assertFalse(defaults["bea"])
        self.assertFalse(defaults["census"])
        self.assertTrue(defaults["treasury"])
        self.assertFalse(defaults["world_bank"])
        self.assertFalse(defaults["imf"])
        self.assertFalse(defaults["gdelt"])

    def test_default_policy_for_adr_enables_global_macro(self) -> None:
        defaults = default_macro_source_settings(
            "BABA",
            fred_api_key="",
            bea_api_key="",
            census_api_key="",
            enable_default_macro=True,
            global_macro_mode=False,
        )
        self.assertTrue(defaults["world_bank"])
        self.assertTrue(defaults["imf"])

    def test_world_bank_default_uses_generic_fpi_events(self) -> None:
        provider = WorldBankMacroProvider(
            enabled=None,
            enable_default_macro=True,
            global_macro_mode=False,
            fetch_json=lambda url, timeout: [
                {"page": 1},
                [
                    {"date": "2025", "value": 2.1},
                    {"date": "2024", "value": 1.8},
                ],
            ],
        )
        event = _event("2026-06-15")
        event.source = "20-F"
        package = provider.fetch(CompanyIdentity("ZZZ", "0000000000", "Foreign Issuer"), [event])
        self.assertEqual(package.status, "Available")

    def test_explicit_override_can_disable_default_source(self) -> None:
        config.BLS_MACRO_OVERRIDE = False
        defaults = default_macro_source_settings("AAPL", enable_default_macro=True)
        self.assertFalse(defaults["bls"])

    def test_fred_macro_provider_parses_point_in_time_change(self) -> None:
        provider = FredMacroProvider(
            api_key="demo",
            enabled=True,
            fetch_json=lambda url, timeout: {
                "observations": [
                    {"date": "2026-05-01", "value": "4.00"},
                    {"date": "2026-05-30", "value": "4.25"},
                ]
            },
        )
        package = provider.fetch(_identity(), [_event("2026-06-01")])
        self.assertEqual(package.status, "Available")
        item = package.evidence[0]
        self.assertEqual(item.metric_name, "DGS10")
        self.assertAlmostEqual(item.metric_value, 0.25)
        self.assertTrue(item.lookahead_safe)
        self.assertEqual(item.source_tier, 2)

    def test_bls_macro_provider_parses_monthly_series_without_key(self) -> None:
        def fetch(url, timeout):
            return {
                "Results": {
                    "series": [{
                        "data": [
                            {"year": "2026", "period": "M06", "value": "321.5"},
                            {"year": "2025", "period": "M06", "value": "315.0"},
                        ]
                    }]
                }
            }

        provider = BlsMacroProvider(enabled=True, fetch_json=fetch)
        package = provider.fetch(_identity(), [_event("2026-06-15")])
        self.assertEqual(package.status, "Available")
        cpi = package.evidence[0]
        self.assertEqual(cpi.provider, "BLS macro")
        self.assertEqual(cpi.source_as_of, "2026-06-01")
        self.assertAlmostEqual(cpi.metric_value, 6.5)

    def test_bls_macro_provider_skips_placeholder_values(self) -> None:
        def fetch(url, timeout):
            return {
                "Results": {
                    "series": [{
                        "data": [
                            {"year": "2026", "period": "M06", "value": "-"},
                            {"year": "2026", "period": "M05", "value": "320.0"},
                            {"year": "2025", "period": "M06", "value": "315.0"},
                        ]
                    }]
                }
            }

        provider = BlsMacroProvider(enabled=True, fetch_json=fetch)
        package = provider.fetch(_identity(), [_event("2026-06-15")])
        self.assertEqual(package.status, "Available")
        self.assertNotIn("could not convert", " ".join(package.data_gaps))

    def test_treasury_and_world_bank_sources_parse_public_json(self) -> None:
        treasury = TreasuryMacroProvider(
            enabled=True,
            fetch_json=lambda url, timeout: {
                "data": [
                    {"record_date": "2026-06-01", "avg_interest_rate_amt": "3.75"},
                    {"record_date": "2026-05-01", "avg_interest_rate_amt": "3.50"},
                ]
            },
        )
        treasury_package = treasury.fetch(_identity(), [_event("2026-06-15")])
        self.assertEqual(treasury_package.status, "Available")
        self.assertAlmostEqual(treasury_package.evidence[0].metric_value, 0.25)

        world_bank = WorldBankMacroProvider(
            enabled=True,
            fetch_json=lambda url, timeout: [
                {"page": 1},
                [
                    {"date": "2025", "value": 2.1},
                    {"date": "2024", "value": 1.8},
                ],
            ],
        )
        world_bank_package = world_bank.fetch(_identity(), [_event("2026-06-15")])
        self.assertEqual(world_bank_package.status, "Available")
        self.assertAlmostEqual(world_bank_package.evidence[0].metric_value, 0.3)

    def test_ken_french_provider_parses_latest_point_in_time_factor_rows(self) -> None:
        def fetch_bytes(url, timeout):
            if "Momentum" in url:
                return _zip_csv("Header\n,Mom\n20260530,1.10\n20260605,2.00\n")
            if "ST_Reversal" in url:
                return _zip_csv("Header\n,ST_Rev\n20260530,-0.40\n20260605,0.20\n")
            return _zip_csv("Header\n,Mkt-RF,SMB,HML,RMW,CMA,RF\n20260530,0.50,0.10,-0.20,0.30,-0.10,0.01\n20260605,2.00,2.00,2.00,2.00,2.00,0.01\n")

        provider = KenFrenchFactorProvider(fetch_bytes=fetch_bytes)
        package = provider.fetch(_identity(), [_event("2026-06-01")])
        self.assertEqual(package.status, "Available")
        factors = {item.metric_name: item.metric_value for item in package.evidence}
        self.assertEqual(factors["Market excess return"], 0.50)
        self.assertEqual(factors["Momentum"], 1.10)
        self.assertEqual(factors["Short-term reversal"], -0.40)
        self.assertTrue(all(item.source_as_of == "2026-05-30" for item in package.evidence))
        self.assertTrue(all(item.lookahead_safe for item in package.evidence))

    def test_disabled_provider_returns_explicit_health(self) -> None:
        package = FredMacroProvider(api_key="", enabled=False).fetch(_identity(), [_event()])
        self.assertEqual(package.status, "Unavailable")
        self.assertEqual(package.provider_statuses[0].entitlement_status, "disabled")

    def test_census_forced_without_key_returns_missing_key(self) -> None:
        package = CensusMacroProvider(api_key="", enabled=True).fetch(_identity(), [_event()])
        self.assertEqual(package.status, "Unavailable")
        self.assertEqual(package.provider_statuses[0].entitlement_status, "missing_key")

    def test_ofr_enabled_without_mapped_series_is_explicit(self) -> None:
        package = OfrMacroProvider(enabled=True).fetch(_identity(), [_event()])
        self.assertEqual(package.status, "Unavailable")
        self.assertEqual(package.provider_statuses[0].entitlement_status, "series_not_mapped")

    def test_macro_observations_are_persisted_without_raw_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            package = ExternalEvidenceBundle(
                "AAPL",
                "Available",
                [
                    ExternalEvidence(
                        provider="FRED/ALFRED macro",
                        source_type="macro_factor",
                        title="10-year Treasury yield",
                        summary="Yield changed.",
                        observed_at="2026-06-02T00:00:00+00:00",
                        source_as_of="2026-05-30",
                        source_tier=2,
                        official=True,
                        confidence="Medium",
                        metric_name="DGS10",
                        metric_value=0.25,
                        unit="percent",
                        frequency="daily",
                        release_date="2026-05-30",
                        vintage_date="2026-05-30",
                        lookahead_safe=True,
                        citation=Citation("FRED", "https://fred.test/DGS10", source_tier=2),
                        tags=["macro", "rates"],
                    )
                ],
                [],
                [],
            )
            store.save_external_evidence(package)
            health = store.macro_health("AAPL")
            self.assertEqual(health["observations"][0]["series_id"], "DGS10")
            self.assertEqual(health["observations"][0]["citation"]["source"], "FRED")
            self.assertIn(health["observations"][0]["cache_status"], {"same_day", "historical"})

    def test_external_stack_reuses_same_day_macro_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            store.save_external_evidence(ExternalEvidenceBundle(
                "AAPL",
                "Available",
                [_macro_item(observed_at=_today_observed_at())],
                [],
                [],
            ))
            provider = _FailingMacroProvider()
            stack = ExternalEvidenceStack([provider], store=store)
            package = stack.fetch(_identity(), [_event()])
            self.assertEqual(package.status, "Available")
            self.assertEqual(package.evidence[0].metric_name, "DGS10")
            self.assertEqual(package.provider_statuses[0].entitlement_status, "cached")
            self.assertEqual(provider.calls, 0)

    def test_wisburg_provider_normalizes_external_context_without_raw_payloads(self) -> None:
        client = _FakeWisburgClient({
            "list-company-reports": (
                "Found 2 reports:\n\n"
                "[88591] 阿里巴巴集团 (BABA US)：买入：AI商业化拐点\n"
                "  date: 2026-05-14T13:31:53+08:00\n"
                "  **AI商业化拐点**，云业务利润率有望改善，目标价上调。\n\n"
                "[89585] Alibaba Group: Cloud and AI update\n"
                "  date: 2026-05-21T21:40:56+08:00\n"
                "  Cloud became a focus while ecommerce remained soft.\n"
            ),
            "list-earning-calls": "No earning call transcripts found matching the criteria.",
        })
        provider = WisburgEvidenceProvider(api_key="secret", enabled=True, client=client)
        package = provider.fetch(CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Ltd"), [_event()])
        self.assertIn(package.status, {"Available", "Partial"})
        self.assertTrue(any(item.source_type == "external_analyst_context" for item in package.evidence))
        self.assertTrue(any(item.source_type == "narrative_saturation" for item in package.evidence))
        analyst = next(item for item in package.evidence if item.source_type == "external_analyst_context")
        self.assertFalse(analyst.official)
        self.assertTrue(analyst.disqualifies_high_conviction)
        self.assertEqual(analyst.licensing_policy, "metadata_and_excerpt_only")
        self.assertIn("zh", analyst.tags)
        self.assertNotIn("secret", str(package))
        narrative = next(item for item in package.evidence if item.source_type == "narrative_saturation")
        self.assertEqual(narrative.metric_name, "wisburg_research_item_count")
        self.assertGreaterEqual(narrative.metric_value or 0, 2)

    def test_wisburg_provider_reports_unauthorized_without_leaking_key(self) -> None:
        provider = WisburgEvidenceProvider(
            api_key="secret-wisburg",
            enabled=True,
            client=_FailingWisburgClient("HTTP 401 secret-wisburg", "unauthorized"),
        )
        package = provider.fetch(_identity(), [_event()])
        self.assertEqual(package.status, "Unavailable")
        self.assertEqual(package.provider_statuses[0].entitlement_status, "unauthorized")
        self.assertNotIn("secret-wisburg", str(package))

    def test_wisburg_disabled_and_missing_key_are_explicit(self) -> None:
        disabled = WisburgEvidenceProvider(api_key="secret", enabled=False).fetch(_identity(), [_event()])
        self.assertEqual(disabled.provider_statuses[0].entitlement_status, "disabled")
        missing = WisburgEvidenceProvider(api_key="", enabled=True).fetch(_identity(), [_event()])
        self.assertEqual(missing.provider_statuses[0].entitlement_status, "missing_key")


def _identity() -> CompanyIdentity:
    return CompanyIdentity("AAPL", "0000320193", "Apple Inc.")


def _event(event_date: str = "2026-06-01") -> ChangeEvent:
    return ChangeEvent(
        "macro_test",
        "Macro test event",
        "Fixture event.",
        3,
        "positive",
        event_date,
        "Fixture",
    )


def _macro_item(observed_at: str = "2026-06-02T00:00:00+00:00") -> ExternalEvidence:
    return ExternalEvidence(
        provider="FRED/ALFRED macro",
        source_type="macro_factor",
        title="10-year Treasury yield",
        summary="Yield changed.",
        observed_at=observed_at,
        source_as_of="2026-05-30",
        source_tier=2,
        official=True,
        confidence="Medium",
        metric_name="DGS10",
        metric_value=0.25,
        unit="percent",
        frequency="daily",
        release_date="2026-05-30",
        vintage_date="2026-05-30",
        lookahead_safe=True,
        citation=Citation("FRED", "https://fred.test/DGS10", source_tier=2),
        tags=["macro", "rates"],
    )


def _zip_csv(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("factors.csv", text)
    return buffer.getvalue()


def _today_observed_at() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _FailingMacroProvider:
    provider_name = "FRED/ALFRED macro"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, identity, events):
        self.calls += 1
        raise AssertionError("Provider should not be called when same-day cache exists.")


class _FakeWisburgClient:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def list_tool(self, tool_name: str, query: str, first: int) -> dict:
        self.calls.append((tool_name, query))
        text = self.responses.get(tool_name, "No reports found matching the criteria.")
        return {"content": [{"type": "text", "text": text}]}


class _FailingWisburgClient:
    def __init__(self, message: str, entitlement_status: str) -> None:
        self.message = message
        self.entitlement_status = entitlement_status

    def list_tool(self, tool_name: str, query: str, first: int) -> dict:
        raise WisburgMcpError(self.message.replace("secret-wisburg", "[redacted]"), self.entitlement_status)


if __name__ == "__main__":
    unittest.main()
