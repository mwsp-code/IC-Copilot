from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .domain.research_integrity import (
    AuditFinding,
    AuditResolution,
    CandidateDiscoveryItem,
    CandidateStatus,
    CandidateType,
    ClosureAuditResult,
    ClosureState,
    EvidenceAdmissionDecision,
    EvidenceRequest,
    PrimaryAuditResponse,
    PrimaryResponseState,
    ResearchRequest,
)
from .domain.publication_readiness import (
    CanonicalPublicationRevision,
    DerivedArtifactDependency,
    PublicationGateEvaluation,
    ResearchRepairAction,
    SemanticQualityEvaluation,
    ScopeInvalidationResult,
)


@dataclass(frozen=True)
class CompanyIdentity:
    ticker: str
    cik: str
    name: str
    exchange: str = "US"
    sic: str | None = None
    sic_description: str | None = None


@dataclass
class EntityResolution:
    ticker: str
    name: str
    cik: str
    exchange: str
    sic: str | None
    sic_description: str | None
    listing_status: str
    reporting_forms: list[str] = field(default_factory=list)
    adr_ratio: float = 1.0
    similar_tickers: list[str] = field(default_factory=list)
    warning: str | None = None


@dataclass(frozen=True)
class FilingRecord:
    form: str
    accession: str
    filing_date: str
    report_date: str
    primary_doc: str
    description: str
    url: str
    accepted_at: str | None = None


@dataclass(frozen=True)
class Citation:
    source: str
    url: str
    filed: str | None = None
    form: str | None = None
    section: str | None = None
    snippet: str | None = None
    accession: str | None = None
    period_end: str | None = None
    retrieved_at: str | None = None
    source_tier: int | None = None


@dataclass(frozen=True)
class SectionText:
    name: str
    text: str
    citation: Citation


@dataclass(frozen=True)
class PriorContextAudit:
    status: str = "not_attempted"
    search_attempted: bool = False
    candidates_considered: int = 0
    sources_attempted: list[str] = field(default_factory=list)
    selected_accession: str | None = None
    text_loaded: bool = False
    text_parsed: bool = False
    section_matched: bool = False
    zero_mentions_is_valid: bool = False
    blocker: str = ""
    discovery_error: str = ""
    fallback_source_types: list[str] = field(default_factory=list)
    contextual_comparison_eligible: bool = False
    llm_comparison_ready: bool = False
    llm_rules: list[str] = field(default_factory=list)
    stage_history: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DisclosureComparison:
    comparison_status: str
    reason_code: str
    comparison_type: str
    confidence: str
    current_form: str
    current_accession: str
    current_filing_date: str
    current_period: str
    current_section: str
    current_section_key: str
    prior_form: str | None = None
    prior_accession: str | None = None
    prior_filing_date: str | None = None
    prior_period: str | None = None
    prior_section: str | None = None
    prior_section_key: str | None = None
    current_mentions: int = 0
    prior_mentions: int | None = None
    current_word_count: int = 0
    prior_word_count: int | None = None
    current_mentions_per_1000_words: float = 0.0
    prior_mentions_per_1000_words: float | None = None
    mention_rate_delta: float | None = None
    section_length_change_pct: float | None = None
    semantic_similarity: float | None = None
    topic_drift_score: float | None = None
    added_sentence_count: int = 0
    removed_sentence_count: int = 0
    materiality_score: float = 0.0
    investment_relevance: str = "Low"
    interpretation: str = ""
    research_work_order: str = ""
    notes: list[str] = field(default_factory=list)
    prior_context_audit: PriorContextAudit = field(default_factory=PriorContextAudit)
    alignment_type: str = "unavailable"
    current_url: str = ""
    prior_url: str = ""
    current_excerpt: str = ""
    prior_excerpt: str = ""
    added_sentences: list[str] = field(default_factory=list)
    removed_sentences: list[str] = field(default_factory=list)
    changed_phrases: list[str] = field(default_factory=list)
    affected_driver: str = "Unmapped"
    semantic_direction: str = "neutral"
    required_confirmation: list[str] = field(default_factory=list)
    thesis_grade_status: str = "Watch Item"


@dataclass
class ContextualDisclosureComparison:
    comparison_id: str
    ticker: str
    event_title: str
    topic: str
    status: str
    comparison_type: str
    current_source: str
    current_period: str | None
    current_excerpt: str
    current_citation: Citation | None
    prior_source: str = ""
    prior_period: str | None = None
    prior_excerpt: str = ""
    prior_citation: Citation | None = None
    semantic_shift: str = ""
    affected_driver: str = "Unmapped"
    direction: str = "neutral"
    confidence: str = "Low"
    required_confirmation: list[str] = field(default_factory=list)
    citations_used: list[str] = field(default_factory=list)
    provider: str = "deterministic"
    llm_status: str = "not_run"
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class FinancialMetric:
    name: str
    value: float
    unit: str
    period_end: str
    fiscal_period: str | None = None
    fiscal_year: int | None = None
    form: str | None = None
    filed: str | None = None
    previous_value: float | None = None
    yoy_change_pct: float | None = None
    source_url: str | None = None
    accession: str | None = None
    source_kind: str = "companyfacts"
    previous_period_end: str | None = None
    trailing_twelve_month_value: float | None = None
    trailing_period_start: str | None = None
    trailing_period_end: str | None = None
    trailing_method: str = ""
    trailing_confidence: str = "Unknown"
    period_start: str | None = None
    duration_days: int | None = None
    fiscal_frame: str | None = None


@dataclass
class FinancialCoverage:
    status: str
    reason: str
    source: str
    periodic_forms: list[str] = field(default_factory=list)
    registration_forms: list[str] = field(default_factory=list)
    concepts_found: list[str] = field(default_factory=list)
    metrics_count: int = 0
    attempted_registration_accessions: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class CoverageExpansionAction:
    area: str
    priority: str
    source_type: str
    action: str
    why_it_matters: str
    integrity_rule: str
    expected_output: str = ""
    cost_latency: str = "Free / variable latency"


@dataclass
class CoverageExpansionDiagnostics:
    ticker: str
    status: str
    coverage_profile: str
    summary: str
    why_no_convincing_thesis: list[str] = field(default_factory=list)
    research_ready_blockers: list[str] = field(default_factory=list)
    high_conviction_blockers: list[str] = field(default_factory=list)
    recommended_expansions: list[CoverageExpansionAction] = field(default_factory=list)
    latency_policy: list[str] = field(default_factory=list)
    integrity_notes: list[str] = field(default_factory=list)


@dataclass
class CoverageCase:
    ticker: str
    company_name: str
    geography: str
    region: str
    jurisdiction: str
    exchange: str
    security_type: str
    sector: str
    industry: str
    filing_regime: str
    reporting_standard: str
    currency: str
    fiscal_year_end: str = ""
    home_ticker: str = ""
    home_exchange: str = ""
    primary_sources: list[str] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)
    representative_dimensions: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    profile_source: str = "built_in"


@dataclass
class GlobalCoverageUniverse:
    status: str
    generated_at: str
    cases: list[CoverageCase] = field(default_factory=list)
    representative_geographies: list[str] = field(default_factory=list)
    representative_security_types: list[str] = field(default_factory=list)
    representative_sectors: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class SourceCoverageEntry:
    source_type: str
    label: str
    jurisdiction: str
    source_family: str
    priority: str
    official: bool
    source_tier: int
    access_mode: str
    licensing_policy: str
    url: str = ""
    supports_xbrl: bool = False
    supports_ixbrl: bool = False
    supports_pdf: bool = False
    supports_html: bool = False
    status: str = "source not attempted"
    blocker: str = ""
    notes: str = ""


@dataclass
class SourceCoverageMatrix:
    ticker: str
    status: str
    summary: str
    entries: list[SourceCoverageEntry] = field(default_factory=list)
    source_families: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class CanonicalMetricDefinition:
    metric: str
    family: str
    unit_type: str
    aliases: list[str] = field(default_factory=list)
    formula: str = ""
    required_for_drivers: list[str] = field(default_factory=list)
    sector_templates: list[str] = field(default_factory=list)
    preferred_sources: list[str] = field(default_factory=list)


@dataclass
class CanonicalMetricOntology:
    status: str
    definitions: list[CanonicalMetricDefinition] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class MetricResolutionItem:
    metric: str
    status: str
    resolution_method: str
    value: float | None = None
    unit: str = ""
    currency: str = ""
    period_end: str | None = None
    source_metric: str = ""
    formula: str = ""
    source_type: str = ""
    citation: Citation | None = None
    blocker: str = ""


@dataclass
class MetricResolutionAudit:
    ticker: str
    status: str
    summary: str
    items: list[MetricResolutionItem] = field(default_factory=list)
    missing_core_metrics: list[str] = field(default_factory=list)
    derived_metrics: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class SecurityTypeProfile:
    security_type: str
    description: str
    required_identity_checks: list[str] = field(default_factory=list)
    normalization_rules: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class BudgetPolicy:
    mode: str
    cost_target: str
    description: str
    max_monthly_budget_usd: float | None = None
    allow_llm: bool = False
    allow_paid_data: bool = False
    primary_llm_profile: str = ""
    secondary_llm_profile: str = ""
    secondary_llm_enabled: bool = False
    provider_policy: dict[str, list[str]] = field(default_factory=dict)
    config_source: str = "builtin"
    enabled_sources: list[str] = field(default_factory=list)
    disabled_sources: list[str] = field(default_factory=list)
    optional_upgrade_slots: list[str] = field(default_factory=list)
    llm_policy: str = ""
    data_policy: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class ManualDataSourceStatus:
    source_type: str
    path: str
    status: str
    rows_loaded: int = 0
    message: str = ""
    required_columns: list[str] = field(default_factory=list)


@dataclass
class BringYourOwnDataStatus:
    status: str
    base_dir: str
    sources: list[ManualDataSourceStatus] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class CompanyDriver:
    name: str
    category: str
    materiality: str
    current_evidence: str
    latest_value: str | None = None
    trend: str = "Unknown"
    why_it_matters: str = ""
    source: str = ""


@dataclass
class DriverCoverageCheck:
    driver_name: str
    materiality: str
    status: str
    current_evidence: str
    required_evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    next_source: str = ""
    falsification_test: str = ""
    stage_impact: str = ""


@dataclass
class PlaybookQualityCheck:
    area: str
    status: str
    score: int
    summary: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    next_action: str = ""
    stage_impact: str = ""


@dataclass
class IndustryPlaybook:
    industry_label: str
    sector_template: str
    key_kpis: list[str] = field(default_factory=list)
    leading_indicators: list[str] = field(default_factory=list)
    valuation_methods: list[str] = field(default_factory=list)
    macro_sensitivities: list[str] = field(default_factory=list)
    normal_catalysts: list[str] = field(default_factory=list)
    peer_tickers: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    playbook_source: str = "built_in"


@dataclass
class CompanyEconomics:
    ticker: str
    status: str
    business_model: str
    industry_playbook: IndustryPlaybook
    drivers: list[CompanyDriver] = field(default_factory=list)
    driver_coverage: list[DriverCoverageCheck] = field(default_factory=list)
    playbook_quality: list[PlaybookQualityCheck] = field(default_factory=list)
    playbook_quality_score: int = 0
    material_driver_count: int = 0
    data_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlaybookAssignment:
    playbook_id: str
    label: str
    role: str
    status: str
    rationale: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class CompanyPlaybookPortfolio:
    ticker: str
    primary: PlaybookAssignment
    secondary: list[PlaybookAssignment] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DriverExplanationTemplate:
    driver_key: str
    label: str
    why_it_matters: str
    confirm_evidence: str
    falsify_evidence: str
    next_source: str


@dataclass
class ShareReconciliation:
    status: str
    basis: str = "Unknown"
    adr_ratio: float | None = None
    period: str | None = None
    ordinary_share_count: float | None = None
    ads_share_count: float | None = None
    weighted_average_shares: float | None = None
    period_end_shares: float | None = None
    buyback_amount: float | None = None
    split_or_corporate_action: bool = False
    xbrl_concept_consistent: bool | None = None
    source: str = ""
    citations: list[Citation] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ThesisCluster:
    cluster_id: str
    label: str
    status: str
    stage: str
    direction: str
    score: int | None
    idea_ids: list[str] = field(default_factory=list)
    driver_name: str = "Unmapped"
    thesis: str = ""
    supporting_evidence: list[str] = field(default_factory=list)
    counter_thesis: str = ""
    valuation_bridge: list[str] = field(default_factory=list)
    priced_in: str = "Unknown"
    monitor_checklist: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    conviction_chain_status: str = "Not built"
    why_now: str = ""
    what_must_be_true: list[str] = field(default_factory=list)
    what_would_falsify: list[str] = field(default_factory=list)
    next_research_actions: list[str] = field(default_factory=list)


@dataclass
class ResearchQuestion:
    question_id: str
    title: str
    priority: str
    status: str
    driver_name: str
    source_signal: str
    why_it_matters: str
    missing_links: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    next_sources: list[str] = field(default_factory=list)
    market_capture_needs: list[str] = field(default_factory=list)
    answerability_status: str = "Unknown"
    answerability_score: int = 0
    answerability_gaps: list[str] = field(default_factory=list)
    decision_rule: str = ""
    hypothesis: str = ""
    minimum_evidence_package: list[str] = field(default_factory=list)
    answer_format: str = ""
    stop_condition: str = ""
    promotion_criteria: list[str] = field(default_factory=list)
    primary_source_types: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    falsification_tests: list[str] = field(default_factory=list)
    workplan_steps: list[str] = field(default_factory=list)
    related_idea_ids: list[str] = field(default_factory=list)
    equity_lens: str = ""
    credit_lens: str = ""


@dataclass
class ResearchScoutQuestion:
    question_id: str
    lens: str
    priority: str
    question: str
    why_it_matters: str
    source_types: list[str] = field(default_factory=list)
    expected_evidence: str = ""
    confirms_or_disproves: str = ""
    current_status: str = "source not attempted"
    related_idea_ids: list[str] = field(default_factory=list)
    story_use: str = ""


@dataclass
class ResearchScoutReport:
    ticker: str
    status: str
    summary: str
    generated_at: str
    questions: list[ResearchScoutQuestion] = field(default_factory=list)
    company_story_axes: list[str] = field(default_factory=list)
    sector_story_axes: list[str] = field(default_factory=list)
    geography_story_axes: list[str] = field(default_factory=list)
    peer_story_axes: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    provider: str = "deterministic_research_scout"


@dataclass
class ChangeEvent:
    category: str
    title: str
    summary: str
    severity: int
    direction: str
    event_date: str | None
    source: str
    citations: list[Citation] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    event_timestamp: str | None = None
    why_this_matters: str = ""


@dataclass
class MarketCapture:
    category: str
    price_reaction_pct: float | None
    consensus_revision_pct: float | None
    narrative_saturation: str
    explanation: str
    data_gaps: list[str] = field(default_factory=list)
    benchmark_ticker: str | None = None
    benchmark_reaction_pct: float | None = None
    abnormal_reaction_pct: float | None = None
    volatility_adjusted_move: float | None = None
    volume_ratio: float | None = None
    beta: float | None = None
    consensus_official: bool = True
    price_status: str = "unknown"
    consensus_status: str = "unknown"
    diagnosis: str = ""
    required_inputs: list[str] = field(default_factory=list)
    point_in_time_note: str = ""
    capture_mode: str = "Unclassified"


@dataclass
class MarketCaptureAction:
    area: str
    priority: str
    status: str
    action: str
    why_it_matters: str
    source_type: str
    related_idea_ids: list[str] = field(default_factory=list)


@dataclass
class MarketCaptureSnapshotNeed:
    idea_id: str
    event_date: str | None
    metric_family: str
    pre_event_snapshot: str
    post_event_snapshot: str
    accepted_sources: list[str] = field(default_factory=list)
    csv_row_hints: list[str] = field(default_factory=list)
    reason: str = ""
    status: str = "Needed"


