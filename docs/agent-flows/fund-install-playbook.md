# Fund Install 剧本（Playbook）

> **最后更新:** 2026-06-02
> **目标读者:** 任何人 —— 人或 AI —— 被问到"怎么把 `fund-data` 装到 OpenClaw / Codex / Claude Code？"、"为什么装失败？"、"symlink 安装和 copy 安装有什么区别？"或"那个隐私 flag 是干嘛的？"。这是 **分发边界的回答脚本**。配套 [`fund-install-pipeline.md`](./fund-install-pipeline.md)（图表 + 代码锚点）一起看。
>
> **使用场景:**
> - onboarding 新开发者或 agent 到数据平面。
> - 审查涉及 `install_skill.py`、`SKILL.md`（manifest）或 `SKILLS.md`（per-platform 布局）的 PR。
> - 排查"装上显示 OK 但 agent 看不到 skill"或"装被拒绝"或"装泄漏了我的 IP"这类报告。
> - 回答关于便携安装和公开发布之间隐私边界的问题。
> - 在安装矩阵里加新 agent 平台。
>
> **不在使用场景之内:**
> - 问题是关于运行时 MCP 表面 → 用 [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md)。
> - 问题是关于数据平面（search、sync、coverage、cloud bundle）—— 用对应的 playbook。
> - 问题是关于给项目做贡献（lint、test、CI）→ 用 [`../../CONTRIBUTING.md`](../../CONTRIBUTING.md)。

---

## 60 秒答案（TL;DR）

`fund-data` 是一个 **单一源树、多平台 skill**，从一个源文件夹（`fund-data/SKILL.md` 加伴随文件）装到 OpenClaw / Codex / Claude Code / 个人 `~/.agents` 目录。安装器（`fund-data/scripts/install_skill.py`）是一个 330 行、零依赖的 Python 脚本，有三个 action（`install` / `uninstall` / `status`）、五个 target，每个 target 两种安装模式（symlink 或 copy）。

定义性特征：

- **一个源树，四个发现 target。** 安装器在每个 target 的路径创建 symlink 或 copy；agent 通过 symlink 或 copy 读文件。四个 target 是 `~/.claude/skills/fund-data` / `~/.codex/skills/fund-data` / `~/.openclaw/skills/fund-data` / `~/.agents/skills/fund-data`。
- **Symlink 是默认，copy 是 override。** 对 `claude` / `openclaw` / `agents` 安装器 symlink；对 `codex` copy（因为 Codex 的发现在所有情况下不跟 symlink）。`--copy` 强制所有 target 都用 copy。
- **三个 action 组成生命周期。** `install` 创建或刷新 target；`uninstall` 移除（对真目录有安全检查）；`status` 报当前状态。
- **两种 data mode。** `none`（默认）排除 SQLite；`copy`（`--include-data`）包含一致 snapshot。`--scrub-raw-responses` 是隐私 flag，在目的地清空 `raw_responses` 表。

---

## 完整回答模板（用这个骨架）

当被问到"怎么把 `fund-data` 装到 agent X？"，按这个结构回答，**四段对应四个关切**。顺序重要 —— 跟安装器的决策流一致。

### 第 1 段 —— 源树和 target 矩阵

> 源树是 repo 根的 `fund-data/` 文件夹，包含 `SKILL.md`（manifest）加 `scripts/`、`references/`、`agents/` 以及 SKILL.md 体的源。安装器把这个树 copy 或 symlink 到四个 target 路径之一：`~/.claude/skills/fund-data`（Claude Code）、`~/.codex/skills/fund-data`（Codex）、`~/.openclaw/skills/fund-data`（OpenClaw，managed scope）、`~/.agents/skills/fund-data`（OpenClaw，personal scope）。`--target` flag 选一个或 `all`；默认是 `all`。父目录不存在的 target（比如你没装 OpenClaw）被 `SKIP`，不是失败。

