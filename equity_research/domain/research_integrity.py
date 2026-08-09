from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Any, Iterable
from urllib.parse import urlsplit


class CandidateType(str, Enum):
    EVIDENCE = "evidence_candidate"
    CONTRADICTION = "contradiction_candidate"
    MAPPING = "mapping_candidate"
    SOURCE = "source_candidate"
    EXPERT_SPECIALIST = "expert_specialist_candidate"


class CandidateStatus(str, Enum):
    DISCOVERED = "discovered"
    QUARANTINED = "quarantined"
    RETRIEVAL_REQUESTED = "retrieval_requested"
    RETRIEVAL_FAILED = "retrieval_failed"
    ADMISSION_PENDING = "admission_pending"
    ADMITTED = "admitted"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PrimaryResponseState(str, Enum):
    ACCEPTED = "accepted"
    REJECTED_WITH_EVIDENCE = "rejected_with_evidence"
    UNRESOLVED = "unresolved"


class ClosureState(str, Enum):
    CLOSED = "closed"
    CLOSED_WITH_RESIDUAL_RISK = "closed_with_residual_risk"
    FAILED_MATERIAL_FINDINGS_UNADDRESSED = "failed_material_findings_unaddressed"
    INDETERMINATE_PROVIDER_OR_EVIDENCE_FAILURE = "indeterminate_provider_or_evidence_failure"


_ALLOWED_TRANSITIONS = {
    CandidateStatus.DISCOVERED: {CandidateStatus.QUARANTINED, CandidateStatus.REJECTED},
    CandidateStatus.QUARANTINED: {
        CandidateStatus.RETRIEVAL_REQUESTED,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
    },
    CandidateStatus.RETRIEVAL_REQUESTED: {
        CandidateStatus.RETRIEVAL_FAILED,
        CandidateStatus.ADMISSION_PENDING,
        CandidateStatus.EXPIRED,
    },
    CandidateStatus.RETRIEVAL_FAILED: {
        CandidateStatus.RETRIEVAL_REQUESTED,
        CandidateStatus.REJECTED,
        CandidateStatus.EXPIRED,
    },
    CandidateStatus.ADMISSION_PENDING: {
        CandidateStatus.ADMITTED,
        CandidateStatus.REJECTED,
    },
    CandidateStatus.ADMITTED: set(),
    CandidateStatus.REJECTED: set(),
    CandidateStatus.EXPIRED: set(),
}


@dataclass(frozen=True)
class CandidateStatusEvent:
    status: str
    changed_at: str
    reason: str = ""


@dataclass(frozen=True)
class CandidateDiscoveryItem:
    candidate_id: str
    candidate_type: str
    status: str
    ticker: str
    objective: str
    origin_role: str
    candidate_version: str = "candidate-discovery-v1"
    origin_provider: str = ""
    research_request_id: str = ""
    url: str = ""
    title: str = ""
    snippet: str = ""
    proposed_source_family: str = ""
    proposed_mapping: str = ""
    company_or_expert: str = ""
    materiality: str = "Medium"
    discovered_at: str = ""
    status_history: tuple[CandidateStatusEvent, ...] = ()
    connector_id: str = ""
    admission_decision_id: str = ""
    admitted_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        CandidateType(self.candidate_type)
        status = CandidateStatus(self.status)
        if status != CandidateStatus.ADMITTED and self.admitted_evidence_ids:
            raise ValueError("Only an admitted candidate may reference an evidence ID.")
        if status == CandidateStatus.ADMITTED and (
            not self.admission_decision_id or not self.admitted_evidence_ids
        ):
            raise ValueError("An admitted candidate requires a gateway decision and evidence ID.")


@dataclass
class ResearchRequest:
    request_id: str
    objective: str
    request_version: str = "research-request-v2"
    requested_by_role: str = "independent_research_auditor"
    required_source_families: list[str] = field(default_factory=list)
    desired_confirming_evidence: list[str] = field(default_factory=list)
    desired_contradicting_evidence: list[str] = field(default_factory=list)
    scope: dict[str, Any] = field(default_factory=dict)
    priority: str = "Medium"
    materiality: str = "Medium"
    affected_claim_ids: list[str] = field(default_factory=list)
    affected_finding_ids: list[str] = field(default_factory=list)
    preferred_connectors: list[str] = field(default_factory=list)
    stopping_rule: str = "Stop after one sufficient confirming and one sufficient contradicting source, or when unavailable."
    status: str = "planned"
    attempted_connector_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    admitted_evidence_ids: list[str] = field(default_factory=list)
    resolution_summary: str = ""

    @classmethod
    def from_evidence_request(cls, request: "EvidenceRequest") -> "ResearchRequest":
        return cls(
            request_id=request.request_id,
            objective=request.question,
            required_source_families=list(request.source_types),
            priority=request.priority,
            affected_claim_ids=list(request.affected_claim_ids),
            status=request.status,
            scope={"required_fields": list(request.required_fields)},
        )

    def to_evidence_request(self) -> "EvidenceRequest":
        return EvidenceRequest(
            request_id=self.request_id,
            question=self.objective,
            source_types=list(self.required_source_families),
            required_fields=[
                str(value) for value in self.scope.get("required_fields", []) if value
            ],
            priority=self.priority,
            affected_claim_ids=list(self.affected_claim_ids),
            status=self.status,
        )


