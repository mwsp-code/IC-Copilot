from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from statistics import mean

from . import config
from .idea_engine import expected_value
from .models import (
    CalibrationReport,
    CalibrationReadinessCheck,
    CalibrationSlice,
    ChangeEvent,
    ConsensusPackage,
    DataQualityIssue,
    DataQualityReport,
    EvidenceClaim,
    EvidenceItem,
    EvidenceLedger,
    ExpectationsBridge,
    FilingRecord,
    FinancialMetric,
    ManagementCredibility,
    ManagementCrossCheck,
    ManagementPromise,
    RunManifest,
    ResearchSourcePlan,
    LlmExtractionManifest,
    TradeIdea,
    TranscriptComparison,
)
from .sentiment import score_text, tone_shift_summary
from .providers import PriceReaction
from .research_store import ResearchStore


def build_evidence_ledger(
    ticker: str,
    ideas: list[TradeIdea],
    events: list[ChangeEvent],
    management_cross_checks: list[ManagementCrossCheck] | None = None,
) -> EvidenceLedger:
    ledger = EvidenceLedger()
    cross_checks_by_claim: dict[str, list[ManagementCrossCheck]] = {}
    for check in management_cross_checks or []:
        cross_checks_by_claim.setdefault(check.claim_id, []).append(check)
    for idea in ideas:
        if not idea.source_events:
            continue
        source_event = idea.source_events[0]
        claim_id = _stable_id("claim", ticker, idea.idea_id, source_event.title)
        support_ids: list[str] = []
        contradiction_ids: list[str] = []
        citations = source_event.citations or [None]
        for citation in citations:
            evidence_id = _stable_id("evidence", claim_id, "support", citation.url if citation else source_event.source)
            ledger.items.append(EvidenceItem(
                evidence_id=evidence_id,
                claim_id=claim_id,
                ticker=ticker.upper(),
                stance="Supports",
                statement=(citation.snippet if citation and citation.snippet else source_event.summary),
                source_tier=_source_tier(citation, source_event.source),
                source_type=source_event.source,
                materiality=source_event.severity,
                citation=citation,
                observed_at=citation.retrieved_at if citation else None,
            ))
            support_ids.append(evidence_id)

        contradictions = _contradictions_for_idea(idea, events)
        for statement, materiality, citation, source_type in contradictions:
            evidence_id = _stable_id("evidence", claim_id, "contradicts", statement)
            ledger.items.append(EvidenceItem(
                evidence_id=evidence_id, claim_id=claim_id, ticker=ticker.upper(),
                stance="Contradicts", statement=statement,
                source_tier=_source_tier(citation, source_type), source_type=source_type,
                materiality=materiality, citation=citation, unresolved=materiality >= 3,
            ))
            contradiction_ids.append(evidence_id)

        management_claim_id = str(source_event.metrics.get("management_claim_id") or "")
        for check in cross_checks_by_claim.get(management_claim_id, []):
            if check.status not in {"Confirmed", "Contradicted", "Too vague"}:
                continue
            stance = "Supports" if check.status == "Confirmed" else "Contradicts"
            evidence_id = _stable_id("evidence", claim_id, "management_cross_check", check.check_id)
            ledger.items.append(EvidenceItem(
                evidence_id=evidence_id,
                claim_id=claim_id,
                ticker=ticker.upper(),
                stance=stance,
                statement=check.summary,
                source_tier=check.source_tier,
                source_type=check.source_type,
                materiality=check.materiality,
                citation=check.citation,
                unresolved=stance == "Contradicts" and check.materiality >= 3,
            ))
            if stance == "Supports":
                support_ids.append(evidence_id)
            else:
                contradiction_ids.append(evidence_id)

        material_counters = [item for item in ledger.items if item.claim_id == claim_id and item.stance == "Contradicts" and item.materiality >= 3]
        strongest = max(material_counters, key=lambda item: item.materiality).statement if material_counters else None
        ledger.claims.append(EvidenceClaim(
            claim_id=claim_id, idea_id=idea.idea_id, text=idea.thesis,
            status="Contradicted" if strongest else "Supported" if support_ids else "Unsubstantiated",
            supporting_evidence_ids=support_ids,
            contradicting_evidence_ids=contradiction_ids,
            strongest_counter=strongest,
        ))

    counters = [item for item in ledger.items if item.stance == "Contradicts" and item.materiality >= 3]
    ledger.unresolved_material_contradictions = len(counters)
    if counters:
        ledger.strongest_counter_thesis = max(counters, key=lambda item: item.materiality).statement
    return ledger