@dataclass
class MarketCaptureImportPlan:
    status: str
    summary: str
    minimum_required_rows: int = 0
    minimum_viable_rows: int = 0
    full_revision_rows: int = 0
    required_files: list[str] = field(default_factory=list)
    optional_files: list[str] = field(default_factory=list)
    metric_families: list[str] = field(default_factory=list)
    event_dates: list[str] = field(default_factory=list)
    template_command: str = ""
    import_command: str = ""
    blocking_reason: str = ""
    next_steps: list[str] = field(default_factory=list)
    provider_options: list[str] = field(default_factory=list)
    practical_next_step: str = ""


@dataclass
class ConsensusCoverageAdvisor:
    status: str
    blocker: str
    summary: str
    required_fix: str
    no_lookahead_rule: str
    provider_options: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class MarketCaptureAutofillPlan:
    status: str
    minimum_viable_rows: int
    full_revision_rows: int
    summary: str
    next_steps: list[str] = field(default_factory=list)
    required_files: list[str] = field(default_factory=list)
    optional_files: list[str] = field(default_factory=list)


@dataclass
class MarketCaptureReadiness:
    ticker: str
    status: str
    summary: str
    total_ideas: int
    classified_ideas: int
    unknown_ideas: int
    price_coverage: str
    consensus_coverage: str
    official_consensus_available: bool
    revision_windows_available: int = 0
    price_only_ideas: int = 0
    actions: list[MarketCaptureAction] = field(default_factory=list)
    snapshot_needs: list[MarketCaptureSnapshotNeed] = field(default_factory=list)
    import_plan: MarketCaptureImportPlan | None = None
    consensus_advisor: ConsensusCoverageAdvisor | None = None
    autofill_plan: MarketCaptureAutofillPlan | None = None
    data_gaps: list[str] = field(default_factory=list)
    point_in_time_rule: str = (
        "Use only point-in-time price windows and consensus snapshots observed on or before the event or post-event date; "
        "never backfill historical market-capture claims with today's consensus."
    )


@dataclass
class TargetConsensus:
    ticker: str
    as_of: str
    currency: str = "USD"
    target_aggregate: float | None = None
    target_mean: float | None = None
    target_median: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    analyst_count: int | None = None
    current_price: float | None = None
    implied_upside_pct: float | None = None
    dispersion_pct: float | None = None
    provider_timestamp: str | None = None
    freshness_days: int | None = None
    source: str = ""
    observed_at: str | None = None
    source_as_of: str | None = None
    entitlement_status: str = "available"
    provenance: str = ""
    official: bool = True
    target_label: str = "Mean target"
    target_kind: str = "mean"


@dataclass
class RecommendationConsensus:
    ticker: str
    as_of: str
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    consensus_label: str | None = None
    source: str = ""
    observed_at: str | None = None
    source_as_of: str | None = None
    entitlement_status: str = "available"
    provenance: str = ""
    official: bool = True


@dataclass
class EstimatePoint:
    ticker: str
    as_of: str
    metric: str
    period_end: str
    period_type: str
    average: float | None = None
    high: float | None = None
    low: float | None = None
    analyst_count: int | None = None
    currency: str = "USD"
    source: str = ""
    observed_at: str | None = None
    source_as_of: str | None = None
    entitlement_status: str = "available"
    provenance: str = ""
    official: bool = True
    period_precision: str = "day"
    revisions_up: int | None = None
    revisions_down: int | None = None


@dataclass
class EarningsSurprise:
    ticker: str
    period_end: str
    actual_eps: float | None
    estimated_eps: float | None
    surprise_pct: float | None
    source: str = ""
    observed_at: str | None = None
    source_as_of: str | None = None
    provenance: str = ""
    official: bool = True


@dataclass
class RevisionWindow:
    metric: str
    window_days: int
    start_date: str | None
    end_date: str | None
    start_value: float | None
    end_value: float | None
    change_pct: float | None
    provider: str = ""
    status: str = "available"
    reason: str = ""
    source_kind: str = "local_snapshot"
    official: bool = True


@dataclass
class ProviderObservation:
    ticker: str
    provider: str
    field: str
    observed_at: str
    source_as_of: str | None
    value_numeric: float | None = None
    value_text: str | None = None
    currency: str | None = None
    analyst_count: int | None = None
    entitlement_status: str = "available"
    provenance: str = ""
    official: bool = True
    confidence: str = "Medium"


@dataclass
class ProviderStatus:
    provider: str
    status: str
    official: bool
    entitlement_status: str
    observed_at: str
    message: str = ""


@dataclass
class NetworkProbeStatus:
    provider: str
    endpoint_host: str
    check_type: str
    status: str
    failure_class: str
    message: str
    retryable: bool
    proxy_used: str = ""
    observed_at: str = ""
    suggested_fix: str = ""
    http_status: int | None = None


@dataclass
class NetworkDiagnosticReport:
    status: str
    network_class: str
    summary: str
    observed_at: str
    proxy_state: dict[str, str] = field(default_factory=dict)
    runtime_context: dict[str, str] = field(default_factory=dict)
    probes: list[NetworkProbeStatus] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ProviderComparison:
    field: str
    values: dict[str, float | str | None]
    spread_pct: float | None
    interpretation: str


@dataclass
class ConsensusPackage:
    ticker: str
    provider: str
    status: str
    target: TargetConsensus | None = None
    recommendations: RecommendationConsensus | None = None
    estimates: list[EstimatePoint] = field(default_factory=list)
    surprises: list[EarningsSurprise] = field(default_factory=list)
    revisions: list[RevisionWindow] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    observations: list[ProviderObservation] = field(default_factory=list)
    provider_statuses: list[ProviderStatus] = field(default_factory=list)
    comparisons: list[ProviderComparison] = field(default_factory=list)
    unofficial_only: bool = False
    provider_targets: list[TargetConsensus] = field(default_factory=list)


@dataclass
class ExpectationComparison:
    metric: str
    period_end: str | None
    expected: float | None
    actual: float | None
    surprise_pct: float | None
    post_event_revision_pct: float | None
    interpretation: str
    drivers: dict[str, float | None] = field(default_factory=dict)
    actual_source: str = ""
    estimate_source: str = ""
    estimate_as_of: str | None = None
    estimate_eligibility: str = "Unknown"


@dataclass
class GuidancePoint:
    metric: str
    period_end: str | None
    low: float | None
    high: float | None
    currency: str | None
    citation: Citation


@dataclass
class ExpectationsBridge:
    status: str
    headline: str
    comparisons: list[ExpectationComparison] = field(default_factory=list)
    target_revisions: list[RevisionWindow] = field(default_factory=list)
    numeric_guidance: list[GuidancePoint] = field(default_factory=list)
    price_reaction_pct: float | None = None
    price_source: str | None = None
    point_in_time_note: str = ""
    data_gaps: list[str] = field(default_factory=list)
    timeline: list[dict[str, object]] = field(default_factory=list)
    event_audits: list[ExpectationEventAudit] = field(default_factory=list)


@dataclass
class ExpectationEventAudit:
    event_id: str
    event_label: str
    form: str | None
    accession: str | None
    filing_date: str | None
    reporting_period: str | None
    actual_metrics_checked: list[str] = field(default_factory=list)
    eligible_pre_event_snapshots: int = 0
    eligible_post_event_snapshots: int = 0
    status: str = "Unavailable"
    reason_code: str = "actual_missing"
    reason: str = ""
    providers: list[str] = field(default_factory=list)
    point_in_time_note: str = ""


@dataclass
class ValuationCase:
    name: str
    probability: float
    fair_value: float | None
    method: str
    assumptions: list[str] = field(default_factory=list)
    probability_status: str = "Uncalibrated"


@dataclass
class ValuationBridgeStep:
    case: str
    metric: str
    value: float | None
    unit: str
    formula: str
    source: str


@dataclass
class SensitivityPoint:
    row_label: str
    column_label: str
    fair_value: float | None


@dataclass
class ValuationResult:
    template: str
    status: str
    currency: str = "USD"
    cases: list[ValuationCase] = field(default_factory=list)
    probability_weighted_value: float | None = None
    expected_return_pct: float | None = None
    consensus_target: float | None = None
    disagreement_pct: float | None = None
    confidence: str = "Low"
    methodology: str = ""
    normalization_notes: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    bridge: list[ValuationBridgeStep] = field(default_factory=list)
    sensitivity: list[SensitivityPoint] = field(default_factory=list)
    reference_sources: list[str] = field(default_factory=list)


@dataclass
class CreditMetric:
    name: str
    value: float | None
    unit: str
    status: str
    interpretation: str
    source: str = ""


@dataclass
class CreditBridgeCheck:
    area: str
    status: str
    credit_question: str
    current_evidence: str
    required_evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    next_source: str = ""
    equity_implication: str = ""
    credit_implication: str = ""
    falsification_test: str = ""
    stage_impact: str = ""


@dataclass
class CreditLens:
    status: str
    risk_level: str
    summary: str
    metrics: list[CreditMetric] = field(default_factory=list)
    credit_bridge: list[CreditBridgeCheck] = field(default_factory=list)
    positives: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    monitor_rules: list[str] = field(default_factory=list)
    credit_catalysts: list[str] = field(default_factory=list)
    falsification_tests: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    source_note: str = "Derived from available structured financial metrics; not a rating opinion."


@dataclass
class WatchlistStatus:
    list_name: str
    ticker: str
    active: bool
    last_snapshot_at: str | None = None


@dataclass
class AlertRecord:
    alert_id: int
    ticker: str
    alert_type: str
    title: str
    message: str
    severity: int
    status: str
    created_at: str
    dedupe_key: str
    fiscal_period: str | None = None


@dataclass
class ScoreBreakdown:
    total: int
    evidence_strength: int
    novelty: int
    magnitude: int
    timing: int
    market_capture: int
    data_confidence: int
    rationale: list[str] = field(default_factory=list)
    score_cap: int | None = None
    score_cap_reason: str | None = None
    thesis_specificity: int = 0
    valuation_payoff: int = 0
    catalyst_timing: int = 0
    reproducibility: int = 0
    research_quality: int = 0
    valuation_completeness: int = 0
    evidence_strength_score: int = 0
    market_capture_confidence: int = 0
    actionability: int = 0


@dataclass
class ThesisAuditStep:
    step: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ThesisAuditChain:
    idea_id: str
    status: str
    summary: str
    steps: list[ThesisAuditStep] = field(default_factory=list)
    broken_links: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    name: str
    probability: float
    upside_downside_pct: float
    assumptions: list[str]
    probability_status: str = "Uncalibrated"
    exit_value: float | None = None
    entry_value: float | None = None
    gross_return_pct: float | None = None
    net_return_pct: float | None = None
    currency: str | None = None


@dataclass
class ScenarioAssumption:
    case: str
    metric: str
    value: float | None
    unit: str
    source: str
    formula: str = ""


@dataclass(frozen=True)
class PriceProviderStatus:
    ticker: str
    provider: str
    status: str
    observed_at: str
    message: str
    official: bool = True
    adjusted: bool = False
    source_url: str | None = None


@dataclass
class ProbabilityProvenance:
    source: str
    status: str
    sample_size: int = 0
    minimum_sample_size: int = 30
    note: str = ""


@dataclass
class PayoffCompleteness:
    status: str
    missing_inputs: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class AssumptionProvenance:
    field: str
    value: float | str | None
    source: str
    status: str = "Assumed"
    note: str = ""


@dataclass
class PayoffModel:
    status: str
    structure: str
    entry_price: float | None
    currency: str
    scenarios: list[Scenario] = field(default_factory=list)
    assumptions: list[ScenarioAssumption] = field(default_factory=list)
    expected_value_pct: float | None = None
    probability_provenance: ProbabilityProvenance | None = None
    transaction_cost_pct: float = 0.10
    dividend_return_pct: float = 0.0
    borrow_cost_pct: float | None = None
    hedge_ratio: float | None = None
    rank_eligible: bool = False
    data_gaps: list[str] = field(default_factory=list)
    payoff_completeness: PayoffCompleteness | None = None
    assumption_provenance: list[AssumptionProvenance] = field(default_factory=list)
    assumption_mode: str = "Model-derived"
    assumption_quality: str = "Unknown"
    entry_price_source: str = ""
    entry_price_as_of: str | None = None
    limitations: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class ConvictionChainStep:
    label: str
    status: str
    statement: str
    evidence: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ThesisConvictionChain:
    idea_id: str
    status: str
    confidence: str
    summary: str
    steps: list[ConvictionChainStep] = field(default_factory=list)
    what_must_be_true: list[str] = field(default_factory=list)
    what_would_falsify: list[str] = field(default_factory=list)
    next_research_actions: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class IdeaGateResult:
    stage: str
    eligible: bool
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    evaluated_at: str | None = None
    research_ready: bool = False
    high_conviction: bool = False
    research_ready_passed: list[str] = field(default_factory=list)
    research_ready_failed: list[str] = field(default_factory=list)
    high_conviction_passed: list[str] = field(default_factory=list)
    high_conviction_failed: list[str] = field(default_factory=list)


@dataclass
class MonitorItem:
    criterion: str
    data_source: str
    cadence: str
    confirm_trigger: str
    break_trigger: str
    status: str = "Watching"
    metric: str | None = None
    operator: str | None = None
    confirm_threshold: float | None = None
    break_threshold: float | None = None
    confirm_value: str | bool | None = None
    break_value: str | bool | None = None
    deadline: str | None = None
    source_field: str | None = None


@dataclass
class DriverFactor:
    cause: str
    direction: str
    confidence: str
    magnitude_hint: str
    explanation: str
    citations: list[Citation] = field(default_factory=list)
    missing_data_notes: list[str] = field(default_factory=list)


@dataclass
class DriverAnalysis:
    headline: str
    factors: list[DriverFactor] = field(default_factory=list)
    template: DriverExplanationTemplate | None = None
    bridge_status: str = "Unknown"
    primary_driver: str = ""
    mechanism: str = ""
    evidence_needed: list[str] = field(default_factory=list)
    peer_metric_checks: list[str] = field(default_factory=list)
    valuation_implication: str = ""
    credit_implication: str = ""
    falsification_tests: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ExternalEvidence:
    provider: str
    source_type: str
    title: str
    summary: str
    observed_at: str
    source_as_of: str | None
    source_tier: int
    official: bool
    confidence: str
    licensing_policy: str = "metadata_and_excerpt_only"
    metric_name: str | None = None
    metric_value: float | None = None
    unit: str | None = None
    frequency: str | None = None
    release_date: str | None = None
    vintage_date: str | None = None
    lookahead_safe: bool = True
    direction: str = "neutral"
    event_date: str | None = None
    citation: Citation | None = None
    tags: list[str] = field(default_factory=list)
    disqualifies_high_conviction: bool = True
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class ExternalEvidenceBundle:
    ticker: str
    status: str
    evidence: list[ExternalEvidence] = field(default_factory=list)
    provider_statuses: list[ProviderStatus] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class NewsSourceObservation:
    observation_id: str
    ticker: str
    provider: str
    source_family: str
    headline: str
    url: str
    published_at: str | None
    observed_at: str
    source_tier: int
    licensing_policy: str
    may_store_full_text: bool = False
    excerpt: str = ""
    language: str = "unknown"
    entity_match: str = "ticker"
    topic_tags: list[str] = field(default_factory=list)
    retrieval_manifest: dict[str, str] = field(default_factory=dict)


@dataclass
class NewsClaim:
    claim_id: str
    observation_id: str
    ticker: str
    company: str
    event_type: str
    affected_driver: str
    claimed_fact: str
    event_date: str | None
    source_tier: int
    confidence: str
    required_corroboration: list[str] = field(default_factory=list)
    citation: Citation | None = None
    status: str = "News detected"
    allowed_stage: str = "Candidate"
    source_family: str = "news"
    created_at: str = ""


@dataclass(frozen=True)
class RegisteredNewsSource:
    source_id: str
    label: str
    provider: str
    source_family: str
    default_tier: int
    licensing_policy: str
    enabled: bool
    configured: bool
    retrieval_mode: str = "metadata_and_excerpt"
    ownership_group: str = ""
    notes: str = ""


