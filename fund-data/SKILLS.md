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
```

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
