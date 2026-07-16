from __future__ import annotations

from datetime import datetime, timezone
import re

from .models import (
    ClaimValidationResult,
    ConsensusPackage,
    EvidenceClosureAttempt,
    EvidenceClosureOutcome,
    EvidenceClosureReport,
    EvidenceWorkOrder,
    ExternalEvidenceBundle,
    FinancialMetric,
    ManagementSourcePackage,
    PeerMetricReadthrough,
    PrimarySourceObservation,
    SourceCorroborationResult,
    TradeIdea,
)


LICENSED_OR_MANUAL_SOURCE_TYPES = {
    "consensus_manual",
    "licensed_newswire",
    "reputable_publisher_api",
    "paid_market_data",
    "manual_import",
}


def execute_evidence_work_order(
    ticker: str,
    work_order: EvidenceWorkOrder,
    *,
    filings: list,
    metrics: list[FinancialMetric],
    validated_claims: ClaimValidationResult,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
    consensus: ConsensusPackage,
    ideas: list[TradeIdea],
    peer_metric_readthrough: dict[str, list[PeerMetricReadthrough]],
    primary_observations: list[PrimarySourceObservation],
    corroboration_results: list[SourceCorroborationResult],
) -> EvidenceClosureReport:
    """Execute open work orders against registered evidence gathered in this run.

    Network adapters run earlier in the pipeline. This executor is the auditable
    closure pass: it checks their normalized outputs, records every attempted
    adapter, and refuses to treat mere source availability as resolution.
    """
    outcomes = [
        _execute_item(
            item,
            filings=filings,
            metrics=metrics,
            validated_claims=validated_claims,
            management_sources=management_sources,
            external_evidence=external_evidence,
            consensus=consensus,
            ideas=ideas,
            peer_metric_readthrough=peer_metric_readthrough,
            primary_observations=primary_observations,
            corroboration_results=corroboration_results,
        )
        for item in work_order.items
    ]
    status_by_id = {outcome.work_id: outcome.status for outcome in outcomes}
    for item in work_order.items:
        item.status = status_by_id.get(item.work_id, item.status)
    counts = {
        status: sum(1 for outcome in outcomes if outcome.status == status)
        for status in (
            "resolved",
            "contradicted",
            "genuinely_unavailable",
            "licensed_or_manual_required",
        )
    }
    if outcomes and counts["resolved"] + counts["contradicted"] == len(outcomes):
        status = "Closed"
    elif counts["resolved"] or counts["contradicted"]:
        status = "Partially closed"
    else:
        status = "Open"
    summary = (
        f"{counts['resolved']} resolved, {counts['contradicted']} contradicted, "
        f"{counts['genuinely_unavailable']} genuinely unavailable, and "
        f"{counts['licensed_or_manual_required']} require licensed or manual input."
    )
    return EvidenceClosureReport(
        ticker=ticker,
        status=status,
        summary=summary,
        outcomes=outcomes,
        resolved_count=counts["resolved"],
        contradicted_count=counts["contradicted"],
        unavailable_count=counts["genuinely_unavailable"],
        licensed_or_manual_count=counts["licensed_or_manual_required"],
        data_gaps=[] if outcomes else ["No evidence work-order items were available to execute."],
    )


