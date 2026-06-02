# Fund Data Coverage Summary

> Last refreshed: 2026-06-02 17:38 Asia/Shanghai.
> Snapshot source: latest successfully pulled cloud query bundle
> `2026-06-02-1701`.
> Bundle path:
> `/Users/xiongjiali/.cache/fund-data/releases/2026-06-02-1701/fund_data_query.sqlite`.
> Manifest URL:
> `https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json`.

This document is the compact, current coverage map for the
`fund-data` project. It answers four questions:

1. Which fund datasets are available locally?
2. What is missing or naturally empty?
3. What time window does each dataset cover?
4. Which upstream source produced each dataset?

The cloud query bundle is the data surface used by CLI/MCP/agent
queries when `FUND_DATA_DB` is unset. It contains business tables only;
local audit tables are discussed separately at the end.

## Snapshot Card

| Item | Current value |
|---|---:|
| Fund universe | 26,953 funds |
| Query bundle size | 721,027,072 bytes uncompressed / 119,329,974 bytes gzip |
| Business tables | 11 tables |
| Fully covered base profile | `funds` 100%, `fund_profiles` 100% |
| Strong coverage | `snapshots` 99.93%, `fee_structures` 99.91%, `fund_managers` 98.84% fund-resolved, `nav_history` 97.58% |
| Sparse / structural coverage | stock holdings 48.96%, bond holdings 57.02%, industry allocation 49.15%, dividends 28.58%, splits 2.19% |
| Last successful cloud pull | 2026-06-02T09:38:40+00:00 |

The remote manifest later advertised `2026-06-02T093613Z`, but
`cloud pull` returned HTTP 404 for that release artifact. Treat
`2026-06-02-1701` as the latest verified-good bundle until the
manifest and release files agree again.

## What We Have

| Dataset | Table | Rows | Fund coverage | Data time window | Fetch time window | Source |
|---|---:|---:|---:|---|---|---|
| Fund universe / latest NAV card | `funds` | 26,953 | 26,953 / 26,953 = 100.00% | `nav_date`: 2026-06-01 | 2026-06-01T18:25:21Z to 2026-06-02T05:50:47Z | `eastmoney.fundcode_search` 26,934 rows; `eastmoney.search` 10; `eastmoney.snapshot` 9 |
| Fund profile / company / benchmark | `fund_profiles` | 26,953 | 26,953 / 26,953 = 100.00% | `establishment_date`: 2001-09-21 to 2026-06-01; `asset_size_date`: 2018-09-30 to 2026-06-01 | 2026-06-01T06:04:11Z to 2026-06-02T08:27:14Z | `akshare.fund_overview_em` 26,945; `investoday.fund_all` 8 |
| Historical NAV | `nav_history` | 528,083 | 26,300 / 26,953 = 97.58% | `nav_date`: 2018-05-29 to 2026-06-01 | 2026-06-01T06:04:12Z to 2026-06-02T04:27:10Z | `eastmoney.nav_history` 527,211; `akshare.fund_open_fund_info_em` 872 |
| Snapshot returns / min purchase / stock codes | `snapshots` | 26,935 | 26,935 / 26,953 = 99.93% | Snapshot payload as fetched | 2026-06-01T17:11:06Z to 2026-06-02T04:27:09Z | `eastmoney.snapshot` 26,935 |
| Stock holdings | `stock_holdings` | 2,467,012 | 13,195 / 26,953 = 48.96% | `report_period`: 2024-03-31 to 2025-12-31 | 2026-06-01T06:04:12Z to 2026-06-01T21:30:53Z | `akshare.fund_portfolio_hold_em` |
| Bond holdings | `bond_holdings` | 546,502 | 15,369 / 26,953 = 57.02% | `report_period`: 2024-03-31 to 2025-12-31 | 2026-06-01T06:04:13Z to 2026-06-01T21:30:53Z | `akshare.fund_portfolio_bond_hold_em` |
| Industry allocation | `industry_allocations` | 415,444 | 13,247 / 26,953 = 49.15% | `report_period`: 2024-03-05 to 2025-12-31 | 2026-06-01T06:04:13Z to 2026-06-01T21:30:53Z | `akshare.fund_portfolio_industry_allocation_em` |
| Fee structures | `fee_structures` | 80,097 | 26,929 / 26,953 = 99.91% | Fee table as fetched | 2026-06-01T06:04:15Z to 2026-06-01T21:30:56Z | `eastmoney.fund_fee_page` 58,607; `akshare.fee_fallback:etf_no_data` 13,364; `akshare.fund_fee_em` 8,126 |
| Dividends | `dividends` | 52,347 | 7,702 / 26,953 = 28.58% | `dividend_date`: 2002-04-19 to 2026-06-03 | 2026-06-01T06:04:17Z to 2026-06-01T21:30:56Z | `akshare.fund_open_fund_info_em:分红送配详情` |
| Splits / conversions | `splits` | 1,740 | 589 / 26,953 = 2.19% | `split_date`: 2005-02-04 to 2026-06-10 | 2026-06-01T06:04:17Z to 2026-06-01T18:57:16Z | `akshare.fund_open_fund_info_em:拆分详情` |
| Fund managers | `fund_managers` | 34,654 manager rows | 26,641 / 26,953 = 98.84% current-fund coverage | Current manager roster as fetched | 2026-06-01T17:16:21Z to 2026-06-01T21:31:08Z | `akshare.fund_manager_em` |

Notes:

- `fund_profiles` is currently complete in the verified cloud bundle.
  Most rows are from AkShare's Eastmoney-backed F10 profile scraper;
  only 8 rows carry `investoday.fund_all` as the last source in this
  bundle.
