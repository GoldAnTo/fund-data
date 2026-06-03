"""Read-only fund self-audit queue.

Generates a prioritized remediation queue over the local SQLite base
without ever calling a provider. The queue separates actionable
missing / stale rows from structural-empty / naturally-sparse
patterns so a downstream agent does not turn "货币型 has no stock
holdings" into a P1 false positive.

Priority model (stable, ordered, score-broken inside):

* P1  -- core answer path missing: ``fund_profiles``, ``nav_history``,
          ``snapshots``.
* P2  -- actionable research dataset missing: ``stock_holdings``,
          ``bond_holdings``, ``industry_allocations``, ``fee_structures``.
* P3  -- present but stale: ``fetched_at`` older than ``max_age_hours``
          on a table that carries a ``stale_column``.
* P4  -- structural-empty (the matrix below) or naturally sparse
          (``dividends``, ``splits``).

The structural-empty matrix mirrors the one in
``scripts/coverage_report.py``; the difference is the dataset keys
here are **DB table names** (``industry_allocations``) instead of
the short coverage labels (``industry``), so the queue is
self-describing for any caller. Update the two together when a new
fund_type ships.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .normalizers import normalize_fund_code
from .paths import default_db_path


DATASET_WEIGHTS = {
    "funds": 0,
    "fund_profiles": 10,
    "nav_history": 20,
    "snapshots": 30,
    "stock_holdings": 40,
    "bond_holdings": 50,
    "industry_allocations": 60,
    "fee_structures": 70,
    "dividends": 90,
    "splits": 95,
}


EXPECTED_EMPTY = {
    "货币型": frozenset({"stock_holdings", "industry_allocations"}),
    "债券型": frozenset({"stock_holdings", "industry_allocations"}),
    "指数型-固收": frozenset({"stock_holdings", "industry_allocations"}),
    "指数型": frozenset({"stock_holdings", "industry_allocations"}),
    "FOF": frozenset({"stock_holdings", "bond_holdings", "industry_allocations"}),
    "REITs": frozenset({"stock_holdings", "bond_holdings", "industry_allocations"}),
    "QDII": frozenset({"industry_allocations"}),
}


DATASET_RULES = {
    "fund_profiles": {"table": "fund_profiles", "priority": "P1", "tool": "fund_profile", "batch_flag": "--include-profile"},
    "nav_history": {"table": "nav_history", "priority": "P1", "tool": "fund_nav_history", "batch_flag": "", "stale_column": "fetched_at"},
    "snapshots": {"table": "snapshots", "priority": "P1", "tool": "fund_snapshot", "batch_flag": None, "stale_column": "fetched_at"},
    "stock_holdings": {"table": "stock_holdings", "priority": "P2", "tool": "fund_stock_holdings", "batch_flag": "--include-holdings"},
    "bond_holdings": {"table": "bond_holdings", "priority": "P2", "tool": "fund_bond_holdings", "batch_flag": "--include-bonds"},
    "industry_allocations": {"table": "industry_allocations", "priority": "P2", "tool": "fund_industry_allocations", "batch_flag": "--include-industries"},
    "fee_structures": {"table": "fee_structures", "priority": "P2", "tool": "fund_fee_structures", "batch_flag": "--include-fees"},
    "dividends": {"table": "dividends", "priority": "P4", "tool": "fund_dividends", "batch_flag": "--include-distributions", "naturally_sparse": True},
    "splits": {"table": "splits", "priority": "P4", "tool": "fund_splits", "batch_flag": "--include-distributions", "naturally_sparse": True},
}


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path is not None else default_db_path()


def _is_structural_empty(fund_type: str | None, dataset: str) -> bool:
    """Match the coverage_report matrix: prefix-match, case-insensitive
    on Latin fund types (FOF, REITs, QDII), exact on the Chinese ones.
    """
    if not fund_type:
        return False
    text = fund_type.lower()
    for prefix, datasets in EXPECTED_EMPTY.items():
        if text.startswith(prefix.lower()) and dataset in datasets:
            return True
    return False


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fund_rows(
    conn: sqlite3.Connection,
    codes: list[str] | tuple[str, ...] | None,
    fund_type: str | None,
) -> list[sqlite3.Row]:
    clauses = []
    params: list[str] = []
    if codes:
        normalized = [normalize_fund_code(code) for code in codes]
        clauses.append("fund_code in (" + ",".join("?" for _ in normalized) + ")")
        params.extend(normalized)
    if fund_type:
        clauses.append("fund_type like ?")
        params.append(f"%{fund_type}%")
    where = " where " + " and ".join(clauses) if clauses else ""
    return conn.execute(
        f"select fund_code, fund_name, fund_type from funds{where} order by fund_code",
        params,
    ).fetchall()


def _codes_with_rows(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[0] for row in conn.execute(f"select distinct fund_code from {table}")}


def _latest_fetched_at(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(
        f"select fund_code, max(fetched_at) from {table} group by fund_code"
    ).fetchall()
    return {row[0]: row[1] for row in rows if row[1]}


def _is_stale(value: str | None, max_age_hours: float) -> bool:
    if not value:
        return True
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt < datetime.now(UTC) - timedelta(hours=max_age_hours)


def _entry(
    *,
    fund: sqlite3.Row,
    dataset: str,
    issue_type: str,
    priority: str,
    reason: str,
    severity: str,
    score: int,
) -> dict[str, Any]:
    rule = DATASET_RULES[dataset]
    code = fund["fund_code"]
    subcmd = rule["tool"].replace("fund_", "")
    return {
        "priority": priority,
        "score": score,
        "fund_code": code,
        "fund_name": fund["fund_name"],
        "fund_type": fund["fund_type"],
        "dataset": dataset,
        "issue_type": issue_type,
        "severity": severity,
        "reason": reason,
        "recommended_mcp_tool": rule["tool"],
        "recommended_mcp_arguments": {"code": code, "refresh": True},
        "recommended_cli": f"fund-data/scripts/fund_cli.py {subcmd} {code} --provider auto",
        "auto_fill_executed": False,
    }


def _batch_suggestions(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the (P1 / P2 / P3) queue by (priority, dataset) and emit
    one ``batch-sync`` recommendation per group. P4 / structural-empty
    items are intentionally skipped -- they would just spam the agent
    with noise (every 货币型 needs a "batch sync stock_holdings" call
    that will never produce rows).
    """
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in queue:
        if item["priority"] not in {"P1", "P2", "P3"}:
            continue
        grouped[(item["priority"], item["dataset"])].append(item["fund_code"])
    out = []
    for (priority, dataset), codes in sorted(grouped.items()):
        rule = DATASET_RULES[dataset]
        flag = rule.get("batch_flag")
        if flag is None:
            continue
        filename = f"{dataset.lower()}_{priority.lower()}_{len(codes)}_codes.txt"
        command = (
            f"fund-data/scripts/fund_cli.py batch-sync --codes-file {filename} "
            f"--provider auto --concurrency 4"
        )
        if flag:
            command += f" {flag}"
        out.append({
            "dataset": dataset,
            "priority": priority,
            "count": len(codes),
            "codes_file": filename,
            "recommended_cli": command,
        })
    return out


