# OpenClaw 演示案例：110022 易方达消费行业股票

现场日期：2026-06-03 Asia/Shanghai  
推荐演示基金：`110022`，易方达消费行业股票  
推荐时长：核心演示 7 分钟，含问答 10 到 12 分钟  
演示目标：让观众看到 OpenClaw 可以从一只基金查到基金档案、净值、快照、持仓、行业、费率、经理、缺失项和数据来源。

## 1. 先给 OpenClaw 的一键提示词

如果你已经连接 OpenClaw，可以直接输入这段：

```text
请使用 fund-data 工具查询 110022 易方达消费行业股票。
为了现场稳定，请优先读取本地已安装的 persisted rows，不要强制 live refresh。
按这个顺序调用工具：
1. fund_cloud_status，确认当前使用的 cloud query bundle；
2. fund_coverage_report，先判断这只基金有哪些数据、缺哪些数据；
3. fund_export table=fund_profiles fund_code=110022，查询基金档案；
4. fund_export table=nav_history fund_code=110022，查询历史净值，并总结最新 5 条；
5. fund_export table=snapshots fund_code=110022，查询快照、费率折扣、近期收益和股票代码列表；
6. fund_export table=stock_holdings fund_code=110022，查询股票持仓；
7. fund_export table=bond_holdings fund_code=110022，查询债券持仓；
8. fund_export table=industry_allocations fund_code=110022，查询行业配置；
9. fund_export table=fee_structures fund_code=110022，查询费率；
10. fund_export table=dividends fund_code=110022 和 table=splits fund_code=110022，确认分红和拆分是否为空。

最后请按“基金概况、净值、组合、费用、经理、缺失项、数据来源、风险边界”总结。
基金经理优先使用 fund_profiles.manager 字段；如果 MCP server 是用 .venv-akshare/bin/python 启动的，再额外调用 fund_managers code=110022。
不要给投资建议，只说明数据事实和数据来源。
```

如果 OpenClaw 需要逐个点工具，参数如下：

| 步骤 | 工具 | 参数 |
|---|---|---|
| Cloud 状态 | `fund_cloud_status` | `{}` |
| 覆盖报告 | `fund_coverage_report` | `{"codes":["110022"]}` |
| 基金档案 | `fund_export` | `{"table":"fund_profiles","fund_code":"110022"}` |
| 净值历史 | `fund_export` | `{"table":"nav_history","fund_code":"110022"}` |
| 快照 | `fund_export` | `{"table":"snapshots","fund_code":"110022"}` |
| 股票持仓 | `fund_export` | `{"table":"stock_holdings","fund_code":"110022"}` |
| 债券持仓 | `fund_export` | `{"table":"bond_holdings","fund_code":"110022"}` |
| 行业配置 | `fund_export` | `{"table":"industry_allocations","fund_code":"110022"}` |
| 费率 | `fund_export` | `{"table":"fee_structures","fund_code":"110022"}` |
| 分红 | `fund_export` | `{"table":"dividends","fund_code":"110022"}` |
| 拆分 | `fund_export` | `{"table":"splits","fund_code":"110022"}` |
| 经理增强 | `fund_managers` | `{"code":"110022"}`，仅在 MCP server 使用 `.venv-akshare/bin/python` 时演示 |

备用 CLI 路径：

```bash
python3 fund-data/scripts/fund_cli.py doctor --skip-network --quiet
python3 fund-data/scripts/fund_cli.py cloud status
python3 fund-data/scripts/fund_cli.py coverage-report --code 110022
python3 fund-data/scripts/fund_cli.py export fund_profiles --fund-code 110022
python3 fund-data/scripts/fund_cli.py export nav_history --fund-code 110022
python3 fund-data/scripts/fund_cli.py export snapshots --fund-code 110022
python3 fund-data/scripts/fund_cli.py export stock_holdings --fund-code 110022
python3 fund-data/scripts/fund_cli.py export bond_holdings --fund-code 110022
python3 fund-data/scripts/fund_cli.py export industry_allocations --fund-code 110022
python3 fund-data/scripts/fund_cli.py export fee_structures --fund-code 110022
```

如果现场要展示 live provider fetch，而不是读取本地 persisted rows，推荐确认 MCP/CLI 使用项目 venv。系统 Python 下 AkShare 相关 provider 会是 degraded 状态：