def apply_evidence_score_caps(ideas: list[TradeIdea], ledger: EvidenceLedger) -> None:
    items_by_claim = {claim.claim_id: [item for item in ledger.items if item.claim_id == claim.claim_id] for claim in ledger.claims}
    claims_by_idea = {claim.idea_id: claim for claim in ledger.claims}
    for idea in ideas:
        if not idea.score:
            continue
        claim = claims_by_idea.get(idea.idea_id)
        items = items_by_claim.get(claim.claim_id, []) if claim else []
        has_primary_support = any(item.stance == "Supports" and item.source_tier == 1 for item in items)
        unresolved_counter_item = next((
            item for item in items
            if item.stance == "Contradicts" and item.materiality >= 3 and item.unresolved
        ), None)
        unresolved_counter = bool(unresolved_counter_item)
        cap = None
        reason = None
        if not has_primary_support:
            cap, reason = 55, "No Tier 1 supporting evidence."
        elif unresolved_counter:
            reason = f"Unresolved contradiction: {unresolved_counter_item.statement}"
            cap = 70
        if cap is not None:
            idea.score.total = min(idea.score.total, cap)
            idea.score.score_cap = cap
            idea.score.score_cap_reason = reason
            idea.score.rationale.append(f"Score capped at {cap}: {reason}")
        if idea.market_capture and not idea.market_capture.consensus_official:
            idea.score.rationale.append(
                "Official consensus is unavailable, so market-capture confidence is limited."
            )


def build_data_quality_report(
    events: list[ChangeEvent],
    ideas: list[TradeIdea],
    consensus: ConsensusPackage,
) -> DataQualityReport:
    material = [event for event in events if event.severity >= 3]
    primary = [
        event for event in material
        if any((citation.source_tier or _source_tier(citation, event.source)) == 1 for citation in event.citations)
    ]
    coverage = len(primary) / len(material) * 100 if material else 0.0
    official_consensus = any(
        status.official and status.status != "Unavailable" for status in consensus.provider_statuses
    ) or bool(consensus.target and consensus.target.official)
    point_in_time = bool(ideas) and all(
        idea.market_capture
        and idea.market_capture.price_reaction_pct is not None
        and idea.market_capture.consensus_revision_pct is not None
        for idea in ideas
    )
    issues: list[DataQualityIssue] = []
    if coverage < 80:
        issues.append(DataQualityIssue("primary_coverage", "high", f"Only {coverage:.0f}% of material events have Tier 1 citations."))
    if not official_consensus:
        issues.append(DataQualityIssue("official_consensus", "high", "No official consensus observation is available."))
    if not point_in_time:
        issues.append(DataQualityIssue("point_in_time", "medium", "At least one idea lacks event-date price or consensus history."))
    for status in consensus.provider_statuses:
        if status.status == "Unavailable":
            issues.append(DataQualityIssue("provider_unavailable", "low", status.message or status.status, provider=status.provider))
    score = max(0, round(coverage * 0.45 + (30 if official_consensus else 0) + (25 if point_in_time else 0) - max(0, len(issues) - 2) * 3))
    status = "Strong" if score >= 80 else "Partial" if score >= 55 else "Weak"
    return DataQualityReport(score, status, coverage, point_in_time, official_consensus, issues)