@dataclass
class NewsResearchPlan:
    ticker: str
    status: str
    generated_at: str
    aliases: list[str] = field(default_factory=list)
    event_queries: list[str] = field(default_factory=list)
    driver_queries: list[str] = field(default_factory=list)
    peer_queries: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=lambda: ["en"])
    event_start: str | None = None
    event_end: str | None = None
    provider: str = "deterministic"
    prompt_version: str = ""
    allowed_source_ids: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SyndicationFingerprint:
    fingerprint: str
    canonical_url: str
    normalized_headline: str
    source_origin_group: str
    duplicate_observation_ids: tuple[str, ...] = ()


@dataclass
class NewsArticleObservation:
    observation_id: str
    ticker: str
    provider: str
    source_id: str
    publisher: str
    source_origin_group: str
    article_type: str
    headline: str
    url: str
    published_at: str | None
    observed_at: str
    source_tier: int
    licensing_policy: str
    excerpt: str = ""
    language: str = "unknown"
    geography: str = ""
    topic_tags: list[str] = field(default_factory=list)
    causal_driver: str = "external_narrative"
    event_window_match: bool = False
    full_text_status: str = "metadata_only"
    syndication_fingerprint: str = ""
    evidence_id: str = ""


@dataclass
class NewsEventCluster:
    cluster_id: str
    ticker: str
    label: str
    causal_driver: str
    stance: str
    article_ids: list[str] = field(default_factory=list)
    source_origin_groups: list[str] = field(default_factory=list)
    summary: str = ""
    primary_source_status: str = "Unverified"
    confidence: str = "Low"


@dataclass
class NewsClaimAssessment:
    assessment_id: str
    ticker: str
    claim_id: str
    claim: str
    status: str
    article_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    source_origin_groups: list[str] = field(default_factory=list)
    source_tier: int = 4
    affected_driver: str = "external_narrative"
    primary_evidence_ids: list[str] = field(default_factory=list)
    explanation: str = ""
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class NewsIntelligenceReport:
    ticker: str
    status: str
    coverage_status: str
    summary: str
    plan: NewsResearchPlan
    registered_sources: list[RegisteredNewsSource] = field(default_factory=list)
    articles: list[NewsArticleObservation] = field(default_factory=list)
    clusters: list[NewsEventCluster] = field(default_factory=list)
    claim_assessments: list[NewsClaimAssessment] = field(default_factory=list)
    syndication: list[SyndicationFingerprint] = field(default_factory=list)
    top_debate: str = ""
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class PrimarySourceObservation:
    observation_id: str
    ticker: str
    source_type: str
    provider: str
    title: str
    url: str
    observed_at: str
    source_as_of: str | None
    source_tier: int
    official: bool
    driver_family: str
    summary: str
    citation: Citation | None = None
    corroborates_claim_ids: list[str] = field(default_factory=list)
    contradicts_claim_ids: list[str] = field(default_factory=list)
    licensing_policy: str = "metadata_and_excerpt_only"


@dataclass
class SourceCorroborationResult:
    result_id: str
    ticker: str
    claim_id: str
    status: str
    driver_family: str
    primary_source_status: str
    explanation: str
    required_sources: list[str] = field(default_factory=list)
    matched_observation_ids: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    observed_at: str = ""


@dataclass
class CausalBridgeSourceNeed:
    need_id: str
    ticker: str
    driver_family: str
    source_type: str
    source_family: str
    priority: str
    reason: str
    expected_evidence: str
    confirms_or_disproves: str
    related_claim_id: str | None = None


@dataclass
class BridgeComponent:
    name: str
    status: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)


@dataclass
class CausalBridge:
    bridge_id: str
    ticker: str
    idea_id: str | None
    driver_family: str
    status: str
    thesis_direction: str
    explanation: str
    required_primary_sources: list[str] = field(default_factory=list)
    supporting_news_claims: list[str] = field(default_factory=list)
    corroboration_status: str = "Primary corroboration missing"
    components: list[BridgeComponent] = field(default_factory=list)
    source_needs: list[CausalBridgeSourceNeed] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    observed_at: str = ""


@dataclass
class ExternalResearchExcerpt:
    excerpt_id: str
    ticker: str
    provider: str
    category: str
    report_id: str
    title: str
    source_as_of: str | None
    observed_at: str
    source_language: str
    original_excerpt: str
    translated_summary: str
    generated_summary: str
    theme_tags: list[str] = field(default_factory=list)
    citation: Citation | None = None
    source_tier: int = 4
    confidence: str = "Low"
    licensing_policy: str = "local_capped_excerpt_only"
    mentions_target_or_rating: bool = False
    non_consensus_label: str = "External analyst context; not official consensus."


@dataclass
class WisburgTheme:
    theme_id: str
    label: str
    stance: str
    driver: str
    summary: str
    evidence_count: int
    source_excerpt_ids: list[str] = field(default_factory=list)
    source_language_mix: list[str] = field(default_factory=list)
    confidence: str = "Low"


@dataclass
class AnalystDebateMap:
    status: str
    bullish_themes: list[WisburgTheme] = field(default_factory=list)
    bearish_themes: list[WisburgTheme] = field(default_factory=list)
    mixed_themes: list[WisburgTheme] = field(default_factory=list)
    strongest_bull_case: str = ""
    strongest_bear_case: str = ""
    caveats: list[str] = field(default_factory=list)


@dataclass
class ExternalNarrativeScore:
    status: str
    score: float | None
    label: str
    item_count: int
    repeated_topics: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


@dataclass
class WisburgSourceSuggestion:
    suggestion_id: str
    source_type: str
    title: str
    reason_to_inspect: str
    expected_evidence_type: str
    priority: str
    confirms_or_disproves: str
    linked_theme_id: str | None = None
    provider: str = "Wisburg research lens"


@dataclass
class WisburgToolEntitlement:
    tool_name: str
    status: str
    source_category: str
    detail_tool: str | None = None
    query_count: int = 0
    item_count: int = 0
    detail_success_count: int = 0
    message: str = ""

    @property
    def category(self) -> str:
        """Backward-compatible presentation alias for source_category."""
        return self.source_category

    @property
    def entitlement_status(self) -> str:
        """Backward-compatible presentation alias for status."""
        return self.status


@dataclass
class WisburgCoverageAudit:
    ticker: str
    status: str
    observed_at: str
    endpoint: str
    authentication_status: str
    tool_discovery_status: str
    tools: list[WisburgToolEntitlement] = field(default_factory=list)
    query_variants: list[str] = field(default_factory=list)
    total_items: int = 0
    detailed_items: int = 0
    source_classes_covered: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    licensing_policy: str = "capped_structured_extract_no_full_payload"


@dataclass
class WisburgReportRecord:
    report_key: str
    ticker: str
    report_id: str
    category: str
    title: str
    published_at: str | None
    observed_at: str
    source_language: str
    source_tier: int
    publisher: str = "Unknown"
    detail_status: str = "listing_only"
    content_scope: str = "metadata_excerpt_only"
    sections_found: list[str] = field(default_factory=list)
    capped_excerpt: str = ""
    citation: Citation | None = None
    content_fingerprint: str = ""
    licensing_policy: str = "capped_structured_extract_no_full_payload"


@dataclass
class WisburgStructuredClaim:
    claim_id: str
    report_key: str
    ticker: str
    claim_type: str
    statement: str
    driver: str
    direction: str
    source_as_of: str | None
    source_tier: int
    metric: str | None = None
    fiscal_period: str | None = None
    value: float | None = None
    previous_value: float | None = None
    unit: str | None = None
    currency: str | None = None
    confidence: str = "Low"
    citation: Citation | None = None
    corroboration_status: str = "Unverified external claim"
    primary_evidence_ids: list[str] = field(default_factory=list)
    corroboration_explanation: str = "Primary-source cross-check has not run."
    allowed_stage: str = "Candidate"
    evidence_label: str = "External opinion"


@dataclass
class WisburgRevisionObservation:
    revision_id: str
    report_key: str
    ticker: str
    revision_type: str
    metric: str
    source_as_of: str | None
    direction: str
    current_value: float | None = None
    previous_value: float | None = None
    change_pct: float | None = None
    fiscal_period: str | None = None
    currency: str | None = None
    unit: str | None = None
    statement: str = ""
    citation: Citation | None = None
    eligibility: str = "external_non_consensus"
    confidence: str = "Low"


@dataclass
class WisburgCorroborationDecision:
    claim_id: str
    status: str
    explanation: str
    matched_primary_evidence_ids: list[str] = field(default_factory=list)
    contradictory_evidence_ids: list[str] = field(default_factory=list)
    required_primary_sources: list[str] = field(default_factory=list)
    observed_at: str = ""


@dataclass
class WisburgResearchTask:
    task_id: str
    claim_id: str
    priority: str
    source_type: str
    action: str
    expected_evidence: str
    confirms_or_disproves: str
    status: str = "planned"
    provider: str = "Wisburg research intelligence"


@dataclass
class WisburgResearchLens:
    ticker: str
    status: str
    observed_at: str
    excerpts: list[ExternalResearchExcerpt] = field(default_factory=list)
    themes: list[WisburgTheme] = field(default_factory=list)
    debate_map: AnalystDebateMap | None = None
    narrative_score: ExternalNarrativeScore | None = None
    source_suggestions: list[WisburgSourceSuggestion] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    provider_status: str = "Unavailable"
    coverage_audit: WisburgCoverageAudit | None = None
    reports: list[WisburgReportRecord] = field(default_factory=list)
    structured_claims: list[WisburgStructuredClaim] = field(default_factory=list)
    revisions: list[WisburgRevisionObservation] = field(default_factory=list)
    corroboration: list[WisburgCorroborationDecision] = field(default_factory=list)
    research_tasks: list[WisburgResearchTask] = field(default_factory=list)


@dataclass
class WisburgSnapshotDelta:
    ticker: str
    status: str
    observed_at: str
    prior_observed_at: str | None = None
    new_report_ids: list[str] = field(default_factory=list)
    new_report_titles: list[str] = field(default_factory=list)
    new_themes: list[str] = field(default_factory=list)
    theme_stance_changes: list[dict[str, str]] = field(default_factory=list)
    prior_narrative_label: str | None = None
    current_narrative_label: str | None = None
    item_count_change: int | None = None
    new_revision_ids: list[str] = field(default_factory=list)
    new_revision_summaries: list[str] = field(default_factory=list)
    corroboration_changes: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""
    caveats: list[str] = field(default_factory=list)


@dataclass
class DailySnapshotStatus:
    ticker: str
    run_date: str
    observed_at: str
    overall_status: str
    consensus_status: str
    price_status: str
    wisburg_status: str
    consensus_rows_before: int = 0
    consensus_rows_after: int = 0
    alerts_created: int = 0
    used_same_day_wisburg_cache: bool = False
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class AttributionFactor:
    driver_type: str
    label: str
    direction: str
    confidence: str
    magnitude_pct: float | None
    explanation: str
    disconfirming_evidence: str
    source_tier: int
    citations: list[Citation] = field(default_factory=list)


@dataclass
class AttributionComponent:
    component_type: str
    label: str
    contribution_pct: float | None
    confidence: str
    source: str
    explanation: str
    disconfirming_evidence: str
    source_tier: int = 3


@dataclass
class AttributionWaterfall:
    status: str
    return_window: str | None
    raw_return_pct: float | None
    explained_pct: float | None
    residual_pct: float | None
    balance_check_pct: float | None
    components: list[AttributionComponent] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class AttributionQualityCheck:
    area: str
    status: str
    score: int
    summary: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    next_action: str = ""
    stage_impact: str = ""


@dataclass
class FactorExposure:
    factor_name: str
    factor_return_pct: float | None
    beta: float | None
    contribution_pct: float | None
    window: str | None
    confidence: str
    source: str
    source_as_of: str | None = None


@dataclass
class MacroShock:
    provider: str
    label: str
    metric_name: str | None
    change: float | None
    direction: str
    confidence: str
    source_as_of: str | None
    release_date: str | None = None


@dataclass
class PositioningSignal:
    provider: str
    label: str
    metric_name: str | None
    value: float | None
    direction: str
    confidence: str
    source_as_of: str | None
    summary: str = ""


@dataclass
class LiquiditySignal:
    label: str
    value: float | None
    direction: str
    confidence: str
    source: str
    summary: str = ""


@dataclass
class OptionsExpectation:
    provider: str
    status: str
    implied_move_pct: float | None = None
    implied_volatility_change_pct: float | None = None
    skew_signal: str = "Unknown"
    confidence: str = "Low"
    source_as_of: str | None = None
    summary: str = ""


@dataclass
class DriverAttribution:
    status: str
    classification: str
    headline: str
    confidence: str
    event_date: str | None
    return_window: str | None = None
    raw_return_pct: float | None = None
    market_relative_pct: float | None = None
    sector_relative_pct: float | None = None
    beta_adjusted_pct: float | None = None
    peer_sympathy_pct: float | None = None
    consensus_revision_pct: float | None = None
    narrative_saturation: str = "Unknown"
    narrative_score: float | None = None
    macro_context: list[ExternalEvidence] = field(default_factory=list)
    factors: list[AttributionFactor] = field(default_factory=list)
    waterfall: AttributionWaterfall | None = None
    factor_context: list[FactorExposure] = field(default_factory=list)
    macro_calendar_context: list[MacroShock] = field(default_factory=list)
    positioning_context: list[PositioningSignal] = field(default_factory=list)
    liquidity_context: list[LiquiditySignal] = field(default_factory=list)
    options_context: list[OptionsExpectation] = field(default_factory=list)
    residual_pct: float | None = None
    residual_explanation: str = ""
    classification_evidence: list[str] = field(default_factory=list)
    attribution_summary: list[str] = field(default_factory=list)
    attribution_readiness: str = "Unknown"
    attribution_quality_score: int = 0
    attribution_quality: list[AttributionQualityCheck] = field(default_factory=list)
    falsification_tests: list[str] = field(default_factory=list)
    next_attribution_checks: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class PeerReadthrough:
    peer_ticker: str
    evidence_status: str
    relation: str
    key_metric_changes: list[str] = field(default_factory=list)
    price_reaction_pct: float | None = None
    conclusion: str = ""
    citations: list[Citation] = field(default_factory=list)
    failure_status: str | None = None
    failure_reason: str | None = None
    sympathy_reaction: EventWindowReaction | None = None
    own_event_reaction: EventWindowReaction | None = None
    fiscal_alignment: str = "Unknown"
    metric_readthrough: PeerMetricReadthrough | None = None
    global_peer_coverage: GlobalPeerCoverage | None = None


@dataclass
class ValidatedClaim:
    claim_id: str
    ticker: str
    event_title: str
    event_category: str
    status: str
    is_substantive: bool
    claim_type: str
    direction: str
    metric: str | None = None
    period: str | None = None
    business_driver: str = "Unmapped"
    changed_text: str = ""
    prior_text: str = ""
    supporting_quote: str = ""
    counter_quote: str = ""
    confidence: str = "Low"
    reason: str = ""
    not_thesis_grade_reason: str = ""
    citation: Citation | None = None
    source: str = "deterministic"
    source_tier: int | None = None
    created_at: str = ""
    comparison_type: str = ""
    semantic_shift: str = ""
    required_confirmation: list[str] = field(default_factory=list)
    citation_ids_used: list[str] = field(default_factory=list)


@dataclass
class ClaimValidationResult:
    ticker: str
    status: str
    claims: list[ValidatedClaim] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    llm_used: bool = False
    provider: str = "deterministic"


@dataclass(frozen=True)
class SourceRegistryEntry:
    source_type: str
    label: str
    source_tier: int
    allowed: bool = True
    fetch_mode: str = "deterministic_adapter"
    notes: str = ""
    source_family: str = "core"
    allowed_stage: str = "High-Conviction"
    licensing_policy: str = "metadata_and_excerpt_only"
    default_tier: int | None = None


