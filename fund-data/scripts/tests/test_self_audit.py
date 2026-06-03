import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402


def _seed_fund(store, code="110022", fund_type="股票型"):
    store.upsert_funds([
        {
            "fund_code": code,
            "fund_name": "易方达消费行业股票",
            "fund_type": fund_type,
            "company": "",
            "manager": "",
            "nav": None,
            "nav_date": "",
            "other_names": "",
            "source": "test",
        }
    ])


class SelfAuditTests(unittest.TestCase):
    def test_missing_profile_is_p1_recommended_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            _seed_fund(store)

            result = fund_data.build_self_audit_queue(db_path=db_path, limit=10)

        item = next(i for i in result["queue"] if i["dataset"] == "fund_profiles")
        self.assertEqual(item["priority"], "P1")
        self.assertEqual(item["issue_type"], "missing")
        self.assertEqual(item["recommended_mcp_tool"], "fund_profile")
        self.assertEqual(item["recommended_mcp_arguments"], {"code": "110022", "refresh": True})
        self.assertFalse(item["auto_fill_executed"])
