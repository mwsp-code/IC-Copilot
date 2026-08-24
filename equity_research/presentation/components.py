from __future__ import annotations

import html
from typing import Any, Callable, Iterable

from equity_research.application import price_csv_template

from .view_models import (
    CandidateStatusViewModel,
    DecisionPageIntent,
    DecisionBriefViewModel,
    EvidencePageViewModel,
    EvidenceReferenceViewModel,
    IntegrityTimelineViewModel,
    InvestigationActionViewModel,
    InvestigationJourneyViewModel,
    ModelValuationViewModel,
    PublicationReadinessViewModel,
    PublicationRepairIntent,
    ResearchRouteViewModel,
    ResearchRequestCardViewModel,
    ResearchSessionViewModel,
)


_STATUS_ICONS = {
    "completed": "✓",
    "complete": "✓",
    "closed": "✓",
    "available": "✓",
    "partial": "△",
    "diligence gap": "△",
    "needs attention": "△",
    "unavailable": "—",
    "not completed": "—",
    "independent qa not run": "—",
    "failed": "!",
    "rejected": "!",
}


def render_status_badge(st: Any, label: str, status: str) -> None:
    icon = _STATUS_ICONS.get(status.casefold(), "•")
    st.caption(f"{icon} {label}: {status}")


def render_metric_cards(st: Any, items: Iterable[tuple[str, str]]) -> None:
    values = list(items)
    for start in range(0, len(values), 4):
        batch = values[start:start + 4]
        columns = st.columns(len(batch))
        for column, (label, value) in zip(columns, batch):
            column.metric(label, value)


def render_section_header(st: Any, title: str, description: str = "") -> None:
    st.markdown(f"#### {title}")
    if description:
        st.caption(description)


def render_insight_card(
    st: Any,
    *,
    title: str,
    insight: str,
    status: str,
    next_action: str = "",
) -> None:
    with st.container(border=True):
        render_status_badge(st, title, status)
        st.write(insight)
        if next_action:
            st.caption(f"Next action: {next_action}")


def render_valuation_card(st: Any, model: DecisionBriefViewModel) -> None:
    render_section_header(st, "Valuation")
    render_metric_cards(st, [
        ("Scenario range", model.valuation_range),
        ("Weighted value", model.probability_weighted_value),
        ("Expected return", model.expected_return),
        ("Confidence", model.valuation_confidence),
    ])


def render_risk_card(st: Any, model: DecisionBriefViewModel) -> None:
    render_insight_card(
        st,
        title="Counter-thesis",
        insight=model.counter_thesis,
        status=model.counter_thesis_status,
        next_action=(
            model.next_actions[0] if model.counter_thesis_status == "Diligence gap"
            and model.next_actions else ""
        ),
    )


def render_evidence_reference(st: Any, reference: EvidenceReferenceViewModel) -> None:
    label = " · ".join(
        value for value in (reference.label, reference.date) if value
    )
    if reference.url:
        st.markdown(f"[{label}]({reference.url})")
    else:
        st.caption(label)


def render_evidence_drawer(
    st: Any,
    references: Iterable[EvidenceReferenceViewModel],
    *,
    label: str = "Evidence and provenance",
) -> None:
    rows = list(references)
    if not rows:
        return
    with st.expander(label, expanded=False):
        for reference in rows:
            render_evidence_reference(st, reference)


def render_research_route_header(
    st: Any,
    model: ResearchRouteViewModel,
) -> None:
    """Render the shared reading hierarchy for one lazy Research subroute."""

    st.markdown(f"### {model.title}")
    render_metric_cards(st, list(model.metrics[:4]))
    if model.what_matters:
        with st.container(border=True):
            st.markdown("**What matters**")
            for item in model.what_matters[:5]:
                st.markdown(f"- {item}")
    if model.strongest_limitation:
        st.warning(f"Limiting observation: {model.strongest_limitation}")
    if isinstance(model, ModelValuationViewModel) and model.input_resolutions:
        with st.expander("How valuation inputs were resolved", expanded=False):
            for item in model.input_resolutions:
                with st.container(border=True):
                    render_status_badge(st, item.input_name, item.status)
                    st.caption(f"Value: {item.value} | Source: {item.source}")
                    if item.formula:
                        st.caption("Formula: " + item.formula)
                    if item.reason:
                        st.write(item.reason)
    if model.action_references:
        st.caption("Relevant next actions: " + ", ".join(model.action_references))


