# OpenClaw Active Completion Pipeline

OpenClaw is allowed to operate `fund-data` only through project-owned tools
and a controlled loop. The project remains the source of truth for
priorities, budgets, locking, audit logs, and the publish gate.

## The Loop

```text
cloud status / pull-if-needed
    -> self-audit (fund_self_audit)
    -> completion-plan (fund_completion_plan)
    -> [approval gate]
    -> completion-run (fund_completion_run)
    -> completion-verify (fund_completion_verify + doctor)
    -> [manual cloud publish]
```

OpenClaw is the operator. The project is the policy.

## Operating Modes

| Mode | Behavior | When to Use |
|---|---|---|
| `audit_only` | OpenClaw can inspect and produce plans, but cannot mutate the DB | onboarding, debugging, demos |
| `assisted` (default) | OpenClaw produces a plan and waits for human approval before execution | nightly/cron-style controlled operation |
| `autonomous` | OpenClaw can execute allowed work under strict budget and policy | experimental; do not enable without a successful assisted trail |

The mode is stored in `fund-data/config/openclaw-active-completion.json`
(deploy your own copy of the example). Do **not** rely on OpenClaw
prompts to enforce mode — the project runner is the gate.

## Project Commands

```bash
# 1. Bootstrap: status + doctor.
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud status
.venv-akshare/bin/python fund-data/scripts/doctor.py --skip-network --quiet

# 2. Refresh cache if missing/stale. Matching cache returns downloaded=false.
.venv-akshare/bin/python fund-data/scripts/fund_cli.py cloud pull

# 3. Build the priority queue.
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit \
    --limit 500 \
    --max-age-hours 36 \
    --output fund-data/data/openclaw_self_audit_queue.json

# 4. Convert the queue into a bounded plan.
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-plan \
    --queue fund-data/data/openclaw_self_audit_queue.json \
    --config fund-data/config/openclaw-active-completion.json \
    --output fund-data/data/openclaw_completion_plan.json

# 5a. In assisted mode, stop and ask for approval.
# 5b. In autonomous mode, execute under budget.
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-run \
    --plan fund-data/data/openclaw_completion_plan.json \
    --config fund-data/config/openclaw-active-completion.json \
    --confirm-execute

# 6. Verify.
.venv-akshare/bin/python fund-data/scripts/doctor.py --skip-network --quiet
.venv-akshare/bin/python fund-data/scripts/fund_cli.py self-audit \
    --limit 500 \
    --output fund-data/data/openclaw_self_audit_after.json
.venv-akshare/bin/python fund-data/scripts/fund_cli.py completion-verify \
    --before fund-data/data/openclaw_self_audit_queue.json \
    --after fund-data/data/openclaw_self_audit_after.json \
    --execution fund-data/data/openclaw_runs/<run-id>/execution.json
```

## What the Runner Will Never Do

- Process `P4` (structural-empty / naturally sparse) items.
- Touch blocked datasets (`dividends`, `splits`).
- Run two completion runs concurrently (lock file).
- Exceed the configured budget (elapsed minutes, provider calls, failure rate).
- Publish OSS as part of fill execution.

## What the Runner Will Always Do

- Write per-run artifacts under `fund-data/data/openclaw_runs/<run-id>/`:
  `plan.json`, `execution.json`, `verification.json`, `stdout.log`,
  `stderr.log`, `codes/*.txt`.
- Acquire and release the lock file:
  `fund-data/data/openclaw_active_completion.lock`.
- Mark a stale lock (>12h) as stale and replace it.
- Stop early if a provider's failure rate exceeds the policy budget.

## MCP Tools

| Tool | Purpose | Mutates? |
|---|---|---|
| `fund_completion_plan` | Convert a self-audit queue JSON into bounded batches | No (read-only) |
| `fund_completion_run` | Execute a plan under budget; requires `confirm_execute: true` | Yes (local DB only) |
| `fund_completion_verify` | Compare before/after queue + execution report | No (read-only) |

`fund_completion_run` never publishes OSS. Publishing is a separate
operator action (`fund_cli.py cloud build-bundle` + `cloud upload`).

## Provider Policy

The default provider policy (see `provider_policy` in the example config)
leans on Eastmoney for `nav_history` and `snapshots` (fast, stable) and
uses `auto` (AkShare/Investoday/Tushare chain) for everything else. See
the `provider_notes` block in the example config for the current
known-good mapping and the AkShare v1.18.64 schema-drift caveat.

## Operational Checklist

1. The first time you onboard a new OpenClaw, start in `audit_only`
   for at least one cycle so the plan output is reviewable.
2. Switch to `assisted` for the first real fill. Review the plan JSON
   before approving.
3. After at least one successful assisted run, consider `autonomous`
   with a conservative budget.
4. Publishing remains manual until several successful runs have
   validated the verification path.
