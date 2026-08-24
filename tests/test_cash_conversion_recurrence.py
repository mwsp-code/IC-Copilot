from __future__ import annotations

from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from equity_research.application.cash_conversion import (
    CashConversionRecurrenceService,
    DeterministicCashConversionRefreshService,
    DeterministicEvidenceRefreshCommand,
    SecCompanyFactsFinancialFactSeries,
    normalize_financial_fact_metric,
)
from equity_research.application.research_tools import ContextResearchToolGateway
from equity_research.application.research_request_outcomes import (
    ResearchIntentAnswerResolver,
)
from equity_research.application.source_capabilities import SourceCapabilityRegistry
from equity_research.auditable_retrieval import AuditableEvidenceRetriever
from equity_research.domain.financial_facts import FinancialFactSeriesQuery
from equity_research.domain.agent_research import ResearchToolCall
from equity_research.models import CompanyIdentity
from equity_research.hybrid_research import (
    _apply_deterministic_cash_conversion_answer,
    reconcile_cash_conversion_qa_freshness,
)
from equity_research.application.research_agenda import ResearchAgendaBuilder
from equity_research.domain.agent_research import (
    AgentResearchBudget,
    AgentResearchSession,
    ResearchAnswer,
)
from equity_research.presentation.components import _render_plain_text
from equity_research.presentation.streamlit_app import (
    _has_current_cash_conversion_bridge,
    _is_focused_cash_conversion_result,
)


_CONCEPTS = {
    "NetIncomeLoss": (90.0, 60.0, 100.0, 70.0, 80.0),
    "NetCashProvidedByUsedInOperatingActivities": (110.0, 70.0, 120.0, 80.0, 100.0),
    "PaymentsToAcquirePropertyPlantAndEquipment": (9.0, 5.0, 10.0, 6.0, 8.0),
    "IncreaseDecreaseInAccountsReceivable": (2.0, 1.0, 5.0, 3.0, 4.0),
    "IncomeTaxesPaidNet": (18.0, 12.0, 20.0, 14.0, 15.0),
    "ProceedsFromIssuanceOfLongTermDebt": (4.0, 2.0, 3.0, 1.0, 2.0),
    "RepaymentsOfLongTermDebt": (5.0, 2.0, 6.0, 3.0, 4.0),
    "PaymentsOfDividends": (14.0, 9.0, 15.0, 10.0, 11.0),
    "PaymentsForRepurchaseOfCommonStock": (70.0, 45.0, 75.0, 50.0, 55.0),
}


def _row(start: str, end: str, value: float, filed: str, fy: int, fp: str, form: str):
    return {
        "start": start,
        "end": end,
        "val": value,
        "accn": f"{fy}-{fp}-{end}",
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }


def company_facts(*, omit_cash_taxes: bool = False):
    facts = {}
    for concept, values in _CONCEPTS.items():
        if omit_cash_taxes and concept == "IncomeTaxesPaidNet":
            continue
        annual_2024, ytd_2024, annual_2025, ytd_2025, ytd_2026 = values
        rows = [
            _row("2023-10-01", "2024-09-28", annual_2024, "2024-11-01", 2024, "FY", "10-K"),
            _row("2023-10-01", "2024-06-29", ytd_2024, "2024-08-02", 2024, "Q3", "10-Q"),
            _row("2024-09-29", "2025-09-27", annual_2025, "2025-10-31", 2025, "FY", "10-K"),
            _row("2024-09-29", "2025-06-28", ytd_2025, "2025-08-01", 2025, "Q3", "10-Q"),
            _row("2025-09-28", "2026-06-27", ytd_2026, "2026-07-31", 2026, "Q3", "10-Q"),
            # Must not affect a run whose cutoff precedes this filing.
            _row("2025-09-28", "2026-06-27", 9999.0, "2027-01-01", 2027, "Q3", "10-Q"),
        ]
        if concept == "NetIncomeLoss":
            # Exact shape of the AAPL regression: the quarter-only and nine-month
            # facts share an end date. The comparable-TTM bridge must select the
            # duration-aligned YTD fact rather than the $29.789B quarter.
            rows.append(
                _row(
                    "2026-03-29",
                    "2026-06-27",
                    29.789,
                    "2026-07-31",
                    2026,
                    "Q3",
                    "10-Q",
                )
            )
        facts[concept] = {"units": {"USD": rows}}
    return {"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": facts}}


