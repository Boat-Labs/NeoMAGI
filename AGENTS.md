# AGENTS.md

> 本文件与 `CLAUDE.md` 保持一致，供非 Claude Code 的 coding 系统使用；Claude Code 执行以 `CLAUDE.md`（行为约束）与 `AGENTTEAMS.md`（协作控制）为准。

## 定义与范围
- `AGENTS.md` 是仓库级、多代理协作的治理契约；不是产品运行时规范。
- `AGENTS.md` 面向非 Claude Code 系统；`CLAUDE.md` 面向 Claude Code。两者必须保持一致。
- Agent Teams 协作控制协议以 `AGENTTEAMS.md` 为 SSOT，本文件保留一致性镜像与引用。
- 本文件只定义协作流程、职责边界、交付质量、风险控制与验收门槛。
- 运行时 prompt/memory/heartbeat 等实现细节不在此维护。

## Mission
NeoMAGI 是一个开源 personal agent：有持久记忆、代表用户信息利益、可从商业 API 平滑迁移到本地模型。

## Core Principles
- 考虑充分，实现极简。
- 先做最小可用闭环，不做过度工程。
- 默认给出可执行结果（代码/命令/文件改动），少空谈。
- 以“对抗熵增”为核心设计目标：在满足需求的前提下，优先选择更少概念、更少依赖、更短路径的实现。
- 所有实现在提交前增加一轮“极简审阅”：删除非必要抽象、重复逻辑和可合并配置，以换取长期成长性。

## Safety Boundaries
- Never exfiltrate private data.
- 禁止未经确认执行破坏性操作（删库、批量删除、危险系统命令、历史重写）。
- 对高风险操作先说明影响，再请求确认。

## 协作职责
- PM 负责：任务拆解、worktree 预创建、角色分派、合并顺序、阶段验收。
- Teammate 负责：仅在分配的 worktree 内开发、自测通过后再提交、阻塞及时上报。
- 禁止多人共享同一 working directory。
- 任何阻塞反馈必须包含：现象、影响、已尝试动作、下一步建议。

## 协作控制与活性治理（Agent Teams）
- 说明：以下为协作层治理规则，不是运行时 agent heartbeat 功能实现。
- SSOT：Agent Teams 协作控制协议以 `AGENTTEAMS.md` 为准；本节与 `CLAUDE.md` 保持一致。
- 目标：解决“worktree 并行导致分支状态不一致、指令未确认即生效、Gate 越权推进、产物不可追溯”。
- 规则优先级：`Gate 状态机` > `PM 非结构化指令` > `teammate 自主判断`。

### Gate 状态机（强制）

- 只有 PM 可以发布 Gate 状态；teammate 不得自行切换 phase。
- PM 放行必须带 commit pin：
  - `GATE_OPEN gate=<gate-id> phase=<phase> target_commit=<sha> allowed_role=<role>`
- Gate 关闭必须记录结论：
  - `GATE_CLOSE gate=<gate-id> result=<PASS|PASS_WITH_RISK|FAIL> report=<path> report_commit=<sha>`
- 无 `GATE_OPEN` 且无 `target_commit` 时，Backend 不得进入下一 Phase，Tester 不得启动验收。
- Phase 边界同步（强制）：teammate 完成当前 phase 后必须发送 `PHASE_COMPLETE role=<role> phase=<N> commit=<sha>`，并等待 PM 的 `GATE_OPEN` 放行；未放行前不得开始下一 phase 的任何编码或测试。

### 指令确认（ACK）与生效条件

- 以下指令必须 ACK 才生效：`STOP`、`WAIT`、`RESUME`、`GATE_OPEN`、`PING`。
- teammate 收到后必须回：
  - `[ACK] role=<role> cmd=<cmd> gate=<gate-id|na> commit=<sha|na>`
- PM 仅在收到 ACK 后将指令状态记为 `effective`；未 ACK 状态统一记为 `pending`。
- PM 发出需 ACK 指令后，若 10 分钟内未收到 ACK 且该角色无新状态事件，PM 才可发第二次 `PING` 并记录 `unconfirmed_instruction` 事件。

### 恢复/重启握手（强制）

