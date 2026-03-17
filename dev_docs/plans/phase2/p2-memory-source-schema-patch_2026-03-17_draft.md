# P2 Memory Source Schema Patch 实施计划

- Date: 2026-03-17
- Status: draft
- Scope: 仅覆盖 ADR 0053 的最小实现补丁；让 daily note 真源写入 `entry_id + scope_key + source_session_id`，并让数据库索引层能够解析和持久化这些字段
- Track Type: parallel implementation patch track; outside the `P2-M*` product milestone series
- Execution Issue: `NeoMAGI-6ra`
- Role Split:
  - Claude: implementation player; 负责按本计划提交 patch
  - Codex: reviewer / steering; 负责审阅实现、卡边界、验收风险与回归
- Basis:
  - [`AGENTS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTS.md)
  - [`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md)
  - [`design_docs/memory_architecture_v2.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/memory_architecture_v2.md)
  - [`design_docs/phase1/memory_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase1/memory_architecture.md)
  - [`decisions/0034-openclaw-dmscope-session-and-memory-scope-alignment.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0034-openclaw-dmscope-session-and-memory-scope-alignment.md)
  - [`decisions/0053-memory-entry-ids-and-projection-only-content-hashes.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0053-memory-entry-ids-and-projection-only-content-hashes.md)
  - [`src/memory/writer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/writer.py)
  - [`src/memory/indexer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/indexer.py)
  - [`src/memory/models.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/models.py)
  - [`src/tools/builtins/memory_append.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/builtins/memory_append.py)
  - [`src/tools/context.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/context.py)
  - [`src/session/database.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/database.py)

## Context

ADR 0053 已经明确 daily note 真源的最小字段集应为：

- `entry_id`
- `scope_key`
- `source_session_id`

同时明确：

- `content_sha256` 仍属于数据库 projection / reindex state，不回写 source markdown
- `thread_id` 留在数据库 projection，不进入 daily note 真源
- retrieval 边界仍由 `scope_key` 控制，`source_session_id` 只表达 provenance，不承担隔离语义

当前代码与该决议仍有明显偏差：

- [`MemoryWriter.append_daily_note()`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/writer.py) 只写 `source` 与 `scope`
- [`MemoryAppendTool.execute()`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/builtins/memory_append.py) 没有把 `ToolContext.session_id` 透传到 writer
- `process_flush_candidates()` 虽然拿到 `ResolvedFlushCandidate.source_session_id`，但没有写入 daily note
- [`MemoryIndexer`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/indexer.py) 只解析 `scope` 和正文，不解析 `entry_id` / `source_session_id`
- [`memory_entries`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/models.py) 还没有能够保存这两个字段的列

因此，本轮 patch 的目标不是做新 retrieval，而是先把“源格式”和“数据库 projection 基础字段”对齐，让后续 graph/thread 演化建立在稳定 provenance 上。

## Goal

在不破坏当前 daily note append-only 协议的前提下，完成 ADR 0053 的最小可用闭环：

1. 新写入的 daily note 条目都带有：
   - `entry_id`
   - `scope_key`
   - `source_session_id`
2. 数据库索引层能够解析并持久化 `entry_id` 与 `source_session_id`
3. 历史条目继续可读、可加载、可 reindex，不要求回写旧文件
4. 现有 recall / prompt 注入 / memory search 行为不发生意外退化
5. 为后续 thread membership / graph projection 预留稳定对象身份与 provenance 字段

## Non-Goals

- 不在本轮实现 `thread_id`
- 不在本轮实现 `doc_nodes` / `doc_edges`
- 不在本轮实现 multi-hop retrieval
- 不在本轮实现 hybrid search
- 不在本轮引入 YAML frontmatter
- 不在本轮把 `content_sha256` 写回 source markdown
- 不在本轮重写现有 `PromptBuilder` 的 daily note 加载语义
- 不要求回填或重写已有 daily note 文件中的历史条目

## Hard Constraints

- daily note 仍保持现有 `---` 分隔的 append-only 容器协议
- `scope_key` 仍是检索隔离唯一真源；不得从 `source_session_id` 反推或替代 `scope_key`
- 历史数据兼容必须保留：
  - 没有 `entry_id` 的旧条目可正常加载和 reindex
  - 没有 `source_session_id` 的旧条目可为空，不得报错
- 不新增外部依赖来生成 ID
- 当前运行时是 Python `3.13.7`，标准库无 `uuid.uuid7()`；若坚持 `UUIDv7`，必须用最小本地 helper 实现，不允许为此引入第三方包
- 不把 `thread_id`、主题归类、语义聚类逻辑提前混入本轮 patch

## Patch Boundary

本轮 patch 只覆盖两层：

### 1. Source-Truth Layer

