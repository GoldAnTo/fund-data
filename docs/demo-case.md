# fund-data 详细演示案例

> 现场检查日期：2026-06-03 Asia/Shanghai
> 演示目标：用一只真实基金（`110022` 易方达消费行业股票）走通"查询 → 覆盖判断 → 净值/快照/持仓 → 导出 → agent 消费"完整闭环，证明 fund-data 已经不是概念验证，而是一个可被团队和 agent 同时复用的本地基金数据底座。
> 演示时长建议：核心版 5 分钟；完整版 10 分钟；含 MCP/Cloud 现场演示 15 分钟。
> 演示基金：`110022` 易方达消费行业股票（已选好，所有数据集都有数据，最适合做 happy path）

---

## 0. 演示前 5 分钟 — Pre-flight checklist

> 这一节是给你自己准备的"开机流程"。**下午演示开始前先完整跑一遍**，确认网络、数据库、命令路径都活。

### 0.0 数据全景速查（先讲这一节，回答"数据从哪来/查哪些/缺哪些"）

> **这一节是观众 80% 的问题源头**——"数据从哪抓的？""能查什么？""还有什么没补？"三个问题都在这里答。演示时可以先口述一遍，再开始 hero flow。

#### 0.0.1 数据从哪来 — 4 个 provider 走 fallback 链

项目不依赖单一接口，**主链路是 `auto` 模式按 `Eastmoney → AkShare → Investoday → Tushare` 顺序尝试**，每个 provider 失败就自动 fall through。

| Provider | 接入方式 | 主力场景 | 是否要 key |
|---|---|---|---|
| **Eastmoney** | 直接 HTTP | 基金池、快照、费率、净值（部分） | **免 key** |
| **AkShare** | Python 库（v1.18.64） | 档案、持仓、债券、行业、费率、分红、拆分、经理、净值（部分） | **免 key** |
| **Investoday** | 付费 API | 基金池/档案/净值/股票持仓/债券持仓/行业配置（180+ 接口） | **要 `INVESTODAY_API_KEY`** |
| **Tushare** | Python 库 | 基金池/档案/净值/股票持仓/经理 | **要 `TUSHARE_TOKEN`** |

> **讲法**：
> "实际主力是 AkShare 不是 Eastmoney——很多人会以为免 key 的 Eastmoney 应该是主力。看一眼表就能发现：fund_profiles 几乎全来自 `akshare.fund_overview_em`，stock/bond/industry 三个表的 source 全部是 AkShare。**Eastmoney 真正占主力的是 funds 池、snapshot 和 fee_structures 三张**。这个错位是 AkShare 接口覆盖更广造成的，不是设计失误。"

#### 0.0.2 能查哪些数据 — 11 张业务表

> 默认 cloud query bundle 含 11 张业务表；本地 full DB 还有 `raw_responses` / `sync_runs` / `sync_failures` / `schema_migrations` 4 张审计表。

| 数据集 | 表名 | 主要字段 | 对应 CLI |
|---|---|---|---|
| 基金池 | `funds` | fund_code, fund_name, fund_type, company, manager, nav, nav_date, other_names, source, updated_at | `list` / `search` |
| 基金档案 | `fund_profiles` | establishment_date, asset_size, asset_size_date, company, manager, performance_benchmark, source | `profile` |
| 历史净值 | `nav_history` | nav_date, unit_nav, accumulated_nav, daily_growth_rate, source | `nav` |
| 快照 | `snapshots` | source_rate, current_rate, min_purchase, stock_codes[], returns{1m/3m/6m/1y}, source | `snapshot` |
| 股票持仓 | `stock_holdings` | report_period, stock_code, stock_name, net_value_ratio, shares, market_value, source | `holdings` |
| 债券持仓 | `bond_holdings` | report_period, bond_code, bond_name, net_value_ratio, source | `bonds` |
| 行业配置 | `industry_allocations` | report_period, industry_name, net_value_ratio, source | `industries` |
| 费率 | `fee_structures` | fee_type, fee_indicator, condition, rate, source | `fees` |
| 分红 | `dividends` | dividend_date, dividend_per_unit, source | `dividends` |
| 拆分/折算 | `splits` | split_date, split_ratio, source | `splits` |
| 基金经理 | `fund_managers` | manager_name, company, current_fund_codes (CSV), current_aum, best_return, source | `managers` |

