from __future__ import annotations

from equity_research.company_economics import (
    attach_economic_context,
    build_company_economics,
)
from equity_research.domain.mapping_escalation import resolve_observation_mapping
from equity_research.idea_engine import build_driver_analysis
from equity_research.models import ChangeEvent, CompanyIdentity
from equity_research.peers import peer_universe_for


def test_aapl_risk_factor_language_is_a_disclosure_lead_not_an_unmapped_metric() -> None:
    identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.")
    event = ChangeEvent(
        category="risk_factors",
        title="Risk Factors language changed",
        summary="The current Item 1A wording differs from the prior filing.",
        severity=4,
        direction="uncertain",
        event_date="2026-07-31",
        source="SEC filing comparison",
        metrics={},
    )
    economics = build_company_economics(
        identity,
        [],
        [event],
        peer_universe_for("AAPL"),
    )

    attach_economic_context([event], economics)
    analysis = build_driver_analysis(event, [])

    assert event.metrics["economic_driver"] == "Disclosure risk / research lead"
    assert event.metrics["driver_family"] == "disclosure"
    assert event.metrics.get("metric_mapping_error") is not True
    assert analysis.primary_driver == "Disclosure risk / research lead"
    assert "Unmapped metric" not in analysis.primary_driver


def test_unknown_numeric_observation_reserves_the_mapping_error_label() -> None:
    result = resolve_observation_mapping(
        ticker="AAPL",
        metric_name="IssuerCustomKpi42",
        category="financial_kpi",
        title="IssuerCustomKpi42 changed",
        metrics={"value": 42.0, "unit": "units", "period_end": "2026-06-30"},
    )

    assert result.observation_kind == "numeric_observation"
    assert result.status == "unresolved"
    assert result.label == "Numeric metric mapping unresolved"
    assert result.mapping_regression is False
    assert "do not ask the user" in result.unresolved_reason


def test_well_covered_security_becomes_mapping_regression_only_after_exhaustion() -> None:
    pending = resolve_observation_mapping(
        ticker="AAPL",
        metric_name="IssuerCustomKpi42",
        category="financial_kpi",
        title="Custom KPI",
        metrics={"value": 42.0},
        automation_exhausted=False,
    )
    exhausted = resolve_observation_mapping(
        ticker="AAPL",
        metric_name="IssuerCustomKpi42",
        category="financial_kpi",
        title="Custom KPI",
        metrics={"value": 42.0},
        automation_exhausted=True,
    )

    assert pending.mapping_regression is False
    assert exhausted.mapping_regression is True
    assert [step.step for step in exhausted.steps] == [
        "taxonomy_and_issuer_aliases",
        "filing_table_segment_period_unit_context",
        "primary_model_mapping",
        "frozen_auditor_challenge",
        "adversarial_mapping_discovery",
        "registered_retrieval_and_specialist_review",
    ]


def test_model_mapping_requires_admitted_evidence() -> None:
    unsupported = resolve_observation_mapping(
        ticker="AAPL",
        metric_name="CustomMetric",
        category="financial_kpi",
        title="Custom metric",
        metrics={"value": 1.0},
        primary_model_candidate={"proposed_mapping": "Revenue"},
    )
    supported = resolve_observation_mapping(
        ticker="AAPL",
        metric_name="CustomMetric",
        category="financial_kpi",
        title="Custom metric",
        metrics={"value": 1.0},
        primary_model_candidate={
            "proposed_mapping": "Revenue",
            "admitted_evidence_ids": ["admitted-evidence-1"],
        },
    )

    assert unsupported.status == "unresolved"
    assert supported.status == "resolved"
    assert supported.resolved_mapping == "Revenue"
    assert supported.resolution_method == "primary_model_mapping"