def build_self_audit_queue(
    *,
    db_path: str | Path | None = None,
    codes: list[str] | tuple[str, ...] | None = None,
    fund_type: str | None = None,
    max_age_hours: float = 36.0,
    include_structural: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Build a prioritized read-only remediation queue.

    The function never calls a provider and never mutates the
    database. It reads ``funds`` plus a single ``select distinct
    fund_code`` per dataset, then classifies each (fund, dataset)
    pair as missing / stale / structural-empty / naturally-sparse.
    """
    with _connect(db_path) as conn:
        funds = _fund_rows(conn, codes, fund_type)
        present = {
            dataset: _codes_with_rows(conn, rule["table"])
            for dataset, rule in DATASET_RULES.items()
        }
        fetched_at = {
            dataset: _latest_fetched_at(conn, rule["table"])
            for dataset, rule in DATASET_RULES.items()
            if rule.get("stale_column")
        }

    queue: list[dict[str, Any]] = []
    structural_count = 0
    for fund in funds:
        for dataset, rule in DATASET_RULES.items():
            has_rows = fund["fund_code"] in present[dataset]
            structural = _is_structural_empty(fund["fund_type"], dataset)
            naturally_sparse = bool(rule.get("naturally_sparse"))
            if not has_rows:
                if structural or naturally_sparse:
                    structural_count += 1
                    if include_structural:
                        queue.append(_entry(
                            fund=fund,
                            dataset=dataset,
                            issue_type="structural_empty" if structural else "naturally_sparse",
                            priority="P4",
                            reason="Dataset is expected to be empty or naturally sparse for this fund type.",
                            severity="info",
                            score=100 - DATASET_WEIGHTS[dataset],
                        ))
                    continue
                priority = str(rule["priority"])
                base = 900 if priority == "P1" else 700
                queue.append(_entry(
                    fund=fund,
                    dataset=dataset,
                    issue_type="missing",
                    priority=priority,
                    reason="Dataset has zero local rows and is actionable for this fund type.",
                    severity="warning",
                    score=base - DATASET_WEIGHTS[dataset],
                ))
                continue
            if dataset in fetched_at and _is_stale(
                fetched_at[dataset].get(fund["fund_code"]), max_age_hours
            ):
                queue.append(_entry(
                    fund=fund,
                    dataset=dataset,
                    issue_type="stale",
                    priority="P3",
                    reason=f"Latest fetched_at is older than {max_age_hours:g} hours.",
                    severity="notice",
                    score=500 - DATASET_WEIGHTS[dataset],
                ))

    queue.sort(key=lambda item: (-int(item["score"]), item["fund_code"], item["dataset"]))
    limited = queue[:limit] if limit else queue
    summary = {
        "total_funds": len(funds),
        "queue_size": len(queue),
        "returned": len(limited),
        "p0": sum(1 for item in queue if item["priority"] == "P0"),
        "p1": sum(1 for item in queue if item["priority"] == "P1"),
        "p2": sum(1 for item in queue if item["priority"] == "P2"),
        "p3": sum(1 for item in queue if item["priority"] == "P3"),
        "p4": sum(1 for item in queue if item["priority"] == "P4"),
        "structural_empty": structural_count,
        "auto_fill_executed": False,
    }
    return {
        "summary": summary,
        "queue": limited,
        "batch_suggestions": _batch_suggestions(limited),
    }


def check_fund_health(
    code: str,
    *,
    db_path: str | Path | None = None,
    max_age_hours: float = 36.0,
    include_structural: bool = True,
) -> dict[str, Any]:
    """Single-fund wrapper around :func:`build_self_audit_queue`.

    Defaults ``include_structural=True`` for the single-fund view
    because an operator asking "what's wrong with 110022?" usually
    wants to see the structural-empty / naturally-sparse
    context too (a "stock_holdings: info" row on a 货币型 fund
    saves a roundtrip).
    """
    return build_self_audit_queue(
        db_path=db_path,
        codes=[code],
        max_age_hours=max_age_hours,
        include_structural=include_structural,
    )


__all__ = [
    "DATASET_RULES",
    "DATASET_WEIGHTS",
    "EXPECTED_EMPTY",
    "build_self_audit_queue",
    "check_fund_health",
]
