# Investoday Fund API Catalog

更新时间: 2026-06-01

## 来源

- 官方文档入口: <https://data-api.investoday.net/hub?url=%2Fapidocs%2Fai-native-financial-data>
- 真实文档 URL: <https://std.investoday.net/apidocs/ai-native-financial-data>（hub 是 SPA 壳，需访问 std 子域拿到完整 markdown）
- API 总览: <https://std.investoday.net/apidocs/api-overview>
- 每个接口的 URL 模板: <https://std.investoday.net/apidocs/api-reference/{接口名}/{接口名}>
- API 基础地址: `https://data-api.investoday.net/data`
- 鉴权方式: HTTP header `apiKey: <INVESTDATA_API_KEY>`

## 汇总结论

按当前网站可见的 API 文档全量复核后，基金相关接口分三层：

- **基金产品数据接口**：48 条，分布于 8 个子分类（基金行情、基金资料、基金业绩表现、基金投资组合、基金持有人、特色数据、ETF 基金、基金财务数据）
- **基金工作流辅助接口**：3 条（综合标的搜索、实体识别、闪电诊基）
- **描述里出现"基金/ETF"但不是基金产品接口**：4 条（已在末尾单独说明，不建议落入基金底座）

> 官方"基金"导航的二级分类共 10 个，本次抓取覆盖了 8 个产品分类 + 2 个非基金（公告类、提示词类，已纳入相应的工作流表中）。其余 `tools/`（工具）分类下的接口不属于基金产品范围。

## 通用调用规则

- **Base URL**: `https://data-api.investoday.net/data`
- **鉴权 header**: `apiKey: $INVESTDATA_API_KEY`（必填）
- **GET 接口**：参数放 query string
- **POST 接口**：参数放 JSON body，并带 `Content-Type: application/json`
- **分页参数**：`pageNum`（最小 1）、`pageSize`（最小 1，最大 500，官方示例常用 10）
- **基金代码**：6 位字符串，如 `110022`、`000001`、`510300`
- **绝大多数 POST 接口使用「`fundCode` 或 `fundCodes` 二选一」约定**：必须且只能传其中一个
- **响应统一格式**：
  ```json
  {
    "code": 0,
    "message": "success",
    "data": [...] 或 {...}
  }
  ```
  成功 `code=0`，失败 `code` 为非 0 业务码，`message` 含错误信息

### 通用 cURL 模板

GET（少数接口，如 `/fund-quote/realtime`、`/fund/all`、`/search`）：

```bash
curl -sS "$BASE_URL/fund/all" \
  -H "apiKey: $INVESTDATA_API_KEY" \
  --get \
  --data-urlencode "pageNum=1" \
  --data-urlencode "pageSize=500"
```

POST（绝大多数接口）：

```bash
curl -sS "$BASE_URL/fund/nav/history" \
  -H "apiKey: $INVESTDATA_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{
    "fundCode": "110022",
    "beginDate": "2024-01-01",
    "endDate": "2024-12-31",
    "pageNum": 1,
    "pageSize": 500
  }'
```

### Python 通用封装

```python
import os
import requests

BASE_URL = os.getenv("FINANCIAL_DATA_BASE_URL", "https://data-api.investoday.net/data")
API_KEY = os.environ["INVESTDATA_API_KEY"]


def investoday_get(path: str, params: dict | None = None) -> dict:
    """GET 请求，自动加 apiKey 头。"""
    response = requests.get(
        f"{BASE_URL}{path}",
        headers={"apiKey": API_KEY},
        params=params or {},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def investoday_post(path: str, body: dict | None = None) -> dict:
    """POST 请求，JSON body，自动加 apiKey + Content-Type 头。"""
    response = requests.post(
        f"{BASE_URL}{path}",
        headers={"apiKey": API_KEY, "Content-Type": "application/json"},
        json=body or {},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


# 示例：拉全市场基金列表
funds = investoday_get("/fund/all", {"pageNum": 1, "pageSize": 500})

# 示例：拉某只基金的 1 年历史净值
nav = investoday_post(
    "/fund/nav/history",
    {
        "fundCode": "110022",
        "beginDate": "2024-01-01",
        "endDate": "2024-12-31",
        "pageNum": 1,
        "pageSize": 500,
    },
)
```

## 推荐拉取顺序

1. **发现 + 基础资料**：`/search` → `/fund/all` → `/fund/basic-info` → `/fund/categories` → `/fund/code-associations` → `/fund/listings-record` → `/fund/subscription-redemption-status`
2. **净值收益**：`/fund/nav/history` → `/fund/adjusted-navs` → `/fund/currency-yield-history` → `/fund/return-rate` → `/fund/eval-peer-avg-ind` → `/fund/performance-attribution`
3. **持仓与资产配置**：`/fund/portfolio-stock-holdings` → `/fund/portfolio-bond-holdings` → `/fund/portfolio-fund-holdings` → `/fund/portfolio-asset-holdings` → `/fund/hold-industry` → `/fund/industry-hold-fund` → `/fund/concept-hold-fund`
4. **基金公司与经理**：`/fund-manager/basic-info` → `/fund/current-manager-returns` → `/fund/manager/performance` → `/fund/manager/interval-returns` → `/fund/manager/hist-performance` → `/fund-company/evaluations`
5. **事件类**：`/fund/fee-structures` → `/fund/dividend` → `/funds/share-splits` → `/fund/shares-changes` → `/fund/holder-structures` → `/fund/award-records` → `/fund/announcements`
6. **ETF 专项**：`/fund-quote/realtime` → `/fund/etf-sub-redemption-list` → `/fund/etf-constituent-stocks`
7. **AI 辅助**：`/api/prompt/diagnosis-fund`（结合 LLM 生成基金诊断文本）

---

## 1. 基金行情（4 条）

### #1 基金未复权日行情