@dataclass
class ResearchSourceRequest:
    request_id: str
    source_type: str
    title: str
    reason_to_inspect: str
    expected_evidence_type: str
    priority: str
    cost_latency: str
    confirms_or_disproves: str
    suggested_url: str | None = None
    status: str = "planned"
    provider: str = "deterministic"


@dataclass
class ResearchSourceOutcome:
    request_id: str
    status: str
    provider: str
    observed_at: str
    source_url: str | None = None
    message: str = ""
    citation: Citation | None = None
    retrieval_status: str = ""
    parser_status: str = ""
    validation_status: str = ""
    failure_code: str = ""
    lifecycle: list[str] = field(default_factory=list)


@dataclass
class ResearchSourcePlan:
    ticker: str
    status: str
    generated_at: str
    registry_version: str
    requests: list[ResearchSourceRequest] = field(default_factory=list)
    outcomes: list[ResearchSourceOutcome] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    provider: str = "deterministic"


@dataclass
class LlmExtractionManifest:
    provider: str
    model: str
    prompt_version: str
    generated_at: str
    status: str
    validated_claim_ids: list[str] = field(default_factory=list)
    source_plan_request_ids: list[str] = field(default_factory=list)
    redacted_config: dict[str, str] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GlobalPeerIdentity:
    ticker: str
    issuer_name: str
    home_ticker: str
    home_exchange: str
    reporting_currency: str
    aliases: list[str] = field(default_factory=list)
    source_priority: list[str] = field(default_factory=list)
    source_urls: dict[str, str] = field(default_factory=dict)
    profile_source: str = "built_in"


@dataclass
class OfficialDocumentParseStatus:
    source_type: str
    status: str
    message: str
    url: str | None = None
    observed_at: str | None = None
    confidence: str = "Low"


@dataclass
class GlobalPeerDocument:
    document_id: str
    peer_ticker: str
    source_type: str
    title: str
    url: str | None
    published_at: str | None
    fiscal_period: str | None
    reporting_currency: str
    observed_at: str
    status: str
    parse_status: str
    source_tier: int = 1
    language: str = "unknown"
    excerpt: str = ""
    licensing_policy: str = "metadata_and_excerpt_only"


@dataclass
class GlobalPeerMetricObservation:
    observation_id: str
    peer_ticker: str
    metric: str
    value: float
    unit: str
    currency: str
    period_end: str | None
    fiscal_period: str | None
    source_document_id: str
    source_url: str | None
    source_type: str
    observed_at: str
    confidence: str = "Medium"
    validation_status: str = "validated"
    previous_value: float | None = None
    yoy_change_pct: float | None = None
    citation: Citation | None = None


@dataclass
class GlobalPeerCoverage:
    ticker: str
    status: str
    observed_at: str
    identity: GlobalPeerIdentity | None = None
    documents: list[GlobalPeerDocument] = field(default_factory=list)
    metrics: list[GlobalPeerMetricObservation] = field(default_factory=list)
    parse_statuses: list[OfficialDocumentParseStatus] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    provider: str = "official_global_peer"


@dataclass
class PeerMetricReadthrough:
    peer_ticker: str
    metric_family: str
    status: str
    relation: str
    summary: str
    fiscal_alignment: str = "Unknown"
    observations: list[GlobalPeerMetricObservation] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    source_tier: int | None = None
    required_metrics: list[str] = field(default_factory=list)
    present_metrics: list[str] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    falsification_tests: list[str] = field(default_factory=list)


@dataclass
class PeerMetricReadthroughSummary:
    status: str
    score: int
    summary: str
    total_peers: int = 0
    operating_metric_peers: int = 0
    missing_metric_peers: int = 0
    stale_metric_peers: int = 0
    price_only_peers: int = 0
    global_peer_peers: int = 0
    metric_families: list[str] = field(default_factory=list)
    confirmations: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    stage_impact: str = ""


@dataclass
class LlmResearchAgentManifest:
    provider: str
    model: str
    prompt_version: str
    generated_at: str
    status: str
    source_plan_request_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    metric_draft_ids: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    redacted_config: dict[str, str] = field(default_factory=dict)
    allowed_roles: list[str] = field(default_factory=list)
    prohibited_actions: list[str] = field(default_factory=list)
    source_registry_version: str = ""
    deterministic_executor: str = "deterministic_adapters"
    validation_gates: list[str] = field(default_factory=list)
    evidence_boundary: str = (
        "LLM assistant output is process guidance only; deterministic source adapters, "
        "citations, period/unit validation, and thesis gates remain authoritative."
    )


@dataclass
class LlmDocumentTriageResult:
    document_id: str
    status: str
    relevant_sections: list[str] = field(default_factory=list)
    table_hints: list[str] = field(default_factory=list)
    confidence: str = "Low"
    message: str = ""


@dataclass
class LlmMetricExtractionDraft:
    draft_id: str
    document_id: str
    metric: str
    value: float | None
    unit: str | None
    currency: str | None
    period_end: str | None
    quote: str
    table_or_page: str
    confidence: str
    validation_status: str = "provisional"
    ambiguity_flags: list[str] = field(default_factory=list)


@dataclass
class LlmTrendAnalysis:
    status: str
    summary: str
    peer_patterns: list[str] = field(default_factory=list)
    company_patterns: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    provider: str = "deterministic"


@dataclass
class TradeIdea:
    idea_id: str
    title: str
    direction: str
    structure: str
    thesis: str
    horizon: str
    catalyst: str
    variant_perception: str
    source_events: list[ChangeEvent]
    citations: list[Citation] = field(default_factory=list)
    market_capture: MarketCapture | None = None
    score: ScoreBreakdown | None = None
    driver_analysis: DriverAnalysis | None = None
    driver_attribution: DriverAttribution | None = None
    scenarios: list[Scenario] = field(default_factory=list)
    monitor_items: list[MonitorItem] = field(default_factory=list)
    peer_readthrough: list[PeerReadthrough] = field(default_factory=list)
    stage: str = "Candidate"
    signal_family: str = ""
    strongest_counter_thesis: str = "Not yet evaluated."
    gate_result: IdeaGateResult | None = None
    payoff_model: PayoffModel | None = None
    probability_provenance: ProbabilityProvenance | None = None
    thesis_cluster_id: str | None = None
    thesis_cluster_label: str | None = None
    conviction_chain: ThesisConvictionChain | None = None
    validated_claim_ids: list[str] = field(default_factory=list)
    thesis_grade_status: str = "Unvalidated"
    direction_rationale: str = ""
    next_source_to_check: str = ""
    driver_template_summary: str = ""
    normalization_status: str = ""
    share_reconciliation: ShareReconciliation | None = None
    thesis_audit_chain: ThesisAuditChain | None = None
    news_claim_ids: list[str] = field(default_factory=list)
    primary_source_status: str = ""
    corroboration_gaps: list[str] = field(default_factory=list)
    causal_bridge: CausalBridge | None = None
    bridge_status: str = ""
    bridge_direction_rationale: str = ""
    peer_metric_readthrough: list[PeerMetricReadthrough] = field(default_factory=list)
    peer_metric_summary: PeerMetricReadthroughSummary | None = None
    global_peer_coverage: list[GlobalPeerCoverage] = field(default_factory=list)
    causal_bridge_status: str = ""
    equity_credit_lens: dict[str, str] = field(default_factory=dict)
    llm_contribution: dict[str, str] = field(default_factory=dict)
    user_assumptions: dict[str, object] = field(default_factory=dict)
    promotion_decision: PromotionGateDecision | None = None
    promotion_label: str = ""


@dataclass
class EventWorkflowItem:
    item_type: str
    title: str
    due_date: str | None
    priority: str
    source: str
    reason: str
    related_idea_id: str | None = None
    status: str = "Pending"


@dataclass
class EventWorkflow:
    ticker: str
    status: str
    generated_at: str
    items: list[EventWorkflowItem] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ActionPlan:
    criterion: str
    source_field: str
    metric: str | None
    operator: str | None
    threshold: float | None
    deadline: str | None
    confirm_trigger: str
    break_trigger: str
    cadence: str = "Daily or after material events"


@dataclass
class EvidenceSufficiency:
    status: str
    score: int
    rationale: str
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ThesisCritique:
    strongest_counter_thesis: str
    key_uncertainties: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    what_would_falsify: list[str] = field(default_factory=list)


@dataclass
class ThesisBrief:
    status: str
    verdict: str
    idea_id: str | None
    title: str
    stage: str
    direction: str
    thesis: str
    variant_perception: str
    evidence_chain: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    supporting_idea_ids: list[str] = field(default_factory=list)
    source: str = "deterministic"
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ICOnePager:
    ticker: str
    status: str
    verdict: str
    title: str
    stage: str
    direction: str
    thesis: str
    variant_perception: str
    causal_bridge: str
    price_move: str
    market_capture: str
    valuation: str
    equity_lens: str
    credit_lens: str
    counter_thesis: str
    monitor_actions: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)
    work_order_actions: list[str] = field(default_factory=list)
    source: str = "deterministic"
    decision: str = ""
    decision_reason: str = ""
    why_now: str = ""
    blocking_issue: str = ""
    next_best_action: str = ""
    rank_eligibility: str = ""
    go_no_go_reason: str = ""


@dataclass
class ICDecisionBrief:
    ticker: str
    verdict: str
    decision: str
    direction: str
    stage: str
    confidence: str
    horizon: str
    thesis: str
    why_now: str
    financial_bridge: str
    valuation: str
    principal_risk: str
    next_decision: str
    freshness: str = "Unknown"
    evidence_status: str = "Unknown"
    why_research_next: str = ""
    why_now_evidence_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DemoCase:
    demo_id: str
    ticker: str
    title: str
    lesson: str
    expected_runtime: str = "Instant"
    network_required: bool = False
    badge: str = "No API keys"
    screenshot_focus: str = ""
    content_version: str = ""
    refreshed_at: str = ""
    research_profile: str = ""
    budget_mode: str = ""
    enabled_layers: tuple[str, ...] = ()
    fixture_policy: str = "Sanitized deterministic fixture; no live provider call at demo load."


@dataclass
class PipelineStage:
    stage_id: str
    label: str
    status: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    next_action: str = ""


@dataclass
class ResearchRunProgress:
    status: str
    summary: str
    stages: list[PipelineStage] = field(default_factory=list)


@dataclass
class EvidenceDrawer:
    label: str
    claim: str
    source: str = ""
    url: str = ""
    source_tier: int | None = None
    accession: str | None = None
    section: str | None = None
    metric: str = ""
    value: str = ""
    formula: str = ""
    period: str | None = None
    unit: str = ""
    currency: str = ""
    confidence: str = "Unknown"
    parser_status: str = "Unknown"
    excerpt: str = ""


@dataclass
class StoryCard:
    card_id: str
    title: str
    status: str
    summary: str
    body: str
    next_action: str = ""
    evidence: list[EvidenceDrawer] = field(default_factory=list)


@dataclass
class BullBearJudgePanel:
    status: str
    bull_case: str
    bear_case: str
    judge_accepts: list[str] = field(default_factory=list)
    still_unproven: list[str] = field(default_factory=list)
    resolution_plan: list["JudgeResolutionItem"] = field(default_factory=list)


@dataclass
class JudgeResolutionItem:
    issue_type: str
    status: str
    issue: str
    evidence: str = ""
    app_action: str = ""
    user_action: str = ""
    blocking_scope: str = "High-Conviction"
    auto_resolvable: bool = False
    action_id: str = ""


@dataclass
class FormulaTrace:
    trace_id: str
    label: str
    value: str
    source_field: str
    formula: str
    period: str | None = None
    currency: str = ""
    confidence: str = "Unknown"
    source: str = ""
    citation: Citation | None = None


@dataclass
class LlmGuardrailCheck:
    area: str
    status: str
    score: int
    summary: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    enforcement: str = ""


@dataclass
class LLMRunManifest:
    provider: str
    model: str
    prompt_version: str
    generated_at: str
    status: str
    llm_execution_status: str = ""
    llm_guardrail_status: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    citation_ids: list[str] = field(default_factory=list)
    token_estimate: int | None = None
    redacted_config: dict[str, str] = field(default_factory=dict)
    message: str = ""
    prompt_hash: str = ""
    prompt_context_counts: dict[str, int] = field(default_factory=dict)
    guardrail_policy: list[str] = field(default_factory=list)
    guardrail_checks: list[LlmGuardrailCheck] = field(default_factory=list)
    guardrail_score: int = 0
    failure_class: str = ""
    retryable: bool = False
    provider_health: str = ""
    timeout_seconds: int | None = None


@dataclass
class LlmProviderPreset:
    preset_id: str
    label: str
    adapter: str
    default_model: str
    default_base_url: str
    requires_base_url: bool = False
    supports_json: bool = True
    notes: str = ""


@dataclass
class LlmProviderProfile:
    profile_id: str
    display_name: str
    provider_preset: str
    model: str
    base_url: str
    role_eligibility: str = "primary_secondary"
    created_at: str = ""
    updated_at: str = ""
    key_configured: bool = False
    secret_ref: str = ""
    last_test_status: str = "not_tested"
    last_test_message: str = ""
    last_test_at: str | None = None


@dataclass
class SourceLanguageExcerpt:
    citation_id: str
    source_language: str
    original_excerpt: str
    translated_summary: str | None = None
    source: str = ""
    url: str = ""


@dataclass
class LanguageAudit:
    policy: str
    source_languages: list[str] = field(default_factory=list)
    excerpts: list[SourceLanguageExcerpt] = field(default_factory=list)
    chinese_source_notes: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class LlmReviewResult:
    role: str
    provider: str
    model: str
    status: str
    summary: str = ""
    disagreements: list[str] = field(default_factory=list)
    missed_counter_thesis: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    language_quality_issues: list[str] = field(default_factory=list)
    readability_suggestions: list[str] = field(default_factory=list)
    generated_at: str = ""
    message: str = ""


@dataclass
class LlmComparison:
    status: str
    primary_provider: str = ""
    secondary_provider: str = ""
    agreement: str = "Unknown"
    key_differences: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    verdict: str = ""


@dataclass(frozen=True)
class ExecutionBackend:
    backend_id: str
    label: str
    model: str
    public: bool
    reasoning_mode: str = ""
    supports_background: bool = False
    local_only: bool = False


@dataclass(frozen=True)
class HybridResearchProfile:
    profile_id: str
    label: str
    round_count: int = 5
    planner_model: str = "deepseek-v4-pro"
    executor_model: str = "gpt-5.6-sol"
    checker_model: str = "deepseek-v4-pro"
    critic_model: str = "gpt-5.6-sol"
    reasoning_mode: str = "pro"
    requires_deep_initiation: bool = True


@dataclass
class ResearchRoundArtifact:
    artifact_id: str
    artifact_type: str
    status: str
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    citation_ids: list[str] = field(default_factory=list)
    source_request_ids: list[str] = field(default_factory=list)
    validation_gaps: list[str] = field(default_factory=list)


@dataclass
class ResearchAuditFinding:
    finding_id: str
    category: str
    severity: str
    summary: str
    evidence_ids: list[str] = field(default_factory=list)
    recommended_source_types: list[str] = field(default_factory=list)
    evidence_request_ids: list[str] = field(default_factory=list)
    affected_claim_ids: list[str] = field(default_factory=list)
    what_would_change_assessment: str = ""
    resolved: bool = False


@dataclass
class ResearchRound:
    round_number: int
    role: str
    provider: str
    model: str
    backend: str
    status: str
    started_at: str = ""
    completed_at: str = ""
    latency_ms: int | None = None
    prompt_hash: str = ""
    artifact: ResearchRoundArtifact | None = None
    findings: list[ResearchAuditFinding] = field(default_factory=list)
    message: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    provider_cost_usd: float | None = None
    retry_count: int = 0
    quality_score: int | None = None
    quality_gain: int | None = None