- daily note 元数据行新增：
  - `entry_id`
  - `source_session_id`
- `scope_key` 继续保留

目标格式：

```md
---
[22:47] (entry_id: 0195..., source: user, scope: main, source_session_id: telegram:peer:123)
用户喜欢蓝色。
```

### 2. Retrieval Data Plane Compatibility Layer

- `memory_entries` 新增可空字段：
  - `entry_id`
  - `source_session_id`
- reindex / incremental index 都能把它们写入数据库
- 当前 search API 不要求立刻把这些字段暴露给 prompt 或工具返回

这意味着数据库“理解 0053”，但 retrieval 策略本身不升级。

## Proposed Implementation

### Slice A: ID Generation and Writer Contract

目标：让 writer 具备生成 `entry_id` 并写入 provenance 的能力。

建议改动：

- 在 [`src/memory/writer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/writer.py) 中：
  - 为 `append_daily_note()` 增加 `source_session_id` 参数
  - 引入最小 `UUIDv7` 生成 helper
  - 将 metadata line 扩展为 `entry_id + source + scope + source_session_id`
  - `logger` 输出中增加 `entry_id`，便于审计
- 保持返回值仍为 `Path`，避免扩大上层接口变更

说明：

- 本轮不要求 writer 返回结构化 entry object
- 若未来需要返回 `entry_id`，再单独做 API 升级
- `append_daily_note()` 一旦新增 `source_session_id` 参数，所有调用点必须在同一实现切片内同步更新；不接受只改 writer 签名、暂时让 flush/tool 路径处于半坏状态

### Slice B: Tool and Flush Propagation

目标：把现有运行时上下文中的 `session_id` 真正落到 daily note 真源。

建议改动：

- 在 [`src/tools/builtins/memory_append.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/builtins/memory_append.py) 中：
  - 使用 `context.session_id`
  - 将其透传为 `source_session_id`
- 在 [`src/memory/writer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/writer.py) 的 `process_flush_candidates()` 路径中：
  - 将 `ResolvedFlushCandidate.source_session_id` 透传给 `append_daily_note()`
- 不修改 `ToolContext` 结构；[`src/tools/context.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/context.py) 已具备 `session_id`

约束：

- Slice A 与 Slice B 在提交层面视为一个不可拆开的纵向切片：writer 签名变更、`memory_append` 透传和 flush 透传必须同一 commit 完成

### Slice C: Parser and Indexer Compatibility

目标：让数据库层真正能理解并持久化 `0053` 字段。

建议改动：

- 在 [`src/memory/indexer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/indexer.py) 中：
  - 增加 metadata 解析 helper，至少提取：
    - `entry_id`
    - `scope`
    - `source_session_id`
  - 避免对同一 metadata line 重复跑多次正则，优先统一抽一个 metadata parser
  - `_parse_daily_entries()` 生成 row 时带上新字段
  - `index_entry_direct()` 支持写入 `entry_id` / `source_session_id`
- 保持旧格式兼容：
  - 没有 `entry_id` 时，DB 中可为 `NULL`
  - 没有 `source_session_id` 时，DB 中可为 `NULL`

### Slice D: Database Model and Schema Backfill

目标：让 `memory_entries` 有地方保存新增 provenance 字段。

建议改动：

- 在 [`src/memory/models.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/models.py) 中给 `MemoryEntry` 增加：
  - `entry_id`
  - `source_session_id`
- 字段要求：
  - 初始为 nullable
  - 不在本轮强加 unique constraint
  - 可根据需要增加普通 index，优先服务后续 lookup / projection
