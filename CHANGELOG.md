# Changelog

All notable changes to this project are documented in this file. Versions
follow [Semantic Versioning](https://semver.org/) once the project reaches
1.0. The pre-1.0 series (0.x) is allowed to break compat in minor bumps.

## 0.3.0 (unreleased)

The "split the monolith" release. The 0.2.0 line shipped the
data plane; 0.3.0 is about making the codebase sustainable for
the next round of capability additions. The pre-1.0 series is
allowed to break compat in minor bumps, but every planned break
ships behind a re-export facade so CLI / MCP consumers keep
working unchanged.

### Planned (RFCs in flight)

- **`fund_data.py` 3605-line split** — propose:
  `providers/`, `store.py`, `schema.py`, `sync.py`,
  `normalizers.py`. The top-level `fund_data.py` stays as a
  re-export facade so every existing
  `from scripts import fund_data; fund_data.snapshot(...)` and
  every CLI/MCP entry point continues to work. RFC draft
  pending review.
- **fund_managers fund-centric link view** — KNOWN_GAPS #5.
  Either upgrade the Investoday key to ¥45 基础包
  (unlocks `/fund-manager/basic-info` L1, ~200 calls/min,
  structured JSON) and bulk-import, or schedule a one-shot
  9 h AkShare run on cron. The data shape is small; the
  work is provider onboarding.
- **`refresh_fund_type` nightly automation** — KNOWN_GAPS #6.
  Wire `scripts/refresh_fund_type.py` into the nightly sync
  so new funds land with a real type from day one.
- **`AkshareProvider.snapshot`** — closes the 380
  `sync_failures` row gap; pairs with KNOWN_GAPS #3.
- **`coverage_report` `EXPECTED_EMPTY` matrix as a
  data-driven artifact** — currently a flat dict in
  `scripts/coverage_report.py`; the 0.3.0 plan is to lift
  it into `docs/fund-data-inventory.md` as the canonical
  source and have the script import it. See follow-up note
  in commit `55cb6be`.

### Carried forward from 0.2.0 (KNOWN_GAPS)

- **`docs/KNOWN_GAPS.md`** is the live inventory. Items
  #1, #5, #6, #7, #8 are still open at the 0.2.0 → 0.2.1
  boundary; #2, #3, #4 were reclassified during the
  2026-06-02 docs sync (see the "Status as of 2026-06-02"
  block at the top of that file).

### Deferred indefinitely

Multi-currency / FX conversion, live NAV streaming, Tushare
onboarding. See `docs/KNOWN_GAPS.md` "Items not in 0.3.0
scope" for the full list and the rationale.

## 0.2.1 (2026-06-02)

Post-0.2.0 cleanup patch. The 0.2.0 release was functionally
shipped on 2026-06-01; this release rolls up the same-day
follow-ups that were not in the original tag. No behavior
break — every change is additive or fixes a wrong-but-unused
default. The agent contract (`fund_mcp.py` tools, CLI flags,
JSON schemas) is forward-compatible.

### Added

- **`install_skill.py status` now reports `STALE_COPY`** —
  compares the version + content hash of the installed
  `SKILL.md` against the repo and surfaces a one-line
  refresh hint when they diverge. New outcomes: `MISSING`,
  `LINKED`, `STALE_COPY` (version or hash diff), `INSTALLED`,
  `BROKEN`. 6 new unit tests; the previous version just printed
  `INSTALLED — <path>` regardless of drift. The Codex install
  (which had silently been on 0.1.0 while the repo was 0.2.0)
  was refreshed as part of this release.
- **`doctor.py` gains two new top-level checks** —
  `default_db` reports which database agents will actually
  open (`fund_data.default_db_path()` resolver, with
  `source` tagged as `env_override` / `cloud_cache` /
  `full_local` / `unknown`), and `cloud_cache` reports the
  installed bundle version vs the remote manifest's
  `update_available` flag. `--db` now defaults to
  `fund_data.default_db_path()` so `sync_failures` /
  `coverage` / `backfill_stale` match what agents see
  (previously, doctor reported the on-disk full DB while
  agents wrote to the cloud query cache — see
  `fund-data/AGENTS.md` "Long-running pitfalls"). 11 new
  unit tests; the top-level schema is pinned by
  `MainOutputSchemaTests.EXPECTED_TOP_LEVEL_KEYS` so any
  future refactor that drops a key fails CI.
- **`coverage_report.py` separates actionable missing from
  structural empty** — the global 49 % "missing stock_holdings"
  figure was inflated by every 货币型 / 债券型 / 指数型-固收 /
  FOF / REITs fund that is structurally not supposed to
  have equity. A new `EXPECTED_EMPTY` matrix
  (7 fund_type × 9 dataset rules) splits each fund's
  `missing` list into `actionable_missing` (real backfill
  work) and `structural_empty` (expected by design).
  `adjusted_completeness` is recomputed against the
  reduced denominator so a fully-populated 货币型 scores
  100 % instead of 75 %. The matrix itself is dumped into
  the markdown / table output so the reader does not have
  to cross-reference `docs/fund-data-inventory.md`.
  18 new unit tests; total suite 233 → 262.
- **MCP `SERVER_VERSION` bumped to 0.2.0** — was still
  `0.1.0` while SKILL.md had moved to 0.2.0, so
  `initialize` responses lied about the on-disk contract.

### Fixed

- **`COVERAGE_DATASETS` had a stale `industries` (plural)
  entry** — `fund_data.coverage_report` has always emitted
  the key as `industry` (singular), so the previous
  per-dataset markdown aggregate silently skipped the
  industry_allocations column. Aligned to the API.

### Docs

- `README.md` — version badge 0.1.0 → 0.2.0, line count
  3.4k → 3.6k, test count 148 → 227, CI row adds
  `nightly.yml`, `sync_failures` row points at
  `doctor.py` (the previous "0" was a DB-dependent
  number, not a constant).
- `docs/nightly-ci-design.md` — top "Status: design only,
  no workflow wired up yet" → "Status: shipped
  (2026-06-01), see `.github/workflows/nightly.yml`".
  The workflow has existed since 0.2.0 but the doc
  had not caught up.
- `docs/KNOWN_GAPS.md` — adds a "Status as of 2026-06-02"
  block at the top of the 0.3.0 candidates list. #2
  (fees) shipped (now 100 %); #3 (snapshots) partially
  shipped (95.7 %, blocked on `AkshareProvider.snapshot`
  — AGENTS.md follow-up #3); #4 (splits) is structural,
  not actionable.
- `run_tests.sh` top comment 178 → 227.

## 0.2.0 (2026-06-01)

The "data layer is actually production-grade" release. SQLite WAL
mode, multi-provider backfill with crash recovery, the Investoday
profile sync, the akShare bulk sync (runnable standalone so the main
DB never holds a long write lock), the MCP stdio server for AI
agents, and the contributor / governance hygiene (CHANGELOG /
CONTRIBUTING / SECURITY / COC / Issue & PR templates / dependabot /
release workflow).

### Headline numbers (final, post-resume)

- **Profile coverage: 98.87 %** (26,632 / 26,936 funds) — driven by
  `scripts/investoday_profile_sync.py` reading the Investoday
  `/fund/all` catalog. ~40 s for the full universe.
- **Stock / bond / industry holdings: 49 – 100 %** — the
  akShare bulk sync raised stock_holdings 0.5 k → 13.2 k
  (49.06 %), bond_holdings 538 → 15.4 k (57.20 %),
  industry_allocations 493 → 13.3 k (49.27 %); the backfill
  defaults keep `industries` at 100 %.
- **Dividends / splits: 28 % / 2 %** — the akShare sync
  landed 7.2 k dividend rows and 572 split rows, mostly money-market
  funds that historically had no dividend history.
- **Fees: 18.14 %** — AkShare's page-scraped fee endpoint returns
  empty for the full universe, so the Eastmoney backfill is the
  only source. The 2ec363b fix to `akshare_capability_backfill.py`
  switches the per-fund call from the wrong kwarg `indicator=` to
  the correct `indicators=[...]`, so a re-run after the next
  release can land the full ~27 k funds. Still mostly empty in
  this release's headline numbers; the cheapest fix is to upgrade
  the Investoday key to the ¥45 基础包 which unlocks the
  `/fund/fee-structures` L1 endpoint.
- **Backfill reliability** — SQLite store now uses
  `journal_mode=WAL` + `busy_timeout=30 s` (commit `0bfe4ac`), and
  `backfill.py` catches `OperationalError: database is locked` and
  retries with exponential backoff (2 s, 4 s, 8 s + jitter, 3
  attempts) before propagating (commit `4b0de13`).
- **akShare bulk sync** — `scripts/akshare_capability_backfill.py`
  walks the 27 k-fund universe against the AkShare provider for the
  6 akShare-only capabilities. It can write into a fresh SQLite at
  `--separate-db PATH` and `ATTACH` + `INSERT OR REPLACE` the rows
  into the main DB at the end, so it never holds the production
  write lock during the long sync.
- **MCP stdio server** — `scripts/fund_mcp.py` is a dependency-free
  JSON-RPC 2.0 server over stdio. It exposes 17 tools wrapping the
  Python API (`fund_search`, `fund_list`, `fund_nav_history`,
  `fund_snapshot`, `fund_profile`, `fund_stock_holdings`,
  `fund_bond_holdings`, `fund_industry_allocations`,
  `fund_fee_structures`, `fund_dividends`, `fund_splits`,
  `fund_managers`, `fund_sync`, `fund_batch_sync`, `fund_coverage`,
  `fund_coverage_report`, `fund_export`). 3 unit tests cover the
  protocol flow.
- **CLI access** — `--provider tushare` now works from
  `fund_cli.py`, matching the provider chain already supported by
  `backfill.py` and `retry_failures.py`. `fund-cli cloud build-bundle
  / pull / status` adds OSS-hosted query-bundle support (SHA-256
  verified, default cache for MCP/CLI when `FUND_DATA_DB` is unset).
- **Test count: 95 → 125.** The new test files cover the Investoday
  provider, akShare `--separate-db` flow, the backfill `database
  is locked` retry path, and the test-suite bootstrap that pins
  `FUND_DATA_DB` to a tmp file.

### Added

- **Project hygiene** — `pyproject.toml` (package metadata + ruff
  + black + mypy config), `.editorconfig`, `.gitattributes`,
  `.pre-commit-config.yaml`, `.github/workflows/lint.yml` (CI gate
  for ruff / black / pre-commit), `.github/dependabot.yml`,
  `.github/workflows/release.yml` (auto GitHub release on tag push).
- **Community files** — `CONTRIBUTING.md`, `SECURITY.md`,
  `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1),
  `.github/ISSUE_TEMPLATE/{bug,feature,docs}.yml`,
  `.github/PULL_REQUEST_TEMPLATE.md`.
- **`scripts/coverage_report.py`** — produces a markdown table of
  per-dataset coverage % and stale rows for the local SQLite base.
  Replaces the "0.03 %" hardcode that used to live in `README.md`.
  Adds the `--stale` subcommand promised in 0.1.0's known gaps.
- **`scripts/doctor.py`** — now also detects a stale backfill
  state (no batch progress in >24h while the state file says
  the run is in progress). Promised in 0.1.0's known gaps.
- **`scripts/investoday_profile_sync.py`** — bulk-import
  `fund_profiles` rows from the Investoday (今日投资) provider's
  `/fund/all` catalog. Idempotent, safe to run alongside the main
  backfill, ~40 s for the full 27k-fund universe.
- **`examples/`** — three runnable demo scripts (coverage report,
  watchlist sync, JSON export pipeline) for agents and new
  contributors.
- **README badges** — CI / Lint / Nightly Sync / License / Python /
  Version / Last Commit, all in the header.
- **`scripts/fund_mcp.py`** — dependency-free MCP stdio server that
  exposes the local fund data base through `initialize`,
  `tools/list`, and `tools/call`. It wraps the existing Python API
  as tools such as `fund_search`, `fund_nav_history`, `fund_sync`,
  `fund_coverage_report`, and `fund_export`.
- **Cloud data bundles** — `fund-cli cloud build-bundle`, `cloud pull`,
  and `cloud status` support OSS/static-hosted query databases. Bundles
  exclude `raw_responses` and sync audit tables, verify downloads with
  SHA-256, and let MCP/CLI default to the pulled cache when
  `FUND_DATA_DB` is unset. The MCP server also exposes
  `fund_cloud_status`.
- **Private full archives** — `fund-cli cloud archive-full` creates a
  consistent compressed SQLite snapshot that keeps `raw_responses`,
  `sync_runs`, and `sync_failures` for private OSS backup.

### Changed

- `requirements.txt` — `akshare==1.18.64` → `akshare>=1.18.0,<2.0`
  to dodge the exact-pin resolution problem and match the
  [project.dependencies] block in `pyproject.toml`.
- `SKILL.md` — `version: 1.0.0` → `0.1.0` to match the
  `CHANGELOG.md` and `pyproject.toml` version.
- `README.md` — status table rewritten with real DB numbers
  (103 unit tests, 55.34 % snapshot coverage, 98.87 % profile
  coverage); "Known gaps" rewritten to reflect that the v0.1.0
  list (`no CI`, `no nightly sync`, `38 stale failures`,
  `0.03 % coverage`) is done.
- **`InvestodayProvider`** — `fund_list` now auto-paginates
  (`pageSize=500` × 55 pages, was 10000 hardcoded which the API
  rejects with HTTP 400); `search_funds` and the new `profile()`
  read from an in-memory catalog cache (1-hour TTL) so a
  backfill only hits `/fund/all` once. Both `INVESTODAY_API_KEY`
  and the legacy `INVESTDATA_API_KEY` are accepted; the canonical
  name is checked first. 9 unit tests added.
- **Real DB coverage after the Investoday pass**:
  `fund_profiles` 724 / 26,936 (2.69 %) → **26,632 / 26,936
  (98.87 %)** in ~40 s, no extra API quota.

### Fixed

- SQLite writes now use WAL mode and a 30 s busy timeout, which makes
  long concurrent backfills wait for the writer instead of failing
  immediately with `database is locked`.
- `fund_cli.py --provider tushare` now works from the main CLI, matching
  the provider chain already supported by `backfill.py` and
  `retry_failures.py`.
- The installed `fund-cli` console script now imports the package-local
  `fund_data` module correctly after `pip install -e .`.
- `scripts/install_skill.py install --copy` now excludes generated data,
  logs, caches, and SQLite files, and removes stale copies of those
  artifacts from an existing skill install.
- `scripts/install_skill.py install --include-data` now creates a
  portable copy that includes a consistent `data/fund_data.sqlite`
  snapshot. The default remains lightweight (`--data-mode none`), and
  logs, backfill state, WAL/SHM sidecars, and caches stay excluded.

## 0.1.0 (2026-06-01)

First public release of the `fund-data` skill. Designed to be installed
into Codex, Claude Code, and OpenClaw from a single source tree.

### Added

- **Three free providers** with auto-fallback chain:
  - `EastmoneyProvider` — primary source for fund list, search, NAV
    history, and snapshot. No API key required.
  - `AkshareProvider` — primary source for profile, holdings, fees,
    dividends, splits, and fund managers. Optional dependency
    (install via `.venv-akshare`).
  - `TushareProvider` — standardized JSON for the same AkShare-covered
    capabilities. Opt-in via `TUSHARE_TOKEN`.
- **`InvestodayProvider`** — paid 180+-endpoint adapter. Opt-in via
  `INVESTODAY_API_KEY` (also accepts `INVESTDATA_API_KEY`).
  See `fund-data/PROVIDERS.md` for the 5-minute onboarding guide.
- **`scripts/backfill.py`** — resumable end-to-end backfill of the
  local SQLite data base. Honors `fund_type` filtering, persists a
  state JSON for resume across restarts, and emits a summary.
- **`scripts/doctor.py`** — single-shot environment health check
  (Python version, DB schema, AkShare venv, Eastmoney reachability,
  provider construction, sync failures queue, coverage stats). Exits
  non-zero on failure so it can gate CI.
- **`scripts/retry_failures.py`** — drain the `sync_failures` queue
  through `batch_sync_funds`, with `--dry-run` for inspection.
- **`scripts/install_skill.py`** — cross-platform installer with
  `install` / `uninstall` / `status` subcommands and
  `--target {claude,codex,openclaw,agents,all}`. Symlinks by default
  so local edits propagate immediately.
- **75 unit tests** across parser, provider, store, CLI, backfill,
  doctor, retry_failures, and Tushare adapter.
- **Documentation**:
  - `SKILL.md` — Codex / Claude / OpenClaw entrypoint with
    OpenClaw-style frontmatter (`version`, `tags`, `tools`).
  - `SKILLS.md` — per-platform install layout, refresh flow,
    discovery mechanism.
  - `PROVIDERS.md` — Investoday onboarding, "register your own
    provider" recipe.
  - `AGENTS.md` — performance notes, backfill parameter recipes.
  - `README.md` — project quickstart, status, known gaps.
- **GitHub Actions**:
  - `test.yml` — runs the 75 unit tests on Python 3.11 / 3.12 / 3.13
    for every push, PR, and manual dispatch.
  - `sync.yml` — nightly resumable backfill (UTC 02:00 == 10:00
    Asia/Shanghai) plus manual dispatch with provider/concurrency
    inputs.
- **MIT License** at the repo root.

### Fixed

- `build_providers` silently dropped `ProviderError` in auto mode,
  causing `sync --include-all` to record seven `dataset_errors` per
  fund when `akshare` was not installed. The rows in `sync_failures`
  from the pre-fix runs all share this root cause;
  the new `build_providers_full` returns the warnings and the
  `logger.warning` channel makes the degraded chain visible.
- CI `sync.yml` now `mkdir -p data/` before probing the funds
  table, and seeds the table when the runner starts on an empty
  checkout. The gitignored DB was the root cause of the first
  "unable to open database file" failure on a clean runner.
