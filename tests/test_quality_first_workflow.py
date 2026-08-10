from __future__ import annotations

from equity_research.hybrid_research import run_quality_first_research
from equity_research.models import NormalizedSourceRecord, SourceRetrievalLifecycle


class RecordingProvider:
    def __init__(self, provider_name: str, model: str, payloads) -> None:
        self.provider_name = provider_name
        self.model = model
        self.payloads = list(payloads)
        self.prompts = []
        self.last_usage = {"input_tokens": 10, "output_tokens": 5}
        self.last_cost_usd = 0.01

    def complete_json(self, prompt):
        self.prompts.append(prompt)
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        return payload


class IncrementingRetriever:
    def __init__(self):
        self.calls = 0

    def retrieve(self, *, request_id, source_type, query, period, result_limit):
        self.calls += 1
        source_id = f"retrieved-{self.calls}"
        record = NormalizedSourceRecord(
            source_id=source_id,
            ticker="AAPL",
            entity_name="Apple Inc.",
            security_id="AAPL",
            source_family="primary_official",
            source_type="sec_filing",
            document_id=f"filing-{self.calls}",
            source_url=f"https://www.sec.gov/example-{self.calls}",
            publication_date="2026-07-01",
            reporting_period="2026-06-30",
            section="Risk Factors",
            page_or_table="Item 1A",
            exact_excerpt="The filing contains material source text for the requested investigation.",
            metric_or_claim=query,
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
        lifecycle = SourceRetrievalLifecycle(
            request_id=request_id,
            source_id=source_id,
            source_type=source_type,
            status="completed",
            history=["planned", "retrieved", "parsed", "validated"],
            completed_at="2026-07-02T00:00:00+00:00",
        )
        return [record], lifecycle


def _context():
    return {
        "ticker": "AAPL",
        "company": "Apple Inc.",
        "research_profile": "adaptive_ic",
        "evidence_hash": "frozen-evidence-hash",
        "source_registry_version": "source-registry-v1",
        "allowed_source_types": ["sec_filing"],
        "evidence_catalog": [
            {
                "evidence_id": "e1",
                "citation_id": "c1",
                "source_type": "sec_filing",
                "source_url": "https://www.sec.gov/example",
                "summary": "Operating cash flow increased.",
                "kind": "financial_metric",
                "period": "2025",
                "validation_status": "validated",
            }
        ],
        "claim_eligible_evidence_ids": ["e1"],
    }


def test_quality_first_sequence_preserves_frozen_audit_before_scout_and_closure_last() -> None:
    primary = RecordingProvider(
        "deepseek",
        "deepseek-v4-pro",
        [
            {"summary": "Plan the primary-source work."},
            {"summary": "Primary thesis.", "evidence_ids": ["e1"]},
            {
                "summary": "Primary revised after audit.",
                "evidence_ids": ["e1"],
                "primary_responses": [
                    {
                        "finding_id": "finding-1",
                        "state": "accepted",
                        "explanation": "The conclusion was narrowed.",
                        "conclusion_changed": True,
                    }
                ],
            },
        ],
    )
    auditor = RecordingProvider(
        "codex_local",
        "gpt-5.6-sol",
        [
            {
                "summary": "Frozen independent audit.",
                "findings": [
                    {
                        "finding_id": "finding-1",
                        "severity": "High",
                        "summary": "The regulatory countercase needs testing.",
                        "what_would_change_assessment": "An admitted regulator source.",
                    }
                ],
            },
            {
                "summary": "Candidate discovery only.",
                "source_candidates": [
                    {
                        "title": "Potential regulator release",
                        "url": "https://www.ftc.gov/example",
                        "publisher": "Federal Trade Commission",
                        "source_type": "regulator_release",
                        "reason": "Test the audit finding.",
                    }
                ],
            },
            {
                "summary": "Every material finding was addressed.",
                "closure_state": "closed_with_residual_risk",
                "audit_decision": {
                    "status": "passed",
                    "critical_failures": [],
                    "unresolved_findings": [],
                },
            },
        ],
    )

    run = run_quality_first_research(
        _context(),
        primary_provider=primary,
        auditor_provider=auditor,
    )

    assert [round_.role for round_ in run.rounds] == [
        "research_planner",
        "primary_investment_analyst",
        "independent_research_auditor",
        "adversarial_evidence_scout",
        "primary_revision",
        "closure_checker",
    ]
    frozen_prompt, scout_prompt, closure_prompt = auditor.prompts
    assert frozen_prompt["frozen_evidence_hash"] == "frozen-evidence-hash"
    assert frozen_prompt["independence_contract"] == {
        "scouting_results_visible": False,
        "follow_up_evidence_visible": False,
        "mutable_primary_context_visible": False,
    }
    assert "scout_artifact" not in frozen_prompt
    assert scout_prompt["audit_artifact"]["summary"] == "Frozen independent audit."
    assert closure_prompt["role"] == "closure_checker"
    assert run.status == "completed"
    assert run.final_artifact.payload["closure_state"] == "closed_with_residual_risk"


def test_missing_optional_auditor_preserves_primary_result_and_labels_qa_not_run() -> None:
    primary = RecordingProvider(
        "deepseek",
        "deepseek-v4-pro",
        [
            {"summary": "Plan."},
            {"summary": "Primary-only research.", "evidence_ids": ["e1"]},
        ],
    )

    run = run_quality_first_research(
        _context(),
        primary_provider=primary,
        auditor_provider=None,
    )

    assert run.status == "completed"
    assert run.final_artifact.payload["independent_qa_status"] == "not_run"
    assert run.final_artifact.payload["closure_state"] == (
        "indeterminate_provider_or_evidence_failure"
    )
    assert any(round_.role == "closure_checker" and round_.status == "not_configured" for round_ in run.rounds)


def test_quality_first_planner_retries_truncated_json_once() -> None:
    primary = RecordingProvider(
        "deepseek",
        "deepseek-v4-pro",
        [
            ValueError("Unterminated string starting at: line 10 column 200"),
            {"summary": "Recovered research plan."},
            {"summary": "Primary research after recovery.", "evidence_ids": ["e1"]},
        ],
    )

    run = run_quality_first_research(
        _context(),
        primary_provider=primary,
        auditor_provider=None,
    )

    assert run.status == "completed"
    assert run.rounds[0].status == "completed"
    assert run.rounds[0].retry_count == 1
    assert "Provider retry count: 1" in run.rounds[0].message
    assert len(primary.prompts) == 3


def test_deep_initiation_allows_two_bounded_investigation_cycles_before_final_closure() -> None:
    context = _context()
    context["research_profile"] = "deep_initiation"
    context["quality_first_execution"] = {
        "max_investigation_cycles": 2,
        "stop_on_no_new_evidence": True,
        "specialist_roles": ["industry_specialist"],
    }
    retriever = IncrementingRetriever()
    context["_retrieval_engine"] = retriever
    primary = RecordingProvider(
        "deepseek",
        "deepseek-v4-pro",
        [
            {"summary": "Plan."},
            {"summary": "Primary."},
            {
                "summary": "First revision retains an open material issue.",
                "primary_responses": [{
                    "finding_id": "material-1",
                    "state": "unresolved",
                    "explanation": "One more source is required.",
                }],
            },
            {
                "summary": "Second revision addresses the material issue.",
                "primary_responses": [{
                    "finding_id": "material-1",
                    "state": "accepted",
                    "explanation": "The conclusion was narrowed.",
                    "conclusion_changed": True,
                }],
            },
        ],
    )
    auditor = RecordingProvider(
        "codex_local",
        "gpt-5.6-sol",
        [
            {
                "summary": "Frozen audit.",
                "findings": [{
                    "finding_id": "material-1",
                    "severity": "High",
                    "summary": "Material regulatory evidence is incomplete.",
                }],
                "source_requests": [{
                    "request_id": "regulatory-request",
                    "source_type": "sec_filing",
                    "title": "Retrieve the comparable filing section",
                    "objective": "Test the material regulatory finding.",
                }],
            },
            {"summary": "First bounded scout pass."},
            {"summary": "Second bounded scout pass."},
            {
                "summary": "Closure passed.",
                "closure_state": "closed",
                "audit_decision": {"status": "passed"},
            },
        ],
    )

    run = run_quality_first_research(
        context,
        primary_provider=primary,
        auditor_provider=auditor,
    )

    assert [item.role for item in run.rounds] == [
        "research_planner",
        "primary_investment_analyst",
        "independent_research_auditor",
        "adversarial_evidence_scout",
        "primary_revision",
        "adversarial_evidence_scout",
        "primary_revision",
        "closure_checker",
    ]
    assert retriever.calls == 2
    assert run.status == "completed"
    assert run.generation_provenance["investigation_cycles_completed"] == "2"
    assert run.generation_provenance["stopping_reason"] == "all_material_findings_resolved"


def test_configured_cost_limit_stops_without_implying_closure_passed() -> None:
    context = _context()
    context["quality_first_execution"] = {
        "max_investigation_cycles": 1,
        "max_model_spend_usd": 0.02,
    }
    primary = RecordingProvider(
        "deepseek",
        "deepseek-v4-pro",
        [{"summary": "Plan."}, {"summary": "Primary."}],
    )
    auditor = RecordingProvider(
        "codex_local",
        "gpt-5.6-sol",
        [{"summary": "Frozen audit.", "findings": []}],
    )

    run = run_quality_first_research(
        context,
        primary_provider=primary,
        auditor_provider=auditor,
    )

    assert [item.role for item in run.rounds] == [
        "research_planner",
        "primary_investment_analyst",
        "independent_research_auditor",
    ]
    assert run.status == "investigated_draft"
    assert run.final_artifact.payload["closure_state"] == (
        "indeterminate_provider_or_evidence_failure"
    )
    assert run.generation_provenance["stopping_reason"] == "configured_cost_limit"


def test_auditor_provider_failure_preserves_primary_artifact_as_partial() -> None:
    primary = RecordingProvider(
        "deepseek",
        "deepseek-v4-pro",
        [{"summary": "Plan."}, {"summary": "Primary."}],
    )
    auditor = RecordingProvider(
        "codex_local",
        "gpt-5.6-sol",
        [RuntimeError("fixture provider failure")],
    )

    run = run_quality_first_research(
        _context(),
        primary_provider=primary,
        auditor_provider=auditor,
    )

    assert run.status == "investigated_draft"
    assert any(item.role == "independent_research_auditor" and item.status == "failed" for item in run.rounds)
    assert any("fixture provider failure" in gap for gap in run.data_gaps)


def test_deterministic_closure_overrides_model_pass_when_material_finding_is_ignored() -> None:
    primary = RecordingProvider(
        "deepseek",
        "deepseek-v4-pro",
        [
            {"summary": "Plan."},
            {"summary": "Primary."},
            {
                "summary": "Revision ignored the material finding.",
                "primary_responses": [{
                    "finding_id": "material-ignored",
                    "state": "unresolved",
                    "explanation": "No evidence was obtained.",
                }],
            },
        ],
    )
    auditor = RecordingProvider(
        "codex_local",
        "gpt-5.6-sol",
        [
            {
                "summary": "Frozen audit.",
                "findings": [{
                    "finding_id": "material-ignored",
                    "severity": "High",
                    "summary": "Material counter-evidence is missing.",
                }],
            },
            {"summary": "Scout found no admissible source."},
            {
                "summary": "Incorrect model pass.",
                "closure_state": "closed",
                "audit_decision": {"status": "passed"},
            },
        ],
    )

    run = run_quality_first_research(
        _context(),
        primary_provider=primary,
        auditor_provider=auditor,
    )

    assert run.status == "investigated_draft"
    closure = run.final_artifact.payload["closure_audit"]
    assert closure["closure_state"] == "failed_material_findings_unaddressed"
    assert closure["audit_decision"]["status"] == "failed"
    assert any(
        "material-ignored remains unresolved" in item
        for item in closure["deterministic_closure_failures"]
    )