> **讲法**：
> "11 张业务表覆盖了基金研究能想到的几乎所有维度——基础信息、时间序列、截面、持仓三个角度（股票/债券/行业）、费率、分红、拆分、经理。每一张都有 CLI 子命令同名对应。"

#### 0.0.3 当前覆盖度（2026-06-03 10:21 实测）

| 数据集 | 行数 | 覆盖基金 / 池 | 覆盖度 | 主力 source |
|---|---:|---:|---:|---|
| `funds` 基金池 | 26,953 | 26,953 | **100.00%** | `eastmoney.fundcode_search` |
| `fund_profiles` | 26,953 | 26,953 | **100.00%** | `akshare.fund_overview_em` |
| `snapshots` | 26,952 | 26,952 | **100.00%** | `eastmoney.snapshot` |
| `fee_structures` | 80,097 | 26,929 | **99.91%** | `eastmoney.fund_fee_page` |
| `nav_history` | 1,318,192 | 26,337 | **97.71%** | `akshare.fund_open_fund_info_em` (主) + `eastmoney.nav_history` |
| `fund_managers` | 34,654 经理 | 26,645 (可解析) | **98.86%** | `akshare.fund_manager_em` |
| `bond_holdings` | 548,975 | 15,426 | **57.23%** | `akshare.fund_portfolio_bond_hold_em` |
| `stock_holdings` | 2,475,195 | 13,255 | **49.18%** | `akshare.fund_portfolio_hold_em` |
| `industry_allocations` | 415,700 | 13,268 | **49.23%** | `akshare.fund_portfolio_industry_allocation_em` |
| `dividends` | 52,347 | 7,702 | **28.58%** | `akshare.fund_open_fund_info_em:分红送配详情` |
| `splits` | 1,740 | 589 | **2.19%** | `akshare.fund_open_fund_info_em:拆分详情` |

**数据时间范围**（2026-06-03 实测）：

- 最新 `nav_date`: **2026-06-02**
- `nav_history` 抓取窗口：2026-06-01 ~ 2026-06-03
- `snapshots` 抓取窗口：2026-06-01 ~ 2026-06-03
- `stock/bond/industry` 抓取窗口：2026-06-01 ~ 2026-06-02
- `dividends/splits` 抓取窗口：2026-06-01

> **讲法**：
> "覆盖率分三档：
> 1. **100% / 接近 100%**：funds、fund_profiles、snapshots、fee_structures、fund_managers——这些是基础信息，**主力源是 Eastmoney 和 AkShare，免 key 就能拉满**；
> 2. **97%~98%**：nav_history——还差 616 只基金，主要是后端份额类、稀疏产品和新基金；
> 3. **<60%**：stock_holdings、bond_holdings、industry_allocations——**主力源 AkShare 当前有 schema drift**（见 0.0.4），抓取卡住；剩下缺的主要是货币型、纯债、REITs 这种天然没有股票持仓的基金。"
>
> "dividends 28.58% 和 splits 2.19% 是天然稀疏——大部分基金成立以来没分过红、没拆过份额，不是 bug。"

#### 0.0.4 还没补全的数据 + 为什么

| 缺口 | 缺失基金数 | 主要原因 | 补齐路径 |
|---|---:|---|---|
| `nav_history` 差 616 只 | 616 | 后端份额类、稀疏产品、新产品 | 等 provider 能力变化后重试；不能每晚 retry |
| `snapshots` 差 1 只 | 1 | 类型未识别的新基金 | 等 Eastmoney 更新 `fundcode_search` / snapshot 页面 |
| `fee_structures` 差 24 只 | 24 | 同上 + 少量货币型 | 定期重试费率页抓取；尾部小缺口 |
| `fund_managers` 差 308 只 | 308 | 后端份额类、未识别新基金 | **需做 fund-centric 物化表或 view**（PR 待开） |
| `stock_holdings` 差 13,698 只 | 13,698 | **AkShare v1.18.64 schema drift** + 货币/纯债/REITs/QDII 天然没有 | 短期：打 patch 或 wrap AkShare 调用；长期：补 Investoday 的 `bond_holdings` / `industry_allocations` 接口 |
| `bond_holdings` 差 11,527 只 | 11,527 | 同上 + 股票型/指数型无债券 | 同上 |
| `industry_allocations` 差 13,685 只 | 13,685 | 同上 + 货币/纯债/REITs 天然没有 | 同上 |
| `dividends` 差 19,251 只 | 19,251 | 结构性稀疏 | **不补**——多数基金没分过红 |
| `splits` 差 26,364 只 | 26,364 | 结构性极稀疏 | **不补**——拆分事件本来就少 |

