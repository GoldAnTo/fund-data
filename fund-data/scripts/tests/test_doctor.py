import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys_path = SCRIPT_DIR  # fund-data/ so "scripts" is a package
import sys

sys.path.insert(0, str(sys_path))

from scripts import doctor  # noqa: E402
from scripts import fund_cloud  # noqa: E402
from scripts import fund_data  # noqa: E402


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
                conn.executescript("""
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
                    """)
            result = doctor._check_db(db)
            self.assertTrue(result["ok"])


class CheckAkShareTests(unittest.TestCase):
    def test_venv_missing_reports_error(self):
        # The doctor function tries ``import akshare`` against the
        # current Python first. When that succeeds (because the
        # host has akshare installed) the function short-circuits
        # with ok=True and never reaches the venv check, regardless
        # of the venv path passed in. To exercise the "venv missing"
        # path we need to make the in-process import fail.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "akshare" or name.startswith("akshare."):
                raise ImportError("akshare not importable in this test")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
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


class ClassifyDefaultDbSourceTests(unittest.TestCase):
    """Pin the four-way ``source`` tag so an agent that branches on
    ``payload["default_db"]["source"]`` does not silently flip when
    the resolver changes.

    Layers (must be exhaustive, otherwise the agent gets a stale
    "unknown" and can't tell which knob to turn):
      env_override | cloud_cache | full_local | unknown
    """

    def test_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "explicit.sqlite"
            target.touch()
            with patch.dict("os.environ", {"FUND_DATA_DB": str(target)}):
                self.assertEqual(
                    doctor._classify_default_db_source(target), "env_override"
                )

    def test_full_local(self):
        # The on-disk fallback the project ships with.
        self.assertEqual(
            doctor._classify_default_db_source(doctor.DEFAULT_DB_PATH), "full_local"
        )

    def test_cloud_cache(self):
        # Anything under the cloud cache dir is the cloud query DB.
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            cache_dir.mkdir()
            cached_db = cache_dir / "fund_data.sqlite"
            with patch.object(doctor.fund_cloud, "default_cache_dir", return_value=cache_dir):
                self.assertEqual(
                    doctor._classify_default_db_source(cached_db), "cloud_cache"
                )

    def test_unknown(self):
        # A path that is not env, not in the cache dir, not the
        # on-disk fallback — surfaces the "I don't know" verdict
        # so the agent does not assume cloud_cache.
        with tempfile.TemporaryDirectory() as tmp:
            elsewhere = Path(tmp) / "somewhere.sqlite"
            with patch.object(doctor.fund_cloud, "default_cache_dir", return_value=Path(tmp) / "nope"):
                self.assertEqual(
                    doctor._classify_default_db_source(elsewhere), "unknown"
                )


class CheckDefaultDbTests(unittest.TestCase):
    def test_resolves_to_default_when_db_missing(self):
        # First run / fresh checkout: no DB on disk yet. ``ok`` is
        # still True (this is not a hard failure), ``exists``
        # reports the truth.
        with patch.object(doctor.fund_data, "default_db_path", return_value=Path("/nonexistent/fund.sqlite")):
            result = doctor._check_default_db()
        self.assertTrue(result["ok"])
        self.assertFalse(result["exists"])
        self.assertEqual(result["path"], "/nonexistent/fund.sqlite")
        # The classifier must still tag the path so an agent that
        # branches on ``source`` does not KeyError.
        self.assertIn("source", result)

    def test_reports_resolver_error_as_failure(self):
        # Network blip during cloud bootstrap should not silently
        # succeed — surface it as ok=False with the error name.
        with patch.object(
            doctor.fund_data, "default_db_path", side_effect=RuntimeError("bootstrap boom")
        ):
            result = doctor._check_default_db()
        self.assertFalse(result["ok"])
        self.assertIn("bootstrap boom", result["message"])


