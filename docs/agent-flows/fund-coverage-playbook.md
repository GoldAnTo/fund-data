# Fund Coverage 剧本（Playbook）

> **最后更新:** 2026-06-02
> **目标读者:** 任何人 —— 人或 AI —— 被问到"怎么衡量数据完整度？"、"backfill 跑完了吗？"、"哪些基金缺 NAV？"或"49% stock 持仓覆盖率是什么意思？"。这是 **只读内省层的回答脚本**。配套 [`fund-coverage-pipeline.md`](./fund-coverage-pipeline.md)（图表 + 代码锚点）一起看。
>
> **使用场景:**
> - onboarding 新 operator 或 agent 进入数据平面。
> - 审查涉及 `coverage_report`、`coverage_report.py` 或 `doctor.py` 的 coverage 部分的 PR。
> - 排查"backfill 说 ok=27000 但我没数据"或"doctor 说 100% 但 agent 说 49%"这类报告。
> - 回答关于"自然稀疏"数据集 vs 可修复缺口的问题。
> - 计划新数据集添加。
>
> **不在使用场景之内:**
> - 问题是关于 writer（backfill）→ 用 [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md)。
> - 问题是关于分发路径 → 用 [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md)。
> - 问题是关于单个基金的数据 → 用 [`fund-search-playbook.md`](./fund-search-playbook.md)。

---

## 60 秒答案（TL;DR）

`fund-data` 的 coverage 是 **只读内省层**，回答"数据对这个 question 够好吗？"，不写任何行。有两种报告模式：

- **Coverage 模式** —— 在基金池上 per-dataset 覆盖率 %，加 per-fund **completeness 评分** `[0, 1]` 和 per-fund `missing` 列表（空数据集名）。评分是 8 个数据集（`profile`、`nav`、`stock_holdings`、`bond_holdings`、`industries`、`fees`、`dividends`、`splits`）的等权平均。`fund_managers` 在行里有报但 **不**算入 completeness。
- **Stale 模式** —— 最新 snapshot 或 NAV 超过 `--max-age-hours`（默认 24 小时）的基金，或者两者都没有的。用来回答"夜间 backfill 是不是漏了什么？"。

定义性特征：

- **只读。** Coverage 跑 `SELECT`，从不 `INSERT/UPDATE/DELETE`。在 sync 任何点调都安全，不会推进数据。
- **两层聚合。** Per-fund 行（`completeness`、`missing`）是输入；per-dataset % 是从它们派生的。% 是过滤器产出的行上的，不是整个池子。
- **8 个数据集，不是 14 个。** `fund_managers` 和 3 个审计表（`raw_responses`、`sync_runs`、`sync_failures`）不在 coverage 评分里。Manager 数据是目录式的；审计表是 operator telemetry。
- **Stale 是 per-fund，不是 per-dataset。** Stock 持仓 6 个月 stale 但 NAV fresh 的基金整体是"stale"。Per-dataset stale 视图在 v0.3.0 backlog。

---

## 完整回答模板（用这个骨架）

当被问到"coverage 是怎么工作的？"，按这个结构回答，**四段对应四个概念**。顺序重要。

### 第 1 段 —— 两种模式

> `fund-data` 有两种只读内省模式。**Coverage 模式**回答"每个基金的数据多完整？" —— 返回一个字典列表，每个基金一个，每个带一个 `[0, 1]` 的 `completeness` 评分（8 个数据集等权平均）和一个空数据集名的 `missing` 列表。**Stale 模式**回答"哪些基金最近没刷过？" —— 返回最新 `snapshots.fetched_at` 或 `nav_history.fetched_at` 超过 `--max-age-hours`（默认 24）的基金，或者根本没有行的。两种模式共用同一个 DB 路径；渲染不同。都是 `SELECT`-only，从不写。

### 第 2 段 —— 8 个数据集

