from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# 0.3.0 split (RFC docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md):
# paths + schema migrations have been lifted to submodules. The legacy
# 3605-line file is now this __init__.py; the public name set is
# unchanged (every `from scripts import fund_data; fund_data.foo`
# site keeps working).
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
from .schema.migrations import (
    FUND_DATA_SCHEMA_VERSION,
    MIGRATIONS,
    _migration_001_add_industry_allocations_market_value,
    _migration_002_add_fee_structures_fee_text,
    _migration_003_add_fee_structures_discount_fee,
    _migration_004_add_fee_structures_discount_fee_text,
    _migration_005_align_column_order,
)

logger = logging.getLogger("fund_data")

# 0.3.0 split (RFC docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md):
# normalizers have been lifted to a sibling submodule. ``normalize_fund_code``
# is the public entry; the rest are underscored because callers that
# reach into them are themselves low-level (parsers / store / fetch).
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
from . import http, providers
from .http import FundDataClient, _RateLimiter
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
from .sync import (
    batch_sync_funds,
    coverage_report,
    coverage_rows,
    sync_fund,
)


def write_rows(rows: list[dict[str, Any]], output_path: str | Path | None, fmt: str) -> str:
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
    return FundDataStore(db_path).export_table(table, fund_code=fund_code)
