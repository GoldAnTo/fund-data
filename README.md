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
> Architecture reference: [`fund-data/ARCHITECTURE.md`](fund-data/ARCHITECTURE.md).
> Provider onboarding: [`fund-data/PROVIDERS.md`](fund-data/PROVIDERS.md).

## Status (v0.2.0)

| | |
|---|---|
| Core library | `fund-data/scripts/fund_data.py` (≈3.4k lines) |
| CLI | `fund-data/scripts/fund_cli.py` |
| Tests | **148 unit tests**, all green (Python 3.11 / 3.12 / 3.13) |
| Default DB | `fund-data/data/fund_data.sqlite` (gitignored; rebuild on first run) |
| Providers | Eastmoney (no key) → AkShare (optional) → Tushare (`TUSHARE_TOKEN`) → Investoday (`INVESTODAY_API_KEY`) |
| Fund universe | 26,953 funds on first seed |
| Snapshot coverage | 26,935 / 26,953 = **99.93 %** (Eastmoney `pingzhongdata`) |
| NAV coverage | 26,300 unique funds / 26,953 = **97.58 %** (Eastmoney NAV history) |
| Profile coverage | 26,650 / 26,953 = **98.88 %** (Investoday `/fund/all`) |
| Stock holdings coverage | 13,195 / 26,953 = **48.95 %** (AkShare `fund_portfolio_hold_em`) |
| Bond holdings coverage | 15,369 / 26,953 = **57.01 %** (AkShare) |
| Industry allocation coverage | 13,247 / 26,953 = **49.14 %** (AkShare) |
| Fee coverage | 26,929 / 26,953 = **99.90 %** (AkShare + Eastmoney page fallback) |
| Fund manager records | 4,055 distinct managers, 34,654 manager-fund rows |
| `sync_failures` | **0** (last merge of query bundle v2026-06-02-130900) |
| CI | test.yml (3.11 / 3.12 / 3.13) + lint.yml (ruff / black) + sync.yml (nightly 02:00 UTC) + release.yml + security.yml |
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

## MCP server

The same data base can be exposed to MCP-capable agents over stdio:

```bash
python3 fund-data/scripts/fund_mcp.py
```

If installed as a Python package, use the console script:

```bash
fund-mcp
```

Example MCP client config:

```json
{
  "mcpServers": {
    "fund-data": {
      "command": "python3",
      "args": ["/Users/xiongjiali/Desktop/code/fundData/fund-data/scripts/fund_mcp.py"]
    }
  }
}
```

The server exposes tools such as `fund_search`, `fund_nav_history`,
`fund_snapshot`, `fund_sync`, `fund_coverage_report`, and
`fund_export`.

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

By default copied installs are lightweight and exclude
`data/fund_data.sqlite`. To make a portable install that includes the
current local SQLite data snapshot, opt in explicitly:

```bash
python3 fund-data/scripts/install_skill.py install --target codex --include-data
# equivalent:
python3 fund-data/scripts/install_skill.py install --target codex --copy --data-mode copy
```

`--include-data` copies only a consistent `data/fund_data.sqlite`
snapshot; logs, backfill state, WAL/SHM sidecars, and caches are still
excluded.

## Cloud data bundle for OSS

For OpenClaw and other agents, the recommended setup is a lightweight
skill install plus a query-only data bundle hosted on OSS or any HTTPS
static file server. The bundle excludes `raw_responses`, sync logs, and
failure queues so it is much smaller than the full local database.

Build a release locally:

```bash
VERSION=$(date +%F)
python3 fund-data/scripts/fund_cli.py cloud build-bundle \
  --source-db fund-data/data/fund_data.sqlite \
  --output-dir dist/fund-data/releases/$VERSION \
  --base-url https://YOUR_BUCKET.oss-cn-hangzhou.aliyuncs.com/fund-data/releases/$VERSION/ \
  --version $VERSION \
  --manifest-output dist/fund-data/current/manifest.json
```

Upload release files first, then publish the manifest last:

