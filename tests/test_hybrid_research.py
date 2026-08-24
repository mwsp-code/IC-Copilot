from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import asdict

import pytest

from equity_research.hybrid_research import (
    CODEX_LOCAL_BACKEND,
    OPENAI_RESPONSES_BACKEND,
    CodexLocalProvider,
    HybridResearchJobManager,
    OpenAIResponsesProvider,
    build_hybrid_execution_preflight,
    local_codex_readiness,
    _codex_wrapper_schema,
    _execute_registered_requests,
    _provider_system_prompt,
    _prompt_metadata_index,
    _round_prompt,
    _validate_round_artifact,
    _admit_artifact_source_candidates,
    _admit_retrieved_candidate_records,
    _unwrap_codex_payload,
    _is_packaged_codex_path,
    _normalize_replayed_audit_findings,
    _normalize_primary_response_contract,
    _resolve_codex_executable,
    _windows_codex_desktop_candidates,
    frozen_fixture_hybrid_run,
    hybrid_run_from_payload,
    run_dual_choreography_research,
    run_hybrid_research,
)
from equity_research.research_store import ResearchStore
from equity_research.sample_data import demo_result
from equity_research.secret_safety import contains_credential_like_token
from equity_research.models import (
    DisplayedResultContext,
    NormalizedSourceRecord,
    ResearchRoundArtifact,
    SourceRetrievalLifecycle,
)


def test_model_candidate_retrieval_uses_gateway_minted_evidence_id() -> None:
    context = {
        "ticker": "AAPL",
        "source_policy": {
            "enabled_source_families": ["primary_official"],
            "licensing_rules": ["public_official"],
            "data_cutoff": "2026-07-31",
        },
        "_source_candidates": [{
            "candidate_id": "candidate-1",
            "ticker": "AAPL",
            "url": "https://www.ftc.gov/example",
            "title": "Official decision",
            "proposed_source_family": "primary_official",
            "candidate_type": "contradiction_candidate",
            "publication_date": "2026-07-01",
        }],
        "_source_admission_decisions": [{
            "candidate_id": "candidate-1",
            "status": "admitted_as_lead",
            "admitted_as": "discovery_lead",
        }],
    }
    record = NormalizedSourceRecord(
        source_id="connector-created-id-must-not-survive",
        ticker="AAPL",
        entity_name="Apple Inc.",
        security_id="AAPL",
        source_family="primary_official",
        source_type="regulator_court",
        document_id="ftc-decision",
        source_url="https://www.ftc.gov/example",
        publication_date="2026-07-01",
        reporting_period="",
        section="Decision",
        page_or_table="",
        exact_excerpt="The regulator issued a final decision affecting covered distribution practices.",
        metric_or_claim="Test regulatory downside.",
        value=None,
        unit="",
        currency="",
        source_tier=1,
        origin_group="Federal Trade Commission",
        licensing_policy="public_official",
        retrieval_status="fetched_live",
        parser_status="parsed",
        validation_status="validated",
    )
    lifecycle = SourceRetrievalLifecycle(
        request_id="request-1",
        source_id=record.source_id,
        source_type="regulator_court",
        status="completed",
        completed_at="2026-07-02T00:00:00+00:00",
    )

    admitted = _admit_retrieved_candidate_records(
        context,
        candidate_id="candidate-1",
        records=[record],
        lifecycle=lifecycle,
    )

    assert len(admitted) == 1
    assert admitted[0].source_id != record.source_id
    assert context["_candidate_gateway_decisions"][0]["pipeline_completed"] is True
    assert context["_source_admission_decisions"][0]["status"] == "admitted"


class FixtureProvider:
    def __init__(self, provider_name: str, model: str, payloads) -> None:
        self.provider_name = provider_name
        self.model = model
        self.payloads = list(payloads)
        self.prompts = []
        self.last_usage = {"input_tokens": 10, "output_tokens": 5}

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        next_item = self.payloads.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class NoopRetriever:
    def retrieve(
        self,
        *,
        request_id,
        source_type,
        query,
        period,
        result_limit,
    ):
        return [], SourceRetrievalLifecycle(
            request_id=request_id,
            source_id="",
            source_type=source_type,
            status="unavailable",
            history=["planned", "unavailable"],
            failure_code="fixture_unavailable",
            message="No fixture evidence was returned.",
        )


def test_local_codex_readiness_uses_login_status_without_reading_tokens(tmp_path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "Logged in", "")

    readiness = local_codex_readiness(str(executable), runner=runner)

    assert readiness.status == "ready"
    assert readiness.credential_source == "local Codex authentication"
    assert calls[0][0][-2:] == ["login", "status"]
    assert "token" not in " ".join(calls[0][0]).casefold()


def test_local_codex_not_authenticated_is_actionable_and_needs_no_api_key(tmp_path) -> None:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")

    readiness = local_codex_readiness(
        str(executable),
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "Not logged in"
        ),
    )

    assert readiness.status == "not_authenticated"
    assert "codex login" in readiness.message
    assert "no API key" in readiness.message


def _context() -> dict:
    return {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "exchange": "NASDAQ",
        "sic": "3571",
        "research_profile": "deep_initiation",
        "company_economics": {"industry": "Technology hardware"},
        "analog_signature": {"driver": "capital allocation"},
        "analog_candidates": [
            {
                "analog_id": "analog-1",
                "episode": "Apple services and capital-return cycle",
            }
        ],
        "news_status": "Available",
        "news_coverage": "Partial",
        "source_requests": [],
        "evidence_closure": [],
        "evidence_catalog": [
            {
                "evidence_id": "e1",
                "citation_id": "c1",
                "kind": "historical_source",
                "source_type": "sec_filing",
                "source_tier": 1,
                "period": "2025-09-27",
                "summary": "Operating cash flow increased.",
                "exact_excerpt": "Operating cash flow increased.",
                "source_url": "https://www.sec.gov/example",
                "publication_date": "2025-10-31",
                "document_id": "aapl-2025-10-k",
                "origin_group": "sec.gov",
                "retrieval_status": "cached_document",
                "parser_status": "parsed",
                "validation_status": "validated",
            },
            {
                "evidence_id": "e2",
                "citation_id": "c2",
                "kind": "historical_source",
                "source_type": "external_research",
                "source_tier": 3,
                "period": "2019-01-01",
                "summary": "Services mix increased.",
                "exact_excerpt": "Services mix increased.",
                "source_url": "https://example.com/history",
                "publication_date": "2020-01-15",
                "document_id": "history-1",
                "origin_group": "example.com",
                "retrieval_status": "cached_document",
                "parser_status": "parsed",
                "validation_status": "validated",
            },
            {
                "evidence_id": "e3",
                "citation_id": "c3",
                "kind": "historical_source",
                "source_type": "institutional_history",
                "source_tier": 2,
                "period": "2019-12-31",
                "summary": "Institutional history documents the episode.",
                "exact_excerpt": "Institutional history documents the episode.",
                "source_url": "https://institution.example/history",
                "publication_date": "2020-02-15",
                "document_id": "history-2",
                "origin_group": "institution.example",
                "retrieval_status": "cached_document",
                "parser_status": "parsed",
                "validation_status": "validated",
            },
            {
                "evidence_id": "e4",
                "citation_id": "c4",
                "kind": "historical_source",
                "source_type": "quantitative_historical_price",
                "source_tier": 2,
                "period": "2020-12-31",
                "publication_date": "2020-12-31",
                "summary": "Adjusted price and valuation path documented.",
                "exact_excerpt": "Adjusted price and valuation path documented.",
                "source_url": "https://prices.example/AAPL",
                "document_id": "price-path-1",
                "origin_group": "prices.example",
                "retrieval_status": "cached_document",
                "parser_status": "parsed",
                "validation_status": "validated",
            },
        ],
        "allowed_source_types": [
            "sec_filing",
            "external_research",
            "institutional_history",
            "quantitative_historical_price",
        ],
        "source_registry_version": "source-registry-v1",
        "evidence_hash": "evidence-hash",
    }


