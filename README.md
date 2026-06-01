# Fund Data

A local Chinese public fund data base. Wraps a few no-key Eastmoney
endpoints, an optional AkShare fallback, and a structured Investoday
adapter behind one Python skill so agents (or a developer) can search,
fetch, persist, and export fund data without re-deriving the parsing
logic every time.

> Codex skill home: `fund-data/SKILL.md`.
> Design spec: `docs/superpowers/specs/2026-06-01-fund-data-skill-design.md`.
> Implementation plan: `docs/superpowers/plans/2026-06-01-fund-data-skill.md`.

## Status

| | |
|---|---|
| Core library | `fund-data/scripts/fund_data.py` (≈2.8k lines) |
| CLI | `fund-data/scripts/fund_cli.py` |
| Tests | 39 unit tests, all green (`python3 -m unittest discover fund-data`) |
| Default DB | `fund-data/data/fund_data.sqlite` (gitignored; rebuild on first run) |
| Providers | Eastmoney (no key) → AkShare (optional) → Investoday (key) |

The skill is feature-complete against the v1 spec. The data base is
intentionally empty on first clone — populate it with the CLI commands
below. See "Known gaps" for the work that is still in flight.

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

# 5. Inspect coverage and export.
python3 fund-data/scripts/fund_cli.py coverage --fund-code 110022
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
python3 -m unittest discover scripts
```

The test suite uses static Eastmoney/AkShare fixtures — no network
required.

## Project layout

```
.
├── .gitignore
├── README.md                  # you are here
├── requirements.txt
├── docs/
│   └── superpowers/
│       ├── specs/             # design documents
│       └── plans/             # implementation plans
└── fund-data/                 # the Codex skill
    ├── SKILL.md               # agent entrypoint
    ├── agents/openai.yaml
    ├── references/schema.md   # SQLite schema reference
    ├── scripts/
    │   ├── fund_data.py       # parsers, providers, store, sync helpers
    │   ├── fund_cli.py        # CLI wrapper
    │   └── tests/             # unittest suite
    └── data/                  # SQLite DB + watchlist files (gitignored)
```

## Known gaps

These are the items the team is actively working through. They are
listed in priority order, not all of them are blockers.

1. **fund_profiles coverage is 0.03%.** Only the 8 sample funds have a
   full profile, NAV history, and holdings. A backfill script for the
   rest of the 26,936 funds is the next task.
2. **38 stale entries in `sync_failures`.** All share the same root
   cause: auto mode silently dropped `akshare` from the chain when it
   was not installed. Fixed in commit history; rerun `batch-sync` on
   the failed codes after pulling the latest code.
3. **No CI.** Tests run locally only. Adding a GitHub Actions workflow
   that runs the unittest suite on push is on the roadmap.
4. **No scheduled sync.** No cron / launchd entry yet — fund data is
   only as fresh as the last manual run.
5. **Splits table is sparse.** Most funds have not split; a small
   audit is needed to confirm the empty rows are correct, not missing.

## Safety

- Fund data is for research only. Do not use it as personalized
  investment advice.
- Always report the `source` column and `fetched_at` timestamp when
  quoting numbers.
- The CLI defaults to one request per second; do not lower the
  interval or you will be rate-limited by the public endpoints.