- **方法 / 路径**：`POST /fund/daily-quotes`
- **Tool ID**：`list_fund_daily_quotes`
- **API 等级**：`L1(x1)`
- **用途**：根据基金代码和日期范围查询历史日行情，含开高低收、成交量、成交金额。
- **入参**：`fundCode` / `fundCodes`（二选一） · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `date` · `prevClose` · `openPrice` · `highPrice` · `lowPrice` · `closePrice` · `volume` · `amount`
- **cURL**：
  ```bash
  curl -X POST "$BASE_URL/fund/daily-quotes" \
    -H "apiKey: $INVESTDATA_API_KEY" -H "Content-Type: application/json" \
    -d '{"fundCode":"000001","beginDate":"2024-01-01","endDate":"2024-12-31","pageNum":1,"pageSize":500}'
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金未复权日行情/基金未复权日行情>

### #2 基金前复权日行情

- **方法 / 路径**：`POST /fund/adjusted-quotes`
- **Tool ID**：`list_fund_adj_quotes`
- **API 等级**：`L1(x1)`
- **用途**：根据基金代码和日期范围查询前复权日行情。
- **入参**：同 #1
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `date` · `prevClosePrice` · `openPrice` · `highPrice` · `lowPrice` · `closePrice` · `volume` · `amount`
- **cURL**：
  ```bash
  curl -X POST "$BASE_URL/fund/adjusted-quotes" \
    -H "apiKey: $INVESTDATA_API_KEY" -H "Content-Type: application/json" \
    -d '{"fundCode":"000001","beginDate":"2024-01-01","endDate":"2024-12-31","pageNum":1,"pageSize":500}'
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金前复权日行情/基金前复权日行情>

### #3 ETF 最新实时日行情

- **方法 / 路径**：`GET /fund-quote/realtime`
- **Tool ID**：`get_fund_quote_realtime`
- **API 等级**：`L3(x5)`
- **用途**：获取 ETF 基金最新实时行情，含价格、涨跌幅、最高最低价、数据时间。
- **入参（query string）**：`fundCode`（必填）
- **关键出参字段**：`fundCode` · `fundName` · `marketType`（`sh`/`sz`）· `openPrice` · `closePriceYDay` · `currentPrice` · `changeRatio` · `highPrice` · `lowPrice` · `dataTime` · `sysTime` · `status`
- **cURL**：
  ```bash
  curl "$BASE_URL/fund-quote/realtime?fundCode=159001" \
    -H "apiKey: $INVESTDATA_API_KEY"
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/etf最新实时日行情/etf最新实时日行情>

### #4 基金技术指标

- **方法 / 路径**：`POST /fund/technical-indicators`
- **Tool ID**：`list_fund_tech_indicators`
- **API 等级**：`L2(x2)`
- **用途**：查询基金技术指标数据，包含压力位、支撑位。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `date` · `fundAsset` · `pressure` · `support`
- **cURL**：
  ```bash
  curl -X POST "$BASE_URL/fund/technical-indicators" \
    -H "apiKey: $INVESTDATA_API_KEY" -H "Content-Type: application/json" \
    -d '{"fundCode":"000001","beginDate":"2024-01-01","endDate":"2024-12-31","pageNum":1,"pageSize":500}'
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金技术指标/基金技术指标>

---

## 2. 基金资料（13 条）

### 2.1 基金概况（6 条）

#### #5 基金基本信息

- **方法 / 路径**：`POST /fund/basic-info`
- **Tool ID**：`get_fund_basic_info`
- **API 等级**：`L1(x1)`
- **用途**：查询基金名称、类型、管理人、托管人、投资目标、策略、风险收益特征、关键日期等基础属性。
- **入参**：`fundCode` / `fundCodes` · `pageNum` · `pageSize`（**注意：此接口的 `fundCode` 与 `fundCodes` 不强制二选一**）
- **关键出参字段**：`fundId` · `fundCode` · `fundName` · `tradeName` · `indexFundType` · `fundType` · `isQdii` · `isFof` · `lockPeriod` · `exchangeCode` · `listStatus` · `establishDate` · `listDate` · `delistDate` · `expireDate` · `custodianName` · `investmentField` · `investmentObjective` · `benchmarkCode` · `outstandingShares` · `managementCompanyName` · `fundNameFull` · `fundMasterId` · `investmentStrategy` · `investmentPhilosophy` · `riskReturnProfile` · `fundMasterCode` · `exchangeSubscriptionCode` · `exchangeName` · `backEndSubscriptionCode` · `settlementDays`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金基本信息/基金基本信息>

#### #6 基金分类

- **方法 / 路径**：`POST /fund/categories`
- **Tool ID**：`get_fund_categories`
- **API 等级**：`L1(x1)`
- **用途**：查询基金基础信息及一级、二级、三级分类体系。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `fundTypeL1Code` · `fundTypeL1Name` · `fundTypeL2Code` · `fundTypeL2Name` · `fundTypeL3Code` · `fundTypeL3Name` · `investmentType`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金分类/基金分类>

#### #7 基金代码关联

- **方法 / 路径**：`POST /fund/code-associations`
- **Tool ID**：`get_fund_code_assoc`
- **API 等级**：`L1(x1)`
- **用途**：查询基金与其他基金的历史关联关系（封转开、复制、分级基金关联等）。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`fundId` · `fundCode` · `fundName` · `relationType` · `relatedFundId` · `relatedFundCode` · `relatedFundName` · `changeDate` · `endDate`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金代码关联/基金代码关联>

#### #8 基金的现任基金经理及回报

- **方法 / 路径**：`POST /fund/current-manager-returns`
- **Tool ID**：`list_fund_current_manager_returns`
- **API 等级**：`L2(x2)`
- **用途**：查询基金当前基金经理、任期回报、从业年限、擅长类型等。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`date` · `fundCode` · `fundId` · `fundName` · `fundManagerCode` · `fundManagerName` · `employmentBeginDate` · `tenureReturn` · `goodAtType` · `goodAtMarketTrend` · `careerBeginDate` · `yearsOfPractice` · `researchField` · `avgAnnualizedPerformance`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金的现任基金经理及回报/基金的现任基金经理及回报>

