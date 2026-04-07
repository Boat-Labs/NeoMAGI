---
doc_id: 019d648c-4aa8-74f0-8d02-703c98a7015b
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-06T22:46:49+02:00
---
# P2-M1 Post Works P3：Atomic Coding Tools

- Date: 2026-04-06
- Status: approved
- Scope: 为后续 coding capability 测试补齐最小 text/coding file transaction surface，并按风险分层推进
- Basis:
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`decisions/0025-mode-switching-user-controlled-chat-safe-default.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0025-mode-switching-user-controlled-chat-safe-default.md)
  - [`decisions/0026-session-mode-storage-and-propagation.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0026-session-mode-storage-and-propagation.md)
  - [`decisions/0058-coding-mode-open-conditions.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0058-coding-mode-open-conditions.md)
  - [`dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06.md)
  - [`src/tools/base.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/base.py)
  - [`src/session/manager.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/manager.py)
  - [`src/config/settings.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/config/settings.py)
  - [`src/gateway/protocol.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/gateway/protocol.py)
  - [`src/tools/builtins/read_file.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/builtins/read_file.py)

## Goal

补齐最小 text/coding atomic tools，使 agent 可以完成：

- repo inspection
- deterministic text/code file mutation
- 条件允许时的受控命令执行

同时保持 `chat_safe` 与 coding 路径的边界清晰。

本轮只聚焦文本 / 代码文件事务层，不实现 PDF / image / notebook 等多格式读取。

## Current Baseline

- 当前已有 `read_file`，并且只在 `ToolMode.coding` 下可见。
- `read_file` 语义只读，已声明 `is_read_only = True`，但未声明 `is_concurrency_safe = True`；`BaseTool` 默认值为 `False`
- `read_file` 当前只做 UTF-8 整文件读取，没有 line range、read state、encoding / line-ending tracking、staleness check 或 structured diff 基础。
- 当前没有 `glob` / `grep` / `write_file` / `edit_file` / `bash`。
- 当前虽然存在 `ToolMode.coding` 概念，但 `SessionManager.get_mode()` 存在 M1.5 guardrail：即使 DB 中 `mode=coding`，运行时也会被强制降级为 `chat_safe`
- `SessionSettings.default_mode` 当前也通过 validator 拒绝非 `chat_safe` 的 `SESSION_DEFAULT_MODE`；ADR 0058 要求继续保持这一点。
- 当前没有外部 mode 写接口：`SessionManager` 无 `set_mode()`，WebSocket 也没有显式 mode 切换方法。
- `gateway/protocol.py` 已有 `ToolDeniedData.mode`，可承载执行闸门拒绝事件中的 mode 信息；这为 `chat_safe` 下拒绝 coding-only tool call 提供现有协议基础

这意味着：

- `P3` 代码实现必须先补齐 per-session `coding` 入口，否则 atomic coding tools 即使实现了也跑不到。
- mode 入口策略已经由 ADR 0058 固定；本计划不要求 Claude Code 重新判断策略。

## Implementation Scope

`P3` 按三层推进，但只有前两层是当前硬 scope：

1. `Stage A`：`read_file` upgrade + `glob` / `grep`
2. `Stage B`：`write_file` / `edit_file`
3. `Stage C`：`bash`

当前建议：

- `Stage A/B` = 本轮硬 scope
- `Stage C` = 条件性 follow-up，不作为第一轮硬验收

## Why `bash` Is Not A First-Round Must

`bash` 的风险和实现复杂度明显高于前两层：

- 需要处理 `cwd`、timeout、输出截断、非交互限制
- 需要处理环境变量与平台差异
- 很容易从“补一个工具”滑向“放开整个 agent harness”

而前两层先完成后，已经能支撑一轮更干净的 coding 验收：

- 找文件
- 搜文本
- 读文件
- 写文件
- 做局部编辑

这套组合已经足够验证大量 repo-level coding 行为。

## Shared Text File Transaction Contract

`read_file` / `write_file` / `edit_file` 必须共享同一套 text/coding file transaction contract。

### V1 Hard Requirements

#### Path Model

- Agent-facing 参数统一使用 `file_path`，要求是 workspace 内绝对路径。
- V1 过渡期内，`read_file` 同时接受既有 `path` alias 和新 `file_path`。新工具 `glob` / `grep` / `write_file` / `edit_file` 直接使用 `file_path`。
- 若传入 `path` alias，工具入口必须立即 canonicalize，并在返回中给出 canonical `file_path`。
- 工具结果同时返回 workspace-relative `relative_path`，供 UI / 日志展示，避免把绝对路径作为唯一人类可读标识。
- 所有 path 必须 `resolve()` 后做 workspace boundary 检查；symlink escape 必须拒绝。

#### Read State

- `read_file` 成功后记录 read state，至少包含：`session_id`、canonical `file_path`、`relative_path`、`mtime_ns`、`size`、`read_scope`、`truncated`、`read_at`。
- `read_scope` 至少包含本次读取的 line `offset` / `limit`；后续若支持 richer scope，也必须保持可序列化和可审计。
- read state 不保存完整文件内容，避免扩大内存、DB 与隐私风险。
- V1 使用进程内 map keyed by `(session_id, file_path)`，不要求数据库迁移；进程重启后 read state 丢失，replace / edit 返回 `READ_REQUIRED` 是可接受行为。
- `write_file` 覆盖已有文件、`edit_file` 修改文件前，必须检查当前 `mtime_ns + size` 与 read state 是否一致。
- 若未读过或文件已被外部修改，必须 fail-fast，提示先重新 `read_file`。
- V1 不合并多次 partial read；同一 `(session_id, file_path)` 只保留最后一次 read state。

#### Read Semantics

- `read_file` V1 只支持 text/code 文件。
- 输入支持 `file_path`、`offset`、`limit`；默认限制输出，返回 `total_lines`、实际 range、`truncated` 标记。
- V1 只支持 UTF-8。非 UTF-8 返回明确错误，不做 encoding detection。
- V1 不要求 line-ending detection / roundtrip；但文件读写路径不得使用会隐式转换换行符的默认 text mode。
- 实现应使用 `open(..., encoding="utf-8", newline="")` 或 binary read/write + 显式 UTF-8 decode/encode，避免 Python universal newline mode 静默把 CRLF 归一化为 LF。
- 这不要求 V1 检测 LF / CRLF / mixed 风格，也不要求自动修复调用方传入的新内容；只要求 I/O 层不隐式改写未触碰内容。

#### Write / Edit Semantics

- `write_file` create 新文件不要求 read-before-write。
- `write_file` replace 已有文件必须 read-before-write，并通过 staleness check。
- `write_file` 的 `overwrite=true` 且目标已存在即视为 full-file update。
- `write_file` full-file update 必须基于完整且未截断的 read state；V1 中“完整”定义为最近一次 read state 的 `offset=0` 且 `truncated=false`。
- 由于 V1 不合并多次 partial read，即使 agent 曾分段读过同一文件，只要最近一次 read state 不是完整 read，也不得覆盖整个既有文件。
- `write_file` 默认 create-only；文件已存在时返回 `FILE_EXISTS`，只有显式 `overwrite=true` 才允许 replace。
- `write_file` 成功结果必须明确 `operation=create|update`，不要只返回通用 success。
- `edit_file` 必须 read-before-edit，并通过 staleness check。
- `edit_file` 不要求完整 read state；V1 最低要求是同一文件存在未 stale 的 read state，然后由 `old_string` 唯一匹配负责局部上下文精确性。
- `edit_file` 采用 `old_string -> new_string` 精确字符串匹配；默认必须唯一匹配。
- 多处匹配时，除非显式 `replace_all=true`，否则必须 fail-fast，要求调用方提供更精确上下文。

### V1+ Reserved Enhancements

- `content_hash` 进入 read state，用于比 `mtime_ns + size` 更强的 staleness check。
- encoding detection 与 encoding roundtrip。
- line-ending detection 与 line-ending roundtrip。
- `write_file` / `edit_file` 返回 structured diff 或 unified diff。
- 成功写入后返回轻量 `file_event`，字段可包含：`operation`、`file_path`、`before_hash`、`after_hash`、`diff`、`encoding`、`line_ending`。
- V1 不直接引入 LSP client。
- 后续 LSP adapter 只能监听成功提交后的 `file_event`，LSP 失败不得回滚已成功的文件事务，只能作为 warning / diagnostics unavailable。

## Implementation Assumptions

本计划只指导 Claude Code 的代码实现；mode 入口策略以 ADR 0058 为准，不在实现阶段重新讨论。

固定假设：

- `coding` 入口是 per-session、用户显式动作。
- `SESSION_DEFAULT_MODE` 继续只允许 `chat_safe`。
- 模型不能自行切换 mode，也不能基于请求意图自动升级到 `coding`。
- `SessionManager.get_mode()` 应尊重 DB 中合法的 per-session `coding` 值；异常、缺失或非法值仍 fail-closed 到 `chat_safe`。
- coding-only tools 在 `chat_safe` 下必须同时满足不可见和不可执行。

## Stage A: Read-Only Repo Inspection

### Tools

- `read_file` upgrade
- `glob`
- `grep`

### Suggested Properties

- `allowed_modes = coding`
- `risk_level = low`
- `is_read_only = True`
- `is_concurrency_safe = True` only if implemented with bounded non-blocking filesystem operations; otherwise `False`

本计划沿用 P2 对 `is_concurrency_safe` 的定义：它表示“可进入 runtime 自动并行组”，不只是“无共享可变状态”。因此工具不能阻塞 event loop，也不能放大不可控资源争用。

`glob` / `grep` 可以声明并发安全的前提是：只读、无共享可变状态、输出有上限、不会写 cache / temp file，并且同步文件系统扫描 / 搜索必须通过有界 `asyncio.to_thread` 或等价非阻塞封装执行。若实现直接在 async 路径中调用同步 `Path.glob()` / `Path.read_text()` / 大规模文件遍历，则必须保持 `is_concurrency_safe = False`。

`read_file` 采用同一标准，不随 `Stage A` 自动改为并发安全。除非同一 slice 明确把 `read_file` 改为非阻塞文件读取并同步更新 P2 并发测试，否则保持 `is_read_only = True`、`is_concurrency_safe = False`。

### Usage

- 文件发现
- 文本 / 模式搜索
- 与现有 `read_file` 形成最小 inspection surface

### Required Boundaries

- `file_path` / pattern 必须限制在 workspace 内
- 输出必须有 `max_results` / `max_bytes` 或等价截断策略
- path escape / symlink escape 必须被拒绝或明确按 `read_file` 同等规则处理
- `grep` 正则错误必须返回结构化错误，不能抛出未处理异常

### Acceptance

- agent 能找到相关文件
- agent 能搜索文本或模式
- `glob` / `grep` 可以与 `read_file` 联合完成最小 repo inspection
- path escape 被正确拒绝
- 超限输出会被截断并带有明确截断标记
- `chat_safe` mode 下 `glob` / `grep` / `read_file` 不可见且不可执行

## Stage B: Deterministic File Mutation

### Tools

- `write_file`
- `edit_file`

### Suggested Properties

- `allowed_modes = coding`
- `risk_level = high`
- `is_read_only = False`
- `is_concurrency_safe = False`

### Required Boundaries

- `file_path` 必须限制在 workspace 内
- replace / edit 必须基于 read state 做 read-before-write / read-before-edit 与 staleness check
- full-file update 必须要求 read state 覆盖完整且未截断的文件
- `write_file` 负责 create / explicit update
- `write_file` 默认 create-only；文件已存在时返回 `FILE_EXISTS`
- `write_file` 只有在调用参数显式传入 `overwrite=true` 时才允许 replace
- `write_file` 成功结果必须返回 `operation=create|update`
- `edit_file` 采用 `old_string -> new_string` 精确字符串匹配
- `edit_file` 要求 `old_string` 在目标文件中唯一匹配；0 次匹配或多次匹配都必须 fail-fast
- `edit_file` 不做模糊 patch、不做 regex patch、不默认按行号替换
- structured diff 与 `file_event` 属于 V1+ 预留，不作为本轮硬要求

### Acceptance

- agent 能在受限路径内创建或替换文件
- agent 能在上下文匹配时做局部编辑
- 上下文不匹配时，`edit_file` 会明确失败而不是 silent drift
- `write_file` 在文件已存在且未传 `overwrite=true` 时明确失败
- `write_file` 在仅有 partial read state 时拒绝 full-file update
- `edit_file` 在 0 次匹配和多次匹配时都明确失败
- `edit_file` 在 `replace_all=true` 且存在多处匹配时全部替换；`replace_all=true` 且 0 次匹配时仍明确失败
- 未先 `read_file` 或 read state stale 时，replace / edit 明确失败
- 成功 create / update 返回 `operation=create|update`
- path escape / symlink escape 被正确拒绝
- `chat_safe` mode 下 `write_file` / `edit_file` 不可见且不可执行

## Stage C: Guarded Shell

### Tool

- `bash`

### Suggested Properties

- `allowed_modes = coding`
- `risk_level = high`
- `is_read_only = False`
- `is_concurrency_safe = False`

### Required Boundaries

- workspace-bounded `cwd`
- 明确 timeout
- 输出截断
- 禁止交互式命令
- 明确环境变量继承策略

## Stage C Scope Rule

当前建议是：**先不把 `bash` 纳入第一轮硬实现与硬验收。**

建议只有在以下条件都满足后，再开启 `Stage C`：

1. `Stage A/B` 已经稳定
2. coding mode 入口代码路径已经完成
3. 后续验收明确需要“运行测试 / lint / build 命令”这一类能力

如果这三个条件还没同时满足，`bash` 应继续保留为 reserved follow-up。

## Suggested Implementation Slices

### Slice A. Coding Entry Implementation

- 按 ADR 0058 移除或条件化 `SessionManager.get_mode()` 的 M1.5 hard guardrail，让合法 `mode=coding` 可生效。
- 新增 `SessionManager.set_mode()`，写入 per-session mode，并保持非法 mode fail-closed / validation error。
- 增加显式用户触发的 WebSocket RPC mode 切换方法；不要把 mode 加成 `chat.send` 的隐式升级参数。
- 保持 `SessionSettings.default_mode` validator 只接受 `chat_safe`。
- 更新 frontend RPC 类型与最小 UI 入口，使用户可以显式切换当前 session mode。
- 确认现有 `ToolDeniedData.mode` 足以表达 mode denial；除非实现发现缺字段，否则 `gateway/protocol.py` schema 不作为本 slice 必改项。
- 保持“tool 未注册”和“tool 已注册但当前 mode 不允许”的执行闸门错误区分，避免把 coding-only tool call 在 `chat_safe` 下误报为普通 unknown tool。
- 补齐 mode 相关测试：`chat_safe` 默认路径、per-session `coding` 生效、非法 mode 拒绝、DB 异常 fail-closed、模型不能通过 tool call 自行升级。

### Slice B1. `read_file` Upgrade

- 更新 `read_file` 为 text/code file reader：`file_path`、`offset`、`limit`、输出截断、process-local read state tracking。
- V1 同时接受既有 `path` alias 和新 `file_path`，内部统一 canonicalize；schema / description 应推荐 `file_path`。
- 返回 canonical `file_path`、`relative_path`、`total_lines`、实际 range、`truncated`。
- read state V1 的 staleness check 只要求 `mtime_ns + size + read_scope + truncated`；完整 read state record 仍按 Contract 字段实现：`session_id`、`file_path`、`relative_path`、`mtime_ns`、`size`、`read_scope`、`truncated`、`read_at`。
- 不要求 `content_hash`、encoding detection 或 line-ending detection。
- 补齐 `read_file` schema / return shape / workspace boundary / partial read tests。
- 不默认改 `read_file.is_concurrency_safe`；若要改，必须同 slice 完成非阻塞读取改造和测试更新。

### Slice B2. `glob` / `grep`

- `glob`
- `grep`
- 相关 schema / tests
- 推荐 `glob` / `grep` 使用有界非阻塞封装后声明 `is_concurrency_safe = True`；若直接使用同步文件系统 API，则必须保持 `False`

### Slice C. Stage B Tools

- `write_file`
- `edit_file`
- 基于 `mtime_ns + size` 的 read state / staleness check
- `write_file` create / update 语义与 `operation=create|update` 返回
- `edit_file` unique-match 与 `replace_all=true` 语义
- workspace boundary / overwrite / partial-read full-update / stale read / unique-match fail-fast tests

### Slice D. Stage C Evaluation

- 不先默认实现
- 先记录是否进入独立 follow-up 的判断
- 若进入 follow-up，再单独实现 `bash`

## Acceptance

### Hard Acceptance For This Round

- coding 路径下可用 `glob` / `grep` / `read_file`
- coding 路径下可用 `write_file` / `edit_file`
- `chat_safe` 默认不暴露 coding-only tools，且 hallucinated tool call 会被执行闸门拒绝
- workspace boundary 覆盖 `glob` / `grep` / `read_file` / `write_file` / `edit_file`
- `read_file` 支持 line range 与 output truncation，并记录 read state
- `edit_file` 对 0 次匹配和多次匹配都有负向测试
- `edit_file` 对 `replace_all=true` 的多处匹配成功和 0 次匹配失败都有测试
- `write_file` 对未显式 overwrite 的既有文件有负向测试
- `write_file` 对 partial read 后尝试 full-file update 有负向测试
- `write_file` replace 与 `edit_file` 对未读过 / stale read state 都有负向测试
- `write_file` 成功后返回 `operation=create|update`
- 若未重构 `read_file` 为 async / 非阻塞读取，则 `read_file.is_concurrency_safe` 保持 `False`

### Explicitly Not Required In First Round

- `bash`
- 命令输出 streaming
- 复杂 shell session 语义
- `content_hash` read state
- encoding detection / encoding roundtrip
- line-ending detection / line-ending roundtrip
- structured diff / `file_event`
- PDF / image / notebook 等多格式读取
- LSP client / diagnostics refresh / code action
- dynamic skill trigger

## Risks

### R1. 不先实现 coding 入口，工具实现会失去真实运行路径

ADR 0058 已固定入口策略。因此 `P3` 必须先完成 per-session `coding` 入口代码路径，再验收 atomic coding tools。

### R2. `edit_file` 如果支持模糊 patch，会放大 silent drift

因此本计划明确要求 fail-fast。

### R3. 过早引入 `bash` 会显著放大 blast radius

这也是当前把 `bash` 设为条件性 follow-up 的主要原因。

## Clean Handoff Boundary

Claude Code 实现 `P3` 时，建议分两轮：

1. 先做 `Stage A/B`
2. 再单独判断 `Stage C`

不要在第一轮里把 `bash` 和前四个工具打包落地。
