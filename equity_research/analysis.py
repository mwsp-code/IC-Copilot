from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import html
from math import sqrt
import re
from difflib import SequenceMatcher
from html.parser import HTMLParser
from statistics import median
from datetime import datetime, timezone

from .models import (
    ChangeEvent,
    Citation,
    DisclosureComparison,
    FilingRecord,
    FinancialMetric,
    PriorContextAudit,
    SectionText,
)
from .metric_intelligence import metric_policy_for


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "table"}:
            self._skip_depth += 1
        if tag.lower() in {"p", "div", "br", "tr", "li", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "table"} and self._skip_depth:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "tr", "li", "h1", "h2", "h3"}:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return normalize_text(" ".join(self._parts))


class _InlineXbrlExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: dict[str, dict[str, str]] = {}
        self.units: dict[str, str] = {}
        self.facts: list[dict[str, object]] = []
        self._context_id: str | None = None
        self._context_field: str | None = None
        self._unit_id: str | None = None
        self._in_measure = False
        self._fact: dict[str, object] | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        local = lower.split(":")[-1]
        values = {key.lower(): value or "" for key, value in attrs}
        if local == "context":
            self._context_id = values.get("id")
            if self._context_id:
                self.contexts.setdefault(self._context_id, {})
        elif self._context_id and local in {"explicitmember", "typedmember"}:
            self.contexts[self._context_id]["has_dimensions"] = "true"
        elif self._context_id and local in {"startdate", "enddate", "instant"}:
            self._context_field = local
            self._parts = []
        elif local == "unit":
            self._unit_id = values.get("id")
        elif self._unit_id and local == "measure":
            self._in_measure = True
            self._parts = []
        elif local == "nonfraction":
            self._fact = {
                "concept": values.get("name", "").split(":")[-1],
                "context": values.get("contextref", ""),
                "unit": values.get("unitref", ""),
                "scale": values.get("scale", "0"),
                "sign": values.get("sign", ""),
            }
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._context_field or self._in_measure or self._fact is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        local = tag.lower().split(":")[-1]
        if self._context_field and local == self._context_field:
            if self._context_id:
                self.contexts[self._context_id][self._context_field] = "".join(self._parts).strip()
            self._context_field = None
            self._parts = []
        elif self._in_measure and local == "measure":
            if self._unit_id:
                self.units[self._unit_id] = "".join(self._parts).strip().split(":")[-1]
            self._in_measure = False
            self._parts = []
        elif self._fact is not None and local == "nonfraction":
            value = _parse_inline_number("".join(self._parts), self._fact)
            if value is not None:
                self._fact["value"] = value
                self.facts.append(self._fact)
            self._fact = None
            self._parts = []
        elif local == "context":
            self._context_id = None
            self._context_field = None
        elif local == "unit":
            self._unit_id = None
            self._in_measure = False


SECTION_RULES = {
    "risk_factors": {
        "label": "Risk Factors",
        "starts": [
            r"\bitem\s+1a\.?\s+risk\s+factors\b",
            r"\bitem\s+3\.?d\.?\s+risk\s+factors\b",
            r"\bitem\s+3\.?\s+key\s+information\b[\s\S]{0,500}\brisk\s+factors\b",
            r"\bd\.?\s+risk\s+factors\b",
        ],
        "ends": [
            r"\bitem\s+1b\.?\b",
            r"\bitem\s+2\.?\b",
            r"\bitem\s+4\.?\b",
            r"\bitem\s+7\.?\b",
        ],
    },
    "mda": {
        "label": "MD&A",
        "starts": [
            r"\bitem\s+7\.?\s+management'?s\s+discussion",
            r"\bitem\s+2\.?\s+management'?s\s+discussion",
            r"\bitem\s+5\.?\s+operating\s+and\s+financial\s+review",
        ],
        "ends": [
            r"\bitem\s+3\.?\b",
            r"\bitem\s+6\.?\b",
            r"\bitem\s+7a\.?\b",
            r"\bitem\s+8\.?\b",
        ],
    },
    "legal": {
        "label": "Legal Proceedings",
        "starts": [
            r"\bitem\s+3\.?\s+legal\s+proceedings\b",
            r"\bitem\s+8\.?\s+financial\s+information\b",
        ],
        "ends": [r"\bitem\s+4\.?\b", r"\bitem\s+9\.?\b", r"\bitem\s+1a\.?\b"],
    },
}


KEYWORD_GROUPS = {
    "guidance": [
        "guidance",
        "outlook",
        "expects",
        "forecast",
        "target",
        "raise",
        "lower",
        "headwind",
        "tailwind",
    ],
    "debt_liquidity": [
        "debt",
        "liquidity",
        "covenant",
        "refinance",
        "maturity",
        "credit facility",
        "going concern",
    ],
    "margin": [
        "gross margin",
        "operating margin",
        "pricing",
        "cost pressure",
        "product mix",
        "margin expansion",
        "margin compression",
    ],
    "litigation": [
        "litigation",
        "lawsuit",
        "investigation",
        "subpoena",
        "settlement",
        "regulatory proceeding",
    ],
    "dilution": [
        "dilution",
        "shares outstanding",
        "at-the-market",
        "convertible",
        "stock-based compensation",
        "warrants",
    ],
    "customer_concentration": [
        "customer concentration",
        "major customer",
        "single customer",
        "top customer",
        "significant customer",
        "accounts for",
    ],
}

DISCLOSURE_SECTION_PREFERENCES = {
    "guidance": ["mda", "risk_factors"],
    "debt_liquidity": ["mda", "risk_factors", "legal"],
    "margin": ["mda"],
    "litigation": ["legal", "risk_factors"],
    "dilution": ["risk_factors", "mda"],
    "customer_concentration": ["risk_factors", "mda"],
}

COMPARABLE_DISCLOSURE_STATUSES = {"period_aligned", "comparable_imperfect"}
OBSERVATION_DISCLOSURE_STATUSES = {"no_comparable_prior"}
INVALID_DISCLOSURE_STATUSES = {
    "invalid_same_source",
    "invalid_form_mismatch",
    "invalid_period_mismatch",
}

DISCLOSURE_STOPWORDS = {
    "about", "above", "after", "again", "against", "also", "among", "because",
    "been", "before", "being", "between", "both", "business", "company",
    "could", "during", "each", "from", "have", "including", "into", "more",
    "other", "over", "report", "such", "than", "that", "their", "there",
    "these", "this", "through", "under", "were", "which", "while", "with",
    "would", "year", "years",
}


GUIDANCE_BOILERPLATE_PATTERNS = (
    "forward-looking statements",
    "safe harbor",
    "undue reliance",
    "words or phrases such as",
    "included in this annual report relate to",
    "other similar expressions",
    "could have a material adverse effect",
    "risk factors",
)

SUBSTANTIVE_GUIDANCE_TERMS = (
    "we expect",
    "we anticipate",
    "we forecast",
    "we guide",
    "guidance",
    "outlook for",
    "for the next quarter",
    "for fiscal",
    "for the year",
    "revenue between",
    "margin between",
    "eps between",
)


FINANCIAL_CONCEPTS = {
    "Revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "Revenue",
        "RevenueFromContractsWithCustomers",
    ],
    "Gross Profit": ["GrossProfit", "GrossIncome", "GrossProfitLoss"],
    "Cost of Revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfSales",
    ],
    "Operating Income": [
        "OperatingIncomeLoss",
        "OperatingProfitLoss",
        "ProfitLossFromOperatingActivities",
    ],
    "SG&A Expense": [
        "SellingGeneralAndAdministrativeExpense",
        "GeneralAndAdministrativeExpense",
        "AdministrativeExpense",
        "AdministrativeExpenses",
        "SellingAndAdministrativeExpense",
    ],
    "R&D Expense": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenses",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
        "TechnologyAndContentExpense",
        "TechnologyAndInfrastructureExpense",
        "ProductDevelopmentExpense",
    ],
    "Sales and Marketing Expense": [
        "SellingAndMarketingExpense",
        "SellingAndMarketingExpenses",
        "MarketingExpense",
        "MarketingExpenses",
        "MarketingAndAdvertisingExpense",
        "AdvertisingExpense",
        "SellingExpense",
        "SellingExpenses",
        "DistributionCosts",
    ],
    "Interest Expense": [
        "InterestExpenseNonOperating",
        "InterestExpense",
        "InterestAndDebtExpense",
        "InterestExpenseDebt",
        "FinanceCosts",
    ],
    "Income Tax Expense": [
        "IncomeTaxExpenseBenefit",
        "IncomeTaxExpenseContinuingOperations",
        "IncomeTaxExpense",
    ],
    "Net Income": ["NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "Total Assets": ["Assets"],
    "Total Liabilities": ["Liabilities"],
    "Stockholders' Equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "Equity",
        "EquityAttributableToOwnersOfParent",
    ],
    "Goodwill": ["Goodwill"],
    "Intangible Assets": ["FiniteLivedIntangibleAssetsNet", "IntangibleAssetsNetExcludingGoodwill"],
    "Cash": ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"],
    "Long-term Debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "NoncurrentBorrowings",
        "LongtermBorrowings",
        "NoncurrentInterestBearingBorrowings",
    ],
    "Current Debt": [
        "DebtCurrent",
        "ShortTermDebt",
        "ShortTermBorrowings",
        "CurrentPortionOfLongTermDebt",
        "LongTermDebtCurrent",
        "ShortTermDebtAndCurrentMaturitiesOfLongTermDebt",
        "CurrentPortionOfLongTermDebtAndFinanceLeaseObligations",
        "CommercialPaper",
        "CurrentBorrowings",
        "CurrentInterestBearingBorrowings",
    ],
    "Operating Cash Flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "CashFlowsFromUsedInOperatingActivities",
        "NetCashFlowsFromUsedInOperatingActivities",
    ],
    "Funds From Operations": ["FundsFromOperations", "FundsFromOperationsAvailableToCommonShareholdersBasic"],
    "Dividends Paid": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"],
    "Share Repurchases": [
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfCommonShares",
        "StockRepurchasedDuringPeriodValue",
        "ShareRepurchases",
        "RepurchaseOfOrdinaryShares",
    ],
    "Credit Loss Provision": ["ProvisionForCreditLosses", "ProvisionForLoanLeaseAndOtherLosses"],
    "Insurance Revenue": ["PremiumsEarnedNet", "InsuranceRevenue"],
    "Capital Expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "CapitalExpenditures",
        "CapitalExpenditure",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForProceedsFromProductiveAssets",
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PropertyPlantAndEquipmentAdditions",
        "PaymentsForCapitalImprovements",
        "PurchasesOfPropertyPlantAndEquipment",
        "PurchasesOfPropertyAndEquipment",
    ],
    "Shares": [
        "EntityCommonStockSharesOutstanding",
        "NumberOfSharesOutstanding",
        "OrdinarySharesNumber",
    ],
}

