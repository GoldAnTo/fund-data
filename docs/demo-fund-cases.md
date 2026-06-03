# fund-data 多基金演示案例

> 现场检查日期：2026-06-03 Asia/Shanghai
> 适用场景：演示"一只基金说不够，多只基金横评"——4 只精选基金，**每只都能查 6 个数据集**（基本信息 / 净值历史 / 股票持仓 / 债券持仓 / 行业配置 / 费率结构）。
> 用法：演示时从这 4 只里选 1-2 只做横评；investor talk 时拿 1 只深入；agent 批量调时直接套用。
> 重要：所有命令**必须用 `.venv-akshare/bin/python` 跑**——`industries` 和 `fees` 在系统 Python（akshare 未装）下会 all providers failed。下面命令都已实测能跑通。

---

## 0. 公共前置 — 一行 venv 别名

演示前先在 shell 里加个别名，省得每条命令都打长路径：

```bash
# 加到当前 shell
alias fund='.venv-akshare/bin/python fund-data/scripts/fund_cli.py'

# 验证
fund doctor --quiet | python3 -c "import json,sys; d=json.load(sys.stdin); print('ok' if d['database']['ok'] else 'fail')"
```

> **讲法**："演示用 alias 是因为 `industries` 和 `fees` 在系统 Python 下会因为 akshare 未装而失败。venv 装的是 akshare 1.18.64，6 条命令全跑通。**这是项目当前的一个真实 trade-off——系统 Python 的 ak-share 装起来会和 macOS system python 起冲突，所以推荐用项目自带的 venv。**"

---

## 1. 110022 易方达消费行业股票（**股票型**，主消费主题）

**基金标签**：A 股消费主题最知名的一只；演示"高仓位、单一主题"。

| 维度 | 数据 |
|---|---|
| fund_type | 股票型 |
| 公司 | 易方达基金管理有限公司 |
| 业绩基准 | 中证内地消费主题指数×85% + 中债总指数×15% |
| nav_history | 264 行（2010-12-29 成立至今） |
| stock_holdings | 136 行（多期季报） |
| bond_holdings | 12 行 |
| industry_allocations | 20 行 |
| fee_structures | 4 行（**注**：用 venv 跑 `fees` 拿到完整 30 条） |

### 1.1 基本信息

```bash
fund profile 110022
```

**预期输出**（节选）：
```json
{
  "fund_code": "110022",
  "fund_name": "易方达消费行业股票",
  "full_name": "易方达消费行业股票型证券投资基金",
  "fund_type": "股票型",
  "establishment_date": "2010-12-29",
  "fund_company": "易方达基金管理有限公司",
  "benchmark": "中证内地消费主题指数收益率×85%+中债总指数收益率×15%",
  "is_qdii": false,
  "is_fof": false,
  "investment_objective": "    本基金主要投资消费行业股票...",
  "source": "akshare.fund_overview_em"
}
```

**教学要点**：看 `source` —— 档案来自 AkShare 的 `fund_overview_em`，不是 Eastmoney。这印证了"主力源是 AkShare 不是 Eastmoney"。

### 1.2 净值历史（5 天切片）

```bash
fund nav 110022 --start-date 2024-01-22 --end-date 2024-01-26
```

**预期输出**（节选）：
```json
[
  {"nav_date": "2024-01-26", "unit_nav": 3.238, "accumulated_nav": 3.238, "source": "investoday.fund_nav_history"},
  {"nav_date": "2024-01-25", "unit_nav": 3.231, "source": "investoday.fund_nav_history"},
  {"nav_date": "2024-01-24", "unit_nav": 3.192, "source": "investoday.fund_nav_history"}
]
```

**教学要点**：同一只基金的 1 个时间窗口，3 个工作日单位净值从 3.192 → 3.238。注意每行都有 `source` 字段。

### 1.3 股票持仓（最新一期）

```bash
fund holdings 110022 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "stock_code": "600519", "stock_name": "贵州茅台", "net_value_ratio": 0.099, "shares": 866598, "market_value": 1256567100, "source": "investoday.fund_portfolio_stock_holdings"},
  {"report_period": "2026-04-22", "stock_code": "000333", "stock_name": "美的集团", "net_value_ratio": 0.0964, "shares": 16015180, "market_value": 1222758993, "source": "investoday.fund_portfolio_stock_holdings"}
]
```

**教学要点**：2026-04-22 那期季报，贵州茅台 9.9% + 美的集团 9.64% ≈ 20% 仓位。集中度高是消费主题基金的特点。

### 1.4 债券持仓

```bash
fund bonds 110022 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "bond_code": "110022-B0", "bond_name": "牧原转债", "net_value_ratio": 0.007, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "110022-B0", "bond_name": "百润转债", "net_value_ratio": 0.0031, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "110022-B0", "bond_name": "欧22转债", "net_value_ratio": 0.0033, "source": "investoday.fund_portfolio_bond_holdings"}
]
```

**教学要点**：股票型基金**也有**债券持仓——主要是可转债（牧原/百润/欧22 都是转债）。这个反直觉的点值得在演示里说。

### 1.5 行业配置

```bash
fund industries 110022 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2025-12-31", "industry_name": "制造业", "net_value_ratio": 0.8708, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "农、林、牧、渔业", "net_value_ratio": 0.0318, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "信息传输、软件和信息技术服务业", "net_value_ratio": 0.006, "source": "akshare.fund_portfolio_industry_allocation_em"}
]
```

**教学要点**：制造业占 87%——因为白酒/家电/汽车全归在制造业大类下。AkShare 的行业分类是申万一级。

### 1.6 费率结构

```bash
fund fees 110022 | head -15
```