#### #9 全市场基金列表（GET）

- **方法 / 路径**：`GET /fund/all`
- **Tool ID**：`list_fund_all`
- **API 等级**：`L1(x1)`
- **用途**：获取全市场基金列表，支持分页。**官方文档同时提供 GET 和 POST 两个入口，返回字段一致**。
- **入参（query string）**：`pageNum` · `pageSize`（max 500）
- **关键出参字段**：同 #5（fund basic-info 字段全集）
- **cURL**：
  ```bash
  curl "$BASE_URL/fund/all?pageNum=1&pageSize=500" \
    -H "apiKey: $INVESTDATA_API_KEY"
  ```
- **文档（GET）**：<https://std.investoday.net/apidocs/api-reference/全市场基金列表/全市场基金列表>

#### #10 全市场基金列表（POST）

- **方法 / 路径**：`POST /fund/all`
- **Tool ID**：`list_fund_all`
- **API 等级**：`L1(x1)`
- **用途**：与 GET 入口等价。POST 版本适合在统一客户端中以 body 传分页。
- **入参（body）**：`pageNum` · `pageSize`
- **cURL**：
  ```bash
  curl -X POST "$BASE_URL/fund/all" \
    -H "apiKey: $INVESTDATA_API_KEY" -H "Content-Type: application/json" \
    -d '{"pageNum":1,"pageSize":500}'
  ```
- **文档（POST）**：<https://std.investoday.net/apidocs/api-reference/全市场基金列表/全市场基金列表-1>

### 2.2 基金状态与变动（2 条）

#### #11 基金发行上市

- **方法 / 路径**：`POST /fund/listings-record`
- **Tool ID**：`get_fund_listings_record`
- **API 等级**：`L1(x1)`
- **用途**：查询基金发行与上市信息，含发行要素、关键日期、发行统计。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `firstPublishDate` · `prospectusPublishDate` · `issueTarget` · `issueStartDate` · `issueEndDate` · `parValue` · `issuePrice` · `issueFee` · `issueTotalShare` · `validSubscriptionShare` · `interestConversionShare` · `sponsorSubscriptionShare` · `subscribeCode` · `validSubscriptionAccounts` · `oversubscriptionMultiple` · `subscribeOpenDate` · `redeemOpenDate` · `currencyCode` · `issueStatusCode` · `managementFee` · `custodyFee`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金发行上市/基金发行上市>

#### #12 基金申购赎回状态

- **方法 / 路径**：`POST /fund/subscription-redemption-status`
- **Tool ID**：`list_subscription_redemption_status`
- **API 等级**：`L1(x1)`
- **用途**：查询基金申购赎回状态变更历史和交易限制。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `changeTypeName` · `subscribeLimit` · `resumeDate` · `channelName` · `objectType` · `objectName` · `remark`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金申购赎回状态/基金申购赎回状态>

### 2.3 基金经理信息（1 条）

#### #13 基金经理基本信息

- **方法 / 路径**：`POST /fund-manager/basic-info`
- **Tool ID**：`get_fund_manager_basic_info`
- **API 等级**：`L1(x1)`
- **用途**：查询指定基金的基金经理基础信息、任职情况和背景介绍。
- **入参**：`fundCode` / `fundCodes`（二选一）· `isIncumbent`（`1` 在任 / `0` 历任）
- **关键出参字段**：`fundId` · `fundName` · `fundCode` · `announceDate` · `fundManagerCode` · `fundManagerName` · `region` · `gender` · `birthDate` · `educationLevel` · `graduationSchool` · `certificate` · `position` · `isIncumbent` · `startDate` · `endDate` · `backgroundDesc` · `partyId`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金经理基本信息/基金经理基本信息>

### 2.4 基金投资配置（2 条）

#### #14 基金业绩比较基准配置

- **方法 / 路径**：`POST /fund/performance-benchmarks`
- **Tool ID**：`list_fund_perf_benchmarks`
- **API 等级**：`L1(x1)`
- **用途**：查询基金业绩比较基准配置，含基准指数、基准利率、权重、年化收益率。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `startDate` · `endDate` · `serialNo` · `indexCode` · `indexName` · `benchmarkRatePct` · `benchmarkRateName` · `benchmarkWeightPct` · `returnAnnPct`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金业绩比较基准配置/基金业绩比较基准配置>

#### #15 基金投资标的及比例

- **方法 / 路径**：`POST /fund/investment-targets`
- **Tool ID**：`list_fund_invest_targets`
- **API 等级**：`L1(x1)`
- **用途**：查询基金投资标的类别、代码、名称、最大/最小投资比例、生效日期。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `publishDate` · `investTargetType` · `effectiveDate` · `investTargetCode` · `investTargetName` · `maxRatio` · `minRatio` · `description` · `ratioBenchmark` · `remark`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金投资标的及比例/基金投资标的及比例>

### 2.5 基金事件与公告（1 条）

#### #16 基金获奖信息

- **方法 / 路径**：`POST /fund/award-records`
- **Tool ID**：`get_fund_award_records`
- **API 等级**：`L1(x1)`
- **用途**：查询基金奖项、颁奖单位、获奖年度、获奖基金公司等。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`fundId` · `fundCode` · `fundName` · `awardCode` · `awardName` · `awardOrgCode` · `awardOrgName` · `awardObjectTypeCode` · `publishDate` · `awardYear` · `companyId` · `companyName`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金获奖信息/基金获奖信息>

### 2.6 基金费率与规模（1 条）

#### #17 基金费率