```bash
.venv-akshare/bin/python fund-data/scripts/fund_cli.py holdings 110022 --report-year 2024
.venv-akshare/bin/python fund-data/scripts/fund_cli.py bonds 110022 --report-year 2024
.venv-akshare/bin/python fund-data/scripts/fund_cli.py industries 110022 --report-year 2024
.venv-akshare/bin/python fund-data/scripts/fund_cli.py fees 110022 --indicator 申购费率
.venv-akshare/bin/python fund-data/scripts/fund_cli.py managers --code 110022
```

## 2. 为什么选 110022

`110022` 是易方达消费行业股票，属于股票型基金，主题鲜明，适合演示“主动权益基金”的完整数据链。

它的好处是：

- 有清楚的基金档案：公司、经理、成立日、业绩基准都有。
- 有净值历史和快照，能讲时间序列和当前状态。
- 有股票持仓、债券持仓、行业配置，能讲组合结构。
- 有费率，能讲交易成本。
- 有经理数据，能讲后续研究对象。
- 缺分红和拆分，正好能讲“项目不会把空数据伪装成完整数据”。

当前实测覆盖报告：

```json
{
  "fund_code": "110022",
  "fund_name": "易方达消费行业股票",
  "fund_type": "股票型",
  "has_profile": 1,
  "nav_rows": 264,
  "stock_holding_rows": 136,
  "bond_holding_rows": 12,
  "industry_rows": 20,
  "fee_rows": 4,
  "dividend_rows": 0,
  "split_rows": 0,
  "completeness": 0.75,
  "missing": ["dividends", "splits"]
}
```

现场讲法：

> 我选这只基金不是因为它收益好坏，而是因为它非常适合演示数据覆盖。它是典型的主动股票型消费主题基金，基本信息、净值、快照、持仓、债券、行业、费率、经理都有数据。缺的是分红和拆分，而这两类本来就是事件型数据，不是每只基金都会发生。

## 3. 110022 当前能讲出来的数据

### 3.1 基金档案

当前本地 bundle 实测：

| 字段 | 值 |
|---|---|
| 基金代码 | `110022` |
| 基金名称 | 易方达消费行业股票 |
| 全称 | 易方达消费行业股票型证券投资基金 |
| 类型 | 股票型 |
| 成立日 | 2010-08-20 |
| 资产规模字段 | `126.86`，上游口径 |
| 资产规模日期 | 2026-03-31 |
| 基金公司 | 易方达基金 |
| 基金经理 | 萧楠 |
| 业绩基准 | 中证内地消费主题指数收益率*85% + 中债总指数收益率*15% |
| 来源 | `akshare.fund_overview_em` |
| 抓取时间 | 2026-06-03T05:37:43+00:00 |

现场讲法：

> 这一步说明项目不是只查基金代码和名字，而是已经把基金档案结构化落表。这里能看到基金公司、基金经理、成立日、业绩基准，以及 source 和 fetched_at。source 告诉我们来自哪个 provider，fetched_at 告诉我们什么时候写入本地库。

### 3.2 净值历史

最近几条本地净值：

| 日期 | 单位净值 | 累计净值 | 日增长率 | 来源 |
|---|---:|---:|---:|---|
| 2026-06-01 | 2.890 | 2.890 | 0.03% | `eastmoney.nav_history` |
| 2026-05-29 | 2.889 | 2.889 | 1.12% | `eastmoney.nav_history` |
| 2026-05-28 | 2.857 | 2.857 | -1.55% | `eastmoney.nav_history` |
| 2026-05-27 | 2.902 | 2.902 | -0.38% | `eastmoney.nav_history` |
| 2026-05-26 | 2.913 | 2.913 | -0.14% | `eastmoney.nav_history` |

现场讲法：

> 净值是时间序列数据，适合做走势、回测、组合归因的基础。这个项目里净值不是临时打印出来，而是进入 `nav_history` 表，主键是 fund_code 加 nav_date，可以稳定复用。

### 3.3 快照

快照字段：

| 字段 | 值 |
|---|---|
| 原申购费率 | 1.5% |
| 当前折扣费率 | 0.15% |
| 起购金额 | 10.0 |
| 近 1 月 | -7.76% |
| 近 3 月 | -12.26% |
| 近 6 月 | -19.70% |
| 近 1 年 | -16.23% |
| 快照来源 | `eastmoney.snapshot` |

