import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys_path = SCRIPT_DIR
import sys

sys.path.insert(0, str(sys_path))

import fund_data  # noqa: E402

from scripts import install_skill  # noqa: E402


class CopyIntoTests(unittest.TestCase):
    def test_copy_into_excludes_generated_runtime_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src"
            dst = Path(tmpdir) / "dst"
            (src / "data" / "backfill_logs").mkdir(parents=True)
            (src / "scripts" / "__pycache__").mkdir(parents=True)
            (src / "SKILL.md").write_text("---\nname: fund-data\n---\n", encoding="utf-8")
            (src / "data" / "fund_codes_sample.txt").write_text("110022\n", encoding="utf-8")
            (src / "data" / "fund_data.sqlite").write_text("large db", encoding="utf-8")
            (src / "data" / "backfill_state.json").write_text("{}", encoding="utf-8")
            (src / "data" / "backfill_logs" / "run.log").write_text("log", encoding="utf-8")
            (src / "scripts" / "fund_cli.py").write_text("print('ok')\n", encoding="utf-8")
            (src / "scripts" / "__pycache__" / "fund_cli.pyc").write_bytes(b"pyc")
            (src / ".DS_Store").write_text("finder", encoding="utf-8")

            # Existing stale artifacts in the destination should be removed
            # during refresh, otherwise `install --copy` leaves a huge DB in
            # the installed skill forever.
            (dst / "data" / "backfill_logs").mkdir(parents=True)
            (dst / "data" / "fund_data.sqlite").write_text("old db", encoding="utf-8")
            (dst / "data" / "backfill_logs" / "old.log").write_text("old", encoding="utf-8")

            install_skill._copy_into(src, dst, data_mode="none")

            self.assertTrue((dst / "SKILL.md").is_file())
            self.assertTrue((dst / "data" / "fund_codes_sample.txt").is_file())
            self.assertTrue((dst / "scripts" / "fund_cli.py").is_file())
            self.assertFalse((dst / "data" / "fund_data.sqlite").exists())
            self.assertFalse((dst / "data" / "backfill_state.json").exists())
            self.assertFalse((dst / "data" / "backfill_logs").exists())
            self.assertFalse((dst / "scripts" / "__pycache__").exists())
            self.assertFalse((dst / ".DS_Store").exists())

    def test_copy_into_can_include_sqlite_database_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src"
            dst = Path(tmpdir) / "dst"
            (src / "data").mkdir(parents=True)
            (src / "SKILL.md").write_text("---\nname: fund-data\n---\n", encoding="utf-8")
            db_path = src / "data" / "fund_data.sqlite"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE funds (fund_code TEXT PRIMARY KEY)")
                conn.execute("INSERT INTO funds VALUES ('110022')")
            (src / "data" / "fund_data.sqlite-wal").write_text("wal", encoding="utf-8")
            (src / "data" / "backfill_state.json").write_text("{}", encoding="utf-8")

            install_skill._copy_into(src, dst, data_mode="copy")

            copied_db = dst / "data" / "fund_data.sqlite"
            self.assertTrue(copied_db.is_file())
            with sqlite3.connect(copied_db) as conn:
                rows = conn.execute("SELECT fund_code FROM funds").fetchall()
            self.assertEqual(rows, [("110022",)])
            self.assertFalse((dst / "data" / "fund_data.sqlite-wal").exists())
            self.assertFalse((dst / "data" / "backfill_state.json").exists())

    def test_data_mode_must_be_explicit(self):
        with self.assertRaises(ValueError):
            install_skill._copy_into(Path("/tmp/nope"), Path("/tmp/nope2"), data_mode="bundle")

    def test_include_data_cli_enables_copy_data_mode(self):
        calls = []

        def fake_install_one(target, dest, *, copy, data_mode, scrub_raw):
            calls.append((target, dest, copy, data_mode, scrub_raw))
            return "  [codex] OK"

        with (
            mock.patch.object(install_skill, "_validate_source"),
            mock.patch.object(
                install_skill,
                "_resolve_targets",
                return_value=[("codex", Path("/tmp/fund-data-skill"))],
            ),
            mock.patch.object(install_skill, "_install_one", side_effect=fake_install_one),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(
                install_skill.main(["install", "--target", "codex", "--include-data"]),
                0,
            )

        # scrub_raw defaults to False; --scrub-raw-responses is
        # opt-in because dropping raw_responses is destructive
        # on the destination and the IP-leak only matters when
        # the snapshot leaves the local machine.
        self.assertEqual(
            calls,
            [("codex", Path("/tmp/fund-data-skill"), True, "copy", False)],
        )

    def test_copy_sqlite_includes_wal_pages(self):
        """A production ``fund_data.sqlite`` sits on journal_mode=WAL
        with an open writer. The snapshot must include rows that
        are still in the WAL (i.e. the destination must be consistent
        with the source, not stale-checkpoint-only).

        Earlier the test suite called ``_copy_sqlite_database`` on a
        fresh DB with no live writer, so the WAL-page codepath was
        never exercised. This test opens two connections on the
        source, writes through one, then triggers the copy while the
        other is still alive.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src.sqlite"
            dst = Path(tmpdir) / "dst.sqlite"

            # Bootstrap a source DB on WAL with one table, one row.
            with sqlite3.connect(src) as conn:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute(
                    "CREATE TABLE waltest (k TEXT PRIMARY KEY, v TEXT)"
                )
                conn.execute(
                    "INSERT INTO waltest VALUES ('k1', 'checkpointed')"
                )
                conn.commit()

            # Open a second connection in another writer so the WAL
            # has at least one uncheckpointed page by the time the
            # copy runs. SQLite WAL auto-checkpoints at ~1000 pages;
            # one tiny row is well below that.
            live = sqlite3.connect(src, timeout=30.0)
            live.execute(
                "INSERT INTO waltest VALUES ('k2', 'wal-only')"
            )
            live.commit()

            install_skill._copy_sqlite_database(src, dst)

            # The copy must carry the WAL-only row, not just the
            # checkpointed one. Close the live writer before
            # asserting so any pending WAL is durably committed.
            live.close()
            with sqlite3.connect(dst) as conn:
                rows = {
                    k: v
                    for k, v in conn.execute("SELECT k, v FROM waltest")
                }
            self.assertEqual(rows, {"k1": "checkpointed", "k2": "wal-only"})

    def test_scrub_raw_responses_drops_table(self):
        """With ``scrub_raw=True`` the snapshot must have its
        ``raw_responses`` table emptied. The table itself stays
        (so existing queries that select from it still type-check),
        but every row is gone.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src.sqlite"
            dst = Path(tmpdir) / "dst.sqlite"
            fund_data.FundDataStore(str(src)).ensure_schema()
            with sqlite3.connect(src) as conn:
                conn.execute(
                    """INSERT INTO raw_responses
                          (source, request_key, fetched_at, raw_text)
                       VALUES (?, ?, ?, ?)""",
                    (
                        "eastmoney",
                        "demo-key",
                        "2024-01-01T00:00:00+00:00",
                        "X-Forwarded-For: 203.0.113.1",
                    ),
                )
                conn.commit()

            install_skill._copy_sqlite_database(src, dst, scrub_raw=True)

            with sqlite3.connect(dst) as conn:
                # Table still exists, but is empty.
                count = conn.execute(
                    "SELECT COUNT(*) FROM raw_responses"
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_include_data_warns_about_ip_leak(self):
        """`--include-data` without `--scrub-raw-responses` must print
        a `::warning::` GitHub Actions–style note pointing the user
        at the IP-leak risk documented in SECURITY.md. The note has
        to be impossible to miss so a CI publish can't silently
        ship raw upstream headers.
        """
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(install_skill, "_validate_source"),
            mock.patch.object(
                install_skill,
                "_resolve_targets",
                return_value=[("codex", Path(tmpdir) / "codex")],
            ),
            mock.patch.object(install_skill, "_install_one", return_value="ok"),
        ):
            # SKILL_DIR_FOR_TARGETS is a module-level constant,
            # not a function, so point the install path at a
            # throwaway skill dir instead of touching the real
            # repo skill on disk.
            fake_skill = Path(tmpdir) / "skill"
            fake_skill.mkdir()
            (fake_skill / "SKILL.md").write_text("---\n", encoding="utf-8")
            with mock.patch.object(
                install_skill, "SKILL_DIR_FOR_TARGETS", fake_skill
            ):
                buf = io.StringIO()
                with mock.patch("sys.stdout", buf):
                    install_skill.main(
                        ["install", "--target", "codex", "--include-data"]
                    )
            output = buf.getvalue()
            self.assertIn("::warning::", output)
            self.assertIn("raw_responses", output)
            self.assertIn("--scrub-raw-responses", output)
            # Sanity: the scrub flag suppresses the warning.
            buf.truncate(0)
            buf.seek(0)
            with mock.patch("sys.stdout", buf):
                install_skill.main(
                    [
                        "install",
                        "--target",
                        "codex",
                        "--include-data",
                        "--scrub-raw-responses",
                    ]
                )
            self.assertNotIn("::warning::", buf.getvalue())


class StatusTests(unittest.TestCase):
    """Pin the install-skill `status` action so silent regressions
    don't make the agent start trusting a stale local install.

    The status line is what an agent (or a human) reads first
    when something is "weird" with the skill — wrong version,
    copy doesn't match repo, symlink broken, etc. We test the
    four outcomes that matter: MISSING, STALE_COPY (version),
    STALE_COPY (hash), INSTALLED.
    """

    def _write_skill(self, path: Path, *, version: str | None = None, body: str = "body") -> None:
        path.mkdir(parents=True, exist_ok=True)
        front = "---\n"
        if version is not None:
            front += f"version: {version}\n"
        front += "---\n"
        (path / "SKILL.md").write_text(front + body + "\n", encoding="utf-8")

    def test_status_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "codex"
            self.assertIn("MISSING", install_skill._status_one("codex", missing))

    def test_status_installed_reports_version(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            installed = Path(tmpdir) / "codex"
            self._write_skill(installed, version="0.2.0")
            with mock.patch.object(
                install_skill, "SKILL_MARKER", installed / "SKILL.md"
            ):
                self._write_skill(installed, version="0.2.0")
                line = install_skill._status_one("codex", installed)
            self.assertIn("INSTALLED", line)
            self.assertIn("v0.2.0", line)

    def test_status_stale_copy_when_version_differs(self):
        """Source repo is on 0.2.0, agent install is on 0.1.0 — flag it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            installed = Path(tmpdir) / "codex"
            self._write_skill(installed, version="0.1.0")
            source = Path(tmpdir) / "source"
            self._write_skill(source, version="0.2.0")
            with mock.patch.object(
                install_skill, "SKILL_MARKER", source / "SKILL.md"
            ):
                line = install_skill._status_one("codex", installed)
            self.assertIn("STALE_COPY", line)
            self.assertIn("v0.1.0", line)
            self.assertIn("v0.2.0", line)
            self.assertIn("--copy", line)

    def test_status_stale_copy_when_hash_differs(self):
        """Versions match but content diverged (e.g. uncommitted edit
        on either side) — still flag, but show the hash mismatch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            installed = Path(tmpdir) / "codex"
            source = Path(tmpdir) / "source"
            self._write_skill(installed, version="0.2.0", body="installed body")
            self._write_skill(source, version="0.2.0", body="source body (newer)")
            with mock.patch.object(
                install_skill, "SKILL_MARKER", source / "SKILL.md"
            ):
                line = install_skill._status_one("codex", installed)
            self.assertIn("STALE_COPY", line)
            self.assertIn("hash differs", line)

    def test_read_skill_version_handles_missing_file(self):
        self.assertIsNone(install_skill._read_skill_version(Path("/nonexistent/path/SKILL.md")))

    def test_read_skill_version_strips_whitespace_and_skips_comments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_md = Path(tmpdir) / "SKILL.md"
            skill_md.write_text(
                "# top-level comment\n"
                "---\n"
                "name: fund-data\n"
                "version:   0.2.0  \n"
                "---\n",
                encoding="utf-8",
            )
            self.assertEqual(install_skill._read_skill_version(skill_md), "0.2.0")


if __name__ == "__main__":
    unittest.main()
