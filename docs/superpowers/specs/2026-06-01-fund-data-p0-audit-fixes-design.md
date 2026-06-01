# Fund Data P0 Audit Fixes — Design

## Goal

Resolve the five P0 gaps surfaced by the 2026-06-01 audit of `fund-data`, ordered by impact-to-cost. Once these land, the nightly sync and core backfill paths have real test coverage and the doctor can finally gate CI as designed.

## Background

The audit identified twelve gaps total. Five were rated P0 (would bite again soon); the rest are tracked separately.

| # | Gap | Why P0 |
|---|---|---|
| 1 | `sync.yml` doctor step has `\|\| true`, swallowing doctor failures | Doctor's stated contract is "exit non-zero to gate CI." Currently the gate doesn't bite. |
| 2 | `backfill.py` OperationalError retry path (commit `4b0de13`) has no test | The 5h-progress-killing bug is un-guarded — next refactor silently drops the retry. |
| 3 | `akshare_capability_backfill` and `investoday_profile_sync` have zero tests | Only path that lifts AkShare coverage from 1.8 % to 98.87 %; no regression net. |
| 4 | No `PRAGMA user_version` / migration registry in `FundDataStore.ensure_schema` | v0.2 column adds have no way to write a "old DB → new column exists" test. |
| 5 | Nightly sync has no real failure notification (only an `::error::` echo) | `sync_failures` table has 148 stale rows nobody noticed. |

## Scope

In scope:

- One CI workflow edit (`sync.yml`) to restore doctor gating
- New test file content (extensions to existing test files + 2 new files)
- Schema migration registry inside `FundDataStore.ensure_schema` (4 existing `_ensure_column` calls promoted to a migration list)
- One new CI step to auto-open an issue on nightly failure

Out of scope (deferred to a follow-up round):

- `fund_cli.py --quiet` / `--log-level` (P1)
- Coverage gate in CI (P1)
- `ARCHITECTURE.md` (P1)
- SCA / CVE scanning (P1)
- `install_skill --include-data` IP-leak warning (P2)
- 3.7 GB SQLite team-sync workflow (P2)
- WAL-page path coverage in `test_install_skill` (P2)

## Components

### P0.1 — Restore doctor CI gating

- File: `fund-data/.github/workflows/sync.yml` lines 115-117
- Change: drop `\|\| true` on the doctor invocation; add a second step that `grep '"ok": false' data/doctor-post.log` and exits 1 on any non-ok check.
- Why: the doctor already exits non-zero on failed checks (per its docstring). Removing `\|\| true` is the minimum fix; the grep is a belt-and-suspenders against any future check that prints a failure but exits 0.

### P0.2 — Test the OperationalError retry

- File: `fund-data/scripts/tests/test_backfill.py`
- Add two tests using `unittest.mock.patch("time.sleep")` to skip the 14 s of real backoff:
  - `test_backfill_retries_on_lock`: patch `batch_sync_funds` to raise `OperationalError("database is locked")` twice, then succeed. Assert the run completes successfully and `time.sleep` was called twice.
  - `test_backfill_aborts_after_three_lock_failures`: raise four times. Assert the summary records `completed=0` and the run returns non-zero.
- Also assert: a non-lock `OperationalError` (e.g. `"unable to open database file"`) is **not** caught by the retry path — it re-raises.

### P0.3 — Smoke tests for the two long-runner scripts

- New file: `fund-data/scripts/tests/test_akshare_capability_backfill.py`
  - `test_main_writes_to_separate_db_and_merges`: 5 fake funds through `--separate-db` mode; assert all six MERGE_TABLES are populated after `_merge_separate_db()` runs.
  - `test_merge_copies_all_rows`: unit test on `_merge_separate_db()` alone, asserting the `{table: rows_copied}` count matches the source.
- New file: `fund-data/scripts/tests/test_investoday_profile_sync.py`
  - `test_main_upserts_all_catalog_entries`: mock `InvestodayProvider._get_catalog` to return 5 fake profiles; assert `db.funds` upsert count equals catalog size.
  - `test_main_continues_on_per_fund_failure`: mock one fund to raise; assert the other four still land.

### P0.4 — Schema version / migration registry

