# Agent Teams 协作控制与活性治理

> 本文件是 Agent Teams 多代理协作控制协议的 SSOT。
> `CLAUDE.md`（Claude Code）与 `AGENTS.md`（其他系统）为一致性镜像入口，必须与本文件保持一致。
> 以下为协作层治理规则，不是运行时 agent heartbeat 功能实现。

- 目标：解决"worktree 并行导致分支状态不一致、指令未确认即生效、Gate 越权推进、产物不可追溯"。
- 规则优先级：`Gate 状态机` > `PM 非结构化指令` > `teammate 自主判断`。

## Gate 状态机（强制）

- 只有 PM 可以发布 Gate 状态；teammate 不得自行切换 phase。
- PM 放行必须带 commit pin：
  - `GATE_OPEN gate=<gate-id> phase=<phase> target_commit=<sha> allowed_role=<role>`
- Gate 关闭必须记录结论：
  - `GATE_CLOSE gate=<gate-id> result=<PASS|PASS_WITH_RISK|FAIL> report=<path> report_commit=<sha>`
- 无 `GATE_OPEN` 且无 `target_commit` 时，Backend 不得进入下一 Phase，Tester 不得启动验收。
- Phase 边界同步（强制）：teammate 完成当前 phase 后必须发送 `PHASE_COMPLETE role=<role> phase=<N> commit=<sha>`，并等待 PM 的 `GATE_OPEN` 放行；未放行前不得开始下一 phase 的任何编码或测试。

## 指令确认（ACK）与生效条件

- 以下指令必须 ACK 才生效：`STOP`、`WAIT`、`RESUME`、`GATE_OPEN`、`PING`。
- teammate 收到后必须回：
  - `[ACK] role=<role> cmd=<cmd> gate=<gate-id|na> commit=<sha|na>`
- PM 仅在收到 ACK 后将指令状态记为 `effective`；未 ACK 状态统一记为 `pending`。
- PM 发出需 ACK 指令后，若 10 分钟内未收到 ACK 且该角色无新状态事件，PM 才可发第二次 `PING` 并记录 `unconfirmed_instruction` 事件。

## 恢复/重启握手（强制）

- 任一 teammate 发生 context 压缩、进程重启或长时间中断后，必须先发：
  - `RECOVERY_CHECK role=<role> last_seen_gate=<gate-id|unknown>`
- PM 必须回复当前状态快照（至少包含：`current_phase`、`latest_gate`、`allowed_role`、`target_commit`）。
- teammate 仅在收到：
  - `STATE_SYNC_OK role=<role> gate=<gate-id> target_commit=<sha>`
  后才可继续执行；否则一律保持 `WAIT`。

## worktree/分支同步协议（强制）

- 每个角色只允许在自己的 worktree 工作，禁止跨目录读写执行态产物。
- Backend phase 完成后必须先 `commit + push`，再向 PM 回传 `phase` 与 `commit sha`。
- Tester 启动 Gate 验收前必须执行并回传结果：
  - `git fetch --all --prune`
  - `git merge --ff-only origin/<backend-branch>`（或明确约定 rebase）
  - `git rev-parse HEAD`
- Tester 禁止基于"未 push 的本地中间态"输出 Gate 结论。

## Spawn 规则注入（强制）

- PM spawn teammate 时，prompt 必须显式包含以下协议摘要：`Gate 状态机`、`指令 ACK 生效机制`、`恢复/重启握手`、`worktree/分支同步协议`、`验收产物可见性闭环（commit + push）`。
- 如使用 Claude Code，PM / backend / tester 分别加载 `.claude/skills/devcoord-pm/SKILL.md`、`.claude/skills/devcoord-backend/SKILL.md`、`.claude/skills/devcoord-tester/SKILL.md`。
- 对 Claude Code 的 devcoord 关键流程，spawn prompt 必须显式写出对应 skill 名称，并优先使用 slash 形式（如 `/devcoord-backend`、`/devcoord-tester`）降低同名/近义语义污染。
- 对 Claude Code 的 teammate devcoord 写操作，spawn prompt 必须要求先校验 `git rev-parse HEAD == target_commit`；若不一致，只允许回报阻塞，不允许写入控制面。
- 对 Claude Code 的 devcoord 关键流程，PM 应至少用一次 Claude Code CLI debug 日志验证技能实际命中；`processPromptSlashCommand` 或 `SkillTool returning` 命中预期 skill 才算有效注入证据。
- 未完成上述规则注入的 spawn，不得视为有效开工。

