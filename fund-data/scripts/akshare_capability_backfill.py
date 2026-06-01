"""Bulk backfill the five fund-detail capabilities that AkShare can serve
and the rest of the provider chain can not.

The currently covered surface of the data base has a profile at 98.87 %
but only 1.6 – 2.7 % coverage on the "akshare-only" capabilities
(stock_holdings, bond_holdings, industry_allocations, fee_structures,
dividends, splits). Each of those rows is small (a few hundred bytes
per fund per period) and the missing-fund count is ~26,400, so this
script walks the local ``funds`` table, asks the AkShare provider for
the missing rows for each code, and upserts the result via the
:mod:`fund_data` store.

Why 8-way concurrency? Benchmarks (see ``fund-data/AGENTS.md``) put
8 concurrent in-flight calls at the sweet spot — beyond that the
Eastmoney / AkShare upstream starts 5xx-throttling and the
throughput does not improve. ``min-interval-seconds=0.1`` keeps the
client polite.

The ``fund_managers`` capability is intentionally **not** included in
this script: it is ~10 s/fund on AkShare (a 9 h wall-clock run for
the full universe) and its table schema is manager-centric rather
than fund-centric, so a separate, longer-lived script will land it
later (see ``docs/investoday-api-catalog.md`` for the cheaper L1
endpoints to consider before paying for that).

Typical use::

    # Default: 26,400 funds x 5 capabilities x 3.3 s/fund / 8 conc = ~3 h
    .venv-akshare/bin/python3 scripts/akshare_capability_backfill.py

    # Smoke test
    .venv-akshare/bin/python3 scripts/akshare_capability_backfill.py \\
        --limit 50 --concurrency 2

    # Skip funds that already have a row in any of the target tables
    .venv-akshare/bin/python3 scripts/akshare_capability_backfill.py --skip-existing
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import logging
import sqlite3
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

logger = logging.getLogger("fund_data.akshare_capability_backfill")

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
DEFAULT_REPORT_YEAR = "2024"
DEFAULT_FEE_INDICATOR = "申购费率"

# Five capabilities that map cleanly to the per-fund row schema used
# by the rest of the store. The order here is the upsert order — the
# slower ones (fees) come last so the warm-up cost is paid for the
# bulk of the rows already.
CAPABILITIES: list[tuple[str, str, str]] = [
    # (capability name, source field on AkShare, args the method expects)
    ("stock_holdings", "stock_holdings", "stock_holdings"),
    ("bond_holdings", "bond_holdings", "bond_holdings"),
    ("industry_allocations", "industry_allocations", "industry_allocations"),
    ("dividends", "dividends", "dividends"),
    ("splits", "splits", "splits"),
    ("fee_structures", "fee_structures", "fee_structures"),
]

UPSERT_FNS: dict[str, Callable[[fund_data.FundDataStore, str, list[dict]], int]] = {
    "stock_holdings": lambda s, c, r: s.upsert_stock_holdings(c, r),
    "bond_holdings": lambda s, c, r: s.upsert_bond_holdings(c, r),
    "industry_allocations": lambda s, c, r: s.upsert_industry_allocations(c, r),
    "dividends": lambda s, c, r: s.upsert_dividends(c, r),
    "splits": lambda s, c, r: s.upsert_splits(c, r),
    "fee_structures": lambda s, c, r: s.upsert_fee_structures(c, r),
}


@dataclass
class SyncStats:
    fund_attempted: int = 0
    fund_succeeded: int = 0
    rows_upserted: int = 0
    capability_failures: dict[str, int] = None
    elapsed: float = 0.0

    def __post_init__(self) -> None:
        if self.capability_failures is None:
            self.capability_failures = defaultdict(int)


def _select_targets(
    db_path: Path,
    skip_existing: bool,
    capabilities: list[str],
    limit: int | None,
) -> list[str]:
    """Return the fund codes that still need at least one of the
    target capabilities. With ``skip_existing=False`` we still return
    every code so re-runs are explicit overwrites.
    """
    where = ""
    if skip_existing:
        # A fund is "in" if it has a row in *every* target table. We
        # only skip the ones that are fully covered.
        not_in_parts = " AND ".join(
            f"NOT EXISTS (SELECT 1 FROM {t} WHERE {t}.fund_code = f.fund_code)"
            for t in capabilities
        )
        where = f" WHERE {not_in_parts}"
    limit_clause = f" LIMIT {int(limit)}" if limit else ""
    sql = f"SELECT f.fund_code FROM funds f{where} ORDER BY f.fund_code{limit_clause}"
    with sqlite3.connect(db_path, timeout=30) as conn:
        return [r[0] for r in conn.execute(sql)]


# Tables that the per-fund upserts land in. The merge step replaces
# the *fund* (every row with the same ``fund_code``) rather than
# individual PK tuples — backfills always want "freshest data wins"
# for the whole fund, not a partial row-level update that would leave
# stale report_period rows behind.
MERGE_TABLES = (
    "stock_holdings",
    "bond_holdings",
    "industry_allocations",
    "dividends",
    "splits",
    "fee_structures",
)


def _merge_separate_db(separate_db: Path, main_db: Path) -> dict[str, int]:
    """Replace every fund touched by ``separate_db`` into ``main_db``,
    fund-by-fund, so the freshest per-fund data wins. Returns
    ``{table_name: rows_copied}``.

    For each table in :data:`MERGE_TABLES`:

    1. Find the distinct ``fund_code`` values present in ``sep.{table}``.
    2. ``DELETE FROM main.{table} WHERE fund_code IN (...)`` — wipes
       every stale row for those funds in the main DB.
    3. ``INSERT INTO main.{table} SELECT * FROM sep.{table}`` —
       copies the fresh rows in.

    The caller is responsible for ensuring ``separate_db`` already
    has the target schema (run :func:`FundDataStore.ensure_schema`
    on it before invoking this). The merge holds the main DB write
    lock for the duration, so run it once at the end of the bulk
    sync, not in a loop.
    """
    counts: dict[str, int] = {}
    if not separate_db.is_file():
        raise FileNotFoundError(f"separate DB not found: {separate_db}")
    with sqlite3.connect(main_db, timeout=30) as main_conn:
        # ATTACH is a connection-level setting. We must issue it on
        # the *target* connection so the rows land in main.
        main_conn.execute("ATTACH DATABASE ? AS sep", (str(separate_db),))
        try:
            main_conn.execute("BEGIN")
            for table in MERGE_TABLES:
                # 1) Find which fund_codes the separate DB has for this table.
                sep_rows = main_conn.execute(
                    f"SELECT DISTINCT fund_code FROM sep.{table}"
                ).fetchall()
                sep_codes = [r[0] for r in sep_rows]
                # 2) Wipe those funds from main so stale report_period
                #    rows don't linger behind the fresh ones.
                if sep_codes:
                    placeholders = ",".join("?" * len(sep_codes))
                    main_conn.execute(
                        f"DELETE FROM main.{table} WHERE fund_code IN ({placeholders})",
                        sep_codes,
                    )
                # 3) Copy fresh rows from sep into main.
                cur = main_conn.execute(
                    f"INSERT INTO main.{table} SELECT * FROM sep.{table}"
                )
                counts[table] = cur.rowcount
            main_conn.execute("COMMIT")
        except Exception:
            main_conn.execute("ROLLBACK")
            raise
        finally:
            main_conn.execute("DETACH DATABASE sep")
    return counts


def _sync_one_fund(
    code: str,
    provider: fund_data.AkshareProvider,
    store: fund_data.FundDataStore,
    report_year: str,
    fee_indicator: str,
) -> SyncStats:
    """Pull every target capability for one fund and upsert each.
    Run inside a worker thread — ``AkshareProvider`` is shared
    because it is essentially stateless."""
    stats = SyncStats()
    stats.fund_attempted = 1
    for cap, method_name, _store_name in CAPABILITIES:
        method = getattr(provider, method_name, None)
        if method is None:
            stats.capability_failures[cap] += 1
            continue
        try:
            if cap == "fee_structures":
                # ``AkshareProvider.fee_structures`` takes ``indicators`` (plural,
                # list of indicator names), not ``indicator``. Passing the wrong
                # kwarg raises ``TypeError`` and the fund gets recorded as a
                # capability-level failure with zero rows, which is why
                # ``fee_structures`` has stayed at ~700-fund coverage for the
                # whole backfill run.
                rows = method(code, indicators=[fee_indicator])
            elif cap in ("stock_holdings", "bond_holdings", "industry_allocations"):
                rows = method(code, report_year=report_year)
            else:
                rows = method(code)
        except Exception as exc:  # noqa: BLE001 — bulk runner must not abort
            logger.debug("akshare %s(%s) failed: %s", method_name, code, exc)
            stats.capability_failures[cap] += 1
            continue
        if not rows:
            # Empty list is a valid response — the fund genuinely has
            # no such rows (e.g. an index fund with no dividend).
            continue
        try:
            UPSERT_FNS[cap](store, code, rows)
            stats.rows_upserted += len(rows)
        except sqlite3.OperationalError as exc:
            logger.debug("upsert %s(%s) failed: %s", cap, code, exc)
            stats.capability_failures[cap] += 1
    if sum(stats.capability_failures.values()) < len(CAPABILITIES):
        stats.fund_succeeded = 1
    return stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of funds to sync (default: every fund).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip funds that already have a row in every target table. "
            "Use this to turn the script into an incremental backfill."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="In-flight HTTP calls (default: 8 — AkShare sweet spot).",
    )
    parser.add_argument(
        "--report-year",
        default=DEFAULT_REPORT_YEAR,
        help=f"Report year for stock/bond/industry (default: {DEFAULT_REPORT_YEAR}).",
    )
    parser.add_argument(
        "--fee-indicator",
        default=DEFAULT_FEE_INDICATOR,
        help=f"Fee indicator (default: {DEFAULT_FEE_INDICATOR}).",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print a progress line every N funds (default: 50).",
    )
    parser.add_argument(
        "--separate-db",
        default=None,
        help=(
            "Write to a fresh SQLite file at this path and merge the "
            "rows into the main DB at the end. Use this when the main "
            "DB is being written by another process (e.g. the main "
            "backfill) — avoids 'database is locked' crashes that "
            "would otherwise lose the 5-hour progress of the other writer."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db_path = Path(args.db)

    targets = _select_targets(db_path, args.skip_existing, [c[0] for c in CAPABILITIES], args.limit)
    if not targets:
        logger.info("nothing to do — every fund already has all target rows")
        return 0
    logger.info(
        "targeting %d funds x %d capabilities (concurrency=%d)",
        len(targets),
        len(CAPABILITIES),
        args.concurrency,
    )

    provider = fund_data.AkshareProvider()
    # In --separate-db mode, we run every upsert against the temp DB
    # and merge at the end. The schema mirrors the main DB so the
    # merge is a row-for-row INSERT OR REPLACE.
    if args.separate_db:
        separate_path = Path(args.separate_db)
        if separate_path.exists():
            logger.info("removing pre-existing %s", separate_path)
            separate_path.unlink()
        fund_data.FundDataStore(str(separate_path)).ensure_schema()
        store = fund_data.FundDataStore(str(separate_path))
        logger.info("writing to separate DB at %s; will merge at the end", separate_path)
    else:
        store = fund_data.FundDataStore(db_path)
    aggregate = SyncStats()
    t0 = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(
                _sync_one_fund,
                code,
                provider,
                store,
                args.report_year,
                args.fee_indicator,
            ): code
            for code in targets
        }
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            stats = fut.result()
            aggregate.fund_attempted += stats.fund_attempted
            aggregate.fund_succeeded += stats.fund_succeeded
            aggregate.rows_upserted += stats.rows_upserted
            for k, v in stats.capability_failures.items():
                aggregate.capability_failures[k] += v
            if i % args.progress_every == 0 or i == len(targets):
                elapsed = time.time() - t0
                eta = elapsed * (len(targets) - i) / i if i else 0
                logger.info(
                    "[%d/%d] ok=%d fail=%d rows=%d elapsed=%.0fs eta=%.0fs",
                    i,
                    len(targets),
                    aggregate.fund_succeeded,
                    sum(aggregate.capability_failures.values()),
                    aggregate.rows_upserted,
                    elapsed,
                    eta,
                )

    aggregate.elapsed = time.time() - t0
    logger.info(
        "DONE: ok=%d fail=%d rows_upserted=%d elapsed=%.0fs",
        aggregate.fund_succeeded,
        sum(aggregate.capability_failures.values()),
        aggregate.rows_upserted,
        aggregate.elapsed,
    )
    for cap in [c[0] for c in CAPABILITIES]:
        n = aggregate.capability_failures.get(cap, 0)
        if n:
            logger.info("  capability %s failed for %d funds", cap, n)

    if args.separate_db:
        separate_path = Path(args.separate_db)
        logger.info("merging %s into %s (INSERT OR REPLACE)", separate_path, db_path)
        counts = _merge_separate_db(separate_path, db_path)
        for table, n in counts.items():
            logger.info("  merged %d rows into %s", n, table)
        with contextlib.suppress(OSError):
            separate_path.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
