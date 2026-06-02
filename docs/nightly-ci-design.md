# Nightly CI design — fund-data data-plane health gate

**Status**: shipped (2026-06-01). The gate lives in
`.github/workflows/nightly.yml` (job `nightly-data-plane-gate`,
03:00 Asia/Shanghai / 19:00 UTC) and runs
`scripts/ci/nightly_health_check.py` against the local SQLite +
OSS bundle + manifest URL. This document is still the contract
that any future change to the gate has to honor — treat the
workflow as the source of truth for *what* runs, and this file
as the source of truth for *why* it runs and what the on-call
discipline is.

**Owner**: whoever picks up the next sprint's "CI gate" ticket.
**Last updated**: 2026-06-02.

## 1. Goal

Catch silent regressions in the data plane **before** an agent
that consumes the cloud bundle hits them. Concretely the gate
should answer three questions every night:

1. *Is the local SQLite still healthy?* — schema intact,
   sync_failures empty, coverage on the 12 tables has not
   regressed.
2. *Does the cloud bundle in OSS still match what we ship
   from main?* — every ``cloud pull`` consumer sees the same
   dataset the latest ``cloud build-bundle`` produced, with a
   matching sha256.
3. *Is the published manifest still reachable?* — the public
   HTTPS URL returns 200 and the ``current.json`` metadata is
   in sync with the version field of the just-built bundle.

The current ``fund_cli doctor`` and ``fund_cli cloud status``
subcommands are the building blocks; the nightly job is just a
composed pipeline that calls them in order and gates on the
exit code.

## 2. Trigger

GitHub Actions cron, 03:00 Asia/Shanghai (19:00 UTC, before
the Eastmoney business-day cutover). One job, ~5 minutes of
runtime, no matrix — the same runner shape is fine for every
night.

```yaml
on:
  schedule:
    - cron: '0 19 * * *'   # 03:00 Asia/Shanghai
  workflow_dispatch:        # allow manual run from the Actions tab
```

`workflow_dispatch` is important so the next operator can re-run
the gate when the build goes red and the doctor surfaces a
real failure; we do not want the gate locked behind a 24 h
wait.

## 3. Pipeline

**Five steps** as of 2026-06-02 (a pre-flight was added when
the gate grew the cloud-pull DB bootstrap; the four core
data-plane checks are unchanged). Each step has an
agent-friendly contract. The runner is a single shell
script that captures stdout/stderr per step and produces a
single JSON summary at the end that the workflow gate and
the alert hooks read.

| # | Step | Command | Pass criteria |
|---|------|---------|---------------|
| 0 | **pre-flight: pull query DB** | `fund_cli.py cloud pull --cache-dir /tmp/nightly-cache --output /tmp/cloud-pull-init.json` | exit 0 AND `/tmp/nightly-cache/releases/<ver>/fund_data_query.sqlite` exists AND `current.json` pointer is updated |
| 1 | **doctor** | `fund_cli.py doctor --skip-network --skip-sync-state --output /tmp/nightly-doctor.json` | exit 0 AND every `checks[*].ok == true` (note `--skip-sync-state` is required: the query DB excludes `raw_responses` / `sync_runs` / `sync_failures` per `fund_cloud.EXCLUDED_TABLES`) |
| 2 | **build-bundle** | `fund_cli.py cloud build-bundle --source-db <from step 0> --output-dir /tmp/nightly-release --manifest-output /tmp/nightly-release/manifest.json --output /tmp/nightly-build.json` | exit 0 AND `manifest.query_db.sha256` is present |
| 3 | **upload** | `fund_cli.py cloud upload --release-dir /tmp/nightly-release --manifest /tmp/nightly-release/manifest.json --output /tmp/nightly-upload.json` | exit 0 AND every `uploaded[*].remote` is on `oss://fund-data-public-l/` |
| 4 | **pull-and-verify** | `fund_cli.py cloud pull --manifest-url <manifest_url> --cache-dir /tmp/nightly-cache --output /tmp/nightly-pull.json` | exit 0 AND `pull.integrity_verified == true` AND `pull.sha256 == step 2 sha256` |

