# Fund Cloud Bundle Playbook

> **Last updated:** 2026-06-02
> **Audience:** Anyone — human or AI — who gets asked "how does
> the OSS query bundle work?", "why is my agent reading from
> the cache and not from `FUND_DATA_DB`?", "how do I publish a
> new bundle?", or "what's the difference between the query
> bundle and the full archive?". This is the **answer script**
> for the cloud distribution path. Pair with
> [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md)
> for diagrams and code anchors.
>
> **Use it when:**
> - Onboarding a new operator or agent to the data plane.
> - Reviewing a PR that touches `fund_cloud.py`,
>   `fund_cli cloud` subcommands, or the
>   `.github/workflows/nightly.yml` upload step.
> - Debugging a report of "the agent is reading from the
>   wrong DB" or "the upload said success but nothing
>   changed" or "the bundle's sha256 mismatches".
> - Fielding a question about the privacy boundary
>   between the public query bundle and the private
>   full archive.
> - Planning a schema migration or a new table
>   addition.
>
> **Do NOT use it when:**
> - The question is about the in-process bootstrap
>   (covered in
>   [`fund-lookup-pipeline.md` §3.2](./fund-lookup-pipeline.md#32-cloud-bootstrap--fund_cloudensure_project_bundle)).
> - The question is about the data plane (search,
>   sync, coverage) — use the matching playbook.
> - The question is about installing the skill into
>   an agent platform → use
>   [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md).

---

## TL;DR (60-second answer)

The `fund-data` cloud bundle is the **distribution path**
for a fresh OpenClaw / Codex / Claude daemon. It is a
gzipped query-only SQLite (11 data tables, ~700 MB
gzipped) plus a manifest with a sha256, hosted on a public
OSS bucket. The pull is a trust chain: fetch the manifest
over HTTPS, verify the schema version, download the .gz,
verify the sha256, gunzip, atomic-rename into place, write
a `current.json` pointer. The build is the mirror:
attach the source SQLite, copy 11 tables with
`INSERT ... SELECT`, gzip, write the manifest, upload
the three artifacts to OSS in the order `gz → sha256 →
manifest` so a consumer that polls `current/manifest.json`
never sees a half-published release.

The defining characteristics are:

- **Public read, private write.** The query bundle is on
  a public bucket (`fund-data-public-l`, `cn-shanghai`).
  The full archive (with `raw_responses` containing caller
  IPs) goes to a private bucket / private prefix.
- **Three-file triplet.** `fund_data_query.sqlite.gz`,
  its `.sha256` sidecar, and a `manifest.json` describing
  both. The triplet is the unit of publish.
- **Three-layer trust chain.** Manifest validation,
  HTTPS for the download URL, sha256 of the downloaded
  file. A malicious manifest is rejected before the
  download starts; a tampered .gz is rejected before the
  gunzip.
- **Atomic everything.** `.download` files for the
  in-flight .gz and .db, then `os.replace` to the final
  paths. A crash mid-pull leaves `.download` files that
  the next pull overwrites; the cache is never in a
  half-published state.
- **Two storage tiers, by design.** Query bundle strips
  `raw_responses` / `sync_runs` / `sync_failures`. Full
  archive keeps them. The choice is the privacy
  boundary.

---

## The full answer template (use this skeleton)

When asked "how does the cloud bundle work?", structure
the answer in **four paragraphs**, one per side. Order
matters — it matches the publish-then-consume runtime
flow.

### Paragraph 1 — Publish side

> The operator (or CI) runs `fund-cli cloud build-bundle`
> to produce the triplet. `build_bundle` (line 71) opens
> the source SQLite, attaches it to a fresh query DB,
> and runs `CREATE TABLE + INSERT INTO ... SELECT * FROM`
> for each of the 11 query tables. The destination
> pragmas are set to `journal_mode=OFF`,
> `synchronous=OFF`, `temp_store=MEMORY` — durability is
> not needed because the source is canonical. The build
> creates indexes that match the agent read patterns
> (`funds.fund_name`, `funds.fund_type`, `nav_history.nav_date`,
> `fund_profiles.fund_company`, `fund_managers.current_fund_codes`),
> runs `VACUUM` to reclaim half-empty pages, gzips the
> result at `compresslevel=9`, computes the sha256, and
> writes `manifest.json` with `kind: "fund-data-cloud-bundle"`,
> `schema_version: 1`, and per-table row counts.

### Paragraph 2 — Upload side

> The operator (or CI) then runs `fund-cli cloud upload`,
> which shells out to `ossutil cp -f local oss://...`
> three times. `-f` is **required** — without it, ossutil
> prompts "y or N" on existing keys and the non-interactive
> shell silently no-ops. The upload order is **gz first,
> then sha256, then the manifest at `current/manifest.json`**,
> which is the consumer-facing pointer. A consumer that
> polls the manifest never sees a half-published release:
> either the manifest points to the previous version
> (no change) or to the new version (everything is
> already there). Uploading the manifest before the .gz
> would create a window where a consumer reads a
> manifest that references a non-existent .gz.

### Paragraph 3 — Pull side

> On the consumer, any tool call without an explicit `db`
> argument triggers `_maybe_bootstrap_cloud`, which calls
> `ensure_project_bundle`. That function is a 5-step gate:
> if `FUND_DATA_DB` is set, skip; if `FUND_DATA_AUTO_PULL=0`,
> skip and return `fallback: "api"`; if `current.json` is
> present and points at an existing file, reuse the cache;
> else call `pull_bundle(manifest_url)`. `pull_bundle`
> reads the manifest, validates `kind` / `version` /
> `schema_version` / `files.query_db.{sha256,url|path}`,
> downloads the .gz to a `.download` file, sha256-verifies
> against the manifest, gunzips to a `.download` db, then
> `os.replace`s both to the final paths. A `current.json`
> is written atomically with the version pointer.

### Paragraph 4 — Status and the privacy boundary

> The status tool (`fund_cloud_status` MCP, `fund-cli
> cloud status` CLI) reports three views: the local
> cache (what is installed), the remote manifest (what
> is the latest), and a comparison (`update_available:
> bool`). The query bundle **strips the three audit
> tables** — `raw_responses` (which contains caller IPs
> in upstream HTTP headers), `sync_runs`, and
> `sync_failures`. The full archive (`cloud archive-full`)
> keeps them and is meant for private operator backup,
> not public distribution. The manifest's
> `privacy: "private"` field is the warning; the team's
> `archive-full` documentation explicitly says "store
> in a private bucket or private object prefix".

---

## The 12 most-asked questions (with full answers)

These are the questions that come up the most in onboarding,
support, and PR review. **Answer them in the order they
appear here, with the same level of detail** — these are
the explanations the team has settled on after multiple
rounds of "but why?".

### Q1. Why is the query bundle public but the full archive private?

- **The query bundle strips the three audit tables** —
  `raw_responses`, `sync_runs`, `sync_failures`. The
  remaining 11 tables are the public data plane: fund
  names, NAV history, snapshots, holdings, profiles,
  etc. The data is already public from Eastmoney /
  AkShare; the bundle is a download convenience, not
  a privacy expansion.
- **`raw_responses` is the leak source.** It stores
  full upstream HTTP bodies, including any
  `X-Forwarded-For` or other caller-IP headers that
  some upstream proxies add. A consumer's
  `raw_responses` would expose the consumer's IP to
  every other consumer that pulls the bundle.
- **`sync_runs` and `sync_failures` are operator
  telemetry.** They are the per-sync audit log and
  the failure queue; an agent on a different machine
  has no use for them.
- **The full archive keeps all three tables** for
  private operator use cases (audit, rebuild, debug).
  The manifest's `privacy: "private"` field is the
  warning, and the team's `archive-full`
  documentation explicitly says "store in a private
  bucket or private object prefix". Publishing the
  full archive to a public prefix is a data leak,
  not a configuration error.

### Q2. Why is the upload order `gz → sha256 → manifest`?

- **`manifest.json` at `current/manifest.json` is the
  consumer-facing pointer.** A consumer that polls
  this file and follows its references must never see
  a half-published state.
- **If the manifest is uploaded first**, a consumer
  in between would read a manifest that points to a
  `.gz` that has not been uploaded yet, and the
  download would 404. The consumer would either
  retry (and maybe succeed on a later attempt, but
  with no guarantee of ordering) or report the
  failure.
- **If the `.gz` and `.sha256` are uploaded first**,
  the manifest upload is the atomic commit: from the
  consumer's perspective, the new version appears in
  one observable step.
- **The team has documented this** in `cloud upload`'s
  source and in `fund-data/SKILLS.md` §Cloud data
  cache. A manual `ossutil cp` outside the subcommand
  can violate the order; the subcommand enforces it.

### Q3. Why does the pull verify sha256 if the download is already over HTTPS?

- **HTTPS protects the channel, not the publisher.**
  A man-in-the-middle on the network cannot tamper
  with the bytes in transit, but an attacker that
  compromises the OSS bucket (or a malicious insider
  with write access) can replace the .gz with one
  that has a different sha256 than the manifest
  advertises. The sha256 check rejects that.
- **The manifest is the "what should be there"
  contract; the sha256 is the "verify it is actually
  there" check.** A consumer that skips the check
  trusts the publisher to never be compromised; a
  consumer that runs the check is safe against a
  class of attacks the publisher cannot defend.
- **The cost is one sha256 computation per pull.**
  Hashing a 700 MB file is sub-second on modern
  hardware. The latency is in the download, not the
  hash. The verification is free.

### Q4. Why is the build an `ATTACH` + `INSERT INTO ... SELECT` rather than `sqlite3 backup()`?

- **`backup()` is row-by-row and slow.** It streams
  every page through Python, which means the build
  takes an hour or more for a 2.5 GB source. The
  `ATTACH` + `INSERT INTO ... SELECT` path runs the
  copy inside SQLite, which is the only way to
  sustain the multi-hundred-MB/s transfer rate the
  build needs.
- **`backup()` is for online backups of a live
  source.** The build runs against a static source
  (the operator has stopped writes or is building
  from a checkpointed DB); `ATTACH` is the right
  primitive for the offline case.
- **The build adds `CREATE TABLE` from
  `sqlite_master.sql` for each table** rather than
  using the source's schema. This is to make the
  build robust to schema drift: if the source has
  a column the query DB does not (e.g. a
  half-applied migration), the build fails loudly
  rather than silently producing a wrong-shape DB.
  A `backup()` would copy the source's schema
  verbatim, including any drift.

### Q5. Why are the destination pragmas `journal_mode=OFF` / `synchronous=OFF` / `temp_store=MEMORY`?

- **The build is a one-shot copy, not a long-running
  database.** Durability is not needed because the
  source is the canonical DB; if the build crashes,
  the operator can re-run it.
- **`journal_mode=OFF` skips the WAL.** The WAL is
  for incremental durability; the build writes
  everything once and never reads back.
- **`synchronous=OFF` skips the fsync barrier.**
  The destination is a temp file that will be
  renamed atomically; if the host crashes mid-build,
  the operator re-runs the build.
- **`temp_store=MEMORY` keeps the build's
  intermediate structures in RAM** rather than on
  disk. SQLite's `VACUUM` and `ANALYZE` produce
  large temp tables; in-memory storage saves
  significant I/O.

### Q6. Why is the `current.json` pointer the source of truth, not the file mtime?

- **`mtime` is not a version pointer.** Two
  `cloud pull` invocations that produce the same
  version have different `mtime` values; two
  invocations that produce different versions
  could have the same `mtime` if the system clock
  is coarse. A consumer that decides "do I need to
  re-pull?" from `mtime` is making a guess.
- **`current.json` carries the version, sha256, and
  manifest URL explicitly.** A consumer reads
  `current.json`, gets the version, compares to its
  desired version, and decides. There is no
  guessing.
- **The pointer is atomic** (`_write_json_atomic`
  writes `.tmp` then `os.replace`). A consumer
  that reads `current.json` always sees either the
  old version or the new version, never a
  half-written file.

### Q7. Why does the pull download to `.download` and then `os.replace`?

- **Atomicity.** A consumer that crashes
  mid-download leaves a `.download` file on disk.
  The next pull overwrites the `.download` (the
  build always unlinks the destination before
  writing) and the final paths are never
  half-written.
- **No torn writes.** The `os.replace` is atomic
  on POSIX (and on Windows, since Python 3.3). A
  consumer that reads the final `.sqlite` while a
  pull is in progress sees either the old file or
  the new file, never a half-written file.
- **The `current.json` is written the same way**
  for the same reason. A consumer that reads
  `current.json` while a pull is in progress sees
  either the old pointer or the new pointer, never
  a half-written JSON.

### Q8. Why does `pull_bundle` re-read the manifest every time, instead of caching the manifest contents?

- **The manifest can change between pulls.** A
  publisher uploads a new `manifest.json` to
  advertise a new version. A consumer that caches
  the manifest would miss new versions until its
  cache is invalidated.
- **The manifest is small** (a few KB). The
  network cost of re-reading is sub-second; the
  validation cost is microseconds. Caching would
  save a few hundred milliseconds per pull at the
  cost of correctness.
- **The manifest is the version pointer.** A
  consumer that reads `current/manifest.json` and
  pulls the referenced `.gz` is following a
  standard CDN pattern: a small "what is
  available" file and a large "what I actually
  want" file. Caching the small file
  unnecessarily complicates the standard pattern.

### Q9. Why is the manifest's `kind` field checked?

- **A future schema migration could produce a
  different manifest kind.** The team envisions
  `fund-data-cloud-bundle-v2` for the next
  schema; an old consumer that sees the v2
  manifest should refuse to consume it rather
  than silently mis-parsing.
- **The `kind` check is a one-liner** that
  prevents a class of "silent compatibility
  breakage" bugs. A consumer that trusts the
  manifest without checking the kind could
  consume a future v2 manifest and produce
  unpredictable results.
- **The `schema_version` field is the more
  granular check.** The team uses both: `kind` is
  the "is this the right family?" check,
  `schema_version` is the "is this the right
  generation within the family?" check.

### Q10. Why does the build write `manifest.json` separately from the upload step?

- **The build is a deterministic function of the
  source DB.** Running the build twice on the
  same source produces the same triplet (the
  only non-determinism is the `updated_at`
  timestamp, which the build sets to
  `datetime.now(UTC)`). The manifest is the
  build's "what I produced" record.
- **The upload is a separate concern.** The
  build writes the triplet to local disk; the
  upload pushes it to OSS. Splitting them lets
  the operator inspect the build before
  uploading (the `ossutil` commands are
  destructive in the sense that they overwrite
  existing keys, even with `-f`).
- **The CI workflow runs build then upload in
  sequence.** A failure in build does not
  trigger an upload; a failure in upload does
  not re-trigger the build. The two steps
  have independent failure modes and independent
  retry policies.

### Q11. Why does `status` accept a `manifest_url` argument, and why is it optional?

- **The `manifest_url` is the source of the
  "what is the latest version" question.** A
  consumer that wants to know whether to pull
  needs to know both what is installed locally
  and what is available remotely. Without the
  URL, `status` reports only the local view.
- **The URL is optional** because not every
  consumer has a manifest URL configured. The
  `default_manifest_url()` helper provides the
  project OSS bucket as a default, but a
  consumer that points at a private mirror
  (e.g. an air-gapped deployment) would pass a
  custom URL. A consumer that does not pass one
  gets the local-only view; passing one
  upgrades the view to local-vs-remote.
- **The MCP `fund_cloud_status` tool wraps
  `status`** with the same optionality. An
  agent that calls the tool without arguments
  gets the local view; an agent that passes
  `manifest_url` gets the comparison.

### Q12. Why are there 11 query tables and not all 14?

- **The 11 query tables are the agent's data
  plane.** `funds`, `nav_history`, `snapshots`,
  `stock_holdings`, `fund_profiles`,
  `bond_holdings`, `industry_allocations`,
  `fee_structures`, `dividends`, `splits`,
  `fund_managers`. These are the tables the
  agent reads at runtime.
- **The 3 excluded tables are operator
  telemetry.** `raw_responses` (full upstream
  HTTP bodies; may contain caller IP),
  `sync_runs` (every sync call's audit row),
  `sync_failures` (every hard-failed sync
  call's queue row). These are meaningful to
  the operator who runs the backfill, not to
  an agent on a different machine.
- **The split is the privacy boundary.** A
  consumer that pulls the query bundle gets
  the data plane but not the audit trail. A
  consumer that wants the audit trail runs
  the backfill itself, or uses the private
  `cloud archive-full` command.
- **If a new table is added**, the team
  decides case-by-case: a new data table
  (e.g. `fund_benchmarks`) goes into
  `QUERY_TABLES`; a new audit table (e.g.
  `provider_call_log`) goes into
  `EXCLUDED_TABLES`. The default is "data in,
  audit out" — a table is excluded unless the
  team explicitly adds it to the query list.

---

## Design philosophy (the "why" of the seven-layer shape)

Read this section once and the rest of the playbook
becomes obvious.

1. **The trust chain is the contract.** Manifest
   validation + HTTPS + sha256 is the three-layer
   defense. A consumer that runs all three is safe
   against a class of attacks the publisher cannot
   defend (compromised bucket, malicious insider,
   MITM). The verification is cheap; the
   correctness is not.
2. **Atomicity everywhere.** `.download` files +
   `os.replace` for the .gz and the .db;
   `_write_json_atomic` for `current.json`. A
   consumer that crashes mid-pull or mid-publish
   always finds the cache in a consistent state.
   The cost is a small amount of "this file is
   half-written" complexity in the code; the
   benefit is a system that has no torn states.
3. **Two storage tiers, by design.** Query bundle
   is public; full archive is private. The choice
   is the privacy boundary, not a convenience. A
   new table added to the wrong tier is a bug
   waiting to be filed; the team's review checklist
   asks "is this table query or audit?" for every
   schema change.
4. **The manifest is the source of truth.** A
   consumer reads `current/manifest.json`, follows
   the references, downloads the .gz, verifies
   the sha256. The `current.json` on the consumer
   side is a cache of the manifest's version
   field; the manifest itself is the authoritative
   pointer.
5. **Upload order is part of the contract.** The
   `gz → sha256 → manifest` order ensures a
   consumer that polls the manifest never sees a
   half-published state. A manual `ossutil cp` that
   reorders the calls is a bug; the `cloud upload`
   subcommand enforces the order.
6. **The build is deterministic.** Running the
   build twice on the same source produces the
   same triplet (modulo `updated_at`). The
   non-determinism is the timestamp, which the
   team accepts as a single source of
   non-determinism in an otherwise reproducible
   pipeline.
7. **The query bundle is the "new agent
   starting point".** A fresh OpenClaw daemon
   should be able to pull the bundle and be
   operational in 30 seconds. The 21-hour
   AkShare backfill is the operator's path, not
   the agent's; the bundle is the team's
   contribution to making `fund-data` a
   "download and run" experience rather than a
   "build and run" experience.

---

## What NOT to say (anti-patterns)

These are common wrong answers the team has seen in PR
reviews and support threads. Avoid them.

- **"Just `wget` the .gz."** Without the
  manifest, there is no way to know the latest
  version or verify the sha256. The pull path
  (`fund-cli cloud pull` or the in-process
  bootstrap) is the only correct way to
  download.
- **"The bundle includes the audit tables."** It
  does not. `raw_responses` is excluded for
  privacy (`X-Forwarded-For` may contain caller
  IP); `sync_runs` and `sync_failures` are
  excluded because they are operator telemetry.
  A consumer that wants the audit trail runs
  `cloud archive-full` and stores the result
  privately.
- **"You can re-run the build on the same
  source to get the same triplet."** Almost.
  The `updated_at` field is
  `datetime.now(UTC)` and will differ between
  runs. Everything else (the sha256 of the .gz,
  the per-table row counts) is deterministic
  given the same source state.
- **"The pull is async."** It is not. The
  `cloud pull` CLI command (and the
  in-process `ensure_project_bundle`) is a
  blocking operation that downloads, verifies,
  gunzips, and writes `current.json` before
  returning. An agent that runs the pull in
  the foreground will block for ~30-60
  seconds; an agent that runs it in the
  background needs to poll `current.json` for
  completion.
- **"`-f` is optional for `ossutil cp`."** It
  is not. Without `-f`, ossutil prompts "y or
  N" on existing keys; in a non-interactive
  shell, the prompt is invisible and the
  upload silently no-ops. The `cloud upload`
  subcommand passes `-f`; a manual
  `ossutil cp` outside the subcommand must do
  the same.
- **"The bucket is private."** The query
  bundle is on `fund-data-public-l`, which is
  a public read bucket. The full archive
  goes to `fund-data-private` (or a private
  prefix); publishing the full archive to a
  public bucket is a data leak, not a
  configuration error.
- **"You can point `FUND_DATA_MANIFEST_URL`
  at a `file://` URL."** You can (`_open_location`
  handles `file` scheme), but it is not a
  supported configuration. The manifest is
  expected to be reachable over HTTPS; the
  `file://` path is an implementation detail
  that may change.

---

## How to keep this playbook accurate

The playbook is the team's *settled* explanation, not
the live code. When the code changes, update the
playbook in the same PR. The check is:

- A new table is added to `QUERY_TABLES` or
  `EXCLUDED_TABLES` → update §3.2 and Q12.
- The build pragmas change → update §3.3 and
  the philosophy section.
- The pull verification chain changes (new
  check added) → update §3.4 and the
  philosophy section.
- The upload order or the `ossutil` invocation
  changes → update §3.5 and Q2.
- A new env var lands → add it to the decision
  table in `fund-cloud-bundle-pipeline.md`.
- A new subcommand is added → update §6
  workflows.

If a PR changes any of the above and does not update
the playbook, request changes with a pointer to this
section.

---

## Related documents

- [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) —
  diagrams + code anchors + env var table.
- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —
  the in-process view of the bootstrap (what happens
  when an agent calls a tool without `db`).
- [`fund-search-playbook.md`](./fund-search-playbook.md) —
  Q8 explains why `--include-data` warns about
  `raw_responses`; Q13 explains why the 1-hour OSS
  TTL cache is a nightly backfill gotcha.
- [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) —
  the data-plane consumer of the bundle (where the
  backfill writes land after `cloud pull`).
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —
  the agent-facing skill manifest.
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —
  per-platform install layout; the "Cloud data cache"
  section has the canonical `cloud pull` /
  `cloud status` invocation.
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —
  the `default_db_path()` vs `doctor.py` divergence
  note (§Long-running pitfalls), which is the most
  common "wrong DB" report.
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —
  the v0.3.0 backlog items.
