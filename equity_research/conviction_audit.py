from __future__ import annotations

from statistics import mean

from .idea_engine import expected_value
from .models import (
    CalibrationReport,
    CompanyEconomics,
    ConsensusPackage,
    ConvictionAuditItem,
    ConvictionAuditReport,
    CreditLens,
    DataQualityReport,
    EvidenceLedger,
    ExternalEvidenceBundle,
    HistoricalReferenceSet,
    LLMRunManifest,
    LlmResearchAgentManifest,
    LlmComparison,
    LlmReviewResult,
    ManagementSourcePackage,
    MarketCaptureReadiness,
    ResearchQuestion,
    ThesisBrief,
    ThesisCluster,
    TradeIdea,
    ValuationResult,
)


def build_conviction_audit(
    ideas: list[TradeIdea],
    evidence: EvidenceLedger,
    data_quality: DataQualityReport,
    consensus: ConsensusPackage,
    valuation: ValuationResult,
    management_sources: ManagementSourcePackage,
    external_evidence: ExternalEvidenceBundle,
    historical_references: HistoricalReferenceSet,
    calibration: CalibrationReport,
    llm_manifest: LLMRunManifest,
    llm_reviews: list[LlmReviewResult],
    llm_comparison: LlmComparison,
    *,
    company_economics: CompanyEconomics | None = None,
    market_capture_readiness: MarketCaptureReadiness | None = None,
    thesis_brief: ThesisBrief | None = None,
    thesis_clusters: list[ThesisCluster] | None = None,
    research_questions: list[ResearchQuestion] | None = None,
    credit_lens: CreditLens | None = None,
    llm_research_manifest: LlmResearchAgentManifest | None = None,
) -> ConvictionAuditReport:
    top = _top_idea(ideas)
    items = [
        _thesis_quality_item(top),
        _company_playbook_item(company_economics),
        _primary_evidence_item(top, evidence, data_quality),
        _point_in_time_item(ideas, data_quality),
        _market_capture_workflow_item(market_capture_readiness),
        _expectations_item(consensus),
        _valuation_item(top, valuation),
        _price_attribution_item(top),
        _peer_metric_item(top),
        _management_cross_check_item(management_sources),
        _ic_one_pager_item(thesis_brief, thesis_clusters),
        _research_question_mode_item(research_questions),
        _credit_lens_item(credit_lens),
        _historical_reference_item(historical_references),
        _calibration_item(calibration),
        _monitorability_item(top),
        _llm_guardrail_item(llm_manifest, llm_reviews, llm_comparison),
        _llm_research_agent_item(llm_research_manifest),
        _external_context_item(external_evidence),
    ]
    score = round(mean(item.score for item in items)) if items else 0
    status = "Robust" if score >= 80 else "Researchable" if score >= 60 else "Needs work"
    differentiators = [
        "Source-linked evidence and citations are separated from model-written synthesis.",
        "Historical analogs come from locally frozen idea versions, not model memory.",
        "LLM output is accepted only after citation guardrails and can be challenged by a secondary reader.",
        "Point-in-time gaps, missing consensus, and weak evidence remain visible instead of being smoothed over.",
    ]
    gaps = [gap for item in items for gap in item.gaps]
    summary = (
        f"Conviction audit is {status.lower()} at {score}/100. "
        "The checklist measures research process quality, not probability of success."
    )
    return ConvictionAuditReport(
        status=status,
        score=score,
        summary=summary,
        items=items,
        differentiators=differentiators,
        data_gaps=_dedupe(gaps),
    )


