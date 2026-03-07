# P2-Devcoord Stage C 实施计划：Grouped CLI Surface

- Date: 2026-03-07
- Status: approved
- Scope: `P2-Devcoord Stage C` only; collapse `coord.py` human/debug CLI into grouped commands while retaining flat compatibility aliases and preserving `apply` as the machine-first entrypoint
- Track Type: parallel development-process repair track; outside the `P2-M*` product milestone series
- Basis:
  - [`dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07.md)
  - [`dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md)
  - [`dev_docs/plans/phase2/p2-devcoord-stage-b_sqlite-backend_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-b_sqlite-backend_2026-03-07.md)
  - [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md)
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)

## Context

`Stage A` 已完成 store seam 抽象，`Stage B` 已完成 SQLite backend 与 `render/audit` 切换。当前剩余的主要噪声已经不在持久化层，而在命令面：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py) 目前仍暴露 17 个顶层命令：
  - `init`
  - `open-gate`
  - `ack`
  - `heartbeat`
  - `phase-complete`
  - `recovery-check`
  - `state-sync-ok`
  - `ping`
  - `unconfirmed-instruction`
  - `log-pending`
  - `stale-detected`
  - `gate-review`
  - `gate-close`
  - `milestone-close`
  - `render`
  - `audit`
  - `apply`
- 同一命令语义目前散落在三层：
  - `build_parser()` 中定义 CLI 形状
  - `run_cli()` 中把 argparse 结果手工转换为 payload
  - `_execute_action()` 中再把 action 映射到 service 方法
- 如果直接在当前结构上叠加 `gate ...` / `command ...` / `event ...` 分组命令，很容易形成“新命令 + 旧命令 + apply action”三套并行接线，导致回归和维护成本继续上涨。
- `.claude/skills/devcoord-pm/SKILL.md`、`.claude/skills/devcoord-backend/SKILL.md`、`.claude/skills/devcoord-tester/SKILL.md` 当前仍主要以 flat action / `apply <action>` 组织说明；若 CLI 只在代码层 regroup、而不更新 skill 示例，新的 canonical path 不会真正落地。

因此 `Stage C` 的目标不是“再加一层子命令”，而是把**人类可见命令面收敛成单一 canonical surface，同时让兼容 alias 成为薄适配层**。

## Precondition

`Stage C` 启动前必须满足：

- [`p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md) 已实现并落地
- [`p2-devcoord-stage-b_sqlite-backend_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-b_sqlite-backend_2026-03-07.md) 已实现并落地
- SQLite 路径已是可工作的 fresh-start 控制面
- `render/audit` 已以 SQLite 为真源工作

当前按用户口径，这些前置条件已满足，且本计划已获批准，因此 `Stage C` 可进入实施准备。

## Core Decision

`Stage C` 采用**命令面收敛 + 兼容期保留 + machine-first 保持稳定** 的策略：

1. 顶层 human/debug CLI 收敛为：
   - `init`
   - `gate ...`
   - `command ...`
   - `event ...`
   - `projection ...`
   - `milestone ...`
   - `apply ...`
2. `apply` 继续保留现有 action-oriented machine-first 入口，不在 `Stage C` 改成 grouped action tree。
3. 旧 flat commands 保留为 compatibility aliases，但不再作为主要 help surface。
4. grouped commands、flat aliases、`apply` 最终都要收敛到同一套内部 canonical action 映射，避免三套 dispatch 逻辑分叉。
5. skill / runbook 的**面向人阅读**示例切到 grouped CLI；机器化 payload reference 继续保留 `apply` 形式。

这意味着 `Stage C` 的成功标准不是“命令名变了”，而是：

- canonical path 只剩一套
- 旧入口还能跑
- skill / 文档 / 帮助面一致指向新的 grouped surface

## Goals

- 将 [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py) 顶层命令面收敛为 grouped surface。
- 保持 `apply` 为稳定的 machine-first 入口。
- 保持旧 flat commands 在兼容期内继续可执行。
- 让 grouped commands、flat aliases、`apply` 共享同一条内部 action/payload 归一化路径。
- 更新 devcoord skills 与相关运行文档，使 grouped CLI 成为新默认口径。
- 增加 CLI smoke coverage，验证 regrouping 不改变协议行为、输出和关键 guard。

## Non-Goals

- 不改 SQLite schema、事务、`CoordStore` 或 `CoordService` 的协议语义。
- 不改 `apply` 的 JSON payload 形状，也不重命名既有 action ids（如 `open-gate`、`gate-close`）。
- 不为了 `Stage C` 单独引入新的 protocol write path；若 `STOP / WAIT / RESUME` 当前缺少显式 service/runtime 支撑，不在本阶段顺手补完整套协议实现。
- 不移除旧 flat aliases。
- 不删除 `--backend`、`--beads-dir`、`--bd-bin`、`--dolt-bin`。
- 不在本阶段完成 `Stage D` 的 beads cutover / closeout cleanup。
- 不在本阶段修改 [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)、[`AGENTS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTS.md)、[`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md) 中 “`.beads` 为 SSOT” 的全局治理口径。
- 不把 `coord.py` 扩张成通用 workflow engine 或插件式 CLI 框架。

## Current Baseline

当前 CLI 形态的主要问题不是功能缺失，而是实现与认知负担同时偏大：

### 1. 命令面噪声仍高

- top-level 子命令过多，help 直接暴露全部协议事件名。
- `open-gate`、`gate-review`、`gate-close` 与 `ack`、`ping`、`heartbeat` 并列，无法体现 `gate / command / event / projection / milestone` 的语义分层。

### 2. 接线重复

- 同一动作至少在 parser、payload 组装、service dispatch 三处重复出现。
- `Stage C` 若继续照旧扩展，很容易让 grouped commands 变成“额外平铺”而不是“真正 canonical”。

### 3. 文档口径尚未切换

- devcoord 三个 skill 当前都偏向 flat action / `apply` 组织。
- [`dev_docs/devcoord/beads_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/beads_control_plane.md) 仍以 flat 命令表述控制面命令。
- 如果不同步切示例，实际使用者仍会继续沿用旧命令。

