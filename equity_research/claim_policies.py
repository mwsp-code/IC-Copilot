from __future__ import annotations

from dataclasses import replace
import re
from typing import Iterable

from .models import (
    ClaimEvidenceAudit,
    ClaimEvidencePolicy,
    NormalizedSourceRecord,
)


CLAIM_EVIDENCE_POLICIES = (
    ClaimEvidencePolicy(
        "financial_metric_change",
        "financial_metric_change",
        "Financial metric change",
        (
            "entity",
            "metric",
            "current_period",
            "prior_period",
            "value",
            "unit",
            "currency",
            "source",
            "citation",
        ),
        ("sec_filing", "historical_filing", "issuer_ir_report"),
        current_prior_required=True,
        numerical_reconciliation_required=True,
    ),
    ClaimEvidencePolicy(
        "acquisition_goodwill",
        "acquisition_goodwill",
        "Acquisition and goodwill",
        (
            "acquired_entity",
            "transaction_date",
            "consideration",
            "goodwill_or_intangibles",
            "source",
            "citation",
        ),
        ("sec_filing", "historical_filing", "issuer_announcement"),
        primary_or_official_required=True,
        numerical_reconciliation_required=True,
    ),
    ClaimEvidencePolicy(
        "guidance_commitment",
        "guidance_commitment",
        "Guidance or management commitment",
        (
            "speaker_or_issuer",
            "event_date",
            "metric_or_commitment",
            "target_period",
            "exact_quote",
            "source",
        ),
        (
            "sec_filing",
            "issuer_announcement",
            "issuer_management_remarks",
            "earnings_transcript",
        ),
        primary_or_official_required=True,
    ),
    ClaimEvidencePolicy(
        "contractual_exposure",
        "contractual_exposure",
        "Supplier or contractual exposure",
        (
            "counterparty_or_exposure_type",
            "period",
            "scope_or_amount",
            "terms",
            "source",
            "citation",
        ),
        (
            "sec_filing",
            "historical_filing",
            "issuer_announcement",
            "supplier_customer_announcement",
        ),
        primary_or_official_required=True,
    ),
    ClaimEvidencePolicy(
        "debt_capital_allocation",
        "debt_capital_allocation",
        "Debt and capital allocation",
        (
            "period",
            "cash_flow_or_balance",
            "metric",
            "unit",
            "currency",
            "source",
        ),
        ("sec_filing", "historical_filing", "issuer_ir_report"),
        current_prior_required=True,
        numerical_reconciliation_required=True,
    ),
    ClaimEvidencePolicy(
        "historical_market_outcome",
        "historical_market_outcome",
        "Historical price or valuation outcome",
        (
            "observation_date",
            "price_or_multiple",
            "formula_components",
            "corporate_action_status",
            "source",
        ),
        ("quantitative_historical_price", "macro_market"),
        minimum_independent_origins=2,
        numerical_reconciliation_required=True,
    ),
    ClaimEvidencePolicy(
        "regulatory_legal",
        "regulatory_legal",
        "Regulatory or legal effect",
        (
            "jurisdiction",
            "authority_or_proceeding",
            "procedural_stage",
            "event_date",
            "affected_business",
            "financial_mechanism",
            "source",
        ),
        (
            "regulator_court",
            "sec_filing",
            "issuer_announcement",
            "licensed_newswire",
            "reputable_publisher",
        ),
        primary_or_official_required=True,
    ),
    ClaimEvidencePolicy(
        "peer_industry",
        "peer_industry",
        "Peer or industry comparison",
        (
            "peer_identity",
            "metric",
            "period",
            "unit",
            "currency",
            "source",
        ),
        (
            "sec_filing",
            "global_peer_official_document",
            "issuer_ir_report",
            "industry_official_dataset",
        ),
        current_prior_required=True,
        numerical_reconciliation_required=True,
    ),
)

_POLICY_BY_ID = {item.policy_id: item for item in CLAIM_EVIDENCE_POLICIES}


def claim_evidence_policies() -> list[ClaimEvidencePolicy]:
    return list(CLAIM_EVIDENCE_POLICIES)


