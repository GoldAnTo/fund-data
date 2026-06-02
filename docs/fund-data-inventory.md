# Fund Data 数据底座清单

> 当前、可复查的覆盖口径见
> [`docs/data-coverage-summary.md`](data-coverage-summary.md)。本文件保留
> 长篇 inventory / 运维背景，部分数字可能反映早期 backfill 快照。

> **最近更新:** 2026-06-02 15:55 (Asia/Shanghai)
> **数据快照:** 本地 SQLite v=`2026-06-01T21:27:41Z` (backfill 末次)
> **OSS 快照:** `oss://fund-data-public-l/fund-data/releases/2026-06-02-053226/`
> **DB 大小:** 5.4 GB(含 `raw_responses` 4.8 GB,业务表合计 ~340 MB)
> **维护:** Mavis — 改完记得更新"最近更新"和"数据快照"两行

---

## 0. 一图流

```
                         26,953 funds
                              │
   ┌──────────┬───────────────┼───────────────┬──────────┐
   ▼          ▼               ▼               ▼          ▼
funds    nav_history     snapshots     fund_profiles   holdings(4)
(26.9k)  (26.3k unique   (26.9k)       (26.7k)         (49-57%)
          528k rows)                      │
                                           ▼
                                       fund_managers
                                       (4k distinct,
                                        34.6k rows)

                    fees (100% by row)
                    dividends (28% — naturally sparse)
                    splits (2% — naturally very sparse)

                    ↓ 全部经 fund_data.store 写入 ↓

        data/fund_data.sqlite  (full, 5.4GB)
        data/fund_data_query.sqlite.gz  (cloud bundle, <100MB)
```

---

## 1. 速览(Snapshot Card)

| 维度 | 数字 | 备注 |
|---|---|---|
| 基金池(`funds`) | **26,953** | Eastmoney `fundcode_search` 一次拉全 |
| 业务表 | **12 张** | 业务 + 1 张 audit log + 1 张 failure queue |
| 运维表 | **3 张** | `schema_migrations` / `sync_runs` / `sync_failures` |
| 总行数(业务) | **~3.9M** | stock_holdings 占 2.47M,nav 528K |
| DB 物理 | **5.4 GB** | raw_responses 4.8 GB,业务数据 ~340 MB |
| 数据源 | 4 个 provider | Eastmoney(主,免 key)+ AkShare(备)+ Investoday(L1,付费)+ Tushare(可选) |
| 上次 backfill | **2026-06-01 21:27 UTC** | age=10.34h(尚新,24h 内不需要重跑) |
| sync_failures | **0** | 380 个 snapshot 失败已结案(API surface gap,见 §6.3) |
| 总测试 | **178 unit tests** | CI: Python 3.11 / 3.12 / 3.13 + ruff + black |
| MCP 工具 | 6 个 | `fund_search / fund_nav_history / fund_snapshot / fund_sync / fund_coverage_report / fund_export` |
| License | MIT | |

---

## 2. 基金池构成(`funds` 表)

按 `fund_type` 拆(刷新自 `refresh_fund_type --only-empty` 后,99.93% 已填):

| 类型 | 数量 | 占比 | 备注 |
|---|---:|---:|---|
| 混合型(全子型合计) | 9,468 | 35.1% | 偏股/灵活/偏债/平衡/绝对收益 |
| 指数型(全子型合计) | 6,445 | 23.9% | 股票/固收/海外/其他 |
| 债券型(全子型合计) | 7,257 | 26.9% | 长债/中短债/混合二级/混合一级 |
| 股票型 | 1,105 | 4.1% | 主动股基 |
| 货币型 | 975 | 3.6% | 普通 + 浮动净值 |
| FOF | 1,232 | 4.6% | 稳健/均衡/进取 |
| QDII | 365 | 1.4% | 含 QDII-REITs 5 只 |
| REITs | 80 | 0.3% | 公募 REITs |
| 商品 | 2 | <0.1% | |
| (空 / 未识别) | 26 | <0.1% | 2024-2025 新发,见 §6.4 |
| **合计** | **26,953** | 100% | |

