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

For the small tail (18 funds in the 2026-06-02 baseline) where
Eastmoney itself returns an empty fund_type but the row has a real
``fund_name`` we can read, a regex fallback infers the type from
the name. Both passes are idempotent: each writes a ``sync_runs``
audit row.

Typical use::

    .venv-akshare/bin/python3 scripts/refresh_fund_type.py
"""

from __future__ import annotations

import argparse
import json
import logging
import re
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


# Regex fallback for the 18 funds whose Eastmoney fundcode_search
# row carries an empty fund_type but a real fund_name. Order
# matters: more specific patterns first so ``(FOF)`` and ``(QDII)``
# win over the bare ``混合`` / ``ETF`` matches in the same name.
#
# The canonical fund_type values follow the ``<category>-<sub>``
# pattern (see the 2026-06-02 coverage report). We pick the most
# common subcategory for each keyword so the inferred type is
# usable for ``fund_type``-based filters without a follow-up
# refresh.
_NAME_TYPE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"FOF"), "FOF-稳健型"),
    (re.compile(r"QDII"), "QDII-混合偏股"),
    (re.compile(r"ETF"), "指数型-股票"),
    # 纯债 / 定开债 / 持有期债券 are all "long-only" bond funds;
    # 债券型-长债 is the most common subcategory on Eastmoney.
    (re.compile(r"(?:纯债|定开债|持有期债券)"), "债券型-长债"),
    (re.compile(r"债券"), "债券型-长债"),
    (re.compile(r"混合"), "混合型-灵活"),
    (re.compile(r"货币"), "货币型-普通货币"),
    (re.compile(r"股票"), "股票型"),
]


def infer_fund_type_from_name(name: str) -> str:
    """Best-effort fund_type guess from a Chinese fund name.

    Used as a regex fallback for the 18 funds whose Eastmoney
    fundcode_search.js row carries an empty fund_type (typically
    recently-launched or 发起式 funds that Eastmoney has not
    classified yet). The canonical fund_type values follow the
    ``<category>-<sub>`` pattern (e.g. ``指数型-股票``,
    ``债券型-长债``), and the fund_name carries the same
    vocabulary, so a keyword match produces a usable value
    without any further lookups.

    Returns an empty string if no pattern matches -- callers
    should treat that as "leave the existing fund_type alone".
    """
    if not name:
        return ""
    for pattern, fund_type in _NAME_TYPE_PATTERNS:
        if pattern.search(name):
            return fund_type
    return ""
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


def _apply_name_fallback(
    db_path: Path,
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Run :func:`infer_fund_type_from_name` over every row whose
    ``fund_type`` is still empty after the Eastmoney refresh and
    that has a real ``fund_name`` (not the placeholder
    ``fund_code`` value some back-end share classes carry).

    The 2026-06-02 baseline had 18 funds in this bucket -- the
    Eastmoney ``fundcode_search.js`` row exists but its
    ``fund_type`` slot is blank, and the 190 back-end share
    classes that carry an empty fund_name as well are silently
    skipped (``no_name`` counter).

    Each successful inference is a single ``UPDATE funds`` with
    the same bypass-the-upsert rationale as :func:`_apply`: we
    do not want to clobber the company / manager / fund_name
    columns that AkShare populated. The whole pass is wrapped
    in a single transaction so a mid-loop crash rolls back to
    the pre-pass state.
    """
    updated = 0
    skipped = 0
    no_name = 0
    with sqlite3.connect(db_path, timeout=30) as conn:
        if not dry_run:
            conn.execute("BEGIN")
        try:
            rows = conn.execute(
                "SELECT fund_code, fund_name, fund_type FROM funds "
                "WHERE fund_type = '' OR fund_type IS NULL"
            ).fetchall()
            for fund_code, fund_name, _existing in rows:
                if not fund_name or fund_name == fund_code:
                    # The back-end share classes live here -- their
                    # fund_name is just the fund_code, so the
                    # regex has nothing to work with.
                    no_name += 1
                    continue
                new_type = infer_fund_type_from_name(fund_name)
                if not new_type:
                    skipped += 1
                    continue
                if not dry_run:
                    conn.execute(
                        "UPDATE funds SET fund_type = ?, updated_at = ? "
                        "WHERE fund_code = ?",
                        (new_type, _utc_now(), fund_code),
                    )
                updated += 1
            if not dry_run:
                conn.execute(
                    "INSERT INTO sync_runs(operation, status, rows_changed, "
                    "started_at, finished_at, message) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "refresh_fund_type.name_fallback",
                        "ok",
                        updated,
                        _utc_now(),
                        _utc_now(),
                        json.dumps(
                            {
                                "no_name": no_name,
                                "skipped": skipped,
                                "dry_run": dry_run,
                            }
                        ),
                    )
                )
                conn.execute("COMMIT")
        except Exception:
            if not dry_run:
                conn.execute("ROLLBACK")
            raise
    return {"updated": updated, "skipped": skipped, "no_name": no_name}


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
    parser.add_argument(
        "--no-fallback-name-regex",
        dest="fallback_name_regex",
        action="store_false",
        default=True,
        help="Skip the regex-on-fund_name fallback pass (default: enabled)",
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
    if args.fallback_name_regex:
        fb_stats = _apply_name_fallback(db_path, dry_run=args.dry_run)
        logger.info(
            "FALLBACK updated=%d skipped=%d no_name=%d dry_run=%s",
            fb_stats["updated"],
            fb_stats["skipped"],
            fb_stats["no_name"],
            args.dry_run,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