def compare_transcripts(rows: list[dict]) -> TranscriptComparison:
    if not rows:
        return TranscriptComparison("Unavailable", data_gaps=["No transcript provider returned content."])
    current = rows[0]
    previous = rows[1] if len(rows) > 1 else None
    current_text = _transcript_text(current)
    previous_text = _transcript_text(previous) if previous else ""
    if not current_text:
        return TranscriptComparison("Unavailable", data_gaps=["Latest transcript has no readable text."])
    priorities = (
        "artificial intelligence", "cloud", "margin", "cost reduction", "capital return",
        "buyback", "international", "regulation", "pricing", "market share", "customer concentration",
    )
    current_terms = {term for term in priorities if term in current_text.lower()}
    previous_terms = {term for term in priorities if term in previous_text.lower()}
    evasive = _sentences_matching(current_text, (
        "we do not disclose", "we don't disclose", "not going to comment",
        "too early to", "cannot provide", "not prepared to",
    ))
    current_promises = set(_sentences_matching(current_text, ("we expect", "we will", "we plan", "our target")))
    previous_promises = set(_sentences_matching(previous_text, ("we expect", "we will", "we plan", "our target")))
    current_scores = _transcript_scores(current, current_text)
    previous_scores = _transcript_scores(previous, previous_text) if previous else {}
    sentiment_shift = _shift(current_scores.get("sentiment_score"), previous_scores.get("sentiment_score"))
    uncertainty_shift = _shift(current_scores.get("uncertainty_score"), previous_scores.get("uncertainty_score"))
    evasion_shift = _shift(current_scores.get("evasion_score"), previous_scores.get("evasion_score"))
    specificity_shift = _shift(current_scores.get("specificity_score"), previous_scores.get("specificity_score"))
    return TranscriptComparison(
        "Available", current_period=_row_period(current), previous_period=_row_period(previous) if previous else None,
        new_priorities=sorted(current_terms - previous_terms),
        removed_priorities=sorted(previous_terms - current_terms),
        evasive_qa_flags=evasive[:8], repeated_promises=sorted(current_promises & previous_promises)[:8],
        data_gaps=[] if previous else ["No prior transcript was available for comparison."],
        current_sentiment_score=current_scores.get("sentiment_score"),
        previous_sentiment_score=previous_scores.get("sentiment_score"),
        sentiment_shift=sentiment_shift,
        current_uncertainty_score=current_scores.get("uncertainty_score"),
        previous_uncertainty_score=previous_scores.get("uncertainty_score"),
        uncertainty_shift=uncertainty_shift,
        current_evasion_score=current_scores.get("evasion_score"),
        previous_evasion_score=previous_scores.get("evasion_score"),
        evasion_shift=evasion_shift,
        current_specificity_score=current_scores.get("specificity_score"),
        previous_specificity_score=previous_scores.get("specificity_score"),
        specificity_shift=specificity_shift,
        tone_shift_summary=tone_shift_summary(sentiment_shift, uncertainty_shift, evasion_shift, specificity_shift),
    )


def build_management_credibility(
    expectations: ExpectationsBridge,
    metrics: list[FinancialMetric],
    transcript_rows: list[dict],
) -> ManagementCredibility:
    metric_map = {(metric.name.lower(), metric.period_end): metric for metric in metrics}
    promises: list[ManagementPromise] = []
    for guidance in expectations.numeric_guidance:
        outcome_metric = metric_map.get((guidance.metric.lower(), guidance.period_end or ""))
        outcome = outcome_metric.value if outcome_metric else None
        status = "Unresolved"
        if outcome is not None:
            if guidance.low is not None and outcome < guidance.low:
                status = "Missed"
            elif guidance.high is not None and outcome > guidance.high:
                status = "Exceeded"
            else:
                status = "Kept"
        promises.append(ManagementPromise(
            _stable_id("promise", guidance.metric, guidance.period_end, guidance.citation.snippet),
            guidance.citation.snippet or f"{guidance.metric} guidance",
            guidance.metric, guidance.period_end, guidance.low, guidance.high,
            outcome, status, guidance.citation,
        ))
    resolved = [promise for promise in promises if promise.status != "Unresolved"]
    kept = [promise for promise in resolved if promise.status in {"Kept", "Exceeded"}]
    missed = [promise for promise in resolved if promise.status == "Missed"]
    score = len(kept) / len(resolved) * 100 if resolved else None
    comparison = compare_transcripts(transcript_rows)
    gaps = []
    if not promises:
        gaps.append("No citation-backed numeric guidance history is available.")
    gaps.extend(comparison.data_gaps)
    return ManagementCredibility(
        "Available" if promises or comparison.status == "Available" else "Unavailable",
        score, len(promises), len(resolved), len(kept), len(missed), promises, comparison, gaps,
    )


