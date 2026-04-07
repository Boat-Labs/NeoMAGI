---
doc_id: 019d648c-4aa8-7ada-9add-18fd3287aa88
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-06T22:46:49+02:00
---
# P2-M1 Post Works P2：Tool Concurrency Metadata

- Date: 2026-04-06
- Status: approved
- Scope: 为 tool runtime 增加轻量、fail-closed 的并发元数据与同 turn 只读工具并行调度能力
- Basis:
  - [`src/tools/base.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/base.py)
  - [`src/tools/registry.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/registry.py)
  - [`src/agent/message_flow.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/message_flow.py)
  - [`src/agent/tool_runner.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/tool_runner.py)
  - [`src/agent/guardrail.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/guardrail.py)

## Goal

在不引入重型 scheduler 的前提下，让 runtime 能自动并行同一 turn 内连续出现的只读、安全 tool calls，并保持 transcript 与调试语义稳定。

## Current Baseline

- `BaseTool` 当前只有 `group`、`allowed_modes`、`risk_level` 等元数据。
- `message_flow` 当前按模型顺序串行执行每个 tool call。
- transcript 写回顺序与执行顺序完全绑定。

## Decision

保留两个 fail-closed 元数据，而不是只保留一个：

- `is_read_only: bool = False`
- `is_concurrency_safe: bool = False`

只有两者都为 `True`，工具才允许被 runtime 自动并行。

## Why Two Flags

只读不等于可安全并发。  
以下场景都可能是“只读但不该并发”：

- 有严格 rate limit 的外部 API
- 共享临时目录或 cache 文件
- 顺序敏感的远程读取
- 成本很高、并发会放大资源争用的扫描

因此：

- `is_read_only` 回答“是否写状态”
- `is_concurrency_safe` 回答“是否适合自动并发”

## Execution Model

本阶段只做“同一 LLM turn 内、连续只读批次”的轻量并行。

### Rules

1. 扫描模型返回的 `tool_calls_result`
2. 按顺序切分 execution groups
3. 连续出现、且全部声明为 `read_only + concurrency_safe` 的 group 并行执行
4. 任何写入型或未声明并发安全的工具都是 barrier
5. barrier 后重新开始下一组

### Examples

- `[current_time, memory_search, soul_status]` -> 一个并行组
- `[memory_search, memory_append, current_time]` -> 并行组 -> barrier -> 新并行组
- `[read_file, memory_search]` -> `read_file` 默认 barrier，除非本轮先完成 async 文件读取改造并明确声明并发安全
- `[unknown_tool, memory_search]` -> `unknown_tool` barrier -> 新并行组

### Runtime Shape

当前 `message_flow._execute_single_tool` 是 async generator，边执行边 `yield` `ToolCallInfo` / `ToolDenied`，并在函数内部写 transcript。并发实现不得直接并发消费多个 async generator。

V1 需要把单工具执行拆成“准备事件 / 执行工具 / 返回结果 / 串行落盘”四步：

- 外层按原 tool call 顺序先生成并 `yield` `ToolCallInfo`
- 并发组内使用 `asyncio.TaskGroup` 执行工具调用
- 每个 task 返回自己的 `result`、`failure_signals`、可选 `ToolDenied` event
- 外层等待组内任务完成后，按原 tool call 顺序 `yield` `ToolDenied`、合并 `failure_signals`、串行 append `tool` message

这样避免多个 async generator 并发 `yield`，同时保持 UI 事件和 transcript 的确定性顺序。

## Transcript Semantics

即使执行并行，transcript 写入顺序也应保持模型给出的原始顺序。

### Required Behavior

- 执行可以并行
- `tool` messages 仍按原 tool call 顺序 append
- `append_message(role="tool")` 必须在并发组执行完成后按原序串行调用，不允许 task 内部自行 append

### Why

- 保持与当前串行语义尽量一致
- 降低 replay / compaction / debug 风险
- 避免“谁先跑完谁先写回”污染模型的顺序预期
- session `seq` 分配依赖 append 顺序；并发 append 会让 DB 原子性正确但 transcript 顺序不确定

## Bounded Parallelism

V1 必须设置并发上限。

决策：

- 每组最多 `3` 个并发 tool calls
- 超出部分按顺序分批执行
- 后续如有明确观测数据，再通过独立变更调整该值

## V1 Tool Marking

默认仍 fail-closed：未显式覆盖的 tool 都保持 `False + False`，不会自动并行。

V1 明确标注：

- `current_time`: `is_read_only=True`, `is_concurrency_safe=True`
- `memory_search`: `is_read_only=True`, `is_concurrency_safe=True`
- `soul_status`: `is_read_only=True`, `is_concurrency_safe=True`

V1 明确不标注为并发安全：

- `memory_append`: 写 memory，保持默认 `False + False`
- `soul_propose`: 会 propose / evaluate / apply，保持默认 `False + False`
- `soul_rollback`: 会 rollback / veto，保持默认 `False + False`
- `read_file`: 语义只读，但当前实现使用同步 `Path.read_text()`；本计划不要求顺手重构文件 I/O，因此只允许在同一实现中先改为 async 文件读取后，才标记 `is_concurrency_safe=True`。否则保持 `is_read_only=True`, `is_concurrency_safe=False`。

`risk_level` 不是并发 eligibility。group builder 只检查 `is_read_only and is_concurrency_safe`，guardrail 仍由现有 pre-tool guard 执行。

## Suggested Implementation Slices

### Slice A. Metadata

- 在 `BaseTool` 上增加：
  - `is_read_only`
  - `is_concurrency_safe`
- 默认值都为 `False`
- 两个字段都使用 `@property`，与现有 `group`、`allowed_modes`、`risk_level` 元数据模式一致
- 按 `V1 Tool Marking` 清单更新 builtin tools
- 增加 metadata 声明测试，验证默认 fail-closed 与 V1 显式覆盖

### Slice B. Group Builder

- 在 `message_flow` 中增加 execution group 切分逻辑
- 把连续可并发工具收成 group
- barrier 单独执行
- unknown tool、写入型 tool、未声明并发安全的 tool 都必须作为 barrier
- group 内保留原始 `tool_index`，用于后续按模型顺序 merge / yield / append

### Slice C. Parallel Executor

- 对只读 group 使用 bounded parallel execution，必须使用 `asyncio.TaskGroup`
- 每个 task 返回独立 execution outcome，不直接写 `state.accumulated_failure_signals`，不直接 append transcript
- 收集结果后按原顺序：
  - `yield` `ToolDenied` event
  - merge `failure_signals`
  - append `tool` message
- 保留现有 guard / error 语义
- 串行 barrier 可复用同一 execution outcome 路径，减少并行 / 串行语义分叉

### Slice D. Observability

- 新增日志：
  - `tool_parallel_group_started`
  - `tool_parallel_group_finished`
  - `serial_barrier_tool`
- 组级日志字段至少包含：
  - `session_id`
  - `iteration`
  - `group_index`
  - `group_size`
  - `max_concurrency`
  - `tool_names`
- barrier 日志字段至少包含：
  - `session_id`
  - `iteration`
  - `tool_index`
  - `tool_name`
  - `reason`

### Slice E. Tests

- metadata：验证 `BaseTool` 默认 `False + False`，V1 标注工具显式 override
- grouping：验证连续 `current_time + memory_search` 形成并发组，`memory_append` / 未声明工具形成 barrier
- executor：用受控 async fake tool 验证并发执行存在 overlap，但 `ToolCallInfo`、`ToolDenied`、`tool` message append 顺序仍按原 tool call 顺序
- failure signals：验证并发组内多个失败按原 tool call 顺序 merge
- regression：验证 mode denied、unknown tool、guardrail denied 的现有语义不回归

## Acceptance

- 至少两种 V1 双标记工具（建议 `current_time` + `memory_search`）能在同一 turn 内并行执行。
- 写入型工具会形成 barrier。
- 未声明元数据的工具继续串行，保持 fail-closed。
- transcript 中的 `tool` message 顺序仍 deterministic。
- `ToolCallInfo` / `ToolDenied` event 顺序仍 deterministic。
- `state.accumulated_failure_signals` 不受 task 完成顺序影响。
- 现有 guardrail 拒绝语义不回归。
- 并发组日志可通过 `group_index` 与同一 iteration 内的 group 关联。

## Risks

### R1. 错把“只读”当“并发安全”

这是本阶段最大的语义风险。  
因此双标记是必须项，不是锦上添花。

### R2. 并发执行后错误地按完成顺序写 transcript

这会让历史与模型顺序脱钩。  
本计划明确禁止这种写法。

### R3. 组切分过于激进导致收益不明显

这不是灾难性问题，因为 fail-closed 优先级更高。  
收益不足可以后续再放宽，不能先放开再回收。

### R4. read_file 语义只读但实现仍是同步 I/O

`read_file` 不能仅因为语义只读就进入并发组。
若本轮不改为 async 文件读取，必须保持 `is_concurrency_safe=False`。

## Clean Handoff Boundary

Claude Code 实现 `P2` 时，默认不要顺手做：

- multi-session UI
- 新 atomic tools
- procedure-level scheduler

`P2` 的任务目标很窄：  
增加元数据、增加 execution grouping、增加 bounded parallel execution。
