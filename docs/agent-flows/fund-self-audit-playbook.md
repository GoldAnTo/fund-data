# Fund Self-Audit Playbook

> **给谁看：** 人 + 中文 agent。回答"**fund-data 怎么自查**？"、
> "**怎么在不动 provider 的情况下找出本地数据缺什么**？"。
> 配套 reference 见 [`fund-self-audit-pipeline.md`](./fund-self-audit-pipeline.md)（pipeline 图 + 代码锚点 + env 表）。

## 这是什么

self-audit 是 `fund-data` 的**项目级内省层**。它干一件事：

> 扫一遍本地 SQLite base，按优先级排好"哪些 fund / dataset
> 真的需要补 / 刷"，告诉你**先补哪个**。

它和 `coverage_report`（**进度仪表**）和 `doctor`（**环境健康**）
是三个不同维度的内省层：

| 工具 | 问的问题 | 写不写 DB | 调不调 provider |
|---|---|---|---|
| `coverage_report` | 现在每只基金每个 dataset 的覆盖率多少？ | 不写 | 不调 |
| `doctor` | 这个环境跑得动吗？cloud cache 装了吗？ | 不写 | 不调 |
| **`self-audit`** | **现在最该补哪只基金的哪个 dataset？** | **不写** | **不调** |

`self-audit` 也不会**自动去补**。它只发"建议"——`recommended_cli`
和 `recommended_mcp_tool` 都是给**人/agent** 看的，不是给 self-audit
自己跑的。**没有自动 batch-sync**，没有 GitHub Actions workflow
去消费这个 queue，**没有 provider 调用**。这都是显式 non-goal。

## 怎么跑

CLI：

```bash
# 全局排队，top 100
python fund-data/scripts/fund_cli.py self-audit --limit 100

# 单只基金体检
python fund-data/scripts/fund_cli.py health-check 110022

# 按 fund_type 过滤
python fund-data/scripts/fund_cli.py self-audit --fund-type 股票型 --limit 200

# 看 watchlist
python fund-data/scripts/fund_cli.py self-audit --codes-file watchlist.txt --include-structural

# 落到文件
python fund-data/scripts/fund_cli.py self-audit --limit 100 --output data/self_audit_queue.json
```

MCP：

```json
{
  "name": "fund_self_audit",
  "arguments": {"limit": 100, "max_age_hours": 36, "include_structural": false}
}
```

```json
{
  "name": "fund_health_check",
  "arguments": {"code": "110022"}
}
```

Python：

```python
import fund_data
result = fund_data.build_self_audit_queue(limit=100)
result = fund_data.check_fund_health("110022")
```

## 优先级怎么读

P1 > P2 > P3 > P4。**先 P1，再 P2，再 P3，P4 默认不当回事**。

| 优先级 | 含义 | 例子 | agent 该干啥 |
|---|---|---|---|
| P1 | 核心答案路径断 | 缺 `fund_profiles` / `nav_history` / `snapshots` | **立刻补**——agent 的"110022 的净值多少"答不上来都是 P1 |
| P2 | 重要研究数据缺 | 缺 `stock_holdings` / `bond_holdings` / `industry_allocations` / `fee_structures` | **排 batch-sync**——P2 同 dataset 的合一个 `batch-sync` 跑 |
| P3 | 有但过期 | `fetched_at` 超过 `--max-age-hours`（默认 36h） | **看场景**——如果是给"今天 110022 净值"的回答，stale 就得刷；如果只是研究，stale 也能用 |
| P4 | 自然稀疏 / 结构性空 | 货币型没有 stock_holdings、QDII 没有 industry_allocations、`dividends` / `splits` | **默认忽略**——除非用户显式说"我要看 货币型的 stock_holdings 为 0 是不是真的" |

## 12 个 FAQ

### Q1. 跑 self-audit 会调网络吗？

不会。`build_self_audit_queue` 和 `check_fund_health` 都只
`select distinct fund_code from <table>`，然后在 Python 侧分类。
**不调 provider，不写 DB**。`auto_fill_executed` 永远是
`false`——这一字段是 spec 里硬要求的契约，让你和 agent
看见它时立即知道"这只是个报告，不是动作"。

### Q2. 跑 self-audit 会不会很慢？

不会。核心是 9 个表各一次 `select distinct fund_code`（实际
用一个 `sqlite3.Connection` 复用 + dict 缓存），然后在内存里
分类 + 排序。26,953 只基金的本机测试大约 0.5 秒。

