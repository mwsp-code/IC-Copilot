from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import time

from . import config
from .adr_profiles import adr_profile_for
from .alerts import generate_consensus_alerts
from .analysis import (
    build_financial_metrics,
    build_periodic_inline_xbrl_financial_metrics,
    build_registration_financial_metrics,
    compare_filing_pair,
    financial_change_events,
    format_number,
    annotate_change_importance,
    html_to_text,
)
from .budget import build_budget_policy, budget_allows_paid_data
from .claim_validation import validate_events
from .company_economics import attach_economic_context, build_company_economics
from .playbook_portfolio import build_playbook_portfolio
from .conviction_chain import build_conviction_chains
from .coverage import REGISTRATION_FORMS, assess_financial_coverage, resolve_entity
from .coverage_expansion import build_coverage_expansion_diagnostics
from .conviction_audit import build_conviction_audit
from .credit_lens import build_credit_lens
from .driver_attribution import attach_driver_attributions
from .disclosure_intelligence import attach_disclosure_intelligence
from .evidence_work_order import build_evidence_work_order
from .evidence_closure import execute_evidence_work_order
from .expectations import attach_revision_history, build_expectations_bridge
from .external_evidence import ExternalEvidenceProvider, ExternalEvidenceStack
from .global_peers import (
    GlobalPeerFinancialProvider,
    coverage_metrics_as_financial_metrics,
)
from .global_coverage import (
    build_canonical_metric_ontology,
    build_metric_resolution_audit,
    coverage_case_for,
    source_coverage_matrix_for,
)
from .historical_references import build_historical_references
from .ic_one_pager import build_ic_one_pager
from .idea_engine import (
    apply_valuation_context,
    finalize_idea_research,
    generate_trade_ideas,
    ideas_with_changed_evidence_not_price_or_consensus,
    refresh_ideas_after_investigation,
)
from .manual_data import load_china_macro_evidence, scan_manual_data_sources
from .market_capture_workflow import build_market_capture_readiness
from .market_implied import build_market_implied_expectations
from .earnings_surprise_proxy import build_earnings_surprise_proxy
from .metric_intelligence import build_metric_change_assessments
from .memo import build_dd_memo
from .management_sources import (
    MANAGEMENT_FORMS,
    ManagementSourceAdapter,
    ManagementSourceStack,
    build_management_source_package,
    management_events_from_package,
    transcript_document_from_payload,
)
from .models import (
    ActionPlan,
    AlertRecord,
    BringYourOwnDataStatus,
    BudgetPolicy,
    ChangeEvent,
    CompanyEconomics,
    CompanyPlaybookPortfolio,
    ContextualDisclosureComparison,
    Citation,
    CompanyIdentity,
    ConsensusPackage,
    CoverageExpansionDiagnostics,
    CoverageCase,
    ConvictionAuditReport,
    CreditLens,
    DataQualityReport,
    EntityResolution,
    EvidenceLedger,
    EvidenceClosureReport,
    EvidenceWorkOrder,
    EventWindowReaction,
    EventWorkflow,
    ExternalEvidenceBundle,
    ExpectationsBridge,
    EarningsSurpriseProxy,
    EvidenceSufficiency,
    FilingRecord,
    FinancialMetric,
    FinancialCoverage,
    MetricResolutionAudit,
    MetricChangeAssessment,
    GlobalPeerCoverage,
    GlobalPeerMetricObservation,
    HistoricalReferenceSet,
    HistoricalResearchPack,
    ICOnePager,
    IdeaGateResult,
    BullBearJudgePanel,
    DemoCase,
    FormulaTrace,
    ClaimValidationResult,
    LLMRunManifest,
    LlmExtractionManifest,
    LlmResearchAgentManifest,
    LlmTrendAnalysis,
    LanguageAudit,
    LlmComparison,
    LlmReviewResult,
    ManagementCredibility,
    ManagementSourcePackage,
    MarketCaptureReadiness,
    MarketImpliedExpectations,
    NewsClaim,
    PeerReadthrough,
    PeerMetricReadthrough,
    PeerMetricReadthroughSummary,
    PeerUniverse,
    PrimarySourceObservation,
    ProfilingReport,
    ProviderStatus,
    SourceCorroborationResult,
    SourceCoverageMatrix,
    TradeIdea,
    CausalBridge,
    CalibrationReport,
    RunManifest,
    ResearchQuestion,
    ResearchProfile,
    RecentMarketContext,
    ResearchScoutReport,
    ResearchSourcePlan,
    ResearchRunProgress,
    ResearchModeSuite,
    StoryCard,
    ThesisBrief,
    ThesisCluster,
    ThesisCritique,
    ThesisValidationReport,
    WisburgResearchLens,
    ValuationResult,
    CompanyModelWorkspace,
    CausalThesisGraph,
    WatchlistStatus,
    PromotionEvidenceBundle,
    PromotionGateDecision,
)
from .peers import peer_universe_for
from .performance import ResearchProfiler
from .providers import (
    ConsensusAdapter,
    PriceReaction,
    StooqPriceClient,
    TranscriptAdapter,
    build_consensus_provider,
)
from .research_store import ResearchStore
from .research_questions import build_research_questions
from .research_profiles import (
    build_historical_research_pack,
    event_identifier,
    profile_manifest_payload,
    resolve_research_profile,
    select_profile_events,
)
from .promotion_evidence import build_promotion_evidence_bundles
from .research_modes import build_research_mode_suite
from .research_scout import build_research_scout_report
from .rigor import (
    build_calibration_report,
    build_data_quality_report,
    build_evidence_ledger,
    build_management_credibility,
    build_run_manifest,
    record_event_studies,
)
from .sec_client import SEC_FACTS_URL, SecClient, SecClientError
from .share_reconciliation import reconcile_share_event
from .thesis_synthesis import LlmProvider, provider_from_config, synthesize_ic_thesis
from .thesis_accountability import attach_thesis_audit_chains, build_event_workflow
from .thesis_clusters import build_thesis_clusters
from .thesis_validation import build_thesis_validation_report
from .valuation import build_valuation
from .company_model import build_company_model_workspace
from .causal_thesis_graph import build_causal_thesis_graphs
from .source_planner import build_source_plan
from .storytelling import (
    build_story_presentation,
    empty_bull_bear_judge,
    empty_run_progress,
)
from .news_intelligence import (
    attach_causal_bridges,
    build_corroboration_results,
    build_news_intelligence,
    enrich_source_plan_with_news,
    generate_news_candidate_ideas,
    primary_observations_from_payloads,
)
from .wisburg_lens import (
    build_wisburg_lens,
    enrich_source_plan_with_wisburg,
    generate_wisburg_candidate_ideas,
)
from .wisburg_intelligence import (
    corroborate_wisburg_lens,
    wisburg_source_corroboration_results,
)


DOMESTIC_PERIODIC_FORMS = {"10-K", "10-Q"}
DOMESTIC_EVENT_FORMS = {"8-K"}
FOREIGN_PERIODIC_FORMS = {"20-F", "40-F"}
FOREIGN_EVENT_FORMS = {"6-K"}
OWNERSHIP_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
SUPPORTED_US_LISTED_FORMS = (
    DOMESTIC_PERIODIC_FORMS
    | DOMESTIC_EVENT_FORMS
    | FOREIGN_PERIODIC_FORMS
    | FOREIGN_EVENT_FORMS
    | OWNERSHIP_FORMS
)


@dataclass
class ResearchResult:
    identity: CompanyIdentity
    filings: list[FilingRecord]
    metrics: list[FinancialMetric]
    events: list[ChangeEvent]
    ideas: list[TradeIdea]
    wow_ideas: list[TradeIdea]
    memo_markdown: str
    price_reaction: PriceReaction | None
    transcript_count: int
    coverage_notes: list[str]
    consensus: ConsensusPackage
    expectations_bridge: ExpectationsBridge
    valuation: ValuationResult
    watchlist_status: WatchlistStatus
    active_alerts: list[AlertRecord]
    data_quality: DataQualityReport
    evidence_ledger: EvidenceLedger
    management_credibility: ManagementCredibility
    run_manifest: RunManifest
    calibration: CalibrationReport
    price_reactions_by_event: dict[str, PriceReaction]
    entity_resolution: EntityResolution
    financial_coverage: FinancialCoverage
    peer_universe: PeerUniverse
    peer_reactions: dict[str, list[EventWindowReaction]]
    idea_gate_results: list[IdeaGateResult]
    event_window_reactions: dict[str, EventWindowReaction]
    management_sources: ManagementSourcePackage
    external_evidence: ExternalEvidenceBundle
    thesis_brief: ThesisBrief
    ic_one_pager: ICOnePager
    thesis_critique: ThesisCritique
    evidence_sufficiency: EvidenceSufficiency
    action_plan: list[ActionPlan]
    llm_run_manifest: LLMRunManifest
    llm_reviews: list[LlmReviewResult]
    llm_comparison: LlmComparison
    language_audit: LanguageAudit
    historical_references: HistoricalReferenceSet
    thesis_validation: ThesisValidationReport
    conviction_audit: ConvictionAuditReport
    budget_policy: BudgetPolicy
    manual_data_status: BringYourOwnDataStatus
    company_economics: CompanyEconomics
    credit_lens: CreditLens
    thesis_clusters: list[ThesisCluster]
    research_questions: list[ResearchQuestion]
    research_scout: ResearchScoutReport
    market_capture_readiness: MarketCaptureReadiness
    validated_claims: ClaimValidationResult
    source_plan: ResearchSourcePlan
    llm_extraction_manifest: LlmExtractionManifest
    event_workflow: EventWorkflow
    wisburg_lens: WisburgResearchLens
    profiling: ProfilingReport
    coverage_expansion: CoverageExpansionDiagnostics
    news_claims: list[NewsClaim]
    primary_source_observations: list[PrimarySourceObservation]
    source_corroboration_results: list[SourceCorroborationResult]
    causal_bridges: list[CausalBridge]
    global_peer_coverage: dict[str, GlobalPeerCoverage]
    peer_metric_readthrough: dict[str, list[PeerMetricReadthrough]]
    llm_research_manifest: LlmResearchAgentManifest
    llm_trend_analysis: LlmTrendAnalysis
    evidence_work_order: EvidenceWorkOrder
    coverage_case: CoverageCase
    source_coverage_matrix: SourceCoverageMatrix
    metric_resolution_audit: MetricResolutionAudit
    evidence_closure: EvidenceClosureReport
    causal_thesis_graphs: list[CausalThesisGraph]
    market_implied_expectations: MarketImpliedExpectations
    company_model: CompanyModelWorkspace
    research_modes: ResearchModeSuite
    demo_case: DemoCase | None = None
    run_progress: ResearchRunProgress = field(default_factory=empty_run_progress)
    story_cards: list[StoryCard] = field(default_factory=list)
    bull_bear_judge: BullBearJudgePanel = field(default_factory=empty_bull_bear_judge)
    formula_traces: list[FormulaTrace] = field(default_factory=list)
    contextual_disclosure_comparisons: list[ContextualDisclosureComparison] = field(default_factory=list)
    research_profile: ResearchProfile | None = None
    historical_research: HistoricalResearchPack | None = None
    metric_assessments: list[MetricChangeAssessment] = field(default_factory=list)
    promotion_evidence: dict[str, PromotionEvidenceBundle] = field(default_factory=dict)
    promotion_decisions: list[PromotionGateDecision] = field(default_factory=list)
    playbook_portfolio: CompanyPlaybookPortfolio | None = None
    earnings_surprise_proxy: EarningsSurpriseProxy | None = None
    recent_market_context: RecentMarketContext | None = None


@dataclass
class _PeerSnapshot:
    ticker: str
    metrics: list[FinancialMetric]
    events: list[ChangeEvent]
    price_reactions: dict[str, EventWindowReaction] | None = None
    own_event_reactions: list[EventWindowReaction] | None = None
    sec_error: str | None = None
    global_coverage: GlobalPeerCoverage | None = None