def _series(*, omit_cash_taxes: bool = False):
    provider = SecCompanyFactsFinancialFactSeries(
        company_facts(omit_cash_taxes=omit_cash_taxes),
        ticker="AAPL",
        security_key="cik:0000320193",
    )
    return provider.query(FinancialFactSeriesQuery(
        ticker="AAPL",
        security_key="cik:0000320193",
        normalized_metrics=(),
        data_cutoff="2026-07-31",
    ))


def test_two_comparable_ttm_bridges_use_annual_plus_ytd_minus_prior_ytd():
    bridge = CashConversionRecurrenceService().build(_series(), comparison_periods=2)

    assert bridge.status == "answered"
    assert len(bridge.periods) == 2
    current, prior = bridge.periods
    assert current.net_income == pytest.approx(110.0)
    assert current.operating_cash_flow == pytest.approx(140.0)
    assert current.capital_expenditure == pytest.approx(12.0)
    assert current.free_cash_flow == pytest.approx(128.0)
    assert current.working_capital_change == pytest.approx(-6.0)
    assert prior.net_income == pytest.approx(100.0)
    assert prior.operating_cash_flow == pytest.approx(120.0)
    assert prior.free_cash_flow == pytest.approx(110.0)
    assert prior.working_capital_change == pytest.approx(-4.0)
    assert "annual ending 2025-09-27" in current.formula
    assert bridge.assessment.confidence == "High"


@pytest.mark.parametrize(("supplied", "expected"), (
    ("Net Income", "net_income"),
    ("NetIncomeLoss", "net_income"),
    ("Operating Cash Flow", "operating_cash_flow"),
    ("Payments for Repurchase of Common Stock", "repurchases"),
    ("RepaymentsOfLongTermDebt", "debt_repaid_long_term"),
))
def test_financial_fact_metric_aliases_accept_model_friendly_names(
    supplied: str,
    expected: str,
) -> None:
    assert normalize_financial_fact_metric(supplied) == expected


def test_fact_series_tool_returns_bounded_comparable_rows_for_readable_aliases():
    identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.", "Nasdaq")
    context = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "security_key": "cik:0000320193",
        "data_cutoff": "2026-07-31",
        "normalized_sources": [],
        "searchable_evidence_catalog": [],
        "claim_eligible_evidence_ids": [],
    }
    gateway = ContextResearchToolGateway(
        context,
        SourceCapabilityRegistry(),
        retrieval_engine=AuditableEvidenceRetriever(
            identity,
            company_facts=company_facts(),
        ),
    )

    observation = gateway.execute(ResearchToolCall(
        call_id="readable-series-aliases",
        tool_name="query_sec_fact_series",
        question_id="cash",
        arguments={
            "metrics": ["Net Income", "Operating Cash Flow"],
            "comparison_periods": 2,
            "data_cutoff": "2026-07-31",
        },
    ))

    assert observation.status == "completed"
    assert len(observation.content) <= 20
    rows = observation.content[1:]
    assert any(
        item["normalized_metric"] == "net_income"
        and item["period_start"] == "2025-09-28"
        and item["period_end"] == "2026-06-27"
        for item in rows
    )
    assert observation.content[0]["series_summary"]["returned_observations"] == len(rows)


def test_recurrence_objective_routes_legacy_cash_bridge_to_comparable_ttm():
    identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.", "Nasdaq")
    context = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "security_key": "cik:0000320193",
        "data_cutoff": "2026-07-31",
        "normalized_sources": [],
        "searchable_evidence_catalog": [],
        "claim_eligible_evidence_ids": [],
    }
    observation = ContextResearchToolGateway(
        context,
        SourceCapabilityRegistry(),
        retrieval_engine=AuditableEvidenceRetriever(
            identity,
            company_facts=company_facts(),
        ),
    ).execute(ResearchToolCall(
        call_id="recurrence-routing",
        tool_name="compute_financial_bridge",
        question_id="cash",
        objective="Test whether cash conversion remains recurring.",
        arguments={
            "bridge_kind": "cash_conversion",
            "comparison_periods": 2,
            "period_basis": "comparable_ttm",
        },
    ))

    assert observation.status == "completed"
    assert observation.content[0]["status"] == "answered"
    assert len(observation.content[0]["periods"]) == 2