**Why a pre-flight is required**: the runner starts on a
clean checkout with no `fund_data.sqlite` on disk. The 5.4 GB
full DB is gitignored. Step 0 pulls the much smaller
(~50–100 MB) `fund_data_query.sqlite` from the project OSS
bucket, lands it in `/tmp/nightly-cache`, and updates
`current.json` so `fund_data.default_db_path()` (used by
doctor, build-bundle, and the runner's own `--db`
resolution) resolves to that file. Without step 0 the four
core steps all `FileNotFoundError` on the source DB.

**Why `--skip-sync-state` is required on doctor**: the
query bundle excludes `raw_responses` / `sync_runs` /
`sync_failures` (see `fund_cloud.EXCLUDED_TABLES`). Doctor's
`sync_failures` / `coverage` / `backfill_stale` checks all
`SELECT COUNT(*)` from these tables and would fail with
`no such table`. The flag short-circuits those three
checks; the gate is verifying the data plane, not the
operator's local sync state, so this is the right shape.

If any step fails, the workflow exits 1 and posts the failed
step's `*.json` as an artifact (`nightly-doctor-fail.json`,
`nightly-upload-fail.json`, etc.) so the on-call human can
diff against the previous night's run.

## 4. What the gate is *not*

- **It is not a unit-test run.** That lives in the existing
  PR-time `pytest` workflow. The nightly job is a *data*
  gate, not a code gate.
- **It is not a backfill.** The gate is read-only against the
  local SQLite. If the doctor surfaces a coverage gap, the
  on-call human runs a one-off backfill — the gate is the
  canary, not the fix.
- **It is not a full-coverage sweep.** Step 1 checks
  *non-regression* against last night's coverage. Step 2
  *rebuilds* the bundle. The full weekly/monthly coverage
  expansion is a separate `weekly-expand.yml` workflow that
  is out of scope for this design.

## 5. Failure handling

The contract is: **transient failures get retried with
exponential backoff; data failures get escalated.** The
runner separates the two via the JSON envelope.

| Step | Failure class | Behavior |
|------|---------------|----------|
| 1 (doctor) | `database.ok == false` (schema drift) | **escalate** — humans must look |
| 1 (doctor) | `sync_failures.ok == false` and `count > 0` | **escalate** — humans must look |
| 1 (doctor) | `coverage.ok == false` (any table lost > 2 percentage points vs last night) | **escalate** |
| 2 (build-bundle) | `manifest.query_db.sha256` mismatch across rerun | **escalate** — the build is non-deterministic |
| 2 (build-bundle) | `ossutil` network error (timeout, 5xx) | **transient** — retry 3x with 60s/120s/240s backoff |
| 3 (upload) | `ossutil cp` SHA mismatch on the destination | **escalate** — the bucket rejected our upload |
| 3 (upload) | `ossutil` 403 (credential expiry) | **escalate** — the deploy key rotated |
| 4 (pull) | `pull.integrity_verified == false` | **escalate** — the bundle is corrupt in transit |
| 4 (pull) | `pull.sha256 != step 2 sha256` | **escalate** — the manifest was tampered with |

The runner pattern:

```python
def run_step(name: str, cmd: list[str]) -> StepResult:
    """Run a CLI step, capture stdout/stderr, return a StepResult.

    Does NOT retry: the caller decides whether the failure is
    transient (e.g. ossutil timeout) or data (e.g. sha256
    mismatch) and acts accordingly. Keeping the retry logic
    out of the step runner means the failure envelope is
    stable -- the agent can branch on the JSON status field
    without a separate retry trail.
    """
```

The retry-vs-escalate split is exactly the discipline
documented in `fund-data/AGENTS.md` under "Backfill performance
notes" -- the same rule applies to CI: re-trying a real
data failure only hides the regression.

## 6. Outputs (per night)

Every run produces a single JSON envelope at
`/tmp/nightly-summary.json` and a separate per-step JSON at
`/tmp/nightly-<step>.json`. The summary has this shape:

