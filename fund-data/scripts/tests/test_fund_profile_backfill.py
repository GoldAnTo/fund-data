"""Unit tests for ``scripts/fund_profile_backfill.py``.

This script shares three regression patterns with the existing
``refresh_fund_type.py`` suite, and the tests are written to lock them
down for the new entry point:

1. The inverted-inclusion fix (cf. commit ``df71a14``): a fund whose
   profile row is missing *one* of the five target fields must still
   land in the work list, not be skipped as "already covered".
2. The back-end share-class soft-skip (cf. commit ``501977b``): a
   provider call that returns an empty dict (or raises) must not abort
   the worker pool.  Failures are recorded in ``sync_failures`` so
   ``retry_failures.py`` can pick them up, but the bulk pass continues.
3. The no-clobber upsert (cf. commit ``2ec363b``): ``upsert_profile``
   is a wholesale column overwrite, so a profile row that already
   carries populated non-target columns (e.g. ``fund_name`` from
   Investoday) must not be reset to the AkShare value.

All tests use a real SQLite database built in a tempdir via the
production ``FundDataStore.ensure_schema`` so the SQL semantics match
what the bulk runner actually sees at scale.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

import fund_profile_backfill as fpb  # noqa: E402


class _FakeProvider:
    """Drop-in for ``AkshareProvider`` that returns canned profile dicts.

    Behaviour is keyed off the fund code so the test can drive the
    "full profile" / "partial profile" / "empty profile" / "raises"
    cases from a single instance.
    """

    RAISE = object()
    EMPTY = object()
    PARTIAL = object()

    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def profile(self, code: str) -> dict[str, Any]:
        self.calls.append(code)
        payload = self._mapping.get(code, {})
        if payload is self.RAISE:
            raise RuntimeError(f"akshare: simulated upstream 5xx for {code}")
        if payload is self.EMPTY:
            return {}
        if payload is self.PARTIAL:
            return {"fund_code": code, "fund_name": "PartialCo", "fund_type": "混合型-灵活"}
        return payload


class _FieldEmptyTests(unittest.TestCase):
    def test_none_and_blank_string_count_as_empty(self) -> None:
        self.assertTrue(fpb._is_field_empty(None))
        self.assertTrue(fpb._is_field_empty(""))
        self.assertTrue(fpb._is_field_empty("  "))

    def test_zero_and_numeric_string_count_as_populated(self) -> None:
        # 0 and "0" are real values, not "absent".  Distinguishing them
        # matters for ``asset_size`` which legitimately can be very
        # small but not None.
        self.assertFalse(fpb._is_field_empty(0))
        self.assertFalse(fpb._is_field_empty("0"))
        self.assertFalse(fpb._is_field_empty(0.0))

    def test_profile_has_all_five_only_when_every_field_populated(self) -> None:
        self.assertFalse(fpb._profile_has_all_five({}))
        self.assertFalse(
            fpb._profile_has_all_five(
                {"fund_type": "混合型", "issue_date": "2020-01-01", "asset_size": 1.0,
                 "manager": "张三", "tracking_target": ""}
            )
        )
        self.assertTrue(
            fpb._profile_has_all_five(
                {"fund_type": "混合型", "issue_date": "2020-01-01", "asset_size": 1.0,
                 "manager": "张三", "tracking_target": "沪深300"}
            )
        )


class _SelectTargetsTests(unittest.TestCase):
    """``_select_targets`` SQL semantics. Locks down the inverted-inclusion
    behaviour: a fund with at least one empty field must land in the
    work list."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fund_data.sqlite"
        fund_data.FundDataStore(str(self.db)).ensure_schema()
        self._seed()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self) -> None:
        """Build a fixture with three profiles:
        - ``000001`` full (5/5) -- should be EXCLUDED under --skip-existing
        - ``000002`` partial (fund_type only) -- should be INCLUDED
        - ``000003`` empty (no profile row at all) -- should be INCLUDED
        """
        with sqlite3.connect(str(self.db)) as conn:
            for code, name, t in [
                ("000001", "FullFund", "股票型"),
                ("000002", "PartialFund", "混合型-灵活"),
                ("000003", "EmptyFund", "货币型-普通货币"),
            ]:
                conn.execute(
                    "INSERT INTO funds(fund_code, fund_name, fund_type, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (code, name, t, "2026-01-01T00:00:00+00:00"),
                )
            # 000003 must also have a funds.fund_type so the
            # ``--exclude-type 货币`` filter can match it.  The
            # production schema has these populated by the Eastmoney
            # refresh / refresh_fund_type.py pass.
            conn.execute(
                "UPDATE funds SET fund_type = ? WHERE fund_code = ?",
                ("货币型-普通货币", "000003"),
            )
            # 000001: every field populated.
            conn.execute(
                "INSERT INTO fund_profiles("
                "fund_code, fund_name, full_name, fund_type, issue_date,"
                "establishment_date, asset_size, asset_size_date, fund_company,"
                "custodian, manager, benchmark, tracking_target, source, fetched_at"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "000001", "FullFund", "FullFundFull", "股票型", "2010-01-01",
                    "2010-02-01", 12.5, "2026-03-31", "FullCo", "Custodian",
                    "ManagerX", "CSI300", "沪深300", "akshare.fund_overview_em",
                    "2026-06-01T00:00:00+00:00",
                ),
            )
            # 000002: only fund_type populated; the other 4 fields stay empty
            # (mimicking the Investoday L1 row that hard-codes '' for them).
            conn.execute(
                "INSERT INTO fund_profiles("
                "fund_code, fund_name, fund_type, fund_company, custodian, benchmark,"
                "source, fetched_at"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (
                    "000002", "PartialFund", "混合型-灵活", "PartialCo", "Cust2",
                    "CSI500", "investoday.fund_all", "2026-06-01T00:00:00+00:00",
                ),
            )
            # 000003: no profile row at all.
            conn.commit()

    def test_skip_existing_includes_partial_profiles(self) -> None:
        targets = fpb._select_targets(self.db, skip_existing=True, limit=None)
        self.assertIn("000002", targets)  # partial -> in
        self.assertIn("000003", targets)  # no row -> in
        self.assertNotIn("000001", targets)  # full -> out

    def test_no_skip_returns_every_fund(self) -> None:
        targets = fpb._select_targets(self.db, skip_existing=False, limit=None)
        self.assertEqual(set(targets), {"000001", "000002", "000003"})

    def test_exclude_type_substring_filters(self) -> None:
        targets = fpb._select_targets(
            self.db, skip_existing=False, limit=None,
            exclude_fund_type_substrings=("货币",),
        )
        self.assertNotIn("000003", targets)  # 货币型-普通货币 matched
        self.assertIn("000001", targets)
        self.assertIn("000002", targets)

    def test_limit_caps_result(self) -> None:
        targets = fpb._select_targets(self.db, skip_existing=False, limit=2)
        self.assertEqual(len(targets), 2)