def _primary_evidence_item(
    top: TradeIdea | None,
    evidence: EvidenceLedger,
    data_quality: DataQualityReport,
) -> ConvictionAuditItem:
    if not top:
        return _item(
            "Primary evidence",
            "Fail",
            "No top idea exists.",
            "No generated thesis has source-linked evidence.",
            ["Run research with SEC or issuer-source coverage."],
        )
    claim = next((item for item in evidence.claims if item.idea_id == top.idea_id), None)
    support_ids = set(claim.supporting_evidence_ids if claim else [])
    supports = [item for item in evidence.items if item.evidence_id in support_ids]
    primary_supports = [item for item in supports if item.source_tier == 1]
    if primary_supports and data_quality.primary_source_coverage_pct >= 80:
        return _item(
            "Primary evidence",
            "Pass",
            "Top thesis has Tier 1 support and broad primary-source coverage.",
            f"{len(primary_supports)} Tier 1 support item(s); material-event coverage {data_quality.primary_source_coverage_pct:.0f}%.",
            [],
            source_type="SEC/issuer evidence",
        )
    if supports:
        gaps = []
        if not primary_supports:
            gaps.append("Top idea lacks Tier 1 supporting evidence.")
        if data_quality.primary_source_coverage_pct < 80:
            gaps.append(f"Material-event primary-source coverage is {data_quality.primary_source_coverage_pct:.0f}%.")
        return _item(
            "Primary evidence",
            "Partial",
            "The top thesis has evidence, but primary-source coverage is incomplete.",
            f"{len(supports)} support item(s); {len(primary_supports)} Tier 1.",
            gaps,
            source_type="evidence ledger",
        )
    return _item(
        "Primary evidence",
        "Fail",
        "The top thesis has no supporting evidence item.",
        "Evidence ledger has no support for the top idea.",
        ["Do not treat this as an investable thesis until source-linked support exists."],
        source_type="evidence ledger",
    )


def _thesis_quality_item(top: TradeIdea | None) -> ConvictionAuditItem:
    if not top:
        return _item(
            "Thesis quality gates",
            "Fail",
            "No thesis exists to validate against evidence, driver mapping, valuation, and monitor gates.",
            "No generated top idea.",
            ["Generate source-linked candidates before promoting a thesis."],
            source_type="idea gate",
        )
    gate = top.gate_result
    research_gaps = list(gate.research_ready_failed if gate else [])
    high_gaps = list(gate.high_conviction_failed if gate else [])
    audit_summary = top.thesis_audit_chain.summary if top.thesis_audit_chain else ""
    if top.stage in {"High-Conviction", "Investable"} and not high_gaps:
        return _item(
            "Thesis quality gates",
            "Pass",
            "The top thesis passed Research-Ready and High-Conviction gates.",
            f"Stage {top.stage}; {audit_summary or 'gate result available'}.",
            [],
            source_type="idea gate",
        )
    if top.stage == "Research-Ready" and not research_gaps:
        return _item(
            "Thesis quality gates",
            "Partial",
            "The top thesis is actionable for research, but still has high-conviction gaps.",
            f"Stage {top.stage}; {audit_summary or 'research-ready gate passed'}.",
            high_gaps[:5] or ["High-conviction gates remain incomplete."],
            source_type="idea gate",
        )
    gaps = research_gaps or high_gaps or ["The top idea did not expose a complete gate audit."]
    return _item(
        "Thesis quality gates",
        "Fail",
        "The top signal is not yet a vetted investment thesis.",
        f"Stage {top.stage}; thesis-grade status {top.thesis_grade_status}.",
        gaps[:6],
        source_type="idea gate",
    )


def _company_playbook_item(company_economics: CompanyEconomics | None) -> ConvictionAuditItem:
    if not company_economics:
        return _item(
            "Company / industry playbook",
            "Fail",
            "Ideas are harder to trust without a business-driver and industry-KPI map.",
            "Company economics were not supplied to the audit.",
            ["Build company economics before synthesizing thesis clusters."],
            source_type="company economics",
        )
    playbook = company_economics.industry_playbook
    has_playbook = bool(playbook.key_kpis or playbook.valuation_methods or playbook.peer_tickers)
    if company_economics.material_driver_count and has_playbook:
        return _item(
            "Company / industry playbook",
            "Pass",
            "The app mapped the company to material drivers, peer context, KPIs, and valuation methods.",
            (
                f"{company_economics.material_driver_count} material driver(s); "
                f"industry {playbook.industry_label}; KPIs {', '.join(playbook.key_kpis[:4]) or 'n/a'}."
            ),
            company_economics.data_gaps[:3],
            source_type="company economics",
        )
    return _item(
        "Company / industry playbook",
        "Partial" if has_playbook or company_economics.drivers else "Fail",
        "Business context exists but does not yet fully map signals to material economic drivers.",
        f"Status {company_economics.status}; material drivers {company_economics.material_driver_count}.",
        company_economics.data_gaps[:5] or ["Add segment drivers, peer KPIs, and valuation method mapping."],
        source_type="company economics",
    )