> Coverage 评分覆盖 **8 个数据集**：`profile`、`nav`、`stock_holdings`、`bond_holdings`、`industries`、`fees`、`dividends`、`splits`。每个数据集用 SQL `case when ... is null then 0 else 1` 跨从 `funds` 表的 `LEFT JOIN` 检查。8 个都齐的基金有 `completeness = 1.0` 和 `missing = []`。4 个齐的基金有 `completeness = 0.5` 和 `missing` 列其他 4 个。`fund_managers` 在 SQL 输出里（`manager_rows` 列）但不计入 completeness。3 个审计表（`raw_responses`、`sync_runs`、`sync_failures`）完全不在评分里。

### 第 3 段 —— 自然稀疏 vs 可修复

> 一些数据集对一些基金类型期望就是空的。**自然稀疏**意思是那种基金类型按设计就不带那个数据集：债券型基金没 `stock_holdings`、货币型基金没 `stock_holdings` 也没 `bond_holdings`、REITs 没公开披露、大多数基金不分红或不拆分。AGENTS.md 里的"全局"覆盖率数字被这些基金夸大了；per-fund_type 分解显示结构性缺口。**可修复**意思是数据集应该在那里但缺了：应该有 profile 的基金 profile 空，sync 后 NAV 空，应该被接住的季度报告 holdings 空。Coverage 不区分自然稀疏和可修复；它只报"行在不在"。想要 fund_type 感知视图的 agent 应该按 `fund_type` filter。

### 第 4 段 —— Stale 模式

> Stale 模式是"backfill 是不是漏了什么？"检查。一个基金是 stale 如果它最新的 `snapshots.fetched_at` 超过 cutoff（默认 24 小时），**或**它最新的 `nav_history.fetched_at` 超过 cutoff，**或**任一时间戳是 null。Cutoff 是 `utc_now() - max_age_hours`。默认 24 小时是粗的阈值；对于 03:00 跑的夜间 backfill，`--max-age-hours 36` 更准（今天 03:00 刷的基金到明天 15:00 都算 fresh，尽管数据是一天前的）。CLI 调用是 `coverage_report.py --stale --max-age-hours 36`。Stale 模式是 per-fund 不是 per-dataset —— stock 持仓 6 个月 stale 但 NAV fresh 的基金整体是"stale"。Per-dataset stale 视图在 v0.3.0 backlog。

---

## 12 个最常被问到的问题（含详细答案 + 为什么这么设计）

下面这些问题是在 onboarding、support、PR review 中最常出现的。**按这里出现的顺序回答，用同样的详细程度** —— 这些是团队经过多轮"但为什么？"之后沉淀下来的解释。

### Q1. 为什么是 8 个数据集，不是所有 14 个？

- **8 个数据集是 agent 的数据平面。** `profile`、`nav`、`stock_holdings`、`bond_holdings`、`industries`、`fees`、`dividends`、`splits` 是 agent 在运行时读的表，回答基金问题。
- **`fund_managers` 是目录式数据。** 一个基金有 0-N 经理在它的生命周期里；行数告诉你"我们对这个基金的经理历史有多少记录"，不是"这个基金完整吗"。
- **3 个审计表是 operator telemetry。** `raw_responses`（完整上游 HTTP body；可能含调用方 IP）、`sync_runs`（每次 sync 调用的 audit 行）、`sync_failures`（每次硬失败 sync 调用的队列行）。不同机器上的 agent 用不到它们。
- **分割也是隐私边界。** Query bundle（`fund-cloud-bundle-pipeline.md` §3.2）剥掉审计表；coverage 遵循同样逻辑。

### Q2. 为什么 `fund_managers` 在行输出里但不在 completeness 评分里？

- **Manager 数据是目录，不是数据集。** 一个基金有当前和历史的经理；计数告诉你"我们对这个基金的经理历史有多少记录"，不是"基金完整吗"。
- **经理记录有噪声。** 基金的经理可能每个季度换；计数会波动。把计数包含进 completeness 会让评分不稳定。
- **Agent 仍然可以通过 `fund_managers(code=...)` 查询经理数据。** 数据是可用的；它就是不在头条评分里。

### Q3. 为什么 completeness 评分是等权的，不是按重要性加权？