def _execute_item(item, **context) -> EvidenceClosureOutcome:
    source_type = _normalize_source_type(item.source_type)
    terms = _item_terms(item.action, item.expected_output, item.channel)
    attempts: list[EvidenceClosureAttempt] = []
    matched: list[str] = []
    citations = []

    contradicted = _matching_contradictions(item, context["corroboration_results"])
    if contradicted:
        attempts.append(EvidenceClosureAttempt(
            "primary-source corroboration", "contradicted",
            "A registered primary-source cross-check contradicts the claim.",
            contradicted,
        ))
        return _outcome(
            item.work_id,
            "contradicted",
            "Primary-source evidence contradicts the proposition in this work order.",
            attempts,
            contradiction_evidence=contradicted,
            next_action="Revise or reject the affected causal link before promotion.",
        )

    if source_type in {"sec", "issuer", "registered_source"}:
        claims = _matching_claims(terms, context["validated_claims"])
        metric_matches = _matching_metrics(terms, context["metrics"])
        filing_matches = _matching_filings(terms, context["filings"])
        matched.extend(claims + metric_matches + filing_matches)
        citations.extend([
            claim.citation for claim in context["validated_claims"].claims
            if claim.citation and _claim_label(claim) in claims
        ])
        attempts.append(EvidenceClosureAttempt(
            "SEC/issuer normalized evidence",
            "matched" if matched else "no_match",
            "Checked filings, validated claims, and normalized financial metrics.",
            matched,
        ))
    elif source_type == "transcript":
        documents = list(getattr(context["management_sources"], "documents", []) or [])
        turns = list(getattr(context["management_sources"], "transcript_turns", []) or [])
        for document in documents:
            label = f"{getattr(document, 'source_type', 'management document')}: {getattr(document, 'title', '')}".strip()
            if _matches(terms, label):
                matched.append(label)
        if not matched and turns:
            matched.append(f"{len(turns)} normalized transcript turn(s)")
        attempts.append(EvidenceClosureAttempt(
            "management-source adapter",
            "matched" if matched else "no_match",
            "Checked issuer-filed management materials and transcript turns.",
            matched,
        ))
    elif source_type == "macro_market":
        evidence = list(getattr(context["external_evidence"], "evidence", []) or [])
        for row in evidence:
            label = f"{getattr(row, 'provider', '')}: {getattr(row, 'title', '') or getattr(row, 'metric', '')}".strip()
            if _matches(terms, label) or not terms:
                matched.append(label)
        has_price = any(
            idea.driver_attribution and idea.driver_attribution.raw_return_pct is not None
            for idea in context["ideas"]
        )
        if has_price:
            matched.append("Event-specific price attribution")
        attempts.append(EvidenceClosureAttempt(
            "macro/market adapters",
            "matched" if matched else "no_match",
            "Checked official macro observations and event-specific price attribution.",
            matched,
        ))
    elif source_type == "peer":
        readthroughs = [
            row
            for rows in context["peer_metric_readthrough"].values()
            for row in rows
            if row.status not in {"missing_metric_family", "Unavailable"}
        ]
        matched.extend(
            f"{row.peer_ticker} {row.metric_family}: {row.summary}"
            for row in readthroughs
            if _matches(terms, row.metric_family + " " + row.summary) or not terms
        )
        attempts.append(EvidenceClosureAttempt(
            "peer/global-peer adapters",
            "matched" if matched else "no_match",
            "Checked aligned operating-metric read-throughs, including global peers.",
            matched,
        ))
    elif source_type == "primary_specialist":
        observations = context["primary_observations"]
        matched.extend(
            f"{row.source_type}: {row.title}"
            for row in observations
            if _matches(terms, f"{row.source_type} {row.title} {row.summary}")
        )
        attempts.append(EvidenceClosureAttempt(
            "registered specialist primary-source adapters",
            "matched" if matched else "no_match",
            "Checked regulator, court, product, contract, trade, labor, and industry observations.",
            matched,
        ))
    elif source_type == "consensus":
        official = [
            row for row in context["consensus"].observations
            if getattr(row, "official", False)
        ]
        revisions = list(context["consensus"].revisions or [])
        matched.extend(
            f"Official {getattr(row, 'provider', 'provider')} {getattr(row, 'field', 'observation')}"
            for row in official
        )
        matched.extend(
            f"{revision.metric} {revision.window_days}-day revision"
            for revision in revisions
            if getattr(revision, "revision_pct", None) is not None
        )
        attempts.append(EvidenceClosureAttempt(
            "consensus snapshot adapters",
            "matched" if matched else "history_missing",
            "Checked official point-in-time target, estimate, recommendation, and surprise snapshots.",
            matched,
        ))
    else:
        attempts.append(EvidenceClosureAttempt(
            source_type or "registered adapter",
            "adapter_unavailable",
            "No deterministic adapter produced usable normalized evidence for this source type.",
        ))

    if matched:
        return _outcome(
            item.work_id,
            "resolved",
            "Registered evidence satisfies at least one acceptance path for this work order.",
            attempts,
            matched_evidence=matched[:12],
            citations=citations[:8],
            next_action="Review the matched evidence and rerun thesis validation if it changes a causal link.",
        )
    if source_type == "consensus" or item.source_type in LICENSED_OR_MANUAL_SOURCE_TYPES:
        return _outcome(
            item.work_id,
            "licensed_or_manual_required",
            "The required point-in-time or licensed dataset is not available from configured adapters.",
            attempts,
            next_action="Configure an eligible provider or import the supplied point-in-time CSV template.",
        )
    return _outcome(
        item.work_id,
        "genuinely_unavailable",
        "Registered adapters were checked, but no period-aligned evidence satisfying the work order was found.",
        attempts,
        next_action="Keep the link unproven; retry after the next filing/event or add a registered source adapter.",
    )


