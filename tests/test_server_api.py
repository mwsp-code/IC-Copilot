from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import server
from equity_research import config
from equity_research.local_secrets import ValidationResult
from equity_research.models import (
    AttributionWaterfall,
    ConsensusPackage,
    Citation,
    DriverAttribution,
    DailySnapshotStatus,
    EventWindowReaction,
    ExternalEvidence,
    ExternalEvidenceBundle,
    NetworkDiagnosticReport,
    NetworkProbeStatus,
    PriceProviderStatus,
    ProviderObservation,
    TargetConsensus,
    WisburgSnapshotDelta,
)
from equity_research.research_store import ResearchStore
from equity_research.sample_data import demo_result


class ServerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.original_db = config.RESEARCH_DB_PATH
        self.original_secret_manager = server.LocalSecretsManager
        self.original_validate_provider_keys = server.validate_provider_keys
        self.original_save_validated_keys = server.save_validated_keys
        config.RESEARCH_DB_PATH = Path(self.temporary.name) / "research.db"
        server.LocalSecretsManager = lambda: FakeSecretsManager()
        server.validate_provider_keys = fake_validate_provider_keys
        server.save_validated_keys = fake_save_validated_keys
        self.store = ResearchStore()
        self.store.save_consensus_package(ConsensusPackage(
            "AAPL", "Test", "Available",
            target=TargetConsensus("AAPL", "2026-06-24", target_mean=250, source="Test"),
            observations=[ProviderObservation(
                "AAPL", "Test", "target_mean", "2026-06-24T01:00:00+00:00",
                "2026-06-24", value_numeric=250, currency="USD",
                provenance="Fixture", official=True,
            )],
        ))
        self.store.set_provider_health("Test", "Available", "Fixture provider available.")
        self.store.set_provider_health("FRED/ALFRED macro", "Available", "Fixture macro available.")
        self.store.save_external_evidence(ExternalEvidenceBundle(
            "AAPL",
            "Available",
            [
                ExternalEvidence(
                    "FRED/ALFRED macro",
                    "macro_factor",
                    "10-year Treasury yield",
                    "Yield changed.",
                    "2026-06-25T00:00:00+00:00",
                    "2026-06-24",
                    2,
                    True,
                    "Medium",
                    metric_name="DGS10",
                    metric_value=0.1,
                    unit="percent",
                    frequency="daily",
                    release_date="2026-06-24",
                    vintage_date="2026-06-24",
                    lookahead_safe=True,
                    citation=Citation("FRED", "https://fred.test/DGS10", source_tier=2),
                    tags=["macro", "rates"],
                )
            ],
            [],
            [],
        ))
        self.store.save_price_provider_status(PriceProviderStatus(
            "AAPL", "CSV daily prices", "available", "2026-06-25T00:00:00+00:00",
            "Fixture prices", official=False, adjusted=True,
        ))
        self.store.save_price_bars(
            "AAPL", "CSV daily prices",
            [{"date": "2026-06-24", "close": 200.0, "volume": 1000}],
            adjusted=True, official=False, source_url="fixture.csv",
        )
        self.store.save_event_reactions([EventWindowReaction(
            "AAPL", "event-1", "2026-06-24", None, "2026-06-25", 200.0,
            "CSV daily prices", "window_pending", "Forward event windows pending.",
            raw_returns={"1d": 1.0, "5d": None, "20d": None},
        )])
        self.demo = demo_result("AAPL")
        self.store.save_wisburg_lens(self.demo.wisburg_lens)
        self.store.save_wisburg_delta(WisburgSnapshotDelta(
            ticker="AAPL",
            status="Baseline",
            observed_at="2026-06-25T00:00:00+00:00",
            summary="Fixture Wisburg baseline.",
        ))
        self.store.save_daily_snapshot_status(DailySnapshotStatus(
            ticker="AAPL",
            run_date="2026-06-25",
            observed_at="2026-06-25T00:00:00+00:00",
            overall_status="Partial",
            consensus_status="Available",
            price_status="Available",
            wisburg_status="Unavailable",
        ))
        self.store.save_research_run("AAPL", self.demo.run_manifest)
        self.store.save_entity_coverage(
            "fixture-run", self.demo.entity_resolution, self.demo.financial_coverage,
        )
        self.store.save_peer_universe(self.demo.peer_universe)
        self.store.save_management_sources(
            "fixture-run", self.demo.management_sources,
        )
        self.store.save_validated_claims(
            self.demo.run_manifest.run_id, "AAPL", self.demo.validated_claims.claims,
        )
        self.store.save_source_plan(self.demo.run_manifest.run_id, self.demo.source_plan)
        self.store.save_llm_research_manifest(
            self.demo.run_manifest.run_id,
            "AAPL",
            self.demo.llm_research_manifest,
        )
        self.store.save_decision_artifacts(
            self.demo.run_manifest.run_id,
            "AAPL",
            self.demo.evidence_closure,
            self.demo.causal_thesis_graphs,
            self.demo.market_implied_expectations,
            self.demo.company_model,
            self.demo.research_modes,
        )
        if self.demo.ideas:
            self.demo.ideas[0].stage = "Candidate"
            self.demo.ideas[0].gate_result.eligible = True
            self.demo.ideas[0].gate_result.research_ready = True
            self.demo.ideas[0].gate_result.high_conviction = True
            self.demo.ideas[0].gate_result.research_ready_failed = []
            self.demo.ideas[0].gate_result.high_conviction_failed = []
            self.demo.ideas[0].driver_attribution = DriverAttribution(
                "Available",
                "Company-specific",
                "Fixture attribution.",
                "Medium",
                "2026-06-24",
                return_window="1d",
                raw_return_pct=1.0,
                residual_pct=1.0,
                waterfall=AttributionWaterfall(
                    "Available", "1d", 1.0, 0.0, 1.0, 0.0, [], [],
                ),
            )
        self.store.save_idea_versions("AAPL", self.demo.run_manifest.run_id, self.demo.ideas)
        self.httpd = server.ReusableThreadingTCPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        config.RESEARCH_DB_PATH = self.original_db
        server.LocalSecretsManager = self.original_secret_manager
        server.validate_provider_keys = self.original_validate_provider_keys
        server.save_validated_keys = self.original_save_validated_keys
        self.temporary.cleanup()

    def test_consensus_watchlist_and_alert_routes(self) -> None:
        consensus = self._request("/api/consensus?ticker=AAPL")
        self.assertEqual(consensus["consensus"]["target"]["target_mean"], 250)

        created = self._request(
            "/api/watchlist", method="POST", payload={"ticker": "AAPL"},
        )
        self.assertEqual(created["watchlist"][0]["ticker"], "AAPL")
        listed = self._request("/api/watchlist")
        self.assertEqual(len(listed["watchlist"]), 1)

        alert = self.store.create_alert("AAPL", "test", "Test alert", "Message", 3, "AAPL:test")
        read = self._request(f"/api/alerts/{alert.alert_id}/read", method="POST")
        self.assertTrue(read["updated"])
        alerts = self._request("/api/alerts?status=read")
        self.assertEqual(alerts["alerts"][0]["status"], "read")

        removed = self._request("/api/watchlist?ticker=AAPL", method="DELETE")
        self.assertEqual(removed["watchlist"], [])

    def test_demo_payload_exposes_new_research_sections(self) -> None:
        demo_cases = self._request("/api/demo-cases")["demo_cases"]
        self.assertTrue(any(item["ticker"] == "BABA" for item in demo_cases))
        response = self._request("/api/demo?ticker=BABA")
        self.assertEqual(response["demo_case"]["ticker"], "BABA")
        payload = response["result"]
        for field in (
            "consensus",
            "expectations_bridge",
            "valuation",
            "watchlist_status",
            "active_alerts",
            "management_sources",
            "thesis_brief",
            "thesis_critique",
            "evidence_sufficiency",
            "action_plan",
            "llm_run_manifest",
            "llm_reviews",
            "llm_comparison",
            "language_audit",
            "historical_references",
            "conviction_audit",
            "thesis_validation",
            "budget_policy",
            "manual_data_status",
            "company_economics",
            "thesis_clusters",
            "validated_claims",
            "source_plan",
            "market_capture_readiness",
            "llm_extraction_manifest",
            "llm_research_manifest",
            "wisburg_lens",
            "ic_one_pager",
            "demo_case",
            "run_progress",
            "story_cards",
            "bull_bear_judge",
            "formula_traces",
            "evidence_closure",
            "causal_thesis_graphs",
            "market_implied_expectations",
            "earnings_surprise_proxy",
            "recent_market_context",
            "company_model",
            "research_modes",
            "research_profile",
            "historical_research",
            "metric_assessments",
            "promotion_evidence",
            "promotion_decisions",
            "playbook_portfolio",
        ):
            self.assertIn(field, payload)
        self.assertIn(payload["llm_run_manifest"]["status"], {"Available", "Skipped"})
        self.assertIn("guardrail_checks", payload["llm_run_manifest"])
        self.assertGreater(payload["llm_run_manifest"]["guardrail_score"], 0)
        self.assertIn("allowed_roles", payload["llm_research_manifest"])
        self.assertIn("source_planning_from_registered_source_types", payload["llm_research_manifest"]["allowed_roles"])
        self.assertIn("promoting_candidates_or_overriding_deterministic_gates", payload["llm_research_manifest"]["prohibited_actions"])
        self.assertIn("no_high_conviction_without_evidence_gates", payload["llm_research_manifest"]["validation_gates"])
        self.assertIn("decision", payload["ic_one_pager"])
        self.assertTrue(payload["ic_one_pager"]["next_best_action"])
        self.assertIn("playbook_quality", payload["company_economics"])
        self.assertGreaterEqual(payload["company_economics"]["playbook_quality_score"], 0)
        self.assertTrue(payload["events"][0]["why_this_matters"])
        self.assertIn("point-in-time", payload["market_capture_readiness"]["point_in_time_rule"])
        self.assertIn("import_plan", payload["market_capture_readiness"])
        self.assertIn("import_command", payload["market_capture_readiness"]["import_plan"])
        self.assertTrue(payload["run_progress"]["stages"])
        self.assertTrue(payload["story_cards"])
        self.assertTrue(payload["formula_traces"])
        self.assertEqual(len(payload["research_modes"]["modes"]), 8)

    def test_decision_artifact_routes(self) -> None:
        routes = {
            "/api/evidence-closure?ticker=AAPL": "evidence_closure",
            "/api/causal-thesis-graph?ticker=AAPL": "causal_thesis_graph",
            "/api/market-implied?ticker=AAPL": "market_implied",
            "/api/company-model?ticker=AAPL": "company_model",
            "/api/research-modes?ticker=AAPL": "research_modes",
        }
        for route, field in routes.items():
            response = self._request(route)
            self.assertEqual(response["ticker"], "AAPL")
            self.assertIn(field, response)

    def test_market_implied_assumption_routes_save_load_and_reset(self) -> None:
        saved = self._request(
            "/api/market-implied-assumptions",
            method="POST",
            payload={
                "ticker": "AAPL",
                "assumptions": {
                    "discount_rate_pct": 11.5,
                    "terminal_growth_pct": 2.5,
                    "forecast_years": 6,
                },
            },
        )
        self.assertTrue(saved["recalculate_on_next_research_run"])
        loaded = self._request("/api/market-implied-assumptions?ticker=AAPL")
        self.assertEqual(loaded["assumptions"]["discount_rate_pct"], 11.5)
        cleared = self._request(
            "/api/market-implied-assumptions?ticker=AAPL", method="DELETE",
        )
        self.assertTrue(cleared["deleted"])
        self.assertEqual(
            self._request("/api/market-implied-assumptions?ticker=AAPL")["assumptions"],
            {},
        )

    def test_lightweight_ui_exposes_outcome_post_mortem_form(self) -> None:
        request = Request(f"http://127.0.0.1:{self.port}/")
        with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
            html = response.read().decode("utf-8")
        self.assertIn("Reverse FCF / DCF Snapshot", html)

        self.assertIn("Save Outcome For Calibration", html)
        self.assertIn("/api/ideas/", html)
        self.assertIn("/outcome", html)
        self.assertIn("Outcome Calibration", html)
        self.assertIn("Rank By EV", html)
        self.assertIn("Calibration Readiness Checklist", html)
        self.assertIn("LLM Research Assistant Policy", html)
        self.assertIn("LLM Guardrail Checklist", html)
        self.assertIn("Allowed role", html)
        self.assertIn("Consensus Import Plan", html)
        self.assertIn("Adaptive IC Research", html)
        self.assertIn("Investigate This Event", html)
        self.assertIn("Event-Specific Expectations Audit", html)
        self.assertIn("Promotion evidence audit", html)
        self.assertIn("Edit reverse-model assumptions", html)

    def test_research_profile_api_exposes_adaptive_default(self) -> None:
        payload = self._request("/api/research-profiles")
        self.assertEqual(payload["default"], "adaptive_ic")
        profiles = {item["profile_id"]: item for item in payload["profiles"]}
        self.assertEqual(profiles["adaptive_ic"]["quarter_depth"], 12)
        self.assertEqual(profiles["adaptive_ic"]["annual_depth"], 4)
        self.assertEqual(profiles["adaptive_ic"]["call_depth"], 12)

    def test_wisburg_lens_routes_expose_sanitized_demo_coverage(self) -> None:
        external = self._request("/api/external-research?ticker=AAPL")
        self.assertEqual(external["ticker"], "AAPL")
        self.assertTrue(external["wisburg_lens"]["status"].startswith("Available"))
        self.assertTrue(external["excerpts"])
        self.assertEqual(external["wisburg_delta"]["status"], "Baseline")

        themes = self._request("/api/wisburg-themes?ticker=AAPL")
        self.assertTrue(themes["themes"])
        self.assertTrue(any("Tier 3" in caveat for caveat in themes["caveats"]))

        delta = self._request("/api/wisburg-delta?ticker=AAPL")
        self.assertEqual(delta["wisburg_delta"]["summary"], "Fixture Wisburg baseline.")
        snapshot = self._request("/api/snapshot-status?ticker=AAPL")
        self.assertEqual(snapshot["snapshot_status"]["consensus_status"], "Available")
        self.assertEqual(snapshot["snapshot_status"]["overall_status"], "Partial")

        intelligence_routes = {
            "/api/wisburg-coverage?ticker=AAPL": "coverage",
            "/api/wisburg-reports?ticker=AAPL": "reports",
            "/api/wisburg-claims?ticker=AAPL": "claims",
            "/api/wisburg-revisions?ticker=AAPL": "revisions",
            "/api/wisburg-work-orders?ticker=AAPL": "work_orders",
        }
        for route, field in intelligence_routes.items():
            response = self._request(route)
            self.assertEqual(response["ticker"], "AAPL")
            self.assertIn(field, response)

        suggestions = self._request("/api/wisburg-source-suggestions?ticker=AAPL")
        self.assertTrue(suggestions["source_suggestions"])
        self.assertEqual(suggestions["source_suggestions"][0]["source_type"], "issuer_ir_report")

    def test_llm_profile_vault_routes_are_redacted(self) -> None:
        created = self._request(
            "/api/llm-profiles",
            method="POST",
            payload={
                "display_name": "DeepSeek fixture",
                "provider_preset": "deepseek",
                "api_key": "fixture-llm-secret",
            },
        )
        profile = created["profile"]
        self.assertEqual(profile["provider_preset"], "deepseek")
        self.assertEqual(profile["base_url"], "https://api.deepseek.com")
        self.assertNotIn("fixture-llm-secret", json.dumps(created))
        listed = self._request("/api/llm-profiles")
        self.assertTrue(any(item["profile_id"] == profile["profile_id"] for item in listed["profiles"]))
        self.assertTrue(any(item["preset_id"] == "deepseek" for item in listed["presets"]))
        selected = self._request(
            "/api/llm-selection",
            method="POST",
            payload={
                "primary_profile_id": profile["profile_id"],
                "secondary_profile_id": "",
                "enable_secondary": True,
                "secondary_min_stage": "Research-Ready",
                "language_policy": "bilingual_audit",
            },
        )
        self.assertEqual(selected["selection"]["primary_profile_id"], profile["profile_id"])
        health = self._request("/api/llm-health")
        self.assertEqual(health["selection"]["primary_profile_id"], profile["profile_id"])
        removed = self._request(f"/api/llm-profiles/{profile['profile_id']}", method="DELETE")
        self.assertTrue(removed["deleted"])
        self.assertNotIn("fixture-llm-secret", json.dumps(removed))

    def test_provider_comparison_and_health_routes(self) -> None:
        providers = self._request("/api/provider-comparison?ticker=AAPL")
        self.assertEqual(providers["ticker"], "AAPL")
        self.assertEqual(providers["observations"][0]["field"], "target_mean")
        self.assertEqual(providers["comparisons"][0]["values"]["Test"], 250)
        health = self._request("/api/provider-health")
        self.assertTrue(any(item["provider"] == "Test" for item in health["health"]))

    def test_network_health_route_returns_redacted_diagnostics(self) -> None:
        report = NetworkDiagnosticReport(
            status="Available",
            network_class="general_outbound_block",
            summary="Outbound HTTPS appears blocked or refused across multiple hosts.",
            observed_at="2026-07-08T00:00:00+00:00",
            proxy_state={"env:HTTPS_PROXY": "http://[redacted]:[redacted]@127.0.0.1:7890"},
            probes=[
                NetworkProbeStatus(
                    "Alpha Vantage",
                    "www.alphavantage.co",
                    "python_https",
                    "failed",
                    "connection_refused",
                    "The target actively refused the connection.",
                    True,
                    "http://[redacted]:[redacted]@127.0.0.1:7890",
                    "2026-07-08T00:00:00+00:00",
                    "Try airport Wi-Fi login, VPN, a different network, or offline/cached mode.",
                )
            ],
            suggested_actions=["Try a VPN, mobile hotspot, or a different Wi-Fi network."],
        )

        with patch.object(server, "run_network_diagnostics", return_value=report):
            payload = self._request("/api/network-health?timeout=1&powershell=false")

        self.assertEqual(payload["network"]["network_class"], "general_outbound_block")
        self.assertEqual(payload["network"]["probes"][0]["failure_class"], "connection_refused")
        self.assertNotIn("secret", json.dumps(payload))

    def test_entity_peer_idea_audit_promotion_and_calibration_routes(self) -> None:
        entity = self._request("/api/entity?ticker=AAPL")
        self.assertEqual(entity["entity_resolution"]["ticker"], "AAPL")
        coverage = self._request("/api/financial-coverage?ticker=AAPL")
        self.assertEqual(coverage["financial_coverage"]["status"], "available")
        peers = self._request("/api/peers?ticker=AAPL")
        self.assertEqual(peers["peer_universe"]["status"], "Configured")

        idea_id = self.demo.ideas[0].idea_id
        audit = self._request(f"/api/ideas/{idea_id}/audit")
        self.assertEqual(audit["audit"]["idea_id"], idea_id)
        promoted = self._request(f"/api/ideas/{idea_id}/promote", method="POST")
        self.assertTrue(promoted["promoted"])
        audit_after = self._request(f"/api/ideas/{idea_id}/audit")
        self.assertEqual(audit_after["audit"]["versions"][0]["stage"], "High-Conviction")
        assumptions = self._request(
            f"/api/ideas/{idea_id}/assumptions",
            method="POST",
            payload={"borrow_cost_pct": 1.5},
        )
        self.assertEqual(assumptions["assumptions"]["borrow_cost_pct"], 1.5)
        outcome = self._request(
            f"/api/ideas/{idea_id}/outcome",
            method="POST",
            payload={
                "realized_return_pct": 2.5,
                "max_adverse_excursion_pct": -1.0,
                "max_favorable_excursion_pct": 3.5,
                "thesis_outcome": "confirmed",
                "closure_reason": "Fixture close.",
                "evidence_valid": "yes",
                "what_worked": "Driver evidence held up.",
                "what_failed": "Market capture remained incomplete.",
                "lessons": "Capture consensus snapshots before event review.",
                "next_process_change": "Add consensus import to the pre-IC checklist.",
            },
        )
        self.assertEqual(outcome["idea_id"], idea_id)
        self.assertEqual(outcome["outcome"]["evidence_valid"], "yes")
        self.assertEqual(outcome["audit"]["outcomes"][0]["what_failed"], "Market capture remained incomplete.")
        self.assertEqual(outcome["calibration"]["sample_size"], 1)
        calibration = self._request("/api/calibration")
        self.assertEqual(calibration["calibration"]["minimum_sample_size"], 30)
        self.assertEqual(calibration["calibration"]["sample_size"], 1)

    def test_promotion_route_rejects_candidate_with_research_ready_gaps(self) -> None:
        unsafe = demo_result("AAPL").ideas[0]
        unsafe.idea_id = "unsafe-server-promotion"
        unsafe.stage = "Candidate"
        unsafe.gate_result.eligible = True
        unsafe.gate_result.research_ready = False
        unsafe.gate_result.high_conviction = True
        unsafe.gate_result.research_ready_failed = ["Signal is not mapped to a material driver"]
        unsafe.gate_result.high_conviction_failed = []
        self.store.save_idea_versions("AAPL", "unsafe-server-run", [unsafe])

        rejected = self._request(
            f"/api/ideas/{unsafe.idea_id}/promote",
            method="POST",
            expected_status=409,
        )
        audit = self._request(f"/api/ideas/{unsafe.idea_id}/audit")

        self.assertFalse(rejected["promoted"])
        self.assertIn("Research-Ready", rejected["reason"])
        self.assertEqual(audit["audit"]["versions"][0]["stage"], "Candidate")

    def test_price_health_and_event_reaction_routes(self) -> None:
        health = self._request("/api/price-health?ticker=AAPL")
        self.assertEqual(health["statuses"][0]["status"], "available")
        reactions = self._request("/api/event-reactions?ticker=AAPL")
        self.assertEqual(reactions["event_reactions"][0]["status"], "window_pending")
        macro = self._request("/api/macro-health?ticker=AAPL")
        self.assertEqual(macro["macro"]["provider_health"][0]["provider"], "FRED/ALFRED macro")
        self.assertEqual(macro["macro"]["observations"][0]["series_id"], "DGS10")
        attribution = self._request("/api/attribution?ticker=AAPL")
        self.assertEqual(attribution["ticker"], "AAPL")
        self.assertTrue(attribution["attributions"])
        historical = self._request("/api/historical-references?ticker=AAPL")
        self.assertEqual(historical["ticker"], "AAPL")
        global_peer = self._request("/api/global-peer-coverage?ticker=AAPL")
        self.assertEqual(global_peer["ticker"], "AAPL")
        peer_metrics = self._request("/api/peer-metrics?ticker=AAPL")
        self.assertEqual(peer_metrics["ticker"], "AAPL")
        llm_manifest = self._request("/api/llm-research-manifest?ticker=AAPL")
        self.assertTrue(llm_manifest["llm_research_manifest"]["status"].startswith("Available"))
        self.assertIn("allowed_roles", llm_manifest["llm_research_manifest"])
        self.assertIn("deterministic_executor", llm_manifest["llm_research_manifest"])
        self.assertIn("historical_references", historical)

    def test_validated_claim_source_plan_and_claim_audit_routes(self) -> None:
        claims = self._request("/api/validated-claims?ticker=AAPL")
        self.assertEqual(claims["ticker"], "AAPL")
        self.assertTrue(claims["claims"])
        self.assertIn("status", claims["claims"][0])
        plan = self._request("/api/source-plan?ticker=AAPL")
        self.assertEqual(plan["ticker"], "AAPL")
        self.assertTrue(plan["source_plan"]["requests"])
        rerun = self._request(
            "/api/source-plan/run",
            method="POST",
            payload={"ticker": "AAPL", "refresh": False},
        )
        self.assertFalse(rerun["ran_research"])
        idea_id = self.demo.ideas[0].idea_id
        audit = self._request(f"/api/ideas/{idea_id}/claim-audit")
        self.assertEqual(audit["idea_id"], idea_id)
        self.assertIn("thesis_grade_status", audit)
        self.assertIn("validated_claim_ids", audit)

    def test_news_import_and_corroboration_routes_are_context_only(self) -> None:
        imported = self._request(
            "/api/news/import",
            method="POST",
            payload={
                "ticker": "AAPL",
                "provider": "Reuters",
                "headline": "Apple faces new antitrust investigation",
                "url": "https://reuters.example/aapl-antitrust",
                "excerpt": "Regulators opened an antitrust investigation into Apple services practices.",
                "full_text": "This licensed full text must not be retained.",
            },
        )

        self.assertTrue(imported["imported"])
        self.assertEqual(imported["claim"]["allowed_stage"], "Candidate")
        self.assertFalse(imported["stored_full_text"])
        self.assertNotIn("This licensed full text", json.dumps(imported))
        self.assertTrue(any(item["source_type"] == "regulator_court" for item in imported["source_needs"]))

        news = self._request("/api/news-claims?ticker=AAPL")
        self.assertTrue(news["claims"])
        self.assertEqual(news["claims"][0]["source_family"], "licensed_newswire")

        corroboration = self._request("/api/source-corroboration?ticker=AAPL")
        self.assertEqual(corroboration["corroboration"][0]["status"], "Primary corroboration missing")

        source_plan = self._request("/api/primary-source-plan?ticker=AAPL")
        self.assertTrue(any(item["source_type"] == "regulator_court" for item in source_plan["source_needs"]))

    def test_local_secret_status_test_save_and_clear_routes_are_redacted(self) -> None:
        status = self._request("/api/local-secrets/status")
        self.assertEqual(status["status"][0]["key"], "ALPHAVANTAGE_API_KEY")
        self.assertNotIn("fixture-secret", json.dumps(status))
        tested = self._request(
            "/api/local-secrets/test",
            method="POST",
            payload={"keys": {"ALPHAVANTAGE_API_KEY": "fixture-secret"}},
        )
        self.assertEqual(tested["results"][0]["status"], "valid")
        self.assertNotIn("fixture-secret", json.dumps(tested))
        saved = self._request(
            "/api/local-secrets/save",
            method="POST",
            payload={"keys": {"ALPHAVANTAGE_API_KEY": "fixture-secret", "WISBURG_API_KEY": "fixture-wisburg"}},
        )
        self.assertEqual(saved["saved"], ["ALPHAVANTAGE_API_KEY", "WISBURG_API_KEY"])
        self.assertNotIn("fixture-secret", json.dumps(saved))
        self.assertNotIn("fixture-wisburg", json.dumps(saved))
        cleared = self._request("/api/local-secrets", method="DELETE")
        self.assertIn("ALPHAVANTAGE_API_KEY", cleared["deleted"])
        self.assertIn("WISBURG_API_KEY", cleared["deleted"])

    def test_management_source_routes_and_transcript_import(self) -> None:
        sources = self._request("/api/management-sources?ticker=AAPL")
        self.assertTrue(sources["management_sources"]["claims"])
        claims = self._request("/api/management-claims?ticker=AAPL")
        self.assertEqual(claims["claims"][0]["status"], "Confirmed")
        checks = self._request("/api/cross-checks?ticker=AAPL")
        self.assertEqual(checks["cross_checks"][0]["status"], "Confirmed")
        transcripts = self._request("/api/transcripts?ticker=AAPL")
        self.assertTrue(transcripts["transcript_turns"])
        imported = self._request(
            "/api/transcripts/import",
            method="POST",
            payload={
                "ticker": "MSFT",
                "text": "We expect revenue between $10 billion and $12 billion.",
                "fiscal_period": "2026Q1",
            },
        )
        self.assertTrue(imported["imported"])

    def _request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        expected_status: int = 200,
    ) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method=method,
            headers={"Content-Type": "application/json"},
        )
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(request, timeout=5) as response:
                self.assertEqual(response.status, expected_status)
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code != expected_status:
                raise
            return json.loads(exc.read().decode("utf-8"))

