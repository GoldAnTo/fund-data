# Fund Coverage Pipeline

> **Last updated:** 2026-06-02
> **Source of truth:** `fund-data/scripts/fund_data.py:3141-3185`
> (`coverage_report`), `fund-data/scripts/fund_data.py:3133-3138`
> (`coverage_rows`), `fund-data/scripts/fund_data.py` (the
> `FundDataStore.coverage_rows` method),
> `fund-data/scripts/coverage_report.py` (the renderer),
> `fund-data/scripts/doctor.py` (the health gate).
> **For:** Anyone — human or AI — who needs to understand how
> `fund-data` measures data completeness per fund and per
> dataset, and what the two report modes (coverage / stale)
> actually compute. The companion to
> [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md)
> (which is the writer) and
> [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md)
> (which is the distribution path that lands a fresh agent at
> a known coverage state).

Coverage is the **read-only introspection layer** of
`fund-data`. It is how an agent answers "is the data I have
good enough for this question?" without writing any rows. Two
report modes:

- **Coverage mode** — per-dataset coverage % over the fund
  universe, plus a per-fund completeness score (0-1) and a
  per-fund `missing` list of empty dataset names. Used by
  `fund_coverage_report` MCP, `fund-cli coverage`, and
  `examples/coverage_report.py`.
- **Stale mode** — funds whose newest snapshot or NAV is
  older than `--max-age-hours`, or that have neither.
  Used by `coverage_report.py --stale` and by the operator's
  "did the nightly backfill skip something?" review.

The output is a list of dicts that both humans (markdown /
table renderer) and agents (JSON renderer) consume. The
schema is stable; the team's downstream tools depend on the
shape.

---

## 1. End-to-end flow (Mermaid)

```mermaid
flowchart TD
    A[Agent / operator asks<br/>'is the data complete?'] --> B{Entry point}

    B -- fund_coverage_report MCP tool --> C[coverage_report<br/>fund_data.py:3141]
    B -- fund_coverage MCP tool --> D[coverage_rows<br/>fund_data.py:3133]
    B -- fund-cli coverage --> C
    B -- coverage_report.py CLI --> C
    B -- examples/coverage_report.py --> C
    B -- doctor.py health gate --> D
    B -- coverage_report.py --stale --> E[stale_rows<br/>coverage_report.py:189]

    C --> F[Normalize codes if provided]
    F --> G[Build WHERE clause:<br/>codes filter + fund_type filter]
    G --> H[Open FundDataStore<br/>use default_db_path]
    H --> I[Run single SQL query:<br/>LEFT JOIN 8 tables<br/>case when ... is null then 0 else 1]
    I --> J[For each row:<br/>compute completeness = present / 8<br/>compute missing = datasets where 0]
    J --> K[Apply only_incomplete / min_completeness / limit]
    K --> L[Return list of dicts:<br/>{fund_code, fund_name, fund_type, has_profile,<br/>nav_rows, stock_holding_rows, ...,<br/>completeness, missing}]

    L --> M{Output format?}
    M -- markdown --> N[Markdown table renderer<br/>coverage_report.py:102]
    M -- json --> O[JSON renderer<br/>coverage_report.py:155]
    M -- table --> P[Fixed-width table renderer<br/>coverage_report.py:167]
    M -- MCP structuredContent --> Q[FundDataStore rows<br/>+ count in envelope]

    D --> R[FundDataStore.coverage_rows<br/>fund_code optional]
    R --> S[Return per-fund dataset summary:<br/>{fund_code, profile, nav, holdings, ...}]

    E --> T[Direct SQL:<br/>max(fetched_at) from snapshots<br/>max(fetched_at) from nav_history]
    T --> U[Filter: last_snapshot < cutoff<br/>or last_nav < cutoff or null]
    U --> V[Order by oldest first]
    V --> W[Return list of dicts]
```

## 2. End-to-end flow (ASCII fallback)

