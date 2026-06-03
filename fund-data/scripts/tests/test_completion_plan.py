"""Tests for the OpenClaw active-completion plan builder.

The plan builder takes the read-only self-audit queue JSON and converts
it into bounded, executable batch groups while honoring the project
policy (mode, allowed priorities, blocked datasets, budgets).

These tests cover the read-only conversion only -- the runnable side
lives in ``completion.py`` and is tested in ``test_completion_run.py``.
"""

import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402


def _write_queue(tmp: Path, items: list[dict]) -> Path:
    payload = {
        "summary": {
            "total_funds": len({i["fund_code"] for i in items}),
            "queue_size": len(items),
            "returned": len(items),
            "p0": 0,
            "p1": sum(1 for i in items if i["priority"] == "P1"),
            "p2": sum(1 for i in items if i["priority"] == "P2"),
            "p3": sum(1 for i in items if i["priority"] == "P3"),
            "p4": sum(1 for i in items if i["priority"] == "P4"),
            "structural_empty": 0,
            "auto_fill_executed": False,
        },
        "queue": items,
        "batch_suggestions": [],
    }
    queue_path = tmp / "queue.json"
    queue_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return queue_path


def _queue_item(
    code: str,
    dataset: str,
    priority: str = "P1",
    issue_type: str = "missing",
) -> dict:
    return {
        "priority": priority,
        "score": 900,
        "fund_code": code,
        "fund_name": f"测试基金 {code}",
        "fund_type": "股票型",
        "dataset": dataset,
        "issue_type": issue_type,
        "severity": "warning",
        "reason": "test",
        "recommended_mcp_tool": f"fund_{dataset}",
        "recommended_mcp_arguments": {"code": code, "refresh": True},
        "recommended_cli": "fund-data/scripts/fund_cli.py nav 110022 --provider auto",
        "auto_fill_executed": False,
    }


