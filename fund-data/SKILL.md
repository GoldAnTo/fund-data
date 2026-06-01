---
name: fund-data
version: 1.0.0
description: Search, fetch, normalize, and persist Chinese public fund data for local research workflows with provider fallback across Investoday, Eastmoney, and optional AkShare. Use when agents need to find funds by name/code/theme, pull fund basic information, fetch historical NAV, capture Eastmoney fund snapshots, fetch stock/bond holdings, industry allocation, fee structures, dividends, splits, fund manager data, run batch fund sync with failure queues, populate a local SQLite fund data base, export persisted fund datasets, or build repeatable fund-data ingestion flows. Trigger on requests mentioning 基金搜索, 基金数据, 基金净值, 基金持仓, 基金经理, 基金费率, 基金行业配置, 批量同步, 失败队列, AkShare fallback, 多源切换, 基金数据底座, fund search, fund NAV, local fund database, batch sync, or persistent fund data sync.
homepage: https://github.com/GoldAnTo/fund-data
tags:
  - fund
  - finance
  - eastmoney
  - akshare
  - investoday
  - sqlite
  - chinese-market
  - data-pipeline
tools:
  - bash
  - read
  - edit
  - web_fetch
  - python
---

# Fund Data

## Overview

Use this skill to turn ad hoc fund lookup into a repeatable local data workflow. The bundled Python scripts search funds, fetch core public fund data, store normalized rows in SQLite, and keep raw response snapshots for auditability.

The first version uses no-key Eastmoney public endpoints and optional AkShare fallback. If the user provides an Investoday API key or asks for provider-catalog endpoints such as benchmark returns, manager data, or fund categories, use the existing financial-data API workflow as the higher-fidelity source and keep this skill's SQLite schema as the local persistence layer.

## Quick Start

Resolve paths relative to this skill folder.

```bash
python3 scripts/fund_cli.py list --provider auto --limit 20
python3 scripts/fund_cli.py search 沪深300
python3 scripts/fund_cli.py nav 110022 --start-date 2024-01-01 --end-date 2024-01-31
python3 scripts/fund_cli.py snapshot 110022
python3 scripts/fund_cli.py profile 110022 --provider akshare
python3 scripts/fund_cli.py holdings 110022 --provider akshare --report-year 2024
python3 scripts/fund_cli.py bonds 110022 --provider akshare --report-year 2024
python3 scripts/fund_cli.py industries 110022 --provider akshare --report-year 2024
python3 scripts/fund_cli.py fees 110022 --provider akshare --indicator 申购费率
python3 scripts/fund_cli.py managers --code 110022 --provider akshare
python3 scripts/fund_cli.py coverage --fund-code 110022
python3 scripts/fund_cli.py sync 110022 --start-date 2024-01-01 --end-date 2024-01-31 --include-holdings --report-year 2024
python3 scripts/fund_cli.py sync 110022 --provider auto --include-all --report-year 2024 --fee-indicator 申购费率
python3 scripts/fund_cli.py batch-sync --codes-file fund_codes.txt --provider auto --include-all --report-year 2024 --fee-indicator 申购费率
python3 scripts/fund_cli.py export funds --format csv --output /tmp/funds.csv
```

Default SQLite path:

```text
data/fund_data.sqlite
```

Override with:

```bash
FUND_DATA_DB=/absolute/path/fund_data.sqlite python3 scripts/fund_cli.py search 易方达消费
```

Provider selection:

```bash
python3 scripts/fund_cli.py search 沪深300 --provider auto
python3 scripts/fund_cli.py list --provider eastmoney --limit 100
python3 scripts/fund_cli.py nav 110022 --provider eastmoney
python3 scripts/fund_cli.py holdings 110022 --provider akshare --report-year 2024
python3 scripts/fund_cli.py profile 110022 --provider akshare
```

`auto` tries `investoday` first when `INVESTDATA_API_KEY` is set. Without a key, search/NAV use Eastmoney first and fall back to AkShare if installed. Holdings, profile, industry, fee, dividend/split, and manager commands use AkShare-backed free sources until a structured provider is configured.