```
┌──────────────────────────────────────────────────────────────┐
│  Caller: "how complete is the data?"                          │
│  · MCP tool fund_coverage / fund_coverage_report              │
│  · CLI: fund-cli coverage / coverage_report.py                │
│  · doctor.py (health gate uses coverage_rows)                 │
│  · examples/coverage_report.py                                │
└────────────────────────┬─────────────────────────────────────┘
                         │
            ┌────────────┴────────────┐
            │                         │
   coverage mode                stale mode
   ─────────────                ──────────
   coverage_report()            _stale_rows()
   fund_data.py:3141            coverage_report.py:189
            │                         │
            ▼                         ▼
   ┌─────────────────────┐   ┌──────────────────────────┐
   │ 1. Normalize codes   │   │ 1. cutoff = now - max_age│
   │ 2. Build WHERE:      │   │ 2. SQL:                  │
   │    codes IN (...)    │   │    max(fetched_at)       │
   │    fund_type LIKE ?  │   │      from snapshots      │
   │ 3. limit clause      │   │    max(fetched_at)       │
   │ 4. Open FundDataStore│   │      from nav_history    │
   │ 5. Single SQL:       │   │ 3. having last < cutoff  │
   │    LEFT JOIN 8 tables│   │      or null              │
   │    case when is null │   │ 4. order by oldest first │
   │      then 0 else 1   │   │ 5. limit ?               │
   │ 6. Per row:          │   └──────────┬───────────────┘
   │    completeness =    │              │
   │      present / 8     │              ▼
   │    missing = []      │   ┌──────────────────────────┐
   │ 7. Apply only_incomp │   │ Return list of {          │
   │    / min_complete    │   │   fund_code, fund_name,   │
   │    / limit           │   │   fund_type, last_snapshot│
   └──────────┬────────────┘   │   last_nav                │
              │                │ }                        │
              │                └──────────────────────────┘
              ▼
   ┌──────────────────────────────────────┐
   │ List of dicts:                       │
   │ {                                    │
   │   fund_code, fund_name, fund_type,   │
   │   has_profile,                       │
   │   nav_rows, stock_holding_rows,      │
   │   bond_holding_rows, industry_rows,  │
   │   fee_rows, dividend_rows,           │
   │   split_rows,                        │
   │   manager_rows,                      │
   │   completeness, missing              │
   │ }                                    │
   └──────────┬───────────────────────────┘
              │
       ┌──────┴──────┐
       │             │
   renderer      MCP envelope
   ──────────    ─────────────
   markdown      content[0].text
   json          structuredContent
   table         isError
```

---

## 3. The eight datasets and how coverage is measured

`fund-data/scripts/fund_data.py:3141-3185`

`coverage_report` measures **eight datasets** per fund, each
with a `case when ... is null then 0 else 1` SQL check:

| Dataset | Source | Coverage check |
|---|---|---|
| `profile` | `fund_profiles.fund_code` | `case when p.fund_code is null then 0 else 1` |
| `nav` | `nav_history.fund_code` | count of nav rows > 0 |
| `stock_holdings` | `stock_holdings.fund_code` | count > 0 |
| `bond_holdings` | `bond_holdings.fund_code` | count > 0 |
| `industries` | `industry_allocations.fund_code` | count > 0 |
| `fees` | `fee_structures.fund_code` | count > 0 |
| `dividends` | `dividends.fund_code` | count > 0 |
| `splits` | `splits.fund_code` | count > 0 |

`fund_managers` is **not** part of the 8-dataset coverage
score — it is in the SQL output (`manager_rows`) but does
not count toward `completeness`. The team's reasoning is
that manager data is a directory lookup, not a per-fund
data plane; including it in the completeness score would
penalise funds whose manager records have moved to a
different fund.

The 8-dataset score is the **8-dataset weighted average**:
each present dataset contributes `1/8` to the score. A
fund with profile + nav + stock_holdings + fees but no
bond_holdings / industries / dividends / splits has
`completeness = 0.5` and `missing = ["bond_holdings",
"industries", "dividends", "splits"]`.

