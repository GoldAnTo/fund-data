"""FundDataStore: the SQLite persistence layer for the data plane.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
A single 730-line class that owns the SQLite connection,
the schema migration runner, and the per-table upsert /
count / coverage helpers. Splitting the class into per-
table modules is a separate 0.4.0 conversation; for 0.3.0
we just lift the file out of the package root so the
package's __init__.py is no longer a 1500-line dump.

Dependency direction: ``store`` depends on ``schema`` (for
``MIGRATIONS`` / ``FUND_DATA_SCHEMA_VERSION``) and
``normalizers`` (for ``normalize_fund_code``). It does
not import from ``fetch`` / ``sync`` / ``http`` / the four
``providers.*`` classes -- those are the caller side of
the store, not the callee.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import normalizers
from .paths import default_db_path, utc_now
from .schema.migrations import FUND_DATA_SCHEMA_VERSION, MIGRATIONS
from .normalizers import normalize_fund_code

__all__ = ["FundDataStore"]

logger = logging.getLogger("fund_data")


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
        code = normalizers.normalize_fund_code(fund_code)
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
                    normalizers._json_dumps(snapshot.get("returns", {})),
                    normalizers._json_dumps(snapshot.get("stock_codes", [])),
                    snapshot.get("source", "eastmoney.snapshot"),
                    now,
                ),
            )
        return 1

    def upsert_stock_holdings(self, fund_code: str, rows: list[dict[str, Any]]) -> int:
        code = normalizers.normalize_fund_code(fund_code)
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
                    normalizers.normalize_fund_code(profile["fund_code"]),
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
        code = normalizers.normalize_fund_code(fund_code)
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
        code = normalizers.normalize_fund_code(fund_code)
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
        code = normalizers.normalize_fund_code(fund_code)
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
        code = normalizers.normalize_fund_code(fund_code)
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
        code = normalizers.normalize_fund_code(fund_code)
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
                    normalizers.normalize_fund_code(fund_code) if fund_code else None,
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
            params = (normalizers.normalize_fund_code(fund_code),)
        sql += " order by 1"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def coverage_rows(self, fund_code: str | None = None) -> list[dict[str, Any]]:
        where = ""
        params: tuple[str, ...] = ()
        if fund_code:
            where = "where f.fund_code = ?"
            params = (normalizers.normalize_fund_code(fund_code),)
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