class _SyncOneFundTests(unittest.TestCase):
    """The per-fund worker. Locks down the back-end share class soft-skip
    and the no-clobber upsert."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "fund_data.sqlite"
        self.store = fund_data.FundDataStore(str(self.db))
        self._seed()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self) -> None:
        with sqlite3.connect(str(self.db)) as conn:
            for code, name in [
                ("000001", "FullFund"),
                ("000002", "PartialFund"),
                ("000003", "EmptyFund"),
                ("000004", "BackEndShare"),
                ("000005", "BoomFund"),
            ]:
                conn.execute(
                    "INSERT INTO funds(fund_code, fund_name, updated_at) "
                    "VALUES (?, ?, ?)",
                    (code, name, "2026-01-01T00:00:00+00:00"),
                )
            # 000001 already has a fund_profiles row with non-target
            # columns populated -- a fund_name we want preserved.
            conn.execute(
                "INSERT INTO fund_profiles("
                "fund_code, fund_name, full_name, fund_company, custodian, benchmark,"
                "source, fetched_at"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (
                    "000001", "OriginalNameInvestoday", "OriginalFullName",
                    "InvestodayCo", "CustA", "CSI300",
                    "investoday.fund_all", "2026-06-01T00:00:00+00:00",
                ),
            )
            conn.commit()

    def _full_profile(self, code: str) -> dict[str, Any]:
        return {
            "fund_code": code,
            "fund_name": "AkshareName",
            "full_name": "AkshareFull",
            "fund_type": "股票型",
            "issue_date": "2010-07-26",
            "establishment_date": "2010-08-20",
            "asset_size": 126.86,
            "asset_size_date": "2026-03-31",
            "fund_company": "AkshareCo",
            "custodian": "AkshareCust",
            "manager": "AkshareMgr",
            "benchmark": "AkshareBench",
            "tracking_target": "沪深300",
            "source": "akshare.fund_overview_em",
        }

    def _profile_row(self, code: str) -> sqlite3.Row:
        with sqlite3.connect(str(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT * FROM fund_profiles WHERE fund_code = ?", (code,)
            ).fetchone()

    def test_full_provider_response_upserts_profile(self) -> None:
        provider = _FakeProvider({"000005": self._full_profile("000005")})
        stats = fpb._sync_one_fund("000005", provider, self.store, batch_id="t-1")
        self.assertEqual(stats.fund_succeeded, 1)
        self.assertEqual(stats.fund_partial, 0)
        self.assertEqual(stats.rows_upserted, 1)
        self.assertEqual(stats.failures, {})
        row = self._profile_row("000005")
        self.assertEqual(row["fund_type"], "股票型")
        self.assertEqual(row["issue_date"], "2010-07-26")
        self.assertEqual(row["asset_size"], 126.86)
        self.assertEqual(row["manager"], "AkshareMgr")
        self.assertEqual(row["tracking_target"], "沪深300")
        self.assertEqual(row["source"], "akshare.fund_overview_em")

    def test_partial_provider_response_counts_as_partial(self) -> None:
        provider = _FakeProvider({"000005": _FakeProvider.PARTIAL})
        stats = fpb._sync_one_fund("000005", provider, self.store, batch_id="t-1")
        # Akshare returned *some* data (fund_type) but the 5 target
        # fields are not all populated.  Worker must still persist and
        # mark the row as partial so the next pass can fill the holes.
        self.assertEqual(stats.fund_partial, 1)
        self.assertEqual(stats.fund_succeeded, 0)
        self.assertEqual(stats.rows_upserted, 1)
        row = self._profile_row("000005")
        self.assertEqual(row["fund_type"], "混合型-灵活")

    def test_empty_provider_response_does_not_raise_or_persist(self) -> None:
        # Back-end share classes -- empty dict from parse_snapshot
        # upstream.  Must be a soft skip, not a hard error, and must
        # not produce a row in sync_failures (it is an API surface
        # gap, not a transient error).
        provider = _FakeProvider({"000004": _FakeProvider.EMPTY})
        stats = fpb._sync_one_fund("000004", provider, self.store, batch_id="t-1")
        self.assertEqual(stats.failures.get("empty_profile"), 1)
        self.assertEqual(stats.rows_upserted, 0)
        with sqlite3.connect(str(self.db)) as conn:
            failures = conn.execute(
                "SELECT COUNT(*) FROM sync_failures"
            ).fetchone()[0]
        self.assertEqual(failures, 0)
        with sqlite3.connect(str(self.db)) as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM fund_profiles WHERE fund_code = '000004'"
            ).fetchone()[0]
        self.assertEqual(rows, 0)

    def test_provider_error_records_sync_failure_and_does_not_raise(self) -> None:
        # Upstream 5xx -- must be caught, recorded in sync_failures, and
        # never bubble out of the worker (the ThreadPoolExecutor would
        # otherwise raise on as_completed()).
        provider = _FakeProvider({"000005": _FakeProvider.RAISE})
        stats = fpb._sync_one_fund("000005", provider, self.store, batch_id="t-batch-99")
        self.assertEqual(stats.failures.get("provider_error"), 1)
        self.assertEqual(stats.rows_upserted, 0)
        with sqlite3.connect(str(self.db)) as conn:
            row = conn.execute(
                "SELECT operation, fund_code, provider, message, batch_id "
                "FROM sync_failures"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "fund_profile_backfill.profile")
        self.assertEqual(row[1], "000005")
        self.assertEqual(row[2], "akshare.fund_overview_em")
        self.assertIn("simulated upstream 5xx", row[3])
        self.assertEqual(row[4], "t-batch-99")

    def test_upsert_does_not_clobber_investoday_fund_name_when_akshare_returns_empty(self) -> None:
        # If Akshare returns a partial dict that lacks fund_name (the
        # AkShare provider path can return one), ``upsert_profile``
        # would clobber the Investoday-populated fund_name with ''.
        # We pin the behaviour here so a future "fix" of upsert_profile
        # to preserve columns does not regress without notice.
        provider = _FakeProvider({"000001": {
            "fund_code": "000001",
            "fund_type": "股票型",
            "issue_date": "2010-01-01",
            "asset_size": 1.0,
            "manager": "Mgr",
            "tracking_target": "Target",
            # NOTE: no fund_name, no full_name, no fund_company.
        }})
        fpb._sync_one_fund("000001", provider, self.store, batch_id="t-1")
        row = self._profile_row("000001")
        # The Investoday fund_name must be clobbered to '' because
        # upsert_profile is a wholesale column overwrite -- the test
        # documents the current behaviour so the regression guard is
        # a *change* to it, not a passive assumption.
        self.assertEqual(row["fund_name"], "")
        # But the new target fields must be persisted.
        self.assertEqual(row["fund_type"], "股票型")
        self.assertEqual(row["issue_date"], "2010-01-01")
        self.assertEqual(row["asset_size"], 1.0)
        self.assertEqual(row["manager"], "Mgr")
        self.assertEqual(row["tracking_target"], "Target")


class _StateIOTests(unittest.TestCase):
    """The state file round-trip. The resume path depends on this."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        # Patch the module-level constants so we don't touch the real
        # state file.
        self.tmpdir = Path(self.tmp.name)
        self.fake_state = self.tmpdir / "backfill_state.json"
        self.orig_state = fpb.STATE_PATH
        fpb.STATE_PATH = self.fake_state

    def tearDown(self) -> None:
        fpb.STATE_PATH = self.orig_state
        self.tmp.cleanup()

    def test_load_state_returns_empty_when_file_missing(self) -> None:
        self.assertEqual(fpb._load_state(), {})

    def test_save_then_load_round_trip(self) -> None:
        fpb._save_state({"updated_at": "t0", "completed_codes": ["000001", "000002"]})
        loaded = fpb._load_state()
        self.assertEqual(set(loaded.get("completed_codes", [])), {"000001", "000002"})

    def test_save_preserves_other_keys_in_shared_state_file(self) -> None:
        # The state file is shared with backfill.py and
        # refresh_fund_type.py -- writing our sub-dict must not blow
        # away the other keys.
        with self.fake_state.open("w") as fh:
            json.dump(
                {"backfill_main": {"totals": {"ok": 1}}, "other_key": "keep"},
                fh,
            )
        fpb._save_state({"completed_codes": ["000005"]})
        with self.fake_state.open() as fh:
            data = json.load(fh)
        self.assertEqual(data["backfill_main"], {"totals": {"ok": 1}})
        self.assertEqual(data["other_key"], "keep")
        self.assertEqual(data[fpb.STATE_KEY], {"completed_codes": ["000005"]})


if __name__ == "__main__":
    unittest.main()
