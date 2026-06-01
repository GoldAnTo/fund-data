#!/usr/bin/env python3
"""Print the fund-data coverage report.

Demonstrates:

* Loading the `scripts` package without installing the project
  (the script prepends the path itself).
* Reading the SQLite store through `fund_data.coverage_report`.
* Emitting the same Markdown table that `scripts/coverage_report.py`
  produces — useful as a one-liner for an agent task.

Run::

    python3 examples/coverage_report.py
    python3 examples/coverage_report.py --stale --stale-limit 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the in-tree `scripts` package importable without `pip install -e .`.
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "fund-data" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fund_data  # noqa: E402  (import after sys.path tweak)

DEFAULT_DB = SCRIPTS_DIR.parent / "data" / "fund_data.sqlite"


def _print_markdown(db_path: Path, *, limit: int | None) -> None:
    rows = fund_data.coverage_report(
        db_path=db_path,
        only_incomplete=False,
        limit=limit,
    )
    total = len(rows)
    if not total:
        print("# fund-data coverage report")
        print()
        print("_No funds in the data base yet. Run `fund_cli.py list` first._")
        return

    DATASETS = [
        "profile",
        "nav",
        "stock_holdings",
        "bond_holdings",
        "industries",
        "fees",
        "dividends",
        "splits",
    ]
    present = dict.fromkeys(DATASETS, 0)
    for r in rows:
        missing = set(r.get("missing") or [])
        for d in DATASETS:
            if d not in missing:
                present[d] += 1

    print("# fund-data coverage report")
    print()
    print(f"DB: `{db_path}`  •  funds: **{total}**")
    print()
    print("| Dataset | Present | Coverage |")
    print("|---|---:|---:|")
    for d in DATASETS:
        n = present[d]
        pct = (100.0 * n / total) if total else 0.0
        print(f"| {d} | {n} / {total} | {pct:.2f} % |")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    _print_markdown(Path(args.db), limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
