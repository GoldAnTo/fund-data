# OSS 流量 / 成本预估报告

> 生成时间 2026-06-04 11:21 Asia/Shanghai
> 工具：ossutil 1.7.18 + 本地 cache dir 探查
> 适用：fund-data 公共 OSS bucket `oss://fund-data-public-l/fund-data/`

---

## 1. 现状（实拉数据）

| 指标 | 数值 |
|---|---|
| OSS 对象数 | 36（18 个 gz + 18 个 sha256） |
| 存储占用 | 2.82 GB（du 含 multipart = 3.25 GB） |
| 已发布版本数 | 18 个 gz 版本 |
| 最新 gz 大小 | **135.8 MB**（压缩）/ 856 MB（解压后 SQLite）|
| 最小 gz | 19.9 MB（2026-06-01-230019，最早一次） |
| 最大 gz | 135.8 MB（最新） |

## 2. Publish 频次（publisher 侧）

- 第一次 publish：**2026-06-01 23:00**
- 最新一次 publish：**2026-06-04 05:47**
- 总跨度：**~55 小时**
- 已发版本：**18 个**
- **平均每 3 小时 publish 一次**（实际开发期比这更密）
- 月预估：**~250 次 publish/月**（按 12 小时/次 估算）

## 3. Pull 频次（你本机）

- `~/.cache/fund-data/releases/` 目录里有 8 个版本（你是手动攒的）
- `current.json` 最后更新：**2026-06-03 11:29**
- **真实 pull 频次：手动、按需，估计 1-2 次/周**（约 4-8 次/月）
- 单次 pull 流量：**135.8 MB**（gz）
- 月 pull 流量：8 次 × 135.8 MB = **~1.1 GB/月**

## 4. 成本预估（阿里云 OSS Standard，cn-shanghai，2026 公开价）

| 项 | 单价 | 当前用量 | 月费用 |
|---|---|---|---|
| 存储 | ¥0.12 / GB / 月 | 2.82 GB | **¥0.34** |
| 公网流出（pull） | ¥0.50 / GB | 1.1 GB | **¥0.55** |
| PUT 请求（publish） | ¥0.01 / 万次 | 250 次 | **¥0.00** |
| GET 请求（pull/manifest） | ¥0.01 / 万次 | 16 次 | **¥0.00** |
| **合计** | | | **~¥0.89 / 月** |

## 5. 扩展场景预估（如果 agent 数量 / 频次变化）

> 假设每个 agent 每天 pull 1 次 = 30 次/月

| Agent 数量 | 月 pull 次数 | 月出流量 | 月费用 |
|---|---|---|---|
| 1（本机） | 30 | 4.0 GB | **¥2.0** |
| 5 | 150 | 20 GB | **¥10.0** |
| 10 | 300 | 40 GB | **¥20.0** |
| 50 | 1500 | 200 GB | **¥100.0** |
| 100 | 3000 | 400 GB | **¥200.0** |

| Pull 频次 / Agent | 月出流量（1 agent） | 月费用（1 agent）|
|---|---|---|
| 每天 1 次 | 4.0 GB | ¥2.0 |
| 每 6 小时 1 次 | 16 GB | ¥8.0 |
| 每小时 1 次 | 96 GB | ¥48.0 |
| 每 5 分钟 1 次 | 1.1 TB | **¥570** |

## 6. 关键发现

1. **存储费几乎可忽略**（2.82 GB × ¥0.12 = ¥0.34/月）——但**版本累积是问题**，如果你不清旧版本，1 年后可能存 30+ GB。
2. **出流量是真实成本驱动**——目前你手动 1 pull/周 = ¥0.55/月；agent 化部署（每天 1 pull × 100 agent）= ¥200/月。
3. **Publish 侧基本免费**（PUT 请求费可以忽略；存储增长靠 lifecycle 自动清理旧版本来压）。
4. **每次 publish 流量（出 OSS）** 实际是**入 OSS**（你本机 → OSS）——**阿里云 OSS 入流量免费**。所以 publish 多少次都不花钱。
5. **bundle 体积增长趋势**：19.9 MB（1 day）→ 135.8 MB（3 days）= **~70 MB/day 增长**（反映持仓/费率/经理等 capability 数据补全）。如果继续每天 publish 1 次且数据继续补全，1 年后 gz 可能到 **1-2 GB 量级**（30+ 倍）。

## 7. 优化 ROI 评估

按当前真实使用（¥0.89/月），**优化没 ROI**——你 1 杯咖啡的钱（¥15-30）能买 12+ 个月的 OSS 账单。

**值得优化的触发条件**（任一）：

| 触发 | 临界值 | 影响 |
|---|---|---|
| Agent 数量 | > 10 持续运转 | 月费 > ¥20 |
| Pull 频次 | 每小时 1 次 | 月费 > ¥50 |
| Bundle 体积 | gz > 500 MB | 单次 pull 变慢 + 流量跳一档 |
| Publish 版本数 | > 50 个未清理 | 存储费占比上升 |

## 8. 如果以后真要优化，路线优先级

按 ROI 排：

1. **OSS lifecycle policy**（3 行配置）—— 自动清理 30 天前的旧 release，把存储费压平。
   成本：零开发，存 1 个月 ¥0.34 → 永远是 ¥0.34。
2. **OSS CDN 加速**（10 分钟）—— 把 `oss://fund-data-public-l.oss-cn-shanghai.aliyuncs.com` 走阿里云 CDN，**公网流出 ¥0.50/GB → CDN ¥0.18/GB**（按 2026 公开价）。
   成本：零开发，10 agent 场景月费从 ¥20 → ¥7.2。
3. **按表分片 bundle**（中-高开发量）—— Publisher 改 `build-bundle` 加 `--tables`，Puller 改 `cloud pull` 按表选下。
   成本：1-2 天开发 + 多文件 manifest 校验逻辑。**只有在 agent 真的"只关心某几张表"时才有用**。
4. **Delta bundle**（高开发量）—— Publisher 记 last-pulled cursor，bundle 只含 changed rows。
   成本：1 周+。**目前你 18 个版本总共才 2.82 GB storage，delta 价值低**。

## 9. 立即可做的（如果你想现在动）

```bash
# 1. 跑 ossutil 现状报告（不消耗任何流量）
ossutil du oss://fund-data-public-l/fund-data/ -v

# 2. 设置 lifecycle（30 天自动删除旧 release）
#    走阿里云控制台 > OSS > bucket > 基础设置 > 生命周期 > 创建规则
#    路径前缀: releases/, 过期天数: 30, 操作: 删除

# 3. 如果想实时看流量，aliyun CLI 也可以看
#    aliyun oss get-bucket-stat --bucket fund-data-public-l
#    需要 RAM AccessKey 权限 oss:GetBucketStat
```

---

**TL;DR：当前月费用 ~¥0.89，触发优化需要 agent 规模 / 频次显著增长。现在不动。**