股票代码列表中包含：`600519`、`000333`、`000858`、`600809`、`002594`、`600660`、`000568`、`000596`、`601633`、`603129`。

现场讲法：

> 快照和净值历史不一样，快照是抓取时点的页面状态，适合做当前展示，比如近期收益、折扣费率、起购金额和公开披露股票代码列表。

### 3.4 股票持仓

2024-12-31 前十大持仓节选：

| 股票 | 占净值比例 |
|---|---:|
| 福耀玻璃 | 9.60% |
| 美的集团 | 9.58% |
| 贵州茅台 | 9.15% |
| 五粮液 | 8.59% |
| 山西汾酒 | 7.56% |
| 古井贡酒 | 7.02% |
| 长城汽车 | 6.57% |
| 东鹏饮料 | 4.93% |
| 海尔智家 | 4.11% |
| 赛轮轮胎 | 3.55% |

来源：`akshare.fund_portfolio_hold_em`。

现场讲法：

> 这一步最适合让观众感受到“查到所有信息”的效果。110022 是消费主题基金，前十大里有白酒、家电、汽车和食品饮料，组合特征非常直观。项目把这些持仓落成 `stock_holdings` 表，而不是只在页面上展示一段文本。

### 3.5 债券持仓

2024-12-31 债券持仓节选：

| 债券 | 占净值比例 |
|---|---:|
| 牧原转债 | 0.40% |
| 百润转债 | 0.18% |
| 欧22转债 | 0.18% |

来源：`akshare.fund_portfolio_bond_hold_em`。

现场讲法：

> 股票型基金也可能有少量债券或可转债仓位。这个例子里债券仓位不高，但能说明系统不是只做股票持仓，而是把股票、债券、行业三类组合披露分开存。

### 3.6 行业配置

2024-12-31 行业配置节选：

| 行业 | 占净值比例 |
|---|---:|
| 制造业 | 86.95% |
| 农、林、牧、渔业 | 0.46% |
| 科学研究和技术服务业 | 0.00% |
| 信息传输、软件和信息技术服务业 | 0.00% |

来源：`akshare.fund_portfolio_industry_allocation_em`。

现场讲法：

> 行业口径要看上游分类。消费基金里很多白酒、家电、汽车会被归到制造业，所以这里制造业很高。项目保留 source，就是为了后面解释这些口径差异。

### 3.7 费率结构

申购费率节选：

| 条件 | 原费率 | 折扣费率 |
|---|---:|---:|
| 小于100万元 | 1.50% | 0.15% |
| 大于等于100万元，小于500万元 | 1.20% | 0.12% |
| 大于等于500万元，小于1000万元 | 0.30% | 0.03% |
| 大于等于1000万元 | 每笔1000元 | 空 |

来源：`eastmoney.fund_fee_page`。

现场讲法：

> 费率不是研究收益本身，但它是实际交易和展示不可缺的一部分。项目当前已经把费率结构按条件拆成结构化行，而不是只有一个字符串。

### 3.8 基金经理

| 字段 | 值 |
|---|---|
| 经理 | 萧楠 |
| 公司 | 易方达基金 |
| 当前基金代码 | `110022` |
| 当前基金 | 易方达消费行业股票 |
| 任职天数字段 | `4996` |
| 当前管理规模字段 | `225.82` |
| 最佳回报字段 | `2.7283` |
| 来源 | `akshare.fund_manager_em` |
| 抓取时间 | 2026-06-03T05:26:03+00:00 |

现场讲法：

> 经理表当前能查，但它的结构是 manager-centric，也就是按经理组织，再通过 current_fund_codes 反查基金。它能用，但后续应该补一个 fund-centric 的关联表或 view，这样“基金到经理”的查询会更直接。

## 4. 项目做了什么

一句话版本：

> `fund-data` 把中国公募基金的公开数据和可选付费数据统一接入、清洗、落库，并通过 CLI、MCP 和 Python API 暴露给人和 agent 使用。

展开讲：

- 做了本地基金数据底座，默认 SQLite。
- 做了 OpenClaw / MCP 入口，让 agent 能直接调工具。
- 做了 CLI，方便人手工演示和自动化脚本调用。
- 做了 provider fallback，不绑定单一数据源。
- 做了 OSS query bundle，OpenClaw 默认不用从零抓全量数据。
- 做了覆盖率报告，能说明每只基金有哪些数据、缺哪些数据。
- 做了导出能力，能把业务表导出成 JSON 或 CSV。
- 做了 doctor 自检，能判断数据库、provider、cloud cache 是否可用。