def test_validated_bridge_replaces_weaker_model_cash_answer():
    agenda = ResearchAgendaBuilder().build(
        ticker="AAPL",
        company_name="Apple Inc.",
        research_profile="deep_initiation",
        selected_objective="Test whether AAPL cash conversion remains recurring.",
    )
    cash = next(
        item for item in agenda.items
        if item.question_key == "cash_generation_capital_allocation"
    )
    cash.status = "partially_answered"
    cash.answer = ResearchAnswer(
        question_id=cash.question_id,
        status="partially_answered",
        answer="Nine-month net income remains unavailable.",
    )
    session = AgentResearchSession(
        session_id="cash-session",
        ticker="AAPL",
        role="primary_research_agent",
        provider="deepseek",
        model="deepseek-v4-pro",
        status="completed",
        agenda=agenda,
        budget=AgentResearchBudget("focused", "Focused", 60, 5, 2),
        started_at="2026-08-14T00:00:00+00:00",
        answers=[cash.answer],
    )
    bridge = CashConversionRecurrenceService().build(
        _series(), comparison_periods=2, intent_id=cash.intent_id
    )

    _apply_deterministic_cash_conversion_answer(session, bridge)

    assert cash.status == "answered"
    assert cash.answer.answer == bridge.assessment.direct_answer
    assert cash.answer.confidence == "High"
    assert session.answers[0].evidence_hash == bridge.evidence_hash


def test_post_cutoff_fact_is_excluded_and_missing_component_is_not_zero():
    series = _series(omit_cash_taxes=True)
    assert all(item.filed <= "2026-07-31" for item in series.observations)

    bridge = CashConversionRecurrenceService().build(series, comparison_periods=2)
    assert bridge.periods[0].net_income == pytest.approx(110.0)
    assert bridge.periods[0].cash_taxes is None
    assert "cash taxes" in bridge.assessment.material_gaps
    assert bridge.assessment.confidence == "Medium"


def test_answer_resolver_prefers_current_bridge_over_earlier_partial_request():
    bridge = CashConversionRecurrenceService().build(_series(), comparison_periods=2)
    result = SimpleNamespace(cash_conversion_recurrence=bridge, hybrid_research=None)
    requests = [
        SimpleNamespace(
            request_id="legacy-partial",
            objective="Test whether AAPL cash conversion remains recurring",
            attempted_connector_ids=("sec",),
            admitted_evidence_ids=(),
            status="attempted",
        ),
        SimpleNamespace(
            request_id="newer-complete",
            objective="Align two comparable TTM cash conversion periods",
            attempted_connector_ids=("sec_companyfacts",),
            admitted_evidence_ids=("evidence-1",),
            status="resolved",
        ),
    ]

    answer = ResearchIntentAnswerResolver().resolve(
        result,
        requests,
        question="Test whether AAPL cash conversion remains recurring after working capital, capex, taxes, financing, and capital returns",
    )

    assert answer.status == "answered"
    assert answer.bridge is bridge
    assert answer.direct_answer == bridge.assessment.direct_answer
    assert set(answer.request_ids) == {"legacy-partial", "newer-complete"}
    assert asdict(answer.bridge)["periods"][0]["net_income"] == pytest.approx(110.0)


