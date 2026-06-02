# Fund Coverage Playbook

> **Last updated:** 2026-06-02
> **Audience:** Anyone — human or AI — who gets asked "how do I
> measure data completeness?", "is the backfill done yet?",
> "which funds are missing NAV?", or "what does the 49 % stock
> holdings coverage mean?". This is the **answer script** for
> the read-only introspection layer. Pair with
> [`fund-coverage-pipeline.md`](./fund-coverage-pipeline.md)
> for diagrams and code anchors.
>
> **Use it when:**
> - Onboarding a new operator or agent to the data plane.
> - Reviewing a PR that touches `coverage_report`,
>   `coverage_report.py`, or `doctor.py`'s coverage
>   section.
> - Debugging a report of "the backfill said ok=27000
>   but I have no data" or "the doctor says 100 % but
>   the agent says 49 %".
> - Fielding a question about "naturally sparse"
>   datasets vs fixable gaps.
> - Planning a new dataset addition.
>
> **Do NOT use it when:**
> - The question is about the writer (backfill) →
>   use [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md).
> - The question is about the distribution path →
>   use [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md).
> - The question is about a single fund's data →
>   use [`fund-search-playbook.md`](./fund-search-playbook.md).

---

## TL;DR (60-second answer)

Coverage in `fund-data` is the **read-only introspection
layer** that answers "is the data good enough for this
question?" without writing any rows. There are two report
modes:

- **Coverage mode** — per-dataset coverage % over the fund
  universe, plus a per-fund **completeness score** in `[0, 1]`
  and a per-fund `missing` list. The score is the equal-
  weighted average of 8 datasets (`profile`, `nav`,
  `stock_holdings`, `bond_holdings`, `industries`, `fees`,
  `dividends`, `splits`). `fund_managers` is reported in
  the row but does **not** count toward completeness.
- **Stale mode** — funds whose newest snapshot or NAV is
  older than `--max-age-hours` (default 24 h), or that have
  neither. Used for "did the nightly backfill skip
  something?".

The defining characteristics are:

- **Read-only.** Coverage runs `SELECT`, never
  `INSERT/UPDATE/DELETE`. It is safe to call at any
  point in a sync, and it does not move the data
  forward.
- **Two-tier aggregation.** Per-fund rows
  (`completeness`, `missing`) are the input;
  per-dataset % is derived from them. The % is
  over the rows the filters produced, not the
  whole universe.
- **8 datasets, not 14.** `fund_managers` and the
  3 audit tables (`raw_responses`, `sync_runs`,
  `sync_failures`) are not part of the coverage
  score. Manager data is directory-style; audit
  tables are operator telemetry.
- **Stale is per-fund, not per-dataset.** A fund
  whose stock_holdings are 6 months old but whose
  NAV is fresh is "stale" overall. The v0.3.0
  backlog has a per-dataset staleness view.

---

## The full answer template (use this skeleton)

When asked "how does coverage work?", structure the answer
in **four paragraphs**, one per concept. Order matters.

### Paragraph 1 — Two modes

> `fund-data` has two read-only introspection modes.
> **Coverage mode** answers "how complete is the data per
> fund?" — it returns a list of dicts, one per fund, each
> with a `completeness` score in `[0, 1]` (8-dataset
> equal-weighted average) and a `missing` list of empty
> dataset names. **Stale mode** answers "which funds
> haven't been refreshed recently?" — it returns the funds
> whose newest `snapshots.fetched_at` or `nav_history.fetched_at`
> is older than `--max-age-hours` (default 24), or that
> have no row at all. The two modes share the same DB
> path; the rendering is different. Both are
> `SELECT`-only and never write.

### Paragraph 2 — The 8 datasets

> The coverage score is over **8 datasets**:
> `profile`, `nav`, `stock_holdings`, `bond_holdings`,
> `industries`, `fees`, `dividends`, `splits`. Each
> dataset is checked with a SQL `case when ... is null
> then 0 else 1` over a `LEFT JOIN` from the `funds`
> table. A fund with all 8 present has `completeness = 1.0`
> and `missing = []`. A fund with 4 present has
> `completeness = 0.5` and `missing` lists the other 4.
> `fund_managers` is in the SQL output (`manager_rows`
> column) but does not count toward completeness. The 3
> audit tables (`raw_responses`, `sync_runs`,
> `sync_failures`) are not in the score at all.

