# Fund MCP Server 剧本（Playbook）

> **最后更新:** 2026-06-02
> **目标读者:** 任何人 —— 人或 AI —— 被问到"fund-data 的 MCP server 是怎么工作的？"、"怎么加 tool？"或"为什么我的 client 拿到 `INVALID_PARAMS` / `isError: true`？"。这是 **MCP 表面的回答脚本**。配套 [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md)（图表 + 代码锚点）一起看。
>
> **使用场景:**
> - onboarding 新 contributor 或 agent 进入 MCP 表面。
> - 审查涉及 `fund_mcp.py`、`TOOLS` 列表、`TOOL_HANDLERS` 或 `handle_message` 的 PR。
> - 排查"agent 看不到 tools"或"agent 调用返回空"或"agent 调用抛 `INVALID_PARAMS`"这类报告。
> - 回答关于 MCP 跟特定 client（OpenClaw、Codex、Claude Code、自定义）兼容性的问题。
> - 在工具目录里加新 tool。
>
> **不在使用场景之内:**
> - 问题是关于某个 tool 的数据语义 → 用 [`fund-search-playbook.md`](./fund-search-playbook.md) 或 [`fund-batch-sync-playbook.md`](./fund-batch-sync-playbook.md)。
> - 问题是关于底层 Python 库 → 用 [`fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md)。
> - 问题是关于把 MCP server 装到特定 agent 平台 → 用 [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md)。

---

## 60 秒答案（TL;DR）

`fund-data` 的 MCP server 是一个 **零依赖、单文件、跑在 stdin/stdout 上的 JSON-RPC 2.0 server**，把 17 个 tool 暴露给消费本地 `fund_data` Python 库的 agent。它实现了四个协议方法（`initialize`、`ping`、`tools/list`、`tools/call`），有一组固定的 tool（没有动态注册），两种错误形态（JSON-RPC 错误表示协议违反；`isError: true` 表示应用失败）。除了 `fund_cloud_status` 之外，每个 tool 在第一次调用时，如果 agent 没显式传 `db`，就会触发 cloud bootstrap。

定义性特征：

- **零依赖**。Server 不依赖 Python 标准库之外的任何包。如果 `fund_data` 和 `fund_cloud` 成功 import，server 就跑。
- **仅 stdio transport。** Server 是一个子进程；client spawn 它，通过 stdin/stdout 谈 JSON，需要时杀它。没有 socket，没有 HTTP，没有 daemon。
- **能力：只 tool。** 没有 resources、prompts、sampling。`initialize` 里的 `capabilities` 字段是 `{"tools": {"listChanged": false}}`。
- **17 个 tool，进程生命周期内固定。** 加 tool 要改代码 + 重启 server（`TOOLS` 列表是模块级常量）。

---

## 完整回答模板（用这个骨架）

当被问到"fund-data 的 MCP server 是怎么工作的？"，按这个结构回答，**四段对应四层**。顺序重要 —— 跟 client 视角的运行时调用顺序一致。

### 第 1 段 —— Transport

> Server 是 client 当作子进程 spawn 的单个 Python 进程。通信是 **stdin 和 stdout 上以换行符分隔的 JSON-RPC 2.0** —— 每边每行一个 JSON 对象，没有长度前缀 framing。Server 在 `main()`（line 644）里跑一个 `for line in sys.stdin` 循环，解析每行，通过 `handle_message` 分发，把响应写到 stdout。stderr 给日志用；stdout **严格**只放 JSON-RPC 响应。Tool handler 里意外往 stdout `print()` 会立即打破每个 MCP client，因为下一个 client 的行解析器会被杂散文本噎住。

### 第 2 段 —— Protocol

> Server 实现了四个方法。**`initialize`** 是 client 必须先发的调用；它返回协商好的 `protocolVersion`（`2024-11-05`、`2025-03-26`、`2025-06-18`、`2025-11-25` 之一，默认 fallback `2025-06-18`），server 的 `capabilities` —— `{"tools": {"listChanged": false}}`，`serverInfo`（name `fund-data`，version `0.2.0`），和一个 `instructions` 字符串。**`ping`** 返回 `{}` 用作健康检查。**`tools/list`** 返回 17 个 tool 字典，每个有 `name`、`description` 和 `inputSchema`（schema 声明 `additionalProperties: false`，所以未知字段会用 `INVALID_PARAMS` 拒绝）。**`tools/call`** 是干活的 —— 它根据 `params.name` 分发到 17 个 `TOOL_HANDLERS` 之一。Server 不实现 `resources/*`、`prompts/*` 或 client 来的任何 `notifications/*`。

### 第 3 段 —— Tool dispatch 和 cloud bootstrap

> 对每个 `tools/call`（`fund_cloud_status` 除外），server 在 tool handler 之前调 `_maybe_bootstrap_cloud(arguments)`。如果 agent 在 tool 参数里传了 `db`，bootstrap 跳过；否则它跑 `fund_cloud.ensure_project_bundle()`，那个函数会拉 OSS query bundle（或复用 cache，或者网络挂了安静 fallback —— 完整失败策略见 search playbook Q2）。Handler 然后对解析出来的 DB 路径执行。Handler 抛的 `TypeError` / `ValueError` 变成 JSON-RPC `INVALID_PARAMS`（参数验证失败）；任何其他异常变成 `isError: true` 的 tool 结果（应用失败）。成功结果返回 `content` + `structuredContent` 三件套 —— 见下段。

### 第 4 段 —— Result envelope

> 每个成功的 `tools/call` 响应有三个字段。**`content`** 是一个 text block 列表：`{"type": "text", "text": "<payload 的 JSON dump>"}`。对于列表 payload（比如 `fund_search` rows），dump 是 `json.dumps(rows, ensure_ascii=False, indent=2)` —— 人类可读，中文基金名原样渲染。**`structuredContent`** 是机器类型化的 payload：列表是 `{"rows": [...], "count": N}`；字典就是字典本身。**`isError`** 是成功标志 —— `true` 表示应用错误，`text` 字段带 `{"error": "<message>"}`。尊重 MCP 规范的 client 可以直接读 `structuredContent`，不用重新解析 text。把 `isError: true` 映射到 JSON-RPC 重试的 client 会进入循环，因为下一次调用返回同样的错误。

---

## 12 个最常被问到的问题（含详细答案 + 为什么这么设计）

下面这些问题是在 onboarding、support、PR review 中最常出现的。**按这里出现的顺序回答，用同样的详细程度** —— 这些是团队经过多轮"但为什么？"之后沉淀下来的解释。

### Q1. 为什么 server 是单一 stdio 进程，零依赖？

- **MCP stdio transport 是通用基线。** 每个支持 MCP 的 client（OpenClaw、Codex、Claude Code、参考 SDK）都知道怎么 spawn 一个子进程然后 pipe JSON。用 stdio 的 server 在哪都"开箱即用"；用 socket 的 server 需要 per-platform plumbing。
- **零依赖意味着没版本漂移。** Server 只 import `json`、`sys`、`pathlib`、`typing`，以及 in-tree 的 `fund_data` / `fund_cloud` 模块。Python 3.11 上的 OpenClaw 和 Python 3.13 上的 Codex 跑的是同一个 server。没有 `pip install mcp` 这步。
- **代价是没 HTTP / SSE transport。** 一个不能 spawn 本地子进程的远程 agent 没法调 server。团队在 v0.3.0 backlog 跟踪 Streamable HTTP wrapper。

### Q2. 为什么有两种错误形态（JSON-RPC error vs `isError: true`）？

- **JSON-RPC 错误是为协议违反。** Client 发了 server 没法解释的东西 —— 解析错误、未知方法、缺失必填字段、类型不匹配。Client 应该修调用，不该重试。错误码（`-32700` / `-32600` / `-32601` / `-32602` / `-32603`）是标准 JSON-RPC，大多数 client 把它映射到"开发者错误"。
- **`isError: true` 是为应用失败。** Client 发了有效调用，server 试了 honoring 它；调用失败因为 provider 链抛了，或 SQLite 写失败，或网络挂了。Client 应该决定是否重试（通常是 —— 网络闪断是瞬时的）还是 surface 错误（通常是 —— 找不到基金不是瞬时的）。
- **混了两种会让一种策略绑到另一种上。** 自动重试 JSON-RPC 错误的 client 会用畸形调用 spam server。不重试 `isError: true` 的 client 会在瞬时闪断时丢数据。两种形态让每个 client 选适合的策略。

### Q3. 为什么除了 `fund_cloud_status` 之外的每个 tool 都会触发 cloud bootstrap？

- **Bootstrap 是"免费基础设施"。** OSS 上的 `fund_data_query.sqlite.gz` bundle 是团队努力的结果，让 agent 不用在首次安装时跑 21 小时 AkShare backfill。如果 bundle 能到达，agent 几秒钟就拿到预填充的 DB。如果不能，agent fallback 到 live provider。
- **触发是 per-tool 不是 per-process。** 每个 tool 调用独立决定要不要 bootstrap，根据是否传了 `db`。一个想一次 pin 住 DB 然后重用的 agent 应该在每次调用时传 `db`；重复参数的代价低，写错 DB 的代价高。
- **`fund_cloud_status` 不触发 bootstrap 因为它 *就是* bootstrap 内省。** 一个想在做其他任何调用之前检查 cache 状态的 client 应该先调 `fund_cloud_status`；触发 bootstrap 来回答关于 bootstrap 的问题会是循环的。Cache 目录通过 `fund_cloud.status()` 直接查。

### Q4. 为什么 `capabilities.tools.listChanged` 设为 `false`？

- **Tool 集合是模块级常量。** `TOOLS` 在 `fund_mcp.py:98` 是一个 list literal；改它的唯一方法是重启 server 进程。一个拿到 `listChanged: true` 的 client 会期望 `notifications/tools/list_changed` 然后 poll `tools/list`；两个都没实现。
- **诚实的信号比虚假的承诺更有用。** 一个乐观尊重 `listChanged` 的 client 会重复调 `tools/list` 没好处。一个悲观尊重它（或忽略它）的 client 得到同样的答案。`false` 是契约。
- **团队没排除动态注册。** 如果"运行时注册自定义 tool"用例出现，`listChanged` 标志翻到 `true` 然后 server 开始发通知。在那之前，更简单的契约是对的。

### Q5. 为什么协议版本协商是"先支持胜出"，不是"用最新的"？

- **Client 可能比 server 老。** 一个 2025 年 3 月的 Codex 只知道 `2025-03-26`；如果 server 强制 `2025-11-25`，client 会发 server 没法解析的消息。协商让更老的 client 用它偏好的版本，只要 server 支持。
- **Fallback 是 `2025-06-18`。** 那是 2025 年底大多数 client 实现的版本；问未知版本的 client 拿到 fallback 而不是错误。Client 应该在那之后尊重返回的版本。
- **四个支持的版本（`2024-11-05` / `2025-03-26` / `2025-06-18` / `2025-11-25`）覆盖每个出货的 client。** 团队从 server 写好之后没需要过 bump 支持集；一个请求 `2026-03-01` 的新 client 会拿到 `2025-06-18` fallback 然后能工作，但团队应该把 `2026-03-01` 加到 `SUPPORTED_PROTOCOL_VERSIONS` 准备下次发布。

### Q6. 为什么 tool input schema 是 `additionalProperties: false`？

- **Server 拒绝拼写错误。** 一个打了 `keywords` 而不是 `keyword` 的 agent 拿到 `JSONRPC_INVALID_PARAMS: ...` 而不是静默空结果。严格 schema 是 agent 作者的开发者人体工程学胜利。
- **代价是前向兼容。** 给 tool 的 schema 加新字段，技术上对依赖旧 `additionalProperties: true` 形状的 client 是破坏性变更。团队接受这点因为 tool 集合小（17 个 tool）而且 changelog 是改了什么的事实之源。
- **`additionalProperties: false` 也是 MCP 规范建议。** 读 schema 然后构造验证器的 client 从显式 allow-list 受益。

### Q7. 为什么 server 不实现 `resources/list` 或 `prompts/list`？

- **团队目前不需要它们。** 17 个 tool 覆盖 agent 的数据平面需求；resource 形状（`fund://funds/110022` URI）是 `fund_export` 之上的糖层，团队还没建。Prompts 是更大的层（模板、partials、占位符），不在 v0.2.0 范围。
- **`capabilities` 字段是诚实的信号。** 一个问 `resources/list` 的 client 拿到 `METHOD_NOT_FOUND` 错误而不是误导性的空列表。Client 不应该从缺失的调用推断"server 没有 resources"；应该从 `capabilities` 推断"server 没实现 resources"。
- **加 resources 或 prompts 是清晰的下一步。** `fund://funds/{code}` resource URI 映射到 `fund_export(table="funds", fund_code=code)` 是 30 行变更。一个 `fund-compare` prompt 拉三个基金的 snapshot 然后让 LLM 比较，是 50 行变更。两个都跟踪在 v0.3.0 backlog。

### Q8. 为什么 `content[0].text` 是 JSON dump，不是 markdown 表格？

- **JSON 是通用交换格式。** 每个 MCP client 知道怎么渲染 JSON；不是每个 client 知道怎么渲染 markdown 表格。Text 永远是有效 JSON，不管 payload 形状。
- **`structuredContent` 是给想渲染 JSON 以外的东西的 client 的类型化 payload。** 想显示表格的 client 可以读 `structuredContent.rows` 然后格式化。想显示 JSON 的 client 可以直接读 `content[0].text`。两条路都支持。
- **Dump 用 `ensure_ascii=False`。** 中文基金名原样渲染，不是 `\uXXXX` 转义。假设 ASCII 的 client 会解析错；契约是 UTF-8。

### Q9. 为什么 `tools/call` 只把 `TypeError` 和 `ValueError` 当 `INVALID_PARAMS`？

- **`TypeError` 是最常见的参数验证失败。** 一个期望 string 但拿到 `None` 的 tool 抛 `TypeError`；一个期望 list 但拿到 dict 的 tool 抛 `TypeError`。这些是 agent 的错，不是 server 的错。
- **`ValueError` 是第二常见的。** 一个期望 6 位 fund code 但拿到 `"abc"` 的 tool 抛 `ValueError("fund code must contain 6 digits")`。同样是 agent 的错。
- **任何其他异常是应用错误。** 来自 SQL 响应缺列的 `KeyError`，锁住的 DB 来的 `sqlite3.OperationalError`，链来的 `ProviderError` —— 都变成 `isError: true` 的 tool 结果。Client 决定是否重试。

### Q10. 为什么 server 不为大表流式返回结果？

- **Stdio 是行缓冲的。** 契约是每行一个 JSON 对象。流式意味着要么每次结果多行（打破行作为消息边界的契约），要么单次响应多个 `content` block（spec 对 `tools/call` 不鼓励）。
- **`fund_export` 接受 `limit` 参数。** 想走 26k-fund 表的 agent 应该分块 1000 调 `fund_export`，而不是一次问整个表。`structuredContent` 里的 `count` 字段让 agent 知道有多少行。
- **团队没看到流式的用例。** 最大现实调用是对 26k 基金的 `fund_coverage_report`；当前调用返回 ~26k 小行，序列化成 ~3 MB JSON。Client 不用流式也能处理。未来的 `notifications/progress` 通道会帮 agent 知道调用还在工作，但它不会改结果交付形状。

### Q11. 为什么 server 不验证 client？

- **Server 是本地子进程。** Spawn server 的 client 就有本地机器访问权；能跟 server stdin 对话的 attacker 已经有本地机器访问权。没有远程信任边界需要强制。
- **数据是公开的。** 基金池、NAV 历史、持仓都是 Eastmoney 和 AkShare 上的公开信息。响应 payload 里没有秘密材料。
- **Provider key 是用户的，不是 server 的。** `INVESTODAY_API_KEY` 和 `TUSHARE_TOKEN` 在 server 的环境里，不在请求里。有 server 环境的 agent 继承 key；没有的 agent 有对 no-key provider 的只读访问。这是对的信任模型：用户控制 key，agent 拿到用户付费的范围。
- **未来 HTTP transport 需要 auth。** 跟踪在 v0.3.0；auth 方案可能是 per-session bearer token，token 由用户带外分享。

### Q12. 为什么 server 不实现 `notifications/cancelled`？

- **Server 是同步的。** 一个 `tools/call` 阻塞到 handler 返回；server 在阻塞时没法接受 `notifications/cancelled`。
- **变通办法在 client 层。** 想取消长调用的 client 关掉 server 的 stdin，这终止子进程。Server 在 EOF 时 `main()` 返回 0；client 清理。
- **Spec 允许这点。** 同步 server 被显式允许忽略 `notifications/cancelled`；期望是 async server 会尊重。当团队加 Streamable HTTP transport 时，async server 会实现取消。

---

## 设计哲学（为什么四方法是这个形状）

读完这一节，剩下的 playbook 就显而易见了。

1. **Stdio 是通用基线。** 每个 MCP client 知道怎么 spawn 子进程。用 stdio 的 server 在哪都"开箱即用"；用 socket 的 server 需要 per-platform plumbing。团队的哲学是"每个 client 都能用的最简单 transport" —— stdio 赢。
2. **零依赖意味着没版本漂移。** Server 只 import Python 标准库和 in-tree 的 `fund_data` / `fund_cloud`。没有 `pip install mcp` 这步，没有版本 pin，Python 小版本 bump 不会破。代价是没有第三方 helper（比如 `pydantic` 做 schema 验证）；收益是零安装。
3. **两种错误形态对应两种失败策略。** JSON-RPC 错误是为协议违反（开发者修）；`isError: true` 是为应用失败（client 重试）。混起来会让一种策略绑到另一种。两种形态让每个 client 选。
4. **Cloud bootstrap 是 per-call 决定，不是 per-process。** 想 pin DB 的 agent 在每次调用时传 `db`；想要 cache 默认的省略它。Server 的 `_maybe_bootstrap_cloud` 每次跑，cache 命中时 ~1 ms 便宜。
5. **Capabilities 是诚实的信号。** Server 声明它实际实现的（`tools`，`listChanged: false`）。问 resources 的 client 拿到 `METHOD_NOT_FOUND` 可以决定是否 fallback。`capabilities` 字段是契约。
6. **`content[0].text` 是通用 payload，`structuredContent` 是类型化 payload。** 只渲染 text 的 client 读 text；想要类型化 payload 的 client 读 `structuredContent`。两个在每次成功调用时都填；都不被偏好。
7. **17 tool 集合在进程生命周期内固定。** 加 tool 是代码变更 + server 重启。团队没建"运行时注册自定义 tool"的 API；如果用例出现，`listChanged` 翻到 `true` 然后 server 发通知。在那之前，静态 tool 集合是对的形状。

---

## 反面教材（不要这么说）

这些是 PR review 和 support 线程里见过的常见错误回答。避开它们。

- **"Server 是 async 的。"** 不是。`for line in sys.stdin` 循环是阻塞 readline；长 tool 调用阻塞循环。假设 async 的 client 可以并行 pipe 多次调用，会看到它们被串行化。
- **"它支持 HTTP。"** 不支持。仅 stdio。未来的 Streamable HTTP transport 跟踪在 v0.3.0；不要今天就承诺。
- **"它支持 resources 和 prompts。"** 不支持。`capabilities` 是 `{"tools": {}}`；问 `resources/list` 的 client 拿到 `METHOD_NOT_FOUND`。
- **"Tool 结果是 JSON。"** 是三件套：`content`（text JSON dump）+ `structuredContent`（类型化 payload）+ `isError`（成功标志）。混了这三个是常见的 client bug。
- **"`isError: true` 表示调用在协议层失败。"** 不，表示调用在协议层成功，tool 在应用层抛。JSON-RPC 错误才是协议失败的样子。
- **"Server 有 `fund_doctor` tool。"** 没有。v0.3.0 backlog 有；在那之前，想要健康检查的 agent 必须 shell 出去调 `fund-cli doctor`。
- **"Server 流式返回结果。"** 不。每响应一个 JSON 对象，没有 chunked encoding，没有 `notifications/progress`。想走大表的 agent 应该分块 1000 调 `fund_export`。
- **"协议版本是硬编码的。"** 在 `initialize` 里协商。Server 声明支持四个版本（`2024-11-05` / `2025-03-26` / `2025-06-18` / `2025-11-25`），client 问未知版本时 fallback 到 `2025-06-18`。
- **"Cloud bootstrap 是一次性的。"** 是 per-call 的。每个 tool 调用（`fund_cloud_status` 除外）重跑 bootstrap 决策；传 `db` 跳过，省略重新检查 cache。

---

## 怎么加新 tool（贡献者配方）

当新 capability 在 `fund_data.py` 落地并且团队想把它暴露为 MCP tool：

1. **加 Python helper** 到 `fund_data.py`（如果还不存在）。Helper 必须接受 `db_path` 和 `provider` 作为 keyword 参数（或者通过现有的 fetch 约定）。
2. **加 tool 字典** 到 `fund_mcp.py` 的 `TOOLS`：
   ```python
   _tool(
       "fund_new_thing",
       "描述解释 agent 用例的，不是实现的。",
       {
           **COMMON_ARGS,
           "code": _string_schema("6 位 fund code。"),
           # ... 其他参数
       },
       required=["code"],
   ),
   ```
3. **加 `_call_fund_new_thing` handler** 在 `fund_mcp.py`，调 Python helper。Handler 签名是 `(arguments: dict) -> Any`。
4. **在 `TOOL_HANDLERS` 注册 handler**：
   ```python
   "fund_new_thing": _call_fund_new_thing,
   ```
5. **更新 `SKILL.md` 和 `install_skill.py` skill manifest** 如果 tool 需要新 frontmatter 字段。
6. **加单元测试** 在 `fund-data/scripts/tests/`，用一个 fake fund_data 模块走 handler。
7. **Bump `SERVER_VERSION`** 在 `fund_mcp.py:27` 如果 tool 对 client 是破坏性变更。

如果 tool 不需要 cloud bootstrap（比如纯 cache 内省），加到 `handle_message` 里的 exception 列表（`tool_name != "fund_cloud_status"`）。

---

## 怎么保持这个剧本准确

剧本是团队 *settled* 的解释，不是 live 代码。代码变了，在同一个 PR 里更新剧本。检查项：

- 新 tool 加到 `TOOLS` → 更新 Q3 目录和贡献者配方。
- 新 MCP 方法实现 → 更新 Q2（错误形态）和 Q7（能力）。
- 新错误码引入 → 更新 Q2 和协议表。
- 特定 tool 的 bootstrap 行为变了 → 更新 Q3。
- 协议版本集变了 → 更新 Q5。
- 新 client 集成 → 更新相关 playbook 里的 gateway config 例子。

如果 PR 改了上面任何一项但没更新剧本，request changes 时指这一节。

---

## 相关文档

- [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md) —— 图表 + 代码锚点 + tool 目录。
- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —— tool 调用 *内部* 发生什么（cloud bootstrap + provider chain）。
- [`fund-search-playbook.md`](./fund-search-playbook.md) —— 单次 search 的回答脚本。
- [`fund-batch-sync-playbook.md`](./fund-batch-sync-playbook.md) —— batch sync 的回答脚本（给 `fund_sync` / `fund_batch_sync` tool 用）。
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —— agent-facing skill manifest。
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —— Codex / Claude / OpenClaw 的 per-platform install 布局；§"MCP server" 的 MCP server config block 是规范的 gateway 片段。
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —— v0.3.0 backlog（HTTP/SSE transport、progress 通知、resources、prompts、doctor tool）。
