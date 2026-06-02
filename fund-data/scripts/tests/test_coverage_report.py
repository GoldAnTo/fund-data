"""Unit tests for ``scripts/coverage_report.py``.

The script is mostly a thin renderer on top of
:func:`fund_data.coverage_report`, so we focus on the local
helpers: stable Markdown / JSON shape, the stale query against a
synthetic SQLite fixture, and the empty-data-base path.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import coverage_report  # noqa: E402

import fund_data  # noqa: E402


def _make_fixture_db() -> str:
    """Build a minimal SQLite with the tables ``coverage_report`` queries."""
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        path = f.name
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE funds (
            fund_code TEXT PRIMARY KEY,
            fund_name TEXT,
            fund_type TEXT
        );
        CREATE TABLE snapshots (
            fund_code TEXT,
            fetched_at TEXT
        );
        CREATE TABLE nav_history (
            fund_code TEXT,
            fetched_at TEXT
        );
        CREATE TABLE fund_profiles (fund_code TEXT);
        CREATE TABLE stock_holdings (fund_code TEXT);
        CREATE TABLE bond_holdings (fund_code TEXT);
        CREATE TABLE industry_allocations (fund_code TEXT);
        CREATE TABLE fee_structures (fund_code TEXT);
        CREATE TABLE dividends (fund_code TEXT);
        CREATE TABLE splits (fund_code TEXT);
        """)
    now = datetime.now(UTC).isoformat()
    old = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    conn.executemany(
        "INSERT INTO funds(fund_code, fund_name, fund_type) VALUES (?, ?, ?)",
        [
            ("110022", "易方达消费", "股票型"),
            ("000001", "华夏成长", "混合型-偏股"),
            ("000002", "Stale Fund", "货币型"),
        ],
    )
    # Fund 110022: snapshot + nav, recently refreshed.
    conn.execute("INSERT INTO snapshots(fund_code, fetched_at) VALUES (?, ?)", ("110022", now))
    conn.execute("INSERT INTO nav_history(fund_code, fetched_at) VALUES (?, ?)", ("110022", now))
    # Fund 000001: snapshot + nav, but the snapshot is older than the
    # stale threshold.
    conn.execute("INSERT INTO snapshots(fund_code, fetched_at) VALUES (?, ?)", ("000001", old))
    conn.execute("INSERT INTO nav_history(fund_code, fetched_at) VALUES (?, ?)", ("000001", now))
    # Fund 000002: no snapshot / no nav at all.
    conn.commit()
    conn.close()
    return path


class StaleRowsTests(unittest.TestCase):
    def test_returns_empty_when_db_missing(self) -> None:
        self.assertEqual(
            coverage_report._stale_rows(Path("/nope.sqlite"), max_age_hours=1.0, limit=10), []
        )

    def test_flags_stale_and_missing(self) -> None:
        db_path = _make_fixture_db()
        try:
            rows = coverage_report._stale_rows(Path(db_path), max_age_hours=24.0, limit=10)
        finally:
            Path(db_path).unlink()
        codes = [r["fund_code"] for r in rows]
        # 000001 has a snapshot older than 24h, 000002 has no snapshot at all.
        # 110022 was refreshed just now and must NOT appear.
        self.assertIn("000001", codes)
        self.assertIn("000002", codes)
        self.assertNotIn("110022", codes)

    def test_respects_limit(self) -> None:
        db_path = _make_fixture_db()
        try:
            rows = coverage_report._stale_rows(Path(db_path), max_age_hours=24.0, limit=1)
        finally:
            Path(db_path).unlink()
        self.assertEqual(len(rows), 1)


