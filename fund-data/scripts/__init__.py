"""fund-data skill scripts package.

Bundled helpers for the fund-data Codex / Claude / OpenClaw skill. The
real entrypoints live in this package:

- ``fund_data`` — parsers, providers, SQLite store, sync helpers.
- ``fund_cli`` — argparse CLI used by Codex, Claude Code (via bash),
  and OpenClaw (via exec) to search, fetch, persist, and export
  Chinese public fund data.

This package has no third-party runtime dependency: the standard
library is enough for the Eastmoney code path. AkShare is optional
and lives in a separate virtual environment; see ``SKILL.md`` for
the recommended setup.
"""
