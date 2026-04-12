---
doc_id: 019d8153-aad9-7561-b8e9-21e002fd8f16
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-12T12:53:57+02:00
---
# P2-M3b 实现计划：Memory Ledger & Visibility Policy

> 状态：approved
> 日期：2026-04-12
> 输入：`design_docs/phase2/p2_m3_architecture.md` Section 3 (P2-M3b)
> 架构基础：ADR 0034 (dmScope alignment), ADR 0053 (stable identity), ADR 0059 (Shared Companion boundary), ADR 0060 (Memory Source Ledger)
> 前置完成：P2-M2d (Memory Source Ledger Prep), P2-M3a (Auth & Principal Kernel)

## 0. 目标

将 P2-M2d 的 DB append-only source ledger 接入 principal identity 与 visibility policy，使 memory 写入、检索、reindex 路径具备可解释的身份与可见性语义。

回答的问题：**一条记忆属于谁，谁能看到它，系统怎么解释这个决定？**

完成后：
- `memory_source_ledger` 携带 `principal_id` 与 `visibility` 列，每条写入关联到 authenticated principal
- `memory_entries` 检索索引携带 `principal_id` 与 `visibility`，search 路径按 principal + visibility 过滤
- `reindex_all` 可从 DB ledger current view 重建 `memory_entries`，不再依赖 workspace Markdown parser
- `memory_append` / `memory_search` 工具透传 principal identity
- visibility 三级语义生效：`private_to_principal`（默认）、`shareable_summary`（reserved）、`shared_in_space`（reserved, deny-by-default）
- 匿名路径（no-auth mode）行为不变：`principal_id = NULL`，`visibility = private_to_principal`，scope_key = "main"
- workspace projection 保持为人类可读 export，不作为机器写入真源

一句话边界：本轮只把 identity + visibility 接入 memory ledger 与 read path；不做 retrieval quality 增强、federation protocol 或 shared-space 产品能力（P2-M3c）。

## 1. 当前基线

| 组件 | 状态 |
|------|------|
| `memory_source_ledger` (P2-M2d) | append-only, 8 列 (event_id, entry_id, event_type, scope_key, source, source_session_id, content, metadata); 无 principal_id/visibility |
| `MemoryLedgerWriter` (`src/memory/ledger.py`) | `append()` 接受 scope_key/source/source_session_id; 不接受 principal_id/visibility |
| `memory_entries` (`src/memory/models.py`) | search index, 无 principal_id/visibility 列 |
| `MemorySearcher` (`src/memory/searcher.py`) | scope_key WHERE 过滤; 无 principal/visibility 过滤 |
| `MemoryIndexer` (`src/memory/indexer.py`) | `reindex_all()` 从 workspace `memory/*.md` + `MEMORY.md` 重建; `index_entry_direct()` 用于增量索引 |
| `MemoryWriter` (`src/memory/writer.py`) | dual-mode (ledger truth-first + workspace projection best-effort); 增量索引仅在 `projection_written` 时触发; 不传递 principal_id |
| `MemoryAppendTool` (`src/tools/builtins/memory_append.py`) | 从 `context.scope_key` / `context.session_id` 读取; 不读取 principal_id |
| `MemorySearchTool` (`src/tools/builtins/memory_search.py`) | 从 `context.scope_key` 读取; 不读取 principal_id |
| `ToolContext` (`src/tools/context.py`) | 6 字段, 含 `principal_id: str \| None` (P2-M3a 已新增) |
| `MemoryParityChecker` (`src/memory/parity.py`) | 比较 ledger vs workspace: content + scope_key + source + source_session_id |
| `PromptBuilder` (`src/agent/prompt_builder.py`) | `_load_daily_notes()` 从 workspace 文件读取 recent daily notes 注入 system prompt; 仅按 scope_key 过滤，不看 principal_id |
| `AgentLoop._fetch_memory_recall()` (`src/agent/agent.py:166`) | 自动 recall 直接调用 `MemorySearcher.search(scope_key=...)`; 不传递 principal_id |
| `execute_tool()` (`src/agent/tool_runner.py:17`) | 已含 `principal_id` 参数, 构造 `ToolContext` 时传入 (P2-M3a 已实现) |
| `AgentLoop._execute_tool()` (`src/agent/agent.py:252`) | 已含 `principal_id` 参数, 透传到 `execute_tool()` (P2-M3a 已实现) |
| `_run_single_tool()` / `_run_procedure_action()` (`src/agent/tool_concurrency.py`) | 已从 `state.principal_id` 读取并传递 (P2-M3a 已实现) |
| `RequestState` (`src/agent/message_flow.py`) | 已含 `principal_id: str \| None` 字段, 从 `identity.principal_id` 填充 (P2-M3a 已实现) |
| `scripts/restore.py:238` | `_reindex_memory_entries()` 调用 `indexer.reindex_all()` 无 ledger 参数，从 workspace 文件重建 |
| `SessionIdentity` (P2-M3a) | 新增 `principal_id: str \| None` (由 auth 层填充) |
| `principals` / `principal_bindings` (P2-M3a) | 身份与绑定模型已存在 |

## 2. 核心决策

### D1：Ledger schema 扩展 — `principal_id` + `visibility` 列

`memory_source_ledger` 新增两列：

```
principal_id  VARCHAR(36)  NULL   -- 逻辑关联 principals.id; 不设 DB FK (nullable for anonymous/legacy)
visibility    VARCHAR(32)  NOT NULL  DEFAULT 'private_to_principal'
```

语义：
- **`principal_id`**：写入该记忆的 authenticated principal。NULL = 匿名写入（no-auth mode 或 legacy 数据）。
- **`visibility`**：该记忆条目的可见性级别，写入时确定，后续可通过 ledger 追加事件修改。

V1 visibility 枚举：
| 值 | 语义 | V1 状态 |
|----|------|---------|
| `private_to_principal` | 仅写入者 principal 可见 | 默认值，活跃 |
| `shareable_summary` | 可发布的脱敏摘要 | reserved，写入接受但 read 行为与 private 相同 |
| `shared_in_space` | shared space 中的共享记忆 | reserved, deny-by-default（P2-M3c visibility policy hook 拦截） |

约束：
- `visibility` 值不在上述三者之内时，写入拒绝（`CHECK` 约束）。
- `principal_id` 不设 FK 约束（避免跨表锁；运行时已有 principal 验证）。
- `shared_in_space` 的写入在 V1 被 writer 层 fail-closed 拦截，不依赖 DB 约束。

### D2：`memory_entries` 检索索引扩展

`memory_entries` 新增两列，与 ledger 对齐：