@dataclass
class SharedRetrievalManifest:
    evidence_hash: str
    initial_request_ids: list[str] = field(default_factory=list)
    follow_up_request_ids: list[str] = field(default_factory=list)
    initial_outcomes: list[dict[str, Any]] = field(default_factory=list)
    follow_up_outcomes: list[dict[str, Any]] = field(default_factory=list)
    source_registry_version: str = ""
    shared_by_variants: bool = True


@dataclass
class AnalogAuditDecision:
    status: str
    decision: str
    auditor_provider: str = ""
    auditor_model: str = ""
    retry_count: int = 0
    failure_class: str = ""
    critical_failures: list[str] = field(default_factory=list)
    unresolved_findings: list[str] = field(default_factory=list)
    last_success_at: str = ""


@dataclass
class AnalogQualityScorecard:
    total_score: int
    status: str
    current_baseline: int = 0
    exact_episode: int = 0
    chronology: int = 0
    mechanism: int = 0
    similarities: int = 0
    differences: int = 0
    outcome_path: int = 0
    hidden_risks: int = 0
    lessons_and_monitors: int = 0
    citation_relevance: int = 0
    critical_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class AnalogResearchVariant:
    variant_id: str
    label: str
    status: str
    primary_provider: str
    investigator_model: str
    auditor_provider: str
    auditor_model: str
    rounds: list[ResearchRound] = field(default_factory=list)
    final_artifact: ResearchRoundArtifact | None = None
    audit_decision: AnalogAuditDecision | None = None
    quality_scorecard: AnalogQualityScorecard | None = None
    latency_ms: int = 0
    cache_status: str = "miss"
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class AnalogComparison:
    status: str
    primary_variant_id: str
    challenger_variant_id: str
    agreement: str = "Unknown"
    primary_quality_score: int | None = None
    challenger_quality_score: int | None = None
    agreements: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    evidence_coverage_differences: list[str] = field(default_factory=list)


@dataclass
class EvidenceExclusionRecord:
    evidence_id: str
    kind: str
    reason: str
    score: float
    source_type: str = ""
    source_tier: int | None = None
    period: str = ""
    paired_evidence_id: str = ""
    retained_in_search_catalog: bool = True


@dataclass
class EvidencePackAudit:
    ticker: str
    status: str
    available_count: int
    core_count: int
    searchable_count: int
    core_evidence_ids: list[str] = field(default_factory=list)
    searchable_evidence_ids: list[str] = field(default_factory=list)
    exclusions: list[EvidenceExclusionRecord] = field(default_factory=list)
    current_prior_pairs: list[list[str]] = field(default_factory=list)
    unpaired_change_ids: list[str] = field(default_factory=list)
    core_missing_period_ids: list[str] = field(default_factory=list)
    core_missing_provenance_ids: list[str] = field(default_factory=list)
    source_family_counts: dict[str, int] = field(default_factory=dict)
    duplicate_count: int = 0
    selection_policy: str = ""
    order_invariant: bool = True
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class SourceRetrievalLifecycle:
    request_id: str
    source_id: str
    status: str
    source_type: str = ""
    history: list[str] = field(default_factory=list)
    failure_code: str = ""
    message: str = ""
    attempted_at: str = ""
    completed_at: str = ""


@dataclass
class NormalizedSourceRecord:
    source_id: str
    ticker: str
    entity_name: str
    security_id: str
    source_family: str
    source_type: str
    document_id: str
    source_url: str
    publication_date: str
    reporting_period: str
    section: str
    page_or_table: str
    exact_excerpt: str
    metric_or_claim: str
    value: float | str | None
    unit: str
    currency: str
    source_tier: int | None
    origin_group: str
    licensing_policy: str
    retrieval_status: str
    parser_status: str
    validation_status: str
    citation_id: str = ""
    accession: str = ""
    form: str = ""
    content_hash: str = ""
    formula: str = ""
    derived_from_source_ids: list[str] = field(default_factory=list)
    observed_at: str = ""
    period_start: str = ""
    duration_days: int | None = None
    fiscal_period: str = ""
    fiscal_year: int | None = None
    fiscal_frame: str = ""
    failure_code: str = ""
    message: str = ""


@dataclass
class HistoricalFinancialPath:
    ticker: str
    metric: str
    status: str
    observations: list[dict[str, Any]] = field(default_factory=list)
    formula: str = ""
    source_ids: list[str] = field(default_factory=list)
    no_lookahead_safe: bool = True
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class HistoricalValuationObservation:
    ticker: str
    observation_date: str
    price: float | None
    diluted_shares: float | None
    market_cap: float | None
    enterprise_value: float | None
    revenue_ttm: float | None
    earnings_ttm: float | None
    fcf_ttm: float | None
    cash: float | None
    debt: float | None
    currency: str
    formula_traces: dict[str, str] = field(default_factory=dict)
    source_ids: list[str] = field(default_factory=list)
    confidence: str = "Unknown"
    no_lookahead_safe: bool = True
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ContractualExposureObservation:
    exposure_id: str
    ticker: str
    exposure_type: str
    status: str
    description: str
    amount: float | None = None
    unit: str = ""
    currency: str = ""
    period: str = ""
    counterparty: str = ""
    cancellation_terms: str = ""
    source_ids: list[str] = field(default_factory=list)
    confidence: str = "Unknown"
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class RegulatoryEventObservation:
    event_id: str
    ticker: str
    proceeding: str
    event_date: str
    jurisdiction: str
    status: str
    remedy: str = ""
    affected_business: str = ""
    affected_geography: str = ""
    issuer_response: str = ""
    financial_mechanism: str = ""
    source_ids: list[str] = field(default_factory=list)
    confidence: str = "Unknown"
    data_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClaimEvidencePolicy:
    policy_id: str
    claim_type: str
    label: str
    required_fields: tuple[str, ...]
    preferred_source_types: tuple[str, ...]
    minimum_independent_origins: int = 1
    primary_or_official_required: bool = False
    current_prior_required: bool = False
    numerical_reconciliation_required: bool = False
    no_lookahead_required: bool = True
    notes: str = ""


@dataclass
class DiscoveredSourceCandidate:
    candidate_id: str
    ticker: str
    url: str
    title: str
    publisher: str
    ownership_group: str
    proposed_source_family: str
    proposed_source_type: str
    publication_date: str = ""
    language: str = "unknown"
    discovery_provider: str = ""
    discovery_method: str = "model_or_search"
    claimed_relevance: str = ""
    claim_to_test: str = ""
    excerpt: str = ""
    licensing_policy: str = "metadata_only"
    full_text_status: str = "metadata_only"
    syndication_fingerprint: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    candidate_type: str = "source_candidate"
    materiality: str = "Medium"
    research_request_id: str = ""
    proposed_mapping: str = ""
    company_or_expert: str = ""


@dataclass
class SourceAdmissionDecision:
    decision_id: str
    candidate_id: str
    ticker: str
    status: str
    admitted_as: str
    canonical_url: str
    source_family: str
    source_type: str
    source_tier: int | None
    allowed_stage: str
    ownership_group: str
    licensing_policy: str
    retrieval_status: str
    parser_status: str
    validation_status: str
    citation_bound: bool = False
    prompt_injection_quarantined: bool = False
    reasons: list[str] = field(default_factory=list)
    evidence_id: str = ""
    decided_at: str = ""


@dataclass
class RegulatoryProspectObservation:
    prospect_id: str
    ticker: str
    headline: str
    jurisdiction: str
    authority: str
    procedural_stage: str
    classification: str
    affected_business: str = ""
    affected_geography: str = ""
    expected_milestone: str = ""
    event_date: str = ""
    financial_mechanisms: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    source_origin_groups: list[str] = field(default_factory=list)
    official_source_ids: list[str] = field(default_factory=list)
    semi_official_source_ids: list[str] = field(default_factory=list)
    external_source_ids: list[str] = field(default_factory=list)
    allowed_stage: str = "Candidate"
    confidence: str = "Low"
    status: str = "Unverified"
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class RegulatoryMomentumAssessment:
    ticker: str
    status: str
    summary: str
    highest_stage: str
    prospect_count: int
    official_milestone_count: int
    independent_external_origin_count: int
    next_milestones: list[str] = field(default_factory=list)
    prospects: list[RegulatoryProspectObservation] = field(default_factory=list)
    coverage_status: str = "Unavailable"
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ClaimEvidenceAudit:
    claim_id: str
    claim_text: str
    classification: str
    materiality: str
    claim_type: str = ""
    required_evidence_fields: list[str] = field(default_factory=list)
    supporting_evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    period_alignment: str = "Unknown"
    entity_alignment: str = "Unknown"
    independent_origin_count: int = 0
    numerical_reconciliation: str = "not_applicable"
    status: str = "unreviewed"
    blockers: list[str] = field(default_factory=list)


@dataclass
class AuditRepairItem:
    repair_id: str
    claim_id: str
    rejection_reason: str
    missing_source_type: str
    missing_metric: str
    retrieval_request: dict[str, Any]
    retrieval_status: str = "planned"
    retrieval_outcome_ids: list[str] = field(default_factory=list)
    resolution: str = "unresolved"
    revised_claim: str = ""
    final_audit_status: str = "not_run"


@dataclass
class AuditRepairQueue:
    ticker: str
    status: str
    items: list[AuditRepairItem] = field(default_factory=list)
    critical_open_count: int = 0
    generated_at: str = ""
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class PartialResearchRerunManifest:
    parent_run_id: str
    parent_evidence_hash: str
    new_evidence_hash: str
    changed_evidence_ids: list[str] = field(default_factory=list)
    reused_round_numbers: list[int] = field(default_factory=list)
    rerun_round_numbers: list[int] = field(default_factory=list)
    idempotency_key: str = ""
    audit_retry_history: list[str] = field(default_factory=list)
    status: str = "not_applicable"


@dataclass
class DemoFreezeDecision:
    ticker: str
    status: str
    allowed: bool
    evaluated_at: str
    failed_claim_ids: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    output_path: str = ""
    evidence_hash: str = ""
    validation_status: str = "not_run"
    source_run_id: str = ""
    source_run_completed_at: str = ""
    qa_closure_state: str = ""
    policy_version: str = "demo-quality-policy-v3"
    artifact_schema_version: str = "demo-hybrid-artifact-v2"
    sanitization_status: str = "not_run"
    research_profile: str = ""
    source_policy_summary: str = ""
    publication_gate_evaluation: PublicationGateEvaluation | None = None
    repair_actions: list[ResearchRepairAction] = field(default_factory=list)
    publication_label: str = "Not publication-ready"
    canonical_revision_id: str = ""


@dataclass(frozen=True)
class DemoQualityPolicy:
    policy_version: str = "demo-quality-policy-v3"
    artifact_schema_version: str = "demo-hybrid-artifact-v2"
    require_completed_run: bool = True
    require_deterministic_validation: bool = True
    require_closed_qa: bool = True
    require_audited_analog: bool = False
    require_independent_quantified_origins: int = 2
    require_no_failed_material_claims: bool = True
    require_no_promoted_quarantined_metadata: bool = True
    require_sanitized_artifact: bool = True
    minimum_publishable_score: int = 75
    publication_quality_score: int = 85
    eligible_closure_states: tuple[str, ...] = (
        "closed",
        "closed_with_residual_risk",
    )
    allow_analog_candidates: bool = True
    require_no_critical_failures: bool = True
    ticker_minimum_scores: dict[str, int] = field(
        default_factory=dict
    )
    ticker_required_paths: dict[str, tuple[str, ...]] = field(
        default_factory=lambda: {
            "AAPL": (
                "Free Cash Flow TTM",
                "Long-term Debt",
                "Share Repurchases",
            ),
        }
    )
    default_coverage_profile_id: str = "operating_company"
    enforce_coverage_profile_paths: bool = False
    validation_policy_version: str = "deterministic-validation-v1"
    parser_version: str = "registered-parser-v1"


@dataclass
class RetrievalAuditReport:
    ticker: str
    profile: str
    status: str
    benchmark_ready: bool
    generated_at: str
    summary: str
    entity_complete: bool
    input_sufficiency_score: int
    claim_ready_count: int
    claim_blocked_count: int
    source_status_counts: dict[str, int] = field(default_factory=dict)
    source_origin_count: int = 0
    critical_blockers: list[str] = field(default_factory=list)
    manual_requirements: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    retrieval_lifecycles: list[SourceRetrievalLifecycle] = field(
        default_factory=list
    )
    normalized_sources: list[NormalizedSourceRecord] = field(
        default_factory=list
    )
    claim_audits: list[ClaimEvidenceAudit] = field(default_factory=list)
    repair_queue: AuditRepairQueue | None = None
    regulatory_momentum: RegulatoryMomentumAssessment | None = None
    semantic_evidence_hash: str = ""
    output_json: str = ""
    output_markdown: str = ""
    no_llm_calls: bool = True
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class InputSufficiencyDimension:
    dimension_id: str
    label: str
    score: int
    maximum: int
    status: str
    summary: str
    gaps: list[str] = field(default_factory=list)


@dataclass
class InputSufficiencyScore:
    ticker: str
    status: str
    total_score: int
    benchmark_ready: bool
    threshold: int = 80
    dimensions: list[InputSufficiencyDimension] = field(default_factory=list)
    critical_gaps: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ResearchUsageSummary:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0
    retries: int = 0
    retrieval_calls: int = 0
    wall_clock_ms: int = 0
    provider_cost_usd: float | None = None
    preflight_estimated_tokens: int = 0
    preflight_multiple: float | None = None
    review_required: bool = False


@dataclass
class HybridProviderReadiness:
    role: str
    provider: str
    model: str
    status: str
    credential_source: str = ""
    message: str = ""


@dataclass
class HybridExecutionPreflight:
    ticker: str
    status: str
    research_profile: str
    deepseek: HybridProviderReadiness
    sol: HybridProviderReadiness
    evidence_status: str = "Pending deterministic preflight"
    execution_status: str = "new"
    expected_model_calls: int = 10
    expected_retrieval_passes: int = 2
    cache_status: str = "unknown"
    blockers: list[str] = field(default_factory=list)


@dataclass
class HybridJobProgress:
    status: str = "queued"
    current_stage: str = "Preparing deterministic research"
    current_round: int = 0
    attempted_model_calls: int = 0
    completed_model_calls: int = 0
    provider: str = ""
    model: str = ""
    started_at: str = ""
    elapsed_seconds: float = 0.0
    retry_count: int = 0
    failure_class: str = ""
    message: str = ""


@dataclass
class DisplayedResultContext:
    result_type: str
    label: str
    ticker: str
    run_id: str = ""
    generated_at: str = ""
    active_job_id: str = ""
    active_job_ticker: str = ""
    message: str = ""


@dataclass
class SourceFreshnessStatus:
    component: str
    status: str
    source_as_of: str = ""
    observed_at: str = ""
    source: str = ""
    message: str = ""


@dataclass
class ResearchFreshnessSummary:
    overall_status: str
    summary: str
    run_generated_at: str = ""
    run_completed_at: str = ""
    result_type: str = "live"
    components: list[SourceFreshnessStatus] = field(default_factory=list)
    stale_components: list[str] = field(default_factory=list)
    unavailable_components: list[str] = field(default_factory=list)


@dataclass
class BenchmarkGapDecomposition:
    ticker: str
    status: str
    original_input_score: int
    enriched_input_score: int
    original_deepseek_score: int | None = None
    enriched_deepseek_score: int | None = None
    original_sol_score: int | None = None
    enriched_sol_score: int | None = None
    input_uplift: int = 0
    residual_reasoning_gap: int | None = None
    input_gap_categories: list[str] = field(default_factory=list)
    retrieval_gap_categories: list[str] = field(default_factory=list)
    reasoning_gap_categories: list[str] = field(default_factory=list)
    presentation_gap_categories: list[str] = field(default_factory=list)
    accepted_fixes: list[str] = field(default_factory=list)
    unresolved_gaps: list[str] = field(default_factory=list)


