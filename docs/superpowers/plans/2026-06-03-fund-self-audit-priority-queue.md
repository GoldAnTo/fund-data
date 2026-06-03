# Fund Self-Audit Priority Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a project-level self-audit command and MCP tool that scans local OSS/SQLite fund data, identifies missing or stale datasets, classifies structural-empty cases, and emits a prioritized remediation queue without executing any provider refresh.

**Architecture:** Add a new read-only `fund_data.self_audit` module as the single source for self-audit rules and queue generation. Expose it through Python (`fund_data.self_audit(...)`), CLI (`fund_cli.py self-audit`), and MCP (`fund_self_audit`). Keep existing `coverage_report` behavior intact, but reuse its structural-empty matrix so the audit does not turn naturally sparse data into false alarms.

**Tech Stack:** Python stdlib, SQLite, existing `FundDataStore`, existing MCP JSON-RPC server in `fund-data/scripts/fund_mcp.py`, existing unittest/pytest suite.

---

## What This Builds

This is not just a hint for OpenClaw. It is a project self-check layer.

After implementation, an operator or another AI can run:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit --limit 100 --output /tmp/fund-self-audit.json
```

or call MCP:

```json
{
  "name": "fund_self_audit",
  "arguments": {
    "limit": 100,
    "max_age_hours": 36,
    "include_structural": false
  }
}
```

and get a ranked queue like:

```json
{
  "summary": {
    "total_funds": 26953,
    "queue_size": 1842,
    "p0": 0,
    "p1": 52,
    "p2": 471,
    "p3": 1321,
    "structural_empty": 8600,
    "auto_fill_executed": false
  },
  "queue": [
    {
      "priority": "P1",
      "score": 910,
      "fund_code": "000001",
      "fund_name": "华夏成长混合",
      "fund_type": "混合型",
      "dataset": "fund_profiles",
      "issue_type": "missing",
      "severity": "warning",
      "reason": "Core profile row is missing; fund detail answers will be incomplete.",
      "recommended_mcp_tool": "fund_profile",
      "recommended_mcp_arguments": {
        "code": "000001",
        "refresh": true
      },
      "recommended_cli": "fund-data/scripts/fund_cli.py profile 000001 --provider auto",
      "auto_fill_executed": false
    }
  ],
  "batch_suggestions": [
    {
      "dataset": "fund_profiles",
      "priority": "P1",
      "count": 52,
      "codes_file": "fund_profiles_p1_missing.txt",
      "recommended_cli": "fund-data/scripts/fund_cli.py batch-sync --codes-file fund_profiles_p1_missing.txt --include-profile --provider auto --concurrency 4"
    }
  ]
}
```

The tool must never call live providers. It only reads local SQLite and emits advice.

## Priority Model

Use these priorities exactly:

| Priority | Meaning | Examples | Action |
|---|---|---|---|
| P0 | Cannot audit or identify the fund | `funds` row missing for explicitly requested code, DB missing core tables | Fix local DB/bootstrap before provider refresh |
| P1 | Core answer path broken | missing `fund_profiles`, missing `nav_history`, missing `snapshots` | Refresh soon |
| P2 | Important research dataset missing and actionable | missing `stock_holdings`, `bond_holdings`, `industry_allocations`, `fee_structures` where fund type expects the dataset | Queue for batch fill |
| P3 | Present but stale | newest `nav_history.fetched_at` or `snapshots.fetched_at` older than threshold | Refresh if current data matters |
| P4 | Naturally sparse or structural empty | missing `dividends`, `splits`, or structural-empty holdings for fund type | Report as info, do not prioritize |

Score within priority:

```text
P0 = 1000
P1 = 900 - dataset_weight - stale_penalty
P2 = 700 - dataset_weight
P3 = 500 - age_bucket
P4 = 100
```

Dataset weights:

```python
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
```

This makes core data rise first while keeping the queue stable and predictable.

## Data Rules

Datasets:

```python
DATASET_RULES = {
    "funds": {
        "table": "funds",
        "core": True,
        "tool": "fund_search",
        "cli": "search {code}",
    },
    "fund_profiles": {
        "table": "fund_profiles",
        "core": True,
        "tool": "fund_profile",
        "cli": "profile {code} --provider auto",
        "batch_flag": "--include-profile",
    },
    "nav_history": {
        "table": "nav_history",
        "core": True,
        "tool": "fund_nav_history",
        "cli": "nav {code} --provider auto --refresh",
        "batch_flag": "",
        "stale_column": "fetched_at",
    },
    "snapshots": {
        "table": "snapshots",
        "core": True,
        "tool": "fund_snapshot",
        "cli": "snapshot {code} --provider auto",
        "stale_column": "fetched_at",
    },
    "stock_holdings": {
        "table": "stock_holdings",
        "core": False,
        "tool": "fund_stock_holdings",
        "cli": "holdings {code} --provider auto",
        "batch_flag": "--include-holdings",
    },
    "bond_holdings": {
        "table": "bond_holdings",
        "core": False,
        "tool": "fund_bond_holdings",
        "cli": "bonds {code} --provider auto",
        "batch_flag": "--include-bonds",
    },
    "industry_allocations": {
        "table": "industry_allocations",
        "core": False,
        "tool": "fund_industry_allocations",
        "cli": "industries {code} --provider auto",
        "batch_flag": "--include-industries",
    },
    "fee_structures": {
        "table": "fee_structures",
        "core": False,
        "tool": "fund_fee_structures",
        "cli": "fees {code} --provider auto",
        "batch_flag": "--include-fees",
    },
    "dividends": {
        "table": "dividends",
        "core": False,
        "naturally_sparse": True,
        "tool": "fund_dividends",
        "cli": "dividends {code} --provider auto",
        "batch_flag": "--include-distributions",
    },
    "splits": {
        "table": "splits",
        "core": False,
        "naturally_sparse": True,
        "tool": "fund_splits",
        "cli": "splits {code} --provider auto",
        "batch_flag": "--include-distributions",
    },
}
```

Structural-empty logic must reuse the existing matrix in `fund-data/scripts/coverage_report.py`:

- `货币型`: `stock_holdings`, `industry`
- `债券型`: `stock_holdings`, `industry`
- `指数型-固收`: `stock_holdings`, `industry`
- `指数型`: `stock_holdings`, `industry`
- `FOF`: `stock_holdings`, `bond_holdings`, `industry`
- `REITs`: `stock_holdings`, `bond_holdings`, `industry`
- `QDII`: `industry`

Normalize `industry` to `industry_allocations` inside the new module so the queue uses DB table names consistently.

## File Structure

Create:

- `fund-data/scripts/fund_data/self_audit.py`  
  Owns audit rules, SQLite reads, priority scoring, queue output, and batch suggestions.

- `fund-data/scripts/tests/test_self_audit.py`  
  Unit tests for queue generation, structural-empty classification, stale detection, and no-provider behavior.

Modify:

- `fund-data/scripts/fund_data/__init__.py`  
  Re-export `self_audit`, `check_fund_health`, and `build_self_audit_queue`.

- `fund-data/scripts/fund_mcp.py`  
  Add MCP tools `fund_health_check` and `fund_self_audit`.

- `fund-data/scripts/fund_cli.py`  
  Add CLI command `self-audit` and optional single-fund `health-check`.

- `docs/agent-flows/fund-coverage-pipeline.md` or create `docs/agent-flows/fund-self-audit-pipeline.md`  
  Document operational flow after implementation.

Do not modify provider implementations in this work.

## Public APIs

Python API:

```python
def check_fund_health(
    code: str,
    *,
    db_path: str | Path | None = None,
    max_age_hours: float = 36.0,
    include_structural: bool = True,
) -> dict[str, Any]:
    ...