```
principal_id  VARCHAR(36)  NULL
visibility    VARCHAR(32)  NOT NULL  DEFAULT 'private_to_principal'
```

新增索引：
- `idx_memory_entries_principal` on `(principal_id)`：支持按 principal 过滤。
- `idx_memory_entries_visibility` on `(visibility)`：支持按 visibility 过滤。

这两列由 indexer 从 ledger 或 writer 参数填充。reindex 从 ledger 读取时，直接复制 ledger 的 principal_id + visibility。

### D3：消费 M3a 已提供的 `ToolContext.principal_id`

> **P2-M3a 已实现**：`ToolContext.principal_id` (`context.py:27`)、`execute_tool(principal_id=)` (`tool_runner.py:25`)、`AgentLoop._execute_tool(principal_id=)` (`agent.py:252`)、`_run_single_tool` / `_run_procedure_action` (`tool_concurrency.py:225,255`) 已完成 principal 传播。`RequestState.principal_id` (`message_flow.py:122`) 已从 `identity.principal_id` 填充。

P2-M3b 不再修改这些接口；只在 memory tools (`memory_append` / `memory_search`) 中消费 `context.principal_id`，将其透传到 writer / searcher。

### D4：Memory 写入路径 — principal_id + visibility 透传

写入链路变更：

1. **MemoryAppendTool** 从 `context.principal_id` 读取 principal_id，传递给 writer。
2. **MemoryWriter.append_daily_note()** 新增 `principal_id` + `visibility` 参数。
3. **MemoryLedgerWriter.append()** 新增 `principal_id` + `visibility` 参数，写入 ledger 新列。
4. **MemoryIndexer.index_entry_direct()** 新增 `principal_id` + `visibility` 参数，写入 `memory_entries` 新列。
5. **Workspace projection** metadata 行增加 `visibility: <value>` 字段（始终渲染）。`principal: <id>` **仅在 `principal_id is not None` 时渲染；`principal_id` 为 NULL 时省略 `principal` 字段**。理由：D10 PromptBuilder 将任何 `principal:` 标记视为 principal-owned 并对 anonymous 隐藏；若 no-auth 写入渲染 `principal: None` 或 `principal: null`，会导致 anonymous/no-auth prompt 错误跳过该条目。D7 parity 比较依赖 `visibility` 始终存在。

Visibility 写入规则：
- V1 所有用户直接写入默认为 `private_to_principal`。
- `shareable_summary` 接受写入但不改变读取行为（reserved）。
- `shared_in_space` 在 writer 层 fail-closed 拦截：`MemoryWriter.append_daily_note()` 检查 visibility，若为 `shared_in_space` 则抛出 `MemoryWriteError("shared_in_space visibility not yet supported")`。

### D5：Memory 搜索路径 — principal + visibility 过滤

搜索 SQL 变更（`MemorySearcher._build_search_sql()`）：

```sql
WHERE scope_key = :scope_key
  AND search_vector @@ query
  AND (principal_id = :principal_id OR principal_id IS NULL)
  AND visibility IN ('private_to_principal', 'shareable_summary')
```

过滤规则：
- **有 principal_id 的请求**：只返回该 principal 写入的条目 + 匿名条目（legacy 兼容）。
- **无 principal_id 的请求（anonymous）**：只返回 `principal_id IS NULL` 的条目。
- **visibility 过滤**：V1 只允许 `private_to_principal` 和 `shareable_summary`。`shared_in_space` 永不返回（deny-by-default）。

这个策略是 fail-closed 的：
- 新增的 visibility 值默认不被搜索包含，必须显式加入白名单。
- 不同 principal 之间的记忆默认互不可见。

放弃方案：在 searcher 之外增加独立 visibility gateway — 过于复杂，V1 的 visibility 逻辑足够简单，直接在 SQL WHERE 中表达。

### D6：Reindex 从 DB ledger 重建

`MemoryIndexer` 新增 `reindex_from_ledger()` 方法，从 DB ledger current view 重建 `memory_entries`：

```python
async def reindex_from_ledger(
    self, ledger: MemoryLedgerWriter, *, scope_key: str | None = None,
) -> int:
    """Delete-reinsert memory_entries from ledger current view.

    Uses ledger 'append' events as truth source (ADR 0060).
    Replaces workspace-based reindex as primary path.
    """
```

实现：
1. 从 ledger 读取所有 `event_type='append'` 的条目（含 principal_id, visibility）。
2. 在 `memory_entries` 中 delete scope_key 对应的 `daily_note` 类型条目（或全部 daily_note 类型，若 scope_key is None）。
3. 重新插入，逐条生成 tsvector。
4. `curated` 类型（MEMORY.md）仍从 workspace 文件 reindex（curated memory 不进入 ledger）。

`reindex_all()` 行为变更：
- 若传入 `ledger` 参数：daily_note 从 ledger 重建，curated 从 workspace 文件。
- 若无 `ledger`：回退到现有 workspace-only 行为（向后兼容）。

CLI `reindex` 命令默认使用 ledger-based reindex。

### D7：Parity checker 扩展

`MemoryParityChecker` 扩展比较维度：

- `get_entries_for_parity()` 返回值增加 `principal_id` + `visibility` 字段。
- `check()` 对 matched entry_ids 增加 principal_id + visibility 比较。
- `MemoryIndexer._parse_entry_metadata()` 增加 **`principal` 和 `visibility`** 两个字段的解析（当前只解析 entry_id, source, scope, source_session_id）。缺失 `principal` → `None`；缺失 `visibility` → `"private_to_principal"`（legacy 默认值）。parity workspace 侧读取这两个字段与 ledger 比较。

这使 parity report 能检测 ledger 与 workspace 之间的 identity/visibility 不一致。若只解析 `principal` 不解析 `visibility`，`shareable_summary` 条目的 workspace 侧会 fallback 到 `private_to_principal`，导致与 ledger 的 visibility 比较始终不匹配。

### D8：Flush candidates 携带 principal_id

`ResolvedFlushCandidate` 需要携带 `principal_id`，使 compaction flush 产生的记忆条目能关联到原始 principal：

```python
@dataclass(frozen=True)
class ResolvedFlushCandidate:
    candidate_text: str
    confidence: float
    scope_key: str = "main"
    source_session_id: str | None = None
    principal_id: str | None = None  # P2-M3b
```

`MemoryWriter.process_flush_candidates()` 将 `candidate.principal_id` 传递给 `append_daily_note()`。

### D9：Backup/restore

