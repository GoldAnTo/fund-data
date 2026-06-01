# Investoday Fund API Catalog

更新时间: 2026-06-01

来源:
- 外层入口: https://data-api.investoday.net/hub?url=%2Fapidocs%2Fapi-reference%2F%25E4%25BA%25A4%25E6%2598%2593%25E6%2597%25A5%25E5%258E%2586%25E8%25A1%258D%25E7%2594%259F%2F%25E4%25BA%25A4%25E6%2598%2593%25E6%2597%25A5%25E5%258E%2586%25E8%25A1%258D%25E7%2594%259F
- 实际文档 iframe: https://std.investoday.net/apidocs/ai-native-financial-data
- API 基础地址: `https://data-api.investoday.net/data`
- 鉴权方式: HTTP header `apiKey: <api-key>`

## 汇总口径

本文件汇总官方文档当前可见的基金产品相关接口，纳入路径前缀:

- `/fund/*`
- `/funds/*`
- `/fund-manager/*`
- `/fund-company/*`
- `/fund-quote/*`

未纳入股票资金流向、股票基本面等非基金产品接口，即使英文路径里包含 `fund` 字样。`/fund/all` 在官方文档里同时提供 `GET` 和 `POST`，这里按两个 API 入口分别列出。

## 数据底座映射建议

| 数据域 | 优先接口 |
|---|---|
| 基金池/基础资料 | `/fund/all`, `/fund/basic-info`, `/fund/categories`, `/fund/code-associations`, `/fund/listings-record` |
| 交易状态 | `/fund/subscription-redemption-status`, `/fund/fee-structures` |
| 净值/行情 | `/fund/nav/history`, `/fund/adjusted-navs`, `/fund/daily-quotes`, `/fund/adjusted-quotes`, `/fund-quote/realtime` |
| 收益/评价 | `/fund/return-rate`, `/fund/eval-peer-avg-ind`, `/fund/performance-attribution`, `/fund/technical-indicators` |
| 持仓/组合 | `/fund/portfolio-stock-holdings`, `/fund/portfolio-bond-holdings`, `/fund/portfolio-fund-holdings`, `/fund/portfolio-asset-holdings`, `/fund/hold-industry` |
| 行业/概念反查 | `/fund/industry-hold-fund`, `/fund/industry-hold-fund/batch`, `/fund/concept-hold-fund`, `/fund/concept-hold-fund/batch` |
| ETF 清单 | `/fund/etf-sub-redemption-list`, `/fund/etf-constituent-stocks` |
| 基金经理/公司 | `/fund-manager/basic-info`, `/fund/current-manager-returns`, `/fund-manager/performance`, `/fund-manager/interval-returns`, `/fund-manager/hist-performance`, `/fund-company/evaluations` |
| 分红/份额/持有人 | `/fund/dividend`, `/funds/share-splits`, `/fund/shares-changes`, `/fund/holder-structures` |
| 公告/奖项/财务 | `/fund/announcements`, `/fund/award-records`, `/fund/financial-indicators`, `/fund/financial-indicators-q` |

## 接口总表

