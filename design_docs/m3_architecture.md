# M3 Architecture（计划）

> 状态：planned  
> 对应里程碑：M3 会话外持久记忆  
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0006/0014、现有 memory 相关实现状态

## 1. 目标
- 建立“可沉淀、可检索、可治理”的会话外记忆闭环。

## 2. 当前基线（输入）
- 工作区模板已包含 `memory/` 与 `MEMORY.md`。
- `MEMORY.md` 已在 main session 注入。
- `memory_search` 工具已注册但仍为占位实现。
- `memory_append` 尚未落地，当前缺少受控的记忆写入原子接口。
- M1.5 仅预留 Memory 组授权框架，不负责 `memory_append` 的实际实现。

实现参考：
- `src/infra/init_workspace.py`
- `src/agent/prompt_builder.py`
- `src/tools/builtins/memory_search.py`

## 3. 目标架构（高层）
- 记忆数据源保持文件导向（daily notes + `MEMORY.md`）。
- 检索数据面与基础数据库决议对齐：PostgreSQL 16 + `pg_search` + `pgvector`。
- 检索路径按阶段推进：先 BM25，再 Hybrid Search（BM25 + vector 融合）。
- 记忆操作通过原子工具暴露给 agent：
  - `memory_search`：检索
  - `memory_append`：受控追加写入 daily notes

## 4. 边界
- In:
  - 会话外的记忆写入、检索、治理闭环。
  - 明确短期（daily）与长期（curated）记忆职责分层。
- Out:
  - 不做重型知识图谱或复杂多库同步。

## 5. 验收对齐（来自 roadmap）
- 用户已确认的偏好和事实，跨天可被稳定记起并用于后续任务。
- 用户追问历史原因时，agent 可给出可追溯、可复用的信息。