def test_model_source_candidate_stays_a_lead_until_deterministic_fetch() -> None:
    context = _context()
    payload = {
        "summary": "A regulator source should be checked.",
        "source_candidates": [{
            "title": "FTC proposed rule",
            "url": "https://www.ftc.gov/news/proposed-rule",
            "publisher": "Federal Trade Commission",
            "ownership_group": "ftc.gov",
            "source_type": "regulator_release",
            "reason": "Test a regulatory exposure.",
        }],
    }
    artifact, _ = _validate_round_artifact(
        1, "research_planner", payload, context
    )
    candidate = artifact.payload["source_candidates"][0]
    assert candidate["full_text_status"] == "metadata_only"
    assert candidate["evidence_ids"] == []

    class CandidateRetriever:
        def __init__(self):
            self.registered_source_catalog = []

    context["_retrieval_engine"] = CandidateRetriever()
    requests = _admit_artifact_source_candidates(
        context, artifact.payload
    )
    assert requests[0]["source_type"] == "regulator_court"
    assert context["_source_admission_decisions"][0]["admitted_as"] == (
        "discovery_lead"
    )
    assert (
        context["_retrieval_engine"].registered_source_catalog[0]
        .validation_status
        == "metadata_only"
    )


def test_round_prompt_bounds_large_search_catalog_without_losing_core() -> None:
    context = _context()
    core = []
    metadata = []
    exclusions = []
    for index in range(5_000):
        evidence_id = f"large-{index:05d}"
        item = {
            "evidence_id": evidence_id,
            "citation_id": f"citation-{index:05d}",
            "kind": "financial_metric",
            "source_type": "sec_filing",
            "source_tier": 1,
            "period": "2025-09-27",
            "metric": f"Metric {index}",
            "summary": "Decision-relevant evidence " + ("x" * 700),
            "source_url": f"https://example.com/{index}",
            "selection_score": 100 - (index % 100),
        }
        metadata.append({
            key: value for key, value in item.items()
            if key not in {"summary", "source_url"}
        })
        if index < 160:
            core.append(item)
        else:
            exclusions.append({
                "evidence_id": evidence_id,
                "kind": "financial_metric",
                "reason": (
                    "Retained in the complete searchable catalog; omitted "
                    "from the core prompt because higher-scoring evidence "
                    "filled the profile budget."
                ),
                "score": item["selection_score"],
                "source_type": "sec_filing",
                "source_tier": 1,
                "period": "2025-09-27",
            })
    context["evidence_catalog"] = core
    context["searchable_evidence_catalog"] = [
        *core,
        *[
            {
                **item,
                "summary": "Catalog-only evidence.",
                "source_url": "",
            }
            for item in metadata[160:]
        ],
    ]
    context["evidence_metadata_catalog"] = metadata
    context["claim_eligible_evidence_ids"] = [
        item["evidence_id"] for item in metadata
    ]
    context["evidence_pack_audit"] = {
        "ticker": "AAPL",
        "status": "complete_searchable_catalog",
        "available_count": 5_000,
        "core_count": 160,
        "searchable_count": 5_000,
        "core_evidence_ids": [item["evidence_id"] for item in core],
        "searchable_evidence_ids": [
            item["evidence_id"] for item in metadata
        ],
        "exclusions": exclusions,
        "current_prior_pairs": [],
    }

    prompt = _round_prompt(1, "research_planner", context, [], {})
    clean_prompt = {
        key: value for key, value in prompt.items()
        if not key.startswith("_")
    }
    wrapped = (
        _provider_system_prompt()
        + json.dumps(
            {
                "research_output_schema": prompt["_output_schema"],
                "research_input": clean_prompt,
            },
            ensure_ascii=True,
        )
    )

    assert len(wrapped) < 1_048_576
    assert {
        item["evidence_id"] for item in core
    } <= set(prompt["allowed_evidence_ids"])
    assert (
        prompt["searchable_evidence_index_status"]
        ["complete_catalog_count"]
        == 5_000
    )
    assert (
        prompt["searchable_evidence_index_status"]
        ["omitted_from_prompt_index"]
        > 0
    )
    assert len(context["searchable_evidence_catalog"]) == 5_000
    assert (
        prompt["evidence_pack_audit"]
        ["full_exclusion_audit_retained_outside_prompt"]
        is True
    )


def test_prompt_metadata_index_never_readds_permitted_items_past_cap() -> None:
    metadata = [
        {
            "evidence_id": f"permitted-{index:04d}",
            "citation_id": f"citation-{index:04d}",
            "kind": "financial_metric",
            "source_type": "sec_filing",
            "source_tier": 1,
            "period": "2026-06-30",
            "metric": f"Decision metric {index}",
            "selection_score": 100 - (index % 100),
            "provenance": "must remain outside prompt " + ("x" * 4_000),
        }
        for index in range(2_000)
    ]
    permitted = [
        {"evidence_id": item["evidence_id"]} for item in metadata
    ]

    prompt_index, status = _prompt_metadata_index(
        {"evidence_metadata_catalog": metadata},
        permitted,
        max_chars=25_000,
    )

    assert len(json.dumps(prompt_index, ensure_ascii=True)) <= 25_000
    assert status["permitted_omitted_from_prompt_index"] > 0
    assert status["projection_chars"] <= status["projection_limit_chars"]
    assert status["complete_catalog_searchable_between_rounds"] is True
    assert all("provenance" not in item for item in prompt_index)


def test_primary_revision_normalizes_audit_responses_for_closure() -> None:
    payload = _normalize_primary_response_contract(
        {
            "audit_responses": [{
                "finding_id": "mc2",
                "status": "accepted",
                "reason": "The conversion claim was narrowed and retained as a limitation.",
            }]
        },
        role="primary_revision",
    )

    assert payload["primary_responses"] == [{
        "finding_id": "mc2",
        "status": "accepted",
        "reason": "The conversion claim was narrowed and retained as a limitation.",
        "state": "accepted",
        "explanation": "The conversion claim was narrowed and retained as a limitation.",
        "change_summary": "The conversion claim was narrowed and retained as a limitation.",
    }]
    assert payload["primary_response_contract_migration"].startswith(
        "audit_responses-v1"
    )

    existing = _normalize_primary_response_contract(
        {
            "primary_responses": [{
                "finding_id": "mc2",
                "response": "accepted",
                "rationale": "The conclusion was removed.",
                "state": "",
            }]
        },
        role="primary_revision",
    )
    assert existing["primary_responses"][0]["state"] == "accepted"
    assert existing["primary_responses"][0]["change_summary"] == (
        "The conclusion was removed."
    )


def test_primary_revision_recovers_legacy_responses_when_canonical_list_empty() -> None:
    finding_text = "Cash conversion requires an exact comparable-period bridge."
    payload = _normalize_primary_response_contract(
        {
            "primary_responses": [],
            "audit_responses": [{
                "audit_finding": finding_text,
                "status": "accepted",
                "response": "Accepted: the directional conclusion was removed.",
                "evidence_ids": ["evidence-1"],
            }],
        },
        role="primary_revision",
        audit_findings=[{
            "finding_id": "cash-conversion-bridge",
            "summary": finding_text,
        }],
    )

    assert payload["primary_responses"] == [{
        "audit_finding": finding_text,
        "status": "accepted",
        "response": "Accepted: the directional conclusion was removed.",
        "evidence_ids": ["evidence-1"],
        "finding_id": "cash-conversion-bridge",
        "state": "accepted",
        "explanation": "Accepted: the directional conclusion was removed.",
        "change_summary": "Accepted: the directional conclusion was removed.",
    }]