def run_us_equity_research(
    ticker: str,
    sec_client: SecClient | None = None,
    price_client: StooqPriceClient | None = None,
    consensus: ConsensusAdapter | None = None,
    transcripts: TranscriptAdapter | None = None,
    management_sources: ManagementSourceAdapter | None = None,
    external_evidence_provider: ExternalEvidenceProvider | None = None,
    global_peer_provider: GlobalPeerFinancialProvider | None = None,
    llm_provider: LlmProvider | None = None,
    secondary_llm_provider: LlmProvider | None = None,
    enable_secondary_llm_review: bool | None = None,
    secondary_llm_min_stage: str | None = None,
    llm_language_policy: str | None = None,
    budget_mode: str | None = None,
    store: ResearchStore | None = None,
    profiler: ResearchProfiler | None = None,
    research_profile: str | ResearchProfile | None = None,
    investigate_event_id: str | None = None,
) -> ResearchResult:
    profiler = profiler or ResearchProfiler(enabled=config.ENABLE_RESEARCH_PROFILING)
    profiler.start()
    selected_profile = resolve_research_profile(research_profile or config.RESEARCH_PROFILE)
    sec = sec_client or SecClient()
    store = store or ResearchStore()
    price_client = price_client or StooqPriceClient(store=store)
    if consensus is None:
        consensus = build_consensus_provider(
            store=store,
            fmp_key=config.FMP_API_KEY if budget_allows_paid_data(budget_mode) else "",
        )
    if getattr(consensus, "store", None) is None:
        consensus.store = store
    transcripts = transcripts or TranscriptAdapter()
    management_sources = management_sources or ManagementSourceStack()
    external_evidence_provider = external_evidence_provider or ExternalEvidenceStack(store=store)
    if hasattr(external_evidence_provider, "store") and getattr(external_evidence_provider, "store", None) is None:
        external_evidence_provider.store = store
    profiler.checkpoint("adapter_setup")

    identity = sec.map_ticker(ticker)
    submissions: dict = {}
    try:
        submissions = sec.get_submissions(identity.cik)
        identity = CompanyIdentity(
            ticker=identity.ticker,
            cik=identity.cik,
            name=identity.name,
            exchange=identity.exchange,
            sic=str(submissions.get("sic") or "") or None,
            sic_description=submissions.get("sicDescription") or None,
        )
    except Exception:
        pass
    filings = sec.get_recent_filings(
        identity.cik,
        forms=SUPPORTED_US_LISTED_FORMS,
        limit=80,
    )
    filings = _expand_profile_filings(sec, identity.cik, filings, selected_profile)
    registration_filings = sec.get_recent_filings(
        identity.cik,
        forms=REGISTRATION_FORMS,
        limit=20,
    )
    management_filings = sec.get_recent_filings(
        identity.cik,
        forms=MANAGEMENT_FORMS,
        limit=30,
    )
    entity_resolution = resolve_entity(
        identity, submissions, filings + registration_filings,
    )
    coverage_notes = _coverage_notes(filings)
    if getattr(sec, "ticker_map_source", "") == "bundled_sec_snapshot":
        coverage_notes.append(
            "Entity identity used the bundled SEC ticker snapshot because the live "
            "SEC ticker index was unavailable. Filings, submissions, and XBRL facts "
            "remain live-source or cache-backed."
        )
    if entity_resolution.warning:
        coverage_notes.append(entity_resolution.warning)
    profiler.checkpoint("entity_and_filings")

    filing_analysis_cache: dict[str, str] = {}
    events: list[ChangeEvent] = []
    events.extend(_compare_latest_pairs(sec, identity.cik, filings, "10-Q", filing_analysis_cache))
    events.extend(_compare_latest_pairs(sec, identity.cik, filings, "10-K", filing_analysis_cache))
    events.extend(_compare_latest_pairs(sec, identity.cik, filings, "20-F", filing_analysis_cache))
    events.extend(_compare_latest_pairs(sec, identity.cik, filings, "40-F", filing_analysis_cache))
    events.extend(_events_from_recent_current_reports(filings))
    events.extend(_ownership_events_from_filings(filings))
    parsed_history_accessions, historical_trend_summaries = _inspect_profile_history(
        sec,
        identity.cik,
        filings,
        selected_profile,
        events,
        investigate_event_id,
        filing_analysis_cache,
    )
    profiler.checkpoint("filing_change_detection")

    facts_url = SEC_FACTS_URL.format(cik=identity.cik)
    facts: dict | None = None
    facts_error: str | None = None
    registration_attempts: list[str] = []
    try:
        facts = sec.get_company_facts(identity.cik)
        metrics = build_financial_metrics(facts)
    except SecClientError as exc:
        metrics = []
        facts_error = str(exc)
    if not metrics and registration_filings:
        for registration_filing in registration_filings[:3]:
            registration_attempts.append(registration_filing.accession)
            try:
                registration_metrics = build_registration_financial_metrics(
                    sec.get_filing_text(registration_filing), registration_filing,
                )
            except Exception as exc:  # pragma: no cover - parser/network boundary
                coverage_notes.append(
                    f"Registration XBRL extraction failed for {registration_filing.accession}: {exc}"
                )
                continue
            if registration_metrics:
                metrics = registration_metrics
                break
    financial_coverage = assess_financial_coverage(
        metrics,
        facts,
        filings,
        registration_filings,
        provider_error=facts_error,
        registration_attempts=registration_attempts,
    )
    coverage_notes.append(f"Financial coverage [{financial_coverage.status}]: {financial_coverage.reason}")
    events.extend(financial_change_events(metrics, facts_url))
    sec_events = sorted(events, key=lambda event: (event.severity, event.event_date or ""), reverse=True)
    profiler.checkpoint("financial_facts_and_events")
    primary_event_date = sec_events[0].event_date if sec_events else None
    initial_price_reactions = _price_reactions_for_events(price_client, identity, sec_events)
    price_reaction = initial_price_reactions.get(primary_event_date or "")
    if price_reaction is None:
        price_reaction = price_client.price_reaction_since(identity.ticker, primary_event_date)
    recent_market_context = _recent_market_context(
        price_client,
        identity.ticker,
        price_reaction,
    )
    current_price = (
        recent_market_context.current_price
        or (price_reaction.latest_price if price_reaction else None)
    )
    consensus_package = consensus.fetch_package(
        identity.ticker,
        current_price,
    )
    try:
        store.save_consensus_package(consensus_package)
        attach_revision_history(consensus_package, store)
        if consensus_package.provider_statuses:
            for status in consensus_package.provider_statuses:
                store.set_provider_health(status.provider, status.status, status.message or "Provider fetch succeeded.")
        else:
            store.set_provider_health(
                consensus_package.provider, consensus_package.status,
                "; ".join(consensus_package.data_gaps) or "Provider fetch succeeded.",
            )
    except Exception as exc:
        consensus_package.data_gaps.append(f"Local consensus persistence failed: {exc}")
    expectations_bridge = build_expectations_bridge(
        identity.ticker,
        consensus_package,
        metrics,
        store,
        events,
        price_reaction,
    )
    earnings_surprise_proxy = build_earnings_surprise_proxy(
        identity.ticker,
        expectations_bridge,
        metrics,
    )
    profiler.checkpoint("initial_prices_consensus_expectations")
    legacy_transcript_rows = transcripts.recent_transcripts(identity.ticker)
    try:
        transcript_documents, transcript_turns, management_provider_statuses = management_sources.fetch_documents(
            identity.ticker, history_limit=selected_profile.call_depth,
        )
    except TypeError:
        transcript_documents, transcript_turns, management_provider_statuses = management_sources.fetch_documents(identity.ticker)
    if legacy_transcript_rows:
        legacy_document, legacy_turns = transcript_document_from_payload(
            identity.ticker,
            {"transcript": legacy_transcript_rows},
            getattr(transcripts, "provider_name", "Transcript adapter"),
            "local:transcript-adapter",
            run_observed_at(),
            official=False,
        )
        if legacy_document:
            transcript_documents.append(legacy_document)
            transcript_turns.extend(legacy_turns)
    try:
        cached_documents, cached_turns = store.cached_management_inputs(identity.ticker)
    except Exception:
        cached_documents, cached_turns = [], []
    live_document_ids = {document.document_id for document in transcript_documents}
    cached_provider_documents = [
        document for document in cached_documents
        if document.document_id not in live_document_ids
        and document.provider.lower() not in {"sec", "sec edgar", "sec/issuer filing"}
        and not document.source_type.lower().startswith("sec_")
    ]
    if cached_provider_documents:
        allowed_cached_ids = {document.document_id for document in cached_provider_documents}
        live_turn_ids = {turn.turn_id for turn in transcript_turns}
        transcript_documents.extend(cached_provider_documents)
        transcript_turns.extend(
            turn for turn in cached_turns
            if turn.document_id in allowed_cached_ids and turn.turn_id not in live_turn_ids
        )
        management_provider_statuses.append(ProviderStatus(
            provider="Local management cache",
            status="Available",
            official=False,
            entitlement_status="cached_fallback",
            observed_at=run_observed_at(),
            message=(
                f"Reused {len(cached_provider_documents)} previously normalized source document(s) "
                "that were absent from the current provider response; original dates and source tiers were preserved."
            ),
        ))
    management_filing_texts = _management_filing_texts(sec, management_filings)
    management_package = build_management_source_package(
        identity.ticker,
        management_filings,
        management_filing_texts,
        transcript_documents,
        transcript_turns,
        management_provider_statuses,
        sec_events,
        metrics,
        consensus_package.surprises,
    )
    management_events = sorted(
        management_events_from_package(management_package),
        key=lambda event: (event.severity, event.event_date or ""),
        reverse=True,
    )
    profiler.checkpoint("management_sources_and_events")
    events = annotate_change_importance(sec_events + management_events)
    peer_universe = peer_universe_for(identity.ticker)
    manual_data_status = scan_manual_data_sources(identity.ticker)
    company_economics = build_company_economics(
        identity,
        metrics,
        events,
        peer_universe,
        manual_data_status,
    )
    credit_lens = build_credit_lens(identity, metrics)
    attach_economic_context(events, company_economics)
    contextual_disclosure_comparisons = attach_disclosure_intelligence(
        identity.ticker,
        events,
        management_package,
        company_economics,
    )
    _attach_share_reconciliation(identity, events)
    for event in events:
        event.metrics["event_id"] = event_identifier(event)
    profile_events = select_profile_events(events, selected_profile, investigate_event_id)
    investigation_events = (
        profile_events if selected_profile.event_scoped else (profile_events or events)
    )
    historical_research = build_historical_research_pack(
        identity.ticker,
        selected_profile,
        filings,
        management_package,
        events,
        investigate_event_id,
        parsed_filing_accessions=parsed_history_accessions,
        historical_trend_summaries=historical_trend_summaries,
    )
    metric_assessments = build_metric_change_assessments(
        events, metrics, historical_research.selected_event_ids,
    )
    playbook_portfolio = build_playbook_portfolio(
        identity, company_economics, events, management_package,
    )
    extraction_llm_provider = llm_provider
    if extraction_llm_provider is None and (config.ENABLE_LLM_CLAIM_VALIDATION or config.ENABLE_LLM_SOURCE_AGENT):
        extraction_llm_provider = provider_from_config(enabled=True)
    validated_claims, llm_extraction_manifest = validate_events(
        identity,
        investigation_events,
        llm_provider=extraction_llm_provider,
        use_llm=config.ENABLE_LLM_CLAIM_VALIDATION,
    )
    profiler.checkpoint("economics_claim_validation")
    source_plan = build_source_plan(
        identity,
        investigation_events,
        validated_claims,
        management_package,
        llm_provider=extraction_llm_provider,
        use_llm=config.ENABLE_LLM_SOURCE_AGENT,
    )
    llm_extraction_manifest.source_plan_request_ids = [
        request.request_id for request in source_plan.requests
    ]
    external_evidence = external_evidence_provider.fetch(identity, investigation_events)
    manual_china_macro = load_china_macro_evidence(identity.ticker)
    if manual_china_macro:
        external_evidence.evidence.extend(manual_china_macro)
        if external_evidence.status == "Unavailable":
            external_evidence.status = "Available"
    wisburg_lens = build_wisburg_lens(identity, external_evidence, company_economics)
    wisburg_lens = corroborate_wisburg_lens(
        wisburg_lens,
        metrics,
        validated_claims,
        management_package,
    )
    source_plan = enrich_source_plan_with_wisburg(source_plan, wisburg_lens)
    llm_extraction_manifest.source_plan_request_ids = [
        request.request_id for request in source_plan.requests
    ]
    for status in external_evidence.provider_statuses:
        try:
            store.set_provider_health(status.provider, status.status, status.message or status.entitlement_status)
        except Exception:
            pass
    try:
        store.save_external_evidence(external_evidence)
        store.save_wisburg_lens(wisburg_lens)
    except Exception as exc:
        external_evidence.data_gaps.append(f"Local external-evidence persistence failed: {exc}")
    news_claims = build_news_intelligence(
        identity.ticker,
        identity.name,
        external_evidence,
        store.latest_news_claims(identity.ticker),
    )
    primary_source_observations = primary_observations_from_payloads(
        store.latest_primary_source_observations(identity.ticker)
    )
    source_corroboration_results = build_corroboration_results(
        identity.ticker,
        news_claims,
        primary_source_observations,
    )
    source_corroboration_results.extend(
        wisburg_source_corroboration_results(wisburg_lens)
    )
    source_plan = enrich_source_plan_with_news(source_plan, news_claims)
    llm_extraction_manifest.source_plan_request_ids = [
        request.request_id for request in source_plan.requests
    ]
    profiler.checkpoint("source_plan_external_evidence")
    price_reactions = _price_reactions_for_events(price_client, identity, events)
    event_window_reactions = _event_window_reactions_for_events(
        price_client, identity, events,
    )
    capture_reactions = _legacy_reactions_from_event_windows(event_window_reactions)
    price_reaction = price_reactions.get(primary_event_date or "") or price_reaction
    profiler.checkpoint("event_price_windows")
    ideas = generate_trade_ideas(
        identity,
        investigation_events if selected_profile.event_scoped else events,
        price_reaction,
        consensus,
        metrics=metrics,
        price_reactions=capture_reactions, source_plan=source_plan,
    )
    if not selected_profile.event_scoped:
        ideas.extend(generate_wisburg_candidate_ideas(identity, wisburg_lens))
        ideas.extend(generate_news_candidate_ideas(identity.ticker, identity.name, news_claims))
    peer_snapshots = _build_peer_snapshots(
        peer_universe, sec, price_client, events,
        global_peer_provider=global_peer_provider or GlobalPeerFinancialProvider(),
    )
    _attach_peer_readthroughs(ideas, peer_snapshots)
    global_peer_coverage = {
        ticker: snapshot.global_coverage
        for ticker, snapshot in peer_snapshots.items()
        if _is_reportable_global_coverage(snapshot.global_coverage)
    }
    peer_metric_readthrough = {
        idea.idea_id: list(idea.peer_metric_readthrough)
        for idea in ideas
        if idea.peer_metric_readthrough
    }
    llm_research_manifest = _build_llm_research_manifest(global_peer_coverage)
    llm_trend_analysis = _build_llm_trend_analysis(global_peer_coverage)
    _attach_global_peer_artifacts(ideas, global_peer_coverage, llm_research_manifest, llm_trend_analysis)
    attach_driver_attributions(
        ideas, event_window_reactions, external_evidence, expectations_bridge, ticker=identity.ticker,
    )
    profiler.checkpoint("ideas_peers_attribution")
    valuation = build_valuation(
        identity,
        metrics,
        consensus_package,
        current_price,
    )
    evidence_ledger = build_evidence_ledger(
        identity.ticker, ideas, events, management_package.cross_checks,
    )
    promotion_evidence = build_promotion_evidence_bundles(
        ideas,
        evidence_ledger,
        external_evidence,
        wisburg_lens,
        news_claims,
        source_plan,
        primary_adapter_attempted=True,
    )
    _hydrate_saved_idea_assumptions(ideas, store)
    idea_gate_results = finalize_idea_research(
        ideas,
        valuation,
        evidence_ledger,
        current_price,
        calibration_lookup=lambda signal_family, horizon: store.calibrated_probability(
            signal_family, horizon, 30,
        ),
        promotion_bundles=promotion_evidence,
        annualized_volatility_pct=recent_market_context.annualized_volatility_pct,
        entry_price_source=recent_market_context.source,
        entry_price_as_of=recent_market_context.price_as_of,
    )
    causal_bridges = attach_causal_bridges(
        identity.ticker,
        ideas,
        news_claims,
        source_corroboration_results,
    )
    attach_thesis_audit_chains(ideas, valuation)
    apply_valuation_context(ideas, valuation)
    for idea in ideas:
        idea.causal_bridge_status = _causal_bridge_status_for_idea(idea)
        idea.equity_credit_lens = _equity_credit_lens_for_idea(idea)
    ideas.sort(key=lambda item: item.score.total if item.score else 0, reverse=True)
    profiler.checkpoint("valuation_evidence_gating")
    build_conviction_chains(
        ideas,
        company_economics,
        valuation,
        consensus_package,
    )
    thesis_clusters = build_thesis_clusters(
        ideas,
        company_economics,
        valuation,
        consensus_package,
    )
    research_questions = build_research_questions(
        ideas,
        thesis_clusters,
        company_economics,
        source_plan,
    )
    market_capture_readiness = build_market_capture_readiness(
        identity.ticker,
        ideas,
        consensus_package,
        manual_data_status,
        source_plan,
    )
    coverage_expansion = build_coverage_expansion_diagnostics(
        identity,
        entity_resolution,
        financial_coverage,
        company_economics,
        consensus_package,
        valuation,
        ideas,
        thesis_clusters,
        source_plan,
    )
    coverage_case = coverage_case_for(identity.ticker, identity, entity_resolution, filings)
    source_coverage_matrix = source_coverage_matrix_for(coverage_case, filings)
    metric_resolution_audit = build_metric_resolution_audit(
        identity.ticker,
        metrics,
        coverage_case,
        build_canonical_metric_ontology(),
    )
    event_workflow = build_event_workflow(
        identity.ticker,
        filings,
        ideas,
        source_plan,
        consensus_package,
    )
    wow_ideas = ideas_with_changed_evidence_not_price_or_consensus(ideas)
    management_credibility = build_management_credibility(
        expectations_bridge, metrics, _transcript_rows_from_turns(management_package.transcript_turns),
    )
    data_quality = build_data_quality_report(events, ideas, consensus_package)
    historical_references = build_historical_references(ideas, store)
    thesis_validation = build_thesis_validation_report(
        ideas,
        evidence_ledger,
        consensus_package,
        valuation,
        management_package,
        external_evidence,
        historical_references,
    )
    record_event_studies(store, identity.ticker, ideas, capture_reactions)
    calibration = build_calibration_report(store)
    evidence_work_order = build_evidence_work_order(
        thesis_validation,
        market_capture_readiness,
        research_questions,
        source_plan,
        coverage_expansion,
        coverage_case,
        source_coverage_matrix,
        metric_resolution_audit,
        events,
    )
    evidence_closure = execute_evidence_work_order(
        identity.ticker,
        evidence_work_order,
        filings=filings,
        metrics=metrics,
        validated_claims=validated_claims,
        management_sources=management_package,
        external_evidence=external_evidence,
        consensus=consensus_package,
        ideas=ideas,
        peer_metric_readthrough=peer_metric_readthrough,
        primary_observations=primary_source_observations,
        corroboration_results=source_corroboration_results,
    )
    company_model = build_company_model_workspace(identity, metrics, valuation)
    try:
        market_implied_overrides = store.latest_market_implied_assumptions(identity.ticker)
    except Exception:
        market_implied_overrides = {}
    market_implied_expectations = build_market_implied_expectations(
        identity,
        metrics,
        current_price,
        valuation,
        company_model,
        price_source=recent_market_context.source,
        price_as_of=recent_market_context.price_as_of,
        assumption_overrides=market_implied_overrides,
    )

    # The first gate pass creates actionable evidence work orders. Feed their
    # closure outcomes and the completed company model back into the ideas,
    # then rebuild all evidence-dependent outputs before any LLM synthesis.
    refresh_ideas_after_investigation(
        identity,
        ideas,
        metrics,
        evidence_closure,
        evidence_work_order,
    )
    evidence_ledger = build_evidence_ledger(
        identity.ticker, ideas, events, management_package.cross_checks,
    )
    promotion_evidence = build_promotion_evidence_bundles(
        ideas,
        evidence_ledger,
        external_evidence,
        wisburg_lens,
        news_claims,
        source_plan,
        primary_adapter_attempted=True,
    )
    idea_gate_results = finalize_idea_research(
        ideas,
        valuation,
        evidence_ledger,
        current_price,
        calibration_lookup=lambda signal_family, horizon: store.calibrated_probability(
            signal_family, horizon, 30,
        ),
        promotion_bundles=promotion_evidence,
        annualized_volatility_pct=recent_market_context.annualized_volatility_pct,
        entry_price_source=recent_market_context.source,
        entry_price_as_of=recent_market_context.price_as_of,
    )
    causal_bridges = attach_causal_bridges(
        identity.ticker,
        ideas,
        news_claims,
        source_corroboration_results,
    )
    attach_thesis_audit_chains(ideas, valuation)
    apply_valuation_context(ideas, valuation)
    for idea in ideas:
        idea.causal_bridge_status = _causal_bridge_status_for_idea(idea)
        idea.equity_credit_lens = _equity_credit_lens_for_idea(idea)
    ideas.sort(key=lambda item: item.score.total if item.score else 0, reverse=True)
    build_conviction_chains(ideas, company_economics, valuation, consensus_package)
    thesis_clusters = build_thesis_clusters(
        ideas, company_economics, valuation, consensus_package,
    )
    research_questions = build_research_questions(
        ideas, thesis_clusters, company_economics, source_plan,
    )
    market_capture_readiness = build_market_capture_readiness(
        identity.ticker, ideas, consensus_package, manual_data_status, source_plan,
    )
    coverage_expansion = build_coverage_expansion_diagnostics(
        identity,
        entity_resolution,
        financial_coverage,
        company_economics,
        consensus_package,
        valuation,
        ideas,
        thesis_clusters,
        source_plan,
    )
    event_workflow = build_event_workflow(
        identity.ticker, filings, ideas, source_plan, consensus_package,
    )
    wow_ideas = ideas_with_changed_evidence_not_price_or_consensus(ideas)
    data_quality = build_data_quality_report(events, ideas, consensus_package)
    historical_references = build_historical_references(ideas, store)
    thesis_validation = build_thesis_validation_report(
        ideas,
        evidence_ledger,
        consensus_package,
        valuation,
        management_package,
        external_evidence,
        historical_references,
    )
    evidence_work_order = build_evidence_work_order(
        thesis_validation,
        market_capture_readiness,
        research_questions,
        source_plan,
        coverage_expansion,
        coverage_case,
        source_coverage_matrix,
        metric_resolution_audit,
        events,
    )
    evidence_closure = execute_evidence_work_order(
        identity.ticker,
        evidence_work_order,
        filings=filings,
        metrics=metrics,
        validated_claims=validated_claims,
        management_sources=management_package,
        external_evidence=external_evidence,
        consensus=consensus_package,
        ideas=ideas,
        peer_metric_readthrough={
            idea.idea_id: list(idea.peer_metric_readthrough)
            for idea in ideas if idea.peer_metric_readthrough
        },
        primary_observations=primary_source_observations,
        corroboration_results=source_corroboration_results,
    )
    causal_thesis_graphs = build_causal_thesis_graphs(
        identity.ticker,
        ideas,
        validated_claims,
        company_model,
        valuation,
        market_implied_expectations,
        evidence_closure,
        wisburg_lens=wisburg_lens,
    )
    research_modes = build_research_mode_suite(
        identity.ticker,
        metrics,
        events,
        ideas,
        management_package,
        evidence_closure,
    )
    profiler.checkpoint("evidence_closure_decision_models")
    research_scout = build_research_scout_report(
        identity,
        ideas,
        company_economics,
        peer_universe,
        source_plan,
        evidence_work_order,
    )
    profiler.checkpoint("thesis_clusters_validation_calibration")
    llm_provider = llm_provider if llm_provider is not None else provider_from_config()
    budget_policy = build_budget_policy(
        budget_mode,
        consensus_package,
        external_evidence,
        llm_enabled=llm_provider is not None,
    )
    thesis_synthesis = synthesize_ic_thesis(
        identity,
        ideas,
        evidence_ledger,
        valuation,
        data_quality,
        management_credibility,
        expectations_bridge,
        management_package,
        external_evidence,
        calibration,
        llm_provider,
        secondary_llm_provider,
        enable_secondary=config.ENABLE_SECONDARY_LLM_REVIEW if enable_secondary_llm_review is None else enable_secondary_llm_review,
        secondary_min_stage=secondary_llm_min_stage or config.SECONDARY_LLM_MIN_STAGE,
        language_policy=llm_language_policy or config.LLM_LANGUAGE_POLICY,
        historical_references=historical_references,
        thesis_validation=thesis_validation,
        budget_policy=budget_policy,
        manual_data_status=manual_data_status,
        company_economics=company_economics,
        credit_lens=credit_lens,
        thesis_clusters=thesis_clusters,
        research_questions=research_questions,
        research_scout=research_scout,
        validated_claims=validated_claims,
        source_plan=source_plan,
        llm_extraction_manifest=llm_extraction_manifest,
        event_workflow=event_workflow,
        wisburg_lens=wisburg_lens,
        evidence_work_order=evidence_work_order,
    )
    ic_one_pager = build_ic_one_pager(
        identity,
        thesis_synthesis.thesis_brief,
        thesis_synthesis.thesis_critique,
        thesis_synthesis.evidence_sufficiency,
        ideas,
        valuation,
        thesis_validation,
        evidence_work_order,
        company_economics,
        credit_lens,
        thesis_clusters,
        thesis_synthesis.action_plan,
    )
    profiler.checkpoint("thesis_synthesis")
    conviction_audit = build_conviction_audit(
        ideas,
        evidence_ledger,
        data_quality,
        consensus_package,
        valuation,
        management_package,
        external_evidence,
        historical_references,
        calibration,
        thesis_synthesis.llm_manifest,
        thesis_synthesis.llm_reviews,
        thesis_synthesis.llm_comparison,
        company_economics=company_economics,
        market_capture_readiness=market_capture_readiness,
        thesis_brief=thesis_synthesis.thesis_brief,
        thesis_clusters=thesis_clusters,
        research_questions=research_questions,
        credit_lens=credit_lens,
        llm_research_manifest=llm_research_manifest,
    )
    profile_summary, history_summary = profile_manifest_payload(selected_profile, historical_research)
    run_manifest = build_run_manifest(
        identity.ticker, filings, metrics, events, consensus_package,
        coverage_notes + management_credibility.data_gaps + validated_claims.data_gaps + source_plan.data_gaps,
        source_plan=source_plan,
        llm_extraction_manifest=llm_extraction_manifest,
        research_profile_summary=profile_summary,
        effective_history_summary=history_summary,
    )
    store.save_research_run(identity.ticker, run_manifest)
    store.save_evidence_ledger(run_manifest.run_id, identity.ticker, evidence_ledger)
    store.save_entity_coverage(run_manifest.run_id, entity_resolution, financial_coverage)
    store.save_peer_universe(peer_universe)
    store.save_management_sources(run_manifest.run_id, management_package)
    store.save_validated_claims(run_manifest.run_id, identity.ticker, validated_claims.claims)
    store.save_source_plan(run_manifest.run_id, source_plan)
    store.save_global_peer_coverage(run_manifest.run_id, identity.ticker, global_peer_coverage)
    store.save_peer_metric_readthroughs(run_manifest.run_id, identity.ticker, peer_metric_readthrough)
    store.save_llm_research_manifest(run_manifest.run_id, identity.ticker, llm_research_manifest)
    store.save_news_claims(news_claims)
    store.save_primary_source_observations(primary_source_observations)
    store.save_source_corroboration_results(source_corroboration_results)
    store.save_causal_bridges(causal_bridges)
    store.save_decision_artifacts(
        run_manifest.run_id,
        identity.ticker,
        evidence_closure,
        causal_thesis_graphs,
        market_implied_expectations,
        company_model,
        research_modes,
        historical_research=historical_research,
        metric_assessments=metric_assessments,
        promotion_evidence=promotion_evidence,
        playbook_portfolio=playbook_portfolio,
        expectation_event_audits=expectations_bridge.event_audits,
        earnings_surprise_proxy=earnings_surprise_proxy,
        recent_market_context=recent_market_context,
    )
    all_reactions = list(event_window_reactions.values())
    for snapshot in peer_snapshots.values():
        all_reactions.extend((snapshot.price_reactions or {}).values())
        all_reactions.extend(snapshot.own_event_reactions or [])
    store.save_event_reactions(all_reactions)
    store.save_idea_versions(identity.ticker, run_manifest.run_id, ideas)
    store.save_thesis_checks(identity.ticker, ideas)
    generate_consensus_alerts(consensus_package, store, events, filings, ideas)
    active_alerts = [
        alert for alert in store.list_alerts(status="unread")
        if alert.ticker == identity.ticker
    ]
    watchlist_rows = store.list_watchlist()
    watchlist_match = next((item for item in watchlist_rows if item.ticker == identity.ticker), None)
    watchlist_status = watchlist_match or WatchlistStatus("default", identity.ticker, False)
    profiler.checkpoint("persistence_alerts")
    memo_markdown = build_dd_memo(
        identity,
        filings,
        metrics,
        events,
        ideas,
        consensus_package,
        expectations_bridge,
        valuation,
        evidence_ledger,
        data_quality,
        management_credibility,
        calibration,
        run_manifest,
        entity_resolution,
        financial_coverage,
        peer_universe,
        management_package,
        external_evidence,
        thesis_synthesis.thesis_brief,
        ic_one_pager,
        thesis_synthesis.thesis_critique,
        thesis_synthesis.evidence_sufficiency,
        thesis_synthesis.action_plan,
        thesis_synthesis.llm_manifest,
        thesis_synthesis.llm_reviews,
        thesis_synthesis.llm_comparison,
        thesis_synthesis.language_audit,
        historical_references,
        thesis_validation,
        conviction_audit,
        budget_policy,
        manual_data_status,
        company_economics,
        credit_lens,
        thesis_clusters,
        research_questions,
        research_scout,
        market_capture_readiness,
        validated_claims,
        source_plan,
        llm_extraction_manifest,
        llm_research_manifest=llm_research_manifest,
        event_workflow=event_workflow,
        wisburg_lens=wisburg_lens,
        coverage_expansion=coverage_expansion,
        evidence_work_order=evidence_work_order,
        coverage_case=coverage_case,
        source_coverage_matrix=source_coverage_matrix,
        metric_resolution_audit=metric_resolution_audit,
        evidence_closure=evidence_closure,
        causal_thesis_graphs=causal_thesis_graphs,
        market_implied_expectations=market_implied_expectations,
        company_model=company_model,
        research_modes=research_modes,
        earnings_surprise_proxy=earnings_surprise_proxy,
        recent_market_context=recent_market_context,
    )
    profiler.checkpoint("memo_rendering")
    profiling = profiler.finish()
    story_presentation = build_story_presentation(
        identity=identity,
        ideas=ideas,
        one_pager=ic_one_pager,
        thesis_brief=thesis_synthesis.thesis_brief,
        thesis_critique=thesis_synthesis.thesis_critique,
        validation=thesis_validation,
        validated_claims=validated_claims,
        economics=company_economics,
        credit_lens=credit_lens,
        valuation=valuation,
        market_capture=market_capture_readiness,
        work_order=evidence_work_order,
        metric_audit=metric_resolution_audit,
        entity_resolution=entity_resolution,
        financial_coverage=financial_coverage,
        market_implied=market_implied_expectations,
        earnings_surprise=earnings_surprise_proxy,
        recent_market_context=recent_market_context,
    )

    return ResearchResult(
        identity=identity,
        filings=filings,
        metrics=metrics,
        events=events,
        ideas=ideas,
        wow_ideas=wow_ideas,
        memo_markdown=memo_markdown,
        price_reaction=price_reaction,
        transcript_count=len(management_package.transcript_turns),
        coverage_notes=coverage_notes,
        consensus=consensus_package,
        expectations_bridge=expectations_bridge,
        valuation=valuation,
        watchlist_status=watchlist_status,
        active_alerts=active_alerts,
        data_quality=data_quality,
        evidence_ledger=evidence_ledger,
        management_credibility=management_credibility,
        run_manifest=run_manifest,
        calibration=calibration,
        price_reactions_by_event=price_reactions,
        entity_resolution=entity_resolution,
        financial_coverage=financial_coverage,
        peer_universe=peer_universe,
        peer_reactions={
            ticker: list(snapshot.price_reactions.values())
            for ticker, snapshot in peer_snapshots.items()
            if snapshot.price_reactions
        },
        idea_gate_results=idea_gate_results,
        event_window_reactions=event_window_reactions,
        management_sources=management_package,
        external_evidence=external_evidence,
        thesis_brief=thesis_synthesis.thesis_brief,
        ic_one_pager=ic_one_pager,
        thesis_critique=thesis_synthesis.thesis_critique,
        evidence_sufficiency=thesis_synthesis.evidence_sufficiency,
        action_plan=thesis_synthesis.action_plan,
        llm_run_manifest=thesis_synthesis.llm_manifest,
        llm_reviews=thesis_synthesis.llm_reviews,
        llm_comparison=thesis_synthesis.llm_comparison,
        language_audit=thesis_synthesis.language_audit,
        historical_references=historical_references,
        thesis_validation=thesis_validation,
        conviction_audit=conviction_audit,
        budget_policy=budget_policy,
        manual_data_status=manual_data_status,
        company_economics=company_economics,
        credit_lens=credit_lens,
        thesis_clusters=thesis_clusters,
        research_questions=research_questions,
        research_scout=research_scout,
        market_capture_readiness=market_capture_readiness,
        validated_claims=validated_claims,
        source_plan=source_plan,
        llm_extraction_manifest=llm_extraction_manifest,
        event_workflow=event_workflow,
        wisburg_lens=wisburg_lens,
        profiling=profiling,
        coverage_expansion=coverage_expansion,
        news_claims=news_claims,
        primary_source_observations=primary_source_observations,
        source_corroboration_results=source_corroboration_results,
        causal_bridges=causal_bridges,
        global_peer_coverage=global_peer_coverage,
        peer_metric_readthrough=peer_metric_readthrough,
        llm_research_manifest=llm_research_manifest,
        llm_trend_analysis=llm_trend_analysis,
        evidence_work_order=evidence_work_order,
        coverage_case=coverage_case,
        source_coverage_matrix=source_coverage_matrix,
        metric_resolution_audit=metric_resolution_audit,
        evidence_closure=evidence_closure,
        causal_thesis_graphs=causal_thesis_graphs,
        market_implied_expectations=market_implied_expectations,
        company_model=company_model,
        research_modes=research_modes,
        contextual_disclosure_comparisons=contextual_disclosure_comparisons,
        research_profile=selected_profile,
        historical_research=historical_research,
        metric_assessments=metric_assessments,
        promotion_evidence=promotion_evidence,
        promotion_decisions=[
            idea.promotion_decision for idea in ideas if idea.promotion_decision is not None
        ],
        playbook_portfolio=playbook_portfolio,
        earnings_surprise_proxy=earnings_surprise_proxy,
        recent_market_context=recent_market_context,
        **story_presentation,
    )