def _point_in_time_item(
    ideas: list[TradeIdea],
    data_quality: DataQualityReport,
) -> ConvictionAuditItem:
    with_price = sum(
        1 for idea in ideas
        if idea.market_capture and idea.market_capture.price_reaction_pct is not None
    )
    with_consensus = sum(
        1 for idea in ideas
        if idea.market_capture and idea.market_capture.consensus_revision_pct is not None
    )
    if data_quality.point_in_time_complete:
        return _item(
            "Point-in-time market reaction",
            "Pass",
            "Ideas have event-date price and consensus context.",
            f"{len(ideas)} of {len(ideas)} ideas have point-in-time context.",
            [],
            source_type="price/consensus snapshots",
        )
    if with_price or with_consensus:
        return _item(
            "Point-in-time market reaction",
            "Partial",
            "Some event-date market context exists, but at least one idea is incomplete.",
            f"{with_price} idea(s) with price reaction; {with_consensus} with consensus revision.",
            ["Missing point-in-time fields reduce confidence in market-capture claims."],
            source_type="price/consensus snapshots",
        )
    return _item(
        "Point-in-time market reaction",
        "Fail",
        "No usable event-date price or consensus context exists.",
        "Market-capture claims cannot be checked.",
        ["Connect price bars and consensus snapshots before treating market-capture claims as evidence."],
        source_type="price/consensus snapshots",
    )


def _market_capture_workflow_item(
    readiness: MarketCaptureReadiness | None,
) -> ConvictionAuditItem:
    if not readiness:
        return _item(
            "Market-capture workflow",
            "Fail",
            "Market capture needs a ticker-level checklist, not only idea-level unknown labels.",
            "No market-capture readiness report was supplied.",
            ["Build market-capture readiness from idea price and consensus diagnostics."],
            source_type="market capture",
        )
    if readiness.status == "Ready":
        return _item(
            "Market-capture workflow",
            "Pass",
            "Ideas have event-specific price windows and point-in-time consensus revision history.",
            (
                f"{readiness.summary} Classified {readiness.classified_ideas}/"
                f"{readiness.total_ideas}; price {readiness.price_coverage}; "
                f"consensus {readiness.consensus_coverage}."
            ),
            readiness.data_gaps[:4],
            source_type="market capture",
        )
    if readiness.status.startswith("Blocked") or readiness.status == "Partial":
        return _item(
            "Market-capture workflow",
            "Partial",
            "The workflow distinguishes missing price, missing consensus, and missing revision history.",
            (
                f"{readiness.status}; price {readiness.price_coverage}; "
                f"consensus {readiness.consensus_coverage}; classified "
                f"{readiness.classified_ideas}/{readiness.total_ideas}."
            ),
            [action.action for action in readiness.actions[:4]] + readiness.data_gaps[:4],
            source_type="market capture",
        )
    return _item(
        "Market-capture workflow",
        "Fail",
        "Market capture is not ready for thesis classification.",
        readiness.summary,
        [action.action for action in readiness.actions[:4]] + readiness.data_gaps[:4],
        source_type="market capture",
    )