**当前真实阻塞 — AkShare v1.18.64 schema drift（2026-06-02 起）**：

AkShare 三个接口在当前版本坏了：

| 接口 | 错误 | 根因 |
|---|---|---|
| `fund_portfolio_industry_allocation_em` | `ValueError: Length mismatch: Expected axis has 1 elements, new values have 17 elements` | `reset_index()` 创 1 列 index，然后 `temp_df.columns = [...]`（17 列）失败 |
| `fund_portfolio_bond_hold_em` | `KeyError: '占净值比例'` | Eastmoney 改了这个列名，新名未知 |
| `fund_portfolio_hold_em` | 返回 0 行（不抛错） | API 响应结构变了，没显式 error |

**实际影响**：industry_allocations、bond_holdings、stock_holdings 三张表从 2026-06-02 起抓取冻结（最后一次成功 stock_holdings.fetched_at = 2026-06-02 21:24）。`run_provider_chain` 正确 fall through AkShare → Investoday，但 `investoday.py` 还没实现 `bond_holdings` 和 `industry_allocations`——所以这两个数据集当前**没有下游 provider 在跑**。

**修法选项**：
1. **短期（推荐）**：在 `investoday.py` 加 `bond_holdings` / `industry_allocations` 方法，调用 `/fund/portfolio-bond-holdings` 和 `/fund/portfolio-industry-alloc` 端点。API key 已配好，立即生效。
2. **长期**：把 AkShare 调用包一层 try/except + 显式列名 fallback，不依赖上游版本。

> **演示时怎么用这个**：
> - 如果观众问"为什么 stock/bond/industry 缺这么多"——直接聊到 AkShare drift，给出上面表里"补齐路径"那一列；
> - 如果观众问"接下来做什么"——这就是 PR 1（`fix(akshare)`）和 PR 2（`feat(investoday)`）的源头；
> - **不要回避**——这是项目当前最真实的 known gap，**主动说出来比被问到再解释更显诚意**。



### 0.1 现场环境自检

```bash
# 进入项目根目录
cd /Users/xiongjiali/Desktop/code/fundData

# 一行命令拿到全部自检结果（agent-friendly JSON）
python3 fund-data/scripts/fund_cli.py doctor --quiet
```

**预期关键字段**（逐条念给观众）：

| 字段 | 预期值 | 讲法 |
|---|---|---|
| `database.ok` | `true` | 本地查询库可读 |
| `database.path` | `~/.cache/fund-data/releases/2026-06-02T214538Z/fund_data_query.sqlite` | 默认走已发布的 cloud query bundle，不是空库 |
| `python.version` | `3.13.3` | 运行环境 |
| `providers.eastmoney.ok` | `true` | 免 key 主力源 |
| `providers.akshare.ok` / `degraded_ok` | `false` / `true` | 系统 Python 没装 AkShare 也能跑（degraded 是预期行为） |
| `providers.investoday.ok` | `true` | 已配付费源作为增强 |
| `sync_failures.count` | `0` | 失败队列空 |
| `coverage.total_funds` | `26953` | 基金池规模 |
| `coverage.min_completeness` | `0.25` | 完整度最低的基金也覆盖了 1/4 |
| `backfill_stale.completed` | `25784` | 上一次 backfill 完成度 |
| `backfill_stale.age_hours` | `< 24` | 离现在多久前 |

> **如果某个关键字段对不上**：先别慌，对照 §5 出错应对处理。

### 0.2 确认 OSS 远端状态

```bash
python3 fund-data/scripts/fund_cli.py cloud status
```

**预期关键字段**：

| 字段 | 预期值 | 含义 |
|---|---|---|
| `installed` | `true` | 本地有 bundle |
| `version` | `2026-06-02T214538Z` | 已装版本号（UTC 时间戳） |
| `manifest_url` | `https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json` | 远端 manifest 路径 |
| `update_available` | `true` 或 `false` | 远端是否有新版 |

> 现场讲法："这步不是业务查询，是版本检查。本地装的是哪一版、远端是不是有新版本，一目了然。如果 `update_available=true` 且现场网络好，先 `cloud pull` 升一下；否则直接用本地已校验的 bundle 演示，不影响闭环。"

### 0.3 准备工作目录（避免现场乱）

```bash
mkdir -p /tmp/demo-2026-06-03
```

