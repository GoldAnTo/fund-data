"""Prune old ``raw_responses`` rows from a local fund_data DB.

Why this script exists
----------------------
``raw_responses`` is the local HTTP-echo log -- one row per
provider call, with the full ``raw_text`` of the request and
the response. It is intentionally excluded from the OSS query
bundle (``fund_cloud.EXCLUDED_TABLES``), so it only affects
the *local dev* SQLite file (``fund-data/data/fund_data.sqlite``).

Local growth is the only thing this script protects against:
on a busy backfill day the table can grow by 2-3 GB, and the
local SQLite hits multi-hundred-GB territory after a few
months. The audit value of a 30-day-old raw response is low
(provider responses only matter while a backfill cycle is
still reconciling them) so the standard policy is a rolling
90-day window. ``sync_runs`` and ``sync_failures`` are NOT
touched -- those are the operational audit log and are tiny
relative to ``raw_responses``.

Typical use::

    # Dry run: see what would be deleted, do not modify the DB
    .venv-akshare/bin/python3 scripts/prune_raw_responses.py \\
        --older-than-days 90 --dry-run

    # Default: delete rows older than 90 days, then VACUUM
    # to reclaim disk space (slower, but a one-shot reclaim)
    .venv-akshare/bin/python3 scripts/prune_raw_responses.py

    # Skip VACUUM if you only want to mark rows for deletion
    # (next normal insert will reuse the freed pages without
    # a full VACUUM pass -- preferred on a hot DB)
    .venv-akshare/bin/python3 scripts/prune_raw_responses.py --no-vacuum

The ``--vacuum`` step rewrites every page of the DB file
so it can take minutes on a 5+ GB database. Skip it on a
hot DB; run it as a one-shot reclaim from a maintenance
window.

DB path resolution
------------------
By default the script targets the same DB the rest of the
project uses: ``fund_data.default_db_path()`` walks
``FUND_DATA_CACHE_DIR`` → ``FUND_DATA_DB`` → ``fund_cloud
.current_db_path()`` → the on-disk ``fund-data/data/
fund_data.sqlite`` fallback. Pass ``--db`` to override
(``fund_cli.py cloud pull`` is the typical workflow that
puts a fresh query DB into the cache; the prune then
needs ``FUND_DATA_DB`` to point at the on-disk full DB,
not the query DB -- the OSS query bundle excludes
``raw_responses`` so the prune has nothing to do on
a query DB).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402  (sys.path tweak above)

DEFAULT_OLDER_THAN_DAYS = 90


def _table_size_mb(conn: sqlite3.Connection, table: str) -> float:
    """Return the on-disk size of ``table`` in MiB.

    Uses ``dbstat`` so the result reflects actual page
    allocation rather than ``page_count * page_size`` (which
    can over-count if the table shares pages with other
    tables). Returns 0.0 if the table is empty or the
    ``dbstat`` virtual table is not available.
    """
    try:
        row = conn.execute(
            "select coalesce(sum(pgsize), 0) from dbstat where name = ?",
            (table,),
        ).fetchone()
    except sqlite3.OperationalError:
        # Older SQLite (< 3.16) lacks ``dbstat``. Fall back
        # to a coarse estimate; the prune still works, the
        # size report is just less accurate.
        return 0.0
    return float(row[0]) / 1024.0 / 1024.0 if row else 0.0


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"select count(*) from {table}").fetchone()
    return int(row[0]) if row else 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prune raw_responses rows older than N days from a local "
            "fund_data SQLite DB. Does NOT touch sync_runs / sync_failures "
            "-- those are the operational audit log and are kept forever."
        ),
    )
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "Path to the SQLite file. Defaults to "
            "``fund_data.default_db_path()`` (same resolver the "
            "CLI / MCP / batch-sync use)."
        ),
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=DEFAULT_OLDER_THAN_DAYS,
        help=(
            f"Delete rows whose ``fetched_at`` is older than this many "
            f"days. Default: {DEFAULT_OLDER_THAN_DAYS}. Pass a smaller "
            f"value to be more aggressive; pass 0 to delete all rows "
            f"(rarely useful -- prefer ``sync_fund --include-all`` "
            f"to re-fetch rather than wipe)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted and the size estimate, then exit.",
    )
    parser.add_argument(
        "--no-vacuum",
        action="store_true",
        help=(
            "Skip the post-prune VACUUM. The freed pages are reused "
            "by future inserts without a full VACUUM pass; only skip "
            "the VACUUM if you cannot afford the multi-minute rewrite "
            "of a multi-GB DB."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    db_path = Path(args.db) if args.db else fund_data.default_db_path()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    cutoff_days = max(args.older_than_days, 0)
    with sqlite3.connect(str(db_path)) as conn:
        # Read-only counts for the size report. We do this
        # BEFORE the prune so the operator can see the "before"
        # state. raw_responses uses ``fetched_at`` ISO-8601 text;
        # ``datetime('now', '-N days')`` produces a comparable
        # ISO-8601 string so the comparison is a string op (no
        # timezone juggling required -- every row was written
        # with ``utc_now()`` so they are all in the same
        # ``+00:00`` offset).
        before_count = _row_count(conn, "raw_responses")
        before_mb = _table_size_mb(conn, "raw_responses")
        candidate_count = conn.execute(
            "select count(*) from raw_responses "
            "where fetched_at < datetime('now', ?)",
            (f"-{cutoff_days} days",),
        ).fetchone()[0]
        # Cheap candidate-size estimate: same WHERE clause, but
        # sum length of raw_text so the operator sees the bytes
        # that will actually be freed. ``dbstat`` after the
        # delete gives the precise post-prune size; this is just
        # the planning number.
        candidate_mb = conn.execute(
            "select coalesce(sum(length(raw_text)), 0) / 1024.0 / 1024.0 "
            "from raw_responses where fetched_at < datetime('now', ?)",
            (f"-{cutoff_days} days",),
        ).fetchone()[0]

    print(f"DB: {db_path}")
    print(f"raw_responses before: {before_count} rows, {before_mb:.1f} MiB")
    print(
        f"would delete: {candidate_count} rows (~{candidate_mb:.1f} MiB "
        f"of raw_text, plus freed page overhead after VACUUM)"
    )
    if cutoff_days != args.older_than_days:
        print(
            f"NOTE: --older-than-days clamped to {cutoff_days} (negative values treated as 0)",
            file=sys.stderr,
        )

    if args.dry_run:
        print("DRY RUN -- no changes made.")
        return 0

    if candidate_count == 0:
        print("Nothing to prune (no rows match the cutoff).")
        return 0

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "delete from raw_responses where fetched_at < datetime('now', ?)",
            (f"-{cutoff_days} days",),
        )
        conn.commit()
        after_count = _row_count(conn, "raw_responses")
        after_mb = _table_size_mb(conn, "raw_responses")
        if not args.no_vacuum:
            # ``VACUUM`` rewrites the DB file so freed pages
            # return to the filesystem. It is the only way to
            # shrink a SQLite file -- ``DELETE``+``ANALYZE`` only
            # marks pages as free in the page pool. Cost: a full
            # file rewrite, ~1-2 minutes for 5 GB.
            print("VACUUM -- rewriting DB to reclaim disk space...")
            conn.execute("VACUUM")
            # Re-read after VACUUM so the size report reflects
            # the on-disk reclaim.
            after_mb = _table_size_mb(conn, "raw_responses")

    print(f"raw_responses after: {after_count} rows, {after_mb:.1f} MiB")
    if not args.no_vacuum:
        print(f"reclaimed: {before_mb - after_mb:.1f} MiB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
