from __future__ import annotations

from equity_research.claim_policies import (
    apply_claim_evidence_policies,
    policy_for_claim,
)
from equity_research.models import ClaimEvidenceAudit, NormalizedSourceRecord


def _record(
    source_id: str,
    *,
    source_type: str = "sec_filing",
    period: str = "2025-12-31",
    origin: str = "sec.gov",
) -> NormalizedSourceRecord:
    return NormalizedSourceRecord(
        source_id=source_id,
        ticker="AAPL",
        entity_name="Apple Inc.",
        security_id="AAPL",
        source_family="primary_company",
        source_type=source_type,
        document_id=f"document-{source_id}",
        source_url=f"https://{origin}/{source_id}",
        publication_date="2026-01-30",
        reporting_period=period,
        section="Financial statements",
        page_or_table="Table 1",
        exact_excerpt="Revenue was 120 USD compared with 100 USD.",
        metric_or_claim="Revenue",
        value=120.0 if period == "2025-12-31" else 100.0,
        unit="USD",
        currency="USD",
        source_tier=1,
        origin_group=origin,
        licensing_policy="public_filing_excerpt",
        retrieval_status="cached_document",
        parser_status="parsed",
        validation_status="validated",
        citation_id=f"citation-{source_id}",
    )


def test_goodwill_claim_uses_acquisition_policy() -> None:
    policy = policy_for_claim(
        "Goodwill increased after the acquisition closed."
    )
    assert policy.policy_id == "acquisition_goodwill"
    assert policy.primary_or_official_required is True


def test_financial_change_requires_aligned_current_and_prior_periods() -> None:
    audit = ClaimEvidenceAudit(
        claim_id="claim-1",
        claim_text="Revenue increased from 100 USD to 120 USD.",
        classification="factual",
        materiality="Material",
        supporting_evidence_ids=["current"],
        period_alignment="aligned",
        entity_alignment="aligned",
        independent_origin_count=1,
        numerical_reconciliation="passed",
        status="passed",
    )
    result = apply_claim_evidence_policies(
        [audit], [_record("current")]
    )[0]
    assert result.status == "failed"
    assert any("current and prior" in item for item in result.blockers)


def test_financial_change_passes_with_two_validated_periods() -> None:
    audit = ClaimEvidenceAudit(
        claim_id="claim-1",
        claim_text="Revenue increased from 100 USD to 120 USD.",
        classification="factual",
        materiality="Material",
        supporting_evidence_ids=["current", "prior"],
        period_alignment="aligned",
        entity_alignment="aligned",
        independent_origin_count=1,
        numerical_reconciliation="passed",
        status="passed",
    )
    result = apply_claim_evidence_policies(
        [audit],
        [
            _record("current"),
            _record("prior", period="2024-12-31"),
        ],
    )[0]
    assert result.status == "passed"
    assert result.blockers == []