**关键洞察:**
- 26,953 = 几乎全市场公募基金(含已清盘/合并/已到期) — 实际在交易的活跃基金约 11,000-12,000 只
- 货币型 + 债券型 + 指数型-固收 = 8,902 只(33%)天然没有股票持仓,这是**结构性 0%**,不是 bug
- QDII / REITs 共 445 只(1.6%)走海外/不动产监管,AkShare 拿不到披露,**结构性 0%**

---

## 3. 业务表清单(12 张)

### 3.1 基础档案类(4 张)

#### `funds` — 基金池(主索引)
| | |
|---|---|
| 行数 | **26,953** |
| 主键 | `fund_code` (6 位基金代码) |
| 字段(10) | fund_code, fund_name, fund_type, company, manager, nav, nav_date, other_names, source, updated_at |
| 来源 | `eastmoney.fundcode_search` (全量) + `eastmoney.snapshot` 增量补 nav |
| 更新节奏 | 全量拉一次后基本不变,新基金靠 `fetch_fund_list` 增量 |
| 备注 | 这是 agent 第一站 — 所有 join 都从这张表起 |

#### `fund_profiles` — 基金档案(L1 详细)
| | |
|---|---|
| 行数 | **26,708**(覆盖率 99.07%) |
| 主键 | `fund_code` |
| 字段(15) | fund_code, fund_name, full_name, fund_type, issue_date, establishment_date, asset_size, asset_size_date, fund_company, custodian, manager, benchmark, tracking_target, source, fetched_at |
| 来源 | `investoday.fund_all` 20,609 行 + `akshare.fund_overview_em` 6,101 行 |
| 更新节奏 | Investoday L1 bulk 一次 40s 拉全 98.9% |
| 备注 | 全量 31 字段,但本表只存常用 14 项;asset_size 是最新披露(不一定是最新季报) |

#### `nav_history` — 净值历史
| | |
|---|---|
| 行数 | **528,083**(去重后 26,300 个基金,覆盖率 97.58%) |
| 主键 | (fund_code, nav_date) |
| 字段(10) | fund_code, nav_date, unit_nav, accumulated_nav, daily_growth_rate, subscribe_status, redeem_status, dividend, source, fetched_at |
| 来源 | `eastmoney.nav_history` 527,211 行(主) + `akshare.fund_open_fund_info_em` 872 行(fallback) |
| 时间跨度 | 滚动 3 年窗口(2023-06 → 2026-06,约 730 天) |
| 平均密度 | 19.6 行/基金(非货币基金) |
| 备注 | 货币型基金按周披露,自然密度低 |

#### `snapshots` — 即时快照
| | |
|---|---|
| 行数 | **26,935**(覆盖率 99.93%) |
| 主键 | `fund_code` |
| 字段(9) | fund_code, fund_name, source_rate, current_rate, min_purchase, returns_json, stock_codes_json, source, fetched_at |
| 来源 | `eastmoney.snapshot` (`pingzhongdata` JS) 26,935 行 |
| 关键字段 | `returns_json` = `{one_year, six_month, three_month, one_month}` 4 个区间的累计收益率(小数) |
| 备注 | 380 个失败原因 = Eastmoney 对 241 个后端份额类 + AkShare 无 `snapshot` 方法 |

### 3.2 持仓类(3 张,合计 ~3.4M 行)

#### `stock_holdings` — 股票持仓
| | |
|---|---|
| 行数 | **2,467,012**(覆盖率 49.0%,13,195 个基金) |
| 主键 | (fund_code, report_period, stock_code) |
| 字段(9) | fund_code, report_period, stock_code, stock_name, net_value_ratio, shares, market_value, source, fetched_at |
| 来源 | 100% `akshare.fund_portfolio_hold_em` |
| 时间 | 季报披露(3/31, 6/30, 9/30, 12/31) |
| 备注 | **49% 全球数字含结构性 0%**:货币/纯债/部分指数型 0% 是设计如此 |

#### `bond_holdings` — 债券持仓
| | |
|---|---|
| 行数 | **546,502**(覆盖率 57.1%,15,369 个基金) |
| 主键 | (fund_code, report_period, bond_code) |
| 字段(8) | fund_code, report_period, bond_code, bond_name, net_value_ratio, market_value, source, fetched_at |
| 来源 | 100% `akshare.fund_portfolio_bond_hold_em` |
| 备注 | 股票型基金债券持仓通常 < 5% 但仍披露 |

