from __future__ import annotations

from .analysis import format_number
from .models import (
    BullBearJudgePanel,
    Citation,
    ClaimValidationResult,
    CompanyEconomics,
    CreditLens,
    DemoCase,
    EntityResolution,
    EvidenceDrawer,
    EvidenceWorkOrder,
    FinancialCoverage,
    FormulaTrace,
    ICOnePager,
    JudgeResolutionItem,
    MarketCaptureReadiness,
    MarketImpliedExpectations,
    EarningsSurpriseProxy,
    RecentMarketContext,
    MetricResolutionAudit,
    PipelineStage,
    ResearchRunProgress,
    StoryCard,
    ThesisBrief,
    ThesisCritique,
    ThesisValidationReport,
    TradeIdea,
    ValuationResult,
)


DEMO_CASES: tuple[DemoCase, ...] = (
    DemoCase(
        demo_id="aapl-source-backed-thesis",
        ticker="AAPL",
        title="AAPL: Source-backed thesis",
        lesson="Follow a filing-backed margin and cash-flow signal through claim validation, a causal bridge, market-implied expectations, and monitor rules.",
        screenshot_focus="Story-first IC verdict, formula traces, and evidence drawers.",
        content_version="Adaptive IC 2026.07",
        refreshed_at="2026-07-15",
    ),
    DemoCase(
        demo_id="nvda-neutral-first-investment-cycle",
        ticker="NVDA",
        title="NVDA: Neutral-first investment-cycle audit",
        lesson="Investigate capex and acquisition-related goodwill with dual hypotheses, semiconductor peer metrics, and explicit evidence work orders before choosing direction.",
        screenshot_focus="Neutral-first anomaly cards, causal thesis graph, and TSM/AMD operating read-through.",
        content_version="Adaptive IC 2026.07",
        refreshed_at="2026-07-15",
    ),
    DemoCase(
        demo_id="baba-adr-fpi-complexity",
        ticker="BABA",
        title="BABA: ADR/FPI complexity",
        lesson="Trace China commerce, cloud, buybacks, ADR normalization, 20-F/6-K evidence, and price-only market capture without overstating consensus history.",
        screenshot_focus="ADR/FPI evidence closure, segment drivers, and China peer context.",
        content_version="Adaptive IC 2026.07",
        refreshed_at="2026-07-15",
    ),
    DemoCase(
        demo_id="tsla-peer-metric-readthrough",
        ticker="TSLA",
        title="TSLA: Peer metric read-through",
        lesson="Compare deliveries, automotive economics, and gross-margin evidence across GM and BYD rather than relying on stock sympathy alone.",
        screenshot_focus="Driver-specific peer metric read-through and causal bridge.",
        content_version="Adaptive IC 2026.07",
        refreshed_at="2026-07-15",
    ),
    DemoCase(
        demo_id="gs-financial-sector-playbook",
        ticker="GS",
        title="GS: Financial-sector playbook",
        lesson="Use a broker/bank playbook to connect investment-banking fees, trading, ROTCE, CET1, and provisions to equity and credit conclusions.",
        screenshot_focus="Financial-sector KPI bridge and MS/JPM read-through.",
        content_version="Adaptive IC 2026.07",
        refreshed_at="2026-07-15",
    ),
    DemoCase(
        demo_id="spcx-spxc-entity-resolution",
        ticker="SPCX",
        title="SPCX/SPXC: Entity-resolution warning",
        lesson="Stop the workflow before analysis when a similar ticker, registration-only history, or unsupported entity prevents reliable financial mapping.",
        screenshot_focus="Entity warning, coverage diagnosis, and executable next action.",
        content_version="Adaptive IC 2026.07",
        refreshed_at="2026-07-15",
    ),
)


def demo_cases() -> list[DemoCase]:
    return list(DEMO_CASES)


def demo_case_for(ticker: str) -> DemoCase | None:
    normalized = ticker.upper().strip()
    if normalized == "SPXC":
        normalized = "SPCX"
    return next((case for case in DEMO_CASES if case.ticker == normalized), None)