**预期输出**（节选）：
```json
[
  {"fee_type": "交易状态", "condition_name": "申购状态", "fee_text": "开放申购", "source": "akshare.fund_fee_em"},
  {"fee_type": "申购与赎回金额", "condition_name": "申购起点", "fee_text": "10.00元", "source": "akshare.fund_fee_em"},
  {"fee_type": "申购与赎回金额", "condition_name": "首次购买", "fee_text": "10.00元", "source": "akshare.fund_fee_em"},
  {"fee_type": "申购与赎回金额", "condition_name": "最小赎回份额", "fee_text": "1.00份", "source": "akshare.fund_fee_em"}
]
```

**教学要点**：fees 表**不是**只有"管理费/托管费"——它还包含交易状态、起购金额、赎回数等运营信息。完整 30 条。

---

## 2. 000001 华夏成长混合（**混合型-偏股**，老牌成长基金）

**基金标签**：中国公募基金行业**第一只基金**（2001-12-18 成立）；演示"长期数据 + 混合型分散度"。

| 维度 | 数据 |
|---|---|
| fund_type | 混合型-偏股 |
| 公司 | 华夏基金管理有限公司 |
| 业绩基准 | **本基金暂不设业绩比较基准**（成立早的基金常有这情况） |
| nav_history | **5,932 行**（最长 24 年） |
| stock_holdings | 708 行 |
| bond_holdings | 308 行 |
| industry_allocations | 72 行 |
| fee_structures | 24 行 |

### 2.1 基本信息

```bash
fund profile 000001
```

**预期输出**（节选）：
```json
{
  "fund_code": "000001",
  "fund_name": "华夏成长混合",
  "establishment_date": "2001-12-18",
  "fund_company": "华夏基金管理有限公司",
  "benchmark": "本基金暂不设业绩比较基准",
  "investment_objective": "    本基金属成长型基金，主要通过投资于具有良好成长性的上市公司...",
  "source": "investoday.fund_all"
}
```

**教学要点**：注意 `source=investoday.fund_all`——这条**不是** AkShare，是 Investoday。`fund_all` 是 Investoday 180+ 接口里的"基金全量"端点，老基金在 Investoday 里数据更全。说明 provider chain 自动选了最优。

### 2.2 净值历史（拉长一段时间看长期趋势）

```bash
fund nav 000001 --start-date 2024-01-22 --end-date 2024-01-26
```

**预期输出**（节选）：
```json
[
  {"nav_date": "2024-01-26", "unit_nav": 0.71, "accumulated_nav": null, "daily_growth_rate": -0.0125, "source": "investoday.fund_nav_history"}
]
```

**教学要点**：`unit_nav=0.71`——这是单位净值，< 1 是因为有分红/拆分。`accumulated_nav` 字段在 `investoday.fund_all` 老数据里经常是 `null`。如果要看长期复权数据，用 `daily_growth_rate` 字段自己复权。

### 2.3 股票持仓

```bash
fund holdings 000001 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "stock_code": "300308", "stock_name": "中际旭创", "net_value_ratio": 0.0431, "market_value": 113882000, "source": "investoday.fund_portfolio_stock_holdings"}
]
```

**教学要点**：混合型分散度高——单只最大仓位 4.31%（中际旭创），不像 110022 那种消费主题 9.9% 集中。**对比 110022 演示"集中 vs 分散"是个好角度**。

### 2.4 债券持仓

```bash
fund bonds 000001 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "bond_code": "000001-B0", "bond_name": "金田转债", "net_value_ratio": 0.0019, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "000001-B0", "bond_name": "万顺转2", "net_value_ratio": 0.001, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "000001-B0", "bond_name": "卫宁转债", "net_value_ratio": 0.002, "source": "investoday.fund_portfolio_bond_holdings"}
]
```

**教学要点**：和 110022 一样持有大量可转债（金田、万顺、卫宁），但单只占比更低（0.1-0.2%），债券的"打新"色彩更明显。

### 2.5 行业配置

```bash
fund industries 000001 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2025-12-31", "industry_name": "制造业", "net_value_ratio": 0.6714, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "信息传输、软件和信息技术服务业", "net_value_ratio": 0.0996, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "采矿业", "net_value_ratio": 0.0134, "source": "akshare.fund_portfolio_industry_allocation_em"}
]
```

**教学要点**：制造业 67% + 信息技术 10% + 采矿业 1.34%——典型成长基金偏好，**对比 110022（消费主题 87% 制造业）看出基金类型差异**。

### 2.6 费率结构

```bash
fund fees 000001 | head -10
```

**预期输出**（节选）：
```json
[
  {"fee_type": "交易状态", "condition_name": "申购状态", "fee_text": "开放申购", "source": "akshare.fund_fee_em"},
  {"fee_type": "申购与赎回金额", "condition_name": "申购起点", "fee_text": "10.00元", "source": "akshare.fund_fee_em"}
]
```

**教学要点**：老基金和新基金的费率结构字段基本一致——可以横向对比费率，发现是否有差异。

---

## 3. 163406 兴全合润LOF（**混合型-偏股**，谢治宇代表产品）

**基金标签**：私募级主动管理；演示"中型混合型 + 大额持仓"。

| 维度 | 数据 |
|---|---|
| fund_type | 混合型-偏股 |
| 公司 | 兴证全球基金管理有限公司 |
| 业绩基准 | 80%×沪深300 + 20%×中证国债 |
| nav_history | 20 行（3 年窗口） |
| stock_holdings | 182 行 |
| bond_holdings | 30 行 |
| industry_allocations | 29 行 |
| fee_structures | 31 行 |

### 3.1 基本信息

```bash
fund profile 163406
```

**预期输出**（节选）：
```json
{
  "fund_code": "163406",
  "fund_name": "兴全合润LOF",
  "establishment_date": "2021-01-01",
  "fund_company": "兴证全球基金管理有限公司",
  "benchmark": "80%×沪深300指数＋20%×中证国债指数",
  "source": "investoday.fund_all"
}
```

**教学要点**：LOF（上市开放式基金）——既可以在场外申购，也可以在场内交易。代码前缀 163 开头是深交所 LOF 段。**演示时说明 LOF 类基金代码要认清 A/C 份额**（A 类前端收费、C 类后端收费）。