@dataclass
class HybridResearchRun:
    run_id: str
    ticker: str
    status: str
    profile: HybridResearchProfile
    execution_backend: ExecutionBackend
    rounds: list[ResearchRound] = field(default_factory=list)
    final_artifact: ResearchRoundArtifact | None = None
    audit_findings: list[ResearchAuditFinding] = field(default_factory=list)
    evidence_hash: str = ""
    source_registry_version: str = ""
    prompt_version: str = ""
    generated_at: str = ""
    completed_at: str = ""
    # Empty means the artifact predates the versioned lifecycle contract.
    # New run constructors set an explicit state; deserializers preserve the
    # distinction so historical policy decisions remain reproducible.
    lifecycle_state: str = ""
    research_attempt_finished_at: str = ""
    validation_completed_at: str = ""
    audit_completed_at: str = ""
    revision_completed_at: str = ""
    closure_completed_at: str = ""
    publishable_at: str = ""
    published_at: str = ""
    terminal_at: str = ""
    cache_status: str = "miss"
    deterministic_validation_status: str = "not_run"
    generation_provenance: dict[str, str] = field(default_factory=dict)
    data_gaps: list[str] = field(default_factory=list)
    variants: list[AnalogResearchVariant] = field(default_factory=list)
    selected_primary_variant_id: str = ""
    shared_retrieval_manifest: SharedRetrievalManifest | None = None
    analog_comparison: AnalogComparison | None = None
    audit_failure_streak: int = 0
    last_successful_audit_at: str = ""
    evidence_pack_audit: EvidencePackAudit | None = None
    input_sufficiency: InputSufficiencyScore | None = None
    usage_summary: ResearchUsageSummary | None = None
    gap_decomposition: BenchmarkGapDecomposition | None = None
    normalized_sources: list[NormalizedSourceRecord] = field(
        default_factory=list
    )
    retrieval_lifecycles: list[SourceRetrievalLifecycle] = field(
        default_factory=list
    )
    claim_evidence_audits: list[ClaimEvidenceAudit] = field(
        default_factory=list
    )
    audit_repair_queue: AuditRepairQueue | None = None
    historical_financial_paths: list[HistoricalFinancialPath] = field(
        default_factory=list
    )
    historical_valuation_observations: list[
        HistoricalValuationObservation
    ] = field(default_factory=list)
    contractual_exposures: list[ContractualExposureObservation] = field(
        default_factory=list
    )
    regulatory_events: list[RegulatoryEventObservation] = field(
        default_factory=list
    )
    source_candidates: list[DiscoveredSourceCandidate] = field(
        default_factory=list
    )
    source_admission_decisions: list[SourceAdmissionDecision] = field(
        default_factory=list
    )
    regulatory_momentum: RegulatoryMomentumAssessment | None = None
    evidence_fingerprints: dict[str, str] = field(default_factory=dict)
    partial_rerun_manifest: PartialResearchRerunManifest | None = None
    demo_freeze_decision: DemoFreezeDecision | None = None
    publication_gate_evaluations: list[PublicationGateEvaluation] = field(
        default_factory=list
    )
    repair_actions: list[ResearchRepairAction] = field(default_factory=list)
    semantic_quality_history: list[SemanticQualityEvaluation] = field(
        default_factory=list
    )
    derived_artifact_dependencies: list[DerivedArtifactDependency] = field(
        default_factory=list
    )
    scope_invalidations: list[ScopeInvalidationResult] = field(
        default_factory=list
    )
    canonical_publication_revisions: list[CanonicalPublicationRevision] = field(
        default_factory=list
    )


@dataclass
class ModelResearchArtifact:
    artifact_id: str
    stage_id: str
    role: str
    provider: str
    model: str
    status: str
    summary: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    citation_ids: list[str] = field(default_factory=list)
    source_request_ids: list[str] = field(default_factory=list)
    validation_gaps: list[str] = field(default_factory=list)
    latency_ms: int | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchGapDiagnosis:
    gap_id: str
    stage_id: str
    category: str
    severity: str
    summary: str
    stronger_model: str = "Unresolved"
    evidence_ids: list[str] = field(default_factory=list)
    recommended_fix: str = ""
    status: str = "open"


@dataclass
class AcceptedSecondaryDelta:
    delta_id: str
    stage_id: str
    summary: str
    evidence_ids: list[str] = field(default_factory=list)
    validation_status: str = "rejected"
    adoption_status: str = "not_adopted"
    reason: str = ""


@dataclass
class AdjudicationDecision:
    status: str
    selected_interpretation: str
    rationale: str
    adjudicator_provider: str = ""
    adjudicator_model: str = ""
    accepted_delta_ids: list[str] = field(default_factory=list)
    unresolved_disagreements: list[str] = field(default_factory=list)
    prohibited_overrides: list[str] = field(default_factory=list)


@dataclass
class ResearchOutputComparison:
    stage_id: str
    status: str
    agreement: str
    deepseek_score: int
    sol_score: int
    stronger_model: str
    dimension_scores: dict[str, dict[str, int]] = field(default_factory=dict)
    agreements: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)
    sol_additions: list[str] = field(default_factory=list)
    deepseek_additions: list[str] = field(default_factory=list)
    gap_ids: list[str] = field(default_factory=list)


@dataclass
class DualModelStage:
    stage_id: str
    label: str
    status: str
    deepseek_artifact: ModelResearchArtifact | None = None
    sol_artifact: ModelResearchArtifact | None = None
    comparison: ResearchOutputComparison | None = None
    accepted_secondary_deltas: list[AcceptedSecondaryDelta] = field(
        default_factory=list
    )
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ResearchImprovementCandidate:
    candidate_id: str
    category: str
    title: str
    proposed_change: str
    supporting_gap_ids: list[str] = field(default_factory=list)
    required_benchmark_tickers: list[str] = field(default_factory=list)
    activation_status: str = "review_required"
    runtime_mutation_allowed: bool = False


@dataclass
class ResearchQualityBenchmark:
    status: str
    deepseek_score: int
    sol_score: int
    combined_score: int
    stage_winners: dict[str, str] = field(default_factory=dict)
    critical_failures: list[str] = field(default_factory=list)
    benchmark_dimensions: dict[str, dict[str, int]] = field(
        default_factory=dict
    )


@dataclass
class DualModelResearchRun:
    run_id: str
    ticker: str
    status: str
    research_profile: str
    evidence_hash: str
    generated_at: str
    completed_at: str = ""
    final_label: str = "Paired research not started"
    stages: list[DualModelStage] = field(default_factory=list)
    gap_diagnoses: list[ResearchGapDiagnosis] = field(default_factory=list)
    accepted_secondary_deltas: list[AcceptedSecondaryDelta] = field(
        default_factory=list
    )
    adjudication: AdjudicationDecision | None = None
    benchmark: ResearchQualityBenchmark | None = None
    improvement_candidates: list[ResearchImprovementCandidate] = field(
        default_factory=list
    )
    cache_status: str = "miss"
    deterministic_validation_status: str = "not_run"
    execution_backend: str = ""
    provenance_class: str = "live_run"
    provenance_message: str = ""
    data_gaps: list[str] = field(default_factory=list)
    evidence_pack_audit: EvidencePackAudit | None = None
    input_sufficiency: InputSufficiencyScore | None = None
    usage_summary: ResearchUsageSummary | None = None
    gap_decomposition: BenchmarkGapDecomposition | None = None
    deepseek_execution_status: str = "not_started"
    sol_execution_status: str = "not_started"
    adjudication_status: str = "not_run"
    attempted_stage_count: int = 0
    completed_stage_count: int = 0
    deepseek_completed_stage_count: int = 0
    sol_completed_stage_count: int = 0
    execution_failure_reason: str = ""


@dataclass(frozen=True)
class PeerDefinition:
    ticker: str
    rationale: str
    relationship: str = "Operating peer"


@dataclass(frozen=True)
class PeerUniverse:
    ticker: str
    status: str
    sector_template: str
    provenance: str
    effective_date: str
    peers: list[PeerDefinition] = field(default_factory=list)
    key_metrics: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class EventWindowReaction:
    ticker: str
    event_id: str
    event_date: str | None
    event_timestamp: str | None
    anchor_date: str | None
    prior_close: float | None
    source: str
    status: str
    reason: str = ""
    confidence: str = "High"
    currency: str = "USD"
    benchmark_ticker: str | None = None
    sector_benchmark_ticker: str | None = None
    beta: float | None = None
    volume_ratio: float | None = None
    raw_returns: dict[str, float | None] = field(default_factory=dict)
    market_relative_returns: dict[str, float | None] = field(default_factory=dict)
    sector_relative_returns: dict[str, float | None] = field(default_factory=dict)
    beta_adjusted_returns: dict[str, float | None] = field(default_factory=dict)
    path_min_20d_pct: float | None = None
    path_max_20d_pct: float | None = None
    corporate_action_adjusted: bool = True


@dataclass
class PeerObservation:
    peer_ticker: str
    universe_ticker: str
    evidence_status: str
    failure_status: str | None = None
    failure_reason: str | None = None
    sympathy_reactions: list[EventWindowReaction] = field(default_factory=list)
    own_event_reactions: list[EventWindowReaction] = field(default_factory=list)


@dataclass
class EvidenceItem:
    evidence_id: str
    claim_id: str
    ticker: str
    stance: str
    statement: str
    source_tier: int
    source_type: str
    materiality: int
    citation: Citation | None = None
    observed_at: str | None = None
    unresolved: bool = False
    promotion_eligible: bool = False
    source_origin_group: str = ""
    syndication_fingerprint: str = ""
    licensing_status: str = "Unknown"


@dataclass
class EvidenceClaim:
    claim_id: str
    idea_id: str
    text: str
    status: str
    supporting_evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    strongest_counter: str | None = None


@dataclass
class EvidenceLedger:
    claims: list[EvidenceClaim] = field(default_factory=list)
    items: list[EvidenceItem] = field(default_factory=list)
    strongest_counter_thesis: str = "Counter-thesis not established — diligence gap."
    unresolved_material_contradictions: int = 0


@dataclass
class DataQualityIssue:
    code: str
    severity: str
    message: str
    field: str | None = None
    provider: str | None = None


@dataclass
class DataQualityReport:
    score: int
    status: str
    primary_source_coverage_pct: float
    point_in_time_complete: bool
    official_consensus_available: bool
    issues: list[DataQualityIssue] = field(default_factory=list)


@dataclass
class ManagementPromise:
    promise_id: str
    statement: str
    metric: str | None
    period_end: str | None
    low: float | None
    high: float | None
    outcome: float | None
    status: str
    citation: Citation | None = None


@dataclass
class TranscriptComparison:
    status: str
    current_period: str | None = None
    previous_period: str | None = None
    new_priorities: list[str] = field(default_factory=list)
    removed_priorities: list[str] = field(default_factory=list)
    evasive_qa_flags: list[str] = field(default_factory=list)
    repeated_promises: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    current_sentiment_score: float | None = None
    previous_sentiment_score: float | None = None
    sentiment_shift: float | None = None
    current_uncertainty_score: float | None = None
    previous_uncertainty_score: float | None = None
    uncertainty_shift: float | None = None
    current_evasion_score: float | None = None
    previous_evasion_score: float | None = None
    evasion_shift: float | None = None
    current_specificity_score: float | None = None
    previous_specificity_score: float | None = None
    specificity_shift: float | None = None
    tone_shift_summary: str | None = None


@dataclass
class ManagementCredibility:
    status: str
    score: float | None
    promises_total: int
    promises_resolved: int
    promises_kept: int
    promises_missed: int
    promises: list[ManagementPromise] = field(default_factory=list)
    transcript_comparison: TranscriptComparison | None = None
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ManagementDocument:
    document_id: str
    ticker: str
    source_type: str
    provider: str
    title: str
    url: str | None
    event_date: str | None
    fiscal_period: str | None
    source_tier: int
    observed_at: str
    entitlement_status: str = "available"
    raw_payload_policy: str = "normalized_excerpt_only"
    excerpt: str = ""


@dataclass
class TranscriptTurn:
    turn_id: str
    document_id: str
    speaker: str
    role: str | None
    section: str
    text: str
    turn_index: int
    sentiment: str | None = None
    sentiment_label: str | None = None
    sentiment_score: float | None = None
    sentiment_confidence: str | None = None
    sentiment_source: str | None = None
    positive_terms: list[str] = field(default_factory=list)
    negative_terms: list[str] = field(default_factory=list)
    uncertainty_terms: list[str] = field(default_factory=list)
    evasion_terms: list[str] = field(default_factory=list)
    specificity_score: float | None = None


@dataclass
class ManagementClaim:
    claim_id: str
    ticker: str
    document_id: str
    claim_type: str
    statement: str
    source_type: str
    source_tier: int
    event_date: str | None
    citation: Citation
    speaker: str | None = None
    metric: str | None = None
    period_end: str | None = None
    value: float | None = None
    low: float | None = None
    high: float | None = None
    unit: str | None = None
    currency: str | None = None
    direction: str = "neutral"
    machine_readable: bool = False
    status: str = "Unverified"
    sentiment_label: str | None = None
    sentiment_score: float | None = None
    sentiment_confidence: str | None = None
    specificity_score: float | None = None
    uncertainty_terms: list[str] = field(default_factory=list)
    evasion_terms: list[str] = field(default_factory=list)


@dataclass
class MeetingEvent:
    event_id: str
    ticker: str
    document_id: str
    event_type: str
    description: str
    event_date: str | None
    citation: Citation
    source_tier: int
    status: str = "Detected"


@dataclass
class ManagementCrossCheck:
    check_id: str
    claim_id: str
    ticker: str
    status: str
    check_type: str
    summary: str
    source_type: str
    source_tier: int
    materiality: int
    citation: Citation | None = None


@dataclass
class ManagementSourcePackage:
    ticker: str
    status: str
    documents: list[ManagementDocument] = field(default_factory=list)
    transcript_turns: list[TranscriptTurn] = field(default_factory=list)
    claims: list[ManagementClaim] = field(default_factory=list)
    meeting_events: list[MeetingEvent] = field(default_factory=list)
    cross_checks: list[ManagementCrossCheck] = field(default_factory=list)
    provider_statuses: list[ProviderStatus] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class RunManifest:
    run_id: str
    generated_at: str
    app_version: str
    parser_versions: dict[str, str]
    source_urls: list[str]
    assumptions: list[str]
    data_gaps: list[str]
    retrieval_times: dict[str, str] = field(default_factory=dict)
    source_plan_summary: dict[str, Any] = field(default_factory=dict)
    llm_extraction_summary: dict[str, Any] = field(default_factory=dict)
    research_profile_summary: dict[str, Any] = field(default_factory=dict)
    effective_history_summary: dict[str, Any] = field(default_factory=dict)
    research_execution_policy: dict[str, Any] = field(default_factory=dict)
    data_access_policy: dict[str, Any] = field(default_factory=dict)
    source_policy_fingerprint: str = ""
    admitted_evidence_hash: str = ""
    evidence_graph_version: str = ""
    model_role_assignments: list[dict[str, Any]] = field(default_factory=list)
    independent_research_qa_summary: dict[str, Any] = field(default_factory=dict)
    research_requests: list[dict[str, Any]] = field(default_factory=list)
    candidate_discovery: list[dict[str, Any]] = field(default_factory=list)
    evidence_admission_log: list[dict[str, Any]] = field(default_factory=list)
    primary_audit_responses: list[dict[str, Any]] = field(default_factory=list)
    closure_audit_summary: dict[str, Any] = field(default_factory=dict)
    recursion_depth: int = 0
    estimated_research_cost_usd: float | None = None
    research_stopping_reason: str = ""
    research_integrity_version: str = "research-integrity-v1"


@dataclass
class ResearchExecutionPolicy:
    research_depth: str
    adaptive_macro: bool = True
    use_all_entitled_sources: bool = True
    retrieval_pass_limit: int = 1
    evidence_gap_loop_limit: int = 1
    specialist_roles: list[str] = field(default_factory=list)
    permitted_model_roles: list[str] = field(default_factory=list)
    max_model_spend_usd: float | None = None
    cache_bypass: bool = False
    legacy_budget_mode: str = ""
    max_investigation_cycles: int = 1
    focused_mapping_escalation_limit: int = 0
    stop_on_no_new_evidence: bool = True
    policy_version: str = "research-execution-policy-v1"


