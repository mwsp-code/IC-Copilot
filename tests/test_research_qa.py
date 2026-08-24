from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

from equity_research.domain.research_integrity import AuditFinding, ResearchRequest
from equity_research.models import SourceRetrievalLifecycle
from equity_research.research_qa import (
    _audit_resolutions,
    _evidence_gaps,
    apply_source_policy_to_context,
    build_adaptive_macro_plan,
    build_data_access_policy,
    build_research_ai_execution_plan,
    build_research_execution_policy,
)


class Provider:
    def __init__(self, provider_name: str, model: str) -> None:
        self.provider_name = provider_name
        self.model = model


def _context() -> dict:
    evidence = [
        {
            "evidence_id": "sec-1",
            "source_type": "sec_filing",
            "origin_group": "sec.gov",
            "summary": "Operating cash flow increased year over year.",
            "source_url": "https://www.sec.gov/example",
            "publication_date": "2026-01-30",
            "validation_status": "validated",
        },
        {
            "evidence_id": "news-1",
            "source_type": "reputable_publisher",
            "origin_group": "publisher.example",
            "summary": "Demand may be slowing.",
            "source_url": "https://publisher.example/story",
            "publication_date": "2026-01-31",
            "validation_status": "validated",
        },
    ]
    return {
        "ticker": "AAPL",
        "evidence_catalog": evidence,
        "searchable_evidence_catalog": evidence,
        "claim_eligible_evidence_ids": ["sec-1", "news-1"],
        "evidence_metadata_catalog": evidence,
        "evidence_fingerprints": {"sec-1": "a", "news-1": "b"},
        "prompt_version": "prompt-v1",
        "source_registry_version": "sources-v1",
    }


def test_source_policy_excludes_disabled_families_and_changes_fingerprint() -> None:
    context = _context()
    execution = build_research_execution_policy("adaptive_ic")
    all_sources = build_data_access_policy(context["searchable_evidence_catalog"])
    filtered = build_data_access_policy(
        context["searchable_evidence_catalog"],
        disabled_source_families=["news"],
    )

    admitted_context, admitted_fingerprint, admitted_graph = apply_source_policy_to_context(
        context, execution, all_sources
    )
    filtered_context, filtered_fingerprint, filtered_graph = apply_source_policy_to_context(
        context, execution, filtered
    )

    assert set(admitted_context["claim_eligible_evidence_ids"]) == {"sec-1", "news-1"}
    assert filtered_context["claim_eligible_evidence_ids"] == ["sec-1"]
    assert filtered_graph.excluded_evidence_ids == ["news-1"]
    assert filtered_fingerprint.fingerprint != admitted_fingerprint.fingerprint
    assert admitted_graph.evidence_hash != filtered_graph.evidence_hash


def test_execution_plan_keeps_optional_auditor_non_blocking() -> None:
    execution = build_research_execution_policy("deep_initiation")
    primary = Provider("deepseek", "deepseek-v4-pro")

    plan = build_research_ai_execution_plan(
        execution,
        primary_provider=primary,
        auditor_provider=None,
    )

    assert plan.status == "primary_ready_audit_optional"
    assert plan.primary_readiness.status == "ready"
    assert plan.independent_audit_readiness.status == "optional_not_configured"
    assert plan.expected_model_calls == 4
    assignments = {item.role: asdict(item) for item in plan.assignments}
    assert assignments["independent_research_auditor"]["required"] is False
    assert assignments["evidence_gap_resolver"]["deterministic"] is True


def test_adaptive_macro_selects_only_material_company_exposures() -> None:
    plan = build_adaptive_macro_plan(
        "JPM",
        company_name="JPMorgan Chase",
        sic_description="National commercial bank",
    )

    assert plan.status == "Available"
    assert 1 <= len(plan.selected_exposures) <= 3
    factors = {item.factor for item in plan.selected_exposures}
    assert "Rates and yield curve" in factors
    assert "Liquidity and financial conditions" in factors
    assert "Commodity prices" not in factors
    assert "cannot independently create or promote a thesis" in plan.selection_policy


def test_validated_retrieval_without_admission_does_not_close_request_gap() -> None:
    request = ResearchRequest(
        request_id="audit_cash_conversion",
        objective="Build a same-duration cash-conversion bridge.",
        required_source_families=["sec_filing"],
        status="attempted",
        attempted_connector_ids=["sec_filing"],
    )
    finding = AuditFinding(
        finding_id="cash-finding",
        category="financial_bridge",
        severity="High",
        summary="Cash-conversion recurrence is not established.",
        evidence_request_ids=["audit_cash_conversion"],
    )
    run = SimpleNamespace(
        run_id="run-1",
        retrieval_lifecycles=[SourceRetrievalLifecycle(
            request_id="audit_cash_conversion",
            source_id="sec-filing",
            status="validated",
            source_type="sec_filing",
            message="12 source records reached validated.",
        )],
    )

    gaps = _evidence_gaps([finding], [request], run)
    resolutions = _audit_resolutions([finding], gaps)

    assert gaps[0].status == "unresolved"
    assert gaps[0].resolution_evidence_ids == []
    assert resolutions[0].status == "unresolved"


def test_admitted_candidate_is_partial_until_request_answer_is_completed() -> None:
    request = ResearchRequest(
        request_id="audit_cash_conversion",
        objective="Build a same-duration cash-conversion bridge.",
        required_source_families=["sec_filing"],
        status="evidence_admitted",
        admitted_evidence_ids=["evidence-1"],
    )
    finding = AuditFinding(
        finding_id="cash-finding",
        category="financial_bridge",
        severity="High",
        summary="Cash-conversion recurrence is not established.",
        evidence_request_ids=["audit_cash_conversion"],
    )
    run = SimpleNamespace(run_id="run-2", retrieval_lifecycles=[])

    gaps = _evidence_gaps([finding], [request], run)
    resolutions = _audit_resolutions([finding], gaps)

    assert gaps[0].status == "partially_resolved"
    assert gaps[0].resolution_evidence_ids == ["evidence-1"]
    assert resolutions[0].status == "unresolved"
