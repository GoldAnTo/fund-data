# Fund Self-Audit Pipeline

> **Last updated:** 2026-06-03
> **Source of truth:** `fund-data/scripts/fund_data/self_audit.py`
> (the audit core), `fund-data/scripts/fund_cli.py` (`self-audit` /
> `health-check` subcommands), `fund-data/scripts/fund_mcp.py`
> (`fund_self_audit` / `fund_health_check` tools).
> **For:** Anyone — human or AI — who needs a project-level
> missing / stale data queue over the local SQLite base. The
> audit is **read-only** (never calls a provider, never writes
> rows) and is the triage step that should run **before** any
> `fund-batch-sync` or `fund_profile_backfill` pass.

The self-audit pipeline is the project-level data quality queue.
It scans the local SQLite base, classifies missing and stale
datasets, suppresses structural-empty false positives, and
emits a prioritized queue for humans or agents to process.

## Layer breakdown

| Layer | Module | Role |
|---|---|---|
| Core | `fund_data.self_audit` | rules, SQLite reads, priority scoring, queue output, batch suggestions |
| CLI | `fund_data.fund_cli` | `self-audit` and `health-check` subcommands |
| MCP | `fund_data.fund_mcp` | `fund_self_audit` and `fund_health_check` tools |

The audit **never** calls a provider. The output is JSON with
`summary.queue_size`, `summary.p0..p4`, `summary.structural_empty`,
`summary.auto_fill_executed` (always `false`), `queue[]`, and
`batch_suggestions[]`.

## Priority model

| Priority | Score | Meaning | Examples |
|---|---|---|---|
| P0 | 1000 | Cannot audit or identify the fund | `funds` row missing, DB missing core tables |
| P1 | 900 - dataset_weight - stale_penalty | Core answer path broken | missing `fund_profiles`, `nav_history`, `snapshots` |
| P2 | 700 - dataset_weight | Important research dataset missing and actionable | missing `stock_holdings`, `bond_holdings`, `industry_allocations`, `fee_structures` where fund type expects the dataset |
| P3 | 500 - age_bucket | Present but stale | `fetched_at` older than `--max-age-hours` on `nav_history` / `snapshots` |
| P4 | 100 | Naturally sparse or structural empty | missing `dividends`, `splits`, or structural-empty holdings for fund type |

## Dataset rules

Each rule maps a logical name to a DB table, an MCP tool, a CLI
subcommand, and an optional `batch-sync` flag. The rule set is
the single source of truth — both the audit queue and the
`batch_suggestions[]` builder read it.

```python
DATASET_RULES = {
    "fund_profiles":        {"table": "fund_profiles",        "priority": "P1", "tool": "fund_profile",            "cli": "profile",    "batch_flag": "--include-profile"},
    "nav_history":          {"table": "nav_history",          "priority": "P1", "tool": "fund_nav_history",        "cli": "nav",        "batch_flag": "", "stale_column": "fetched_at"},
    "snapshots":            {"table": "snapshots",            "priority": "P1", "tool": "fund_snapshot",           "cli": "snapshot",   "batch_flag": None, "stale_column": "fetched_at"},
    "stock_holdings":       {"table": "stock_holdings",       "priority": "P2", "tool": "fund_stock_holdings",     "cli": "holdings",   "batch_flag": "--include-holdings"},
    "bond_holdings":        {"table": "bond_holdings",        "priority": "P2", "tool": "fund_bond_holdings",      "cli": "bonds",      "batch_flag": "--include-bonds"},
    "industry_allocations": {"table": "industry_allocations", "priority": "P2", "tool": "fund_industry_allocations","cli": "industries", "batch_flag": "--include-industries"},
    "fee_structures":       {"table": "fee_structures",       "priority": "P2", "tool": "fund_fee_structures",     "cli": "fees",       "batch_flag": "--include-fees"},
    "dividends":            {"table": "dividends",            "priority": "P4", "tool": "fund_dividends",          "cli": "dividends",  "batch_flag": "--include-distributions", "naturally_sparse": True},
    "splits":               {"table": "splits",               "priority": "P4", "tool": "fund_splits",             "cli": "splits",     "batch_flag": "--include-distributions", "naturally_sparse": True},
}
```

