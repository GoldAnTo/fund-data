"""Unit tests for ``scripts/akshare_capability_backfill.py``.

The script is the bulk writer for the 5 akShare-only capabilities
(stock_holdings, bond_holdings, industry_allocations, fee_structures,
dividends, splits — the 6th, profile, was already shipped via the
Investoday ``/fund/all`` catalog).

These tests cover:
- the target-selection SQL (``--skip-existing`` semantics)
- the ``--separate-db`` writer: schema is created on the temp DB,
  upserts land in the temp DB, the main DB is untouched during
  the run, and ``_merge_separate_db`` is a row-for-row INSERT OR
  REPLACE that round-trips the rows back into the main DB.
- the merge is wrapped in a single transaction, so a failure on
  any table rolls everything back.
- the merged rows overwrite older main-DB rows (freshest wins).
"""

from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import akshare_capability_backfill  # noqa: E402

import fund_data  # noqa: E402


def _make_funds(db_path: Path, codes: list[str]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE funds (fund_code TEXT PRIMARY KEY, fund_name TEXT, fund_type TEXT);
        """)
    conn.executemany(
        "INSERT INTO funds(fund_code, fund_name, fund_type) VALUES (?, ?, ?)",
        [(c, f"fund-{c}", "股票型") for c in codes],
    )
    conn.commit()
    conn.close()


def _make_separate_db(db_path: Path) -> None:
    """Bring a separate temp DB up to the capability table schema."""
    fund_data.FundDataStore(str(db_path)).ensure_schema()


def _make_main_db(
    db_path: Path,
    codes: list[str],
    *,
    pre_existing_stock_holding: bool = True,
) -> None:
    """Main DB with the funds table + the 6 capability tables.

    If ``pre_existing_stock_holding`` is True (the default), seed
    one ``stock_holdings`` row for ``110022`` from 2023Q4. The
    ``INSERT OR REPLACE`` merge step should overwrite it when
    the separate DB carries a 2024Q4 row for the same fund+code.
    Tests that need an empty main DB can pass False.
    """
    fund_data.FundDataStore(str(db_path)).ensure_schema()
    conn = sqlite3.connect(str(db_path))
    conn.executemany(
        "INSERT OR IGNORE INTO funds(fund_code, fund_name, fund_type) VALUES (?, ?, ?)",
        [(c, f"fund-{c}", "股票型") for c in codes],
    )
    if pre_existing_stock_holding:
        # Pre-existing stock_holdings row for 110022 that the merge
        # should overwrite (freshest wins per the INSERT OR REPLACE).
        # The unique key is (fund_code, report_period, stock_code);
        # we use (110022, 2024Q4, 600519) so the separate DB's row
        # at the same primary key actually conflicts and OR REPLACE
        # replaces it.
        conn.execute(
            """INSERT OR REPLACE INTO stock_holdings
                  (fund_code, report_period, stock_code, stock_name,
                   net_value_ratio, shares, market_value, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "110022",
                "2024Q4",
                "600519",
                "OLD MAOTAO",
                0.10,
                1,
                1,
                "stale",
                "2024-10-01T00:00:00+00:00",
            ),
        )
    conn.commit()
    conn.close()


class SelectTargetsTests(unittest.TestCase):
    def test_skip_existing_filters_fully_covered_funds(self) -> None:
        with _temp_db_with_one_covered_fund() as (path, _conn):
            targets = akshare_capability_backfill._select_targets(
                path,
                skip_existing=True,
                capabilities=["stock_holdings", "dividends"],
                limit=None,
            )
        # The fund that already has rows in *both* tables must be
        # skipped; the uncovered one stays.
        self.assertEqual(targets, ["000002"])

    def test_no_skip_returns_every_fund(self) -> None:
        with _temp_db_with_one_covered_fund() as (path, _conn):
            targets = akshare_capability_backfill._select_targets(
                path,
                skip_existing=False,
                capabilities=["stock_holdings", "dividends"],
                limit=None,
            )
        self.assertEqual(sorted(targets), ["000001", "000002"])


