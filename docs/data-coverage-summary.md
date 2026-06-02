# 基金数据覆盖说明

> 最近刷新：2026-06-02 17:38 Asia/Shanghai。
> 快照来源：最后一次成功拉取并校验通过的 cloud query bundle
> `2026-06-02-1701`。
> Bundle 路径：
> `/Users/xiongjiali/.cache/fund-data/releases/2026-06-02-1701/fund_data_query.sqlite`。
> Manifest URL：
> `https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json`。

这份文档是 `fund-data` 项目的当前数据覆盖总览，面向人和 agent
共同阅读。它回答四件事：

1. 本地现在有哪些基金数据。
2. 哪些数据缺失，哪些是天然为空。
3. 每类数据的时间范围。
4. 每类数据来自哪个上游来源。

默认情况下，CLI / MCP / agent 在没有设置 `FUND_DATA_DB` 时会使用
cloud query bundle。这个 bundle 只包含业务查询表，不包含本地审计
表；本地 full DB 的审计表单独列在文末。

## 中文处理说明

项目可以处理中文数据和中文输出：

- 基金名称、基金类型、基金经理、公司、费用条件、行业名称等字段均保留中文。
- CLI / MCP 的 JSON 输出使用中文内容，不需要再做转码才能阅读。
- 文档正文使用中文；数据库表名、字段名、provider 名、命令名保留英文，
  方便和 SQL、代码、日志直接对应。
- 报告里提到的“缺失”分两类：一类是可重试/可补齐的真实缺口，另一类是
  货币型、纯债、REITs、未分红/未拆分等天然没有对应披露的数据。

## 快照卡片

| 项目 | 当前值 |
|---|---:|
| 基金池 | 26,953 只基金 |
| Query bundle 大小 | 721,027,072 bytes 解压后 / 119,329,974 bytes gzip |
| 业务表 | 11 张 |
| 基础档案覆盖 | `funds` 100%，`fund_profiles` 100% |
| 高覆盖数据 | `snapshots` 99.93%，`fee_structures` 99.91%，`fund_managers` 98.84% 可解析到当前基金池，`nav_history` 97.58% |
| 稀疏或结构性数据 | 股票持仓 48.96%，债券持仓 57.02%，行业配置 49.15%，分红 28.58%，拆分 2.19% |
| 最近成功 cloud pull | 2026-06-02T09:38:40+00:00 |

远端 manifest 后续又公布了 `2026-06-02T093613Z`，但 `cloud pull`
拉取该 release artifact 时返回 HTTP 404。直到 manifest 和 release
文件重新一致前，`2026-06-02-1701` 是当前最后一个已校验可用的 bundle。

## 已覆盖的数据

