"""Collapse the Chinese quarterly ``report_period`` values in
``stock_holdings`` / ``bond_holdings`` to their ISO quarter-end date.

Why this script exists
----------------------
AkShare's ``fund_portfolio_hold_em`` and ``fund_portfolio_bond_hold_em``
return a ``季度`` field shaped like ``"2024年4季度股票投资明细"`` /
``"2024年4季度债券投资明细"``.  The 2026-06-02 baseline has 2.4 M
``stock_holdings`` rows and 0.5 M ``bond_holdings`` rows carrying
that long-form label, while ``industry_allocations`` already
uses ``"2024-12-31"`` -- so a single JOIN / GROUP BY across the
three disclosure tables leaks every fund through the filter,
and the agent-side "latest quarter" question becomes two queries
instead of one.

The 2026-06-02 patch in :func:`fund_data._normalize_report_period`
makes every *new* row land in ISO form (AkShare provider's
``stock_holdings`` / ``bond_holdings`` call it before the upsert).
This script is the *historical* side of the same fix: it rewrites
the existing rows in one transaction, then writes a ``sync_runs``
audit row so a downstream agent can confirm the change.

The script is **safe to re-run**: the UPDATE matches on the *old*
label and writes the *new* ISO date, and after a successful pass
nothing matches the WHERE clause so the row count is 0.  We
also keep a ``--dry-run`` flag (default) that prints the planned
``UPDATE`` statements without touching the db, so an operator can
eyeball the diff before committing.

Typical use::

    # Show what would change (no db write).
    .venv-akshare/bin/python scripts/migrate_normalize_report_period.py

    # Apply.
    .venv-akshare/bin/python scripts/migrate_normalize_report_period.py --commit
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Apply the macOS proxy / IPv4 / sqlite-timeout patches BEFORE
# importing :mod:`fund_data` (or any module that might open a
# connection).  Idempotent so a re-import is cheap.
from _net_compat import apply as _apply_net_compat  # noqa: E402

_apply_net_compat()

import fund_data  # noqa: E402

# Pick up `INVESTODAY_API_KEY` / `TUSHARE_TOKEN` from the
# project-root .env (see ``fund_data._env``). The migration
# itself does not call providers, but loading the keys keeps
# the script consistent with the rest of the entry points.
from fund_data._env import load_env  # noqa: E402

load_env()

logger = logging.getLogger("fund_data.migrate_normalize_report_period")

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"

# Tables whose ``report_period`` column still carries the
# long-form Chinese label.  ``industry_allocations`` was already
# ISO when it was first written, so it is intentionally absent
# from this list.
TARGET_TABLES = ("stock_holdings", "bond_holdings")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _plan(db_path: Path) -> list[tuple[str, str, str, int]]:
    """Return the planned ``(table, old, new, count)`` updates.

    The list is ordered so a deterministic ``--commit`` run
    produces a deterministic log line, which makes the
    ``sync_runs`` audit row reproducible.
    """
    plan: list[tuple[str, str, int]] = []
    with sqlite3.connect(db_path, timeout=30) as conn:
        for table in TARGET_TABLES:
            rows = conn.execute(
                f"SELECT report_period, COUNT(*) FROM {table} "
                f"GROUP BY report_period"
            ).fetchall()
            for old_value, count in rows:
                new_value = fund_data._normalize_report_period(old_value)
                if new_value != old_value:
                    plan.append((table, old_value, new_value, count))
    # Stable order: table name, then old value, so the diff
    # is easy to eyeball across runs.
    plan.sort(key=lambda r: (r[0], r[1]))
    return plan


def _apply(db_path: Path, plan: list[tuple[str, str, str, int]]) -> dict[str, int]:
    """Run the planned UPDATEs in a single transaction.

    Returns ``{table: rows_changed}`` for the audit row.  Each
    table is its own UPDATE so the per-table row count is
    precise, and the four updates run inside one ``BEGIN``/
    ``COMMIT`` so a mid-flight crash rolls the whole migration
    back instead of leaving a half-converted db.
    """
    per_table: dict[str, int] = dict.fromkeys(TARGET_TABLES, 0)
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute("BEGIN")
        try:
            for table, old_value, new_value, _expected in plan:
                cur = conn.execute(
                    f"UPDATE {table} SET report_period = ? "
                    f"WHERE report_period = ?",
                    (new_value, old_value),
                )
                per_table[table] = per_table.get(table, 0) + cur.rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return per_table


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Apply the UPDATEs. Default: dry-run (print the plan, do not write).",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help=(
            "Write a sync_runs audit row on success. Implied by --commit; "
            "off by default in dry-run mode so a series of "
            "``migrate --dry-run`` calls does not spam the audit table."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db_path = Path(args.db)
    if not db_path.is_file():
        logger.error("db not found: %s", db_path)
        return 2

    plan = _plan(db_path)
    if not plan:
        logger.info("no rows to migrate; stock/bond report_periods are already ISO")
        if args.audit or args.commit:
            store = fund_data.FundDataStore(db_path)
            store.record_sync_run(
                operation="migrate_normalize_report_period",
                fund_code=None,
                status="ok",
                rows_changed=0,
                started_at=_utc_now(),
                message=json.dumps({"plan_size": 0, "commit": args.commit}),
            )
        return 0

    total_rows = sum(count for *_, count in plan)
    logger.info(
        "plan: %d (table, old, new) groups, %d total rows to rewrite",
        len(plan),
        total_rows,
    )
    for table, old_value, new_value, count in plan:
        logger.info("  %s: %r -> %r  (%d rows)", table, old_value, new_value, count)

    if not args.commit:
        logger.info("dry-run: pass --commit to apply")
        if args.audit:
            store = fund_data.FundDataStore(db_path)
            store.record_sync_run(
                operation="migrate_normalize_report_period",
                fund_code=None,
                status="ok",
                rows_changed=0,
                started_at=_utc_now(),
                message=json.dumps(
                    {
                        "plan_size": len(plan),
                        "total_rows": total_rows,
                        "commit": False,
                    }
                ),
            )
        return 0

    per_table = _apply(db_path, plan)
    logger.info("applied: %s", per_table)
    store = fund_data.FundDataStore(db_path)
    store.record_sync_run(
        operation="migrate_normalize_report_period",
        fund_code=None,
        status="ok",
        rows_changed=sum(per_table.values()),
        started_at=_utc_now(),
        message=json.dumps(
            {
                "plan_size": len(plan),
                "per_table": per_table,
                "commit": True,
            }
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
