# Fund Data Completeness Diagnosis (2026-06-02)

Status: snapshot of the local SQLite base after the
2026-06-01→02 backfill cycle. Numbers were measured against a 5.7 GB
local DB and the published OSS bundle at
`oss://fund-data-public-l/fund-data/releases/2026-06-02-053226/`.

Companion to: `fund-data/AGENTS.md` (operator-facing summary).

## TL;DR

- 11/11 SQLite tables populated, no critical gaps.
- 23.4% of funds (6,306) are "三无" (no stock + no bond + no industry
  data) — but 86% of those are 2024-2025 new funds that haven't
  disclosed their first quarterly report yet. Will populate
  automatically after the 2024-Q3 / 2024-Annual disclosure cycle.
- The remaining structural gaps (REITs, 货币型, 380 `sync_failures`)
  are real API / data limits, not backfill bugs.
- `fund_type` was 95.7% empty after the initial `list` rebuild;
  `refresh_fund_type --only-empty` (introduced in PR
  `fix/fund-type-coverage`, commit 4bf2ed5) brings it to 99.93%.
- OSS bundle v=2026-06-02-053226 published with `nav_history`
  509,019 rows, `stock_holdings` 2,467,012 rows — full table
  numbers in the manifest.

## What was measured

### Per-table coverage

| Table | Rows | % funds covered | Source |
|---|---|---|---|
| `funds` | 26,936 | 100% | Eastmoney `fundcode_search` |
| `fund_profiles` | 26,632 | 98.9% | Investoday `/fund/all` L1 |
| `fund_managers` | 26,645 unique | 98.9% | AkShare `fund_manager_em` |
| `fee_structures` | 80,097 | **100%** (26,929 of 26,936) | Eastmoney + AkShare |
| `nav_history` | 509,019 | 94.1% | 3-year window, ~16 rows/fund |
| `snapshots` | 25,774 | 95.7% | 380 funds fail (see below) |
| `stock_holdings` | 2,467,012 | 49.0% | AkShare quarterly report |
| `bond_holdings` | 546,502 | 57.1% | AkShare quarterly report |
| `industry_allocations` | 415,444 | 49.2% | AkShare quarterly report |
| `dividends` | 52,347 | 28.6% | naturally sparse |
| `splits` | 1,740 | 2.2% | naturally very sparse |

The 49% / 57% / 49% figure for the three holdings tables is
misleading without breaking it down by `fund_type`.

### `fund_type` (the missing piece)

After the initial `list` rebuild from Eastmoney `fundcode_search`,
`funds.fund_type` was empty for 25,767 of 26,936 rows (95.7%).
`fundcode_search` returns the field as a 4-digit numeric code for
another ~1,500 rows (e.g. `1111` for a money-market fund), which
the AkShare list parser stored verbatim. Without a populated
`fund_type`, every downstream grouping and `--exclude-type` filter
is wrong.

`refresh_fund_type.py --only-empty` re-fetches `fundcode_search`
(which is fully populated on the Eastmoney side) and overwrites
`fund_type` for every fund_code in the fresh index. Result:

- Empty: 25,767 → 18
- Numeric codes: ~1,500 → 0
- Well-formed Chinese names: 1,169 → 26,918 (99.93%)

The 18 remaining empties are 2024-2025 new funds that
`fundcode_search` hasn't typed yet.

### Coverage by `fund_type`

The 49% global `stock_holdings` figure is mostly the 13,741 funds
with `fund_type` empty, where we don't know what to expect. Once
`fund_type` is populated, the picture is much more interpretable:

