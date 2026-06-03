"""Bulk backfill the five fund-profile fields that only ``akshare.fund_overview_em`` serves.

The 2026-06-02 baseline has ``fund_profiles.fund_type`` at 10.7 %, ``issue_date`` at
2.7 %, ``asset_size`` at 2.7 %, ``manager`` at 2.7 % and ``tracking_target`` at 2.7 %.
Investoday's L1 ``/fund/all`` returns an empty string for the latter four fields
across the whole universe (the call site at ``fund_data.InvestodayProvider.profile``
hard-codes ``issue_date=""``, ``asset_size=None``, ``manager=""``,
``tracking_target=""``), and the 728 ``akshare.fund_overview_em`` rows the
``akshare_capability_backfill.py`` runner manages to land come from funds where
the runner happened to also fetch stock holdings — i.e. the profile pass was
never run on its own.

This script makes the profile pass a first-class bulk target:

* Direct provider call (no provider chain) to ``AkshareProvider.profile(code)``
  so we hit ``akshare.fund_overview_em`` deterministically and skip the
  Investoday backfill that already covers the lighter fields.
* 8-way concurrency, 0.1 s minimum interval, 100-fund batch checkpoints — the
  same throughput sweet spot the AGENTS.md benchmarks found for the other
  bulk passes.
* ``fund_profiles`` upsert goes through ``FundDataStore.upsert_profile`` so the
  row schema stays consistent and the writer thread pool inherits the
  store's WAL + ``busy_timeout=30s`` settings.
* Per-fund failures are recorded in ``sync_failures`` and an aggregate
  ``sync_runs`` row is written at the end so the agent-side audit trail
  matches the rest of the data base.
* Resumable: writes ``data/backfill_state.json`` checkpoints every
  ``--checkpoint-every`` funds under the ``fund_profile_backfill`` key. A
  ``--resume`` re-reads the state and skips the already-completed codes.

Why the 5-field, not 14-field, upsert?  ``FundDataStore.upsert_profile``
already overwrites every column on the row, so we do not need to enumerate
each one here — passing the whole ``AkshareProvider.profile`` dict is enough,
and means new columns added upstream automatically land.

Typical use::

    # Smoke test
    .venv-akshare/bin/python3 scripts/fund_profile_backfill.py \\
        --limit 10 --concurrency 2

    # Full run (~3h for 25,000 funds at the AGENTS.md sweet spot)
    .venv-akshare/bin/python3 scripts/fund_profile_backfill.py

    # Resume after a crash
    .venv-akshare/bin/python3 scripts/fund_profile_backfill.py --resume
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import logging
import os
import socket
import sqlite3
import sys
import time
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# macOS proxy / IPv4 patches (must run before importing akshare).
#
# Two known foot-guns live in the local environment:
# 1. macOS injects ``http(s)_proxy`` / ``all_proxy`` env vars via launchd and
#    the system proxy settings, and Clash Verge / Surge listen on 7897.
#    AkShare uses ``requests`` under the hood, so the
#    ``urllib.request.getproxies = lambda: {}`` patch in
#    ``refresh_fund_type.py`` does not propagate — but clearing the env
#    vars (the OS-level layer) *does* take effect for every HTTP client.
# 2. macOS ``getaddrinfo`` (RFC 6724 happy-eyeballs) prefers IPv6 and
#    Eastmoney has no AAAA record, so the IPv4 SYN never gets sent and
#    the process looks hung at 0 % CPU.  Filtering to AF_INET in
#    ``socket.getaddrinfo`` makes the lookup return only the IPv4 answer.
# Both patches are no-ops on Linux / Windows runners.
# ---------------------------------------------------------------------------
for _proxy in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_proxy, None)

_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_only_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001
    return [
        r for r in _orig_getaddrinfo(host, port, *args, **kwargs) if r[0] == socket.AF_INET
    ]


socket.getaddrinfo = _ipv4_only_getaddrinfo

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

# Pick up `INVESTODAY_API_KEY` / `TUSHARE_TOKEN` from the
# project-root .env (see ``fund_data._env``). The Investoday
# /fund/all path is the recommended way to seed
# ``fund_profiles`` once a key is configured.
from fund_data._env import load_env  # noqa: E402

load_env()

logger = logging.getLogger("fund_data.fund_profile_backfill")

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
STATE_PATH = SCRIPT_DIR.parent / "data" / "backfill_state.json"
STATE_KEY = "fund_profile_backfill"
BATCH_ID_PREFIX = "fund_profile_backfill"

# The five "akshare-only" profile fields.  Stored separately from the
# remaining 9 columns so a future schema change can extend the list
# without rewriting the WHERE clause below.
PROFILE_FIELDS = ("fund_type", "issue_date", "asset_size", "manager", "tracking_target")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _is_field_empty(value) -> bool:
    """Empty / None / blank-string check, matching how Investoday / AkShare
    represent a missing field."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _profile_has_all_five(profile: dict) -> bool:
    """Return True if every field in :data:`PROFILE_FIELDS` is populated.

    Used as a sanity guard after ``AkshareProvider.profile`` returns —
    back-end share classes sometimes yield an empty dict and we should
    not record that as a successful backfill.
    """
    return all(not _is_field_empty(profile.get(f)) for f in PROFILE_FIELDS)