Backup：`scripts/backup.py` 无需额外修改（`memory_source_ledger` 已在 P2-M2d 加入 TRUTH_TABLES，新列自动包含在 `pg_dump --table` 中）。

Restore：`scripts/restore.py` 必须使用 ledger-based reindex。当前 `_reindex_memory_entries()` 调用 `indexer.reindex_all()` 不带 `ledger` 参数，从 workspace 文件重建。这在 P2-M3b 之后是不充分的：restore 的 ledger 包含 principal_id + visibility，但 workspace projection 可能 stale 或缺失这些字段。

变更：
1. `_reindex_memory_entries()` 构造 `MemoryLedgerWriter(session_factory)` 并传递给 `indexer.reindex_all(ledger=ledger)`。
2. 先检查 ledger 是否有数据：有 → ledger-based reindex；空 → fallback 到 workspace-based（兼容 pre-M2d 备份）。
3. 测试覆盖：ledger-only entry（无对应 workspace 文件）能被正确重建；workspace stale/missing 不影响重建。

### D10：PromptBuilder daily notes — principal 过滤

**问题**：`PromptBuilder._load_daily_notes()` 从 workspace `memory/*.md` 文件直接读取 recent daily notes 注入 system prompt（`src/agent/prompt_builder.py:273-295`）。当前仅按 `scope_key` 过滤条目（`_filter_entries_by_scope()`），不看 `principal_id`。P2-M3b 保持 `scope_key = "main"`，因此 principal B 会在 prompt 中看到 principal A 的 daily notes — **绕过了 D5 的 search-level 隔离**。

**方案**：扩展 `_filter_entries_by_scope()` 为 `_filter_entries()`，同时按 scope_key + principal_id + visibility 过滤。

变更：
1. `PromptBuilder.build()` 新增 `principal_id: str | None = None` 参数，透传到 `_layer_workspace()` → `_load_daily_notes()` → `_filter_entries()`。**不改 `__init__()`**：`principal_id` 是 per-request runtime context，PromptBuilder 由 AgentLoop 长期持有，放入 `__init__()` 会造成跨 session stale state。
2. `_filter_entries()` 在现有 scope_key 匹配基础上增加 principal + visibility 匹配，策略必须与 D5 search WHERE 等价：
   - **principal 过滤**：metadata 行有 `principal: <id>` 时，仅匹配的 principal 可见。**anonymous 调用方 (`principal_id=None`) 不可见**（与 D5 的 `principal_id IS NULL` 语义一致：anonymous 只看无 principal 标记的 legacy 条目）。
   - metadata 行无 `principal` 字段（legacy 数据）→ 对所有 principal 和 anonymous 可见。
   - **visibility 过滤**：metadata 行有 `visibility:` 时，只允许 `private_to_principal` 和 `shareable_summary`。`shared_in_space` 和未知值一律跳过（deny-by-default，与 D5 一致）。无 `visibility` 标记（legacy）→ 视为 `private_to_principal`，通过。
3. `_build_system_prompt()` （`src/agent/message_flow.py:523`）透传 `principal_id` 到 `PromptBuilder.build()`。
4. `RequestState.principal_id` 已由 P2-M3a 新增；`_build_system_prompt()` 从已存在的 `state.principal_id` 透传到 `PromptBuilder.build()`。

放弃方案：在 authenticated multi-principal 模式下完全禁用 workspace daily notes 注入、改走 ledger/search — 改动过大，且 curated memory (MEMORY.md) 注入也需要重新设计；V1 的 principal 在 workspace 文件中已有 metadata 标记（D4 第 5 条），直接过滤是最小变更。

### D11：Automatic memory recall — principal 传播

**问题**：`AgentLoop._fetch_memory_recall()` 直接调用 `self._memory_searcher.search(query, scope_key=scope_key)`，不传递 `principal_id`。D5 的 searcher 变更后，`principal_id=None` 将被解释为 anonymous-only，authenticated 用户的 principal memory 不会进入自动 recall。

**方案**：`_fetch_memory_recall()` 接受并传递 `principal_id`。

变更：
1. `_fetch_memory_recall()` 签名新增 `principal_id: str | None = None`。
2. 调用 `self._memory_searcher.search(query, scope_key=scope_key, principal_id=principal_id)`。
3. **调用顺序修正**：当前 `_initialize_request_state()` 在 `message_flow.py:117` 先调用 `_fetch_memory_recall()`，`:122` 才提取 `principal_id`。**必须将 `principal_id = identity.principal_id if identity else None` 移到 `_fetch_memory_recall()` 调用之前**，否则只改签名但仍传 None。
4. `RequestState` 的 `principal_id` 字段（P2-M3a 已新增）在 `_apply_compaction_result()` 等重建 system prompt 的路径中也透传。

约束：自动 recall 与 `memory_search` tool 使用同一 `MemorySearcher.search()` 入口 + 同一 principal/visibility policy，不存在两条独立策略。

### D12：compaction flush + procedure publish flush — principal 全链路

> **P2-M3a 已实现（M3b 不再重复）**：`execute_tool(principal_id=)` 签名、`AgentLoop._execute_tool(principal_id=)` 签名、`_run_single_tool()` / `_run_procedure_action()` 从 `state.principal_id` 透传到 `loop._execute_tool()` 和 `ToolContext`。

**问题 B**：compaction flush 路径没有 principal 参数。当前调用链：
- `_apply_compaction()` → `try_compact()` → `_finalize_compaction_result()` → `_persist_flush_candidates()` (`compaction_flow.py:172`) → `loop._persist_flush_candidates()` (`agent.py:209`)
- 其中 `_persist_flush_candidates()` 只传 `session_id` + `scope_key`，构造的 `ResolvedFlushCandidate` 无 `principal_id`。

**问题 C**：procedure publish flush 路径同样缺失。当前调用链：
- `_run_procedure_action()` → `_handle_publish_flush()` (`tool_concurrency.py:304`) → `loop._persist_flush_candidates()` — 无 `principal_id`。

若不修正，authenticated session 的 flush memory 和 procedure result 会写成 `principal_id=NULL`（legacy），对其他 principal 可见。

**方案**：从 `RequestState.principal_id`（P2-M3a 已新增）向下透传到 flush 旁路。

变更（仅列 M3b 剩余工作）：
1. `_handle_publish_flush()` 传递 `state.principal_id` 给 `loop._persist_flush_candidates()`。
2. `compaction_flow.try_compact()` / `_finalize_compaction_result()` / `_persist_flush_candidates()` 全链路新增 `principal_id` 参数。
3. `message_flow._apply_compaction()` 调用 `try_compact()` 时传递 `state.principal_id`。
4. `AgentLoop._persist_flush_candidates()` 签名新增 `principal_id`，填充到 `ResolvedFlushCandidate.principal_id`。