- **方法 / 路径**：`POST /fund/fee-structures`
- **Tool ID**：`list_fund_fee_structures`
- **API 等级**：`L1(x1)`
- **用途**：查询公募基金（J、K 类）的详细费率信息，含费率类别、币种、适用客户、费率范围、计算方式、执行状态。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `publishDate` · `dataSource` · `isExecuted` · `executeDate` · `cancelDate` · `chargeCategoryDesc` · `chargeCurrency` · `applicableClientTypeDesc` · `transferInFundCode` · `transferInFundScope` · `chargeRateMin` · `chargeRateMax` · `chargeCalcMethod` · `chargeUnit` · `chargeRangeDesc` · `chargeDesc` · `chargeDivisionDesc` · `chargeCriteria1` / 2 / 3 阶梯费率
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金费率/基金费率>

### 2.7 基金公司（1 条）

#### #18 基金公司综合评价

- **方法 / 路径**：`POST /fund-company/evaluations`
- **Tool ID**：`get_fund_company_evals`
- **API 等级**：`L2(x2)`
- **用途**：查询基金所属基金公司的综合实力、管理规模、经理评分及不同基金类型评价。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`date` · `fundCode` · `fundName` · `companyCode` · `companyName` · `companyNameFull` · `establishDate` · `fundCount` · `aumTotal` · `fundCountNonMonetary` · `aumNonMonetary` · `aumNonMonetaryRankPct` · `managerScoreAvg` · `managerScoreRankPct` · 各类基金（股票/债券/混合/货币/QDII/商品/另类/FOF/指数）的 `fundCountTop*` / `aumTop*` / `topFundRatioRank*` · `isExpert*` 各类型擅长标识 · `managerCount` · `managerTenureAvgDays`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金公司综合评价/基金公司综合评价>

---

## 3. 基金业绩表现（14 条）

### 3.1 净值数据（3 条）

#### #19 基金历史净值

- **方法 / 路径**：`POST /fund/nav/history`
- **Tool ID**：`list_fund_nav_history`
- **API 等级**：`L1(x1)`
- **用途**：查询基金历史净值，含单位净值、累计净值、发布日期。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `date` · `nav` · `navAcc`
- **cURL**：
  ```bash
  curl -X POST "$BASE_URL/fund/nav/history" \
    -H "apiKey: $INVESTDATA_API_KEY" -H "Content-Type: application/json" \
    -d '{"fundCode":"110022","beginDate":"2024-01-01","endDate":"2024-12-31","pageNum":1,"pageSize":500}'
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金历史净值/基金历史净值>

#### #20 基金历史复权净值

- **方法 / 路径**：`POST /fund/adjusted-navs`
- **Tool ID**：`list_fund_adj_navs`
- **API 等级**：`L1(x1)`
- **用途**：查询基金历史复权单位净值。
- **入参**：同 #19
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `date` · `navAdj`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金历史复权净值/基金历史复权净值>

#### #21 货币基金收益情况

- **方法 / 路径**：`POST /fund/currency-yield-history`
- **Tool ID**：`list_currency_yield_history`
- **API 等级**：`L1(x1)`
- **用途**：查询货币基金万份收益、七日年化收益率、基金资产净值。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `date` · `avgDailyProfitPer10k` · `avg7dYieldAnn` · `nav` · `shareClass`
- **文档**：<https://std.investoday.net/apidocs/api-reference/货币基金收益情况/货币基金收益情况>

### 3.2 收益与排名（2 条）

#### #22 基金回报率

- **方法 / 路径**：`POST /fund/return-rate`
- **Tool ID**：`list_fund_return_rate`
- **API 等级**：`L1(x1)`
- **用途**：查询日、周、月、季度、半年、年、今年以来等多维度回报率。
- **入参**：`fundCode` / `fundCodes`（二选一）— **注意：本接口只接受 `fundCode` 或 `fundCodes`，不要传 beginDate/endDate/pageNum/pageSize**
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `returnDaily` · `returnYtd` · `return1w` · `return1m` · `return3m` · `return6m` · `return1y` · `return3y`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金回报率/基金回报率>

#### #23 基金评价同类平均指标

- **方法 / 路径**：`POST /fund/eval-peer-avg-ind`
- **Tool ID**：`get_fund_peer_avg_metric`
- **API 等级**：`L2(x2)`
- **用途**：查询基金与同类平均的收益、波动、回撤、风险收益等评价指标。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**（按 9 个时间维度 sinceInception / ytd / 1w / 1m / 3m / 6m / 1y / 3y / 5y 输出 8 大类指标）：
  - `returnXxx` 累计收益率
  - `returnXxxPeerAvg` 同类平均
  - `returnRankXxx` 同类排名
  - `alphaReturnXxx` α 收益
  - `volatilityAnnXxx` 年化波动率
  - `maxDrawdownXxx` 最大回撤
  - `volatilityDownsideAnnXxx` 下行波动率
  - `sharpeRatioXxx` 夏普比率
  - `trackingErrorXxx` 跟踪误差
  - `betaXxx` 贝塔
  - `varXxx` VaR
  - `sortinoRatioXxx` 索提诺比率
  - `informationRatioXxx` 信息比率
  - `treynorRatioXxx` 特雷诺比率
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金评价同类平均指标/基金评价同类平均指标>

### 3.3 基金经理业绩（3 条）

#### #24 基金经理任职收益

- **方法 / 路径**：`POST /fund-manager/performance`
- **Tool ID**：`list_fund_mgr_perf`
- **API 等级**：`L2(x2)`
- **用途**：查询基金经理任职收益、任职天数、任职状态及管理基金信息。
- **入参**：`fundCode` / `fundCodes`（二选一）· `managerName` · `employmentStatus`（`1` 在任 / `0` 历任）
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `fundManagerCode` · `fundManagerName` · `returnAnn` · `endDate` · `workingDays` · `employmentStatus`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金经理任职收益/基金经理任职收益>

#### #25 基金经理区间回报

- **方法 / 路径**：`POST /fund-manager/interval-returns`
- **Tool ID**：`list_fund_mgr_returns`
- **API 等级**：`L2(x2)`
- **用途**：查询基金经理在不同区间的投资回报率。
- **入参**：`fundManagerName`（**必填**）· `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundManagerCode` · `fundManagerName` · `date` · `investmentType` · `returnMtd` · `return1m` · `return3m` · `return6m` · `returnYtd` · `return1y` · `return3y` · `return5y`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金经理区间回报/基金经理区间回报>

#### #26 基金经理历史管理基金业绩

- **方法 / 路径**：`POST /fund-manager/hist-performance`
- **Tool ID**：`list_fund_mgr_hist_per`
- **API 等级**：`L2(x2)`
- **用途**：查询基金经理历史管理基金及任期业绩。
- **入参**：`fundManagerName`（**必填**，支持模糊查询）· `pageNum` · `pageSize`
- **关键出参字段**：`date` · `fundCode` · `fundId` · `fundName` · `fundManagerCode` · `fundManagerName` · `isIncumbent` · `tenureEndDate` · `tenureStartDate` · `returnTenure`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金经理历史管理基金业绩/基金经理历史管理基金业绩>

### 3.4 业绩指标与基准（3 条）

#### #27 归因分析

- **方法 / 路径**：`POST /fund/performance-attribution`
- **Tool ID**：`get_fund_performance_attribution`
- **API 等级**：`L2(x2)`
- **用途**：查询基金综合业绩评估和归因指标（仅 J、K 类）。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**（按 9 个时间维度 sinceInception / ytd / 1w / 1m / 3m / 6m / 1y / 3y / 5y 输出）：
  - `returnAnnXxx` / `returnXxx` / `returnSimpleAnnXxx` 各类收益率
  - `alphaReturnXxx` α 收益
  - `minMonthlyReturnXxx` / `maxMonthlyReturnXxx` 月度最大最小
  - `volatilityAnnXxx` / `volatilityDownsideAnnXxx` 波动率
  - `maxDrawdownXxx` 最大回撤
  - `betaXxx` 贝塔
  - `varXxx` VaR
  - `sharpeRatioXxx` / `sortinoRatioXxx` / `calmarRatioXxx` / `omegaRatioXxx` / `informationRatioXxx` / `treynorRatioXxx` / `m2RatioXxx` / `sterlingRatioXxx` / `burkeRatioXxx` / `tailRatioXxx` / `rachevRatioXxx` / `stabilityIndexXxx` 比率指标
  - `winRateMonthlyXxx` 月度胜率
  - `stockSelectionXxx` / `marketTimingXxx` 选股/择时能力
  - `trackingErrorXxx` 跟踪误差
- **文档**：<https://std.investoday.net/apidocs/api-reference/归因分析/归因分析>

#### #28 业绩比较基准行情

- **方法 / 路径**：`POST /fund/perf-benchmark-quote`
- **Tool ID**：`list_perf_benchmark_quote`
- **API 等级**：`L1(x1)`
- **用途**：查询基金业绩比较基准历史行情。
- **入参**：`benchmarkIndexCode` / `benchmarkIndexCodes`（二选一，**不是 fundCode！**）· `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`benchmarkIndexCode` · `benchmarkIndexName` · `date` · `closePrice` · `chg` · `changePct`
- **文档**：<https://std.investoday.net/apidocs/api-reference/业绩比较基准行情/业绩比较基准行情>

