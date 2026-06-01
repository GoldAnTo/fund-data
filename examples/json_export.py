#!/usr/bin/env python3
"""Export the `funds` table to JSON Lines.

Demonstrates:

* The most direct path to the SQLite store (no provider required).
* A streaming export pattern (one row at a time) that scales to
  the full 26k-fund universe without blowing the heap.
* JSON Lines (`.jsonl`) as the agent-friendly interchange format:
  one fund per line, no surrounding array.

Run::

    python3 examples/json_export.py > /tmp/funds.jsonl
    head -3 /tmp/funds.jsonl
    wc -l /tmp/funds.jsonl
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "fund-data" / "scripts"
DEFAULT_DB = SCRIPTS_DIR.parent / "data" / "fund_data.sqlite"

# Columns we surface in the export. Keep this list in sync with the
# `funds` table schema in `fund_data.FundDataStore._init_schema` —
# adding a column there should trigger an update here.
EXPORT_COLUMNS = (
    "fund_code",
    "fund_name",
    "fund_type",
    "company",
    "manager",
    "nav",
    "nav_date",
    "other_names",
    "source",
    "updated_at",
)


def _iter_rows(db_path: Path, fund_type: str | None, limit: int | None):
    if not db_path.is_file():
        raise SystemExit(
            f"data base not found: {db_path}\n"
            "Run `python3 fund-data/scripts/fund_cli.py list` to bootstrap it."
        )
    where = ""
    params: list[str] = []
    if fund_type:
        where = " WHERE fund_type LIKE ?"
        params.append(f"%{fund_type}%")
    limit_clause = f" LIMIT {int(limit)}" if limit else ""
    sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM funds{where}{limit_clause}"
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        yield from conn.execute(sql, params)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--fund-type",
        default=None,
        help="Filter by fund_type substring (e.g. '股票型' or '货币').",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    count = 0
    for row in _iter_rows(Path(args.db), args.fund_type, args.limit):
        sys.stdout.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        count += 1
    sys.stderr.write(f"# wrote {count} rows\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
