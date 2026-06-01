"""Backfill ``fee_structures`` for funds that still have zero rows.

Why this script exists
----------------------
The bulk :mod:`akshare_capability_backfill` runner calls
``AkshareProvider.fee_structures(code, indicator=...)``, but the client
method only accepts the plural ``indicators=[...]`` keyword. The
mismatched kwarg raises ``TypeError`` on every fund, which is why
``fee_structures`` coverage sat at ~700 funds for the entire
akshare-capability run.

The page-scrape fallback inside
``AkshareProvider._fee_structures_from_eastmoney_page`` is the cheaper
path (no AkShare dependency, ~0.27 s/fund, 3-10 rows each) and
produces the same ``fund_fee_page`` rows that
``fetch_fee_structures(provider='eastmoney')`` already returns. This
script reruns that page-scrape path for the ~26k funds that still
have zero ``fee_structures`` rows, in a small thread pool, and
upserts the result via :func:`FundDataStore.upsert_fee_structures``.

Typical use::

    # Default: 26k funds x 3 indicators, ~30 min wall-clock
    .venv-akshare/bin/python3 scripts/fee_only_backfill.py

    # Smoke test
    .venv-akshare/bin/python3 scripts/fee_only_backfill.py --limit 20
"""

from __future__ import annotations

import argparse
import concurrent.futures
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

logger = logging.getLogger("fund_data.fee_only_backfill")

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"

# Indicators that page-scrape returns reliably. ``申购与赎回金额``,
# ``交易状态`` and ``交易确认日`` are noisy/no-data on most pages and
# were dropped to keep the run under ~30 min for the full universe.
DEFAULT_INDICATORS = ("申购费率", "赎回费率", "运作费用")


@dataclass
class Stats:
    funds_attempted: int = 0
    funds_with_rows: int = 0
    rows_upserted: int = 0
    failed: int = 0
    elapsed: float = 0.0
    failed_codes: list[str] = field(default_factory=list)


def _select_missing_fee_funds(db_path: Path, limit: int | None) -> list[str]:
    """Funds that have a row in ``funds`` but zero rows in
    ``fee_structures``. The set is small enough to enumerate at
    script start (~26k for a fresh run, ~700 on a follow-up).
    """
    limit_clause = f" LIMIT {int(limit)}" if limit else ""
    sql = (
        "SELECT f.fund_code FROM funds f "
        "WHERE NOT EXISTS (SELECT 1 FROM fee_structures fs "
        "                  WHERE fs.fund_code = f.fund_code) "
        "ORDER BY f.fund_code" + limit_clause
    )
    with sqlite3.connect(db_path, timeout=30) as conn:
        return [r[0] for r in conn.execute(sql)]


def _scrape_one(
    code: str, provider: fund_data.AkshareProvider, indicators: list[str]
) -> tuple[str, list[dict]]:
    """Run only the eastmoney page-scrape path. The AkShare API path
    is the slow one (~1.7 s/fund x 7 indicators) and is what the bulk
    runner uses; the page scrape is enough to seed the dataset.
    """
    try:
        rows = provider._fee_structures_from_eastmoney_page(code, indicators)
    except Exception as exc:  # noqa: BLE001 — bulk runner must not abort
        logger.debug("fee page scrape(%s) failed: %s", code, exc)
        return code, []
    return code, rows


def _run(
    db_path: Path,
    codes: list[str],
    indicators: list[str],
    concurrency: int,
    progress_every: int,
) -> Stats:
    provider = fund_data.AkshareProvider()
    store = fund_data.FundDataStore(db_path)
    stats = Stats()
    start = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {
            ex.submit(_scrape_one, code, provider, list(indicators)): code
            for code in codes
        }
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            code, rows = fut.result()
            stats.funds_attempted += 1
            if not rows:
                stats.failed += 1
                stats.failed_codes.append(code)
            else:
                try:
                    store.upsert_fee_structures(code, rows)
                    stats.rows_upserted += len(rows)
                    stats.funds_with_rows += 1
                except sqlite3.OperationalError as exc:
                    logger.warning("upsert %s failed: %s", code, exc)
                    stats.failed += 1
                    stats.failed_codes.append(code)
            if i % progress_every == 0 or i == len(codes):
                elapsed = time.monotonic() - start
                rate = i / elapsed if elapsed else 0
                eta = (len(codes) - i) / rate if rate else 0
                logger.info(
                    "[%d/%d] rows=%d fails=%d rate=%.2f fund/s eta=%.0fs",
                    i,
                    len(codes),
                    stats.rows_upserted,
                    stats.failed,
                    rate,
                    eta,
                )

    stats.elapsed = time.monotonic() - start
    return stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of funds (smoke test); default is all missing",
    )
    parser.add_argument(
        "--concurrency", type=int, default=8, help="Thread-pool size (default 8)"
    )
    parser.add_argument(
        "--indicators",
        nargs="+",
        default=list(DEFAULT_INDICATORS),
        help="Indicator names to scrape (default: 申购费率 赎回费率 运作费用)",
    )
    parser.add_argument(
        "--progress-every", type=int, default=200, help="Log every N funds"
    )
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(name)s: %(message)s",
    )
    db_path = Path(args.db)
    if not db_path.is_file():
        raise FileNotFoundError(db_path)

    codes = _select_missing_fee_funds(db_path, args.limit)
    logger.info(
        "targeting %d funds x %d indicators (concurrency=%d, db=%s)",
        len(codes),
        len(args.indicators),
        args.concurrency,
        db_path,
    )
    if not codes:
        logger.info("nothing to do — every fund already has fee_structures rows")
        return 0

    stats = _run(db_path, codes, args.indicators, args.concurrency, args.progress_every)
    logger.info(
        "DONE: attempted=%d with_rows=%d rows=%d failed=%d elapsed=%.1fs",
        stats.funds_attempted,
        stats.funds_with_rows,
        stats.rows_upserted,
        stats.failed,
        stats.elapsed,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
