from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from equity_research.network_diagnostics import (  # noqa: E402
    default_report_path,
    run_network_diagnostics,
    write_network_diagnostic_report,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose network/proxy/provider reachability from the current Python environment. "
            "Run this from the same PowerShell venv session used for Streamlit."
        )
    )
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--no-powershell", action="store_true", help="Skip PowerShell Invoke-WebRequest comparison.")
    parser.add_argument("--output", type=Path, default=None, help="Output JSON report path. Defaults to data/network_diagnostics_<timestamp>.json")
    parser.add_argument("--print-json", action="store_true", help="Print full redacted JSON report.")
    args = parser.parse_args()

    output_path = args.output or default_report_path()
    report = run_network_diagnostics(
        timeout_seconds=args.timeout,
        include_powershell=not args.no_powershell,
    )
    write_network_diagnostic_report(report, output_path)

    print(f"Network class: {report.network_class}")
    print(f"Summary: {report.summary}")
    print(f"Report: {output_path}")
    if report.runtime_context:
        print(f"Python: {report.runtime_context.get('python_executable', 'Unknown')}")
        print(f"PID: {report.runtime_context.get('pid', 'Unknown')}")
    if report.suggested_actions:
        print("Suggested actions:")
        for action in report.suggested_actions:
            print(f"- {action}")
    if args.print_json:
        print(json.dumps(asdict(report), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
