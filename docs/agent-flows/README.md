# Agent Consumption Flows

> Reference diagrams for AI agents (and the humans who onboard them) to
> understand how `fund-data` resolves a fund lookup, a NAV pull, a
> cloud-bootstrap request, a sync, an MCP tool call, or a coverage
> check.

This directory is the **agent-facing companion** to
[`fund-data/ARCHITECTURE.md`](../fund-data/ARCHITECTURE.md) (which is
contributor-facing) and [`fund-data/SKILL.md`](../fund-data/SKILL.md)
(which is the SKILL.md skill manifest). When an agent asks "why did
`fund_search` return this provider's data?" or "why did the SQLite
land in `~/.cache/...` and not in my working directory?", the answer
lives here.

## Documents

Each flow ships as a pair: a **pipeline** (diagrams + code anchors +
env var table; the reference) and a **playbook** (standard answer +
FAQs + design philosophy; the answer script).

| Flow | Reference (pipeline) | Answer script (playbook) |
|---|---|---|
| `fund_search` / `fund_list` — keyword search and full universe | [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) | [`fund-search-playbook.md`](./fund-search-playbook.md) |
| `fund_sync` / `fund_batch_sync` / `fund-backfill` — the long-running pipeline | [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) | [`fund-batch-sync-playbook.md`](./fund-batch-sync-playbook.md) |
| `fund-data` MCP server — JSON-RPC 2.0 over stdio (17 tools) | [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md) | [`fund-mcp-server-playbook.md`](./fund-mcp-server-playbook.md) |
| `fund-cli cloud {build-bundle, pull, status, upload, archive-full}` — the OSS distribution path | [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) | [`fund-cloud-bundle-playbook.md`](./fund-cloud-bundle-playbook.md) |
| `fund_coverage` / `fund_coverage_report` — the read-only introspection layer | [`fund-coverage-pipeline.md`](./fund-coverage-pipeline.md) | [`fund-coverage-playbook.md`](./fund-coverage-playbook.md) |

## How to read these diagrams

- **Mermaid block** is the canonical version — GitHub, OpenClaw, Codex,
  and Claude Code all render it natively.
- **ASCII block** is the fallback when a renderer does not support
  Mermaid (e.g. raw terminal, some IDE previews, `lynx`).
- **Code anchors** under each diagram point to the exact function and
  line range in the repo so a reader can verify the diagram against
  the source.
- **Env var table** at the end of each pipeline doc lists every knob
  an agent can flip to override the default behaviour.
- **Playbook docs** are the "answer script" — use them when someone
  asks "how does X work?" and you need a structured, opinionated
  answer with the reasoning behind each design choice.