- 任一 teammate 发生 context 压缩、进程重启或长时间中断后，必须先发：
  - `RECOVERY_CHECK role=<role> last_seen_gate=<gate-id|unknown>`
- PM 必须回复当前状态快照（至少包含：`current_phase`、`latest_gate`、`allowed_role`、`target_commit`）。
- teammate 仅在收到：
  - `STATE_SYNC_OK role=<role> gate=<gate-id> target_commit=<sha>`
  后才可继续执行；否则一律保持 `WAIT`。

### worktree/分支同步协议（强制）

- 每个角色只允许在自己的 worktree 工作，禁止跨目录读写执行态产物。
- Backend phase 完成后必须先 `commit + push`，再向 PM 回传 `phase` 与 `commit sha`。
- Tester 启动 Gate 验收前必须执行并回传结果：
  - `git fetch --all --prune`
  - `git merge --ff-only origin/<backend-branch>`（或明确约定 rebase）
  - `git rev-parse HEAD`
- Tester 禁止基于“未 push 的本地中间态”输出 Gate 结论。

### Spawn 规则注入（强制）

- PM spawn teammate 时，prompt 必须显式包含以下协议摘要：`Gate 状态机`、`指令 ACK 生效机制`、`恢复/重启握手`、`worktree/分支同步协议`、`验收产物可见性闭环（commit + push）`。
- 如使用 Claude Code，PM / backend / tester 分别加载 `.claude/skills/devcoord-pm/SKILL.md`、`.claude/skills/devcoord-backend/SKILL.md`、`.claude/skills/devcoord-tester/SKILL.md`。
- 对 Claude Code 的 devcoord 关键流程，spawn prompt 必须显式写出对应 skill 名称，并优先使用 slash 形式（如 `/devcoord-backend`、`/devcoord-tester`）降低同名/近义语义污染。
- 对 Claude Code 的 devcoord 关键流程，PM 应至少用一次 Claude Code CLI debug 日志验证技能实际命中；`processPromptSlashCommand` 或 `SkillTool returning` 命中预期 skill 才算有效注入证据。
- 未完成上述规则注入的 spawn，不得视为有效开工。

### 心跳 SLA 与长任务可打断点

- 每个 teammate 至少每 15 分钟同步一次状态。
- 长任务（测试、迁移、全量回归）开始即发状态，完成后 2 分钟内补发结果。
- 长命令执行超过 10 分钟时，必须发进度心跳并在可中断点检查 inbox。
- 推荐统一格式：`[HEARTBEAT] role=<role> phase=<phase> status=<working|blocked|done> since=<ISO8601> eta=<min> next=<one-line>`
- Tester 长测建议标记：`TEST_RUN_STARTED` / `TEST_RUN_PROGRESS` / `TEST_RUN_FINISHED`。

### 事件日志（强制，append-only）

- PM 必须通过 `uv run python scripts/devcoord/coord.py ...` 记录协作控制事件；repo 根 `.beads` 为 SSOT，`dev_docs/logs/{milestone}_{YYYY-MM-DD}/heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md` 为 `render` 投影。
- PM 收到任何状态变更消息（含 ACK、Gate、PING、报告同步）后，必须在同一 PM 回合先完成对应 control plane 写入，再发送下一条控制指令（append-first）。
- 若同回合无法落盘，PM 必须先记录 `LOG_PENDING`（通过 `log-pending`），并在下一 PM 回合第一步补录。
- 最大允许滞后为 1 个 PM 回合，不得跨 2 个 PM 回合。
- `heartbeat_events.jsonl` 投影每条至少包含：`ts`、`role`、`phase`、`status`、`task`、`eta_min`。
- 建议附加字段：`event`、`gate`、`target_commit`、`ack_of`、`branch`、`worktree`、`source_msg_id`、`event_seq`。
- PM 在 `GATE_CLOSE` 前必须先执行 `render` 与 `audit`，并满足 `audit.reconciled=true`；不一致时禁止关 Gate。

### PM 超时判定与重启前置