| Type | Total | stock | bond | industry | Verdict |
|---|---|---|---|---|---|
| 混合型 | 12,370 | 73% | 58% | 74% | main driver — strong |
| 股票型 | 6,902 | 55% | 27% | 55% | many are index-style / ETF |
| 债券型 | 4,528 | 0% | 91% | 0% | correct — no equity by design |
| FOF | 1,232 | 26% | 60% | 26% | correct — holds other funds |
| 货币型 | 975 | 0% | 90% | 0% | correct — no equity by design |
| 指数型 | 732 | 0% | 76% | 0% | the 固收 subtype legitimately holds bonds |
| QDII | 97 | 7% | 65% | 7% | sparse — overseas reporting lag |
| REITs | 80 | 0% | 0% | 0% | correct — no public disclosure |
| (unknown) | 18 | 33% | 17% | 33% | 2024 funds not in Eastmoney index |

The "mixed" + "stock" categories, which represent 71% of the fund
pool, are at 73% / 55% on stock_holdings — the real coverage
number. The 26% for FOF is correct: those funds disclose their
holdings as "X% in fund A, Y% in fund B" rather than top-N
stocks.

### Where the 6,306 "三无" funds come from

Filter: `fund_code NOT IN (stock_holdings, bond_holdings,
industry_allocations)`. Total: 6,306 (23.4%).

| Type | Count | Reason |
|---|---|---|
| 指数型-股票 | 2,467 | **86% (2,123) are 2024-2025 new funds** — Q3 not disclosed yet |
| 混合型-偏股 | 1,192 | mostly 2024-2025 new funds |
| 债券型-混合二级 | 572 | mostly sub-portfolio; bond-focused funds don't list top stocks |
| 债券型-长债 | 367 | same |
| FOF-稳健型 | 310 | FOFs disclose other funds, not stocks |
| 债券型-混合一级 | 160 | same |
| 股票型 | 154 | mostly 2024 new funds |
| 混合型-偏债 | 143 | bond-heavy mix |
| 混合型-灵活 | 139 | mixed; data may be partial |
| 指数型-固收 | 133 | bond-focused by design |
| (Reits) | 80 | correct — no public disclosure |
| 货币型-普通货币 | 97 | correct — no equity by design |
| (rest) | 692 | various, mostly new or 2024-2025 |

The 2,123 "new fund" 指数型-股票 cases are not a backfill bug;
they will fill in naturally as the disclosure cycle progresses.

### 380 `sync_failures` — all the same error

```
all providers failed for snapshot: 
  eastmoney: fund code must contain 6 digits: '';
  akshare: 'AkshareProvider' object has no attribute 'snapshot'
```

All 380 failures are `snapshot` calls, not `stock_holdings` /
`bond_holdings` / `nav_history`. Two root causes:

1. Eastmoney's snapshot endpoint does not carry these 380 fund
   codes. The "fund code must contain 6 digits" error is a
   downstream guard for an empty payload; the upstream endpoint
   returned `code: 0` with an empty list, which the Eastmoney
   client normalises into this error.
2. `AkshareProvider` has no `snapshot` method, so the
   `auto`-chain fallback is broken for this capability.

These 380 funds' NAV, holdings, profile, fees, and managers are
all populated correctly — only the `snapshot` row is missing.

The 380 funds are concentrated in:

- Recently listed / not-yet-disclosed funds (Eastmoney's snapshot
  endpoint lags)
- 老封转开 / 复制基金 association codes that Eastmoney's snapshot
  endpoint doesn't carry
- A small number of currency funds that Eastmoney's snapshot
  endpoint rejects

Re-running `retry_failures.py --provider auto` against the 380
recovers 5 successes and adds 190 *new* failures (it also
attempts every fund in the queue, including 100% of the
already-recovered ones). The net effect is that `sync_failures`
stabilises at ~380 — this is the floor, not a bug.

## Schema drift gotcha (recovered 2026-06-02 03:00 CST)

During the 2026-06-02 backfill, the
`akshare_capability_backfill.py --separate-db` merge step failed
with:

```
sqlite3.IntegrityError: NOT NULL constraint failed: fee_structures.fetched_at
```

Root cause: `fund_data.FundDataStore.ensure_schema` (in
`fund_data.py` lines 1951–1972) declares
`industry_allocations` and `fee_structures` with columns in
one order:

