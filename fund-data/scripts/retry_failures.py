"""Rerun batch sync for codes that landed in the ``sync_failures`` queue.

When ``backfill --include-all`` hits a transient provider error
(network blip, rate-limit, etc.) the failed fund code is recorded
in the ``sync_failures`` table together with the error message and
the failing provider. This script pulls those codes back out and
feeds them through :func:`fund_data.batch_sync_funds` so the
operator can clear the queue without redoing the whole 25k-fund
backfill.

Typical use::

    # All failures, default provider chain
    .venv-akshare/bin/python3 scripts/retry_failures.py

    # Only the first 50, pinned to Eastmoney (faster, no AkShare rate limit)
    .venv-akshare/bin/python3 scripts/retry_failures.py --limit 50 --provider eastmoney

    # Just show the queue without retrying
    python3 scripts/retry_failures.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

logger = logging.getLogger("fund_data.retry")

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_failed_codes(db_path: Path) -> list[str]:
    """Read distinct fund_codes from sync_failures, oldest failure first."""
    if not db_path.is_file():
        return []
    with sqlite3.connect(db_path) as conn:
        try:
            # Oldest failure first, then by code for stability when
            # many codes share the same failed_at.
            rows = conn.execute(
                """
                SELECT fund_code
                FROM sync_failures
                WHERE fund_code IS NOT NULL
                GROUP BY fund_code
                ORDER BY MIN(failed_at), fund_code
                """
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # table not yet created
    return [r[0] for r in rows]


def _delete_failures(db_path: Path, codes: list[str]) -> int:
    """Remove the rows we are about to retry so a re-failure writes a
    fresh sync_failures row instead of duplicating history."""
    if not codes or not db_path.is_file():
        return 0
    placeholders = ",".join("?" * len(codes))
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            f"DELETE FROM sync_failures WHERE fund_code IN ({placeholders})",
            codes,
        )
        return cur.rowcount


def retry(
    *,
    db_path: Path,
    provider: str = "auto",
    concurrency: int = 4,
    include_all: bool = True,
    report_year: str | None = None,
    fee_indicators: list[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    codes = _load_failed_codes(db_path)
    if limit is not None:
        codes = codes[:limit]

    summary: dict[str, object] = {
        "started_at": utc_now(),
        "retried": len(codes),
        "ok": 0,
        "failed": 0,
        "dataset_errors": [],
        "codes": codes,
    }

    if not codes:
        logger.info("nothing to retry: sync_failures is empty")
        return summary

    logger.info("retrying %d failed funds via provider=%s concurrency=%d", len(codes), provider, concurrency)
    if dry_run:
        logger.info("dry run: would have retried %d funds", len(codes))
        return summary

    # Clear the rows we are about to retry so the next batch sync
    # writes a fresh failure record on re-failure.
    deleted = _delete_failures(db_path, codes)
    logger.info("cleared %d old sync_failures rows before retry", deleted)

    result = fund_data.batch_sync_funds(
        codes,
        db_path=db_path,
        provider=provider,
        concurrency=concurrency,
        include_all=include_all,
        report_year=report_year,
        fee_indicators=fee_indicators,
    )

    for outcome in result.get("results", []):
        if outcome.get("status") == "ok":
            summary["ok"] = int(summary["ok"]) + 1
        else:
            summary["failed"] = int(summary["failed"]) + 1
            # Re-record into sync_failures so the queue keeps the new error.
            try:
                store = fund_data.FundDataStore(db_path)
                store.record_sync_failure(
                    batch_id=result.get("batch_id", "retry"),
                    operation="retry",
                    fund_code=outcome.get("fund_code", ""),
                    provider=provider,
                    message=outcome.get("message", ""),
                )
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("could not record re-failure for %s: %s", outcome.get("fund_code"), exc)

    summary["finished_at"] = utc_now()
    summary["batch_id"] = result.get("batch_id")
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--provider",
        choices=["auto", "eastmoney", "akshare", "investoday", "tushare"],
        default="auto",
        help="Provider chain to use (default: auto).",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--report-year", help="Year for stock/bond/industry holdings")
    parser.add_argument("--fee-indicator", action="append", help="Fee section to fetch (repeatable)")
    parser.add_argument("--no-include-all", action="store_true", help="Only retry the hard requirements (snapshot+NAV)")
    parser.add_argument("--limit", type=int, help="Cap the number of codes to retry")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be retried, do not run")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    summary = retry(
        db_path=Path(args.db),
        provider=args.provider,
        concurrency=args.concurrency,
        include_all=not args.no_include_all,
        report_year=args.report_year,
        fee_indicators=args.fee_indicator,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
