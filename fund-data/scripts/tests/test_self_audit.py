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

    def test_structural_empty_stock_holdings_are_info_not_actionable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            _seed_fund(store, code="000001", fund_type="货币型")

            result = fund_data.build_self_audit_queue(
                db_path=db_path,
                codes=["000001"],
                include_structural=True,
            )

        item = next(i for i in result["queue"] if i["dataset"] == "stock_holdings")
        self.assertEqual(item["priority"], "P4")
        self.assertEqual(item["issue_type"], "structural_empty")
        self.assertEqual(item["severity"], "info")
        self.assertFalse(item["auto_fill_executed"])

    def test_stale_nav_is_p3(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            _seed_fund(store)
            store.upsert_nav_history("110022", [{"nav_date": "2024-01-01", "unit_nav": 1.0, "source": "test"}])
            with sqlite3.connect(db_path) as conn:
                conn.execute("update nav_history set fetched_at = '2000-01-01T00:00:00+00:00'")

            result = fund_data.build_self_audit_queue(db_path=db_path, codes=["110022"], max_age_hours=1)

        item = next(i for i in result["queue"] if i["dataset"] == "nav_history" and i["issue_type"] == "stale")
        self.assertEqual(item["priority"], "P3")
        self.assertEqual(item["severity"], "notice")

    def test_unknown_code_in_explicit_codes_is_p0_with_bootstrap_action(self):
        """Regression for the Bug 1 / 2026-06-03 audit: when the
        caller passes an explicit ``codes`` list and one of the
        codes is not in the local funds table, the audit must emit
        a P0 entry with a fund_search bootstrap action instead of
        silently returning an empty queue."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            _seed_fund(store)  # 110022 exists

            result = fund_data.build_self_audit_queue(
                db_path=db_path, codes=["110022", "999999"]
            )

        p0 = [item for item in result["queue"] if item["priority"] == "P0"]
        self.assertEqual(len(p0), 1)
        item = p0[0]
        self.assertEqual(item["fund_code"], "999999")
        self.assertEqual(item["dataset"], "funds")
        self.assertEqual(item["issue_type"], "fund_not_in_universe")
        self.assertEqual(item["severity"], "error")
        self.assertEqual(item["recommended_mcp_tool"], "fund_search")
        self.assertEqual(item["recommended_mcp_arguments"], {"keyword": "999999", "limit": 5})
        self.assertIn("search 999999", item["recommended_cli"])
        # total_funds reflects the caller's request (existing + missing)
        self.assertEqual(result["summary"]["total_funds"], 2)
        self.assertEqual(result["summary"]["p0"], 1)

    def test_unknown_code_only_request_returns_p0_only(self):
        """The simplest reproduction: health-check 999999 with an
        otherwise empty DB must return p0=1, queue_size=1."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            fund_data.FundDataStore(db_path)  # empty, no funds
            result = fund_data.check_fund_health("999999", db_path=db_path)
        self.assertEqual(result["summary"]["total_funds"], 1)
        self.assertEqual(result["summary"]["queue_size"], 1)
        self.assertEqual(result["summary"]["p0"], 1)
        self.assertEqual(len(result["queue"]), 1)
        self.assertEqual(result["queue"][0]["issue_type"], "fund_not_in_universe")
