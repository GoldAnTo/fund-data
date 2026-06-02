"""Unit tests for ``scripts/refresh_fund_type.py``.

The script has two passes:

1. The canonical Eastmoney refresh -- re-fetch
   ``fundcode_search.js`` and overwrite ``funds.fund_type`` for
   every fund whose row the index carries a non-empty type for.
2. A regex-on-fund_name fallback -- for the small tail (18 funds
   in the 2026-06-02 baseline) whose Eastmoney row has an empty
   ``fund_type`` but a real ``fund_name`` we can read, infer the
   type from the name.

These tests cover the regex helper in isolation, the fallback
DB apply path against a real SQLite file, and the regression
guards (the no-op for back-end share classes whose fund_name
is just the fund_code).
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

import refresh_fund_type  # noqa: E402


class InferFundTypeFromNameTests(unittest.TestCase):
    """The regex helper. Verifies the 18-row 2026-06-02 baseline
    and a handful of edge cases."""

    def test_returns_empty_string_for_empty_or_none_name(self) -> None:
        self.assertEqual(refresh_fund_type.infer_fund_type_from_name(""), "")
        self.assertEqual(refresh_fund_type.infer_fund_type_from_name("   "), "")

    def test_returns_empty_string_when_no_keyword_matches(self) -> None:
        # 启航 / 价值 / 启明 -- marketing verbs, not fund-type
        # vocabulary. The caller should leave the fund_type alone.
        self.assertEqual(refresh_fund_type.infer_fund_type_from_name("东财价值启航A"), "")
        self.assertEqual(refresh_fund_type.infer_fund_type_from_name("启航回报混合"), "混合型-灵活")

    def test_2026_06_02_baseline_18_fund_name_mappings(self) -> None:
        """The exact 18 names that were left empty after the
        2026-06-02 Eastmoney refresh. Two of them (东财价值启航A/C)
        have no fund-type keyword in the name and the helper must
        return '' for them; the other 16 land on canonical
        fund_type values."""
        cases = [
            ("国联安鑫稳3个月持有混合A", "混合型-灵活"),
            ("国联安鑫稳3个月持有混合C", "混合型-灵活"),
            ("鹏华汽车产业混合发起式A", "混合型-灵活"),
            ("鹏华汽车产业混合发起式C", "混合型-灵活"),
            ("创金合信荣和积极养老目标五年持有期混合发起(FOF)", "FOF-稳健型"),
            ("东财价值启航A", ""),
            ("东财价值启航C", ""),
            ("国寿安保尊悦纯债债券A", "债券型-长债"),
            ("国寿安保尊悦纯债债券C", "债券型-长债"),
            ("国泰海通稳健欣享债券A", "债券型-长债"),
            ("国泰海通稳健欣享债券C", "债券型-长债"),
            ("宝盈裕安增利6个月持有期债券A", "债券型-长债"),
            ("宝盈裕安增利6个月持有期债券C", "债券型-长债"),
            ("平安合聚定开债C", "债券型-长债"),
            ("大成中证红利低波动100ETF发起式联接A", "指数型-股票"),
            ("大成中证红利低波动100ETF发起式联接C", "指数型-股票"),
            ("嘉实恒生科技ETF发起联接(QDII)A", "QDII-混合偏股"),
            ("嘉实恒生科技ETF发起联接(QDII)C", "QDII-混合偏股"),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    refresh_fund_type.infer_fund_type_from_name(name),
                    expected,
                    f"name={name!r}",
                )

    def test_keyword_priority_fof_beats_bare_hybrid(self) -> None:
        """A name carrying both 混合 and (FOF) must classify as
        FOF, not as 混合 -- FOF is a more specific structural
        marker."""
        self.assertEqual(
            refresh_fund_type.infer_fund_type_from_name("某某养老混合(FOF)"),
            "FOF-稳健型",
        )

    def test_keyword_priority_qdii_beats_etf(self) -> None:
        """A name carrying both ETF and QDII must classify as
        QDII -- QDII is the regulatory bucket, ETF is the
        structure."""
        self.assertEqual(
            refresh_fund_type.infer_fund_type_from_name("某某跨境ETF(QDII)"),
            "QDII-混合偏股",
        )


class ApplyNameFallbackTests(unittest.TestCase):
    """The DB apply path. Uses a real SQLite file (in a tempdir)
    so the SQL semantics are exactly what production sees."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fund_data.sqlite"
        # Bootstrap the funds table via the real FundDataStore so
        # the schema matches production (PRAGMA user_version,
        # schema_migrations row, ...).
        fund_data.FundDataStore(str(self.db)).ensure_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _insert_fund(
        self, fund_code: str, fund_name: str, fund_type: str = ""
    ) -> None:
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute(
                "INSERT INTO funds(fund_code, fund_name, fund_type, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (fund_code, fund_name, fund_type, "2026-01-01T00:00:00+00:00"),
            )
            conn.commit()

    def _type_for(self, fund_code: str) -> str:
        with sqlite3.connect(str(self.db)) as conn:
            row = conn.execute(
                "SELECT fund_type FROM funds WHERE fund_code = ?", (fund_code,)
            ).fetchone()
        return row[0] if row else None

    def test_fills_18_baseline_funds_and_skips_no_name_rows(self) -> None:
        # Two unmappable funds (no keyword): 东财价值启航A/C
        for fund_code, fund_name in [
            ("010817", "国联安鑫稳3个月持有混合A"),
            ("010818", "国联安鑫稳3个月持有混合C"),
            ("017218", "鹏华汽车产业混合发起式A"),
            ("017219", "鹏华汽车产业混合发起式C"),
            ("017728", "创金合信荣和积极养老目标五年持有期混合发起(FOF)"),
            ("018096", "东财价值启航A"),
            ("018097", "东财价值启航C"),
            ("023694", "国寿安保尊悦纯债债券A"),
            ("023695", "国寿安保尊悦纯债债券C"),
            ("027428", "国泰海通稳健欣享债券A"),
            ("027429", "国泰海通稳健欣享债券C"),
            ("027660", "宝盈裕安增利6个月持有期债券A"),
            ("027661", "宝盈裕安增利6个月持有期债券C"),
            ("027679", "平安合聚定开债C"),
            ("027721", "大成中证红利低波动100ETF发起式联接A"),
            ("027722", "大成中证红利低波动100ETF发起式联接C"),
            ("027765", "嘉实恒生科技ETF发起联接(QDII)A"),
            ("027766", "嘉实恒生科技ETF发起联接(QDII)C"),
        ]:
            self._insert_fund(fund_code, fund_name)

        # Plus the 190 back-end share classes that have no fund_name.
        # The script must skip them as no_name without raising.
        for code in ["000002", "000012", "000108", "000140", "000154"]:
            self._insert_fund(code, code)  # fund_name == fund_code

        stats = refresh_fund_type._apply_name_fallback(self.db, dry_run=False)
        self.assertEqual(stats["updated"], 16)
        self.assertEqual(stats["skipped"], 2)  # 启航A/C
        self.assertEqual(stats["no_name"], 5)  # back-end share classes

        # Spot-check the inferred values.
        self.assertEqual(self._type_for("010817"), "混合型-灵活")
        self.assertEqual(self._type_for("017728"), "FOF-稳健型")
        self.assertEqual(self._type_for("023694"), "债券型-长债")
        self.assertEqual(self._type_for("027721"), "指数型-股票")
        self.assertEqual(self._type_for("027765"), "QDII-混合偏股")
        # Unmappable stays empty.
        self.assertEqual(self._type_for("018096"), "")
        # Back-end shares stay empty.
        self.assertEqual(self._type_for("000002"), "")

    def test_dry_run_does_not_write(self) -> None:
        self._insert_fund("010817", "国联安鑫稳3个月持有混合A")
        stats = refresh_fund_type._apply_name_fallback(self.db, dry_run=True)
        self.assertEqual(stats["updated"], 1)
        self.assertEqual(self._type_for("010817"), "")

    def test_does_not_clobber_existing_fund_type(self) -> None:
        # A fund that already has a real fund_type must not be
        # touched even if the regex would have set a different
        # value. (The WHERE clause filters those out, but the
        # test is the regression guard.)
        self._insert_fund("010817", "国联安鑫稳3个月持有混合A", "指数型-股票")
        stats = refresh_fund_type._apply_name_fallback(self.db, dry_run=False)
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(self._type_for("010817"), "指数型-股票")

    def test_writes_sync_runs_audit_row(self) -> None:
        self._insert_fund("010817", "国联安鑫稳3个月持有混合A")
        refresh_fund_type._apply_name_fallback(self.db, dry_run=False)
        with sqlite3.connect(str(self.db)) as conn:
            row = conn.execute(
                "SELECT operation, status, rows_changed, message "
                "FROM sync_runs WHERE operation = 'refresh_fund_type.name_fallback'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "refresh_fund_type.name_fallback")
        self.assertEqual(row[1], "ok")
        self.assertEqual(row[2], 1)
        # The message JSON must include the no_name / skipped / dry_run
        # counters so an operator can spot the 190-share-classes
        # tail without re-querying the DB.
        import json
        message = json.loads(row[3])
        self.assertIn("no_name", message)
        self.assertIn("skipped", message)
        self.assertIn("dry_run", message)


if __name__ == "__main__":
    unittest.main()
