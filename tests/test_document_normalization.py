from __future__ import annotations

from equity_research.document_normalization import (
    normalize_document_content,
    validate_llm_metric_extraction_draft,
)
from equity_research.models import (
    LlmMetricExtractionDraft,
    NormalizedSourceRecord,
)


def _source() -> NormalizedSourceRecord:
    return NormalizedSourceRecord(
        source_id="source-1",
        ticker="AAPL",
        entity_name="Apple Inc.",
        security_id="AAPL",
        source_family="primary_company",
        source_type="issuer_announcement",
        document_id="document-1",
        source_url="https://example.com/report.html",
        publication_date="2026-07-01",
        reporting_period="2026-06-30",
        section="Capital allocation",
        page_or_table="page 4",
        exact_excerpt="Capital expenditure was USD 12.5 billion.",
        metric_or_claim="Capital expenditure",
        value=12.5,
        unit="billion",
        currency="USD",
        source_tier=1,
        origin_group="issuer",
        licensing_policy="public_issuer_excerpt",
        retrieval_status="fetched_live",
        parser_status="parsed_html",
        validation_status="validated",
    )


def test_html_and_ixbrl_are_normalized_to_text() -> None:
    result = normalize_document_content(
        "<html><body><ix:nonfraction>12.5</ix:nonfraction>"
        "<table><tr><td>Capital expenditure</td></tr></table></body></html>",
        source_url="https://example.com/report.xhtml",
    )
    assert result.parser_status == "parsed_html"
    assert "Capital expenditure" in result.text


def test_image_pdf_reports_ocr_required_without_claiming_missing_data() -> None:
    result = normalize_document_content(
        b"%PDF-1.7 image-only fixture",
        source_url="https://example.com/report.pdf",
    )
    assert result.parser_status == "ocr_required"
    assert result.text == ""
    assert "OCR" in result.data_gaps[0]


def test_approved_ocr_callback_can_supply_official_document_text() -> None:
    result = normalize_document_content(
        b"%PDF-1.7 image-only fixture",
        source_url="https://example.com/report.pdf",
        ocr_extractor=lambda _raw: "Revenue was CNY 10 billion.",
    )
    assert result.parser_status == "parsed_ocr"
    assert "Revenue" in result.text


def test_llm_metric_draft_requires_quote_period_scope_and_valid_source() -> None:
    accepted, blockers = validate_llm_metric_extraction_draft(
        LlmMetricExtractionDraft(
            draft_id="draft-1",
            document_id="document-1",
            metric="Capital expenditure",
            value=12.5,
            unit="billion",
            currency="USD",
            period_end="2026-06-30",
            quote="Capital expenditure was USD 12.5 billion.",
            table_or_page="page 4",
            confidence="High",
        ),
        _source(),
    )
    assert accepted is True
    assert blockers == []


def test_llm_metric_draft_cannot_upgrade_metadata_or_invent_quote() -> None:
    source = _source()
    source.retrieval_status = "metadata_only"
    source.validation_status = "metadata_only"
    accepted, blockers = validate_llm_metric_extraction_draft(
        LlmMetricExtractionDraft(
            draft_id="draft-1",
            document_id="document-1",
            metric="Capital expenditure",
            value=99.0,
            unit="billion",
            currency="USD",
            period_end="2026-06-30",
            quote="Capital expenditure was USD 99.0 billion.",
            table_or_page="page 4",
            confidence="High",
        ),
        source,
    )
    assert accepted is False
    assert "quote_not_found_in_source" in blockers
    assert "source_not_validated" in blockers
