from __future__ import annotations

from equity_research.domain.research_intents import (
    LegacyIntentMappingStatus,
    ResearchIntentResolver,
)


def _resolve_cash(resolver: ResearchIntentResolver, **overrides):
    values = {
        "ticker": "AAPL",
        "cik": "320193",
        "action_kind": "cash_conversion_analysis",
        "title": "Test whether AAPL cash conversion is recurring",
        "objective": "Bridge cash conversion after working capital and capex",
        "question_status": "open",
        "answer_status": "planned",
        "remaining_work": ("Validate cash taxes",),
        "legacy_provider": "deepseek",
        "legacy_source_families": ("sec_filing",),
    }
    values.update(overrides)
    result = resolver.resolve_legacy(**values)
    assert result.intent is not None
    return result


def test_identity_excludes_provider_source_wording_and_status() -> None:
    first = _resolve_cash(ResearchIntentResolver()).intent
    second = _resolve_cash(
        ResearchIntentResolver(),
        title="Can the issuer sustain free cash flow?",
        objective="Different display wording",
        question_status="researching",
        answer_status="partially_answered",
        legacy_provider="codex",
        legacy_source_families=("issuer_ir_report", "market_data"),
    ).intent

    assert first is not None and second is not None
    assert first.intent_id == second.intent_id


def test_identity_changes_for_material_scope_and_decision_dimensions() -> None:
    resolver = ResearchIntentResolver()
    base = _resolve_cash(resolver).intent
    event = _resolve_cash(resolver, event_id="event-2026-q3").intent
    period = _resolve_cash(resolver, period_scope="FY2026 annual").intent
    dependency = _resolve_cash(resolver, decision_dependency="credit").intent

    assert base is not None and event is not None and period is not None
    assert dependency is not None
    assert len({base.intent_id, event.intent_id, period.intent_id, dependency.intent_id}) == 4


def test_cik_identity_survives_ticker_and_exchange_changes() -> None:
    first = _resolve_cash(ResearchIntentResolver(), ticker="AAPL", exchange="NASDAQ").intent
    second = _resolve_cash(ResearchIntentResolver(), ticker="APPLX", exchange="NYSE").intent

    assert first is not None and second is not None
    assert first.security_key == "cik:0000320193"
    assert first.intent_id == second.intent_id


def test_legacy_alias_and_text_inference_return_diagnostics() -> None:
    alias = _resolve_cash(ResearchIntentResolver())
    inferred = ResearchIntentResolver().resolve_legacy(
        ticker="AAPL",
        exchange="NASDAQ",
        objective=(
            "Test whether AAPL cash conversion remains recurring after working "
            "capital, capex, taxes, financing, and capital returns"
        ),
    )

    assert alias.status == LegacyIntentMappingStatus.ALIAS.value
    assert inferred.status == LegacyIntentMappingStatus.INFERRED.value
    assert inferred.intent is not None
    assert inferred.intent.question_key == "cash_generation_capital_allocation"
    assert any(item.code == "legacy_alias_mapped" for item in inferred.diagnostics)


def test_unmapped_legacy_wording_does_not_create_wording_derived_identity() -> None:
    result = ResearchIntentResolver().resolve_legacy(
        ticker="AAPL",
        objective="Explore an entirely novel unspecified issue",
    )

    assert result.status == LegacyIntentMappingStatus.UNRESOLVED.value
    assert result.intent is None
    assert any(item.code == "unmapped_legacy_intent" for item in result.diagnostics)


def test_cash_recurrence_and_repurchases_are_distinct_mechanisms() -> None:
    resolver = ResearchIntentResolver()
    recurrence = resolver.resolve_legacy(
        ticker="AAPL",
        question_key="cash_generation_capital_allocation",
        objective="Test recurring cash conversion after working capital and capex",
    ).intent
    repurchases = resolver.resolve_legacy(
        ticker="AAPL",
        objective="Test whether repurchases create net per share value",
    ).intent

    assert recurrence is not None and repurchases is not None
    assert recurrence.question_key == repurchases.question_key
    assert recurrence.mechanism_key == "recurring_cash_conversion"
    assert repurchases.mechanism_key == "per_share_capital_allocation"
    assert recurrence.intent_id != repurchases.intent_id


def test_upsert_is_deterministic_and_preserves_remaining_work_and_origins() -> None:
    first = _resolve_cash(
        ResearchIntentResolver(),
        answer_status="answered",
        remaining_work=(),
        origin_refs=("work-order-1",),
        title="Test cash conversion",
    ).intent
    second = _resolve_cash(
        ResearchIntentResolver(),
        answer_status="partially_answered",
        remaining_work=("Validate cash taxes", "Validate capital returns"),
        origin_refs=("qa-request-2",),
        materiality="Critical",
        title="Test whether AAPL cash conversion remains recurring",
    ).intent
    assert first is not None and second is not None

    forward = ResearchIntentResolver()
    forward.upsert(first)
    merged_forward = forward.upsert(second).intent
    reverse = ResearchIntentResolver()
    reverse.upsert(second)
    merged_reverse = reverse.upsert(first).intent

    assert merged_forward == merged_reverse
    assert merged_forward.answer_status == "partially_answered"
    assert merged_forward.question_status == "partially_answered"
    assert merged_forward.remaining_work == (
        "Validate capital returns",
        "Validate cash taxes",
    )
    assert merged_forward.origin_refs == ("qa-request-2", "work-order-1")
    assert merged_forward.materiality == "Critical"


def test_upsert_rejects_distinct_intents() -> None:
    resolver = ResearchIntentResolver()
    cash = _resolve_cash(ResearchIntentResolver()).intent
    valuation = resolver.resolve_legacy(
        ticker="AAPL",
        action_kind="valuation_scenarios",
        objective="Complete bull base and bear valuation",
    ).intent
    assert cash is not None and valuation is not None

    resolver.upsert(cash)
    resolver.upsert(valuation)

    assert len(resolver.all()) == 2
