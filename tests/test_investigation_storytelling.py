from __future__ import annotations

from copy import deepcopy

from equity_research.application.research_request_outcomes import (
    build_research_request_outcome,
)
from equity_research.application.investigation_journey import (
    InvestigationStage,
    build_investigation_journey,
)
from equity_research.auditable_retrieval import build_historical_financial_paths
from equity_research.domain.research_integrity import (
    CandidateDiscoveryItem,
    CandidateStatus,
    CandidateStatusEvent,
    CandidateType,
    ResearchRequest,
)
from equity_research.domain.financial_facts import (
    CashConversionAssessment,
    CashConversionPeriodBridge,
    CashConversionRecurrenceBridge,
)
from equity_research.domain.saved_research import DisplayedResearchContext
from equity_research.models import FinancialMetric
from equity_research.presentation.view_models import (
    build_evidence_page_view_model,
    build_investigation_journey_view_model,
)
from equity_research.sample_data import demo_result


def test_completed_investigation_story_reports_deterministic_changes() -> None:
    parent = demo_result("AAPL")
    updated = deepcopy(parent)
    updated.evidence_graph.admitted_evidence_ids.append("new-admitted-evidence")
    updated.prioritized_research_queue[0].status = "resolved"
    updated.prioritized_research_queue[0].resolution_summary = (
        "Cash conversion was tested against admitted filing evidence."
    )

    journey = build_investigation_journey(
        updated,
        operation_kind="action_investigation",
        operation_label="Test whether AAPL cash conversion remains recurring",
        objective="Test recurring cash conversion after working capital and capex.",
        outcome="resolved",
        persistence_status="verified_saved_result",
        parent_result=parent,
    )
    view_model = build_investigation_journey_view_model(journey)

    assert journey.current_stage is InvestigationStage.RESULT_READY
    assert view_model.outcome == "Answered · Qa Pending"
    assert view_model.persistence_status == "Verified Saved Result"
    assert view_model.admitted_evidence_count >= 1
    assert any("newly admitted" in item for item in view_model.change_summaries)
    assert any("action changed status" in item for item in view_model.change_summaries)


def test_active_journey_uses_controlled_stage_sequence() -> None:
    result = demo_result("AAPL")
    journey = build_investigation_journey(
        result,
        operation_kind="action_investigation",
        operation_label="Test AAPL cash conversion",
        active=True,
        active_stage="Independent auditor reviewing the frozen artifact",
        elapsed_seconds=125,
    )
    view_model = build_investigation_journey_view_model(journey)

    assert journey.current_stage is InvestigationStage.INDEPENDENT_QA
    assert view_model.active
    assert view_model.elapsed_label == "2m 05s"
    assert view_model.qa_label == "Independent QA is active"
    assert len(view_model.stages) == 7
    assert view_model.progress_percent > 50
    assert view_model.progress_label.startswith("Stage 5 of 7")


def test_agent_planning_and_research_activity_advances_past_selection() -> None:
    result = demo_result("AAPL")
    journey = build_investigation_journey(
        result,
        operation_kind="action_investigation",
        operation_label="Test AAPL cash conversion",
        active=True,
        active_stage="DeepSeek research planner is building the research plan",
    )
    model = build_investigation_journey_view_model(journey)

    assert journey.current_stage is InvestigationStage.PRIMARY_ANALYSIS
    assert [status for _, status in journey.stages[:2]] == [
        "completed",
        "completed",
    ]
    assert model.progress_percent >= 35


def test_completed_cash_journey_prefers_canonical_bridge_over_partial_agent_answer() -> None:
    result = demo_result("AAPL")
    journey = build_investigation_journey(
        result,
        operation_kind="action_investigation",
        operation_label=(
            "Test whether AAPL cash conversion remains recurring after working "
            "capital, capex, taxes, financing, and capital returns"
        ),
        outcome="completed",
        persistence_status="frozen_demo",
    )
    model = build_investigation_journey_view_model(journey)

    assert model.outcome == "Answered · Qa Pending"
    assert model.answer_status == "answered"
    assert model.direct_answer.startswith("Cash conversion appears recurring")
    assert model.confidence == "High"
    assert model.remaining_actions == (
        "Independent QA has not yet closed the refreshed answer.",
    )
    assert model.persistence_status == "Frozen Demo"


