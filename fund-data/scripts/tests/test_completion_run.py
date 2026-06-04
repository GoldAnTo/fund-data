"""Tests for the OpenClaw active-completion runner.

The runner is the safety boundary between "OpenClaw can read the
self-audit queue" and "OpenClaw can mutate the local SQLite base".
The contract is:

* Without ``confirm_execute=True``, the runner never spawns a
  subprocess and never calls :func:`fund_data.batch_sync_funds`.
* ``mode=audit_only`` always refuses execution.
* A plan that exceeds the configured call budget is refused
  before any subprocess is spawned.
* An execution report is always written, even on refusal.
* The lock file prevents two concurrent runners; a stale lock
  (>12h) is replaced.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402
from fund_data import completion  # noqa: E402


def _plan_payload(**overrides) -> dict:
    payload = {
        "run_id": "20260603T000000Z",
        "generated_at": "2026-06-03T00:00:00+00:00",
        "mode": "assisted",
        "dry_run": True,
        "allowed_to_execute": False,
        "config_path": None,
        "queue_path": "/tmp/queue.json",
        "run_root": "fund-data/data/openclaw_runs/20260603T000000Z",
        "summary": {
            "queue_size": 1,
            "blocked": 0,
            "planned_items": 1,
            "estimated_provider_calls": 1,
            "estimated_minutes": 1,
            "concurrency": 4,
            "skipped_for_budget": 0,
        },
        "batches": [
            {
                "batch_id": "openclaw-20260603T000000Z-fund_profiles-p1",
                "priority": "P1",
                "dataset": "fund_profiles",
                "provider": "auto",
                "codes": ["000001"],
                "codes_file": "fund-data/data/openclaw_runs/20260603T000000Z/codes/fund_profiles_p1_1_codes.txt",
                "command": (
                    ".venv-akshare/bin/python fund-data/scripts/fund_cli.py "
                    "batch-sync --codes-file x --provider auto --concurrency 4"
                ),
            }
        ],
        "blocked": [],
        "policy_snapshot": {
            "mode": "assisted",
            "allowed_priorities": ["P1", "P2", "P3"],
            "blocked_datasets": ["dividends", "splits"],
            "budgets": {
                "max_funds_per_run": 100,
                "max_provider_calls_per_run": 300,
                "max_elapsed_minutes": 30,
                "concurrency": 4,
                "min_interval_seconds": 0.2,
                "max_failure_rate": 0.25,
            },
        },
    }
    payload.update(overrides)
    return payload


def _write_plan(tmp: Path, plan: dict) -> Path:
    path = tmp / "plan.json"
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    return path


class CompletionRunnerDryRunTests(unittest.TestCase):
    def test_no_confirm_execute_never_calls_subprocess(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            plan = _plan_payload()
            plan_path = _write_plan(tmp, plan)

            with mock.patch.object(completion.subprocess, "run") as mock_run:
                with mock.patch.object(
                    fund_data, "batch_sync_funds"
                ) as mock_sync:
                    execution = fund_data.run_completion_plan(
                        plan_path=plan_path, confirm_execute=False
                    )

        self.assertFalse(execution["executed"])
        self.assertIsNotNone(execution["refusal_reason"])
        mock_run.assert_not_called()
        mock_sync.assert_not_called()

    def test_audit_only_mode_always_refuses_even_with_confirm(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            plan = _plan_payload()
            plan_path = _write_plan(tmp, plan)
            config = tmp / "policy.json"
            config.write_text(json.dumps({"mode": "audit_only"}), encoding="utf-8")

            with mock.patch.object(completion.subprocess, "run") as mock_run:
                execution = fund_data.run_completion_plan(
                    plan_path=plan_path,
                    config_path=config,
                    confirm_execute=True,
                )

        self.assertFalse(execution["executed"])
        self.assertIn("audit_only", execution["refusal_reason"])
        mock_run.assert_not_called()

    def test_plan_over_max_provider_calls_refuses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            plan = _plan_payload()
            plan["summary"]["estimated_provider_calls"] = 999
            plan_path = _write_plan(tmp, plan)
            config = tmp / "policy.json"
            config.write_text(
                json.dumps(
                    {
                        "mode": "assisted",
                        "budgets": {"max_provider_calls_per_run": 10},
                    }
                ),
                encoding="utf-8",
            )

            with mock.patch.object(completion.subprocess, "run") as mock_run:
                execution = fund_data.run_completion_plan(
                    plan_path=plan_path,
                    config_path=config,
                    confirm_execute=True,
                )

        self.assertFalse(execution["executed"])
        self.assertIn("budget", execution["refusal_reason"].lower())
        mock_run.assert_not_called()

    def test_refusal_writes_execution_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            plan = _plan_payload()
            plan_path = _write_plan(tmp, plan)
            fund_data.run_completion_plan(plan_path=plan_path, confirm_execute=False)
            run_root = Path(plan["run_root"])
            execution_path = run_root / "execution.json"
            self.assertTrue(execution_path.exists())
            payload = json.loads(execution_path.read_text(encoding="utf-8"))
            self.assertFalse(payload["executed"])
            self.assertIsNotNone(payload["refusal_reason"])


class CompletionRunnerLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # run_completion_plan creates run_root relative to cwd; cd into
        # the tmp dir so all artifacts land there.
        self._orig_cwd = Path.cwd()
        import os
        os.chdir(self.tmp)
        # Patch the lock file path so the test never collides with
        # any other concurrent runner.
        self._lock_patch = mock.patch.object(
            completion, "_lock_path", return_value=self.tmp / "openclaw_active_completion.lock"
        )
        self._lock_patch.start()

    def tearDown(self):
        import os
        os.chdir(self._orig_cwd)
        self._lock_patch.stop()
        self._tmp.cleanup()

    def test_alive_lock_refuses_second_runner(self):
        plan = _plan_payload()
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

        # First runner: claim the lock manually. Use a *fresh*
        # created_at so the lock is not auto-stale (>12h is the
        # threshold in completion.LOCK_STALE_HOURS).
        from datetime import UTC, datetime
        lock = self.tmp / "openclaw_active_completion.lock"
        lock.write_text(
            json.dumps(
                {
                    "pid": 999999,  # not this process
                    "created_at": datetime.now(UTC).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(completion, "_pid_alive", return_value=True), \
             mock.patch.object(completion.subprocess, "run") as mock_run:
            execution = fund_data.run_completion_plan(
                plan_path=plan_path, confirm_execute=True
            )
        self.assertFalse(execution["executed"], f"expected refused, got {execution}")
        self.assertIn("another completion run", execution["refusal_reason"])
        mock_run.assert_not_called()

    def test_stale_lock_is_replaced(self):
        # 24h-old lock with a dead pid should be replaced.
        from datetime import UTC, datetime, timedelta
        lock = self.tmp / "openclaw_active_completion.lock"
        old_ts = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
        lock.write_text(
            json.dumps({"pid": 999999, "created_at": old_ts}),
            encoding="utf-8",
        )
        result = completion._acquire_lock(lock)
        self.assertTrue(result["acquired"])
        on_disk = json.loads(lock.read_text(encoding="utf-8"))
        # The pid on disk should now be ours, not the dead one.
        self.assertNotEqual(on_disk["pid"], 999999)


class CompletionRunnerExecutionTests(unittest.TestCase):
    """Subprocess-backed paths. We mock ``subprocess.run`` so the
    tests stay offline and fast."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self._orig_cwd = Path.cwd()
        import os
        os.chdir(self.tmp)
        self._lock_patch = mock.patch.object(
            completion, "_lock_path", return_value=self.tmp / "openclaw_active_completion.lock"
        )
        self._lock_patch.start()

    def tearDown(self):
        import os
        os.chdir(self._orig_cwd)
        self._lock_patch.stop()
        self._tmp.cleanup()

    def test_confirmed_run_executes_each_batch_once(self):
        plan = _plan_payload()
        # Three batches so we can assert that the runner keeps going
        # after the first success.
        plan["batches"] = [
            {**plan["batches"][0], "batch_id": f"openclaw-b{i}", "codes": [f"{i:06d}"]}
            for i in range(1, 4)
        ]
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

        with mock.patch.object(
            completion.subprocess, "run",
            return_value=subprocess.CompletedProcess(args="x", returncode=0, stdout="ok", stderr=""),
        ) as mock_run:
            execution = fund_data.run_completion_plan(
                plan_path=plan_path, confirm_execute=True
            )
        self.assertTrue(execution["executed"])
        self.assertEqual(mock_run.call_count, 3)
        self.assertEqual(execution["summary"]["executed_batches"], 3)
        self.assertEqual(execution["summary"]["failed_batches"], 0)

    def test_failure_rate_budget_stops_run(self):
        plan = _plan_payload()
        plan["batches"] = [
            {**plan["batches"][0], "batch_id": f"openclaw-b{i}", "codes": [f"{i:06d}"]}
            for i in range(1, 6)
        ]
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        config = self.tmp / "policy.json"
        config.write_text(
            json.dumps(
                {
                    "mode": "assisted",
                    "budgets": {"max_failure_rate": 0.25, "max_provider_calls_per_run": 1000},
                }
            ),
            encoding="utf-8",
        )

        def _flaky(*args, **kwargs):
            # First two batches fail, rest succeed. With a 25%
            # budget the runner should stop after seeing the second
            # failure (2/2 = 100% > 25%).
            call_count = getattr(_flaky, "n", 0) + 1
            _flaky.n = call_count
            return subprocess.CompletedProcess(
                args="x",
                returncode=1 if call_count <= 2 else 0,
                stdout="",
                stderr="boom",
            )

        with mock.patch.object(completion.subprocess, "run", side_effect=_flaky):
            execution = fund_data.run_completion_plan(
                plan_path=plan_path,
                config_path=config,
                confirm_execute=True,
            )
        # 2 batches were attempted, both failed, runner stopped.
        self.assertEqual(execution["summary"]["failed_batches"], 2)
        self.assertIn("failure rate", execution["refusal_reason"])

    def test_run_writes_per_batch_logs(self):
        plan = _plan_payload()
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

        with mock.patch.object(
            completion.subprocess, "run",
            return_value=subprocess.CompletedProcess(args="x", returncode=0, stdout="ok", stderr=""),
        ):
            fund_data.run_completion_plan(plan_path=plan_path, confirm_execute=True)
        run_root = Path(plan["run_root"])
        stdout_log = run_root / "logs" / "openclaw-20260603T000000Z-fund_profiles-p1.stdout.log"
        self.assertTrue(stdout_log.exists())

    def test_rows_changed_is_summed_from_per_fund_results(self):
        """Regression for the 2026-06-03 trial: batch-sync's stdout
        only reports ``rows_changed`` inside ``results[0]``, not at
        the top level. The runner used to surface 0 even when the
        per-fund value was 22, so the verify report thought the
        fill had no effect. Now we sum the per-fund values when no
        aggregate counter is present."""
        plan = _plan_payload()
        plan["batches"] = [
            {**plan["batches"][0], "batch_id": "openclaw-b1", "codes": ["110022"]}
        ]
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        # Realistic batch-sync stdout for a single successful fill.
        stdout = json.dumps({
            "batch_id": "openclaw-b1",
            "total": 1, "ok": 1, "failed": 0,
            "results": [
                {
                    "fund_code": "110022", "status": "ok",
                    "rows_changed": 22, "nav_rows": 20,
                }
            ],
        })
        with mock.patch.object(
            completion.subprocess, "run",
            return_value=subprocess.CompletedProcess(args="x", returncode=0, stdout=stdout, stderr=""),
        ):
            execution = fund_data.run_completion_plan(
                plan_path=plan_path, confirm_execute=True
            )
        self.assertEqual(execution["summary"]["rows_changed"], 22)

    def test_rows_actually_inserted_uses_real_db_diff(self):
        """Regression for the 2026-06-04 rows-actually-inserted
        ship: ``rows_changed`` from batch-sync counts attempted
        upserts (including no-op writes against existing rows).
        A second run against a fully-populated DB reports
        ``rows_changed=22`` even though the DB grew by zero, so
        the verify report would falsely recommend publish.

        The runner now samples the actual DB row counts on the
        8 query tables before and after each batch and reports
        the diff as ``rows_actually_inserted``. The verify layer
        gates on this field instead of the optimistic stdout
        counter.
        """
        plan = _plan_payload()
        plan["batches"] = [
            {**plan["batches"][0], "batch_id": "openclaw-b1", "codes": ["110022"]}
        ]
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        # batch-sync stdout is the optimistic case: it claims 22
        # rows were written. The DB diff below will report 0 -- the
        # runner should prefer the diff over the stdout.
        stdout = json.dumps({
            "batch_id": "openclaw-b1",
            "total": 1, "ok": 1, "failed": 0,
            "results": [
                {
                    "fund_code": "110022", "status": "ok",
                    "rows_changed": 22, "nav_rows": 20,
                }
            ],
        })

        # Snapshot the tables before / after; the diff is 0
        # because no rows were actually inserted.
        snapshot_calls: list[int] = []
        real_snapshot = completion._snapshot_table_counts

        def tracking_snapshot(db_path, codes):
            value = real_snapshot(db_path, codes)
            snapshot_calls.append(int(sum(value.values())))
            return value

        with mock.patch.object(
            completion.subprocess, "run",
            return_value=subprocess.CompletedProcess(args="x", returncode=0, stdout=stdout, stderr=""),
        ), mock.patch.object(
            completion, "_snapshot_table_counts", side_effect=tracking_snapshot
        ):
            execution = fund_data.run_completion_plan(
                plan_path=plan_path, confirm_execute=True
            )
        # batch-sync reported rows_changed=22 (optimistic), but the
        # DB diff is 0 because nothing was actually inserted.
        self.assertEqual(execution["summary"]["rows_changed"], 22)
        self.assertEqual(execution["summary"]["rows_actually_inserted"], 0)
        # We snapshotted the tables at least once before and once
        # after the batch.
        self.assertGreaterEqual(len(snapshot_calls), 2)

    def test_verify_uses_rows_actually_inserted_for_publish_gate(self):
        """``publish_recommended`` should fire only when the DB
        actually grew, not when batch-sync stdout claims it did."""
        execution = {
            "executed": True,
            "refusal_reason": None,
            "summary": {
                # batch-sync's optimistic counter says 22 rows
                # changed, but the real DB diff is 0 -- a no-op
                # re-run.
                "rows_changed": 22,
                "rows_actually_inserted": 0,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            before = tmpdir / "before.json"
            after = tmpdir / "after.json"
            exec_p = tmpdir / "execution.json"
            before.write_text(json.dumps({"summary": {"queue_size": 1, "p3": 1}}), encoding="utf-8")
            after.write_text(json.dumps({"summary": {"queue_size": 1, "p3": 1}}), encoding="utf-8")
            exec_p.write_text(json.dumps(execution), encoding="utf-8")

            report = fund_data.verify_completion_run(
                before_queue_path=before,
                after_queue_path=after,
                execution_path=exec_p,
            )
        self.assertEqual(report["rows_actually_inserted"], 0)
        # The optimistic counter is surfaced for transparency but
        # the publish gate now uses the trustworthy diff signal.
        self.assertEqual(report["rows_changed"], 22)
        self.assertFalse(report["publish_recommended"])

    def test_provider_calls_is_not_double_counted(self):
        """Regression for Bug 2 / 2026-06-03 completion: the runner
        used to pre-fill ``summary.provider_calls`` from the plan's
        ``estimated_provider_calls`` and then add it again as each
        batch executed, so a 2-code plan reported provider_calls=4.
        After the fix the field is purely the sum of actually
        executed batch sizes."""
        plan = _plan_payload()
        # 2 codes per batch, 1 batch -> real provider_calls = 2.
        plan["batches"] = [
            {**plan["batches"][0], "batch_id": "openclaw-b1", "codes": ["000001", "000002"]}
        ]
        plan["summary"]["estimated_provider_calls"] = 2
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

        with mock.patch.object(
            completion.subprocess, "run",
            return_value=subprocess.CompletedProcess(args="x", returncode=0, stdout="ok", stderr=""),
        ):
            execution = fund_data.run_completion_plan(
                plan_path=plan_path, confirm_execute=True
            )
        self.assertEqual(execution["summary"]["provider_calls"], 2)

    def test_provider_calls_refusal_does_not_leak_estimated(self):
        """The budget-overflow refusal path used to set
        ``provider_calls`` to the planned estimate and then return,
        so the execution report read as if the run had already
        touched the database even though it was a refusal. The
        post-fix value stays at 0 for refusals."""
        plan = _plan_payload()
        plan["batches"] = [
            {**plan["batches"][0], "batch_id": "openclaw-b1", "codes": ["000001"]}
        ]
        plan["summary"]["estimated_provider_calls"] = 999
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        config = self.tmp / "policy.json"
        config.write_text(
            json.dumps(
                {
                    "mode": "assisted",
                    "budgets": {"max_provider_calls_per_run": 10},
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(completion.subprocess, "run") as mock_run:
            execution = fund_data.run_completion_plan(
                plan_path=plan_path,
                config_path=config,
                confirm_execute=True,
            )
        self.assertFalse(execution["executed"])
        self.assertIn("budget", execution["refusal_reason"].lower())
        # Bug 2 regression: the refusal path must not pre-fill
        # provider_calls with the planned estimate.
        self.assertEqual(execution["summary"]["provider_calls"], 0)
        mock_run.assert_not_called()


class CompletionVerifyTests(unittest.TestCase):
    def test_verify_completion_run_reports_improvement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            before = {
                "summary": {"queue_size": 100, "p3": 50},
                "queue": [],
                "batch_suggestions": [],
            }
            after = {
                "summary": {"queue_size": 80, "p3": 50},
                "queue": [],
                "batch_suggestions": [],
            }
            before_p = tmp / "before.json"
            after_p = tmp / "after.json"
            before_p.write_text(json.dumps(before), encoding="utf-8")
            after_p.write_text(json.dumps(after), encoding="utf-8")
            execution = {
                "executed": True,
                "refusal_reason": None,
                "summary": {"rows_changed": 25, "elapsed_seconds": 12.0},
            }
            exec_p = tmp / "execution.json"
            exec_p.write_text(json.dumps(execution), encoding="utf-8")

            result = fund_data.verify_completion_run(
                before_queue_path=before_p,
                after_queue_path=after_p,
                execution_path=exec_p,
            )

        self.assertEqual(result["before_queue_size"], 100)
        self.assertEqual(result["after_queue_size"], 80)
        self.assertEqual(result["improved_items"], 20)
        self.assertEqual(result["rows_changed"], 25)
        self.assertTrue(result["publish_recommended"])

    def test_verify_marks_publish_blocked_when_p3_grew(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            before = {"summary": {"queue_size": 50, "p3": 10}, "queue": [], "batch_suggestions": []}
            after = {"summary": {"queue_size": 40, "p3": 25}, "queue": [], "batch_suggestions": []}
            before_p = tmp / "before.json"
            after_p = tmp / "after.json"
            before_p.write_text(json.dumps(before), encoding="utf-8")
            after_p.write_text(json.dumps(after), encoding="utf-8")
            exec_p = tmp / "execution.json"
            exec_p.write_text(
                json.dumps(
                    {
                        "executed": True,
                        "refusal_reason": None,
                        "summary": {"rows_changed": 30},
                    }
                ),
                encoding="utf-8",
            )

            result = fund_data.verify_completion_run(
                before_queue_path=before_p,
                after_queue_path=after_p,
                execution_path=exec_p,
            )
        self.assertEqual(result["new_failures"], 15)
        self.assertFalse(result["publish_recommended"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
