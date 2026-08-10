from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from equity_research.auditable_retrieval import (
    evaluate_demo_freeze,
    partial_rerun_eligible,
)
from equity_research.hybrid_research import (
    hybrid_run_from_payload,
    load_frozen_hybrid_run,
)
from equity_research.models import DemoQualityPolicy
from scripts.generate_hybrid_demo import _sort_saved_candidate_payloads


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "aapl_freeze_failures"


LEGACY_DEMO_POLICY = DemoQualityPolicy(
    policy_version="demo-quality-policy-v2",
    minimum_publishable_score=85,
    publication_quality_score=85,
    require_audited_analog=True,
    ticker_minimum_scores={"AAPL": 85},
)


@pytest.fixture(params=(
    "incomplete_newer_draft.json",
    "completed_nonpublishable.json",
))
def freeze_failure_fixture(request) -> dict:
    return json.loads((FIXTURE_DIR / request.param).read_text(encoding="utf-8"))


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _dossiers(payload: dict) -> list[SimpleNamespace]:
    return [SimpleNamespace(**item) for item in payload["audited_dossiers"]]


def _failure_codes(decision) -> set[str]:
    codes: set[str] = set()
    blockers = "\n".join(decision.blockers).casefold()
    if "run is not completed" in blockers:
        codes.add("run_incomplete")
    if decision.validation_status == "not_run":
        codes.add("deterministic_validation_not_run")
    elif decision.validation_status != "passed":
        codes.add("deterministic_validation_partial")
    if "did not pass the final" in blockers:
        codes.add("final_closure_not_passed")
    if "85/100 semantic quality gate" in blockers:
        codes.add("semantic_quality_below_threshold")
    if "fewer than two independent validated source origins" in blockers:
        codes.add("analog_independent_provenance_insufficient")
    return codes


def _evaluate(payload: dict, tmp_path: Path):
    run = hybrid_run_from_payload(deepcopy(payload["run"]))
    return run, evaluate_demo_freeze(
        "AAPL",
        run=run,
        audited_dossiers=_dossiers(payload),
        output_path=str(tmp_path / "AAPL.json"),
        policy=LEGACY_DEMO_POLICY,
    )


def test_failure_fixtures_are_sanitized_and_preserve_source_identity(
    freeze_failure_fixture: dict,
) -> None:
    serialized = json.dumps(freeze_failure_fixture).casefold()

    assert freeze_failure_fixture["fixture_version"] == "aapl-freeze-regression-v1"
    assert freeze_failure_fixture["sanitization_status"] == "passed"
    assert freeze_failure_fixture["source_run_id"] == freeze_failure_fixture["run"]["run_id"]
    assert "api_key" not in serialized
    assert "authorization" not in serialized
    assert "bearer " not in serialized
    assert "hidden reasoning" not in serialized


def test_incomplete_newer_draft_reproduces_gates_and_resume_rules(tmp_path) -> None:
    payload = _load("incomplete_newer_draft.json")
    run, decision = _evaluate(payload, tmp_path)

    assert run.status == "investigated_draft"
    assert run.completed_at == ""
    assert payload["observed_attempt_finished_at"]
    assert decision.allowed is False
    assert decision.source_run_id == payload["source_run_id"]
    assert decision.source_run_completed_at == ""
    assert set(payload["expected_failure_codes"]) == _failure_codes(decision)

    parent = payload["run"]["evidence_fingerprints"]
    eligible, additions = partial_rerun_eligible(
        parent,
        payload["resume_cases"]["strictly_additive"],
    )
    assert eligible is True
    assert additions == ["fixture-evidence-2"]
    assert partial_rerun_eligible(
        parent,
        payload["resume_cases"]["unchanged"],
    ) == (True, [])
    assert partial_rerun_eligible(
        parent,
        payload["resume_cases"]["changed"],
    ) == (False, [])


def test_completed_run_is_not_equivalent_to_publication_eligibility(tmp_path) -> None:
    payload = _load("completed_nonpublishable.json")
    run, decision = _evaluate(payload, tmp_path)

    assert run.status == "completed"
    assert run.completed_at
    assert decision.allowed is False
    assert decision.validation_status == "partial"
    assert decision.qa_closure_state == "failed_material_findings_unaddressed"
    assert set(payload["expected_failure_codes"]) == _failure_codes(decision)