def test_evidence_trail_uses_explicit_saved_context_to_resolve_cash_answer() -> None:
    result = demo_result("AAPL")
    action = next(
        item for item in result.prioritized_research_queue
        if item.action_kind == "cash_conversion_analysis"
    )

    model = build_evidence_page_view_model(
        result,
        displayed_context=DisplayedResearchContext(
            operation_kind="action_investigation",
            operation_label="Independent QA for refreshed cash-conversion answer",
            operation_action_id=action.action_id,
            research_intent_id=(
                "cash_generation_capital_allocation:recurring_cash_conversion"
            ),
            outcome="completed",
        ),
    )

    assert model.investigation_trail is not None
    assert model.investigation_trail.answer_status == "answered"
    assert model.investigation_trail.confidence == "High"
    assert len(model.investigation_trail.period_comparison) == 2
    assert model.investigation_trail.qa_freshness == "not_reviewed"


def test_evidence_story_hides_scope_mismatch_and_groups_requests() -> None:
    result = demo_result("AAPL")
    qa = result.independent_research_qa
    action = result.prioritized_research_queue[0]
    mismatched = CandidateDiscoveryItem(
        candidate_id="scope-mismatch-candidate",
        candidate_type=CandidateType.SOURCE.value,
        status=CandidateStatus.REJECTED.value,
        ticker="AAPL",
        objective="Unrelated company news",
        origin_role="adversarial_evidence_scout",
        title="Charles River Laboratories insider sale",
        research_request_id=(
            action.related_research_request_ids[0]
            if action.related_research_request_ids else ""
        ),
        scope_status="scope_mismatch",
        status_history=(CandidateStatusEvent(
            status=CandidateStatus.REJECTED.value,
            changed_at="2026-08-11T00:00:00+00:00",
            reason="Candidate is outside the security and Research Request scope.",
        ),),
    )
    qa.candidate_discovery_items.append(mismatched)

    model = build_evidence_page_view_model(
        result,
        operation_kind="action_investigation",
        operation_label=action.objective or action.title,
        operation_action_id=action.action_id,
        outcome="completed",
    )

    assert model.investigation_trail is not None
    assert model.request_groups
    assert model.hidden_candidate_count >= 1
    assert all("Charles River" not in item.title for item in model.candidates)
    assert len(model.investigation_trail.steps) == 6


def test_evidence_story_repairs_common_mojibake_for_display() -> None:
    result = demo_result("AAPL")
    result.independent_research_qa.candidate_discovery_items.append(
        CandidateDiscoveryItem(
            candidate_id="mojibake-candidate",
            candidate_type=CandidateType.SOURCE.value,
            status=CandidateStatus.QUARANTINED.value,
            ticker="AAPL",
            objective="Readable punctuation",
            origin_role="adversarial_evidence_scout",
            title="Apple â€” cash conversion â€™ update",
        )
    )

    model = build_evidence_page_view_model(result)
    title = next(
        item.title for item in model.candidates
        if item.candidate_id == "mojibake-candidate"
    )

    assert title == "Apple — cash conversion ’ update"


def test_legacy_unlinked_rejected_candidate_is_hidden_from_routine_view() -> None:
    result = demo_result("AAPL")
    result.independent_research_qa.candidate_discovery_items.append(
        CandidateDiscoveryItem(
            candidate_id="legacy-unrelated-candidate",
            candidate_type=CandidateType.SOURCE.value,
            status=CandidateStatus.REJECTED.value,
            ticker="AAPL",
            objective="Legacy unrelated lead",
            origin_role="adversarial_evidence_scout",
            title="Charles River Labs insider sale",
        )
    )

    model = build_evidence_page_view_model(result)

    assert all(
        item.candidate_id != "legacy-unrelated-candidate"
        for item in model.candidates
    )
    assert model.hidden_candidate_count >= 1


