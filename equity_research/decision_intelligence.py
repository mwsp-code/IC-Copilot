from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterable

from .models import (
    CatalystClock,
    CatalystClockItem,
    CompanyModelWorkspace,
    DecisionDelta,
    EvidenceWorkOrder,
    EventWorkflow,
    IcImpliedExpectationsSnapshot,
    MarketImpliedExpectation,
    MarketImpliedExpectations,
    PrioritizedResearchAction,
    TradeIdea,
    ValuationResult,
)
from .research_profiles import event_identifier


def build_ic_implied_expectations_snapshot(
    implied: MarketImpliedExpectations,
    company_model: CompanyModelWorkspace,
    valuation: ValuationResult,
) -> IcImpliedExpectationsSnapshot:
    rows = [row for row in implied.expectations if row.implied_value is not None]
    operating = _first_row(
        rows,
        (
            "Reverse DCF base FCF",
            "Tangible book value per share",
            "Book value per share",
            "Book value",
            "FFO",
        ),
    )
    anchor = _first_row(
        rows,
        (
            "Current free-cash-flow yield",
            "Current enterprise value / revenue",
            "Current price / tangible book",
            "Current price / book",
            "Implied insurer price-to-book",
            "Current price / FFO",
        ),
    )
    solved_prefixes = {
        "Bank": ("Implied sustainable ROE", "Implied credit-loss"),
        "Insurer": ("Implied insurer sustainable ROE", "Implied reserve"),
        "REIT": ("Implied FFO growth", "Implied property cap-rate"),
    }.get(
        implied.template,
        (
            "Implied commodity break-even",
            "Reverse DCF: implied",
            "Implied commodity",
        ),
    )
    solved = _first_matching(rows, solved_prefixes)
    if solved is None:
        solved = next(
            (row for row in rows if row is not operating and row is not anchor),
            None,
        )
    base_case = next(
        (case for case in company_model.cases if case.name.lower() == "base"),
        None,
    )
    base_label, base_value = _internal_base(base_case, valuation)
    disagreement = _disagreement(implied.current_price, base_case, valuation)
    weakest = _weakest_input(implied, solved)
    confidence = solved.confidence if solved else (
        anchor.confidence if anchor else "Unavailable"
    )
    interpretation = (
        solved.interpretation
        if solved
        else implied.summary
    )
    return IcImpliedExpectationsSnapshot(
        ticker=implied.ticker,
        status=implied.status,
        template=implied.template,
        title="What the Market Must Believe",
        current_price=implied.current_price,
        price_as_of=implied.price_as_of,
        currency=implied.currency,
        operating_base_label=operating.metric if operating else "Operating base",
        operating_base_value=_format_row(operating),
        valuation_anchor_label=anchor.metric if anchor else "Valuation anchor",
        valuation_anchor_value=_format_row(anchor),
        implied_variable_label=solved.metric if solved else "Price-implied variable",
        implied_variable_value=_format_row(solved),
        internal_base_label=base_label,
        internal_base_value=base_value,
        disagreement=disagreement,
        confidence=confidence,
        weakest_input=weakest,
        interpretation=interpretation,
        assumption_keys=[item.key for item in implied.assumptions if item.key],
    )


def build_decision_snapshot(
    ticker: str,
    ideas: list[TradeIdea],
    implied: IcImpliedExpectationsSnapshot,
    work_order: EvidenceWorkOrder,
    run_id: str,
    *,
    generated_at: str = "",
    completed_at: str = "",
    result_type: str = "live",
    research_profile: str = "",
    market_data_cutoff: str = "",
) -> dict:
    top = ideas[0] if ideas else None
    score = top.score.total if top and top.score else None
    event_ids = []
    principal_event_date = ""
    principal_event_title = ""
    if top:
        for event in top.source_events:
            event_id = str(event.metrics.get("event_id") or event.title)
            if event_id not in event_ids:
                event_ids.append(event_id)
            if not principal_event_date and event.event_date:
                principal_event_date = str(event.event_date)
                principal_event_title = str(event.title or "")
    return {
        "ticker": ticker.upper(),
        "run_id": run_id,
        "generated_at": generated_at,
        "completed_at": completed_at or generated_at,
        "result_type": result_type,
        "research_profile": research_profile,
        "principal_event_date": principal_event_date,
        "principal_event_title": principal_event_title,
        "market_data_cutoff": market_data_cutoff,
        "idea_id": top.idea_id if top else None,
        "stage": top.stage if top else "No thesis",
        "direction": top.direction if top else "Neutral",
        "score": score,
        "top_event_ids": event_ids[:5],
        "open_action_count": sum(item.status.lower() == "open" for item in work_order.items),
        "implied_variable_label": implied.implied_variable_label,
        "implied_variable_value": implied.implied_variable_value,
    }