def build_story_presentation(
    *,
    identity,
    ideas: list[TradeIdea],
    one_pager: ICOnePager,
    thesis_brief: ThesisBrief,
    thesis_critique: ThesisCritique,
    validation: ThesisValidationReport,
    validated_claims: ClaimValidationResult,
    economics: CompanyEconomics,
    credit_lens: CreditLens,
    valuation: ValuationResult,
    market_capture: MarketCaptureReadiness,
    work_order: EvidenceWorkOrder,
    metric_audit: MetricResolutionAudit,
    entity_resolution: EntityResolution,
    financial_coverage: FinancialCoverage,
    demo_case: DemoCase | None = None,
    market_implied: MarketImpliedExpectations | None = None,
    earnings_surprise: EarningsSurpriseProxy | None = None,
    recent_market_context: RecentMarketContext | None = None,
) -> dict:
    top_idea = ideas[0] if ideas else None
    story_cards = build_story_cards(
        top_idea,
        one_pager,
        thesis_critique,
        validation,
        work_order,
        metric_audit,
    )
    market_cards = _market_expectation_story_cards(
        market_implied,
        earnings_surprise,
        recent_market_context,
    )
    story_cards[6:6] = market_cards
    return {
        "demo_case": demo_case,
        "run_progress": build_run_progress(
            entity_resolution,
            financial_coverage,
            validated_claims,
            economics,
            valuation,
            market_capture,
            thesis_brief,
            top_idea,
            work_order,
        ),
        "story_cards": story_cards,
        "bull_bear_judge": build_bull_bear_judge(
            top_idea,
            thesis_brief,
            thesis_critique,
            validation,
            validated_claims,
            work_order,
        ),
        "formula_traces": build_formula_traces(ideas, valuation, metric_audit),
    }


def _market_expectation_story_cards(
    market_implied: MarketImpliedExpectations | None,
    earnings_surprise: EarningsSurpriseProxy | None,
    recent_market_context: RecentMarketContext | None,
) -> list[StoryCard]:
    cards: list[StoryCard] = []
    if earnings_surprise:
        detail = earnings_surprise.methodology
        if earnings_surprise.data_gaps:
            detail += f" Limitation: {earnings_surprise.data_gaps[0]}"
        cards.append(StoryCard(
            "earnings_surprise",
            "Earnings vs Expectations",
            earnings_surprise.status,
            earnings_surprise.headline,
            detail,
            next_action=(
                "Store daily point-in-time estimate snapshots to measure subsequent revision follow-through."
                if not earnings_surprise.revision_follow_through_available else ""
            ),
        ))
    if market_implied:
        available = [row for row in market_implied.expectations if row.implied_value is not None]
        values = "; ".join(
            f"{row.metric}: {row.implied_value:,.2f} {row.unit} ({row.confidence})"
            for row in available[:3]
        )
        cards.append(StoryCard(
            "market_implied",
            "What Price Appears to Assume",
            market_implied.status,
            values or market_implied.summary,
            (
                f"{market_implied.summary} Price source: {market_implied.price_source or 'Unknown'} "
                f"as of {market_implied.price_as_of or 'Unknown'}; financial basis: {market_implied.financial_basis}."
            ),
            next_action=(
                "Stress the reverse model against operating history, peer economics, and explicit bull/base/bear assumptions."
                if available else "Normalize the missing price, share-count, cash-flow, and balance-sheet inputs."
            ),
        ))
    if recent_market_context and recent_market_context.thesis_implications:
        cards.append(StoryCard(
            "recent_market_context",
            "Recent Price Context",
            recent_market_context.status,
            recent_market_context.thesis_implications[0],
            recent_market_context.summary,
            next_action="Test whether source-backed fundamentals explain the relative move; price context alone does not establish causality.",
        ))
    return cards


def empty_run_progress() -> ResearchRunProgress:
    return ResearchRunProgress("Unavailable", "Presentation progress has not been built.", [])


def empty_bull_bear_judge() -> BullBearJudgePanel:
    return BullBearJudgePanel(
        status="Unavailable",
        bull_case="No thesis has been selected.",
        bear_case="No counter-thesis has been evaluated.",
    )


