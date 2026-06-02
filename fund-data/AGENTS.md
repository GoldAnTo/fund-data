
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

## Gotchas worth remembering before the next backfill

- **`funds.fund_type` is empty for 84% of the universe out of
  `fetch_fund_list(provider='auto'|'akshare')`.** AkShare's
  `ak.fund_name_em()` returns the `基金类型` column as a numeric
  category code (``1111``/``1211``/...) or blank for most funds,
  and `FundDataStore.upsert_funds` is a wholesale column
  overwrite. Pull the real fund_type from the Eastmoney
  `fundcode_search.js` index instead — the same row that backs
  search, which has ``[code, pinyin, name, fund_type, pinyin_full]``
  with the real category for every fund. The project ships
  `scripts/refresh_fund_type.py` for this; it writes via direct
  SQL so the populated `company` / `manager` / `fund_name`
  columns are not clobbered. See commit `2ec363b`.

- **Back-end share classes (``000002`` / ``000012`` / ``000108``
  / ...) have a stub Eastmoney snapshot page.** The body is
  effectively empty: `fS_code` / `fS_name` / every returns field
  all blank. The previous `parse_snapshot` raised
  `ValueError("fund code must contain 6 digits: ''")`, which
  surfaced in `sync_failures` as a confusing 6-digit-regex
  failure. Now `parse_snapshot` returns `None` (the provider
  layer turns that into an empty dict so the provider chain
  does not raise) and `sync_fund` skips the snapshot row
  without aborting the whole sync. 241 funds were sitting in
  `sync_failures` because of this; the test
  `test_batch_sync_funds_does_not_record_back_end_share_as_failure`
  is the regression guard. See commit `501977b`.

- **`akshare_capability_backfill.py:216` previously called
  `AkshareProvider.fee_structures(code, indicator=...)` with the
  wrong kwarg name** (the method only accepts `indicators=[...]`).
  Every fund tripped `TypeError` and the bulk runner reported
  "fee_structures failed for 26936 funds" with zero rows. The
  result was `fee_structures` stuck at ~700 funds for the whole
  26,936-fund run. The fix lives in the bulk runner; if you ever
  need to seed fees from scratch,
  `scripts/fee_only_backfill.py` is the dedicated page-scrape
  runner (eastmoney-only, ~0.27 s/fund, 14 min for the full
  universe). See commit `2ec363b`.

- **`akshare_capability_backfill.py --skip-existing` had the
  inclusion direction inverted** (AND of NOT EXISTS instead of
  OR). A fund that lost a single capability to a partial
  backfill such as the fee TypeError above was skipped, so the
  resumed run touched no rows for it. After the fix the worker
  picks up exactly the funds missing at least one of the target
  rows. See commit `df71a14`.
