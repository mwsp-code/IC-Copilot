from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from equity_research.benchmarking import render_benchmark_markdown, run_fixture_benchmark


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the no-network IC Copilot research-integrity benchmark.")
    parser.add_argument("--json-out", default="benchmarks/baseline.json")
    parser.add_argument("--markdown-out", default="benchmarks/baseline.md")
    args = parser.parse_args()

    report = run_fixture_benchmark()
    json_path = ROOT / args.json_out
    markdown_path = ROOT / args.markdown_out
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    markdown_path.write_text(render_benchmark_markdown(report), encoding="utf-8")
    print(
        f"{report['status']}: {report['integrity_cases']} research-integrity cases, "
        f"{report['checks_passed']}/{report['checks_total']} checks passed."
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
