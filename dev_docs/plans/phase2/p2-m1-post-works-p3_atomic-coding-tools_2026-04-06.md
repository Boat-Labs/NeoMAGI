---
doc_id: 019d648c-4aa8-74f0-8d02-703c98a7015b
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-06T22:46:49+02:00
---
# P2-M1 Post Works P3：Atomic Coding Tools

- Date: 2026-04-06
- Status: approved
- Scope: 为后续 coding capability 测试补齐最小 atomic coding surface，并按风险分层推进
- Basis:
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`decisions/0025-mode-switching-user-controlled-chat-safe-default.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0025-mode-switching-user-controlled-chat-safe-default.md)
  - [`decisions/0026-session-mode-storage-and-propagation.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0026-session-mode-storage-and-propagation.md)
  - [`dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06.md)
  - [`src/tools/base.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/base.py)
  - [`src/session/manager.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/manager.py)
  - [`src/config/settings.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/config/settings.py)
  - [`src/gateway/protocol.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/gateway/protocol.py)
  - [`src/tools/builtins/read_file.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/builtins/read_file.py)

## Goal

补齐最小 coding atomic tools，使 agent 可以完成：

- repo inspection
- deterministic file mutation
- 条件允许时的受控命令执行

同时保持 `chat_safe` 与 coding 路径的边界清晰。

## Current Baseline

- 当前已有 `read_file`，并且只在 `ToolMode.coding` 下可见
- `read_file` 语义只读，已声明 `is_read_only = True`，但未声明 `is_concurrency_safe = True`；`BaseTool` 默认值为 `False`
- 当前没有 `glob` / `grep` / `write_file` / `edit_file` / `bash`
- 当前虽然存在 `ToolMode.coding` 概念，但 `SessionManager.get_mode()` 存在 M1.5 guardrail：即使 DB 中 `mode=coding`，运行时也会被强制降级为 `chat_safe`
- `SessionSettings.default_mode` 当前也通过 validator 拒绝非 `chat_safe` 的 `SESSION_DEFAULT_MODE`
- 当前没有外部 mode 写接口：`SessionManager` 无 `set_mode()`，WebSocket `chat.send` 参数也没有 mode 字段
- `gateway/protocol.py` 已有 `ToolDeniedData.mode`，可承载执行闸门拒绝事件中的 mode 信息；这为 `chat_safe` 下拒绝 coding-only tool call 提供现有协议基础

这意味着：

- 先不解决入口策略，atomic coding tools 即使实现了也未必真正可用
- 开放 `coding` 入口不是单纯参数调整，而是解除 M1.5 guardrail 与 ADR 0025 阶段边界

## Core Decision

`P3` 按三层推进，但只有前两层是当前硬 scope：

1. `Stage A`：`glob` / `grep`
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

## Prerequisite

在真正实现 `Stage A/B/C` 之前，必须先冻结 coding 入口策略。

至少要明确：

- 哪类 session 可以进入 coding mode
- 这个入口是实验路径还是正式路径
- `chat_safe` 是否继续保持默认降级

必须显式完成的前置任务：

- 若本轮开放 `coding`，先新建下一编号 ADR，作为 ADR 0025 的后续决议，明确 mode 开放条件、交互提示与回退策略；当前建议文件名：`decisions/0058-coding-mode-open-conditions.md`
- 决定入口策略：保持 per-session 显式用户切换，不允许模型自动从请求意图升级 mode
- 决定 `chat_safe` 仍为默认路径，且高风险 coding tools 默认不暴露给 `chat_safe`
- 移除或条件化 `SessionManager.get_mode()` 中的 M1.5 hard guardrail
- 建议 `SessionSettings.default_mode` validator 继续只接受 `chat_safe`；`coding` 入口通过 per-session `set_mode()` 实现，而不是通过全局默认配置打开
- 决定是否新增 `SessionManager.set_mode()` 与对应 RPC / UI 显式切换路径
- 决定 `gateway/app.py` 中仅注入 `default_mode` 是否足够，还是必须支持 per-session 动态切换
- 增加测试覆盖：DB 中 `mode=coding` 在已开放策略下不再被降级；未开放策略下仍 fail-closed 到 `chat_safe`

如果这一步不先完成，后续工具会变成“实现了但跑不到”。

## Stage A: Read-Only Repo Inspection

### Tools

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