def test_cash_conversion_attempt_shows_partial_answer_and_missing_bridge() -> None:
    result = demo_result("AAPL")
    result.cash_conversion_recurrence = None
    result.hybrid_research.cash_conversion_recurrence = None
    result.metrics = [
        FinancialMetric(
            "Operating Cash Flow", 117.0, "USD", "2026-06-27",
            trailing_twelve_month_value=146.724e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Net Income", 98.0, "USD", "2026-06-27",
            trailing_twelve_month_value=128.930e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Capital Expenditure", 6.8, "USD", "2026-06-27",
            trailing_twelve_month_value=10.041e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Dividends Paid", 11.8, "USD", "2026-06-27",
            trailing_twelve_month_value=15.640e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
        FinancialMetric(
            "Repurchases", 62.1, "USD", "2026-06-27",
            trailing_twelve_month_value=82.226e9,
            trailing_period_end="2026-06-27",
            filed="2026-07-31",
        ),
    ]
    result.historical_financial_paths = build_historical_financial_paths(
        "AAPL", result.metrics
    )
    request = ResearchRequest(
        request_id="audit_cash_conversion",
        objective=(
            "Obtain the current and comparable cash-flow bridge across net income, "
            "operating cash flow, working capital, cash taxes, capex, financing, "
            "dividends, and repurchases."
        ),
        required_source_families=["sec_filing", "issuer_ir_report"],
        status="attempted",
        attempted_connector_ids=["sec_filing"],
        resolution_summary=(
            "Registered connector attempts completed without candidate evidence admission."
        ),
    )
    result.independent_research_qa.research_requests = [request]
    action = next(
        item for item in result.prioritized_research_queue
        if item.action_kind == "cash_conversion_analysis"
    )
    action.related_research_request_ids = []

    outcome = build_research_request_outcome(result, request)
    model = build_evidence_page_view_model(
        result,
        operation_kind="action_investigation",
        operation_label=(
            "Test whether AAPL cash conversion remains recurring after working "
            "capital, capex, taxes, financing, and capital returns"
        ),
        operation_action_id=action.action_id,
        outcome="completed",
    )

    assert outcome.status == "partially_answered"
    assert "$146.7B" in outcome.answer
    assert any("$136.7B" in item for item in outcome.evidence_found)
    assert any("prior same-duration" in item for item in outcome.evidence_missing)
    assert model.investigation_trail is not None
    assert model.investigation_trail.answer_status == "partially_answered"
    assert "$146.7B" in model.investigation_trail.answer
    assert "Partially answered" in {
        label for label, _items in model.request_groups
    }


def test_cash_conversion_evidence_story_prefers_two_period_answer_card() -> None:
    result = demo_result("AAPL")
    def period(end, ni, ocf, capex):
        return CashConversionPeriodBridge(
            period_start="2025-06-27",
            period_end=end,
            duration_days=365,
            net_income=ni,
            operating_cash_flow=ocf,
            operating_cash_flow_to_net_income=ocf / ni,
            working_capital_change=10.0,
            cash_taxes=20.0,
            capital_expenditure=capex,
            free_cash_flow=ocf - capex,
            free_cash_flow_to_net_income=(ocf - capex) / ni,
            debt_issued=2.0,
            debt_repaid=3.0,
            dividends=15.0,
            repurchases=80.0,
            cash_after_dividends_and_repurchases=ocf - capex - 95.0,
            evidence_ids=(f"evidence-{end}",),
        )
    result.cash_conversion_recurrence = CashConversionRecurrenceBridge(
        ticker="AAPL",
        status="answered",
        periods=(
            period("2026-06-27", 128.93e9, 146.724e9, 10.041e9),
            period("2025-06-28", 99.28e9, 108.565e9, 12.381e9),
        ),
        assessment=CashConversionAssessment(
            assessment="recurring",
            confidence="High",
            direct_answer="Cash conversion appears recurring across comparable TTM periods.",
            strongest_support="OCF/net-income conversion was 113.8% versus 109.4%.",
            strongest_limitation="No material component gap was identified.",
            comparison_periods=2,
            coverage_ratio=1.0,
        ),
        data_cutoff="2026-07-31",
        source_snapshot_hash="snapshot",
        evidence_hash="bridge-evidence",
        intent_id="cash-intent",
        qa_freshness="pending",
    )
    action = next(
        item for item in result.prioritized_research_queue
        if item.action_kind == "cash_conversion_analysis"
    )

    model = build_evidence_page_view_model(
        result,
        operation_kind="action_investigation",
        operation_label=action.objective,
        operation_action_id=action.action_id,
        outcome="completed",
    )

    trail = model.investigation_trail
    assert trail is not None
    assert trail.answer_status == "answered"
    assert trail.confidence == "High"
    assert len(trail.period_comparison) == 2
    assert trail.period_comparison[0]["Net income"] == "$128.9B"
    assert trail.qa_freshness == "pending"
    assert trail.component_sections
    assert any(
        "cash" in item.objective.casefold()
        for item in model.requests
    )