```json
{
  "run_id": "2026-06-02T19:00:00Z",
  "git_sha": "7d51ead",
  "started_at": "2026-06-02T19:00:00Z",
  "finished_at": "2026-06-02T19:04:23Z",
  "overall_ok": true,
  "steps": [
    {
      "name": "doctor",
      "ok": true,
      "exit_code": 0,
      "checks": [
        {"name": "database", "ok": true},
        {"name": "sync_failures", "ok": true, "count": 0},
        {"name": "coverage", "ok": true, "total_funds": 26936}
      ]
    },
    {
      "name": "build-bundle",
      "ok": true,
      "exit_code": 0,
      "sha256": "c47c88a0cde9f74441b2cccb3660a1bd999e9fb48473510a05d3a86682119146",
      "size_bytes": 121246785
    },
    {
      "name": "upload",
      "ok": true,
      "exit_code": 0,
      "manifest_url": "https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json"
    },
    {
      "name": "pull-and-verify",
      "ok": true,
      "exit_code": 0,
      "integrity_verified": true
    }
  ]
}
```

The shape is *intentionally* close to the per-step JSON
envelopes already locked down by the unit tests in
`tests/test_doctor.py`, `tests/test_fund_cloud.py`, and
`tests/test_fund_mcp.py` -- a new agent should be able to
parse any of those envelopes with the same code path.

## 7. Files this design will touch

When the implementation PR lands, the diff will be roughly:

- `scripts/ci/nightly_health_check.py` — new, the runner
  shell that calls the four steps and emits the summary.
- `.github/workflows/nightly.yml` — new, the cron trigger
  and the matrix-less job.
- `tests/test_nightly_health_check.py` — new, locks the
  retry-vs-escalate split and the summary envelope.
- `fund-data/SKILL.md` — update, add a "Nightly CI gate"
  section that points at this design doc and the workflow
  file so the next agent does not have to dig for it.

Roughly 250-400 lines of new code total. The runner is the
largest piece because it is the only place that does
exit-code + JSON-envelope coordination.

## 8. Pre-flight checklist before implementing

- [ ] Confirm the GitHub Actions runner has ossutil and the
  `~/.ossutilconfig` deploy key installed (currently the
  key is the user's personal one; for CI we need a
  `fund-data-ci` deploy-only key with `PutObject` and
  `GetObject` on the `fund-data-public-l` bucket).
- [ ] Decide whether the runner talks to Eastmoney at all.
  The doctor step is `--skip-network` so the live probe is
  already off, but step 2 builds the bundle from the local
  SQLite, which already needs the local data to be
  current. **No network calls are needed for the gate** --
  the design is read-only against the running data plane.
- [ ] Confirm the cron schedule does not collide with the
  existing `weekly-expand.yml` or any other scheduled
  workflow. 03:00 Asia/Shanghai / 19:00 UTC is currently
  empty.
- [ ] Update the `feat/cloud-upload-oss` README to mention
  the gate. Once the gate exists, an agent on a fresh
  checkout can run `nightly_health_check.py` locally and
  see the same data-plane health status that CI sees.

## 9. Out of scope (and where it lives instead)

- **Coverage expansion** — handled by the existing
  `scripts/akshare_capability_backfill.py` and
  `scripts/fee_only_backfill.py`. The gate only reports
  regressions; it does not run the fix.
- **Cloud bundle version pinning** — `cloud pull` already
  accepts `--manifest-url`; pinning a specific version is a
  consumer concern, not a CI gate concern.
- **Backfill state recovery** — the
  `mavis cron self backfill-monitor` pattern is the
  correct surface for a long-running pull that needs
  resume; the nightly gate is a canary, not a driver.
- **Alert routing** — GitHub repo notifications are enough
  for v1. PagerDuty / Lark webhook routing is a follow-up
  ticket once we have on-call coverage.

## 10. Why this design and not a "just run pytest" gate

The existing PR-time `pytest` job proves *the code compiles
and the unit tests pass.* It does not prove that the data
plane is healthy: a perfectly green PR that adds an
`akshare_capability_backfill.py` flag can leave the local
SQLite with 13,000 funds that have no `stock_holdings`
because the bulk runner no longer matches them. That bug
shows up only when an agent queries `fund_coverage_report`
on the cloud bundle and gets back a half-empty result.

The nightly gate is the layer that catches *data drift*
specifically. It is read-only against the running system
(no new backfill, no new sync), it is bounded in runtime
(<5 min), and it produces an artifact that the on-call
human can diff against the previous night without reading
prose.
