from __future__ import annotations

import json
from dataclasses import asdict

from equity_research.dual_model_benchmark import (
    build_dual_model_research_run,
    dual_model_prompt_payload,
    dual_model_run_from_payload,
)
from equity_research.models import (
    AnalogResearchVariant,
    ExecutionBackend,
    HybridResearchProfile,
    HybridResearchRun,
    ResearchRound,
    ResearchRoundArtifact,
)
from equity_research.research_store import ResearchStore


def _round(number: int, provider: str, model: str, payload: dict) -> ResearchRound:
    artifact = ResearchRoundArtifact(
        artifact_id=f"{provider}-{number}",
        artifact_type="dual_model_stage",
        status="completed",
        summary=str(payload.get("summary") or ""),
        evidence_ids=list(payload.get("evidence_ids") or []),
        citation_ids=list(payload.get("citation_ids") or []),
        payload=payload,
    )
    return ResearchRound(
        round_number=number,
        role="research",
        provider=provider,
        model=model,
        backend="fixture",
        status="completed",
        latency_ms=number * 10,
        artifact=artifact,
    )


def _hybrid_run() -> HybridResearchRun:
    deep_payloads = [
        {
            "summary": "Plan management, peer, macro, and valuation checks.",
            "findings": [{
                "summary": "Investigate the demand driver.",
                "evidence_ids": ["e1"],
            }],
            "evidence_ids": ["e1"],
        },
        {
            "summary": "Investigate management history and operating metrics.",
            "claims": [{
                "claim": "Operating cash flow improved.",
                "evidence_ids": ["e1"],
            }],
            "evidence_ids": ["e1"],
        },
        {
            "summary": "Challenge the independent investigation.",
            "hidden_risks": [{
                "summary": "Demand may not convert to cash.",
                "evidence_ids": ["e1"],
            }],
            "evidence_ids": ["e1"],
        },
        {
            "summary": "Reconcile the causal bridge and valuation context.",
            "monitors": [{
                "summary": "Monitor free-cash-flow conversion.",
                "evidence_ids": ["e1"],
            }],
            "evidence_ids": ["e1"],
        },
        {
            "summary": "Challenge the final interpretation.",
            "limitations": [{
                "summary": "The event window remains short.",
                "evidence_ids": ["e2"],
            }],
            "evidence_ids": ["e2"],
        },
    ]
    sol_payloads = [
        {
            "summary": "Plan an independent hidden-risk and peer check.",
            "findings": [{
                "summary": "Investigate supplier commitments.",
                "evidence_ids": ["e2"],
            }],
            "evidence_ids": ["e2"],
        },
        {
            "summary": "Investigate peers, geography, and management context.",
            "claims": [{
                "claim": "Peer demand remained constructive.",
                "evidence_ids": ["e2"],
            }],
            "evidence_ids": ["e2"],
        },
        {
            "summary": "Audit the primary causal chain.",
            "hidden_risks": [{
                "summary": "Supplier commitments create downside asymmetry.",
                "evidence_ids": ["e2"],
            }],
            "evidence_ids": ["e2"],
        },
        {
            "summary": "Reconcile peer, credit, and valuation effects.",
            "monitors": [{
                "summary": "Monitor commitments relative to demand.",
                "evidence_ids": ["e2"],
            }],
            "evidence_ids": ["e2"],
        },
        {
            "summary": "Audit passed with an evidence-bound secondary addition.",
            "claims": [
                {
                    "claim": "Supplier commitments remain a hidden risk.",
                    "evidence_ids": ["e2"],
                },
                {
                    "claim": "An invented unsupported fact must be rejected.",
                    "evidence_ids": ["missing-evidence"],
                },
            ],
            "audit_decision": {
                "status": "passed",
                "unresolved_findings": [],
            },
            "evidence_ids": ["e2"],
        },
    ]
    primary = [
        _round(1, "deepseek", "deepseek-v4-pro", deep_payloads[0]),
        _round(2, "deepseek", "deepseek-v4-pro", deep_payloads[1]),
        _round(3, "openai", "gpt-5.6-sol", sol_payloads[2]),
        _round(4, "deepseek", "deepseek-v4-pro", deep_payloads[3]),
        _round(5, "openai", "gpt-5.6-sol", sol_payloads[4]),
    ]
    challenger = [
        _round(1, "openai", "gpt-5.6-sol", sol_payloads[0]),
        _round(2, "openai", "gpt-5.6-sol", sol_payloads[1]),
        _round(3, "deepseek", "deepseek-v4-pro", deep_payloads[2]),
        _round(4, "openai", "gpt-5.6-sol", sol_payloads[3]),
        _round(5, "deepseek", "deepseek-v4-pro", deep_payloads[4]),
    ]
    return HybridResearchRun(
        run_id="dual-fixture",
        ticker="AAPL",
        status="completed",
        profile=HybridResearchProfile(
            profile_id="dual",
            label="Dual",
            requires_deep_initiation=False,
        ),
        execution_backend=ExecutionBackend(
            "fixture", "Fixture", "gpt-5.6-sol", public=False,
        ),
        rounds=primary,
        evidence_hash="frozen-evidence-hash",
        generated_at="2026-07-28T00:00:00+00:00",
        completed_at="2026-07-28T00:01:00+00:00",
        deterministic_validation_status="passed",
        variants=[
            AnalogResearchVariant(
                "deepseek_led",
                "DeepSeek primary",
                "completed",
                "deepseek",
                "deepseek-v4-pro",
                "openai",
                "gpt-5.6-sol",
                rounds=primary,
            ),
            AnalogResearchVariant(
                "sol_led_challenger",
                "Sol challenger",
                "completed",
                "openai",
                "gpt-5.6-sol",
                "deepseek",
                "deepseek-v4-pro",
                rounds=challenger,
            ),
        ],
    )


