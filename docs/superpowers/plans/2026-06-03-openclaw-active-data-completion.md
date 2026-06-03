# OpenClaw Active Data Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let OpenClaw safely operate this project as an active data-completion agent: inspect the local/OSS data state, decide what should be filled next, run bounded provider refreshes, verify the result, and optionally publish a new OSS query bundle.

**Architecture:** Use a controlled loop instead of giving OpenClaw unlimited provider access. The loop is `cloud pull -> self-audit queue -> completion plan -> bounded execution -> verification -> optional publish`. OpenClaw should call project-owned tools/CLI commands only; the project owns priority, budgets, locking, audit logs, and publish gates.

**Tech Stack:** Existing `fund-data` Python package, SQLite, MCP stdio server, existing `fund_cli.py`, existing `batch_sync_funds`, existing OSS bundle commands, new self-audit queue from `docs/superpowers/plans/2026-06-03-fund-self-audit-priority-queue.md`.

---

## Executive Summary

The right way to put this project into OpenClaw is not:

```text
OpenClaw freely calls every fund_* provider tool until the data looks complete.
```

That is too risky: it can waste provider calls, retry structural-empty datasets, hit known AkShare schema drift, pollute raw audit tables, or publish a half-filled bundle.

The right shape is:

```text
OpenClaw runs project self-audit
  -> project produces prioritized work queue
  -> OpenClaw selects allowed P1/P2 work under budget
  -> project runner executes batch refresh
  -> project verifies row growth and doctor health
  -> only then publish OSS bundle if policy allows
```

OpenClaw is the operator. The project remains the source of truth for what is allowed, what is expected empty, which providers are degraded, and when a result is safe to publish.

## Required Prerequisite

Implement this plan first:

```text
docs/superpowers/plans/2026-06-03-fund-self-audit-priority-queue.md
```

That gives the project:

- `fund_self_audit`: full project priority queue.
- `fund_health_check`: single-fund diagnostic queue.
- P0-P4 priority classification.
- structural-empty / naturally sparse classification.
- recommended CLI/MCP actions.
- explicit `auto_fill_executed: false`.

OpenClaw active completion depends on that queue. Do not build an autonomous filler without a self-audit queue.

## Operating Modes

Support three modes. Default to `assisted`.

| Mode | Behavior | Use Case |
|---|---|---|
| `audit_only` | OpenClaw can inspect and produce plans, but cannot mutate the DB | onboarding, debugging, demos |
| `assisted` | OpenClaw produces a completion plan and waits for human approval before execution | recommended default |
| `autonomous` | OpenClaw can execute allowed work under strict budget and policy | nightly/cron-style controlled operation |

The mode must be stored in a project config file, not hidden in an OpenClaw prompt.

Create:

```text
fund-data/config/openclaw-active-completion.example.json
```

Example:

```json
{
  "mode": "assisted",
  "database": {
    "prefer_cloud_cache": true,
    "cache_dir": "~/.cache/fund-data",
    "full_db_path": "fund-data/data/fund_data.sqlite"
  },
  "allowed_priorities": ["P1", "P2", "P3"],
  "allowed_datasets": [
    "fund_profiles",
    "nav_history",
    "snapshots",
    "stock_holdings",
    "bond_holdings",
    "industry_allocations",
    "fee_structures"
  ],
  "blocked_datasets": ["dividends", "splits"],
  "provider_policy": {
    "fund_profiles": "auto",
    "nav_history": "eastmoney",
    "snapshots": "eastmoney",
    "stock_holdings": "auto",
    "bond_holdings": "auto",
    "industry_allocations": "auto",
    "fee_structures": "auto"
  },
  "budgets": {
    "max_funds_per_run": 100,
    "max_provider_calls_per_run": 300,
    "max_elapsed_minutes": 30,
    "concurrency": 4,
    "min_interval_seconds": 0.2,
    "max_failure_rate": 0.25
  },
  "publish": {
    "mode": "manual",
    "min_rows_changed": 100,
    "require_tests": true,
    "require_doctor": true
  }
}
```

## OpenClaw Environment Setup

The OpenClaw MCP server should run from this repo with the project venv.

