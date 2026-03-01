# M7 DevCoord 重构计划 v2

> 状态：approved
> 日期：2026-03-01
> 性质：内部治理 / 执行层重构，不属于产品 roadmap 新功能里程碑
> 依据：`AGENTTEAMS.md`、`AGENTS.md`、ADR 0042/0043、`dev_docs/devcoord/beads_control_plane.md`
> 说明：本版仅修订 devcoord 运行时入口，替换 `just -> scripts/devcoord` 为 `scripts/devcoord` 直接入口；其余 M7 范围和边界保持不变。

## 1. 目标

将当前依赖 prompt + 手工文档维护的多 agent 协作控制流程，重构为：

- `beads + Dolt` 作为开发协作控制面 SSOT；
- `scripts/devcoord` 作为唯一运行时写入口；
- `dev_docs/logs/` 与 `dev_docs/progress/` 降级为兼容投影层；
- M7 只实现“最小协调内核”，不试图在本阶段做成覆盖全部执行路径的 workflow engine；
- 在不改变战略层文档与产品运行时数据库路线的前提下，提升协作状态机的可追溯性、可验证性与 deterministic 程度。

## 2. 范围

### 2.1 In Scope
- PM / backend / tester 的协作控制状态：
  - Gate open / close
  - ACK 生效
  - heartbeat / watchdog
  - recovery / state sync
  - phase complete / gate review / stale detection
- 统一命令面：
  - `scripts/devcoord/*`
  - 对应 skill
  - 结构化 payload（JSON file / stdin）调用约定
- 兼容投影：
  - `dev_docs/logs/{milestone}_{date}/heartbeat_events.jsonl`
  - `dev_docs/logs/{milestone}_{date}/gate_state.md`
  - `dev_docs/logs/{milestone}_{date}/watchdog_status.md`
  - `dev_docs/progress/project_progress.md`

### 2.2 Out of Scope
- 产品运行时 PostgreSQL 数据面
- `decisions/`、`design_docs/`、`dev_docs/reviews/`、`dev_docs/reports/` 的主存储语义
- beads 远程 sync / federation / `beads-sync` 分支工作流
- 自定义 beads schema 或 fork beads
- 让 agent 或人类直接自由写 `bd` 来表达协议状态

## 3. 目标架构

### 3.1 分层
- LLM：判断动作、补全参数、产出分析与结论。
- `scripts/devcoord`：参数校验、状态机约束、beads 适配、投影生成、对账与幂等；是协议语义唯一实现层。
- Beads/Dolt：执行层 persistence / query / history SSOT，不承担状态机语义。
- `dev_docs`：兼容投影层。

补充说明：
- skill 仍然存在，但只作为行为约束与调用规范，不作为运行时系统分层。
- `just` 仍用于仓库常规开发任务，但不再作为 devcoord 控制面的中间层。

### 3.2 调用约束
- agent 的正式写路径固定为：`scripts/devcoord/coord.py`
- 优先使用结构化 payload（`--payload-file` 或 stdin JSON），避免长参数串和 shell quoting 漂移。
- beads 直连仅允许用于 inspection/query，不允许替代 wrapper 进行协议写入。
- 共享控制面默认目录固定为仓库根 `.beads`；`.coord/beads` 不再作为默认 fallback。

## 4. 执行阶段

### Phase 1：控制面骨架
- 产物：
  - `scripts/devcoord/coord.py`
  - 结构化 payload 入口
- 要求：
  - 保持“state machine for governance, not for single-path execution”的设计边界
  - 固定与 beads 的交互方式为 `bd ... --json` CLI shell-out
  - 明确不直接写 Dolt SQL，不经由 MCP server
  - 先覆盖最小事件集：`init`、`open-gate`、`ack`、`heartbeat`、`phase-complete`、`render`
  - 所有写操作统一从 wrapper 进入

### Phase 2：Shadow Mode
- 产物：
  - M6 真实日志回放脚本
  - beads -> `dev_docs` 投影输出
