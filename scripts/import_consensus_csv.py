from __future__ import annotations

import argparse
from pathlib import Path

from equity_research.consensus_import import import_consensus_csv, write_consensus_csv_templates


def main() -> None:
    parser = argparse.ArgumentParser(description="Import point-in-time consensus CSV snapshots into SQLite.")
    parser.add_argument("--directory", type=Path, default=None, help="Directory containing targets/estimates/recommendations/surprises CSV files.")
    parser.add_argument("--tickers", nargs="*", default=None, help="Optional ticker list. Defaults to all tickers found in CSV files.")
    parser.add_argument("--write-templates", action="store_true", help="Write empty point-in-time consensus CSV templates, then exit.")
    parser.add_argument("--ticker", default="", help="Optional ticker used to prefill one example row when writing templates.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing templates when used with --write-templates.")
    args = parser.parse_args()
    if args.write_templates:
        result = write_consensus_csv_templates(args.directory, ticker=args.ticker, overwrite=args.overwrite)
        print(f"Directory: {result.directory}")
        print(f"Files written: {', '.join(result.files_written) if result.files_written else 'none'}")
        print(f"Files existing: {', '.join(result.files_existing) if result.files_existing else 'none'}")
        for message in result.messages:
            print(f"- {message}")
        return
    result = import_consensus_csv(args.directory, args.tickers)
    print(f"Directory: {result.directory}")
    print(f"Tickers: {', '.join(result.tickers) if result.tickers else 'none'}")
    print(f"Imported: {result.imported}; skipped: {result.skipped}")
    for message in result.messages:
        print(f"- {message}")


if __name__ == "__main__":
    main()