## 心跳 SLA 与长任务可打断点

- 每个 teammate 至少每 15 分钟同步一次状态。
- 长任务（测试、迁移、全量回归）开始即发状态，完成后 2 分钟内补发结果。
- 长命令执行超过 10 分钟时，必须发进度心跳并在可中断点检查 inbox。
- 推荐统一格式：`[HEARTBEAT] role=<role> phase=<phase> status=<working|blocked|done> since=<ISO8601> eta=<min> next=<one-line>`
- Tester 长测建议标记：`TEST_RUN_STARTED` / `TEST_RUN_PROGRESS` / `TEST_RUN_FINISHED`。

## 事件日志（强制，append-only）

- PM 必须通过 `uv run python scripts/devcoord/coord.py ...` 记录协作控制事件；repo 根 `.beads` 为 SSOT，`dev_docs/logs/{milestone}_{YYYY-MM-DD}/heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md` 为 `render` 投影。
- PM 收到任何状态变更消息（含 ACK、Gate、PING、报告同步）后，必须在同一 PM 回合先完成对应 control plane 写入，再发送下一条控制指令（append-first）。
- 若同回合无法落盘，PM 必须先记录 `LOG_PENDING`（通过 `log-pending`），并在下一 PM 回合第一步补录。
- 最大允许滞后为 1 个 PM 回合，不得跨 2 个 PM 回合。
- `heartbeat_events.jsonl` 投影每条至少包含：`ts`、`role`、`phase`、`status`、`task`、`eta_min`。
- 建议附加字段：`event`、`gate`、`target_commit`、`ack_of`、`branch`、`worktree`、`source_msg_id`、`event_seq`。
- PM 在 `GATE_CLOSE` 前必须先执行 `render` 与 `audit`，并满足 `audit.reconciled=true`；不一致时禁止关 Gate。

## PM 超时判定与重启前置

- 超过 20 分钟无状态，先发送 `PING` 并等待 5 分钟。
- 最近状态若为长任务执行中，再追加 20 分钟观察窗口。
- 仍无响应，标记 `suspected_stale`，先输出风险说明，再决定是否重启。
- 重启前置条件（必须全部满足）：
  - 连续两次 `PING` 无响应。
  - 无新提交、无新增日志、无状态更新事件。
  - 已形成"重启影响评估 + 回滚方案"并记录。

## 验收产物可见性闭环（强制）

- Tester 报告必须 `commit + push`，并回传 `report path + report commit sha`。
- PM 关闭 Gate 前必须验证报告在主仓库可见（merge/sync 完成），否则 Gate 不可关闭。
- 审阅结论与证据以主仓库可见文件为准，不以单一 worktree 未提交文件为准。

## Agent 工作日志（临时降级策略）

- 状态：M1.5 阶段临时降级为"非阻塞"。
- 作用域：本降级仅适用于 role 经验日志，不适用于协作控制日志。
- 原因：Agent Teams 当前存在指令未稳定透传到 agent 层的问题。
- 执行：保留 `dev_docs/logs/{milestone}_{YYYY-MM-DD}/` 目录；协作控制三件套由 `scripts/devcoord/coord.py render` 生成，各 role 经验日志改为尽力提供。
- 协作控制日志（`heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md`）仍为强制门槛，必须按上述各节执行。
- 验收：当前阶段不因缺少单个 role 经验日志而阻塞；缺少协作控制日志则阻塞。
- 恢复条件：并行流程连续 3 次无透传丢失后，恢复为强制门槛。
- 如提交 role 日志，仍建议包含：技能/工具名称、调用次数、典型场景、效果评估。
- 如提交 role 日志，需保持脱敏，禁止记录密钥、token、隐私原文。
