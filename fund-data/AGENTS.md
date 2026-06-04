
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

## Long-running pitfalls worth pre-flighting before kicking one off

- **`fund_data.default_db_path()` and `doctor.py` resolve to
  different databases by default.** `default_db_path()` walks
  ``FUND_DATA_CACHE_DIR`` -> ``FUND_DATA_DB`` ->
  ``fund_cloud.current_db_path()`` (the ``~/.cache/fund-data/
  current.json`` pointer) -> the on-disk ``fund-data/data/
  fund_data.sqlite`` fallback. ``doctor.py`` only knows about
  that last fallback. So after a successful ``cloud pull`` the
  CLI / MCP / batch-sync writes land in the cloud cache
  query db while ``doctor`` reports the on-disk production db
  numbers. Pick one explicitly: either ``export
  FUND_DATA_DB=/path/to/fund_data.sqlite`` before the run, or
  ``rm ~/.cache/fund-data/current.json`` to force the fallback.
  Always check ``~/.cache/fund-data/current.json`` before a
  long-running pull so the on-call human does not later wonder
  why a 19k-row increment is in the cache but not the on-disk
  db.

- **macOS Python `urllib` will silently route through three
  layers of proxy** (env vars, ``scutil --proxy``, and a
  third-party app on 7897/1080). The CLI has no proxy bypass
  knob, and ``env -u http_proxy -u https_proxy`` only clears
  layer 1 -- the macOS system proxy and the third-party
  app's launchd injection are still in play. To run a
  long-running pull **without** a proxy, inject at the
  Python layer: ``urllib.request.getproxies = lambda: {}``
  before importing the rest of the project. Do not patch
  ``_scproxy.get_proxy_settings`` -- that is a C extension
  attribute, and the underlying ``SCDynamicStoreCopyProxies``
  call ignores Python-level attribute replacement.

- **macOS happy-eyeballs + urllib can deadlock on an
  IPv4-only server.** Eastmoney only returns A records (no
  AAAA), but macOS ``getaddrinfo`` (RFC 6724) prefers IPv6
  and queues the IPv4 SYN behind the IPv6 one. The IPv6 SYN
  never completes, the IPv4 SYN never gets sent, and the
  process looks like it is hung at 0% CPU. ``dig +short
  <host>`` before any long-running pull: if the result has
  no AAAA, monkey-patch ``socket.getaddrinfo`` to drop the
  IPv6 candidates before importing the project. The patch is
  the same shape as the proxy one -- Python-level
  ``socket.getaddrinfo`` filter, not a system-level change.

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

### AkShare v1.18.64 schema drift — bond/industry/stock providers broken (2026-06-02)

AkShare `fund_portfolio_industry_allocation_em`, `fund_portfolio_bond_hold_em`,
and `fund_portfolio_hold_em` all fail in v1.18.64 (and likely later) due to
upstream Eastmoney API changes:

| Function | Error | Root Cause |
|---|---|---|
| `fund_portfolio_industry_allocation_em` | `ValueError: Length mismatch: Expected axis has 1 elements, new values have 17 elements` | `reset_index()` creates a 1-col index, then `temp_df.columns = [...]` (17 cols) fails |
| `fund_portfolio_bond_hold_em` | `KeyError: '占净值比例'` | Eastmoney renamed this column (new name unknown) |
| `fund_portfolio_hold_em` | returns 0 rows (no crash) | API response shape changed, no error surfaced |

**Fix options** (user decides, not hardcoded):
1. **Add Investoday fallback for bond/industry** — `investoday.py` has `stock_holdings` but not `bond_holdings`/`industry_allocations`. Add them via `/fund/portfolio-bond-holdings` and `/fund/portfolio-industry-alloc` endpoints (API key already set).
2. **Patch AkShare calls with column name fallbacks** — defensive `_first_value` already handles `占净值比例` aliases; the actual fix needs to be in AkShare itself (patch upstream or wrap the call in a try/except that skips `reset_index`).

Until fixed: `run_provider_chain` correctly falls through AkShare → Investoday for `stock_holdings` (works), but `bond_holdings` and `industry_allocations` have no downstream provider in the chain — they return empty rows and the monitor sees 0 DB growth.

**Operational impact (2026-06-02)**: 11 consecutive monitor cycles (105 min) of 0 DB growth across all three tables (548,975 / 415,700 / 2,475,195 frozen). `local-fill-backup-monitor` goes into restart→stall-kill loop because the spec has no "provider is dead" branch. Fix first, then re-enable the fillers.

