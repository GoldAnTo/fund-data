# Agent Consumption Flows

> Reference diagrams for AI agents (and the humans who onboard them) to
> understand how `fund-data` resolves a fund lookup, a NAV pull, or a
> cloud-bootstrap request.

This directory is the **agent-facing companion** to
[`fund-data/ARCHITECTURE.md`](../fund-data/ARCHITECTURE.md) (which is
contributor-facing) and [`fund-data/SKILL.md`](../fund-data/SKILL.md)
(which is the SKILL.md skill manifest). When an agent asks "why did
`fund_search` return this provider's data?" or "why did the SQLite
land in `~/.cache/...` and not in my working directory?", the answer
lives here.

## Documents

| Flow | Doc |
|---|---|
| `fund_search` / `fund_list` — keyword search and full universe | [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) (diagrams + code anchors) |
| Answering "how does fund search work?" | [`fund-search-playbook.md`](./fund-search-playbook.md) (standard answer + 12 FAQs + design philosophy) |
| Fund-by-fund sync (`fund_sync` / `fund_batch_sync`) | _TBD — see [`fund-data/AGENTS.md` §Backfill](../fund-data/AGENTS.md) for the long-running recipe_ |

## How to read these diagrams

- **Mermaid block** is the canonical version — GitHub, OpenClaw, Codex,
  and Claude Code all render it natively.
- **ASCII block** is the fallback when a renderer does not support
  Mermaid (e.g. raw terminal, some IDE previews, `lynx`).
- **Code anchors** under each diagram point to the exact function and
  line range in the repo so a reader can verify the diagram against
  the source.
- **Env var table** at the end of each doc lists every knob an agent
  can flip to override the default behaviour.