def _expectations_item(consensus: ConsensusPackage) -> ConvictionAuditItem:
    provider_count = len(consensus.provider_statuses)
    official = consensus.status == "Available" and data_quality_official(consensus)
    if official:
        return _item(
            "Consensus and expectations APIs",
            "Pass",
            "Official or keyed consensus evidence is available.",
            f"Provider package status {consensus.status}; {provider_count} provider status row(s).",
            [],
            source_type="financial data API",
        )
    if consensus.status.startswith("Partial") or consensus.target or consensus.estimates or consensus.recommendations:
        return _item(
            "Consensus and expectations APIs",
            "Partial",
            "Expectations evidence exists but is incomplete, unofficial, or semantically limited.",
            f"Provider package status {consensus.status}; provider {consensus.provider}.",
            consensus.data_gaps[:4] or ["Official consensus is missing or incomplete."],
            source_type="financial data API",
        )
    return _item(
        "Consensus and expectations APIs",
        "Fail",
        "No usable consensus or expectations evidence is available.",
        f"Provider package status {consensus.status}.",
        consensus.data_gaps[:4] or ["Configure Alpha Vantage/Finnhub/FMP/CSV or enable approved fallbacks."],
        source_type="financial data API",
    )


def _valuation_item(top: TradeIdea | None, valuation: ValuationResult) -> ConvictionAuditItem:
    ev = expected_value(top.scenarios) if top else None
    if valuation.status == "Available" and ev is not None:
        return _item(
            "Valuation and payoff anchor",
            "Pass",
            "The top thesis has an internal valuation and scenario payoff table.",
            f"Valuation template {valuation.template}; illustrative EV {ev:+.1f}%.",
            [],
            source_type="valuation model",
        )
    if valuation.status == "Available" or ev is not None:
        return _item(
            "Valuation and payoff anchor",
            "Partial",
            "Either valuation or payoff exists, but the chain is incomplete.",
            f"Valuation status {valuation.status}; illustrative EV {'available' if ev is not None else 'unavailable'}.",
            valuation.missing_data[:4] or ["Complete scenario assumptions and internal valuation bridge."],
            source_type="valuation model",
        )
    return _item(
        "Valuation and payoff anchor",
        "Fail",
        "No valuation/payoff anchor exists for sizing or risk/reward judgment.",
        f"Valuation status {valuation.status}.",
        valuation.missing_data[:4] or ["Add company-specific valuation inputs before ranking by payoff."],
        source_type="valuation model",
    )


def _price_attribution_item(top: TradeIdea | None) -> ConvictionAuditItem:
    attribution = top.driver_attribution if top else None
    if attribution and attribution.status == "Available":
        gaps = list(attribution.data_gaps)
        status = "Pass" if attribution.confidence in {"High", "Medium"} else "Partial"
        return _item(
            "Price move attribution",
            status,
            "The top thesis has raw, market/sector/beta-adjusted, peer, and residual context where available.",
            f"{attribution.classification} ({attribution.confidence}); residual {attribution.residual_pct}.",
            gaps[:5],
            source_type="price attribution",
        )
    if attribution:
        return _item(
            "Price move attribution",
            "Partial",
            "Attribution object exists but is not fully available.",
            f"Status {attribution.status}; {attribution.headline}",
            attribution.data_gaps[:5] or ["Complete price windows, benchmarks, peers, and factor context."],
            source_type="price attribution",
        )
    return _item(
        "Price move attribution",
        "Fail",
        "The top thesis has no price-move attribution layer.",
        "No DriverAttribution attached to the top idea.",
        ["Compute event-specific market, sector, peer, macro, and residual attribution."],
        source_type="price attribution",
    )


def _peer_metric_item(top: TradeIdea | None) -> ConvictionAuditItem:
    rows = list(top.peer_metric_readthrough if top else [])
    available = [row for row in rows if row.status == "Available"]
    if available:
        return _item(
            "Peer metric read-through",
            "Pass",
            "The top thesis separates peer operating-metric confirmation from stock-price sympathy.",
            f"{len(available)} peer metric read-through row(s) available.",
            [gap for row in rows for gap in row.data_gaps][:5],
            source_type="peer metrics",
        )
    if rows:
        return _item(
            "Peer metric read-through",
            "Partial",
            "Peer metric checks exist but are stale, unaligned, unavailable, or incomplete.",
            f"{len(rows)} peer metric row(s); statuses {', '.join(sorted({row.status for row in rows}))}.",
            [gap for row in rows for gap in row.data_gaps][:5] or ["Align peer metrics to the idea's metric family and fiscal period."],
            source_type="peer metrics",
        )
    return _item(
        "Peer metric read-through",
        "Fail",
        "No operating-metric read-through is attached to the top idea.",
        "Peer checks may only show price sympathy or SEC availability.",
        ["Add peer KPI comparison for the idea's driver family before relying on peer evidence."],
        source_type="peer metrics",
    )