SUPPORTED_FINANCIAL_FORMS = {"10-K", "10-Q", "20-F", "40-F", "6-K"}
FACT_TAXONOMY_ORDER = ("us-gaap", "ifrs-full", "dei")


def html_to_text(raw: str) -> str:
    if "<html" not in raw[:5000].lower() and "<document" not in raw[:5000].lower():
        return normalize_text(raw)
    parser = _TextExtractor()
    parser.feed(raw)
    return parser.text()


def normalize_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"\xa0", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def extract_key_sections(text: str, filing: FilingRecord) -> dict[str, SectionText]:
    sections: dict[str, SectionText] = {}
    lower_text = text.lower()
    for key, rule in SECTION_RULES.items():
        section_text = _extract_section(lower_text, text, rule["starts"], rule["ends"])
        if not section_text:
            continue
        sections[key] = SectionText(
            name=rule["label"],
            text=section_text,
            citation=Citation(
                source=f"{filing.form} {filing.filing_date}",
                url=filing.url,
                filed=filing.filing_date,
                form=filing.form,
                section=rule["label"],
                snippet=snippet(section_text),
                accession=filing.accession,
                period_end=filing.report_date,
                retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source_tier=1,
            ),
        )
    return sections


def compare_filing_pair(
    current_filing: FilingRecord,
    current_text: str,
    previous_filing: FilingRecord | None,
    previous_text: str | None,
    prior_search_audit: dict[str, object] | None = None,
) -> list[ChangeEvent]:
    current_sections = extract_key_sections(current_text, current_filing)
    previous_sections = (
        extract_key_sections(previous_text, previous_filing) if previous_filing and previous_text else {}
    )
    events: list[ChangeEvent] = []

    for section_key, section in current_sections.items():
        previous = previous_sections.get(section_key)
        if previous:
            similarity = SequenceMatcher(
                None,
                _sample_for_similarity(previous.text),
                _sample_for_similarity(section.text),
            ).ratio()
            if similarity < 0.88:
                events.append(
                    ChangeEvent(
                        category=section_key,
                        title=f"{section.name} language changed",
                        summary=(
                            f"{section.name} changed versus the prior {previous_filing.form}; "
                            f"text similarity is {similarity:.0%}."
                        ),
                        severity=_severity_from_similarity(similarity),
                        direction="mixed",
                        event_date=current_filing.filing_date,
                        source=current_filing.form,
                        citations=[section.citation],
                        metrics={"text_similarity": similarity},
                        event_timestamp=current_filing.accepted_at,
                    )
                )
        else:
            events.append(
                ChangeEvent(
                    category=section_key,
                    title=f"{section.name} section detected",
                    summary=f"The filing includes a {section.name} section available for review.",
                    severity=2,
                    direction="neutral",
                    event_date=current_filing.filing_date,
                    source=current_filing.form,
                    citations=[section.citation],
                    event_timestamp=current_filing.accepted_at,
                )
            )

    for category, words in KEYWORD_GROUPS.items():
        comparison = build_disclosure_comparison(
            category,
            words,
            current_filing,
            current_text,
            current_sections,
            previous_filing,
            previous_text,
            previous_sections,
            prior_search_audit,
        )
        if comparison.current_mentions < 3:
            continue
        if (
            comparison.comparison_status in COMPARABLE_DISCLOSURE_STATUSES
            and comparison.materiality_score < 20
            and abs(comparison.mention_rate_delta or 0) < 0.75
        ):
            continue
        selected_text = _selected_current_text_for_comparison(comparison, current_sections, current_text)
        evidence = keyword_snippets(selected_text, words, max_items=2) or keyword_snippets(current_text, words, max_items=2)
        event_metrics = _disclosure_metrics(comparison)
        if category == "guidance":
            evidence = substantive_guidance_snippets(evidence)
            if not evidence:
                continue
            numeric_guidance = next(
                (parsed for item in evidence if (parsed := extract_numeric_guidance(item))),
                None,
            )
            if numeric_guidance:
                event_metrics.update(numeric_guidance)
            elif not any(_contains_substantive_guidance(item) for item in evidence):
                continue
            event_metrics["substantive_evidence_count"] = len(evidence)
        direction = _disclosure_direction(category, comparison, selected_text)
        events.append(
            ChangeEvent(
                category=category,
                title=_disclosure_title(category, comparison),
                summary=comparison.interpretation,
                severity=_severity_from_disclosure(comparison),
                direction=direction,
                event_date=current_filing.filing_date,
                source=current_filing.form,
                citations=_disclosure_event_citations(
                    comparison, current_filing, previous_filing, evidence, category,
                ),
                metrics=event_metrics,
                event_timestamp=current_filing.accepted_at,
            )
        )

    return dedupe_events(events)


def _disclosure_event_citations(
    comparison: DisclosureComparison,
    current_filing: FilingRecord,
    previous_filing: FilingRecord | None,
    evidence: list[str],
    category: str,
) -> list[Citation]:
    retrieved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    citations = [
        Citation(
            source=f"{current_filing.form} {current_filing.filing_date}",
            url=current_filing.url,
            filed=current_filing.filing_date,
            form=current_filing.form,
            section=comparison.current_section or category.replace("_", " ").title(),
            snippet=evidence_item,
            accession=current_filing.accession,
            period_end=current_filing.report_date,
            retrieved_at=retrieved_at,
            source_tier=1,
        )
        for evidence_item in evidence
    ]
    if previous_filing and comparison.prior_excerpt:
        citations.append(Citation(
            source=f"{previous_filing.form} {previous_filing.filing_date} (prior)",
            url=previous_filing.url,
            filed=previous_filing.filing_date,
            form=previous_filing.form,
            section=comparison.prior_section or category.replace("_", " ").title(),
            snippet=comparison.prior_excerpt,
            accession=previous_filing.accession,
            period_end=previous_filing.report_date,
            retrieved_at=retrieved_at,
            source_tier=1,
        ))
    return citations


