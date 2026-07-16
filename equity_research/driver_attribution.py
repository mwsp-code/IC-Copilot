from __future__ import annotations

from statistics import mean

from .adr_profiles import adr_profile_for
from .models import (
    AttributionFactor,
    AttributionComponent,
    AttributionQualityCheck,
    AttributionWaterfall,
    ChangeEvent,
    DriverAttribution,
    EventWindowReaction,
    ExpectationsBridge,
    ExternalEvidence,
    ExternalEvidenceBundle,
    FactorExposure,
    LiquiditySignal,
    MacroShock,
    OptionsExpectation,
    PositioningSignal,
    TradeIdea,
)


RETURN_WINDOWS = ("5d", "1d", "20d")


def attach_driver_attributions(
    ideas: list[TradeIdea],
    event_window_reactions: dict[str, EventWindowReaction],
    external_evidence: ExternalEvidenceBundle,
    expectations: ExpectationsBridge | None = None,
    ticker: str | None = None,
) -> None:
    for idea in ideas:
        event = idea.source_events[0] if idea.source_events else None
        if not event:
            continue
        reaction = _reaction_for_event(event, event_window_reactions)
        idea.driver_attribution = build_driver_attribution(
            idea, reaction, external_evidence, expectations, ticker=ticker,
        )


def build_driver_attribution(
    idea: TradeIdea,
    reaction: EventWindowReaction | None,
    external_evidence: ExternalEvidenceBundle | None,
    expectations: ExpectationsBridge | None = None,
    *,
    ticker: str | None = None,
) -> DriverAttribution:
    event = idea.source_events[0] if idea.source_events else None
    if event is None:
        return DriverAttribution(
            "Unavailable", "Unknown", "No source event is attached to this idea.",
            "Low", None, data_gaps=["Idea has no source event."],
        )
    window = _selected_window(reaction)
    raw = _window_value(reaction.raw_returns if reaction else {}, window)
    market_relative = _window_value(reaction.market_relative_returns if reaction else {}, window)
    sector_relative = _window_value(reaction.sector_relative_returns if reaction else {}, window)
    beta_adjusted = _window_value(reaction.beta_adjusted_returns if reaction else {}, window)
    residual = _first_not_none(beta_adjusted, sector_relative, market_relative, raw)
    peer_sympathy = _peer_sympathy(idea)
    consensus_revision = (
        idea.market_capture.consensus_revision_pct
        if idea.market_capture else None
    )
    macro = _macro_evidence(external_evidence, event)
    factor_context = _factor_context(external_evidence, event, window)
    macro_calendar_context = _macro_calendar_context(macro)
    positioning_context = _positioning_context(external_evidence, event)
    liquidity_context = _liquidity_context(reaction, window)
    options_context = _options_context(external_evidence, event)
    narrative = _narrative_evidence(external_evidence, event)
    narrative_score = narrative[0].metric_value if narrative else None
    narrative_label = _narrative_label(narrative_score)
    waterfall = _build_waterfall(
        raw=raw,
        market_relative=market_relative,
        sector_relative=sector_relative,
        beta_adjusted=beta_adjusted,
        peer_sympathy=peer_sympathy,
        consensus_revision=consensus_revision,
        factor_context=factor_context,
        macro_context=macro_calendar_context,
        positioning_context=positioning_context,
        liquidity_context=liquidity_context,
        options_context=options_context,
        reaction=reaction,
        window=window,
    )
    residual = waterfall.residual_pct if waterfall else _first_not_none(beta_adjusted, sector_relative, market_relative, raw)
    factors: list[AttributionFactor] = []
    factors.append(_company_factor(event, residual))
    market_factor = _market_factor(reaction, raw, market_relative, window)
    if market_factor:
        factors.append(market_factor)
    sector_factor = _sector_factor(reaction, raw, sector_relative, market_relative, window)
    if sector_factor:
        factors.append(sector_factor)
    china_adr_factor = _china_adr_benchmark_factor(ticker, reaction)
    if china_adr_factor:
        factors.append(china_adr_factor)
    peer_factor = _peer_factor(peer_sympathy, event)
    if peer_factor:
        factors.append(peer_factor)
    expectations_factor = _expectations_factor(consensus_revision, expectations, event)
    if expectations_factor:
        factors.append(expectations_factor)
    management_factor = _management_factor(event)
    if management_factor:
        factors.append(management_factor)
    for item in factor_context[:6]:
        factors.append(_factor_exposure_factor(item))
    for item in macro[:4]:
        factors.append(_macro_factor(item))
    for item in positioning_context[:4]:
        factors.append(_positioning_factor(item))
    for item in liquidity_context[:2]:
        factors.append(_liquidity_factor(item))
    for item in options_context[:1]:
        factors.append(_options_factor(item))
    if narrative:
        factors.append(_narrative_factor(narrative[0]))

    gaps = []
    if not reaction:
        gaps.append("No event-window price reaction was available.")
    elif reaction.status not in {"available", "window_pending"}:
        gaps.append(f"Price reaction status: {reaction.status}. {reaction.reason}")
    if consensus_revision is None:
        gaps.append("Consensus revision is unavailable for this event.")
    if not macro:
        gaps.append("No point-in-time macro factor was available at or before the event date.")
    if not factor_context:
        gaps.append("No point-in-time factor-return context was available at or before the event date.")
    if not positioning_context:
        gaps.append("No point-in-time positioning signal was available at or before the event date.")
    if not options_context or all(item.status == "Unavailable" for item in options_context):
        gaps.append("Options-implied move is unavailable; no options EV or implied expectation is inferred.")
    if not narrative:
        gaps.append("Narrative saturation is unavailable or disabled.")

    classification = _classification(
        raw, market_relative, sector_relative, residual,
        peer_sympathy, consensus_revision, macro, narrative_label, event,
    )
    confidence = _confidence(classification, reaction, factors, gaps)
    headline = _headline(classification, raw, residual, peer_sympathy, consensus_revision)
    classification_evidence = _classification_evidence(
        classification, raw, market_relative, sector_relative, beta_adjusted,
        residual, peer_sympathy, consensus_revision, macro, narrative_label, reaction,
    )
    falsification_tests = _attribution_falsification_tests(
        classification, event, residual, peer_sympathy, consensus_revision, macro,
    )
    next_checks = _next_attribution_checks(classification, gaps, reaction, idea)
    attribution_summary = _attribution_summary(
        classification, waterfall, raw, residual, peer_sympathy, consensus_revision, narrative_label,
    )
    attribution_readiness = _attribution_readiness(reaction, waterfall, consensus_revision, gaps)
    attribution_quality = _attribution_quality_checks(
        reaction,
        waterfall,
        idea,
        event,
        raw,
        market_relative,
        sector_relative,
        beta_adjusted,
        peer_sympathy,
        consensus_revision,
        macro,
        factor_context,
        positioning_context,
        liquidity_context,
        options_context,
        narrative,
    )
    return DriverAttribution(
        status="Available" if factors else "Unavailable",
        classification=classification,
        headline=headline,
        confidence=confidence,
        event_date=event.event_date,
        return_window=window,
        raw_return_pct=raw,
        market_relative_pct=market_relative,
        sector_relative_pct=sector_relative,
        beta_adjusted_pct=beta_adjusted,
        peer_sympathy_pct=peer_sympathy,
        consensus_revision_pct=consensus_revision,
        narrative_saturation=narrative_label,
        narrative_score=narrative_score,
        macro_context=macro,
        factors=factors,
        waterfall=waterfall,
        factor_context=factor_context,
        macro_calendar_context=macro_calendar_context,
        positioning_context=positioning_context,
        liquidity_context=liquidity_context,
        options_context=options_context,
        residual_pct=waterfall.residual_pct if waterfall else residual,
        residual_explanation=_residual_explanation(residual, raw),
        classification_evidence=classification_evidence,
        attribution_summary=attribution_summary,
        attribution_readiness=attribution_readiness,
        attribution_quality_score=_quality_score(attribution_quality),
        attribution_quality=attribution_quality,
        falsification_tests=falsification_tests,
        next_attribution_checks=next_checks,
        data_gaps=gaps,
    )


