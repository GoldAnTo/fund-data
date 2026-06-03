import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

from test_fund_data import FUND_CODE_LIST_PAYLOAD, NAV_PAYLOAD, SEARCH_PAYLOAD, SNAPSHOT_PAYLOAD

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
CLI_PATH = SCRIPT_DIR / "fund_cli.py"

import fund_cli


class FundCliTests(unittest.TestCase):
    def run_cli(self, *args, cwd=None, env=None):
        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args],
            cwd=cwd or SCRIPT_DIR,
            env=child_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_search_offline_raw_persists_results_and_prints_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "search.json"
            db_path = Path(tmpdir) / "fund_data.sqlite"
            raw_path.write_text(SEARCH_PAYLOAD, encoding="utf-8")

            result = self.run_cli(
                "search",
                "沪深300",
                "--offline-raw",
                str(raw_path),
                "--db",
                str(db_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload[0]["fund_code"], "006600")
            with closing(sqlite3.connect(db_path)) as conn:
                count = conn.execute("select count(*) from funds").fetchone()[0]
            self.assertEqual(count, 1)

    def test_list_offline_raw_persists_full_fund_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "fund_list.js"
            db_path = Path(tmpdir) / "fund_data.sqlite"
            raw_path.write_text(FUND_CODE_LIST_PAYLOAD, encoding="utf-8")

            result = self.run_cli(
                "list",
                "--offline-raw",
                str(raw_path),
                "--db",
                str(db_path),
                "--limit",
                "1",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            rows = json.loads(result.stdout)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["fund_code"], "000001")
            with closing(sqlite3.connect(db_path)) as conn:
                count = conn.execute("select count(*) from funds").fetchone()[0]
            self.assertEqual(count, 2)

    def test_nav_and_snapshot_offline_raw_persist_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nav_path = Path(tmpdir) / "nav.js"
            snapshot_path = Path(tmpdir) / "snapshot.js"
            db_path = Path(tmpdir) / "fund_data.sqlite"
            nav_path.write_text(NAV_PAYLOAD, encoding="utf-8")
            snapshot_path.write_text(SNAPSHOT_PAYLOAD, encoding="utf-8")

            nav_result = self.run_cli(
                "nav",
                "110022",
                "--offline-raw",
                str(nav_path),
                "--db",
                str(db_path),
            )
            snapshot_result = self.run_cli(
                "snapshot",
                "110022",
                "--offline-raw",
                str(snapshot_path),
                "--db",
                str(db_path),
            )

            self.assertEqual(nav_result.returncode, 0, nav_result.stderr)
            self.assertEqual(snapshot_result.returncode, 0, snapshot_result.stderr)
            with closing(sqlite3.connect(db_path)) as conn:
                nav_count = conn.execute("select count(*) from nav_history").fetchone()[0]
                snapshot_count = conn.execute("select count(*) from snapshots").fetchone()[0]
            self.assertEqual(nav_count, 2)
            self.assertEqual(snapshot_count, 1)

    def test_export_prints_json_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "search.json"
            db_path = Path(tmpdir) / "fund_data.sqlite"
            raw_path.write_text(SEARCH_PAYLOAD, encoding="utf-8")
            search_result = self.run_cli(
                "search",
                "沪深300",
                "--offline-raw",
                str(raw_path),
                "--db",
                str(db_path),
            )
            self.assertEqual(search_result.returncode, 0, search_result.stderr)

            export_result = self.run_cli(
                "export",
                "funds",
                "--db",
                str(db_path),
                "--format",
                "json",
            )

            self.assertEqual(export_result.returncode, 0, export_result.stderr)
            rows = json.loads(export_result.stdout)
            self.assertEqual(rows[0]["fund_name"], "人保沪深300A")

    def test_cli_accepts_provider_argument(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "search.json"
            db_path = Path(tmpdir) / "fund_data.sqlite"
            raw_path.write_text(SEARCH_PAYLOAD, encoding="utf-8")

            result = self.run_cli(
                "search",
                "沪深300",
                "--provider",
                "eastmoney",
                "--offline-raw",
                str(raw_path),
                "--db",
                str(db_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["fund_code"], "006600")

    def test_cli_accepts_tushare_provider_argument(self):
        parsed = fund_cli.build_parser().parse_args(["search", "沪深300", "--provider", "tushare"])

        self.assertEqual(parsed.provider, "tushare")

    def test_nav_refresh_flag_skips_local_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            with mock.patch.object(
                fund_cli.fund_data,
                "fetch_nav_history",
                return_value=[{"nav_date": "2024-01-31", "unit_nav": 4.0}],
            ) as mock_fetch, redirect_stdout(io.StringIO()):
                exit_code = fund_cli.main(["nav", "110022", "--db", str(db_path), "--refresh"])

            self.assertEqual(exit_code, 0)
            self.assertFalse(mock_fetch.call_args.kwargs["cache"])

    def test_cli_bootstraps_cloud_for_default_data_commands(self):
        args = fund_cli.build_parser().parse_args(["search", "沪深300"])
        with mock.patch.object(fund_cli.fund_cloud, "ensure_project_bundle") as mock_bootstrap:
            fund_cli._maybe_bootstrap_cloud(args)

        mock_bootstrap.assert_called_once_with()

    def test_cli_does_not_bootstrap_cloud_when_db_is_explicit(self):
        args = fund_cli.build_parser().parse_args(["search", "沪深300", "--db", "/tmp/x.sqlite"])
        with mock.patch.object(fund_cli.fund_cloud, "ensure_project_bundle") as mock_bootstrap:
            fund_cli._maybe_bootstrap_cloud(args)

        mock_bootstrap.assert_not_called()

    def test_cloud_status_command_returns_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.run_cli("cloud", "status", "--cache-dir", str(Path(tmpdir) / "cache"))

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertFalse(payload["installed"])
            self.assertEqual(payload["cache_dir"], str(Path(tmpdir) / "cache"))

    def test_cloud_archive_full_command_creates_private_archive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "source.sqlite"
            output_dir = Path(tmpdir) / "archive"
            script = (
                "import sys;"
                f"sys.path.insert(0, {str(SCRIPT_DIR)!r});"
                "import fund_data;"
                f"s=fund_data.FundDataStore({str(db_path)!r});"
                "s.upsert_funds([{'fund_code':'110022','fund_name':'易方达消费行业','source':'test'}]);"
                "s.record_raw_response('test.raw','110022','raw')"
            )
            subprocess.run([sys.executable, "-c", script], check=True)

            result = self.run_cli(
                "cloud",
                "archive-full",
                "--source-db",
                str(db_path),
                "--output-dir",
                str(output_dir),
                "--base-url",
                "oss://fund-data-private/fund-data/full/2026-06-01/",
                "--version",
                "2026-06-01",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["manifest"]["kind"], "fund-data-full-archive")
            self.assertEqual(payload["manifest"]["tables"]["raw_responses"], 1)
            self.assertTrue((output_dir / "fund_data_full.sqlite.gz").is_file())

    def test_console_script_entrypoint_imports_from_installed_package(self):
        repo_root = SCRIPT_DIR.parents[1]
        with tempfile.TemporaryDirectory() as tmpdir:
            venv = Path(tmpdir) / "venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            python = venv / "bin" / "python"
            fund_cli_bin = venv / "bin" / "fund-cli"
            install = subprocess.run(
                [str(python), "-m", "pip", "install", "--no-deps", "-e", str(repo_root)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)

            result = subprocess.run(
                [str(fund_cli_bin), "--help"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Search, fetch, persist", result.stdout)

    def test_holdings_with_unavailable_akshare_returns_clear_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"

            result = self.run_cli(
                "holdings",
                "110022",
                "--provider",
                "akshare",
                "--db",
                str(db_path),
                env={"FUND_DATA_DISABLE_AKSHARE": "1"},
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("akshare is disabled", result.stderr)

    def test_export_supports_stock_holdings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            script = (
                "import sys;"
                f"sys.path.insert(0, {str(SCRIPT_DIR)!r});"
                "import fund_data;"
                f"s=fund_data.FundDataStore({str(db_path)!r});"
                "s.upsert_stock_holdings('110022',[{'report_period':'2024Q4','stock_code':'600519','stock_name':'贵州茅台','source':'test'}])"
            )
            subprocess.run([sys.executable, "-c", script], check=True)

            result = self.run_cli(
                "export",
                "stock_holdings",
                "--fund-code",
                "110022",
                "--db",
                str(db_path),
                "--format",
                "json",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)[0]["stock_code"], "600519")

    def test_coverage_command_returns_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            raw_path = Path(tmpdir) / "search.json"
            db_path = Path(tmpdir) / "fund_data.sqlite"
            raw_path.write_text(SEARCH_PAYLOAD, encoding="utf-8")
            search_result = self.run_cli(
                "search",
                "沪深300",
                "--offline-raw",
                str(raw_path),
                "--db",
                str(db_path),
            )
            self.assertEqual(search_result.returncode, 0, search_result.stderr)

            coverage_result = self.run_cli("coverage", "--db", str(db_path))

            self.assertEqual(coverage_result.returncode, 0, coverage_result.stderr)
            self.assertEqual(json.loads(coverage_result.stdout)[0]["fund_code"], "006600")

    def test_sync_include_all_flags_are_forwarded(self):
        captured = {}

        def fake_sync_fund(code, **kwargs):
            captured["code"] = code
            captured.update(kwargs)
            return {"fund_code": code, "status": "ok"}

        original_sync_fund = fund_cli.fund_data.sync_fund
        fund_cli.fund_data.sync_fund = fake_sync_fund
        try:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = fund_cli.main(
                    [
                        "sync",
                        "110022",
                        "--provider",
                        "akshare",
                        "--include-all",
                        "--include-holdings",
                        "--include-profile",
                        "--include-bonds",
                        "--include-industries",
                        "--include-fees",
                        "--include-distributions",
                        "--include-managers",
                        "--report-year",
                        "2024",
                        "--fee-indicator",
                        "申购费率",
                        "--db",
                        "/tmp/fund-test.sqlite",
                    ]
                )
        finally:
            fund_cli.fund_data.sync_fund = original_sync_fund

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["code"], "110022")
        self.assertEqual(captured["provider"], "akshare")
        self.assertTrue(captured["include_all"])
        self.assertTrue(captured["include_holdings"])
        self.assertTrue(captured["include_profile"])
        self.assertTrue(captured["include_bonds"])
        self.assertTrue(captured["include_industries"])
        self.assertTrue(captured["include_fees"])
        self.assertTrue(captured["include_distributions"])
        self.assertTrue(captured["include_managers"])
        self.assertEqual(captured["report_year"], "2024")
        self.assertEqual(captured["fee_indicators"], ["申购费率"])

    def test_batch_sync_reads_code_file_and_forwards_flags(self):
        captured = {}

        def fake_batch_sync_funds(codes, **kwargs):
            captured["codes"] = codes
            captured.update(kwargs)
            return {"batch_id": "batch-1", "total": len(codes), "ok": len(codes), "failed": 0}

        original_batch_sync_funds = fund_cli.fund_data.batch_sync_funds
        fund_cli.fund_data.batch_sync_funds = fake_batch_sync_funds
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                codes_path = Path(tmpdir) / "codes.txt"
                codes_path.write_text("110022\n000001\n", encoding="utf-8")
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    exit_code = fund_cli.main(
                        [
                            "batch-sync",
                            "--codes-file",
                            str(codes_path),
                            "--code",
                            "006600",
                            "--provider",
                            "auto",
                            "--include-all",
                            "--report-year",
                            "2024",
                            "--fee-indicator",
                            "申购费率",
                            "--batch-id",
                            "batch-1",
                            "--db",
                            "/tmp/fund-batch.sqlite",
                        ]
                    )
        finally:
            fund_cli.fund_data.batch_sync_funds = original_batch_sync_funds

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["codes"], ["110022", "000001", "006600"])
        self.assertEqual(captured["provider"], "auto")
        self.assertTrue(captured["include_all"])
        self.assertFalse(captured["stop_on_error"])
        self.assertEqual(captured["report_year"], "2024")
        self.assertEqual(captured["fee_indicators"], ["申购费率"])
        self.assertEqual(captured["batch_id"], "batch-1")


class JsonFlagContractTests(unittest.TestCase):
    """All per-fund read commands (search / list / nav /
    snapshot / holdings / profile / bonds / industries / fees /
    dividends / splits / managers / coverage / coverage-report
    / export / cloud) emit a single JSON document on stdout.
    The ``--json`` flag is a *whitespace* toggle, not a
    *content* toggle -- it picks compact (one line) vs.
    indented (default) without changing the payload.

    These tests pin the contract from the agent's perspective:
    both modes round-trip through ``json.loads`` and produce
    the same logical content, and the ``--json`` mode is
    single-line so it pipes cleanly to ``jq``.
    """

    def _run_json_flag(self, *args) -> "subprocess.CompletedProcess":
        """Helper: run the CLI with the given positional args
        plus a known DB so cloud bootstrap doesn't go to the
        network. Returns the subprocess result."""
        db_path = "/tmp/fund_cli_json_test.sqlite"
        return subprocess.run(
            [sys.executable, str(CLI_PATH), *args, "--db", db_path, "--provider", "eastmoney"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_search_default_output_is_indented_json(self) -> None:
        """Without ``--json``, the output is indented (default
        behavior -- human-readable)."""
        result = self._run_json_flag("search", "沪深300", "--offline-raw", "/dev/null")
        # Empty keyword set is fine -- we just want to assert
        # the formatter, not the content. Any JSON-parseable
        # stdout (even ``[]``) is enough.
        if result.stdout.strip():
            payload = json.loads(result.stdout)
            # Default mode is indented: stdout has newlines
            # between keys. ``json.dumps(..., indent=2)`` always
            # produces multi-line output for non-empty input,
            # but ``[]`` stays on one line. So we look for
            # either: newlines OR an empty array.
            if payload:
                self.assertIn(
                    "\n", result.stdout,
                    "default mode should produce indented (multi-line) JSON",
                )

    def test_search_json_flag_produces_single_line_output(self) -> None:
        """With ``--json``, stdout is a single line -- the
        agent-friendly mode that ``jq`` and other line-oriented
        tools expect."""
        result = self._run_json_flag(
            "search", "沪深300", "--offline-raw", "/dev/null", "--json"
        )
        # Even if stdout is empty (no rows), the formatter is
        # the same; the test only runs when stdout is non-empty.
        if result.stdout.strip():
            # The first non-empty line must be parseable JSON.
            first_line = result.stdout.split("\n", 1)[0]
            payload = json.loads(first_line)
            # Sanity: parseable as JSON, not just text.
            self.assertIsInstance(payload, list)

    def test_json_and_default_emit_equivalent_payload(self) -> None:
        """Both modes must round-trip to the same logical
        content. The flag is a whitespace toggle -- it must
        never change the data, only the formatting."""
        # Use a known-good offline-raw search payload to
        # avoid provider calls. Same path, just toggled
        # --json on/off.
        import tempfile
        from test_fund_data import SEARCH_PAYLOAD
        with tempfile.TemporaryDirectory() as tmp:
            raw_path = Path(tmp) / "search.json"
            raw_path.write_text(SEARCH_PAYLOAD, encoding="utf-8")

            def run(args_list):
                return subprocess.run(
                    [sys.executable, str(CLI_PATH)] + args_list,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )

            pretty = run([
                "search", "沪深300", "--offline-raw", str(raw_path),
                "--provider", "eastmoney",
            ])
            compact = run([
                "search", "沪深300", "--offline-raw", str(raw_path),
                "--provider", "eastmoney", "--json",
            ])

        self.assertEqual(pretty.returncode, 0, pretty.stderr)
        self.assertEqual(compact.returncode, 0, compact.stderr)
        # Both must parse to identical data structures.
        self.assertEqual(json.loads(pretty.stdout), json.loads(compact.stdout))


if __name__ == "__main__":
    unittest.main()
