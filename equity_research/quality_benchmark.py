from __future__ import annotations

import csv
import math
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from .models import (
    BenchmarkGapDecomposition,
    DualModelResearchRun,
    InputSufficiencyScore,
    ResearchUsageSummary,
)


QUALITY_BENCHMARK_VERSION = "quality-first-input-benchmark-v1"
DEFAULT_CASES_PATH = Path("data/quality_benchmark_cases.csv")


@dataclass(frozen=True)
class QualityBenchmarkCase:
    ticker: str
    cohort: str
    research_focus: str
    sector: str
    security_type: str
    geography: str
    language: str
    required_evidence: tuple[str, ...] = ()


@dataclass
class QualityBenchmarkAcceptance:
    status: str
    benchmark_ready_count: int
    required_ready_count: int
    total_development_cases: int
    exclusions_auditable: bool
    ordering_invariant: bool
    periods_aligned: bool
    contradictions_retained: bool
    no_lookahead_passed: bool
    blockers: list[str] = field(default_factory=list)


@dataclass
class CostLearningReport:
    status: str
    run_count: int
    median_total_tokens: int
    p90_total_tokens: int
    median_latency_ms: int
    p90_latency_ms: int
    median_provider_cost_usd: float | None = None
    p90_provider_cost_usd: float | None = None
    review_required_runs: int = 0
    notes: list[str] = field(default_factory=list)


def load_quality_benchmark_cases(
    path: Path | str = DEFAULT_CASES_PATH,
) -> list[QualityBenchmarkCase]:
    source = Path(path)
    if not source.exists():
        return []
    cases = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            cases.append(QualityBenchmarkCase(
                ticker=str(row.get("ticker") or "").upper(),
                cohort=str(row.get("cohort") or ""),
                research_focus=str(row.get("research_focus") or ""),
                sector=str(row.get("sector") or ""),
                security_type=str(row.get("security_type") or ""),
                geography=str(row.get("geography") or ""),
                language=str(row.get("language") or "English"),
                required_evidence=tuple(
                    item.strip()
                    for item in str(
                        row.get("required_evidence") or ""
                    ).split("|")
                    if item.strip()
                ),
            ))
    return cases


def benchmark_case_manifest(
    cases: Iterable[QualityBenchmarkCase],
) -> list[dict]:
    return [asdict(item) for item in cases]


def build_gap_decomposition(
    original: DualModelResearchRun,
    enriched: DualModelResearchRun,
) -> BenchmarkGapDecomposition:
    original_input = _input_score(original.input_sufficiency)
    enriched_input = _input_score(enriched.input_sufficiency)
    original_deepseek, original_sol = _model_scores(original)
    enriched_deepseek, enriched_sol = _model_scores(enriched)
    input_categories = sorted({
        _gap_category(item)
        for item in (
            original.input_sufficiency.data_gaps
            if original.input_sufficiency else []
        )
        if item
    })
    retrieval_categories = sorted({
        item.category
        for item in enriched.gap_diagnoses
        if item.category in {
            "evidence-input gap",
            "registered-source retrieval gap",
            "parser/schema/validation gap",
        }
    })
    reasoning_categories = sorted({
        item.category
        for item in enriched.gap_diagnoses
        if item.category in {
            "model reasoning gap",
            "prompt or research-question gap",
            "industry/ADR playbook gap",
            "metric ontology or period-normalization gap",
        }
    })
    presentation_categories = sorted({
        item.category
        for item in enriched.gap_diagnoses
        if item.category == "presentation-compression gap"
    })
    residual = None
    if enriched_deepseek is not None and enriched_sol is not None:
        residual = enriched_sol - enriched_deepseek
    status = (
        "benchmark_ready"
        if enriched.input_sufficiency
        and enriched.input_sufficiency.benchmark_ready
        else "input_remediation_required"
    )
    return BenchmarkGapDecomposition(
        ticker=enriched.ticker,
        status=status,
        original_input_score=original_input,
        enriched_input_score=enriched_input,
        original_deepseek_score=original_deepseek,
        enriched_deepseek_score=enriched_deepseek,
        original_sol_score=original_sol,
        enriched_sol_score=enriched_sol,
        input_uplift=enriched_input - original_input,
        residual_reasoning_gap=residual,
        input_gap_categories=input_categories,
        retrieval_gap_categories=retrieval_categories,
        reasoning_gap_categories=reasoning_categories,
        presentation_gap_categories=presentation_categories,
        accepted_fixes=[
            item.proposed_change
            for item in enriched.improvement_candidates
            if item.activation_status in {"accepted", "validated"}
        ],
        unresolved_gaps=[
            item.summary
            for item in enriched.gap_diagnoses
            if item.status != "resolved"
        ],
    )


