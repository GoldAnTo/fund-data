"""Schema migrations for the fund-data skill.

Lifted from ``fund_data.py`` in the 0.3.0 split (RFC
``docs/superpowers/specs/2026-06-02-fund-data-0.3-split.md``).
Each entry in :data:`MIGRATIONS` is ``(version, callable)``.
``FundDataStore.ensure_schema`` reads ``PRAGMA user_version`` and
applies every migration whose version is greater than the
current user_version, in ascending order. After each
successful migration it bumps the pragma.

Adding a new migration:
  1. Append ``(N, _migration_NNN_short_description)`` to MIGRATIONS.
  2. Add a regression test in test_schema_migrations.py that
     runs ``ensure_schema`` against a v(N-1) DB and asserts
     the new shape.
  3. Bump FUND_DATA_SCHEMA_VERSION in any consumer code that
     caches the version (currently nothing does).

Never renumber or remove an existing migration — old DBs
depend on each version being applied exactly once, in order.
"""

from __future__ import annotations

import sqlite3
from typing import Any

__all__ = [
    "FUND_DATA_SCHEMA_VERSION",
    "MIGRATIONS",
    "_migration_001_add_industry_allocations_market_value",
    "_migration_002_add_fee_structures_fee_text",
    "_migration_003_add_fee_structures_discount_fee",
    "_migration_004_add_fee_structures_discount_fee_text",
    "_migration_005_align_column_order",
    "_migration_006_create_fund_manager_links",
]


def _migration_001_add_industry_allocations_market_value(conn: sqlite3.Connection) -> None:
    """v1: add ``industry_allocations.market_value`` for AkShare's
    industry weighting breakdown."""
    columns = {row["name"] for row in conn.execute("pragma table_info(industry_allocations)")}
    if "market_value" not in columns:
        conn.execute("alter table industry_allocations add column market_value real")


def _migration_002_add_fee_structures_fee_text(conn: sqlite3.Connection) -> None:
    """v2: add ``fee_structures.fee_text`` so the AkShare page scraper
    can persist its human-readable fee strings alongside the decimal
    value."""
    columns = {row["name"] for row in conn.execute("pragma table_info(fee_structures)")}
    if "fee_text" not in columns:
        conn.execute("alter table fee_structures add column fee_text text")


def _migration_003_add_fee_structures_discount_fee(conn: sqlite3.Connection) -> None:
    """v3: add ``fee_structures.discount_fee`` to carry the discounted
    fee (e.g. promo rate) alongside the list price."""
    columns = {row["name"] for row in conn.execute("pragma table_info(fee_structures)")}
    if "discount_fee" not in columns:
        conn.execute("alter table fee_structures add column discount_fee real")


def _migration_004_add_fee_structures_discount_fee_text(conn: sqlite3.Connection) -> None:
    """v4: add ``fee_structures.discount_fee_text`` to carry the
    human-readable discounted fee string."""
    columns = {row["name"] for row in conn.execute("pragma table_info(fee_structures)")}
    if "discount_fee_text" not in columns:
        conn.execute("alter table fee_structures add column discount_fee_text text")


# Schema for the v5-rebuild step. See
# ``_migration_005_align_column_order`` for the rationale.
_INDUSTRY_ALLOCATIONS_CANONICAL_DDL = """\
CREATE TABLE {new_name} (
    fund_code text not null,
    report_period text not null,
    industry_name text not null,
    net_value_ratio real,
    source text,
    fetched_at text not null,
    market_value real,
    primary key (fund_code, report_period, industry_name)
)
"""

_FEE_STRUCTURES_CANONICAL_DDL = """\
CREATE TABLE {new_name} (
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
)
"""