- 要求：
  - 基于既有 M6 日志回放生成控制面状态
  - 核对投影结果与现有日志文件口径是否一致
  - 验证幂等：重复回放不产生错误漂移

### Phase 3：PM First
- 产物：
  - `.claude/skills/devcoord-pm/SKILL.md`
  - `dev_docs/prompts/PM_ActionPlan_M7.md`
- 要求：
  - PM 停止手写控制日志
  - PM 全部改为调用 `scripts/devcoord`
  - teammate 暂时维持旧消息格式

### Phase 4：Teammate Cutover
- 产物：
  - `.claude/skills/devcoord-backend/SKILL.md`
  - `.claude/skills/devcoord-tester/SKILL.md`
  - `scripts/devcoord/check_skill_activation.sh`
  - `dev_docs/devcoord/claude_code_skill_triggering.md`
- 要求：
  - `ACK`、`HEARTBEAT`、`PHASE_COMPLETE`、`RECOVERY_CHECK` 统一走 wrapper
  - 禁止直接编辑 `dev_docs/logs/*`
  - 用 Claude Code CLI debug 日志验证 skill 实际命中，而不是只看自然语言回答

### Phase 5：Projection-Only 收口
- 产物：
  - 更新后的 `dev_docs/logs/README.md`
  - 更新后的协作文档说明
- 要求：
  - `dev_docs/logs/*` 明确为 projection
  - 人工维护入口彻底收敛到 `scripts/devcoord`

## 5. 验收标准

- 任一协作状态变更都能在 beads 中查询到结构化记录。
- `dev_docs/logs/*` 可由 `scripts/devcoord` 完整重建，不依赖人工补写。
- skill 不直接修改日志文件，不直接自由拼装 `bd` 命令。
- 共享 `BEADS_DIR` 下，多 worktree 可以基于同一控制面协作，不出现各自独立状态。
- 使用 M6 回放时，能生成与既有 `heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md` 语义一致的投影。
- M7 实施后，控制面仍允许 handoff / rescue 等开放协作路径，不因单一 agent 卡住而把任务锁死在唯一路径中。

## 6. 测试策略

### 6.1 控制面单元测试
- 状态对象与命令参数校验测试
- 协议不变量测试：未 ACK 不生效、append-first、缺事件禁止 gate close
- 结构化 payload 入口测试
- `event_seq` 分配测试：共享控制面下全局单调递增

### 6.2 适配层测试
- `bd ... --json` CLI 成功路径
- CLI 失败、超时、非预期 JSON 的错误路径
- beads 不可达时 fail-closed 行为

### 6.3 Projector 测试
- golden tests：固定输入事件流，验证 `heartbeat_events.jsonl` / `gate_state.md` / `watchdog_status.md` 输出
- schema 转换测试：确保 projection 字段与当前日志口径一致

## 7. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| `scripts/devcoord` 仍暴露过多 flag | agent 继续走字符串拼装 | 增加结构化 payload 入口，推荐 file/stdin JSON |
| agent 绕过 wrapper 直接写 `bd` | 状态分叉 | skill / prompt 明确禁止；code review 以 wrapper 调用为准 |
| 角色权限当前仅靠 skill / prompt 约束 | 未加载 skill 时可能越权调用高权限动作 | 当前阶段接受；若后续需要代码层强制权限，应引入 actor/subject 分离，而不是复用现有 `role` 字段做简单校验 |
| 双入口并存（`just` + script） | 文档与行为漂移 | 删除 devcoord recipes，文档统一改为 `scripts/devcoord` |
| Dolt 运维成本超出当前规模承受范围 | 控制面收益被运维噪声抵消 | 保持 `scripts/devcoord` 接口稳定，仅替换存储适配层 |

## 8. 下一步建议

1. 用新建的 teammate skills 跑一次真实 backend/tester 会话，验证 `ACK / HEARTBEAT / PHASE_COMPLETE / RECOVERY_CHECK / gate-review` 不再依赖 PM 手工转录。
2. 完成 projection-only 收口，后续协作文档不再把 `dev_docs/logs/*` 当作人工主写入口。
