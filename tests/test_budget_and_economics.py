from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

import equity_research.company_economics as company_economics
from equity_research.budget import (
    build_budget_policy,
    budget_allows_paid_data,
    load_budget_mode_definitions,
    normalize_budget_mode,
)
from equity_research.company_economics import attach_economic_context, build_company_economics
from equity_research.manual_data import load_china_macro_evidence, scan_manual_data_sources
from equity_research.models import ChangeEvent, CompanyIdentity, ConsensusPackage, FinancialMetric
from equity_research.peers import peer_universe_for
from equity_research.sample_data import demo_result


class BudgetAndEconomicsTests(unittest.TestCase):
    def test_budget_modes_keep_paid_data_optional(self) -> None:
        self.assertEqual(normalize_budget_mode("paid"), "Stable")
        self.assertFalse(budget_allows_paid_data("Lean"))
        self.assertTrue(budget_allows_paid_data("Premium"))
        policy = build_budget_policy("Free", ConsensusPackage("AAPL", "Test", "Unavailable"), None)
        self.assertEqual(policy.mode, "Free")
        self.assertFalse(policy.allow_paid_data)
        self.assertEqual(policy.max_monthly_budget_usd, 0)
        self.assertIn("prices", policy.provider_policy)
        self.assertTrue(any("Paid" in item for item in policy.disabled_sources))
        self.assertTrue(policy.optional_upgrade_slots)

    def test_local_budget_mode_override_is_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "budget_modes.json"
            path.write_text(
                json.dumps({
                    "modes": {
                        "Lean": {
                            "cost_target": "$12/month custom cap",
                            "max_monthly_budget_usd": 12,
                            "allow_paid_data": False,
                            "provider_policy": {
                                "llm": ["custom cheap primary"],
                                "prices": ["CSV/manual"],
                            },
                        },
                        "ResearchLab": {
                            "cost_target": "$40/month",
                            "description": "Custom user research mode.",
                            "max_monthly_budget_usd": 40,
                            "allow_llm": True,
                            "allow_paid_data": True,
                            "provider_policy": {"prices": ["Tiingo"]},
                        },
                    }
                }),
                encoding="utf-8",
            )
            definitions = load_budget_mode_definitions(path)
        self.assertEqual(definitions["Lean"]["cost_target"], "$12/month custom cap")
        self.assertEqual(definitions["ResearchLab"]["provider_policy"]["prices"], ["Tiingo"])

    def test_manual_data_scan_counts_ticker_rows_without_requiring_paid_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "segment_kpis.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["ticker", "segment", "metric", "value"])
                writer.writeheader()
                writer.writerow({"ticker": "AAPL", "segment": "Services", "metric": "Revenue", "value": "100"})
                writer.writerow({"ticker": "MSFT", "segment": "Cloud", "metric": "Revenue", "value": "200"})
            status = scan_manual_data_sources("AAPL", base)
        segment = next(item for item in status.sources if item.source_type == "segment_kpis")
        self.assertEqual(segment.status, "Available")
        self.assertEqual(segment.rows_loaded, 1)

    def test_manual_china_macro_rows_are_loaded_without_lookahead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            path = base / "china_macro.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["ticker", "as_of", "metric", "value", "unit", "source_url", "summary"],
                )
                writer.writeheader()
                writer.writerow({
                    "ticker": "BABA",
                    "as_of": "2026-05-30",
                    "metric": "China retail sales",
                    "value": "4.5",
                    "unit": "% y/y",
                    "source_url": "https://example.test/nbs",
                    "summary": "Retail sales grew 4.5% y/y.",
                })
                writer.writerow({
                    "ticker": "BABA",
                    "as_of": "2026-07-30",
                    "metric": "Future retail sales",
                    "value": "8.0",
                    "unit": "% y/y",
                    "source_url": "https://example.test/nbs",
                    "summary": "Future row.",
                })
            evidence = load_china_macro_evidence("BABA", "2026-06-01", base)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].source_type, "china_macro")
        self.assertEqual(evidence[0].metric_name, "China retail sales")

    def test_company_economics_maps_events_to_material_drivers(self) -> None:
        identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.")
        metrics = [
            FinancialMetric(
                "Revenue", 100, "USD", "2026-03-31",
                previous_value=90, yoy_change_pct=11.1,
            )
        ]
        events = [
            ChangeEvent(
                "financial_kpi", "Revenue changed", "Revenue rose sharply.", 4,
                "positive", "2026-05-01", "SEC",
                metrics={"metric_name": "Revenue"},
            )
        ]
        economics = build_company_economics(identity, metrics, events, peer_universe_for("AAPL"))
        attach_economic_context(events, economics)
        self.assertEqual(economics.status, "Available")
        self.assertEqual(events[0].metrics["economic_driver"], "Revenue growth / demand")
        self.assertEqual(events[0].metrics["driver_materiality"], "High")
        coverage = {item.driver_name: item for item in economics.driver_coverage}
        self.assertIn("Revenue growth / demand", coverage)
        self.assertEqual(coverage["Revenue growth / demand"].status, "Mapped / needs corroboration")
        self.assertTrue(any("Segment revenue" in item for item in coverage["Revenue growth / demand"].missing_evidence))
        quality = {item.area: item for item in economics.playbook_quality}
        self.assertGreaterEqual(economics.playbook_quality_score, 70)
        self.assertEqual(quality["Business model specificity"].status, "Specific")
        self.assertEqual(quality["KPI and leading-indicator coverage"].status, "Covered")

    def test_adr_profile_adds_segment_drivers_to_company_economics(self) -> None:
        identity = CompanyIdentity("BABA", "0001577552", "Alibaba Group Holding Ltd")
        economics = build_company_economics(identity, [], [], peer_universe_for("BABA"))
        driver_names = {driver.name for driver in economics.drivers}
        self.assertIn("China commerce", driver_names)
        self.assertIn("Cloud", driver_names)
        self.assertIn("Buybacks", driver_names)
        self.assertIn("KWEB", economics.industry_playbook.leading_indicators)
        coverage = {item.driver_name: item for item in economics.driver_coverage}
        self.assertEqual(coverage["China commerce"].status, "Playbook-only / needs source validation")
        self.assertIn("Blocks Research-Ready", coverage["China commerce"].stage_impact)
        quality = {item.area: item for item in economics.playbook_quality}
        self.assertIn("+adr_profile", economics.industry_playbook.playbook_source)
        self.assertEqual(quality["Business model specificity"].status, "Specific")
        self.assertEqual(quality["Driver source validation"].status, "Playbook-only")
        self.assertIn("playbook-only", quality["Driver source validation"].gaps[0])
        self.assertIn("issuer", quality["Driver source validation"].gaps[0])
        self.assertIn("Playbook-only drivers block Research-Ready", quality["Driver source validation"].stage_impact)

    def test_sector_playbooks_cover_non_adr_us_equities(self) -> None:
        software = build_company_economics(
            CompanyIdentity("MSFT", "0000789019", "Microsoft Corp", sic="7372", sic_description="Services-prepackaged software"),
            [],
            [],
            peer_universe_for("MSFT"),
        )
        energy = build_company_economics(
            CompanyIdentity("XOM", "0000034088", "Exxon Mobil Corp", sic="1311", sic_description="Crude petroleum and natural gas"),
            [],
            [],
            peer_universe_for("XOM"),
        )

        self.assertEqual(software.industry_playbook.industry_label, "Software / cloud platform")
        self.assertIn("ARR/RPO", software.industry_playbook.key_kpis)
        self.assertEqual(energy.industry_playbook.industry_label, "Energy / upstream and refining")
        self.assertIn("Brent/WTI", energy.industry_playbook.leading_indicators)

    def test_lean_cross_sector_representatives_get_appropriate_playbooks(self) -> None:
        cases = [
            (CompanyIdentity("AAPL", "0000320193", "Apple Inc", sic="3571", sic_description="Electronic computers"), "Semiconductors"),
            (CompanyIdentity("NVDA", "0001045810", "NVIDIA Corp", sic="3674", sic_description="Semiconductors"), "Semiconductors"),
            (CompanyIdentity("JPM", "0000019617", "JPMorgan Chase & Co"), "Bank"),
            (CompanyIdentity("GS", "0000886982", "Goldman Sachs Group Inc"), "Investment bank"),
            (CompanyIdentity("PLD", "0001045609", "Prologis Inc"), "REIT"),
            (CompanyIdentity("XOM", "0000034088", "Exxon Mobil Corp", sic="1311", sic_description="Crude petroleum and natural gas"), "Energy"),
            (CompanyIdentity("LLY", "0000059478", "Eli Lilly and Co", sic="2834", sic_description="Pharmaceutical preparations"), "Healthcare"),
            (CompanyIdentity("TSLA", "0001318605", "Tesla Inc", sic="3711", sic_description="Motor vehicles"), "Autos"),
        ]

        for identity, expected_label in cases:
            economics = build_company_economics(identity, [], [], peer_universe_for(identity.ticker))
            self.assertIn(expected_label, economics.industry_playbook.industry_label, identity.ticker)
            self.assertTrue(economics.industry_playbook.key_kpis, identity.ticker)
            self.assertTrue(economics.industry_playbook.leading_indicators, identity.ticker)

        for ticker in ["BABA", "NIO", "TSM", "INFY", "NVO", "VALE"]:
            economics = build_company_economics(
                CompanyIdentity(ticker, "0000000000", f"{ticker} ADR"),
                [],
                [],
                peer_universe_for(ticker),
            )
            self.assertIn("+adr_profile", economics.industry_playbook.playbook_source, ticker)
            self.assertTrue(economics.industry_playbook.leading_indicators, ticker)

    def test_csv_playbook_override_by_ticker_is_user_extensible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "industry_playbooks.csv"
            path.write_text(
                "\n".join([
                    "ticker,industry_label,business_model,key_kpis,leading_indicators,valuation_methods,macro_sensitivities,normal_catalysts,peer_tickers,source",
                    "DEMO,Vertical software testbed,Recurring software and services model.,ARR|RPO|FCF conversion,Seat expansion|renewals,EV/revenue|FCF yield,IT budgets|rates,Earnings|RPO update,CRM|NOW,unit-test",
                ]),
                encoding="utf-8",
            )
            previous = company_economics.INDUSTRY_PLAYBOOK_CSV
            company_economics.INDUSTRY_PLAYBOOK_CSV = path
            try:
                economics = build_company_economics(
                    CompanyIdentity("DEMO", "0000000000", "Demo Software Inc", sic="7372"),
                    [],
                    [],
                    None,
                )
            finally:
                company_economics.INDUSTRY_PLAYBOOK_CSV = previous

        self.assertEqual(economics.industry_playbook.industry_label, "Vertical software testbed")
        self.assertEqual(economics.business_model, "Recurring software and services model.")
        self.assertEqual(economics.industry_playbook.key_kpis, ["ARR", "RPO", "FCF conversion"])
        self.assertEqual(economics.industry_playbook.peer_tickers, ["CRM", "NOW"])
        self.assertTrue(economics.industry_playbook.playbook_source.startswith("csv:unit-test"))
        self.assertNotIn("Curated peer universe is not configured.", economics.industry_playbook.data_gaps)

    def test_csv_playbook_override_by_sic_range_preserves_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "industry_playbooks.csv"
            path.write_text(
                "\n".join([
                    "sic_min,sic_max,industry_label,key_kpis,source",
                    "7300,7399,Custom software services,Bookings|Backlog,sector-test",
                ]),
                encoding="utf-8",
            )
            previous = company_economics.INDUSTRY_PLAYBOOK_CSV
            company_economics.INDUSTRY_PLAYBOOK_CSV = path
            try:
                economics = build_company_economics(
                    CompanyIdentity("SOFT", "0000000001", "Soft Services Corp", sic="7372", sic_description="software services"),
                    [],
                    [],
                    peer_universe_for("MSFT"),
                )
            finally:
                company_economics.INDUSTRY_PLAYBOOK_CSV = previous

        self.assertEqual(economics.industry_playbook.industry_label, "Custom software services")
        self.assertEqual(economics.industry_playbook.key_kpis, ["Bookings", "Backlog"])
        self.assertIn("Forward P/E", economics.industry_playbook.valuation_methods)
        self.assertTrue(economics.industry_playbook.playbook_source.startswith("csv:sector-test"))

    def test_demo_result_exposes_clusters_and_economics(self) -> None:
        result = demo_result("AAPL")
        self.assertEqual(result.budget_policy.mode, "Lean")
        self.assertTrue(result.company_economics.drivers)
        self.assertTrue(result.company_economics.playbook_quality)
        self.assertIn("Playbook quality checklist", result.memo_markdown)
        self.assertTrue(result.thesis_clusters)
        self.assertIsNotNone(result.ideas[0].conviction_chain)
        self.assertIn("Conviction Chain", result.memo_markdown)
        self.assertIn(result.thesis_clusters[0].conviction_chain_status, {"Convincing", "Promising but incomplete", "Early research", "Weak"})
        self.assertIn("Company Economics", result.memo_markdown)
        self.assertIn("Driver coverage checklist", result.memo_markdown)

    def test_watch_clusters_are_not_presented_as_investable_theses(self) -> None:
        result = demo_result("BABA")
        watch_clusters = [cluster for cluster in result.thesis_clusters if cluster.direction == "Watch"]

        self.assertTrue(watch_clusters)
        self.assertTrue(all(cluster.label.startswith("Watch item:") for cluster in watch_clusters))
        self.assertTrue(all(cluster.score is not None and cluster.score <= 40 for cluster in watch_clusters))
        self.assertTrue(all(cluster.priced_in.startswith("Not assessed.") for cluster in watch_clusters))
        self.assertTrue(all(cluster.status == "Watch item / source validation" for cluster in watch_clusters))


if __name__ == "__main__":
    unittest.main()