### 4. CLI 回归测试仍偏 service-heavy

- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py) 已覆盖大量 service / store / projection 行为，但 CLI smoke 仍以少量 flat command / `apply` 路径为主。
- `Stage C` 需要补足 grouped form 与 alias compatibility 的测试，而不只是依赖 service 行为测试兜底。

## Target CLI Shape

### 1. Top-level Commands

`Stage C` 后的人类可见顶层命令目标固定为：

- `init`
- `gate`
- `command`
- `event`
- `projection`
- `milestone`
- `apply`

### 2. Grouped Subcommands

建议分组保持与设计稿一致：

- `gate`
  - `open`
  - `review`
  - `close`
- `command`
  - `ack`
  - `send`
- `event`
  - `heartbeat`
  - `phase-complete`
  - `recovery-check`
  - `state-sync-ok`
  - `stale-detected`
  - `log-pending`
  - `unconfirmed-instruction`
- `projection`
  - `render`
  - `audit`
- `milestone`
  - `close`

其中：

- `command send` 的 `Stage C` 最小实现先覆盖当前已存在的显式发送路径，即 `PING`
- `command send --name PING` 在 `Stage C` 中固定沿用当前 `ping` 的参数集：
  - `--milestone`
  - `--role`
  - `--phase`
  - `--gate`
  - `--task`
  - `--target-commit`
- `--name` 在 `Stage C` 只是为 canonical path 预留命名位，不引入按命令名动态切换 argparse 参数集的 generic dispatch
- `STOP / WAIT / RESUME` 只有在不新增一层 protocol/service 语义、且能复用同一条 ACK-required command write path 的前提下，才允许一并并入 `command send`
- `GATE_OPEN` 明确保留在 `gate open`，不回收到 `command send`
- `apply` 继续直接使用 `apply <action>`，不要求写成 `apply gate open`

### 3. Compatibility Alias Mapping

本阶段必须明确保留以下 alias：

