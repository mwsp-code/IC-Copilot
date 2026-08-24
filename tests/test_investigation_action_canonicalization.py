from __future__ import annotations

from copy import deepcopy

from equity_research.application.investigation_action_canonicalization import (
    CANONICAL_ACTION_IDENTITY_VERSION,
    canonicalize_investigation_actions,
)
from equity_research.models import PrioritizedResearchAction


def _action(
    action_id: str,
    *,
    kind: str,
    title: str,
    semantic_key: str,
    sources: list[str] | None = None,
    status: str = "planned",
    rank: int = 1,
    materiality: str = "High",
    event_id: str = "",
    claims: list[str] | None = None,
) -> PrioritizedResearchAction:
    return PrioritizedResearchAction(
        rank=rank,
        action_id=action_id,
        title=title,
        objective=title,
        why_it_matters="This can change the investment conclusion.",
        source_type=", ".join(sources or []),
        status=status,
        decision_impact="Tests thesis quality.",
        auto_resolvable=True,
        blocks_stage="Research-Ready",
        action_kind=kind,
        materiality=materiality,
        event_id=event_id,
        required_source_families=list(sources or []),
        related_claim_ids=list(claims or []),
        semantic_key=semantic_key,
    )


def test_exact_legacy_aapl_cash_keys_merge_without_mutating_artifact() -> None:
    title = (
        "Test whether AAPL cash conversion remains recurring after working "
        "capital, capex, taxes, financing, and capital returns"
    )
    original = [
        _action(
            "efef48f363e4",
            kind="cash_conversion_analysis",
            title=title,
            semantic_key=(
                "cash_conversion_analysis:AAPL:company:cash_flow_statement"
            ),
            sources=["cash_flow_statement"],
        ),
        _action(
            "rq-watch-cash-generation-liquidity-1fea35b160",
            kind="cash_conversion_analysis",
            title=title,
            semantic_key=(
                "cash_conversion_analysis:AAPL:company:Cash flow statement"
            ),
            sources=[
                "Cash flow statement",
                "Debt footnote and maturity table",
            ],
        ),
    ]
    untouched = deepcopy(original)

    result = canonicalize_investigation_actions("aapl", original)

    assert original == untouched
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.rank == 1
    assert action.semantic_key.startswith(
        CANONICAL_ACTION_IDENTITY_VERSION + ":cash_conversion_analysis:AAPL:"
    )
    assert "sec_filing" in action.required_source_families
    assert result.resolve_action_id("efef48f363e4") == action.action_id
    assert result.resolve_action_id(
        "rq-watch-cash-generation-liquidity-1fea35b160"
    ) == action.action_id
    assert result.resolve_action_id(untouched[0].semantic_key) == action.action_id
    aliases = result.aliases_by_canonical_id[action.action_id]
    assert set(aliases.action_ids) >= {
        "efef48f363e4",
        "rq-watch-cash-generation-liquidity-1fea35b160",
    }
    assert result.display_collisions == ()


def test_shared_research_intent_merges_different_execution_origins() -> None:
    primary = _action(
        "work-order",
        kind="primary_source_validation",
        title="Reconcile AAPL cash conversion to filings",
        semantic_key="legacy-work-order",
    )
    request = _action(
        "qa-request",
        kind="cash_conversion_analysis",
        title="Test whether AAPL cash conversion is recurring",
        semantic_key="legacy-qa-request",
    )
    primary.intent_id = "intent-cash-conversion"
    request.intent_id = "intent-cash-conversion"

    result = canonicalize_investigation_actions("AAPL", [primary, request])

    assert len(result.actions) == 1
    assert result.actions[0].intent_id == "intent-cash-conversion"
    assert result.resolve_action_id("work-order") == result.actions[0].action_id
    assert result.resolve_action_id("qa-request") == result.actions[0].action_id


def test_repeated_consensus_cards_merge_but_explicit_events_do_not() -> None:
    title = "Capture point-in-time AAPL estimate revisions around 2026-07-31"
    repeated = [
        _action(
            "request-a",
            kind="consensus_revision_capture",
            title=title,
            semantic_key="consensus_revision_capture:AAPL:company:Court reference",
            sources=["Court or docket reference", "consensus_manual"],
        ),
        _action(
            "request-b",
            kind="consensus_revision_capture",
            title=title,
            semantic_key="consensus_revision_capture:AAPL:company:registered_source",
            sources=["Point-in-time consensus"],
        ),
    ]
    merged = canonicalize_investigation_actions("AAPL", repeated)
    assert len(merged.actions) == 1

    distinct = canonicalize_investigation_actions("AAPL", [
        _action(
            "event-a",
            kind="consensus_revision_capture",
            title="Capture AAPL estimate revisions",
            semantic_key="old-a",
            event_id="earnings-2026-q2",
        ),
        _action(
            "event-b",
            kind="consensus_revision_capture",
            title="Capture AAPL estimate revisions",
            semantic_key="old-b",
            event_id="wwdc-2026",
        ),
    ])
    assert len(distinct.actions) == 2
    assert len(distinct.display_collisions) == 1