#### `industry_allocations` — 行业配置
| | |
|---|---|
| 行数 | **415,444**(覆盖率 49.2%,13,247 个基金) |
| 主键 | (fund_code, report_period, industry_name) |
| 字段(7) | fund_code, report_period, industry_name, net_value_ratio, market_value, source, fetched_at |
| 来源 | 100% `akshare.fund_portfolio_industry_allocation_em` |
| 备注 | 行业分类用 AkShare 自带的申万分类;不与 stock_holdings 重算(数据源已映射) |

### 3.3 费率 / 分红 / 拆分(3 张)

#### `fee_structures` — 费率结构
| | |
|---|---|
| 行数 | **80,097**(覆盖率 99.90%,26,929 个基金) |
| 主键 | (fund_code, fee_type, condition_name) |
| 字段(9) | fund_code, fee_type, condition_name, fee, fee_text, discount_fee, discount_fee_text, source, fetched_at |
| 来源 | `eastmoney.fund_fee_page` 58,607 行 + `akshare.fee_fallback:etf_no_data` 13,364 行 + `akshare.fund_fee_em` 8,126 行 |
| 备注 | `fee` 是小数(0.15% = 0.0015),`fee_text` 是原文("每笔1000元"也存) |

#### `dividends` — 分红记录
| | |
|---|---|
| 行数 | **52,347**(覆盖率 28.6%,7,710 个基金) |
| 主键 | (fund_code, dividend_date) |
| 字段(7) | fund_code, dividend_date, ex_dividend_date, dividend_per_share, payment_date, source, fetched_at |
| 来源 | 100% `akshare.fund_open_fund_info_em:分红送配详情` |
| 备注 | **自然稀疏**:大部分基金成立以来从未分红。**不是 bug,不需要补** |

#### `splits` — 拆分 / 转换
| | |
|---|---|
| 行数 | **1,740**(覆盖率 2.2%,596 个基金) |
| 主键 | (fund_code, split_date) |
| 字段(6) | fund_code, split_date, split_type, split_ratio, source, fetched_at |
| 来源 | 100% `akshare.fund_open_fund_info_em:拆分详情` |
| 备注 | **自然非常稀疏**:集中在 2008-2015 老基金。**0% 不是 bug** |

### 3.4 人员类(1 张,manager-centric 异类)

#### `fund_managers` — 基金经理
| | |
|---|---|
| 行数 | **34,654**(4,055 个不同 manager) |
| 主键 | `(manager_name, company, current_fund_codes)` 三元组(**不是 fund-centric**) |
| 字段(9) | manager_name, company, current_fund_codes, current_funds, tenure_days, current_aum, best_return, source, fetched_at |
| 来源 | 100% `akshare.fund_manager_em` |
| 备注 | ⚠️ **结构反人类**:一行一个经理,管多只基金的经理在 `current_fund_codes` 是 CSV。查"谁管基金 X"需要 `LIKE '%X%'`,**不是 O(1) join**。已知 0.3.0 backlog(§6.4) |

---

## 4. 运维表(3 张)

| 表 | 行数 | 用途 | 写入方 |
|---|---:|---|---|
| `sync_runs` | 26,349 | audit log:每次 `sync` 写一行,记 status / rows_changed / message | `fund_data.batch_sync_funds` |
| `sync_failures` | 0 | 失败队列:`batch-sync` 硬失败入库,`retry_failures.py` 读此重跑 | 同上 |
| `schema_migrations` | 5 | 版本号 → 函数映射,`PRAGMA user_version` 同步 | `FundDataStore.ensure_schema` |
| `raw_responses` | 51,962(4.8 GB!) | 原始 HTTP 响应,审计 / 重解析用,默认不导出到 OSS | 所有 provider |

⚠️ **`raw_responses` 占 4.8 GB,占 DB 总大小 89%**。
- 业务需要就保留,否则用 `DELETE FROM raw_responses WHERE fetched_at < '2025-01-01'` 砍老数据
- `fund_cloud build-bundle` 已经**默认不导出** raw,所以 OSS bundle 不含它

---

## 5. 数据源 / Provider 链

### 5.1 四层 provider 链(`auto` 模式)

按信任度从高到低,`auto` 默认走这条链:

