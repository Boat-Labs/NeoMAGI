# Beads Control Plane

> 状态：approved
> 日期：2026-02-28
> 适用范围：NeoMAGI 开发协作控制面（非产品运行时）

## 1. 目的

本文定义 NeoMAGI 在 M7 中引入的 beads 控制面架构，用于替代当前由 prompt 驱动、PM 手工维护 `dev_docs/logs/*` 的战术执行层协作流程。

本文只覆盖：
- PM / backend / tester 的协作控制状态；
- control plane 的分层、对象模型、命令面、投影规则；
- 与现有 `AGENTTEAMS.md` 协议的衔接方式。

本文不覆盖：
- 产品运行时 PostgreSQL 数据面；
- `decisions/`、`design_docs/`、`dev_docs/reviews/`、`dev_docs/reports/` 的存储替换；
- beads federation / remote sync / `beads-sync` 远程分支工作流。

## 2. 核心原则

- 战略层文档不动，执行层结构化。
- LLM 负责思考，程序负责确定性执行。
- skill 只负责约束 agent 行为与调用规范，不承载复杂状态机，也不作为独立运行时分层。
- 所有协作状态变更必须先进入 beads，再生成 `dev_docs` 投影。
- `dev_docs/logs/*` 与 `project_progress.md` 是 projection，不是控制面本体。
- `scripts/devcoord` 是协议语义唯一实现层；beads 只负责 persistence / query / history。
- M7 实现的是最小协调内核，不是覆盖所有执行路径的 workflow engine。
- 治理状态机只约束权限、审计、恢复与对账边界，不规定任务必须按唯一执行路径完成。
- 执行层默认采用 lease / handoff 协作方式；必要时允许 `open_competition`，但不得削弱治理边界。

## 3. 分层架构

```text
LLM
  -> scripts/devcoord/*
    -> beads / Dolt
    -> dev_docs/logs/* + dev_docs/progress/project_progress.md
```

### 3.1 LLM
- 判断下一步动作。
- 提取参数。
- 产出结论性文本，如风险说明、review 结论、阶段总结。

### 3.2 `scripts/devcoord`
- 参数校验。
- 状态机约束。
- 幂等与顺序控制。
- beads 适配与写入。
- 投影生成。
- 对账和恢复辅助。
- 是 ACK、生效、恢复握手、超时判定、日志对账等协议语义的唯一实现层。
- 是 devcoord 的正式运行时入口。
- 推荐通过结构化 payload（JSON file / stdin）调用，减少长参数串和 shell quoting 风险。

### 3.3 Beads / Dolt
- 存储结构化控制面状态。
- 提供历史查询与并发写支持。
- 作为战术执行层 persistence / query / history SSOT。
- 不直接承担 NeoMAGI 协作协议状态机语义。

### 3.4 Projection
- 生成兼容文件给现有文档体系与人工审阅使用。
- 不允许人工直接作为主写入口。

### 3.5 Skill 位置说明
- skill 继续存在，用于约束 agent 只调用 `scripts/devcoord`，禁止直接写日志文件或自由拼接 `bd`。
- skill 是行为规范，不是控制面的额外运行时 hop。

### 3.6 治理层与执行层
- 治理层回答“现在谁被允许推进、什么条件下可以关闭/恢复/继续”。
- 执行层回答“谁先尝试、何时交接、何时救援、是否允许并行探索”。
- `phase` / `gate` 在本架构中是授权窗口，不是要求所有任务按单一路径流转的 workflow state。
- handoff / rescue 属于正常执行语义，不应被建模为违规例外。

## 4. 存储拓扑

### 4.1 共享控制面目录
- 所有 worktree 共享同一个 `BEADS_DIR`。
- 默认共享控制面目录固定为仓库根 `.beads/`。
- `.coord/beads/` 只视为早期草案阶段的 legacy path，不再作为隐式 fallback。
- 初期只做本机共享，不开启远程 sync。

### 4.2 原因
- 如果每个 worktree 各自初始化 `.beads`，控制面会天然分裂。
- 协作控制状态需要跨 PM、backend、tester 可见，必须共享一份 SSOT。

### 4.3 启动要求
- wrapper 启动时校验 `BEADS_DIR` 已固定。
- wrapper 启动时校验当前 worktree 不是在使用本地私有 `.beads`。
- 若检测到孤立的 `.coord/beads/` 且仓库根 `.beads/` 不存在，直接 fail-closed，要求显式迁移或 override。
- 若发现控制面路径不一致，直接 fail-closed。

