
## Backfill performance notes (2026-06-01)

- **AkShare is the throughput bottleneck, not CPU.** Each call is a
  1-3s HTTP round-trip to Eastmoney, and AkShare has no per-process
  rate limiter. The server side appears to throttle beyond ~8
  concurrent in-flight calls: 16-way concurrency did **not** speed
  up the backfill, only raised the risk of 5xx errors.
- **Sweet spot: `--concurrency 8 --min-interval-seconds 0.1`.**
  ~2.9s per fund with 2-year NAV and include_all, ~96% success rate.
- **Use `--batch-size 100`** for tighter checkpoints. A 200-fund
  batch can take 5-8 minutes, and any single failure costs the whole
  batch in state. 100-fund batches lose at most 100 to a transient
  issue.
- **Currency funds are pure waste for `include_all`.** They have no
  stock/bond/industry holdings, so 80% of the calls return empty
  + a `dataset_errors` row. Always pass `--exclude-type 货币`.
- **Total runtime: ~21h for the full 25,961 non-currency funds.**
  Run under cron supervision (see mavis cron self
  backfill-monitor) so a crash or 24h restart can resume from
  `data/backfill_state.json`.

## Update (2026-06-01, evening): Eastmoney-only beats AkShare 8x

The original sweet spot was tuned for AkShare. We then measured:

- `fetch_nav_history` over Eastmoney directly: **0.36 s/fund**
- `fetch_nav_history` over AkShare: **>6 s/fund** (server throttled
  the test environment hard after a 16-way burst)

So the actual throughput ceiling is set by **which provider is
being asked to do the call**, not by `--concurrency`. Concretely:

- `--provider eastmoney --concurrency 8` runs the full 25,961-fund
  snapshot+NAV sync in **~90 minutes** with 95% success rate.
- `--provider akshare` is currently unusable for full-coverage
  backfill (limit your batch to 50 funds at concurrency 2 and expect
  a 30% failure rate).
- Tushare (`--provider tushare` with `TUSHARE_TOKEN` set) covers
  the AkShare-only capabilities (profile/holdings/fees/.../managers)
  at ~200 calls/min — pass when you have the token.

When you want the full per-fund base row, run **two passes**:

1. `backfill --provider eastmoney` (snapshot + NAV, fast, ~90 min)
2. `backfill --provider tushare` (profile/holdings/managers/etc.,
   only the missing datasets land because snapshot/NAV are
   idempotent)

The Investoday slot (see `PROVIDERS.md`) is the long-term fix:
apply for an API key, set `INVESTDATA_API_KEY`, and the
`auto` chain will put Investoday first for every capability.

## Data completeness snapshot (2026-06-02, OSS v=2026-06-02-053226)

Snapshot of the local SQLite base after the 2026-06-01→02 backfill
cycle (AkShare bulk + Eastmoney NAV + Investoday `/fund/all`).
Diagnosed from a 5.7 GB local DB and the published OSS bundle at
`oss://fund-data-public-l/fund-data/releases/2026-06-02-053226/`.
Full report: `docs/superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md`.

### Per-table coverage (vs 26,936 fund pool)

| Table | Rows | % funds | Source |
|---|---|---|---|
| `funds` | 26,936 | 100% | Eastmoney `fundcode_search` |
| `fund_profiles` | 26,632 | 98.9% | Investoday `/fund/all` L1 |
| `fund_managers` | 26,645 unique | 98.9% | AkShare `fund_manager_em` |
| `fee_structures` | 80,097 | **100%** | Eastmoney + AkShare |
| `nav_history` | 509,019 | 94.1% | 3-year window, ~16 rows/fund |
| `snapshots` | 25,774 | 95.7% | 380 funds fail (see below) |
| `stock_holdings` | 2,467,012 | 49.0% | AkShare quarterly report |
| `bond_holdings` | 546,502 | 57.1% | AkShare quarterly report |
| `industry_allocations` | 415,444 | 49.2% | AkShare quarterly report |
| `dividends` | 52,347 | 28.6% | naturally sparse |
| `splits` | 1,740 | 2.2% | naturally very sparse |

### Coverage by `fund_type` (after `refresh_fund_type --only-empty`)