@dataclass
class EvidenceRequest:
    """Backward-compatible v1 request retained for stored artifacts."""

    request_id: str
    question: str
    source_types: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    priority: str = "Medium"
    affected_claim_ids: list[str] = field(default_factory=list)
    status: str = "planned"

    def as_research_request(self) -> ResearchRequest:
        return ResearchRequest.from_evidence_request(self)


@dataclass
class EvidenceAdmissionDecision:
    evidence_id: str
    status: str
    reason: str
    source_family: str = ""
    provider: str = ""
    licensing_policy: str = ""
    freshness: str = "Unknown"
    no_lookahead_status: str = "Unknown"
    admitted_at: str = ""
    decision_id: str = ""
    candidate_id: str = ""
    origin_role: str = ""
    origin_provider: str = ""
    retrieval_status: str = "not_attempted"
    parser_status: str = "not_parsed"
    deduplication_status: str = "not_checked"
    timestamp_validation_status: str = "not_checked"
    entitlement_status: str = "not_checked"
    provenance_status: str = "not_checked"
    prompt_injection_quarantined: bool = False
    gateway_version: str = "evidence-admission-gateway-v1"
    pipeline_completed: bool = False


@dataclass
class AuditFinding:
    finding_id: str
    category: str
    severity: str
    summary: str
    finding_version: str = "audit-finding-v1"
    status: str = "open"
    evidence_ids: list[str] = field(default_factory=list)
    evidence_request_ids: list[str] = field(default_factory=list)
    affected_claim_ids: list[str] = field(default_factory=list)
    proposed_change: str = ""
    frozen_evidence_hash: str = ""
    source_policy_fingerprint: str = ""
    materiality: str = "Medium"
    what_would_change_assessment: str = ""
    primary_response_state: str = ""


@dataclass
class PrimaryAuditResponse:
    finding_id: str
    state: str
    explanation: str
    response_version: str = "primary-audit-response-v1"
    evidence_ids: list[str] = field(default_factory=list)
    conclusion_changed: bool = False

    def __post_init__(self) -> None:
        state = PrimaryResponseState(self.state)
        if state == PrimaryResponseState.REJECTED_WITH_EVIDENCE and not self.evidence_ids:
            raise ValueError("rejected_with_evidence requires at least one admitted evidence ID.")
        if state == PrimaryResponseState.ACCEPTED and not self.conclusion_changed:
            raise ValueError(
                "accepted requires a revision, qualification, or conclusion removal."
            )


@dataclass
class AuditResolution:
    finding_id: str
    status: str
    resolution: str
    evidence_ids: list[str] = field(default_factory=list)
    conclusion_changed: bool = False
    primary_response_state: str = ""


