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


class FundDataStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        default_path = default_db_path()
        self.db_path = Path(db_path) if db_path is not None else default_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
                create table if not exists funds (
                    fund_code text primary key,
                    fund_name text not null,
                    fund_type text,
                    company text,
                    manager text,
                    nav real,
                    nav_date text,
                    other_names text,
                    source text,
                    updated_at text not null
                );
                create table if not exists nav_history (
                    fund_code text not null,
                    nav_date text not null,
                    unit_nav real,
                    accumulated_nav real,
                    daily_growth_rate real,
                    subscribe_status text,
                    redeem_status text,
                    dividend text,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, nav_date)
                );
                create table if not exists snapshots (
                    fund_code text primary key,
                    fund_name text,
                    source_rate real,
                    current_rate real,
                    min_purchase real,
                    returns_json text,
                    stock_codes_json text,
                    source text,
                    fetched_at text not null
                );
                create table if not exists raw_responses (
                    source text not null,
                    request_key text not null,
                    fetched_at text not null,
                    raw_text text not null,
                    primary key (source, request_key)
                );
                create table if not exists sync_runs (
                    id integer primary key autoincrement,
                    operation text not null,
                    fund_code text,
                    status text not null,
                    rows_changed integer not null,
                    started_at text not null,
                    finished_at text not null,
                    message text
                );
                create table if not exists sync_failures (
                    id integer primary key autoincrement,
                    batch_id text not null,
                    operation text not null,
                    fund_code text,
                    provider text,
                    message text not null,
                    failed_at text not null
                );
                create table if not exists stock_holdings (
                    fund_code text not null,
                    report_period text not null,
                    stock_code text not null,
                    stock_name text,
                    net_value_ratio real,
                    shares real,
                    market_value real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, report_period, stock_code)
                );
                create table if not exists fund_profiles (
                    fund_code text primary key,
                    fund_name text,
                    full_name text,
                    fund_type text,
                    issue_date text,
                    establishment_date text,
                    asset_size real,
                    asset_size_date text,
                    fund_company text,
                    custodian text,
                    manager text,
                    benchmark text,
                    tracking_target text,
                    source text,
                    fetched_at text not null
                );
                create table if not exists bond_holdings (
                    fund_code text not null,
                    report_period text not null,
                    bond_code text not null,
                    bond_name text,
                    net_value_ratio real,
                    market_value real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, report_period, bond_code)
                );
                create table if not exists industry_allocations (
                    fund_code text not null,
                    report_period text not null,
                    industry_name text not null,
                    net_value_ratio real,
                    source text,
                    fetched_at text not null,
                    market_value real,
                    primary key (fund_code, report_period, industry_name)
                );
                create table if not exists fee_structures (
                    fund_code text not null,
                    fee_type text not null,
                    condition_name text not null,
                    fee real,
                    source text,
                    fetched_at text not null,
                    fee_text text,
                    discount_fee real,
                    discount_fee_text text,
                    primary key (fund_code, fee_type, condition_name)
                );
                create table if not exists dividends (
                    fund_code text not null,
                    dividend_date text not null,
                    ex_dividend_date text,
                    dividend_per_share real,
                    payment_date text,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, dividend_date)
                );
                create table if not exists splits (
                    fund_code text not null,
                    split_date text not null,
                    split_type text,
                    split_ratio real,
                    source text,
                    fetched_at text not null,
                    primary key (fund_code, split_date)
                );
                create table if not exists fund_managers (
                    manager_name text not null,
                    company text,
                    current_fund_codes text,
                    current_funds text,
                    tenure_days integer,
                    current_aum real,
                    best_return real,
                    source text,
                    fetched_at text not null,
                    primary key (manager_name, company, current_fund_codes)
                );
                -- Schema migration registry. Bumped by apply_migrations()
                -- below. The version column here is the *audit log*;
                -- PRAGMA user_version is the *fast read* of the same
                -- value (read on every open).
                create table if not exists schema_migrations (
                    version integer primary key,
                    applied_at text not null
                );
                """)
            self._apply_migrations(conn)

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        """Run every migration in :data:`MIGRATIONS` whose version is
        greater than the database's current ``PRAGMA user_version``.

        A failed migration aborts the whole ``ensure_schema`` call —
        the migration that errored has its transaction rolled back,
        so re-running ``ensure_schema`` retries the failed migration
        (the prior version remains as ``user_version``).
        """
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, fn in MIGRATIONS:
            if version <= current:
                # Already applied (either this is a fresh DB and the
                # version is < the first migration, or an old DB
                # that's been upgraded before).
                continue
            try:
                fn(conn)
            except Exception:
                logger.exception("schema migration %d failed", version)
                raise
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, utc_now()),
            )
            conn.execute(f"PRAGMA user_version = {int(version)}")

    def _ensure_column(
        self, conn: sqlite3.Connection, table: str, column: str, column_type: str
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"pragma table_info({table})")}
        if column not in columns:
            conn.execute(f"alter table {table} add column {column} {column_type}")

    def upsert_funds(self, rows: list[dict[str, Any]]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into funds (
                    fund_code, fund_name, fund_type, company, manager, nav, nav_date,
                    other_names, source, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code) do update set
                    fund_name=excluded.fund_name,
                    fund_type=excluded.fund_type,
                    company=excluded.company,
                    manager=excluded.manager,
                    nav=excluded.nav,
                    nav_date=excluded.nav_date,
                    other_names=excluded.other_names,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        row["fund_code"],
                        row.get("fund_name", ""),
                        row.get("fund_type", ""),
                        row.get("company", ""),
                        row.get("manager", ""),
                        row.get("nav"),
                        row.get("nav_date", ""),
                        row.get("other_names", ""),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_nav_history(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into nav_history (
                    fund_code, nav_date, unit_nav, accumulated_nav, daily_growth_rate,
                    subscribe_status, redeem_status, dividend, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, nav_date) do update set
                    unit_nav=excluded.unit_nav,
                    accumulated_nav=excluded.accumulated_nav,
                    daily_growth_rate=excluded.daily_growth_rate,
                    subscribe_status=excluded.subscribe_status,
                    redeem_status=excluded.redeem_status,
                    dividend=excluded.dividend,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row["nav_date"],
                        row.get("unit_nav"),
                        row.get("accumulated_nav"),
                        row.get("daily_growth_rate"),
                        row.get("subscribe_status", ""),
                        row.get("redeem_status", ""),
                        row.get("dividend", ""),
                        row.get("source", "eastmoney.nav_history"),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_snapshot(self, snapshot: dict[str, Any]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                insert into snapshots (
                    fund_code, fund_name, source_rate, current_rate, min_purchase,
                    returns_json, stock_codes_json, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code) do update set
                    fund_name=excluded.fund_name,
                    source_rate=excluded.source_rate,
                    current_rate=excluded.current_rate,
                    min_purchase=excluded.min_purchase,
                    returns_json=excluded.returns_json,
                    stock_codes_json=excluded.stock_codes_json,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                (
                    snapshot["fund_code"],
                    snapshot.get("fund_name", ""),
                    snapshot.get("source_rate"),
                    snapshot.get("current_rate"),
                    snapshot.get("min_purchase"),
                    _json_dumps(snapshot.get("returns", {})),
                    _json_dumps(snapshot.get("stock_codes", [])),
                    snapshot.get("source", "eastmoney.snapshot"),
                    now,
                ),
            )
        return 1

    def upsert_stock_holdings(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into stock_holdings (
                    fund_code, report_period, stock_code, stock_name, net_value_ratio,
                    shares, market_value, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, report_period, stock_code) do update set
                    stock_name=excluded.stock_name,
                    net_value_ratio=excluded.net_value_ratio,
                    shares=excluded.shares,
                    market_value=excluded.market_value,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("report_period", ""),
                        str(row.get("stock_code", "")).zfill(6),
                        row.get("stock_name", ""),
                        row.get("net_value_ratio"),
                        row.get("shares"),
                        row.get("market_value"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_profile(self, profile: dict[str, Any]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                insert into fund_profiles (
                    fund_code, fund_name, full_name, fund_type, issue_date,
                    establishment_date, asset_size, asset_size_date, fund_company,
                    custodian, manager, benchmark, tracking_target, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code) do update set
                    fund_name=excluded.fund_name,
                    full_name=excluded.full_name,
                    fund_type=excluded.fund_type,
                    issue_date=excluded.issue_date,
                    establishment_date=excluded.establishment_date,
                    asset_size=excluded.asset_size,
                    asset_size_date=excluded.asset_size_date,
                    fund_company=excluded.fund_company,
                    custodian=excluded.custodian,
                    manager=excluded.manager,
                    benchmark=excluded.benchmark,
                    tracking_target=excluded.tracking_target,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                (
                    normalize_fund_code(profile["fund_code"]),
                    profile.get("fund_name", ""),
                    profile.get("full_name", ""),
                    profile.get("fund_type", ""),
                    profile.get("issue_date", ""),
                    profile.get("establishment_date", ""),
                    profile.get("asset_size"),
                    profile.get("asset_size_date", ""),
                    profile.get("fund_company", ""),
                    profile.get("custodian", ""),
                    profile.get("manager", ""),
                    profile.get("benchmark", ""),
                    profile.get("tracking_target", ""),
                    profile.get("source", ""),
                    now,
                ),
            )
        return 1

    def upsert_bond_holdings(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into bond_holdings (
                    fund_code, report_period, bond_code, bond_name, net_value_ratio,
                    market_value, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, report_period, bond_code) do update set
                    bond_name=excluded.bond_name,
                    net_value_ratio=excluded.net_value_ratio,
                    market_value=excluded.market_value,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("report_period", ""),
                        str(row.get("bond_code", "")),
                        row.get("bond_name", ""),
                        row.get("net_value_ratio"),
                        row.get("market_value"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_industry_allocations(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into industry_allocations (
                    fund_code, report_period, industry_name, net_value_ratio,
                    market_value, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, report_period, industry_name) do update set
                    net_value_ratio=excluded.net_value_ratio,
                    market_value=excluded.market_value,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("report_period", ""),
                        row.get("industry_name", ""),
                        row.get("net_value_ratio"),
                        row.get("market_value"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_fee_structures(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into fee_structures (
                    fund_code, fee_type, condition_name, fee, fee_text,
                    discount_fee, discount_fee_text, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, fee_type, condition_name) do update set
                    fee=excluded.fee,
                    fee_text=excluded.fee_text,
                    discount_fee=excluded.discount_fee,
                    discount_fee_text=excluded.discount_fee_text,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("fee_type", ""),
                        row.get("condition_name", ""),
                        row.get("fee"),
                        row.get("fee_text", ""),
                        row.get("discount_fee"),
                        row.get("discount_fee_text", ""),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_dividends(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into dividends (
                    fund_code, dividend_date, ex_dividend_date, dividend_per_share,
                    payment_date, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                on conflict(fund_code, dividend_date) do update set
                    ex_dividend_date=excluded.ex_dividend_date,
                    dividend_per_share=excluded.dividend_per_share,
                    payment_date=excluded.payment_date,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("dividend_date", ""),
                        row.get("ex_dividend_date", ""),
                        row.get("dividend_per_share"),
                        row.get("payment_date", ""),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_splits(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalize_fund_code(fund_code)
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into splits (
                    fund_code, split_date, split_type, split_ratio, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?)
                on conflict(fund_code, split_date) do update set
                    split_type=excluded.split_type,
                    split_ratio=excluded.split_ratio,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        code,
                        row.get("split_date", ""),
                        row.get("split_type", ""),
                        row.get("split_ratio"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def upsert_fund_managers(self, rows: list[dict[str, Any]]) -> int:
        now = utc_now()
        with self.connect() as conn:
            conn.executemany(
                """
                insert into fund_managers (
                    manager_name, company, current_fund_codes, current_funds,
                    tenure_days, current_aum, best_return, source, fetched_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(manager_name, company, current_fund_codes) do update set
                    current_funds=excluded.current_funds,
                    tenure_days=excluded.tenure_days,
                    current_aum=excluded.current_aum,
                    best_return=excluded.best_return,
                    source=excluded.source,
                    fetched_at=excluded.fetched_at
                """,
                [
                    (
                        row.get("manager_name", ""),
                        row.get("company", ""),
                        row.get("current_fund_codes", ""),
                        row.get("current_funds", ""),
                        row.get("tenure_days"),
                        row.get("current_aum"),
                        row.get("best_return"),
                        row.get("source", ""),
                        now,
                    )
                    for row in rows
                ],
            )
        return len(rows)

    def record_raw_response(self, source: str, request_key: str, raw_text: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into raw_responses (source, request_key, fetched_at, raw_text)
                values (?, ?, ?, ?)
                on conflict(source, request_key) do update set
                    fetched_at=excluded.fetched_at,
                    raw_text=excluded.raw_text
                """,
                (source, request_key, utc_now(), raw_text),
            )

    def record_sync_run(
        self,
        *,
        operation: str,
        fund_code: str | None,
        status: str,
        rows_changed: int,
        started_at: str,
        message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into sync_runs (
                    operation, fund_code, status, rows_changed, started_at, finished_at, message
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (operation, fund_code, status, rows_changed, started_at, utc_now(), message),
            )

    def record_sync_failure(
        self,
        *,
        batch_id: str,
        operation: str,
        fund_code: str | None,
        provider: str,
        message: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into sync_failures (
                    batch_id, operation, fund_code, provider, message, failed_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    operation,
                    normalize_fund_code(fund_code) if fund_code else None,
                    provider,
                    message,
                    utc_now(),
                ),
            )

    def export_table(self, table: str, fund_code: str | None = None) -> list[dict[str, Any]]:
        allowed = {
            "funds",
            "nav_history",
            "snapshots",
            "raw_responses",
            "sync_runs",
            "sync_failures",
            "stock_holdings",
            "fund_profiles",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
            "fund_managers",
        }
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        sql = f"select * from {table}"
        params: tuple[str, ...] = ()
        if fund_code and table in {
            "funds",
            "nav_history",
            "snapshots",
            "sync_runs",
            "sync_failures",
            "stock_holdings",
            "fund_profiles",
            "bond_holdings",
            "industry_allocations",
            "fee_structures",
            "dividends",
            "splits",
        }:
            sql += " where fund_code = ?"
            params = (normalize_fund_code(fund_code),)
        sql += " order by 1"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def coverage_rows(self, fund_code: str | None = None) -> list[dict[str, Any]]:
        where = ""
        params: tuple[str, ...] = ()
        if fund_code:
            where = "where f.fund_code = ?"
            params = (normalize_fund_code(fund_code),)
        sql = f"""
            select
                f.fund_code,
                f.fund_name,
                case when p.fund_code is null then 0 else 1 end as has_profile,
                (select count(*) from nav_history n where n.fund_code = f.fund_code) as nav_rows,
                (select count(*) from stock_holdings s where s.fund_code = f.fund_code) as stock_holding_rows,
                (select count(*) from bond_holdings b where b.fund_code = f.fund_code) as bond_holding_rows,
                (select count(*) from industry_allocations i where i.fund_code = f.fund_code) as industry_rows,
                (select count(*) from fee_structures fs where fs.fund_code = f.fund_code) as fee_rows,
                (select count(*) from dividends d where d.fund_code = f.fund_code) as dividend_rows,
                (select count(*) from splits sp where sp.fund_code = f.fund_code) as split_rows
            from funds f
            left join fund_profiles p on p.fund_code = f.fund_code
            {where}
            order by f.fund_code
        """
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]


