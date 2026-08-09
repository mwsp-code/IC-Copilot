from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from equity_research import config
from equity_research.dual_model_benchmark import (
    dual_model_run_from_payload,
)
from equity_research.hybrid_research import (
    OPENAI_RESPONSES_BACKEND,
    OpenAIResponsesProvider,
)
from equity_research.local_secrets import LocalSecretsManager
from equity_research.pipeline import run_us_equity_research
from equity_research.quality_benchmark import (
    aggregate_cost_learning,
    benchmark_case_manifest,
    build_gap_decomposition,
    evaluate_acceptance_gate,
    load_quality_benchmark_cases,
    validate_sol_open_research_reference,
)
from equity_research.source_planner import SOURCE_REGISTRY
from equity_research.thesis_synthesis import OpenAICompatibleProvider


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the quality-first DeepSeek/Sol input benchmark. The command "
            "is a dry run unless --execute is supplied."
        )
    )
    parser.add_argument(
        "--cohort",
        choices=("development", "validation", "all"),
        default="development",
    )
    parser.add_argument("--tickers", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--sol-open-reference",
        action="store_true",
        help=(
            "Run one additional web-enabled Sol source-scout call per case. "
            "Discoveries remain outside app evidence until deterministic "
            "validation."
        ),
    )
    parser.add_argument(
        "--no-lookahead-suite-passed",
        action="store_true",
        help=(
            "Assert only after the repository no-lookahead tests passed for "
            "this code revision. Otherwise the acceptance gate remains blocked."
        ),
    )
    parser.add_argument(
        "--output",
        default="benchmarks/quality-first-latest.json",
    )
    parser.add_argument(
        "--baseline-json",
        default="",
        help="Optional earlier benchmark JSON for input-uplift decomposition.",
    )
    args = parser.parse_args()

    cases = load_quality_benchmark_cases(ROOT / "data/quality_benchmark_cases.csv")
    requested = {
        item.strip().upper() for item in args.tickers.split(",") if item.strip()
    }
    selected = [
        item for item in cases
        if (args.cohort == "all" or item.cohort == args.cohort)
        and (not requested or item.ticker in requested)
    ]
    preflight = {
        "version": "quality-first-input-benchmark-v1",
        "mode": "quality_first_no_fixed_cost_cap",
        "cases": benchmark_case_manifest(selected),
        "estimated_model_calls": len(selected) * (
            11 if args.sol_open_reference else 10
        ),
        "maximum_retrieval_passes": len(selected) * 2,
        "automatic_retry_limit_per_audited_call": 1,
        "no_lookahead_suite_asserted": args.no_lookahead_suite_passed,
        "spend_policy": (
            "No fixed pilot dollar cap. Live token, latency, provider charge, "
            "retry and retrieval usage is recorded. Review is required when "
            "actual usage exceeds preflight by more than 2x."
        ),
    }
    if not args.execute:
        print(json.dumps(preflight, indent=2))
        print(
            "\nDry run only. Add --execute after reviewing provider usage and "
            "the selected cases."
        )
        return 0

    manager = LocalSecretsManager()
    manager.load_into_environment()
    config.refresh_runtime_secrets()
    if not config.DEEPSEEK_API_KEY or not config.OPENAI_API_KEY:
        raise SystemExit(
            "Both DEEPSEEK_API_KEY and OPENAI_API_KEY must be available in "
            "the current process or OS keychain."
        )
    deepseek = OpenAICompatibleProvider(
        api_key=config.DEEPSEEK_API_KEY,
        model=config.HYBRID_DEEPSEEK_MODEL,
        base_url="https://api.deepseek.com",
        provider_name="deepseek",
        timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
        max_tokens=config.HYBRID_DEEPSEEK_MAX_TOKENS,
        reasoning_effort=config.HYBRID_DEEPSEEK_REASONING_EFFORT,
        thinking_enabled=config.HYBRID_DEEPSEEK_THINKING,
    )
    sol = OpenAIResponsesProvider(
        config.OPENAI_API_KEY,
        model=config.HYBRID_OPENAI_MODEL,
        reasoning_mode="pro",
        reasoning_effort=config.HYBRID_SOL_REASONING_EFFORT,
        max_output_tokens=config.HYBRID_SOL_MAX_OUTPUT_TOKENS,
        timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
    )
    sol_reference = (
        OpenAIResponsesProvider(
            config.OPENAI_API_KEY,
            model=config.HYBRID_OPENAI_MODEL,
            reasoning_mode="pro",
            reasoning_effort=config.HYBRID_SOL_REASONING_EFFORT,
            max_output_tokens=config.HYBRID_SOL_MAX_OUTPUT_TOKENS,
            web_search_enabled=True,
            research_reference_mode=True,
            timeout_seconds=config.HYBRID_RESEARCH_TIMEOUT_SECONDS,
        )
        if args.sol_open_reference else None
    )
    baseline = _load_baseline(args.baseline_json)
    completed = []
    for case in selected:
        print(f"Running quality-first benchmark: {case.ticker}", flush=True)
        reference_result = None
        if sol_reference is not None:
            reference_payload = sol_reference.complete_json({
                "task": (
                    "Independently identify the strongest evidence and source "
                    "paths needed to research this company and focal mechanism."
                ),
                "ticker": case.ticker,
                "research_focus": case.research_focus,
                "required_evidence": list(case.required_evidence),
                "source_discovery_rules": [
                    "Return source_discoveries only; each item needs title, "
                    "publisher, URL, published_at, source_type, "
                    "ownership_origin, licensing_policy, and claim_to_check.",
                    "Do not treat any discovery as validated app evidence.",
                ],
                "_output_schema": _sol_reference_schema(),
            })
            reference_result = validate_sol_open_research_reference(
                reference_payload,
                allowed_source_types={
                    item.source_type for item in SOURCE_REGISTRY if item.allowed
                },
            )
            reference_result["usage"] = dict(sol_reference.last_usage)
            reference_result["provider_cost_usd"] = (
                sol_reference.last_cost_usd
            )
        result = run_us_equity_research(
            case.ticker,
            budget_mode="Premium",
            research_profile="deep_initiation",
            enable_hybrid_research=True,
            hybrid_planner_provider=deepseek,
            hybrid_executor_provider=sol,
            hybrid_checker_provider=deepseek,
            hybrid_critic_provider=sol,
            hybrid_execution_backend=OPENAI_RESPONSES_BACKEND,
        )
        run = result.dual_model_research
        if run is None:
            completed.append({
                "ticker": case.ticker,
                "status": "no_dual_model_result",
            })
            continue
        if case.ticker in baseline:
            run.gap_decomposition = build_gap_decomposition(
                baseline[case.ticker], run
            )
        completed.append({
            "ticker": case.ticker,
            "status": run.status,
            "dual_model_research": asdict(run),
            "sol_open_research_reference": reference_result,
        })

    runs = [
        dual_model_run_from_payload(item["dual_model_research"])
        for item in completed if item.get("dual_model_research")
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "preflight": preflight,
        "runs": completed,
        "acceptance_gate": asdict(evaluate_acceptance_gate(
            runs,
            no_lookahead_passed=args.no_lookahead_suite_passed,
        )),
        "cost_learning": asdict(aggregate_cost_learning([
            run.usage_summary for run in runs if run.usage_summary
        ])),
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
    print(f"Wrote redacted benchmark report: {output}")
    return 0


def _load_baseline(path: str) -> dict[str, object]:
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("runs", []) if isinstance(payload, dict) else []
    return {
        str(item.get("ticker") or "").upper(): dual_model_run_from_payload(
            item["dual_model_research"]
        )
        for item in rows
        if isinstance(item, dict) and item.get("dual_model_research")
    }


def _sol_reference_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "source_discoveries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "publisher": {"type": "string"},
                        "url": {"type": "string"},
                        "published_at": {"type": "string"},
                        "source_type": {"type": "string"},
                        "ownership_origin": {"type": "string"},
                        "licensing_policy": {"type": "string"},
                        "claim_to_check": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "publisher",
                        "url",
                        "published_at",
                        "source_type",
                        "ownership_origin",
                        "licensing_policy",
                        "claim_to_check",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "source_discoveries"],
        "additionalProperties": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
