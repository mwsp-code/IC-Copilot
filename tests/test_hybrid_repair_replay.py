from __future__ import annotations

from types import SimpleNamespace

from equity_research.hybrid_research import build_quality_repair_replay_provider
from equity_research.models import NormalizedSourceRecord
from equity_research.pipeline import (
    _closure_repair_replay_roles,
    _restore_frozen_repair_evidence_context,
)


class _Delegate:
    provider_name = "provider"
    model = "model"
    last_usage = {}
    last_cost_usd = None

    def __init__(self) -> None:
        self.calls = []

    def complete_json(self, prompt):
        self.calls.append(prompt["role"])
        return {"summary": "new revision"}


def _round(role, payload):
    return SimpleNamespace(
        role=role,
        artifact=SimpleNamespace(payload=payload),
    )


def test_repair_replays_frozen_roles_and_delegates_only_new_work() -> None:
    delegate = _Delegate()
    run = SimpleNamespace(
        rounds=[
            _round("independent_research_auditor", {"findings": []}),
            _round("adversarial_evidence_scout", {"summary": "scouted"}),
        ],
        final_artifact=SimpleNamespace(payload={
            "closure_audit": {
                "audit_decision": {
                    "unresolved_findings": ["MC6"],
                    "unaddressed_contrary_items": [{
                        "contrary_id": "MC6",
                        "finding": "Concentration and export-control risk was omitted.",
                        "evidence_ids": ["ev-1", "ev-2"],
                    }],
                }
            }
        }),
    )
    provider = build_quality_repair_replay_provider(
        delegate,
        run,
        roles=("independent_research_auditor", "adversarial_evidence_scout"),
    )

    audit = provider.complete_json({"role": "independent_research_auditor"})
    provider.complete_json({"role": "adversarial_evidence_scout"})
    revised = provider.complete_json({"role": "closure_checker"})

    assert audit["findings"][0]["finding_id"] == "MC6"
    assert "export-control" in audit["findings"][0]["summary"]
    assert audit["findings"][0]["evidence_ids"] == ["ev-1", "ev-2"]
    assert delegate.calls == ["closure_checker"]
    assert revised["summary"] == "new revision"


def test_repair_can_replay_completed_primary_revision_without_provider_call() -> None:
    delegate = _Delegate()
    run = SimpleNamespace(
        rounds=[_round("primary_revision", {
            "summary": "Conservative validated revision",
            "primary_responses": [],
        })],
        final_artifact=None,
    )
    provider = build_quality_repair_replay_provider(
        delegate,
        run,
        roles=("primary_revision",),
    )

    revision = provider.complete_json({"role": "primary_revision"})

    assert revision["summary"] == "Conservative validated revision"
    assert delegate.calls == []


def test_closure_only_resume_replays_completed_revision_snapshot() -> None:
    completed = SimpleNamespace(
        rounds=[SimpleNamespace(
            role="primary_revision",
            status="completed",
            artifact=SimpleNamespace(payload={"summary": "revised"}),
        )]
    )
    incomplete = SimpleNamespace(
        rounds=[SimpleNamespace(
            role="primary_revision",
            status="failed",
            artifact=None,
        )]
    )

    assert _closure_repair_replay_roles(completed) == (
        "research_planner",
        "primary_investment_analyst",
        "primary_revision",
    )
    assert _closure_repair_replay_roles(incomplete) == (
        "research_planner",
        "primary_investment_analyst",
    )


def test_unanswered_closure_finding_forces_fresh_primary_revision() -> None:
    failed_closure = SimpleNamespace(
        rounds=[
            SimpleNamespace(
                role="primary_revision",
                status="completed",
                artifact=SimpleNamespace(payload={"summary": "old revision"}),
            ),
            SimpleNamespace(
                role="closure_checker",
                status="completed",
                artifact=SimpleNamespace(payload={
                    "findings": [{
                        "finding_id": "mc2",
                        "claim": "Below-operating-line conversion is unresolved.",
                        "evidence_ids": ["ev-1"],
                    }],
                    "audit_decision": {
                        "status": "failed",
                        "unresolved_findings": ["mc2"],
                    },
                }),
            ),
        ],
        final_artifact=SimpleNamespace(payload={
            "closure_audit": {
                "audit_decision": {
                    "status": "failed",
                    "unresolved_findings": [
                        "Material audit finding mc2 has no primary response."
                    ],
                }
            }
        }),
    )

    assert _closure_repair_replay_roles(failed_closure) == (
        "research_planner",
        "primary_investment_analyst",
    )

    # Add the frozen audit artifact, then exercise enrichment from both the
    # original closure payload and its deterministic readable failure form.
    failed_closure.rounds.insert(
        0,
        _round("independent_research_auditor", {"findings": []}),
    )
    provider = build_quality_repair_replay_provider(
        _Delegate(), failed_closure, roles=("independent_research_auditor",)
    )
    audit = provider.complete_json({"role": "independent_research_auditor"})

    assert [item["finding_id"] for item in audit["findings"]] == ["mc2"]
    assert audit["findings"][0]["evidence_ids"] == ["ev-1"]