- File: `fund-data/scripts/fund_data.py` (around `ensure_schema`, currently lines ~1744-1906)
- Refactor:
  - Add `schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)` table.
  - Add a module-level `MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]]` list.
  - Promote the four existing `_ensure_column` calls into named migrations `(1, _add_foo_column), (2, _add_bar_column), ...`
  - `ensure_schema` now: read `PRAGMA user_version`; for each migration with `version > current_version`, run it inside a transaction, then insert into `schema_migrations` and `PRAGMA user_version = migration.version`.
- New tests in `test_fund_data.py`:
  - `test_ensure_schema_is_idempotent`: call `ensure_schema()` twice, assert `user_version` is unchanged on the second call.
  - `test_ensure_schema_runs_migrations_in_order`: simulate a v0 DB, run `ensure_schema`, assert the new columns exist.
  - `test_migrations_skip_already_applied`: simulate a v0.2 DB, re-run `ensure_schema`, assert no migration is re-applied (and `user_version` doesn't bump).
- Constraint: the four existing column additions must continue to be a no-op on already-migrated databases. No data is dropped or rewritten.

### P0.5 — Nightly failure notification

- File: `fund-data/.github/workflows/sync.yml`
- Append a new step at the end of the nightly-sync job, gated on `if: failure()`:
  - Use `actions/github-script@v7` to open an issue titled `🔴 nightly-sync failed at <ISO timestamp>`.
  - Body links to the failed run URL and mentions the `sync_failures` table.
  - Label the issue so it can be triaged / auto-closed.
- Why: `slackapi/slack-github-action` and similar require a repo secret webhook; auto-opening an issue is the zero-config equivalent that still creates a durable signal in the repo.

## Data Flow

P0.4 changes how `FundDataStore` initializes. The flow becomes:

```
FundDataStore(db_path)
  ├── connect
  ├── PRAGMA journal_mode = WAL
  ├── PRAGMA busy_timeout = 30000
  ├── ensure_schema
  │   ├── CREATE TABLE IF NOT EXISTS funds / nav_history / ... (same as today)
  │   ├── CREATE TABLE IF NOT EXISTS schema_migrations
  │   ├── current_version = PRAGMA user_version
  │   └── for migration in MIGRATIONS where version > current_version:
  │         BEGIN TRANSACTION
  │         migration.fn(conn)
  │         INSERT INTO schema_migrations
  │         PRAGMA user_version = migration.version
  │         COMMIT
  └── ready
```

No other code path needs to change. The first run on an existing 26,936-fund DB will apply the four migrations sequentially and bump `user_version` to 4.

## Error Handling

- P0.1: doctor now exits non-zero → CI red. Acceptable: this is the contract that was always intended.
- P0.2: lock retry covers `OperationalError("database is locked")` only. Other `OperationalError` subclasses (e.g. `unable to open database file`) re-raise as before. The test pins this behavior.
- P0.3: per-fund failure in a long runner must not abort the whole run. The new test asserts partial success.
- P0.4: a migration that fails mid-transaction rolls back. `user_version` stays at the last successful version. On next `ensure_schema`, the failed migration retries.
- P0.5: the issue-opener step is itself wrapped in `continue-on-error: true` so a notification failure doesn't mask the original nightly failure.

## Testing

- P0.1: cannot be unit-tested. Manual verification by intentionally breaking a doctor check and watching the workflow turn red.
- P0.2: 2 new test cases in `test_backfill.py` using `mock.patch("time.sleep")` to keep the test fast.
- P0.3: 4 new test cases across 2 new test files, using `tmp_path` fixtures and a stub `InvestodayProvider`.
- P0.4: 3 new test cases in `test_fund_data.py`, plus an integration check that the existing 26k-fund DB still loads cleanly after the refactor.
- P0.5: cannot be unit-tested. Manual verification by deliberately failing the nightly job.

Total new tests: 6. Total test count after this round: 112 (from 106).

## Implementation Order

```
P0.1 (5 min)  →  P0.2 (1 h)  →  P0.5 (1-2 h)  →  P0.3 (half day)  →  P0.4 (half day)
  ^trivial     ^low risk      ^low risk        ^new files only    ^core change, last
```

P0.4 is intentionally last because it touches the core storage layer; by the time we get to it, P0.1-P0.5 have shipped and any regression will surface fast in CI.

Each P0 lands as its own commit + push. No squashing.