class CompletionPlanTests(unittest.TestCase):
    def test_p1_profile_is_planned_and_p4_splits_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            queue_path = _write_queue(
                tmp,
                [
                    _queue_item("000001", "fund_profiles", "P1"),
                    _queue_item("000002", "fund_profiles", "P1"),
                    _queue_item("110022", "splits", "P4", "naturally_sparse"),
                ],
            )
            config = tmp / "policy.json"
            config.write_text(
                json.dumps(
                    {
                        "mode": "assisted",
                        "allowed_priorities": ["P1", "P2", "P3"],
                        "blocked_datasets": ["dividends", "splits"],
                    }
                ),
                encoding="utf-8",
            )

            plan = fund_data.build_completion_plan(
                queue_path=queue_path, config_path=config
            )

        # assisted mode forbids execution
        self.assertEqual(plan["mode"], "assisted")
        self.assertFalse(plan["allowed_to_execute"])
        self.assertTrue(plan["dry_run"])

        # P1 profile is grouped into one batch
        profile_batches = [b for b in plan["batches"] if b["dataset"] == "fund_profiles"]
        self.assertEqual(len(profile_batches), 1)
        self.assertEqual(sorted(profile_batches[0]["codes"]), ["000001", "000002"])
        self.assertIn("--include-profile", profile_batches[0]["command"])
        # The codes-file path is written next to the queue file by default
        self.assertTrue(profile_batches[0]["codes_file"].endswith(".txt"))
        # batch_id uses the dataset + priority for traceability
        self.assertIn("fund_profiles", profile_batches[0]["batch_id"])
        self.assertIn("p1", profile_batches[0]["batch_id"])

        # P4 splits is reported as blocked, not planned
        self.assertEqual(plan["batches"], profile_batches)
        blocked = plan["blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertEqual(blocked[0]["fund_code"], "110022")
        self.assertEqual(blocked[0]["dataset"], "splits")
        self.assertIn("blocked", blocked[0]["reason"].lower())

        # provider call estimate = sum of planned codes
        self.assertEqual(plan["summary"]["planned_items"], 2)
        self.assertEqual(plan["summary"]["estimated_provider_calls"], 2)
        self.assertGreater(plan["summary"]["estimated_minutes"], 0)

    def test_p2_dividends_is_blocked_by_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            queue_path = _write_queue(
                tmp,
                [
                    _queue_item("000003", "stock_holdings", "P2"),
                    _queue_item("000004", "dividends", "P2"),  # blocked even at P2
                ],
            )
            plan = fund_data.build_completion_plan(queue_path=queue_path)

        batches_by_dataset = {b["dataset"] for b in plan["batches"]}
        self.assertIn("stock_holdings", batches_by_dataset)
        self.assertNotIn("dividends", batches_by_dataset)
        blocked_datasets = {item["dataset"] for item in plan["blocked"]}
        self.assertIn("dividends", blocked_datasets)

    def test_budget_cap_limits_planned_funds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            items = [
                _queue_item(f"{i:06d}", "fund_profiles", "P1")
                for i in range(1, 11)
            ]
            queue_path = _write_queue(tmp, items)
            config = tmp / "policy.json"
            config.write_text(
                json.dumps(
                    {
                        "mode": "assisted",
                        "allowed_priorities": ["P1", "P2", "P3"],
                        "budgets": {"max_funds_per_run": 3, "max_provider_calls_per_run": 100},
                    }
                ),
                encoding="utf-8",
            )

            plan = fund_data.build_completion_plan(
                queue_path=queue_path, config_path=config
            )

        codes = [code for batch in plan["batches"] for code in batch["codes"]]
        self.assertEqual(len(codes), 3)
        self.assertEqual(plan["summary"]["skipped_for_budget"], 7)

    def test_output_path_writes_plan_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            queue_path = _write_queue(
                tmp, [_queue_item("000005", "nav_history", "P1")]
            )
            output_path = tmp / "plan.json"

            plan = fund_data.build_completion_plan(
                queue_path=queue_path, output_path=output_path
            )

            # The assertion has to live INSIDE the `with` block --
            # TemporaryDirectory() is cleaned up on exit, so reading
            # output_path after the block returns False by design.
            self.assertTrue(output_path.exists())
            on_disk = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                on_disk["batches"][0]["dataset"],
                plan["batches"][0]["dataset"],
            )

    def test_default_policy_when_no_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            queue_path = _write_queue(
                tmp,
                [
                    _queue_item("000006", "fund_profiles", "P1"),
                    _queue_item("000007", "dividends", "P4", "naturally_sparse"),
                ],
            )
            plan = fund_data.build_completion_plan(queue_path=queue_path)
        # No config -> defaults apply. P1 should be planned, P4 should be blocked.
        self.assertEqual(plan["mode"], "assisted")
        self.assertEqual(len(plan["batches"]), 1)
        self.assertEqual(plan["batches"][0]["dataset"], "fund_profiles")

    def test_estimated_minutes_uses_budget_concurrency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            queue_path = _write_queue(
                tmp,
                [
                    _queue_item(f"{i:06d}", "stock_holdings", "P2")
                    for i in range(1, 9)
                ],
            )
            config = tmp / "policy.json"
            config.write_text(
                json.dumps(
                    {
                        "budgets": {
                            "concurrency": 4,
                            "max_funds_per_run": 100,
                            "max_provider_calls_per_run": 1000,
                        }
                    }
                ),
                encoding="utf-8",
            )

            plan = fund_data.build_completion_plan(
                queue_path=queue_path, config_path=config
            )

        # 8 funds / 4 concurrency = 2 waves; est_minutes should be at least 1
        self.assertGreaterEqual(plan["summary"]["estimated_minutes"], 1)
        self.assertEqual(plan["summary"]["estimated_provider_calls"], 8)


class CompletionPolicyTests(unittest.TestCase):
    def test_load_completion_policy_applies_safe_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cfg = tmp / "policy.json"
            cfg.write_text(json.dumps({"mode": "audit_only"}), encoding="utf-8")
            policy = fund_data.load_completion_policy(cfg)
        self.assertEqual(policy["mode"], "audit_only")
        self.assertEqual(policy["allowed_priorities"], ["P1", "P2", "P3"])
        self.assertIn("dividends", policy["blocked_datasets"])
        self.assertEqual(policy["budgets"]["concurrency"], 4)
        self.assertEqual(policy["publish"]["mode"], "manual")

    def test_load_completion_policy_without_path_uses_builtin_defaults(self):
        policy = fund_data.load_completion_policy()
        self.assertEqual(policy["mode"], "assisted")
        self.assertIn("P1", policy["allowed_priorities"])


class CompletionPlanTimestampTests(unittest.TestCase):
    def test_run_id_is_utc_iso_basic(self):
        """Run-id format is YYYYMMDDTHHMMSSZ so file systems sort it
        chronologically and downstream bash scripts can parse it
        without extra tooling."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            queue_path = _write_queue(
                tmp, [_queue_item("000008", "fund_profiles", "P1")]
            )
            plan = fund_data.build_completion_plan(queue_path=queue_path)
        run_id = plan["run_id"]
        # Ends with Z (UTC), has T separator, all digits between
        self.assertTrue(run_id.endswith("Z"))
        self.assertIn("T", run_id)
        body = run_id.rstrip("Z")
        self.assertEqual(body, body.replace("-", "").replace(":", ""))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
