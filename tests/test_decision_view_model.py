from __future__ import annotations

import re
from types import SimpleNamespace

from equity_research.models import ModelExecutionRecord
from equity_research.presentation.view_models import build_decision_brief_view_model
from equity_research.presentation.view_models import build_integrity_timeline_view_model
from equity_research.sample_data import demo_result


def test_aapl_decision_view_is_readable_and_hides_raw_timing_hashes() -> None:
    result = demo_result("AAPL")
    model = build_decision_brief_view_model(result)

    assert model.ticker == "AAPL"
    assert not re.search(r"\b[a-f0-9]{10,64}\b", model.why_now, re.I)
    assert len(model.next_actions) <= 3
    assert model.valuation_range
    assert model.probability_weighted_value
    assert model.expected_return
    assert model.counter_thesis
    assert model.market_expectations is not None
    assert model.market_expectations.assumptions
    assert model.historical_analogs
    assert {item.title for item in model.story_lenses} >= {
        "Equity Lens",
        "Credit Lens",
    }
    assert model.debate is not None


def test_weak_no_counter_evidence_copy_becomes_an_explicit_diligence_gap() -> None:
    result = demo_result("AAPL")
    result.ic_decision_brief.principal_risk = (
        "No material counter-evidence identified in the current run."
    )

    model = build_decision_brief_view_model(result)

    assert model.counter_thesis_status == "Diligence gap"
    assert "Counter-thesis not established" in model.counter_thesis


def test_model_execution_timeline_uses_execution_message_not_risk_fields() -> None:
    result = demo_result("AAPL")
    result.independent_research_qa = SimpleNamespace(
        status="partial",
        model_executions=[
            ModelExecutionRecord(
                role="independent_research_auditor",
                provider="Local Codex",
                model="codex",
                status="failed",
                message="Auditor provider was unavailable; QA did not complete.",
                failure_class="provider_unavailable",
            )
        ],
        research_requests=[],
        candidate_discovery_items=[],
        closure_result=None,
        stopping_reason="provider_failure",
        recursion_depth=0,
        estimated_cost_usd=None,
    )

    model = build_integrity_timeline_view_model(result)

    audit_event = next(
        item for item in model.events
        if item.label == "Independent frozen-artifact audit"
    )
    assert audit_event.status == "Failed"
    assert "did not complete" in audit_event.summary
    assert audit_event.technical_details["failure_class"] == "provider_unavailable"


def test_missing_optional_qa_is_explicit_and_never_crashes_audit_view_model() -> None:
    result = demo_result("AAPL")
    result.independent_research_qa = None

    model = build_integrity_timeline_view_model(result)

    assert model.status == "Independent QA not run"
    assert any(
        item.label == "Independent Research QA" and item.status == "Not run"
        for item in model.events
    )
