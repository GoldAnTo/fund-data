"""Install, uninstall, or check the fund-data skill on a target agent.

Targets:
  claude    -> ~/.claude/skills/fund-data
  codex     -> ~/.codex/skills/fund-data
  openclaw  -> ~/.openclaw/skills/fund-data
  agents    -> ~/.agents/skills/fund-data  (OpenClaw personal-scope)
  all       -> every target above

By default the installer creates a symlink so local code changes show
up immediately for the agent. Use --copy to mirror the directory
instead (matches the manual workflow described in SKILL.md).
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path

SKILL_NAME = "fund-data"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Resolve the actual skill directory (where SKILL.md lives) and the
# project root (the repo). Some agents expect the skill at the top
# of the linked directory; Codex already copies the inner ``fund-data``
# folder, so we mirror that layout.
SKILL_DIR_FOR_TARGETS = PROJECT_ROOT  # the inner fund-data/ folder
SKILL_MARKER = SKILL_DIR_FOR_TARGETS / "SKILL.md"

# When the destination does not exist, the default is symlink. When it
# already exists and is a symlink to our path, refresh it. When it
# exists and is a real directory, we either copy into it (--copy) or
# refuse (default) to avoid clobbering someone else's work.


def _target_paths() -> dict[str, Path]:
    home = Path.home()
    return {
        "claude": home / ".claude" / "skills" / SKILL_NAME,
        "codex": home / ".codex" / "skills" / SKILL_NAME,
        "openclaw": home / ".openclaw" / "skills" / SKILL_NAME,
        "agents": home / ".agents" / "skills" / SKILL_NAME,
    }


def _validate_source() -> None:
    if not SKILL_MARKER.is_file():
        raise SystemExit(
            f"fatal: SKILL.md not found at {SKILL_MARKER}. "
            f"Run this script from the project layout (fund-data/scripts/install_skill.py)."
        )


def _install_one(
    target: str,
    dest: Path,
    *,
    copy: bool,
    data_mode: str,
    scrub_raw: bool = False,
) -> str:
    if not dest.parent.exists():
        return f"  [{target}] SKIP — {dest.parent} does not exist (agent not installed?)"

    # Already pointing here: nothing to do.
    if not copy and dest.is_symlink() and dest.resolve() == SKILL_DIR_FOR_TARGETS.resolve():
        return f"  [{target}] OK   — already linked to {SKILL_DIR_FOR_TARGETS}"

    # Real directory or broken symlink at the destination: be safe.
    if dest.exists() or dest.is_symlink():
        if copy and dest.is_symlink():
            dest.unlink()
            _copy_into(SKILL_DIR_FOR_TARGETS, dest, data_mode=data_mode, scrub_raw=scrub_raw)
            return (
                f"  [{target}] OK   — replaced symlink with copy "
                f"{SKILL_DIR_FOR_TARGETS} -> {dest} (data={data_mode})"
            )
        if copy:
            # Merge contents (overwrite files, recurse into directories, keep
            # the dest directory itself so any agent-side state survives).
            for child in SKILL_DIR_FOR_TARGETS.iterdir():
                _copy_into(child, dest / child.name, data_mode=data_mode, scrub_raw=scrub_raw)
            return (
                f"  [{target}] OK   — merged {SKILL_DIR_FOR_TARGETS} -> {dest} (data={data_mode})"
            )
        return (
            f"  [{target}] EXISTS — {dest} already exists. "
            f"Use --copy to refresh its contents, or remove it and rerun."
        )

    # Fresh install.
    dest.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        _copy_into(SKILL_DIR_FOR_TARGETS, dest, data_mode=data_mode, scrub_raw=scrub_raw)
        return f"  [{target}] OK   — copied {SKILL_DIR_FOR_TARGETS} -> {dest} (data={data_mode})"
    os.symlink(SKILL_DIR_FOR_TARGETS, dest)
    return f"  [{target}] OK   — symlinked {SKILL_DIR_FOR_TARGETS} -> {dest}"


def _copy_into(
    src: Path, dst: Path, *, data_mode: str = "none", scrub_raw: bool = False
) -> None:
    """Copy ``src`` to ``dst``, recursing into directories and overwriting
    files. Preserves any extra files in ``dst`` that are not in ``src``.

    ``scrub_raw`` is threaded through to the SQLite snapshot writer
    so a single call covers the whole tree.
    """
    if data_mode not in {"none", "copy"}:
        raise ValueError(f"unknown data_mode: {data_mode}")
    if _should_skip_install_path(src, data_mode=data_mode):
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        elif dst.is_dir():
            shutil.rmtree(dst)
        return
    if dst.is_symlink():
        dst.unlink()
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_into(child, dst / child.name, data_mode=data_mode, scrub_raw=scrub_raw)
        for child in list(dst.iterdir()):
            if _should_skip_install_path(child, data_mode=data_mode):
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    else:
        if dst.is_dir():
            shutil.rmtree(dst)
        if src.name == "fund_data.sqlite" and data_mode == "copy":
            _copy_sqlite_database(src, dst, scrub_raw=scrub_raw)
        else:
            shutil.copy2(src, dst)


def _copy_sqlite_database(src: Path, dst: Path, *, scrub_raw: bool = False) -> None:
    """Write a consistent SQLite snapshot, including any live WAL pages.

    With ``scrub_raw=True``, the freshly-written destination has its
    ``raw_responses`` table emptied after the backup. That table
    stores full upstream HTTP bodies (including the caller's IP in
    some headers), so a snapshot published to a public skill install
    leaks the operator's network identity unless the user opts in to
    the scrub. The flag is opt-in because dropping the table is
    destructive on the destination and the IP-leak only matters
    when the snapshot leaves the local machine.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    source_uri = f"file:{src}?mode=ro"
    with (
        sqlite3.connect(source_uri, uri=True, timeout=30.0) as source,
        sqlite3.connect(dst, timeout=30.0) as target,
    ):
        source.backup(target)
    if scrub_raw:
        with sqlite3.connect(dst, timeout=30.0) as target:
            target.execute("DELETE FROM raw_responses")
            target.commit()