## 5. 对象模型

| 控制面概念 | beads 对象 | 说明 |
| --- | --- | --- |
| Milestone | bead (`epic` 或 `task`) | 顶层协作单元，如 `M6`、`M7` |
| Phase | bead | milestone 的子阶段 |
| Gate | bead | 保存 `gate_id`、`phase`、`target_commit`、`allowed_role`、`result` |
| Role / Agent | agent bead | `pm`、`backend`、`tester` 的活跃状态 |
| 指令 | message bead | `GATE_OPEN`、`STOP`、`WAIT`、`RESUME`、`PING` |
| 高频事件 | append-only event bead | `heartbeat`、`ack`、`recovery_check`、`phase_complete`、`stale_detected` |

说明：
- 不使用 beads/wisp 承载需要审计追溯的事件。
- wisp 的临时生命周期与本项目 append-only 审计要求不一致。
- 控制面对象优先表达治理边界与共享状态，不预先把完整执行路径编码进对象生命周期。

## 6. 元数据规范

本控制面不采用“所有对象共享一套 metadata 扁平字段”的方式。

原则：
- 优先使用 beads 的对象关系、层级与类型表达语义。
- metadata 只存无法稳定从关系或对象本身推导出的值。
- 同一语义不重复落在“关系 + metadata”两处。

### 6.1 可推导字段
以下信息优先通过 bead 类型、parent-child、thread / reply、对象归属关系推导，不作为默认 metadata：

- `milestone`
- `phase`
- `gate_id`
- `ack_of`
- `source_msg_id`
- `worktree`

说明：
- `milestone` / `phase` 优先从层级关系推导。
- `gate_id` 优先通过 gate bead 本身或事件挂接关系推导。
- `ack_of` / `source_msg_id` 优先通过 message thread / relation 推导。
- `worktree` 属于调试辅助信息，初期不作为控制面必需字段。

### 6.2 Gate Bead Metadata
仅在 gate bead 上使用以下字段：

- `allowed_role`
- `target_commit`
- `result`
- `report_path`
- `report_commit`

说明：
- `result` 仅在 review / close 后需要。
- `report_path` / `report_commit` 仅在已有验收报告时需要。

### 6.3 Event Bead Metadata
仅在 append-only event bead 上按需使用以下字段：

- `event_seq`
- `eta_min`
- `result`
- `branch`

说明：
- `event_seq` 是事件流对账与稳定排序的核心字段。
- `eta_min` 只对 heartbeat 等进度事件有意义。
- `result` 只对带结论的事件有意义。
- `branch` 仅在需要补充审计来源时使用，不要求所有事件都带。

### 6.4 Agent Bead Metadata
agent bead 初期不定义必需 metadata。

说明：
- 角色活性优先使用 beads 原生字段，如 `agent_state`、`last_activity`。
- 如需记录附加执行来源，优先只加 `branch`，避免扩张字段表面积。

### 6.5 通用约束
- key 命名统一 snake_case。
- 事件时间统一由脚本注入，禁止模型手写。
- `event_seq` 只能由控制面脚本生成。
- 初期采用共享控制面下的全局单调递增序列，不做每 agent 独立 seq。
- 新增 metadata 前，先判断该语义是否已可由 beads 对象关系表达；若可以，则不得重复加字段。

## 7. 实现接口

Phase 1 固定采用以下实现方式：

- `scripts/devcoord` 通过 `bd ... --json` CLI shell-out 与 beads 交互。
- 不直接写 Dolt SQL。
- 不通过 beads MCP server。

选择原因：
- 最短路径，减少与 beads 内部实现的耦合。
- 错误边界清晰，便于在 wrapper 层统一处理失败和重试。
- 若后续判定 beads 不适合当前问题，可替换存储适配层而不影响 `scripts/devcoord` 命令面。

## 8. 命令面

建议最小动作集如下：

- `uv run python scripts/devcoord/coord.py init`
- `uv run python scripts/devcoord/coord.py open-gate`
- `uv run python scripts/devcoord/coord.py ack`
- `uv run python scripts/devcoord/coord.py heartbeat`
- `uv run python scripts/devcoord/coord.py phase-complete`
- `uv run python scripts/devcoord/coord.py recovery-check`
- `uv run python scripts/devcoord/coord.py state-sync-ok`
- `uv run python scripts/devcoord/coord.py stale-detected`
- `uv run python scripts/devcoord/coord.py render`

推荐调用方式：
- `uv run python scripts/devcoord/coord.py apply <action> --payload-file <json>`
- 或 `uv run python scripts/devcoord/coord.py apply <action> --payload-stdin`