- 在 [`src/session/database.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/database.py) 的 schema ensure/backfill 路径中，增加 additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS ...`

说明：

- 本轮不做复杂 migration 系统改造
- 目标是确保新列在现有 `ensure_schema()` 路径上可幂等创建
- 本轮选择 `ensure_schema()` additive backfill，是延续当前仓库既有模式；后续若项目统一 schema 管理，再单开 follow-up 补 Alembic migration，不在本 patch 混做

### Slice E: Test and Backward-Compatibility Guardrails

目标：用测试锁住新增字段和旧数据兼容语义。

建议新增或修改测试：

- [`tests/test_memory_writer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_memory_writer.py)
  - 新写入条目包含 `entry_id`
  - 新写入条目包含 `source_session_id`
  - flush candidate path 会写入 `source_session_id`
- [`tests/test_memory_append_tool.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_memory_append_tool.py)
  - `context.session_id` 被透传
- [`tests/test_memory_indexer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_memory_indexer.py)
  - 新格式 metadata 可解析
  - 旧格式 metadata 继续兼容
- [`tests/test_prompt_daily_notes.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_prompt_daily_notes.py)
  - 带新 metadata 的条目仍按 `scope_key` 正常过滤
- [`tests/test_ensure_schema.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_ensure_schema.py)
  - 验证 `memory_entries` 新列在 `ensure_schema()` 下可幂等存在

## Expected File Touch List

Claude 实现时，预期主要落在这些文件：

- [`src/memory/writer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/writer.py)
- [`src/memory/indexer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/indexer.py)
- [`src/memory/models.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/models.py)
- [`src/tools/builtins/memory_append.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/builtins/memory_append.py)
- [`src/session/database.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/database.py)
- [`tests/test_memory_writer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_memory_writer.py)
- [`tests/test_memory_append_tool.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_memory_append_tool.py)
- [`tests/test_memory_indexer.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_memory_indexer.py)
- [`tests/test_prompt_daily_notes.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_prompt_daily_notes.py)
- [`tests/test_ensure_schema.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_ensure_schema.py)

原则上不应扩大到以下区域：

- `src/memory/searcher.py`
- `src/tools/builtins/memory_search.py`
- `src/agent/prompt_builder.py` 的整体重构
- thread / graph retrieval 相关新模块

## Execution Strategy

建议 Claude 按 3 个小 commit 或 3 个 patch slice 实现，而不是一个大补丁：

1. model + ensure_schema + indexer additive support
2. writer + tool propagation + flush propagation
3. tests +兼容性收口

理由：

- 先让数据库层“接得住”新增字段，再让 writer 发射新增字段，减少中间态不一致
- 方便审阅 provenance 字段是“写进去但没解析”，还是“解析了但 schema 没跟上”
- 方便 Codex 以 reviewer 身份逐层卡边界
- 避免把“源格式升级”和“DB 模型升级”混成难以定位的回归

中间态约束：

- commit 1 完成后，数据库层已能解析/持久化 `entry_id` 与 `source_session_id`，但 writer 尚未开始发射这两个字段，这是可接受中间态
- commit 2 完成后，新写入 daily note 与增量索引路径应同时带上新字段，不允许出现“真源已写入但 `index_entry_direct()` 仍未跟上”的半更新状态
- 若 Claude 因实现细节临时保留“commit 1 之后、commit 2 之前的增量索引行缺少新字段”的窗口，必须在 handoff 中明确说明，并注明一次全量 reindex 可修复；但首选方案仍是按本计划顺序避免该窗口

## Review Protocol

为了尽量分开“裁判”和“运动员”，执行方式建议固定为：

1. Claude 按本计划实现 patch
2. Codex 不直接代写实现，而是在 patch 完成后执行 review
3. Review 重点只看以下几类问题：
   - `source_session_id` 是否真的写进真源，而不是只存在日志或 DB
   - `scope_key` 是否仍然是唯一隔离边界
   - 旧 daily note 是否继续可加载
   - `memory_entries` 新字段是否只是 additive，不破坏现有 search
   - parser 是否避免不必要的重复 regex / 协议分叉
4. 若 Claude 实现偏离本计划，优先让 Claude 修正；Codex 只做 steering 和裁判结论

## Verification Plan

Claude 完成 patch 后，至少需要跑：

1. `uv run pytest tests/test_memory_writer.py`
2. `uv run pytest tests/test_memory_append_tool.py`
3. `uv run pytest tests/test_memory_indexer.py`
4. `uv run pytest tests/test_prompt_daily_notes.py`
5. `uv run pytest tests/test_ensure_schema.py`

若环境允许，再补：

1. `just lint`
2. `just test`

若 `tests/test_ensure_schema.py` 受本地 PostgreSQL 环境限制无法通过，必须在 handoff 中明确说明，不得省略。

## Acceptance

本 patch 视为完成，至少需要同时满足：

- 新写入 daily note 的 metadata line 包含 `entry_id + scope + source_session_id`
- `memory_append` 路径透传 `context.session_id`
- flush candidate 路径透传 `source_session_id`
- `memory_entries` 存在 `entry_id` 与 `source_session_id` 字段
- full reindex 能从新格式 daily note 解析并写入这两个字段
- 旧格式 daily note 不报错，兼容测试通过
- 现有按 `scope_key` 的 prompt 加载和 search 行为未被破坏
- 本轮没有引入 `thread_id`、graph traversal 或 retrieval 策略升级

## Handoff Notes for Claude

- 这是一个“source-truth schema alignment patch”，不是 retrieval feature 迭代。
- 不要借机重写 `PromptBuilder` 或 `memory_search`。
- 不要把 `source_session_id` 做成从 `scope_key` 反推的派生值；它必须来自运行时 session provenance。
- 不要把 `thread_id` 偷渡进 schema。
- 若 `UUIDv7` helper 需要自实现，保持最小、可测试、无外部依赖；不要为此引入新包。
