from __future__ import annotations

import html
import hashlib
import json
import os
import sys
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

from equity_research import config
from equity_research.application.research_runs import (
    ResearchRunCommand,
    ResearchRunService,
)
from equity_research.application import (
    DeterministicCashConversionRefreshService,
    DeterministicEvidenceRefreshCommand,
    ContextResearchToolGateway,
    InMemoryEphemeralCredentialBroker,
    InvestigationActionCommand,
    InvestigationActionResult,
    InvestigationActionService,
    build_investigation_journey,
    build_displayed_research_context,
    build_displayed_investigation_reference,
    build_provider_connection_statuses,
    parse_event_price_csv,
    SavedResearchError,
    SavedResearchLoadError,
    SavedResearchService,
    ResearchLaunchIntent,
    ResearchLaunchIntentStatus,
    ResearchLaunchService,
    ResearchOperationKind,
    build_research_launch_intent,
    resolve_research_parent_reference,
    should_auto_load_saved_result,
    canonicalize_investigation_actions,
    SourceCapabilityRegistry,
)
from equity_research.application.repair_stage_execution import (
    RepairExecutionPolicy,
)
from equity_research.auditable_retrieval import (
    AuditableEvidenceRetriever,
    source_record_to_evidence,
)
from equity_research.application.readiness import preview_research_readiness
from equity_research.analysis import format_number
from equity_research.consensus_import import import_consensus_csv, write_consensus_csv_templates
from equity_research.contributor_tools import build_contribution_pack, save_contribution_pack
from equity_research.external_evidence import (
    default_macro_source_settings,
    external_evidence_stack_from_config,
)
from equity_research.idea_engine import build_payoff_model, expected_value
from equity_research.infrastructure.job_repository import (
    SqliteResearchJobRepository,
)
from equity_research.infrastructure.repair_checkpoint_repository import (
    SqliteRepairStageCheckpointRepository,
)
from equity_research.infrastructure.saved_research_codec import (
    JsonResearchResultArtifactCodec,
    SavedResearchCodecError,
)
from equity_research.infrastructure.saved_research_repository import (
    ContentAddressedGzipBlobStore,
    InMemorySavedResearchBlobStore,
    InMemorySavedResearchRepository,
    SavedResearchStorageError,
    SqliteSavedResearchRepository,
)
from equity_research.domain.saved_research import (
    DisplayedResearchContext,
    DerivedEvidenceRecoveryAttempt,
    SAVED_RESEARCH_CODEC_VERSION,
    SaveResearchResultCommand,
    SavedArtifactHealth,
    SavedResearchOrigin,
    SavedResearchQuery,
    SavedResearchScope,
    SavedResearchStatus,
)
from equity_research.domain.financial_facts import CASH_CONVERSION_ANALYSIS_VERSION
from equity_research.domain.cache_policy import build_cache_isolation_key
from equity_research.infrastructure.provider_registry import (
    ProviderResolutionRequest,
    resolve_research_providers,
)
from equity_research.local_secrets import (
    LocalSecretsManager,
    save_validated_keys,
    validate_provider_keys,
)
from equity_research.llm_vault import (
    build_llm_profile,
    delete_llm_profile_with_secret,
    get_llm_preset,
    list_llm_presets,
    profile_api_key as saved_profile_api_key,
    profiles_are_genuinely_distinct,
    profile_supports_role,
    save_llm_profile_with_secret,
    test_llm_profile,
)
from equity_research.hybrid_research import (
    HybridResearchJobManager,
    load_frozen_hybrid_run,
    local_codex_readiness,
    start_local_codex_login,
)
from equity_research.freshness import build_research_freshness_from_result
from equity_research.models import DisplayedResultContext
from equity_research.memory import IdeaMemoryStore
from equity_research.network_diagnostics import run_network_diagnostics
from equity_research.pipeline import ResearchResult, run_us_equity_research
from equity_research.presentation.components import (
    render_decision_page as render_decision_page_component,
    render_evidence_page_overview,
    render_investigation_action_register,
    render_investigation_journey,
    render_integrity_timeline as render_integrity_timeline_component,
    render_publication_readiness,
    render_research_route_header,
    render_research_session_workbench,
)
from equity_research.presentation.session_state import UiSessionState
from equity_research.presentation.view_models import (
    DecisionPageIntent,
    PublicationRepairIntent,
    ResearchSubroute,
    build_decision_brief_view_model,
    build_durable_run_timeline_view_model,
    build_evidence_page_view_model,
    build_integrity_timeline_view_model,
    build_investigation_action_view_models,
    build_investigation_journey_view_model,
    build_publication_readiness_view_model,
    build_research_launch_intent_view_model,
    build_research_route_view_model,
    build_research_session_view_model,
    build_saved_research_library_view_model,
)
from equity_research.market_implied import build_market_implied_expectations
from equity_research.decision_intelligence import build_ic_implied_expectations_snapshot
from equity_research.driver_attribution import attach_driver_attributions
from equity_research.providers import StooqPriceClient, build_consensus_provider
from equity_research.research_store import ResearchStore
from equity_research.research_profiles import event_identifier, research_profiles
from equity_research.rigor import build_calibration_report
from equity_research.sample_data import demo_result
from equity_research.sec_client import SecClient, SecClientError
from equity_research.storytelling import demo_cases, public_demo_cases


st.set_page_config(
    page_title="IC Copilot",
    page_icon="",
    layout="wide",
    initial_sidebar_state="collapsed",
)

GITHUB_REPOSITORY_URL = "https://github.com/mwsp-code/IC-Copilot"
LIVE_DEMO_URL = "https://ic-copilot.streamlit.app/"
FEEDBACK_DISCUSSION_URL = "https://github.com/mwsp-code/IC-Copilot/discussions/3"
HYBRID_JOB_MANAGER_SCHEMA_VERSION = "explicit-research-launch-v5"
MACRO_POLICY_OPTIONS = ("Adaptive", "Paused", "Global diagnostic", "Custom")


@st.cache_resource
def _cached_research_run_service(database_path: str) -> ResearchRunService:
    return ResearchRunService(
        SqliteResearchJobRepository(Path(database_path))
    )


def _research_launch_service() -> ResearchLaunchService:
    return ResearchLaunchService(_research_run_service())


def _research_run_service() -> ResearchRunService:
    """Resolve durable run state by path so config/workspace changes cannot reuse stale cache."""

    return _cached_research_run_service(str(Path(config.RESEARCH_DB_PATH).resolve()))


@st.cache_resource
def _repair_stage_repository(database_path: str):
    return SqliteRepairStageCheckpointRepository(Path(database_path))


def _repair_execution_policy(*, closure_retry: bool = False) -> RepairExecutionPolicy:
    return RepairExecutionPolicy(
        revision_timeout_seconds=config.REPAIR_REVISION_TIMEOUT_SECONDS,
        closure_timeout_seconds=(
            config.REPAIR_CLOSURE_RETRY_TIMEOUT_SECONDS
            if closure_retry else config.REPAIR_CLOSURE_TIMEOUT_SECONDS
        ),
        total_timeout_seconds=(
            config.REPAIR_TOTAL_RETRY_TIMEOUT_SECONDS
            if closure_retry else config.REPAIR_TOTAL_TIMEOUT_SECONDS
        ),
        max_transient_retries=config.REPAIR_MAX_TRANSIENT_RETRIES,
        policy_version=(
            "repair-execution-policy-v2-closure-retry"
            if closure_retry else "repair-execution-policy-v1"
        ),
    )


def _selected_research_intent() -> ResearchLaunchIntent | None:
    value = st.session_state.get("_selected_research_launch_intent")
    return value if isinstance(value, ResearchLaunchIntent) else None


def _stage_research_intent(intent: ResearchLaunchIntent) -> None:
    """Persist a selection without launching providers or retrieval."""

    st.session_state._selected_research_launch_intent = intent
    st.session_state.pop("_research_launch_repeat_confirmation", None)


def _clear_research_intent() -> None:
    st.session_state.pop("_selected_research_launch_intent", None)
    st.session_state.pop("_research_launch_repeat_confirmation", None)
    st.session_state.pop("_investigation_source_setup", None)
    st.session_state.pop("_open_additional_sources", None)


def _has_active_research_task() -> bool:
    job_id = str(st.session_state.get("hybrid_job_id") or "")
    job = _hybrid_job_manager().get(job_id) if job_id else None
    if job is not None and job.status in {
        "queued", "running", "saving_result", "cancelling"
    }:
        return True
    focused_id = str(st.session_state.get("_focused_research_run_id") or "")
    snapshot = _research_run_service().inspect(focused_id) if focused_id else None
    return bool(
        snapshot is not None
        and snapshot.status in {
            "queued", "running", "cancelling", "waiting_for_credential",
            "research_attempt_finished", "validation_pending", "repair_required",
            "repair_running", "revision_pending", "audit_pending", "closure_pending",
        }
    )


def _displayed_parent_context(result: ResearchResult | None) -> dict[str, str]:
    artifact_id = str(
        st.session_state.get("_displayed_saved_artifact_id") or ""
    )
    saved_artifact = None
    if artifact_id:
        try:
            storage_scope = str(
                st.session_state.get("_displayed_saved_storage_scope")
                or _saved_research_scope()[0].value
            )
            scope_id = str(
                st.session_state.get("_displayed_saved_scope_id")
                or _saved_research_scope()[1]
            )
            saved_artifact = _saved_research_service_for_scope(
                storage_scope
            ).repository.get(artifact_id, scope_id=scope_id)
        except (OSError, RuntimeError, ValueError):
            saved_artifact = None
    parent = resolve_research_parent_reference(
        result,
        saved_artifact=saved_artifact,
        session_run_id=str(
            st.session_state.get("_displayed_parent_run_id")
            or st.session_state.get("research_lifecycle_run_id")
            or ""
        ),
        artifact_id=artifact_id,
    )
    return {
        "parent_run_id": parent.run_id,
        "parent_artifact_id": parent.artifact_id,
        "parent_evidence_hash": parent.evidence_hash,
    }


def _public_hosted_mode() -> bool:
    explicit = str(os.environ.get("IC_COPILOT_PUBLIC_HOSTED", "")).strip().casefold()
    if explicit:
        return explicit in {"1", "true", "yes", "on"}
    return bool(str(os.environ.get("STREAMLIT_SHARING_MODE", "")).strip())


@st.cache_resource
def _local_saved_research_service(
    database_path: str,
    storage_root: str,
) -> SavedResearchService:
    root = Path(storage_root)
    try:
        return SavedResearchService(
            SqliteSavedResearchRepository(Path(database_path)),
            ContentAddressedGzipBlobStore(root / "blobs"),
            JsonResearchResultArtifactCodec(),
        )
    except (OSError, SavedResearchStorageError) as exc:
        # Research and frozen demos remain usable when a locked-down host,
        # stale test directory, or transient filesystem failure prevents local
        # persistence. Keep the fallback explicitly session-only so the UI
        # never promises restart recovery that is not available.
        service = SavedResearchService(
            InMemorySavedResearchRepository(ttl_seconds=14_400),
            InMemorySavedResearchBlobStore(ttl_seconds=14_400),
            JsonResearchResultArtifactCodec(),
        )
        service.persistence_available = False
        service.persistence_error = str(exc)
        return service


@st.cache_resource
def _hosted_saved_research_service() -> SavedResearchService:
    return SavedResearchService(
        InMemorySavedResearchRepository(ttl_seconds=14_400),
        InMemorySavedResearchBlobStore(ttl_seconds=14_400),
        JsonResearchResultArtifactCodec(),
    )


def _saved_research_scope() -> tuple[SavedResearchScope, str]:
    if not _public_hosted_mode():
        return SavedResearchScope.LOCAL_WORKSPACE, "local"
    if "_saved_research_session_scope" not in st.session_state:
        st.session_state._saved_research_session_scope = "session-" + uuid.uuid4().hex
    return (
        SavedResearchScope.HOSTED_SESSION,
        str(st.session_state._saved_research_session_scope),
    )


def _saved_research_service_for_scope(
    storage_scope: SavedResearchScope | str,
) -> SavedResearchService:
    value = (
        storage_scope.value
        if isinstance(storage_scope, SavedResearchScope)
        else str(storage_scope)
    )
    return (
        _hosted_saved_research_service()
        if value == SavedResearchScope.HOSTED_SESSION.value
        else _local_saved_research_service(
            str(config.SAVED_RESEARCH_DB_PATH),
            str(config.SAVED_RESEARCH_ROOT),
        )
    )


def _saved_research_service() -> SavedResearchService:
    storage_scope, _ = _saved_research_scope()
    return _saved_research_service_for_scope(storage_scope)


@st.cache_resource
def _credential_broker() -> InMemoryEphemeralCredentialBroker:
    return InMemoryEphemeralCredentialBroker()


@st.cache_resource
def _investigation_action_service() -> InvestigationActionService:
    def record(result: InvestigationActionResult) -> None:
        run_id = result.command.parent_run_id
        service = _research_run_service()
        if not run_id or service.inspect(run_id) is None:
            return
        service.record_event(
            run_id,
            stage="targeted_investigation",
            status=result.status,
            summary=result.summary,
            details={
                "action_id": result.command.action_id,
                "event_id": result.command.event_id,
                "admitted_evidence_ids": list(result.admitted_evidence_ids),
                "qa_closure_state": result.qa_closure_state,
                "attempted_connectors": list(result.attempted_connectors),
                "result_payload": result.result_payload,
                "failure_class": result.failure_class,
            },
            idempotency_key=result.command.idempotency_key,
        )

    def load(command: InvestigationActionCommand) -> InvestigationActionResult | None:
        service = _research_run_service()
        if service.inspect(command.parent_run_id) is None:
            return None
        event = next(
            (
                item for item in service.timeline(command.parent_run_id)
                if item.idempotency_key == command.idempotency_key
            ),
            None,
        )
        if event is None:
            return None
        details = dict(event.details or {})
        return InvestigationActionResult(
            command=command,
            status=event.status,
            summary=event.summary,
            attempted_connectors=tuple(details.get("attempted_connectors") or ()),
            admitted_evidence_ids=tuple(details.get("admitted_evidence_ids") or ()),
            result_payload=dict(details.get("result_payload") or {}),
            qa_closure_state=str(details.get("qa_closure_state") or "not_required"),
            completed_at=event.created_at,
            failure_class=str(details.get("failure_class") or ""),
        )

    return InvestigationActionService(event_recorder=record, result_loader=load)


def _issue_session_credential_leases(
    *,
    primary_key: str,
    auditor_key: str,
) -> tuple[str, ...]:
    """Return opaque handles for secrets entered in this browser session."""

    broker = _credential_broker()
    references = []
    for provider_id, secret in (
        ("primary_research_ai", primary_key),
        ("independent_research_qa", auditor_key),
    ):
        if not secret:
            continue
        references.append(
            broker.issue(
                provider_id,
                secret,
                scope="research_run",
                ttl_seconds=14_400,
            )
        )
    st.session_state._credential_lease_references = references
    return tuple(reference.lease_id for reference in references)


def _saved_status_for_result(result: ResearchResult, run_status: str) -> SavedResearchStatus:
    normalized = str(run_status or "").casefold()
    if normalized in {"failed", "unavailable", "timeout", "validation_failed"}:
        return SavedResearchStatus.FAILED
    if normalized in {"partial", "investigated_draft", "cancelled"}:
        return SavedResearchStatus.PARTIAL
    freeze = getattr(result, "demo_freeze_decision", None) or getattr(
        getattr(result, "hybrid_research", None), "demo_freeze_decision", None
    )
    publication = str(getattr(freeze, "publication_label", "") or "").casefold()
    closure = str(
        getattr(
            getattr(
                getattr(result, "independent_research_qa", None),
                "closure_result",
                None,
            ),
            "state",
            "",
        )
        or ""
    ).casefold()
    if "residual" in publication or closure == "closed_with_residual_risk":
        return SavedResearchStatus.RESIDUAL_DILIGENCE
    return SavedResearchStatus.COMPLETED


def _save_displayable_result(
    result: ResearchResult,
    *,
    run_id: str,
    operation_kind: str,
    operation_label: str,
    run_status: str,
    preferred_route: str,
    setup_summary: dict,
    storage_scope: SavedResearchScope,
    scope_id: str,
    created_at: str = "",
    completed_at: str = "",
    parent_artifact_id: str = "",
) -> str:
    status = _saved_status_for_result(result, run_status)
    outcome = "completed"
    if operation_kind in {"targeted_investigation", "action_investigation"}:
        remaining = _remaining_targeted_action(
            result,
            type(
                "SavedOperation",
                (),
                {
                    "investigation_action_id": str(
                        setup_summary.get("investigation_action_id") or ""
                    ),
                    "investigation_action_kind": str(
                        setup_summary.get("investigation_action_kind") or ""
                    ),
                    "investigation_semantic_key": str(
                        setup_summary.get("investigation_semantic_key") or ""
                    ),
                },
            )(),
        )
        outcome = "unresolved" if remaining is not None else "resolved"
    elif status in {SavedResearchStatus.DRAFT, SavedResearchStatus.PARTIAL}:
        outcome = "partial"
    elif status is SavedResearchStatus.FAILED:
        outcome = "failed"
    hybrid = getattr(result, "hybrid_research", None)
    lifecycle_state = str(
        getattr(hybrid, "lifecycle_state", "")
        or ""
    )
    if not lifecycle_state:
        lifecycle_state = (
            "completed_not_publishable"
            if status in {SavedResearchStatus.COMPLETED, SavedResearchStatus.RESIDUAL_DILIGENCE}
            else "research_attempt_finished"
        )
    command = SaveResearchResultCommand(
        result=result,
        run_id=run_id,
        operation_kind=operation_kind,
        operation_label=operation_label,
        lifecycle_state=lifecycle_state,
        outcome=outcome,
        status=status,
        origin=(
            SavedResearchOrigin.LEGACY_ADOPTED
            if operation_kind == "legacy_adopted"
            else SavedResearchOrigin.REPAIR_RECHECK
            if operation_kind in {"repair_recheck", "repair_and_recheck"}
            else SavedResearchOrigin.TARGETED_INVESTIGATION
            if operation_kind in {
                "targeted_investigation",
                "action_investigation",
                "event_investigation",
                "analog_investigation",
            }
            else SavedResearchOrigin.LIVE_RESEARCH
        ),
        created_at=created_at,
        completed_at=completed_at,
        preferred_route=preferred_route,
        setup_summary=setup_summary,
        storage_scope=storage_scope,
        scope_id=scope_id,
        parent_artifact_id=parent_artifact_id,
        supersedes_artifact_id=(
            parent_artifact_id
            if status in {
                SavedResearchStatus.COMPLETED,
                SavedResearchStatus.RESIDUAL_DILIGENCE,
            }
            else ""
        ),
    )
    artifact = _saved_research_service_for_scope(storage_scope).save_result(command)
    return artifact.artifact_id


def _persist_hybrid_job_result(state) -> str:
    if not _is_displayable_research_result(getattr(state, "result", None)):
        return ""
    storage_scope = SavedResearchScope(str(state.saved_storage_scope))
    setup = dict(getattr(state, "saved_setup_summary", {}) or {})
    setup.update({
        "investigation_action_id": str(getattr(state, "investigation_action_id", "") or ""),
        "investigation_action_kind": str(getattr(state, "investigation_action_kind", "") or ""),
        "investigation_semantic_key": str(getattr(state, "investigation_semantic_key", "") or ""),
    })
    artifact_id = _save_displayable_result(
        state.result,
        run_id=state.job_id,
        operation_kind=state.operation_kind,
        operation_label=state.operation_label,
        run_status=(state.result_terminal_status or state.status),
        preferred_route=state.result_route,
        setup_summary=setup,
        storage_scope=storage_scope,
        scope_id=state.saved_scope_id,
        created_at=state.created_at,
        completed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        parent_artifact_id=state.originating_artifact_id,
    )
    try:
        _research_run_service().record_event(
            state.job_id,
            stage="result_persistence",
            status="completed",
            summary="Displayable research result saved for zero-call loading.",
            details={"saved_artifact_id": artifact_id},
            idempotency_key=f"result-saved:{artifact_id}",
        )
    except (KeyError, ValueError, OSError):
        pass
    return artifact_id


@st.cache_resource
def _hybrid_job_manager(
    _schema_version: str = HYBRID_JOB_MANAGER_SCHEMA_VERSION,
) -> HybridResearchJobManager:
    return HybridResearchJobManager(
        _research_run_service(),
        result_persister=_persist_hybrid_job_result,
    )


@st.cache_resource(ttl=30, show_spinner=False)
def _local_codex_readiness():
    return local_codex_readiness(config.CODEX_EXECUTABLE)


def _mark_auditor_backend_user_selected() -> None:
    st.session_state._independent_qa_backend_user_selected = True


def _preferred_auditor_backend(
    *,
    local_codex_status: str,
    primary_research_ready: bool,
    configured_secondary_available: bool,
) -> str:
    """Choose the lowest-friction independent QA path for a new user."""
    if (
        primary_research_ready
        and local_codex_status in {"ready", "not_authenticated"}
    ):
        return "codex_local"
    if configured_secondary_available:
        return "configured_secondary"
    return "openai_responses"


def _handle_decision_page_intent(
    result: ResearchResult,
    intent: DecisionPageIntent | None,
    *,
    tiingo_key: str = "",
    eodhd_key: str = "",
) -> None:
    """Execute action-card intents outside presentation rendering."""

    if intent is None:
        return
    if _has_active_research_task():
        st.warning(
            "Another research task is active. Finish or cancel it before selecting "
            "a different investigation; no hidden queue was created."
        )
        return
    canonical_actions = canonicalize_investigation_actions(
        result.identity.ticker,
        result.prioritized_research_queue,
    )
    canonical_action_id = (
        canonical_actions.resolve_action_id(intent.action_id)
        or intent.action_id
    )
    action = next(
        (
            item for item in canonical_actions.actions
            if item.action_id == canonical_action_id
        ),
        None,
    )
    if action is None:
        st.error("This investigation action is no longer present in the run.")
        return
    if intent.intent_type == "upload_price_data":
        aliases = canonical_actions.aliases_by_canonical_id.get(
            canonical_action_id
        )
        mutable_action = next((
            item for item in result.prioritized_research_queue
            if aliases is not None
            and str(getattr(item, "action_id", "") or "")
            in aliases.action_ids
        ), None)
        if mutable_action is not None:
            action = mutable_action
    if intent.intent_type != "upload_price_data":
        source_families = tuple(
            str(value)
            for value in list(getattr(action, "required_source_families", ()) or ())
            if value
        )
        connectors = tuple(
            str(value)
            for value in list(getattr(action, "preferred_connectors", ()) or ())
            if value
        )
        current_setup = dict(
            st.session_state.get("_current_research_setup") or {}
        )
        parent = _displayed_parent_context(result)
        _, workspace_scope = _saved_research_scope()
        launch_intent = build_research_launch_intent(
            ticker=result.identity.ticker,
            operation_kind=ResearchOperationKind.ACTION_INVESTIGATION,
            operation_label=str(action.title or "Selected investigation"),
            objective=str(action.objective or action.title),
            launch_origin="decision_investigate_next",
            research_profile=str(
                current_setup.get("profile") or "adaptive_ic"
            ),
            preferred_result_route="Evidence",
            action_id=str(action.action_id or ""),
            action_kind=str(action.action_kind or ""),
            action_semantic_key=str(action.semantic_key or action.action_id or ""),
            research_intent_id=str(getattr(action, "intent_id", "") or ""),
            action_semantic_aliases=tuple(
                str(value) for value in
                list(getattr(action, "alias_semantic_keys", ()) or ())
                if value
            ),
            event_id=str(intent.event_id or action.event_id or ""),
            required_source_families=source_families,
            preferred_connectors=connectors,
            qa_enabled=bool(current_setup.get("qa_resolved")),
            workspace_scope=workspace_scope,
            needs_source_setup=intent.intent_type == "configure_source",
            fingerprint_context={
                "source_policy": current_setup,
                "action_kind": str(action.action_kind or ""),
            },
            **parent,
        )
        _stage_research_intent(launch_intent)
        if intent.intent_type == "configure_source":
            st.session_state._open_additional_sources = True
            st.session_state._investigation_queue_notice = (
                "Investigation selected. Complete source setup, then explicitly "
                "confirm the task in Next research task. No research has started."
            )
        else:
            st.session_state._investigation_queue_notice = (
                "Investigation selected. Review its exact scope in Next research "
                "task, then choose Run selected investigation. No providers were called."
            )
        st.rerun()
        return
    qa_run = getattr(result, "independent_research_qa", None)
    parent_run_id = str(
        st.session_state.get("research_lifecycle_run_id")
        or getattr(qa_run, "run_id", "")
        or "displayed-result"
    )
    evidence_hash = str(getattr(qa_run, "evidence_hash", "") or "")
    source_fingerprint = str(
        getattr(qa_run, "source_policy_fingerprint", "") or ""
    )
    event = next(
        (
            item for item in result.events
            if event_identifier(item) == (intent.event_id or action.event_id)
        ),
        result.events[0] if result.events else None,
    )
    if event is None or not event.event_date:
        action.blocking_reason = "No dated source event is available for attribution."
        st.error(action.blocking_reason)
        return
    selected_event_id = event_identifier(event)
    command = InvestigationActionCommand(
        parent_run_id=parent_run_id,
        parent_evidence_hash=evidence_hash,
        action_id=action.action_id,
        ticker=result.identity.ticker,
        action_kind=action.action_kind,
        event_id=selected_event_id,
        registered_connector_ids=("manual_adjusted_csv",)
        if intent.intent_type == "upload_price_data"
        else ("stooq", "tiingo", "eodhd"),
        source_policy_fingerprint=source_fingerprint,
        input_fingerprint=(
            hashlib.sha256(intent.uploaded_csv).hexdigest()
            if intent.uploaded_csv else ""
        ),
    )

    def execute(target: InvestigationActionCommand) -> InvestigationActionResult:
        store = ResearchStore()
        attempted = list(target.registered_connector_ids)
        try:
            if intent.intent_type == "upload_price_data":
                validated = parse_event_price_csv(
                    intent.uploaded_csv,
                    ticker=target.ticker,
                    event_date=event.event_date or "",
                    sector_ticker="XLK" if target.ticker == "AAPL" else "",
                )
                for symbol, rows in validated.items():
                    if symbol.startswith("_"):
                        continue
                    store.save_price_bars(
                        symbol,
                        "User adjusted CSV",
                        rows,
                        adjusted=True,
                        official=False,
                        source_url="session-upload",
                    )
            client = StooqPriceClient(
                store=store,
                tiingo_key=tiingo_key or None,
                eodhd_key=eodhd_key or None,
            )
            reaction = client.event_window_reaction(
                target.ticker,
                target.event_id,
                event.event_date,
                event.event_timestamp,
                "SPY",
                "XLK" if target.ticker == "AAPL" else None,
            )
            result.event_window_reactions[target.event_id] = reaction
            attach_driver_attributions(
                result.ideas,
                result.event_window_reactions,
                result.external_evidence,
                result.expectations_bridge,
                ticker=target.ticker,
            )
            available = reaction.status in {"available", "window_pending"}
            qa_pending = bool(available and qa_run is not None)
            action.status = "closed" if available else "open"
            action.attempt_summary = (
                f"{reaction.source}: {reaction.status}. {reaction.reason}".strip()
            )
            action.blocking_reason = "" if available else reaction.reason
            action.resolution_summary = (
                "Attribution updated · QA closure pending"
                if qa_pending
                else "Event attribution completed"
                if available
                else ""
            )
            return InvestigationActionResult(
                command=target,
                status="completed" if available else "partial",
                summary=action.resolution_summary or action.attempt_summary,
                attempted_connectors=tuple(attempted),
                result_payload=asdict(reaction),
                qa_closure_state="pending" if qa_pending else "not_required",
            )
        except Exception as exc:
            action.status = "open"
            action.blocking_reason = str(exc)
            action.attempt_summary = "Targeted attribution failed validation."
            return InvestigationActionResult(
                command=target,
                status="failed",
                summary=str(exc),
                attempted_connectors=tuple(attempted),
                failure_class=type(exc).__name__,
            )

    action_result = _investigation_action_service().execute(command, execute)
    if action_result.status == "completed":
        st.success(action_result.summary)
    elif action_result.status == "partial":
        st.warning(action_result.summary)
    else:
        st.error(action_result.summary)


def _handle_publication_repair_intent(
    result: ResearchResult,
    intent: PublicationRepairIntent | None,
) -> None:
    """Queue a bounded repair/recheck run outside presentation rendering."""

    if intent is None or intent.intent_type != "repair_and_recheck":
        return
    if _has_active_research_task():
        st.warning(
            "Another research task is active. Finish or cancel it before selecting "
            "repair and recheck; no hidden queue was created."
        )
        return
    hybrid = getattr(result, "hybrid_research", None)
    if hybrid is None:
        st.error("This result has no repairable research artifact.")
        return
    current_setup = dict(st.session_state.get("_current_research_setup") or {})
    _, workspace_scope = _saved_research_scope()
    parent = _displayed_parent_context(result)
    if not parent["parent_run_id"]:
        st.error(
            "Repair and recheck is unavailable because this displayed result "
            "has no recoverable parent run. Reload a saved live result first."
        )
        return
    run_id = parent["parent_run_id"]
    launch_intent = build_research_launch_intent(
        ticker=result.identity.ticker,
        operation_kind=ResearchOperationKind.REPAIR_RECHECK,
        operation_label="Repair and recheck publication readiness",
        objective=(
            "Repair material blockers, revalidate affected claims, revise the "
            "artifact, recompute quality, and rerun closure."
        ),
        launch_origin="audit_publication_readiness",
        research_profile=str(current_setup.get("profile") or "adaptive_ic"),
        preferred_result_route="Audit",
        repair_action_ids=tuple(intent.action_ids),
        repair_retry_stages=tuple(intent.retry_stages),
        qa_enabled=True,
        workspace_scope=workspace_scope,
        fingerprint_context={
            "repair_action_ids": tuple(intent.action_ids),
            "repair_retry_stages": tuple(intent.retry_stages),
        },
        **parent,
    )
    _stage_research_intent(launch_intent)
    lifecycle = _research_run_service()
    if run_id and lifecycle.inspect(run_id) is not None:
        lifecycle.record_event(
            run_id,
            stage="publication_repair",
            status="selected",
            summary=(
                "Material publication repairs were selected for explicit launch "
                "confirmation."
            ),
            details={"action_ids": list(intent.action_ids)},
            idempotency_key=(
                "publication-repair:"
                + hashlib.sha256(
                    "|".join(intent.action_ids).encode("utf-8")
                ).hexdigest()[:20]
            ),
        )
    st.session_state._publication_repair_notice = (
        "Repair and recheck selected. Review the exact task in the sidebar, then "
        "confirm it. No provider or connector has started."
    )
    st.rerun()


def _demo_case_runtime_metadata(case) -> dict[str, object]:
    """Normalize demo metadata across Streamlit hot-reload schema versions."""
    metadata: dict[str, object] = {
        "content_version": getattr(case, "content_version", "") or "",
        "refreshed_at": getattr(case, "refreshed_at", "") or "",
        "research_profile": getattr(case, "research_profile", "") or "",
        "budget_mode": getattr(case, "budget_mode", "") or "",
        "enabled_layers": tuple(getattr(case, "enabled_layers", ()) or ()),
    }
    if not hasattr(case, "content_version") and not hasattr(case, "refreshed_at"):
        return metadata
    published = load_frozen_hybrid_run(str(getattr(case, "ticker", "") or ""))
    if published is None:
        return metadata
    published_at = str(
        getattr(published, "published_at", "")
        or getattr(published, "completed_at", "")
        or ""
    )
    published_date = published_at[:10]
    if published_date:
        metadata["content_version"] = (
            "Deep Initiation + Independent Research QA "
            + published_date.replace("-", ".")
        )
        metadata["refreshed_at"] = published_date
    metadata["enabled_layers"] = tuple(dict.fromkeys((
        *tuple(metadata["enabled_layers"]),
        "Published Independent Research QA",
    )))
    return metadata


def _default_macro_policy() -> str:
    if config.GLOBAL_MACRO_MODE:
        return "Global diagnostic"
    return "Adaptive"


def _research_setup_snapshot(
    *,
    ticker: str,
    profile: str,
    budget_mode: str,
    macro_policy: str,
    macro_sources: list[str] | tuple[str, ...],
    wisburg_enabled: bool,
    llm_enabled: bool,
    hybrid_enabled: bool,
    consensus_source_count: int,
    market_source_count: int,
    qa_requested: bool | None = None,
    qa_resolved: bool | None = None,
    qa_provider: str = "",
) -> dict[str, object]:
    """Return a redacted UI snapshot of exactly what the next live run will use."""
    return {
        "ticker": ticker.upper(),
        "profile": profile,
        "budget_mode": budget_mode,
        "macro_policy": macro_policy,
        "macro_sources": tuple(macro_sources),
        "wisburg_enabled": bool(wisburg_enabled),
        "llm_enabled": bool(llm_enabled),
        "hybrid_enabled": bool(hybrid_enabled),
        "qa_requested": bool(
            hybrid_enabled if qa_requested is None else qa_requested
        ),
        "qa_resolved": bool(
            hybrid_enabled if qa_resolved is None else qa_resolved
        ),
        "qa_provider": qa_provider,
        "consensus_source_count": int(consensus_source_count),
        "market_source_count": int(market_source_count),
    }


def _format_research_setup(
    setup: dict[str, object] | None,
    *,
    qa_label: str = "",
) -> str:
    if not setup:
        return "Setup not recorded"
    profile_id = str(setup.get("profile") or "")
    profile = {
        "fast_screening": "Fast Screening",
        "adaptive_ic": "Adaptive IC Research",
        "deep_initiation": "Deep Initiation",
        "investigate_event": "Focused event investigation",
    }.get(profile_id, profile_id.replace("_", " ").title() or "Current depth")
    macro_policy = str(setup.get("macro_policy") or "Off")
    qa_requested = bool(setup.get("qa_requested", setup.get("hybrid_enabled")))
    qa_resolved = bool(setup.get("qa_resolved", setup.get("hybrid_enabled")))
    qa_provider = str(setup.get("qa_provider") or "")
    if not qa_label:
        qa_label = (
            f"Ready ({qa_provider})" if qa_resolved and qa_provider
            else "Ready" if qa_resolved
            else "Requested - auditor unavailable" if qa_requested
            else "Off"
        )
    return " | ".join([
        profile,
        "Data: Best available",
        f"Macro: {macro_policy}",
        f"Wisburg: {'On' if setup.get('wisburg_enabled') else 'Off'}",
        f"Research QA: {qa_label}",
    ])


def _displayed_qa_label(result: ResearchResult) -> str:
    qa = getattr(result, "independent_research_qa", None)
    if qa is None:
        return "Not included in this artifact"
    status = str(getattr(qa, "status", "") or "Not completed")
    return (
        "Completed"
        if status.casefold() == "completed"
        else status.replace("_", " ").strip().title()
    )


def _friendly_provider_identity(provider: str, model: str) -> str:
    provider_label = {
        "codex_local": "Local Codex",
        "openai_responses": "OpenAI Responses API",
        "configured_secondary": "Configured secondary provider",
    }.get(provider.casefold(), provider.replace("_", " ").title())
    return " / ".join(value for value in (provider_label, model) if value)


def _demo_research_setup(case) -> dict[str, object]:
    metadata = _demo_case_runtime_metadata(case) if case else {}
    layers = tuple(str(item) for item in metadata.get("enabled_layers", ()))
    layer_text = " ".join(layers).lower()
    setup = _research_setup_snapshot(
        ticker=getattr(case, "ticker", "") or "",
        profile=str(metadata.get("research_profile") or "deep_initiation"),
        budget_mode=str(metadata.get("budget_mode") or "Premium"),
        macro_policy="Frozen fixture" if "macro" in layer_text else "Off",
        macro_sources=[],
        wisburg_enabled="wisburg" in layer_text,
        llm_enabled="llm" in layer_text or "deepseek" in layer_text,
        hybrid_enabled="gpt" in layer_text or "dual" in layer_text,
        consensus_source_count=0,
        market_source_count=0,
        qa_requested="gpt" in layer_text or "dual" in layer_text,
        qa_resolved="gpt" in layer_text or "dual" in layer_text,
        qa_provider=(
            "Frozen artifact"
            if "gpt" in layer_text or "dual" in layer_text
            else ""
        ),
    )
    ticker = str(getattr(case, "ticker", "") or "").upper()
    published = load_frozen_hybrid_run(ticker) if ticker else None
    bridge = getattr(published, "cash_conversion_recurrence", None)
    if bridge is not None:
        question = (
            f"Test whether {ticker} cash conversion remains recurring after "
            "working capital, capex, taxes, financing, and capital returns"
        )
        setup.update({
            "operation_kind": "action_investigation",
            "operation_label": question,
            "operation_objective": question,
            "investigation_action_kind": "cash_conversion_analysis",
            "research_intent_id": str(getattr(bridge, "intent_id", "") or ""),
        })
    return setup


def _inject_global_styles() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"],
        [data-testid="stMain"] {
            overflow-x: hidden;
        }
        [data-testid="stHorizontalBlock"] > [data-testid="column"] {
            min-width: 0;
        }
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stAlertContent"],
        [data-testid="stMetricValue"],
        [data-testid="stMetricLabel"] {
            overflow-wrap: anywhere;
            word-break: normal;
        }
        .stTabs [data-baseweb="tab-list"] {
            max-width: 100%;
            overflow-x: auto;
            scrollbar-width: thin;
        }
        @media (max-width: 700px) {
            .block-container {
                padding-left: 1rem;
                padding-right: 1rem;
            }
            [data-testid="stHorizontalBlock"] {
                flex-direction: column;
                gap: 0.6rem;
            }
            [data-testid="stHorizontalBlock"] > [data-testid="column"] {
                width: 100%;
                flex: 1 1 auto;
            }
            [data-testid="stMetric"] {
                min-height: auto;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _is_displayable_research_result(value: object) -> bool:
    """Accept compatible results across Streamlit hot-reload class identities."""

    return bool(
        value is not None
        and getattr(value, "identity", None) is not None
        and getattr(value, "run_manifest", None) is not None
        and hasattr(value, "valuation")
        and hasattr(value, "ideas")
    )


def _remaining_targeted_action(result: object, state: object):
    """Find the selected action when a completed pass did not close it."""

    action_id = str(getattr(state, "investigation_action_id", "") or "")
    semantic_key = str(
        getattr(state, "investigation_semantic_key", "") or ""
    )
    action_kind = str(
        getattr(state, "investigation_action_kind", "") or ""
    )
    original_queue = list(
        getattr(result, "prioritized_research_queue", ()) or ()
    )
    direct_match = next((
        item for item in original_queue
        if (action_id and (
            str(getattr(item, "action_id", "") or "") == action_id
            or action_id in list(
                getattr(item, "alias_action_ids", ()) or ()
            )
            or action_id in list(
                getattr(item, "related_research_request_ids", ()) or ()
            )
            or action_id in list(
                getattr(item, "related_work_order_ids", ()) or ()
            )
        ))
        or (semantic_key and (
            str(getattr(item, "semantic_key", "") or "") == semantic_key
            or semantic_key in list(
                getattr(item, "alias_semantic_keys", ()) or ()
            )
        ))
    ), None)
    if direct_match is not None:
        return direct_match
    identity = getattr(result, "identity", None)
    canonical_actions = canonicalize_investigation_actions(
        str(getattr(identity, "ticker", "") or ""),
        original_queue,
    )
    queue = list(canonical_actions.actions)
    canonical_action_id = (
        canonical_actions.resolve_action_id(action_id) or action_id
    )
    canonical_semantic_action_id = (
        canonical_actions.resolve_action_id(semantic_key) or ""
    )
    match = next(
        (
            item for item in queue
            if (canonical_action_id and (
                str(getattr(item, "action_id", "") or "") == canonical_action_id
                or action_id in list(
                    getattr(item, "related_research_request_ids", ()) or ()
                )
                or action_id in list(
                    getattr(item, "related_work_order_ids", ()) or ()
                )
            ))
            or (
                canonical_semantic_action_id
                and str(getattr(item, "action_id", "") or "")
                == canonical_semantic_action_id
            )
        ),
        None,
    )
    if match is None and action_kind:
        same_kind = [
            item for item in queue
            if str(getattr(item, "action_kind", "") or "") == action_kind
        ]
        match = same_kind[0] if len(same_kind) == 1 else None
    return match


def _apply_loaded_saved_research(loaded, *, show_notice: bool = True) -> None:
    loaded, refresh_notice = _refresh_loaded_cash_conversion_if_needed(loaded)
    artifact = loaded.artifact
    is_draft = artifact.status in {
        SavedResearchStatus.DRAFT,
        SavedResearchStatus.PARTIAL,
        SavedResearchStatus.FAILED,
    }
    st.session_state.result = loaded.result
    st.session_state.demo_mode = None
    st.session_state._displayed_research_setup = dict(artifact.setup_summary or {})
    displayed_context = build_displayed_research_context(artifact)
    st.session_state._displayed_research_context = displayed_context
    st.session_state._displayed_investigation_reference = (
        build_displayed_investigation_reference(displayed_context)
    )
    st.session_state._displayed_saved_artifact_id = artifact.artifact_id
    st.session_state._displayed_parent_run_id = artifact.run_id
    st.session_state._displayed_saved_storage_scope = artifact.storage_scope
    st.session_state._displayed_saved_scope_id = artifact.scope_id
    st.session_state._displayed_result_origin = (
        "saved_research_draft"
        if is_draft
        else "saved_live_research"
    )
    st.session_state._displayed_operation_label = artifact.operation_label
    st.session_state._displayed_outcome = artifact.outcome
    st.session_state.pop("_prepared_session_result_download", None)
    UiSessionState(st.session_state).request_route(
        artifact.preferred_route or "Decision",
        origin="saved_research_load",
    )
    _saved_research_service_for_scope(artifact.storage_scope).remember_displayed(
        artifact.artifact_id,
        scope_id=artifact.scope_id,
    )
    if show_notice:
        st.session_state._saved_research_load_notice = (
            refresh_notice
            or (
                f"Loaded {'saved research draft' if is_draft else 'saved live research'} "
                f"from {_display_timestamp(artifact.completed_at)}. "
                "No providers were called."
            )
        )


def _refresh_loaded_cash_conversion_if_needed(loaded):
    """Derive a cutoff-safe bridge for a legacy focused result, once per session."""

    artifact = loaded.artifact
    result = loaded.result
    if getattr(result, "demo_case", None):
        # Frozen examples must remain reproducible and network-free.  A demo is
        # refreshed only by the explicit publication workflow, never on load.
        return loaded, ""
    # ``analysis_capabilities`` is metadata written by earlier app versions.  It
    # is useful for discovery, but it is not proof that the decoded payload
    # actually contains a complete, comparable-period bridge.  In particular,
    # some pre-upgrade focused investigations advertised the capability while
    # retaining only the one-period legacy cash summary.  Inspect the payload
    # itself so loading that result repairs the missing deterministic analysis
    # instead of showing a misleading completed answer.
    if _has_current_cash_conversion_bridge(result):
        return loaded, ""
    setup = dict(getattr(artifact, "setup_summary", {}) or {})
    if not _is_focused_cash_conversion_result(artifact, result, setup):
        return loaded, ""
    cutoff = str(getattr(artifact, "data_cutoff", "") or "")[:10]
    if not cutoff:
        return loaded, "Historical SEC evidence was not refreshed because the saved result has no enforceable data cutoff."
    recovery_service = _saved_research_service_for_scope(artifact.storage_scope)
    intent_id = "cash_generation_capital_allocation:recurring_cash_conversion"
    base_key = ":".join((
        str(artifact.artifact_id), intent_id, CASH_CONVERSION_ANALYSIS_VERSION, cutoff,
    ))
    recovery_key = hashlib.sha256(base_key.encode("utf-8")).hexdigest()[:32]
    recorded = recovery_service.get_recovery_attempt(
        recovery_key, scope_id=artifact.scope_id
    )
    if recorded is not None and recorded.status == "recovered" and recorded.derived_artifact_id:
        try:
            recovered = recovery_service.load_result(
                recorded.derived_artifact_id, scope_id=artifact.scope_id
            )
        except (OSError, ValueError, SavedResearchError):
            recovered = None
        if recovered is not None and _has_current_cash_conversion_bridge(
            recovered.result, expected_cutoff=cutoff
        ):
            return recovered, (
                "Loaded a verified derived cash-conversion revision. No LLM or Codex call was made; "
                "Independent QA is pending for the refreshed answer."
            )
    if recorded is not None and recorded.status == "blocked":
        return loaded, (
            "Historical comparison unavailable. "
            + (recorded.readable_reason or "The prior deterministic recovery could not complete.")
        )
    if recorded is not None and recorded.status == "running":
        return loaded, (
            "Historical comparison unavailable. A prior deterministic recovery was interrupted; "
            "the original saved result was preserved."
        )
    derived = _load_verified_cash_conversion_revision(artifact, expected_cutoff=cutoff)
    if derived is not None:
        recovery_service.save_recovery_attempt(DerivedEvidenceRecoveryAttempt(
            recovery_key=recovery_key,
            scope_id=artifact.scope_id,
            parent_artifact_id=artifact.artifact_id,
            intent_id=intent_id,
            data_cutoff=cutoff,
            analysis_version=CASH_CONVERSION_ANALYSIS_VERSION,
            status="recovered",
            attempted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            derived_artifact_id=derived.artifact.artifact_id,
            readable_reason="A verified derived revision already existed.",
        ))
        return (
            derived,
            "Loaded a verified derived cash-conversion revision. No LLM or Codex call was made; Independent QA is pending for the refreshed answer.",
        )
    recovery_service.save_recovery_attempt(DerivedEvidenceRecoveryAttempt(
        recovery_key=recovery_key,
        scope_id=artifact.scope_id,
        parent_artifact_id=artifact.artifact_id,
        intent_id=intent_id,
        data_cutoff=cutoff,
        analysis_version=CASH_CONVERSION_ANALYSIS_VERSION,
        status="running",
        attempted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        readable_reason="Refreshing historical SEC evidence for this answer.",
    ))
    try:
        identity = getattr(result, "identity", None)
        cik = str(getattr(identity, "cik", "") or "")
        if not identity or not cik:
            raise ValueError("The saved result has no SEC issuer identity.")
        facts = SecClient().get_company_facts(cik)
        snapshot_hash = hashlib.sha256(
            json.dumps(facts, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        normalized_objects = list(getattr(result, "normalized_sources", ()) or ())
        normalized = [asdict(item) for item in normalized_objects]
        evidence_catalog = [
            source_record_to_evidence(item) for item in normalized_objects
        ]
        context = {
            "ticker": str(getattr(identity, "ticker", "") or artifact.ticker),
            "company": str(getattr(identity, "name", "") or artifact.company_name),
            "security_key": str(artifact.security_key or "cik:" + cik),
            "exchange": str(getattr(identity, "exchange", "") or artifact.exchange),
            "data_cutoff": cutoff,
            "security_archetype": str(
                setup.get("security_archetype")
                or getattr(result, "security_archetype", "")
                or "operating_company"
            ),
            "normalized_sources": normalized,
            "searchable_evidence_catalog": evidence_catalog,
            "claim_eligible_evidence_ids": [
                str(item.get("evidence_id") or "") for item in evidence_catalog
                if item.get("evidence_id")
            ],
        }
        retriever = AuditableEvidenceRetriever(
            identity,
            company_facts=facts,
            metrics=list(getattr(result, "metrics", ()) or ()),
            filings=list(getattr(result, "filings", ()) or ()),
        )
        gateway = ContextResearchToolGateway(
            context,
            SourceCapabilityRegistry(),
            retrieval_engine=retriever,
        )
        command = DeterministicEvidenceRefreshCommand(
            parent_artifact_id=artifact.artifact_id,
            ticker=artifact.ticker,
            intent_id=intent_id,
            data_cutoff=cutoff,
            idempotency_key=hashlib.sha256(
                f"{base_key}:{snapshot_hash}".encode("utf-8")
            ).hexdigest()[:24],
        )
        refreshed = DeterministicCashConversionRefreshService().refresh(
            command,
            result=result,
            gateway=gateway,
        )
        if refreshed.status != "refreshed" or refreshed.result is None:
            raise ValueError(refreshed.failure_reason or refreshed.summary)
        bridge = _cash_conversion_bridge_for_result(refreshed.result)
        derived_setup = {
            **setup,
            "operation_kind": str(artifact.operation_kind),
            "operation_label": str(artifact.operation_label),
            "research_intent_id": str(
                getattr(bridge, "intent_id", "")
                or "cash_generation_capital_allocation:recurring_cash_conversion"
            ),
            "deterministic_evidence_refresh": CASH_CONVERSION_ANALYSIS_VERSION,
            "analysis_version": str(getattr(bridge, "analysis_version", "") or ""),
            "source_snapshot_hash": str(getattr(bridge, "source_snapshot_hash", "") or ""),
            "cash_conversion_bridge_signature": str(
                getattr(bridge, "evidence_hash", "") or ""
            ),
            "qa_freshness": str(getattr(bridge, "qa_freshness", "pending") or "pending"),
        }
        refreshed_id = _save_displayable_result(
            refreshed.result,
            run_id=str(artifact.run_id),
            operation_kind=str(artifact.operation_kind),
            operation_label=str(artifact.operation_label),
            run_status="completed",
            preferred_route=str(artifact.preferred_route or "Evidence"),
            setup_summary=derived_setup,
            storage_scope=artifact.storage_scope,
            scope_id=artifact.scope_id,
            created_at=artifact.created_at,
            completed_at=artifact.completed_at,
            parent_artifact_id=artifact.artifact_id,
        )
        derived = _saved_research_service_for_scope(
            artifact.storage_scope
        ).load_result(refreshed_id, scope_id=artifact.scope_id)
        notice = (
            "Historical SEC evidence was refreshed and saved as a derived revision. "
            "No LLM or Codex call was made. Independent QA is pending for the refreshed answer."
        )
        recovery_service.save_recovery_attempt(DerivedEvidenceRecoveryAttempt(
            recovery_key=recovery_key,
            scope_id=artifact.scope_id,
            parent_artifact_id=artifact.artifact_id,
            intent_id=intent_id,
            data_cutoff=cutoff,
            analysis_version=CASH_CONVERSION_ANALYSIS_VERSION,
            status="recovered",
            attempted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            source_snapshot_hash=str(getattr(bridge, "source_snapshot_hash", "") or ""),
            derived_artifact_id=refreshed_id,
            readable_reason=notice,
        ))
        return derived, notice
    except (SecClientError, SavedResearchError, SavedResearchCodecError, OSError, ValueError) as exc:
        notice = f"Historical comparison unavailable. Historical SEC series could not be completed: {exc}"
        recovery_service.save_recovery_attempt(DerivedEvidenceRecoveryAttempt(
            recovery_key=recovery_key,
            scope_id=artifact.scope_id,
            parent_artifact_id=artifact.artifact_id,
            intent_id=intent_id,
            data_cutoff=cutoff,
            analysis_version=CASH_CONVERSION_ANALYSIS_VERSION,
            status="blocked",
            attempted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            readable_reason=notice,
        ))
        return loaded, notice


def _load_verified_cash_conversion_revision(artifact, *, expected_cutoff: str = ""):
    """Return the newest verified derived bridge for a legacy parent artifact.

    The lookup examines decoded payloads, rather than treating the metadata
    capability flag as proof.  It performs only local storage I/O and codec
    verification—never a provider, model, or SEC call.
    """

    service = _saved_research_service_for_scope(artifact.storage_scope)
    try:
        summaries = service.list_results(
            SavedResearchQuery(
                scope_id=artifact.scope_id,
                ticker=artifact.ticker,
                include_drafts=True,
                limit=100,
            )
        )
    except (OSError, ValueError, SavedResearchError):
        return None
    candidates = sorted(
        (
            item for item in summaries
            if str(getattr(item, "parent_artifact_id", "") or "")
            == str(artifact.artifact_id)
            and getattr(item, "health", None) is SavedArtifactHealth.VERIFIED
        ),
        key=lambda item: str(getattr(item, "saved_at", "") or ""),
        reverse=True,
    )
    for candidate in candidates:
        try:
            loaded = service.load_result(candidate.artifact_id, scope_id=candidate.scope_id)
        except (OSError, ValueError, SavedResearchError):
            continue
        if _has_current_cash_conversion_bridge(
            loaded.result, expected_cutoff=expected_cutoff
        ):
            return loaded
    return None


def _cash_conversion_bridge_for_result(result):
    """Return the bridge persisted by either current or transitional payloads."""

    bridge = getattr(result, "cash_conversion_recurrence", None)
    if bridge is not None:
        return bridge
    return getattr(
        getattr(result, "hybrid_research", None),
        "cash_conversion_recurrence",
        None,
    )


def _has_current_cash_conversion_bridge(result, *, expected_cutoff: str = "") -> bool:
    """Whether a loaded result has the complete v1 comparable-TTM analysis."""

    bridge = _cash_conversion_bridge_for_result(result)
    if bridge is None:
        return False
    from equity_research.application.cash_conversion import (
        validate_cash_conversion_recurrence_bridge,
    )
    return validate_cash_conversion_recurrence_bridge(
        bridge,
        expected_cutoff=expected_cutoff,
    ).valid


def _is_focused_cash_conversion_result(artifact, result, setup: dict) -> bool:
    """Identify a targeted cash-conversion result without refreshing broad runs.

    Saved runs from the launch-intent migration used slightly different setup
    keys.  We deliberately accept those aliases, but require both a cash
    mechanism and a focused/repair operation so a normal company refresh does
    not silently initiate an extra SEC retrieval simply because its agenda
    contains a cash-flow question.
    """

    operation_kind = str(getattr(artifact, "operation_kind", "") or "").casefold()
    action_kind = str(setup.get("investigation_action_kind") or "").casefold()
    operation_label = str(getattr(artifact, "operation_label", "") or "")
    focused_kinds = {
        "action_investigation",
        "event_investigation",
        "repair_recheck",
        "canonical_action_investigation",
    }
    focused = operation_kind in focused_kinds or any(
        token in operation_label.casefold()
        for token in ("investigation", "repair", "independent qa")
    )
    if not focused:
        return False

    searchable: list[str] = [operation_label, action_kind]
    for key in (
        "research_intent_id",
        "intent_id",
        "investigation_objective",
        "objective",
        "selected_action_title",
        "selected_action_objective",
    ):
        searchable.append(str(setup.get(key) or ""))
    hybrid = getattr(result, "hybrid_research", None)
    searchable.extend((
        str(getattr(hybrid, "objective", "") or ""),
        str(getattr(hybrid, "research_intent_id", "") or ""),
    ))
    text = " ".join(searchable).casefold().replace("-", " ").replace("_", " ")
    return action_kind == "cash_conversion_analysis" or any(
        token in text
        for token in (
            "cash conversion",
            "cash generation capital allocation",
            "recurring cash conversion",
            "operating cash flow",
            "free cash flow",
        )
    )


def _apply_in_memory_research(payload: dict) -> None:
    """Display a completed result even when restart recovery failed."""

    result = payload.get("result")
    if not _is_displayable_research_result(result):
        return
    st.session_state.pop("_prepared_session_result_download", None)
    st.session_state.result = result
    st.session_state.demo_mode = None
    st.session_state._displayed_saved_artifact_id = ""
    setup_summary = dict(payload.get("setup_summary") or {})
    displayed_context = DisplayedResearchContext(
        run_id=str(payload.get("run_id") or ""),
        operation_kind=str(payload.get("operation_kind") or ""),
        operation_label=str(payload.get("operation_label") or ""),
        operation_action_id=str(
            payload.get("operation_action_id")
            or setup_summary.get("investigation_action_id")
            or ""
        ),
        research_intent_id=str(
            payload.get("research_intent_id")
            or setup_summary.get("research_intent_id")
            or ""
        ),
        evidence_hash=str(payload.get("evidence_hash") or ""),
        outcome=str(payload.get("outcome") or "completed"),
    )
    st.session_state._displayed_research_context = displayed_context
    st.session_state._displayed_investigation_reference = (
        build_displayed_investigation_reference(displayed_context)
    )
    st.session_state._displayed_parent_run_id = str(payload.get("run_id") or "")
    st.session_state._displayed_saved_storage_scope = ""
    st.session_state._displayed_saved_scope_id = ""
    st.session_state._displayed_result_origin = "live_not_saved"
    st.session_state._displayed_research_setup = setup_summary
    st.session_state._displayed_operation_label = str(
        payload.get("operation_label") or "Research"
    )
    st.session_state._displayed_outcome = str(payload.get("outcome") or "completed")
    UiSessionState(st.session_state).request_route(
        str(payload.get("preferred_route") or "Decision"),
        origin="session_result_load",
    )


def _result_adoption_key(result: object) -> str:
    manifest = getattr(result, "run_manifest", None)
    hybrid = getattr(result, "hybrid_research", None)
    raw = {
        "run_id": str(
            getattr(manifest, "run_id", "")
            or getattr(hybrid, "run_id", "")
            or "session-result"
        ),
        "evidence_hash": str(
            getattr(manifest, "admitted_evidence_hash", "")
            or getattr(hybrid, "evidence_hash", "")
            or ""
        ),
        "codec": SAVED_RESEARCH_CODEC_VERSION,
        "runtime": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]


def _load_saved_artifact(artifact_id: str, *, scope_id: str) -> bool:
    try:
        loaded = _saved_research_service().load_result(
            artifact_id,
            scope_id=scope_id,
        )
        _apply_loaded_saved_research(loaded)
        return True
    except (
        SavedResearchError,
        SavedResearchCodecError,
        SavedResearchStorageError,
        OSError,
        ValueError,
    ) as exc:
        st.session_state._saved_research_load_error = {
            "message": str(exc),
            "reason": str(getattr(exc, "reason", "") or "load_failed"),
            "artifact_id": str(getattr(exc, "artifact_id", "") or artifact_id),
            "failure_path": str(getattr(exc, "failure_path", "") or ""),
            "annotation": str(getattr(exc, "annotation", "") or ""),
        }
        return False


def _saved_status_label(status: str) -> str:
    return {
        "completed": "Completed research",
        "residual_diligence": "Completed with residual diligence",
        "draft": "Research draft",
        "partial": "Partial research draft",
        "failed": "Interrupted research draft",
    }.get(str(status or "").casefold(), str(status or "Unavailable").replace("_", " ").title())


def _render_displayed_research_card() -> None:
    result = st.session_state.get("result")
    if result is None:
        return
    active_job_id = str(st.session_state.get("hybrid_job_id") or "")
    active_job = _hybrid_job_manager().get(active_job_id) if active_job_id else None
    ticker = str(getattr(getattr(result, "identity", None), "ticker", "") or "")
    company = str(getattr(getattr(result, "identity", None), "name", "") or ticker)
    st.markdown("**Displayed research**")
    if active_job is not None and active_job.status in {
        "queued", "running", "cancelling", "saving_result"
    }:
        st.info(
            f"Active run · {active_job.ticker}\n\n"
            f"{active_job.operation_label or 'Research in progress'}\n\n"
            f"{active_job.status.replace('_', ' ').title()}"
        )
        st.caption(f"The previously displayed {ticker} result remains readable while this runs.")
        return
    artifact_id = str(st.session_state.get("_displayed_saved_artifact_id") or "")
    if artifact_id:
        storage_scope, scope_id = _saved_research_scope()
        artifact = _saved_research_service_for_scope(storage_scope).repository.get(
            artifact_id,
            scope_id=scope_id,
        )
        if artifact is not None:
            origin = (
                "Saved research draft"
                if artifact.status in {
                    SavedResearchStatus.DRAFT,
                    SavedResearchStatus.PARTIAL,
                    SavedResearchStatus.FAILED,
                }
                else "Saved live research"
            )
            st.info(
                f"{origin} · {ticker}\n\n{company}\n\n"
                f"{artifact.operation_label}\n\n"
                f"{_saved_status_label(artifact.status.value)} · "
                f"{artifact.outcome.replace('_', ' ').title()}"
            )
            st.caption(
                f"Completed {_display_timestamp(artifact.completed_at)}"
            )
            st.caption(
                f"Validation: {artifact.validation_status.replace('_', ' ').title()} · "
                f"Independent QA: {artifact.independent_qa_status.replace('_', ' ').title()}"
            )
            st.caption(
                "Restart recovery: "
                + artifact.health.value.replace("_", " ").title()
            )
            if artifact.data_cutoff:
                st.caption(f"Data cutoff: {_display_timestamp(artifact.data_cutoff)}")
            return
    if getattr(result, "demo_case", None) is not None:
        st.info(f"Frozen demo · {ticker}\n\n{company}\n\nRead-only illustrative research")
        st.caption("Loading this demo does not refresh data or call a provider.")
    else:
        st.warning(f"Live result · {ticker}\n\n{company}\n\nNot saved for restart recovery")


def _render_saved_research_library(current_ticker: str) -> None:
    storage_scope, scope_id = _saved_research_scope()
    service = _saved_research_service_for_scope(storage_scope)
    with st.expander("Saved research", expanded=False):
        if not getattr(service, "persistence_available", True):
            st.warning(
                "Restart recovery is temporarily unavailable. Research can "
                "still run, and completed results remain available in this "
                "app session. Check the local saved-research folder before "
                "closing the app."
            )
        all_tickers = st.checkbox(
            "All tickers",
            value=False,
            key="saved_research_all_tickers",
            help="Off shows only the ticker in the run bar.",
        )
        ticker_search = (
            st.text_input(
                "Find ticker",
                value="",
                key="saved_research_ticker_search",
                placeholder="AAPL",
            ).strip().upper()
            if all_tickers
            else ""
        )
        ticker_filter = ticker_search if all_tickers else current_ticker.upper()
        artifacts = service.list_results(
            SavedResearchQuery(
                scope_id=scope_id,
                ticker=ticker_filter,
                include_drafts=True,
                limit=50,
            )
        )
        view_model = build_saved_research_library_view_model(
            artifacts,
            current_ticker=current_ticker,
            session_only=storage_scope is SavedResearchScope.HOSTED_SESSION,
        )
        if view_model.session_only:
            st.caption("Private to this browser session · not saved after expiry.")
        else:
            total_bytes = sum(item.compressed_size_bytes for item in artifacts)
            st.caption(
                f"{len(view_model.completed)} completed · {len(view_model.drafts)} drafts · "
                f"{total_bytes / (1024 * 1024):.1f} MB shown"
            )
        latest = next((item for item in artifacts if item.is_completed), None)
        if st.button(
            "Load latest completed",
            key="load_latest_saved_research",
            use_container_width=True,
            disabled=latest is None,
        ) and latest is not None:
            loaded_latest = service.load_latest_completed(
                scope_id=scope_id,
                ticker=ticker_filter,
            )
            if loaded_latest is not None:
                _apply_loaded_saved_research(loaded_latest)
                st.rerun()
            else:
                st.session_state._saved_research_load_error = {
                    "message": (
                        "No verified completed result could be opened. "
                        "Select an artifact below to retry its compatibility check."
                    ),
                    "reason": "verification_failed",
                }
                st.rerun()
        if not artifacts:
            if str(st.session_state.get("_displayed_result_origin") or "") == "live_not_saved":
                st.warning(
                    "The displayed result was not saved by this app version or its save "
                    "check failed. It cannot be reconstructed from run metadata alone."
                )
            else:
                st.info(
                    "No saved research for this selection. Completed and partial live runs "
                    "will appear here automatically without using extra provider credits to reopen them."
                )
            return
        group_options = [
            label
            for label, items in (
                ("Completed research", view_model.completed),
                ("Drafts and interrupted work", view_model.drafts),
            )
            if items
        ]
        if str(st.session_state.get("saved_research_group") or "") not in group_options:
            st.session_state.saved_research_group = group_options[0]
        selected_group = (
            st.radio(
                "Library section",
                group_options,
                horizontal=True,
                key="saved_research_group",
            )
            if len(group_options) > 1
            else group_options[0]
        )
        selectable_artifacts = [
            item
            for item in artifacts
            if item.is_completed == (selected_group == "Completed research")
        ]
        st.caption(
            f"{selected_group} · {len(selectable_artifacts)} result"
            f"{'s' if len(selectable_artifacts) != 1 else ''}"
        )
        labels = {
            item.artifact_id: (
                f"{'★ ' if item.pinned else ''}{item.ticker} · {item.operation_label} · "
                f"{_saved_status_label(item.status.value)} · {_display_timestamp(item.completed_at)}"
            )
            for item in selectable_artifacts
        }
        selected_id = st.selectbox(
            "Saved result",
            list(labels),
            format_func=lambda value: labels[value],
            key="saved_research_selection",
        )
        selected = next(
            item for item in selectable_artifacts
            if item.artifact_id == selected_id
        )
        st.markdown(
            f"**{selected.ticker} · {selected.company_name}**\n\n"
            f"{selected.operation_label}"
        )
        st.caption(
            f"Outcome: {selected.outcome.replace('_', ' ')} · "
            f"Depth: {selected.research_profile.replace('_', ' ').title()} · "
            f"QA: {selected.independent_qa_status.replace('_', ' ').title()} · "
            f"Open in {selected.preferred_route}"
        )
        st.caption(
            "Restart recovery: "
            + selected.health.value.replace("_", " ").title()
        )
        if st.button(
            "Load selected",
            key="load_selected_saved_research",
            use_container_width=True,
        ):
            if _load_saved_artifact(selected_id, scope_id=scope_id):
                st.rerun()
        if selected.health is not SavedArtifactHealth.VERIFIED and st.button(
            "Retry compatibility check",
            key="retry_selected_saved_research_compatibility",
            use_container_width=True,
        ):
            if _load_saved_artifact(selected_id, scope_id=scope_id):
                st.rerun()
        if st.button(
            "Use this setup for next run",
            key="use_saved_research_setup",
            use_container_width=True,
        ):
            st.session_state.ticker_input = selected.ticker
            profile = str(selected.setup_summary.get("profile") or selected.research_profile or "")
            if profile:
                st.session_state.research_profile_selector = profile
            st.success("Non-secret ticker and research-depth settings were applied. No research was run.")
        manage = st.checkbox("Manage saved research", key="manage_saved_research")
        if manage:
            manage_cols = st.columns(2)
            if manage_cols[0].button(
                "Unpin" if selected.pinned else "Pin",
                key="pin_selected_saved_research",
                use_container_width=True,
            ):
                service.pin_result(
                    selected.artifact_id,
                    scope_id=scope_id,
                    pinned=not selected.pinned,
                )
                st.rerun()
            if manage_cols[1].button(
                "Delete",
                key="delete_selected_saved_research",
                use_container_width=True,
                disabled=selected.pinned,
            ):
                if service.delete_result(selected.artifact_id, scope_id=scope_id):
                    if str(st.session_state.get("_displayed_saved_artifact_id") or "") == selected.artifact_id:
                        st.session_state._displayed_saved_artifact_id = ""
                        st.session_state._displayed_result_origin = "live_not_saved"
                    st.rerun()
            unpinned_drafts = [
                item for item in artifacts
                if not item.pinned
                and item.status in {
                    SavedResearchStatus.DRAFT,
                    SavedResearchStatus.PARTIAL,
                    SavedResearchStatus.FAILED,
                }
            ]
            if st.button(
                "Remove unpinned drafts",
                key="delete_unpinned_saved_research_drafts",
                use_container_width=True,
                disabled=not unpinned_drafts,
            ):
                deleted_ids = {item.artifact_id for item in unpinned_drafts}
                for item in unpinned_drafts:
                    service.delete_result(item.artifact_id, scope_id=scope_id)
                if str(st.session_state.get("_displayed_saved_artifact_id") or "") in deleted_ids:
                    st.session_state._displayed_saved_artifact_id = ""
                    st.session_state._displayed_result_origin = "live_not_saved"
                st.rerun()


def main() -> None:
    _inject_global_styles()
    # Initialize the durable lifecycle schema even before the first launch.
    # This makes the valid zero-job state inspectable and prevents a different
    # SQLite consumer from creating the shared file without the job tables.
    _research_run_service()
    title_col, repo_col = st.columns([4, 1])
    with title_col:
        st.title("IC Copilot")
        st.caption(
            "Evidence-first equity research: source event -> business driver -> "
            "financial impact -> valuation -> IC decision."
        )
    with repo_col:
        st.link_button("View on GitHub", GITHUB_REPOSITORY_URL, use_container_width=True)
        st.link_button("Give Feedback", FEEDBACK_DISCUSSION_URL, use_container_width=True)
    demo_video = Path(__file__).resolve().parents[2] / "docs" / "assets" / "demo" / "ic-copilot-aapl-demo.mp4"
    if demo_video.exists():
        with st.expander("Watch the 91-second AAPL research walkthrough"):
            st.video(str(demo_video))
            st.caption(
                "Source event -> causal bridge -> peer evidence -> reverse DCF -> "
                "Bull/Bear/Judge. Frozen illustrative research; not investment advice."
            )
    if "result" not in st.session_state:
        storage_scope, scope_id = _saved_research_scope()
        restored = (
            _saved_research_service_for_scope(storage_scope).load_last_displayed(
                scope_id=scope_id
            )
            if storage_scope is SavedResearchScope.LOCAL_WORKSPACE
            else None
        )
        if restored is not None:
            _apply_loaded_saved_research(restored, show_notice=False)
            restored_is_draft = restored.artifact.status in {
                SavedResearchStatus.DRAFT,
                SavedResearchStatus.PARTIAL,
                SavedResearchStatus.FAILED,
            }
            st.session_state._saved_research_load_notice = (
                f"Restored {'saved research draft' if restored_is_draft else 'saved live research'} from "
                f"{_display_timestamp(restored.artifact.completed_at)}. "
                "No providers were called."
            )
        else:
            st.session_state.result = demo_result("AAPL")
            st.session_state.demo_mode = "AAPL"
            UiSessionState(st.session_state).request_public_demo_landing()
            st.session_state._displayed_research_setup = _demo_research_setup(
                demo_cases()[0]
            )
            st.session_state.pop("_displayed_research_context", None)
            st.session_state._displayed_result_origin = "frozen_demo"
            st.session_state._displayed_saved_artifact_id = ""
            st.session_state._displayed_parent_run_id = ""
            st.session_state._displayed_saved_storage_scope = ""
            st.session_state._displayed_saved_scope_id = ""
    active_storage_scope, active_scope_id = _saved_research_scope()
    displayed_artifact_id = str(
        st.session_state.get("_displayed_saved_artifact_id") or ""
    )
    if (
        active_storage_scope is SavedResearchScope.HOSTED_SESSION
        and displayed_artifact_id
        and _saved_research_service_for_scope(active_storage_scope).repository.get(
            displayed_artifact_id,
            scope_id=active_scope_id,
        )
        is None
    ):
        st.session_state.result = demo_result("AAPL")
        st.session_state.demo_mode = "AAPL"
        UiSessionState(st.session_state).request_public_demo_landing()
        st.session_state._displayed_research_setup = _demo_research_setup(
            demo_cases()[0]
        )
        st.session_state.pop("_displayed_research_context", None)
        st.session_state._displayed_result_origin = "frozen_demo"
        st.session_state._displayed_saved_artifact_id = ""
        st.session_state._displayed_parent_run_id = ""
        st.session_state._displayed_saved_storage_scope = ""
        st.session_state._displayed_saved_scope_id = ""
        st.session_state._saved_research_load_notice = (
            "The private saved-research session expired, so the frozen AAPL demo "
            "was restored. No providers were called."
        )
    existing_session_result = st.session_state.get("result")
    adoption_key = (
        _result_adoption_key(existing_session_result)
        if _is_displayable_research_result(existing_session_result)
        else ""
    )
    attempted_adoptions = set(
        st.session_state.get("_saved_research_adoption_attempts") or ()
    )
    if (
        _is_displayable_research_result(existing_session_result)
        and getattr(existing_session_result, "demo_case", None) is None
        and not str(st.session_state.get("_displayed_saved_artifact_id") or "")
        and adoption_key not in attempted_adoptions
    ):
        attempted_adoptions.add(adoption_key)
        st.session_state._saved_research_adoption_attempts = tuple(
            sorted(attempted_adoptions)
        )
        existing_manifest = getattr(existing_session_result, "run_manifest", None)
        existing_hybrid = getattr(existing_session_result, "hybrid_research", None)
        try:
            adoption_scope, adoption_scope_id = _saved_research_scope()
            adopted_artifact_id = _save_displayable_result(
                existing_session_result,
                run_id=str(
                    getattr(existing_manifest, "run_id", "")
                    or getattr(existing_hybrid, "run_id", "")
                    or uuid.uuid4()
                ),
                operation_kind="legacy_adopted",
                operation_label=str(
                    st.session_state.get("_displayed_operation_label")
                    or f"Recovered live research for {existing_session_result.identity.ticker}"
                ),
                run_status=str(getattr(existing_hybrid, "status", "") or "completed"),
                preferred_route=str(st.session_state.get("research_view") or "Decision"),
                setup_summary=dict(
                    st.session_state.get("_displayed_research_setup") or {}
                ),
                storage_scope=adoption_scope,
                scope_id=adoption_scope_id,
            )
            adopted = _saved_research_service_for_scope(
                adoption_scope
            ).load_result(
                adopted_artifact_id,
                scope_id=adoption_scope_id,
            )
            _apply_loaded_saved_research(adopted, show_notice=False)
            st.session_state._saved_research_load_notice = (
                "Recovered and saved the live research already held in this session. "
                "No providers were called."
            )
        except (
            SavedResearchError,
            SavedResearchCodecError,
            SavedResearchStorageError,
            OSError,
            ValueError,
        ) as exc:
            st.session_state._displayed_result_origin = "live_not_saved"
            st.session_state._saved_research_load_error = {
                "message": (
                    "The live result remains available, but restart recovery "
                    "could not be verified. No rerun is required."
                ),
                "reason": str(getattr(exc, "reason", "") or "verification_failed"),
                "artifact_id": str(getattr(exc, "artifact_id", "") or ""),
                "failure_path": str(getattr(exc, "failure_path", "") or ""),
                "annotation": str(getattr(exc, "annotation", "") or ""),
            }
    saved_notice = st.session_state.pop("_saved_research_load_notice", "")
    if saved_notice:
        st.success(saved_notice)
    saved_error = st.session_state.pop("_saved_research_load_error", "")
    if saved_error:
        error_payload = (
            dict(saved_error)
            if isinstance(saved_error, dict)
            else {"message": str(saved_error)}
        )
        st.warning(
            f"Saved-result recovery needs attention. "
            f"{error_payload.get('message') or 'The result could not be reopened.'} "
            "The displayed research was preserved and no providers were called."
        )
        if any(
            error_payload.get(key)
            for key in ("reason", "failure_path", "annotation")
        ):
            with st.expander("Review technical compatibility details", expanded=False):
                st.json({
                    "reason": error_payload.get("reason") or "load_failed",
                    "failure_path": error_payload.get("failure_path") or "Not reported",
                    "annotation": error_payload.get("annotation") or "Not reported",
                    "codec_version": SAVED_RESEARCH_CODEC_VERSION,
                    "python_runtime": sys.version.split()[0],
                })
    if "network_diagnostics" not in st.session_state:
        st.session_state.network_diagnostics = None
    hybrid_job_id = str(st.session_state.get("hybrid_job_id") or "")
    if hybrid_job_id:
        hybrid_job = _hybrid_job_manager().get(hybrid_job_id)
        if hybrid_job and hybrid_job.status == "completed":
            if _is_displayable_research_result(hybrid_job.result):
                result_route = str(
                    getattr(hybrid_job, "result_route", "Decision")
                    or "Decision"
                )
                operation_label = str(
                    getattr(hybrid_job, "operation_label", "")
                    or f"Research for {hybrid_job.ticker}"
                )
                st.session_state._last_completed_operation_kind = str(
                    getattr(hybrid_job, "operation_kind", "full_research")
                    or "full_research"
                )
                st.session_state._last_completed_operation_label = operation_label
                displayed_result = st.session_state.get("result")
                displayed_run_id = str(
                    getattr(
                        getattr(displayed_result, "run_manifest", None),
                        "run_id",
                        "",
                    )
                    or ""
                )
                origin_run_id = str(
                    getattr(
                        getattr(hybrid_job, "displayed_result", None),
                        "run_id",
                        "",
                    )
                    or ""
                )
                still_viewing_origin = should_auto_load_saved_result(
                    displayed_run_id=displayed_run_id,
                    originating_run_id=origin_run_id,
                    displayed_artifact_id=str(
                        st.session_state.get("_displayed_saved_artifact_id") or ""
                    ),
                    originating_artifact_id=str(
                        getattr(hybrid_job, "originating_artifact_id", "") or ""
                    ),
                )
                saved_artifact_id = str(
                    getattr(hybrid_job, "saved_artifact_id", "") or ""
                )
                if still_viewing_origin:
                    if saved_artifact_id:
                        loaded_ok = _load_saved_artifact(
                            saved_artifact_id,
                            scope_id=str(getattr(hybrid_job, "saved_scope_id", "local")),
                        )
                        if not loaded_ok:
                            load_error = st.session_state.get("_saved_research_load_error")
                            in_memory_payload = {
                                "result": hybrid_job.result,
                                "run_id": hybrid_job.job_id,
                                "operation_kind": str(
                                    getattr(hybrid_job, "operation_kind", "full_research")
                                    or "full_research"
                                ),
                                "operation_label": operation_label,
                                "ticker": hybrid_job.ticker,
                                "outcome": "completed",
                                "preferred_route": result_route,
                                "parent_artifact_id": str(
                                    getattr(hybrid_job, "originating_artifact_id", "") or ""
                                ),
                                "setup_summary": dict(
                                    getattr(hybrid_job, "saved_setup_summary", {}) or {}
                                ),
                                "completed_at": datetime.now(timezone.utc).isoformat(
                                    timespec="seconds"
                                ),
                                "save_error": (
                                    dict(load_error)
                                    if isinstance(load_error, dict)
                                    else {"message": str(load_error or "")}
                                ),
                            }
                            st.session_state._completed_in_memory_result = in_memory_payload
                            _apply_in_memory_research(in_memory_payload)
                            saved_artifact_id = ""
                    else:
                        save_failure = str(
                            getattr(hybrid_job, "result_save_error", "") or ""
                        )
                        in_memory_payload = {
                            "result": hybrid_job.result,
                            "run_id": hybrid_job.job_id,
                            "operation_kind": str(
                                getattr(hybrid_job, "operation_kind", "full_research")
                                or "full_research"
                            ),
                            "operation_label": operation_label,
                            "ticker": hybrid_job.ticker,
                            "outcome": (
                                "unresolved"
                                if _remaining_targeted_action(hybrid_job.result, hybrid_job)
                                else "completed"
                            ),
                            "preferred_route": result_route,
                            "parent_artifact_id": str(
                                getattr(hybrid_job, "originating_artifact_id", "") or ""
                            ),
                            "setup_summary": dict(
                                getattr(hybrid_job, "saved_setup_summary", {}) or {}
                            ),
                            "completed_at": datetime.now(timezone.utc).isoformat(
                                timespec="seconds"
                            ),
                            "save_error": (
                                {
                                    "message": save_failure,
                                    "reason": str(
                                        getattr(
                                            hybrid_job,
                                            "result_save_error_reason",
                                            "",
                                        )
                                        or "verification_failed"
                                    ),
                                    "failure_path": str(
                                        getattr(
                                            hybrid_job,
                                            "result_save_failure_path",
                                            "",
                                        )
                                        or ""
                                    ),
                                    "annotation": str(
                                        getattr(
                                            hybrid_job,
                                            "result_save_annotation",
                                            "",
                                        )
                                        or ""
                                    ),
                                }
                                if save_failure else {}
                            ),
                        }
                        st.session_state._completed_in_memory_result = in_memory_payload
                        _apply_in_memory_research(in_memory_payload)
                    UiSessionState(st.session_state).request_route(
                        result_route,
                        origin="background_research_completion",
                    )
                elif saved_artifact_id:
                    st.session_state._completed_saved_result = {
                        "artifact_id": saved_artifact_id,
                        "scope_id": str(getattr(hybrid_job, "saved_scope_id", "local")),
                        "operation_label": operation_label,
                        "ticker": hybrid_job.ticker,
                        "outcome": (
                            "unresolved"
                            if _remaining_targeted_action(hybrid_job.result, hybrid_job)
                            else "completed"
                        ),
                        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                else:
                    background_save_failure = str(
                        getattr(hybrid_job, "result_save_error", "") or ""
                    )
                    st.session_state._completed_in_memory_result = {
                        "result": hybrid_job.result,
                        "run_id": hybrid_job.job_id,
                        "operation_kind": str(
                            getattr(hybrid_job, "operation_kind", "full_research")
                            or "full_research"
                        ),
                        "operation_label": operation_label,
                        "ticker": hybrid_job.ticker,
                        "outcome": "completed",
                        "preferred_route": result_route,
                        "parent_artifact_id": str(
                            getattr(hybrid_job, "originating_artifact_id", "") or ""
                        ),
                        "setup_summary": dict(
                            getattr(hybrid_job, "saved_setup_summary", {}) or {}
                        ),
                        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "save_error": (
                            {
                                "message": background_save_failure,
                                "reason": str(
                                    getattr(
                                        hybrid_job,
                                        "result_save_error_reason",
                                        "",
                                    )
                                    or "verification_failed"
                                ),
                                "failure_path": str(
                                    getattr(
                                        hybrid_job,
                                        "result_save_failure_path",
                                        "",
                                    )
                                    or ""
                                ),
                                "annotation": str(
                                    getattr(
                                        hybrid_job,
                                        "result_save_annotation",
                                        "",
                                    )
                                    or ""
                                ),
                            }
                            if background_save_failure else {}
                        ),
                    }
                    if still_viewing_origin:
                        _apply_in_memory_research(
                            st.session_state._completed_in_memory_result
                        )
                # The completed card is rebuilt from the displayed artifact on
                # every render.  Never cache a view model here: a later saved
                # result or derived evidence revision would make it stale.
                handoff_message = (
                    f"The saved result is loaded below in {result_route}."
                    if still_viewing_origin and saved_artifact_id
                    else f"The result is loaded below in {result_route}, but it was not saved."
                    if still_viewing_origin
                    else "The saved result is ready to load without rerunning research."
                    if saved_artifact_id
                    else "The result is available to load in this session, but it was not saved."
                )
                save_error = str(
                    getattr(hybrid_job, "result_save_error", "") or ""
                )
                if save_error:
                    st.warning(
                        "Research completed and remains available in this session, "
                        f"but it was not saved for restart recovery. {save_error}"
                    )
                if str(getattr(hybrid_job, "operation_kind", "")) in {
                    "targeted_investigation",
                    "action_investigation",
                }:
                    remaining_action = _remaining_targeted_action(
                        hybrid_job.result, hybrid_job
                    )
                    if remaining_action is not None:
                        attempt = str(
                            getattr(remaining_action, "attempt_summary", "")
                            or "The action remains open."
                        )
                        st.warning(
                            f"Research pass completed: {operation_label}. "
                            f"The investigation remains unresolved. {attempt} "
                            + handoff_message
                        )
                    else:
                        st.success(
                            f"Investigation resolved: {operation_label}. "
                            + handoff_message
                        )
                else:
                    st.success(
                        f"Completed: {operation_label}. "
                        + handoff_message
                    )
            else:
                st.warning(
                    "The background job finished, but it did not return a "
                    "displayable research artifact. The previously displayed "
                    "result has been preserved."
                )
            _hybrid_job_manager().clear(hybrid_job.job_id)
            st.session_state.hybrid_job_id = ""
            hybrid_job_id = ""
        elif hybrid_job and hybrid_job.status in {
            "failed",
            "cancelled",
            "unavailable",
            "investigated_draft",
            "partial",
            "timeout",
            "validation_failed",
            "not_started_missing_credentials",
        }:
            displayable_partial = _is_displayable_research_result(
                hybrid_job.result
            )
            saved_artifact_id = ""
            draft_loaded = False
            if displayable_partial:
                saved_artifact_id = str(
                    getattr(hybrid_job, "saved_artifact_id", "") or ""
                )
                displayed_run_id = str(
                    getattr(
                        getattr(
                            st.session_state.get("result"),
                            "run_manifest",
                            None,
                        ),
                        "run_id",
                        "",
                    )
                    or ""
                )
                origin_run_id = str(
                    getattr(
                        getattr(hybrid_job, "displayed_result", None),
                        "run_id",
                        "",
                    )
                    or ""
                )
                still_viewing_origin = should_auto_load_saved_result(
                    displayed_run_id=displayed_run_id,
                    originating_run_id=origin_run_id,
                    displayed_artifact_id=str(
                        st.session_state.get("_displayed_saved_artifact_id") or ""
                    ),
                    originating_artifact_id=str(
                        getattr(hybrid_job, "originating_artifact_id", "") or ""
                    ),
                )
                draft_loaded = still_viewing_origin
                if still_viewing_origin and saved_artifact_id:
                    if not _load_saved_artifact(
                        saved_artifact_id,
                        scope_id=str(getattr(hybrid_job, "saved_scope_id", "local")),
                    ):
                        st.session_state.result = hybrid_job.result
                        st.session_state.demo_mode = None
                        st.session_state._displayed_result_origin = "live_not_saved"
                elif still_viewing_origin:
                    st.session_state.result = hybrid_job.result
                    st.session_state.demo_mode = None
                    st.session_state._displayed_research_setup = dict(
                        st.session_state.get("_active_research_setup") or {}
                    )
                    st.session_state._displayed_result_origin = "live_not_saved"
                    UiSessionState(st.session_state).request_route(
                        str(getattr(hybrid_job, "result_route", "Audit") or "Audit"),
                        origin="background_partial_completion",
                    )
                elif saved_artifact_id:
                    st.session_state._completed_saved_result = {
                        "artifact_id": saved_artifact_id,
                        "scope_id": str(getattr(hybrid_job, "saved_scope_id", "local")),
                        "operation_label": str(
                            getattr(hybrid_job, "operation_label", "")
                            or f"Research for {hybrid_job.ticker}"
                        ),
                        "ticker": hybrid_job.ticker,
                        "outcome": "partial",
                        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                else:
                    st.session_state._completed_in_memory_result = {
                        "result": hybrid_job.result,
                        "operation_label": str(
                            getattr(hybrid_job, "operation_label", "")
                            or f"Research for {hybrid_job.ticker}"
                        ),
                        "ticker": hybrid_job.ticker,
                        "outcome": "partial",
                        "preferred_route": str(
                            getattr(hybrid_job, "result_route", "Audit") or "Audit"
                        ),
                        "setup_summary": dict(
                            getattr(hybrid_job, "saved_setup_summary", {}) or {}
                        ),
                        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
            operation_label = str(
                getattr(hybrid_job, "operation_label", "")
                or f"Research for {hybrid_job.ticker}"
            )
            st.session_state._last_completed_operation_kind = str(
                getattr(hybrid_job, "operation_kind", "full_research")
                or "full_research"
            )
            st.session_state._last_completed_operation_label = operation_label
            st.warning(
                f"{operation_label} ended as {hybrid_job.status}. "
                f"{hybrid_job.error or 'Deterministic research remains available.'} "
                + (
                    f"The latest draft is loaded in "
                    f"{getattr(hybrid_job, 'result_route', 'Audit') or 'Audit'}."
                    if draft_loaded else
                    "The saved draft is ready to load without rerunning research."
                    if saved_artifact_id else
                    "The previously displayed result has been preserved."
                )
            )
            _hybrid_job_manager().clear(hybrid_job.job_id)
            st.session_state.hybrid_job_id = ""
            hybrid_job_id = ""
    completed_saved_result = st.session_state.get("_completed_saved_result")
    if completed_saved_result:
        st.info(
            f"Saved result ready: {completed_saved_result.get('operation_label') or 'Research'} "
            f"for {completed_saved_result.get('ticker') or 'the selected ticker'} | "
            f"{str(completed_saved_result.get('outcome') or 'completed').replace('_', ' ')} | "
            f"{_display_timestamp(completed_saved_result.get('completed_at'))}."
        )
        if st.button("Load completed result", key="load_completed_background_result"):
            if _load_saved_artifact(
                str(completed_saved_result.get("artifact_id") or ""),
                scope_id=str(completed_saved_result.get("scope_id") or "local"),
            ):
                st.session_state.pop("_completed_saved_result", None)
                st.rerun()
    completed_in_memory_result = st.session_state.get("_completed_in_memory_result")
    if completed_in_memory_result:
        st.warning(
            f"Live research · not saved: "
            f"{completed_in_memory_result.get('operation_label') or 'Research'} for "
            f"{completed_in_memory_result.get('ticker') or 'the selected ticker'} | "
            f"{completed_in_memory_result.get('outcome') or 'completed'}. "
            "Research completed successfully, but restart recovery is unavailable. "
            "No rerun is required."
        )
        session_result_is_displayed = (
            st.session_state.get("result") is completed_in_memory_result.get("result")
        )
        if not session_result_is_displayed and st.button(
            "Load session result",
            key="load_completed_in_memory_result",
            use_container_width=True,
        ):
            _apply_in_memory_research(completed_in_memory_result)
            st.rerun()
        if st.button(
            "Retry saving",
            key="retry_save_completed_in_memory_result",
            use_container_width=True,
        ):
            try:
                retry_scope, retry_scope_id = _saved_research_scope()
                artifact_id = _save_displayable_result(
                    completed_in_memory_result["result"],
                    run_id=str(
                        completed_in_memory_result.get("run_id")
                        or getattr(
                            getattr(completed_in_memory_result["result"], "run_manifest", None),
                            "run_id",
                            "",
                        )
                        or uuid.uuid4()
                    ),
                    operation_kind=str(
                        completed_in_memory_result.get("operation_kind")
                        or "full_research"
                    ),
                    operation_label=str(
                        completed_in_memory_result.get("operation_label") or "Research"
                    ),
                    run_status="completed",
                    preferred_route=str(
                        completed_in_memory_result.get("preferred_route") or "Decision"
                    ),
                    setup_summary=dict(
                        completed_in_memory_result.get("setup_summary") or {}
                    ),
                    storage_scope=retry_scope,
                    scope_id=retry_scope_id,
                    completed_at=str(
                        completed_in_memory_result.get("completed_at") or ""
                    ),
                    parent_artifact_id=str(
                        completed_in_memory_result.get("parent_artifact_id") or ""
                    ),
                )
                if _load_saved_artifact(artifact_id, scope_id=retry_scope_id):
                    st.session_state.pop("_completed_in_memory_result", None)
                    st.rerun()
            except (
                SavedResearchError,
                SavedResearchCodecError,
                SavedResearchStorageError,
                OSError,
                ValueError,
            ) as exc:
                completed_in_memory_result["save_error"] = {
                    "message": str(exc),
                    "reason": str(getattr(exc, "reason", "") or "verification_failed"),
                    "failure_path": str(getattr(exc, "failure_path", "") or ""),
                    "annotation": str(getattr(exc, "annotation", "") or ""),
                }
                st.session_state._saved_research_load_error = dict(
                    completed_in_memory_result["save_error"]
                )
                st.rerun()
        download_result_key = _result_adoption_key(
            completed_in_memory_result["result"]
        )
        prepared_download = st.session_state.get(
            "_prepared_session_result_download"
        )
        if (
            not isinstance(prepared_download, dict)
            or prepared_download.get("key") != download_result_key
        ):
            prepared_download = None
        if prepared_download is None and st.button(
            "Prepare sanitized download",
            key="prepare_completed_in_memory_download",
            use_container_width=True,
        ):
            try:
                sanitized_download = JsonResearchResultArtifactCodec().encode(
                    completed_in_memory_result["result"]
                )
                st.session_state._prepared_session_result_download = {
                    "key": download_result_key,
                    "payload": sanitized_download,
                }
                st.rerun()
            except (SavedResearchCodecError, TypeError, ValueError) as exc:
                st.warning(f"A sanitized download could not be prepared. {exc}")
        elif prepared_download is not None:
            st.download_button(
                "Download sanitized result",
                data=prepared_download["payload"],
                file_name=(
                    f"{str(completed_in_memory_result.get('ticker') or 'research').lower()}-"
                    "session-result.json"
                ),
                mime="application/json",
                key="download_completed_in_memory_result",
                use_container_width=True,
            )
        if completed_in_memory_result.get("save_error"):
            with st.expander("Review technical compatibility details", expanded=False):
                st.json(completed_in_memory_result["save_error"])
    pending_demo_preset = st.session_state.pop("_pending_demo_preset", None)
    if pending_demo_preset:
        st.session_state.research_profile_selector = pending_demo_preset.get(
            "research_profile", "deep_initiation"
        )
        st.session_state.budget_mode_selector = pending_demo_preset.get("budget_mode", "Premium")
    # Pre-intent app versions used this flag to auto-start research after a
    # rerun. Discard it during migration so an upgrade cannot launch hidden work.
    st.session_state.pop("_pending_live_refresh", None)

    with st.sidebar:
        st.header("Research Run")
        display_timezone = st.selectbox(
            "Display timezone",
            [
                "UTC",
                "America/New_York",
                "America/Los_Angeles",
                "Asia/Seoul",
                "Asia/Hong_Kong",
                "Europe/London",
            ],
            index=0,
            key="display_timezone",
            help="Canonical UTC timestamps remain available in Audit & Memo.",
        )
        _render_displayed_research_card()
        saved_library_ticker = str(
            st.session_state.get("ticker_input")
            or getattr(
                getattr(st.session_state.get("result"), "identity", None),
                "ticker",
                "AAPL",
            )
            or "AAPL"
        ).upper()
        _render_saved_research_library(saved_library_ticker)
        secrets_manager = LocalSecretsManager()
        local_secret_status = secrets_manager.redacted_status(getattr(config, "_SYSTEM_ENV_KEYS", set()))
        configured_secret_count = sum(
            1 for item in local_secret_status
            if item["configured"] and item["key"] != "SEC_USER_AGENT"
        )
        local_secret_backend = local_secret_status[0]["backend"] if local_secret_status else "unavailable"
        if configured_secret_count:
            st.caption(f"{configured_secret_count} saved provider key(s) configured via {local_secret_backend}.")
        elif not secrets_manager.backend_available:
            st.warning("OS keychain is unavailable. Install keyring support or use .env.local manually.")
        cases = public_demo_cases()
        demo_labels = [f"{case.ticker} - {case.title}" for case in cases]
        st.subheader("Load Demo Gallery")
        selected_demo = st.selectbox("Demo case", demo_labels, index=0)
        selected_case = cases[demo_labels.index(selected_demo)]
        case_metadata = _demo_case_runtime_metadata(selected_case)
        case_content_version = str(case_metadata["content_version"])
        case_refreshed_at = str(case_metadata["refreshed_at"])
        case_research_profile = str(case_metadata["research_profile"])
        case_budget_mode = str(case_metadata["budget_mode"])
        case_enabled_layers = tuple(case_metadata["enabled_layers"])
        freshness = (
            f" Version: {case_content_version}; refreshed {case_refreshed_at}."
            if case_content_version or case_refreshed_at else ""
        )
        st.caption(f"{selected_case.lesson} {selected_case.badge}; {selected_case.expected_runtime}.{freshness}")
        if case_research_profile:
            st.caption(
                f"Demo preset: {case_research_profile or 'Current research profile'}."
            )
        if case_enabled_layers:
            st.caption("Included layers: " + ", ".join(case_enabled_layers) + ".")
        load_demo_clicked = st.button("Load Selected Demo", use_container_width=True)
        ticker = st.text_input(
            "Ticker",
            value=selected_case.ticker or "AAPL",
            key="ticker_input",
        ).upper().strip()
        profile_options = research_profiles()
        profile_ids = [item.profile_id for item in profile_options if not item.event_scoped]
        profile_by_id = {item.profile_id: item for item in profile_options}
        default_profile = config.RESEARCH_PROFILE if config.RESEARCH_PROFILE in profile_ids else "adaptive_ic"
        profile_radio_kwargs: dict[str, object] = {}
        if "research_profile_selector" not in st.session_state:
            profile_radio_kwargs["index"] = profile_ids.index(default_profile)
        selected_profile_id = st.radio(
            "Research depth",
            profile_ids,
            format_func=lambda value: profile_by_id[value].label,
            help="Fast screens one anomaly; Adaptive is the default analyst workflow; Deep builds a fuller initiation pack.",
            key="research_profile_selector",
            **profile_radio_kwargs,
        )
        selected_profile = profile_by_id[selected_profile_id]
        st.caption(
            f"{selected_profile.quarter_depth} quarters, {selected_profile.annual_depth} annual reports, "
            f"{selected_profile.call_depth} calls."
        )
        agent_research_effort = st.selectbox(
            "Agent research effort",
            ("answer_oriented", "maximum"),
            index=0,
            format_func=lambda value: (
                "Answer-oriented"
                if value == "answer_oriented" else "Maximum depth"
            ),
            help=(
                "Answer-oriented is the readable default. Maximum depth gives "
                "the primary researcher and Codex larger bounded tool and time budgets."
            ),
            key="agent_research_effort_selector",
        )
        select_event_clicked = False
        investigate_event_id = None
        prior_result = st.session_state.get("result")
        if prior_result and getattr(prior_result, "events", None):
            event_options = prior_result.events[:20]
            event_labels = {
                event_identifier(item): f"{item.event_date or 'Date unknown'} | {item.title}"
                for item in event_options
            }
            investigate_event_id = st.selectbox(
                "Investigate an event",
                list(event_labels),
                format_func=lambda value: event_labels[value],
            )
            select_event_clicked = st.button(
                "Select event investigation",
                use_container_width=True,
                disabled=_has_active_research_task(),
            )
        # Legacy internals still accept a budget label, but production research
        # always admits every configured and entitled source. Research depth
        # controls breadth and latency, never source eligibility.
        budget_mode = "Premium"
        sec_user_agent_input = st.text_input(
            "SEC user agent",
            value="",
            placeholder=(
                "Already configured locally"
                if config.SEC_USER_AGENT != config.DEFAULT_SEC_USER_AGENT
                else "Your App/Version research contact name@example.com"
            ),
            help=(
                "Leave blank to use the locally configured identity. The "
                "saved value is not returned to the browser or status APIs."
            ),
        ).strip()
        sec_user_agent = sec_user_agent_input or config.SEC_USER_AGENT
        st.subheader("Consensus Connection")
        setup_intent = _selected_research_intent()
        source_setup_request = (
            {
                "ticker": setup_intent.ticker,
                "title": setup_intent.operation_label,
                "source_families": tuple(
                    value.replace("_", " ")
                    for value in setup_intent.required_source_families
                ),
                "connectors": tuple(
                    value.replace("_", " ")
                    for value in setup_intent.preferred_connectors
                ),
                "is_frozen_demo": bool(
                    getattr(st.session_state.get("result"), "demo_case", None)
                ),
            }
            if setup_intent is not None
            and setup_intent.status
            is ResearchLaunchIntentStatus.NEEDS_SOURCE_SETUP
            else None
        )
        if source_setup_request:
            setup_title = str(
                source_setup_request.get("title") or "Selected investigation"
            )
            setup_sources = tuple(
                source_setup_request.get("source_families") or ()
            )
            setup_connectors = tuple(
                source_setup_request.get("connectors") or ()
            )
            st.info(
                f"Step 1 of 2 — source setup for {setup_title}. "
                + (
                    "Frozen demos are read-only. Connect an eligible source "
                    "below; step 2 is to confirm the selected investigation. "
                    if source_setup_request.get("is_frozen_demo")
                    else "Connect an eligible source below; step 2 is to confirm "
                    "the selected investigation. "
                )
                + (
                    "Requested source families: " + ", ".join(setup_sources) + "."
                    if setup_sources
                    else "The live source planner will try registered adapters."
                )
            )
            if setup_connectors:
                st.caption(
                    "Preferred registered connectors: "
                    + ", ".join(setup_connectors)
                    + "."
                )
            if st.button(
                "Clear selected investigation",
                key="dismiss-investigation-source-setup",
                use_container_width=True,
            ):
                _clear_research_intent()
                st.rerun()
        configured_alpha_key = config.ALPHAVANTAGE_API_KEY or _streamlit_secret("ALPHAVANTAGE_API_KEY")
        configured_finnhub_key = config.FINNHUB_API_KEY or _streamlit_secret("FINNHUB_API_KEY")
        configured_fmp_key = config.FMP_API_KEY or _streamlit_secret("FMP_API_KEY")
        configured_fred_key = config.FRED_API_KEY or _streamlit_secret("FRED_API_KEY")
        configured_bls_key = config.BLS_API_KEY or _streamlit_secret("BLS_API_KEY")
        configured_bea_key = config.BEA_API_KEY or _streamlit_secret("BEA_API_KEY")
        configured_census_key = config.CENSUS_API_KEY or _streamlit_secret("CENSUS_API_KEY")
        configured_wisburg_key = config.WISBURG_API_KEY or _streamlit_secret("WISBURG_API_KEY")
        configured_tiingo_key = config.TIINGO_API_KEY or _streamlit_secret("TIINGO_API_KEY")
        configured_eodhd_key = config.EODHD_API_KEY or _streamlit_secret("EODHD_API_KEY")
        configured_deepseek_key = config.DEEPSEEK_API_KEY or _streamlit_secret("DEEPSEEK_API_KEY")
        configured_openai_key = config.OPENAI_API_KEY or _streamlit_secret("OPENAI_API_KEY")
        llm_store = ResearchStore()
        alpha_session_key = st.text_input(
            "Alpha Vantage API key",
            type="password",
            key="alpha_session_key",
            help="Used only in this Streamlit session. It is never written to SQLite or logs.",
            placeholder="Already configured" if configured_alpha_key else "Free key",
        ).strip()
        finnhub_session_key = st.text_input(
            "Finnhub API key",
            type="password",
            key="finnhub_session_key",
            help="Used only in this Streamlit session. It is never written to SQLite or logs.",
            placeholder="Already configured" if configured_finnhub_key else "Free key",
        ).strip()
        with st.expander("All credential methods"):
            st.caption(
                "Alternative connection methods are listed for setup and diagnostics. "
                "The resolved connection used by the next run appears below."
            )
            st.dataframe(
                [
                    {
                        "Provider": item["label"],
                        "Status": (
                            "Ready"
                            if item["configured"]
                            else "Alternative · not used"
                        ),
                        "Source": item["source"],
                    }
                    for item in local_secret_status
                ],
                hide_index=True,
                use_container_width=True,
            )
        with st.expander(
            "Additional sources",
            expanded=bool(st.session_state.get("_open_additional_sources")),
        ):
            fmp_session_key = st.text_input(
                "FMP API key",
                type="password",
                key="fmp_session_key",
                help="Optional legacy provider. Used only in this Streamlit session.",
                placeholder="Already configured" if configured_fmp_key else "Optional",
            ).strip()
            tiingo_session_key = st.text_input(
                "Tiingo API key",
                type="password",
                key="tiingo_session_key",
                help="Session-only unless saved. Used for adjusted EOD prices, event windows, beta, and peer-return attribution.",
                placeholder="Already configured" if configured_tiingo_key else "Optional market data key",
            ).strip()
            eodhd_session_key = st.text_input(
                "EODHD API key",
                type="password",
                key="eodhd_session_key",
                help=(
                    "Session-only unless saved. The free plan supplies recent adjusted EOD prices for "
                    "event windows, peer reactions, market context, and reverse-implied expectations."
                ),
                placeholder="Already configured" if configured_eodhd_key else "Optional EOD price key",
            ).strip()
            enable_nasdaq = st.toggle(
                "Nasdaq estimates (unofficial)",
                value=config.ENABLE_NASDAQ_CONSENSUS,
            )
            enable_tradingview = st.toggle(
                "TradingView targets (unofficial)",
                value=config.ENABLE_TRADINGVIEW_CONSENSUS,
            )
            st.markdown("**Macro / external evidence**")
            fred_session_key = st.text_input(
                "FRED API key",
                type="password",
                key="fred_session_key",
                help="Session-only. Enables FRED/ALFRED macro context when the toggle is on.",
                placeholder="Already configured" if configured_fred_key else "Free key",
            ).strip()
            bls_session_key = st.text_input(
                "BLS API key",
                type="password",
                key="bls_session_key",
                help="Optional session-only BLS key. BLS may allow limited no-key calls.",
                placeholder="Already configured" if configured_bls_key else "Optional",
            ).strip()
            bea_session_key = st.text_input(
                "BEA API key",
                type="password",
                key="bea_session_key",
                help="Session-only. BEA macro calls require a BEA key.",
                placeholder="Already configured" if configured_bea_key else "Free key",
            ).strip()
            census_session_key = st.text_input(
                "Census API key",
                type="password",
                key="census_session_key",
                help="Session-only. Census macro calls require a Census key.",
                placeholder="Already configured" if configured_census_key else "Free key",
            ).strip()
            default_macro_policy = _default_macro_policy()
            macro_policy = st.radio(
                "Macro context",
                MACRO_POLICY_OPTIONS,
                index=MACRO_POLICY_OPTIONS.index(default_macro_policy),
                horizontal=True,
                key="macro_policy_selector",
                help=(
                    "Adaptive selects only company-relevant domestic and global factors. "
                    "Global diagnostic broadens the macro lens. Custom exposes individual "
                    "provider controls."
                ),
            )
            global_macro_mode = macro_policy in {"Adaptive", "Global diagnostic"}
            enable_default_macro = macro_policy in {"Adaptive", "Global diagnostic"}
            effective_fred_key_preview = fred_session_key or configured_fred_key
            effective_bea_key_preview = bea_session_key or configured_bea_key
            effective_census_key_preview = census_session_key or configured_census_key
            macro_defaults = default_macro_source_settings(
                ticker,
                fred_api_key=effective_fred_key_preview,
                bea_api_key=effective_bea_key_preview,
                census_api_key=effective_census_key_preview,
                enable_default_macro=enable_default_macro,
                global_macro_mode=global_macro_mode,
            )
            if macro_policy == "Paused":
                macro_defaults = {
                    name: False for name in macro_defaults
                }
            if macro_policy == "Custom":
                st.caption("Choose the exact macro adapters for the next run.")
                enable_fred = st.toggle("FRED / ALFRED", value=macro_defaults["fred"])
                enable_bls = st.toggle("BLS", value=macro_defaults["bls"])
                enable_bea = st.toggle("BEA", value=macro_defaults["bea"])
                enable_census = st.toggle("Census", value=macro_defaults["census"])
                enable_treasury = st.toggle("Treasury / Fiscal Data", value=macro_defaults["treasury"])
                enable_ofr = st.toggle("OFR financial stress", value=macro_defaults["ofr"])
                enable_world_bank = st.toggle("World Bank", value=macro_defaults["world_bank"])
                enable_imf = st.toggle("IMF", value=macro_defaults["imf"])
            else:
                enable_fred = macro_defaults["fred"]
                enable_bls = macro_defaults["bls"]
                enable_bea = macro_defaults["bea"]
                enable_census = macro_defaults["census"]
                enable_treasury = macro_defaults["treasury"]
                enable_ofr = macro_defaults["ofr"]
                enable_world_bank = macro_defaults["world_bank"]
                enable_imf = macro_defaults["imf"]
                enabled_macro_names = [
                    label for label, enabled in (
                        ("FRED", enable_fred),
                        ("BLS", enable_bls),
                        ("BEA", enable_bea),
                        ("Census", enable_census),
                        ("Treasury", enable_treasury),
                        ("OFR", enable_ofr),
                        ("World Bank", enable_world_bank),
                        ("IMF", enable_imf),
                    ) if enabled
                ]
                st.caption(
                    "No optional macro datasets will be fetched."
                    if not enabled_macro_names else
                    "Automatic macro sources: " + ", ".join(enabled_macro_names) + "."
                )
            enable_gdelt = st.toggle("GDELT narrative saturation", value=config.ENABLE_GDELT)
            refresh_macro_cache = st.toggle(
                "Bypass macro cache",
                value=False,
                disabled=not any((
                    enable_fred, enable_bls, enable_bea, enable_census,
                    enable_treasury, enable_ofr, enable_world_bank, enable_imf,
                )),
                help="Fetch fresh macro observations instead of reusing eligible cached values.",
            )
            wisburg_session_key = st.text_input(
                "Wisburg API key",
                type="password",
                key="wisburg_session_key",
                help="Session-only unless saved to the OS keychain. Used for external analyst/narrative context only.",
                placeholder="Already configured" if configured_wisburg_key else "Optional",
            ).strip()
            enable_wisburg = st.toggle(
                "Wisburg research",
                value=True,
                help=(
                    "Desired by default for local research. With no valid credential, "
                    "the app uses eligible cached material only and makes no live request."
                ),
            )
            st.markdown("**IC Copilot / LLM synthesis**")
            enable_llm = st.toggle(
                "LLM thesis synthesis",
                value=config.ENABLE_LLM_THESIS or budget_mode in {"Lean", "Stable", "Premium"},
                help="Optional. Sends only curated excerpts and structured claims to the selected provider.",
            )
            presets = {preset.preset_id: preset for preset in list_llm_presets()}
            profiles = llm_store.list_llm_profiles()
            selection = llm_store.get_llm_selection()
            profile_labels = {"": "None"}
            profile_labels.update({
                profile.profile_id: (
                    f"{profile.display_name} ({profile.provider_preset}, {profile.model})"
                    + (" - key saved" if profile.key_configured else " - no key")
                )
                for profile in profiles
            })
            profile_ids = list(profile_labels)
            primary_default = selection.get("primary_profile_id") or config.LLM_PRIMARY_PROFILE_ID
            secondary_default = selection.get("secondary_profile_id") or config.LLM_SECONDARY_PROFILE_ID
            if not primary_default:
                first_configured = next((profile.profile_id for profile in profiles if profile.key_configured), "")
                primary_default = first_configured or (profiles[0].profile_id if profiles else "")
            primary_profile_id = st.selectbox(
                "Primary LLM",
                profile_ids,
                index=profile_ids.index(primary_default) if primary_default in profile_ids else 0,
                format_func=lambda value: profile_labels[value],
            )
            with st.expander("Advanced synthesis and language", expanded=False):
                st.caption(
                    "Optional legacy second-pass synthesis is separate from "
                    "Independent Research QA."
                )
                secondary_profile_id = st.selectbox(
                    "Secondary reader",
                    profile_ids,
                    index=profile_ids.index(secondary_default) if secondary_default in profile_ids else 0,
                    format_func=lambda value: profile_labels[value],
                )
                enable_secondary_review = st.toggle(
                    "Secondary review for Research-Ready+",
                    value=bool(selection.get("enable_secondary", config.ENABLE_SECONDARY_LLM_REVIEW)),
                )
                secondary_min_stage = st.selectbox(
                    "Secondary minimum stage",
                    ["Research-Ready", "High-Conviction"],
                    index=0 if selection.get("secondary_min_stage", config.SECONDARY_LLM_MIN_STAGE) != "High-Conviction" else 1,
                )
                language_policy = st.selectbox(
                    "Language policy",
                    ["bilingual_audit", "english_only"],
                    index=0 if selection.get("language_policy", config.LLM_LANGUAGE_POLICY) != "english_only" else 1,
                )
                st.caption("Actionability policy applies to the Decision recommendation; it does not change the underlying research evidence.")
                st.slider(
                    "Minimum risk-adjusted return for Long / Short (%)",
                    min_value=5,
                    max_value=30,
                    value=int(st.session_state.get("actionability_min_return_pct", 10)),
                    key="actionability_min_return_pct",
                )
                st.slider(
                    "Minimum evidence quality for an actionable rating",
                    min_value=40,
                    max_value=90,
                    value=int(st.session_state.get("actionability_min_evidence_score", 60)),
                    key="actionability_min_evidence_score",
                )
            st.markdown("**Independent Research QA**")
            codex_readiness = _local_codex_readiness()
            selected_primary_profile = next(
                (
                    profile for profile in profiles
                    if profile.profile_id == primary_profile_id
                ),
                None,
            )
            selected_secondary_profile = next(
                (
                    profile for profile in profiles
                    if profile.profile_id == secondary_profile_id
                ),
                None,
            )
            configured_primary_profile_available = bool(
                selected_primary_profile
                and (
                    selected_primary_profile.key_configured
                    or selected_primary_profile.provider_preset == "ollama"
                )
                and profile_supports_role(
                    selected_primary_profile, "planner"
                )
            )
            auto_local_sol = bool(
                codex_readiness.status == "ready"
                and (
                    configured_deepseek_key
                    or configured_primary_profile_available
                )
            )
            primary_research_ready = bool(
                configured_deepseek_key
                or configured_primary_profile_available
            )
            local_codex_candidate = bool(
                primary_research_ready
                and codex_readiness.status in {"ready", "not_authenticated"}
            )
            enable_hybrid_research = st.toggle(
                "Audit primary research over one shared evidence graph",
                value=bool(
                    config.ENABLE_HYBRID_RESEARCH or local_codex_candidate
                ),
                help=(
                    "The selected primary model performs planning and analysis. An optional "
                    "distinct model audits the frozen artifact, scouts research leads, and "
                    "checks final closure over the same admitted evidence graph. "
                    "Research depth controls breadth, investigative loops, and latency."
                ),
            )
            configured_secondary_available = bool(
                secondary_profile_id
                and secondary_profile_id != primary_profile_id
                and selected_secondary_profile
                and (
                    selected_secondary_profile.key_configured
                    or selected_secondary_profile.provider_preset == "ollama"
                )
            )
            configured_secondary_distinct = bool(
                configured_secondary_available
                and profiles_are_genuinely_distinct(
                    selected_primary_profile,
                    selected_secondary_profile,
                )
            )
            local_codex_available = codex_readiness.status in {
                "ready", "not_authenticated"
            }
            hybrid_backend_options = []
            if local_codex_available:
                hybrid_backend_options.append("codex_local")
            if configured_secondary_available:
                hybrid_backend_options.append("configured_secondary")
            hybrid_backend_options.append("openai_responses")
            backend_state_key = "independent_qa_backend"
            preferred_backend = _preferred_auditor_backend(
                local_codex_status=codex_readiness.status,
                primary_research_ready=primary_research_ready,
                configured_secondary_available=configured_secondary_available,
            )
            if (
                st.session_state.get(backend_state_key) not in hybrid_backend_options
                or (
                    preferred_backend == "codex_local"
                    and not st.session_state.get(
                        "_independent_qa_backend_user_selected",
                        False,
                    )
                )
            ):
                st.session_state[backend_state_key] = preferred_backend
            hybrid_backend_id = st.selectbox(
                "Independent auditor backend",
                hybrid_backend_options,
                index=(
                    hybrid_backend_options.index(
                        st.session_state[backend_state_key]
                    )
                ),
                format_func=lambda value: (
                    (
                        "Selected secondary provider profile (independent)"
                        if configured_secondary_distinct
                        else "Selected secondary profile (same-model second pass)"
                    )
                    if value == "configured_secondary"
                    else "OpenAI Responses API (session BYOK)"
                    if value == "openai_responses"
                    else (
                        "Local Codex (ChatGPT connected)"
                        if codex_readiness.status == "ready"
                        else "Local Codex (connect once)"
                    )
                ),
                disabled=not enable_hybrid_research,
                key=backend_state_key,
                on_change=_mark_auditor_backend_user_selected,
            )
            trusted_local_hybrid = bool(
                config.ENABLE_LOCAL_CODEX_EXECUTOR
                or codex_readiness.status == "ready"
            )
            owner_funded_hybrid = bool(config.ALLOW_OWNER_FUNDED_HYBRID)
            configured_primary_secret_available = bool(
                configured_deepseek_key
                and (trusted_local_hybrid or owner_funded_hybrid)
            )
            saved_openai_auditor_available = any(
                profile.key_configured
                and profile.provider_preset in {"openai", "openai_sol"}
                and profile_supports_role(profile, "critic")
                for profile in profiles
            )
            configured_auditor_secret_available = bool(
                configured_openai_key
                and (trusted_local_hybrid or owner_funded_hybrid)
            )
            needs_primary_fallback = bool(
                enable_hybrid_research
                and not configured_primary_profile_available
                and not configured_primary_secret_available
            )
            if needs_primary_fallback:
                hybrid_deepseek_session_key = st.text_input(
                    "Primary research key (session only)",
                    type="password",
                    key="hybrid_deepseek_session_key",
                    placeholder="Required only because no saved primary profile is ready",
                    help=(
                        "Available for this browser session only and never saved in "
                        "research artifacts. Re-enter it after the browser session, "
                        "credential lease, or server process ends."
                    ),
                ).strip()
            else:
                hybrid_deepseek_session_key = ""
                st.caption(
                    "Primary research credentials resolved from the selected saved profile; "
                    "no fallback key is required."
                )
            needs_auditor_fallback = bool(
                enable_hybrid_research
                and hybrid_backend_id == "openai_responses"
                and not saved_openai_auditor_available
                and not configured_auditor_secret_available
            )
            if needs_auditor_fallback:
                hybrid_openai_session_key = st.text_input(
                    "Independent auditor key (session only)",
                    type="password",
                    key="hybrid_openai_session_key",
                    placeholder="Required for the selected OpenAI Responses backend",
                    help=(
                        "Available for this browser session only and never saved in "
                        "research artifacts. Re-enter it after the browser session, "
                        "credential lease, or server process ends."
                    ),
                ).strip()
            else:
                hybrid_openai_session_key = ""
            if enable_hybrid_research:
                codex_status_label = {
                    "ready": "Authenticated and ready",
                    "not_authenticated": "Installed; sign-in required",
                    "not_installed": "Not installed or not detected",
                    "unavailable": "Readiness check failed",
                }.get(codex_readiness.status, codex_readiness.status.replace("_", " ").title())
                with st.container(border=True):
                    st.markdown("**Local Codex**")
                    st.write(codex_status_label)
                    st.caption(codex_readiness.message)
                if codex_readiness.status == "not_authenticated":
                    st.info(
                        "Local Codex is installed but not signed in. Sign in once; "
                        "the app does not need or inspect an OpenAI API key."
                    )
                    if st.button("Connect local Codex", use_container_width=True):
                        try:
                            start_local_codex_login(config.CODEX_EXECUTABLE)
                            _local_codex_readiness.clear()
                            st.info(
                                "Codex sign-in started in your browser. Complete it, "
                                "then choose Recheck local Codex."
                            )
                        except Exception as exc:
                            st.warning(f"Could not start Codex sign-in: {exc}")
                elif codex_readiness.status == "not_installed":
                    st.info(
                        "Install the Codex CLI to use ChatGPT-authenticated local QA, "
                        "or select a configured secondary provider."
                    )
                    st.link_button(
                        "Open Codex CLI setup",
                        "https://developers.openai.com/codex/cli/",
                        use_container_width=True,
                    )
                elif codex_readiness.status == "unavailable":
                    st.warning(
                        "The app could not verify Codex readiness. Deterministic and "
                        "primary research can still run."
                    )
                if st.button(
                    "Recheck local Codex",
                    key="refresh_local_codex_readiness",
                    use_container_width=True,
                ):
                    _local_codex_readiness.clear()
                    st.rerun()
                if auto_local_sol and hybrid_backend_id == "codex_local":
                    st.caption(
                        "Authenticated local Codex will run the independent audit. "
                        "A policy-compatible evidence hash may reuse cached QA."
                    )
                st.caption(
                    "The production workflow freezes the primary artifact before independent "
                    "audit, then runs adversarial scouting, deterministic evidence admission, "
                    "primary revision, and final closure. Any manually entered keys stay in "
                    "this session and are excluded from stored artifacts."
                )
            if st.button("Save LLM Selection", use_container_width=True):
                llm_store.save_llm_selection(
                    primary_profile_id,
                    secondary_profile_id,
                    enable_secondary_review,
                    secondary_min_stage,
                    language_policy,
                )
                st.success("Saved LLM primary/secondary selection.")
            with st.expander("Provider Vault"):
                st.caption(
                    "Local installation: save securely on this device using the "
                    "operating-system credential store. Public deployments use "
                    "expiring session credentials instead."
                )
                saved_rows = [
                    {
                        "Name": profile.display_name,
                        "Preset": profile.provider_preset,
                        "Model": profile.model,
                        "Base URL": profile.base_url or "n/a",
                        "Key": "Configured" if profile.key_configured else "Missing",
                        "Last test": profile.last_test_status,
                    }
                    for profile in profiles
                ]
                if saved_rows:
                    st.dataframe(saved_rows, hide_index=True, use_container_width=True)
                preset_id = st.selectbox(
                    "Provider preset",
                    list(presets),
                    index=list(presets).index("deepseek") if "deepseek" in presets else 0,
                    format_func=lambda value: presets[value].label,
                )
                preset = get_llm_preset(preset_id)
                profile_name = st.text_input("Profile name", value=f"{preset.label} primary").strip()
                profile_model = st.text_input("Model", value=preset.default_model).strip()
                profile_base_url = st.text_input(
                    "Base URL",
                    value=preset.default_base_url,
                    placeholder="Required for custom, Qwen, and Kimi OpenAI-compatible endpoints",
                ).strip()
                profile_roles = st.multiselect(
                    "Eligible roles",
                    ["primary", "secondary", "planner", "investigator", "checker", "critic"],
                    default=["primary", "secondary"],
                    help=(
                        "Ordinary synthesis uses primary/secondary. Independent Research QA "
                        "uses role-specific planning, analysis, audit, gap resolution, and writing."
                    ),
                )
                profile_api_key = st.text_input(
                    "API key",
                    type="password",
                    key="llm_profile_api_key",
                    placeholder="Saved to OS keychain only",
                ).strip()
                profile_action_cols = st.columns(2)
                if profile_action_cols[0].button("Save Provider Profile", use_container_width=True):
                    try:
                        saved_profile = save_llm_profile_with_secret(
                            llm_store,
                            secrets_manager,
                            display_name=profile_name,
                            provider_preset=preset_id,
                            model=profile_model,
                            base_url=profile_base_url,
                            api_key=profile_api_key,
                            role_eligibility=" ".join(profile_roles),
                        )
                        st.success(f"Saved {saved_profile.display_name}.")
                    except Exception as exc:
                        st.error(f"Could not save LLM profile: {exc}")
                if profile_action_cols[1].button("Test Unsaved Profile", use_container_width=True):
                    try:
                        candidate = build_llm_profile(
                            display_name=profile_name,
                            provider_preset=preset_id,
                            model=profile_model,
                            base_url=profile_base_url,
                            role_eligibility=" ".join(profile_roles),
                            key_configured=bool(profile_api_key),
                        )
                        status = test_llm_profile(candidate, manager=secrets_manager, api_key=profile_api_key)
                        st.info(f"{status.status}: {status.message}")
                    except Exception as exc:
                        st.error(f"Could not test LLM profile: {exc}")
                if profiles:
                    delete_id = st.selectbox(
                        "Delete saved profile",
                        [profile.profile_id for profile in profiles],
                        format_func=lambda value: profile_labels.get(value, value),
                    )
                    if st.button("Delete Selected LLM Profile", use_container_width=True):
                        delete_llm_profile_with_secret(llm_store, secrets_manager, delete_id)
                        st.warning("Deleted selected LLM profile and its keychain secret.")
            entered_keys = {
                "ALPHAVANTAGE_API_KEY": alpha_session_key,
                "FINNHUB_API_KEY": finnhub_session_key,
                "FMP_API_KEY": fmp_session_key,
                "FRED_API_KEY": fred_session_key,
                "BEA_API_KEY": bea_session_key,
                "CENSUS_API_KEY": census_session_key,
                "WISBURG_API_KEY": wisburg_session_key,
                "TIINGO_API_KEY": tiingo_session_key,
                "EODHD_API_KEY": eodhd_session_key,
                "SEC_USER_AGENT": sec_user_agent_input,
            }
            key_cols = st.columns(3)
            if key_cols[0].button("Test Keys", use_container_width=True):
                with st.spinner("Testing provider keys..."):
                    st.session_state.key_validation_results = validate_provider_keys(entered_keys)
            if key_cols[1].button("Save Valid Keys", use_container_width=True):
                with st.spinner("Testing and saving valid keys..."):
                    results = validate_provider_keys(entered_keys)
                    st.session_state.key_validation_results = results
                    try:
                        outcome = save_validated_keys(secrets_manager, entered_keys, results)
                        config.refresh_runtime_secrets()
                        st.success(
                            f"Saved {len(outcome['saved'])} key(s). "
                            "Restart the app if a running workflow already captured old settings."
                        )
                    except Exception as exc:
                        st.error(f"Could not save keys to OS keychain: {exc}")
            if key_cols[2].button("Clear Saved Keys", use_container_width=True):
                secrets_manager.delete_many()
                config.refresh_runtime_secrets()
                st.warning("Cleared saved local keys from the OS keychain.")
            if st.session_state.get("key_validation_results"):
                st.dataframe(
                    [
                        {"Provider": item.label, "Status": item.status, "Message": item.message}
                        for item in st.session_state.key_validation_results
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
        effective_alpha_key = alpha_session_key or configured_alpha_key
        effective_finnhub_key = finnhub_session_key or configured_finnhub_key
        effective_fmp_key = fmp_session_key or configured_fmp_key
        effective_fred_key = fred_session_key or configured_fred_key
        effective_bls_key = bls_session_key or configured_bls_key
        effective_bea_key = bea_session_key or configured_bea_key
        effective_census_key = census_session_key or configured_census_key
        effective_wisburg_key = wisburg_session_key or configured_wisburg_key
        effective_tiingo_key = tiingo_session_key or configured_tiingo_key
        effective_eodhd_key = eodhd_session_key or configured_eodhd_key
        primary_profile = llm_store.get_llm_profile(primary_profile_id)
        secondary_profile = llm_store.get_llm_profile(secondary_profile_id)
        trusted_local_hybrid = bool(
            config.ENABLE_LOCAL_CODEX_EXECUTOR
            or codex_readiness.status == "ready"
        )
        owner_funded_hybrid = bool(config.ALLOW_OWNER_FUNDED_HYBRID)
        saved_deepseek_profile = next(
            (
                profile for profile in profiles
                if profile.provider_preset == "deepseek"
                and profile_supports_role(profile, "planner")
            ),
            None,
        )
        saved_sol_profile = next(
            (
                profile for profile in profiles
                if profile.provider_preset in {"openai", "openai_sol"}
                and profile_supports_role(profile, "critic")
            ),
            None,
        )
        local_deepseek_key = (
            saved_profile_api_key(saved_deepseek_profile, secrets_manager)
            if saved_deepseek_profile else ""
        )
        local_openai_key = (
            saved_profile_api_key(saved_sol_profile, secrets_manager)
            if saved_sol_profile else ""
        )
        allow_configured_hybrid_keys = bool(
            trusted_local_hybrid or owner_funded_hybrid
        )
        effective_hybrid_deepseek_key = (
            hybrid_deepseek_session_key
            or local_deepseek_key
            or (
                configured_deepseek_key
                if allow_configured_hybrid_keys else ""
            )
        )
        effective_hybrid_openai_key = (
            hybrid_openai_session_key
            or local_openai_key
            or (
                configured_openai_key
                if allow_configured_hybrid_keys else ""
            )
        )
        hybrid_deepseek_credential_source = (
            "session BYOK" if hybrid_deepseek_session_key
            else "local provider vault" if local_deepseek_key
            else "owner-funded server secret"
            if owner_funded_hybrid and configured_deepseek_key
            else "trusted local configuration"
            if trusted_local_hybrid and configured_deepseek_key
            else "missing"
        )
        hybrid_openai_credential_source = (
            "local Codex authentication"
            if hybrid_backend_id == "codex_local"
            else "selected secondary provider profile"
            if hybrid_backend_id == "configured_secondary"
            else "session BYOK" if hybrid_openai_session_key
            else "local provider vault" if local_openai_key
            else "owner-funded server secret"
            if owner_funded_hybrid and configured_openai_key
            else "trusted local configuration"
            if trusted_local_hybrid and configured_openai_key
            else "missing"
        )
        configured_sources = sum(bool(value) for value in (
            effective_alpha_key, effective_finnhub_key, effective_fmp_key,
            enable_nasdaq, enable_tradingview,
        ))
        configured_market_sources = sum(bool(value) for value in (
            effective_tiingo_key,
            effective_eodhd_key,
        ))
        resolved_providers = resolve_research_providers(
            ProviderResolutionRequest(
                ticker=ticker,
                research_profile=selected_profile_id,
                primary_profile_id=primary_profile_id,
                primary_profile=primary_profile,
                secondary_profile=secondary_profile,
                secrets_manager=secrets_manager,
                enable_llm=enable_llm,
                enable_secondary_review=enable_secondary_review,
                enable_independent_qa=enable_hybrid_research,
                auditor_backend_id=hybrid_backend_id,
                configured_secondary_available=configured_secondary_available,
                local_codex_status=codex_readiness.status,
                primary_fallback_key=effective_hybrid_deepseek_key,
                auditor_fallback_key=effective_hybrid_openai_key,
                primary_credential_source=hybrid_deepseek_credential_source,
                auditor_credential_source=hybrid_openai_credential_source,
            )
        )
        llm_provider = resolved_providers.primary_synthesis
        secondary_llm_provider = resolved_providers.secondary_synthesis
        hybrid_planner_provider = resolved_providers.primary_research
        hybrid_executor_provider = resolved_providers.auditor
        hybrid_execution_backend = resolved_providers.execution_backend
        hybrid_preflight = resolved_providers.preflight
        research_readiness = preview_research_readiness(
            hybrid_preflight,
            deterministic_available=True,
            independent_qa_requested=enable_hybrid_research,
            qa_relationship=resolved_providers.qa_relationship,
        )
        if configured_primary_profile_available:
            primary_preset = str(
                getattr(primary_profile, "provider_preset", "") or ""
            ).casefold()
            primary_connection_source = (
                "Saved DeepSeek profile"
                if "deepseek" in primary_preset
                else "Saved primary provider profile"
            )
        elif hybrid_deepseek_session_key:
            primary_connection_source = "Available this session · not saved"
        elif local_deepseek_key:
            primary_connection_source = "Saved securely on this device"
        else:
            primary_connection_source = hybrid_deepseek_credential_source
        if hybrid_backend_id == "codex_local":
            auditor_connection_source = (
                "Authenticated local Codex"
                if codex_readiness.status == "ready"
                else "Connect local Codex"
            )
        elif hybrid_backend_id == "configured_secondary":
            auditor_connection_source = "Saved secondary provider profile"
        elif hybrid_openai_session_key:
            auditor_connection_source = "Available this session · not saved"
        else:
            auditor_connection_source = hybrid_openai_credential_source
        source_names = ["SEC"]
        if enable_wisburg:
            source_names.append("Wisburg")
        if configured_sources:
            source_names.append(f"{configured_sources} consensus connector(s)")
        if configured_market_sources:
            source_names.append(f"{configured_market_sources} market connector(s)")
        connection_statuses = build_provider_connection_statuses(
            research_readiness,
            primary_source=primary_connection_source,
            auditor_source=auditor_connection_source,
            independent_qa_requested=enable_hybrid_research,
            required_source_summary=" · ".join(source_names),
        )
        st.markdown("**Connections for next run**")
        for connection in connection_statuses:
            with st.container(border=True):
                st.markdown(f"**{connection.label}**")
                st.write(f"{connection.status} · {connection.source}")
                if connection.message:
                    st.caption(connection.message)
        for notice in research_readiness.notices:
            st.info(notice)
        configured_external_sources = sum(bool(value) for value in (
            enable_fred, enable_bls, enable_bea, enable_census, enable_treasury,
            enable_ofr, enable_world_bank, enable_imf, enable_gdelt, enable_wisburg,
        ))
        enabled_macro_sources = [
            label for label, enabled in (
                ("FRED", enable_fred),
                ("BLS", enable_bls),
                ("BEA", enable_bea),
                ("Census", enable_census),
                ("Treasury", enable_treasury),
                ("OFR", enable_ofr),
                ("World Bank", enable_world_bank),
                ("IMF", enable_imf),
            ) if enabled
        ]
        external_cache_isolation = build_cache_isolation_key(
            enabled_source_families=[
                "sec",
                *(name.casefold().replace(" ", "_") for name in enabled_macro_sources),
                *(["wisburg"] if enable_wisburg else []),
            ],
            entitlement_rules={
                "wisburg": "metadata_and_excerpt_only" if enable_wisburg else "disabled",
                "public_macro": "public_registered_connectors",
            },
            workspace_scope="local",
        )
        external_provider = external_evidence_stack_from_config(
            fred_api_key=effective_fred_key,
            enable_fred=enable_fred,
            bls_api_key=effective_bls_key,
            enable_bls=enable_bls,
            bea_api_key=effective_bea_key,
            enable_bea=enable_bea,
            census_api_key=effective_census_key,
            enable_census=enable_census,
            enable_treasury=enable_treasury,
            enable_ofr=enable_ofr,
            enable_world_bank=enable_world_bank,
            enable_imf=enable_imf,
            enable_gdelt=enable_gdelt,
            wisburg_api_key=effective_wisburg_key,
            enable_wisburg=enable_wisburg,
            enable_default_macro=enable_default_macro,
            global_macro_mode=global_macro_mode,
            refresh_cache=refresh_macro_cache,
            cache_isolation=external_cache_isolation,
        )
        current_research_setup = _research_setup_snapshot(
            ticker=ticker,
            profile=selected_profile_id,
            budget_mode=budget_mode,
            macro_policy=macro_policy,
            macro_sources=enabled_macro_sources,
            wisburg_enabled=enable_wisburg,
            llm_enabled=enable_llm,
            hybrid_enabled=bool(
                research_readiness.qa_resolved
            ),
            consensus_source_count=configured_sources,
            market_source_count=configured_market_sources,
            qa_requested=enable_hybrid_research,
            qa_resolved=research_readiness.qa_resolved,
            qa_provider=(
                _friendly_provider_identity(
                    research_readiness.auditor_provider,
                    research_readiness.auditor_model,
                )
            ),
        )
        current_research_setup["agent_research_effort"] = (
            agent_research_effort
        )
        st.session_state._current_research_setup = current_research_setup
        if configured_sources:
            st.caption(
                f"Sources: {configured_sources} consensus connector(s) enabled."
            )
        else:
            st.warning("No consensus source is enabled. SEC research will still run.")
        if configured_market_sources:
            st.caption(
                f"Market data: {configured_market_sources} paid connector(s) "
                "enabled for price attribution."
            )
        primary_agent_ready = hybrid_preflight.deepseek.status == "ready"
        effective_agent_research = bool(enable_llm and primary_agent_ready)
        effective_hybrid_research = bool(
            enable_hybrid_research and research_readiness.qa_resolved
        )
        launch_source_families = tuple(dict.fromkeys([
            "sec",
            *(name.casefold().replace(" ", "_") for name in enabled_macro_sources),
            *(["wisburg"] if enable_wisburg else []),
            *(["consensus"] if configured_sources else []),
            *(["market_data"] if configured_market_sources else []),
        ]))
        _, launch_workspace_scope = _saved_research_scope()
        launch_data_cutoff = datetime.now(timezone.utc).date().isoformat()
        displayed_result = st.session_state.get("result")
        parent_context = _displayed_parent_context(displayed_result)

        if select_event_clicked and investigate_event_id:
            event_label = str(
                event_labels.get(
                    investigate_event_id,
                    f"Selected {ticker} event",
                )
            )
            _stage_research_intent(build_research_launch_intent(
                ticker=ticker,
                operation_kind=ResearchOperationKind.EVENT_INVESTIGATION,
                operation_label=f"Investigate {event_label}",
                objective=(
                    "Run a focused investigation of this event, its operating "
                    "mechanism, market reaction, and affected conclusions."
                ),
                launch_origin="sidebar_event_selector",
                research_profile="investigate_event",
                preferred_result_route="Research",
                event_id=str(investigate_event_id),
                required_source_families=launch_source_families,
                qa_enabled=effective_hybrid_research,
                workspace_scope=launch_workspace_scope,
                fingerprint_context={
                    "setup": current_research_setup,
                    "primary_provider": primary_profile_id,
                    "auditor_provider": secondary_profile_id,
                },
                **parent_context,
            ))
            st.session_state._investigation_queue_notice = (
                "Event investigation selected. Review and confirm it in Next "
                "research task. No providers were called."
            )
            st.rerun()

        selected_launch_intent = _selected_research_intent()
        full_launch_intent = build_research_launch_intent(
            ticker=ticker,
            operation_kind=ResearchOperationKind.FULL_RESEARCH,
            operation_label=(
                f"{selected_profile.label} for {ticker}"
                + (
                    " with Independent Research QA"
                    if effective_hybrid_research else ""
                )
            ),
            objective=(
                "Refresh broad company research using the selected depth, "
                "providers, source policy, and data cutoff."
            ),
            launch_origin="sidebar_full_research",
            research_profile=selected_profile_id,
            preferred_result_route="Decision",
            required_source_families=launch_source_families,
            qa_enabled=effective_hybrid_research,
            workspace_scope=launch_workspace_scope,
            fingerprint_context={
                "setup": current_research_setup,
                "primary_provider": primary_profile_id,
                "auditor_provider": secondary_profile_id,
                "source_families": launch_source_families,
                "data_cutoff": launch_data_cutoff,
                "cache_policy": "refresh" if refresh_macro_cache else "eligible_cache",
            },
        )
        st.session_state._current_full_research_intent = full_launch_intent
        launch_intent_for_run = selected_launch_intent or full_launch_intent
        launch_view_model = build_research_launch_intent_view_model(
            launch_intent_for_run
        )
        run_clicked = False
        confirm_repeat = False
        repeat_attempt_id = ""

        st.markdown("**Next research task**")
        launch_blocker = str(
            st.session_state.pop("_research_launch_blocker", "") or ""
        )
        if launch_blocker:
            st.error(launch_blocker)
        active_hybrid_job_id = str(
            st.session_state.get("hybrid_job_id") or ""
        )
        active_job = (
            _hybrid_job_manager().get(active_hybrid_job_id)
            if active_hybrid_job_id else None
        )
        focused_run_id = str(
            st.session_state.get("_focused_research_run_id") or ""
        )
        focused_snapshot = (
            _research_run_service().inspect(focused_run_id)
            if focused_run_id else None
        )
        focused_is_active = bool(
            focused_snapshot is not None
            and focused_snapshot.status in {
                "queued", "running", "cancelling", "waiting_for_credential",
                "research_attempt_finished", "validation_pending",
                "repair_required", "repair_running", "revision_pending",
                "audit_pending", "closure_pending",
            }
        )
        if active_job is not None or focused_is_active:
            with st.container(border=True):
                st.markdown("**Active research task**")
                st.write(
                    str(
                        getattr(active_job, "operation_label", "")
                        if active_job is not None
                        else getattr(focused_snapshot.command, "operation_label", "")
                    )
                    or f"Research for {ticker}"
                )
                st.caption(
                    f"{str(getattr(active_job, 'operation_kind', '') if active_job is not None else focused_snapshot.command.operation_kind).replace('_', ' ').title()} · "
                    f"{getattr(active_job, 'ticker', ticker) if active_job is not None else focused_snapshot.command.ticker} · "
                    f"{getattr(active_job, 'status', '') if active_job is not None else focused_snapshot.status}"
                )
                progress = getattr(active_job, "progress", None)
                st.caption(
                    "Current stage: "
                    + str(
                        getattr(progress, "current_stage", "")
                        or getattr(focused_snapshot, "current_stage", "")
                        or "Preparing research"
                    )
                )
                if active_job is not None and progress is not None:
                    if getattr(progress, "research_question", ""):
                        st.write(
                            "**Now researching:** "
                            + str(progress.research_question)
                        )
                    if getattr(progress, "current_role", ""):
                        st.caption(
                            "Current role: "
                            + str(progress.current_role).replace("_", " ").title()
                        )
                    if getattr(progress, "questions_total", 0):
                        st.caption(
                            f"Agenda answers: {progress.questions_answered} of "
                            f"{progress.questions_total} · "
                            f"{progress.tool_actions} research tool action(s)"
                        )
                    if getattr(progress, "source_capabilities_used", None):
                        st.caption(
                            "Sources in use: "
                            + ", ".join(progress.source_capabilities_used[:5])
                        )
                    st.caption(
                        "Elapsed: "
                        + _duration_label(
                            getattr(progress, "elapsed_seconds", 0)
                        )
                    )
            st.caption(
                "Another task cannot be launched or silently queued while this "
                "task is active. Your current selection is preserved."
            )
        else:
            with st.container(border=True):
                st.markdown(f"**{launch_view_model.operation_type_label}**")
                st.write(launch_view_model.operation_label)
                st.caption(f"Ticker: {launch_view_model.ticker}")
                st.caption(
                    "Status: "
                    + launch_view_model.status.replace("_", " ").title()
                )
                st.caption(f"Objective: {launch_view_model.objective}")
                st.caption(
                    f"Sources: {launch_view_model.source_summary} · "
                    f"{launch_view_model.qa_summary}"
                )
                st.caption(
                    f"Expected result: {launch_view_model.preferred_result_route}"
                )
                if launch_view_model.needs_source_setup:
                    required_names = tuple(
                        str(item).casefold()
                        for item in launch_intent_for_run.required_source_families
                    )
                    missing_required_sources = []
                    if any("consensus" in item or "estimate" in item for item in required_names) and not configured_sources:
                        missing_required_sources.append("consensus or estimate-history source")
                    if any("wisburg" in item for item in required_names) and not enable_wisburg:
                        missing_required_sources.append("Wisburg research source")
                    st.warning(
                        "Source setup required. Configure an eligible source above, "
                        "then mark the selection ready. It will not auto-start."
                    )
                    if missing_required_sources:
                        st.caption(
                            "Still needed: " + ", ".join(missing_required_sources) + "."
                        )
                    if st.button(
                        "Use current eligible sources",
                        key="mark-selected-research-sources-ready",
                        use_container_width=True,
                        disabled=bool(missing_required_sources),
                    ):
                        ready_intent = replace(
                            launch_intent_for_run,
                            status=ResearchLaunchIntentStatus.READY,
                            blocking_reason="",
                        )
                        _stage_research_intent(ready_intent)
                        st.session_state.pop("_investigation_source_setup", None)
                        st.session_state.pop("_open_additional_sources", None)
                        st.rerun()

            repeat_confirmation = st.session_state.get(
                "_research_launch_repeat_confirmation"
            )
            if (
                isinstance(repeat_confirmation, dict)
                and repeat_confirmation.get("fingerprint")
                == launch_intent_for_run.operation_fingerprint
            ):
                existing_status = str(
                    repeat_confirmation.get("status") or "completed"
                )
                st.warning(
                    "This exact research task already has an attempt with status "
                    f"{existing_status.replace('_', ' ')}. Loading it is the "
                    "zero-call default."
                )
                existing_reference = str(
                    repeat_confirmation.get("result_reference") or ""
                )
                repeat_cols = st.columns(
                    2 if existing_status == "completed" else 3
                )
                if repeat_cols[0].button(
                    "Load completed result"
                    if existing_status == "completed"
                    else "Load saved draft",
                    key="load-repeated-research-result",
                    use_container_width=True,
                    disabled=not existing_reference,
                ):
                    if _load_saved_artifact(
                        existing_reference,
                        scope_id=launch_workspace_scope,
                    ):
                        _clear_research_intent()
                        st.rerun()
                if existing_status == "completed":
                    if repeat_cols[1].button(
                        "Run again",
                        key="confirm-repeated-research-launch",
                        use_container_width=True,
                        type="primary",
                    ):
                        run_clicked = True
                        confirm_repeat = True
                        repeat_attempt_id = str(
                            repeat_confirmation.get("repeat_attempt_id") or ""
                        )
                else:
                    if repeat_cols[1].button(
                        "Resume / repair",
                        key="repair-repeated-research-launch",
                        use_container_width=True,
                    ):
                        repair_intent = build_research_launch_intent(
                            ticker=str(
                                repeat_confirmation.get("ticker")
                                or launch_intent_for_run.ticker
                            ),
                            operation_kind=ResearchOperationKind.REPAIR_RECHECK,
                            operation_label=(
                                "Repair and resume: "
                                + str(
                                    repeat_confirmation.get("operation_label")
                                    or launch_intent_for_run.operation_label
                                )
                            ),
                            objective=(
                                "Resume from the preserved draft, repair material "
                                "blockers, revalidate, and rerun required closure."
                            ),
                            launch_origin="repeat_partial_repair",
                            research_profile=launch_intent_for_run.research_profile,
                            preferred_result_route="Audit",
                            parent_run_id=str(
                                repeat_confirmation.get("run_id") or ""
                            ),
                            parent_artifact_id=existing_reference,
                            parent_evidence_hash=str(
                                repeat_confirmation.get("evidence_hash") or ""
                            ),
                            qa_enabled=launch_intent_for_run.qa_enabled,
                            workspace_scope=launch_intent_for_run.workspace_scope,
                        )
                        _stage_research_intent(repair_intent)
                        st.rerun()
                    if repeat_cols[2].button(
                        "Start new attempt",
                        key="restart-repeated-research-launch",
                        use_container_width=True,
                        type="primary",
                    ):
                        run_clicked = True
                        confirm_repeat = True
                        repeat_attempt_id = str(
                            repeat_confirmation.get("repeat_attempt_id") or ""
                        )
                if st.button(
                    "Cancel",
                    key="cancel-repeated-research-launch",
                    use_container_width=True,
                ):
                    st.session_state.pop(
                        "_research_launch_repeat_confirmation", None
                    )
                    st.rerun()
            elif launch_view_model.can_launch:
                separate_full_refresh = bool(
                    selected_launch_intent is None
                    and st.session_state.get("_last_completed_operation_kind")
                    in {
                        "action_investigation",
                        "targeted_investigation",
                        "event_investigation",
                        "analog_investigation",
                        "repair_recheck",
                        "repair_and_recheck",
                    }
                )
                if separate_full_refresh and not st.session_state.get(
                    "_confirm_separate_full_refresh"
                ):
                    if st.button(
                        "Start a separate full research refresh",
                        key="stage-separate-full-refresh",
                        use_container_width=True,
                        type="primary",
                    ):
                        st.session_state._confirm_separate_full_refresh = True
                        st.rerun()
                else:
                    cta = (
                        "Confirm full research refresh"
                        if separate_full_refresh
                        else launch_view_model.primary_cta
                    )
                    run_clicked = st.button(
                        cta,
                        key="run-selected-research-task",
                        type="primary",
                        use_container_width=True,
                    )
            if selected_launch_intent is not None:
                selection_cols = st.columns(2)
                if selection_cols[0].button(
                    "Clear selection",
                    key="clear-selected-research-task",
                    use_container_width=True,
                ):
                    _clear_research_intent()
                    st.rerun()
                if selection_cols[1].button(
                    "Switch to full research",
                    key="switch-to-full-research-task",
                    use_container_width=True,
                ):
                    _clear_research_intent()
                    st.session_state._confirm_separate_full_refresh = True
                    st.rerun()

        st.caption(
            "Every configured and entitled source is eligible. Selecting a task "
            "does not start provider or connector calls."
        )
        run_network_check = st.button("Run Network Diagnostics", use_container_width=True)
        test_connection = st.button("Test Consensus Sources", use_container_width=True)
        if (
            run_clicked
            and enable_hybrid_research
            and not primary_agent_ready
        ):
            st.warning(
                "Independent Research QA cannot start because its primary research "
                "provider is unavailable. Deterministic research will continue and "
                "the result will state that Independent QA was not run."
            )
        if active_hybrid_job_id:
            if active_job:
                st.info(
                    f"Active: {getattr(active_job, 'operation_label', '') or f'Research for {active_job.ticker}'}. "
                    f"Status: {active_job.status}. The job survives this page rerun."
                )
                job_cols = st.columns(2)
                if job_cols[0].button(
                    "Refresh status",
                    use_container_width=True,
                ):
                    st.rerun()
                if job_cols[1].button(
                    "Cancel active work",
                    use_container_width=True,
                ):
                    _hybrid_job_manager().cancel(active_hybrid_job_id)
                    st.warning("Cancellation requested.")
        st.caption("US equities MVP. HK support can plug into the same pipeline later.")

    if load_demo_clicked:
        st.session_state.result = demo_result(selected_case.ticker)
        st.session_state.demo_mode = selected_case.ticker
        UiSessionState(st.session_state).request_public_demo_landing()
        st.session_state._displayed_saved_artifact_id = ""
        st.session_state._displayed_parent_run_id = ""
        st.session_state._displayed_saved_storage_scope = ""
        st.session_state._displayed_saved_scope_id = ""
        st.session_state._displayed_result_origin = "frozen_demo"
        st.session_state._displayed_operation_label = ""
        st.session_state._displayed_outcome = ""
        st.session_state._displayed_research_setup = _demo_research_setup(
            selected_case
        )
        st.session_state.pop("_displayed_research_context", None)
        st.session_state._pending_demo_preset = {
            "research_profile": "deep_initiation" if case_budget_mode == "Premium" else default_profile,
            "budget_mode": case_budget_mode or budget_mode,
        }
        st.rerun()

    if run_network_check:
        with st.spinner("Running network diagnostics from the Streamlit process..."):
            st.session_state.network_diagnostics = run_network_diagnostics(timeout_seconds=8)

    if st.session_state.network_diagnostics:
        report = st.session_state.network_diagnostics
        with st.sidebar.expander("Network Diagnostics", expanded=True):
            st.caption(f"Class: {report.network_class}")
            st.caption(report.summary)
            if report.runtime_context:
                st.caption(f"Python: {report.runtime_context.get('python_executable', 'Unknown')}")
                st.caption(f"PID: {report.runtime_context.get('pid', 'Unknown')}")
            for action in report.suggested_actions[:4]:
                st.write(f"- {action}")
            rows = [
                {
                    "Provider": probe.provider,
                    "Check": probe.check_type,
                    "Host": probe.endpoint_host,
                    "Status": probe.status,
                    "Class": probe.failure_class,
                    "Fix": probe.suggested_fix,
                }
                for probe in report.probes
            ]
            st.dataframe(rows, hide_index=True, use_container_width=True)

    if test_connection:
        if not configured_sources:
            st.sidebar.error("Enter a free provider key or enable an unofficial fallback.")
        elif not ticker:
            st.sidebar.error("Enter a ticker first.")
        else:
            with st.spinner(f"Testing consensus sources for {ticker}..."):
                package = build_consensus_provider(
                    alpha_vantage_key=effective_alpha_key,
                    finnhub_key=effective_finnhub_key,
                    fmp_key=effective_fmp_key,
                    enable_nasdaq=enable_nasdaq,
                    enable_tradingview=enable_tradingview,
                    enable_yahoo=False,
                ).fetch_package(ticker)
            if package.status == "Unavailable":
                st.sidebar.error("No enabled source returned usable consensus data.")
            elif package.status.startswith("Partial"):
                st.sidebar.warning(package.status)
            else:
                st.sidebar.success("Official consensus data is available.")
            for status in package.provider_statuses:
                st.sidebar.caption(f"{status.provider}: {status.status}")
            for gap in package.data_gaps:
                st.sidebar.caption(gap)

    # Discard legacy implicit-run markers without launching hidden work. Any new
    # selection is represented by ResearchLaunchIntent.
    st.session_state.pop("_pending_research_operation", None)
    st.session_state.pop("_pending_publication_repair", None)
    st.session_state.pop("_pending_analog_investigation", None)

    if run_clicked and ticker:
        selected_intent = launch_intent_for_run
        if (
            selected_intent.operation_kind is ResearchOperationKind.REPAIR_RECHECK
            and not selected_intent.parent_run_id
        ):
            st.session_state._research_launch_blocker = (
                "Repair and recheck could not start because the displayed result "
                "has no recoverable parent run. Reload the saved result and try "
                "again; no provider calls were made."
            )
            st.rerun()
        if selected_intent.qa_enabled and not research_readiness.qa_resolved:
            st.session_state._research_launch_blocker = (
                "Independent QA is required for this task but its auditor is not "
                "ready. Connect local Codex or select an available auditor; the "
                "task selection was preserved and no provider calls were made."
            )
            st.rerun()
        effective_hybrid_for_run = bool(
            effective_hybrid_research
            or (selected_intent.qa_enabled and research_readiness.qa_resolved)
        )
        ticker = selected_intent.ticker
        effective_profile = selected_intent.research_profile
        operation_kind = selected_intent.operation_kind.value
        operation_label = selected_intent.operation_label
        investigation_action_id = selected_intent.action_id
        investigation_action_kind = selected_intent.action_kind
        investigation_objective = selected_intent.objective
        investigation_semantic_key = selected_intent.action_semantic_key
        investigation_source_families = tuple(
            selected_intent.required_source_families
        )
        investigation_preferred_connectors = tuple(
            selected_intent.preferred_connectors
        )
        investigate_event_id = selected_intent.event_id or None
        investigate_analog_id = selected_intent.analog_id or None
        result_route = selected_intent.preferred_result_route
        pending_publication_repair = (
            {
                "source_run_id": selected_intent.parent_run_id,
                "source_evidence_hash": selected_intent.parent_evidence_hash,
                "action_ids": tuple(selected_intent.repair_action_ids),
                "retry_stages": tuple(selected_intent.repair_retry_stages),
            }
            if selected_intent.operation_kind
            is ResearchOperationKind.REPAIR_RECHECK
            else None
        )
        active_research_setup = dict(current_research_setup)
        active_research_setup["profile"] = effective_profile
        active_research_setup["ticker"] = ticker
        st.session_state._active_research_setup = active_research_setup
        frozen_source_families = tuple(dict.fromkeys([
            "sec",
            *(name.casefold().replace(" ", "_") for name in enabled_macro_sources),
            *( ["wisburg"] if enable_wisburg else [] ),
            *( ["consensus"] if configured_sources else [] ),
            *( ["market_data"] if configured_market_sources else [] ),
        ]))
        frozen_run_command = ResearchRunCommand(
            ticker=ticker,
            research_depth=effective_profile,
            primary_provider_id=(
                primary_profile_id
                or str(getattr(hybrid_planner_provider, "provider_name", "") or "")
            ),
            auditor_provider_id=(
                "local_codex"
                if hybrid_backend_id == "codex_local"
                else secondary_profile_id
                if hybrid_backend_id == "configured_secondary"
                else str(getattr(hybrid_executor_provider, "provider_name", "") or "")
            ),
            independent_qa_enabled=effective_hybrid_for_run,
            adaptive_macro=macro_policy != "Paused",
            wisburg_enabled=enable_wisburg,
            enabled_source_families=frozen_source_families,
            data_cutoff=launch_data_cutoff,
            cache_bypass=bool(refresh_macro_cache),
            investigate_event_id=investigate_event_id or "",
            investigation_action_id=investigation_action_id,
            investigation_action_kind=investigation_action_kind,
            investigation_objective=investigation_objective,
            investigation_semantic_key=investigation_semantic_key,
            research_intent_id=selected_intent.research_intent_id,
            investigation_source_families=investigation_source_families,
            investigation_preferred_connectors=(
                investigation_preferred_connectors
            ),
            operation_kind=operation_kind,
            operation_label=operation_label,
            result_route=result_route,
            execution_mode=(
                "repair_and_recheck"
                if pending_publication_repair else "research"
            ),
            parent_run_id=selected_intent.parent_run_id,
            parent_evidence_hash=selected_intent.parent_evidence_hash,
            repair_action_ids=tuple(selected_intent.repair_action_ids),
            repair_retry_stages=tuple(selected_intent.repair_retry_stages),
            workspace_scope=selected_intent.workspace_scope,
            agent_research_effort=agent_research_effort,
            credential_lease_ids=_issue_session_credential_leases(
                primary_key=hybrid_deepseek_session_key,
                auditor_key=hybrid_openai_session_key,
            ),
        )
        launch_result = _research_launch_service().launch(
            selected_intent,
            frozen_run_command,
            confirm_repeat=confirm_repeat,
            repeat_attempt_id=repeat_attempt_id,
        )
        if launch_result.confirmation_required:
            existing = launch_result.snapshot
            st.session_state._research_launch_repeat_confirmation = {
                "fingerprint": selected_intent.operation_fingerprint,
                "run_id": str(getattr(existing, "run_id", "") or ""),
                "status": str(getattr(existing, "status", "") or "completed"),
                "result_reference": str(
                    getattr(existing, "result_reference", "") or ""
                ),
                "evidence_hash": str(
                    getattr(existing, "evidence_hash", "") or ""
                ),
                "ticker": str(
                    getattr(getattr(existing, "command", None), "ticker", "")
                    or selected_intent.ticker
                ),
                "operation_label": str(
                    getattr(
                        getattr(existing, "command", None),
                        "operation_label",
                        "",
                    )
                    or selected_intent.operation_label
                ),
                "repeat_attempt_id": uuid.uuid4().hex,
            }
            st.rerun()
        if not launch_result.created:
            existing = launch_result.snapshot
            if existing is not None:
                st.session_state.research_run_id = existing.run_id
                st.session_state._focused_research_run_id = existing.run_id
            st.info(
                "The same research task is already active. The existing task "
                "was focused; no duplicate provider calls were started."
            )
            st.rerun()
        lifecycle_run_id = str(launch_result.snapshot.run_id)
        st.session_state.research_run_id = lifecycle_run_id
        try:
            store = ResearchStore()

            def execute_live_research(
                cancel_event=None,
                progress_callback=None,
            ):
                if configured_sources:
                    provider = build_consensus_provider(
                        store=store,
                        alpha_vantage_key=effective_alpha_key,
                        finnhub_key=effective_finnhub_key,
                        fmp_key=effective_fmp_key,
                        enable_nasdaq=enable_nasdaq,
                        enable_tradingview=enable_tradingview,
                        enable_yahoo=False,
                    )
                    return run_us_equity_research(
                        ticker,
                        sec_client=SecClient(user_agent=sec_user_agent),
                        price_client=StooqPriceClient(
                            store=store,
                            tiingo_key=effective_tiingo_key,
                            eodhd_key=effective_eodhd_key,
                        ),
                        consensus=provider,
                        external_evidence_provider=external_provider,
                        llm_provider=llm_provider,
                        secondary_llm_provider=secondary_llm_provider,
                        enable_secondary_llm_review=enable_secondary_review,
                        secondary_llm_min_stage=secondary_min_stage,
                        llm_language_policy=language_policy,
                        budget_mode=budget_mode,
                        store=store,
                        research_profile=effective_profile,
                        investigate_event_id=investigate_event_id,
                        investigate_analog_id=investigate_analog_id,
                        investigation_action_id=investigation_action_id,
                        investigation_action_kind=investigation_action_kind,
                        investigation_objective=investigation_objective,
                        investigation_semantic_key=investigation_semantic_key,
                        research_intent_id=selected_intent.research_intent_id,
                        investigation_source_families=investigation_source_families,
                        investigation_preferred_connectors=(
                            investigation_preferred_connectors
                        ),
                        enable_hybrid_research=effective_hybrid_for_run,
                        enable_agent_research=effective_agent_research,
                        enable_independent_research_qa=effective_hybrid_for_run,
                        resume_hybrid_run_id=str(
                            (pending_publication_repair or {}).get("source_run_id")
                            or ""
                        ),
                        hybrid_planner_provider=hybrid_planner_provider,
                        hybrid_executor_provider=hybrid_executor_provider,
                        hybrid_checker_provider=hybrid_planner_provider,
                        hybrid_critic_provider=None,
                        hybrid_execution_backend=hybrid_execution_backend,
                        hybrid_cancel_event=cancel_event,
                        hybrid_progress_callback=progress_callback,
                        agent_research_effort=agent_research_effort,
                        repair_stage_repository=_repair_stage_repository(
                            str(Path(config.RESEARCH_DB_PATH).resolve())
                        ),
                        repair_attempt_id=lifecycle_run_id,
                        repair_execution_policy=_repair_execution_policy(
                            closure_retry=(
                                "closure_checker"
                                in selected_intent.repair_retry_stages
                            )
                        ),
                        repair_retry_stages=tuple(
                            selected_intent.repair_retry_stages
                        ),
                    )
                return run_us_equity_research(
                    ticker,
                    sec_client=SecClient(user_agent=sec_user_agent),
                    price_client=StooqPriceClient(
                        store=store,
                        tiingo_key=effective_tiingo_key,
                        eodhd_key=effective_eodhd_key,
                    ),
                    external_evidence_provider=external_provider,
                    llm_provider=llm_provider,
                    secondary_llm_provider=secondary_llm_provider,
                    enable_secondary_llm_review=enable_secondary_review,
                    secondary_llm_min_stage=secondary_min_stage,
                    llm_language_policy=language_policy,
                    budget_mode=budget_mode,
                    store=store,
                    research_profile=effective_profile,
                    investigate_event_id=investigate_event_id,
                    investigate_analog_id=investigate_analog_id,
                    investigation_action_id=investigation_action_id,
                    investigation_action_kind=investigation_action_kind,
                    investigation_objective=investigation_objective,
                    investigation_semantic_key=investigation_semantic_key,
                    research_intent_id=selected_intent.research_intent_id,
                    investigation_source_families=investigation_source_families,
                    investigation_preferred_connectors=(
                        investigation_preferred_connectors
                    ),
                    enable_hybrid_research=effective_hybrid_for_run,
                    enable_agent_research=effective_agent_research,
                    enable_independent_research_qa=effective_hybrid_for_run,
                    resume_hybrid_run_id=str(
                        (pending_publication_repair or {}).get("source_run_id")
                        or ""
                    ),
                    hybrid_planner_provider=hybrid_planner_provider,
                    hybrid_executor_provider=hybrid_executor_provider,
                    hybrid_checker_provider=hybrid_planner_provider,
                    hybrid_critic_provider=None,
                    hybrid_execution_backend=hybrid_execution_backend,
                    hybrid_cancel_event=cancel_event,
                    hybrid_progress_callback=progress_callback,
                    agent_research_effort=agent_research_effort,
                    repair_stage_repository=_repair_stage_repository(
                        str(Path(config.RESEARCH_DB_PATH).resolve())
                    ),
                    repair_attempt_id=lifecycle_run_id,
                    repair_execution_policy=_repair_execution_policy(
                        closure_retry=(
                            "closure_checker"
                            in selected_intent.repair_retry_stages
                        )
                    ),
                    repair_retry_stages=tuple(
                        selected_intent.repair_retry_stages
                    ),
                )
            if effective_agent_research:
                if st.session_state.get("hybrid_job_id"):
                    st.warning(
                        "An agent research job is already active. Refresh "
                        "or cancel it before starting another."
                    )
                else:
                    displayed = st.session_state.get("result")
                    displayed_context = (
                        DisplayedResultContext(
                            result_type=(
                                "frozen_demo"
                                if getattr(displayed, "demo_case", None)
                                else "previous_live"
                            ),
                            label=(
                                f"Frozen {displayed.identity.ticker} demo"
                                if getattr(displayed, "demo_case", None)
                                else f"Previous {displayed.identity.ticker} live result"
                            ),
                            ticker=displayed.identity.ticker,
                            run_id=displayed.run_manifest.run_id,
                            generated_at=displayed.run_manifest.generated_at,
                            message=(
                                "This displayed result does not contain output "
                                "from the active Research QA job."
                            ),
                        )
                        if displayed else None
                    )
                    state = _hybrid_job_manager().start(
                        ticker,
                        execute_live_research,
                        providers=[
                            hybrid_planner_provider,
                            hybrid_executor_provider,
                        ],
                        research_profile=effective_profile,
                        preflight=hybrid_preflight,
                        displayed_result=displayed_context,
                        operation_kind=operation_kind,
                        operation_label=operation_label,
                        investigation_action_id=investigation_action_id,
                        investigation_action_kind=investigation_action_kind,
                        investigation_semantic_key=investigation_semantic_key,
                        result_route=result_route,
                        originating_artifact_id=str(
                            st.session_state.get("_displayed_saved_artifact_id") or ""
                        ),
                        saved_setup_summary={
                            **active_research_setup,
                            "operation_kind": operation_kind,
                            "operation_label": operation_label,
                            "operation_objective": selected_intent.objective,
                            "investigation_action_id": investigation_action_id,
                            "investigation_action_kind": investigation_action_kind,
                            "investigation_semantic_key": investigation_semantic_key,
                        },
                        saved_storage_scope=_saved_research_scope()[0].value,
                        saved_scope_id=_saved_research_scope()[1],
                        job_id=lifecycle_run_id,
                    )
                    st.session_state.hybrid_job_id = state.job_id
                    _clear_research_intent()
                    st.session_state.pop("_confirm_separate_full_refresh", None)
                    st.info(
                        (
                            "Agent research with Independent Research QA started in the background. "
                            if effective_hybrid_for_run else
                            "Primary agent research started in the background. Independent QA is not configured for this run. "
                        )
                        + "Use Refresh QA status in the "
                        "sidebar; the current result remains visible."
                    )
                    st.rerun()
            else:
                parent_displayed_result = st.session_state.get("result")
                _research_run_service().mark_running(
                    lifecycle_run_id,
                    "deterministic_bootstrap",
                    "Deterministic research started.",
                )
                with st.spinner(f"Running research workflow for {ticker}..."):
                    st.session_state.result = execute_live_research()
                    st.session_state.demo_mode = None
                    st.session_state._displayed_research_setup = {
                        **active_research_setup,
                        "operation_kind": operation_kind,
                        "operation_label": operation_label,
                        "operation_objective": selected_intent.objective,
                        "investigation_action_id": investigation_action_id,
                        "investigation_action_kind": investigation_action_kind,
                        "investigation_semantic_key": investigation_semantic_key,
                    }
                completed_result = st.session_state.result
                completed_manifest = getattr(
                    completed_result, "run_manifest", None
                )
                saved_artifact_id = ""
                try:
                    saved_storage_scope, saved_scope_id = _saved_research_scope()
                    saved_artifact_id = _save_displayable_result(
                        completed_result,
                        run_id=lifecycle_run_id,
                        operation_kind=operation_kind,
                        operation_label=operation_label,
                        run_status="completed",
                        preferred_route=result_route,
                        setup_summary={
                            **active_research_setup,
                            "operation_kind": operation_kind,
                            "operation_label": operation_label,
                            "operation_objective": selected_intent.objective,
                            "investigation_action_id": investigation_action_id,
                            "investigation_action_kind": investigation_action_kind,
                            "investigation_semantic_key": investigation_semantic_key,
                        },
                        storage_scope=saved_storage_scope,
                        scope_id=saved_scope_id,
                        parent_artifact_id=str(
                            st.session_state.get("_displayed_saved_artifact_id") or ""
                        ),
                    )
                    loaded = _saved_research_service_for_scope(
                        saved_storage_scope
                    ).load_result(
                        saved_artifact_id,
                        scope_id=saved_scope_id,
                    )
                    _apply_loaded_saved_research(loaded, show_notice=False)
                    _research_run_service().record_event(
                        lifecycle_run_id,
                        stage="result_persistence",
                        status="completed",
                        summary="Displayable research result saved for zero-call loading.",
                        details={"saved_artifact_id": saved_artifact_id},
                        idempotency_key=f"result-saved:{saved_artifact_id}",
                    )
                except (
                    SavedResearchError,
                    SavedResearchCodecError,
                    SavedResearchStorageError,
                    OSError,
                    ValueError,
                ) as exc:
                    st.session_state._completed_in_memory_result = {
                        "result": completed_result,
                        "run_id": lifecycle_run_id,
                        "operation_kind": operation_kind,
                        "operation_label": operation_label,
                        "ticker": ticker,
                        "outcome": "completed",
                        "preferred_route": result_route,
                        "parent_artifact_id": str(
                            st.session_state.get("_displayed_saved_artifact_id") or ""
                        ),
                        "setup_summary": dict(
                            st.session_state.get("_displayed_research_setup") or {}
                        ),
                        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "save_error": {
                            "message": str(exc),
                            "reason": str(getattr(exc, "reason", "") or "verification_failed"),
                            "failure_path": str(getattr(exc, "failure_path", "") or ""),
                            "annotation": str(getattr(exc, "annotation", "") or ""),
                        },
                    }
                    _apply_in_memory_research(
                        st.session_state._completed_in_memory_result
                    )
                    st.warning(
                        "Research completed and remains visible, but it could not be "
                        f"saved for restart recovery. {exc}"
                    )
                _research_run_service().finish(
                    lifecycle_run_id,
                    status="completed",
                    result_reference=saved_artifact_id,
                    evidence_hash=str(
                        getattr(
                            completed_manifest,
                            "admitted_evidence_hash",
                            "",
                        )
                        or ""
                    ),
                    stopping_reason=str(
                        getattr(
                            completed_manifest,
                            "research_stopping_reason",
                            "",
                        )
                        or "completed"
                    ),
                )
                # See the background completion path: journey presentation is
                # always reconstructed from result plus displayed context.
                _clear_research_intent()
                st.session_state.pop("_confirm_separate_full_refresh", None)
                st.session_state._last_completed_operation_kind = operation_kind
                st.session_state._last_completed_operation_label = operation_label
        except SecClientError as exc:
            if lifecycle_run_id:
                try:
                    _research_run_service().finish(
                        lifecycle_run_id,
                        status="failed",
                        stopping_reason="sec_source_failure",
                        errors=(str(exc),),
                    )
                except (KeyError, ValueError, OSError):
                    pass
            retained = st.session_state.get("result")
            retained_label = (
                f" The previously loaded {retained.identity.ticker} result remains visible below."
                if retained is not None else ""
            )
            st.error(
                f"Live SEC research could not complete: {exc}{retained_label} "
                "This is a source-access failure, not a finding about the company."
            )
        except Exception as exc:  # pragma: no cover - UI guardrail
            if lifecycle_run_id:
                try:
                    _research_run_service().finish(
                        lifecycle_run_id,
                        status="failed",
                        stopping_reason="pipeline_failure",
                        errors=(str(exc),),
                    )
                except (KeyError, ValueError, OSError):
                    pass
            st.exception(exc)
            st.stop()

    ui_state = UiSessionState(st.session_state)
    result: ResearchResult | None = ui_state.result
    if result is None:
        st.info("Enter a US ticker and run the workflow.")
        return

    demo_mode = st.session_state.get("demo_mode")
    if demo_mode:
        st.caption(
            f"Sample artifact: frozen {demo_mode} Deep Initiation research. "
            "Run live research to replace it with your configured sources and providers."
        )
    repair_notice = st.session_state.pop("_publication_repair_notice", "")
    if repair_notice:
        st.info(repair_notice)
    investigation_notice = st.session_state.pop("_investigation_queue_notice", "")
    if investigation_notice:
        st.info(investigation_notice)

    render_header(
        result,
        active_job_id=str(st.session_state.get("hybrid_job_id") or ""),
    )
    # Apply a renderer-originated navigation request before the route radio is
    # created.  This is the only point in a rerun allowed to update
    # ``research_view`` directly.
    ui_state.apply_pending_route()
    if str(st.session_state.get("research_view") or "") not in {
        "Decision", "Research", "Evidence", "Audit",
    }:
        ui_state.selected_route = "Decision"
    research_view = st.radio(
        "Research view",
        ["Decision", "Research", "Evidence", "Audit"],
        horizontal=True,
        label_visibility="collapsed",
        key="research_view",
    )
    if research_view == "Decision":
        decision_intent = render_decision_page_component(
            st,
            build_decision_brief_view_model(
                result,
                minimum_expected_return_pct=float(
                    st.session_state.get("actionability_min_return_pct", 10)
                ),
                minimum_evidence_score=int(
                    st.session_state.get("actionability_min_evidence_score", 60)
                ),
            ),
            assumptions_renderer=(
                (
                    lambda: _render_market_implied_assumption_editor(
                        result,
                        "decision",
                    )
                )
                if getattr(result, "market_implied_expectations", None)
                else None
            ),
        )
        _handle_decision_page_intent(
            result,
            decision_intent,
            tiingo_key=effective_tiingo_key,
            eodhd_key=effective_eodhd_key,
        )
        completed_journey = _investigation_journey_view_model(
            result,
            active_job_id=str(st.session_state.get("hybrid_job_id") or ""),
        )
        if completed_journey is not None and not completed_journey.active:
            render_investigation_journey(
                st,
                completed_journey,
                heading="Latest investigation result",
                compact=True,
                on_open_evidence=lambda: ui_state.request_route(
                    "Evidence", origin="latest_investigation_result"
                ),
            )
    elif research_view == "Research":
        route_values = [route.value for route in ResearchSubroute]
        selected_route_value = st.radio(
            "Research section",
            route_values,
            horizontal=True,
            key="research_subroute",
        )
        selected_route = ResearchSubroute(selected_route_value)
        render_research_route_header(
            st,
            build_research_route_view_model(result, selected_route),
        )
        render_research_session_workbench(
            st,
            build_research_session_view_model(result, selected_route),
        )
        if selected_route is ResearchSubroute.THESIS_DRIVERS:
            render_research_modes(result)
            render_causal_thesis_graphs(result)
            render_idea_factory(result)
            render_idea_scorer(result)
        elif selected_route is ResearchSubroute.MODEL_VALUATION:
            render_company_model_workspace(result)
            render_earnings_surprise_proxy(result)
            render_market_implied_expectations(result)
        elif selected_route is ResearchSubroute.EXPECTATIONS_REACTION:
            render_recent_market_context(result)
            render_price_move_attribution(result)
            render_market_capture_readiness(result)
        else:
            render_historical_analog_intelligence(result, compact=False)
    elif research_view == "Evidence":
        displayed_setup = dict(
            st.session_state.get("_displayed_research_setup") or {}
        )
        evidence_intent = render_evidence_page_overview(
            st,
            build_evidence_page_view_model(
                result,
                displayed_context=st.session_state.get("_displayed_research_context"),
                operation_kind=str(displayed_setup.get("operation_kind") or ""),
                operation_label=str(
                    displayed_setup.get("operation_label")
                    or st.session_state.get("_displayed_operation_label")
                    or ""
                ),
                operation_action_id=str(
                    displayed_setup.get("investigation_action_id") or ""
                ),
                outcome=str(st.session_state.get("_displayed_outcome") or ""),
            ),
        )
        if evidence_intent == "run_refreshed_answer_qa":
            parent_context = _displayed_parent_context(result)
            profile = getattr(result, "research_profile", None)
            _stage_research_intent(build_research_launch_intent(
                ticker=str(getattr(getattr(result, "identity", None), "ticker", "") or ""),
                operation_kind=ResearchOperationKind.REPAIR_RECHECK,
                operation_label="Independent QA for refreshed cash-conversion answer",
                objective=(
                    "Audit the refreshed two-period cash-conversion bridge, address any "
                    "material findings, and run final closure."
                ),
                launch_origin="evidence_refreshed_answer_qa",
                research_profile=str(
                    getattr(profile, "profile_id", "")
                    or displayed_setup.get("research_profile")
                    or "deep_initiation"
                ),
                preferred_result_route="Evidence",
                research_intent_id="cash_generation_capital_allocation:recurring_cash_conversion",
                required_source_families=("sec_filing",),
                qa_enabled=True,
                **parent_context,
            ))
            st.rerun()
        render_investigation_action_register(
            st,
            build_investigation_action_view_models(result),
        )
        render_research_radar(result)
        render_validated_claims_and_source_plan(result)
        render_management_sources(result)
        render_news_intelligence_report(result, compact=False)
        render_evidence_work_order(result)
        render_research_questions(result)
        render_thesis_monitor(result)
    else:
        st.markdown("### Independent Research QA")
        st.caption(
            "Frozen audit, adversarial investigation, primary responses, and closure are shown without implying that an incomplete review passed."
        )
        repair_intent = render_publication_readiness(
            st,
            build_publication_readiness_view_model(result),
        )
        _handle_publication_repair_intent(result, repair_intent)
        render_integrity_timeline_component(
            st,
            build_integrity_timeline_view_model(
                result,
                displayed_context=st.session_state.get("_displayed_research_context"),
            ),
        )
        render_investigation_action_register(
            st,
            build_investigation_action_view_models(result),
            heading="Investigation and closure actions",
        )
        durable_run_id = str(
            st.session_state.get("research_run_id") or ""
        )
        durable_snapshot = (
            _research_run_service().inspect(durable_run_id)
            if durable_run_id else None
        )
        if durable_snapshot is not None:
            with st.expander(
                "Durable execution event log",
                expanded=False,
            ):
                render_integrity_timeline_component(
                    st,
                    build_durable_run_timeline_view_model(
                        durable_snapshot,
                        _research_run_service().timeline(durable_run_id),
                    ),
                    heading="Durable Run Events",
                )
        render_ic_audit_trail(result)
        render_memo(result)


def _streamlit_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def _display_timestamp(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown"
    if len(text) == 10:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    zone_name = str(st.session_state.get("display_timezone") or "UTC")
    try:
        parsed = parsed.astimezone(ZoneInfo(zone_name))
    except Exception:
        parsed = parsed.astimezone(timezone.utc)
        zone_name = "UTC"
    return parsed.strftime("%b %d, %Y %I:%M %p") + f" {zone_name}"


def _duration_label(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute}m"
    if minutes:
        return f"{minutes}m {second}s"
    return f"{second}s"


def _relative_time(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return "at an unknown time"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return f"on {text}"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    seconds = max(
        0,
        int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()),
    )
    if seconds < 90:
        return "less than 2 minutes ago"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86_400:
        return f"{seconds // 3600} hours ago"
    return f"{seconds // 86_400} days ago"


def _localized_freshness_summary(freshness) -> str:
    components = {
        item.component: item for item in freshness.components
    }
    run = components.get("Research run")
    price = components.get("Market price")
    filing = components.get("Filings")
    consensus = components.get("Consensus")
    run_cutoff = run.source_as_of if run else freshness.run_completed_at
    completion_label = (
        f"frozen {_display_timestamp(run_cutoff)}"
        if freshness.result_type == "frozen_demo" else
        f"completed {_relative_time(run_cutoff)} "
        f"({_display_timestamp(run_cutoff)})"
    )
    consensus_label = "unavailable"
    if consensus and consensus.source_as_of:
        consensus_label = _display_timestamp(consensus.source_as_of)
        if consensus.status == "Illustrative only":
            consensus_label = f"illustrative only through {consensus_label}"
    return (
        f"{freshness.overall_status}. Research {completion_label}; "
        f"price through {_display_timestamp(price.source_as_of if price else '')}; "
        f"filings through {_display_timestamp(filing.source_as_of if filing else '')}; "
        f"consensus {consensus_label}."
    )


def _human_decision_comparison(delta) -> str:
    result_type = str(
        getattr(delta, "compared_with_result_type", None) or "live"
    ).replace("_", " ")
    compared_at = getattr(delta, "compared_with_completed_at", None)
    timestamp = (
        _display_timestamp(compared_at)
        if compared_at else "timestamp unavailable"
    )
    profile_value = str(
        getattr(delta, "compared_with_profile", "") or ""
    ).replace("_", " ")
    profile = f", {profile_value}" if profile_value else ""
    verb = "completed" if compared_at else "saved"
    return f"Prior {result_type} run {verb} {timestamp}{profile}"


def render_active_hybrid_job_banner(
    job_id: str,
    *,
    result: ResearchResult | None = None,
) -> None:
    if not job_id:
        return
    state = _hybrid_job_manager().get(job_id)
    if state is None or state.status not in {
        "queued", "running", "saving_result", "cancelling"
    }:
        return
    progress = getattr(state, "progress", None)
    displayed = getattr(state, "displayed_result", None)
    operation_kind = str(
        getattr(state, "operation_kind", "full_research")
        or "full_research"
    )
    operation_label = str(
        getattr(state, "operation_label", "")
        or f"Research for {state.ticker}"
    )
    verb = {
        "targeted_investigation": "Investigating",
        "action_investigation": "Investigating",
        "event_investigation": "Investigating",
        "analog_investigation": "Investigating",
        "repair_recheck": "Repairing and rechecking",
        "repair_and_recheck": "Repairing and rechecking",
    }.get(operation_kind, "Running")
    if result is not None:
        journey_model = _investigation_journey_view_model(
            result,
            active_job_id=job_id,
        )
        if journey_model is not None:
            render_investigation_journey(
                st,
                journey_model,
                heading="Research journey",
                compact=True,
            )
    displayed_label = (
        displayed.label.lower() if displayed else "the prior result"
    )
    st.caption(
        f"{verb} {operation_label}. {displayed_label.title()} remains visible "
        "until the new result is saved and loaded."
    )
    with st.expander("Live run details", expanded=False):
        repair_stage_index = int(
            getattr(progress, "repair_stage_index", 0) or 0
        )
        repair_stage_count = int(
            getattr(progress, "repair_stage_count", 0) or 0
        )
        if repair_stage_index and repair_stage_count:
            st.caption(
                f"Repair stage {repair_stage_index} of {repair_stage_count} · "
                f"{getattr(progress, 'current_stage', 'Working')}"
            )
        st.caption(
            f"Model calls: {getattr(progress, 'completed_model_calls', 0)}/"
            f"{getattr(progress, 'attempted_model_calls', 0)} completed · "
            "Provider: "
            + (
                " / ".join(
                    item for item in [
                        getattr(progress, "provider", ""),
                        getattr(progress, "model", ""),
                    ] if item
                )
                or "Deterministic preparation"
            )
        )
        st.caption(
            f"Started {_display_timestamp(getattr(progress, 'started_at', ''))}; "
            f"last update {_display_timestamp(getattr(state, 'updated_at', ''))}."
        )
        if getattr(progress, "execution_state", ""):
            execution_state = str(progress.execution_state).replace(
                "_", " "
            ).title()
            activity_at = _display_timestamp(
                getattr(progress, "execution_last_activity_at", "")
            )
            deadline_at = _display_timestamp(
                getattr(progress, "execution_hard_deadline_at", "")
            )
            st.caption(
                f"Local Codex: {execution_state} · last activity "
                f"{activity_at} · hard deadline {deadline_at}."
            )
        if getattr(progress, "message", ""):
            st.caption(progress.message)
        if getattr(progress, "failure_class", ""):
            failure_class = str(progress.failure_class)
            if failure_class == "provider_credit_required":
                st.warning(
                    "The selected model provider needs additional credit. "
                    "Completed research is preserved; add credit or choose "
                    "another eligible primary provider, then resume explicitly."
                )
            elif failure_class == "provider_authentication_required":
                st.warning(
                    "The selected model provider needs authentication. "
                    "Completed research is preserved; reconnect or choose "
                    "another eligible provider, then resume explicitly."
                )
            else:
                st.warning(
                    f"Current model-stage failure: {failure_class}. "
                    f"Retries used: {getattr(progress, 'retry_count', 0)}."
                )
        elif getattr(progress, "retry_count", 0):
            st.caption(f"Automatic retries used: {progress.retry_count}.")
    controls = st.columns(2)
    if controls[0].button(
        "Refresh status",
        key=f"refresh-active-job-{job_id}",
        use_container_width=True,
    ):
        st.rerun()
    if controls[1].button(
        "Cancel active work",
        key=f"cancel-active-job-{job_id}",
        use_container_width=True,
    ):
        _hybrid_job_manager().cancel(job_id)
        st.rerun()


def _investigation_journey_view_model(
    result: ResearchResult,
    *,
    active_job_id: str,
):
    state = _hybrid_job_manager().get(active_job_id) if active_job_id else None
    if state is not None and state.status in {
        "queued", "running", "saving_result", "cancelling",
    }:
        progress = getattr(state, "progress", None)
        setup = dict(getattr(state, "saved_setup_summary", {}) or {})
        try:
            events = _research_run_service().timeline(state.job_id)
        except (KeyError, OSError, ValueError):
            events = []
        journey_model = build_investigation_journey_view_model(
            build_investigation_journey(
                result,
                operation_kind=str(getattr(state, "operation_kind", "full_research") or "full_research"),
                operation_label=str(
                    getattr(state, "operation_label", "") or f"Research for {state.ticker}"
                ),
                objective=str(setup.get("operation_objective") or ""),
                persistence_status="saving" if state.status == "saving_result" else "pending",
                active_stage=str(
                    getattr(progress, "journey_stage", "")
                    or getattr(progress, "current_stage", "")
                    or state.status
                ),
                active=True,
                elapsed_seconds=float(getattr(progress, "elapsed_seconds", 0) or 0),
                events=events,
                research_intent_id=str(setup.get("research_intent_id") or ""),
                data_cutoff=str(setup.get("data_cutoff") or ""),
            )
        )
        repair_index = int(getattr(progress, "repair_stage_index", 0) or 0)
        repair_count = int(getattr(progress, "repair_stage_count", 0) or 0)
        if repair_index and repair_count:
            journey_model = replace(
                journey_model,
                current_stage=str(
                    getattr(progress, "current_stage", "")
                    or journey_model.current_stage
                ),
                progress_percent=min(
                    99,
                    round(100 * (repair_index - 0.5) / repair_count),
                ),
                progress_label=(
                    f"Repair stage {repair_index} of {repair_count} · "
                    f"{getattr(progress, 'current_stage', 'Working')}"
                ),
                execution_state=str(
                    getattr(progress, "execution_state", "") or ""
                ),
                execution_last_activity_at=str(
                    getattr(
                        progress,
                        "execution_last_activity_at",
                        "",
                    ) or ""
                ),
                execution_hard_deadline_at=str(
                    getattr(
                        progress,
                        "execution_hard_deadline_at",
                        "",
                    ) or ""
                ),
            )
        return journey_model
    setup = dict(st.session_state.get("_displayed_research_setup") or {})
    displayed_context = st.session_state.get("_displayed_research_context")
    displayed_reference = st.session_state.get(
        "_displayed_investigation_reference"
    ) or displayed_context
    operation_kind = str(
        getattr(displayed_reference, "operation_kind", "")
        or setup.get("operation_kind")
        or ""
    )
    if operation_kind not in {
        "targeted_investigation", "action_investigation", "event_investigation",
        "analog_investigation", "repair_recheck", "repair_and_recheck",
    }:
        return None
    persistence_status = (
        "verified saved result"
        if st.session_state.get("_displayed_saved_artifact_id")
        else "session only · not saved"
    )
    if getattr(result, "demo_case", None):
        persistence_status = "frozen demo"
    return build_investigation_journey_view_model(
        build_investigation_journey(
            result,
            operation_kind=operation_kind,
            operation_label=str(
                getattr(displayed_context, "operation_label", "")
                or getattr(displayed_reference, "operation_label", "")
                or setup.get("operation_label")
                or st.session_state.get("_displayed_operation_label")
                or "Focused investigation"
            ),
            objective=str(
                setup.get("operation_objective")
                or getattr(displayed_reference, "operation_label", "")
                or ""
            ),
            outcome=str(
                getattr(displayed_context, "outcome", "")
                or st.session_state.get("_displayed_outcome")
                or "completed"
            ),
            persistence_status=persistence_status,
            research_intent_id=str(
                getattr(displayed_reference, "intent_id", "")
                or getattr(displayed_context, "research_intent_id", "")
                or setup.get("research_intent_id")
                or ""
            ),
            data_cutoff=str(
                getattr(displayed_reference, "data_cutoff", "")
                or getattr(displayed_context, "data_cutoff", "")
                or ""
            ),
            evidence_hash=str(
                getattr(displayed_reference, "evidence_hash", "")
                or getattr(displayed_context, "evidence_hash", "")
                or ""
            ),
        )
    )


def render_header(
    result: ResearchResult,
    *,
    active_job_id: str = "",
) -> None:
    identity = result.identity
    profile = getattr(result, "research_profile", None)
    result_type = "Frozen demo" if getattr(result, "demo_case", None) else "Live research"
    freshness = (
        getattr(result, "research_freshness", None)
        or build_research_freshness_from_result(result)
    )
    run_timestamp = (
        freshness.run_completed_at
        if getattr(result, "demo_case", None)
        else getattr(result.run_manifest, "generated_at", "")
    )
    st.subheader(f"{identity.name.title()} ({identity.ticker})")
    render_active_hybrid_job_banner(active_job_id, result=result)
    st.caption(
        " · ".join(
            value for value in (
                f"Displayed result: {result_type}",
                getattr(profile, "label", "") if profile else "",
                f"Research completed {_display_timestamp(run_timestamp)}",
            ) if value
        )
    )
    st.caption(_localized_freshness_summary(freshness))
    displayed_context = st.session_state.get("_displayed_research_context")
    if bool(getattr(displayed_context, "is_derived_revision", False)):
        st.info(
            "Evidence refreshed from the saved result's original cutoff. "
            "Independent QA is pending for the affected answer."
        )
    displayed_setup = st.session_state.get("_displayed_research_setup")
    next_setup = st.session_state.get("_current_research_setup")
    displayed_label = _format_research_setup(
        displayed_setup,
        qa_label=_displayed_qa_label(result),
    )
    next_label = _format_research_setup(next_setup)
    if displayed_label != next_label:
        st.caption(
            f"Viewing now: {displayed_label}\n\n"
            f"Next research setup: {next_label}"
        )
    else:
        st.caption(f"Viewing now and next research setup use: {next_label}")
    action_cols = st.columns([1.4, 2.6])
    if action_cols[0].button(
        "Select full research refresh",
        key=f"refresh-live-{identity.ticker}",
        use_container_width=True,
        disabled=_has_active_research_task(),
        help=(
            "Stages a broad refresh with the exact setup shown above. Review and "
            "confirm it in the sidebar; selecting it makes no provider calls."
        ),
    ):
        full_intent = st.session_state.get("_current_full_research_intent")
        if isinstance(full_intent, ResearchLaunchIntent):
            _stage_research_intent(full_intent)
            st.session_state._investigation_queue_notice = (
                "Full research refresh selected. Review and confirm it in Next "
                "research task. No providers were called."
            )
        st.rerun()
    with action_cols[1].expander("All source cutoffs", expanded=False):
        st.caption(
            "Canonical source dates and retrieval timestamps remain available here; "
            "the Decision view uses only the concise freshness summary above."
        )
        st.dataframe(
            [
                {
                    "Component": item.component,
                    "Status": item.status,
                    "Source as of": (
                        _display_timestamp(item.source_as_of)
                        if item.source_as_of else "Not available"
                    ),
                    "Retrieved / generated": (
                        _display_timestamp(item.observed_at)
                        if item.observed_at else "Not recorded"
                    ),
                    "Source": item.source or "Not recorded",
                    "Meaning": item.message,
                }
                for item in freshness.components
            ],
            hide_index=True,
            use_container_width=True,
        )


def render_story_first(result: ResearchResult) -> None:
    demo_case = getattr(result, "demo_case", None)
    if demo_case:
        metadata = _demo_case_runtime_metadata(demo_case)
        st.info(
            f"Demo gallery: **{demo_case.title}**. {demo_case.lesson} "
            f"Runtime: {demo_case.expected_runtime}; network required: {'yes' if demo_case.network_required else 'no'}; "
            f"version: {metadata['content_version'] or 'Unversioned'}; "
            f"refreshed: {metadata['refreshed_at'] or 'Unknown'}."
        )
    one_pager = getattr(result, "ic_one_pager", None)
    decision_brief = getattr(result, "ic_decision_brief", None)
    brief = result.thesis_brief
    st.markdown("### IC Story")
    _wrapped_metric_grid([
        ("Verdict", decision_brief.verdict if decision_brief else one_pager.verdict if one_pager else brief.verdict),
        ("Decision", decision_brief.decision if decision_brief else one_pager.decision if one_pager and one_pager.decision else "Research next"),
        ("Direction", decision_brief.direction if decision_brief else one_pager.direction if one_pager else brief.direction),
        ("Stage", decision_brief.stage if decision_brief else one_pager.stage if one_pager else brief.stage),
        ("Confidence", decision_brief.confidence if decision_brief else "Unknown"),
    ])
    if decision_brief:
        st.markdown(f"**Thesis:** {decision_brief.thesis}")
        story_cols = st.columns(2)
        with story_cols[0].container(border=True):
            st.markdown("**Why now**")
            st.write(decision_brief.why_now or "No time-sensitive catalyst has been validated.")
            why_now_ids = getattr(
                decision_brief, "why_now_evidence_ids", []
            )
            if why_now_ids:
                st.caption(
                    "Timing evidence: "
                    + ", ".join(why_now_ids[:3])
                )
            st.markdown("**Why research next**")
            st.write(
                getattr(decision_brief, "why_research_next", "")
                or "No additional automatic research action is queued."
            )
            st.markdown("**Financial bridge**")
            st.write(decision_brief.financial_bridge or "The financial bridge remains incomplete.")
            st.caption(f"Evidence: {decision_brief.evidence_status}; freshness: {decision_brief.freshness}.")
        with story_cols[1].container(border=True):
            st.markdown("**Valuation and principal risk**")
            st.write(decision_brief.valuation or "Valuation is not yet decision-ready.")
            st.write(decision_brief.principal_risk or "The counter-thesis remains under investigation.")
            st.caption(f"Horizon: {decision_brief.horizon}.")
        st.success(f"Next decision: {decision_brief.next_decision}")
    elif one_pager:
        st.write(one_pager.thesis)
        if one_pager.next_best_action:
            st.success(f"Next action: {one_pager.next_best_action}")
    profile = getattr(result, "research_profile", None)
    history = getattr(result, "historical_research", None)
    if profile and history:
        _wrapped_metric_grid([
            ("Research Profile", profile.label),
            ("Quarter History", f"{history.analyzed_quarters}/{history.requested_quarters}"),
            ("Annual History", f"{history.analyzed_annual_reports}/{history.requested_annual_reports}"),
            ("Call History", f"{history.analyzed_calls}/{history.requested_calls}"),
        ])
        if history.adaptive_deepening_reasons:
            st.caption("Adaptive deepening: " + ", ".join(history.adaptive_deepening_reasons) + ".")
        st.caption(
            f"Discovered before parsing: {history.discovered_quarters} quarterly filings, "
            f"{history.discovered_annual_reports} annual reports, and {history.discovered_calls} calls."
        )
    render_pipeline_progress(result)
    render_independent_research_qa(result, compact=True)
    render_story_cards(result)
    render_bull_bear_judge(result)
    with st.expander("Formula transparency", expanded=False):
        render_formula_traces(result)
    render_contributor_surface(result)


def _wrapped_metric_grid(items: list[tuple[str, object]]) -> None:
    cards = []
    for label, value in items:
        safe_label = html.escape(str(label))
        safe_value = html.escape(str("Unknown" if value is None or value == "" else value))
        cards.append(
            '<div style="min-height:92px;border:1px solid rgba(128,128,128,0.32);border-radius:8px;'
            'padding:0.85rem;overflow-wrap:anywhere;word-break:normal;">'
            f'<div style="font-size:0.78rem;color:#9aa0aa;margin-bottom:0.4rem;">{safe_label}</div>'
            f'<div style="font-size:1.12rem;line-height:1.3;font-weight:650;letter-spacing:0;">{safe_value}</div>'
            '</div>'
        )
    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:0.75rem;'
        'width:100%;margin-bottom:0.75rem;">' + "".join(cards) + '</div>',
        unsafe_allow_html=True,
    )


def render_pipeline_progress(result: ResearchResult) -> None:
    progress = getattr(result, "run_progress", None)
    if not progress or not progress.stages:
        return
    st.markdown("#### Research Pipeline")
    st.caption(progress.summary)
    status_icon = {
        "Passed": "[OK]",
        "Partial": "[..]",
        "Blocked": "[!]",
        "Skipped": "[-]",
        "Unavailable": "[?]",
    }
    cols = st.columns(len(progress.stages))
    for col, stage in zip(cols, progress.stages):
        col.markdown(f"**{status_icon.get(stage.status, '[?]')} {stage.label}**")
        col.caption(stage.status)
    with st.expander("Pipeline details", expanded=False):
        st.dataframe(
            [
                {
                    "Stage": stage.label,
                    "Status": stage.status,
                    "Summary": stage.summary,
                    "Evidence": "; ".join(stage.evidence),
                    "Blockers": "; ".join(stage.blockers),
                    "Next action": stage.next_action,
                }
                for stage in progress.stages
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_independent_research_qa(
    result: ResearchResult,
    *,
    compact: bool,
) -> None:
    run = getattr(result, "independent_research_qa", None)
    if run is None:
        return
    report = getattr(run, "qa_report", None)
    score = getattr(report, "score", None) if report else None
    st.markdown("#### Independent Research QA")
    st.caption(
        f"One shared evidence graph | status {run.status} | "
        f"evidence {str(getattr(run, 'evidence_hash', '') or 'Unavailable')[:12]}"
    )
    _wrapped_metric_grid([
        (
            "QA score",
            f"{score.total_score}/100" if score else "Unscored",
        ),
        ("Audit", report.status if report else "Not run"),
        (
            "Evidence gaps",
            (
                f"{report.evidence_gaps_closed} closed / "
                f"{report.evidence_gaps_unresolved} unresolved"
            ) if report else "Not evaluated",
        ),
        (
            "Alternative hypotheses",
            report.alternative_hypotheses_explored if report else 0,
        ),
    ])
    if report and report.summary:
        st.info(report.summary)
    if not compact and score and score.dimensions:
        st.dataframe(
            [
                {
                    "Dimension": item.name,
                    "Score": f"{item.score}/100",
                    "Status": item.status,
                    "Basis": item.explanation,
                    "Deductions": "; ".join(item.deductions),
                }
                for item in score.dimensions
            ],
            hide_index=True,
            use_container_width=True,
        )
    gaps = list(getattr(run, "evidence_gaps", []) or [])
    if gaps and not compact:
        with st.expander("Evidence gaps and resolution status", expanded=False):
            st.dataframe(
                [
                    {
                        "Gap": item.summary,
                        "Severity": item.severity,
                        "Status": item.status,
                        "Evidence requests": ", ".join(item.evidence_request_ids),
                        "Resolution evidence": ", ".join(item.resolution_evidence_ids),
                    }
                    for item in gaps
                ],
                hide_index=True,
                use_container_width=True,
            )
    revisions = list(getattr(run, "conclusion_revisions", []) or [])
    if revisions and not compact:
        with st.expander("Conclusions changed after audit", expanded=False):
            st.dataframe(
                [
                    {
                        "Area": item.conclusion_area,
                        "Materiality": item.materiality,
                        "Before": item.before,
                        "After": item.after,
                        "Reason": item.reason,
                    }
                    for item in revisions
                ],
                hide_index=True,
                use_container_width=True,
            )
    for gap in list(getattr(run, "data_gaps", []) or [])[: (2 if compact else 6)]:
        st.warning(gap)


def render_hybrid_research_timeline(
    result: ResearchResult,
    *,
    compact: bool,
) -> None:
    run = getattr(result, "hybrid_research", None)
    if run is None:
        return
    st.markdown("#### Research Workflow Trace")
    st.caption(
        f"{run.execution_backend.label} | status {run.status} | "
        f"validation {run.deterministic_validation_status} | "
        f"cache {run.cache_status}"
    )
    if not run.rounds:
        for gap in run.data_gaps[:3]:
            st.warning(gap)
        return
    primary_label = (
        "DeepSeek-led primary"
        if getattr(run, "variants", None) else "Primary workflow"
    )
    st.dataframe(
        [
            {
                "Variant": primary_label,
                "Round": item.round_number,
                "Role": item.role.replace("_", " ").title(),
                "Provider": f"{item.provider} / {item.model}",
                "Status": item.status,
                "Latency": (
                    f"{item.latency_ms / 1000:.1f}s"
                    if item.latency_ms is not None else "Unknown"
                ),
                "Evidence": (
                    len(item.artifact.evidence_ids) if item.artifact else 0
                ),
            }
            for item in run.rounds
        ],
        hide_index=True,
        use_container_width=True,
    )
    if getattr(run, "variants", None):
        challenger = next(
            (
                item for item in run.variants
                if item.variant_id == "sol_led_challenger"
            ),
            None,
        )
        primary = next(
            (
                item for item in run.variants
                if item.variant_id == "deepseek_led"
            ),
            None,
        )
        _wrapped_metric_grid([
            (
                "Primary audit",
                primary.audit_decision.status
                if primary and primary.audit_decision else "not_run",
            ),
            (
                "Primary quality",
                primary.quality_scorecard.total_score
                if primary and primary.quality_scorecard else "Unscored",
            ),
            (
                "Challenger",
                challenger.status if challenger else "not_run",
            ),
            (
                "Sol failure streak",
                run.audit_failure_streak,
            ),
        ])
        if not compact and challenger:
            with st.expander("Sol-led challenger rounds", expanded=False):
                st.dataframe(
                    [
                        {
                            "Round": item.round_number,
                            "Role": item.role.replace("_", " ").title(),
                            "Provider": f"{item.provider} / {item.model}",
                            "Status": item.status,
                            "Latency": (
                                f"{item.latency_ms / 1000:.1f}s"
                                if item.latency_ms is not None else "Unknown"
                            ),
                        }
                        for item in challenger.rounds
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
    final = run.final_artifact
    if final:
        with st.container(border=True):
            st.markdown("**Audited research result**")
            st.write(final.summary)
            if final.validation_gaps:
                st.caption(
                    "Validation gaps: "
                    + "; ".join(final.validation_gaps[:3])
                )
    if not compact:
        for item in run.rounds:
            with st.expander(
                f"Round {item.round_number}: "
                f"{item.role.replace('_', ' ').title()}",
                expanded=False,
            ):
                st.write(
                    item.artifact.summary
                    if item.artifact
                    else item.message or "No artifact returned."
                )
                if item.artifact and item.artifact.validation_gaps:
                    for gap in item.artifact.validation_gaps:
                        st.warning(gap)
        if run.audit_findings:
            st.markdown("**Open audit findings**")
            for finding in run.audit_findings[:10]:
                st.write(
                    f"{finding.severity} | {finding.category}: "
                    f"{finding.summary}"
                )


def render_dual_model_summary(result: ResearchResult) -> None:
    run = getattr(result, "dual_model_research", None)
    if run is None:
        return
    active_job_id = str(st.session_state.get("hybrid_job_id") or "")
    st.markdown(
        "#### Previous Displayed Result: Model Research Comparison"
        if active_job_id else
        "#### Model Research Comparison"
    )
    benchmark = run.benchmark
    adjudication = run.adjudication
    completed = getattr(
        run,
        "completed_stage_count",
        sum(item.status == "completed" for item in run.stages),
    )
    attempted = getattr(
        run,
        "attempted_stage_count",
        sum(item.status != "unavailable" for item in run.stages),
    )
    accepted = (
        len(run.accepted_secondary_deltas)
        if getattr(run, "adjudication_status", "not_run") != "not_run"
        else "Not evaluated"
    )
    unresolved = (
        len(adjudication.unresolved_disagreements)
        if adjudication else len([
            item for item in run.gap_diagnoses if item.status != "resolved"
        ])
    )
    input_quality = run.input_sufficiency
    evidence_label = (
        f"{input_quality.total_score}/100 | "
        f"{'Benchmark-ready' if input_quality.benchmark_ready else 'Remediation needed'}"
        if input_quality else "Not scored"
    )
    if run.status == "frozen_fixture":
        _wrapped_metric_grid([
            ("Research status", "Frozen demo"),
            ("Evidence pack", evidence_label),
            ("Model execution at load", "None"),
            ("Provenance", run.provenance_class.replace("_", " ").title()),
        ])
        st.info(run.provenance_message or run.final_label)
        if run.provenance_class == "sanitized_fixture":
            st.caption(
                "Real live benchmarking is still needed to establish whether "
                "DeepSeek research quality approaches GPT-5.6 Sol across "
                "actual companies and sectors."
            )
        return
    if run.status in {
        "not_started",
        "not_started_missing_credentials",
        "unavailable",
    } and not run.stages:
        _wrapped_metric_grid([
            ("Research status", "Paired research did not start"),
            ("Evidence pack", evidence_label),
            ("DeepSeek execution", "Not started"),
            ("GPT-5.6 Sol execution", "Not started"),
            ("Adjudication", "Not run"),
        ])
        st.warning(
            getattr(run, "execution_failure_reason", "")
            or run.provenance_message
            or "Required model providers were unavailable."
        )
        return
    _wrapped_metric_grid([
        ("Research status", run.final_label),
        ("Evidence pack", evidence_label),
        (
            "DeepSeek execution",
            f"{getattr(run, 'deepseek_execution_status', 'not_started').replace('_', ' ').title()} | "
            f"{getattr(run, 'deepseek_completed_stage_count', 0)}/5 stages",
        ),
        (
            "GPT-5.6 Sol execution",
            f"{getattr(run, 'sol_execution_status', 'not_started').replace('_', ' ').title()} | "
            f"{getattr(run, 'sol_completed_stage_count', 0)}/5 stages",
        ),
        (
            "Adjudication",
            getattr(run, "adjudication_status", "not_run").replace(
                "_", " "
            ).title(),
        ),
        ("Comparable stages", f"{completed}/{attempted} attempted"),
        ("Accepted Sol amendments", accepted),
        ("Result provenance", run.provenance_class.replace("_", " ").title()),
    ])
    if unresolved:
        st.caption(f"Unresolved model/evidence issues: {unresolved}.")
    execution_failure_reason = getattr(
        run, "execution_failure_reason", ""
    )
    if execution_failure_reason:
        st.warning(execution_failure_reason)
    if benchmark:
        winners = [
            f"{stage.replace('_', ' ')}: {winner}"
            for stage, winner in benchmark.stage_winners.items()
        ]
        st.caption(
            f"DeepSeek {benchmark.deepseek_score}/100; GPT-5.6 Sol "
            f"{benchmark.sol_score}/100. "
            + (" | ".join(winners[:3]) if winners else "")
        )
        if benchmark.status == "input_not_qualified":
            st.warning(
                "The model comparison is diagnostic only. Input sufficiency "
                "has not reached 80/100 or a critical identity, period, or "
                "citation gap remains."
            )
    if adjudication:
        st.write(adjudication.rationale)
        if adjudication.unresolved_disagreements:
            st.warning(
                "Unresolved: "
                + "; ".join(adjudication.unresolved_disagreements[:3])
            )
    with st.expander("What the secondary model added", expanded=False):
        if getattr(run, "adjudication_status", "not_run") == "not_run":
            st.caption(
                "Not evaluated because the evidence-bound Sol adjudication "
                "did not complete."
            )
        elif run.accepted_secondary_deltas:
            for item in run.accepted_secondary_deltas[:8]:
                st.write(f"- {item.summary}")
                st.caption(
                    f"Stage: {item.stage_id.replace('_', ' ')}; "
                    f"validated evidence IDs: {len(item.evidence_ids)}."
                )
        else:
            st.caption(
                "No distinct Sol addition passed deterministic evidence-ID "
                "validation in this run."
            )


def render_story_cards(result: ResearchResult) -> None:
    cards = getattr(result, "story_cards", [])
    if not cards:
        return
    st.markdown("#### Story Cards")
    primary_cards = cards[:4]
    for idx in range(0, len(primary_cards), 2):
        cols = st.columns(2)
        for card, col in zip(primary_cards[idx:idx + 2], cols):
            with col.container(border=True):
                st.markdown(f"**{card.title}**")
                st.caption(card.status)
                st.write(card.body or card.summary)
                if card.next_action:
                    st.caption(f"Next: {card.next_action}")
                if card.evidence:
                    with st.expander("Show evidence", expanded=False):
                        render_evidence_drawers(card.evidence)
    if len(cards) > 4:
        with st.expander(f"More IC context ({len(cards) - 4})", expanded=False):
            st.dataframe(
                [
                    {
                        "Section": card.title,
                        "Status": card.status,
                        "Finding": card.body or card.summary,
                        "Next action": card.next_action,
                        "Evidence items": len(card.evidence),
                    }
                    for card in cards[4:]
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_evidence_drawers(drawers) -> None:
    for drawer in drawers:
        st.markdown(f"**{drawer.label}**")
        st.write(drawer.claim)
        meta = [
            f"Source: {drawer.source or 'Unknown'}",
            f"Tier: {drawer.source_tier if drawer.source_tier is not None else 'Unknown'}",
            f"Section: {drawer.section or 'Unknown'}",
            f"Period: {drawer.period or 'Unknown'}",
            f"Metric: {drawer.metric or 'n/a'}",
            f"Value: {drawer.value or 'n/a'}",
            f"Formula: {drawer.formula or 'n/a'}",
            f"Parser: {drawer.parser_status or 'Unknown'}",
            f"Confidence: {drawer.confidence or 'Unknown'}",
        ]
        st.caption(" | ".join(meta))
        if drawer.url:
            st.caption(drawer.url)
        if drawer.excerpt:
            st.code(drawer.excerpt[:700])


def render_bull_bear_judge(result: ResearchResult) -> None:
    panel = getattr(result, "bull_bear_judge", None)
    if not panel:
        return
    st.markdown("#### Bull / Bear / Judge")
    cols = st.columns(3)
    with cols[0].container(border=True):
        st.markdown("**Bull case**")
        st.write(panel.bull_case)
    with cols[1].container(border=True):
        st.markdown("**Bear case**")
        st.write(panel.bear_case)
    with cols[2].container(border=True):
        st.markdown("**Judge accepts**")
        for item in panel.judge_accepts[:5]:
            st.write(f"- {item}")
        if panel.still_unproven:
            st.markdown("**Still unproven**")
            for item in panel.still_unproven[:5]:
                st.write(f"- {item}")
    if panel.resolution_plan:
        st.markdown("**How the app will try to resolve open items**")
        st.dataframe(
            [
                {
                    "Type": item.issue_type,
                    "Status": item.status,
                    "What it means": item.issue,
                    "Triggering evidence": item.evidence,
                    "App action": item.app_action,
                    "User action": item.user_action,
                    "Blocks": item.blocking_scope,
                    "Automatic": "Yes" if item.auto_resolvable else "No",
                }
                for item in panel.resolution_plan
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_formula_traces(result: ResearchResult) -> None:
    traces = getattr(result, "formula_traces", [])
    if not traces:
        st.info("No formula traces are attached to this run.")
        return
    st.dataframe(
        [
            {
                "Label": trace.label,
                "Value": trace.value,
                "Source field": trace.source_field,
                "Formula": trace.formula,
                "Period": trace.period or "Unknown",
                "Currency": trace.currency or "Unknown",
                "Confidence": trace.confidence,
                "Source": trace.source,
            }
            for trace in traces[:30]
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_contributor_surface(result: ResearchResult) -> None:
    with st.expander("Improve this project", expanded=False):
        st.link_button(
            "Star or contribute on GitHub",
            GITHUB_REPOSITORY_URL,
            use_container_width=True,
        )
        pack = build_contribution_pack(result)
        st.write(
            "The app can prefill safe configuration and test specifications from this run. "
            "It saves a reviewable draft and does not silently alter the authoritative source registry."
        )
        st.dataframe(
            [
                {
                    "Contribution": "Sector playbook",
                    "Status": pack["sector_playbook"]["status"],
                    "App support": "Prefill KPIs, indicators, valuation methods, catalysts, and fixture requirement",
                },
                {
                    "Contribution": "ADR profile",
                    "Status": pack["adr_profile"]["status"],
                    "App support": "Prefill ratio, currency, forms, exchange, and issuer sources",
                },
                {
                    "Contribution": "Metric aliases",
                    "Status": f"{len(pack['metric_alias_drafts'])} unresolved metric(s)",
                    "App support": "Draft canonical metric rows; exact source tags still require validation",
                },
                {
                    "Contribution": "Source adapters",
                    "Status": f"{len(pack['source_adapter_specs'])} source gap(s)",
                    "App support": "Generate adapter contracts and fixture requirements; executable code requires review",
                },
                {
                    "Contribution": "Demo case",
                    "Status": "Draft ready",
                    "App support": "Prefill lesson and no-network regression requirement",
                },
            ],
            use_container_width=True,
            hide_index=True,
        )
        serialized = json.dumps(pack, indent=2, ensure_ascii=False)
        left, right = st.columns(2)
        if left.button(
            "Save local contribution draft",
            key=f"save-contribution-{result.identity.ticker}",
            use_container_width=True,
        ):
            path = save_contribution_pack(pack)
            st.success(f"Saved reviewable contribution draft to {path}.")
        right.download_button(
            "Download contribution pack",
            data=serialized,
            file_name=f"{result.identity.ticker}_contribution_pack.json",
            mime="application/json",
            key=f"download-contribution-{result.identity.ticker}",
            use_container_width=True,
        )


def render_ic_copilot(result: ResearchResult) -> None:
    render_story_first(result)
    render_decision_delta(result)
    render_market_implied_ic_card(result)
    render_catalyst_clock(result)
    render_prioritized_research_queue(result)
    render_llm_guardrail_summary(result)
    render_historical_analog_intelligence(result, compact=True)
    render_news_intelligence_report(result, compact=True)
    render_credit_lens_summary(result)


def render_decision_delta(result: ResearchResult) -> None:
    delta = getattr(result, "decision_delta", None)
    if not delta:
        return
    st.markdown("#### Decision Delta")
    _wrapped_metric_grid([
        ("Status", delta.status),
        (
            "Compared with",
            (
                _human_decision_comparison(delta)
                if delta.compared_with_run_id else
                "First stored baseline"
            ),
        ),
    ])
    st.write(delta.summary)
    changes = (
        [("Strengthens", item) for item in delta.strengthening_changes]
        + [("Weakens", item) for item in delta.weakening_changes]
        + [("Neutral", item) for item in delta.neutral_changes]
    )
    if changes:
        with st.expander("What changed since the prior run", expanded=False):
            for label, item in changes[:8]:
                st.write(f"- **{label}:** {item}")
            if delta.compared_with_run_id:
                st.caption(
                    f"Audit run ID: {delta.compared_with_run_id}. "
                    f"Canonical comparison time: "
                    f"{getattr(delta, 'compared_with_completed_at', None) or 'Unknown'}."
                )


def render_catalyst_clock(result: ResearchResult) -> None:
    clock = getattr(result, "catalyst_clock", None)
    if not clock:
        return
    st.markdown("#### Catalyst Clock")
    st.write(clock.summary)
    if clock.items:
        for index, item in enumerate(clock.items[:3]):
            with st.container(border=True):
                st.markdown(
                    f"**{'Next' if index == 0 else 'Then'}: {item.label}**"
                )
                st.write(
                    item.decision_relevance
                    or "This event may change the current IC decision."
                )
                st.caption(
                    f"{item.due_date or 'Date not validated'} | "
                    f"{item.status} | "
                    f"{item.source_label or item.source or 'Registered source'}"
                )
    elif clock.data_gaps:
        st.caption(clock.data_gaps[0])


def render_prioritized_research_queue(result: ResearchResult) -> None:
    queue = canonicalize_investigation_actions(
        result.identity.ticker,
        getattr(result, "prioritized_research_queue", []),
    ).actions
    if not queue:
        return
    st.markdown("#### Research Queue")
    st.caption("The three open tasks most likely to change the current IC decision.")
    for item in queue[:3]:
        with st.container(border=True):
            st.markdown(f"**{item.rank}. {item.title}**")
            st.write(item.why_it_matters)
            st.caption(
                f"{item.decision_impact} | Source: {item.source_type} | "
                f"App can attempt automatically: {'yes' if item.auto_resolvable else 'no'}"
            )


def render_llm_guardrail_summary(result: ResearchResult) -> None:
    manifest = result.llm_run_manifest
    st.markdown("#### LLM Guardrail Checklist")
    _wrapped_metric_grid([
        ("LLM Status", manifest.status),
        ("Execution", manifest.llm_execution_status or "n/a"),
        ("Guardrails", manifest.llm_guardrail_status or "n/a"),
        ("Guardrail Score", f"{manifest.guardrail_score}/100"),
    ])
    st.caption(manifest.message)
    with st.expander("LLM guardrail detail", expanded=False):
        if manifest.failure_class or manifest.provider_health or manifest.timeout_seconds:
            st.caption(
                "Provider health: "
                f"{manifest.provider_health or 'n/a'}; failure class: {manifest.failure_class or 'none'}; "
                f"retryable: {'yes' if manifest.retryable else 'no'}; timeout: {manifest.timeout_seconds or 'n/a'}s."
            )
        if manifest.guardrail_checks:
            st.dataframe(
                [
                    {
                        "Area": item.area,
                        "Status": item.status,
                        "Score": item.score,
                        "Summary": item.summary,
                        "Gaps": "; ".join(item.gaps),
                        "Enforcement": item.enforcement,
                    }
                    for item in manifest.guardrail_checks
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_historical_analog_intelligence(result: ResearchResult, *, compact: bool) -> None:
    report = getattr(result, "historical_analogs", None)
    st.markdown("#### Historical Perspective" if compact else "#### Historical Analog Lab")
    if not report:
        st.info("Historical analog research has not been built for this run.")
        return
    researched = [
        item for item in getattr(report, "dossiers", [])
        if item.status in {
            "Audited Research Analog", "Frozen Demo Dossier", "Researched Episode"
        }
    ]
    investigation = getattr(report, "investigation_status", None)
    _wrapped_metric_grid([
        ("Status", report.status),
        ("Coverage", report.coverage_status),
        ("Researched Episodes", len(researched)),
        ("LLM Research", investigation.code if investigation else report.llm_status),
    ])
    if investigation and investigation.reason:
        st.caption(investigation.reason)
    brief = getattr(report, "brief", None)
    if brief:
        with st.container(border=True):
            st.markdown(f"**{brief.episode}**")
            st.write(brief.lesson)
            st.caption(
                f"Material difference: {brief.material_difference} "
                f"Monitor: {brief.linked_monitor} "
                f"Evidence: {brief.evidence_status}."
            )
    elif report.top_lesson:
        st.write(report.top_lesson)
    if researched:
        visible_dossiers = researched[:1] if compact else researched[:3]
        for dossier in visible_dossiers:
            _render_historical_episode_dossier(dossier, compact=compact)
    elif not report.assessments:
        st.info(
            report.coverage_status
            or "No analog passed discovery and source-quality checks. This is a coverage result, not evidence that no analog exists."
        )
        for gap in report.data_gaps[:3]:
            st.caption(gap)
        return

    dossier_ids = {item.analog_id for item in researched}
    candidates = sorted(
        [
            item for item in report.assessments
            if item.analog_id not in dossier_ids
            and int(getattr(item, "applicability_score", 0) or 0) >= 60
            and str(getattr(item, "scope_status", "") or "") in {"", "in_scope"}
            and str(getattr(item, "eligibility_code", "") or "") not in {
                "issuer_scope_violation", "archetype_scope_violation",
                "mechanism_scope_violation", "core_mechanism_unresolved",
                "security_profile_unresolved", "scope_not_evaluated",
            }
        ],
        key=lambda item: (
            bool(
                int(getattr(item, "source_relevance_score", 0) or 0) >= 60
                and list(getattr(item, "sources", []) or [])
            ),
            int(getattr(item, "applicability_score", 0) or 0),
            int(getattr(item, "source_relevance_score", 0) or 0),
        ),
        reverse=True,
    )
    if not researched and not candidates:
        st.info(
                "No credible historical playbook is established for this mechanism yet. "
                "The app will not substitute a different issuer or mechanism merely to fill this section."
            )
        return
    visible = candidates[:1] if compact and not researched else candidates if not compact else []
    for assessment in visible:
        with st.container(border=True):
            st.markdown(f"**Candidate analog: {assessment.episode}**")
            source_backed = bool(
                assessment.source_relevance_score >= 60 and assessment.sources
            )
            st.caption(
                (
                    f"Investigated candidate · {assessment.classification} · "
                    f"confidence {assessment.confidence}"
                )
                if source_backed else
                "Candidate only · relevant mechanism, but episode evidence is not admitted"
            )
            st.write(assessment.what_it_suggests)
            reasons = list(getattr(assessment, "applicability_reasons", []) or [])
            if reasons:
                st.caption("Why it fits: " + " ".join(reasons[:2]))
            if assessment.material_differences:
                st.caption("Material difference: " + assessment.material_differences[0])
            st.caption(assessment.what_it_cannot_establish)
            with st.expander("Analog evidence and monitor tests", expanded=False):
                st.write(f"Selected because: {assessment.selection_rationale}")
                if assessment.lessons:
                    st.markdown("**Lessons**")
                    for item in assessment.lessons[:5]:
                        st.write(f"- {item}")
                if assessment.confirm_kpis or assessment.reject_kpis:
                    st.markdown("**Current tests**")
                    for item in assessment.confirm_kpis[:4]:
                        st.write(f"- Confirm: {item}")
                    for item in assessment.reject_kpis[:4]:
                        st.write(f"- Reject: {item}")
                for source in assessment.sources[:6]:
                    label = source.title or source.provider
                    if source.url:
                        st.markdown(f"- [{label}]({source.url}) ({source.source_class}; {source.source_origin_group})")
                    else:
                        st.write(f"- {label} ({source.source_class}; {source.source_origin_group})")
                if assessment.rejected_sources:
                    st.markdown("**Rejected source matches**")
                    for link in assessment.rejected_sources[:6]:
                        source_label = (
                            link.source_title
                            or link.source_provider
                            or "Retrieved source"
                        )
                        st.write(f"- **{source_label}**")
                        st.caption(
                            link.rejection_summary
                            or ", ".join(link.rejection_reasons)
                        )
                    with st.expander("Rejected-source audit IDs", expanded=False):
                        for link in assessment.rejected_sources[:12]:
                            st.code(
                                f"{link.source_id}: "
                                + ", ".join(link.rejection_reasons)
                            )
            if assessment.status not in {
                "Audited Research Analog", "Research Analog"
            }:
                st.caption(
                    "This is a mechanism-shaped research lead, not a documented historical episode."
                )
            if st.button(
                "Investigate Further",
                key=f"investigate-analog-{result.identity.ticker}-{assessment.analog_id}-{compact}",
                use_container_width=True,
                disabled=_has_active_research_task(),
            ):
                current_setup = dict(
                    st.session_state.get("_current_research_setup") or {}
                )
                _, workspace_scope = _saved_research_scope()
                _stage_research_intent(build_research_launch_intent(
                    ticker=result.identity.ticker,
                    operation_kind=ResearchOperationKind.ANALOG_INVESTIGATION,
                    operation_label=(
                        f"Investigate historical analog: {assessment.episode}"
                    ),
                    objective=(
                        "Test the analog's mechanism, material differences, "
                        "independent provenance, outcomes, and monitor value."
                    ),
                    launch_origin="research_analog_card",
                    research_profile=str(
                        current_setup.get("profile") or "adaptive_ic"
                    ),
                    preferred_result_route="Research",
                    analog_id=str(assessment.analog_id or ""),
                    qa_enabled=bool(current_setup.get("qa_resolved")),
                    workspace_scope=workspace_scope,
                    fingerprint_context={"setup": current_setup},
                    **_displayed_parent_context(result),
                ))
                st.session_state._investigation_queue_notice = (
                    "Historical analog investigation selected. Review and "
                    "confirm it in Next research task. No providers were called."
                )
                st.rerun()
    hidden = max(
        0,
        len([
            item for item in report.assessments
            if item.analog_id not in dossier_ids
        ]) - len(visible),
    )
    if compact and hidden > 0:
        st.caption(
            f"{hidden} additional candidate analog(s) are available in "
            "Historical Analogs."
        )
    if not compact and getattr(report, "work_orders", None):
        with st.expander("Historical evidence work orders", expanded=False):
            for item in report.work_orders:
                st.markdown(f"**{item.episode}**")
                st.write(item.blocker or item.status.replace("_", " ").title())
                st.caption(
                    f"Next: {item.next_action} Required sources: "
                    + ", ".join(item.required_source_types)
                )
    if getattr(report, "comparison", None):
        comparison = report.comparison
        with st.expander("DeepSeek primary vs GPT-5.6 Sol challenger", expanded=False):
            _wrapped_metric_grid([
                ("Comparison", comparison.status),
                ("Agreement", comparison.agreement),
                ("Primary quality", comparison.primary_quality_score or "Unscored"),
                ("Challenger quality", comparison.challenger_quality_score or "Unscored"),
            ])
            for item in comparison.disagreements[:5]:
                st.write(f"- Disagreement: {item}")
            for item in comparison.evidence_coverage_differences[:5]:
                st.write(f"- Evidence coverage: {item}")


def _render_historical_episode_dossier(dossier, *, compact: bool) -> None:
    with st.container(border=True):
        st.markdown(f"**{dossier.episode}**")
        score = (
            f"{dossier.audited_similarity_score}/100"
            if dossier.audited_similarity_score is not None
            else "withheld pending audit"
        )
        st.caption(
            f"{dossier.status} | {dossier.start_date} to {dossier.end_date} | "
            f"{dossier.period_precision} precision | similarity "
            f"{dossier.similarity_band} ({score}) | confidence "
            f"{dossier.confidence} | "
            f"{len({item.source_origin_group for item in dossier.sources})} source origin(s)"
        )
        if dossier.primary_model or dossier.secondary_auditor_model:
            st.caption(
                f"Primary: {dossier.primary_provider or 'Unknown'} "
                f"{dossier.primary_model or ''}; secondary audit: "
                f"{dossier.secondary_auditor_provider or 'Unknown'} "
                f"{dossier.secondary_auditor_model or ''}; "
                f"audit {dossier.audit_status}."
            )
        if dossier.lessons:
            st.write(dossier.lessons[0])
        if dossier.material_differences:
            st.caption("Material difference: " + dossier.material_differences[0])
        with st.expander("Episode, outcomes, evidence, and current tests", expanded=False):
            if dossier.triggering_decisions:
                st.markdown("**What happened**")
                for item in dossier.triggering_decisions:
                    st.write(f"- {item}")
            st.markdown("**Outcome path**")
            st.write(f"- **Operating:** {dossier.operating_outcome}")
            st.write(f"- **Financing:** {dossier.financing_outcome}")
            st.write(f"- **Valuation:** {dossier.valuation_outcome}")
            st.write(f"- **Price:** {dossier.price_outcome}")
            st.markdown("**Context and differences**")
            st.write(f"- **Macro:** {dossier.macro_context}")
            st.write(f"- **Competition:** {dossier.competitive_context}")
            for item in dossier.material_differences:
                st.write(f"- {item}")
            if dossier.hidden_risks:
                st.markdown("**Hidden risks**")
                for item in dossier.hidden_risks:
                    st.write(f"- {item}")
            if dossier.dimensions:
                st.markdown("**Mechanism comparison**")
                st.dataframe(
                    [
                        {
                            "Dimension": item.dimension,
                            "Current": item.current_value,
                            "Historical": item.historical_value,
                            "Score": item.score,
                            "Assessment": item.assessment,
                        }
                        for item in dossier.dimensions
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            st.markdown("**Current monitor tests**")
            for item in dossier.confirm_kpis:
                st.write(f"- Confirm: {item}")
            for item in dossier.reject_kpis:
                st.write(f"- Reject: {item}")
            if dossier.linked_monitor_rules:
                st.caption("Linked rules: " + "; ".join(dossier.linked_monitor_rules))
            st.markdown("**Sources**")
            for source in dossier.sources:
                label = source.title or source.provider
                if source.url:
                    st.markdown(
                        f"- [{label}]({source.url}) ({source.source_class}; {source.source_origin_group})"
                    )
                else:
                    st.write(f"- {label} ({source.source_class}; {source.source_origin_group})")
            for item in dossier.limitations:
                st.caption(item)


def render_news_intelligence_report(result: ResearchResult, *, compact: bool) -> None:
    report = getattr(result, "news_intelligence", None)
    st.markdown("#### News and Debate" if compact else "#### LLM-Directed News Intelligence")
    if not report:
        st.info("News intelligence has not been built for this run.")
        return
    _wrapped_metric_grid([
        ("Status", report.status),
        ("Coverage", report.coverage_status),
        ("Themes", len(report.clusters)),
        ("Independent Articles", len(report.articles)),
    ])
    if report.top_debate:
        st.write(report.top_debate)
    if not report.clusters:
        st.info(
            report.coverage_status
            or "No relevant news claims were retrieved in the event window. Provider coverage and entitlement remain visible below."
        )
        for gap in report.data_gaps[:4]:
            st.caption(gap)
    else:
        visible = report.clusters[:3] if compact else report.clusters
        for cluster in visible:
            with st.container(border=True):
                st.markdown(f"**{cluster.label}**")
                st.caption(
                    f"{cluster.stance} | {cluster.confidence} confidence | "
                    f"{len(cluster.source_origin_groups)} independent source origin(s) | "
                    f"primary check: {cluster.primary_source_status}"
                )
                st.write(cluster.summary)
        if compact and len(report.clusters) > 3:
            st.caption(f"{len(report.clusters) - 3} additional theme(s) are available in Evidence & Debate.")
    if not compact:
        with st.expander("Registered source coverage", expanded=False):
            st.dataframe(
                [
                    {
                        "Source": source.label,
                        "Provider": source.provider,
                        "Enabled": "Yes" if source.enabled else "No",
                        "Configured": "Yes" if source.configured else "No",
                        "Tier": source.default_tier,
                        "Licensing": source.licensing_policy,
                        "Notes": source.notes,
                    }
                    for source in report.registered_sources
                ],
                use_container_width=True,
                hide_index=True,
            )
        if report.articles:
            st.dataframe(
                [
                    {
                        "Published": article.published_at or "Unknown",
                        "Publisher": article.publisher,
                        "Type": article.article_type,
                        "Headline": article.headline,
                        "Driver": article.causal_driver,
                        "Tier": article.source_tier,
                        "Language": article.language,
                        "Retention": article.full_text_status,
                        "URL": article.url,
                    }
                    for article in report.articles
                ],
                use_container_width=True,
                hide_index=True,
            )
        if report.claim_assessments:
            st.markdown("**Claim corroboration**")
            st.dataframe(
                [
                    {
                        "Claim": item.claim,
                        "Status": item.status,
                        "Driver": item.affected_driver,
                        "Independent origins": len(item.source_origin_groups),
                        "Primary evidence": len(item.primary_evidence_ids),
                        "Explanation": item.explanation,
                    }
                    for item in report.claim_assessments
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_credit_lens_summary(result: ResearchResult) -> None:
    lens = getattr(result, "credit_lens", None)
    st.markdown("#### Credit Lens")
    if not lens:
        st.info("Credit analysis is unavailable for this run.")
        return
    _wrapped_metric_grid([
        ("Status", lens.status),
        ("Risk Level", lens.risk_level),
        ("Credit Metrics", len(lens.metrics)),
    ])
    st.write(lens.summary)
    with st.expander("Credit evidence, risks, and monitors", expanded=False):
        render_credit_lens(result)


def render_ic_audit_trail(result: ResearchResult) -> None:
    st.subheader("IC Audit Trail")
    render_retrieval_and_regulatory_audit(result)
    brief = result.thesis_brief
    critique = result.thesis_critique
    sufficiency = result.evidence_sufficiency
    manifest = result.llm_run_manifest
    _wrapped_metric_grid([
        ("IC Verdict", brief.verdict),
        ("Evidence Sufficiency", sufficiency.status),
        ("Sufficiency Score", f"{sufficiency.score}/100"),
        ("Synthesis Source", brief.source),
    ])
    st.markdown("#### LLM Guardrail Checklist")
    guard_cols = st.columns(5)
    guard_cols[0].metric("LLM Status", manifest.status)
    guard_cols[1].metric("Execution", manifest.llm_execution_status or "n/a")
    guard_cols[2].metric("Guardrails", manifest.llm_guardrail_status or "n/a")
    guard_cols[3].metric("Guardrail Score", f"{manifest.guardrail_score}/100")
    guard_cols[4].metric("Prompt Fingerprint", manifest.prompt_hash[:12] + "..." if manifest.prompt_hash else "n/a")
    st.caption(manifest.message)
    if manifest.failure_class or manifest.provider_health or manifest.timeout_seconds:
        st.caption(
            "Provider health: "
            f"{manifest.provider_health or 'n/a'}; failure class: {manifest.failure_class or 'none'}; "
            f"retryable: {'yes' if manifest.retryable else 'no'}; timeout: {manifest.timeout_seconds or 'n/a'}s."
        )
    if manifest.guardrail_checks:
        st.dataframe(
            [
                {
                    "Area": item.area,
                    "Status": item.status,
                    "Score": item.score,
                    "Summary": item.summary,
                    "Evidence": "; ".join(item.evidence),
                    "Gaps": "; ".join(item.gaps),
                    "Enforcement": item.enforcement,
                }
                for item in manifest.guardrail_checks
            ],
            use_container_width=True,
            hide_index=True,
        )
    if manifest.llm_execution_status == "timeout":
        st.info("LLM provider timed out before output was returned. This is not an evidence-guardrail rejection; deterministic synthesis was used.")
    one_pager = getattr(result, "ic_one_pager", None)
    if one_pager:
        st.markdown("#### IC One-Pager")
        _wrapped_metric_grid([
            ("One-Pager Status", one_pager.status),
            ("Direction", one_pager.direction),
            ("Stage", one_pager.stage),
            ("Source", one_pager.source),
            ("Decision", one_pager.decision or "n/a"),
            ("Rank Eligible", one_pager.rank_eligibility or "n/a"),
            ("Why Now", one_pager.why_now or "n/a"),
            ("Blocking Issue", one_pager.blocking_issue or "n/a"),
        ])
        st.caption(one_pager.decision_reason or one_pager.go_no_go_reason)
        if one_pager.next_best_action:
            st.info(f"Next best action: {one_pager.next_best_action}")
        if one_pager.go_no_go_reason:
            st.caption(f"Go / no-go: {one_pager.go_no_go_reason}")
        st.write(one_pager.thesis)
        st.caption(one_pager.variant_perception)
        summary_cols = st.columns(2)
        with summary_cols[0]:
            st.markdown("**Causal Bridge**")
            st.write(one_pager.causal_bridge)
            st.markdown("**Price / Capture**")
            st.write(one_pager.price_move)
            st.write(one_pager.market_capture)
            st.markdown("**Valuation**")
            st.write(one_pager.valuation)
        with summary_cols[1]:
            st.markdown("**Equity Lens**")
            st.write(one_pager.equity_lens)
            st.markdown("**Credit Lens**")
            st.write(one_pager.credit_lens)
            st.markdown("**Counter-Thesis**")
            st.write(one_pager.counter_thesis)
        if one_pager.work_order_actions:
            st.markdown("**Top Evidence Work Orders**")
            for item in one_pager.work_order_actions[:4]:
                st.write(f"- {item}")
        if one_pager.monitor_actions:
            st.markdown("**Monitor Next**")
            for item in one_pager.monitor_actions[:4]:
                st.write(f"- {item}")
    st.markdown("#### Why This Beats Generic Chat")
    st.write("- Starts from cited filings, prices, consensus snapshots, and source manifests, not model memory.")
    st.write("- Separates facts, gaps, and hypotheses; weak evidence becomes a work order, not a recommendation.")
    st.write("- Builds an auditable IC trail: causal bridge, peer metrics, market capture, valuation, counter-thesis, and monitors.")
    budget = result.budget_policy
    st.markdown("#### Budget Mode and Data Policy")
    cols = st.columns(3)
    cols[0].metric("Mode", budget.mode)
    cols[1].metric("Cost Target", budget.cost_target)
    cols[2].metric("Manual Data", result.manual_data_status.status)
    st.caption(budget.description)
    st.caption(
        f"Config: {budget.config_source}; max monthly budget "
        f"{budget.max_monthly_budget_usd if budget.max_monthly_budget_usd is not None else 'uncapped / user-defined'}; "
        f"paid data {'allowed' if budget.allow_paid_data else 'disabled'}; "
        f"LLM {'allowed' if budget.allow_llm else 'disabled'}."
    )
    with st.expander("Enabled Sources and Upgrade Slots", expanded=False):
        if budget.provider_policy:
            st.markdown("**Provider policy**")
            for group, sources in budget.provider_policy.items():
                st.write(f"- {group.replace('_', ' ').title()}: {', '.join(sources) or 'none'}")
        for source in budget.enabled_sources:
            st.write(f"- {source}")
        if budget.optional_upgrade_slots:
            st.markdown("**Upgrade slots**")
            for source in budget.optional_upgrade_slots:
                st.write(f"- {source}")
        for warning in budget.warnings:
            st.warning(warning)
    render_research_scout(result)
    render_evidence_work_order(result)
    render_global_coverage(result)
    render_market_capture_readiness(result)
    render_company_economics(result)
    render_credit_lens(result)
    render_coverage_expansion(result)
    render_thesis_clusters(result)
    render_research_questions(result)
    render_validated_claims_and_source_plan(result)
    render_wisburg_research_lens(result)
    render_latency_profile(result)
    render_conviction_chain(result)
    audit_report = result.conviction_audit
    st.markdown("#### Conviction Audit")
    cols = st.columns(3)
    cols[0].metric("Audit Status", audit_report.status)
    cols[1].metric("Process Score", f"{audit_report.score}/100")
    cols[2].metric("Checklist Items", len(audit_report.items))
    st.caption(audit_report.summary)
    if audit_report.items:
        st.dataframe(
            [
                {
                    "Check": item.name,
                    "Status": item.status,
                    "Score": item.score,
                    "Evidence": item.evidence,
                    "Gaps": "; ".join(item.gaps),
                    "Source": item.source_type,
                }
                for item in audit_report.items
            ],
            use_container_width=True,
            hide_index=True,
        )
    if audit_report.differentiators:
        with st.expander("Workflow Differentiators", expanded=False):
            for item in audit_report.differentiators:
                st.write(f"- {item}")
    validation = result.thesis_validation
    st.markdown("#### Thesis Validation Matrix")
    cols = st.columns(3)
    cols[0].metric("Validation Status", validation.status)
    cols[1].metric("Validation Score", f"{validation.score}/100")
    cols[2].metric("Channels", len(validation.checks))
    st.caption(validation.summary)
    if validation.checks:
        st.dataframe(
            [
                {
                    "Channel": check.channel,
                    "Status": check.status,
                    "Score": check.score,
                    "Evidence": check.evidence,
                    "Implication": check.implication,
                    "Gaps": "; ".join(check.gaps),
                    "Tier": check.source_tier or "n/a",
                    "Citations": check.citation_count,
                }
                for check in validation.checks
            ],
            use_container_width=True,
            hide_index=True,
        )
    if validation.required_next_evidence:
        with st.expander("Required Next Evidence", expanded=False):
            for item in validation.required_next_evidence:
                st.write(f"- {item}")
    if validation.next_evidence_actions:
        st.markdown("#### Evidence Action Plan")
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Channel": item.channel,
                    "Action": item.action,
                    "Source": item.source,
                    "Blocks High Conviction": "Yes" if item.blocks_high_conviction else "No",
                    "Why": item.why_it_matters,
                }
                for item in validation.next_evidence_actions
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("#### Thesis")
    st.write(brief.thesis)
    st.markdown("#### Variant Perception")
    st.write(brief.variant_perception)
    if brief.evidence_chain:
        st.markdown("#### Evidence Chain")
        for item in brief.evidence_chain:
            st.write(f"- {item}")
    st.markdown("#### Strongest Counter-Thesis")
    st.write(critique.strongest_counter_thesis)
    if critique.key_uncertainties:
        st.markdown("#### Key Uncertainties")
        for item in critique.key_uncertainties:
            st.write(f"- {item}")
    if critique.what_would_falsify:
        st.markdown("#### What Would Falsify This")
        for item in critique.what_would_falsify:
            st.write(f"- {item}")
    st.markdown("#### Model Disagreement")
    comparison = result.llm_comparison
    st.caption(
        f"Status: {comparison.status}; agreement: {comparison.agreement}; "
        f"primary: {comparison.primary_provider or 'n/a'}; secondary: {comparison.secondary_provider or 'n/a'}."
    )
    if comparison.key_differences:
        for item in comparison.key_differences:
            st.write(f"- {item}")
    if comparison.unsupported_claims:
        st.warning("Secondary reader flagged unsupported claims.")
        for item in comparison.unsupported_claims:
            st.write(f"- {item}")
    if result.llm_reviews:
        st.markdown("#### Secondary Reader")
        st.dataframe(
            [
                {
                    "Provider": review.provider,
                    "Model": review.model,
                    "Status": review.status,
                    "Summary": review.summary or review.message,
                    "Disagreements": "; ".join(review.disagreements),
                    "Language issues": "; ".join(review.language_quality_issues),
                }
                for review in result.llm_reviews
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.markdown("#### Language Audit")
    audit = result.language_audit
    st.caption(
        f"Policy: {audit.policy}; source languages: {', '.join(audit.source_languages) or 'n/a'}; "
        f"flags: {', '.join(audit.flags) or 'none'}."
    )
    for note in audit.chinese_source_notes:
        st.write(f"- {note}")
    references = result.historical_references
    st.markdown("#### App Outcome Memory")
    st.caption(
        f"Status: {references.status}; scope: {references.scope}; "
        f"resolved sample: {references.sample_size}/{references.minimum_sample_size}."
    )
    if references.summary:
        st.write(references.summary)
    if references.hit_rate_pct is not None or references.mean_realized_return_pct is not None:
        cols = st.columns(2)
        cols[0].metric(
            "Analog Hit Rate",
            f"{references.hit_rate_pct:.1f}%" if references.hit_rate_pct is not None else "n/a",
        )
        cols[1].metric(
            "Mean Realized Return",
            f"{references.mean_realized_return_pct:+.1f}%" if references.mean_realized_return_pct is not None else "n/a",
        )
    if references.references:
        st.dataframe(
            [
                {
                    "Ticker": reference.ticker,
                    "Idea": reference.idea_title,
                    "Stage": reference.stage,
                    "Direction": reference.direction,
                    "Similarity": reference.similarity_score,
                    "Outcome": reference.outcome_status,
                    "Realized": (
                        f"{reference.realized_return_pct:+.1f}%"
                        if reference.realized_return_pct is not None else "n/a"
                    ),
                    "Reasons": "; ".join(reference.match_reasons),
                }
                for reference in references.references
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in references.data_gaps:
        st.caption(gap)
    if result.action_plan:
        st.markdown("#### Action Plan")
        st.dataframe(
            [
                {
                    "Criterion": item.criterion,
                    "Metric": item.metric or "n/a",
                    "Operator": item.operator or "n/a",
                    "Threshold": item.threshold if item.threshold is not None else "n/a",
                    "Deadline": item.deadline or "n/a",
                    "Source": item.source_field,
                    "Confirm": item.confirm_trigger,
                    "Break": item.break_trigger,
                }
                for item in result.action_plan
            ],
            use_container_width=True,
            hide_index=True,
        )
    if brief.citations:
        st.markdown("#### Source Citations")
        for citation in brief.citations[:6]:
            st.write(f"- [{citation.source}]({citation.url}) - {citation.snippet or citation.section or citation.form or 'source excerpt'}")
    if brief.data_gaps or sufficiency.data_gaps:
        st.markdown("#### Data Gaps")
        for gap in list(dict.fromkeys(brief.data_gaps + sufficiency.data_gaps)):
            st.caption(gap)
    st.markdown("#### LLM Run Manifest")
    st.caption(
        f"Status: {manifest.status}; provider: {manifest.provider}; model: {manifest.model}; "
        f"prompt: {manifest.prompt_version}; token estimate: {manifest.token_estimate or 'n/a'}."
    )
    if manifest.prompt_hash:
        st.caption(f"Prompt fingerprint: {manifest.prompt_hash[:16]}...")
    if manifest.prompt_context_counts:
        st.caption(
            "Context counts: "
            + ", ".join(f"{key}={value}" for key, value in manifest.prompt_context_counts.items())
        )
    if manifest.guardrail_policy:
        st.caption("Guardrails: " + ", ".join(manifest.guardrail_policy))
    if manifest.message:
        st.caption(manifest.message)
    research_manifest = getattr(result, "llm_research_manifest", None)
    if research_manifest:
        st.markdown("#### LLM Research Assistant Policy")
        cols = st.columns(4)
        cols[0].metric("Assistant Status", research_manifest.status)
        cols[1].metric("Provider", research_manifest.provider)
        cols[2].metric("Registry", research_manifest.source_registry_version or "n/a")
        cols[3].metric("Executor", research_manifest.deterministic_executor or "n/a")
        if research_manifest.allowed_roles or research_manifest.prohibited_actions or research_manifest.validation_gates:
            policy_rows = []
            policy_rows.extend(
                {"Category": "Allowed role", "Rule": item}
                for item in research_manifest.allowed_roles
            )
            policy_rows.extend(
                {"Category": "Prohibited action", "Rule": item}
                for item in research_manifest.prohibited_actions
            )
            policy_rows.extend(
                {"Category": "Validation gate", "Rule": item}
                for item in research_manifest.validation_gates
            )
            st.dataframe(policy_rows, use_container_width=True, hide_index=True)
        st.caption(research_manifest.evidence_boundary)
        for message in research_manifest.messages[:3]:
            st.caption(message)


def render_company_economics(result: ResearchResult) -> None:
    economics = result.company_economics
    st.markdown("#### Company Economics + Industry Playbook")
    cols = st.columns(3)
    cols[0].metric("Economics Status", economics.status)
    cols[1].metric("Industry", economics.industry_playbook.industry_label)
    cols[2].metric("Material Drivers", economics.material_driver_count)
    st.metric("Playbook Quality", f"{economics.playbook_quality_score}/100")
    st.write(economics.business_model)
    playbook = economics.industry_playbook
    st.caption(
        f"KPIs: {', '.join(playbook.key_kpis) or 'n/a'} | "
        f"Valuation: {', '.join(playbook.valuation_methods) or 'n/a'} | "
        f"Macro: {', '.join(playbook.macro_sensitivities) or 'n/a'} | "
        f"Source: {getattr(playbook, 'playbook_source', 'built_in')}"
    )
    portfolio = getattr(result, "playbook_portfolio", None)
    if portfolio:
        st.markdown("**Validated Playbook Portfolio**")
        st.dataframe(
            [
                {
                    "Role": item.role,
                    "Playbook": item.label,
                    "Status": item.status,
                    "Rationale": item.rationale,
                    "Evidence IDs": "; ".join(item.evidence_ids[:4]) or "Unknown",
                }
                for item in [portfolio.primary] + portfolio.secondary
            ],
            use_container_width=True,
            hide_index=True,
        )
    if economics.drivers:
        st.dataframe(
            [
                {
                    "Driver": driver.name,
                    "Materiality": driver.materiality,
                    "Trend": driver.trend,
                    "Evidence": driver.current_evidence,
                    "Why it matters": driver.why_it_matters,
                    "Source": driver.source,
                }
                for driver in economics.drivers
            ],
            use_container_width=True,
            hide_index=True,
        )
    if getattr(economics, "playbook_quality", None):
        st.markdown("**Playbook Quality Checklist**")
        st.dataframe(
            [
                {
                    "Area": item.area,
                    "Status": item.status,
                    "Score": item.score,
                    "Summary": item.summary,
                    "Evidence": "; ".join(item.evidence[:3]),
                    "Gaps": "; ".join(item.gaps[:3]),
                    "Next action": item.next_action,
                    "Stage impact": item.stage_impact,
                }
                for item in economics.playbook_quality
            ],
            use_container_width=True,
            hide_index=True,
        )
    if getattr(economics, "driver_coverage", None):
        st.markdown("**Driver Coverage Checklist**")
        st.dataframe(
            [
                {
                    "Driver": item.driver_name,
                    "Materiality": item.materiality,
                    "Status": item.status,
                    "Current evidence": item.current_evidence,
                    "Required evidence": "; ".join(item.required_evidence[:2]),
                    "Missing evidence": "; ".join(item.missing_evidence[:3]),
                    "Next source": item.next_source,
                    "Stage impact": item.stage_impact,
                }
                for item in economics.driver_coverage
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in economics.data_gaps:
        st.caption(gap)


def render_credit_lens(result: ResearchResult) -> None:
    lens = getattr(result, "credit_lens", None)
    st.markdown("#### Credit Lens")
    if not lens:
        st.info("Credit lens is unavailable for the current run.")
        return
    cols = st.columns(3)
    cols[0].metric("Credit Status", lens.status)
    cols[1].metric("Risk Level", lens.risk_level)
    cols[2].metric("Metrics", len(lens.metrics))
    st.write(lens.summary)
    st.caption(lens.source_note)
    if lens.metrics:
        st.dataframe(
            [
                {
                    "Metric": metric.name,
                    "Value": metric.value if metric.value is not None else "n/a",
                    "Unit": metric.unit,
                    "Status": metric.status,
                    "Interpretation": metric.interpretation,
                    "Source": metric.source,
                }
                for metric in lens.metrics
            ],
            use_container_width=True,
            hide_index=True,
        )
    if getattr(lens, "credit_bridge", None):
        st.markdown("**Credit Bridge Checklist**")
        st.dataframe(
            [
                {
                    "Area": item.area,
                    "Status": item.status,
                    "Credit question": item.credit_question,
                    "Current evidence": item.current_evidence,
                    "Missing evidence": "; ".join(item.missing_evidence[:3]),
                    "Next source": item.next_source,
                    "Falsification test": item.falsification_test,
                    "Stage impact": item.stage_impact,
                }
                for item in lens.credit_bridge
            ],
            use_container_width=True,
            hide_index=True,
        )
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**Supports**")
        for item in lens.positives[:6]:
            st.write(f"- {item}")
    with cols[1]:
        st.markdown("**Risks**")
        for item in lens.risks[:6]:
            st.write(f"- {item}")
    with cols[2]:
        st.markdown("**Evidence Needed**")
        for item in lens.required_evidence[:6]:
            st.write(f"- {item}")
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**Monitor Rules**")
        for item in lens.monitor_rules[:6]:
            st.write(f"- {item}")
    with cols[1]:
        st.markdown("**Credit Catalysts**")
        for item in lens.credit_catalysts[:6]:
            st.write(f"- {item}")
    with cols[2]:
        st.markdown("**Falsification Tests**")
        for item in lens.falsification_tests[:6]:
            st.write(f"- {item}")
    for gap in lens.data_gaps[:8]:
        st.caption(gap)


def render_coverage_expansion(result: ResearchResult) -> None:
    diagnostics = result.coverage_expansion
    st.markdown("#### Coverage Expansion Diagnostics")
    cols = st.columns(3)
    cols[0].metric("Thesis Status", diagnostics.status)
    cols[1].metric("Coverage Profile", diagnostics.coverage_profile)
    cols[2].metric("Next Expansions", len(diagnostics.recommended_expansions))
    st.caption(diagnostics.summary)
    if diagnostics.why_no_convincing_thesis:
        with st.expander("Why no convincing thesis yet", expanded=diagnostics.status == "No convincing thesis yet"):
            for reason in diagnostics.why_no_convincing_thesis:
                st.write(f"- {reason}")
    if diagnostics.recommended_expansions:
        st.dataframe(
            [
                {
                    "Priority": action.priority,
                    "Area": action.area,
                    "Source": action.source_type,
                    "Action": action.action,
                    "Why": action.why_it_matters,
                    "Integrity rule": action.integrity_rule,
                    "Expected output": action.expected_output,
                    "Cost / latency": action.cost_latency,
                }
                for action in diagnostics.recommended_expansions
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("Integrity and latency policy", expanded=False):
        for note in diagnostics.integrity_notes:
            st.write(f"- {note}")
        for policy in diagnostics.latency_policy:
            st.write(f"- {policy}")


def render_global_coverage(result: ResearchResult) -> None:
    coverage_case = getattr(result, "coverage_case", None)
    matrix = getattr(result, "source_coverage_matrix", None)
    audit = getattr(result, "metric_resolution_audit", None)
    if not coverage_case and not matrix and not audit:
        return
    st.markdown("#### Global Coverage and Metric Resolution")
    cols = st.columns(4)
    cols[0].metric("Geography", coverage_case.geography if coverage_case else "Unknown")
    cols[1].metric("Security Type", coverage_case.security_type if coverage_case else "Unknown")
    cols[2].metric("Sources", len(matrix.entries) if matrix else 0)
    cols[3].metric("Metric Audit", audit.status if audit else "Unknown")
    if coverage_case:
        st.caption(
            f"{coverage_case.company_name}; filing regime {coverage_case.filing_regime}; "
            f"reporting standard {coverage_case.reporting_standard}; currency {coverage_case.currency}."
        )
        if coverage_case.primary_sources:
            st.write("Primary source stack: " + ", ".join(coverage_case.primary_sources))
        for gap in coverage_case.data_gaps[:4]:
            st.warning(gap)
    if matrix:
        with st.expander("Source Coverage Matrix", expanded=False):
            st.caption(matrix.summary)
            st.dataframe(
                [
                    {
                        "Source": item.source_type,
                        "Family": item.source_family,
                        "Status": item.status,
                        "Priority": item.priority,
                        "Official": "Yes" if item.official else "No",
                        "Tier": item.source_tier,
                        "Access": item.access_mode,
                        "Licensing": item.licensing_policy,
                        "Blocker": item.blocker,
                    }
                    for item in matrix.entries
                ],
                use_container_width=True,
                hide_index=True,
            )
            for gap in matrix.data_gaps[:6]:
                st.warning(gap)
    if audit:
        with st.expander("Canonical Metric Resolution Audit", expanded=False):
            st.caption(audit.summary)
            st.dataframe(
                [
                    {
                        "Metric": item.metric,
                        "Status": item.status,
                        "Method": item.resolution_method,
                        "Value": item.value if item.value is not None else "Unknown",
                        "Unit": item.unit,
                        "Period": item.period_end or "Unknown",
                        "Source metric": item.source_metric,
                        "Formula": item.formula,
                        "Blocker": item.blocker,
                    }
                    for item in audit.items[:24]
                ],
                use_container_width=True,
                hide_index=True,
            )
            for gap in audit.data_gaps[:8]:
                st.warning(gap)


def render_evidence_work_order(result: ResearchResult) -> None:
    work_order = getattr(result, "evidence_work_order", None)
    if not work_order:
        return
    st.markdown("#### Evidence Work Order")
    _wrapped_metric_grid([
        ("Status", work_order.status),
        ("Open Actions", len(work_order.items)),
        ("Research-Ready Blockers", sum(1 for item in work_order.items if item.blocks_research_ready)),
        ("High-Conviction Blockers", sum(1 for item in work_order.items if item.blocks_high_conviction)),
    ])
    st.caption(work_order.summary)
    if work_order.items:
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Channel": item.channel,
                    "Action": item.action,
                    "Source": item.source_type,
                    "Expected output": item.expected_output,
                    "Blocks RR": "Yes" if item.blocks_research_ready else "No",
                    "Blocks HC": "Yes" if item.blocks_high_conviction else "No",
                    "Origin": item.origin,
                }
                for item in work_order.items[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
        top = work_order.items[0]
        with st.expander("Top Evidence Work Order Detail", expanded=False):
            st.markdown(f"**Action:** {top.action}")
            st.write(top.why_it_matters)
            st.caption(f"Source type: {top.source_type}; origin: {top.origin}; cost/latency: {top.cost_latency or 'n/a'}")
            if top.acceptance_criteria:
                st.markdown("**Acceptance criteria**")
                for item in top.acceptance_criteria:
                    st.write(f"- {item}")
            if top.falsification_tests:
                st.markdown("**Falsification tests**")
                for item in top.falsification_tests:
                    st.write(f"- {item}")
    for gap in work_order.data_gaps:
        st.warning(gap)
    render_evidence_closure(result)


def render_evidence_closure(result: ResearchResult) -> None:
    report = getattr(result, "evidence_closure", None)
    if not report:
        return
    st.markdown("##### Automatic Evidence Closure")
    cols = st.columns(4)
    cols[0].metric("Resolved", report.resolved_count)
    cols[1].metric("Contradicted", report.contradicted_count)
    cols[2].metric("Unavailable", report.unavailable_count)
    cols[3].metric("Licensed / manual", report.licensed_or_manual_count)
    st.caption(report.summary)
    if report.outcomes:
        st.dataframe(
            [
                {
                    "Work order": outcome.work_id,
                    "Outcome": outcome.status.replace("_", " ").title(),
                    "Finding": outcome.summary,
                    "Adapters tried": "; ".join(attempt.adapter for attempt in outcome.attempted_adapters),
                    "Next action": outcome.next_action,
                }
                for outcome in report.outcomes
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_causal_thesis_graphs(result: ResearchResult) -> None:
    graphs = getattr(result, "causal_thesis_graphs", [])
    if not graphs:
        return
    st.markdown("#### Causal Thesis Graph")
    st.caption("Each connection is scored independently: source event → driver → KPI → earnings/FCF → valuation → catalyst.")
    graph = graphs[0]
    _wrapped_metric_grid([
        ("Graph status", graph.status),
        ("Connection score", f"{graph.overall_score}/100"),
        ("Weakest link", graph.weakest_link),
    ])
    st.info(graph.summary)
    st.dataframe(
        [
            {
                "Connection": edge.label,
                "Status": edge.status,
                "Score": edge.score,
                "Evidence": "; ".join(edge.evidence[:2]) or "Unknown",
                "Exact missing evidence": "; ".join(edge.missing_evidence[:2]) or "None",
                "Automatic next action": edge.next_automatic_action,
            }
            for edge in graph.edges
        ],
        use_container_width=True,
        hide_index=True,
    )
    if len(graphs) > 1:
        with st.expander("Other idea graphs", expanded=False):
            st.dataframe(
                [
                    {
                        "Idea": item.idea_id,
                        "Status": item.status,
                        "Score": item.overall_score,
                        "Weakest link": item.weakest_link,
                        "Diagnosis": item.summary,
                    }
                    for item in graphs[1:]
                ],
                use_container_width=True,
                hide_index=True,
            )


def render_market_implied_expectations(result: ResearchResult) -> None:
    implied = getattr(result, "market_implied_expectations", None)
    if not implied:
        return
    st.markdown("#### Reverse FCF / DCF and Market-Implied Expectations")
    _wrapped_metric_grid([
        ("Status", implied.status),
        ("Template", implied.template),
        ("Current price", f"{implied.current_price:,.2f} {implied.currency}" if implied.current_price else "Unknown"),
    ])
    st.caption(implied.summary)
    st.caption(
        f"Price source: {implied.price_source or 'Unknown'} | Price as of: {implied.price_as_of or 'Unknown'} | "
        f"Financial basis: {implied.financial_basis} | Period: {implied.financial_period or 'Unknown'}"
    )
    if implied.assumptions:
        with st.expander("Edit reverse-model assumptions", expanded=False):
            _render_market_implied_assumption_editor(
                result, "market_workspace"
            )
    if implied.expectations:
        st.dataframe(
            [
                {
                    "Market-implied variable": row.metric,
                    "Basis status": row.status,
                    "Value": (
                        f"{row.implied_value:,.2f} {row.unit}"
                        if row.implied_value is not None else row.status
                    ),
                    "Confidence": row.confidence,
                    "Interpretation": row.interpretation,
                    "Formula": row.formula,
                    "Missing inputs": "; ".join(row.missing_inputs),
                }
                for row in implied.expectations
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("Assumption provenance", expanded=False):
        for assumption in implied.assumptions:
            value = (
                "Unknown"
                if assumption.value is None
                else f"{assumption.value:g} {assumption.unit}"
            )
            st.write(
                f"- **{assumption.name}:** {value} | "
                f"{assumption.provenance} | {assumption.source}"
            )
    for gap in implied.data_gaps:
        st.info(gap)


def render_retrieval_and_regulatory_audit(
    result: ResearchResult,
) -> None:
    report = getattr(result, "retrieval_audit", None)
    radar = getattr(result, "regulatory_momentum", None)
    admissions = getattr(result, "source_admission_decisions", [])
    if report is None and radar is None and not admissions:
        return
    st.markdown("#### Evidence Retrieval and Regulatory Readiness")
    _wrapped_metric_grid([
        (
            "Benchmark readiness",
            (
                "Ready" if report and report.benchmark_ready
                else "Blocked" if report else "Not audited"
            ),
        ),
        (
            "Claim audit",
            (
                f"{report.claim_ready_count} passed / "
                f"{report.claim_blocked_count} blocked"
                if report else "Not run"
            ),
        ),
        (
            "Validated origins",
            report.source_origin_count if report else 0,
        ),
        (
            "Regulatory radar",
            radar.status if radar else "Unavailable",
        ),
    ])
    if report:
        st.caption(
            f"Coverage diagnostic: {report.input_sufficiency_score}/100. "
            "This score describes evidence breadth; claim-level validation "
            "remains the readiness authority."
        )
        if report.critical_blockers:
            st.warning(
                "Highest-priority blocker: "
                + report.critical_blockers[0]
            )
        if report.next_actions:
            st.info("Next automatic action: " + report.next_actions[0])
    if radar:
        st.write(radar.summary)
        if radar.next_milestones:
            st.caption(
                "Next procedural milestones: "
                + "; ".join(radar.next_milestones[:3])
            )
    with st.expander(
        "Source admission, regulatory prospects, and retrieval blockers",
        expanded=False,
    ):
        if admissions:
            st.markdown("**Open discovery / closed evidence admission**")
            st.dataframe(
                [
                    {
                        "Candidate": item.candidate_id,
                        "Decision": item.status,
                        "Admitted as": item.admitted_as,
                        "Source family": item.source_family,
                        "Tier": item.source_tier or "Unknown",
                        "Retrieval": item.retrieval_status,
                        "Validation": item.validation_status,
                        "Reason": "; ".join(item.reasons),
                    }
                    for item in admissions
                ],
                hide_index=True,
                use_container_width=True,
            )
        if radar and radar.prospects:
            st.markdown("**Regulatory prospects**")
            st.dataframe(
                [
                    {
                        "Prospect": item.headline,
                        "Classification": item.classification,
                        "Stage": item.procedural_stage.replace("_", " "),
                        "Jurisdiction": item.jurisdiction,
                        "Allowed thesis stage": item.allowed_stage,
                        "Origins": len(item.source_origin_groups),
                        "Official milestones": len(item.official_source_ids),
                        "Financial mechanism": "; ".join(
                            item.financial_mechanisms
                        ) or "Unquantified",
                        "Gap": "; ".join(item.data_gaps),
                    }
                    for item in radar.prospects
                ],
                hide_index=True,
                use_container_width=True,
            )
        if report and report.critical_blockers:
            st.markdown("**Critical evidence blockers**")
            for item in report.critical_blockers:
                st.write(f"- {item}")
        if report and report.manual_requirements:
            st.markdown("**Licensed or manual evidence required**")
            for item in report.manual_requirements:
                st.write(f"- {item}")
def render_market_implied_ic_card(result: ResearchResult) -> None:
    snapshot = getattr(result, "ic_implied_snapshot", None)
    implied = getattr(result, "market_implied_expectations", None)
    if not implied:
        return
    if snapshot is None:
        snapshot = build_ic_implied_expectations_snapshot(
            implied,
            result.company_model,
            result.valuation,
        )
        result.ic_implied_snapshot = snapshot
    st.markdown("#### What the Market Must Believe")
    _wrapped_metric_grid([
        (
            "Current price",
            f"{snapshot.current_price:,.2f} {snapshot.currency}"
            if snapshot.current_price is not None else "Unknown",
        ),
        (snapshot.operating_base_label, snapshot.operating_base_value),
        (snapshot.valuation_anchor_label, snapshot.valuation_anchor_value),
        (snapshot.implied_variable_label, snapshot.implied_variable_value),
        ("Confidence", snapshot.confidence),
    ])
    st.write(snapshot.interpretation)
    if snapshot.disagreement != "Unknown":
        st.caption(snapshot.disagreement)
    st.caption(
        f"Price as of {snapshot.price_as_of or 'Unknown'} | Weakest input: "
        f"{snapshot.weakest_input or 'None identified'}"
    )
    with st.expander("Assumptions & Sensitivity", expanded=False):
        _render_market_implied_assumption_editor(result, "ic_brief")
        if result.company_model.sensitivity:
            st.markdown("**Internal model sensitivity**")
            st.dataframe(
                [
                    {
                        "Row": item.row_label,
                        "Column": item.column_label,
                        "Value": (
                            f"{item.value:,.2f} {result.company_model.currency}"
                            if item.value is not None else "Unknown"
                        ),
                    }
                    for item in result.company_model.sensitivity[:12]
                ],
                use_container_width=True,
                hide_index=True,
            )
        st.caption(
            "The full formula, provenance, and all implied variables remain in the Research view."
        )


def _render_market_implied_assumption_editor(
    result: ResearchResult,
    key_prefix: str,
) -> None:
    implied = result.market_implied_expectations
    if not implied.assumptions:
        st.info("This sector template has no editable reverse-model assumptions yet.")
        return
    st.caption(
        "Implied variables are computed first from transparent screening defaults. Saved overrides "
        "remain user assumptions, not source facts, calibrated forecasts, or promotion evidence."
    )
    with st.form(f"{key_prefix}_market_implied_assumptions_{implied.ticker}"):
        input_columns = st.columns(min(4, len(implied.assumptions)))
        edited_assumptions: dict[str, float] = {}
        for index, assumption in enumerate(implied.assumptions):
            key = assumption.key or assumption.name.lower().replace(" ", "_")
            edited_assumptions[key] = input_columns[index % len(input_columns)].number_input(
                assumption.name,
                min_value=float(assumption.minimum) if assumption.minimum is not None else None,
                max_value=float(assumption.maximum) if assumption.maximum is not None else None,
                value=float(assumption.value or 0.0),
                step=float(assumption.step or 0.1),
                help=f"{assumption.source}. Current provenance: {assumption.provenance}.",
                key=f"{key_prefix}_{implied.ticker}_{key}",
            )
        save_assumptions = st.form_submit_button(
            "Recalculate and Save",
            use_container_width=True,
        )
        reset_assumptions = st.form_submit_button(
            "Reset Sensible Defaults",
            use_container_width=True,
        )
    if save_assumptions or reset_assumptions:
        store = ResearchStore()
        if reset_assumptions:
            store.clear_market_implied_assumptions(implied.ticker)
            overrides = {}
        else:
            overrides = store.save_market_implied_assumptions(
                implied.ticker,
                edited_assumptions,
            )
        result.market_implied_expectations = build_market_implied_expectations(
            result.identity,
            result.metrics,
            implied.current_price,
            result.valuation,
            result.company_model,
            price_source=implied.price_source,
            price_as_of=implied.price_as_of,
            assumption_overrides=overrides,
        )
        result.ic_implied_snapshot = build_ic_implied_expectations_snapshot(
            result.market_implied_expectations,
            result.company_model,
            result.valuation,
        )
        st.session_state.result = result
        st.rerun()
    st.caption(
        "Defaults are for screening. Test sensitivities before using the output in an IC decision."
    )


def render_reverse_fcf_snapshot(result: ResearchResult) -> None:
    render_market_implied_ic_card(result)


def _implied_row_value(row) -> str:
    if not row or row.implied_value is None:
        return "Unavailable"
    return f"{row.implied_value:,.2f} {row.unit}".strip()


def render_earnings_surprise_proxy(result: ResearchResult) -> None:
    proxy = getattr(result, "earnings_surprise_proxy", None)
    if not proxy:
        return
    st.markdown("#### Earnings-Surprise Proxy")
    _wrapped_metric_grid([
        ("Status", proxy.status),
        ("Comparable observations", str(len(proxy.items))),
        ("Revision follow-through", "Available" if proxy.revision_follow_through_available else "Not measured"),
    ])
    st.write(proxy.headline)
    st.caption(proxy.methodology)
    if proxy.items:
        st.dataframe(
            [
                {
                    "Event": item.event_label,
                    "Event date": item.event_date or "Unknown",
                    "Period": item.reporting_period or "Unknown",
                    "Metric": item.metric,
                    "Actual": item.actual if item.actual is not None else "Unknown",
                    "Estimate": item.estimate if item.estimate is not None else "Unknown",
                    "Surprise %": item.surprise_pct if item.surprise_pct is not None else "Unknown",
                    "Estimate as of": item.estimate_as_of or "Unknown",
                    "Estimate source": item.estimate_source,
                    "Actual source": item.actual_source,
                    "Confidence": item.confidence,
                }
                for item in proxy.items
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in proxy.data_gaps:
        st.info(gap)


def render_recent_market_context(result: ResearchResult) -> None:
    context = getattr(result, "recent_market_context", None)
    if not context:
        return
    st.markdown("#### Recent Market Context")
    _wrapped_metric_grid([
        ("Status", context.status),
        ("Price source", context.source),
        ("Price as of", context.price_as_of or "Unknown"),
        ("60d volatility", f"{context.annualized_volatility_pct:.1f}%" if context.annualized_volatility_pct is not None else "Unknown"),
        ("Max drawdown", f"{context.max_drawdown_pct:.1f}%" if context.max_drawdown_pct is not None else "Unknown"),
        ("Recent volume ratio", f"{context.recent_volume_ratio:.2f}x" if context.recent_volume_ratio is not None else "Unknown"),
    ])
    st.caption(context.summary)
    if context.windows:
        st.dataframe(
            [
                {
                    "Window": window.label,
                    "Stock return %": window.return_pct if window.return_pct is not None else "Unknown",
                    "SPY return %": window.benchmark_return_pct if window.benchmark_return_pct is not None else "Unknown",
                    "Relative return %": window.relative_return_pct if window.relative_return_pct is not None else "Unknown",
                    "Status": window.status,
                }
                for window in context.windows
            ],
            use_container_width=True,
            hide_index=True,
        )
    for implication in context.thesis_implications:
        st.write(f"- {implication}")
    for gap in context.data_gaps:
        st.info(gap)


def render_company_model_workspace(result: ResearchResult) -> None:
    model = getattr(result, "company_model", None)
    if not model:
        return
    st.markdown("#### Company Model Workspace")
    st.caption(f"{model.status}. {model.summary}")
    if model.cases:
        st.dataframe(
            [
                {
                    "Case": case.name,
                    "Revenue": case.revenue if case.revenue is not None else "Unknown",
                    "Operating margin %": case.operating_margin_pct if case.operating_margin_pct is not None else "Unknown",
                    "Net income": case.net_income if case.net_income is not None else "Unknown",
                    "FCF": case.free_cash_flow if case.free_cash_flow is not None else "Unknown",
                    "Fair value": case.fair_value if case.fair_value is not None else "Unknown",
                }
                for case in model.cases
            ],
            use_container_width=True,
            hide_index=True,
        )
    with st.expander("Historical model and formula audit", expanded=False):
        st.dataframe(
            [
                {
                    "Statement": row.statement,
                    "Metric": row.metric,
                    "Period": row.period,
                    "Value": row.value if row.value is not None else "Unknown",
                    "Unit": row.unit,
                    "Provenance": row.provenance,
                    "Formula": row.formula,
                    "Source": row.source,
                }
                for row in model.historicals
            ],
            use_container_width=True,
            hide_index=True,
        )
        for gap in model.data_gaps[:8]:
            st.warning(gap)


def render_research_modes(result: ResearchResult) -> None:
    suite = getattr(result, "research_modes", None)
    if not suite:
        return
    st.markdown("#### Driver-Specific Research Modes")
    st.caption("Choose the workflow that matches the decision; recommended modes are inferred from current events and drivers.")
    st.dataframe(
        [
            {
                "Mode": mode.label,
                "Recommended": "Yes" if mode.recommended else "No",
                "Readiness": mode.status,
                "Score": mode.score,
                "Available metrics": ", ".join(mode.available_metrics),
                "Missing metrics": ", ".join(mode.missing_metrics),
                "Next action": mode.next_actions[0] if mode.next_actions else "Ready for analysis",
            }
            for mode in sorted(suite.modes, key=lambda item: (not item.recommended, -item.score))
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_market_capture_readiness(result: ResearchResult) -> None:
    readiness = getattr(result, "market_capture_readiness", None)
    if not readiness:
        return
    st.markdown("#### Market Capture Readiness")
    cols = st.columns(6)
    cols[0].metric("Status", readiness.status)
    cols[1].metric("Classified", f"{readiness.classified_ideas}/{readiness.total_ideas}")
    cols[2].metric("Price-only", getattr(readiness, "price_only_ideas", 0))
    cols[3].metric("Unknown", readiness.unknown_ideas)
    cols[4].metric("Price", readiness.price_coverage)
    cols[5].metric("Consensus", readiness.consensus_coverage)
    st.caption(readiness.summary)
    st.caption(readiness.point_in_time_rule)
    import_plan = getattr(readiness, "import_plan", None)
    if import_plan:
        st.markdown("**Consensus Import Plan**")
        plan_cols = st.columns(4)
        plan_cols[0].metric("Import status", import_plan.status)
        plan_cols[1].metric("Minimum viable rows", getattr(import_plan, "minimum_viable_rows", import_plan.minimum_required_rows))
        plan_cols[2].metric("Full revision rows", getattr(import_plan, "full_revision_rows", import_plan.minimum_required_rows))
        plan_cols[3].metric("Event dates", len(import_plan.event_dates))
        st.caption(import_plan.summary)
        if getattr(import_plan, "practical_next_step", ""):
            st.info(import_plan.practical_next_step)
        if import_plan.blocking_reason:
            st.warning(import_plan.blocking_reason)
        if getattr(import_plan, "provider_options", None):
            st.markdown("**Cost-effective provider paths**")
            for option in import_plan.provider_options:
                st.write(f"- {option}")
        if import_plan.required_files:
            st.write("Required files: " + ", ".join(import_plan.required_files))
        if import_plan.optional_files:
            st.caption("Optional files: " + ", ".join(import_plan.optional_files))
        if import_plan.template_command:
            st.code(import_plan.template_command, language="bash")
        if import_plan.import_command:
            st.code(import_plan.import_command, language="bash")
        for step in import_plan.next_steps:
            st.write(f"- {step}")
    advisor = getattr(readiness, "consensus_advisor", None)
    if advisor:
        st.markdown("**Consensus Coverage Advisor**")
        advisor_cols = st.columns(3)
        advisor_cols[0].metric("Advisor status", advisor.status)
        advisor_cols[1].metric("Blocker", advisor.blocker)
        advisor_cols[2].metric("Required fix", advisor.required_fix[:48] + ("..." if len(advisor.required_fix) > 48 else ""))
        st.caption(advisor.summary)
        st.caption(advisor.no_lookahead_rule)
    autofill = getattr(readiness, "autofill_plan", None)
    if autofill:
        st.markdown("**Market Capture Autofill Plan**")
        autofill_cols = st.columns(3)
        autofill_cols[0].metric("Status", autofill.status)
        autofill_cols[1].metric("Minimum viable rows", autofill.minimum_viable_rows)
        autofill_cols[2].metric("Full revision rows", autofill.full_revision_rows)
        st.caption(autofill.summary)
    if readiness.actions:
        st.dataframe(
            [
                {
                    "Priority": action.priority,
                    "Area": action.area,
                    "Status": action.status,
                    "Action": action.action,
                    "Why it matters": action.why_it_matters,
                    "Source type": action.source_type,
                    "Ideas": ", ".join(action.related_idea_ids),
                }
                for action in readiness.actions
            ],
            use_container_width=True,
            hide_index=True,
        )
    if readiness.snapshot_needs:
        st.markdown("**Point-in-time Snapshot Checklist**")
        st.dataframe(
            [
                {
                    "Idea": item.idea_id,
                    "Event date": item.event_date or "unknown",
                    "Metric family": item.metric_family,
                    "Pre-event snapshot": item.pre_event_snapshot,
                    "Post-event snapshot": item.post_event_snapshot,
                    "Accepted sources": "; ".join(item.accepted_sources[:4]),
                    "CSV row hints": " | ".join(item.csv_row_hints[:3]),
                    "Reason": item.reason,
                    "Status": item.status,
                }
                for item in readiness.snapshot_needs
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in readiness.data_gaps:
        st.warning(gap)


def render_latency_profile(result: ResearchResult) -> None:
    profiling = getattr(result, "profiling", None)
    if not profiling:
        return
    st.markdown("#### Latency Profile")
    cols = st.columns(3)
    cols[0].metric("Status", profiling.status)
    cols[1].metric("Total runtime", f"{profiling.total_ms / 1000:.2f}s" if profiling.total_ms else "n/a")
    cols[2].metric("Stages", len(profiling.steps))
    if profiling.bottlenecks:
        st.markdown("**Slowest stages**")
        for item in profiling.bottlenecks[:5]:
            st.write(f"- {item}")
    if getattr(profiling, "treatments", None):
        st.markdown("**Bottleneck treatments**")
        for item in profiling.treatments[:8]:
            st.write(f"- {item}")
    for note in profiling.notes:
        st.caption(note)


def render_thesis_clusters(result: ResearchResult) -> None:
    st.markdown("#### Thesis Clusters")
    if not result.thesis_clusters:
        st.info("No thesis clusters were generated.")
        return
    st.dataframe(
        [
            {
                "Cluster": cluster.label,
                "Status": cluster.status,
                "Stage": cluster.stage,
                "Score": cluster.score if cluster.score is not None else "n/a",
                "Chain": cluster.conviction_chain_status,
                "Driver": cluster.driver_name,
                "Ideas": len(cluster.idea_ids),
                "Priced in": cluster.priced_in,
                "Gaps": "; ".join(cluster.evidence_gaps),
            }
            for cluster in result.thesis_clusters[:8]
        ],
        use_container_width=True,
        hide_index=True,
    )
    top = result.thesis_clusters[0]
    with st.expander("Top Cluster IC One-Pager", expanded=False):
        st.write(top.thesis)
        if top.why_now:
            st.markdown(f"**Why now:** {top.why_now}")
        st.markdown(f"**Counter-thesis:** {top.counter_thesis}")
        if top.what_must_be_true:
            st.markdown("**What must be true**")
            for item in top.what_must_be_true:
                st.write(f"- {item}")
        if top.what_would_falsify:
            st.markdown("**What would falsify it**")
            for item in top.what_would_falsify:
                st.write(f"- {item}")
        st.markdown("**Valuation bridge**")
        for item in top.valuation_bridge:
            st.write(f"- {item}")
        st.markdown("**Monitoring checklist**")
        for item in top.monitor_checklist:
            st.write(f"- {item}")
        if top.next_research_actions:
            st.markdown("**Next research actions**")
            for item in top.next_research_actions:
                st.write(f"- {item}")


def render_research_questions(result: ResearchResult) -> None:
    st.markdown("#### Research Questions")
    questions = getattr(result, "research_questions", [])
    if not questions:
        st.info("No open research questions were generated. Either the evidence chain is complete, or no source-linked signal was strong enough to investigate.")
        return
    st.dataframe(
        [
            {
                "Question": item.title,
                "Priority": item.priority,
                "Status": item.status,
                "Answerability": item.answerability_status,
                "Score": item.answerability_score,
                "Driver": item.driver_name,
                "Missing links": "; ".join(item.missing_links[:3]),
                "Market capture needs": "; ".join(item.market_capture_needs[:2]),
            }
            for item in questions[:8]
        ],
        use_container_width=True,
        hide_index=True,
    )
    top = questions[0]
    with st.expander("Top Research Question Workplan", expanded=False):
        st.write(top.source_signal)
        st.markdown(f"**Why it matters:** {top.why_it_matters}")
        st.markdown(
            f"**Answerability:** {top.answerability_status} "
            f"({top.answerability_score}/100)"
        )
        if top.decision_rule:
            st.write(top.decision_rule)
        if top.hypothesis:
            st.markdown("**Hypothesis to test**")
            st.write(top.hypothesis)
        if top.minimum_evidence_package:
            st.markdown("**Minimum evidence package**")
            for item in top.minimum_evidence_package[:5]:
                st.write(f"- {item}")
        if top.answer_format:
            st.markdown("**Expected answer format**")
            st.write(top.answer_format)
        if top.stop_condition:
            st.markdown("**Stop condition**")
            st.write(top.stop_condition)
        if top.answerability_gaps:
            st.markdown("**Answerability gaps**")
            for item in top.answerability_gaps[:6]:
                st.write(f"- {item}")
        if top.required_evidence:
            st.markdown("**Required evidence**")
            for item in top.required_evidence[:6]:
                st.write(f"- {item}")
        if top.primary_source_types:
            st.markdown("**Primary source types**")
            for item in top.primary_source_types[:6]:
                st.write(f"- {item}")
        if top.next_sources:
            st.markdown("**Next sources to check**")
            for item in top.next_sources[:6]:
                st.write(f"- {item}")
        if top.workplan_steps:
            st.markdown("**Workplan steps**")
            for item in top.workplan_steps[:6]:
                st.write(f"- {item}")
        if top.acceptance_criteria:
            st.markdown("**Acceptance criteria**")
            for item in top.acceptance_criteria[:6]:
                st.write(f"- {item}")
        if top.falsification_tests:
            st.markdown("**Falsification tests**")
            for item in top.falsification_tests[:6]:
                st.write(f"- {item}")
        if top.promotion_criteria:
            st.markdown("**Promotion criteria**")
            for item in top.promotion_criteria:
                st.write(f"- {item}")
        if top.equity_lens or top.credit_lens:
            st.markdown("**Equity / credit lens**")
            if top.equity_lens:
                st.write(top.equity_lens)
            if top.credit_lens:
                st.write(top.credit_lens)


def render_conviction_chain(result: ResearchResult) -> None:
    if not result.ideas or not result.ideas[0].conviction_chain:
        st.markdown("#### Conviction Chain")
        st.info("No conviction chain was generated for the current run.")
        return
    chain = result.ideas[0].conviction_chain
    st.markdown("#### Conviction Chain")
    cols = st.columns(3)
    cols[0].metric("Chain Status", chain.status)
    cols[1].metric("Confidence", chain.confidence)
    cols[2].metric("Links Complete", f"{sum(1 for step in chain.steps if step.status == 'Complete')}/{len(chain.steps)}")
    st.caption(chain.summary)
    st.dataframe(
        [
            {
                "Link": step.label,
                "Status": step.status,
                "Statement": step.statement,
                "Evidence": "; ".join(step.evidence),
                "Gaps": "; ".join(step.data_gaps),
            }
            for step in chain.steps
        ],
        use_container_width=True,
        hide_index=True,
    )
    cols = st.columns(3)
    with cols[0]:
        st.markdown("**What must be true**")
        for item in chain.what_must_be_true:
            st.write(f"- {item}")
    with cols[1]:
        st.markdown("**What would falsify it**")
        for item in chain.what_would_falsify:
            st.write(f"- {item}")
    with cols[2]:
        st.markdown("**Next research actions**")
        for item in chain.next_research_actions or ["No immediate chain gaps."]:
                st.write(f"- {item}")


def render_research_scout(result: ResearchResult) -> None:
    scout = getattr(result, "research_scout", None)
    if not scout:
        return
    st.markdown("#### Research Scout")
    cols = st.columns(3)
    cols[0].metric("Status", scout.status)
    cols[1].metric("Open questions", len(scout.questions))
    cols[2].metric("Provider", scout.provider)
    st.caption(scout.summary)
    axis_rows = []
    for label, rows in (
        ("Company", scout.company_story_axes),
        ("Sector", scout.sector_story_axes),
        ("Geography", scout.geography_story_axes),
        ("Peers", scout.peer_story_axes),
    ):
        for item in rows[:4]:
            axis_rows.append({"Story axis": label, "What to frame": item})
    if axis_rows:
        st.dataframe(axis_rows, use_container_width=True, hide_index=True)
    if scout.questions:
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Lens": item.lens,
                    "Question": item.question,
                    "Source types": "; ".join(item.source_types[:4]),
                    "Expected evidence": item.expected_evidence,
                    "Status": item.current_status,
                    "Story use": item.story_use,
                }
                for item in scout.questions[:10]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if scout.data_gaps:
        with st.expander("Research Scout gaps", expanded=False):
            for gap in scout.data_gaps:
                st.warning(gap)


def render_validated_claims_and_source_plan(result: ResearchResult) -> None:
    st.markdown("#### Validated Claims")
    claims = result.validated_claims
    cols = st.columns(3)
    cols[0].metric("Claim Status", claims.status)
    cols[1].metric("Claims", len(claims.claims))
    cols[2].metric("Provider", claims.provider)
    if claims.claims:
        st.dataframe(
            [
                {
                    "Status": claim.status,
                    "Category": claim.event_category,
                    "Direction": claim.direction,
                    "Driver": claim.business_driver,
                    "Metric": claim.metric or "n/a",
                    "Confidence": claim.confidence,
                    "What changed": claim.changed_text or claim.supporting_quote,
                    "Reason": claim.reason,
                    "Why not thesis-grade": claim.not_thesis_grade_reason,
                }
                for claim in claims.claims[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in claims.data_gaps:
        st.caption(gap)
    plan = result.source_plan
    st.markdown("#### Research Source Plan")
    st.caption(
        f"Status: {plan.status}; registry: {plan.registry_version}; provider: {plan.provider}. "
        "The LLM may recommend source types, but deterministic adapters must fetch them."
    )
    if plan.requests:
        st.dataframe(
            [
                {
                    "Priority": request.priority,
                    "Source type": request.source_type,
                    "Title": request.title,
                    "Why inspect": request.reason_to_inspect,
                    "Expected evidence": request.expected_evidence_type,
                    "Confirms/disproves": request.confirms_or_disproves,
                    "Cost/latency": request.cost_latency,
                    "Status": request.status,
                }
                for request in plan.requests[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    for gap in plan.data_gaps:
        st.caption(gap)
    render_news_intelligence(result)


def render_news_intelligence(result: ResearchResult) -> None:
    claims = getattr(result, "news_claims", []) or []
    corroboration = getattr(result, "source_corroboration_results", []) or []
    bridges = getattr(result, "causal_bridges", []) or []
    st.markdown("#### News Intelligence & Primary Source Corroboration")
    st.caption(
        "Credible news is used for event discovery and source leads only. "
        "It cannot independently create Research-Ready or High-Conviction ideas."
    )
    cols = st.columns(3)
    cols[0].metric("News Claims", len(claims))
    cols[1].metric("Corroboration Checks", len(corroboration))
    missing = sum(1 for item in corroboration if item.status == "Primary corroboration missing")
    cols[2].metric("Primary Gaps", missing)
    if claims:
        st.dataframe(
            [
                {
                    "Status": claim.status,
                    "Source family": claim.source_family,
                    "Driver": claim.affected_driver,
                    "Event": claim.event_type,
                    "Confidence": claim.confidence,
                    "Claim": claim.claimed_fact,
                    "Required corroboration": "; ".join(claim.required_corroboration[:2]),
                }
                for claim in claims[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if corroboration:
        st.dataframe(
            [
                {
                    "Status": item.status,
                    "Driver": item.driver_family,
                    "Explanation": item.explanation,
                    "Gaps": "; ".join(item.gaps[:2]),
                }
                for item in corroboration[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if bridges:
        st.caption(
            f"Causal bridge records attached: {len(bridges)}. "
            "Bridge status is shown on idea cards when relevant."
        )


def render_wisburg_research_lens(result: ResearchResult) -> None:
    lens = getattr(result, "wisburg_lens", None)
    st.markdown("#### Outside Analyst Debate")
    if not lens or lens.status == "Unavailable":
        caveats = lens.caveats if lens else ["Wisburg research lens was not attached."]
        st.caption("; ".join(caveats))
        return

    narrative = lens.narrative_score
    debate = lens.debate_map
    cols = st.columns(4)
    cols[0].metric("Wisburg Lens", lens.status)
    cols[1].metric("Research Excerpts", len(lens.excerpts))
    cols[2].metric("Themes", len(lens.themes))
    cols[3].metric("Narrative", narrative.label if narrative else "Unknown")
    st.caption(
        "Wisburg is used for outside-analyst debate, narrative crowding, and source suggestions. "
        "It cannot independently promote an idea to Research-Ready or High-Conviction."
    )
    coverage = getattr(lens, "coverage_audit", None)
    if coverage:
        with st.expander("Wisburg entitlement and coverage audit", expanded=False):
            st.write(
                f"**{coverage.status}**. Authentication: {coverage.authentication_status}. "
                f"Tool discovery: {coverage.tool_discovery_status}. "
                f"Observed {coverage.total_items} item(s); fetched structured detail for "
                f"{coverage.detailed_items} item(s)."
            )
            st.dataframe(
                [
                    {
                        "Tool": item.tool_name,
                        "Category": item.source_category,
                        "Entitlement": item.status,
                        "Queries": item.query_count,
                        "Items": item.item_count,
                        "Details": item.detail_success_count,
                        "Message": item.message,
                    }
                    for item in coverage.tools
                ],
                use_container_width=True,
                hide_index=True,
            )
            for gap in coverage.data_gaps:
                st.caption(gap)
            if lens.reports:
                st.markdown("**Normalized report coverage**")
                st.dataframe(
                    [
                        {
                            "Report": item.title,
                            "Category": item.category,
                            "Publisher": item.publisher,
                            "Published": item.published_at or "Unknown",
                            "Language": item.source_language,
                            "Detail status": item.detail_status,
                            "Stored scope": item.content_scope,
                            "Tier": item.source_tier,
                        }
                        for item in lens.reports[:20]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
    delta = ResearchStore().latest_wisburg_delta(result.identity.ticker)
    if delta:
        st.markdown("**Point-in-Time Wisburg Change**")
        st.write(delta.get("summary") or "No delta summary is available.")
        st.caption(
            f"Status: {delta.get('status', 'Unknown')}; current snapshot: {delta.get('observed_at', 'Unknown')}; "
            f"prior snapshot: {delta.get('prior_observed_at') or 'First baseline'}; "
            f"new reports: {len(delta.get('new_report_ids') or [])}; "
            f"stance changes: {len(delta.get('theme_stance_changes') or [])}; "
            f"external revisions: {len(delta.get('new_revision_ids') or [])}; "
            f"corroboration changes: {len(delta.get('corroboration_changes') or [])}."
        )
        if delta.get("new_report_titles"):
            with st.expander("Newly observed Wisburg items", expanded=False):
                for title in delta["new_report_titles"]:
                    st.write(f"- {title}")
    if lens.revisions:
        st.markdown("**External Revision Observations**")
        st.caption(
            "These are report-level analyst observations from Wisburg. They are not official "
            "consensus snapshots and cannot establish that expectations moved."
        )
        st.dataframe(
            [
                {
                    "As of": item.source_as_of or "Unknown",
                    "Type": item.revision_type,
                    "Metric": item.metric,
                    "Direction": item.direction,
                    "Previous": item.previous_value,
                    "Current": item.current_value,
                    "Change %": item.change_pct,
                    "Period": item.fiscal_period or "Unknown",
                    "Eligibility": item.eligibility,
                    "Statement": item.statement,
                }
                for item in lens.revisions[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if lens.structured_claims:
        st.markdown("**Structured Claims and Primary-Source Cross-Check**")
        st.dataframe(
            [
                {
                    "Claim": item.statement,
                    "Type": item.claim_type,
                    "Driver": item.driver,
                    "Metric": item.metric or "Unknown",
                    "Period": item.fiscal_period or "Unknown",
                    "Tier": item.source_tier,
                    "Cross-check": item.corroboration_status,
                    "Primary matches": len(item.primary_evidence_ids),
                    "Allowed stage": item.allowed_stage,
                }
                for item in lens.structured_claims[:20]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if lens.research_tasks:
        st.markdown("**Executable Wisburg Research Work Orders**")
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Source type": item.source_type,
                    "Action": item.action,
                    "Expected evidence": item.expected_evidence,
                    "Confirm/disprove": item.confirms_or_disproves,
                    "Status": item.status,
                }
                for item in lens.research_tasks
            ],
            use_container_width=True,
            hide_index=True,
        )
    if debate:
        st.markdown("**Bull / Bear Debate Map**")
        st.write(
            f"Status: {debate.status}. "
            f"Bull case: {debate.strongest_bull_case or 'n/a'} "
            f"Bear case: {debate.strongest_bear_case or 'n/a'}"
        )
    if lens.themes:
        st.dataframe(
            [
                {
                    "Theme": theme.label,
                    "Stance": theme.stance,
                    "Driver": theme.driver,
                    "Evidence": theme.evidence_count,
                    "Language": ", ".join(theme.source_language_mix) or "n/a",
                    "Confidence": theme.confidence,
                    "Summary": theme.summary,
                }
                for theme in lens.themes[:8]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if narrative:
        st.markdown("**Narrative Crowding**")
        st.caption(
            f"{narrative.label}; score {narrative.score if narrative.score is not None else 'n/a'}; "
            f"items {narrative.item_count}; repeated topics: {', '.join(narrative.repeated_topics) or 'n/a'}."
        )
    if lens.source_suggestions:
        st.markdown("**Wisburg Source Suggestions**")
        st.dataframe(
            [
                {
                    "Priority": item.priority,
                    "Source type": item.source_type,
                    "Title": item.title,
                    "Why inspect": item.reason_to_inspect,
                    "Expected evidence": item.expected_evidence_type,
                    "Confirm/disprove": item.confirms_or_disproves,
                }
                for item in lens.source_suggestions[:8]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if lens.excerpts:
        with st.expander("Capped Wisburg excerpts and caveats", expanded=False):
            st.dataframe(
                [
                    {
                        "Title": item.title,
                        "Category": item.category,
                        "Language": item.source_language,
                        "As of": item.source_as_of or "n/a",
                        "Themes": ", ".join(item.theme_tags),
                        "Target/rating": item.non_consensus_label if item.mentions_target_or_rating else "n/a",
                        "Excerpt": item.original_excerpt,
                        "Summary": item.translated_summary or item.generated_summary,
                    }
                    for item in lens.excerpts[:10]
                ],
                use_container_width=True,
                hide_index=True,
            )
            for caveat in lens.caveats:
                st.caption(caveat)


def render_research_radar(result: ResearchResult) -> None:
    resolution = result.entity_resolution
    coverage = result.financial_coverage
    status_cols = st.columns(3)
    status_cols[0].metric("Listing Status", resolution.listing_status)
    status_cols[1].metric("Financial Coverage", coverage.status.replace("_", " ").title())
    status_cols[2].metric("Exchange", resolution.exchange or "Unknown")
    if resolution.warning:
        st.warning(resolution.warning)
    st.caption(coverage.reason)
    if result.coverage_notes:
        for note in result.coverage_notes:
            st.info(note)

    render_consensus(result)
    render_event_workflow(result)

    st.subheader("Detected Changes")
    if not result.events:
        st.warning("No material changes detected from the loaded filings and facts.")
    else:
        assessment_by_event = {
            item.event_id: item for item in getattr(result, "metric_assessments", [])
        }
        for event in result.events[:8]:
            assessment = assessment_by_event.get(event_identifier(event))
            if not assessment:
                continue
            with st.container(border=True):
                st.markdown(f"**{assessment.metric_name}: {assessment.interpretation}**")
                st.caption(f"{assessment.event_label} | Polarity: {assessment.polarity}")
                st.write(assessment.observed_change)
                hypothesis_cols = st.columns(2)
                with hypothesis_cols[0]:
                    st.markdown("**Constructive hypothesis**")
                    st.write(assessment.constructive_hypothesis.mechanism if assessment.constructive_hypothesis else "Unknown")
                with hypothesis_cols[1]:
                    st.markdown("**Adverse hypothesis**")
                    st.write(assessment.adverse_hypothesis.mechanism if assessment.adverse_hypothesis else "Unknown")
                st.caption(f"Historical trend: {assessment.historical_trend}")
                st.info(f"Next automatic research action: {assessment.next_automatic_action}")
        event_rows = [
            {
                "Category": event.category.replace("_", " ").title(),
                "Title": event.title,
                "Direction": event.direction,
                "Severity": event.severity,
                "Date": event.event_date,
                "Summary": event.summary,
                "Why this matters": event.why_this_matters,
            }
            for event in result.events[:20]
        ]
        with st.expander("Raw detected-change table", expanded=False):
            st.dataframe(event_rows, use_container_width=True, hide_index=True)

    st.subheader("Financial Snapshot")
    metric_rows = [
        {
            "Metric": metric.name,
            "Latest": f"{format_number(metric.value)} {metric.unit}",
            "Period": metric.period_end,
            "Filed": metric.filed,
            "YoY / comparable": (
                f"{metric.yoy_change_pct:+.1f}%"
                if metric.yoy_change_pct is not None
                else "n/a"
            ),
        }
        for metric in result.metrics
    ]
    if metric_rows:
        st.dataframe(metric_rows, use_container_width=True, hide_index=True)
    else:
        st.info(f"Financial snapshot unavailable [{coverage.status}]: {coverage.reason}")
        for gap in coverage.data_gaps:
            st.caption(gap)

    st.subheader("Source Filings")
    filing_rows = [
        {
            "Form": filing.form,
            "Filed": filing.filing_date,
            "Report": filing.report_date,
            "Description": filing.description,
            "URL": filing.url,
        }
        for filing in result.filings[:12]
    ]
    st.dataframe(filing_rows, use_container_width=True, hide_index=True)


def render_event_workflow(result: ResearchResult) -> None:
    workflow = result.event_workflow
    st.subheader("Event Calendar + Next Source Workflow")
    st.caption(
        "This queue turns the research output into follow-up checks: upcoming filing windows, "
        "consensus seeding, source-plan requests, and monitor rules."
    )
    if not workflow.items:
        st.info("No workflow items are available yet.")
        for gap in workflow.data_gaps:
            st.caption(gap)
        return
    st.dataframe(
        [
            {
                "Priority": item.priority,
                "Type": item.item_type,
                "Title": item.title,
                "Due": item.due_date or "n/a",
                "Source": item.source,
                "Status": item.status,
                "Related idea": item.related_idea_id or "n/a",
                "Reason": item.reason,
            }
            for item in workflow.items
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_idea_factory(result: ResearchResult) -> None:
    st.subheader("Wow Filter: Changed Evidence, Stale Market Reaction")
    if result.wow_ideas:
        for idea in result.wow_ideas[:5]:
            render_idea_summary(idea)
    else:
        st.info(
            "No uncaptured or partially captured setup was detected. "
            "Connect consensus and richer price data to sharpen this filter."
        )

    st.subheader("Generated Ideas")
    if not result.ideas:
        st.warning("No trade ideas generated.")
        return
    ready = [idea for idea in result.ideas if idea.stage == "Research-Ready"]
    high = [idea for idea in result.ideas if idea.stage in {"High-Conviction", "Investable"}]
    candidates = [
        idea for idea in result.ideas
        if idea.stage not in {"Research-Ready", "High-Conviction", "Investable"}
    ]
    questions = getattr(result, "research_questions", [])
    idea_view = st.radio(
        "Generated idea view",
        [
        f"Research-Ready ({len(ready)})",
        f"High-Conviction ({len(high)})",
        f"Research Questions ({len(questions)})",
        f"Candidates ({len(candidates)})",
        ],
        horizontal=True,
        key="generated_idea_view",
    )
    if idea_view.startswith("Research-Ready"):
        if not ready:
            st.info("No idea passed the practical research-ready gate.")
        for idea in ready[:8]:
            render_idea_summary(idea)
    elif idea_view.startswith("High-Conviction"):
        if not high:
            st.info("No idea passed every high-conviction gate.")
        for idea in high[:8]:
            render_idea_summary(idea)
    elif idea_view.startswith("Research Questions"):
        if not questions:
            st.info("No research questions were generated from weak thesis chains.")
        for question in questions[:8]:
            with st.expander(f"{question.priority} | {question.title}", expanded=False):
                st.write(question.source_signal)
                st.markdown(f"**Status:** {question.status}")
                st.markdown(
                    f"**Answerability:** {question.answerability_status} "
                    f"({question.answerability_score}/100)"
                )
                st.markdown(f"**Driver:** {question.driver_name}")
                st.markdown(f"**Why it matters:** {question.why_it_matters}")
                if question.decision_rule:
                    st.write(question.decision_rule)
                if question.answerability_gaps:
                    st.markdown("**Answerability gaps**")
                    for item in question.answerability_gaps[:5]:
                        st.write(f"- {item}")
                if question.missing_links:
                    st.markdown("**Missing links**")
                    for item in question.missing_links[:5]:
                        st.write(f"- {item}")
                if question.required_evidence:
                    st.markdown("**Required evidence**")
                    for item in question.required_evidence[:5]:
                        st.write(f"- {item}")
                if question.primary_source_types:
                    st.markdown("**Primary source types**")
                    for item in question.primary_source_types[:5]:
                        st.write(f"- {item}")
                if question.next_sources:
                    st.markdown("**Next sources**")
                    for item in question.next_sources[:5]:
                        st.write(f"- {item}")
                if question.workplan_steps:
                    st.markdown("**Workplan steps**")
                    for item in question.workplan_steps[:5]:
                        st.write(f"- {item}")
                if question.acceptance_criteria:
                    st.markdown("**Acceptance criteria**")
                    for item in question.acceptance_criteria[:5]:
                        st.write(f"- {item}")
                if question.falsification_tests:
                    st.markdown("**Falsification tests**")
                    for item in question.falsification_tests[:5]:
                        st.write(f"- {item}")
    else:
        for idea in candidates[:8]:
            render_idea_summary(idea)


def _idea_capture_heading(idea) -> str:
    capture = idea.market_capture
    if not capture:
        return "Capture: not evaluated"
    if capture.capture_mode == "Unavailable":
        price_status = (capture.price_status or "price unavailable").replace("_", " ")
        return f"Capture: unavailable ({price_status})"
    return f"Capture: {capture.capture_mode.lower()}"


def render_idea_summary(idea) -> None:
    score = idea.score.total if idea.score else "n/a"
    capture = idea.market_capture.category if idea.market_capture else "Unknown"
    capture_mode = idea.market_capture.capture_mode if idea.market_capture else "Unavailable"
    capture_heading = _idea_capture_heading(idea)
    ev = expected_value(idea.scenarios)
    with st.expander(f"{idea.stage} | {idea.title} | Score {score}/100 | {capture_heading}", expanded=False):
        col1, col2, col3 = st.columns(3)
        col1.metric("Direction", idea.direction)
        col2.metric("Illustrative EV", f"{ev:+.1f}%" if ev is not None else "Unavailable")
        col3.metric("Horizon", idea.horizon)
        probability_status = idea.probability_provenance.status if idea.probability_provenance else "Uncalibrated"
        st.caption(
            f"Probability source: {probability_status}. Illustrative values are excluded from ranking until calibrated."
        )
        if idea.market_capture:
            st.caption(
                f"Market-capture mode: {capture_mode}. "
                f"{idea.market_capture.diagnosis or idea.market_capture.explanation}"
            )
        if idea.score:
            st.markdown("**Score dimensions**")
            st.dataframe(
                [{
                    "Research quality": idea.score.research_quality,
                    "Evidence strength": idea.score.evidence_strength_score,
                    "Valuation completeness": idea.score.valuation_completeness,
                    "Market-capture confidence": idea.score.market_capture_confidence,
                    "Actionability": idea.score.actionability,
                }],
                use_container_width=True,
                hide_index=True,
            )
        if idea.thesis_audit_chain:
            st.markdown("**Thesis audit chain**")
            st.write(idea.thesis_audit_chain.summary)
            st.dataframe(
                [
                    {
                        "Step": step.step,
                        "Status": step.status,
                        "Summary": step.summary,
                        "Evidence": "; ".join(step.evidence[:2]),
                        "Gaps": "; ".join(step.data_gaps[:2]),
                    }
                    for step in idea.thesis_audit_chain.steps
                ],
                use_container_width=True,
                hide_index=True,
            )
            if idea.thesis_audit_chain.next_actions:
                st.caption("Next audit actions: " + "; ".join(idea.thesis_audit_chain.next_actions[:4]))
        if idea.driver_analysis:
            st.markdown("**Possible causes**")
            st.write(idea.driver_analysis.headline)
            if idea.driver_analysis.bridge_status or idea.driver_analysis.mechanism:
                st.markdown("**Causal bridge detail**")
                st.caption(
                    f"{idea.driver_analysis.bridge_status or 'Unknown'}"
                    + (
                        f" | Driver: {idea.driver_analysis.primary_driver}"
                        if idea.driver_analysis.primary_driver else ""
                    )
                )
                if idea.driver_analysis.mechanism:
                    st.write(idea.driver_analysis.mechanism)
                bridge_rows = []
                if idea.driver_analysis.evidence_needed:
                    bridge_rows.append({
                        "Area": "Evidence needed",
                        "Items": "; ".join(idea.driver_analysis.evidence_needed[:4]),
                    })
                if idea.driver_analysis.peer_metric_checks:
                    bridge_rows.append({
                        "Area": "Peer metric checks",
                        "Items": "; ".join(idea.driver_analysis.peer_metric_checks[:4]),
                    })
                if idea.driver_analysis.falsification_tests:
                    bridge_rows.append({
                        "Area": "Falsification tests",
                        "Items": "; ".join(idea.driver_analysis.falsification_tests[:4]),
                    })
                if bridge_rows:
                    st.dataframe(bridge_rows, use_container_width=True, hide_index=True)
                if idea.driver_analysis.valuation_implication:
                    st.caption("Valuation implication: " + idea.driver_analysis.valuation_implication)
                if idea.driver_analysis.credit_implication:
                    st.caption("Credit implication: " + idea.driver_analysis.credit_implication)
                for gap in idea.driver_analysis.data_gaps[:4]:
                    st.warning(gap)
            for factor in idea.driver_analysis.factors:
                st.write(
                    f"- {factor.cause} ({factor.confidence} confidence, "
                    f"{factor.magnitude_hint}): {factor.explanation}"
                )
                for note in factor.missing_data_notes:
                    st.caption(note)
        if idea.causal_bridge_status:
            st.markdown("**Causal bridge**")
            st.write(idea.causal_bridge_status)
        if idea.equity_credit_lens:
            st.markdown("**Equity / credit lens**")
            st.write(idea.equity_credit_lens.get("equity", ""))
            st.write(idea.equity_credit_lens.get("credit", ""))
        if idea.llm_contribution:
            st.markdown("**LLM contribution**")
            st.caption(
                "; ".join(f"{key}: {value}" for key, value in idea.llm_contribution.items())
            )
        if idea.driver_attribution:
            attribution = idea.driver_attribution
            st.markdown("**Price move attribution**")
            st.write(f"{attribution.classification} ({attribution.confidence}). {attribution.headline}")
            st.caption(
                f"Raw {attribution.return_window or 'n/a'}: {_percent(attribution.raw_return_pct)}; "
                f"market-relative: {_percent(attribution.market_relative_pct)}; "
                f"sector-relative: {_percent(attribution.sector_relative_pct)}; "
                f"beta-adjusted: {_percent(attribution.beta_adjusted_pct)}."
            )
        st.write(idea.thesis)
        st.markdown(f"**Thesis-grade status:** {idea.thesis_grade_status}")
        if idea.promotion_decision:
            with st.expander("Promotion evidence audit", expanded=False):
                decision = idea.promotion_decision
                st.markdown(f"**{decision.label}**")
                st.caption(
                    f"Status: {decision.status}; substituted gate: {decision.substituted_gate or 'none'}; "
                    f"score cap: {decision.score_cap if decision.score_cap is not None else 'none'}."
                )
                for item in decision.checks:
                    st.write(f"- Passed: {item}")
                for item in decision.failed_checks:
                    st.write(f"- Failed: {item}")
                if decision.source_ids:
                    st.caption("Eligible source IDs: " + ", ".join(decision.source_ids))
        st.markdown(f"**Direction rationale:** {idea.direction_rationale or 'n/a'}")
        if idea.driver_template_summary:
            st.markdown("**Driver explanation template**")
            st.write(idea.driver_template_summary)
        if idea.normalization_status:
            st.markdown(f"**Normalization status:** {idea.normalization_status}")
        if idea.share_reconciliation:
            st.markdown("**Share reconciliation**")
            st.write(f"Status: {idea.share_reconciliation.status}; basis: {idea.share_reconciliation.basis}")
            if idea.share_reconciliation.adr_ratio:
                st.caption(f"ADR ratio: {idea.share_reconciliation.adr_ratio}")
            for gap in idea.share_reconciliation.data_gaps:
                st.caption(gap)
        if idea.source_events:
            event = idea.source_events[0]
            changed_text = event.metrics.get("changed_text") or event.metrics.get("supporting_quote")
            if changed_text:
                st.markdown("**What exactly changed**")
                st.write(changed_text)
            if event.metrics.get("not_thesis_grade_reason"):
                st.warning(str(event.metrics["not_thesis_grade_reason"]))
        st.markdown(f"**Variant perception:** {idea.variant_perception}")
        if idea.market_capture:
            st.markdown("**Market capture diagnosis**")
            st.caption(f"Capture mode: {idea.market_capture.capture_mode}; category: {capture}.")
            st.write(idea.market_capture.diagnosis or idea.market_capture.explanation)
            st.caption(
                f"Price status: {idea.market_capture.price_status}; "
                f"consensus status: {idea.market_capture.consensus_status}."
            )
            for item in idea.market_capture.required_inputs[:5]:
                st.write(f"- {item}")
            if idea.market_capture.point_in_time_note:
                st.caption(idea.market_capture.point_in_time_note)
        st.markdown(f"**Catalyst:** {idea.catalyst}")
        st.markdown(f"**Strongest counter-thesis:** {idea.strongest_counter_thesis}")
        st.markdown(f"**Next source to check:** {idea.next_source_to_check or 'n/a'}")
        if idea.gate_result and idea.gate_result.research_ready_failed:
            st.markdown("**Research-ready gaps**")
            for failure in idea.gate_result.research_ready_failed:
                st.write(f"- {failure}")
        if idea.gate_result and idea.gate_result.high_conviction_failed:
            st.markdown("**High-conviction gaps**")
            for failure in idea.gate_result.high_conviction_failed:
                st.write(f"- {failure}")
        if idea.peer_readthrough:
            st.markdown("**Direct peer checks**")
            st.dataframe(
                [
                    {
                        "Peer": readthrough.peer_ticker,
                        "Evidence": readthrough.evidence_status,
                        "Relation": readthrough.relation,
                        "Price reaction": (
                            f"{readthrough.price_reaction_pct:+.1f}%"
                            if readthrough.price_reaction_pct is not None
                            else "n/a"
                        ),
                        "Provider": (
                            readthrough.sympathy_reaction.source
                            if readthrough.sympathy_reaction else "n/a"
                        ),
                        "Status": (
                            readthrough.sympathy_reaction.status
                            if readthrough.sympathy_reaction else readthrough.failure_status or ""
                        ),
                        "Anchor": (
                            readthrough.sympathy_reaction.anchor_date or "pending"
                            if readthrough.sympathy_reaction else "n/a"
                        ),
                        "1d / 5d / 20d": (
                            " / ".join(
                                (
                                    "pending"
                                    if readthrough.sympathy_reaction.status == "window_pending"
                                    else "n/a"
                                )
                                if readthrough.sympathy_reaction.raw_returns.get(window) is None
                                else f"{readthrough.sympathy_reaction.raw_returns[window]:+.1f}%"
                                for window in ("1d", "5d", "20d")
                            )
                            if readthrough.sympathy_reaction else "n/a"
                        ),
                        "Reason": (
                            readthrough.failure_reason
                            or (readthrough.sympathy_reaction.reason if readthrough.sympathy_reaction else "")
                        ),
                        "Key changes": "; ".join(readthrough.key_metric_changes),
                        "Conclusion": readthrough.conclusion,
                    }
                    for readthrough in idea.peer_readthrough
                ],
                use_container_width=True,
                hide_index=True,
            )
        if idea.peer_metric_summary:
            summary = idea.peer_metric_summary
            st.markdown("**Peer metric readiness**")
            summary_cols = st.columns(5)
            summary_cols[0].metric("Status", summary.status)
            summary_cols[1].metric("Score", f"{summary.score}/100")
            summary_cols[2].metric("Operating peers", f"{summary.operating_metric_peers}/{summary.total_peers}")
            summary_cols[3].metric("Stale", summary.stale_metric_peers)
            summary_cols[4].metric("Price-only", summary.price_only_peers)
            st.caption(summary.summary)
            if summary.stage_impact:
                st.caption(summary.stage_impact)
            if summary.confirmations:
                st.write("Confirming peer metric evidence: " + "; ".join(summary.confirmations))
            if summary.contradictions:
                st.write("Contradicting peer metric evidence: " + "; ".join(summary.contradictions))
            for gap in summary.data_gaps:
                st.warning(gap)
            for action in summary.next_actions:
                st.write(f"- {action}")
        if idea.peer_metric_readthrough:
            st.markdown("**Peer metric read-through**")
            st.dataframe(
                [
                    {
                        "Peer": readthrough.peer_ticker,
                        "Metric family": readthrough.metric_family,
                        "Status": readthrough.status,
                        "Relation": readthrough.relation,
                        "Fiscal alignment": readthrough.fiscal_alignment,
                        "Present metrics": "; ".join(readthrough.present_metrics),
                        "Missing metrics": "; ".join(readthrough.missing_metrics),
                        "Summary": readthrough.summary,
                        "Acceptance": "; ".join(readthrough.acceptance_criteria[:2]),
                        "Falsifiers": "; ".join(readthrough.falsification_tests[:2]),
                        "Gaps": "; ".join(readthrough.data_gaps),
                    }
                    for readthrough in idea.peer_metric_readthrough
                ],
                use_container_width=True,
                hide_index=True,
            )
        if idea.global_peer_coverage:
            with st.expander("Global peer coverage"):
                st.dataframe(
                    [
                        {
                            "Peer": coverage.ticker,
                            "Status": coverage.status,
                            "Documents": len(coverage.documents),
                            "Metrics": len(coverage.metrics),
                            "Gaps": "; ".join(coverage.data_gaps),
                        }
                        for coverage in idea.global_peer_coverage
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
        if idea.citations:
            st.markdown("**Source evidence**")
            for citation in idea.citations[:3]:
                st.write(f"- [{citation.source}]({citation.url}) - {citation.snippet or citation.section}")


def render_price_move_attribution(result: ResearchResult) -> None:
    st.subheader("Price Move Attribution")
    rows = []
    waterfall_rows = []
    factor_context_rows = []
    positioning_rows = []
    liquidity_rows = []
    options_rows = []
    factor_rows = []
    audit_rows = []
    quality_rows = []
    for idea in result.ideas:
        attribution = idea.driver_attribution
        if not attribution:
            continue
        rows.append({
            "Idea": idea.title,
            "Class": attribution.classification,
            "Confidence": attribution.confidence,
            "Readiness": attribution.attribution_readiness,
            "Window": attribution.return_window or "n/a",
            "Raw": _percent(attribution.raw_return_pct),
            "Market-relative": _percent(attribution.market_relative_pct),
            "Sector-relative": _percent(attribution.sector_relative_pct),
            "Beta-adjusted": _percent(attribution.beta_adjusted_pct),
            "Peer sympathy": _percent(attribution.peer_sympathy_pct),
            "Consensus revision": _percent(attribution.consensus_revision_pct),
            "Narrative": attribution.narrative_saturation,
            "Quality score": f"{attribution.attribution_quality_score}/100",
            "Summary": "; ".join(attribution.attribution_summary[:4]),
            "Residual": attribution.residual_explanation,
        })
        for item in attribution.attribution_quality:
            quality_rows.append({
                "Idea": idea.title,
                "Area": item.area,
                "Status": item.status,
                "Score": item.score,
                "Summary": item.summary,
                "Evidence": "; ".join(item.evidence[:3]),
                "Gaps": "; ".join(item.gaps[:3]),
                "Next action": item.next_action,
                "Stage impact": item.stage_impact,
            })
        for item in attribution.classification_evidence:
            audit_rows.append({
                "Idea": idea.title,
                "Audit area": "Classification evidence",
                "Item": item,
            })
        for item in attribution.falsification_tests:
            audit_rows.append({
                "Idea": idea.title,
                "Audit area": "Falsification test",
                "Item": item,
            })
        for item in attribution.next_attribution_checks:
            audit_rows.append({
                "Idea": idea.title,
                "Audit area": "Next attribution check",
                "Item": item,
            })
        if attribution.waterfall:
            waterfall = attribution.waterfall
            waterfall_rows.append({
                "Idea": idea.title,
                "Component": "Residual company-specific move",
                "Type": "residual",
                "Contribution": _percent(waterfall.residual_pct),
                "Confidence": attribution.confidence,
                "Source": "Waterfall balance",
                "Why": attribution.residual_explanation,
            })
            for component in waterfall.components:
                waterfall_rows.append({
                    "Idea": idea.title,
                    "Component": component.label,
                    "Type": component.component_type,
                    "Contribution": _percent(component.contribution_pct),
                    "Confidence": component.confidence,
                    "Source": component.source,
                    "Why": component.explanation,
                })
        for exposure in attribution.factor_context:
            factor_context_rows.append({
                "Idea": idea.title,
                "Factor": exposure.factor_name,
                "Return": _percent(exposure.factor_return_pct),
                "Beta": exposure.beta if exposure.beta is not None else "n/a",
                "Contribution": _percent(exposure.contribution_pct),
                "Window": exposure.window or "n/a",
                "Confidence": exposure.confidence,
                "As of": exposure.source_as_of or "n/a",
                "Source": exposure.source,
            })
        for item in attribution.positioning_context:
            positioning_rows.append({
                "Idea": idea.title,
                "Provider": item.provider,
                "Signal": item.label,
                "Value": item.value if item.value is not None else "n/a",
                "Direction": item.direction,
                "Confidence": item.confidence,
                "As of": item.source_as_of or "n/a",
                "Summary": item.summary,
            })
        for item in attribution.liquidity_context:
            liquidity_rows.append({
                "Idea": idea.title,
                "Signal": item.label,
                "Value": item.value if item.value is not None else "n/a",
                "Direction": item.direction,
                "Confidence": item.confidence,
                "Source": item.source,
                "Summary": item.summary,
            })
        for item in attribution.options_context:
            options_rows.append({
                "Idea": idea.title,
                "Provider": item.provider,
                "Status": item.status,
                "Implied move": _percent(item.implied_move_pct),
                "IV change": _percent(item.implied_volatility_change_pct),
                "Skew": item.skew_signal,
                "Confidence": item.confidence,
                "Summary": item.summary,
            })
        for factor in attribution.factors:
            factor_rows.append({
                "Idea": idea.title,
                "Driver": factor.label,
                "Type": factor.driver_type,
                "Direction": factor.direction,
                "Confidence": factor.confidence,
                "Magnitude": _percent(factor.magnitude_pct),
                "Tier": factor.source_tier,
                "Why": factor.explanation,
                "Disconfirm if": factor.disconfirming_evidence,
            })
    if rows:
        st.markdown("#### Event Return Decomposition")
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("No price move attribution is available yet. Check event-window price health and detected events.")
    if factor_rows:
        st.markdown("#### Likely Driver Factors")
        st.dataframe(factor_rows, use_container_width=True, hide_index=True)
    if quality_rows:
        st.markdown("#### Attribution Quality Checklist")
        st.dataframe(quality_rows, use_container_width=True, hide_index=True)
    if audit_rows:
        st.markdown("#### Attribution Audit Trail")
        st.dataframe(audit_rows, use_container_width=True, hide_index=True)
    if waterfall_rows:
        st.markdown("#### Attribution Waterfall")
        st.dataframe(waterfall_rows, use_container_width=True, hide_index=True)
    if factor_context_rows:
        st.markdown("#### Style / Factor Context")
        st.dataframe(factor_context_rows, use_container_width=True, hide_index=True)
    if positioning_rows or liquidity_rows or options_rows:
        st.markdown("#### Positioning, Liquidity, and Options")
        if positioning_rows:
            st.dataframe(positioning_rows, use_container_width=True, hide_index=True)
        if liquidity_rows:
            st.dataframe(liquidity_rows, use_container_width=True, hide_index=True)
        if options_rows:
            st.dataframe(options_rows, use_container_width=True, hide_index=True)
    evidence = result.external_evidence
    macro_items = [item for item in evidence.evidence if item.source_type in {"macro_factor", "china_macro"}]
    official_macro_rows = [
        {
            "Provider": item.provider,
            "Series": item.metric_name or "n/a",
            "Title": item.title,
            "As of": item.source_as_of or "n/a",
            "Release": item.release_date or "n/a",
            "Vintage": item.vintage_date or "n/a",
            "Lookahead-safe": item.lookahead_safe,
            "Frequency": item.frequency or "n/a",
            "Change": item.metric_value,
            "Unit": item.unit or "n/a",
            "Direction": item.direction,
            "Summary": item.summary,
        }
        for item in macro_items
        if item.provider not in {"World Bank macro", "IMF macro"} and item.source_type != "china_macro"
    ]
    global_macro_rows = [
        {
            "Provider": item.provider,
            "Series": item.metric_name or "n/a",
            "Title": item.title,
            "As of": item.source_as_of or "n/a",
            "Lookahead-safe": item.lookahead_safe,
            "Change": item.metric_value,
            "Unit": item.unit or "n/a",
            "Summary": item.summary,
        }
        for item in macro_items
        if item.provider in {"World Bank macro", "IMF macro"} or item.source_type == "china_macro"
    ]
    narrative_rows = [
        {
            "Provider": item.provider,
            "Signal": item.title,
            "Tier": item.source_tier,
            "Score": item.metric_value,
            "As of": item.source_as_of or "n/a",
            "Summary": item.summary,
        }
        for item in evidence.evidence
        if item.source_type == "narrative_saturation"
    ]
    analyst_context_rows = [
        {
            "Provider": item.provider,
            "Type": item.source_type,
            "Title": item.title,
            "Tier": item.source_tier,
            "As of": item.source_as_of or "n/a",
            "Language": next((tag for tag in item.tags if tag in {"en", "zh"}), "n/a"),
            "Summary": item.summary,
            "High-conviction role": "Context only" if item.disqualifies_high_conviction else "Can support",
        }
        for item in evidence.evidence
        if item.source_type in {"external_analyst_context", "management_transcript_context", "external_market_context"}
    ]
    if official_macro_rows:
        st.markdown("#### Official Macro Context")
        st.dataframe(official_macro_rows, use_container_width=True, hide_index=True)
    if global_macro_rows:
        st.markdown("#### Global / ADR Macro Context")
        st.dataframe(global_macro_rows, use_container_width=True, hide_index=True)
    if narrative_rows:
        st.markdown("#### Narrative Saturation")
        st.dataframe(narrative_rows, use_container_width=True, hide_index=True)
    render_wisburg_research_lens(result)
    if analyst_context_rows:
        st.markdown("#### External Analyst Context")
        st.caption(
            "Wisburg and similar third-party research can sharpen counter-theses and narrative awareness, "
            "but cannot independently create High-Conviction ideas."
        )
        st.dataframe(analyst_context_rows, use_container_width=True, hide_index=True)
    st.markdown("#### External Evidence Sources")
    st.caption(
        f"Status: {evidence.status}. Evidence items: {len(evidence.evidence)}. "
        "Narrative and third-party evidence are supporting context only."
    )
    if evidence.provider_statuses:
        st.dataframe(
            [
                {
                    "Provider": status.provider,
                    "Status": status.status,
                    "Official": status.official,
                    "Entitlement": status.entitlement_status,
                    "Observed": status.observed_at,
                    "Message": status.message,
                }
                for status in evidence.provider_statuses
            ],
            use_container_width=True,
            hide_index=True,
        )
    if evidence.evidence:
        st.dataframe(
            [
                {
                    "Provider": item.provider,
                    "Type": item.source_type,
                    "Title": item.title,
                    "Tier": item.source_tier,
                    "Confidence": item.confidence,
                    "As of": item.source_as_of or "n/a",
                    "Metric": item.metric_name or "n/a",
                    "Value": item.metric_value,
                    "Summary": item.summary,
                }
                for item in evidence.evidence
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_idea_scorer(result: ResearchResult) -> None:
    st.subheader("Idea Quality Scores")
    render_valuation(result)
    if not result.ideas:
        st.warning(
            "No ideas are available to score. This usually means no supported filings, "
            "sections, or financial metrics were extracted for the ticker."
        )
        return

    rows = []
    for idea in result.ideas:
        score = idea.score
        capture = idea.market_capture
        if not score:
            continue
        rows.append(
            {
                "Idea": idea.title,
                "Total": score.total,
                "Research Quality": score.research_quality,
                "Evidence Strength Score": score.evidence_strength_score,
                "Valuation Completeness": score.valuation_completeness,
                "Market-Capture Confidence": score.market_capture_confidence,
                "Actionability": score.actionability,
                "Evidence": score.evidence_strength,
                "Novelty": score.novelty,
                "Valuation / Payoff": score.valuation_payoff,
                "Thesis Specificity": score.thesis_specificity,
                "Catalyst Timing": score.catalyst_timing,
                "Market Capture": score.market_capture,
                "Reproducibility": score.reproducibility,
                "Stage": idea.stage,
                "Capture Class": capture.category if capture else "Unknown",
                "Price Reaction": (
                    f"{capture.price_reaction_pct:+.1f}%"
                    if capture and capture.price_reaction_pct is not None
                    else "n/a"
                ),
                "Consensus Revision": (
                    f"{capture.consensus_revision_pct:+.1f}%"
                    if capture and capture.consensus_revision_pct is not None
                    else "Not connected"
                ),
                "Price Status": capture.price_status if capture else "unknown",
                "Consensus Status": capture.consensus_status if capture else "unknown",
                "Capture Diagnosis": capture.diagnosis if capture else "No market-capture object attached.",
            }
        )
    st.dataframe(rows, use_container_width=True, hide_index=True)
    quality_rows = _management_signal_quality_rows(result.ideas)
    if quality_rows:
        st.markdown("#### Management Signal Quality")
        st.dataframe(quality_rows, use_container_width=True, hide_index=True)

    selected = st.selectbox("Scenario simulator", [idea.title for idea in result.ideas])
    idea = next(item for item in result.ideas if item.title == selected)
    st.markdown("#### Scenario + Payoff")
    if idea.payoff_model:
        col_status, col_probability, col_rank = st.columns(3)
        completeness = idea.payoff_model.payoff_completeness
        col_status.metric(
            "Payoff completeness",
            completeness.status if completeness else idea.payoff_model.status,
        )
        col_probability.metric(
            "Probability source",
            idea.payoff_model.probability_provenance.status
            if idea.payoff_model.probability_provenance else "Uncalibrated",
        )
        col_rank.metric("Rank eligible", "Yes" if idea.payoff_model.rank_eligible else "No")
        for gap in idea.payoff_model.data_gaps:
            st.caption(gap)
        render_payoff_assumption_inputs(
            idea,
            result.valuation,
            getattr(result, "recent_market_context", None),
        )
    st.caption(
        "Scenario labels describe the stock outcome. Position payoff applies the idea direction, "
        "so a lower stock exit is profitable for a Short after borrow, dividends, and transaction costs."
        if idea.direction == "Short"
        else "Scenario labels describe the stock outcome; position payoff applies dividends and transaction costs."
    )
    cols = st.columns(3)
    for idx, scenario in enumerate(idea.scenarios):
        with cols[idx]:
            st.markdown(f"**{scenario.name}**")
            st.metric("Probability", f"{scenario.probability * 100:.1f}%")
            st.metric("Entry", f"{scenario.entry_value:.2f}" if scenario.entry_value is not None else "n/a")
            st.metric("Exit value", f"{scenario.exit_value:.2f}" if scenario.exit_value is not None else "n/a")
            stock_move = _stock_outcome_return(scenario.entry_value, scenario.exit_value)
            st.metric("Stock move", f"{stock_move:+.1f}%" if stock_move is not None else "Incomplete")
            payoff = scenario.net_return_pct
            st.metric("Position payoff", f"{payoff:+.1f}%" if payoff is not None else "Incomplete")
    normalized_ev = idea.payoff_model.expected_value_pct if idea.payoff_model else expected_value(idea.scenarios)
    status = idea.probability_provenance.status if idea.probability_provenance else "Uncalibrated"
    st.metric(
        f"Illustrative expected value ({status})",
        f"{normalized_ev:+.1f}%" if normalized_ev is not None else "Unavailable",
    )


def render_payoff_assumption_inputs(idea, valuation, market_context=None) -> None:
    payoff = idea.payoff_model
    if not payoff:
        return
    existing = getattr(idea, "user_assumptions", {}) or {}
    with st.expander("Assumption inputs"):
        st.caption(
            "Sensible defaults calculate immediately. Market prices remain sourced facts; model, market-range, "
            "illustrative, and custom inputs are labelled separately and do not override evidence or conviction gates."
        )
        mode_options = ["Automatic", "Model-derived", "Market-implied", "Illustrative", "Custom"]
        saved_mode = str(existing.get("assumption_mode") or "Model-derived")
        if saved_mode not in mode_options:
            saved_mode = "Model-derived"
        selected_mode = st.radio(
            "Scenario basis",
            mode_options,
            index=mode_options.index(saved_mode),
            horizontal=True,
            key=f"scenario_mode_{idea.idea_id}",
            help=(
                "Model-derived is the default and uses internal fair-value cases. If those are unavailable, the app "
                "shows a labelled market-range or illustrative fallback. Select Custom to edit exit anchors."
            ),
        )
        market_volatility = getattr(market_context, "annualized_volatility_pct", None)
        price_source = getattr(market_context, "source", "") or payoff.entry_price_source
        price_as_of = getattr(market_context, "price_as_of", None) or payoff.entry_price_as_of
        preset = build_payoff_model(
            idea,
            valuation,
            payoff.entry_price,
            scenario_mode=selected_mode,
            annualized_volatility_pct=market_volatility,
            entry_price_source=price_source,
            entry_price_as_of=price_as_of,
        )
        scenario_by_name = {scenario.name: scenario for scenario in preset.scenarios}
        st.caption(
            f"Selected basis: {preset.assumption_mode} ({preset.assumption_quality} confidence). "
            f"Entry source: {price_source or 'Unknown'}; as of {price_as_of or 'Unknown'}."
        )
        for limitation in preset.limitations[:2]:
            st.caption(f"Limitation: {limitation}")
        use_saved_values = selected_mode == saved_mode
        with st.form(f"payoff_assumptions_{idea.idea_id}_{selected_mode}"):
            entry_default = _existing_assumption_number(
                existing if use_saved_values else {},
                "entry_price",
                preset.entry_price or payoff.entry_price or 0.0,
            )
            entry_price = st.number_input(
                "Current entry price",
                min_value=0.0,
                value=entry_default,
                step=1.0,
                help="Use the price you would underwrite as the entry point for the idea.",
            )
            cols = st.columns(3)
            exits = {}
            exits_editable = selected_mode == "Custom"
            for idx, name in enumerate(("Bear", "Base", "Bull")):
                scenario = scenario_by_name.get(name)
                default = existing.get(f"{name.lower()}_exit") if use_saved_values else None
                if default is None and scenario and scenario.exit_value is not None:
                    default = scenario.exit_value
                exits[f"{name.lower()}_exit"] = cols[idx].number_input(
                    f"{name} exit anchor",
                    min_value=0.0,
                    value=float(default or 0.0),
                    step=1.0,
                    disabled=not exits_editable,
                    help="Exit price or fair-value anchor for this scenario.",
                )
            probability_cols = st.columns(3)
            probabilities = {}
            for idx, name in enumerate(("Bear", "Base", "Bull")):
                scenario = scenario_by_name.get(name)
                default_probability = (
                    existing.get(f"{name.lower()}_probability_pct")
                    if use_saved_values else None
                )
                if default_probability is None and scenario:
                    default_probability = scenario.probability * 100
                probabilities[f"{name.lower()}_probability_pct"] = probability_cols[idx].number_input(
                    f"{name} probability %",
                    min_value=0.0,
                    max_value=100.0,
                    value=float(default_probability if default_probability is not None else {"Bear": 25, "Base": 50, "Bull": 25}[name]),
                    step=5.0,
                    help="Illustrative probability. The app normalizes all three values to 100%.",
                )
            cost_cols = st.columns(4)
            transaction_cost_pct = cost_cols[0].number_input(
                "Transaction cost %",
                min_value=0.0,
                value=_existing_assumption_number(existing if use_saved_values else {}, "transaction_cost_pct", preset.transaction_cost_pct or 0.10),
                step=0.05,
            )
            dividend_return_pct = cost_cols[1].number_input(
                "Dividend return %",
                value=_existing_assumption_number(existing if use_saved_values else {}, "dividend_return_pct", preset.dividend_return_pct or 0.0),
                step=0.10,
            )
            borrow_cost_pct = cost_cols[2].number_input(
                "Borrow cost %",
                min_value=0.0,
                value=_existing_assumption_number(existing if use_saved_values else {}, "borrow_cost_pct", preset.borrow_cost_pct or 0.0),
                step=0.25,
                help="Relevant for short ideas.",
            )
            hedge_ratio = cost_cols[3].number_input(
                "Hedge ratio",
                min_value=0.0,
                value=_existing_assumption_number(existing if use_saved_values else {}, "hedge_ratio", preset.hedge_ratio or 0.0),
                step=0.05,
                help="Relevant for relative-value ideas.",
            )
            note = st.text_area(
                "Assumption note",
                value=str(existing.get("note") or "") if use_saved_values else "",
                placeholder="Example: Base exit uses 18x next-year EPS; bear assumes margin reversion.",
            )
            preview_rows = _payoff_assumption_preview(
                idea.direction,
                entry_price,
                exits,
                transaction_cost_pct,
                dividend_return_pct,
                borrow_cost_pct,
                hedge_ratio,
                probabilities,
            )
            st.dataframe(preview_rows, hide_index=True, use_container_width=True)
            action_cols = st.columns(2)
            submitted = action_cols[0].form_submit_button("Save Assumptions", use_container_width=True)
            reset_defaults = action_cols[1].form_submit_button("Reset to Model-derived", use_container_width=True)
        if reset_defaults:
            ResearchStore().update_idea_assumptions(idea.idea_id, {})
            idea.user_assumptions = {}
            idea.payoff_model = build_payoff_model(
                idea,
                valuation,
                payoff.entry_price,
                scenario_mode="Model-derived",
                annualized_volatility_pct=market_volatility,
                entry_price_source=price_source,
                entry_price_as_of=price_as_of,
            )
            idea.scenarios = idea.payoff_model.scenarios
            idea.probability_provenance = idea.payoff_model.probability_provenance
            st.success("Defaults restored. Model-derived is active, with labelled fallbacks only when the model is unavailable.")
        elif submitted:
            if sum(probabilities.values()) <= 0:
                st.error("At least one scenario probability must be greater than zero.")
                return
            payload = {
                "assumption_mode": selected_mode,
                "entry_price": entry_price,
                **probabilities,
                "transaction_cost_pct": transaction_cost_pct,
                "dividend_return_pct": dividend_return_pct,
                "borrow_cost_pct": borrow_cost_pct,
                "hedge_ratio": hedge_ratio,
                "note": note.strip(),
            }
            if selected_mode == "Custom":
                payload.update(exits)
            saved = ResearchStore().update_idea_assumptions(idea.idea_id, payload)
            idea.user_assumptions = payload
            probability_values = {
                name.title(): probabilities[f"{name}_probability_pct"] / 100
                for name in ("bear", "base", "bull")
            }
            idea.payoff_model = build_payoff_model(
                idea,
                valuation,
                entry_price,
                borrow_cost_pct=borrow_cost_pct,
                transaction_cost_pct=transaction_cost_pct,
                dividend_return_pct=dividend_return_pct,
                hedge_ratio=hedge_ratio or None,
                scenario_exit_values=(
                    {name.replace("_exit", ""): value for name, value in exits.items()}
                    if selected_mode == "Custom" else None
                ),
                scenario_probabilities=probability_values,
                scenario_mode=selected_mode,
                annualized_volatility_pct=market_volatility,
                entry_price_source=price_source,
                entry_price_as_of=price_as_of,
            )
            idea.scenarios = idea.payoff_model.scenarios
            idea.probability_provenance = idea.payoff_model.probability_provenance
            if saved is None:
                st.warning("Assumptions were captured for this session, but the idea version is not saved in SQLite yet. Run research once more to persist it.")
                st.session_state[f"user_assumptions_{idea.idea_id}"] = payload
            else:
                st.success(
                    "Assumptions saved and the illustrative payoff was recalculated. "
                    "The probabilities remain uncalibrated and excluded from EV ranking."
                )


def _existing_assumption_number(existing: dict, key: str, fallback: float) -> float:
    value = existing.get(key)
    if value in (None, ""):
        return float(fallback or 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback or 0.0)


def _payoff_assumption_preview(
    direction: str,
    entry_price: float,
    exits: dict[str, float],
    transaction_cost_pct: float,
    dividend_return_pct: float,
    borrow_cost_pct: float,
    hedge_ratio: float,
    probability_inputs: dict[str, float],
) -> list[dict[str, str]]:
    raw_probabilities = {
        key: max(0.0, probability_inputs.get(key.replace("_exit", "_probability_pct"), 0.0))
        for key in ("bear_exit", "base_exit", "bull_exit")
    }
    probability_total = sum(raw_probabilities.values())
    probabilities = {
        key: value / probability_total for key, value in raw_probabilities.items()
    } if probability_total > 0 else {"bear_exit": 0.25, "base_exit": 0.50, "bull_exit": 0.25}
    rows: list[dict[str, str]] = []
    ev_terms: list[float] = []
    for key, probability in probabilities.items():
        exit_value = exits.get(key)
        gross = _preview_directional_return(direction, entry_price, exit_value)
        net = None
        if gross is not None and direction == "Long":
            net = gross + dividend_return_pct - transaction_cost_pct
        elif gross is not None and direction == "Short":
            net = gross - borrow_cost_pct - dividend_return_pct - transaction_cost_pct
        elif gross is not None and direction == "Relative Value" and hedge_ratio > 0:
            net = gross - transaction_cost_pct
        if net is not None:
            ev_terms.append(probability * net)
        rows.append({
            "Scenario": key.replace("_exit", "").title(),
            "Probability": f"{probability * 100:.0f}%",
            "Entry": f"{entry_price:.2f}" if entry_price > 0 else "Missing",
            "Exit": f"{exit_value:.2f}" if exit_value and exit_value > 0 else "Missing",
            "Stock move": f"{_preview_stock_return(entry_price, exit_value):+.1f}%" if _preview_stock_return(entry_price, exit_value) is not None else "Incomplete",
            "Position before costs": f"{gross:+.1f}%" if gross is not None else "Incomplete",
            "Net position payoff": f"{net:+.1f}%" if net is not None else "Incomplete",
        })
    rows.append({
        "Scenario": "Illustrative EV",
        "Probability": "/".join(f"{probabilities[key] * 100:.0f}" for key in ("bear_exit", "base_exit", "bull_exit")),
        "Entry": "",
        "Exit": "",
        "Stock move": "",
        "Position before costs": "",
        "Net position payoff": f"{sum(ev_terms):+.1f}%" if len(ev_terms) == 3 else "Incomplete",
    })
    return rows


def _stock_outcome_return(entry_value: float | None, exit_value: float | None) -> float | None:
    if entry_value is None or exit_value is None or entry_value <= 0 or exit_value <= 0:
        return None
    return (exit_value - entry_value) / entry_value * 100


def _preview_stock_return(entry_price: float, exit_value: float | None) -> float | None:
    if entry_price <= 0 or not exit_value or exit_value <= 0:
        return None
    return (exit_value - entry_price) / entry_price * 100


def _preview_directional_return(
    direction: str,
    entry_price: float,
    exit_value: float | None,
) -> float | None:
    if entry_price <= 0 or not exit_value or exit_value <= 0:
        return None
    if direction == "Long":
        return (exit_value - entry_price) / entry_price * 100
    if direction == "Short":
        return (entry_price - exit_value) / entry_price * 100
    if direction == "Relative Value":
        return (exit_value - entry_price) / entry_price * 100
    return None


def render_management_sources(result: ResearchResult) -> None:
    st.subheader("Management Sources")
    package = result.management_sources
    col_status, col_docs, col_claims, col_checks = st.columns(4)
    col_status.metric("Status", package.status)
    col_docs.metric("Documents", len(package.documents))
    col_claims.metric("Claims", len(package.claims))
    col_checks.metric("Cross-checks", len(package.cross_checks))
    for gap in package.data_gaps:
        st.caption(gap)

    st.markdown("**Documents**")
    st.dataframe(
        [
            {
                "Type": doc.source_type,
                "Provider": doc.provider,
                "Tier": doc.source_tier,
                "Date": doc.event_date,
                "Title": doc.title,
                "Raw policy": doc.raw_payload_policy,
            }
            for doc in package.documents
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Management Claims**")
    st.dataframe(
        [
            {
                "Status": claim.status,
                "Type": claim.claim_type,
                "Source": claim.source_type,
                "Tier": claim.source_tier,
                "Speaker": claim.speaker or "n/a",
                "Sentiment": claim.sentiment_label or "n/a",
                "Score": claim.sentiment_score,
                "Specificity": claim.specificity_score,
                "Evasion": ", ".join(claim.evasion_terms) or "n/a",
                "Statement": claim.statement,
            }
            for claim in package.claims
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Cross-Checks**")
    st.dataframe(
        [
            {
                "Status": check.status,
                "Type": check.check_type,
                "Source": check.source_type,
                "Tier": check.source_tier,
                "Materiality": check.materiality,
                "Summary": check.summary,
            }
            for check in package.cross_checks
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Meeting / Proxy Events**")
    st.dataframe(
        [
            {
                "Type": event.event_type,
                "Status": event.status,
                "Date": event.event_date,
                "Description": event.description,
            }
            for event in package.meeting_events
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Transcript Turns**")
    st.dataframe(
        [
            {
                "Speaker": turn.speaker,
                "Section": turn.section,
                "Sentiment": turn.sentiment_label or turn.sentiment or "n/a",
                "Score": turn.sentiment_score,
                "Confidence": turn.sentiment_confidence or "n/a",
                "Specificity": turn.specificity_score,
                "Evasion": ", ".join(turn.evasion_terms) or "n/a",
                "Uncertainty": ", ".join(turn.uncertainty_terms) or "n/a",
                "Text": turn.text,
            }
            for turn in package.transcript_turns[:40]
        ],
        use_container_width=True,
        hide_index=True,
    )


def render_thesis_monitor(result: ResearchResult) -> None:
    store = IdeaMemoryStore()
    research_store = ResearchStore()
    st.subheader("Watchlist")
    current_watchlist = research_store.list_watchlist()
    watchlisted = any(item.ticker == result.identity.ticker for item in current_watchlist)
    watch_col1, watch_col2 = st.columns(2)
    if not watchlisted:
        if watch_col1.button("Add To Default Watchlist", use_container_width=True):
            research_store.add_watchlist(result.identity.ticker)
            st.success(f"{result.identity.ticker} added to the daily snapshot watchlist.")
            st.rerun()
    else:
        watch_col1.success(f"{result.identity.ticker} is on the default watchlist.")
        if watch_col2.button("Remove From Watchlist", use_container_width=True):
            research_store.remove_watchlist(result.identity.ticker)
            st.rerun()
    watchlist = research_store.list_watchlist()
    if watchlist:
        st.dataframe(
            [
                {
                    "Ticker": item.ticker,
                    "Last Snapshot": item.last_snapshot_at or "Not yet collected",
                }
                for item in watchlist
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.subheader("Daily Research Snapshot")
    daily_status = research_store.latest_daily_snapshot_status(result.identity.ticker)
    if daily_status:
        daily_cols = st.columns(4)
        daily_cols[0].metric("Overall", daily_status.get("overall_status", "Unknown"))
        daily_cols[1].metric("Consensus", daily_status.get("consensus_status", "Unknown"))
        daily_cols[2].metric("Prices", daily_status.get("price_status", "Unknown"))
        daily_cols[3].metric("Wisburg", daily_status.get("wisburg_status", "Unknown"))
        st.caption(
            f"Run date: {daily_status.get('run_date', 'Unknown')}; "
            f"alerts created: {daily_status.get('alerts_created', 0)}; "
            f"same-day Wisburg cache: {'reused' if daily_status.get('used_same_day_wisburg_cache') else 'not reused'}."
        )
        if daily_status.get("data_gaps"):
            with st.expander("Daily snapshot gaps", expanded=False):
                for gap in daily_status["data_gaps"]:
                    st.write(f"- {gap}")
    else:
        st.info(
            "No daily snapshot has run for this ticker. Add it to the watchlist and run the scheduled collector; "
            "the dashboard does not need to stay open."
        )

    st.subheader("Alert Inbox")
    alerts = research_store.list_alerts(limit=50)
    if alerts:
        st.dataframe(
            [
                {
                    "ID": alert.alert_id,
                    "Ticker": alert.ticker,
                    "Severity": alert.severity,
                    "Status": alert.status,
                    "Alert": alert.title,
                    "Created": alert.created_at,
                }
                for alert in alerts
            ],
            use_container_width=True,
            hide_index=True,
        )
        selected_alert = st.selectbox(
            "Alert action",
            alerts,
            format_func=lambda alert: f"#{alert.alert_id} {alert.ticker} - {alert.title}",
        )
        action_col1, action_col2 = st.columns(2)
        if action_col1.button("Mark Read", use_container_width=True):
            research_store.update_alert_status(selected_alert.alert_id, "read")
            st.rerun()
        if action_col2.button("Dismiss", use_container_width=True):
            research_store.update_alert_status(selected_alert.alert_id, "dismissed")
            st.rerun()
    else:
        st.info("No alerts yet. Daily consensus, price, and external-research snapshots accumulate monitor history.")

    st.subheader("Continuous Thesis Monitor")
    if not result.ideas:
        st.warning("No ideas available for monitoring.")
        return

    selected_title = st.selectbox("Idea to monitor", [idea.title for idea in result.ideas])
    idea = next(item for item in result.ideas if item.title == selected_title)
    for item in idea.monitor_items:
        with st.container(border=True):
            st.markdown(f"**{item.criterion}**")
            st.write(f"Source: {item.data_source}")
            st.write(f"Cadence: {item.cadence}")
            st.write(f"Confirm: {item.confirm_trigger}")
            st.write(f"Break: {item.break_trigger}")

    st.subheader("Outcome + Post-Mortem")
    calibration = build_calibration_report(research_store, result.identity.ticker)
    cols = st.columns(3)
    cols[0].metric("Calibration", calibration.status)
    cols[1].metric("Resolved Outcomes", calibration.sample_size)
    cols[2].metric("Threshold", calibration.minimum_sample_size)
    readiness_cols = st.columns(3)
    readiness_cols[0].metric(
        "Nearest Slice",
        calibration.nearest_calibration_slice or "n/a",
    )
    readiness_cols[1].metric("Outcomes Needed", calibration.outcomes_needed_for_calibration)
    readiness_cols[2].metric("Rank By EV", "Yes" if calibration.rank_by_ev_allowed else "No")
    st.metric("Calibration Readiness Score", f"{calibration.readiness_score}/100")
    if calibration.readiness_checks:
        with st.expander("Calibration Readiness Checklist", expanded=False):
            st.dataframe(
                [
                    {
                        "Area": item.area,
                        "Status": item.status,
                        "Score": item.score,
                        "Summary": item.summary,
                        "Evidence": "; ".join(item.evidence),
                        "Gaps": "; ".join(item.gaps),
                        "Next action": item.next_action,
                        "Stage impact": item.stage_impact,
                    }
                    for item in calibration.readiness_checks
                ],
                use_container_width=True,
                hide_index=True,
            )
    if calibration.calibration_actions:
        with st.expander("Calibration Readiness Actions", expanded=False):
            for item in calibration.calibration_actions:
                st.write(f"- {item}")
            st.markdown("**Required outcome fields**")
            for item in calibration.required_outcome_fields:
                st.write(f"- {item}")
    if calibration.slices:
        st.markdown("**Calibration Slice Scoreboard**")
        st.dataframe(
            [
                {
                    "Slice": item.signal_type,
                    "Status": item.status,
                    "Sample": item.sample_size,
                    "Needed": item.outcomes_needed_for_calibration,
                    "Rank by EV": "Yes" if item.rank_by_ev_allowed else "No",
                    "Hit rate": f"{item.hit_rate_pct:.1f}%" if item.hit_rate_pct is not None else "n/a",
                    "Brier": f"{item.brier_score:.3f}" if item.brier_score is not None else "n/a",
                    "Expected": f"{item.mean_expected_return_pct:.1f}%" if item.mean_expected_return_pct is not None else "n/a",
                    "Realized": f"{item.mean_realized_return_pct:.1f}%" if item.mean_realized_return_pct is not None else "n/a",
                    "Next action": item.next_action,
                }
                for item in calibration.slices
            ],
            use_container_width=True,
            hide_index=True,
        )
    process_cols = st.columns(3)
    process_cols[0].metric("Post-Mortems", calibration.post_mortem_count)
    process_cols[1].metric(
        "Review Coverage",
        f"{calibration.post_mortem_coverage_pct:.0f}%"
        if calibration.post_mortem_coverage_pct is not None else "n/a",
    )
    process_cols[2].metric(
        "Evidence Valid",
        f"{calibration.evidence_valid_rate_pct:.0f}%"
        if calibration.evidence_valid_rate_pct is not None else "n/a",
    )
    quality_cols = st.columns(3)
    quality_cols[0].metric("Post-Mortem Quality", calibration.post_mortem_quality_status)
    quality_cols[1].metric("Complete Reviews", calibration.complete_post_mortem_count)
    quality_cols[2].metric(
        "Complete Coverage",
        f"{calibration.complete_post_mortem_coverage_pct:.0f}%"
        if calibration.complete_post_mortem_coverage_pct is not None else "n/a",
    )
    for gap in calibration.post_mortem_quality_gaps[:3]:
        st.caption(f"Post-mortem quality gap: {gap}")
    if calibration.recurring_failure_modes or calibration.recurring_lessons or calibration.process_improvement_actions:
        with st.expander("Calibration Learning Loop", expanded=False):
            if calibration.recurring_failure_modes:
                st.markdown("**Recurring failure modes**")
                for item in calibration.recurring_failure_modes[:5]:
                    st.write(f"- {item}")
            if calibration.recurring_lessons:
                st.markdown("**Recurring lessons**")
                for item in calibration.recurring_lessons[:5]:
                    st.write(f"- {item}")
            if calibration.process_improvement_actions:
                st.markdown("**Process changes**")
                for item in calibration.process_improvement_actions[:5]:
                    st.write(f"- {item}")
    for gap in calibration.data_gaps[:3]:
        st.caption(gap)
    with st.form(f"post_mortem_{idea.idea_id}"):
        outcome_cols = st.columns(3)
        realized_return = outcome_cols[0].number_input("Realized return %", value=0.0, step=0.5)
        adverse = outcome_cols[1].number_input("Max adverse excursion %", value=0.0, step=0.5)
        favorable = outcome_cols[2].number_input("Max favorable excursion %", value=0.0, step=0.5)
        thesis_outcome = st.selectbox(
            "Thesis outcome",
            ["confirmed", "partly_confirmed", "contradicted", "inconclusive", "stopped_out", "expired"],
        )
        evidence_valid = st.selectbox(
            "Was the original evidence valid?",
            ["yes", "partly", "no", "unknown"],
        )
        closure_reason = st.text_input("Closure reason", value="")
        what_worked = st.text_area("What worked", value="", height=80)
        what_failed = st.text_area("What failed", value="", height=80)
        lessons = st.text_area("Lessons", value="", height=80)
        next_process_change = st.text_area("Process change for next time", value="", height=80)
        submitted = st.form_submit_button("Record Outcome For Calibration", use_container_width=True)
    if submitted:
        outcome = research_store.record_idea_post_mortem(
            idea.idea_id,
            {
                "horizon": idea.horizon,
                "realized_return_pct": realized_return,
                "max_adverse_excursion_pct": adverse,
                "max_favorable_excursion_pct": favorable,
                "thesis_outcome": thesis_outcome,
                "closure_reason": closure_reason,
                "evidence_valid": evidence_valid,
                "what_worked": what_worked,
                "what_failed": what_failed,
                "lessons": lessons,
                "next_process_change": next_process_change,
            },
        )
        if outcome:
            st.success("Outcome recorded. Calibration and historical analogs will include it after refresh.")
            st.rerun()
        else:
            st.error("Could not find a frozen idea version for this idea. Run research once, then retry.")

    note = st.text_area("Memory note", value="")
    if st.button("Save Idea To Memory", use_container_width=True):
        store.save_idea(result.identity.ticker, idea, note)
        st.success("Idea saved to local memory.")

    st.subheader("Idea Memory + Post-Mortem")
    records = store.list_records()
    if not records:
        st.info("No saved ideas yet.")
        return
    st.dataframe(
        [
            {
                "Ticker": record.get("ticker"),
                "Idea": record.get("title"),
                "Score": record.get("score"),
                "Capture": record.get("market_capture"),
                "EV": record.get("expected_value_pct"),
                "Status": record.get("status"),
                "Saved": record.get("saved_at"),
            }
            for record in records
        ],
        use_container_width=True,
        hide_index=True,
        )


def render_dual_model_research_audit(result: ResearchResult) -> None:
    run = getattr(result, "dual_model_research", None)
    if run is None:
        return
    st.markdown("#### DeepSeek / GPT-5.6 Sol Research Audit")
    _wrapped_metric_grid([
        ("Status", run.status),
        ("Profile", run.research_profile),
        (
            "DeepSeek quality",
            run.benchmark.deepseek_score if run.benchmark else "Unscored",
        ),
        (
            "Sol quality",
            run.benchmark.sol_score if run.benchmark else "Unscored",
        ),
        (
            "Input sufficiency",
            (
                f"{run.input_sufficiency.total_score}/100"
                if run.input_sufficiency else "Unscored"
            ),
        ),
    ])
    if run.input_sufficiency:
        st.markdown("**Input qualification**")
        st.dataframe(
            [
                {
                    "Dimension": item.label,
                    "Status": item.status,
                    "Score": f"{item.score}/{item.maximum}",
                    "Finding": item.summary,
                    "Gap": "; ".join(item.gaps),
                }
                for item in run.input_sufficiency.dimensions
            ],
            hide_index=True,
            use_container_width=True,
        )
        if run.input_sufficiency.critical_gaps:
            for gap in run.input_sufficiency.critical_gaps:
                st.error(gap)
    if run.evidence_pack_audit:
        audit = run.evidence_pack_audit
        st.markdown("**Evidence pack selection audit**")
        _wrapped_metric_grid([
            ("Normalized evidence", audit.searchable_count),
            ("Core prompt evidence", audit.core_count),
            ("Searchable outside core", len(audit.exclusions)),
            ("Current / prior pairs", len(audit.current_prior_pairs)),
        ])
        st.caption(
            "Core evidence is materiality-ranked. Every omitted item remains "
            "searchable and has an explicit exclusion reason."
        )
        with st.expander("Core exclusions and period-pair audit", expanded=False):
            if audit.exclusions:
                st.dataframe(
                    [
                        {
                            "Evidence": item.evidence_id,
                            "Kind": item.kind,
                            "Score": round(item.score, 1),
                            "Source": item.source_type,
                            "Period": item.period or "Unknown",
                            "Paired evidence": item.paired_evidence_id or "",
                            "Reason": item.reason,
                        }
                        for item in audit.exclusions
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            if audit.unpaired_change_ids:
                st.warning(
                    f"{len(audit.unpaired_change_ids)} change item(s) still "
                    "lack an explicit current/prior evidence pair."
                )
    hybrid = getattr(result, "hybrid_research", None)
    if hybrid and hybrid.retrieval_lifecycles:
        st.markdown("**Auditable retrieval lifecycle**")
        status_counts: dict[str, int] = {}
        for item in hybrid.retrieval_lifecycles:
            status_counts[item.status] = status_counts.get(item.status, 0) + 1
        _wrapped_metric_grid([
            ("Retrieval tasks", len(hybrid.retrieval_lifecycles)),
            ("Validated", status_counts.get("validated", 0)),
            ("Catalog matches", status_counts.get("catalog_match", 0)),
            (
                "Unavailable / manual",
                status_counts.get("unavailable", 0)
                + status_counts.get("licensed_manual_required", 0),
            ),
        ])
        st.caption(
            "Catalog matches are discovery only. A factual claim requires "
            "fetched or cached content, parsing, citation binding, and validation."
        )
        with st.expander(
            "Source retrieval, parser, and validation audit",
            expanded=False,
        ):
            st.dataframe(
                [
                    {
                        "Request": item.request_id,
                        "Source family": item.source_type or "Unknown",
                        "Status": item.status,
                        "Lifecycle": " -> ".join(item.history),
                        "Failure": item.failure_code,
                        "Finding": item.message,
                    }
                    for item in hybrid.retrieval_lifecycles
                ],
                hide_index=True,
                use_container_width=True,
            )
    if hybrid and hybrid.claim_evidence_audits:
        st.markdown("**Claim-level evidence audit**")
        passed = sum(
            item.status == "passed"
            for item in hybrid.claim_evidence_audits
        )
        failed = sum(
            item.status == "failed"
            for item in hybrid.claim_evidence_audits
        )
        _wrapped_metric_grid([
            ("Material claims", len(hybrid.claim_evidence_audits)),
            ("Passed", passed),
            ("Failed", failed),
            (
                "Repair tasks",
                len(hybrid.audit_repair_queue.items)
                if hybrid.audit_repair_queue else 0,
            ),
        ])
        with st.expander(
            "Rejected claims and executable repair queue",
            expanded=bool(failed),
        ):
            st.dataframe(
                [
                    {
                        "Claim": item.claim_text,
                        "Class": item.classification,
                        "Status": item.status,
                        "Period": item.period_alignment,
                        "Entity": item.entity_alignment,
                        "Independent origins": item.independent_origin_count,
                        "Numeric check": item.numerical_reconciliation,
                        "Blocker": "; ".join(item.blockers),
                    }
                    for item in hybrid.claim_evidence_audits
                ],
                hide_index=True,
                use_container_width=True,
            )
            if hybrid.audit_repair_queue:
                for item in hybrid.audit_repair_queue.items[:12]:
                    st.markdown(
                        f"**{item.missing_source_type}: "
                        f"{item.missing_metric or 'claim evidence'}**"
                    )
                    st.write(item.rejection_reason)
                    st.caption(
                        "Retrieval task: "
                        + str(item.retrieval_request.get("title") or "")
                    )
    if hybrid and hybrid.partial_rerun_manifest:
        manifest = hybrid.partial_rerun_manifest
        st.info(
            "Partial evidence repair rerun: reused rounds "
            + ", ".join(str(item) for item in manifest.reused_round_numbers)
            + "; reran rounds "
            + ", ".join(str(item) for item in manifest.rerun_round_numbers)
            + f". Status: {manifest.status}."
        )
    if run.usage_summary:
        usage = run.usage_summary
        st.markdown("**Quality-first usage**")
        _wrapped_metric_grid([
            ("Model calls", usage.model_calls),
            ("Total tokens", f"{usage.total_tokens:,}"),
            ("Retrieval calls", usage.retrieval_calls),
            ("Retries", usage.retries),
        ])
        st.caption(
            "No fixed pilot dollar cap is applied. Usage, latency and "
            "provider charges are observations for a later profile-specific "
            "budget policy."
        )
        if usage.review_required:
            st.warning(
                "Actual usage exceeded the preflight estimate by more than "
                "2x. Review the workflow for repetition or non-progress."
            )
    if run.stages:
        st.dataframe(
            [
                {
                    "Stage": item.label,
                    "Status": item.status,
                    "DeepSeek": (
                        item.comparison.deepseek_score
                        if item.comparison else "Unscored"
                    ),
                    "Sol": (
                        item.comparison.sol_score
                        if item.comparison else "Unscored"
                    ),
                    "Stronger": (
                        item.comparison.stronger_model
                        if item.comparison else "Unknown"
                    ),
                    "Agreement": (
                        item.comparison.agreement
                        if item.comparison else "Unavailable"
                    ),
                    "Accepted Sol additions": len(item.accepted_secondary_deltas),
                }
                for item in run.stages
            ],
            hide_index=True,
            use_container_width=True,
        )
    with st.expander("Gap diagnoses and improvement candidates", expanded=False):
        for gap in run.gap_diagnoses:
            st.markdown(
                f"**{gap.stage_id.replace('_', ' ').title()} | "
                f"{gap.category}**"
            )
            st.write(gap.summary)
            st.caption(
                f"Severity: {gap.severity}; stronger model: "
                f"{gap.stronger_model}; treatment: {gap.recommended_fix}"
            )
        if run.improvement_candidates:
            st.markdown("**Human-reviewable improvement queue**")
            for item in run.improvement_candidates:
                st.write(f"- {item.title}: {item.proposed_change}")
                st.caption(
                    f"Status: {item.activation_status}; runtime code/prompt "
                    f"mutation: {'allowed' if item.runtime_mutation_allowed else 'not allowed'}."
                )


def render_memo(result: ResearchResult) -> None:
    st.subheader("DD Memo + Investment Committee Pack")
    st.download_button(
        "Download Markdown Memo",
        result.memo_markdown,
        file_name=f"{result.identity.ticker.lower()}_ic_memo.md",
        mime="text/markdown",
        use_container_width=True,
    )
    st.markdown(result.memo_markdown)


def render_consensus(result: ResearchResult) -> None:
    st.subheader("Consensus and Expectations")
    package = result.consensus
    if package.target:
        target = package.target
        columns = st.columns(6)
        columns[0].metric("Current Price", _money(target.current_price, target.currency))
        columns[1].metric(target.target_label, _money(_target_value(target), target.currency))
        columns[2].metric("Target Kind", target.target_kind.title())
        columns[3].metric("High / Low", f"{_plain(target.target_high)} / {_plain(target.target_low)}")
        columns[4].metric("Implied Upside", _percent(target.implied_upside_pct))
        columns[5].metric("Analysts", target.analyst_count if target.analyst_count is not None else "n/a")
        st.caption(
            f"Selected provider: {target.source or package.provider}. "
            f"Dispersion: {_percent(target.dispersion_pct)}. Observed {target.observed_at or target.as_of}; "
            f"source as-of: {target.source_as_of or target.provider_timestamp or 'Unknown'}; freshness: "
            f"{target.freshness_days if target.freshness_days is not None else 'n/a'} days."
        )
    else:
        st.info(f"Consensus status: {package.status}.")
    if package.data_gaps:
        for gap in package.data_gaps:
            st.caption(gap)

    provider_targets = package.provider_targets or ([package.target] if package.target else [])
    if provider_targets:
        st.markdown("#### Provider Targets")
        st.dataframe(
            [
                {
                    "Provider": item.source,
                    "Official": item.official,
                    "Semantic": item.target_kind,
                    "Aggregate": item.target_aggregate,
                    "Mean": item.target_mean,
                    "Median": item.target_median,
                    "High": item.target_high,
                    "Low": item.target_low,
                    "Analysts": item.analyst_count if item.analyst_count is not None else "Unknown",
                    "Observed": item.observed_at or item.as_of,
                    "Source as-of": item.source_as_of or item.provider_timestamp or "Unknown",
                }
                for item in provider_targets
            ],
            use_container_width=True,
            hide_index=True,
        )

    if package.provider_statuses:
        st.markdown("#### Provider Health")
        st.dataframe(
            [
                {
                    "Provider": item.provider,
                    "Status": item.status,
                    "Official": item.official,
                    "Entitlement": item.entitlement_status,
                    "Observed": item.observed_at,
                    "Message": item.message,
                }
                for item in package.provider_statuses
            ],
            use_container_width=True,
            hide_index=True,
        )

    if package.comparisons:
        st.markdown("#### Provider Comparison")
        st.dataframe(
            [
                {
                    "Field": item.field,
                    "Values": "; ".join(f"{key}: {value}" for key, value in item.values.items()),
                    "Spread": _percent(item.spread_pct),
                    "Interpretation": item.interpretation,
                }
                for item in package.comparisons
            ],
            use_container_width=True,
            hide_index=True,
        )

    revision_rows = [
        {
            "Metric": revision.metric,
            "Window": f"{revision.window_days}d",
            "Provider": revision.provider or "n/a",
            "Status": revision.status,
            "Start": revision.start_date or "n/a",
            "End": revision.end_date or "n/a",
            "Change": _percent(revision.change_pct),
            "Reason": revision.reason or "n/a",
        }
        for revision in package.revisions
    ]
    if revision_rows:
        st.markdown("#### Revision History")
        st.dataframe(revision_rows, use_container_width=True, hide_index=True)

    trend_rows = _recommendation_trend_rows(package)
    if trend_rows:
        st.markdown("#### Recommendation Trend")
        st.dataframe(trend_rows, use_container_width=True, hide_index=True)

    if package.recommendations:
        rec = package.recommendations
        st.markdown("#### Recommendation Mix")
        st.dataframe(
            [{
                "Strong Buy": rec.strong_buy, "Buy": rec.buy, "Hold": rec.hold,
                "Sell": rec.sell, "Strong Sell": rec.strong_sell,
                "Consensus": rec.consensus_label or "n/a",
            }],
            use_container_width=True,
            hide_index=True,
        )

    if package.estimates:
        st.markdown("#### Forward Estimates")
        st.dataframe(
            [
                {
                    "Metric": item.metric,
                    "Period": item.period_end,
                    "Type": item.period_type,
                    "Average": item.average,
                    "High": item.high,
                    "Low": item.low,
                    "Analysts": item.analyst_count,
                    "Currency": item.currency,
                    "Provider": item.source,
                    "Date precision": item.period_precision,
                    "Revisions up/down": f"{item.revisions_up if item.revisions_up is not None else 'n/a'} / {item.revisions_down if item.revisions_down is not None else 'n/a'}",
                }
                for item in package.estimates[:24]
            ],
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("Import consensus CSV snapshots", expanded=False):
        st.caption(
            "Use this to seed point-in-time targets, estimates, recommendations, and surprises. "
            "Rows are stored in local SQLite and immediately support 7/30/90-day revision calculations."
        )
        directory = st.text_input(
            "CSV directory",
            value=str(config.CONSENSUS_CSV_DIR),
            help="Directory containing targets.csv, estimates.csv, recommendations.csv, surprises.csv, and optional revision CSVs.",
        )
        if st.button("Create CSV Templates", use_container_width=True):
            try:
                template_result = write_consensus_csv_templates(
                    Path(directory),
                    ticker=result.identity.ticker,
                    overwrite=False,
                )
                if template_result.files_written:
                    st.success("Created templates: " + ", ".join(template_result.files_written))
                if template_result.files_existing:
                    st.info("Already existed: " + ", ".join(template_result.files_existing))
                for message in template_result.messages:
                    st.caption(message)
            except Exception as exc:
                st.error(f"Consensus CSV template creation failed: {exc}")
        tickers = st.text_input(
            "Tickers to import",
            value=result.identity.ticker,
            help="Comma or space separated. Leave blank to import every ticker found in the CSV files.",
        )
        if st.button("Import Consensus CSV To SQLite", use_container_width=True):
            ticker_list = [item.strip().upper() for item in tickers.replace(",", " ").split() if item.strip()] or None
            try:
                outcome = import_consensus_csv(Path(directory), ticker_list, ResearchStore())
                st.success(f"Imported {outcome.imported} ticker(s); skipped {outcome.skipped}.")
                cols = st.columns(3)
                cols[0].metric("Metadata observations", outcome.metadata_observations)
                cols[1].metric("Revision windows", outcome.revision_windows_available)
                cols[2].metric("Incomplete windows", outcome.revision_windows_incomplete)
                if outcome.rows_by_file:
                    st.dataframe(
                        [
                            {"File": filename, "Rows": count}
                            for filename, count in outcome.rows_by_file.items()
                        ],
                        use_container_width=True,
                        hide_index=True,
                    )
                for message in outcome.messages[:8]:
                    st.caption(message)
                st.info("Run the research workflow again to refresh the consensus panel from the imported snapshots.")
            except Exception as exc:
                st.error(f"Consensus CSV import failed: {exc}")

    bridge = result.expectations_bridge
    st.markdown("#### Expectations Bridge")
    st.write(bridge.headline)
    if bridge.event_audits:
        st.dataframe(
            [
                {
                    "Event": item.event_label,
                    "Form": item.form or "Unknown",
                    "Accession": item.accession or "Unknown",
                    "Filed": item.filing_date or "Unknown",
                    "Reporting period": item.reporting_period or "Unknown",
                    "Actual metrics checked": "; ".join(item.actual_metrics_checked) or "None",
                    "Pre-event snapshots": item.eligible_pre_event_snapshots,
                    "Post-event snapshots": item.eligible_post_event_snapshots,
                    "Status": item.status,
                    "Exact reason": item.reason,
                }
                for item in bridge.event_audits[:12]
            ],
            use_container_width=True,
            hide_index=True,
        )
    if bridge.comparisons:
        st.dataframe(
            [
                {
                    "Metric": item.metric,
                    "Period": item.period_end,
                    "Expected": item.expected,
                    "Actual": item.actual,
                    "Surprise": _percent(item.surprise_pct),
                    "Post-event revision": _percent(item.post_event_revision_pct),
                    "Interpretation": item.interpretation,
                }
                for item in bridge.comparisons
            ],
            use_container_width=True,
            hide_index=True,
        )
    st.caption(bridge.point_in_time_note)


def render_valuation(result: ResearchResult) -> None:
    valuation = result.valuation
    st.markdown("#### Valuation Triangulation")
    if valuation.status != "Available":
        st.info(
            f"{valuation.template}: {valuation.status}. "
            f"{' '.join(valuation.missing_data)}"
        )
        return
    cols = st.columns(4)
    cols[0].metric("Template", valuation.template)
    cols[1].metric("Weighted Value", _money(valuation.probability_weighted_value, valuation.currency))
    cols[2].metric("Expected Return", _percent(valuation.expected_return_pct))
    cols[3].metric("Vs Consensus", _percent(valuation.disagreement_pct))
    st.dataframe(
        [
            {
                "Case": case.name,
                "Probability": f"{case.probability:.0%}",
                "Fair Value": _money(case.fair_value, valuation.currency),
                "Method": case.method,
                "Assumptions": "; ".join(case.assumptions),
            }
            for case in valuation.cases
        ],
        use_container_width=True,
        hide_index=True,
    )
    if valuation.normalization_notes:
        st.caption(" ".join(valuation.normalization_notes))
    if valuation.missing_data:
        st.caption("Unavailable methods: " + " ".join(valuation.missing_data))
    st.caption(f"Confidence: {valuation.confidence}. {valuation.methodology}")


def _plain(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def _money(value: float | None, currency: str) -> str:
    return "n/a" if value is None else f"{value:,.2f} {currency}"


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:+.1f}%"


def _target_value(target) -> float | None:
    if target.target_kind == "aggregate":
        return target.target_aggregate
    if target.target_kind == "median":
        return target.target_median
    return target.target_mean


def _recommendation_trend_rows(package) -> list[dict]:
    by_period: dict[str, dict] = {}
    for observation in package.observations:
        if not observation.field.startswith("recommendation_trend_"):
            continue
        period = observation.source_as_of or observation.observed_at or "Unknown"
        row = by_period.setdefault(period, {"Period": period, "Provider": observation.provider})
        label = observation.field.replace("recommendation_trend_", "").replace("_", " ").title()
        row[label] = (
            observation.value_text
            if observation.value_text is not None
            else observation.value_numeric
        )
        if observation.analyst_count is not None:
            row["Analysts"] = observation.analyst_count
    return [by_period[key] for key in sorted(by_period.keys(), reverse=True)]


def _management_signal_quality_rows(ideas) -> list[dict]:
    rows = []
    for idea in ideas:
        event = idea.source_events[0] if idea.source_events else None
        if not event:
            continue
        metrics = event.metrics or {}
        if not (
            metrics.get("management_claim_id")
            or metrics.get("meeting_event_id")
            or metrics.get("sentiment_label")
        ):
            continue
        rows.append({
            "Idea": idea.title,
            "Signal": event.category,
            "Sentiment": metrics.get("sentiment_label") or metrics.get("cross_check_status") or "n/a",
            "Score": metrics.get("sentiment_score"),
            "Specificity": metrics.get("specificity_score"),
            "Cross-check": metrics.get("cross_check_status") or "n/a",
            "Evasion terms": ", ".join(metrics.get("evasion_terms") or []) or "n/a",
            "Uncertainty terms": ", ".join(metrics.get("uncertainty_terms") or []) or "n/a",
        })
    return rows


if __name__ == "__main__":
    main()