> 这个目录用来收集演示过程中导出的产物（JSON/CSV），让观众看到"数据真的被拉出来了"。

---

## 1. Hero flow — 用 110022 走通完整闭环（10 分钟版）

> 这是演示的主线。每一步都按 **"命令 → 预期输出 → 讲法 → 时间"** 四段式写。跟着走就稳。

### Step 1：自检（30 秒）

**命令**：
```bash
python3 fund-data/scripts/fund_cli.py doctor --quiet | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('数据库:', d['database']['path'])
print('基金池:', d['coverage']['total_funds'])
print('失败队列:', d['sync_failures']['count'])
print('已装版本:', d.get('default_db', {}).get('version', 'N/A'))
"
```

**预期输出**（示例）：
```
数据库: /Users/xiongjiali/.cache/fund-data/releases/2026-06-02T214538Z/fund_data_query.sqlite
基金池: 26953
失败队列: 0
已装版本: 2026-06-02T214538Z
```

**讲法**：
> "这是 agent 视角的自检。`doctor` 永远输出 JSON 到 stdout，exit code 反映 ok 状态——所以我可以用管道继续处理。我没有写一行定制校验代码，agent 跑这条命令就能判断环境是否就绪。这就是我们说的'agent-friendly 默认行为'。"

**要点**：
- ✅ JSON-only stdout：stderr 是日志，stdout 是数据，agent 解析无歧义
- ✅ 失败码语义清晰：所有 ok=true 时退出码是 0
- ✅ 演示里"agent 怎么判断环境健康"——这一条就回答了

---

### Step 2：单只基金覆盖报告（1 分钟）

**命令**：
```bash
python3 fund-data/scripts/fund_cli.py coverage-report --code 110022
```

**预期输出**（已用真实验证，2026-06-03）：
```json
{
  "total_funds": 1,
  "fully_covered": 0,
  "average_completeness": 0.75,
  "rows": [
    {
      "fund_code": "110022",
      "fund_name": "易方达消费行业股票",
      "fund_type": "股票型",
      "has_profile": 1,
      "nav_rows": 284,
      "stock_holding_rows": 146,
      "bond_holding_rows": 12,
      "industry_rows": 20,
      "fee_rows": 4,
      "dividend_rows": 0,
      "split_rows": 0,
      "completeness": 0.75,
      "missing": ["dividends", "splits"]
    }
  ]
}
```

**讲法**：
> "这只基金我选了 110022，易方达消费行业股票——算是 A 股消费主题里最知名的一只。`coverage-report` 不是只告诉我'查得到'，而是把'有什么、缺什么、缺多少'一次说清楚。可以看到：基础档案有、净值 284 行、股票持仓 146 行、债券持仓 12 行、行业配置 20 行、费率 4 行，整体完整度 0.75。缺的两项是分红和拆分。"
>
> "这是项目设计上一个很重要的取舍——**不把空数据假装成完整数据**。'有'和'缺'都显式说出来。同时要注意：分红和拆分天然稀疏，对一只成立十几年但分红次数很少的主动基金来说，缺失不一定是 bug。我们后续会用 fund_type-aware 的矩阵给缺失打'结构性为空'或'真实缺口'的标签。"

**如果观众问"为什么完整度 0.75 这么算？"**：
> "分母是 8 类数据集（档案、净值、快照、持仓、债券、行业、费率、分红/拆分等）。分子是这只基金有数据的集合数。脚本里完整度 = `has_data / total_datasets`——但 0.25~1.0 范围是按 fund_type-aware 矩阵校正过的，比如货币型基金天然没有股票持仓但仍然算完整。"

---

### Step 3：时间序列 — 净值历史（1 分钟）

**命令**：
```bash
python3 fund-data/scripts/fund_cli.py nav 110022 \
  --start-date 2024-01-22 \
  --end-date 2024-01-26
```

**预期输出**（已用真实验证）：
```json
[
  {
    "nav_date": "2024-01-26 00:00:00",
    "unit_nav": 3.238,
    "accumulated_nav": 3.238,
    "daily_growth_rate": null,
    "subscribe_status": "",
    "redeem_status": "",
    "dividend": "",
    "source": "investoday.fund_nav_history"
  },
  {
    "nav_date": "2024-01-25 00:00:00",
    "unit_nav": 3.231,
    "accumulated_nav": 3.231,
    ...
  },
  {
    "nav_date": "2024-01-24 00:00:00",
    "unit_nav": 3.192,
    ...
  }
]
```