#### #29 基金与指数回报相关系数

- **方法 / 路径**：`POST /fund/index-return-correlations`
- **Tool ID**：`list_fund_idx_ret_corr`
- **API 等级**：`L2(x2)`
- **用途**：查询基金与指数在 6 个月、1 年、3 年、5 年等区间的回报相关系数。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundName` · `date` · `fundId` · `indexCode` · `indexName` · `corr6m` · `corr1y` · `corr3y` · `corr5y`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金与指数回报相关系数/基金与指数回报相关系数>

### 3.5 基金份额与分红（2 条）

#### #30 基金分红

- **方法 / 路径**：`POST /fund/dividend`
- **Tool ID**：`list_fund_dividend_distributions`
- **API 等级**：`L1(x1)`
- **用途**：查询基金分红年度、对象、单位基金收益、分红比例、分红总额。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`date` · `fundCode` · `fundId` · `fundName` · `dividendYear` · `dividendTarget` · `fundIncomePerUnit` · `fundUndistributedIncomePerUnit` · `isDividend` · `dividendRatioBeforeTax` · `dividendRatioAfterTax` · `totalDividendAmount` · `recordDate` · `exDividendDate` · `dividendPayDate` · `planChangeDescription`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金分红/基金分红>

#### #31 基金拆分折算

- **方法 / 路径**：`POST /funds/share-splits`（**注意：路径是 `/funds/` 复数，不是 `/fund/`**）
- **Tool ID**：`list_fund_share_splits`
- **API 等级**：`L1(x1)`
- **用途**：查询基金份额拆分与折算历史记录。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundName` · `fundId` · `splitExDate` · `splitRatio` · `splitTarget` · `navAfterSplit` · `navAccDisclosureNote` · `splitNote`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金拆分折算/基金拆分折算>

---

## 4. 基金投资组合（7 条）

### #32 基金的持仓股票

- **方法 / 路径**：`POST /fund/portfolio-stock-holdings`
- **Tool ID**：`list_fund_portfolio_stock_holdings`
- **API 等级**：`L1(x1)`
- **用途**：查询基金持仓股票代码、名称、数量、市值、占净值比例。
- **入参**：`fundCode` / `fundCodes`（二选一）— **注意：本接口不要传 beginDate/endDate/pageNum/pageSize**
- **关键出参字段**：`date` · `fundCode` · `fundId` · `fundName` · `investmentType` · `stockCode` · `rnk` · `holdingShares` · `marketValue` · `navRatio` · `stockName` · `source`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金的持仓股票/基金的持仓股票>

### #33 基金的资产分布

