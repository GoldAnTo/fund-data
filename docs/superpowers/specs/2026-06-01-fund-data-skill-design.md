# Fund Data Skill Design

## Goal

Build a Codex skill that helps agents search Chinese public funds, fetch core fund data, and persist fetched results into a local SQLite data base for repeatable research workflows.

## Shape

The skill follows the useful parts of `simonlin1212/a-stock-data`: clear activation wording, source priority, runnable Python helpers, and local persistence. It differs by keeping `SKILL.md` thin and moving deterministic code into `scripts/`, so later agents can run and test code instead of copying large Markdown snippets.

## Scope

First version supports:

- Search funds by name, pinyin, keyword, or 6-digit code.
- Fetch historical NAV rows from Eastmoney public fund pages.
- Fetch snapshot metadata from Eastmoney `pingzhongdata`.
- Persist search results, fund metadata, NAV rows, raw responses, and sync runs into SQLite.
- Provide a CLI for `search`, `nav`, `snapshot`, `sync`, and `export`.
- Leave an Investoday adapter hook for later API-key-backed sources.

Out of scope for the first version:

- Formal fund recommendation or personalized investment advice.
- Full holdings, benchmark, manager, and disclosure parsing.
- A web UI.

## Data Sources

Primary no-key sources:

- Eastmoney fund suggestion API for search.
- Eastmoney fund code list for broad local indexing.
- Eastmoney F10 historical NAV HTML endpoint.
- Eastmoney `pingzhongdata/{code}.js` for fund snapshot variables.

Optional future source:

- Investoday financial data API when `INVESTDATA_API_KEY` is available.

## Persistence

SQLite lives at `fund-data/data/fund_data.sqlite` by default. The caller can override it with `FUND_DATA_DB`.

Tables:

- `funds`: one row per fund code.
- `nav_history`: one row per fund code and NAV date.
- `snapshots`: one row per fund code fetch.
- `raw_responses`: raw response snippets keyed by source and request key.
- `sync_runs`: audit log for CLI operations.

## Error Handling

Network calls use standard-library `urllib` and browser-like headers so the first version has no third-party runtime dependency. Parsers must accept saved raw payloads in tests. CLI commands return non-zero exit codes for invalid inputs or failed fetches.

## Testing

Use Python `unittest` with static payload fixtures. Tests cover parsing, SQLite upserts, and CLI offline paths. Live network smoke checks are separate and not required for normal test pass.