### 第 2 段 —— 安装模式（symlink vs copy）

> 对 `claude` / `openclaw` / `agents` 安装器创建 symlink：agent 通过它读文件，对源树的任何本地编辑立刻可见。对 `codex` 安装器 copy：Codex 的发现在所有情况下不跟 symlink，所以安装必须是真目录。`--copy` flag 强制所有 target 用 copy。权衡：symlink 安装对编辑源树的开发者是零摩擦；copy 安装需要重跑 `install --copy` 来刷新，对有 pin 住 skill 版本的 CI runner 是正确形状。

### 第 3 段 —— Data mode 和隐私 flag

> 默认 data mode 是 `none`：SQLite（`data/fund_data.sqlite`）和它的 WAL/SHM sidecars、`backfill_logs/`、`raw_responses/`、`backfill_state.json`、`backfill_summary.json` 在 copy 安装时全部 skip。Agent 可以从 provider 重建或拉 cloud bundle。对 air-gapped 安装，`--include-data`（`--copy --data-mode copy` 的简写）通过 `sqlite3 source.backup(target)` copy 一致 SQLite snapshot。`--scrub-raw-responses` flag 是隐私 opt-in：备份后在目的地 `DELETE FROM raw_responses`，去掉上游 proxy 加的调用方 IP。安装器在 `data-mode copy` 生效但没 scrub 时打印警告；警告是 operator 的信号，如果安装会离开机器就重跑带 flag。

### 第 4 段 —— Status、uninstall 和 refresh

> `status` 对每个 target 报五种状态之一：`LINKED`（symlink 指向源）、`INSTALLED`（真目录带 `SKILL.md`）、`STALE`（symlink 指向别处）、`MISSING`（没条目）、`BROKEN`（条目存在但没 `SKILL.md`）。`uninstall` 移除条目：symlink 被 unlink，文件被 unlink，真目录仅当 `--copy` 在 `sys.argv` 里（安全信号）或传了 `--force` 时被 `rmtree`。`install` 是幂等的：已正确指向的现有 symlink 是 no-op；现有 copy 被 merge（源文件覆盖，目的地自己的状态保留）。生命周期是 fresh install → upgrade（重跑）→ downgrade / replace（`--copy` 覆盖）→ uninstall。

---

## 12 个最常被问到的问题（含详细答案 + 为什么这么设计）

下面这些问题是在 onboarding、support、PR review 中最常出现的。**按这里出现的顺序回答，用同样的详细程度** —— 这些是团队经过多轮"但为什么？"之后沉淀下来的解释。

### Q1. 为什么安装器是一个无依赖的 Python 脚本？

- **安装器是副作用工具，不是数据平面的一部分。** 它每台机器跑一次；它不 import `fund_data` 或 `fund_cloud`。零依赖设计意味着安装器可以在 fresh checkout 上跑不 `pip install`。
- **330 行装在一个文件里。** 脚本小到可以端到端读，这是有安全意识的 operator 在装 skill 之前做的事。跨模块拆分会在没让安装故事更清楚的情况下加 import-path 复杂度。
- **Console script `fund-install-skill` 是给 `pip install -e .` 用户的。** 把项目放在 PATH 上的开发者免费拿到 script；CI runner 跑 `pip install` wheel 也拿到。直接跑脚本的开发者（不 `pip install`）就 `python3 fund-data/scripts/install_skill.py ...`。

### Q2. 为什么对 Claude / OpenClaw / `~/.agents` 是 symlink，对 Codex 是 copy？

- **Codex 的发现在所有情况下不跟 symlink。** 团队的测试显示一些 Codex 版本在 scan 时解析 symlink，其他不解析；不一致的行为很脆弱。真目录是安全形状。
- **Symlink 是编辑器的正确形状。** Claude Code 和 OpenClaw 透明地跟 symlink，所以开发者编辑源树里的 `SKILL.md` 在 agent 里立刻看到变化。Copy 安装每次编辑都要重跑 `install --copy`，那是摩擦。
- **团队可以为所有 target 强制 copy。** 权衡是"Claude / OpenClaw 开发者拿到无摩擦编辑循环" vs "四个 target 的安装形状一致"。团队选了无摩擦循环。一致性故事是"`--copy` 对谁都能用"。

