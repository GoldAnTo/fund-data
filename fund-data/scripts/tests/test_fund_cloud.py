import gzip
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_cloud  # noqa: E402

import fund_data  # noqa: E402
import fund_cli  # noqa: E402


class FundCloudBundleTests(unittest.TestCase):
    def _source_db(self, tmpdir: str) -> Path:
        db_path = Path(tmpdir) / "source.sqlite"
        store = fund_data.FundDataStore(db_path)
        store.upsert_funds(
            [
                {
                    "fund_code": "110022",
                    "fund_name": "易方达消费行业",
                    "fund_type": "混合型",
                    "source": "test",
                }
            ]
        )
        store.upsert_nav_history(
            "110022",
            [
                {
                    "nav_date": "2026-05-29",
                    "unit_nav": 3.14,
                    "accumulated_nav": 4.2,
                    "source": "test",
                }
            ],
        )
        store.record_raw_response("test.raw", "110022", "x" * 1024)
        store.record_sync_failure(
            batch_id="batch-1",
            operation="batch-sync",
            fund_code="110022",
            provider="test",
            message="kept out of query bundle",
        )
        return db_path

    def test_build_bundle_creates_query_database_without_raw_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_db = self._source_db(tmpdir)
            release_dir = Path(tmpdir) / "dist" / "releases" / "2026-06-01"

            result = fund_cloud.build_bundle(
                source_db=source_db,
                output_dir=release_dir,
                base_url="https://example.com/fund-data/releases/2026-06-01/",
                version="2026-06-01",
            )

            query_gz = release_dir / "fund_data_query.sqlite.gz"
            query_db = release_dir / "fund_data_query.sqlite"
            manifest_path = release_dir / "manifest.json"
            self.assertTrue(query_gz.is_file())
            self.assertTrue(query_db.is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertEqual(result["manifest"]["version"], "2026-06-01")
            self.assertEqual(result["manifest"]["tables"]["funds"], 1)
            self.assertEqual(result["manifest"]["tables"]["nav_history"], 1)
            self.assertIn("raw_responses", result["manifest"]["excluded_tables"])
            self.assertEqual(
                result["manifest"]["files"]["query_db"]["url"],
                "https://example.com/fund-data/releases/2026-06-01/fund_data_query.sqlite.gz",
            )

            with closing(sqlite3.connect(query_db)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "select name from sqlite_master where type = 'table'"
                    ).fetchall()
                }
                fund_count = conn.execute("select count(*) from funds").fetchone()[0]
                nav_count = conn.execute("select count(*) from nav_history").fetchone()[0]

            self.assertNotIn("raw_responses", tables)
            self.assertNotIn("sync_failures", tables)
            self.assertEqual(fund_count, 1)
            self.assertEqual(nav_count, 1)

            with gzip.open(query_gz, "rb") as gz:
                self.assertEqual(gz.read(16), query_db.read_bytes()[:16])

    def test_archive_full_creates_private_snapshot_with_raw_responses(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_db = self._source_db(tmpdir)
            archive_dir = Path(tmpdir) / "dist" / "full" / "2026-06-01"

            result = fund_cloud.archive_full(
                source_db=source_db,
                output_dir=archive_dir,
                base_url="oss://fund-data-private/fund-data/full/2026-06-01/",
                version="2026-06-01",
            )

            full_db = archive_dir / "fund_data_full.sqlite"
            full_gz = archive_dir / "fund_data_full.sqlite.gz"
            manifest_path = archive_dir / "manifest.json"
            self.assertTrue(full_db.is_file())
            self.assertTrue(full_gz.is_file())
            self.assertTrue((archive_dir / "fund_data_full.sqlite.gz.sha256").is_file())
            self.assertTrue(manifest_path.is_file())
            self.assertEqual(result["manifest"]["kind"], "fund-data-full-archive")
            self.assertEqual(result["manifest"]["files"]["full_db"]["url"], None)
            self.assertEqual(
                result["manifest"]["files"]["full_db"]["oss_uri"],
                "oss://fund-data-private/fund-data/full/2026-06-01/fund_data_full.sqlite.gz",
            )
            self.assertEqual(result["manifest"]["tables"]["funds"], 1)
            self.assertEqual(result["manifest"]["tables"]["raw_responses"], 1)
            with closing(sqlite3.connect(full_db)) as conn:
                raw_count = conn.execute("select count(*) from raw_responses").fetchone()[0]
                failure_count = conn.execute("select count(*) from sync_failures").fetchone()[0]
            self.assertEqual(raw_count, 1)
            self.assertEqual(failure_count, 1)

            with gzip.open(full_gz, "rb") as gz:
                self.assertEqual(gz.read(16), full_db.read_bytes()[:16])

    def test_pull_bundle_downloads_verifies_and_installs_current_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_db = self._source_db(tmpdir)
            release_dir = Path(tmpdir) / "remote" / "releases" / "2026-06-01"
            build = fund_cloud.build_bundle(
                source_db=source_db,
                output_dir=release_dir,
                base_url=release_dir.as_uri() + "/",
                version="2026-06-01",
            )
            cache_dir = Path(tmpdir) / "cache"

            result = fund_cloud.pull_bundle(
                build["manifest_path"].as_uri(),
                cache_dir=cache_dir,
            )

            self.assertEqual(result["version"], "2026-06-01")
            current_db = Path(result["db_path"])
            self.assertTrue(current_db.is_file())
            with closing(sqlite3.connect(current_db)) as conn:
                row = conn.execute(
                    "select fund_name from funds where fund_code = '110022'"
                ).fetchone()
            self.assertEqual(row[0], "易方达消费行业")

            status = fund_cloud.status(cache_dir=cache_dir)
            self.assertTrue(status["installed"])
            self.assertEqual(status["version"], "2026-06-01")
            self.assertEqual(Path(status["db_path"]), current_db)
            with mock.patch.dict(os.environ, {"FUND_DATA_CACHE_DIR": str(cache_dir)}, clear=False):
                self.assertEqual(fund_data.default_db_path(), current_db)

    def test_pull_bundle_rejects_sha256_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_db = self._source_db(tmpdir)
            release_dir = Path(tmpdir) / "remote" / "releases" / "2026-06-01"
            build = fund_cloud.build_bundle(
                source_db=source_db,
                output_dir=release_dir,
                base_url=release_dir.as_uri() + "/",
                version="2026-06-01",
            )
            (release_dir / "fund_data_query.sqlite.gz").write_bytes(b"tampered")

            with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
                fund_cloud.pull_bundle(
                    build["manifest_path"].as_uri(), cache_dir=Path(tmpdir) / "cache"
                )

    def test_status_reports_missing_cache_without_creating_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"

            result = fund_cloud.status(cache_dir=cache_dir)

            self.assertFalse(result["installed"])
            self.assertEqual(result["cache_dir"], str(cache_dir))
            self.assertFalse(cache_dir.exists())

    def test_default_manifest_url_uses_project_oss_configuration(self):
        self.assertEqual(
            fund_cloud.default_manifest_url(),
            "https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json",
        )

    def test_ensure_project_bundle_pulls_default_oss_when_no_cache_or_db_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir) / "cache"
            payload = {
                "installed": True,
                "cache_dir": str(cache_dir),
                "db_path": str(cache_dir / "releases" / "v1" / fund_cloud.QUERY_DB_NAME),
                "version": "v1",
            }
            with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
                fund_cloud, "pull_bundle", return_value=payload
            ) as mock_pull:
                result = fund_cloud.ensure_project_bundle(cache_dir=cache_dir)

        mock_pull.assert_called_once_with(
            fund_cloud.default_manifest_url(),
            cache_dir=cache_dir,
        )
        self.assertTrue(result["installed"])
        self.assertEqual(result["source"], "oss")
        self.assertEqual(result["fallback"], None)

    def test_ensure_project_bundle_skips_when_explicit_db_is_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ, {"FUND_DATA_DB": "/tmp/explicit.sqlite"}, clear=True
        ), mock.patch.object(fund_cloud, "pull_bundle") as mock_pull:
            result = fund_cloud.ensure_project_bundle(cache_dir=Path(tmpdir) / "cache")

        mock_pull.assert_not_called()
        self.assertFalse(result["installed"])
        self.assertEqual(result["skipped"], "FUND_DATA_DB is set")

    def test_ensure_project_bundle_returns_api_fallback_when_oss_pull_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir, mock.patch.dict(
            os.environ, {}, clear=True
        ), mock.patch.object(
            fund_cloud, "pull_bundle", side_effect=OSError("network unavailable")
        ):
            result = fund_cloud.ensure_project_bundle(cache_dir=Path(tmpdir) / "cache")

        self.assertFalse(result["installed"])
        self.assertEqual(result["fallback"], "api")
        self.assertIn("network unavailable", result["error"])