`fund_managers` is intentionally **not** in the rule set —
`fund_managers` and the new `fund_manager_links` projection
are populated together by `fetch_fund_managers` and there is
no natural per-fund "missing" signal that would survive
the per-fund self-audit model.

## Structural-empty matrix

Reused from `fund-data/scripts/coverage_report.py`. Dataset
keys here use **DB table names** (``industry_allocations``) so
the queue is self-describing for any caller; the coverage
report uses the short labels (``industry``). Update both
together when a new fund_type ships.

```python
EXPECTED_EMPTY = {
    "货币型":     frozenset({"stock_holdings", "industry_allocations"}),
    "债券型":     frozenset({"stock_holdings", "industry_allocations"}),
    "指数型-固收": frozenset({"stock_holdings", "industry_allocations"}),
    "指数型":     frozenset({"stock_holdings", "industry_allocations"}),
    "FOF":        frozenset({"stock_holdings", "bond_holdings", "industry_allocations"}),
    "REITs":      frozenset({"stock_holdings", "bond_holdings", "industry_allocations"}),
    "QDII":       frozenset({"industry_allocations"}),
}
```

## Commands

```bash
# Full project-level queue (top 100)
python fund-data/scripts/fund_cli.py self-audit --limit 100 --output data/self_audit_queue.json

# Single fund triage (default include_structural=True)
python fund-data/scripts/fund_cli.py health-check 110022

# Filter to a fund type or watchlist
python fund-data/scripts/fund_cli.py self-audit --fund-type 股票型 --limit 200
python fund-data/scripts/fund_cli.py self-audit --codes-file watchlist.txt --include-structural
```

## MCP tools

- `fund_self_audit` — full or filtered queue.
- `fund_health_check` — single-fund queue (defaults
  `include_structural=True` so the caller sees the
  structural-empty context too).

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

## Queue entry shape

```json
{
  "priority": "P1",
  "score": 910,
  "fund_code": "000001",
  "fund_name": "华夏成长混合",
  "fund_type": "混合型",
  "dataset": "fund_profiles",
  "issue_type": "missing",
  "severity": "warning",
  "reason": "Dataset has zero local rows and is actionable for this fund type.",
  "recommended_mcp_tool": "fund_profile",
  "recommended_mcp_arguments": {"code": "000001", "refresh": true},
  "recommended_cli": "fund-data/scripts/fund_cli.py profile 000001 --provider auto",
  "auto_fill_executed": false
}
```

`issue_type` is one of `missing` (actionable) / `stale` (P3
notice) / `structural_empty` (P4 info, fund type does not
disclose this dataset) / `naturally_sparse` (P4 info, e.g.
`dividends` on a money-market fund).

`severity` is one of `warning` (actionable), `notice` (stale),
`info` (structural-empty / naturally-sparse).

## Processing discipline

Agents must process P1 before P2, P2 before P3, and must not
process P4 unless the user explicitly asks to verify sparse
data. The `batch_suggestions[]` array emits one
`fund_cli batch-sync` command per (priority, dataset) group
so the consumer can act on the entire priority tier in one
run; the suggestions are only emitted for P1 / P2 / P3
groups with a `batch_flag` (`nav_history` and `snapshots`
have no batch flag and stay out of the suggestions because
the per-fund CLI invocation is the only sensible entry
point).

The self-audit output includes `recommended_cli` and
`recommended_mcp_tool`, but the self-audit itself **never**
executes them. Treat the output as advice, never as a
job. A future GitHub Actions workflow that consumes the
queue is an explicit non-goal of this work.

## Environment

| Var | Default | Used for |
|---|---|---|
| `FUND_DATA_DB` | `fund-data/data/fund_data.sqlite` (or cloud cache) | Pin the audit to a specific DB |
| `FUND_DATA_CACHE_DIR` | `~/.cache/fund-data` | Override the cloud cache resolver |
| `FUND_DATA_AUTO_PULL=0` | `1` | Skip the cloud bootstrap when no `FUND_DATA_DB` is pinned (the audit still runs, just against the on-disk fallback) |

The audit does not read `INVESTODAY_API_KEY` / `TUSHARE_TOKEN` /
`AKSHARE_*`; it never calls a provider.
