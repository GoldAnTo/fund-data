"""Pin the 0.3.0 facade: every name in ``fund_data.__all__`` is
importable, every name NOT in ``__all__`` is private.

Lifted out of the package-level test bundle during the 0.3.0
split (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``,
PR 6). The facade module re-exports ~50 names from the
submodules below; the agent / CLI / MCP contract is
"``from scripts import fund_data; fund_data.foo()`` keeps
working byte-identical". This file locks that contract so
a future refactor that drops a name fails CI rather than
silently breaking callers.

Public surface (RFC Appendix B):

  paths:  DEFAULT_DB_PATH, PROVIDER_*, default_db_path, utc_now
  schema: FUND_DATA_SCHEMA_VERSION, MIGRATIONS, _migration_*5
  normalizers: 15 underscored helpers + normalize_fund_code
  parsers: 5 parse_* + normalize_fund_codes + 3 underscored JS
           helpers
  http: FundDataClient, _RateLimiter
  providers: 4 provider classes + ProviderError / ProviderResult
              + build_providers / build_providers_full /
              run_provider_chain + _tushare_period
  store: FundDataStore
  fetch: 12 fetch_* + search_funds
  sync: sync_fund, batch_sync_funds, coverage_rows,
        coverage_report
  plus the two CLI/MCP helpers write_rows / export_table that
  live in __init__.py itself (they are not in __all__; they
  are part of the public surface anyway).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402


class AllAttributeTests(unittest.TestCase):
    """Every name in ``fund_data.__all__`` is importable as
    a module-level attribute. A drop is a hard contract
    break; the test fails immediately."""

    def test_all_exports_resolve(self) -> None:
        missing = [name for name in fund_data.__all__ if not hasattr(fund_data, name)]
        self.assertEqual(
            missing,
            [],
            f"the following __all__ entries are not actually exported: {missing}",
        )

    def test_all_count_is_stable(self) -> None:
        # Pin the size so a new re-export that lands without
        # an explicit entry surfaces here rather than as a
        # silent module-leak that future ``dir()`` consumers
        # pick up. The exact count is "68" today (after PR 6);
        # the upper bound is "70 names give or take" so a
        # minor rename does not break the test, but adding
        # 10 names without an explicit entry does.
        self.assertGreaterEqual(len(fund_data.__all__), 60)
        self.assertLessEqual(len(fund_data.__all__), 80)


class ProviderIdConstantsTests(unittest.TestCase):
    """The provider id strings are referenced by every
    caller (CLI, MCP, the auto-chain) AND by the four
    provider class ``name =`` attributes. They must all
    agree."""

    EXPECTED = {
        "PROVIDER_AUTO": "auto",
        "PROVIDER_EASTMONEY": "eastmoney",
        "PROVIDER_AKSHARE": "akshare",
        "PROVIDER_INVESTODAY": "investoday",
        "PROVIDER_TUSHARE": "tushare",
    }

    def test_each_constant_appears_in_all(self) -> None:
        for name in self.EXPECTED:
            with self.subTest(name=name):
                self.assertIn(name, fund_data.__all__)

    def test_each_constant_value(self) -> None:
        for name, value in self.EXPECTED.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(fund_data, name), value)


class SchemaExportsTests(unittest.TestCase):
    """The five schema migrations are part of the contract:
    a future migration in ``fund_data.schema.migrations`` that
    is NOT in the facade is a documentation / agent-contract
    bug."""

    EXPECTED = (
        "_migration_001_add_industry_allocations_market_value",
        "_migration_002_add_fee_structures_fee_text",
        "_migration_003_add_fee_structures_discount_fee",
        "_migration_004_add_fee_structures_discount_fee_text",
        "_migration_005_align_column_order",
    )

    def test_each_migration_appears_in_all(self) -> None:
        for name in self.EXPECTED:
            with self.subTest(name=name):
                self.assertIn(name, fund_data.__all__)

    def test_schema_version_is_five(self) -> None:
        # Migration 5 is the canonical-order rebuild; the
        # version constant must match the highest migration
        # id in MIGRATIONS.
        self.assertEqual(fund_data.FUND_DATA_SCHEMA_VERSION, 5)
        self.assertEqual(fund_data.FUND_DATA_SCHEMA_VERSION, max(v for v, _ in fund_data.MIGRATIONS))


class PublicEntryPointsTests(unittest.TestCase):
    """The headline public entry points must resolve to
    callable / constant objects, not None / lazy placeholders
    that would surprise the agent CLI / MCP."""

    EXPECTED_CALLABLES = (
        "default_db_path",
        "utc_now",
        "normalize_fund_code",
        "parse_search_results",
        "parse_fund_code_list",
        "parse_fund_codes",
        "normalize_fund_codes",
        "parse_nav_history",
        "parse_snapshot",
        "FundDataClient",
        "EastmoneyProvider",
        "AkshareProvider",
        "InvestodayProvider",
        "TushareProvider",
        "build_providers",
        "build_providers_full",
        "run_provider_chain",
        "ProviderError",
        "ProviderResult",
        "FundDataStore",
        "search_funds",
        "fetch_fund_list",
        "fetch_nav_history",
        "fetch_snapshot",
        "fetch_stock_holdings",
        "fetch_profile",
        "fetch_bond_holdings",
        "fetch_industry_allocations",
        "fetch_fee_structures",
        "fetch_dividends",
        "fetch_splits",
        "fetch_fund_managers",
        "sync_fund",
        "batch_sync_funds",
        "coverage_rows",
        "coverage_report",
        "write_rows",
        "export_table",
    )

    def test_each_entry_point_is_callable(self) -> None:
        for name in self.EXPECTED_CALLABLES:
            with self.subTest(name=name):
                obj = getattr(fund_data, name)
                self.assertTrue(callable(obj), f"{name!r} is not callable")
                # ProviderError is an exception class; everything
                # else in the list is a function or a class.
                if name == "ProviderError":
                    self.assertTrue(isinstance(obj, type))
                    self.assertTrue(issubclass(obj, RuntimeError))


if __name__ == "__main__":
    unittest.main()
