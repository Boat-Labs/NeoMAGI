# P2-M1 Post Works P3：Atomic Coding Tools（草案）

- Date: 2026-04-06
- Status: draft
- Scope: 为后续 coding capability 测试补齐最小 atomic coding surface，并按风险分层推进
- Basis:
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`src/tools/base.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/base.py)
  - [`src/session/manager.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/manager.py)
  - [`src/tools/builtins/read_file.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/builtins/read_file.py)

## Goal

补齐最小 coding atomic tools，使 agent 可以完成：

- repo inspection
- deterministic file mutation
- 条件允许时的受控命令执行

同时保持 `chat_safe` 与 coding 路径的边界清晰。

## Current Baseline

- 当前已有 `read_file`
- 当前没有 `glob` / `grep` / `write_file` / `edit_file` / `bash`
- 当前虽然存在 `ToolMode.coding` 概念，但 session mode 读取逻辑仍会把非 `chat_safe` 模式降级

这意味着：

- 先不解决入口策略，atomic coding tools 即使实现了也未必真正可用

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

如果这一步不先完成，后续工具会变成“实现了但跑不到”。

## Stage A: Read-Only Repo Inspection

### Tools

- `glob`
- `grep`

### Suggested Properties

- `allowed_modes = coding`
- `risk_level = low`
- `is_read_only = True`
- `is_concurrency_safe = True`

### Usage

- 文件发现
- 文本 / 模式搜索
- 与现有 `read_file` 形成最小 inspection surface

### Acceptance

- agent 能找到相关文件
- agent 能搜索文本或模式
- `glob` / `grep` 可以与 `read_file` 联合完成最小 repo inspection

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
- `write_file` 负责 create / replace
- `edit_file` 负责局部编辑
- `edit_file` 必须 fail-fast，不做模糊 patch

### Acceptance

- agent 能在受限路径内创建或替换文件
- agent 能在上下文匹配时做局部编辑
- 上下文不匹配时，`edit_file` 会明确失败而不是 silent drift

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

- 明确 coding mode 的进入方式
- 明确 default path 仍是 `chat_safe`

### Slice B. Stage A Tools

- `glob`
- `grep`
- 相关 schema / tests

### Slice C. Stage B Tools

- `write_file`
- `edit_file`
- workspace boundary / fail-fast tests

### Slice D. Stage C Evaluation

- 不先默认实现
- 先做 go / no-go 决策
- 若 go，再单独实现 `bash`

## Acceptance

### Hard Acceptance For This Round

- coding 路径下可用 `glob` / `grep` / `read_file`
- coding 路径下可用 `write_file` / `edit_file`
- `chat_safe` 默认不暴露这些高风险工具

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