### D13：Incremental index 由 ledger_written 驱动

**问题**：当前 `MemoryWriter.append_daily_note()` 仅在 `projection_written` 为 True 时触发 `_try_incremental_index()`（`src/memory/writer.py:155-166`）。P2-M3b 保持 workspace projection 为 best-effort，projection 写失败或 daily note 超限时，ledger 写入成功但 `memory_entries` 中无对应条目 — search 立即不可见。

**方案**：增量索引由 `ledger_written` 驱动，不再依赖 `projection_written`。

变更：
1. 在 ledger-wired 模式下，`_try_incremental_index()` 在 `ledger_written` 为 True 时无条件调用，无论 projection 是否成功。
2. `_try_incremental_index()` 的 `source_path` 参数允许 None（ledger-only entry 无文件路径）。
3. `projection_path` 仍由 `projection_written` 决定（与 MemoryWriteResult 语义一致）。
4. 测试覆盖：projection 失败（size limit 或 OSError）但 ledger 写入成功时，`memory_entries` 中仍有对应条目可搜索。

### D14：Clean-start 前提 — 不做 workspace-only legacy import

P2-M3b 的 ledger-based reindex 以 P2-M2d 之后的 DB ledger 为唯一 daily_note truth。项目尚未上线，不存在需要迁移的生产 workspace-only 历史数据。

明确约束：
- **Clean-start**：P2-M3b 不做 one-time legacy workspace import / backfill。workspace-only daily notes（P2-M2d 之前的本地开发文件）不参与 P2-M3b 验收。
- **"Legacy 兼容"定义收窄**：仅指 DB 表中 `principal_id IS NULL` 的匿名/历史 ledger 行或 `memory_entries` 行。不承诺导入 workspace-only 历史 Markdown。
- **Restore fallback 只服务空 ledger**：如果 ledger 有数据，restore/reindex 只信 ledger，不 merge workspace daily notes。空 ledger（pre-M2d 备份恢复）时 fallback 到 workspace reindex。
- **Parity 预期**：本地开发环境中 `only_in_workspace` 报告视为 dev artifact，不作为数据丢失。
- **验收前置条件**：Gate 2 在 clean workspace（或清理旧 local daily note artifact 后）验收。

放弃方案：one-time backfill CLI — 项目未上线，没有生产 legacy 数据需要迁移；backfill 增加实现复杂度但无实际消费者。若未来产品上线后需要迁移历史数据，可作为独立运维任务补充。

## 3. 不做的事

- 不做 retrieval quality 增强（lexical normalization, vector search, hybrid ranking → P2-M3c）
- 不做 `can_read` / `can_write` visibility policy hook 函数（→ P2-M3c）
- 不做 `shared_in_space` 的写入或读取激活（V1 deny-by-default）
- 不做 `shareable_summary` 的多方确认流程
- 不做 membership 表或 shared_space_id 规范化
- 不做 federation protocol
- 不做 scope_key 的 principal-aware 语义变更（scope_key 仍为 `"main"`；principal 过滤是独立维度）
- 不做 memory correction / retraction / contested event types（ledger V1 只有 `append`）
- 不删除 workspace-based `reindex_all()` 路径（保留为空 ledger fallback）
- 不做 workspace-only legacy daily notes 的 one-time import / backfill（D14 clean-start 前提；项目未上线）

## 4. 实现切片

### Slice A：DB Schema — ledger + memory_entries 新列

**新增文件**：
- `alembic/versions/<hash>_add_principal_visibility_to_memory.py`

**修改文件**：
- `src/memory/models.py`：`MemoryEntry` 增加 `principal_id` (VARCHAR(36), nullable) + `visibility` (VARCHAR(32), NOT NULL, default='private_to_principal') 列
- `src/session/database.py`：`ensure_schema()` idempotent DDL 增加两列

**`memory_source_ledger` 新增列**：
| 列 | 类型 | 约束 |
|----|------|------|
| `principal_id` | VARCHAR(36) | NULL |
| `visibility` | VARCHAR(32) | NOT NULL, DEFAULT 'private_to_principal', CHECK IN ('private_to_principal', 'shareable_summary', 'shared_in_space') |

**`memory_entries` 新增列**：
| 列 | 类型 | 约束 |
|----|------|------|
| `principal_id` | VARCHAR(36) | NULL |
| `visibility` | VARCHAR(32) | NOT NULL, DEFAULT 'private_to_principal' |

**新增索引**：
- `idx_memory_entries_principal` on `memory_entries(principal_id)`
- `idx_memory_entries_visibility` on `memory_entries(visibility)`
- `idx_memory_source_ledger_principal` on `memory_source_ledger(principal_id)`

**测试**：
- `tests/test_memory_schema_m3b.py`：migration up/down、新列默认值、CHECK 约束、索引存在性

### Slice B：Writer 路径 — principal_id + visibility 透传 + 增量索引解耦

**修改文件**：
- `src/memory/ledger.py`：`MemoryLedgerWriter.append()` 新增 `principal_id: str | None = None` + `visibility: str = "private_to_principal"` 参数；INSERT SQL 增加两列
- `src/memory/writer.py`：`MemoryWriter.append_daily_note()` 新增 `principal_id` + `visibility` 参数；`shared_in_space` fail-closed 检查；传递给 ledger + indexer
- `src/memory/writer.py`：**增量索引改由 `ledger_written` 驱动（D13）**；ledger-wired 模式下，`_try_incremental_index()` 在 ledger 写入成功后无条件调用，不再依赖 `projection_written`
- `src/memory/writer.py`：workspace projection metadata 行追加 `visibility: <value>`（始终渲染）+ `principal: <id>`（**仅 `principal_id is not None` 时渲染，NULL 时省略**——D10 PromptBuilder 和 Slice F parity 依赖此格式；NULL principal 渲染为字面量会被 PromptBuilder 误判为 principal-owned）
- `src/memory/contracts.py`：`ResolvedFlushCandidate` 增加 `principal_id: str | None = None`
- `src/memory/writer.py`：`process_flush_candidates()` 透传 `candidate.principal_id`

**`MemoryLedgerWriter.append()` 签名变更**：
```python
async def append(
    self,
    *,
    entry_id: str,
    content: str,
    scope_key: str = "main",
    source: str = "user",
    source_session_id: str | None = None,
    metadata: dict | None = None,
    principal_id: str | None = None,        # P2-M3b
    visibility: str = "private_to_principal",  # P2-M3b
) -> bool:
```