- 超过 20 分钟无状态，先发送 `PING` 并等待 5 分钟。
- 最近状态若为长任务执行中，再追加 20 分钟观察窗口。
- 仍无响应，标记 `suspected_stale`，先输出风险说明，再决定是否重启。
- 重启前置条件（必须全部满足）：
  - 连续两次 `PING` 无响应。
  - 无新提交、无新增日志、无状态更新事件。
  - 已形成“重启影响评估 + 回滚方案”并记录。

### 验收产物可见性闭环（强制）

- Tester 报告必须 `commit + push`，并回传 `report path + report commit sha`。
- PM 关闭 Gate 前必须验证报告在主仓库可见（merge/sync 完成），否则 Gate 不可关闭。
- 审阅结论与证据以主仓库可见文件为准，不以单一 worktree 未提交文件为准。

## Git 与分支策略
- Commit message 格式：`<type>(<scope>): <description>`
  - type: feat, fix, refactor, docs, test, chore
  - scope: gateway, agent, memory, session, tools, channel, config
  - 例: `feat(memory): implement BM25 search with pg_search`
- 一个 commit 做一件事，不要混合不相关的变更。
- Agent Teams 必须使用 git worktree 隔离并行开发。
- Agent Teams worktree 规则：PM 负责维护，teammate 必须遵守。
- 每个 teammate 在独立 worktree 中工作，禁止多人共享同一 working directory。
- PM 负责在 spawn 前创建 worktree，在阶段完成后合并和清理。
- 分支命名：`feat/<role>-<milestone>-<owner-or-task>`（如 `feat/backend-m1.1-agent-loop`）。
- 开始改动前固定执行：`pwd && git branch --show-current && git status --short`。
- 清理或切换 worktree 后，先确认变更已迁移到目标分支，再继续开发或测试。

## 实施基线（治理层）
- 数据库统一使用 PostgreSQL 16（`pgvector` + ParadeDB `pg_search`），不使用 SQLite。
- 数据库连接信息读取本地 `.env`，共享模板使用 `.env_template`（不提交真实凭据）。
- Python 包管理器使用 `uv`。
- Frontend 包管理器使用 `pnpm`。
- 常规开发/测试命令入口统一使用 `just`；devcoord 控制面协议写操作直接使用 `scripts/devcoord/coord.py`，不再额外包一层 `just`。

## 决策与计划治理

### M0 Governance (Decision Log)
- 关键技术选型、架构边界变更、优先级调整，必须写入 `decisions/`。
- 一条决策一个文件：`decisions/NNNN-short-title.md`。
- 每条决策至少写清楚三件事：选了什么、为什么、放弃了什么。
- 写入或更新决策时，同步维护 `decisions/INDEX.md`。
- 没有实质性取舍时，不新增决策文件，避免噪音。

### Plan 持久化
- `dev_docs/plans/` 不分目录：允许存在一个讨论中的草稿文件和已审批正稿文件。
- 草稿命名：`{milestone}_{目标简述}_{YYYY-MM-DD}_draft.md`。
- 讨论阶段必须持续更新同一个 `_draft` 文件；禁止因讨论轮次新开 `_v2`、`_v3`。
- 用户批准后，使用正确正稿文件名生成计划：`{milestone}_{目标简述}_{YYYY-MM-DD}.md`（或满足条件时 `_v2`、`_v3`），并删除对应 `_draft` 文件。
- `_v2`、`_v3` 仅用于“同一 scope 下，上一版已审批且已执行”后的再次获批修订；不得用于未执行的讨论迭代。
- 这是项目的持久记忆，后续 PM 重启时首先读取最新 plan。
- 产出计划前先对齐 `AGENTTEAMS.md`、`AGENTS.md`、`CLAUDE.md`、`decisions/`、`design_docs/` 约束。

## 质量与验收
- 开发过程先跑受影响测试；里程碑合并前必须跑全量回归。
- 常规开发命令入口：后端 `just test`，前端 `just test-frontend`，静态检查 `just lint`（必要时 `just format`）。
- devcoord 控制面写操作统一使用 `uv run python scripts/devcoord/coord.py ...`，优先走结构化 payload（`--payload-file` / `--payload-stdin`）。
- 修复/重构任务提交时需附验证结果摘要（命令与通过概况）。