### Q3. 为什么 SQLite 从默认 copy 安装中排除？

- **SQLite 是 5+ GB 而且安装时很少需要。** 刚想要 `SKILL.md` 和 scripts 的 agent 可以在首次使用时拉 cloud bundle（`fund-cli cloud pull`）或从 provider 重建。安装是元数据；数据是 runtime。
- **排除列表就是隐私边界。** 即使 SQLite 排除，skip 列表（`raw_responses/`、`backfill_state.json` 等）是显式的"这是 operator telemetry，不是 skill 内容"列表。Skill 是 agent 需要的；audit trail 是 operator 需要的。混它们是 footgun。
- **`--include-data` 是显式 override。** air-gapped 机器的便携安装传 `--include-data` 拿到 SQLite。默认是"小"；override 是"便携"。

### Q4. 为什么 `--scrub-raw-responses` 是 opt-in，不是 opt-out？

- **默认是"保留源有的一切"。** 安装器是 copy 工具；删数据是非 copy 操作。Opt-in 让破坏性步骤显式。
- **警告是 operator 的信号。** 安装器在 `data-mode copy` 生效但没 `--scrub-raw-responses` 时打印 `::warning::The SQLite snapshot includes the raw_responses table...`。警告是 operator 重跑带 flag 的机会。读了警告 *并* 选择保留数据的 operator 在做知情决定。
- **Flag 命名无歧义。** `--scrub-raw-responses` 是动宾配对，不会跟别的东西混。更短的 flag 像 `--private` 或 `--safe` 在 scrub 什么上会有歧义。

### Q5. 为什么安装器跳过 `__pycache__` / `.pyc` / `backfill_logs/`？

- **这些是 runtime artifacts，不是 skill 内容。** `__pycache__/` 和 `*.pyc` 是 Python 的 bytecode cache；fresh 安装在第一次 import 时重建。`backfill_logs/` 是 operator 的 audit log；agent 不需要。`backfill_state.json` 和 `backfill_summary.json` 是 backfill runner 的 state；传输它们会把上一台机器的进度带到新机器。
- **Skip 列表是显式契约。** 项目里加的新 artifact（比如新 cache 目录）通过加到 `_should_skip_install_path` 来排除。契约是"skill 内容是源树里的，减去 skip 列表"。
- **两遍 `_copy_into` 确保目的地永远不携带 skip 列表 artifacts**，即使之前的安装（用不同的 skip 列表）留下了它们。第一遍在源端 skip；第二遍在目的地 unlink。

### Q6. 为什么 `uninstall` 对真目录这么谨慎？

- **目的地是 `~/.claude/skills/fund-data` / `~/.codex/skills/fund-data` / 等等。** 同名的外国 skill（极少见，但 operator 手动在那里建了目录就有可能）会被粗心的 `uninstall` `rmtree` 掉。`REFUSE` 是安全检查。
- **`--copy` 在 `sys.argv` 里是安全信号。** 团队选择字面检测 `sys.argv` 里的 `--copy` flag，因为这个 flag 是 operator 在知道安装是 copy（因此目的地归安装器所有）时传的。备选是单独的 `--yes-i-know-what-im-doing` flag，但团队偏好现有 flag 作为信号。
- **`--force` 是显式 override。** 确定想 `rmtree` 真目录的 operator 传 `--force`。flag 无歧义，不太可能误传。

### Q7. 为什么 `INSTALLED` 状态返回给真目录，不是 `LINKED`？

