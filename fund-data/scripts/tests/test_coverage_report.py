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
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            md = coverage_report._format_coverage_markdown(Path(tmp) / "absent.sqlite", rows)
        self.assertIn("# fund-data coverage report", md)
        self.assertIn("| Dataset | Present | Coverage |", md)
        self.assertIn("110022", md)

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
                    }
                ]
            )
        )
        self.assertIn("rows", payload)
        self.assertEqual(payload["count"], 1)


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
