from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

from ..domain.saved_research import (
    DisplayedResearchContext,
    DisplayedInvestigationReference,
    DerivedEvidenceRecoveryAttempt,
    LoadSavedResearchResult,
    ArtifactVerificationResult,
    ResearchResultArtifactCodec,
    SaveResearchResultCommand,
    SavedResearchArtifact,
    SavedResearchBlobStore,
    SavedResearchQuery,
    SavedResearchRepository,
    SavedResearchStatus,
    SavedArtifactHealth,
)


class SavedResearchError(RuntimeError):
    pass


class SavedResearchLoadError(SavedResearchError):
    def __init__(
        self,
        message: str,
        *,
        reason: str,
        artifact_id: str = "",
        failure_path: str = "",
        annotation: str = "",
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.artifact_id = artifact_id
        self.failure_path = failure_path
        self.annotation = annotation


def build_displayed_research_context(
    artifact: SavedResearchArtifact,
) -> DisplayedResearchContext:
    """Build presentation context from an artifact without inferring prose.

    Earlier versions stored launch details under several ``setup_summary``
    aliases.  Retain those documented aliases for compatibility, but only use
    explicit IDs; arbitrary operation wording must not become an intent ID.
    """

    setup = dict(getattr(artifact, "setup_summary", {}) or {})

    def value(*keys: str) -> str:
        for key in keys:
            candidate = setup.get(key)
            if candidate not in (None, ""):
                return str(candidate)
        return ""

    operation_kind = str(
        getattr(artifact, "operation_kind", "")
        or value("operation_kind", "research_operation_kind")
    )
    operation_label = str(
        getattr(artifact, "operation_label", "")
        or value("operation_label", "research_operation_label", "selected_action_title")
    )
    action_id = value(
        "investigation_action_id", "action_id", "selected_action_id",
    )
    intent_id = value(
        "research_intent_id", "intent_id", "agenda_item_id",
    )
    # Cash-conversion focused runs created before intent IDs existed have one
    # safe semantic compatibility mapping.  Do not derive identities from
    # arbitrary free text.
    if not intent_id and str(value("investigation_action_kind")).casefold() == "cash_conversion_analysis":
        intent_id = "cash_generation_capital_allocation:recurring_cash_conversion"
    return DisplayedResearchContext(
        artifact_id=str(getattr(artifact, "artifact_id", "") or ""),
        run_id=str(getattr(artifact, "run_id", "") or ""),
        operation_kind=operation_kind,
        operation_label=operation_label,
        operation_action_id=action_id,
        research_intent_id=intent_id,
        parent_artifact_id=str(getattr(artifact, "parent_artifact_id", "") or ""),
        evidence_hash=str(getattr(artifact, "evidence_hash", "") or ""),
        data_cutoff=str(getattr(artifact, "data_cutoff", "") or ""),
        is_derived_revision=bool(getattr(artifact, "parent_artifact_id", "")),
        outcome=str(getattr(artifact, "outcome", "") or ""),
    )


def build_displayed_investigation_reference(
    context: DisplayedResearchContext,
) -> DisplayedInvestigationReference:
    """Keep only stable identifiers; journey prose is rebuilt on render."""

    return DisplayedInvestigationReference(
        artifact_id=context.artifact_id,
        run_id=context.run_id,
        operation_kind=context.operation_kind,
        operation_label=context.operation_label,
        action_id=context.operation_action_id,
        intent_id=context.research_intent_id,
        parent_artifact_id=context.parent_artifact_id,
        evidence_hash=context.evidence_hash,
        data_cutoff=context.data_cutoff,
    )


class SaveResearchResult:
    def __init__(
        self,
        repository: SavedResearchRepository,
        blob_store: SavedResearchBlobStore,
        codec: ResearchResultArtifactCodec,
        *,
        persistence_available: bool = True,
        persistence_error: str = "",
    ) -> None:
        self.repository = repository
        self.blob_store = blob_store
        self.codec = codec
        self.persistence_available = bool(persistence_available)
        self.persistence_error = str(persistence_error or "")

    def __call__(self, command: SaveResearchResultCommand) -> SavedResearchArtifact:
        result = command.result
        identity = getattr(result, "identity", None)
        if identity is None or not str(getattr(identity, "ticker", "") or ""):
            raise SavedResearchError("The research result has no resolved security identity.")
        manifest = getattr(result, "run_manifest", None)
        run_id = str(command.run_id or getattr(manifest, "run_id", "") or "")
        if not run_id:
            raise SavedResearchError("The research result has no run ID.")
        raw_payload = self.codec.encode(result)
        try:
            _restored, verification = self.codec.verify(raw_payload)
        except Exception as exc:
            reason = str(getattr(exc, "reason", "") or "verification_failed")
            raise SavedResearchLoadError(
                "The completed result could not be verified for restart recovery.",
                reason=(
                    reason
                    if reason in {
                        "runtime_incompatible",
                        "annotation_unresolved",
                        "schema_unsupported",
                        "payload_corrupt",
                        "payload_missing",
                        "migration_failed",
                    }
                    else "migration_failed"
                ),
                failure_path=str(getattr(exc, "failure_path", "") or ""),
                annotation=str(getattr(exc, "annotation", "") or ""),
            ) from exc
        payload_reference, payload_sha256, compressed_size = self.blob_store.put(raw_payload)
        saved_at = _utc_now()
        verification = replace(verification, verified_at=saved_at)
        evidence_hash = str(
            getattr(manifest, "admitted_evidence_hash", "")
            or getattr(getattr(result, "hybrid_research", None), "evidence_hash", "")
            or ""
        )
        ticker = str(identity.ticker).upper()
        cik = str(getattr(identity, "cik", "") or "")
        exchange = str(getattr(identity, "exchange", "") or "")
        security_key = _security_key(ticker=ticker, cik=cik, exchange=exchange)
        operation_label = command.operation_label or _default_operation_label(
            ticker, command.operation_kind
        )
        result_version = str(getattr(manifest, "app_version", "") or "unknown")
        artifact_id = _artifact_id(
            command.scope_id,
            run_id,
            evidence_hash,
            command.operation_kind,
            command.outcome,
            payload_sha256,
        )
        validation, qa_status, closure, publication = _quality_statuses(result)
        if command.status in {
            SavedResearchStatus.DRAFT,
            SavedResearchStatus.PARTIAL,
            SavedResearchStatus.FAILED,
        }:
            qa_status = "incomplete"
            closure = "not_closed"
        profile = _research_profile(result)
        artifact = SavedResearchArtifact(
            artifact_id=artifact_id,
            run_id=run_id,
            security_key=security_key,
            ticker=ticker,
            company_name=str(getattr(identity, "name", "") or ticker),
            exchange=exchange,
            cik=cik,
            ticker_aliases=_ticker_aliases(
                ticker,
                command.setup_summary.get("ticker_aliases", ()),
            ),
            research_profile=profile,
            source_policy_summary=_source_policy_summary(result),
            operation_kind=command.operation_kind,
            operation_label=operation_label,
            lifecycle_state=command.lifecycle_state,
            validation_status=validation,
            independent_qa_status=qa_status,
            closure_status=closure,
            publication_status=publication,
            outcome=command.outcome,
            status=command.status,
            origin=command.origin,
            created_at=command.created_at or str(getattr(manifest, "generated_at", "") or saved_at),
            completed_at=command.completed_at or _completed_at(result) or saved_at,
            saved_at=saved_at,
            data_cutoff=_data_cutoff(result),
            source_freshness=_freshness(result),
            evidence_hash=evidence_hash,
            result_version=result_version,
            preferred_route=command.preferred_route or "Decision",
            setup_summary=_redacted_setup(command.setup_summary),
            payload_reference=payload_reference,
            payload_sha256=payload_sha256,
            payload_size_bytes=len(raw_payload),
            compressed_size_bytes=compressed_size,
            storage_scope=command.storage_scope,
            scope_id=command.scope_id,
            analysis_capabilities=_analysis_capabilities(result),
            parent_artifact_id=command.parent_artifact_id,
            supersedes_artifact_id=command.supersedes_artifact_id,
            health=verification.status,
            verified_at=verification.verified_at,
            codec_version=verification.codec_version,
            writer_runtime=verification.writer_runtime,
        )
        saved = self.repository.save(artifact)
        if saved.health is not SavedArtifactHealth.VERIFIED:
            updated = self.repository.update_health(
                saved.artifact_id,
                scope_id=saved.scope_id,
                verification=verification,
            )
            if updated is not None:
                saved = updated
        return saved


def _analysis_capabilities(result: Any) -> tuple[str, ...]:
    capabilities: list[str] = []
    bridge = getattr(result, "cash_conversion_recurrence", None)
    if bridge is None:
        bridge = getattr(
            getattr(result, "hybrid_research", None),
            "cash_conversion_recurrence",
            None,
        )
    version = str(getattr(bridge, "analysis_version", "") or "")
    if version:
        capabilities.append(version)
    return tuple(dict.fromkeys(capabilities))


class ListSavedResearch:
    def __init__(self, repository: SavedResearchRepository) -> None:
        self.repository = repository

    def __call__(self, query: SavedResearchQuery) -> list[SavedResearchArtifact]:
        return self.repository.list(query)


class LoadSavedResearch:
    def __init__(
        self,
        repository: SavedResearchRepository,
        blob_store: SavedResearchBlobStore,
        codec: ResearchResultArtifactCodec,
    ) -> None:
        self.repository = repository
        self.blob_store = blob_store
        self.codec = codec

    def __call__(self, artifact_id: str, *, scope_id: str) -> LoadSavedResearchResult:
        try:
            artifact = self.repository.get(artifact_id, scope_id=scope_id)
        except Exception as exc:
            raise SavedResearchLoadError(
                "Saved research metadata could not be read.",
                reason="payload_missing",
                artifact_id=artifact_id,
            ) from exc
        if artifact is None:
            raise SavedResearchLoadError(
                "The selected saved research result is unavailable.",
                reason="payload_missing",
                artifact_id=artifact_id,
            )
        try:
            payload = self.blob_store.get(
                artifact.payload_reference,
                expected_sha256=artifact.payload_sha256,
            )
        except Exception as exc:
            message = str(exc).casefold()
            reason = (
                "payload_missing"
                if "missing" in message or "expired" in message
                else "payload_corrupt"
            )
            raise SavedResearchLoadError(
                "The saved research payload is missing or failed its integrity check.",
                reason=reason,
                artifact_id=artifact_id,
            ) from exc
        try:
            result, verification = self.codec.verify(payload)
            # Verification happens on the reader runtime. Preserve the recorded
            # writer runtime instead of retroactively claiming that a legacy
            # blob was written by the current process.
            verification = replace(
                verification,
                writer_runtime=artifact.writer_runtime,
            )
        except Exception as exc:
            reason = str(getattr(exc, "reason", "") or "migration_failed")
            if reason not in {
                "runtime_incompatible",
                "annotation_unresolved",
                "schema_unsupported",
                "payload_corrupt",
                "payload_missing",
                "migration_failed",
            }:
                reason = "migration_failed"
            readable = {
                "runtime_incompatible": (
                    "This saved result cannot be opened by the current Python runtime."
                ),
                "annotation_unresolved": (
                    "This saved result contains a type annotation that the current app "
                    "cannot resolve safely."
                ),
                "schema_unsupported": (
                    "This saved result uses a schema that this app version does not support."
                ),
                "payload_corrupt": "The saved research payload is corrupt.",
                "payload_missing": "The saved research payload is missing.",
                "migration_failed": (
                    "This saved result could not be migrated to the current app schema."
                ),
            }[reason]
            health = _health_for_failure(reason)
            try:
                self.repository.update_health(
                    artifact_id,
                    scope_id=scope_id,
                    verification=ArtifactVerificationResult(
                        status=health,
                        verified_at=_utc_now(),
                        codec_version=str(getattr(artifact, "codec_version", "") or ""),
                        writer_runtime=str(getattr(artifact, "writer_runtime", "") or ""),
                        reason=reason,
                        failure_path=str(getattr(exc, "failure_path", "") or ""),
                        annotation=str(getattr(exc, "annotation", "") or ""),
                    ),
                )
            except Exception:
                pass
            raise SavedResearchLoadError(
                readable,
                reason=reason,
                artifact_id=artifact_id,
                failure_path=str(getattr(exc, "failure_path", "") or ""),
                annotation=str(getattr(exc, "annotation", "") or ""),
            ) from exc
        if artifact.health is not SavedArtifactHealth.VERIFIED:
            try:
                verification = replace(verification, verified_at=_utc_now())
                updated = self.repository.update_health(
                    artifact_id,
                    scope_id=scope_id,
                    verification=verification,
                )
                if updated is not None:
                    artifact = updated
            except Exception:
                pass
        return LoadSavedResearchResult(artifact=artifact, result=result)


class LoadLatestCompletedResearch:
    def __init__(
        self,
        repository: SavedResearchRepository,
        loader: LoadSavedResearch,
    ) -> None:
        self.repository = repository
        self.loader = loader

    def __call__(
        self,
        *,
        scope_id: str,
        ticker: str = "",
        security_key: str = "",
    ) -> LoadSavedResearchResult | None:
        matches = self.repository.list(
            SavedResearchQuery(
                scope_id=scope_id,
                ticker=ticker,
                security_key=security_key,
                completed_only=True,
                limit=25,
            )
        )
        unhealthy = {
            SavedArtifactHealth.RUNTIME_INCOMPATIBLE,
            SavedArtifactHealth.SCHEMA_UNSUPPORTED,
            SavedArtifactHealth.PAYLOAD_CORRUPT,
            SavedArtifactHealth.PAYLOAD_MISSING,
            SavedArtifactHealth.VERIFICATION_FAILED,
        }
        for artifact in matches:
            if artifact.health in unhealthy:
                continue
            try:
                return self.loader(artifact.artifact_id, scope_id=scope_id)
            except SavedResearchLoadError:
                continue
        return None


class DeleteSavedResearch:
    def __init__(
        self,
        repository: SavedResearchRepository,
        blob_store: SavedResearchBlobStore,
    ) -> None:
        self.repository = repository
        self.blob_store = blob_store

    def __call__(self, artifact_id: str, *, scope_id: str) -> bool:
        artifact = self.repository.get(artifact_id, scope_id=scope_id)
        if artifact is None or artifact.pinned:
            return False
        deleted = self.repository.delete(artifact_id, scope_id=scope_id)
        if deleted and self.repository.payload_reference_count(artifact.payload_reference) == 0:
            self.blob_store.delete(artifact.payload_reference)
        return deleted


class PinSavedResearch:
    def __init__(self, repository: SavedResearchRepository) -> None:
        self.repository = repository

    def __call__(self, artifact_id: str, *, scope_id: str, pinned: bool) -> bool:
        return self.repository.set_pinned(
            artifact_id, scope_id=scope_id, pinned=pinned
        )


class AdoptCompletedInMemoryResult(SaveResearchResult):
    pass


class SavedResearchService:
    """Small facade shared by Streamlit and future HTTP adapters."""

    def __init__(
        self,
        repository: SavedResearchRepository,
        blob_store: SavedResearchBlobStore,
        codec: ResearchResultArtifactCodec,
    ) -> None:
        self.repository = repository
        self.blob_store = blob_store
        self.codec = codec
        self.save_result = SaveResearchResult(repository, blob_store, codec)
        self.list_results = ListSavedResearch(repository)
        self.load_result = LoadSavedResearch(repository, blob_store, codec)
        self.load_latest_completed = LoadLatestCompletedResearch(
            repository, self.load_result
        )
        self.delete_result = DeleteSavedResearch(repository, blob_store)
        self.pin_result = PinSavedResearch(repository)

    def get_recovery_attempt(
        self, recovery_key: str, *, scope_id: str
    ) -> DerivedEvidenceRecoveryAttempt | None:
        return self.repository.get_recovery_attempt(recovery_key, scope_id=scope_id)

    def save_recovery_attempt(
        self, attempt: DerivedEvidenceRecoveryAttempt
    ) -> DerivedEvidenceRecoveryAttempt:
        return self.repository.save_recovery_attempt(attempt)

    def remember_displayed(self, artifact_id: str, *, scope_id: str) -> None:
        self.repository.set_last_displayed(scope_id=scope_id, artifact_id=artifact_id)

    def load_last_displayed(self, *, scope_id: str) -> LoadSavedResearchResult | None:
        artifact_id = self.repository.get_last_displayed(scope_id=scope_id)
        if not artifact_id:
            return None
        try:
            return self.load_result(artifact_id, scope_id=scope_id)
        except (SavedResearchError, OSError, RuntimeError, ValueError):
            return None


def should_auto_load_saved_result(
    *,
    displayed_run_id: str,
    originating_run_id: str,
    displayed_artifact_id: str = "",
    originating_artifact_id: str = "",
) -> bool:
    """Do not replace research the user opened while a background job ran."""

    artifact_origin = str(originating_artifact_id or "")
    if artifact_origin:
        return str(displayed_artifact_id or "") == artifact_origin
    origin = str(originating_run_id or "")
    return not origin or str(displayed_run_id or "") == origin


def _health_for_failure(reason: str) -> SavedArtifactHealth:
    return {
        "runtime_incompatible": SavedArtifactHealth.RUNTIME_INCOMPATIBLE,
        "annotation_unresolved": SavedArtifactHealth.VERIFICATION_FAILED,
        "schema_unsupported": SavedArtifactHealth.SCHEMA_UNSUPPORTED,
        "payload_corrupt": SavedArtifactHealth.PAYLOAD_CORRUPT,
        "payload_missing": SavedArtifactHealth.PAYLOAD_MISSING,
    }.get(str(reason or ""), SavedArtifactHealth.VERIFICATION_FAILED)


def _quality_statuses(result: Any) -> tuple[str, str, str, str]:
    hybrid = getattr(result, "hybrid_research", None)
    qa = getattr(result, "independent_research_qa", None)
    freeze = getattr(result, "demo_freeze_decision", None) or getattr(
        hybrid, "demo_freeze_decision", None
    )
    validation = str(
        getattr(hybrid, "deterministic_validation_status", "")
        or getattr(freeze, "validation_status", "")
        or "not_recorded"
    )
    qa_status = str(getattr(qa, "status", "") or "not_run")
    closure_result = getattr(qa, "closure_result", None)
    closure = str(
        getattr(closure_result, "state", "")
        or getattr(freeze, "qa_closure_state", "")
        or "not_run"
    )
    publication = str(
        getattr(freeze, "publication_label", "")
        or getattr(freeze, "status", "")
        or "not_evaluated"
    )
    return validation, qa_status, closure, publication


def _research_profile(result: Any) -> str:
    profile = getattr(result, "research_profile", None)
    if profile is not None:
        return str(getattr(profile, "profile_id", "") or getattr(profile, "label", "") or "")
    policy = getattr(result, "research_execution_policy", None)
    if policy is not None:
        return str(getattr(policy, "research_depth", "") or "")
    return ""


def _source_policy_summary(result: Any) -> str:
    policy = getattr(result, "source_policy_fingerprint", None)
    if policy is not None:
        families = list(getattr(policy, "enabled_source_families", ()) or ())
        return ", ".join(families) if families else str(getattr(policy, "fingerprint", "") or "")
    return ""


def _data_cutoff(result: Any) -> str:
    policy = getattr(result, "data_access_policy", None)
    return str(getattr(policy, "data_cutoff", "") or "")


def _freshness(result: Any) -> str:
    freshness = getattr(result, "research_freshness", None)
    component_timestamps = [
        f"{str(getattr(item, 'component', '') or 'source')}:"
        f"{str(getattr(item, 'source_as_of', '') or getattr(item, 'observed_at', '') or 'unknown')}"
        for item in (getattr(freshness, "components", ()) or ())
    ]
    return str(
        "; ".join(component_timestamps)
        or getattr(freshness, "summary", "")
        or getattr(freshness, "overall_status", "")
        or ""
    )


def _completed_at(result: Any) -> str:
    qa = getattr(result, "independent_research_qa", None)
    hybrid = getattr(result, "hybrid_research", None)
    freshness = getattr(result, "research_freshness", None)
    return str(
        getattr(qa, "completed_at", "")
        or getattr(hybrid, "completed_at", "")
        or getattr(freshness, "run_completed_at", "")
        or ""
    )


_SENSITIVE_SETUP_KEY_PARTS = (
    "authorization",
    "cookie",
    "credential",
    "key",
    "lease",
    "password",
    "request_body",
    "response_body",
    "secret",
    "token",
)
_SENSITIVE_SETUP_VALUE = re.compile(
    r"(?i)(?:\bBearer\s+[A-Za-z0-9._~+/-]+=*|\bsk-[A-Za-z0-9_-]{12,}|"
    r"(?:api[_-]?key|access[_-]?token|auth(?:orization)?)\s*[:=]\s*\S+)"
)
_WINDOWS_USER_PATH = re.compile(
    r"[A-Za-z]:\\Users\\[^\\\r\n\"']+\\[^\r\n\"']+"
)
_POSIX_USER_PATH = re.compile(r"/(?:home|Users)/[^/\r\n\"']+/[^\r\n\"']+")


def _redacted_setup(value: dict[str, Any]) -> dict[str, Any]:
    sanitized = _sanitize_setup_value(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_setup_value(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key).casefold()
    if any(part in normalized_key for part in _SENSITIVE_SETUP_KEY_PARTS):
        return None
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            normalized_child_key = str(child_key)
            sanitized = _sanitize_setup_value(
                child_value,
                key=normalized_child_key,
            )
            if sanitized is not None:
                result[normalized_child_key] = sanitized
        return result
    if isinstance(value, (list, tuple)):
        return [
            sanitized
            for item in value
            if (sanitized := _sanitize_setup_value(item)) is not None
        ]
    if isinstance(value, str):
        if _SENSITIVE_SETUP_VALUE.search(value):
            return None
        return _POSIX_USER_PATH.sub(
            "[local path redacted]",
            _WINDOWS_USER_PATH.sub("[local path redacted]", value),
        )
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return None


def _security_key(*, ticker: str, cik: str, exchange: str) -> str:
    normalized_cik = "".join(character for character in cik if character.isdigit())
    if normalized_cik:
        return f"us:cik:{normalized_cik.zfill(10)}"
    return f"us:{exchange.casefold() or 'listed'}:{ticker.casefold()}"


def _ticker_aliases(ticker: str, aliases: Any) -> tuple[str, ...]:
    values = (aliases,) if isinstance(aliases, str) else tuple(aliases or ())
    return tuple(
        dict.fromkeys(
            str(item).strip().upper()
            for item in (ticker, *values)
            if item is not None and str(item).strip()
        )
    )


def _artifact_id(*parts: str) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "saved-" + hashlib.sha256(encoded).hexdigest()[:24]


def _default_operation_label(ticker: str, operation_kind: str) -> str:
    labels = {
        "targeted_investigation": "Targeted investigation",
        "event_investigation": "Event investigation",
        "repair_recheck": "Repair and recheck",
    }
    return f"{labels.get(operation_kind, 'Research')} for {ticker}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
