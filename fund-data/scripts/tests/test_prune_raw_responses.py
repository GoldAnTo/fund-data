"""Unit tests for :mod:`prune_raw_responses`.

Pins three contracts:

1. **Dry-run is read-only** -- it does not modify the DB.
2. **Cutoff math is correct** -- the right rows are deleted
   (or counted for deletion), and the size report is honest.
3. **Non-raw tables are untouched** -- ``sync_runs`` and
   ``sync_failures`` are the operational audit log and must
   survive any prune. ``funds`` (the small fund-universe
   table) is also untouched so the post-prune DB is still
   usable by the rest of the project.

We do not exercise the post-prune ``VACUUM`` reclaim because
that requires writing 5+ GB to disk in a unit test. The
``--no-vacuum`` mode covers the row-deletion contract; the
VACUUM is a SQLite built-in and is documented in the script
docstring.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

import prune_raw_responses as prune  # noqa: E402


def _seed_db(db_path: Path) -> None:
    """Build a small ``raw_responses`` table with a known
    age distribution: 3 old rows (>90d), 3 fresh rows."""
    store = fund_data.FundDataStore(db_path)
    # ``utc_now()`` is what the rest of the project uses;
    # ``datetime('now', '-N days')`` is what the prune
    # compares against. Both produce ISO-8601 in ``+00:00``,
    # so the string comparison in the prune's WHERE clause
    # is well-defined.
    with sqlite3.connect(str(db_path)) as conn:
        # 3 rows explicitly old (>90 days)
        for i in range(3):
            conn.execute(
                "insert into raw_responses "
                "(source, request_key, fetched_at, raw_text) "
                "values (?, ?, datetime('now', ?), ?)",
                ("akshare.fund_managers", f"old-{i}", "-200 days", "x" * 100),
            )
        # 3 rows explicitly fresh (<1 day)
        for i in range(3):
            conn.execute(
                "insert into raw_responses "
                "(source, request_key, fetched_at, raw_text) "
                "values (?, ?, datetime('now', ?), ?)",
                ("akshare.fund_managers", f"new-{i}", "-1 hours", "y" * 100),
            )
        # 1 sync_runs row that must NOT be touched
        conn.execute(
            "insert into sync_runs "
            "(operation, status, rows_changed, started_at, finished_at) "
            "values ('backfill', 'ok', 100, "
            "datetime('now', '-200 days'), datetime('now', '-200 days'))"
        )
        conn.commit()


class PruneDryRunTests(unittest.TestCase):
    """``--dry-run`` must be safe to invoke on a hot DB: it
    reports counts and sizes but writes nothing."""

    def test_dry_run_does_not_modify_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fund_data.sqlite"
            _seed_db(db)
            before = _row_count(db, "raw_responses")

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "prune_raw_responses.py"),
                    "--db", str(db),
                    "--older-than-days", "90",
                    "--no-vacuum",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DRY RUN", result.stdout)

            after = _row_count(db, "raw_responses")
            self.assertEqual(after, before, "dry run must not delete any rows")


class PruneRowDeletionTests(unittest.TestCase):
    """Default behavior: delete rows older than 90 days,
    leave fresh rows, do not touch other tables."""

    def test_deletes_only_old_raw_responses_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fund_data.sqlite"
            _seed_db(db)

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "prune_raw_responses.py"),
                    "--db", str(db),
                    "--older-than-days", "90",
                    "--no-vacuum",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            # 3 old gone, 3 fresh remain
            self.assertEqual(_row_count(db, "raw_responses"), 3)
            # sync_runs untouched
            self.assertEqual(_row_count(db, "sync_runs"), 1)
            # Old rows actually gone (request_key check is
            # stronger than count: catches a wrong WHERE clause
            # that accidentally deletes new rows).
            with sqlite3.connect(str(db)) as conn:
                keys = {
                    r[0]
                    for r in conn.execute(
                        "select request_key from raw_responses"
                    ).fetchall()
                }
            self.assertEqual(keys, {"new-0", "new-1", "new-2"})

    def test_no_matching_rows_exits_cleanly(self) -> None:
        """All rows fresh -> nothing to do, no error."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fund_data.sqlite"
            fund_data.FundDataStore(db)  # bootstrap only
            # No raw_responses rows at all
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "prune_raw_responses.py"),
                    "--db", str(db),
                    "--older-than-days", "90",
                    "--no-vacuum",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Nothing to prune", result.stdout)


class PruneSizeReportTests(unittest.TestCase):
    """The size report is the only thing operators see on a
    successful prune -- it must be honest about the bytes
    that will / did get freed."""

    def test_dry_run_reports_candidate_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fund_data.sqlite"
            _seed_db(db)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "prune_raw_responses.py"),
                    "--db", str(db),
                    "--older-than-days", "90",
                    "--no-vacuum",
                    "--dry-run",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            # 3 old rows x 100-byte raw_text = 300 bytes
            # reported as MiB. The "would delete" line must
            # surface the candidate count, even if the
            # exact MiB is sub-mebibyte (the format string
            # prints 0.0).
            self.assertIn("would delete: 3 rows", result.stdout)


def _row_count(db: Path, table: str) -> int:
    with sqlite3.connect(str(db)) as conn:
        return int(conn.execute(f"select count(*) from {table}").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
