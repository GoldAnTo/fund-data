# fund-data Architecture

> Last updated: 2026-06-03 (NAV read-through cache behavior).

This document is the contributor-facing architecture reference for the
`fund-data` skill. It is intentionally separate from `SKILL.md`
(agent-facing usage) and `PROVIDERS.md` (provider onboarding). If you
are reading code for the first time and want to know **where** a new
piece belongs, start here.

## 30-second overview

`fund-data` is a SQLite-backed local cache of Chinese public fund
data, fronted by three interchangeable surfaces:

1. **Python library** — `from scripts import fund_data` and call
   `fund_data.fetch_nav_history(...)` etc.
2. **CLI** — `fund-cli` (built from `scripts.fund_cli`) for human
   and cron use.
3. **MCP server** — `fund-mcp` (built from `scripts.fund_mcp`)
   for AI agents via the Model Context Protocol.

All three converge on a single store: `FundDataStore`, a thin
SQLite wrapper with WAL mode, 30 s busy timeout, and a versioned
migration registry.

## Layer diagram

```
                         ┌─────────────────────────────────┐
                         │     AI agent / cron / human     │
                         └────────────┬────────────────────┘
                                      │
                  ┌───────────────────┼───────────────────┐
                  ▼                   ▼                   ▼
            ┌──────────┐        ┌──────────┐        ┌──────────┐
            │ fund-cli │        │ fund-mcp │        │ library  │
            │  (CLI)   │        │  (MCP)   │        │  import  │
            └─────┬────┘        └─────┬────┘        └─────┬────┘
                  │                   │                   │
                  └───────────────────┼───────────────────┘
                                      │
                                      ▼
            ┌─────────────────────────────────────────────────┐
            │   scripts/fund_data.py (the only public API)    │
            │   - fetch_fund_list / search_funds              │
            │   - fetch_nav_history / fetch_snapshot           │
            │   - fetch_stock_holdings / fetch_bond_holdings  │
            │   - fetch_industry_allocations / fetch_*        │
            │   - batch_sync_funds (per-fund pipeline)        │
            │   - FundDataStore (storage)                     │
            │   - Provider hierarchy: Investoday → AkShare →  │
            │     Tushare → Eastmoney (free fallback)         │
            └─────────────┬──────────────────┬────────────────┘
                          │                  │
                          ▼                  ▼
            ┌──────────────────────┐  ┌──────────────────────┐
            │  data/fund_data.sqlite │  │  fund_cloud bundle    │
            │  (full audit-log DB)   │  │  (compressed query    │
            │                       │  │   DB, no raw log)     │
            └──────────────────────┘  └──────────────────────┘
```

Two storage tiers on purpose:

- **`fund_data.sqlite`** — the operator-facing full DB. Contains
  every table including `raw_responses`, `sync_runs`,
  `sync_failures`. This is what nightly-sync writes to.
- **`fund_cloud` bundle** (`fund_data_query.sqlite.gz` +
  `manifest.json` + `.sha256`) — a query-only subset of the
  same tables, stripped of the audit log. The team's CI runners
  and developers download this on day 1 instead of running the
  21 h AkShare backfill.

`fund_data.default_db_path()` prefers the cloud bundle when one
is configured, falling back to the local full DB.

`fund_data.fetch_nav_history()` is a read-through path. With no
`raw_text` or explicit `client`, it first reads `nav_history` from
the resolved SQLite DB (OSS query bundle when present, otherwise the
local DB). If no matching rows are present, or any matching row is
older than `cache_max_age_hours` (24 h by default), it falls through
to the provider chain and writes the refreshed rows back. Use
`cache=False` in Python or `fund_cli.py nav --refresh` to force a
provider refresh.

## Provider chain contract

Every fund-related query goes through one of four provider
implementations, all behind a common shape defined by
`AkshareProvider` (the most complete one):