def build_run_manifest(
    ticker: str,
    filings: list[FilingRecord],
    metrics: list[FinancialMetric],
    events: list[ChangeEvent],
    consensus: ConsensusPackage,
    extra_gaps: list[str],
    source_plan: ResearchSourcePlan | None = None,
    llm_extraction_manifest: LlmExtractionManifest | None = None,
    research_profile_summary: dict | None = None,
    effective_history_summary: dict | None = None,
) -> RunManifest:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    reproducible_input = {
        "ticker": ticker.upper(),
        "research_profile": research_profile_summary or {},
        "effective_history": effective_history_summary or {},
        "filings": [(filing.accession, filing.filing_date, filing.report_date) for filing in filings],
        "metrics": [(metric.name, metric.period_end, metric.value, metric.unit, metric.filed) for metric in metrics],
        "events": [(event.category, event.title, event.event_date, event.direction) for event in events],
        "observations": [
            (item.provider, item.field, item.source_as_of, item.value_numeric, item.value_text)
            for item in consensus.observations
        ],
    }
    canonical = json.dumps(reproducible_input, sort_keys=True, separators=(",", ":"), default=str)
    run_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    source_urls = sorted({filing.url for filing in filings} | {
        citation.url for event in events for citation in event.citations if citation.url
    })
    retrieval_times = {url: generated_at for url in source_urls}
    for event in events:
        for citation in event.citations:
            if citation.url and citation.retrieved_at:
                retrieval_times[citation.url] = citation.retrieved_at
    for observation in consensus.observations:
        retrieval_times[f"provider:{observation.provider}:{observation.field}"] = observation.observed_at
    assumptions = [
        "Daily closing data; no intraday consensus inference.",
        "External analyst targets are comparison benchmarks, not internal fair-value inputs.",
        "Scenario probabilities remain uncalibrated until the minimum sample is reached.",
    ]
    return RunManifest(
        run_id, generated_at, config.APP_VERSION,
        {
            "filing_parser": "2", "consensus_normalizer": "3",
            "valuation": "3", "transcript_comparator": "1", "event_study": "1",
            "claim_validator": "1", "source_planner": "1",
        },
        source_urls, assumptions, sorted(set(consensus.data_gaps + extra_gaps)),
        retrieval_times,
        source_plan_summary=(
            {
                "status": source_plan.status,
                "registry_version": source_plan.registry_version,
                "request_count": len(source_plan.requests),
                "provider": source_plan.provider,
            }
            if source_plan else {}
        ),
        llm_extraction_summary=(
            {
                "status": llm_extraction_manifest.status,
                "provider": llm_extraction_manifest.provider,
                "model": llm_extraction_manifest.model,
                "prompt_version": llm_extraction_manifest.prompt_version,
                "validated_claim_count": len(llm_extraction_manifest.validated_claim_ids),
                "source_plan_request_count": len(llm_extraction_manifest.source_plan_request_ids),
            }
            if llm_extraction_manifest else {}
        ),
        research_profile_summary=research_profile_summary or {},
        effective_history_summary=effective_history_summary or {},
    )


def build_calibration_report(store: ResearchStore, ticker: str | None = None) -> CalibrationReport:
    outcome_rows = store.realized_outcome_rows(ticker)
    process = _post_mortem_process_stats(outcome_rows)
    rows = [
        row for row in store.event_signal_rows(ticker)
        if row.get("realized_return_pct") is not None
        and row.get("stage") in {"Research-Ready", "High-Conviction", "Investable"}
    ]
    minimum = config.CALIBRATION_MIN_SAMPLE
    if not rows:
        readiness_checks = _calibration_readiness_checks([], process, minimum, "", 0, minimum, False)
        return CalibrationReport(
            "Uncalibrated", 0, minimum, None, None, None,
            data_gaps=["No resolved point-in-time event outcomes are stored."],
            outcomes_needed_for_calibration=minimum,
            required_outcome_fields=_required_outcome_fields(),
            calibration_actions=_calibration_actions(minimum, "", 0),
            readiness_checks=readiness_checks,
            readiness_score=_calibration_readiness_score(readiness_checks),
            **process,
        )
    hits = []
    brier_values = []
    absolute_errors = []
    for row in rows:
        directional = row["realized_return_pct"] if row["direction"] != "negative" else -row["realized_return_pct"]
        outcome = 1.0 if directional > 0 else 0.0
        hits.append(outcome)
        probability = row.get("predicted_success_probability")
        if probability is not None:
            brier_values.append((probability - outcome) ** 2)
        expected = row.get("expected_return_pct")
        if expected is not None:
            absolute_errors.append(abs(expected - directional))
    slices: list[CalibrationSlice] = []
    slice_keys = sorted({(row["signal_type"], row.get("horizon_label") or f"{row.get('horizon_days', 20)}d") for row in rows})
    calibrated_slices = 0
    for signal_type, horizon_label in slice_keys:
        selected = [
            row for row in rows
            if row["signal_type"] == signal_type
            and (row.get("horizon_label") or f"{row.get('horizon_days', 20)}d") == horizon_label
        ]
        selected_hits = [
            1.0 if (row["realized_return_pct"] if row["direction"] != "negative" else -row["realized_return_pct"]) > 0 else 0.0
            for row in selected
        ]
        slice_needed = max(0, minimum - len(selected))
        slice_status = "Calibrated" if slice_needed == 0 else "Building sample"
        slices.append(CalibrationSlice(
            f"{signal_type} | {horizon_label}", len(selected), mean(selected_hits) * 100,
            _expanding_window_brier(selected, minimum), _mean_optional(selected, "expected_return_pct"),
            _mean_optional(selected, "realized_return_pct"),
            min((row["max_adverse_excursion_pct"] for row in selected if row["max_adverse_excursion_pct"] is not None), default=None),
            status=slice_status,
            outcomes_needed_for_calibration=slice_needed,
            rank_by_ev_allowed=slice_status == "Calibrated",
            next_action=(
                "EV ranking may use calibrated probabilities for this comparable slice."
                if slice_status == "Calibrated" else
                f"Record {slice_needed} more resolved outcome(s) in this signal-family/horizon slice."
            ),
        ))
        if len(selected) >= minimum:
            calibrated_slices += 1
    status = "Calibrated" if calibrated_slices else "Uncalibrated"
    largest_slice = max((item.sample_size for item in slices), default=0)
    nearest = max(slices, key=lambda item: item.sample_size, default=None)
    outcomes_needed = max(0, minimum - largest_slice)
    gaps = [] if status == "Calibrated" else [
        f"Need {outcomes_needed} more resolved outcomes in one signal-family/horizon slice before probabilities are calibrated."
    ]
    readiness_checks = _calibration_readiness_checks(
        rows,
        process,
        minimum,
        nearest.signal_type if nearest else "",
        largest_slice,
        outcomes_needed,
        status == "Calibrated",
    )
    return CalibrationReport(
        status, len(rows), minimum, mean(hits) * 100,
        mean(brier_values) if brier_values else None,
        mean(absolute_errors) if absolute_errors else None,
        slices, gaps,
        nearest_calibration_slice=nearest.signal_type if nearest else "",
        nearest_calibration_sample_size=nearest.sample_size if nearest else 0,
        outcomes_needed_for_calibration=outcomes_needed,
        rank_by_ev_allowed=status == "Calibrated",
        required_outcome_fields=_required_outcome_fields(),
        calibration_actions=_calibration_actions(outcomes_needed, nearest.signal_type if nearest else "", largest_slice),
        readiness_checks=readiness_checks,
        readiness_score=_calibration_readiness_score(readiness_checks),
        **process,
    )


