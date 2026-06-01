# Known Gaps — left to 0.3.0

These items were identified during the 0.2.0 work but intentionally
deferred so we could ship the WAL + Investoday + akShare + MCP
work in one release. Track them in the next milestone.

## 0.3.0 candidates, in priority order

1. **`fund_managers` bulk sync** — the table is `manager-centric`
   (PK on `(manager_name, company, current_fund_codes)`) rather
   than fund-centric like every other capability. AkShare's
   `fund_managers(code)` is ~10 s/fund (vs <1 s for the other
   capabilities) because it scrapes the manager bio page; full
   coverage is a ~9 h serial run. The fix is one of:
   - Upgrade the Investoday key to ¥45 基础包 which unlocks
     `/fund-manager/basic-info` (L1, ~200 calls/min, structured
     JSON). Then bulk-import via InvestodayProvider.
   - Or, accept the 9 h AkShare run as a one-shot and schedule it
     as a cron job (see `backfill.py --provider akshare`).
   See `docs/investoday-api-catalog.md` for the endpoint shape
   (48 endpoints documented, 19 already wired).

2. **Fees 18 % → ~95 %** — the akShare bulk sync merged
   **0 rows into fee_structures** because
   `akshare_capability_backfill.py` called
   `fee_structures(code, indicator=...)` but the method only
   accepts `indicators=[...]`. Commit `2ec363b` flips the kwarg
   to the correct form; after that lands, a one-shot bulk sync
   re-run should land fees at 95 %+. The current
   `fees = 18.14 %` number is what Eastmoney backfill alone can
   cover from its `fund_basic` payload.

3. **Snapshots 16 % → 100 %** — pending the Eastmoney resume
   finishing the last ~2 k funds (already in flight; cron
   monitor reports ETA at 00:30). After that, all 26,936 funds
   will have a snapshot, a NAV history, and a profile.

4. **`split_type` 0 % → 95 %+** — the `splits` table is at 2.12 %
   because most active funds have never executed a split. The
   ~470 splits that exist cluster in older 2008–2015 era
   funds. A targeted re-sync of pre-2016 funds would close this
   gap; for the rest, the rows are correctly empty.

5. **`fund_managers` 871 rows are not fund-resolved** — the
   existing rows are manager-centric (one row per manager with
   `current_fund_codes` as a CSV). To answer "who manages
   fund X" today, you have to scan the whole table. A small
   denormalized view (or a join table) would make the
   "fund → manager" lookup O(1). See
   `fund_managers` section in `fund-data/AGENTS.md` for the
   schema discussion.

6. **`refresh_fund_type` automation** — peer added
   `refresh_fund_type.py` to fix the 22 k blank / 1.5 k
   placeholder rows in `funds.fund_type`, but the script is not
   on a schedule. Wire it into the nightly sync so new funds
   land with a real type from day one.

7. **`fund-data/scripts/akshare_capability_backfill.py` page
   scrape fallback** — for the ~2 % of funds that fail AkShare
   due to 5xx / rate-limit, a one-shot retry pass with a
   longer `min-interval-seconds` and per-batch jitter is
   sufficient. Not a v0.2.0 blocker because the bulk sync is
   idempotent and re-runnable.

8. **MCP server: per-tool authorization** — anyone with the
   `fund-mcp` console script can read every fund profile and
   write to every table. For a multi-user / multi-tenant
   deployment we'd want per-tool scopes, but for a single-owner
   local skill this is overkill.

## Items not in 0.3.0 scope (deferred indefinitely)

- **Multi-currency / FX conversion** — every fund's NAV is in
  CNY today. Cross-currency dashboards would need a daily
  FX rate table.
- **Live NAV streaming** — current snapshots refresh on a
  nightly batch. Sub-minute streaming needs a WebSocket
  provider and a different storage backend.
- **Tushare token onboarding** — Tushare Pro works in the
  provider chain but requires a 2,000-credit verification tier
  the operator has not yet signed up for. Investoday
  (`/fund-portfolio-*` L1) covers most of the same ground at
  a friendlier price point.

## Tracking

Each item above should land as a separate `fix/...` or
`feature/...` branch under `.worktrees/`. PRs follow the
project's PR template (`.github/PULL_REQUEST_TEMPLATE.md`),
and the next CHANGELOG entry is `## 0.3.0 (unreleased)`
mirroring the structure of `## 0.2.0 (2026-06-01)`.
