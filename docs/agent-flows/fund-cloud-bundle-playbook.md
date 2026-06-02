# Fund Cloud Bundle 剧本（Playbook）

> **最后更新:** 2026-06-02
> **目标读者:** 任何人 —— 人或 AI —— 被问到"OSS query bundle 是怎么工作的？"、"为什么 agent 从 cache 读而不是从 `FUND_DATA_DB`？"、"怎么发布新 bundle？"或"query bundle 和 full archive 有什么区别？"。这是 **cloud 分发路径的回答脚本**。配套 [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md)（图表 + 代码锚点）一起看。
>
> **使用场景:**
> - onboarding 新 operator 或 agent 进入数据平面。
> - 审查涉及 `fund_cloud.py`、`fund_cli cloud` 子命令或 `.github/workflows/nightly.yml` upload 步骤的 PR。
> - 排查"agent 读错 DB"或"upload 说成功但啥都没变"或"bundle 的 sha256 不匹配"这类报告。
> - 回答关于公开 query bundle 和私有 full archive 之间隐私边界的问题。
> - 计划 schema 迁移或新表添加。
>
> **不在使用场景之内:**
> - 问题是关于进程内 bootstrap（[`fund-lookup-pipeline.md` §3.2](./fund-lookup-pipeline.md#32-cloud-bootstrap--fund_cloudensure_project_bundle) 覆盖了）。
> - 问题是关于数据平面（search、sync、coverage）—— 用对应的 playbook。
> - 问题是关于把 skill 装到 agent 平台 → 用 [`fund-data/SKILLS.md`](../../fund-data/SKILLS.md)。

---

## 60 秒答案（TL;DR）

`fund-data` 的 cloud bundle 是 **fresh OpenClaw / Codex / Claude daemon 的分发路径**。它是一个 gzip 过的 query-only SQLite（11 个数据表，~700 MB gzipped）加上一个带 sha256 的 manifest，托管在公共 OSS bucket 上。Pull 是个信任链：HTTPS 拉 manifest → 校验 schema 版本 → 下载 .gz → 校验 sha256 → gunzip → atomic rename → 写 `current.json` 指针。Build 是镜像：attach 源 SQLite，用 `INSERT ... SELECT` 复制 11 个表，gzip，写 manifest，按 `gz → sha256 → manifest` 顺序上传三件 artifact 到 OSS，这样 poll `current/manifest.json` 的消费者永远看不到半发布的发布。

定义性特征：

- **公开读，私有写。** Query bundle 在公共 bucket（`fund-data-public-l`，`cn-shanghai`）。Full archive（含调用方 IP 的 `raw_responses`）发到私有 bucket / 私有 prefix。
- **三件 artifact。** `fund_data_query.sqlite.gz`、它的 `.sha256` sidecar 和一个 `manifest.json` 描述两个。三件是 publish 单位。
- **三层信任链。** Manifest 验证、HTTPS 下载 URL、下载文件的 sha256。恶意 manifest 在下载开始前被拒绝；被篡改的 .gz 在 gunzip 之前被拒绝。
- **Atomic 一切。** `.download` 文件存 in-flight .gz 和 .db，然后 `os.replace` 到最终路径。Pull 中途崩溃留下 `.download` 文件，下一次 pull 覆盖；cache 从不处于半发布状态。
- **两个存储层，有意为之。** Query bundle 剥掉 `raw_responses` / `sync_runs` / `sync_failures`。Full archive 保留它们。选择就是隐私边界。

---

## 完整回答模板（用这个骨架）

当被问到"cloud bundle 是怎么工作的？"，按这个结构回答，**四段对应两侧**。顺序重要 —— 跟 publish-then-consume 运行时流一致。

### 第 1 段 —— Publish 侧

> Operator（或 CI）跑 `fund-cli cloud build-bundle` 产生三件 artifact。`build_bundle`（line 71）打开源 SQLite，把它 attach 到一个新的 query DB，对 11 个 query 表中的每一个跑 `CREATE TABLE + INSERT INTO ... SELECT * FROM`。目标 pragmas 设为 `journal_mode=OFF`、`synchronous=OFF`、`temp_store=MEMORY` —— 不需要持久性因为源是权威的。Build 创索引匹配 agent 读模式（`funds.fund_name`、`funds.fund_type`、`nav_history.nav_date`、`fund_profiles.fund_company`、`fund_managers.current_fund_codes`），跑 `VACUUM` 回收半空 page，以 `compresslevel=9` gzip 结果，计算 sha256，然后写 `manifest.json`，带 `kind: "fund-data-cloud-bundle"`、`schema_version: 1` 和 per-table 行数。

### 第 2 段 —— Upload 侧

> Operator（或 CI）然后跑 `fund-cli cloud upload`，shell 出去调三次 `ossutil cp -f local oss://...`。**`-f` 是必需的** —— 没有它，ossutil 在已有 key 上提示 "y or N"，非交互 shell 静默 no-op。上传顺序是 **gz 先，然后 sha256，然后 manifest 在 `current/manifest.json`**，是消费者面对的指针。Poll manifest 的消费者永远看不到半发布状态：要么 manifest 指向上一个版本（没变化），要么指向新版本（一切都已经就位）。如果 manifest 在 .gz 之前上传，中间窗口里消费者读 manifest 引用一个不存在的 .gz。

### 第 3 段 —— Pull 侧

> 消费者侧，任何不带显式 `db` 参数的 tool 调用触发 `_maybe_bootstrap_cloud`，它调 `ensure_project_bundle`。那个函数是个 5 步 gate：如果设了 `FUND_DATA_DB`，skip；如果 `FUND_DATA_AUTO_PULL=0`，skip 并返回 `fallback: "api"`；如果 `current.json` 存在且指向一个现有文件，复用 cache；否则调 `pull_bundle(manifest_url)`。`pull_bundle` 读 manifest，验证 `kind` / `version` / `schema_version` / `files.query_db.{sha256,url|path}`，下载 .gz 到 `.download` 文件，sha256 验证（对 manifest），gunzip 到 `.download` db，然后 `os.replace` 两个到最终路径。以原子方式写 `current.json`，带版本指针。

### 第 4 段 —— Status 和隐私边界

> Status tool（`fund_cloud_status` MCP，`fund-cli cloud status` CLI）报告三个视图：本地 cache（装了什么）、远端 manifest（最新是什么）、一个比较（`update_available: bool`）。Query bundle **剥掉三个审计表** —— `raw_responses`（上游 HTTP header 里有调用方 IP）、`sync_runs`、`sync_failures`。Full archive（`cloud archive-full`）保留它们，给私有 operator 备份用，不是公开分发。Manifest 的 `privacy: "private"` 字段就是警告；团队的 `archive-full` 文档明确说"存到私有 bucket 或私有对象 prefix"。

---

## 12 个最常被问到的问题（含详细答案 + 为什么这么设计）

下面这些问题是在 onboarding、support、PR review 中最常出现的。**按这里出现的顺序回答，用同样的详细程度** —— 这些是团队经过多轮"但为什么？"之后沉淀下来的解释。

### Q1. 为什么 query bundle 公开但 full archive 私有？

- **Query bundle 剥掉三个审计表** —— `raw_responses`、`sync_runs`、`sync_failures`。剩下的 11 个表是公开数据平面：基金名、NAV 历史、snapshot、持仓、profile 等。数据从 Eastmoney / AkShare 已经公开；bundle 是下载便利，不是隐私扩张。
- **`raw_responses` 是泄漏源。** 它存储完整的上游 HTTP body，包括上游 proxy 加的任何 `X-Forwarded-For` 或其他调用方 IP header。一个消费者的 `raw_responses` 会把消费者的 IP 暴露给拉这个 bundle 的每个其他消费者。
- **`sync_runs` 和 `sync_failures` 是 operator telemetry。** 它们是 per-sync audit log 和失败队列；在不同机器上的 agent 用不到它们。
- **Full archive 保留所有三个表** 给私有 operator 用例（audit、rebuild、debug）。Manifest 的 `privacy: "private"` 字段是警告，团队的 `archive-full` 文档明确说"存到私有 bucket 或私有对象 prefix"。把 full archive 发到公共 prefix 是数据泄漏，不是配置错误。

### Q2. 为什么上传顺序是 `gz → sha256 → manifest`？

- **`manifest.json` 在 `current/manifest.json` 是消费者面对的指针。** Poll 这个文件并跟随它引用的消费者必须永远看不到半发布状态。
- **如果 manifest 先上传**，中间窗口里的消费者会读一个指向还没上传的 `.gz` 的 manifest，下载会 404。消费者要么重试（可能在下一次尝试成功，但没顺序保证）要么报告失败。
- **如果 `.gz` 和 `.sha256` 先上传**，manifest 上传就是原子提交：从消费者角度，新版本在一个可观察的步骤里出现。
- **团队在 `cloud upload` 的源码和 `fund-data/SKILLS.md` §Cloud data cache 里文档化了这点。** Subcommand 之外的 `ossutil cp` 手动调用可能违反顺序；subcommand 强制了。

### Q3. 为什么 pull 在已经走 HTTPS 的情况下还要验证 sha256？

- **HTTPS 保护信道，不保护发布者。** 网络上的中间人不能篡改传输中的字节，但能妥协 OSS bucket 的 attacker（或有写访问权的恶意内部人）可以用一个 sha256 跟 manifest 宣传的不一样的 .gz 替换。Sha256 检查拒绝那种。
- **Manifest 是"应该是什么"的契约；sha256 是"实际是那个"的检查。** 跳过检查的消费者信任发布者永远不被妥协；跑检查的消费者对发布者没法防御的一类攻击是安全的。
- **代价是每次 pull 一次 sha256 计算。** Hash 700 MB 文件在现代硬件上是亚秒级。延迟在下载，不在 hash。验证是免费的。

### Q4. 为什么 build 是 `ATTACH` + `INSERT INTO ... SELECT` 而不是 `sqlite3 backup()`？

- **`backup()` 是逐行、慢的。** 它通过 Python 流式传输每个 page，意思是 build 要花一个小时以上跑 2.5 GB 源。`ATTACH` + `INSERT INTO ... SELECT` 路径在 SQLite 内部跑复制，这是 build 需要维持的每秒几百 MB 传输率的唯一方式。
- **`backup()` 是给活源的在线备份。** Build 跑的是静态源（operator 停了写或从 checkpoint DB build）；`ATTACH` 是离线情况下的正确原语。
- **Build 从 `sqlite_master.sql` 给每个表加 `CREATE TABLE`**，不是用源的 schema。这让 build 对 schema 漂移健壮：如果源有一列 query DB 没有（比如半应用的迁移），build 响亮失败而不是静默产生错形状的 DB。`backup()` 会逐字复制源的 schema，包括任何漂移。

### Q5. 为什么目标 pragmas 是 `journal_mode=OFF` / `synchronous=OFF` / `temp_store=MEMORY`？

- **Build 是一次性 copy，不是长跑数据库。** 不需要持久性，因为源是权威 DB；如果 build 崩溃，operator 重新跑。
- **`journal_mode=OFF` 跳过 WAL。** WAL 是给增量持久性的；build 一次写所有东西，从不回读。
- **`synchronous=OFF` 跳过 fsync barrier。** 目标是会被原子重命名的 temp 文件；如果主机在 build 中途崩溃，operator 重新跑。
- **`temp_store=MEMORY` 把 build 的中间结构放在 RAM** 而不是磁盘。SQLite 的 `VACUUM` 和 `ANALYZE` 产生大 temp 表；内存存储省下显著 I/O。

### Q6. 为什么 `current.json` 指针是事实之源，不是文件 mtime？

- **`mtime` 不是版本指针。** 两次产生相同版本的 `cloud pull` 有不同的 `mtime` 值；如果系统时钟粗，两次产生不同版本的调用可以有相同的 `mtime`。从 `mtime` 决定"我要不要重 pull？"的消费者在猜。
- **`current.json` 显式带版本、sha256 和 manifest URL。** 消费者读 `current.json`，拿到版本，跟它想要的版本比，决定。没有猜。
- **指针是原子的**（`_write_json_atomic` 写 `.tmp` 然后 `os.replace`）。Pull 期间读 `current.json` 的消费者看到要么老版本要么新版本，从不是半写文件。

### Q7. 为什么 pull 下载到 `.download` 然后 `os.replace`？

- **原子性。** Pull 中途崩溃的消费者在磁盘上留下 `.download` 文件。下一次 pull 覆盖 `.download`（build 在写之前总是 unlink 目标）而最终路径永远不半写。
- **没有撕裂写。** `os.replace` 在 POSIX 上是原子的（Windows 上从 Python 3.3 也是）。Pull 期间读最终 `.sqlite` 的消费者看到要么老文件要么新文件，从不是半写文件。
- **`current.json` 用同样的方式写**，原因一样。Pull 期间读 `current.json` 的消费者看到要么老指针要么新指针，从不是半写 JSON。

### Q8. 为什么 `pull_bundle` 每次重新读 manifest，而不是缓存 manifest 内容？

- **Manifest 在 pull 之间可能变。** 发布者上传新 `manifest.json` 来宣传新版本。缓存 manifest 的消费者在 cache 被失效之前会错过新版本。
- **Manifest 很小**（几 KB）。网络重读成本亚秒级；验证成本微秒级。缓存会每次 pull 省几百毫秒，代价是正确性。
- **Manifest 是版本指针。** 读 `current/manifest.json` 然后拉引用的 `.gz` 的消费者在跟一个标准 CDN 模式走：一个小的"什么可用"文件 + 一个大的"我实际想要"文件。不必要地缓存小文件让标准模式复杂化。

### Q9. 为什么检查 manifest 的 `kind` 字段？

- **未来 schema 迁移可能产生不同的 manifest kind。** 团队设想 `fund-data-cloud-bundle-v2` 作为下一个 schema；看到 v2 manifest 的老消费者应该拒绝消费而不是静默错误解析。
- **`kind` 检查是一行** 防止一类"静默兼容性破坏"bug。信任 manifest 不检查 kind 的消费者可以消费未来的 v2 manifest 产生不可预测结果。
- **`schema_version` 字段是更细粒度的检查。** 团队两个都用：`kind` 是"这是对家族吗？"检查，`schema_version` 是"这是家族里的对代吗？"检查。

### Q10. 为什么 build 单独写 `manifest.json`，不跟 upload 步骤合并？

- **Build 是源的确定性函数。** 在同一源上跑 build 两次产生相同的三件 artifact（唯一的非确定性是 `updated_at` 时间戳，build 设为 `datetime.now(UTC)`）。Manifest 是 build 的"我产出了什么"记录。
- **Upload 是单独的关切。** Build 把三件 artifact 写到本地磁盘；upload 推送到 OSS。分开让 operator 在 upload 前检查 build（`ossutil` 命令在"它们覆盖已有 key"这个意义上是破坏性的，即使有 `-f`）。
- **CI workflow 顺序跑 build 然后 upload。** Build 失败不触发 upload；upload 失败不重触发 build。两步有独立的失败模式和独立的重试策略。

### Q11. 为什么 `status` 接受 `manifest_url` 参数，为什么是可选的？

- **`manifest_url` 是"最新版本是什么"问题的源。** 想知道要不要 pull 的消费者需要知道本地装了什么和远端可用什么。没 URL，`status` 只报告本地视图。
- **URL 是可选的**，因为不是每个消费者都配置了 manifest URL。`default_manifest_url()` helper 提供项目 OSS bucket 作为默认，但指向私有镜像（比如 air-gapped 部署）的消费者会传自定义 URL。不传的消费者拿到本地视图；传的把视图升级到本地 vs 远端。
- **MCP `fund_cloud_status` tool 包 `status`**，同样的可选性。不带参数调 tool 的 agent 拿到本地视图；带 `manifest_url` 的拿到比较。

### Q12. 为什么是 11 个 query 表，不是所有 14 个？

- **11 个 query 表是 agent 的数据平面。** `funds`、`nav_history`、`snapshots`、`stock_holdings`、`fund_profiles`、`bond_holdings`、`industry_allocations`、`fee_structures`、`dividends`、`splits`、`fund_managers`。这些是 agent 在运行时读的表。
- **3 个被排除的表是 operator telemetry。** `raw_responses`（完整上游 HTTP body；可能含调用方 IP）、`sync_runs`（每次 sync 调用的 audit 行）、`sync_failures`（每次硬失败 sync 调用的队列行）。这些对跑 backfill 的 operator 有意义，对不同机器上的 agent 没用。
- **分割就是隐私边界。** 拉 query bundle 的消费者拿到数据平面但没 audit trail。想要 audit trail 的消费者自己跑 backfill，或者用私有 `cloud archive-full` 命令。
- **如果新表加进来**，团队逐案决定：新的数据表（比如 `fund_benchmarks`）进 `QUERY_TABLES`；新的审计表（比如 `provider_call_log`）进 `EXCLUDED_TABLES`。默认是"数据进，审计出" —— 一个表被排除，除非团队显式加它到 query 列表。

---

## 设计哲学（为什么七层是这个形状）

读完这一节，剩下的 playbook 就显而易见了。

1. **信任链就是契约。** Manifest 验证 + HTTPS + sha256 是三层防御。跑全三层的消费者对发布者没法防御的一类攻击是安全的（被妥协的 bucket、恶意内部人、MITM）。验证便宜；正确性不便宜。
2. **原子性无处不在。** `.download` 文件 + `os.replace` 给 .gz 和 .db；`_write_json_atomic` 给 `current.json`。Pull 中途或 publish 中途崩溃的消费者总能找到 cache 在一致状态。代价是代码里少量的"这个文件半写"复杂度；收益是一个没有撕裂状态的系统。
3. **两个存储层，有意为之。** Query bundle 公开；full archive 私有。选择就是隐私边界，不是便利。每往错 tier 加一个新表就是等着的 bug；团队的 review checklist 问每个 schema 变更"这个表是 query 还是 audit？"。
4. **Manifest 是事实之源。** 消费者读 `current/manifest.json`，跟随引用，下载 .gz，验证 sha256。消费者那边的 `current.json` 是 manifest `version` 字段的 cache；manifest 本身是权威指针。
5. **上传顺序是契约的一部分。** `gz → sha256 → manifest` 顺序确保 poll manifest 的消费者永远看不到半发布状态。重排调用的 `ossutil cp` 是 bug；`cloud upload` subcommand 强制顺序。
6. **Build 是确定性的。** 在同一源上跑 build 两次产生相同的三件 artifact（modulo `updated_at`）。非确定性是时间戳，团队接受它作为其他方面可复现管道中的单一非确定性源。
7. **Query bundle 是"新 agent 起点"。** Fresh OpenClaw daemon 应该能拉 bundle 在 30 秒内 operational。21 小时 AkShare backfill 是 operator 路径，不是 agent 路径；bundle 是团队对让 `fund-data` 是"下载即用"体验而不是"build 才能用"体验的贡献。

---

## 反面教材（不要这么说）

这些是 PR review 和 support 线程里见过的常见错误回答。避开它们。

- **"就 `wget` .gz。"** 没 manifest 就没法知道最新版本或验证 sha256。Pull 路径（`fund-cli cloud pull` 或进程内 bootstrap）是下载的唯一正确方式。
- **"Bundle 含审计表。"** 不含。`raw_responses` 因隐私被排除（`X-Forwarded-For` 可能含调用方 IP）；`sync_runs` 和 `sync_failures` 因是 operator telemetry 被排除。想要 audit trail 的消费者跑 `cloud archive-full` 然后私有存结果。
- **"你可以在同一源上重跑 build 拿相同三件 artifact。"** 差不多。`updated_at` 字段是 `datetime.now(UTC)` 跑跟跑之间会变。其他（.gz 的 sha256、per-table 行数）在同一源状态下是确定性的。
- **"Pull 是 async 的。"** 不是。`cloud pull` CLI 命令（和进程内 `ensure_project_bundle`）是阻塞操作，下载、验证、gunzip、写 `current.json` 然后才返回。前台跑 pull 的 agent 会阻塞 ~30-60 秒；后台跑 pull 的 agent 需要 poll `current.json` 等完成。
- **"`-f` 对 `ossutil cp` 是可选的。"** 不是。没 `-f`，ossutil 在已有 key 上提示 "y or N"；在非交互 shell 里，提示是隐形的，upload 静默 no-op。`cloud upload` subcommand 传 `-f`；subcommand 之外的 `ossutil cp` 手动调用必须也这样。
- **"Bucket 是私有的。"** Query bundle 在 `fund-data-public-l`，是公开读 bucket。Full archive 发到 `fund-data-private`（或私有 prefix）；把 full archive 发到公共 bucket 是数据泄漏，不是配置错误。
- **"你可以把 `FUND_DATA_MANIFEST_URL` 指向 `file://` URL。"** 你可以（`_open_location` 处理 `file` scheme），但不是支持的配置。Manifest 期望 HTTPS 可达；`file://` 路径是实现细节可能变。

---

## 怎么保持这个剧本准确

剧本是团队 *settled* 的解释，不是 live 代码。代码变了，在同一个 PR 里更新剧本。检查项：

- 新表加到 `QUERY_TABLES` 或 `EXCLUDED_TABLES` → 更新 §3.2 和 Q12。
- Build pragmas 变了 → 更新 §3.3 和哲学部分。
- Pull 验证链变了（新检查加了）→ 更新 §3.4 和哲学部分。
- 上传顺序或 `ossutil` 调用变了 → 更新 §3.5 和 Q2。
- 新 env var 落地 → 加到 `fund-cloud-bundle-pipeline.md` 的决策表。
- 新 subcommand 加进来 → 更新 §6 workflow。

如果 PR 改了上面任何一项但没更新剧本，request changes 时指这一节。

---

## 相关文档

- [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) —— 图表 + 代码锚点 + env var 表。
- [`fund-lookup-pipeline.md`](./fund-lookup-pipeline.md) —— bootstrap 的进程内视图（agent 在没 `db` 的情况下调 tool 时发生什么）。
- [`fund-search-playbook.md`](./fund-search-playbook.md) —— Q8 解释为什么 `--include-data` 警告 `raw_responses`；Q13 解释为什么 1 小时 OSS TTL cache 是 nightly backfill 陷阱。
- [`fund-batch-sync-pipeline.md`](./fund-batch-sync-pipeline.md) —— bundle 的数据平面消费者（`cloud pull` 之后 backfill writes 落哪）。
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —— agent-facing skill manifest。
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —— per-platform install 布局；"Cloud data cache" 章节有规范的 `cloud pull` / `cloud status` 调用。
- [`../../fund-data/AGENTS.md`](../../fund-data/AGENTS.md) —— `default_db_path()` vs `doctor.py` 分歧笔记（§Long-running pitfalls），那是最常见的"错 DB"报告。
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —— v0.3.0 backlog。