| 数据集 | 表 | 行数 | 基金覆盖 | 数据时间范围 | 抓取时间范围 | 来源 |
|---|---:|---:|---:|---|---|---|
| 基金池 / 最新净值卡片 | `funds` | 26,953 | 26,953 / 26,953 = 100.00% | `nav_date`: 2026-06-01 | 2026-06-01T18:25:21Z 至 2026-06-02T05:50:47Z | `eastmoney.fundcode_search` 26,934 行；`eastmoney.search` 10；`eastmoney.snapshot` 9 |
| 基金档案 / 公司 / 业绩基准 | `fund_profiles` | 26,953 | 26,953 / 26,953 = 100.00% | `establishment_date`: 2001-09-21 至 2026-06-01；`asset_size_date`: 2018-09-30 至 2026-06-01 | 2026-06-01T06:04:11Z 至 2026-06-02T08:27:14Z | `akshare.fund_overview_em` 26,945；`investoday.fund_all` 8 |
| 历史净值 | `nav_history` | 528,083 | 26,300 / 26,953 = 97.58% | `nav_date`: 2018-05-29 至 2026-06-01 | 2026-06-01T06:04:12Z 至 2026-06-02T04:27:10Z | `eastmoney.nav_history` 527,211；`akshare.fund_open_fund_info_em` 872 |
| 快照收益 / 起购金额 / 股票代码列表 | `snapshots` | 26,935 | 26,935 / 26,953 = 99.93% | 快照型数据，以抓取时点为准 | 2026-06-01T17:11:06Z 至 2026-06-02T04:27:09Z | `eastmoney.snapshot` 26,935 |
| 股票持仓 | `stock_holdings` | 2,467,012 | 13,195 / 26,953 = 48.96% | `report_period`: 2024-03-31 至 2025-12-31 | 2026-06-01T06:04:12Z 至 2026-06-01T21:30:53Z | `akshare.fund_portfolio_hold_em` |
| 债券持仓 | `bond_holdings` | 546,502 | 15,369 / 26,953 = 57.02% | `report_period`: 2024-03-31 至 2025-12-31 | 2026-06-01T06:04:13Z 至 2026-06-01T21:30:53Z | `akshare.fund_portfolio_bond_hold_em` |
| 行业配置 | `industry_allocations` | 415,444 | 13,247 / 26,953 = 49.15% | `report_period`: 2024-03-05 至 2025-12-31 | 2026-06-01T06:04:13Z 至 2026-06-01T21:30:53Z | `akshare.fund_portfolio_industry_allocation_em` |
| 费率结构 | `fee_structures` | 80,097 | 26,929 / 26,953 = 99.91% | 费率表型数据，以抓取时点为准 | 2026-06-01T06:04:15Z 至 2026-06-01T21:30:56Z | `eastmoney.fund_fee_page` 58,607；`akshare.fee_fallback:etf_no_data` 13,364；`akshare.fund_fee_em` 8,126 |
| 分红 | `dividends` | 52,347 | 7,702 / 26,953 = 28.58% | `dividend_date`: 2002-04-19 至 2026-06-03 | 2026-06-01T06:04:17Z 至 2026-06-01T21:30:56Z | `akshare.fund_open_fund_info_em:分红送配详情` |
| 拆分 / 折算 / 转换 | `splits` | 1,740 | 589 / 26,953 = 2.19% | `split_date`: 2005-02-04 至 2026-06-10 | 2026-06-01T06:04:17Z 至 2026-06-01T18:57:16Z | `akshare.fund_open_fund_info_em:拆分详情` |
| 基金经理 | `fund_managers` | 34,654 条经理记录 | 26,641 / 26,953 = 98.84% 可解析到当前基金池 | 当前经理列表，以抓取时点为准 | 2026-06-01T17:16:21Z 至 2026-06-01T21:31:08Z | `akshare.fund_manager_em` |

补充说明：

- `fund_profiles` 在当前已校验 bundle 中已经覆盖完整。大多数记录来自
  AkShare 的 Eastmoney F10 档案抓取；只有 8 行的最后来源是
  `investoday.fund_all`。
- `fund_managers.current_fund_codes` 是按经理组织的 CSV 文本，不是
  fund-centric 关联表。当前表里有 26,645 个经理侧基金代码，其中
  26,641 个能解析到当前 `funds` 基金池。
- 分红和拆分的最大日期可能晚于 bundle 拉取日期，因为上游可能披露未来
  计划执行的权益事件。

## 缺失的数据

### 可补齐或接近可补齐的缺口

| 缺口 | 缺失基金数 | 主要形态 | 建议动作 |
|---|---:|---|---|
| `nav_history` | 653 | 主要是后端份额类、稀疏产品和新产品。例如 `000002`、`000012`、`000108`、`000140`、`000154`。 | 只有 provider 能力变化后才值得重试；不要把每个缺失净值都当成 nightly 故障。 |
| `snapshots` | 18 | 17 只类型未识别的新基金，加 1 只货币型基金。 | 等 Eastmoney 更新 `fundcode_search` / snapshot 页面后重跑；也可以用基金名称做类型兜底识别。 |
| `fee_structures` | 24 | 17 只类型未识别的新基金，加 7 只货币型基金。 | 定期重试费率页抓取；当前属于很小的尾部缺口。 |
| `fund_managers` | 312 个当前基金无法从经理表解析 | 主要是后端份额类、未识别新基金和少量普通基金。 | 增加 fund-centric 物化表或 view，让“基金 -> 经理”查询成为 O(1)，再补尾部。 |
| Query bundle 新鲜度 | 已安装 `2026-06-02-1701`；远端 manifest 指向 `2026-06-02T093613Z` 但 artifact 404。 | OSS 发布顺序或一致性问题。 | 先上传 release archive 和 sha256，再发布 `current/manifest.json`；manifest 必须最后发布。 |

### 结构性为空或自然稀疏的数据

这些不应直接视为 bug：