def build_decision_delta(
    current: dict,
    previous: dict | None,
) -> DecisionDelta:
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not previous:
        return DecisionDelta(
            ticker=current["ticker"],
            status="Baseline",
            summary="This is the first comparable stored decision snapshot for the ticker.",
            neutral_changes=["Future runs will compare thesis stage, score, evidence actions, and market-implied assumptions."],
            observed_at=observed_at,
            current_run_id=str(current.get("run_id") or "") or None,
            current_completed_at=str(
                current.get("completed_at") or current.get("generated_at") or ""
            ) or None,
            current_result_type=str(current.get("result_type") or "live"),
            comparison_label="First stored baseline",
        )
    strengthening: list[str] = []
    weakening: list[str] = []
    neutral: list[str] = []
    current_profile = str(current.get("research_profile") or "")
    previous_profile = str(previous.get("research_profile") or "")
    incomparable_profile = bool(
        current_profile
        and previous_profile
        and current_profile != previous_profile
    )
    if incomparable_profile:
        neutral.append(
            "Research profile changed from "
            f"{previous_profile} to {current_profile}; depth-sensitive counts "
            "and scores are not directly comparable."
        )
    current_score = current.get("score")
    previous_score = previous.get("score")
    if isinstance(current_score, (int, float)) and isinstance(previous_score, (int, float)):
        change = current_score - previous_score
        if change >= 3:
            strengthening.append(f"Idea quality increased {change:.0f} points to {current_score:.0f}/100.")
        elif change <= -3:
            weakening.append(f"Idea quality decreased {abs(change):.0f} points to {current_score:.0f}/100.")
        else:
            neutral.append(f"Idea quality is broadly unchanged at {current_score:.0f}/100.")
    if current.get("stage") != previous.get("stage"):
        text = f"Thesis stage moved from {previous.get('stage', 'Unknown')} to {current.get('stage', 'Unknown')}."
        if _stage_rank(current.get("stage")) > _stage_rank(previous.get("stage")):
            strengthening.append(text)
        else:
            weakening.append(text)
    current_actions = int(current.get("open_action_count") or 0)
    previous_actions = int(previous.get("open_action_count") or 0)
    if current_actions < previous_actions:
        strengthening.append(f"Open evidence actions fell from {previous_actions} to {current_actions}.")
    elif current_actions > previous_actions:
        weakening.append(f"Open evidence actions increased from {previous_actions} to {current_actions}.")
    if current.get("direction") != previous.get("direction"):
        neutral.append(
            f"Direction changed from {previous.get('direction', 'Unknown')} to {current.get('direction', 'Unknown')}; review the causal bridge."
        )
    if current.get("implied_variable_value") != previous.get("implied_variable_value"):
        neutral.append(
            f"Market-implied output changed from {previous.get('implied_variable_value', 'Unknown')} "
            f"to {current.get('implied_variable_value', 'Unknown')}."
        )
    status = (
        "Incomparable profile change" if incomparable_profile
        else "Strengthened" if strengthening and not weakening
        else "Weakened" if weakening and not strengthening
        else "Mixed" if strengthening or weakening
        else "Unchanged"
    )
    summary = {
        "Incomparable profile change": (
            "The prior and current runs used different research depths; "
            "review the underlying evidence changes before inferring a delta."
        ),
        "Strengthened": "The new run closes more decision gaps than it opens.",
        "Weakened": "The new run adds or deepens decision-relevant concerns.",
        "Mixed": "The new run contains both strengthening and weakening evidence.",
        "Unchanged": "No material decision-level change was detected.",
    }[status]
    return DecisionDelta(
        ticker=current["ticker"],
        status=status,
        summary=summary,
        compared_with_run_id=str(previous.get("run_id") or "") or None,
        strengthening_changes=strengthening,
        weakening_changes=weakening,
        neutral_changes=neutral,
        observed_at=observed_at,
        current_run_id=str(current.get("run_id") or "") or None,
        current_completed_at=str(
            current.get("completed_at") or current.get("generated_at") or ""
        ) or None,
        current_result_type=str(current.get("result_type") or "live"),
        compared_with_completed_at=str(
            previous.get("completed_at") or previous.get("generated_at") or ""
        ) or None,
        compared_with_result_type=str(
            previous.get("result_type") or "live"
        ),
        compared_with_profile=str(
            previous.get("research_profile") or ""
        ) or None,
        comparison_label=_decision_comparison_label(previous),
    )


