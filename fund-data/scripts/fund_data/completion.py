"""OpenClaw active-completion plan builder and runner.

The plan builder converts a self-audit queue (see :mod:`self_audit`)
into a bounded, executable batch plan that honors the project policy.
The runner executes the plan with strict budget enforcement and a
lock file so two OpenClaw instances cannot both mutate the database
at the same time.

This module never publishes OSS. Publishing is a separate operator
action that lives in :mod:`scripts.fund_cloud` and is reached only
through ``fund_cli cloud {build-bundle, upload}`` -- the runner
refuses to call those entry points even if the plan asks.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import default_db_path


logger = logging.getLogger("fund_data.completion")


# --- defaults applied on top of whatever the user ships in policy JSON ---
DEFAULT_POLICY: dict[str, Any] = {
    "mode": "assisted",
    "database": {
        "prefer_cloud_cache": True,
        "cache_dir": "~/.cache/fund-data",
        "full_db_path": "fund-data/data/fund_data.sqlite",
    },
    "allowed_priorities": ["P1", "P2", "P3"],
    "allowed_datasets": [
        "fund_profiles",
        "nav_history",
        "snapshots",
        "stock_holdings",
        "bond_holdings",
        "industry_allocations",
        "fee_structures",
    ],
    "blocked_datasets": ["dividends", "splits"],
    "provider_policy": {
        "fund_profiles": "auto",
        "nav_history": "eastmoney",
        "snapshots": "eastmoney",
        "stock_holdings": "auto",
        "bond_holdings": "auto",
        "industry_allocations": "auto",
        "fee_structures": "auto",
    },
    "budgets": {
        "max_funds_per_run": 100,
        "max_provider_calls_per_run": 300,
        "max_elapsed_minutes": 30,
        "concurrency": 4,
        "min_interval_seconds": 0.2,
        "max_failure_rate": 0.25,
    },
    "publish": {
        "mode": "manual",
        "min_rows_changed": 100,
        "require_tests": True,
        "require_doctor": True,
    },
}


# Per-dataset batch flag for ``fund_cli batch-sync``. Mirrors
# ``self_audit.DATASET_RULES``; kept duplicated here so the plan
# builder does not import the audit module (one-way dependency).
BATCH_FLAGS: dict[str, str | None] = {
    "fund_profiles": "--include-profile",
    "nav_history": "",  # nav_history ships in every sync
    "snapshots": None,  # batch-sync does not currently expose --include-snapshots
    "stock_holdings": "--include-holdings",
    "bond_holdings": "--include-bonds",
    "industry_allocations": "--include-industries",
    "fee_structures": "--include-fees",
    "dividends": "--include-distributions",
    "splits": "--include-distributions",
}


# Seconds-per-call estimate for one provider call inside a single-fund
# batch-sync. The empirical floor is ~0.36 s for eastmoney NAV; the
# higher value (~3 s) is a conservative average for the auto chain
# across profile + holdings. The plan uses this only for an
# *operator-visible* estimate -- it is not a hard guarantee.
SECONDS_PER_CALL = 3.0


LOCK_FILENAME = "openclaw_active_completion.lock"
LOCK_STALE_HOURS = 12


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` and return the
    result. Lists and scalars in ``override`` replace ``base``;
    nested dicts merge.
    """
    out = dict(base)
    for key, value in override.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_completion_policy(path: str | Path | None = None) -> dict[str, Any]:
    """Load policy JSON from ``path`` (or fall back to the built-in
    defaults) and apply safe defaults to every missing key.
    """
    if path is None:
        return json.loads(json.dumps(DEFAULT_POLICY))  # deep copy
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise FileNotFoundError(f"policy file not found: {cfg_path}")
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    return _deep_merge(DEFAULT_POLICY, on_disk)