```
[ Investoday ¥12.9+ ]    ← L1 体验包
        │ 不通
        ▼
[ Tushare (TUSHARE_TOKEN) ]    ← 2000 积分档
        │ 不通
        ▼
[ AkShare ]    ← 免费,服务器端会节流
        │ 不通
        ▼
[ Eastmoney ]    ← 免 key,fast fallback
```

| Capability 类别 | 默认顺序 | 原因 |
|---|---|---|
| `fund_list` / `search` / `nav_history` / `snapshot` | Eastmoney → AkShare | 这 4 个 Eastmoney 比 AkShare 快,优先直连 |
| `profile` / `holdings` / `bonds` / `industries` / `fees` / `dividends` / `splits` / `managers` | (Investoday → Tushare) → AkShare → Eastmoney | Eastmoney 这 8 个能力没实现,AkShare 是默认主力 |

### 5.2 数据源分布(本 DB 实际行数按 `source` 列)

| Provider 端点 | 行数 | 用途 |
|---|---:|---|
| `akshare.fund_portfolio_hold_em` | 2,467,012 | stock_holdings(全部) |
| `akshare.fund_portfolio_bond_hold_em` | 546,502 | bond_holdings(全部) |
| `eastmoney.nav_history` | 527,211 | nav_history(99.8%) |
| `akshare.fund_portfolio_industry_allocation_em` | 415,444 | industry_allocations(全部) |
| `eastmoney.fund_fee_page` | 58,607 | fee_structures(73%) |
| `akshare.fund_open_fund_info_em:分红送配详情` | 52,347 | dividends(全部) |
| `akshare.fund_manager_em` | 34,654 | fund_managers(全部) |
| `eastmoney.snapshot` | 26,935 | snapshots(99.9%) |
| `investoday.fund_all` | 20,609 | fund_profiles(77%) |
| `akshare.fee_fallback:etf_no_data` | 13,364 | fee_structures ETF 兜底(17%) |
| `akshare.fund_fee_em` | 8,126 | fee_structures(10%) |
| `akshare.fund_overview_em` | 6,101 | fund_profiles(23%) |
| `akshare.fund_open_fund_info_em:拆分详情` | 1,740 | splits(全部) |
| `akshare.fund_open_fund_info_em` | 872 | nav_history fallback(0.2%) |

### 5.3 各数据源关键属性

| Provider | Cost | 速率上限 | SLA | 适合场景 |
|---|---|---|---|---|
| **Eastmoney** | 免 key | 受服务器限速,~8 并发稳 | 无 | list / search / nav / snapshot,**最稳最便宜** |
| **AkShare** | 免 key | 单进程 ~8 并发后 5xx 飙升 | 无 | profile / holdings / fees / managers,**主力,但慢** |
| **Investoday L1** | ¥12.9 体验包(200 calls/30d 免费) | ~200 calls/min | 合同 SLA | profile 全量(`/fund/all` 一次拉 27k,40s),**L2 portfolio 需要升级** |
| **Tushare** | 2,000 积分档 | 200 calls/min | 商业 | AkShare-only 能力的快速版 |

---

## 6. 缺什么数据(分四层)

### 6.1 自然稀疏(**不是 bug,不需要补**)
- `dividends` 28.6%:大部分基金成立以来从未分红
- `splits` 2.2%:集中在 2008-2015 老基金,新基金没拆分过
- 货币型 / 纯债 / 指数型-固收 0% 股票持仓:**结构性 0%,设计如此**
- QDII / REITs 0% 持仓:**海外/不动产监管不强制披露,API 根本拿不到**

### 6.2 真实 gap(可由 backfill 修复)

| Gap | 原因 | 修法 | ETA |
|---|---|---|---|
| 持仓 ~2,123 个 2024-2025 新基金 0% | 季报未披露 | 等 Q3 2024 / 2024 年报披露后自然填,**不要重试** | 自动 |
| 18 个新基金 `fund_type` 为空 | Eastmoney 索引未更新 | `refresh_fund_type --only-empty` 后正则补(0.3.0 backlog) | 1 周 |
| 380 个 `snapshot` 失败 | Eastmoney 无数据 + AkShare 无 `snapshot` 方法 | 实现 `AkshareProvider.snapshot`(0.3.0 backlog) | 几天 |