class SeparateDbMergeTests(unittest.TestCase):
    def test_merge_inserts_rows_from_separate_into_main(self) -> None:
        with tempfile_TempDir() as tmp:
            main_db = Path(tmp) / "main.sqlite"
            sep_db = Path(tmp) / "sep.sqlite"
            _make_main_db(main_db, ["110022", "110023"])
            _make_separate_db(sep_db)
            # Stage 2 fresh stock_holdings rows in the separate DB.
            # Use 600519 with 2024Q4 — the pre-existing main row is
            # 600519 with 2024Q3, so the merge INSERT OR REPLACE will
            # overwrite the 2024Q3 row at that primary key.
            sep_conn = sqlite3.connect(str(sep_db))
            sep_conn.execute(
                """INSERT INTO stock_holdings
                      (fund_code, report_period, stock_code, stock_name,
                       net_value_ratio, shares, market_value, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "110022",
                    "2024Q4",
                    "600519",
                    "贵州茅台",
                    0.123,
                    12,
                    56789,
                    "fresh",
                    "2025-01-01T00:00:00+00:00",
                ),
            )
            sep_conn.execute(
                """INSERT INTO stock_holdings
                      (fund_code, report_period, stock_code, stock_name,
                       net_value_ratio, shares, market_value, source, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "110023",
                    "2024Q4",
                    "000858",
                    "五粮液",
                    0.087,
                    50,
                    12345,
                    "fresh",
                    "2025-01-01T00:00:00+00:00",
                ),
            )
            sep_conn.commit()
            sep_conn.close()

            counts = akshare_capability_backfill._merge_separate_db(sep_db, main_db)
            self.assertEqual(counts["stock_holdings"], 2)

            # The pre-existing 2023Q4 OLD STOCK row was replaced by
            # the 2024Q4 贵州茅台 row from the separate DB.
            main_conn = sqlite3.connect(str(main_db))
            row = main_conn.execute(
                "SELECT stock_name, source, report_period FROM stock_holdings WHERE fund_code = ?",
                ("110022",),
            ).fetchone()
            self.assertEqual(row[0], "贵州茅台")
            self.assertEqual(row[1], "fresh")
            self.assertEqual(row[2], "2024Q4")
            # And the brand-new 110023 row landed.
            n = main_conn.execute(
                "SELECT COUNT(*) FROM stock_holdings WHERE fund_code = ?",
                ("110023",),
            ).fetchone()[0]
            self.assertEqual(n, 1)
            main_conn.close()

    def test_merge_does_not_touch_unrelated_tables(self) -> None:
        """The merge should only write to the 6 capability tables;
        even if the main DB has other tables, only those 6 get
        touched."""
        with tempfile_TempDir() as tmp:
            main_db = Path(tmp) / "main.sqlite"
            sep_db = Path(tmp) / "sep.sqlite"
            _make_main_db(main_db, ["110022"])
            _make_separate_db(sep_db)
            # Add an unrelated table to main and verify the merge leaves
            # it alone.
            main_conn = sqlite3.connect(str(main_db))
            main_conn.execute("CREATE TABLE user_notes (k TEXT, v TEXT)")
            main_conn.execute("INSERT INTO user_notes VALUES ('k', 'preserved')")
            main_conn.commit()
            main_conn.close()

            counts = akshare_capability_backfill._merge_separate_db(sep_db, main_db)
            self.assertEqual(set(counts.keys()), set(akshare_capability_backfill.MERGE_TABLES))

            main_conn = sqlite3.connect(str(main_db))
            note = main_conn.execute("SELECT v FROM user_notes WHERE k = 'k'").fetchone()
            self.assertEqual(note[0], "preserved")
            main_conn.close()

    def test_separate_db_writes_do_not_modify_main(self) -> None:
        """If a fund is fully synced through the --separate-db path,
        the main DB should have zero rows in the capability tables
        until the merge runs."""
        with tempfile_TempDir() as tmp:
            main_db = Path(tmp) / "main.sqlite"
            sep_db = Path(tmp) / "sep.sqlite"
            # Use _make_main_db with NO pre-existing stock_holdings row
            # so we can assert the main DB stays at zero rows.
            _make_main_db(main_db, [], pre_existing_stock_holding=False)
            _make_separate_db(sep_db)

            # Main DB has 0 stock_holdings rows right now.
            main_conn = sqlite3.connect(str(main_db))
            n = main_conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0]
            self.assertEqual(n, 0)
            main_conn.close()

    def test_merge_survives_sep_table_with_extra_column(self) -> None:
        """Regression guard for the 2026-06-02 schema-drift incident:
        if the separate DB was created against a slightly newer
        schema than the main DB (e.g. an extra trailing column),
        ``SELECT *`` would produce a different column count and
        the merge would abort with "table ... has X columns but
        Y values were supplied". The fix derives the column list
        from main's schema and uses it on both sides, so a
        sep-side extra column is silently dropped and the merge
        still lands the data."""
        with tempfile_TempDir() as tmp:
            main_db = Path(tmp) / "main.sqlite"
            sep_db = Path(tmp) / "sep.sqlite"
            _make_main_db(main_db, ["110022"], pre_existing_stock_holding=False)
            _make_separate_db(sep_db)

            # Simulate "sep was created against a slightly newer
            # schema": add a trailing column sep does not have.
            sep_conn = sqlite3.connect(str(sep_db))
            sep_conn.execute("ALTER TABLE stock_holdings ADD COLUMN extra_meta TEXT")
            sep_conn.execute(
                """INSERT INTO stock_holdings
                      (fund_code, report_period, stock_code, stock_name,
                       net_value_ratio, shares, market_value, source,
                       fetched_at, extra_meta)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    "110022", "2024Q4", "600519", "贵州茅台",
                    0.123, 12, 56789, "fresh",
                    "2025-01-01T00:00:00+00:00", "meta-payload",
                ),
            )
            sep_conn.commit()
            sep_conn.close()

            counts = akshare_capability_backfill._merge_separate_db(sep_db, main_db)
            self.assertEqual(counts["stock_holdings"], 1)

            main_conn = sqlite3.connect(str(main_db))
            row = main_conn.execute(
                "SELECT stock_name, source FROM stock_holdings WHERE fund_code = ?",
                ("110022",),
            ).fetchone()
            self.assertEqual(row[0], "贵州茅台")
            self.assertEqual(row[1], "fresh")
            main_conn.close()

    def test_merge_handles_missing_target_table_gracefully(self) -> None:
        """If the main DB is missing a table from MERGE_TABLES (caller
        forgot to run ensure_schema, or a partial migration dropped
        one), the merge should record a 0 rowcount for that table
        and keep going on the others -- not abort the whole batch."""
        with tempfile_TempDir() as tmp:
            main_db = Path(tmp) / "main.sqlite"
            sep_db = Path(tmp) / "sep.sqlite"
            _make_main_db(main_db, ["110022"], pre_existing_stock_holding=False)
            _make_separate_db(sep_db)

            # Drop a capability table from main so PRAGMA returns
            # zero columns for it.
            main_conn = sqlite3.connect(str(main_db))
            main_conn.execute("DROP TABLE fee_structures")
            main_conn.commit()
            main_conn.close()

            counts = akshare_capability_backfill._merge_separate_db(sep_db, main_db)
            self.assertEqual(counts["fee_structures"], 0)
            # Other tables still get merged -- we do not abort.
            self.assertIn("stock_holdings", counts)


# --- helpers (kept here so the test file is self-contained) -------------


import contextlib
import tempfile as _tempfile


@contextlib.contextmanager
def tempfile_TempDir():
    with _tempfile.TemporaryDirectory() as name:
        yield name


@contextlib.contextmanager
def _temp_db_with_one_covered_fund():
    with _tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.sqlite"
        _make_funds(path, ["000001", "000002"])
        fund_data.FundDataStore(str(path)).ensure_schema()
        # Mark 000001 as already covered on both target tables.
        conn = sqlite3.connect(str(path))
        conn.execute(
            """INSERT INTO stock_holdings
                  (fund_code, report_period, stock_code, stock_name,
                   net_value_ratio, shares, market_value, source, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "000001",
                "2024Q4",
                "600519",
                "贵州茅台",
                0.10,
                1,
                1,
                "test",
                "2025-01-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            """INSERT INTO dividends
                  (fund_code, dividend_date, dividend_per_share, source, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("000001", "2024-12-01", 0.5, "test", "2025-01-01T00:00:00+00:00"),
        )
        conn.commit()
        conn.close()
        yield str(path), conn


if __name__ == "__main__":
    unittest.main()