def test_failed_closure_finding_forces_revision_when_unresolved_id_list_is_blank() -> None:
    failed_closure = SimpleNamespace(
        rounds=[
            SimpleNamespace(
                role="primary_revision",
                status="completed",
                artifact=SimpleNamespace(payload={"summary": "old revision"}),
            ),
            SimpleNamespace(
                role="closure_checker",
                status="completed",
                artifact=SimpleNamespace(payload={
                    "closure_state": "failed_material_findings_unaddressed",
                    "findings": [{
                        "finding_id": "closure-control-1",
                        "severity": "High",
                        "summary": "A material response linkage must be repaired.",
                        "evidence_ids": ["ev-1"],
                    }],
                    "audit_decision": {
                        "status": "failed",
                        "unresolved_findings": [""],
                    },
                }),
            ),
        ],
        final_artifact=None,
    )

    assert _closure_repair_replay_roles(failed_closure) == (
        "research_planner",
        "primary_investment_analyst",
    )


def test_response_alias_failure_reuses_and_normalizes_primary_revision() -> None:
    run = SimpleNamespace(
        rounds=[
            SimpleNamespace(
                role="primary_revision",
                status="completed",
                artifact=SimpleNamespace(payload={
                    "primary_responses": [{
                        "finding_id": "mc2",
                        "response": "accepted",
                        "state": "",
                        "rationale": "The affected conclusion was removed.",
                    }]
                }),
            ),
            SimpleNamespace(
                role="closure_checker",
                status="completed",
                artifact=SimpleNamespace(payload={
                    "audit_decision": {
                        "status": "failed",
                        "unresolved_findings": [
                            "Audit finding mc2 has an invalid primary response state."
                        ],
                    }
                }),
            ),
        ],
        final_artifact=None,
    )

    assert _closure_repair_replay_roles(run) == (
        "research_planner",
        "primary_investment_analyst",
        "primary_revision",
    )


def test_frozen_repair_restores_parent_graph_and_discards_live_additions() -> None:
    record = NormalizedSourceRecord(
        source_id="parent-evidence",
        ticker="NVDA",
        entity_name="NVIDIA Corporation",
        security_id="NVDA",
        source_family="primary_official",
        source_type="sec_filing",
        document_id="parent-filing",
        source_url="https://www.sec.gov/parent-filing",
        publication_date="2026-05-20",
        reporting_period="2026-04-30",
        section="Risk Factors",
        page_or_table="Item 1A",
        exact_excerpt="Export controls and concentration remain material risks.",
        metric_or_claim="Export-control and customer-concentration risk",
        value=None,
        unit="",
        currency="",
        source_tier=1,
        origin_group="sec.gov",
        licensing_policy="public_official",
        retrieval_status="fetched_live",
        parser_status="parsed",
        validation_status="validated",
    )
    metadata_record = NormalizedSourceRecord(
        source_id="parent-metadata",
        ticker="NVDA",
        entity_name="NVIDIA Corporation",
        security_id="NVDA",
        source_family="external_research",
        source_type="news",
        document_id="metadata-lead",
        source_url="https://example.com/discovery-lead",
        publication_date="2026-05-21",
        reporting_period="",
        section="",
        page_or_table="",
        exact_excerpt="",
        metric_or_claim="Potential discovery lead",
        value=None,
        unit="",
        currency="",
        source_tier=3,
        origin_group="example.com",
        licensing_policy="metadata_only",
        retrieval_status="metadata_only",
        parser_status="not_parsed",
        validation_status="unvalidated",
    )
    parent = SimpleNamespace(
        run_id="parent-run",
        evidence_hash="parent-evidence-hash",
        evidence_fingerprints={
            "parent-evidence": "parent-fingerprint",
            "parent-metadata": "metadata-fingerprint",
        },
        normalized_sources=[record, metadata_record],
        source_candidates=[],
        source_admission_decisions=[],
        cash_conversion_recurrence=None,
    )
    context = {
        "evidence_catalog": [{"evidence_id": "new-live-evidence"}],
        "searchable_evidence_catalog": [{"evidence_id": "new-live-evidence"}],
        "evidence_fingerprints": {"new-live-evidence": "new-fingerprint"},
        "quality_first_execution": {"max_investigation_cycles": 2},
    }

    restored = _restore_frozen_repair_evidence_context(context, parent)

    assert restored is True
    assert context["evidence_hash"] == "parent-evidence-hash"
    assert context["evidence_fingerprints"] == {
        "parent-evidence": "parent-fingerprint",
        "parent-metadata": "metadata-fingerprint",
    }
    assert [item["evidence_id"] for item in context["evidence_catalog"]] == [
        "parent-evidence"
    ]
    assert [
        item["evidence_id"] for item in context["searchable_evidence_catalog"]
    ] == ["parent-evidence", "parent-metadata"]
    assert context["claim_eligible_evidence_ids"] == ["parent-evidence"]
    assert context["_resume_evidence_relationship"] == "frozen_parent_snapshot"
    assert context["quality_first_execution"]["max_investigation_cycles"] == 0
