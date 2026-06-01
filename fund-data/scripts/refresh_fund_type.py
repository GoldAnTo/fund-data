"""Backfill ``funds.fund_type`` from the Eastmoney fundcode_search index.

Why this script exists
----------------------
The bulk :mod:`akshare_capability_backfill` runner never touched
``funds.fund_type`` because :class:`AkshareProvider.fund_list` reads
``akshare.fund_name_em()``, whose ``基金类型`` column is often blank
or a numeric category code (``1111``, ``1211``, ...) on the upstream
AkShare/Eastmoney side. As a result, 22,756 of the 26,936 rows in
``funds`` (84.4 %) have an empty ``fund_type`` and a further 1,489 rows
hold the numeric code strings — a data-quality bug that blocks every
``fund_type``-based filter and grouping downstream.

The fix: the *same* Eastmoney index endpoint
(``https://fund.eastmoney.com/js/fundcode_search.js``) returns
``[code, pinyin, name, fund_type, pinyin_full]`` for every fund, and
its ``fund_type`` column is fully populated. We re-fetch that file,
parse it, and update ``funds.fund_type`` via direct SQL — bypassing
``FundDataStore.upsert_funds`` so we do not clobber the
``company`` / ``manager`` / ``fund_name`` columns that the AkShare
provider populates with non-empty values from its own row source.

The script is idempotent: it overwrites ``fund_type`` for every
fund_code that appears in the fresh index, leaves other columns
untouched, and writes a ``sync_runs`` audit row.

Typical use::

    .venv-akshare/bin/python3 scripts/refresh_fund_type.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

logger = logging.getLogger("fund_data.refresh_fund_type")

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
FUND_CODE_URL = "https://fund.eastmoney.com/js/fundcode_search.js"


def _fetch_index() -> str:
    logger.info("downloading %s", FUND_CODE_URL)
    with urlopen(FUND_CODE_URL, timeout=30) as resp:  # noqa: S310 - trusted source
        return resp.read().decode("utf-8", errors="replace")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _apply(
    db_path: Path,
    rows: list[dict],
    *,
    only_empty: bool,
    dry_run: bool,
) -> dict[str, int]:
    """Update ``funds.fund_type`` for every ``fund_code`` in ``rows``.

    ``only_empty=True`` (the default) skips rows that already have a
    non-numeric ``fund_type`` set, so the refresh only fills the
    empty-string / numeric-code holes. ``only_empty=False`` overwrites
    every row's fund_type with the fresh value.
    """
    updated = 0
    skipped = 0
    missing_in_db = 0
    bad_type_skipped = 0
    with sqlite3.connect(db_path, timeout=30) as conn:
        if not dry_run:
            conn.execute("BEGIN")
        try:
            for row in rows:
                code = row["fund_code"]
                new_type = row.get("fund_type", "")
                if not new_type:
                    bad_type_skipped += 1
                    continue
                cur = conn.execute(
                    "SELECT fund_type FROM funds WHERE fund_code = ?", (code,)
                )
                existing = cur.fetchone()
                if existing is None:
                    missing_in_db += 1
                    continue
                old_type = existing[0] or ""
                if only_empty and old_type and not old_type[:1].isdigit():
                    skipped += 1
                    continue
                if old_type == new_type:
                    skipped += 1
                    continue
                if not dry_run:
                    conn.execute(
                        "UPDATE funds SET fund_type = ?, updated_at = ? "
                        "WHERE fund_code = ?",
                        (new_type, _utc_now(), code),
                    )
                updated += 1
            if not dry_run:
                conn.execute(
                    "INSERT INTO sync_runs(operation, status, rows_changed, "
                    "started_at, finished_at, message) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "refresh_fund_type",
                        "ok",
                        updated,
                        _utc_now(),
                        _utc_now(),
                        json.dumps(
                            {
                                "only_empty": only_empty,
                                "skipped": skipped,
                                "missing_in_db": missing_in_db,
                                "bad_type_skipped": bad_type_skipped,
                                "dry_run": dry_run,
                                "source": FUND_CODE_URL,
                            }
                        ),
                    )
                )
                conn.execute("COMMIT")
        except Exception:
            if not dry_run:
                conn.execute("ROLLBACK")
            raise
    return {
        "updated": updated,
        "skipped": skipped,
        "missing_in_db": missing_in_db,
        "bad_type_skipped": bad_type_skipped,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--only-empty",
        action="store_true",
        default=True,
        help="Skip rows whose fund_type is already a non-numeric string (default)",
    )
    parser.add_argument(
        "--all",
        dest="only_empty",
        action="store_false",
        help="Overwrite every fund_type, including the non-empty values",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the would-be changes without writing",
    )
    parser.add_argument(
        "--raw-cache",
        type=Path,
        default=None,
        help="Use a cached fundcode_search.js instead of downloading",
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

    text = (
        Path(args.raw_cache).read_text(encoding="utf-8")
        if args.raw_cache
        else _fetch_index()
    )
    rows = fund_data.parse_search_results(text)
    logger.info("parsed %d rows from the fundcode_search index", len(rows))

    stats = _apply(db_path, rows, only_empty=args.only_empty, dry_run=args.dry_run)
    logger.info(
        "DONE updated=%d skipped=%d missing_in_db=%d bad_type_skipped=%d dry_run=%s",
        stats["updated"],
        stats["skipped"],
        stats["missing_in_db"],
        stats["bad_type_skipped"],
        args.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