def _decision_comparison_label(previous: dict) -> str:
    result_type = str(previous.get("result_type") or "live").replace("_", " ")
    completed_at = str(
        previous.get("completed_at") or previous.get("generated_at") or ""
    )
    if completed_at:
        return f"Prior {result_type} run completed {completed_at}"
    return f"Prior {result_type} run"


def build_catalyst_clock(
    ticker: str,
    event_workflow: EventWorkflow,
    ideas: list[TradeIdea],
) -> CatalystClock:
    items: list[CatalystClockItem] = []
    for item in event_workflow.items:
        items.append(CatalystClockItem(
            catalyst_type=item.item_type,
            label=_readable_catalyst_label(item.title, item.item_type),
            due_date=item.due_date,
            status=item.status,
            source=item.source,
            related_idea_id=item.related_idea_id,
            related_idea_ids=(
                [item.related_idea_id] if item.related_idea_id else []
            ),
            decision_relevance=_catalyst_relevance(item.item_type),
            source_label=_readable_source_label(item.source),
        ))
    for idea in ideas[:3]:
        for monitor in idea.monitor_items:
            if not monitor.deadline:
                continue
            items.append(CatalystClockItem(
                catalyst_type="monitor_deadline",
                label=_readable_catalyst_label(
                    monitor.criterion,
                    "monitor_deadline",
                ),
                due_date=monitor.deadline,
                status=monitor.status,
                source=monitor.data_source,
                related_idea_id=idea.idea_id,
                related_idea_ids=[idea.idea_id],
                decision_relevance=(
                    "Tests whether the thesis is strengthening, weakening, "
                    "or should be closed."
                ),
                source_label=_readable_source_label(monitor.data_source),
            ))
    items = _dedupe_catalysts(items)
    items.sort(key=lambda item: (item.due_date is None, item.due_date or "", item.label))
    if items:
        next_item = items[0]
        summary = (
            f"Next dated catalyst: {next_item.label}"
            + (f" on {next_item.due_date}" if next_item.due_date else "")
            + "."
        )
        return CatalystClock(ticker, "Available", summary, items[:12])
    return CatalystClock(
        ticker,
        "Partial",
        "No dated catalyst was validated; recurring filing and earnings monitors remain active.",
        data_gaps=["A provider or issuer calendar is required for an exact next-event date."],
    )