### Paragraph 3 — Naturally sparse vs fixable

> Some datasets are expected to be empty for some fund
> types. **Naturally sparse** means the fund type does
> not carry that dataset by design: bond funds have no
> `stock_holdings`, money-market funds have no
> `stock_holdings` or `bond_holdings`, REITs have no
> public disclosure, and most funds do not pay dividends
> or split. The "global" coverage numbers in AGENTS.md
> are inflated by these funds; the per-fund_type
> breakdown shows the structural gap. **Fixable** means
> the dataset should be there but is missing: profile
> empty for a fund that should have one, NAV empty
> after a sync, holdings empty after a quarterly report
> that should have been picked up. Coverage does not
> distinguish naturally sparse from fixable; it just
> reports "is the row present or not". An agent that
> wants the fund_type-aware view should filter by
> `fund_type`.

### Paragraph 4 — Stale mode

> Stale mode is the "did the backfill skip something?"
> check. A fund is stale if its newest `snapshots.fetched_at`
> is older than the cutoff (default 24 hours), **or** its
> newest `nav_history.fetched_at` is older than the
> cutoff, **or** either timestamp is null. The cutoff
> is `utc_now() - max_age_hours`. The default 24 hours
> is a coarse threshold; for a nightly backfill that
> runs at 03:00, `--max-age-hours 36` is more accurate
> (a fund refreshed at 03:00 today is "stale" by 03:00
> tomorrow morning, even though the data is one day
> old). The CLI invocation is
> `coverage_report.py --stale --max-age-hours 36`. The
> stale mode is per-fund, not per-dataset — a fund
> whose stock_holdings are 6 months old but whose NAV
> is fresh is "stale" overall. A per-dataset staleness
> view is on the v0.3.0 backlog.

---

## The 12 most-asked questions (with full answers)

These are the questions that come up the most in onboarding,
support, and PR review. **Answer them in the order they
appear here, with the same level of detail** — these are
the explanations the team has settled on after multiple
rounds of "but why?".

### Q1. Why 8 datasets and not all 14?

- **The 8 datasets are the agent's data plane.** `profile`,
  `nav`, `stock_holdings`, `bond_holdings`, `industries`,
  `fees`, `dividends`, `splits` are the tables the agent
  reads at runtime to answer fund questions.
- **`fund_managers` is directory-style data.** A fund's
  manager record is a directory lookup, not a per-fund
  data plane. Including it in the completeness score
  would penalise funds whose manager records have moved
  to a different fund, which is a common event (managers
  rotate funds frequently).