def policy_for_claim(claim_text: str, classification: str = "") -> ClaimEvidencePolicy:
    text = f"{classification} {claim_text}".casefold()
    if classification in {"valuation_outcome", "price_outcome"}:
        return _POLICY_BY_ID["historical_market_outcome"]
    if classification == "financing_outcome":
        return _POLICY_BY_ID["debt_capital_allocation"]
    if classification in {
        "operating_outcome",
        "macro_context",
        "competitive_context",
        "triggering_decisions",
        "hidden_risks",
        "material_differences",
    }:
        return ClaimEvidencePolicy(
            "historical_analog_context",
            "historical_analog_context",
            "Historical analog context",
            (
                "entity",
                "source",
                "publication_date",
                "reporting_period",
                "exact_excerpt",
                "citation",
            ),
            (
                "historical_filing",
                "sec_filing",
                "institutional_history",
                "external_research",
                "reputable_publisher",
                "issuer_announcement",
            ),
            minimum_independent_origins=2,
        )
    if any(token in text for token in ("goodwill", "acquisition", "intangible")):
        return _POLICY_BY_ID["acquisition_goodwill"]
    if any(token in text for token in ("guidance", "outlook", "expects", "commitment")):
        return _POLICY_BY_ID["guidance_commitment"]
    if any(token in text for token in (
        "purchase obligation",
        "supplier",
        "take-or-pay",
        "capacity commitment",
        "guarantee",
        "lease commitment",
    )):
        return _POLICY_BY_ID["contractual_exposure"]
    if any(token in text for token in (
        "debt",
        "repurchase",
        "dividend",
        "capital allocation",
        "interest expense",
        "maturity",
    )):
        return _POLICY_BY_ID["debt_capital_allocation"]
    if any(token in text for token in (
        "valuation",
        "multiple",
        "share price",
        "drawdown",
        "return",
        "enterprise value",
    )):
        return _POLICY_BY_ID["historical_market_outcome"]
    if any(token in text for token in (
        "regulat",
        "antitrust",
        "court",
        "law",
        "policy",
        "enforcement",
    )):
        return _POLICY_BY_ID["regulatory_legal"]
    if any(token in text for token in (
        "peer",
        "industry",
        "competitor",
        "read-through",
    )):
        return _POLICY_BY_ID["peer_industry"]
    return _POLICY_BY_ID["financial_metric_change"]


def apply_claim_evidence_policies(
    audits: Iterable[ClaimEvidenceAudit],
    records: Iterable[NormalizedSourceRecord],
) -> list[ClaimEvidenceAudit]:
    record_map = {item.source_id: item for item in records}
    result: list[ClaimEvidenceAudit] = []
    for audit in audits:
        policy = policy_for_claim(
            audit.claim_text,
            audit.claim_type or audit.classification,
        )
        support = [
            record_map[item]
            for item in audit.supporting_evidence_ids
            if item in record_map
        ]
        blockers = list(audit.blockers)
        required_fields = list(dict.fromkeys(
            [*audit.required_evidence_fields, *policy.required_fields]
        ))
        source_types = {item.source_type for item in support}
        if support and not source_types.intersection(policy.preferred_source_types):
            blockers.append(
                f"{policy.label} lacks an eligible driver-specific source."
            )
        if policy.primary_or_official_required and support and not any(
            item.source_tier in {1, 2}
            and item.source_type in policy.preferred_source_types
            for item in support
        ):
            blockers.append(
                f"{policy.label} requires primary or official evidence."
            )
        if (
            policy.current_prior_required
            and support
            and len({
                item.reporting_period for item in support if item.reporting_period
            }) < 2
        ):
            blockers.append(
                f"{policy.label} requires aligned current and prior periods."
            )
        if (
            policy.minimum_independent_origins > 1
            and len({item.origin_group for item in support if item.origin_group})
            < policy.minimum_independent_origins
        ):
            blockers.append(
                f"{policy.label} requires {policy.minimum_independent_origins} "
                "independent source origins."
            )
        if (
            policy.numerical_reconciliation_required
            and _contains_number(audit.claim_text)
            and audit.numerical_reconciliation != "passed"
        ):
            blockers.append(
                f"{policy.label} requires numerical reconciliation."
            )
        if support and any(
            not item.document_id
            or not item.source_url
            or not item.publication_date
            or not item.citation_id
            or item.retrieval_status in {"metadata_only", "catalog_match"}
            or item.parser_status != "parsed"
            or item.validation_status != "validated"
            for item in support
        ):
            blockers.append(
                f"{policy.label} has incomplete retrieval, parser, or citation provenance."
            )
        blockers = list(dict.fromkeys(blockers))
        result.append(replace(
            audit,
            required_evidence_fields=required_fields,
            status=(
                "passed"
                if not blockers
                else "qualitative_required"
                if support and audit.classification in {"inference", "hypothesis"}
                else "failed"
            ),
            blockers=blockers,
        ))
    return result


def _contains_number(value: str) -> bool:
    return bool(re.search(r"\b\d+(?:\.\d+)?%?\b", value or ""))