def build_disclosure_comparison(
    category: str,
    keywords: list[str],
    current_filing: FilingRecord,
    current_text: str,
    current_sections: dict[str, SectionText],
    previous_filing: FilingRecord | None,
    previous_text: str | None,
    previous_sections: dict[str, SectionText] | None = None,
    prior_search_audit: dict[str, object] | None = None,
) -> DisclosureComparison:
    current_section_key, current_section_name, current_section_text = _select_disclosure_text(
        category, keywords, current_sections, current_text,
    )
    current_mentions = keyword_count(current_section_text, keywords)
    current_word_count = _word_count(current_section_text)
    current_rate = _mention_rate(current_mentions, current_word_count)
    notes: list[str] = []

    if not previous_filing:
        return _comparison_without_prior(
            category, current_filing, current_section_key, current_section_name,
            current_mentions, current_word_count, current_rate,
            "prior_filing_missing", "No prior filing with a comparable form was selected.",
            notes, prior_search_audit=prior_search_audit,
        )
    if not previous_text or not normalize_text(previous_text):
        return _comparison_without_prior(
            category, current_filing, current_section_key, current_section_name,
            current_mentions, current_word_count, current_rate,
            "prior_text_missing", "A prior filing was selected but no readable text was retrieved for comparison.",
            notes,
            previous_filing=previous_filing, prior_search_audit=prior_search_audit,
        )
    if _same_source(current_filing, current_text, previous_filing, previous_text):
        return _invalid_comparison(
            category, current_filing, previous_filing, current_section_key, current_section_name,
            current_mentions, current_word_count, current_rate,
            "invalid_same_source", "same_document_detected",
            "The prior comparison points to the same accession or identical filing text.",
            prior_search_audit=prior_search_audit,
        )
    if not _forms_comparable(current_filing.form, previous_filing.form):
        return _invalid_comparison(
            category, current_filing, previous_filing, current_section_key, current_section_name,
            current_mentions, current_word_count, current_rate,
            "invalid_form_mismatch", "form_type_mismatch",
            f"{current_filing.form} should not be compared directly with {previous_filing.form}.",
            prior_search_audit=prior_search_audit,
        )
    if not _periods_comparable(current_filing, previous_filing):
        return _invalid_comparison(
            category, current_filing, previous_filing, current_section_key, current_section_name,
            current_mentions, current_word_count, current_rate,
            "invalid_period_mismatch", "period_mismatch",
            "The prior filing period is not earlier than the current filing period.",
            prior_search_audit=prior_search_audit,
        )

    previous_sections = previous_sections or {}
    prior_section_key, prior_section_name, prior_section_text = _select_prior_disclosure_text(
        category,
        keywords,
        current_section_key,
        previous_sections,
        previous_text,
    )
    if not prior_section_text:
        return _comparison_without_prior(
            category, current_filing, current_section_key, current_section_name,
            current_mentions, current_word_count, current_rate,
            "prior_section_missing",
            "A prior filing was retrieved, but no comparable prior section or keyword context was extracted.",
            notes,
            previous_filing=previous_filing, prior_search_audit=prior_search_audit,
        )

    prior_mentions = keyword_count(prior_section_text, keywords)
    prior_word_count = _word_count(prior_section_text)
    prior_rate = _mention_rate(prior_mentions, prior_word_count)
    semantic_similarity = _semantic_similarity(current_section_text, prior_section_text)
    topic_drift = None if semantic_similarity is None else round(1 - semantic_similarity, 4)
    length_change = _pct_change(current_word_count, prior_word_count)
    added_sentences, removed_sentences = _sentence_delta(current_section_text, prior_section_text, keywords)
    changed_phrases = _changed_phrase_pairs(added_sentences, removed_sentences)
    status = "period_aligned" if current_section_key == prior_section_key else "comparable_imperfect"
    reason = "same_section_period_aligned" if status == "period_aligned" else "section_matched_by_topic"
    if semantic_similarity is not None and semantic_similarity < 0.12:
        status = "comparable_imperfect"
        reason = "section_similarity_low"
        notes.append("Comparable text was found, but semantic overlap is low; review the section mapping.")
    if prior_section_key == "keyword_context":
        status = "comparable_imperfect"
        reason = "prior_section_aligned_by_keyword_context"
        notes.append("Prior text was recovered from keyword-context windows because a named section did not align.")

    rate_delta = round(current_rate - prior_rate, 4)
    materiality = _materiality_score(
        current_mentions,
        prior_mentions,
        current_rate,
        prior_rate,
        length_change,
        topic_drift,
        len(added_sentences),
        len(removed_sentences),
    )
    relevance = _investment_relevance(category, materiality, current_mentions, len(added_sentences))
    comparison_type = "Change" if status in COMPARABLE_DISCLOSURE_STATUSES else "Observation"
    work_order = _disclosure_work_order(
        category,
        status,
        reason,
        current_filing,
        previous_filing,
        current_section_name,
        prior_section_name,
    )
    return DisclosureComparison(
        comparison_status=status,
        reason_code=reason,
        comparison_type=comparison_type,
        confidence=_comparison_confidence(status, reason, semantic_similarity),
        current_form=current_filing.form,
        current_accession=current_filing.accession,
        current_filing_date=current_filing.filing_date,
        current_period=current_filing.report_date,
        current_section=current_section_name,
        current_section_key=current_section_key,
        prior_form=previous_filing.form,
        prior_accession=previous_filing.accession,
        prior_filing_date=previous_filing.filing_date,
        prior_period=previous_filing.report_date,
        prior_section=prior_section_name,
        prior_section_key=prior_section_key,
        current_mentions=current_mentions,
        prior_mentions=prior_mentions,
        current_word_count=current_word_count,
        prior_word_count=prior_word_count,
        current_mentions_per_1000_words=current_rate,
        prior_mentions_per_1000_words=prior_rate,
        mention_rate_delta=rate_delta,
        section_length_change_pct=length_change,
        semantic_similarity=semantic_similarity,
        topic_drift_score=topic_drift,
        added_sentence_count=len(added_sentences),
        removed_sentence_count=len(removed_sentences),
        materiality_score=materiality,
        investment_relevance=relevance,
        interpretation=_disclosure_interpretation(
            category, status, current_filing, previous_filing, current_section_name,
            prior_section_name, current_rate, prior_rate, rate_delta, length_change,
            semantic_similarity, len(added_sentences), len(removed_sentences), materiality,
        ),
        research_work_order=work_order,
        notes=notes,
        prior_context_audit=_prior_context_audit(
            current_filing,
            previous_filing,
            status="prior_loaded_zero_mentions" if prior_mentions == 0 else "comparison_ready",
            prior_search_audit=prior_search_audit,
            text_loaded=True,
            text_parsed=True,
            section_matched=True,
            zero_mentions_is_valid=prior_mentions == 0,
        ),
        alignment_type="same_section" if current_section_key == prior_section_key else "same_topic",
        current_url=current_filing.url,
        prior_url=previous_filing.url,
        current_excerpt=_comparison_excerpt(current_section_text, keywords),
        prior_excerpt=_comparison_excerpt(prior_section_text, keywords),
        added_sentences=added_sentences[:8],
        removed_sentences=removed_sentences[:8],
        changed_phrases=changed_phrases[:8],
        affected_driver=_disclosure_driver(category),
        semantic_direction=_semantic_disclosure_direction(category, rate_delta, current_section_text),
        required_confirmation=_disclosure_confirmation_requirements(category),
        thesis_grade_status=(
            "Eligible for validation"
            if changed_phrases and materiality >= 45
            else "Watch Item"
        ),
    )


def _comparison_without_prior(
    category: str,
    current_filing: FilingRecord,
    current_section_key: str,
    current_section_name: str,
    current_mentions: int,
    current_word_count: int,
    current_rate: float,
    reason_code: str,
    note: str,
    notes: list[str],
    previous_filing: FilingRecord | None = None,
    prior_search_audit: dict[str, object] | None = None,
) -> DisclosureComparison:
    search = prior_search_audit or {}
    audit_status = {
        "prior_filing_missing": "prior_not_found" if search.get("search_attempted") else "prior_not_attempted",
        "prior_text_missing": (
            "prior_parse_failed" if search.get("parse_failed")
            else "prior_fetch_failed" if search.get("discovery_error")
            else "prior_text_empty"
        ),
        "prior_section_missing": "prior_section_missing",
    }.get(reason_code, "prior_unavailable")
    work_order = _disclosure_work_order(
        category,
        "no_comparable_prior",
        reason_code,
        current_filing,
        previous_filing,
        current_section_name,
        None,
    )
    return DisclosureComparison(
        comparison_status="no_comparable_prior",
        reason_code=reason_code,
        comparison_type="Observation",
        confidence="Low",
        current_form=current_filing.form,
        current_accession=current_filing.accession,
        current_filing_date=current_filing.filing_date,
        current_period=current_filing.report_date,
        current_section=current_section_name,
        current_section_key=current_section_key,
        prior_form=previous_filing.form if previous_filing else None,
        prior_accession=previous_filing.accession if previous_filing else None,
        prior_filing_date=previous_filing.filing_date if previous_filing else None,
        prior_period=previous_filing.report_date if previous_filing else None,
        current_mentions=current_mentions,
        prior_mentions=None,
        current_word_count=current_word_count,
        current_mentions_per_1000_words=current_rate,
        materiality_score=_observation_materiality(current_mentions, current_rate),
        investment_relevance=_investment_relevance(category, _observation_materiality(current_mentions, current_rate), current_mentions, 0),
        interpretation=(
            f"{category.replace('_', ' ').title()} discussion was detected in the current "
            f"{current_filing.form}, but the app cannot determine whether disclosure increased, "
            "decreased, or remained stable because no comparable prior section was established."
        ),
        research_work_order=work_order,
        notes=notes + [note],
        prior_context_audit=_prior_context_audit(
            current_filing,
            previous_filing,
            status=audit_status,
            prior_search_audit=prior_search_audit,
            text_loaded=bool(previous_filing and audit_status not in {"prior_fetch_failed", "prior_text_empty"}),
            text_parsed=bool(previous_filing and audit_status == "prior_section_missing"),
            section_matched=False,
            zero_mentions_is_valid=False,
            blocker=note,
        ),
        alignment_type="unavailable",
        current_url=current_filing.url,
        prior_url=previous_filing.url if previous_filing else "",
        affected_driver=_disclosure_driver(category),
        required_confirmation=_disclosure_confirmation_requirements(category),
    )


def _invalid_comparison(
    category: str,
    current_filing: FilingRecord,
    previous_filing: FilingRecord,
    current_section_key: str,
    current_section_name: str,
    current_mentions: int,
    current_word_count: int,
    current_rate: float,
    status: str,
    reason_code: str,
    note: str,
    prior_search_audit: dict[str, object] | None = None,
) -> DisclosureComparison:
    work_order = _disclosure_work_order(
        category, status, reason_code, current_filing, previous_filing, current_section_name, None,
    )
    return DisclosureComparison(
        comparison_status=status,
        reason_code=reason_code,
        comparison_type="Observation",
        confidence="Invalid",
        current_form=current_filing.form,
        current_accession=current_filing.accession,
        current_filing_date=current_filing.filing_date,
        current_period=current_filing.report_date,
        current_section=current_section_name,
        current_section_key=current_section_key,
        prior_form=previous_filing.form,
        prior_accession=previous_filing.accession,
        prior_filing_date=previous_filing.filing_date,
        prior_period=previous_filing.report_date,
        current_mentions=current_mentions,
        prior_mentions=None,
        current_word_count=current_word_count,
        current_mentions_per_1000_words=current_rate,
        materiality_score=0.0,
        investment_relevance="Low",
        interpretation=(
            f"{category.replace('_', ' ').title()} discussion was detected, but the proposed prior "
            f"comparison is invalid: {note}"
        ),
        research_work_order=work_order,
        notes=[note],
        prior_context_audit=_prior_context_audit(
            current_filing,
            previous_filing,
            status=status,
            prior_search_audit=prior_search_audit,
            text_loaded=True,
            text_parsed=True,
            section_matched=False,
            zero_mentions_is_valid=False,
            blocker=note,
        ),
        alignment_type="invalid",
        current_url=current_filing.url,
        prior_url=previous_filing.url,
        affected_driver=_disclosure_driver(category),
        required_confirmation=_disclosure_confirmation_requirements(category),
    )


