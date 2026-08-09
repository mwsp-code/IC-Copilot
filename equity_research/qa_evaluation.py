from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class QaEvaluationVariant(str, Enum):
    PRIMARY_ONLY = "primary_only"
    FROZEN_AUDIT = "primary_plus_frozen_audit"
    AUDIT_AND_SCOUT = "primary_plus_audit_and_scout"
    FULL_CLOSURE = "full_audit_scout_admission_revision_closure"


@dataclass(frozen=True)
class QaEvaluationObservation:
    variant: str
    unsupported_claims_removed: int = 0
    material_contradictions_discovered: int = 0
    evidence_gaps_closed: int = 0
    false_audit_findings: int = 0
    newly_admitted_independent_evidence: int = 0
    mappings_resolved: int = 0
    thesis_revisions: int = 0
    valuation_revisions: int = 0
    confidence_revisions: int = 0
    ignored_material_findings_seeded: int = 0
    ignored_material_findings_detected: int = 0
    integrity_violations: int = 0
    latency_ms: int = 0
    model_calls: int = 0
    total_tokens: int = 0
    provider_cost_usd: float | None = None

    def __post_init__(self) -> None:
        QaEvaluationVariant(self.variant)


@dataclass(frozen=True)
class QaEvaluationGate:
    status: str
    improved_decision_quality_measures: tuple[str, ...] = ()
    integrity_regression: bool = False
    seeded_closure_detection_passed: bool = False
    blockers: tuple[str, ...] = ()
    observations: tuple[QaEvaluationObservation, ...] = ()


_DECISION_MEASURES = (
    "unsupported_claims_removed",
    "material_contradictions_discovered",
    "evidence_gaps_closed",
    "newly_admitted_independent_evidence",
    "mappings_resolved",
    "thesis_revisions",
    "valuation_revisions",
    "confidence_revisions",
)


def evaluate_quality_first_variants(
    observations: Iterable[QaEvaluationObservation],
    *,
    minimum_improved_measures: int = 2,
) -> QaEvaluationGate:
    rows = tuple(observations)
    by_variant = {QaEvaluationVariant(row.variant): row for row in rows}
    missing = [
        variant.value for variant in QaEvaluationVariant
        if variant not in by_variant
    ]
    blockers: list[str] = []
    if missing:
        blockers.append("Missing frozen-input variants: " + ", ".join(missing))
        return QaEvaluationGate(
            status="blocked_incomplete_harness",
            blockers=tuple(blockers),
            observations=rows,
        )
    primary = by_variant[QaEvaluationVariant.PRIMARY_ONLY]
    full = by_variant[QaEvaluationVariant.FULL_CLOSURE]
    improved = tuple(
        measure for measure in _DECISION_MEASURES
        if getattr(full, measure) > getattr(primary, measure)
    )
    integrity_regression = full.integrity_violations > primary.integrity_violations
    seeded_detection = (
        full.ignored_material_findings_detected
        >= full.ignored_material_findings_seeded
    )
    if len(improved) < minimum_improved_measures:
        blockers.append(
            f"Full QA improved {len(improved)} decision-quality measure(s); "
            f"{minimum_improved_measures} are required."
        )
    if integrity_regression:
        blockers.append("Full QA introduced an evidence-integrity regression.")
    if not seeded_detection:
        blockers.append("Closure missed at least one seeded ignored material finding.")
    return QaEvaluationGate(
        status="passed" if not blockers else "blocked",
        improved_decision_quality_measures=improved,
        integrity_regression=integrity_regression,
        seeded_closure_detection_passed=seeded_detection,
        blockers=tuple(blockers),
        observations=rows,
    )


def observation_from_research_qa(
    variant: QaEvaluationVariant | str,
    qa_run: Any,
) -> QaEvaluationObservation:
    variant = QaEvaluationVariant(variant)
    report = getattr(qa_run, "qa_report", None)
    usage = _usage_from_run(qa_run)
    findings = list(getattr(qa_run, "audit_findings", []) or [])
    candidates = list(getattr(qa_run, "candidate_discovery_items", []) or [])
    resolutions = list(getattr(qa_run, "audit_resolutions", []) or [])
    revisions = list(getattr(qa_run, "conclusion_revisions", []) or [])
    closure = getattr(qa_run, "closure_result", None)
    integrity_violations = sum(
        1 for item in candidates
        if str(getattr(item, "status", "")) != "admitted"
        and bool(getattr(item, "admitted_evidence_ids", ()))
    )
    material_findings = [
        item for item in findings
        if str(getattr(item, "severity", "")).casefold()
        in {"high", "critical", "material"}
    ]
    detected = len(
        list(getattr(closure, "unaddressed_material_finding_ids", []) or [])
    )
    return QaEvaluationObservation(
        variant=variant.value,
        unsupported_claims_removed=int(
            getattr(report, "unsupported_claims_removed", 0) or 0
        ),
        material_contradictions_discovered=sum(
            "contradict" in str(getattr(item, "category", "")).casefold()
            or "counter" in str(getattr(item, "summary", "")).casefold()
            for item in material_findings
        ),
        evidence_gaps_closed=int(
            getattr(report, "evidence_gaps_closed", 0) or 0
        ),
        newly_admitted_independent_evidence=sum(
            len(getattr(item, "admitted_evidence_ids", ()) or ())
            for item in candidates
            if str(getattr(item, "origin_role", ""))
            != "primary_investment_analyst"
            and str(getattr(item, "status", "")) == "admitted"
        ),
        mappings_resolved=sum(
            str(getattr(item, "candidate_type", "")) == "mapping_candidate"
            and str(getattr(item, "status", "")) == "admitted"
            for item in candidates
        ),
        thesis_revisions=sum(
            "thesis" in str(getattr(item, "conclusion_area", "")).casefold()
            for item in revisions
        ),
        valuation_revisions=sum(
            "valuation" in str(getattr(item, "conclusion_area", "")).casefold()
            for item in revisions
        ),
        confidence_revisions=sum(
            "confidence" in str(getattr(item, "conclusion_area", "")).casefold()
            for item in revisions
        ),
        ignored_material_findings_seeded=len(material_findings),
        ignored_material_findings_detected=detected,
        integrity_violations=integrity_violations,
        latency_ms=int(usage.get("latency_ms", 0)),
        model_calls=int(usage.get("model_calls", 0)),
        total_tokens=int(usage.get("total_tokens", 0)),
        provider_cost_usd=usage.get("provider_cost_usd"),
    )


def _usage_from_run(qa_run: Any) -> dict[str, Any]:
    executions = list(getattr(qa_run, "model_executions", []) or [])
    token_usage = [dict(getattr(item, "token_usage", {}) or {}) for item in executions]
    costs = [
        float(getattr(item, "provider_cost_usd", 0) or 0)
        for item in executions
        if getattr(item, "provider_cost_usd", None) is not None
    ]
    return {
        "latency_ms": sum(int(getattr(item, "latency_ms", 0) or 0) for item in executions),
        "model_calls": len(executions),
        "total_tokens": sum(
            int(usage.get("total_tokens") or 0)
            or int(usage.get("input_tokens") or 0)
            + int(usage.get("output_tokens") or 0)
            for usage in token_usage
        ),
        "provider_cost_usd": sum(costs) if costs else None,
    }