def test_deterministic_saved_refresh_uses_gateway_without_model_provider():
    identity = CompanyIdentity("AAPL", "0000320193", "Apple Inc.", "Nasdaq")
    retriever = AuditableEvidenceRetriever(
        identity,
        company_facts=company_facts(),
    )
    context = {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "security_key": "cik:0000320193",
        "data_cutoff": "2026-07-31",
        "normalized_sources": [],
        "searchable_evidence_catalog": [],
        "claim_eligible_evidence_ids": [],
    }
    gateway = ContextResearchToolGateway(
        context,
        SourceCapabilityRegistry(),
        retrieval_engine=retriever,
    )
    original = SimpleNamespace(
        identity=identity,
        metrics=[],
        filings=[],
        normalized_sources=[],
        cash_conversion_recurrence=None,
        hybrid_research=None,
        independent_research_qa=None,
        evidence_graph=None,
    )

    refreshed = DeterministicCashConversionRefreshService().refresh(
        DeterministicEvidenceRefreshCommand(
            parent_artifact_id="saved-legacy",
            ticker="AAPL",
            intent_id="cash-intent",
            data_cutoff="2026-07-31",
        ),
        result=original,
        gateway=gateway,
    )

    assert refreshed.status == "refreshed"
    assert refreshed.result is not original
    assert original.cash_conversion_recurrence is None
    assert refreshed.result.cash_conversion_recurrence.periods[0].net_income == pytest.approx(110.0)
    assert 0 < len(context["claim_eligible_evidence_ids"]) <= len(_series().observations)


def test_plain_text_renderer_preserves_currency_without_markdown_math():
    calls = []
    fake_streamlit = SimpleNamespace(
        markdown=lambda value, **kwargs: calls.append((value, kwargs))
    )

    _render_plain_text(fake_streamlit, "$146.7B, or 113.8% of $128.9B")

    assert calls == [
        ("<p>$146.7B, or 113.8% of $128.9B</p>", {"unsafe_allow_html": True})
    ]


def test_completed_closure_marks_the_pre_audit_cash_bridge_reviewed() -> None:
    bridge = CashConversionRecurrenceService().build(
        _series(),
        comparison_periods=2,
    )
    run = SimpleNamespace(
        status="completed",
        cash_conversion_recurrence=bridge,
        generation_provenance={
            "cash_conversion_closure_evidence_hash": bridge.evidence_hash,
        },
        final_artifact=SimpleNamespace(
            payload={"closure_state": "closed_with_residual_risk"}
        ),
        rounds=[],
    )

    reconcile_cash_conversion_qa_freshness(run)

    assert run.cash_conversion_recurrence.qa_freshness == (
        "closed_with_residual_risk"
    )


def test_unscoped_closure_cannot_make_a_refreshed_bridge_qa_reviewed() -> None:
    bridge = CashConversionRecurrenceService().build(
        _series(),
        comparison_periods=2,
    )
    bridge = replace(
        bridge,
        qa_freshness="closed_with_residual_risk",
    )
    run = SimpleNamespace(
        status="completed",
        cash_conversion_recurrence=bridge,
        generation_provenance={},
        final_artifact=SimpleNamespace(
            payload={"closure_state": "closed_with_residual_risk"}
        ),
        rounds=[],
    )

    reconcile_cash_conversion_qa_freshness(run)

    assert run.cash_conversion_recurrence.qa_freshness == "not_reviewed"


def test_legacy_focused_cash_repair_refreshes_when_capability_metadata_is_stale() -> None:
    """Metadata must not mask a missing comparable-period bridge on load."""

    result = SimpleNamespace(
        cash_conversion_recurrence=None,
        hybrid_research=SimpleNamespace(
            objective="Independent QA for refreshed cash-conversion answer",
            research_intent_id="cash_generation_capital_allocation:recurring_cash_conversion",
            cash_conversion_recurrence=None,
        ),
    )
    artifact = SimpleNamespace(
        operation_kind="repair_recheck",
        operation_label="Independent QA for refreshed cash-conversion answer",
        # This is the historical defect: the writer advertised the capability
        # while the stored payload itself had no recurrence bridge.
        analysis_capabilities=("cash-conversion-recurrence-v1",),
    )

    assert not _has_current_cash_conversion_bridge(result)
    assert _is_focused_cash_conversion_result(
        artifact,
        result,
        {"research_intent_id": "cash_generation_capital_allocation:recurring_cash_conversion"},
    )


def test_complete_hybrid_cash_bridge_does_not_request_a_legacy_refresh() -> None:
    bridge = CashConversionRecurrenceService().build(_series(), comparison_periods=2)
    result = SimpleNamespace(
        cash_conversion_recurrence=None,
        hybrid_research=SimpleNamespace(cash_conversion_recurrence=bridge),
    )

    assert _has_current_cash_conversion_bridge(result)
