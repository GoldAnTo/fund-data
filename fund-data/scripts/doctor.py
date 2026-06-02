"""Environment health check for the fund-data skill.

Run this on a fresh checkout or before kicking off a long
backfill. It surfaces the most common "why is this thing broken"
problems so the operator can fix the before the first batch fails:

- Is the SQLite database reachable? Is the schema intact?
- Is the AkShare virtual environment present and importable?
- Is the Eastmoney client reachable from this network?
- Are the provider scripts callable from the current Python?
- Are there stale sync failures in the queue?
- What is the current coverage rate per dataset?
- **Where will agents actually write?**
  ``default_db`` calls ``fund_data.default_db_path()`` (the same
  resolver an agent uses) and tags the result as
  ``cloud_cache`` / ``full_local`` / ``env_override`` /
  ``unknown``. Without this, ``--db`` defaulted to the on-disk
  full DB while agents and the cloud bootstrap pointed at a
  different file — see ``fund-data/AGENTS.md`` §"Long-running
  pitfalls".
- **Is the cloud bundle stale?**
  ``cloud_cache`` reads ``fund_cloud.status()`` and surfaces
  ``update_available``. Doctor never triggers a pull; the
  caller decides whether to refresh.

Exits non-zero if any check fails, so it can gate CI or a backfill.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

# Apply the macOS proxy / IPv4 / sqlite-timeout patches BEFORE
# importing :mod:`fund_data` (or any module that might open a
# connection).  Idempotent so a re-import is cheap.
from _net_compat import apply as _apply_net_compat  # noqa: E402

_apply_net_compat()

import fund_data  # noqa: E402
import fund_cloud  # noqa: E402

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
DEFAULT_VENV = SCRIPT_DIR.parent.parent / ".venv-akshare"
DEFAULT_BACKFILL_STATE = SCRIPT_DIR.parent / "data" / "backfill_state.json"
DEFAULT_STALE_HOURS = 24.0

REQUIRED_TABLES = (
    "funds",
    "nav_history",
    "snapshots",
    "stock_holdings",
    "fund_profiles",
    "bond_holdings",
    "industry_allocations",
    "fee_structures",
    "dividends",
    "splits",
    "fund_managers",
    "raw_responses",
    "sync_runs",
    "sync_failures",
)


def _check_python() -> dict[str, object]:
    return {"ok": True, "version": sys.version.split()[0]}


def _check_db(db_path: Path) -> dict[str, object]:
    if not db_path.is_file():
        return {"ok": False, "message": f"database not found at {db_path}"}
    try:
        with sqlite3.connect(db_path) as conn:
            existing = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            missing = [t for t in REQUIRED_TABLES if t not in existing]
        if missing:
            return {"ok": False, "message": f"missing tables: {missing}"}
        return {"ok": True, "path": str(db_path)}
    except sqlite3.Error as exc:
        return {"ok": False, "message": f"sqlite error: {exc}"}


def _check_akshare(venv: Path) -> dict[str, object]:
    """Try three ways to find an AkShare install:
    1. The running Python's import (most common case for ad-hoc checks).
    2. The configured venv (default .venv-akshare in the repo).
    3. The FUND_DATA_AKSHARE_PYTHON env var (any python with akshare).
    Reports where the install is so the operator can fix the right thing.
    """
    if os.environ.get("FUND_DATA_DISABLE_AKSHARE") == "1":
        return {"ok": True, "message": "akshare disabled by FUND_DATA_DISABLE_AKSHARE=1"}

    # 1. Current Python.
    try:
        import akshare  # type: ignore  # noqa: F401

        return {"ok": True, "version": akshare.__version__, "source": "current python"}
    except ImportError:
        pass

    # 2. Configured venv (default .venv-akshare).
    py = venv / "bin" / "python"
    candidates: list[tuple[str, Path]] = [("venv", py)]
    override = os.environ.get("FUND_DATA_AKSHARE_PYTHON")
    if override:
        candidates.append(("FUND_DATA_AKSHARE_PYTHON", Path(override)))

    for label, candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            version = (
                subprocess.check_output(
                    [str(candidate), "-c", "import akshare; print(akshare.__version__)"],
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
                .decode()
                .strip()
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            return {
                "ok": False,
                "message": f"akshare not importable in {label} python ({candidate}): {exc}",
                "hint": f"run `{candidate} -m pip install akshare`",
            }
        return {
            "ok": True,
            "version": version,
            "source": f"{label} python ({candidate})",
            "hint": "run this script with the same python to use AkShare",
        }

    return {
        "ok": False,
        "message": "akshare is not installed in the current python or the configured venv",
        "hint": (
            f"install: `python3 -m venv {venv}` then "
            f"`{venv}/bin/python -m pip install akshare`. "
            f"Or set FUND_DATA_AKSHARE_PYTHON=/path/to/python-with-akshare."
        ),
        "venv": str(venv),
    }


def _check_eastmoney() -> dict[str, object]:
    try:
        with urllib.request.urlopen(
            "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx?m=1&key=%E6%98%93%E6%96%B9%E8%BE%BE",
            timeout=10,
        ) as response:
            return {"ok": response.status == 200, "status": response.status}
    except Exception as exc:
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


def _check_providers() -> dict[str, object]:
    """Make sure each provider can at least be constructed."""
    result: dict[str, object] = {}
    for name, builder in (("eastmoney", lambda: fund_data.EastmoneyProvider()),):
        try:
            builder()
            result[name] = {"ok": True}
        except Exception as exc:
            result[name] = {"ok": False, "message": str(exc)}
    # AkShare requires a separate venv; we report it as best-effort.
    try:
        fund_data.AkshareProvider()
        result["akshare"] = {"ok": True}
    except fund_data.ProviderError as exc:
        result["akshare"] = {"ok": False, "message": str(exc), "degraded_ok": True}
    # Investoday is opt-in via API key.
    if os.environ.get("INVESTDATA_API_KEY"):
        try:
            fund_data.InvestodayProvider()
            result["investoday"] = {"ok": True}
        except Exception as exc:
            result["investoday"] = {"ok": False, "message": str(exc)}
    else:
        result["investoday"] = {"ok": True, "skipped": "INVESTDATA_API_KEY not set"}
    return result


def _check_sync_failures(db_path: Path) -> dict[str, object]:
    if not db_path.is_file():
        return {"ok": True, "count": 0, "skipped": "db missing"}
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM sync_failures").fetchone()
    count = int(row[0]) if row else 0
    return {
        "ok": count == 0,
        "count": count,
        "hint": "rerun `fund_cli.py batch-sync` on the failed codes" if count else None,
    }


def _check_coverage(db_path: Path) -> dict[str, object]:
    if not db_path.is_file():
        return {"ok": True, "skipped": "db missing"}
    if "funds" not in {
        r[0]
        for r in sqlite3.connect(db_path)
        .execute("SELECT name FROM sqlite_master WHERE type='table'")
        .fetchall()
    }:
        return {"ok": True, "skipped": "no funds table"}
    report = fund_data.coverage_report(db_path=db_path, only_incomplete=True, limit=5)
    total = sqlite3.connect(db_path).execute("SELECT COUNT(*) FROM funds").fetchone()[0]
    return {
        "ok": True,
        "total_funds": total,
        "incomplete_examples": len(report),
        "min_completeness": min((r["completeness"] for r in report), default=None),
    }


def _check_backfill_stale(
    state_path: Path,
    db_path: Path,
    *,
    stale_hours: float = DEFAULT_STALE_HOURS,
) -> dict[str, object]:
    """A backfill run is "stale" when the state file says the run is in
    progress but ``updated_at`` is older than ``stale_hours``. This usually
    means the process died, the runner was recycled, or a cron job was
    disabled — the operator should re-launch or accept and delete the
    state file.

    A run is also reported as stale when ``last_batch_id`` is missing
    even though ``started_at`` is older than the threshold (the run
    never produced a single batch before going silent).
    """
    if not state_path.is_file():
        return {"ok": True, "skipped": "no backfill state file"}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "message": f"could not read {state_path}: {exc}"}

    started_at = state.get("started_at")
    updated_at = state.get("updated_at")
    last_batch_id = state.get("last_batch_id")
    completed = state.get("completed_codes") or []
    failed = state.get("failed_codes") or []
    totals = state.get("totals") or {}

    if not updated_at:
        return {
            "ok": False,
            "message": "backfill state file has no updated_at; cannot determine staleness",
            "started_at": started_at,
            "last_batch_id": last_batch_id,
        }

    try:
        updated_dt = datetime.fromisoformat(updated_at)
    except ValueError:
        return {"ok": False, "message": f"updated_at is not ISO-8601: {updated_at!r}"}

    age = datetime.now(UTC) - updated_dt
    total_funds = 0
    if db_path.is_file():
        try:
            with sqlite3.connect(db_path) as conn:
                total_funds = int(conn.execute("SELECT COUNT(*) FROM funds").fetchone()[0])
        except sqlite3.OperationalError:
            pass
    finished = total_funds and len(completed) >= total_funds
    is_stale = age > timedelta(hours=stale_hours) and not finished

    result: dict[str, object] = {
        "ok": not is_stale,
        "started_at": started_at,
        "updated_at": updated_at,
        "age_hours": round(age.total_seconds() / 3600, 2),
        "stale_threshold_hours": stale_hours,
        "completed": len(completed),
        "failed": len(failed),
        "totals": totals,
        "total_funds": total_funds,
        "last_batch_id": last_batch_id,
    }
    if is_stale:
        result["hint"] = (
            "the backfill state has not progressed in "
            f"{age.total_seconds() / 3600:.1f}h; re-launch `scripts/backfill.py` "
            "(resumable) or delete `data/backfill_state.json` if you want "
            "to start over"
        )
    return result


def _classify_default_db_source(resolved: Path) -> str:
    """Tag a resolved default DB path by which layer of fund_data.default_db_path() it came from.

    Layers (see fund_data.default_db_path docstring):
      1. ``FUND_DATA_DB`` env var            -> "env_override"
      2. ``fund_cloud.ensure_project_bundle``-> "cloud_cache" (newly pulled)
      3. ``fund_cloud.current_db_path``      -> "cloud_cache" (reused)
      4. ``DEFAULT_DB_PATH`` fallback        -> "full_local"

    Anything else is "unknown" — that should not happen in
    practice, but the field is explicit so an agent can
    branch on it without guessing.
    """
    configured = os.environ.get("FUND_DATA_DB", "").strip()
    if configured:
        try:
            if Path(configured).resolve() == resolved.resolve():
                return "env_override"
        except OSError:
            pass
    try:
        cache_dir = fund_cloud.default_cache_dir()
        if cache_dir and resolved.is_relative_to(cache_dir):
            return "cloud_cache"
    except (ValueError, OSError):
        pass
    try:
        if resolved.resolve() == DEFAULT_DB_PATH.resolve():
            return "full_local"
    except OSError:
        pass
    return "unknown"


def _check_default_db() -> dict[str, object]:
    """Resolve the DB an agent will actually open via fund_data.default_db_path().

    The previous doctor default (DEFAULT_DB_PATH) silently disagreed
    with the resolver agents and the cloud bootstrap use, which is
    why ``sync_failures`` could be 8 in the on-disk full DB and 0 in
    the cloud query DB. This check is the single source of truth for
    the question "where will writes land?".

    ``ok`` is True as long as the resolver returned a path;
    `exists=False` is reported but not treated as a hard failure
    — first run / fresh checkout legitimately has no DB yet.
    """
    try:
        resolved = fund_data.default_db_path()
    except Exception as exc:  # network/import errors during cloud bootstrap
        return {
            "ok": False,
            "message": f"default_db_path raised {type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "path": str(resolved),
        "exists": resolved.is_file(),
        "source": _classify_default_db_source(resolved),
    }


def _check_cloud_cache(*, manifest_url: str | None = None) -> dict[str, object]:
    """Read fund_cloud.status() and surface a stale-cache warning.

    The cloud status subcommand already reports ``update_available``;
    this check pipes that signal into doctor so the operator or
    agent does not have to run a second command. doctor does NOT
    trigger a pull — that decision belongs to the caller.

    ``ok`` stays True even when ``update_available`` is true:
    staleness is a warning, not a hard failure. Agents that want
    to fail on staleness should branch on ``update_available``
    in the payload rather than on the exit code.

    Pass ``manifest_url=None`` to skip the remote HEAD probe (CI
    friendly). When non-None, ``fund_cloud.status`` does a
    ``HEAD``/``GET`` on the manifest URL — the caller decides
    whether network is allowed.
    """
    try:
        info = fund_cloud.status(manifest_url=manifest_url)
    except Exception as exc:
        return {
            "ok": False,
            "message": f"fund_cloud.status raised {type(exc).__name__}: {exc}",
        }
    out: dict[str, object] = {
        "ok": True,
        "installed": bool(info.get("installed")),
        "version": info.get("version"),
        "manifest_url": info.get("manifest_url") or manifest_url,
        "remote_version": info.get("remote_version"),
        "update_available": bool(info.get("update_available", False)),
    }
    if not out["installed"]:
        out["skipped"] = "no cloud cache installed"
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--db",
        default=None,
        help=(
            "SQLite path. Default: whatever fund_data.default_db_path() "
            "resolves to (i.e. the same DB agents will open when they "
            "call the skill). Pass --db to override for one run."
        ),
    )
    parser.add_argument("--venv", default=str(DEFAULT_VENV), help="AkShare venv path")
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="Skip the live Eastmoney reachability probe (useful in CI).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Emit compact JSON (no indent) and skip the human-readable "
        "FAIL banner on stderr. The exit code still mirrors the overall "
        "ok flag, so this is the agent-friendly mode.",
    )
    parser.add_argument(
        "--output",
        help="Write the JSON report to this file instead of stdout. "
        "Parent directories are created on demand.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.db is None:
        # Resolve the same way the agent does, so doctor's
        # sync_failures / coverage / backfill_stale numbers match
        # what the agent actually sees. --db overrides stay as-is
        # for the ad-hoc "poke this one db" workflow.
        try:
            args.db = str(fund_data.default_db_path())
        except Exception:
            # If the resolver itself fails (e.g. cloud bootstrap
            # network error), fall back to the on-disk file so the
            # rest of the report is still useful. The
            # default_db check surfaces the resolution failure.
            args.db = str(DEFAULT_DB_PATH)
    db_path = Path(args.db)
    venv = Path(args.venv)

    checks: dict[str, object] = {
        "python": _check_python(),
        "database": _check_db(db_path),
        "akshare": _check_akshare(venv),
        "providers": _check_providers(),
    }
    if not args.skip_network:
        checks["eastmoney_reachable"] = _check_eastmoney()
    checks["sync_failures"] = _check_sync_failures(db_path)
    checks["coverage"] = _check_coverage(db_path)
    checks["backfill_stale"] = _check_backfill_stale(DEFAULT_BACKFILL_STATE, db_path)
    checks["default_db"] = _check_default_db()
    if args.skip_network:
        # fund_cloud.status() with manifest_url=None skips the
        # remote probe and reports only the local cache metadata.
        cloud_manifest_url: str | None = None
    else:
        # Default: check the project OSS manifest for staleness
        # so doctor surfaces a warning the moment a newer bundle
        # is published. FUND_DATA_MANIFEST_URL still wins.
        cloud_manifest_url = (
            os.environ.get("FUND_DATA_MANIFEST_URL")
            or fund_cloud.default_manifest_url()
        )
    checks["cloud_cache"] = _check_cloud_cache(manifest_url=cloud_manifest_url)

    payload = json.dumps(checks, ensure_ascii=False, indent=None if args.quiet else 2)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)

    def _is_ok(value: object) -> bool:
        if isinstance(value, dict):
            return bool(value.get("ok", True))
        return True

    overall_ok = all(_is_ok(v) for v in checks.values())
    if not overall_ok and not args.quiet:
        print("\nFAIL: one or more checks reported a problem.", file=sys.stderr)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