def render_research_session_workbench(
    st: Any,
    model: ResearchSessionViewModel | None,
) -> None:
    if model is None:
        return
    with st.expander("Agent research answers", expanded=True):
        st.caption(
            f"{model.provider_label or 'Configured primary researcher'} · "
            f"{model.budget_label} · {model.answered_count} answered · "
            f"{model.partial_count} partial · {model.total_count} shown"
        )
        for question in model.questions[:6]:
            with st.container(border=True):
                render_status_badge(
                    st,
                    question.domain,
                    question.status.replace("_", " ").title(),
                )
                st.markdown(f"**{question.title}**")
                if question.answer:
                    st.write(question.answer)
                    st.caption(
                        f"Confidence: {question.confidence} · "
                        f"{question.evidence_count} admitted evidence reference(s)"
                    )
                else:
                    st.caption(
                        "Not researched in this bounded run; retained in the agenda."
                    )
                if question.strongest_limitation:
                    st.warning(question.strongest_limitation)
                if question.remaining_work:
                    with st.expander("What remains", expanded=False):
                        for item in question.remaining_work:
                            st.markdown(f"- {item}")
        if len(model.questions) > 6:
            st.caption(
                f"Showing 6 of {len(model.questions)} route-relevant questions. "
                "The complete agenda and provenance remain under Evidence and Audit."
            )
        if model.stopping_reason:
            st.caption(
                "Research stopped because: "
                + model.stopping_reason.replace("_", " ").rstrip(".")
                + "."
            )


def render_investigation_action_register(
    st: Any,
    actions: Iterable[InvestigationActionViewModel],
    *,
    heading: str = "Investigation actions",
) -> None:
    """Render full queue mechanics where provenance belongs."""

    rows = list(actions)
    st.markdown(f"#### {heading}")
    if not rows:
        render_actionable_empty_state(
            st,
            title="No open investigation actions",
            reason="All material actions were resolved or no action was necessary.",
        )
        return
    for action in rows:
        with st.container(border=True):
            render_status_badge(st, f"Action {action.rank}", action.status)
            st.markdown(f"**{action.objective}**")
            st.caption(
                f"Kind: {action.action_kind.replace('_', ' ')} | "
                f"Materiality: {action.materiality} | Mode: {action.execution_mode}"
            )
            st.write(action.why_it_matters)
            if action.source_families:
                st.caption("Required sources: " + ", ".join(action.source_families))
            if action.completion_criteria:
                with st.expander("Completion criteria", expanded=False):
                    for criterion in action.completion_criteria:
                        st.markdown(f"- {criterion}")
            if action.attempt_summary:
                st.caption(f"Latest attempt: {action.attempt_summary}")
            if action.blocking_reason:
                st.info(action.blocking_reason)


