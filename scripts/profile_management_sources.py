from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from equity_research import config  # noqa: E402
from equity_research.management_sources import (  # noqa: E402
    AlphaVantageTranscriptProvider,
    CsvTranscriptProvider,
    FmpTranscriptProvider,
    IssuerIrArtifactProvider,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Profile management-source provider latency by provider.")
    parser.add_argument("--ticker", default="BABA")
    parser.add_argument("--issuer-timeout", type=int, default=8)
    parser.add_argument("--transcript-timeout", type=int, default=25)
    parser.add_argument("--max-documents-per-seed", type=int, default=config.ISSUER_IR_MAX_DOCUMENTS_PER_SEED)
    args = parser.parse_args()

    providers = [
        IssuerIrArtifactProvider(
            timeout_seconds=args.issuer_timeout,
            max_documents_per_seed=args.max_documents_per_seed,
        ),
        AlphaVantageTranscriptProvider(timeout_seconds=args.transcript_timeout),
        FmpTranscriptProvider(timeout_seconds=args.transcript_timeout),
        CsvTranscriptProvider(),
    ]

    rows = []
    total_started = perf_counter()
    for provider in providers:
        started = perf_counter()
        try:
            documents, turns, statuses = provider.fetch_documents(args.ticker)
            status = "ok"
            error = ""
        except Exception as exc:  # pragma: no cover - profiling boundary
            documents, turns, statuses = [], [], []
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        row = {
            "provider": provider.provider_name,
            "status": status,
            "duration_ms": (perf_counter() - started) * 1000,
            "documents": len(documents),
            "turns": len(turns),
            "provider_statuses": [
                {
                    "status": item.status,
                    "entitlement_status": item.entitlement_status,
                    "message": item.message[:240],
                }
                for item in statuses[:12]
            ],
            "error": error,
        }
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print(json.dumps({
        "ticker": args.ticker.upper(),
        "total_ms": (perf_counter() - total_started) * 1000,
        "providers": rows,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
