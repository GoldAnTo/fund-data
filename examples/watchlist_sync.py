#!/usr/bin/env python3
"""Sync a small watchlist through `fund_data.batch_sync_funds`.

Demonstrates:

* Loading the in-tree `scripts` package without `pip install`.
* Reading a watchlist file with the same `normalize_fund_codes`
  parser the CLI uses (so `# comments` and blank lines work).
* Calling `batch_sync_funds` with a small concurrency / batch size
  suitable for a developer machine, and a 1-year NAV window.

Run::

    python3 examples/watchlist_sync.py \\
        --codes-file fund-data/data/fund_codes_sample.txt \\
        --provider eastmoney --limit 4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "fund-data" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fund_data  # noqa: E402  (import after sys.path tweak)

DEFAULT_CODES_FILE = SCRIPTS_DIR.parent / "data" / "fund_codes_sample.txt"
DEFAULT_DB = SCRIPTS_DIR.parent / "data" / "fund_data.sqlite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--codes-file",
        default=str(DEFAULT_CODES_FILE),
        help="Path to a watchlist (one code per line, `# comments` OK).",
    )
    parser.add_argument(
        "--db", default=str(DEFAULT_DB), help="SQLite path (overrides FUND_DATA_DB)."
    )
    parser.add_argument(
        "--provider",
        default="eastmoney",
        choices=("auto", "eastmoney", "akshare", "tushare", "investoday"),
        help="Provider chain to use. Default: eastmoney (fastest, no key).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=4,
        help="Maximum number of codes to sync (default: 4 — keep this small).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="In-flight HTTP calls (default: 2 — keep small for a dev machine).",
    )
    parser.add_argument(
        "--report-year",
        default="2024",
        help="Report year for holdings / fees / managers (default: 2024).",
    )
    args = parser.parse_args()

    codes = fund_data.normalize_fund_codes(
        Path(args.codes_file).read_text(encoding="utf-8").splitlines()
    )[: args.limit]
    if not codes:
        print(f"No codes found in {args.codes_file}.", file=sys.stderr)
        return 1
    print(f"Syncing {len(codes)} codes through `{args.provider}` provider...")
    result = fund_data.batch_sync_funds(
        codes,
        db_path=args.db,
        provider=args.provider,
        concurrency=args.concurrency,
        batch_size=len(codes),
        include_all=True,
        report_year=args.report_year,
    )
    print(
        f"done — ok={result.get('ok', 0)} failed={result.get('failed', 0)} "
        f"concurrency={result.get('concurrency')}"
    )
    for row in result.get("results", []):
        print(
            f"  {row.get('fund_code', '?'):<8} ok={row.get('ok')} err={row.get('error', '')[:80]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