def test_auditor_findings_receive_stable_ids_and_materiality_defaults() -> None:
    context = _context()
    artifact, findings = _validate_round_artifact(
        3,
        "independent_research_auditor",
        {
            "summary": "Independent audit found one material gap.",
            "findings": [{
                "finding": "AUM growth is not reconciled to flows and markets.",
                "evidence_ids": [],
            }],
        },
        context,
    )

    persisted = artifact.payload["findings"][0]
    assert persisted["finding_id"]
    assert persisted["summary"] == (
        "AUM growth is not reconciled to flows and markets."
    )
    assert persisted["severity"] == "High"
    assert findings[0].finding_id == persisted["finding_id"]
    assert findings[0].summary == persisted["summary"]


def test_replayed_audit_findings_replace_blank_ids_without_collapsing_evidence() -> None:
    findings = _normalize_replayed_audit_findings(
        [
            {
                "finding_id": "",
                "severity": "High",
                "summary": "A material legacy finding requires a response.",
                "evidence_ids": ["evidence-a"],
            },
            {
                "finding_id": "",
                "severity": "High",
                "summary": "A material legacy finding requires a response.",
                "evidence_ids": ["evidence-b"],
            },
        ],
        ticker="BLK",
    )

    assert all(item["finding_id"] for item in findings)
    assert findings[0]["finding_id"] != findings[1]["finding_id"]
    assert findings == _normalize_replayed_audit_findings(
        findings,
        ticker="BLK",
    )


def test_round_prompt_balances_core_and_audit_repair_evidence() -> None:
    context = _context()
    core = []
    repair = []
    for index in range(100):
        core.append({
            "evidence_id": f"core-{index}",
            "kind": "validated_claim",
            "source_type": "sec_filing",
            "source_tier": 1,
            "period": "2026-03-28",
            "summary": "Current financial baseline " + ("x" * 1_200),
            "exact_excerpt": "Current fact.",
        })
        repair.append({
            "evidence_id": f"repair-{index}",
            "kind": "historical_source",
            "source_type": "historical_filing",
            "source_tier": 1,
            "period": "2016",
            "summary": "Historical repair evidence " + ("y" * 1_200),
            "exact_excerpt": "Historical fact.",
        })
    context["evidence_catalog"] = core
    context["searchable_evidence_catalog"] = [*core, *repair]
    context["claim_eligible_evidence_ids"] = [
        item["evidence_id"] for item in [*core, *repair]
    ]
    context["_opened_evidence_ids"] = [
        item["evidence_id"] for item in repair
    ]
    context["_audit_repair_evidence_ids"] = [
        item["evidence_id"] for item in repair
    ]

    prompt = _round_prompt(
        4,
        "reconciliation_analyst",
        context,
        [
            ResearchRoundArtifact("r1", "planner", "validated"),
            ResearchRoundArtifact("r2", "investigator", "validated"),
            ResearchRoundArtifact("r3", "auditor", "validated"),
        ],
        {"initial": [], "follow_up": []},
    )

    allowed = set(prompt["allowed_evidence_ids"])
    assert "core-0" in allowed
    assert "repair-0" in allowed
    assert any(item.startswith("core-") for item in allowed)
    assert any(item.startswith("repair-") for item in allowed)


def test_all_later_rounds_bound_evidence_artifacts_and_retrieval() -> None:
    context = _context()
    catalog = []
    for index in range(260):
        catalog.append({
            "evidence_id": f"evidence-{index:04d}",
            "citation_id": f"citation-{index:04d}",
            "kind": (
                "historical_source"
                if index % 4 == 0 else "financial_metric"
            ),
            "source_type": "historical_filing",
            "source_tier": 1,
            "period": "2020-09-26",
            "summary": f"Evidence {index} " + ("s" * 3_000),
            "exact_excerpt": f"Excerpt {index} " + ("x" * 4_000),
            "source_url": f"https://www.sec.gov/example/{index}",
            "selection_score": 100 - (index % 100),
            "retrieval_status": "cached_document",
            "parser_status": "parsed",
            "validation_status": "validated",
        })
    context["evidence_catalog"] = catalog
    context["searchable_evidence_catalog"] = catalog
    context["evidence_metadata_catalog"] = [
        {
            key: value for key, value in item.items()
            if key not in {"summary", "exact_excerpt", "source_url"}
        }
        for item in catalog
    ]
    context["claim_eligible_evidence_ids"] = [
        item["evidence_id"] for item in catalog
    ]
    huge_payload = {
        "summary": "Prior round " + ("p" * 20_000),
        "claims": [
            {
                "claim": f"Claim {index} " + ("c" * 5_000),
                "evidence_ids": [f"evidence-{index % 260:04d}"],
            }
            for index in range(120)
        ],
        "historical_analog_dossiers": [
            {
                "analog_id": f"analog-{index}",
                "narrative": "Historical narrative " + ("n" * 8_000),
                "source_ids": [f"evidence-{index % 260:04d}"],
            }
            for index in range(40)
        ],
    }
    artifacts = [
        ResearchRoundArtifact(
            artifact_id=f"artifact-{index}",
            artifact_type="fixture",
            status="validated",
            payload=huge_payload,
        )
        for index in range(4)
    ]
    retrieval = {
        "initial": [
            {
                "summary": "Retrieved evidence " + ("r" * 8_000),
                "evidence_ids": [f"evidence-{index % 260:04d}"],
            }
            for index in range(80)
        ],
        "follow_up": [
            {
                "summary": "Follow-up evidence " + ("f" * 8_000),
                "evidence_ids": [f"evidence-{index % 260:04d}"],
            }
            for index in range(80)
        ],
    }

    for round_number in (2, 3, 4, 5):
        prompt = _round_prompt(
            round_number,
            "fixture_role",
            context,
            artifacts[: round_number - 1],
            retrieval,
        )
        assert (
            prompt["prompt_budget"]
            ["estimated_prompt_chars_before_schema_wrapper"]
            < 900_000
        )
        assert prompt["evidence_projection_status"][
            "complete_records_retained_outside_prompt"
        ] is True
        assert len(context["searchable_evidence_catalog"]) == 260


def _planner_payloads():
    return [
        {
            "summary": "Plan exact same-company and mechanism research.",
            "source_requests": [
                {
                    "request_id": "r1",
                    "source_type": "sec_filing",
                    "title": "Check prior filings",
                    "reason": "Establish chronology",
                    "evidence_ids": ["e1"],
                }
            ],
            "constructive_hypotheses": [
                {"text": "Cash conversion improved", "evidence_ids": ["e1"]}
            ],
        },
        {
            "summary": "Audit identified supplier commitments and source gaps.",
            "findings": [
                {
                    "finding_id": "f1",
                    "category": "hidden_risk",
                    "severity": "High",
                    "summary": "Supplier commitments require explicit review.",
                    "evidence_ids": ["e1"],
                    "recommended_source_types": ["sec_filing"],
                }
            ],
            "source_requests": [
                {
                    "request_id": "r2",
                    "source_type": "sec_filing",
                    "title": "Check commitments",
                }
            ],
        },
    ]


def _executor_payloads():
    return [
        {
            "summary": "The episode has an evidence-linked chronology.",
            "claims": [
                {
                    "claim": "Operating cash flow improved in the cited period.",
                    "evidence_ids": ["e1"],
                    "citation_ids": ["c1"],
                }
            ],
            "differences": [{"text": "Macro regimes differ", "evidence_ids": ["e2"]}],
        },
        {
            "summary": "The draft reconciles the audit and follow-up evidence.",
            "lessons": [{"text": "Track conversion", "evidence_ids": ["e1"]}],
            "limitations": [{"text": "Timing remains uncertain", "evidence_ids": ["e2"]}],
        },
        {
            "summary": "Final audited narrative retains the material differences.",
            "final_narrative": "Use the analogy as a monitor framework, not a forecast.",
            "claims": [
                {
                    "claim": "The current evidence supports a cash-conversion comparison.",
                    "evidence_ids": ["e1", "e2"],
                    "citation_ids": ["c1", "c2"],
                }
            ],
            "monitors": [{"text": "FCF conversion", "evidence_ids": ["e1"]}],
        },
    ]