def _context() -> dict:
    return {
        "research_profile": "adaptive_ic",
        "evidence_catalog": [
            {
                "evidence_id": "e1",
                "source_tier": 1,
                "summary": "SEC filing cash-flow fact.",
            },
            {
                "evidence_id": "e2",
                "source_tier": 2,
                "summary": "Issuer commitment disclosure.",
            },
        ],
    }


def test_paired_benchmark_covers_every_stage_and_revalidates_sol_additions() -> None:
    run = build_dual_model_research_run(_hybrid_run(), _context())

    assert run is not None
    assert run.status == "adjudicated"
    assert len(run.stages) == 5
    assert all(stage.deepseek_artifact for stage in run.stages)
    assert all(stage.sol_artifact for stage in run.stages)
    assert run.adjudication is not None
    assert run.adjudication.status == "adjudicated"
    assert run.adjudication.prohibited_overrides
    accepted_text = " ".join(
        item.summary for item in run.accepted_secondary_deltas
    )
    assert "Supplier commitments remain a hidden risk" in accepted_text
    assert "invented unsupported fact" not in accepted_text


def test_improvement_candidates_are_review_gated_and_cannot_self_deploy() -> None:
    run = build_dual_model_research_run(_hybrid_run(), _context())

    assert run is not None
    assert run.improvement_candidates
    assert all(
        item.activation_status == "review_required"
        and item.runtime_mutation_allowed is False
        and len(item.required_benchmark_tickers) >= 10
        for item in run.improvement_candidates
    )


def test_prompt_payload_contains_adjudication_not_hidden_or_protected_state() -> None:
    run = build_dual_model_research_run(_hybrid_run(), _context())
    payload = dual_model_prompt_payload(run)
    encoded = json.dumps(payload)

    assert "accepted_secondary_deltas" in payload
    assert "probability_override" not in encoded
    assert "promotion_override" not in encoded
    assert "api_key" not in encoded