def build_run_progress(
    entity: EntityResolution,
    coverage: FinancialCoverage,
    claims: ClaimValidationResult,
    economics: CompanyEconomics,
    valuation: ValuationResult,
    capture: MarketCaptureReadiness,
    brief: ThesisBrief,
    top_idea: TradeIdea | None,
    work_order: EvidenceWorkOrder,
) -> ResearchRunProgress:
    substantive_claims = [claim for claim in claims.claims if claim.is_substantive and not claim.not_thesis_grade_reason]
    peer_summary = top_idea.peer_metric_summary if top_idea else None
    has_monitor = bool(top_idea and top_idea.monitor_items)
    stages = [
        PipelineStage(
            "entity",
            "Entity",
            "Partial" if entity.warning else "Passed" if entity.ticker and entity.name else "Blocked",
            entity.warning or f"Resolved {entity.ticker} to {entity.name}.",
            evidence=[f"CIK: {entity.cik or 'Unknown'}", f"Forms: {', '.join(entity.reporting_forms) or 'Unknown'}"],
            blockers=[entity.warning] if entity.warning else [],
            next_action="Resolve similar ticker warning before relying on the run." if entity.warning else "",
        ),
        PipelineStage(
            "sources",
            "Sources",
            "Passed" if coverage.status == "available" else "Partial" if coverage.metrics_count else "Blocked",
            coverage.reason,
            evidence=[f"Coverage: {coverage.status}", f"Metrics: {coverage.metrics_count}"],
            blockers=[] if coverage.status == "available" else [coverage.reason],
            next_action="Fetch registered issuer/filing sources or use manual imports for missing structured data.",
        ),
        PipelineStage(
            "claims",
            "Claims",
            "Passed" if substantive_claims else "Partial" if claims.claims else "Blocked",
            f"{len(substantive_claims)} thesis-grade claim(s) from {len(claims.claims)} validated claim(s).",
            evidence=[claim.changed_text or claim.supporting_quote or claim.event_title for claim in substantive_claims[:3]],
            blockers=claims.data_gaps[:3],
            next_action="Validate exact changed text and direction before promoting a thesis.",
        ),
        PipelineStage(
            "drivers",
            "Drivers",
            "Passed" if economics.material_driver_count else "Partial" if economics.drivers else "Blocked",
            economics.business_model or "Company economics playbook unavailable.",
            evidence=[driver.name for driver in economics.drivers[:4]],
            blockers=economics.data_gaps[:3],
            next_action="Map source signal to a material business or industry driver.",
        ),
        PipelineStage(
            "peers",
            "Peer Read-through",
            _peer_stage_status(peer_summary),
            peer_summary.summary if peer_summary else "No peer metric read-through summary is attached.",
            evidence=(peer_summary.confirmations[:3] if peer_summary else []),
            blockers=(peer_summary.data_gaps[:3] if peer_summary else ["Peer metric read-through unavailable."]),
            next_action=(peer_summary.next_actions[0] if peer_summary and peer_summary.next_actions else "Collect aligned peer operating metrics."),
        ),
        PipelineStage(
            "valuation",
            "Valuation",
            "Passed" if valuation.cases and not valuation.missing_data else "Partial" if valuation.cases else "Blocked",
            valuation.methodology or valuation.status,
            evidence=[f"{case.name}: {_format_value(case.fair_value, valuation.currency)}" for case in valuation.cases[:3]],
            blockers=valuation.missing_data[:3],
            next_action="Add explicit entry, exit, operating, and multiple assumptions for payoff-ready ideas.",
        ),
        PipelineStage(
            "attribution",
            "Attribution",
            "Passed" if top_idea and top_idea.driver_attribution else "Partial" if capture.price_only_ideas else "Blocked",
            _attribution_summary(top_idea, capture.summary),
            evidence=[capture.price_coverage, capture.consensus_coverage],
            blockers=capture.data_gaps[:3],
            next_action="Use price-only attribution unless point-in-time consensus snapshots exist.",
        ),
        PipelineStage(
            "thesis",
            "Thesis",
            "Passed" if "convincing" in brief.verdict.lower() else "Partial" if top_idea else "Blocked",
            brief.verdict,
            evidence=brief.evidence_chain[:3],
            blockers=brief.data_gaps[:3],
            next_action=work_order.items[0].action if work_order.items else "No thesis work order is open.",
        ),
        PipelineStage(
            "monitor",
            "Monitor",
            "Passed" if has_monitor else "Partial",
            f"{len(top_idea.monitor_items) if top_idea else 0} monitor rule(s) attached.",
            evidence=[item.criterion for item in top_idea.monitor_items[:3]] if top_idea else [],
            blockers=[] if has_monitor else ["No machine-readable monitor rule attached to the top idea."],
            next_action="Add confirmation and break criteria with metric, threshold, source, and cadence.",
        ),
    ]
    if any(stage.status == "Blocked" for stage in stages):
        status = "Blocked"
    elif any(stage.status == "Partial" for stage in stages):
        status = "Partial"
    else:
        status = "Passed"
    return ResearchRunProgress(status, f"{sum(1 for stage in stages if stage.status == 'Passed')}/{len(stages)} stages passed.", stages)