def _calibration_readiness_checks(
    rows: list[dict],
    process: dict,
    minimum: int,
    nearest_slice: str,
    nearest_count: int,
    outcomes_needed: int,
    rank_by_ev_allowed: bool,
) -> list[CalibrationReadinessCheck]:
    sample_status = "Passed" if rank_by_ev_allowed else "Missing" if not rows else "Partial"
    checks = [
        CalibrationReadinessCheck(
            area="Comparable outcome sample",
            status=sample_status,
            score=100 if rank_by_ev_allowed else 55 if rows else 15,
            summary=f"Nearest comparable slice {nearest_slice or 'n/a'} has {nearest_count}/{minimum} resolved outcome(s).",
            evidence=[f"Resolved outcomes: {len(rows)}", f"Nearest slice size: {nearest_count}"],
            gaps=[] if rank_by_ev_allowed else [f"Need {outcomes_needed} more resolved outcome(s) in one signal-family/horizon slice."],
            next_action=(
                "Use calibrated probabilities only for eligible slices and keep monitoring drift."
                if rank_by_ev_allowed else
                "Keep recording outcomes for one comparable signal-family/horizon slice before enabling EV ranking."
            ),
            stage_impact=(
                "Calibrated EV ranking may be used for eligible ideas."
                if rank_by_ev_allowed else
                "Illustrative EV remains visible but unranked."
            ),
        )
    ]

    review_status = str(process.get("post_mortem_quality_status") or "Unavailable")
    review_score = {
        "Complete": 100,
        "Partial": 70,
        "Incomplete": 45,
        "Missing": 20,
        "Unavailable": 10,
    }.get(review_status, 40)
    checks.append(CalibrationReadinessCheck(
        area="Post-mortem quality",
        status=review_status,
        score=review_score,
        summary=(
            f"{process.get('complete_post_mortem_count', 0)} complete review(s); "
            f"{process.get('post_mortem_count', 0)} reviewed outcome(s)."
        ),
        evidence=[
            f"Post-mortem coverage: {_format_pct(process.get('post_mortem_coverage_pct'))}",
            f"Complete coverage: {_format_pct(process.get('complete_post_mortem_coverage_pct'))}",
        ],
        gaps=list(process.get("post_mortem_quality_gaps") or []),
        next_action=(
            "Maintain complete post-mortems for every resolved idea."
            if review_status == "Complete" else
            "Fill closure reason, evidence-valid review, what worked/failed, lesson, and process-change fields."
        ),
        stage_impact="Incomplete post-mortems weaken calibration interpretability even when returns are recorded.",
    ))

    expected_rows = [row for row in rows if row.get("expected_return_pct") is not None]
    probability_rows = [row for row in rows if row.get("predicted_success_probability") is not None]
    forecast_complete = bool(rows) and len(expected_rows) == len(rows) and len(probability_rows) == len(rows)
    checks.append(CalibrationReadinessCheck(
        area="Forecast fields",
        status="Passed" if forecast_complete else "Partial" if rows else "Missing",
        score=100 if forecast_complete else 60 if rows else 10,
        summary=(
            f"Expected-return fields on {len(expected_rows)}/{len(rows)} outcome(s); "
            f"probability fields on {len(probability_rows)}/{len(rows)} outcome(s)."
        ),
        evidence=[f"Rows with expected return: {len(expected_rows)}", f"Rows with probability: {len(probability_rows)}"],
        gaps=[] if forecast_complete else [
            "Store expected return and probability provenance on every frozen idea version before outcome resolution."
        ],
        next_action="Freeze expected return, probability provenance, scenario assumptions, and entry data before monitoring starts.",
        stage_impact="Missing forecast fields block Brier/error analysis and prevent credible calibrated EV ranking.",
    ))

    learning_items = (
        len(process.get("recurring_failure_modes") or [])
        + len(process.get("recurring_lessons") or [])
        + len(process.get("process_improvement_actions") or [])
    )
    checks.append(CalibrationReadinessCheck(
        area="Learning loop",
        status="Passed" if learning_items >= 3 else "Partial" if learning_items else "Missing",
        score=90 if learning_items >= 3 else 55 if learning_items else 15,
        summary=f"{learning_items} recurring lesson/failure/process-improvement item(s) captured.",
        evidence=[
            f"Failure modes: {len(process.get('recurring_failure_modes') or [])}",
            f"Lessons: {len(process.get('recurring_lessons') or [])}",
            f"Process changes: {len(process.get('process_improvement_actions') or [])}",
        ],
        gaps=[] if learning_items else ["Resolved outcomes have not yet produced reusable process lessons."],
        next_action="Promote recurring lessons into source-checklists, gates, or import requirements after each review cycle.",
        stage_impact="The app becomes more convincing when past mistakes change the future research process.",
    ))
    return checks