def test_dual_model_round_trip_and_storage_never_add_credentials(tmp_path) -> None:
    run = build_dual_model_research_run(_hybrid_run(), _context())
    assert run is not None
    restored = dual_model_run_from_payload(asdict(run))
    assert restored.evidence_hash == run.evidence_hash
    assert restored.adjudication.status == "adjudicated"

    store = ResearchStore(tmp_path / "dual.db")
    store.save_dual_model_research_run(run)
    saved = store.latest_dual_model_research_run("AAPL")
    assert saved is not None
    assert saved["final_label"] == "DeepSeek primary, GPT-5.6 Sol adjudicated"
    assert "api_key" not in json.dumps(saved)
    assert store.list_research_improvement_candidates()


def test_frozen_fixture_clearly_states_no_live_model_executed() -> None:
    hybrid = _hybrid_run()
    hybrid.cache_status = "frozen_fixture"
    run = build_dual_model_research_run(hybrid, _context())

    assert run is not None
    assert run.status == "frozen_fixture"
    assert run.provenance_class == "sanitized_fixture"
    assert "No live model executed" in run.final_label
    assert not run.stages


def test_validated_provider_run_uses_precise_frozen_loading_message() -> None:
    hybrid = _hybrid_run()
    hybrid.cache_status = "frozen_fixture"
    hybrid.final_artifact = hybrid.variants[0].rounds[-1].artifact
    hybrid.generation_provenance = {
        "status": "validated_live_model_run",
        "primary": "DeepSeek V4 Pro research + GPT-5.6 Sol audit",
        "challenger": "GPT-5.6 Sol independent research",
    }
    run = build_dual_model_research_run(hybrid, _context())

    assert run is not None
    assert run.provenance_class == "validated_live_model_run"
    assert run.final_label == (
        "Frozen from a validated DeepSeek V4 Pro + GPT-5.6 Sol run on "
        "2026-07-28. No live model call was made while loading this demo."
    )
    assert not run.data_gaps


def test_zero_round_live_run_never_claims_adjudication_or_model_scores() -> None:
    hybrid = _hybrid_run()
    hybrid.status = "not_started_missing_credentials"
    hybrid.rounds = []
    hybrid.variants = []
    hybrid.deterministic_validation_status = "not_run"
    hybrid.data_gaps = [
        "Dual choreography requires both configured providers; missing: "
        "DeepSeek V4 Pro, GPT-5.6 Sol."
    ]

    run = build_dual_model_research_run(hybrid, _context())

    assert run is not None
    assert run.status == "not_started_missing_credentials"
    assert run.final_label == "Paired research did not start"
    assert run.stages == []
    assert run.benchmark is None
    assert run.adjudication is None
    assert run.deepseek_execution_status == "not_started"
    assert run.sol_execution_status == "not_started"
    assert run.adjudication_status == "not_run"


def test_legacy_five_empty_stage_payload_is_normalized_on_load() -> None:
    run = build_dual_model_research_run(_hybrid_run(), _context())
    payload = asdict(run)
    payload.update({
        "status": "completed",
        "final_label": "DeepSeek primary, GPT-5.6 Sol adjudicated",
        "provenance_class": "live_run",
        "provenance_message": "Providers not configured; missing credentials.",
        "deepseek_execution_status": "completed",
        "sol_execution_status": "completed",
        "adjudication_status": "adjudicated",
    })
    for stage in payload["stages"]:
        stage["deepseek_artifact"]["status"] = "unavailable"
        stage["sol_artifact"]["status"] = "unavailable"
        stage["comparison"] = None
        stage["status"] = "unavailable"

    restored = dual_model_run_from_payload(payload)

    assert restored.status == "not_started_missing_credentials"
    assert restored.final_label == "Paired research did not start"
    assert restored.stages == []
    assert restored.benchmark is None
    assert restored.adjudication is None
    assert restored.accepted_secondary_deltas == []
