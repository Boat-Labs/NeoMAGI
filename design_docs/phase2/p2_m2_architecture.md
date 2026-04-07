---
doc_id: 019cc914-bf10-7794-80e3-d2855bda8849
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-07T17:15:06+01:00
---
# P2-M2 Architecture（计划）

> 状态：planned  
> 对应里程碑：`P2-M2` Procedure Runtime 与多 Agent 执行  
> 依据：`design_docs/phase2/roadmap_milestones_v1.md`、`design_docs/procedure_runtime.md`、ADR 0047、ADR 0048、ADR 0059

## 1. 目标

- 将 `Procedure Runtime` 从草案推进为最小可用的 deterministic runtime control layer。
- 在单一用户利益、单一 `SOUL / principal` 约束下，引入 execution-oriented 多 agent runtime。
- 建立中途 steering / interrupt / resume 的产品语义。
- 固化 bounded handoff 与 purposeful compact，避免多 agent runtime 重新变成全文上下文复制。
- 为后续 Shared Companion 预留 actor / principal / shared-space execution context，但不在 `P2-M2a` 中实现关系记忆或多方身份治理。

## 2. 当前基线（输入）

- 现有 agent runtime 仍以单 agent loop 为主。
- M2 compaction 已存在，但主要服务长会话压缩，不是 task-state-oriented compact。
- `Procedure Runtime` 设计已定稿，但 runtime object、spec registry 与 active procedure lifecycle 尚未实现。
- devcoord 已验证“多执行单元协作”在开发治理层有价值，但这套能力尚未进入产品运行时。
- 当前没有正式的 runtime handoff packet、sub-agent role contract、publish / merge contract。
- 当前 `ToolContext` 与 procedure 草案主要以 `scope_key/session_id` 为上下文边界，还没有稳定的 actor principal、publish target 或 `shared_space_id` 表达。

实现参考：
- `src/agent/agent.py`
- `src/agent/compaction.py`
- `design_docs/procedure_runtime.md`
- `decisions/0047-neomagi-multi-agent-single-soul-execution-units.md`

## 3. 复杂度评估与建议拆分

`P2-M2` 复杂度：**高**。  
原因：它同时覆盖 runtime state machine、多 agent contract、handoff、steering、compact。

建议拆成 2 个内部子阶段：

### P2-M2a：Procedure Runtime Core
- `ProcedureSpec`
- `ActiveProcedure`
- `ProcedureContextRegistry` / `ProcedureGuardRegistry`
- `ToolResult.context_patch`
- validator / executor / transition
- session-scoped single active procedure
- checkpoint-based steering / resume
- execution context 余量：不要求实现 shared-space memory，但不能把所有 guard / event / handoff 设计永久写死为单 actor

### P2-M2b：Multi-Agent Runtime
- primary / worker / reviewer roles
- handoff packet
- bounded context exchange
- purposeful compact 与 publish / merge
- handoff / publish 语义预留 `source_actor`、`target_context` 与未来 `shared_space_id`

## 4. 目标架构（高层）

### 4.1 Procedure Plane

- 引入正式 runtime object：
  - `ProcedureSpec`
  - `ActiveProcedure`
- V1 先固定为 session-scoped single active procedure；并发 procedure 留待后续单独设计。
- `AgentLoop` 只负责识别当前是否有 active procedure 并委托执行，不内联完整流程状态机。
- `ProcedureRuntime` / `ProcedureExecutor` 负责 `guard -> execute -> patch -> transition -> CAS` 主链路。
- 只约束：
  - checkpoint
  - guard
  - transition
  - side-effect boundary
- 不追求完整 choreography，不将其扩张成重型 workflow engine。

### 4.2 Agent Role Plane

- runtime 角色应保持简单：
  - `primary agent`
  - `worker agent`
  - `reviewer / critic agent`
- 所有角色共享同一用户利益与同一 `SOUL / principal`。
- 子 agent 默认不拥有独立长期记忆与独立长期身份。
- 这里的 `principal` 仍是当前执行所代表的用户利益轴；未来 Shared Companion 场景中的多 principal / shared space 不应通过多 SOUL 或多长期人格来表达，而应通过 `P2-M3` 的 membership 与 memory visibility policy 表达。

### 4.3 Handoff / Exchange Plane

- agent 间默认只交换 bounded packet，而不是全文上下文：
  - task brief
  - constraints
  - current state
  - intermediate result
  - evidence
  - open questions
- publish / merge 应显式发生：
  - 没有 publish / merge 的结果，不进入用户级连续性。
- packet 结构应预留来源与可见性字段，例如 source actor、source principal、intended publish target、visibility intent。`P2-M2` 可以先保留为可选元数据，不在本阶段判定 shared-space memory 权限。

### 4.4 Steering / Resume Plane

- 用户中途追加 steering 时，不直接依赖模型“自行理解新意图”。
- steering 应在 checkpoint 生效。
- 中断后恢复依赖：
  - `ActiveProcedure.state`
  - handoff packet
  - compacted task state
而不是只依赖 prompt 历史。

### 4.5 Purposeful Compact Plane

- compact 的目标从“摘要聊天”升级为“保留任务状态”：
  - 当前目标
  - TODO
  - blockers
  - last valid result
  - pending approvals
- 该层既服务长任务恢复，也服务 multi-agent handoff。
- compacted task state 不应混入未授权私有记忆；若未来被发布到 shared space，必须经 `P2-M3` 的 consent / visibility policy 过滤。

### 4.6 Shared Companion Reserve

- `P2-M2` 不实现 Shared Companion 产品能力，但必须避免把 runtime 设计封死在“一个 session 永远只等于一个私有 principal”的假设上。
- `ProcedureRuntime` / `ProcedureExecutor` 的 V1 最小实现可以继续以 session-scoped single active procedure 为边界；同时应让后续扩展能显式携带：
  - actor / source role
  - principal identity
  - publish target
  - visibility intent
  - future `shared_space_id`
- 如果需要 demo，可选用无持久关系记忆的 `relationship_checkin` procedure 来验证 checkpoint / 双方输入收集 / 同意确认 / summary confirmation；该 demo 不得读取或写入真实 shared memory。

## 5. 边界

- In:
  - 最小 procedure runtime。
  - execution-oriented multi-agent runtime。
  - steering / interrupt / resume。
  - bounded handoff。
  - purposeful compact。
  - 为 actor / principal / future shared-space metadata 预留 runtime context。
- Out:
  - 不建设通用 workflow engine。
  - 不引入 DAG / DSL / 并行调度系统。
  - 不实现多人格产品层。
  - 不让子 agent 获得独立长期记忆。
  - 不追求无边界 agent society。
  - 不实现 Shared Companion 的 relationship memory、membership、consent policy 或 shared-space retrieval。

## 6. 验收对齐（来自 roadmap）

- 用户可在多阶段任务中途追加 steering，并在 checkpoint 生效。
- 同一任务可由多个 agent 分工推进，handoff 只交换必要上下文。
- 流程中断后可从明确状态恢复，而不是完全依赖模型重新理解历史。
- 多 agent 运行时保持“单一用户利益 / 单一 SOUL”边界，不退化为多人格协作系统。
- runtime / handoff 设计不阻断后续 `P2-M3` 添加 principal / shared-space visibility policy。