def build_prioritized_research_queue(
    work_order: EvidenceWorkOrder,
    *,
    ticker: str = "",
    research_requests: Iterable[Any] = (),
    research_questions: Iterable[Any] = (),
    qa_findings: Iterable[Any] = (),
    events: Iterable[Any] = (),
    valuation: Any | None = None,
    limit: int = 20,
) -> list[PrioritizedResearchAction]:
    """Create one semantic queue shared by Decision, Evidence, and QA."""
    normalized_ticker = str(ticker or "").upper()
    event_id, event_date = _priority_action_event(events)
    candidates: list[PrioritizedResearchAction] = []
    for item in work_order.items:
        if str(item.status).casefold() in {"resolved", "closed"}:
            continue
        manual = any(token in item.source_type.lower() for token in ("manual", "licensed"))
        blocks = (
            "Research-Ready"
            if item.blocks_research_ready
            else "High-Conviction"
            if item.blocks_high_conviction
            else "Thesis quality"
        )
        action_kind = _action_kind(item.channel, item.action, item.source_type)
        source_families = _source_families(item.source_type, item.channel)
        candidates.append(PrioritizedResearchAction(
            rank=0,
            action_id=item.work_id,
            title=_readable_action_objective(
                normalized_ticker,
                action_kind,
                item.action,
                event_date=event_date,
            ),
            why_it_matters=item.why_it_matters,
            source_type=item.source_type,
            status=item.status,
            decision_impact=f"Blocks or weakens {blocks}.",
            auto_resolvable=not manual,
            blocks_stage=blocks,
            related_idea_ids=list(item.related_idea_ids),
            action_kind=action_kind,
            objective=_readable_action_objective(
                normalized_ticker,
                action_kind,
                item.action,
                event_date=event_date,
            ),
            materiality=(
                "High" if item.blocks_research_ready else
                "Medium" if item.blocks_high_conviction else "Low"
            ),
            execution_mode="manual" if manual else "automatic",
            event_id=event_id if action_kind == "market_event_attribution" else "",
            related_work_order_ids=[item.work_id],
            required_source_families=source_families,
            completion_criteria=list(item.acceptance_criteria),
            blocking_reason=(
                item.cost_latency
                if str(item.status).casefold() in {
                    "failed", "unavailable", "licensed_or_manual_required"
                }
                else ""
            ),
            origin_labels=["Evidence work order"],
            semantic_key=_semantic_action_key(
                normalized_ticker,
                action_kind,
                event_id if action_kind == "market_event_attribution" else "",
                source_families,
            ),
        ))

    for request in research_requests:
        objective = str(getattr(request, "objective", "") or "").strip()
        if not objective or str(getattr(request, "status", "")).casefold() in {
            "resolved", "closed"
        }:
            continue
        source_families = [
            str(value) for value in
            list(getattr(request, "required_source_families", []) or [])
            if value
        ]
        action_kind = _action_kind("", objective, " ".join(source_families))
        request_id = str(getattr(request, "request_id", "") or "")
        candidates.append(PrioritizedResearchAction(
            rank=0,
            action_id=request_id or _short_action_id(objective),
            title=_readable_action_objective(
                normalized_ticker, action_kind, objective, event_date=event_date
            ),
            why_it_matters=(
                str(getattr(request, "resolution_summary", "") or "")
                or "This targeted request may materially change the research conclusion."
            ),
            source_type=", ".join(source_families),
            status=str(getattr(request, "status", "planned") or "planned"),
            decision_impact=(
                f"{getattr(request, 'materiality', 'Medium')} materiality QA request."
            ),
            auto_resolvable=True,
            blocks_stage="Research QA",
            action_kind=action_kind,
            objective=_readable_action_objective(
                normalized_ticker, action_kind, objective, event_date=event_date
            ),
            materiality=str(getattr(request, "materiality", "Medium") or "Medium"),
            execution_mode="automatic",
            event_id=event_id if action_kind == "market_event_attribution" else "",
            related_research_request_ids=[request_id] if request_id else [],
            related_claim_ids=list(getattr(request, "affected_claim_ids", []) or []),
            related_finding_ids=list(getattr(request, "affected_finding_ids", []) or []),
            required_source_families=source_families,
            preferred_connectors=list(getattr(request, "preferred_connectors", []) or []),
            completion_criteria=[str(getattr(request, "stopping_rule", "") or "")],
            attempt_summary=_attempt_summary(request),
            resolution_summary=str(getattr(request, "resolution_summary", "") or ""),
            origin_labels=["Independent Research QA"],
            semantic_key=_semantic_action_key(
                normalized_ticker,
                action_kind,
                event_id if action_kind == "market_event_attribution" else "",
                source_families,
            ),
        ))

    for question in research_questions:
        title = str(getattr(question, "title", "") or "").strip()
        if not title or str(getattr(question, "status", "")).casefold() in {
            "resolved", "closed"
        }:
            continue
        sources = list(getattr(question, "primary_source_types", []) or [])
        action_kind = _action_kind(
            str(getattr(question, "driver_name", "") or ""),
            title,
            " ".join(str(value) for value in sources),
        )
        candidates.append(PrioritizedResearchAction(
            rank=0,
            action_id=str(getattr(question, "question_id", "") or _short_action_id(title)),
            title=_readable_action_objective(
                normalized_ticker, action_kind, title, event_date=event_date
            ),
            why_it_matters=str(getattr(question, "why_it_matters", "") or ""),
            source_type=", ".join(str(value) for value in sources),
            status=str(getattr(question, "status", "Open") or "Open"),
            decision_impact="Tests a decision-relevant research mechanism.",
            auto_resolvable=True,
            blocks_stage="Thesis quality",
            related_idea_ids=list(getattr(question, "related_idea_ids", []) or []),
            action_kind=action_kind,
            objective=_readable_action_objective(
                normalized_ticker, action_kind, title, event_date=event_date
            ),
            materiality=str(getattr(question, "priority", "Medium") or "Medium"),
            execution_mode="automatic",
            required_source_families=[str(value) for value in sources],
            completion_criteria=list(getattr(question, "acceptance_criteria", []) or []),
            origin_labels=["Research question"],
            semantic_key=_semantic_action_key(
                normalized_ticker, action_kind, "", [str(value) for value in sources]
            ),
        ))

    for resolution in list(
        getattr(valuation, "input_resolutions", []) or []
    ):
        if str(getattr(resolution, "status", "")).casefold() != "missing":
            continue
        input_name = str(
            getattr(resolution, "input_name", "valuation input")
            or "valuation input"
        )
        reason = str(
            getattr(resolution, "reason", "")
            or f"{input_name} is not yet normalized."
        )
        slug = re.sub(r"[^a-z0-9]+", "_", input_name.casefold()).strip("_")
        source_families = ["sec_filings", "consensus"]
        objective = (
            f"Resolve {normalized_ticker or 'company'} {input_name} for the "
            "valuation bridge"
        )
        candidates.append(PrioritizedResearchAction(
            rank=0,
            action_id=_short_action_id(
                f"{normalized_ticker}:{input_name}:valuation-input"
            ),
            title=objective,
            why_it_matters=(
                "A decision-useful valuation method is unavailable until the exact "
                "input, period, unit, and currency basis is validated."
            ),
            source_type="SEC filings and configured point-in-time consensus",
            status="planned",
            decision_impact="Weakens valuation triangulation and confidence.",
            auto_resolvable=True,
            blocks_stage="Thesis quality",
            action_kind="valuation_input_resolution",
            objective=objective,
            materiality="Medium",
            execution_mode="automatic",
            required_source_families=source_families,
            preferred_connectors=[
                "sec_companyfacts",
                "issuer_filings",
                "configured_consensus",
            ],
            completion_criteria=[
                "Retrieve or derive the input from admitted evidence.",
                "Validate period, unit, currency, and formula alignment.",
                "Recompute the affected valuation method and QA closure.",
            ],
            blocking_reason=reason,
            origin_labels=["Valuation input resolver"],
            semantic_key=(
                f"valuation_input_resolution:{normalized_ticker or 'unknown'}:"
                f"{slug}:sec_filings"
            ),
        ))

    finding_by_request = {
        request_id: str(getattr(finding, "finding_id", "") or "")
        for finding in qa_findings
        for request_id in list(getattr(finding, "evidence_request_ids", []) or [])
    }
    for candidate in candidates:
        related_findings = list(candidate.related_finding_ids)
        for request_id in candidate.related_research_request_ids:
            finding_id = finding_by_request.get(request_id, "")
            if finding_id:
                related_findings.append(finding_id)
        candidate.related_finding_ids = list(dict.fromkeys(related_findings))

    merged = _merge_investigation_actions(candidates)
    priority_rank = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
    merged.sort(key=lambda item: (
        priority_rank.get(item.materiality, 4),
        item.blocks_stage != "Research-Ready",
        not item.auto_resolvable,
        item.semantic_key,
    ))
    for rank, item in enumerate(merged[:max(1, limit)], start=1):
        item.rank = rank
    return merged[:max(1, limit)]


