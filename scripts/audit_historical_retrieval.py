from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from equity_research import config
from equity_research.local_secrets import LocalSecretsManager
from equity_research.retrieval_audit import run_retrieval_audit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic retrieval and claim-evidence preflight. "
            "This command never invokes an LLM."
        )
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--profile",
        default="deep_initiation",
        choices=(
            "fast_screening",
            "adaptive_ic",
            "deep_initiation",
            "investigate_event",
        ),
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--output-dir",
        default="data/retrieval_audits",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    LocalSecretsManager().load_into_environment()
    config.refresh_runtime_secrets()
    report = run_retrieval_audit(
        args.ticker,
        profile=args.profile,
        refresh=args.refresh,
        offline=args.offline,
        output_dir=Path(args.output_dir),
    )
    print(report.summary)
    print(f"Input sufficiency: {report.input_sufficiency_score}/100")
    print(
        f"Claim audit: {report.claim_ready_count} passed / "
        f"{report.claim_blocked_count} blocked"
    )
    print(f"JSON: {report.output_json}")
    print(f"Markdown: {report.output_markdown}")
    if report.critical_blockers:
        print("Critical blockers:")
        for item in report.critical_blockers:
            print(f"- {item}")
    return 0 if report.benchmark_ready else 2


if __name__ == "__main__":
    sys.exit(main())
