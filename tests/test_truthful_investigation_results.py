from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from equity_research.application.cash_conversion import (
    CashConversionRecurrenceService,
    SecCompanyFactsFinancialFactSeries,
    validate_cash_conversion_recurrence_bridge,
)
from equity_research.application.research_request_outcomes import (
    ResearchIntentAnswerResolver,
)
from equity_research.domain.financial_facts import FinancialFactSeriesQuery
from equity_research.domain.saved_research import DerivedEvidenceRecoveryAttempt
from equity_research.infrastructure.saved_research_repository import (
    SqliteSavedResearchRepository,
)
from equity_research.presentation.session_state import UiSessionState


def _series():
    rows = [
        {"start": "2023-10-01", "end": "2024-09-28", "val": 90, "fy": 2024, "fp": "FY", "form": "10-K", "filed": "2024-11-01", "accn": "a"},
        {"start": "2023-10-01", "end": "2024-06-29", "val": 60, "fy": 2024, "fp": "Q3", "form": "10-Q", "filed": "2024-08-01", "accn": "b"},
        {"start": "2024-09-29", "end": "2025-09-27", "val": 100, "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01", "accn": "c"},
        {"start": "2024-09-29", "end": "2025-06-28", "val": 70, "fy": 2025, "fp": "Q3", "form": "10-Q", "filed": "2025-08-01", "accn": "d"},
        {"start": "2025-09-28", "end": "2026-06-27", "val": 80, "fy": 2026, "fp": "Q3", "form": "10-Q", "filed": "2026-07-31", "accn": "e"},
    ]
    facts = {
        "cik": 320193,
        "facts": {"us-gaap": {
            "NetIncomeLoss": {"units": {"USD": rows}},
            "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [dict(row, val=row["val"] + 20) for row in rows]}},
            "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [dict(row, val=10) for row in rows]}},
        }},
    }
    return SecCompanyFactsFinancialFactSeries(
        facts, ticker="AAPL", security_key="cik:0000320193"
    ).query(FinancialFactSeriesQuery(
        ticker="AAPL", security_key="cik:0000320193", normalized_metrics=(),
        data_cutoff="2026-07-31",
    ))


def test_route_navigation_is_staged_until_before_the_radio() -> None:
    values = {"research_view": "Decision"}
    state = UiSessionState(values)

    state.request_route("Evidence", origin="investigation_card")

    assert values["research_view"] == "Decision"
    assert state.apply_pending_route() == "Evidence"
    assert values["research_view"] == "Evidence"


def test_public_demo_load_stages_decision_without_mutating_route_widget() -> None:
    values = {"research_view": "Audit"}
    state = UiSessionState(values)

    state.request_public_demo_landing()

    assert values["research_view"] == "Audit"
    assert state.apply_pending_route() == "Decision"
    assert values["research_view"] == "Decision"


def test_focused_intent_never_falls_back_to_an_unlinked_request() -> None:
    result = SimpleNamespace(cash_conversion_recurrence=None, hybrid_research=None)
    request = SimpleNamespace(
        request_id="unlinked", objective="Test cash conversion", status="completed",
        admitted_evidence_ids=("e1",),
    )

    state = ResearchIntentAnswerResolver().resolve_state(
        result, [request], intent_id="issuer:cash:recurrence", question="Cash conversion"
    )

    assert state.answer_status == "not_researched"
    assert "exact investigation intent" in state.direct_answer


def test_cash_bridge_rejects_single_period_and_never_presents_it_as_ttm_comparison() -> None:
    bridge = CashConversionRecurrenceService().build(_series(), comparison_periods=2)
    one_period = replace(bridge, periods=bridge.periods[:1])

    validation = validate_cash_conversion_recurrence_bridge(one_period)

    assert not validation.valid
    assert "Two comparable TTM periods are required." in validation.reasons


def test_recovery_attempt_is_durable_and_terminal_outcomes_are_idempotent(tmp_path) -> None:
    repository = SqliteSavedResearchRepository(tmp_path / "saved.db")
    first = DerivedEvidenceRecoveryAttempt(
        recovery_key="recovery-1", scope_id="local", parent_artifact_id="parent",
        intent_id="cash", data_cutoff="2026-07-31", analysis_version="v1",
        status="blocked", attempted_at="2026-08-15T00:00:00+00:00",
        readable_reason="Historical series unavailable.",
    )
    later = replace(first, status="running", attempted_at="2026-08-15T01:00:00+00:00")

    repository.save_recovery_attempt(first)
    saved = repository.save_recovery_attempt(later)

    assert saved.status == "blocked"
    assert repository.get_recovery_attempt("recovery-1", scope_id="local") == first
