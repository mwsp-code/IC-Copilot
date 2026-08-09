from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
import re
from typing import Callable

from .analysis import html_to_text
from .models import LlmMetricExtractionDraft, NormalizedSourceRecord


@dataclass
class NormalizedDocumentContent:
    text: str
    document_format: str
    parser_status: str
    page_count: int = 0
    data_gaps: list[str] = field(default_factory=list)


def normalize_document_content(
    raw: str | bytes,
    *,
    source_url: str = "",
    ocr_extractor: Callable[[bytes], str] | None = None,
) -> NormalizedDocumentContent:
    """Normalize fetched official documents without treating parse failure as absence."""
    suffix = Path(source_url.split("?", 1)[0]).suffix.casefold()
    raw_bytes = raw if isinstance(raw, bytes) else raw.encode(
        "utf-8", errors="ignore"
    )
    is_pdf = suffix == ".pdf" or raw_bytes.startswith(b"%PDF")
    if is_pdf:
        text, pages = _pdf_text(raw_bytes)
        if text:
            return NormalizedDocumentContent(
                text=text,
                document_format="pdf",
                parser_status="parsed_pdf",
                page_count=pages,
            )
        if ocr_extractor is not None:
            try:
                ocr_text = _clean_text(ocr_extractor(raw_bytes))
            except Exception:
                ocr_text = ""
            if ocr_text:
                return NormalizedDocumentContent(
                    text=ocr_text,
                    document_format="pdf_ocr",
                    parser_status="parsed_ocr",
                    page_count=pages,
                )
        return NormalizedDocumentContent(
            text="",
            document_format="pdf",
            parser_status="ocr_required",
            page_count=pages,
            data_gaps=[
                "The official PDF appears image-based or has no extractable "
                "text; an approved OCR extractor is required."
            ],
        )

    decoded = (
        raw.decode("utf-8", errors="replace")
        if isinstance(raw, bytes) else raw
    )
    lowered = decoded[:2_000].casefold()
    is_html = suffix in {".htm", ".html", ".xhtml"} or any(
        token in lowered for token in ("<html", "<body", "<table", "<ix:")
    )
    text = (
        _clean_text(
            " ".join((
                html_to_text(decoded),
                _all_html_text(decoded),
            ))
        )
        if is_html else _clean_text(decoded)
    )
    return NormalizedDocumentContent(
        text=text,
        document_format="ixbrl_or_html" if is_html else "text",
        parser_status=(
            "parsed_html" if is_html and text
            else "parsed_text" if text
            else "table_parse_failed"
        ),
        data_gaps=[] if text else ["No usable text was extracted."],
    )


def validate_llm_metric_extraction_draft(
    draft: LlmMetricExtractionDraft,
    source: NormalizedSourceRecord,
) -> tuple[bool, list[str]]:
    """Validate an LLM extraction proposal against an already fetched document."""
    blockers: list[str] = []
    if draft.document_id != source.document_id:
        blockers.append("document_id_mismatch")
    if not draft.metric.strip():
        blockers.append("metric_missing")
    if draft.value is None:
        blockers.append("value_missing")
    if not str(draft.period_end or "").strip():
        blockers.append("period_missing")
    if not str(draft.unit or "").strip():
        blockers.append("unit_missing")
    if not str(draft.currency or "").strip():
        blockers.append("currency_missing")
    if not draft.table_or_page.strip():
        blockers.append("page_or_table_missing")
    quote = _clean_text(draft.quote)
    source_excerpt = _clean_text(source.exact_excerpt)
    if not quote:
        blockers.append("exact_quote_missing")
    elif quote.casefold() not in source_excerpt.casefold():
        blockers.append("quote_not_found_in_source")
    if (
        source.retrieval_status in {"metadata_only", "catalog_match"}
        or source.validation_status != "validated"
    ):
        blockers.append("source_not_validated")
    if draft.value is not None and quote:
        numeric_tokens = {
            _normalized_number(item)
            for item in re.findall(r"-?\d[\d,]*(?:\.\d+)?", quote)
        }
        proposed = _normalized_number(str(draft.value))
        if proposed not in numeric_tokens:
            blockers.append("value_not_reconciled_to_quote")
    return not blockers, blockers


def _pdf_text(raw: bytes) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
    except Exception:
        return "", 0
    try:
        reader = PdfReader(BytesIO(raw))
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = _clean_text(page.extract_text() or "")
            if text:
                pages.append(f"[Page {page_number}] {text}")
        return "\n".join(pages), len(reader.pages)
    except Exception:
        return "", 0


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _all_html_text(value: str) -> str:
    parser = _VisibleTextParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return ""
    return _clean_text(" ".join(parser.parts))


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if (
            tag.casefold() in {"script", "style", "noscript"}
            and self._ignored_depth
        ):
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data)


def _normalized_number(value: str) -> str:
    try:
        return f"{float(value.replace(',', '')):.12g}"
    except (TypeError, ValueError):
        return value.replace(",", "").strip()