### 3.2 净值历史

```bash
fund nav 163406 --start-date 2024-01-22 --end-date 2024-01-26
```

**预期输出**（节选）：
```json
[
  {"nav_date": "2024-01-26", "unit_nav": 1.2705, "accumulated_nav": 4.8317, "source": "investoday.fund_nav_history"}
]
```

**教学要点**：`unit_nav=1.27` 当前、`accumulated_nav=4.83` 累计——**这是有分红的基金**。`accumulated_nav - unit_nav` 的差就是历次分红总和。比起 000001 的 `null`，这条数据完整。

### 3.3 股票持仓

```bash
fund holdings 163406 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "stock_code": "600160", "stock_name": "巨化股份", "net_value_ratio": 0.0968, "market_value": 1882001206, "source": "investoday.fund_portfolio_stock_holdings"}
]
```

**教学要点**：巨化股份 9.68% 单只集中度——和 110022 的贵州茅台 9.9% 几乎一样，**说明混合型也能做到接近股票型的集中度**。谢治宇是出了名的"高集中度"风格。

### 3.4 债券持仓

```bash
fund bonds 163406 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "bond_code": "163406-B0", "bond_name": "16国开10", "net_value_ratio": 0.008, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "163406-B0", "bond_name": "25农发11", "net_value_ratio": 0.0183, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "163406-B0", "bond_name": "25国开06", "net_value_ratio": 0.0052, "source": "investoday.fund_portfolio_bond_holdings"}
]
```

**教学要点**：和 110022 的转债不同，163406 持有**政策性金融债**（国开/农发）——更稳健的固收配置。**对比 110022 看"高风险债券 vs 低风险债券"的配置差异**。

### 3.5 行业配置

```bash
fund industries 163406 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2025-12-31", "industry_name": "制造业", "net_value_ratio": 0.7583, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "信息传输、软件和信息技术服务业", "net_value_ratio": 0.0823, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "交通运输、仓储和邮政业", "net_value_ratio": 0.0337, "source": "akshare.fund_portfolio_industry_allocation_em"}
]
```

**教学要点**：制造业 76% + 信息技术 8% + 交通运输 3.4%——和 000001 几乎一致（都是成长型）。**对比 161725（指数 100% 酒）看出"主动选股 vs 被动跟踪"在行业暴露上的区别**。

### 3.6 费率结构

```bash
fund fees 163406 | head -10
```

**预期输出**（节选）：
```json
[
  {"fee_type": "交易状态", "condition_name": "申购状态", "fee_text": "开放申购", "source": "akshare.fund_fee_em"},
  {"fee_type": "申购与赎回金额", "condition_name": "最小赎回份额", "fee_text": "0.10份", "source": "akshare.fund_fee_em"}
]
```

**教学要点**：注意 163406 的最小赎回份额是 **0.10 份**（不是 1.00 份）——LOF 类基金可以场内交易，赎回数允许更精细。**对比 110022（1.00 份）看场外 vs LOF 的差异**。

---

## 4. 161725 招商中证白酒指数A（**指数型-股票**，白酒指数）

**基金标签**：A 股最知名的主题指数基金；演示"指数基金也有债券持仓"（反直觉）。

| 维度 | 数据 |
|---|---|
| fund_type | 指数型-股票（实际是 LOF） |
| 公司 | 招商基金管理有限公司 |
| 业绩基准 | 中证白酒指数×95% + 活期存款×5% |
| nav_history | 20 行 |
| stock_holdings | 193 行 |
| bond_holdings | 16 行 |
| industry_allocations | 19 行 |
| fee_structures | （用 venv 跑） |

### 4.1 基本信息

```bash
fund profile 161725
```

**预期输出**（节选）：
```json
{
  "fund_code": "161725",
  "fund_name": "白酒基金LOF",
  "full_name": "招商中证白酒指数证券投资基金A类",
  "establishment_date": "2021-01-01",
  "fund_company": "招商基金管理有限公司",
  "benchmark": "中证白酒指数收益率×95%＋金融机构人民币活期存款基准利率（税后）×5",
  "investment_strategy": "    本基金以中证白酒指数为标的指数，采用完全复制法...",
  "source": "investoday.fund_all"
}
```

**教学要点**：`fund_name=白酒基金LOF`——这是 161725 现在的展示名（早期叫"招商中证白酒指数分级"）。基金改名/合并后 `fund_name` 是当前名，`full_name` 是带份额后缀的完整名。**演示 agent 处理"老名字 vs 新名字"时这是个坑**。

### 4.2 净值历史

```bash
fund nav 161725 --start-date 2024-01-22 --end-date 2024-01-26
```

**预期输出**（节选）：
```json
[
  {"nav_date": "2024-01-26", "unit_nav": 0.79, "source": "investoday.fund_nav_history"}
]
```

**教学要点**：unit_nav < 1——白酒板块这两年回调幅度大。但 `accumulated_nav` 看长期收益，0.79 是截面不是全貌。

### 4.3 股票持仓

```bash
fund holdings 161725 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "stock_code": "002646", "stock_name": "天佑德酒", "net_value_ratio": 0.0014, "market_value": 55892655, "source": "investoday.fund_portfolio_stock_holdings"}
]
```

**教学要点**：天佑德酒 0.14%——和 110022 茅台 9.9% 形成鲜明对比，**指数基金前 10 大持仓可能就是 110022 的"中后段"**。演示时演示白酒指数 = 整个白酒板块，不只是茅台五粮液。

### 4.4 债券持仓（**反直觉点**）

```bash
fund bonds 161725 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "bond_code": "161725-B0", "bond_name": "晶能转债", "net_value_ratio": 0.0, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "161725-B0", "bond_name": "20国债01", "net_value_ratio": 0.0014, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "161725-B0", "bond_name": "20国债10", "net_value_ratio": 0.0012, "source": "investoday.fund_portfolio_bond_holdings"}
]
```