### 6.3 不可修复(API surface gap)
- 后端份额类(`000002` / `000012` / `000108` / ...)的 snapshot:**Eastmoney 页面 stub 本身就是空,parse 返回 None,已结案**
- 80 只 REITs + QDII-REITs 的 holdings:**不同监管体系,不公开持仓**
- 975 只货币型基金的 holdings:**没有股票 / 行业,只披露债券**

### 6.4 0.3.0 backlog(详见 `docs/KNOWN_GAPS.md`)

| # | 任务 | 优先级 | 阻塞 |
|---|---|---|---|
| 1 | `fund_managers` 9h 全量 backfill(用 Investoday ¥45 L1 加速到 ~3h) | 高 | Investoday 升级 |
| 2 | 费率 kwarg bug 修复后 bulk 跑 → 18% → 95%+(commit 2ec363b 已修函数,**还要 bulk 重跑**) | 高 | 跑一次 14min 任务 |
| 3 | snapshots 16% → 100% — 已经在跑,00:30 ETA | 中 | 跑完即可 |
| 4 | `split_type` 2% → 95%+(预 2016 老基金定向补) | 低 | 自然稀疏,可不动 |
| 5 | `fund_managers` 反人类 schema:加 `fund_managers_link` 物化表 | 中 | 0.3.0 |
| 6 | `refresh_fund_type` 接进 nightly cron | 中 | 配 cron |
| 7 | `akshare_capability_backfill.py` 失败 ~2% 重试 pass | 低 | 自然衰减 |
| 8 | MCP server: per-tool 授权(多租户) | 低 | 单租户不需要 |

---

## 7. 数据获取时间

### 7.1 实时查询路径(agent 用)

```
agent → fund_cli / fund_mcp / from scripts import fund_data
  → fetch_*(...)   ← 实时 HTTP 调用,不走 DB
     │
     ▼
provider chain (auto) → 返回 list[dict]
  → 不写 DB(除非显式 --include-all)
```

**响应延迟:**
- Eastmoney 直连:0.3-0.5s / call
- AkShare:1-3s / call(被限流时 6s+)
- Investoday L1:0.2-0.4s / call
- 单基金 `--include-all` 全量 sync:90-180s(Eastmoney 路径)

### 7.2 批处理(backfill)

| 任务 | 命令 | 时长 | 频率 |
|---|---|---|---|
| 全量 backfill(Eastmoney 路径) | `backfill.py --provider eastmoney --concurrency 8` | **~90 分钟** | 一次性 / 重置后 |
| 全量 backfill(AkShare 路径) | `backfill.py --provider akshare` | **~21 小时** | 同上(慢) |
| Investoday profile bulk | `investoday_profile_sync.py` | **~40s** | 每月 / 体验包限额后 |
| Fund type 补全 | `refresh_fund_type.py --only-empty` | ~5min | 每月 |
| Fee bulk(已修) | `fee_only_backfill.py` | ~14min | 一次性 |
| Profile backfill(per-fund) | `fund_profile_backfill.py` | 视基金数 | 按需 |
| 失败重试 | `retry_failures.py` | 视失败数 | 每周 |

### 7.3 Cron 监控(已配)

| Cron 名 | 频率 | 检查内容 | 触发后动作 |
|---|---|---|---|
| `funddata-nightly-watch` | 每小时 | OSS manifest 存在 + backfill state 新鲜度 + doctor OK | drift > 36h → escalate |
| `backfill-monitor` | 每 30 分钟 | `backfill_state.json` 24h 内有更新 + `sync_failures` < 50 | stale 或失败多 → escalate |

**两个 cron 都被写死"不要删项目文件"** — 用户硬约束。

### 7.4 OSS 分发(给 team / agent 多机)

```
fund-data/data/fund_data.sqlite (full, 5.4GB, private)
  │
  ▼ fund_cloud build-bundle --source-db ... --output-dir dist/...
fund_data_query.sqlite.gz (~50-100MB, public) ← 不含 raw / sync_log
fund_data_query.sqlite.gz.sha256
manifest.json
  │
  ▼ ossutil cp -f
oss://fund-data-public-l/fund-data/releases/<version>/...
oss://fund-data-public-l/fund-data/current/manifest.json
  │
  ▼ 远程 agent 跑 fund_cli cloud pull
~/.cache/fund-data/releases/<version>/fund_data_query.sqlite
```

