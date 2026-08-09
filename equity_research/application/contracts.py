from __future__ import annotations

from dataclasses import fields
from typing import Any, TypeVar

from ..domain.research_integrity import (
    AuditFinding,
    CandidateDiscoveryItem,
    ClosureAuditResult,
    EvidenceAdmissionDecision,
    EvidenceRequest,
    PrimaryAuditResponse,
    ResearchRequest,
)
from ..models import RunManifest


T = TypeVar("T")


def research_request_from_payload(payload: dict[str, Any]) -> ResearchRequest:
    version = str(
        payload.get("request_version")
        or payload.get("schema_version")
        or ""
    )
    if not version or version in {"v1", "evidence-request-v1"}:
        legacy = EvidenceRequest(
            request_id=str(payload.get("request_id") or ""),
            question=str(
                payload.get("question")
                or payload.get("objective")
                or "Targeted evidence request"
            ),
            source_types=_strings(
                payload.get("source_types")
                or payload.get("required_source_families")
            ),
            required_fields=_strings(payload.get("required_fields")),
            priority=str(payload.get("priority") or "Medium"),
            affected_claim_ids=_strings(payload.get("affected_claim_ids")),
            status=str(payload.get("status") or "planned"),
        )
        return legacy.as_research_request()
    return _construct(ResearchRequest, payload)


def evidence_request_from_payload(payload: dict[str, Any]) -> EvidenceRequest:
    return research_request_from_payload(payload).to_evidence_request()


def candidate_discovery_from_payload(
    payload: dict[str, Any],
) -> CandidateDiscoveryItem:
    migrated = dict(payload)
    migrated.setdefault("candidate_type", "source_candidate")
    migrated.setdefault("status", "quarantined")
    migrated.setdefault("objective", migrated.get("title") or "Research lead")
    migrated.setdefault("origin_role", migrated.get("discovery_method") or "legacy_discovery")
    migrated.setdefault("ticker", "")
    migrated.setdefault("candidate_id", "")
    migrated["admitted_evidence_ids"] = tuple(
        migrated.get("admitted_evidence_ids") or ()
    )
    migrated["status_history"] = tuple(migrated.get("status_history") or ())
    if migrated["status"] != "admitted":
        migrated["admitted_evidence_ids"] = ()
    return _construct(CandidateDiscoveryItem, migrated)


def evidence_admission_from_payload(
    payload: dict[str, Any],
) -> EvidenceAdmissionDecision:
    return _construct(EvidenceAdmissionDecision, payload)


def audit_finding_from_payload(payload: dict[str, Any]) -> AuditFinding:
    return _construct(AuditFinding, payload)


def primary_audit_response_from_payload(
    payload: dict[str, Any],
) -> PrimaryAuditResponse:
    migrated = dict(payload)
    evidence_ids = _strings(migrated.get("evidence_ids"))
    state = str(migrated.get("state") or migrated.get("status") or "unresolved")
    if state == "rejected_with_evidence" and not evidence_ids:
        state = "unresolved"
        migrated["explanation"] = (
            str(migrated.get("explanation") or "")
            + " Legacy unsupported rejection migrated to unresolved."
        ).strip()
    migrated["state"] = state
    migrated["evidence_ids"] = evidence_ids
    return _construct(PrimaryAuditResponse, migrated)


def closure_audit_from_payload(payload: dict[str, Any]) -> ClosureAuditResult:
    migrated = dict(payload)
    state = str(migrated.get("state") or migrated.get("closure_state") or "")
    state = {
        "passed": "closed",
        "passed_with_gaps": "closed_with_residual_risk",
        "failed": "failed_material_findings_unaddressed",
        "not_run": "indeterminate_provider_or_evidence_failure",
    }.get(state, state or "indeterminate_provider_or_evidence_failure")
    migrated["state"] = state
    migrated.setdefault("summary", "Migrated closure result.")
    return _construct(ClosureAuditResult, migrated)


def run_manifest_from_payload(payload: dict[str, Any]) -> RunManifest:
    """Load historical manifests while retaining all new integrity defaults."""
    migrated = dict(payload)
    migrated.setdefault("run_id", "")
    migrated.setdefault("generated_at", "")
    migrated.setdefault("app_version", "unknown")
    migrated.setdefault("parser_versions", {})
    migrated.setdefault("source_urls", [])
    migrated.setdefault("assumptions", [])
    migrated.setdefault("data_gaps", [])
    return _construct(RunManifest, migrated)


def _construct(cls: type[T], payload: dict[str, Any]) -> T:
    allowed = {item.name for item in fields(cls)}
    return cls(**{
        key: value for key, value in payload.items()
        if key in allowed
    })


def _strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item) for item in value if item]
