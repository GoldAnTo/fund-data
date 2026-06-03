"""fund-data skill: the 0.3.0 public API surface.

Lifted out of the 3605-line monolith by the 0.3.0 split
series (RFC ``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``,
PR 1 through PR 5). The implementation now lives in
focused submodules under this package; this file is the
facade that re-exports the agent / CLI / MCP contract so
every ``from scripts import fund_data; fund_data.foo()``
site keeps working byte-identical.

Layer breakdown (see the per-module docstring for the
detailed contract; the summary here is for the agent who
needs to find a name in a hurry):

  paths              DEFAULT_DB_PATH, PROVIDER_*, default_db_path,
                     utc_now
  schema/migrations  MIGRATIONS, FUND_DATA_SCHEMA_VERSION,
                     _migration_001..005 (the 5 schema
                     migrations; ``ensure_schema`` itself
                     lives in :mod:`store` because it is
                     a FundDataStore method)
  normalizers        15 text / float / date / report-period
                     helpers; only ``normalize_fund_code`` is
                     public
  parsers            parse_search_results, parse_fund_code_list,
                     parse_fund_codes, normalize_fund_codes,
                     parse_nav_history, parse_snapshot
  http               FundDataClient, _RateLimiter
  providers/         EastmoneyProvider, AkshareProvider,
                     InvestodayProvider, TushareProvider,
                     ProviderError, ProviderResult,
                     build_providers, build_providers_full,
                     run_provider_chain
  store              FundDataStore (the SQLite persistence
                     layer + WAL + busy_timeout + the 14
                     per-table upsert_* methods)
  fetch              12 fetch_* convenience functions +
                     search_funds
  sync               sync_fund, batch_sync_funds,
                     coverage_rows, coverage_report

This ``__init__`` file is intentionally thin: the
~50 public names below are the agent contract. Anything
not in ``__all__`` is a private helper; callers that
reach into underscored symbols do so at their own risk
and break on every minor bump.

The two ``write_rows`` / ``export_table`` helpers stay
here because the CLI (``fund-data/scripts/fund_cli.py``
``export`` subcommand) and the MCP server (``fund_mcp.py``
``fund_export`` tool) both call them as
``fund_data.write_rows(...)`` / ``fund_data.export_table(...)``.
A future refactor (PR 6+) can move them under
``scripts/_export.py`` if more bulk export logic
accumulates.
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import Any

# --- paths ---
from .paths import (
    DEFAULT_DB_PATH,
    PROVIDER_AUTO,
    PROVIDER_EASTMONEY,
    PROVIDER_AKSHARE,
    PROVIDER_INVESTODAY,
    PROVIDER_TUSHARE,
    default_db_path,
    utc_now,
)

# --- schema migrations ---
from .schema.migrations import (
    FUND_DATA_SCHEMA_VERSION,
    MIGRATIONS,
    _migration_001_add_industry_allocations_market_value,
    _migration_002_add_fee_structures_fee_text,
    _migration_003_add_fee_structures_discount_fee,
    _migration_004_add_fee_structures_discount_fee_text,
    _migration_005_align_column_order,
    _migration_006_create_fund_manager_links,
)

# --- normalizers (only ``normalize_fund_code`` is public; the rest
# are underscored because callers that reach into them are
# themselves low-level -- parsers, store, fetch, sync) ---
from .normalizers import (
    _clean_text,
    _extract_payload_records,
    _fee_indicator_alias,
    _first_number,
    _first_value,
    _is_missing,
    _json_dumps,
    _normalize_date_text,
    _normalize_report_period,
    _profile_dict,
    _rate_to_decimal,
    _ratio_value,
    _records,
    _to_float,
    normalize_fund_code,
)

# --- parsers ---
from .parsers import (
    _decode_js_fragment,
    _extract_js_array,
    _extract_js_string,
    normalize_fund_codes,
    parse_fund_code_list,
    parse_fund_codes,
    parse_nav_history,
    parse_search_results,
    parse_snapshot,
)

# --- http client + rate limiter ---
from . import http, providers
from .http import FundDataClient, _RateLimiter

# --- providers + chain ---
from .providers import (
    AkshareProvider,
    EastmoneyProvider,
    InvestodayProvider,
    ProviderError,
    ProviderResult,
    TushareProvider,
    _tushare_period,
    build_providers,
    build_providers_full,
    run_provider_chain,
)

# --- store / fetch / sync ---
from . import fetch, store, sync
from .store import FundDataStore
from .fetch import (
    fetch_bond_holdings,
    fetch_dividends,
    fetch_fee_structures,
    fetch_fund_list,
    fetch_fund_managers,
    fetch_industry_allocations,
    fetch_nav_history,
    fetch_profile,
    fetch_snapshot,
    fetch_splits,
    fetch_stock_holdings,
    search_funds,
)
from .self_audit import build_self_audit_queue, check_fund_health
from .completion import (
    build_completion_plan,
    load_completion_policy,
    run_completion_plan,
    verify_completion_run,
)
from .sync import (
    batch_sync_funds,
    coverage_report,
    coverage_rows,
    sync_fund,
)

logger = logging.getLogger("fund_data")


__all__ = [
    # paths / constants
    "DEFAULT_DB_PATH",
    "PROVIDER_AUTO",
    "PROVIDER_EASTMONEY",
    "PROVIDER_AKSHARE",
    "PROVIDER_INVESTODAY",
    "PROVIDER_TUSHARE",
    "default_db_path",
    "utc_now",
    # schema
    "FUND_DATA_SCHEMA_VERSION",
    "MIGRATIONS",
    "_migration_001_add_industry_allocations_market_value",
    "_migration_002_add_fee_structures_fee_text",
    "_migration_003_add_fee_structures_discount_fee",
    "_migration_004_add_fee_structures_discount_fee_text",
    "_migration_005_align_column_order",
    "_migration_006_create_fund_manager_links",
    # normalizers
    "_clean_text",
    "_extract_payload_records",
    "_fee_indicator_alias",
    "_first_number",
    "_first_value",
    "_is_missing",
    "_json_dumps",
    "_normalize_date_text",
    "_normalize_report_period",
    "_profile_dict",
    "_rate_to_decimal",
    "_ratio_value",
    "_records",
    "_to_float",
    "normalize_fund_code",
    # parsers
    "_decode_js_fragment",
    "_extract_js_array",
    "_extract_js_string",
    "normalize_fund_codes",
    "parse_fund_code_list",
    "parse_fund_codes",
    "parse_nav_history",
    "parse_search_results",
    "parse_snapshot",
    # http
    "FundDataClient",
    "_RateLimiter",
    # providers
    "AkshareProvider",
    "EastmoneyProvider",
    "InvestodayProvider",
    "ProviderError",
    "ProviderResult",
    "TushareProvider",
    "_tushare_period",
    "build_providers",
    "build_providers_full",
    "run_provider_chain",
    # store
    "FundDataStore",
    # fetch
    "fetch_bond_holdings",
    "fetch_dividends",
    "fetch_fee_structures",
    "fetch_fund_list",
    "fetch_fund_managers",
    "fetch_industry_allocations",
    "fetch_nav_history",
    "fetch_profile",
    "fetch_snapshot",
    "fetch_splits",
    "fetch_stock_holdings",
    "search_funds",
    # sync
    "batch_sync_funds",
    "coverage_report",
    "coverage_rows",
    "sync_fund",
    # self-audit
    "build_self_audit_queue",
    "check_fund_health",
    # openclaw active-completion
    "build_completion_plan",
    "load_completion_policy",
    "run_completion_plan",
    "verify_completion_run",
]


# --- public export helpers (used by fund_cli ``export`` subcommand
# and fund_mcp ``fund_export`` tool) ---
#
# These two live in __init__.py because both callers reach them
# as ``fund_data.write_rows(...)`` / ``fund_data.export_table(...)``;
# a future PR can move them under ``scripts/_export.py`` if
# more bulk export logic accumulates, but for 0.3.0 they stay
# where the public name expects them.


def write_rows(
    rows: list[dict[str, Any]],
    output_path: str | Path | None,
    fmt: str,
) -> str:
    """Serialize ``rows`` as JSON or CSV, optionally writing to
    ``output_path``. Returns the serialized text. The
    ``fund_cli export`` subcommand and the ``fund_mcp
    fund_export`` tool both call this."""
    if fmt == "json":
        text = json.dumps(rows, ensure_ascii=False, indent=2)
        if output_path:
            Path(output_path).write_text(text + "\n", encoding="utf-8")
        return text
    if fmt != "csv":
        raise ValueError(f"unsupported format: {fmt}")
    if not rows:
        text = ""
    else:
        fieldnames = list(rows[0].keys())
        if output_path:
            with Path(output_path).open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            return str(output_path)
        from io import StringIO

        buffer = StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        text = buffer.getvalue()
    return text


def export_table(
    table: str,
    *,
    db_path: str | Path | None = None,
    fund_code: str | None = None,
) -> list[dict[str, Any]]:
    """Pull every row of ``table`` (optionally filtered to
    ``fund_code``) out of the local SQLite base. Used by the
    ``fund_cli export`` subcommand and the ``fund_mcp
    fund_export`` tool."""
    return FundDataStore(db_path).export_table(table, fund_code=fund_code)
