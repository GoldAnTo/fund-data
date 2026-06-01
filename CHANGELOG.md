# Changelog

All notable changes to this project are documented in this file. Versions
follow [Semantic Versioning](https://semver.org/) once the project reaches
1.0. The pre-1.0 series (0.x) is allowed to break compat in minor bumps.

## Unreleased

Track of the work in flight toward **0.2.0**. Items land in
chronological order — see `git log` for the per-commit detail.

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

### Changed

- `requirements.txt` — `akshare==1.18.64` → `akshare>=1.18.0,<2.0`
  to dodge the exact-pin resolution problem and match the
  [project.dependencies] block in `pyproject.toml`.
- `SKILL.md` — `version: 1.0.0` → `0.1.0` to match the
  `CHANGELOG.md` and `pyproject.toml` version.
- `README.md` — status table rewritten with real DB numbers
  (75 unit tests, 16.93 % snapshot coverage, 2.69 % profile
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