**讲法**：
> "这一段是时间序列。注意每行都有 `source` 字段——这个值是 `investoday.fund_nav_history`，代表这行数据来自 Investoday 的 fund_nav_history 接口。后续 agent 引用任何数字时，都能追溯来源。同时表里也保留了 `fetched_at`，所以'是哪个时间点抓的'也说得清。"
>
> "这一步是给回测、走势分析、组合关联用的底层数据。CLI 可以按 `--start-date` / `--end-date` 切片，Python API 里更细。"

---

### Step 4：当前状态 — 快照（1 分钟）

**命令**：
```bash
python3 fund-data/scripts/fund_cli.py snapshot 110022
```

**预期输出**（已用真实验证）：
```json
{
  "fund_code": "110022",
  "fund_name": "易方达消费行业股票",
  "source_rate": 1.5,
  "current_rate": 0.15,
  "min_purchase": 10.0,
  "stock_codes": [
    "1.600519", "0.000333", "0.000858", "1.600809", "0.002594",
    "1.600660", "0.000568", "0.000596", "1.601633", "1.603129"
  ],
  "returns": {
    "one_year": -0.16140000000000002,
    "six_month": -0.19260000000000002,
    "three_month": -0.1131,
    "one_month": -0.0766
  },
  "source": "eastmoney.snapshot"
}
```

**讲法**：
> "快照和历史净值的区别：快照反映'抓取时点'，历史净值反映'日期序列'。这只基金当前费率结构 1.5% / 0.15%、起购 10 元、前十大重仓股票代码列表（注意前缀 `1.` 和 `0.` 是交易所标识：1=沪市、0=深市）。"
>
> "近一年回报 -16.14%，近半年 -19.26%，消费板块这两年的承压直接体现在数据上。"

---

### Step 5：持仓明细（1 分钟）

**命令**：
```bash
python3 fund-data/scripts/fund_cli.py holdings 110022 | head -20
```

**预期输出**（已用真实验证）：
```json
[
  {
    "report_period": "2026-04-22 00:00:00",
    "stock_code": "600519",
    "stock_name": "贵州茅台",
    "net_value_ratio": 0.099,
    "shares": 866598.0,
    "market_value": 1256567100.0,
    "source": "investoday.fund_portfolio_stock_holdings"
  },
  {
    "report_period": "2026-04-22 00:00:00",
    "stock_code": "000333",
    "stock_name": "美的集团",
    "net_value_ratio": 0.0964,
    "shares": 16015180.0,
    "market_value": 1222758993.0,
    "source": "investoday.fund_portfolio_stock_holdings"
  }
]
```

**讲法**：
> "这是 2026-04-22 那期报告的持仓。贵州茅台 9.9%、美的集团 9.64%——两只加起来接近 20% 仓位，集中度是消费主题基金的特点。注意 `report_period` 是季报披露日，`source` 是 `investoday.fund_portfolio_stock_holdings`。每个数据集都有 source 字段，这是项目做'数据可追溯'的硬约束。"

---

### Step 6：导出 — 给下游用（1.5 分钟）

**命令**：
```bash
# 导出 funds 表这只基金 → JSON
python3 fund-data/scripts/fund_cli.py export funds --fund-code 110022 --format json

# 导出 nav_history → JSON Lines
python3 fund-data/scripts/fund_cli.py export nav_history --fund-code 110022 --format json \
  > /tmp/demo-2026-06-03/110022_nav.jsonl

# 导出 stock_holdings → CSV
python3 fund-data/scripts/fund_cli.py export stock_holdings --fund-code 110022 --format csv \
  > /tmp/demo-2026-06-03/110022_holdings.csv

ls -la /tmp/demo-2026-06-03/
wc -l /tmp/demo-2026-06-03/*
```

**预期输出**：
```
total 128
drwxr-xr-x   ...
-rw-r--r--  1 ...  110022_nav.jsonl       # 约 285 行
-rw-r--r--  1 ...  110022_holdings.csv    # 约 147 行

285 /tmp/demo-2026-06-03/110022_nav.jsonl
147 /tmp/demo-2026-06-03/110022_holdings.csv
```

**讲法**：
> "导出的两条产物：JSON Lines 和 CSV。JSON Lines 是给 agent / 脚本消费的，**一行一条记录**——agent 可以 stream parse，不用把整个文件读进内存。CSV 是给 Excel / pandas 用的。我们故意不做 nested JSON，因为线上的 agent 任务通常更想要扁平结构。"