AkShare is optional. Install it into the Python environment used to run the CLI:

```bash
python3 -m venv .venv-akshare
.venv-akshare/bin/python -m pip install akshare
.venv-akshare/bin/python fund-data/scripts/fund_cli.py holdings 110022 --provider akshare --report-year 2024
.venv-akshare/bin/python fund-data/scripts/fund_cli.py profile 110022 --provider akshare
```

To force a run to skip AkShare, set `FUND_DATA_DISABLE_AKSHARE=1`.

## Workflow

1. For a broad local index, run `list` and persist the fund universe.
2. For discovery, run `search` with a fund name, index/theme keyword, pinyin, or 6-digit code.
3. For a specific fund, run `snapshot` to capture fund name, fees, recent return variables, and disclosed stock-code list from Eastmoney `pingzhongdata`.
4. Run `nav` for the required date window. Store returns as decimals: `1%` becomes `0.01`.
5. Run `profile`, `holdings`, `bonds`, `industries`, `fees`, and `managers` for AkShare-backed free-source fundamentals and portfolio disclosures.
6. Run `dividends` and `splits` when the workflow needs fund-specific distribution or split history. Empty arrays are valid for funds with no disclosed records.
7. Run `sync --include-holdings` when the user wants snapshot, NAV, and stock holdings persisted in one step.
8. Run `sync --include-all` when the user wants a local fund base row plus snapshot, NAV, profile, stock/bond holdings, industry allocation, fees, dividends, splits, managers, and a coverage object in one run. Snapshot and NAV failures stop the fund sync; optional dataset failures are returned in `dataset_errors` so sparse fund types can still persist partial data.
9. Run `batch-sync --codes-file fund_codes.txt --include-all` to build the local data base for a watchlist. The command continues after per-fund hard failures and records those failures in `sync_failures`; optional dataset gaps stay on each fund result as `dataset_errors`.
10. Run `coverage` to see which local tables are populated for a fund.
11. Run `export` when a downstream report, notebook, or project needs the data as JSON or CSV.

## Python API

Use `scripts/fund_data.py` directly when writing project code:

```python
from pathlib import Path
from fund_data import (
    coverage_rows,
    fetch_bond_holdings,
    fetch_fee_structures,
    fetch_fund_list,
    fetch_fund_managers,
    fetch_industry_allocations,
    fetch_nav_history,
    fetch_profile,
    fetch_snapshot,
    fetch_stock_holdings,
    parse_fund_codes,
    search_funds,
    batch_sync_funds,
    sync_fund,
)

db_path = Path("data/fund_data.sqlite")
fund_list = fetch_fund_list(db_path=db_path, provider="auto")
funds = search_funds("沪深300", db_path=db_path, provider="auto")
snapshot = fetch_snapshot("110022", db_path=db_path)
nav_rows = fetch_nav_history("110022", start_date="2024-01-01", end_date="2024-01-31", db_path=db_path)
holding_rows = fetch_stock_holdings("110022", report_year="2024", db_path=db_path, provider="akshare")
profile = fetch_profile("110022", db_path=db_path, provider="akshare")
bond_rows = fetch_bond_holdings("110022", report_year="2024", db_path=db_path, provider="akshare")
industry_rows = fetch_industry_allocations("110022", report_year="2024", db_path=db_path, provider="akshare")
fee_rows = fetch_fee_structures("110022", indicators=["申购费率"], db_path=db_path, provider="akshare")
manager_rows = fetch_fund_managers("110022", db_path=db_path, provider="akshare")
coverage = coverage_rows(db_path=db_path, fund_code="110022")
sync_result = sync_fund("110022", db_path=db_path, provider="auto", include_all=True, report_year="2024")
codes = parse_fund_codes(Path("fund_codes.txt").read_text(encoding="utf-8"))
batch_result = batch_sync_funds(codes, db_path=db_path, provider="auto", include_all=True, report_year="2024")
```

For tests, pass `raw_text=` to parsing/fetch helpers or `--offline-raw` to the CLI so no network call is required.

## Persistence Rules

