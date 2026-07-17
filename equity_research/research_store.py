from __future__ import annotations

import json
import hashlib
import sqlite3
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import config
from .models import (
    AlertRecord,
    ConsensusPackage,
    Citation,
    EarningsSurprise,
    EarningsSurpriseProxy,
    DailySnapshotStatus,
    EstimatePoint,
    EvidenceLedger,
    EvidenceClosureReport,
    EntityResolution,
    EventWindowReaction,
    ExternalEvidence,
    ExternalEvidenceBundle,
    FinancialCoverage,
    CausalBridge,
    CausalThesisGraph,
    CompanyModelWorkspace,
    MarketImpliedExpectations,
    RecentMarketContext,
    ResearchModeSuite,
    LlmProviderProfile,
    ManagementDocument,
    ManagementSourcePackage,
    NewsClaim,
    NewsSourceObservation,
    PeerUniverse,
    PrimarySourceObservation,
    PriceProviderStatus,
    ProviderComparison,
    ProviderObservation,
    ProviderStatus,
    RecommendationConsensus,
    RevisionWindow,
    SourceCorroborationResult,
    TargetConsensus,
    TranscriptTurn,
    WisburgResearchLens,
    WisburgSnapshotDelta,
    WatchlistStatus,
)


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class ResearchStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.RESEARCH_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, factory=_ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            # WAL mode persists at the database level. Setting it once avoids a
            # filesystem synchronization round trip on every short-lived query.
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS consensus_snapshots (
                    ticker TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    target_aggregate REAL,
                    target_mean REAL,
                    target_median REAL,
                    target_high REAL,
                    target_low REAL,
                    analyst_count INTEGER,
                    current_price REAL,
                    provider_timestamp TEXT,
                    target_kind TEXT NOT NULL DEFAULT 'mean',
                    target_label TEXT NOT NULL DEFAULT 'Mean target',
                    PRIMARY KEY (ticker, as_of, provider)
                );
                CREATE TABLE IF NOT EXISTS estimate_snapshots (
                    ticker TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    period_type TEXT NOT NULL,
                    average REAL,
                    high REAL,
                    low REAL,
                    analyst_count INTEGER,
                    currency TEXT NOT NULL,
                    period_precision TEXT NOT NULL DEFAULT 'day',
                    revisions_up INTEGER,
                    revisions_down INTEGER,
                    PRIMARY KEY (ticker, as_of, provider, metric, period_end, period_type)
                );
                CREATE TABLE IF NOT EXISTS recommendation_snapshots (
                    ticker TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    strong_buy INTEGER NOT NULL,
                    buy INTEGER NOT NULL,
                    hold INTEGER NOT NULL,
                    sell INTEGER NOT NULL,
                    strong_sell INTEGER NOT NULL,
                    consensus_label TEXT,
                    PRIMARY KEY (ticker, as_of, provider)
                );
                CREATE TABLE IF NOT EXISTS earnings_surprises (
                    ticker TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    actual_eps REAL,
                    estimated_eps REAL,
                    surprise_pct REAL,
                    PRIMARY KEY (ticker, period_end, provider)
                );
                CREATE TABLE IF NOT EXISTS watchlists (
                    list_name TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    added_at TEXT NOT NULL,
                    last_snapshot_at TEXT,
                    PRIMARY KEY (list_name, ticker)
                );
                CREATE TABLE IF NOT EXISTS provider_health (
                    provider TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_package_cache (
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    cache_date TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, provider, cache_date)
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    alert_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    severity INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'unread',
                    created_at TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    fiscal_period TEXT
                );
                CREATE TABLE IF NOT EXISTS idea_records (
                    idea_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_observations (
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    field TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_as_of TEXT,
                    value_numeric REAL,
                    value_text TEXT,
                    currency TEXT,
                    analyst_count INTEGER,
                    entitlement_status TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    official INTEGER NOT NULL,
                    confidence TEXT NOT NULL,
                    PRIMARY KEY (ticker, provider, field, observed_at)
                );
                CREATE TABLE IF NOT EXISTS research_runs (
                    run_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_ledgers (
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, ticker)
                );
                CREATE TABLE IF NOT EXISTS thesis_checks (
                    idea_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    criterion TEXT NOT NULL,
                    metric TEXT,
                    operator TEXT,
                    confirm_threshold REAL,
                    break_threshold REAL,
                    confirm_value TEXT,
                    break_value TEXT,
                    deadline TEXT,
                    source_field TEXT,
                    status TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (idea_id, criterion)
                );
                CREATE TABLE IF NOT EXISTS event_signals (
                    signal_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    event_date TEXT,
                    direction TEXT NOT NULL,
                    expected_return_pct REAL,
                    predicted_success_probability REAL,
                    observed_at TEXT NOT NULL,
                    realized_return_pct REAL,
                    abnormal_return_pct REAL,
                    max_adverse_excursion_pct REAL,
                    horizon_days INTEGER NOT NULL DEFAULT 20,
                    outcome_as_of TEXT
                );
                CREATE TABLE IF NOT EXISTS entity_coverage (
                    ticker TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    entity_json TEXT NOT NULL,
                    coverage_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, run_id)
                );
                CREATE TABLE IF NOT EXISTS peer_universes (
                    ticker TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, effective_date)
                );
                CREATE TABLE IF NOT EXISTS event_reactions (
                    ticker TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_date TEXT,
                    price_source TEXT NOT NULL,
                    return_window TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, event_id, event_date, price_source, return_window)
                );
                CREATE TABLE IF NOT EXISTS price_bars (
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    price_date TEXT NOT NULL,
                    close REAL NOT NULL,
                    volume REAL,
                    adjusted INTEGER NOT NULL DEFAULT 0,
                    official INTEGER NOT NULL DEFAULT 1,
                    observed_at TEXT NOT NULL,
                    source_url TEXT,
                    PRIMARY KEY (ticker, provider, price_date)
                );
                CREATE TABLE IF NOT EXISTS price_provider_statuses (
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT NOT NULL,
                    official INTEGER NOT NULL DEFAULT 1,
                    adjusted INTEGER NOT NULL DEFAULT 0,
                    source_url TEXT,
                    PRIMARY KEY (ticker, provider, observed_at)
                );
                CREATE TABLE IF NOT EXISTS macro_observations (
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    series_id TEXT NOT NULL,
                    source_as_of TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    value_numeric REAL,
                    unit TEXT,
                    frequency TEXT,
                    release_date TEXT,
                    vintage_date TEXT,
                    direction TEXT NOT NULL,
                    source_tier INTEGER NOT NULL,
                    official INTEGER NOT NULL,
                    confidence TEXT NOT NULL,
                    lookahead_safe INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    citation_json TEXT,
                    PRIMARY KEY (ticker, provider, series_id, source_as_of, observed_at)
                );
                CREATE TABLE IF NOT EXISTS external_research_excerpts (
                    excerpt_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    category TEXT NOT NULL,
                    report_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_as_of TEXT,
                    observed_at TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    original_excerpt TEXT NOT NULL,
                    translated_summary TEXT NOT NULL,
                    generated_summary TEXT NOT NULL,
                    theme_tags_json TEXT NOT NULL,
                    citation_json TEXT,
                    source_tier INTEGER NOT NULL,
                    confidence TEXT NOT NULL,
                    licensing_policy TEXT NOT NULL,
                    mentions_target_or_rating INTEGER NOT NULL,
                    non_consensus_label TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wisburg_lens_cache (
                    ticker TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, observed_at)
                );
                CREATE TABLE IF NOT EXISTS wisburg_coverage_audits (
                    ticker TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, observed_at)
                );
                CREATE TABLE IF NOT EXISTS wisburg_reports (
                    ticker TEXT NOT NULL,
                    report_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    published_at TEXT,
                    detail_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, report_key, observed_at)
                );
                CREATE TABLE IF NOT EXISTS wisburg_structured_claims (
                    ticker TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    report_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_as_of TEXT,
                    claim_type TEXT NOT NULL,
                    corroboration_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, claim_id, observed_at)
                );
                CREATE TABLE IF NOT EXISTS wisburg_revisions (
                    ticker TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    report_key TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    source_as_of TEXT,
                    revision_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, revision_id, observed_at)
                );
                CREATE TABLE IF NOT EXISTS wisburg_research_tasks (
                    ticker TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, task_id, observed_at)
                );
                CREATE TABLE IF NOT EXISTS wisburg_snapshot_deltas (
                    ticker TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, observed_at)
                );
                CREATE TABLE IF NOT EXISTS daily_snapshot_runs (
                    ticker TEXT NOT NULL,
                    run_date TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    overall_status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, run_date)
                );
                CREATE TABLE IF NOT EXISTS news_source_observations (
                    observation_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    source_family TEXT NOT NULL,
                    published_at TEXT,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_claims (
                    claim_id TEXT PRIMARY KEY,
                    observation_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    affected_driver TEXT NOT NULL,
                    status TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS primary_source_observations (
                    observation_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    driver_family TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_corroboration_results (
                    result_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    driver_family TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS causal_bridges (
                    bridge_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    idea_id TEXT,
                    driver_family TEXT NOT NULL,
                    status TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_artifacts (
                    ticker TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, run_id, artifact_type)
                );
                CREATE TABLE IF NOT EXISTS market_implied_assumptions (
                    ticker TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS validated_claims (
                    ticker TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, run_id, claim_id)
                );
                CREATE TABLE IF NOT EXISTS source_plans (
                    ticker TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, run_id)
                );
                CREATE TABLE IF NOT EXISTS global_peer_coverage (
                    ticker TEXT NOT NULL,
                    peer_ticker TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, peer_ticker, run_id)
                );
                CREATE TABLE IF NOT EXISTS peer_metric_readthroughs (
                    ticker TEXT NOT NULL,
                    idea_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, idea_id, run_id)
                );
                CREATE TABLE IF NOT EXISTS llm_research_manifests (
                    ticker TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (ticker, run_id)
                );
                CREATE TABLE IF NOT EXISTS idea_versions (
                    idea_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    run_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    signal_family TEXT NOT NULL,
                    horizon TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (idea_id, version),
                    UNIQUE (idea_id, run_id)
                );
                CREATE TABLE IF NOT EXISTS scenario_forecasts (
                    idea_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    case_name TEXT NOT NULL,
                    probability REAL NOT NULL,
                    probability_source TEXT NOT NULL,
                    exit_value REAL,
                    net_return_pct REAL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (idea_id, version, case_name),
                    FOREIGN KEY (idea_id, version) REFERENCES idea_versions(idea_id, version)
                );
                CREATE TABLE IF NOT EXISTS realized_outcomes (
                    idea_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    horizon TEXT NOT NULL,
                    realized_return_pct REAL,
                    max_adverse_excursion_pct REAL,
                    max_favorable_excursion_pct REAL,
                    thesis_outcome TEXT,
                    closure_reason TEXT,
                    evidence_valid TEXT,
                    what_worked TEXT,
                    what_failed TEXT,
                    lessons TEXT,
                    next_process_change TEXT,
                    observed_at TEXT NOT NULL,
                    PRIMARY KEY (idea_id, version, horizon),
                    FOREIGN KEY (idea_id, version) REFERENCES idea_versions(idea_id, version)
                );
                CREATE TABLE IF NOT EXISTS management_documents (
                    document_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    ticker TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT,
                    event_date TEXT,
                    fiscal_period TEXT,
                    source_tier INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    entitlement_status TEXT NOT NULL,
                    raw_payload_policy TEXT NOT NULL,
                    excerpt TEXT
                );
                CREATE TABLE IF NOT EXISTS transcript_turns (
                    turn_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    role TEXT,
                    section TEXT NOT NULL,
                    text TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    sentiment TEXT,
                    sentiment_label TEXT,
                    sentiment_score REAL,
                    sentiment_confidence TEXT,
                    sentiment_source TEXT,
                    positive_terms TEXT,
                    negative_terms TEXT,
                    uncertainty_terms TEXT,
                    evasion_terms TEXT,
                    specificity_score REAL,
                    FOREIGN KEY (document_id) REFERENCES management_documents(document_id)
                );
                CREATE TABLE IF NOT EXISTS management_claims (
                    claim_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    ticker TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    claim_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES management_documents(document_id)
                );
                CREATE TABLE IF NOT EXISTS meeting_events (
                    event_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    ticker TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (document_id) REFERENCES management_documents(document_id)
                );
                CREATE TABLE IF NOT EXISTS management_cross_checks (
                    check_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    ticker TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    check_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY (claim_id) REFERENCES management_claims(claim_id)
                );
                CREATE TABLE IF NOT EXISTS management_promise_outcomes (
                    promise_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS management_package_state (
                    ticker TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS llm_provider_profiles (
                    profile_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    provider_preset TEXT NOT NULL,
                    model TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    role_eligibility TEXT NOT NULL,
                    secret_ref TEXT NOT NULL,
                    key_configured INTEGER NOT NULL DEFAULT 0,
                    last_test_status TEXT NOT NULL DEFAULT 'not_tested',
                    last_test_message TEXT NOT NULL DEFAULT '',
                    last_test_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS llm_selection (
                    selection_id TEXT PRIMARY KEY,
                    primary_profile_id TEXT,
                    secondary_profile_id TEXT,
                    enable_secondary INTEGER NOT NULL DEFAULT 1,
                    secondary_min_stage TEXT NOT NULL DEFAULT 'Research-Ready',
                    language_policy TEXT NOT NULL DEFAULT 'bilingual_audit',
                    updated_at TEXT NOT NULL
                );
                """
            )
            _ensure_column(db, "consensus_snapshots", "target_aggregate", "REAL")
            _ensure_column(db, "consensus_snapshots", "target_kind", "TEXT NOT NULL DEFAULT 'mean'")
            _ensure_column(db, "consensus_snapshots", "target_label", "TEXT NOT NULL DEFAULT 'Mean target'")
            _ensure_column(db, "estimate_snapshots", "period_precision", "TEXT NOT NULL DEFAULT 'day'")
            _ensure_column(db, "estimate_snapshots", "revisions_up", "INTEGER")
            _ensure_column(db, "estimate_snapshots", "revisions_down", "INTEGER")
            _ensure_column(db, "event_signals", "horizon_days", "INTEGER NOT NULL DEFAULT 20")
            _ensure_column(db, "event_signals", "outcome_as_of", "TEXT")
            _ensure_column(db, "event_signals", "max_favorable_excursion_pct", "REAL")
            _ensure_column(db, "event_signals", "probability_source", "TEXT NOT NULL DEFAULT 'illustrative_default'")
            _ensure_column(db, "event_signals", "stage", "TEXT NOT NULL DEFAULT 'Candidate'")
            _ensure_column(db, "event_signals", "horizon_label", "TEXT")
            _ensure_column(db, "realized_outcomes", "evidence_valid", "TEXT")
            _ensure_column(db, "realized_outcomes", "what_worked", "TEXT")
            _ensure_column(db, "realized_outcomes", "what_failed", "TEXT")
            _ensure_column(db, "realized_outcomes", "lessons", "TEXT")
            _ensure_column(db, "realized_outcomes", "next_process_change", "TEXT")
            _ensure_column(db, "transcript_turns", "sentiment_label", "TEXT")
            _ensure_column(db, "transcript_turns", "sentiment_score", "REAL")
            _ensure_column(db, "transcript_turns", "sentiment_confidence", "TEXT")
            _ensure_column(db, "transcript_turns", "sentiment_source", "TEXT")
            _ensure_column(db, "transcript_turns", "positive_terms", "TEXT")
            _ensure_column(db, "transcript_turns", "negative_terms", "TEXT")
            _ensure_column(db, "transcript_turns", "uncertainty_terms", "TEXT")
            _ensure_column(db, "transcript_turns", "evasion_terms", "TEXT")
            _ensure_column(db, "transcript_turns", "specificity_score", "REAL")
            _ensure_column(db, "llm_provider_profiles", "role_eligibility", "TEXT NOT NULL DEFAULT 'primary_secondary'")
            _ensure_column(db, "llm_provider_profiles", "secret_ref", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(db, "llm_provider_profiles", "key_configured", "INTEGER NOT NULL DEFAULT 0")
            _ensure_column(db, "llm_provider_profiles", "last_test_status", "TEXT NOT NULL DEFAULT 'not_tested'")
            _ensure_column(db, "llm_provider_profiles", "last_test_message", "TEXT NOT NULL DEFAULT ''")
            _ensure_column(db, "llm_provider_profiles", "last_test_at", "TEXT")
            _ensure_column(db, "thesis_checks", "confirm_value", "TEXT")
            _ensure_column(db, "thesis_checks", "break_value", "TEXT")
            db.execute(
                "UPDATE idea_records SET status='legacy_candidate' "
                "WHERE status NOT IN ('Candidate','Research-Ready','High-Conviction','Investable','legacy_candidate')"
            )

    def save_llm_profile(self, profile: LlmProviderProfile) -> LlmProviderProfile:
        now = _utc_now()
        created_at = profile.created_at or now
        updated_at = now
        secret_ref = profile.secret_ref or f"LLM_PROFILE_API_KEY::{profile.profile_id}"
        saved = LlmProviderProfile(
            profile_id=profile.profile_id,
            display_name=profile.display_name,
            provider_preset=profile.provider_preset,
            model=profile.model,
            base_url=profile.base_url,
            role_eligibility=profile.role_eligibility or "primary_secondary",
            created_at=created_at,
            updated_at=updated_at,
            key_configured=profile.key_configured,
            secret_ref=secret_ref,
            last_test_status=profile.last_test_status,
            last_test_message=profile.last_test_message,
            last_test_at=profile.last_test_at,
        )
        with self.connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO llm_provider_profiles
                (profile_id, display_name, provider_preset, model, base_url, role_eligibility,
                 secret_ref, key_configured, last_test_status, last_test_message, last_test_at,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saved.profile_id, saved.display_name, saved.provider_preset, saved.model,
                    saved.base_url, saved.role_eligibility, saved.secret_ref,
                    int(saved.key_configured), saved.last_test_status, saved.last_test_message,
                    saved.last_test_at, saved.created_at, saved.updated_at,
                ),
            )
        return saved

    def list_llm_profiles(self) -> list[LlmProviderProfile]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM llm_provider_profiles ORDER BY updated_at DESC, display_name ASC"
            ).fetchall()
        return [_llm_profile_from_row(row) for row in rows]

    def get_llm_profile(self, profile_id: str | None) -> LlmProviderProfile | None:
        if not profile_id:
            return None
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM llm_provider_profiles WHERE profile_id = ?",
                (profile_id,),
            ).fetchone()
        return _llm_profile_from_row(row) if row else None

    def delete_llm_profile(self, profile_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "DELETE FROM llm_provider_profiles WHERE profile_id = ?",
                (profile_id,),
            )
            db.execute(
                """
                UPDATE llm_selection
                SET primary_profile_id = CASE WHEN primary_profile_id = ? THEN NULL ELSE primary_profile_id END,
                    secondary_profile_id = CASE WHEN secondary_profile_id = ? THEN NULL ELSE secondary_profile_id END,
                    updated_at = ?
                WHERE selection_id = 'default'
                """,
                (profile_id, profile_id, _utc_now()),
            )
            return cursor.rowcount > 0

    def update_llm_profile_test_status(
        self,
        profile_id: str,
        status: str,
        message: str,
        key_configured: bool | None = None,
    ) -> None:
        assignments = "last_test_status = ?, last_test_message = ?, last_test_at = ?, updated_at = ?"
        params: list[object] = [status, message, _utc_now(), _utc_now()]
        if key_configured is not None:
            assignments += ", key_configured = ?"
            params.append(int(key_configured))
        params.append(profile_id)
        with self.connect() as db:
            db.execute(f"UPDATE llm_provider_profiles SET {assignments} WHERE profile_id = ?", params)

    def save_llm_selection(
        self,
        primary_profile_id: str | None,
        secondary_profile_id: str | None,
        enable_secondary: bool,
        secondary_min_stage: str,
        language_policy: str,
    ) -> dict:
        payload = {
            "primary_profile_id": primary_profile_id or "",
            "secondary_profile_id": secondary_profile_id or "",
            "enable_secondary": bool(enable_secondary),
            "secondary_min_stage": secondary_min_stage or "Research-Ready",
            "language_policy": language_policy or "bilingual_audit",
            "updated_at": _utc_now(),
        }
        with self.connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO llm_selection
                (selection_id, primary_profile_id, secondary_profile_id, enable_secondary,
                 secondary_min_stage, language_policy, updated_at)
                VALUES ('default', ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["primary_profile_id"], payload["secondary_profile_id"],
                    int(payload["enable_secondary"]), payload["secondary_min_stage"],
                    payload["language_policy"], payload["updated_at"],
                ),
            )
        return payload

    def get_llm_selection(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM llm_selection WHERE selection_id = 'default'").fetchone()
        if not row:
            return {
                "primary_profile_id": config.LLM_PRIMARY_PROFILE_ID,
                "secondary_profile_id": config.LLM_SECONDARY_PROFILE_ID,
                "enable_secondary": config.ENABLE_SECONDARY_LLM_REVIEW,
                "secondary_min_stage": config.SECONDARY_LLM_MIN_STAGE,
                "language_policy": config.LLM_LANGUAGE_POLICY,
                "updated_at": "",
            }
        return {
            "primary_profile_id": row["primary_profile_id"] or "",
            "secondary_profile_id": row["secondary_profile_id"] or "",
            "enable_secondary": bool(row["enable_secondary"]),
            "secondary_min_stage": row["secondary_min_stage"],
            "language_policy": row["language_policy"],
            "updated_at": row["updated_at"],
        }

    def save_consensus_package(self, package: ConsensusPackage) -> None:
        with self.connect() as db:
            if package.target:
                target = package.target
                provider = target.source or package.provider
                db.execute(
                    """
                    INSERT OR REPLACE INTO consensus_snapshots
                    (ticker, as_of, provider, currency, target_aggregate, target_mean,
                     target_median, target_high, target_low, analyst_count, current_price,
                     provider_timestamp, target_kind, target_label)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target.ticker.upper(), target.as_of, provider, target.currency,
                        target.target_aggregate, target.target_mean, target.target_median, target.target_high,
                        target.target_low, target.analyst_count, target.current_price,
                        target.provider_timestamp, target.target_kind, target.target_label,
                    ),
                )
            if package.recommendations:
                rec = package.recommendations
                provider = rec.source or package.provider
                db.execute(
                    """
                    INSERT OR REPLACE INTO recommendation_snapshots
                    (ticker, as_of, provider, strong_buy, buy, hold, sell, strong_sell, consensus_label)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec.ticker.upper(), rec.as_of, provider, rec.strong_buy, rec.buy,
                        rec.hold, rec.sell, rec.strong_sell, rec.consensus_label,
                    ),
                )
            for estimate in package.estimates:
                provider = estimate.source or package.provider
                db.execute(
                    """
                    INSERT OR REPLACE INTO estimate_snapshots
                    (ticker, as_of, provider, metric, period_end, period_type, average,
                     high, low, analyst_count, currency, period_precision, revisions_up,
                     revisions_down)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        estimate.ticker.upper(), estimate.as_of, provider, estimate.metric,
                        estimate.period_end, estimate.period_type, estimate.average,
                        estimate.high, estimate.low, estimate.analyst_count, estimate.currency,
                        estimate.period_precision, estimate.revisions_up, estimate.revisions_down,
                    ),
                )
            for surprise in package.surprises:
                provider = surprise.source or package.provider
                db.execute(
                    """
                    INSERT OR REPLACE INTO earnings_surprises
                    (ticker, period_end, provider, actual_eps, estimated_eps, surprise_pct)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        surprise.ticker.upper(), surprise.period_end, provider,
                        surprise.actual_eps, surprise.estimated_eps, surprise.surprise_pct,
                    ),
                )
            for observation in package.observations:
                db.execute(
                    """INSERT OR REPLACE INTO provider_observations
                    (ticker,provider,field,observed_at,source_as_of,value_numeric,value_text,
                     currency,analyst_count,entitlement_status,provenance,official,confidence)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        observation.ticker.upper(), observation.provider, observation.field,
                        observation.observed_at, observation.source_as_of,
                        observation.value_numeric, observation.value_text, observation.currency,
                        observation.analyst_count, observation.entitlement_status,
                        observation.provenance, int(observation.official), observation.confidence,
                    ),
                )

    def latest_target(self, ticker: str, provider: str | None = None) -> TargetConsensus | None:
        where = "ticker = ?"
        params: list[object] = [ticker.upper()]
        if provider:
            where += " AND provider = ?"
            params.append(provider)
        with self.connect() as db:
            row = db.execute(
                f"SELECT * FROM consensus_snapshots WHERE {where} ORDER BY as_of DESC LIMIT 1",
                params,
            ).fetchone()
        return _target_from_row(row) if row else None

    def target_at_or_before(
        self,
        ticker: str,
        as_of: str,
        provider: str | None = None,
    ) -> TargetConsensus | None:
        provider_clause = " AND provider = ?" if provider else ""
        params: list[object] = [ticker.upper(), as_of]
        if provider:
            params.append(provider)
        with self.connect() as db:
            row = db.execute(
                f"""
                SELECT * FROM consensus_snapshots
                WHERE ticker = ? AND as_of <= ?{provider_clause}
                ORDER BY as_of DESC LIMIT 1
                """,
                params,
            ).fetchone()
        return _target_from_row(row) if row else None

    def previous_target(
        self,
        ticker: str,
        before: str,
        provider: str | None = None,
    ) -> TargetConsensus | None:
        provider_clause = " AND provider = ?" if provider else ""
        params: list[object] = [ticker.upper(), before]
        if provider:
            params.append(provider)
        with self.connect() as db:
            row = db.execute(
                f"""SELECT * FROM consensus_snapshots
                WHERE ticker=? AND as_of<?{provider_clause}
                ORDER BY as_of DESC LIMIT 1""",
                params,
            ).fetchone()
        return _target_from_row(row) if row else None

    def target_snapshot_bounds(
        self,
        ticker: str,
        provider: str | None = None,
    ) -> tuple[TargetConsensus | None, TargetConsensus | None]:
        provider_clause = " AND provider=?" if provider else ""
        params: list[object] = [ticker.upper()]
        if provider:
            params.append(provider)
        with self.connect() as db:
            first = db.execute(
                f"""SELECT * FROM consensus_snapshots
                WHERE ticker=?{provider_clause}
                ORDER BY as_of ASC LIMIT 1""",
                params,
            ).fetchone()
            latest = db.execute(
                f"""SELECT * FROM consensus_snapshots
                WHERE ticker=?{provider_clause}
                ORDER BY as_of DESC LIMIT 1""",
                params,
            ).fetchone()
        return (
            _target_from_row(first) if first else None,
            _target_from_row(latest) if latest else None,
        )

    def revisions(
        self,
        ticker: str,
        windows: tuple[int, ...] = (7, 30, 90),
        provider: str | None = None,
    ) -> list[RevisionWindow]:
        latest = self.latest_target(ticker, provider)
        if not latest:
            return []
        results: list[RevisionWindow] = []
        earliest, _ = self.target_snapshot_bounds(ticker, provider)
        latest_date = _parse_date(latest.as_of)
        metric_name = f"price_target_{latest.target_kind}"
        latest_value = _target_primary_value(latest)
        provider_name = provider or latest.source
        for days in windows:
            cutoff = (latest_date - timedelta(days=days)).isoformat()
            start = self.target_at_or_before(
                ticker,
                cutoff,
                provider,
            )
            start_value = _target_primary_value(start) if start else None
            status, reason = _revision_status_and_reason(
                start_value=start_value,
                end_value=latest_value,
                start_date=start.as_of if start else None,
                end_date=latest.as_of,
                cutoff=cutoff,
                earliest_date=earliest.as_of if earliest else None,
                provider=provider_name,
                metric=metric_name,
            )
            results.append(
                RevisionWindow(
                    metric=metric_name,
                    window_days=days,
                    start_date=start.as_of if start else None,
                    end_date=latest.as_of,
                    start_value=start_value,
                    end_value=latest_value,
                    change_pct=_pct_change(start_value, latest_value),
                    provider=provider_name,
                    status=status,
                    reason=reason,
                    source_kind="local_snapshot",
                    official=False if provider_name and "unofficial" in provider_name.lower() else True,
                )
            )
        return results

    def estimate_revision(
        self,
        ticker: str,
        metric: str,
        period_end: str,
        start: str,
        end: str,
        provider: str | None = None,
    ) -> RevisionWindow:
        provider_clause = " AND provider=?" if provider else ""
        start_params: list[object] = [ticker.upper(), metric, period_end, start]
        end_params: list[object] = [ticker.upper(), metric, period_end, end]
        if provider:
            start_params.append(provider)
            end_params.append(provider)
        with self.connect() as db:
            start_row = db.execute(
                f"""SELECT * FROM estimate_snapshots
                WHERE ticker=? AND metric=? AND period_end=? AND as_of<=?{provider_clause}
                ORDER BY as_of DESC LIMIT 1""",
                start_params,
            ).fetchone()
            end_row = db.execute(
                f"""SELECT * FROM estimate_snapshots
                WHERE ticker=? AND metric=? AND period_end=? AND as_of<=?{provider_clause}
                ORDER BY as_of DESC LIMIT 1""",
                end_params,
            ).fetchone()
        start_value = start_row["average"] if start_row else None
        end_value = end_row["average"] if end_row else None
        provider_name = provider or (end_row["provider"] if end_row else start_row["provider"] if start_row else "")
        earliest_date = self._earliest_estimate_snapshot_date(ticker, metric, period_end, provider)
        status, reason = _revision_status_and_reason(
            start_value=start_value,
            end_value=end_value,
            start_date=start_row["as_of"] if start_row else None,
            end_date=end_row["as_of"] if end_row else None,
            cutoff=start,
            earliest_date=earliest_date,
            provider=provider_name,
            metric=metric,
        )
        return RevisionWindow(
            metric=metric,
            window_days=max(0, (_parse_date(end) - _parse_date(start)).days),
            start_date=start_row["as_of"] if start_row else None,
            end_date=end_row["as_of"] if end_row else None,
            start_value=start_value,
            end_value=end_value,
            change_pct=_pct_change(start_value, end_value),
            provider=provider_name,
            status=status,
            reason=reason,
            source_kind="local_snapshot",
            official=False if provider_name and "unofficial" in provider_name.lower() else True,
        )

    def _earliest_estimate_snapshot_date(
        self,
        ticker: str,
        metric: str,
        period_end: str,
        provider: str | None = None,
    ) -> str | None:
        provider_clause = " AND provider=?" if provider else ""
        params: list[object] = [ticker.upper(), metric, period_end]
        if provider:
            params.append(provider)
        with self.connect() as db:
            row = db.execute(
                f"""SELECT as_of FROM estimate_snapshots
                WHERE ticker=? AND metric=? AND period_end=?{provider_clause}
                ORDER BY as_of ASC LIMIT 1""",
                params,
            ).fetchone()
        return row["as_of"] if row else None

    def estimate_at_or_before(
        self,
        ticker: str,
        metric: str,
        period_end: str,
        as_of: str,
        provider: str | None = None,
    ) -> EstimatePoint | None:
        provider_clause = " AND provider=?" if provider else ""
        params: list[object] = [ticker.upper(), metric, period_end, as_of]
        if provider:
            params.append(provider)
        with self.connect() as db:
            row = db.execute(
                f"""SELECT * FROM estimate_snapshots
                WHERE ticker=? AND metric=? AND period_end=? AND as_of<=?{provider_clause}
                ORDER BY as_of DESC LIMIT 1""",
                params,
            ).fetchone()
        return _estimate_from_row(row) if row else None

    def latest_estimates(self, ticker: str) -> list[EstimatePoint]:
        with self.connect() as db:
            rows = db.execute(
                """
                SELECT e.* FROM estimate_snapshots e
                JOIN (
                    SELECT ticker, metric, period_end, period_type, MAX(as_of) AS max_as_of
                    FROM estimate_snapshots WHERE ticker=?
                    GROUP BY ticker, metric, period_end, period_type
                ) latest
                ON e.ticker=latest.ticker AND e.metric=latest.metric
                AND e.period_end=latest.period_end AND e.period_type=latest.period_type
                AND e.as_of=latest.max_as_of
                ORDER BY e.period_end, e.metric
                """,
                (ticker.upper(),),
            ).fetchall()
        return [_estimate_from_row(row) for row in rows]

    def latest_recommendations(self, ticker: str) -> RecommendationConsensus | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM recommendation_snapshots WHERE ticker=? ORDER BY as_of DESC LIMIT 1",
                (ticker.upper(),),
            ).fetchone()
        if not row:
            return None
        return RecommendationConsensus(
            ticker=row["ticker"], as_of=row["as_of"], strong_buy=row["strong_buy"],
            buy=row["buy"], hold=row["hold"], sell=row["sell"],
            strong_sell=row["strong_sell"], consensus_label=row["consensus_label"],
            source=row["provider"],
        )

    def recommendation_before(
        self,
        ticker: str,
        before: str,
        provider: str | None = None,
    ) -> RecommendationConsensus | None:
        provider_clause = " AND provider=?" if provider else ""
        params: list[object] = [ticker.upper(), before]
        if provider:
            params.append(provider)
        with self.connect() as db:
            row = db.execute(
                f"""SELECT * FROM recommendation_snapshots
                WHERE ticker=? AND as_of<?{provider_clause}
                ORDER BY as_of DESC LIMIT 1""",
                params,
            ).fetchone()
        if not row:
            return None
        return RecommendationConsensus(
            ticker=row["ticker"], as_of=row["as_of"], strong_buy=row["strong_buy"],
            buy=row["buy"], hold=row["hold"], sell=row["sell"],
            strong_sell=row["strong_sell"], consensus_label=row["consensus_label"],
            source=row["provider"],
        )

    def surprises(self, ticker: str, limit: int = 8) -> list[EarningsSurprise]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM earnings_surprises WHERE ticker=? ORDER BY period_end DESC LIMIT ?",
                (ticker.upper(), limit),
            ).fetchall()
        return [
            EarningsSurprise(
                ticker=row["ticker"], period_end=row["period_end"],
                actual_eps=row["actual_eps"], estimated_eps=row["estimated_eps"],
                surprise_pct=row["surprise_pct"], source=row["provider"],
            )
            for row in rows
        ]

    def add_watchlist(self, ticker: str, list_name: str = "default") -> None:
        now = _utc_now()
        with self.connect() as db:
            db.execute(
                """INSERT INTO watchlists (list_name,ticker,active,added_at)
                VALUES (?,?,1,?)
                ON CONFLICT(list_name,ticker) DO UPDATE SET active=1""",
                (list_name, ticker.upper(), now),
            )

    def remove_watchlist(self, ticker: str, list_name: str = "default") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE watchlists SET active=0 WHERE list_name=? AND ticker=?",
                (list_name, ticker.upper()),
            )

    def touch_watchlist(self, ticker: str, list_name: str = "default") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE watchlists SET last_snapshot_at=? WHERE list_name=? AND ticker=?",
                (_utc_now(), list_name, ticker.upper()),
            )

    def list_watchlist(self, list_name: str = "default") -> list[WatchlistStatus]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM watchlists WHERE list_name=? AND active=1 ORDER BY ticker",
                (list_name,),
            ).fetchall()
        return [
            WatchlistStatus(
                list_name=row["list_name"], ticker=row["ticker"],
                active=bool(row["active"]), last_snapshot_at=row["last_snapshot_at"],
            )
            for row in rows
        ]

    def set_provider_health(self, provider: str, status: str, message: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO provider_health VALUES (?,?,?,?)",
                (provider, status, _utc_now(), message[:1000]),
            )

    def list_provider_health(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT provider,status,checked_at,message FROM provider_health ORDER BY provider"
            ).fetchall()
        return [dict(row) for row in rows]

    def save_external_evidence(self, package: ExternalEvidenceBundle) -> None:
        macro_items = [item for item in package.evidence if item.source_type == "macro_factor"]
        if not macro_items:
            return
        with self.connect() as db:
            for item in macro_items:
                if not item.metric_name or not item.source_as_of:
                    continue
                db.execute(
                    """INSERT OR REPLACE INTO macro_observations
                    (ticker,provider,series_id,source_as_of,observed_at,title,value_numeric,
                     unit,frequency,release_date,vintage_date,direction,source_tier,official,
                     confidence,lookahead_safe,summary,tags_json,citation_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        package.ticker.upper(), item.provider, item.metric_name,
                        item.source_as_of, item.observed_at, item.title,
                        item.metric_value, item.unit, item.frequency,
                        item.release_date, item.vintage_date, item.direction,
                        item.source_tier, int(item.official), item.confidence,
                        int(item.lookahead_safe), item.summary,
                        json.dumps(item.tags), json.dumps(asdict(item.citation)) if item.citation else None,
                    ),
                )

    def save_wisburg_lens(self, lens: WisburgResearchLens) -> None:
        with self.connect() as db:
            for item in lens.excerpts:
                db.execute(
                    """INSERT OR REPLACE INTO external_research_excerpts
                    (excerpt_id,ticker,provider,category,report_id,title,source_as_of,observed_at,
                     source_language,original_excerpt,translated_summary,generated_summary,
                     theme_tags_json,citation_json,source_tier,confidence,licensing_policy,
                     mentions_target_or_rating,non_consensus_label)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item.excerpt_id,
                        item.ticker.upper(),
                        item.provider,
                        item.category,
                        item.report_id,
                        item.title,
                        item.source_as_of,
                        item.observed_at,
                        item.source_language,
                        item.original_excerpt,
                        item.translated_summary,
                        item.generated_summary,
                        json.dumps(item.theme_tags),
                        json.dumps(asdict(item.citation)) if item.citation else None,
                        item.source_tier,
                        item.confidence,
                        item.licensing_policy,
                        int(item.mentions_target_or_rating),
                        item.non_consensus_label,
                    ),
                )
            db.execute(
                """INSERT OR REPLACE INTO wisburg_lens_cache
                (ticker,observed_at,status,payload_json) VALUES (?,?,?,?)""",
                (
                    lens.ticker.upper(),
                    lens.observed_at,
                    lens.status,
                    json.dumps(asdict(lens), default=str),
                ),
            )
            if lens.coverage_audit:
                db.execute(
                    """INSERT OR REPLACE INTO wisburg_coverage_audits
                    (ticker,observed_at,status,payload_json) VALUES (?,?,?,?)""",
                    (
                        lens.ticker.upper(), lens.observed_at, lens.coverage_audit.status,
                        json.dumps(asdict(lens.coverage_audit), default=str),
                    ),
                )
            for report in lens.reports:
                db.execute(
                    """INSERT OR REPLACE INTO wisburg_reports
                    (ticker,report_key,observed_at,published_at,detail_status,payload_json)
                    VALUES (?,?,?,?,?,?)""",
                    (
                        lens.ticker.upper(), report.report_key, lens.observed_at,
                        report.published_at, report.detail_status,
                        json.dumps(asdict(report), default=str),
                    ),
                )
            for claim in lens.structured_claims:
                db.execute(
                    """INSERT OR REPLACE INTO wisburg_structured_claims
                    (ticker,claim_id,report_key,observed_at,source_as_of,claim_type,
                     corroboration_status,payload_json) VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        lens.ticker.upper(), claim.claim_id, claim.report_key,
                        lens.observed_at, claim.source_as_of, claim.claim_type,
                        claim.corroboration_status, json.dumps(asdict(claim), default=str),
                    ),
                )
            for revision in lens.revisions:
                db.execute(
                    """INSERT OR REPLACE INTO wisburg_revisions
                    (ticker,revision_id,report_key,observed_at,source_as_of,revision_type,payload_json)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        lens.ticker.upper(), revision.revision_id, revision.report_key,
                        lens.observed_at, revision.source_as_of, revision.revision_type,
                        json.dumps(asdict(revision), default=str),
                    ),
                )
            for task in lens.research_tasks:
                db.execute(
                    """INSERT OR REPLACE INTO wisburg_research_tasks
                    (ticker,task_id,claim_id,observed_at,status,payload_json)
                    VALUES (?,?,?,?,?,?)""",
                    (
                        lens.ticker.upper(), task.task_id, task.claim_id,
                        lens.observed_at, task.status,
                        json.dumps(asdict(task), default=str),
                    ),
                )

    def latest_wisburg_lens(self, ticker: str, before: str | None = None) -> dict | None:
        query = "SELECT payload_json FROM wisburg_lens_cache WHERE ticker=?"
        params: list[object] = [ticker.upper()]
        if before:
            query += " AND observed_at<?"
            params.append(before)
        query += " ORDER BY observed_at DESC LIMIT 1"
        with self.connect() as db:
            row = db.execute(query, params).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def wisburg_lens_on_date(self, ticker: str, snapshot_date: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM wisburg_lens_cache
                WHERE ticker=? AND substr(observed_at,1,10)=?
                ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(), snapshot_date[:10]),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_wisburg_delta(self, delta: WisburgSnapshotDelta) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO wisburg_snapshot_deltas
                (ticker,observed_at,status,payload_json) VALUES (?,?,?,?)""",
                (
                    delta.ticker.upper(),
                    delta.observed_at,
                    delta.status,
                    json.dumps(asdict(delta), default=str),
                ),
            )

    def latest_wisburg_delta(self, ticker: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM wisburg_snapshot_deltas
                WHERE ticker=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(),),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_daily_snapshot_status(self, status: DailySnapshotStatus) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO daily_snapshot_runs
                (ticker,run_date,observed_at,overall_status,payload_json)
                VALUES (?,?,?,?,?)""",
                (
                    status.ticker.upper(),
                    status.run_date,
                    status.observed_at,
                    status.overall_status,
                    json.dumps(asdict(status), default=str),
                ),
            )

    def latest_daily_snapshot_status(self, ticker: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM daily_snapshot_runs
                WHERE ticker=? ORDER BY run_date DESC, observed_at DESC LIMIT 1""",
                (ticker.upper(),),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def list_external_research_excerpts(self, ticker: str, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT * FROM external_research_excerpts
                WHERE ticker=? ORDER BY observed_at DESC, source_as_of DESC, title LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        return [_external_research_excerpt_from_row(row) for row in rows]

    def list_wisburg_themes(self, ticker: str) -> list[dict]:
        payload = self.latest_wisburg_lens(ticker) or {}
        return payload.get("themes", [])

    def list_wisburg_source_suggestions(self, ticker: str) -> list[dict]:
        payload = self.latest_wisburg_lens(ticker) or {}
        return payload.get("source_suggestions", [])

    def latest_wisburg_coverage(self, ticker: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM wisburg_coverage_audits
                WHERE ticker=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(),),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def list_wisburg_reports(self, ticker: str, limit: int = 100) -> list[dict]:
        return self._list_wisburg_payloads("wisburg_reports", ticker, limit)

    def list_wisburg_claims(self, ticker: str, limit: int = 100) -> list[dict]:
        return self._list_wisburg_payloads("wisburg_structured_claims", ticker, limit)

    def list_wisburg_revisions(self, ticker: str, limit: int = 100) -> list[dict]:
        return self._list_wisburg_payloads("wisburg_revisions", ticker, limit)

    def list_wisburg_research_tasks(self, ticker: str, limit: int = 100) -> list[dict]:
        return self._list_wisburg_payloads("wisburg_research_tasks", ticker, limit)

    def _list_wisburg_payloads(self, table: str, ticker: str, limit: int) -> list[dict]:
        allowed = {
            "wisburg_reports", "wisburg_structured_claims", "wisburg_revisions",
            "wisburg_research_tasks",
        }
        if table not in allowed:
            raise ValueError("Unsupported Wisburg table")
        with self.connect() as db:
            rows = db.execute(
                f"SELECT payload_json FROM {table} WHERE ticker=? ORDER BY observed_at DESC LIMIT ?",
                (ticker.upper(), limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_news_observation(self, observation: NewsSourceObservation, claim: NewsClaim | None = None) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO news_source_observations
                (observation_id,ticker,provider,source_family,published_at,observed_at,payload_json)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    observation.observation_id,
                    observation.ticker.upper(),
                    observation.provider,
                    observation.source_family,
                    observation.published_at,
                    observation.observed_at,
                    json.dumps(asdict(observation), default=str),
                ),
            )
            if claim:
                db.execute(
                    """INSERT OR REPLACE INTO news_claims
                    (claim_id,observation_id,ticker,event_type,affected_driver,status,observed_at,payload_json)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        claim.claim_id,
                        claim.observation_id,
                        claim.ticker.upper(),
                        claim.event_type,
                        claim.affected_driver,
                        claim.status,
                        claim.created_at or observation.observed_at,
                        json.dumps(asdict(claim), default=str),
                    ),
                )

    def save_news_claims(self, claims: list[NewsClaim]) -> None:
        with self.connect() as db:
            for claim in claims:
                db.execute(
                    """INSERT OR REPLACE INTO news_claims
                    (claim_id,observation_id,ticker,event_type,affected_driver,status,observed_at,payload_json)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        claim.claim_id,
                        claim.observation_id,
                        claim.ticker.upper(),
                        claim.event_type,
                        claim.affected_driver,
                        claim.status,
                        claim.created_at or _utc_now(),
                        json.dumps(asdict(claim), default=str),
                    ),
                )

    def latest_news_claims(self, ticker: str, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload_json FROM news_claims
                WHERE ticker=? ORDER BY observed_at DESC, claim_id LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def latest_news_observations(self, ticker: str, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload_json FROM news_source_observations
                WHERE ticker=? ORDER BY observed_at DESC, observation_id LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_primary_source_observations(self, observations: list[PrimarySourceObservation]) -> None:
        with self.connect() as db:
            for item in observations:
                db.execute(
                    """INSERT OR REPLACE INTO primary_source_observations
                    (observation_id,ticker,source_type,provider,driver_family,observed_at,payload_json)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        item.observation_id,
                        item.ticker.upper(),
                        item.source_type,
                        item.provider,
                        item.driver_family,
                        item.observed_at,
                        json.dumps(asdict(item), default=str),
                    ),
                )

    def latest_primary_source_observations(self, ticker: str, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload_json FROM primary_source_observations
                WHERE ticker=? ORDER BY observed_at DESC, observation_id LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_source_corroboration_results(self, results: list[SourceCorroborationResult]) -> None:
        with self.connect() as db:
            for item in results:
                db.execute(
                    """INSERT OR REPLACE INTO source_corroboration_results
                    (result_id,ticker,claim_id,status,driver_family,observed_at,payload_json)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        item.result_id,
                        item.ticker.upper(),
                        item.claim_id,
                        item.status,
                        item.driver_family,
                        item.observed_at or _utc_now(),
                        json.dumps(asdict(item), default=str),
                    ),
                )

    def latest_source_corroboration(self, ticker: str, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload_json FROM source_corroboration_results
                WHERE ticker=? ORDER BY observed_at DESC, result_id LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_causal_bridges(self, bridges: list[CausalBridge]) -> None:
        with self.connect() as db:
            for bridge in bridges:
                db.execute(
                    """INSERT OR REPLACE INTO causal_bridges
                    (bridge_id,ticker,idea_id,driver_family,status,observed_at,payload_json)
                    VALUES (?,?,?,?,?,?,?)""",
                    (
                        bridge.bridge_id,
                        bridge.ticker.upper(),
                        bridge.idea_id,
                        bridge.driver_family,
                        bridge.status,
                        bridge.observed_at or _utc_now(),
                        json.dumps(asdict(bridge), default=str),
                    ),
                )

    def latest_causal_bridges(self, ticker: str, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload_json FROM causal_bridges
                WHERE ticker=? ORDER BY observed_at DESC, bridge_id LIMIT ?""",
                (ticker.upper(), limit),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_decision_artifacts(
        self,
        run_id: str,
        ticker: str,
        evidence_closure: EvidenceClosureReport,
        causal_graphs: list[CausalThesisGraph],
        market_implied: MarketImpliedExpectations,
        company_model: CompanyModelWorkspace,
        research_modes: ResearchModeSuite,
        historical_research: object | None = None,
        metric_assessments: list | None = None,
        promotion_evidence: dict | None = None,
        playbook_portfolio: object | None = None,
        expectation_event_audits: list | None = None,
        earnings_surprise_proxy: EarningsSurpriseProxy | None = None,
        recent_market_context: RecentMarketContext | None = None,
    ) -> None:
        artifacts = {
            "evidence_closure": evidence_closure,
            "causal_thesis_graph": causal_graphs,
            "market_implied": market_implied,
            "company_model": company_model,
            "research_modes": research_modes,
            "historical_research": historical_research,
            "metric_assessments": metric_assessments or [],
            "promotion_evidence": promotion_evidence or {},
            "playbook_portfolio": playbook_portfolio,
            "expectation_event_audits": expectation_event_audits or [],
            "earnings_surprise_proxy": earnings_surprise_proxy,
            "recent_market_context": recent_market_context,
        }
        with self.connect() as db:
            for artifact_type, payload in artifacts.items():
                if payload is None:
                    continue
                if isinstance(payload, list):
                    serialized = [asdict(item) for item in payload]
                elif isinstance(payload, dict):
                    serialized = {
                        key: asdict(value) if hasattr(value, "__dataclass_fields__") else value
                        for key, value in payload.items()
                    }
                else:
                    serialized = asdict(payload)
                db.execute(
                    """INSERT OR REPLACE INTO decision_artifacts
                    (ticker,run_id,artifact_type,observed_at,payload_json)
                    VALUES (?,?,?,?,?)""",
                    (
                        ticker.upper(), run_id, artifact_type, _utc_now(),
                        json.dumps(serialized, default=str),
                    ),
                )

    def latest_decision_artifact(self, ticker: str, artifact_type: str) -> dict | list | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM decision_artifacts
                WHERE ticker=? AND artifact_type=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(), artifact_type),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def list_macro_observations(
        self,
        ticker: str | None = None,
        latest_only: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        params: list[object] = []
        query = "SELECT * FROM macro_observations"
        if ticker:
            query += " WHERE ticker=?"
            params.append(ticker.upper())
        query += " ORDER BY observed_at DESC, provider, series_id LIMIT ?"
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        observations = [
            _macro_observation_from_row(row)
            for row in rows
        ]
        if not latest_only:
            return observations
        seen: set[tuple[str, str]] = set()
        latest: list[dict] = []
        for observation in observations:
            key = (observation["provider"], observation["series_id"])
            if key in seen:
                continue
            seen.add(key)
            latest.append(observation)
        return latest

    def cached_macro_evidence(
        self,
        ticker: str,
        provider: str,
        cache_date: date | None = None,
    ) -> ExternalEvidenceBundle | None:
        cache_date = cache_date or datetime.now(timezone.utc).date()
        with self.connect() as db:
            rows = db.execute(
                """SELECT * FROM macro_observations
                WHERE ticker=? AND provider=? AND substr(observed_at,1,10)=?
                ORDER BY observed_at DESC, series_id""",
                (ticker.upper(), provider, cache_date.isoformat()),
            ).fetchall()
        if not rows:
            return None
        seen: set[str] = set()
        evidence: list[ExternalEvidence] = []
        for row in rows:
            series_id = row["series_id"]
            if series_id in seen:
                continue
            seen.add(series_id)
            evidence.append(_external_evidence_from_macro_row(row))
        if not evidence:
            return None
        now = _utc_now()
        return ExternalEvidenceBundle(
            ticker.upper(),
            "Available",
            evidence,
            [
                ProviderStatus(
                    provider,
                    "Available",
                    True,
                    "cached",
                    now,
                    f"Using {len(evidence)} same-day cached macro observation(s).",
                )
            ],
            [],
        )

    def macro_health(self, ticker: str | None = None) -> dict:
        macro_provider_names = {
            "FRED/ALFRED macro",
            "BLS macro",
            "BEA macro",
            "Census macro",
            "Treasury macro",
            "OFR macro",
            "World Bank macro",
            "IMF macro",
        }
        health = [
            row for row in self.list_provider_health()
            if row["provider"] in macro_provider_names
        ]
        return {
            "ticker": ticker.upper() if ticker else None,
            "provider_health": health,
            "observations": self.list_macro_observations(ticker, latest_only=True),
        }

    def save_provider_package(self, package: ConsensusPackage) -> None:
        if package.status == "Unavailable":
            return
        observed_at = max(
            (item.observed_at for item in package.observations if item.observed_at),
            default=_utc_now(),
        )
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO provider_package_cache
                (ticker,provider,cache_date,observed_at,status,payload_json)
                VALUES (?,?,?,?,?,?)""",
                (
                    package.ticker.upper(), package.provider, date.today().isoformat(),
                    observed_at, package.status, json.dumps(asdict(package)),
                ),
            )

    def load_provider_package(
        self,
        ticker: str,
        provider: str,
        cache_date: str | None = None,
    ) -> ConsensusPackage | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM provider_package_cache
                WHERE ticker=? AND provider=? AND cache_date=?""",
                (ticker.upper(), provider, cache_date or date.today().isoformat()),
            ).fetchone()
        if not row:
            return None
        try:
            return _package_from_dict(json.loads(row["payload_json"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def list_provider_observations(
        self,
        ticker: str,
        field: str | None = None,
        latest_only: bool = False,
    ) -> list[ProviderObservation]:
        where = "ticker=?"
        params: list[object] = [ticker.upper()]
        if field:
            where += " AND field=?"
            params.append(field)
        query = f"SELECT * FROM provider_observations WHERE {where} ORDER BY observed_at DESC"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        observations = [_observation_from_row(row) for row in rows]
        if not latest_only:
            return observations
        seen: set[tuple[str, str]] = set()
        latest: list[ProviderObservation] = []
        for observation in observations:
            key = (observation.provider, observation.field)
            if key in seen:
                continue
            seen.add(key)
            latest.append(observation)
        return latest

    def observation_at_or_before(
        self,
        ticker: str,
        provider: str,
        field: str,
        observed_at: str,
    ) -> ProviderObservation | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT * FROM provider_observations
                WHERE ticker=? AND provider=? AND field=? AND observed_at<=?
                ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(), provider, field, observed_at),
            ).fetchone()
        return _observation_from_row(row) if row else None

    def save_research_run(self, ticker: str, manifest) -> None:
        payload = asdict(manifest)
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO research_runs VALUES (?,?,?,?)",
                (manifest.run_id, ticker.upper(), manifest.generated_at, json.dumps(payload)),
            )

    def save_evidence_ledger(self, run_id: str, ticker: str, ledger: EvidenceLedger) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO evidence_ledgers VALUES (?,?,?)",
                (run_id, ticker.upper(), json.dumps(asdict(ledger))),
            )

    def latest_evidence_payload(self, ticker: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT e.payload_json FROM evidence_ledgers e
                JOIN research_runs r ON r.run_id=e.run_id
                WHERE e.ticker=? ORDER BY r.generated_at DESC LIMIT 1""",
                (ticker.upper(),),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_entity_coverage(
        self,
        run_id: str,
        entity: EntityResolution,
        coverage: FinancialCoverage,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO entity_coverage
                (ticker,run_id,observed_at,entity_json,coverage_json) VALUES (?,?,?,?,?)""",
                (
                    entity.ticker.upper(), run_id, _utc_now(),
                    json.dumps(asdict(entity)), json.dumps(asdict(coverage)),
                ),
            )

    def latest_entity_coverage(self, ticker: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT entity_json,coverage_json,observed_at FROM entity_coverage
                WHERE ticker=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(),),
            ).fetchone()
        if not row:
            return None
        return {
            "entity_resolution": json.loads(row["entity_json"]),
            "financial_coverage": json.loads(row["coverage_json"]),
            "observed_at": row["observed_at"],
        }

    def save_peer_universe(self, universe: PeerUniverse) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO peer_universes
                (ticker,effective_date,observed_at,payload_json) VALUES (?,?,?,?)""",
                (
                    universe.ticker.upper(), universe.effective_date, _utc_now(),
                    json.dumps(asdict(universe)),
                ),
            )

    def latest_peer_universe(self, ticker: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM peer_universes WHERE ticker=?
                ORDER BY effective_date DESC LIMIT 1""",
                (ticker.upper(),),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_global_peer_coverage(self, run_id: str, ticker: str, coverage_by_peer: dict) -> None:
        observed_at = _utc_now()
        with self.connect() as db:
            for peer_ticker, coverage in coverage_by_peer.items():
                payload = asdict(coverage)
                db.execute(
                    """INSERT OR REPLACE INTO global_peer_coverage
                    (ticker,peer_ticker,run_id,observed_at,status,payload_json)
                    VALUES (?,?,?,?,?,?)""",
                    (
                        ticker.upper(), peer_ticker.upper(), run_id, observed_at,
                        payload.get("status", "Unknown"), json.dumps(payload),
                    ),
                )

    def latest_global_peer_coverage(self, ticker: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT payload_json FROM global_peer_coverage
                WHERE ticker=? ORDER BY observed_at DESC, peer_ticker ASC""",
                (ticker.upper(),),
            ).fetchall()
        seen: set[str] = set()
        payloads = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            peer = str(payload.get("ticker") or "").upper()
            if peer in seen:
                continue
            seen.add(peer)
            payloads.append(payload)
        return payloads

    def save_peer_metric_readthroughs(self, run_id: str, ticker: str, readthrough_by_idea: dict) -> None:
        observed_at = _utc_now()
        with self.connect() as db:
            for idea_id, rows in readthrough_by_idea.items():
                db.execute(
                    """INSERT OR REPLACE INTO peer_metric_readthroughs
                    (ticker,idea_id,run_id,observed_at,payload_json)
                    VALUES (?,?,?,?,?)""",
                    (
                        ticker.upper(), idea_id, run_id, observed_at,
                        json.dumps([asdict(item) for item in rows]),
                    ),
                )

    def latest_peer_metric_readthroughs(self, ticker: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT idea_id,payload_json FROM peer_metric_readthroughs
                WHERE ticker=? ORDER BY observed_at DESC""",
                (ticker.upper(),),
            ).fetchall()
        seen: set[str] = set()
        payloads = []
        for row in rows:
            idea_id = row["idea_id"]
            if idea_id in seen:
                continue
            seen.add(idea_id)
            payloads.append({"idea_id": idea_id, "readthroughs": json.loads(row["payload_json"])})
        return payloads

    def save_llm_research_manifest(self, run_id: str, ticker: str, manifest) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO llm_research_manifests
                (ticker,run_id,observed_at,payload_json) VALUES (?,?,?,?)""",
                (ticker.upper(), run_id, _utc_now(), json.dumps(asdict(manifest))),
            )

    def latest_llm_research_manifest(self, ticker: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM llm_research_manifests
                WHERE ticker=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(),),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def save_price_provider_status(self, status: PriceProviderStatus) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO price_provider_statuses
                (ticker,provider,observed_at,status,message,official,adjusted,source_url)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    status.ticker.upper(), status.provider, status.observed_at,
                    status.status, status.message, int(status.official),
                    int(status.adjusted), status.source_url,
                ),
            )

    def list_price_provider_statuses(self, ticker: str | None = None, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            if ticker:
                rows = db.execute(
                    """SELECT * FROM price_provider_statuses WHERE ticker=?
                    ORDER BY observed_at DESC LIMIT ?""",
                    (ticker.upper(), limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM price_provider_statuses ORDER BY observed_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            dict(row) | {"official": bool(row["official"]), "adjusted": bool(row["adjusted"])}
            for row in rows
        ]

    def save_price_bars(
        self,
        ticker: str,
        provider: str,
        rows: list[dict],
        adjusted: bool,
        official: bool,
        source_url: str | None = None,
    ) -> None:
        observed_at = _utc_now()
        with self.connect() as db:
            for row in rows:
                price_date = row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])
                close = row.get("close")
                if close is None:
                    continue
                db.execute(
                    """INSERT OR REPLACE INTO price_bars
                    (ticker,provider,price_date,close,volume,adjusted,official,observed_at,source_url)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        ticker.upper(), provider, price_date, float(close),
                        row.get("volume"), int(adjusted), int(official),
                        observed_at, source_url,
                    ),
                )

    def load_price_bars(self, ticker: str, cache_date: date | None = None) -> dict | None:
        cache_date = cache_date or datetime.now(timezone.utc).date()
        with self.connect() as db:
            provider_row = db.execute(
                """SELECT provider, MAX(observed_at) AS observed_at
                FROM price_bars WHERE ticker=? GROUP BY provider
                HAVING date(observed_at)=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker.upper(), cache_date.isoformat()),
            ).fetchone()
            if not provider_row:
                return None
            rows = db.execute(
                """SELECT price_date,close,volume,adjusted,official,source_url,observed_at
                FROM price_bars WHERE ticker=? AND provider=? ORDER BY price_date""",
                (ticker.upper(), provider_row["provider"]),
            ).fetchall()
        if not rows:
            return None
        first = rows[0]
        return {
            "ticker": ticker.upper(),
            "provider": provider_row["provider"],
            "observed_at": provider_row["observed_at"],
            "adjusted": bool(first["adjusted"]),
            "official": bool(first["official"]),
            "source_url": first["source_url"],
            "rows": [dict(row) for row in rows],
        }

    def save_event_reactions(self, reactions: list[EventWindowReaction]) -> None:
        observed_at = _utc_now()
        with self.connect() as db:
            for reaction in reactions:
                payload = json.dumps(asdict(reaction))
                windows = set(reaction.raw_returns) or {"status"}
                for window in windows:
                    db.execute(
                        """INSERT OR REPLACE INTO event_reactions
                        (ticker,event_id,event_date,price_source,return_window,observed_at,payload_json)
                        VALUES (?,?,?,?,?,?,?)""",
                        (
                            reaction.ticker.upper(), reaction.event_id, reaction.event_date,
                            reaction.source, window, observed_at, payload,
                        ),
                    )

    def list_event_reactions(self, ticker: str, event_id: str | None = None) -> list[dict]:
        query = "SELECT payload_json FROM event_reactions WHERE ticker=?"
        params: list[object] = [ticker.upper()]
        if event_id:
            query += " AND event_id=?"
            params.append(event_id)
        query += " ORDER BY observed_at DESC,return_window"
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        unique: dict[str, dict] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            unique.setdefault(payload["event_id"], payload)
        return list(unique.values())

    def save_management_sources(self, run_id: str, package: ManagementSourcePackage) -> None:
        checked_at = _utc_now()
        artifact_payload = {
            "documents": [_stable_artifact_payload(asdict(item)) for item in package.documents],
            "transcript_turns": [_stable_artifact_payload(asdict(item)) for item in package.transcript_turns],
            "claims": [_stable_artifact_payload(asdict(item)) for item in package.claims],
            "meeting_events": [_stable_artifact_payload(asdict(item)) for item in package.meeting_events],
            "cross_checks": [_stable_artifact_payload(asdict(item)) for item in package.cross_checks],
        }
        content_hash = hashlib.sha256(
            json.dumps(
                artifact_payload, sort_keys=True, separators=(",", ":"), default=str,
            ).encode("utf-8")
        ).hexdigest()
        with self.connect() as db:
            db.executemany(
                """INSERT OR REPLACE INTO provider_health
                (provider,status,checked_at,message) VALUES (?,?,?,?)""",
                [
                    (
                        status.provider, status.status, checked_at,
                        status.message or status.entitlement_status,
                    )
                    for status in package.provider_statuses
                ],
            )
            cached = db.execute(
                "SELECT content_hash FROM management_package_state WHERE ticker=?",
                (package.ticker.upper(),),
            ).fetchone()
            if cached and cached["content_hash"] == content_hash:
                # The normalized evidence is identical. Preserve its original
                # retrieval timestamps and provenance instead of rewriting it.
                db.execute(
                    "UPDATE management_package_state SET updated_at=? WHERE ticker=?",
                    (checked_at, package.ticker.upper()),
                )
                return
            db.executemany(
                """INSERT OR REPLACE INTO management_documents
                (document_id,run_id,ticker,source_type,provider,title,url,event_date,
                 fiscal_period,source_tier,observed_at,entitlement_status,
                 raw_payload_policy,excerpt)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        document.document_id, run_id, document.ticker.upper(),
                        document.source_type, document.provider, document.title,
                        document.url, document.event_date, document.fiscal_period,
                        document.source_tier, document.observed_at,
                        document.entitlement_status, document.raw_payload_policy,
                        document.excerpt,
                    )
                    for document in package.documents
                ],
            )
            db.executemany(
                """INSERT OR REPLACE INTO transcript_turns
                (turn_id,document_id,speaker,role,section,text,turn_index,sentiment,
                 sentiment_label,sentiment_score,sentiment_confidence,sentiment_source,
                 positive_terms,negative_terms,uncertainty_terms,evasion_terms,specificity_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        turn.turn_id, turn.document_id, turn.speaker, turn.role,
                        turn.section, turn.text, turn.turn_index, turn.sentiment,
                        turn.sentiment_label, turn.sentiment_score,
                        turn.sentiment_confidence, turn.sentiment_source,
                        json.dumps(turn.positive_terms),
                        json.dumps(turn.negative_terms),
                        json.dumps(turn.uncertainty_terms),
                        json.dumps(turn.evasion_terms),
                        turn.specificity_score,
                    )
                    for turn in package.transcript_turns
                ],
            )
            db.executemany(
                """INSERT OR REPLACE INTO management_claims
                (claim_id,run_id,ticker,document_id,claim_type,status,payload_json)
                VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        claim.claim_id, run_id, claim.ticker.upper(), claim.document_id,
                        claim.claim_type, claim.status, json.dumps(asdict(claim)),
                    )
                    for claim in package.claims
                ],
            )
            db.executemany(
                """INSERT OR REPLACE INTO meeting_events
                (event_id,run_id,ticker,document_id,event_type,status,payload_json)
                VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        event.event_id, run_id, event.ticker.upper(), event.document_id,
                        event.event_type, event.status, json.dumps(asdict(event)),
                    )
                    for event in package.meeting_events
                ],
            )
            db.executemany(
                """INSERT OR REPLACE INTO management_cross_checks
                (check_id,run_id,ticker,claim_id,status,check_type,payload_json)
                VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        check.check_id, run_id, check.ticker.upper(), check.claim_id,
                        check.status, check.check_type, json.dumps(asdict(check)),
                    )
                    for check in package.cross_checks
                ],
            )
            db.execute(
                """INSERT INTO management_package_state (ticker,content_hash,updated_at)
                VALUES (?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET
                content_hash=excluded.content_hash,updated_at=excluded.updated_at""",
                (package.ticker.upper(), content_hash, checked_at),
            )

    def latest_management_sources(self, ticker: str) -> dict:
        ticker = ticker.upper()
        with self.connect() as db:
            documents = db.execute(
                "SELECT * FROM management_documents WHERE ticker=? ORDER BY observed_at DESC,event_date DESC",
                (ticker,),
            ).fetchall()
            turns = db.execute(
                """SELECT t.* FROM transcript_turns t
                JOIN management_documents d ON d.document_id=t.document_id
                WHERE d.ticker=? ORDER BY d.observed_at DESC,t.document_id,t.turn_index""",
                (ticker,),
            ).fetchall()
            claims = db.execute(
                "SELECT payload_json FROM management_claims WHERE ticker=? ORDER BY claim_type,status",
                (ticker,),
            ).fetchall()
            meetings = db.execute(
                "SELECT payload_json FROM meeting_events WHERE ticker=? ORDER BY event_type,status",
                (ticker,),
            ).fetchall()
            checks = db.execute(
                "SELECT payload_json FROM management_cross_checks WHERE ticker=? ORDER BY status,check_type",
                (ticker,),
            ).fetchall()
        return {
            "ticker": ticker,
            "documents": [dict(row) for row in documents],
            "transcript_turns": [_turn_row_dict(row) for row in turns],
            "claims": [json.loads(row["payload_json"]) for row in claims],
            "meeting_events": [json.loads(row["payload_json"]) for row in meetings],
            "cross_checks": [json.loads(row["payload_json"]) for row in checks],
        }

    def cached_management_inputs(
        self, ticker: str,
    ) -> tuple[list[ManagementDocument], list[TranscriptTurn]]:
        """Return normalized cached source inputs without reusing derived claims."""
        payload = self.latest_management_sources(ticker)
        documents = [
            ManagementDocument(
                document_id=str(row["document_id"]),
                ticker=str(row["ticker"]),
                source_type=str(row["source_type"]),
                provider=str(row["provider"]),
                title=str(row["title"]),
                url=row.get("url"),
                event_date=row.get("event_date"),
                fiscal_period=row.get("fiscal_period"),
                source_tier=int(row["source_tier"]),
                observed_at=str(row["observed_at"]),
                entitlement_status=str(row.get("entitlement_status") or "available"),
                raw_payload_policy=str(row.get("raw_payload_policy") or "normalized_excerpt_only"),
                excerpt=str(row.get("excerpt") or ""),
            )
            for row in payload.get("documents", [])
        ]
        turns = [
            TranscriptTurn(
                turn_id=str(row["turn_id"]),
                document_id=str(row["document_id"]),
                speaker=str(row.get("speaker") or "Unknown"),
                role=row.get("role"),
                section=str(row.get("section") or "Unknown"),
                text=str(row.get("text") or ""),
                turn_index=int(row.get("turn_index") or 0),
                sentiment=row.get("sentiment"),
                sentiment_label=row.get("sentiment_label"),
                sentiment_score=row.get("sentiment_score"),
                sentiment_confidence=row.get("sentiment_confidence"),
                sentiment_source=row.get("sentiment_source"),
                positive_terms=list(row.get("positive_terms") or []),
                negative_terms=list(row.get("negative_terms") or []),
                uncertainty_terms=list(row.get("uncertainty_terms") or []),
                evasion_terms=list(row.get("evasion_terms") or []),
                specificity_score=row.get("specificity_score"),
            )
            for row in payload.get("transcript_turns", [])
        ]
        return documents, turns

    def save_validated_claims(self, run_id: str, ticker: str, claims: list) -> None:
        observed_at = _utc_now()
        with self.connect() as db:
            for claim in claims:
                db.execute(
                    """INSERT OR REPLACE INTO validated_claims
                    (ticker,run_id,claim_id,observed_at,status,payload_json)
                    VALUES (?,?,?,?,?,?)""",
                    (
                        ticker.upper(),
                        run_id,
                        claim.claim_id,
                        claim.created_at or observed_at,
                        claim.status,
                        json.dumps(asdict(claim), default=str),
                    ),
                )

    def latest_validated_claims(self, ticker: str) -> list[dict]:
        ticker = ticker.upper()
        with self.connect() as db:
            run = db.execute(
                """SELECT run_id FROM validated_claims
                WHERE ticker=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker,),
            ).fetchone()
            if not run:
                return []
            rows = db.execute(
                """SELECT payload_json FROM validated_claims
                WHERE ticker=? AND run_id=? ORDER BY claim_id""",
                (ticker, run["run_id"]),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def save_source_plan(self, run_id: str, plan) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO source_plans
                (ticker,run_id,observed_at,payload_json)
                VALUES (?,?,?,?)""",
                (
                    plan.ticker.upper(),
                    run_id,
                    plan.generated_at or _utc_now(),
                    json.dumps(asdict(plan), default=str),
                ),
            )

    def latest_source_plan(self, ticker: str) -> dict:
        ticker = ticker.upper()
        with self.connect() as db:
            row = db.execute(
                """SELECT payload_json FROM source_plans
                WHERE ticker=? ORDER BY observed_at DESC LIMIT 1""",
                (ticker,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else {}

    def save_idea_versions(self, ticker: str, run_id: str, ideas: list) -> None:
        created_at = _utc_now()
        with self.connect() as db:
            for idea in ideas:
                existing = db.execute(
                    "SELECT version FROM idea_versions WHERE idea_id=? AND run_id=?",
                    (idea.idea_id, run_id),
                ).fetchone()
                if existing:
                    version = existing["version"]
                else:
                    row = db.execute(
                        "SELECT COALESCE(MAX(version),0)+1 AS version FROM idea_versions WHERE idea_id=?",
                        (idea.idea_id,),
                    ).fetchone()
                    version = int(row["version"])
                db.execute(
                    """INSERT OR REPLACE INTO idea_versions
                    (idea_id,version,run_id,ticker,stage,signal_family,horizon,created_at,payload_json)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        idea.idea_id, version, run_id, ticker.upper(), idea.stage,
                        idea.signal_family, idea.horizon, created_at, json.dumps(asdict(idea)),
                    ),
                )
                if idea.payoff_model:
                    source = (
                        idea.payoff_model.probability_provenance.source
                        if idea.payoff_model.probability_provenance else "unknown"
                    )
                    for scenario in idea.payoff_model.scenarios:
                        db.execute(
                            """INSERT OR REPLACE INTO scenario_forecasts
                            (idea_id,version,case_name,probability,probability_source,
                             exit_value,net_return_pct,payload_json)
                            VALUES (?,?,?,?,?,?,?,?)""",
                            (
                                idea.idea_id, version, scenario.name, scenario.probability,
                                source, scenario.exit_value, scenario.net_return_pct,
                                json.dumps(asdict(scenario)),
                            ),
                        )

    def idea_audit(self, idea_id: str) -> dict | None:
        with self.connect() as db:
            versions = db.execute(
                "SELECT * FROM idea_versions WHERE idea_id=? ORDER BY version DESC",
                (idea_id,),
            ).fetchall()
            if not versions:
                return None
            forecasts = db.execute(
                "SELECT * FROM scenario_forecasts WHERE idea_id=? ORDER BY version DESC,case_name",
                (idea_id,),
            ).fetchall()
            outcomes = db.execute(
                "SELECT * FROM realized_outcomes WHERE idea_id=? ORDER BY version DESC,horizon",
                (idea_id,),
            ).fetchall()
        return {
            "idea_id": idea_id,
            "versions": [dict(row) | {"payload": json.loads(row["payload_json"])} for row in versions],
            "forecasts": [dict(row) | {"payload": json.loads(row["payload_json"])} for row in forecasts],
            "outcomes": [dict(row) for row in outcomes],
        }

    def latest_attributions(self, ticker: str) -> list[dict]:
        with self.connect() as db:
            run = db.execute(
                "SELECT run_id FROM research_runs WHERE ticker=? ORDER BY generated_at DESC LIMIT 1",
                (ticker.upper(),),
            ).fetchone()
            if not run:
                return []
            rows = db.execute(
                """SELECT payload_json FROM idea_versions
                WHERE ticker=? AND run_id=? ORDER BY signal_family, idea_id""",
                (ticker.upper(), run["run_id"]),
            ).fetchall()
        attributions = []
        for row in rows:
            payload = json.loads(row["payload_json"])
            attribution = payload.get("driver_attribution")
            if attribution:
                attributions.append({
                    "idea_id": payload.get("idea_id"),
                    "idea_title": payload.get("title"),
                    "stage": payload.get("stage"),
                    "driver_attribution": attribution,
                })
        return attributions

    def promote_idea(self, idea_id: str) -> bool:
        return self.promote_idea_with_audit(idea_id)["promoted"]

    def promote_idea_with_audit(self, idea_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM idea_versions WHERE idea_id=? ORDER BY version DESC LIMIT 1",
                (idea_id,),
            ).fetchone()
            if not row:
                return {
                    "promoted": False,
                    "idea_id": idea_id,
                    "reason": "Idea not found",
                    "research_ready_failed": [],
                    "high_conviction_failed": [],
                }
            payload = json.loads(row["payload_json"])
            allowed, reason, ready_failed, high_failed = _promotion_gate_status(payload)
            if not allowed:
                return {
                    "promoted": False,
                    "idea_id": idea_id,
                    "reason": reason,
                    "research_ready_failed": ready_failed,
                    "high_conviction_failed": high_failed,
                    "stage": row["stage"],
                }
            payload["stage"] = "High-Conviction"
            db.execute(
                "UPDATE idea_versions SET stage='High-Conviction',payload_json=? WHERE idea_id=? AND version=?",
                (json.dumps(payload), idea_id, row["version"]),
            )
        return {
            "promoted": True,
            "idea_id": idea_id,
            "reason": "High-Conviction gates passed.",
            "research_ready_failed": [],
            "high_conviction_failed": [],
            "stage": "High-Conviction",
        }

    def update_idea_assumptions(self, idea_id: str, assumptions: dict) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM idea_versions WHERE idea_id=? ORDER BY version DESC LIMIT 1",
                (idea_id,),
            ).fetchone()
            if not row:
                return None
            payload = json.loads(row["payload_json"])
            payload["user_assumptions"] = assumptions
            payload["assumptions_updated_at"] = _utc_now()
            db.execute(
                "UPDATE idea_versions SET payload_json=? WHERE idea_id=? AND version=?",
                (json.dumps(payload), idea_id, row["version"]),
            )
        return payload

    def latest_idea_assumptions(self, idea_id: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM idea_versions WHERE idea_id=? ORDER BY version DESC LIMIT 1",
                (idea_id,),
            ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return {}
        assumptions = payload.get("user_assumptions")
        return assumptions if isinstance(assumptions, dict) else {}

    def latest_idea_assumptions_many(self, idea_ids: list[str]) -> dict[str, dict]:
        normalized_ids = [str(item) for item in idea_ids if str(item)]
        if not normalized_ids:
            return {}
        placeholders = ",".join("?" for _ in normalized_ids)
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT idea_id,version,payload_json FROM idea_versions
                WHERE idea_id IN ({placeholders}) ORDER BY idea_id,version DESC""",
                normalized_ids,
            ).fetchall()
        results: dict[str, dict] = {}
        for row in rows:
            idea_id = row["idea_id"]
            if idea_id in results:
                continue
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                results[idea_id] = {}
                continue
            assumptions = payload.get("user_assumptions")
            results[idea_id] = assumptions if isinstance(assumptions, dict) else {}
        return results

    def save_market_implied_assumptions(self, ticker: str, assumptions: dict) -> dict:
        normalized_ticker = str(ticker or "").strip().upper()
        if not normalized_ticker:
            raise ValueError("ticker is required")
        safe_payload: dict[str, float] = {}
        for key, raw_value in (assumptions or {}).items():
            if key not in {
                "discount_rate_pct", "terminal_growth_pct", "forecast_years",
                "revenue_growth_pct", "sustainable_roe_anchor_pct", "pb_roe_sensitivity_pct",
            }:
                continue
            try:
                safe_payload[key] = float(raw_value)
            except (TypeError, ValueError):
                continue
        with self.connect() as db:
            db.execute(
                """INSERT INTO market_implied_assumptions (ticker,updated_at,payload_json)
                VALUES (?,?,?)
                ON CONFLICT(ticker) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json""",
                (normalized_ticker, _utc_now(), json.dumps(safe_payload)),
            )
        return safe_payload

    def latest_market_implied_assumptions(self, ticker: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT payload_json FROM market_implied_assumptions WHERE ticker=?",
                (str(ticker or "").strip().upper(),),
            ).fetchone()
        if not row:
            return {}
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def clear_market_implied_assumptions(self, ticker: str) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "DELETE FROM market_implied_assumptions WHERE ticker=?",
                (str(ticker or "").strip().upper(),),
            )
        return bool(cursor.rowcount)

    def record_realized_outcome(
        self,
        idea_id: str,
        version: int,
        horizon: str,
        realized_return_pct: float | None,
        max_adverse_excursion_pct: float | None,
        max_favorable_excursion_pct: float | None,
        thesis_outcome: str,
        closure_reason: str,
        evidence_valid: str | None = None,
        what_worked: str | None = None,
        what_failed: str | None = None,
        lessons: str | None = None,
        next_process_change: str | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO realized_outcomes
                (idea_id,version,horizon,realized_return_pct,max_adverse_excursion_pct,
                 max_favorable_excursion_pct,thesis_outcome,closure_reason,evidence_valid,
                 what_worked,what_failed,lessons,next_process_change,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    idea_id, version, horizon, realized_return_pct,
                    max_adverse_excursion_pct, max_favorable_excursion_pct,
                    thesis_outcome, closure_reason, evidence_valid,
                    what_worked, what_failed, lessons, next_process_change, _utc_now(),
                ),
            )

    def record_idea_post_mortem(
        self,
        idea_id: str,
        payload: dict,
    ) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM idea_versions WHERE idea_id=? ORDER BY version DESC LIMIT 1",
                (idea_id,),
            ).fetchone()
            if not row:
                return None
            idea_payload = json.loads(row["payload_json"])
            version = int(row["version"])
        horizon = str(payload.get("horizon") or row["horizon"] or idea_payload.get("horizon") or "default")
        realized = _optional_float(payload.get("realized_return_pct"))
        adverse = _optional_float(payload.get("max_adverse_excursion_pct"))
        favorable = _optional_float(payload.get("max_favorable_excursion_pct"))
        abnormal = _optional_float(payload.get("abnormal_return_pct"))
        thesis_outcome = str(payload.get("thesis_outcome") or payload.get("outcome") or "unreviewed")
        closure_reason = str(payload.get("closure_reason") or "")
        evidence_valid = str(payload.get("evidence_valid") or "")
        what_worked = str(payload.get("what_worked") or "")
        what_failed = str(payload.get("what_failed") or "")
        lessons = str(payload.get("lessons") or "")
        next_process_change = str(payload.get("next_process_change") or "")
        self.record_realized_outcome(
            idea_id,
            version,
            horizon,
            realized,
            adverse,
            favorable,
            thesis_outcome,
            closure_reason,
            evidence_valid=evidence_valid,
            what_worked=what_worked,
            what_failed=what_failed,
            lessons=lessons,
            next_process_change=next_process_change,
        )
        event = (idea_payload.get("source_events") or [{}])[0] or {}
        direction = _event_signal_direction(idea_payload, event)
        expected_return = _optional_float(
            (idea_payload.get("payoff_model") or {}).get("expected_value_pct")
        )
        probability = _positive_scenario_probability(idea_payload)
        probability_source = (
            (idea_payload.get("probability_provenance") or {}).get("source")
            or ((idea_payload.get("payoff_model") or {}).get("probability_provenance") or {}).get("source")
            or "illustrative_default"
        )
        self.record_event_signal(
            signal_id=f"{row['ticker']}:{idea_id}:{event.get('event_date') or 'post_mortem'}",
            ticker=row["ticker"],
            signal_type=str(idea_payload.get("signal_family") or event.get("category") or "unknown"),
            event_date=event.get("event_date"),
            direction=direction,
            expected_return_pct=expected_return,
            predicted_success_probability=probability,
            realized_return_pct=realized,
            abnormal_return_pct=abnormal,
            max_adverse_excursion_pct=adverse,
            max_favorable_excursion_pct=favorable,
            probability_source=probability_source,
            stage=row["stage"],
            horizon_label=horizon,
        )
        return {
            "idea_id": idea_id,
            "version": version,
            "ticker": row["ticker"],
            "horizon": horizon,
            "realized_return_pct": realized,
            "thesis_outcome": thesis_outcome,
            "closure_reason": closure_reason,
            "evidence_valid": evidence_valid,
            "what_worked": what_worked,
            "what_failed": what_failed,
            "lessons": lessons,
            "next_process_change": next_process_change,
        }

    def save_thesis_checks(self, ticker: str, ideas: list) -> None:
        observed_at = _utc_now()
        with self.connect() as db:
            for idea in ideas:
                for item in idea.monitor_items:
                    db.execute(
                        """INSERT OR REPLACE INTO thesis_checks
                        (idea_id,ticker,criterion,metric,operator,confirm_threshold,break_threshold,
                         confirm_value,break_value,deadline,source_field,status,observed_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            idea.idea_id, ticker.upper(), item.criterion, item.metric, item.operator,
                            item.confirm_threshold, item.break_threshold,
                            _monitor_value(item.confirm_value), _monitor_value(item.break_value), item.deadline,
                            item.source_field, item.status, observed_at,
                        ),
                    )

    def list_thesis_checks(self, ticker: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM thesis_checks WHERE ticker=? ORDER BY idea_id,criterion",
                (ticker.upper(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_event_signal(
        self,
        signal_id: str,
        ticker: str,
        signal_type: str,
        event_date: str | None,
        direction: str,
        expected_return_pct: float | None,
        predicted_success_probability: float | None,
        realized_return_pct: float | None,
        abnormal_return_pct: float | None,
        max_adverse_excursion_pct: float | None = None,
        max_favorable_excursion_pct: float | None = None,
        horizon_days: int = 20,
        probability_source: str = "illustrative_default",
        stage: str = "Candidate",
        horizon_label: str | None = None,
    ) -> None:
        outcome_as_of = _utc_now() if realized_return_pct is not None else None
        with self.connect() as db:
            db.execute(
                """INSERT INTO event_signals
                (signal_id,ticker,signal_type,event_date,direction,expected_return_pct,
                 predicted_success_probability,observed_at,realized_return_pct,
                 abnormal_return_pct,max_adverse_excursion_pct,horizon_days,outcome_as_of,
                 max_favorable_excursion_pct,probability_source,stage,horizon_label)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(signal_id) DO UPDATE SET
                    realized_return_pct=excluded.realized_return_pct,
                    abnormal_return_pct=excluded.abnormal_return_pct,
                    max_adverse_excursion_pct=COALESCE(excluded.max_adverse_excursion_pct,event_signals.max_adverse_excursion_pct),
                    max_favorable_excursion_pct=COALESCE(excluded.max_favorable_excursion_pct,event_signals.max_favorable_excursion_pct),
                    horizon_days=excluded.horizon_days,
                    outcome_as_of=COALESCE(excluded.outcome_as_of,event_signals.outcome_as_of)
                """,
                (
                    signal_id, ticker.upper(), signal_type, event_date, direction,
                    expected_return_pct, predicted_success_probability, _utc_now(),
                    realized_return_pct, abnormal_return_pct, max_adverse_excursion_pct,
                    horizon_days, outcome_as_of, max_favorable_excursion_pct,
                    probability_source, stage, horizon_label,
                ),
            )

    def event_signal_rows(self, ticker: str | None = None) -> list[dict]:
        with self.connect() as db:
            if ticker:
                rows = db.execute(
                    "SELECT * FROM event_signals WHERE ticker=? ORDER BY observed_at",
                    (ticker.upper(),),
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM event_signals ORDER BY observed_at").fetchall()
        return [dict(row) for row in rows]

    def realized_outcome_rows(self, ticker: str | None = None) -> list[dict]:
        with self.connect() as db:
            if ticker:
                rows = db.execute(
                    """SELECT r.*, i.ticker, i.stage, i.signal_family
                       FROM realized_outcomes r
                       JOIN idea_versions i ON i.idea_id=r.idea_id AND i.version=r.version
                       WHERE i.ticker=?
                       ORDER BY r.observed_at""",
                    (ticker.upper(),),
                ).fetchall()
            else:
                rows = db.execute(
                    """SELECT r.*, i.ticker, i.stage, i.signal_family
                       FROM realized_outcomes r
                       JOIN idea_versions i ON i.idea_id=r.idea_id AND i.version=r.version
                       ORDER BY r.observed_at"""
                ).fetchall()
        return [dict(row) for row in rows]

    def historical_idea_rows(self, limit: int = 250) -> list[dict]:
        with self.connect() as db:
            try:
                rows = db.execute(
                    """SELECT rowid AS storage_order, idea_id, version, run_id, ticker, stage,
                       signal_family, horizon, created_at,
                       json_extract(payload_json, '$.title') AS payload_title,
                       json_extract(payload_json, '$.direction') AS payload_direction,
                       json_extract(payload_json, '$.stage') AS payload_stage,
                       json_extract(payload_json, '$.score.total') AS payload_score_total,
                       json_extract(payload_json, '$.market_capture.category') AS payload_capture_category,
                       json_extract(payload_json, '$.source_events[0].category') AS payload_event_category,
                       json_extract(payload_json, '$.source_events[0].event_date') AS payload_event_date
                       FROM idea_versions
                       ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
                compact_payloads = True
            except sqlite3.OperationalError:
                # Older SQLite builds may omit JSON1. Retain the compatible path,
                # while modern builds avoid decoding deeply nested idea payloads.
                rows = db.execute(
                    """SELECT rowid AS storage_order, idea_id, version, run_id, ticker, stage,
                       signal_family, horizon, created_at, payload_json
                       FROM idea_versions
                       ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
                compact_payloads = False
            idea_ids = list(dict.fromkeys(str(row["idea_id"]) for row in rows))
            placeholders = ",".join("?" for _ in idea_ids)
            outcomes = db.execute(
                f"""SELECT idea_id, version, horizon, realized_return_pct,
                   max_adverse_excursion_pct, max_favorable_excursion_pct,
                   thesis_outcome, closure_reason, evidence_valid, what_worked,
                   what_failed, lessons, next_process_change, observed_at
                   FROM realized_outcomes
                   {f'WHERE idea_id IN ({placeholders})' if idea_ids else 'WHERE 0'}""",
                idea_ids,
            ).fetchall()
            signals = db.execute(
                """SELECT signal_id, ticker, signal_type, event_date, direction,
                   realized_return_pct, abnormal_return_pct, max_adverse_excursion_pct,
                   max_favorable_excursion_pct, horizon_label, stage, outcome_as_of
                   FROM event_signals"""
            ).fetchall()
        outcomes_by_key = {
            (row["idea_id"], row["version"], row["horizon"]): dict(row)
            for row in outcomes
        }
        signals_by_idea: dict[str, list[dict]] = {}
        for row in signals:
            parts = str(row["signal_id"]).split(":")
            if len(parts) >= 2:
                signals_by_idea.setdefault(parts[1], []).append(dict(row))
        result = []
        for row in rows:
            if compact_payloads:
                payload = {
                    "title": row["payload_title"],
                    "direction": row["payload_direction"],
                    "stage": row["payload_stage"] or row["stage"],
                    "signal_family": row["signal_family"],
                    "horizon": row["horizon"],
                    "score": {"total": row["payload_score_total"] or 0},
                    "market_capture": {
                        "category": row["payload_capture_category"] or "Unknown",
                    },
                    "source_events": [{
                        "category": row["payload_event_category"] or "",
                        "event_date": row["payload_event_date"],
                    }] if row["payload_event_category"] or row["payload_event_date"] else [],
                }
            else:
                payload = json.loads(row["payload_json"])
            key = (row["idea_id"], row["version"], row["horizon"])
            result.append(
                dict(row)
                | {
                    "payload": payload,
                    "outcome": outcomes_by_key.get(key),
                    "event_signals": signals_by_idea.get(row["idea_id"], []),
                }
            )
        return result

    def calibrated_probability(
        self,
        signal_type: str,
        horizon_label: str,
        minimum_sample_size: int = 30,
    ) -> tuple[float | None, int]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT direction,realized_return_pct FROM event_signals
                WHERE signal_type=? AND horizon_label=? AND stage IN ('Research-Ready','High-Conviction','Investable')
                AND realized_return_pct IS NOT NULL ORDER BY observed_at""",
                (signal_type, horizon_label),
            ).fetchall()
        sample_size = len(rows)
        if sample_size < minimum_sample_size:
            return None, sample_size
        hits = [
            1.0 if (
                row["realized_return_pct"]
                if row["direction"] != "negative"
                else -row["realized_return_pct"]
            ) > 0 else 0.0
            for row in rows
        ]
        return sum(hits) / len(hits), sample_size

    def create_alert(
        self,
        ticker: str,
        alert_type: str,
        title: str,
        message: str,
        severity: int,
        dedupe_key: str,
        fiscal_period: str | None = None,
    ) -> AlertRecord | None:
        now = _utc_now()
        with self.connect() as db:
            existing = db.execute(
                "SELECT * FROM alerts WHERE dedupe_key=?",
                (dedupe_key,),
            ).fetchone()
            if existing:
                existing_date = datetime.fromisoformat(existing["created_at"])
                if datetime.now(timezone.utc) - existing_date < timedelta(days=7):
                    if severity <= existing["severity"]:
                        return None
                db.execute(
                    """UPDATE alerts SET title=?,message=?,severity=?,status='unread',created_at=?
                    WHERE dedupe_key=?""",
                    (title, message, severity, now, dedupe_key),
                )
            else:
                db.execute(
                    """INSERT INTO alerts
                    (ticker,alert_type,title,message,severity,status,created_at,dedupe_key,fiscal_period)
                    VALUES (?,?,?,?,?,'unread',?,?,?)""",
                    (ticker.upper(), alert_type, title, message, severity, now, dedupe_key, fiscal_period),
                )
            row = db.execute("SELECT * FROM alerts WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return _alert_from_row(row)

    def create_alerts(self, alerts: list[dict]) -> list[AlertRecord]:
        if not alerts:
            return []
        now = _utc_now()
        rows = []
        with self.connect() as db:
            for alert in alerts:
                ticker = str(alert["ticker"]).upper()
                alert_type = str(alert["alert_type"])
                title = str(alert["title"])
                message = str(alert["message"])
                severity = int(alert["severity"])
                dedupe_key = str(alert["dedupe_key"])
                fiscal_period = alert.get("fiscal_period")
                existing = db.execute(
                    "SELECT * FROM alerts WHERE dedupe_key=?",
                    (dedupe_key,),
                ).fetchone()
                if existing:
                    existing_date = datetime.fromisoformat(existing["created_at"])
                    if datetime.now(timezone.utc) - existing_date < timedelta(days=7):
                        if severity <= existing["severity"]:
                            continue
                    db.execute(
                        """UPDATE alerts SET title=?,message=?,severity=?,status='unread',created_at=?
                        WHERE dedupe_key=?""",
                        (title, message, severity, now, dedupe_key),
                    )
                else:
                    db.execute(
                        """INSERT INTO alerts
                        (ticker,alert_type,title,message,severity,status,created_at,dedupe_key,fiscal_period)
                        VALUES (?,?,?,?,?,'unread',?,?,?)""",
                        (ticker, alert_type, title, message, severity, now, dedupe_key, fiscal_period),
                    )
                row = db.execute("SELECT * FROM alerts WHERE dedupe_key=?", (dedupe_key,)).fetchone()
                if row:
                    rows.append(row)
        return [_alert_from_row(row) for row in rows]

    def list_alerts(self, status: str | None = None, limit: int = 100) -> list[AlertRecord]:
        query = "SELECT * FROM alerts"
        params: list[object] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
        return [_alert_from_row(row) for row in rows]

    def update_alert_status(self, alert_id: int, status: str) -> bool:
        if status not in {"unread", "read", "dismissed"}:
            raise ValueError("Invalid alert status")
        with self.connect() as db:
            cursor = db.execute("UPDATE alerts SET status=? WHERE alert_id=?", (status, alert_id))
        return cursor.rowcount > 0

    def migrate_idea_memory(self, path: Path | None = None) -> int:
        source = path or config.IDEA_MEMORY_PATH
        if not source.exists():
            return 0
        try:
            records = json.loads(source.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return 0
        imported = 0
        with self.connect() as db:
            for record in records:
                idea_id = record.get("idea_id")
                ticker = record.get("ticker")
                if not idea_id or not ticker:
                    continue
                cursor = db.execute(
                    """INSERT OR IGNORE INTO idea_records
                    (idea_id,ticker,status,payload_json,imported_at) VALUES (?,?,?,?,?)""",
                    (idea_id, ticker, "legacy_candidate", json.dumps(record), _utc_now()),
                )
                imported += cursor.rowcount
        return imported

    def list_idea_records(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT status,payload_json FROM idea_records ORDER BY imported_at").fetchall()
        records: list[dict] = []
        for row in rows:
            try:
                record = json.loads(row["payload_json"])
                record["status"] = row["status"]
                record["legacy"] = row["status"] == "legacy_candidate"
                records.append(record)
            except json.JSONDecodeError:
                continue
        return records

    def save_idea_record(self, record: dict) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO idea_records
                (idea_id,ticker,status,payload_json,imported_at) VALUES (?,?,?,?,?)""",
                (
                    record["idea_id"], record["ticker"].upper(), record.get("status", "Open"),
                    json.dumps(record), _utc_now(),
                ),
            )

    def update_idea_post_mortem(
        self,
        idea_id: str,
        outcome: str,
        realized_return_pct: float | None,
        lessons: str,
    ) -> None:
        records = self.list_idea_records()
        record = next((item for item in records if item.get("idea_id") == idea_id), None)
        if not record:
            return
        record["status"] = "Closed"
        record["post_mortem"] = {
            "closed_at": _utc_now(),
            "outcome": outcome,
            "realized_return_pct": realized_return_pct,
            "lessons": lessons,
        }
        self.save_idea_record(record)


def _target_from_row(row: sqlite3.Row) -> TargetConsensus:
    target = TargetConsensus(
        ticker=row["ticker"], as_of=row["as_of"], currency=row["currency"],
        target_aggregate=row["target_aggregate"],
        target_mean=row["target_mean"], target_median=row["target_median"],
        target_high=row["target_high"], target_low=row["target_low"],
        analyst_count=row["analyst_count"], current_price=row["current_price"],
        provider_timestamp=row["provider_timestamp"], source=row["provider"],
        target_kind=row["target_kind"], target_label=row["target_label"],
    )
    primary = _target_primary_value(target)
    if target.current_price and primary:
        target.implied_upside_pct = (primary / target.current_price - 1) * 100
    if target.target_mean and target.target_high is not None and target.target_low is not None:
        target.dispersion_pct = (target.target_high - target.target_low) / abs(target.target_mean) * 100
    target.freshness_days = _freshness_days(target.provider_timestamp or target.as_of)
    return target


def _optional_float(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _event_signal_direction(idea_payload: dict, event_payload: dict) -> str:
    event_direction = str(event_payload.get("direction") or "").lower()
    if event_direction in {"positive", "negative", "neutral"}:
        return event_direction
    idea_direction = str(idea_payload.get("direction") or "").lower()
    if idea_direction == "short":
        return "negative"
    if idea_direction == "long":
        return "positive"
    return event_direction or idea_direction or "neutral"


def _promotion_gate_status(payload: dict) -> tuple[bool, str, list[str], list[str]]:
    gate = payload.get("gate_result") or {}
    ready_failed = [str(item) for item in gate.get("research_ready_failed") or [] if str(item)]
    high_failed = [str(item) for item in gate.get("high_conviction_failed") or [] if str(item)]
    research_ready = bool(gate.get("research_ready")) and not ready_failed
    high_conviction = bool(gate.get("high_conviction")) and not high_failed
    eligible = bool(gate.get("eligible"))
    if not gate:
        return False, "Idea has no gate audit.", ready_failed, high_failed
    if ready_failed or not research_ready:
        return (
            False,
            "Promotion blocked: Research-Ready gates must pass before High-Conviction.",
            ready_failed or ["Research-Ready gate status is not passed."],
            high_failed,
        )
    if high_failed or not high_conviction or not eligible:
        return (
            False,
            "Promotion blocked: High-Conviction gates are incomplete.",
            ready_failed,
            high_failed or ["High-Conviction gate status is not passed."],
        )
    return True, "High-Conviction gates passed.", [], []


def _positive_scenario_probability(idea_payload: dict) -> float | None:
    scenarios = (
        ((idea_payload.get("payoff_model") or {}).get("scenarios") or [])
        or idea_payload.get("scenarios")
        or []
    )
    total_probability = 0.0
    has_probability = False
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        probability = _optional_float(scenario.get("probability"))
        net_return = _optional_float(scenario.get("net_return_pct"))
        if probability is None or net_return is None:
            continue
        has_probability = True
        if net_return > 0:
            total_probability += probability
    return total_probability if has_probability else None


def _ensure_column(
    db: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _monitor_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _estimate_from_row(row: sqlite3.Row) -> EstimatePoint:
    return EstimatePoint(
        ticker=row["ticker"], as_of=row["as_of"], metric=row["metric"],
        period_end=row["period_end"], period_type=row["period_type"],
        average=row["average"], high=row["high"], low=row["low"],
        analyst_count=row["analyst_count"], currency=row["currency"], source=row["provider"],
        period_precision=row["period_precision"], revisions_up=row["revisions_up"],
        revisions_down=row["revisions_down"],
    )


def _turn_row_dict(row: sqlite3.Row) -> dict:
    payload = dict(row)
    for field in ("positive_terms", "negative_terms", "uncertainty_terms", "evasion_terms"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            try:
                payload[field] = json.loads(value)
            except json.JSONDecodeError:
                payload[field] = []
        elif value is None:
            payload[field] = []
    return payload


def _package_from_dict(payload: dict) -> ConsensusPackage:
    target = TargetConsensus(**payload["target"]) if payload.get("target") else None
    recommendations = (
        RecommendationConsensus(**payload["recommendations"])
        if payload.get("recommendations") else None
    )
    return ConsensusPackage(
        ticker=payload["ticker"], provider=payload["provider"], status=payload["status"],
        target=target, recommendations=recommendations,
        estimates=[EstimatePoint(**item) for item in payload.get("estimates", [])],
        surprises=[EarningsSurprise(**item) for item in payload.get("surprises", [])],
        revisions=[RevisionWindow(**item) for item in payload.get("revisions", [])],
        data_gaps=list(payload.get("data_gaps", [])),
        observations=[ProviderObservation(**item) for item in payload.get("observations", [])],
        provider_statuses=[ProviderStatus(**item) for item in payload.get("provider_statuses", [])],
        comparisons=[ProviderComparison(**item) for item in payload.get("comparisons", [])],
        unofficial_only=bool(payload.get("unofficial_only", False)),
        provider_targets=[TargetConsensus(**item) for item in payload.get("provider_targets", [])],
    )


def _target_primary_value(target: TargetConsensus) -> float | None:
    if target.target_kind == "aggregate":
        return target.target_aggregate
    if target.target_kind == "median":
        return target.target_median
    return target.target_mean


def _revision_status_and_reason(
    start_value: float | None,
    end_value: float | None,
    start_date: str | None,
    end_date: str | None,
    cutoff: str,
    earliest_date: str | None,
    provider: str,
    metric: str,
) -> tuple[str, str]:
    if end_value is None or not end_date:
        return (
            "no_current_snapshot",
            f"No current {metric} snapshot is available for {provider or 'the selected provider'}.",
        )
    if start_value is None or not start_date:
        if earliest_date:
            return (
                "insufficient_history",
                (
                    f"No {metric} snapshot on or before {cutoff}; earliest local "
                    f"snapshot for {provider or 'the selected provider'} is {earliest_date}."
                ),
            )
        return (
            "insufficient_history",
            f"No prior local {metric} snapshots are available for {provider or 'the selected provider'}.",
        )
    return (
        "available",
        f"Calculated from local point-in-time snapshots on {start_date} and {end_date}.",
    )


def _alert_from_row(row: sqlite3.Row) -> AlertRecord:
    return AlertRecord(
        alert_id=row["alert_id"], ticker=row["ticker"], alert_type=row["alert_type"],
        title=row["title"], message=row["message"], severity=row["severity"],
        status=row["status"], created_at=row["created_at"], dedupe_key=row["dedupe_key"],
        fiscal_period=row["fiscal_period"],
    )


def _observation_from_row(row: sqlite3.Row) -> ProviderObservation:
    return ProviderObservation(
        ticker=row["ticker"], provider=row["provider"], field=row["field"],
        observed_at=row["observed_at"], source_as_of=row["source_as_of"],
        value_numeric=row["value_numeric"], value_text=row["value_text"],
        currency=row["currency"], analyst_count=row["analyst_count"],
        entitlement_status=row["entitlement_status"], provenance=row["provenance"],
        official=bool(row["official"]), confidence=row["confidence"],
    )


def _macro_observation_from_row(row: sqlite3.Row) -> dict:
    payload = dict(row)
    payload["official"] = bool(payload["official"])
    payload["lookahead_safe"] = bool(payload["lookahead_safe"])
    payload["cache_status"] = (
        "same_day"
        if str(payload.get("observed_at") or "")[:10] == date.today().isoformat()
        else "historical"
    )
    for field in ("tags_json", "citation_json"):
        value = payload.get(field)
        if isinstance(value, str) and value:
            try:
                payload[field.replace("_json", "")] = json.loads(value)
            except json.JSONDecodeError:
                payload[field.replace("_json", "")] = [] if field == "tags_json" else None
        else:
            payload[field.replace("_json", "")] = [] if field == "tags_json" else None
        payload.pop(field, None)
    return payload


def _external_evidence_from_macro_row(row: sqlite3.Row) -> ExternalEvidence:
    citation_payload = row["citation_json"]
    citation = None
    if citation_payload:
        try:
            citation = Citation(**json.loads(citation_payload))
        except (TypeError, json.JSONDecodeError):
            citation = None
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
    except json.JSONDecodeError:
        tags = []
    return ExternalEvidence(
        provider=row["provider"],
        source_type="macro_factor",
        title=row["title"],
        summary=row["summary"],
        observed_at=row["observed_at"],
        source_as_of=row["source_as_of"],
        source_tier=row["source_tier"],
        official=bool(row["official"]),
        confidence=row["confidence"],
        metric_name=row["series_id"],
        metric_value=row["value_numeric"],
        unit=row["unit"],
        frequency=row["frequency"],
        release_date=row["release_date"],
        vintage_date=row["vintage_date"],
        lookahead_safe=bool(row["lookahead_safe"]),
        direction=row["direction"],
        citation=citation,
        tags=tags,
        disqualifies_high_conviction=False,
    )


def _external_research_excerpt_from_row(row: sqlite3.Row) -> dict:
    citation_payload = None
    if row["citation_json"]:
        try:
            citation_payload = json.loads(row["citation_json"])
        except json.JSONDecodeError:
            citation_payload = None
    try:
        theme_tags = json.loads(row["theme_tags_json"]) if row["theme_tags_json"] else []
    except json.JSONDecodeError:
        theme_tags = []
    return {
        "excerpt_id": row["excerpt_id"],
        "ticker": row["ticker"],
        "provider": row["provider"],
        "category": row["category"],
        "report_id": row["report_id"],
        "title": row["title"],
        "source_as_of": row["source_as_of"],
        "observed_at": row["observed_at"],
        "source_language": row["source_language"],
        "original_excerpt": row["original_excerpt"],
        "translated_summary": row["translated_summary"],
        "generated_summary": row["generated_summary"],
        "theme_tags": theme_tags,
        "citation": citation_payload,
        "source_tier": row["source_tier"],
        "confidence": row["confidence"],
        "licensing_policy": row["licensing_policy"],
        "mentions_target_or_rating": bool(row["mentions_target_or_rating"]),
        "non_consensus_label": row["non_consensus_label"],
    }


def _llm_profile_from_row(row: sqlite3.Row) -> LlmProviderProfile:
    return LlmProviderProfile(
        profile_id=row["profile_id"],
        display_name=row["display_name"],
        provider_preset=row["provider_preset"],
        model=row["model"],
        base_url=row["base_url"],
        role_eligibility=row["role_eligibility"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        key_configured=bool(row["key_configured"]),
        secret_ref=row["secret_ref"],
        last_test_status=row["last_test_status"],
        last_test_message=row["last_test_message"],
        last_test_at=row["last_test_at"],
    )


def _pct_change(start: float | None, end: float | None) -> float | None:
    if start in (None, 0) or end is None:
        return None
    return (end / start - 1) * 100


def _parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def _stable_artifact_payload(value):
    """Remove retrieval-time fields while retaining source/event dates."""
    if isinstance(value, dict):
        return {
            key: _stable_artifact_payload(item)
            for key, item in value.items()
            if key not in {"observed_at", "retrieved_at"}
        }
    if isinstance(value, list):
        return [_stable_artifact_payload(item) for item in value]
    return value


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _freshness_days(value: str) -> int | None:
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        try:
            observed = date.fromisoformat(value[:10])
        except (TypeError, ValueError):
            return None
    return max(0, (date.today() - observed).days)