def evaluate_acceptance_gate(
    runs: Iterable[DualModelResearchRun],
    *,
    required_ready_count: int = 5,
    no_lookahead_passed: bool = False,
) -> QualityBenchmarkAcceptance:
    development = [
        run for run in runs
        if run.ticker in {"AAPL", "NVDA", "BABA", "JPM", "XOM", "NVO"}
    ]
    ready = sum(
        bool(run.input_sufficiency and run.input_sufficiency.benchmark_ready)
        for run in development
    )
    exclusions_auditable = all(
        run.evidence_pack_audit is not None
        and run.evidence_pack_audit.searchable_count
        >= run.evidence_pack_audit.core_count
        for run in development
    )
    ordering_invariant = all(
        run.evidence_pack_audit is not None
        and run.evidence_pack_audit.order_invariant
        for run in development
    )
    periods_aligned = all(
        run.evidence_pack_audit is not None
        and not run.input_sufficiency.critical_gaps
        and not run.evidence_pack_audit.core_missing_period_ids
        and not run.evidence_pack_audit.core_missing_provenance_ids
        for run in development
        if run.input_sufficiency is not None
    )
    contradictions_retained = all(
        any(
            item.dimension_id == "contradictions" and item.score > 0
            for item in run.input_sufficiency.dimensions
        )
        for run in development
        if run.input_sufficiency is not None
    )
    blockers = []
    if ready < required_ready_count:
        blockers.append(
            f"Only {ready}/{len(development)} development cases are "
            f"Benchmark-ready; {required_ready_count} are required."
        )
    if not exclusions_auditable:
        blockers.append("At least one evidence exclusion audit is incomplete.")
    if not ordering_invariant:
        blockers.append("Evidence selection is not order invariant.")
    if not periods_aligned:
        blockers.append("At least one critical identity/period/citation gap remains.")
    if not contradictions_retained:
        blockers.append("Contradiction retention has not passed every case.")
    if not no_lookahead_passed:
        blockers.append("Historical no-lookahead validation has not passed.")
    return QualityBenchmarkAcceptance(
        status="proceed_to_30_case_benchmark" if not blockers else "blocked",
        benchmark_ready_count=ready,
        required_ready_count=required_ready_count,
        total_development_cases=len(development),
        exclusions_auditable=exclusions_auditable,
        ordering_invariant=ordering_invariant,
        periods_aligned=periods_aligned,
        contradictions_retained=contradictions_retained,
        no_lookahead_passed=no_lookahead_passed,
        blockers=blockers,
    )


def aggregate_cost_learning(
    summaries: Iterable[ResearchUsageSummary],
) -> CostLearningReport:
    rows = list(summaries)
    if not rows:
        return CostLearningReport(
            status="no_observations",
            run_count=0,
            median_total_tokens=0,
            p90_total_tokens=0,
            median_latency_ms=0,
            p90_latency_ms=0,
            notes=[
                "No production budget is inferred until live usage exists."
            ],
        )
    token_values = [item.total_tokens for item in rows]
    latency_values = [item.wall_clock_ms for item in rows]
    costs = [
        item.provider_cost_usd for item in rows
        if item.provider_cost_usd is not None
    ]
    return CostLearningReport(
        status="observed_no_cost_cap_selected",
        run_count=len(rows),
        median_total_tokens=round(statistics.median(token_values)),
        p90_total_tokens=_percentile(token_values, 0.9),
        median_latency_ms=round(statistics.median(latency_values)),
        p90_latency_ms=_percentile(latency_values, 0.9),
        median_provider_cost_usd=(
            round(statistics.median(costs), 6) if costs else None
        ),
        p90_provider_cost_usd=(
            round(_percentile_float(costs, 0.9), 6) if costs else None
        ),
        review_required_runs=sum(item.review_required for item in rows),
        notes=[
            "There is no fixed pilot dollar cap.",
            "A production budget should be selected only after median and "
            "90th-percentile quality/cost observations exist by profile.",
        ],
    )


