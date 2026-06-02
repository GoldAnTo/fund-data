"""Unit tests for ``scripts/fund_data/paths.py``.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
The four provider id strings, the on-disk fallback path,
the ``utc_now()`` formatter, and the ``default_db_path()``
resolver each get a pin so a future refactor cannot silently
break the agent / CLI / MCP contract.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from fund_data import paths  # noqa: E402
import fund_cloud  # noqa: E402  — sibling module, patched by string below

# Where ``fund_data/paths.py`` thinks the on-disk full DB
# lives. ``parents[2]`` from this test file is ``fund-data/``,
# so this resolves to ``fund-data/data/fund_data.sqlite``.
# Pin it so a future repo-layout change cannot silently break
# a 5.4 GB install.
EXPECTED_DEFAULT_DB_PATH = paths.Path(__file__).resolve().parents[2] / "data" / "fund_data.sqlite"


class ConstantsTests(unittest.TestCase):
    def test_default_db_path_points_at_fund_data_dir(self) -> None:
        self.assertEqual(paths.DEFAULT_DB_PATH, EXPECTED_DEFAULT_DB_PATH)
        # The directory is gitignored but the parent must exist
        # in any checkout (it's where fund_data.sqlite *will* be
        # written). If this fails the worktree is broken.
        self.assertTrue(paths.DEFAULT_DB_PATH.parent.exists())

    def test_provider_id_strings_are_stable(self) -> None:
        # These are part of the agent / CLI / MCP contract.
        # ``fund_cli --provider <X>`` and ``PROVIDER_X`` constants
        # in callers must keep matching the strings the auto-chain
        # builder uses.
        self.assertEqual(paths.PROVIDER_AUTO, "auto")
        self.assertEqual(paths.PROVIDER_EASTMONEY, "eastmoney")
        self.assertEqual(paths.PROVIDER_AKSHARE, "akshare")
        self.assertEqual(paths.PROVIDER_INVESTODAY, "investoday")
        self.assertEqual(paths.PROVIDER_TUSHARE, "tushare")

    def test_all_exports_match_dunder_all(self) -> None:
        # ``__all__`` is the agent contract; every name in it
        # must be importable from the module.
        for name in paths.__all__:
            self.assertTrue(
                hasattr(paths, name),
                f"paths.__all__ lists {name!r} but the module does not export it",
            )


class UtcNowTests(unittest.TestCase):
    def test_iso_8601_with_second_precision(self) -> None:
        # AkShare and the Eastmoney endpoints both give
        # second-precision timestamps, and a mixed-precision
        # schema causes ``UNIQUE (fund_code, nav_date)`` to
        # fire spuriously. Pin microsecond == 0.
        text = paths.utc_now()
        self.assertRegex(
            text,
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$",
        )
        self.assertNotIn(".", text)  # no microsecond fraction

    def test_no_trailing_z(self) -> None:
        # Python's isoformat emits ``+00:00`` not ``Z``. Pin
        # so an upstream Python change does not silently
        # break AkShare's date parser (which expects ``+00:00``).
        self.assertFalse(paths.utc_now().endswith("Z"))


class DefaultDbPathPrecedenceTests(unittest.TestCase):
    """The five-layer resolver is documented in
    ``paths.default_db_path()``. The order matters because
    agents / CI / the test suite rely on it: CI sets
    ``FUND_DATA_CACHE_DIR`` to land on the cloud cache, tests
    set ``FUND_DATA_DB`` to land on a tmp file, and an
    unconfigured shell falls through to the on-disk fallback.
    """

    def setUp(self) -> None:
        # Every test in this class manipulates env vars that
        # the resolver reads. Save / restore around each test
        # so a misbehaving test cannot leak state into the
        # next one (the test suite bootstrap in tests/__init__.py
        # sets ``FUND_DATA_DB`` unconditionally otherwise).
        self._saved = {
            k: os.environ.get(k)
            for k in (
                "FUND_DATA_CACHE_DIR",
                "FUND_DATA_DB",
                "FUND_DATA_AUTO_PULL",
            )
        }

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_fund_data_db_wins_when_no_cache_dir(self) -> None:
        target = Path("/tmp/explicit.sqlite")
        # Layer 2 only takes effect when layer 1 (cache_dir) is
        # unset. We pop FUND_DATA_CACHE_DIR for the duration of
        # this test so the dev shell's ~/.cache/fund-data/...
        # does not pollute the result.
        os.environ.pop("FUND_DATA_CACHE_DIR", None)
        os.environ["FUND_DATA_DB"] = str(target)
        self.assertEqual(paths.default_db_path(), target)

    def test_fund_data_db_ignored_when_cache_dir_set(self) -> None:
        # Layer 1 (FUND_DATA_CACHE_DIR) trumps layer 2 (FUND_DATA_DB).
        # Without this, an agent that has wired up the cloud cache
        # would still hit the on-disk fallback because FUND_DATA_DB
        # was leaking from a previous run.
        with tempfile.TemporaryDirectory() as cache_dir:
            os.environ["FUND_DATA_CACHE_DIR"] = cache_dir
            os.environ["FUND_DATA_DB"] = "/tmp/leaked.sqlite"
            # The resolver will try the cloud bootstrap, which
            # may fall through to the on-disk fallback if there
            # is no live bundle. We only assert the explicit
            # FUND_DATA_DB was NOT honoured, by patching the
            # bootstrap + current_db_path to return None.
            with patch("fund_cloud.ensure_project_bundle", return_value={}), \
                 patch("fund_cloud.current_db_path", return_value=None):
                result = paths.default_db_path()
            self.assertNotEqual(result, Path("/tmp/leaked.sqlite"))
            self.assertEqual(result, paths.DEFAULT_DB_PATH)

    def test_bootstrap_db_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            os.environ["FUND_DATA_CACHE_DIR"] = cache_dir
            bootstrap_db = Path(cache_dir) / "bootstrap.sqlite"
            bootstrap_db.touch()
            with patch(
                "fund_cloud.ensure_project_bundle",
                return_value={"db_path": str(bootstrap_db)},
            ), patch(
                "fund_cloud.current_db_path", return_value=None
            ):
                self.assertEqual(paths.default_db_path(), bootstrap_db)

    def test_current_db_used_when_bootstrap_missing(self) -> None:
        with tempfile.TemporaryDirectory() as cache_dir:
            os.environ["FUND_DATA_CACHE_DIR"] = cache_dir
            current_db = Path(cache_dir) / "current.sqlite"
            current_db.touch()
            with patch(
                "fund_cloud.ensure_project_bundle", return_value={}
            ), patch(
                "fund_cloud.current_db_path", return_value=current_db
            ):
                self.assertEqual(paths.default_db_path(), current_db)

    def test_on_disk_fallback(self) -> None:
        # No env vars, no cloud bundle — must land on DEFAULT_DB_PATH.
        os.environ.pop("FUND_DATA_CACHE_DIR", None)
        os.environ.pop("FUND_DATA_DB", None)
        with patch(
            "fund_cloud.ensure_project_bundle", return_value={}
        ), patch(
            "fund_cloud.current_db_path", return_value=None
        ):
            self.assertEqual(paths.default_db_path(), paths.DEFAULT_DB_PATH)


if __name__ == "__main__":
    unittest.main()