def _prior_context_audit(
    current_filing: FilingRecord,
    previous_filing: FilingRecord | None,
    *,
    status: str,
    prior_search_audit: dict[str, object] | None,
    text_loaded: bool,
    text_parsed: bool,
    section_matched: bool,
    zero_mentions_is_valid: bool,
    blocker: str = "",
) -> PriorContextAudit:
    search = prior_search_audit or {}
    fallback_sources = ["sec_filing"]
    if current_filing.form.upper() in {"20-F", "40-F", "6-K"}:
        fallback_sources.extend([
            "issuer_ir_report",
            "global_peer_official_document",
            "earnings_transcript",
            "presentation",
            "agm_egm_proxy",
        ])
    parse_failed = bool(search.get("parse_failed"))
    if previous_filing is None:
        stage_history = ["prior_not_attempted"] if not search.get("search_attempted") else ["prior_not_attempted", "prior_document_not_found"]
    else:
        stage_history = ["prior_document_found"]
        if status in {"prior_fetch_failed", "prior_text_empty", "prior_parse_failed"}:
            stage_history.append(status)
        elif status in {"prior_loaded_section_missing", "prior_section_missing"}:
            stage_history.append("prior_section_missing")
        elif status == "invalid_same_source":
            stage_history.append("same_source_invalid")
        elif status == "prior_loaded_zero_mentions":
            stage_history.append("prior_loaded_no_mentions")
        elif status == "comparison_ready":
            stage_history.append("comparison_valid")
        else:
            stage_history.append(status)
    return PriorContextAudit(
        status=status,
        search_attempted=bool(search.get("search_attempted", previous_filing is not None)),
        candidates_considered=int(search.get("candidates_considered") or (1 if previous_filing else 0)),
        sources_attempted=[str(item) for item in search.get("sources_attempted", [])],
        selected_accession=previous_filing.accession if previous_filing else None,
        text_loaded=text_loaded,
        text_parsed=text_parsed,
        section_matched=section_matched,
        zero_mentions_is_valid=zero_mentions_is_valid,
        blocker=blocker,
        discovery_error=str(search.get("discovery_error") or ""),
        fallback_source_types=fallback_sources,
        contextual_comparison_eligible=True,
        llm_comparison_ready=bool(
            previous_filing and text_loaded and text_parsed and section_matched
        ),
        llm_rules=[
            "Compare only retrieved excerpts with source citations and periods.",
            "Separate same-form disclosure change from cross-source management-intent shift.",
            "Do not infer a zero prior count from missing text or parser failure.",
            "LLM output is provisional until source, period, section, and metric mapping pass deterministic validation.",
        ],
        stage_history=stage_history,
    )


def _comparison_excerpt(text: str, keywords: list[str], max_items: int = 3) -> str:
    sentences = _keyword_sentences(text, keywords, max_items)
    return snippet(" ".join(sentences) if sentences else text, 1200)


def _changed_phrase_pairs(added: list[str], removed: list[str], max_pairs: int = 8) -> list[str]:
    pairs: list[str] = []
    unused_added = list(added)
    for prior in removed:
        if not unused_added:
            break
        current = max(
            unused_added,
            key=lambda item: SequenceMatcher(None, prior.lower(), item.lower()).ratio(),
        )
        similarity = SequenceMatcher(None, prior.lower(), current.lower()).ratio()
        if similarity >= 0.18:
            pairs.append(f"Prior: {snippet(prior, 300)} | Current: {snippet(current, 300)}")
            unused_added.remove(current)
        if len(pairs) >= max_pairs:
            break
    for current in unused_added[: max(0, max_pairs - len(pairs))]:
        pairs.append(f"New disclosure: {snippet(current, 360)}")
    return pairs


def _disclosure_driver(category: str) -> str:
    return {
        "guidance": "Guidance / expectations",
        "debt_liquidity": "Debt / liquidity",
        "margin": "Gross margin / mix",
        "litigation": "Regulation / legal risk",
        "dilution": "Share count / capital return",
        "customer_concentration": "Revenue concentration / demand",
        "risk_factors": "Risk / required return",
    }.get(category, "Unmapped")


def _semantic_disclosure_direction(category: str, rate_delta: float, text: str) -> str:
    risk_categories = {"debt_liquidity", "litigation", "dilution", "customer_concentration", "risk_factors"}
    if category in risk_categories and abs(rate_delta) >= 0.75:
        return "negative" if rate_delta > 0 else "positive"
    return keyword_direction(category, text) if abs(rate_delta) >= 0.75 else "mixed"


def _disclosure_confirmation_requirements(category: str) -> list[str]:
    return {
        "guidance": ["Exact metric, period, range/value, speaker, and issuer citation", "Subsequent KPI or estimate-revision evidence"],
        "debt_liquidity": ["Cash-flow reconciliation", "Debt maturity, restricted cash, interest burden, covenant, or refinancing evidence"],
        "margin": ["Revenue, COGS, mix, pricing, and segment margin bridge", "Aligned peer margin evidence"],
        "litigation": ["Regulator, court, SEC, or issuer confirmation", "Scope, timing, and quantified exposure"],
        "dilution": ["Ordinary-share/ADS and weighted-average share reconciliation", "Buyback, issuance, split, and SBC evidence"],
        "customer_concentration": ["Customer or segment revenue evidence", "Renewal, churn, order, or demand corroboration"],
    }.get(category, ["Exact current/prior excerpts", "Mapped KPI, valuation, credit, or operating-driver evidence"])


def _select_disclosure_text(
    category: str,
    keywords: list[str],
    sections: dict[str, SectionText],
    fallback_text: str,
) -> tuple[str, str, str]:
    preferred = DISCLOSURE_SECTION_PREFERENCES.get(category, [])
    scored: list[tuple[float, str, SectionText]] = []
    for key, section in sections.items():
        count = keyword_count(section.text, keywords)
        if count <= 0:
            continue
        preference = (len(preferred) - preferred.index(key)) * 1000 if key in preferred else 0
        density = count / max(_word_count(section.text), 1) * 1000
        scored.append((preference + count * 10 + density, key, section))
    if scored:
        _score, key, section = max(scored, key=lambda item: item[0])
        return key, section.name, section.text
    window = _keyword_context_window(fallback_text, keywords)
    if window:
        return "keyword_context", category.replace("_", " ").title(), window
    return "full_text", "Full filing text", fallback_text


def _select_prior_disclosure_text(
    category: str,
    keywords: list[str],
    current_section_key: str,
    previous_sections: dict[str, SectionText],
    previous_text: str,
) -> tuple[str | None, str | None, str | None]:
    same_section = previous_sections.get(current_section_key)
    if same_section:
        return current_section_key, same_section.name, same_section.text
    key, name, text = _select_disclosure_text(category, keywords, previous_sections, "")
    if text:
        return key, name, text
    window = _keyword_context_window(previous_text, keywords)
    if window:
        return "keyword_context", category.replace("_", " ").title(), window
    return None, None, None


def _selected_current_text_for_comparison(
    comparison: DisclosureComparison,
    current_sections: dict[str, SectionText],
    current_text: str,
) -> str:
    section = current_sections.get(comparison.current_section_key)
    if section:
        return section.text
    return _keyword_context_window(current_text, KEYWORD_GROUPS.get(comparison.current_section_key, [])) or current_text


def _disclosure_metrics(comparison: DisclosureComparison) -> dict[str, object]:
    metrics = asdict(comparison)
    metrics["signal_method"] = "disclosure_change_engine"
    metrics["comparison_status"] = comparison.comparison_status
    metrics["comparison_reason_code"] = comparison.reason_code
    metrics["disclosure_event_type"] = comparison.comparison_type.lower()
    metrics["previous_mentions"] = (
        comparison.prior_mentions
        if comparison.prior_mentions != 0 or comparison.prior_context_audit.zero_mentions_is_valid
        else None
    )
    metrics["previous_form"] = comparison.prior_form
    metrics["previous_accession"] = comparison.prior_accession
    metrics["previous_filing_date"] = comparison.prior_filing_date
    metrics["previous_period"] = comparison.prior_period
    metrics["previous_section"] = comparison.prior_section
    metrics["disclosure_comparison"] = asdict(comparison)
    return metrics


def _disclosure_title(category: str, comparison: DisclosureComparison) -> str:
    label = category.replace("_", " ").title()
    if comparison.comparison_status not in COMPARABLE_DISCLOSURE_STATUSES:
        return f"{label} discussion detected"
    if (comparison.mention_rate_delta or 0) >= 0.75 or comparison.section_length_change_pct and comparison.section_length_change_pct >= 25:
        return f"{label} disclosure expanded"
    if (comparison.mention_rate_delta or 0) <= -0.75 or comparison.section_length_change_pct and comparison.section_length_change_pct <= -25:
        return f"{label} disclosure contracted"
    return f"{label} disclosure changed"


def _disclosure_direction(category: str, comparison: DisclosureComparison, text: str) -> str:
    if comparison.comparison_status not in COMPARABLE_DISCLOSURE_STATUSES:
        return "neutral"
    rate_delta = comparison.mention_rate_delta or 0
    risk_categories = {"litigation", "debt_liquidity", "dilution", "customer_concentration", "risk_factors"}
    if category in risk_categories and abs(rate_delta) >= 0.75:
        return "negative" if rate_delta > 0 else "positive"
    if abs(rate_delta) < 0.75:
        return "mixed"
    return keyword_direction(category, text)


