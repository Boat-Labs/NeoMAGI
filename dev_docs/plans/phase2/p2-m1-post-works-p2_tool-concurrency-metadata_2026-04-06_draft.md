# P2-M1 Post Works P2：Tool Concurrency Metadata（草案）

- Date: 2026-04-06
- Status: draft
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

- `[glob, grep, read_file]` -> 一个并行组
- `[grep, write_file, grep]` -> 并行组 -> barrier -> 新组
- `[bash, grep]` -> 默认串行，除非未来 `bash` 明确声明并发安全

## Transcript Semantics

即使执行并行，transcript 写入顺序也应保持模型给出的原始顺序。

### Required Behavior

- 执行可以并行
- `tool` messages 仍按原 tool call 顺序 append

### Why

- 保持与当前串行语义尽量一致
- 降低 replay / compaction / debug 风险
- 避免“谁先跑完谁先写回”污染模型的顺序预期

## Bounded Parallelism

V1 必须设置并发上限。

建议：

- 每组最多 `2~4` 个并发 tool calls
- 超出部分按顺序分批执行

## Suggested Implementation Slices

### Slice A. Metadata

- 在 `BaseTool` 上增加：
  - `is_read_only`
  - `is_concurrency_safe`
- 默认值都为 `False`

### Slice B. Group Builder

- 在 `message_flow` 中增加 execution group 切分逻辑
- 把连续可并发工具收成 group
- barrier 单独执行

### Slice C. Parallel Executor

- 对只读 group 使用 bounded parallel execution
- 收集结果后按原顺序写回 transcript
- 保留现有 guard / error 语义

### Slice D. Observability

- 新增日志：
  - `tool_parallel_group_started`
  - `tool_parallel_group_finished`
  - `group_size`
  - `serial_barrier_tool`

## Acceptance

- 至少两种只读工具能在同一 turn 内并行执行。
- 写入型工具会形成 barrier。
- 未声明元数据的工具继续串行，保持 fail-closed。
- transcript 中的 `tool` message 顺序仍 deterministic。
- 现有 guardrail 拒绝语义不回归。

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

## Clean Handoff Boundary

Claude Code 实现 `P2` 时，默认不要顺手做：

- multi-session UI
- 新 atomic tools
- procedure-level scheduler

`P2` 的任务目标很窄：  
增加元数据、增加 execution grouping、增加 bounded parallel execution。
