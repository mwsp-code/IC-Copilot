from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from equity_research.providers import FmpConsensusProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Test FMP consensus access without printing the API key.")
    parser.add_argument("--tickers", default="AAPL,BABA", help="Comma-separated symbols.")
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        print("FMP_API_KEY is not set in this process.")
        return 2

    provider = FmpConsensusProvider(api_key=api_key, timeout_seconds=args.timeout)
    failures = 0
    for ticker in sorted({item.strip().upper() for item in args.tickers.split(",") if item.strip()}):
        package = provider.fetch_package(ticker)
        target = package.target
        print(
            f"{ticker}: status={package.status}; target={'yes' if target else 'no'}; "
            f"estimates={len(package.estimates)}; recommendations="
            f"{'yes' if package.recommendations else 'no'}; surprises={len(package.surprises)}"
        )
        for gap in package.data_gaps:
            print(f"  - {gap}")
        failures += int(package.status == "Unavailable")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