def test_repeated_scenario_cards_merge_and_valuation_inputs_remain_distinct() -> None:
    title = "Complete AAPL bull/base/bear valuation and payoff assumptions"
    scenarios = canonicalize_investigation_actions("AAPL", [
        _action(
            "rq-watch-unmapped-metric-10d8f8324a",
            kind="valuation_scenarios",
            title=title,
            semantic_key="valuation_scenarios:AAPL:company:Issuer filing",
        ),
        _action(
            "rq-watch-share-count-capital-return-fbf42cae54a5",
            kind="valuation_scenarios",
            title=title,
            semantic_key="valuation_scenarios:AAPL:company:ADR ratio",
        ),
    ])
    assert len(scenarios.actions) == 1

    inputs = canonicalize_investigation_actions("AAPL", [
        _action(
            "input-ebitda",
            kind="valuation_input_resolution",
            title="Resolve AAPL Forward EBITDA for the valuation bridge",
            semantic_key="old-ebitda",
        ),
        _action(
            "input-debt",
            kind="valuation_input_resolution",
            title="Resolve AAPL Net Debt for the valuation bridge",
            semantic_key="old-debt",
        ),
    ])
    assert len(inputs.actions) == 2
    assert len({item.action_id for item in inputs.actions}) == 2


def test_named_external_themes_remain_distinct_while_generic_aliases_merge() -> None:
    named = canonicalize_investigation_actions("AAPL", [
        _action(
            "ai",
            kind="external_theme_validation",
            title="Validate the AAPL AI / cloud monetization theme with issuer evidence",
            semantic_key="old-ai",
        ),
        _action(
            "margin",
            kind="external_theme_validation",
            title="Validate the AAPL margin / EBITA debate theme with issuer evidence",
            semantic_key="old-margin",
        ),
    ])
    assert len(named.actions) == 2

    generic = canonicalize_investigation_actions("AAPL", [
        _action(
            "generic",
            kind="external_theme_validation",
            title="Validate the AAPL external research theme with issuer evidence",
            semantic_key="old-generic",
        ),
        _action(
            "general",
            kind="external_theme_validation",
            title="Validate the AAPL General external research theme with issuer evidence",
            semantic_key="old-general",
        ),
    ])
    assert len(generic.actions) == 1


def test_explicit_claim_targets_do_not_overmerge_and_report_display_collision() -> None:
    title = "Validate primary-source evidence for the top claim for AAPL"
    result = canonicalize_investigation_actions("AAPL", [
        _action(
            "claim-a-action",
            kind="primary_source_validation",
            title=title,
            semantic_key="legacy-a",
            claims=["claim-a"],
        ),
        _action(
            "claim-b-action",
            kind="primary_source_validation",
            title=title,
            semantic_key="legacy-b",
            claims=["claim-b"],
        ),
    ])

    assert len(result.actions) == 2
    assert len(result.display_collisions) == 1
    collision = result.display_collisions[0]
    assert set(collision.original_action_ids) == {
        "claim-a-action",
        "claim-b-action",
    }


def test_intent_mapping_supplies_missing_target_context() -> None:
    title = "Complete AAPL attribution"
    actions = [
        _action(
            "legacy-one",
            kind="market_event_attribution",
            title=title,
            semantic_key="legacy-key-one",
        ),
        _action(
            "legacy-two",
            kind="market_event_attribution",
            title=title,
            semantic_key="legacy-key-two",
        ),
    ]
    result = canonicalize_investigation_actions(
        "AAPL",
        actions,
        intent_mapping={
            "legacy-one": {"event_id": "event-one"},
            "legacy-key-two": {"event_id": "event-two"},
        },
    )
    assert len(result.actions) == 2
    assert len(result.display_collisions) == 1


def test_resolved_groups_are_filtered_and_open_actions_are_reranked() -> None:
    result = canonicalize_investigation_actions("JPM", [
        _action(
            "low",
            kind="primary_source_validation",
            title="Validate JPM deposit beta",
            semantic_key="low",
            rank=8,
            materiality="Low",
        ),
        _action(
            "closed",
            kind="primary_source_validation",
            title="Validate JPM CET1 ratio",
            semantic_key="closed",
            status="resolved",
            rank=1,
            materiality="Critical",
        ),
        _action(
            "high",
            kind="cash_conversion_analysis",
            title="Test JPM capital generation",
            semantic_key="high",
            rank=9,
            materiality="Critical",
        ),
    ])

    assert [item.rank for item in result.actions] == [1, 2]
    assert [item.materiality for item in result.actions] == ["Critical", "Low"]
    assert result.resolve_action_id("closed") is not None


def test_stale_consensus_kind_is_repaired_by_cash_conversion_intent() -> None:
    title = (
        "Test whether AAPL cash conversion remains recurring after working "
        "capital, capex, taxes, financing, and capital returns"
    )
    action = _action(
        "legacy-misclassified-cash",
        kind="consensus_revision_capture",
        title=title,
        semantic_key="legacy:consensus:AAPL",
        sources=["cash_flow_statement"],
    )

    result = canonicalize_investigation_actions("AAPL", [action])

    assert len(result.actions) == 1
    assert result.actions[0].action_kind == "cash_conversion_analysis"
    assert result.actions[0].required_source_families == ["sec_filing"]
