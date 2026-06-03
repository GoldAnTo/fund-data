# fund-data 演示说明稿（Talk Track）

> 现场检查日期：2026-06-03 Asia/Shanghai
> 适用场景：下午演示，1 个基金代码（`110022` 易方达消费行业股票）走通"查询 → 覆盖判断 → 净值/快照/持仓 → 导出 → agent 消费"完整闭环。
> 三档时长：5 分钟精简 / 10 分钟标准 / 15 分钟完整。**默认按 10 分钟准备**。
> 阅读方式：每段是"你该说什么"，**【屏幕】**标记表示同时操作的内容。

---

## 开场（30 秒）

【屏幕】空白终端，光标闪烁。

> 大家好。我今天演示的项目叫 **fund-data**——一个本地化的中国公募基金数据底座。它要解决的问题很朴素：
>
> 一、基金数据散落在各家网站，agent 没法稳定消费；
> 二、团队里既有人想查、也有自动化流程要调，需要同一个入口；
> 三、研究分析时引用的数字，必须能追溯到"来自哪个接口、什么时候抓的"。
>
> 接下来 10 分钟，我会用一只具体的基金——**易方达消费行业股票，代码 110022**——走通"查得到 → 看得清 → 拉得出 → 嵌得进"四个动作，证明这个项目已经不是一个概念验证，而是一个可被团队和 agent 同时复用的本地数据底座。

---

## 第一幕 · 数据底座（1.5 分钟）

【屏幕】运行 `python3 fund-data/scripts/fund_cli.py doctor --quiet | python3 -c "..."` 提取关键字段。

> 先看一眼系统状态。这是项目给 agent 用的 contract test——`doctor` 永远输出 JSON 到 stdout，stderr 是日志，agent 解析没有歧义。
>
> 现在这个环境的几个关键事实：
>
> - **数据库 OK**：路径是 `~/.cache/fund-data/releases/2026-06-02T214538Z/fund_data_query.sqlite`，这不是个空库，而是一份 135MB 的"查询版 SQLite"——是项目把全量库做了精简之后分发的版本。
> - **基金池 26,953 只**——中国公募基金的完整维度。
> - **同步失败队列是 0**。
> - **主力数据源 Eastmoney 可用**——这是免 key 的主源；AkShare 备选但系统 Python 没装也没关系，会降级；Investoday 付费增强源也已配好。

【屏幕】运行 `python3 fund-data/scripts/fund_cli.py cloud status`。

> 再看一下版本：本地装的是 `2026-06-02T214538Z` 这版，远端 manifest 也指向我们公开的 OSS。这步不是业务查询，是**版本检查**——同事、`cloud pull` 装的 agent 都能拿这个对齐状态。

---

## 第二幕 · 一只基金看清全局（2 分钟）

【屏幕】运行 `python3 fund-data/scripts/fund_cli.py coverage-report --code 110022`。

> 我用 **110022 易方达消费行业股票**做例子——A 股消费主题里最知名的一只。
>
> `coverage-report` 不是只告诉我"查得到"，而是一次说清楚"有什么、缺什么、缺多少"：
>
> - 基础档案有；
> - 净值 **284** 行——这只基金从 2010 年成立到现在的日序列；
> - 股票持仓 **146** 行——历次季报披露；
> - 债券持仓 12 行、行业配置 20 行、费率 4 行；
> - **整体完整度 0.75**。
>
> 缺的两项是 **dividends 和 splits**。
>
> 【停顿】
>
> 这是项目设计上一个很重要的取舍——**不把空数据假装成完整数据**。"有"和"缺"都显式说出来，让人和 agent 都能做判断。同时要注意：分红和拆分天然稀疏，对一只成立十几年但分红次数很少的主动基金来说，缺失不一定代表系统失败。
>
> 我们下一步会做 fund_type-aware 的覆盖矩阵——给"缺"打"结构性为空"或"真实缺口"的标签。

---

## 第三幕 · 时间序列 + 当前状态（2.5 分钟）

