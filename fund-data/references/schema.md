# Fund Data Schema

SQLite default path: `data/fund_data.sqlite`.

Set `FUND_DATA_DB` or pass `--db` to use another database.

## Tables

### funds

One current discovery/basic-info row per fund code.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code, primary key |
| `fund_name` | short display name |
| `fund_type` | source fund type, such as `指数型-股票` |
| `company` | fund company name when available |
| `manager` | current manager text when available |
| `nav` | latest unit NAV from source payload |
| `nav_date` | NAV date as `YYYY-MM-DD` |
| `other_names` | comma-separated aliases from source |
| `source` | source identifier |
| `updated_at` | UTC fetch/update timestamp |

### nav_history

Historical NAV rows keyed by fund code and NAV date.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code |
| `nav_date` | NAV date as `YYYY-MM-DD` |
| `unit_nav` | unit NAV |
| `accumulated_nav` | accumulated NAV |
| `daily_growth_rate` | decimal daily return, so `-1.32%` is `-0.0132` |
| `subscribe_status` | source subscription status text |
| `redeem_status` | source redemption status text |
| `dividend` | dividend/split text from source table |
| `source` | source identifier |
| `fetched_at` | UTC fetch timestamp |

### snapshots

One latest snapshot row per fund code from Eastmoney `pingzhongdata`.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code, primary key |
| `fund_name` | fund name |
| `source_rate` | original fee rate, source units |
| `current_rate` | current fee rate, source units |
| `min_purchase` | minimum purchase amount when present |
| `returns_json` | JSON object with `one_year`, `six_month`, `three_month`, `one_month` decimal returns |
| `stock_codes_json` | JSON array of disclosed stock codes with Eastmoney market prefix |
| `source` | source identifier |
| `fetched_at` | UTC fetch timestamp |

### raw_responses

Raw response storage for traceability.

| column | meaning |
|---|---|
| `source` | source identifier |
| `request_key` | keyword, fund code, or fund-code/date/page key |
| `fetched_at` | UTC fetch timestamp |
| `raw_text` | raw response body |

### stock_holdings

Fund stock holdings, currently populated by AkShare fallback or future structured providers.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code |
| `report_period` | source reporting period or quarter text |
| `stock_code` | 6-digit stock code |
| `stock_name` | stock display name |
| `net_value_ratio` | decimal holding ratio to fund NAV, so `9.83%` is `0.0983` |
| `shares` | source share count, AkShare unit is usually 万股 |
| `market_value` | source holding market value, AkShare unit is usually 万元 |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### fund_profiles

One profile/basic archive row per fund code.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code, primary key |
| `fund_name` | short fund name |
| `full_name` | full legal fund name |
| `fund_type` | source fund type |
| `issue_date` | issue date as `YYYY-MM-DD` when available |
| `establishment_date` | establishment date as `YYYY-MM-DD` when available |
| `asset_size` | latest disclosed net asset size, AkShare/Eastmoney unit is usually 亿元 |
| `asset_size_date` | asset-size cutoff date as `YYYY-MM-DD` |
| `fund_company` | fund management company |
| `custodian` | custodian bank |
| `manager` | manager text from profile page |
| `benchmark` | performance benchmark text |
| `tracking_target` | tracking target text for index-style funds |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### bond_holdings

Fund bond holdings from disclosed portfolio pages.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code |
| `report_period` | source reporting period or quarter text |
| `bond_code` | bond code |
| `bond_name` | bond display name |
| `net_value_ratio` | decimal holding ratio to fund NAV |
| `market_value` | source holding market value, AkShare unit is usually 万元 |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### industry_allocations

Fund industry allocation rows by report period.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code |
| `report_period` | report date such as `2024-12-31` |
| `industry_name` | source industry category |
| `net_value_ratio` | decimal allocation ratio to fund NAV |
| `market_value` | source industry market value |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### fee_structures

Fund trading and fee rules. Text columns preserve non-rate values such as `每笔1000元`.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code |
| `fee_type` | source section, such as `申购费率`, `赎回费率`, or `运作费用` |
| `condition_name` | amount band, holding-period band, or fee item |
| `fee` | decimal rate when the source value is a percentage |
| `fee_text` | original source value |
| `discount_fee` | decimal discounted rate when available |
| `discount_fee_text` | original discounted value |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### dividends

Fund-specific dividend/distribution history. Empty result sets are valid for funds with no records.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code |
| `dividend_date` | rights registration date |
| `ex_dividend_date` | ex-dividend date |
| `dividend_per_share` | cash dividend per share/unit |
| `payment_date` | payment date |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### splits

Fund-specific split/conversion history. Empty result sets are valid for funds with no records.

| column | meaning |
|---|---|
| `fund_code` | 6-digit fund code |
| `split_date` | split or conversion date |
| `split_type` | source split/conversion type |
| `split_ratio` | numeric ratio when disclosed |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### fund_managers

Current fund-manager rows from AkShare/Eastmoney manager list.

| column | meaning |
|---|---|
| `manager_name` | manager name |
| `company` | management company |
| `current_fund_codes` | current fund code for the row |
| `current_funds` | current fund name for the row |
| `tenure_days` | cumulative industry tenure in days |
| `current_aum` | current managed fund asset size, source unit usually 亿元 |
| `best_return` | decimal best current-fund return |
| `source` | provider source identifier |
| `fetched_at` | UTC fetch timestamp |

### sync_runs

Audit log for `sync`.

| column | meaning |
|---|---|
| `id` | autoincrement run id |
| `operation` | operation name, currently `sync` |
| `fund_code` | fund code if applicable |
| `status` | `ok` or `error` |
| `rows_changed` | normalized rows written in the run |
| `started_at` | UTC start timestamp |
| `finished_at` | UTC finish timestamp |
| `message` | error or status text |

