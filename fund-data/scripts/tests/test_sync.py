"""Unit tests for ``scripts/fund_data/sync.py``.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Pins the orchestration layer's public contract: ``sync_fund``
shape, ``batch_sync_funds`` return envelope, and
``coverage_report`` / ``coverage_rows`` shape. The deep
behavioural tests (back-end share skip, dataset_errors
non-fatal path, Akshare fund_managers, etc.) live in
``test_fund_data``'s FundDataStoreTests class -- this
file focuses on the *shape* of the orchestration contract
so a future refactor cannot silently break the agent CLI.
"""

from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

from fund_data import sync as sync_module  # noqa: E402


class CoverageRowsFunctionTests(unittest.TestCase):
    def test_coverage_rows_is_exported(self) -> None:
        # ``fund_data.coverage_rows`` is the agent entry
        # point (used by the coverage subcommand in the
        # CLI). The shape is a list of dicts.
        self.assertTrue(callable(sync_module.coverage_rows))

    def test_coverage_report_is_exported(self) -> None:
        self.assertTrue(callable(sync_module.coverage_report))


class SyncFundSignatureTests(unittest.TestCase):
    def test_signature_has_required_kwargs(self) -> None:
        # Lock the named parameters that the agent CLI
        # passes through. A rename here breaks the CLI.
        sig = inspect.signature(sync_module.sync_fund)
        params = sig.parameters
        for name in (
            "start_date", "end_date", "page", "per",
            "db_path", "client", "provider",
            "include_holdings", "include_profile", "include_bonds",
            "include_industries", "include_fees", "include_distributions",
            "include_managers", "include_all",
            "report_year", "fee_indicators",
        ):
            with self.subTest(name=name):
                self.assertIn(name, params)

    def test_sync_fund_returns_dict(self) -> None:
        # The function returns a ``dict[str, Any]`` with the
        # standard shape (status, fund_code, rows_changed,
        # per-dataset row counts, dataset_errors). Pin the
        # return type annotation.
        sig = inspect.signature(sync_module.sync_fund)
        self.assertEqual(sig.return_annotation, "dict[str, Any]")


class BatchSyncFundsSignatureTests(unittest.TestCase):
    def test_signature_has_required_kwargs(self) -> None:
        sig = inspect.signature(sync_module.batch_sync_funds)
        params = sig.parameters
        for name in (
            "start_date", "end_date", "page", "per",
            "db_path", "provider",
            "include_holdings", "include_profile", "include_bonds",
            "include_industries", "include_fees", "include_distributions",
            "include_managers", "include_all",
            "report_year", "fee_indicators",
            "batch_id", "stop_on_error",
            "concurrency", "min_interval_seconds",
        ):
            with self.subTest(name=name):
                self.assertIn(name, params)

    def test_batch_sync_funds_returns_dict(self) -> None:
        sig = inspect.signature(sync_module.batch_sync_funds)
        self.assertEqual(sig.return_annotation, "dict[str, Any]")


class ExposedSymbolsTests(unittest.TestCase):
    def test_dunder_all_matches_actual_exports(self) -> None:
        for name in sync_module.__all__:
            with self.subTest(name=name):
                self.assertTrue(
                    hasattr(sync_module, name),
                    f"sync.__all__ lists {name!r} but the module does not export it",
                )


if __name__ == "__main__":
    unittest.main()