def render_research_request_card(
    st: Any,
    request: ResearchRequestCardViewModel,
) -> None:
    with st.container(border=True):
        render_status_badge(
            st,
            f"{request.priority} priority request",
            request.answer_status.replace("_", " ").title(),
        )
        st.markdown(f"**{request.objective}**")
        if request.answer:
            st.write(request.answer)
        if request.confidence and request.confidence != "Unknown":
            st.caption(
                "Answer confidence: " + request.confidence
                + (f" · {request.confidence_reason}" if request.confidence_reason else "")
            )
        if request.blocking_reason:
            st.warning(request.blocking_reason)
        st.caption(
            "Request workflow: " + request.status.replace("_", " ").title()
        )
        if request.source_families:
            st.caption("Source families: " + ", ".join(request.source_families))
        with st.expander("Evidence found and remaining work", expanded=False):
            if request.evidence_found:
                st.markdown("**What the attempt established**")
                for item in request.evidence_found:
                    st.markdown(f"- {item}")
            if request.evidence_missing:
                st.markdown("**What is still missing**")
                for item in request.evidence_missing:
                    st.markdown(f"- {item}")
            if request.next_step:
                st.markdown("**Next step**")
                st.write(request.next_step)
            if request.confirming_evidence:
                st.write("Confirming evidence sought: " + "; ".join(request.confirming_evidence))
            if request.contradicting_evidence:
                st.write("Contradicting evidence sought: " + "; ".join(request.contradicting_evidence))
            if request.attempted_connectors:
                st.write("Connectors attempted: " + ", ".join(request.attempted_connectors))
            if request.stopping_rule:
                st.write("Stopping rule: " + request.stopping_rule)
            if request.resolution_summary:
                st.write("Resolution: " + request.resolution_summary)
            flow = [
                value for value in (
                    request.search_summary,
                    request.reading_summary,
                    request.extraction_summary,
                    request.calculation_summary,
                ) if value
            ]
            if flow:
                st.markdown("**How the app researched this question**")
                for item in flow:
                    st.markdown(f"- {item}")


def render_candidate_status_row(
    st: Any,
    candidate: CandidateStatusViewModel,
) -> None:
    with st.container(border=True):
        render_status_badge(
            st,
            candidate.candidate_type.replace("_", " ").title(),
            candidate.status.replace("_", " ").title(),
        )
        st.write(candidate.title)
        st.caption(" · ".join(
            value for value in (
                candidate.materiality,
                candidate.source_family,
                candidate.origin,
            ) if value
        ))
        if candidate.url:
            st.caption("Candidate URL (not evidence until admitted): " + candidate.url)
        if candidate.decision_reason:
            st.caption(candidate.decision_reason)


def render_actionable_empty_state(
    st: Any,
    *,
    title: str,
    reason: str,
    action: str = "",
) -> None:
    message = f"{title}: {reason}"
    if action:
        message += f" Next action: {action}"
    st.info(message)


def render_bounded_data_table(
    st: Any,
    rows: Iterable[dict[str, Any]],
    *,
    max_rows: int = 20,
    empty_title: str = "No rows",
    empty_reason: str = "No eligible data was produced for this run.",
) -> None:
    values = list(rows)
    if not values:
        render_actionable_empty_state(
            st,
            title=empty_title,
            reason=empty_reason,
        )
        return
    st.dataframe(values[:max_rows], hide_index=True, use_container_width=True)
    if len(values) > max_rows:
        st.caption(f"Showing {max_rows} of {len(values)} rows.")