def build_story_cards(
    top_idea: TradeIdea | None,
    one_pager: ICOnePager,
    critique: ThesisCritique,
    validation: ThesisValidationReport,
    work_order: EvidenceWorkOrder,
    metric_audit: MetricResolutionAudit,
) -> list[StoryCard]:
    event = top_idea.source_events[0] if top_idea and top_idea.source_events else None
    primary_drawers = _event_drawers(event, metric_audit)
    next_action = one_pager.next_best_action or (work_order.items[0].action if work_order.items else "")
    contextual = event.metrics.get("contextual_disclosure_comparison") if event else {}
    contextual = contextual if isinstance(contextual, dict) else {}
    exact_change = str(contextual.get("semantic_shift") or (event.title if event else "No source change selected."))
    comparison_note = (
        f"{contextual.get('comparison_type', 'comparison')} versus "
        f"{contextual.get('prior_source') or 'prior source'} ({contextual.get('prior_period') or 'period unknown'})."
        if contextual else (event.summary if event else "The run did not identify a top source change.")
    )
    return [
        StoryCard(
            "what_changed",
            "What Changed",
            "Available" if event else "Unavailable",
            exact_change,
            comparison_note,
            evidence=primary_drawers,
        ),
        StoryCard(
            "why_it_matters",
            "Why It Matters",
            "Available" if top_idea else "Unavailable",
            top_idea.driver_template_summary or top_idea.direction_rationale if top_idea else "No driver mapped.",
            event.why_this_matters if event and getattr(event, "why_this_matters", "") else one_pager.why_now,
            next_action=top_idea.next_source_to_check if top_idea else next_action,
            evidence=primary_drawers,
        ),
        StoryCard("causal_bridge", "Causal Bridge", "Available", one_pager.causal_bridge, one_pager.causal_bridge, evidence=primary_drawers),
        StoryCard("equity_lens", "Equity Lens", "Available", "Equity implication", one_pager.equity_lens, evidence=primary_drawers),
        StoryCard("credit_lens", "Credit Lens", "Available", "Credit implication", one_pager.credit_lens, evidence=primary_drawers),
        StoryCard("price_market", "Price / Market Context", "Available", one_pager.price_move, one_pager.market_capture),
        StoryCard(
            "counter_thesis",
            "Counter-Thesis",
            "Available",
            one_pager.counter_thesis or critique.strongest_counter_thesis,
            one_pager.counter_thesis or critique.strongest_counter_thesis,
            next_action="Resolve contradictions before calling the thesis high conviction." if validation.strongest_contradictions else "",
        ),
        StoryCard(
            "next_action",
            "Next Action",
            "Open" if next_action else "Unavailable",
            next_action or "No next action attached.",
            "; ".join(one_pager.work_order_actions[:3] or critique.missing_evidence[:3] or [next_action or "No action attached."]),
            next_action=next_action,
        ),
    ]


def build_bull_bear_judge(
    top_idea: TradeIdea | None,
    brief: ThesisBrief,
    critique: ThesisCritique,
    validation: ThesisValidationReport,
    claims: ClaimValidationResult,
    work_order: EvidenceWorkOrder,
) -> BullBearJudgePanel:
    accepted_claims = [
        claim.changed_text or claim.supporting_quote or claim.event_title
        for claim in claims.claims
        if claim.is_substantive and claim.citation and not claim.not_thesis_grade_reason
    ]
    resolution_plan = _judge_resolution_plan(top_idea, validation, work_order)
    unproven = [
        (
            f"{item.issue} Next: {item.app_action}"
            if item.app_action else item.issue
        )
        for item in resolution_plan
        if item.status not in {"Resolved", "Informational"}
    ]
    if not unproven:
        unproven = _dedupe(
            item for item in (
                critique.missing_evidence + validation.required_next_evidence
            )
            if not _is_ranking_only_gap(item)
        )[:4]
    bull = brief.thesis if top_idea and top_idea.direction.lower() != "short" else "Bull case requires evidence that the bearish signal is temporary, already priced, or offset by stronger fundamentals."
    bear = critique.strongest_counter_thesis or (validation.strongest_contradictions[0] if validation.strongest_contradictions else "No counter-thesis has been accepted yet.")
    return BullBearJudgePanel(
        status="Available",
        bull_case=bull,
        bear_case=bear,
        judge_accepts=_dedupe(accepted_claims + brief.evidence_chain)[:6],
        still_unproven=_dedupe(unproven)[:8],
        resolution_plan=_visible_resolution_plan(resolution_plan),
    )


def _judge_resolution_plan(
    top_idea: TradeIdea | None,
    validation: ThesisValidationReport,
    work_order: EvidenceWorkOrder,
) -> list[JudgeResolutionItem]:
    rows: list[JudgeResolutionItem] = []
    for check in validation.checks:
        if check.status != "Contradicts":
            continue
        action = next(
            (
                item.action for item in validation.next_evidence_actions
                if item.channel == check.channel
            ),
            check.gaps[0] if check.gaps else "Fetch or recompute the evidence needed to test this contradiction.",
        )
        rows.append(JudgeResolutionItem(
            issue_type="Evidence contradiction",
            status="Open",
            issue=f"{check.channel} conflicts with the proposed thesis direction.",
            evidence=check.evidence,
            app_action=action,
            user_action="Review the cited conflict only if the automated source refresh remains inconclusive.",
            blocking_scope="High-Conviction",
            auto_resolvable=True,
        ))

    seen_actions = {item.app_action for item in rows}
    for item in work_order.items:
        if not (item.blocks_high_conviction or item.blocks_research_ready):
            continue
        if item.action in seen_actions:
            continue
        source_type = (item.source_type or "").lower()
        auto_resolvable = source_type not in {
            "manual", "manual_csv", "consensus_manual", "user_assumption",
        }
        rows.append(JudgeResolutionItem(
            issue_type="Evidence gap",
            status=item.status or "Open",
            issue=item.why_it_matters or item.action,
            evidence=item.expected_output,
            app_action=item.action,
            user_action=(
                "No manual action yet; the app should attempt the registered source adapter first."
                if auto_resolvable else
                "Import or enter this licensed/manual input because no registered automatic source can supply it."
            ),
            blocking_scope=(
                "Research-Ready and High-Conviction"
                if item.blocks_research_ready else "High-Conviction"
            ),
            auto_resolvable=auto_resolvable,
        ))
        seen_actions.add(item.action)

    if top_idea:
        probability = top_idea.probability_provenance
        probability_status = probability.status if probability else "Uncalibrated"
        if probability_status != "Calibrated":
            rows.append(JudgeResolutionItem(
                issue_type="Ranking limitation",
                status="Informational",
                issue=(
                    "Scenario probabilities are illustrative, so expected value is shown for analysis "
                    "but excluded from cross-idea ranking."
                ),
                evidence=f"Probability source: {probability_status}.",
                app_action=(
                    "Freeze Research-Ready forecasts and record resolved outcomes until the signal-family "
                    "calibration threshold is met."
                ),
                user_action="Review scenario payoffs now; do not interpret illustrative EV as a forecast hit rate.",
                blocking_scope="EV ranking only",
                auto_resolvable=True,
            ))
    return _dedupe_resolution_items(rows)


