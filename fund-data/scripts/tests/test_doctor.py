import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys_path = SCRIPT_DIR  # fund-data/ so "scripts" is a package
import sys

sys.path.insert(0, str(sys_path))

from scripts import doctor  # noqa: E402


class CheckDbTests(unittest.TestCase):
    def test_missing_db_reports_ok_false(self):
        result = doctor._check_db(Path("/nonexistent/path.sqlite"))
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["message"])

    def test_existing_db_with_schema_reports_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fund_data.sqlite"
            import sqlite3
            with sqlite3.connect(db) as conn:
                conn.executescript(
                    """
                    CREATE TABLE funds (fund_code TEXT PRIMARY KEY);
                    CREATE TABLE nav_history (fund_code TEXT);
                    CREATE TABLE snapshots (fund_code TEXT);
                    CREATE TABLE raw_responses (source TEXT);
                    CREATE TABLE sync_runs (id INTEGER);
                    CREATE TABLE sync_failures (id INTEGER);
                    CREATE TABLE stock_holdings (fund_code TEXT);
                    CREATE TABLE fund_profiles (fund_code TEXT);
                    CREATE TABLE bond_holdings (fund_code TEXT);
                    CREATE TABLE industry_allocations (fund_code TEXT);
                    CREATE TABLE fee_structures (fund_code TEXT);
                    CREATE TABLE dividends (fund_code TEXT);
                    CREATE TABLE splits (fund_code TEXT);
                    CREATE TABLE fund_managers (manager_name TEXT);
                    """
                )
            result = doctor._check_db(db)
            self.assertTrue(result["ok"])


class CheckAkShareTests(unittest.TestCase):
    def test_venv_missing_reports_error(self):
        result = doctor._check_akshare(Path("/nonexistent/.venv-akshare"))
        self.assertFalse(result["ok"])
        self.assertIn("not installed", result["message"])
        self.assertIn("install", result["hint"])

    def test_disabled_env_var_short_circuits(self):
        import os
        old = os.environ.get("FUND_DATA_DISABLE_AKSHARE")
        os.environ["FUND_DATA_DISABLE_AKSHARE"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                venv = Path(tmp) / ".venv-akshare"
                (venv / "bin").mkdir(parents=True)
                (venv / "bin" / "python").touch()
                result = doctor._check_akshare(venv)
                self.assertTrue(result["ok"])
                self.assertIn("disabled", result["message"])
        finally:
            if old is None:
                os.environ.pop("FUND_DATA_DISABLE_AKSHARE", None)
            else:
                os.environ["FUND_DATA_DISABLE_AKSHARE"] = old


class CheckEastMoneyTests(unittest.TestCase):
    def test_returns_ok_on_200(self):
        from unittest.mock import MagicMock

        fake = MagicMock()
        fake.__enter__.return_value.status = 200
        with patch("scripts.doctor.urllib.request.urlopen", return_value=fake):
            result = doctor._check_eastmoney()
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], 200)

    def test_returns_failure_on_exception(self):
        with patch("scripts.doctor.urllib.request.urlopen", side_effect=OSError("network down")):
            result = doctor._check_eastmoney()
            self.assertFalse(result["ok"])
            self.assertIn("network down", result["message"])


class CheckSyncFailuresTests(unittest.TestCase):
    def test_no_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "test.sqlite"
            import sqlite3
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE sync_failures (id INTEGER)")
            result = doctor._check_sync_failures(db)
            self.assertTrue(result["ok"])
            self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
