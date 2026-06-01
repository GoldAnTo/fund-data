"""Environment health check for the fund-data skill.

Run this on a fresh checkout or before kicking off a long
backfill. It surfaces the most common "why is this thing broken"
problems so the operator can fix them before the first batch fails:

- Is the SQLite database reachable? Is the schema intact?
- Is the AkShare virtual environment present and importable?
- Is the Eastmoney client reachable from this network?
- Are the provider scripts callable from the current Python?
- Are there stale sync failures in the queue?
- What is the current coverage rate per dataset?

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
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import fund_data  # noqa: E402

DEFAULT_DB_PATH = SCRIPT_DIR.parent / "data" / "fund_data.sqlite"
DEFAULT_VENV = SCRIPT_DIR.parent.parent / ".venv-akshare"

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
            existing = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
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
            version = subprocess.check_output(
                [str(candidate), "-c", "import akshare; print(akshare.__version__)"],
                stderr=subprocess.DEVNULL,
                timeout=15,
            ).decode().strip()
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
    for name, builder in (
        ("eastmoney", lambda: fund_data.EastmoneyProvider()),
    ):
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
        r[0] for r in sqlite3.connect(db_path).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite path")
    parser.add_argument("--venv", default=str(DEFAULT_VENV), help="AkShare venv path")
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="Skip the live Eastmoney reachability probe (useful in CI).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
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

    print(json.dumps(checks, ensure_ascii=False, indent=2))

    def _is_ok(value: object) -> bool:
        if isinstance(value, dict):
            return bool(value.get("ok", True))
        return True

    overall_ok = all(_is_ok(v) for v in checks.values())
    if not overall_ok:
        print("\nFAIL: one or more checks reported a problem.", file=sys.stderr)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
