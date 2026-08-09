from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from .models import (
    AcceptedSecondaryDelta,
    AdjudicationDecision,
    BenchmarkGapDecomposition,
    DualModelResearchRun,
    DualModelStage,
    EvidenceExclusionRecord,
    EvidencePackAudit,
    HybridResearchRun,
    InputSufficiencyDimension,
    InputSufficiencyScore,
    ModelResearchArtifact,
    ResearchGapDiagnosis,
    ResearchImprovementCandidate,
    ResearchOutputComparison,
    ResearchQualityBenchmark,
    ResearchRound,
    ResearchUsageSummary,
)


DUAL_MODEL_BENCHMARK_VERSION = "dual-model-benchmark-v1"

_STAGES = (
    ("research_planning", "Research Planning"),
    ("evidence_investigation", "Evidence Investigation"),
    ("causal_financial_analysis", "Causal & Financial Analysis"),
    ("adversarial_closure", "Adversarial Audit & Closure"),
    ("ic_adjudication", "IC Synthesis & Adjudication"),
)

_QUALITY_DIMENSIONS = (
    "evidence_coverage",
    "primary_source_use",
    "historical_management_context",
    "metric_period_accuracy",
    "causal_bridge",
    "peer_sector_geography_macro",
    "valuation_credit",
    "counter_thesis_hidden_risk",
    "monitoring_usefulness",
    "clarity_guardrails",
)

_PROHIBITED_OVERRIDES = (
    "source facts and normalized metrics",
    "valuation calculations and scenario arithmetic",
    "calibrated probabilities",
    "idea score or promotion stage",
    "source tier and licensing policy",
)

_BENCHMARK_TICKERS = (
    "AAPL",
    "BABA",
    "NVDA",
    "ORCL",
    "TSLA",
    "GS",
    "JPM",
    "PLD",
    "XOM",
    "NVO",
    "TSM",
    "BHP",
)


