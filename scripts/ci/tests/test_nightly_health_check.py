"""Tests for ``scripts/ci/nightly_health_check.py``.

The runner is the contract the next implementation pass picks
up. These tests lock three things:

- ``run_step`` parses exit codes correctly and classifies
  the failure as transient vs data by sniffing stderr/stdout
  for the well-known ossutil / SSL / network markers
- ``_retry_with_backoff`` retries *only* transient failures,
  and only up to ``max_attempts`` times. Data failures are
  returned on the first attempt and never silently masked
  by a backoff
- ``run_gate`` produces a stable summary envelope with the
  same shape the GitHub Actions workflow gate reads

The runner shells out to ``fund_cli.py`` for real in CI; here
we patch ``subprocess.run`` so the tests do not need a real
SQLite / ossutil / network.
"""

import io
import json
import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]  # scripts/
sys.path.insert(0, str(SCRIPT_DIR))

# ``fund_cli`` does not exist as an importable module from
# here, so we re-implement a tiny stub for the runner to
# resolve. The runner resolves ``FUND_CLI`` as a Path, not
# via import, so a fake file is enough.
import nightly_health_check  # noqa: E402
from nightly_health_check import (  # noqa: E402
    StepResult,
    _looks_transient,
    _retry_with_backoff,
    _is_transient_step,
    _is_data_step,
    run_step,
    run_gate,
)


def _fake_completed(
    *,
    returncode: int,
    stdout: str = "",
    stderr: str = "",
) -> mock.MagicMock:
    """Build a mock that looks like a ``subprocess.run``
    ``CompletedProcess``."""
    cp = mock.MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


class RunStepTests(unittest.TestCase):
    def test_zero_exit_with_json_stdout_is_ok_and_payload_parsed(self):
        payload = {"checks": [{"name": "database", "ok": True}]}
        with mock.patch.object(
            nightly_health_check.subprocess, "run",
            return_value=_fake_completed(
                returncode=0,
                stdout=json.dumps(payload),
            ),
        ):
            result = run_step("doctor", ["fund_cli", "doctor"])
        self.assertTrue(result.ok)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.transient)
        self.assertEqual(result.payload, payload)

    def test_non_zero_exit_classifies_ssl_blip_as_transient(self):
        # SSL handshake error in stderr -- the most common
        # transient failure on a flaky network. The runner
        # must mark this transient so the gate retries.
        with mock.patch.object(
            nightly_health_check.subprocess, "run",
            return_value=_fake_completed(
                returncode=1,
                stderr="ssl.SSLEOFError: EOF occurred in violation of protocol",
            ),
        ):
            result = run_step("upload", ["fund_cli", "cloud", "upload"])
        self.assertFalse(result.ok)
        self.assertTrue(result.transient)
        self.assertIn("SSL", result.error)

    def test_non_zero_exit_with_data_message_is_data_not_transient(self):
        # sha256 mismatch -- the canonical data failure. The
        # runner must NOT mark this transient; retrying only
        # hides the regression.
        with mock.patch.object(
            nightly_health_check.subprocess, "run",
            return_value=_fake_completed(
                returncode=1,
                stdout="sha256 mismatch on the destination object",
            ),
        ):
            result = run_step("pull-and-verify", ["fund_cli", "cloud", "pull"])
        self.assertFalse(result.ok)
        self.assertFalse(result.transient)
        self.assertIn("sha256", result.error)

    def test_subprocess_timeout_is_transient(self):
        with mock.patch.object(
            nightly_health_check.subprocess, "run",
            side_effect=subprocess.TimeoutExpired(cmd="foo", timeout=5),
        ):
            result = run_step("upload", ["fund_cli", "cloud", "upload"])
        self.assertFalse(result.ok)
        self.assertTrue(result.transient)
        self.assertEqual(result.exit_code, -1)
        self.assertIn("timed out", result.error)


class TransientVsDataRetryTests(unittest.TestCase):
    """``_retry_with_backoff`` is the discipline boundary. A
    wrong call here either hides regressions (data failure gets
    retried into a green) or wastes minutes on a hard error
    (data failure gets retried into a green)."""

    def test_transient_failure_is_retried_with_backoff(self):
        attempts: list[int] = []
        def fake_run(name, cmd):
            attempts.append(len(attempts) + 1)
            return StepResult(
                name=name,
                ok=False,
                exit_code=1,
                duration_seconds=0.0,
                transient=True,
                error="connection reset",
            )
        with mock.patch.object(nightly_health_check, "run_step", fake_run), mock.patch.object(
            nightly_health_check.time, "sleep"
        ) as mock_sleep:
            results = _retry_with_backoff(
                [("upload", ["x"])], max_attempts=3, backoff_seconds=(1, 2, 4)
            )
        self.assertEqual(len(attempts), 3)
        # Sleeps use the backoff sequence: 1s, 2s, 4s.
        self.assertEqual([c.args[0] for c in mock_sleep.call_args_list], [1, 2])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertTrue(results[0].transient)

    def test_data_failure_is_returned_after_first_attempt(self):
        attempts: list[int] = []
        def fake_run(name, cmd):
            attempts.append(len(attempts) + 1)
            return StepResult(
                name=name,
                ok=False,
                exit_code=1,
                duration_seconds=0.0,
                transient=False,
                error="sha256 mismatch",
            )
        with mock.patch.object(nightly_health_check, "run_step", fake_run), mock.patch.object(
            nightly_health_check.time, "sleep"
        ) as mock_sleep:
            results = _retry_with_backoff(
                [("pull-and-verify", ["x"])], max_attempts=3, backoff_seconds=(1, 2, 4)
            )
        # Data failure must not retry -- that hides the
        # regression. One attempt, no sleep, return the result.
        self.assertEqual(len(attempts), 1)
        self.assertEqual(mock_sleep.call_count, 0)
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].ok)
        self.assertFalse(results[0].transient)

    def test_first_success_stops_retrying(self):
        attempts: list[int] = []
        def fake_run(name, cmd):
            attempts.append(len(attempts) + 1)
            return StepResult(
                name=name,
                ok=True,
                exit_code=0,
                duration_seconds=0.0,
                transient=False,
            )
        with mock.patch.object(nightly_health_check, "run_step", fake_run):
            results = _retry_with_backoff([("doctor", ["x"])], max_attempts=3)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)

    def test_transient_succeeds_on_retry_does_not_retry_again(self):
        attempts: list[int] = []
        def fake_run(name, cmd):
            attempts.append(len(attempts) + 1)
            if len(attempts) == 1:
                return StepResult(
                    name=name, ok=False, exit_code=1,
                    duration_seconds=0.0, transient=True, error="timeout",
                )
            return StepResult(
                name=name, ok=True, exit_code=0,
                duration_seconds=0.0, transient=False,
            )
        with mock.patch.object(nightly_health_check, "run_step", fake_run), mock.patch.object(
            nightly_health_check.time, "sleep"
        ):
            results = _retry_with_backoff([("upload", ["x"])], max_attempts=3)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)