- **等权是最简单又正确的答案。** 每个数据集是 `1/8` 的评分。想要不同加权的 agent 可以从 per-fund 字典里自己算。
- **团队考虑过 NAV 加权**（NAV 是最重要的数据集，所以权重更高）。权衡是"权重选择有文档支持、跨 release 稳定吗？"。等权稳定；任何其他权重需要 schema 迁移。
- **评分是提示，不是判决。** `completeness = 0.5` 的基金可能有 profile + nav + stock_holdings + fees（最常见的 4 个 agent 查询），对大多数问题"够好"。评分是单数字汇总，不是 per-question 适配指标。

### Q4. 为什么 markdown header 里的 per-dataset % 取决于过滤器？

- **Renderer 在它收到的行上聚合。** 调用方传 `fund_type='股票型'`，per-dataset % 是股票型基金上的，不是整个池。这是 feature 不是 bug：想要"股票型 coverage"的 operator 不用第二次 pass。
- **"全局"视图**是 `coverage_report()` 不带过滤器。Markdown header 读 `funds: <total> • reported: <reported>` 让差异显式。
- **想要两个视图的 agent**应该调两次：一次不带过滤器（池），一次带 `fund_type`（per-type 视图）。代价是两次 SQL 查询；两个在 27k 基金 DB 上都是亚秒级。

### Q5. 为什么 stale 阈值是 24 小时，什么时候改？

- **24 小时是粗默认。** 它能抓住"夜间 backfill 跑了 5% 基金被跳过"，但对"03:00 backfill 刷了 100% 池吗？"太噪。
- **对于 03:00 跑的日常 backfill**，用 `--max-age-hours 36`。今天 03:00 刷的基金到明天 15:00 都算 fresh，尽管数据是一天前的。
- **对于每周 backfill**，用 `--max-age-hours 192`（8 天）。阈值是"数据不应该比 backfill 节奏 + 边距更老"。
- **对于 on-demand pull**，用 `--max-age-hours 1`。过去一小时没碰过的基金是 stale，因为 on-demand pull 期望是当下的。

### Q6. 为什么 stale 模式是 per-fund，不是 per-dataset？

- **MVP 是 per-fund。** 团队第一个迭代是"这个基金 stale 吗？" —— per-dataset 视图是后续。
- **Per-dataset staleness 对诊断更有用。** Stock 持仓 stale 但 NAV fresh 的基金有真问题（季度报告接住失败了）。Per-fund staleness 藏了这个；per-dataset staleness 暴露它。
- **V0.3.0 backlog 有 per-dataset 视图。** 实现直白 —— 一个 SQL 带 per 表 `max(fetched_at)` —— 但 renderer 需要新列形状，markdown header 需要新聚合行。

### Q7. 为什么 `coverage_report` 在算分时不知道 fund_type？

- **SQL 是通用的 8 表 LEFT JOIN。** 知道 fund_type 需要 SQL JOIN `funds.fund_type` 然后应用 per-type 规则，这是更复杂更难维护的查询。
- **Agent / operator 在 `WHERE` 子句里按 fund_type 过滤。** Per-type 视图通过传 `--fund-type 股票型`（或代码中等价的）来计算。
- **权衡是简单性 vs 准确度。** 团队选了简单。AGENTS.md 里的"全局"覆盖率数字是头条；per-type 数字是后续 drilldown。

### Q8. 为什么 coverage 用 `LEFT JOIN` 而不是分开的 `EXISTS` 子查询？

- **`LEFT JOIN` 是单 SQL pass。** 8 张表 join 在一次 round trip；case-when 检查 join key 的 nullability。分开 `EXISTS` 一次一表就是 8 次 round trip。
- **`LEFT JOIN` 优雅处理缺失表。** 如果 `fund_profiles` 不存在（DB 半建），LEFT JOIN 返回 null，case-when 报 0；针对不存在表的 `EXISTS` 查询会抛 `OperationalError`。
- **代价是宽结果行。** 8 表 join 产生一行带所有 8 个 `*_rows` 列的行；per-fund 字典宽。权衡是一个宽行 vs 八个窄行。SQLite 优化了同查询里的宽行。