def reconcile_targeted_investigation_action(
    actions: list[PrioritizedResearchAction],
    *,
    action_id: str,
    action_kind: str = "",
    semantic_key: str = "",
    retrieval_lifecycles: Iterable[Any] = (),
) -> PrioritizedResearchAction | None:
    """Attach the targeted run's actual retrieval outcome to its open action.

    A completed research workflow does not imply that the selected investigation
    closed. If the action remains in the canonical queue, record whether a
    registered retrieval was attempted and keep the action explicitly open.
    """

    if not action_id and not semantic_key and not action_kind:
        return None
    target = next(
        (
            item for item in actions
            if (action_id and (
                item.action_id == action_id
                or action_id in item.related_research_request_ids
                or action_id in item.related_work_order_ids
            ))
            or (semantic_key and item.semantic_key == semantic_key)
        ),
        None,
    )
    if target is None and action_kind:
        same_kind = [item for item in actions if item.action_kind == action_kind]
        target = same_kind[0] if len(same_kind) == 1 else None
    if target is None:
        return None

    attempts = [
        item for item in retrieval_lifecycles
        if str(getattr(item, "request_id", "") or "") == action_id
    ]
    if attempts:
        sources = list(dict.fromkeys(
            str(
                getattr(item, "source_type", "")
                or getattr(item, "source_id", "")
                or "registered connector"
            )
            for item in attempts
        ))
        statuses = list(dict.fromkeys(
            str(getattr(item, "status", "") or "attempted")
            .replace("_", " ")
            for item in attempts
        ))
        target.status = "attempted_unresolved"
        target.attempt_summary = (
            "Attempted via " + ", ".join(sources)
            + "; outcome: " + ", ".join(statuses) + "."
        )
        failure = next(
            (
                str(getattr(item, "message", "") or "")
                for item in attempts
                if str(getattr(item, "message", "") or "")
            ),
            "",
        )
        target.blocking_reason = failure or (
            "The targeted retrieval completed, but the action's completion "
            "criteria were not satisfied."
        )
    else:
        target.status = "not_attempted_missing_connector"
        target.attempt_summary = (
            "No registered connector attempt was recorded for this action."
        )
        target.blocking_reason = (
            "The research pass completed without a compatible registered "
            "retrieval. Configure an eligible source or review Evidence for "
            "the unavailable source family."
        )
    return target