| Flat command | Canonical grouped path |
| --- | --- |
| `open-gate` | `gate open` |
| `ack` | `command ack` |
| `heartbeat` | `event heartbeat` |
| `phase-complete` | `event phase-complete` |
| `recovery-check` | `event recovery-check` |
| `state-sync-ok` | `event state-sync-ok` |
| `ping` | `command send --name PING` |
| `unconfirmed-instruction` | `event unconfirmed-instruction` |
| `log-pending` | `event log-pending` |
| `stale-detected` | `event stale-detected` |
| `gate-review` | `gate review` |
| `gate-close` | `gate close` |
| `render` | `projection render` |
| `audit` | `projection audit` |
| `milestone-close` | `milestone close` |

注意：

- alias 的存在是为了兼容执行，不是为了继续并列暴露在主帮助面。
- 兼容期的 deprecation 信息以 help/doc note 为主，不默认增加运行时 stderr warning，避免污染脚本输出。

## Target Implementation Strategy

### 1. Argv Normalization Before Parsing

为了让 top-level help 真正收敛到 grouped surface，旧 flat commands 不应继续作为一组并列 subparsers 暴露在主 parser 上。

建议实现：

- 在 argparse 之前增加一个极薄的 argv normalization 层
- normalization 只作用于 top-level command slot：
  - 先跳过 root parser 的前置全局参数及其值（如 `--backend sqlite`）
  - 只检查紧随其后的第一个命令 token 是否命中 alias 表
  - 不扫描、不改写后续 token，更不触碰 `--task` 等 option value 中出现的 `open-gate` / `ping` 字样
- 将：
  - `open-gate ...`
  - `ack ...`
  - `render ...`
  - `milestone-close ...`
  等 flat 入口重写为 canonical grouped token 序列
- 对 `ping` 这类需要补齐动作名的别名，重写为：
  - `command send --name PING ...`

这样可以同时满足：

- `coord.py --help` 只展示 grouped top-level surface
- `coord.py open-gate ...` 仍继续可执行
- `coord.py open-gate --help` 可自然落到 `gate open --help`

### 2. Canonical Action Builder

`Stage C` 不宜继续让 `run_cli()` 维护一长串 if/elif，把 grouped/flat/`apply` 分别手工拼 payload。

建议引入一个最小 canonical command layer，例如：

- 解析结果先归一为：
  - `action`
  - `payload`
- 再统一交给 `_execute_action()`
- argparse attrs 到 payload 的清洗逻辑也统一收敛到这里，包括：
  - `_none_if_placeholder()`
  - `gate` / `gate_id`
  - `cmd` / `command`
  - 其他 grouped/alias 共享的字段归一化

约束：

- 不把整个 CLI 改造成大型 declarative framework
- 只做足以避免 grouped + alias 双倍重复接线的最小重构
- `_execute_action()` 的 action ids 暂时保持与当前 `apply` choices 一致

### 3. Help Surface and Documentation Policy

命令面切换后，帮助面和文档要明确区分两类入口：

- human/debug canonical path
  - grouped CLI
- machine-first structured path
  - `apply <action> --payload-file|--payload-stdin`

因此建议采用以下口径：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py) 的主 help、skill 快速示例、运行手册默认展示 grouped CLI
- `.claude/skills/devcoord-*/references/payloads.md` 继续保留 `apply` 的 JSON payload 参考
- `SKILL.md` 中的 narrative / checklist / quick examples 切为 grouped CLI，并在必要处补一句“structured payload path 仍可用”

## Implementation Shape

`Stage C` 复杂度评估为**中等**。

代码量不会比 `Stage B` 大，但它的风险在于：CLI regrouping 很容易造成“帮助面变了、真实 dispatch 漏了一条”这种低级回归。因此仍建议拆成 3 个切片。

补充约束：

- `Slice C1` 与 `Slice C2` 是概念切片，不允许出现“grouped 命令已 parse 成功但尚不可执行”的中间态。
- 若实现中发现两者代码量过小、拆分反而制造过渡复杂度，则允许同一实现切片内一起落地；但验收口径仍按 `C1 parser/help` 与 `C2 canonical dispatch` 两类结果检查。

### Slice C1: Grouped Parser Skeleton and Alias Normalization

目标：

- 引入 grouped top-level commands
- 新增 flat alias -> grouped path 的 argv normalization
- 保持 `apply` parser 不变