def test_five_round_workflow_runs_in_order_and_validates_evidence() -> None:
    planner = FixtureProvider("deepseek", "deepseek-v4-pro", _planner_payloads())
    executor = FixtureProvider("openai", "gpt-5.6-sol", _executor_payloads())

    run = run_hybrid_research(
        _context(),
        planner_provider=planner,
        executor_provider=executor,
        checker_provider=planner,
        critic_provider=executor,
    )

    assert run.status == "completed"
    assert [item.role for item in run.rounds] == [
        "research_planner",
        "primary_investment_analyst",
        "independent_research_auditor",
        "evidence_gap_resolver",
        "investment_committee_writer",
    ]
    assert all(item.status == "completed" for item in run.rounds)
    assert run.final_artifact is not None
    assert run.final_artifact.evidence_ids == ["e1", "e2"]
    assert run.deterministic_validation_status == "passed"
    assert planner.prompts[0]["round_number"] == 1
    assert planner.prompts[1]["round_number"] == 3
    assert executor.prompts[-1]["round_number"] == 5


def _dual_deepseek_payloads():
    return [
        {
            "summary": "Primary planner established the current baseline.",
            "source_requests": [{
                "request_id": "p-initial",
                "source_type": "sec_filing",
                "title": "Primary filing history",
            }],
        },
        {
            "summary": "DeepSeek investigated chronology and mechanisms.",
            "claims": [{"claim": "Cash conversion improved.", "evidence_ids": ["e1"]}],
            "similarities": [{"text": "Capital return mechanism", "evidence_ids": ["e1"]}],
            "differences": [{"text": "Macro regime differs", "evidence_ids": ["e2"]}],
        },
        {
            "summary": "DeepSeek audited the Sol-led challenger.",
            "hidden_risks": [{"text": "Commitment risk", "evidence_ids": ["e1"]}],
        },
        {
            "summary": "DeepSeek reconciled the primary audit.",
            "lessons": ["Track cash conversion"],
            "monitors": ["FCF conversion"],
            "historical_analog_dossiers": [_complete_dossier()],
        },
        {
            "summary": "DeepSeek challenged the Sol-led final interpretation.",
            "hidden_risks": [{"text": "Demand durability", "evidence_ids": ["e1"]}],
            "limitations": [{"text": "Timing remains uncertain", "evidence_ids": ["e2"]}],
        },
    ]


def _dual_sol_payloads():
    return [
        {
            "summary": "Sol independently planned the mechanism check.",
            "source_requests": [{
                "request_id": "c-initial",
                "source_type": "external_research",
                "title": "Independent history",
            }],
        },
        {
            "summary": "Sol investigated the challenger chronology.",
            "claims": [{"claim": "Services mix changed.", "evidence_ids": ["e2"]}],
        },
        {
            "summary": "Sol audited the DeepSeek-led primary.",
            "hidden_risks": [{"text": "Supplier commitments", "evidence_ids": ["e1"]}],
            "source_requests": [{
                "request_id": "p-follow-up",
                "source_type": "sec_filing",
                "title": "Commitments footnote",
            }],
        },
        {
            "summary": "Sol reconciled the challenger.",
            "lessons": ["Track cash conversion"],
            "monitors": ["FCF conversion"],
        },
        {
            "summary": "Sol fresh-context audit passed the primary.",
            "lessons": ["Track cash conversion"],
            "monitors": ["FCF conversion"],
            "claims": [{"claim": "The analogy remains conditional.", "evidence_ids": ["e1", "e2"]}],
            "audit_decision": {
                "status": "passed",
                "critical_failures": [],
                "unresolved_findings": [],
                "accepted_analog_ids": ["analog-1"],
                "rejected_analog_ids": [],
            },
        },
    ]


def _complete_dossier() -> dict:
    return {
        "analog_id": "analog-1",
        "episode": "Apple services and capital-return cycle",
        "start_date": "2018",
        "end_date": "2020",
        "period_precision": "year",
        "companies": ["Apple Inc."],
        "securities": ["AAPL"],
        "triggering_decisions": ["Services investment and repurchases."],
        "operating_outcome": "Services mix and cash conversion improved.",
        "financing_outcome": "Repurchases reduced share count.",
        "valuation_outcome": "The valuation path was documented.",
        "price_outcome": "The adjusted price path was documented.",
        "macro_context": "The rate regime differed.",
        "competitive_context": "Platform competition remained material.",
        "dimensions": [
            {
                "dimension": dimension,
                "current_value": "Current",
                "historical_value": "Historical",
                "score": 70,
                "assessment": "Moderate similarity.",
                "evidence_ids": ["e2", "e3"],
            }
            for dimension in (
                "economic_mechanism",
                "operating_economics",
                "capital_intensity_financing",
                "valuation",
                "competitive_regulatory_structure",
                "macro_regime",
            )
        ],
        "material_differences": ["The macro regime differs."],
        "hidden_risks": ["Supplier commitments remain uncertain."],
        "lessons": ["Track cash conversion."],
        "limitations": ["The analog cannot determine direction."],
        "confirm_kpis": ["FCF conversion"],
        "reject_kpis": ["Commitments rise without demand conversion"],
        "linked_monitor_rules": ["fcf_conversion > 0"],
        "source_ids": ["e1", "e2", "e3", "e4"],
    }


def test_dual_choreography_runs_both_variants_on_shared_evidence() -> None:
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
    )
    sol = FixtureProvider(
        "openai", "gpt-5.6-sol", _dual_sol_payloads()
    )

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "completed"
    assert run.selected_primary_variant_id == "deepseek_led"
    assert [item.variant_id for item in run.variants] == [
        "deepseek_led", "sol_led_challenger",
    ]
    assert all(len(item.rounds) == 5 for item in run.variants)
    assert run.shared_retrieval_manifest is not None
    assert run.shared_retrieval_manifest.shared_by_variants is True
    assert run.shared_retrieval_manifest.evidence_hash == run.evidence_hash
    assert len(run.evidence_hash) == 64
    assert len(deepseek.prompts) == 5
    assert len(sol.prompts) == 5
    primary = run.variants[0]
    assert primary.audit_decision is not None
    assert primary.audit_decision.status == "passed"
    assert run.final_artifact is primary.final_artifact
    assert len(
        run.final_artifact.payload["historical_analog_dossiers"]
    ) == 1
    assert (
        run.final_artifact.payload["dossier_handoff"]["owner"]
        == "deepseek_reconciliation_round_4"
    )
    assert run.analog_comparison is not None


@pytest.mark.parametrize(
    "profile_id",
    [
        "fast_screening",
        "adaptive_ic",
        "deep_initiation",
        "investigate_event",
    ],
)
def test_dual_choreography_participates_in_every_research_profile(
    profile_id: str,
) -> None:
    context = _context()
    context["research_profile"] = profile_id
    context["require_historical_dossiers"] = (
        profile_id == "deep_initiation"
    )
    run = run_dual_choreography_research(
        context,
        deepseek_provider=FixtureProvider(
            "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
        ),
        sol_provider=FixtureProvider(
            "openai", "gpt-5.6-sol", _dual_sol_payloads()
        ),
    )

    assert run.status == "completed"
    assert all(len(item.rounds) == 5 for item in run.variants)
    for round_number in range(1, 6):
        providers = {
            item.provider
            for variant in run.variants
            for item in variant.rounds
            if item.round_number == round_number
        }
        assert providers == {"deepseek", "openai"}


def test_primary_handoff_keeps_deepseek_dossier_when_sol_returns_audit_stub() -> None:
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
    )
    sol_payloads = _dual_sol_payloads()
    sol_payloads[4]["historical_analog_dossiers"] = [{
        "analog_id": "analog-1",
        "status": "accepted",
    }]
    sol = FixtureProvider("openai", "gpt-5.6-sol", sol_payloads)

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "completed"
    dossier = run.final_artifact.payload["historical_analog_dossiers"][0]
    assert dossier["episode"] == "Apple services and capital-return cycle"
    assert (
        run.final_artifact.payload["dossier_handoff"]["owner"]
        == "deepseek_reconciliation_round_4"
    )


