from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

from equity_research.external_evidence import ExternalEvidenceStack
from equity_research.management_sources import ManagementSourceStack
from equity_research.models import (
    CompanyIdentity,
    ExternalEvidence,
    ExternalEvidenceBundle,
    FilingRecord,
    ManagementDocument,
    ManagementSourcePackage,
    ProviderStatus,
    TranscriptTurn,
)
from equity_research.pipeline import _profile_filing_text
from equity_research.providers import StooqPriceClient, _DailyRowsResult
from equity_research.research_store import ResearchStore
from equity_research.sec_client import SecClient


class _ConcurrencyProbe:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def enter(self) -> None:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def exit(self) -> None:
        with self.lock:
            self.active -= 1


class _SlowExternalProvider:
    def __init__(self, name: str, probe: _ConcurrencyProbe) -> None:
        self.provider_name = name
        self.probe = probe

    def fetch(self, identity, events):
        self.probe.enter()
        try:
            time.sleep(0.04)
        finally:
            self.probe.exit()
        return ExternalEvidenceBundle(
            identity.ticker,
            "Available",
            [ExternalEvidence(
                provider=self.provider_name,
                source_type="fixture",
                title=self.provider_name,
                summary="Fixture evidence",
                observed_at="2026-07-14T00:00:00+00:00",
                source_as_of="2026-07-14",
                source_tier=2,
                official=True,
                confidence="High",
            )],
            [ProviderStatus(
                provider=self.provider_name,
                status="Available",
                official=True,
                entitlement_status="available",
                observed_at="2026-07-14T00:00:00+00:00",
            )],
            [],
        )


class _SlowManagementProvider:
    def __init__(self, name: str, probe: _ConcurrencyProbe) -> None:
        self.provider_name = name
        self.probe = probe

    def fetch_documents(self, ticker: str, history_limit: int | None = None):
        self.probe.enter()
        try:
            time.sleep(0.04)
        finally:
            self.probe.exit()
        return [], [], [ProviderStatus(
            provider=self.provider_name,
            status="Available",
            official=True,
            entitlement_status="available",
            observed_at="2026-07-14T00:00:00+00:00",
        )]


def test_external_evidence_runs_independent_providers_concurrently_and_preserves_order() -> None:
    probe = _ConcurrencyProbe()
    providers = [_SlowExternalProvider(f"provider-{index}", probe) for index in range(4)]

    result = ExternalEvidenceStack(providers, use_cache=False).fetch(
        CompanyIdentity("AAPL", "0000320193", "Apple Inc."),
        [],
    )

    assert probe.max_active >= 2
    assert [item.provider for item in result.evidence] == [provider.provider_name for provider in providers]


def test_management_sources_run_concurrently_and_preserve_provider_order() -> None:
    probe = _ConcurrencyProbe()
    providers = [_SlowManagementProvider(f"management-{index}", probe) for index in range(4)]

    _, _, statuses = ManagementSourceStack(providers).fetch_documents("AAPL", history_limit=12)

    assert probe.max_active >= 2
    assert [item.provider for item in statuses] == [provider.provider_name for provider in providers]


def test_sec_json_is_parsed_once_per_client() -> None:
    class CountingSecClient(SecClient):
        def __init__(self, cache_dir: Path) -> None:
            super().__init__(cache_dir=cache_dir)
            self.text_calls = 0

        def get_text(self, url: str, ttl_seconds: int) -> str:
            self.text_calls += 1
            return json.dumps({"value": 7})

    with tempfile.TemporaryDirectory() as tmp:
        client = CountingSecClient(Path(tmp))
        assert client.get_json("https://example.test/data", 60)["value"] == 7
        assert client.get_json("https://example.test/data", 60)["value"] == 7
        assert client.text_calls == 1


def test_parsed_filing_text_cache_survives_new_in_memory_cache() -> None:
    class FilingClient:
        def __init__(self, cache_dir: Path) -> None:
            self.cache_dir = cache_dir
            self.calls = 0

        def get_filing_text(self, filing) -> str:
            self.calls += 1
            return "<html><body>Management discussion and analysis revenue increased.</body></html>"

    filing = FilingRecord(
        form="10-Q",
        accession="0000320193-26-000001",
        filing_date="2026-05-01",
        report_date="2026-03-28",
        primary_doc="aapl.htm",
        description="Quarterly report",
        url="https://www.sec.gov/aapl.htm",
    )
    with tempfile.TemporaryDirectory() as tmp:
        client = FilingClient(Path(tmp))
        first = _profile_filing_text(client, filing, {})
        second = _profile_filing_text(client, filing, {})
        assert first == second
        assert client.calls == 1


def test_price_rows_are_fetched_once_when_same_ticker_is_requested_concurrently() -> None:
    class CountingPriceClient(StooqPriceClient):
        def __init__(self) -> None:
            super().__init__(enable_yahoo_price=False)
            self.calls = 0

        def _fetch_daily_rows_result(self, ticker: str, bypass_cache: bool = False):
            self.calls += 1
            time.sleep(0.04)
            return _DailyRowsResult("fixture", "available", "fixture", [])

    client = CountingPriceClient()
    threads = [threading.Thread(target=client._cached_daily_rows, args=("SPY",)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert client.calls == 1


def test_normalized_management_inputs_can_be_reused_when_live_provider_is_partial() -> None:
    document = ManagementDocument(
        document_id="aapl-call-2026q2",
        ticker="AAPL",
        source_type="earnings_call_transcript",
        provider="Licensed transcript fixture",
        title="AAPL Q2 call",
        url="https://example.test/aapl-call",
        event_date="2026-05-01",
        fiscal_period="2026-Q2",
        source_tier=2,
        observed_at="2026-05-01T22:00:00+00:00",
        excerpt="Demand remained resilient.",
    )
    turn = TranscriptTurn(
        turn_id="turn-1",
        document_id=document.document_id,
        speaker="CFO",
        role="management",
        section="Q&A",
        text="Demand remained resilient.",
        turn_index=0,
        positive_terms=["resilient"],
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = ResearchStore(Path(tmp) / "research.db")
        store.save_management_sources(
            "run-1",
            ManagementSourcePackage("AAPL", "Available", [document], [turn]),
        )
        documents, turns = store.cached_management_inputs("AAPL")
    assert documents == [document]
    assert turns == [turn]


def test_unchanged_management_evidence_ignores_retrieval_time_for_persistence_cache() -> None:
    first_document = ManagementDocument(
        document_id="aapl-call-stable",
        ticker="AAPL",
        source_type="earnings_call_transcript",
        provider="Licensed transcript fixture",
        title="AAPL call",
        url="https://example.test/aapl-call-stable",
        event_date="2026-05-01",
        fiscal_period="2026-Q2",
        source_tier=2,
        observed_at="2026-05-01T22:00:00+00:00",
        excerpt="Demand remained resilient.",
    )
    later_document = ManagementDocument(
        **(
            {
                key: value
                for key, value in first_document.__dict__.items()
                if key != "observed_at"
            }
            | {"observed_at": "2026-05-02T22:00:00+00:00"}
        )
    )
    with tempfile.TemporaryDirectory() as tmp:
        store = ResearchStore(Path(tmp) / "research.db")
        store.save_management_sources(
            "run-1", ManagementSourcePackage("AAPL", "Available", [first_document]),
        )
        store.save_management_sources(
            "run-2", ManagementSourcePackage("AAPL", "Available", [later_document]),
        )
        payload = store.latest_management_sources("AAPL")

    assert payload["documents"][0]["observed_at"] == first_document.observed_at
