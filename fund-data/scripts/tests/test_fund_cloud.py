import gzip
import os
import sqlite3
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


if __name__ == "__main__":
    unittest.main()
