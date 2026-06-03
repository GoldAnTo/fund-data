# Fund Search 剧本（Playbook）

> **最后更新:** 2026-06-02
> **目标读者:** 任何人 —— 人或 AI —— 被问到"fund-data 是怎么查基金的？"或"为什么 search 走这四层？"或"为什么 search 不直接查本地 SQLite？"。这是 **回答脚本**，不是架构参考。配套 [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md)（图表 + 代码锚点）一起看。
>
> **使用场景:**
> - onboarding 新 contributor 或 agent 进入数据平面。
> - 审查涉及 `fund_data.search_funds`、`fund_cloud.ensure_project_bundle` 或 `default_db_path` 的 PR。
> - 排查"search 返回的不是这个 provider 的数据"或"agent 的数据在另一个 DB"这类报告。
> - 回答下游团队关于"会不会走网络调用？命中 cache？还是 4 RPS 突发？"的问题。
>
> **不在使用场景之内:**
> - 问题是关于 batch sync → 用 [`fund-batch-sync-playbook.md`](./fund-batch-sync-playbook.md)。
> - 问题是关于某个具体 provider 的怪癖 → 用 [`fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md)。
> - 问题是"怎么装 skill" → 用 [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md)。

---

## 60 秒答案（TL;DR）

`fund-data` 的基金查询走 **四层**，按顺序：

1. **入口** —— MCP tool、CLI 子命令或 Python import。
2. **Cloud bootstrap** —— `fund_cloud.ensure_project_bundle()` 决定要不要装 OSS query bundle，还是直接走本地 DB。
3. **DB 路径解析** —— `fund_data.default_db_path()` 把环境变量和 cache 折叠成一个具体的 SQLite 文件。
4. **Provider 链** —— `build_providers_full()` 挑活数据源，`run_provider_chain()` 执行，结果 upsert 回 DB。

每层职责单一，每层失败都不会级联，**最后那次持久化副作用**是唯一跨调用存活的状态。前面所有都是**无状态、可复现**的。

---

## 完整回答模板（用这个骨架）

当被问"fund-data 是怎么查基金的？"，按这个结构回答，**四段对应四层**。顺序重要，跟运行时调用顺序一致。

### 第 1 段 —— 入口

> 用户可以走三个入口：MCP stdio server（`fund_search` tool）、`fund-cli search` 子命令，或者直接 Python import `fund_data.search_funds`。三个最后都汇到同一个函数。MCP 路径是 OpenClaw / Codex / Claude Code agent 最常走的；它接受一个 `db` 参数，**省略时会触发自动 cloud bootstrap**。CLI 和 Python 路径让用户可以通过传 `db` / `db_path` 或设 `FUND_DATA_DB` 来跳过 bootstrap。

### 第 2 段 —— Cloud bootstrap

> 在任何数据调用之前，`fund_cloud.ensure_project_bundle()` 按顺序查四件事：`FUND_DATA_DB` 是不是已经设了（用本地，skip bootstrap）；`FUND_DATA_AUTO_PULL=0` 是不是（skip bootstrap，让 live provider 兜底）；`~/.cache/fund-data/` 里是不是已经有拉过的 bundle（复用，不走网络）；否则从 OSS bucket 下载 manifest，校验 `fund_data_query.sqlite.gz` 的 SHA-256，解压到 cache 里。**下载失败会返回一个结构化的 `fallback: "api"` 信号 —— 不会抛错**，这样 live provider 仍然有机会服务请求。

### 第 3 段 —— DB 路径解析

> bootstrap 决策拿到手后，`default_db_path()` 用一个窄优先级列表解析出唯一的一个 SQLite 文件：显式的 `FUND_DATA_DB`（带 cache override）、刚拉下来的 OSS bundle、`current.json` cache 指针，或者本机 fallback `fund-data/data/fund_data.sqlite`。这个文件就是后续读 coverage / export 的目标，也是持久化副作用写入的地方。

### 第 4 段 —— Provider 链

> `build_providers_full("auto", capability="search")` 接着编排活数据源列表。`auto` 模式下，付费 provider（`investoday` 如果设了 `INVESTODAY_API_KEY`，然后是 `tushare` 如果设了 `TUSHARE_TOKEN`）会放到队首；免费的 `[Eastmoney, AkShare]` 按 capability 特定顺序放到末尾 —— search / NAV refresh / snapshot / fund_list 走 Eastmoney 优先，profile / holdings / bonds / industries / fees / dividends / splits / managers 走 AkShare 优先。NAV 查询先读已解析出的 OSS/local `nav_history` 缓存，只有 missing/stale 或显式 refresh 时才进这条 provider 链。`run_provider_chain` 按顺序调每个 provider，把 `None` 和空结果当成失败（记到 `failures` 列表），**第一个返回非空 rows 的胜出**。成功的返回会带上 `failures` 路径，所以 agent 可以 audit 试过哪些 provider。如果所有 provider 都失败，链会抛 `ProviderError("all providers failed for search_funds: ...")`，message 里带完整路径。

### 第 5 段 —— 持久化（顺带提一下，不展开）

> Search 不是只读 —— 是读 + upsert。返回的 rows 写到 `funds` 表（按 `fund_code` 主键，`INSERT OR REPLACE`，整列覆盖），原始 provider payload 追加到 `raw_responses` 做 audit。这意味着重复 search 会让本地 DB 保持同步，但也会覆盖其他流程（比如 `refresh_fund_type`）从其他数据源填进来的列值。

---

## 12 个最常被问到的问题（含详细答案 + 为什么这么设计）

下面这些问题是在 onboarding、support、PR review 中最常出现的。**按这里出现的顺序回答，用同样的详细程度** —— 这些是团队经过多轮"但为什么？"之后沉淀下来的解释。

### Q1. 为什么 `search_funds` 总是走 live provider 链？为什么不先查本地 SQLite？

团队的考虑，权衡也讲明：

- **新鲜度优先。** Search 是一个发现动作；用户是在找 *这个* 基金，不是"我可能已经知道的任何基金"。上周刷过的本地 DB 可能会漏掉新基金（基金池平均每周增长 ~30 个），也可能显示基金合并后过时的名字。
- **本地是用来分析的，不是用来查的。** 本地 SQLite 是为 *查完之后* 的路径优化的 —— `coverage_report`、`fund_export table=funds`、你想 join 到 `nav_history` 的 search 结果。如果用本地做关键字搜索，会让查询质量跟 backfill 新鲜度耦合。
- **权衡是明写的。** `FundDataClient` 的 1 RPS 限流意味着 30 个关键字的批量要 30+ 秒。我们为了正确性赢接受了这点。批量查找走 `fetch_fund_list`（全量，一次调用）然后客户端 filter，或者用 `batch_sync` + 显式 codes。

### Q2. 为什么 cloud bootstrap 失败要静默，provider 链失败要响亮？

两种失败模式，两种失败策略 —— 有意为之。

- **Cloud bootstrap 是"尽力而为"的基础设施。** OSS bundle 的存在是为了让 agent 不用在首次安装时跑 21 小时的 AkShare backfill。如果 bucket 挂了，agent 也不会比 bundle 存在之前更糟 —— live provider 还能工作。抛异常会阻塞每次 tool 调用，bucket 一打嗝就全挂，agent 也无法恢复。Bootstrap 的 `fallback: "api"` 信号就是结构化的"你的 cache stale 了，但我还能服务你"消息。
- **Provider 链是数据契约。** 当 agent 要"110022 的 NAV 历史"时，链的工作就是交付。如果每个 provider 都失败，那是 *数据* 失败 —— agent 必须知道，不能静默拿到一个空列表。抛 `ProviderError` 是响亮信号；message 里有每个 provider 的路径，agent 可以 surface 出来。
- **不对称是有意为之。** 用户明确要求的事情要响亮失败；框架替用户做事要安静失败。

### Q3. 为什么 auto provider 链顺序会按 capability 变化？

因为每个 provider 在每个 capability 上有可测量的优势，团队测试过：

- **Eastmoney 对高频读数据更快更可靠：** fund list、fund search、NAV history、snapshot。不用 key，不用装 AkShare，1 RPS 但 ~0.36 s/fund。Tushare/Investoday 在这里也好，但我们有不设 key 的成本故事给 evaluator。
- **AkShare（以及它的结构化镜像 Tushare / Investoday）是更深层数据的唯一来源：** profile、holdings、bonds、industries、fees、dividends、splits、managers。Eastmoney 的公共 endpoint 大多数都不暴露这些（snapshot 来自 `pingzhongdata`，是不同的 endpoint）。把 AkShare 放到链首反映了：对于这些 capability，Eastmoney 是 fallback，不是主导。
- **顺序是 benchmark，不是信念。** 2025 年底当 Tushare 的 `fund_profile` endpoint 比 AkShare 的 `fund_overview_em` 更快时，auto 链把 Tushare 移到 AkShare 前面。顺序每个季度重测；要改顺序，看的文件是 `fund-data/AGENTS.md` "Eastmoney-only beats AkShare 8x" 那一节。

### Q4. 为什么 provider 链是"第一个非空胜出"，不是"按 completeness 评分"或"并发全调"？

- **第一个非空胜出是最便宜又正确的答案。** 主要 provider（便宜的四个用 Eastmoney，全都要看用 Investoday）是高信任源。如果 `InvestodayProvider.search_funds()` 返回 30 行，我们没有理由让 AkShare 再给一个"second opinion"。加 completeness 评分需要不存在的（也是主观的）逐行评分函数。
- **并发全调会烧限流。** 4 路并发 search 会让每次关键字的 HTTP 成本 ×4。agent 不是用 CPU 付钱，是用秒数（Eastmoney 在 ~8 in-flight 后开始节流）和钱（Investoday 是计量的）付钱。带早退的串行是正确的形状。
- **`failures` 列表是 audit 钩子。** 即使是成功返回，结果也带 failure 路径。想验证"AkShare 有没有更好的答案？"的 agent 可以看这个路径，然后显式用 `--provider akshare` 调一次。

### Q5. 为什么 `upsert_funds` 覆盖所有列？为什么不 merge？

- **`upsert` 是 search 结果需要的。** 当 provider 返回 `{fund_code, fund_name, fund_type, ...}`，调用方是在说"这是基金 X 的当前状态"。Merge 会让来自前一个数据源的过期数据留在行里。
- **代价有文档说明并绕过了。** 单独的流程 `refresh_fund_type` 从 Eastmoney 的 `fundcode_search.js`（*全量* 索引，不是 search 索引）填充 `fund_type`。Investoday provider 从 `/fund/all`（*catalog*，不是 search 结果）填充。两者都比 `search_funds` 返回的更丰富。因为 `upsert_funds` 覆盖，调用 `fetch_fund_list` 的 `list` 重建会*丢失*更好的值，你必须再跑一次 `refresh_fund_type`。`refresh_fund_type --only-empty` 标志加进来就是让重跑便宜。
- **DB schema 是契约，不是 merge 逻辑。** 如果我们要加 partial-update 模式，它会是 `FundDataStore` 上的一个新方法（比如 `upsert_fund_names_only`），不是 `upsert_funds` 里的行为变更。这保持文档契约稳定。

### Q6. 为什么 `FundDataClient` 把限流硬编码到 1 RPS？

- **经验上，上游在突发 ~2-3 RPS 时开始节流。** 1 RPS 是团队 backfill 测试中不产生 5xx 错误的安全水位（见 `fund-data/AGENTS.md` "AkShare is the throughput bottleneck" 一节）。限流是 `min_interval_seconds=1.0` 写在 client 上。
- **这是默认值，不是硬规则。** `fund-batch-sync` 和 `fund-batch_sync_funds` 接受 `--min-interval-seconds` 和 `--concurrency`；团队发现 `concurrency=8, min-interval=0.1` 是 Eastmoney 的甜蜜点。1 RPS 默认是给单次 `fund_search` 路径用的，这样一个循环调关键字的 agent 不会意外超过安全水位。
- **错了代价高。** Eastmoney 在突发时返回 5xx，没有能从"你被限流 10 分钟"恢复的重试预算。保守默认是对的。

### Q7. 为什么 `fund_cloud_status` 是一等公民 tool？

- **Agent 需要知道自己在跑哪个版本。** 一份说"基金 110022 有 NAV 行 X"的报告，如果不知道本地 DB 反映的是 2026-06-01 还是 2026-05-15 的 backfill，就不可执行。Manifest URL 是版本指针；`fund_cloud_status` 暴露本地 cache 版本 + 远端版本 + diff，让 agent 要么拉新 bundle，要么标记 staleness。
- **它是 bootstrap 的 audit 通道。** Bootstrap 返回 `source: cache|oss|api` 和 `skipped: ...`。Status tool 按需暴露同样信息。想验证"bootstrap 真的做了它应该做的事"的 agent 在第一次数据调用之后调这个 tool。

### Q8. 为什么 `install_skill.py --include-data` 会警告 `raw_responses`？

- **`raw_responses` 表存储完整的上游 HTTP 响应体。** 对于 Eastmoney 和 AkShare，这些 body 可以包含调用方 IP（在 `X-Forwarded-For` 或上游 proxy 加的其他 header 里）。表也包括响应的 `Content-Type` 和任何返回的 cookies。
- **当 snapshot 离开你的机器时，IP 泄漏。** 一个发到公共 OSS bucket、发给同事 laptop、或作为 CI artifact 的 `--include-data` 安装是一次 publish。`--scrub-raw-responses` 标志在 publish 前清空那个表；没有它，用户是在知情的情况下同意泄漏。
- **默认是更安全的行为。** 不带 `--include-data` 的 `install_skill` 根本不包含 SQLite 文件，问题不存在。标志是 opt-in，开启时 scrub 也是 opt-in —— 匹配团队对任何 publish 的"显式 > 隐式"规则。

### Q9. 为什么 `fetch_fund_list` 一次性拉全部 ~27k 基金，不按"类型"或"交易所"过滤？

- **上游没有干净的过滤。** Eastmoney `fundcode_search.js` 索引是一个 JS 对象，包含每个基金。AkShare 的 `fund_name_em()` 返回同样的 DataFrame。没有 `fund_name_em(exchange="SH")` 或 `fund_name_em(type="货币型")` 这种查询参数。Provider 要么返回全量，要么啥都不返回。
- **够快。** JS 文件 ~2.5 MB；DataFrame ~5 MB。代价是一次 HTTP 调用，~1-2 秒，不撞限流。客户端 filter 一下"给我 货币型 基金" ~50 ms。
- **它是 `fund_type` 的唯一来源。** `fund_type` 列是从这次拉取填的。没有它，coverage report 没法区分"空因为没数据"和"空因为用户 filter 了"。强制全量拉取让上面的 filter 层有意义。

### Q10. 为什么 `PROVIDER_AUTO` 存在，而不是选一个默认？

- **不同 agent 有不同的成本/可靠性预算。** 一周跑一次的研究团队批量 job 想要 Investoday 优先（付费、最快、有合同 SLA）。一个只跑一次的 evaluator 烟雾测试想要 Eastmoney-only（免费、没 key、没安装）。一个金融团队的生产 run 想要 Tushare（干净 JSON、稳定 schema）。硬编码任何一个都会打破另外两个。
- **Env var 是配置面。** 链组合在调用时读 `INVESTODAY_API_KEY` 和 `TUSHARE_TOKEN`，所以 agent 可以不改代码就翻转顺序。CLI 标志 `--provider investoday` 是一次性运行的显式 override。
- **`auto` 对 evaluator 来说是更安全的默认。** 它选 operator 配置的最高保真度源。强制 evaluator 设 key 才能跑 search 对项目的"没 key、没安装"评估故事太不友好。

### Q11. 为什么 search 不做"本地优先，miss 走远程"？那样更便宜。

- **它会把查询延迟跟本地 DB 新鲜度耦合。** Search 全部的意义就是当下。本地 DB miss 是"我们*上周*知道的没有匹配你的关键词"这个已知真信号，但不是一个"不存在"的信号。Live provider 会返回 5 行的场景里静默返回"无结果"是比 1 RPS 代价更糟糕的失败模式。
- **本地 DB 是用来 join 的，不是用来 gate 的。** 当 agent 已经有 fund code 时，可以对本地 DB 做便宜的 `coverage` 查询。当 agent 有 keyword 时，必须去上游。
- **代价不对称是错的。** 1 秒的 HTTP 调用就是 1 秒。"先查本地，再 fallback 远程"是 1 秒 + 同样的 1 秒，*加上* 在本地 DB 上建 search index 的代价。"快路径"实际上并不快。

### Q12. 为什么 `default_db_path()` 不缓存解析出来的路径？

- **每次 `FundDataStore()` 构造时都会重新解析。** 缓存它意味着在 `cloud_pull` 之后调 `search_funds` 的进程会针对旧路径构建。用户可见的行为变化会是"我拉了新 bundle，但我的下一次 search 还是写到旧 DB" —— 正是 bootstrap 设计要防止的失败模式。
- **代价可以忽略。** 函数读两个 env var，看一个文件，返回。我们测量的每个调用点是亚毫秒。缓存一个 100 微秒的函数来省 100 微秒是错的权衡。
- **契约是"每次都问"**，这意味着想切 DB（通过 `FUND_DATA_DB` 或 `cloud_pull` 拉新 bundle）的长跑 daemon 不需要清缓存。简单性是 feature。

---

## 设计哲学（为什么四层是这个形状）

读完这一节，剩下的 playbook 就显而易见了。

1. **管道形状由失败模式决定，不由成功模式决定。** Cloud bootstrap 允许静默失败，因为它的失败是可恢复的。Provider 链不允许静默失败，因为它的失败是用户可见的。DB 路径解析允许隐式失败（fallback 到默认），因为它的失败是"本地 DB 还不存在" —— 持久化步骤会创建它。层数就是不同失败策略的数量，不是不同步骤的数量。

2. **Stateful 之前先 Stateless。** 从入口到 provider 链执行全是纯的：相同输入 → 相同输出，没有副作用。第一个副作用是 `upsert_funds` 写，在最后。这个形状就是系统可 debug 的原因：当 agent 报告"search 返回了 X"，调查者可以用同样的 `keyword` 和 env vars 重放调用得到同样的 rows，*如果* 底层 provider 是确定性的（它们大多数是这样；Eastmoney 和 AkShare 是只读且幂等的）。

3. **通过 env vars 配置，不通过代码配置。** `FUND_DATA_DB`、`FUND_DATA_AUTO_PULL`、`FUND_DATA_MANIFEST_URL`、`FUND_DATA_CACHE_DIR`、`INVESTODAY_API_KEY`、`TUSHARE_TOKEN`、`FUND_DATA_DISABLE_AKSHARE`。每个改变行为的开关都是 env var。原因就是 agent 群体：长跑的 OpenClaw daemon 可以在调用之间翻转这些而不重启，CI runner 可以在不重 build image 的情况下为每个 step 设置它们。

4. **两个存储层是有意为之的。** `fund-data/data/fund_data.sqlite`（全 audit-log DB）保留 `raw_responses`、`sync_runs`、`sync_failures` 给需要重建或 audit 的 operator。`fund_data_query.sqlite.gz`（query-only bundle）剥掉它们让 publish 大小可控、operator IP 不泄漏。同样的数据形状，两种存储类型。Skill 安装默认（`--data-mode none`）两个都跳过，指向 OSS bundle；显式的 `--include-data` 标志 opt-in 到全 DB；私有的 `archive-full` 流程处理剩下的。

5. **Provider 链是成本阶梯，不是质量阶梯。** "第一个非空胜出"是当更高成本 provider 也是更高信任时的正确形状。如果便宜 provider 更高信任，链会反转。顺序是 benchmark；改之前看 AGENTS.md "Eastmoney-only beats AkShare 8x" 那条笔记。

6. **错误带 trail，不带 blame。** `ProviderError` 说"all providers failed for search_funds: eastmoney: ...; akshare: ..."。`ensure_project_bundle` 失败时返回 `fallback: "api"` 和 `error: "..."`。结构化 trail 就是 agent 自诊断需要的。约定是：失败响应是 success-shaped message 包含 `error` 或 `isError`，绝不是裸字符串。

---

## 反面教材（不要这么说）

这些是 PR review 和 support 线程里见过的常见错误回答。避开它们。

- **"Search 用 cache。"** 它不用。Search 用 live provider 链并写到 cache。Cache 是输出，不是输入。
- **"把 `FUND_DATA_DB` 设在快盘上。"** 那是 SQLite 调优建议，不是 `fund-data` 设计答案。`fund-data` 答案应该是"那个 DB 里有什么数据？多久刷一次？" —— 这才是让 `fund-data` 问题跟 SQLite 问题不同的东西。
- **"我们用 AkShare 做 fallback。"** 误导。AkShare 对 12 个 capability 中的 8 个是 primary，对另外 4 个是 fallback。永远说哪个 capability。
- **"看 provider 而定。"** 偷懒。链顺序有文档，引用 `build_providers_full` 和 `capability` 参数。
- **"查 `sync_failures` 表。"** 对 search 是错的。`sync_failures` 是 `batch-sync` 失败队列。Search 失败在 `raw_responses` audit log（payload 的 `failures` 键）和 `ProviderError` message 里。
- **"1 RPS 限流是因为礼貌。"** 它是 *正确性* —— 超了会触发上游 5xx，而且没有 graceful 重试预算。礼貌是副作用。

---

## 怎么保持这个剧本准确

剧本是团队 *settled* 的解释，不是 live 代码。代码变了，在同一个 PR 里更新剧本。检查项：

- `default_db_path()` 优先级变了（env vars / cache 顺序）→ 更新"60 秒答案"和第 3 段。
- `build_providers_full()` 路由变了 → 更新第 4 段和 Q3。
- 失败策略变了（响亮 ↔ 安静）→ 更新 Q2 和哲学部分。
- 新 env var 落地 → 加到第 3 段和 `fund-lookup-pipeline.md` 的 env var 决策表。
- 新 capability 落地 → 更新第 4 段的 capability 列表，检查 Q3 / Q5 是否还适用。

如果 PR 改了上面任何一项但没更新剧本，request changes 时指这一节。

---

## 相关文档

- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —— 图表 + 代码锚点 + env var 表。
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —— agent-facing skill manifest，加载到系统 prompt。
- [`../../fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md) —— contributor-facing 架构参考。
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —— backfill 配方、长跑陷阱、各 provider 性能数字。
- [`../../fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md) —— 怎么启用每个 provider、每个 provider 实际解锁什么、怎么注册新 provider。
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —— Codex / Claude / OpenClaw 的 per-platform install 布局。
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —— v0.3.0 backlog（没有 `--json` flag、没 HTTP/SSE MCP、没 progress 通知、没 `fund_doctor` MCP tool）。