@dataclass
class DataAccessPolicy:
    enabled_source_families: list[str] = field(default_factory=list)
    disabled_source_families: list[str] = field(default_factory=list)
    provider_entitlements: dict[str, bool] = field(default_factory=dict)
    allowed_licensing_policies: list[str] = field(default_factory=list)
    market: str = "US-listed"
    jurisdiction: str = "US"
    data_cutoff: str = ""
    allow_cached_evidence: bool = True
    policy_version: str = "data-access-policy-v1"


@dataclass
class SourcePolicyFingerprint:
    fingerprint: str
    enabled_source_families: list[str] = field(default_factory=list)
    provider_entitlements: dict[str, bool] = field(default_factory=dict)
    licensing_rules: list[str] = field(default_factory=list)
    research_depth: str = "adaptive_ic"
    adaptive_macro_factors: list[str] = field(default_factory=list)
    market: str = "US-listed"
    jurisdiction: str = "US"
    data_cutoff: str = ""
    permitted_model_roles: list[str] = field(default_factory=list)
    prompt_version: str = ""
    source_registry_version: str = ""


@dataclass
class EvidenceNode:
    node_id: str
    node_type: str
    entity: str
    statement: str
    source: str = ""
    citation: str = ""
    publication_timestamp: str = ""
    event_timestamp: str = ""
    retrieval_timestamp: str = ""
    reporting_period: str = ""
    metric: str = ""
    value: float | str | None = None
    unit: str = ""
    currency: str = ""
    licensing_classification: str = ""
    provider: str = ""
    freshness: str = "Unknown"
    source_policy_admission_state: str = "excluded"
    no_lookahead_status: str = "Unknown"
    confidence: str = "Unknown"
    validation_state: str = "Unknown"


@dataclass
class EvidenceRelationship:
    relationship_id: str
    source_node_id: str
    target_node_id: str
    relationship_type: str
    confidence: str = "Unknown"


@dataclass
class EvidenceGraph:
    ticker: str
    graph_version: str
    evidence_hash: str
    source_policy_fingerprint: str
    nodes: list[EvidenceNode] = field(default_factory=list)
    relationships: list[EvidenceRelationship] = field(default_factory=list)
    admission_decisions: list[EvidenceAdmissionDecision] = field(default_factory=list)
    admitted_evidence_ids: list[str] = field(default_factory=list)
    excluded_evidence_ids: list[str] = field(default_factory=list)
    generated_at: str = ""


@dataclass
class CompanyMacroExposure:
    exposure_id: str
    factor: str
    materiality: int
    causal_relevance: int
    affected_business_driver: str
    time_horizon: str
    current_signal: str = "Unknown"
    direction: str = "uncertain"
    rationale: str = ""
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class AdaptiveMacroResearchPlan:
    ticker: str
    status: str
    baseline_factors: list[str] = field(default_factory=list)
    selected_exposures: list[CompanyMacroExposure] = field(default_factory=list)
    excluded_factors: list[str] = field(default_factory=list)
    selection_policy: str = ""
    company_evidence_required: bool = True
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ResearchRoleAssignment:
    role: str
    provider: str
    model: str
    status: str
    required: bool = True
    deterministic: bool = False
    message: str = ""


@dataclass
class PrimaryModelReadiness:
    status: str
    provider: str = ""
    model: str = ""
    credential_source: str = ""
    message: str = ""


@dataclass
class IndependentAuditReadiness:
    status: str
    provider: str = ""
    model: str = ""
    credential_source: str = ""
    optional: bool = True
    message: str = ""


@dataclass
class ResearchAiExecutionPlan:
    status: str
    assignments: list[ResearchRoleAssignment] = field(default_factory=list)
    primary_readiness: PrimaryModelReadiness | None = None
    independent_audit_readiness: IndependentAuditReadiness | None = None
    expected_model_calls: int = 0
    expected_retrieval_passes: int = 0
    expected_gap_loops: int = 0
    deterministic_fallback: bool = False


@dataclass
class ResearchSetupPreview:
    depth: str
    data_status: str
    source_count: int
    macro_status: str
    primary_ai_status: str
    independent_qa_status: str
    expected_execution: str
    expected_stages: list[str] = field(default_factory=list)
    excluded_evidence_count: int = 0
    estimated_provider_spend_usd: float | None = None


@dataclass
class ResearchHypothesis:
    hypothesis_id: str
    statement: str
    status: str = "open"
    confirming_evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    falsification_test: str = ""


@dataclass
class EvidenceGap:
    gap_id: str
    summary: str
    severity: str
    status: str = "open"
    evidence_request_ids: list[str] = field(default_factory=list)
    affected_claim_ids: list[str] = field(default_factory=list)
    resolution_evidence_ids: list[str] = field(default_factory=list)


@dataclass
class ResearchQaDimension:
    name: str
    score: int
    weight: float
    status: str
    explanation: str
    deductions: list[str] = field(default_factory=list)


@dataclass
class ResearchQaScore:
    total_score: int
    status: str
    dimensions: list[ResearchQaDimension] = field(default_factory=list)
    deductions: list[str] = field(default_factory=list)


@dataclass
class ModelExecutionRecord:
    role: str
    provider: str
    model: str
    status: str
    started_at: str = ""
    completed_at: str = ""
    latency_ms: int | None = None
    prompt_hash: str = ""
    evidence_hash: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    provider_cost_usd: float | None = None
    failure_class: str = ""
    message: str = ""


@dataclass
class ConclusionRevision:
    revision_id: str
    conclusion_area: str
    before: str
    after: str
    reason: str
    evidence_ids: list[str] = field(default_factory=list)
    materiality: str = "Medium"


@dataclass
class UnresolvedResearchRisk:
    risk_id: str
    summary: str
    severity: str
    affected_conclusions: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    confidence_impact: str = ""


@dataclass
class ResearchQaReport:
    ticker: str
    status: str
    score: ResearchQaScore
    primary_thesis_challenged: bool = False
    alternative_hypotheses_explored: int = 0
    unsupported_claims_removed: int = 0
    evidence_gaps_identified: int = 0
    evidence_gaps_closed: int = 0
    evidence_gaps_partially_resolved: int = 0
    evidence_gaps_unresolved: int = 0
    additional_validated_sources: int = 0
    market_capture_challenged: bool = False
    confidence_before: str = "Unknown"
    confidence_after: str = "Unknown"
    summary: str = ""


@dataclass
class IndependentResearchQaRun:
    run_id: str
    ticker: str
    status: str
    generated_at: str
    completed_at: str = ""
    evidence_hash: str = ""
    source_policy_fingerprint: str = ""
    execution_plan: ResearchAiExecutionPlan | None = None
    setup_preview: ResearchSetupPreview | None = None
    evidence_graph: EvidenceGraph | None = None
    hypotheses: list[ResearchHypothesis] = field(default_factory=list)
    audit_findings: list[AuditFinding] = field(default_factory=list)
    evidence_requests: list[EvidenceRequest] = field(default_factory=list)
    research_requests: list[ResearchRequest] = field(default_factory=list)
    candidate_discovery_items: list[CandidateDiscoveryItem] = field(default_factory=list)
    evidence_gaps: list[EvidenceGap] = field(default_factory=list)
    primary_audit_responses: list[PrimaryAuditResponse] = field(default_factory=list)
    audit_resolutions: list[AuditResolution] = field(default_factory=list)
    model_executions: list[ModelExecutionRecord] = field(default_factory=list)
    conclusion_revisions: list[ConclusionRevision] = field(default_factory=list)
    unresolved_risks: list[UnresolvedResearchRisk] = field(default_factory=list)
    qa_report: ResearchQaReport | None = None
    closure_result: ClosureAuditResult | None = None
    final_artifact: ResearchRoundArtifact | None = None
    recursion_depth: int = 0
    estimated_cost_usd: float | None = None
    stopping_reason: str = ""
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class CalibrationSlice:
    signal_type: str
    sample_size: int
    hit_rate_pct: float | None
    brier_score: float | None
    mean_expected_return_pct: float | None
    mean_realized_return_pct: float | None
    max_adverse_excursion_pct: float | None
    status: str = "Uncalibrated"
    outcomes_needed_for_calibration: int = 0
    rank_by_ev_allowed: bool = False
    next_action: str = ""


@dataclass
class CalibrationReadinessCheck:
    area: str
    status: str
    score: int
    summary: str
    evidence: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    next_action: str = ""
    stage_impact: str = ""


@dataclass
class CalibrationReport:
    status: str
    sample_size: int
    minimum_sample_size: int
    hit_rate_pct: float | None
    brier_score: float | None
    mean_absolute_error_pct: float | None
    slices: list[CalibrationSlice] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    post_mortem_count: int = 0
    post_mortem_coverage_pct: float | None = None
    complete_post_mortem_count: int = 0
    complete_post_mortem_coverage_pct: float | None = None
    incomplete_post_mortem_count: int = 0
    post_mortem_quality_status: str = "Unavailable"
    post_mortem_quality_gaps: list[str] = field(default_factory=list)
    evidence_valid_rate_pct: float | None = None
    recurring_lessons: list[str] = field(default_factory=list)
    recurring_failure_modes: list[str] = field(default_factory=list)
    process_improvement_actions: list[str] = field(default_factory=list)
    nearest_calibration_slice: str = ""
    nearest_calibration_sample_size: int = 0
    outcomes_needed_for_calibration: int = 0
    rank_by_ev_allowed: bool = False
    required_outcome_fields: list[str] = field(default_factory=list)
    calibration_actions: list[str] = field(default_factory=list)
    readiness_checks: list[CalibrationReadinessCheck] = field(default_factory=list)
    readiness_score: int = 0


@dataclass
class CalibrationStats:
    signal_family: str
    horizon: str
    status: str
    sample_size: int
    minimum_sample_size: int
    hit_rate_pct: float | None = None
    brier_score: float | None = None
    mean_expected_return_pct: float | None = None
    mean_realized_return_pct: float | None = None
    uncertainty_note: str = ""


@dataclass
class HistoricalReference:
    reference_id: str
    ticker: str
    idea_title: str
    signal_family: str
    direction: str
    stage: str
    event_date: str | None
    horizon: str
    similarity_score: int
    match_reasons: list[str] = field(default_factory=list)
    realized_return_pct: float | None = None
    abnormal_return_pct: float | None = None
    max_adverse_excursion_pct: float | None = None
    max_favorable_excursion_pct: float | None = None
    outcome_status: str = "unresolved"
    confidence: str = "Low"


@dataclass
class HistoricalReferenceSet:
    status: str
    scope: str
    sample_size: int
    minimum_sample_size: int
    references: list[HistoricalReference] = field(default_factory=list)
    hit_rate_pct: float | None = None
    mean_realized_return_pct: float | None = None
    data_gaps: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class HistoricalCompanyBaseline:
    business_model: str = "Unknown"
    segments: list[str] = field(default_factory=list)
    owned_capacity: str = "Unknown"
    contracted_capacity: str = "Unknown"
    capex: str = "Unknown"
    research_and_development: str = "Unknown"
    inventory: str = "Unknown"
    purchase_obligations: str = "Unknown"
    guarantees_and_leases: str = "Unknown"
    debt_and_liquidity: str = "Unknown"
    free_cash_flow: str = "Unknown"
    valuation: str = "Unknown"
    competition: str = "Unknown"
    management_changes: str = "Unknown"
    macro_regime: str = "Unknown"
    evidence_ids: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class HistoricalAnalogSignature:
    ticker: str
    driver: str
    industry: str
    revenue_growth_pct: float | None = None
    backlog_growth_pct: float | None = None
    capex_intensity_pct: float | None = None
    utilization_signal: str = "Unknown"
    fcf_conversion_pct: float | None = None
    leverage_signal: str = "Unknown"
    valuation_signal: str = "Unknown"
    competition_signal: str = "Unknown"
    macro_regime: str = "Unknown"
    evidence_ids: list[str] = field(default_factory=list)
    current_baseline: HistoricalCompanyBaseline = field(
        default_factory=HistoricalCompanyBaseline
    )


@dataclass
class AnalogSourceObservation:
    source_id: str
    provider: str
    title: str
    source_class: str
    source_origin_group: str
    published_at: str | None
    url: str
    evidence_id: str
    source_tier: int
    licensing_policy: str
    excerpt: str = ""
    quantitative: bool = False
    contemporaneous: bool = False
    citation: Citation | None = None


@dataclass
class AnalogSourceLink:
    source_id: str
    analog_id: str
    relevance_score: int
    status: str
    matched_entities: list[str] = field(default_factory=list)
    matched_mechanisms: list[str] = field(default_factory=list)
    episode_period_match: str = "Unknown"
    outcome_coverage: list[str] = field(default_factory=list)
    source_quality_score: int = 0
    rejection_reasons: list[str] = field(default_factory=list)
    source_title: str = ""
    source_provider: str = ""
    source_published_at: str | None = None
    rejection_summary: str = ""


@dataclass
class HistoricalAnalogWorkOrder:
    work_order_id: str
    analog_id: str
    episode: str
    status: str
    entities: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    period_start: str = ""
    period_end: str = ""
    mechanism: str = ""
    expected_outcomes: list[str] = field(default_factory=list)
    required_source_types: list[str] = field(default_factory=list)
    attempted_source_types: list[str] = field(default_factory=list)
    resolved_source_ids: list[str] = field(default_factory=list)
    blocker: str = ""
    next_action: str = ""


@dataclass
class HistoricalAnalogBrief:
    status: str
    episode: str
    lesson: str
    material_difference: str
    linked_monitor: str
    evidence_status: str
    analog_id: str = ""


@dataclass
class HistoricalOutcomePath:
    operating_outcome: str = "Unknown"
    financing_outcome: str = "Unknown"
    valuation_outcome: str = "Unknown"
    price_outcome: str = "Unknown"
    quantitative_verified: bool = False


@dataclass
class HistoricalAnalogCandidate:
    analog_id: str
    analog_class: str
    episode: str
    companies: list[str]
    selection_rationale: str
    search_terms: list[str] = field(default_factory=list)
    proposed_by: str = "deterministic"
    status: str = "Exploratory Candidate"
    mechanism: str = ""
    period_start: str = ""
    period_end: str = ""
    period_precision: str = "unknown"
    expected_outcomes: list[str] = field(default_factory=list)
    required_source_types: list[str] = field(default_factory=list)
    reference_tickers: list[str] = field(default_factory=list)


@dataclass
class HistoricalAnalogAssessment:
    analog_id: str
    analog_class: str
    status: str
    classification: str
    episode: str
    companies: list[str]
    selection_rationale: str
    similarity_score: int | None
    dimension_scores: dict[str, int] = field(default_factory=dict)
    similarity_band: str = "Unscored"
    source_relevance_score: int = 0
    source_links: list[AnalogSourceLink] = field(default_factory=list)
    rejected_sources: list[AnalogSourceLink] = field(default_factory=list)
    material_differences: list[str] = field(default_factory=list)
    outcome: HistoricalOutcomePath = field(default_factory=HistoricalOutcomePath)
    what_it_suggests: str = ""
    what_it_cannot_establish: str = ""
    lessons: list[str] = field(default_factory=list)
    confirm_kpis: list[str] = field(default_factory=list)
    reject_kpis: list[str] = field(default_factory=list)
    sources: list[AnalogSourceObservation] = field(default_factory=list)
    source_quality: str = "Exploratory"
    confidence: str = "Low"