建议文件：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py)
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)

产出：

- `gate / command / event / projection / milestone` 顶层命令
- `open-gate` 等 flat aliases 的 token rewrite
- grouped 命令通过一条薄映射直接归一到现有 action ids，确保 `gate open`、`command ack`、`projection render` 等在 `C1` 结束时即可执行
- 顶层 `--help` 收敛到 grouped surface

验收：

- `coord.py --help` 不再把 flat aliases 作为主要 top-level command 暴露
- `coord.py open-gate ...` 仍能成功执行
- `coord.py open-gate --help` 与 `coord.py gate open --help` 对齐
- grouped 命令不会出现“能 parse、不能执行”的过渡态

### Slice C2: Canonical Dispatch and Output Compatibility

目标：

- 让 grouped/alias/`apply` 三种入口收敛到同一内部 action/payload 归一化路径
- 避免重复 if/elif 持续膨胀
- 保持输出与 guard 语义不变

建议文件：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py)
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)

产出：

- grouped 命令统一映射到现有 action ids
- `projection audit` 仍输出同样 JSON
- `projection render`、`gate close`、`milestone close` 等 guard 行为保持不变
- `--backend` 与现有 backend 选择逻辑不回归
- `command send` 至少可稳定承接 `PING`
- `argparse -> payload` 的清洗逻辑统一收敛到 canonical action builder，包括 `_none_if_placeholder()` 与字段 alias 归一

验收：

- grouped CLI 与旧 alias 对同一输入产生相同 store/service 结果
- `apply` 路径完全保持兼容
- 关键失败消息和 exit code 不发生意外破坏性变化

### Slice C3: Skill / Runbook Cutover and CLI Coverage

目标：

- 让 grouped CLI 成为新的对外说明口径
- 保留 `apply` 的 structured payload 参考
- 补足 CLI smoke coverage

建议文件：

- [`scripts/devcoord/coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py)
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)
- [`.claude/skills/devcoord-pm/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-pm/SKILL.md)
- [`.claude/skills/devcoord-backend/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-backend/SKILL.md)
- [`.claude/skills/devcoord-tester/SKILL.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-tester/SKILL.md)
- [`.claude/skills/devcoord-pm/references/payloads.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-pm/references/payloads.md)
- [`.claude/skills/devcoord-backend/references/payloads.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-backend/references/payloads.md)
- [`.claude/skills/devcoord-tester/references/payloads.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/skills/devcoord-tester/references/payloads.md)
- [`dev_docs/devcoord/beads_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/beads_control_plane.md) 或新增 stage-c 过渡说明文档

产出：

- skill narrative / checklist / quick examples 切到 grouped CLI
- payload reference 继续以 `apply` 为主
- alias mapping 表与兼容期说明落文档
- CLI smoke tests 覆盖 grouped + alias + `apply`

验收：

- PM / backend / tester 的新示例口径一致
- 旧 flat alias 仍可支撑兼容期操作
- `apply` payload 参考无需大改即可继续工作

## Test Strategy

`Stage C` 的核心测试不是协议语义测试，而是**命令面收敛不改行为**。建议测试面如下：

### 0. Argv Normalization Unit Tests

至少覆盖：

- `open-gate ...` 重写为 `gate open ...`
- `ping ...` 重写为 `command send --name PING ...`
- 含 root-level 全局参数的调用，如 `--backend sqlite open-gate ...`
- 不误改写 option value：
  - 例如 `ping --task "follow up open-gate"` 中的 `open-gate`
  - 例如 `heartbeat --task "waiting for ping ack"` 中的 `ping`

### 1. Grouped CLI Smoke Tests

至少覆盖：

- `gate open`
- `command ack`
- `command send --name PING`
- `event heartbeat`
- `projection render`
- `projection audit`
- `milestone close`

### 2. Flat Alias Compatibility Tests

至少覆盖：

- `open-gate`
- `ack`
- `ping`
- `gate-review`
- `gate-close`
- `render`
- `audit`
- `milestone-close`

### 3. Help Surface Tests

至少覆盖：

- top-level `--help` 只暴露 grouped canonical commands
- `open-gate --help` 能正确导向 grouped 语义