def render_evidence_page_overview(st: Any, model: EvidencePageViewModel) -> str:
    requested_intent = ""
    if model.investigation_trail is not None:
        trail = model.investigation_trail
        render_section_header(
            st,
            "Investigation evidence trail",
            "Follow the selected question from Research Request through evidence admission and conclusion change.",
        )
        with st.container(border=True):
            render_status_badge(
                st,
                "Answer so far",
                trail.answer_status.replace("_", " ").title(),
            )
            _render_plain_text(st, trail.question, strong=True)
            if trail.answer:
                _render_plain_text(st, trail.answer)
            answer_metrics = [
                ("Confidence", trail.confidence),
                ("Coverage", trail.coverage),
                ("Comparison", (
                    f"{len(trail.period_comparison)} comparable TTM periods"
                    if trail.period_comparison else "No period bridge"
                )),
                ("Independent QA", trail.qa_freshness.replace("_", " ").title()),
            ]
            render_metric_cards(st, answer_metrics)
            if trail.period_comparison:
                st.markdown("**Comparable cash-conversion periods**")
                render_bounded_data_table(
                    st,
                    trail.period_comparison,
                    max_rows=2,
                    empty_title="No comparable periods",
                    empty_reason="A duration-aligned comparison was not available.",
                )
            if trail.evidence_found:
                st.markdown("**Strongest supporting result**")
                for item in trail.evidence_found[:2]:
                    _render_plain_text(st, item, list_item=True)
            for heading, rows in trail.component_sections:
                with st.expander(heading, expanded=False):
                    render_bounded_data_table(
                        st,
                        rows,
                        max_rows=10,
                        empty_title="No component detail",
                        empty_reason="This component was not separately disclosed.",
                    )
            if trail.evidence_missing:
                st.markdown("**Remaining material action**")
                _render_plain_text(st, trail.evidence_missing[0], list_item=True)
            if trail.next_step:
                st.info("Next step: " + trail.next_step)
            if trail.qa_freshness in {"pending", "stale", "not_reviewed"} and trail.period_comparison:
                if st.button(
                    "Run QA for refreshed answer",
                    key="run_qa_for_refreshed_cash_conversion",
                    use_container_width=True,
                ):
                    requested_intent = "run_refreshed_answer_qa"
            with st.expander("How the investigation progressed", expanded=False):
                render_status_badge(st, "Run outcome", trail.outcome)
                for step in trail.steps:
                    render_status_badge(
                        st,
                        step.label,
                        step.status.replace("_", " ").title(),
                    )
                    st.caption(step.summary)
            st.caption(
                "Sandbox leads: "
                f"{trail.relevant_candidate_count} relevant; "
                f"{trail.hidden_candidate_count} rejected or hidden. "
                "Existing admitted evidence is counted separately."
            )
    render_section_header(
        st,
        "Evidence integrity",
        "Only deterministically admitted evidence can support claims, scores, or conclusions.",
    )
    render_metric_cards(st, [
        ("Admitted", str(model.admitted_count)),
        ("Excluded", str(model.excluded_count)),
        ("Contradictions", str(model.contradiction_count)),
        ("Research requests", str(len(model.requests))),
    ])
    with st.expander("Complete evidence inventory", expanded=model.investigation_trail is None):
        render_section_header(st, "Targeted Research Requests")
        if model.request_groups:
            for group_label, requests in model.request_groups:
                st.markdown(f"**{group_label} · {len(requests)}**")
                for request in requests[:6]:
                    render_research_request_card(st, request)
                if len(requests) > 6:
                    st.caption(f"Showing 6 of {len(requests)} requests in this group.")
        else:
            render_actionable_empty_state(
                st,
                title="No Research Requests",
                reason="The auditor did not generate a targeted request, or Independent QA was not run.",
            )
        render_section_header(
            st,
            "Research Sandbox",
            "Discovered candidates stay quarantined until registered retrieval and common admission checks succeed.",
        )
        st.caption(
            f"{model.relevant_candidate_count + model.hidden_candidate_count} candidates discovered · "
            f"{model.relevant_candidate_count} relevant · "
            f"{model.hidden_candidate_count} rejected or hidden"
        )
        if model.candidates:
            for candidate in model.candidates[:8]:
                render_candidate_status_row(st, candidate)
            if len(model.candidates) > 8:
                st.caption(f"Showing 8 of {len(model.candidates)} relevant candidates.")
        else:
            render_actionable_empty_state(
                st,
                title="No relevant Sandbox candidates",
                reason="Discovery was unnecessary, not attempted, or produced only off-scope or rejected metadata leads.",
            )
    return requested_intent


def _render_plain_text(
    st: Any,
    value: str,
    *,
    strong: bool = False,
    list_item: bool = False,
) -> None:
    """Render untrusted prose literally, without Markdown/LaTeX parsing."""

    escaped = html.escape(str(value or ""), quote=True).replace("\n", "<br>")
    if list_item:
        markup = f"<ul><li>{escaped}</li></ul>"
    elif strong:
        markup = f"<p><strong>{escaped}</strong></p>"
    else:
        markup = f"<p>{escaped}</p>"
    st.markdown(markup, unsafe_allow_html=True)