def _calibration_readiness_score(checks: list[CalibrationReadinessCheck]) -> int:
    if not checks:
        return 0
    return round(sum(item.score for item in checks) / len(checks))


def _format_pct(value: object) -> str:
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def _post_mortem_process_stats(rows: list[dict]) -> dict:
    if not rows:
        return {
            "post_mortem_count": 0,
            "post_mortem_coverage_pct": None,
            "complete_post_mortem_count": 0,
            "complete_post_mortem_coverage_pct": None,
            "incomplete_post_mortem_count": 0,
            "post_mortem_quality_status": "Unavailable",
            "post_mortem_quality_gaps": ["No stored outcomes are available for post-mortem quality checks."],
            "evidence_valid_rate_pct": None,
            "recurring_lessons": [],
            "recurring_failure_modes": [],
            "process_improvement_actions": [],
        }
    reviewed = [
        row for row in rows
        if any(str(row.get(key) or "").strip() for key in (
            "evidence_valid", "what_worked", "what_failed", "lessons", "next_process_change"
        ))
    ]
    complete = [row for row in rows if not _post_mortem_missing_fields(row)]
    incomplete = [row for row in reviewed if _post_mortem_missing_fields(row)]
    evidence_rows = [
        str(row.get("evidence_valid") or "").strip().lower()
        for row in rows
        if str(row.get("evidence_valid") or "").strip().lower() in {"yes", "partly", "no"}
    ]
    valid_count = sum(1 for value in evidence_rows if value in {"yes", "partly"})
    quality_gaps = _post_mortem_quality_gaps(rows, reviewed, complete)
    return {
        "post_mortem_count": len(reviewed),
        "post_mortem_coverage_pct": len(reviewed) / len(rows) * 100 if rows else None,
        "complete_post_mortem_count": len(complete),
        "complete_post_mortem_coverage_pct": len(complete) / len(rows) * 100 if rows else None,
        "incomplete_post_mortem_count": len(incomplete),
        "post_mortem_quality_status": _post_mortem_quality_status(rows, reviewed, complete),
        "post_mortem_quality_gaps": quality_gaps,
        "evidence_valid_rate_pct": valid_count / len(evidence_rows) * 100 if evidence_rows else None,
        "recurring_lessons": _top_text(rows, "lessons"),
        "recurring_failure_modes": _top_text(rows, "what_failed"),
        "process_improvement_actions": _top_text(rows, "next_process_change"),
    }