- **Status 反映 agent 看到什么。** Symlink 是指针；agent 通过指针看到文件。`LINKED` 状态是用户的信号说 symlink 指向期望的源。
- **真目录是 post-copy 状态。** Agent 看到文件；用户的信号是 `INSTALLED`。区别重要因为在 `LINKED` 安装上重跑 `install` 是 no-op（symlink 已经指向源），在 `INSTALLED` 安装上重跑把源 merge 到目的地。
- **`STALE` 安装有指向源以外地方的 symlink。** 最常见的原因是 repo 被移动或重命名；修法是移除 stale symlink 然后重跑 `install`。安装器不自动重写 stale symlink 因为错目标可能根本是另一个 repo。

### Q8. 为什么安装器的安全检查用 `if "--copy" in sys.argv`？

- **检查必须便宜可靠。** 字面查 `sys.argv` 里的 `--copy` 是可能的最便宜检查：没 argparse 解析，没验证，没边界情况。权衡是 operator 必须在 uninstall 命令行传 `--copy`（或 `--force`）；drop flag 的 alias 或包装脚本会失败检查。
- **检查是契约，不是 feature。** 未来用 `args.copy`（解析后的 argparse 值）的重构会是一行变更，但团队偏好字面 `sys.argv` 检查的透明性。读 `_uninstall_one` 的 reviewer 立即知道检查在干什么。
- **检查在意图层，不在能力层。** "Operator 传 `--copy` 了吗？"是问题；"目的地是 copy 安装吗？"是不同问题（更难回答，不查文件系统）。字面检查是最简单的意图测试。

### Q9. 为什么 console script `fund-install-skill` 在 `pyproject.toml` 里声明，不是 `setup.py`？

- **项目用 `pyproject.toml` 作为 build metadata 的单一事实之源。** `setup.py` 不存在；`pyproject.toml` 声明 package、依赖、entry points、ruff 配置、black 配置、mypy 配置。加 `setup.py` 会把 metadata 拆到两个文件。
- **`[project.scripts]` 是 PEP 621 声明 console scripts 的方式。** `pip install -e .` 读这个表，在 PATH 上创建 entry point。备选（`setup.py` 里的 `console_scripts` 加单独的 `entry_points.txt`）是 legacy 方式；`pyproject.toml` 是现代方式。
- **Console script 是 `fund-install-skill`，不是 `install_skill`。** `fund-` 前缀匹配其他 console script（`fund-cli`、`fund-mcp`、`fund-backfill`、`fund-doctor`、`fund-coverage-report`、`fund-retry-failures`）。前缀是项目在 PATH 上的 namespace。

### Q10. 为什么安装器不检查源树的 `git status`？

- **安装器是 file-copy 工具，不是 VCS 工具。** 它不知道 `git`、`hg` 或 `svn`。源树是 operator 磁盘上的东西；dirty tree 原样安装。
- **Operator 负责源状态。** 想要干净安装的 operator 应该在 `install` 前 `git status` 和 `git stash`（或 commit）。安装器不是强制 VCS 卫生的地方。
- **备选会是错的。** "拒绝装 dirty tree" 这样的检查会破坏开发者的"编辑 + 装 + 测试"循环，那是 symlink target 的常见情况。安装器不应该质疑 operator 的工作流。

### Q11. 为什么 `codex` 安装总是 copy，即使 user 没传 `--copy`？

- **团队的测试显示 Codex 不一致地跟 symlink。** 一些 Codex 版本在 scan 时解析 symlink，其他不。不一致的行为很脆弱；安全形状是真目录。
- **团队考虑过加 `--link` flag 强制 Codex 上 symlink。** Flag 是一行变更。团队没加因为用例罕见（想要 Codex 上的 live-edit 循环的开发者可以编辑源树然后重跑 `install --copy`；循环不像 symlink 在 Claude 上那么紧，但可以接受）。
- **`--copy` flag 是显式"对所有 target 强制 copy" override。** 跑 `install --copy` 的 operator 对所有四个 target 都拿到 copy，不管 per-target 默认。

