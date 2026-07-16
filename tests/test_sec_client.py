from __future__ import annotations

from equity_research.models import FilingRecord
from equity_research.sec_client import SecClient


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
