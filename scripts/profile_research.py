from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from equity_research import config
from equity_research.local_secrets import LocalSecretsManager
from equity_research.performance import ResearchProfiler
from equity_research.pipeline import run_us_equity_research


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile the evidence-first research pipeline without exposing credentials.",
    )
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument(
        "--profile",
        default="adaptive_ic",
        choices=("fast_screening", "adaptive_ic", "deep_initiation"),
    )
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    LocalSecretsManager().load_into_environment()
    config.refresh_runtime_secrets()
    result = run_us_equity_research(
        args.ticker.upper(),
        research_profile=args.profile,
        profiler=ResearchProfiler(enabled=True),
    )
    report = result.profiling
    history = result.historical_research
    payload = {
        "ticker": result.identity.ticker,
        "profile": result.research_profile.profile_id if result.research_profile else args.profile,
        "total_seconds": round(report.total_ms / 1000, 3),
        "stages": [asdict(item) for item in report.steps],
        "bottlenecks": report.bottlenecks,
        "treatments": report.treatments,
        "quality_counts": {
            "events": len(result.events),
            "ideas": len(result.ideas),
            "peers": len(result.peer_universe.peers),
            "management_documents": len(result.management_sources.documents),
            "quarters_analyzed": history.analyzed_quarters if history else 0,
            "annual_reports_analyzed": history.analyzed_annual_reports if history else 0,
            "calls_analyzed": history.analyzed_calls if history else 0,
            "validated_claims": len(result.validated_claims.claims),
        },
    }
    print(json.dumps(payload, indent=2))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