class CheckCloudCacheTests(unittest.TestCase):
    def test_reports_installed_and_up_to_date(self):
        with patch.object(
            doctor.fund_cloud,
            "status",
            return_value={
                "installed": True,
                "version": "2026-06-02-130900",
                "manifest_url": "https://example/manifest.json",
                "remote_version": "2026-06-02-130900",
                "update_available": False,
            },
        ):
            result = doctor._check_cloud_cache(manifest_url="https://example/manifest.json")
        self.assertTrue(result["ok"])
        self.assertTrue(result["installed"])
        self.assertFalse(result["update_available"])
        self.assertIsNone(result.get("skipped"))

    def test_surfaces_stale_as_warning_not_error(self):
        # update_available=True is the headline use case for this
        # check. ok must stay True — agents that want to fail on
        # staleness branch on ``update_available``, not the exit
        # code (which would break CI for a non-error condition).
        with patch.object(
            doctor.fund_cloud,
            "status",
            return_value={
                "installed": True,
                "version": "2026-06-02-053226",
                "manifest_url": "https://example/manifest.json",
                "remote_version": "2026-06-02-163200",
                "update_available": True,
            },
        ):
            result = doctor._check_cloud_cache(manifest_url="https://example/manifest.json")
        self.assertTrue(result["ok"])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["version"], "2026-06-02-053226")
        self.assertEqual(result["remote_version"], "2026-06-02-163200")

    def test_skips_remote_probe_when_manifest_url_is_none(self):
        # CI / --skip-network mode: caller passes manifest_url=None
        # and ``status`` returns only local cache metadata. The
        # check must not crash and must not pretend to know the
        # remote version.
        with patch.object(
            doctor.fund_cloud,
            "status",
            return_value={
                "installed": True,
                "version": "2026-06-02-053226",
            },
        ) as mock_status:
            result = doctor._check_cloud_cache(manifest_url=None)
        # status was called with manifest_url=None (no remote probe)
        mock_status.assert_called_once_with(manifest_url=None)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["remote_version"])
        self.assertFalse(result["update_available"])

    def test_reports_not_installed_as_skipped(self):
        with patch.object(
            doctor.fund_cloud,
            "status",
            return_value={"installed": False, "version": None},
        ):
            result = doctor._check_cloud_cache(manifest_url=None)
        self.assertTrue(result["ok"])
        self.assertFalse(result["installed"])
        self.assertEqual(result["skipped"], "no cloud cache installed")

    def test_reports_status_error_as_failure(self):
        with patch.object(
            doctor.fund_cloud, "status", side_effect=OSError("manifest URL unreachable")
        ):
            result = doctor._check_cloud_cache(manifest_url="https://broken/")
        self.assertFalse(result["ok"])
        self.assertIn("manifest URL unreachable", result["message"])