class CloudCliSubcommandTests(unittest.TestCase):
    """``fund_cli.py cloud <cmd>`` is the agent's stable
    entry point for inspecting / building / pulling the cloud
    bundle. These tests lock down the stdout / --output /
    exit-code contract for the four subcommands and pin the
    status subcommand's top-level JSON schema so a refactor
    cannot silently rename ``installed`` / ``version`` /
    ``db_path`` / ``sha256``."""

    def _run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "fund_cli.py"), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_status_emits_valid_json_with_stable_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            result = self._run_cli(
                "cloud", "status", "--cache-dir", str(cache_dir)
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        # Pin the schema. The agent branches on these four keys
        # for "is the cloud cache healthy / installed / current
        # version / what path does it live at".
        self.assertIn("installed", payload)
        self.assertIn("cache_dir", payload)
        self.assertIn("db_path", payload)
        self.assertIn("version", payload)
        # When the cache is empty, these are exactly None / False.
        self.assertFalse(payload["installed"])
        self.assertIsNone(payload["db_path"])
        self.assertIsNone(payload["version"])

    def test_status_writes_to_output_file_and_leaves_stdout_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "cache"
            out_path = Path(tmp) / "report.json"
            result = self._run_cli(
                "cloud",
                "status",
                "--cache-dir",
                str(cache_dir),
                "--output",
                str(out_path),
            )
            # Diagnostic: print everything on a failure so the
            # next agent does not have to guess what the child
            # subprocess saw.
            self.assertEqual(
                result.returncode,
                0,
                f"returncode={result.returncode}\n"
                f"stdout={result.stdout!r}\nstderr={result.stderr!r}\n"
                f"out_path.exists()={out_path.exists()}\n"
                f"out_path={out_path}",
            )
            self.assertEqual(result.stdout, "")
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertIn("installed", payload)
            self.assertFalse(payload["installed"])

    def test_pull_without_manifest_url_uses_project_default_manifest(self):
        payload = {
            "installed": True,
            "cache_dir": "/tmp/fund-data-cache",
            "db_path": "/tmp/fund-data-cache/query.sqlite",
            "version": "v1",
        }
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            fund_cli.fund_cloud, "pull_bundle", return_value=payload
        ) as mock_pull, mock.patch("sys.stdout", new=io.StringIO()) as stdout:
            exit_code = fund_cli.main(["cloud", "pull"])

        self.assertEqual(exit_code, 0)
        mock_pull.assert_called_once_with(fund_cloud.default_manifest_url(), cache_dir=None)
        self.assertEqual(json.loads(stdout.getvalue()), payload)

    def test_build_bundle_end_to_end_with_output_file(self):
        # Confirm the --output flag round-trips through the CLI
        # by patching ``fund_cloud.build_bundle`` at the import
        # site (the test runner already has the package on the
        # path, so a child subprocess sees the same module).
        # Skipping the real bundle keeps the test independent of
        # the SQLite ATTACH locking edge cases that an
        # interactive run of build_bundle can hit.
        import fund_data
        import fund_cloud

        # Reuse the real fund_data db (a small per-test copy of
        # an empty SQLite would be ideal, but a 3.8 GB production
        # db is fine when we never read it). The mocked
        # build_bundle never opens it.
        source_db = fund_data.DEFAULT_DB_PATH

        fake_result = {
            "release_dir": "/tmp/release",
            "manifest_path": "/tmp/release/manifest.json",
            "query_db_path": "/tmp/release/fund_data_query.sqlite.gz",
            "query_archive_path": "/tmp/release/fund_data_query.sqlite.gz.sha256",
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_dir = tmp_path / "release"
            manifest_output = tmp_path / "manifest.json"
            report_path = tmp_path / "build_report.json"
            # Build a tiny launcher that uses the same Python
            # interpreter but patches fund_cloud.build_bundle
            # before calling fund_cli.main. This way the CLI's
            # --output flag is exercised against a real
            # subprocess without paying for a full bundle build.
            launcher = tmp_path / "launcher.py"
            launcher.write_text(
                "import json\n"
                "import sys\n"
                "from unittest import mock\n"
                "sys.path.insert(0, " + repr(str(SCRIPT_DIR.resolve())) + ")\n"
                "import fund_cloud\n"
                "import fund_cli\n"
                "with mock.patch.object(\n"
                "    fund_cloud, \"build_bundle\",\n"
                "    return_value=json.load(open(sys.argv[1])),\n"
                "):\n"
                "    sys.exit(fund_cli.main(sys.argv[2:]))\n",
                encoding="utf-8",
            )
            result_path = tmp_path / "fake_result.json"
            result_path.write_text(
                json.dumps(fake_result), encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ,
                {"PYTHONPATH": str(SCRIPT_DIR.resolve().parent)},
                clear=False,
            ):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(launcher),
                        str(result_path),
                        "cloud",
                        "build-bundle",
                        "--source-db",
                        str(source_db),
                        "--output-dir",
                        str(output_dir),
                        "--base-url",
                        "https://example.com/fund-data/releases/2026-06-01/",
                        "--version",
                        "2026-06-01",
                        "--manifest-output",
                        str(manifest_output),
                        "--output",
                        str(report_path),
                    ],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=os.environ.copy(),
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            # Pin the success envelope: the agent needs the
            # release_dir, manifest_path, and query_db_path to
            # then upload to OSS.
            self.assertIn("release_dir", payload)
            self.assertIn("manifest_path", payload)
            self.assertIn("query_db_path", payload)
            self.assertEqual(payload["release_dir"], fake_result["release_dir"])

    def test_upload_dry_run_reports_planned_artifacts_without_calling_ossutil(self):
        # ``cloud upload --dry-run`` must build the response
        # envelope (version / bucket / region / prefix /
        # manifest_url / uploaded[]) without shelling out to
        # ossutil. That way an agent can preview the blast
        # radius in CI before flipping the switch.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            release_dir = tmp_path / "2026-06-02"
            release_dir.mkdir()
            (release_dir / fund_cloud.QUERY_ARCHIVE_NAME).write_bytes(
                b"fake-gz-payload"
            )
            (release_dir / f"{fund_cloud.QUERY_ARCHIVE_NAME}.sha256").write_text(
                "deadbeef  fund_data_query.sqlite.gz\n", encoding="utf-8"
            )
            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text("{}", encoding="utf-8")
            with mock.patch.object(
                fund_cloud, "_ossutil_upload"
            ) as mock_upload, mock.patch.object(
                fund_cloud.subprocess, "run"
            ) as mock_run:
                result = fund_cloud.upload_to_oss(
                    release_dir=release_dir,
                    manifest_path=manifest_path,
                    dry_run=True,
                )
            # Dry-run still routes through the _ossutil_upload
            # helper (it is the place where the ``if dry_run:
            # return`` short-circuit lives), but the
            # underlying ``subprocess.run`` that talks to
            # ossutil is never invoked. The CLI consumer never
            # sees a real upload.
            self.assertEqual(mock_upload.call_count, 3)
            mock_run.assert_not_called()
            payload = result.to_dict()
            # Pin the top-level schema so a future refactor
            # cannot silently rename any of these keys -- the
            # CI gate branches on every one of them.
            self.assertEqual(set(payload.keys()), {
                "version",
                "bucket",
                "region",
                "prefix",
                "manifest_url",
                "uploaded",
                "dry_run",
            })
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["version"], "2026-06-02")
            self.assertEqual(payload["bucket"], fund_cloud.DEFAULT_BUCKET)
            self.assertEqual(payload["region"], fund_cloud.DEFAULT_REGION)
            # The .gz + sha256 + manifest = 3 artifacts.
            self.assertEqual(len(payload["uploaded"]), 3)
            # And the manifest URL is the public HTTPS one, not
            # the oss:// URI.
            self.assertIn("https://", payload["manifest_url"])
            self.assertNotIn("oss://", payload["manifest_url"])

    def test_upload_missing_release_dir_raises(self):
        # The agent must get a clear FileNotFoundError before
        # we shell out to ossutil -- otherwise the response
        # envelope would silently land at the wrong prefix.
        with self.assertRaises(FileNotFoundError):
            fund_cloud.upload_to_oss(
                release_dir="/nonexistent/release-2026-06-02",
                dry_run=True,
            )

    def test_upload_missing_gz_artifact_raises(self):
        # A release dir without the gzip db (e.g. a half-built
        # build-bundle) is an error -- never silently upload
        # the .sha256 alone.
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "2026-06-02"
            release_dir.mkdir()
            (release_dir / f"{fund_cloud.QUERY_ARCHIVE_NAME}.sha256").write_text(
                "deadbeef  fund_data_query.sqlite.gz\n", encoding="utf-8"
            )
            with self.assertRaises(FileNotFoundError):
                fund_cloud.upload_to_oss(
                    release_dir=release_dir,
                    dry_run=True,
                )


if __name__ == "__main__":
    unittest.main()
