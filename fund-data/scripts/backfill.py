"""End-to-end backfill of the local fund data base.

Builds the local SQLite store from a bare ``funds`` table up to a
complete per-fund base row. Reuses :func:`fund_data.batch_sync_funds`
for the heavy lifting, adding:

- **fund_type filtering** — skip rows that would never have the dataset
  (currency funds have no stock holdings, etc.). This is the biggest
  single time saver.
- **state persistence** — write a JSON state file so an interrupted
  run can resume without redoing the work that already finished.
- **batched progress reports** — print a per-batch summary so the
  operator can watch a long run without tailing the log.
- **final coverage report** — dump a JSON summary of what changed and
  what is still missing.

Typical use:

    .venv-akshare/bin/python scripts/backfill.py \\
        --concurrency 8 --report-year 2024

The defaults match the values recommended in ``README.md`` under
"known gaps": NAV since ``2021-01-01`` (5 years), include all optional
datasets, fund_type filtering on.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

logger = logging.getLogger("fund_data.backfill")

# Fund types where the *hard* snapshot + NAV fetches still apply but the
# optional datasets are guaranteed to be empty. We skip those optional
# dataset flags for these types to cut the API call count by ~80%.
SKIP_OPTIONAL_DATASETS_FOR_TYPES = (
    "货币型",
    "货币",
)

DEFAULT_NAV_YEARS = 5
DEFAULT_REPORT_YEAR = str(datetime.now().year - 1)  # 上一年的持仓数据
DEFAULT_CONCURRENCY = 8
DEFAULT_BATCH_SIZE = 500
DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
DEFAULT_STATE_PATH = SCRIPT_DIR.parent / "data" / "backfill_state.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_funds(
    db_path: Path,
    *,
    include_types: list[str] | None,
    exclude_types: list[str] | None,
    skip_optional_for_currency: bool,
) -> list[tuple[str, str]]:
    """Return ``[(fund_code, fund_type), ...]`` filtered by type rules."""
    with sqlite3.connect(db_path) as conn:
        where: list[str] = []
        params: list[str] = []
        if include_types:
            for t in include_types:
                where.append("fund_type LIKE ?")
                params.append(f"%{t}%")
        if exclude_types:
            for t in exclude_types:
                where.append("fund_type NOT LIKE ?")
                params.append(f"%{t}%")
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            f"SELECT fund_code, fund_type FROM funds{where_clause} ORDER BY fund_code",
            params,
        ).fetchall()
    return [(code, ftype or "") for code, ftype in rows]


def _resolve_include_flags(
    fund_type: str, *, always_include_all: bool, skip_optional_for_currency: bool
) -> dict[str, bool]:
    """Pick which optional datasets to request for a given fund type.

    Currency funds return empty ``stock_holdings``/``bond_holdings``/
    ``industry_allocations``/``fee_structures`` from AkShare anyway, so
    the calls just cost rate-limit budget. Skipping them is a pure win.
    """
    if always_include_all:
        return {flag: True for flag in (
            "include_holdings",
            "include_profile",
            "include_bonds",
            "include_industries",
            "include_fees",
            "include_distributions",
            "include_managers",
        )}
    if skip_optional_for_currency and any(token in fund_type for token in SKIP_OPTIONAL_DATASETS_FOR_TYPES):
        return {
            "include_holdings": False,
            "include_profile": True,
            "include_bonds": False,
            "include_industries": False,
            "include_fees": False,
            "include_distributions": True,
            "include_managers": True,
        }
    return {
        "include_holdings": True,
        "include_profile": True,
        "include_bonds": True,
        "include_industries": True,
        "include_fees": True,
        "include_distributions": True,
        "include_managers": True,
    }


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        return {
            "started_at": _utc_now(),
            "config": {},
            "completed_codes": [],
            "failed_codes": [],
            "last_batch_id": None,
            "totals": {"ok": 0, "failed": 0},
        }
    return json.loads(state_path.read_text(encoding="utf-8"))


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _utc_now()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def backfill(
    *,
    db_path: Path,
    state_path: Path,
    include_types: list[str] | None,
    exclude_types: list[str] | None,
    skip_optional_for_currency: bool,
    start_date: str,
    end_date: str | None,
    report_year: str,
    fee_indicators: list[str] | None,
    concurrency: int,
    batch_size: int,
    max_funds: int | None,
    min_interval_seconds: float | None,
    reset: bool,
) -> dict[str, Any]:
    if reset and state_path.is_file():
        state_path.unlink()
        logger.info("reset state file: %s", state_path)

    state = _load_state(state_path)
    state["config"] = {
        "db_path": str(db_path),
        "start_date": start_date,
        "end_date": end_date,
        "report_year": report_year,
        "concurrency": concurrency,
        "batch_size": batch_size,
        "skip_optional_for_currency": skip_optional_for_currency,
        "fee_indicators": fee_indicators,
    }

    all_codes = _load_funds(
        db_path,
        include_types=include_types,
        exclude_types=exclude_types,
        skip_optional_for_currency=skip_optional_for_currency,
    )
    if max_funds is not None:
        all_codes = all_codes[:max_funds]

    completed = set(state.get("completed_codes", []))
    failed = set(state.get("failed_codes", []))
    pending = [(c, t) for c, t in all_codes if c not in completed and c not in failed]
    if not pending:
        logger.info("nothing to do: %d total, %d completed, %d failed", len(all_codes), len(completed), len(failed))
        return {"total": len(all_codes), "pending": 0, "ok": len(completed), "failed": len(failed), "batches": []}

    logger.info(
        "backfill plan: %d total, %d already done, %d failed previously, %d pending",
        len(all_codes), len(completed), len(failed), len(pending),
    )

    # Group by fund_type to batch funds that share the same include flag set.
    groups: dict[tuple, list[str]] = {}
    for code, ftype in pending:
        flags = tuple(sorted(_resolve_include_flags(
            ftype, always_include_all=False, skip_optional_for_currency=skip_optional_for_currency
        ).items()))
        groups.setdefault(flags, []).append(code)

    batch_reports: list[dict[str, Any]] = []
    run_started = time.monotonic()
    for flags_tuple, codes in groups.items():
        flags = dict(flags_tuple)
        for i in range(0, len(codes), batch_size):
            batch = codes[i:i + batch_size]
            batch_id = f"backfill-{_utc_now()}-{i // batch_size:04d}"
            logger.info(
                "running batch %s: %d funds, flags=%s",
                batch_id, len(batch),
                {k: v for k, v in flags.items() if v},
            )
            batch_started = time.monotonic()
            result = fund_data.batch_sync_funds(
                batch,
                start_date=start_date,
                end_date=end_date,
                page=1,
                per=200,
                db_path=db_path,
                provider="auto",
                fee_indicators=fee_indicators,
                report_year=report_year,
                batch_id=batch_id,
                concurrency=concurrency,
                min_interval_seconds=min_interval_seconds,
                **flags,
            )
            elapsed = time.monotonic() - batch_started
            batch_report = {
                "batch_id": batch_id,
                "size": len(batch),
                "ok": result["ok"],
                "failed": result["failed"],
                "elapsed_seconds": round(elapsed, 1),
                "flags": flags,
            }
            batch_reports.append(batch_report)
            logger.info(
                "batch %s done in %.1fs: ok=%d failed=%d",
                batch_id, elapsed, result["ok"], result["failed"],
            )
            for outcome in result["results"]:
                if outcome.get("status") == "ok":
                    state["completed_codes"].append(outcome["fund_code"])
                else:
                    state["failed_codes"].append(outcome["fund_code"])
            state["totals"]["ok"] = result["ok"]
            state["totals"]["failed"] = result["failed"]
            state["last_batch_id"] = batch_id
            _save_state(state_path, state)

    total_elapsed = time.monotonic() - run_started
    summary = {
        "total": len(all_codes),
        "completed": len(state["completed_codes"]),
        "failed": len(state["failed_codes"]),
        "elapsed_seconds": round(total_elapsed, 1),
        "started_at": state["started_at"],
        "finished_at": _utc_now(),
        "batches": batch_reports,
    }
    summary_path = state_path.with_name("backfill_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "backfill finished: %d ok, %d failed, %.1fs total. summary=%s",
        summary["completed"], summary["failed"], total_elapsed, summary_path,
    )
    return summary


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH), help="State JSON path")
    parser.add_argument("--include-type", action="append", help="Substring match for fund_type to include (repeatable)")
    parser.add_argument("--exclude-type", action="append", help="Substring match for fund_type to skip (repeatable)")
    parser.add_argument(
        "--no-skip-currency",
        action="store_true",
        help="Do not skip optional datasets for currency funds. Slower.",
    )
    parser.add_argument(
        "--nav-years",
        type=int,
        default=DEFAULT_NAV_YEARS,
        help=f"Number of years of NAV history to fetch (default: {DEFAULT_NAV_YEARS})",
    )
    parser.add_argument("--start-date", help="Override the NAV start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="NAV end date (YYYY-MM-DD)")
    parser.add_argument(
        "--report-year",
        default=DEFAULT_REPORT_YEAR,
        help=f"Year for stock/bond/industry holdings (default: {DEFAULT_REPORT_YEAR})",
    )
    parser.add_argument("--fee-indicator", action="append", help="Fee section to fetch (repeatable)")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, help="Parallel fund fetches")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Funds per batch_sync call")
    parser.add_argument("--max-funds", type=int, help="Limit total funds (for testing)")
    parser.add_argument(
        "--min-interval-seconds",
        type=float,
        help="Override the per-call rate-limit interval (default: 0.25 with concurrency, 1.0 serial).",
    )
    parser.add_argument("--reset", action="store_true", help="Discard the saved state and start from scratch")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    start_date = args.start_date or (
        datetime.now(timezone.utc) - timedelta(days=365 * args.nav_years)
    ).strftime("%Y-%m-%d")
    summary = backfill(
        db_path=Path(args.db),
        state_path=Path(args.state),
        include_types=args.include_type,
        exclude_types=args.exclude_type,
        skip_optional_for_currency=not args.no_skip_currency,
        start_date=start_date,
        end_date=args.end_date,
        report_year=args.report_year,
        fee_indicators=args.fee_indicator,
        concurrency=args.concurrency,
        batch_size=args.batch_size,
        max_funds=args.max_funds,
        min_interval_seconds=args.min_interval_seconds,
        reset=args.reset,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