def validate_sol_open_research_reference(
    payload: object,
    *,
    allowed_source_types: set[str],
) -> dict:
    """Validate discovery metadata without admitting it as app evidence."""
    if not isinstance(payload, dict):
        return {
            "status": "malformed_response",
            "validated_discoveries": [],
            "rejected_discoveries": [],
            "admitted_to_evidence": False,
        }
    raw = payload.get("source_discoveries")
    if not isinstance(raw, list):
        raw = []
    accepted = []
    rejected = []
    seen_origins: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        source_type = str(item.get("source_type") or "")
        publisher = str(item.get("publisher") or "")
        published_at = str(item.get("published_at") or "")
        claim = str(
            item.get("claim_to_check")
            or item.get("claim")
            or ""
        )
        origin = str(
            item.get("ownership_origin")
            or item.get("source_origin_group")
            or publisher
        )
        reasons = []
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            reasons.append("invalid_or_missing_url")
        if source_type not in allowed_source_types:
            reasons.append("unregistered_source_type")
        if not publisher:
            reasons.append("missing_publisher")
        if not published_at:
            reasons.append("missing_publication_date")
        if not claim:
            reasons.append("missing_claim_to_validate")
        fingerprint = (origin.casefold(), claim.casefold())
        if fingerprint in seen_origins:
            reasons.append("duplicate_or_syndicated_origin")
        seen_origins.add(fingerprint)
        normalized = {
            "title": str(item.get("title") or ""),
            "publisher": publisher,
            "url": url,
            "published_at": published_at,
            "source_type": source_type,
            "ownership_origin": origin,
            "licensing_policy": str(
                item.get("licensing_policy")
                or "metadata_only_pending_validation"
            ),
            "claim_to_check": claim,
            "status": (
                "discovery_metadata_validated"
                if not reasons else "rejected"
            ),
            "rejection_reasons": reasons,
            "admitted_to_evidence": False,
        }
        (accepted if not reasons else rejected).append(normalized)
    return {
        "status": (
            "discovery_candidates_validated"
            if accepted else "no_validated_discoveries"
        ),
        "validated_discoveries": accepted,
        "rejected_discoveries": rejected,
        "admitted_to_evidence": False,
        "policy": (
            "The Sol open-research lane is an upper-bound source scout. "
            "Discoveries require deterministic retrieval and factual "
            "validation in a later run before entering the evidence pack."
        ),
    }


def _input_score(value: InputSufficiencyScore | None) -> int:
    return value.total_score if value else 0


def _model_scores(
    run: DualModelResearchRun,
) -> tuple[int | None, int | None]:
    if run.benchmark is None:
        return None, None
    return run.benchmark.deepseek_score, run.benchmark.sol_score


def _gap_category(message: str) -> str:
    text = message.casefold()
    if "period" in text or "current/prior" in text:
        return "period_alignment"
    if "peer" in text or "industry" in text:
        return "peer_industry"
    if "management" in text or "transcript" in text:
        return "management_record"
    if "valuation" in text or "credit" in text:
        return "valuation_credit"
    if "citation" in text or "source url" in text:
        return "citation_integrity"
    return "evidence_coverage"


def _percentile(values: list[int], quantile: float) -> int:
    return round(_percentile_float([float(item) for item in values], quantile))


def _percentile_float(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return ordered[index]