| Provider        | Cost    | Speed   | Coverage                                 |
|-----------------|---------|---------|------------------------------------------|
| Investoday      | paid    | fast    | 48 L1+L2 fund endpoints, structured       |
| Tushare         | paid    | fast    | AkShare-only capabilities (profile etc.) |
| AkShare         | free    | slow    | Full surface, server-throttled            |
| Eastmoney       | free    | fast    | Snapshot + NAV only (best fallback)      |

`auto` (the default) builds a capability-specific chain. Search, fund
list, NAV, and snapshot use configured structured providers first,
then Eastmoney and AkShare. Profile, holdings, bonds, industries,
fees, dividends, splits, and managers use configured structured
providers first, then AkShare and Eastmoney. For NAV, this provider
chain is reached only after the local/OSS cache misses or is stale.

The contract every provider satisfies:

```python
class Provider:
    def fetch_fund_list(self, raw_text=None) -> list[dict]: ...
    def search_funds(self, keyword) -> list[dict]: ...
    def fetch_nav_history(self, code, *, start_date, end_date, page, per) -> list[dict]: ...
    def fetch_snapshot(self, code) -> dict: ...
    def fetch_stock_holdings(self, code, *, report_year=None) -> list[dict]: ...
    # ... (bonds, industries, fees, dividends, splits, managers)
```

The contract **does not** mandate how the data is fetched; each
provider may go HTTP, scrape, or call a third-party SDK. What it
**does** mandate is that every method returns a list of
flat-dict rows whose keys match the column names in
`fund-data/references/schema.md`. The normalizer in
`fund_data._normalize_*` papers over Akshare's bilingual
key aliases and the Eastmoney page scraper's HTML quirks.

Adding a new provider: see `fund-data/PROVIDERS.md`. The TL;DR
is — implement the methods you need, leave the rest to
`AkshareProvider`'s default behaviour (it walks through every
method and raises `NotImplementedError` for the ones you skip).

## `FundDataStore` lifecycle

`FundDataStore` is the only object that touches SQLite directly.
Every layer above (CLI, MCP, library, `backfill.py`,
`investoday_profile_sync.py`, `akshare_capability_backfill.py`)
talks to it.

The lifecycle on every connection:

```
FundDataStore(db_path)
  ├── connect()
  │     ├── Path(db_path).parent.mkdir(parents=True, exist_ok=True)
  │     │     # avoids 'unable to open database file' on fresh CI runners
  │     ├── sqlite3.connect(db_path, timeout=30)
  │     ├── PRAGMA journal_mode = WAL      # readers don't block writers
  │     ├── PRAGMA busy_timeout = 30000    # wait up to 30 s for write lock
  │     └── PRAGMA synchronous = NORMAL    # WAL default; durable enough
  ├── ensure_schema()
  │     ├── CREATE TABLE IF NOT EXISTS (12 tables)  # see schema.md
  │     ├── CREATE TABLE IF NOT EXISTS schema_migrations
  │     ├── current = PRAGMA user_version
  │     └── for migration in MIGRATIONS where version > current:
  │           fn(conn) → INSERT INTO schema_migrations
  │           → PRAGMA user_version = version
  └── ready
```

### Schema migration registry (P0.4)

The four historical column adds (`industry_allocations.market_value`,
`fee_structures.fee_text`, `discount_fee`, `discount_fee_text`)
were promoted into the module-level `MIGRATIONS` list in
`fund_data.py`:

```python
MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_add_industry_allocations_market_value),
    (2, _migration_002_add_fee_structures_fee_text),
    (3, _migration_003_add_fee_structures_discount_fee),
    (4, _migration_004_add_fee_structures_discount_fee_text),
]
FUND_DATA_SCHEMA_VERSION = max(version for version, _fn in MIGRATIONS)
```

Adding a new migration:

1. Append `(N, _migration_NNN_short_description)` to `MIGRATIONS`.
2. Add a regression test in `test_fund_data.py::SchemaMigrationTests`.
3. **Never** renumber or remove an existing migration — old DBs
   depend on each version being applied exactly once, in order.