| # | 分类 | API 名称 | 方法 | 路径 | Tool ID | 等级 | 用途摘要 | 官方文档 |
|---:|---|---|---|---|---|---|---|---|
| 1 | 公告 | 基金公司的公告 | `POST` | `/fund/announcements` | `list_fund_announcements` | `L4(x10)` | 支持通过基金代码、公告 ID、日期范围、标题和分页条件查询基金公司公告。 | [文档](https://std.investoday.net/apidocs/api-reference/基金公司的公告/基金公司的公告) |
| 2 | 基金行情 | 基金未复权日行情 | `POST` | `/fund/daily-quotes` | `list_fund_daily_quotes` | `L1(x1)` | 根据基金代码和日期范围查询历史日行情，含开高低收、成交量、成交金额等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金未复权日行情/基金未复权日行情) |
| 3 | 基金行情 | 基金前复权日行情 | `POST` | `/fund/adjusted-quotes` | `list_fund_adj_quotes` | `L1(x1)` | 根据基金代码和日期范围查询前复权日行情。 | [文档](https://std.investoday.net/apidocs/api-reference/基金前复权日行情/基金前复权日行情) |
| 4 | 基金行情 | ETF 最新实时日行情 | `GET` | `/fund-quote/realtime` | `get_fund_quote_realtime` | `L3(x5)` | 获取 ETF 基金最新实时行情，含价格、涨跌幅、最高最低价、数据时间等。 | [文档](https://std.investoday.net/apidocs/api-reference/etf最新实时日行情/etf最新实时日行情) |
| 5 | 基金行情 | 基金技术指标 | `POST` | `/fund/technical-indicators` | `list_fund_tech_indicators` | `L2(x2)` | 查询基金技术指标数据，包含压力位、支撑位等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金技术指标/基金技术指标) |
| 6 | 基金资料/概况 | 基金的现任基金经理及回报 | `POST` | `/fund/current-manager-returns` | `list_fund_current_manager_returns` | `L2(x2)` | 查询基金当前基金经理、任期回报、从业年限、擅长类型等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金的现任基金经理及回报/基金的现任基金经理及回报) |
| 7 | 基金资料/概况 | 基金基本信息 | `POST` | `/fund/basic-info` | `get_fund_basic_info` | `L1(x1)` | 查询基金名称、类型、管理人、托管人、投资目标、策略、风险收益特征、关键日期等基础属性。 | [文档](https://std.investoday.net/apidocs/api-reference/基金基本信息/基金基本信息) |
| 8 | 基金资料/概况 | 基金代码关联 | `POST` | `/fund/code-associations` | `get_fund_code_assoc` | `L1(x1)` | 查询基金与其他基金的历史关联关系，如封转开、复制、分级基金关联等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金代码关联/基金代码关联) |
| 9 | 基金资料/概况 | 基金分类 | `POST` | `/fund/categories` | `get_fund_categories` | `L1(x1)` | 查询基金基础信息及一级、二级、三级分类体系。 | [文档](https://std.investoday.net/apidocs/api-reference/基金分类/基金分类) |
| 10 | 基金资料/概况 | 全市场基金列表 | `GET` | `/fund/all` | `list_fund_all` | `L1(x1)` | 获取全市场基金列表，支持分页，返回基金代码、名称、类型、管理人、托管人、上市状态等核心信息。 | [文档](https://std.investoday.net/apidocs/api-reference/全市场基金列表/全市场基金列表) |
| 11 | 基金资料/概况 | 全市场基金列表 | `POST` | `/fund/all` | `list_fund_all` | `L1(x1)` | 获取全市场基金列表，POST 版本，适合统一用请求体传分页或筛选条件。 | [文档](https://std.investoday.net/apidocs/api-reference/全市场基金列表/全市场基金列表-1) |
| 12 | 基金资料/状态与变动 | 基金发行上市 | `POST` | `/fund/listings-record` | `get_fund_listings_record` | `L1(x1)` | 查询基金发行与上市信息，含发行要素、关键日期、发行统计等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金发行上市/基金发行上市) |
| 13 | 基金资料/状态与变动 | 基金申购赎回状态 | `POST` | `/fund/subscription-redemption-status` | `list_subscription_redemption_status` | `L1(x1)` | 查询基金申购赎回状态变更历史和交易限制。 | [文档](https://std.investoday.net/apidocs/api-reference/基金申购赎回状态/基金申购赎回状态) |
| 14 | 基金资料/经理与公司 | 基金经理基本信息 | `POST` | `/fund-manager/basic-info` | `get_fund_manager_basic_info` | `L1(x1)` | 查询指定基金的基金经理基础信息、任职情况和背景介绍。 | [文档](https://std.investoday.net/apidocs/api-reference/基金经理基本信息/基金经理基本信息) |
| 15 | 基金资料/配置与费率 | 基金业绩比较基准配置 | `POST` | `/fund/performance-benchmarks` | `list_fund_perf_benchmarks` | `L1(x1)` | 查询基金业绩比较基准配置，含基准指数、基准利率、权重、年化收益率等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金业绩比较基准配置/基金业绩比较基准配置) |
| 16 | 基金资料/配置与费率 | 基金投资标的及比例 | `POST` | `/fund/investment-targets` | `list_fund_invest_targets` | `L1(x1)` | 查询基金投资标的类别、代码、名称、最大/最小投资比例、生效日期等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金投资标的及比例/基金投资标的及比例) |
| 17 | 基金资料/配置与费率 | 基金获奖信息 | `POST` | `/fund/award-records` | `get_fund_award_records` | `L1(x1)` | 查询基金奖项、颁奖单位、获奖年度、获奖基金公司等信息。 | [文档](https://std.investoday.net/apidocs/api-reference/基金获奖信息/基金获奖信息) |
| 18 | 基金资料/配置与费率 | 基金费率 | `POST` | `/fund/fee-structures` | `list_fund_fee_structures` | `L1(x1)` | 查询基金费率类别、币种、适用客户、费率范围、计算方式、执行状态等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金费率/基金费率) |
| 19 | 基金资料/经理与公司 | 基金公司综合评价 | `POST` | `/fund-company/evaluations` | `get_fund_company_evals` | `L2(x2)` | 查询基金所属基金公司的综合实力、管理规模、经理评分及不同基金类型评价。 | [文档](https://std.investoday.net/apidocs/api-reference/基金公司综合评价/基金公司综合评价) |
| 20 | 基金业绩表现/净值收益 | 基金历史净值 | `POST` | `/fund/nav/history` | `list_fund_nav_history` | `L1(x1)` | 查询基金历史净值，含单位净值、累计净值、发布日期等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金历史净值/基金历史净值) |
| 21 | 基金业绩表现/净值收益 | 基金历史复权净值 | `POST` | `/fund/adjusted-navs` | `list_fund_adj_navs` | `L1(x1)` | 查询基金历史复权单位净值。 | [文档](https://std.investoday.net/apidocs/api-reference/基金历史复权净值/基金历史复权净值) |
| 22 | 基金业绩表现/净值收益 | 货币基金收益情况 | `POST` | `/fund/currency-yield-history` | `list_currency_yield_history` | `L1(x1)` | 查询货币基金万份收益、七日年化收益率、基金资产净值等。 | [文档](https://std.investoday.net/apidocs/api-reference/货币基金收益情况/货币基金收益情况) |
| 23 | 基金业绩表现/净值收益 | 基金评价同类平均指标 | `POST` | `/fund/eval-peer-avg-ind` | `get_fund_peer_avg_metric` | `L2(x2)` | 查询基金与同类平均的收益、波动、回撤、风险收益等评价指标。 | [文档](https://std.investoday.net/apidocs/api-reference/基金评价同类平均指标/基金评价同类平均指标) |
| 24 | 基金业绩表现/净值收益 | 基金回报率 | `POST` | `/fund/return-rate` | `list_fund_return_rate` | `L1(x1)` | 查询日、周、月、季度、半年、年、今年以来等多维度回报率。 | [文档](https://std.investoday.net/apidocs/api-reference/基金回报率/基金回报率) |
| 25 | 基金业绩表现/经理业绩 | 基金经理任职收益 | `POST` | `/fund-manager/performance` | `list_fund_mgr_perf` | `L2(x2)` | 查询基金经理任职收益、任职天数、任职状态及管理基金信息。 | [文档](https://std.investoday.net/apidocs/api-reference/基金经理任职收益/基金经理任职收益) |
| 26 | 基金业绩表现/经理业绩 | 基金经理区间回报 | `POST` | `/fund-manager/interval-returns` | `list_fund_mgr_returns` | `L2(x2)` | 查询基金经理在不同区间的投资回报率。 | [文档](https://std.investoday.net/apidocs/api-reference/基金经理区间回报/基金经理区间回报) |
| 27 | 基金业绩表现/经理业绩 | 基金经理历史管理基金业绩 | `POST` | `/fund-manager/hist-performance` | `list_fund_mgr_hist_per` | `L2(x2)` | 查询基金经理历史管理基金及任期业绩。 | [文档](https://std.investoday.net/apidocs/api-reference/基金经理历史管理基金业绩/基金经理历史管理基金业绩) |
| 28 | 基金业绩表现/指标与基准 | 归因分析 | `POST` | `/fund/performance-attribution` | `get_fund_performance_attribution` | `L2(x2)` | 查询基金综合业绩评估和归因指标。 | [文档](https://std.investoday.net/apidocs/api-reference/归因分析/归因分析) |
| 29 | 基金业绩表现/指标与基准 | 业绩比较基准行情 | `POST` | `/fund/perf-benchmark-quote` | `list_perf_benchmark_quote` | `L1(x1)` | 查询基金业绩比较基准历史行情。 | [文档](https://std.investoday.net/apidocs/api-reference/业绩比较基准行情/业绩比较基准行情) |
| 30 | 基金业绩表现/指标与基准 | 基金与指数回报相关系数 | `POST` | `/fund/index-return-correlations` | `list_fund_idx_ret_corr` | `L2(x2)` | 查询基金与指数在 6 个月、1 年、3 年、5 年等区间的回报相关系数。 | [文档](https://std.investoday.net/apidocs/api-reference/基金与指数回报相关系数/基金与指数回报相关系数) |
| 31 | 基金业绩表现/份额分红 | 基金分红 | `POST` | `/fund/dividend` | `list_fund_dividend_distributions` | `L1(x1)` | 查询基金分红年度、对象、单位基金收益、分红比例、分红总额等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金分红/基金分红) |
| 32 | 基金业绩表现/份额分红 | 基金拆分折算 | `POST` | `/funds/share-splits` | `list_fund_share_splits` | `L1(x1)` | 查询基金份额拆分与折算历史记录。 | [文档](https://std.investoday.net/apidocs/api-reference/基金拆分折算/基金拆分折算) |
| 33 | 基金投资组合 | 基金的持仓股票 | `POST` | `/fund/portfolio-stock-holdings` | `list_fund_portfolio_stock_holdings` | `L1(x1)` | 查询基金持仓股票代码、名称、数量、市值、占净值比例等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金的持仓股票/基金的持仓股票) |
| 34 | 基金投资组合 | 基金的资产分布 | `POST` | `/fund/portfolio-asset-holdings` | `list_fund_portfolio_asset_holdings` | `L1(x1)` | 查询基金股票、债券、现金等资产配置及占比。 | [文档](https://std.investoday.net/apidocs/api-reference/基金的资产分布/基金的资产分布) |
| 35 | 基金投资组合 | 基金的持仓基金 | `POST` | `/fund/portfolio-fund-holdings` | `list_portfolio_fund_holdings` | `L1(x1)` | 查询基金持仓基金数量、市值、占净资产比例、管理人等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金的持仓基金/基金的持仓基金) |
| 36 | 基金投资组合 | 基金的持仓债券 | `POST` | `/fund/portfolio-bond-holdings` | `list_fund_portfolio_bond_holdings` | `L1(x1)` | 查询基金持仓债券、排名、数量、市值、占净值比例、摊余成本等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金的持仓债券/基金的持仓债券) |
| 37 | 基金投资组合 | 行业持仓的基金列表 | `POST` | `/fund/industry-hold-fund` | `list_industry_hold_fund` | `L1(x1)` | 通过行业代码查询持有该行业股票的基金列表及持仓比例。 | [文档](https://std.investoday.net/apidocs/api-reference/行业持仓的基金列表/行业持仓的基金列表) |
| 38 | 基金投资组合 | 概念持仓的基金列表 | `POST` | `/fund/concept-hold-fund` | `list_concept_hold_fund` | `L2(x2)` | 通过概念代码查询持有该概念的基金列表和不同报告期的概念持有比例。 | [文档](https://std.investoday.net/apidocs/api-reference/概念持仓的基金列表/概念持仓的基金列表) |
| 39 | 基金投资组合 | 基金持仓的行业分布 | `POST` | `/fund/hold-industry` | `list_fund_hold_industry` | `L1(x1)` | 查询持有指定行业股票的基金及对应行业占比。 | [文档](https://std.investoday.net/apidocs/api-reference/基金持仓的行业分布/基金持仓的行业分布) |
| 40 | 基金持有人 | 基金持有人结构信息 | `POST` | `/fund/holder-structures` | `list_fund_hold_structures` | `L1(x1)` | 查询基金持有人户数、户均份额、机构/个人/其他投资者持有份额与占比。 | [文档](https://std.investoday.net/apidocs/api-reference/基金持有人结构信息/基金持有人结构信息) |
| 41 | 基金持有人 | 基金份额变动 | `POST` | `/fund/shares-changes` | `list_fund_shares` | `L1(x1)` | 查询基金份额申购、赎回、转入、转出、红利再投资、拆分、扩募、折算等变动。 | [文档](https://std.investoday.net/apidocs/api-reference/基金份额变动/基金份额变动) |
| 42 | 特色数据 | 概念持仓的基金列表（批量） | `POST` | `/fund/concept-hold-fund/batch` | `list_concept_hold_fund_batch` | `L2(x2)` | 批量按概念代码、匹配类型和阈值筛选持有相关概念的基金。 | [文档](https://std.investoday.net/apidocs/api-reference/概念持仓的基金列表（批量）/概念持仓的基金列表（批量）) |
| 43 | 特色数据 | 行业持仓的基金列表（批量） | `POST` | `/fund/industry-hold-fund/batch` | `list_industry_hold_fund_batch` | `L2(x2)` | 批量通过行业代码查询持有该行业的基金列表。 | [文档](https://std.investoday.net/apidocs/api-reference/行业持仓的基金列表（批量）/行业持仓的基金列表（批量）) |
| 44 | 特色数据 | 基金持仓股票及行业涨幅 | `POST` | `/fund/holdings-stocks-industries` | `list_fund_holdings_perf` | `L2(x2)` | 查询基金持仓股票、所属行业，以及股票/行业今年以来涨幅。 | [文档](https://std.investoday.net/apidocs/api-reference/基金持仓股票及行业涨幅/基金持仓股票及行业涨幅) |
| 45 | ETF 基金 | ETF 申购赎回清单基本信息 | `POST` | `/fund/etf-sub-redemption-list` | `list_etf_sub_red_lists` | `L1(x1)` | 查询 ETF 申购赎回清单基本信息，含标的指数、现金差额、最小申赎单位等。 | [文档](https://std.investoday.net/apidocs/api-reference/etf申购赎回清单基本信息/etf申购赎回清单基本信息) |
| 46 | ETF 基金 | ETF 申购赎回成份股信息 | `POST` | `/fund/etf-constituent-stocks` | `list_etf_constituent_stks` | `L1(x1)` | 查询 ETF 申购赎回清单中的成份股、数量、现金替代标志、替代比例等。 | [文档](https://std.investoday.net/apidocs/api-reference/etf申购赎回成份股信息/etf申购赎回成份股信息) |
| 47 | 基金财务数据 | 基金主要财务指标 | `POST` | `/fund/financial-indicators` | `list_fund_fin_inds` | `L1(x1)` | 查询基金报告期主要财务指标，如净值增长率、收益分配、费用、利润等。 | [文档](https://std.investoday.net/apidocs/api-reference/基金主要财务指标/基金主要财务指标) |
| 48 | 基金财务数据 | 基金主要财务指标(季度） | `POST` | `/fund/financial-indicators-q` | `list_fund_fin_inds_q` | `L1(x1)` | 查询基金季度主要财务指标。 | [文档](https://std.investoday.net/apidocs/api-reference/基金主要财务指标季度）/基金主要财务指标季度）) |

## 接入优先级

1. 先接基础资料和净值: `/fund/all`, `/fund/basic-info`, `/fund/nav/history`, `/fund/adjusted-navs`, `/fund/return-rate`。
2. 再接持仓和资产配置: `/fund/portfolio-stock-holdings`, `/fund/portfolio-bond-holdings`, `/fund/portfolio-fund-holdings`, `/fund/portfolio-asset-holdings`, `/fund/hold-industry`。
3. 再接基金经理、费率、分红、份额、持有人结构。
4. 最后接 L2/L3/L4 的评价、实时行情、公告、特色批量筛选接口。

## 本项目落库建议

建议 `fund-data` 技能在正式 Investoday Provider 中保留当前 AkShare fallback，同时按以下顺序扩表或映射:

- `funds`: `/fund/all`, `/fund/basic-info`, `/fund/categories`
- `nav_history`: `/fund/nav/history`, `/fund/adjusted-navs`, `/fund/currency-yield-history`
- `fund_profiles`: `/fund/basic-info`, `/fund/listings-record`, `/fund/subscription-redemption-status`
- `stock_holdings`: `/fund/portfolio-stock-holdings`
- `bond_holdings`: `/fund/portfolio-bond-holdings`
- `fund_holdings`: `/fund/portfolio-fund-holdings`
- `asset_allocations`: `/fund/portfolio-asset-holdings`
- `industry_allocations`: `/fund/hold-industry`
- `fee_structures`: `/fund/fee-structures`
- `dividends`: `/fund/dividend`
- `splits`: `/funds/share-splits`
- `fund_managers`: `/fund-manager/basic-info`, `/fund/current-manager-returns`
- `fund_performance`: `/fund/return-rate`, `/fund/eval-peer-avg-ind`, `/fund/performance-attribution`
- `fund_announcements`: `/fund/announcements`