- **The 3 audit tables are operator telemetry.**
  `raw_responses` (full upstream HTTP bodies; may
  contain caller IP), `sync_runs` (every sync call's
  audit row), `sync_failures` (every hard-failed sync
  call's queue row). An agent on a different machine
  has no use for them.
- **The split is the privacy boundary too.** The query
  bundle (`fund-cloud-bundle-pipeline.md` §3.2) strips
  the audit tables; coverage follows the same logic.

### Q2. Why is `fund_managers` in the row output but not in the completeness score?

- **Manager data is a directory, not a dataset.** A fund
  has 0-N managers over its lifetime; the row count
  tells you "how many records do we have for this fund's
  manager history", not "is the fund complete".
- **Manager records are noisy.** A fund's manager may
  change every quarter; the count fluctuates. Including
  the count in completeness would make the score
  unstable.
- **The agent can still query manager data via
  `fund_managers(code=...)`.** The data is available; it
  is just not part of the headline score.

### Q3. Why is the completeness score equal-weighted, not weighted by importance?

- **Equal weighting is the simplest correct answer.**
  Every dataset is `1/8` of the score. An agent that
  wants a different weighting can compute it from the
  per-row dicts.
- **The team has considered NAV-weighted** (NAV is the
  most important dataset, so weight it more). The
  trade-off is "is the weight choice documented and
  stable across releases?". Equal weighting is stable;
  any other weighting would need a schema migration.
- **The score is a hint, not a verdict.** A `completeness
  = 0.5` fund may have profile + nav + stock_holdings +
  fees (the four most common agent queries), which is
  "good enough" for most questions. The score is a
  one-number summary, not a per-question fitness
  indicator.

### Q4. Why does the per-dataset % in the markdown header depend on the filters?

- **The renderer aggregates over the rows it received.**
  If the caller passes `fund_type='股票型'`, the per-
  dataset % is over the stock-type funds, not the
  whole universe. This is a feature, not a bug: an
  operator that wants "stock-type coverage" gets it
  without a second pass.
- **The "global" view** is `coverage_report()` with no
  filter. The markdown header reads
  `funds: <total> • reported: <reported>` to make the
  difference explicit.
- **An agent that wants both views** should call twice:
  once with no filter (the universe), once with
  `fund_type` (the per-type view). The cost is two
  SQL queries; both are sub-second on a 27k-fund
  database.

### Q5. Why is the stale threshold 24 hours, and when should I change it?

- **24 hours is a coarse default.** It catches "the
  nightly backfill ran and 5 % of funds were skipped"
  but is too noisy for "did the 03:00 backfill refresh
  100 % of the universe?".
- **For a daily backfill that runs at 03:00**, use
  `--max-age-hours 36`. A fund refreshed at 03:00 today
  is "fresh" until 15:00 tomorrow, even though the
  data is one day old.
- **For a weekly backfill**, use `--max-age-hours 192`
  (8 days). The threshold is "the data should not be
  older than the backfill cadence plus a margin".
- **For an on-demand pull**, use `--max-age-hours 1`. A
  fund that has not been touched in the last hour is
  stale because the on-demand pull is expected to be
  current.

### Q6. Why is stale mode per-fund, not per-dataset?

- **The MVP was per-fund.** The team's first iteration
  was "is this fund stale?" — the per-dataset view is a
  follow-up.
- **Per-dataset staleness is more useful for diagnosis.**
  A fund whose NAV is fresh but stock_holdings are
  6 months old has a real problem (the quarterly
  report pick-up failed). Per-fund staleness hides
  this; per-dataset staleness surfaces it.
- **The v0.3.0 backlog has the per-dataset view.** The
  implementation is straightforward — a SQL with
  `max(fetched_at)` per table — but the renderer
  needs a new column shape and the markdown header
  needs new aggregate lines.

### Q7. Why does `coverage_report` not know about fund_type when computing the score?

- **The SQL is a generic 8-table LEFT JOIN.** Knowing
  about fund_type would require the SQL to JOIN
  `funds.fund_type` and apply per-type rules, which is
  a much more complex query and harder to maintain.
- **The agent / operator filters by fund_type** in the
  `WHERE` clause. The per-type view is computed by
  passing `--fund-type 股票型` (or equivalent in code).
- **The trade-off is simplicity vs. accuracy.** The
  team chose simplicity. The "global" coverage numbers
  in AGENTS.md are the headline; the per-type numbers
  are a follow-up drilldown.

### Q8. Why does coverage use `LEFT JOIN` instead of separate `EXISTS` subqueries?

- **`LEFT JOIN` is a single SQL pass.** The 8 tables
  join in one round trip; the case-when checks the
  nullability of the join keys. A separate `EXISTS`
  query per table would be 8 round trips.
- **The LEFT JOIN handles missing tables gracefully.**
  If `fund_profiles` does not exist (the DB is half-
  built), the LEFT JOIN returns null and the case-when
  reports 0; an `EXISTS` query against a non-existent
  table would raise `OperationalError`.
- **The cost is a wide result row.** The 8-table join
  produces a row with all 8 `*_rows` columns; the
  per-row dict is wide. The trade-off is one wide
  row vs. eight narrow ones. SQLite is optimised for
  wide rows in the same query.

### Q9. Why does the doctor's coverage section not match the in-process report?

- **`doctor.py` reads from the on-disk DB**;
  `coverage_report` reads from `default_db_path()`,
  which prefers the OSS cache. The two are different
  DBs after a `cloud pull`.
- **The team's "Long-running pitfalls" note in
  AGENTS.md** documents this as the most common "wrong
  DB" report. An agent that wants the doctor to
  report the cache numbers should pass
  `FUND_DATA_DB=/path/to/cache/.../fund_data_query.sqlite`
  to doctor, or unset the env var to force the
  fallback.
- **The two views are intentional.** Doctor is the
  operator's view of the production DB; the in-process
  report is the agent's view of whatever DB the
  bootstrap resolved. Conflating them would lose the
  signal.

### Q10. Why is `coverage_report` exposed as both an MCP tool and a Python helper?

- **The MCP tool is for agents.** An OpenClaw daemon
  that wants to check coverage calls
  `fund_coverage_report` and gets the structured
  payload.
- **The Python helper is for humans and embedded
  use.** An operator that wants to add a coverage
  check to a custom script imports `coverage_report`
  directly.
- **Both wrap the same SQL.** The MCP tool calls the
  Python helper; the helper calls the same SQL. There
  is no "MCP version" vs "CLI version" divergence.
  A change in the helper is automatically a change in
  the tool.

### Q11. Why does the renderer not include `fund_managers` in the `missing` list?

- **`fund_managers` is not in the 8-dataset score.** A
  fund with no manager row but with all 8 datasets
  present has `completeness = 1.0` and `missing = []`,
  even though `manager_rows = 0`.
- **Adding `fund_managers` to the `missing` list
  would be inconsistent** — the score says 1.0 but
  the missing list says "managers" is empty.
- **The team's choice is to report `manager_rows` as
  a column for introspection** but not count it in
  the score. The `missing` list is consistent with
  the score.

### Q12. Why does the markdown renderer show the top 10 most-incomplete funds, and the table renderer show 200?

- **The markdown is for PR descriptions and chat
  messages.** 10 rows is the upper limit for "human
  reads this"; more would be noise.
- **The table is for terminal review.** 200 rows is
  the upper limit for a readable fixed-width table
  in a 100-column terminal. More would wrap.
- **The JSON output is for downstream tooling.** No
  limit (well, the `limit` parameter) — the consumer
  can handle the full list.
- **The three limits reflect the three consumers.**
  An agent that wants the full list uses
  `--format json --limit 0` (or no limit). The
  default `--limit 10` on markdown is the human-
  reader sweet spot.

---

## Design philosophy (the "why" of the two-mode shape)

Read this section once and the rest of the playbook
becomes obvious.

1. **Coverage is read-only.** The SQL is `SELECT`,
   never `INSERT/UPDATE/DELETE`. Coverage can run
   during a sync without interfering. An agent that
   wants a "is the data ready?" check calls coverage
   after a backfill batch; the coverage report
   reflects the rows committed so far.
2. **The 8-dataset score is a one-number summary.**
   It is not a per-question fitness indicator. A
   fund with `completeness = 0.5` may be "good
   enough" for the most common agent queries
   (profile + nav + holdings + fees) and "not good
   enough" for a dividend analysis. The agent
   decides per-question.
3. **Equal weighting is the simplest correct
   answer.** Any other weighting would need a schema
   migration and a documentation burden. The team's
   choice is "equal + simple".
4. **`fund_managers` is directory-style, not
   data-plane.** It is reported in the row for
   introspection but does not count toward the
   score. Manager rotations would destabilise the
   score; the team prefers stability.
5. **Stale is per-fund, not per-dataset.** The MVP
   is per-fund; the per-dataset view is a v0.3.0
   follow-up. The team chose to ship the simpler
   version first.
6. **The 24-hour threshold is a coarse default.**
   Operators tune it via `--max-age-hours` to match
   their backfill cadence. The default is for
   evaluators who have not yet measured their
   cadence; production deployments set it to
   `cadence + margin`.
7. **Doctor and the in-process report are different
   views.** Doctor reads the on-disk DB; the
   in-process report reads `default_db_path()`. The
   two diverge when a backfill writes to the cache
   DB. The team's "Long-running pitfalls" note
   documents the divergence; the fix is to set
   `FUND_DATA_DB` explicitly for the backfill run.
8. **The renderer is split by consumer.** Markdown
   for humans (PR descriptions, chat), table for
   terminals (quick review), JSON for downstream
   tooling (agents, scripts). The three limits
   (10, 200, unlimited) match the three
   consumers' readability thresholds.

---

## What NOT to say (anti-patterns)

These are common wrong answers the team has seen in PR
reviews and support threads. Avoid them.

- **"Coverage is global."** It is not. The per-dataset
  % is over the rows the filters produced, not the
  whole universe. An agent that wants the global
  view should pass no filter; an agent that wants
  the per-type view should pass `--fund-type`.
- **"`completeness = 1.0` means the fund is fully
  covered."** It means the 8 datasets are all
  present. It does not mean the data is current
  (use stale mode), accurate (no such check), or
  complete at the row level (a fund with 1 NAV row
  has the same `completeness` as one with 1000).
- **"Stale means the data is wrong."** Stale means
  the data is old. A 24-hour-stale NAV may still be
  the best available; the agent decides whether to
  refresh.
- **"Doctor and the in-process report should
  agree."** They can diverge; the divergence is
  intentional and documented. The fix is to align
  the DB paths, not to align the reports.
- **"Run coverage after every sync to verify."**
  Coverage is a `SELECT`; running it 27k times in
  a backfill would slow the backfill down. The
  team's guidance is to run coverage once at the
  end of the backfill, not per-fund.
- **"`fund_managers = 0` is a coverage miss."** It
  is not. `fund_managers` is not in the 8-dataset
  score; the column is for introspection. A fund
  with no manager row but with all 8 datasets
  present is fully covered.
- **"Naturally sparse datasets inflate the global
  coverage number."** They do. The team's
  AGENTS.md §Coverage by `fund_type` shows the
  per-type breakdown. An agent that wants the
  type-aware view should filter.
- **"The completeness score is the percentage of
  funds covered."** It is the percentage of the 8
  datasets present per fund, weighted equally. A
  fund with 4 of 8 datasets has `completeness
  = 0.5`, not "50 % coverage".

---

## How to keep this playbook accurate

The playbook is the team's *settled* explanation, not
the live code. When the code changes, update the
playbook in the same PR. The check is:

- A dataset is added to or removed from
  `coverage_report` (the 8-dataset list) → update
  §3 and the completeness definition.
- A new entry point is added (e.g.
  `fund_coverage_diff`) → update §4.
- The output shape changes (a field added/removed
  in the dict) → update §5.
- The stale threshold default changes → update
  §3.2 and §6.
- A new filter is added (e.g.
  `min_manager_rows`) → update §6.
- A renderer limit changes (markdown 10, table
  200) → update §6 and Q12.

If a PR changes any of the above and does not update
the playbook, request changes with a pointer to this
section.

---

## Related documents

- [`fund-coverage-pipeline.md`](./fund-coverage-pipeline.md) —
  diagrams + code anchors + result shape.
- [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) —
  the writer of the data that coverage measures.
- [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) —
  the distribution path that lands a fresh agent at
  a known coverage state.
- [`fund-search-playbook.md`](./fund-search-playbook.md) —
  the single-search answer script (for
  `fund_coverage` / `fund_coverage_report` tools).
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —
  the agent-facing skill manifest.
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —
  the per-fund_type coverage breakdown, the
  long-running pitfalls (default_db_path vs doctor
  divergence), and the operational checklist.
- [`../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md`](../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md) —
  the structural gap analysis (naturally sparse vs
  fixable vs not-fixable vs 0.3.0 backlog).
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —
  the v0.3.0 backlog items (per-dataset staleness,
  fund_doctor MCP tool, etc.).