### 3.1 What "naturally sparse" means

Some datasets are expected to be empty for some fund
types:

- **`stock_holdings`** for `债券型` (bond funds), `货币型`
  (money-market funds), `指数型-固收` (index-bond funds),
  `REITs` — these funds do not hold equities by design.
- **`bond_holdings`** for `股票型` (stock funds) and pure
  `指数型-股票` (stock-index funds) — these funds do not
  hold bonds by design.
- **`dividends` / `splits`** for most funds — most funds
  do not pay dividends or split.

The "naturally sparse" datasets inflate the per-dataset
coverage % (e.g. 49 % global stock_holdings coverage looks
low until you exclude the 12k funds that legitimately have
no equity holdings). The team documents this in
[`fund-data/AGENTS.md`](../../fund-data/AGENTS.md) §Coverage
by `fund_type`.

`coverage_report` does not know about fund_type when
computing the score; it just reports "is the row present
or not". A downstream agent that wants the
fund-type-aware coverage should filter by `fund_type` in
the `WHERE` clause (`--fund-type 股票型` on the CLI).

### 3.2 What "stale" means

`coverage_report.py:189-228`

A fund is **stale** if either:
- The newest `snapshots.fetched_at` is older than
  `cutoff = utc_now() - max_age_hours`, **or**
- The newest `nav_history.fetched_at` is older than
  cutoff, **or**
- Either timestamp is null (no row at all).

The default `max_age_hours` is 24.0. The `cutoff` is
compared as an ISO 8601 string; the database is responsible
for the ordering.

The stale mode is **per-fund**, not per-dataset. A fund
whose stock_holdings are 6 months stale but whose NAV is
fresh is "stale" overall. The team has considered a
per-dataset staleness view; the v0.3.0 backlog tracks it.

---

## 4. The five entry points

| Surface | Code | What it returns |
|---|---|---|
| `fund_coverage_report` MCP tool | `fund-data/scripts/fund_mcp.py:249-260` | Same shape as `coverage_report` Python helper. Returns the list of dicts in `structuredContent.rows` and JSON in `content[0].text`. |
| `fund_coverage` MCP tool | `fund-data/scripts/fund_mcp.py:244-248` | Per-fund dataset summary from `FundDataStore.coverage_rows(fund_code=...)`. Returns one row per fund. |
| `fund-cli coverage` | `fund-data/scripts/fund_cli.py` | `coverage_report` Python helper, JSON output. |
| `coverage_report.py` | `fund-data/scripts/coverage_report.py:326` | Markdown / JSON / table renderer; `--stale` for the stale mode. |
| `examples/coverage_report.py` | `examples/coverage_report.py` | One-screen agent example; prints markdown. |
| `doctor.py` | `fund-data/scripts/doctor.py` | Reads `coverage_rows` for the health gate; emits a JSON report. |

The **canonical Python entry point** is
`fund_data.coverage_report(...)` at line 3141. All other
surfaces wrap this or the per-fund `coverage_rows`.

---

## 5. The result shape

### 5.1 `coverage_report` (per-fund, with completeness)

```json
{
  "fund_code": "110022",
  "fund_name": "易方达消费行业",
  "fund_type": "股票型",
  "has_profile": 1,
  "nav_rows": 1245,
  "stock_holding_rows": 87,
  "bond_holding_rows": 0,
  "industry_rows": 18,
  "fee_rows": 4,
  "dividend_rows": 12,
  "split_rows": 0,
  "manager_rows": 3,
  "completeness": 0.625,
  "missing": ["bond_holdings", "splits"]
}
```

The `completeness` value is in `[0, 1]`; the `missing`
list is a sorted list of dataset names. An empty `missing`
list means all 8 datasets are present.

### 5.2 `coverage_rows` (per-fund, dataset summary)

`fund-data/scripts/fund_data.py:3133-3138`

