from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from equity_research.idea_engine import finalize_idea_research, generate_trade_ideas
from equity_research.management_sources import (
    IssuerIrArtifactProvider,
    _provider_status,
    build_management_source_package,
    management_events_from_package,
    transcript_document_from_payload,
)
from equity_research.models import (
    Citation,
    ChangeEvent,
    CompanyIdentity,
    FinancialMetric,
    ManagementCrossCheck,
    ManagementSourcePackage,
    ValuationBridgeStep,
    ValuationCase,
    ValuationResult,
)
from equity_research.providers import ConsensusAdapter, PriceReaction
from equity_research.research_store import ResearchStore
from equity_research.rigor import build_evidence_ledger


class _OfficialConsensus(ConsensusAdapter):
    official_for_conviction = True

    def revision_since(self, ticker, event_date):
        return 0.0


class ManagementSourceTests(unittest.TestCase):
    def test_provider_status_redacts_api_keys_from_vendor_messages(self) -> None:
        status = _provider_status(
            "Alpha Vantage transcripts",
            "Unavailable",
            "2026-07-06T00:00:00+00:00",
            "We have detected your API key as sk-secret-test and url apikey=sk-secret-test&symbol=BABA.",
            "rate_limit_or_entitlement",
            True,
        )
        self.assertNotIn("sk-secret-test", status.message)
        self.assertIn("[redacted]", status.message)

    def test_alpha_vantage_style_transcript_payload_normalizes_turns_without_raw_payload(self) -> None:
        payload = {
            "symbol": "AAPL",
            "quarter": "2026Q1",
            "transcript": [
                {
                    "speaker": "CFO",
                    "title": "Chief Financial Officer",
                    "content": "We expect revenue between $100 billion and $110 billion. Demand improved.",
                    "sentiment": "positive",
                }
            ],
        }
        document, turns = transcript_document_from_payload(
            "AAPL", payload, "Alpha Vantage transcripts", "https://example.test", "2026-06-28T00:00:00+00:00", True,
        )
        self.assertIsNotNone(document)
        self.assertEqual(document.raw_payload_policy, "normalized_excerpt_only")
        self.assertEqual(turns[0].speaker, "CFO")
        self.assertEqual(turns[0].sentiment, "positive")
        self.assertLessEqual(len(turns[0].text), 1000)

    def test_management_claims_cross_check_against_sec_facts(self) -> None:
        document, turns = transcript_document_from_payload(
            "AAPL",
            {
                "quarter": "2026Q1",
                "date": "2026-05-01",
                "transcript": [
                    {"speaker": "CFO", "content": "We expect operating margin to improve from 30 percent to 32 percent."}
                ],
            },
            "Alpha Vantage transcripts",
            "https://example.test",
            "2026-06-28T00:00:00+00:00",
            True,
        )
        metric = FinancialMetric(
            "Operating Income", 120, "USD", "2026-03-31",
            previous_value=100, yoy_change_pct=20.0,
            source_url="https://www.sec.gov/facts", source_kind="companyfacts",
        )
        package = build_management_source_package(
            "AAPL", [], {}, [document], turns, [], [], [metric], [],
        )
        self.assertTrue(package.claims)
        self.assertTrue(package.transcript_turns[0].sentiment_label)
        self.assertEqual(package.transcript_turns[0].sentiment_source, "rules_based")
        self.assertTrue(any(check.status == "Confirmed" for check in package.cross_checks))
        events = management_events_from_package(package)
        self.assertTrue(any(event.category == "guidance_shift" for event in events))

    def test_unverified_transcript_claim_cannot_be_high_conviction(self) -> None:
        event = ChangeEvent(
            "strategic_priority_change",
            "Management signal: AI priority",
            "Management said AI will be a priority.",
            3,
            "positive",
            date.today().isoformat(),
            "earnings_call_transcript",
            [Citation(
                "Transcript", "https://example.test", filed=date.today().isoformat(),
                section="prepared_remarks", snippet="AI will be a priority.",
                period_end="2026-03-31", source_tier=2,
            )],
            metrics={
                "management_claim_id": "claim-1",
                "machine_readable": True,
                "cross_checked": True,
                "cross_check_status": "Unverified",
            },
        )
        ideas = generate_trade_ideas(
            CompanyIdentity("AAPL", "1", "Apple Inc."),
            [event],
            PriceReaction("AAPL", event.event_date, 100, 101, 1, "Fixture"),
            _OfficialConsensus(),
        )
        evidence = build_evidence_ledger("AAPL", ideas, [event], [])
        gates = finalize_idea_research(ideas, _valuation(), evidence, 100)
        self.assertNotEqual(ideas[0].stage, "High-Conviction")
        self.assertIn("High-Conviction management signals require confirmed corroboration", gates[0].high_conviction_failed)

    def test_confirmed_management_cross_check_can_support_high_conviction(self) -> None:
        event = ChangeEvent(
            "guidance_shift",
            "Management signal: margin guidance",
            "Management expects operating margin to improve.",
            5,
            "positive",
            date.today().isoformat(),
            "earnings_call_transcript",
            [Citation(
                "Transcript", "https://example.test", filed=date.today().isoformat(),
                section="prepared_remarks", snippet="Operating margin to improve.",
                period_end="2026-03-31", source_tier=2,
            )],
            metrics={
                "management_claim_id": "claim-2",
                "machine_readable": True,
                "cross_checked": True,
                "cross_check_status": "Confirmed",
            },
        )
        cross = ManagementCrossCheck(
            "check-2", "claim-2", "AAPL", "Confirmed", "financial_fact",
            "SEC companyfacts confirms operating income improved.",
            "SEC companyfacts", 1, 4,
            Citation("companyfacts", "https://www.sec.gov/facts", snippet="Operating income improved.", source_tier=1),
        )
        ideas = generate_trade_ideas(
            CompanyIdentity("AAPL", "1", "Apple Inc."),
            [event],
            PriceReaction("AAPL", event.event_date, 100, 101, 1, "Fixture"),
            _OfficialConsensus(),
        )
        evidence = build_evidence_ledger("AAPL", ideas, [event], [cross])
        gates = finalize_idea_research(ideas, _valuation(), evidence, 100)
        self.assertTrue(gates[0].high_conviction)
        self.assertEqual(ideas[0].stage, "High-Conviction")

    def test_storage_persists_management_package_without_full_raw_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ResearchStore(Path(temporary) / "research.db")
            document, turns = transcript_document_from_payload(
                "AAPL",
                {"quarter": "2026Q1", "transcript": [{"speaker": "CEO", "content": "We expect demand to improve."}]},
                "CSV transcripts",
                "manual:test",
                "2026-06-28T00:00:00+00:00",
                False,
            )
            package = build_management_source_package("AAPL", [], {}, [document], turns, [], [], [], [])
            store.save_management_sources("run-1", package)
            payload = store.latest_management_sources("AAPL")
            self.assertEqual(payload["documents"][0]["raw_payload_policy"], "normalized_excerpt_only")
            self.assertFalse(any("raw_payload" in row for row in payload["documents"]))
            self.assertFalse(any("payload_json" in row for row in payload["transcript_turns"]))

    def test_issuer_ir_discovery_normalizes_transcript_and_meeting_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            seed = Path(temporary) / "issuer_ir_sources.csv"
            seed.write_text(
                "ticker,source_type,url\nTEST,quarterly_results,https://ir.example.test/results\n",
                encoding="utf-8",
            )
            fake_pdf = (
                b"%PDF-1.4\n"
                b"1 0 obj <<>> stream\n"
                b"BT (CFO: We expect operating margin to improve from 30 percent to 32 percent as cloud demand improves materially.) Tj ET\n"
                b"endstream endobj\n%%EOF"
            )

            def fetcher(url: str) -> str:
                if url.endswith("/results"):
                    return """
                    <script>
                    {"documentTitle":"March Quarter 2026 Results","urls":{
                    "transcriptUrl":"https://ir.example.test/q1-transcript.pdf",
                    "presentationUrl":"https://ir.example.test/q1-presentation.pdf"}}
                    </script>
                    <a href="/q1-transcript.html">March Quarter 2026 Results Transcript</a>
                    <a href="/agm.html">Annual meeting vote results approved</a>
                    """
                if url.endswith("/q1-transcript.pdf"):
                    return fake_pdf
                if url.endswith("/q1-transcript.html"):
                    return """
                    <p>CFO: We expect operating margin to improve from 30 percent to 32 percent as cloud demand improves materially.</p>
                    <p>CEO: AI and cloud remain strategic priorities for the next fiscal year.</p>
                    """
                return "<p>Annual meeting vote results approved by shareholders.</p>"

            provider = IssuerIrArtifactProvider(sources_csv=seed, fetcher=fetcher)
            documents, turns, statuses = provider.fetch_documents("TEST")
            self.assertTrue(any(doc.source_type == "earnings_call_transcript" for doc in documents))
            self.assertTrue(any(doc.source_type == "agm_egm_material" for doc in documents))
            self.assertTrue(turns)
            self.assertTrue(any(turn.speaker == "CFO" for turn in turns))
            self.assertTrue(any(status.status == "Available" for status in statuses))

            package = build_management_source_package("TEST", [], {}, documents, turns, statuses, [], [], [])
            self.assertNotEqual(package.transcript_turns[0].sentiment_label, None)
            self.assertTrue(package.claims)
            self.assertTrue(package.meeting_events)

    def test_issuer_ir_triage_retains_metadata_for_unparsed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            seed = Path(temporary) / "issuer_ir_sources.csv"
            seed.write_text(
                "ticker,source_type,url\nTEST,quarterly_results,https://ir.example.test/results\n",
                encoding="utf-8",
            )

            def fetcher(url: str) -> str:
                if url.endswith("/results"):
                    return """
                    <a href="/q1-transcript.html">March Quarter 2026 Results Transcript</a>
                    <a href="/q1-presentation.html">March Quarter 2026 Results Presentation</a>
                    <a href="/agm.html">Annual meeting vote results approved</a>
                    """
                return (
                    "<p>CFO: We expect operating margin to improve from 30 percent to 32 percent "
                    "as cloud demand improves and cost discipline continues next quarter.</p>"
                )

            provider = IssuerIrArtifactProvider(
                sources_csv=seed,
                fetcher=fetcher,
                max_documents_per_seed=1,
                metadata_limit_per_seed=4,
            )
            documents, turns, statuses = provider.fetch_documents("TEST")
            parsed = [doc for doc in documents if doc.raw_payload_policy != "metadata_only_latency_triaged"]
            metadata_only = [doc for doc in documents if doc.raw_payload_policy == "metadata_only_latency_triaged"]
            self.assertEqual(len(parsed), 1)
            self.assertGreaterEqual(len(metadata_only), 1)
            self.assertTrue(any("metadata-only" in status.message for status in statuses))
            self.assertTrue(turns)


def _valuation() -> ValuationResult:
    cases = [
        ValuationCase("Bear", 0.25, 80.0, "DCF", ["Bear assumptions"]),
        ValuationCase("Base", 0.50, 100.0, "DCF", ["Base assumptions"]),
        ValuationCase("Bull", 0.25, 130.0, "DCF", ["Bull assumptions"]),
    ]
    return ValuationResult(
        template="Non-financial",
        status="Available",
        cases=cases,
        currency="USD",
        bridge=[
            ValuationBridgeStep(case.name, "Fair value", case.fair_value, "USD", "DCF", "Fixture")
            for case in cases
        ],
    )


if __name__ == "__main__":
    unittest.main()
