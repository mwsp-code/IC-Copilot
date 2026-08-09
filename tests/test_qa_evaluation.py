from __future__ import annotations

from equity_research.qa_evaluation import (
    QaEvaluationObservation,
    QaEvaluationVariant,
    evaluate_quality_first_variants,
)


def _observation(variant, **overrides):
    values = {
        "variant": variant.value,
        "ignored_material_findings_seeded": 2,
        "ignored_material_findings_detected": 0,
    }
    values.update(overrides)
    return QaEvaluationObservation(**values)


def test_full_workflow_gate_requires_quality_uplift_integrity_and_closure_detection() -> None:
    gate = evaluate_quality_first_variants([
        _observation(QaEvaluationVariant.PRIMARY_ONLY),
        _observation(
            QaEvaluationVariant.FROZEN_AUDIT,
            material_contradictions_discovered=1,
        ),
        _observation(
            QaEvaluationVariant.AUDIT_AND_SCOUT,
            material_contradictions_discovered=2,
            newly_admitted_independent_evidence=0,
        ),
        _observation(
            QaEvaluationVariant.FULL_CLOSURE,
            unsupported_claims_removed=2,
            material_contradictions_discovered=2,
            evidence_gaps_closed=1,
            newly_admitted_independent_evidence=2,
            ignored_material_findings_detected=2,
        ),
    ])

    assert gate.status == "passed"
    assert len(gate.improved_decision_quality_measures) >= 2
    assert gate.integrity_regression is False
    assert gate.seeded_closure_detection_passed is True


def test_gate_blocks_integrity_regression_or_missed_seeded_finding() -> None:
    gate = evaluate_quality_first_variants([
        _observation(QaEvaluationVariant.PRIMARY_ONLY),
        _observation(QaEvaluationVariant.FROZEN_AUDIT),
        _observation(QaEvaluationVariant.AUDIT_AND_SCOUT),
        _observation(
            QaEvaluationVariant.FULL_CLOSURE,
            unsupported_claims_removed=1,
            evidence_gaps_closed=1,
            integrity_violations=1,
            ignored_material_findings_detected=1,
        ),
    ])

    assert gate.status == "blocked"
    assert any("integrity" in blocker.casefold() for blocker in gate.blockers)
    assert any("seeded" in blocker.casefold() for blocker in gate.blockers)