**`MemoryWriter.append_daily_note()` 签名变更**：
```python
async def append_daily_note(
    self,
    text: str,
    *,
    scope_key: str = "main",
    source: str = "user",
    source_session_id: str | None = None,
    target_date: date | None = None,
    principal_id: str | None = None,        # P2-M3b
    visibility: str = "private_to_principal",  # P2-M3b
) -> MemoryWriteResult:
```

**Visibility fail-closed 检查**（在 `append_daily_note` 入口）：
```python
_ALLOWED_VISIBILITY = {"private_to_principal", "shareable_summary", "shared_in_space"}
_WRITABLE_VISIBILITY = {"private_to_principal", "shareable_summary"}

if visibility not in _ALLOWED_VISIBILITY:
    raise MemoryWriteError(f"Unknown visibility: {visibility}")
if visibility not in _WRITABLE_VISIBILITY:
    raise MemoryWriteError(f"Visibility '{visibility}' is not yet writable")
```

**测试**：
- `tests/test_memory_writer_m3b.py`：principal_id 透传到 ledger、visibility 默认值、shared_in_space 被拒绝、flush candidate 透传 principal_id、**projection 失败但 ledger 成功时 memory_entries 仍有条目（D13）**
- `tests/test_memory_ledger_m3b.py`：append 含 principal_id + visibility、get_entries_for_parity 返回新字段

### Slice C：Search 路径 — principal + visibility 过滤

**修改文件**：
- `src/memory/searcher.py`：`MemorySearcher.search()` 新增 `principal_id: str | None = None` 参数；`_build_search_sql()` 增加 principal + visibility WHERE 条件
- `src/memory/searcher.py`：`MemorySearchResult` 增加 `principal_id: str | None` + `visibility: str` 字段
- `src/memory/indexer.py`：`MemoryIndexer.index_entry_direct()` 新增 `principal_id` + `visibility` 参数

**Search SQL 变更**：

```python
@staticmethod
def _build_search_sql(
    query: str, *, scope_key: str, limit: int,
    min_score: float, source_types: list[str] | None,
    principal_id: str | None,  # P2-M3b
) -> tuple[str, dict]:
```

新增 WHERE 条件：
```sql
-- Principal filtering: own entries + anonymous legacy entries
AND (principal_id = :principal_id OR principal_id IS NULL)
-- Visibility filtering: deny shared_in_space
AND visibility IN ('private_to_principal', 'shareable_summary')
```

当 `principal_id is None`（anonymous）时：
```sql
AND principal_id IS NULL
AND visibility IN ('private_to_principal', 'shareable_summary')
```

**测试**：
- `tests/test_memory_searcher_m3b.py`：principal 过滤（own + legacy）、anonymous 只看 NULL、shared_in_space 不可见、cross-principal 隔离

### Slice D：Runtime 集成 — ToolContext + memory tools + PromptBuilder + auto recall

本 slice 覆盖所有运行时 memory 消费路径的 principal 传播，确保无旁路泄漏。

**P2-M3a 已提供（M3b 不再修改）**：
- `src/tools/context.py`：`ToolContext.principal_id` 已存在
- `src/agent/tool_runner.py`：`execute_tool(principal_id=)` 已含参数并构造 `ToolContext` 时传入
- `src/agent/agent.py`：`_execute_tool(principal_id=)` 已透传到 `execute_tool()`
- `src/agent/tool_concurrency.py`：`_run_single_tool()` / `_run_procedure_action()` 已从 `state.principal_id` 传递
- `src/agent/message_flow.py`：`RequestState.principal_id` 已存在，`_initialize_request_state()` 已从 `identity.principal_id` 填充

**M3b 剩余修改文件**：
- `src/tools/builtins/memory_append.py`：从 `context.principal_id` 读取，传递给 writer
- `src/tools/builtins/memory_search.py`：从 `context.principal_id` 读取，传递给 searcher
- `src/agent/message_flow.py`：
  - **调用顺序修正**：把 `principal_id` 提取移到 `_fetch_memory_recall()` 之前（D11）
  - `_build_system_prompt()` 透传 `principal_id` 到 `PromptBuilder.build()`
  - `_apply_compaction_result()` 重建 system prompt 时同步透传 `principal_id`
  - `_fetch_memory_recall()` 调用时传递 `principal_id`
- `src/agent/agent.py`：
  - `_fetch_memory_recall()` 签名新增 `principal_id: str | None = None`，传递给 `searcher.search()`（D11）
  - `_persist_flush_candidates()` 新增 `principal_id` 参数，填充到 `ResolvedFlushCandidate.principal_id`
- `src/agent/compaction_flow.py`：
  - `_persist_flush_candidates()` 新增 `principal_id` 参数，从调用方透传到 `loop._persist_flush_candidates()`
  - `_finalize_compaction_result()` 新增 `principal_id` 参数，透传到 `_persist_flush_candidates()`
  - `try_compact()` 新增 `principal_id` 参数，透传到 `_finalize_compaction_result()`
- `src/agent/tool_concurrency.py`：
  - `_handle_publish_flush()` 传递 `state.principal_id` 给 `loop._persist_flush_candidates()`
- `src/agent/prompt_builder.py`：
  - `build()` 新增 `principal_id: str | None = None` 参数（per-request，不改 `__init__`），透传到 `_layer_workspace()`（D10）
  - `_layer_workspace()` / `_load_daily_notes()` 透传 `principal_id`
  - `_filter_entries_by_scope()` 重命名为 `_filter_entries()`，增加 principal 匹配（D10）

**PromptBuilder._filter_entries() principal + visibility 过滤逻辑**：

策略必须与 D5 的 `MemorySearcher.search()` WHERE 条件等价：
- **有 principal 标记的条目**：仅匹配 principal 可见。anonymous 调用方 (`principal_id=None`) 不可见。
- **无 principal 标记的条目（legacy）**：对所有 principal 和 anonymous 可见。
- **visibility 过滤**：只允许 `private_to_principal` 和 `shareable_summary`（或无标记 = legacy，视为 `private_to_principal`）。`shared_in_space` 和未知值一律跳过（deny-by-default）。

