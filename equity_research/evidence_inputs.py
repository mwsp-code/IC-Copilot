from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict
from datetime import date
from typing import Any, Iterable

from .models import (
    EvidenceExclusionRecord,
    EvidencePackAudit,
    InputSufficiencyDimension,
    InputSufficiencyScore,
)


EVIDENCE_SELECTION_POLICY = "quality-first-materiality-v1"
INPUT_SUFFICIENCY_THRESHOLD = 80

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.%/-]*", re.IGNORECASE)
_CONTRADICTION_TERMS = {
    "adverse",
    "contradict",
    "counter",
    "decline",
    "deteriorat",
    "falsif",
    "impair",
    "miss",
    "negative",
    "risk",
    "weak",
}


def build_quality_first_evidence_pack(
    ticker: str,
    catalog: Iterable[dict[str, Any]],
    *,
    profile_id: str,
    identity_complete: bool,
    supplemental_context: dict[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    EvidencePackAudit,
    InputSufficiencyScore,
]:
    """Return a ranked core, complete searchable catalog, and audit objects.

    Selection is independent of incoming list order. Omitted core items remain
    searchable and validatable; the exclusion audit explains every omission.
    """
    raw = [dict(item) for item in catalog if item.get("evidence_id")]
    searchable, duplicate_count = _dedupe_catalog(raw)
    source_counts = Counter(
        str(item.get("source_type") or "unknown") for item in searchable
    )
    focal_terms = _focal_terms(searchable)
    anchor_period = max(
        (_period_ordinal(str(item.get("period") or "")) for item in searchable),
        default=0,
    )
    scored: list[dict[str, Any]] = []
    for item in searchable:
        score, components = _evidence_score(
            item,
            source_counts=source_counts,
            focal_terms=focal_terms,
            anchor_period=anchor_period,
        )
        item["selection_score"] = round(score, 2)
        item["selection_components"] = components
        item["untrusted_source_content"] = True
        scored.append(item)
    scored.sort(
        key=lambda item: (
            -float(item.get("selection_score") or 0),
            str(item.get("evidence_id") or ""),
        )
    )

    core_limit = _core_limit(profile_id)
    core_eligible = [
        item for item in scored if evidence_is_claim_eligible(item)
    ]
    core_ids = {
        str(item["evidence_id"])
        for item in core_eligible[:core_limit]
    }
    core_ids.update(
        str(item["evidence_id"])
        for item in core_eligible
        if _contradiction_value(item) >= 0.7
    )
    pairs = _current_prior_pairs(scored)
    for current_id, prior_id in pairs:
        pair_items = [
            item for item in scored
            if str(item["evidence_id"]) in {current_id, prior_id}
        ]
        if (
            len(pair_items) == 2
            and all(evidence_is_claim_eligible(item) for item in pair_items)
            and (current_id in core_ids or prior_id in core_ids)
        ):
            core_ids.update((current_id, prior_id))
    core = [item for item in scored if str(item["evidence_id"]) in core_ids]
    exclusions = [
        EvidenceExclusionRecord(
            evidence_id=str(item["evidence_id"]),
            kind=str(item.get("kind") or "unknown"),
            reason=(
                _exclusion_reason(item, core_limit)
            ),
            score=float(item.get("selection_score") or 0),
            source_type=str(item.get("source_type") or ""),
            source_tier=_optional_int(item.get("source_tier")),
            period=str(item.get("period") or ""),
            paired_evidence_id=_pair_for(str(item["evidence_id"]), pairs),
        )
        for item in scored
        if str(item["evidence_id"]) not in core_ids
    ]
    paired_ids = {evidence_id for pair in pairs for evidence_id in pair}
    unpaired_change_ids = [
        str(item["evidence_id"])
        for item in scored
        if _is_change_item(item)
        and str(item["evidence_id"]) not in paired_ids
    ]
    core_missing_period_ids = [
        str(item["evidence_id"]) for item in core
        if not item.get("period")
    ]
    core_missing_provenance_ids = [
        str(item["evidence_id"]) for item in core
        if not item.get("source_url")
        and not item.get("citation_id")
        and not item.get("formula")
        and not item.get("provenance")
    ]
    audit_gaps = []
    if unpaired_change_ids:
        audit_gaps.append(
            f"{len(unpaired_change_ids)} change item(s) lack an explicit "
            "current/prior evidence pair."
        )
    if core_missing_period_ids:
        audit_gaps.append(
            f"{len(core_missing_period_ids)} core evidence item(s) lack an "
            "explicit period."
        )
    if core_missing_provenance_ids:
        audit_gaps.append(
            f"{len(core_missing_provenance_ids)} core evidence item(s) lack "
            "a source URL or citation ID."
        )
    audit = EvidencePackAudit(
        ticker=ticker.upper(),
        status=(
            "complete"
            if not unpaired_change_ids
            else "complete_with_pairing_gaps"
        ),
        available_count=len(raw),
        core_count=len(core),
        searchable_count=len(scored),
        core_evidence_ids=[str(item["evidence_id"]) for item in core],
        searchable_evidence_ids=[
            str(item["evidence_id"]) for item in scored
        ],
        exclusions=exclusions,
        current_prior_pairs=[list(pair) for pair in pairs],
        unpaired_change_ids=unpaired_change_ids,
        core_missing_period_ids=core_missing_period_ids,
        core_missing_provenance_ids=core_missing_provenance_ids,
        source_family_counts=dict(sorted(source_counts.items())),
        duplicate_count=duplicate_count,
        selection_policy=EVIDENCE_SELECTION_POLICY,
        order_invariant=True,
        data_gaps=audit_gaps,
    )
    sufficiency = score_input_sufficiency(
        ticker,
        scored,
        audit,
        identity_complete=identity_complete,
        supplemental_context=supplemental_context or {},
    )
    return core, scored, audit, sufficiency


def search_evidence_catalog(
    catalog: Iterable[dict[str, Any]],
    query: str,
    *,
    source_type: str = "",
    period: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    query_terms = set(_tokens(query))
    candidates = []
    for item in catalog:
        if source_type and str(item.get("source_type") or "") != source_type:
            continue
        if period and period not in str(item.get("period") or ""):
            continue
        haystack = " ".join(
            str(item.get(key) or "")
            for key in (
                "kind",
                "source_type",
                "period",
                "driver",
                "metric",
                "summary",
            )
        )
        item_terms = set(_tokens(haystack))
        overlap = len(query_terms & item_terms)
        lexical = overlap / max(1, len(query_terms))
        score = (
            lexical * 60
            + float(item.get("selection_score") or 0) * 0.4
        )
        if query_terms and overlap == 0:
            continue
        candidates.append((score, str(item.get("evidence_id") or ""), item))
    candidates.sort(key=lambda row: (-row[0], row[1]))
    return [dict(row[2]) for row in candidates[: max(1, limit)]]


def evidence_catalog_metadata(
    catalog: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "evidence_id": item.get("evidence_id"),
            "citation_id": item.get("citation_id"),
            "kind": item.get("kind"),
            "source_type": item.get("source_type"),
            "source_tier": item.get("source_tier"),
            "period": item.get("period"),
            "driver": item.get("driver"),
            "metric": item.get("metric"),
            "formula": item.get("formula"),
            "provenance": item.get("provenance"),
            "retrieval_status": item.get("retrieval_status"),
            "parser_status": item.get("parser_status"),
            "validation_status": item.get("validation_status"),
            "selection_score": item.get("selection_score"),
            "pair_id": item.get("pair_id"),
            "pair_role": item.get("pair_role"),
        }
        for item in catalog
    ]


def input_sufficiency_payload(
    score: InputSufficiencyScore | None,
) -> dict[str, Any]:
    return asdict(score) if score else {}


def score_input_sufficiency(
    ticker: str,
    catalog: list[dict[str, Any]],
    audit: EvidencePackAudit,
    *,
    identity_complete: bool,
    supplemental_context: dict[str, Any],
) -> InputSufficiencyScore:
    eligible_catalog = [
        item for item in catalog if evidence_is_claim_eligible(item)
    ]
    primary = [
        item for item in eligible_catalog
        if _optional_int(item.get("source_tier")) in {1, 2}
    ]
    event_primary = [
        item for item in primary
        if item.get("kind") in {"validated_claim", "source_event"}
    ]
    historical = [
        item for item in eligible_catalog
        if item.get("kind") in {
            "historical_source",
            "financial_metric_prior",
            "validated_claim_prior",
        }
    ]
    causal = [
        item for item in eligible_catalog
        if item.get("kind") in {
            "financial_metric",
            "financial_metric_prior",
            "validated_claim",
        }
        and (
            item.get("driver")
            or item.get("metric")
            or item.get("kind") == "financial_metric"
        )
    ]
    cited = [
        item for item in eligible_catalog if item.get("source_url")
    ]
    periodized = [
        item for item in eligible_catalog if item.get("period")
    ]
    contradictory = [
        item for item in eligible_catalog
        if _contradiction_value(item) >= 0.7
    ]

    peer_context = _context_has(
        supplemental_context,
        ("peer_metric_readthrough", "peer", "industry"),
    )
    management_context = _context_has(
        supplemental_context,
        ("management_sources", "management", "transcript"),
    )
    valuation_context = _context_has(
        supplemental_context,
        ("valuation", "company_model", "credit"),
    )
    market_context = _context_has(
        supplemental_context,
        ("market_implied", "expectations", "macro", "price"),
    )

    dimensions = [
        _dimension(
            "identity",
            "Identity and security mapping",
            10 if identity_complete else 0,
            10,
            "Ticker, company, exchange and filing identity are resolved."
            if identity_complete
            else "Entity or security mapping is incomplete.",
            [] if identity_complete else ["Resolve entity and security identity."],
        ),
        _dimension(
            "primary_event",
            "Primary event evidence",
            _scaled_score(len(event_primary), 2, 15),
            15,
            f"{len(event_primary)} primary event/claim item(s) are available.",
            [] if event_primary else ["No primary event evidence is available."],
        ),
        _dimension(
            "history",
            "Historical comparison",
            min(
                15,
                _scaled_score(len(audit.current_prior_pairs), 3, 10)
                + _scaled_score(len(historical), 4, 5),
            ),
            15,
            (
                f"{len(audit.current_prior_pairs)} explicit current/prior "
                f"pair(s) and {len(historical)} historical item(s)."
            ),
            list(audit.data_gaps),
        ),
        _dimension(
            "causal_kpi",
            "Causal KPI coverage",
            _scaled_score(len(causal), 8, 15),
            15,
            f"{len(causal)} metric or driver-linked item(s) are available.",
            [] if causal else ["No causal KPI evidence is normalized."],
        ),
        _binary_dimension(
            "peer_industry",
            "Peer and industry context",
            peer_context,
            10,
            "Peer or industry context is present.",
            "Peer and industry context is absent.",
        ),
        _binary_dimension(
            "management",
            "Management record",
            management_context,
            10,
            "Management, transcript, or commitment context is present.",
            "Management history is absent.",
        ),
        _binary_dimension(
            "valuation_credit",
            "Valuation and credit inputs",
            valuation_context,
            10,
            "Valuation, model, or credit inputs are present.",
            "Valuation and credit inputs are incomplete.",
        ),
        _binary_dimension(
            "market_macro",
            "Market and macro context",
            market_context,
            5,
            "Market, expectations, or macro context is present.",
            "Market and macro context is absent.",
        ),
        _dimension(
            "contradictions",
            "Contradictions and falsification",
            _scaled_score(len(contradictory), 2, 5),
            5,
            f"{len(contradictory)} contradiction/falsification item(s) retained.",
            (
                []
                if contradictory
                else ["No explicit contradiction or falsification evidence."]
            ),
        ),
        _dimension(
            "integrity",
            "Citation and period integrity",
            _integrity_score(eligible_catalog, cited, periodized),
            5,
            (
                f"{len(cited)}/{len(eligible_catalog)} claim-eligible item(s) have source URLs; "
                f"{len(periodized)}/{len(eligible_catalog)} have periods."
            ),
            _integrity_gaps(eligible_catalog, cited, periodized),
        ),
    ]
    total = sum(item.score for item in dimensions)
    critical_gaps = []
    if not identity_complete:
        critical_gaps.append("Critical identity/security mapping gap.")
    if event_primary and not any(item.get("period") for item in event_primary):
        critical_gaps.append("Critical primary-event period gap.")
    if event_primary and not any(item.get("source_url") for item in event_primary):
        critical_gaps.append("Critical primary-event citation gap.")
    if audit.core_missing_period_ids:
        critical_gaps.append(
            "Critical core-evidence period gap: "
            f"{len(audit.core_missing_period_ids)} item(s)."
        )
    if audit.core_missing_provenance_ids:
        critical_gaps.append(
            "Critical core-evidence provenance gap: "
            f"{len(audit.core_missing_provenance_ids)} item(s)."
        )
    status = (
        "benchmark_ready"
        if total >= INPUT_SUFFICIENCY_THRESHOLD and not critical_gaps
        else "input_remediation_required"
    )
    return InputSufficiencyScore(
        ticker=ticker.upper(),
        status=status,
        total_score=total,
        benchmark_ready=status == "benchmark_ready",
        threshold=INPUT_SUFFICIENCY_THRESHOLD,
        dimensions=dimensions,
        critical_gaps=critical_gaps,
        data_gaps=[
            gap for item in dimensions for gap in item.gaps
        ],
    )


def _dedupe_catalog(
    catalog: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    by_id: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for item in catalog:
        evidence_id = str(item.get("evidence_id") or "")
        existing = by_id.get(evidence_id)
        if existing is None:
            by_id[evidence_id] = item
            continue
        duplicate_count += 1
        if _catalog_richness(item) > _catalog_richness(existing):
            by_id[evidence_id] = item
    return list(by_id.values()), duplicate_count


def _catalog_richness(item: dict[str, Any]) -> tuple[int, int]:
    populated = sum(
        bool(item.get(key))
        for key in (
            "citation_id",
            "source_url",
            "period",
            "summary",
            "driver",
            "metric",
        )
    )
    return populated, len(str(item.get("summary") or ""))


def evidence_is_claim_eligible(item: dict[str, Any]) -> bool:
    retrieval_status = str(item.get("retrieval_status") or "")
    parser_status = str(item.get("parser_status") or "")
    validation_status = str(item.get("validation_status") or "")
    if retrieval_status or parser_status or validation_status:
        if retrieval_status == "metadata_only":
            return False
        if parser_status not in {"parsed", "structured_fact", "derived"}:
            return False
        if validation_status not in {"validated", "contradicted"}:
            return False
    has_provenance = bool(
        item.get("source_url")
        or item.get("citation_id")
        or item.get("formula")
        or item.get("provenance")
    )
    return bool(item.get("period") and has_provenance)


def _exclusion_reason(
    item: dict[str, Any],
    core_limit: int,
) -> str:
    gaps = []
    if not item.get("period"):
        gaps.append("period")
    if not (
        item.get("source_url")
        or item.get("citation_id")
        or item.get("formula")
        or item.get("provenance")
    ):
        gaps.append("provenance")
    if gaps:
        return (
            "Retained in the complete searchable catalog but excluded from "
            "the factual core until " + " and ".join(gaps)
            + " metadata is normalized."
        )
    return (
        "Retained in the complete searchable catalog; omitted from the core "
        "prompt because higher-scoring evidence filled the "
        f"{core_limit}-item profile budget."
    )


def _evidence_score(
    item: dict[str, Any],
    *,
    source_counts: Counter,
    focal_terms: set[str],
    anchor_period: int,
) -> tuple[float, dict[str, float]]:
    materiality = _materiality(item)
    relevance = _driver_relevance(item, focal_terms)
    alignment = _period_alignment(item, anchor_period)
    source_quality = _source_quality(item)
    contradiction = _contradiction_value(item)
    freshness = _freshness(item, anchor_period)
    source_type = str(item.get("source_type") or "unknown")
    diversity = 1 / math.sqrt(max(1, source_counts[source_type]))
    components = {
        "materiality": round(materiality * 25, 2),
        "driver_relevance": round(relevance * 20, 2),
        "period_alignment": round(alignment * 15, 2),
        "source_quality": round(source_quality * 15, 2),
        "contradiction_value": round(contradiction * 10, 2),
        "freshness": round(freshness * 10, 2),
        "source_diversity": round(diversity * 5, 2),
    }
    return sum(components.values()), components


def _materiality(item: dict[str, Any]) -> float:
    explicit = item.get("materiality")
    if explicit is not None:
        try:
            return max(0.0, min(1.0, float(explicit)))
        except (TypeError, ValueError):
            pass
    kind = str(item.get("kind") or "")
    if kind == "validated_claim":
        return 1.0 if item.get("thesis_grade") else 0.8
    if kind == "source_event":
        try:
            return max(0.2, min(1.0, float(item.get("severity") or 3) / 5))
        except (TypeError, ValueError):
            return 0.6
    if kind.startswith("financial_metric"):
        try:
            magnitude = abs(float(item.get("change_pct")))
            return min(1.0, 0.35 + math.log1p(magnitude) / 6)
        except (TypeError, ValueError):
            return 0.55
    if kind == "historical_source":
        return 0.55
    return 0.4


def _driver_relevance(
    item: dict[str, Any],
    focal_terms: set[str],
) -> float:
    direct = " ".join(
        str(item.get(key) or "")
        for key in ("driver", "metric", "summary")
    )
    terms = set(_tokens(direct))
    overlap = len(terms & focal_terms)
    if item.get("driver") or item.get("metric"):
        return min(1.0, 0.65 + overlap * 0.1)
    return min(0.7, 0.2 + overlap * 0.1)


def _period_alignment(item: dict[str, Any], anchor: int) -> float:
    ordinal = _period_ordinal(str(item.get("period") or ""))
    if not ordinal or not anchor:
        return 0.2
    distance = max(0, anchor - ordinal)
    if distance <= 100:
        return 1.0
    if distance <= 500:
        return 0.8
    if distance <= 1500:
        return 0.55
    return 0.3


def _source_quality(item: dict[str, Any]) -> float:
    tier = _optional_int(item.get("source_tier"))
    return {1: 1.0, 2: 0.82, 3: 0.58, 4: 0.3}.get(tier, 0.35)


def _contradiction_value(item: dict[str, Any]) -> float:
    if item.get("is_contradiction"):
        return 1.0
    text = str(item.get("summary") or "").casefold()
    return 0.8 if any(term in text for term in _CONTRADICTION_TERMS) else 0.1


def _freshness(item: dict[str, Any], anchor: int) -> float:
    ordinal = _period_ordinal(str(item.get("period") or ""))
    if not ordinal or not anchor:
        return 0.2
    years = max(0.0, (anchor - ordinal) / 1000)
    return max(0.15, 1.0 - years * 0.18)


def _focal_terms(catalog: list[dict[str, Any]]) -> set[str]:
    terms: list[str] = []
    for item in catalog:
        if item.get("kind") in {"validated_claim", "source_event"}:
            terms.extend(_tokens(" ".join(
                str(item.get(key) or "")
                for key in ("driver", "metric", "summary")
            )))
    counts = Counter(terms)
    return {
        term for term, count in counts.items()
        if count >= 1 and len(term) >= 3
    }


def _current_prior_pairs(
    catalog: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    by_pair: dict[str, dict[str, str]] = {}
    for item in catalog:
        pair_id = str(item.get("pair_id") or "")
        role = str(item.get("pair_role") or "")
        if pair_id and role in {"current", "prior"}:
            by_pair.setdefault(pair_id, {})[role] = str(item["evidence_id"])
    return sorted(
        (roles["current"], roles["prior"])
        for roles in by_pair.values()
        if roles.get("current") and roles.get("prior")
    )


def _pair_for(
    evidence_id: str,
    pairs: list[tuple[str, str]],
) -> str:
    for current_id, prior_id in pairs:
        if evidence_id == current_id:
            return prior_id
        if evidence_id == prior_id:
            return current_id
    return ""


def _is_change_item(item: dict[str, Any]) -> bool:
    return bool(
        item.get("change_pct") is not None
        or item.get("pair_role") == "current"
        or item.get("kind") in {"validated_claim", "source_event"}
    )


def _period_ordinal(value: str) -> int:
    match = re.search(r"((?:19|20)\d{2})[-/]?(\d{2})?[-/]?(\d{2})?", value)
    if not match:
        return 0
    year = int(match.group(1))
    month = int(match.group(2) or 1)
    day = int(match.group(3) or 1)
    try:
        return date(year, month, day).toordinal()
    except ValueError:
        return date(year, 1, 1).toordinal()


def _core_limit(profile_id: str) -> int:
    return {
        "fast_screening": 40,
        "adaptive_ic": 80,
        "investigate_event": 70,
        "deep_initiation": 160,
    }.get(profile_id, 80)


def _tokens(value: str) -> list[str]:
    return [
        token.casefold()
        for token in _TOKEN_RE.findall(value or "")
        if len(token) >= 2
    ]


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _scaled_score(count: int, target: int, maximum: int) -> int:
    if count <= 0:
        return 0
    return min(maximum, round(maximum * min(1.0, count / target)))


def _dimension(
    dimension_id: str,
    label: str,
    score: int,
    maximum: int,
    summary: str,
    gaps: list[str],
) -> InputSufficiencyDimension:
    status = (
        "passed"
        if score == maximum
        else "partial"
        if score > 0
        else "missing"
    )
    return InputSufficiencyDimension(
        dimension_id=dimension_id,
        label=label,
        score=score,
        maximum=maximum,
        status=status,
        summary=summary,
        gaps=gaps,
    )


def _binary_dimension(
    dimension_id: str,
    label: str,
    available: bool,
    maximum: int,
    available_summary: str,
    missing_summary: str,
) -> InputSufficiencyDimension:
    return _dimension(
        dimension_id,
        label,
        maximum if available else 0,
        maximum,
        available_summary if available else missing_summary,
        [] if available else [missing_summary],
    )


def _context_has(
    context: dict[str, Any],
    keys: tuple[str, ...],
) -> bool:
    for key, value in context.items():
        normalized = str(key).casefold()
        if any(term in normalized for term in keys) and _meaningful(value):
            return True
    return False


def _meaningful(value: Any) -> bool:
    if value is None or value is False:
        return False
    if value == "" or value == [] or value == {}:
        return False
    if isinstance(value, str) and value in {
        "Unknown", "Unavailable", "unavailable"
    }:
        return False
    return True


def _integrity_score(
    catalog: list[dict[str, Any]],
    cited: list[dict[str, Any]],
    periodized: list[dict[str, Any]],
) -> int:
    if not catalog:
        return 0
    source_ratio = len(cited) / len(catalog)
    period_ratio = len(periodized) / len(catalog)
    return round(5 * min(source_ratio, period_ratio))


def _integrity_gaps(
    catalog: list[dict[str, Any]],
    cited: list[dict[str, Any]],
    periodized: list[dict[str, Any]],
) -> list[str]:
    gaps = []
    if catalog and len(cited) < len(catalog):
        gaps.append("Some evidence items lack a traceable source URL.")
    if catalog and len(periodized) < len(catalog):
        gaps.append("Some evidence items lack an explicit period.")
    return gaps