### 3.1 净值历史（1.5 分钟）

【屏幕】运行 `python3 fund-data/scripts/fund_cli.py nav 110022 --start-date 2024-01-22 --end-date 2024-01-26`。

> 这一段是时间序列。选了 2024 年 1 月 22 到 26 这 5 个交易日做切片。可以看到：
>
> - 1 月 24 日单位净值 **3.192**；
> - 1 月 25 日 **3.231**；
> - 1 月 26 日 **3.238**。
>
> 注意每行都有 `source` 字段——这行数据来自 `investoday.fund_nav_history` 接口。我们做研究汇报时，引用任何数字都能追溯到"是哪个接口、什么时候抓的"。
>
> 这步是给回测、走势分析、组合关联用的底层数据。CLI 可以按时间切片，Python API 里更细。

### 3.2 当前快照（1 分钟）

【屏幕】运行 `python3 fund-data/scripts/fund_cli.py snapshot 110022`。

> 快照反映"抓取时点"——和历史净值的区别是：前者是截面，后者是序列。
>
> 这只基金：
>
> - 原始费率 1.5%、当前费率 0.15%——大概率是申购费打折后；
> - 起购 10 元；
> - 前十大重仓股票代码列表——前缀 `1.` 是沪市、`0.` 是深市；
> - 近一年回报 **-16.14%**、近半年 **-19.26%**。
>
> 消费板块这两年的承压直接体现在数据上。这就是为什么我选 110022 做演示——它的数据有故事。

---

## 第四幕 · 持仓、导出、Python 嵌入（2.5 分钟）

### 4.1 持仓明细（1 分钟）

【屏幕】运行 `python3 fund-data/scripts/fund_cli.py holdings 110022 | head -20`。

> 这是 2026-04-22 那期季报披露的持仓。**贵州茅台 9.9%**、**美的集团 9.64%**——两只加起来接近 20% 仓位，集中度是消费主题基金的特点。
>
> 注意 `report_period` 是季报披露日，`source` 是 `investoday.fund_portfolio_stock_holdings`。**每个数据集都有 source 字段**——这是项目做"数据可追溯"的硬约束。

### 4.2 导出下游（1 分钟）

【屏幕】依次运行：
```bash
python3 fund-data/scripts/fund_cli.py export nav_history --fund-code 110022 --format json > /tmp/demo-2026-06-03/110022_nav.jsonl
python3 fund-data/scripts/fund_cli.py export stock_holdings --fund-code 110022 --format csv > /tmp/demo-2026-06-03/110022_holdings.csv
ls -la /tmp/demo-2026-06-03/
```

> 导出两条产物：
>
> - **JSON Lines** 给 agent 和脚本消费——一行一条记录，可以 stream parse，agent 不需要把整个文件读进内存；
> - **CSV** 给 Excel 和 pandas 用。
>
> 我们故意不做 nested JSON，因为线上的 agent 任务通常更想要扁平结构。

### 4.3 Python 嵌入（30 秒）

【屏幕】运行 `PYTHONPATH=fund-data python3 - <<'PY' ...`。

> 最后一步证明它不只是命令行工具。我把 `scripts` 加到 PYTHONPATH 后直接 import `fund_data`——`default_db_path()` 和 `coverage_report()` 是公开 API。
>
> Agent、Notebook、FastAPI 服务层都能复用同一个入口，不会有"Python 路径走另一套逻辑"的隐式不一致。

---

## 第五幕 · 进阶能力（1.5 分钟）— 视时间选做

### 5.1 Cloud bundle（45 秒）

【屏幕】运行 `python3 fund-data/scripts/fund_cli.py cloud status`。

> 刚提到了 cloud bundle，再展开讲一句。同事或者 agent 进新环境，两步就能自举：
>
> 1. `cloud pull` 下载查询版 SQLite；
> 2. `doctor` 跑自检。
>
> 整个过程**不需要任何 provider token**。这就是项目给 agent 多环境消费的关键路径。