def _management_cross_check_item(
    management_sources: ManagementSourcePackage,
) -> ConvictionAuditItem:
    confirmed = [item for item in management_sources.cross_checks if item.status == "Confirmed"]
    contradicted = [item for item in management_sources.cross_checks if item.status == "Contradicted"]
    if confirmed and not contradicted:
        return _item(
            "Management cross-check",
            "Pass",
            "Management claims have corroborating source checks and no contradiction in the current package.",
            f"{len(confirmed)} confirmed cross-check(s).",
            [],
            source_type="management source intelligence",
        )
    if management_sources.claims or confirmed or contradicted:
        return _item(
            "Management cross-check",
            "Partial",
            "Management evidence exists, but some claims are unverified or contradicted.",
            f"{len(management_sources.claims)} claim(s), {len(confirmed)} confirmed, {len(contradicted)} contradicted.",
            management_sources.data_gaps[:4] or ["Review unverified or contradicted management claims."],
            source_type="management source intelligence",
        )
    return _item(
        "Management cross-check",
        "Fail",
        "No management-source claims or cross-checks are available.",
        f"Management package status {management_sources.status}.",
        management_sources.data_gaps[:4] or ["Add transcripts, issuer presentations, proxy, 8-K/6-K, or manual imports."],
        source_type="management source intelligence",
    )


def _ic_one_pager_item(
    thesis_brief: ThesisBrief | None,
    thesis_clusters: list[ThesisCluster] | None,
) -> ConvictionAuditItem:
    clusters = thesis_clusters or []
    top_cluster = clusters[0] if clusters else None
    if thesis_brief and thesis_brief.verdict != "No convincing thesis yet" and top_cluster:
        has_ic_fields = bool(
            top_cluster.counter_thesis
            and top_cluster.what_would_falsify
            and top_cluster.monitor_checklist
        )
        return _item(
            "IC one-pager",
            "Pass" if has_ic_fields else "Partial",
            "The IC view condenses thesis, evidence chain, counter-thesis, falsification, valuation, and monitoring.",
            f"Verdict {thesis_brief.verdict}; top cluster {top_cluster.label}.",
            [] if has_ic_fields else ["Complete counter-thesis, falsification, and monitor checklist fields."],
            source_type="IC copilot",
        )
    if thesis_brief:
        return _item(
            "IC one-pager",
            "Partial",
            "The app produced an IC-ready no-thesis conclusion instead of forcing a recommendation.",
            f"Verdict {thesis_brief.verdict}; stage {thesis_brief.stage}.",
            thesis_brief.data_gaps[:5] or ["Resolve thesis evidence gaps before drafting a positive IC view."],
            source_type="IC copilot",
        )
    return _item(
        "IC one-pager",
        "Fail",
        "No IC one-pager synthesis was supplied.",
        "Missing thesis brief.",
        ["Run thesis synthesis after deterministic evidence extraction."],
        source_type="IC copilot",
    )


def _research_question_mode_item(
    research_questions: list[ResearchQuestion] | None,
) -> ConvictionAuditItem:
    questions = research_questions or []
    if questions:
        high = sum(1 for question in questions if question.priority == "High")
        return _item(
            "Research Question mode",
            "Pass",
            "Weak thesis chains are converted into explicit source-check workplans instead of recommendations.",
            f"{len(questions)} research question(s), including {high} high-priority.",
            [],
            source_type="research questions",
        )
    return _item(
        "Research Question mode",
        "Partial",
        "No research questions were generated; this is acceptable only if thesis chains are already complete.",
        "Question list is empty.",
        ["When gates fail, convert the blocker into required evidence and next-source checks."],
        source_type="research questions",
    )