4. Bump consumers that cache `FUND_DATA_SCHEMA_VERSION` (currently
   none).

If a migration fails mid-flight, the transaction rolls back and
`PRAGMA user_version` stays at the last successful version. Re-running
`ensure_schema` retries the failed migration automatically.

## Concurrency model

`fund_data.sqlite` is configured for:

- `journal_mode = WAL` — readers don't block writers, writers don't
  block readers. Single-writer still applies.
- `busy_timeout = 30000` — a writer that finds the DB locked
  waits up to 30 s instead of failing immediately.

The **production** write path is sequential (one `backfill.py`
process, one writer, optionally `--separate-db` to write to a
sibling DB and `ATTACH` + merge at the end). The **smoke-test**
path is a separate `FundDataStore` instance that points at
`/tmp/smoke_<pid>.sqlite` — never at the production DB.

The `backfill.py` retry path around `batch_sync_funds`:

```python
except sqlite3.OperationalError as exc:
    if "database is locked" not in str(exc):
        raise  # not a lock — re-raise
    if lock_attempt >= LOCK_RETRY_ATTEMPTS:
        raise  # give up after 3 attempts
    time.sleep(backoff + random.uniform(0, 1.0))
    # backoff = 2s, 4s, 8s + jitter
```

Only `OperationalError("database is locked")` is retried. Other
operational errors (e.g. `unable to open database file`,
`database is corrupt`) re-raise immediately so CI can fail loud.

## Entry points

| Surface  | Entry script            | Console script  | Use case                  |
|----------|-------------------------|------------------|---------------------------|
| CLI      | `scripts/fund_cli.py`   | `fund-cli`       | humans + cron             |
| MCP      | `scripts/fund_mcp.py`   | `fund-mcp`       | AI agents (Claude, etc.)  |
| Library  | `scripts/fund_data.py` | —                | embed in another Python app |
| Backfill | `scripts/backfill.py`   | `fund-backfill`  | nightly-sync worker        |
| Doctor   | `scripts/doctor.py`     | `fund-doctor`    | CI gate                   |
| Cloud    | `scripts/fund_cloud.py` | —                | team DB distribution       |

Every console script is declared in `pyproject.toml` under
`[project.scripts]`. The package layout is `fund-data/scripts/*.py`,
exposed to Python as the `scripts` package via
`[tool.setuptools].package-dir`.

## Adding a new MCP tool

1. In `fund_mcp.py`, add a new `_tool(...)` entry to `TOOLS`.
2. Wire a handler in `TOOL_HANDLERS` that calls into
   `fund_data.fetch_*` (do not re-implement the call).
3. Update the `description` so an LLM can pick it.
4. Add a test in `test_fund_mcp.py` that asserts the tool
   appears in `tools/list`.

## Adding a new CLI subcommand

1. In `fund_cli.py::build_parser`, add a new subparser via
   `subparsers.add_parser("<name>")`.
2. Add a dispatcher branch in `main()` that calls
   `fund_data.fetch_*` and `_print_json(...)`.
3. Reuse `_add_common_db_arg`, `_add_offline_arg`,
   `_add_provider_arg` to inherit the common flags.
4. The top-level `--quiet` / `--log-level` flags already apply
   to every subcommand — no per-subcommand work needed.
5. Add a test in `test_fund_cli.py`.

## What this doc does NOT cover

- Per-API capability mapping (read `fund-data/INVESTODAY_FUND_API_CATALOG.md`).
- Per-subcommand behaviour (read `fund-data/SKILL.md`).
- CI / nightly workflow internals (read `fund-data/.github/workflows/*.yml`).
- Skill install / refresh (read `fund-data/SKILLS.md`).
- Backfill performance tuning (read `fund-data/AGENTS.md`).
- Backfill data quality notes (read `fund-data/CHANGELOG.md`).