## 5. 项目怎么做的

数据链路可以这样讲：

```text
OpenClaw / CLI / Python
  -> fund-data MCP 或 fund_cli.py
  -> fund_data Python helpers
  -> 默认解析本地 cloud query bundle
  -> 如缓存缺失或用户强制刷新，再走 provider chain
  -> 标准化写入 SQLite 业务表
  -> 输出 JSON 给 agent 或导出 CSV/JSON 给下游
```

两个顺序要讲清楚：

1. 数据库选择顺序：优先使用显式 `FUND_DATA_DB`，否则自动使用项目 OSS query bundle 的本地缓存；OSS 不可用时再走 live provider/API。
2. provider 查询顺序：当前环境 Investoday key 可用时，`auto` 会优先尝试 Investoday；没有 key 时，免费源主要是 Eastmoney 和 AkShare。净值查询是 read-through cache，默认先读本地库，`--refresh` 才强制刷新。

## 6. 项目怎么用

### OpenClaw / MCP

推荐演示 OpenClaw，因为你已经接好了。核心工具包括：

- `fund_cloud_status`
- `fund_export`
- `fund_coverage_report`
- `fund_search`
- `fund_profile`
- `fund_nav_history`
- `fund_snapshot`
- `fund_stock_holdings`
- `fund_bond_holdings`
- `fund_industry_allocations`
- `fund_fee_structures`
- `fund_managers`

现场主线优先用 `fund_export` 读取本地 persisted rows。`fund_profile`、`fund_nav_history`、`fund_stock_holdings` 等工具更像 provider fetch / refresh 入口，适合在确认 venv、key 和网络都正常时展示。

### CLI

```bash
python3 fund-data/scripts/fund_cli.py coverage-report --code 110022
python3 fund-data/scripts/fund_cli.py nav 110022 --start-date 2026-05-21 --end-date 2026-06-02
python3 fund-data/scripts/fund_cli.py export funds --format csv --output /tmp/funds.csv
```

### Python

```bash
PYTHONPATH=fund-data python3 - <<'PY'
from scripts import fund_data

db = fund_data.default_db_path()
rows = fund_data.coverage_report(db_path=db, codes=["110022"])
print(db)
print(rows[0]["fund_code"], rows[0]["fund_name"], rows[0]["completeness"])
PY
```

## 7. 数据从哪里来

项目当前不是单一 API，而是多源融合：

| 数据源 | 是否需要 key | 典型用途 |
|---|---|---|
| Eastmoney | 不需要 | 基金池、搜索、净值、快照、费率页 |
| AkShare | 不需要，但需要 Python 依赖 | 基金档案、持仓、债券、行业、费率、分红、拆分、基金经理 |
| Investoday | 需要 `INVESTODAY_API_KEY` 或旧名 `INVESTDATA_API_KEY` | 结构化付费源，适合档案、净值、组合等增强数据 |
| Tushare | 需要 `TUSHARE_TOKEN` | 可选专业源，补基金池、档案、净值、持仓、经理等 |
| OSS query bundle | 不需要业务 key | 已构建好的查询版 SQLite，给 OpenClaw 和本地查询默认使用 |

Investoday 文档侧已整理出的基金相关接口：

- 基金产品数据接口 48 条。
- 基金工作流辅助接口 3 条，包括 `/search`、`/entity-recognition`、`/api/prompt/diagnosis-fund`。
- 典型路径包括 `/fund/all`、`/fund/nav/history`、`/fund/portfolio-stock-holdings`、`/fund/portfolio-bond-holdings`、`/fund/fee-structures`、`/fund/dividend`、`/funds/share-splits`、`/fund-manager/basic-info`、`/fund-company/evaluations` 等。

要强调：接口目录已经审过，但不等于每个接口都已经在 provider 里完全落库。当前项目已经接入的是核心查询和研究链路，剩下的接口要按优先级继续补。

## 8. 当前有哪些数据

当前已安装 query bundle：

| 项目 | 值 |
|---|---|
| version | `2026-06-03-104948` |
| 数据库 | `/Users/xiongjiali/.cache/fund-data/releases/2026-06-03-104948/fund_data_query.sqlite` |
| 基金池 | 26,953 只 |
| 同步失败队列 | 0 |
| 默认来源 | cloud cache |
| 最近业务表抓取时间 | 2026-06-03T05:37:43+00:00 |