def _recent_market_context(
    price_client: object,
    ticker: str,
    price_reaction: object | None,
) -> RecentMarketContext:
    builder = getattr(price_client, "recent_market_context", None)
    if callable(builder):
        return builder(ticker)
    latest_price = getattr(price_reaction, "latest_price", None)
    source = str(getattr(price_reaction, "source", None) or type(price_client).__name__)
    return RecentMarketContext(
        ticker=ticker.upper(),
        status="Partial" if latest_price is not None else "Unavailable",
        source=source,
        summary=(
            "The configured price adapter supplies event reactions but not trailing market-context bars; "
            "relative momentum, volatility, drawdown, and volume diagnostics were not calculated."
        ),
        current_price=latest_price,
        data_gaps=[
            "This price adapter does not implement recent_market_context; use the shared EODHD/Tiingo/Stooq price stack for trailing diagnostics."
        ],
    )


def _inspect_profile_history(
    sec: SecClient,
    cik: str,
    filings: list[FilingRecord],
    profile: ResearchProfile,
    current_events: list[ChangeEvent],
    investigate_event_id: str | None,
    text_cache: dict[str, str] | None = None,
) -> tuple[set[str], list[str]]:
    allowed_forms = {"10-Q", "10-K", "20-F", "40-F", "6-K"}
    if profile.event_scoped:
        selected = next(
            (item for item in current_events if event_identifier(item) == investigate_event_id),
            None,
        )
        selected_forms = {
            citation.form for citation in (selected.citations if selected else []) if citation.form
        }
        if selected_forms:
            allowed_forms &= selected_forms
        elif selected is None:
            return set(), []
    parsed: set[str] = set()
    summaries: list[str] = []
    text_cache = text_cache if text_cache is not None else {}
    annual_limit = max(profile.annual_depth, 5 if profile.adaptive_deepening else profile.annual_depth)
    form_limits = {
        "10-Q": profile.quarter_depth,
        "6-K": profile.quarter_depth,
        "10-K": annual_limit,
        "20-F": annual_limit,
        "40-F": annual_limit,
    }
    comparison_pairs: list[tuple[str, FilingRecord, FilingRecord, int]] = []
    for form in sorted(allowed_forms):
        form_rows = [item for item in filings if item.form == form]
        if form == "6-K":
            result_rows = [
                item for item in form_rows
                if any(token in (item.description or "").lower() for token in ("result", "earn", "interim", "quarter"))
            ]
            if result_rows:
                form_rows = result_rows
        form_rows = form_rows[: form_limits[form]]
        for current in form_rows:
            previous = _period_aligned_prior(current, form_rows)
            if previous is None:
                continue
            comparison_pairs.append((form, current, previous, len(form_rows)))

    unique_filings = {
        filing.accession: filing
        for _, current, previous, _ in comparison_pairs
        for filing in (current, previous)
        if filing.accession not in text_cache
    }
    workers = min(config.RESEARCH_IO_WORKERS, len(unique_filings))
    if workers:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sec-history") as executor:
            futures = {
                executor.submit(_profile_filing_text, sec, filing, {}): accession
                for accession, filing in unique_filings.items()
            }
            for future in as_completed(futures):
                try:
                    text_cache[futures[future]] = future.result()
                except Exception:
                    text_cache[futures[future]] = ""

    for form, current, previous, candidate_count in comparison_pairs:
        try:
            current_text = text_cache.get(current.accession) or _profile_filing_text(sec, current, text_cache)
            previous_text = text_cache.get(previous.accession) or _profile_filing_text(sec, previous, text_cache)
        except Exception:
            continue
        if not current_text or not previous_text:
            continue
        parsed.update((current.accession, previous.accession))
        comparison_summary = _historical_comparison_summary(
            sec, form, current, previous, current_text, previous_text, candidate_count,
        )
        if comparison_summary:
            summaries.append(comparison_summary)
    return parsed, list(dict.fromkeys(summaries))[:20]


