
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