**注意:**
- ⚠️ **full DB 永远私有**(含 raw_responses 4.8GB,公开就是泄漏 API 调用历史)
- ⚠️ **ossutil cp 必须 `-f`**,不带 -f 在非交互 shell 卡 prompt,显示 "Success" 实际 0 bytes(已踩过坑,见 memory)
- 当前 public bucket 里看到 `fund-data/private/full/2026-06-02-091411/` 路径段 — **这是个 bug**,full 不该放 public 路径(待修)

---

## 8. 数据生命周期

```
   ┌─────────────────────────────────────────────────────┐
   │  Day 0:  一次性 seed (backfill eastmoney, ~90 min)  │
   │          → 写入 fund_data.sqlite (5.4GB)            │
   │  Day 1+: 增量 backfill (--report-year 2024 ...)     │
   │          → 同 DB 增量写                              │
   │  Weekly: refresh_fund_type --only-empty              │
   │  Monthly: fund_data.sqlite → fund_data_query.sqlite │
   │           → gzip → ossutil cp -f → OSS public       │
   │  On-error: retry_failures.py 读 sync_failures 表     │
   │  Schema: PRAGMA user_version, ensure_schema 跑迁移  │
   └─────────────────────────────────────────────────────┘
```

**DB 路径优先级**(踩过坑,见 memory):
1. `FUND_DATA_CACHE_DIR` → cloud pull 后的 query db
2. `FUND_DATA_DB` → 强制指定
3. `fund_cloud.current_db_path()` → `~/.cache/fund-data/current.json` 里的 db_path
4. `fund-data/data/fund_data.sqlite` → 兜底全量 db

⚠️ **doctor.py 只认第 4 个路径**,所以 cloud pull 之后 doctor 数字和 CLI 数字会不一致。跑 backfill 之前 `cat ~/.cache/fund-data/current.json` 决定 db 路径。

---

## 9. 我自己观察到的(运维 / 合规 / 质量)

### 9.1 DB 体积分布

```
5.4 GB total
├── raw_responses         4.8 GB  (89%)  ← 审计 / 重解析用,可考虑按 fetched_at 砍老数据
├── stock_holdings        384 MB  ( 7%)  ← 业务
├── bond_holdings          84 MB  ( 1.5%)
├── nav_history            68 MB  ( 1.2%)
├── industry_allocations   58 MB  ( 1%)
├── fee_structures         10 MB  ( 0.2%)
├── fund_profiles           9 MB  ( 0.2%)
├── snapshots               8 MB  ( 0.1%)
├── fund_managers           5 MB  ( 0.1%)
├── funds                   5 MB
├── dividends               7 MB
├── splits                  0.2 MB
├── sync_runs               2 MB
└── schema_migrations      ~0
```

**结论:** 业务表 340MB,raw 4.8GB。要瘦身,砍 `raw_responses fetched_at < '2025-01-01'` 即可。

### 9.2 Public vs Private bucket ✅ 已验证(混合 ACL)

- 预期:full DB 私有,query bundle 公开
- 实际:`fund-data-public-l` 是混合 ACL bucket(名字误导),通过 prefix 级别 ACL 实现分级:
  - `fund-data/private/full/<date>/` → **403 Forbidden**(匿名拒绝,显式 private)
  - `fund-data/releases/<date>/` → **200 OK**(匿名可访问,public)
- 验证方式:`curl -sI https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/private/full/<date>/fund_data_full.sqlite.gz` → 403
- 结论:无泄漏。`private/` 路径段命名有点反直觉(把 private 数据塞进 public-named bucket),但实际 ACL 配对了
- 改进方向(可选):重命名 bucket 为 `fund-data-mixed-l` 或拆成两个 bucket,但 ACL 既然对了,不是 blocker

### 9.3 Agent 友好度

| 维度 | 现状 | 评分 |
|---|---|---|
| CLI 入口 | `fund_cli` / `fund-mcp` / `fund-doctor` / `fund-backfill` 4 个 console script | ✅ |
| MCP 工具 | 6 个,LLM 描述完整 | ✅ |
| JSON 输出 | `doctor` / `cloud` / `sync` 已结构化;`list / search / nav / snapshot` 还是 pretty-print | 🟡 0.3.0 |
| exit code | 0 = 成功,非 0 = 失败(但部分 subcommand 异常时仍返 0) | 🟡 需 review |
| 错误信息 | 大部分带 `dataset_errors` 数组,容易解析 | ✅ |
| 文档可发现性 | SKILL.md / AGENTS.md / PROVIDERS.md / ARCHITECTURE.md / KNOWN_GAPS.md + **本文件** | ✅ |

