from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, is_dataclass, replace
from datetime import date
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
from typing import Literal, Mapping, Optional, Sequence, Union

import pytest

from equity_research.application.saved_research import (
    SavedResearchLoadError,
    SavedResearchService,
    build_displayed_research_context,
    should_auto_load_saved_result,
)
from equity_research.domain.saved_research import (
    ArtifactVerificationResult,
    SaveResearchResultCommand,
    SavedArtifactHealth,
    SavedResearchQuery,
    SavedResearchStatus,
)
from equity_research.hybrid_research import HybridResearchJobManager
from equity_research.infrastructure.saved_research_codec import (
    JsonResearchResultArtifactCodec,
    SavedResearchCodecError,
    _resolved_type_hints,
)
import equity_research.infrastructure.saved_research_codec as saved_codec
from equity_research.infrastructure.saved_research_repository import (
    ContentAddressedGzipBlobStore,
    InMemorySavedResearchBlobStore,
    InMemorySavedResearchRepository,
    SavedResearchStorageError,
    SqliteSavedResearchRepository,
)
from equity_research.presentation.view_models import (
    ResearchSubroute,
    build_decision_brief_view_model,
    build_evidence_page_view_model,
    build_integrity_timeline_view_model,
    build_research_route_view_model,
    build_saved_research_library_view_model,
)
from equity_research.sample_data import demo_result
from equity_research.models import BullBearJudgePanel, JudgeResolutionItem
import equity_research.presentation.streamlit_app as streamlit_app


class _CodecFixtureStatus(str, Enum):
    READY = "ready"


@dataclass
class _CodecCompatibilityFixture:
    union_value: int | None
    optional_text: str | None
    classic_optional: Optional[int]
    classic_union: Union[int, str]
    literal_value: Literal["ready", "failed"]
    nested: dict[str, list[int | None]]
    mapping_value: Mapping[str, int]
    sequence_value: Sequence[int]
    tuple_value: tuple[str, ...]
    status: _CodecFixtureStatus
    observed_on: date
    path: Path
    payload: bytes


@pytest.fixture(scope="module")
def aapl_result():
    return demo_result("AAPL")


def _service(tmp_path: Path) -> SavedResearchService:
    return SavedResearchService(
        SqliteSavedResearchRepository(tmp_path / "research.db"),
        ContentAddressedGzipBlobStore(tmp_path / "blobs"),
        JsonResearchResultArtifactCodec(),
    )


def test_unavailable_sqlite_parent_raises_typed_storage_error(
    tmp_path: Path,
) -> None:
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("not a directory", encoding="utf-8")

    with pytest.raises(
        SavedResearchStorageError,
        match="metadata storage could not be initialized",
    ):
        SqliteSavedResearchRepository(blocked_parent / "research.db")


def test_local_saved_research_factory_falls_back_to_session_storage(
    tmp_path: Path,
) -> None:
    blocked_parent = tmp_path / "blocked-local-storage"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    streamlit_app._local_saved_research_service.clear()
    try:
        service = streamlit_app._local_saved_research_service(
            str(blocked_parent / "research.db"),
            str(blocked_parent),
        )
    finally:
        streamlit_app._local_saved_research_service.clear()

    assert service.persistence_available is False
    assert isinstance(service.repository, InMemorySavedResearchRepository)
    assert "could not be initialized" in service.persistence_error


def test_python39_resolves_quoted_nested_dataclass_arguments() -> None:
    annotation = _resolved_type_hints(BullBearJudgePanel)["resolution_plan"]

    assert annotation == list[JudgeResolutionItem]


def _save(
    service: SavedResearchService,
    result,
    *,
    run_id: str,
    status: SavedResearchStatus = SavedResearchStatus.COMPLETED,
    completed_at: str = "2026-08-10T20:00:00+00:00",
    scope_id: str = "local",
):
    return service.save_result(
        SaveResearchResultCommand(
            result=result,
            run_id=run_id,
            operation_kind="targeted_investigation",
            operation_label="Test AAPL cash conversion",
            outcome="resolved" if status is SavedResearchStatus.COMPLETED else "partial",
            status=status,
            completed_at=completed_at,
            setup_summary={
                "ticker": "AAPL",
                "profile": "deep_initiation",
                "credential_lease_ids": ["must-not-persist"],
            },
            scope_id=scope_id,
        )
    )


