from __future__ import annotations

from datetime import datetime

from .analysis import format_number
from .idea_engine import expected_value
from .models import (
    ActionPlan,
    BringYourOwnDataStatus,
    BudgetPolicy,
    CalibrationReport,
    ChangeEvent,
    CompanyEconomics,
    CompanyIdentity,
    ConsensusPackage,
    CoverageCase,
    CoverageExpansionDiagnostics,
    ConvictionAuditReport,
    CreditLens,
    DataQualityReport,
    EntityResolution,
    EvidenceLedger,
    EvidenceClosureReport,
    EvidenceWorkOrder,
    ExpectationsBridge,
    EventWorkflow,
    ExternalEvidenceBundle,
    FilingRecord,
    FinancialMetric,
    FinancialCoverage,
    MetricResolutionAudit,
    HistoricalReferenceSet,
    ICOnePager,
    EvidenceSufficiency,
    LLMRunManifest,
    LanguageAudit,
    LlmComparison,
    LlmReviewResult,
    ManagementCredibility,
    ManagementSourcePackage,
    MarketCaptureReadiness,
    MarketImpliedExpectations,
    EarningsSurpriseProxy,
    RecentMarketContext,
    PeerUniverse,
    RunManifest,
    ThesisBrief,
    ThesisCluster,
    ThesisCritique,
    ThesisValidationReport,
    TradeIdea,
    ValuationResult,
    ClaimValidationResult,
    ResearchSourcePlan,
    ResearchQuestion,
    ResearchScoutReport,
    ResearchModeSuite,
    SourceCoverageMatrix,
    LlmExtractionManifest,
    LlmResearchAgentManifest,
    WisburgResearchLens,
    CausalThesisGraph,
    CompanyModelWorkspace,
)