class FormatCoverageTests(unittest.TestCase):
    def test_markdown_includes_dataset_table(self) -> None:
        rows = [
            {
                "fund_code": "110022",
                "fund_name": "易方达消费",
                "fund_type": "股票型",
                "has_profile": 1,
                "nav_rows": 200,
                "stock_holding_rows": 50,
                "bond_holding_rows": 0,
                "industry_rows": 20,
                "fee_rows": 5,
                "dividend_rows": 3,
                "split_rows": 0,
                "completeness": 0.75,
                "missing": ["bond_holdings", "splits"],
                "actionable_missing": ["bond_holdings", "splits"],
                "structural_empty": [],
                "adjusted_completeness": 0.75,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            md = coverage_report._format_coverage_markdown(Path(tmp) / "absent.sqlite", rows)
        self.assertIn("# fund-data coverage report", md)
        # The new schema is split into actionable + structural so an
        # agent / reader can tell 49% "missing stock" apart from
        # 49% "stock is structural for 货币型". The plain
        # "Coverage" column would be a regression.
        self.assertIn("| Dataset | Present | Actionable missing | Structural empty |", md)
        self.assertIn("110022", md)
        # The matrix itself ships in the markdown so the reader
        # does not have to cross-reference docs/fund-data-inventory.
        self.assertIn("Structural-empty matrix", md)
        self.assertIn("货币型", md)

    def test_markdown_handles_empty_rows(self) -> None:
        md = coverage_report._format_coverage_markdown(Path("/absent.sqlite"), [])
        self.assertIn("No rows match the filter", md)

    def test_json_is_parseable_and_contains_rows(self) -> None:
        rows = fund_data.coverage_report  # touch import to fail loudly if missing
        self.assertTrue(callable(rows))
        payload = json.loads(
            coverage_report._format_coverage_json(
                [
                    {
                        "fund_code": "110022",
                        "fund_name": "易方达消费",
                        "fund_type": "股票型",
                        "completeness": 0.5,
                        "missing": ["splits"],
                        "actionable_missing": ["splits"],
                        "structural_empty": [],
                        "adjusted_completeness": 0.5,
                    }
                ]
            )
        )
        self.assertIn("rows", payload)
        self.assertEqual(payload["count"], 1)

    def test_markdown_renders_structural_suffix_for_currency_fund(self) -> None:
        # A 货币型 fund missing stock_holdings / industries must
        # land in the structural column, not actionable. The
        # markdown table should not show it as "needs backfill".
        rows = [
            {
                "fund_code": "000002",
                "fund_name": "Stale Fund",
                "fund_type": "货币型",
                "has_profile": 1,
                "nav_rows": 100,
                "stock_holding_rows": 0,
                "bond_holding_rows": 90,
                "industry_rows": 0,
                "fee_rows": 5,
                "dividend_rows": 0,
                "split_rows": 0,
                "completeness": 0.5,
                "actionable_missing": [],
                "structural_empty": ["stock_holdings", "industry"],
                "adjusted_completeness": 1.0,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            md = coverage_report._format_coverage_markdown(Path(tmp) / "absent.sqlite", rows)
        # Adjusted completeness is 100% for this row; the "Most
        # incomplete sample" header should not even mention a
        # 0%-adjusted fund. (The renderer formats it as "100%".)
        self.assertIn("100%", md)
        # The dataset table shows 货币型's stock_holdings / industries
        # as structural, not actionable.
        self.assertIn("| stock_holdings |", md)
        self.assertIn("| industry |", md)


class IsStructuralEmptyTests(unittest.TestCase):
    """Pin the fund_type × dataset matrix so a refactor that
    accidentally drops a fund_type or a dataset immediately
    surfaces in CI rather than as silent 49% inflation in
    production coverage numbers."""

    def test_currency_fund(self) -> None:
        self.assertTrue(coverage_report._is_structural_empty("货币型", "stock_holdings"))
        self.assertTrue(coverage_report._is_structural_empty("货币型", "industry"))
        # 货币型 does have bond_holdings
        self.assertFalse(coverage_report._is_structural_empty("货币型", "bond_holdings"))
        # 货币型 does have fees
        self.assertFalse(coverage_report._is_structural_empty("货币型", "fees"))

    def test_bond_fund(self) -> None:
        self.assertTrue(coverage_report._is_structural_empty("债券型", "stock_holdings"))
        self.assertTrue(coverage_report._is_structural_empty("债券型", "industry"))
        self.assertFalse(coverage_report._is_structural_empty("债券型", "bond_holdings"))

    def test_index_gu_shou_subtype(self) -> None:
        # The more specific key wins over the broader "指数型"
        # key for stock/industries; bond_holdings is structurally
        # expected for the 固收 subtype.
        self.assertTrue(coverage_report._is_structural_empty("指数型-固收", "stock_holdings"))
        self.assertTrue(coverage_report._is_structural_empty("指数型-固收", "industry"))

    def test_fof(self) -> None:
        # FOF holds other funds, not direct equity / bond / industry.
        for ds in ("stock_holdings", "bond_holdings", "industry"):
            self.assertTrue(coverage_report._is_structural_empty("FOF", ds))

    def test_reits(self) -> None:
        for ds in ("stock_holdings", "bond_holdings", "industry"):
            self.assertTrue(coverage_report._is_structural_empty("REITs", ds))

    def test_qdii_industry_only(self) -> None:
        # QDII can still surface stock_holdings (7% per inventory)
        # but industry allocation is structurally absent in CN schemas.
        self.assertTrue(coverage_report._is_structural_empty("QDII", "industry"))
        self.assertFalse(coverage_report._is_structural_empty("QDII", "stock_holdings"))

    def test_unknown_fund_type_is_never_structural_empty(self) -> None:
        # Conservative: unknown / blank fund_type surfaces the
        # gap rather than hiding it under "structural".
        self.assertFalse(coverage_report._is_structural_empty(None, "stock_holdings"))
        self.assertFalse(coverage_report._is_structural_empty("", "stock_holdings"))
        self.assertFalse(coverage_report._is_structural_empty("(unknown)", "stock_holdings"))

    def test_hybrid_fund_is_never_structural_empty(self) -> None:
        # 混合型 holds everything, nothing is structural-empty.
        for ds in ("stock_holdings", "bond_holdings", "industry", "splits", "dividends"):
            self.assertFalse(coverage_report._is_structural_empty("混合型", ds))


class ClassifyMissingTests(unittest.TestCase):
    def test_splits_missing_list_split_actionable_vs_structural(self) -> None:
        actionable, structural = coverage_report._classify_missing(
            "货币型", ["stock_holdings", "industry", "dividends"]
        )
        self.assertEqual(actionable, ["dividends"])
        self.assertEqual(structural, ["stock_holdings", "industry"])

    def test_unknown_fund_type_keeps_everything_actionable(self) -> None:
        actionable, structural = coverage_report._classify_missing(
            "(unknown)", ["stock_holdings", "industry"]
        )
        self.assertEqual(actionable, ["stock_holdings", "industry"])
        self.assertEqual(structural, [])


class AdjustedDenominatorTests(unittest.TestCase):
    def test_currency_fund_excludes_two(self) -> None:
        # 货币型: stock_holdings + industries are structural, so the
        # denominator drops from 8 to 6.
        self.assertEqual(coverage_report._adjusted_denominator("货币型"), 6)

    def test_fof_excludes_three(self) -> None:
        # FOF: stock + bond + industry = 3 structural
        self.assertEqual(coverage_report._adjusted_denominator("FOF"), 5)

    def test_reits_excludes_three(self) -> None:
        self.assertEqual(coverage_report._adjusted_denominator("REITs"), 5)

    def test_hybrid_fund_keeps_eight(self) -> None:
        # 混合型 has no structural empties — denominator is 8.
        self.assertEqual(coverage_report._adjusted_denominator("混合型"), 8)

    def test_unknown_fund_type_keeps_eight(self) -> None:
        self.assertEqual(coverage_report._adjusted_denominator(None), 8)


class CoverageRowsEnrichmentTests(unittest.TestCase):
    """End-to-end check that _coverage_rows adds the three new
    fields (actionable_missing, structural_empty,
    adjusted_completeness) on top of the raw fund_data output.

    Stub fund_data.coverage_report so the test does not need a
    real SQLite — _coverage_rows' value-add is the post-process,
    not the SQL."""

    def test_currency_fund_adjusted_to_one(self) -> None:
        raw = [
            {
                "fund_code": "000002",
                "fund_name": "Stale Fund",
                "fund_type": "货币型",
                "has_profile": 1,
                "nav_rows": 100,
                "stock_holding_rows": 0,
                "bond_holding_rows": 90,
                "industry_rows": 0,
                "fee_rows": 5,
                "dividend_rows": 0,
                "split_rows": 0,
                "completeness": 0.5,
                "missing": ["stock_holdings", "industry"],
            }
        ]
        with unittest.mock.patch.object(
            coverage_report.fund_data, "coverage_report", return_value=raw
        ):
            enriched = coverage_report._coverage_rows(
                Path("/nope.sqlite"),
                only_incomplete=False,
                fund_type=None,
                limit=None,
            )
        self.assertEqual(len(enriched), 1)
        row = enriched[0]
        # structural empties must be split out, not left in missing
        self.assertEqual(row["actionable_missing"], [])
        self.assertEqual(row["structural_empty"], ["stock_holdings", "industry"])
        # canonical missing == actionable for downstream consumers
        self.assertEqual(row["missing"], [])
        # 6/6 present = 100% adjusted
        self.assertEqual(row["adjusted_completeness"], 1.0)
        # raw completeness unchanged (backward compat)
        self.assertEqual(row["completeness"], 0.5)

    def test_hybrid_fund_keeps_missing_in_actionable(self) -> None:
        raw = [
            {
                "fund_code": "110022",
                "fund_name": "易方达消费",
                "fund_type": "混合型-偏股",
                "has_profile": 1,
                "nav_rows": 200,
                "stock_holding_rows": 50,
                "bond_holding_rows": 0,
                "industry_rows": 20,
                "fee_rows": 5,
                "dividend_rows": 3,
                "split_rows": 0,
                "completeness": 0.75,
                "missing": ["bond_holdings", "splits"],
            }
        ]
        with unittest.mock.patch.object(
            coverage_report.fund_data, "coverage_report", return_value=raw
        ):
            enriched = coverage_report._coverage_rows(
                Path("/nope.sqlite"),
                only_incomplete=False,
                fund_type=None,
                limit=None,
            )
        row = enriched[0]
        # 混合型 has no structural empties — everything missing
        # is actionable.
        self.assertEqual(row["actionable_missing"], ["bond_holdings", "splits"])
        self.assertEqual(row["structural_empty"], [])
        # 6/8 = 0.75 (denominator unchanged)
        self.assertEqual(row["adjusted_completeness"], 0.75)


class FormatStaleTests(unittest.TestCase):
    def test_markdown_for_empty_stale_list(self) -> None:
        md = coverage_report._format_stale_markdown(24.0, [])
        self.assertIn("Nothing is stale", md)

    def test_json_shape(self) -> None:
        payload = json.loads(coverage_report._format_stale_json([]))
        self.assertEqual(payload["count"], 0)
        self.assertIn("generated_at", payload)


if __name__ == "__main__":
    unittest.main()