### Q9. 为什么 doctor 的 coverage 段跟进程内报告不匹配？

- **`doctor.py` 读本机 DB**；`coverage_report` 读 `default_db_path()`，优先 OSS cache。`cloud pull` 之后两个是不同的 DB。
- **AGENTS.md 里的"Long-running pitfalls"笔记**把这点文档化为最常见的"错 DB"报告。想要 doctor 报 cache 数字的 agent 应该传 `FUND_DATA_DB=/path/to/cache/.../fund_data_query.sqlite` 给 doctor，或者 unset env var 强制 fallback。
- **两个视图是有意的。** Doctor 是 operator 看生产 DB 的视图；进程内报告是 agent 看 bootstrap 解析出来的 DB 的视图。混了会丢信号。

### Q10. 为什么 `coverage_report` 既作为 MCP tool 又作为 Python helper 暴露？

- **MCP tool 是给 agent 的。** OpenClaw daemon 想检查 coverage 调 `fund_coverage_report` 拿到结构化 payload。
- **Python helper 是给人用的，给嵌入式场景。** Operator 想给自定义脚本加 coverage 检查直接 import `coverage_report`。
- **两个都包同一个 SQL。** MCP tool 调 Python helper；helper 调同一个 SQL。没"MCP 版本" vs "CLI 版本"分歧。Helper 里的改动自动是 tool 里的改动。

### Q11. 为什么 renderer 不把 `fund_managers` 包含进 `missing` 列表？

- **`fund_managers` 不在 8 数据集评分里。** 没经理行但 8 个数据集都齐的基金有 `completeness = 1.0` 和 `missing = []`，尽管 `manager_rows = 0`。
- **把 `fund_managers` 加到 `missing` 列表会不一致** —— 评分说 1.0 但 missing 列表说"managers"空。
- **团队的选择是把 `manager_rows` 作为内省用的列报**，但不计入评分。`missing` 列表跟评分一致。

### Q12. 为什么 markdown renderer 显示 top 10 最不完整，table renderer 显示 200？

- **Markdown 是给 PR 描述和聊天消息的。** 10 行是人类读的"上限"；更多是噪声。
- **Table 是给终端 review 的。** 200 行是 100 列终端可读固定宽表的上限。更多会折行。
- **JSON 输出是给下游工具的。** 无限制（好吧，`limit` 参数）—— 消费者能处理完整列表。
- **三个限制反映三个消费者。** 想要完整列表的 agent 用 `--format json --limit 0`（或不带 limit）。Markdown 默认 `--limit 10` 是人类读者的甜点。

---

## 设计哲学（为什么两模式是这个形状）

读完这一节，剩下的 playbook 就显而易见了。

1. **Coverage 是只读的。** SQL 是 `SELECT`，从不 `INSERT/UPDATE/DELETE`。Coverage 可以在 sync 期间跑而不干扰。想做"数据准备好了吗？"检查的 agent 在 backfill batch 之后调 coverage；coverage 报告反映到目前为止提交的 rows。
2. **8 数据集评分是单数字汇总。** 它不是 per-question 适配指标。`completeness = 0.5` 的基金可能对最常见的 agent 查询（profile + nav + holdings + fees）"够好"，对红利分析"不够好"。Agent 决定 per-question。
3. **等权是最简单又正确的答案。** 任何其他权重需要 schema 迁移和文档负担。团队选择"等 + 简单"。
4. **`fund_managers` 是目录式，不是数据平面。** 它在行里为了内省报，但不计分。经理轮换会破坏评分；团队偏好稳定。
5. **Stale 是 per-fund 不是 per-dataset。** MVP 是 per-fund；per-dataset 视图是 v0.3.0 后续。团队选择先发布更简单的版本。
6. **24 小时阈值是粗默认。** Operator 通过 `--max-age-hours` 调以匹配他们的 backfill 节奏。默认是给还没测自己节奏的 evaluator；生产部署把它设成 `cadence + 边距`。
7. **Doctor 和进程内报告是不同视图。** Doctor 读本机 DB；进程内报告读 `default_db_path()`。当 backfill 写到 cache DB 时两者分歧。AGENTS.md 里的"Long-running pitfalls"笔记文档化了分歧；修法是为 backfill run 显式设 `FUND_DATA_DB`。
8. **Renderer 按消费者拆分。** Markdown 给人（PR 描述、聊天），table 给终端（快速 review），JSON 给下游工具（agent、脚本）。三个限制（10、200、无限制）匹配三个消费者的可读性阈值。

