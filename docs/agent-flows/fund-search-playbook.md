# Fund Search Playbook

> **Last updated:** 2026-06-02
> **Audience:** Anyone — human or AI — who gets asked "how does
> `fund-data` find a fund?" or "why does the search go through
> those four layers?". This is the **answer script**, not the
> architecture reference. Pair with
> [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) for diagrams
> and code anchors.
>
> **Use it when:**
> - Onboarding a new contributor or agent to the data plane.
> - Reviewing a PR that touches `fund_data.search_funds`,
>   `fund_cloud.ensure_project_bundle`, or `default_db_path`.
> - Debugging a report of "search returned the wrong provider's
>   data" or "the agent's data is in a different DB than mine".
> - Fielding a question from a downstream team about whether to
>   expect a network call, a cache hit, or a 4-RPS burst.
>
> **Do NOT use it when:**
> - The question is about backfill or coverage → use
>   [`fund-data/AGENTS.md`](../../fund-data/AGENTS.md).
> - The question is about a specific provider's quirks → use
>   [`fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md).
> - The question is "how do I install the skill" → use
>   [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md).

---

## TL;DR (60-second answer)

A fund lookup in `fund-data` passes through **four layers** in
order:

1. **Entry point** — MCP tool, CLI subcommand, or Python import.
2. **Cloud bootstrap** — `fund_cloud.ensure_project_bundle()` decides
   whether to install the OSS query bundle or skip straight to the
   local DB.
3. **DB path resolution** — `fund_data.default_db_path()` collapses
   the env vars and cache into one concrete SQLite file.
4. **Provider chain** — `build_providers_full()` picks the live data
   source, `run_provider_chain()` executes it, results are
   upserted into the DB.

Each layer has exactly one job, each layer can fail without
cascading, and the persistence side effect at the end is the only
state that survives across calls. Everything before it is
**stateless and reproducible**.

---

## The full answer template (use this skeleton)

When asked "how does `fund-data` find a fund?", structure the
answer in **four paragraphs**, one per layer. Order matters — it
matches the runtime call order.

### Paragraph 1 — Entry point

> The user can enter through three surfaces: the MCP stdio server
> (`fund_search` tool), the `fund-cli search` subcommand, or a
> direct Python import of `fund_data.search_funds`. All three
> converge on the same function. The MCP path is the one most
> OpenClaw / Codex / Claude Code agents hit; it accepts a `db`
> argument that, when omitted, triggers an automatic cloud
> bootstrap. The CLI and Python paths let the user skip the
> bootstrap by passing `db`/`db_path` or by setting
> `FUND_DATA_DB`.

### Paragraph 2 — Cloud bootstrap

> Before any data call, `fund_cloud.ensure_project_bundle()` checks
> four things in order: is `FUND_DATA_DB` already set (use it,
> skip the bootstrap); is `FUND_DATA_AUTO_PULL=0` (skip the
> bootstrap, fall through to live providers); is there already a
> pulled bundle in `~/.cache/fund-data/` (reuse it, no network
> call); otherwise download the manifest from the OSS bucket,
> verify the `fund_data_query.sqlite.gz` SHA-256, and extract to
> the cache. **A failed download returns a structured `fallback:
> "api"` signal — it never raises** so the live providers still
> get a chance to serve the request.

### Paragraph 3 — DB path resolution

> Once the bootstrap decision is in hand, `default_db_path()`
> resolves to a single SQLite file using a narrow precedence list:
> explicit `FUND_DATA_DB` (with a cache override), the just-pulled
> OSS bundle, the `current.json` cache pointer, or the on-disk
> `fund-data/data/fund_data.sqlite` fallback. This is the file
> that will be read for coverage and exports, and the file that
> will be written to by the persistence side effect.

### Paragraph 4 — Provider chain

> `build_providers_full("auto", capability="search")` then composes
> the live data source list. In `auto` mode, paid providers
> (`investoday` if `INVESTODAY_API_KEY` is set, then `tushare` if
> `TUSHARE_TOKEN` is set) are prepended; the free providers
> `[Eastmoney, AkShare]` are appended in a capability-specific
> order — Eastmoney first for search/NAV/snapshot/fund_list,
> AkShare first for profile/holdings/bonds/industries/fees/
> dividends/splits/managers. `run_provider_chain` then calls each
> provider in order, treats `None` and empty results as failure
> (recording them in a `failures` list), and **the first provider
> to return non-empty rows wins**. A successful return carries
> the `failures` trail so an agent can audit which providers were
> tried. If every provider fails, the chain raises
> `ProviderError("all providers failed for search_funds: ...")`
> with the full trail in the message.

### Paragraph 5 — Persistence (the side effect, mentioned but not dwelled on)

> Search is not a read — it is a read-then-upsert. The returned
> rows are written to the `funds` table keyed on `fund_code`
> (`INSERT OR REPLACE` with full column overwrite), and the raw
> provider payload is appended to `raw_responses` for audit. This
> means repeated searches will keep the local DB in sync, but
> they will also overwrite column values that other flows (like
> `refresh_fund_type`) had filled in from a different source.

---

## The 12 most-asked questions (with full answers)

These are the questions that come up the most in onboarding,
support, and PR review. **Answer them in the order they appear
here, with the same level of detail** — these are the explanations
the team has settled on after multiple rounds of "but why?".

### Q1. Why does `search_funds` always go through the live provider chain? Why not check the local SQLite first?

The team's reasoning, with the trade-off acknowledged:

- **Freshness wins.** Search is a discovery action; the user is
  looking for *the* fund, not "any fund I might already know
  about". A local DB that was last refreshed last week can miss
  new funds (the universe grows by ~30 codes/week on average), and
  it can show stale names after a fund merger.
- **Local is for analysis, not lookup.** The local SQLite is
  optimised for the *post-discovery* path — `coverage_report`,
  `fund_export table=funds`, `fund_search` results that you want
  to join against `nav_history`. Using it for keyword search
  would couple lookup quality to backfill freshness.
- **The trade-off is explicit.** A 1-RPS rate limit on
  `FundDataClient` means a 30-keyword batch takes 30+ seconds.
  We accept that for the correctness win. For high-volume batch
  lookups, use `fetch_fund_list` (full universe, one call) and
  filter client-side, or use `batch_sync` with explicit codes.

### Q2. Why does the cloud bootstrap fail silently and the provider chain fail loudly?

Two failure modes, two failure policies — chosen on purpose.

- **Cloud bootstrap is best-effort infrastructure.** The OSS
  bundle exists to save agents from running a 21-hour AkShare
  backfill on first install. If the bucket is down, the agent
  is no worse off than before the bundle existed — the live
  providers still work. Raising an exception would block
  every tool call whenever the bucket hiccups, and the agent
  has no way to recover. The bootstrap's `fallback: "api"`
  signal is the structured "your cache is stale, but I can
  still serve you" message.
- **The provider chain is the data contract.** When the agent
  asked for "110022's NAV history", it is the chain's job to
  deliver. If every provider failed, that is a *data* failure
  — the agent needs to know, not silently get back an empty
  list. Raising `ProviderError` is the loud signal; the
  message carries the per-provider trail so the agent can
  surface it.
- **The asymmetry is intentional.** Loud failures for things
  the user explicitly asked for; quiet failures for things the
  framework is doing on the user's behalf.

### Q3. Why does the auto provider chain order vary by capability?

Because each provider has a measurable strength on each
capability, and the team tested them:

- **Eastmoney is faster and more reliable for high-volume read
  data**: fund list, fund search, NAV history, snapshot. No
  key required, no AkShare install required, 1 RPS but
  ~0.36 s/fund. Tushare/Investoday are also good here, but
  we have an Eastmoney-first cost story for evaluators who
  don't want to set up keys.
- **AkShare (and its structured mirrors Tushare / Investoday)
  are the only sources for the deeper data**: profile,
  holdings, bonds, industries, fees, dividends, splits,
  managers. Eastmoney's public endpoints do not expose most of
  these (snapshots come from `pingzhongdata`, which is a
  different endpoint). Putting AkShare first in the chain
  reflects that for these capabilities, Eastmoney is the
  fallback, not the leader.
- **The order is a benchmark, not a belief.** When Tushare's
  `fund_profile` endpoint got faster than AkShare's
  `fund_overview_em` in late 2025, the auto chain moved
  Tushare ahead of AkShare. The ordering is re-measured
  every quarter; the file you should check before changing
  the order is `fund-data/AGENTS.md` ("Eastmoney-only beats
  AkShare 8x" section).

### Q4. Why is the provider chain "first non-empty wins" rather than "score by completeness" or "ask all in parallel"?

- **First non-empty wins is the cheapest correct answer.** The
  primary providers (Eastmoney for the cheap four, Investoday
  for everything when keyed) are high-trust. If
  `InvestodayProvider.search_funds()` returns 30 rows, we have
  no business asking AkShare for a second opinion. Adding a
  "completeness score" would require a per-row scoring function
  that does not exist (and would be subjective).
- **Asking all in parallel would burn rate limit.** A 4-way
  parallel search would 4x the per-keyword HTTP cost. The
  agent does not pay for that cost in CPU; it pays in seconds
  (Eastmoney throttles after ~8 in-flight) and in money
  (Investoday is metered). Sequential with early exit is the
  right shape.
- **The `failures` list is the audit hook.** Even on a
  successful return, the result carries the failure trail.
  An agent that wants to verify "did AkShare have a better
  answer?" can inspect the trail and explicitly call AkShare
  with `--provider akshare`.

### Q5. Why does `upsert_funds` overwrite all columns? Why not merge?

- **`upsert` is what the search results need.** When the
  provider returns `{fund_code, fund_name, fund_type, ...}`,
  the caller is telling us "this is the current state of
  fund X". Merging would leave stale data from a previous
  source in the row.
- **The cost is documented and worked around.** A separate
  flow, `refresh_fund_type`, populates `fund_type` from
  Eastmoney's `fundcode_search.js` (the *full* index, not the
  search index). The Investoday provider populates it from
  `/fund/all` (the *catalog*, not the search results). Both
  are richer than what `search_funds` returns. Because
  `upsert_funds` overwrites, a `list` rebuild that calls
  `fetch_fund_list` will *lose* the better values, and you
  have to re-run `refresh_fund_type` after. The
  `refresh_fund_type --only-empty` flag was added precisely
  to make the re-run cheap.
- **The DB schema is the contract, not the merge logic.** If
  we ever add a partial-update mode, it will be a new method
  (`upsert_fund_names_only`, etc.) on `FundDataStore`, not a
  behaviour change in `upsert_funds`. This keeps the
  documented contract stable.

### Q6. Why is the rate limit hard-coded at 1 RPS in `FundDataClient`?

- **Empirically, the upstream throttles at ~2-3 RPS with
  bursts.** 1 RPS is the safe level that did not produce 5xx
  errors in the team's backfill test runs (see
  `fund-data/AGENTS.md` "AkShare is the throughput bottleneck"
  section). The rate limit is `min_interval_seconds=1.0` on
  the client.
- **It is a default, not a hard rule.** `fund-batch-sync` and
  `fund-batch_sync_funds` accept `--min-interval-seconds` and
  `--concurrency`; the team found `concurrency=8,
  min-interval=0.1` is the sweet spot for Eastmoney. The
  1-RPS default is for the single-call `fund_search` path so
  an agent that loops over keywords cannot accidentally
  exceed the safe limit.
- **The cost of being wrong is high.** Eastmoney returns 5xx
  on burst, and there is no retry budget that recovers from
  "you are rate-limited for the next 10 minutes". Erring
  conservative is the right default.

### Q7. Why is `fund_cloud_status` a first-class tool?

- **Agents need to know what version they are running
  against.** A report that says "fund 110022 has NAV row X"
  is not actionable without knowing whether the local DB
  reflects a 2026-06-01 or a 2026-05-15 backfill. The
  manifest URL is the version pointer; `fund_cloud_status`
  exposes the local cache version + the remote version +
  the diff, so an agent can either pull the new bundle or
  flag the staleness.
- **It is the bootstrap's audit channel.** The bootstrap
  returns `source: cache|oss|api` and `skipped: ...`. The
  status tool surfaces the same information on demand. An
  agent that wants to verify "the bootstrap really did the
  thing it was supposed to" calls this tool after the first
  data call.

### Q8. Why does `install_skill.py --include-data` warn about `raw_responses`?

- **The `raw_responses` table stores full upstream HTTP
  bodies.** For Eastmoney and AkShare, those bodies can
  contain the caller's IP in `X-Forwarded-For` or similar
  headers that some upstream proxies add. The table also
  includes the response `Content-Type` and any cookies
  returned.
- **When the snapshot leaves your machine, the IP leaks.**
  An `--include-data` install that ships to a public OSS
  bucket, a colleague's laptop, or a CI artifact is a
  publish. The `--scrub-raw-responses` flag empties that
  table before publishing; without it, the user is opting
  in to the leak knowingly.
- **The default is the safer behaviour.** `install_skill`
  without `--include-data` excludes the SQLite file
  entirely, so the question doesn't arise. The flag is
  opt-in, and when it is on, the scrub is opt-in too —
  matching the team's "explicit > implicit" rule for any
  publish.

### Q9. Why does `fetch_fund_list` pull all ~27k funds in one call, and not "by type" or "by exchange"?

- **There is no clean filter upstream.** The Eastmoney
  `fundcode_search.js` index is a single JS object that
  includes every fund. AkShare's `fund_name_em()` returns a
  DataFrame of the same. There is no
  `fund_name_em(exchange="SH")` or
  `fund_name_em(type="货币型")` query parameter. The
  provider either returns the full universe or nothing.
- **It is fast enough.** The JS file is ~2.5 MB; the
  DataFrame is ~5 MB. The cost is one HTTP call, ~1-2
  seconds, no rate limit hit. Filtering it client-side takes
  ~50 ms for the typical query ("give me 货币型 funds").
- **It is the only source of `fund_type`.** The `fund_type`
  column is populated from this single pull. Without it,
  the coverage report cannot distinguish "empty because no
  data" from "empty because the user filtered". Forcing a
  full pull is what makes the filter layer above it
  meaningful.

### Q10. Why does `PROVIDER_AUTO` exist instead of picking a default?

- **Different agents have different cost / reliability
  budgets.** A research team's batch job that runs weekly
  wants Investoday first (paid, fastest, contract-backed).
  An evaluator's smoke test that runs once wants
  Eastmoney-only (free, no key, no install). A finance
  team's production run wants Tushare (clean JSON, stable
  schema). Hard-coding any one of them breaks the other two.
- **Env vars are the configuration surface.** The chain
  composition reads `INVESTODAY_API_KEY` and
  `TUSHARE_TOKEN` at call time, so an agent can flip the
  order without code changes. The CLI flag
  `--provider investoday` is the explicit override for
  one-off runs.
- **`auto` is the safer default for evaluators.** It picks
  the highest-fidelity source the operator has configured.
  Forcing evaluators to set a key just to run a search
  would be hostile to the project's "no-key, no-install"
  evaluation story.

### Q11. Why does search not do a "local first, remote on miss" pattern? That would be cheaper.

- **It would couple lookup latency to local DB freshness.**
  The whole point of search is to be current. A local-DB
  miss is a known-true signal that the user's keywords did
  not match anything we knew about *last week*, but it is
  not a signal that nothing exists. Silently returning "no
  results" when the live provider would have returned 5
  rows is a worse failure mode than the 1-RPS cost.
- **The local DB is for joins, not for gates.** When the
  agent already has a fund code, it can do a `coverage`
  lookup against the local DB cheaply. When the agent has
  a keyword, it has to go upstream.
- **The cost asymmetry is wrong.** A 1-second HTTP call is
  1 second. A "check local first, then fall back to remote"
  pattern is 1 second + the same 1 second, *plus* the cost
  of building a search index on the local DB. The "fast
  path" is not actually faster.

### Q12. Why does `default_db_path()` not cache the resolved path?

- **The path is resolved on every `FundDataStore()`
  construction.** Caching it would mean a process that
  calls `search_funds` after `cloud_pull` builds against
  the old path. The user-visible behaviour change would be
  "I pulled a new bundle, but my next search still wrote to
  the old DB" — exactly the failure mode the bootstrap is
  designed to prevent.
- **The cost is negligible.** The function reads two env
  vars, looks at one file, and returns. It is sub-millisecond
  on every call site we measured. Caching a 100-microsecond
  function to save 100 microseconds is the wrong trade-off.
- **The contract is "ask every time"**, which means a
  long-running daemon that wants to switch DBs (via
  `FUND_DATA_DB` or by `cloud_pull`-ing a new bundle) does
  not have to clear a cache. The simplicity is the feature.

---

## Design philosophy (the "why" of the four-layer shape)

Read this section once and the rest of the playbook becomes
obvious.

1. **The pipeline is shaped by its failure modes, not its
   success modes.** Cloud bootstrap is allowed to fail
   silently because its failure is recoverable. Provider chain
   is not allowed to fail silently because its failure is
   user-visible. DB path resolution is allowed to fail
   implicitly (fall through to defaults) because its failure
   is "the local DB does not exist yet" — which the persistence
   step will create. The number of layers is the number of
   distinct failure policies, not the number of distinct
   steps.

2. **Stateless before stateful.** Everything from entry point
   to provider chain execution is pure: same input → same
   output, no side effects. The first side effect is the
   `upsert_funds` write, and it is at the very end. This
   shape is what makes the system debuggable: when an agent
   reports "search returned X", the investigator can replay
   the call from the same `keyword` and the same env vars
   and get the same rows, *if* the underlying providers are
   deterministic. (They are mostly so; Eastmoney and AkShare
   are read-only and idempotent.)

3. **Configuration through env vars, not code.** `FUND_DATA_DB`,
   `FUND_DATA_AUTO_PULL`, `FUND_DATA_MANIFEST_URL`,
   `FUND_DATA_CACHE_DIR`, `INVESTODAY_API_KEY`,
   `TUSHARE_TOKEN`, `FUND_DATA_DISABLE_AKSHARE`. Every
   behaviour-altering switch is an env var. The reason is the
   agent population: a long-running OpenClaw daemon can flip
   these between calls without restarting, and a CI runner
   can set them per-step without rebuilding the image.

4. **The two storage tiers exist for a reason.** The
   `fund-data/data/fund_data.sqlite` (full audit-log DB) and
   the `fund_data_query.sqlite.gz` (query-only bundle) are
   intentionally separate. The full DB keeps `raw_responses`,
   `sync_runs`, `sync_failures` for operators who need to
   rebuild or audit. The query bundle strips them so the
   publish size is manageable and the operator's IP is not
   leaked. Same data shape, two storage classes. The skill
   install default (`--data-mode none`) skips both and
   points at the OSS bundle; the explicit `--include-data`
   flag opts in to the full DB; the private
   `archive-full` flow handles the rest.

5. **The provider chain is a cost ladder, not a quality
   ladder.** "First non-empty wins" is the right shape when
   the higher-cost providers are also higher-trust. If a
   cheaper provider were higher-trust, the chain would
   invert. The ordering is a benchmark; check the AGENTS.md
   "Eastmoney-only beats AkShare 8x" note before changing it.

6. **Errors carry trail, not blame.** `ProviderError` says
   "all providers failed for search_funds: eastmoney: ...;
   akshare: ...". `ensure_project_bundle` returns
   `fallback: "api"` and `error: "..."` when the pull fails.
   The structured trail is what an agent needs to
   self-diagnose. The convention is: a failure response is a
   success-shaped message that happens to contain `error` or
   `isError`, never a bare string.

---

## What NOT to say (anti-patterns)

These are common wrong answers the team has seen in PR reviews
and support threads. Avoid them.

- **"Search uses a cache."** It does not. Search uses the
  live provider chain and writes to the cache. The cache is
  the output, not the input.
- **"Set `FUND_DATA_DB` to a path on a fast disk."** That is
  a SQLite tuning tip, not a `fund-data` design answer. The
  `fund-data` answer is "what data is in that DB, and how
  often is it refreshed?" — that is what makes a `fund-data`
  question distinct from a SQLite question.
- **"We use AkShare for fallback."** Misleading. AkShare is
  primary for 8 of the 12 capabilities and fallback for the
  other 4. Always say which capability.
- **"It depends on the provider."** Lazy. The chain order is
  documented; cite `build_providers_full` and the
  `capability` parameter.
- **"Check the `sync_failures` table."** Wrong for search.
  `sync_failures` is for the `batch-sync` failure queue.
  Search failures are in the `raw_responses` audit log
  (under the `failures` key in the JSON payload) and in
  the `ProviderError` message.
- **"The 1-RPS limit is for politeness."** It is for
  *correctness* — exceeding it triggers 5xx upstream, and
  there is no graceful retry budget. Politeness is the
  side effect.

---

## How to keep this playbook accurate

The playbook is the team's *settled* explanation, not the live
code. When the code changes, update the playbook in the same
PR. The check is:

- Did `default_db_path()` precedence change? → Update
  Paragraph 3 of the standard answer.
- Did `build_providers_full()` routing change? → Update
  Paragraph 4 and Q3.
- Did the failure policy change (loud ↔ quiet)? → Update
  Q2 and the philosophy section.
- Did a new env var land? → Add it to Paragraph 3 and the
  env var decision table in `fund-lookup-pipeline.md`.
- Did a new capability land? → Update the capability list
  in Paragraph 4 and check Q3 / Q5 still apply.

If a PR changes any of the above and does not update the
playbook, request changes with a pointer to this section.

---

## Related documents

- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —
  diagrams + code anchors + env var table.
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —
  the agent-facing skill manifest, loaded into the system
  prompt on skill match.
- [`../../fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md) —
  the contributor-facing architecture reference.
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —
  backfill recipes, long-running pitfalls, and the
  per-provider performance numbers that justify the chain
  ordering.
- [`../../fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md) —
  how to enable each provider, what each provider actually
  unlocks, and the recipe for registering a new one.
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —
  per-platform install layout for Codex / Claude / OpenClaw.
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —
  the v0.3.0 backlog items that will land next (no `--json`
  flag, no HTTP/SSE MCP, no progress notifications, no
  `fund_doctor` MCP tool).