def _dedupe_resolution_items(items: list[JudgeResolutionItem]) -> list[JudgeResolutionItem]:
    rows: list[JudgeResolutionItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.issue_type, item.issue)
        if key in seen:
            continue
        seen.add(key)
        rows.append(item)
    return rows


def _visible_resolution_plan(items: list[JudgeResolutionItem], limit: int = 10) -> list[JudgeResolutionItem]:
    blocking = [item for item in items if item.blocking_scope != "EV ranking only"]
    ranking = [item for item in items if item.blocking_scope == "EV ranking only"]
    if not ranking:
        return blocking[:limit]
    return blocking[:max(0, limit - 1)] + ranking[:1]


def _is_ranking_only_gap(value: str) -> bool:
    lower = str(value).lower()
    return "ev ranking" in lower or (
        "probabil" in lower and any(token in lower for token in ("uncalibrated", "illustrative", "ranking"))
    )


def build_formula_traces(
    ideas: list[TradeIdea],
    valuation: ValuationResult,
    metric_audit: MetricResolutionAudit,
) -> list[FormulaTrace]:
    traces: list[FormulaTrace] = []
    for item in metric_audit.items[:12]:
        traces.append(FormulaTrace(
            trace_id=f"metric-{item.metric}",
            label=item.metric,
            value=_format_value(item.value, item.currency or item.unit or ""),
            source_field=item.source_metric or item.metric,
            formula=item.formula or item.resolution_method or "Direct source value",
            period=item.period_end,
            currency=item.currency or item.unit,
            confidence=item.status,
            source=item.source_type,
            citation=item.citation,
        ))
    for step in valuation.bridge[:12]:
        traces.append(FormulaTrace(
            trace_id=f"valuation-{step.case}-{step.metric}",
            label=f"{step.case} {step.metric}",
            value=_format_value(step.value, step.unit),
            source_field=step.metric,
            formula=step.formula or "Valuation bridge input",
            currency=valuation.currency,
            confidence=valuation.confidence,
            source=step.source,
        ))
    if ideas:
        payoff = ideas[0].payoff_model
        if payoff:
            for assumption in payoff.assumptions[:8]:
                traces.append(FormulaTrace(
                    trace_id=f"payoff-{assumption.case}-{assumption.metric}",
                    label=f"{assumption.case} {assumption.metric}",
                    value=_format_value(assumption.value, assumption.unit),
                    source_field=assumption.metric,
                    formula=assumption.formula or "User or model scenario assumption",
                    currency=payoff.currency,
                    confidence=assumption.source,
                    source=assumption.source,
                ))
            for scenario in payoff.scenarios[:3]:
                direction = ideas[0].direction
                traces.append(FormulaTrace(
                    trace_id=f"scenario-{scenario.name}",
                    label=f"{scenario.name} position return",
                    value=_format_value(scenario.net_return_pct, "%"),
                    source_field="entry_value, exit_value, dividends, costs",
                    formula=_scenario_formula(direction),
                    currency=scenario.currency or payoff.currency,
                    confidence=scenario.probability_status,
                    source=payoff.status,
                ))
    return traces


def _scenario_formula(direction: str) -> str:
    if direction == "Short":
        return "Stock move: (exit - entry) / entry. Short position return: (entry - exit) / entry - borrow cost - dividends - transaction costs."
    if direction == "Relative Value":
        return "Pair position return: long return - hedge ratio x short return - transaction costs."
    return "Long position return: (exit - entry) / entry + dividends - transaction costs."


def _peer_stage_status(summary) -> str:
    if not summary:
        return "Blocked"
    if summary.operating_metric_peers:
        return "Passed" if not summary.data_gaps else "Partial"
    if summary.price_only_peers:
        return "Partial"
    return "Blocked"


