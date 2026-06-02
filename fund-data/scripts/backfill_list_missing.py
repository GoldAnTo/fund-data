"""List the funds that are missing from the backfill state, in batch-sync-friendly form.

Why this script exists
----------------------
The :mod:`backfill` runner maintains a JSON state file that snapshots
the ``funds`` table size at startup. Any fund that lands in the
``funds`` table *after* a backfill run starts is invisible to the
state -- the next ``backfill --resume`` will not pick it up until the
state is rebuilt. AGENTS.md (``Long-running pitfalls worth
pre-flighting``) calls this out as the "假 nothing to do" trap: the
operator sees ``completed + failed == total`` and stops, leaving the
newly-listed funds un-synced.

The 2026-06-02 baseline hit this exact failure mode: the state had
``completed=25,767, failed=195`` (sum 25,962) but the live ``funds``
table held 26,953 rows, so 991 funds were silently dropped.

The :mod:`backfill` runner does refresh its in-memory ``all_codes``
list on every invocation (see ``_load_funds`` in ``backfill.py``), so
a vanilla ``backfill --resume`` would in fact catch the 991 funds --
but only if the operator runs the full multi-hour backfill, including
the snapshot+NAV+fees+holdings fetches they already have. This
script is the cheaper diagnostic: read state, list the gap, write
``missing.txt`` for :func:`fund_data.batch_sync_funds` to consume.

Typical use::

    # List every fund in ``funds`` that is not in
    # state.completed_codes and not in state.failed_codes.
    .venv-akshare/bin/python scripts/backfill_list_missing.py

    # Same as above but with the backfill runner's fund_type filters
    # so the result matches what ``backfill.py`` would actually run.
    .venv-akshare/bin/python scripts/backfill_list_missing.py \\
        --exclude-type 货币

    # Pipe the codes into batch_sync_funds.
    .venv-akshare/bin/python scripts/backfill_list_missing.py --output missing.txt
    .venv-akshare/bin/python -c "import fund_data, sys; \\
        fund_data.batch_sync_funds(open('missing.txt').read().split(), \\
        include_all=True, batch_id='resume-2026-06-02')"
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

logger = logging.getLogger("fund_data.backfill_list_missing")

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
DEFAULT_STATE_PATH = SCRIPT_DIR.parent / "data" / "backfill_state.json"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _load_state_codes(state_path: Path) -> tuple[set[str], set[str]]:
    """Read the state file and return (completed, failed) as sets.

    Missing file / malformed JSON / missing keys all collapse to empty
    sets so the script can run against a brand-new data base without
    bootstrapping noise.
    """
    completed: set[str] = set()
    failed: set[str] = set()
    if not state_path.is_file():
        return completed, failed
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return completed, failed
    for key, sink in (("completed_codes", completed), ("failed_codes", failed)):
        for code in state.get(key, []) or []:
            # Codes are stored as 6-digit zero-padded strings; the
            # JSON deserialiser turns them into ``"000001"``, which is
            # what we want.  Defensive cast in case a future writer
            # stores ints.
            sink.add(str(code))
    return completed, failed


def _load_funds(
    db_path: Path,
    *,
    include_types: list[str] | None,
    exclude_types: list[str] | None,
) -> list[tuple[str, str]]:
    """SQL-backed list of ``(fund_code, fund_type)`` with the same
    fund_type filtering as :mod:`backfill`'s ``_load_funds`` helper.

    SQLite's default ``LIKE`` is case-insensitive *and* not
    unicode-aware, so we still avoid it for the substring filter
    (see ``fund_profile_backfill.py`` for the same fix).  We use
    ``instr`` here too.
    """
    where: list[str] = []
    params: list[str] = []
    if include_types:
        for t in include_types:
            where.append("instr(COALESCE(fund_type, ''), ?) > 0")
            params.append(t)
    if exclude_types:
        for t in exclude_types:
            where.append("instr(COALESCE(fund_type, ''), ?) = 0")
            params.append(t)
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""
    with sqlite3.connect(db_path, timeout=30) as conn:
        rows = conn.execute(
            f"SELECT fund_code, fund_type FROM funds{where_clause} ORDER BY fund_code",
            params,
        ).fetchall()
    return [(code, ftype or "") for code, ftype in rows]


def _select_missing(
    db_path: Path,
    state_path: Path,
    *,
    include_types: list[str] | None,
    exclude_types: list[str] | None,
    include_failed: bool,
) -> list[tuple[str, str]]:
    """Return the (fund_code, fund_type) rows that the backfill state
    has not yet recorded as completed.

    If ``include_failed`` is True, funds in ``failed_codes`` are
    *excluded* from the missing list (they have been attempted, the
    caller wants the truly new funds).  The default mirrors
    :mod:`backfill`'s pending calculation, which is the right answer
    for a "what should I queue next" diagnostic.
    """
    all_codes = _load_funds(
        db_path,
        include_types=include_types,
        exclude_types=exclude_types,
    )
    completed, failed = _load_state_codes(state_path)
    skip = set(completed)
    if not include_failed:
        skip |= failed
    return [(c, t) for c, t in all_codes if c not in skip]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--state",
        default=str(DEFAULT_STATE_PATH),
        help="backfill state JSON path",
    )
    parser.add_argument(
        "--include-type",
        action="append",
        help="Substring match for fund_type to include (repeatable)",
    )
    parser.add_argument(
        "--exclude-type",
        action="append",
        help="Substring match for fund_type to skip (repeatable)",
    )
    parser.add_argument(
        "--include-failed",
        action="store_true",
        help=(
            "Include funds currently in state.failed_codes in the "
            "missing list (i.e. do not skip them).  Default: skip "
            "failed -- the caller's use case is usually 'newly listed'."
        ),
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the codes to this file (one per line) in addition to stdout",
    )
    parser.add_argument(
        "--audit",
        action="store_true",
        help=(
            "Write a sync_runs audit row recording the count, the "
            "include/exclude filters and the cutoff timestamp. Useful "
            "for agent / CI consumption."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db_path = Path(args.db)
    state_path = Path(args.state)
    if not db_path.is_file():
        logger.error("db not found: %s", db_path)
        return 2

    missing = _select_missing(
        db_path,
        state_path,
        include_types=args.include_type,
        exclude_types=args.exclude_type,
        include_failed=args.include_failed,
    )
    completed, failed = _load_state_codes(state_path)
    all_codes = _load_funds(
        db_path,
        include_types=args.include_type,
        exclude_types=args.exclude_type,
    )

    logger.info(
        "found %d missing (total=%d completed=%d failed=%d included_failed=%s)",
        len(missing),
        len(all_codes),
        len(completed),
        len(failed),
        args.include_failed,
    )
    # Print the codes one per line so the script composes into a
    # batch_sync_funds pipeline (e.g. ``xargs``).
    for code, _ftype in missing:
        print(code)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            "\n".join(code for code, _ in missing) + ("\n" if missing else ""),
            encoding="utf-8",
        )
        logger.info("wrote %d codes to %s", len(missing), out)

    if args.audit:
        store = fund_data.FundDataStore(db_path)
        store.record_sync_run(
            operation="backfill_list_missing",
            fund_code=None,
            status="ok",
            rows_changed=len(missing),
            started_at=_utc_now(),
            message=json.dumps(
                {
                    "total": len(all_codes),
                    "completed": len(completed),
                    "failed": len(failed),
                    "missing": len(missing),
                    "include_failed": args.include_failed,
                    "include_types": args.include_type or [],
                    "exclude_types": args.exclude_type or [],
                    "output": args.output,
                }
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
