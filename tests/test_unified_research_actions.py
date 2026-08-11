from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import json
from types import SimpleNamespace

import pytest

from equity_research.application import (
    InMemoryEphemeralCredentialBroker,
    ResearchRunCommand,
    build_provider_connection_statuses,
    parse_event_price_csv,
)
from equity_research.application.research_runs import ResearchReadiness
from equity_research.decision_intelligence import (
    build_prioritized_research_queue,
    reconcile_targeted_investigation_action,
)
from equity_research.models import (
    EvidenceWorkOrder,
    EvidenceWorkOrderItem,
    PrioritizedResearchAction,
    SourceRetrievalLifecycle,
)
from equity_research.application.research_runs import ResearchRunService
from equity_research.infrastructure.job_repository import SqliteResearchJobRepository
from equity_research.presentation.view_models import (
    ResearchSubroute,
    build_decision_brief_view_model,
    build_research_route_view_model,
)
from equity_research.sample_data import demo_result


def test_saved_profile_connection_is_ready_without_unused_environment_slot() -> None:
    readiness = ResearchReadiness(
        run_allowed=True,
        deterministic_status="Ready",
        primary_status="Ready | deepseek-v4-pro",
        independent_qa_status="Optional | Independent QA will be skipped",
        evidence_status="Ready",
        execution_status="Ready to start",
    )

    connections = build_provider_connection_statuses(
        readiness,
        primary_source="Saved DeepSeek profile",
        auditor_source="Not requested",
        independent_qa_requested=False,
        required_source_summary="SEC",
    )

    assert connections[0].status == "Ready"
    assert connections[0].source == "Saved DeepSeek profile"
    assert connections[1].status == "Optional"


def test_ephemeral_credentials_are_opaque_scoped_and_revocable() -> None:
    broker = InMemoryEphemeralCredentialBroker()
    reference = broker.issue(
        "deepseek",
        "super-secret-value",
        scope="research_run",
    )
    command = ResearchRunCommand(
        ticker="AAPL",
        credential_lease_ids=(reference.lease_id,),
    )

    assert "super-secret-value" not in json.dumps(asdict(reference))
    assert "super-secret-value" not in json.dumps(asdict(command))
    assert broker.resolve(
        reference,
        provider_id="deepseek",
        scope="research_run",
    ) == "super-secret-value"
    broker.revoke(reference.lease_id)
    with pytest.raises(Exception, match="unavailable or revoked"):
        broker.resolve(reference, provider_id="deepseek", scope="research_run")


def test_credential_expiry_preserves_run_and_reconnects_idempotently(tmp_path) -> None:
    service = ResearchRunService(
        SqliteResearchJobRepository(tmp_path / "credential-resume.sqlite3")
    )
    started = service.start(ResearchRunCommand(ticker="AAPL"))
    service.record_event(
        started.run_id,
        stage="deterministic_bootstrap",
        status="completed",
        summary="Evidence graph preserved.",
        idempotency_key="bootstrap-complete",
    )

    waiting = service.mark_credential_expired(
        started.run_id,
        stage="independent_audit",
        provider_id="local-session-auditor",
    )
    assert waiting.status == "waiting_for_credential"
    assert waiting.stopping_reason == "credential_expired"
    assert service.timeline(started.run_id)[1].status == "completed"

    resumed = service.reconnect_credentials(started.run_id, ("new-opaque-lease",))
    assert resumed.command.credential_lease_ids == ("new-opaque-lease",)
    assert resumed.status == "queued"
    assert any(
        event.status == "credential_reconnected"
        for event in service.timeline(started.run_id)
    )


def test_duplicate_cash_conversion_actions_merge_into_one_canonical_item() -> None:
    work_order = EvidenceWorkOrder(
        status="Open",
        summary="Duplicate origins",
        items=[
            EvidenceWorkOrderItem(
                "cash-work",
                "High",
                "Cash generation",
                "Analyze cash generation after working capital and capex",
                "cash_flow_statement",
                "Recurring cash conversion bridge",
                "Tests durability",
                "Evidence work order",
                blocks_research_ready=True,
            ),
            EvidenceWorkOrderItem(
                "cash-judge",
                "Medium",
                "Liquidity",
                "Test cash conversion after financing, taxes and capital returns",
                "cash_flow_statement",
                "Recurring cash conversion bridge",
                "Resolves the Judge finding",
                "Bull Bear Judge",
            ),
        ],
    )

    queue = build_prioritized_research_queue(work_order, ticker="AAPL")

    assert len(queue) == 1
    assert queue[0].action_kind == "cash_conversion_analysis"
    assert queue[0].related_work_order_ids == ["cash-work", "cash-judge"]
    assert "AAPL" in queue[0].objective


