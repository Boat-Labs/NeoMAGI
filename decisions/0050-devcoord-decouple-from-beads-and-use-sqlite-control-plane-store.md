# 0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store

- Status: proposed
- Date: 2026-03-06
- Note: 本 ADR 只讨论 `devcoord` 的控制面存储与边界，不改变产品运行时 PostgreSQL 基线。

## 背景

- ADR 0042 选择了 `beads + Dolt` 作为 `devcoord` 的结构化 SSOT，动机是把多 agent 协作从 prompt 驱动、手工维护日志文件，提升为可程序化执行的最小控制面。
- 这一方向本身是正确的：`devcoord` 不能退回“主要靠文档维护”的模式，`GATE_OPEN`、`ACK`、`RECOVERY_CHECK`、`STATE_SYNC_OK`、`GATE_CLOSE`、`render`、`audit` 等协议仍然需要 deterministic runtime。
- 但经过 `P2-M1a` 的实际使用，`beads` 同时承载“真实 backlog/work issue”和“control-plane 对象”带来了明显语义冲突：
  - `bd list --status open` 同时混入 backlog 任务与 `coord` milestone / phase / gate / agent / message / event 对象，open 视图失去直接可读性。
  - `coord` 对象的真实状态很多存在 metadata 中，如 `gate_state`、`phase_state`、`agent_state`，而不是 `issue.status`；这与 issue tracker 的直觉语义天然冲突。
  - milestone closeout 若漏掉 `milestone-close` 或 projection 对账，控制面对象会继续显示为 open，进一步污染 backlog 视图。
- 当前问题已经不再是“是否需要结构化 control plane”，而是“是否应该继续用 issue/backlog 系统承载 control-plane 状态机”。

## 选了什么

- 将 `devcoord` 从 `beads` 语义上完全解耦：
  - `beads / bd` 回到 backlog / issue graph / Jira 面，只承载真实工作任务、缺陷、review follow-up、epic 与依赖关系。
  - `devcoord` 保留为独立的协作控制面，只承载 Gate、ACK、生效、恢复握手、对账、审计等协议状态。
- 为 `devcoord` 引入一个专用的 SQLite control-plane store，作为新的开发协作控制面 SSOT。
- 保持 `scripts/devcoord` 作为协议语义唯一实现层；继续禁止 agent 直接手写控制状态。
- 保持 `dev_docs/logs/<phase>/` 与 `dev_docs/progress/project_progress.md` 为 projection，而不是控制面真源。
- 保持 `AGENTTEAMS.md` 中的 Gate / ACK / recovery / audit / closeout 协议边界不变；本 ADR 只替换 control-plane backend，不降级治理强度。
- 不迁移既有 `beads` control-plane 历史数据到 SQLite；cutover 时直接从空的 `.devcoord/control.db` 启动，历史 `coord` 记录仅做关闭/归档。
- 协议语义保持稳定，但 `coord.py` 的人类可见命令面应收敛为更少的分组命令，`apply` 保留为 machine-first 入口。
- 对应技术草案见 `design_docs/devcoord_sqlite_control_plane_draft.md`。
- SQLite 仅用于内部开发协作控制面，不进入产品运行时数据面；产品运行时数据库仍保持 PostgreSQL 17 基线。

## 为什么

- `devcoord` 的核心问题是协议状态机，而不是任务图本身。继续把控制面对象塞进 issue/backlog 系统，会让“谁有待完成工作”和“协议里发生了什么”长期混淆。
- `beads` 很适合 backlog、依赖和 issue 查询，但不适合表达需要靠 metadata 与 wrapper 语义解释的控制协议对象。
- `devcoord` 当前使用场景是本机、共享 worktree 控制面、小规模角色协作；不需要独立 server，也不需要产品级数据库运维负担。
- SQLite 对该场景更匹配：
  - Python 标准库自带 `sqlite3`，不引入新的 server 依赖；
  - 支持事务、约束、索引和 append-only 事件表；
  - 适合单机共享 control-plane store；
  - 比 PostgreSQL 更轻，比纯文档更可程序化。
- 不复用 PostgreSQL 的原因不是能力不足，而是边界不合适：
  - `devcoord` 是开发协作控制面，不应绑定到产品运行时数据库生命周期；
  - 避免把 `.env`、连接可用性、schema 演进和产品数据面耦合进内部协作控制；
  - 保持“产品 SSOT”和“开发协作 SSOT”之间的概念隔离。
- 不退回文档驱动的原因是：那会重新把 ACK 生效、恢复握手、对账与 gate close 条件交还给 prompt 和记忆，失去 `devcoord` 建立的最小确定性保证。
- 不做历史数据迁移的原因是：现有 `coord` 记录本质上是开发阶段实验产物，继续为兼容这些旧记录而设计导入链路，只会增加 SQLite cutover 的复杂度和负担，而不会提高 NeoMAGI 编程智能体的长期能力沉淀质量。

## 放弃了什么

- 方案 A：保持 ADR 0042 的 `beads + Dolt` control-plane backend，只通过过滤查询、改进 closeout 和新增文档解释来降低混淆。
  - 放弃原因：能缓解信噪比，但不能消除“issue tracker 语义”与“control-plane 状态机语义”天然错位的问题。
- 方案 B：将 `devcoord` 再次降级为主要靠 `dev_docs/logs/`、`gate_state.md`、`heartbeat_events.jsonl` 等文档管理。
  - 放弃原因：这会回到 M7 之前的问题；文档适合 projection 和审阅，不适合作为协议真源。
- 方案 C：直接复用产品运行时 PostgreSQL 作为 `devcoord` store。
  - 放弃原因：虽然技术上可行，但会把内部协作控制与产品数据面耦合，增加环境、迁移与运维前提，违背“尽量减少系统依赖复杂度”的目标。
- 方案 D：引入新的远程服务型 control-plane backend（如 Redis、独立服务、消息队列）。
  - 放弃原因：对当前单机共享控制面是明显过度设计。

## 影响

- 若本 ADR 被接受，ADR 0042 中“`beads + Dolt` 作为 devcoord control-plane SSOT”这一存储选择将被 supersede；但其关于“`scripts/devcoord` 负责协议语义、`dev_docs` 只是 projection”的边界仍保留。
- `beads` 的职责将收敛为 backlog / issue graph；日常 `bd list --status open` 将重新可读。
- `scripts/devcoord` 将需要引入新的 SQLite store 适配层，并逐步移除对 `bd ... --json` 的依赖。
- `coord.py` 后续应从“扁平的多命令面”收敛为 grouped command surface，同时保留一段兼容期。
- `dev_docs/devcoord/beads_control_plane.md` 后续应演化为更中性的 `devcoord_control_plane.md`，不再把 `beads` 写成控制面真源。
- `AGENTTEAMS.md` 与相关 PM/backend/tester skill 的协议内容大体不变，但 closeout 语义将从 “beads sync + milestone-close” 调整为 “SQLite store closeout + render/audit/milestone-close”。
- 迁移应采用最小风险路径：
  - 先保留命令面与 projection；
  - 再切换 store adapter；
  - 最后停止将 control-plane 对象写入 `beads`。
- 这里的“迁移”仅指代码与命令面切换，不包括把历史 `beads` control-plane 数据导入 SQLite。