**给观众看一眼真实产物**：
```bash
head -2 /tmp/demo-2026-06-03/110022_nav.jsonl
head -3 /tmp/demo-2026-06-03/110022_holdings.csv
```

---

### Step 7：Python 嵌入（1 分钟）

**命令**：
```bash
PYTHONPATH=fund-data python3 - <<'PY'
from scripts import fund_data

db_path = fund_data.default_db_path()
print(f"using db: {db_path}")

rows = fund_data.coverage_report(db_path=db_path, codes=["110022"])
for r in rows:
    print(
        f"{r['fund_code']} {r['fund_name']} "
        f"completeness={r['completeness']} missing={r['missing']}"
    )
PY
```

**预期输出**：
```
using db: /Users/xiongjiali/.cache/fund-data/releases/2026-06-02T214538Z/fund_data_query.sqlite
110022 易方达消费行业股票 completeness=0.75 missing=['dividends', 'splits']
```

**讲法**：
> "这一步证明它不只是命令行工具。我把 `scripts` 包加到 PYTHONPATH 后直接 import `fund_data`——`default_db_path()` 和 `coverage_report()` 是公开 API。agent、Notebook、FastAPI 服务层都能复用同一个入口，不会有'Python 路径走另一套逻辑'的隐式不一致。"

---

## 2. 进阶 demo — 视时间和现场反应选做

> 这些不是必做项，但观众问到时能立刻展示，比口头解释 100 句都管用。

### 2.1 Agent 自检闭环（30 秒）

> 现场展示"agent 怎么判断环境健康"。

```bash
# 演示"如果 doctor 不 ok，agent 怎么 fail-fast"
if ! python3 fund-data/scripts/fund_cli.py doctor --quiet > /dev/null 2>&1; then
  echo "doctor failed — refuse to run sync"
  exit 1
fi
echo "doctor ok — proceeding"
```

**讲法**：
> "Agent 拿到一个 fund-data 环境，第一件事就是跑 doctor。如果非零退出码，agent 拒绝继续——这就是 data plane 的 contract test。比让 agent '先 sync 一下试试'安全得多。"

---

### 2.2 自定义 watchlist 批量同步（2 分钟）

> 展示"主动拉新数据"的能力。

```bash
# 用项目自带的 8 只基金 sample（覆盖 8 种 fund_type）
cat fund-data/data/fund_codes_sample.txt

# 限制只取前 4 只演示
PYTHONPATH=fund-data python3 examples/watchlist_sync.py \
  --codes-file fund-data/data/fund_codes_sample.txt \
  --provider eastmoney --limit 4 --concurrency 2
```

**预期输出**（简化）：
```
Syncing 4 codes through `eastmoney` provider...
done — ok=4 failed=0 concurrency=2
  110022   ok=True ...
  000001   ok=True ...
  000008   ok=True ...
  510300   ok=True ...
```

**讲法**：
> "这是项目自带的 watchlist 示例脚本。读一个 fund code 文件（支持 `# 注释` 和空行），调 `batch_sync_funds` API，4 只基金并发 2 跑完。注意 `--provider eastmoney` 是默认推荐——免 key、最快、对演示网络最友好。AkShare 备用、Investoday 增强、Tushare 可选。"

**注**：如果现场网络不好或 akshare 没装，跳过这步，改用 §1 的 hero flow 收尾。

---

### 2.3 Cloud bundle 演示（1.5 分钟）

> 展示"怎么把数据底座分发给客户/同事"。

```bash
# 看本地 cache 状态
python3 fund-data/scripts/fund_cli.py cloud status

# 看远端 manifest（可选）
curl -s https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json
```

**预期输出**（cloud status 摘要）：
```json
{
  "installed": true,
  "version": "2026-06-02T214538Z",
  "db_path": "/Users/xiongjiali/.cache/fund-data/releases/2026-06-02T214538Z/fund_data_query.sqlite",
  "size_bytes": 135621159,
  "manifest_url": "https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json"
}
```

**讲法**：
> "Cloud bundle 是查询版 SQLite，已经去掉了 raw_responses 和 sync_runs 这些审计表。体积 135MB，但仍然是真正的 SQLite——同事 `cloud pull` 之后，本地 CLI 直接指向这个文件，**不再需要任何 provider token**。"
>
> "这也是项目给 agent 多环境消费的关键路径：agent 进新环境，`cloud pull` + `doctor` 两步搞定自举。"

---