`fund_type` was 95.7% empty after the initial `list` rebuild.
`refresh_fund_type` (introduced in PR `fix/fund-type-coverage`,
merged in commit 4bf2ed5) brings it to 99.93%. **Always re-run it
after any `list` rebuild** — `--skip-existing` keeps blanks forever.

| Type | Total | stock | bond | industry | Verdict |
|---|---|---|---|---|---|
| 混合型 | 12,370 | 73% | 58% | 74% | main driver |
| 股票型 | 6,902 | 55% | 27% | 55% | many are index-style |
| 债券型 | 4,528 | 0% | 91% | 0% | no equity by design |
| FOF | 1,232 | 26% | 60% | 26% | holds other funds |
| 货币型 | 975 | 0% | 90% | 0% | no equity by design |
| 指数型 | 732 | 0% | 76% | 0% | 固收 subtype holds bonds |
| QDII | 97 | 7% | 65% | 7% | overseas, data sparse |
| REITs | 80 | 0% | 0% | 0% | no public disclosure |
| (unknown) | 18 | 33% | 17% | 33% | 2024 funds not yet typed |

### Structural gaps — not fixable by backfill

- **2,123 (86% of 2,467) missing-stock 指数型 funds are 2024-2025
  new funds.** AkShare's 持仓 API returns the latest quarterly
  report only; new funds haven't disclosed their first Q3 yet.
  Will populate automatically after the 2024-Q3 / 2024-Annual
  disclosure cycle. Do NOT retry — server has no data.
- **80 REITs + QDII-REITs** don't disclose public holdings
  (different regulatory regime). 0% is correct.
- **975 货币型** + 指数型-固收/纯债 legitimately have no stock
  holdings. The 49% global figure is inflated by them.
- **380 `sync_failures`** are all `snapshot` calls:
  `eastmoney: fund code must contain 6 digits: ''`
  (Eastmoney doesn't carry snapshot data for these 380 funds) +
  `akshare: 'AkshareProvider' object has no attribute 'snapshot'`.
  Not a data issue, just an API surface gap.
- **18 funds with empty `fund_type`** are 2024-2025 new funds that
  `fundcode_search` hasn't typed yet. A second
  `refresh_fund_type --only-empty` won't help until Eastmoney adds
  them; fallback plan is regex on `fund_name`.

### Operational checklist for the next operator

- **Re-run `refresh_fund_type --only-empty`** after any `list`
  rebuild. Eastmoney's `fundcode_search` returns `fund_type` for
  ~95% of rows but leaves the rest empty, and `--skip-existing`
  will keep them empty forever. Sweep every couple of weeks.
- **Don't retry the 380 `sync_failures` blindly.** Real API limits
  (Eastmoney rejects, AkShare lacks the method). The
  `retry_failures.py --provider auto` run is part of the standard
  backfill cycle and the result is captured in `sync_failures`.
- **OSS bundle is on a 1-hour TTL cache.** After `cloud pull`,
  `fund_data` and `fund-mcp` prefer the local cache over
  `FUND_DATA_DB` if unset. Bump with `cloud build-bundle` +
  `ossutil cp -f` after each backfill cycle. (`ossutil cp` without
  `-f` silently fails on overwrite in non-interactive shells.)
- **`_merge_separate_db` schema drift gotcha.** When running
  `akshare_capability_backfill.py --separate-db`, the
  `SELECT *`-based merge can fail with
  `NOT NULL constraint failed: <table>.fetched_at` if the
  `--separate-db` temp DB's `ensure_schema` creates columns in a
  different order than the post-migration main DB. Use explicit
  column lists in the INSERT for `industry_allocations` and
  `fee_structures` until the upstream schema is unified.

### Known follow-up work (PRs to open)

1. `fix(akshare): use explicit column lists in _merge_separate_db` —
   defensive fix for the schema drift above.
2. `refactor(fund_data): align ensure_schema column order with
   post-migration main schema` — root cause fix so the drift never
   returns.
3. `feat(akshare): add AkshareProvider.snapshot` — closes the
   snapshot surface gap so the 380 `sync_failures` can fall back to
   AkShare.
4. `feat(refund): refresh 18 (unknown) fund_type by parsing
   fund_name` — regex on the Chinese fund_name (混合/股票/债券/QDII)
   as a fallback when Eastmoney's index is empty.
