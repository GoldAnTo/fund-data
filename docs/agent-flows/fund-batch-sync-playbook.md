# Fund Batch Sync Playbook

> **Last updated:** 2026-06-02
> **Audience:** Anyone — human or AI — who gets asked "how does
> `fund-data` sync a watchlist / 100 funds / 27k funds?" or "why
> is the backfill so slow / why did it fail / why did it write to
> the wrong DB?". This is the **answer script** for the
> long-running pipeline. Pair with
> [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md)
> for diagrams and code anchors.
>
> **Use it when:**
> - Onboarding a new contributor or agent to the data plane.
> - Reviewing a PR that touches `fund_data.sync_fund`,
>   `fund_data.batch_sync_funds`, `scripts/backfill.py`, or any
>   provider's per-fund fetch.
> - Debugging a report of "backfill failed" or "data is in the
>   wrong DB" or "the nightly cron hung at 0% CPU".
> - Fielding a question about whether to use the backfill
>   runner, direct `batch_sync_funds`, or a one-off `fund_sync`.
> - Estimating runtime for a new dataset combination.
>
> **Do NOT use it when:**
> - The question is about a single search → use
>   [`fund-search-playbook.md`](./fund-search-playbook.md).
> - The question is about a specific provider's quirks → use
>   [`fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md).
> - The question is "how do I install the skill" → use
>   [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md).

---

## TL;DR (90-second answer)

A batch sync in `fund-data` is **the same four layers as a single
search, plus a long-running runner on top** that owns state, batch
boundaries, and lock-retry:

1. **Entry point** — `fund_batch_sync` MCP tool, `fund-backfill`
   CLI, or direct Python import of `batch_sync_funds` /
   `backfill`.
2. **Backfill runner** (optional) — `backfill.py` adds
   `fund_type` filtering, state persistence
   (`backfill_state.json`), batch grouping, and "database is
   locked" retry.
3. **Cloud bootstrap** — same as search: `ensure_project_bundle`
   decides whether to install the OSS query bundle.
4. **DB path resolution** — same as search: `default_db_path()`
   collapses env vars and cache into one concrete file.
5. **Batch scheduler** — `batch_sync_funds` runs N parallel
   `sync_fund` calls (or serial when `concurrency=1`).
6. **Per-fund pipeline** — `sync_fund` walks the capability
   ladder: snapshot → profile → fund row → NAV → optional
   datasets (holdings, bonds, industries, fees, distributions,
   managers), then writes a `sync_runs` audit row.
7. **Provider chain (per capability)** — same shape as search;
   each capability picks its own chain.

The key **differences from search** are: (a) `sync_fund` has a
**two-tier failure policy** — snapshot and NAV are hard
failures, optional datasets are soft; (b) `backfill.py` writes
a JSON state file that survives process death; (c) the
`ThreadPoolExecutor` is bounded by `concurrency` and throttled
by a thread-safe `_RateLimiter`.

---

## The full answer template (use this skeleton)

When asked "how does `fund-data` do a batch sync?", structure
the answer in **six paragraphs**, one per layer. Order matters
— it matches the runtime call order.

### Paragraph 1 — Entry point

> The user can enter through three surfaces: the MCP stdio
> server (`fund_batch_sync` and `fund_sync` tools), the
> `fund-batch-sync` / `fund-backfill` CLI subcommands, or
> direct Python imports of `fund_data.batch_sync_funds` or
> `fund_data.backfill`. The MCP path triggers the cloud
> bootstrap; the backfill CLI bypasses it (the operator is
> expected to have set `FUND_DATA_DB`). `fund-backfill` and
> `fund_data.backfill` are the same function — the backfill
> runner is a thin wrapper that adds state, fund_type
> filtering, and lock retry around `batch_sync_funds`.

### Paragraph 2 — Backfill runner (only on the `backfill.py` path)

> When the entry point is `backfill.py`, the runner first
> reads `backfill_state.json` to know which fund codes are
> already done, then loads the full fund list from the local
> SQLite, filters by `--include-type` / `--exclude-type`, and
> groups the remaining codes by their include-flag signature
> (currency funds skip optional datasets, mixed funds do
> not). Each group is sliced into `batch_size` chunks, and
> each chunk is passed to `batch_sync_funds` inside a
> `LOCK_RETRY_ATTEMPTS=3` retry wrapper. The state file is
> updated after every successful batch, so a crash at batch
> 7 resumes at batch 8. `--reset` discards the state for a
> from-scratch run.

### Paragraph 3 — Cloud bootstrap and DB path resolution

> `batch_sync_funds` (and the MCP path) then runs the same
> `ensure_project_bundle` → `default_db_path` sequence as
> search. A successful bootstrap returns an OSS or cache DB;
> a failed bootstrap falls through to the local fallback
> `fund-data/data/fund_data.sqlite`. **The bootstrap is
> silent on failure** — the live providers still get a
> chance to serve — but a long-running backfill that
> accidentally writes to the cache DB instead of the on-disk
> DB will diverge from `doctor.py`'s report. Always set
> `FUND_DATA_DB` explicitly for backfill runs.

### Paragraph 4 — Batch scheduler

> With the DB path resolved, `batch_sync_funds` runs the
> per-fund pipeline. With `concurrency=1` it is a serial
> loop; with `concurrency>1` it is a `ThreadPoolExecutor`
> using `as_completed` for result collection. `stop_on_error`
> short-circuits the rest of the batch on the first hard
> failure (rarely used; backfill prefers continue + retry).
> `min_interval_seconds` defaults to `0.25` for concurrent
> and `1.0` for serial — these are the rate-limit budgets
> that did not produce 5xx errors in the team's measurement
> runs.

### Paragraph 5 — Per-fund pipeline

> `sync_fund` walks the capability ladder. The two **hard
> failure** steps are `fetch_snapshot` and `fetch_nav_history`
> — if either raises, the fund moves to `failed_codes` and
> the rest of its datasets are not requested. The seven
> **soft failure** steps are `fetch_profile`, `fetch_*holdings`,
> `fetch_industry_allocations`, `fetch_fee_structures`,
> `fetch_dividends`, `fetch_splits`, and `fetch_fund_managers`
> — a hard error on any of these becomes a `dataset_errors`
> entry and the fund still gets `status: "ok"`. Snapshot
> pages that come back empty (back-end share classes like
> `000002`) are soft-skipped, not failed. The fund row is
> upserted after the profile fetch (so `fund_name` /
> `fund_type` / `company` / `manager` come from the profile
> payload, not the snapshot).

### Paragraph 6 — Persistence and state

> Each `fetch_*` call upserts into its target table
> (PK on `fund_code` or `(fund_code, report_period, ...)`)
> and appends the raw provider payload to `raw_responses`.
> `sync_fund` writes one row to `sync_runs` with the per-fund
> outcome. A hard failure additionally writes a row to
> `sync_failures` — this is the live queue for
> `retry_failures.py`. The backfill runner additionally
> appends the fund code to `backfill_state.failed_codes` —
> this is the snapshot used for resume. **The two failure
> tracks drift** if both `backfill.py` and `retry_failures.py`
> run interleaved; an agent must read both to get the
> complete picture.

---

## The 14 most-asked questions (with full answers)

These are the questions that come up the most in onboarding,
support, and PR review. **Answer them in the order they appear
here, with the same level of detail** — these are the
explanations the team has settled on after multiple rounds of
"but why?".

### Q1. Why does `backfill` write a `backfill_state.json` while `batch_sync_funds` does not?

- **`backfill.py` is for "finish all 27k funds" runs.** A run
  like that takes 6-21 hours. Without a state file, a
  process crash at fund 26000 means redoing 0-25999 on
  restart. With the state file, a restart reads
  `completed_codes` and skips them.
- **`batch_sync_funds` is for "sync these N specific funds"
  one-shot calls.** It is sized for the watchlist /
  follow-up / on-demand pull case where N is small enough
  that redoing on failure is cheaper than maintaining
  state. A 100-fund pull that crashes at fund 80 takes
  ~2 minutes to redo; a state file is more complexity than
  it is worth.
- **The split is intentional, not a TODO.** If you want
  state-managed behaviour for a 100-fund pull, wrap it in
  your own loop and persist your own state. The
  `backfill_state.json` shape is the reference.

### Q2. Why are snapshot and NAV hard failures while profile / holdings are soft failures?

- **Snapshot and NAV are the data anchors.** Every other
  table joins back to one of them. A `funds` row without a
  snapshot is just a name; a `stock_holdings` row without
  the fund it belongs to is junk. If we soft-failed
  snapshot, we would still write the fund row and the
  holdings row, and downstream queries would have to
  filter out "funds with no snapshot" everywhere. Hard
  failure is the explicit "we do not have the data anchor
  for this fund" signal.
- **Profile / holdings / fees / distributions are
  enrichment.** A fund with `status: "ok"` + empty
  `dataset_errors` profile but populated holdings is
  usable: you can still look up the fund, get NAV
  history, get holdings, and skip the profile. A fund
  with populated profile but empty holdings is also
  usable: the user gets the description and the fees.
  The asymmetry matches the user-facing question shape.
- **The `dataset_errors` channel is the audit trail.** A
  partial success is reported as `status: "ok"` +
  `len(dataset_errors) > 0`. An agent that wants to know
  which funds have which gaps can inspect the channel
  and act (re-run with a different provider, or accept
  the gap).

### Q3. Why does `backfill` skip optional datasets for 货币型 funds by default?

- **货币 funds have no stock / bond / industry holdings by
  regulatory design.** They are money-market funds. AkShare
  and Eastmoney both return `[]` for these endpoints, plus
  a `dataset_errors` row that costs one rate-limit slot.
- **975 货币 funds × 5 empty datasets × 2-3 s per call =
  2.5-4 hours of wasted rate-limit budget.** On a 21-hour
  backfill, that is 12-20 % of the wall time for calls
  that return nothing.
- **`--no-skip-currency` exists for the case where you
  trust a different provider to fill the gap** (e.g. a
  paid Investoday L2 endpoint that returns 货币 dividend
  history). The default is "be cheap"; the override is
  "be exhaustive".

### Q4. Why is `--provider eastmoney --concurrency 8` the fastest path, and why doesn't `--provider akshare --concurrency 8` work?

- **`fetch_nav_history` over Eastmoney is 0.36 s/fund.**
  `fetch_nav_history` over AkShare is > 6 s/fund. The
  16× difference is **the upstream throttling behaviour,
  not the per-call cost** — the team's measurement run hit
  the AkShare throttle at 16-way concurrency and the
  server started returning 429s and 5xx. Eastmoney's
  upstream is a different load-balancer with a more
  permissive throttle.
- **The right concurrency is set by the upstream, not by
  the team's hardware.** Beyond ~8 in-flight AkShare
  calls, the throughput *decreases* because the client
  spends more time waiting on 5xx retries than it saves
  on parallelism.
- **The recommended split is a two-pass backfill:**
  `backfill --provider eastmoney` for snapshot + NAV
  (~90 min), then `backfill --provider tushare` (with
  token) for the AkShare-only capabilities (~3-4 hours).
  Pass 1 is fast because Eastmoney is the right tool;
  pass 2 is fast because Tushare is the right tool.

### Q5. Why does lock retry give up after 3 attempts?

- **The lock retries are for "another writer is finishing
  its WAL commit, wait a beat".** A 2-3 second wait is
  usually enough. A 4-8 second wait covers the tail.
  Beyond that, the lock holder is stuck on a real
  problem (deadlock, network partition, a hung
  subprocess), and waiting longer makes the situation
  worse, not better.
- **Aborting is the right policy because the backfill
  state file is already updated.** The next invocation
  will read `backfill_state.json`, see the incomplete
  batch, and re-process those codes. Aborting fast
  exposes the failure quickly and the operator can
  diagnose.
- **A 9-second wait is cheaper than re-running a
  6-hour backfill** — that is the *floor*. Three
  attempts at 2/4/8 s = up to 14 s. If we are not
  done in 14 s, the lock is held by something that
  will not release in a sane amount of time.

### Q6. Why is `batch-size 100` safer than `batch-size 500`?

- **The state file updates after every batch.** A 200-fund
  batch takes 5-8 minutes; if it fails at fund 199, the
  state file does not see any of the 199 successes. A
  100-fund batch takes 2-4 minutes; the worst case is
  losing 100 funds' worth of work.
- **The failure domain is the whole batch.** A transient
  HTTP blip, a SQLite lock, an OOM kill — any of these
  takes the whole batch with it. Smaller batches mean
  smaller failure domains.
- **The team measured ~96 % success rate at 100-fund
  batches and ~88 % at 500-fund batches** (the gap is
  not just lost time, it is lost rows that need a
  re-run). The default 100 is a calibrated number, not
  an arbitrary one.

### Q7. Why does `default_db_path()` prefer the OSS cache, and why does that conflict with `doctor.py`?

- **`default_db_path()` is the agent-friendly path.** An
  OpenClaw daemon that pulled the OSS bundle wants to
  read from that bundle. Putting the cache ahead of the
  on-disk DB in the precedence list means the daemon
  does not have to think about which DB it is hitting.
- **`doctor.py` is the operator-friendly path.** The
  operator wants to know "is the production DB healthy?",
  which is the on-disk DB, not whatever cache happens
  to be pulled.
- **The two are intentionally separate** — `doctor.py`
  does not walk the cache, and the cache is not
  considered production. A long-running backfill that
  uses `default_db_path()` will write to whichever DB
  wins the precedence; `doctor.py` will report on
  whichever DB it knows about. If they diverge, the
  backfill is writing to the cache and `doctor.py` is
  reporting on the on-disk DB.
- **The fix is to set `FUND_DATA_DB` explicitly for any
  backfill that wants to land in production.** The CI
  workflow does this; local CLI runs without that var
  land in the cache. The trade-off is documented in
  `fund-data/AGENTS.md` §Long-running pitfalls.

### Q8. Why are `--include-type` / `--exclude-type` substring matches, not exact matches?

- **Fund type strings are hierarchical.** A real row
  might be `指数型-股票` or `指数型-固收`. The team
  wanted `--exclude-type 货币` to match both
  `货币型` and `指数型-货币` without listing them
  separately. Substring match is the only way to do
  that with a single flag.
- **The cost is false positives.** `--include-type 股票`
  matches `股票型`, `指数型-股票`, and `股票指数`. The
  team judged that the convenience of one flag per
  category outweighed the risk of catching more than
  intended — the false positives are easy to spot in
  the log.
- **The alternative is a list-of-patterns API.** The
  team chose not to ship it because the substring
  match is the 80/20 design.

### Q9. Why does `refresh_fund_type` go around `upsert_funds` with direct SQL?

- **`upsert_funds` is whole-row replacement.** When
  AkShare's `fund_name_em()` returns a row with an empty
  `fund_type` column, `upsert_funds` writes that empty
  string over the value that a previous source (e.g.
  the Eastmoney `fundcode_search` index) had populated.
  The empty value sticks, the `fund_type` filter
  breaks, the operator gets paged.
- **The same index that backs `search` carries a
  better `fund_type` for every fund.** Pulling that
  index and writing the `fund_type` column via
  `UPDATE funds SET fund_type = ? WHERE fund_code = ?`
  is the surgical fix.
- **The 18 funds with empty `fund_type` in the
  Eastmoney index get a regex fallback** that infers
  the type from the Chinese `fund_name` (e.g. `FOF`,
  `QDII`, `ETF`). The fallback is a separate pass
  with its own `sync_runs` audit row.

### Q10. Why are there two failure tracks (`backfill_state.failed_codes` and `sync_failures`)?

- **The state file is a snapshot; the table is live.**
  `backfill_state.failed_codes` is written by
  `backfill.py` at the end of each batch. It is the
  resume marker. If the process crashes between
  batches, the state file is consistent with what
  actually completed.
- **`sync_failures` is written by `record_sync_failure`
  inside `batch_sync_funds`.** It is the live queue
  for `retry_failures.py`. Every hard failure lands
  here, even those that `backfill.py` then copies to
  its state file.
- **The two drift when both `backfill.py` and
  `retry_failures.py` run interleaved.** A retry that
  succeeds in `retry_failures.py` writes nothing to
  the state file; a backfill that resumes will see
  the code in `failed_codes` and re-fail it. The
  drift is small (the next `backfill` invocation
  re-records the failure), but it is real.
- **The team tracks this as a known ops gotcha, not a
  bug.** A future fix would have `backfill.py` read
  `sync_failures` directly instead of its own state
  field, eliminating the duplication. Until that
  lands, the operator must know both exist.

### Q11. Why does the backfill's `_resolve_include_flags` group funds by their flag set?

- **Funds of the same type share a flag set.** All
  货币型 funds should skip optional datasets; all
  混合型 funds should request all of them. Grouping
  by flag set means the same flag dict is passed to
  every fund in a group, which makes the per-fund
  work in `batch_sync_funds` identical.
- **The optimisation is small but free.** The
  grouping is O(N) over the fund list; the
  per-fund fetch is unchanged. The win is that the
  log output is cleaner (one batch report per group
  rather than per fund).
- **The grouping is a code-readability win, not a
  performance win.** A future refactor could move
  the flag resolution into `sync_fund` itself and
  the runtime would not change. The current
  grouping is the convenience layer for the operator
  who reads the log.

### Q12. Why are the macOS proxy / IPv6 pitfalls patched in Python, not in env vars or system config?

- **macOS has three layers of proxy and they are
  controlled by different mechanisms.** Layer 1
  (env vars `http_proxy` / `https_proxy`) is easy
  to clear. Layer 2 (macOS system proxy via
  `scutil --proxy`) affects every process. Layer 3
  (third-party app like Clash Verge listening on
  7897) injects via launchd env into every process
  the app spawns.
- **Env-var-only fixes (`env -u https_proxy`) only
  clear layer 1.** Layers 2 and 3 are untouched.
  The fix has to live in the Python runtime.
- **`urllib.request.getproxies = lambda: {}`
  monkey-patches the function that all
  `urllib`-based clients consult.** This is layer
  1 + layer 2 in one patch (layer 2 flows through
  the same `getproxies` function). Layer 3 is
  handled by the same patch because layer 3
  ultimately injects env vars that flow through
  `getproxies` too.
- **The same shape applies to the IPv6 fix.**
  `socket.getaddrinfo` is the Python-level
  function that all `socket`-based clients use;
  patching it to drop IPv6 candidates pre-empts
  the happy-eyeballs deadlock without touching
  system config.
- **Both patches are documented in
  `fund-data/AGENTS.md` §Long-running pitfalls.**
  They are not workarounds for a `fund-data` bug;
  they are workarounds for macOS-specific
  behaviours that no Python library can fix from
  outside.

### Q13. Why is the 1-hour OSS cache TTL a problem for nightly backfill?

- **The cache TTL is intentional.** It means a daemon
  that pulls a bundle does not hammer OSS on every
  call. A nightly backfill that needs the latest
  data has to either (a) wait up to 1 hour for the
  cache to refresh, or (b) re-pull explicitly.
- **The nightly CI workflow calls
  `fund_cli cloud pull` first**, which re-checks
  the manifest URL and pulls if the version
  bumped. If the version did not bump, the local
  cache is used and the backfill reads from it.
- **The right cadence is:** nightly cron
  → `cloud build-bundle` (publishes the latest
  query DB) → `cloud upload` (writes the
  manifest) → consumer `cloud pull` (picks up the
  new manifest) → consumer `backfill`. Each step
  has its own failure mode and is logged
  separately. A missing step is the most common
  cause of "the backfill ran but used yesterday's
  data".

### Q14. Why doesn't `batch_sync_funds` have a `--dry-run` flag?

- **`--max-funds N` is the cheap dry run.** It caps
  the fund count and runs the full pipeline. A
  smoke test is `--max-funds 5 --concurrency 1`
  with `--include-all`, which runs in ~30 seconds
  and exercises every code path except the long
  tail.
- **A true `--dry-run` would have to predict
  per-fund dataset fan-out** (which fund will
  return empty for `fees`? which will trip a rate
  limit?), and that prediction requires running
  the fetch. The cheapest faithful dry-run is
  exactly what `--max-funds` does.
- **The team considered a "report what would be
  fetched" mode** that walks the `funds` table
  and prints the include-flag set per code. It was
  rejected as redundant with `coverage_report` —
  the agent can read the coverage report and the
  `funds.fund_type` field and infer the shape.

---

## Design philosophy (the "why" of the seven-layer shape)

Read this section once and the rest of the playbook becomes
obvious.

1. **The pipeline is shaped by its failure modes, not its
   success modes.** Snapshot and NAV are hard-fail because
   they are data anchors. Profile and holdings are soft-fail
   because they are enrichment. The two-tier classification
   is the loud/quiet boundary, and the boundary is drawn
   by what the user can do with partial data.
2. **State is for long runs, not for short ones.** A 100-fund
   pull does not need a state file; a 27k-fund pull does.
   The split between `batch_sync_funds` (no state) and
   `backfill.py` (state) is intentional. A future
   `batch_sync_funds --state-file` flag is a one-line
   addition if needed.
3. **The two-tier failure policy is the contract.** Agents
   that consume `sync_fund` results know that
   `status: "ok"` + empty `dataset_errors` is a full
   success, `status: "ok"` + non-empty `dataset_errors` is
   a partial success, and `status: "error"` is a hard
   failure. The dataset_errors list is the audit hook.
4. **Provider chain ordering is a benchmark, not a belief.**
   Eastmoney-first for the cheap four, AkShare-first for
   the deep eight, paid providers prepended if keyed —
   the team re-measures quarterly. The ordering lives in
   `build_providers_full` and is the only file to touch
   when a provider gets faster.
5. **Two storage tiers exist for a reason.** The full
   audit-log DB (`fund-data/data/fund_data.sqlite`) keeps
   `raw_responses`, `sync_runs`, `sync_failures`; the
   query-only bundle (`fund_data_query.sqlite.gz`) strips
   them. The `default_db_path()` precedence list prefers
   the cache when pulled; the on-disk DB is the
   `doctor.py` baseline. The two are intentionally
   separate, and a long-running backfill that wants to
   land in the on-disk DB must set `FUND_DATA_DB`
   explicitly.
6. **Idempotency is the default.** Every `upsert_*` is
   keyed on a primary key; every `fetch_*` is read-only
   upstream. Re-running the same `sync_fund` on the same
   code is a no-op. Re-running the same `backfill` is
   safe; the state file is what makes resume cheap.
   This is why the team felt comfortable with
   "retry the same call" being the answer to "what if
   it failed halfway".
7. **Configuration through env vars, not code.** Every
   behaviour-altering knob is an env var:
   `FUND_DATA_DB`, `FUND_DATA_AUTO_PULL`,
   `FUND_DATA_MANIFEST_URL`, `FUND_DATA_CACHE_DIR`,
   `INVESTODAY_API_KEY`, `TUSHARE_TOKEN`,
   `FUND_DATA_DISABLE_AKSHARE`. A long-running daemon
   flips these between calls without restarting; a CI
   runner sets them per-step.
8. **Errors carry trail, not blame.** `ProviderError`
   says "all providers failed for sync_fund: eastmoney:
   ...; akshare: ...". `sync_failures` records the
   per-fund failure message. The `dataset_errors` list
   in the `sync_fund` result carries the per-dataset
   trail. The `sync_runs` table carries the per-call
   audit row. The trail is what an agent needs to
   self-diagnose; the convention is "a failure
   response is a success-shaped message that happens
   to contain `error` or `isError`, never a bare
   string".

---

## What NOT to say (anti-patterns)

These are common wrong answers the team has seen in PR reviews
and support threads. Avoid them.

- **"Just call `fund_sync` in a loop."** That is the
  single-fund entry point. The whole batch system exists
  to avoid the loop. The loop is the wrong shape because
  each call spawns the full bootstrap.
- **"Backfill always takes 21 hours."** It takes 21 hours
  on the AkShare path. Eastmoney + concurrency 8 is ~90
  minutes for snapshot + NAV. The runtime is a function
  of the provider choice, not a constant of the system.
- **"It writes to the local DB."** Which local DB? The
  on-disk `fund-data/data/fund_data.sqlite` or the
  `~/.cache/fund-data/releases/<version>/fund_data_query.sqlite`
  the bootstrap just pulled? `default_db_path()` decides.
  Set `FUND_DATA_DB` if you want the on-disk one.
- **"Retry the 380 snapshot failures."** They are
  `eastmoney: fund code must contain 6 digits: ''` +
  `akshare: 'AkshareProvider' object has no attribute
  'snapshot'`. Both will fail forever. The PR that adds
  `AkshareProvider.snapshot` is the fix; retrying is
  noise.
- **"Backfill is best run at night."** Backfill is
  whatever the operator schedules. The nightly CI
  workflow runs at 02:00 UTC because that is when
  the OSS bundle is published. An on-demand pull is
  not bound to that schedule.
- **"Use `akshare` for the full backfill."** AkShare is
  currently unusable for full coverage. Use Eastmoney for
  the cheap four, Tushare/Investoday for the deep eight.
- **"The macOS proxy is a `fund-data` bug."** It is a
  macOS quirk. Three layers of proxy (env vars,
  `scutil --proxy`, third-party app) are how macOS
  works. Patching in Python is the only portable fix
  that does not affect the user's daily network.
- **"`backfill_state.failed_codes` is the failure
  queue."** It is a snapshot. The live queue is
  `sync_failures`. They drift. Read both.
- **"Just run `refresh_fund_type` to fix `fund_type`."
  It fixes 99.93 % of rows.** The 18 funds with empty
  `fund_type` after that pass are 2024-2025 new funds
  that the Eastmoney index has not typed yet. A second
  `refresh_fund_type --only-empty` will not help; the
  fallback is regex on `fund_name`.

---

## How to keep this playbook accurate

The playbook is the team's *settled* explanation, not the
live code. When the code changes, update the playbook in
the same PR. The check is:

- Did `backfill.py` defaults change
  (`DEFAULT_CONCURRENCY`, `DEFAULT_BATCH_SIZE`,
  `LOCK_RETRY_ATTEMPTS`)? → Update the run profile in
  §6 and Q4/Q5/Q6.
- Did `_resolve_include_flags` change? → Update Q3
  and Q11.
- Did `sync_fund` capability ladder change (a new fetch
  added or reordered)? → Update Paragraph 5 and Q2.
- Did the hard-fail / soft-fail classification change? →
  Update Paragraph 5 and Q2.
- Did the provider chain ordering for any capability
  change? → Update Q4 and the philosophy section.
- Did a new env var land? → Add it to Paragraph 3 and
  the env var decision table in
  `fund-batch-sync-pipeline.md`.
- Did a new failure track get added? → Update Q10 and
  the philosophy section.

If a PR changes any of the above and does not update the
playbook, request changes with a pointer to this section.

---

## Related documents

- [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) —
  diagrams + code anchors + env var table.
- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —
  the single-search reference (start here if you have
  not read the search playbook).
- [`fund-search-playbook.md`](./fund-search-playbook.md) —
  the single-search answer script.
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —
  the agent-facing skill manifest.
- [`../../fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md) —
  the contributor-facing architecture reference.
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —
  backfill recipes, long-running pitfalls (macOS proxy /
  IPv6 / `default_db_path` divergence / lock retry),
  and the per-provider performance numbers that
  justify the chain ordering.
- [`../../fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md) —
  how to enable each provider, what each provider
  actually unlocks, and the recipe for registering a
  new one.
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —
  per-platform install layout for Codex / Claude /
  OpenClaw.
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —
  the v0.3.0 backlog items that will land next (no
  `--json` flag, no HTTP/SSE MCP, no progress
  notifications, no `fund_doctor` MCP tool).
- [`../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md`](../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md) —
  the per-table / per-fund_type coverage diagnostic
  that anchors Q2's two-tier failure policy and the
  380-snapshot-failure audit.
