from __future__ import annotations

from equity_research.disclosure_intelligence import attach_disclosure_intelligence
from equity_research.models import (
    ChangeEvent,
    Citation,
    CompanyDriver,
    CompanyEconomics,
    IndustryPlaybook,
    ManagementClaim,
    ManagementSourcePackage,
)


def _economics() -> CompanyEconomics:
    return CompanyEconomics(
        ticker="BABA",
        status="Available",
        business_model="China commerce, cloud, logistics, and international commerce.",
        industry_playbook=IndustryPlaybook(
            "Internet retail and cloud",
            "platform",
            key_kpis=["China commerce revenue", "Cloud revenue", "International commerce", "Buybacks", "Free cash flow"],
            peer_tickers=["TCEHY", "JD", "PDD"],
        ),
        drivers=[
            CompanyDriver("China commerce", "segment", "High", "20-F"),
            CompanyDriver("Cloud", "segment", "High", "6-K results"),
            CompanyDriver("Buybacks", "capital allocation", "High", "Issuer results"),
        ],
    )


def _event() -> ChangeEvent:
    current = "Liquidity pressure is persistent and the company is evaluating refinancing options for upcoming debt maturities."
    return ChangeEvent(
        "debt_liquidity", "Debt Liquidity discussion detected", current,
        4, "negative", "2026-05-20", "20-F",
        citations=[Citation(
            "20-F 2026", "https://www.sec.gov/current", form="20-F",
            accession="current", period_end="2026-03-31", snippet=current, source_tier=1,
        )],
        metrics={
            "signal_method": "disclosure_change_engine",
            "comparison_status": "no_comparable_prior",
            "economic_driver": "Debt / liquidity",
            "disclosure_comparison": {
                "comparison_status": "no_comparable_prior",
                "current_excerpt": current,
                "current_period": "2026-03-31",
                "affected_driver": "Debt / liquidity",
                "required_confirmation": ["Debt maturity table", "Interest expense and cash-flow bridge"],
            },
        },
    )


def test_cross_source_context_uses_only_earlier_cited_management_claim() -> None:
    event = _event()
    prior = "Management expects the liquidity pressure to be temporary and does not anticipate refinancing constraints."
    claim = ManagementClaim(
        "claim-1", "BABA", "doc-1", "demand_commentary", prior,
        "earnings_transcript", 2, "2025-11-15",
        Citation("Earnings call", "https://issuer.example/call", filed="2025-11-15", snippet=prior, source_tier=2),
        machine_readable=True,
    )
    package = ManagementSourcePackage("BABA", "Available", claims=[claim])

    comparisons = attach_disclosure_intelligence("BABA", [event], package, _economics())

    result = comparisons[0]
    assert result.comparison_type == "cross_source_context"
    assert result.status == "Provisional"
    assert result.prior_excerpt == prior
    assert result.direction == "negative"
    assert "temporary" in result.semantic_shift
    assert result.llm_status == "ready"
    intelligence = event.metrics["disclosure_intelligence"]
    assert "Cloud" in intelligence["segment_driver_candidates"]
    assert "Debt maturity schedule and refinancing sources" in intelligence["credit_liquidity_checks"]
    assert "Cloud revenue" in intelligence["peer_operating_checks"]


def test_cross_source_context_stays_unavailable_without_prior_cited_excerpt() -> None:
    event = _event()

    comparisons = attach_disclosure_intelligence(
        "BABA", [event], ManagementSourcePackage("BABA", "Unavailable"), _economics(),
    )

    assert comparisons[0].status == "Source recovery required"
    assert comparisons[0].llm_status == "not_ready"
    assert comparisons[0].prior_excerpt == ""
    assert comparisons[0].data_gaps
