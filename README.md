# Fund Data

[![CI](https://github.com/GoldAnTo/fund-data/actions/workflows/test.yml/badge.svg)](https://github.com/GoldAnTo/fund-data/actions/workflows/test.yml)
[![Lint](https://github.com/GoldAnTo/fund-data/actions/workflows/lint.yml/badge.svg)](https://github.com/GoldAnTo/fund-data/actions/workflows/lint.yml)
[![Nightly Sync](https://github.com/GoldAnTo/fund-data/actions/workflows/sync.yml/badge.svg)](https://github.com/GoldAnTo/fund-data/actions/workflows/sync.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](CHANGELOG.md)
[![Last Commit](https://img.shields.io/github/last-commit/GoldAnTo/fund-data)](https://github.com/GoldAnTo/fund-data/commits/main)

A local Chinese public fund data base. Wraps the no-key Eastmoney
endpoints, an optional AkShare fallback, a Tushare adapter, and a
structured Investoday (paid) adapter behind one Python skill so
agents (or a developer) can search, fetch, persist, and export fund
data without re-deriving the parsing logic every time.

> Codex / Claude / OpenClaw skill home: [`fund-data/SKILL.md`](fund-data/SKILL.md).
> Design spec: [`docs/superpowers/specs/2026-06-01-fund-data-skill-design.md`](docs/superpowers/specs/2026-06-01-fund-data-skill-design.md).
> Implementation plan: [`docs/superpowers/plans/2026-06-01-fund-data-skill.md`](docs/superpowers/plans/2026-06-01-fund-data-skill.md).
> Provider onboarding: [`fund-data/PROVIDERS.md`](fund-data/PROVIDERS.md).

## Status (v0.1.0)

| | |
|---|---|
| Core library | `fund-data/scripts/fund_data.py` (≈3.0k lines) |
| CLI | `fund-data/scripts/fund_cli.py` |
| Tests | **99 unit tests**, all green |
| Default DB | `fund-data/data/fund_data.sqlite` (gitignored; rebuild on first run) |
| Providers | Eastmoney (no key) → AkShare (optional) → Tushare (`TUSHARE_TOKEN`) → Investoday (`INVESTDATA_API_KEY`) |
| Fund universe | 26,936 funds on first seed |
| Snapshot coverage | 14,907 / 26,936 = **55.34 %** (Eastmoney backfill in progress) |
| NAV coverage | 14,859 unique funds / 26,936 = **55.16 %** |
| Profile coverage | 26,632 / 26,936 = **98.87 %** (Investoday `/fund/all`) |
| CI | test.yml (3.11 / 3.12 / 3.13) + lint.yml (ruff / black) + sync.yml (nightly 02:00 UTC) |
| License | MIT |
| Versioning | [Semantic Versioning 2.0](https://semver.org/) (0.x is allowed to break in minor) |

Run `python3 fund-data/scripts/coverage_report.py` at any time to
regenerate the coverage table from your local DB.

## Quick start

```bash
# 1. (Optional) Install AkShare into its own venv for the fallback chain.
python3 -m venv .venv-akshare
.venv-akshare/bin/python -m pip install -r requirements.txt

# 2. Run the CLI with the system Python — Eastmoney works out of the box.
python3 fund-data/scripts/fund_cli.py list --provider auto --limit 20
python3 fund-data/scripts/fund_cli.py search 沪深300
python3 fund-data/scripts/fund_cli.py nav 110022 --start-date 2024-01-01 --end-date 2024-01-31
python3 fund-data/scripts/fund_cli.py snapshot 110022

# 3. Use the AkShare venv for the full data set (profile, holdings, ...).
.venv-akshare/bin/python fund-data/scripts/fund_cli.py sync 110022 \
    --include-all --report-year 2024 --fee-indicator 申购费率

# 4. Run a batch sync from a watchlist.
.venv-akshare/bin/python fund-data/scripts/fund_cli.py batch-sync \
    --codes-file fund-data/data/fund_codes_sample.txt \
    --include-all --report-year 2024

# 5. Inspect coverage, run doctor, and export.
python3 fund-data/scripts/fund_cli.py coverage --fund-code 110022
python3 fund-data/scripts/doctor.py
python3 fund-data/scripts/fund_cli.py export funds --format csv --output /tmp/funds.csv
```

Override the SQLite path with `FUND_DATA_DB=/abs/path/fund_data.sqlite`.

## Use as an agent skill

The skill ships to **Codex**, **Claude Code**, and **OpenClaw** from a
single source tree. Run the installer once:

```bash
python3 fund-data/scripts/install_skill.py install
```

The installer links or copies the skill folder into the standard
discovery directory for each platform. See
[`fund-data/SKILLS.md`](fund-data/SKILLS.md) for the layout,
refresh flow, and platform-specific quirks.

## Tests

```bash
cd fund-data
python3 -m unittest discover scripts/tests
```

The test suite uses static Eastmoney / AkShare / Investoday / Tushare
fixtures — no network required. The same command runs in CI on Python
3.11, 3.12, and 3.13.

## Project layout

```
.
├── .editorconfig
├── .gitattributes
├── .github/
│   ├── CODEOWNERS
│   ├── ISSUE_TEMPLATE/
│   ├── workflows/
│   │   ├── test.yml         # CI: unit tests on 3.11 / 3.12 / 3.13
│   │   ├── lint.yml         # CI: ruff + black
│   │   ├── sync.yml         # nightly resumable backfill (02:00 UTC)
│   │   └── release.yml      # GitHub release on tag push
│   ├── dependabot.yml
│   └── PULL_REQUEST_TEMPLATE.md
├── .gitignore
├── .pre-commit-config.yaml
├── CHANGELOG.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── LICENSE                  # MIT
├── README.md                # you are here
├── SECURITY.md
├── docs/
│   └── superpowers/
│       ├── specs/           # design documents
│       └── plans/           # implementation plans
├── examples/                # runnable demo scripts
├── pyproject.toml           # package metadata + ruff config
├── requirements.txt
└── fund-data/               # the Codex / Claude / OpenClaw skill
    ├── SKILL.md             # agent entrypoint
    ├── SKILLS.md            # per-platform install layout
    ├── PROVIDERS.md         # provider onboarding
    ├── AGENTS.md            # performance / backfill notes
    ├── agents/openai.yaml
    ├── references/schema.md # SQLite schema reference
    ├── scripts/
    │   ├── __init__.py
    │   ├── fund_data.py     # parsers, providers, store, sync helpers
    │   ├── fund_cli.py      # CLI wrapper
    │   ├── backfill.py      # resumable end-to-end backfill
    │   ├── doctor.py        # environment health check
    │   ├── retry_failures.py
    │   ├── coverage_report.py
    │   ├── install_skill.py
    │   └── tests/           # 99 unittest cases
    └── data/                # SQLite DB + watchlist files (gitignored)
```

## Known gaps (tracked for 0.2.0)

These are the items the team is actively working through. They are
listed in priority order, not all of them are blockers.

1. **Profile / holdings / fees coverage sits at 1.8 – 2.7 %** for the
   AkShare-only capabilities (holdings / bonds / industries /
   fees / dividends / splits / managers). Profile is already at
   98.87 % via `scripts/investoday_profile_sync.py` (Investoday
   `/fund/all`); the rest of the L2 portfolio-* family needs a
   higher Investoday tier (see `PROVIDERS.md`) or a `TUSHARE_TOKEN`.
2. **148 rows in `sync_failures`** at the time of writing. Most are
   benign (currency funds with no holdings, ETF profile endpoints
   that Eastmoney never implemented). Drain with
   `python3 fund-data/scripts/retry_failures.py --provider eastmoney --limit 200`
   after the nightly backfill settles.
3. **No `--verbose` / JSON log flag on `fund_cli.py`.** Lines are
   pretty-printed for humans. An agent-friendly `--json` flag is
   queued for 0.2.0.
4. **No MCP server wrapper.** Codex / Claude / OpenClaw currently
   consume the skill via `bash`. An MCP server that exposes the
   same commands over the protocol is queued for 0.2.0.

## Safety

- Fund data is for research only. Do not use it as personalized
  investment advice.
- Always report the `source` column and `fetched_at` timestamp when
  quoting numbers.
- The CLI defaults to one request per second; do not lower the
  interval or you will be rate-limited by the public endpoints.
- See [`SECURITY.md`](SECURITY.md) for how to report a vulnerability
  in this project.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the developer setup
(venv, pre-commit, lint rules, test command, PR template).
The project follows a code of conduct — see
[`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