- **股票持仓 / 行业配置** 缺 13.7k 只基金。大量缺失来自纯债、货币、
  REIT、QDII、FOF 或后端份额类，公开披露中本来就没有对应股票或行业
  数据。
- **债券持仓** 缺 11.6k 只基金。很多股票型、指数型产品没有债券持仓表，
  或债券仓位不是主要披露对象。
- **分红** 只覆盖 28.58%，因为多数基金成立以来没有分红记录。
- **拆分** 只覆盖 2.19%，因为基金拆分、折算、转换事件本来就少，且主要
  集中在较老产品。
- **REITs** 在 AkShare 公募基金组合接口中股票、债券、行业均为 0%，这是
  披露体系不同导致的结构性空白。

## 按主要基金类型看覆盖

| 基金类型 | 总数 | 股票持仓 | 债券持仓 | 行业配置 | 解读 |
|---|---:|---:|---:|---:|---|
| 混合型-偏股 | 5,561 | 4,344 | 2,265 | 4,369 | 股票和行业配置较有用，债券持仓是补充。 |
| 指数型-股票 | 5,345 | 2,602 | 1,270 | 2,606 | 缺口多来自指数份额类别或披露稀疏产品。 |
| 债券型-长债 | 3,520 | 0 | 3,154 | 0 | 股票和行业缺失是设计如此，债券表是主表。 |
| 混合型-灵活 | 2,397 | 2,189 | 1,508 | 2,203 | 股票和行业覆盖较强。 |
| 债券型-混合二级 | 1,779 | 1,062 | 1,200 | 1,064 | 股票和债券都可能重要。 |
| 混合型-偏债 | 1,397 | 1,186 | 1,249 | 1,184 | 多资产覆盖较好。 |
| 股票型 | 1,105 | 945 | 507 | 951 | 股票和行业是主数据。 |
| 债券型-中短债 | 1,009 | 0 | 947 | 0 | 股票和行业缺失是设计如此。 |
| 货币型-普通货币 | 967 | 0 | 870 | 0 | 股票和行业缺失是设计如此。 |
| 债券型-混合一级 | 949 | 64 | 789 | 64 | 以债券为主。 |
| 指数型-固收 | 670 | 0 | 537 | 0 | 固收指数产品，股票和行业通常为空。 |
| FOF-稳健型 | 654 | 142 | 337 | 143 | 主要持有其他基金，直接股票/债券行是部分覆盖。 |
| Reits | 80 | 0 | 0 | 0 | AkShare 公募基金组合接口不覆盖 REIT 披露。 |

## 本地 full DB 审计表

Cloud query bundle 不包含审计表。本地 full DB
`fund-data/data/fund_data.sqlite` 保留这些表：

| 审计表 | 行数 | 时间范围 | 说明 |
|---|---:|---|---|
| `raw_responses` | 51,962 | 2026-06-01T17:11:06Z 至 2026-06-02T05:50:47Z | 上游原始响应，用于审计和 parser replay。 |
| `sync_runs` | 26,351 | 2026-06-01T17:11:06Z 至 2026-06-02T08:08:48Z | 每次 sync 的执行日志。 |
| `sync_failures` | 8 | 2026-06-02T08:17:57Z 至 2026-06-02T08:18:14Z | 8 条都是 `fund_profile_backfill.profile` 失败，来源为 `akshare.fund_overview_em`，根因是 Eastmoney F10 SSL EOF；涉及 `025952`、`025953`、`025967`、`025968`、`025969`、`025970`、`025971`、`025972`。 |

这 8 条本地 full DB 失败不会出现在已校验的 cloud query bundle 里，因为
query bundle 只发布业务查询表，不发布失败队列。

## 重新生成命令

刷新快照并复算核心数字：

```bash
python3 fund-data/scripts/fund_cli.py cloud pull
python3 fund-data/scripts/fund_cli.py cloud status \
  --manifest-url https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json

DB=$(PYTHONPATH=fund-data python3 - <<'PY'
from scripts import fund_data
print(fund_data.default_db_path())
PY
)

sqlite3 "$DB" "select count(*) from funds;"
python3 fund-data/scripts/fund_cli.py coverage-report --limit 20
```

查询 operator-only 审计表：

```bash
sqlite3 fund-data/data/fund_data.sqlite \
  "select operation, provider, count(*) from sync_failures group by operation, provider;"
```
