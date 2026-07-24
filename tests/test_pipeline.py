from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from equity_research.pipeline import _compare_latest_pairs, run_us_equity_research
from equity_research.performance import ResearchProfiler
from equity_research.peers import peer_universe_for
from equity_research.research_store import ResearchStore
from equity_research.sec_client import SecClientError
from equity_research.global_peers import GlobalPeerFinancialProvider


PEER_FACTS = {
    "BABA": {"Revenue": (125, 100)},
    "JD": {"Revenue": (130, 100)},
    "PDD": {"Revenue": (85, 100)},
    "BIDU": {"Revenue": (104, 100)},
    "NTES": {},
    "TCEHY": {"Revenue": (110, 100)},
}


class FakeSecClient:
    def map_ticker(self, ticker):
        from equity_research.models import CompanyIdentity

        return CompanyIdentity(ticker=ticker, cik=ticker, name=f"{ticker} Inc.")

    def get_recent_filings(self, cik, forms=None, limit=30):
        from equity_research.models import FilingRecord

        return [
            FilingRecord(
                form="20-F",
                accession="0001577552-26-000001",
                filing_date="2026-07-01",
                report_date="2026-03-31",
                primary_doc="baba-20260331.htm",
                description="Annual report",
                url="https://example.com/current-20f.htm",
            ),
            FilingRecord(
                form="20-F",
                accession="0001577552-25-000001",
                filing_date="2025-07-01",
                report_date="2025-03-31",
                primary_doc="baba-20250331.htm",
                description="Annual report",
                url="https://example.com/previous-20f.htm",
            ),
            FilingRecord(
                form="6-K",
                accession="0001577552-26-000002",
                filing_date="2026-08-15",
                report_date="2026-08-15",
                primary_doc="baba-6k.htm",
                description="Interim results",
                url="https://example.com/6k.htm",
            ),
        ]

    def get_filing_text(self, filing):
        if filing.form == "6-K":
            return "Interim results outlook guidance tailwind margin expansion " * 20
        if "current" in filing.url:
            return (
                "Item 3.D. Risk Factors "
                + "regulatory risk litigation customer concentration " * 70
                + " Item 4. Information on the Company "
                + " Item 5. Operating and Financial Review and Prospects "
                + "revenue growth gross margin margin expansion outlook " * 70
                + " Item 6. Directors"
            )
        return (
            "Item 3.D. Risk Factors "
            + "regulatory risk " * 20
            + " Item 4. Information on the Company "
            + " Item 5. Operating and Financial Review and Prospects "
            + "revenue growth " * 20
            + " Item 6. Directors"
        )

    def get_company_facts(self, cik):
        if cik == "TCOM":
            raise SecClientError("No companyfacts for test peer.")
        facts = PEER_FACTS.get(cik, {})
        revenue_values = facts.get("Revenue")
        if not revenue_values:
            return {"facts": {"ifrs-full": {}}}
        current, previous = revenue_values
        return {
            "facts": {
                "ifrs-full": {
                    "Revenue": {
                        "units": {
                            "CNY": [
                                {
                                    "val": previous,
                                    "end": "2025-03-31",
                                    "filed": "2025-07-01",
                                    "form": "20-F",
                                    "fp": "FY",
                                    "fy": 2025,
                                },
                                {
                                    "val": current,
                                    "end": "2026-03-31",
                                    "filed": "2026-07-01",
                                    "form": "20-F",
                                    "fp": "FY",
                                    "fy": 2026,
                                },
                            ]
                        }
                    }
                }
            }
        }


class NoopPriceClient:
    def price_reaction_since(self, ticker, event_date):
        from equity_research.providers import PriceReaction

        return PriceReaction(ticker, event_date, 100, 101, 1.0, "Test")


class NoopManagementSources:
    def fetch_documents(self, ticker):
        return [], [], []


class NoopExternalEvidence:
    provider_name = "Noop external evidence"

    def fetch(self, identity, events):
        from equity_research.models import ExternalEvidenceBundle

        return ExternalEvidenceBundle(identity.ticker, "Unavailable", [], [], [])


