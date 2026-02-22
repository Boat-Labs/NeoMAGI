# 记忆架构（当前状态 + M2/M3 规划）

> 原则不变：记忆基于文件，不依赖模型参数记忆。  
> 核心句：**"Mental notes don't survive session restarts. Files do."**

## 1. 当前状态（截至 2026-02-22）

### 1.1 已实现
- 工作区初始化会创建 `memory/` 与 `MEMORY.md` 模板。
- `memory_search` 工具入口已注册（当前仍为占位实现）。
- Session 基础隔离已落地：DM -> `main`，group -> `group:{channel_id}`。

实现参考：
- `src/infra/init_workspace.py`
- `src/agent/prompt_builder.py`
- `src/tools/builtins/memory_search.py`
- `src/session/manager.py`

### 1.2 未实现（已明确进入后续里程碑）
- `memory_search` 实际检索逻辑（BM25）未落地。
- `memory_append` 原子写入工具未落地。
- daily notes 自动加载（今天+昨天）未落地。
- flush 候选持久化到 daily notes 未落地。
- `dmScope` 在 memory 数据面与 recall 检索面的统一过滤未落地。

## 2. 架构分层（目标形态）

```text
┌──────────────────────────────────────────────────┐
│ Context Window (每次 turn)                        │
│ AGENTS / USER / SOUL / IDENTITY / TOOLS / MEMORY │
│ * memory recall must follow current dmScope       │
├──────────────────────────────────────────────────┤
│ Session Isolation Plane                           │
│ dmScope resolver + session key strategy           │
├──────────────────────────────────────────────────┤
│ Session History（当前对话）                        │
│ PostgreSQL sessions + messages                    │
├──────────────────────────────────────────────────┤
│ Short-term Memory（daily notes）                  │
│ memory/YYYY-MM-DD.md（append-only）               │
├──────────────────────────────────────────────────┤
│ Long-term Memory（curated）                       │
│ MEMORY.md                                         │
├──────────────────────────────────────────────────┤
│ Retrieval Data Plane                              │
│ PostgreSQL 16 + ParadeDB pg_search + pgvector    │
│ (scope-aware indexing + scope-aware filtering)    │
└──────────────────────────────────────────────────┘
```

说明：
- 文件层（daily notes / `MEMORY.md`）是记忆源数据。
- PostgreSQL 检索层用于召回，不改变“记忆以文件为准”的边界。
- Session scope 与 memory scope 同源治理，避免跨作用域记忆泄漏。
- 文件层（Short-term / Long-term）不直接执行 scope 过滤；scope 过滤在检索与 recall 层统一执行。

## 3. dmScope 对齐策略（ADR 0034）

### 3.1 作用域枚举
- `main`：私聊统一会话（默认）。
- `per-peer`：每个私聊对象独立会话。
- `per-channel-peer`：同一对象在不同渠道独立会话。
- `per-account-channel-peer`：同渠道下不同账号进一步隔离。

### 3.2 统一约束
- 会话解析输出是作用域唯一真源。
- 记忆写入必须携带来源作用域元数据（至少 `source_session_id` + 规范化 scope key）。
- 检索与 prompt recall 必须应用相同的作用域过滤规则。
- 不允许出现“session 不共享但 recall 共享”的旁路行为。
- 作用域传播路径（数据流）：
  - `session_resolver(input, dm_scope)` 产出 `scope_key`。
  - `scope_key` 注入 tool context，供 `memory_append` / `memory_search` 使用。
  - `scope_key` 显式传入 memory recall 层（如 `_layer_memory_recall(scope_key=...)`）。
  - memory 工具与 recall 层禁止自行从原始 `session_id` 重算 scope。

### 3.3 里程碑切分
- M3：固化作用域契约、检索过滤口径与验收标准（默认 `main` 可继续运行）。
- M4：多渠道接入时激活非 `main` 作用域并完成渠道映射。
- 配置位置：
  - M3：`dmScope` 由 `SessionSettings` 全局配置（默认 `main`）。
  - M4：扩展为 per-channel 覆盖配置。

## 4. 检索路线（与决议对齐）
- 决议基线：统一 PostgreSQL 16（`pgvector` + `pg_search`），不使用 SQLite。
- 阶段策略：
  - 先 BM25（`pg_search`）形成可用检索。
  - 再 Hybrid Search（BM25 + vector）提升召回质量。

决议参考：
- `decisions/0006-use-postgresql-pgvector-instead-of-sqlite.md`
- `decisions/0014-paradedb-tokenization-icu-primary-jieba-fallback.md`

## 5. 写入与治理边界
- 用户显式要求时可写入记忆文件。
- Agent 在明确规则下可沉淀 daily notes 与长期记忆。
- 记忆原子操作目标：
  - `memory_search`：检索历史记忆（scope-aware）。
  - `memory_append`：受控追加写入 `memory/YYYY-MM-DD.md`（scope-aware）。
- 接近 context 上限时，先做 memory flush，再做 compaction（M2/M3 衔接点）。
- 本文仅定义记忆数据面，不定义 `SOUL.md` 进化治理流程（见 `design_docs/m3_architecture.md` + ADR 0027）。
- `memory_append` 仅用于记忆文件，不用于写入 `SOUL.md`。

## 6. M2/M3 衔接点（Contract）
- M2 输出两类产物：
  - 会话内产物：compaction 后继续对话所需的压缩上下文。
  - 记忆候选产物：memory flush 候选（含来源会话信息）。
- M2 聚焦触发时机与输出契约，不负责会话外持久写入。
- M3 接管持久化写入与检索召回，并补齐作用域过滤闭环。
- M3 在持久化 flush 候选时，通过统一 resolver 将 `source_session_id` 映射为 `scope_key`，并写入检索数据面。
- M3 可扩展候选字段，但不得重定义 M2 既有字段语义。

## 7. 里程碑映射
- M2：会话内连续性（含 pre-compaction memory flush 与 compaction 衔接机制）。
- M3：会话外持久记忆闭环 + `dmScope` 作用域契约落地。
- M4：多渠道激活非 `main` 作用域并完成渠道隔离联调。