def _post_mortem_missing_fields(row: dict) -> list[str]:
    required = [
        "thesis_outcome",
        "closure_reason",
        "evidence_valid",
        "what_worked",
        "what_failed",
        "lessons",
        "next_process_change",
    ]
    missing = [key for key in required if not str(row.get(key) or "").strip()]
    if str(row.get("evidence_valid") or "").strip().lower() not in {"yes", "partly", "no"}:
        missing.append("evidence_valid_yes_partly_no")
    return _dedupe_strings(missing)


def _post_mortem_quality_status(rows: list[dict], reviewed: list[dict], complete: list[dict]) -> str:
    if not rows:
        return "Unavailable"
    if len(complete) == len(rows):
        return "Complete"
    if complete:
        return "Partial"
    if reviewed:
        return "Incomplete"
    return "Missing"


def _post_mortem_quality_gaps(rows: list[dict], reviewed: list[dict], complete: list[dict]) -> list[str]:
    if not rows:
        return ["No stored outcomes are available for post-mortem quality checks."]
    gaps: list[str] = []
    if not reviewed:
        gaps.append("Resolved outcomes exist, but no post-mortem review fields are populated.")
    incomplete = [row for row in rows if _post_mortem_missing_fields(row)]
    if incomplete:
        field_counts: Counter[str] = Counter()
        for row in incomplete:
            field_counts.update(_post_mortem_missing_fields(row))
        fields = ", ".join(field for field, _count in field_counts.most_common(6))
        gaps.append(f"{len(incomplete)} outcome(s) are missing learning fields: {fields}.")
    if complete and len(complete) < len(rows):
        gaps.append("Some outcomes are learning-grade, but incomplete post-mortems reduce calibration interpretability.")
    return gaps[:5]


def _required_outcome_fields() -> list[str]:
    return [
        "realized_return_pct",
        "max_adverse_excursion_pct",
        "max_favorable_excursion_pct",
        "thesis_outcome",
        "closure_reason",
        "evidence_valid",
        "what_worked",
        "what_failed",
        "lessons",
        "next_process_change",
    ]


def _calibration_actions(outcomes_needed: int, nearest_slice: str, nearest_count: int) -> list[str]:
    if outcomes_needed <= 0:
        return [
            "Calibration threshold reached for at least one signal-family/horizon slice; EV ranking may use calibrated probabilities for eligible ideas.",
            "Continue recording post-mortems to monitor drift, Brier score, and expected-versus-realized return.",
        ]
    slice_note = (
        f"Current nearest slice is {nearest_slice} with {nearest_count} resolved outcome(s). "
        if nearest_slice else
        "No signal-family/horizon slice has resolved outcomes yet. "
    )
    return [
        slice_note + f"Record {outcomes_needed} more resolved outcome(s) in one comparable slice before enabling calibrated EV ranking.",
        "Freeze every Research-Ready or High-Conviction idea with entry price, event date, thesis chain, assumptions, and monitor rules before outcome tracking.",
        "At close, record realized return, max adverse/favorable excursion, thesis outcome, closure reason, and whether the original evidence was valid.",
    ]


def _top_text(rows: list[dict], key: str, limit: int = 5) -> list[str]:
    values = [str(row.get(key) or "").strip() for row in rows if str(row.get(key) or "").strip()]
    counts = Counter(values)
    return [text for text, _count in counts.most_common(limit)]