def test_complete_result_round_trips_and_renders_without_research_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    aapl_result,
) -> None:
    service = _service(tmp_path)
    artifact = _save(service, aapl_result, run_id="aapl-completed")

    def forbidden(*args, **kwargs):
        raise AssertionError("loading saved research must not execute research")

    monkeypatch.setattr("equity_research.pipeline.run_us_equity_research", forbidden)
    loaded = service.load_result(artifact.artifact_id, scope_id="local")
    view_model = build_decision_brief_view_model(loaded.result)

    assert loaded.result.identity.ticker == "AAPL"
    assert view_model.ticker == "AAPL"
    assert loaded.artifact.operation_label == "Test AAPL cash conversion"
    assert loaded.artifact.health is SavedArtifactHealth.VERIFIED
    assert "credential_lease_ids" not in loaded.artifact.setup_summary
    assert build_evidence_page_view_model(loaded.result).admitted_count >= 0
    assert build_integrity_timeline_view_model(loaded.result).status
    for route in ResearchSubroute:
        assert build_research_route_view_model(loaded.result, route).route is route


def test_displayed_context_uses_saved_ids_and_safe_legacy_cash_alias(
    tmp_path: Path,
    aapl_result,
) -> None:
    service = _service(tmp_path)
    artifact = service.save_result(
        SaveResearchResultCommand(
            result=aapl_result,
            run_id="cash-context",
            operation_kind="action_investigation",
            operation_label="Test recurring cash conversion",
            setup_summary={
                "investigation_action_id": "cash-action",
                "investigation_action_kind": "cash_conversion_analysis",
            },
            scope_id="local",
        )
    )

    context = build_displayed_research_context(artifact)

    assert context.operation_action_id == "cash-action"
    assert context.research_intent_id == (
        "cash_generation_capital_allocation:recurring_cash_conversion"
    )
    assert context.operation_label == "Test recurring cash conversion"


def test_existing_verified_derived_cash_revision_is_loaded_before_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    aapl_result,
) -> None:
    service = _service(tmp_path)
    parent_result = deepcopy(aapl_result)
    parent_result.cash_conversion_recurrence = None
    parent_result.hybrid_research.cash_conversion_recurrence = None
    parent = service.save_result(SaveResearchResultCommand(
        result=parent_result,
        run_id="cash-parent",
        operation_kind="action_investigation",
        operation_label="Test AAPL cash conversion",
        setup_summary={"investigation_action_kind": "cash_conversion_analysis"},
        scope_id="local",
    ))
    derived = service.save_result(SaveResearchResultCommand(
        result=deepcopy(aapl_result),
        run_id="cash-parent",
        operation_kind="action_investigation",
        operation_label="Test AAPL cash conversion",
        setup_summary={"research_intent_id": "cash_generation_capital_allocation:recurring_cash_conversion"},
        scope_id="local",
        parent_artifact_id=parent.artifact_id,
    ))
    monkeypatch.setattr(
        streamlit_app,
        "_saved_research_service_for_scope",
        lambda _scope: service,
    )

    loaded = streamlit_app._load_verified_cash_conversion_revision(parent)

    assert loaded is not None
    assert loaded.artifact.artifact_id == derived.artifact_id
    assert streamlit_app._has_current_cash_conversion_bridge(loaded.result)


def test_save_verifies_before_creating_loadable_metadata(
    tmp_path: Path,
    aapl_result,
) -> None:
    real_codec = JsonResearchResultArtifactCodec()

    class FailingVerificationCodec:
        def encode(self, result):
            return real_codec.encode(result)

        def decode(self, payload):
            return real_codec.decode(payload)

        def verify(self, payload):
            raise SavedResearchCodecError(
                "Unsupported annotation shape.",
                reason="runtime_incompatible",
                failure_path="ResearchResult.example.field",
                annotation="Example | None",
            )

    service = SavedResearchService(
        SqliteSavedResearchRepository(tmp_path / "research.db"),
        ContentAddressedGzipBlobStore(tmp_path / "blobs"),
        FailingVerificationCodec(),
    )

    with pytest.raises(SavedResearchLoadError) as error:
        _save(service, aapl_result, run_id="verification-fails")

    assert error.value.reason == "runtime_incompatible"
    assert error.value.failure_path == "ResearchResult.example.field"
    assert service.list_results(SavedResearchQuery(scope_id="local")) == []
    assert not list((tmp_path / "blobs").rglob("*.json.gz"))