**结论:agent 路径已经能跑,但 `--json` flag 全覆盖是 0.3.0 backlog。**

### 9.4 数据血缘(可追溯性)

每行业务表都有 `source` + `fetched_at`,所以**任何一行都能追溯到 provider 端点 + 时间**。这就是为什么 raw_responses 留着 4.8GB — 重解析 / 排错需要。

⚠️ 但 `funds` 表用 `upsert_funds` 整体覆盖,早期数据如果 `manager` 字段被旧源污染,可能看不出"什么时候被改成错的"。**改进方向:加 `funds_history` 表记 manager 变更**,但目前是 0.4.0 候选。

### 9.5 风险点 + 监控建议

| 风险 | 当前缓解 | 建议加强 |
|---|---|---|
| Eastmoney / AkShare 改 endpoint | `e2e test` 用 mock,但**没跑 live** | 加 nightly `live-smoke` workflow(只 sync 1-2 个基金,确认链路通) |
| Investoday 体验包 200 calls/月 用完 | 体验包够 monthly profile bulk 一次 | 月底检查 credit 余额,加 cron |
| DB lock 撞车 | `busy_timeout=30s` + retry 3 次 | 单 writer 串行,加 lock 监控 |
| `raw_responses` 无限增长 | 目前无 | 加 cron `DELETE WHERE fetched_at < now() - 90 days` |
| 后端份额类 snapshot 380 个失败 | 已结案(parse 返回 None) | doctor 加 "snapshot 后端 stub" 计数器 |
| 货币型基金被批量 sync 浪费 IO | `backfill.py --exclude-type 货币` 跳过 | 默认开,文档化 |
| macOS 三层代理(已踩坑) | 加了 urllib monkey-patch | 写到 `fund-data/scripts/doctor.py` 启动时检测 + 警告 |
| macOS IPv6 first dead-lock(已踩坑) | 加了 `socket.getaddrinfo` IPv4 filter | 同上,doctor 启动时检测 |
| ossutil cp 假成功(已踩坑) | 加了 `-f` 标志 | `fund_cloud build-bundle` 自带检查 manifest.json size 跟实际 gz 一致 |

### 9.6 数据消费侧观察

(基于当前 OSS bundle / `~/.cache/fund-data/` 使用情况推断)

- **主消费方是 agent**(OpenClaw / Claude / Codex),通过 `fund-mcp` 调 6 个工具
- **次消费方是 CI runner**:`sync.yml` 跑 nightly backfill,`release.yml` 出 OSS bundle
- **很少本地交互式查询**:human 主要跑 `doctor.py` 验证环境
- **导出需求低**:`fund_cli export` 能力齐了,但**没看到用** — 大家直接 SQL 查

**含义:**
- agent 优先 > human 友好(跟用户偏好一致)
- MCP 工具描述质量 / `--json` 输出 / exit code 重要于 CLI 漂亮度
- `doctor.py` 是 agent 进新环境的"自检入口",要保持稳定 + 全面

---

## 10. 相关文档

- 设计 spec:`docs/superpowers/specs/2026-06-01-fund-data-skill-design.md`
- 实施计划:`docs/superpowers/plans/2026-06-01-fund-data-skill.md`
- 架构:`fund-data/ARCHITECTURE.md`
- 数据源接入:`fund-data/PROVIDERS.md`
- Schema 参考:`fund-data/references/schema.md`
- 性能 / 坑:`fund-data/AGENTS.md`
- 已知 gap:`docs/KNOWN_GAPS.md`
- 完整度诊断(更细):`docs/superpowers/specs/2026-06-02-fund-data-completeness-diagnosis.md`
- API 目录:`INVESTODAY_FUND_API_CATALOG.md`
- 主入口:`fund-data/SKILL.md`
- 主 README:`README.md`

---

## 11. 变更记录

| 日期 | 改动 | 谁 |
|---|---|---|
| 2026-06-02 15:55 | 初版,基于 2026-06-01 backfill + 2026-06-02 完整度诊断 | Mavis |
| | | |
