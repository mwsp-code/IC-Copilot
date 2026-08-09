from __future__ import annotations

from equity_research.application.research_requests import ResearchRequestDispatcher
from equity_research.domain.research_integrity import (
    CandidateType,
    EvidenceAdmissionPolicy,
    ResearchRequest,
    RetrievedCandidateDocument,
    new_candidate,
)


class OfficialConnector:
    connector_id = "official-regulator"
    supported_source_families = ("primary_official",)

    def retrieve(self, request, candidate):
        return RetrievedCandidateDocument(
            candidate_id=candidate.candidate_id,
            connector_id=self.connector_id,
            canonical_url=candidate.url,
            content="The regulator published a final decision affecting covered distribution practices.",
            source_family="primary_official",
            source_type="regulator_release",
            publisher="Federal Trade Commission",
            publication_timestamp="2026-07-01T00:00:00+00:00",
            retrieval_timestamp="2026-07-02T00:00:00+00:00",
            licensing_policy="public_official",
            provenance="Registered regulator connector.",
        )


class FailingConnector(OfficialConnector):
    connector_id = "failing-regulator"

    def retrieve(self, request, candidate):
        raise TimeoutError("fixture timeout")


def _request():
    return ResearchRequest(
        request_id="request-1",
        objective="Confirm or contradict the regulatory exposure.",
        required_source_families=["primary_official"],
        desired_confirming_evidence=["Final regulator decision"],
        desired_contradicting_evidence=["Official dismissal or exemption"],
        preferred_connectors=["official-regulator"],
    )


def _candidate():
    return new_candidate(
        candidate_type=CandidateType.CONTRADICTION,
        ticker="AAPL",
        objective="Test regulatory downside.",
        origin_role="independent_research_auditor",
        origin_provider="local_codex",
        research_request_id="request-1",
        url="https://www.ftc.gov/example",
        title="Potential final decision",
        proposed_source_family="primary_official",
    )


def test_dispatcher_selects_registered_connector_then_common_admission() -> None:
    request = _request()
    execution = ResearchRequestDispatcher([OfficialConnector()]).dispatch(
        request,
        [_candidate()],
        EvidenceAdmissionPolicy(
            enabled_source_families=("primary_official",),
            allowed_licensing_policies=("public_official",),
            data_cutoff="2026-07-31",
        ),
    )

    assert execution.status == "resolved"
    assert execution.candidates[0].status == "admitted"
    assert request.attempted_connector_ids == ["official-regulator"]
    assert request.admitted_evidence_ids
    assert execution.admission_results[0].decision.pipeline_completed is True


def test_open_web_lead_without_registered_connector_remains_quarantined() -> None:
    request = _request()
    candidate = _candidate()
    execution = ResearchRequestDispatcher([]).dispatch(
        request,
        [candidate],
        EvidenceAdmissionPolicy(),
    )

    assert execution.status == "unavailable"
    assert execution.candidates[0].status == "quarantined"
    assert request.admitted_evidence_ids == []
    assert execution.attempts[0].status == "unavailable"


def test_connector_failure_is_visible_and_never_mints_evidence() -> None:
    request = _request()
    execution = ResearchRequestDispatcher([FailingConnector()]).dispatch(
        request,
        [_candidate()],
        EvidenceAdmissionPolicy(),
    )

    assert execution.status == "unavailable"
    assert execution.candidates[0].status == "retrieval_failed"
    assert execution.attempts[0].status == "retrieval_failed"
    assert request.admitted_evidence_ids == []