Recommended command:

```bash
/Users/xiongjiali/Desktop/code/fundData/.venv-akshare/bin/python \
  /Users/xiongjiali/Desktop/code/fundData/fund-data/scripts/fund_mcp.py
```

Recommended environment:

```bash
FUND_DATA_CACHE_DIR=/Users/xiongjiali/.cache/fund-data
FUND_DATA_AUTO_PULL=1
FUND_DATA_MANIFEST_URL=https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json
PYTHONPATH=/Users/xiongjiali/Desktop/code/fundData/fund-data
```

Optional provider credentials:

```bash
INVESTODAY_API_KEY=...
INVESTDATA_API_KEY=...
TUSHARE_TOKEN=...
```

Important:

- Do not set `FUND_DATA_DB` in OpenClaw unless the operator wants to force a full local DB.
- For query/demos, let `default_db_path()` resolve to the OSS query cache.
- For active fill that mutates rows, use an explicit full DB path or a writable cache DB. Do not mutate a read-only packaged DB.

## Active Completion Loop

OpenClaw should follow this order exactly.

### Step 1: Bootstrap and Status

Run:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud status
.venv-akshare/bin/python fund-data/scripts/doctor.py --skip-network --quiet
```

Expected:

- cloud cache installed
- default DB exists
- missing tables empty
- provider status visible

If cloud cache is stale or missing:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud pull
```

### Step 2: Self-Audit

Run after the self-audit plan is implemented:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit \
  --limit 500 \
  --max-age-hours 36 \
  --output fund-data/data/openclaw_self_audit_queue.json
```

OpenClaw reads:

```text
fund-data/data/openclaw_self_audit_queue.json
```

and only considers queue items with:

- `priority` in allowed priorities
- `dataset` in allowed datasets
- `issue_type` in `missing` or `stale`
- `auto_fill_executed` is false

OpenClaw must ignore:

- `P4`
- `issue_type = structural_empty`
- `issue_type = naturally_sparse`
- datasets in `blocked_datasets`

### Step 3: Build Completion Plan

The project should expose a planning command:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-plan \
  --queue fund-data/data/openclaw_self_audit_queue.json \
  --config fund-data/config/openclaw-active-completion.json \
  --output fund-data/data/openclaw_completion_plan.json
```

The plan groups work by dataset/provider/batch flag:

```json
{
  "mode": "assisted",
  "dry_run": true,
  "allowed_to_execute": false,
  "summary": {
    "planned_items": 80,
    "estimated_provider_calls": 120,
    "estimated_minutes": 18
  },
  "batches": [
    {
      "batch_id": "openclaw-20260603T210000Z-profile-p1",
      "priority": "P1",
      "dataset": "fund_profiles",
      "provider": "auto",
      "codes": ["000001", "000003"],
      "codes_file": "fund-data/data/openclaw_runs/20260603T210000Z/codes/fund_profiles_p1.txt",
      "command": ".venv-akshare/bin/python fund-data/scripts/fund_cli.py batch-sync --codes-file fund-data/data/openclaw_runs/20260603T210000Z/codes/fund_profiles_p1.txt --include-profile --provider auto --concurrency 4 --batch-id openclaw-20260603T210000Z-profile-p1"
    }
  ],
  "blocked": [
    {
      "fund_code": "110022",
      "dataset": "splits",
      "reason": "P4 naturally sparse dataset is blocked by policy"
    }
  ]
}
```

### Step 4: Approval Gate

In `assisted` mode, OpenClaw stops here and reports:

- number of planned funds
- provider calls estimate
- command preview
- blocked items
- expected rows to improve

It must not execute.

In `autonomous` mode, it can execute only if:

- config mode is `autonomous`
- plan is under budget
- lock can be acquired
- doctor precheck passes
- queue has P1/P2/P3 only
- no blocked datasets appear in planned batches

### Step 5: Execute Completion Plan

The project should expose:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-run \
  --plan fund-data/data/openclaw_completion_plan.json \
  --config fund-data/config/openclaw-active-completion.json \
  --confirm-execute