def _attribution_summary(top_idea: TradeIdea | None, fallback: str) -> str:
    attribution = top_idea.driver_attribution if top_idea else None
    if not attribution:
        return fallback
    if getattr(attribution, "headline", ""):
        return attribution.headline
    if getattr(attribution, "attribution_summary", None):
        return "; ".join(attribution.attribution_summary[:2])
    return fallback


def _event_drawers(event, metric_audit: MetricResolutionAudit) -> list[EvidenceDrawer]:
    if not event:
        return []
    drawers = [_drawer_from_citation(citation, event.title, parser_status="source-linked") for citation in event.citations[:3]]
    metrics = event.metrics or {}
    if metrics.get("signal_method") == "disclosure_change_engine" or "disclosure_comparison" in metrics:
        drawers.extend(_disclosure_comparison_drawers(event))
    elif "current_mentions" in metrics or "previous_mentions" in metrics:
        drawers.extend(_text_signal_drawers(event))
    metadata_keys = {
        "signal_method", "comparison_status", "current_mentions", "previous_mentions",
        "current_form", "current_accession", "current_filing_date", "current_period",
        "previous_form", "previous_accession", "previous_filing_date", "previous_period",
        "comparison_reason_code", "disclosure_event_type", "disclosure_comparison",
        "current_section", "current_section_key", "prior_form", "prior_accession",
        "prior_filing_date", "prior_period", "prior_section", "prior_section_key",
        "prior_mentions", "current_word_count", "prior_word_count",
        "current_mentions_per_1000_words", "prior_mentions_per_1000_words",
        "mention_rate_delta", "section_length_change_pct", "semantic_similarity",
        "topic_drift_score", "added_sentence_count", "removed_sentence_count",
        "materiality_score", "investment_relevance", "interpretation",
        "research_work_order", "notes", "confidence", "comparison_type",
    }
    for key, value in [(key, value) for key, value in metrics.items() if key not in metadata_keys][:4]:
        metric_item = next((item for item in metric_audit.items if item.metric.lower() == str(key).replace("_", " ").lower()), None)
        drawers.append(EvidenceDrawer(
            label=str(key).replace("_", " ").title(),
            claim=f"{str(key).replace('_', ' ').title()}: {value}",
            metric=str(key),
            value=str(value),
            formula=metric_item.formula if metric_item else "Source event metric",
            period=metric_item.period_end if metric_item else event.event_date,
            unit=metric_item.unit if metric_item else "",
            currency=metric_item.currency if metric_item else "",
            confidence=metric_item.status if metric_item else "Event-derived",
            parser_status=metric_item.resolution_method if metric_item else "event_metric",
            source=event.source,
        ))
    return drawers