def test_dossier_reference_namespace_and_shape_are_normalized() -> None:
    dossier = _complete_dossier()
    dossier["triggering_decisions"] = "Services investment and repurchases."
    dossier["source_ids"] = []
    dossier["dimensions"][0]["assessment"] = (
        "Historical mechanism is supported by [c2], not [deadbeefdead]."
    )
    dossier["dimensions"][0]["evidence_ids"] = []
    artifact, _ = _validate_round_artifact(
        4,
        "reconciliation_analyst",
        {
            "summary": "Reconciled",
            "citation_ids": ["e1"],
            "historical_analog_dossiers": [dossier],
        },
        _context(),
    )

    normalized = artifact.payload["historical_analog_dossiers"][0]
    assert normalized["triggering_decisions"] == [
        "Services investment and repurchases."
    ]
    assert normalized["source_ids"] == ["e2", "e3"]
    assert normalized["dimensions"][0]["evidence_ids"] == ["e2"]
    assert "deadbeefdead" not in normalized["dimensions"][0]["assessment"]
    assert artifact.evidence_ids == ["e1", "e2", "e3"]
    assert artifact.citation_ids == []


def test_non_auditor_round_cannot_emit_audit_decision() -> None:
    artifact, _findings = _validate_round_artifact(
        4,
        "reconciliation_analyst",
        {
            "summary": "Reconciled evidence.",
            "audit_decision": {
                "status": "revision_required",
            },
        },
        _context(),
    )

    assert "audit_decision" not in artifact.payload
    assert any(
        "only round 5" in gap for gap in artifact.validation_gaps
    )


def test_primary_sol_audit_retries_once_without_replacing_primary() -> None:
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
    )
    sol_payloads = _dual_sol_payloads()
    sol_payloads.insert(2, TimeoutError("audit timeout"))
    sol = FixtureProvider("openai", "gpt-5.6-sol", sol_payloads)

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "completed"
    primary = run.variants[0]
    assert primary.audit_decision is not None
    assert primary.audit_decision.retry_count == 1
    assert run.selected_primary_variant_id == "deepseek_led"
    assert len(sol.prompts) == 6


def test_transient_deepseek_reconciliation_retries_once() -> None:
    deepseek_payloads = _dual_deepseek_payloads()
    deepseek_payloads.insert(
        3,
        RuntimeError(
            "[WinError 10054] An existing connection was forcibly closed "
            "by the remote host"
        ),
    )
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", deepseek_payloads
    )
    sol = FixtureProvider(
        "openai", "gpt-5.6-sol", _dual_sol_payloads()
    )

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "completed"
    assert len(deepseek.prompts) == 6
    assert "Provider retry count: 1" in run.variants[0].rounds[3].message


def test_malformed_deepseek_reconciliation_retries_once() -> None:
    deepseek_payloads = _dual_deepseek_payloads()
    deepseek_payloads.insert(
        3,
        ValueError(
            "Unterminated string starting at: line 472 column 9 "
            "(char 27654)"
        ),
    )
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", deepseek_payloads
    )
    sol = FixtureProvider(
        "openai", "gpt-5.6-sol", _dual_sol_payloads()
    )

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "completed"
    reconciliation = run.variants[0].rounds[3]
    assert reconciliation.retry_count == 1
    assert "Provider retry count: 1" in reconciliation.message
    assert len(deepseek.prompts) == 6


def test_malformed_planning_output_is_not_retried() -> None:
    deepseek_payloads = [
        ValueError("Unterminated string starting at: line 1 column 1"),
        *_dual_deepseek_payloads(),
    ]
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", deepseek_payloads
    )
    sol = FixtureProvider(
        "openai", "gpt-5.6-sol", _dual_sol_payloads()
    )

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    primary_round_one = run.variants[0].rounds[0]
    assert primary_round_one.status == "failed"
    assert primary_round_one.retry_count == 0
    assert sum(
        prompt["round_number"] == 1 for prompt in deepseek.prompts
    ) == 1


def test_partial_run_resumes_completed_variant_prefixes() -> None:
    failed_deepseek_payloads = _dual_deepseek_payloads()
    failure = RuntimeError(
        "[WinError 10054] An existing connection was forcibly closed by "
        "the remote host"
    )
    failed_deepseek_payloads[3:4] = [failure, failure]
    partial = run_dual_choreography_research(
        _context(),
        deepseek_provider=FixtureProvider(
            "deepseek", "deepseek-v4-pro", failed_deepseek_payloads
        ),
        sol_provider=FixtureProvider(
            "openai", "gpt-5.6-sol", _dual_sol_payloads()
        ),
    )

    assert partial.status == "investigated_draft"
    assert len(partial.variants[0].rounds) == 4
    assert len(partial.variants[1].rounds) == 5

    resumed_deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", [_dual_deepseek_payloads()[3]]
    )
    resumed_sol = FixtureProvider(
        "openai", "gpt-5.6-sol", [_dual_sol_payloads()[4]]
    )
    resumed = run_dual_choreography_research(
        _context(),
        deepseek_provider=resumed_deepseek,
        sol_provider=resumed_sol,
        resume_from=partial,
    )

    assert resumed.status == "completed"
    assert len(resumed_deepseek.prompts) == 1
    assert len(resumed_sol.prompts) == 1
    assert len(resumed.variants[0].rounds) == 5
    assert len(resumed.variants[1].rounds) == 5
    assert resumed.generation_provenance["resumed_from_run_id"] == partial.run_id


def test_missing_explicit_sol_decision_suppresses_primary_dossier() -> None:
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
    )
    sol_payloads = _dual_sol_payloads()
    sol_payloads[4].pop("audit_decision")
    sol = FixtureProvider("openai", "gpt-5.6-sol", sol_payloads)

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "investigated_draft"
    assert run.final_artifact is None
    assert run.variants[0].audit_decision.status == "audit_unavailable"


def test_explicit_sol_rejection_is_failed_not_unavailable() -> None:
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
    )
    sol_payloads = _dual_sol_payloads()
    sol_payloads[4]["audit_decision"] = {
        "status": "failed",
        "critical_failures": ["Historical financing outcome is unsupported."],
        "unresolved_findings": ["Debt evidence"],
        "accepted_analog_ids": [],
        "rejected_analog_ids": ["analog-1"],
    }
    sol = FixtureProvider("openai", "gpt-5.6-sol", sol_payloads)

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "investigated_draft"
    primary = run.variants[0]
    assert primary.audit_decision.status == "failed"
    assert "sol_audit_failed" in primary.quality_scorecard.critical_failures
    assert run.audit_repair_queue is not None
    assert run.audit_repair_queue.items
    assert any(
        "Historical financing outcome is unsupported"
        in item.rejection_reason
        for item in run.audit_repair_queue.items
    )
    assert not any(
        "Historical financing outcome is unsupported"
        in item.claim_text
        for item in run.claim_evidence_audits
    )
    assert not any(
        "historical_dossier_0_incomplete" in item
        for item in primary.quality_scorecard.critical_failures
    )