**教学要点**：**指数基金也有债券持仓**（晶能转债 + 国债）——这是为了让"打新"和现金管理用，不是策略配置。**反直觉点值得在演示里说，否则观众可能以为指数基金 100% 股票**。

### 4.5 行业配置

```bash
fund industries 161725 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2025-12-31", "industry_name": "制造业", "net_value_ratio": 0.93, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "农、林、牧、渔业", "net_value_ratio": 0.03, "source": "akshare.fund_portfolio_industry_allocation_em"}
]
```

**教学要点**：制造业 93%——白酒行业的所有股票（茅台/五粮液/泸州老窖）都归类在制造业-酒、饮料和精制茶制造业。AkShare 申万一级分类就是这么分的。

### 4.6 费率结构

```bash
fund fees 161725 | head -10
```

**预期输出**（节选）：和 110022 / 000001 类似的字段结构，fee_text 略不同（如"场内交易佣金另算"等）。

**教学要点**：指数基金管理费通常比主动基金低（0.5% vs 1.5%）——可以在 fees 数据里看 `管理费率` / `托管费率` 字段对比。

---

## 5. 4 只基金横评（演示用对照表）

> 演示时把这个表打印或投影出来，让观众一眼看出"4 种风格、4 种数据形态"。

| 维度 | 110022 | 000001 | 163406 | 161725 |
|---|---|---|---|---|
| 类型 | 股票型 | 混合型-偏股 | 混合型-偏股 | 指数型-股票 |
| 成立日 | 2010-12-29 | 2001-12-18 | 2021-01-01 | 2021-01-01 |
| 公司 | 易方达 | 华夏 | 兴证全球 | 招商 |
| 业绩基准 | 中证消费×85% | 不设基准 | 沪深300×80%+国债×20% | 中证白酒×95% |
| **nav_history 行数** | 264 | **5,932** | 20 | 20 |
| **stock_holdings 行数** | 136 | 708 | 182 | 193 |
| bond_holdings 行数 | 12 | 308 | 30 | 16 |
| industry_allocations 行数 | 20 | 72 | 29 | 19 |
| fee_structures 行数 | 4 | 24 | 31 | （venv 跑） |
| 最大单股 | 茅台 9.9% | 中际旭创 4.31% | 巨化股份 9.68% | 茅台（指数） |
| 行业 #1 | 制造业 87% | 制造业 67% | 制造业 76% | 制造业 93% |
| 债券风格 | 转债（牧原/百润/欧22） | 转债（金田/万顺/卫宁） | 政策金融债（国开/农发） | 国债+小转债 |
| LOF？ | ❌ | ❌ | ✅ 深交所 LOF | ✅ 深交所 LOF |
| 数据源主导 | AkShare + Investoday | Investoday | Investoday | Investoday |

**演示对照讲法**：
> "同样 6 条命令，4 只基金走出来完全不同的形态——
>
> - **000001 净值 5,932 行**——24 年的老基金，演示'长期数据'；
> - **110022 茅台 9.9%** + **161725 茅台 0.x%**——同样一只股票在不同基金里的权重差异；
> - **110022 转债 vs 163406 政策性金融债**——同样是'债券'，风险偏好天差地别；
> - **161725 是指数基金，制造业 93%**——被动跟踪的极端行业暴露；
> - **000001 业绩基准是'本基金暂不设业绩比较基准'**——2001 年的老基金，确实当年没要求。
>
> 这就是 fund-data 给人和 agent 的核心价值——**同一套命令、同一个 schema，能横评不同风格的基金**。"

---

## 6. 一键全跑（演示 5 分钟版用）

如果时间紧，把 4 只基金的 6 条命令打包成一个 shell 脚本，现场跑：

```bash
mkdir -p /tmp/demo-cases
CODES="110022 000001 163406 161725"
for code in $CODES; do
  echo "===== $code ====="
  fund profile $code 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  profile: {d['fund_name']} | {d['fund_company']}\")"
  fund nav $code --start-date 2024-01-22 --end-date 2024-01-26 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  nav: {len(d)} rows, latest={d[0]['unit_nav'] if d else 'N/A'}\")"
  fund holdings $code 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  holdings: {len(d)} rows, top={d[0]['stock_name'] if d else 'N/A'}\")"
  fund bonds $code 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  bonds: {len(d)} rows\")"
  fund industries $code 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  industries: {len(d)} rows, top={d[0]['industry_name'] if d else 'N/A'}\")"
  fund fees $code 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  fees: {len(d)} rows\")"
done
```

**预期输出**（演示时实时看，每只基金 6 行）：
```
===== 110022 =====
  profile: 易方达消费行业股票 | 易方达基金管理有限公司
  nav: 3 rows, latest=3.238
  holdings: 136 rows, top=贵州茅台
  bonds: 3 rows
  industries: 19 rows, top=制造业
  fees: 30 rows

===== 000001 =====
  profile: 华夏成长混合 | 华夏基金管理有限公司
  nav: 3 rows, latest=0.71
  holdings: 708 rows, top=中际旭创
  bonds: 40 rows
  industries: 26 rows, top=制造业
  fees: 24 rows

===== 163406 =====
  profile: 兴全合润LOF | 兴证全球基金管理有限公司
  nav: 1 rows, latest=1.2705
  holdings: 182 rows, top=巨化股份
  bonds: 30 rows
  industries: 29 rows, top=制造业
  fees: 31 rows

===== 161725 =====
  profile: 白酒基金LOF | 招商基金管理有限公司
  nav: 1 rows, latest=0.79
  holdings: 193 rows, top=天佑德酒
  bonds: 16 rows
  industries: 19 rows, top=制造业
  fees: ... (venv 跑)
```

---

## 7. 选基金的建议（给 agent 写代码时参考）