---

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

---

## Self-audit / OpenClaw active-completion gotchas (2026-06-03)

Lessons from the `fund_self_audit` + `fund_completion_*` ships. Most
of these are silent fallthroughs that look fine in the happy path but
break the OpenClaw contract; treat any one of them as a release-blocker.

### `self_audit` queue's P1 missing is dominated by back-end share classes

When you run `fund_cli self-audit --limit 50` against a 26,953-fund
universe, the top 50 P1 missing rows are almost all back-end share
class codes (`000002`, `000012`, `000108`, `000140`, `000154`, ...,
`002343`, `002606`, ...). These funds have a stub Eastmoney
snapshot/NAV page: `eastmoney: provider returned no rows` even with
`--refresh`. The same is true for the 380 funds sitting in
`sync_failures` per the snapshot section above. **If the goal is to
demonstrate `completion-run` actually filling rows, do not pick codes
from the top of the self-audit queue by score** — pick a known good
fund (e.g. `110022` 易方达消费行业股票) and craft the queue / plan by
hand, or filter `--fund-type 股票型` to get past the share-class
prefix. The auto chain (auto → AkShare → Investoday → Tushare) will
*not* rescue a stub Eastmoney response.

### `batch-sync` exits 0 with `failed: 0` even when every fund got 0 rows

`fund_cli batch-sync` swallows per-fund provider failures into a
`failed` JSON field and returns 0. A 3-fund trial against the
back-end share class prefix returns
`{"total": 3, "ok": 3, "failed": 0, "results": [{"status": "ok",
"nav_rows": 0, ...}, ...]}` — the runner sees `returncode=0` and
classifies the batch as a success even though `rows_changed=0`. The
post-fix `completion._batch_failed_count` reads the JSON `failed`
field and reports partial failures to the failure-rate budget, but it
cannot tell the difference between "provider returned stub" and
"fund genuinely has no data for this dataset". A `nav_rows: 0` on a
non-shared class is a stronger "stub" signal than the `failed` field
itself.

### Snapshots have no `batch-sync` primitive yet

**Updated 2026-06-04**: this note is now stale. `fund_cli batch-sync`
exposes `--include-snapshots` (default `True`, with a `--no-include-snapshots`
opt-out). `sync_fund` itself always pulled the snapshot row, so the
only thing that was missing was a visible contract. The completion
plan builder now routes snapshot rows through the regular batch
path; the old `blocked + fallback_cli` behaviour was retired.

If you see a completion plan with `dataset: snapshots` in
`blocked[]`, that is a regression -- it should always be in
`batches[]` instead. (Regression guard:
`test_snapshots_in_queue_lands_in_batch_not_blocked`.)

### P0 is not a queue of bad rows; it is a request to bootstrap the universe

`build_self_audit_queue` emits P0 only when the caller passes an
explicit `codes=` list and one of those codes is not in the local
`funds` table. The recommended action is `fund_search`, not
`fund_sync`. If a P0 appears in a self-audit run that did *not* pass
`--code` / `--codes-file`, that is a regression. (Regression guard:
`test_unknown_code_in_explicit_codes_is_p0_with_bootstrap_action` and
`test_unknown_code_only_request_returns_p0_only`.)

### `provider_calls` is the post-execution sum, not a pre-fill

The runner used to pre-fill `execution["summary"]["provider_calls"]`
with the plan's `estimated_provider_calls` in the budget-check
branch and then increment it again per batch — a 2-code plan
reported 4. After the fix, `provider_calls` starts at 0 and is
incremented only by actually-executed batch sizes. Refusal paths
leave it at 0; an operator reading the report can trust the field to
reflect what the runner actually did, not what the plan estimated.
(Regression guard: `test_provider_calls_is_not_double_counted` and
`test_provider_calls_refusal_does_not_leak_estimated`.)

### OpenClaw completion-run is a local-DB mutation, never a publish

`run_completion_plan` does not import `fund_cloud` and refuses to
call `cloud build-bundle` / `cloud upload` / `cloud archive-full` from
anywhere. The MCP tool description for `fund_completion_run`
explicitly says "Never publishes OSS" so an agent cannot confuse
`executed: true` with "the OSS bundle is updated". Publishing is a
separate operator step documented in
`docs/agent-flows/openclaw-active-publish-playbook.md`.
