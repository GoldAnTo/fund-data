# Fund Batch Sync 剧本（Playbook）

> **最后更新:** 2026-06-02
> **目标读者:** 任何人 —— 人或 AI —— 被问到"fund-data 是怎么同步一组基金的？"、"为什么 backfill 这么慢？"、"为什么失败？"、"为什么数据写到错的 DB 了？"。这是 **长跑管道的回答脚本**。配套 [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md)（图表 + 代码锚点）一起看。
>
> **使用场景:**
> - onboarding 新 contributor 或 agent 进入数据平面。
> - 审查涉及 `fund_data.sync_fund`、`fund_data.batch_sync_funds`、`scripts/backfill.py` 或任何 provider 的 per-fund fetch 的 PR。
> - 排查"backfill 失败"或"数据在错的 DB 里"或"夜间 cron 卡在 0% CPU"这类报告。
> - 回答"该用 backfill runner、direct `batch_sync_funds`、还是 one-off `fund_sync`"。
> - 估算新数据集组合的运行时长。
>
> **不在使用场景之内:**
> - 问题是关于单次 search → 用 [`fund-search-playbook.md`](./fund-search-playbook.md)。
> - 问题是关于某个具体 provider 的怪癖 → 用 [`fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md)。
> - 问题是"怎么装 skill" → 用 [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md)。

---

## 90 秒答案（TL;DR）

`fund-data` 的 batch sync 是 **跟单次 search 一样的四层，再加一个长跑 runner**，负责 state、batch 边界和 lock retry：

1. **入口** —— `fund_batch_sync` MCP tool、`fund-backfill` CLI，或者直接 Python import `batch_sync_funds` / `backfill`。
2. **Backfill runner**（可选）—— `backfill.py` 加 `fund_type` 过滤、state 持久化（`backfill_state.json`）、batch 分组和 "database is locked" 重试。
3. **Cloud bootstrap** —— 跟 search 一样：`ensure_project_bundle` 决定要不要装 OSS query bundle。
4. **DB 路径解析** —— 跟 search 一样：`default_db_path()` 把 env vars 和 cache 折叠成一个具体文件。
5. **Batch scheduler** —— `batch_sync_funds` 并行跑 N 个 `sync_fund` 调用（`concurrency=1` 时串行）。
6. **Per-fund pipeline** —— `sync_fund` 走 capability 阶梯：snapshot → profile → fund row → NAV → 可选数据集（holdings、bonds、industries、fees、distributions、managers），然后写 `sync_runs` audit 行。
7. **Provider 链（每个 capability）** —— 跟 search 一样的形状；每个 capability 选自己的链。

跟 search 的 **关键区别** 是：(a) `sync_fund` 有 **两层失败策略** —— snapshot 和 NAV 是硬失败，可选数据集是软失败；(b) `backfill.py` 写一个 JSON state 文件，跨进程死亡也能存活；(c) `ThreadPoolExecutor` 受 `concurrency` 限制，并由线程安全的 `_RateLimiter` 限流。

---

## 完整回答模板（用这个骨架）

当被问到"fund-data 是怎么做 batch sync 的？"，按这个结构回答，**六段对应六层**。顺序重要 —— 跟运行时调用顺序一致。

### 第 1 段 —— 入口

> 用户可以走三个入口：MCP stdio server（`fund_batch_sync` 和 `fund_sync` tool）、`fund-batch-sync` / `fund-backfill` CLI 子命令，或者直接 Python import `fund_data.batch_sync_funds` 或 `fund_data.backfill`。MCP 路径触发 cloud bootstrap；backfill CLI 绕过它（operator 应该已经设了 `FUND_DATA_DB`）。`fund-backfill` 和 `fund_data.backfill` 是同一个函数 —— backfill runner 是一个薄包装，在 `batch_sync_funds` 周围加 state、fund_type 过滤和 lock retry。

### 第 2 段 —— Backfill runner（仅 `backfill.py` 路径）

> 当入口是 `backfill.py` 时，runner 首先读 `backfill_state.json` 知道哪些 fund code 已经完成，然后从本地 SQLite 加载完整 fund list，按 `--include-type` / `--exclude-type` 过滤，按 include-flag signature 分组剩余 codes（货币型基金跳过可选数据集，混合型不跳）。每组被切成 `batch_size` 的块，每块传给 `batch_sync_funds`，外面包一层 `LOCK_RETRY_ATTEMPTS=3` 重试。State 文件在每个成功的 batch 之后更新，所以 batch 7 崩溃了，下次从 batch 8 继续。`--reset` 丢掉 state 从头开始跑。

### 第 3 段 —— Cloud bootstrap 和 DB 路径解析

> `batch_sync_funds`（和 MCP 路径）然后跑跟 search 一样的 `ensure_project_bundle` → `default_db_path` 序列。成功的 bootstrap 返回 OSS 或 cache DB；失败的 bootstrap fallback 到本地 `fund-data/data/fund_data.sqlite`。**Bootstrap 在失败时是静默的** —— live provider 仍然有机会服务 —— 但是一个长跑 backfill 不小心写到 cache DB 而不是本机 DB，会跟 `doctor.py` 的报告有分歧。Backfill 运行时**总是显式设 `FUND_DATA_DB`**。

### 第 4 段 —— Batch scheduler

> DB 路径解析之后，`batch_sync_funds` 跑 per-fund pipeline。`concurrency=1` 是串行循环；`concurrency>1` 是 `ThreadPoolExecutor` 用 `as_completed` 收集结果。`stop_on_error` 在第一个硬失败时短路剩下的 batch（很少用；backfill 偏好继续 + 重试）。`min_interval_seconds` 默认 concurrent 时 `0.25`、serial 时 `1.0` —— 这些是团队测量中不产生 5xx 错误的限流预算。

### 第 5 段 —— Per-fund pipeline

> `sync_fund` 走 capability 阶梯。两个 **硬失败** 步骤是 `fetch_snapshot` 和 `fetch_nav_history` —— 任一抛错，fund 移到 `failed_codes`，剩下的数据集不再请求。七个 **软失败** 步骤是 `fetch_profile`、`fetch_*holdings`、`fetch_industry_allocations`、`fetch_fee_structures`、`fetch_dividends`、`fetch_splits` 和 `fetch_fund_managers` —— 任一硬错误变成 `dataset_errors` 条目，fund 仍然得到 `status: "ok"`。返回空 snapshot（后端 share class，比如 `000002`）是软跳过，不是失败。Fund row 在 profile fetch 之后 upsert（所以 `fund_name` / `fund_type` / `company` / `manager` 来自 profile payload，不是 snapshot）。

### 第 6 段 —— 持久化和 state

> 每个 `fetch_*` 调用 upsert 到目标表（按 `fund_code` 或 `(fund_code, report_period, ...)` 主键），把原始 provider payload 追加到 `raw_responses`。`sync_fund` 写一行到 `sync_runs`，带 per-fund 结果。硬失败额外写一行到 `sync_failures` —— 这是 `retry_failures.py` 的活队列。Backfill runner 另外把 fund code 追加到 `backfill_state.failed_codes` —— 这是 resume 用的快照。**两个失败追踪会有分歧**，如果 `backfill.py` 和 `retry_failures.py` 交错跑；agent 必须读两个才能看到完整画面。

---

## 14 个最常被问到的问题（含详细答案 + 为什么这么设计）

下面这些问题是在 onboarding、support、PR review 中最常出现的。**按这里出现的顺序回答，用同样的详细程度** —— 这些是团队经过多轮"但为什么？"之后沉淀下来的解释。

### Q1. 为什么 backfill 写 `backfill_state.json`，而 `batch_sync_funds` 不写？

- **`backfill.py` 是为了"完成所有 27k 基金"这种跑。** 这种跑要 6-21 小时。没有 state 文件，进程在 fund 26000 崩溃意味着重启时重做 0-25999。有了 state 文件，重启读 `completed_codes` 然后跳过它们。
- **`batch_sync_funds` 是为了"同步这 N 个特定基金"的一次性调用。** 它给 watchlist / follow-up / on-demand pull 这种场景用，N 小到失败重做比维护 state 便宜。100 个基金的 pull 在 fund 80 崩溃了，重做要 ~2 分钟；state 文件复杂度大于价值。
- **这种分割是有意的，不是 TODO。** 想给 100-fund pull 加上 state-managed 行为，自己外面包循环并持久化自己的 state。`backfill_state.json` 的形状就是参考。

### Q2. 为什么 snapshot 和 NAV 是硬失败，profile / holdings 是软失败？

- **Snapshot 和 NAV 是数据锚点。** 每个其他表都 join 回其中一个。没有 snapshot 的 `funds` 行只是个名字；没有它所属基金的 `stock_holdings` 行是垃圾。如果我们软失败 snapshot，我们仍然会写 fund 行和 holdings 行，下游查询要在每个地方 filter 掉"没 snapshot 的基金"。硬失败是显式的"我们没有这个基金的数据锚点"信号。
- **Profile / holdings / fees / distributions 是 enrichment。** 有 `status: "ok"` + 空 `dataset_errors` profile 但有 holdings 的基金是可用的：你仍然能查到基金、拿到 NAV 历史、拿到 holdings，跳过 profile。有 profile 但空 holdings 也可用：用户拿到描述和费率。这种不对称匹配用户面对的问题形状。
- **`dataset_errors` 通道是 audit trail。** 部分成功被报告为 `status: "ok"` + `len(dataset_errors) > 0`。想看哪些基金有哪些缺口的 agent 可以看这个通道然后行动（用不同 provider 重跑，或接受缺口）。

### Q3. 为什么 backfill 默认对 `货币型` 基金跳过可选数据集？

- **货币型基金按监管设计就没有 stock / bond / industry holdings。** 它们是货币市场基金。AkShare 和 Eastmoney 都为这些 endpoint 返回 `[]`，外加一个 `dataset_errors` 行，那个行要花一个限流 slot。
- **975 货币型基金 × 5 个空数据集 × 每次调用 2-3 秒 = 2.5-4 小时浪费的限流预算。** 在 21 小时 backfill 上，那占总时间的 12-20%，调用全空。
- **`--no-skip-currency` 是你信任不同 provider 填补空白的场景。**（比如付费 Investoday L2 endpoint 返回 货币型 dividend 历史）。默认是"便宜"；override 是"穷尽"。

### Q4. 为什么 `--provider eastmoney --concurrency 8` 是最快路径？为什么 `--provider akshare --concurrency 8` 不行？

- **Eastmoney 上的 `fetch_nav_history` 是 0.36 s/fund。** AkShare 上是 > 6 s/fund。16× 差异是 **上游节流行为，不是每次调用成本** —— 团队的测量在 16-way 突发时撞到了 AkShare 节流，server 开始返回 429 和 5xx。Eastmoney 的上游是不同的 load-balancer，节流更宽松。
- **正确的并发数由上游决定，不由团队硬件决定。** 超过 ~8 in-flight AkShare 调用，吞吐 *下降*，因为 client 比起省下并行，更多时间在等 5xx 重试。
- **推荐的分割是两轮 backfill：** `backfill --provider eastmoney` 跑 snapshot + NAV（~90 分钟），然后 `backfill --provider tushare`（带 token）跑 AkShare-only capability（~3-4 小时）。第一轮快是因为 Eastmoney 是正确的工具；第二轮快是因为 Tushare 是正确的工具。

### Q5. 为什么 lock retry 3 次后就放弃？

- **Lock retry 是为了"另一个 writer 正在收尾它的 WAL commit，等一下"。** 2-3 秒通常够。4-8 秒覆盖尾巴。超过这个，锁持有者卡在真正的问题上（死锁、网络分区、挂起的子进程），等更久让情况更糟，不是更好。
- **放弃是对的策略，因为 backfill state 文件已经更新了。** 下次调用读 `backfill_state.json`，看到不完整的 batch，重新处理这些 code。快速放弃暴露失败，operator 可以诊断。
- **9 秒的等待比重跑 6 小时 backfill 便宜** —— 这是 *底线*。3 次尝试 2/4/8 秒 = 最多 14 秒。如果 14 秒还没完，锁被某个不会在合理时间内释放的东西持有着。

### Q6. 为什么 `batch-size 100` 比 `batch-size 500` 安全？

- **State 文件在每个 batch 之后更新。** 一个 200-fund batch 跑 5-8 分钟；如果它在 fund 199 失败，state 文件看不到 199 个成功的任何一个。100-fund batch 跑 2-4 分钟；最坏情况丢掉 100 个基金的工作。
- **失败域是整个 batch。** 瞬时 HTTP 闪断、SQLite 锁、OOM kill —— 任一个都带走整个 batch。更小的 batch意味着更小的失败域。
- **团队测得 100-fund batch 成功率 ~96%，500-fund batch ~88%**（差距不只是丢掉的时间，是需要重跑的丢掉行）。默认 100 是校准过的数字，不是随便选的。

### Q7. 为什么 `default_db_path()` 优先 OSS cache，为什么跟 `doctor.py` 冲突？

- **`default_db_path()` 是 agent-friendly 路径。** 一个拉过 OSS bundle 的 OpenClaw daemon 想从那个 bundle 读。把 cache 放到本机 DB 之前的优先级列表意味着 daemon 不用想它在打哪个 DB。
- **`doctor.py` 是 operator-friendly 路径。** Operator 想知道"生产 DB 健康吗？"，那是本机 DB，不是碰巧拉过来的 cache。
- **两个有意分开** —— `doctor.py` 不走 cache，cache 不被认为是生产。一个用 `default_db_path()` 的长跑 backfill 会写到优先级里胜出的那个 DB；`doctor.py` 报告它知道的那个。如果它们分歧，backfill 在写 cache，`doctor.py` 在报告本机 DB。
- **修法是给任何想落到生产的 backfill 显式设 `FUND_DATA_DB`。** CI workflow 这么干；没那个 var 的本地 CLI run 落到 cache。权衡在 `fund-data/AGENTS.md` §Long-running pitfalls 有文档。

### Q8. 为什么 `--include-type` / `--exclude-type` 是子串匹配，不是精确匹配？

- **Fund type 字符串是分层的。** 一行可能是 `指数型-股票` 或 `指数型-固收`。团队想让 `--exclude-type 货币` 匹配 `货币型` 和 `指数型-货币`，不用分别列。子串匹配是用单 flag 做到这点的唯一方式。
- **代价是 false positive。** `--include-type 股票` 匹配 `股票型`、`指数型-股票` 和 `股票指数`。团队判断一个 flag 一个类别的便利性超过捕获过多的风险 —— false positive 容易在日志里发现。
- **备选是 list-of-patterns API。** 团队没上它，因为子串匹配是 80/20 设计。

### Q9. 为什么 `refresh_fund_type` 绕开 `upsert_funds` 用 direct SQL？

- **`upsert_funds` 是整行替换。** 当 AkShare 的 `fund_name_em()` 返回带空 `fund_type` 列的行，`upsert_funds` 把空字符串写到 `fund_type`，覆盖之前数据源（比如 Eastmoney `fundcode_search` 索引）填的值。空值卡在那里，`fund_type` filter 挂了，operator 收到 page。
- **支撑 search 的同一个索引对每个基金都带更好的 `fund_type`。** 拉那个索引，用 `UPDATE funds SET fund_type = ? WHERE fund_code = ?` 写 `fund_type` 列，是外科手术式的修法。
- **Eastmoney 自己也给空 `fund_type` 的 18 个基金**（2024-2025 新基金）做 regex fallback，从中文 `fund_name` 推断类型。Fallback 是单独的 pass，带自己的 `sync_runs` audit 行。

### Q10. 为什么有两套失败追踪（`backfill_state.failed_codes` 和 `sync_failures`）？

- **State 文件是快照；表是活的。** `backfill_state.failed_codes` 是 `backfill.py` 在每个 batch 结束时写的。它是 resume marker。如果进程在 batch 之间崩溃，state 文件跟实际完成的一致。
- **`sync_failures` 是 `record_sync_failure` 在 `batch_sync_funds` 里写的。** 它是 `retry_failures.py` 的活队列。每个硬失败都落到这里，即使 `backfill.py` 之后又把它拷到 state 文件。
- **当 `backfill.py` 和 `retry_failures.py` 交错跑时，两者会分歧。** `retry_failures.py` 成功的 retry 不写 state 文件；resume 时的 backfill 看到 code 在 `failed_codes` 里会重新失败。分歧小（下一次 `backfill` 调用重新记失败），但是真的。
- **团队跟踪这个作为已知的 ops 陷阱，不是 bug。** 未来的修法是让 `backfill.py` 直接读 `sync_failures` 而不是它自己的 state 字段，去掉重复。在这之前，operator 必须知道两个都存在。

### Q11. 为什么 backfill 的 `_resolve_include_flags` 按 flag 集合分组基金？

- **同类型的基金共享一个 flag 集合。** 所有 `货币型` 基金应该跳过可选数据集；所有 `混合型` 基金应该请求所有。按时 flag 集合分组意味着同样的 flag dict 传给 group 里每个基金，让 `batch_sync_funds` 里 per-fund 工作相同。
- **优化小但免费。** 分组是 O(N) over fund list；per-fund fetch 不变。赢是日志输出更干净（每个 group 一份 batch 报告，而不是每个 fund）。
- **分组是代码可读性的赢，不是性能的赢。** 未来的重构可以把 flag 解析移到 `sync_fund` 里，运行时不变。当前的分组是读日志的 operator 的便利层。

### Q12. 为什么 macOS proxy / IPv6 坑要在 Python 层 patch，不用 env vars 或系统配置？

- **macOS 有三层 proxy，它们由不同机制控制。** 第一层（env vars `http_proxy` / `https_proxy`）容易清。第二层（macOS system proxy via `scutil --proxy`）影响每个进程。第三层（第三方 app 像 Clash Verge 监听 7897）通过 launchd env 注入 app spawn 的每个进程。
- **只用 env var 的修法（`env -u https_proxy`）只清第一层。** 第二和第三层没动。修法必须活在 Python runtime。
- **`urllib.request.getproxies = lambda: {}` monkey-patch 所有 `urllib`-based client 查的那个函数。** 这一招覆盖第一 + 第二层（第二层流过同一个 `getproxies` 函数）。第三层也被同一招处理，因为第三层最终注入 env vars，那些也流过 `getproxies`。
- **IPv6 修法同理。** `socket.getaddrinfo` 是 Python 层所有 `socket`-based client 用的函数；patch 它在 import 项目之前 drop 掉 IPv6 候选，绕过 happy-eyeballs 死锁，不动系统配置。
- **两招都在 `fund-data/AGENTS.md` §Long-running pitfalls 有文档。** 它们不是 `fund-data` bug 的 workaround；它们是 macOS 特定行为的 workaround，没有 Python 库能从外面修。

### Q13. 为什么 1 小时 OSS cache TTL 是 nightly backfill 的坑？

- **Cache TTL 故意。** 它意味着一个拉了 bundle 的 daemon 不会每次调用都重击 OSS。一个需要最新数据的 nightly backfill 要么（a）等最多 1 小时让 cache 刷新，要么（b）显式重拉。
- **夜间 CI workflow 先调 `fund_cli cloud pull`**，重新检查 manifest URL，版本变了就拉。如果版本没变，用本地 cache，backfill 从那里读。
- **正确节奏是：**夜间 cron → `cloud build-bundle`（发布最新 query DB）→ `cloud upload`（写 manifest）→ 消费者 `cloud pull`（拿起新 manifest）→ 消费者 `backfill`。每步有自己的失败模式，独立日志。少了任何一步是"backfill 跑了但用了昨天数据"最常见的原因。

### Q14. 为什么 `batch_sync_funds` 没有 `--dry-run` 标志？

- **`--max-funds N` 是便宜的 dry run。** 它限制 fund count，跑完整 pipeline。烟雾测试是 `--max-funds 5 --concurrency 1 --include-all`，~30 秒跑完，每个 code path 除了长尾都走过。
- **真正的 `--dry-run` 必须预测 per-fund 数据集展开**（哪个基金会为 `fees` 返回空？哪个会撞限流？），那个预测要求实际跑 fetch。最便宜忠实的 dry-run 就是 `--max-funds` 干的事。
- **团队考虑过"报告要 fetch 什么"模式**，会走 `funds` 表打印每个 code 的 include-flag 集合。被 reject 掉因为跟 `coverage_report` 重复 —— agent 可以读 coverage report 和 `funds.fund_type` 字段推断形状。

---

## 设计哲学（为什么七层是这个形状）

读完这一节，剩下的 playbook 就显而易见了。

1. **管道形状由失败模式决定，不由成功模式决定。** Snapshot 和 NAV 是硬失败，因为它们是数据锚点。Profile 和 holdings 是软失败，因为它们是 enrichment。两层分类是响亮/安静的边界，边界由用户能否用部分数据决定。
2. **State 是为长跑设的，不是为短跑设的。** 100-fund pull 不需要 state 文件；27k-fund pull 需要。`batch_sync_funds`（没 state）和 `backfill.py`（有 state）之间的分割是有意的。如果需要，`batch_sync_funds --state-file` 标志是单行添加。
3. **两层失败策略是契约。** 消费 `sync_fund` 结果的 agent 知道 `status: "ok"` + 空 `dataset_errors` 是完全成功，`status: "ok"` + 非空 `dataset_errors` 是部分成功，`status: "error"` 是硬失败。`dataset_errors` 列表是 audit 钩子。
4. **Provider 链顺序是 benchmark，不是信念。** Eastmoney 优先便宜的四个，AkShare 优先深的八个，付费 provider 设了 key 就前置 —— 团队每个季度重测。顺序在 `build_providers_full` 里，是改 provider 速度时唯一要碰的文件。
5. **两个存储层是有意为之。** 完整 audit-log DB（`fund-data/data/fund_data.sqlite`）保留 `raw_responses`、`sync_runs`、`sync_failures`；query-only bundle（`fund_data_query.sqlite.gz`）剥掉它们。`default_db_path()` 优先级列表在拉过的时候优先 cache；本机 DB 是 `doctor.py` 基准。两者有意分开，想落到本机 DB 的长跑 backfill 必须显式设 `FUND_DATA_DB`。
6. **Idempotency 是默认。** 每个 `upsert_*` 按主键；每个 `fetch_*` 在上游是只读的。在同一个 code 上 re-run 同一个 `sync_fund` 是 no-op。Re-run 同一个 `backfill` 是安全的；让 resume 便宜的是 state 文件。这为什么团队对"重试同一个调用"作为"如果它半路失败怎么办"的答案感到舒服。
7. **通过 env vars 配置，不通过代码配置。** 每个改变行为的旋钮是 env var：`FUND_DATA_DB`、`FUND_DATA_AUTO_PULL`、`FUND_DATA_MANIFEST_URL`、`FUND_DATA_CACHE_DIR`、`INVESTODAY_API_KEY`、`TUSHARE_TOKEN`、`FUND_DATA_DISABLE_AKSHARE`。长跑 daemon 在调用之间翻转这些不重启；CI runner per-step 设它们。
8. **错误带 trail，不带 blame。** `ProviderError` 说"all providers failed for sync_fund: eastmoney: ...; akshare: ..."。`sync_failures` 记录 per-fund 失败 message。`sync_fund` 结果里的 `dataset_errors` 列表带 per-dataset trail。`sync_runs` 表带 per-call audit 行。Trail 就是 agent 自诊断需要的；约定是"失败响应是 success-shaped message 包含 `error` 或 `isError`，绝不是裸字符串"。

---

## 反面教材（不要这么说）

这些是 PR review 和 support 线程里见过的常见错误回答。避开它们。

- **"就在 `fund_sync` 循环里调。"** 那是单 fund 入口。整个 batch 系统存在就是为了避免循环。循环是错的形状因为每次调用 spawn 完整 bootstrap。
- **"Backfill 永远要 21 小时。"** 在 AkShare 路径上要 21 小时。Eastmoney + concurrency 8 是 ~90 分钟跑 snapshot + NAV。运行时是 provider 选择的函数，不是系统常量。
- **"它写到本地 DB。"** 哪个本地 DB？本机 `fund-data/data/fund_data.sqlite` 还是 `~/.cache/fund-data/releases/<version>/fund_data_query.sqlite` 那个 bootstrap 刚拉的？`default_db_path()` 决定。设 `FUND_DATA_DB` 如果想要本机那个。
- **"盲目重试 380 个 snapshot 失败。"** 它们是 `eastmoney: fund code must contain 6 digits: ''` + `akshare: 'AkshareProvider' object has no attribute 'snapshot'`。两个都会永远失败。加 `AkshareProvider.snapshot` 的 PR 是修法；重试是噪声。
- **"Backfill 最好夜里跑。"** Backfill 是 operator 安排的任何时间。夜间 CI workflow 在 02:00 UTC 跑是因为那是 OSS bundle 发布的时候。On-demand pull 不受这个时间表约束。
- **"用 `akshare` 跑全 backfill。"** AkShare 当前对全覆盖不可用。便宜的四个用 Eastmoney，深的八个用 Tushare/Investoday。
- **"macOS proxy 是 `fund-data` bug。"** 是 macOS 怪癖。三层 proxy（env vars、`scutil --proxy`、第三方 app）就是 macOS 的工作方式。Python 层 patch 是唯一不影响用户日常网络的可移植修法。
- **"`backfill_state.failed_codes` 是失败队列。"** 是快照。活队列是 `sync_failures`。两者会分歧。两个都读。
- **"就 `refresh_fund_type` 跑一下修 `fund_type`。"** 它修 99.93% 的行。那 18 个空 `fund_type` 的基金是 2024-2025 新基金，Eastmoney 索引还没给它们定型。再跑一次 `refresh_fund_type --only-empty` 没用；fallback 是 regex on `fund_name`。

---

## 怎么保持这个剧本准确

剧本是团队 *settled* 的解释，不是 live 代码。代码变了，在同一个 PR 里更新剧本。检查项：

- `backfill.py` 默认变了（`DEFAULT_CONCURRENCY`、`DEFAULT_BATCH_SIZE`、`LOCK_RETRY_ATTEMPTS`）→ 更新第 6 段 run profile 和 Q4/Q5/Q6。
- `_resolve_include_flags` 变了 → 更新 Q3 和 Q11。
- `sync_fund` capability 阶梯变了（新 fetch 加了或重排了）→ 更新第 5 段和 Q2。
- 任一 capability 的硬失败/软失败分类变了 → 更新第 5 段和 Q2。
- 任一 capability 的 provider 链顺序变了 → 更新 Q4 和哲学部分。
- 新 env var 落地影响 batch sync（比如新 `--proxy-bypass` 旋钮）→ 加到第 3 段和 `fund-batch-sync-pipeline.md` 的 env var 决策表。
- 新失败追踪加进来 → 更新 Q10 和哲学部分。

如果 PR 改了上面任何一项但没更新剧本，request changes 时指这一节。

---

## 相关文档

- [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) —— 图表 + 代码锚点 + env var 表。
- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —— 单次 search 的 reference（如果你还没读 search playbook，从这里开始）。
- [`fund-search-playbook.md`](./fund-search-playbook.md) —— 单次 search 的回答脚本。
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —— agent-facing skill manifest。
- [`../../fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md) —— contributor-facing 架构参考。
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —— backfill 配方、长跑陷阱（macOS proxy / IPv6 / `default_db_path` 分歧 / lock retry），还有给链顺序撑腰的 per-provider 性能数字。
- [`../../fund-data/PROVIDERS.md`](../../fund-data/PROVIDERS.md) —— 怎么启用每个 provider、每个 provider 实际解锁什么、注册新 provider 的配方。
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —— Codex / Claude / OpenClaw 的 per-platform install 布局。
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —— v0.3.0 backlog（没 `--json` flag、没 HTTP/SSE MCP、没 progress 通知、没 `fund_doctor` MCP tool）。
- [`../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md`](../superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md) —— 撑起 Q2 两层失败策略和 380-snapshot-failure audit 的 per-table / per-fund_type 覆盖诊断。
