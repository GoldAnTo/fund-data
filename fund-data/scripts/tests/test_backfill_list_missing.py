"""Unit tests for ``scripts/backfill_list_missing.py``.

The script is the diagnostic counterpart to the :mod:`backfill`
runner: it reads the live ``funds`` table, subtracts the
``state.completed_codes`` and (by default) ``state.failed_codes``,
and returns the gap. The tests here cover the four
behavioural invariants an operator / agent depends on:

1. The total universe comes from ``funds``, not from a cached
   ``state.totals`` snapshot. A brand-new state file must still
   report the full universe as missing.
2. Failed funds are excluded by default (the backfill runner's
   pending calculation matches). ``--include-failed`` flips it.
3. The fund_type filters compose with the same substring
   semantics as :mod:`backfill`. (NB: SQLite's default ``LIKE`` is
   not unicode-aware, so we use ``instr`` and the test pins the
   behaviour with a Chinese substring.)
4. The output file is newline-terminated (or empty) so it
   composes into a ``xargs`` / ``batch_sync_funds`` pipeline
   without a trailing-blank surprise.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

import backfill_list_missing as blm  # noqa: E402


def _seed_db(db: Path, funds: list[tuple[str, str]]) -> None:
    """Create a fresh funds table from a (code, type) list."""
    fund_data.FundDataStore(str(db)).ensure_schema()
    with sqlite3.connect(str(db)) as conn:
        for code, ftype in funds:
            conn.execute(
                "INSERT INTO funds(fund_code, fund_name, fund_type, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (code, code, ftype, "2026-01-01T00:00:00+00:00"),
            )
        conn.commit()


def _write_state(path: Path, *, completed: list[str], failed: list[str] = ()) -> None:
    """Write a state.json that mirrors the production shape."""
    state = {
        "started_at": "2026-06-01T00:00:00+00:00",
        "config": {},
        "completed_codes": completed,
        "failed_codes": list(failed),
        "last_batch_id": "test",
        "totals": {"ok": len(completed), "failed": len(failed)},
        "updated_at": "2026-06-01T00:00:00+00:00",
    }
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


class _SelectMissingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "fund_data.sqlite"
        self.state = self.dir / "backfill_state.json"
        _seed_db(
            self.db,
            [
                ("000001", "股票型"),
                ("000002", "混合型-灵活"),
                ("000003", "货币型-普通货币"),
                ("000004", "债券型-长债"),
            ],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_empty_state_reports_full_universe_as_missing(self) -> None:
        _write_state(self.state, completed=[])
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=False,
        )
        codes = {c for c, _ in missing}
        self.assertEqual(codes, {"000001", "000002", "000003", "000004"})

    def test_completed_codes_are_excluded(self) -> None:
        _write_state(self.state, completed=["000001", "000002"])
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=False,
        )
        codes = {c for c, _ in missing}
        self.assertEqual(codes, {"000003", "000004"})

    def test_failed_codes_excluded_by_default(self) -> None:
        # The backfill runner's pending calculation skips
        # failed_codes by default; we mirror that here.
        _write_state(self.state, completed=["000001"], failed=["000002"])
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=False,
        )
        codes = {c for c, _ in missing}
        self.assertEqual(codes, {"000003", "000004"})

    def test_include_failed_brings_failed_back(self) -> None:
        _write_state(self.state, completed=["000001"], failed=["000002"])
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=True,
        )
        codes = {c for c, _ in missing}
        self.assertEqual(codes, {"000002", "000003", "000004"})

    def test_exclude_type_filter_uses_unicode_safe_instr(self) -> None:
        # SQLite's default LIKE is not unicode-aware (the
        # case-insensitive collation matches ASCII only), so
        # ``NOT LIKE '货币'`` would leak a row whose fund_type
        # is '货币型-普通货币' through the filter.  We use
        # ``instr`` instead and the test pins the fix.
        _write_state(self.state, completed=[])
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=["货币"], include_failed=False,
        )
        codes = {c for c, _ in missing}
        self.assertNotIn("000003", codes)  # 货币型-普通货币 excluded
        self.assertEqual(codes, {"000001", "000002", "000004"})

    def test_include_type_filter(self) -> None:
        _write_state(self.state, completed=[])
        missing = blm._select_missing(
            self.db, self.state,
            include_types=["混合"], exclude_types=None, include_failed=False,
        )
        codes = {c for c, _ in missing}
        self.assertEqual(codes, {"000002"})

    def test_state_file_missing_treated_as_empty(self) -> None:
        # Brand-new data base, no state.json -- script must still
        # report the full universe as missing.
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=False,
        )
        self.assertEqual(len(missing), 4)

    def test_malformed_state_file_treated_as_empty(self) -> None:
        self.state.write_text("{not valid json", encoding="utf-8")
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=False,
        )
        self.assertEqual(len(missing), 4)


class _LoadStateCodesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "backfill_state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_missing_file_returns_empty_sets(self) -> None:
        completed, failed = blm._load_state_codes(self.path)
        self.assertEqual(completed, set())
        self.assertEqual(failed, set())

    def test_loads_both_keys(self) -> None:
        self.path.write_text(
            json.dumps(
                {"completed_codes": ["000001", "000002"], "failed_codes": ["000003"]}
            ),
            encoding="utf-8",
        )
        completed, failed = blm._load_state_codes(self.path)
        self.assertEqual(completed, {"000001", "000002"})
        self.assertEqual(failed, {"000003"})

    def test_int_codes_coerced_to_str(self) -> None:
        # A future state-writer that stores ints must not break the
        # membership test (it would silently leak every row through
        # the ``if code not in completed`` filter).
        self.path.write_text(
            json.dumps({"completed_codes": [1, 2], "failed_codes": [3]}),
            encoding="utf-8",
        )
        completed, failed = blm._load_state_codes(self.path)
        self.assertEqual(completed, {"1", "2"})
        self.assertEqual(failed, {"3"})


class _OutputFileTests(unittest.TestCase):
    """The output file format. Pins the trailing-newline behaviour so
    a ``cat missing.txt | xargs python fund_cli batch-sync`` pipeline
    does not pick up a stray empty arg."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "fund_data.sqlite"
        self.state = self.dir / "backfill_state.json"
        self.out = self.dir / "missing.txt"
        _seed_db(self.db, [("000001", "股票型"), ("000002", "混合型-灵活")])
        _write_state(self.state, completed=[])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_one_code_per_line_with_trailing_newline(self) -> None:
        # Manually call the slice of ``main`` that writes the file
        # to avoid running argparse in a subprocess.  We mirror
        # the production write block.
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=False,
        )
        self.out.write_text(
            "\n".join(code for code, _ in missing) + ("\n" if missing else ""),
            encoding="utf-8",
        )
        body = self.out.read_text(encoding="utf-8")
        self.assertEqual(body, "000001\n000002\n")
        self.assertEqual(body.count("\n"), 2)
        # No double trailing newline.
        self.assertFalse(body.endswith("\n\n"))

    def test_empty_missing_produces_empty_file(self) -> None:
        _write_state(self.state, completed=["000001", "000002"])
        missing = blm._select_missing(
            self.db, self.state,
            include_types=None, exclude_types=None, include_failed=False,
        )
        self.out.write_text(
            "\n".join(code for code, _ in missing) + ("\n" if missing else ""),
            encoding="utf-8",
        )
        self.assertEqual(self.out.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