### Q12. 为什么安装器的"merge"分支保留目的地自己的文件？

- **目的地是 agent 的工作目录。** Copy 安装是 agent 对 skill 的 copy；agent 可能有边文件（比如 per-agent log、per-agent cache、per-agent config）源树没有。"Merge"分支覆盖源文件（所以安装是新鲜的）但保留目的地自己的文件（所以 agent 的状态存活）。
- **Skip 列表跑两遍来清理目的地 artifacts。** 如果之前安装有不同的 skip 列表留下了 artifacts，post-copy pass 取消链接它们。Operator 自己的文件不在 skip 列表里，所以它们在清理中存活。
- **权衡是更复杂的 copy。** 纯"删目的地，copy 源"更简单，但会丢 agent 的边文件。Merge 是对长跑安装（被刷新）的正确形状。

---

## 设计哲学（为什么三 action、两模式是这个形状）

读完这一节，剩下的 playbook 就显而易见了。

1. **源树是权威。** Skill 就是源树；安装是到 target 的投影。Operator 编辑源树；安装是桥梁。`SKILL.md` frontmatter（`name`、`version`、`description`、`tools`）是契约；安装器通过 copy 正确文件强制契约。
2. **Symlink vs copy 是发现机制决定，不是个人偏好。** Claude Code 和 OpenClaw 跟 symlink；Codex 不（在所有情况下）。安装器选对每个 target 正确的形状，让 `--copy` override。
3. **Skip 列表是隐私/卫生边界。** Skill 是 agent 需要的；audit trail 是 operator 需要的。混它们是 footgun。Skip 列表是显式契约。
4. **`--scrub-raw-responses` 是 opt-in 不是 opt-out，因为默认是"保留源有的一切"。** 警告是 operator 的信号；flag 是显式破坏性步骤。
5. **安装器的安全检查在意图层，不在能力层。** "Operator 传 `--copy` 了吗？"是问题；"目的地是 copy 安装吗？"更难回答。字面 `sys.argv` 检查是最简单的意图测试。
6. **`status` 是只读内省层。** 五个状态（`LINKED` / `INSTALLED` / `STALE` / `MISSING` / `BROKEN`）是 operator 的"一切都接好了吗？"检查。Status 是 fresh-checkout 脚本调来验证安装的。
7. **生命周期是 `install → upgrade → replace → uninstall`。** 每个转换是一个 action；安装器的幂等性（重跑 `install` 是 no-op 当没变化时）意味着 operator 可以自由重跑。Replace 转换（`install --copy` 盖在现有安装上）是 upgrade 路径。
8. **Console script 是 agent-friendly 入口。** PATH 上有 `fund-data` 的 CI runner 可以调 `fund-install-skill install --target codex` 不指定脚本路径。`fund-` 前缀匹配其他 console script，是项目在 PATH 上的 namespace。

---

## 反面教材（不要这么说）

这些是 PR review 和 support 线程里见过的常见错误回答。避开它们。

- **"安装是一个命令。"** 是三个 action（`install` / `uninstall` / `status`）加多个 flag。"一个命令"框架藏了生命周期。
- **"Symlink 是默认。"** 是 `claude` / `openclaw` / `agents` 的默认；`codex` 的默认是 copy。"默认"是 per-target 不是全局。
- **"`--include-data` 是安全的。"** 带 `--scrub-raw-responses` 时安全；不带时安装会从 `raw_responses` 泄漏调用方 IP。Flag 是不带 scrub 的半安全操作。
- **"Uninstall 总是能工作。"** 在真目录上没有 `--copy` 或 `--force` 时拒绝。拒绝是安全检查；变通办法是显式 flag。
- **"安装器验证 skill。"** 不验证。唯一检查是 `SKILL.md` 存在。Skill 内容（frontmatter、body、scripts）是源树的责任。
- **"在每台机器上装。"** 每台机器有自己的 agent 集；安装器是 per-machine。装了 Claude 的 CI runner 自动拿到 `claude` 安装；没装 OpenClaw 的开发者 laptop 拿到一个 `SKIP`。
- **"Status 永远是当前的。"** Status 反映调用时的文件系统状态。一小时前调用时是 `LINKED` 的 symlink 如果 repo 移动了可能就是 `STALE`。Status 是 point-in-time 检查，不是连续监控。