### 2.4 MCP server 演示（可选，2 分钟）

> 这一步需要 MCP client（如 Claude Desktop / Cursor）。如果现场没有就跳。

**讲法**：
> "项目还提供了 MCP server，stdio 协议。任何 MCP-capable agent 都能直接发现并调用 fund-data 的所有能力，不需要 shell out 到 `fund_cli.py`。""

**示意工具列表**（来自 `fund_mcp.py`）：
- `list_funds`、`search_funds`
- `get_snapshot`、`get_nav_history`
- `get_holdings`、`get_industries`、`get_managers`
- `get_fees`、`get_dividends`、`get_splits`
- `coverage_report`
- `doctor`

**调用示意**（JSON-RPC 2.0 over stdio）：
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "coverage_report",
    "arguments": {"code": "110022"}
  }
}
```

**预期返回**：
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [{"type": "text", "text": "..."}]
  }
}
```

> 如果现场没法演示 MCP，**讲一句**：
> "MCP 存在的意义不是替换 CLI，是给 agent 一个标准化的发现机制——agent 不需要 shell out、不需要解析 stderr 就能拿到结构化数据。这是给 OpenClaw / Claude / Codex 等 agent 用的入口。"

---

### 2.5 用 export 给一个 JSONL 喂下游（30 秒）

```bash
# 把全库 26,953 只基金导出成 JSONL（一次性、streaming）
python3 fund-data/scripts/fund_cli.py export funds --format jsonl \
  > /tmp/demo-2026-06-03/all_funds.jsonl 2> /tmp/demo-2026-06-03/all_funds.log
wc -l /tmp/demo-2026-06-03/all_funds.jsonl
```

**预期**：
```
26953 /tmp/demo-2026-06-03/all_funds.jsonl
```

**讲法**：
> "26,953 行——和 doctor 报的基金池对得上。这种 streaming export 不会爆内存，agent 用 `jq` 或 Python stream 都能直接处理。"

---

## 3. FAQ — 观众可能问的 7 个问题

### Q1: 这个项目现在能不能直接用？

**A**: 能。CLI 查询、coverage、doctor、默认 cloud bundle、Python import——这 5 条路径都验证过。**现场走 doctor 看 `database.ok=true` 就是 contract。**

### Q2: 数据是不是全量？

**A**:
- **基金池**：全量维度，26,953 只。
- **档案 / 快照 / 费率**：接近 100%。
- **净值**：97%+。
- **股票持仓 / 债券持仓 / 行业配置**：受基金类型和公开披露限制，约 49%~57%。**这有完整覆盖表**：
  ```bash
  open docs/data-coverage-summary.md
  ```
- **分红 / 拆分**：天然稀疏，不补也合理。

### Q3: 缺失数据怎么解释？

**A**: 分两类——
- **结构性为空**（合理）：货币基金没有股票持仓、很多基金没有拆分事件、新基金没有分红记录。
- **真实缺口**（待补）：新基金、后端份额、上游接口暂时没数据。
- 项目会逐步给每个缺失打 fund_type-aware 标签，让"缺"的原因对人和 agent 都可读。

### Q4: 如果现场网络不好怎么办？

**A**: 演示优先用本地已装的 cloud query bundle（135MB）。只要 `doctor` 显示 `database.ok=true`，`coverage-report` / `snapshot` / `export` / `nav` 都不依赖现场网络。**只 batch-sync 和 search 等需要网络。**

### Q5: 投资建议 / 风险？

**A**: **不构成投资建议**。项目数据来自 Eastmoney（公开）、AkShare（公开）、Investoday（付费增强），用于研究和分析。引用任何数字时带 source + fetched_at 是项目硬约束。

### Q6: 为什么用 SQLite 而不是 PostgreSQL / ClickHouse？

**A**:
- **零运维**：单文件、双击打开、agent 自带。
- **写读均衡**：26k 基金 × 多种数据集，单机 SQLite 完全够；WAL 模式下并发读不阻塞。
- **可移植**：cloud bundle 整库 135MB，传同事一份就能跑。
- **agent 友好**：Python `sqlite3` 标准库，进出口 0 依赖。

### Q7: 和同花顺 iFinD / Wind 的区别？

**A**:
- **价格**：免 key 主力源 + 可选付费增强。
- **可编程**：CLI + Python + MCP，不是 GUI。
- **可追溯**：每行有 source + fetched_at。
- **可分发**：cloud bundle 同事一键装。
- **不可比**：iFinD/Wind 在数据广度和深度上仍是行业标杆，我们做的是"agent 友好、可本地化、可分发的中等覆盖底座"。