@dataclass
class AnalogDimensionComparison:
    dimension: str
    current_value: str
    historical_value: str
    score: int
    assessment: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class HistoricalEpisodeDossier:
    analog_id: str
    status: str
    episode: str
    start_date: str
    end_date: str
    companies: list[str] = field(default_factory=list)
    securities: list[str] = field(default_factory=list)
    triggering_decisions: list[str] = field(default_factory=list)
    operating_outcome: str = "Unknown"
    financing_outcome: str = "Unknown"
    valuation_outcome: str = "Unknown"
    price_outcome: str = "Unknown"
    macro_context: str = "Unknown"
    competitive_context: str = "Unknown"
    dimensions: list[AnalogDimensionComparison] = field(default_factory=list)
    material_differences: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    confirm_kpis: list[str] = field(default_factory=list)
    reject_kpis: list[str] = field(default_factory=list)
    linked_monitor_rules: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    sources: list[AnalogSourceObservation] = field(default_factory=list)
    source_quality: str = "Exploratory"
    confidence: str = "Low"
    quantitative_verified: bool = False
    researched_at: str = ""
    cache_key: str = ""
    period_precision: str = "day"
    similarity_band: str = "Unscored"
    audited_similarity_score: int | None = None
    primary_provider: str = ""
    primary_model: str = ""
    secondary_auditor_provider: str = ""
    secondary_auditor_model: str = ""
    audit_status: str = "not_run"
    audit_decision: AnalogAuditDecision | None = None
    rejected_sources: list[AnalogSourceLink] = field(default_factory=list)
    hidden_risks: list[str] = field(default_factory=list)
    quality_scorecard: AnalogQualityScorecard | None = None
    generation_provenance: dict[str, str] = field(default_factory=dict)


@dataclass
class AnalogInvestigationStatus:
    code: str
    reason: str
    requested_count: int = 0
    attempted_count: int = 0
    completed_count: int = 0
    cache_hits: int = 0


@dataclass
class AnalogResearchManifest:
    ticker: str
    status: str
    profile_id: str
    prompt_version: str
    generated_at: str
    selected_analog_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    cache_hits: int = 0
    validation_failures: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    evidence_hash: str = ""
    source_registry_version: str = ""
    primary_variant_id: str = ""
    challenger_variant_id: str = ""


@dataclass
class HistoricalAnalogIntelligence:
    ticker: str
    status: str
    coverage_status: str
    signature: HistoricalAnalogSignature
    assessments: list[HistoricalAnalogAssessment] = field(default_factory=list)
    dossiers: list[HistoricalEpisodeDossier] = field(default_factory=list)
    top_lesson: str = ""
    llm_status: str = "not_selected_by_profile"
    investigation_status: AnalogInvestigationStatus | None = None
    research_manifest: AnalogResearchManifest | None = None
    data_gaps: list[str] = field(default_factory=list)
    primary_variant: AnalogResearchVariant | None = None
    challenger_variant: AnalogResearchVariant | None = None
    comparison: AnalogComparison | None = None
    work_orders: list[HistoricalAnalogWorkOrder] = field(default_factory=list)
    brief: HistoricalAnalogBrief | None = None


@dataclass
class ConvictionAuditItem:
    name: str
    status: str
    score: int
    why_it_matters: str
    evidence: str
    gaps: list[str] = field(default_factory=list)
    source_type: str = "deterministic"


@dataclass
class ConvictionAuditReport:
    status: str
    score: int
    summary: str
    items: list[ConvictionAuditItem] = field(default_factory=list)
    differentiators: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ThesisValidationCheck:
    channel: str
    status: str
    score: int
    evidence: str
    implication: str
    gaps: list[str] = field(default_factory=list)
    source_tier: int | None = None
    citation_count: int = 0


@dataclass
class EvidenceActionItem:
    channel: str
    priority: str
    action: str
    source: str
    why_it_matters: str
    blocks_high_conviction: bool = False


@dataclass
class EvidenceWorkOrderItem:
    work_id: str
    priority: str
    channel: str
    action: str
    source_type: str
    expected_output: str
    why_it_matters: str
    origin: str
    status: str = "Open"
    related_idea_ids: list[str] = field(default_factory=list)
    blocks_research_ready: bool = False
    blocks_high_conviction: bool = False
    cost_latency: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    falsification_tests: list[str] = field(default_factory=list)


@dataclass
class EvidenceWorkOrder:
    status: str
    summary: str
    items: list[EvidenceWorkOrderItem] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class EvidenceClosureAttempt:
    adapter: str
    status: str
    message: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class EvidenceClosureOutcome:
    work_id: str
    status: str
    summary: str
    attempted_adapters: list[EvidenceClosureAttempt] = field(default_factory=list)
    matched_evidence: list[str] = field(default_factory=list)
    contradiction_evidence: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    next_action: str = ""
    resolved_at: str = ""


@dataclass
class EvidenceClosureReport:
    ticker: str
    status: str
    summary: str
    outcomes: list[EvidenceClosureOutcome] = field(default_factory=list)
    resolved_count: int = 0
    contradicted_count: int = 0
    unavailable_count: int = 0
    licensed_or_manual_count: int = 0
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class CausalThesisNode:
    node_id: str
    node_type: str
    label: str
    status: str
    evidence: list[str] = field(default_factory=list)
    citation_ids: list[str] = field(default_factory=list)


@dataclass
class CausalThesisEdge:
    edge_id: str
    from_node: str
    to_node: str
    label: str
    score: int
    status: str
    explanation: str
    evidence: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    next_automatic_action: str = ""


@dataclass
class CausalThesisGraph:
    idea_id: str
    ticker: str
    status: str
    overall_score: int
    weakest_link: str
    summary: str
    nodes: list[CausalThesisNode] = field(default_factory=list)
    edges: list[CausalThesisEdge] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class MarketImpliedAssumption:
    name: str
    value: float | None
    unit: str
    provenance: str
    source: str
    editable: bool = True
    key: str = ""
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None


@dataclass
class MarketImpliedExpectation:
    metric: str
    status: str
    implied_value: float | None
    unit: str
    formula: str
    interpretation: str
    confidence: str
    required_inputs: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)


@dataclass
class MarketImpliedExpectations:
    ticker: str
    template: str
    status: str
    summary: str
    current_price: float | None = None
    currency: str = "USD"
    assumptions: list[MarketImpliedAssumption] = field(default_factory=list)
    expectations: list[MarketImpliedExpectation] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    price_source: str = ""
    price_as_of: str | None = None
    financial_basis: str = "Latest normalized filing facts"
    financial_period: str | None = None


@dataclass
class IcImpliedExpectationsSnapshot:
    ticker: str
    status: str
    template: str
    title: str
    current_price: float | None = None
    price_as_of: str | None = None
    currency: str = "USD"
    operating_base_label: str = ""
    operating_base_value: str = "Unknown"
    valuation_anchor_label: str = ""
    valuation_anchor_value: str = "Unknown"
    implied_variable_label: str = ""
    implied_variable_value: str = "Unknown"
    internal_base_label: str = ""
    internal_base_value: str = "Unknown"
    disagreement: str = "Unknown"
    confidence: str = "Unknown"
    weakest_input: str = ""
    interpretation: str = ""
    assumption_keys: list[str] = field(default_factory=list)


@dataclass
class DecisionDelta:
    ticker: str
    status: str
    summary: str
    compared_with_run_id: str | None = None
    strengthening_changes: list[str] = field(default_factory=list)
    weakening_changes: list[str] = field(default_factory=list)
    neutral_changes: list[str] = field(default_factory=list)
    observed_at: str = ""
    current_run_id: str | None = None
    current_completed_at: str | None = None
    current_result_type: str = "live"
    compared_with_completed_at: str | None = None
    compared_with_result_type: str | None = None
    compared_with_profile: str | None = None
    comparison_label: str = ""


@dataclass
class CatalystClockItem:
    catalyst_type: str
    label: str
    due_date: str | None
    status: str
    source: str
    related_idea_id: str | None = None
    related_idea_ids: list[str] = field(default_factory=list)
    decision_relevance: str = ""
    source_label: str = ""


@dataclass
class CatalystClock:
    ticker: str
    status: str
    summary: str
    items: list[CatalystClockItem] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class PrioritizedResearchAction:
    rank: int
    action_id: str
    title: str
    why_it_matters: str
    source_type: str
    status: str
    decision_impact: str
    auto_resolvable: bool = False
    blocks_stage: str = ""
    related_idea_ids: list[str] = field(default_factory=list)
    action_kind: str = "primary_source_validation"
    objective: str = ""
    materiality: str = "Medium"
    execution_mode: str = "automatic"
    event_id: str = ""
    related_work_order_ids: list[str] = field(default_factory=list)
    related_research_request_ids: list[str] = field(default_factory=list)
    related_claim_ids: list[str] = field(default_factory=list)
    related_finding_ids: list[str] = field(default_factory=list)
    required_source_families: list[str] = field(default_factory=list)
    preferred_connectors: list[str] = field(default_factory=list)
    completion_criteria: list[str] = field(default_factory=list)
    attempt_summary: str = ""
    blocking_reason: str = ""
    resolution_summary: str = ""
    origin_labels: list[str] = field(default_factory=list)
    semantic_key: str = ""


@dataclass
class EarningsSurpriseProxyItem:
    metric: str
    reporting_period: str | None
    event_label: str
    event_date: str | None
    actual: float | None
    estimate: float | None
    surprise_pct: float | None
    unit: str
    actual_source: str
    estimate_source: str
    estimate_as_of: str | None
    eligibility: str
    confidence: str
    interpretation: str
    drivers: dict[str, float | None] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)


@dataclass
class EarningsSurpriseProxy:
    ticker: str
    status: str
    headline: str
    methodology: str
    items: list[EarningsSurpriseProxyItem] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)
    revision_follow_through_available: bool = False


@dataclass
class MarketWindowPerformance:
    label: str
    sessions: int
    return_pct: float | None
    benchmark_return_pct: float | None
    relative_return_pct: float | None
    status: str = "available"


@dataclass
class RecentMarketContext:
    ticker: str
    status: str
    source: str
    summary: str
    price_as_of: str | None = None
    current_price: float | None = None
    adjusted: bool = False
    windows: list[MarketWindowPerformance] = field(default_factory=list)
    annualized_volatility_pct: float | None = None
    max_drawdown_pct: float | None = None
    recent_volume_ratio: float | None = None
    thesis_implications: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class ModelAssumption:
    assumption_id: str
    name: str
    case: str
    value: float | None
    unit: str
    provenance: str
    source: str
    formula: str = ""
    editable: bool = True


@dataclass
class ModelLineItem:
    statement: str
    metric: str
    period: str
    value: float | None
    unit: str
    source: str
    formula: str = "Direct source field"
    provenance: str = "source"
    confidence: str = "Medium"


@dataclass
class CompanyModelCase:
    name: str
    revenue: float | None = None
    operating_margin_pct: float | None = None
    net_income: float | None = None
    free_cash_flow: float | None = None
    fair_value: float | None = None
    assumptions: list[str] = field(default_factory=list)


@dataclass
class ModelSensitivityPoint:
    row_label: str
    column_label: str
    value: float | None


@dataclass
class CompanyModelWorkspace:
    ticker: str
    status: str
    summary: str
    currency: str
    historicals: list[ModelLineItem] = field(default_factory=list)
    assumptions: list[ModelAssumption] = field(default_factory=list)
    cases: list[CompanyModelCase] = field(default_factory=list)
    sensitivity: list[ModelSensitivityPoint] = field(default_factory=list)
    segment_rows: list[ModelLineItem] = field(default_factory=list)
    debt_rows: list[ModelLineItem] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchModeDefinition:
    mode_id: str
    label: str
    purpose: str
    required_metrics: list[str] = field(default_factory=list)
    required_source_types: list[str] = field(default_factory=list)
    acceptance_tests: list[str] = field(default_factory=list)
    falsification_tests: list[str] = field(default_factory=list)


@dataclass
class ResearchModeResult:
    mode_id: str
    label: str
    status: str
    score: int
    summary: str
    available_metrics: list[str] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)
    evidence_sources: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    recommended: bool = False


@dataclass
class ResearchModeSuite:
    ticker: str
    status: str
    recommended_mode_ids: list[str] = field(default_factory=list)
    modes: list[ResearchModeResult] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchProfile:
    profile_id: str
    label: str
    description: str
    quarter_depth: int
    annual_depth: int
    call_depth: int
    anomaly_limit: int | None
    adaptive_deepening: bool = False
    event_scoped: bool = False


@dataclass(frozen=True)
class MetricInterpretationPolicy:
    metric_key: str
    driver_family: str
    default_polarity: str
    interpretation: str
    constructive_mechanisms: list[str] = field(default_factory=list)
    adverse_mechanisms: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    valuation_effects: list[str] = field(default_factory=list)
    credit_effects: list[str] = field(default_factory=list)
    falsification_tests: list[str] = field(default_factory=list)


@dataclass
class CausalHypothesis:
    hypothesis_id: str
    side: str
    mechanism: str
    status: str = "Unvalidated"
    evidence_ids: list[str] = field(default_factory=list)
    contradicting_evidence_ids: list[str] = field(default_factory=list)
    financial_effects: list[str] = field(default_factory=list)
    next_action: str = ""


@dataclass
class MetricChangeAssessment:
    event_id: str
    metric_name: str
    event_label: str
    observed_change: str
    polarity: str
    driver_family: str
    interpretation: str
    constructive_hypothesis: CausalHypothesis | None = None
    adverse_hypothesis: CausalHypothesis | None = None
    historical_trend: str = "Unknown"
    evidence_labels: list[str] = field(default_factory=list)
    next_automatic_action: str = ""
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class HistoricalResearchPack:
    ticker: str
    profile_id: str
    status: str
    requested_quarters: int
    requested_annual_reports: int
    requested_calls: int
    discovered_quarters: int = 0
    discovered_annual_reports: int = 0
    discovered_calls: int = 0
    analyzed_quarters: int = 0
    analyzed_annual_reports: int = 0
    analyzed_calls: int = 0
    selected_event_ids: list[str] = field(default_factory=list)
    adaptive_deepening_reasons: list[str] = field(default_factory=list)
    filing_accessions: list[str] = field(default_factory=list)
    call_document_ids: list[str] = field(default_factory=list)
    trend_summaries: list[str] = field(default_factory=list)
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class PromotionSourceCheck:
    source_id: str
    provider: str
    source_tier: int
    origin_group: str
    syndication_fingerprint: str
    publication_time: str | None
    claim_match: bool
    period_match: bool
    citation_complete: bool
    eligible: bool
    reason: str


@dataclass
class PromotionEvidenceBundle:
    idea_id: str
    status: str
    primary_adapter_attempted: bool
    primary_unavailable_reason: str
    eligible_tier3_sources: list[PromotionSourceCheck] = field(default_factory=list)
    ineligible_sources: list[PromotionSourceCheck] = field(default_factory=list)
    independent_origin_count: int = 0
    tier1_contradiction: bool = False
    quantitative_bridge_supported: bool = False
    substituted_gate: str | None = None
    data_gaps: list[str] = field(default_factory=list)


@dataclass
class PromotionGateDecision:
    idea_id: str
    status: str
    label: str
    eligible: bool
    substituted_gate: str | None = None
    score_cap: int | None = None
    checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)


@dataclass
class SemanticCorroborationDraft:
    idea_id: str
    claim: str
    source_ids: list[str] = field(default_factory=list)
    same_entity: bool = False
    same_event: bool = False
    same_period: bool = False
    status: str = "Provisional"
    reason: str = ""


@dataclass
class ThesisValidationReport:
    status: str
    score: int
    summary: str
    top_idea_id: str | None
    top_idea_title: str
    checks: list[ThesisValidationCheck] = field(default_factory=list)
    strongest_supports: list[str] = field(default_factory=list)
    strongest_contradictions: list[str] = field(default_factory=list)
    required_next_evidence: list[str] = field(default_factory=list)
    next_evidence_actions: list[EvidenceActionItem] = field(default_factory=list)


@dataclass
class ProfilingStep:
    name: str
    duration_ms: float
    started_at_ms: float
    ended_at_ms: float


@dataclass
class ProfilingReport:
    status: str
    total_ms: float = 0.0
    steps: list[ProfilingStep] = field(default_factory=list)
    bottlenecks: list[str] = field(default_factory=list)
    treatments: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
