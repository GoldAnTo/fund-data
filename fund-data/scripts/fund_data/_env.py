"""Lightweight stdlib ``.env`` loader for fund-data entry points.

The project ships with ``requirements.txt`` declaring
``Standard library only for the core data flow``. Adding
``python-dotenv`` as a hard dep would force an install on
every consumer for what is functionally a 30-line parser,
so this module implements the subset we actually need:

- ``KEY=VALUE`` pairs, one per line.
- Optional ``export `` prefix (handy when the same file is
  sourced from a shell and consumed by Python).
- Optional matching single / double quotes around the value.
- ``#`` line comments and blank lines are skipped.
- Already-set env vars win (so ``export KEY=...`` in a
  shell, or a CI secret, always takes precedence over the
  ``.env`` fallback). ``os.environ.setdefault`` semantics.
- Missing file is a no-op, not an error. CI / containers /
  fresh clones may not have a ``.env`` and that is fine;
  the loader just reports an empty diff.

Search order (first existing file wins):

  1. ``$FUND_DATA_ENV_FILE`` — explicit override for tests
     and for environments where the secrets live outside the
     repo (a shared ``.env`` mount, a CI workspace, etc.).
  2. ``<cwd>/.env`` — the operator's working directory, so
     `cd <project> && python3 ...` and the script's view of
     "project root" agree.
  3. The directory containing this file, then each of its
     parents, looking for ``.env``. This covers the
     ``editable install`` case (a downstream consumer who
     ``pip install -e .``'d the package and runs the CLI
     from anywhere on the filesystem).

The loader is intentionally NOT called from the package
``__init__``. Module-import side effects on ``os.environ``
would break the existing test pattern (tests in
``tests/test_investoday.py`` and
``tests/test_default_db_path`` ``pop`` env vars in
``setUp`` and expect them to stay popped). Instead, every
entry-point script (``fund_cli.py`` / ``fund_mcp.py`` /
``backfill.py`` / ``doctor.py`` / ...) calls
``load_env()`` after its ``import fund_data`` line. The
loader is idempotent: subsequent calls are no-ops.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_env", "parse_line"]


def parse_line(line: str) -> tuple[str, str] | None:
    """Parse one ``KEY=VALUE`` line. Returns ``(key, value)`` or
    ``None`` for comments / blanks / malformed lines.

    Public so ``tests/test_env.py`` can pin the contract
    without touching the filesystem.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    # Optional `export ` prefix so the same file can be both
    # `source`'d in a shell and read by Python.
    if stripped.startswith("export "):
        stripped = stripped[len("export "):].lstrip()
    if "=" not in stripped:
        return None
    key, _, value = stripped.partition("=")
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    # Strip a matching outer quote pair (single or double).
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    return key, value


def _candidate_paths() -> list[Path]:
    """Return candidate ``.env`` paths in priority order, dedup'd."""
    candidates: list[Path] = []
    override = os.environ.get("FUND_DATA_ENV_FILE")
    if override:
        candidates.append(Path(override).expanduser().resolve())
    # cwd first so an operator who `cd`'d into a sub-project
    # and dropped a `.env` there gets the local one.
    candidates.append((Path.cwd() / ".env").resolve())
    # Walk up from this file. ``fund_data/_env.py`` is two
    # levels below the project root (``scripts/`` and
    # ``fund-data/``), so the parent walk surfaces the
    # canonical ``<project-root>/.env`` even when the
    # entry point is launched from somewhere unrelated.
    here = Path(__file__).resolve().parent
    for ancestor in [here, *here.parents]:
        candidates.append((ancestor / ".env").resolve())
    # Deduplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def load_env(path: Path | str | None = None) -> dict[str, str]:
    """Load ``.env`` into ``os.environ`` (only unset keys).

    Returns the dict of ``KEY -> VALUE`` that were actually
    written; the dict is empty when the file is missing /
    empty / fully shadowed by pre-existing env vars. The
    function never raises for a missing file.

    Idempotent at the file level: a second call with the
    same ``path`` is a no-op because the keys it would write
    are already in ``os.environ`` (whether from the first
    call or from the shell). Pass an explicit ``path`` to
    load multiple files in sequence.
    """
    if path is not None:
        candidates = [Path(path).expanduser().resolve()]
    else:
        candidates = _candidate_paths()
    loaded: dict[str, str] = {}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            parsed = parse_line(raw)
            if parsed is None:
                continue
            key, value = parsed
            if key not in os.environ:
                os.environ[key] = value
                loaded[key] = value
        # First existing file wins; stop scanning.
        if loaded or candidate.is_file():
            return loaded
    return loaded
