# fund-data 项目演示讲稿

> 现场检查日期：2026-06-03 Asia/Shanghai  
> 演示目标：证明项目当前可用，并用一个基金代码展示“查询、覆盖判断、来源说明、缺失说明”的完整闭环。  
> 演示基金：`110022`，易方达消费行业股票。

## 1. 当前可用性结论

项目现在可以用于本地基金数据查询、覆盖率检查、数据导出和 agent / MCP
集成。

已验证的可用路径：

- CLI 可用：`fund_cli.py` 能直接读取默认 cloud query bundle。
- 本地数据库可用：默认指向
  `/Users/xiongjiali/.cache/fund-data/releases/2026-06-02-1701/fund_data_query.sqlite`。
- 数据覆盖检查可用：`coverage-report` 可以按单只基金或批量基金输出覆盖结果。
- 环境自检可用：`doctor --skip-network --quiet` 返回结构化 JSON。
- Python 嵌入入口可用：`PYTHONPATH=fund-data` 后可以 `from scripts import fund_data`。

当前 doctor 的关键结果：

| 项目 | 结果 |
|---|---|
| Python | OK，当前为 3.13.3 |
| 数据库 | OK，业务表完整 |
| 默认数据源 | cloud cache |
| 已安装 bundle | `2026-06-02-1701` |
| 基金池 | 26,953 只 |
| 同步失败队列 | 0 |
| AkShare | 已在 `.venv-akshare` 中安装 1.18.64；系统 Python 下未安装时为 degraded ok |
| Investoday | 未设置 `INVESTODAY_API_KEY`（或旧名 `INVESTDATA_API_KEY`）时跳过付费源，属于预期状态 |

注意：如果 `cloud status --manifest-url ...` 显示
`update_available: true`，说明远端 manifest 有新版本。演示时可以先执行
`cloud pull` 更新；如果现场网络或远端文件不可用，继续使用已校验的本地
bundle 演示即可。

## 2. 演示前检查命令

从项目根目录运行：

```bash
python3 fund-data/scripts/fund_cli.py doctor --skip-network --quiet
```

建议现场只讲几个字段：

- `database.ok=true`：本地查询库可读。
- `coverage.total_funds=26953`：当前基金池已经加载。
- `default_db.source=cloud_cache`：默认不是空库，而是已安装的数据包。
- `sync_failures.count=0`：当前查询 bundle 没有失败队列。

再检查远端状态：

```bash
python3 fund-data/scripts/fund_cli.py cloud status \
  --manifest-url https://fund-data-public-l.oss-cn-shanghai.aliyuncs.com/fund-data/current/manifest.json
```

可以这样解释：

“这个命令不是业务查询，而是版本检查。它告诉我本地装的是哪一版数据包，
远端是否有新版本。如果现场要追求最新数据，就先 pull；如果只是演示
功能闭环，本地已校验 bundle 足够。”

## 3. 现场演示例子

### 3.1 查询单只基金覆盖率

```bash
python3 fund-data/scripts/fund_cli.py coverage-report --code 110022
```

当前预期输出重点：

```json
{
  "total_funds": 1,
  "average_completeness": 0.75,
  "rows": [
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
  ]
}
```

现场讲法：

“这里我选 `110022` 做演示。它不是只返回一个基金名称，而是把这只基金
在本项目中的数据覆盖情况一次性列出来。可以看到基础档案有，历史净值
有 264 行，股票持仓有 136 行，债券持仓有 12 行，行业配置有 20 行，
费率有 4 行。缺的是分红和拆分，这不一定是错误，因为很多基金本身就
没有分红或拆分事件。”

### 3.2 查询净值历史

```bash
python3 fund-data/scripts/fund_cli.py nav 110022 \
  --start-date 2024-01-01 \
  --end-date 2024-01-31
```

现场讲法：

“这个命令展示的是时间序列数据。它可以用于回测、走势检查或和其他
组合数据做关联。项目里会保留 `source` 和 `fetched_at`，所以后续汇报
数字时可以说明数据来自哪里、什么时候抓取。”

### 3.3 查询快照

```bash
python3 fund-data/scripts/fund_cli.py snapshot 110022
```

现场讲法：

“快照数据适合做当前状态展示，例如最新净值、估算收益、起购金额、页面
上公开的股票代码列表等。它和历史净值的区别是：快照反映抓取时点，
历史净值反映日期序列。”

### 3.4 Python 代码里直接调用