def _profile_filing_text(
    sec: SecClient, filing: FilingRecord, cache: dict[str, str],
) -> str:
    if filing.accession not in cache:
        parsed_path = _parsed_filing_cache_path(sec, filing)
        if parsed_path is not None and parsed_path.exists() and time.time() - parsed_path.stat().st_mtime < 7 * 24 * 60 * 60:
            cache[filing.accession] = parsed_path.read_text(encoding="utf-8", errors="ignore")
        else:
            cache[filing.accession] = _analysis_text_for_form(
                html_to_text(sec.get_filing_text(filing)), filing.form,
            )
            if parsed_path is not None and cache[filing.accession]:
                parsed_path.write_text(cache[filing.accession], encoding="utf-8")
    return cache[filing.accession]


def _parsed_filing_cache_path(sec: SecClient, filing: FilingRecord):
    cache_dir = getattr(sec, "cache_dir", None)
    if cache_dir is None:
        return None
    digest = hashlib.sha1(
        f"filing-analysis-v2|{filing.form}|{filing.url}".encode("utf-8")
    ).hexdigest()
    return cache_dir / f"parsed-{digest}.cache"


def _historical_comparison_summary(
    sec: SecClient,
    form: str,
    current: FilingRecord,
    previous: FilingRecord,
    current_text: str,
    previous_text: str,
    candidate_count: int,
) -> str:
    cache_dir = getattr(sec, "cache_dir", None)
    cache_path = None
    if cache_dir is not None:
        digest = hashlib.sha1(
            (
                f"historical-comparison-v2|{form}|{current.accession}|"
                f"{previous.accession}|{candidate_count}"
            ).encode("utf-8")
        ).hexdigest()
        cache_path = cache_dir / f"comparison-{digest}.json"
        if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 7 * 24 * 60 * 60:
            try:
                return str(json.loads(cache_path.read_text(encoding="utf-8")).get("summary") or "")
            except (OSError, ValueError, TypeError):
                pass

    comparison_events = compare_filing_pair(
        current,
        current_text,
        previous,
        previous_text,
        prior_search_audit={
            "search_attempted": True,
            "candidates_considered": candidate_count,
            "sources_attempted": ["Profile historical SEC filing cache"],
        },
    )
    summary = ""
    if comparison_events:
        strongest = max(comparison_events, key=lambda item: item.severity)
        summary = (
            f"{form} {current.report_date or current.filing_date} versus "
            f"{previous.report_date or previous.filing_date}: {strongest.title}."
        )
    if cache_path is not None:
        try:
            cache_path.write_text(json.dumps({"summary": summary}), encoding="utf-8")
        except OSError:
            pass
    return summary


def _period_aligned_prior(
    current: FilingRecord, candidates: list[FilingRecord],
) -> FilingRecord | None:
    current_date = _filing_period_date(current)
    older = [item for item in candidates if item.accession != current.accession]
    if current_date:
        aligned: list[tuple[int, FilingRecord]] = []
        for item in older:
            prior_date = _filing_period_date(item)
            if not prior_date or prior_date >= current_date:
                continue
            delta = (current_date - prior_date).days
            if 250 <= delta <= 500:
                aligned.append((abs(delta - 365), item))
        if aligned:
            return min(aligned, key=lambda row: row[0])[1]
    return next(
        (
            item for item in older
            if (item.filing_date or "") < (current.filing_date or "")
        ),
        None,
    )


def _filing_period_date(filing: FilingRecord):
    value = filing.report_date or filing.filing_date
    try:
        return datetime.fromisoformat(value[:10]).date() if value else None
    except (TypeError, ValueError):
        return None


def _expand_profile_filings(
    sec: SecClient,
    cik: str,
    filings: list[FilingRecord],
    profile: ResearchProfile,
) -> list[FilingRecord]:
    annual_limit = max(profile.annual_depth, 5 if profile.adaptive_deepening else profile.annual_depth)
    requested = {
        "10-Q": profile.quarter_depth,
        "10-K": annual_limit,
        "20-F": annual_limit,
        "40-F": annual_limit,
    }
    combined = list(filings)
    fetcher = getattr(sec, "get_comparable_filings", None)
    if callable(fetcher):
        for form, limit in requested.items():
            current_count = sum(item.form == form for item in combined)
            if current_count >= limit:
                continue
            try:
                combined.extend(fetcher(cik, form, limit=limit))
            except Exception:
                continue
    deduped: dict[tuple[str, str], FilingRecord] = {}
    for filing in combined:
        deduped[(filing.accession, filing.form)] = filing
    return sorted(
        deduped.values(),
        key=lambda item: (item.filing_date or "", item.accepted_at or ""),
        reverse=True,
    )


def _compare_latest_pairs(
    sec: SecClient,
    cik: str,
    filings: list[FilingRecord],
    form: str,
    text_cache: dict[str, str] | None = None,
) -> list[ChangeEvent]:
    form_filings = [filing for filing in filings if filing.form == form]
    if len(form_filings) < 1:
        return []
    discovery_error = ""
    sources_attempted = ["SEC recent submissions"]
    if len(form_filings) < 2:
        try:
            comparable_fetcher = getattr(sec, "get_comparable_filings", None)
            if callable(comparable_fetcher):
                form_filings = comparable_fetcher(cik, form, limit=4)
                sources_attempted.append("SEC historical submissions archive")
            else:
                form_filings = sec.get_recent_filings(cik, forms={form}, limit=4)
        except Exception as exc:
            discovery_error = str(exc)
    if len(form_filings) < 1:
        return []
    text_cache = text_cache if text_cache is not None else {}
    current = form_filings[0]
    previous = form_filings[1] if len(form_filings) > 1 else None
    try:
        current_text = _profile_filing_text(sec, current, text_cache)
    except Exception as exc:
        current_text = ""
        discovery_error = str(exc)
    previous_text = None
    prior_parse_failed = False
    if previous:
        try:
            previous_text = _profile_filing_text(sec, previous, text_cache)
            prior_parse_failed = not bool(previous_text)
        except Exception as exc:
            discovery_error = str(exc)
    return compare_filing_pair(
        current,
        current_text,
        previous,
        previous_text,
        prior_search_audit={
            "search_attempted": True,
            "candidates_considered": max(0, len(form_filings) - 1),
            "sources_attempted": sources_attempted,
            "discovery_error": discovery_error,
            "parse_failed": prior_parse_failed,
        },
    )


def _events_from_recent_current_reports(filings: list[FilingRecord]) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []
    for filing in [item for item in filings if item.form in {"8-K", "6-K"}][:5]:
        description = filing.description or "Recent 8-K filing"
        if filing.form == "6-K":
            description = filing.description or "Recent 6-K furnished report"
        events.append(
            ChangeEvent(
                category="event_catalyst",
                title=f"Recent {filing.form}: {description[:80]}",
                summary=(
                    f"A recent {filing.form} may contain event-driven information "
                    "for the research queue."
                ),
                severity=2,
                direction="neutral",
                event_date=filing.filing_date,
                source=filing.form,
                citations=[],
                metrics={"accession": filing.accession},
                event_timestamp=filing.accepted_at,
            )
        )
    return events


def _ownership_events_from_filings(filings: list[FilingRecord]) -> list[ChangeEvent]:
    events: list[ChangeEvent] = []
    for filing in [item for item in filings if item.form in OWNERSHIP_FORMS][:5]:
        if filing.form.startswith("SC 13"):
            category = "ownership_change"
            title = f"Beneficial ownership filing: {filing.form}"
            summary = "A Schedule 13D/G beneficial ownership filing was reported for the issuer."
            severity = 4 if filing.form.startswith("SC 13D") else 3
        else:
            category = "insider_transaction"
            title = f"Insider ownership filing: Form {filing.form}"
            summary = "A Form 3/4/5 insider ownership filing was reported for the issuer."
            severity = 3
        events.append(ChangeEvent(
            category=category,
            title=title,
            summary=summary,
            severity=severity,
            direction="neutral",
            event_date=filing.filing_date,
            source="SEC ownership filing",
            citations=[
                Citation(
                    source="SEC EDGAR ownership filing",
                    url=filing.url,
                    filed=filing.filing_date,
                    form=filing.form,
                    section="Ownership disclosure",
                    snippet=summary,
                    accession=filing.accession,
                    period_end=filing.report_date,
                    retrieved_at=run_observed_at(),
                    source_tier=1,
                )
            ],
            metrics={"form": filing.form, "accession": filing.accession},
        ))
    return events


def _management_filing_texts(sec: SecClient, filings: list[FilingRecord]) -> dict[str, str]:
    selected = filings[:12]
    if not selected:
        return {}
    fetched: dict[str, str] = {}
    workers = min(config.RESEARCH_IO_WORKERS, len(selected))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="sec-management") as executor:
        futures = {
            executor.submit(sec.get_filing_text, filing): filing.accession
            for filing in selected
        }
        for future in as_completed(futures):
            try:
                fetched[futures[future]] = future.result()
            except Exception:
                continue
    return {
        filing.accession: fetched[filing.accession]
        for filing in selected
        if filing.accession in fetched
    }


def _transcript_rows_from_turns(turns: list) -> list[dict]:
    grouped: dict[str, list] = {}
    for turn in turns:
        grouped.setdefault(turn.document_id, []).append(turn)
    rows: list[dict] = []
    for document_id, document_turns in grouped.items():
        sorted_turns = sorted(document_turns, key=lambda item: item.turn_index)
        sentiment_scores = [turn.sentiment_score for turn in sorted_turns if turn.sentiment_score is not None]
        uncertainty = [len(turn.uncertainty_terms) for turn in sorted_turns]
        evasion = [len(turn.evasion_terms) for turn in sorted_turns]
        specificity = [turn.specificity_score for turn in sorted_turns if turn.specificity_score is not None]
        rows.append({
            "period": document_id,
            "text": " ".join(turn.text for turn in sorted_turns),
            "sentiment_score": _avg(sentiment_scores),
            "uncertainty_score": _avg(uncertainty),
            "evasion_score": _avg(evasion),
            "specificity_score": _avg(specificity),
        })
    return rows


def _avg(values: list[float | int]) -> float | None:
    return round(sum(float(value) for value in values) / len(values), 3) if values else None


def run_observed_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _price_reactions_for_events(
    price_client: StooqPriceClient,
    identity: CompanyIdentity,
    events: list[ChangeEvent],
) -> dict[str, PriceReaction]:
    """Measure every event from its own date and keep older test adapters compatible."""
    reactions: dict[str, PriceReaction] = {}
    benchmark = _benchmark_for_identity(identity)
    event_dates = sorted({event.event_date for event in events if event.event_date})
    for event_date in event_dates:
        try:
            reaction = price_client.price_reaction_since(
                identity.ticker,
                event_date,
                benchmark_ticker=benchmark,
            )
        except TypeError:
            reaction = price_client.price_reaction_since(identity.ticker, event_date)
        reactions[event_date] = reaction
    return reactions


def _benchmark_for_identity(identity: CompanyIdentity) -> str:
    adr_profile = adr_profile_for(identity.ticker)
    if adr_profile and adr_profile.benchmark_tickers:
        return adr_profile.benchmark_tickers[0]
    try:
        sic = int(identity.sic or 0)
    except ValueError:
        sic = 0
    if 6000 <= sic <= 6799:
        return "XLF"
    if 1300 <= sic <= 1399 or 2900 <= sic <= 2999:
        return "XLE"
    if 2830 <= sic <= 2839 or 8000 <= sic <= 8099:
        return "XLV"
    if 3570 <= sic <= 3579 or 7370 <= sic <= 7379:
        return "XLK"
    if 4800 <= sic <= 4899:
        return "XLC"
    if sic == 6798:
        return "XLRE"
    return "SPY"


def _event_window_reactions_for_events(
    price_client: StooqPriceClient,
    identity: CompanyIdentity,
    events: list[ChangeEvent],
) -> dict[str, EventWindowReaction]:
    reactions: dict[str, EventWindowReaction] = {}
    sector_benchmark = _benchmark_for_identity(identity)
    for index, event in enumerate(events):
        if not event.event_date:
            continue
        event_id = f"{event.category}:{event.event_date}:{index}"
        reactions[event_id] = _event_window_reaction(
            price_client,
            identity.ticker,
            event_id,
            event.event_date,
            event.event_timestamp,
            sector_benchmark,
        )
    return reactions