class MainOutputSchemaTests(unittest.TestCase):
    """``doctor.main`` is the agent-facing entry point. Lock its
    stdout / stderr / exit-code contract so a future refactor cannot
    silently break JSON consumers.

    The individual ``_check_*`` helpers each open the live SQLite
    schema (which is heavy to mock here), so these tests stub the
    whole check pipeline at the module level and exercise only
    ``main``'s serialization + exit-code contract."""

    # Default (with the live network probe): every check is in the
    # payload. With --skip-network the ``eastmoney_reachable`` key
    # is omitted -- each test below folds the conditional in.
    EXPECTED_TOP_LEVEL_KEYS = {
        "python",
        "database",
        "akshare",
        "providers",
        "eastmoney_reachable",
        "sync_failures",
        "coverage",
        "backfill_stale",
        "default_db",
        "cloud_cache",
    }
    EXPECTED_TOP_LEVEL_KEYS_NO_NETWORK = (
        EXPECTED_TOP_LEVEL_KEYS - {"eastmoney_reachable"}
    )

    def _all_ok_checks(self) -> dict[str, object]:
        return {
            "python": {"ok": True, "version": "3.13.3"},
            "database": {"ok": True, "path": "/tmp/fund.sqlite"},
            "akshare": {"ok": True, "version": "1.18.64", "source": "stub"},
            "providers": {
                "eastmoney": {"ok": True},
                "akshare": {"ok": True},
                "investoday": {"ok": True, "skipped": "INVESTDATA_API_KEY not set"},
            },
            "eastmoney_reachable": {"ok": True, "status": 200},
            "sync_failures": {"ok": True, "count": 0},
            "coverage": {"ok": True, "total_funds": 26936, "incomplete_examples": 0, "min_completeness": 1.0},
            "backfill_stale": {"ok": True, "skipped": "no backfill state file"},
            "default_db": {"ok": True, "path": "/tmp/fund.sqlite", "exists": True, "source": "full_local"},
            "cloud_cache": {
                "ok": True,
                "installed": True,
                "version": "2026-06-02-130900",
                "manifest_url": "https://example/manifest.json",
                "remote_version": "2026-06-02-130900",
                "update_available": False,
            },
        }

    def _mixed_checks(self) -> dict[str, object]:
        checks = self._all_ok_checks()
        checks["database"] = {"ok": False, "message": "database not found"}
        return checks

    def test_default_emits_pretty_json_on_stdout(self):
        # Default run: no --skip-network, so ``eastmoney_reachable``
        # is included in the payload. Mocks return ok for every
        # check, so the overall exit code is 0 and the FAIL banner
        # is suppressed.
        argv = ["--db", "/tmp/fund.sqlite"]
        with patch.object(sys, "argv", ["doctor.py"] + argv), patch.object(
            doctor, "_check_python", return_value={"ok": True, "version": "3.13.3"}
        ), patch.object(
            doctor, "_check_db", return_value={"ok": True, "path": "/tmp/fund.sqlite"}
        ), patch.object(
            doctor, "_check_akshare", return_value={"ok": True, "version": "1.18.64", "source": "stub"}
        ), patch.object(
            doctor, "_check_providers",
            return_value={
                "eastmoney": {"ok": True},
                "akshare": {"ok": True},
                "investoday": {"ok": True, "skipped": "INVESTDATA_API_KEY not set"},
            },
        ), patch.object(
            doctor, "_check_eastmoney", return_value={"ok": True, "status": 200}
        ), patch.object(
            doctor, "_check_sync_failures", return_value={"ok": True, "count": 0}
        ), patch.object(
            doctor, "_check_coverage",
            return_value={"ok": True, "total_funds": 26936, "incomplete_examples": 0, "min_completeness": 1.0},
        ), patch.object(
            doctor, "_check_backfill_stale",
            return_value={"ok": True, "skipped": "no backfill state file"},
        ), patch.object(
            doctor, "_check_default_db",
            return_value={"ok": True, "path": "/tmp/fund.sqlite", "exists": True, "source": "full_local"},
        ), patch.object(
            doctor, "_check_cloud_cache",
            return_value={
                "ok": True,
                "installed": True,
                "version": "2026-06-02-130900",
                "manifest_url": "https://example/manifest.json",
                "remote_version": "2026-06-02-130900",
                "update_available": False,
            },
        ):
            with patch("sys.stdout", new_callable=__import__("io").StringIO) as out, patch(
                "sys.stderr", new_callable=__import__("io").StringIO
            ) as err:
                exit_code = doctor.main(argv)
        self.assertEqual(exit_code, 0)
        payload = json.loads(out.getvalue())
        # The schema is the agent's contract -- every top-level
        # check name must be present so the consumer does not
        # KeyError when adding a new one.
        self.assertEqual(set(payload.keys()), self.EXPECTED_TOP_LEVEL_KEYS)
        # Pretty mode uses 2-space indent; the FAIL banner is
        # not printed because every check passed.
        self.assertIn("\n  ", out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_quiet_emits_compact_json_and_exits_zero_when_all_ok(self):
        argv = [
            "--db",
            "/tmp/fund.sqlite",
            "--skip-network",
            "--quiet",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            doctor, "_check_python", return_value={"ok": True, "version": "3.13.3"}
        ), patch.object(
            doctor, "_check_db", return_value={"ok": True, "path": "/tmp/fund.sqlite"}
        ), patch.object(
            doctor, "_check_akshare", return_value={"ok": True, "version": "1.18.64", "source": "stub"}
        ), patch.object(
            doctor, "_check_providers",
            return_value={
                "eastmoney": {"ok": True},
                "akshare": {"ok": True},
                "investoday": {"ok": True, "skipped": "INVESTDATA_API_KEY not set"},
            },
        ), patch.object(
            doctor, "_check_eastmoney", return_value={"ok": True, "status": 200}
        ), patch.object(
            doctor, "_check_sync_failures", return_value={"ok": True, "count": 0}
        ), patch.object(
            doctor, "_check_coverage",
            return_value={"ok": True, "total_funds": 26936, "incomplete_examples": 0, "min_completeness": 1.0},
        ), patch.object(
            doctor, "_check_backfill_stale",
            return_value={"ok": True, "skipped": "no backfill state file"},
        ), patch.object(
            doctor, "_check_default_db",
            return_value={"ok": True, "path": "/tmp/fund.sqlite", "exists": True, "source": "full_local"},
        ), patch.object(
            doctor, "_check_cloud_cache",
            return_value={
                "ok": True,
                "installed": True,
                "version": "2026-06-02-130900",
                "manifest_url": "https://example/manifest.json",
                "remote_version": "2026-06-02-130900",
                "update_available": False,
            },
        ):
            with patch("sys.stdout", new_callable=__import__("io").StringIO) as out, patch(
                "sys.stderr", new_callable=__import__("io").StringIO
            ) as err:
                exit_code = doctor.main(argv)
        self.assertEqual(exit_code, 0)
        payload = json.loads(out.getvalue())
        # --skip-network drops ``eastmoney_reachable``; the rest
        # of the schema is identical to the default run.
        self.assertEqual(set(payload.keys()), self.EXPECTED_TOP_LEVEL_KEYS_NO_NETWORK)
        # Compact mode: no newlines between fields, no FAIL banner
        self.assertNotIn("\n  ", out.getvalue())
        self.assertNotIn("FAIL", err.getvalue())

    def test_quiet_still_exits_nonzero_on_failure(self):
        # Missing database file should make the database check fail,
        # which flips the overall ok flag and (in compact mode)
        # does NOT print a banner but does exit non-zero so the
        # agent can branch on the return code.
        argv = [
            "--db",
            "/tmp/does-not-exist.sqlite",
            "--skip-network",
            "--quiet",
        ]
        with patch.object(sys, "argv", argv), patch.object(
            doctor, "_check_python", return_value={"ok": True, "version": "3.13.3"}
        ), patch.object(
            doctor, "_check_db", return_value={"ok": False, "message": "database not found"}
        ), patch.object(
            doctor, "_check_akshare", return_value={"ok": True, "version": "1.18.64", "source": "stub"}
        ), patch.object(
            doctor, "_check_providers",
            return_value={
                "eastmoney": {"ok": True},
                "akshare": {"ok": True},
                "investoday": {"ok": True, "skipped": "INVESTDATA_API_KEY not set"},
            },
        ), patch.object(
            doctor, "_check_eastmoney", return_value={"ok": True, "status": 200}
        ), patch.object(
            doctor, "_check_sync_failures", return_value={"ok": True, "count": 0}
        ), patch.object(
            doctor, "_check_coverage",
            return_value={"ok": True, "total_funds": 26936, "incomplete_examples": 0, "min_completeness": 1.0},
        ), patch.object(
            doctor, "_check_backfill_stale",
            return_value={"ok": True, "skipped": "no backfill state file"},
        ), patch.object(
            doctor, "_check_default_db",
            return_value={"ok": True, "path": "/tmp/fund.sqlite", "exists": True, "source": "full_local"},
        ), patch.object(
            doctor, "_check_cloud_cache",
            return_value={
                "ok": True,
                "installed": True,
                "version": "2026-06-02-130900",
                "manifest_url": "https://example/manifest.json",
                "remote_version": "2026-06-02-130900",
                "update_available": False,
            },
        ):
            with patch("sys.stdout", new_callable=__import__("io").StringIO) as out, patch(
                "sys.stderr", new_callable=__import__("io").StringIO
            ) as err:
                exit_code = doctor.main(argv)
        self.assertEqual(exit_code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(set(payload.keys()), self.EXPECTED_TOP_LEVEL_KEYS_NO_NETWORK)
        self.assertFalse(payload["database"]["ok"])
        self.assertNotIn("FAIL", err.getvalue())

    def test_output_flag_writes_to_file_and_skips_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "report.json"
            argv = [
                "--db",
                "/tmp/fund.sqlite",
                "--skip-network",
                "--quiet",
                "--output",
                str(out_path),
            ]
            with patch.object(sys, "argv", argv), patch.object(
                doctor, "_check_python", return_value={"ok": True, "version": "3.13.3"}
            ), patch.object(
                doctor, "_check_db", return_value={"ok": True, "path": "/tmp/fund.sqlite"}
            ), patch.object(
                doctor, "_check_akshare", return_value={"ok": True, "version": "1.18.64", "source": "stub"}
            ), patch.object(
                doctor, "_check_providers",
                return_value={
                    "eastmoney": {"ok": True},
                    "akshare": {"ok": True},
                    "investoday": {"ok": True, "skipped": "INVESTDATA_API_KEY not set"},
                },
            ), patch.object(
                doctor, "_check_eastmoney", return_value={"ok": True, "status": 200}
            ), patch.object(
                doctor, "_check_sync_failures", return_value={"ok": True, "count": 0}
            ), patch.object(
                doctor, "_check_coverage",
                return_value={"ok": True, "total_funds": 26936, "incomplete_examples": 0, "min_completeness": 1.0},
            ), patch.object(
                doctor, "_check_backfill_stale",
                return_value={"ok": True, "skipped": "no backfill state file"},
            ), patch.object(
                doctor, "_check_default_db",
                return_value={"ok": True, "path": "/tmp/fund.sqlite", "exists": True, "source": "full_local"},
            ), patch.object(
                doctor, "_check_cloud_cache",
                return_value={
                    "ok": True,
                    "installed": True,
                    "version": "2026-06-02-130900",
                    "manifest_url": "https://example/manifest.json",
                    "remote_version": "2026-06-02-130900",
                    "update_available": False,
                },
            ):
                with patch("sys.stdout", new_callable=__import__("io").StringIO) as out:
                    exit_code = doctor.main(argv)
            self.assertEqual(exit_code, 0)
            self.assertEqual(out.getvalue(), "")
            written = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(set(written.keys()), self.EXPECTED_TOP_LEVEL_KEYS_NO_NETWORK)


if __name__ == "__main__":
    unittest.main()