```python
_PROMPT_ALLOWED_VISIBILITY = {"private_to_principal", "shareable_summary"}

@staticmethod
def _filter_entries(content: str, scope_key: str, principal_id: str | None = None) -> str:
    entries = re.split(r"^---$", content, flags=re.MULTILINE)
    filtered: list[str] = []
    for entry in entries:
        stripped = entry.strip()
        if not stripped:
            continue
        first_line = stripped.split("\n", 1)[0]
        # scope check (existing)
        scope_match = re.search(r"scope:\s*(\S+)", first_line)
        if scope_match:
            if scope_match.group(1).rstrip(",)") != scope_key:
                continue
        elif scope_key != "main":
            continue
        # visibility check (P2-M3b) — must match D5 deny-by-default
        vis_match = re.search(r"visibility:\s*(\S+)", first_line)
        if vis_match:
            entry_vis = vis_match.group(1).rstrip(",)")
            if entry_vis not in _PROMPT_ALLOWED_VISIBILITY:
                continue  # shared_in_space or unknown → deny
        # no visibility metadata → legacy, treated as private_to_principal (allowed)
        # principal check (P2-M3b) — must match D5 search WHERE
        principal_match = re.search(r"principal:\s*(\S+)", first_line)
        if principal_match:
            entry_principal = principal_match.group(1).rstrip(",)")
            if principal_id is None:
                continue  # anonymous caller cannot see principal-tagged entries
            if entry_principal != principal_id:
                continue  # cross-principal: skip
        # no principal metadata → legacy entry, visible to all (D5 consistency)
        filtered.append(stripped)
    return "\n\n".join(filtered)
```

**MemoryAppendTool 变更**：
```python
async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
    scope_key = context.scope_key if context else "main"
    source_session_id = context.session_id if context else None
    principal_id = context.principal_id if context else None  # P2-M3b
    result = await self._writer.append_daily_note(
        text=text.strip(),
        scope_key=scope_key,
        source="user",
        source_session_id=source_session_id,
        principal_id=principal_id,  # P2-M3b
    )
```

**MemorySearchTool 变更**：
```python
async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
    scope_key = context.scope_key if context else "main"
    principal_id = context.principal_id if context else None  # P2-M3b
    results = await self._searcher.search(
        query=query.strip(),
        scope_key=scope_key,
        limit=limit,
        principal_id=principal_id,  # P2-M3b
    )
```

**关键约束 — 三路等价性**：

所有 memory 消费路径必须执行与 D5 `MemorySearcher.search()` WHERE 等价的 principal + visibility 策略：

| 路径 | principal 规则 | visibility 规则 |
|------|---------------|----------------|
| `MemorySearcher.search()` | `principal_id = :pid OR principal_id IS NULL`；anonymous 只看 NULL | `IN ('private_to_principal', 'shareable_summary')` |
| `PromptBuilder._filter_entries()` | 有 `principal:` 标记 → 仅匹配 principal 可见、anonymous 不可见；无标记 → 全可见 | 有 `visibility:` 标记 → 仅允许集合内；无标记 → legacy，视为 private_to_principal |
| `_fetch_memory_recall()` | 透传 `principal_id` 到同一 `searcher.search()` | 同上 |

三路不等价即为 bug，Gate 1 测试必须覆盖。

**测试**：
- `tests/test_memory_tools_m3b.py`（含轻量 ToolContext 回归）：memory_append 从 context.principal_id 写入 writer、memory_search 从 context.principal_id 传给 searcher（ToolContext 字段本身已由 M3a 引入并通过现有测试）
- `tests/test_prompt_builder_m3b.py`：
  - principal 过滤：own 可见、cross-principal 不可见
  - **anonymous 不看 principal-tagged 条目、只看 legacy 无标记条目**
  - **visibility 过滤：`shared_in_space` 条目被跳过、未知 visibility 被跳过、无 visibility 标记（legacy）通过**
  - legacy 兼容：无 principal + 无 visibility 标记的条目对所有调用方可见
- `tests/test_memory_recall_m3b.py`：auto recall 传递 principal_id、authenticated 用户能 recall 自己的 memory、不 recall cross-principal memory
- `tests/test_flush_principal_m3b.py`：compaction flush + procedure publish flush 写入携带正确 principal_id（非 NULL）

### Slice E：Reindex from ledger + restore 修正

**修改文件**：
- `src/memory/indexer.py`：新增 `reindex_from_ledger()` 方法
- `src/memory/indexer.py`：`reindex_all()` 增加 `ledger` 可选参数，优先使用 ledger-based reindex
- `src/memory/ledger.py`：新增 `get_current_view()` 方法，返回全部 append 事件的完整字段（含 principal_id, visibility）
- `src/backend/cli.py`：`reindex` 命令默认使用 ledger-based 路径
- `scripts/restore.py`：`_reindex_memory_entries()` 构造 `MemoryLedgerWriter` 并传递给 `indexer.reindex_all(ledger=ledger)`（D9）

**`reindex_from_ledger()` 实现**：
```python
async def reindex_from_ledger(
    self,
    ledger: MemoryLedgerWriter,
    *,
    scope_key: str | None = None,
) -> int:
    """Rebuild memory_entries from ledger current view (ADR 0060)."""
    entries = await ledger.get_current_view(scope_key=scope_key)

    async with self._db_factory() as db:
        # Delete daily_note entries (curated entries preserved)
        del_sql = delete(MemoryEntry).where(
            MemoryEntry.source_type == "daily_note"
        )
        if scope_key is not None:
            del_sql = del_sql.where(MemoryEntry.scope_key == scope_key)
        await db.execute(del_sql)

        for e in entries:
            entry = MemoryEntry(
                entry_id=e["entry_id"],
                scope_key=e["scope_key"],
                source_type="daily_note",
                source_path=None,  # ledger entries have no file path
                source_date=e["created_at"].date() if e.get("created_at") else None,
                source_session_id=e.get("source_session_id"),
                principal_id=e.get("principal_id"),
                visibility=e.get("visibility", "private_to_principal"),
                title="",
                content=e["content"],
                tags=[],
                confidence=None,
            )
            db.add(entry)

        await db.commit()

    return len(entries)
```

**`MemoryLedgerWriter.get_current_view()`**：
```python
async def get_current_view(
    self, *, scope_key: str | None = None,
) -> list[dict]:
    """Return all append events with full fields for reindex.

    Returns list of dicts with: entry_id, content, scope_key, source,
    source_session_id, principal_id, visibility, created_at.
    """
```

**`reindex_all()` 签名变更**：
```python
async def reindex_all(
    self,
    *,
    scope_key: str = "main",
    ledger: MemoryLedgerWriter | None = None,  # P2-M3b
) -> int:
```

行为：
- `ledger` 提供 → daily_note 从 ledger 重建（调用 `reindex_from_ledger`），curated 从 workspace 文件。
- `ledger` 为 None → 回退到现有 workspace-only 行为。