def build_self_audit_queue(
    *,
    db_path: str | Path | None = None,
    codes: list[str] | tuple[str, ...] | None = None,
    fund_type: str | None = None,
    max_age_hours: float = 36.0,
    include_structural: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    ...
```

CLI:

```bash
fund_cli.py health-check 110022 --max-age-hours 36
fund_cli.py self-audit --limit 100 --max-age-hours 36 --output data/self_audit_queue.json
fund_cli.py self-audit --fund-type 股票型 --limit 200
fund_cli.py self-audit --codes-file watchlist.txt --include-structural
```

MCP:

```text
fund_health_check(code, max_age_hours?, include_structural?, db?)
fund_self_audit(codes?, fund_type?, max_age_hours?, include_structural?, limit?, db?)
```

## Task 1: Implement Read-Only Self-Audit Core

**Files:**

- Create: `fund-data/scripts/fund_data/self_audit.py`
- Modify: `fund-data/scripts/fund_data/__init__.py`
- Test: `fund-data/scripts/tests/test_self_audit.py`

- [ ] **Step 1: Write failing tests for missing core datasets**

Create `fund-data/scripts/tests/test_self_audit.py` with this structure:

```python
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402


def _seed_fund(store, code="110022", fund_type="股票型"):
    store.upsert_funds([
        {
            "fund_code": code,
            "fund_name": "易方达消费行业股票",
            "fund_type": fund_type,
            "company": "",
            "manager": "",
            "nav": None,
            "nav_date": "",
            "other_names": "",
            "source": "test",
        }
    ])


class SelfAuditTests(unittest.TestCase):
    def test_missing_profile_is_p1_recommended_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            _seed_fund(store)

            result = fund_data.build_self_audit_queue(db_path=db_path, limit=10)

        item = next(i for i in result["queue"] if i["dataset"] == "fund_profiles")
        self.assertEqual(item["priority"], "P1")
        self.assertEqual(item["issue_type"], "missing")
        self.assertEqual(item["recommended_mcp_tool"], "fund_profile")
        self.assertEqual(item["recommended_mcp_arguments"], {"code": "110022", "refresh": True})
        self.assertFalse(item["auto_fill_executed"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_self_audit.py'
```

Expected: import or attribute failure because `fund_data.build_self_audit_queue` does not exist.

- [ ] **Step 3: Implement the minimal self-audit module**

Create `fund-data/scripts/fund_data/self_audit.py`:

```python
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


def _fund_rows(conn: sqlite3.Connection, codes: list[str] | tuple[str, ...] | None, fund_type: str | None) -> list[sqlite3.Row]:
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
    return conn.execute(f"select fund_code, fund_name, fund_type from funds{where} order by fund_code", params).fetchall()


def _codes_with_rows(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[0] for row in conn.execute(f"select distinct fund_code from {table}")}


def _latest_fetched_at(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(f"select fund_code, max(fetched_at) from {table} group by fund_code").fetchall()
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
        "recommended_cli": f"fund-data/scripts/fund_cli.py {rule['tool'].replace('fund_', '').replace('_', '-')} {code} --provider auto",
        "auto_fill_executed": False,
    }


def _batch_suggestions(queue: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        command = f"fund-data/scripts/fund_cli.py batch-sync --codes-file {filename} --provider auto --concurrency 4"
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
    with _connect(db_path) as conn:
        funds = _fund_rows(conn, codes, fund_type)
        present = {dataset: _codes_with_rows(conn, rule["table"]) for dataset, rule in DATASET_RULES.items()}
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
            if dataset in fetched_at and _is_stale(fetched_at[dataset].get(fund["fund_code"]), max_age_hours):
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
    result = build_self_audit_queue(
        db_path=db_path,
        codes=[code],
        max_age_hours=max_age_hours,
        include_structural=include_structural,
    )
    return result
```

- [ ] **Step 4: Re-export from package root**

Modify `fund-data/scripts/fund_data/__init__.py`:

```python
from .self_audit import build_self_audit_queue, check_fund_health
```

Add both names to `__all__`.

- [ ] **Step 5: Run tests**

Run:

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_self_audit.py'
```

Expected: passes.

- [ ] **Step 6: Commit**

```bash
git add fund-data/scripts/fund_data/self_audit.py fund-data/scripts/fund_data/__init__.py fund-data/scripts/tests/test_self_audit.py
git commit -m "feat(audit): add read-only fund self-audit queue"
```

## Task 2: Add Structural-Empty and Stale Tests

**Files:**

- Modify: `fund-data/scripts/tests/test_self_audit.py`

- [ ] **Step 1: Add structural-empty test**

Append:

```python
    def test_structural_empty_stock_holdings_are_info_not_actionable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            _seed_fund(store, code="000001", fund_type="货币型")

            result = fund_data.build_self_audit_queue(
                db_path=db_path,
                codes=["000001"],
                include_structural=True,
            )

        item = next(i for i in result["queue"] if i["dataset"] == "stock_holdings")
        self.assertEqual(item["priority"], "P4")
        self.assertEqual(item["issue_type"], "structural_empty")
        self.assertEqual(item["severity"], "info")
        self.assertFalse(item["auto_fill_executed"])
```

- [ ] **Step 2: Add stale test**

Append:

```python
    def test_stale_nav_is_p3(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "fund_data.sqlite"
            store = fund_data.FundDataStore(db_path)
            _seed_fund(store)
            store.upsert_nav_history("110022", [{"nav_date": "2024-01-01", "unit_nav": 1.0, "source": "test"}])
            with sqlite3.connect(db_path) as conn:
                conn.execute("update nav_history set fetched_at = '2000-01-01T00:00:00+00:00'")

            result = fund_data.build_self_audit_queue(db_path=db_path, codes=["110022"], max_age_hours=1)

        item = next(i for i in result["queue"] if i["dataset"] == "nav_history" and i["issue_type"] == "stale")
        self.assertEqual(item["priority"], "P3")
        self.assertEqual(item["severity"], "notice")
```

- [ ] **Step 3: Run tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_self_audit.py'
```

Expected: passes.

- [ ] **Step 4: Commit**

```bash
git add fund-data/scripts/tests/test_self_audit.py fund-data/scripts/fund_data/self_audit.py
git commit -m "test(audit): cover structural empty and stale rows"
```

## Task 3: Add CLI Commands

**Files:**

- Modify: `fund-data/scripts/fund_cli.py`
- Test: `fund-data/scripts/tests/test_fund_cli.py`

- [ ] **Step 1: Add failing CLI tests**

In `fund-data/scripts/tests/test_fund_cli.py`, add tests that patch `fund_cli.fund_data.build_self_audit_queue` and `fund_cli.fund_data.check_fund_health`:

```python
def test_self_audit_cli_prints_json(self):
    payload = {"summary": {"queue_size": 1, "auto_fill_executed": False}, "queue": [], "batch_suggestions": []}
    with patch.object(fund_cli.fund_data, "build_self_audit_queue", return_value=payload):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            exit_code = fund_cli.main(["self-audit", "--limit", "5"])
    self.assertEqual(exit_code, 0)
    self.assertEqual(json.loads(buf.getvalue()), payload)


def test_health_check_cli_prints_json(self):
    payload = {"summary": {"queue_size": 0, "auto_fill_executed": False}, "queue": [], "batch_suggestions": []}
    with patch.object(fund_cli.fund_data, "check_fund_health", return_value=payload):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            exit_code = fund_cli.main(["health-check", "110022"])
    self.assertEqual(exit_code, 0)
    self.assertEqual(json.loads(buf.getvalue()), payload)
```

Use imports already present in the file; if `io` or `patch` is missing, add them.

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_cli.py'
```

Expected: parser rejects `self-audit` and `health-check`.

- [ ] **Step 3: Add CLI parsers**

In `build_parser()` add:

```python
    health_check = subparsers.add_parser("health-check", help="Inspect one fund and recommend missing-data actions")
    health_check.add_argument("code")
    health_check.add_argument("--max-age-hours", type=float, default=36.0)
    health_check.add_argument("--include-structural", action="store_true")
    health_check.add_argument("--output")
    _add_common_db_arg(health_check)

    self_audit = subparsers.add_parser("self-audit", help="Build a prioritized read-only data remediation queue")
    self_audit.add_argument("--code", action="append", help="Fund code; can be repeated")
    self_audit.add_argument("--codes-file", action="append", help="File containing fund codes")
    self_audit.add_argument("--fund-type")
    self_audit.add_argument("--max-age-hours", type=float, default=36.0)
    self_audit.add_argument("--include-structural", action="store_true")
    self_audit.add_argument("--limit", type=int)
    self_audit.add_argument("--output")
    _add_common_db_arg(self_audit)
```

In `main()` add before `export`:

```python
        if args.command == "health-check":
            payload = fund_data.check_fund_health(
                args.code,
                db_path=args.db,
                max_age_hours=args.max_age_hours,
                include_structural=args.include_structural,
            )
            if args.output:
                _write_json_to_file(args.output, payload)
                print(args.output)
            else:
                _print_json(payload)
            return 0

        if args.command == "self-audit":
            codes = []
            for codes_file in args.codes_file or []:
                codes.extend(fund_data.parse_fund_codes(Path(codes_file).read_text(encoding="utf-8")))
            codes.extend(fund_data.normalize_fund_codes(args.code or []))
            payload = fund_data.build_self_audit_queue(
                db_path=args.db,
                codes=codes or None,
                fund_type=args.fund_type,
                max_age_hours=args.max_age_hours,
                include_structural=args.include_structural,
                limit=args.limit,
            )
            if args.output:
                _write_json_to_file(args.output, payload)
                print(args.output)
            else:
                _print_json(payload)
            return 0
```

- [ ] **Step 4: Run CLI tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_cli.py'
```

Expected: passes.

- [ ] **Step 5: Manual smoke test**

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit --limit 5
```

Expected: JSON with `summary`, `queue`, `batch_suggestions`, and `auto_fill_executed: false`.

- [ ] **Step 6: Commit**

```bash
git add fund-data/scripts/fund_cli.py fund-data/scripts/tests/test_fund_cli.py
git commit -m "feat(cli): expose fund self-audit queue"
```

## Task 4: Add MCP Tools

**Files:**

- Modify: `fund-data/scripts/fund_mcp.py`
- Test: `fund-data/scripts/tests/test_fund_mcp.py`

- [ ] **Step 1: Add failing MCP tests**

In `fund-data/scripts/tests/test_fund_mcp.py`, add:

```python
    def test_fund_self_audit_returns_priority_queue(self):
        payload = {
            "summary": {"queue_size": 1, "auto_fill_executed": False},
            "queue": [{"fund_code": "110022", "dataset": "fund_profiles", "priority": "P1"}],
            "batch_suggestions": [],
        }
        with patch.object(fund_mcp.fund_data, "build_self_audit_queue", return_value=payload) as mock_audit:
            response = fund_mcp.handle_message({
                "jsonrpc": "2.0",
                "id": 50,
                "method": "tools/call",
                "params": {
                    "name": "fund_self_audit",
                    "arguments": {"limit": 10, "max_age_hours": 36},
                },
            })
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"], payload)
        mock_audit.assert_called_once()

    def test_fund_health_check_returns_single_fund_queue(self):
        payload = {"summary": {"queue_size": 0, "auto_fill_executed": False}, "queue": [], "batch_suggestions": []}
        with patch.object(fund_mcp.fund_data, "check_fund_health", return_value=payload) as mock_health:
            response = fund_mcp.handle_message({
                "jsonrpc": "2.0",
                "id": 51,
                "method": "tools/call",
                "params": {
                    "name": "fund_health_check",
                    "arguments": {"code": "110022"},
                },
            })
        self.assertFalse(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"], payload)
        mock_health.assert_called_once()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_mcp.py'
```

Expected: unknown tool errors.

- [ ] **Step 3: Add MCP schemas and handlers**

In `TOOLS`, add:

```python
    _tool(
        "fund_health_check",
        "Inspect one fund's local OSS/SQLite rows and recommend missing-data actions without executing refresh.",
        {
            "db": COMMON_ARGS["db"],
            "code": _string_schema("6-digit fund code."),
            "max_age_hours": _number_schema("Stale threshold in hours.", minimum=0),
            "include_structural": _boolean_schema("Include structural-empty / naturally sparse info rows."),
        },
        required=["code"],
    ),
    _tool(
        "fund_self_audit",
        "Build a prioritized read-only remediation queue for missing or stale fund datasets.",
        {
            "db": COMMON_ARGS["db"],
            "codes": _array_schema("Optional fund codes.", _string_schema("6-digit fund code")),
            "fund_type": _string_schema("Filter by fund type substring."),
            "max_age_hours": _number_schema("Stale threshold in hours.", minimum=0),
            "include_structural": _boolean_schema("Include structural-empty / naturally sparse info rows."),
            "limit": _integer_schema("Maximum queue items to return.", minimum=1),
        },
    ),
```

Add handlers:

```python
def _call_fund_health_check(arguments: dict[str, Any]) -> dict[str, Any]:
    return fund_data.check_fund_health(
        _required_str(arguments, "code"),
        db_path=_db(arguments),
        max_age_hours=_optional_float(arguments, "max_age_hours", 36.0) or 36.0,
        include_structural=_optional_bool(arguments, "include_structural"),
    )


def _call_fund_self_audit(arguments: dict[str, Any]) -> dict[str, Any]:
    return fund_data.build_self_audit_queue(
        db_path=_db(arguments),
        codes=_optional_str_list(arguments, "codes"),
        fund_type=_optional_str(arguments, "fund_type"),
        max_age_hours=_optional_float(arguments, "max_age_hours", 36.0) or 36.0,
        include_structural=_optional_bool(arguments, "include_structural"),
        limit=_optional_int(arguments, "limit"),
    )
```

Register:

```python
    "fund_health_check": _call_fund_health_check,
    "fund_self_audit": _call_fund_self_audit,
```

- [ ] **Step 4: Run MCP tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_mcp.py'
```

Expected: passes.

- [ ] **Step 5: Manual MCP smoke test**

Run a direct handler call:

```bash
.venv-akshare/bin/python - <<'PY'
import json, sys
sys.path.insert(0, "fund-data/scripts")
sys.path.insert(0, "fund-data")
from scripts import fund_mcp
msg = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {"name": "fund_self_audit", "arguments": {"limit": 5}},
}
print(json.dumps(fund_mcp.handle_message(msg), ensure_ascii=False, indent=2)[:2000])
PY
```

Expected: response has `result.structuredContent.summary.auto_fill_executed` equal to `false`.

- [ ] **Step 6: Commit**

```bash
git add fund-data/scripts/fund_mcp.py fund-data/scripts/tests/test_fund_mcp.py
git commit -m "feat(mcp): expose fund self-audit queue"
```

## Task 5: Add Operator Documentation

**Files:**

- Create: `docs/agent-flows/fund-self-audit-pipeline.md`
- Modify: `docs/agent-flows/README.md`

- [ ] **Step 1: Write documentation**

Create `docs/agent-flows/fund-self-audit-pipeline.md`:

```markdown
# Fund Self-Audit Pipeline

The self-audit pipeline is the project-level data quality queue.
It scans the local OSS/SQLite data plane, classifies missing and stale
datasets, suppresses structural-empty false positives, and emits a
prioritized queue for humans or agents to process.

It does not call providers and does not mutate the database.

## Commands

```bash
python fund-data/scripts/fund_cli.py self-audit --limit 100 --output data/self_audit_queue.json
python fund-data/scripts/fund_cli.py health-check 110022
```

## MCP Tools

- `fund_self_audit`: full or filtered queue.
- `fund_health_check`: single-fund queue.

## Priority Order

- P0: cannot audit the fund or local DB is missing core tables.
- P1: core answer path missing profile, NAV, or snapshot.
- P2: actionable research dataset missing.
- P3: present but stale.
- P4: structural-empty or naturally sparse.

## Processing Discipline

Agents must process P1 before P2, P2 before P3, and must not process P4
unless the user explicitly asks to verify sparse data.

The self-audit output includes `recommended_cli` and
`recommended_mcp_tool`, but the self-audit itself never executes them.
```

- [ ] **Step 2: Link from README**

Modify `docs/agent-flows/README.md` and add a row:

```markdown
| `fund_self_audit` / `fund_health_check` — prioritized self-audit queue | [`fund-self-audit-pipeline.md`](./fund-self-audit-pipeline.md) | Project-level missing/stale data triage without provider mutation. |
```

- [ ] **Step 3: Commit**

```bash
git add docs/agent-flows/fund-self-audit-pipeline.md docs/agent-flows/README.md
git commit -m "docs(audit): document fund self-audit pipeline"
```

## Task 6: Final Verification

- [ ] **Step 1: Run targeted tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_self_audit.py'
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_cli.py'
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_mcp.py'
```

Expected: all pass.

- [ ] **Step 2: Run full tests**

```bash
.venv-akshare/bin/python -m pytest fund-data/scripts/tests -q
```

Expected: all tests pass.

- [ ] **Step 3: Run smoke commands**

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py health-check 110022
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit --limit 10
```

Expected:

- output is JSON
- `summary.auto_fill_executed` is `false`
- queue entries include `priority`, `dataset`, `recommended_mcp_tool`, and `recommended_cli`
- no provider network call is made

- [ ] **Step 4: Commit final fixes if needed**

```bash
git status --short
git add <changed-files>
git commit -m "chore(audit): verify self-audit workflow"
```

## Non-Goals

Do not implement these in this work:

- automatic provider refresh
- automatic batch-sync execution
- database mutation from self-audit
- new provider endpoints
- GitHub Actions workflow to process the queue
- changing `coverage_report` scoring

Those can be separate follow-up tasks after the self-audit queue is reliable.

## Acceptance Checklist

The implementation is accepted only if all are true:

- `fund_self_audit` exists in MCP `tools/list`.
- `fund_health_check` exists in MCP `tools/list`.
- CLI `self-audit` returns project-level priority queue.
- CLI `health-check 110022` returns single-fund queue.
- Output includes `auto_fill_executed: false`.
- Structural-empty holdings are not P1/P2.
- Dividends and splits default to P4/info unless explicitly included.
- Stale NAV/snapshot rows become P3.
- Missing profile/NAV/snapshot become P1.
- Missing actionable holdings/bonds/industry/fees become P2.
- Full test suite passes.