def _action_kind(channel: str, action: str, source_type: str) -> str:
    text = " ".join((channel, action, source_type)).casefold()
    if any(token in text for token in (
        "forward ebitda", "net debt", "normalized shares",
        "valuation input", "currency alignment",
    )):
        return "valuation_input_resolution"
    if any(token in text for token in ("consensus", "estimate revision", "expectation")):
        return "consensus_revision_capture"
    if any(token in text for token in ("event-window", "event window", "beta attribution", "price bars", "market reaction")):
        return "market_event_attribution"
    if any(token in text for token in ("bull/base/bear", "valuation", "scenario payoff", "reverse dcf")):
        return "valuation_scenarios"
    if any(token in text for token in ("cash generation", "liquidity", "cash conversion", "free cash flow")):
        return "cash_conversion_analysis"
    if any(token in text for token in ("gross margin", "pricing", "volume", "mix", "cost mechanism")):
        return "operating_driver_analysis"
    if any(token in text for token in ("wisburg", "external theme", "external research")):
        return "external_theme_validation"
    return "primary_source_validation"


def _readable_action_objective(
    ticker: str,
    action_kind: str,
    original: str,
    *,
    event_date: str = "",
) -> str:
    company = ticker or "the company"
    if action_kind == "market_event_attribution":
        return f"Complete {company} event-reaction attribution"
    if action_kind == "consensus_revision_capture":
        anchor = event_date or "the source event"
        return f"Capture point-in-time {company} estimate revisions around {anchor}"
    if action_kind == "valuation_scenarios":
        return f"Complete {company} bull/base/bear valuation and payoff assumptions"
    if action_kind == "valuation_input_resolution":
        clean = re.sub(
            r"^(find|retrieve|resolve|validate)\s+",
            "",
            original.strip(),
            flags=re.I,
        )
        return f"Resolve {company} {clean}" if clean else f"Resolve {company} valuation inputs"
    if action_kind == "cash_conversion_analysis":
        return (
            f"Test whether {company} cash conversion remains recurring after "
            "working capital, capex, taxes, financing, and capital returns"
        )
    if action_kind == "operating_driver_analysis":
        return (
            f"Explain {company} pricing, volume, mix, and cost mechanisms with "
            "durability evidence"
        )
    if action_kind == "external_theme_validation":
        theme = _external_theme(original)
        return f"Validate the {company} {theme} theme with issuer evidence"
    if ticker:
        clean = re.sub(r"^(find or resolve|cross-check|investigate)\s+", "Validate ", original.strip(), flags=re.I)
        return clean if ticker.casefold() in clean.casefold() else f"{clean.rstrip('.')} for {ticker}"
    return original.strip()