Wraps `FundDataStore.coverage_rows(fund_code=...)`. Returns
a list of dicts with `{fund_code, has_profile, nav_rows,
stock_holding_rows, ...}` (no completeness, no missing
list). The MCP `fund_coverage` tool uses this shape.

### 5.3 Stale mode

```json
{
  "fund_code": "000002",
  "fund_name": "某基金",
  "fund_type": "股票型",
  "last_snapshot": "2026-05-15T03:00:00+00:00",
  "last_nav": "2026-05-15T03:00:00+00:00"
}
```

A null `last_snapshot` or `last_nav` means the fund has
no row in that table at all. The cutoff is the implicit
field; the caller knows it from `--max-age-hours`.

---

## 6. Decision points an agent should know

| Question | Default | Override | What changes |
|---|---|---|---|
| Which DB? | `default_db_path()` (OSS cache or local) | `db="/abs/path/fund_data.sqlite"` argument | The `coverage_report` Python helper accepts `db_path=`; the CLI accepts `--db`. |
| Which funds? | All funds in the table | `codes=["110022", "110022"]` or `--fund-type 股票型` | `codes` is exact match; `fund_type` is substring LIKE. |
| Only incomplete funds? | No | `only_incomplete=True` or `--only-incomplete` | Filters out rows where `missing == []`. |
| Min completeness? | 0.0 | `min_completeness=0.5` | Filters out rows with `completeness < 0.5`. |
| Limit? | All | `limit=N` or `--limit N` | Caps the number of rows. |
| Stale threshold? | 24 hours | `--max-age-hours 12` (or `--max-age-hours 168` for weekly) | Used only by the stale mode. |
| Output format? | markdown (CLI) / structuredContent (MCP) | `--format json` / `--format table` | The MCP path always returns JSON-shaped envelope; the CLI picks. |
| How do I get the aggregated per-dataset %? | The markdown / JSON renderer computes it | (no override) | The aggregation is over the rows that the filters produced, not the whole universe. The number is in the report header. |
| How do I know the whole universe size? | `total_funds` in the markdown header | `SELECT COUNT(*) FROM funds` | The renderer calls `_safe_count`; the agent can run the SQL directly. |

---

## 7. Common agent misuses

1. **Treating the per-dataset % as a "global coverage"
   number.** The markdown header says
   "funds: 26936" but the per-dataset % is over
   "reported" (the rows the filters produced). If you
   pass `codes=[...]`, the % is over your subset, not
   the universe. The team's guidance is to run with
   no filter for "global" and use `fund_type` for
   "by-type".

2. **Assuming `manager_rows` counts toward completeness.**
   It does not. The 8-dataset score excludes fund
   managers; the column is in the output for
   introspection only. A fund with no manager row
   has `completeness = 1.0` if all 8 datasets are
   present.

3. **Running coverage against a half-built DB.** The
   SQL is `LEFT JOIN` 8 tables, which means a fund
   with no rows in *any* of them returns with all
   eight 0s and `completeness = 0`. An agent that
   runs coverage immediately after `fund_list` (which
   only populates `funds`) will see a 0 % universe.
   The expected sequence is: `fund_list` → `backfill`
   → `coverage`.

4. **Treating "naturally sparse" datasets as bugs.**
   49 % stock_holdings coverage over 27k funds is
   100 % over the funds that *should* have stock
   holdings. The fund_type breakdown in AGENTS.md
   shows the gap is structural, not a coverage miss.
   `coverage_report` does not know about fund_type
   when computing the score; an agent that wants the
   type-aware view should filter.

5. **Hiding staleness behind a single threshold.**
   `--max-age-hours 24` is the default, but a 24-hour
   threshold is too coarse for a nightly backfill that
   runs at 03:00. A fund that was last refreshed at
   03:00 today is "stale" by 03:00 tomorrow morning,
   even though the data is one day old. The team's
   guidance is `--max-age-hours 36` for daily
   backfills.