def _dedupe_strings(items: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            rows.append(clean)
            seen.add(clean)
    return rows


def record_event_studies(
    store: ResearchStore,
    ticker: str,
    ideas: list[TradeIdea],
    reactions: dict[str, PriceReaction],
) -> None:
    for idea in ideas:
        if idea.stage not in {"Research-Ready", "High-Conviction", "Investable"}:
            continue
        if not idea.source_events:
            continue
        event = idea.source_events[0]
        reaction = reactions.get(event.event_date or "")
        positive_probability = sum(
            scenario.probability for scenario in idea.scenarios
            if (scenario.net_return_pct if scenario.net_return_pct is not None else scenario.upside_downside_pct) > 0
        )
        adverse = None
        if reaction:
            adverse = (
                -reaction.path_max_20d_pct
                if event.direction == "negative" and reaction.path_max_20d_pct is not None
                else reaction.path_min_20d_pct
            )
        favorable = None
        if reaction:
            favorable = (
                -reaction.path_min_20d_pct
                if event.direction == "negative" and reaction.path_min_20d_pct is not None
                else reaction.path_max_20d_pct
            )
        probability_source = (
            idea.probability_provenance.source
            if idea.probability_provenance else "illustrative_default"
        )
        store.record_event_signal(
            f"{ticker.upper()}:{idea.idea_id}:{event.event_date or 'unknown'}",
            ticker, event.category, event.event_date, event.direction,
            expected_value(idea.scenarios), positive_probability,
            reaction.return_20d_pct if reaction else None,
            reaction.abnormal_20d_pct if reaction else None,
            adverse,
            favorable,
            20,
            probability_source,
            idea.stage,
            idea.horizon,
        )


def _contradictions_for_idea(idea: TradeIdea, events: list[ChangeEvent]):
    source = idea.source_events[0]
    contradictions = []
    for event in events:
        if event is source or event.severity < 3:
            continue
        same_metric = source.metrics.get("metric_name") and source.metrics.get("metric_name") == event.metrics.get("metric_name")
        if (event.category == source.category or same_metric) and _opposite(source.direction, event.direction):
            contradictions.append((event.summary, event.severity, event.citations[0] if event.citations else None, event.source))
    for peer in idea.peer_readthrough:
        if peer.relation == "Contradicting read-through":
            statement = peer.conclusion or f"{peer.peer_ticker} contradicts the source signal."
            contradictions.append((statement, 3, peer.citations[0] if peer.citations else None, f"Peer {peer.peer_ticker}"))
    return contradictions


def _source_tier(citation, source: str) -> int:
    if citation and citation.source_tier:
        return citation.source_tier
    lower = source.lower()
    if any(token in lower for token in ("sec", "10-k", "10-q", "8-k", "20-f", "6-k", "regulator")):
        return 1
    if any(token in lower for token in ("transcript", "issuer", "fred", "treasury", "stooq")):
        return 2
    if any(token in lower for token in ("fmp", "alpha vantage", "finnhub", "consensus", "news")):
        return 3
    return 4


def _opposite(first: str, second: str) -> bool:
    return {first, second} == {"positive", "negative"}


def _stable_id(*parts) -> str:
    value = ":".join(str(part or "") for part in parts)
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:14]


def _transcript_text(row: dict | None) -> str:
    if not row:
        return ""
    for key in ("text", "content", "body", "transcript"):
        value = row.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(
                " ".join(item.get("speech", [])) if isinstance(item, dict) and isinstance(item.get("speech"), list)
                else str(item)
                for item in value
            )
    return ""


def _transcript_scores(row: dict | None, text: str) -> dict[str, float | None]:
    if not row:
        return {}
    direct = {
        "sentiment_score": _float(row.get("sentiment_score")),
        "uncertainty_score": _float(row.get("uncertainty_score")),
        "evasion_score": _float(row.get("evasion_score")),
        "specificity_score": _float(row.get("specificity_score")),
    }
    if any(value is not None for value in direct.values()):
        return direct
    result = score_text(text)
    return {
        "sentiment_score": result.score,
        "uncertainty_score": float(len(result.uncertainty_terms)),
        "evasion_score": float(len(result.evasion_terms)),
        "specificity_score": result.specificity_score,
    }


def _shift(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 3)


def _float(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _row_period(row: dict | None) -> str | None:
    if not row:
        return None
    return str(row.get("period") or row.get("date") or row.get("quarter") or "") or None


def _sentences_matching(text: str, phrases: tuple[str, ...]) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [sentence.strip()[:400] for sentence in sentences if any(phrase in sentence.lower() for phrase in phrases)]


def _mean_optional(rows: list[dict], field: str) -> float | None:
    values = [row[field] for row in rows if row.get(field) is not None]
    return mean(values) if values else None


def _slice_brier(rows: list[dict]) -> float | None:
    values = []
    for row in rows:
        probability = row.get("predicted_success_probability")
        realized = row.get("realized_return_pct")
        if probability is None or realized is None:
            continue
        directional = realized if row.get("direction") != "negative" else -realized
        values.append((probability - (1.0 if directional > 0 else 0.0)) ** 2)
    return mean(values) if values else None


def _expanding_window_brier(rows: list[dict], minimum: int) -> float | None:
    ordered = sorted(rows, key=lambda row: row.get("observed_at") or "")
    outcomes = [
        1.0 if (
            row["realized_return_pct"]
            if row.get("direction") != "negative"
            else -row["realized_return_pct"]
        ) > 0 else 0.0
        for row in ordered
    ]
    errors = []
    for index in range(minimum, len(outcomes)):
        probability = mean(outcomes[:index])
        errors.append((probability - outcomes[index]) ** 2)
    return mean(errors) if errors else None