def _should_skip_install_path(path: Path, *, data_mode: str) -> bool:
    """Return true for local runtime artifacts that do not belong in a skill install."""
    if data_mode not in {"none", "copy"}:
        raise ValueError(f"unknown data_mode: {data_mode}")
    artifact_names = {
        ".DS_Store",
        ".ruff_cache",
        ".pytest_cache",
        "__pycache__",
        "backfill_logs",
        "raw_responses",
        "sync_state",
        "backfill_state.json",
        "backfill_summary.json",
    }
    if any(part in artifact_names for part in path.parts):
        return True
    name = path.name
    return (
        name.endswith(".pyc")
        or name.endswith(".pyo")
        or (name.endswith(".sqlite") and not (data_mode == "copy" and name == "fund_data.sqlite"))
        or name.endswith(".sqlite-journal")
        or name.endswith(".sqlite-wal")
        or name.endswith(".sqlite-shm")
    )


def _uninstall_one(target: str, dest: Path) -> str:
    if not dest.exists() and not dest.is_symlink():
        return f"  [{target}] SKIP — {dest} does not exist"
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
        return f"  [{target}] OK   — removed symlink {dest}"
    if "--copy" in sys.argv:
        shutil.rmtree(dest)
        return f"  [{target}] OK   — removed directory {dest}"
    # refuse to remove real directories unless --force is passed
    return f"  [{target}] REFUSE — {dest} is a real directory; pass --force to remove"


def _status_one(target: str, dest: Path) -> str:
    if not dest.exists() and not dest.is_symlink():
        return f"  [{target}] MISSING — {dest}"
    if dest.is_symlink():
        target_path = dest.resolve()
        if target_path == SKILL_DIR_FOR_TARGETS.resolve():
            return f"  [{target}] LINKED  — {dest} -> {target_path}"
        return f"  [{target}] STALE   — {dest} -> {target_path} (expected {SKILL_DIR_FOR_TARGETS})"
    if (dest / "SKILL.md").is_file():
        return f"  [{target}] INSTALLED — {dest} (real directory)"
    return f"  [{target}] BROKEN  — {dest} exists but has no SKILL.md"


def _resolve_targets(name: str) -> Iterable[tuple[str, Path]]:
    paths = _target_paths()
    if name == "all":
        return list(paths.items())
    if name not in paths:
        raise SystemExit(f"unknown target: {name}. Choose from {sorted(paths)} or 'all'.")
    return [(name, paths[name])]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["install", "uninstall", "status"])
    parser.add_argument(
        "--target",
        default="all",
        help="Which agent to act on: claude, codex, openclaw, agents, all (default).",
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        help="Copy files instead of symlinking. Use this if your agent does not follow symlinks.",
    )
    parser.add_argument(
        "--data-mode",
        choices=["none", "copy"],
        default="none",
        help=(
            "Data handling for copy installs: none excludes SQLite data (default); "
            "copy includes a consistent data/fund_data.sqlite snapshot."
        ),
    )
    parser.add_argument(
        "--include-data",
        action="store_true",
        help="Shorthand for --copy --data-mode copy.",
    )
    parser.add_argument(
        "--scrub-raw-responses",
        action="store_true",
        help=(
            "When --data-mode copy is in effect, drop the raw_responses table "
            "from the copied SQLite snapshot. The table stores full upstream "
            "HTTP bodies (including the caller's IP in some headers), so a "
            "snapshot published to a public skill install leaks the "
            "operator's network identity unless the user opts in. Default "
            "is off — pass this flag to explicitly request the scrub."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="When uninstalling, allow removing a real directory.",
    )
    args = parser.parse_args(argv)

    _validate_source()
    if args.include_data:
        args.copy = True
        args.data_mode = "copy"
    if args.data_mode == "copy":
        args.copy = True
    if args.action == "install" and args.data_mode == "copy":
        # SECURITY.md #46-49 already documents the IP-in-raw_responses
        # risk. Mirror it on the CLI so the warning is impossible to
        # miss when a user runs --include-data without knowing.
        if not args.scrub_raw_responses:
            print(
                "::warning::The SQLite snapshot includes the "
                "raw_responses table, which stores full upstream "
                "HTTP bodies. If this snapshot will leave your "
                "machine (commit to a public repo, attach to a "
                "release, etc.) pass --scrub-raw-responses to drop "
                "that table before publishing."
            )

    paths = _resolve_targets(args.target)
    print(f"fund-data {args.action} -> {args.target}")
    print(f"  source: {SKILL_DIR_FOR_TARGETS}")
    for name, dest in paths:
        if args.action == "install":
            print(
                _install_one(
                    name,
                    dest,
                    copy=args.copy,
                    data_mode=args.data_mode,
                    scrub_raw=args.scrub_raw_responses,
                )
            )
        elif args.action == "uninstall":
            line = _uninstall_one(name, dest)
            if args.force and "REFUSE" in line:
                if dest.is_dir():
                    shutil.rmtree(dest)
                    print(f"  [{name}] OK   — force-removed {dest}")
                else:
                    dest.unlink(missing_ok=True)
                    print(f"  [{name}] OK   — force-removed {dest}")
            else:
                print(line)
        else:
            print(_status_one(name, dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
