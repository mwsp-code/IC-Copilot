from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..domain.research_integrity import (
    CandidateAdmissionResult,
    CandidateDiscoveryItem,
    CandidateStatus,
    EvidenceAdmissionGateway,
    EvidenceAdmissionPolicy,
    ResearchRequest,
    RetrievedCandidateDocument,
    transition_candidate,
)


class RegisteredResearchConnector(Protocol):
    connector_id: str
    supported_source_families: tuple[str, ...]

    def retrieve(
        self,
        request: ResearchRequest,
        candidate: CandidateDiscoveryItem,
    ) -> RetrievedCandidateDocument: ...


@dataclass(frozen=True)
class ConnectorAttempt:
    connector_id: str
    candidate_id: str
    status: str
    message: str = ""


@dataclass
class ResearchRequestExecution:
    request: ResearchRequest
    candidates: list[CandidateDiscoveryItem] = field(default_factory=list)
    attempts: list[ConnectorAttempt] = field(default_factory=list)
    admission_results: list[CandidateAdmissionResult] = field(default_factory=list)
    status: str = "planned"
    stopping_reason: str = ""


class ResearchRequestDispatcher:
    """Model-agnostic orchestration over registered deterministic connectors."""

    def __init__(
        self,
        connectors: list[RegisteredResearchConnector],
        *,
        admission_gateway: EvidenceAdmissionGateway | None = None,
        max_connector_attempts: int = 6,
    ) -> None:
        self.connectors = {item.connector_id: item for item in connectors}
        self.admission_gateway = admission_gateway or EvidenceAdmissionGateway()
        self.max_connector_attempts = max(1, max_connector_attempts)

    def dispatch(
        self,
        request: ResearchRequest,
        candidates: list[CandidateDiscoveryItem],
        policy: EvidenceAdmissionPolicy,
    ) -> ResearchRequestExecution:
        execution = ResearchRequestExecution(
            request=request,
            candidates=list(candidates),
            status="running",
        )
        attempted_connectors: list[str] = []
        admitted_ids: list[str] = []
        candidate_ids: list[str] = []
        for index, original in enumerate(list(execution.candidates)):
            if len(execution.attempts) >= self.max_connector_attempts:
                execution.stopping_reason = "connector_attempt_limit"
                break
            candidate_ids.append(original.candidate_id)
            candidate = original
            connectors = self._eligible_connectors(request, candidate)
            if not connectors:
                execution.attempts.append(ConnectorAttempt(
                    connector_id="",
                    candidate_id=candidate.candidate_id,
                    status="unavailable",
                    message="No registered connector supports the requested source family.",
                ))
                continue
            for connector in connectors:
                if len(execution.attempts) >= self.max_connector_attempts:
                    execution.stopping_reason = "connector_attempt_limit"
                    break
                if CandidateStatus(candidate.status) in {
                    CandidateStatus.QUARANTINED,
                    CandidateStatus.RETRIEVAL_FAILED,
                }:
                    candidate = transition_candidate(
                        candidate,
                        CandidateStatus.RETRIEVAL_REQUESTED,
                        f"Research Request selected registered connector {connector.connector_id}.",
                        connector_id=connector.connector_id,
                    )
                attempted_connectors.append(connector.connector_id)
                try:
                    document = connector.retrieve(request, candidate)
                except Exception as exc:
                    candidate = transition_candidate(
                        candidate,
                        CandidateStatus.RETRIEVAL_FAILED,
                        "Registered connector failed without admitting evidence.",
                    )
                    execution.attempts.append(ConnectorAttempt(
                        connector_id=connector.connector_id,
                        candidate_id=candidate.candidate_id,
                        status="retrieval_failed",
                        message=str(exc)[:500],
                    ))
                    continue
                result = self.admission_gateway.admit(
                    candidate,
                    document,
                    policy,
                )
                candidate = result.candidate
                execution.admission_results.append(result)
                execution.attempts.append(ConnectorAttempt(
                    connector_id=connector.connector_id,
                    candidate_id=candidate.candidate_id,
                    status=result.decision.status,
                    message=result.decision.reason,
                ))
                if result.decision.evidence_id:
                    admitted_ids.append(result.decision.evidence_id)
                break
            execution.candidates[index] = candidate

        request.attempted_connector_ids = list(dict.fromkeys(
            [*request.attempted_connector_ids, *attempted_connectors]
        ))
        request.candidate_ids = list(dict.fromkeys(
            [*request.candidate_ids, *candidate_ids]
        ))
        request.admitted_evidence_ids = list(dict.fromkeys(
            [*request.admitted_evidence_ids, *admitted_ids]
        ))
        if admitted_ids:
            request.status = "evidence_admitted"
            request.resolution_summary = (
                f"{len(admitted_ids)} evidence item(s) passed registered retrieval "
                "and common admission. Downstream analysis must still satisfy the "
                "Research Request stopping rule."
            )
            execution.status = "evidence_admitted"
            execution.stopping_reason = execution.stopping_reason or "admitted_evidence"
        elif execution.attempts:
            request.status = "unavailable"
            request.resolution_summary = (
                "Registered connector attempts produced no admissible evidence."
            )
            execution.status = "unavailable"
            execution.stopping_reason = execution.stopping_reason or "evidence_unavailable"
        else:
            request.status = "not_attempted"
            request.resolution_summary = "No candidate was available for registered retrieval."
            execution.status = "not_attempted"
            execution.stopping_reason = "no_candidates"
        return execution

    def _eligible_connectors(
        self,
        request: ResearchRequest,
        candidate: CandidateDiscoveryItem,
    ) -> list[RegisteredResearchConnector]:
        preferred = [
            self.connectors[item]
            for item in request.preferred_connectors
            if item in self.connectors
        ]
        remaining = [
            item for key, item in self.connectors.items()
            if key not in {value.connector_id for value in preferred}
        ]
        requested = set(request.required_source_families)
        if candidate.proposed_source_family:
            requested.add(candidate.proposed_source_family)
        return [
            item for item in [*preferred, *remaining]
            if not requested
            or requested & set(item.supported_source_families)
        ]