当前业务表覆盖：

| 数据集 | 行数 | 覆盖基金 | 覆盖率 |
|---|---:|---:|---:|
| `funds` 基金池 | 26,953 | 26,953 | 100.00% |
| `fund_profiles` 基金档案 | 26,953 | 26,953 | 100.00% |
| `nav_history` 历史净值 | 1,318,202 | 26,337 | 97.71% |
| `snapshots` 快照 | 26,952 | 26,952 | 近 100% |
| `stock_holdings` 股票持仓 | 2,475,205 | 13,255 | 49.18% |
| `bond_holdings` 债券持仓 | 548,976 | 15,426 | 57.23% |
| `industry_allocations` 行业配置 | 415,723 | 13,268 | 49.23% |
| `fee_structures` 费率 | 80,121 | 26,929 | 99.91% |
| `dividends` 分红 | 52,347 | 7,702 | 28.58% |
| `splits` 拆分/折算 | 1,740 | 589 | 2.19% |
| `fund_managers` 基金经理 | 34,706 条经理记录 | manager-centric | 需关联解析 |

## 9. 优点

现场可以讲这 6 点：

1. 本地优先：OpenClaw 默认读已安装 OSS query bundle，现场网络不稳定也能查核心数据。
2. 多源 fallback：Eastmoney、AkShare、Investoday、Tushare 可以按能力互补。
3. Agent-friendly：MCP 工具输出结构化 JSON，适合 OpenClaw 继续总结和推理。
4. 可审计：业务表保留 source 和抓取时间；full DB 还保留 raw responses 和 sync logs。
5. 可复用：同一套能力同时支持 CLI、MCP、Python 和导出。
6. 不掩盖缺口：coverage report 会明确告诉用户每只基金缺哪些数据。

## 10. 现在还欠缺什么

这部分建议主动讲，不要等观众追问：

1. 组合披露还不全。股票持仓、债券持仓、行业配置覆盖约 49% 到 57%。一部分是结构性为空，例如货币基金、纯债基金、REITs 本来没有股票或行业配置；另一部分是上游接口和版本漂移导致的真实缺口。
2. 分红和拆分覆盖低，但这是天然稀疏。分红覆盖 28.58%，拆分覆盖 2.19%，不能简单按 100% 要求补齐。后续要给 coverage report 增加 expected_empty 标签。
3. 基金经理表还偏 manager-centric。现在能查，但“基金到经理”最好补一个 fund-centric view 或 join table。
4. Investoday 接口目录已覆盖，但 provider 未把所有 48 个基金产品接口都落库。下一步要补齐组合、持有人、ETF 专项、公告、公司和经理增强字段。
5. 公共 OSS query bundle 是查询版，不包含 raw_responses、sync_runs、sync_failures 等审计表。完整审计库应继续走私有 full archive。
6. search 这类发现型操作仍可能触发 live provider。演示时核心路径建议优先用 coverage/profile/nav/snapshot 等本地可读数据，减少现场网络变量。

## 11. 7 分钟演示说明稿

大家好，我演示一下 `fund-data` 这个项目。

这个项目解决的问题很具体：我们希望有一个本地可查询、可追溯、能被 agent 复用的中国公募基金数据底座。它不是临时爬一页，也不是手工维护 CSV，而是把基金池、基金档案、净值、快照、持仓、行业、费率、分红、拆分、基金经理这些数据统一进 SQLite，然后通过 CLI、Python API 和 MCP 暴露出来。

我今天用 OpenClaw 演示，基金选 `110022`，易方达消费行业股票。选它不是为了推荐这只基金，而是因为它是典型的主动股票型消费主题基金，数据维度比较完整，适合展示从基础信息到组合结构的完整链路。

第一步我让 OpenClaw 调 `fund_coverage_report`。这里最关键的是，它不是只说“查得到”，而是告诉我们这只基金有哪些数据、缺哪些数据。当前结果显示：档案有，净值有 264 行，股票持仓 136 行，债券持仓 12 行，行业配置 20 行，费率 4 行，分红和拆分是 0，整体 completeness 是 0.75。这体现项目的一个原则：不把空数据伪装成完整数据。

第二步用 `fund_export` 读取 `fund_profiles`。这里能看到它的全称、股票型、成立日 2010-08-20、基金公司易方达基金、基金经理萧楠、业绩基准，以及 source 是 `akshare.fund_overview_em`。这说明我们的数据不是只有结果，还会保留来源。