### sync_failures

Failure queue for `batch-sync`; use it to rerun provider failures later.

| column | meaning |
|---|---|
| `id` | autoincrement failure id |
| `batch_id` | batch run identifier |
| `operation` | operation name, currently `batch-sync` |
| `fund_code` | failed fund code |
| `provider` | provider selected for the failed run |
| `message` | error message |
| `failed_at` | UTC failure timestamp |

## Sync

`sync` always fetches snapshot and NAV, then writes or refreshes a minimal `funds` row from the profile/snapshot data so `coverage` works without a prior `list` or `search` run.

```bash
python3 scripts/fund_cli.py sync 110022 --provider auto --include-all --report-year 2024 --fee-indicator 申购费率
```

Optional sync flags:

| flag | adds |
|---|---|
| `--include-holdings` | stock holdings |
| `--include-profile` | fund profile/basic archive |
| `--include-bonds` | bond holdings |
| `--include-industries` | industry allocation |
| `--include-fees` | fee structures; repeat `--fee-indicator` to limit sections |
| `--include-distributions` | dividends and splits |
| `--include-managers` | manager rows for the fund |
| `--include-all` | all optional datasets above |

The JSON result includes per-table row counts such as `fund_rows`, `snapshot_rows`, `nav_rows`, `stock_holding_rows`, `bond_holding_rows`, `industry_rows`, `fee_rows`, `dividend_rows`, `split_rows`, `manager_rows`, and the current `coverage` row.

Snapshot and NAV are the hard requirements for a successful fund sync. Optional datasets such as profile, stock/bond holdings, industry allocation, fees, dividends, splits, and managers are soft-fail: if one source is sparse or unstable, the result remains `status: "ok"` and includes `dataset_errors` entries with `dataset` and `message`.

## Batch Sync

`batch-sync` reads fund codes from one or more text files and/or repeated `--code` arguments. Lines may contain comments or extra labels; every 6-digit fund code is extracted and deduplicated in order.

```bash
python3 scripts/fund_cli.py batch-sync --codes-file fund_codes.txt --provider auto --include-all --report-year 2024 --fee-indicator 申购费率
```

By default one hard fund failure does not stop the batch. Failed funds are returned in the JSON result and persisted to `sync_failures` with the `batch_id`. Optional dataset failures do not create `sync_failures`; inspect each successful fund's `dataset_errors` and `coverage` to find partial rows.

Useful options:

| flag | behavior |
|---|---|
| `--codes-file` | read fund codes from a text file; can be repeated |
| `--code` | add one fund code; can be repeated |
| `--batch-id` | set a stable run id for auditing |
| `--stop-on-error` | stop at the first failed fund after recording the failure |
| `--include-all` | run the full fund-base sync for each code |

The JSON result includes `total`, `ok`, `failed`, per-fund `results`, and combined `coverage` rows for funds that have local base rows.

## Export

```bash
python3 scripts/fund_cli.py export funds --format json
python3 scripts/fund_cli.py export nav_history --fund-code 110022 --format csv --output /tmp/110022_nav.csv
python3 scripts/fund_cli.py export stock_holdings --fund-code 110022 --format csv --output /tmp/110022_holdings.csv
python3 scripts/fund_cli.py export fund_profiles --fund-code 110022 --format json
python3 scripts/fund_cli.py export sync_failures --format csv --output /tmp/fund_sync_failures.csv
python3 scripts/fund_cli.py coverage --fund-code 110022
```

Allowed export tables: `funds`, `nav_history`, `snapshots`, `stock_holdings`, `fund_profiles`, `bond_holdings`, `industry_allocations`, `fee_structures`, `dividends`, `splits`, `fund_managers`, `raw_responses`, `sync_runs`, `sync_failures`.

## Provider Sources

The `source` column records the concrete provider endpoint/function:

| source | meaning |
|---|---|
| `eastmoney.search` | Eastmoney fund suggestion API |
| `eastmoney.fundcode_search` | Eastmoney full fund-code list |
| `eastmoney.nav_history` | Eastmoney F10 NAV table |
| `eastmoney.snapshot` | Eastmoney `pingzhongdata` JS |
| `eastmoney.fund_fee_page` | Eastmoney fee page parser for stale AkShare fee aliases |
| `akshare.fund_name_em` | AkShare fund list/search fallback |
| `akshare.fund_open_fund_info_em` | AkShare open-fund NAV fallback |
| `akshare.fund_portfolio_hold_em` | AkShare stock holdings |
| `akshare.fund_overview_em` | AkShare fund profile/basic archive |
| `akshare.fund_portfolio_bond_hold_em` | AkShare bond holdings |
| `akshare.fund_portfolio_industry_allocation_em` | AkShare industry allocation |
| `akshare.fund_open_fund_info_em:分红送配详情` | AkShare fund-specific dividend history |
| `akshare.fund_open_fund_info_em:拆分详情` | AkShare fund-specific split history |
| `akshare.fund_manager_em` | AkShare fund manager list |

## Provider Switching

CLI provider values:

| provider | behavior |
|---|---|
| `auto` | Try configured Investoday first, then free sources. Search/NAV prefer Eastmoney then AkShare; AkShare-backed domain commands prefer AkShare until structured API support is configured. |
| `eastmoney` | Use direct Eastmoney public endpoints only. |
| `akshare` | Use AkShare functions only. Requires AkShare installed in the running Python environment. |
| `investoday` | Use structured API paths when `INVESTDATA_API_KEY` is set. |

Set `FUND_DATA_DISABLE_AKSHARE=1` to force AkShare out of the fallback chain during testing or when it is unstable.
