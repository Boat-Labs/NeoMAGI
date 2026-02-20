# 记忆架构（当前状态 + M2/M3 规划）

> 原则不变：记忆基于文件，不依赖模型参数记忆。  
> 核心句：**"Mental notes don't survive session restarts. Files do."**

## 1. 当前状态（截至 2026-02-19）

### 1.1 已实现
- 工作区初始化会创建 `memory/` 与 `MEMORY.md` 模板。
- main session 会注入 `MEMORY.md`（group session 不注入）。
- 记忆相关工具入口已存在：`memory_search` 已注册。

实现参考：
- `src/infra/init_workspace.py`
- `src/agent/prompt_builder.py`
- `src/tools/builtins/memory_search.py`

### 1.2 未实现（已明确进入后续里程碑）
- `memory_search` 检索逻辑仍为占位。
- `memory_append` 原子写入工具尚未实现。
- 自动加载“今天+昨天”daily notes 尚未落地。
- pre-compaction memory flush 尚未落地。
- 会话 compaction 尚未落地。

## 2. 架构分层（目标形态）

```
┌──────────────────────────────────────────────────┐
│ Context Window (每次 turn)                        │
│ AGENTS / USER / SOUL / IDENTITY / TOOLS / MEMORY │
│ * MEMORY.md 仅 main session 注入                  │
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
└──────────────────────────────────────────────────┘
```

说明：
- 文件层（daily notes / MEMORY.md）是记忆源数据。
- PostgreSQL 检索层用于召回，不改变“记忆以文件为准”的设计边界。

## 3. 检索路线（与决议对齐）
- 决议基线：统一 PostgreSQL 16（`pgvector` + `pg_search`），不使用 SQLite。
- 阶段策略：
  - 先 BM25（`pg_search`）形成可用检索。
  - 再 Hybrid Search（BM25 + vector）提升召回质量。

决议参考：
- `decisions/0006-use-postgresql-pgvector-instead-of-sqlite.md`
- `decisions/0014-paradedb-tokenization-icu-primary-jieba-fallback.md`

## 4. 写入与治理边界
- 用户显式要求可写入记忆文件。
- Agent 在明确规则下可沉淀 daily notes 与长期记忆。
- 记忆原子操作目标：
  - `memory_search`：检索历史记忆
  - `memory_append`：受控追加写入 `memory/YYYY-MM-DD.md`
- 接近 context 上限时，先做 memory flush，再做 compaction（M2/M3 衔接点）。

## 5. M2/M3 衔接点（Contract）
- M2 输出两类产物：
  - 会话内产物：compaction 后继续对话所需的压缩上下文。
  - 记忆候选产物：memory flush 生成的候选条目（至少包含候选内容与来源定位）。
- M2 阶段 focus 在“触发时机 + 输出契约（含候选条目数据结构定义）”，不要求交付会话外持久写入能力。
- M3 阶段接管持久化写入：通过 `memory_append` 将候选落盘到 `memory/YYYY-MM-DD.md`，并纳入检索闭环。
- 该分层用于避免 M2 完成后 M3 反向修改 flush/compaction 的输出接口。

## 6. 里程碑映射
- M2：会话内连续性（含 pre-compaction memory flush 与 compaction 衔接机制）。
- M3：会话外持久记忆（记忆检索闭环与长期治理）。
