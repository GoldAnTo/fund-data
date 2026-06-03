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