def render_investigation_journey(
    st: Any,
    model: InvestigationJourneyViewModel,
    *,
    heading: str | None = None,
    compact: bool = False,
    on_open_evidence: Any | None = None,
) -> str:
    title = heading or ("Research journey" if model.active else "Investigation result")
    with st.container(border=True):
        st.markdown(f"#### {title} · {model.operation_label}")
        if compact and not model.active:
            render_metric_cards(st, [
                ("Run", model.run_status.replace("_", " ").title() or model.outcome or "Completed"),
                ("Answer", model.answer_status.replace("_", " ").title() or "Unavailable"),
                ("Independent QA", model.qa_label),
            ])
            if model.direct_answer:
                _render_plain_text(st, model.direct_answer)
            if model.change_summaries:
                st.caption("What changed: " + model.change_summaries[0])
            if model.strongest_limitation:
                st.caption("Limitation: " + model.strongest_limitation)
            st.button(
                "Open full evidence trail",
                key=f"open-evidence-trail-{model.ticker}-{model.operation_label}",
                use_container_width=True,
                on_click=on_open_evidence,
            )
            return ""
        if compact and model.active:
            st.progress(model.progress_percent, text=model.progress_label)
            st.caption(
                f"{model.ticker} · {model.current_stage} · "
                f"{model.elapsed_label} elapsed · {model.qa_label}"
            )
            if model.execution_state:
                st.caption(_execution_state_copy(model.execution_state))
            return ""
        if model.objective and model.objective.casefold() != model.operation_label.casefold():
            st.caption(model.objective)
        if model.active:
            st.progress(model.progress_percent, text=model.progress_label)
            st.caption(
                f"{model.ticker} · {model.current_stage} · "
                f"{model.elapsed_label} elapsed · {model.qa_label}"
            )
            if model.execution_state:
                st.caption(_execution_state_copy(model.execution_state))
        else:
            render_metric_cards(st, [
                ("Outcome", model.outcome or "Completed"),
                ("Evidence admitted", str(model.admitted_evidence_count)),
                ("Restart recovery", model.persistence_status or "Not reported"),
            ])
            if model.direct_answer:
                render_status_badge(
                    st,
                    "Direct answer",
                    model.answer_status.replace("_", " ").title()
                    or "Completed",
                )
                st.write(model.direct_answer)
                st.caption(f"Confidence: {model.confidence}")
                if model.key_evidence:
                    with st.expander("Key evidence and findings", expanded=False):
                        for item in model.key_evidence:
                            st.write(f"- {item}")
                if model.strongest_limitation:
                    st.info("Strongest limitation: " + model.strongest_limitation)
        with st.expander("Stage details", expanded=False):
            for stage in model.stages:
                icon = (
                    "✓" if stage.status == "completed"
                    else "●" if stage.status == "active"
                    else "○"
                )
                st.caption(f"{icon} {stage.label}")
        if model.change_summaries:
            st.markdown("**What changed**")
            for item in model.change_summaries[:4]:
                st.write(f"- {item}")
        if model.remaining_actions:
            with st.expander("What remains to investigate", expanded=False):
                for item in model.remaining_actions[:3]:
                    st.write(f"- {item}")
    return ""


def _execution_state_copy(state: str) -> str:
    return {
        "starting": "Starting local Codex.",
        "active": "Codex is reviewing the scoped evidence.",
        "quiet_but_running": (
            "Codex is still running; no new progress event has arrived recently."
        ),
        "validating_output": "Validating the structured Codex response.",
        "completed": "Codex review completed.",
        "authentication_required": "Local Codex needs authentication.",
        "provider_unavailable": "Local Codex is temporarily unavailable.",
        "hard_deadline_exceeded": (
            "The Codex deadline was reached; completed research was preserved."
        ),
        "process_exit_failed": "Local Codex exited without a valid result.",
        "output_schema_failed": "The Codex response failed structured validation.",
        "cancelled": "Codex review was cancelled.",
        "interrupted_by_restart": (
            "Codex review was interrupted; completed research is preserved."
        ),
        "cli_update_required": (
            "Update the Codex CLI to enable observable local review."
        ),
    }.get(str(state or "").casefold(), "Local Codex status is updating.")