### 4. Structured `apply` Stability Tests

保持或补充：

- `apply init`
- `apply open-gate`
- `apply audit`

重点确认：

- JSON payload 形状不变
- JSON 输出不变
- 非交互路径不被 alias/deprecation 逻辑污染

## Risks

| # | 风险 | 影响 | 概率 | 缓解 |
| --- | --- | --- | --- | --- |
| R1 | grouped CLI 与 flat alias 各自接到不同 dispatch 路径 | 同名操作行为漂移 | 中 | 增加 canonical action builder，所有入口统一收敛到同一 action/payload |
| R2 | flat alias 仍作为 subparser 暴露 | help 面并未真正收敛 | 中 | 用 argv normalization 而非继续并列注册所有 flat subparsers |
| R3 | 为了 regrouping 顺手改 `apply` action ids | 机器化调用与 skill payload 全面回归 | 低 | 明确 `apply` action ids 保持不变 |
| R4 | skill 示例全部改成 `apply` 或全部改成 grouped，导致人机入口混淆 | 新口径不清晰 | 中 | narrative 用 grouped；payload reference 保持 `apply` |
| R5 | 把 `command send` 误扩成 STOP / WAIT / RESUME 全量协议补齐 | 范围蔓延，侵入 service 语义层 | 中 | `Stage C` 最小验收锚定到 `PING`；其余命令仅在可零语义增量复用时并入 |
| R6 | 提前改动 Stage D 的全局治理文档 | 范围蔓延、与当前 SSOT 冲突 | 中 | `Stage C` 仅更新命令示例与兼容说明，不做 `.beads` -> `.devcoord` 的全局治理改写 |

## Acceptance Criteria

- [ ] `coord.py --help` 的主帮助面已收敛为 grouped commands
- [ ] `gate / command / event / projection / milestone / init / apply` 全部可执行
- [ ] 旧 flat commands 在兼容期内继续可执行
- [ ] grouped CLI、flat alias、`apply` 共享同一条内部 action/payload 归一化路径
- [ ] `command send --name PING` 已成为 `ping` 的 canonical grouped path
- [ ] `apply` 的 action ids 与 JSON payload 形状保持兼容
- [ ] `projection audit` 的 JSON 输出不因 regrouping 改变
- [ ] devcoord 三个 skill 的 narrative 示例已切到 grouped CLI
- [ ] payload reference 仍保留 `apply` 的 machine-first 用法
- [ ] 文档已明确 canonical path 与 compatibility alias 的关系

## Resolved Positions

- `Stage C` 的“canonical path”定义为 grouped human/debug CLI，而不是 `apply`
  - 理由：本阶段要解决的是 help surface 与人类记忆负担，不是替换 machine-first payload 入口
- `apply` 继续保持现有 action vocabulary
  - 理由：避免把 regrouping 误扩大成机器接口变更
- `command send` 的 `Stage C` 最小验收只锚定 `PING`
  - 理由：当前 runtime 没有现成的 `STOP / WAIT / RESUME` 显式 service API；若在本阶段补齐，范围会从 CLI regrouping 蔓延到协议能力实现
- `command send --name PING` 在 `Stage C` 固定复用现有 `ping` 参数集
  - 理由：这样可以保持设计稿的 grouped 口径，同时避免为 `--name` 引入按命令名动态切换 argparse 参数集的额外复杂度
- compatibility alias 以 argv normalization 实现为优先
  - 理由：只有这样才能真正缩小顶层 help surface，而不是把旧命令继续并列摆在 parser 上
- skill 文档分两层切换：
  - `SKILL.md` 叙述与 quick examples 切到 grouped CLI
  - `references/payloads.md` 保留 `apply`
- `Stage C` 不提前修改全局治理文档中的 control-plane SSOT 口径
  - 理由：这属于 [`p2-devcoord_sqlite-control-plane_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07.md) 里 `Stage D` 的 beads cutover / closeout hardening 范围
- `event` 分组在 `Stage C` 先保持单层结构
  - 理由：当前子命令数量仍可控；若后续增长到帮助面再次失真，再在后续阶段评估二级分组或协议收敛
