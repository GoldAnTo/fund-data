import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys_path = SCRIPT_DIR
import sys
sys.path.insert(0, str(sys_path))

from scripts import retry_failures  # noqa: E402


def _make_db(path: Path, failures: list[tuple[str, str]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE sync_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT,
                operation TEXT,
                fund_code TEXT,
                provider TEXT,
                message TEXT,
                failed_at TEXT
            );
            """
        )
        for code, message in failures:
            conn.execute(
                "INSERT INTO sync_failures(operation, fund_code, provider, message, failed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("batch-sync", code, "auto", message, "2026-06-01T00:00:00+00:00"),
            )


class LoadFailuresTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_codes_sorted_by_fund_code(self):
        _make_db(self.db, [("110022", "boom"), ("000001", "boom"), ("510300", "boom")])
        codes = retry_failures._load_failed_codes(self.db)
        # All failures share the same failed_at in this test, so the
        # secondary sort key (fund_code) wins -> alphabetical order.
        self.assertEqual(codes, ["000001", "110022", "510300"])

    def test_dedupes_repeated_codes(self):
        _make_db(self.db, [("110022", "boom1"), ("110022", "boom2"), ("000001", "boom")])
        codes = retry_failures._load_failed_codes(self.db)
        self.assertEqual(codes, ["000001", "110022"])

    def test_empty_when_no_failures(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE sync_failures (id INTEGER, fund_code TEXT)")
        codes = retry_failures._load_failed_codes(self.db)
        self.assertEqual(codes, [])

    def test_missing_table_returns_empty(self):
        codes = retry_failures._load_failed_codes(self.db)
        self.assertEqual(codes, [])


class RetryFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "test.sqlite"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_no_failures_returns_zero_summary(self):
        result = retry_failures.retry(db_path=self.db)
        self.assertEqual(result["retried"], 0)
        self.assertEqual(result["ok"], 0)
        self.assertEqual(result["failed"], 0)

    def test_routes_through_batch_sync_funds(self):
        _make_db(self.db, [("110022", "boom"), ("000001", "boom")])
        with patch.object(retry_failures.fund_data, "batch_sync_funds") as mock_batch:
            mock_batch.return_value = {
                "batch_id": "retry-test",
                "total": 2,
                "ok": 1,
                "failed": 1,
                "concurrency": 1,
                "min_interval_seconds": 1.0,
                "results": [
                    {"fund_code": "110022", "status": "ok"},
                    {"fund_code": "000001", "status": "error", "message": "still broken"},
                ],
                "coverage": [],
            }
            result = retry_failures.retry(db_path=self.db, provider="eastmoney", concurrency=4)
        mock_batch.assert_called_once()
        codes_arg = mock_batch.call_args.args[0]
        self.assertEqual(codes_arg, ["000001", "110022"])
        self.assertEqual(result["retried"], 2)
        self.assertEqual(result["ok"], 1)
        self.assertEqual(result["failed"], 1)

    def test_limit_caps_the_number_of_codes(self):
        codes = [f"{i:06d}" for i in range(20)]
        _make_db(self.db, [(c, "x") for c in codes])
        with patch.object(retry_failures.fund_data, "batch_sync_funds") as mock_batch:
            mock_batch.return_value = {
                "batch_id": "x", "total": 5, "ok": 5, "failed": 0,
                "concurrency": 1, "min_interval_seconds": 1.0,
                "results": [], "coverage": [],
            }
            retry_failures.retry(db_path=self.db, limit=5)
        self.assertEqual(len(mock_batch.call_args.args[0]), 5)


if __name__ == "__main__":
    unittest.main()
