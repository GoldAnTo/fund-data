"""Render a coverage report for the local fund data base.

Two report modes:

* **Coverage (default)** — per-dataset coverage % over the fund
  universe, plus a sample of the most-incomplete funds. Driven by
  :func:`fund_data.coverage_report`.
* **Stale (``--stale``)** — funds whose newest snapshot / NAV is
  older than ``--max-age-hours``. Useful for "what did the nightly
  backfill skip / not refresh yet" reviews.

Output formats:

* ``--format markdown`` (default) — a single Markdown table that
  drops straight into a PR description or a chat message.
* ``--format json`` — the raw list of rows, for downstream tooling
  and agents.
* ``--format table`` — a fixed-width table for terminal display.

Typical use::

    # Default markdown report
    python3 scripts/coverage_report.py

    # Stale funds (default = 24h)
    python3 scripts/coverage_report.py --stale

    # JSON for an agent
    python3 scripts/coverage_report.py --format json --only-incomplete --limit 50
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Apply the macOS proxy / IPv4 / sqlite-timeout patches BEFORE
# importing :mod:`fund_data` (or any module that might open a
# connection).  Idempotent so a re-import is cheap.
from _net_compat import apply as _apply_net_compat  # noqa: E402

_apply_net_compat()

import fund_data  # noqa: E402

# Pick up `INVESTODAY_API_KEY` / `TUSHARE_TOKEN` from the
# project-root .env (see ``fund_data._env``). The coverage
# report itself does not call paid providers, but downstream
# agents reading the JSON do — having the keys loaded
# consistently keeps the agent contract honest.
from fund_data._env import load_env  # noqa: E402

load_env()

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
DEFAULT_MAX_AGE_HOURS = 24.0
DEFAULT_STALE_LIMIT = 200

# Columns used by the markdown / table renderer. Keep the order
# stable — downstream agents parse the JSON, but humans read the
# markdown and the order matters for readability.
COVERAGE_DATASETS = [
    "profile",
    "nav",
    "stock_holdings",
    "bond_holdings",
    "industry",
    "fees",
    "dividends",
    "splits",
]

# fund_type (prefix matched) -> datasets that are *expected to be
# empty* by regulatory / structural design, not a backfill gap.
#
# Without this matrix, the global 49% "missing stock_holdings"
# figure is inflated by every 货币型 / 债券型 / 指数型-固收 /
# FOF / REITs / QDII fund, and an agent that alarms on
# `completeness < 0.5` will spam false positives. The matrix
# lets coverage_report separate *actionable missing* (real
# backfill work) from *structural empty* (the dataset is
# definitionally absent for this fund_type).
#
# Sourced from docs/fund-data-inventory.md §9.2 (per-fund_type
# coverage audit, 2026-06-02). Update both files together when a
# new fund_type ships or the regulatory regime changes.
EXPECTED_EMPTY: dict[str, frozenset[str]] = {
    # 货币型 — pure cash equivalent, no equity
    "货币型": frozenset({"stock_holdings", "industry"}),
    # 债券型 — pure fixed income, no equity
    "债券型": frozenset({"stock_holdings", "industry"}),
    # 指数型-固收 subtype — bond index
    "指数型-固收": frozenset({"stock_holdings", "industry"}),
    # 指数型 (the broader bucket — most are 固收 but stock subtypes
    # would still expect zero equity holdings of their own)
    "指数型": frozenset({"stock_holdings", "industry"}),
    # FOF — fund-of-fund, holds other funds; the fund-data schema
    # does not store underlying-fund breakdowns, so direct holdings
    # are structurally absent even when the FOF itself is well-covered.
    "FOF": frozenset({"stock_holdings", "bond_holdings", "industry"}),
    # REITs — different regulatory regime, no public disclosure
    "REITs": frozenset({"stock_holdings", "bond_holdings", "industry"}),
    # QDII — overseas-listed; equity industry allocation rarely
    # disclosed in CN schemas, so we tag industry as expected-empty
    # but stock_holdings can still surface (7% per inventory).
    "QDII": frozenset({"industry"}),
}


def _is_structural_empty(fund_type: str | None, dataset: str) -> bool:
    """Return True if this (fund_type, dataset) is *expected* to be empty
    by structural design rather than a backfill gap.

    The matrix is prefix-matched so ``指数型-固收`` is handled by
    the more specific key before the generic ``指数型`` key.

    An unknown / blank fund_type is treated as "not expected
    empty" — the conservative choice is to surface the gap
    rather than hide it.
    """
    if not fund_type:
        return False
    for prefix, datasets in EXPECTED_EMPTY.items():
        if fund_type.startswith(prefix):
            return dataset in datasets
    return False


def _classify_missing(
    fund_type: str | None, missing: list[str]
) -> tuple[list[str], list[str]]:
    """Split a fund's ``missing`` list into (actionable, structural).

    The split lets an agent branch on the actionable list (real
    backfill work) and the human reader see the structural list
    (so a 货币型 with "stock_holdings: structural" stops looking
    like a regression).
    """
    actionable: list[str] = []
    structural: list[str] = []
    for dataset in missing:
        if _is_structural_empty(fund_type, dataset):
            structural.append(dataset)
        else:
            actionable.append(dataset)
    return actionable, structural


def _adjusted_denominator(fund_type: str | None) -> int:
    """Number of datasets the completeness fraction is measured against,
    after removing structural-empty slots for this fund_type.

    A 货币型 fund has 8 raw datasets but only 6 of them are
    measurable (stock_holdings + industries are expected empty).
    Reporting ``present / 8`` would put 货币型 at 75% even when
    every meaningful dataset is present, hiding the actual gap.
    Reporting ``present / 6`` matches what an operator expects
    to see for that fund_type.
    """
    structural_count = sum(
        1 for d in COVERAGE_DATASETS if _is_structural_empty(fund_type, d)
    )
    return len(COVERAGE_DATASETS) - structural_count


def utc_now() -> datetime:
    return datetime.now(UTC)


def _dataset_value(row: dict[str, object], dataset: str) -> int:
    """Translate a coverage-report `missing` list (and friend rows) into
    a 1/0 column for the renderer."""
    if dataset == "profile":
        return int(bool(row.get("has_profile")))
    return int(row.get(f"{dataset}_rows", 0) or 0) > 0 or 0


# --- Coverage mode --------------------------------------------------------


def _coverage_rows(
    db_path: Path,
    *,
    only_incomplete: bool,
    fund_type: str | None,
    limit: int | None,
) -> list[dict[str, object]]:
    """Fetch raw per-fund coverage from fund_data.coverage_report,
    then enrich each row with the actionable / structural split
    and an adjusted completeness score.

    The raw API does not know about the fund_type × dataset
    matrix — that knowledge lives in this module so a downstream
    consumer (CLI, agent, inventory doc) gets the same
    classification without re-deriving it. We also keep the raw
    `completeness` and `missing` fields so existing consumers
    that branched on them keep working.
    """
    rows = fund_data.coverage_report(
        db_path=db_path,
        fund_type=fund_type,
        only_incomplete=only_incomplete,
        limit=limit,
    )
    enriched: list[dict[str, object]] = []
    for r in rows:
        raw_missing = list(r.get("missing") or [])
        actionable, structural = _classify_missing(r.get("fund_type"), raw_missing)
        denom = _adjusted_denominator(r.get("fund_type"))
        if denom <= 0:
            # Defensive: a fund_type whose every dataset is
            # expected-empty is a misconfiguration of the matrix.
            # Report adjusted_completeness = 1.0 so the row
            # doesn't trigger an "always 0%" alarm.
            adjusted = 1.0
        else:
            adjusted = round((denom - len(actionable)) / denom, 4)
        r["missing"] = actionable  # canonical missing = actionable only
        r["actionable_missing"] = actionable
        r["structural_empty"] = structural
        r["adjusted_completeness"] = adjusted
        enriched.append(r)
    return enriched


def _format_coverage_markdown(db_path: Path, rows: list[dict[str, object]]) -> str:
    total_funds = _safe_count(db_path, "funds")
    lines: list[str] = []
    lines.append("# fund-data coverage report")
    lines.append("")
    lines.append(
        f"DB: `{db_path}`  •  funds: **{total_funds}**  •  "
        f"reported: **{len(rows)}**  •  generated: `{utc_now().isoformat()}`"
    )
    lines.append("")
    if not rows:
        lines.append("_No rows match the filter._")
        return "\n".join(lines) + "\n"

    # Per-dataset coverage with the structural-empty split. The
    # actionable count is what an operator should react to;
    # the structural column is "for your information" so a
    # reader does not mistake 货币型 stock_holdings = 0 for a
    # regression. Both columns are shown for the same row so
    # the total reconstructs to ``n``.
    actionable_agg: dict[str, int] = dict.fromkeys(COVERAGE_DATASETS, 0)
    structural_agg: dict[str, int] = dict.fromkeys(COVERAGE_DATASETS, 0)
    for r in rows:
        actionable = set(r.get("actionable_missing") or [])
        structural = set(r.get("structural_empty") or [])
        for d in COVERAGE_DATASETS:
            if d not in actionable and d not in structural:
                actionable_agg[d] += 1
            elif d in structural:
                structural_agg[d] += 1
    n = len(rows)
    lines.append("## Per-dataset coverage (over the reported rows)")
    lines.append("")
    lines.append(
        "| Dataset | Present | Actionable missing | Structural empty |"
    )
    lines.append("|---|---:|---:|---:|")
    for d in COVERAGE_DATASETS:
        present = actionable_agg[d]
        actionable_missing = n - present - structural_agg[d]
        lines.append(
            f"| {d} | {present} / {n} | {actionable_missing} | {structural_agg[d]} |"
        )
    lines.append("")

    # Most-incomplete sample, ranked by adjusted completeness so
    # structural-empty noise does not push 货币型 / 债券型 to the
    # top of the "look at me" list.
    sample = sorted(rows, key=lambda r: r.get("adjusted_completeness", 1.0))[:10]
    if sample:
        lines.append("## Most-incomplete sample (up to 10, ranked by adjusted completeness)")
        lines.append("")
        lines.append(
            "| fund_code | fund_name | fund_type | completeness | adjusted | missing (actionable) | structural |"
        )
        lines.append("|---|---|---|---:|---:|---|---|")
        for r in sample:
            lines.append(
                "| {code} | {name} | {ftype} | {comp:.0%} | {adj:.0%} | {miss} | {struc} |".format(
                    code=r.get("fund_code", ""),
                    name=_short(r.get("fund_name", "")),
                    ftype=_short(r.get("fund_type", "")),
                    comp=float(r.get("completeness", 0.0)),
                    adj=float(r.get("adjusted_completeness", 0.0)),
                    miss=", ".join(sorted(r.get("actionable_missing") or [])) or "—",
                    struc=", ".join(sorted(r.get("structural_empty") or [])) or "—",
                )
            )
        lines.append("")

    # The matrix itself, so the reader can verify the structural
    # numbers above against the rule that produced them.
    lines.append("## Structural-empty matrix (fund_type × dataset)")
    lines.append("")
    lines.append(
        "Datasets flagged as *expected empty* here do not count against "
        "the adjusted completeness score. Source: "
        "`docs/fund-data-inventory.md` §9.2."
    )
    lines.append("")
    lines.append("| fund_type | structural-empty datasets |")
    lines.append("|---|---|")
    for prefix, datasets in EXPECTED_EMPTY.items():
        lines.append(f"| {prefix} | {', '.join(sorted(datasets))} |")
    lines.append("")
    return "\n".join(lines)


def _format_coverage_json(rows: list[dict[str, object]]) -> str:
    return json.dumps(
        {
            "generated_at": utc_now().isoformat(),
            "count": len(rows),
            "rows": rows,
        },
        ensure_ascii=False,
        indent=2,
    )


def _format_coverage_table(rows: list[dict[str, object]]) -> str:
    header = (
        f"{'fund_code':<10}  {'fund_name':<24}  {'type':<10}  "
        f"{'raw':>4}  {'adj':>4}  missing (+ structural)"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows[:200]:
        raw_missing = ", ".join(sorted(r.get("actionable_missing") or [])) or "—"
        structural = r.get("structural_empty") or []
        structural_suffix = f" [+ {','.join(structural)}]" if structural else ""
        lines.append(
            "{code:<10}  {name:<24.24}  {ftype:<10.10}  {raw:>3.0%}  {adj:>3.0%}  {miss}{extra}".format(
                code=str(r.get("fund_code", "")),
                name=_short(r.get("fund_name", ""), limit=24),
                ftype=_short(r.get("fund_type", ""), limit=10),
                raw=float(r.get("completeness", 0.0)),
                adj=float(r.get("adjusted_completeness", 0.0)),
                miss=raw_missing,
                extra=structural_suffix,
            )
        )
    if len(rows) > 200:
        lines.append(f"... and {len(rows) - 200} more (use --format json for the full list)")
    return "\n".join(lines) + "\n"


# --- Stale mode -----------------------------------------------------------


def _stale_rows(
    db_path: Path,
    *,
    max_age_hours: float,
    limit: int,
) -> list[dict[str, object]]:
    """Find funds whose newest snapshot/NAV is older than max_age_hours,
    or that have no snapshot/NAV at all."""
    if not db_path.is_file():
        return []
    cutoff = (utc_now() - timedelta(hours=max_age_hours)).isoformat()
    sql = """
        select
            f.fund_code,
            f.fund_name,
            f.fund_type,
            (select max(fetched_at) from snapshots s where s.fund_code = f.fund_code) as last_snapshot,
            (select max(fetched_at) from nav_history n where n.fund_code = f.fund_code) as last_nav
        from funds f
        group by f.fund_code
        having (last_snapshot is null or last_snapshot < ?)
            or (last_nav is null or last_nav < ?)
        order by coalesce(last_snapshot, last_nav, '0000-01-01') asc
        limit ?
    """
    with sqlite3.connect(db_path) as conn:
        try:
            rows = conn.execute(sql, (cutoff, cutoff, int(limit))).fetchall()
        except sqlite3.OperationalError:
            return []  # tables not yet created
    return [
        {
            "fund_code": r[0],
            "fund_name": r[1] or "",
            "fund_type": r[2] or "",
            "last_snapshot": r[3],
            "last_nav": r[4],
        }
        for r in rows
    ]


def _format_stale_markdown(max_age_hours: float, rows: list[dict[str, object]]) -> str:
    lines: list[str] = []
    lines.append(f"# fund-data stale rows (> {max_age_hours:g}h without refresh)")
    lines.append("")
    lines.append(f"Generated: `{utc_now().isoformat()}`  •  matches: **{len(rows)}**")
    lines.append("")
    if not rows:
        lines.append("_Nothing is stale — every fund has a recent snapshot/NAV._")
        return "\n".join(lines) + "\n"
    lines.append("| fund_code | fund_name | fund_type | last_snapshot | last_nav |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        lines.append(
            "| {code} | {name} | {ftype} | {snap} | {nav} |".format(
                code=r["fund_code"],
                name=_short(r["fund_name"]),
                ftype=_short(r["fund_type"]),
                snap=r["last_snapshot"] or "—",
                nav=r["last_nav"] or "—",
            )
        )
    return "\n".join(lines) + "\n"


def _format_stale_json(rows: list[dict[str, object]]) -> str:
    return json.dumps(
        {"generated_at": utc_now().isoformat(), "count": len(rows), "rows": rows},
        ensure_ascii=False,
        indent=2,
    )


# --- Shared helpers -------------------------------------------------------


def _safe_count(db_path: Path, table: str) -> int:
    if not db_path.is_file():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _short(value: object, limit: int = 20) -> str:
    s = str(value) if value is not None else ""
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "table"),
        default="markdown",
        help="Output format. Default: markdown.",
    )
    parser.add_argument(
        "--only-incomplete",
        action="store_true",
        help="Skip funds that already have every dataset present.",
    )
    parser.add_argument(
        "--fund-type",
        default=None,
        help="Filter by fund_type substring (e.g. '股票型' or '货币').",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap the number of per-fund rows in the report.",
    )
    parser.add_argument(
        "--stale",
        action="store_true",
        help="Show stale rows (funds with no snapshot/NAV refresh in the last N hours).",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help=f"Stale threshold in hours (default: {DEFAULT_MAX_AGE_HOURS:g}). Used with --stale.",
    )
    parser.add_argument(
        "--stale-limit",
        type=int,
        default=DEFAULT_STALE_LIMIT,
        help=f"Cap the number of stale rows (default: {DEFAULT_STALE_LIMIT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    db_path = Path(args.db)

    if args.stale:
        rows = _stale_rows(
            db_path,
            max_age_hours=args.max_age_hours,
            limit=args.stale_limit,
        )
        if args.format == "json":
            print(_format_stale_json(rows))
        else:
            print(_format_stale_markdown(args.max_age_hours, rows))
        return 0

    rows = _coverage_rows(
        db_path,
        only_incomplete=args.only_incomplete,
        fund_type=args.fund_type,
        limit=args.limit,
    )
    if args.format == "json":
        print(_format_coverage_json(rows))
    elif args.format == "table":
        print(_format_coverage_table(rows))
    else:
        print(_format_coverage_markdown(db_path, rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