def test_final_audit_repair_reruns_only_reconciliation_and_audit() -> None:
    context = _context()
    context["_enable_final_audit_repair"] = True
    # The audit finding in this fixture is a rewrite task, so the registered
    # retriever must exist but is not called.
    context["_retrieval_engine"] = NoopRetriever()

    deepseek_payloads = [
        *_dual_deepseek_payloads(),
        _dual_deepseek_payloads()[3],
    ]
    sol_payloads = _dual_sol_payloads()
    sol_payloads[4] = {
        **sol_payloads[4],
        "summary": "Sol rejected the structured citation contract.",
        "audit_decision": {
            "status": "failed",
            "critical_failures": [
                "The reconciled draft violates the structured citation contract."
            ],
            "unresolved_findings": [],
            "accepted_analog_ids": [],
            "rejected_analog_ids": ["analog-1"],
        },
    }
    sol_payloads.append(_dual_sol_payloads()[4])
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", deepseek_payloads
    )
    sol = FixtureProvider("openai", "gpt-5.6-sol", sol_payloads)

    run = run_dual_choreography_research(
        context,
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "completed"
    assert len(deepseek.prompts) == 6
    assert len(sol.prompts) == 6
    assert run.partial_rerun_manifest is not None
    assert run.partial_rerun_manifest.reused_round_numbers == [1, 2, 3]
    assert run.partial_rerun_manifest.rerun_round_numbers == [4, 5]
    assert (
        run.generation_provenance["final_audit_repair_pass"]
        == "executed_once"
    )


def test_sol_schema_rejection_creates_rewrite_not_fake_source_claim() -> None:
    sol_payloads = _dual_sol_payloads()
    sol_payloads[4]["audit_decision"] = {
        "status": "failed",
        "critical_failures": [
            "Evidence IDs appear in narrative prose instead of exclusively "
            "in structured evidence arrays.",
        ],
        "unresolved_findings": [],
        "accepted_analog_ids": [],
        "rejected_analog_ids": ["analog-1"],
    }
    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=FixtureProvider(
            "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
        ),
        sol_provider=FixtureProvider(
            "openai", "gpt-5.6-sol", sol_payloads
        ),
    )

    repair = next(
        item for item in run.audit_repair_queue.items
        if "Evidence IDs appear" in item.rejection_reason
    )
    assert repair.missing_source_type == "validation_rewrite"
    assert repair.retrieval_status == "not_applicable"
    assert repair.resolution == "rewrite_or_remove"
    assert repair.retrieval_request["repair_action"] == "rewrite_or_remove"
    assert not any(
        "Evidence IDs appear" in item.claim_text
        for item in run.claim_evidence_audits
    )


def test_nested_evidence_chain_is_bubbled_to_claim_without_redaction() -> None:
    payload = {
        "summary": "Period-aligned baseline.",
        "claims": [{
            "claim": "Operating cash flow increased in 2025.",
            "evidence_chain": [{
                "claim": "Operating cash flow increased.",
                "evidence_ids": ["e1"],
            }],
        }],
    }

    artifact, _findings = _validate_round_artifact(
        4, "reconciliation_analyst", payload, _context()
    )

    claim = artifact.payload["claims"][0]
    assert claim["claim"] == "Operating cash flow increased in 2025."
    assert claim["evidence_ids"] == ["e1"]
    assert "[unverified value]" not in json.dumps(artifact.payload)


def test_inline_evidence_reference_is_moved_to_structured_array() -> None:
    artifact, _findings = _validate_round_artifact(
        4,
        "reconciliation_analyst",
        {
            "summary": "Cash conversion improved [e1].",
            "claims": [],
        },
        _context(),
    )

    assert artifact.payload["summary"] == "Cash conversion improved."
    assert artifact.payload["evidence_ids"] == ["e1"]


def test_repair_round_receives_prior_failed_audit_contract() -> None:
    context = _context()
    context["_prior_audit_run_id"] = "parent-run"
    context["_prior_failed_audit"] = {
        "critical_failures": ["Remove unsupported price causality."],
        "unresolved_findings": ["Resolve financial duration."],
    }

    prompt = _round_prompt(
        4,
        "reconciliation_analyst",
        context,
        [
            ResearchRoundArtifact(
                artifact_id=f"a{index}",
                artifact_type="fixture",
                status="validated",
                summary="fixture",
                payload={"summary": "fixture"},
            )
            for index in range(3)
        ],
        {"initial": [], "follow_up": []},
    )

    assert prompt["prior_failed_audit"]["parent_run_id"] == "parent-run"
    assert (
        "Remove unsupported price causality."
        in prompt["prior_failed_audit"]["critical_failures"]
    )
    assert "Never repeat a previously rejected clause" in prompt["instruction"]


def test_failed_primary_audit_resumes_from_reconciliation() -> None:
    failed_sol_payloads = _dual_sol_payloads()
    failed_sol_payloads[4]["audit_decision"] = {
        "status": "failed",
        "critical_failures": ["Historical outcome exceeded supplied evidence."],
        "unresolved_findings": ["Historical valuation path"],
        "accepted_analog_ids": [],
        "rejected_analog_ids": ["analog-1"],
    }
    failed = run_dual_choreography_research(
        _context(),
        deepseek_provider=FixtureProvider(
            "deepseek", "deepseek-v4-pro", _dual_deepseek_payloads()
        ),
        sol_provider=FixtureProvider(
            "openai", "gpt-5.6-sol", failed_sol_payloads
        ),
    )
    assert failed.variants[0].audit_decision.status == "failed"

    resumed_deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", [_dual_deepseek_payloads()[3]]
    )
    resumed_sol = FixtureProvider(
        "openai", "gpt-5.6-sol", [_dual_sol_payloads()[4]]
    )
    resumed = run_dual_choreography_research(
        _context(),
        deepseek_provider=resumed_deepseek,
        sol_provider=resumed_sol,
        resume_from=failed,
    )

    assert resumed.status == "completed"
    assert len(resumed_deepseek.prompts) == 1
    assert len(resumed_sol.prompts) == 1
    assert len(resumed.variants[0].rounds) == 5
    assert resumed.variants[0].audit_decision.status == "passed"


def test_incomplete_reconciled_dossier_fails_quality_gate() -> None:
    deepseek_payloads = _dual_deepseek_payloads()
    deepseek_payloads[3]["historical_analog_dossiers"] = [{
        "analog_id": "analog-1",
        "episode": "Incomplete episode",
        "source_ids": ["e1", "e2"],
    }]
    deepseek = FixtureProvider(
        "deepseek", "deepseek-v4-pro", deepseek_payloads
    )
    sol = FixtureProvider(
        "openai", "gpt-5.6-sol", _dual_sol_payloads()
    )

    run = run_dual_choreography_research(
        _context(),
        deepseek_provider=deepseek,
        sol_provider=sol,
    )

    assert run.status == "investigated_draft"
    assert any(
        "historical_dossier_0_incomplete" in item
        for item in run.variants[0].quality_scorecard.critical_failures
    )


def test_provider_credentials_never_enter_run_prompt_or_persistence(tmp_path) -> None:
    planner = FixtureProvider("deepseek", "deepseek-v4-pro", _planner_payloads())
    planner.api_key = "deepseek-session-secret"
    executor = FixtureProvider("openai", "gpt-5.6-sol", _executor_payloads())
    executor.api_key = "openai-session-secret"

    run = run_hybrid_research(
        _context(),
        planner_provider=planner,
        executor_provider=executor,
    )
    serialized = json.dumps(asdict(run))
    prompts = json.dumps(planner.prompts + executor.prompts)
    assert "deepseek-session-secret" not in serialized
    assert "openai-session-secret" not in serialized
    assert "deepseek-session-secret" not in prompts
    assert "openai-session-secret" not in prompts

    database = tmp_path / "research.db"
    store = ResearchStore(database)
    store.save_hybrid_research_run(run)
    database_bytes = database.read_bytes()
    assert b"deepseek-session-secret" not in database_bytes
    assert b"openai-session-secret" not in database_bytes