def build_dual_model_research_run(
    hybrid: HybridResearchRun | None,
    context: dict[str, Any] | None,
) -> DualModelResearchRun | None:
    if hybrid is None:
        return None
    context = context or {}
    profile = str(
        context.get("research_profile")
        or getattr(hybrid.profile, "profile_id", "")
        or "adaptive_ic"
    )
    if hybrid.cache_status == "frozen_fixture":
        provenance_class, provenance_message = _frozen_provenance(hybrid)
        return DualModelResearchRun(
            run_id=hybrid.run_id,
            ticker=hybrid.ticker,
            status="frozen_fixture",
            research_profile=profile,
            evidence_hash=hybrid.evidence_hash,
            generated_at=hybrid.generated_at,
            completed_at=hybrid.completed_at,
            final_label=provenance_message,
            cache_status="frozen_fixture",
            deterministic_validation_status=(
                hybrid.deterministic_validation_status
            ),
            execution_backend=hybrid.execution_backend.backend_id,
            provenance_class=provenance_class,
            provenance_message=provenance_message,
            evidence_pack_audit=hybrid.evidence_pack_audit,
            input_sufficiency=hybrid.input_sufficiency,
            usage_summary=hybrid.usage_summary,
            gap_decomposition=hybrid.gap_decomposition,
            data_gaps=(
                [
                    "Real live benchmarking is still needed to establish "
                    "whether DeepSeek research quality approaches GPT-5.6 Sol "
                    "across actual companies and sectors."
                ]
                if provenance_class == "sanitized_fixture"
                else []
            ),
        )
    if not hybrid.rounds and not any(
        variant.rounds for variant in hybrid.variants
    ):
        reason = (
            hybrid.data_gaps[0]
            if hybrid.data_gaps else
            "No DeepSeek or GPT-5.6 Sol round executed."
        )
        missing_credentials = (
            hybrid.status == "not_started_missing_credentials"
            or "missing:" in reason.casefold()
            or "not configured" in reason.casefold()
        )
        status = (
            "not_started_missing_credentials"
            if missing_credentials else "not_started"
        )
        return DualModelResearchRun(
            run_id=hybrid.run_id,
            ticker=hybrid.ticker,
            status=status,
            research_profile=profile,
            evidence_hash=hybrid.evidence_hash,
            generated_at=hybrid.generated_at,
            completed_at=hybrid.completed_at,
            final_label="Paired research did not start",
            cache_status=hybrid.cache_status,
            deterministic_validation_status=(
                hybrid.deterministic_validation_status
            ),
            execution_backend=hybrid.execution_backend.backend_id,
            provenance_class=status,
            provenance_message=reason,
            data_gaps=list(hybrid.data_gaps),
            evidence_pack_audit=hybrid.evidence_pack_audit,
            input_sufficiency=hybrid.input_sufficiency,
            usage_summary=hybrid.usage_summary,
            gap_decomposition=hybrid.gap_decomposition,
            execution_failure_reason=reason,
        )

    catalog = {
        str(item.get("evidence_id")): item
        for item in context.get("evidence_catalog", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    paired_rounds = _paired_rounds(hybrid)
    stages: list[DualModelStage] = []
    gaps: list[ResearchGapDiagnosis] = []
    accepted: list[AcceptedSecondaryDelta] = []
    for index, (stage_id, label) in enumerate(_STAGES, start=1):
        deepseek_round, sol_round = paired_rounds.get(index, (None, None))
        deepseek = _artifact_from_round(stage_id, deepseek_round)
        sol = _artifact_from_round(stage_id, sol_round)
        comparable = bool(
            deepseek and sol
            and deepseek.status == "completed"
            and sol.status == "completed"
        )
        if comparable:
            comparison, stage_gaps, deltas = _compare_stage(
                stage_id,
                deepseek,
                sol,
                catalog,
            )
        else:
            comparison, stage_gaps, deltas = None, [], []
        gaps.extend(stage_gaps)
        accepted.extend(deltas)
        status = (
            "completed"
            if comparable
            else "partial"
            if deepseek or sol
            else "unavailable"
        )
        stages.append(DualModelStage(
            stage_id=stage_id,
            label=label,
            status=status,
            deepseek_artifact=deepseek,
            sol_artifact=sol,
            comparison=comparison,
            accepted_secondary_deltas=deltas,
            data_gaps=[
                item.summary for item in stage_gaps
                if item.severity in {"High", "Critical"}
            ][:4],
        ))

    comparable_stages = [
        item for item in stages if item.status == "completed"
    ]
    adjudication = (
        _adjudication(stages, accepted) if comparable_stages else None
    )
    benchmark = (
        _benchmark(stages, hybrid) if comparable_stages else None
    )
    if (
        benchmark is not None
        and
        hybrid.input_sufficiency is not None
        and not hybrid.input_sufficiency.benchmark_ready
    ):
        benchmark.status = "input_not_qualified"
        benchmark.critical_failures.append(
            "Model-quality conclusions are withheld because input "
            f"sufficiency is {hybrid.input_sufficiency.total_score}/100; "
            f"the required threshold is "
            f"{hybrid.input_sufficiency.threshold}/100 with no critical gap."
        )
    improvements = _improvement_candidates(gaps)
    deepseek_completed = sum(
        bool(item.deepseek_artifact)
        and item.deepseek_artifact.status == "completed"
        for item in stages
    )
    sol_completed = sum(
        bool(item.sol_artifact)
        and item.sol_artifact.status == "completed"
        for item in stages
    )
    attempted = sum(
        bool(item.deepseek_artifact or item.sol_artifact) for item in stages
    )
    completed = len(comparable_stages)
    fully_adjudicated = bool(
        hybrid.status == "completed"
        and hybrid.deterministic_validation_status == "passed"
        and completed == len(_STAGES)
        and adjudication is not None
        and adjudication.status in {"adjudicated", "supported_with_gaps"}
    )
    status = (
        "adjudicated" if fully_adjudicated
        else "partial" if attempted
        else "not_started"
    )
    failure_reason = (
        hybrid.data_gaps[0]
        if hybrid.data_gaps and not fully_adjudicated else ""
    )
    run = DualModelResearchRun(
        run_id=hybrid.run_id,
        ticker=hybrid.ticker,
        status=status,
        research_profile=profile,
        evidence_hash=hybrid.evidence_hash,
        generated_at=hybrid.generated_at,
        completed_at=hybrid.completed_at,
        final_label=(
            "DeepSeek primary, GPT-5.6 Sol adjudicated"
            if fully_adjudicated else "Paired research incomplete"
        ),
        stages=stages,
        gap_diagnoses=gaps,
        accepted_secondary_deltas=accepted,
        adjudication=adjudication,
        benchmark=benchmark,
        improvement_candidates=improvements,
        cache_status=hybrid.cache_status,
        deterministic_validation_status=(
            hybrid.deterministic_validation_status
        ),
        execution_backend=hybrid.execution_backend.backend_id,
        provenance_class="live_run",
        provenance_message=(
            "DeepSeek V4 Pro and GPT-5.6 Sol completed all paired stages and "
            "the final evidence-bound adjudication."
            if fully_adjudicated else
            failure_reason or "Paired model execution was incomplete."
        ),
        evidence_pack_audit=hybrid.evidence_pack_audit,
        input_sufficiency=hybrid.input_sufficiency,
        usage_summary=hybrid.usage_summary,
        gap_decomposition=hybrid.gap_decomposition,
        data_gaps=list(dict.fromkeys([
            *hybrid.data_gaps,
            *(
                []
                if fully_adjudicated
                else [
                    "Secondary audit unavailable; deterministic and DeepSeek "
                    "research remain visible."
                ]
            ),
        ])),
        deepseek_execution_status=(
            "completed" if deepseek_completed == len(_STAGES)
            else "partial" if deepseek_completed else "not_started"
        ),
        sol_execution_status=(
            "completed" if sol_completed == len(_STAGES)
            else "partial" if sol_completed else "not_started"
        ),
        adjudication_status=(
            adjudication.status if adjudication else "not_run"
        ),
        attempted_stage_count=attempted,
        completed_stage_count=completed,
        deepseek_completed_stage_count=deepseek_completed,
        sol_completed_stage_count=sol_completed,
        execution_failure_reason=failure_reason,
    )
    return run


def _frozen_provenance(
    hybrid: HybridResearchRun,
) -> tuple[str, str]:
    provenance = hybrid.generation_provenance or {}
    status = str(provenance.get("status") or "").casefold()
    explicit_live = status in {
        "validated_live_model_run",
        "validated_provider_run",
        "live_model_run",
    }
    legacy_live = bool(
        "deepseek" in str(provenance.get("primary") or "").casefold()
        and "gpt-5.6" in str(
            provenance.get("challenger")
            or provenance.get("secondary_audit")
            or ""
        ).casefold()
        and hybrid.variants
        and hybrid.execution_backend.backend_id != "frozen_fixture"
    )
    passed = bool(
        hybrid.status == "completed"
        and hybrid.deterministic_validation_status == "passed"
        and hybrid.final_artifact is not None
    )
    if passed and (explicit_live or legacy_live):
        generated_date = str(hybrid.generated_at or "an unknown date")[:10]
        return (
            "validated_live_model_run",
            "Frozen from a validated DeepSeek V4 Pro + GPT-5.6 Sol run on "
            f"{generated_date}. No live model call was made while loading "
            "this demo.",
        )
    return (
        "sanitized_fixture",
        "No live model executed. This is a hand-authored or sanitized "
        "deterministic fixture.",
    )


def dual_model_prompt_payload(
    run: DualModelResearchRun | None,
) -> dict[str, Any]:
    if run is None:
        return {}
    return {
        "status": run.status,
        "label": run.final_label,
        "evidence_hash": run.evidence_hash,
        "adjudication": (
            asdict(run.adjudication) if run.adjudication else None
        ),
        "accepted_secondary_deltas": [
            asdict(item) for item in run.accepted_secondary_deltas[:12]
        ],
        "unresolved_gaps": [
            asdict(item) for item in run.gap_diagnoses
            if item.status != "resolved"
        ][:12],
        "stage_comparisons": [
            asdict(item.comparison)
            for item in run.stages
            if item.comparison
        ],
        "rules": [
            "Use accepted secondary deltas only when their evidence IDs remain "
            "present in the deterministic evidence pack.",
            "Do not let model adjudication change source facts, valuation "
            "arithmetic, probability calibration, scores, or promotion.",
            "Mark unresolved model disagreements explicitly.",
        ],
    }


def dual_model_run_from_payload(
    payload: dict[str, Any],
) -> DualModelResearchRun:
    stages = []
    for raw in payload.get("stages", []):
        deepseek = raw.get("deepseek_artifact")
        sol = raw.get("sol_artifact")
        comparison = raw.get("comparison")
        stages.append(DualModelStage(
            stage_id=raw["stage_id"],
            label=raw["label"],
            status=raw["status"],
            deepseek_artifact=(
                ModelResearchArtifact(**deepseek) if deepseek else None
            ),
            sol_artifact=ModelResearchArtifact(**sol) if sol else None,
            comparison=(
                ResearchOutputComparison(**comparison)
                if comparison else None
            ),
            accepted_secondary_deltas=[
                AcceptedSecondaryDelta(**item)
                for item in raw.get("accepted_secondary_deltas", [])
            ],
            data_gaps=list(raw.get("data_gaps", [])),
        ))
    adjudication = payload.get("adjudication")
    benchmark = payload.get("benchmark")
    evidence_pack = payload.get("evidence_pack_audit")
    sufficiency = payload.get("input_sufficiency")
    usage = payload.get("usage_summary")
    decomposition = payload.get("gap_decomposition")
    run = DualModelResearchRun(
        **{
            key: value for key, value in payload.items()
            if key not in {
                "stages",
                "gap_diagnoses",
                "accepted_secondary_deltas",
                "adjudication",
                "benchmark",
                "improvement_candidates",
                "evidence_pack_audit",
                "input_sufficiency",
                "usage_summary",
                "gap_decomposition",
            }
        },
        stages=stages,
        gap_diagnoses=[
            ResearchGapDiagnosis(**item)
            for item in payload.get("gap_diagnoses", [])
        ],
        accepted_secondary_deltas=[
            AcceptedSecondaryDelta(**item)
            for item in payload.get("accepted_secondary_deltas", [])
        ],
        adjudication=(
            AdjudicationDecision(**adjudication)
            if adjudication else None
        ),
        benchmark=(
            ResearchQualityBenchmark(**benchmark) if benchmark else None
        ),
        improvement_candidates=[
            ResearchImprovementCandidate(**item)
            for item in payload.get("improvement_candidates", [])
        ],
        evidence_pack_audit=_evidence_pack_from_payload(evidence_pack),
        input_sufficiency=_sufficiency_from_payload(sufficiency),
        usage_summary=(
            ResearchUsageSummary(**usage)
            if isinstance(usage, dict) else None
        ),
        gap_decomposition=(
            BenchmarkGapDecomposition(**decomposition)
            if isinstance(decomposition, dict) else None
        ),
    )
    return _normalize_legacy_execution_state(run)


def _normalize_legacy_execution_state(
    run: DualModelResearchRun,
) -> DualModelResearchRun:
    """Correct persisted rows that claimed execution without model artifacts."""
    executed_artifacts = [
        artifact
        for stage in run.stages
        for artifact in (stage.deepseek_artifact, stage.sol_artifact)
        if artifact is not None
        and str(artifact.status).casefold() not in {
            "unavailable", "not_started", "queued"
        }
    ]
    if executed_artifacts or run.status in {
        "frozen_fixture",
        "sanitized_fixture",
    }:
        return run

    combined_reason = " ".join([
        run.execution_failure_reason,
        run.provenance_message,
        *run.data_gaps,
    ]).strip()
    missing = any(
        marker in combined_reason.casefold()
        for marker in ("missing", "not configured", "credential", "api key")
    )
    run.status = (
        "not_started_missing_credentials" if missing else "not_started"
    )
    run.final_label = "Paired research did not start"
    run.stages = []
    run.adjudication = None
    run.benchmark = None
    run.accepted_secondary_deltas = []
    run.deepseek_execution_status = "not_started"
    run.sol_execution_status = "not_started"
    run.adjudication_status = "not_run"
    run.attempted_stage_count = 0
    run.completed_stage_count = 0
    run.deepseek_completed_stage_count = 0
    run.sol_completed_stage_count = 0
    run.execution_failure_reason = (
        combined_reason or "No model round executed in this persisted run."
    )
    run.provenance_class = run.status
    run.provenance_message = run.execution_failure_reason
    return run


def _evidence_pack_from_payload(
    payload: Any,
) -> EvidencePackAudit | None:
    if not isinstance(payload, dict):
        return None
    return EvidencePackAudit(
        **{
            key: value for key, value in payload.items()
            if key != "exclusions"
        },
        exclusions=[
            EvidenceExclusionRecord(**item)
            for item in payload.get("exclusions", [])
            if isinstance(item, dict)
        ],
    )


def _sufficiency_from_payload(
    payload: Any,
) -> InputSufficiencyScore | None:
    if not isinstance(payload, dict):
        return None
    return InputSufficiencyScore(
        **{
            key: value for key, value in payload.items()
            if key != "dimensions"
        },
        dimensions=[
            InputSufficiencyDimension(**item)
            for item in payload.get("dimensions", [])
            if isinstance(item, dict)
        ],
    )


def _paired_rounds(
    hybrid: HybridResearchRun,
) -> dict[int, tuple[ResearchRound | None, ResearchRound | None]]:
    rows: dict[int, list[ResearchRound]] = {}
    variants = hybrid.variants or []
    if not variants:
        variants = []
    for variant in variants:
        for item in variant.rounds:
            rows.setdefault(item.round_number, []).append(item)
    if not rows:
        for item in hybrid.rounds:
            rows.setdefault(item.round_number, []).append(item)
    result = {}
    for round_number, items in rows.items():
        deepseek = next(
            (item for item in items if _is_deepseek(item)), None
        )
        sol = next((item for item in items if _is_sol(item)), None)
        result[round_number] = (deepseek, sol)
    return result


def _artifact_from_round(
    stage_id: str,
    item: ResearchRound | None,
) -> ModelResearchArtifact | None:
    if item is None:
        return None
    artifact = item.artifact
    return ModelResearchArtifact(
        artifact_id=(
            artifact.artifact_id
            if artifact else _stable_id(stage_id, item.provider, item.model)
        ),
        stage_id=stage_id,
        role=item.role,
        provider=item.provider,
        model=item.model,
        status=item.status,
        summary=artifact.summary if artifact else item.message,
        evidence_ids=list(artifact.evidence_ids if artifact else []),
        citation_ids=list(artifact.citation_ids if artifact else []),
        source_request_ids=list(
            artifact.source_request_ids if artifact else []
        ),
        validation_gaps=list(
            artifact.validation_gaps if artifact else [item.message]
        ),
        latency_ms=item.latency_ms,
        token_usage=dict(item.token_usage),
        payload=dict(artifact.payload if artifact else {}),
    )


def _compare_stage(
    stage_id: str,
    deepseek: ModelResearchArtifact | None,
    sol: ModelResearchArtifact | None,
    catalog: dict[str, dict[str, Any]],
) -> tuple[
    ResearchOutputComparison,
    list[ResearchGapDiagnosis],
    list[AcceptedSecondaryDelta],
]:
    deep_scores = _quality_scores(stage_id, deepseek, catalog)
    sol_scores = _quality_scores(stage_id, sol, catalog)
    deep_total = _average(deep_scores.values())
    sol_total = _average(sol_scores.values())
    stronger = (
        "GPT-5.6 Sol" if sol_total > deep_total + 2
        else "DeepSeek V4 Pro" if deep_total > sol_total + 2
        else "Tie"
    )
    deep_points = _research_points(deepseek)
    sol_points = _research_points(sol)
    agreements = sorted(deep_points & sol_points)[:8]
    sol_additions = sorted(sol_points - deep_points)[:10]
    deep_additions = sorted(deep_points - sol_points)[:10]
    disagreements = _disagreements(deepseek, sol)
    gaps = _diagnose_gaps(
        stage_id,
        deepseek,
        sol,
        deep_scores,
        sol_scores,
        stronger,
        sol_additions,
    )
    deltas = _accepted_sol_deltas(stage_id, deepseek, sol, catalog)
    comparison = ResearchOutputComparison(
        stage_id=stage_id,
        status=(
            "completed" if deepseek and sol else "partial"
        ),
        agreement=(
            "Broad agreement"
            if agreements and not disagreements
            else "Material differences surfaced"
            if disagreements or sol_additions or deep_additions
            else "Comparison unavailable"
        ),
        deepseek_score=deep_total,
        sol_score=sol_total,
        stronger_model=stronger,
        dimension_scores={
            key: {
                "deepseek": deep_scores[key],
                "sol": sol_scores[key],
            }
            for key in _QUALITY_DIMENSIONS
        },
        agreements=agreements,
        disagreements=disagreements,
        sol_additions=sol_additions,
        deepseek_additions=deep_additions,
        gap_ids=[item.gap_id for item in gaps],
    )
    return comparison, gaps, deltas


def _quality_scores(
    stage_id: str,
    artifact: ModelResearchArtifact | None,
    catalog: dict[str, dict[str, Any]],
) -> dict[str, int]:
    if artifact is None or artifact.status != "completed":
        return {key: 0 for key in _QUALITY_DIMENSIONS}
    text = json.dumps(artifact.payload, ensure_ascii=True).casefold()
    evidence = [
        catalog[item] for item in artifact.evidence_ids if item in catalog
    ]
    primary = [
        item for item in evidence
        if isinstance(item.get("source_tier"), int)
        and int(item["source_tier"]) <= 2
    ]
    dimensions = {
        "evidence_coverage": min(
            100, 25 + len(evidence) * 10
        ) if evidence else 30 if stage_id == "research_planning" else 10,
        "primary_source_use": (
            round(len(primary) / len(evidence) * 100)
            if evidence else 25
        ),
        "historical_management_context": _keyword_score(
            text, ("histor", "management", "transcript", "capital allocation")
        ),
        "metric_period_accuracy": (
            max(20, 100 - len(artifact.validation_gaps) * 15)
            if evidence else 30
        ),
        "causal_bridge": _keyword_score(
            text, ("driver", "operating", "earnings", "cash", "valuation", "catalyst")
        ),
        "peer_sector_geography_macro": _keyword_score(
            text, ("peer", "sector", "industry", "geograph", "macro")
        ),
        "valuation_credit": _keyword_score(
            text, ("valuation", "multiple", "cash flow", "credit", "leverage", "liquidity")
        ),
        "counter_thesis_hidden_risk": _keyword_score(
            text, ("counter", "contradiction", "hidden_risk", "risk", "adverse")
        ),
        "monitoring_usefulness": _keyword_score(
            text, ("monitor", "confirm", "reject", "falsif", "deadline")
        ),
        "clarity_guardrails": max(
            20,
            95 - len(artifact.validation_gaps) * 20
            - (15 if len(artifact.summary) > 1_200 else 0),
        ),
    }
    return dimensions


def _keyword_score(text: str, tokens: Iterable[str]) -> int:
    tokens = tuple(tokens)
    hits = sum(token in text for token in tokens)
    return min(100, 20 + round(hits / max(1, len(tokens)) * 80))


def _research_points(
    artifact: ModelResearchArtifact | None,
) -> set[str]:
    if artifact is None:
        return set()
    result: set[str] = set()
    for key in (
        "claims",
        "findings",
        "hidden_risks",
        "lessons",
        "limitations",
        "monitors",
        "constructive_hypotheses",
        "adverse_hypotheses",
    ):
        for item in artifact.payload.get(key, []) or []:
            if isinstance(item, dict):
                value = str(
                    item.get("claim")
                    or item.get("summary")
                    or item.get("text")
                    or ""
                )
            else:
                value = str(item)
            normalized = " ".join(value.casefold().split())
            if len(normalized) >= 12:
                result.add(normalized[:260])
    return result


def _disagreements(
    deepseek: ModelResearchArtifact | None,
    sol: ModelResearchArtifact | None,
) -> list[str]:
    if deepseek is None or sol is None:
        return ["One model did not return a comparable artifact."]
    result = []
    deep_risks = set(_text_items(deepseek.payload.get("hidden_risks")))
    sol_risks = set(_text_items(sol.payload.get("hidden_risks")))
    if deep_risks != sol_risks and (deep_risks or sol_risks):
        result.append("The models identified different hidden-risk sets.")
    deep_limits = set(_text_items(deepseek.payload.get("limitations")))
    sol_limits = set(_text_items(sol.payload.get("limitations")))
    if deep_limits != sol_limits and (deep_limits or sol_limits):
        result.append("The models framed material limitations differently.")
    if set(deepseek.evidence_ids) != set(sol.evidence_ids):
        result.append("The models relied on different subsets of the evidence pack.")
    return result


def _diagnose_gaps(
    stage_id: str,
    deepseek: ModelResearchArtifact | None,
    sol: ModelResearchArtifact | None,
    deep_scores: dict[str, int],
    sol_scores: dict[str, int],
    stronger: str,
    sol_additions: list[str],
) -> list[ResearchGapDiagnosis]:
    result = []
    if deepseek is None or sol is None:
        return [ResearchGapDiagnosis(
            gap_id=_stable_id(stage_id, "provider"),
            stage_id=stage_id,
            category="model reasoning gap",
            severity="High",
            summary="One provider did not return a comparable stage artifact.",
            stronger_model=stronger,
            recommended_fix=(
                "Retry the failed provider with the same evidence hash; do not "
                "substitute another model silently."
            ),
        )]
    for dimension in _QUALITY_DIMENSIONS:
        delta = sol_scores[dimension] - deep_scores[dimension]
        if abs(delta) < 20:
            continue
        category = _dimension_gap_category(dimension)
        winner = "GPT-5.6 Sol" if delta > 0 else "DeepSeek V4 Pro"
        result.append(ResearchGapDiagnosis(
            gap_id=_stable_id(stage_id, dimension, winner),
            stage_id=stage_id,
            category=category,
            severity="High" if abs(delta) >= 35 else "Medium",
            summary=(
                f"{winner} produced materially stronger "
                f"{dimension.replace('_', ' ')} coverage."
            ),
            stronger_model=winner,
            recommended_fix=_recommended_fix(category),
        ))
    if sol_additions:
        result.append(ResearchGapDiagnosis(
            gap_id=_stable_id(stage_id, "sol-additions"),
            stage_id=stage_id,
            category="model reasoning gap",
            severity="Medium",
            summary=(
                f"Sol surfaced {len(sol_additions)} distinct research point(s) "
                "that were absent from the DeepSeek artifact."
            ),
            stronger_model="GPT-5.6 Sol",
            recommended_fix=(
                "Convert recurring supported additions into an eval case and "
                "strengthen the stage prompt only after cross-ticker testing."
            ),
        ))
    return result


def _accepted_sol_deltas(
    stage_id: str,
    deepseek: ModelResearchArtifact | None,
    sol: ModelResearchArtifact | None,
    catalog: dict[str, dict[str, Any]],
) -> list[AcceptedSecondaryDelta]:
    if sol is None or sol.status != "completed":
        return []
    deep_points = _research_points(deepseek)
    result = []
    for key in ("claims", "findings", "hidden_risks", "lessons", "monitors"):
        for index, item in enumerate(sol.payload.get(key, []) or []):
            if not isinstance(item, dict):
                continue
            summary = str(
                item.get("claim")
                or item.get("summary")
                or item.get("text")
                or ""
            ).strip()
            normalized = " ".join(summary.casefold().split())[:260]
            evidence_ids = [
                str(value) for value in item.get("evidence_ids", []) or []
                if str(value) in catalog
            ]
            if (
                len(normalized) < 12
                or normalized in deep_points
                or not evidence_ids
            ):
                continue
            result.append(AcceptedSecondaryDelta(
                delta_id=_stable_id(stage_id, key, index, summary),
                stage_id=stage_id,
                summary=summary[:700],
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                validation_status="validated_evidence_ids",
                adoption_status="adopted_for_synthesis",
                reason=(
                    "The addition references only existing deterministic "
                    "evidence IDs and does not alter protected decision fields."
                ),
            ))
    return result[:10]


def _adjudication(
    stages: list[DualModelStage],
    deltas: list[AcceptedSecondaryDelta],
) -> AdjudicationDecision:
    final = stages[-1] if stages else None
    sol = final.sol_artifact if final else None
    raw = (
        sol.payload.get("audit_decision")
        if sol and isinstance(sol.payload, dict) else None
    )
    raw = raw if isinstance(raw, dict) else {}
    raw_status = str(raw.get("status") or "").casefold()
    passed = raw_status in {"passed", "pass", "accepted"}
    unresolved = _text_items(raw.get("unresolved_findings"))
    return AdjudicationDecision(
        status=(
            "adjudicated" if passed and not unresolved
            else "supported_with_gaps" if passed
            else "secondary_audit_unavailable"
        ),
        selected_interpretation=(
            "DeepSeek primary narrative with deterministically validated Sol additions"
            if passed else "DeepSeek and deterministic research retained"
        ),
        rationale=(
            "Sol may resolve narrative disagreements only among interpretations "
            "supported by the shared evidence catalog."
        ),
        adjudicator_provider=sol.provider if sol else "",
        adjudicator_model=sol.model if sol else "",
        accepted_delta_ids=[item.delta_id for item in deltas],
        unresolved_disagreements=unresolved,
        prohibited_overrides=list(_PROHIBITED_OVERRIDES),
    )


def _benchmark(
    stages: list[DualModelStage],
    hybrid: HybridResearchRun,
) -> ResearchQualityBenchmark:
    comparisons = [
        item.comparison for item in stages if item.comparison
    ]
    deepseek = _average(item.deepseek_score for item in comparisons)
    sol = _average(item.sol_score for item in comparisons)
    critical = [
        gap for gap in hybrid.data_gaps
        if any(token in gap.casefold() for token in (
            "suppressed", "failed", "unavailable", "rejected"
        ))
    ]
    return ResearchQualityBenchmark(
        status="passed" if not critical and comparisons else "partial",
        deepseek_score=deepseek,
        sol_score=sol,
        combined_score=round((deepseek + sol) / 2),
        stage_winners={
            item.stage_id: item.stronger_model for item in comparisons
        },
        critical_failures=critical[:8],
        benchmark_dimensions={
            item.stage_id: {
                "deepseek": item.deepseek_score,
                "sol": item.sol_score,
            }
            for item in comparisons
        },
    )


def _improvement_candidates(
    gaps: list[ResearchGapDiagnosis],
) -> list[ResearchImprovementCandidate]:
    by_category: dict[str, list[ResearchGapDiagnosis]] = {}
    for gap in gaps:
        by_category.setdefault(gap.category, []).append(gap)
    result = []
    for category, rows in by_category.items():
        result.append(ResearchImprovementCandidate(
            candidate_id=_stable_id("improvement", category),
            category=category,
            title=f"Evaluate a systematic {category} treatment",
            proposed_change=_recommended_fix(category),
            supporting_gap_ids=[item.gap_id for item in rows],
            required_benchmark_tickers=list(_BENCHMARK_TICKERS),
            activation_status="review_required",
            runtime_mutation_allowed=False,
        ))
    return result


def _dimension_gap_category(dimension: str) -> str:
    return {
        "evidence_coverage": "evidence-input gap",
        "primary_source_use": "registered-source retrieval gap",
        "historical_management_context": "prompt or research-question gap",
        "metric_period_accuracy": "metric ontology or period-normalization gap",
        "causal_bridge": "model reasoning gap",
        "peer_sector_geography_macro": "industry/ADR playbook gap",
        "valuation_credit": "prompt or research-question gap",
        "counter_thesis_hidden_risk": "model reasoning gap",
        "monitoring_usefulness": "prompt or research-question gap",
        "clarity_guardrails": "presentation-compression gap",
    }[dimension]


def _recommended_fix(category: str) -> str:
    return {
        "evidence-input gap": (
            "Expand the curated evidence pack or record the missing source as "
            "genuinely unavailable."
        ),
        "registered-source retrieval gap": (
            "Add or repair a registered adapter and verify citation, period, "
            "licensing, and no-lookahead behavior."
        ),
        "metric ontology or period-normalization gap": (
            "Add a canonical alias or derivation rule and test units, fiscal "
            "scope, currency, and period alignment."
        ),
        "industry/ADR playbook gap": (
            "Extend the driver and KPI playbook, then test it across multiple "
            "issuers rather than one ticker."
        ),
        "prompt or research-question gap": (
            "Add the missing decision question to the stage prompt and require "
            "evidence-linked output in the schema."
        ),
        "model reasoning gap": (
            "Create a semantic regression case and compare prompt treatments "
            "across sectors before activation."
        ),
        "parser/schema/validation gap": (
            "Tighten structured output normalization and reject unsupported "
            "values without discarding valid evidence."
        ),
        "presentation-compression gap": (
            "Move audit detail to drilldowns and keep the IC summary focused "
            "on decision-relevant differences."
        ),
    }.get(category, "Review the gap across the benchmark universe.")


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict):
            text = str(
                item.get("summary")
                or item.get("text")
                or item.get("claim")
                or ""
            )
        else:
            text = str(item)
        if text.strip():
            result.append(text.strip())
    return result


def _average(values: Iterable[int]) -> int:
    rows = list(values)
    return round(sum(rows) / len(rows)) if rows else 0


def _is_deepseek(item: ResearchRound) -> bool:
    text = f"{item.provider} {item.model}".casefold()
    return "deepseek" in text


def _is_sol(item: ResearchRound) -> bool:
    text = f"{item.provider} {item.model}".casefold()
    return "gpt-5.6" in text or "codex_local" in text


def _stable_id(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
