# Changelog

All notable changes to this project are documented in this file. Versions
follow [Semantic Versioning](https://semver.org/) once the project reaches
1.0. The pre-1.0 series (0.x) is allowed to break compat in minor bumps.

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
  `INVESTDATA_API_KEY`. See `fund-data/PROVIDERS.md` for the 5-minute
  onboarding guide.
- **`scripts/backfill.py`** — resumable end-to-end backfill of the
  local SQLite data base. Honors `fund_type` filtering, persists a
  state JSON for resume across restarts, and emits a summary.
- **`scripts/doctor.py`** — single-shot environment health check
  (Python version, DB schema, AkShare venv, Eastmoney reachability,
  provider construction, sync failures queue, coverage stats). Exits
  non-zero on failure so it can gate CI.
- **`scripts/install_skill.py`** — cross-platform installer with
  `install` / `uninstall` / `status` subcommands and
  `--target {claude,codex,openclaw,agents,all}`. Symlinks by default
  so local edits propagate immediately.
- **68 unit tests** across parser, provider, store, CLI, backfill,
  doctor, and Tushare adapter.
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
  - `test.yml` — runs the 68 unit tests on Python 3.11 / 3.12 / 3.13
    for every push, PR, and manual dispatch.
  - `sync.yml` — nightly resumable backfill (UTC 02:00 == 10:00
    Asia/Shanghai) plus manual dispatch with provider/concurrency
    inputs.
- **MIT License** at the repo root.

### Fixed

- `build_providers` silently dropped `ProviderError` in auto mode,
  causing `sync --include-all` to record seven `dataset_errors` per
  fund when `akshare` was not installed. The 38 rows in
  `sync_failures` from the pre-fix runs all share this root cause;
  the new `build_providers_full` returns the warnings and the
  `logger.warning` channel makes the degraded chain visible.

### Known Gaps (tracked for 0.2.0)

- `fund_profiles`, `stock_holdings`, `bond_holdings`,
  `industry_allocations`, `fee_structures`, `dividends`, and
  `fund_managers` are still mostly empty after the v0.1.0 backfill
  (Eastmoney does not implement those capabilities). The Tushare
  adapter covers them once a token is configured; Investoday does
  the same once a key is configured.
- The 25,961-fund Eastmoney-only backfill is currently projected to
  take ~8h on a 2-year NAV window. A 5-year window and the missing
  datasets are queued for v0.2.0.
- `doctor.py` does not yet detect a stale backfill state (>24h with
  no progress); will land alongside the `coverage-report --stale`
  subcommand.