- **方法 / 路径**：`POST /fund/portfolio-asset-holdings`
- **Tool ID**：`list_fund_portfolio_asset_holdings`
- **API 等级**：`L1(x1)`
- **用途**：查询基金股票、债券、现金等资产配置及占比（**字段特别多，约 60+ 个资产/比率对**）。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`date` · `fundId` · `fundCode` · `fundName` · `nav` · 各类资产 `*Value` / `*Ratio` 字段（股票、债券、现金、国债、金融债、可转债、ABS、贵金属、REIT、衍生品等）· `totalShare` · `totalAssetValue` · `borrowingBalance` · `custodianReviewDate`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金的资产分布/基金的资产分布>

### #34 基金的持仓基金

- **方法 / 路径**：`POST /fund/portfolio-fund-holdings`
- **Tool ID**：`list_portfolio_fund_holdings`
- **API 等级**：`L1(x1)`
- **用途**：查询基金持仓基金数量、市值、占净资产比例、管理人。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`date` · `fundId` · `fundCode` · `fundName` · `holdingFundCode` · `holdingShares` · `holdingValue` · `holdingRatio` · `fundTypeOverseas` · `fundTypeOverseasDesc` · `fundOperationType` · `fundOperationTypeDesc` · `managerName` · `managerId`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金的持仓基金/基金的持仓基金>

### #35 基金的持仓债券

- **方法 / 路径**：`POST /fund/portfolio-bond-holdings`
- **Tool ID**：`list_fund_portfolio_bond_holdings`
- **API 等级**：`L1(x1)`
- **用途**：查询基金持仓债券、排名、数量、市值、占净值比例、摊余成本。
- **入参**：`fundCode` / `fundCodes`（二选一）
- **关键出参字段**：`date` · `fundId` · `fundCode` · `fundName` · `bondId` · `rnk` · `maturityYears` · `marketValue` · `bondQuantity` · `weightNavPct` · `amortizedCost` · `costWeightNavPct` · `isConvertiblePeriod` · `bondName`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金的持仓债券/基金的持仓债券>

### #36 行业持仓的基金列表

- **方法 / 路径**：`POST /fund/industry-hold-fund`
- **Tool ID**：`list_industry_hold_fund`
- **API 等级**：`L1(x1)`
- **用途**：通过行业代码查询持有该行业股票的基金列表及持仓比例。
- **入参**：`fundCode` / `fundCodes`（二选一）· `pageNum` · `pageSize`
- **关键出参字段**：`date` · `fundId` · `fundCode` · `fundName` · `fundType` · `industryStandard` · `industryCode` · `industryName` · `industryLevel` · `holdingWeightLatest` · `holdingWeightQuarterly` · `holdingWeightAnnual` · `holdingRatioLatest` · `holdingRatioQuarterly` · `holdingRatioAnnual` · `holdingRatioLatestPenetrated`
- **文档**：<https://std.investoday.net/apidocs/api-reference/行业持仓的基金列表/行业持仓的基金列表>

### #37 概念持仓的基金列表

- **方法 / 路径**：`POST /fund/concept-hold-fund`
- **Tool ID**：`list_concept_hold_fund`
- **API 等级**：`L2(x2)`
- **用途**：通过概念代码查询持有该概念的基金列表和不同报告期的概念持有比例。
- **入参**：`conceptCode` / `conceptCodes` · `pageNum` · `pageSize`（**注意：参数是 conceptCode 不是 fundCode**）
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `endDate` · `fundType` · `conceptCode` · `conceptName` · `conceptCategory` · `holdingWeightLatest` · `holdingWeightQuarterly` · `holdingWeightAnnual`
- **文档**：<https://std.investoday.net/apidocs/api-reference/概念持仓的基金列表/概念持仓的基金列表>

### #38 基金持仓的行业分布

- **方法 / 路径**：`POST /fund/hold-industry`
- **Tool ID**：`list_fund_hold_industry`
- **API 等级**：`L1(x1)`
- **用途**：查询持有指定行业股票的基金及对应行业占比。
- **入参**：`fundCode` / `fundCodes`（二选一）· `pageNum` · `pageSize`
- **关键出参字段**：同 #36 `行业持仓的基金列表`（字段一致）
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金持仓的行业分布/基金持仓的行业分布>

---

## 5. 基金持有人（2 条）

### #39 基金持有人结构信息

- **方法 / 路径**：`POST /fund/holder-structures`
- **Tool ID**：`list_fund_hold_structures`
- **API 等级**：`L1(x1)`
- **用途**：查询基金持有人户数、户均份额、机构/个人/其他投资者持有份额与占比。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `infoSourceCode` · `infoSource` · `publishDate` · `reportDate` · `holderCount` · `avgHoldingShare` · `instHoldingShare` · `instHoldingRatio` · `retailHoldingShare` · `retailHoldingRatio` · `otherHoldingShare` · `otherHoldingRatio` · `staffHoldingShare` · `staffHoldingRatio`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金持有人结构信息/基金持有人结构信息>

### #40 基金份额变动

- **方法 / 路径**：`POST /fund/shares-changes`
- **Tool ID**：`list_fund_shares`
- **API 等级**：`L1(x1)`
- **用途**：查询基金份额申购、赎回、转入、转出、红利再投资、拆分、扩募、折算等变动。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `endDate` · `dataSource` · `dataSourceDesc` · `startDate` · `initialTotalShare` · `beginShare` · `subscribeShare` · `redeemShare` · `transferInShare` · `transferOutShare` · `dividendReinvestShare` · `splitShare` · `expansionShare` · `conversionShareChange` · `endShare` · `totalEndShare` · `isConsolidated`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金份额变动/基金份额变动>

---

## 6. 特色数据（5 条）

### #41 概念持仓的基金列表（批量）

- **方法 / 路径**：`POST /fund/concept-hold-fund/batch`
- **Tool ID**：`list_concept_hold_fund_batch`
- **API 等级**：`L2(x2)`
- **用途**：批量按概念代码、匹配类型和阈值筛选持有相关概念的基金。
- **入参**：`conceptCodes`（**必填**）· `matchType`（**必填**，1=最新报告持有比重% / 2=季报 / 3=年报或半年报）· `threshold`（**必填**，阈值）· `isETF`
- **关键出参字段**：同 #37
- **文档**：<https://std.investoday.net/apidocs/api-reference/概念持仓的基金列表（批量）/概念持仓的基金列表（批量）>