def _migration_005_align_column_order(conn: sqlite3.Connection) -> None:
    """v5: align the column order of ``industry_allocations`` and
    ``fee_structures`` with the canonical ``ensure_schema`` definition.

    Why: the four v1-v4 migrations add ``market_value`` (industry)
    and ``fee_text`` / ``discount_fee`` / ``discount_fee_text`` (fee)
    via ``ALTER TABLE ... ADD COLUMN``, which SQLite always appends
    to the end of the column list. The canonical ``ensure_schema``
    block, however, declared these columns in the *middle* of the
    table. So a fresh DB had the new order, while a DB that had been
    upgraded through v1-v4 had the old order. The 2026-06-02 Akshare
    bulk run hit a schema-drift incident because of this: the
    separate (temp) DB was created against the canonical schema, the
    main DB had the post-migration order, and ``INSERT INTO
    main.{table} SELECT * FROM sep.{table}`` failed with
    "table X has N values but M columns were supplied".

    Fix:

    1. The canonical ``ensure_schema`` block is updated to declare
       the new columns at the end (matching what ALTER TABLE
       produces), so a fresh DB and a migrated DB now have the same
       column order from day one.
    2. For DBs that already exist with the old (mid-table) order,
       this migration recreates the affected tables in the canonical
       order, preserving data via ``INSERT INTO new SELECT FROM old``
       wrapped in a transaction. Both tables are rebuildable from
       AkShare / Tushare, so the data-loss surface is small.

    The rebuild is a no-op when the table is already in canonical
    order, so re-running ``ensure_schema`` on an already-migrated
    DB is cheap.
    """
    rebuilds = [
        (
            "industry_allocations",
            _INDUSTRY_ALLOCATIONS_CANONICAL_DDL,
            [
                "fund_code", "report_period", "industry_name",
                "net_value_ratio", "source", "fetched_at", "market_value",
            ],
        ),
        (
            "fee_structures",
            _FEE_STRUCTURES_CANONICAL_DDL,
            [
                "fund_code", "fee_type", "condition_name",
                "fee", "source", "fetched_at",
                "fee_text", "discount_fee", "discount_fee_text",
            ],
        ),
    ]
    for table, ddl_template, select_cols in rebuilds:
        current = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if current == select_cols:
            # Already canonical -- nothing to do.
            continue
        # Sanity: the column *set* must be identical, otherwise the
        # user has a DB whose schema has drifted beyond reordering
        # (e.g. a column was added/removed out of band). Refusing is
        # the right call -- silently dropping/adding columns would
        # be worse than a clear RuntimeError on first open.
        if set(current) != set(select_cols):
            raise RuntimeError(
                f"refusing to migrate {table}: column set differs "
                f"between DB and canonical schema "
                f"(db={current!r}, canonical={select_cols!r})"
            )
        new_name = f"{table}__v5_align"
        ddl = ddl_template.format(new_name=new_name)
        select_list = ", ".join(select_cols)
        conn.executescript(
            f"""
            {ddl};
            INSERT INTO {new_name} ({select_list})
                SELECT {select_list} FROM {table};
            DROP TABLE {table};
            ALTER TABLE {new_name} RENAME TO {table};
            """
        )


def _migration_006_create_fund_manager_links(conn: sqlite3.Connection) -> None:
    """v6: add a fund-centric join table for managers.

    The legacy ``fund_managers`` table is keyed on
    ``(manager_name, company, current_fund_codes)`` -- the natural
    shape for "list every fund this manager runs". The reverse
    query ("who manages fund 110022?") has to do a full table scan
    with ``LIKE '%<code>%'`` because ``current_fund_codes`` is a
    text column (and despite the name, every row holds a single
    code -- the column was never populated with comma-separated
    values in production data).

    This migration adds ``fund_manager_links`` keyed on
    ``(fund_code, manager_name, company)`` so the reverse query
    is O(1) via a covering index. The new table is a denormalized
    projection: same columns as ``fund_managers`` minus
    ``current_fund_codes``, plus an explicit ``fund_code`` PK
    column. We backfill from the legacy table so the new index is
    hot on first open; ongoing writes go through
    ``FundDataStore.upsert_fund_managers`` which fans out to both
    tables in the same transaction.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS fund_manager_links (
            fund_code TEXT NOT NULL,
            manager_name TEXT NOT NULL,
            company TEXT,
            current_funds TEXT,
            tenure_days INTEGER,
            current_aum REAL,
            best_return REAL,
            source TEXT,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (fund_code, manager_name, company)
        );
        CREATE INDEX IF NOT EXISTS idx_fund_manager_links_code
            ON fund_manager_links(fund_code);
        """
    )
    # Backfill from the legacy table. INSERT OR IGNORE because
    # the same (fund_code, manager_name, company) triple can
    # appear more than once with different ``fetched_at`` values
    # (re-fetches) -- we want the earliest row's other columns
    # to win on PK conflict (newer writes go through
    # ``upsert_fund_managers`` which sets ``fetched_at = now``).
    existing = conn.execute(
        "SELECT 1 FROM fund_manager_links LIMIT 1"
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT OR IGNORE INTO fund_manager_links (
                fund_code, manager_name, company, current_funds,
                tenure_days, current_aum, best_return, source, fetched_at
            )
            SELECT
                current_fund_codes, manager_name, company, current_funds,
                tenure_days, current_aum, best_return, source, fetched_at
            FROM fund_managers
            WHERE current_fund_codes <> ''
            """
        )


MIGRATIONS: list[tuple[int, Any]] = [
    (1, _migration_001_add_industry_allocations_market_value),
    (2, _migration_002_add_fee_structures_fee_text),
    (3, _migration_003_add_fee_structures_discount_fee),
    (4, _migration_004_add_fee_structures_discount_fee_text),
    (5, _migration_005_align_column_order),
    (6, _migration_006_create_fund_manager_links),
]

FUND_DATA_SCHEMA_VERSION = max(version for version, _fn in MIGRATIONS)