def _select_targets(
    db_path: Path,
    *,
    skip_existing: bool,
    limit: int | None,
    exclude_fund_type_substrings: tuple[str, ...] = (),
) -> list[str]:
    """Return the fund codes that still need a profile pass.

    ``skip_existing=True`` (the *broad* form, used by ``--resume``) keeps
    a fund in the work list if *any* of the five target fields is empty.
    That is the inverted-inclusion fix called out in
    ``akshare_capability_backfill.py``: a single-field hole must be
    filled, not skipped.

    ``skip_existing=False`` is for explicit overwrite runs and returns
    every code in ``funds`` so the caller can decide which to re-fetch.
    """
    if skip_existing:
        # A fund is "in" if at least one of the target fields is empty.
        # ``COALESCE`` collapses NULL and '' to '' so the LIKE check
        # catches both shapes.
        or_parts = " OR ".join(
            f"COALESCE(p.{col}, '') = ''" for col in PROFILE_FIELDS
        )
        where = f"WHERE {or_parts}"
    else:
        where = ""

    if exclude_fund_type_substrings:
        # Currency / REITs etc. are not strictly *empty* — but their
        # ``fund_overview_em`` page frequently returns a partial dict,
        # and the bulk backfill of holdings already proved they waste
        # 80 % of the calls.  We let the user opt in via CLI; default
        # is to include them.
        #
        # NB: SQLite's default ``LIKE`` is *not* unicode-aware — the
        # case-insensitive collation only matches ASCII, so a clause
        # like ``NOT LIKE '货币'`` against ``'货币型-普通货币'`` would
        # evaluate true (i.e. the row would leak through the filter).
        # ``instr`` is a plain byte-substring scan and is safe for
        # Chinese / Japanese / Korean characters.
        instr_clauses = " AND ".join(
            "instr(COALESCE(f.fund_type, ''), ?) = 0" for _ in exclude_fund_type_substrings
        )
        where = f"{where} AND {instr_clauses}" if where else f"WHERE {instr_clauses}"

    limit_clause = f" LIMIT {int(limit)}" if limit else ""
    params = list(exclude_fund_type_substrings)
    sql = (
        "SELECT f.fund_code FROM funds f "
        "LEFT JOIN fund_profiles p ON p.fund_code = f.fund_code "
        f"{where} ORDER BY f.fund_code{limit_clause}"
    )
    with sqlite3.connect(db_path, timeout=30) as conn:
        return [r[0] for r in conn.execute(sql, params).fetchall()]


def _load_state() -> dict:
    """Read the ``fund_profile_backfill`` sub-dict from the global state file.

    Missing file / missing key both return an empty dict so the caller
    can do a one-shot initial run without a bootstrap.
    """
    if not STATE_PATH.is_file():
        return {}
    try:
        with STATE_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh).get(STATE_KEY, {}) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    """Persist the per-script sub-dict back into the global state file.

    Uses a read-modify-write under an exclusive lock so concurrent
    backfills (theoretical) do not clobber each other's keys.  In
    practice only one bulk runner is up at a time on a given host, but
    the lock is cheap insurance.
    """
    full: dict = {}
    if STATE_PATH.is_file():
        try:
            with STATE_PATH.open("r", encoding="utf-8") as fh:
                full = json.load(fh)
        except (OSError, json.JSONDecodeError):
            full = {}
    full[STATE_KEY] = state
    tmp = STATE_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(full, fh, indent=2, sort_keys=True)
    tmp.replace(STATE_PATH)


