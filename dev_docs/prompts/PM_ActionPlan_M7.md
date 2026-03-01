# M7 PM Action Plan: Devcoord Teammate Cutover

> 状态: approved
> 日期: 2026-03-01
> 依据: `dev_docs/plans/m7_devcoord-refactor_2026-02-28_v2.md`
> 协议 SSOT: `AGENTTEAMS.md`
> 控制面实现: `scripts/devcoord/coord.py`

## 1. 当前阶段

- Phase 1 skeleton 已完成并关闭 `G-M7-P1`
- Phase 2 live PM-first cutover 已完成并关闭 `G-M7-P2`
- 当前目标是 Phase 4 teammate cutover 和 Phase 5 projection-only 收口

## 2. 必备技能文件

- PM: `.claude/skills/devcoord-pm/SKILL.md`
- Backend: `.claude/skills/devcoord-backend/SKILL.md`
- Tester: `.claude/skills/devcoord-tester/SKILL.md`

## 3. PM 操作基线

1. 所有协作控制写操作都走 `uv run python scripts/devcoord/coord.py apply <action> --payload-stdin`
2. 不直接编辑 `dev_docs/logs/*`、`dev_docs/progress/project_progress.md`
3. 不直接自由拼装 `bd` 命令写控制面
4. append-first 的落点是 beads 事件，不是手写 `heartbeat_events.jsonl`
5. `gate-close` 前固定执行：`render -> audit -> gate-close -> render`

## 4. Spawn 注入要求

PM spawn backend/tester 时，prompt 至少显式注入：

- `Gate 状态机`
- `指令 ACK 生效机制`
- `恢复/重启握手`
- `worktree/分支同步协议`
- `验收产物可见性闭环（commit + push）`
- 对应角色的 devcoord skill 路径

若缺少上述任一项，本次 spawn 不视为有效开工。

## 5. 角色动作边界

### Backend

- 允许：`ack`、`heartbeat`、`phase-complete`、`recovery-check`
- 禁止：`open-gate`、`state-sync-ok`、`gate-close`

### Tester

- 允许：`ack`、`heartbeat`、`recovery-check`、`gate-review`
- 禁止：`open-gate`、`state-sync-ok`、`gate-close`

### PM

- 负责：`open-gate`、`state-sync-ok`、`ping`、`unconfirmed-instruction`、`stale-detected`、`log-pending`、`render`、`audit`、`gate-close`

## 6. Phase 4 验收门槛

- backend / tester 的协作状态写入不再依赖 PM 手工转录
- `ACK`、`HEARTBEAT`、`PHASE_COMPLETE`、`RECOVERY_CHECK` 至少完成一次真实会话演练
- tester 的 review 提交通过 `gate-review` 写入控制面
- 不出现直接编辑 `dev_docs/logs/*` 的执行路径

## 7. Phase 5 验收门槛

- `dev_docs/logs/README.md` 明确声明 projection-only
- 协作文档统一写成“通过 devcoord skill + `scripts/devcoord/coord.py` 调控制面”
- `render` 能稳定重建 `heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md`、`project_progress.md`