def _credit_lens_item(credit_lens: CreditLens | None) -> ConvictionAuditItem:
    if not credit_lens:
        return _item(
            "Credit analyst lens",
            "Fail",
            "Equity theses are less convincing without liquidity, leverage, coverage, and refinancing context.",
            "No credit lens supplied.",
            ["Build credit lens from cash, debt, interest, coverage, maturities, and required evidence."],
            source_type="credit lens",
        )
    if credit_lens.status == "Available" and credit_lens.metrics:
        return _item(
            "Credit analyst lens",
            "Pass",
            "Credit support, risks, required evidence, and source notes are available for analyst review.",
            f"Risk level {credit_lens.risk_level}; {len(credit_lens.metrics)} credit metric(s).",
            credit_lens.data_gaps[:4],
            source_type="credit lens",
        )
    return _item(
        "Credit analyst lens",
        "Partial" if credit_lens.metrics else "Fail",
        "Credit context exists but key evidence is incomplete.",
        f"Status {credit_lens.status}; risk level {credit_lens.risk_level}.",
        credit_lens.data_gaps[:5] or ["Add debt, liquidity, interest burden, maturity, and rating/spread evidence."],
        source_type="credit lens",
    )


def _historical_reference_item(
    historical_references: HistoricalReferenceSet,
) -> ConvictionAuditItem:
    if historical_references.status == "Supported":
        hit_rate = historical_references.hit_rate_pct
        if hit_rate is not None and hit_rate <= 35:
            return _item(
                "Historical references",
                "Fail",
                "Similar resolved local ideas had poor outcomes; analog history is a warning, not support.",
                historical_references.summary,
                [
                    "Explain why the current thesis is different from failed analogs before using history as evidence.",
                    "Keep historical analogs as Tier 4 context only.",
                ],
                source_type="local frozen idea history",
            )
        if hit_rate is None or hit_rate < 55:
            return _item(
                "Historical references",
                "Partial",
                "Similar prior ideas have enough resolved outcomes, but hit rate is not strong enough to support conviction.",
                historical_references.summary,
                [
                    "Use analogs as checklist context; do not treat them as positive support.",
                ],
                source_type="local frozen idea history",
            )
        return _item(
            "Historical references",
            "Pass",
            "Similar prior ideas have enough resolved local outcomes to support analog context.",
            historical_references.summary,
            [],
            source_type="local frozen idea history",
        )
    if historical_references.references:
        return _item(
            "Historical references",
            "Partial",
            "Similar prior ideas exist, but the resolved sample is too sparse for calibrated conviction.",
            historical_references.summary,
            historical_references.data_gaps[:4],
            source_type="local frozen idea history",
        )
    return _item(
        "Historical references",
        "Fail",
        "No similar local historical idea references are available.",
        historical_references.summary,
        historical_references.data_gaps[:4],
        source_type="local frozen idea history",
    )


def _calibration_item(calibration: CalibrationReport) -> ConvictionAuditItem:
    if calibration.status == "Calibrated":
        return _item(
            "Outcome calibration",
            "Pass",
            "Resolved outcomes are sufficient for at least one calibrated signal-family slice.",
            f"{calibration.sample_size} total resolved outcome(s).",
            [],
            source_type="realized outcomes",
        )
    if calibration.sample_size:
        return _item(
            "Outcome calibration",
            "Partial",
            "Some outcomes are recorded, but no signal-family/horizon slice has enough history.",
            f"{calibration.sample_size}/{calibration.minimum_sample_size} outcome(s) in the largest calibration threshold context.",
            calibration.data_gaps[:4],
            source_type="realized outcomes",
        )
    return _item(
        "Outcome calibration",
        "Fail",
        "No resolved outcomes are available for probability calibration.",
        "Probabilities and EV ranking remain uncalibrated.",
        calibration.data_gaps[:4] or ["Record idea outcomes and post-mortems to calibrate signal families."],
        source_type="realized outcomes",
    )


