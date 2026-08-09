from __future__ import annotations

from equity_research.models import CompanyIdentity, FilingRecord
import json

from equity_research import config
from equity_research.retrieval_audit import (
    run_retrieval_audit,
    write_retrieval_audit,
)


class AuditSecFixture:
    def map_ticker(self, ticker):
        return CompanyIdentity(
            ticker=ticker.upper(),
            cik="0000320193",
            name="Apple Inc.",
            exchange="NASDAQ",
        )

    def get_submissions(self, cik):
        return {
            "sic": "3571",
            "sicDescription": "Electronic Computers",
        }

    def get_recent_filings(self, cik, forms=None, limit=12):
        return [
            FilingRecord(
                "10-K",
                "0000320193-25-000079",
                "2025-10-31",
                "2025-09-27",
                "aapl-20250927.htm",
                "Annual report",
                "https://www.sec.gov/aapl-2025.htm",
            ),
            FilingRecord(
                "10-K",
                "0000320193-24-000123",
                "2024-11-01",
                "2024-09-28",
                "aapl-20240928.htm",
                "Annual report",
                "https://www.sec.gov/aapl-2024.htm",
            ),
        ][:limit]

    def get_comparable_filings(self, cik, form, limit=4):
        return self.get_recent_filings(cik, forms={form}, limit=limit)

    def get_company_facts(self, cik):
        return {
            "facts": {
                "us-gaap": {
                    "RevenueFromContractWithCustomerExcludingAssessedTax": {
                        "units": {
                            "USD": [
                                {
                                    "val": 100.0,
                                    "start": "2023-09-30",
                                    "end": "2024-09-28",
                                    "filed": "2024-11-01",
                                    "form": "10-K",
                                    "fp": "FY",
                                    "fy": 2024,
                                    "accn": "0000320193-24-000123",
                                },
                                {
                                    "val": 120.0,
                                    "start": "2024-09-29",
                                    "end": "2025-09-27",
                                    "filed": "2025-10-31",
                                    "form": "10-K",
                                    "fp": "FY",
                                    "fy": 2025,
                                    "accn": "0000320193-25-000079",
                                },
                            ]
                        }
                    }
                }
            }
        }

    def is_text_cached(self, url):
        return True

    def get_filing_text(self, filing):
        return (
            "<p>Revenue was 120 USD compared with 100 USD.</p>"
            "<p>Purchase obligations were 8 USD for supplier capacity.</p>"
        )

    def get_filing_documents(self, filing):
        return []

    def get_text(self, url, ttl_seconds):
        return self.get_filing_text(None)


class AuditPriceFixture:
    def historical_daily_rows(self, ticker, *, start="", end=""):
        return {
            "status": "available",
            "provider": "fixture",
            "official": False,
            "adjusted": True,
            "source_url": "https://prices.example/AAPL",
            "retrieval_status": "cached_document",
            "rows": [
                {"date": "2024-01-02", "close": 180.0},
                {"date": "2025-12-31", "close": 250.0},
            ],
        }


def test_retrieval_preflight_is_no_llm_and_reports_lifecycle() -> None:
    report = run_retrieval_audit(
        "AAPL",
        profile="fast_screening",
        sec_client=AuditSecFixture(),
        price_client=AuditPriceFixture(),
        write_reports=False,
    )
    assert report.no_llm_calls is True
    assert report.ticker == "AAPL"
    assert report.retrieval_lifecycles
    assert report.semantic_evidence_hash
    assert all(
        item.status in {
            "planned",
            "discovered",
            "catalog_match",
            "fetched_live",
            "cached_document",
            "metadata_only",
            "parsed",
            "validated",
            "contradicted",
            "unavailable",
            "licensed_manual_required",
            "failed",
        }
        for item in report.retrieval_lifecycles
    )


def test_retrieval_report_redacts_keys_and_sec_identity(
    tmp_path, monkeypatch
) -> None:
    report = run_retrieval_audit(
        "AAPL",
        profile="fast_screening",
        sec_client=AuditSecFixture(),
        price_client=AuditPriceFixture(),
        write_reports=False,
    )
    secret = "fixture-secret-value"
    monkeypatch.setattr(config, "FMP_API_KEY", secret)
    monkeypatch.setattr(
        config,
        "SEC_USER_AGENT",
        "IC Copilot test contact private@example.com",
    )
    report.data_gaps.append(
        f"https://provider.example/data?apikey={secret}"
    )
    json_path, markdown_path = write_retrieval_audit(report, tmp_path)
    raw = json_path.read_text(encoding="utf-8")
    markdown = markdown_path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "private@example.com" not in raw
    assert secret not in markdown
    assert json.loads(raw)["ticker"] == "AAPL"