def test_missing_valuation_input_becomes_a_targeted_automatic_action() -> None:
    work_order = EvidenceWorkOrder(
        status="Closed",
        summary="No other open work",
        items=[],
    )
    valuation = SimpleNamespace(input_resolutions=[SimpleNamespace(
        input_name="Forward EBITDA",
        status="missing",
        reason=(
            "No normalized EBITDA fact or aligned Operating Income plus "
            "Depreciation and Amortization pair is available."
        ),
    )])

    queue = build_prioritized_research_queue(
        work_order,
        ticker="AAPL",
        valuation=valuation,
    )

    assert len(queue) == 1
    assert queue[0].action_kind == "valuation_input_resolution"
    assert queue[0].auto_resolvable is True
    assert "Forward EBITDA" in queue[0].title
    assert queue[0].preferred_connectors == [
        "sec_companyfacts",
        "issuer_filings",
        "configured_consensus",
    ]


def test_decision_uses_canonical_actions_and_research_has_lazy_route_models() -> None:
    result = demo_result("AAPL")
    decision = build_decision_brief_view_model(result)

    assert 0 < len(decision.investigation_actions) <= 3
    assert decision.market_expectations is not None
    assert decision.market_expectations.price_label == "Price at research cutoff"
    assert decision.market_expectations.current_price == "$313.33"
    assert decision.market_expectations.price_as_of == "2026-08-07"
    assert all(
        action.intent_type in {
            "configure_source",
            "execute_action",
            "retry_attribution",
        }
        for action in decision.investigation_actions
    )
    cash_action = next(
        action for action in decision.investigation_actions
        if action.action_kind == "cash_conversion_analysis"
    )
    assert cash_action.intent_type == "execute_action"
    assert cash_action.primary_cta == "Run investigation"
    assert cash_action.setup_steps == ()
    assert decision.debate is not None
    assert decision.debate.resolution_actions == ()
    for route in ResearchSubroute:
        model = build_research_route_view_model(result, route)
        assert model.route is route
        assert len(model.metrics) <= 4
        assert len(model.what_matters) <= 5


def test_manual_action_keeps_the_two_step_source_setup_handoff() -> None:
    result = demo_result("AAPL")
    result.prioritized_research_queue = [PrioritizedResearchAction(
        rank=1,
        action_id="manual-consensus",
        title="Capture licensed consensus history",
        why_it_matters="Point-in-time estimates require an entitled source.",
        source_type="licensed consensus",
        status="licensed_or_manual_required",
        decision_impact="Expectations remain incomplete.",
        auto_resolvable=False,
        action_kind="consensus_revision_capture",
        objective="Capture licensed consensus history",
        execution_mode="manual",
    )]

    action = build_decision_brief_view_model(result).investigation_actions[0]

    assert action.intent_type == "configure_source"
    assert action.primary_cta == "Set up source, then rerun"
    assert action.setup_steps == (
        "Set up an eligible source.",
        "Run / Refresh Research to create an updated live result.",
    )


def test_targeted_action_records_attempt_without_claiming_resolution() -> None:
    action = PrioritizedResearchAction(
        rank=1,
        action_id="cash-work-order",
        title="Test AAPL cash conversion",
        why_it_matters="Tests recurring cash generation.",
        source_type="SEC filing",
        status="unavailable",
        decision_impact="Thesis quality",
        auto_resolvable=True,
        action_kind="cash_conversion_analysis",
        semantic_key="cash_conversion_analysis:AAPL:company:sec_filing",
        related_research_request_ids=["selected-cash-action"],
    )
    lifecycle = SourceRetrievalLifecycle(
        request_id="selected-cash-action",
        source_id="sec-filing",
        status="unavailable",
        source_type="sec_filing",
        message="No additional period-aligned cash-flow evidence was returned.",
    )

    matched = reconcile_targeted_investigation_action(
        [action],
        action_id="selected-cash-action",
        action_kind="cash_conversion_analysis",
        retrieval_lifecycles=[lifecycle],
    )

    assert matched is action
    assert action.status == "attempted_unresolved"
    assert "Attempted via sec_filing" in action.attempt_summary
    assert "No additional period-aligned" in action.blocking_reason


def test_price_csv_requires_beta_history_and_supports_complete_aapl_package() -> None:
    event_day = date(2026, 7, 1)
    pre_dates = [event_day - timedelta(days=value) for value in range(148, 0, -1)]
    post_dates = [event_day + timedelta(days=value) for value in range(21)]
    rows = ["date,ticker,adjusted_close,volume"]
    for symbol in ("AAPL", "SPY", "XLK"):
        rows.extend(
            f"{observed.isoformat()},{symbol},{100 + index / 10:.2f},1000"
            for index, observed in enumerate([*pre_dates, *post_dates])
        )
    parsed = parse_event_price_csv(
        ("\n".join(rows) + "\n").encode(),
        ticker="AAPL",
        event_date=event_day.isoformat(),
        sector_ticker="XLK",
    )

    assert set(parsed) == {"AAPL", "SPY", "XLK"}
    assert all(len(values) == 169 for values in parsed.values())

    insufficient = ["date,ticker,adjusted_close"]
    for symbol in ("AAPL", "SPY"):
        insufficient.extend(
            f"{observed.isoformat()},{symbol},100"
            for observed in [*pre_dates[-10:], *post_dates]
        )
    with pytest.raises(ValueError, match="148 pre-event"):
        parse_event_price_csv(
            ("\n".join(insufficient) + "\n").encode(),
            ticker="AAPL",
            event_date=event_day.isoformat(),
        )