def _severity_from_disclosure(comparison: DisclosureComparison) -> int:
    if comparison.comparison_status in INVALID_DISCLOSURE_STATUSES:
        return 2
    if comparison.comparison_status in OBSERVATION_DISCLOSURE_STATUSES:
        return 3 if comparison.materiality_score >= 35 else 2
    if comparison.materiality_score >= 70:
        return 5
    if comparison.materiality_score >= 45:
        return 4
    if comparison.materiality_score >= 20:
        return 3
    return 2


def _same_source(
    current_filing: FilingRecord,
    current_text: str,
    previous_filing: FilingRecord,
    previous_text: str,
) -> bool:
    return (
        previous_filing.accession == current_filing.accession
        or normalize_text(previous_text) == normalize_text(current_text)
    )


def _forms_comparable(current_form: str, previous_form: str) -> bool:
    if current_form == previous_form:
        return True
    annual_foreign = {"20-F", "40-F"}
    annual_domestic = {"10-K", "10-K/A"}
    return (
        current_form in annual_foreign and previous_form in annual_foreign
    ) or (
        current_form in annual_domestic and previous_form in annual_domestic
    )


def _periods_comparable(current_filing: FilingRecord, previous_filing: FilingRecord) -> bool:
    current_period = _parse_date(current_filing.report_date or current_filing.filing_date)
    previous_period = _parse_date(previous_filing.report_date or previous_filing.filing_date)
    if not current_period or not previous_period:
        return True
    return previous_period < current_period


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _keyword_context_window(text: str, keywords: list[str], max_windows: int = 6, radius: int = 900) -> str:
    if not text or not keywords:
        return ""
    lower = text.lower()
    spans: list[tuple[int, int]] = []
    for word in keywords:
        idx = lower.find(word.lower())
        if idx == -1:
            continue
        spans.append((max(0, idx - radius), min(len(text), idx + radius)))
        if len(spans) >= max_windows:
            break
    if not spans:
        return ""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return normalize_text("\n\n".join(text[start:end] for start, end in merged))


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))


def _mention_rate(mentions: int, words: int) -> float:
    if words <= 0:
        return 0.0
    return round(mentions / words * 1000, 4)


def _pct_change(current: int | float, previous: int | float | None) -> float | None:
    if previous in {None, 0}:
        return None
    return round((float(current) - float(previous)) / abs(float(previous)) * 100, 2)


def _semantic_similarity(current_text: str, prior_text: str) -> float | None:
    left = _token_counter(current_text)
    right = _token_counter(prior_text)
    if not left or not right:
        return None
    shared = set(left) & set(right)
    numerator = sum(left[token] * right[token] for token in shared)
    denominator = sqrt(sum(value * value for value in left.values())) * sqrt(
        sum(value * value for value in right.values())
    )
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _token_counter(text: str) -> Counter[str]:
    tokens = [
        token
        for token in re.findall(r"\b[a-z][a-z0-9]{3,}\b", text.lower())
        if token not in DISCLOSURE_STOPWORDS
    ]
    return Counter(tokens)


def _sentence_delta(
    current_text: str,
    prior_text: str,
    keywords: list[str],
    max_sentences: int = 80,
) -> tuple[list[str], list[str]]:
    current = _keyword_sentences(current_text, keywords, max_sentences)
    prior = _keyword_sentences(prior_text, keywords, max_sentences)
    prior_keys = {_sentence_key(item) for item in prior}
    current_keys = {_sentence_key(item) for item in current}
    added = [item for item in current if _sentence_key(item) not in prior_keys]
    removed = [item for item in prior if _sentence_key(item) not in current_keys]
    return added, removed


def _keyword_sentences(text: str, keywords: list[str], max_sentences: int) -> list[str]:
    lower_keywords = [word.lower() for word in keywords]
    sentences = re.split(r"(?<=[.!?])\s+", normalize_text(text))
    hits = [
        sentence.strip()
        for sentence in sentences
        if any(word in sentence.lower() for word in lower_keywords)
    ]
    return hits[:max_sentences]


def _sentence_key(sentence: str) -> str:
    return " ".join(re.findall(r"\b[a-z0-9]+\b", sentence.lower()))[:240]


def _materiality_score(
    current_mentions: int,
    prior_mentions: int,
    current_rate: float,
    prior_rate: float,
    length_change_pct: float | None,
    topic_drift: float | None,
    added_sentence_count: int,
    removed_sentence_count: int,
) -> float:
    mention_move = abs(current_mentions - prior_mentions) / max(prior_mentions, 1)
    rate_move = abs(current_rate - prior_rate) / max(abs(prior_rate), 0.25)
    length_move = abs(length_change_pct or 0) / 100
    sentence_move = (added_sentence_count + removed_sentence_count) / 30
    drift = topic_drift or 0
    score = 100 * min(
        1.0,
        0.30 * min(rate_move, 1)
        + 0.20 * min(mention_move, 1)
        + 0.20 * min(length_move, 1)
        + 0.20 * min(sentence_move, 1)
        + 0.10 * min(drift, 1),
    )
    return round(score, 1)


def _observation_materiality(current_mentions: int, current_rate: float) -> float:
    return round(min(45.0, current_mentions * 2 + current_rate * 3), 1)


def _investment_relevance(
    category: str,
    materiality: float,
    current_mentions: int,
    added_sentence_count: int,
) -> str:
    risk_categories = {"debt_liquidity", "litigation", "dilution", "customer_concentration"}
    if materiality >= 60 or (category in risk_categories and current_mentions >= 12 and added_sentence_count >= 3):
        return "High"
    if materiality >= 30 or current_mentions >= 8:
        return "Medium"
    return "Low"


def _comparison_confidence(status: str, reason: str, semantic_similarity: float | None) -> str:
    if status == "period_aligned" and (semantic_similarity is None or semantic_similarity >= 0.35):
        return "High"
    if status in COMPARABLE_DISCLOSURE_STATUSES:
        return "Medium" if reason != "section_similarity_low" else "Low"
    if status in INVALID_DISCLOSURE_STATUSES:
        return "Invalid"
    return "Low"


def _disclosure_interpretation(
    category: str,
    status: str,
    current_filing: FilingRecord,
    previous_filing: FilingRecord,
    current_section: str,
    prior_section: str | None,
    current_rate: float,
    prior_rate: float,
    rate_delta: float,
    length_change_pct: float | None,
    semantic_similarity: float | None,
    added_sentence_count: int,
    removed_sentence_count: int,
    materiality: float,
) -> str:
    label = category.replace("_", " ")
    direction = "increased" if rate_delta > 0 else "decreased" if rate_delta < 0 else "was unchanged"
    section_note = (
        f"{current_section} vs {prior_section}"
        if prior_section and current_section != prior_section
        else current_section
    )
    similarity_note = (
        f"semantic similarity {semantic_similarity:.0%}"
        if semantic_similarity is not None else "semantic similarity unavailable"
    )
    length_note = (
        f"section length changed {length_change_pct:+.1f}%"
        if length_change_pct is not None else "section length baseline unavailable"
    )
    return (
        f"Comparable {current_filing.form} disclosure was aligned for {label} "
        f"({section_note}; current period {current_filing.report_date}, prior period {previous_filing.report_date}). "
        f"Normalized disclosure intensity {direction} from {prior_rate:.2f} to {current_rate:.2f} mentions per 1,000 words. "
        f"Sentence-level diagnostics show {added_sentence_count} added and {removed_sentence_count} removed relevant sentence(s); "
        f"{length_note}; {similarity_note}. Materiality score {materiality:.1f}/100."
    )


def _disclosure_work_order(
    category: str,
    status: str,
    reason: str,
    current_filing: FilingRecord,
    previous_filing: FilingRecord | None,
    current_section: str,
    prior_section: str | None,
) -> str:
    label = category.replace("_", " ")
    if status in COMPARABLE_DISCLOSURE_STATUSES:
        return (
            f"Review added and removed {label} sentences in {current_section}"
            + (f" versus {prior_section}" if prior_section and prior_section != current_section else "")
            + " and map any substantive wording change to a KPI, valuation, credit, or thesis-driver bridge."
        )
    if reason == "prior_filing_missing":
        return (
            f"Retrieve the previous comparable {current_filing.form} for {current_filing.accession} "
            f"before making a directional {label} disclosure-change claim."
        )
    if reason in {"prior_text_missing", "prior_section_missing", "prior_section_aligned_by_keyword_context"}:
        prior = previous_filing.accession if previous_filing else "the prior filing"
        return (
            f"Retrieve and align the prior {current_filing.form} {label} section for {prior}; "
            "confirm accession, period, section heading, and parser output before treating missing text as zero."
        )
    if status in INVALID_DISCLOSURE_STATUSES:
        return (
            f"Replace the invalid {label} comparison with a distinct same-issuer, same-form, earlier-period filing "
            "and aligned section text."
        )
    return f"Validate current and prior {label} disclosure provenance before promoting the signal."


def build_financial_metrics(company_facts: dict) -> list[FinancialMetric]:
    fact_taxonomies = company_facts.get("facts", {})
    metrics: list[FinancialMetric] = []
    for metric_name, concepts in FINANCIAL_CONCEPTS.items():
        observations = _companyfact_observations_for_metric(
            fact_taxonomies,
            metric_name,
            concepts,
        )
        if not observations:
            continue

        observations.sort(key=lambda row: (row.get("end", ""), row.get("filed", "")))
        latest = observations[-1]
        previous = _previous_comparable(latest, observations[:-1])
        previous_value = float(previous["val"]) if previous else None
        value = float(latest["val"])
        yoy = None
        if previous_value not in (None, 0):
            yoy = (value / previous_value - 1.0) * 100
        metrics.append(
            FinancialMetric(
                name=metric_name,
                value=value,
                unit=str(latest.get("_unit") or "unit"),
                period_end=latest.get("end", ""),
                fiscal_period=latest.get("fp"),
                fiscal_year=_safe_int(latest.get("fy")),
                form=latest.get("form"),
                filed=latest.get("filed"),
                previous_value=previous_value,
                yoy_change_pct=yoy,
            )
        )
    return _with_derived_financial_metrics(metrics)