class ClassifierTests(unittest.TestCase):
    def test_classifier_ossutil_5xx_is_transient(self):
        self.assertTrue(_looks_transient("", "ossutil error: 503 Service Unavailable"))

    def test_classifier_schema_drift_is_not_transient(self):
        self.assertFalse(
            _looks_transient("", "missing tables: funds, nav_history")
        )

    def test_classifier_helper_consistency(self):
        transient_result = StepResult(
            name="x", ok=False, exit_code=1, duration_seconds=0.0,
            transient=True, error="timeout",
        )
        data_result = StepResult(
            name="x", ok=False, exit_code=1, duration_seconds=0.0,
            transient=False, error="schema drift",
        )
        self.assertTrue(_is_transient_step(transient_result))
        self.assertFalse(_is_transient_step(data_result))
        self.assertTrue(_is_data_step(data_result))
        self.assertFalse(_is_data_step(transient_result))


class RunGateEnvelopeTests(unittest.TestCase):
    """``run_gate`` produces the JSON envelope the GitHub
    Actions workflow gate reads. The shape is the consumer
    contract; the per-step payload is intentionally close to
    the per-subcommand JSON envelopes already locked down by
    the unit tests in tests/test_doctor.py /
    test_fund_cloud.py / test_fund_mcp.py."""

    def test_run_gate_summary_envelope_shape(self):
        fake_results = [
            StepResult(
                name="doctor", ok=True, exit_code=0,
                duration_seconds=0.4, transient=False,
                payload={"checks": [{"name": "database", "ok": True}]},
            ),
            StepResult(
                name="build-bundle", ok=True, exit_code=0,
                duration_seconds=12.0, transient=False,
                payload={"manifest": {"version": "2026-06-02"}},
            ),
            StepResult(
                name="upload", ok=True, exit_code=0,
                duration_seconds=8.0, transient=False,
                payload={"manifest_url": "https://example/manifest.json"},
            ),
            StepResult(
                name="pull-and-verify", ok=True, exit_code=0,
                duration_seconds=3.0, transient=False,
            ),
        ]
        with mock.patch.object(
            nightly_health_check, "_retry_with_backoff", return_value=fake_results
        ):
            summary = run_gate(
                db=Path("/tmp/fund.sqlite"),
                release_dir=Path("/tmp/release"),
                manifest_output=Path("/tmp/release/manifest.json"),
            )
        # Pin the top-level schema. The workflow gate reads
        # every one of these keys.
        self.assertEqual(
            set(summary.keys()),
            {
                "run_id",
                "started_at",
                "finished_at",
                "overall_ok",
                "data_failure",
                "transient_exhausted",
                "steps",
            },
        )
        self.assertTrue(summary["overall_ok"])
        self.assertFalse(summary["data_failure"])
        self.assertFalse(summary["transient_exhausted"])
        self.assertEqual(len(summary["steps"]), 4)
        step_names = [step["name"] for step in summary["steps"]]
        self.assertEqual(
            step_names,
            ["doctor", "build-bundle", "upload", "pull-and-verify"],
        )

    def test_run_gate_marks_data_failure_when_any_step_is_data(self):
        fake_results = [
            StepResult(
                name="doctor", ok=True, exit_code=0,
                duration_seconds=0.1, transient=False,
            ),
            StepResult(
                name="build-bundle", ok=True, exit_code=0,
                duration_seconds=1.0, transient=False,
            ),
            StepResult(
                name="upload", ok=False, exit_code=1,
                duration_seconds=0.0, transient=False,
                error="sha256 mismatch on the destination object",
            ),
            StepResult(
                name="pull-and-verify", ok=True, exit_code=0,
                duration_seconds=0.1, transient=False,
            ),
        ]
        with mock.patch.object(
            nightly_health_check, "_retry_with_backoff", return_value=fake_results
        ):
            summary = run_gate(
                db=Path("/tmp/fund.sqlite"),
                release_dir=Path("/tmp/release"),
                manifest_output=Path("/tmp/release/manifest.json"),
            )
        self.assertFalse(summary["overall_ok"])
        self.assertTrue(summary["data_failure"])
        self.assertFalse(summary["transient_exhausted"])


if __name__ == "__main__":
    unittest.main()