### 5.2 MCP server（45 秒）— 仅在观众问到"agent 怎么调"时展开

> 还有一个面向 agent 的标准化入口——MCP server，stdio 协议。任何 MCP-capable agent（Claude、Codex、Cursor）都能直接发现并调用 fund-data 的所有能力，不需要 shell out 到 `fund_cli.py`。
>
> 这步现场不细演示，**核心意思是**：CLI 不是唯一的入口。agent 想要标准化发现机制，走 MCP；想要脚本化，走 Python API；想要快速调试，走 CLI。三条路同一个数据底座。

---

## 收束（30 秒）

> 总结一下。
>
> `fund-data` 现在已经不是一个概念验证，而是一个**可运行的本地基金数据底座**。它有：
>
> - **本地 SQLite**——单文件、零运维、agent 自带；
> - **四源自动 fallback**——Eastmoney 免 key 主力，AkShare 备用，Investoday / Tushare 付费增强；
> - **三种消费入口**——CLI、Python API、MCP server；
> - **覆盖率和缺失解释**——不假装数据完整；
> - **cloud bundle 分发**——同事和 agent 跨环境一键自举。
>
> 下一步不是从零开始，而是在现有底座上**补展示层、补结构化缺口解释、补更细的数据新鲜度**。
>
> 最后，也是最重要的边界——**这些数据用于研究和分析，不构成投资建议**。真正引用任何数字时，都应该带上 `source` 和 `fetched_at`。
>
> 谢谢。Q&A？

---

## 备选开场（如果时间允许做 15 分钟完整版）

> 在第一幕前补一段：
>
> "在说 fund-data 之前，先说**为什么这件事值得做**。
>
> 我们的 agent 自动化流程里，会反复需要查基金数据：研究员让 agent 找'消费主题里近一年回撤最大的'，产品经理让 agent 列出'所有货币基金的最新 7 日年化'，合规让 agent 检查'持仓里有没有某个被监管点名的股票'。
>
> 这些任务的共同点：
> - 数据必须**新鲜**——不是去年的快照；
> - 数据必须**可追溯**——agent 不能凭印象回答；
> - 数据必须**机器可消费**——不是 Excel 截图。
>
> fund-data 就是为了把这三个要求都满足而存在的本地底座。"

---

## Q&A 速答卡

| 问题 | 一句话答案 |
|---|---|
| 能用吗？ | 能，5 条路径都验证过；`doctor` 是 contract。 |
| 数据全吗？ | 基金池全量；持仓/行业约 50–60%；分红/拆分天然稀疏。 |
| 缺失怎么解释？ | 结构为空 vs 真实缺口；下版加 fund_type-aware 标签。 |
| 网络差怎么办？ | 用本地已装 bundle（135MB），`doctor` 之后所有查询不依赖网络。 |
| 投资建议？ | **不构成**。研究用，引用带 source + fetched_at。 |
| 为什么 SQLite？ | 零运维、单文件、Python `sqlite3` 标准库、agent 自带。 |
| 和 iFinD/Wind 区别？ | 价格、可编程、可追溯、可分发；它们仍是行业标杆。 |
| 4 个 provider 怎么选？ | Eastmoney 主力（免 key、最快）；AkShare 备用（公开免费）；Investoday 增强（付费）；Tushare 可选。 |

---

## 演示节奏自查

| 时点 | 应当做到 |
|---|---|
| 0:30 | 介绍项目定位 + 选 110022 |
| 1:30 | doctor + cloud status 跑完 |
| 3:30 | coverage-report 跑完，解释完整度 |
| 5:00 | nav + snapshot 跑完 |
| 7:00 | holdings 跑完 |
| 8:00 | 导出两条 + Python import 跑完 |
| 9:00 | cloud bundle 收尾 |
| 9:30 | 收束 + Q&A 提示 |

> 节奏偏快就跳过 §5.1-5.2 的"进阶能力"小节，§1-4 跑完即收束。