def _companyfact_observations_for_metric(
    fact_taxonomies: dict,
    metric_name: str,
    concepts: list[str],
) -> list[dict]:
    observations: list[dict] = []
    concept_rank = {concept: index for index, concept in enumerate(concepts)}
    for concept in concepts:
        concept_data = _find_concept_data(fact_taxonomies, concept)
        if not concept_data:
            continue
        units = concept_data.get("units", {})
        for unit_name in _ordered_units(metric_name, units.keys()):
            for row in units.get(unit_name, []):
                if row.get("form") not in SUPPORTED_FINANCIAL_FORMS:
                    continue
                if row.get("val") is None or not row.get("end"):
                    continue
                copied = dict(row)
                copied["_unit"] = unit_name
                copied["_concept"] = concept
                copied["_concept_rank"] = concept_rank.get(concept, 999)
                observations.append(copied)
    if not observations:
        return []
    observations = _dedupe_fact_observations(observations)
    return _best_unit_observations(metric_name, observations)


def build_registration_financial_metrics(
    raw_html: str,
    filing: FilingRecord,
    preferred_currency: str | None = None,
    source_kind: str = "registration_inline_xbrl",
) -> list[FinancialMetric]:
    """Extract only explicitly tagged Inline XBRL facts from a registration filing."""
    parser = _InlineXbrlExtractor()
    parser.feed(raw_html)
    concept_to_metric = {
        concept: metric_name
        for metric_name, concepts in FINANCIAL_CONCEPTS.items()
        for concept in concepts
    }
    observations: dict[str, list[dict[str, object]]] = {}
    for fact in parser.facts:
        metric_name = concept_to_metric.get(str(fact.get("concept") or ""))
        context = parser.contexts.get(str(fact.get("context") or ""), {})
        period_end = context.get("instant") or context.get("enddate")
        if not metric_name or not period_end:
            continue
        start = context.get("startdate")
        fiscal_period = _inline_fiscal_period(start, period_end)
        duration_days = _inline_duration_days(start, period_end)
        unit_id = str(fact.get("unit") or "")
        unit = parser.units.get(unit_id) or _inline_unit(unit_id, metric_name)
        observations.setdefault(metric_name, []).append(
            {
                "val": fact["value"],
                "end": period_end,
                "filed": filing.filing_date,
                "form": filing.form,
                "fp": fiscal_period,
                "fy": _safe_int(period_end[:4]),
                "unit": unit,
                "start": start,
                "duration_days": duration_days,
                "has_dimensions": context.get("has_dimensions") == "true",
            }
        )

    metrics: list[FinancialMetric] = []
    for metric_name, rows in observations.items():
        rows = _select_inline_metric_unit(rows, preferred_currency)
        rows = _select_inline_metric_period(rows, filing)
        if not rows:
            continue
        rows.sort(key=lambda row: (str(row["end"]), str(row.get("fp") or "")))
        latest = rows[-1]
        previous = _previous_comparable(latest, rows[:-1])
        value = float(latest["val"])
        previous_value = float(previous["val"]) if previous else None
        yoy = None
        if previous_value not in (None, 0):
            yoy = (value / previous_value - 1.0) * 100
        metrics.append(
            FinancialMetric(
                name=metric_name,
                value=value,
                unit=str(latest["unit"]),
                period_end=str(latest["end"]),
                fiscal_period=str(latest.get("fp") or "") or None,
                fiscal_year=_safe_int(latest.get("fy")),
                form=filing.form,
                filed=filing.filing_date,
                previous_value=previous_value,
                yoy_change_pct=yoy,
                source_url=filing.url,
                accession=filing.accession,
                source_kind=source_kind,
            )
        )
    return _with_derived_financial_metrics(sorted(metrics, key=lambda item: item.name))


def build_periodic_inline_xbrl_financial_metrics(
    raw_html: str,
    filing: FilingRecord,
    preferred_currency: str | None = None,
) -> list[FinancialMetric]:
    return build_registration_financial_metrics(
        raw_html,
        filing,
        preferred_currency=preferred_currency,
        source_kind="periodic_inline_xbrl",
    )


def _select_inline_metric_unit(
    rows: list[dict[str, object]],
    preferred_currency: str | None,
) -> list[dict[str, object]]:
    by_unit: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_unit.setdefault(str(row.get("unit") or "unit"), []).append(row)
    if not by_unit:
        return []
    preferred = (preferred_currency or "").upper()
    if preferred and preferred in by_unit:
        return by_unit[preferred]
    if "USD" in by_unit:
        return by_unit["USD"]
    return max(
        by_unit.values(),
        key=lambda unit_rows: (
            len({str(row.get("end") or "") for row in unit_rows}),
            max((str(row.get("end") or "") for row in unit_rows), default=""),
            len(unit_rows),
        ),
    )


def _select_inline_metric_period(
    rows: list[dict[str, object]],
    filing: FilingRecord,
) -> list[dict[str, object]]:
    consolidated = [row for row in rows if not row.get("has_dimensions")]
    if consolidated:
        rows = consolidated
    form = (filing.form or "").upper()
    preferred_fp = "FY" if form in {"10-K", "20-F", "40-F", "S-1", "F-1", "424B4"} else "Q" if form == "10-Q" else None
    if not preferred_fp:
        return rows
    duration_rows = [row for row in rows if row.get("duration_days") is not None]
    preferred = [row for row in duration_rows if row.get("fp") == preferred_fp]
    if preferred:
        return preferred
    # Instant balance-sheet facts do not have a duration and remain valid for
    # either annual or quarterly filings.
    instant = [row for row in rows if row.get("duration_days") is None]
    if instant:
        return instant
    return preferred or rows


def _with_derived_financial_metrics(metrics: list[FinancialMetric]) -> list[FinancialMetric]:
    by_name = {metric.name: metric for metric in metrics}
    additions: list[FinancialMetric] = []
    revenue = by_name.get("Revenue")
    cost = by_name.get("Cost of Revenue")
    gross_profit = by_name.get("Gross Profit")
    if not gross_profit and revenue and cost and _same_metric_basis(revenue, cost):
        previous_value = None
        if revenue.previous_value is not None and cost.previous_value is not None:
            previous_value = revenue.previous_value - abs(cost.previous_value)
        value = revenue.value - abs(cost.value)
        yoy = (value / previous_value - 1.0) * 100 if previous_value not in (None, 0) else None
        gross_profit = FinancialMetric(
            name="Gross Profit",
            value=value,
            unit=revenue.unit,
            period_end=revenue.period_end,
            fiscal_period=revenue.fiscal_period,
            fiscal_year=revenue.fiscal_year,
            form=revenue.form,
            filed=revenue.filed,
            previous_value=previous_value,
            yoy_change_pct=yoy,
            source_url=revenue.source_url or cost.source_url,
            accession=revenue.accession or cost.accession,
            source_kind="derived_companyfacts",
        )
        additions.append(gross_profit)
    if revenue and gross_profit and revenue.value and _same_metric_basis(revenue, gross_profit) and "Gross Margin" not in by_name:
        previous_value = None
        if revenue.previous_value not in (None, 0) and gross_profit.previous_value is not None:
            previous_value = gross_profit.previous_value / revenue.previous_value * 100
        value = gross_profit.value / revenue.value * 100
        additions.append(FinancialMetric(
            name="Gross Margin",
            value=value,
            unit="%",
            period_end=revenue.period_end,
            fiscal_period=revenue.fiscal_period,
            fiscal_year=revenue.fiscal_year,
            form=revenue.form,
            filed=revenue.filed,
            previous_value=previous_value,
            yoy_change_pct=(value - previous_value) if previous_value is not None else None,
            source_url=revenue.source_url or gross_profit.source_url,
            accession=revenue.accession or gross_profit.accession,
            source_kind="derived_companyfacts",
        ))
    if not additions:
        return metrics
    return sorted(metrics + additions, key=lambda item: item.name)


def _same_metric_basis(left: FinancialMetric, right: FinancialMetric) -> bool:
    return left.unit == right.unit and left.period_end == right.period_end