def render_decision_page(
    st: Any,
    model: DecisionBriefViewModel,
    *,
    assumptions_renderer: Callable[[], None] | None = None,
) -> DecisionPageIntent | None:
    intent: DecisionPageIntent | None = None
    st.markdown("### Decision")
    render_status_badge(st, "Research status", model.publication_label)
    st.caption(
        f"{model.run_status} · Freshness: {model.freshness} · "
        f"Evidence: {model.evidence_quality} | "
        f"Independent QA: {model.independent_qa_status}"
    )
    if model.publication_qualifications:
        st.info(
            "Residual diligence: "
            + " ".join(model.publication_qualifications[:3])
        )

    with st.container(border=True):
        render_status_badge(st, "Recommendation", model.direction)
        st.markdown(f"#### {model.verdict}")
        st.write(model.thesis)
        render_metric_cards(st, [
            ("Stage", model.stage),
            ("Recommendation", model.direction),
            ("Confidence", model.confidence),
        ])
        if model.recommendation_reason:
            st.caption(model.recommendation_reason)
        why_now, next_decision = st.columns(2)
        with why_now:
            st.markdown("**Why now**")
            st.write(model.why_now)
            render_evidence_drawer(
                st,
                model.why_now_evidence,
                label="Timing evidence and provenance",
            )
        with next_decision:
            st.markdown("**What price appears to assume**")
            st.write(model.priced_in)

    render_section_header(
        st,
        "Valuation and market-implied expectations",
        "Compare the research value range with the operating outcome embedded in the price used by this result.",
    )
    render_metric_cards(st, [
        ("Scenario range", model.valuation_range),
        ("Weighted value", model.probability_weighted_value),
        ("Expected return", model.expected_return),
        ("Valuation confidence", model.valuation_confidence),
    ])
    expectations = model.market_expectations
    if expectations is not None:
        with st.container(border=True):
            render_status_badge(
                st,
                expectations.methodology,
                expectations.status,
            )
            render_metric_cards(st, [
                (expectations.price_label, expectations.current_price),
                (
                    expectations.implied_variable_label,
                    expectations.implied_variable_value,
                ),
                (
                    expectations.internal_base_label,
                    expectations.internal_base_value,
                ),
                ("Confidence", expectations.confidence),
            ])
            st.write(expectations.interpretation)
            if expectations.disagreement.casefold() != "unknown":
                st.caption(expectations.disagreement)
            st.caption(
                f"Price as of {expectations.price_as_of} · Source: "
                f"{expectations.price_source} · Weakest input: "
                f"{expectations.weakest_input}"
            )
            with st.expander("Assumptions and sensitivity", expanded=False):
                if assumptions_renderer is not None:
                    assumptions_renderer()
                elif expectations.assumptions:
                    st.dataframe(
                        [
                            {
                                "Assumption": item.name,
                                "Value": item.value,
                                "Unit": item.unit,
                                "Source": item.source,
                                "Provenance": item.provenance,
                            }
                            for item in expectations.assumptions
                        ],
                        hide_index=True,
                        use_container_width=True,
                    )
                else:
                    render_actionable_empty_state(
                        st,
                        title="No editable assumptions",
                        reason="The active valuation methodology has no safe scenario controls.",
                    )
    else:
        render_actionable_empty_state(
            st,
            title="Market-implied expectations unavailable",
            reason="The current price or methodology inputs were not sufficient for a reverse valuation.",
        )
    if model.valuation_cases:
        with st.expander("Operating cases and valuation methods", expanded=False):
            st.dataframe(
                [
                    {
                        "Case": case.name,
                        "Probability": case.probability,
                        "Fair value": case.fair_value,
                        "Method": case.method,
                    }
                    for case in model.valuation_cases
                ],
                hide_index=True,
                use_container_width=True,
            )

    render_section_header(
        st,
        "Historical playbook",
        "Researched episodes and quarantined candidate analogs are labeled separately.",
    )
    if model.historical_analogs:
        for start in range(0, len(model.historical_analogs), 2):
            batch = model.historical_analogs[start:start + 2]
            columns = st.columns(len(batch))
            for column, analog in zip(columns, batch):
                with column.container(border=True):
                    render_status_badge(st, analog.kind, analog.status)
                    st.markdown(f"**{analog.episode}**")
                    st.caption(f"{analog.similarity} · {analog.source_quality}")
                    st.write(analog.insight)
                    st.caption(f"Material difference: {analog.material_difference}")
                    with st.expander("Limits and monitor", expanded=False):
                        st.write(f"Monitor: {analog.monitor}")
                        st.write(f"Limit: {analog.limitation}")
    else:
        render_actionable_empty_state(
            st,
            title="No credible historical playbook yet",
            reason=(
                "No issuer- and mechanism-eligible episode passed the current scope, "
                "source, and audit rules. Investigate a historical playbook when it is decision-relevant."
            ),
        )

    if model.story_lenses:
        render_section_header(
            st,
            "Company story",
            "One decision-relevant insight per lens; supporting evidence stays folded.",
        )
        for start in range(0, len(model.story_lenses), 2):
            batch = model.story_lenses[start:start + 2]
            columns = st.columns(len(batch))
            for column, lens in zip(columns, batch):
                with column.container(border=True):
                    render_status_badge(st, lens.title, lens.status)
                    st.write(lens.insight)
                    if lens.next_action:
                        st.caption(f"Next: {lens.next_action}")
                    if lens.evidence_count:
                        st.caption(
                            f"{lens.evidence_count} supporting evidence item(s); "
                            "open the Evidence view for provenance."
                        )

    if model.debate is not None:
        render_section_header(
            st,
            "Bull / Bear / Judge",
            "The judge separates what is established from what still requires evidence.",
        )
        debate_columns = st.columns(3)
        with debate_columns[0].container(border=True):
            st.markdown("**Bull case**")
            st.write(model.debate.bull_case)
        with debate_columns[1].container(border=True):
            st.markdown("**Bear case**")
            st.write(model.debate.bear_case)
        with debate_columns[2].container(border=True):
            render_status_badge(st, "Judge", model.debate.status)
            if model.debate.judge_accepts:
                st.markdown("**Accepted**")
                for item in model.debate.judge_accepts:
                    st.markdown(f"- {item}")
            if model.debate.still_unproven:
                st.markdown("**Still unproven**")
                for item in model.debate.still_unproven:
                    st.markdown(f"- {item}")
            if model.debate.action_references:
                st.caption(
                    "Resolve through " + ", ".join(model.debate.action_references)
                )
    else:
        render_section_header(st, "What could disprove it")
        render_risk_card(st, model)

    render_section_header(st, "Investigate next")
    if model.investigation_actions:
        for action in model.investigation_actions[:3]:
            with st.container(border=True):
                render_status_badge(st, f"Action {action.rank}", action.status)
                st.markdown(f"**{action.title}**")
                if action.objective.casefold() != action.title.casefold():
                    st.write(action.objective)
                st.caption(f"Why it matters: {action.why_it_matters}")
                if action.setup_steps:
                    st.caption("Step 1 of 2 — " + action.setup_steps[0])
                    st.caption("Step 2 of 2 — " + action.setup_steps[1])
                if action.attempt_summary:
                    st.caption(f"Latest attempt: {action.attempt_summary}")
                if action.blocking_reason:
                    st.info(action.blocking_reason)
                if action.primary_cta and st.button(
                    action.primary_cta,
                    key=f"investigation-action-{action.action_id}",
                    use_container_width=True,
                ):
                    intent = DecisionPageIntent(
                        action_id=action.action_id,
                        intent_type=action.intent_type,
                        event_id=action.event_id,
                    )
                if action.allow_price_upload:
                    with st.expander("Add price data", expanded=False):
                        st.caption(
                            "Upload adjusted daily closes for the company and SPY. "
                            "Include the sector benchmark for complete attribution. "
                            "Provide at least 148 pre-event observations (273 "
                            "recommended), the prior close, and 20 post-event sessions."
                        )
                        st.download_button(
                            "Download CSV template",
                            data=price_csv_template(
                                model.ticker,
                                "XLK" if model.ticker.upper() == "AAPL" else "",
                            ),
                            file_name="event_attribution_prices.csv",
                            mime="text/csv",
                            key=f"price-template-{action.action_id}",
                        )
                        upload = st.file_uploader(
                            "Price data CSV",
                            type=("csv",),
                            key=f"price-upload-{action.action_id}",
                        )
                        if upload is not None and st.button(
                            "Validate and run attribution",
                            key=f"run-uploaded-attribution-{action.action_id}",
                            use_container_width=True,
                        ):
                            intent = DecisionPageIntent(
                                action_id=action.action_id,
                                intent_type="upload_price_data",
                                event_id=action.event_id,
                                uploaded_csv=upload.getvalue(),
                            )
    else:
        for index, action in enumerate(model.next_actions[:3], start=1):
            st.markdown(f"{index}. {action}")

    return intent


