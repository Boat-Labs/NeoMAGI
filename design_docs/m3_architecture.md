# M3 Architecture（计划）

> 状态：planned  
> 对应里程碑：M3 会话外持久记忆  
> 依据：`design_docs/roadmap_milestones_v3.md`、`design_docs/memory_architecture.md`、ADR 0006/0014/0027/0034

## 1. 目标
- 建立“可沉淀、可检索、可治理”的会话外记忆闭环。
- 建立“可验证、可回滚、可审计”的自我进化最小闭环（以 `SOUL.md` 为首个治理对象）。
- 在 M3 固化与 OpenClaw 对齐的 `dmScope` 契约，使会话隔离与记忆召回采用同一作用域语义。

## 2. 当前基线（输入）
- 工作区模板已包含 `memory/` 与 `MEMORY.md`。
- `memory_search` 已注册但仍为占位实现，`memory_append` 尚未落地。
- Prompt 侧当前仅对 `MEMORY.md` 做 main session 条件注入。
- Session 解析当前为简化模型：DM -> `main`，group -> `group:{channel_id}`。
- M2 已输出 flush 候选契约，包含 `source_session_id`，可作为 M3 作用域治理输入。
- `SOUL.md` 已注入 prompt，但尚无提案/eval/回滚管线与版本审计。

实现参考：
- `src/session/manager.py`
- `src/agent/prompt_builder.py`
- `src/tools/builtins/memory_search.py`
- `src/agent/compaction.py`

## 3. 目标架构（高层）
- M3 采用“三层协同”：
  - Session Isolation Plane：会话作用域解析（`dmScope`）。
  - Memory Loop：记忆沉淀、检索、策展。
  - Evolution Loop：`SOUL.md` 提案、评测、生效、回滚。

### 3.1 Session Isolation Plane（dmScope）
- 对齐 OpenClaw 的 `dmScope` 枚举：
  - `main`：私聊统一汇聚到单一会话（当前默认）。
  - `per-peer`：每个私聊对象独立会话。
  - `per-channel-peer`：同一用户在不同渠道独立会话。
  - `per-account-channel-peer`：同一渠道下不同账号进一步隔离。
- 关键约束：
  - 会话 key 解析是作用域唯一真源。
  - Memory 写入必须携带来源作用域元数据。
  - Memory 检索与 prompt recall 必须按作用域过滤，不允许旁路。
- 作用域传播路径（数据流）：
  - `session_resolver(input, dm_scope)` 产出 `scope_key`。
  - `scope_key` 注入到 tool context，由 `memory_append` / `memory_search` 消费。
  - `scope_key` 传递到 prompt recall 层（如 `_layer_memory_recall(scope_key=...)`）用于召回过滤。
  - memory 相关工具与 prompt layer 禁止自行二次推导 scope，避免多套规则漂移。
- 配置策略：
  - M3 阶段 `dmScope` 采用全局默认 `main`，配置归属 `SessionSettings`。
  - M4 阶段扩展为 per-channel 配置覆盖（保留全局默认作为回退）。
- 兼容策略：
  - 保留现有 `main` 与 `group:{channel_id}` 语义作为过渡。
  - 迁移不改变已有 session record 的 key 语义，仅扩展作用域元数据与过滤规则。

### 3.2 Memory Loop（会话外记忆）
- 记忆源数据保持文件导向（daily notes + `MEMORY.md`），DB 仅作为检索数据面。
- 检索数据面与决议对齐：PostgreSQL 16 + `pg_search` + `pgvector`。
- 检索路径按阶段推进：M3 先 BM25，Hybrid Search（BM25 + vector）后续迭代。
- 记忆操作通过原子工具暴露给 agent：
  - `memory_search`：检索（必须受作用域过滤）。
  - `memory_append`：受控追加写入 daily notes（写入时记录作用域元数据）。
- Prompt recall 规则：
  - 召回来源必须与当前会话作用域一致。
  - 召回阈值、结果数与 token 上限可配置。

### 3.3 Evolution Loop（SOUL 自我进化最小闭环）
- 更新流程固定为：提案 -> eval -> 生效 -> 回滚。
- bootstrap 例外：仅当 `SOUL.md` 缺失时允许一次性 `v0-seed` 初始化，之后进入常规提案流程。
- 治理边界（ADR 0027）：
  - `SOUL.md` 常态写入仅允许 agent。
  - 所有变更必须先通过 eval。
  - 用户随时保留 veto/rollback 权限。
  - 版本链路必须可审计、可追溯。

## 4. M3 与 M4 职责切分
- M3 负责：
  - `dmScope` 契约定义与验证口径落地。
  - Session/Memory/Prompt 三层作用域语义一致性。
  - 在默认 `main` 运行形态下完成完整闭环。
- M4 负责：
  - 第二渠道（Telegram）接入时激活非 `main` 作用域。
  - 渠道身份映射与跨渠道隔离行为联调验证。

## 5. 边界
- In:
  - 会话外记忆写入、检索、策展闭环。
  - `dmScope` 作用域契约与验收口径（会话解析 + 记忆召回一致性）。
  - `SOUL.md` 的 AI-only 写入、eval gating、veto/rollback 与审计。
- Out:
  - 不做重型知识图谱或复杂多库同步。
  - 不在 M3 完成第二渠道接入与全量非 `main` 作用域联调。
  - 不允许未评测、不可回滚的人格/行为变更直接生效。

## 6. 验收对齐（来自 roadmap）
- 用户已确认的偏好和事实，跨天可被稳定记起并用于后续任务。
- 用户追问历史原因时，agent 可给出可追溯、可复用的信息。
- agent 提出的 `SOUL.md` 更新仅在 eval 通过后生效，失败可回滚。
- 用户可在生效后执行 veto/rollback，恢复到稳定版本并可追溯变更链路。
- 不同会话作用域下，记忆召回遵循 `dmScope`，不会发生未授权跨作用域泄漏。