### Q3. P0 是什么？为什么我的 queue_size 有 67097 但 p0 = 0？

P0 是"**不能识别这只基金**"——比如 `funds` 表里没有这只代码，
或者 DB 连 core tables 都没有（schema 都没建）。生产环境的
self-audit 应该是 `p0 = 0`，因为 `funds` 表是 backfill 的
第一步必有。如果你看到 `p0 > 0`，那是 DB 状态坏了，先查
`doctor` 再说。

### Q4. 为什么 stock_holdings 总是 49%？

因为**结构性空**——货币型 / 债券型 / 指数型-固收 / FOF / REITs
等都不该有 stock_holdings。self-audit 用
`EXPECTED_EMPTY` 矩阵把它们的 stock_holdings 标成 P4/info，
**不计入 actionable missing**。所以你看到 P2 的
stock_holdings 缺失才是真要补的。

QDII 的 industry_allocations 也是 structural_empty。
REITs 的 stock + bond + industry 全部 structural_empty。
（这是中国公募基金监管决定的，**不是 bug**。）

### Q5. self-audit 跟 coverage_report 啥区别？

| 维度 | coverage_report | self-audit |
|---|---|---|
| 关注点 | **现在每个 dataset 多少 %**（进度仪表） | **接下来该补哪个**（行动队列） |
| 颗粒度 | 整个 universe + per-fund | per-(fund, dataset) |
| 输出 | `completeness`, `missing`, `actionable_missing` | `priority`, `score`, `recommended_cli` |
| 排序 | completeness asc（最缺的排前） | score desc（最该补的排前） |
| 用途 | 写 PR 描述的"修了 49% → 80%" | 给 agent 喂"先补这 100 只 P1 profile" |

`coverage_report` 是看**整体**的，`self-audit` 是**下一步
动作清单**。两者都从同一份 `funds` + 9 个 dataset 表读
SQLite，但**不互相依赖**——coverage_report 跑不跑都不影响
self-audit。

### Q6. queue 里的 `recommended_cli` 能直接复制粘贴跑吗？

能。所有 `recommended_cli` 都是 `fund-data/scripts/fund_cli.py
<subcmd> <code> --provider auto` 格式，对应**真实存在**的
subcommand。`fund_data.self_audit.DATASET_RULES['cli']` 字段
是显式存的（spec 里只让我用 `replace('fund_', '')` 拼，但
`fund_nav_history` tool 对应的实际 subcommand 是 `nav`，
不是 `nav-history`——所以我加了 `cli` 字段显式映射，避免
agent 复制粘贴报 unknown command）。

### Q7. 队列里 P3 stale 的 `nav_history` 要怎么补？

最简单的就是 `batch_suggestions[]` 里那行命令——它会调
`fund_cli.py nav <code> --provider auto --refresh`，等价于
MCP 的 `fund_nav_history(code, refresh=True)`。注意
`nav_history` 和 `snapshots` 这两个 P3 dataset 在
`batch_suggestions` 里**没有**对应的 batch 命令——因为
`--include-nav` / `--include-snapshot` 不存在（`fund sync`
默认就抓这两个），所以 `batch_suggestions` 跳过它们，让
consumer 用 per-fund CLI/MCP 单独处理。

### Q8. 为什么 `fund_managers` 不在 queue 里？

`fund_managers` 是 (manager_name, company, current_fund_codes)
复合主键的表，没有自然的"这只基金缺经理记录"信号。新的
`fund_manager_links` 投影虽然按 fund_code 查 O(1)，但
manager 数据是 fund-agnostic 的——一只基金可能有 1 个或 3 个
经理，缺一个不能说"数据缺"。`fund_managers` 走
`fund_sync --include-managers` 路径，不走 self-audit。

### Q9. `include_structural` 怎么用？

默认 **`self-audit` 不带 P4**（`include_structural=False`），
因为你问"现在该补啥"，P4 不是 actionable。**`health-check`
单只基金时默认带 P4**（`include_structural=True`），因为你
问"这只基金有什么问题"，"stock_holdings 是 info，因为你是
货币型"也算是个答案，agent 不用再去查 EXPECTED_EMPTY 矩阵。

如果你跑的是 **debug**（"为什么 coverage 这么低？"），就
`--include-structural` 看 P4；如果是 **生产调度**（"今晚
补啥？"），**别加**——P4 进去 batch-suggestions 也没意义，
每个 货币型 都会有一条 "batch-sync stock_holdings"，而
provider 永远返回空。