def _attach_share_reconciliation(identity: CompanyIdentity, events: list[ChangeEvent]) -> None:
    for event in events:
        reconciliation = reconcile_share_event(identity, event)
        if not reconciliation:
            continue
        event.metrics["share_reconciliation"] = asdict(reconciliation)
        event.metrics["share_reconciliation_status"] = reconciliation.status
        event.metrics["normalization_status"] = reconciliation.status
        if reconciliation.status != "Reconciled":
            event.metrics["normalization_required"] = True
            if reconciliation.data_gaps:
                event.metrics["normalization_reason"] = reconciliation.data_gaps[0]


def _legacy_reactions_from_event_windows(
    reactions: dict[str, EventWindowReaction],
) -> dict[str, PriceReaction]:
    by_date: dict[str, PriceReaction] = {}
    for reaction in reactions.values():
        if not reaction.event_date:
            continue
        selected_window = "5d" if reaction.raw_returns.get("5d") is not None else "1d"
        raw = reaction.raw_returns.get(selected_window)
        market_relative = reaction.market_relative_returns.get(selected_window)
        beta_adjusted = reaction.beta_adjusted_returns.get(selected_window)
        benchmark = (
            raw - market_relative
            if raw is not None and market_relative is not None else None
        )
        by_date[reaction.event_date] = PriceReaction(
            ticker=reaction.ticker,
            event_date=reaction.event_date,
            start_price=reaction.prior_close,
            latest_price=None,
            reaction_pct=raw,
            source=reaction.source,
            note=reaction.reason,
            benchmark_ticker=reaction.benchmark_ticker,
            benchmark_reaction_pct=benchmark,
            abnormal_reaction_pct=beta_adjusted if beta_adjusted is not None else market_relative,
            volume_ratio=reaction.volume_ratio,
            beta=reaction.beta,
            return_1d_pct=reaction.raw_returns.get("1d"),
            return_5d_pct=reaction.raw_returns.get("5d"),
            return_20d_pct=reaction.raw_returns.get("20d"),
            abnormal_20d_pct=(
                reaction.beta_adjusted_returns.get("20d")
                if reaction.beta_adjusted_returns.get("20d") is not None
                else reaction.market_relative_returns.get("20d")
            ),
            path_min_20d_pct=reaction.path_min_20d_pct,
            path_max_20d_pct=reaction.path_max_20d_pct,
        )
    return by_date


def _event_window_reaction(
    price_client,
    ticker: str,
    event_id: str,
    event_date: str | None,
    event_timestamp: str | None,
    sector_benchmark: str | None,
) -> EventWindowReaction:
    method = getattr(price_client, "event_window_reaction", None)
    if method:
        return method(
            ticker,
            event_id,
            event_date,
            event_timestamp,
            "SPY",
            sector_benchmark,
        )
    legacy = price_client.price_reaction_since(ticker, event_date)
    status = "available" if legacy.reaction_pct is not None else _price_failure_status(legacy.note)
    return EventWindowReaction(
        ticker=ticker.upper(),
        event_id=event_id,
        event_date=event_date,
        event_timestamp=event_timestamp,
        anchor_date=event_date,
        prior_close=legacy.start_price,
        source=legacy.source,
        status=status,
        reason=legacy.note,
        confidence="Low",
        benchmark_ticker=legacy.benchmark_ticker,
        sector_benchmark_ticker=sector_benchmark,
        beta=legacy.beta,
        volume_ratio=legacy.volume_ratio,
        raw_returns={
            "1d": legacy.return_1d_pct if legacy.return_1d_pct is not None else legacy.reaction_pct,
            "5d": legacy.return_5d_pct,
            "20d": legacy.return_20d_pct,
        },
        beta_adjusted_returns={"20d": legacy.abnormal_20d_pct},
        path_min_20d_pct=legacy.path_min_20d_pct,
        path_max_20d_pct=legacy.path_max_20d_pct,
        corporate_action_adjusted=False,
    )


def _price_failure_status(note: str) -> str:
    lower = (note or "").lower()
    if "blocked" in lower or "verification" in lower or "javascript" in lower:
        return "provider_blocked"
    if "malformed" in lower or "not valid" in lower:
        return "malformed_response"
    if "pending" in lower:
        return "window_pending"
    if "event date" in lower:
        return "event_date_missing"
    if "trading day" in lower or "history" in lower:
        return "insufficient_history"
    if "timeout" in lower:
        return "timeout"
    if "symbol" in lower or "no prices" in lower:
        return "unsupported_symbol"
    return "price_unavailable"