---

## 4. 出错应对 — 5 个最可能的现场问题

### 4.1 doctor 报 `database.ok=false`

**症状**：`missing_tables` 非空 / `path` 不存在。

**应对**：
```bash
# 1. 看具体错
python3 fund-data/scripts/fund_cli.py doctor 2>&1 | python3 -m json.tool

# 2. 重新拉一次 bundle
python3 fund-data/scripts/fund_cli.py cloud pull

# 3. 还不行就用本地全量库
python3 fund-data/scripts/fund_cli.py doctor --db /Users/xiongjiali/Desktop/code/fundData/fund-data/data/fund_data.sqlite --quiet
```

### 4.2 search / nav 报 WARNING `akshare unavailable`

**症状**：stderr 一行 `WARNING fund_data: akshare unavailable for ...`

**应对**：**这是预期行为，不是 error**。系统 Python 没装 AkShare 时降级到只用 Eastmoney。stdout 仍然输出 JSON，退出码 0。如果演示需要 AkShare：

```bash
# 用项目自带的 venv（AkShare 已装 1.18.64）
/Users/xiongjiali/Desktop/code/fundData/.venv-akshare/bin/python fund-data/scripts/fund_cli.py search "易方达" --limit 3
```

### 4.3 cloud pull 卡住或超时

**应对**：跳过 cloud 演示，直接用本地已校验 bundle。如果远端有新版且想升：
```bash
# 手动指定短超时
timeout 30 python3 fund-data/scripts/fund_cli.py cloud pull
```

### 4.4 batch-sync 跑 0 行退出

**应对**：
```bash
# 1. 看 codes 文件是否被注释行吃掉了
grep -v '^#' fund-data/data/fund_codes_sample.txt | grep -v '^$'

# 2. 用 watchlist_sync.py 跑小批量
PYTHONPATH=fund-data python3 examples/watchlist_sync.py --limit 2 --concurrency 1
```

### 4.5 现场数据库路径对不上

**应对**：
```bash
# 一行命令查实际路径
python3 -c "from scripts.fund_data.paths import default_db_path; print(default_db_path())"
```

---

## 5. 三种时长版本

### 5.1 5 分钟精简版（§0.0 数据全景快讲 + §1 Step 1-3 + 收束）

| 步骤 | 时长 | 命令 |
|---|---|---|
| 开场 + 数据全景（快讲） | 1min | 直接口述 §0.0 三段 |
| doctor | 30s | `doctor --quiet` |
| coverage | 1min | `coverage-report --code 110022` |
| snapshot | 1min | `snapshot 110022` |
| nav | 1min | `nav 110022 --start-date 2024-01-22 --end-date 2024-01-26` |
| 收束 | 30s | 一句话讲"MCP + cloud + Python import" |

### 5.2 10 分钟标准版（§0.0 数据全景 + §1 完整 7 步）

| 段落 | 时长 |
|---|---|
| 开场 | 30s |
| §0.0 数据全景 | 1min |
| §1 Hero flow 7 步 | 7.5min |
| 收束 | 1min |

### 5.3 15 分钟完整版（§0.0 + §1 + §2.3 + §2.4 + FAQ）

| 段落 | 时长 |
|---|---|
| 开场 | 30s |
| §0.0 数据全景 | 1min |
| §1 Hero flow 7 步 | 7.5min |
| §2.3 Cloud bundle | 1.5min |
| §2.4 MCP 示意 | 2min |
| FAQ / Q&A | 2.5min |

---

## 6. 演示前 5 条不能忘

1. **`doctor` 永远先跑**——它是给 agent 用的 contract test。
2. **认 `default_db.path`**——观众会问"你用的是哪份库"。
3. **`coverage-report` 拿 110022**——这是已选好的 happy path 基金。
4. **导出物留现场**——`/tmp/demo-2026-06-03/` 目录要存，观众可以拷走。
5. **结尾不卖关子**——明确说"研究用，不构成投资建议"。

---

## 7. 升级自检命令（5 分钟版演示前 30 秒跑一次）

```bash
cd /Users/xiongjiali/Desktop/code/fundData
python3 fund-data/scripts/fund_cli.py doctor --quiet | python3 -m json.tool | head -20
```

如果输出 `database.ok=true` 且 `coverage.total_funds=26953`，绿灯，可以开讲。