```bash
ossutil cp dist/fund-data/releases/$VERSION/fund_data_query.sqlite.gz \
  oss://YOUR_BUCKET/fund-data/releases/$VERSION/
ossutil cp dist/fund-data/releases/$VERSION/fund_data_query.sqlite.gz.sha256 \
  oss://YOUR_BUCKET/fund-data/releases/$VERSION/
ossutil cp dist/fund-data/current/manifest.json \
  oss://YOUR_BUCKET/fund-data/current/manifest.json
```

Install or refresh the local cache from the project OSS bucket:

```bash
python3 fund-data/scripts/fund_cli.py cloud pull
python3 fund-data/scripts/fund_cli.py cloud status
```

OpenClaw, MCP, and CLI data commands automatically try the project OSS
manifest first (`FUND_DATA_MANIFEST_URL` overrides it). If the query
bundle is available, it is cached under `~/.cache/fund-data/` and used
before live providers. If OSS is unavailable, commands fall back to the
normal provider/API chain. Use `FUND_DATA_CACHE_DIR` to move the cache,
`FUND_DATA_DB` to force a specific SQLite file, or
`FUND_DATA_AUTO_PULL=0` to skip the OSS bootstrap.

Full database archives should be private. They keep `raw_responses`,
sync logs, and failure queues for audit/rebuild use, so do not publish
them through a public-read bucket or public prefix.

```bash
VERSION=$(date +%F-%H%M%S)
python3 fund-data/scripts/fund_cli.py cloud archive-full \
  --source-db fund-data/data/fund_data.sqlite \
  --output-dir dist/fund-data/full/$VERSION \
  --base-url oss://YOUR_PRIVATE_BUCKET/fund-data/full/$VERSION/ \
  --version $VERSION
```

Upload the generated private archive with `ossutil cp` only to a
private bucket or to objects with private ACL:

```bash
ossutil cp dist/fund-data/full/$VERSION/fund_data_full.sqlite.gz \
  oss://YOUR_PRIVATE_BUCKET/fund-data/full/$VERSION/
ossutil cp dist/fund-data/full/$VERSION/fund_data_full.sqlite.gz.sha256 \
  oss://YOUR_PRIVATE_BUCKET/fund-data/full/$VERSION/
ossutil cp dist/fund-data/full/$VERSION/manifest.json \
  oss://YOUR_PRIVATE_BUCKET/fund-data/full/$VERSION/
```

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
    ├── ARCHITECTURE.md     # contributor-facing layer / lifecycle reference
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
    │   ├── fund_mcp.py      # MCP stdio server
    │   ├── retry_failures.py
    │   ├── coverage_report.py
    │   ├── install_skill.py
    │   └── tests/           # 148 unittest cases
    └── data/                # SQLite DB + watchlist files (gitignored)
```

## Known gaps (tracked for 0.3.0)

These are the items the team is actively working through. They are
listed in priority order, not all of them are blockers.

1. **Holdings / bonds / industry coverage sits at 49 – 57 %** for
   AkShare's `fund_portfolio_*_em` endpoints. The remaining gap is
   mostly **后端 (B/C share) classes** that Eastmoney / AkShare
   don't expose holdings for. Profile is at 98.88 % via
   `scripts/investoday_profile_sync.py`; for higher-fidelity
   holdings, upgrade to Investoday's L2 portfolio-* set (see
   `PROVIDERS.md`) or wire a `TUSHARE_TOKEN`.
2. **Dividend (28 %) and split (2 %) coverage** is naturally low:
   most funds have never paid out / split. Don't treat the gap as
   a bug.
3. **Eastmoney has no `profile()` / `holdings()` / `fees()`**
   implementations on the direct provider; we fall back to
   AkShare for those capabilities, which adds AkShare's free-tier
   latency. The nightly `sync.yml` workflow reruns the gap queue
   daily.
4. **No HTTP/SSE MCP transport.** The current MCP server is stdio-only,
   which matches local agent clients. A Streamable HTTP wrapper can land
   later if remote clients need it.
5. **`--json` log flag on `fund_cli.py` is on the v0.3.0 backlog**.
   The `doctor` and `cloud build-bundle` / `cloud pull` subcommands
   already emit structured JSON; the per-fund commands (list,
   search, nav, snapshot, profile, …) still pretty-print for humans.

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