```

Execution behavior:

- create run directory:

```text
fund-data/data/openclaw_runs/YYYYMMDDTHHMMSSZ/
```

- write:

```text
plan.json
execution.json
verification.json
stdout.log
stderr.log
codes/*.txt
```

- acquire lock:

```text
fund-data/data/openclaw_active_completion.lock
```

- run batches one by one
- stop if elapsed time exceeds budget
- stop if failure rate exceeds budget
- stop if provider returns repeated structural/API-surface errors
- record all skipped/failed items

The runner must call project commands/functions, not raw OpenClaw web scraping.

Allowed execution primitives:

```bash
fund_cli.py batch-sync --codes-file ... --include-profile ...
fund_cli.py batch-sync --codes-file ... --include-holdings ...
fund_cli.py batch-sync --codes-file ... --include-bonds ...
fund_cli.py batch-sync --codes-file ... --include-industries ...
fund_cli.py batch-sync --codes-file ... --include-fees ...
fund_cli.py nav CODE --refresh
fund_cli.py snapshot CODE
```

Do not execute:

- `cloud upload`
- `cloud archive-full`
- destructive SQLite cleanup
- public OSS publish

from the filler step.

### Step 6: Verify

After execution:

```bash
.venv-akshare/bin/python fund-data/scripts/doctor.py --skip-network --quiet
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit \
  --limit 500 \
  --output fund-data/data/openclaw_self_audit_after.json
```

The project should compute:

```json
{
  "before_queue_size": 1842,
  "after_queue_size": 1710,
  "rows_changed": 356,
  "improved_items": 132,
  "new_failures": 4,
  "doctor_ok": true,
  "publish_recommended": true
}
```

OpenClaw can summarize this, but the project should compute it.

### Step 7: Publish Gate

Publishing is separate from filling.

Only publish if:

- doctor is ok
- full tests or targeted publish checks pass
- rows changed >= `publish.min_rows_changed`
- public query bundle excludes audit tables
- operator policy allows publish

Manual publish:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud build-bundle \
  --source-db fund-data/data/fund_data.sqlite \
  --output-dir dist/openclaw-release \
  --base-url https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/releases/openclaw \
  --version openclaw-$(date -u +%Y%m%dT%H%M%SZ)
```

Then:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud upload \
  --release-dir dist/openclaw-release \
  --manifest dist/openclaw-release/manifest.json
```

Autonomous publish should be disabled until the filler has several successful assisted runs.

## Provider Strategy

Default provider policy:

| Dataset | Preferred Provider | Reason |
|---|---|---|
| `fund_profiles` | `auto` | Investoday can fill structured profiles when key exists; AkShare fallback works |
| `nav_history` | `eastmoney` | fastest and already cache-first |
| `snapshots` | `eastmoney` | stable for normal funds |
| `stock_holdings` | `auto` | AkShare/Investoday depending environment; watch schema drift |
| `bond_holdings` | `auto` | known AkShare drift; prefer Investoday fallback once implemented |
| `industry_allocations` | `auto` | known AkShare drift; prefer Investoday fallback once implemented |
| `fee_structures` | `auto` | AkShare/Eastmoney fallback |
| `dividends` | blocked by default | naturally sparse |
| `splits` | blocked by default | naturally sparse |

Known caution:

- AkShare 1.18.64 has known schema drift for stock/bond/industry paths.
- Do not let OpenClaw retry the same broken provider path endlessly.
- If failure rate for a dataset exceeds policy, mark dataset `provider_degraded` in the run report and stop that dataset.

## New Project Components

Create:

```text
fund-data/config/openclaw-active-completion.example.json
fund-data/scripts/fund_data/completion.py
fund-data/scripts/tests/test_completion_plan.py
fund-data/scripts/tests/test_completion_run.py
docs/agent-flows/openclaw-active-completion-pipeline.md
```

Modify:

```text
fund-data/scripts/fund_data/__init__.py
fund-data/scripts/fund_cli.py
fund-data/scripts/fund_mcp.py
docs/agent-flows/README.md
```

Optional later:

```text
.github/workflows/openclaw-active-completion.yml
```

Do not add a workflow until the local/assisted mode has passed several real runs.

## Proposed Python API

Add to `fund-data/scripts/fund_data/completion.py`:

```python
def load_completion_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load policy JSON and apply safe defaults."""


def build_completion_plan(
    *,
    queue_path: str | Path,
    config_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Turn self-audit queue JSON into executable batch groups."""


def run_completion_plan(
    *,
    plan_path: str | Path,
    config_path: str | Path | None = None,
    confirm_execute: bool = False,
) -> dict[str, Any]:
    """Execute a bounded completion plan. Refuse mutation unless confirmed and policy allows it."""


def verify_completion_run(
    *,
    before_queue_path: str | Path,
    after_queue_path: str | Path,
    execution_path: str | Path,
) -> dict[str, Any]:
    """Compare before/after audit state and summarize row improvement."""
```

## Proposed CLI

Add commands:

```bash
fund_cli.py completion-plan \
  --queue fund-data/data/openclaw_self_audit_queue.json \
  --config fund-data/config/openclaw-active-completion.json \
  --output fund-data/data/openclaw_completion_plan.json

fund_cli.py completion-run \
  --plan fund-data/data/openclaw_completion_plan.json \
  --config fund-data/config/openclaw-active-completion.json \
  --confirm-execute

fund_cli.py completion-verify \
  --before fund-data/data/openclaw_self_audit_queue.json \
  --after fund-data/data/openclaw_self_audit_after.json \
  --execution fund-data/data/openclaw_runs/<run-id>/execution.json
```

Defaults:

- `completion-plan` is read-only.
- `completion-run` defaults to dry-run unless `--confirm-execute` is present.
- `completion-run` refuses `mode=audit_only`.
- `completion-run` in `assisted` mode requires `--confirm-execute`.
- `completion-run` in `autonomous` mode still requires policy budgets to pass.

## Proposed MCP Tools

After CLI is stable, expose these to OpenClaw:

```text
fund_completion_plan(queue_path, config_path?, output_path?)
fund_completion_run(plan_path, config_path?, confirm_execute?)
fund_completion_verify(before_queue_path, after_queue_path, execution_path)
```

MCP tool descriptions must say:

- plan is read-only
- run mutates local DB only when confirmed
- run never publishes OSS
- publish is a separate operator action

## Task 1: OpenClaw Onboarding Config

**Files:**

- Create: `fund-data/config/openclaw-active-completion.example.json`
- Create: `docs/agent-flows/openclaw-active-completion-pipeline.md`

- [ ] **Step 1: Add example config**

Create `fund-data/config/openclaw-active-completion.example.json` using the JSON in the Operating Modes section.

- [ ] **Step 2: Add pipeline documentation**

Create `docs/agent-flows/openclaw-active-completion-pipeline.md` with:

```markdown
# OpenClaw Active Completion Pipeline

OpenClaw is allowed to operate fund-data only through project-owned tools.
The active loop is:

1. cloud status / pull
2. self-audit
3. completion-plan
4. completion-run
5. completion-verify
6. manual cloud publish

The runner never processes P4 structural-empty or naturally sparse rows.
The runner never publishes OSS as part of fill execution.
```

- [ ] **Step 3: Commit**

```bash
git add fund-data/config/openclaw-active-completion.example.json docs/agent-flows/openclaw-active-completion-pipeline.md
git commit -m "docs(openclaw): add active completion operating model"
```

## Task 2: Completion Plan Builder

**Files:**

- Create: `fund-data/scripts/fund_data/completion.py`
- Modify: `fund-data/scripts/fund_data/__init__.py`
- Test: `fund-data/scripts/tests/test_completion_plan.py`

- [ ] **Step 1: Write failing tests**

Test that a queue with P1 profile and P4 splits produces one planned profile batch and one blocked splits item.

Required assertions:

- `allowed_to_execute` is false in assisted mode
- P1 profile is planned
- P4 splits is blocked
- planned command includes `--include-profile`
- `estimated_provider_calls` respects codes count

- [ ] **Step 2: Implement `load_completion_policy`**

Safe defaults:

```python
DEFAULT_POLICY = {
    "mode": "assisted",
    "allowed_priorities": ["P1", "P2", "P3"],
    "blocked_datasets": ["dividends", "splits"],
    "budgets": {
        "max_funds_per_run": 100,
        "max_provider_calls_per_run": 300,
        "max_elapsed_minutes": 30,
        "concurrency": 4,
        "min_interval_seconds": 0.2,
        "max_failure_rate": 0.25
    },
    "publish": {"mode": "manual"}
}
```

- [ ] **Step 3: Implement `build_completion_plan`**

Rules:

- read self-audit JSON
- filter queue by priority/dataset
- group by `dataset`, `priority`, `recommended_cli`
- write codes files under `fund-data/data/openclaw_runs/<run-id>/codes/`
- cap fund count by budget
- compute estimates
- write plan JSON if `output_path` is provided

- [ ] **Step 4: Run tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_completion_plan.py'
```

- [ ] **Step 5: Commit**

```bash
git add fund-data/scripts/fund_data/completion.py fund-data/scripts/fund_data/__init__.py fund-data/scripts/tests/test_completion_plan.py
git commit -m "feat(completion): build OpenClaw completion plans"
```

## Task 3: Completion Runner

**Files:**

- Modify: `fund-data/scripts/fund_data/completion.py`
- Test: `fund-data/scripts/tests/test_completion_run.py`

- [ ] **Step 1: Write dry-run refusal tests**

Required assertions:

- `run_completion_plan(confirm_execute=False)` does not call subprocess or `batch_sync_funds`
- `mode=audit_only` refuses execution
- plan over `max_provider_calls_per_run` refuses execution
- run writes an execution report with `executed: false`

- [ ] **Step 2: Implement lock handling**

Use:

```text
fund-data/data/openclaw_active_completion.lock
```

Lock behavior:

- if lock exists and process appears alive, refuse
- if stale lock is older than 12 hours, mark stale and replace
- always remove lock on clean exit

- [ ] **Step 3: Implement bounded execution**

Runner should execute planned commands with `subprocess.run`.

Capture:

- command
- start/end time
- return code
- stdout path
- stderr path
- rows changed if the command returns JSON
- failure count

Stop when:

- elapsed minutes > budget
- failure rate > budget
- command exits nonzero for a non-transient reason

- [ ] **Step 4: Run tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_completion_run.py'
```

- [ ] **Step 5: Commit**

```bash
git add fund-data/scripts/fund_data/completion.py fund-data/scripts/tests/test_completion_run.py
git commit -m "feat(completion): run bounded OpenClaw fill plans"
```

## Task 4: CLI Surface

**Files:**

- Modify: `fund-data/scripts/fund_cli.py`
- Test: `fund-data/scripts/tests/test_fund_cli.py`

- [ ] **Step 1: Add CLI parser tests**

Add tests for:

- `completion-plan`
- `completion-run`
- `completion-verify`

Patch `fund_cli.fund_data.build_completion_plan`, `run_completion_plan`, and `verify_completion_run`.

- [ ] **Step 2: Add parser commands**

Add:

```bash
completion-plan --queue --config --output
completion-run --plan --config --confirm-execute
completion-verify --before --after --execution --output
```

- [ ] **Step 3: Run CLI tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_cli.py'
```

- [ ] **Step 4: Commit**

```bash
git add fund-data/scripts/fund_cli.py fund-data/scripts/tests/test_fund_cli.py
git commit -m "feat(cli): expose OpenClaw completion controls"
```

## Task 5: MCP Surface

**Files:**

- Modify: `fund-data/scripts/fund_mcp.py`
- Test: `fund-data/scripts/tests/test_fund_mcp.py`

- [ ] **Step 1: Add tools**

Add:

```text
fund_completion_plan
fund_completion_run
fund_completion_verify
```

- [ ] **Step 2: Safety in schemas**

`fund_completion_run` must require:

```json
{
  "plan_path": "...",
  "confirm_execute": true
}
```

The tool description must say it mutates only local DB and never publishes OSS.

- [ ] **Step 3: Run MCP tests**

```bash
.venv-akshare/bin/python -m unittest discover -s fund-data/scripts/tests -t fund-data -p 'test_fund_mcp.py'
```

- [ ] **Step 4: Commit**

```bash
git add fund-data/scripts/fund_mcp.py fund-data/scripts/tests/test_fund_mcp.py
git commit -m "feat(mcp): expose OpenClaw completion controls"
```

## Task 6: End-to-End Assisted Trial

**Files:**

- No code changes unless defects are found.

- [ ] **Step 1: Generate queue**

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit \
  --limit 50 \
  --output fund-data/data/openclaw_self_audit_queue.json
```

- [ ] **Step 2: Build plan**

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-plan \
  --queue fund-data/data/openclaw_self_audit_queue.json \
  --config fund-data/config/openclaw-active-completion.example.json \
  --output fund-data/data/openclaw_completion_plan.json
```

- [ ] **Step 3: Dry-run execution**

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-run \
  --plan fund-data/data/openclaw_completion_plan.json \
  --config fund-data/config/openclaw-active-completion.example.json
```

Expected:

- no DB mutation
- `executed: false`
- command previews present

- [ ] **Step 4: Assisted execution on tiny queue**

Use a config with:

```json
{
  "budgets": {
    "max_funds_per_run": 3,
    "max_provider_calls_per_run": 10,
    "max_elapsed_minutes": 5
  }
}
```

Run:

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-run \
  --plan fund-data/data/openclaw_completion_plan.json \
  --config /tmp/openclaw-small-policy.json \
  --confirm-execute
```

- [ ] **Step 5: Verify**

```bash
.venv-akshare/bin/python fund-data/scripts/doctor.py --skip-network --quiet
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit \
  --limit 50 \
  --output fund-data/data/openclaw_self_audit_after.json
```

- [ ] **Step 6: Commit docs/fixes**

```bash
git status --short
git add <changed-files>
git commit -m "chore(openclaw): verify active completion dry run"
```

## Task 7: Optional Publish Workflow

Only do this after at least one assisted fill run succeeds.

**Files:**

- Create: `docs/agent-flows/openclaw-active-publish-playbook.md`

Publish remains manual by default.

Document:

1. build bundle
2. upload release artifacts
3. upload manifest last
4. pull fresh bundle
5. run doctor against pulled bundle
6. confirm OpenClaw now reads new version

Do not implement autonomous publish in the first version.

## OpenClaw Prompt / Operating Instruction

After implementation, configure OpenClaw with this project instruction:

```text
You are operating fund-data through its MCP tools. Do not call provider refresh tools directly to improve coverage. First call fund_self_audit, then fund_completion_plan. In assisted mode, stop and report the plan. In autonomous mode, call fund_completion_run only when the plan is under policy budgets and confirm_execute is allowed by the project config. Never process P4 structural-empty or naturally sparse items. Never publish OSS from a completion run. After any run, call fund_completion_verify and doctor before reporting success.
```

This prompt is a guardrail, not the source of truth. The source of truth is the project config and completion runner.

## Acceptance Checklist

The integration is accepted only if all are true:

- OpenClaw can start the project MCP server with `.venv-akshare/bin/python`.
- `fund_self_audit` produces a queue without provider calls.
- `completion-plan` converts queue items into bounded batches.
- `completion-run` refuses to execute without confirmation or autonomous policy.
- `completion-run` never handles P4 items.
- `completion-run` writes run artifacts under `fund-data/data/openclaw_runs/`.
- lock prevents overlapping runs.
- failure-rate budget stops degraded providers.
- verification compares before/after queue state.
- publishing is not part of execution.
- full tests pass.

## Non-Goals

Do not implement these in the first version:

- fully autonomous OSS publishing
- always-on background daemon inside OpenClaw
- provider web scraping outside `fund-data`
- retrying `dividends` / `splits` by default
- retrying structural-empty holdings
- bypassing project budgets with prompt instructions

## Recommended Implementation Order

1. Implement the self-audit queue plan.
2. Add OpenClaw active completion config and docs.
3. Add completion-plan.
4. Add dry-run completion-run.
5. Add confirmed assisted completion-run.
6. Add MCP wrappers.
7. Do a tiny real fill run.
8. Only then consider autonomous mode.