- Preserve fund codes as 6-character strings.
- Keep raw responses in `raw_responses` before relying on normalized tables for reports.
- Use `sync_runs` as the audit trail for repeatable pulls.
- Use `sync_failures` as the failure queue for `batch-sync`; rerun those codes after provider instability clears.
- `sync` writes or refreshes a minimal `funds` row from profile/snapshot data so `coverage` works even when `list` or `search` has not been run first.
- Treat `dataset_errors` as partial-coverage warnings for optional datasets, not as failed fund syncs.
- Use `source` fields and `raw_responses` to see which provider actually served a request.
- When a provider is unstable, switch with `--provider eastmoney`, `--provider akshare`, or `--provider investoday`; use `auto` for fallback.
- Do not claim live, current, or complete market data unless a fresh call succeeds in the current turn.
- Do not use these outputs as personalized investment advice. Report data source, fetch date, and uncertainty when interpreting fund data.

Read `references/schema.md` when a task needs table names, field meanings, or downstream integration details.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `akshare is not installed; run python3 -m pip install akshare` | CLI run with system Python; AkShare lives in `.venv-akshare` | `python3 scripts/install_skill.py status` then re-run via the venv python it reports |
| `ProviderError: TUSHARE_TOKEN is not set` | Tushare selected by auto chain but no token | `export TUSHARE_TOKEN=...` or pass `--provider eastmoney` to skip it |
| `ProviderError: INVESTDATA_API_KEY is not set` | Same, for Investoday | `export INVESTDATA_API_KEY=...` or pick another provider |
| 38+ rows in `sync_failures` after a long backfill | AkShare or Eastmoney rate-limited mid-run | `python3 scripts/retry_failures.py --provider eastmoney --limit 50` to drain the queue once the window opens; `--dry-run` to preview |
| `dataset_errors` full of `no attribute` for `profile` / `bonds` / etc. | `--provider eastmoney` was forced but those capabilities need AkShare or Tushare | Drop `--provider` to let auto pick, or use `--provider tushare` / `--provider akshare` |
| `backfill.py` exit code 1 inside CI | `data/` directory does not exist on a fresh runner (gitignored) | The nightly workflow seeds it; for ad-hoc local runs, run `python3 scripts/fund_cli.py list` first |
| All 25k funds sync but `fund_profiles` is still empty | Eastmoney does not implement profile | Pass `--provider tushare` (with token) or `--provider akshare` (with venv) for the second pass |
| `doctor.py` reports `akshare: ok=false, venv missing` | The `.venv-akshare` directory was deleted or never created | `python3 -m venv .venv-akshare && .venv-akshare/bin/python -m pip install -r requirements.txt` |

For a complete environment audit, run:

```bash
python3 scripts/doctor.py
```

It exits non-zero on the first failure and prints a JSON report you can pipe into a CI gate.

## Source Notes

No-key Eastmoney endpoints used by `scripts/fund_data.py`:

- Fund search: `https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx`
- Full code list: `https://fund.eastmoney.com/js/fundcode_search.js`
- Historical NAV: `https://fundf10.eastmoney.com/F10DataApi.aspx`
- Snapshot JS: `https://fund.eastmoney.com/pingzhongdata/{code}.js`

Optional AkShare functions used when installed:

- Fund list/search fallback: `ak.fund_name_em()`
- Historical NAV fallback: `ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")`
- Stock holdings: `ak.fund_portfolio_hold_em(symbol=code, date=year)`
- Fund profile: `ak.fund_overview_em(symbol=code)`
- Bond holdings: `ak.fund_portfolio_bond_hold_em(symbol=code, date=year)`
- Industry allocation: `ak.fund_portfolio_industry_allocation_em(symbol=code, date=year)`
- Dividends/splits: `ak.fund_open_fund_info_em(symbol=code, indicator="分红送配详情" | "拆分详情")`
- Fund managers: `ak.fund_manager_em()`

Direct Eastmoney page parsing used when AkShare fee aliases are stale:

- Fee page: `https://fundf10.eastmoney.com/jjfl_{code}.html`

Keep requests serial and conservative. The client defaults to a one-second minimum interval.