---

## 反面教材（不要这么说）

这些是 PR review 和 support 线程里见过的常见错误回答。避开它们。

- **"Coverage 是全局的。"** 不是。Per-dataset % 是过滤器产出的行上的，不是整个池。想要全局视图的 agent 应该不传过滤器；想要 per-type 视图的应该传 `--fund-type`。
- **"`completeness = 1.0` 表示基金完全覆盖了。"** 表示 8 个数据集都齐。不表示数据是当下的（用 stale 模式）、准确的（没那种检查）、行级完整的（有 1 个 NAV 行的基金跟有 1000 个的有同样 `completeness`）。
- **"Stale 意味着数据是错的。"** Stale 意味着数据是老的。24 小时 stale 的 NAV 可能仍然是最好的可用的；agent 决定要不要刷新。
- **"Doctor 和进程内报告应该一致。"** 它们可以分歧；分歧是有意的和文档化的。修法是对齐 DB 路径，不是对齐报告。
- **"每次 sync 后跑 coverage 验证。"** Coverage 是 `SELECT`；在 backfill 里跑它 27k 次会拖慢 backfill。团队建议是 backfill 末尾跑一次 coverage，不是 per-fund。
- **"`fund_managers = 0` 是 coverage miss。"** 不是。`fund_managers` 不在 8 数据集评分里；列是为了内省。没经理行但 8 个数据集都齐的基金是全覆盖。
- **"自然稀疏数据集夸大了全局覆盖率数字。"** 是。AGENTS.md §Coverage by `fund_type` 显示 per-type 分解。想要类型感知视图的 agent 应该过滤。
- **"Completeness 评分是覆盖基金的百分比。"** 是基金 8 个数据集的现存的百分比，等权。4/8 数据集的基金有 `completeness = 0.5`，不是"50% 覆盖"。

---

## 怎么保持这个剧本准确

剧本是团队 *settled* 的解释，不是 live 代码。代码变了，在同一个 PR 里更新剧本。检查项：

- 数据集加进或从 `coverage_report` 移除（8 数据集列表）→ 更新 §3 和 completeness 定义。
- Stale 阈值默认变了 → 更新 §3.2 和 §6。
- 新入口加进来（比如 `fund_coverage_diff` MCP tool 比较两个 snapshot）→ 更新 §4。
- 输出形状变了（字典加/删字段）→ 更新 §5。
- 新过滤器加进来（比如 `min_manager_rows`）→ 更新 §6。
- Renderer 限制变了（markdown 10、table 200）→ 更新 §6 和 Q12。

如果 PR 改了上面任何一项但没更新剧本，request changes 时指这一节。

---

## 相关文档

- [`fund-coverage-pipeline.md`](./fund-coverage-pipeline.md) —— 图表 + 代码锚点 + 结果形状。
- [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) —— coverage 衡量的数据的 writer。
- [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) —— 让 fresh agent 落在已知 coverage 状态的分发路径。
- [`fund-search-playbook.md`](./fund-search-playbook.md) —— 单次 search 的回答脚本（给 `fund_coverage` / `fund_coverage_report` tool 用）。
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —— agent-facing skill manifest。
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —— per-fund_type coverage 分解、长跑陷阱（default_db_path vs doctor 分歧）和操作清单。
- [`../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md`](../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md) —— 结构性缺口分析（自然稀疏 vs 可修复 vs 不可修复 vs 0.3.0 backlog）。
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —— v0.3.0 backlog（per-dataset staleness、fund_doctor MCP tool 等）。
