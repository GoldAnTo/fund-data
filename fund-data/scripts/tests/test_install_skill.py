import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys_path = SCRIPT_DIR
import sys

sys.path.insert(0, str(sys_path))

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

        def fake_install_one(target, dest, *, copy, data_mode):
            calls.append((target, dest, copy, data_mode))
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

        self.assertEqual(calls, [("codex", Path("/tmp/fund-data-skill"), True, "copy")])


if __name__ == "__main__":
    unittest.main()