def search_funds(
    keyword: str,
    *,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    if raw_text is not None:
        rows = parse_search_results(raw_text)
        source = "eastmoney.search"
        raw = raw_text
    elif client is not None:
        raw = client.search(keyword)
        rows = parse_search_results(raw)
        source = "eastmoney.search"
    else:
        result = run_provider_chain(
            build_providers(provider, capability="search"), "search_funds", keyword
        )
        rows = result.rows
        source = f"{result.provider}.search"
        raw = _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
    if persist:
        store = FundDataStore(db_path)
        store.upsert_funds(rows)
        store.record_raw_response(source, keyword, raw)
    return rows


def fetch_fund_list(
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    if raw_text is not None:
        rows = parse_search_results(raw_text)
        source = "eastmoney.fundcode_search"
        raw = raw_text
    else:
        result = run_provider_chain(build_providers(provider, capability="fund_list"), "fund_list")
        rows = result.rows
        source = f"{result.provider}.fund_list"
        raw = _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
    if persist:
        store = FundDataStore(db_path)
        store.upsert_funds(rows)
        store.record_raw_response(source, "all", raw)
    return rows


def fetch_nav_history(
    code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    per: int = 20,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    if raw_text is not None:
        raw = raw_text
        rows = parse_nav_history(raw)
        source = "eastmoney.nav_history"
    elif client is not None:
        raw = client.nav_history(code, start_date=start_date, end_date=end_date, page=page, per=per)
        rows = parse_nav_history(raw)
        source = "eastmoney.nav_history"
    else:
        result = run_provider_chain(
            build_providers(provider, capability="nav_history"),
            "nav_history",
            code,
            start_date=start_date,
            end_date=end_date,
            page=page,
            per=per,
        )
        rows = result.rows
        source = f"{result.provider}.nav_history"
        raw = _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures})
    if persist:
        request_key = (
            f"{normalize_fund_code(code)}:{start_date or ''}:{end_date or ''}:{page}:{per}"
        )
        store = FundDataStore(db_path)
        store.upsert_nav_history(code, rows)
        store.record_raw_response(source, request_key, raw)
    return rows


def fetch_snapshot(
    code: str,
    *,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    persist: bool = True,
    raw_text: str | None = None,
    provider: str = PROVIDER_AUTO,
) -> dict[str, Any] | None:
    if raw_text is not None:
        raw = raw_text
        snapshot = parse_snapshot(raw, default_code=code)
        source = "eastmoney.snapshot"
    elif client is not None:
        raw = client.snapshot(code)
        snapshot = parse_snapshot(raw, default_code=code)
        source = "eastmoney.snapshot"
    else:
        result = run_provider_chain(
            build_providers(provider, capability="snapshot"), "snapshot", code
        )
        snapshot = result.rows
        source = f"{result.provider}.snapshot"
        raw = _json_dumps(
            {"provider": result.provider, "snapshot": snapshot, "failures": result.failures}
        )
    if persist:
        # Back-end share classes (000002, 000012, ...) yield an
        # empty Eastmoney page; parse_snapshot returns None and the
        # EastmoneyProvider layer converts that to an empty dict.
        # Either signal means "no snapshot available" -- do not
        # write a half-row to the data base.
        if snapshot:
            store = FundDataStore(db_path)
            store.upsert_snapshot(snapshot)
            store.record_raw_response(source, normalize_fund_code(code), raw)
    return snapshot


def fetch_stock_holdings(
    code: str,
    *,
    report_year: str | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        build_providers(provider, capability="stock_holdings"),
        "stock_holdings",
        code,
        report_year=report_year,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_stock_holdings(code, rows)
        store.record_raw_response(
            f"{result.provider}.stock_holdings",
            f"{normalize_fund_code(code)}:{report_year or ''}",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_profile(
    code: str,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> dict[str, Any]:
    result = run_provider_chain(build_providers(provider, capability="profile"), "profile", code)
    profile = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_profile(profile)
        store.record_raw_response(
            f"{result.provider}.profile",
            normalize_fund_code(code),
            _json_dumps(
                {"provider": result.provider, "profile": profile, "failures": result.failures}
            ),
        )
    return profile


def fetch_bond_holdings(
    code: str,
    *,
    report_year: str | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        build_providers(provider, capability="bond_holdings"),
        "bond_holdings",
        code,
        report_year=report_year,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_bond_holdings(code, rows)
        store.record_raw_response(
            f"{result.provider}.bond_holdings",
            f"{normalize_fund_code(code)}:{report_year or ''}",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_industry_allocations(
    code: str,
    *,
    report_year: str | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        build_providers(provider, capability="industry_allocations"),
        "industry_allocations",
        code,
        report_year=report_year,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_industry_allocations(code, rows)
        store.record_raw_response(
            f"{result.provider}.industry_allocations",
            f"{normalize_fund_code(code)}:{report_year or ''}",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_fee_structures(
    code: str,
    *,
    indicators: list[str] | None = None,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        build_providers(provider, capability="fee_structures"),
        "fee_structures",
        code,
        indicators=indicators,
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_fee_structures(code, rows)
        store.record_raw_response(
            f"{result.provider}.fee_structures",
            normalize_fund_code(code),
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_dividends(
    code: str,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        build_providers(provider, capability="dividends"), "dividends", code, allow_empty=True
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_dividends(code, rows)
        store.record_raw_response(
            f"{result.provider}.dividends",
            normalize_fund_code(code),
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_splits(
    code: str,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        build_providers(provider, capability="splits"), "splits", code, allow_empty=True
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_splits(code, rows)
        store.record_raw_response(
            f"{result.provider}.splits",
            normalize_fund_code(code),
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def fetch_fund_managers(
    code: str | None = None,
    *,
    db_path: str | Path | None = None,
    persist: bool = True,
    provider: str = PROVIDER_AUTO,
) -> list[dict[str, Any]]:
    result = run_provider_chain(
        build_providers(provider, capability="fund_managers"), "fund_managers", code
    )
    rows = result.rows
    if persist:
        store = FundDataStore(db_path)
        store.upsert_fund_managers(rows)
        store.record_raw_response(
            f"{result.provider}.fund_managers",
            normalize_fund_code(code) if code else "all",
            _json_dumps({"provider": result.provider, "rows": rows, "failures": result.failures}),
        )
    return rows


def coverage_rows(
    *,
    db_path: str | Path | None = None,
    fund_code: str | None = None,
) -> list[dict[str, Any]]:
    return FundDataStore(db_path).coverage_rows(fund_code=fund_code)


def coverage_report(
    *,
    db_path: str | Path | None = None,
    codes: list[str] | tuple[str, ...] | None = None,
    fund_type: str | None = None,
    only_incomplete: bool = False,
    min_completeness: float = 0.0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return per-fund coverage with completeness score.

    Each row carries a `completeness` value in [0, 1] (8 dataset columns weighted equally)
    and a `missing` list of dataset names that are empty for that fund.
    """
    code_list = normalize_fund_codes(codes) if codes else None
    store = FundDataStore(db_path)

    where_clauses: list[str] = []
    params: list[str] = []
    if code_list:
        placeholders = ",".join("?" * len(code_list))
        where_clauses.append(f"f.fund_code in ({placeholders})")
        params.extend(code_list)
    if fund_type:
        where_clauses.append("f.fund_type like ?")
        params.append(f"%{fund_type}%")
    where = (" where " + " and ".join(where_clauses)) if where_clauses else ""

    limit_clause = f" limit {int(limit)}" if limit else ""

    sql = f"""
        select
            f.fund_code,
            f.fund_name,
            f.fund_type,
            case when p.fund_code is null then 0 else 1 end as has_profile,
            (select count(*) from nav_history n where n.fund_code = f.fund_code) as nav_rows,
            (select count(*) from stock_holdings s where s.fund_code = f.fund_code) as stock_holding_rows,
            (select count(*) from bond_holdings b where b.fund_code = f.fund_code) as bond_holding_rows,
            (select count(*) from industry_allocations i where i.fund_code = f.fund_code) as industry_rows,
            (select count(*) from fee_structures fs where fs.fund_code = f.fund_code) as fee_rows,
            (select count(*) from dividends d where d.fund_code = f.fund_code) as dividend_rows,
            (select count(*) from splits sp where sp.fund_code = f.fund_code) as split_rows
        from funds f
        left join fund_profiles p on p.fund_code = f.fund_code
        {where}
        order by f.fund_code
        {limit_clause}
    """

    DATASETS = [
        ("has_profile", "profile"),
        ("nav_rows", "nav"),
        ("stock_holding_rows", "stock_holdings"),
        ("bond_holding_rows", "bond_holdings"),
        ("industry_rows", "industry"),
        ("fee_rows", "fees"),
        ("dividend_rows", "dividends"),
        ("split_rows", "splits"),
    ]

    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    for row in rows:
        present = 0
        missing: list[str] = []
        for column, name in DATASETS:
            value = row.get(column) or 0
            if value:
                present += 1
            else:
                missing.append(name)
        row["completeness"] = round(present / len(DATASETS), 4)
        row["missing"] = missing

    if only_incomplete:
        rows = [r for r in rows if r["completeness"] < 1.0]
    if min_completeness > 0:
        rows = [r for r in rows if r["completeness"] >= min_completeness]

    return rows


def _fund_row_from_sync(
    code: str,
    *,
    snapshot: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    profile = profile or {}
    return {
        "fund_code": normalize_fund_code(code),
        "fund_name": profile.get("fund_name")
        or snapshot.get("fund_name")
        or normalize_fund_code(code),
        "fund_type": profile.get("fund_type", ""),
        "company": profile.get("fund_company", ""),
        "manager": profile.get("manager", ""),
        "nav": None,
        "nav_date": "",
        "other_names": "",
        "source": profile.get("source") or snapshot.get("source", "sync"),
    }


def sync_fund(
    code: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    per: int = 50,
    db_path: str | Path | None = None,
    client: FundDataClient | None = None,
    provider: str = PROVIDER_AUTO,
    include_holdings: bool = False,
    include_profile: bool = False,
    include_bonds: bool = False,
    include_industries: bool = False,
    include_fees: bool = False,
    include_distributions: bool = False,
    include_managers: bool = False,
    include_all: bool = False,
    report_year: str | None = None,
    fee_indicators: list[str] | None = None,
) -> dict[str, Any]:
    started_at = utc_now()
    store = FundDataStore(db_path)
    code = normalize_fund_code(code)
    if include_all:
        include_holdings = True
        include_profile = True
        include_bonds = True
        include_industries = True
        include_fees = True
        include_distributions = True
        include_managers = True
    rows_changed = 0
    dataset_errors: list[dict[str, str]] = []

    def record_dataset_error(dataset: str, exc: Exception) -> None:
        dataset_errors.append({"dataset": dataset, "message": str(exc)})

    try:
        snapshot = fetch_snapshot(
            code, db_path=db_path, client=client, persist=True, provider=provider
        )
        # Back-end share classes have no standalone Eastmoney
        # page; treat "no snapshot available" (None or empty dict)
        # as a soft skip rather than aborting the whole sync.
        snapshot_count = 1 if snapshot else 0
        rows_changed += snapshot_count

        profile_count = 0
        profile: dict[str, Any] | None = None
        if include_profile:
            try:
                profile = fetch_profile(code, db_path=db_path, persist=True, provider=provider)
                profile_count = 1
                rows_changed += profile_count
            except Exception as exc:
                record_dataset_error("profile", exc)

        fund_count = store.upsert_funds(
            [_fund_row_from_sync(code, snapshot=snapshot, profile=profile)]
        )
        rows_changed += fund_count

        nav_rows = fetch_nav_history(
            code,
            start_date=start_date,
            end_date=end_date,
            page=page,
            per=per,
            db_path=db_path,
            client=client,
            persist=True,
            provider=provider,
        )
        nav_count = len(nav_rows)
        rows_changed += nav_count

        holdings_count = 0
        if include_holdings:
            try:
                holding_rows = fetch_stock_holdings(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                holdings_count = len(holding_rows)
                rows_changed += holdings_count
            except Exception as exc:
                record_dataset_error("stock_holdings", exc)

        bond_count = 0
        if include_bonds:
            try:
                bond_rows = fetch_bond_holdings(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                bond_count = len(bond_rows)
                rows_changed += bond_count
            except Exception as exc:
                record_dataset_error("bond_holdings", exc)

        industry_count = 0
        if include_industries:
            try:
                industry_rows = fetch_industry_allocations(
                    code, report_year=report_year, db_path=db_path, persist=True, provider=provider
                )
                industry_count = len(industry_rows)
                rows_changed += industry_count
            except Exception as exc:
                record_dataset_error("industry_allocations", exc)

        fee_count = 0
        if include_fees:
            try:
                fee_rows = fetch_fee_structures(
                    code,
                    indicators=fee_indicators,
                    db_path=db_path,
                    persist=True,
                    provider=provider,
                )
                fee_count = len(fee_rows)
                rows_changed += fee_count
            except Exception as exc:
                record_dataset_error("fee_structures", exc)

        dividend_count = 0
        split_count = 0
        if include_distributions:
            try:
                dividend_rows = fetch_dividends(
                    code, db_path=db_path, persist=True, provider=provider
                )
                dividend_count = len(dividend_rows)
                rows_changed += dividend_count
            except Exception as exc:
                record_dataset_error("dividends", exc)
            try:
                split_rows = fetch_splits(code, db_path=db_path, persist=True, provider=provider)
                split_count = len(split_rows)
                rows_changed += split_count
            except Exception as exc:
                record_dataset_error("splits", exc)

        manager_count = 0
        if include_managers:
            try:
                manager_rows = fetch_fund_managers(
                    code, db_path=db_path, persist=True, provider=provider
                )
                manager_count = len(manager_rows)
                rows_changed += manager_count
            except Exception as exc:
                record_dataset_error("fund_managers", exc)

        coverage = store.coverage_rows(fund_code=code)
        store.record_sync_run(
            operation="sync",
            fund_code=code,
            status="ok",
            rows_changed=rows_changed,
            started_at=started_at,
        )
        return {
            "fund_code": code,
            "status": "ok",
            "rows_changed": rows_changed,
            "fund_rows": fund_count,
            "snapshot_rows": snapshot_count,
            "nav_rows": nav_count,
            "holdings_rows": holdings_count,
            "stock_holding_rows": holdings_count,
            "profile_rows": profile_count,
            "bond_holding_rows": bond_count,
            "industry_rows": industry_count,
            "fee_rows": fee_count,
            "dividend_rows": dividend_count,
            "split_rows": split_count,
            "manager_rows": manager_count,
            "dataset_errors": dataset_errors,
            "coverage": coverage[0] if coverage else {},
        }
    except Exception as exc:
        store.record_sync_run(
            operation="sync",
            fund_code=code,
            status="error",
            rows_changed=rows_changed,
            started_at=started_at,
            message=str(exc),
        )
        raise


def batch_sync_funds(
    codes: list[str] | tuple[str, ...],
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    page: int = 1,
    per: int = 50,
    db_path: str | Path | None = None,
    provider: str = PROVIDER_AUTO,
    include_holdings: bool = False,
    include_profile: bool = False,
    include_bonds: bool = False,
    include_industries: bool = False,
    include_fees: bool = False,
    include_distributions: bool = False,
    include_managers: bool = False,
    include_all: bool = False,
    report_year: str | None = None,
    fee_indicators: list[str] | None = None,
    batch_id: str | None = None,
    stop_on_error: bool = False,
    concurrency: int = 1,
    min_interval_seconds: float | None = None,
) -> dict[str, Any]:
    code_list = normalize_fund_codes(codes)
    if not code_list:
        raise ValueError("no fund codes provided for batch sync")

    batch_id = batch_id or f"batch-{utc_now()}"
    store = FundDataStore(db_path)
    results: list[dict[str, Any]] = []
    ok_count = 0
    failed_count = 0

    concurrency = max(1, int(concurrency))
    if min_interval_seconds is None:
        min_interval_seconds = 0.25 if concurrency > 1 else 1.0

    sync_kwargs: dict[str, Any] = {
        "start_date": start_date,
        "end_date": end_date,
        "page": page,
        "per": per,
        "db_path": db_path,
        "provider": provider,
        "include_holdings": include_holdings,
        "include_profile": include_profile,
        "include_bonds": include_bonds,
        "include_industries": include_industries,
        "include_fees": include_fees,
        "include_distributions": include_distributions,
        "include_managers": include_managers,
        "include_all": include_all,
        "report_year": report_year,
        "fee_indicators": fee_indicators,
    }
    if provider == PROVIDER_EASTMONEY:
        sync_kwargs["client"] = FundDataClient(
            min_interval_seconds=min_interval_seconds,
            rate_limiter=_RateLimiter(min_interval_seconds) if concurrency > 1 else None,
        )

    def _run(code: str) -> dict[str, Any]:
        try:
            result = sync_fund(code, **sync_kwargs)
            result.setdefault("fund_code", code)
            result.setdefault("status", "ok")
            return result
        except Exception as exc:
            return {"fund_code": code, "status": "error", "message": str(exc)}

    if concurrency <= 1:
        for code in code_list:
            outcome = _run(code)
            if outcome.get("status") == "ok":
                results.append(outcome)
                ok_count += 1
            else:
                store.record_sync_failure(
                    batch_id=batch_id,
                    operation="batch-sync",
                    fund_code=outcome["fund_code"],
                    provider=provider,
                    message=outcome.get("message", ""),
                )
                results.append(outcome)
                failed_count += 1
                if stop_on_error:
                    raise ProviderError(outcome.get("message", ""))
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_code = {executor.submit(_run, code): code for code in code_list}
            for future in as_completed(future_to_code):
                outcome = future.result()
                if outcome.get("status") == "ok":
                    results.append(outcome)
                    ok_count += 1
                else:
                    store.record_sync_failure(
                        batch_id=batch_id,
                        operation="batch-sync",
                        fund_code=outcome["fund_code"],
                        provider=provider,
                        message=outcome.get("message", ""),
                    )
                    results.append(outcome)
                    failed_count += 1
                    if stop_on_error:
                        for pending in future_to_code:
                            pending.cancel()
                        raise ProviderError(outcome.get("message", ""))

    coverage: list[dict[str, Any]] = []
    for code in code_list:
        coverage.extend(store.coverage_rows(fund_code=code))

    return {
        "batch_id": batch_id,
        "total": len(code_list),
        "ok": ok_count,
        "failed": failed_count,
        "concurrency": concurrency,
        "min_interval_seconds": min_interval_seconds,
        "results": results,
        "coverage": coverage,
    }


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