def test_latest_completed_skips_known_unhealthy_artifact(
    tmp_path: Path,
    aapl_result,
) -> None:
    service = _service(tmp_path)
    older = _save(
        service,
        aapl_result,
        run_id="healthy-older",
        completed_at="2026-08-10T18:00:00+00:00",
    )
    newer = _save(
        service,
        aapl_result,
        run_id="unhealthy-newer",
        completed_at="2026-08-10T21:00:00+00:00",
    )
    service.repository.update_health(
        newer.artifact_id,
        scope_id="local",
        verification=ArtifactVerificationResult(
            status=SavedArtifactHealth.RUNTIME_INCOMPATIBLE,
            reason="runtime_incompatible",
            failure_path="ResearchResult.example.field",
        ),
    )

    loaded = service.load_latest_completed(scope_id="local", ticker="AAPL")

    assert loaded is not None
    assert loaded.artifact.artifact_id == older.artifact_id


def test_unchecked_legacy_artifact_is_verified_lazily_without_blob_rewrite(
    tmp_path: Path,
    aapl_result,
) -> None:
    service = _service(tmp_path)
    artifact = _save(service, aapl_result, run_id="legacy-unchecked")
    blob = tmp_path / "blobs" / Path(artifact.payload_reference)
    original_bytes = blob.read_bytes()
    service.repository.update_health(
        artifact.artifact_id,
        scope_id="local",
        verification=ArtifactVerificationResult(
            status=SavedArtifactHealth.UNCHECKED,
        ),
    )

    loaded = service.load_result(artifact.artifact_id, scope_id="local")

    assert loaded.artifact.health is SavedArtifactHealth.VERIFIED
    assert blob.read_bytes() == original_bytes


