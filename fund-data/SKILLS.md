# Skill Installation

This project is packaged as a skill consumable by three agent
platforms from a single source tree. The CLI is the same on every
platform — only the discovery mechanism differs.

| Platform | Skill location | Install mode |
|---|---|---|
| Codex CLI | `~/.codex/skills/fund-data/` | copy (refreshed by `install --copy`) |
| Claude Code | `~/.claude/skills/fund-data/` | symlink (auto-picks up local edits) |
| OpenClaw | `~/.openclaw/skills/fund-data/` | symlink (auto-picks up local edits) |

## One-shot install

```bash
cd /path/to/fundData/fund-data
python3 scripts/install_skill.py install
```

The installer prints a status table for all four targets. Targets
whose parent directory does not exist (e.g. you do not have OpenClaw
installed) are skipped, not failed.

## Targeted install

```bash
# Only install where Claude looks for skills
python3 scripts/install_skill.py install --target claude

# Copy instead of symlink (some agents follow symlinks, some do not)
python3 scripts/install_skill.py install --target codex --copy

# Portable copy with the current SQLite data snapshot included
python3 scripts/install_skill.py install --target codex --include-data
```

Copy refreshes include only the skill source files. Generated runtime
artifacts such as `data/backfill_logs/`, `data/backfill_state.json`,
`__pycache__/`, and `.DS_Store` are excluded so a Codex install stays
deterministic.

Data has two explicit modes for copy installs:

| Mode | Command | What happens |
|---|---|---|
| Lightweight (default) | `--copy --data-mode none` | Excludes `data/fund_data.sqlite`; the target rebuilds or points `FUND_DATA_DB` elsewhere. |
| Portable with data | `--include-data` or `--copy --data-mode copy` | Copies a consistent `data/fund_data.sqlite` snapshot using SQLite backup. WAL/SHM sidecars, logs, state, and caches stay excluded. |

## Cloud data cache

For OpenClaw, Codex, Claude, or any MCP-capable agent, prefer a
lightweight skill install plus a cloud data cache:

```bash
python3 scripts/fund_cli.py cloud status
python3 scripts/fund_cli.py cloud pull
```

`cloud status` is the cheap local inspection step. `cloud pull` reads
the remote manifest and downloads `fund_data_query.sqlite.gz` only when
the local cache is missing or its version/sha256 differs; it returns
`downloaded: false` when the existing cache already matches. Use
`cloud pull --force` only when CI or an operator deliberately wants to
re-download and verify the gzip. The command defaults to the project OSS
manifest (`FUND_DATA_MANIFEST_URL` overrides it). When `FUND_DATA_DB` is
not set, CLI, MCP, and direct Python helper calls automatically prefer
that cached query database over live providers. If OSS is unavailable,
they fall back to the normal provider/API chain.

To publish a new OSS release:

```bash
VERSION=$(date +%F)
python3 scripts/fund_cli.py cloud build-bundle \
  --source-db data/fund_data.sqlite \
  --output-dir ../dist/fund-data/releases/$VERSION \
  --base-url https://YOUR_BUCKET.oss-cn-hangzhou.aliyuncs.com/fund-data/releases/$VERSION/ \
  --version $VERSION \
  --manifest-output ../dist/fund-data/current/manifest.json
```

Upload `fund_data_query.sqlite.gz` and its `.sha256` object first, then
upload `current/manifest.json` last so clients never observe a
half-published release.

Full database archives are separate and private:

```bash
VERSION=$(date +%F-%H%M%S)
python3 scripts/fund_cli.py cloud archive-full \
  --source-db data/fund_data.sqlite \
  --output-dir ../dist/fund-data/full/$VERSION \
  --base-url oss://YOUR_PRIVATE_BUCKET/fund-data/full/$VERSION/ \
  --version $VERSION
```

The full archive keeps `raw_responses`, `sync_runs`, and
`sync_failures` for audit/rebuild use. Store it in a private bucket or
private object prefix, not the public query-bundle path.

## MCP server

Platforms that support MCP can run the bundled stdio server directly:

```bash
python3 /path/to/fundData/fund-data/scripts/fund_mcp.py
```

Example client config:

```json
{
  "mcpServers": {
    "fund-data": {
      "command": "python3",
      "args": ["/path/to/fundData/fund-data/scripts/fund_mcp.py"]
    }
  }
}
```

If the project has been installed as a Python package, use
`"command": "fund-mcp"` with no args.

The MCP server includes `fund_cloud_status` so agents can inspect the
local cache version and compare it with a remote manifest URL.

## Refresh

```bash
# Codex uses a real copy, refresh it after editing SKILL.md or scripts/
python3 scripts/install_skill.py install --target codex --copy

# Claude and OpenClaw symlinks pick up local edits automatically,
# no refresh needed.
```

## Uninstall

```bash
python3 scripts/install_skill.py uninstall
python3 scripts/install_skill.py uninstall --target codex --force
```

`--force` is required when removing a real directory (the Codex
install), to avoid clobbering a foreign skill with the same name.

## Status

```bash
python3 scripts/install_skill.py status
```

Reports `LINKED`, `INSTALLED`, `STALE`, or `MISSING` for each target.
A `STALE` symlink points somewhere other than this project — usually
because the repo was moved or renamed.

## How each platform discovers the skill

### Codex
Codex scans `~/.codex/skills/*/SKILL.md` at startup and uses the
YAML `description` to decide when to trigger. Place the file in
`scripts/` for the CLI, and `references/` for additional Markdown
the agent reads on demand.

### Claude Code
Claude Code scans `~/.claude/skills/*/SKILL.md` (or
`<project>/.claude/skills/`) and applies progressive disclosure:
only the YAML `name` + `description` are loaded into the system
prompt; the SKILL.md body loads on first match. `scripts/` is
executed via the bash tool.

### OpenClaw
OpenClaw scans six locations in priority order:

1. `openclaw-extra` (plugin dirs)
2. `openclaw-bundled` (framework)
3. `~/.openclaw/skills/` (managed)
4. `~/.agents/skills/` (personal)
5. `<workspace>/.agents/skills/` (project)
6. `<workspace>/skills/` (workspace local)

We install into `~/.openclaw/skills/fund-data/` by default. The
frontmatter `tools:` list tells OpenClaw which tools the skill
expects to call — keep it minimal and honest.