def _monitorability_item(top: TradeIdea | None) -> ConvictionAuditItem:
    if not top:
        return _item(
            "Monitorability",
            "Fail",
            "No top idea exists to monitor.",
            "No machine-readable checks can be generated.",
            ["Generate an idea before creating monitor criteria."],
        )
    machine_readable = [
        item for item in top.monitor_items
        if item.metric and item.operator and (item.confirm_threshold is not None or item.break_threshold is not None)
    ]
    if machine_readable:
        return _item(
            "Monitorability",
            "Pass",
            "The top thesis has machine-readable confirmation or break criteria.",
            f"{len(machine_readable)} machine-readable monitor item(s).",
            [],
            source_type="thesis monitor",
        )
    if top.monitor_items:
        return _item(
            "Monitorability",
            "Partial",
            "The top thesis has monitor text, but thresholds/operators are incomplete.",
            f"{len(top.monitor_items)} monitor item(s).",
            ["Convert monitor criteria into metric/operator/threshold/deadline fields."],
            source_type="thesis monitor",
        )
    return _item(
        "Monitorability",
        "Fail",
        "The top thesis has no monitor criteria.",
        "No confirmation/break rules can be tracked.",
        ["Add confirmation and break criteria before acting on the idea."],
        source_type="thesis monitor",
    )


def _llm_guardrail_item(
    llm_manifest: LLMRunManifest,
    llm_reviews: list[LlmReviewResult],
    llm_comparison: LlmComparison,
) -> ConvictionAuditItem:
    secondary_available = any(review.status == "Available" for review in llm_reviews)
    if llm_manifest.status == "Available" and secondary_available:
        return _item(
            "LLM synthesis guardrails",
            "Pass",
            "Primary LLM synthesis was accepted and challenged by a secondary reader.",
            f"{llm_manifest.provider}/{llm_manifest.model}; comparison {llm_comparison.status}.",
            [],
            source_type="LLM synthesis",
        )
    if llm_manifest.status == "Skipped":
        weak_evidence_skip = "deterministic evidence is weak" in (llm_manifest.message or "").lower()
        return _item(
            "LLM synthesis guardrails",
            "Pass" if weak_evidence_skip else "Partial",
            (
                "LLM synthesis was intentionally skipped because deterministic evidence was weak; "
                "the guardrail prevented the app from polishing an unconvincing thesis."
                if weak_evidence_skip
                else "LLM synthesis was skipped before model output was used."
            ),
            f"LLM status {llm_manifest.status}; provider {llm_manifest.provider}; model {llm_manifest.model}.",
            [
                llm_manifest.message
                or "Resolve source, valuation, market-capture, or monitor gaps before running synthesis."
            ],
            source_type="LLM synthesis",
        )
    if llm_manifest.status in {"Provider timeout", "Provider error"}:
        return _item(
            "LLM synthesis guardrails",
            "Partial",
            (
                "LLM synthesis did not complete because the provider failed before accepted output was available; "
                "deterministic synthesis remains authoritative."
            ),
            (
                f"LLM status {llm_manifest.status}; failure class "
                f"{llm_manifest.failure_class or 'unknown'}; provider {llm_manifest.provider}; model {llm_manifest.model}."
            ),
            [
                llm_manifest.message
                or "Retry with a compressed evidence pack, longer timeout, or faster provider/model."
            ],
            source_type="LLM synthesis",
        )
    if llm_manifest.status in {"Available", "Disabled", "Rejected", "Not requested"}:
        status = "Partial" if llm_manifest.status in {"Available", "Disabled", "Not requested"} else "Fail"
        return _item(
            "LLM synthesis guardrails",
            status,
            "LLM output is controlled by citation guardrails, but secondary critique is unavailable or synthesis did not run.",
            f"LLM status {llm_manifest.status}; provider {llm_manifest.provider}; model {llm_manifest.model}.",
            [llm_manifest.message or "Enable and test primary/secondary LLM profiles for synthesis plus critique."],
            source_type="LLM synthesis",
        )
    return _item(
        "LLM synthesis guardrails",
        "Fail",
        "LLM status is unavailable.",
        f"LLM status {llm_manifest.status}.",
        ["Check LLM provider configuration."],
        source_type="LLM synthesis",
    )