def financial_change_events(metrics: list[FinancialMetric], facts_url: str) -> list[ChangeEvent]:
    by_name = {metric.name: metric for metric in metrics}
    events: list[ChangeEvent] = []
    revenue = by_name.get("Revenue")
    gross_profit = by_name.get("Gross Profit")
    operating_income = by_name.get("Operating Income")
    long_term_debt = by_name.get("Long-term Debt")

    for metric in metrics:
        if metric.yoy_change_pct is None or abs(metric.yoy_change_pct) < 8:
            continue
        share_basis_needs_check = (
            metric.name == "Shares"
            and abs(metric.yoy_change_pct) >= 30
        )
        policy = metric_policy_for(metric.name)
        direction = "positive" if metric.yoy_change_pct > 0 else "negative"
        if metric.name in {"Long-term Debt", "Current Debt"}:
            direction = "negative" if metric.yoy_change_pct > 0 else "positive"
        if share_basis_needs_check or policy.default_polarity == "neutral":
            direction = "neutral"
        source_label = _metric_source_label(metric)
        citation_source = _metric_citation_source(metric)
        events.append(
            ChangeEvent(
                category="financial_kpi",
                title=(
                    "Shares basis requires normalization"
                    if share_basis_needs_check
                    else f"{metric.name} changed {metric.yoy_change_pct:+.1f}%"
                ),
                summary=(
                    (
                        f"{metric.name} was {format_number(metric.value)} {metric.unit} "
                        f"for {metric.period_end}, versus {format_number(metric.previous_value)} previously. "
                        "The magnitude of the change suggests a possible ADR, split, security-basis, "
                        "or XBRL concept mismatch; do not interpret it as dilution or buyback until normalized."
                    )
                    if share_basis_needs_check
                    else (
                        f"{metric.name} was {format_number(metric.value)} {metric.unit} "
                        f"for {metric.period_end}, versus {format_number(metric.previous_value)} previously."
                    )
                ),
                severity=3 if share_basis_needs_check else min(5, max(2, int(abs(metric.yoy_change_pct) // 10) + 1)),
                direction=direction,
                event_date=metric.filed or metric.period_end,
                source=source_label,
                citations=[
                    Citation(
                        source=citation_source,
                        url=metric.source_url or facts_url,
                        filed=metric.filed,
                        form=metric.form,
                        section=metric.name,
                        snippet=f"{metric.name}: {format_number(metric.value)} {metric.unit}",
                        accession=metric.accession,
                        period_end=metric.period_end,
                        retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        source_tier=1,
                    )
                ],
                metrics={
                    "metric_name": metric.name,
                    "yoy_change_pct": metric.yoy_change_pct,
                    "value": metric.value,
                    "current_value": metric.value,
                    "previous_value": metric.previous_value,
                    "unit": metric.unit,
                    "metric_policy_key": policy.metric_key,
                    "driver_family": policy.driver_family,
                    "metric_interpretation": policy.interpretation,
                    "default_polarity": policy.default_polarity,
                    "direction_validation_required": policy.default_polarity == "neutral",
                    "constructive_mechanisms": list(policy.constructive_mechanisms),
                    "adverse_mechanisms": list(policy.adverse_mechanisms),
                    "required_evidence": list(policy.required_evidence),
                    "valuation_effects": list(policy.valuation_effects),
                    "credit_effects": list(policy.credit_effects),
                    "falsification_tests": list(policy.falsification_tests),
                    "normalization_required": share_basis_needs_check,
                    "normalization_reason": (
                        "Share-count change exceeds 30%; verify ADR ratio, ordinary-share basis, split/corporate action, "
                        "and comparable XBRL concept before using it as dilution evidence."
                        if share_basis_needs_check else ""
                    ),
                },
            )
        )

    if revenue and gross_profit and revenue.value:
        gross_margin = gross_profit.value / revenue.value * 100
        prev_margin = None
        if revenue.previous_value and gross_profit.previous_value:
            prev_margin = gross_profit.previous_value / revenue.previous_value * 100
        if prev_margin is not None and abs(gross_margin - prev_margin) >= 2:
            events.append(
                ChangeEvent(
                    category="margin",
                    title=f"Gross margin moved {gross_margin - prev_margin:+.1f} pts",
                    summary=(
                        f"Gross margin is {gross_margin:.1f}% versus {prev_margin:.1f}% "
                        "in the comparable period."
                    ),
                    severity=min(5, max(2, int(abs(gross_margin - prev_margin)))),
                    direction="positive" if gross_margin > prev_margin else "negative",
                    event_date=revenue.filed or revenue.period_end,
                    source="SEC Companyfacts",
                    citations=[
                        Citation(
                            source="SEC XBRL Companyfacts",
                            url=facts_url,
                            filed=revenue.filed,
                            section="Gross margin",
                            snippet=f"Revenue {format_number(revenue.value)}, gross profit {format_number(gross_profit.value)}.",
                            period_end=revenue.period_end,
                            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            source_tier=1,
                        )
                    ],
                    metrics={
                        "metric_name": "Gross Margin",
                        "gross_margin": gross_margin,
                        "previous_gross_margin": prev_margin,
                        "current_value": gross_margin,
                        "previous_value": prev_margin,
                        "unit": "percentage points",
                    },
                )
            )

    if revenue and operating_income and revenue.value:
        operating_margin = operating_income.value / revenue.value * 100
        if abs(operating_margin) > 0:
            events.append(
                ChangeEvent(
                    category="margin",
                    title=f"Operating margin is {operating_margin:.1f}%",
                    summary="Operating profitability is available for scenario modeling.",
                    severity=2,
                    direction="positive" if operating_margin > 0 else "negative",
                    event_date=revenue.filed or revenue.period_end,
                    source="SEC Companyfacts",
                    citations=[
                        Citation(
                            source="SEC XBRL Companyfacts",
                            url=facts_url,
                            filed=revenue.filed,
                            section="Operating margin",
                            snippet=f"Operating income {format_number(operating_income.value)} on revenue {format_number(revenue.value)}.",
                            period_end=revenue.period_end,
                            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            source_tier=1,
                        )
                    ],
                    metrics={
                        "metric_name": "Operating Margin",
                        "operating_margin": operating_margin,
                        "current_value": operating_margin,
                        "unit": "percent",
                    },
                )
            )

    if long_term_debt and revenue and revenue.value:
        debt_to_revenue = long_term_debt.value / revenue.value
        if debt_to_revenue >= 0.5:
            events.append(
                ChangeEvent(
                    category="debt_liquidity",
                    title=f"Long-term debt equals {debt_to_revenue:.1f}x revenue",
                    summary="Debt load is material relative to the latest reported revenue base.",
                    severity=min(5, max(2, round(debt_to_revenue * 2))),
                    direction="negative",
                    event_date=long_term_debt.filed or long_term_debt.period_end,
                    source="SEC Companyfacts",
                    citations=[
                        Citation(
                            source="SEC XBRL Companyfacts",
                            url=facts_url,
                            filed=long_term_debt.filed,
                            section="Debt",
                            snippet=f"Long-term debt {format_number(long_term_debt.value)}; revenue {format_number(revenue.value)}.",
                            period_end=long_term_debt.period_end,
                            retrieved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            source_tier=1,
                        )
                    ],
                    metrics={"debt_to_revenue": debt_to_revenue},
                )
            )

    return dedupe_events(events)


def _metric_source_label(metric: FinancialMetric) -> str:
    if metric.source_kind == "registration_inline_xbrl":
        return "SEC registration Inline XBRL"
    if metric.source_kind and metric.source_kind != "companyfacts":
        return metric.source_kind.replace("_", " ").title()
    return "SEC Companyfacts"


def _metric_citation_source(metric: FinancialMetric) -> str:
    if metric.source_kind == "registration_inline_xbrl":
        return f"{metric.form} Inline XBRL"
    if metric.source_kind and metric.source_kind != "companyfacts":
        return f"{metric.source_kind.replace('_', ' ').title()} official document"
    return "SEC XBRL Companyfacts"


def keyword_count(text: str, keywords: list[str]) -> int:
    lower = text.lower()
    return sum(len(re.findall(re.escape(word.lower()), lower)) for word in keywords)


def extract_numeric_guidance(text: str) -> dict[str, object] | None:
    normalized = normalize_text(text)
    metric_match = re.search(
        r"\b(revenue|sales|gross margin|operating margin|earnings per share|eps|free cash flow)\b",
        normalized,
        re.IGNORECASE,
    )
    if not metric_match:
        return None
    number = r"([0-9]+(?:\.[0-9]+)?)"
    range_match = re.search(
        rf"(?:between|from|range(?:\s+of)?)?\s*[$¥￥HKDUSD CNYRMB]*\s*{number}\s*(million|billion|%|percent)?\s*(?:to|and|[-–])\s*[$¥￥HKDUSD CNYRMB]*\s*{number}\s*(million|billion|%|percent)?",
        normalized,
        re.IGNORECASE,
    )
    if not range_match:
        return None
    low = float(range_match.group(1))
    high = float(range_match.group(3))
    scale_label = (range_match.group(4) or range_match.group(2) or "").lower()
    scale = 1_000_000_000 if scale_label == "billion" else 1_000_000 if scale_label == "million" else 1
    currency = None
    if "$" in range_match.group(0) or "USD" in range_match.group(0).upper():
        currency = "USD"
    elif any(token in range_match.group(0).upper() for token in ("CNY", "RMB", "¥", "￥")):
        currency = "CNY"
    elif "HKD" in range_match.group(0).upper():
        currency = "HKD"
    metric = metric_match.group(1).upper() if metric_match.group(1).lower() == "eps" else metric_match.group(1).title()
    return {
        "guidance_metric": metric,
        "guidance_low": low * scale,
        "guidance_high": high * scale,
        "guidance_currency": currency,
        "guidance_period": None,
    }


def substantive_guidance_snippets(snippets: list[str]) -> list[str]:
    result = []
    for item in snippets:
        if _is_guidance_boilerplate(item):
            continue
        if extract_numeric_guidance(item) or _contains_substantive_guidance(item):
            result.append(item)
    return result


def _is_guidance_boilerplate(text: str) -> bool:
    lower = normalize_text(text).lower()
    return any(pattern in lower for pattern in GUIDANCE_BOILERPLATE_PATTERNS)


def _contains_substantive_guidance(text: str) -> bool:
    lower = normalize_text(text).lower()
    return any(term in lower for term in SUBSTANTIVE_GUIDANCE_TERMS)


def keyword_snippets(text: str, keywords: list[str], max_items: int = 3) -> list[str]:
    lower = text.lower()
    snippets: list[str] = []
    for word in keywords:
        idx = lower.find(word.lower())
        if idx == -1:
            continue
        start = max(0, idx - 180)
        end = min(len(text), idx + 260)
        snippets.append(snippet(text[start:end], max_chars=420))
        if len(snippets) >= max_items:
            break
    return snippets


def keyword_direction(category: str, text: str) -> str:
    lower = text.lower()
    positive_terms = ["improve", "increase", "growth", "tailwind", "expand", "raise"]
    negative_terms = ["decline", "decrease", "headwind", "risk", "adverse", "lower", "pressure"]
    positive = keyword_count(lower, positive_terms)
    negative = keyword_count(lower, negative_terms)
    if category in {"litigation", "debt_liquidity", "dilution", "customer_concentration"}:
        return "negative" if negative >= positive else "mixed"
    if positive > negative * 1.2:
        return "positive"
    if negative > positive * 1.2:
        return "negative"
    return "mixed"


def snippet(text: str, max_chars: int = 360) -> str:
    text = normalize_text(text).replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def format_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.1f}"


def annotate_change_importance(events: list[ChangeEvent]) -> list[ChangeEvent]:
    for event in events:
        if event.why_this_matters:
            continue
        event.why_this_matters = _why_change_matters(event)
    return events


def _why_change_matters(event: ChangeEvent) -> str:
    category = (event.category or "").lower()
    direction = (event.direction or "neutral").lower()
    severity = event.severity
    direction_phrase = {
        "positive": "may improve",
        "negative": "may pressure",
        "mixed": "could change",
        "neutral": "may update",
    }.get(direction, "may update")
    severity_phrase = "material" if severity >= 4 else "research-relevant"
    category_reasons = {
        "margin": "margins, earnings power, and operating leverage",
        "revenue": "growth expectations and segment momentum",
        "guidance": "forward estimates, management credibility, and near-term catalysts",
        "risk": "the risk discount, required return, or thesis break criteria",
        "debt": "balance-sheet flexibility, refinancing risk, and equity value",
        "cash": "capital return capacity, reinvestment flexibility, and downside support",
        "dilution": "per-share value, incentive alignment, and future financing risk",
        "litigation": "tail-risk assessment and valuation discount",
        "customer_concentration": "revenue durability and bargaining-power assumptions",
        "ownership_change": "control, activism, or governance risk",
        "event_catalyst": "event timing, expected revisions, and price-move attribution",
        "management_tone": "management credibility and whether qualitative claims need corroboration",
        "tone_shift": "management credibility and whether qualitative claims need corroboration",
        "qa_evasion": "disclosure quality and unresolved thesis risks",
        "guidance_specificity_change": "forecast reliability and monitorable thesis criteria",
        "strategic_priority_change": "capital allocation, KPI focus, and future evidence checks",
        "capital_allocation_change": "shareholder returns, reinvestment, and balance-sheet priorities",
        "governance_change": "board oversight, incentives, and minority-holder risk",
        "incentive_alignment": "management incentives and KPI quality",
        "shareholder_vote_signal": "governance pressure and potential strategic change",
    }
    reason = category_reasons.get(category, "the thesis, valuation assumptions, or monitoring checklist")
    return (
        f"This {severity_phrase} {category.replace('_', ' ') or 'change'} signal {direction_phrase} "
        f"{reason}. It should be cross-checked against filings, facts, price reaction, peers, "
        "and expectations before becoming an IC thesis."
    )


def dedupe_events(events: list[ChangeEvent]) -> list[ChangeEvent]:
    seen: set[tuple[str, str]] = set()
    deduped: list[ChangeEvent] = []
    for event in sorted(events, key=lambda item: item.severity, reverse=True):
        key = (event.category, event.title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _extract_section(
    lower_text: str,
    original_text: str,
    start_patterns: list[str],
    end_patterns: list[str],
) -> str | None:
    candidates: list[str] = []
    for start_pattern in start_patterns:
        for match in re.finditer(start_pattern, lower_text, re.IGNORECASE):
            start = match.start()
            end = len(original_text)
            for end_pattern in end_patterns:
                end_match = re.search(end_pattern, lower_text[match.end() + 300 :], re.IGNORECASE)
                if end_match:
                    end = min(end, match.end() + 300 + end_match.start())
            candidate = original_text[start:end]
            if len(candidate) > 700:
                candidates.append(candidate)
    if not candidates:
        return None
    return normalize_text(max(candidates, key=len))


def _sample_for_similarity(text: str, max_chars: int = 80_000) -> str:
    if len(text) <= max_chars:
        return text.lower()
    head = text[: max_chars // 2]
    tail = text[-max_chars // 2 :]
    return (head + tail).lower()


def _severity_from_similarity(similarity: float) -> int:
    if similarity < 0.55:
        return 5
    if similarity < 0.7:
        return 4
    if similarity < 0.82:
        return 3
    return 2


def _severity_from_keyword_delta(current: int, previous: int | None) -> int:
    if previous is None:
        return 3 if current >= 8 else 2
    if previous == 0:
        return 4 if current >= 8 else 3
    ratio = current / max(previous, 1)
    if ratio >= 2 or ratio <= 0.5:
        return 4
    return 3


def _preferred_unit(units: object) -> str:
    unit_names = list(units)
    for preferred in ("USD", "CNY", "HKD", "RMB", "shares", "USD/shares", "pure"):
        if preferred in unit_names:
            return preferred
    return unit_names[0] if unit_names else "unit"


def _ordered_units(metric_name: str, units: object) -> list[str]:
    unit_names = list(units)
    if not unit_names:
        return []
    preferred_order = (
        ("shares", "pure")
        if metric_name == "Shares"
        else ("USD", "CNY", "HKD", "RMB", "EUR", "JPY", "GBP", "CAD", "AUD", "BRL", "INR", "KRW", "TWD", "CHF")
    )
    ranked = {unit: index for index, unit in enumerate(preferred_order)}
    return sorted(unit_names, key=lambda unit: (ranked.get(unit, 999), unit))


def _dedupe_fact_observations(observations: list[dict]) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[object, ...]] = set()
    for row in observations:
        key = (
            row.get("_concept"),
            row.get("_unit"),
            row.get("end"),
            row.get("filed"),
            row.get("form"),
            row.get("fp"),
            row.get("fy"),
            row.get("val"),
        )
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def _best_unit_observations(metric_name: str, observations: list[dict]) -> list[dict]:
    by_unit: dict[str, list[dict]] = {}
    for row in observations:
        unit = str(row.get("_unit") or "unit")
        by_unit.setdefault(unit, []).append(row)
    if not by_unit:
        return []
    unit_order = _ordered_units(metric_name, by_unit.keys())
    unit_rank = {unit: index for index, unit in enumerate(unit_order)}

    def unit_score(item: tuple[str, list[dict]]) -> tuple[str, str, int, int]:
        unit, rows = item
        latest = max(rows, key=lambda row: (str(row.get("end") or ""), str(row.get("filed") or "")))
        return (
            str(latest.get("end") or ""),
            str(latest.get("filed") or ""),
            -unit_rank.get(unit, 999),
            len(rows),
        )

    best_unit, rows = max(by_unit.items(), key=unit_score)
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("end") or ""),
            str(row.get("filed") or ""),
            -int(row.get("_concept_rank") or 0),
        ),
    )


def _find_concept_data(fact_taxonomies: dict, concept: str) -> dict | None:
    for taxonomy in FACT_TAXONOMY_ORDER:
        taxonomy_facts = fact_taxonomies.get(taxonomy, {})
        if concept in taxonomy_facts:
            return taxonomy_facts[concept]
        matched = _case_insensitive_concept_lookup(taxonomy_facts, concept)
        if matched:
            return matched
    for taxonomy_facts in fact_taxonomies.values():
        if isinstance(taxonomy_facts, dict) and concept in taxonomy_facts:
            return taxonomy_facts[concept]
        if isinstance(taxonomy_facts, dict):
            matched = _case_insensitive_concept_lookup(taxonomy_facts, concept)
            if matched:
                return matched
    return None


def _case_insensitive_concept_lookup(taxonomy_facts: dict, concept: str) -> dict | None:
    target = concept.lower()
    for fact_name, fact_data in taxonomy_facts.items():
        if str(fact_name).lower() == target:
            return fact_data
    return None


def _previous_comparable(latest: dict, observations: list[dict]) -> dict | None:
    fp = latest.get("fp")
    fy = _safe_int(latest.get("fy"))
    if fy is not None:
        for row in reversed(observations):
            if row.get("fp") == fp and _safe_int(row.get("fy")) == fy - 1:
                return row
    if observations:
        values = [float(row["val"]) for row in observations[-4:] if row.get("val") is not None]
        if len(values) >= 2:
            synthetic = dict(observations[-1])
            synthetic["val"] = median(values)
            return synthetic
        return observations[-1]
    return None


def _parse_inline_number(text: str, fact: dict[str, object]) -> float | None:
    cleaned = html.unescape(text).strip()
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", cleaned)
    if cleaned in {"", "-", "."}:
        return None
    try:
        value = float(cleaned)
        scale = int(str(fact.get("scale") or "0"))
    except (TypeError, ValueError):
        return None
    value *= 10 ** scale
    if negative or str(fact.get("sign") or "") == "-":
        value = -abs(value)
    return value


def _inline_fiscal_period(start: str | None, end: str) -> str | None:
    days = _inline_duration_days(start, end)
    if days is None:
        return "FY"
    if days >= 300:
        return "FY"
    if 70 <= days <= 110:
        return "Q"
    return None


def _inline_duration_days(start: str | None, end: str) -> int | None:
    if not start:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
    except ValueError:
        return None


def _inline_unit(unit_id: str, metric_name: str) -> str:
    lower = unit_id.lower()
    if metric_name == "Shares" or "share" in lower:
        return "shares"
    for currency in ("USD", "CNY", "HKD", "EUR", "GBP", "JPY"):
        if currency.lower() in lower:
            return currency
    return "unit"


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