class CrowdedAnnualSecClient:
    def get_recent_filings(self, cik, forms=None, limit=30):
        from equity_research.models import FilingRecord

        if forms == {"20-F"}:
            return [
                FilingRecord("20-F", "current-20f", "2026-05-20", "2026-03-31", "current.htm", "Annual report", "https://example.com/current.htm"),
                FilingRecord("20-F", "prior-20f", "2025-06-26", "2025-03-31", "prior.htm", "Annual report", "https://example.com/prior.htm"),
            ]
        return [
            FilingRecord("6-K", f"recent-{idx}", "2026-07-01", "2026-07-01", "6k.htm", "Current report", f"https://example.com/{idx}.htm")
            for idx in range(12)
        ] + [
            FilingRecord("20-F", "current-20f", "2026-05-20", "2026-03-31", "current.htm", "Annual report", "https://example.com/current.htm")
        ]

    def get_filing_text(self, filing):
        if filing.accession == "current-20f":
            return "Debt liquidity covenant refinancing maturity risk. " * 5
        return "Debt liquidity covenant refinancing maturity risk. " * 2


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = ResearchStore(Path(self.temporary.name) / "research.db")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_foreign_private_issuer_forms_generate_results(self) -> None:
        result = run_us_equity_research(
            "BABA",
            sec_client=FakeSecClient(),
            price_client=NoopPriceClient(),
            management_sources=NoopManagementSources(),
            external_evidence_provider=NoopExternalEvidence(),
            global_peer_provider=GlobalPeerFinancialProvider(enable_live=False),
            store=self.store,
        )
        self.assertTrue(result.coverage_notes)
        self.assertTrue(any(filing.form == "20-F" for filing in result.filings))
        self.assertTrue(any(filing.form == "6-K" for filing in result.filings))
        self.assertTrue(result.metrics)
        self.assertTrue(result.events)
        self.assertTrue(result.ideas)
        self.assertIn("20-F/40-F", result.coverage_notes[0])

    def test_baba_direct_peer_checks_cover_full_basket(self) -> None:
        result = run_us_equity_research(
            "BABA",
            sec_client=FakeSecClient(),
            price_client=NoopPriceClient(),
            management_sources=NoopManagementSources(),
            external_evidence_provider=NoopExternalEvidence(),
            global_peer_provider=GlobalPeerFinancialProvider(enable_live=False),
            store=self.store,
        )
        revenue_idea = next(idea for idea in result.ideas if "Revenue changed" in idea.title)
        peers = {peer.peer_ticker: peer for peer in revenue_idea.peer_readthrough}
        configured_peers = {peer.ticker for peer in peer_universe_for("BABA").peers}
        self.assertEqual(set(peers), configured_peers)
        self.assertIn("TCEHY", peers)
        self.assertEqual(peers["JD"].relation, "Confirming read-through")
        self.assertEqual(peers["PDD"].relation, "Contradicting read-through")
        self.assertEqual(peers["NTES"].evidence_status, "No direct evidence found")
        self.assertEqual(peers["TCOM"].evidence_status, "Global peer coverage unavailable")
        self.assertEqual(peers["TCOM"].failure_status, "official_document_not_found")

    def test_pipeline_profiling_reports_stage_bottlenecks_without_skipping_workflow(self) -> None:
        result = run_us_equity_research(
            "BABA",
            sec_client=FakeSecClient(),
            price_client=NoopPriceClient(),
            management_sources=NoopManagementSources(),
            external_evidence_provider=NoopExternalEvidence(),
            global_peer_provider=GlobalPeerFinancialProvider(enable_live=False),
            store=self.store,
            profiler=ResearchProfiler(enabled=True),
        )

        self.assertEqual(result.profiling.status, "Available")
        self.assertGreater(result.profiling.total_ms, 0)
        self.assertTrue(result.profiling.steps)
        self.assertTrue(result.profiling.bottlenecks)
        self.assertTrue(result.ideas)
        self.assertTrue(result.thesis_validation.checks)

    def test_compare_latest_pairs_fetches_form_specific_prior_when_window_is_crowded(self) -> None:
        sec = CrowdedAnnualSecClient()
        crowded = sec.get_recent_filings("BABA", forms={"20-F", "6-K"}, limit=30)

        events = _compare_latest_pairs(sec, "BABA", crowded, "20-F")
        debt = next(event for event in events if event.category == "debt_liquidity")

        self.assertEqual(debt.metrics["previous_accession"], "prior-20f")
        self.assertIn(debt.metrics["comparison_status"], {"period_aligned", "comparable_imperfect"})

    def test_blocked_filing_html_does_not_abort_structured_sec_research(self) -> None:
        class BlockedFilingClient(CrowdedAnnualSecClient):
            def get_filing_text(self, filing):
                raise SecClientError("HTTP Error 403: Forbidden")

        filings = BlockedFilingClient().get_recent_filings("BABA")

        events = _compare_latest_pairs(
            BlockedFilingClient(), "BABA", filings, "20-F",
        )

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
