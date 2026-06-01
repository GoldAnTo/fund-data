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


def _install_one(target: str, dest: Path, *, copy: bool) -> str:
    if not dest.parent.exists():
        return f"  [{target}] SKIP — {dest.parent} does not exist (agent not installed?)"

    # Already pointing here: nothing to do.
    if dest.is_symlink() and dest.resolve() == SKILL_DIR_FOR_TARGETS.resolve():
        return f"  [{target}] OK   — already linked to {SKILL_DIR_FOR_TARGETS}"

    # Real directory or broken symlink at the destination: be safe.
    if dest.exists() or dest.is_symlink():
        if copy:
            # Merge contents (overwrite files, recurse into directories, keep
            # the dest directory itself so any agent-side state survives).
            for child in SKILL_DIR_FOR_TARGETS.iterdir():
                _copy_into(child, dest / child.name)
            return f"  [{target}] OK   — merged {SKILL_DIR_FOR_TARGETS} -> {dest}"
        return (
            f"  [{target}] EXISTS — {dest} already exists. "
            f"Use --copy to refresh its contents, or remove it and rerun."
        )

    # Fresh install.
    dest.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copytree(SKILL_DIR_FOR_TARGETS, dest)
        return f"  [{target}] OK   — copied {SKILL_DIR_FOR_TARGETS} -> {dest}"
    os.symlink(SKILL_DIR_FOR_TARGETS, dest)
    return f"  [{target}] OK   — symlinked {SKILL_DIR_FOR_TARGETS} -> {dest}"


def _copy_into(src: Path, dst: Path) -> None:
    """Copy ``src`` to ``dst``, recursing into directories and overwriting
    files. Preserves any extra files in ``dst`` that are not in ``src``."""
    if _should_skip_install_path(src):
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
            _copy_into(child, dst / child.name)
        for child in list(dst.iterdir()):
            if _should_skip_install_path(child):
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
    else:
        if dst.is_dir():
            shutil.rmtree(dst)
        shutil.copy2(src, dst)


def _should_skip_install_path(path: Path) -> bool:
    """Return true for local runtime artifacts that do not belong in a skill install."""
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
        or name.endswith(".sqlite")
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
        "--force",
        action="store_true",
        help="When uninstalling, allow removing a real directory.",
    )
    args = parser.parse_args(argv)

    _validate_source()

    paths = _resolve_targets(args.target)
    print(f"fund-data {args.action} -> {args.target}")
    print(f"  source: {SKILL_DIR_FOR_TARGETS}")
    for name, dest in paths:
        if args.action == "install":
            print(_install_one(name, dest, copy=args.copy))
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