def _disclosure_comparison_drawers(event) -> list[EvidenceDrawer]:
    metrics = event.metrics or {}
    comparison = metrics.get("disclosure_comparison") if isinstance(metrics.get("disclosure_comparison"), dict) else metrics
    status = str(comparison.get("comparison_status") or metrics.get("comparison_status") or "unknown")
    reason = str(comparison.get("reason_code") or metrics.get("comparison_reason_code") or "unknown")
    event_type = str(comparison.get("comparison_type") or metrics.get("disclosure_event_type") or "Observation")
    current_rate = comparison.get("current_mentions_per_1000_words")
    prior_rate = comparison.get("prior_mentions_per_1000_words")
    rate_value = (
        f"{float(current_rate):.2f} / 1k words"
        if isinstance(current_rate, (int, float))
        else "Unavailable"
    )
    prior_value = (
        f"{float(prior_rate):.2f} / 1k words"
        if isinstance(prior_rate, (int, float))
        else "No comparable prior rate"
    )
    diagnostics = [
        f"Status: {status}",
        f"Reason: {reason}",
        f"Type: {event_type}",
        f"Materiality: {comparison.get('materiality_score', 'n/a')}/100",
        f"Relevance: {comparison.get('investment_relevance', 'n/a')}",
    ]
    rows = [
        EvidenceDrawer(
            label="Disclosure comparison",
            claim=str(comparison.get("interpretation") or event.summary),
            source=str(comparison.get("current_form") or event.source),
            accession=str(comparison.get("current_accession") or ""),
            section=str(comparison.get("current_section") or event.category.replace("_", " ").title()),
            period=str(comparison.get("current_period") or event.event_date or ""),
            metric="normalized_disclosure_intensity",
            value=rate_value,
            formula=(
                "Section-aligned mentions per 1,000 words, section-length change, "
                "semantic similarity, sentence additions/removals, and topic drift."
            ),
            confidence=str(comparison.get("confidence") or status),
            parser_status="disclosure_change_engine",
            excerpt=" | ".join(diagnostics),
        ),
        EvidenceDrawer(
            label="Prior comparison source",
            claim=(
                "Comparable prior section aligned."
                if prior_rate is not None else
                "No comparable prior section was established; do not treat missing prior text as zero."
            ),
            source=" ".join(
                str(part) for part in (
                    comparison.get("prior_form"),
                    comparison.get("prior_filing_date"),
                ) if part
            ) or "No comparable prior source",
            accession=str(comparison.get("prior_accession") or ""),
            section=str(comparison.get("prior_section") or "Not aligned"),
            period=str(comparison.get("prior_period") or "No comparable prior period"),
            metric="prior_normalized_disclosure_intensity",
            value=prior_value,
            formula="Prior rate is populated only after a distinct same-form, earlier-period section or topic window is aligned.",
            confidence=str(comparison.get("confidence") or status),
            parser_status=status,
        ),
    ]
    audit = comparison.get("prior_context_audit")
    if isinstance(audit, dict):
        attempted = ", ".join(str(item) for item in audit.get("sources_attempted", [])) or "No source attempt recorded"
        fallbacks = ", ".join(str(item) for item in audit.get("fallback_source_types", [])) or "None"
        rows.append(EvidenceDrawer(
            label="Prior context audit",
            claim=(
                f"Prior-context status: {audit.get('status', 'unknown')}. "
                f"Zero mentions valid: {'yes' if audit.get('zero_mentions_is_valid') else 'no'}. "
                f"Blocker: {audit.get('blocker') or audit.get('discovery_error') or 'None'}."
            ),
            source=attempted,
            accession=str(audit.get("selected_accession") or ""),
            section=str(comparison.get("prior_section") or "Not aligned"),
            period=str(comparison.get("prior_period") or "No comparable prior period"),
            metric="prior_context_status",
            value=str(audit.get("status") or "unknown"),
            formula=(
                "A zero baseline is valid only after a distinct earlier filing is loaded, parsed, "
                "period-checked, and its comparable section is searched. Fallback sources: " + fallbacks
            ),
            confidence="Audited" if audit.get("llm_comparison_ready") else "Needs source recovery",
            parser_status="prior_context_audit",
        ))
    current_excerpt = str(comparison.get("current_excerpt") or "")
    prior_excerpt = str(comparison.get("prior_excerpt") or "")
    changed_phrases = comparison.get("changed_phrases") if isinstance(comparison.get("changed_phrases"), list) else []
    if current_excerpt:
        rows.append(EvidenceDrawer(
            label="Current disclosure excerpt",
            claim=current_excerpt,
            source=str(comparison.get("current_form") or event.source),
            url=str(comparison.get("current_url") or ""),
            accession=str(comparison.get("current_accession") or ""),
            section=str(comparison.get("current_section") or ""),
            period=str(comparison.get("current_period") or ""),
            metric="current_disclosure_excerpt",
            value=str(comparison.get("alignment_type") or status),
            formula="Exact retrieved excerpt selected from the current topic-aligned section.",
            confidence=str(comparison.get("confidence") or status),
            parser_status="source_linked_excerpt",
            excerpt=current_excerpt,
        ))
    if prior_excerpt:
        rows.append(EvidenceDrawer(
            label="Prior disclosure excerpt",
            claim=prior_excerpt,
            source=str(comparison.get("prior_form") or "Prior source"),
            url=str(comparison.get("prior_url") or ""),
            accession=str(comparison.get("prior_accession") or ""),
            section=str(comparison.get("prior_section") or ""),
            period=str(comparison.get("prior_period") or ""),
            metric="prior_disclosure_excerpt",
            value=str(comparison.get("alignment_type") or status),
            formula="Exact retrieved excerpt selected from a distinct earlier source and period.",
            confidence=str(comparison.get("confidence") or status),
            parser_status="source_linked_prior_excerpt",
            excerpt=prior_excerpt,
        ))
    if changed_phrases:
        rows.append(EvidenceDrawer(
            label="Changed language",
            claim=" | ".join(str(item) for item in changed_phrases[:3]),
            source="Deterministic disclosure comparison",
            section=str(comparison.get("current_section") or event.category.replace("_", " ").title()),
            period=f"{comparison.get('prior_period') or 'Unknown'} to {comparison.get('current_period') or 'Unknown'}",
            metric="changed_phrases",
            value=f"{len(changed_phrases)} phrase pair(s)",
            formula="Keyword-relevant added and removed sentences paired by textual similarity.",
            confidence=str(comparison.get("confidence") or status),
            parser_status="deterministic_sentence_diff",
        ))
    contextual = metrics.get("contextual_disclosure_comparison")
    if isinstance(contextual, dict) and contextual.get("comparison_type") == "cross_source_context":
        rows.append(EvidenceDrawer(
            label="Management intent comparison",
            claim=str(contextual.get("semantic_shift") or "No semantic shift isolated."),
            source=f"{contextual.get('prior_source') or 'Prior source'} -> {contextual.get('current_source') or event.source}",
            period=f"{contextual.get('prior_period') or 'Unknown'} to {contextual.get('current_period') or 'Unknown'}",
            metric="management_intent_shift",
            value=str(contextual.get("direction") or "neutral"),
            formula="Cross-source contextual comparison; it is not represented as a same-section filing diff.",
            confidence=str(contextual.get("confidence") or "Low"),
            parser_status=f"contextual_{contextual.get('status') or 'unknown'}",
            excerpt=f"Prior: {contextual.get('prior_excerpt') or 'Unavailable'} | Current: {contextual.get('current_excerpt') or 'Unavailable'}",
        ))
    intelligence = metrics.get("disclosure_intelligence")
    if isinstance(intelligence, dict):
        rows.append(EvidenceDrawer(
            label="Disclosure research bridge",
            claim=(
                f"Affected driver: {intelligence.get('affected_driver') or 'Unmapped'}. "
                f"Segment/industry checks: {', '.join(str(item) for item in intelligence.get('industry_kpis', [])[:5]) or 'Unknown'}."
            ),
            source="Company economics and industry playbook",
            section=event.category.replace("_", " ").title(),
            period=str(event.event_date or ""),
            metric="disclosure_research_bridge",
            value=str(intelligence.get("comparison_type") or "unknown"),
            formula=(
                "Connect disclosure shift to segment KPIs, capital allocation, credit/liquidity, peer operating metrics, "
                "confirmation evidence, and falsification tests."
            ),
            confidence="Requires corroboration",
            parser_status="research_work_order",
            excerpt=" | ".join(
                str(item) for item in (
                    intelligence.get("capital_allocation_checks", [])
                    + intelligence.get("credit_liquidity_checks", [])
                    + intelligence.get("peer_operating_checks", [])
                )[:8]
            ),
        ))
    work_order = str(comparison.get("research_work_order") or "")
    if work_order:
        rows.append(EvidenceDrawer(
            label="Disclosure work order",
            claim=work_order,
            source="SEC filing comparison",
            section=str(comparison.get("current_section") or ""),
            period=str(comparison.get("current_period") or ""),
            metric="research_work_order",
            value=reason,
            formula="Follow-up action generated from disclosure comparison diagnostics.",
            confidence="Blocks thesis-grade promotion" if status not in {"period_aligned", "comparable_imperfect"} else "Requires analyst validation",
            parser_status="work_order",
        ))
    return rows