## Agent 工作日志（临时降级策略）
- 状态：M1.5 阶段临时降级为“非阻塞”。
- 作用域：本降级仅适用于 role 经验日志，不适用于协作控制日志。
- 原因：Agent Teams 当前存在指令未稳定透传到 agent 层的问题。
- 执行：保留 `dev_docs/logs/{milestone}_{YYYY-MM-DD}/` 目录；协作控制三件套由 `scripts/devcoord/coord.py render` 生成，各 role 日志改为尽力提供。
- 协作控制日志（`heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md`）仍为强制门槛，必须按“协作控制与活性治理”章节执行。
- 验收：当前阶段不因缺少单个 role 经验日志而阻塞；缺少协作控制日志则阻塞。
- 恢复条件：并行流程连续 3 次无透传丢失后，恢复为强制门槛。
- 如提交 role 日志，仍建议包含：技能/工具名称、调用次数、典型场景、效果评估。
- 如提交 role 日志，需保持脱敏，禁止记录密钥、token、隐私原文。

## Style
- 回复简洁、技术导向、可复制执行。
- 明确假设和限制；不确定时先查证再回答。
- 优先中文，保留必要英文技术术语。

## 规范引用（SSOT）
- 运行时 prompt 文件加载顺序、按需加载与优先级：`design_docs/system_prompt.md`
- Memory 架构与策略：`design_docs/memory_architecture.md`
- 里程碑顺序与产品实现路线：`design_docs/roadmap_milestones_v3.md`
- 详细开发手册与技术栈摘要：`CLAUDE.md`

<!-- BEGIN BEADS INTEGRATION -->
## Issue Tracking with bd (beads)

**IMPORTANT**: This project uses **bd (beads)** for ALL issue tracking. Do NOT use markdown TODOs, task lists, or other tracking methods.

### Why bd?

- Dependency-aware: Track blockers and relationships between issues
- Git-friendly: Dolt-powered version control with native sync
- Agent-optimized: JSON output, ready work detection, discovered-from links
- Prevents duplicate tracking systems and confusion

### Quick Start

**Check for ready work:**

```bash
bd ready --json
```

**Create new issues:**

```bash
bd create "Issue title" --description="Detailed context" -t bug|feature|task -p 0-4 --json
bd create "Issue title" --description="What this issue is about" -p 1 --deps discovered-from:bd-123 --json
```

**Claim and update:**

```bash
bd update <id> --claim --json
bd update bd-42 --priority 1 --json
```

**Complete work:**

```bash
bd close bd-42 --reason "Completed" --json
```

### Issue Types

- `bug` - Something broken
- `feature` - New functionality
- `task` - Work item (tests, docs, refactoring)
- `epic` - Large feature with subtasks
- `chore` - Maintenance (dependencies, tooling)

### Priorities

- `0` - Critical (security, data loss, broken builds)
- `1` - High (major features, important bugs)
- `2` - Medium (default, nice-to-have)
- `3` - Low (polish, optimization)
- `4` - Backlog (future ideas)

### Workflow for AI Agents

1. **Check ready work**: `bd ready` shows unblocked issues
2. **Claim your task atomically**: `bd update <id> --claim`
3. **Work on it**: Implement, test, document
4. **Discover new work?** Create linked issue:
   - `bd create "Found bug" --description="Details about what was found" -p 1 --deps discovered-from:<parent-id>`
5. **Complete**: `bd close <id> --reason "Done"`

### Auto-Sync

bd automatically syncs via Dolt:

- Each write auto-commits to Dolt history
- Use `bd dolt push`/`bd dolt pull` for remote sync
- No manual export/import needed!

### Important Rules

- ✅ Use bd for ALL task tracking
- ✅ Always use `--json` flag for programmatic use
- ✅ Link discovered work with `discovered-from` dependencies
- ✅ Check `bd ready` before asking "what should I work on?"
- ❌ Do NOT create markdown TODO lists
- ❌ Do NOT use external issue trackers
- ❌ Do NOT duplicate tracking systems

For more details, see README.md and docs/QUICKSTART.md.

<!-- END BEADS INTEGRATION -->

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
