from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from equity_research.models import FilingRecord
from equity_research.sec_client import SecClient, SecClientError


class ArchiveFixtureClient(SecClient):
    def __init__(self) -> None:
        pass

    def get_recent_filings(self, cik: str, forms=None, limit: int = 12):
        return [FilingRecord(
            form="20-F",
            accession="0000000000-26-000001",
            filing_date="2026-05-20",
            report_date="2026-03-31",
            primary_doc="current.htm",
            description="Annual report",
            url="https://www.sec.gov/current.htm",
        )]

    def get_submissions(self, cik: str):
        return {"filings": {"files": [{"name": "CIK0000000000-submissions-001.json"}]}}

    def get_json(self, url: str, ttl_seconds: int):
        return {
            "accessionNumber": ["0000000000-25-000001", "0000000000-24-000001"],
            "filingDate": ["2025-05-20", "2024-05-20"],
            "reportDate": ["2025-03-31", "2024-03-31"],
            "form": ["20-F", "20-F"],
            "primaryDocument": ["prior.htm", "older.htm"],
            "primaryDocDescription": ["Annual report", "Annual report"],
            "acceptanceDateTime": ["20250520120000", "20240520120000"],
        }


def test_comparable_filings_expand_into_sec_submission_archives() -> None:
    filings = ArchiveFixtureClient().get_comparable_filings("0000000000", "20-F", limit=3)

    assert [item.report_date for item in filings] == ["2026-03-31", "2025-03-31", "2024-03-31"]
    assert filings[1].accession == "0000000000-25-000001"
    assert "/000000000025000001/prior.htm" in filings[1].url


def test_map_ticker_uses_bundled_snapshot_when_live_index_is_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "tickers.json"
        snapshot.write_text(json.dumps({
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }), encoding="utf-8")
        client = SecClient(
            cache_dir=Path(tmp) / "cache",
            ticker_snapshot_path=snapshot,
        )

        def blocked_live_index(url: str, ttl_seconds: int) -> dict:
            raise SecClientError("HTTP Error 403: Forbidden")

        client.get_json = blocked_live_index  # type: ignore[method-assign]
        identity = client.map_ticker("aapl")

        assert identity.ticker == "AAPL"
        assert identity.cik == "0000320193"
        assert identity.name == "Apple Inc."
        assert client.ticker_map_source == "bundled_sec_snapshot"


def test_map_ticker_prefers_live_sec_index() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "tickers.json"
        snapshot.write_text(json.dumps({
            "0": {"cik_str": 1, "ticker": "AAPL", "title": "Stale name"},
        }), encoding="utf-8")
        client = SecClient(
            cache_dir=Path(tmp) / "cache",
            ticker_snapshot_path=snapshot,
        )
        client.get_json = lambda url, ttl_seconds: {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        }  # type: ignore[method-assign]

        identity = client.map_ticker("AAPL")

        assert identity.cik == "0000320193"
        assert identity.name == "Apple Inc."
        assert client.ticker_map_source == "live_sec_or_cache"


def test_map_ticker_reports_snapshot_boundary_for_unknown_ticker() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "tickers.json"
        snapshot.write_text("{}", encoding="utf-8")
        client = SecClient(
            cache_dir=Path(tmp) / "cache",
            ticker_snapshot_path=snapshot,
        )
        client.get_json = lambda url, ttl_seconds: (_ for _ in ()).throw(
            SecClientError("HTTP Error 403: Forbidden")
        )  # type: ignore[method-assign]

        with pytest.raises(SecClientError, match="bundled SEC ticker snapshot"):
            client.map_ticker("NOTREAL")


def test_packaged_sec_snapshot_covers_public_demo_tickers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = SecClient(cache_dir=Path(tmp) / "cache")
        client.get_json = lambda url, ttl_seconds: (_ for _ in ()).throw(
            SecClientError("HTTP Error 403: Forbidden")
        )  # type: ignore[method-assign]

        identities = {
            ticker: client.map_ticker(ticker)
            for ticker in ("AAPL", "BABA", "NVDA", "TSLA", "GS")
        }

        assert identities["AAPL"].cik == "0000320193"
        assert identities["BABA"].cik == "0001577552"
        assert identities["NVDA"].cik == "0001045810"
        assert all(identity.name for identity in identities.values())