命令职责：
- `init`：初始化共享控制面对象与基础 metadata。
- `open-gate`：创建/更新 gate bead，并发出 command message。
- `ack`：记录 ACK 事件，并将待确认指令转为 effective。
- `heartbeat`：更新 agent bead 与 heartbeat event。
- `phase-complete`：写入阶段完成事件，更新 phase/gate 相关状态。
- `recovery-check`：记录 teammate 重启/上下文压缩后的恢复请求，并将角色置为等待同步。
- `state-sync-ok`：记录 PM 的状态同步确认，并将目标角色恢复到可继续执行状态。
- `stale-detected`：记录超时观察后判定的可疑失活，并将角色标记为 `suspected_stale`。
- `render`：从 beads 投影到 `dev_docs`。

补充命令如 `PING`、`unconfirmed-instruction` 在最小闭环跑通后再引入，避免过早扩大命令表面积。
后续若需要引入 `claim`、`handoff` 或开放竞争相关命令，也应作为执行层原语补充，而不是把执行路径反推回治理状态机。

## 9. 协议映射

### 9.1 指令类
- `GATE_OPEN`
- `STOP`
- `WAIT`
- `RESUME`
- `PING`

映射方式：
- 使用 message bead 承载。
- `assignee` 对应目标 role。
- `metadata.requires_ack=true`。
- command 只有收到 `ACK` 后才标记 effective。

### 9.2 状态类
- `PHASE_COMPLETE`
- `RECOVERY_CHECK`
- `STATE_SYNC_OK`
- `STALE_DETECTED`
- `GATE_REVIEW_COMPLETE`

映射方式：
- 使用 append-only event bead 承载。
- 同步更新相关 bead 的聚合状态字段。
- `PHASE_COMPLETE` 表示当前尝试已提交可评审结果，不等同于问题空间已被彻底穷尽。

### 9.3 角色活性
- 使用 agent bead 的 `agent_state` 与 `last_activity`。
- heartbeat 更新 agent bead，并额外落一条 append-only event bead。

## 10. Projection 规则

### 10.1 生成文件
- `dev_docs/logs/{milestone}_{date}/heartbeat_events.jsonl`
- `dev_docs/logs/{milestone}_{date}/gate_state.md`
- `dev_docs/logs/{milestone}_{date}/watchdog_status.md`
- `dev_docs/progress/project_progress.md`

### 10.2 生成原则
- `scripts/devcoord` 每次从 beads 读取完整状态后重建目标文件。
- projection 文件允许被覆盖重写，不允许人工增量维护。
- projection 格式尽量兼容现有文件，降低切换成本。
- `scripts/devcoord` 负责把内部控制面对象转换为现有 `heartbeat_events.jsonl` schema；该转换逻辑属于控制面实现的一部分，需要单独测试。

### 10.3 兼容阶段
- shadow mode 期间，保留旧文件并对比输出。
- cutover 后，旧文件仍保留，但来源改为 `scripts/devcoord` 投影。

## 11. 关键不变量

- 没有通过 wrapper 的状态变更，一律视为无效。
- 没有 ACK 的需确认指令，一律是 pending，不得假设 effective。
- `dev_docs` 文件不是控制面真源。
- 所有 worktree 必须使用同一个共享控制面目录。
- agent 只允许通过 `scripts/devcoord` 进入控制面；skill 负责约束这一点。
- control plane 失败时默认 fail-closed，不静默补写文件假装成功。
- append-only 审计事件不得映射为会删除的临时对象。
- 治理状态机不得把执行路径锁死为唯一路径；系统必须为 handoff / rescue / 更优尝试保留空间。

## 12. 迁移顺序

### Stage 1：Shadow Mode
- 回放既有 M6 日志到 beads。
- 生成兼容投影。
- 比对结果。

### Stage 2：PM First
- PM 改为只调用控制面命令。
- teammate 暂保持旧回报语义。

### Stage 3：Teammate Cutover
- backend / tester 改为通过 skill 调 `scripts/devcoord`。

### Stage 4：Projection-Only
- `dev_docs/logs/*` 正式降级为投影层。

## 13. 与现有治理文档的关系

- `AGENTTEAMS.md`：仍是协作协议 SSOT。
- ADR 0042：定义为什么引入 beads 控制面。
- M7 计划：定义如何分阶段实施。
- 本文：定义控制面架构与实现边界。

本文不 supersede `AGENTTEAMS.md`，只提供其程序化落地方式。
