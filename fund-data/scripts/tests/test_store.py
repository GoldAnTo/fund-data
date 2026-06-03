"""Unit tests for ``scripts/fund_data/store.py``.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Pins the SQLite persistence layer's public contract: the
constructor / connect / record_raw_response helpers, the
WAL + busy_timeout pragmas, and the per-table upsert
shape. The deep ``upsert_*`` SQL is already covered by
``test_fund_data``'s 80+ cases; this file focuses on
the lifecycle.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from fund_data import store  # noqa: E402


class ConstructorTests(unittest.TestCase):
    def test_constructor_creates_db_file_and_parent_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "subdir" / "fund.sqlite"
            store.FundDataStore(target)
            self.assertTrue(target.is_file())

    def test_schema_runs_to_schema_version(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            path = Path(f.name)
        try:
            store.FundDataStore(path)
            with sqlite3.connect(path) as conn:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, store.FUND_DATA_SCHEMA_VERSION)
        finally:
            path.unlink()

    def test_required_tables_exist(self) -> None:
        # Lock the 14-table list. A drift here means a
        # migration was added but the ``store`` import was
        # not updated, or vice versa.
        with sqlite3.connect(":memory:") as conn:
            for table in (
                "funds", "nav_history", "snapshots", "raw_responses",
                "sync_runs", "sync_failures", "stock_holdings",
                "fund_profiles", "bond_holdings", "industry_allocations",
                "fee_structures", "dividends", "splits",
                "fund_managers", "fund_manager_links",
            ):
                # We only need a no-op check that the
                # SQL we generate in ``store`` references
                # the table; the table itself is created by
                # ``ensure_schema`` which the constructor
                # runs. Use a fresh tmp db so we get a real
                # schema, not :memory: quirks.
                pass
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
                path = Path(f.name)
            try:
                store.FundDataStore(path)
                with sqlite3.connect(path) as conn:
                    names = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                for table in (
                    "funds", "nav_history", "snapshots", "raw_responses",
                    "sync_runs", "sync_failures", "stock_holdings",
                    "fund_profiles", "bond_holdings", "industry_allocations",
                    "fee_structures", "dividends", "splits",
                    "fund_managers", "fund_manager_links",
                ):
                    with self.subTest(table=table):
                        self.assertIn(table, names)
            finally:
                path.unlink()


class ConnectionTests(unittest.TestCase):
    def test_connection_uses_wal_mode_and_long_busy_timeout(self) -> None:
        # The class turns on ``PRAGMA journal_mode=WAL`` and
        # ``PRAGMA busy_timeout=30000`` at connect time so
        # long backfills do not abort with "database is
        # locked" mid-write. Pin the pragmas so a future
        # change does not silently regress to journal=DELETE.
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            path = Path(f.name)
        try:
            db = store.FundDataStore(path)
            with db.connect() as conn:
                journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")
            self.assertGreaterEqual(busy_timeout, 30000)
        finally:
            path.unlink()

    def test_connection_commits_on_clean_exit(self) -> None:
        # ``connect`` is a context manager that commits on
        # a clean ``__exit__``. A bare ``connect()`` +
        # write + exit must leave the row visible from a
        # separate connection.
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            path = Path(f.name)
        try:
            db = store.FundDataStore(path)
            with db.connect() as conn:
                conn.execute(
                    "insert into sync_runs (operation, status, rows_changed, started_at, finished_at) "
                    "values (?, ?, ?, ?, ?)",
                    ("test", "ok", 0, "2024-01-01", "2024-01-01"),
                )
            with sqlite3.connect(path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM sync_runs").fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            path.unlink()


class RawResponseTests(unittest.TestCase):
    def test_record_raw_response_persists_and_overwrites(self) -> None:
        # The primary key is (source, request_key); a second
        # write with the same pair overwrites the previous
        # row (the ON CONFLICT path). Pin so the UPSERT
        # SQL stays correct.
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            path = Path(f.name)
        try:
            db = store.FundDataStore(path)
            db.record_raw_response("eastmoney.search", "110022", "old body")
            db.record_raw_response("eastmoney.search", "110022", "new body")
            with sqlite3.connect(path) as conn:
                text = conn.execute(
                    "SELECT raw_text FROM raw_responses WHERE source=? AND request_key=?",
                    ("eastmoney.search", "110022"),
                ).fetchone()[0]
            self.assertEqual(text, "new body")
        finally:
            path.unlink()


class ExposedSymbolsTests(unittest.TestCase):
    """The store's ``__all__`` is the contract. Any name in it
    must be importable; any name NOT in it must not be
    considered a public surface."""

    def test_dunder_all_matches_actual_exports(self) -> None:
        for name in store.__all__:
            with self.subTest(name=name):
                self.assertTrue(
                    hasattr(store, name),
                    f"store.__all__ lists {name!r} but the module does not export it",
                )


if __name__ == "__main__":
    unittest.main()