@dataclass
class ClosureAuditResult:
    state: str
    summary: str
    closure_version: str = "closure-audit-v1"
    checked_finding_ids: list[str] = field(default_factory=list)
    unaddressed_material_finding_ids: list[str] = field(default_factory=list)
    contrary_evidence_ids: list[str] = field(default_factory=list)
    ignored_contrary_evidence_ids: list[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    completed_at: str = ""

    def __post_init__(self) -> None:
        state = ClosureState(self.state)
        if (
            self.unaddressed_material_finding_ids
            and state in {ClosureState.CLOSED, ClosureState.CLOSED_WITH_RESIDUAL_RISK}
        ):
            raise ValueError("Closure cannot pass with an unaddressed material finding.")
        if self.ignored_contrary_evidence_ids and state == ClosureState.CLOSED:
            raise ValueError("Closure cannot pass while contrary evidence is ignored.")


@dataclass(frozen=True)
class RetrievedCandidateDocument:
    candidate_id: str
    connector_id: str
    canonical_url: str
    content: str
    source_family: str
    source_type: str
    publisher: str
    publication_timestamp: str
    retrieval_timestamp: str
    licensing_policy: str
    provenance: str
    entitled: bool = True
    parser_status: str = "parsed"


@dataclass(frozen=True)
class EvidenceAdmissionPolicy:
    enabled_source_families: tuple[str, ...] = ()
    disabled_source_families: tuple[str, ...] = ()
    allowed_licensing_policies: tuple[str, ...] = ()
    data_cutoff: str = ""
    minimum_content_length: int = 20


@dataclass(frozen=True)
class CandidateAdmissionResult:
    candidate: CandidateDiscoveryItem
    decision: EvidenceAdmissionDecision
    evidence: dict[str, Any] | None = None


class EvidenceAdmissionGateway:
    """The sole authority for turning a retrieved candidate into evidence."""

    version = "evidence-admission-gateway-v1"
    _injection_patterns = (
        re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I),
        re.compile(r"system\s+prompt", re.I),
        re.compile(r"reveal\s+(?:the\s+)?(?:api\s+key|secret|credentials)", re.I),
        re.compile(r"execute\s+(?:this\s+)?(?:command|code)", re.I),
    )

    def __init__(self, seen_content_fingerprints: Iterable[str] = ()) -> None:
        self._seen = set(seen_content_fingerprints)

    def admit(
        self,
        candidate: CandidateDiscoveryItem,
        document: RetrievedCandidateDocument,
        policy: EvidenceAdmissionPolicy,
    ) -> CandidateAdmissionResult:
        if candidate.candidate_id != document.candidate_id:
            raise ValueError("Retrieved content must be bound to the candidate being evaluated.")
        current = CandidateStatus(candidate.status)
        if current == CandidateStatus.RETRIEVAL_REQUESTED:
            candidate = transition_candidate(
                candidate,
                CandidateStatus.ADMISSION_PENDING,
                "Registered retrieval completed; deterministic admission checks started.",
            )
        elif current != CandidateStatus.ADMISSION_PENDING:
            raise ValueError("Candidate must be retrieval_requested or admission_pending before admission.")

        reasons: list[str] = []
        scheme = urlsplit(document.canonical_url).scheme.casefold()
        retrieval_status = "retrieved" if document.content else "retrieval_failed"
        parser_status = document.parser_status or "not_parsed"
        fingerprint = _stable_hash(document.content)
        dedupe_status = "duplicate" if fingerprint in self._seen else "unique"
        timestamp_status = "eligible"
        entitlement_status = "entitled" if document.entitled else "not_entitled"
        provenance_status = "verified" if document.provenance.strip() else "missing"
        injection = any(pattern.search(document.content or "") for pattern in self._injection_patterns)

        if scheme not in {"http", "https"}:
            reasons.append("A canonical HTTP(S) source URL is required.")
        if len(document.content.strip()) < policy.minimum_content_length:
            reasons.append("Retrieved source content is missing or too short to validate.")
        if parser_status != "parsed":
            reasons.append("Registered parser did not complete successfully.")
        if dedupe_status == "duplicate":
            reasons.append("Retrieved content duplicates material already evaluated.")
        if policy.data_cutoff and document.publication_timestamp:
            if document.publication_timestamp[:10] > policy.data_cutoff[:10]:
                timestamp_status = "ineligible"
                reasons.append("Source publication occurs after the run data cutoff.")
        if not document.publication_timestamp:
            timestamp_status = "missing"
            reasons.append("Publication timestamp is required for no-lookahead validation.")
        if not document.entitled:
            reasons.append("The active run is not entitled to use this source.")
        if document.source_family in set(policy.disabled_source_families):
            reasons.append("Source family is disabled by the active source policy.")
        if policy.enabled_source_families and document.source_family not in set(policy.enabled_source_families):
            reasons.append("Source family is not enabled by the active source policy.")
        if (
            policy.allowed_licensing_policies
            and document.licensing_policy not in set(policy.allowed_licensing_policies)
        ):
            reasons.append("Licensing policy is outside the active allowlist.")
        if any(token in document.licensing_policy.casefold() for token in ("prohibited", "unlicensed", "do_not_use")):
            reasons.append("Licensing policy prohibits evidence admission.")
        if provenance_status != "verified" or not document.publisher.strip():
            reasons.append("Publisher identity and provenance must be verified.")
        if injection:
            reasons.append("Instruction-like source content is quarantined as untrusted data.")

        admitted = not reasons
        evidence_id = _stable_hash({
            "candidate_id": candidate.candidate_id,
            "url": document.canonical_url,
            "content_fingerprint": fingerprint,
            "publication_timestamp": document.publication_timestamp,
            "gateway_version": self.version,
        })[:24] if admitted else ""
        status = "admitted" if admitted else "rejected"
        decision_id = _stable_hash({
            "candidate_id": candidate.candidate_id,
            "status": status,
            "reasons": reasons,
            "fingerprint": fingerprint,
        })[:24]
        decision = EvidenceAdmissionDecision(
            evidence_id=evidence_id,
            status=status,
            reason="Admitted by the deterministic evidence gateway." if admitted else "; ".join(reasons),
            source_family=document.source_family,
            provider=document.connector_id,
            licensing_policy=document.licensing_policy,
            freshness="Current",
            no_lookahead_status=timestamp_status,
            admitted_at=_utc_now() if admitted else "",
            decision_id=decision_id,
            candidate_id=candidate.candidate_id,
            origin_role=candidate.origin_role,
            origin_provider=candidate.origin_provider,
            retrieval_status=retrieval_status,
            parser_status=parser_status,
            deduplication_status=dedupe_status,
            timestamp_validation_status=timestamp_status,
            entitlement_status=entitlement_status,
            provenance_status=provenance_status,
            prompt_injection_quarantined=injection,
            gateway_version=self.version,
            pipeline_completed=True,
        )
        candidate = transition_candidate(
            candidate,
            CandidateStatus.ADMITTED if admitted else CandidateStatus.REJECTED,
            decision.reason,
            admission_decision_id=decision_id,
            admitted_evidence_ids=(evidence_id,) if admitted else (),
        )
        if admitted:
            self._seen.add(fingerprint)
        evidence = None if not admitted else {
            "evidence_id": evidence_id,
            "candidate_id": candidate.candidate_id,
            "source_url": document.canonical_url,
            "source_family": document.source_family,
            "source_type": document.source_type,
            "provider": document.connector_id,
            "publisher": document.publisher,
            "publication_date": document.publication_timestamp,
            "retrieved_at": document.retrieval_timestamp,
            "licensing_policy": document.licensing_policy,
            "provenance": document.provenance,
            "content_fingerprint": fingerprint,
            "validation_status": "validated",
        }
        return CandidateAdmissionResult(candidate=candidate, decision=decision, evidence=evidence)