def test_unknown_evidence_urls_sources_and_decision_fields_are_rejected() -> None:
    planner_payloads = _planner_payloads()
    planner_payloads[0]["source_requests"].extend([
        {
            "request_id": "bad-source",
            "source_type": "random_web",
            "title": "Browse arbitrary site",
        },
        {
            "request_id": "bad-url",
            "source_type": "sec_filing",
            "title": "Invented URL",
            "suggested_url": "https://unregistered.invalid/report",
        },
    ])
    executor_payloads = _executor_payloads()
    executor_payloads[-1].update({
        "fair_value": 999,
        "promotion": "High-Conviction",
        "claims": [
            {
                "claim": "Revenue grew 999% in 2024.",
                "evidence_ids": ["invented-evidence"],
                "citation_ids": ["invented-citation"],
            }
        ],
    })
    planner = FixtureProvider("deepseek", "deepseek-v4-pro", planner_payloads)
    executor = FixtureProvider("openai", "gpt-5.6-sol", executor_payloads)

    run = run_hybrid_research(
        _context(),
        planner_provider=planner,
        executor_provider=executor,
    )

    first = run.rounds[0].artifact
    final = run.rounds[-1].artifact
    assert first is not None and final is not None
    assert all(
        item["source_type"] != "random_web"
        for item in first.payload["source_requests"]
    )
    bad_url = next(
        item for item in first.payload["source_requests"]
        if item["request_id"] == "bad-url"
    )
    assert bad_url["suggested_url"] == ""
    assert "fair_value" not in final.payload
    assert "promotion" not in final.payload
    assert run.status == "investigated_draft"
    assert run.final_artifact is None
    assert run.audit_repair_queue is not None
    assert run.audit_repair_queue.items
    assert "invented-evidence" not in final.evidence_ids
    assert any("unknown evidence" in gap.lower() for gap in final.validation_gaps)


def test_timeout_preserves_investigated_draft() -> None:
    planner = FixtureProvider("deepseek", "deepseek-v4-pro", _planner_payloads())
    executor = FixtureProvider(
        "openai",
        "gpt-5.6-sol",
        [_executor_payloads()[0], TimeoutError("critic timeout")],
    )

    run = run_hybrid_research(
        _context(),
        planner_provider=planner,
        executor_provider=executor,
    )

    assert run.status == "investigated_draft"
    assert run.rounds[-1].status == "timeout"
    assert run.final_artifact is None
    assert run.rounds[0].artifact is not None


def test_openai_responses_provider_polls_background_response() -> None:
    calls = []

    def request(method, url, payload, headers, timeout):
        calls.append((method, url, payload, headers, timeout))
        if method == "POST":
            return {"id": "resp-1", "status": "queued"}
        return {
            "id": "resp-1",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"summary": "Completed"}),
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }

    provider = OpenAIResponsesProvider(
        "session-key",
        request_json=request,
        poll_interval_seconds=0,
    )
    payload = provider.complete_json({"task": "test"})

    assert payload["summary"] == "Completed"
    assert calls[0][0] == "POST"
    assert calls[1][0] == "GET"
    assert calls[0][2]["background"] is True
    assert calls[0][2]["reasoning"]["mode"] == "pro"
    assert provider.last_usage == {"input_tokens": 4, "output_tokens": 2}


def test_codex_local_provider_is_locked_down_and_structured(tmp_path) -> None:
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        output_path = command[command.index("-o") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump({"summary": "Local structured result"}, handle)
        return subprocess.CompletedProcess(command, 0, "", "")

    provider = CodexLocalProvider(enabled=True, runner=runner)
    payload = provider.complete_json({"task": "test"})

    assert payload["summary"] == "Local structured result"
    command = captured["command"]
    assert 'web_search="disabled"' in command
    assert 'approval_policy="never"' in command
    assert "read-only" in command
    assert "--output-schema" in command
    assert "shell" not in captured["kwargs"]
    assert captured["kwargs"]["encoding"] == "utf-8"
    assert captured["kwargs"]["errors"] == "replace"
    assert "session-key" not in captured["kwargs"]["input"]


def test_codex_local_uses_role_scoped_closure_reasoning(tmp_path) -> None:
    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        output_path = command[command.index("-o") + 1]
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump({"summary": "Closed"}, handle)
        return subprocess.CompletedProcess(command, 0, "", "")

    provider = CodexLocalProvider(
        enabled=True,
        runner=runner,
        reasoning_effort="max",
        closure_reasoning_effort="medium",
    )
    provider.complete_json({"role": "independent_research_auditor"})
    provider.complete_json({"role": "closure_checker"})

    assert 'model_reasoning_effort="max"' in commands[0]
    assert 'model_reasoning_effort="medium"' in commands[1]
    assert provider.last_reasoning_effort == "medium"


def test_codex_local_requires_explicit_enablement() -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        CodexLocalProvider(enabled=False).complete_json({"task": "test"})


def test_codex_wrapper_schema_is_strict_and_decodes_research_payload() -> None:
    schema = _codex_wrapper_schema()

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["json_payload"]
    assert _unwrap_codex_payload({
        "json_payload": json.dumps({"summary": "Audited"}),
    }) == {"summary": "Audited"}


def test_packaged_codex_path_is_detected_without_matching_other_apps() -> None:
    assert _is_packaged_codex_path(
        r"C:\Program Files\WindowsApps\OpenAI.Codex_1.2.3_x64\app"
        r"\resources\codex.exe"
    )
    assert not _is_packaged_codex_path(
        r"C:\Program Files\WindowsApps\Another.App_1.2.3_x64\codex.exe"
    )
    assert not _is_packaged_codex_path(r"C:\Tools\codex.exe")


def test_codex_desktop_runtime_is_discovered_when_process_path_is_stale(
    tmp_path, monkeypatch
) -> None:
    older = tmp_path / "OpenAI" / "Codex" / "bin" / "old" / "codex.exe"
    newer = tmp_path / "OpenAI" / "Codex" / "bin" / "new" / "codex.exe"
    older.parent.mkdir(parents=True)
    newer.parent.mkdir(parents=True)
    older.write_bytes(b"older")
    newer.write_bytes(b"newer")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        "equity_research.hybrid_research.shutil.which",
        lambda *args, **kwargs: None,
    )

    assert _windows_codex_desktop_candidates() == [newer, older]
    if os.name == "nt":
        assert _resolve_codex_executable("codex") == str(newer)


def test_background_job_can_be_cancelled_without_persisting_payload() -> None:
    manager = HybridResearchJobManager()
    started = threading.Event()

    def worker(cancel_event):
        started.set()
        while not cancel_event.is_set():
            time.sleep(0.005)
        return frozen_fixture_hybrid_run(
            "AAPL",
            evidence_hash="cancelled",
            summary="Cancelled",
            generated_at="2026-07-27T00:00:00+00:00",
        )

    state = manager.start("AAPL", worker)
    assert started.wait(1)
    cancelled = manager.cancel(state.job_id)
    assert cancelled is not None
    assert cancelled.status == "cancelling"
    for _ in range(100):
        latest = manager.get(state.job_id)
        if latest and latest.status != "cancelling":
            break
        time.sleep(0.005)
    assert manager.get(state.job_id) is not None
    assert not hasattr(manager.get(state.job_id), "api_key")


def test_background_manager_preserves_full_result_across_polling() -> None:
    manager = HybridResearchJobManager()
    completed = threading.Event()

    class FullResearchResult:
        ticker = "AAPL"

    def worker(_cancel_event):
        completed.set()
        return FullResearchResult()

    state = manager.start("AAPL", worker)
    assert completed.wait(1)
    for _ in range(100):
        latest = manager.get(state.job_id)
        if latest and latest.status == "completed":
            break
        time.sleep(0.005)
    latest = manager.get(state.job_id)
    assert latest is not None
    assert latest.status == "completed"
    assert isinstance(latest.result, FullResearchResult)


def test_research_qa_preflight_keeps_missing_auditor_optional() -> None:
    deepseek = FixtureProvider("deepseek", "deepseek-v4-pro", [])

    missing_sol = build_hybrid_execution_preflight(
        "AAPL",
        deepseek_provider=deepseek,
        sol_provider=None,
        research_profile="deep_initiation",
        deepseek_credential_source="session BYOK",
        sol_credential_source="missing",
        evidence_status="96/100, benchmark-ready",
    )

    assert missing_sol.status == "ready"
    assert missing_sol.deepseek.status == "ready"
    assert missing_sol.sol.status == "missing"
    assert missing_sol.blockers == []
    assert "optional" in missing_sol.sol.message.lower()
    assert "session BYOK" in json.dumps(asdict(missing_sol))
    assert "api_key" not in json.dumps(asdict(missing_sol))