### Q10. 监控 / cron 怎么用 self-audit？

最简单的方式——**每天一次**生成 `data/self_audit_queue.json`，
然后**只对 P1 触发** batch-sync。具体做法（不在这次 spec
范围）：

```bash
# 每天凌晨 4 点跑
0 4 * * * cd /path/to/fundData && .venv-akshare/bin/python \
  fund-data/scripts/fund_cli.py self-audit --limit 1000 \
  --output data/self_audit_queue.json

# 用 jq 取 P1 codes，喂给 batch-sync
jq -r '.queue | map(select(.priority == "P1")) | .[].fund_code' \
  data/self_audit_queue.json > data/p1_codes.txt

# batch-sync 只补 P1 profile
.venv-akshare/bin/python fund-data/scripts/fund_cli.py batch-sync \
  --codes-file data/p1_codes.txt --include-profile --provider auto \
  --concurrency 4
```

注意：把 `self-audit` 喂给 cron 之前，确保 `FUND_DATA_DB` /
`FUND_DATA_CACHE_DIR` 都明确设了——否则 `default_db_path()`
会触发 `ensure_project_bundle()` 网络拉取，可能 hang。

### Q11. self-audit 输出能直接 push 到 Slack 吗？

能，因为它是纯 JSON。`summary.queue_size` / `p1` / `p2` /
`p3` 几行就能画个 markdown 表格。但 P1 的 `queue[]` 建议
**不要**全 push——只推 top 10 就行，剩下的存文件 `data/`
里等 agent 自己拿。

### Q12. 跟 2026-06-02 inventory 里的 49% 矛盾吗？

不矛盾。inventory 说"49% stock_holdings 覆盖率"——那个
49% 是**原始**占比，包含所有货币型 / 债券型 / REITs 等
**结构性空**。self-audit 会把它们的 stock_holdings 标成
P4/info，**P1/P2 的 stock_holdings 才是真缺的**。两条
数字都对，看你用哪个 layer 的定义。

## 反面教材

- **把 P4 当 actionable 跑 batch-sync**——会触发 8,600 次
  货币型 + 债券型 + REITs 的 stock_holdings 请求，provider
  全部返 0 行，**纯浪费**。`include_structural=False` 是
  default 就是为了挡这个。
- **把 P1 全部丢给 batch-sync 不看 score**——`fund_profiles`
  P1 缺 52 只的批次和 `nav_history` P1 缺 600 只的批次应该
  拆开，混在一起 batch-sync 会把 100-fund batch 的失败
  风险扩散。
- **拿 `summary.queue_size = 67097` 当 alarm 指标**——这
  是 raw 计数（含 P3 stale），不是 actionable 数。看
  `p1 + p2` 才是 actionable 缺口。

## 设计哲学

1. **不调 provider，不写 DB**——self-audit 是 read-only
   introspection layer，**永远只发建议**。`auto_fill_executed`
   字段是契约，让你一眼看出"这只是份报告"。
2. **复用 `EXPECTED_EMPTY` 矩阵**——货币型 / 债券型 /
   FOF / REITs / QDII 的结构性空是**已知事实**（监管决定
   的），不能因为"stock_holdings = 0"就报警。self-audit 和
   coverage_report 共享同一份矩阵（self-audit 用 DB 表名
   `industry_allocations`，coverage_report 用短标签
   `industry`），改一个记得改另一个。
3. **P1 > P2 > P3 > P4 严格单调**——score 公式保证
   `score(P1) > score(P2) > score(P3) > score(P4)`，agent
   截到 `--limit N` 拿到的就是**前 N 个最高优先级**，不会
   "P3 排在 P1 前面"这种"按 score 排看着对但其实是别的因素
   干扰"的情况。
4. **每条 queue entry 都带 actionable 命令**——`recommended_cli`
   和 `recommended_mcp_tool` 是**真实能跑**的命令（不是文档
   伪代码），agent 复制粘贴就能补。`cli` 字段显式存而不靠
   `replace('fund_', '')` 拼——后者会把 `fund_nav_history`
   拼成不存在的 `nav-history` subcommand。
5. **`batch_suggestions` 只发 P1/P2/P3**——P4 永远是
   structural_empty / naturally_sparse，喂给 batch-sync 是
   纯浪费。