def _reaction_for_event(
    event: ChangeEvent,
    reactions: dict[str, EventWindowReaction],
) -> EventWindowReaction | None:
    for key, reaction in reactions.items():
        if reaction.event_date == event.event_date and key.startswith(f"{event.category}:"):
            return reaction
    for reaction in reactions.values():
        if reaction.event_date == event.event_date:
            return reaction
    return None


def _selected_window(reaction: EventWindowReaction | None) -> str | None:
    if not reaction:
        return None
    for window in RETURN_WINDOWS:
        if reaction.raw_returns.get(window) is not None:
            return window
    return None


def _window_value(values: dict[str, float | None], window: str | None) -> float | None:
    return values.get(window) if window else None


def _first_not_none(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def _build_waterfall(
    *,
    raw: float | None,
    market_relative: float | None,
    sector_relative: float | None,
    beta_adjusted: float | None,
    peer_sympathy: float | None,
    consensus_revision: float | None,
    factor_context: list[FactorExposure],
    macro_context: list[MacroShock],
    positioning_context: list[PositioningSignal],
    liquidity_context: list[LiquiditySignal],
    options_context: list[OptionsExpectation],
    reaction: EventWindowReaction | None,
    window: str | None,
) -> AttributionWaterfall:
    gaps: list[str] = []
    components: list[AttributionComponent] = []
    if raw is None:
        return AttributionWaterfall(
            "Unavailable", window, None, None, None, None, [],
            ["Raw event-window return is unavailable."],
        )

    market_residual = beta_adjusted if beta_adjusted is not None else market_relative
    market_component = raw - market_residual if market_residual is not None else None
    if market_component is not None:
        components.append(AttributionComponent(
            "market_beta",
            f"Market beta versus {reaction.benchmark_ticker if reaction else 'market'}",
            round(market_component, 3),
            "High" if reaction and reaction.beta is not None else "Medium",
            reaction.benchmark_ticker if reaction and reaction.benchmark_ticker else "SPY",
            "Market contribution is the portion removed by market- or beta-adjusting the event return.",
            "If beta-adjusted residual remains large, broad market beta is not enough to explain the move.",
            2,
        ))
    else:
        gaps.append("Market/beta component is unavailable because benchmark or beta-adjusted returns are missing.")

    sector_component = None
    if sector_relative is not None:
        sector_total = raw - sector_relative
        sector_component = sector_total - (market_component or 0)
        if abs(sector_component) >= 0.25:
            components.append(AttributionComponent(
                "sector_industry",
                f"Sector or industry move versus {reaction.sector_benchmark_ticker if reaction else 'sector ETF'}",
                round(sector_component, 3),
                "Medium",
                reaction.sector_benchmark_ticker if reaction and reaction.sector_benchmark_ticker else "sector ETF",
                "Sector contribution is measured incrementally after the available market/beta component.",
                "If curated peers do not show sympathy, the sector explanation weakens.",
                2,
            ))
    elif reaction and reaction.sector_benchmark_ticker:
        gaps.append("Sector component is unavailable because sector benchmark returns are missing.")

    factor_component = sum(
        item.contribution_pct for item in factor_context
        if item.contribution_pct is not None
    )
    if factor_context:
        components.append(AttributionComponent(
            "style_factor",
            "Style/factor context",
            round(factor_component, 3) if factor_component else None,
            "Medium" if factor_component else "Low",
            "Ken French Data Library",
            "Daily factor returns are shown as context; numeric contribution requires pre-event factor betas.",
            "If factor betas are unavailable or unstable, style-factor attribution should stay contextual.",
            3,
        ))
    else:
        gaps.append("Style/factor context is unavailable.")

    if macro_context:
        components.append(AttributionComponent(
            "macro_shock",
            "Macro calendar and shock context",
            None,
            "Medium",
            ", ".join(sorted({item.provider for item in macro_context}))[:80],
            "Macro observations released on or before the event date are available as explanatory context.",
            "If residual remains large after market/sector adjustment, macro evidence is not sufficient by itself.",
            2,
        ))
    else:
        gaps.append("Macro shock context is unavailable or not lookahead-safe.")

    if positioning_context or liquidity_context:
        numeric_liquidity = [
            item.value for item in liquidity_context
            if item.value is not None and item.label == "Event volume ratio"
        ]
        components.append(AttributionComponent(
            "positioning_liquidity",
            "Positioning and liquidity context",
            None,
            "Medium" if numeric_liquidity or positioning_context else "Low",
            "Price/volume, FINRA/SEC/CFTC placeholders",
            "Volume, short-sale, fails-to-deliver, COT, or options evidence can indicate crowding and flow pressure.",
            "If positioning data is stale or unavailable, this driver remains supporting context only.",
            3,
        ))
    else:
        gaps.append("Positioning/liquidity context is unavailable.")

    if consensus_revision is not None and abs(consensus_revision) >= 2:
        components.append(AttributionComponent(
            "expectations",
            "Consensus or surprise revision",
            None,
            "Medium",
            "Consensus snapshots",
            f"Consensus revised {consensus_revision:+.1f}% around the event.",
            "If revisions reverse or fail to appear in later snapshots, this explanation weakens.",
            3,
        ))
    else:
        gaps.append("Numeric expectations contribution is unavailable or too small to attribute.")

    if options_context:
        option = options_context[0]
        components.append(AttributionComponent(
            "options_expectations",
            "Options-implied expectations",
            None,
            option.confidence,
            option.provider,
            option.summary or "Options adapter is present but no implied move is populated.",
            "Options attribution requires contract/expiry/strike/premium/IV/liquidity data.",
            3,
        ))

    explained = round(sum(
        item.contribution_pct for item in components
        if item.contribution_pct is not None
    ), 3)
    residual = round(raw - explained, 3)
    balance = round(raw - explained - residual, 6)
    return AttributionWaterfall(
        "Available", window, raw, explained, residual, balance, components, gaps,
    )


def _factor_context(
    package: ExternalEvidenceBundle | None,
    event: ChangeEvent,
    window: str | None,
) -> list[FactorExposure]:
    if not package or not event.event_date:
        return []
    rows = [
        item for item in package.evidence
        if item.source_type == "factor_return"
        and item.source_as_of
        and item.source_as_of[:10] <= event.event_date[:10]
        and item.lookahead_safe
    ]
    return [
        FactorExposure(
            factor_name=item.metric_name or item.title,
            factor_return_pct=item.metric_value,
            beta=None,
            contribution_pct=None,
            window=window,
            confidence=item.confidence,
            source=item.provider,
            source_as_of=item.source_as_of,
        )
        for item in rows
    ]


def _macro_calendar_context(items: list[ExternalEvidence]) -> list[MacroShock]:
    return [
        MacroShock(
            provider=item.provider,
            label=item.title,
            metric_name=item.metric_name,
            change=item.metric_value,
            direction=item.direction,
            confidence=item.confidence,
            source_as_of=item.source_as_of,
            release_date=item.release_date,
        )
        for item in items
    ]


def _positioning_context(
    package: ExternalEvidenceBundle | None,
    event: ChangeEvent,
) -> list[PositioningSignal]:
    if not package:
        return []
    rows = [
        item for item in package.evidence
        if item.source_type in {"positioning", "short_sale", "fails_to_deliver", "cot_positioning"}
        and (not item.source_as_of or not event.event_date or item.source_as_of[:10] <= event.event_date[:10])
        and item.lookahead_safe
    ]
    return [
        PositioningSignal(
            provider=item.provider,
            label=item.title,
            metric_name=item.metric_name,
            value=item.metric_value,
            direction=item.direction,
            confidence=item.confidence,
            source_as_of=item.source_as_of,
            summary=item.summary,
        )
        for item in rows
    ]


def _liquidity_context(
    reaction: EventWindowReaction | None,
    window: str | None,
) -> list[LiquiditySignal]:
    if not reaction:
        return []
    rows: list[LiquiditySignal] = []
    if reaction.volume_ratio is not None:
        direction = "positive" if reaction.volume_ratio >= 1.5 else "neutral"
        rows.append(LiquiditySignal(
            "Event volume ratio",
            round(reaction.volume_ratio, 3),
            direction,
            "Medium",
            reaction.source,
            f"Event-day volume was {reaction.volume_ratio:.2f}x the prior 60-session average.",
        ))
    if reaction.path_min_20d_pct is not None or reaction.path_max_20d_pct is not None:
        rows.append(LiquiditySignal(
            "20-day price path",
            None,
            "neutral",
            "Medium" if window else "Low",
            reaction.source,
            f"20-day path range: min {_fmt_pct(reaction.path_min_20d_pct)}, max {_fmt_pct(reaction.path_max_20d_pct)}.",
        ))
    return rows


def _options_context(
    package: ExternalEvidenceBundle | None,
    event: ChangeEvent,
) -> list[OptionsExpectation]:
    if not package:
        return [OptionsExpectation(
            "Options adapter",
            "Unavailable",
            summary="No options provider is configured; implied move is not inferred.",
        )]
    rows = [
        item for item in package.evidence
        if item.source_type == "options_expectation"
        and (not item.source_as_of or not event.event_date or item.source_as_of[:10] <= event.event_date[:10])
        and item.lookahead_safe
    ]
    if not rows:
        return [OptionsExpectation(
            "Options adapter",
            "Unavailable",
            summary="No options provider populated contract-level implied move data.",
        )]
    return [
        OptionsExpectation(
            provider=item.provider,
            status="Available",
            implied_move_pct=item.metric_value,
            confidence=item.confidence,
            source_as_of=item.source_as_of,
            summary=item.summary,
        )
        for item in rows
    ]


def _company_factor(event: ChangeEvent, residual: float | None) -> AttributionFactor:
    source_tier = min(
        [citation.source_tier or 3 for citation in event.citations] or [3]
    )
    confidence = "High" if source_tier <= 1 and residual is not None and abs(residual) >= 2 else "Medium" if event.citations else "Low"
    return AttributionFactor(
        driver_type="company_evidence",
        label=event.category.replace("_", " ").title(),
        direction=event.direction,
        confidence=confidence,
        magnitude_pct=residual,
        explanation=(
            f"The source event says: {event.summary} "
            "This is the company-specific explanation to test against market, peer, macro, and consensus moves."
        ),
        disconfirming_evidence=(
            "The driver weakens if next filings, facts, management commentary, or consensus revisions fail "
            "to move in the same direction."
        ),
        source_tier=source_tier,
        citations=list(event.citations),
    )


def _market_factor(
    reaction: EventWindowReaction | None,
    raw: float | None,
    market_relative: float | None,
    window: str | None,
) -> AttributionFactor | None:
    if not reaction or raw is None or market_relative is None or window is None:
        return None
    market_component = raw - market_relative
    if abs(market_component) < 1.5:
        return None
    return AttributionFactor(
        "market_beta",
        f"Broad market move versus {reaction.benchmark_ticker or 'market'}",
        "positive" if market_component > 0 else "negative",
        "Medium" if reaction.beta is None else "High",
        market_component,
        f"Raw {window} return differs from market-relative return by {market_component:+.1f} percentage points.",
        "The market-beta explanation weakens if beta-adjusted and sector-relative residuals stay large.",
        2,
    )


def _sector_factor(
    reaction: EventWindowReaction | None,
    raw: float | None,
    sector_relative: float | None,
    market_relative: float | None,
    window: str | None,
) -> AttributionFactor | None:
    if not reaction or raw is None or sector_relative is None or window is None:
        return None
    sector_component = raw - sector_relative
    market_component = raw - market_relative if market_relative is not None else None
    if abs(sector_component) < 1.5 or (market_component is not None and abs(sector_component) <= abs(market_component) + 0.5):
        return None
    return AttributionFactor(
        "sector_peer",
        f"Sector move versus {reaction.sector_benchmark_ticker or 'sector benchmark'}",
        "positive" if sector_component > 0 else "negative",
        "Medium",
        sector_component,
        f"Raw {window} return differs from sector-relative return by {sector_component:+.1f} percentage points.",
        "The sector explanation weakens if curated peers do not show sympathy moves.",
        2,
    )


def _china_adr_benchmark_factor(
    ticker: str | None,
    reaction: EventWindowReaction | None,
) -> AttributionFactor | None:
    if not ticker:
        return None
    profile = adr_profile_for(ticker)
    if not profile or not profile.benchmark_tickers:
        return None
    used = reaction.sector_benchmark_ticker if reaction else None
    proxies = ", ".join(profile.benchmark_tickers)
    return AttributionFactor(
        "china_adr_context",
        "China ADR benchmark bundle",
        "neutral",
        "Medium" if used and used in profile.benchmark_tickers else "Low",
        None,
        (
            f"{ticker.upper()} is covered by an ADR/FPI playbook. Attribution should be cross-checked against "
            f"{proxies}; the event-window benchmark used here is {used or 'unavailable'}."
        ),
        (
            "The China ADR context weakens if KWEB/MCHI/HK-tech proxies, RMB/CNH, and local demand indicators "
            "do not move with the stock or if company-specific residual evidence remains dominant."
        ),
        3,
    )


def _peer_sympathy(idea: TradeIdea) -> float | None:
    values = [
        item.price_reaction_pct for item in idea.peer_readthrough
        if item.price_reaction_pct is not None
    ]
    return round(mean(values), 3) if values else None


def _peer_factor(peer_sympathy: float | None, event: ChangeEvent) -> AttributionFactor | None:
    if peer_sympathy is None or abs(peer_sympathy) < 1:
        return None
    event_sign = 1 if event.direction == "positive" else -1 if event.direction == "negative" else 0
    confirms = event_sign == 0 or peer_sympathy * event_sign > 0
    return AttributionFactor(
        "peer_sympathy",
        "Curated peer sympathy move",
        "positive" if peer_sympathy > 0 else "negative",
        "Medium",
        peer_sympathy,
        "Curated peers moved in the same direction as the event." if confirms else "Curated peers moved against the event direction, creating possible counter-evidence.",
        "Peer read-through weakens if peer filings or own-event reactions do not corroborate the focal event.",
        3,
    )


def _expectations_factor(
    consensus_revision: float | None,
    expectations: ExpectationsBridge | None,
    event: ChangeEvent,
) -> AttributionFactor | None:
    comparison = None
    if expectations:
        comparison = next(
            (item for item in expectations.comparisons if item.surprise_pct is not None and abs(item.surprise_pct) >= 3),
            None,
        )
    if consensus_revision is None and comparison is None:
        return None
    magnitude = consensus_revision if consensus_revision is not None else comparison.surprise_pct if comparison else None
    return AttributionFactor(
        "expectations",
        "Expectations revision or surprise",
        "positive" if (magnitude or 0) > 0 else "negative" if (magnitude or 0) < 0 else event.direction,
        "Medium",
        magnitude,
        (
            f"Consensus revised {consensus_revision:+.1f}% after the event."
            if consensus_revision is not None
            else f"{comparison.metric} surprise was {comparison.surprise_pct:+.1f}%."
        ),
        "The expectations explanation weakens if post-event revisions reverse or fail to appear in later snapshots.",
        3,
    )


def _management_factor(event: ChangeEvent) -> AttributionFactor | None:
    metrics = event.metrics or {}
    if not (
        metrics.get("management_claim_id")
        or metrics.get("sentiment_label")
        or event.category in {"tone_shift", "qa_evasion", "guidance_specificity_change", "guidance_shift"}
    ):
        return None
    label = metrics.get("sentiment_label") or event.category.replace("_", " ")
    score = metrics.get("sentiment_score")
    return AttributionFactor(
        "management_signal",
        f"Management signal: {label}",
        event.direction,
        "Medium" if metrics.get("cross_checked") else "Low",
        float(score) if isinstance(score, (int, float)) else None,
        "Management language, tone, specificity, or evasiveness contributed to the thesis.",
        "The management explanation weakens if filings/facts contradict the statement or next-call tone reverts.",
        2 if event.source in {"earnings_call_transcript", "issuer meeting/proxy"} else 1,
        list(event.citations),
    )


def _macro_evidence(
    package: ExternalEvidenceBundle | None,
    event: ChangeEvent,
) -> list[ExternalEvidence]:
    if not package or not event.event_date:
        return []
    return [
        item for item in package.evidence
        if item.source_type in {"macro_factor", "china_macro"}
        and item.source_as_of
        and item.source_as_of[:10] <= event.event_date[:10]
        and item.lookahead_safe
    ]


def _narrative_evidence(
    package: ExternalEvidenceBundle | None,
    event: ChangeEvent,
) -> list[ExternalEvidence]:
    if not package:
        return []
    return [
        item for item in package.evidence
        if item.source_type == "narrative_saturation"
        and (not item.source_as_of or not event.event_date or item.source_as_of[:10] <= event.event_date[:10])
    ]


def _macro_factor(item: ExternalEvidence) -> AttributionFactor:
    value = item.metric_value
    return AttributionFactor(
        "macro_factor",
        item.title,
        item.direction,
        item.confidence,
        value,
        (
            item.summary
            + (
                f" Source as of {item.source_as_of}; release/vintage check passed."
                if item.lookahead_safe
                else " Source timing is not lookahead-safe."
            )
        ),
        "The macro explanation weakens if company residual returns remain large after market/sector adjustment.",
        item.source_tier,
        [item.citation] if item.citation else [],
    )


def _factor_exposure_factor(item: FactorExposure) -> AttributionFactor:
    return AttributionFactor(
        "style_factor",
        item.factor_name,
        "positive" if (item.factor_return_pct or 0) > 0 else "negative" if (item.factor_return_pct or 0) < 0 else "neutral",
        item.confidence,
        item.contribution_pct if item.contribution_pct is not None else item.factor_return_pct,
        (
            f"{item.factor_name} return was {_fmt_pct(item.factor_return_pct)} as of {item.source_as_of or 'n/a'}. "
            "Contribution remains contextual until pre-event factor beta is available."
        ),
        "The factor explanation weakens if style-factor returns are small or company residual remains large.",
        3,
    )


def _positioning_factor(item: PositioningSignal) -> AttributionFactor:
    return AttributionFactor(
        "positioning",
        item.label,
        item.direction,
        item.confidence,
        item.value,
        item.summary or "Positioning signal is available as supporting context.",
        "The positioning explanation weakens if the signal is stale, unavailable, or contradicted by volume/liquidity data.",
        3,
    )


def _liquidity_factor(item: LiquiditySignal) -> AttributionFactor:
    return AttributionFactor(
        "liquidity",
        item.label,
        item.direction,
        item.confidence,
        item.value,
        item.summary,
        "The liquidity explanation weakens if abnormal volume or price path evidence is absent.",
        3,
    )


def _options_factor(item: OptionsExpectation) -> AttributionFactor:
    return AttributionFactor(
        "options_expectations",
        "Options-implied expectations",
        "neutral",
        item.confidence,
        item.implied_move_pct,
        item.summary or "Options-implied expectations are unavailable.",
        "Options attribution requires populated option contract, expiry, premium, IV, and liquidity data.",
        3,
    )


def _narrative_factor(item: ExternalEvidence) -> AttributionFactor:
    return AttributionFactor(
        "narrative_saturation",
        item.title,
        "neutral",
        item.confidence,
        item.metric_value,
        item.summary,
        "The narrative-crowding explanation weakens if primary evidence is new and consensus/price have not reacted.",
        item.source_tier,
        [item.citation] if item.citation else [],
    )


def _narrative_label(score: float | None) -> str:
    if score is None:
        return "Unknown"
    if score >= 5:
        return "Crowded"
    if score >= 1:
        return "Active"
    return "Quiet"


def _classification(
    raw: float | None,
    market_relative: float | None,
    sector_relative: float | None,
    residual: float | None,
    peer_sympathy: float | None,
    consensus_revision: float | None,
    macro: list[ExternalEvidence],
    narrative_label: str,
    event: ChangeEvent,
) -> str:
    if raw is None:
        return "Unknown"
    if market_relative is not None and abs(market_relative) < 1 and abs(raw) >= 2:
        return "Market-driven"
    if sector_relative is not None and abs(sector_relative) < 1 and abs(raw) >= 2:
        return "Sector-driven"
    if macro and residual is not None and abs(residual) < max(2.5, abs(raw) * 0.6):
        return "Macro-sensitive"
    event_sign = 1 if event.direction == "positive" else -1 if event.direction == "negative" else 0
    if peer_sympathy is not None and event_sign and peer_sympathy * event_sign > 0 and abs(peer_sympathy) >= 2:
        return "Peer-confirmed"
    if consensus_revision is not None and event_sign and consensus_revision * event_sign > 0 and abs(consensus_revision) >= 2:
        return "Expectations-driven"
    if residual is not None and abs(residual) >= 2:
        return "Company-specific"
    if narrative_label == "Crowded":
        return "Narrative-crowded"
    return "Unexplained"


def _confidence(
    classification: str,
    reaction: EventWindowReaction | None,
    factors: list[AttributionFactor],
    gaps: list[str],
) -> str:
    if classification == "Unknown" or not reaction:
        return "Low"
    high_factors = sum(1 for item in factors if item.confidence == "High")
    if high_factors >= 1 and len(gaps) <= 2 and reaction.status == "available":
        return "High"
    if factors and len(gaps) <= 4:
        return "Medium"
    return "Low"


def _headline(
    classification: str,
    raw: float | None,
    residual: float | None,
    peer_sympathy: float | None,
    consensus_revision: float | None,
) -> str:
    pieces = [classification.replace("-", " ").lower()]
    if raw is not None:
        pieces.append(f"raw move {raw:+.1f}%")
    if residual is not None:
        pieces.append(f"residual {residual:+.1f}%")
    if peer_sympathy is not None:
        pieces.append(f"peer sympathy {peer_sympathy:+.1f}%")
    if consensus_revision is not None:
        pieces.append(f"consensus revision {consensus_revision:+.1f}%")
    return "Price move attribution: " + "; ".join(pieces) + "."


def _classification_evidence(
    classification: str,
    raw: float | None,
    market_relative: float | None,
    sector_relative: float | None,
    beta_adjusted: float | None,
    residual: float | None,
    peer_sympathy: float | None,
    consensus_revision: float | None,
    macro: list[ExternalEvidence],
    narrative_label: str,
    reaction: EventWindowReaction | None,
) -> list[str]:
    rows: list[str] = []
    if raw is not None:
        rows.append(f"Raw event-window move was {raw:+.1f}%.")
    if classification == "Market-driven":
        rows.append(f"Market-relative move was {_fmt_pct(market_relative)}, suggesting broad market beta explained most of the raw move.")
    elif classification == "Sector-driven":
        rows.append(f"Sector-relative move was {_fmt_pct(sector_relative)}, suggesting sector/industry context explained most of the raw move.")
    elif classification == "Macro-sensitive":
        rows.append(f"Lookahead-safe macro context was available: {', '.join(item.metric_name or item.title for item in macro[:3])}.")
        rows.append(f"Residual after available market/sector/beta context was {_fmt_pct(residual)}.")
    elif classification == "Peer-confirmed":
        rows.append(f"Curated peer sympathy move was {_fmt_pct(peer_sympathy)} in the event direction.")
    elif classification == "Expectations-driven":
        rows.append(f"Point-in-time consensus revision was {_fmt_pct(consensus_revision)} in the event direction.")
    elif classification == "Company-specific":
        rows.append(f"Residual after available market/sector/beta context was {_fmt_pct(residual)}, large enough to require company-specific explanation.")
        if beta_adjusted is not None:
            rows.append(f"Beta-adjusted return was {_fmt_pct(beta_adjusted)}.")
    elif classification == "Narrative-crowded":
        rows.append(f"Narrative saturation was {narrative_label}; narrative signals are context only.")
    elif classification == "Unexplained":
        rows.append("Available market, sector, peer, macro, consensus, and narrative inputs did not explain the event move.")
    if reaction and reaction.source:
        rows.append(f"Price source: {reaction.source}; anchor date {reaction.anchor_date or reaction.event_date or 'unknown'}.")
    return _dedupe(rows)[:8]


def _attribution_falsification_tests(
    classification: str,
    event: ChangeEvent,
    residual: float | None,
    peer_sympathy: float | None,
    consensus_revision: float | None,
    macro: list[ExternalEvidence],
) -> list[str]:
    rows: list[str] = []
    if classification == "Company-specific":
        rows.extend([
            "Company-specific label weakens if beta-adjusted/sector-relative residual falls below 1% after better benchmarks or intraday timestamps.",
            "Company-specific label weakens if peer metric read-through shows the same operating change across the sector.",
            "Company-specific label weakens if later filings, management commentary, or consensus revisions do not corroborate the source claim.",
        ])
    elif classification == "Market-driven":
        rows.extend([
            "Market-driven label weakens if beta-adjusted residual remains above 2% using a pre-event beta with sufficient history.",
            "Market-driven label weakens if company evidence and peer metric read-through independently point to a focal-company driver.",
        ])
    elif classification == "Sector-driven":
        rows.extend([
            "Sector-driven label weakens if curated peers or sector ETF did not move in the same direction on the anchored event window.",
            "Sector-driven label weakens if company-specific residual stays large after sector and peer adjustments.",
        ])
    elif classification == "Macro-sensitive":
        rows.extend([
            "Macro-sensitive label weakens if macro observations were not released on or before the event timestamp.",
            "Macro-sensitive label weakens if market/sector-adjusted residual remains large after macro shock days are excluded.",
        ])
    elif classification == "Peer-confirmed":
        rows.extend([
            "Peer-confirmed label weakens if peer own-event metrics do not corroborate the focal company driver.",
            "Peer-confirmed label weakens if peer price moves were caused by unrelated company-specific events.",
        ])
    elif classification == "Expectations-driven":
        rows.extend([
            "Expectations-driven label weakens if point-in-time consensus revisions are stale, unofficial-only, or reverse in later snapshots.",
            "Expectations-driven label weakens if price moved before the revision was observable.",
        ])
    elif classification == "Narrative-crowded":
        rows.extend([
            "Narrative-crowded label weakens if primary evidence is new and consensus/price did not react.",
            "Narrative signals cannot establish causality without issuer, filing, price, or consensus corroboration.",
        ])
    else:
        rows.extend([
            "Unexplained label should be revisited after price, benchmark, peer, consensus, macro, and source-evidence gaps are filled.",
            "Attribution should not be used as thesis support until at least one source-backed driver is confirmed.",
        ])
    if residual is not None:
        rows.append(f"Residual check: current residual is {_fmt_pct(residual)}.")
    if peer_sympathy is not None:
        rows.append(f"Peer check: current peer sympathy is {_fmt_pct(peer_sympathy)}.")
    if consensus_revision is not None:
        rows.append(f"Consensus check: current revision is {_fmt_pct(consensus_revision)}.")
    if macro:
        rows.append(f"Macro timing check: {len(macro)} macro observation(s) passed lookahead filters.")
    if event.citations:
        rows.append("Source-evidence check: revisit citation excerpts if later facts contradict the event claim.")
    return _dedupe(rows)[:8]


def _next_attribution_checks(
    classification: str,
    gaps: list[str],
    reaction: EventWindowReaction | None,
    idea: TradeIdea,
) -> list[str]:
    rows: list[str] = []
    if not reaction or reaction.status != "available":
        rows.append("Fetch adjusted event-window price bars and benchmark/sector ETF returns for the exact event anchor date.")
    if any("Consensus revision" in gap for gap in gaps):
        rows.append("Seed official point-in-time consensus snapshots before and after the event.")
    if any("macro" in gap.lower() for gap in gaps):
        rows.append("Attach lookahead-safe macro calendar observations released on or before the event date.")
    if any("factor" in gap.lower() for gap in gaps):
        rows.append("Attach daily factor returns and pre-event factor betas before assigning factor contribution.")
    if any("positioning" in gap.lower() for gap in gaps):
        rows.append("Attach volume, short-sale, fails-to-deliver, COT, or other positioning context when relevant.")
    if any("Options-implied" in gap for gap in gaps):
        rows.append("Attach contract-level options data before discussing implied expectations.")
    if idea.peer_metric_readthrough:
        rows.append("Compare attribution classification against peer operating-metric read-through.")
    else:
        rows.append("Add peer operating-metric read-through for the same driver family.")
    if classification in {"Company-specific", "Unexplained"}:
        rows.append("Verify the exact source claim still maps to a material business driver after follow-up evidence.")
    return _dedupe(rows)[:8]


def _attribution_summary(
    classification: str,
    waterfall: AttributionWaterfall | None,
    raw: float | None,
    residual: float | None,
    peer_sympathy: float | None,
    consensus_revision: float | None,
    narrative_label: str,
) -> list[str]:
    rows: list[str] = []
    rows.append(f"Classification: {classification}.")
    if raw is not None:
        rows.append(f"Raw event-window return: {_fmt_pct(raw)}.")
    if waterfall and waterfall.components:
        numeric = [
            component for component in waterfall.components
            if component.contribution_pct is not None
        ]
        context = [
            component for component in waterfall.components
            if component.contribution_pct is None
        ]
        for component in numeric[:3]:
            rows.append(
                f"{component.label}: {_fmt_pct(component.contribution_pct)} contribution "
                f"({component.confidence} confidence)."
            )
        if context:
            rows.append(
                "Context-only components: "
                + ", ".join(component.component_type for component in context[:4])
                + "."
            )
    if residual is not None:
        rows.append(f"Residual company-specific move: {_fmt_pct(residual)}.")
    if peer_sympathy is not None:
        rows.append(f"Curated peer sympathy: {_fmt_pct(peer_sympathy)}.")
    if consensus_revision is not None:
        rows.append(f"Point-in-time consensus revision: {_fmt_pct(consensus_revision)}.")
    if narrative_label != "Unknown":
        rows.append(f"Narrative saturation: {narrative_label}.")
    return _dedupe(rows)[:8]


def _attribution_readiness(
    reaction: EventWindowReaction | None,
    waterfall: AttributionWaterfall | None,
    consensus_revision: float | None,
    gaps: list[str],
) -> str:
    if not reaction:
        return "Missing price reaction"
    if reaction.status != "available":
        return f"Price reaction {reaction.status}"
    has_price_waterfall = bool(waterfall and waterfall.status == "Available" and waterfall.raw_return_pct is not None)
    missing_context = sum(
        1 for gap in gaps
        if any(token in gap.lower() for token in ("consensus", "macro", "factor", "positioning", "options", "narrative"))
    )
    if has_price_waterfall and consensus_revision is not None and missing_context <= 2:
        return "Attribution-ready"
    if has_price_waterfall:
        return "Price-ready / context incomplete"
    return "Partial"


def _attribution_quality_checks(
    reaction: EventWindowReaction | None,
    waterfall: AttributionWaterfall | None,
    idea: TradeIdea,
    event: ChangeEvent,
    raw: float | None,
    market_relative: float | None,
    sector_relative: float | None,
    beta_adjusted: float | None,
    peer_sympathy: float | None,
    consensus_revision: float | None,
    macro: list[ExternalEvidence],
    factor_context: list[FactorExposure],
    positioning_context: list[PositioningSignal],
    liquidity_context: list[LiquiditySignal],
    options_context: list[OptionsExpectation],
    narrative: list[ExternalEvidence],
) -> list[AttributionQualityCheck]:
    return [
        _price_anchor_quality(reaction, raw),
        _market_beta_quality(reaction, market_relative, beta_adjusted),
        _sector_peer_quality(idea, reaction, sector_relative, peer_sympathy),
        _consensus_quality(consensus_revision),
        _macro_factor_quality(macro, factor_context),
        _positioning_options_quality(positioning_context, liquidity_context, options_context),
        _source_evidence_quality(event, idea, waterfall, narrative),
    ]


def _price_anchor_quality(
    reaction: EventWindowReaction | None,
    raw: float | None,
) -> AttributionQualityCheck:
    if reaction and reaction.status == "available" and raw is not None:
        return AttributionQualityCheck(
            "Price anchor",
            "Passed",
            100,
            "Event-window price reaction is available.",
            [
                f"Anchor date {reaction.anchor_date or reaction.event_date or 'unknown'}.",
                f"Price source {reaction.source or 'unknown'}; raw return {_fmt_pct(raw)}.",
            ],
            next_action="Use event-specific anchor date; prefer intraday timestamp when available.",
            stage_impact="Price attribution can support the thesis audit, subject to context checks.",
        )
    if reaction and reaction.status == "window_pending":
        return AttributionQualityCheck(
            "Price anchor",
            "Pending",
            50,
            "Event-window reaction is partially pending.",
            [f"Status {reaction.status}; reason {reaction.reason or 'n/a'}."],
            ["Wait for full 5d/20d windows before final attribution."],
            "Refresh daily bars after the pending window closes.",
            "Pending price windows block final market-capture conclusions.",
        )
    return AttributionQualityCheck(
        "Price anchor",
        "Missing",
        0,
        "No usable event-window price reaction is available.",
        [],
        ["Fetch adjusted daily bars for the stock and benchmarks on the event-specific anchor date."],
        "Run the price-provider stack or import manual daily bars.",
        "Attribution cannot support a thesis without price reaction evidence.",
    )


def _market_beta_quality(
    reaction: EventWindowReaction | None,
    market_relative: float | None,
    beta_adjusted: float | None,
) -> AttributionQualityCheck:
    evidence: list[str] = []
    gaps: list[str] = []
    if market_relative is not None:
        evidence.append(f"Market-relative return {_fmt_pct(market_relative)}.")
    else:
        gaps.append("Market-relative return is missing.")
    if beta_adjusted is not None:
        evidence.append(f"Beta-adjusted return {_fmt_pct(beta_adjusted)}.")
    else:
        gaps.append("Beta-adjusted return is missing.")
    if reaction and reaction.beta is not None:
        evidence.append(f"Pre-event beta {reaction.beta:.2f}.")
    else:
        gaps.append("Pre-event beta with sufficient history is missing.")
    if not gaps:
        status, score = "Passed", 100
    elif evidence:
        status, score = "Partial", 60
    else:
        status, score = "Missing", 0
    return AttributionQualityCheck(
        "Market and beta adjustment",
        status,
        score,
        "Checks whether broad market beta explains the move before assigning company-specific causality.",
        evidence,
        gaps,
        "Estimate beta from pre-event daily returns and compare against SPY/QQQ/IWM/RSP where relevant.",
        "Company-specific attribution is weaker until market/beta adjustment is available.",
    )


def _sector_peer_quality(
    idea: TradeIdea,
    reaction: EventWindowReaction | None,
    sector_relative: float | None,
    peer_sympathy: float | None,
) -> AttributionQualityCheck:
    evidence: list[str] = []
    gaps: list[str] = []
    if sector_relative is not None:
        benchmark = reaction.sector_benchmark_ticker if reaction and reaction.sector_benchmark_ticker else "sector benchmark"
        evidence.append(f"Sector-relative return {_fmt_pct(sector_relative)} versus {benchmark}.")
    else:
        gaps.append("Sector-relative return is missing.")
    if peer_sympathy is not None:
        evidence.append(f"Curated peer sympathy {_fmt_pct(peer_sympathy)}.")
    else:
        gaps.append("Curated peer sympathy return is missing.")
    if idea.peer_metric_readthrough:
        evidence.append(f"{len(idea.peer_metric_readthrough)} peer metric read-through check(s) attached.")
    else:
        gaps.append("Peer operating-metric read-through for the same driver family is missing.")
    if not gaps:
        status, score = "Passed", 100
    elif evidence:
        status, score = "Partial", 60
    else:
        status, score = "Missing", 0
    return AttributionQualityCheck(
        "Sector and peer read-through",
        status,
        score,
        "Checks whether the move was sector-wide or corroborated by peers on the same operating metric.",
        evidence,
        gaps,
        "Add sector ETF/curated peer returns and peer metric read-through aligned to the idea driver.",
        "Sector/peer attribution is contextual until both price and operating-metric peer evidence are aligned.",
    )


def _consensus_quality(consensus_revision: float | None) -> AttributionQualityCheck:
    if consensus_revision is not None:
        return AttributionQualityCheck(
            "Expectations and consensus",
            "Passed",
            100,
            "Point-in-time consensus revision is available for the event.",
            [f"Consensus revision {_fmt_pct(consensus_revision)}."],
            next_action="Compare pre-event and post-event EPS/revenue/target snapshots by provider.",
            stage_impact="Market-capture classification can use official consensus if source status is valid.",
        )
    return AttributionQualityCheck(
        "Expectations and consensus",
        "Missing",
        0,
        "Point-in-time consensus revision is missing.",
        [],
        ["Seed official or manual consensus snapshots observed before and after the event."],
        "Import consensus CSV rows or fetch provider snapshots before assessing whether the move was priced in.",
        "Missing consensus blocks definitive market-capture and uncaptured-evidence claims.",
    )


def _macro_factor_quality(
    macro: list[ExternalEvidence],
    factor_context: list[FactorExposure],
) -> AttributionQualityCheck:
    evidence: list[str] = []
    gaps: list[str] = []
    if macro:
        evidence.append(f"{len(macro)} lookahead-safe macro observation(s) available.")
    else:
        gaps.append("Lookahead-safe macro observations are missing.")
    if factor_context:
        evidence.append(f"{len(factor_context)} factor context observation(s) available.")
    else:
        gaps.append("Point-in-time factor context is missing.")
    if macro and factor_context:
        status, score = "Passed", 90
    elif evidence:
        status, score = "Context only", 55
    else:
        status, score = "Missing", 0
    return AttributionQualityCheck(
        "Macro and factor context",
        status,
        score,
        "Checks whether macro calendar or style factors provide a plausible non-company explanation.",
        evidence,
        gaps,
        "Attach FRED/BLS/BEA/Treasury/Ken French context released on or before the event date.",
        "Macro/factor signals are context only and cannot independently create High-Conviction ideas.",
    )


def _positioning_options_quality(
    positioning_context: list[PositioningSignal],
    liquidity_context: list[LiquiditySignal],
    options_context: list[OptionsExpectation],
) -> AttributionQualityCheck:
    evidence: list[str] = []
    gaps: list[str] = []
    if positioning_context:
        evidence.append(f"{len(positioning_context)} positioning signal(s) available.")
    else:
        gaps.append("Short-sale, FTD, COT, or other positioning context is missing.")
    if liquidity_context:
        evidence.append(f"{len(liquidity_context)} liquidity signal(s) available.")
    else:
        gaps.append("Volume/liquidity context is missing.")
    available_options = [item for item in options_context if item.status != "Unavailable"]
    if available_options:
        evidence.append(f"{len(available_options)} options expectation signal(s) available.")
    else:
        gaps.append("Contract-level options-implied move is unavailable; do not infer options expectations.")
    if available_options and (positioning_context or liquidity_context):
        status, score = "Passed", 85
    elif evidence:
        status, score = "Context only", 50
    else:
        status, score = "Missing", 0
    return AttributionQualityCheck(
        "Positioning, liquidity, and options",
        status,
        score,
        "Checks whether trading pressure, liquidity, or implied expectations could explain the move.",
        evidence,
        gaps,
        "Add volume shock, short-sale/FTD, and contract-level options data when relevant.",
        "These signals explain market mechanics only; they cannot prove fundamental causality.",
    )


def _source_evidence_quality(
    event: ChangeEvent,
    idea: TradeIdea,
    waterfall: AttributionWaterfall | None,
    narrative: list[ExternalEvidence],
) -> AttributionQualityCheck:
    evidence: list[str] = []
    gaps: list[str] = []
    has_tier1 = any((citation.source_tier or 4) <= 1 for citation in event.citations)
    if has_tier1:
        evidence.append("Tier 1 source citation is attached to the event.")
    elif event.citations:
        evidence.append("Source citation is attached, but not Tier 1.")
        gaps.append("Tier 1 issuer/SEC/regulator evidence is missing.")
    else:
        gaps.append("Source citation is missing.")
    driver = str(event.metrics.get("economic_driver") or idea.driver_template_summary or idea.bridge_status or "").strip()
    if driver and driver != "Unmapped":
        evidence.append(f"Event maps to driver: {driver}.")
    else:
        gaps.append("Event is not mapped to a material business driver.")
    if waterfall and waterfall.residual_pct is not None:
        evidence.append(f"Residual check available: {_fmt_pct(waterfall.residual_pct)}.")
    else:
        gaps.append("Residual check is unavailable.")
    if narrative:
        evidence.append("Narrative context is present but supporting only.")
    if not gaps:
        status, score = "Passed", 100
    elif evidence:
        status, score = "Partial", 55
    else:
        status, score = "Missing", 0
    return AttributionQualityCheck(
        "Source evidence and driver corroboration",
        status,
        score,
        "Checks whether attribution is tied back to source evidence, not just returns.",
        evidence,
        gaps,
        "Validate the exact source claim, driver mapping, and residual explanation before using attribution as thesis support.",
        "Attribution cannot support Research-Ready or High-Conviction status without source-backed driver evidence.",
    )


def _quality_score(checks: list[AttributionQualityCheck]) -> int:
    if not checks:
        return 0
    return round(mean(check.score for check in checks))


def _residual_explanation(residual: float | None, raw: float | None) -> str:
    if raw is None:
        return "No price move is available, so residual attribution cannot be estimated."
    if residual is None:
        return "Residual move is unavailable because benchmark or beta-adjusted returns are missing."
    if abs(residual) < 1:
        return "Most of the move can be explained by market, sector, or beta context."
    return "A meaningful residual move remains after available market, sector, or beta adjustments."


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _dedupe(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            rows.append(text)
            seen.add(text)
    return rows