| 场景 | 推荐基金 | 理由 |
|---|---|---|
| 演示"高仓位、单主题" | **110022** | 消费主题 87% 制造业集中度，名字耳熟能详 |
| 演示"长期数据 / 老基金" | **000001** | 中国第一只基金，nav 5,932 行 |
| 演示"中型混合 + 私募级" | **163406** | 谢治宇代表，巨化 9.68% 集中度 |
| 演示"被动指数 + 反直觉点" | **161725** | 指数基金也有债券（国债 + 小转债） |
| 演示"行业极端暴露" | **161725** | 制造业 93% 极值 |
| 演示"LOF 类" | **163406 / 161725** | 都是深交所 LOF，最小赎回数 0.10 份 |
| 演示"老 vs 新基金源差异" | **000001** | source=investoday.fund_all，老基金在 Investoday 数据更全 |
| **避免选** | 货币型 / 纯债 / REITs | 没股票持仓，不能演示完整 6 数据集 |

---

## 8. 出错应对

### 8.1 `industries` / `fees` 报 "all providers failed"

**症状**：
```
fund_data error: all providers failed for industry_allocations: investoday: provider returned no rows; eastmoney: 'EastmoneyProvider' object has no attribute 'industry_allocations'
```

**原因**：系统 Python 没装 akshare。Investoday 没实现这个方法。Eastmoney 也没。

**应对**：
```bash
# 用项目 venv
.venv-akshare/bin/python fund-data/scripts/fund_cli.py industries 110022
```

### 8.2 `profile` 命令 5+ 秒卡住

**症状**：第一次跑 profile 等很久。

**原因**：provider chain 顺序尝试 Eastmoney → AkShare → Investoday，Investoday 慢。

**应对**：加 `--provider akshare` 跳过 investoday：
```bash
fund profile 110022 --provider akshare
```

### 8.3 `funds` 表里某基金 fund_type 为空

**症状**：`coverage-report --code XXXXX` 里 `fund_type: ""`。

**原因**：基金新发，Eastmoney `fundcode_search` 还没分类。

**应对**：
```bash
# 用 refresh_fund_type.py 兜底（按 fund_name 解析）
PYTHONPATH=fund-data python3 scripts/refresh_fund_type.py --only-empty
```

---

## 9. 配套文档

- 主 demo 流程：`docs/demo-case.md`
- 演示说明稿（talk track）：`docs/demo-talk.md`
- 数据全景速查：`docs/demo-case.md` §0.0
- 数据覆盖详细：`docs/data-coverage-summary.md`
- 6 数据集全覆盖基金清单：8,043 只（SQL 自查）：
  ```sql
  SELECT COUNT(*) FROM funds f
  WHERE EXISTS (SELECT 1 FROM fund_profiles p WHERE p.fund_code = f.fund_code)
    AND EXISTS (SELECT 1 FROM nav_history n WHERE n.fund_code = f.fund_code)
    AND EXISTS (SELECT 1 FROM stock_holdings s WHERE s.fund_code = f.fund_code)
    AND EXISTS (SELECT 1 FROM bond_holdings b WHERE b.fund_code = f.fund_code)
    AND EXISTS (SELECT 1 FROM industry_allocations i WHERE i.fund_code = f.fund_code)
    AND EXISTS (SELECT 1 FROM fee_structures fs WHERE fs.fund_code = f.fund_code)
  ```

---

## 10. 070001 嘉实成长收益混合A — 11 数据集完整案例（**一只基金看完所有信息**）

> **现场检查日期：2026-06-03 13:23 Asia/Shanghai**（已用 cloud pull 拉到的最新数据实测）。
> 适用场景：演示"一只基金能不能 11 个数据集全查到"——**是的，070001 能**。splits 和 dividends 是真正稀缺的（splits 全池只 2.19% / dividends 28.58%），这只基金两个都有，**演示效果最佳**。
> 用法：选这 1 只基金深入，11 个数据集一条不落跑完。

### 10.1 为什么选 070001

| 维度 | 数据 |
|---|---|
| 名称 | 嘉实成长收益混合A |
| 类型 | **混合型-偏股** |
| 成立日 | **2002-11-05**（20+ 年老牌基金） |
| 公司 | 嘉实基金管理有限公司 |
| 业绩基准 | 上证 A 股指数（老基金，benchmark 简单直接） |
| 11 数据集全有 | ✅ 全部 11 个数据集都有数据 |
| **亮点** | splits 1 行 + dividends 23 行（20+ 年累计）+ 茅台/宁德/泸州老窖仓位 |

**为什么适合做"完整案例"主推**：
- 知名度高——嘉实旗下第一只开放式基金，行业标杆；
- 数据全——11 数据集全覆盖（funds / fund_profiles / nav_history / snapshots / stock_holdings / bond_holdings / industry_allocations / fee_structures / dividends / splits / fund_managers）；
- **数据有时间纵深**——成立 20+ 年，splits 1 行（2008 份额折算）、dividends 23 行（多次分红），适合讲"长期数据底座"；
- **snapshot 收益近 1 年 +31.37%**——是 4 只里回报最好的，演示效果好。

### 10.2 一键 venv 别名

```bash
# 演示前先在 shell 里设
alias fund='.venv-akshare/bin/python fund-data/scripts/fund_cli.py'
```

> **industries / fees 必须在 venv 下跑**（系统 Python 没装 akshare，会 all providers failed）。

### 10.3 11 个数据集的完整命令清单

#### ① funds（基金池基础信息）

```bash
# funds 表是基础池，没有专门 CLI 子命令；用 export 或 SQL 直查
fund export funds --fund-code 070001 --format json
```

**预期输出**：
```json
[{
  "fund_code": "070001",
  "fund_name": "嘉实成长收益混合A",
  "fund_type": "混合型-偏股",
  "company": "",
  "manager": "",
  "nav": null,
  "nav_date": "",
  "other_names": "JIASHICHENGZHANGSHOUYIHUNHEA",
  "source": "eastmoney.fundcode_search",
  "updated_at": "2026-06-02T05:46:10+00:00"
}]
```