### #42 行业持仓的基金列表（批量）

- **方法 / 路径**：`POST /fund/industry-hold-fund/batch`
- **Tool ID**：`list_industry_hold_fund_batch`
- **API 等级**：`L2(x2)`
- **用途**：批量通过行业代码查询持有该行业的基金列表。
- **入参**：`industryCodes`（**必填**）· `matchType`（**必填**，1=最新报告（占总值）/ 2=季报（占总值）/ 3=年报（占总值）/ 4=最新报告（占净值）/ 5=季报（占净值）/ 6=年报（占净值）/ 7=最新报告（占净值，含穿透））· `threshold`（**必填**）· `isETF`
- **关键出参字段**：同 #36
- **文档**：<https://std.investoday.net/apidocs/api-reference/行业持仓的基金列表（批量）/行业持仓的基金列表（批量）>

### #43 基金持仓股票及行业涨幅

- **方法 / 路径**：`POST /fund/holdings-stocks-industries`
- **Tool ID**：`list_fund_holdings_perf`
- **API 等级**：`L2(x2)`
- **用途**：查询基金持仓股票、所属行业，以及股票/行业今年以来涨幅。
- **入参**：`fundCode` / `fundCodes`（二选一）— **注意：本接口不要传 beginDate/endDate/pageNum/pageSize**
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `publishDate` · `endDate` · `rnk` · `stockCode` · `stockName` · `returnYtd` · `industryCode` · `industryName` · `industryReturnYtd` · `marketCap`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金持仓股票及行业涨幅/基金持仓股票及行业涨幅>

### #44 行业持仓的基金列表（已计入 #36，本条目是按官方文档编号的占位）

> 实际为 #36 的别名，不重复。已在上文列出。

### #45 基金公司的公告

- **方法 / 路径**：`POST /fund/announcements`
- **Tool ID**：`list_fund_announcements`
- **API 等级**：`L4(x10)`
- **用途**：通过基金代码、公告 ID、日期范围、标题、分页条件查询基金公司公告。
- **入参**：`fundCodes`（**必填**，数组）· `beginDate`（最小 2020-01-01）· `endDate` · `title`（支持模糊查询）· `announcementID` · `pageNum` · `pageSize`
- **关键出参字段**：`date` · `announcementId` · `fundCode` · `fundName` · `title` · `riskType` · `announcementSource` · `announcementContentType`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金公司的公告/基金公司的公告>

---

## 7. ETF 基金（2 条）

### #46 ETF 申购赎回清单基本信息

- **方法 / 路径**：`POST /fund/etf-sub-redemption-list`
- **Tool ID**：`list_etf_sub_red_lists`
- **API 等级**：`L1(x1)`
- **用途**：查询 ETF 申购赎回清单基本信息，含标的指数、现金差额、最小申赎单位。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`publishDate` · `fundId` · `fundCode` · `fundName` · `underlyingIndexCode` · `prevTradeDate` · `cashBalance` · `navPerCreationUnit` · `nav` · `estimatedCashComponent` · `cashSubstitutionRatioLimit` · `creationUnit` · `dividendPerCreationUnit` · `iopvPublishDesc` · `subscribeAllowedDesc` · `redeemAllowedDesc` · `isIopvPublished` · `isSubscribeAllowed` · `isRedeemAllowed` · `subscribeShareLimit` · `redeemShareLimit` · 各类单账户/单日/净额申赎限额 · `iopvClosePrice`
- **文档**：<https://std.investoday.net/apidocs/api-reference/etf申购赎回清单基本信息/etf申购赎回清单基本信息>

### #47 ETF 申购赎回成份股信息

- **方法 / 路径**：`POST /fund/etf-constituent-stocks`
- **Tool ID**：`list_etf_constituent_stks`
- **API 等级**：`L1(x1)`
- **用途**：查询 ETF 申购赎回清单中的成份股、数量、现金替代标志、替代比例。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`publishDate` · `fundId` · `fundCode` · `fundName` · `stockCode` · `stockName` · `stockQuantity` · `cashSubstituteFlagDesc` · `cashSubstituteFlag` · `cashSubstituteRatio` · `subscribeCashSubstitutePremiumRatio` · `redeemCashSubstituteDiscountRatio` · `fixedSubstituteAmount` · `subscribeSubstituteAmount` · `redeemSubstituteAmount` · `constituentMarketCapWeight`
- **文档**：<https://std.investoday.net/apidocs/api-reference/etf申购赎回成份股信息/etf申购赎回成份股信息>

---

## 8. 基金财务数据（2 条）

### #48 基金主要财务指标

- **方法 / 路径**：`POST /fund/financial-indicators`
- **Tool ID**：`list_fund_fin_inds`
- **API 等级**：`L1(x1)`
- **用途**：查询基金报告期主要财务指标，如净值增长率、收益分配、费用、利润。
- **入参**：`fundCode` / `fundCodes` · `beginDate` · `endDate` · `pageNum` · `pageSize`
- **关键出参字段**：`fundCode` · `fundId` · `fundName` · `reportType`（`I`=中报 / `A`=年报）· `publishDate` · `reportPeriodEnd` · `isConsolidated` · `navChangePctCurrent` · `navReturnWeightedAvg` · `distributableIncome` · `distributableIncomePerShare` · `navPerShareEnd` · `navReturnCurrent` · `profitExclFairValueChange` · `navEnd` · `navReturnCumulative` · `profitCurrent` · `fairValueChangeIncome` · `profitPerShareWeightedAvg` · `profitMarginPerShareWeightedAvg` · `navAccAdjEnd` · `totalNavEnd`
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金主要财务指标/基金主要财务指标>

### #49 基金主要财务指标（季度）