- path / pattern 必须限制在 workspace 内
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

- path 必须限制在 workspace 内
- `write_file` 负责 create / explicit replace
- `write_file` 默认 create-only；文件已存在时返回 `FILE_EXISTS`
- `write_file` 只有在调用参数显式传入 `overwrite=true` 时才允许 replace
- `edit_file` 采用 `old_text -> new_text` 精确字符串匹配
- `edit_file` 要求 `old_text` 在目标文件中唯一匹配；0 次匹配或多次匹配都必须 fail-fast
- `edit_file` 不做模糊 patch、不做 regex patch、不默认按行号替换

### Acceptance

- agent 能在受限路径内创建或替换文件
- agent 能在上下文匹配时做局部编辑
- 上下文不匹配时，`edit_file` 会明确失败而不是 silent drift
- `write_file` 在文件已存在且未传 `overwrite=true` 时明确失败
- `edit_file` 在 0 次匹配和多次匹配时都明确失败
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

## Go / No-Go Recommendation For `bash`

当前建议是：**先不把 `bash` 纳入第一轮硬实现与硬验收。**

建议只有在以下条件都满足后，再开启 `Stage C`：

1. `Stage A/B` 已经稳定
2. coding mode 入口已经冻结
3. 后续验收明确需要“运行测试 / lint / build 命令”这一类能力

如果这三个条件还没同时满足，`bash` 应继续保留为 reserved follow-up。

## Suggested Implementation Slices

### Slice A. Coding Entry Freeze

- 新建下一编号 ADR，作为 ADR 0025 的后续决议，明确开放 `coding` 的条件、交互提示与回退策略；当前建议文件名：`decisions/0058-coding-mode-open-conditions.md`
- 明确 coding mode 的进入方式：per-session、用户显式动作、模型不可自行切换
- 明确 default path 仍是 `chat_safe`
- 移除或条件化 `SessionManager.get_mode()` 的 M1.5 hard guardrail
- 决定是否新增 `SessionManager.set_mode()` 与 WebSocket RPC / frontend UI 显式切换
- 保持 `SESSION_DEFAULT_MODE` 只允许 `chat_safe`，除非新 ADR 明确给出偏离理由
- 更新并补齐 mode 相关测试

### Slice B. Stage A Tools

- `glob`
- `grep`
- 相关 schema / tests
- 推荐 `glob` / `grep` 使用有界非阻塞封装后声明 `is_concurrency_safe = True`；若直接使用同步文件系统 API，则必须保持 `False`
- 不默认改 `read_file.is_concurrency_safe`；若要改，必须同 slice 完成非阻塞读取改造和测试更新

### Slice C. Stage B Tools

- `write_file`
- `edit_file`
- workspace boundary / overwrite / fail-fast tests

### Slice D. Stage C Evaluation

- 不先默认实现
- 先做 go / no-go 决策
- 若 go，再单独实现 `bash`

## Acceptance

### Hard Acceptance For This Round

- coding 路径下可用 `glob` / `grep` / `read_file`
- coding 路径下可用 `write_file` / `edit_file`
- `chat_safe` 默认不暴露 coding-only tools，且 hallucinated tool call 会被执行闸门拒绝
- workspace boundary 覆盖 `glob` / `grep` / `read_file` / `write_file` / `edit_file`
- `edit_file` 对 0 次匹配和多次匹配都有负向测试
- `write_file` 对未显式 overwrite 的既有文件有负向测试
- 若未重构 `read_file` 为 async / 非阻塞读取，则 `read_file.is_concurrency_safe` 保持 `False`

### Explicitly Not Required In First Round

- `bash`
- 命令输出 streaming
- 复杂 shell session 语义

## Risks

### R1. 不先冻结 coding 入口，工具实现会失去真实运行路径

这不是文档问题，而是验收问题。  
因此入口冻结必须写进 `P3` 前置。

### R2. `edit_file` 如果支持模糊 patch，会放大 silent drift

因此本计划明确要求 fail-fast。

### R3. 过早引入 `bash` 会显著放大 blast radius

这也是当前把 `bash` 设为条件性 follow-up 的主要原因。

## Clean Handoff Boundary

Claude Code 实现 `P3` 时，建议分两轮：

1. 先做 `Stage A/B`
2. 再单独判断 `Stage C`

不要在第一轮里把 `bash` 和前四个工具打包落地。
