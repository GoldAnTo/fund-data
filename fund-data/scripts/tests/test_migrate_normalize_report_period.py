"""Unit tests for the ``_normalize_report_period`` helper and the
:meth:`migrate_normalize_report_period` integration.

The helper is the small but high-leverage piece: every ``AkshareProvider``
row that lands in ``stock_holdings`` / ``bond_holdings`` flows through
it, and the migration script relies on it to compute the new value
for the 3 M existing rows.  The tests pin down:

1. Each of the five documented input shapes maps to the
   expected ISO output, plus the empty / ``None`` / unknown
   fall-throughs.
2. The helper is idempotent: an ISO date in -> the same ISO date
   out, so a re-run of the migration does not change anything.
3. The integration test runs the actual ``migrate_normalize_report_period``
   code path against a tempdb fixture and asserts the row counts
   match ``--dry-run``'s plan.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

import migrate_normalize_report_period as mnp  # noqa: E402


class _NormalizeHelperTests(unittest.TestCase):
    def test_empty_inputs_return_empty_string(self) -> None:
        self.assertEqual(fund_data._normalize_report_period(""), "")
        self.assertEqual(fund_data._normalize_report_period(None), "")

    def test_iso_passes_through_unchanged(self) -> None:
        # Idempotent: a re-run of the migration must not re-map
        # an already-ISO row to a different quarter end.
        for iso in ("2024-12-31", "2024-03-31", "2024-06-30", "2024-09-30", "2025-12-31"):
            with self.subTest(iso=iso):
                self.assertEqual(fund_data._normalize_report_period(iso), iso)

    def test_chinese_quarter_label_with_stock_suffix(self) -> None:
        # The exact shape AkShare returns for
        # ``fund_portfolio_hold_em``.
        cases = [
            ("2024年1季度股票投资明细", "2024-03-31"),
            ("2024年2季度股票投资明细", "2024-06-30"),
            ("2024年3季度股票投资明细", "2024-09-30"),
            ("2024年4季度股票投资明细", "2024-12-31"),
            ("2025年1季度股票投资明细", "2025-03-31"),
            ("2025年4季度股票投资明细", "2025-12-31"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(fund_data._normalize_report_period(raw), expected)

    def test_chinese_quarter_label_with_bond_suffix(self) -> None:
        cases = [
            ("2024年1季度债券投资明细", "2024-03-31"),
            ("2024年4季度债券投资明细", "2024-12-31"),
            ("2025年3季度债券投资明细", "2025-09-30"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(fund_data._normalize_report_period(raw), expected)

    def test_chinese_quarter_label_without_suffix(self) -> None:
        # The "just quarter" shape: callers sometimes pass
        # ``item.get("季度") or year`` with the suffix stripped.
        for raw, expected in [
            ("2024年4季度", "2024-12-31"),
            ("2023年2季度", "2023-06-30"),
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(fund_data._normalize_report_period(raw), expected)

    def test_year_only_collapses_to_dec_31(self) -> None:
        # Akshare's ``year or ""`` fallback when the upstream
        # returns no quarter column.
        self.assertEqual(fund_data._normalize_report_period("2024"), "2024-12-31")
        self.assertEqual(fund_data._normalize_report_period("2025"), "2025-12-31")

    def test_unknown_format_passes_through_unchanged(self) -> None:
        # Defensive: an unrecognised label must round-trip -- the
        # migration script surfaces these in the dry-run output so
        # the operator can decide what to do, but it must not
        # silently rewrite them to "" or a wrong quarter.
        self.assertEqual(
            fund_data._normalize_report_period("2024年半年度"), "2024年半年度"
        )
        self.assertEqual(
            fund_data._normalize_report_period("garbage"), "garbage"
        )

    def test_whitespace_only_collapses_to_empty(self) -> None:
        self.assertEqual(fund_data._normalize_report_period("   "), "")


class _MigrationIntegrationTests(unittest.TestCase):
    """End-to-end run of the migration on a tempdb fixture.

    We seed a stock_holdings / bond_holdings snapshot that mirrors
    the 2026-06-02 production shape (one long-form label per
    quarter, no ISO rows yet), call :func:`_plan` and
    :func:`_apply`, and assert the table state matches the
    expected ISO distribution.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fund_data.sqlite"
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        # Insert fixture rows -- one per documented long-form label.
        seed = [
            ("stock_holdings", "000001", "2024年1季度股票投资明细", "600519"),
            ("stock_holdings", "000001", "2024年4季度股票投资明细", "600519"),
            ("stock_holdings", "000002", "2024年2季度股票投资明细", "000001"),
            ("stock_holdings", "000003", "2025年3季度股票投资明细", "000002"),
            ("bond_holdings",  "000001", "2024年1季度债券投资明细", "127045"),
            ("bond_holdings",  "000001", "2024年4季度债券投资明细", "127045"),
            ("bond_holdings",  "000002", "2025年2季度债券投资明细", "019827"),
        ]
        with sqlite3.connect(str(self.db)) as conn:
            now = "2026-06-02T00:00:00+00:00"
            for table, fund_code, period, code in seed:
                cols = {
                    "stock_holdings": (
                        "INSERT INTO stock_holdings"
                        "(fund_code, report_period, stock_code, stock_name, "
                        "net_value_ratio, shares, market_value, source, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    ),
                    "bond_holdings": (
                        "INSERT INTO bond_holdings"
                        "(fund_code, report_period, bond_code, bond_name, "
                        "net_value_ratio, market_value, source, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    ),
                }[table]
                if table == "stock_holdings":
                    conn.execute(
                        cols,
                        (
                            fund_code, period, code, "TestName",
                            0.1, 100.0, 1000.0, "akshare.test", now,
                        ),
                    )
                else:
                    conn.execute(
                        cols,
                        (
                            fund_code, period, code, "TestBond",
                            0.1, 1000.0, "akshare.test", now,
                        ),
                    )
            conn.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_plan_reports_all_long_form_rows(self) -> None:
        plan = mnp._plan(self.db)
        # 4 stock + 3 bond distinct labels, all need rewriting.
        self.assertEqual(len(plan), 7)
        for table, old, new, _count in plan:
            self.assertTrue(old.startswith("2024年") or old.startswith("2025年"))
            self.assertRegex(new, r"^\d{4}-\d{2}-\d{2}$")

    def test_apply_rewrites_to_iso(self) -> None:
        plan = mnp._plan(self.db)
        per_table = mnp._apply(self.db, plan)
        self.assertEqual(per_table["stock_holdings"], 4)
        self.assertEqual(per_table["bond_holdings"], 3)

        with sqlite3.connect(str(self.db)) as conn:
            stock_periods = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT report_period FROM stock_holdings"
                ).fetchall()
            }
            bond_periods = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT report_period FROM bond_holdings"
                ).fetchall()
            }
        # Every long-form label has been replaced.
        self.assertFalse(any(p.startswith("2024年") for p in stock_periods))
        self.assertFalse(any(p.startswith("2025年") for p in stock_periods))
        self.assertFalse(any(p.startswith("2024年") for p in bond_periods))
        self.assertFalse(any(p.startswith("2025年") for p in bond_periods))
        # The ISO labels match the plan.
        expected_stock = {"2024-03-31", "2024-12-31", "2024-06-30", "2025-09-30"}
        expected_bond = {"2024-03-31", "2024-12-31", "2025-06-30"}
        self.assertEqual(stock_periods, expected_stock)
        self.assertEqual(bond_periods, expected_bond)

    def test_apply_is_idempotent(self) -> None:
        plan = mnp._plan(self.db)
        mnp._apply(self.db, plan)
        # A second plan should now be empty (no long-form rows left).
        plan_again = mnp._plan(self.db)
        self.assertEqual(plan_again, [])
        # And a second apply should be a no-op.
        per_table_again = mnp._apply(self.db, plan_again)
        self.assertEqual(per_table_again["stock_holdings"], 0)
        self.assertEqual(per_table_again["bond_holdings"], 0)

    def test_industry_allocations_is_never_touched(self) -> None:
        # The migration script's TARGET_TABLES must not include
        # ``industry_allocations`` -- it has been ISO since
        # day one, and an accidental UPDATE there would destroy
        # the quarter-end alignment.  The integration test seeds
        # one ISO row and asserts it survives.
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute(
                "INSERT INTO industry_allocations"
                "(fund_code, report_period, industry_name, net_value_ratio, "
                "market_value, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "000001", "2024-12-31", "制造业", 0.5,
                    1000.0, "akshare.test", "2026-06-02T00:00:00+00:00",
                ),
            )
            conn.commit()
        plan = mnp._plan(self.db)
        mnp._apply(self.db, plan)
        with sqlite3.connect(str(self.db)) as conn:
            row = conn.execute(
                "SELECT report_period FROM industry_allocations WHERE fund_code = '000001'"
            ).fetchone()
        self.assertEqual(row[0], "2024-12-31")


if __name__ == "__main__":
    unittest.main()
