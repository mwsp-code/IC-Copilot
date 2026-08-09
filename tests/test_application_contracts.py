from __future__ import annotations

from equity_research.application.contracts import (
    candidate_discovery_from_payload,
    closure_audit_from_payload,
    evidence_request_from_payload,
    primary_audit_response_from_payload,
    research_request_from_payload,
    run_manifest_from_payload,
)


def test_legacy_evidence_request_migrates_both_directions() -> None:
    payload = {
        "request_id": "request-1",
        "question": "What evidence tests Services durability?",
        "source_types": ["sec_filing", "issuer_ir"],
        "required_fields": ["segment revenue"],
        "priority": "High",
    }

    request = research_request_from_payload(payload)
    legacy = evidence_request_from_payload({
        **payload,
        "request_version": "evidence-request-v1",
    })

    assert request.request_version == "research-request-v2"
    assert request.objective == payload["question"]
    assert request.required_source_families == payload["source_types"]
    assert legacy.question == payload["question"]


def test_legacy_candidate_remains_quarantined_and_cannot_import_evidence_ids() -> None:
    candidate = candidate_discovery_from_payload({
        "candidate_id": "candidate-1",
        "ticker": "AAPL",
        "title": "Potential source",
        "url": "https://example.com/lead",
        "evidence_ids": ["legacy-model-id"],
    })

    assert candidate.status == "quarantined"
    assert candidate.candidate_type == "source_candidate"
    assert candidate.admitted_evidence_ids == ()


def test_legacy_unsupported_rejection_migrates_to_unresolved() -> None:
    response = primary_audit_response_from_payload({
        "finding_id": "finding-1",
        "status": "rejected_with_evidence",
        "explanation": "Primary disagreed without a citation.",
    })

    assert response.state == "unresolved"
    assert "migrated to unresolved" in response.explanation


def test_legacy_closure_and_manifest_deserialize_with_new_defaults() -> None:
    closure = closure_audit_from_payload({
        "closure_state": "passed_with_gaps",
        "summary": "Legacy closure.",
    })
    manifest = run_manifest_from_payload({
        "run_id": "old-run",
        "generated_at": "2025-01-01T00:00:00+00:00",
        "app_version": "0.0.9",
        "parser_versions": {},
        "source_urls": [],
        "assumptions": [],
        "data_gaps": [],
    })

    assert closure.state == "closed_with_residual_risk"
    assert manifest.research_requests == []
    assert manifest.candidate_discovery == []
    assert manifest.research_integrity_version == "research-integrity-v1"