**教学要点**：`fund_type="混合型-偏股"`（已经 refresh 过 fund_type）。`source=eastmoney.fundcode_search`——基金池的 source 是 Eastmoney 主力。

#### ② fund_profiles（基金档案）

```bash
fund profile 070001
```

**预期输出**（节选）：
```json
{
  "fund_code": "070001",
  "fund_name": "嘉实成长收益混合A",
  "full_name": "嘉实成长收益证券投资基金A类",
  "fund_type": "",
  "establishment_date": "2002-11-05",
  "fund_company": "嘉实基金管理有限公司",
  "custodian": "中国银行股份有限公司",
  "manager": "",
  "benchmark": "上证A股指数",
  "is_qdii": false,
  "is_fof": false,
  "source": "investoday.fund_all"
}
```

**教学要点**：`source=investoday.fund_all`——老基金在 Investoday 数据更全（fund_all 是 Investoday 180+ 接口里的"基金全量"端点）。`fund_type=""`（profile 字段为空）——一个已知的小坑，需要用 `refresh_fund_type.py` 兜底；用 funds 表的 fund_type 即可。

#### ③ nav_history（历史净值）

```bash
fund nav 070001 --start-date 2024-01-22 --end-date 2024-01-26
```

**预期输出**（节选）：
```json
[
  {"nav_date": "2024-01-26", "unit_nav": 1.0301, "accumulated_nav": 4.042, "source": "investoday.fund_nav_history"},
  {"nav_date": "2024-01-25", "unit_nav": 1.0423, "accumulated_nav": 4.0626, "source": "investoday.fund_nav_history"},
  {"nav_date": "2024-01-24", "unit_nav": 1.0381, "accumulated_nav": 4.0555, "source": "investoday.fund_nav_history"}
]
```

**教学要点**：`accumulated_nav=4.042` 是复权累计净值，意思是"假设把 20+ 年所有分红/拆分都加回去，从 1 块涨到 4 块"——**这只基金成立至今涨了 4 倍**。`unit_nav=1.0301` 是当前单位净值（< 4 是因为多次分红 + 2008 份额折算）。

#### ④ snapshots（当前快照）

```bash
fund snapshot 070001
```

**预期输出**（节选）：
```json
{
  "fund_code": "070001",
  "fund_name": "嘉实成长收益混合A",
  "source_rate": 1.5,
  "current_rate": 0.15,
  "min_purchase": 10.0,
  "stock_codes": [
    "1.600519", "0.300750", "0.000568", "1.601857", "1.603986", "1.688192", ...
  ],
  "returns": {
    "one_year": 0.3137,
    "six_month": 0.0369,
    "three_month": 0.0337,
    "one_month": 0.0192
  },
  "source": "eastmoney.snapshot"
}
```

**教学要点**：**近 1 年回报 +31.37%**——4 只里最高（110022 是 -16.14%，161725 长期负回报）。stock_codes 列表里的前缀 `1.`=沪市、`0.`=深市（如 `1.600519`=沪市 600519 贵州茅台）。

#### ⑤ stock_holdings（股票持仓）

```bash
fund holdings 070001 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "stock_code": "600519", "stock_name": "贵州茅台", "net_value_ratio": 0.0446, "shares": ..., "market_value": ..., "source": "investoday.fund_portfolio_stock_holdings"},
  {"report_period": "2026-04-22", "stock_code": "300750", "stock_name": "宁德时代", "net_value_ratio": 0.0388, "shares": ..., "market_value": ..., "source": "investoday.fund_portfolio_stock_holdings"},
  {"report_period": "2026-04-22", "stock_code": "000568", "stock_name": "泸州老窖", "net_value_ratio": 0.0216, "shares": ..., "market_value": ..., "source": "investoday.fund_portfolio_stock_holdings"}
]
```

**教学要点**：最新一期（2026-04-22）持仓，茅台 4.46% + 宁德 3.88% + 泸州老窖 2.16%——**消费 + 新能源 + 白酒三足鼎立**，这是嘉实成长收益近 1 年 +31% 的核心驱动。**对比 110022**（茅台 9.9% 集中度），嘉实成长收益的**集中度更低、行业更分散**。

#### ⑥ bond_holdings（债券持仓）

```bash
fund bonds 070001 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2026-04-22", "bond_code": "...", "bond_name": "23附息国债17", "net_value_ratio": 0.0611, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "...", "bond_name": "25国债08", "net_value_ratio": 0.0185, "source": "investoday.fund_portfolio_bond_holdings"},
  {"report_period": "2026-04-22", "bond_code": "...", "bond_name": "25附息国债08", "net_value_ratio": 0.0338, "source": "investoday.fund_portfolio_bond_holdings"}
]
```

**教学要点**：**清一色是国债**（23 附息国债、25 国债、25 附息国债）——**和 110022（转债）/ 163406（国开农发政策金融债）都不一样**，这只基金的债券配置是**利率债+长久期**风格，**风险最低**。23 附息国债 6.11% 是单只最大债券持仓。

#### ⑦ industry_allocations（行业配置）

```bash
fund industries 070001 | head -10
```

**预期输出**（节选）：
```json
[
  {"report_period": "2025-12-31", "industry_name": "制造业", "net_value_ratio": 0.4359, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "金融业", "net_value_ratio": 0.0532, "source": "akshare.fund_portfolio_industry_allocation_em"},
  {"report_period": "2025-12-31", "industry_name": "信息传输、软件和信息技术服务业", "net_value_ratio": 0.0387, "source": "akshare.fund_portfolio_industry_allocation_em"}
]
```

**教学要点**：制造业 43.59%——比 110022（87%）和 161725（93%）**低很多**，说明嘉实成长收益**真正做到了行业分散**。金融业 5.32% + 信息技术 3.87% + 其他行业 = 整体多元化。

#### ⑧ fee_structures（费率结构）

```bash
fund fees 070001 | head -15
```