- `fund_managers.current_fund_codes` is manager-centric CSV text, not
  a normalized join table. The table contains 26,645 unique manager-side
  fund codes, of which 26,641 resolve to the current `funds` universe.
- Dividend and split maximum dates are provider-announced event dates.
  They can be later than the bundle pull date when the upstream source
  publishes future scheduled actions.

## What Is Missing

### Actionable Or Near-Actionable Gaps

| Gap | Missing funds | Main shape | Recommended next action |
|---|---:|---|---|
| `nav_history` | 653 | Mostly back-end share classes and sparse/new products. Examples include `000002`, `000012`, `000108`, `000140`, `000154`. | Keep in retry queue only when the provider changes; do not treat every missing row as a nightly failure. |
| `snapshots` | 18 | 17 untyped new funds plus 1 money-market fund. | Re-run after Eastmoney updates `fundcode_search` / snapshot pages; fallback can infer type from fund name. |
| `fee_structures` | 24 | 17 untyped new funds plus 7 money-market funds. | Retry fee page scrape periodically; otherwise classify as small tail. |
| `fund_managers` | 312 current-fund rows unresolved | Mostly back-end share classes, untyped new funds, and a small number of regular funds. | Add a fund-centric materialized table or view so "fund -> manager" is O(1), then backfill unresolved rows. |
| Query bundle freshness | Installed bundle `2026-06-02-1701`; remote manifest advertises `2026-06-02T093613Z` but the artifact 404s. | Publish order / OSS consistency issue. | Upload release archive and sha256 before publishing `current/manifest.json`; keep manifest publish last. |

### Structural Or Naturally Sparse Data

These are not automatically bugs:

- **Stock holdings / industry allocation** are missing for 13.7k funds.
  Large chunks are pure bond, money-market, REIT, QDII, FOF, or
  back-end share classes where public equity/industry disclosures are
  absent or not applicable.
- **Bond holdings** are missing for 11.6k funds. Many equity/index
  products legitimately carry no bond table.
- **Dividends** cover only 28.58% of funds because most funds have never
  paid a dividend.
- **Splits** cover only 2.19% of funds because fund split/conversion
  events are rare and concentrated in older products.
- **REITs** show 0% stock/bond/industry coverage in the public AkShare
  path; their disclosure regime is different.

## Coverage By Major Fund Type

| Fund type | Total | Stock holdings | Bond holdings | Industry allocation | Interpretation |
|---|---:|---:|---:|---:|---|
| 混合型-偏股 | 5,561 | 4,344 | 2,265 | 4,369 | Equity/industry mostly useful; bond optional. |
| 指数型-股票 | 5,345 | 2,602 | 1,270 | 2,606 | Many missing rows are index share classes or sparse disclosures. |
| 债券型-长债 | 3,520 | 0 | 3,154 | 0 | No stock/industry by design; bond is the useful table. |
| 混合型-灵活 | 2,397 | 2,189 | 1,508 | 2,203 | Strong equity/industry coverage. |
| 债券型-混合二级 | 1,779 | 1,062 | 1,200 | 1,064 | Both equity and bond can matter. |
| 混合型-偏债 | 1,397 | 1,186 | 1,249 | 1,184 | Good multi-asset coverage. |
| 股票型 | 1,105 | 945 | 507 | 951 | Equity/industry are the main tables. |
| 债券型-中短债 | 1,009 | 0 | 947 | 0 | No stock/industry by design. |
| 货币型-普通货币 | 967 | 0 | 870 | 0 | No stock/industry by design. |
| 债券型-混合一级 | 949 | 64 | 789 | 64 | Mostly bond-oriented. |
| 指数型-固收 | 670 | 0 | 537 | 0 | Fixed-income index products; stock/industry absent by design. |
| FOF-稳健型 | 654 | 142 | 337 | 143 | Holds other funds; direct stock/bond rows are partial. |
| Reits | 80 | 0 | 0 | 0 | Public AkShare fund portfolio path does not cover REIT disclosure. |

## Local Full DB Audit Tables

The cloud query bundle intentionally excludes audit tables. The local
full DB at `fund-data/data/fund_data.sqlite` keeps them:

| Audit table | Rows | Time window | Notes |
|---|---:|---|---|
| `raw_responses` | 51,962 | 2026-06-01T17:11:06Z to 2026-06-02T05:50:47Z | Raw upstream payloads for audit / parser replay. |
| `sync_runs` | 26,351 | 2026-06-01T17:11:06Z to 2026-06-02T08:08:48Z | Per-sync execution log. |
| `sync_failures` | 8 | 2026-06-02T08:17:57Z to 2026-06-02T08:18:14Z | All 8 are `fund_profile_backfill.profile` failures from `akshare.fund_overview_em` caused by Eastmoney F10 SSL EOF errors for `025952`, `025953`, `025967`, `025968`, `025969`, `025970`, `025971`, `025972`. |

These 8 local full-DB failures are not present in the verified cloud
query bundle because the bundle contains business tables only and does
not ship the failure queue.

## Regeneration Commands

Use these commands to refresh the snapshot and recompute the same
numbers:

```bash
python3 fund-data/scripts/fund_cli.py cloud pull
python3 fund-data/scripts/fund_cli.py cloud status \
  --manifest-url https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json

DB=$(PYTHONPATH=fund-data python3 - <<'PY'
from scripts import fund_data
print(fund_data.default_db_path())
PY
)

sqlite3 "$DB" "select count(*) from funds;"
python3 fund-data/scripts/fund_cli.py coverage-report --limit 20
```

For operator-only audit tables, query the full local DB directly:

```bash
sqlite3 fund-data/data/fund_data.sqlite \
  "select operation, provider, count(*) from sync_failures group by operation, provider;"
```