def test_paired_research_preflight_rejects_silent_model_substitution() -> None:
    fast_deepseek = FixtureProvider("deepseek", "deepseek-v4-flash", [])
    sol = FixtureProvider("openai", "gpt-5.6-sol", [])

    preflight = build_hybrid_execution_preflight(
        "AAPL",
        deepseek_provider=fast_deepseek,
        sol_provider=sol,
        research_profile="adaptive_ic",
    )

    assert preflight.status == "blocked"
    assert preflight.deepseek.status == "unavailable"
    assert "deepseek-v4-pro" in preflight.deepseek.message


def test_research_qa_preflight_discloses_unavailable_optional_local_codex() -> None:
    deepseek = FixtureProvider("deepseek", "deepseek-v4-pro", [])
    local_sol = CodexLocalProvider(
        model="gpt-5.6-sol",
        executable="definitely-missing-codex-executable",
        enabled=True,
    )

    preflight = build_hybrid_execution_preflight(
        "AAPL",
        deepseek_provider=deepseek,
        sol_provider=local_sol,
        research_profile="deep_initiation",
    )

    assert preflight.status == "ready"
    assert preflight.sol.status == "unavailable"
    assert "Could not locate" in preflight.sol.message


def test_background_job_refuses_blocked_preflight() -> None:
    manager = HybridResearchJobManager()
    preflight = build_hybrid_execution_preflight(
        "AAPL",
        deepseek_provider=None,
        sol_provider=None,
        research_profile="deep_initiation",
    )

    with pytest.raises(Exception, match="did not start"):
        manager.start("AAPL", lambda _cancel: None, preflight=preflight)


def test_background_job_tracks_progress_and_displayed_result_relationship() -> None:
    manager = HybridResearchJobManager()
    completed = threading.Event()
    deepseek = FixtureProvider("deepseek", "deepseek-v4-pro", [])
    sol = FixtureProvider("openai", "gpt-5.6-sol", [])
    preflight = build_hybrid_execution_preflight(
        "AAPL",
        deepseek_provider=deepseek,
        sol_provider=sol,
        research_profile="deep_initiation",
    )
    displayed = DisplayedResultContext(
        result_type="frozen_demo",
        label="Frozen AAPL demo",
        ticker="AAPL",
    )

    def worker(_cancel_event, progress_callback):
        progress_callback({
            "status": "running",
            "current_stage": "Evidence investigation",
            "current_round": 2,
            "provider": "deepseek",
            "model": "deepseek-v4-pro",
            "attempted_increment": 1,
            "completed_increment": 1,
        })
        completed.set()
        return type("Result", (), {"status": "completed"})()

    state = manager.start(
        "AAPL",
        worker,
        research_profile="deep_initiation",
        preflight=preflight,
        displayed_result=displayed,
        operation_kind="targeted_investigation",
        operation_label="Resolve AAPL Forward EBITDA",
        investigation_action_id="action-ebitda",
        investigation_action_kind="valuation_input_resolution",
        investigation_semantic_key=(
            "valuation_input_resolution:AAPL:company:sec_filings"
        ),
        result_route="Evidence",
    )
    assert completed.wait(1)
    for _ in range(100):
        latest = manager.get(state.job_id)
        if latest and latest.status == "completed":
            break
        time.sleep(0.005)

    latest = manager.get(state.job_id)
    assert latest is not None
    assert latest.displayed_result.label == "Frozen AAPL demo"
    assert latest.research_profile == "deep_initiation"
    assert latest.progress.current_stage == "Evidence investigation"
    assert latest.progress.attempted_model_calls == 1
    assert latest.progress.completed_model_calls == 1
    assert latest.operation_kind == "targeted_investigation"
    assert latest.operation_label == "Resolve AAPL Forward EBITDA"
    assert latest.investigation_action_id == "action-ebitda"
    assert latest.investigation_action_kind == "valuation_input_resolution"
    assert latest.investigation_semantic_key.endswith("sec_filings")
    assert latest.result_route == "Evidence"


def test_background_progress_is_monotonic_and_persists_only_coarse_changes() -> None:
    class Lifecycle:
        def __init__(self) -> None:
            self.events = []

        def mark_running(self, *args, **kwargs) -> None:
            return None

        def record_event(self, run_id, **kwargs) -> None:
            self.events.append((run_id, kwargs))

        def finish(self, *args, **kwargs) -> None:
            return None

    lifecycle = Lifecycle()
    manager = HybridResearchJobManager(lifecycle)
    progress_sent = threading.Event()
    release = threading.Event()

    def worker(_cancel_event, progress_callback):
        for _ in range(12):
            progress_callback({
                "status": "running",
                "current_stage": "Searching SEC evidence",
                "current_round": 1,
                "message": "Reading the same source batch.",
            })
        progress_callback({
            "status": "running",
            "current_stage": "DeepSeek research planner",
            "current_role": "research_planner",
            "current_round": 2,
            "message": "Building the research plan.",
        })
        progress_sent.set()
        release.wait(1)
        return type("Result", (), {"status": "completed"})()

    state = manager.start("AAPL", worker)
    assert progress_sent.wait(1)
    active = manager.get(state.job_id)
    assert active is not None
    assert active.progress.journey_stage == "primary_analysis"
    assert len(lifecycle.events) == 2
    assert [item[1]["stage"] for item in lifecycle.events] == [
        "evidence_retrieval_and_admission",
        "primary_analysis",
    ]
    release.set()


def test_mandatory_targeted_request_runs_when_planner_omits_it() -> None:
    context = {
        "searchable_evidence_catalog": [],
        "_mandatory_source_requests": [{
            "request_id": "selected-cash-action",
            "source_type": "sec_filing",
            "title": "Test AAPL cash conversion",
            "reason": "User-selected targeted investigation.",
        }],
    }

    outcomes = _execute_registered_requests(
        context,
        {"source_requests": []},
    )

    assert len(outcomes) == 1
    assert outcomes[0]["request_id"] == "selected-cash-action"
    assert outcomes[0]["status"] == "unavailable"


def test_hybrid_run_round_trips_through_store(tmp_path) -> None:
    run = frozen_fixture_hybrid_run(
        "AAPL",
        evidence_hash="hash",
        summary="Fixture summary",
        generated_at="2026-07-27T00:00:00+00:00",
    )
    store = ResearchStore(tmp_path / "research.db")
    store.save_hybrid_research_run(run)

    payload = store.load_hybrid_research_run(run.run_id)
    assert payload is not None
    restored = hybrid_run_from_payload(payload)
    assert restored.run_id == run.run_id
    assert len(restored.rounds) == 5
    assert restored.final_artifact is not None
    assert restored.final_artifact.summary == "Fixture summary"


def test_sol_audit_health_warns_after_three_failures_and_resets(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research.db")

    for _ in range(3):
        status = store.record_llm_audit_result(
            "gpt-5.6-sol",
            success=False,
            failure_class="timeout",
            message="Audit timed out after retry.",
        )

    assert status["consecutive_failures"] == 3
    assert status["failure_class"] == "timeout"
    reset = store.record_llm_audit_result(
        "gpt-5.6-sol",
        success=True,
        message="Audit passed.",
    )
    assert reset["consecutive_failures"] == 0
    assert reset["last_success_at"]


@pytest.mark.parametrize(
    "ticker", ["AAPL", "BABA", "BLK", "NVDA", "ORCL", "TSLA", "GS"]
)
def test_frozen_demos_include_no_network_hybrid_provenance(ticker: str) -> None:
    result = demo_result(ticker)
    run = result.hybrid_research
    assert run is not None
    assert run.status == "completed"
    assert len(run.rounds) >= 5
    assert run.final_artifact is not None
    assert run.cache_status == "frozen_fixture"
    assert run.deterministic_validation_status == "passed"
    assert result.dual_model_research.provenance_class == "sanitized_fixture"
    assert "No live model executed" in (
        result.dual_model_research.provenance_message
    )
    serialized = json.dumps(asdict(run))
    assert not contains_credential_like_token(serialized)