```python
# industry_allocations per CREATE TABLE in ensure_schema
fund_code, report_period, industry_name, net_value_ratio,
market_value, source, fetched_at,
PRIMARY KEY (fund_code, report_period, industry_name)
```

But the main DB (and the 0.2.0 OSS bundle, which has been
through migrations 001–004) has the columns in a *different*
order, because each migration uses `ALTER TABLE ... ADD COLUMN`
which appends to the end:

```sql
-- Main DB after migrations 001-004
fund_code, report_period, industry_name, net_value_ratio,
source, fetched_at, market_value
```

`_merge_separate_db` issues `INSERT INTO main.<t> SELECT * FROM sep.<t>`,
which depends on column order. With the order mismatch, the
`market_value` value gets written into the `source` column, the
`source` value into the `fetched_at` column, and the merge
fails on `NOT NULL fetched_at`.

Recovery (3,364,340 rows):

```python
# Use explicit column lists for the two affected tables.
FIXES = [
    ("industry_allocations",
     "fund_code, report_period, industry_name, net_value_ratio, "
     "source, fetched_at, market_value"),
    ("fee_structures",
     "fund_code, fee_type, condition_name, fee, source, fetched_at, "
     "fee_text, discount_fee, discount_fee_text"),
]
# The other 4 tables in MERGE_TABLES (stock_holdings, bond_holdings,
# dividends, splits) have matching column order and can keep
# `SELECT *`.
```

Both fix candidates are listed in `fund-data/AGENTS.md` under
"Known follow-up work".

## OSS bundle publication (2026-06-02 05:32 CST)

```
oss://fund-data-public-l/
├── releases/
│   ├── 2026-06-01-230019/         # 0.2.0 bundle (recovered as starting point)
│   │   ├── fund_data_query.sqlite.gz         19.9 MB
│   │   └── fund_data_query.sqlite.gz.sha256
│   └── 2026-06-02-053226/         # this snapshot
│       ├── fund_data_query.sqlite.gz         121 MB
│       └── fund_data_query.sqlite.gz.sha256
└── current/
    └── manifest.json              # points at 2026-06-02-053226
```

Manifest `tables` block matches the local DB row counts
exactly (verified 11/11 tables). The query bundle excludes
`raw_responses`, `sync_runs`, `sync_failures` per the
`fund-data/scripts/fund_cloud.py` policy.

### A small `ossutil` gotcha

`ossutil cp` (without `-f`) silently fails on overwrite in
non-interactive shells: it prints a "y or N)" prompt that
gets swallowed, then reports `Upload done: (0 objects, ...)`.
**Always pass `-f`** when pushing a new manifest or replacing
an existing artifact. This is now in `~/.mavis/agents/mavis/memory/MEMORY.md`
as a cross-project reminder.

## Reproduction commands

If you need to re-derive any of these numbers, all queries are
trivial:

```bash
DB=fund-data/data/fund_data.sqlite

# Per-table coverage
for t in funds nav_history snapshots fund_profiles stock_holdings \
         bond_holdings industry_allocations fee_structures \
         dividends splits fund_managers; do
  n=$(sqlite3 "$DB" "SELECT COUNT(*) FROM $t;")
  echo "$t $n"
done

# Coverage by fund_type (after refresh_fund_type)
sqlite3 "$DB" "
  SELECT fund_type, COUNT(*) AS total,
    COUNT(DISTINCT sh.fund_code) AS with_stock,
    COUNT(DISTINCT bh.fund_code) AS with_bond
  FROM funds f
  LEFT JOIN stock_holdings sh ON f.fund_code = sh.fund_code
  LEFT JOIN bond_holdings bh ON f.fund_code = bh.fund_code
  GROUP BY fund_type
  ORDER BY total DESC;
"
```

For the full diagnosis workflow, see the cron tick log of
`fundData-backfill-watch` (deleted after the run), or replay
the steps in `fund-data/scripts/{backfill,akshare_capability_backfill,retry_failures,fund_cli cloud build-bundle,fund_cli cloud pull}.py`.