def _build_peer_snapshots(
    universe: PeerUniverse,
    sec: SecClient,
    price_client: StooqPriceClient,
    focal_events: list[ChangeEvent],
    global_peer_provider: GlobalPeerFinancialProvider | None = None,
) -> dict[str, _PeerSnapshot]:
    if not universe.peers:
        return {}
    sector_benchmark = _sector_benchmark_for_universe(universe)
    global_provider = global_peer_provider or GlobalPeerFinancialProvider()
    completed: dict[str, _PeerSnapshot] = {}
    workers = min(config.RESEARCH_IO_WORKERS, len(universe.peers))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="peer-research") as executor:
        futures = {
            executor.submit(
                _build_single_peer_snapshot,
                peer_definition.ticker,
                universe.ticker,
                sec,
                price_client,
                focal_events,
                sector_benchmark,
                global_provider,
            ): peer_definition.ticker
            for peer_definition in universe.peers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                completed[ticker] = future.result()
            except Exception as exc:  # pragma: no cover - defensive boundary
                completed[ticker] = _PeerSnapshot(ticker, [], [], sec_error=str(exc))
    return {
        peer.ticker: completed[peer.ticker]
        for peer in universe.peers
        if peer.ticker in completed
    }


def _build_single_peer_snapshot(
    peer_ticker: str,
    focal_ticker: str,
    sec: SecClient,
    price_client: StooqPriceClient,
    focal_events: list[ChangeEvent],
    sector_benchmark: str,
    global_provider: GlobalPeerFinancialProvider,
) -> _PeerSnapshot:
    price_reactions = {
        f"{event.category}:{event.event_date}:{index}": _event_window_reaction(
            price_client,
            peer_ticker,
            f"focal:{focal_ticker}:{event.category}:{event.event_date}:{index}",
            event.event_date,
            event.event_timestamp,
            sector_benchmark,
        )
        for index, event in enumerate(focal_events)
        if event.event_date
    }
    metrics: list[FinancialMetric] = []
    peer_events: list[ChangeEvent] = []
    sec_error = None
    global_coverage = None
    try:
        identity = sec.map_ticker(peer_ticker)
        filings = sec.get_recent_filings(
            identity.cik, forms=SUPPORTED_US_LISTED_FORMS, limit=30,
        )
        periodic_filings = sec.get_recent_filings(
            identity.cik, forms={"10-K", "10-Q", "20-F", "40-F"}, limit=4,
        )
        filings = _dedupe_filings(filings + periodic_filings)
        facts_url = SEC_FACTS_URL.format(cik=identity.cik)
        facts = sec.get_company_facts(identity.cik)
        metrics = build_financial_metrics(facts)
        for metric in metrics:
            if not metric.source_url:
                metric.source_url = facts_url
        profile = adr_profile_for(peer_ticker)
        periodic_filing = next(
            (filing for filing in filings if filing.form in {"10-K", "10-Q", "20-F", "40-F"}),
            None,
        )
        needs_newer_fpi_filing = bool(
            profile and periodic_filing
            and _filing_is_newer_than_metrics(periodic_filing, metrics, focal_events)
        )
        if _peer_metrics_need_recovery(metrics, focal_events) or needs_newer_fpi_filing:
            preferred_currency = profile.reporting_currency if profile else None
            if periodic_filing:
                try:
                    recovered = build_periodic_inline_xbrl_financial_metrics(
                        sec.get_filing_text(periodic_filing), periodic_filing,
                        preferred_currency=preferred_currency,
                    )
                    metrics = _merge_peer_metrics(metrics, recovered)
                except Exception:
                    pass
        peer_events = financial_change_events(metrics, facts_url)
        peer_events.extend(_events_from_recent_current_reports(filings))
    except Exception as exc:  # pragma: no cover - defensive network boundary
        sec_error = str(exc)
    profile = adr_profile_for(peer_ticker)
    if sec_error or not metrics or (profile and _peer_metrics_need_recovery(metrics, focal_events)):
        global_coverage = global_provider.fetch(peer_ticker)
        global_metrics = coverage_metrics_as_financial_metrics(global_coverage)
        if global_metrics:
            metrics = _merge_peer_metrics(metrics, global_metrics)
            peer_events = financial_change_events(
                metrics, global_coverage.documents[0].url if global_coverage.documents else "",
            )
            sec_error = None
    own_reactions = [
        _event_window_reaction(
            price_client,
            peer_ticker,
            f"peer:{peer_ticker}:{event.category}:{event.event_date}:{index}",
            event.event_date,
            event.event_timestamp,
            sector_benchmark,
        )
        for index, event in enumerate(peer_events[:5])
        if event.event_date
    ]
    return _PeerSnapshot(
        ticker=peer_ticker,
        metrics=metrics,
        events=peer_events,
        price_reactions=price_reactions,
        own_event_reactions=own_reactions,
        sec_error=sec_error,
        global_coverage=global_coverage,
    )


def _peer_metrics_need_recovery(
    metrics: list[FinancialMetric],
    focal_events: list[ChangeEvent],
) -> bool:
    if not metrics:
        return True
    for event in focal_events:
        family = _metric_family_for_event(event)
        related = _metrics_for_family(family)
        aligned = [
            metric.name for metric in metrics
            if metric.name in related and _metric_period_status(event, metric) != "stale_period"
        ]
        if _blocking_missing_metrics(family, related, aligned):
            return True
    return False


def _merge_peer_metrics(
    existing: list[FinancialMetric],
    recovered: list[FinancialMetric],
) -> list[FinancialMetric]:
    by_name = {metric.name: metric for metric in existing}
    for metric in recovered:
        current = by_name.get(metric.name)
        if current is None or (metric.period_end or "") > (current.period_end or ""):
            by_name[metric.name] = metric
    return sorted(by_name.values(), key=lambda item: item.name)


def _dedupe_filings(filings: list[FilingRecord]) -> list[FilingRecord]:
    by_accession = {filing.accession: filing for filing in filings}
    return sorted(
        by_accession.values(),
        key=lambda filing: (filing.filing_date or "", filing.report_date or ""),
        reverse=True,
    )


def _filing_is_newer_than_metrics(
    filing: FilingRecord,
    metrics: list[FinancialMetric],
    focal_events: list[ChangeEvent],
) -> bool:
    relevant_names = {
        metric_name
        for event in focal_events
        for metric_name in _metrics_for_family(_metric_family_for_event(event))
    }
    relevant_metrics = [metric for metric in metrics if metric.name in relevant_names]
    comparison_metrics = relevant_metrics or metrics
    latest_metric_period = max(
        (metric.period_end or "" for metric in comparison_metrics),
        default="",
    )
    filing_period = filing.report_date or filing.filing_date or ""
    return bool(filing_period and filing_period > latest_metric_period)


def _sector_benchmark_for_universe(universe: PeerUniverse) -> str:
    lower = universe.sector_template.lower()
    if any(token in lower for token in ("bank", "financial", "broker")):
        return "XLF"
    if "energy" in lower:
        return "XLE"
    if any(token in lower for token in ("pharma", "health")):
        return "XLV"
    if any(token in lower for token in ("technology", "semiconductor")):
        return "XLK"
    return "SPY"


def _attach_peer_readthroughs(
    ideas: list[TradeIdea],
    peer_snapshots: dict[str, _PeerSnapshot],
) -> None:
    if not peer_snapshots:
        return
    for idea in ideas:
        source_event = idea.source_events[0] if idea.source_events else None
        if not source_event:
            continue
        idea.peer_readthrough = [
            _peer_readthrough_for_event(source_event, snapshot)
            for snapshot in peer_snapshots.values()
        ]
        idea.peer_metric_readthrough = [
            readthrough.metric_readthrough
            for readthrough in idea.peer_readthrough
            if readthrough.metric_readthrough is not None
        ]
        idea.peer_metric_summary = _peer_metric_summary(idea.peer_readthrough, idea.peer_metric_readthrough)


def _peer_metric_summary(
    peer_readthroughs: list[PeerReadthrough],
    metric_readthroughs: list[PeerMetricReadthrough],
) -> PeerMetricReadthroughSummary:
    total = len(peer_readthroughs)
    if not total:
        return PeerMetricReadthroughSummary(
            status="Unavailable",
            score=0,
            summary="No peer universe was available, so peer operating read-through cannot be assessed.",
            stage_impact="Peer evidence cannot support the thesis until a curated peer universe is configured.",
            next_actions=["Configure a curated peer universe and rerun peer metric checks."],
        )
    available = [
        item for item in metric_readthroughs
        if item.status == "available" and item.observations
    ]
    stale = [item for item in peer_readthroughs if item.failure_status == "stale_period"]
    missing = [
        item for item in metric_readthroughs
        if item.status != "available" or not item.observations
    ]
    metric_peer_names = {item.peer_ticker for item in metric_readthroughs}
    no_metric = [item for item in peer_readthroughs if item.peer_ticker not in metric_peer_names]
    price_only = [
        item for item in peer_readthroughs
        if item.price_reaction_pct is not None and (
            item.peer_ticker not in metric_peer_names
            or item.peer_ticker in {missing_item.peer_ticker for missing_item in missing}
            or item.failure_status == "stale_period"
        )
    ]
    global_peers = [
        item for item in peer_readthroughs
        if item.global_peer_coverage is not None
    ]
    families = _dedupe_list([item.metric_family for item in metric_readthroughs if item.metric_family])
    confirmations = [
        f"{item.peer_ticker}: {item.summary}"
        for item in available
        if item.relation.startswith("Confirming")
    ][:3]
    contradictions = [
        f"{item.peer_ticker}: {item.summary}"
        for item in available
        if item.relation.startswith("Contradicting")
    ][:3]
    gaps: list[str] = []
    if not available:
        gaps.append("No current aligned peer operating metrics are available; peer evidence is price-sympathy only or missing.")
    if stale:
        gaps.append(f"{len(stale)} peer(s) have stale or fiscally unaligned metric evidence.")
    if missing or no_metric:
        gaps.append(f"{len(missing) + len(no_metric)} peer(s) lack aligned metrics for the focal metric family.")
    if global_peers and not any(item.status == "available" for item in available):
        gaps.append("Registered global peers need official-document extraction before they can support operating read-through.")

    next_actions: list[str] = []
    if stale:
        next_actions.append("Refresh peer financial facts or official filings for the same fiscal period as the focal event.")
    if missing or no_metric:
        next_actions.append("Pull peer metrics from SEC Companyfacts, official global reports, or manual KPI imports for the same metric family.")
    if global_peers:
        next_actions.append("For global peers, run official-source extraction and validate table/page citations before using the comparison.")
    if not next_actions:
        next_actions.append("Use this peer read-through as supporting context; do not substitute it for company-specific source evidence.")

    if len(available) >= 2 and not stale:
        status = "Usable"
        score = 80 if not gaps else 72
        summary = (
            f"{len(available)}/{total} peer(s) have aligned operating metrics for {', '.join(families) or 'the focal driver'}. "
            f"{_peer_metric_conclusion(available)}"
        )
        stage_impact = "Peer operating evidence can support Research-Ready analysis but does not replace company-specific evidence."
    elif available:
        status = "Partial"
        score = 60
        summary = (
            f"{len(available)}/{total} peer(s) have aligned operating metrics; treat peer read-through as incomplete. "
            f"{_peer_metric_conclusion(available)}"
        )
        stage_impact = "Peer evidence can support a candidate, but missing/stale peers should remain an explicit diligence gap."
    elif stale or price_only:
        status = "Weak - missing/stale"
        score = 35
        summary = "Peer operating read-through is weak because aligned metrics are stale, missing, or only price-sympathy evidence exists."
        stage_impact = "Do not use peer sympathy moves as operating confirmation until metric evidence is validated."
    else:
        status = "Unavailable"
        score = 20
        summary = "Peer operating metric read-through is unavailable for the current idea."
        stage_impact = "Peer read-through cannot support promotion until current same-driver peer metrics are available."

    return PeerMetricReadthroughSummary(
        status=status,
        score=score,
        summary=summary,
        total_peers=total,
        operating_metric_peers=len(available),
        missing_metric_peers=len(missing) + len(no_metric),
        stale_metric_peers=len(stale),
        price_only_peers=len(price_only),
        global_peer_peers=len(global_peers),
        metric_families=families,
        confirmations=confirmations,
        contradictions=contradictions,
        data_gaps=_dedupe_list(gaps),
        next_actions=_dedupe_list(next_actions),
        stage_impact=stage_impact,
    )


def _peer_metric_conclusion(available: list[PeerMetricReadthrough]) -> str:
    confirming = [item.peer_ticker for item in available if item.relation.startswith("Confirming")]
    contradicting = [item.peer_ticker for item in available if item.relation.startswith("Contradicting")]
    mixed = [item.peer_ticker for item in available if item.relation.startswith("Mixed")]
    if confirming and not contradicting:
        return f"Peer operating data mostly confirms the direction ({', '.join(confirming[:4])})."
    if contradicting and not confirming:
        return f"Peer operating data mostly contradicts the direction ({', '.join(contradicting[:4])}), so the thesis may be company-specific or wrong."
    if confirming and contradicting:
        return (
            f"Peer operating data is mixed: confirming peers include {', '.join(confirming[:3])}; "
            f"contradicting peers include {', '.join(contradicting[:3])}."
        )
    if mixed:
        return f"Peer operating data is available but directionally mixed or unclear ({', '.join(mixed[:4])})."
    return "Peer operating data is available, but it does not establish a directional read-through."


def _peer_readthrough_for_event(
    source_event: ChangeEvent,
    snapshot: _PeerSnapshot,
) -> PeerReadthrough:
    event_key = next(
        (
            key for key in (snapshot.price_reactions or {})
            if f":{source_event.event_date}:" in key
        ),
        None,
    )
    reaction = (snapshot.price_reactions or {}).get(event_key or "")
    price_pct = reaction.raw_returns.get("1d") if reaction else None
    price_failure = reaction.status if reaction and reaction.status != "available" else None
    if snapshot.sec_error:
        if snapshot.global_coverage and snapshot.global_coverage.identity:
            return PeerReadthrough(
                peer_ticker=snapshot.ticker,
                evidence_status="Global peer coverage unavailable",
                relation="No direct evidence",
                price_reaction_pct=price_pct,
                conclusion=(
                    f"{snapshot.ticker} has a global peer profile, but official-source extraction did not "
                    f"produce usable metrics: {'; '.join(snapshot.global_coverage.data_gaps[:2]) or snapshot.sec_error}"
                ),
                failure_status=snapshot.global_coverage.status or "sec_unavailable",
                failure_reason="; ".join(snapshot.global_coverage.data_gaps) or snapshot.sec_error,
                sympathy_reaction=reaction,
                global_peer_coverage=snapshot.global_coverage,
            )
        if snapshot.global_coverage:
            return PeerReadthrough(
                peer_ticker=snapshot.ticker,
                evidence_status="Unavailable",
                relation="No direct evidence",
                price_reaction_pct=price_pct,
                conclusion=(
                    f"SEC evidence unavailable for {snapshot.ticker}, and no registered global peer profile "
                    "was configured for official-source fallback."
                ),
                failure_status="unsupported_global_peer",
                failure_reason="; ".join(snapshot.global_coverage.data_gaps) or snapshot.sec_error,
                sympathy_reaction=reaction,
                global_peer_coverage=snapshot.global_coverage,
            )
        return PeerReadthrough(
            peer_ticker=snapshot.ticker,
            evidence_status="Unavailable",
            relation="No direct evidence",
            price_reaction_pct=price_pct,
            conclusion=f"SEC evidence unavailable for {snapshot.ticker}: {snapshot.sec_error}",
            failure_status="sec_unavailable",
            failure_reason=snapshot.sec_error,
            sympathy_reaction=reaction,
        )

    matching_events = _matching_peer_events(source_event, snapshot.events)
    own_reaction = (snapshot.own_event_reactions or [None])[0]
    stale_events = _stale_matching_peer_events(source_event, snapshot.events)
    metric_audit = _peer_metric_readthrough_for_event(source_event, snapshot)
    if matching_events:
        relation = _peer_relation(source_event, matching_events)
        key_changes = [event.summary for event in matching_events[:3]]
        conclusion = _peer_conclusion(snapshot.ticker, source_event, relation, key_changes)
        citations = [citation for event in matching_events for citation in event.citations][:5]
        return PeerReadthrough(
            peer_ticker=snapshot.ticker,
            evidence_status="Direct evidence found",
            relation=relation,
            key_metric_changes=key_changes,
            price_reaction_pct=price_pct,
            conclusion=conclusion,
            citations=citations,
            failure_status=price_failure,
            failure_reason=reaction.reason if price_failure and reaction else None,
            sympathy_reaction=reaction,
            own_event_reaction=own_reaction,
            fiscal_alignment=_fiscal_alignment(source_event, matching_events[0]),
            metric_readthrough=metric_audit,
            global_peer_coverage=snapshot.global_coverage,
        )

    metric_readthrough = _metric_readthrough_for_event(source_event, snapshot, price_pct)
    if metric_readthrough:
        metric_readthrough.failure_status = price_failure
        metric_readthrough.failure_reason = reaction.reason if price_failure and reaction else None
        metric_readthrough.sympathy_reaction = reaction
        metric_readthrough.own_event_reaction = own_reaction
        metric_readthrough.global_peer_coverage = snapshot.global_coverage
        return metric_readthrough

    if stale_events:
        return PeerReadthrough(
            peer_ticker=snapshot.ticker,
            evidence_status="Stale peer metric",
            relation="No direct evidence",
            key_metric_changes=[
                "Related peer KPI data exists, but it is stale or not aligned to the source event period."
            ],
            price_reaction_pct=price_pct,
            conclusion=(
                f"{snapshot.ticker} has related KPI evidence, but it is too stale or fiscally unaligned "
                "to support this read-through."
            ),
            failure_status="stale_period",
            failure_reason="Peer facts are not close enough to the focal event period.",
            sympathy_reaction=reaction,
            own_event_reaction=own_reaction,
            metric_readthrough=metric_audit,
            global_peer_coverage=snapshot.global_coverage,
        )

    return PeerReadthrough(
        peer_ticker=snapshot.ticker,
        evidence_status="No direct evidence found",
        relation="No direct evidence",
        key_metric_changes=["No aligned same-category KPI change was found in available peer financial sources."],
        price_reaction_pct=price_pct,
        conclusion=(
            f"No direct peer evidence was found for {snapshot.ticker}. The source idea may be "
            "company-specific, or peer data may be missing from available peer financial sources."
        ),
        failure_status=price_failure,
        failure_reason=reaction.reason if price_failure and reaction else None,
        sympathy_reaction=reaction,
        own_event_reaction=own_reaction,
        metric_readthrough=metric_audit,
        global_peer_coverage=snapshot.global_coverage,
    )


def _build_llm_research_manifest(
    global_peer_coverage: dict[str, GlobalPeerCoverage],
) -> LlmResearchAgentManifest:
    document_ids = [
        document.document_id
        for coverage in global_peer_coverage.values()
        for document in coverage.documents
    ]
    metric_ids = [
        metric.observation_id
        for coverage in global_peer_coverage.values()
        for metric in coverage.metrics
    ]
    status = "Available" if global_peer_coverage else "Not required"
    messages = (
        ["Deterministic global-peer source planning and extraction ran; LLM assistant lanes remain provisional."]
        if global_peer_coverage else
        ["No global peer profile was needed for this run."]
    )
    return LlmResearchAgentManifest(
        provider="deterministic",
        model="none",
        prompt_version="global-peer-research-agent-v1",
        generated_at=_utc_now(),
        status=status,
        document_ids=document_ids,
        metric_draft_ids=metric_ids,
        messages=messages,
        redacted_config={"llm_used": "false"},
        allowed_roles=[
            "source_planning_from_registered_source_types",
            "official_document_triage",
            "provisional_metric_extraction_drafts",
            "validated_trend_summarization",
            "IC_narrative_from_validated_claims_only",
        ],
        prohibited_actions=[
            "free_browsing_or_arbitrary_trusted_URL_creation",
            "inventing_facts_citations_targets_or_probabilities",
            "promoting_candidates_or_overriding_deterministic_gates",
            "treating_external_research_or_news_as_standalone_high_conviction_proof",
            "persisting_raw_vendor_payloads_or_API_keys",
        ],
        source_registry_version="source-registry-v1",
        deterministic_executor="registered_source_adapters_and_validators",
        validation_gates=[
            "allowed_source_type_filter",
            "source_url_and_citation_required",
            "metric_period_unit_currency_validation",
            "primary_source_or_explicit_gap_for_promotion",
            "no_high_conviction_without_evidence_gates",
        ],
    )


def _is_reportable_global_coverage(coverage: GlobalPeerCoverage | None) -> bool:
    if coverage is None:
        return False
    return coverage.identity is not None or coverage.status != "unsupported_global_peer"


def _build_llm_trend_analysis(
    global_peer_coverage: dict[str, GlobalPeerCoverage],
) -> LlmTrendAnalysis:
    metrics = [
        metric
        for coverage in global_peer_coverage.values()
        for metric in coverage.metrics
    ]
    if not metrics:
        return LlmTrendAnalysis(
            status="Unavailable",
            summary="No validated global peer metric observations were available for trend analysis.",
            data_gaps=[
                gap
                for coverage in global_peer_coverage.values()
                for gap in coverage.data_gaps
            ],
        )
    patterns = [
        f"{metric.peer_ticker} {metric.metric}: {format_number(metric.value)} {metric.unit}"
        + (f" ({metric.yoy_change_pct:+.1f}% YoY)" if metric.yoy_change_pct is not None else "")
        for metric in metrics[:8]
    ]
    return LlmTrendAnalysis(
        status="Available",
        summary="Validated global peer metrics are available for analyst trend context.",
        peer_patterns=patterns,
    )


def _attach_global_peer_artifacts(
    ideas: list[TradeIdea],
    global_peer_coverage: dict[str, GlobalPeerCoverage],
    llm_research_manifest: LlmResearchAgentManifest,
    llm_trend_analysis: LlmTrendAnalysis,
) -> None:
    coverage_rows = list(global_peer_coverage.values())
    for idea in ideas:
        idea.global_peer_coverage = coverage_rows
        idea.causal_bridge_status = _causal_bridge_status_for_idea(idea)
        idea.equity_credit_lens = _equity_credit_lens_for_idea(idea)
        idea.llm_contribution = {
            "source_planning": llm_research_manifest.status,
            "document_triage": "deterministic" if coverage_rows else "not_required",
            "metric_extraction": (
                "validated_deterministic_parse"
                if any(coverage.metrics for coverage in coverage_rows)
                else "no_validated_global_peer_metrics"
            ),
            "trend_analysis": llm_trend_analysis.status,
            "final_synthesis": "guardrailed_by_evidence_sufficiency",
        }


def _causal_bridge_status_for_idea(idea: TradeIdea) -> str:
    if not idea.driver_analysis:
        return "Unavailable: driver analysis was not built."
    if idea.driver_analysis.bridge_status:
        detail = idea.driver_analysis.bridge_status
        if idea.driver_analysis.primary_driver:
            detail += f" for {idea.driver_analysis.primary_driver}."
        if idea.driver_analysis.data_gaps:
            detail += " Gaps: " + "; ".join(idea.driver_analysis.data_gaps[:3])
        elif idea.peer_metric_readthrough:
            detail += " Peer metric read-through context is available."
        else:
            detail += " Peer metric read-through is missing or incomplete."
        return detail
    if not idea.driver_analysis.factors:
        return "Incomplete: no driver factors were identified."
    low_confidence = all(factor.confidence == "Low" for factor in idea.driver_analysis.factors)
    if low_confidence:
        return "Incomplete: only low-confidence causal factors are available."
    if idea.peer_metric_readthrough:
        return "Supported by driver analysis and peer metric read-through context."
    return "Partial: driver analysis exists, but peer metric read-through is missing or incomplete."


def _equity_credit_lens_for_idea(idea: TradeIdea) -> dict[str, str]:
    event = idea.source_events[0] if idea.source_events else None
    driver = str((event.metrics or {}).get("economic_driver") or "").lower() if event else ""
    title = idea.title.lower()
    analysis = idea.driver_analysis
    factors = list(analysis.factors if analysis else [])
    factor_summary = "; ".join(
        f"{factor.cause} ({factor.magnitude_hint})"
        for factor in factors[:3]
    ) or "No validated driver factor is available yet"
    peer_summary = (
        idea.peer_metric_summary.summary
        if idea.peer_metric_summary else
        "Peer operating read-through has not produced aligned same-driver metrics yet"
    )
    market_summary = _market_capture_lens_summary(idea)
    payoff_summary = _payoff_lens_summary(idea)
    gap_summary = _lens_gap_summary(idea)
    if any(token in driver + title for token in ("cash", "debt", "liquidity", "credit")):
        liquidity_story = _liquidity_equity_story(factors, factor_summary)
        return {
            "equity": (
                "Equity lens: the app links this balance-sheet signal to FCF conversion, reinvestment capacity, "
                f"capital return, and downside optionality. {liquidity_story} {payoff_summary} {market_summary}"
            ),
            "credit": (
                "Credit lens: the app links this signal to liquidity runway, debt capacity, refinancing risk, "
                f"and interest burden. Peer/context check: {peer_summary}. Remaining diligence: {gap_summary}"
            ),
        }
    if any(token in driver + title for token in ("gross", "margin", "profit", "opex", "operating")):
        return {
            "equity": (
                "Equity lens: the app links this operating signal to margin durability, EPS/FCF conversion, "
                f"and multiple support through {factor_summary}. {payoff_summary}"
            ),
            "credit": (
                "Credit lens: the app checks whether operating improvement converts into cash flow, coverage, "
                f"and lower funding risk. Peer/context check: {peer_summary}. Remaining diligence: {gap_summary}"
            ),
        }
    if any(token in driver + title for token in ("revenue", "demand", "sales")):
        return {
            "equity": (
                "Equity lens: the app links this demand signal to growth durability, pricing/mix, operating leverage, "
                f"and valuation multiple through {factor_summary}. {payoff_summary}"
            ),
            "credit": (
                "Credit lens: the app checks whether demand improves cash conversion and liquidity rather than "
                f"consuming working capital. Peer/context check: {peer_summary}. Remaining diligence: {gap_summary}"
            ),
        }
    return {
        "equity": (
            "Equity lens: the app attempted to connect the signal to revenue, margin, FCF, capital return, "
            f"and valuation. Current bridge: {factor_summary}. {payoff_summary}"
        ),
        "credit": (
            "Credit lens: the app attempted to connect the signal to liquidity, leverage, refinancing, covenant, "
            f"and spread risk. Peer/context check: {peer_summary}. Remaining diligence: {gap_summary}"
        ),
    }


def _payoff_lens_summary(idea: TradeIdea) -> str:
    payoff = idea.payoff_model
    complete = bool(
        payoff and payoff.payoff_completeness
        and payoff.payoff_completeness.status == "Complete"
    )
    if payoff and complete:
        ev = (
            f"illustrative EV {payoff.expected_value_pct:+.1f}%"
            if payoff.expected_value_pct is not None else
            "payoff scenarios available"
        )
        rank_note = "rank eligible" if payoff.rank_eligible else "uncalibrated and excluded from EV ranking"
        limitation = f" {payoff.limitations[0]}" if payoff.limitations else ""
        return (
            f"Scenario bridge is ready using {payoff.assumption_mode} assumptions "
            f"({ev}; {rank_note}).{limitation} Assumptions remain editable in Scenario + Payoff."
        )
    if payoff:
        missing = _dedupe_list(
            list(payoff.validation_errors)
            + list(payoff.payoff_completeness.missing_inputs if payoff.payoff_completeness else [])
            + list(payoff.data_gaps)
        )
        blocker = "; ".join(missing[:3]) or "the automatic scenario basis could not produce complete net returns"
        return f"Scenario bridge is incomplete. Exact blocker: {blocker}. {_payoff_control_guidance(idea, payoff)}"
    return _payoff_control_guidance(idea, None)


def _payoff_control_guidance(idea: TradeIdea, payoff) -> str:
    if idea.direction == "Watch":
        return (
            "No payoff input is requested yet because the idea is still neutral. Use Causal Bridge > Evidence Closure "
            "to validate a Long or Short mechanism first; scenario controls activate after direction is established."
        )
    if idea.direction == "Relative Value":
        return (
            "In Idea Scorer > Scenario + Payoff, open Assumption inputs, enter the sourced entry value and hedge ratio, "
            "then choose Custom to supply Bear/Base/Bull pair exits before saving assumptions."
        )
    entry_missing = not payoff or not payoff.entry_price or payoff.entry_price <= 0
    custom_invalid = bool(payoff and payoff.validation_errors)
    if entry_missing:
        return (
            "In Idea Scorer > Scenario + Payoff, open Assumption inputs and keep Scenario basis = Model-derived. "
            "Enter Current entry price only if the sourced market price is still missing; review the prefilled exits and "
            "25/50/25 probabilities, then select Save Assumptions to recalculate immediately."
        )
    if custom_invalid:
        return (
            "In Idea Scorer > Scenario + Payoff > Assumption inputs, correct the Custom exit anchors so Bear <= Base <= Bull, "
            "review probabilities and costs, then select Save Assumptions."
        )
    return (
        "In Idea Scorer > Scenario + Payoff, open Assumption inputs. Keep Model-derived to use internal fair-value cases, "
        "or choose Custom to edit Bear/Base/Bull exits; review the prefilled 25/50/25 probabilities and costs, then select "
        "Save Assumptions to recalculate immediately."
    )


def _market_capture_lens_summary(idea: TradeIdea) -> str:
    capture = idea.market_capture
    if not capture:
        return "Market-capture context has not been evaluated."
    if capture.capture_mode == "Price-only":
        reaction = (
            f"{capture.price_reaction_pct:+.1f}%"
            if capture.price_reaction_pct is not None else "available"
        )
        return (
            f"Capture mode: Price-only. Event price reaction is {reaction}; analyst-expectation response "
            "remains unverified and is not treated as a thesis-validity failure."
        )
    if capture.capture_mode == "Consensus-confirmed":
        return f"Capture mode: Consensus-confirmed. {capture.explanation}"
    return f"Capture mode: {capture.capture_mode or 'Unclassified'}. {capture.explanation}"


def _liquidity_equity_story(factors: list, fallback: str) -> str:
    if not factors:
        return "The balance-sheet signal is not yet decomposed into operating cash flow, reinvestment, financing, and capital return."
    cash_conversion = [
        f"{factor.cause} ({factor.magnitude_hint})"
        for factor in factors
        if any(token in factor.cause.lower() for token in ("operating cash", "capex", "free cash"))
    ]
    buyback = next((factor for factor in factors if "buyback" in factor.cause.lower()), None)
    story = (
        "The cash-flow bridge "
        + (" and ".join(cash_conversion[:2]) if cash_conversion else fallback)
        + " indicate the direction of near-term FCF conversion and reinvestment capacity."
    )
    if buyback:
        lower_spend = "-" in str(buyback.magnitude_hint)
        story += (
            " Lower repurchase spending preserves liquidity but reduces immediate per-share capital-return support."
            if lower_spend else
            " Higher repurchase spending supports per-share capital return but consumes liquidity."
        )
    return story


def _lens_gap_summary(idea: TradeIdea) -> str:
    event = idea.source_events[0] if idea.source_events else None
    driver_text = f"{idea.title} {getattr(idea.driver_analysis, 'primary_driver', '')} {event.category if event else ''}".lower()
    if any(token in driver_text for token in ("cash", "debt", "liquidity", "credit")):
        return _cash_credit_lens_gap_summary(idea)
    gaps: list[str] = []
    if idea.driver_analysis:
        gaps.extend(idea.driver_analysis.data_gaps[:2])
        gaps.extend(idea.driver_analysis.evidence_needed[:2])
    if idea.market_capture and idea.market_capture.data_gaps:
        gaps.extend(idea.market_capture.data_gaps[:1])
    if idea.peer_metric_summary and idea.peer_metric_summary.data_gaps:
        gaps.extend(idea.peer_metric_summary.data_gaps[:1])
    if idea.next_source_to_check:
        gaps.append(idea.next_source_to_check)
    return "; ".join(_dedupe_list(gaps)[:4]) or "No major bridge gap was identified, but primary evidence remains authoritative."


def _cash_credit_lens_gap_summary(idea: TradeIdea) -> str:
    factors = list(idea.driver_analysis.factors if idea.driver_analysis else [])
    factor_text = " ".join(f"{factor.cause} {factor.magnitude_hint}" for factor in factors).lower()
    covered: list[str] = []
    remaining: list[str] = []
    if "operating cash flow" in factor_text:
        covered.append("operating cash flow")
    if "capex" in factor_text or "capital expenditure" in factor_text:
        covered.append("capex")
    if "dividend" in factor_text:
        covered.append("dividends")
    if "debt" in factor_text:
        covered.append("debt movement")
    if "cash" in factor_text:
        covered.append("cash balance")
    if covered:
        remaining.append(f"Covered by extracted facts: {', '.join(_dedupe_list(covered))}.")
    else:
        remaining.append("Still needs extracted cash-flow statement facts: OCF, capex, dividends, buybacks, and financing flows.")

    if not any(token in factor_text for token in ("repurchase", "buyback")):
        remaining.append("Buybacks/repurchases and financing cash flows still need issuer table or cash-flow statement confirmation.")
    if not any(token in factor_text for token in ("maturity", "restricted cash", "covenant", "rating", "spread")):
        remaining.append("Debt maturity, restricted cash, covenant, rating, or credit-spread evidence remains unresolved.")
    if idea.market_capture and idea.market_capture.data_gaps:
        remaining.append(idea.market_capture.data_gaps[0])
    if idea.peer_metric_summary and idea.peer_metric_summary.data_gaps:
        remaining.append(idea.peer_metric_summary.data_gaps[0])
    if idea.next_source_to_check:
        remaining.append(idea.next_source_to_check)
    return "; ".join(_dedupe_list(remaining)[:4])


def _fiscal_alignment(source_event: ChangeEvent, peer_event: ChangeEvent) -> str:
    source_period = next((citation.period_end for citation in source_event.citations if citation.period_end), None)
    peer_period = next((citation.period_end for citation in peer_event.citations if citation.period_end), None)
    if not source_period or not peer_period:
        return "Unknown"
    return "Aligned" if source_period[:7] == peer_period[:7] else "Different fiscal period"


def _matching_peer_events(
    source_event: ChangeEvent,
    peer_events: list[ChangeEvent],
) -> list[ChangeEvent]:
    source_metric = source_event.metrics.get("metric_name")
    matches: list[ChangeEvent] = []
    for event in peer_events:
        if not _period_is_usable_for_peer(source_event, event):
            continue
        if source_event.category == "financial_kpi" and source_metric:
            if event.metrics.get("metric_name") == source_metric:
                matches.append(event)
        elif source_event.category == "margin":
            if event.category == "margin":
                matches.append(event)
        elif event.category == source_event.category:
            matches.append(event)
    return sorted(matches, key=lambda event: event.severity, reverse=True)


def _stale_matching_peer_events(
    source_event: ChangeEvent,
    peer_events: list[ChangeEvent],
) -> list[ChangeEvent]:
    source_metric = source_event.metrics.get("metric_name")
    stale: list[ChangeEvent] = []
    for event in peer_events:
        if source_event.category == "financial_kpi" and source_metric:
            related = event.metrics.get("metric_name") == source_metric
        elif source_event.category == "margin":
            related = event.category == "margin"
        else:
            related = event.category == source_event.category
        if related and not _period_is_usable_for_peer(source_event, event):
            stale.append(event)
    return stale


def _period_is_usable_for_peer(source_event: ChangeEvent, peer_event: ChangeEvent) -> bool:
    source_period = next((citation.period_end for citation in source_event.citations if citation.period_end), None)
    peer_period = next((citation.period_end for citation in peer_event.citations if citation.period_end), None)
    if not source_period or not peer_period:
        return True
    try:
        source_year = int(source_period[:4])
        peer_year = int(peer_period[:4])
    except (TypeError, ValueError):
        return True
    return abs(source_year - peer_year) <= 1


def _metric_readthrough_for_event(
    source_event: ChangeEvent,
    snapshot: _PeerSnapshot,
    price_pct: float | None,
) -> PeerReadthrough | None:
    source_metric = source_event.metrics.get("metric_name")
    if not source_metric:
        return None
    metric = next((item for item in snapshot.metrics if item.name == source_metric), None)
    if not metric or metric.yoy_change_pct is None:
        return None
    period_status = _metric_period_status(source_event, metric)
    if period_status == "stale_period":
        return PeerReadthrough(
            peer_ticker=snapshot.ticker,
            evidence_status="Stale peer metric",
            relation="No direct evidence",
            key_metric_changes=[
                f"{source_metric}: peer period {metric.period_end or 'unknown'} is not aligned with the source event."
            ],
            price_reaction_pct=price_pct,
            conclusion=f"{snapshot.ticker} has {source_metric} data, but the period is too stale for this read-through.",
            failure_status="stale_period",
            failure_reason="Peer metric period is not aligned to the focal event period.",
            metric_readthrough=_peer_metric_readthrough_for_event(source_event, snapshot),
            global_peer_coverage=snapshot.global_coverage,
        )

    relation = _direction_relation(source_event.direction, _direction_from_change(metric.yoy_change_pct))
    key_change = (
        f"{source_metric}: {format_number(metric.value)} {metric.unit}, "
        f"{metric.yoy_change_pct:+.1f}% versus comparable period."
    )
    return PeerReadthrough(
        peer_ticker=snapshot.ticker,
        evidence_status="Direct KPI data found",
        relation=relation,
        key_metric_changes=[key_change],
        price_reaction_pct=price_pct,
        conclusion=_peer_conclusion(snapshot.ticker, source_event, relation, [key_change]),
        metric_readthrough=_peer_metric_readthrough_for_event(source_event, snapshot),
        global_peer_coverage=snapshot.global_coverage,
    )


def _peer_metric_readthrough_for_event(
    source_event: ChangeEvent,
    snapshot: _PeerSnapshot,
) -> PeerMetricReadthrough | None:
    family = _metric_family_for_event(source_event)
    if not family:
        return None
    related_metrics = _metrics_for_family(family)
    stale_metrics = _dedupe_list([
        metric.name for metric in snapshot.metrics
        if metric.name in related_metrics and _metric_period_status(source_event, metric) == "stale_period"
    ])
    observations = [
        _metric_to_global_observation(snapshot.ticker, family, metric)
        for metric in snapshot.metrics
        if metric.name in related_metrics and _metric_period_status(source_event, metric) != "stale_period"
    ]
    observations = [item for item in observations if item is not None]
    present_metrics = _dedupe_list([item.metric for item in observations])
    coverage_notes = _peer_metric_coverage_notes(family, present_metrics)
    covered_missing = set(_covered_peer_metrics(family, present_metrics))
    required_missing = _blocking_missing_metrics(family, related_metrics, present_metrics)
    missing_metrics = [
        metric for metric in related_metrics
        if metric not in present_metrics and metric not in covered_missing
    ]
    if not observations:
        gap_details = _peer_metric_gap_details(source_event, snapshot, family, related_metrics)
        return PeerMetricReadthrough(
            peer_ticker=snapshot.ticker,
            metric_family=family,
            status="missing_metric_family",
            relation="No direct evidence",
            summary=f"No aligned peer metric observations were available for the {family} family.",
            data_gaps=gap_details,
            required_metrics=related_metrics,
            present_metrics=[],
            missing_metrics=related_metrics,
            acceptance_criteria=_peer_metric_acceptance_criteria(family),
            falsification_tests=_peer_metric_falsification_tests(family),
        )
    primary = next((item for item in observations if item.metric == _primary_metric_for_family(family)), observations[0])
    relation = (
        _direction_relation(source_event.direction, _direction_from_change(primary.yoy_change_pct))
        if primary.yoy_change_pct is not None else "Mixed / unclear"
    )
    summary = (
        f"{snapshot.ticker} {family} read-through uses "
        + "; ".join(
            f"{item.metric} {format_number(item.value)} {item.unit}"
            + (f" ({item.yoy_change_pct:+.1f}%)" if item.yoy_change_pct is not None else "")
            for item in observations[:4]
        )
    )
    return PeerMetricReadthrough(
        peer_ticker=snapshot.ticker,
        metric_family=family,
        status="available",
        relation=relation,
        summary=summary,
        fiscal_alignment="Aligned or recent",
        observations=observations,
        data_gaps=_peer_metric_partial_gaps(family, missing_metrics, required_missing, stale_metrics, coverage_notes),
        source_tier=min((item.citation.source_tier for item in observations if item.citation and item.citation.source_tier), default=None),
        required_metrics=related_metrics,
        present_metrics=present_metrics,
        missing_metrics=missing_metrics,
        acceptance_criteria=_peer_metric_acceptance_criteria(family),
        falsification_tests=_peer_metric_falsification_tests(family),
    )


def _blocking_missing_metrics(family: str, related_metrics: list[str], present_metrics: list[str]) -> list[str]:
    present = set(present_metrics)
    if family == "revenue_demand":
        return ["Revenue"] if "Revenue" not in present else []
    if family == "gross_margin_mix":
        blockers: list[str] = []
        if "Revenue" not in present:
            blockers.append("Revenue")
        if not ({"Gross Profit", "Gross Margin"} & present):
            blockers.append("Gross Profit or Gross Margin")
        return blockers
    if family == "cash_credit":
        blockers = []
        if not ({"Cash", "Operating Cash Flow"} & present):
            blockers.append("Cash or Operating Cash Flow")
        if not ({"Long-term Debt", "Current Debt", "Interest Expense"} & present):
            blockers.append("Debt or Interest Expense")
        return blockers
    if family == "operating_expense":
        blockers = []
        if "Revenue" not in present:
            blockers.append("Revenue")
        if not ({"Operating Income", "SG&A Expense", "R&D Expense", "Sales and Marketing Expense"} & present):
            blockers.append("Operating income or opex line")
        return blockers
    if family == "financial_kpi":
        blockers = []
        if "Revenue" not in present:
            blockers.append("Revenue")
        if not ({"Gross Profit", "Operating Income", "Net Income"} & present):
            blockers.append("Profit metric")
        return blockers
    return [metric for metric in related_metrics if metric not in present]


def _peer_metric_partial_gaps(
    family: str,
    missing_metrics: list[str],
    required_missing: list[str],
    stale_metrics: list[str] | None = None,
    coverage_notes: list[str] | None = None,
) -> list[str]:
    stale = _dedupe_list(stale_metrics or [])
    unavailable = [metric for metric in missing_metrics if metric not in set(stale)]
    if not missing_metrics:
        return list(coverage_notes or [])
    rows: list[str] = list(coverage_notes or [])
    stale_required = [metric for metric in required_missing if metric in set(stale)]
    if not required_missing:
        if stale:
            rows.append(
                f"Optional peer metrics found only in stale or period-misaligned observations and excluded: {', '.join(stale)}."
            )
        if unavailable:
            if family == "operating_expense":
                rows.append(
                    f"Optional opex sub-lines not separately disclosed in aligned structured facts: {', '.join(unavailable)}. "
                    "Use issuer income-statement table, MD&A, earnings deck, or manual KPI import if the thesis depends on that sub-line."
                )
            else:
                rows.append(
                    f"Optional metrics not found for fuller {family} read-through: {', '.join(unavailable)}."
                )
        rows.append("Core same-driver peer evidence is still usable.")
        return rows
    if stale_required:
        rows.append(
            f"Blocking peer metrics for {family} were found only in stale or period-misaligned observations: {', '.join(stale_required)}."
        )
    missing_required = [metric for metric in required_missing if metric not in set(stale)]
    if missing_required:
        rows.append(f"Missing blocking peer metrics for {family}: {', '.join(missing_required)}.")
    stale_optional = [metric for metric in stale if metric not in set(required_missing)]
    if stale_optional:
        rows.append(
            f"Optional peer metrics found only in stale or period-misaligned observations and excluded: {', '.join(stale_optional)}."
        )
    unavailable_optional = [metric for metric in unavailable if metric not in set(required_missing)]
    if unavailable_optional:
        rows.append(f"Additional metrics not found for fuller read-through: {', '.join(unavailable_optional)}.")
    return rows


def _covered_peer_metrics(family: str, present_metrics: list[str]) -> list[str]:
    present = set(present_metrics)
    covered: list[str] = []
    if family == "operating_expense" and "SG&A Expense" in present:
        covered.append("Sales and Marketing Expense")
    if family == "operating_expense" and "Operating Income" in present:
        covered.append("R&D Expense")
        covered.append("Sales and Marketing Expense")
    if family == "cash_credit" and "Long-term Debt" in present:
        covered.append("Current Debt")
    if family == "cash_credit" and ({"Cash", "Operating Cash Flow"} & present):
        covered.extend(["Dividends Paid", "Share Repurchases"])
    return covered


def _peer_metric_coverage_notes(family: str, present_metrics: list[str]) -> list[str]:
    present = set(present_metrics)
    notes: list[str] = []
    if family == "operating_expense" and "SG&A Expense" in present and "Sales and Marketing Expense" not in present:
        notes.append(
            "Sales and marketing is covered by broader SG&A when the issuer does not disclose a separate sales/marketing line; treat it as opex evidence, not a standalone sales-efficiency KPI."
        )
    if family == "operating_expense" and "Operating Income" in present and "R&D Expense" not in present:
        notes.append(
            "R&D is not separately disclosed in aligned structured facts; operating income and revenue still cover aggregate operating-expense read-through. Use issuer segment tables or manual KPI import when the thesis depends on R&D intensity."
        )
    if family == "cash_credit" and "Long-term Debt" in present and "Current Debt" not in present:
        notes.append(
            "Current debt is not separately disclosed in aligned structured facts; long-term debt and liquidity metrics remain usable, but maturity-table evidence is still preferred."
        )
    if family == "cash_credit" and ({"Cash", "Operating Cash Flow"} & present):
        optional = [metric for metric in ("Dividends Paid", "Share Repurchases") if metric not in present]
        if optional:
            notes.append(
                "Capital-return sub-lines are not separately disclosed in aligned structured facts: "
                f"{', '.join(optional)}. Core cash/credit peer read-through remains usable; buyback or dilution theses still require direct capital-return evidence."
            )
    return notes


def _peer_metric_gap_details(
    source_event: ChangeEvent,
    snapshot: _PeerSnapshot,
    family: str,
    related_metrics: list[str],
) -> list[str]:
    if not snapshot.metrics:
        if snapshot.sec_error:
            return [
                f"SEC/companyfacts fetch failed or returned no usable metrics for {snapshot.ticker}: {snapshot.sec_error}",
                f"Need aligned peer metrics: {', '.join(related_metrics)}.",
            ]
        if snapshot.global_coverage and snapshot.global_coverage.data_gaps:
            return [
                f"Global peer fallback did not validate usable metrics: {'; '.join(snapshot.global_coverage.data_gaps[:2])}",
                f"Need aligned peer metrics: {', '.join(related_metrics)}.",
            ]
        return [
            f"No structured peer financial metrics were available for {snapshot.ticker}.",
            f"Need aligned peer metrics: {', '.join(related_metrics)}.",
        ]
    stale = [
        metric.name for metric in snapshot.metrics
        if metric.name in related_metrics and _metric_period_status(source_event, metric) == "stale_period"
    ]
    available_names = _dedupe_list([metric.name for metric in snapshot.metrics])
    if stale:
        return [
            f"Related metrics exist but are stale or fiscally unaligned: {', '.join(_dedupe_list(stale))}.",
            f"Available peer metrics include: {', '.join(available_names[:8])}.",
            f"Need aligned peer metrics: {', '.join(related_metrics)}.",
        ]
    return [
        f"Peer metrics were fetched, but none matched the {family} family.",
        f"Available peer metrics include: {', '.join(available_names[:8])}.",
        f"Need aligned peer metrics: {', '.join(related_metrics)}.",
    ]


def _peer_metric_acceptance_criteria(family: str) -> list[str]:
    if family == "gross_margin_mix":
        return [
            "Revenue and gross-profit or gross-margin observations are aligned to the focal event period.",
            "COGS, ASP, deliveries, mix, incentives, warranty, or segment margin evidence explains the peer margin move when available.",
            "Peer relation is based on operating metrics, not only stock-price sympathy.",
        ]
    if family == "revenue_demand":
        return [
            "Peer revenue, volume, delivery, or demand metric is aligned to the focal event period.",
            "Price and volume effects are separated when source data allows.",
            "Peer relation is not inferred from market or sector price movement alone.",
        ]
    if family == "operating_expense":
        return [
            "Peer opex and revenue are from the same fiscal period.",
            "Expense growth is compared with revenue growth before calling deleverage.",
            "One-time restructuring or stock-compensation items are checked when disclosed.",
        ]
    if family == "cash_credit":
        return [
            "Peer cash, debt, interest, operating cash flow, and capex are period-aligned where available.",
            "Liquidity read-through distinguishes operating cash generation from financing or working-capital timing.",
            "Credit interpretation is supported by maturity, covenant, rating, or spread evidence when available.",
        ]
    if family == "share_count":
        return [
            "Peer share-count basis is normalized for weighted-average versus period-end shares.",
            "ADR ratio, split, issuance, and buyback disclosures are checked before inferring dilution.",
            "Per-share impact is separated from company-level operating performance.",
        ]
    return [
        "Peer metrics are aligned to the focal event period.",
        "Peer evidence uses the same metric family as the focal thesis.",
        "Peer stock movement is treated separately from operating metric read-through.",
    ]


def _peer_metric_falsification_tests(family: str) -> list[str]:
    if family == "gross_margin_mix":
        return [
            "Read-through weakens if peer revenue grew but gross margin did not improve.",
            "Read-through weakens if peer margin movement is caused by one-time mix, warranty, or accounting effects.",
            "Read-through weakens if the peer period is stale or not comparable to the focal event period.",
        ]
    if family == "revenue_demand":
        return [
            "Read-through weakens if peer revenue movement is price-only, FX-only, or acquisition-driven.",
            "Read-through weakens if official industry demand data contradicts peer revenue signals.",
        ]
    if family == "operating_expense":
        return [
            "Read-through weakens if expense growth is matched by revenue growth or disclosed as one-time.",
            "Read-through weakens if peer opex categories are not comparable with the focal company.",
        ]
    if family == "cash_credit":
        return [
            "Read-through weakens if peer cash movement is restricted, seasonal, or offset by near-term debt.",
            "Read-through weakens if debt maturity, interest burden, or credit-spread evidence contradicts the liquidity signal.",
        ]
    if family == "share_count":
        return [
            "Read-through weakens if share-count movement disappears after split, ADR, or weighted-average normalization.",
            "Read-through weakens if buybacks or issuance disclosures contradict the inferred dilution/capital-return signal.",
        ]
    return [
        "Read-through weakens if peer metrics are stale, missing, or not from the same driver family.",
        "Read-through weakens if peer own-event evidence points to unrelated causes.",
    ]


def _dedupe_list(values: list[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            rows.append(text)
            seen.add(text)
    return rows


def _hydrate_saved_idea_assumptions(ideas: list[TradeIdea], store: ResearchStore) -> None:
    pending = [idea for idea in ideas if not getattr(idea, "user_assumptions", None)]
    try:
        assumptions_by_idea = store.latest_idea_assumptions_many(
            [idea.idea_id for idea in pending]
        )
    except Exception:
        assumptions_by_idea = {}
    for idea in pending:
        assumptions = assumptions_by_idea.get(idea.idea_id, {})
        if assumptions:
            idea.user_assumptions = assumptions


def _metric_family_for_event(source_event: ChangeEvent) -> str:
    text = f"{source_event.category} {source_event.title} {source_event.metrics.get('metric_name', '')} {source_event.metrics.get('economic_driver', '')}".lower()
    if any(token in text for token in ("gross", "margin", "mix")):
        return "gross_margin_mix"
    if any(token in text for token in ("revenue", "sales", "demand")):
        return "revenue_demand"
    if any(token in text for token in ("expense", "opex", "sga", "r&d", "marketing", "operating leverage", "operating income")):
        return "operating_expense"
    if any(token in text for token in ("cash", "debt", "liquidity", "free cash")):
        return "cash_credit"
    if any(token in text for token in ("share", "dilution", "buyback")):
        return "share_count"
    return "financial_kpi"


def _metrics_for_family(family: str) -> list[str]:
    return {
        "gross_margin_mix": ["Revenue", "Gross Profit", "Gross Margin", "Cost of Revenue", "Deliveries"],
        "revenue_demand": ["Revenue", "Deliveries"],
        "operating_expense": ["Revenue", "SG&A Expense", "R&D Expense", "Sales and Marketing Expense", "Operating Income"],
        "cash_credit": ["Cash", "Operating Cash Flow", "Capital Expenditure", "Dividends Paid", "Share Repurchases", "Long-term Debt", "Current Debt", "Interest Expense"],
        "share_count": ["Shares", "Dividends Paid"],
    }.get(family, ["Revenue", "Gross Profit", "Operating Income", "Net Income"])


def _primary_metric_for_family(family: str) -> str:
    return {
        "gross_margin_mix": "Gross Margin",
        "revenue_demand": "Revenue",
        "operating_expense": "Operating Income",
        "cash_credit": "Operating Cash Flow",
        "share_count": "Shares",
    }.get(family, "Revenue")


def _metric_period_status(source_event: ChangeEvent, metric: FinancialMetric) -> str:
    source_period = next((citation.period_end for citation in source_event.citations if citation.period_end), None)
    if not source_period or not metric.period_end:
        return "unknown"
    try:
        source_year = int(source_period[:4])
        metric_year = int(metric.period_end[:4])
    except (TypeError, ValueError):
        return "unknown"
    return "recent" if abs(source_year - metric_year) <= 1 else "stale_period"


def _metric_to_global_observation(
    peer_ticker: str,
    family: str,
    metric: FinancialMetric,
) -> GlobalPeerMetricObservation:
    source_url = metric.source_url or ""
    citation = Citation(
        source=metric.source_kind or metric.form or "Peer financial metric",
        url=source_url,
        filed=metric.filed,
        form=metric.form,
        section=metric.name,
        snippet=f"{metric.name}: {format_number(metric.value)} {metric.unit}",
        period_end=metric.period_end,
        source_tier=_peer_metric_source_tier(metric),
    )
    return GlobalPeerMetricObservation(
        observation_id=f"{peer_ticker}:{family}:{metric.name}:{metric.period_end}",
        peer_ticker=peer_ticker,
        metric=metric.name,
        value=metric.value,
        unit=metric.unit,
        currency="" if metric.unit == "percent" else metric.unit,
        period_end=metric.period_end,
        fiscal_period=metric.fiscal_period,
        source_document_id=metric.accession or source_url,
        source_url=source_url,
        source_type=metric.source_kind or "companyfacts",
        observed_at=_utc_now(),
        previous_value=metric.previous_value,
        yoy_change_pct=metric.yoy_change_pct,
        citation=citation,
    )


def _peer_metric_source_tier(metric: FinancialMetric) -> int:
    source_kind = (metric.source_kind or "companyfacts").lower()
    if source_kind in {
        "companyfacts", "derived_companyfacts", "periodic_inline_xbrl",
        "registration_inline_xbrl", "hkex_document", "cninfo_document",
        "issuer_ir_report", "global_peer_official_document",
    }:
        return 1
    if "fmp" in source_kind or "tiingo" in source_kind or "licensed" in source_kind:
        return 3
    return 4


def _peer_relation(source_event: ChangeEvent, matching_events: list[ChangeEvent]) -> str:
    peer_directions = {_direction_from_event(event) for event in matching_events}
    source_direction = _direction_from_event(source_event)
    if source_direction in peer_directions:
        return "Confirming read-through"
    if _opposite_direction(source_direction) in peer_directions:
        return "Contradicting read-through"
    return "Mixed / unclear"


def _peer_conclusion(
    peer_ticker: str,
    source_event: ChangeEvent,
    relation: str,
    key_changes: list[str],
) -> str:
    category = source_event.category.replace("_", " ")
    if relation == "Confirming read-through":
        return f"{peer_ticker} shows direct {category} evidence in the same direction as the source idea."
    if relation == "Contradicting read-through":
        return f"{peer_ticker} shows direct {category} evidence in the opposite direction, weakening a broad sector read-through."
    if relation == "Mixed / unclear":
        return f"{peer_ticker} has related evidence, but the read-through is mixed: {'; '.join(key_changes[:2])}"
    return f"{peer_ticker} does not provide direct evidence for this read-through."


def _direction_relation(source_direction: str, peer_direction: str) -> str:
    if source_direction == peer_direction:
        return "Confirming read-through"
    if _opposite_direction(source_direction) == peer_direction:
        return "Contradicting read-through"
    return "Mixed / unclear"


def _direction_from_event(event: ChangeEvent) -> str:
    if event.direction in {"positive", "negative"}:
        return event.direction
    change = event.metrics.get("yoy_change_pct")
    if isinstance(change, (int, float)):
        return _direction_from_change(float(change))
    return "mixed"


def _direction_from_change(change_pct: float) -> str:
    if change_pct > 0:
        return "positive"
    if change_pct < 0:
        return "negative"
    return "mixed"


def _opposite_direction(direction: str) -> str:
    if direction == "positive":
        return "negative"
    if direction == "negative":
        return "positive"
    return "mixed"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _analysis_text_for_form(text: str, form: str) -> str:
    """Keep comparisons responsive on very large annual reports."""
    if len(text) <= 900_000:
        return text

    form = form.upper()
    needles = (
        ["item 3.d", "risk factors", "item 5.", "operating and financial review"]
        if form in {"20-F", "40-F"}
        else ["item 1a", "risk factors", "item 7.", "management's discussion"]
    )
    lower = text.lower()
    windows: list[str] = []
    for needle in needles:
        idx = lower.find(needle)
        if idx == -1:
            continue
        start = max(0, idx - 10_000)
        end = min(len(text), idx + 320_000)
        windows.append(text[start:end])

    if windows:
        return "\n\n".join(windows)
    return text[:900_000]


def _coverage_notes(filings: list[FilingRecord]) -> list[str]:
    forms = {filing.form for filing in filings}
    notes: list[str] = []
    if forms & FOREIGN_PERIODIC_FORMS or forms & FOREIGN_EVENT_FORMS:
        notes.append(
            "Foreign private issuer / ADR coverage: using 20-F/40-F annual reports "
            "and 6-K furnished reports instead of 10-K/10-Q/8-K."
        )
    if forms & FOREIGN_EVENT_FORMS:
        notes.append(
            "6-K reports are not standardized like 10-Q/8-K filings; extraction quality "
            "depends on issuer-provided exhibits and report formatting."
        )
    if not filings:
        notes.append(
            "No supported SEC forms were found. Add issuer-specific sources or broaden the form set."
        )
    return notes
