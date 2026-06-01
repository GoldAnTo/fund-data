# Adding a Paid Provider: Investoday (or any structured fund API)

The `fund-data` skill ships with three free providers
(Eastmoney, AkShare, Tushare) and one pre-wired paid slot
(Investoday). The free tier covers 95% of the data, but two pain
points make a paid key worth it for a team/data base:

1. **Rate limits** — AkShare and Eastmoney throttle on bursts; the
   full 25,961-fund backfill currently takes 90 minutes on
   Eastmoney-only and 8+ hours on AkShare.
2. **SLA** — Investoday offers a contract-backed uptime guarantee
   and a unified JSON schema across 180+ endpoints, which is what
   the agent harness wants when it triggers long-running data jobs.

## Enabling Investoday (5 minutes)

1. **Apply for a key** at <https://data-api.investoday.net>. The
   free trial (200 calls / 30 days) is enough to validate the
   integration; paid plans start at ¥12.9 / 5,000 calls (3 months).
2. **Export the key** in your shell profile (or in the OpenClaw /
   Codex / Claude runtime environment). The canonical name is
   `INVESTODAY_API_KEY`; the older `INVESTDATA_API_KEY` is still
   accepted as a fallback for setups that picked it up from the
   Investoday console.

   ```bash
   export INVESTODAY_API_KEY=xxxxxxxxxxxxxxxx
   # or, legacy:
   export INVESTDATA_API_KEY=xxxxxxxxxxxxxxxx
   ```
3. **Verify it loads**:

   ```bash
   .venv-akshare/bin/python3 scripts/doctor.py
   ```

   You should see:

   ```json
   "investoday": {
     "ok": true,
     "skipped": "INVESTODAY_API_KEY not set"
   }
   ```

   become:

   ```json
   "investoday": { "ok": true }
   ```
4. **Rerun a backfill** — Investoday is automatically added to the
   provider chain in `auto` mode:

   ```bash
   .venv-akshare/bin/python3 scripts/backfill.py --include-all --report-year 2024
   ```

## What your key actually unlocks

Investoday's data plane is tiered (L1 基础 → L5 AI 特色). The
**¥12.9 体验包 advertises "all API permissions"** but in practice
the key only opens the L1 surface until you upgrade. Concretely,
on the 体验包 we have observed:

| Endpoint family | 体验包 (¥12.9) | Notes |
|---|---|---|
| `/fund/all` (L1 — 27k-fund catalog with 31 profile fields) | ✅ | The killer feature — full profile per fund in one call. |
| `/fund/nav/history` (L1) | ⚠️ may be disabled | Falls back to Eastmoney; no data loss. |
| `/fund/portfolio-stock-holdings` (L2) | ❌ 40001 `无效的接口` | Falls back to AkShare. |
| `/fund/portfolio-bond-holdings` (L2) | ❌ | Falls back to AkShare. |
| `/fund/portfolio-industry` (L2) | ❌ | Falls back to AkShare. |
| `/fund/portfolio-manager` (L2) | ❌ | Falls back to AkShare. |
| `/fund/fee` (L2) | ❌ | Falls back to AkShare. |
| `/fund/dividend` (L2) | ❌ | Falls back to AkShare. |
| `/fund/split` (L2) | ❌ | Falls back to AkShare. |

If your key returns `code: 40001` / `无效的接口` from a portfolio
endpoint, ask Investoday support to enable the L2 portfolio-* set
on your account, or upgrade to the ¥45 基础包 / ¥80 专业包.

## The one big win that *does* work on the 体验包

The `InvestodayProvider.profile()` method reads from the
`/fund/all` catalog, so the **L1 path is enough to take
`fund_profiles` coverage from ~2.7 % to ~99 % in a single 40-second
run**:

```bash
export INVESTODAY_API_KEY=xxxxxxxxxxxxxxxx
python3 fund-data/scripts/investoday_profile_sync.py
# ok=25828 fail_provider=0 fail_locked=0 elapsed=38s
# fund_profiles: 26632 / 26936 funds (98.87 %)
```

The script is safe to re-run (idempotent INSERT OR REPLACE) and
safe to run alongside the main backfill (it only writes to the
`fund_profiles` table, which the backfill does not touch).

## Why auto mode picks the right order

`build_providers("auto", capability=...)` now produces a chain that
matches the workload:

| Capability | Order (first try → last try) |
|---|---|
| `fund_list`, `search`, `nav_history`, `snapshot` | Eastmoney → AkShare |
| `profile`, `holdings`, `bonds`, `industries`, `fees`, `dividends`, `splits`, `managers` | AkShare → Eastmoney (Tushare/Investoday inserted first if their keys are present) |

When you set `INVESTDATA_API_KEY`, Investoday is **always** tried
first because it is the highest-trust source. The free providers
remain as fallbacks. When you set `TUSHARE_TOKEN`, Tushare is
tried first for the AkShare-only capabilities (profile/holdings/...)
because Tushare has cleaner JSON than the AkShare wrappers.

## Going further: registering your own provider

If you wire up another structured API (iTick, Wind via the official
SDK, etc.):

1. Subclass `EastmoneyProvider` / `AkshareProvider` /
   `TushareProvider` in `scripts/fund_data.py` and implement the
   methods that source has: `search_funds`, `fund_list`,
   `nav_history`, `snapshot`, `profile`, `stock_holdings`,
   `bond_holdings`, `industry_allocations`, `fee_structures`,
   `dividends`, `splits`, `fund_managers`.
2. Add a `PROVIDER_X` constant and a branch in `build_providers`
   that inserts your provider when the matching env var is set.
3. Add unit tests with a fake `pro` module (see
   `scripts/tests/test_tushare.py` for the pattern).
4. Update this document with a one-paragraph "How to enable" entry
   and the provider's terms/price tier.

## Why we don't auto-enable any paid source

`build_providers` is opt-in: a paid key must be set explicitly via
the environment. The skill will never reach out to a commercial
endpoint without the user opting in, both for cost reasons and to
keep the base install zero-config for evaluators.