**Restore 变更**（`scripts/restore.py:_reindex_memory_entries()`）：
```python
async def _reindex_memory_entries(
    session_factory: object,
    memory_settings: object,
    *,
    results: list[tuple[str, str]],
) -> None:
    from src.memory.indexer import MemoryIndexer
    from src.memory.ledger import MemoryLedgerWriter

    indexer = MemoryIndexer(session_factory, memory_settings)
    ledger = MemoryLedgerWriter(session_factory)

    # Prefer ledger-based reindex; fallback to workspace if ledger is empty
    ledger_count = await ledger.count()
    if ledger_count > 0:
        entry_count = await indexer.reindex_all(ledger=ledger)
        results.append(("7. reindex_all", f"OK ({entry_count} entries, ledger-based)"))
    else:
        entry_count = await indexer.reindex_all()
        results.append(("7. reindex_all", f"OK ({entry_count} entries, workspace-based fallback)"))
    logger.info("restore_step_7_done", entries=entry_count)
```

**测试**：
- `tests/test_memory_reindex_m3b.py`：ledger-based reindex 正确重建含 principal_id + visibility 的条目、curated 仍从文件、fallback 行为不变、scope_key 过滤
- `tests/test_restore_m3b.py`：restore reindex 使用 ledger-based 路径、ledger-only entry（无 workspace 文件）被重建、空 ledger fallback 到 workspace

### Slice F：Parity checker + doctor 扩展

**修改文件**：
- `src/memory/parity.py`：比较维度增加 principal_id + visibility
- `src/memory/ledger.py`：`get_entries_for_parity()` 返回值增加 principal_id + visibility
- `src/memory/indexer.py`：`_parse_entry_metadata()` 增加 `principal` + `visibility` 两字段解析（缺失 principal → None；缺失 visibility → `"private_to_principal"`）
- `src/infra/doctor.py`：D5 check（若存在）增加 principal_id + visibility parity 说明

**Parity 比较扩展**：
```python
# 新增 metadata 比较维度
if le.get("principal_id") != we.get("principal_id"):
    metadata_mismatch.append(eid)
if le.get("visibility") != we.get("visibility", "private_to_principal"):
    metadata_mismatch.append(eid)
```

**Workspace projection 元数据行格式**：
```
# authenticated write (principal_id not None):
[HH:MM] (entry_id: xxx, source: user, scope: main, principal: xxx, visibility: private_to_principal)

# anonymous / no-auth write (principal_id is None — principal field omitted):
[HH:MM] (entry_id: xxx, source: user, scope: main, visibility: private_to_principal)
```

**测试**：
- `tests/test_memory_parity_m3b.py`：principal_id + visibility mismatch 检测、缺失 principal 的 legacy 兼容、`_parse_entry_metadata` 返回 principal + visibility 字段、anonymous 写入（无 principal 标记）parity 正确

### Slice G：Data model 文档更新

**修改文件**：
- `design_docs/data_models/postgresql/memory_source_ledger.md`：新增 `principal_id` + `visibility` 列说明、CHECK 约束、新索引
- `design_docs/data_models/postgresql/memory_entries.md`：新增 `principal_id` + `visibility` 列说明、新索引
- `design_docs/data_models/postgresql/index.md`：更新表描述反映 P2-M3b 变更

## 5. 实现顺序

```
Slice A (DB schema)
  ├─→ Slice B (writer path + incremental index decouple)
  │     ├─→ Slice D (runtime integration: tools + prompt + recall + flush + procedure) — depends on B + C
  │     └─→ Slice F (parity + doctor) — depends on B
  ├─→ Slice C (search path) — depends on A
  │     └─→ Slice D (runtime integration) — depends on B + C
  ├─→ Slice E (reindex from ledger + restore) — depends on A
  └─→ Slice G (data model docs) — depends on A, can parallelize with B-F
```

建议分 3 个 gate：
- **Gate 0**：Slice A + B — schema + writer 基础 + 增量索引解耦（ledger 可接收 principal_id + visibility，search index 不再依赖 projection 成功）
- **Gate 1**：Slice C + D — search 过滤 + **全部运行时消费路径** principal + visibility 传播（tool context、PromptBuilder daily notes、auto recall、tool_runner、compaction flush、procedure publish flush — 端到端 principal-aware memory，无旁路泄漏）
- **Gate 2**：Slice E + F + G — ledger-based reindex + restore 修正 + parity 扩展 + data model 文档（运维完备性；clean workspace 验收前置条件）

## 6. 验收标准

### 功能验收

1. **Memory 写入携带 principal**：authenticated 用户写入的记忆条目在 ledger 和 memory_entries 中携带正确的 principal_id。
2. **Visibility 默认值**：所有新写入条目的 visibility 默认为 `private_to_principal`。
3. **Principal 隔离**：principal A 的记忆在 principal B 的搜索中不可见。
4. **匿名兼容**：no-auth mode 下，行为与 P2-M3a 完全相同（principal_id = NULL，所有匿名条目互可见）。
5. **Legacy 兼容**：DB 表中 `principal_id = NULL` 的匿名/历史行对所有 principal 可见。（仅指 DB 内行，不承诺导入 workspace-only 历史 Markdown — D14 clean-start 前提。）
6. **shared_in_space 拒绝**：尝试以 `shared_in_space` visibility 写入时，writer 返回明确错误。
7. **shared_in_space 不可搜索**：即使数据库中存在 `shared_in_space` 条目，search 也不返回。
8. **Ledger-based reindex**：`reindex_from_ledger()` 能从 ledger current view 完整重建 `memory_entries`（含 principal_id + visibility），重建后 search 结果与重建前一致。
9. **Curated 不受影响**：MEMORY.md 的 curated 条目 reindex 行为不变。
10. **Parity checker 覆盖新维度**：principal_id 或 visibility 不一致时，parity report 标记为 metadata_mismatch。
11. **PromptBuilder 不注入 cross-principal daily notes**：authenticated principal A 的 system prompt 不包含 principal B 写入的 daily notes 条目；anonymous prompt 不包含 principal-tagged 条目；`shared_in_space` / unknown visibility 条目不注入任何 prompt；legacy（无 principal + 无 visibility 标记）条目对所有 principal 可见。
12. **Auto recall 与 memory_search 使用同一 principal/visibility policy**：`_fetch_memory_recall()` 和 `MemorySearchTool` 调用同一 `MemorySearcher.search()` 入口、传递同一 `principal_id`，不存在两条独立策略。
13. **Restore 使用 ledger-based reindex**：restore 后 `memory_entries` 从 ledger 重建，含 principal_id + visibility；ledger-only entry（无 workspace 文件）能被正确重建。
14. **Incremental index 不依赖 projection**：ledger 写入成功但 projection 失败时，`memory_entries` 中仍有对应条目可搜索。