---

## 怎么加新 install target（贡献者配方）

当新 agent 平台加进安装矩阵：

1. **确认发现路径。** 问平台维护者：agent 在哪里找 skill？答案是四个现有 pattern 之一（`~/.claude/`、`~/.codex/`、`~/.openclaw/`、`~/.agents/`）或新的一个。在 `SKILLS.md` §"How each platform discovers the skill" 文档化发现机制。
2. **加 target path 到 `_target_paths()`。** 在字典（`fund-data/scripts/install_skill.py:41-48`）里插入新条目。用平台的标准 namespace（`home / ".<platform>" / "skills" / SKILL_NAME`）。
3. **决定安装模式。** Symlink 还是 copy？选择由平台的发现机制驱动：如果平台跟 symlink，symlink 是对的默认；如果不是，copy。在 `SKILLS.md` §新 target 的 "Platform" 里文档化选择。
4. **在 `SKILLS.md` 和 `SKILL.md` frontmatter 文档化平台。** `SKILL.md` 的 `tools:` 列表可能需要增长（需要 `web_search` 的平台）。`SKILLS.md` 应该有一节给新平台的 install 命令、发现机制和任何怪癖。
5. **加单元测试。** 测试应该对新 target 在 temp 目录上跑 `_install_one` 并验证状态。
6. **Bump SKILL.md `version:` 字段。** Manifest 版本是 agent 表明 skill 变了；为新 target 的文档 bump 它。

---

## 怎么保持这个剧本准确

剧本是团队 *settled* 的解释，不是 live 代码。代码变了，在同一个 PR 里更新剧本。检查项：

- 新 install target 加进来 → 更新 §3.1 和贡献者配方。
- Skip 列表变了 → 更新 §3.5 和 Q5。
- Status 语义变了（新状态加了）→ 更新 §3.6。
- 隐私 flag 重命名了或警告文字变了 → 更新 §3.4 和 Q4。
- 新 CLI flag 加进来（比如 `--link`）→ 更新 §4 和 §7。
- Per-target 默认（symlink vs copy）变了 → 更新 Q2 和 Q11。

如果 PR 改了上面任何一项但没更新剧本，request changes 时指这一节。

---

## 相关文档

- [`fund-install-pipeline.md`](./fund-install-pipeline.md) —— 图表 + 代码锚点 + env var 表。
- [`fund-mcp-server-pipeline.md`](./fund-mcp-server-pipeline.md) —— 安装之后的运行时表面。
- [`fund-cloud-bundle-pipeline.md`](./fund-cloud-bundle-pipeline.md) —— 安装接的数据平面。
- [`../../fund-data/SKILL.md`](../../fund-data/SKILL.md) —— agent-facing skill manifest（被安装的文件）。
- [`../../fund-data/SKILLS.md`](../../fund-data/SKILLS.md) —— Codex / Claude / OpenClaw 的 per-platform install 布局；规范的 install 命令和 refresh 流。
- [`../../fund-data/ARCHITECTURE.md`](../../fund-data/ARCHITECTURE.md) —— contributor-facing 架构参考。
- [`../../SECURITY.md`](../../SECURITY.md) —— 隐私边界文档（raw_responses IP 泄漏、`--scrub-raw-responses` opt-in）。
- [`../../pyproject.toml`](../../pyproject.toml) —— `console_scripts` 条目，把 `fund-install-skill` 装到 PATH。
- [`../../README.md` §Known gaps](../../README.md#known-gaps-tracked-for-030) —— v0.3.0 backlog。