def _text_signal_drawers(event) -> list[EvidenceDrawer]:
    metrics = event.metrics or {}
    comparison_status = str(metrics.get("comparison_status") or "unknown")
    current_period = str(metrics.get("current_period") or event.event_date or "Unknown")
    previous_period = str(metrics.get("previous_period") or "No comparable prior period")
    current_count = metrics.get("current_mentions")
    previous_count = metrics.get("previous_mentions")
    current_source = " ".join(
        str(part) for part in (
            metrics.get("current_form") or event.source,
            metrics.get("current_filing_date") or event.event_date,
        ) if part
    ) or event.source
    previous_source = " ".join(
        str(part) for part in (
            metrics.get("previous_form"),
            metrics.get("previous_filing_date"),
        ) if part
    ) or "No comparable prior source"
    if previous_count is None:
        previous_claim = "Prior comparable text was not captured; do not treat the comparison base as zero."
    else:
        previous_claim = f"Prior comparable text mentions: {previous_count}"
    return [
        EvidenceDrawer(
            label="Current text signal",
            claim=f"Current text mentions: {current_count}",
            source=current_source,
            accession=str(metrics.get("current_accession") or ""),
            section=str(event.category).replace("_", " ").title(),
            period=current_period,
            metric="current_mentions",
            value=str(current_count),
            formula="Keyword count over current filing text; not a financial metric.",
            confidence=comparison_status,
            parser_status="text_signal",
        ),
        EvidenceDrawer(
            label="Prior text signal",
            claim=previous_claim,
            source=previous_source,
            accession=str(metrics.get("previous_accession") or ""),
            section=str(event.category).replace("_", " ").title(),
            period=previous_period,
            metric="previous_mentions",
            value="Unknown" if previous_count is None else str(previous_count),
            formula="Prior keyword count requires a distinct comparable filing; missing prior is Unknown, not zero.",
            confidence=comparison_status,
            parser_status="text_signal_comparison",
        ),
    ]


def _drawer_from_citation(citation: Citation, claim: str, parser_status: str = "Unknown") -> EvidenceDrawer:
    return EvidenceDrawer(
        label=citation.section or citation.source or "Source evidence",
        claim=claim,
        source=citation.source,
        url=citation.url,
        source_tier=citation.source_tier,
        accession=citation.accession,
        section=citation.section,
        period=citation.period_end or citation.filed,
        parser_status=parser_status,
        excerpt=citation.snippet or "",
    )


def _format_value(value: float | None, unit: str = "") -> str:
    formatted = format_number(value)
    suffix = str(unit or "").strip()
    return f"{formatted} {suffix}".strip() if suffix and formatted != "n/a" else formatted


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            output.append(clean)
            seen.add(clean)
    return output
