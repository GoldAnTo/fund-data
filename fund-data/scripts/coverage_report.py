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
    "industries",
    "fees",
    "dividends",
    "splits",
]


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
    return fund_data.coverage_report(
        db_path=db_path,
        fund_type=fund_type,
        only_incomplete=only_incomplete,
        limit=limit,
    )


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

    # Aggregate per-dataset coverage from the per-fund completeness rows.
    aggregates: dict[str, int] = dict.fromkeys(COVERAGE_DATASETS, 0)
    for r in rows:
        missing = set(r.get("missing") or [])
        for d in COVERAGE_DATASETS:
            if d not in missing:
                aggregates[d] += 1
    n = len(rows)
    lines.append("## Per-dataset coverage (over the reported rows)")
    lines.append("")
    lines.append("| Dataset | Present | Coverage |")
    lines.append("|---|---:|---:|")
    for d in COVERAGE_DATASETS:
        present = aggregates[d]
        pct = (100.0 * present / n) if n else 0.0
        lines.append(f"| {d} | {present} / {n} | {pct:.2f} % |")
    lines.append("")

    # Most-incomplete sample (top 10).
    sample = sorted(rows, key=lambda r: r.get("completeness", 1.0))[:10]
    if sample:
        lines.append("## Most-incomplete sample (up to 10)")
        lines.append("")
        lines.append("| fund_code | fund_name | fund_type | completeness | missing |")
        lines.append("|---|---|---|---:|---|")
        for r in sample:
            lines.append(
                "| {code} | {name} | {ftype} | {comp:.0%} | {missing} |".format(
                    code=r.get("fund_code", ""),
                    name=_short(r.get("fund_name", "")),
                    ftype=_short(r.get("fund_type", "")),
                    comp=float(r.get("completeness", 0.0)),
                    missing=", ".join(sorted(r.get("missing") or [])) or "—",
                )
            )
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
    header = f"{'fund_code':<10}  {'fund_name':<28}  {'type':<14}  {'comp':>5}  missing"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows[:200]:
        lines.append(
            "{code:<10}  {name:<28.28}  {ftype:<14.14}  {comp:>4.0%}  {missing}".format(
                code=str(r.get("fund_code", "")),
                name=_short(r.get("fund_name", "")),
                ftype=_short(r.get("fund_type", "")),
                comp=float(r.get("completeness", 0.0)),
                missing=", ".join(sorted(r.get("missing") or [])) or "—",
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
