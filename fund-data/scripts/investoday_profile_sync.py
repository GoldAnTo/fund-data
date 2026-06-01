"""Bulk-import ``fund_profiles`` rows from the Investoday provider.

The Investoday (今日投资) data API exposes a ``/fund/all`` endpoint
that returns a 31-field record for every fund in the universe, with
all the profile-class fields (fundNameFull, managementCompanyName,
custodianName, establishDate, benchmarkCode, investmentObjective,
investmentStrategy, riskReturnProfile, ...). The L1 free tier is
enough to call it.

This script walks the local ``funds`` table, asks the provider for
the Investoday record of each code, and upserts a normalized
``fund_profiles`` row via :func:`fund_data.FundDataStore.upsert_profile`.

It is safe to re-run (idempotent: ``upsert_profile`` is an INSERT
OR REPLACE) and safe to run alongside the main backfill (it only
writes to ``fund_profiles``, which the backfill does not touch).

Typical use::

    # Default: 27k funds, ~40s
    INVESTODAY_API_KEY=... python3 scripts/investoday_profile_sync.py

    # Smoke test
    INVESTODAY_API_KEY=... python3 scripts/investoday_profile_sync.py --limit 100

    # Skip already-populated rows
    INVESTODAY_API_KEY=... python3 scripts/investoday_profile_sync.py --skip-existing
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

logger = logging.getLogger("fund_data.investoday_profile_sync")
DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
DEFAULT_BATCH_COMMIT = 500


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of profiles to write (default: all).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip funds that already have a profile row.",
    )
    parser.add_argument(
        "--batch-commit",
        type=int,
        default=DEFAULT_BATCH_COMMIT,
        help=(
            "Print progress every N upserts and yield the GIL briefly so the main "
            f"backfill can grab the SQLite write lock. Default: {DEFAULT_BATCH_COMMIT}."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args(argv)


def _select_targets(db_path: Path, skip_existing: bool, limit: int | None) -> list[str]:
    where = ""
    params: list[str] = []
    if skip_existing:
        where = " LEFT JOIN fund_profiles p ON p.fund_code = f.fund_code WHERE p.fund_code IS NULL"
    limit_clause = f" LIMIT {int(limit)}" if limit else ""
    sql = f"SELECT f.fund_code FROM funds f{where} ORDER BY f.fund_code{limit_clause}"
    with sqlite3.connect(db_path, timeout=30) as conn:
        return [r[0] for r in conn.execute(sql, params)]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db_path = Path(args.db)

    targets = _select_targets(db_path, args.skip_existing, args.limit)
    if not targets:
        logger.info("nothing to do — every fund already has a profile (or --limit hit zero)")
        return 0
    logger.info("targeting %d funds", len(targets))

    provider = fund_data.InvestodayProvider()
    # Warm the catalog so ``profile()`` lookups are pure in-memory.
    catalog = provider._get_catalog()  # noqa: SLF001 — internal but stable for this version
    logger.info("loaded Investoday catalog: %d rows", len(catalog))
    catalog_codes = {row["fund_code"] for row in catalog}
    targets = [c for c in targets if c in catalog_codes]
    logger.info("  %d targets are in the Investoday catalog", len(targets))

    store = fund_data.FundDataStore(db_path)
    ok = fail_locked = fail_provider = 0
    t0 = time.time()
    for i, code in enumerate(targets, 1):
        try:
            profile = provider.profile(code)
            store.upsert_profile(profile)
            ok += 1
        except fund_data.ProviderError:
            fail_provider += 1
        except sqlite3.OperationalError:
            fail_locked += 1
        if i % args.batch_commit == 0:
            elapsed = time.time() - t0
            eta = elapsed * (len(targets) - i) / i if i else 0
            logger.info(
                "[%d/%d] ok=%d fail_provider=%d fail_locked=%d elapsed=%.0fs eta=%.0fs",
                i,
                len(targets),
                ok,
                fail_provider,
                fail_locked,
                elapsed,
                eta,
            )
    elapsed = time.time() - t0
    logger.info(
        "DONE: ok=%d fail_provider=%d fail_locked=%d elapsed=%.1fs",
        ok,
        fail_provider,
        fail_locked,
        elapsed,
    )

    with sqlite3.connect(db_path, timeout=30) as conn:
        total_profiles = conn.execute(
            "SELECT COUNT(DISTINCT fund_code) FROM fund_profiles"
        ).fetchone()[0]
        total_funds = conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0]
    logger.info(
        "fund_profiles: %d / %d funds (%.2f%%)",
        total_profiles,
        total_funds,
        100.0 * total_profiles / total_funds if total_funds else 0,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