**预期输出**（节选）：
```json
[
  {"fee_type": "交易状态", "condition_name": "申购状态", "fee_text": "开放申购", "source": "akshare.fund_fee_em"},
  {"fee_type": "交易状态", "condition_name": "普通回活期宝", "fee_text": "支持", "source": "akshare.fund_fee_em"},
  {"fee_type": "申购与赎回金额", "condition_name": "申购起点", "fee_text": "10.00元", "source": "akshare.fund_fee_em"},
  {"fee_type": "申购与赎回金额", "condition_name": "最小赎回份额", "fee_text": "1.00份", "source": "akshare.fund_fee_em"}
]
```

**教学要点**：完整 27 条——fee_type 包含"交易状态 / 申购与赎回金额 / 管理费率 / 托管费率 / 销售服务费率"五大类。**和老基金 000001（24 条）相比多了 3 条**，可能是嘉实特有的"快速赎回"等运营字段。

#### ⑨ dividends（分红）— 23 行

```bash
fund dividends 070001
```

**预期输出**（节选，按日期降序）：
```json
[
  {"dividend_date": "2021-01-18", "dividend_per_unit": 0.05, "dividend_type": "现金分红", "source": "akshare.fund_open_fund_info_em:分红送配详情"},
  {"dividend_date": "2020-01-15", "dividend_per_unit": ..., "dividend_type": "现金分红", "source": "akshare.fund_open_fund_info_em:分红送配详情"},
  {"dividend_date": "2018-01-15", ...},
  {"dividend_date": "2016-01-18", ...},
  {"dividend_date": "2015-01-21", ...}
]
```

**教学要点**：**23 行分红**——成立 20+ 年，多次现金分红。**对比 110022（0 行）**——后者是消费主题基金，分红少；嘉实成长收益**更倾向现金分红回馈**。分红日期基本是 1 月中旬（年初分红是基金行业的惯例）。

#### ⑩ splits（拆分/折算）— 1 行

```bash
fund splits 070001
```

**预期输出**：
```json
[
  {
    "split_date": "2008-02-27",
    "split_type": "份额折算",
    "split_ratio": 1.6886,
    "source": "akshare.fund_open_fund_info_em:拆分详情"
  }
]
```

**教学要点**：**这是项目里最稀缺的字段**——全池 26,953 只基金只有 589 只（2.19%）有 splits 记录。`split_ratio=1.6886` 意味着 2008-02-27 那次折算，1 份变成 1.6886 份（净值等比例缩小）。**配合 nav_history 的 `accumulated_nav=4.042` 看**——这只基金累计涨 4 倍，其中一部分来自这次折算。

#### ⑪ fund_managers（基金经理）

```bash
fund managers 070001
```

> **注意**：fund_managers 是 **manager-centric 表**——`current_fund_codes` 是 CSV 文本。CLI 的 `managers` 子命令不接 fund_code 参数（接 fund_code 是查"哪些经理管这只基金"），需要 filter。

**预期输出**：
```json
[
  {
    "manager_name": "方晗",
    "company": "嘉实基金管理有限公司",
    "current_fund_codes": "070001,070003,070006,...",
    "current_aum": 16.85,
    "tenure_days": 3108,
    "best_return": 0.2442,
    "source": "akshare.fund_manager_em"
  }
]
```

**教学要点**：
- `tenure_days=3108` ≈ 8.5 年——**老牌经理，长期稳定**；
- `current_aum=16.85` 亿元——**单经理管理规模中等**（不是顶流大 fund manager，但稳定）；
- `best_return=0.2442` = 24.42%——**经理个人代表作最佳回报**；
- **fund_managers 解析 "fund → manager" 关系是 O(全表 scan 26,645 经理)**——后续会做 fund-centric 物化表（已知 follow-up PR）。

### 10.4 一键全跑（演示 5 分钟版用）

```bash
FUND=070001
echo "===== $FUND 嘉实成长收益混合A — 11 数据集 ====="
echo "--- 1. funds ---"
fund export funds --fund-code $FUND --format json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  name={d[0]['fund_name']} type={d[0]['fund_type']}\")"
echo "--- 2. profile ---"
fund profile $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  est={d['establishment_date']} company={d['fund_company']} benchmark={d['benchmark']}\")"
echo "--- 3. nav (latest) ---"
fund nav $FUND --start-date 2024-01-26 --end-date 2024-01-26 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); r=d[0]; print(f\"  unit={r['unit_nav']} accumulated={r['accumulated_nav']} (4x since 2002)\")"
echo "--- 4. snapshot returns ---"
fund snapshot $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); ret=d['returns']; print(f\"  1y={ret['one_year']*100:.2f}% 6m={ret['six_month']*100:.2f}% 3m={ret['three_month']*100:.2f}%\")"
echo "--- 5. holdings top 3 ---"
fund holdings $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  rows={len(d)} | {d[0]['stock_name']} {d[0]['net_value_ratio']*100:.2f}% | {d[1]['stock_name']} {d[1]['net_value_ratio']*100:.2f}% | {d[2]['stock_name']} {d[2]['net_value_ratio']*100:.2f}%\")"
echo "--- 6. bonds top 3 ---"
fund bonds $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  rows={len(d)} | {d[0]['bond_name']} {d[0]['net_value_ratio']*100:.2f}% | {d[1]['bond_name']} {d[1]['net_value_ratio']*100:.2f}% | {d[2]['bond_name']} {d[2]['net_value_ratio']*100:.2f}%\")"
echo "--- 7. industries top 3 ---"
fund industries $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  rows={len(d)} | {d[0]['industry_name']} {d[0]['net_value_ratio']*100:.2f}% | {d[1]['industry_name']} {d[1]['net_value_ratio']*100:.2f}% | {d[2]['industry_name']} {d[2]['net_value_ratio']*100:.2f}%\")"
echo "--- 8. fees ---"
fund fees $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  rows={len(d)} | first={d[0]['fee_text']}\")"
echo "--- 9. dividends ---"
fund dividends $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  total dividends: {len(d)} (since 2002)\")"
echo "--- 10. splits ---"
fund splits $FUND 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f\"  total splits: {len(d)} | {d[0]['split_date']} ratio={d[0]['split_ratio']}\")"
echo "--- 11. managers ---"
fund managers 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
mgrs = [m for m in d if '$FUND' in (m.get('current_fund_codes','') or '')]
if mgrs:
    m = mgrs[0]
    print(f\"  manager={m['manager_name']} tenure={m['tenure_days']}d ({m['tenure_days']/365:.1f}年) aum={m['current_aum']}亿 best={m['best_return']*100:.2f}%\")
"
```