def _external_theme(value: str) -> str:
    match = re.search(r"theme\s*:\s*([^.;]+)", value, re.I)
    return match.group(1).strip() if match else "external research"


def _source_families(source_type: str, channel: str) -> list[str]:
    values = [
        item.strip().casefold().replace(" ", "_")
        for item in re.split(r"[,/;]", source_type or channel)
        if item.strip()
    ]
    return list(dict.fromkeys(values))


def _semantic_action_key(
    ticker: str,
    action_kind: str,
    event_id: str,
    source_families: list[str],
) -> str:
    family = sorted(source_families)[0] if source_families else "any"
    target = event_id if action_kind == "market_event_attribution" else "company"
    return f"{action_kind}:{ticker or 'unknown'}:{target}:{family}"


def _priority_action_event(events: Iterable[Any]) -> tuple[str, str]:
    rows = [item for item in events if getattr(item, "event_date", "")]
    if not rows:
        return "", ""
    event = max(rows, key=lambda item: (
        str(getattr(item, "event_date", "") or ""),
        int(getattr(item, "severity", 0) or 0),
    ))
    metrics = getattr(event, "metrics", {}) or {}
    return (
        str(metrics.get("event_id") or event_identifier(event)),
        str(getattr(event, "event_date", "") or ""),
    )


def _attempt_summary(request: Any) -> str:
    attempted = list(getattr(request, "attempted_connector_ids", []) or [])
    return (
        "Attempted: " + ", ".join(str(value) for value in attempted)
        if attempted else "Not attempted"
    )


def _short_action_id(value: str) -> str:
    return "action-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _merge_investigation_actions(
    candidates: list[PrioritizedResearchAction],
) -> list[PrioritizedResearchAction]:
    by_key: dict[str, PrioritizedResearchAction] = {}
    priority = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}
    for candidate in candidates:
        existing = by_key.get(candidate.semantic_key)
        if existing is None:
            by_key[candidate.semantic_key] = candidate
            continue
        if len(candidate.objective) > len(existing.objective):
            existing.objective = candidate.objective
            existing.title = candidate.title
        if priority.get(candidate.materiality, 0) > priority.get(existing.materiality, 0):
            existing.materiality = candidate.materiality
        existing.auto_resolvable = existing.auto_resolvable or candidate.auto_resolvable
        for field_name in (
            "related_idea_ids",
            "related_work_order_ids",
            "related_research_request_ids",
            "related_claim_ids",
            "related_finding_ids",
            "required_source_families",
            "preferred_connectors",
            "completion_criteria",
            "origin_labels",
        ):
            setattr(existing, field_name, list(dict.fromkeys([
                *getattr(existing, field_name),
                *getattr(candidate, field_name),
            ])))
        if candidate.attempt_summary and candidate.attempt_summary != "Not attempted":
            existing.attempt_summary = candidate.attempt_summary
    return list(by_key.values())


def _first_row(
    rows: list[MarketImpliedExpectation],
    names: tuple[str, ...],
) -> MarketImpliedExpectation | None:
    for name in names:
        row = next((item for item in rows if item.metric == name), None)
        if row:
            return row
    return None


def _first_matching(
    rows: list[MarketImpliedExpectation],
    prefixes: tuple[str, ...],
) -> MarketImpliedExpectation | None:
    for prefix in prefixes:
        row = next((item for item in rows if item.metric.startswith(prefix)), None)
        if row:
            return row
    return None


def _format_row(row: MarketImpliedExpectation | None) -> str:
    if not row or row.implied_value is None:
        return "Unknown"
    return f"{row.implied_value:,.2f} {row.unit}".strip()