- **方法 / 路径**：`POST /fund/financial-indicators-q`
- **Tool ID**：`list_fund_fin_inds_q`
- **API 等级**：`L1(x1)`
- **用途**：查询基金季度主要财务指标。**字段集同 #48，仅 `reportType` 取值为 `Q1`/`Q2`/`Q3`/`Q4`**。
- **入参**：同 #48
- **关键出参字段**：同 #48
- **文档**：<https://std.investoday.net/apidocs/api-reference/基金主要财务指标季度）/基金主要财务指标季度）>

> 注：官方文档 URL 末尾中文括号「）」是文档原样生成，不要在调用时加 — 路径是 `/fund/financial-indicators-q`。

---

## 基金工作流辅助接口（3 条）

### W1 综合标的搜索

- **方法 / 路径**：`GET /search`
- **Tool ID**：`search`
- **API 等级**：`L1(x1)`
- **用途**：搜索沪深京 A 股、基金、ETF、LOF、港股、行业、概念、基金经理、基金公司、基金主题。
- **入参（query string）**：
  - `key`（关键字）
  - `type`（**必填**，多选用 `,` 隔开；11=A 股 / 12=A 股指数 / 13=ETF 基准指数 / 21=基金 / 22=ETF / 23=LOF / 27=基金经理 / 28=基金公司 / 29-基金主题 / 31=港股 / 71=申万 1 级 / 72=申万 2 级 / 81=聚源概念 / 82=财联社概念）
  - `pageNum` · `pageSize`（max 500）
- **关键出参字段**（嵌套两层 `data`）：`pageNum` · `pageSize` · `totalCount` · `totalPage` · `start` · `end` · `data: [{ code, shortName, fullName, first, pinyin, quanPin, type, id, mkt, fundSaleFlag, industryLevel, riskLevel }]`
- **cURL**：
  ```bash
  curl "$BASE_URL/search?key=沪深300&type=21,22,23,27,28,29-基金主题&pageNum=1&pageSize=20" \
    -H "apiKey: $INVESTDATA_API_KEY"
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/综合标的搜索/综合标的搜索>

### W2 实体识别

- **方法 / 路径**：`POST /entity-recognition`
- **Tool ID**：`entity_recognition`
- **API 等级**：`L5(x10)`
- **用途**：输入自然语言文本，识别股票、行业、基金等实体。
- **入参**：`input`（**必填**，待识别的自然语言）
- **关键出参字段**：`data.entities: [{ code, name, correlation, type, reason, level, source }]`
- **cURL**：
  ```bash
  curl -X POST "$BASE_URL/entity-recognition" \
    -H "apiKey: $INVESTDATA_API_KEY" -H "Content-Type: application/json" \
    -d '{"input":"贵州茅台怎么样？"}'
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/实体识别/实体识别>

### W3 闪电诊基

- **方法 / 路径**：`POST /api/prompt/diagnosis-fund`
- **Tool ID**：`prompt_diagnosis_fund`
- **权益**：内部（**非资源包**，是供 LLM 使用的诊断提示词生成工具）
- **用途**：输入基金代码和内容丰富度参数，生成基金诊断提示词。
- **入参**：`fundCode`（**必填**）· `richness`（**必填**，如 `"simple"`）· `industryCodes`（**必填**）· `matchType`（**必填**，同 #42）· `threshold`（**必填**）· `isETF`
- **关键出参字段**：返回与 #42 行业持仓批量接口相同的结构
- **cURL**：
  ```bash
  curl -X POST "$BASE_URL/api/prompt/diagnosis-fund" \
    -H "apiKey: $INVESTDATA_API_KEY" -H "Content-Type: application/json" \
    -d '{"fundCode":"159001","richness":"simple","industryCodes":["640000","740000"],"matchType":1,"threshold":40,"isETF":true}'
  ```
- **文档**：<https://std.investoday.net/apidocs/api-reference/闪电诊基/闪电诊基>

---

## 已审计但不纳入基金产品底座的接口

| API 名称 | 方法 | 路径 | 原因 |
|---|---|---|---|
| 机构持股统计 | `GET` | `/stock/insti_holding` | 描述中有基金、QFII、保险、社保基金等机构类型，但对象是股票机构持股，不是基金产品数据。 |
| 机构持股统计 | `POST` | `/stock/insti_holding` | 同上，属于股票数据。 |
| 行业的最新实时日行情 V2 | `POST` | `/industry-quote/realtime-v2` | 返回行业行情，描述中包含领涨股及 ETF 信息，但主体不是基金。 |
| 概念的最新实时日行情 V2 | `POST` | `/concept-quote/realtime-v2` | 返回概念行情，描述中包含 ETF 信息，但主体不是基金。 |

---

## 数据底座映射建议

| 数据域 | 优先接口 |
|---|---|
| 搜索/识别 | `/search`, `/entity-recognition` |
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
| AI 辅助 | `/api/prompt/diagnosis-fund` |

## 本项目落库建议

`fund-data` 技能在正式 Investoday Provider 中保留当前 AkShare fallback，同时按以下顺序扩表或映射：

- `funds`: `/fund/all`, `/fund/basic-info`, `/fund/categories`
- `fund_search_index`: `/search`, `/entity-recognition`
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
- `fund_ai_prompts`: `/api/prompt/diagnosis-fund`

## 接入优先级

1. 先接发现和基础资料: `/search`, `/fund/all`, `/fund/basic-info`, `/fund/categories`。
2. 再接净值收益: `/fund/nav/history`, `/fund/adjusted-navs`, `/fund/return-rate`, `/fund/currency-yield-history`。
3. 再接持仓与资产配置: `/fund/portfolio-stock-holdings`, `/fund/portfolio-bond-holdings`, `/fund/portfolio-fund-holdings`, `/fund/portfolio-asset-holdings`, `/fund/hold-industry`。
4. 再接基金经理、费率、分红、份额、持有人结构。
5. 最后接 L2/L3/L4/L5 的评价、实时行情、公告、特色批量筛选、AI 诊断接口。