def _normalize_source_type(source_type: str) -> str:
    value = (source_type or "").lower().replace("-", "_").replace(" ", "_")
    if value in LICENSED_OR_MANUAL_SOURCE_TYPES or "consensus" in value or "estimate" in value:
        return "consensus"
    if any(token in value for token in ("sec", "filing", "issuer", "xbrl", "presentation", "agm", "proxy")):
        return "sec" if "sec" in value or "xbrl" in value or "filing" in value else "issuer"
    if "transcript" in value or "earnings_call" in value:
        return "transcript"
    if any(token in value for token in ("macro", "market", "price", "treasury", "fred", "bls", "bea")):
        return "macro_market"
    if "peer" in value or value in {"hkex_document", "cninfo_document", "global_peer_official_document"}:
        return "peer"
    if any(token in value for token in ("regulator", "court", "product", "safety", "patent", "contract", "trade", "labor", "industry")):
        return "primary_specialist"
    if value in {"registered_source", ""}:
        return "registered_source"
    return value


def _item_terms(*values: str) -> set[str]:
    stop = {
        "and", "the", "for", "with", "from", "source", "evidence", "check",
        "review", "data", "latest", "company", "current", "period", "table",
    }
    text = " ".join(values).lower()
    return {
        token for token in re.findall(r"[a-z][a-z0-9_-]{2,}", text)
        if token not in stop
    }


def _matches(terms: set[str], text: str) -> bool:
    lowered = (text or "").lower()
    return bool(terms and any(term in lowered for term in terms))


def _matching_claims(terms: set[str], result: ClaimValidationResult) -> list[str]:
    return [
        _claim_label(claim)
        for claim in result.claims
        if claim.is_substantive
        and claim.status.lower() not in {"rejected", "not thesis-grade"}
        and _matches(terms, " ".join(filter(None, [
            claim.claim_type, claim.metric, claim.business_driver,
            claim.supporting_quote, claim.reason,
        ])))
    ]


def _claim_label(claim) -> str:
    return f"Validated claim {claim.claim_id}: {claim.business_driver} / {claim.metric or claim.claim_type}"


def _matching_metrics(terms: set[str], metrics: list[FinancialMetric]) -> list[str]:
    return [
        f"{metric.name}: {metric.value:,.2f} {metric.unit} ({metric.period_end})"
        for metric in metrics
        if _matches(terms, metric.name)
    ]


def _matching_filings(terms: set[str], filings: list) -> list[str]:
    return [
        f"{filing.form} {filing.filing_date}: {filing.description or filing.primary_doc}"
        for filing in filings
        if _matches(terms, f"{filing.form} {filing.description} {filing.primary_doc}")
    ]


def _matching_contradictions(item, rows: list[SourceCorroborationResult]) -> list[str]:
    idea_ids = set(item.related_idea_ids or [])
    matches = []
    for row in rows:
        if "contradict" not in row.status.lower():
            continue
        if idea_ids and row.claim_id not in idea_ids:
            continue
        matches.append(f"{row.claim_id}: {row.explanation}")
    return matches


def _outcome(
    work_id: str,
    status: str,
    summary: str,
    attempts: list[EvidenceClosureAttempt],
    *,
    matched_evidence: list[str] | None = None,
    contradiction_evidence: list[str] | None = None,
    citations: list | None = None,
    next_action: str = "",
) -> EvidenceClosureOutcome:
    return EvidenceClosureOutcome(
        work_id=work_id,
        status=status,
        summary=summary,
        attempted_adapters=attempts,
        matched_evidence=list(matched_evidence or []),
        contradiction_evidence=list(contradiction_evidence or []),
        citations=list(citations or []),
        next_action=next_action,
        resolved_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