def _internal_base(base_case, valuation: ValuationResult) -> tuple[str, str]:
    if base_case and base_case.fair_value is not None:
        return "Internal base fair value", f"{base_case.fair_value:,.2f} {valuation.currency}"
    valuation_case = next(
        (case for case in valuation.cases if case.name.lower() == "base" and case.fair_value is not None),
        None,
    )
    if valuation_case:
        return "Internal base fair value", f"{valuation_case.fair_value:,.2f} {valuation.currency}"
    return "Internal base case", "Unknown"


def _disagreement(current_price: float | None, base_case, valuation: ValuationResult) -> str:
    fair_value = base_case.fair_value if base_case and base_case.fair_value is not None else None
    if fair_value is None:
        valuation_case = next(
            (case for case in valuation.cases if case.name.lower() == "base" and case.fair_value is not None),
            None,
        )
        fair_value = valuation_case.fair_value if valuation_case else None
    if not current_price or fair_value is None:
        return "Unknown"
    gap = (fair_value / current_price - 1) * 100
    return f"Internal base is {gap:+.1f}% versus current price."


def _weakest_input(
    implied: MarketImpliedExpectations,
    solved: MarketImpliedExpectation | None,
) -> str:
    if solved and solved.missing_inputs:
        return solved.missing_inputs[0]
    if implied.data_gaps:
        return implied.data_gaps[0]
    if solved and solved.confidence == "Low":
        return "The normalized financial base is low confidence."
    return "No critical model input is currently missing."


def _stage_rank(stage: object) -> int:
    value = str(stage or "").lower().replace("_", "-")
    if "high" in value:
        return 3
    if "research" in value:
        return 2
    if "candidate" in value or "watch" in value:
        return 1
    return 0


def _dedupe_catalysts(items: list[CatalystClockItem]) -> list[CatalystClockItem]:
    result: list[CatalystClockItem] = []
    positions: dict[tuple[str, str, str | None], int] = {}
    for item in items:
        key = (
            _normalized_catalyst_type(item.catalyst_type),
            _normalized_catalyst_label(item.label),
            item.due_date,
        )
        if key in positions:
            existing = result[positions[key]]
            existing.related_idea_ids = list(dict.fromkeys([
                *existing.related_idea_ids,
                *([existing.related_idea_id] if existing.related_idea_id else []),
                *item.related_idea_ids,
                *([item.related_idea_id] if item.related_idea_id else []),
            ]))
            if not existing.decision_relevance and item.decision_relevance:
                existing.decision_relevance = item.decision_relevance
            if not existing.source_label and item.source_label:
                existing.source_label = item.source_label
            continue
        positions[key] = len(result)
        result.append(item)
    return result


def _normalized_catalyst_type(value: str) -> str:
    normalized = str(value or "").casefold().replace("-", "_").replace(" ", "_")
    if normalized in {
        "monitor_rule",
        "monitor_deadline",
        "consensus_reaction",
        "market_reaction",
    }:
        return "thesis_monitor"
    if "earn" in normalized or "filing" in normalized:
        return "company_update"
    return normalized or "other"


def _normalized_catalyst_label(value: str) -> str:
    words = [
        word for word in str(value or "").casefold().replace("_", " ").split()
        if word not in {"rule", "deadline", "monitor", "watching", "pending"}
    ]
    return " ".join(words)


def _readable_catalyst_label(label: str, catalyst_type: str) -> str:
    text = str(label or "").replace("_", " ").strip()
    lowered = text.casefold()
    if "consensus" in lowered and "reaction" in lowered:
        return "Analyst-estimate response check"
    if "market" in lowered and "reaction" in lowered:
        return "Abnormal price-reaction check"
    if not text:
        text = str(catalyst_type or "Catalyst").replace("_", " ")
    return text[0].upper() + text[1:]


def _readable_source_label(source: str) -> str:
    text = str(source or "").replace("_", " ").strip()
    if text.casefold() == "consensus.revisions":
        return "Point-in-time consensus snapshots"
    if "provider" in text.casefold():
        return text
    return text or "Registered research source"


def _catalyst_relevance(catalyst_type: str) -> str:
    normalized = str(catalyst_type or "").casefold()
    if "earn" in normalized or "filing" in normalized:
        return "May confirm or falsify the operating and valuation bridge."
    if "regulat" in normalized:
        return "May change the probability or economics of the regulatory thesis."
    return "May change the evidence strength, market-capture view, or monitor status."