### 不变性验收

15. **现有测试全绿**：不破坏 P2-M3a 及之前的所有测试。
16. **No-auth mode 零影响**：不设置 `AUTH_PASSWORD_HASH` 时，memory 读写行为与 P2-M2d 完全一致。
17. **Scope_key 语义不变**：scope_key 过滤逻辑不变；principal_id 是独立的访问控制维度。
18. **Workspace projection 保持可用**：workspace `memory/*.md` 文件仍然生成，格式向后兼容（新字段追加不破坏旧 parser）。

### 验收前置条件

19. **Clean workspace**：Gate 2 验收在 clean workspace（或清理旧 local daily note artifact 后）执行。本地开发环境中 workspace-only daily notes（P2-M2d 之前的残留）产生的 `only_in_workspace` parity 报告视为 dev artifact，不作为数据丢失。

### 测试覆盖

20. 新增测试文件（预估）：9-11 个（含 prompt builder、auto recall、restore、flush principal）。
21. 新增测试数量（预估）：55-75 个。

## 7. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| DB 内 principal_id = NULL 行搜索行为变化 | 中 | 搜索条件 `principal_id = :pid OR principal_id IS NULL` 保证 DB 内 legacy 行对所有 principal 可见；workspace-only daily notes 不在 D14 clean-start 承诺范围内 |
| Ledger-based reindex 丢失 curated 条目 | 中 | curated 和 daily_note 分别处理；reindex_from_ledger 只操作 daily_note 类型 |
| PromptBuilder daily notes 过滤依赖 workspace metadata 准确性 | 中 | 写入路径（D4）已保证新 projection 携带 `principal:` 字段；legacy 无标记条目 fallback 为全可见（与 search 策略一致） |
| auto recall / memory_search / PromptBuilder 三路策略不一致 | 高 | D11 约束：recall + tool 使用同一 searcher 入口；D10 的 PromptBuilder 过滤逻辑与 D5 legacy 兼容策略一致；Gate 1 测试覆盖三路一致性 |
| reindex_from_ledger 大量条目时内存压力 | 低 | V1 数据量小；后续可引入分批处理 |
| visibility CHECK 约束阻止未来新增枚举值 | 低 | migration 时 ALTER CHECK 即可；V1 只有 3 个值 |
| Memory tools 开始消费 context.principal_id 影响 memory_append / memory_search 测试 | 低 | ToolContext 字段已由 M3a 引入并通过现有测试；M3b 只新增 writer/searcher 传播断言 |
| workspace projection 新增 principal 字段导致旧 parser 报错 | 低 | `_parse_entry_metadata` 按 regex 提取，未知字段被忽略 |
| restore 空 ledger 时 fallback 到 workspace 不含 principal | 低 | 只发生在 pre-M2d 备份恢复；此时 principal 语义本就不存在，行为正确 |
| authenticated flush/procedure 写成 principal_id=NULL | 高 | D12 要求 RequestState.principal_id 全链路透传到 compaction_flow + tool_concurrency + procedure ToolContext；Gate 1 测试覆盖 |

## 8. 与 P2-M3a / P2-M3c 的交接

### 从 P2-M3a 接收

- `principals` + `principal_bindings` 表
- `SessionIdentity.principal_id` 已正确传播到 dispatch / agent loop
- `ToolContext.principal_id` + `execute_tool(principal_id=)` + `_run_single_tool` / `_run_procedure_action` principal 传播已就绪
- `RequestState.principal_id` 已从 `identity.principal_id` 填充
- `AuthSettings` 判断 no-auth mode
- M3b 不直接调用 `PrincipalStore`；身份解析由 M3a gateway 层完成，M3b 只消费 `SessionIdentity.principal_id` / `ToolContext.principal_id`

### 交付给 P2-M3c

**Schema & write path（M3b 拥有）**：
- `memory_source_ledger` 携带 `principal_id` + `visibility` 列
- `memory_entries` 携带 `principal_id` + `visibility` 列（从 ledger 传播）
- ledger writer 写入 `principal_id` + `visibility`；indexer 传播到 `memory_entries`
- `shared_in_space` 在 writer 层 fail-closed（直接拒绝，不走 policy hook）

**Read path 基础过滤（M3b 拥有，D5/Gate 1/验收 #3 #7 #11 要求）**：
- `MemorySearcher.search()` 已实现 visibility allowlist filter：`AND visibility IN ('private_to_principal', 'shareable_summary')`。`shared_in_space` 和未知 visibility 在 search 中 deny-by-default。
- `MemorySearcher.search()` 已实现 principal 基础过滤：`AND (principal_id = :pid OR principal_id IS NULL)`。cross-principal 不可见。
- `PromptBuilder._filter_entries()` 已实现等价的 principal + visibility 过滤（D10）。
- 其他读取路径（`MemorySearchTool`、`AgentLoop._fetch_memory_recall()`）通过 ToolContext / 参数传递 `principal_id`，走同一 search / filter 策略。写入路径（compaction flush、procedure publish flush、procedure ToolContext）通过同一 principal context 写入；读取时再由 search / filter 策略生效。
- 这些是 M3b 的交付物，不依赖 M3c。

**P2-M3c 在此基础上新增**（M3b 不实现）：
- `can_read(context, memory_entry)` / `can_write(context, proposed_entry)` → allow/deny + reason：统一 visibility policy hook 函数，替代当前分散在 searcher SQL / writer 检查 / PromptBuilder regex 中的硬编码逻辑
- 可解释的 audit reason（`shared_space_policy_not_implemented` / `membership_unavailable` / `confirmation_missing`）及 `memory_search_filtered` / `visibility_policy_denied` audit trail
- 若未来决定激活 `shared_in_space` 读写，由 M3c 或后续阶段在 policy hook 框架下实现，而非修改 M3b 的 allowlist
- retrieval quality regression（已知 miss case）

**Reindex + CLI（M3b 拥有）**：
- `reindex_from_ledger()` 从 ledger 重建 `memory_entries`（含 principal_id + visibility 传播）
- `reindex_all(ledger=...)` 优先使用 ledger-based path
- CLI `reindex` 命令默认使用 ledger-based reindex（D6/Slice E）
- restore 默认使用 ledger-based reindex（D9/Slice E）

**M3c 在此基础上**：
- `can_read()` / `can_write()` visibility policy hook 及集成
- CJK query normalization + `search_text` 列 + reindex 含 CJK 分词增强
- retrieval quality regression（已知 miss case）