def test_expected_repairs_are_typed_and_cover_every_fixture_failure(
    freeze_failure_fixture: dict,
) -> None:
    actions = freeze_failure_fixture["expected_repair_actions"]
    kinds = {item["repair_kind"] for item in actions}

    assert actions
    assert all(item["objective"].endswith(".") for item in actions)
    assert "retrieve_independent_analog_origin" in kinds
    assert "rerun_closure" in kinds
    if freeze_failure_fixture["fixture_name"] == "incomplete_newer_draft":
        assert "complete_deterministic_validation" in kinds
    else:
        assert "retrieve_missing_primary_evidence" in kinds
        assert "recompute_semantic_quality" in kinds


def test_independent_origin_repair_removes_analog_provenance_blocker(tmp_path) -> None:
    payload = _load("completed_nonpublishable.json")
    repaired = deepcopy(payload)
    second_origin = deepcopy(repaired["run"]["normalized_sources"][0])
    second_origin.update({
        "source_id": "fixture-analog-origin-b",
        "document_id": "sanitized-document-b",
        "source_family": "independent_market_archive",
        "source_type": "historical_market_data",
        "origin_group": "independent-origin-b",
    })
    repaired["run"]["normalized_sources"].append(second_origin)
    for dossier in repaired["audited_dossiers"]:
        dossier["source_ids"].append("fixture-analog-origin-b")

    _, decision = _evaluate(repaired, tmp_path)

    assert not any(
        "fewer than two independent validated source origins" in blocker
        for blocker in decision.blockers
    )
    assert decision.allowed is False


def test_full_repair_path_can_reach_publication_eligibility(tmp_path) -> None:
    payload = _load("completed_nonpublishable.json")
    repaired = deepcopy(payload)
    repaired["run"]["deterministic_validation_status"] = "passed"
    primary = repaired["run"]["variants"][0]
    primary["audit_decision"] = {
        "status": "passed",
        "decision": "All material findings were addressed against the revised artifact.",
    }
    primary["quality_scorecard"] = {
        "total_score": 90,
        "status": "passed",
        "critical_failures": [],
    }
    second_origin = deepcopy(repaired["run"]["normalized_sources"][0])
    second_origin.update({
        "source_id": "fixture-analog-origin-b",
        "document_id": "sanitized-document-b",
        "source_family": "independent_market_archive",
        "source_type": "historical_market_data",
        "origin_group": "independent-origin-b",
    })
    repaired["run"]["normalized_sources"].append(second_origin)
    for dossier in repaired["audited_dossiers"]:
        dossier["source_ids"].append("fixture-analog-origin-b")

    _, decision = _evaluate(repaired, tmp_path)

    assert decision.allowed is True
    assert decision.status == "pass"
    assert decision.blockers == []
    assert decision.qa_closure_state == "closed"


def test_neither_failure_fixture_can_be_loaded_as_public_demo(
    freeze_failure_fixture: dict,
    tmp_path: Path,
) -> None:
    demo_path = tmp_path / "AAPL.json"
    demo_path.write_text(
        json.dumps(freeze_failure_fixture["run"]),
        encoding="utf-8",
    )

    assert load_frozen_hybrid_run("AAPL", directory=tmp_path) is None
    assert json.loads(demo_path.read_text(encoding="utf-8"))["run_id"] == (
        freeze_failure_fixture["source_run_id"]
    )


def test_repaired_fixture_remains_secret_free() -> None:
    payload = _load("completed_nonpublishable.json")
    run = hybrid_run_from_payload(payload["run"])
    serialized = json.dumps(asdict(run)).casefold()

    assert "api_key" not in serialized
    assert "bearer " not in serialized


def test_newest_candidate_is_evaluated_first_without_becoming_eligible() -> None:
    incomplete = _load("incomplete_newer_draft.json")["run"]
    completed = _load("completed_nonpublishable.json")["run"]

    ordered = _sort_saved_candidate_payloads([completed, incomplete])

    assert ordered[0]["run_id"] == "1ddb8c37-7a71-4d2c-95d4-02558d7367d6"
    assert ordered[0]["status"] == "investigated_draft"
    assert ordered[1]["run_id"] == "b3f8d260-a2a5-4c52-9ad9-80ab6fecc898"