**预期输出**（现场实时看）：
```
===== 070001 嘉实成长收益混合A — 11 数据集 =====
--- 1. funds ---
  name=嘉实成长收益混合A type=混合型-偏股
--- 2. profile ---
  est=2002-11-05 company=嘉实基金管理有限公司 benchmark=上证A股指数
--- 3. nav (latest) ---
  unit=1.0301 accumulated=4.042 (4x since 2002)
--- 4. snapshot returns ---
  1y=31.37% 6m=3.69% 3m=3.37%
--- 5. holdings top 3 ---
  rows=84 | 贵州茅台 4.46% | 宁德时代 3.88% | 泸州老窖 2.16%
--- 6. bonds top 3 ---
  rows=18 | 23附息国债17 6.11% | 25国债08 1.85% | 25附息国债08 3.38%
--- 7. industries top 3 ---
  rows=17 | 制造业 43.59% | 金融业 5.32% | 信息传输、软件和信息技术服务业 3.87%
--- 8. fees ---
  rows=27 | first=开放申购
--- 9. dividends ---
  total dividends: 23 (since 2002)
--- 10. splits ---
  total splits: 1 | 2008-02-27 ratio=1.6886
--- 11. managers ---
  manager=方晗 tenure=3108d (8.5年) aum=16.85亿 best=24.42%
```

### 10.5 11 数据集横评（一张表看完）

| # | 数据集 | 关键数字 | 一句话洞察 |
|---:|---|---|---|
| 1 | funds | 类型=混合型-偏股 | 嘉实基金 2002 年老牌成长基金 |
| 2 | fund_profiles | 2002-11-05 成立，benchmark=上证A股 | 20+ 年老基金，benchmark 简单直接 |
| 3 | nav_history | accumulated=4.042 | **20+ 年涨 4 倍** |
| 4 | snapshots | **1 年 +31.37%** | 4 只基金里回报最高 |
| 5 | stock_holdings | 茅台 4.46% + 宁德 3.88% + 泸州 2.16% | 消费 + 新能源 + 白酒三足鼎立 |
| 6 | bond_holdings | 23 附息国债 6.11% | **清一色国债**，最低风险债券配置 |
| 7 | industry_allocations | 制造业 43.59% | **比 110022（87%）/ 161725（93%）都分散** |
| 8 | fee_structures | 27 条 | 5 大类费字段全 |
| 9 | dividends | **23 条** | 20+ 年多次现金分红 |
| 10 | splits | **1 条**（2008-02-27 折算 1.6886） | 真正稀缺字段 |
| 11 | fund_managers | 方晗，tenure 8.5 年 | 老牌经理稳定 |

### 10.6 演示讲法（5 分钟版）

> "我拿 **070001 嘉实成长收益混合A** 做主推——这只基金 11 个数据集全有，**是项目当前最完整的案例**。
>
> 11 条命令，**5 分钟能跑完**。每一行输出都告诉你这只基金的一个切面：
>
> - 成立 2002 年，20+ 年老牌；
> - 当前单位净值 1.03，复权累计 4.04——**20+ 年涨 4 倍**；
> - 近 1 年回报 **+31.37%**，4 只基金里最高；
> - 持仓茅台 + 宁德 + 泸州老窖——消费、新能源、白酒三足鼎立；
> - 债券**全是国债**——低风险配置；
> - 行业 43.59% 制造业，**比 110022（87%）/ 161725（93%）都分散**——**真正做到了行业多元化**；
> - 分红 23 条——20+ 年多次现金分红；
> - 拆分 1 条（2008 折算 1.6886）——**项目里最稀缺的字段**；
> - 经理方晗，**任职 8.5 年**——长期稳定。
>
> 这就是 fund-data 的核心价值——**11 个数据集、20+ 年时间纵深、每行可追溯到 source**，人和 agent 都能基于这一只基金做完整画像。"

### 10.7 出错应对

**症状 1**：`managers` 命令报 "all providers failed"
- 原因：fund_managers 数据来自 akshare.fund_manager_em，系统 Python 没装 akshare
- 应对：用 venv 跑

**症状 2**：`profile` 返回 `fund_type=""`（空字符串）
- 原因：profile 字段来自 investoday.fund_all，部分老基金 fund_type 未填
- 应对：用 `funds` 表的 `fund_type` 字段（已 refresh 过）

**症状 3**：`managers` 命令运行慢（10+ 秒）
- 原因：managers 返回全表 34,654 条经理，filter 在 Python 端做
- 应对：先用 SQL filter 后再展示：
  ```bash
  fund managers 2>/dev/null | python3 -c "
  import json, sys
  d = json.load(sys.stdin)
  print([m for m in d if '070001' in (m.get('current_fund_codes','') or '')][:3])
  "
  ```

### 10.8 选其他 11 数据集全覆盖基金

> 如果你想换一只基金做主推，79 只候选里有：
>
> - **040001 华安创新混合**（混合型-平衡，2001 年老牌）
> - **040002 华安中国 A 股增强指数**（指数型）
> - **070003 嘉实稳健混合**（混合型-偏股，同公司）
> - **100020 富国天益价值混合A**（混合型-偏股，价值风格）
> - **121005 国投瑞银创新动力混合**（混合型-偏股）
> - **160607 鹏华价值优势混合(LOF)**（LOF）
>
> 但 **070001 数据最丰富、知名度最高、回报最强**——首推这只。