def render_integrity_timeline(
    st: Any,
    model: IntegrityTimelineViewModel,
    *,
    heading: str = "Research Integrity Timeline",
) -> None:
    st.markdown(f"#### {heading}")
    render_metric_cards(st, [
        ("Status", model.status),
        ("Investigation loops", str(model.recursion_depth)),
        ("Stopping reason", model.stopping_reason),
        (
            "Estimated cost",
            "Not reported" if model.estimated_cost_usd is None else f"${model.estimated_cost_usd:,.2f}",
        ),
    ])
    for event in model.events:
        with st.container(border=True):
            render_status_badge(st, event.label, event.status)
            st.write(event.summary)
            if event.origin or event.timestamp:
                st.caption(" · ".join(value for value in (event.origin, event.timestamp) if value))
            if any(event.technical_details.values()):
                with st.expander("Technical provenance", expanded=False):
                    st.json(event.technical_details)


def render_publication_readiness(
    st: Any,
    model: PublicationReadinessViewModel,
) -> PublicationRepairIntent | None:
    st.markdown("#### Publication readiness")
    render_metric_cards(st, [
        ("Research", model.research_status),
        ("Evidence validation", model.validation_status),
        ("Independent QA", model.independent_qa_status),
        ("Publication quality", model.publication_eligibility),
    ])
    st.write(model.summary)
    if model.qualification_summaries:
        with st.expander("Residual diligence", expanded=False):
            for summary in model.qualification_summaries:
                st.markdown(f"- {summary}")
    st.caption(
        f"Semantic quality: {model.semantic_quality} · {model.progress_label}"
    )
    if model.repair_progress:
        symbols = {
            "Reused": "✓",
            "Ready to retry": "●",
            "Pending": "○",
        }
        with st.container(border=True):
            for stage in model.repair_progress:
                st.markdown(
                    f"{symbols.get(stage.status, '•')} "
                    f"**{stage.label}** · {stage.status}"
                )
            if model.execution_note:
                st.caption(model.execution_note)
    for card in model.repair_cards:
        with st.container(border=True):
            render_status_badge(st, card.title, card.status)
            st.write(card.why_it_matters)
            st.caption(f"What the app will do: {card.what_the_app_will_do}")
            if card.source_families:
                st.caption(
                    "Registered source families: "
                    + ", ".join(value.replace("_", " ") for value in card.source_families)
                )
            if card.attempts or card.result_or_blocker:
                with st.expander("Attempts and result", expanded=False):
                    st.write(card.result_or_blocker or "No result has been recorded.")
                    st.caption(f"Attempts: {card.attempts}")
    if model.can_repair:
        if st.button(
            model.primary_action_label,
            type="primary",
            use_container_width=True,
            key="publication-repair-and-recheck",
        ):
            return PublicationRepairIntent(
                intent_type="repair_and_recheck",
                action_ids=tuple(
                    card.action_id for card in model.repair_cards if card.action_id
                ),
                retry_stages=(
                    ("closure_checker",)
                    if model.primary_action_label
                    == "Retry Independent QA closure"
                    else ()
                ),
            )
    elif not model.publication_eligible:
        if model.validation_status.strip().lower() == "passed":
            empty_reason = (
                "Run live Independent Research QA and the current publication "
                "evaluation to generate a repair plan for this artifact."
            )
        else:
            empty_reason = (
                "Run current deterministic validation to generate typed repair "
                "actions before rechecking publication quality."
            )
        render_actionable_empty_state(
            st,
            title="No executable repair plan",
            reason=empty_reason,
        )
    return None
