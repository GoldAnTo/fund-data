# Agent 消费流程文档

> 给 AI agent（以及 onboarding 的人）参考的流程图，回答"fund-data 是怎么查 / 怎么拉 / 怎么落到 OSS / 怎么同步 / 怎么起 MCP / 怎么装到 agent 上"这类问题。

这个目录是 `fund-data` 的 **agent-facing 参考**，跟 `fund-data/ARCHITECTURE.md`（贡献者视角）和 `fund-data/SKILL.md`（SKILL 入口 manifest）配套。**人 / 中文 agent 翻 playbook**，**AI 自动化 / OpenClaw / 系统工具翻 pipeline**。

## 文档清单

每个流程都成对发布：

- **pipeline**（reference）—— 图（Mermaid + ASCII）+ 代码锚点 + env var 表。给 AI agent / 自动化脚本看，不翻译。
- **playbook**（answer script）—— 标准答案 + 12 个 FAQ + 设计哲学。**中文**，给人和中文 agent 看，回答"X 是怎么做的 / 为什么这么做"。

| 流程 | Reference（pipeline，英文） | 回答脚本（playbook，**中文**） |
|---|---|---|
| `fund_search` / `fund_list` —— 关键字搜索 + 全量列表 | [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) | [`fund-search-playbook.md`](./fund-search-playbook.md) |
| `fund_sync` / `fund_batch_sync` / `fund-backfill` —— 长跑同步 | [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) | [`fund-batch-sync-playbook.md`](./fund-batch-sync-playbook.md) |
| `fund-data` MCP server —— stdio JSON-RPC 2.0（17 个 tool） | [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md) | [`fund-mcp-server-playbook.md`](./fund-mcp-server-playbook.md) |
| `fund-cli cloud {build-bundle, pull, status, upload, archive-full}` —— OSS 分发 | [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) | [`fund-cloud-bundle-playbook.md`](./fund-cloud-bundle-playbook.md) |
| `fund_coverage` / `fund_coverage_report` —— 只读内省层 | [`fund-coverage-pipeline.md`](./fund-coverage-pipeline.md) | [`fund-coverage-playbook.md`](./fund-coverage-playbook.md) |
| `fund_self_audit` / `fund_health_check` — prioritized self-audit queue | [`fund-self-audit-pipeline.md`](./fund-self-audit-pipeline.md) | [`fund-self-audit-playbook.md`](./fund-self-audit-playbook.md) |
| `fund_completion_plan` / `fund_completion_run` / `fund_completion_verify` — OpenClaw active fill loop | [`openclaw-active-completion-pipeline.md`](./openclaw-active-completion-pipeline.md) | _(playbook pending — for now see the pipeline doc)_ |
| `install_skill.py {install, uninstall, status}` —— 把 skill 装到 OpenClaw / Codex / Claude | [`fund-install-pipeline.md`](./fund-install-pipeline.md) | [`fund-install-playbook.md`](./fund-install-playbook.md) |

## 怎么读这些文档

- **Pipeline（reference）**—— 代码 / 路径 / JSON 例子多的文档，**保留英文**。AI 自动化脚本读 pipeline 不会被中文标点 / 译文术语干扰，照着 `file:line` 锚点直接定位源码。
- **Playbook（answer script）**—— 对话式的回答文档，**中文**。回答"为什么这个设计"这类问题，11 个 FAQ + 设计哲学 + 反面教材是核心。
- **Mermaid 图**在两个版本里都保留——GitHub / OpenClaw / Codex / Claude Code 都原生支持 Mermaid 渲染。
- **ASCII 图**作为 fallback——终端 / `lynx` / 一些 IDE 不支持 Mermaid 时用。
- **`file:line` 代码锚点**在两个版本里都给出——读图之后想看代码，可以直接跳到源文件对应行。
- **env var 表**在每个 pipeline 末尾——agent 进新环境第一件事是看这里。

## 维护约定

按读者维度维护：

- **改 pipeline 的时候**——AI agent 是主要读者，文档必须 `file:line` 准确，mermaid / ascii 图跟代码同步。**保持英文**。
- **改 playbook 的时候**——人和中文 agent 是主要读者，FAQ 和设计哲学要更新到位。**保持中文**。
- **跨两个文档的改动**（比如一个新的 tool 既改了协议又改了语义）——一个 PR 里两个文档都更新。
- **新增流程**——按"成对发布"原则，先 pipeline（reference）落地，再 playbook（answer script）落地。

如果一个 PR 改了代码但没更新对应文档，reviewer 应该 request changes。