def new_candidate(
    *,
    candidate_type: CandidateType | str,
    ticker: str,
    objective: str,
    origin_role: str,
    origin_provider: str = "",
    research_request_id: str = "",
    url: str = "",
    title: str = "",
    snippet: str = "",
    proposed_source_family: str = "",
    proposed_mapping: str = "",
    company_or_expert: str = "",
    materiality: str = "Medium",
) -> CandidateDiscoveryItem:
    candidate_type = CandidateType(candidate_type)
    discovered_at = _utc_now()
    candidate_id = _stable_hash({
        "type": candidate_type.value,
        "ticker": ticker.upper(),
        "objective": objective,
        "origin_role": origin_role,
        "request": research_request_id,
        "url": url,
        "title": title,
    })[:24]
    discovered = CandidateDiscoveryItem(
        candidate_id=candidate_id,
        candidate_type=candidate_type.value,
        status=CandidateStatus.DISCOVERED.value,
        ticker=ticker.upper(),
        objective=objective,
        origin_role=origin_role,
        origin_provider=origin_provider,
        research_request_id=research_request_id,
        url=url,
        title=title,
        snippet=snippet,
        proposed_source_family=proposed_source_family,
        proposed_mapping=proposed_mapping,
        company_or_expert=company_or_expert,
        materiality=materiality,
        discovered_at=discovered_at,
        status_history=(CandidateStatusEvent(CandidateStatus.DISCOVERED.value, discovered_at),),
    )
    return transition_candidate(
        discovered,
        CandidateStatus.QUARANTINED,
        "Candidate metadata is quarantined until registered retrieval and admission.",
    )


def transition_candidate(
    candidate: CandidateDiscoveryItem,
    status: CandidateStatus | str,
    reason: str,
    *,
    connector_id: str | None = None,
    admission_decision_id: str | None = None,
    admitted_evidence_ids: tuple[str, ...] | None = None,
) -> CandidateDiscoveryItem:
    current = CandidateStatus(candidate.status)
    target = CandidateStatus(status)
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid candidate transition: {current.value} -> {target.value}.")
    history = candidate.status_history + (
        CandidateStatusEvent(target.value, _utc_now(), reason),
    )
    return replace(
        candidate,
        status=target.value,
        status_history=history,
        connector_id=candidate.connector_id if connector_id is None else connector_id,
        admission_decision_id=(
            candidate.admission_decision_id
            if admission_decision_id is None else admission_decision_id
        ),
        admitted_evidence_ids=(
            candidate.admitted_evidence_ids
            if admitted_evidence_ids is None else admitted_evidence_ids
        ),
    )


def _stable_hash(value: Any) -> str:
    payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