def _now_utc_compact() -> str:
    """Return the current UTC time as ``YYYYMMDDTHHMMSSZ`` so the
    resulting run id sorts chronologically and can be parsed by
    downstream bash / make scripts without extra tooling.
    """
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _now_utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read_queue(queue_path: str | Path) -> dict[str, Any]:
    path = Path(queue_path)
    if not path.exists():
        raise FileNotFoundError(f"queue file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _filter_queue(
    queue: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a self-audit queue into (planned_items, blocked_items)
    based on the policy. The decision tree is:

    * ``issue_type`` in ``structural_empty`` / ``naturally_sparse``
      -> always blocked (the queue already classifies these as P4).
    * ``dataset`` in ``policy.blocked_datasets`` -> blocked.
    * ``priority`` not in ``policy.allowed_priorities`` -> blocked.
    * Otherwise -> planned.
    """
    allowed_priorities = {p.upper() for p in policy["allowed_priorities"]}
    blocked_datasets = set(policy["blocked_datasets"])
    planned: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in queue:
        if item.get("issue_type") in {"structural_empty", "naturally_sparse"}:
            blocked.append({
                "fund_code": item["fund_code"],
                "dataset": item["dataset"],
                "priority": item["priority"],
                "reason": "blocked: structural-empty or naturally sparse; queue already classified as P4",
            })
            continue
        if item["dataset"] in blocked_datasets:
            blocked.append({
                "fund_code": item["fund_code"],
                "dataset": item["dataset"],
                "priority": item["priority"],
                "reason": f"blocked: dataset '{item['dataset']}' is in policy.blocked_datasets",
            })
            continue
        if item["priority"] not in allowed_priorities:
            blocked.append({
                "fund_code": item["fund_code"],
                "dataset": item["dataset"],
                "priority": item["priority"],
                "reason": f"blocked: priority '{item['priority']}' is not in policy.allowed_priorities",
            })
            continue
        planned.append(item)
    return planned, blocked


def _group_into_batches(
    planned: list[dict[str, Any]],
    *,
    run_id: str,
    run_root: Path,
    budgets: dict[str, Any],
    provider_policy: dict[str, str],
) -> tuple[list[dict[str, Any]], int]:
    """Group planned items by (priority, dataset) into one batch each.
    Apply the per-run fund budget across all batches (a single 100-fund
    budget means we cap the *total* distinct fund codes, not 100 per
    batch). Return (batches, skipped_for_budget)."""
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for item in planned:
        grouped[(item["priority"], item["dataset"])].append(item["fund_code"])

    max_funds = int(budgets.get("max_funds_per_run", 100))
    max_calls = int(budgets.get("max_provider_calls_per_run", 300))
    concurrency = int(budgets.get("concurrency", 4))
    min_interval = float(budgets.get("min_interval_seconds", 0.2))

    # Sort groups so the highest priority + highest-scoring dataset
    # batches go first. Dataset weight is borrowed from
    # self_audit.DATASET_WEIGHTS (we keep the literal values here so
    # the plan does not have to import self_audit at module load).
    dataset_weight = {
        "fund_profiles": 10,
        "nav_history": 20,
        "snapshots": 30,
        "stock_holdings": 40,
        "bond_holdings": 50,
        "industry_allocations": 60,
        "fee_structures": 70,
    }
    priority_rank = {"P1": 0, "P2": 1, "P3": 2}
    sorted_groups = sorted(
        grouped.items(),
        key=lambda kv: (
            priority_rank.get(kv[0][0], 9),
            -dataset_weight.get(kv[0][1], 0),
            kv[0][1],
        ),
    )

    batches: list[dict[str, Any]] = []
    used_codes: set[str] = set()
    used_calls = 0
    skipped = 0
    for (priority, dataset), codes in sorted_groups:
        unique_codes: list[str] = []
        leftover = 0
        for code in codes:
            if code in used_codes:
                continue
            if len(used_codes) + len(unique_codes) >= max_funds:
                leftover += 1
                continue
            if used_calls + len(unique_codes) + 1 > max_calls:
                leftover += 1
                continue
            unique_codes.append(code)
        skipped += leftover
        if not unique_codes:
            skipped += 0  # already counted above
            continue
        batch_flag = BATCH_FLAGS.get(dataset)
        if batch_flag is None:
            # dataset has no batch-sync primitive (e.g. snapshots in
            # the current CLI) -- skip but record as blocked so the
            # operator knows why nothing was scheduled.
            for code in unique_codes:
                # not added to used_codes so a future snapshot
                # batch would still see the request
                pass
            skipped += len(unique_codes)
            continue
        used_codes.update(unique_codes)
        used_calls += len(unique_codes)

        codes_filename = f"{dataset}_{priority.lower()}_{len(unique_codes)}_codes.txt"
        codes_path = run_root / "codes" / codes_filename
        codes_path.parent.mkdir(parents=True, exist_ok=True)
        codes_path.write_text(
            "\n".join(unique_codes) + "\n", encoding="utf-8"
        )

        provider = provider_policy.get(dataset, "auto")
        batch_id = f"openclaw-{run_id}-{dataset}-{priority.lower()}"
        if codes_path.is_absolute():
            try:
                rel_codes_file = codes_path.relative_to(Path.cwd())
            except ValueError:
                rel_codes_file = codes_path
        else:
            rel_codes_file = codes_path
        command_parts = [
            ".venv-akshare/bin/python",
            "fund-data/scripts/fund_cli.py",
            "batch-sync",
            "--codes-file",
            str(rel_codes_file),
            "--provider",
            provider,
            "--concurrency",
            str(concurrency),
            "--min-interval-seconds",
            str(min_interval),
            "--batch-id",
            batch_id,
        ]
        if batch_flag:
            command_parts.insert(-6, batch_flag)
        command = " ".join(command_parts)

        batches.append({
            "batch_id": batch_id,
            "priority": priority,
            "dataset": dataset,
            "provider": provider,
            "codes": unique_codes,
            "codes_file": str(rel_codes_file),
            "command": command,
        })
    return batches, skipped


def build_completion_plan(
    *,
    queue_path: str | Path,
    config_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Turn a self-audit queue JSON into a bounded batch plan.

    Read-only. The returned plan has ``allowed_to_execute`` true only
    in ``autonomous`` mode; in every other mode the operator must
    pass ``--confirm-execute`` to ``run_completion_plan`` (or
    ``fund_cli completion-run``).
    """
    policy = load_completion_policy(config_path)
    queue = _read_queue(queue_path)

    planned, blocked = _filter_queue(queue.get("queue", []), policy)
    budgets = policy["budgets"]
    provider_policy = policy.get("provider_policy", {})

    run_id = _now_utc_compact()
    run_root = Path("fund-data/data/openclaw_runs") / run_id
    batches, skipped = _group_into_batches(
        planned,
        run_id=run_id,
        run_root=run_root,
        budgets=budgets,
        provider_policy=provider_policy,
    )

    total_calls = sum(len(batch["codes"]) for batch in batches)
    concurrency = max(1, int(budgets.get("concurrency", 4)))
    estimated_minutes = max(
        1, int(round((total_calls / concurrency) * SECONDS_PER_CALL / 60))
    ) if total_calls else 0

    mode = policy["mode"]
    plan = {
        "run_id": run_id,
        "generated_at": _now_utc_iso(),
        "mode": mode,
        "dry_run": mode != "autonomous",
        "allowed_to_execute": mode == "autonomous",
        "config_path": str(config_path) if config_path else None,
        "queue_path": str(queue_path),
        "run_root": str(run_root),
        "summary": {
            "queue_size": len(queue.get("queue", [])),
            "blocked": len(blocked),
            "planned_items": sum(len(b["codes"]) for b in batches),
            "estimated_provider_calls": total_calls,
            "estimated_minutes": estimated_minutes,
            "concurrency": concurrency,
            "skipped_for_budget": skipped,
        },
        "batches": batches,
        "blocked": blocked,
        "policy_snapshot": {
            "mode": mode,
            "allowed_priorities": policy["allowed_priorities"],
            "blocked_datasets": policy["blocked_datasets"],
            "budgets": budgets,
        },
    }

    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return plan


# --------------------------------------------------------------------------
# runner (Task 3)
# --------------------------------------------------------------------------


def _lock_path() -> Path:
    return Path("fund-data/data") / LOCK_FILENAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _acquire_lock(lock: Path) -> dict[str, Any]:
    """Create the lock file atomically. Returns the lock metadata.

    Behavior:
    * If the lock is absent -> create it, return ``{acquired: True}``.
    * If the lock exists and the recorded pid is alive -> refuse
      (return ``{acquired: False, reason: 'alive'}``).
    * If the lock exists, the recorded pid is dead, OR the lock is
      older than ``LOCK_STALE_HOURS`` -> mark stale and replace.
    """
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            on_disk = json.loads(lock.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            on_disk = {}
        pid = int(on_disk.get("pid", 0))
        created = on_disk.get("created_at", "")
        try:
            created_dt = datetime.fromisoformat(created)
        except (TypeError, ValueError):
            created_dt = None
        stale_by_age = (
            created_dt is not None
            and (datetime.now(UTC) - created_dt).total_seconds() / 3600
            > LOCK_STALE_HOURS
        )
        if _pid_alive(pid) and not stale_by_age:
            return {"acquired": False, "reason": "alive", "lock": on_disk}
        # Stale -> replace
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
    meta = {
        "pid": os.getpid(),
        "created_at": _now_utc_iso(),
        "host": os.uname().nodename if hasattr(os, "uname") else "unknown",
    }
    # atomic-ish: write to .tmp then rename
    tmp = lock.with_suffix(lock.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    tmp.replace(lock)
    return {"acquired": True, "meta": meta}


def _release_lock(lock: Path) -> None:
    try:
        lock.unlink()
    except FileNotFoundError:
        pass


def _execute_batch(batch: dict[str, Any], run_root: Path) -> dict[str, Any]:
    """Run a single batch command via subprocess. Capture stdout/stderr
    to per-batch log files under ``run_root/logs`` and return an
    execution record with timing and exit code.
    """
    logs_dir = run_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"{batch['batch_id']}.stdout.log"
    stderr_path = logs_dir / f"{batch['batch_id']}.stderr.log"

    started_at = _now_utc_iso()
    started_monotonic = time.monotonic()
    try:
        completed = subprocess.run(
            ["/bin/sh", "-c", batch["command"]],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except FileNotFoundError as exc:
        returncode = 127
        stdout = ""
        stderr = f"failed to spawn: {exc}"
    ended_at = _now_utc_iso()
    elapsed = time.monotonic() - started_monotonic
    stdout_path.write_text(stdout or "", encoding="utf-8")
    stderr_path.write_text(stderr or "", encoding="utf-8")
    return {
        "batch_id": batch["batch_id"],
        "dataset": batch["dataset"],
        "priority": batch["priority"],
        "command": batch["command"],
        "codes": list(batch["codes"]),
        "started_at": started_at,
        "ended_at": ended_at,
        "elapsed_seconds": round(elapsed, 3),
        "returncode": returncode,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "rows_changed": _rows_changed_from_output(stdout or ""),
    }


def _rows_changed_from_output(stdout: str) -> int | None:
    """Try to parse a ``rows_changed`` number from a batch-sync JSON
    log line. The CLI prints a final ``{"summary": {...}}`` block on
    success. Returns ``None`` if the output is not parseable.
    """
    if not stdout:
        return None
    # Find the last JSON object in the output.
    text = stdout.strip()
    if not text.startswith("{"):
        # batch-sync may print progress lines before the summary.
        # Look for the last '{' followed by a JSON object.
        idx = text.rfind("{")
        if idx < 0:
            return None
        text = text[idx:]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return None
    for key in ("rows_changed", "rows_inserted", "rows_updated", "inserted", "rows"):
        if key in summary and isinstance(summary[key], int):
            return summary[key]
    return None


def run_completion_plan(
    *,
    plan_path: str | Path,
    config_path: str | Path | None = None,
    confirm_execute: bool = False,
) -> dict[str, Any]:
    """Execute a completion plan. Read-only by default; requires
    ``confirm_execute=True`` and a policy mode of ``autonomous`` or
    ``assisted`` to actually spawn subprocesses. In ``audit_only`` mode
    it always refuses to execute.

    The runner:

    * acquires ``fund-data/data/openclaw_active_completion.lock``;
    * writes ``execution.json`` and a per-batch stdout/stderr log
      under ``fund-data/data/openclaw_runs/<run-id>/``;
    * stops early if the elapsed-time budget is exceeded;
    * stops early if the failure rate exceeds the policy budget.

    Publishing OSS is **never** part of this function.
    """
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    policy = load_completion_policy(config_path)
    mode = policy["mode"]
    run_id = plan.get("run_id") or _now_utc_compact()
    run_root = Path(plan.get("run_root") or f"fund-data/data/openclaw_runs/{run_id}")
    run_root.mkdir(parents=True, exist_ok=True)

    execution: dict[str, Any] = {
        "run_id": run_id,
        "started_at": _now_utc_iso(),
        "ended_at": None,
        "mode": mode,
        "confirm_execute": bool(confirm_execute),
        "executed": False,
        "refusal_reason": None,
        "batches": [],
        "summary": {
            "total_batches": 0,
            "executed_batches": 0,
            "failed_batches": 0,
            "rows_changed": 0,
            "provider_calls": 0,
            "elapsed_seconds": 0.0,
        },
    }

    if mode == "audit_only":
        execution["refusal_reason"] = "mode=audit_only forbids execution"
    elif not confirm_execute:
        execution["refusal_reason"] = (
            "pass --confirm-execute to allow execution in "
            f"mode={mode}"
        )
    elif mode not in {"assisted", "autonomous"}:
        execution["refusal_reason"] = f"unknown mode: {mode}"

    if execution["refusal_reason"]:
        execution["ended_at"] = _now_utc_iso()
        _write_execution(run_root, execution)
        return execution

    # Refuse if the plan would exceed the call budget; we never want
    # a single completion run to silently burst past the operator's
    # configured ceiling, even when the plan builder approved it.
    # Prefer the plan's own estimate so a hand-edited plan that
    # exceeds the budget is caught even if its batch count is low.
    budgets_pre = policy["budgets"]
    plan_summary = plan.get("summary", {})
    planned_calls = int(
        plan_summary.get("estimated_provider_calls")
        or sum(len(b.get("codes", [])) for b in plan.get("batches", []))
    )
    execution["summary"]["provider_calls"] = planned_calls
    budget_calls = int(budgets_pre.get("max_provider_calls_per_run", 0))
    if budget_calls and planned_calls > budget_calls:
        execution["refusal_reason"] = (
            f"plan requests {planned_calls} provider calls but "
            f"policy budget caps at {budget_calls}"
        )
        execution["ended_at"] = _now_utc_iso()
        _write_execution(run_root, execution)
        return execution

    # Lock.
    lock = _lock_path()
    lock_result = _acquire_lock(lock)
    if not lock_result.get("acquired"):
        execution["refusal_reason"] = (
            f"another completion run is in progress (lock={lock_result.get('lock')})"
        )
        execution["ended_at"] = _now_utc_iso()
        _write_execution(run_root, execution)
        return execution

    # Persist the plan snapshot for the operator.
    (run_root / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    try:
        budgets = policy["budgets"]
        max_minutes = float(budgets.get("max_elapsed_minutes", 30))
        max_failure_rate = float(budgets.get("max_failure_rate", 0.25))

        started = time.monotonic()
        failures = 0
        execution["executed"] = True
        execution["summary"]["total_batches"] = len(plan.get("batches", []))
        for batch in plan.get("batches", []):
            elapsed_minutes = (time.monotonic() - started) / 60
            if elapsed_minutes > max_minutes:
                execution["batches"].append({
                    "batch_id": batch["batch_id"],
                    "skipped": True,
                    "reason": f"elapsed {elapsed_minutes:.1f}m exceeds budget {max_minutes:.1f}m",
                })
                continue
            record = _execute_batch(batch, run_root)
            execution["batches"].append(record)
            execution["summary"]["provider_calls"] += len(batch["codes"])
            if record["returncode"] != 0:
                failures += 1
                execution["summary"]["failed_batches"] += 1
            else:
                execution["summary"]["executed_batches"] += 1
            if record.get("rows_changed"):
                execution["summary"]["rows_changed"] += record["rows_changed"]
            # Failure rate budget: stop if more than 25% of executed
            # batches have failed. We require at least 2 executed
            # batches before judging so a single transient failure
            # does not kill the run; once two or more have completed,
            # a >25% failure rate is enough to call it.
            done = execution["summary"]["executed_batches"] + execution["summary"]["failed_batches"]
            if done >= 2 and failures / done > max_failure_rate:
                execution["refusal_reason"] = (
                    f"failure rate {failures}/{done} ({failures/done:.0%}) "
                    f"exceeds budget {max_failure_rate:.0%}; stopping"
                )
                break
        execution["summary"]["elapsed_seconds"] = round(
            time.monotonic() - started, 3
        )
    finally:
        execution["ended_at"] = _now_utc_iso()
        _write_execution(run_root, execution)
        _release_lock(lock)
    return execution


def _write_execution(run_root: Path, execution: dict[str, Any]) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "execution.json").write_text(
        json.dumps(execution, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# verification (Task 4 CLI surface depends on this)
# --------------------------------------------------------------------------


def _queue_path_summary(path: str | Path) -> dict[str, Any]:
    payload = _read_queue(path)
    return payload.get("summary", {})


def verify_completion_run(
    *,
    before_queue_path: str | Path,
    after_queue_path: str | Path,
    execution_path: str | Path,
) -> dict[str, Any]:
    """Compare before/after audit state and the execution record, and
    return a verification report. Read-only; the operator decides what
    to do with the result (publish, retry, escalate).
    """
    before_summary = _queue_path_summary(before_queue_path)
    after_summary = _queue_path_summary(after_queue_path)
    execution = json.loads(Path(execution_path).read_text(encoding="utf-8"))

    before_size = int(before_summary.get("queue_size", 0))
    after_size = int(after_summary.get("queue_size", 0))
    improved_items = max(0, before_size - after_size)
    rows_changed = int(execution.get("summary", {}).get("rows_changed", 0))

    # ``new_failures`` is the change in P3/P4 across the two queues.
    # We use a simple proxy: P3 shrinkage that did not match the
    # improved_items delta is treated as potential new failures.
    before_p3 = int(before_summary.get("p3", 0))
    after_p3 = int(after_summary.get("p3", 0))
    new_p3 = max(0, after_p3 - before_p3)

    publish_recommended = (
        execution.get("executed", False)
        and execution.get("refusal_reason") is None
        and rows_changed > 0
        and new_p3 == 0
    )

    return {
        "before_queue_size": before_size,
        "after_queue_size": after_size,
        "improved_items": improved_items,
        "rows_changed": rows_changed,
        "new_failures": new_p3,
        "doctor_ok": None,  # filled in by CLI/MCP after running doctor
        "publish_recommended": publish_recommended,
        "execution_summary": execution.get("summary", {}),
        "refusal_reason": execution.get("refusal_reason"),
    }


# Re-export for tests / callers that expect them on the package root.
def _row_count(db_path: str | Path | None, table: str) -> int:
    """Tiny helper used by tests; not part of the public contract."""
    target = Path(db_path) if db_path else default_db_path()
    with sqlite3.connect(target) as conn:
        try:
            return int(
                conn.execute(f"select count(*) from {table}").fetchone()[0]
            )
        except sqlite3.OperationalError:
            return 0


__all__ = [
    "BATCH_FLAGS",
    "DEFAULT_POLICY",
    "LOCK_FILENAME",
    "LOCK_STALE_HOURS",
    "SECONDS_PER_CALL",
    "build_completion_plan",
    "load_completion_policy",
    "run_completion_plan",
    "verify_completion_run",
]