class FakeSecretsManager:
    values: dict[str, str] = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)

    def redacted_status(self, system_env_keys=None):
        return [
            {
                "key": "ALPHAVANTAGE_API_KEY",
                "label": "Alpha Vantage",
                "configured": False,
                "source": "missing",
                "backend_available": True,
                "message": "",
            },
            {
                "key": "OPENAI_API_KEY",
                "label": "OpenAI API",
                "configured": True,
                "source": "os_keychain",
                "backend_available": True,
                "message": "",
            },
            {
                "key": "WISBURG_API_KEY",
                "label": "Wisburg",
                "configured": bool(self.values.get("WISBURG_API_KEY")),
                "source": "os_keychain" if self.values.get("WISBURG_API_KEY") else "missing",
                "backend_available": True,
                "message": "",
            }
        ]

    def delete_many(self):
        self.values.clear()
        return ["ALPHAVANTAGE_API_KEY", "OPENAI_API_KEY", "WISBURG_API_KEY"]


def fake_validate_provider_keys(keys):
    return [
        ValidationResult("ALPHAVANTAGE_API_KEY", "Alpha Vantage", "valid", "ok")
        for key, value in keys.items()
        if key == "ALPHAVANTAGE_API_KEY" and value
    ]


def fake_save_validated_keys(manager, keys, validation_results):
    valid_saved = [item.key for item in validation_results if item.status == "valid"]
    saved = list(valid_saved)
    if keys.get("WISBURG_API_KEY"):
        manager.set("WISBURG_API_KEY", keys["WISBURG_API_KEY"])
        saved.append("WISBURG_API_KEY")
    return {"saved": saved, "skipped": []}


if __name__ == "__main__":
    unittest.main()