6. **Confusing `coverage_report` with `fund_export`.**
   `fund_export table=funds` returns the raw `funds`
   rows; `coverage_report` returns per-fund
   completeness with a derived `completeness` score
   and a `missing` list. They are different shapes
   for different questions. An agent that wants "is
   this fund in the DB?" should use `fund_export
   table=funds fund_code=...`; an agent that wants
   "how complete is the data for this fund?" should
   use `fund_coverage_report`.

7. **Trusting the doctor's coverage report as the
   operational truth.** `doctor.py` reports the
   on-disk DB; a long-running backfill that writes to
   the cache DB will diverge. The `fund-data/AGENTS.md`
   §Long-running pitfalls note this as the most
   common "wrong DB" report.

8. **Hiding dataset_errors in the report.** A fund
   with `status: "ok"` + non-empty `dataset_errors`
   is a partial success; its coverage will show the
   gap, but the operator will not know the gap was
   a soft failure (not a hard failure). An agent that
   consumes coverage should cross-reference
   `sync_runs` for the audit trail.

9. **Forgetting `--limit` on the markdown renderer.**
   The markdown renderer shows the top 10 most-
   incomplete funds; the table renderer shows up to
   200. An agent that wants the full list should
   pass `--format json` and `--limit 0` (or no
   limit).

10. **Calling coverage immediately after a
    `cloud build-bundle` without re-pointing at the
    cache.** The build is a build; it does not
    update the local `fund-data/data/fund_data.sqlite`.
    An agent that wants coverage of the new bundle
    should `cloud pull` first.

---

## 8. Code anchors (cheat-sheet)

| Step | File:line |
|---|---|
| `coverage_rows` (per-fund) | `fund-data/scripts/fund_data.py:3133` |
| `coverage_report` (per-fund + completeness) | `fund-data/scripts/fund_data.py:3141` |
| `FundDataStore.coverage_rows` | `fund-data/scripts/fund_data.py` (search inside `FundDataStore` class) |
| `fund_coverage` MCP tool | `fund-data/scripts/fund_mcp.py:244` |
| `fund_coverage_report` MCP tool | `fund-data/scripts/fund_mcp.py:249` |
| `fund-cli coverage` subcommand | `fund-data/scripts/fund_cli.py` (search inside) |
| `coverage_report.py` (renderer) | `fund-data/scripts/coverage_report.py` |
| Coverage mode `_coverage_rows` | `fund-data/scripts/coverage_report.py:87` |
| Stale mode `_stale_rows` | `fund-data/scripts/coverage_report.py:189` |
| Markdown renderer | `fund-data/scripts/coverage_report.py:102` |
| JSON renderer | `fund-data/scripts/coverage_report.py:155` |
| Table renderer | `fund-data/scripts/coverage_report.py:167` |
| Stale markdown renderer | `fund-data/scripts/coverage_report.py:231` |
| Stale JSON renderer | `fund-data/scripts/coverage_report.py:255` |
| `examples/coverage_report.py` | `examples/coverage_report.py` |
| `doctor.py` (uses `coverage_rows`) | `fund-data/scripts/doctor.py` (search inside) |
| Coverage by `fund_type` reference | `fund-data/AGENTS.md` §Coverage by `fund_type` |
| Naturally sparse vs fixable gap analysis | `docs/superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md` |

---

## 9. Maintenance

When you change any of the following, this document is stale:

- The list of 8 datasets in `coverage_report` (a
  dataset added or removed) → update §3 and the
  completeness definition.
- The staleness threshold default (currently 24
  hours) → update §3.2 and §6.
- A new entry point is added (e.g. a
  `fund_coverage_diff` MCP tool that compares two
  snapshots) → update §4.
- The output shape changes (a field added/removed
  in the dict) → update §5.
- A new filter is added (e.g. `min_manager_rows`)
  → update §6.

Open a PR with the diagram update alongside the code
change. The Mermaid block is the contract; the ASCII
block is the verification target. If they disagree,
ASCII wins.