def _llm_research_agent_item(
    llm_research_manifest: LlmResearchAgentManifest | None,
) -> ConvictionAuditItem:
    if not llm_research_manifest:
        return _item(
            "LLM research assistant lanes",
            "Partial",
            "LLM synthesis may be guarded, but source-planning/document-triage lanes were not reported.",
            "No LLM research-agent manifest supplied.",
            ["Expose source planning, document triage, metric extraction, and trend-analysis contribution status."],
            source_type="LLM research agent",
        )
    if llm_research_manifest.status in {"Available", "Completed"}:
        activity_count = (
            len(llm_research_manifest.source_plan_request_ids)
            + len(llm_research_manifest.document_ids)
            + len(llm_research_manifest.metric_draft_ids)
        )
        return _item(
            "LLM research assistant lanes",
            "Pass",
            "LLM research assistance was constrained by source registry and deterministic validation.",
            f"{llm_research_manifest.provider}/{llm_research_manifest.model}; {activity_count} tracked source/document/draft item(s).",
            llm_research_manifest.messages[:4],
            source_type="LLM research agent",
        )
    if llm_research_manifest.status in {"Not required", "Disabled", "Skipped"}:
        return _item(
            "LLM research assistant lanes",
            "Partial",
            "LLM research assistance did not run, but the workflow can proceed deterministically.",
            f"Status {llm_research_manifest.status}; provider {llm_research_manifest.provider}.",
            llm_research_manifest.messages[:4],
            source_type="LLM research agent",
        )
    return _item(
        "LLM research assistant lanes",
        "Fail",
        "LLM research-agent status indicates the lane is unavailable or failed.",
        f"Status {llm_research_manifest.status}; provider {llm_research_manifest.provider}.",
        llm_research_manifest.messages[:4],
        source_type="LLM research agent",
    )


def _external_context_item(
    external_evidence: ExternalEvidenceBundle,
) -> ConvictionAuditItem:
    official = [item for item in external_evidence.evidence if item.official]
    if official:
        return _item(
            "External context",
            "Pass",
            "Official macro or external evidence was collected for context.",
            f"{len(official)} official external evidence item(s).",
            [],
            source_type="external evidence",
        )
    if external_evidence.evidence:
        return _item(
            "External context",
            "Partial",
            "External context exists, but it is not official primary evidence.",
            f"{len(external_evidence.evidence)} external item(s).",
            external_evidence.data_gaps[:4],
            source_type="external evidence",
        )
    return _item(
        "External context",
        "Fail",
        "No external macro, narrative, or market context was collected.",
        f"External evidence status {external_evidence.status}.",
        external_evidence.data_gaps[:4] or ["Enable official macro/default external providers where relevant."],
        source_type="external evidence",
    )


def data_quality_official(consensus: ConsensusPackage) -> bool:
    if consensus.target and consensus.target.official:
        return True
    return any(status.official and status.status != "Unavailable" for status in consensus.provider_statuses)


def _top_idea(ideas: list[TradeIdea]) -> TradeIdea | None:
    if not ideas:
        return None
    rank = {"High-Conviction": 3, "Investable": 3, "Research-Ready": 2, "Candidate": 1}
    return sorted(
        ideas,
        key=lambda idea: (
            rank.get(idea.stage, 0),
            idea.score.total if idea.score else 0,
            len(idea.citations),
        ),
        reverse=True,
    )[0]


def _item(
    name: str,
    status: str,
    why_it_matters: str,
    evidence: str,
    gaps: list[str],
    *,
    source_type: str = "deterministic",
) -> ConvictionAuditItem:
    scores = {"Pass": 100, "Partial": 60, "Fail": 20, "Unknown": 40}
    return ConvictionAuditItem(
        name=name,
        status=status,
        score=scores.get(status, 40),
        why_it_matters=why_it_matters,
        evidence=evidence,
        gaps=_dedupe(gaps),
        source_type=source_type,
    )


def _dedupe(rows: list[str]) -> list[str]:
    seen = set()
    result = []
    for row in rows:
        clean = str(row or "").strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    return result
