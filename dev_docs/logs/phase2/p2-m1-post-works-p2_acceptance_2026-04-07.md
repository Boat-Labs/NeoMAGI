---
doc_id: 019d675e-8ca3-7523-8e46-867692381be6
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-07T11:55:42+02:00
---
# P2-M1 Post Works P2: Tool Concurrency Metadata — 验收通过

- Date: 2026-04-07
- Plan: `dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06.md`
- Status: **accepted**

## 交付摘要

1 commit, 9 文件变更 (+980 / -92), 1553 tests passed (含 37 新增).

| Commit | 内容 |
|--------|------|
| `ab51a69` | feat(tools): implement tool concurrency metadata and parallel execution |

## 计划验收项对照

| 验收项 | 状态 |
|--------|------|
| V1 双标记工具 (current_time + memory_search) 同 turn 内并行执行 | pass |
| 写入型工具形成 barrier | pass |
| 未声明元数据的工具继续串行 (fail-closed) | pass |
| transcript 中 tool message 顺序 deterministic | pass |
| ToolCallInfo / ToolDenied event 顺序 deterministic | pass |
| state.accumulated_failure_signals 不受 task 完成顺序影响 | pass |
| 现有 guardrail 拒绝语义不回归 | pass |
| 并发组日志可通过 group_index 与同 iteration 内 group 关联 | pass |

## Review Findings 及修复

共 2 轮 review：

**Round 1** — 3 findings:

1. **[P1] message_flow.py 超 800 行硬门禁** — 将 grouping/executor/outcome 拆分到 `src/agent/tool_concurrency.py` (274 行); message_flow.py 降至 710 行
2. **[P1] ToolCallInfo 在执行后才 yield** — 重构 `_handle_tool_calls` 为三阶段: yield ToolCallInfo → 执行 → emit ToolDenied + transcript; 新增 `TestHandleToolCallsEventTiming` 4 cases 验证
3. **[P3] serial_barrier_tool 日志 tool_index 使用 group_index 而非原始索引** — `_ExecutionGroup` 新增 `start_index` 字段; barrier 和 parallel group 日志均使用 `start_index`

**Round 2** — 0 findings, 验收通过.

## 关键实现

### Slice A: Metadata

- `BaseTool` 新增 `is_read_only` / `is_concurrency_safe` 两个 `@property`，默认 `False` (fail-closed)
- V1 标注: `current_time`, `memory_search`, `soul_status` → `True + True`
- `read_file` → `is_read_only=True`, `is_concurrency_safe=False` (同步 I/O)
- `memory_append`, `soul_propose`, `soul_rollback` → 保持默认

### Slice B: Group Builder (`tool_concurrency.py`)

- `_build_execution_groups()`: 按连续可并发工具切分 `_ExecutionGroup`，barrier 单独成组
- `_is_parallel_eligible()`: 仅 `is_read_only and is_concurrency_safe` 为 True
- `_ExecutionGroup.start_index`: 追踪原始 tool call 索引

### Slice C: Parallel Executor (`tool_concurrency.py`)

- `_execute_group()` / `_execute_parallel()`: `asyncio.TaskGroup` bounded 并行 (max 3)
- `_run_single_tool()`: 纯执行，返回 `_ToolOutcome`，无副作用
- `_mode_denial()`: 从 message_flow 迁入，解耦签名

### Slice D: Observability (`tool_concurrency.py`)

- `tool_parallel_group_started/finished`: 组级日志 (session_id, iteration, group_index, group_size, max_concurrency, tool_names)
- `serial_barrier_tool`: barrier 日志 (tool_index, tool_name, reason)

### message_flow 编排 (`message_flow.py`)

- `_handle_tool_calls` 三阶段: Phase 1 yield ToolCallInfo → Phase 2 execute group → Phase 3 emit ToolDenied + merge signals + append transcript

## Evidence

- Commit: `ab51a69`
- Tests: 1553 passed (37 new in `tests/test_tool_concurrency.py`)
- Lint: `ruff check` passed
- Complexity guard: `message_flow.py` 710 lines, `tool_concurrency.py` 274 lines — 均在门禁内
