import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys_path = SCRIPT_DIR  # fund-data/ so "scripts" is a package
import sys

sys.path.insert(0, str(sys_path))

from scripts import backfill  # noqa: E402


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE funds (
                fund_code TEXT PRIMARY KEY,
                fund_name TEXT,
                fund_type TEXT
            );
            """)
        rows = [
            ("110022", "易方达消费", "股票型"),
            ("000001", "华夏成长", "混合型-偏股"),
            ("000003", "华夏现金", "货币型-普通货币"),
            ("000009", "易方达货币", "货币型-普通货币"),
            ("000015", "华夏纯债", "债券型-长债"),
            ("510300", "沪深300ETF", "指数型-股票"),
        ]
        conn.executemany("INSERT INTO funds VALUES (?, ?, ?)", rows)


class LoadFundsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite"
        _make_db(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_all_when_no_filter(self):
        rows = backfill._load_funds(
            self.db, include_types=None, exclude_types=None, skip_optional_for_currency=True
        )
        codes = [c for c, _ in rows]
        self.assertEqual(codes, ["000001", "000003", "000009", "000015", "110022", "510300"])

    def test_exclude_type_filters_out_matching_substring(self):
        rows = backfill._load_funds(
            self.db, include_types=None, exclude_types=["货币"], skip_optional_for_currency=True
        )
        codes = [c for c, _ in rows]
        self.assertNotIn("000003", codes)
        self.assertNotIn("000009", codes)
        self.assertIn("110022", codes)

    def test_include_type_requires_substring(self):
        rows = backfill._load_funds(
            self.db, include_types=["指数"], exclude_types=None, skip_optional_for_currency=True
        )
        codes = [c for c, _ in rows]
        self.assertEqual(codes, ["510300"])


class ResolveIncludeFlagsTests(unittest.TestCase):
    def test_currency_skips_optional_when_enabled(self):
        flags = backfill._resolve_include_flags(
            "货币型-普通货币", always_include_all=False, skip_optional_for_currency=True
        )
        self.assertFalse(flags["include_holdings"])
        self.assertFalse(flags["include_bonds"])
        self.assertFalse(flags["include_industries"])
        self.assertFalse(flags["include_fees"])
        self.assertTrue(flags["include_profile"])
        self.assertTrue(flags["include_managers"])

    def test_equity_includes_all_when_enabled(self):
        flags = backfill._resolve_include_flags(
            "股票型", always_include_all=False, skip_optional_for_currency=True
        )
        self.assertTrue(flags["include_holdings"])
        self.assertTrue(flags["include_bonds"])
        self.assertTrue(flags["include_industries"])
        self.assertTrue(flags["include_fees"])

    def test_skip_currency_disabled_returns_all_for_currency_fund(self):
        flags = backfill._resolve_include_flags(
            "货币型-普通货币", always_include_all=False, skip_optional_for_currency=False
        )
        self.assertTrue(flags["include_holdings"])
        self.assertTrue(flags["include_bonds"])


class StatePersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite"
        _make_db(self.db)
        self.state = Path(self.tmp.name) / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_load_state_creates_empty_when_missing(self):
        state = backfill._load_state(self.state)
        self.assertEqual(state["completed_codes"], [])
        self.assertEqual(state["failed_codes"], [])

    def test_save_state_roundtrip(self):
        state = backfill._load_state(self.state)
        state["completed_codes"].append("110022")
        backfill._save_state(self.state, state)
        reloaded = backfill._load_state(self.state)
        self.assertEqual(reloaded["completed_codes"], ["110022"])
        self.assertIn("updated_at", reloaded)


class BackfillFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite"
        _make_db(self.db)
        self.state = Path(self.tmp.name) / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_backfill_skips_completed_codes_on_resume(self):
        # Pre-mark one code as completed.
        state = backfill._load_state(self.state)
        state["completed_codes"].append("000001")
        backfill._save_state(self.state, state)

        with patch.object(backfill.fund_data, "batch_sync_funds") as mock_batch:
            mock_batch.return_value = {
                "batch_id": "test",
                "total": 1,
                "ok": 1,
                "failed": 0,
                "concurrency": 1,
                "min_interval_seconds": 0.25,
                "results": [{"fund_code": "110022", "status": "ok"}],
                "coverage": [],
            }
            summary = backfill.backfill(
                db_path=self.db,
                state_path=self.state,
                include_types=None,
                exclude_types=["货币"],
                skip_optional_for_currency=True,
                start_date="2024-01-01",
                end_date=None,
                report_year="2024",
                fee_indicators=None,
                concurrency=1,
                batch_size=100,
                max_funds=None,
                min_interval_seconds=None,
                reset=False,
            )

        # 000001 was already done; only 4 codes (excluding 货币) are pending.
        # We assert that batch_sync_funds was called with codes that do NOT
        # include 000001, and that the summary reflects the skip.
        called_codes = set()
        for call in mock_batch.call_args_list:
            called_codes.update(call.args[0])
        self.assertNotIn("000001", called_codes)
        self.assertIn("110022", called_codes)
        self.assertGreater(summary["completed"], 0)

    def test_backfill_writes_failed_codes(self):
        with patch.object(backfill.fund_data, "batch_sync_funds") as mock_batch:
            mock_batch.return_value = {
                "batch_id": "test",
                "total": 1,
                "ok": 0,
                "failed": 1,
                "concurrency": 1,
                "min_interval_seconds": 0.25,
                "results": [{"fund_code": "110022", "status": "error", "message": "boom"}],
                "coverage": [],
            }
            backfill.backfill(
                db_path=self.db,
                state_path=self.state,
                include_types=None,
                exclude_types=["货币"],
                skip_optional_for_currency=True,
                start_date="2024-01-01",
                end_date=None,
                report_year="2024",
                fee_indicators=None,
                concurrency=1,
                batch_size=100,
                max_funds=None,
                min_interval_seconds=None,
                reset=False,
            )
        reloaded = backfill._load_state(self.state)
        self.assertIn("110022", reloaded["failed_codes"])

    def test_reset_clears_state(self):
        # Write some state, then call with reset=True and assert it is cleared.
        state = backfill._load_state(self.state)
        state["completed_codes"].append("000001")
        backfill._save_state(self.state, state)

        with patch.object(backfill.fund_data, "batch_sync_funds") as mock_batch:
            mock_batch.return_value = {
                "batch_id": "test",
                "total": 0,
                "ok": 0,
                "failed": 0,
                "concurrency": 1,
                "min_interval_seconds": 0.25,
                "results": [],
                "coverage": [],
            }
            backfill.backfill(
                db_path=self.db,
                state_path=self.state,
                include_types=None,
                exclude_types=["货币"],
                skip_optional_for_currency=True,
                start_date="2024-01-01",
                end_date=None,
                report_year="2024",
                fee_indicators=None,
                concurrency=1,
                batch_size=100,
                max_funds=None,
                min_interval_seconds=None,
                reset=True,
            )
        reloaded = backfill._load_state(self.state)
        self.assertEqual(reloaded["completed_codes"], [])


if __name__ == "__main__":
    unittest.main()