def test_setup_summary_recursively_removes_credentials(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    artifact = service.save_result(
        SaveResearchResultCommand(
            result=aapl_result,
            run_id="redacted-setup",
            scope_id="local",
            setup_summary={
                "profile": "deep_initiation",
                "provider": {
                    "name": "DeepSeek",
                    "credential_lease_id": "must-not-persist",
                    "api_key": "must-not-persist",
                },
            },
        )
    )

    assert artifact.setup_summary == {
        "profile": "deep_initiation",
        "provider": {"name": "DeepSeek"},
    }


def test_latest_completed_skips_newer_partial_draft(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    completed = _save(
        service,
        aapl_result,
        run_id="completed",
        completed_at="2026-08-10T18:00:00+00:00",
    )
    draft = _save(
        service,
        aapl_result,
        run_id="newer-draft",
        status=SavedResearchStatus.PARTIAL,
        completed_at="2026-08-10T21:00:00+00:00",
    )

    loaded = service.load_latest_completed(scope_id="local", ticker="AAPL")

    assert loaded is not None
    assert loaded.artifact.artifact_id == completed.artifact_id
    assert draft.independent_qa_status == "incomplete"
    assert draft.closure_status == "not_closed"


def test_duplicate_save_is_idempotent_and_blob_is_content_addressed(
    tmp_path: Path,
    aapl_result,
) -> None:
    service = _service(tmp_path)
    first = _save(service, aapl_result, run_id="same-run")
    second = _save(service, aapl_result, run_id="same-run")

    assert second.artifact_id == first.artifact_id
    assert len(service.list_results(SavedResearchQuery(scope_id="local"))) == 1
    assert len(list((tmp_path / "blobs").rglob("*.json.gz"))) == 1


def test_concurrent_content_addressed_writes_share_one_blob(tmp_path: Path) -> None:
    store = ContentAddressedGzipBlobStore(tmp_path / "blobs")
    payload = b"deterministic saved research payload"

    with ThreadPoolExecutor(max_workers=8) as workers:
        references = list(workers.map(lambda _: store.put(payload), range(24)))

    assert len({item[0] for item in references}) == 1
    assert len(list((tmp_path / "blobs").rglob("*.json.gz"))) == 1


def test_metadata_listing_is_lazy_and_does_not_read_blob(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    _save(service, aapl_result, run_id="lazy-list")

    def forbidden(*args, **kwargs):
        raise AssertionError("metadata listing loaded a result payload")

    service.blob_store.get = forbidden
    artifacts = service.list_results(SavedResearchQuery(scope_id="local"))
    view_model = build_saved_research_library_view_model(
        artifacts,
        current_ticker="AAPL",
        session_only=False,
    )

    assert len(view_model.completed) == 1
    assert not view_model.drafts


def test_corrupt_blob_fails_readably_without_removing_metadata(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    artifact = _save(service, aapl_result, run_id="corrupt")
    target = tmp_path / "blobs" / Path(artifact.payload_reference)
    target.write_bytes(b"not gzip")

    with pytest.raises(SavedResearchLoadError) as error:
        service.load_result(artifact.artifact_id, scope_id="local")
    assert error.value.reason == "payload_corrupt"
    assert service.repository.get(artifact.artifact_id, scope_id="local") is not None


def test_python39_annotation_fallback_restores_typed_nested_dataclasses(
    aapl_result,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    codec = JsonResearchResultArtifactCodec()
    payload = codec.encode(aapl_result)
    saved_codec._resolved_type_hints.cache_clear()
    monkeypatch.setattr(saved_codec, "_UNION_TYPE", None)
    monkeypatch.setattr(
        saved_codec,
        "get_type_hints",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("PEP 604 unavailable")),
    )

    restored = codec.decode(payload)

    assert is_dataclass(restored)
    assert is_dataclass(restored.identity)
    assert not isinstance(restored.identity, dict)
    if restored.events:
        assert is_dataclass(restored.events[0])
        assert not isinstance(restored.events[0], dict)
    saved_codec._resolved_type_hints.cache_clear()


def test_restricted_annotation_resolver_supports_compatibility_matrix_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved_codec._resolved_type_hints.cache_clear()
    monkeypatch.setattr(saved_codec, "_UNION_TYPE", None)
    monkeypatch.setattr(
        saved_codec,
        "get_type_hints",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError("fallback")),
    )
    restored = saved_codec._decode_value(
        {
            "union_value": 7,
            "optional_text": None,
            "classic_optional": None,
            "classic_union": "seven",
            "literal_value": "ready",
            "nested": {"values": [1, None, 3]},
            "mapping_value": {"value": 7},
            "sequence_value": [1, 2, 3],
            "tuple_value": ["a", "b"],
            "status": "ready",
            "observed_on": "2026-08-10",
            "path": "fixtures/aapl.json",
            "payload": {"__bytes__": "YWFwbA=="},
        },
        _CodecCompatibilityFixture,
    )

    assert restored.union_value == 7
    assert restored.classic_optional is None
    assert restored.classic_union == "seven"
    assert restored.nested == {"values": [1, None, 3]}
    assert restored.mapping_value == {"value": 7}
    assert restored.sequence_value == [1, 2, 3]
    assert restored.tuple_value == ("a", "b")
    assert restored.status is _CodecFixtureStatus.READY
    assert restored.observed_on == date(2026, 8, 10)
    assert restored.path == Path("fixtures/aapl.json")
    assert restored.payload == b"aapl"
    saved_codec._resolved_type_hints.cache_clear()


def test_saved_load_error_reasons_are_controlled(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    artifact = _save(service, aapl_result, run_id="unsupported-schema")
    target = tmp_path / "blobs" / Path(artifact.payload_reference)
    import gzip

    envelope = json.loads(gzip.decompress(target.read_bytes()).decode("utf-8"))
    envelope["schema_version"] = "future-schema"
    raw = json.dumps(envelope).encode("utf-8")
    # Store through the blob abstraction and point metadata at the altered blob.
    reference, sha256, compressed_size = service.blob_store.put(raw)
    modified = replace(
        artifact,
        artifact_id=artifact.artifact_id + "-unsupported-schema",
        payload_reference=reference,
        payload_sha256=sha256,
        compressed_size_bytes=compressed_size,
    )
    service.repository.save(modified)

    with pytest.raises(SavedResearchLoadError) as error:
        service.load_result(modified.artifact_id, scope_id="local")
    assert error.value.reason == "schema_unsupported"


def test_codec_rejects_credential_like_tokens(aapl_result) -> None:
    unsafe = deepcopy(aapl_result)
    unsafe.run_manifest.assumptions.append("temporary sk-abcdefghijklmnopqrstuv")

    with pytest.raises(SavedResearchCodecError, match="credential-like"):
        JsonResearchResultArtifactCodec().encode(unsafe)


def test_codec_rejects_nested_non_persistable_licensed_content(aapl_result) -> None:
    unsafe = deepcopy(aapl_result)
    unsafe.run_manifest.assumptions.append(
        {
            "licensing_policy": "do_not_store",
            "provider_payload": {"full_text": "licensed transcript"},
        }
    )

    with pytest.raises(SavedResearchCodecError, match="not licensed"):
        JsonResearchResultArtifactCodec().encode(unsafe)


def test_codec_redacts_user_specific_paths(aapl_result) -> None:
    unsafe = deepcopy(aapl_result)
    unsafe.run_manifest.assumptions.append(
        r"Imported from C:\Users\Example User\private-workspace\input.csv"
    )

    payload = JsonResearchResultArtifactCodec().encode(unsafe).decode("utf-8")

    assert "Example User" not in payload
    assert "[local path redacted]" in payload


def test_hosted_memory_repository_is_scope_isolated(aapl_result) -> None:
    service = SavedResearchService(
        InMemorySavedResearchRepository(ttl_seconds=600),
        InMemorySavedResearchBlobStore(),
        JsonResearchResultArtifactCodec(),
    )
    artifact = _save(service, aapl_result, run_id="hosted", scope_id="session-a")

    assert service.repository.get(artifact.artifact_id, scope_id="session-b") is None
    assert not service.list_results(SavedResearchQuery(scope_id="session-b"))
    assert service.load_result(artifact.artifact_id, scope_id="session-a").result.identity.ticker == "AAPL"


def test_hosted_session_artifact_and_payload_expire_together(
    aapl_result,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = {"now": 1_000.0}
    monkeypatch.setattr(
        "equity_research.infrastructure.saved_research_repository.time.time",
        lambda: clock["now"],
    )
    service = SavedResearchService(
        InMemorySavedResearchRepository(ttl_seconds=60),
        InMemorySavedResearchBlobStore(ttl_seconds=60),
        JsonResearchResultArtifactCodec(),
    )
    artifact = _save(service, aapl_result, run_id="expires", scope_id="session-a")

    clock["now"] += 61

    assert service.repository.get(artifact.artifact_id, scope_id="session-a") is None
    with pytest.raises(SavedResearchStorageError, match="expired"):
        service.blob_store.get(
            artifact.payload_reference,
            expected_sha256=artifact.payload_sha256,
        )


def test_pinned_result_survives_delete_until_unpinned(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    artifact = _save(service, aapl_result, run_id="pinned")

    assert service.pin_result(artifact.artifact_id, scope_id="local", pinned=True)
    assert not service.delete_result(artifact.artifact_id, scope_id="local")
    assert service.pin_result(artifact.artifact_id, scope_id="local", pinned=False)
    assert service.delete_result(artifact.artifact_id, scope_id="local")


def test_last_displayed_restores_after_repository_restart(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    artifact = _save(service, aapl_result, run_id="restart")
    service.remember_displayed(artifact.artifact_id, scope_id="local")

    restarted = _service(tmp_path)
    loaded = restarted.load_last_displayed(scope_id="local")

    assert loaded is not None
    assert loaded.artifact.artifact_id == artifact.artifact_id
    assert loaded.result.identity.ticker == "AAPL"


def test_cik_identity_and_ticker_aliases_work_across_symbol_changes(
    tmp_path: Path,
    aapl_result,
) -> None:
    service = _service(tmp_path)
    renamed = deepcopy(aapl_result)
    renamed.identity = replace(renamed.identity, ticker="APPLX")
    artifact = service.save_result(
        SaveResearchResultCommand(
            result=renamed,
            run_id="renamed-security",
            scope_id="local",
            setup_summary={"ticker_aliases": ["AAPL"]},
        )
    )

    matches = service.list_results(
        SavedResearchQuery(scope_id="local", ticker="AAPL")
    )

    assert artifact.security_key == "us:cik:0000320193"
    assert [item.artifact_id for item in matches] == [artifact.artifact_id]


def test_codec_ignores_unknown_future_fields(aapl_result) -> None:
    codec = JsonResearchResultArtifactCodec()
    envelope = json.loads(codec.encode(aapl_result).decode("utf-8"))
    envelope["payload"]["future_optional_section"] = {"value": 1}

    loaded = codec.decode(json.dumps(envelope).encode("utf-8"))

    assert loaded.identity.ticker == "AAPL"


def test_saved_metadata_ignores_unknown_future_fields(tmp_path: Path, aapl_result) -> None:
    service = _service(tmp_path)
    artifact = _save(service, aapl_result, run_id="future-metadata")
    import sqlite3

    with sqlite3.connect(tmp_path / "research.db") as database:
        row = database.execute(
            "SELECT artifact_json FROM saved_research_artifacts WHERE artifact_id=?",
            (artifact.artifact_id,),
        ).fetchone()
        payload = json.loads(row[0])
        payload["future_optional_field"] = {"value": 1}
        database.execute(
            "UPDATE saved_research_artifacts SET artifact_json=? WHERE artifact_id=?",
            (json.dumps(payload), artifact.artifact_id),
        )

    restored = service.repository.get(artifact.artifact_id, scope_id="local")
    assert restored is not None
    assert restored.artifact_id == artifact.artifact_id


def test_completed_investigation_survives_restart_with_updated_action_status(
    tmp_path: Path,
    aapl_result,
) -> None:
    updated = deepcopy(aapl_result)
    original = updated.prioritized_research_queue[0]
    updated.prioritized_research_queue[0] = replace(
        original,
        status="resolved",
        attempt_summary="Cash-conversion evidence admitted and reviewed.",
        resolution_summary="The targeted investigation resolved this action.",
    )
    service = _service(tmp_path)
    artifact = _save(service, updated, run_id="resolved-investigation")

    restarted = _service(tmp_path)
    loaded = restarted.load_result(artifact.artifact_id, scope_id="local")
    restored_action = loaded.result.prioritized_research_queue[0]

    assert restored_action.status == "resolved"
    assert "resolved" in restored_action.resolution_summary.casefold()


class _LifecycleRecorder:
    def __init__(self) -> None:
        self.result_reference = ""

    def mark_running(self, *args, **kwargs) -> None:
        pass

    def finish(self, run_id: str, **kwargs) -> None:
        self.result_reference = str(kwargs.get("result_reference") or "")


def test_background_manager_persists_before_lifecycle_completion(aapl_result) -> None:
    lifecycle = _LifecycleRecorder()
    calls: list[str] = []

    def persist(state) -> str:
        calls.append(state.result_terminal_status)
        return "saved-background-artifact"

    manager = HybridResearchJobManager(lifecycle, result_persister=persist)
    state = manager.start("AAPL", lambda cancel: aapl_result, job_id="background-run")
    deadline = time.time() + 5
    while state.status in {"queued", "running", "saving_result"} and time.time() < deadline:
        time.sleep(0.01)

    assert state.status == "completed"
    assert state.saved_artifact_id == "saved-background-artifact"
    assert calls == ["completed"]
    assert lifecycle.result_reference == "saved-background-artifact"


def test_background_save_failure_keeps_result_without_false_durable_reference(
    aapl_result,
) -> None:
    lifecycle = _LifecycleRecorder()

    def persist(_state) -> str:
        raise SavedResearchCodecError(
            "Writer-runtime verification failed.",
            reason="annotation_unresolved",
            failure_path="ResearchResult.qa.findings",
            annotation="list[AuditFinding | None]",
        )

    manager = HybridResearchJobManager(lifecycle, result_persister=persist)
    state = manager.start("AAPL", lambda cancel: aapl_result, job_id="unsaved-run")
    deadline = time.time() + 5
    while state.status in {"queued", "running", "saving_result"} and time.time() < deadline:
        time.sleep(0.01)

    assert state.status == "completed"
    assert state.result is aapl_result
    assert state.saved_artifact_id == ""
    assert state.result_save_error
    assert state.result_save_error_reason == "annotation_unresolved"
    assert state.result_save_failure_path == "ResearchResult.qa.findings"
    assert state.result_save_annotation == "list[AuditFinding | None]"
    assert lifecycle.result_reference == ""


def test_completed_background_result_does_not_replace_unrelated_research() -> None:
    assert should_auto_load_saved_result(
        displayed_run_id="origin-run",
        originating_run_id="origin-run",
    )
    assert not should_auto_load_saved_result(
        displayed_run_id="different-result-opened-by-user",
        originating_run_id="origin-run",
    )
    assert should_auto_load_saved_result(
        displayed_run_id="shared-run",
        originating_run_id="shared-run",
        displayed_artifact_id="origin-artifact",
        originating_artifact_id="origin-artifact",
    )
    assert not should_auto_load_saved_result(
        displayed_run_id="shared-run",
        originating_run_id="shared-run",
        displayed_artifact_id="different-revision-of-shared-run",
        originating_artifact_id="origin-artifact",
    )
