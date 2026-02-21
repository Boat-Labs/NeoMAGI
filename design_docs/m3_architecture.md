# M3 Architecture（计划）

> 状态：planned  
> 对应里程碑：M3 会话外持久记忆  
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0006/0014/0027、现有 memory 与 prompt 相关实现状态

## 1. 目标
- 建立“可沉淀、可检索、可治理”的会话外记忆闭环。
- 建立“可验证、可回滚、可审计”的自我进化最小闭环（以 `SOUL.md` 为首个治理对象）。

## 2. 当前基线（输入）
- 工作区模板已包含 `memory/` 与 `MEMORY.md`。
- `MEMORY.md` 已在 main session 注入。
- `memory_search` 工具已注册但仍为占位实现。
- `memory_append` 尚未落地，当前缺少受控的记忆写入原子接口。
- M1.5 仅预留 Memory 组授权框架，不负责 `memory_append` 的实际实现。
- `SOUL.md` 已参与每次 turn 注入，但尚无提案/eval/回滚管线。
- 当前缺少 `SOUL.md` 版本快照、回滚入口与审计记录。

实现参考：
- `src/infra/init_workspace.py`
- `src/agent/prompt_builder.py`
- `src/tools/builtins/memory_search.py`
- `design_docs/system_prompt.md`

## 3. 目标架构（高层）
- M3 采用“双闭环”架构，保持低耦合：
  - 记忆闭环（Memory Loop）：沉淀、检索、治理。
  - 进化闭环（Evolution Loop）：提案、评测、生效、回滚。

### 3.1 Memory Loop（会话外记忆）
- 记忆数据源保持文件导向（daily notes + `MEMORY.md`）。
- 检索数据面与基础数据库决议对齐：PostgreSQL 16 + `pg_search` + `pgvector`。
- 检索路径按阶段推进：先 BM25，再 Hybrid Search（BM25 + vector 融合）。
- 记忆操作通过原子工具暴露给 agent：
  - `memory_search`：检索。
  - `memory_append`：受控追加写入 daily notes。

### 3.2 Evolution Loop（SOUL 自我进化最小闭环）
- 更新流程固定为：提案 -> eval -> 生效 -> 回滚。
- bootstrap 例外：仅当 `SOUL.md` 缺失时允许一次性 `v0-seed` 初始化，之后进入常规提案流程。
- 提案阶段：agent 生成变更意图、风险说明与预期行为差异（不直接生效）。
- eval 阶段：基于代表性任务与约束检查验证“用户利益优先”和“反漂移”要求。
- 生效阶段：仅允许 agent 写入 `SOUL.md`，并生成可追溯版本快照。
- 回滚阶段：用户可执行 veto/rollback，系统恢复到最近稳定版本并保留审计记录。
- 进化能力同样遵循原子接口思路（小能力、可组合、可审计），避免一次性大而全流程。

## 4. 边界
- In:
  - 会话外的记忆写入、检索、治理闭环。
  - 明确短期（daily）与长期（curated）记忆职责分层。
  - `SOUL.md` 的 AI-only 写入、eval gating、veto/rollback 与审计。
- Out:
  - 不做重型知识图谱或复杂多库同步。
  - 不允许人类直接编辑 `SOUL.md` 文本作为常规路径。
  - 不允许未评测、不可回滚的人格/行为变更直接生效。

## 5. 验收对齐（来自 roadmap）
- 用户已确认的偏好和事实，跨天可被稳定记起并用于后续任务。
- 用户追问历史原因时，agent 可给出可追溯、可复用的信息。
- agent 提出的 `SOUL.md` 更新仅在 eval 通过后生效，失败可回滚。
- 用户可在生效后执行 veto/rollback，恢复到稳定版本并可追溯变更链路。
