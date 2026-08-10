from __future__ import annotations

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
    ModelValuationViewModel,
    PublicationReadinessViewModel,
    PublicationRepairIntent,
    ResearchRouteViewModel,
    ResearchRequestCardViewModel,
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
            request.status.replace("_", " ").title(),
        )
        st.write(request.objective)
        if request.source_families:
            st.caption("Source families: " + ", ".join(request.source_families))
        with st.expander("Request details", expanded=False):
            if request.confirming_evidence:
                st.write("Confirming evidence sought: " + "; ".join(request.confirming_evidence))
            if request.contradicting_evidence:
                st.write("Contradicting evidence sought: " + "; ".join(request.contradicting_evidence))
            if request.attempted_connectors:
                st.write("Connectors attempted: " + ", ".join(request.attempted_connectors))
            if request.stopping_rule:
                st.write("Stopping rule: " + request.stopping_rule)


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


def render_evidence_page_overview(st: Any, model: EvidencePageViewModel) -> None:
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
    render_section_header(st, "Targeted Research Requests")
    if model.requests:
        for request in model.requests[:8]:
            render_research_request_card(st, request)
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
    if model.candidates:
        for candidate in model.candidates[:12]:
            render_candidate_status_row(st, candidate)
        if len(model.candidates) > 12:
            st.caption(f"Showing 12 of {len(model.candidates)} candidates.")
    else:
        render_actionable_empty_state(
            st,
            title="No Sandbox candidates",
            reason="Candidate discovery was unnecessary, not attempted, or produced no valid metadata leads.",
        )


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
        render_status_badge(st, "Investment conclusion", model.confidence)
        st.markdown(f"#### {model.verdict}")
        st.write(model.thesis)
        render_metric_cards(st, [
            ("Stage", model.stage),
            ("Direction", model.direction),
            ("Confidence", model.confidence),
        ])
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
            title="No historical analogs",
            reason="No episode passed the current discovery and source-quality rules.",
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
            "Repair and recheck",
            type="primary",
            use_container_width=True,
            key="publication-repair-and-recheck",
        ):
            return PublicationRepairIntent(
                intent_type="repair_and_recheck",
                action_ids=tuple(
                    card.action_id for card in model.repair_cards if card.action_id
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