def build_dd_memo(
    identity: CompanyIdentity,
    filings: list[FilingRecord],
    metrics: list[FinancialMetric],
    events: list[ChangeEvent],
    ideas: list[TradeIdea],
    consensus: ConsensusPackage | None = None,
    expectations: ExpectationsBridge | None = None,
    valuation: ValuationResult | None = None,
    evidence: EvidenceLedger | None = None,
    data_quality: DataQualityReport | None = None,
    management: ManagementCredibility | None = None,
    calibration: CalibrationReport | None = None,
    manifest: RunManifest | None = None,
    entity_resolution: EntityResolution | None = None,
    financial_coverage: FinancialCoverage | None = None,
    peer_universe: PeerUniverse | None = None,
    management_sources: ManagementSourcePackage | None = None,
    external_evidence: ExternalEvidenceBundle | None = None,
    thesis_brief: ThesisBrief | None = None,
    ic_one_pager: ICOnePager | None = None,
    thesis_critique: ThesisCritique | None = None,
    evidence_sufficiency: EvidenceSufficiency | None = None,
    action_plan: list[ActionPlan] | None = None,
    llm_manifest: LLMRunManifest | None = None,
    llm_reviews: list[LlmReviewResult] | None = None,
    llm_comparison: LlmComparison | None = None,
    language_audit: LanguageAudit | None = None,
    historical_references: HistoricalReferenceSet | None = None,
    thesis_validation: ThesisValidationReport | None = None,
    conviction_audit: ConvictionAuditReport | None = None,
    budget_policy: BudgetPolicy | None = None,
    manual_data_status: BringYourOwnDataStatus | None = None,
    company_economics: CompanyEconomics | None = None,
    credit_lens: CreditLens | None = None,
    thesis_clusters: list[ThesisCluster] | None = None,
    research_questions: list[ResearchQuestion] | None = None,
    research_scout: ResearchScoutReport | None = None,
    market_capture_readiness: MarketCaptureReadiness | None = None,
    validated_claims: ClaimValidationResult | None = None,
    source_plan: ResearchSourcePlan | None = None,
    llm_extraction_manifest: LlmExtractionManifest | None = None,
    llm_research_manifest: LlmResearchAgentManifest | None = None,
    event_workflow: EventWorkflow | None = None,
    wisburg_lens: WisburgResearchLens | None = None,
    coverage_expansion: CoverageExpansionDiagnostics | None = None,
    evidence_work_order: EvidenceWorkOrder | None = None,
    coverage_case: CoverageCase | None = None,
    source_coverage_matrix: SourceCoverageMatrix | None = None,
    metric_resolution_audit: MetricResolutionAudit | None = None,
    evidence_closure: EvidenceClosureReport | None = None,
    causal_thesis_graphs: list[CausalThesisGraph] | None = None,
    market_implied_expectations: MarketImpliedExpectations | None = None,
    company_model: CompanyModelWorkspace | None = None,
    research_modes: ResearchModeSuite | None = None,
    earnings_surprise_proxy: EarningsSurpriseProxy | None = None,
    recent_market_context: RecentMarketContext | None = None,
) -> str:
    lines: list[str] = []
    lines.append(f"# Investment Committee Pack: {identity.ticker} - {identity.name.title()}")
    lines.append("")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(
        "This memo is an AI-assisted research draft. It is not financial advice and should be reviewed by an analyst."
    )
    lines.append("")
    lines.append("## 1. Executive Summary")
    if thesis_brief:
        lines.append(f"IC verdict: **{thesis_brief.verdict}**")
        lines.append(f"Evidence sufficiency: **{thesis_brief.status}**; synthesis source: **{thesis_brief.source}**.")
        lines.append(f"Thesis: {thesis_brief.thesis}")
        lines.append(f"Variant view: {thesis_brief.variant_perception}")
        if thesis_brief.evidence_chain:
            lines.append("- Evidence chain:")
            for item in thesis_brief.evidence_chain[:5]:
                lines.append(f"  - {item}")
        if thesis_critique:
            lines.append(f"- Strongest counter-thesis: {thesis_critique.strongest_counter_thesis}")
            for uncertainty in thesis_critique.key_uncertainties[:4]:
                lines.append(f"  - Key uncertainty: {uncertainty}")
        if evidence_sufficiency:
            lines.append(f"- Sufficiency score: {evidence_sufficiency.score}/100. {evidence_sufficiency.rationale}")
        if ic_one_pager:
            lines.append("- IC one-pager:")
            lines.append(
                f"  - Status: {ic_one_pager.status}; direction {ic_one_pager.direction}; "
                f"stage {ic_one_pager.stage}."
            )
            lines.append(f"  - Decision: {ic_one_pager.decision}. {ic_one_pager.decision_reason}")
            lines.append(f"  - Why now: {ic_one_pager.why_now}")
            lines.append(f"  - Blocking issue: {ic_one_pager.blocking_issue}")
            lines.append(f"  - Next best action: {ic_one_pager.next_best_action}")
            lines.append(f"  - Rank eligibility: {ic_one_pager.rank_eligibility}")
            lines.append(f"  - Go / no-go: {ic_one_pager.go_no_go_reason}")
            lines.append(f"  - Causal bridge: {ic_one_pager.causal_bridge}")
            lines.append(f"  - Price move: {ic_one_pager.price_move}")
            lines.append(f"  - Market capture: {ic_one_pager.market_capture}")
            lines.append(f"  - Valuation: {ic_one_pager.valuation}")
            lines.append(f"  - Equity lens: {ic_one_pager.equity_lens}")
            lines.append(f"  - Credit lens: {ic_one_pager.credit_lens}")
            for item in ic_one_pager.work_order_actions[:4]:
                lines.append(f"  - Evidence work order: {item}")
            for item in ic_one_pager.monitor_actions[:4]:
                lines.append(f"  - Monitor next: {item}")
        if llm_manifest:
            lines.append(
                f"- LLM status: {llm_manifest.status}; provider {llm_manifest.provider}; "
                f"model {llm_manifest.model}; prompt {llm_manifest.prompt_version}."
            )
            if llm_manifest.prompt_hash:
                lines.append(f"  - Prompt fingerprint: {llm_manifest.prompt_hash[:16]}...")
            if llm_manifest.prompt_context_counts:
                counts = ", ".join(
                    f"{key}={value}" for key, value in llm_manifest.prompt_context_counts.items()
                )
                lines.append(f"  - Prompt context counts: {counts}")
            if llm_manifest.guardrail_policy:
                lines.append(f"  - LLM guardrails: {', '.join(llm_manifest.guardrail_policy)}")
            lines.append(f"  - LLM guardrail score: {llm_manifest.guardrail_score}/100")
            for check in llm_manifest.guardrail_checks[:6]:
                lines.append(
                    f"  - LLM guardrail check - {check.area}: {check.status}; "
                    f"score {check.score}/100. {check.summary}"
                )
                if check.gaps:
                    lines.append(f"    - Gaps: {'; '.join(check.gaps[:3])}")
                if check.enforcement:
                    lines.append(f"    - Enforcement: {check.enforcement}")
        if llm_extraction_manifest:
            lines.append(
                f"- Claim extraction: {llm_extraction_manifest.status}; provider "
                f"{llm_extraction_manifest.provider}; prompt {llm_extraction_manifest.prompt_version}."
            )
        if llm_research_manifest:
            lines.append(
                f"- LLM research assistant: {llm_research_manifest.status}; provider "
                f"{llm_research_manifest.provider}; registry "
                f"{llm_research_manifest.source_registry_version or 'n/a'}; executor "
                f"{llm_research_manifest.deterministic_executor}."
            )
            if llm_research_manifest.allowed_roles:
                lines.append(
                    "  - Assistant allowed roles: "
                    + ", ".join(llm_research_manifest.allowed_roles[:5])
                )
            if llm_research_manifest.prohibited_actions:
                lines.append(
                    "  - Assistant prohibited actions: "
                    + ", ".join(llm_research_manifest.prohibited_actions[:5])
                )
            if llm_research_manifest.validation_gates:
                lines.append(
                    "  - Assistant validation gates: "
                    + ", ".join(llm_research_manifest.validation_gates[:5])
                )
            lines.append(f"  - Evidence boundary: {llm_research_manifest.evidence_boundary}")
        if budget_policy:
            lines.append(
                f"- Budget mode: {budget_policy.mode}; target cost {budget_policy.cost_target}. "
                f"{budget_policy.description}"
            )
        if company_economics:
            lines.append(
                f"- Company economics: {company_economics.status}; "
                f"{company_economics.industry_playbook.industry_label}; "
                f"{company_economics.material_driver_count} material driver(s)."
            )
        if ideas and ideas[0].conviction_chain:
            chain = ideas[0].conviction_chain
            lines.append(
                f"- Conviction chain: {chain.status}; confidence {chain.confidence}. "
                f"{chain.summary}"
            )
        if llm_comparison:
            lines.append(
                f"- Model comparison: {llm_comparison.status}; agreement {llm_comparison.agreement}; "
                f"secondary {llm_comparison.secondary_provider or 'n/a'}."
            )
            for difference in llm_comparison.key_differences[:3]:
                lines.append(f"  - Model difference: {difference}")
        if language_audit:
            lines.append(
                f"- Language audit: policy {language_audit.policy}; sources "
                f"{', '.join(language_audit.source_languages) or 'n/a'}."
            )
            for note in language_audit.chinese_source_notes[:3]:
                lines.append(f"  - Chinese-source note: {note}")
        if historical_references:
            lines.append(
                f"- Historical references: {historical_references.status}; "
                f"{historical_references.sample_size}/{historical_references.minimum_sample_size} resolved analogs."
            )
            lines.append(f"  - {historical_references.summary}")
        if thesis_validation:
            lines.append(
                f"- Thesis validation: {thesis_validation.status}; "
                f"validation score {thesis_validation.score}/100."
            )
            lines.append(f"  - {thesis_validation.summary}")
        if evidence_work_order:
            lines.append(f"- Evidence work order: {evidence_work_order.status}. {evidence_work_order.summary}")
            for item in evidence_work_order.items[:3]:
                blocker = (
                    "Research-Ready blocker"
                    if item.blocks_research_ready else
                    "High-Conviction blocker"
                    if item.blocks_high_conviction else
                    "follow-up"
                )
                lines.append(f"  - [{item.priority}] {blocker}: {item.action} ({item.source_type})")
        if conviction_audit:
            lines.append(
                f"- Conviction audit: {conviction_audit.status}; "
                f"process score {conviction_audit.score}/100."
            )
        if event_workflow and event_workflow.items:
            lines.append("- Next workflow items:")
            for item in event_workflow.items[:5]:
                due = f" due {item.due_date}" if item.due_date else ""
                lines.append(f"  - [{item.priority}] {item.title}{due}: {item.reason}")
            lines.append(f"  - {conviction_audit.summary}")
        lines.append("")
    if ideas:
        top = ideas[0]
        capture = top.market_capture.category if top.market_capture else "Unknown"
        lines.append(
            f"Top setup: **{top.title}** at stage **{top.stage}**, with idea score "
            f"**{top.score.total if top.score else 'n/a'}/100** "
            f"and market capture classified as **{capture}**."
        )
        lines.append(f"Variant view: {top.variant_perception}")
    else:
        lines.append("No high-conviction trade ideas were generated from the available source data.")
    lines.append("")

    lines.append("## 1A. IC Action Plan")
    if action_plan:
        for item in action_plan[:8]:
            threshold = f" {item.operator} {item.threshold}" if item.operator and item.threshold is not None else ""
            lines.append(
                f"- {item.criterion}: watch {item.metric or item.source_field}{threshold}; "
                f"deadline {item.deadline or 'n/a'}; confirm if {item.confirm_trigger}; "
                f"break if {item.break_trigger}."
            )
    else:
        lines.append("- No machine-readable action plan is available.")
    lines.append("")

    if llm_reviews:
        lines.append("## 1B. Secondary Reader")
        for review in llm_reviews:
            lines.append(
                f"- **{review.provider} / {review.model}**: {review.status}. "
                f"{review.summary or review.message}"
            )
            for item in (review.disagreements + review.missed_counter_thesis + review.unsupported_claims)[:5]:
                lines.append(f"  - {item}")
        lines.append("")

    if historical_references:
        lines.append("## 1C. Historical References")
        lines.append(
            f"- Status: {historical_references.status}; scope: {historical_references.scope}; "
            f"resolved sample {historical_references.sample_size}/{historical_references.minimum_sample_size}."
        )
        if historical_references.hit_rate_pct is not None:
            lines.append(f"- Analog hit rate: {historical_references.hit_rate_pct:.1f}%")
        if historical_references.mean_realized_return_pct is not None:
            lines.append(f"- Mean realized return: {historical_references.mean_realized_return_pct:+.1f}%")
        for reference in historical_references.references[:6]:
            realized = (
                f"{reference.realized_return_pct:+.1f}%"
                if reference.realized_return_pct is not None else "unresolved"
            )
            lines.append(
                f"- {reference.ticker}: {reference.idea_title} "
                f"(similarity {reference.similarity_score}/100, {reference.stage}, outcome {realized}). "
                f"Match: {', '.join(reference.match_reasons) or 'n/a'}."
            )
        for gap in historical_references.data_gaps:
            lines.append(f"- Data gap: {gap}")
        lines.append("")

    if conviction_audit:
        lines.append("## 1D. Conviction Audit")
        lines.append(
            f"- Status: {conviction_audit.status}; score {conviction_audit.score}/100."
        )
        lines.append(f"- {conviction_audit.summary}")
        for item in conviction_audit.items:
            lines.append(
                f"- **{item.name}**: {item.status} ({item.score}/100). "
                f"{item.evidence}"
            )
            for gap in item.gaps[:3]:
                lines.append(f"  - Gap: {gap}")
        if conviction_audit.differentiators:
            lines.append("- Why this is different from generic chat:")
            for differentiator in conviction_audit.differentiators[:4]:
                lines.append(f"  - {differentiator}")
        lines.append("")

    if thesis_validation:
        lines.append("## 1E. Thesis Validation Matrix")
        lines.append(
            f"- Status: {thesis_validation.status}; score {thesis_validation.score}/100; "
            f"top idea: {thesis_validation.top_idea_title}."
        )
        lines.append(f"- {thesis_validation.summary}")
        for check in thesis_validation.checks:
            tier = f"Tier {check.source_tier}" if check.source_tier else "n/a"
            lines.append(
                f"- **{check.channel}**: {check.status} ({check.score}/100, {tier}). "
                f"{check.evidence} Implication: {check.implication}"
            )
            for gap in check.gaps[:3]:
                lines.append(f"  - Gap: {gap}")
        if thesis_validation.required_next_evidence:
            lines.append("- Required next evidence:")
            for item in thesis_validation.required_next_evidence[:6]:
                lines.append(f"  - {item}")
        if thesis_validation.next_evidence_actions:
            lines.append("- Evidence action plan:")
            for action in thesis_validation.next_evidence_actions[:6]:
                blocker = "blocks high conviction" if action.blocks_high_conviction else "supporting context"
                lines.append(
                    f"  - [{action.priority}] {action.channel}: {action.action} "
                    f"Source: {action.source}. {blocker}."
                )
        lines.append("")

    if evidence_work_order:
        lines.append("## 1F. Evidence Work Order")
        lines.append(f"- Status: {evidence_work_order.status}. {evidence_work_order.summary}")
        for item in evidence_work_order.items[:10]:
            blockers = []
            if item.blocks_research_ready:
                blockers.append("blocks Research-Ready")
            if item.blocks_high_conviction:
                blockers.append("blocks High-Conviction")
            lines.append(
                f"- [{item.priority}] {item.channel}: {item.action} "
                f"({item.source_type}; {', '.join(blockers) or 'follow-up'}; origin {item.origin})."
            )
            lines.append(f"  - Expected output: {item.expected_output}")
            lines.append(f"  - Why it matters: {item.why_it_matters}")
            if item.acceptance_criteria:
                lines.append(f"  - Acceptance: {'; '.join(item.acceptance_criteria[:2])}")
            if item.falsification_tests:
                lines.append(f"  - Falsify if: {'; '.join(item.falsification_tests[:2])}")
        for gap in evidence_work_order.data_gaps:
            lines.append(f"- Gap: {gap}")
        lines.append("")

    if evidence_closure:
        lines.append("## 1F-a. Automatic Evidence Closure")
        lines.append(f"- {evidence_closure.status}: {evidence_closure.summary}")
        for outcome in evidence_closure.outcomes[:10]:
            lines.append(f"- **{outcome.status}** `{outcome.work_id}`: {outcome.summary}")
            if outcome.matched_evidence:
                lines.append(f"  - Matched: {'; '.join(outcome.matched_evidence[:3])}")
            if outcome.contradiction_evidence:
                lines.append(f"  - Contradiction: {'; '.join(outcome.contradiction_evidence[:2])}")
            if outcome.next_action:
                lines.append(f"  - Next: {outcome.next_action}")
        lines.append("")

    if causal_thesis_graphs:
        lines.append("## 1F-b. Causal Thesis Graph")
        for graph in causal_thesis_graphs[:3]:
            lines.append(
                f"- **{graph.status}** `{graph.idea_id}`: {graph.overall_score}/100; "
                f"weakest link: {graph.weakest_link}."
            )
            for edge in graph.edges:
                lines.append(
                    f"  - {edge.label}: {edge.status} ({edge.score}/100). "
                    f"{edge.missing_evidence[0] if edge.missing_evidence else edge.explanation}"
                )
        lines.append("")

    if market_implied_expectations:
        lines.append("## 1F-c. Market-Implied Expectations")
        lines.append(f"- {market_implied_expectations.status}: {market_implied_expectations.summary}")
        lines.append(
            f"- Price input: {market_implied_expectations.current_price if market_implied_expectations.current_price is not None else 'Unknown'} "
            f"{market_implied_expectations.currency}; source {market_implied_expectations.price_source or 'Unknown'}; "
            f"as of {market_implied_expectations.price_as_of or 'Unknown'}."
        )
        lines.append(
            f"- Financial basis: {market_implied_expectations.financial_basis}; "
            f"period {market_implied_expectations.financial_period or 'Unknown'}."
        )
        for row in market_implied_expectations.expectations:
            value = "Insufficient data" if row.implied_value is None else f"{row.implied_value:,.2f} {row.unit}"
            lines.append(f"- {row.metric}: **{value}**. {row.interpretation}")
            lines.append(f"  - Formula: {row.formula}; confidence: {row.confidence}.")
        lines.append("")

    if earnings_surprise_proxy:
        lines.append("## 1F-c1. Earnings-Surprise Proxy")
        lines.append(f"- **{earnings_surprise_proxy.status}**: {earnings_surprise_proxy.headline}")
        lines.append(f"- Method: {earnings_surprise_proxy.methodology}")
        for item in earnings_surprise_proxy.items[:8]:
            surprise = "Unknown" if item.surprise_pct is None else f"{item.surprise_pct:+.1f}%"
            lines.append(
                f"- {item.event_label}: {item.metric} surprise **{surprise}**; "
                f"actual {item.actual if item.actual is not None else 'Unknown'} versus "
                f"estimate {item.estimate if item.estimate is not None else 'Unknown'} {item.unit}."
            )
            lines.append(
                f"  - Actual source: {item.actual_source}; estimate source: {item.estimate_source}; "
                f"estimate as of {item.estimate_as_of or 'Unknown'}; confidence {item.confidence}."
            )
        for gap in earnings_surprise_proxy.data_gaps[:4]:
            lines.append(f"- Limitation: {gap}")
        lines.append("")

    if recent_market_context:
        lines.append("## 1F-c2. Recent Market Context")
        lines.append(
            f"- **{recent_market_context.status}**: {recent_market_context.summary} "
            f"Price as of {recent_market_context.price_as_of or 'Unknown'}."
        )
        for window in recent_market_context.windows:
            stock = "Unknown" if window.return_pct is None else f"{window.return_pct:+.1f}%"
            relative = "Unknown" if window.relative_return_pct is None else f"{window.relative_return_pct:+.1f}%"
            lines.append(f"- {window.label}: stock {stock}; broad-market relative {relative}.")
        for implication in recent_market_context.thesis_implications[:4]:
            lines.append(f"- Research implication: {implication}")
        lines.append("")

    if company_model:
        lines.append("## 1F-d. Company Model Workspace")
        lines.append(f"- {company_model.status}: {company_model.summary}")
        for case in company_model.cases:
            lines.append(
                f"- {case.name}: revenue {_model_value(case.revenue, company_model.currency)}, "
                f"operating margin {_model_pct(case.operating_margin_pct)}, "
                f"FCF {_model_value(case.free_cash_flow, company_model.currency)}, "
                f"fair value {_model_value(case.fair_value, company_model.currency)}."
            )
        for gap in company_model.data_gaps[:5]:
            lines.append(f"- Model gap: {gap}")
        lines.append("")

    if research_modes:
        lines.append("## 1F-e. Driver-Specific Research Modes")
        for mode in sorted(research_modes.modes, key=lambda row: (not row.recommended, -row.score)):
            marker = "recommended" if mode.recommended else "available"
            lines.append(f"- **{mode.label}**: {mode.status} ({mode.score}/100; {marker}). {mode.summary}")
            if mode.next_actions:
                lines.append(f"  - Next: {mode.next_actions[0]}")
        lines.append("")

    if company_economics:
        lines.append("## 1G. Company Economics and Industry Playbook")
        lines.append(f"- Business model: {company_economics.business_model}")
        playbook = company_economics.industry_playbook
        lines.append(
            f"- Industry playbook: {playbook.industry_label}; template {playbook.sector_template}; "
            f"source {getattr(playbook, 'playbook_source', 'built_in')}; "
            f"quality {company_economics.playbook_quality_score}/100."
        )
        if playbook.key_kpis:
            lines.append(f"- Key KPIs: {', '.join(playbook.key_kpis)}")
        if playbook.valuation_methods:
            lines.append(f"- Valuation methods: {', '.join(playbook.valuation_methods)}")
        if playbook.macro_sensitivities:
            lines.append(f"- Macro sensitivities: {', '.join(playbook.macro_sensitivities)}")
        if company_economics.playbook_quality:
            lines.append("- Playbook quality checklist:")
            for item in company_economics.playbook_quality[:6]:
                gaps = "; ".join(item.gaps[:2]) or "none"
                lines.append(
                    f"  - {item.area}: {item.status} ({item.score}/100); "
                    f"gaps: {gaps}; next: {item.next_action}; stage impact: {item.stage_impact}"
                )
        for driver in company_economics.drivers[:8]:
            lines.append(
                f"- **{driver.name}** ({driver.materiality}, {driver.trend}): "
                f"{driver.current_evidence} Why it matters: {driver.why_it_matters}"
            )
        if company_economics.driver_coverage:
            lines.append("- Driver coverage checklist:")
            for item in company_economics.driver_coverage[:8]:
                missing = "; ".join(item.missing_evidence[:2]) or "none"
                lines.append(
                    f"  - {item.driver_name}: {item.status}; missing: {missing}; "
                    f"next source: {item.next_source}; stage impact: {item.stage_impact}"
                )
        for gap in company_economics.data_gaps:
            lines.append(f"- Economics gap: {gap}")
        lines.append("")

    if credit_lens:
        lines.append("## 1G. Credit Lens")
        lines.append(
            f"- Status: {credit_lens.status}; risk level: {credit_lens.risk_level}. {credit_lens.summary}"
        )
        lines.append(f"- Source note: {credit_lens.source_note}")
        for metric in credit_lens.metrics[:8]:
            value = "n/a" if metric.value is None else format_number(metric.value)
            lines.append(
                f"- {metric.name}: {value} {metric.unit}; {metric.status}. {metric.interpretation}"
            )
        for item in credit_lens.credit_bridge[:6]:
            missing = "; ".join(item.missing_evidence[:3]) or "none"
            lines.append(
                f"- Credit bridge - {item.area}: {item.status}. "
                f"Question: {item.credit_question} Current evidence: {item.current_evidence} "
                f"Missing: {missing}. Next source: {item.next_source}"
            )
            if item.falsification_test:
                lines.append(f"  - Falsification test: {item.falsification_test}")
        for item in credit_lens.positives[:4]:
            lines.append(f"- Credit support: {item}")
        for item in credit_lens.risks[:4]:
            lines.append(f"- Credit risk: {item}")
        for item in credit_lens.required_evidence[:4]:
            lines.append(f"- Credit evidence needed: {item}")
        for item in credit_lens.monitor_rules[:4]:
            lines.append(f"- Credit monitor rule: {item}")
        for item in credit_lens.credit_catalysts[:4]:
            lines.append(f"- Credit catalyst: {item}")
        for item in credit_lens.falsification_tests[:4]:
            lines.append(f"- Credit falsification test: {item}")
        for gap in credit_lens.data_gaps[:4]:
            lines.append(f"- Credit data gap: {gap}")
        lines.append("")

    if thesis_clusters:
        lines.append("## 1H. Thesis Clusters")
        for cluster in thesis_clusters[:6]:
            lines.append(
                f"- **{cluster.label}**: {cluster.status}; stage {cluster.stage}; "
                f"score {cluster.score if cluster.score is not None else 'n/a'}/100; "
                f"conviction chain {cluster.conviction_chain_status}."
            )
            lines.append(f"  - Thesis: {cluster.thesis}")
            if cluster.why_now:
                lines.append(f"  - Why now: {cluster.why_now}")
            lines.append(f"  - Priced in: {cluster.priced_in}")
            if cluster.counter_thesis:
                lines.append(f"  - Counter-thesis: {cluster.counter_thesis}")
            for item in cluster.what_must_be_true[:3]:
                lines.append(f"  - Must be true: {item}")
            for item in cluster.what_would_falsify[:3]:
                lines.append(f"  - Falsifier: {item}")
            for item in cluster.next_research_actions[:3]:
                lines.append(f"  - Next action: {item}")
        for gap in cluster.evidence_gaps[:3]:
            lines.append(f"  - Gap: {gap}")
        lines.append("")

    if research_questions:
        lines.append("## 1I. Research Questions")
        lines.append(
            "These are not trade recommendations. They are the exact open questions the app must answer before a weak signal can become Research-Ready or High-Conviction."
        )
        for question in research_questions[:6]:
            lines.append(f"- **{question.title}** ({question.priority}; {question.status})")
            lines.append(
                f"  - Answerability: {question.answerability_status} "
                f"({question.answerability_score}/100). Decision rule: {question.decision_rule}"
            )
            lines.append(f"  - Hypothesis to test: {question.hypothesis}")
            lines.append(f"  - Expected answer format: {question.answer_format}")
            lines.append(f"  - Stop condition: {question.stop_condition}")
            lines.append(f"  - Driver: {question.driver_name}. Why it matters: {question.why_it_matters}")
            lines.append(f"  - Source signal: {question.source_signal}")
            for item in question.minimum_evidence_package[:3]:
                lines.append(f"  - Minimum evidence package: {item}")
            for gap in question.answerability_gaps[:3]:
                lines.append(f"  - Answerability gap: {gap}")
            for gap in question.missing_links[:3]:
                lines.append(f"  - Missing link: {gap}")
            for evidence_need in question.required_evidence[:3]:
                lines.append(f"  - Evidence needed: {evidence_need}")
            for source_type in question.primary_source_types[:3]:
                lines.append(f"  - Primary source type: {source_type}")
            for source in question.next_sources[:3]:
                lines.append(f"  - Next source: {source}")
            for step in question.workplan_steps[:3]:
                lines.append(f"  - Workplan step: {step}")
            for criterion in question.acceptance_criteria[:2]:
                lines.append(f"  - Acceptance criterion: {criterion}")
            for test in question.falsification_tests[:2]:
                lines.append(f"  - Falsification test: {test}")
            for need in question.market_capture_needs[:2]:
                lines.append(f"  - Market-capture need: {need}")
        lines.append("")

    if coverage_expansion:
        lines.append("## 1J. Coverage Expansion Diagnostics")
        lines.append(
            f"- Status: {coverage_expansion.status}; profile: {coverage_expansion.coverage_profile}."
        )
        lines.append(f"- Summary: {coverage_expansion.summary}")
        for reason in coverage_expansion.why_no_convincing_thesis[:6]:
            lines.append(f"- Why not convincing yet: {reason}")
        for action in coverage_expansion.recommended_expansions[:8]:
            lines.append(
                f"- [{action.priority}] {action.area} ({action.source_type}): {action.action}"
            )
            lines.append(
                f"  - Why: {action.why_it_matters} Integrity rule: {action.integrity_rule}"
            )
        for policy in coverage_expansion.latency_policy[:3]:
            lines.append(f"- Latency policy: {policy}")
        lines.append("")

    if coverage_case or source_coverage_matrix or metric_resolution_audit:
        lines.append("## 1K. Global Coverage and Metric Resolution")
        if coverage_case:
            lines.append(
                f"- Coverage case: {coverage_case.company_name}; {coverage_case.geography}; "
                f"{coverage_case.security_type}; filing regime {coverage_case.filing_regime}; "
                f"reporting standard {coverage_case.reporting_standard}; currency {coverage_case.currency}."
            )
            if coverage_case.primary_sources:
                lines.append(f"- Primary source stack: {', '.join(coverage_case.primary_sources)}")
            for gap in coverage_case.data_gaps[:4]:
                lines.append(f"- Coverage-case gap: {gap}")
        if source_coverage_matrix:
            lines.append(f"- Source matrix: {source_coverage_matrix.status}. {source_coverage_matrix.summary}")
            for entry in source_coverage_matrix.entries[:8]:
                lines.append(
                    f"  - {entry.source_type}: {entry.status}; official {'yes' if entry.official else 'no'}; "
                    f"tier {entry.source_tier}; licensing {entry.licensing_policy}."
                )
                if entry.blocker:
                    lines.append(f"    - Blocker: {entry.blocker}")
        if metric_resolution_audit:
            lines.append(f"- Metric audit: {metric_resolution_audit.status}. {metric_resolution_audit.summary}")
            for item in metric_resolution_audit.items[:10]:
                value = "n/a" if item.value is None else f"{item.value:,.2f}"
                lines.append(
                    f"  - {item.metric}: {item.status}; method {item.resolution_method}; "
                    f"value {value} {item.unit}; period {item.period_end or 'n/a'}."
                )
                if item.formula:
                    lines.append(f"    - Formula: {item.formula}")
                if item.blocker:
                    lines.append(f"    - Blocker: {item.blocker}")
        lines.append("")

    if validated_claims:
        lines.append("## 1L. Validated Claims")
        lines.append(f"- Status: {validated_claims.status}; provider {validated_claims.provider}.")
        for claim in validated_claims.claims[:10]:
            lines.append(
                f"- **{claim.status}** {claim.event_category}: direction {claim.direction}; "
                f"driver {claim.business_driver}; metric {claim.metric or 'n/a'}; confidence {claim.confidence}."
            )
            lines.append(f"  - What changed: {claim.changed_text or claim.supporting_quote}")
            lines.append(f"  - Rationale: {claim.reason}")
            if claim.not_thesis_grade_reason:
                lines.append(f"  - Not thesis-grade: {claim.not_thesis_grade_reason}")
        for gap in validated_claims.data_gaps:
            lines.append(f"- Claim gap: {gap}")
        lines.append("")

    if ideas and ideas[0].conviction_chain:
        chain = ideas[0].conviction_chain
        lines.append("## 1M. Conviction Chain")
        lines.append(f"- Status: {chain.status}; confidence {chain.confidence}.")
        lines.append(f"- Summary: {chain.summary}")
        for step in chain.steps:
            lines.append(f"- **{step.label}**: {step.status}. {step.statement}")
            for gap in step.data_gaps[:2]:
                lines.append(f"  - Gap: {gap}")
        if chain.what_must_be_true:
            lines.append("- What must be true:")
            for item in chain.what_must_be_true[:5]:
                lines.append(f"  - {item}")
        if chain.what_would_falsify:
            lines.append("- What would falsify it:")
            for item in chain.what_would_falsify[:5]:
                lines.append(f"  - {item}")
        if chain.next_research_actions:
            lines.append("- Next research actions:")
            for item in chain.next_research_actions[:5]:
                lines.append(f"  - {item}")
        lines.append("")

    if budget_policy or manual_data_status:
        lines.append("## 1N. Budget and Bring-Your-Own Data")
        if budget_policy:
            lines.append(f"- Mode: {budget_policy.mode}; target: {budget_policy.cost_target}.")
            lines.append(
                f"- Config: {budget_policy.config_source}; max monthly budget "
                f"{budget_policy.max_monthly_budget_usd if budget_policy.max_monthly_budget_usd is not None else 'uncapped / user-defined'}; "
                f"paid data {'allowed' if budget_policy.allow_paid_data else 'disabled'}; "
                f"LLM {'allowed' if budget_policy.allow_llm else 'disabled'}."
            )
            lines.append(f"- Data policy: {budget_policy.data_policy}")
            for group, sources in budget_policy.provider_policy.items():
                lines.append(f"- {group.replace('_', ' ').title()}: {', '.join(sources) or 'none'}")
            for warning in budget_policy.warnings:
                lines.append(f"- Budget warning: {warning}")
        if manual_data_status:
            lines.append(f"- Manual data status: {manual_data_status.status}; base dir {manual_data_status.base_dir}.")
            for source in manual_data_status.sources:
                lines.append(
                    f"  - {source.source_type}: {source.status}; rows {source.rows_loaded}; path {source.path}."
                )
        lines.append("")

    if research_scout:
        lines.append("## 1O. Research Scout: Company, Sector, Peer, and Geography Story")
        lines.append(research_scout.summary)
        for axis in research_scout.company_story_axes[:4]:
            lines.append(f"- Company axis: {axis}")
        for axis in research_scout.sector_story_axes[:4]:
            lines.append(f"- Sector axis: {axis}")
        for axis in research_scout.geography_story_axes[:4]:
            lines.append(f"- Geography axis: {axis}")
        for axis in research_scout.peer_story_axes[:3]:
            lines.append(f"- Peer axis: {axis}")
        for question in research_scout.questions[:8]:
            lines.append(f"- **{question.question}** ({question.priority}; {question.lens})")
            lines.append(f"  - Source types: {', '.join(question.source_types) or 'registered source'}")
            lines.append(f"  - Expected evidence: {question.expected_evidence}")
            lines.append(f"  - Story use: {question.story_use}")
        for gap in research_scout.data_gaps[:3]:
            lines.append(f"- Research Scout gap: {gap}")
        lines.append("")

    if market_capture_readiness:
        lines.append("## 1P. Market Capture Readiness")
        lines.append(f"- Status: {market_capture_readiness.status}. {market_capture_readiness.summary}")
        lines.append(
            f"- Ideas classified: {market_capture_readiness.classified_ideas}/"
            f"{market_capture_readiness.total_ideas}; price-only: "
            f"{getattr(market_capture_readiness, 'price_only_ideas', 0)}; "
            f"unknown: {market_capture_readiness.unknown_ideas}."
        )
        lines.append(
            f"- Price coverage: {market_capture_readiness.price_coverage}; "
            f"consensus coverage: {market_capture_readiness.consensus_coverage}; "
            f"official consensus available: {'yes' if market_capture_readiness.official_consensus_available else 'no'}; "
            f"revision windows available: {market_capture_readiness.revision_windows_available}."
        )
        import_plan = getattr(market_capture_readiness, "import_plan", None)
        if import_plan:
            lines.append(
                f"- Consensus import plan: {import_plan.status}; minimum rows "
                f"{import_plan.minimum_required_rows}. {import_plan.summary}"
            )
            if import_plan.required_files:
                lines.append(f"  - Required files: {', '.join(import_plan.required_files)}")
            if import_plan.optional_files:
                lines.append(f"  - Optional files: {', '.join(import_plan.optional_files)}")
            if import_plan.template_command:
                lines.append(f"  - Template command: `{import_plan.template_command}`")
            if import_plan.import_command:
                lines.append(f"  - Import command: `{import_plan.import_command}`")
            if import_plan.blocking_reason:
                lines.append(f"  - Blocking reason: {import_plan.blocking_reason}")
            for step in import_plan.next_steps[:4]:
                lines.append(f"  - Next step: {step}")
        for action in market_capture_readiness.actions[:8]:
            lines.append(
                f"- [{action.priority}] {action.area} ({action.status}): {action.action}"
            )
            lines.append(
                f"  - Why it matters: {action.why_it_matters}; source type: {action.source_type}."
            )
        for need in market_capture_readiness.snapshot_needs[:8]:
            lines.append(
                f"- Snapshot needed for {need.idea_id}: {need.metric_family}; event {need.event_date or 'unknown'}."
            )
            lines.append(
                f"  - Pre: {need.pre_event_snapshot}; post: {need.post_event_snapshot}; "
                f"sources: {', '.join(need.accepted_sources[:4])}. Reason: {need.reason}"
            )
            for hint in need.csv_row_hints[:3]:
                lines.append(f"  - CSV row hint: {hint}")
        for gap in market_capture_readiness.data_gaps:
            lines.append(f"- Market-capture gap: {gap}")
        lines.append(f"- Point-in-time rule: {market_capture_readiness.point_in_time_rule}")
        lines.append("")

    if source_plan:
        lines.append("## 1P. Source Plan")
        lines.append(
            f"- Status: {source_plan.status}; registry {source_plan.registry_version}; "
            f"provider {source_plan.provider}."
        )
        for request in source_plan.requests[:10]:
            lines.append(
                f"- [{request.priority}] {request.source_type}: {request.title}. "
                f"{request.reason_to_inspect}"
            )
            lines.append(
                f"  - Expected evidence: {request.expected_evidence_type}; "
                f"confirm/disprove: {request.confirms_or_disproves}; cost/latency: {request.cost_latency}."
            )
        for gap in source_plan.data_gaps:
            lines.append(f"- Source-plan gap: {gap}")
        lines.append("")

    if wisburg_lens and wisburg_lens.status != "Unavailable":
        lines.append("## 1Q. Outside Analyst Debate")
        if wisburg_lens.coverage_audit:
            coverage = wisburg_lens.coverage_audit
            lines.append(
                f"- Coverage audit: {coverage.status}; authentication {coverage.authentication_status}; "
                f"tool discovery {coverage.tool_discovery_status}; listed items {coverage.total_items}; "
                f"structured details {coverage.detailed_items}."
            )
            for tool in coverage.tools:
                lines.append(
                    f"  - {tool.tool_name}: {tool.status}; queries {tool.query_count}; "
                    f"items {tool.item_count}; details {tool.detail_success_count}."
                )
        narrative = wisburg_lens.narrative_score
        if narrative:
            lines.append(
                f"- Narrative status: {narrative.label}; Wisburg items {narrative.item_count}; "
                f"repeated topics: {', '.join(narrative.repeated_topics[:6]) or 'n/a'}."
            )
        debate = wisburg_lens.debate_map
        if debate:
            lines.append(f"- Debate status: {debate.status}.")
            if debate.strongest_bull_case:
                lines.append(f"- External bull framing: {debate.strongest_bull_case}")
            if debate.strongest_bear_case:
                lines.append(f"- External bear framing: {debate.strongest_bear_case}")
        for theme in wisburg_lens.themes[:6]:
            lines.append(
                f"- Theme [{theme.stance}] {theme.label}; driver {theme.driver}; "
                f"evidence count {theme.evidence_count}. {theme.summary}"
            )
        for claim in wisburg_lens.structured_claims[:8]:
            lines.append(
                f"- External claim [Tier {claim.source_tier}; {claim.corroboration_status}; "
                f"Candidate-only]: {claim.statement}"
            )
            lines.append(f"  - Cross-check: {claim.corroboration_explanation}")
        if wisburg_lens.revisions:
            lines.append(
                "- External revisions below are report-level analyst context, not official "
                "point-in-time consensus:"
            )
            for revision in wisburg_lens.revisions[:6]:
                change = f"{revision.change_pct:+.1f}%" if revision.change_pct is not None else "Unknown"
                lines.append(
                    f"  - {revision.metric} [{revision.direction}; {change}; "
                    f"{revision.fiscal_period or 'period unknown'}]: {revision.statement}"
                )
        for task in wisburg_lens.research_tasks[:6]:
            lines.append(
                f"- Executable cross-check [{task.priority}] {task.source_type}: {task.action}. "
                f"Expected: {task.expected_evidence}"
            )
        for suggestion in wisburg_lens.source_suggestions[:5]:
            lines.append(
                f"- Wisburg source suggestion [{suggestion.priority}] {suggestion.source_type}: "
                f"{suggestion.title}. {suggestion.reason_to_inspect}"
            )
        for caveat in wisburg_lens.caveats:
            lines.append(f"- Caveat: {caveat}")
        lines.append("")

    lines.append("## 2. What Changed")
    if events:
        for event in events[:10]:
            why = f" Why this matters: {event.why_this_matters}" if event.why_this_matters else ""
            lines.append(
                f"- **{event.title}** ({event.direction}, severity {event.severity}/5): {event.summary}{why}"
            )
    else:
        lines.append("- No material filing or financial-fact changes were detected.")
    lines.append("")

    lines.append("## 3. Financial Snapshot")
    if entity_resolution:
        lines.append(
            f"Entity: {entity_resolution.ticker} / CIK {entity_resolution.cik}; "
            f"{entity_resolution.listing_status}; exchange {entity_resolution.exchange}."
        )
        if entity_resolution.warning:
            lines.append(f"Entity warning: {entity_resolution.warning}")
    if financial_coverage:
        lines.append(
            f"Coverage status: **{financial_coverage.status}**. {financial_coverage.reason}"
        )
    if metrics:
        lines.append("| Metric | Latest | Period | YoY / Comparable Change |")
        lines.append("|---|---:|---|---:|")
        for metric in metrics[:10]:
            change = (
                f"{metric.yoy_change_pct:+.1f}%"
                if metric.yoy_change_pct is not None
                else "n/a"
            )
            lines.append(
                f"| {metric.name} | {format_number(metric.value)} {metric.unit} | "
                f"{metric.period_end} | {change} |"
            )
    else:
        lines.append(
            "No structured financial metrics were available. "
            + (financial_coverage.reason if financial_coverage else "")
        )
    lines.append("")

    lines.append("## 4. Consensus and Expectations")
    if consensus and consensus.target:
        target = consensus.target
        lines.append(
            f"- Selected {target.target_label}: {format_number(_target_value(target))} "
            f"{target.currency} from {target.source}; semantic: {target.target_kind}."
        )
        lines.append(
            f"- High/low: {format_number(target.target_high)} / {format_number(target.target_low)}; "
            f"analyst count: {target.analyst_count if target.analyst_count is not None else 'Unknown'}"
        )
        lines.append(
            f"- Current price: {format_number(target.current_price)} {target.currency}; "
            f"observed: {target.observed_at or target.as_of}; source as-of: "
            f"{target.source_as_of or target.provider_timestamp or 'Unknown'}; freshness: "
            f"{target.freshness_days if target.freshness_days is not None else 'Unknown'}"
        )
        provider_targets = consensus.provider_targets or [target]
        for item in provider_targets:
            lines.append(
                f"- Provider target [{item.source}, {'official' if item.official else 'unofficial'}]: "
                f"aggregate {format_number(item.target_aggregate)}, mean {format_number(item.target_mean)}, "
                f"median {format_number(item.target_median)}, high {format_number(item.target_high)}, "
                f"low {format_number(item.target_low)} {item.currency}."
            )
        lines.append(
            f"- Implied upside: {target.implied_upside_pct:+.1f}%"
            if target.implied_upside_pct is not None
            else "- Implied upside: unavailable without a matching current price."
        )
        if consensus.revisions:
            lines.append("- Revision history:")
            for revision in consensus.revisions[:8]:
                change = (
                    f"{revision.change_pct:+.1f}%"
                    if revision.change_pct is not None else "n/a"
                )
                lines.append(
                    f"  - {revision.metric} {revision.window_days}d "
                    f"[{revision.status}, {revision.provider or 'provider unknown'}]: "
                    f"{change}. {revision.reason}"
                )
    elif consensus:
        lines.append(f"Consensus status: {consensus.status}. {'; '.join(consensus.data_gaps)}")
    else:
        lines.append("Consensus provider not connected.")
    if expectations:
        lines.append(f"- Expectations bridge: {expectations.headline}")
        for comparison in expectations.comparisons[:5]:
            surprise = (
                f"{comparison.surprise_pct:+.1f}%" if comparison.surprise_pct is not None else "n/a"
            )
            lines.append(
                f"  - {comparison.metric} {comparison.period_end or ''}: expected "
                f"{format_number(comparison.expected)}, actual {format_number(comparison.actual)}, "
                f"surprise {surprise}."
            )
        lines.append(f"- Point-in-time rule: {expectations.point_in_time_note}")
    if consensus and consensus.provider_statuses:
        lines.append("- Provider observations are preserved independently:")
        for status in consensus.provider_statuses:
            label = "official" if status.official else "unofficial"
            lines.append(
                f"  - {status.provider}: {status.status}, {label}, "
                f"entitlement {status.entitlement_status}. {status.message}"
            )
    if consensus and consensus.comparisons:
        lines.append("- Provider disagreements:")
        for comparison in consensus.comparisons:
            values = ", ".join(f"{provider}={value}" for provider, value in comparison.values.items())
            lines.append(f"  - {comparison.field}: {values}. {comparison.interpretation}")
    lines.append("")

    lines.append("## 5. Evidence, Contradictions, and Data Quality")
    if evidence:
        lines.append(f"- Strongest counter-thesis: {evidence.strongest_counter_thesis}")
        lines.append(
            f"- Unresolved material contradictions: {evidence.unresolved_material_contradictions}"
        )
        for claim in evidence.claims[:8]:
            lines.append(f"- Claim [{claim.status}]: {claim.text}")
            if claim.strongest_counter:
                lines.append(f"  - Counter-evidence: {claim.strongest_counter}")
    else:
        lines.append("Evidence ledger was not generated.")
    if data_quality:
        lines.append(
            f"- Data quality: {data_quality.status} ({data_quality.score}/100); "
            f"primary-source coverage {data_quality.primary_source_coverage_pct:.0f}%."
        )
        for issue in data_quality.issues:
            lines.append(f"  - [{issue.severity}] {issue.message}")
    lines.append("")

    lines.append("## 6. Management Credibility")
    if management:
        score = f"{management.score:.0f}/100" if management.score is not None else "Unscored"
        lines.append(
            f"- Status: {management.status}; score: {score}; resolved promises: "
            f"{management.promises_resolved}/{management.promises_total}."
        )
        for promise in management.promises[:6]:
            lines.append(f"- [{promise.status}] {promise.statement}")
        if management.transcript_comparison:
            transcript = management.transcript_comparison
            lines.append(f"- Transcript comparison: {transcript.status}")
            if transcript.tone_shift_summary:
                lines.append(f"  - Tone shift: {transcript.tone_shift_summary}")
            if transcript.current_sentiment_score is not None:
                lines.append(
                    f"  - Current sentiment {transcript.current_sentiment_score}; "
                    f"uncertainty {transcript.current_uncertainty_score}; "
                    f"evasion {transcript.current_evasion_score}; "
                    f"specificity {transcript.current_specificity_score}."
                )
            for flag in transcript.evasive_qa_flags[:4]:
                lines.append(f"  - Q&A flag: {flag}")
        for gap in management.data_gaps:
            lines.append(f"- Data gap: {gap}")
    else:
        lines.append("Management credibility was not evaluated.")
    lines.append("")

    lines.append("## 7. Management Source Cross-Checks")
    if management_sources:
        lines.append(
            f"- Status: {management_sources.status}; documents {len(management_sources.documents)}, "
            f"claims {len(management_sources.claims)}, cross-checks {len(management_sources.cross_checks)}."
        )
        for claim in management_sources.claims[:8]:
            sentiment = (
                f"; sentiment {claim.sentiment_label} ({claim.sentiment_score})"
                if claim.sentiment_label else ""
            )
            lines.append(
                f"- [{claim.status}] {claim.claim_type}{sentiment}: {claim.statement}"
            )
        for check in management_sources.cross_checks[:8]:
            lines.append(
                f"  - Cross-check ({check.status}, Tier {check.source_tier}): {check.summary}"
            )
        if management_sources.meeting_events:
            lines.append("- Meeting/proxy events:")
            for event in management_sources.meeting_events[:6]:
                lines.append(f"  - {event.event_type}: {event.description}")
        for gap in management_sources.data_gaps:
            lines.append(f"- Data gap: {gap}")
    else:
        lines.append("Management source cross-checks were not evaluated.")
    lines.append("")

    lines.append("## 8. Valuation Triangulation")
    if valuation and valuation.status == "Available":
        lines.append(f"- Template: {valuation.template}; confidence: {valuation.confidence}")
        lines.append(f"- Methodology: {valuation.methodology}")
        for note in valuation.normalization_notes:
            lines.append(f"- Normalization: {note}")
        for case in valuation.cases:
            lines.append(
                f"- {case.name} ({case.probability:.0%}): fair value "
                f"{format_number(case.fair_value)} {valuation.currency}; {case.method}; "
                f"probability {case.probability_status}."
            )
        if valuation.probability_weighted_value is not None:
            lines.append(
                f"- Probability-weighted value: {format_number(valuation.probability_weighted_value)} "
                f"{valuation.currency}; expected return "
                f"{valuation.expected_return_pct:+.1f}%"
            )
        if valuation.consensus_target is not None:
            lines.append(
                f"- External consensus target benchmark: {format_number(valuation.consensus_target)} "
                f"{valuation.currency}; internal disagreement "
                f"{valuation.disagreement_pct:+.1f}%"
                if valuation.disagreement_pct is not None
                else f"- External consensus target benchmark: {format_number(valuation.consensus_target)}"
            )
        for gap in valuation.missing_data:
            lines.append(f"- Unavailable method/data: {gap}")
    elif valuation:
        lines.append(
            f"{valuation.template} valuation status: {valuation.status}. "
            f"{' '.join(valuation.missing_data)}"
        )
    else:
        lines.append("Valuation was not run.")
    lines.append("")

    lines.append("## 9. Trade Ideas")
    if ideas:
        for idea in ideas[:5]:
            score = idea.score.total if idea.score else "n/a"
            ev = expected_value(idea.scenarios)
            lines.append(f"### {idea.title}")
            lines.append(f"- Stage: {idea.stage}")
            lines.append(f"- Structure: {idea.structure}")
            lines.append(f"- Score: {score}/100")
            if idea.score and idea.score.score_cap_reason:
                lines.append(f"- Score cap: {idea.score.score_cap_reason}")
            lines.append(
                f"- Illustrative expected value from scenarios: {ev:+.1f}%"
                if ev is not None
                else "- Expected value: unavailable until the payoff model has complete net-return inputs."
            )
            if idea.driver_analysis:
                lines.append(f"- Possible drivers: {idea.driver_analysis.headline}")
                if idea.driver_analysis.bridge_status or idea.driver_analysis.mechanism:
                    lines.append(
                        f"- Causal bridge detail: {idea.driver_analysis.bridge_status or 'Unknown'}"
                        + (
                            f" for {idea.driver_analysis.primary_driver}."
                            if idea.driver_analysis.primary_driver else "."
                        )
                    )
                    if idea.driver_analysis.mechanism:
                        lines.append(f"  - Mechanism: {idea.driver_analysis.mechanism}")
                    if idea.driver_analysis.evidence_needed:
                        lines.append(
                            "  - Evidence needed: "
                            + "; ".join(idea.driver_analysis.evidence_needed[:4])
                        )
                    if idea.driver_analysis.peer_metric_checks:
                        lines.append(
                            "  - Peer metric checks: "
                            + "; ".join(idea.driver_analysis.peer_metric_checks[:4])
                        )
                    if idea.driver_analysis.falsification_tests:
                        lines.append(
                            "  - Falsification tests: "
                            + "; ".join(idea.driver_analysis.falsification_tests[:4])
                        )
                    if idea.driver_analysis.valuation_implication:
                        lines.append(f"  - Valuation implication: {idea.driver_analysis.valuation_implication}")
                    if idea.driver_analysis.credit_implication:
                        lines.append(f"  - Credit implication: {idea.driver_analysis.credit_implication}")
                    for gap in idea.driver_analysis.data_gaps[:3]:
                        lines.append(f"  - Bridge gap: {gap}")
                for factor in idea.driver_analysis.factors[:3]:
                    lines.append(
                        f"  - {factor.cause} ({factor.confidence}, {factor.magnitude_hint}): "
                        f"{factor.explanation}"
                    )
            if idea.causal_bridge_status:
                lines.append(f"- Causal bridge: {idea.causal_bridge_status}")
            if idea.market_capture:
                lines.append(
                    f"- Market capture diagnosis ({idea.market_capture.capture_mode}): "
                    f"{idea.market_capture.diagnosis or idea.market_capture.explanation}"
                )
                lines.append(
                    f"  - Price status: {idea.market_capture.price_status}; "
                    f"consensus status: {idea.market_capture.consensus_status}."
                )
                for item in idea.market_capture.required_inputs[:4]:
                    lines.append(f"  - Required input: {item}")
            if idea.equity_credit_lens:
                if idea.equity_credit_lens.get("equity"):
                    lines.append(f"- Equity lens: {idea.equity_credit_lens['equity']}")
                if idea.equity_credit_lens.get("credit"):
                    lines.append(f"- Credit lens: {idea.equity_credit_lens['credit']}")
            if idea.llm_contribution:
                lines.append(
                    "- LLM contribution: "
                    + "; ".join(f"{key}: {value}" for key, value in idea.llm_contribution.items())
                )
            if idea.driver_attribution:
                attribution = idea.driver_attribution
                lines.append(
                    f"- Price move attribution: {attribution.classification} "
                    f"({attribution.confidence}). {attribution.headline}"
                )
                lines.append(f"  - Attribution readiness: {attribution.attribution_readiness}")
                lines.append(f"  - Attribution quality score: {attribution.attribution_quality_score}/100")
                for item in attribution.attribution_quality[:4]:
                    gaps = "; ".join(item.gaps[:2]) or "none"
                    lines.append(
                        f"  - Attribution quality - {item.area}: {item.status} "
                        f"({item.score}/100). Gaps: {gaps}. Next: {item.next_action}"
                    )
                for item in attribution.attribution_summary[:4]:
                    lines.append(f"  - Attribution summary: {item}")
                lines.append(
                    f"  - Raw {attribution.return_window or 'n/a'} return "
                    f"{_fmt_pct(attribution.raw_return_pct)}; market-relative "
                    f"{_fmt_pct(attribution.market_relative_pct)}; sector-relative "
                    f"{_fmt_pct(attribution.sector_relative_pct)}; beta-adjusted "
                    f"{_fmt_pct(attribution.beta_adjusted_pct)}."
                )
                for factor in attribution.factors[:4]:
                    lines.append(
                        f"  - {factor.label} ({factor.driver_type}, {factor.confidence}): "
                        f"{factor.explanation} Disconfirm if: {factor.disconfirming_evidence}"
                    )
                for item in attribution.classification_evidence[:3]:
                    lines.append(f"  - Attribution evidence: {item}")
                for item in attribution.falsification_tests[:3]:
                    lines.append(f"  - Attribution falsifier: {item}")
                for item in attribution.next_attribution_checks[:3]:
                    lines.append(f"  - Attribution next check: {item}")
            lines.append(f"- Thesis: {idea.thesis}")
            lines.append(f"- Catalyst: {idea.catalyst}")
            lines.append(f"- Variant perception: {idea.variant_perception}")
            lines.append(f"- Strongest counter-thesis: {idea.strongest_counter_thesis}")
            if idea.gate_result and idea.gate_result.research_ready_failed:
                lines.append(f"- Research-ready gaps: {'; '.join(idea.gate_result.research_ready_failed)}")
            if idea.gate_result and idea.gate_result.high_conviction_failed:
                lines.append(f"- High-conviction gaps: {'; '.join(idea.gate_result.high_conviction_failed)}")
            if idea.payoff_model:
                completeness = (
                    idea.payoff_model.payoff_completeness.status
                    if idea.payoff_model.payoff_completeness else idea.payoff_model.status
                )
                lines.append(
                    f"- Payoff model: {idea.payoff_model.status}; completeness {completeness}; probability source "
                    f"{idea.payoff_model.probability_provenance.source if idea.payoff_model.probability_provenance else 'Unknown'}; "
                    f"EV ranking eligible: {'yes' if idea.payoff_model.rank_eligible else 'no'}."
                )
                for scenario in idea.payoff_model.scenarios:
                    net = f"{scenario.net_return_pct:+.1f}%" if scenario.net_return_pct is not None else "incomplete"
                    lines.append(
                        f"  - {scenario.name}: entry {format_number(scenario.entry_value)}, "
                        f"exit {format_number(scenario.exit_value)}, net return {net}, "
                        f"probability {scenario.probability:.0%} ({scenario.probability_status})."
                    )
            if idea.market_capture:
                lines.append(
                    f"- Market capture: {idea.market_capture.capture_mode}; "
                    f"{idea.market_capture.category}. {idea.market_capture.explanation}"
                )
            if idea.peer_readthrough:
                lines.append("- Direct peer checks:")
                if peer_universe:
                    lines.append(
                        f"  - Universe: {peer_universe.sector_template}; {peer_universe.provenance}; "
                        f"status {peer_universe.status}."
                    )
                for peer in idea.peer_readthrough[:5]:
                    price = (
                        f"{peer.price_reaction_pct:+.1f}%"
                        if peer.price_reaction_pct is not None
                        else "n/a"
                    )
                    lines.append(
                        f"  - {peer.peer_ticker}: {peer.relation}; {peer.evidence_status}; "
                        f"1-day sympathy reaction {price}; failure {peer.failure_status or 'none'}. "
                        f"{peer.conclusion}"
                    )
            if idea.peer_metric_summary:
                summary = idea.peer_metric_summary
                lines.append(
                    f"- Peer metric readiness: {summary.status}; score {summary.score}/100; "
                    f"operating peers {summary.operating_metric_peers}/{summary.total_peers}. "
                    f"{summary.summary}"
                )
                lines.append(f"  - Stage impact: {summary.stage_impact}")
                for gap in summary.data_gaps[:3]:
                    lines.append(f"  - Peer metric gap: {gap}")
                for action in summary.next_actions[:3]:
                    lines.append(f"  - Peer metric next action: {action}")
            if idea.peer_metric_readthrough:
                lines.append("- Peer metric read-through:")
                for peer in idea.peer_metric_readthrough[:5]:
                    lines.append(
                        f"  - {peer.peer_ticker}: {peer.metric_family}; {peer.status}; "
                        f"{peer.relation}; {peer.summary}"
                    )
                    if peer.present_metrics or peer.missing_metrics:
                        lines.append(
                            f"    - Present metrics: {', '.join(peer.present_metrics) or 'none'}; "
                            f"missing metrics: {', '.join(peer.missing_metrics) or 'none'}."
                        )
                    for criterion in peer.acceptance_criteria[:2]:
                        lines.append(f"    - Acceptance criterion: {criterion}")
                    for test in peer.falsification_tests[:2]:
                        lines.append(f"    - Peer falsifier: {test}")
            if idea.global_peer_coverage:
                lines.append("- Global peer coverage:")
                for coverage in idea.global_peer_coverage[:5]:
                    lines.append(
                        f"  - {coverage.ticker}: {coverage.status}; documents {len(coverage.documents)}; "
                        f"metrics {len(coverage.metrics)}; gaps {'; '.join(coverage.data_gaps[:2]) or 'none'}"
                    )
            lines.append("")
    else:
        lines.append("No generated trade ideas.")
    lines.append("")

    lines.append("## 10. Price Move Attribution Sources")
    if external_evidence:
        lines.append(
            f"- Status: {external_evidence.status}; supporting external evidence items: "
            f"{len(external_evidence.evidence)}."
        )
        for status in external_evidence.provider_statuses[:8]:
            lines.append(
                f"- Provider {status.provider}: {status.status}; "
                f"entitlement/cache status {status.entitlement_status}. {status.message}"
            )
        macro_items = [item for item in external_evidence.evidence if item.source_type == "macro_factor"]
        if macro_items:
            official_macro = [item for item in macro_items if item.provider not in {"World Bank macro", "IMF macro"}]
            global_macro = [item for item in macro_items if item.provider in {"World Bank macro", "IMF macro"}]
            if official_macro:
                lines.append("- Official macro context:")
            for item in official_macro[:8]:
                safe = "lookahead-safe" if item.lookahead_safe else "not lookahead-safe"
                lines.append(
                    f"  - [{item.provider}] {item.metric_name or item.title}: "
                    f"change {format_number(item.metric_value)} {item.unit or ''}; "
                    f"source as-of {item.source_as_of or 'Unknown'}; "
                    f"release {item.release_date or 'Unknown'}; vintage {item.vintage_date or 'Unknown'}; "
                    f"{safe}. {item.summary}"
                )
            if global_macro:
                lines.append("- Global/ADR macro context:")
            for item in global_macro[:8]:
                safe = "lookahead-safe" if item.lookahead_safe else "not lookahead-safe"
                lines.append(
                    f"  - [{item.provider}] {item.metric_name or item.title}: "
                    f"change {format_number(item.metric_value)} {item.unit or ''}; "
                    f"source as-of {item.source_as_of or 'Unknown'}; {safe}. {item.summary}"
                )
        narrative_items = [
            item for item in external_evidence.evidence
            if item.source_type == "narrative_saturation"
        ]
        if narrative_items:
            lines.append("- Narrative saturation context:")
            for item in narrative_items[:4]:
                lines.append(
                    f"  - [{item.provider}, Tier {item.source_tier}] "
                    f"{item.title}: score {format_number(item.metric_value)}. {item.summary}"
                )
        analyst_context = [
            item for item in external_evidence.evidence
            if item.source_type in {"external_analyst_context", "management_transcript_context", "external_market_context"}
        ]
        if analyst_context:
            lines.append("- External analyst / market context:")
            for item in analyst_context[:6]:
                role = "context only" if item.disqualifies_high_conviction else "supporting context"
                lines.append(
                    f"  - [{item.provider}, Tier {item.source_tier}, {role}] "
                    f"{item.title}; as-of {item.source_as_of or 'Unknown'}. {item.summary}"
                )
        for item in external_evidence.evidence[:8]:
            lines.append(
                f"- [{item.provider}] {item.source_type}: {item.title}; "
                f"tier {item.source_tier}; confidence {item.confidence}. {item.summary}"
            )
    else:
        lines.append("External evidence providers were not evaluated.")
    lines.append("")

    lines.append("## 11. Monitoring Plan")
    for idea in ideas[:3]:
        lines.append(f"### {idea.title}")
        for item in idea.monitor_items:
            lines.append(
                f"- {item.criterion}: watch via {item.data_source} ({item.cadence}). "
                f"Confirm if {item.confirm_trigger} Break if {item.break_trigger} "
                f"Machine check: {item.metric or 'n/a'} {item.operator or ''} "
                f"{item.confirm_threshold if item.confirm_threshold is not None else 'n/a'}; "
                f"deadline {item.deadline or 'open'}."
            )
    if not ideas:
        lines.append("No open monitoring plan because no ideas were generated.")
    lines.append("")

    lines.append("## 12. Calibration")
    if calibration:
        lines.append(
            f"- Status: {calibration.status}; sample {calibration.sample_size}/"
            f"{calibration.minimum_sample_size} required for calibrated probabilities."
        )
        lines.append(
            f"- EV ranking eligible: {'yes' if calibration.rank_by_ev_allowed else 'no'}; "
            f"nearest slice: {calibration.nearest_calibration_slice or 'n/a'} "
            f"({calibration.nearest_calibration_sample_size} outcome(s)); "
            f"outcomes needed: {calibration.outcomes_needed_for_calibration}; "
            f"readiness score: {calibration.readiness_score}/100."
        )
        if calibration.hit_rate_pct is not None:
            lines.append(f"- Hit rate: {calibration.hit_rate_pct:.1f}%")
        if calibration.brier_score is not None:
            lines.append(f"- Brier score: {calibration.brier_score:.3f}")
        for slice_row in calibration.slices[:8]:
            hit_rate = f"{slice_row.hit_rate_pct:.1f}%" if slice_row.hit_rate_pct is not None else "n/a"
            brier = f"{slice_row.brier_score:.3f}" if slice_row.brier_score is not None else "n/a"
            lines.append(
                f"- Calibration slice: {slice_row.signal_type}; {slice_row.status}; "
                f"sample {slice_row.sample_size}/{calibration.minimum_sample_size}; "
                f"needed {slice_row.outcomes_needed_for_calibration}; hit rate {hit_rate}; "
                f"Brier {brier}; EV ranking {'allowed' if slice_row.rank_by_ev_allowed else 'blocked'}. "
                f"{slice_row.next_action}"
            )
        if calibration.post_mortem_coverage_pct is not None:
            lines.append(
                f"- Post-mortem coverage: {calibration.post_mortem_count} review(s); "
                f"{calibration.post_mortem_coverage_pct:.1f}% of stored outcomes."
            )
        if calibration.complete_post_mortem_coverage_pct is not None:
            lines.append(
                f"- Post-mortem quality: {calibration.post_mortem_quality_status}; "
                f"{calibration.complete_post_mortem_count} complete review(s); "
                f"{calibration.complete_post_mortem_coverage_pct:.1f}% complete coverage."
            )
        for item in calibration.post_mortem_quality_gaps[:3]:
            lines.append(f"- Post-mortem quality gap: {item}")
        if calibration.evidence_valid_rate_pct is not None:
            lines.append(f"- Evidence-valid rate from post-mortems: {calibration.evidence_valid_rate_pct:.1f}%")
        for item in calibration.recurring_failure_modes[:3]:
            lines.append(f"- Recurring failure mode: {item}")
        for item in calibration.recurring_lessons[:3]:
            lines.append(f"- Recurring lesson: {item}")
        for item in calibration.process_improvement_actions[:3]:
            lines.append(f"- Process improvement action: {item}")
        for item in calibration.calibration_actions[:4]:
            lines.append(f"- Calibration action: {item}")
        for check in calibration.readiness_checks[:6]:
            lines.append(
                f"- Calibration readiness - {check.area}: {check.status}; "
                f"score {check.score}/100. {check.summary}"
            )
            if check.gaps:
                lines.append(f"  - Gaps: {'; '.join(check.gaps[:3])}")
            if check.next_action:
                lines.append(f"  - Next action: {check.next_action}")
        if calibration.required_outcome_fields:
            lines.append(f"- Required outcome fields: {', '.join(calibration.required_outcome_fields)}")
        for gap in calibration.data_gaps:
            lines.append(f"- Data gap: {gap}")
    else:
        lines.append("Calibration history was not evaluated.")
    lines.append("")

    lines.append("## 13. Source Filings")
    if filings:
        for filing in filings[:8]:
            lines.append(
                f"- [{filing.form} filed {filing.filing_date}]({filing.url})"
                f"{' - ' + filing.description if filing.description else ''}"
            )
    else:
        lines.append("- No filings loaded.")
    lines.append("")

    lines.append("## 14. Citations")
    citation_index = 1
    seen: set[str] = set()
    for idea in ideas[:5]:
        for citation in idea.citations:
            key = f"{citation.url}:{citation.section}:{citation.snippet}"
            if key in seen:
                continue
            seen.add(key)
            snippet = citation.snippet or "Source excerpt available in filing."
            lines.append(
                f"{citation_index}. [{citation.source}]({citation.url})"
                f" - {citation.section or 'Source'}; accession "
                f"{citation.accession or 'n/a'}; period {citation.period_end or 'n/a'}; "
                f"Tier {citation.source_tier}: {snippet}"
            )
            citation_index += 1
    if citation_index == 1:
        lines.append("No specific citation snippets were generated.")
    lines.append("")
    lines.append("## 15. Reproducibility Manifest")
    if manifest:
        lines.append(f"- Run ID: `{manifest.run_id}`")
        lines.append(f"- App version: {manifest.app_version}; generated {manifest.generated_at}")
        lines.append(
            "- Parser versions: "
            + ", ".join(f"{name}={version}" for name, version in manifest.parser_versions.items())
        )
        lines.append(f"- Source URL count: {len(manifest.source_urls)}")
        for assumption in manifest.assumptions:
            lines.append(f"- Assumption: {assumption}")
    else:
        lines.append("Run manifest was not generated.")
    return "\n".join(lines)


def _target_value(target) -> float | None:
    if target.target_kind == "aggregate":
        return target.target_aggregate
    if target.target_kind == "median":
        return target.target_median
    return target.target_mean


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _model_value(value: float | None, unit: str) -> str:
    return "Unknown" if value is None else f"{value:,.2f} {unit}"


def _model_pct(value: float | None) -> str:
    return "Unknown" if value is None else f"{value:.1f}%"