第三步用 `fund_export` 读取 `nav_history`。净值历史是时间序列，本地 bundle 最近数据里 2026-06-01 单位净值是 2.890，来源是 `eastmoney.nav_history`。如果现场调用 live `fund_nav_history`，当前 Investoday 可能会返回 2026-06-02 的 2.893，这说明 provider refresh 比本地 bundle 多了一天，并不是矛盾。这个表后续可以用于回测、走势、组合分析。

第四步用 `fund_export` 读取 `snapshots`。快照是抓取时点的数据，包含原申购费率 1.5%、当前折扣费率 0.15%、起购金额 10，以及近 1 月、近 3 月、近 6 月、近 1 年收益等字段。它和历史净值不同，快照更适合做当前状态展示。

第五步看组合披露。股票持仓里，2024-12-31 前几大持仓包括福耀玻璃、美的集团、贵州茅台、五粮液、山西汾酒等，消费主题非常明显。债券持仓里有牧原转债、百润转债、欧22转债，占比不高，但说明股票型基金也可能有可转债仓位。行业配置里制造业占比很高，这是上游行业口径造成的，项目保留 source 就是为了后续解释这些口径。

第六步看费用和经理。费率表里能看到不同申购金额区间的原费率和折扣费率。经理表里能看到萧楠，以及任职天数、当前管理规模字段和最佳回报字段。这里我也会说明一个后续改进点：经理表当前是按经理组织的，后续应该补一个基金到经理的关联 view。

数据来源方面，项目不是只依赖一个 API。免 key 的 Eastmoney 负责基金池、搜索、净值、快照、费率页；AkShare 负责档案、持仓、债券、行业、费率、分红、拆分和经理；Investoday 和 Tushare 是可选增强源，需要 key。OpenClaw 默认会优先读我们发布到 OSS 的 query bundle，所以不需要每次从零抓全量数据。如果本地没有 bundle，或者用户强制刷新，才会走 live provider chain。

当前项目的优势是：本地优先、多源 fallback、输出结构化、source 可追溯、CLI/MCP/Python 可复用，并且 coverage report 会主动暴露缺口。当前还欠缺的是：组合披露类数据还没有全覆盖，基金经理需要 fund-centric view，Investoday 的 48 个基金产品接口还没有全部落库，分红和拆分需要 expected_empty 标签来区分天然为空和真实缺失。

最后强调一下：这个项目提供的是数据底座和研究基础，不是投资建议。任何数字都应该带上 source 和 fetched_at 使用。今天这个演示说明，OpenClaw 已经可以围绕一只基金，从覆盖判断一路查到档案、净值、快照、持仓、行业、费用、经理和缺失项。

## 12. 如果现场出问题怎么说

如果 OpenClaw 搜索慢：

> 搜索是发现型操作，可能触发 live provider。我们先用已知代码 `110022` 走本地 query bundle，演示核心查询链路。

如果 `fund_profile`、`fund_stock_holdings`、`fund_industry_allocations` 或 `fund_fee_structures` 这类 provider 工具失败：

> 这说明当前 MCP server 可能不是用 `.venv-akshare/bin/python` 启动，或者 live provider 没返回数据。核心业务表已经在 query bundle 里，改用 `fund_export table=... fund_code=110022` 读取 persisted rows 即可。

如果 AkShare 报未安装：

> 系统 Python 下 AkShare 是 degraded 状态，但项目 venv 已安装 AkShare 1.18.64。基础查询可以继续，增强维度用 `.venv-akshare/bin/python` 跑。

如果有人问为什么持仓不是 100%：

> 组合披露不是所有基金都有。货币、纯债、REITs、部分后端份额天然没有股票持仓或行业配置。另外上游接口有 schema drift，所以这部分是下一阶段重点补齐。

如果有人问 query bundle 和 full DB 的区别：

> query bundle 是给 OpenClaw 和普通查询用的轻量业务库，不包含 raw responses、sync logs、failure queues。full DB 是内部审计和重放用的完整库，应该走私有归档，不公开发布。

如果有人问数据是否最新：

> 当前演示使用的是 `2026-06-03-104948` bundle。不同表有自己的抓取时间，演示时引用每个字段的 source 和 fetched_at，不把它说成实时行情。