```bash
PYTHONPATH=fund-data python3 - <<'PY'
from scripts import fund_data

db_path = fund_data.default_db_path()
rows = fund_data.coverage_report(db_path=db_path, codes=["110022"])
print(db_path)
print(rows[0]["fund_code"], rows[0]["fund_name"], rows[0]["completeness"])
PY
```

现场讲法：

“这一步证明它不只是命令行工具，也可以作为 Python 数据能力嵌进别的
项目里。agent、脚本、Notebook 或服务层都可以复用同一个入口。”

## 4. 3 到 5 分钟讲稿

大家好，我介绍一下这个 `fund-data` 项目。

这个项目要解决的问题很明确：我们需要一个本地可查询、可追溯、能被
agent 和脚本复用的中国公募基金数据底座。它不是临时爬一个页面，也不是
只保留一份 CSV，而是把基金池、净值、快照、档案、持仓、行业、费率、
分红、拆分、基金经理这些数据统一落到 SQLite 里。

目前项目已经可以使用。默认情况下，命令行会优先读取已经下载到本机的
cloud query bundle。这个 bundle 是查询版数据库，体积比 full DB 小，
但保留了业务查询所需的核心表。我们刚才通过 doctor 检查可以看到：
数据库 OK，业务表完整，基金池有 26,953 只，同步失败队列是 0。

数据来源上，项目不是依赖单一接口。无 key 的 Eastmoney 负责基金池、
净值和快照；AkShare 负责档案、持仓、债券、行业、费率、分红和拆分等
公开数据；Tushare 和 Investoday 是可选增强源，分别需要自己的 token
或 API key。这样设计的好处是：免费公开源能支撑基本使用，付费源或
专业源可以在需要时补充质量和覆盖。

我用 `110022`，易方达消费行业股票，做一个例子。运行
`coverage-report --code 110022` 后，可以看到它的基础档案存在，净值有
264 行，股票持仓有 136 行，债券持仓有 12 行，行业配置有 20 行，费率
有 4 行，整体 completeness 是 0.75。缺失项是分红和拆分。这一点很重要：
项目不会把空数据假装成完整数据，而是把“有”和“缺”都显式说出来。
同时，分红和拆分天然就比较稀疏，所以缺失不一定代表系统失败。

这个项目还特别适合 agent 使用。它提供 CLI，也提供 MCP server，还能被
Python 直接 import。也就是说，同一套数据能力可以服务给命令行、自动化
脚本、Notebook、MCP 客户端和后续的前端查询工具。

最后，我会强调一个边界：这些数据用于研究和分析，不构成投资建议。
真正引用任何数字时，都应该带上数据来源和抓取时间。项目已经在数据库
和导出结果里保留了 `source` 和 `fetched_at`，这也是它适合做长期数据
底座的原因。

## 5. 观众可能问的问题

### 这个项目现在能不能直接用？

能。至少本地 CLI 查询、覆盖率报告、doctor 自检、默认 cloud query
bundle 和 Python 嵌入入口都已经验证通过。

### 数据是不是全量？

基金池是全量维度，目前有 26,953 只。不同数据集覆盖率不同：基金池、
档案、快照、费率接近完整；净值覆盖约 97% 以上；股票持仓、债券持仓、
行业配置受基金类型和公开披露限制影响，覆盖约 49% 到 57%；分红和拆分
天然稀疏。

详细覆盖表看：

```bash
open docs/data-coverage-summary.md
```

### 缺失数据怎么解释？

先分两类：真实缺口和结构性为空。比如新基金、后端份额、上游接口暂时
没有数据，属于真实缺口；货币基金没有股票持仓、很多基金没有拆分事件，
属于结构性为空。

### 如果现场网络不好怎么办？

演示优先使用本地已经安装的 cloud query bundle。只要 doctor 显示
`default_db.ok=true`，覆盖率报告和本地查询都不依赖现场网络。

### 以后还需要补什么？

优先级建议：

1. 增加 fund-centric 的基金经理关联表或 view。
2. 给 holdings / bond / industry 缺口做按基金类型的解释标签。
3. 把 `coverage-report` 的输出增加更适合前端展示的 summary schema。
4. 做一个只读 Web UI，用来演示搜索、覆盖率、数据来源和缺失原因。
5. 增加 per-dataset freshness，让每类数据都有自己的新鲜度判断。

## 6. 演示结束语

“总结一下，`fund-data` 现在已经不是一个概念验证，而是一个可运行的
本地基金数据底座。它有本地数据库、有自动拉取的数据包、有 CLI、有
MCP、有 Python API，也有覆盖率和缺失解释。下一步不是从零开始，而是
在现有数据底座上继续补展示层、补结构化缺口解释、补更细的数据新鲜度。”