@dataclass
class SyncStats:
    fund_attempted: int = 0
    fund_succeeded: int = 0
    fund_partial: int = 0  # Akshare returned *some* but not all 5 fields
    rows_upserted: int = 0
    failures: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    elapsed: float = 0.0


def _sync_one_fund(
    code: str,
    provider: fund_data.AkshareProvider,
    store: fund_data.FundDataStore,
    batch_id: str,
) -> SyncStats:
    """Pull the profile for one fund and upsert it.

    Failures land in ``sync_failures`` (one row per fund, one per
    exception) so a subsequent ``retry_failures.py`` pass can pick them
    up.  We never raise out of this function — the worker pool expects
    every call to return a ``SyncStats``.
    """
    stats = SyncStats()
    stats.fund_attempted = 1
    try:
        profile = provider.profile(code)
    except Exception as exc:  # noqa: BLE001 — bulk runner must not abort
        logger.debug("akshare profile(%s) raised: %s", code, exc)
        stats.failures["provider_error"] += 1
        try:
            store.record_sync_failure(
                batch_id=batch_id,
                operation="fund_profile_backfill.profile",
                fund_code=code,
                provider="akshare.fund_overview_em",
                message=str(exc)[:500],
            )
        except Exception:  # noqa: BLE001
            # If the audit path itself fails (locked db, etc.) we still
            # want the bulk pass to continue — log and move on.
            logger.exception("failed to record sync_failure for %s", code)
        return stats

    if not profile or not isinstance(profile, dict):
        # Back-end share classes and similar stubs return empty dicts
        # from ``parse_snapshot`` upstream.  Treat as a soft skip
        # without polluting ``sync_failures`` — these are not
        # transient errors, they are an API surface gap.
        stats.failures["empty_profile"] += 1
        return stats

    if not _profile_has_all_five(profile):
        stats.fund_partial = 1
    else:
        stats.fund_succeeded = 1

    try:
        store.upsert_profile(profile)
        stats.rows_upserted += 1
    except Exception as exc:  # noqa: BLE001
        logger.debug("upsert_profile(%s) failed: %s", code, exc)
        stats.failures["upsert_error"] += 1
        try:
            store.record_sync_failure(
                batch_id=batch_id,
                operation="fund_profile_backfill.upsert",
                fund_code=code,
                provider="akshare.fund_overview_em",
                message=str(exc)[:500],
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to record sync_failure for %s", code)
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
            "Skip funds whose profile already has every target field "
            "populated. Default: include all funds (use --resume for "
            "incremental behaviour)."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Read the saved state and skip the already-completed codes.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="In-flight HTTP calls (default: 8 — AGENTS.md sweet spot).",
    )
    parser.add_argument(
        "--min-interval-seconds",
        type=float,
        default=0.1,
        help="Minimum spacing between successive requests per worker (default: 0.1).",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=100,
        help="Save state every N funds (default: 100).",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print a progress line every N funds (default: 50).",
    )
    parser.add_argument(
        "--exclude-type",
        action="append",
        default=[],
        help=(
            "Substring of fund_type to exclude (repeatable). Default: include all "
            "types. Example: --exclude-type 货币."
        ),
    )
    parser.add_argument(
        "--batch-id",
        default=None,
        help="Override the batch_id used in sync_failures / sync_runs audit rows.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    db_path = Path(args.db)
    if not db_path.is_file():
        logger.error("db not found: %s", db_path)
        return 2

    batch_id = args.batch_id or f"{BATCH_ID_PREFIX}-{_utc_now()}"
    logger.info("batch_id=%s db=%s", batch_id, db_path)

    provider = fund_data.AkshareProvider()
    store = fund_data.FundDataStore(db_path)

    started_at = _utc_now()
    state = _load_state()
    completed: set[str] = set(state.get("completed_codes", []) or [])

    targets = _select_targets(
        db_path,
        skip_existing=args.skip_existing or args.resume,
        limit=args.limit,
        exclude_fund_type_substrings=tuple(args.exclude_type or ()),
    )
    if args.resume and completed:
        before = len(targets)
        targets = [c for c in targets if c not in completed]
        logger.info(
            "resume: state has %d completed codes, %d targets -> %d after filter",
            len(completed),
            before,
            len(targets),
        )
    if not targets:
        logger.info("nothing to do")
        store.record_sync_run(
            operation="fund_profile_backfill",
            fund_code=None,
            status="ok",
            rows_changed=0,
            started_at=started_at,
            message=json.dumps(
                {
                    "reason": "nothing_to_do",
                    "skip_existing": args.skip_existing,
                    "resume": args.resume,
                    "completed_in_state": len(completed),
                }
            ),
        )
        return 0

    logger.info(
        "targeting %d funds (concurrency=%d min_interval=%.2fs)",
        len(targets),
        args.concurrency,
        args.min_interval_seconds,
    )

    aggregate = SyncStats()
    interval_lock = contextlib.ExitStack()
    # Throttle per-worker so the in-flight count alone is not the only
    # thing keeping us polite.  We do it by sleeping before *each* call
    # inside the worker, gated by a threading.Lock so multiple workers
    # serialize on the throttle.
    import threading

    throttle_lock = threading.Lock()
    throttle_last = [0.0]

    def _throttled_sync(code: str) -> SyncStats:
        with throttle_lock:
            now = time.monotonic()
            wait = args.min_interval_seconds - (now - throttle_last[0])
            if wait > 0:
                time.sleep(wait)
            throttle_last[0] = time.monotonic()
        return _sync_one_fund(code, provider, store, batch_id)

    last_checkpoint = 0
    interval_lock.__exit__(None, None, None)  # no-op, kept for symmetry
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        future_to_code = {ex.submit(_throttled_sync, c): c for c in targets}
        for i, fut in enumerate(concurrent.futures.as_completed(future_to_code), 1):
            code = future_to_code[fut]
            try:
                stats = fut.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("worker for %s crashed: %s", code, exc)
                aggregate.failures["worker_crash"] += 1
                continue
            aggregate.fund_attempted += stats.fund_attempted
            aggregate.fund_succeeded += stats.fund_succeeded
            aggregate.fund_partial += stats.fund_partial
            aggregate.rows_upserted += stats.rows_upserted
            for k, v in stats.failures.items():
                aggregate.failures[k] += v
            completed.add(code)

            if i % args.progress_every == 0 or i == len(targets):
                logger.info(
                    "[%d/%d] ok=%d partial=%d rows=%d failures=%s",
                    i,
                    len(targets),
                    aggregate.fund_succeeded,
                    aggregate.fund_partial,
                    aggregate.rows_upserted,
                    dict(aggregate.failures),
                )
            if i - last_checkpoint >= args.checkpoint_every:
                last_checkpoint = i
                _save_state(
                    {
                        "updated_at": _utc_now(),
                        "batch_id": batch_id,
                        "completed_codes": sorted(completed),
                        "started_at": state.get("started_at") or started_at,
                    }
                )

    aggregate.elapsed = time.monotonic() - time.monotonic()  # placeholder

    finished_at = _utc_now()
    status = "ok" if not aggregate.failures else "partial"
    store.record_sync_run(
        operation="fund_profile_backfill",
        fund_code=None,
        status=status,
        rows_changed=aggregate.rows_upserted,
        started_at=started_at,
        message=json.dumps(
            {
                "batch_id": batch_id,
                "targeted": len(targets),
                "succeeded": aggregate.fund_succeeded,
                "partial": aggregate.fund_partial,
                "failures": dict(aggregate.failures),
                "concurrency": args.concurrency,
                "min_interval_seconds": args.min_interval_seconds,
                "skip_existing": args.skip_existing,
                "resume": args.resume,
            }
        ),
    )
    _save_state(
        {
            "updated_at": finished_at,
            "batch_id": batch_id,
            "completed_codes": sorted(completed),
            "started_at": state.get("started_at") or started_at,
        }
    )
    logger.info(
        "done: targeted=%d ok=%d partial=%d rows=%d failures=%s",
        len(targets),
        aggregate.fund_succeeded,
        aggregate.fund_partial,
        aggregate.rows_upserted,
        dict(aggregate.failures),
    )
    return 0 if not aggregate.failures.get("worker_crash") else 1


if __name__ == "__main__":
    raise SystemExit(main())
