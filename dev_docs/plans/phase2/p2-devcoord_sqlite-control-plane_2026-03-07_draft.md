# P2-Devcoord 实施计划（草稿）：SQLite Control Plane

- Date: 2026-03-07
- Status: draft
- Scope: `P2-Devcoord` only; decouple `devcoord` from `beads` and migrate the coordination control plane to a dedicated SQLite store
- Basis:
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`design_docs/devcoord_sqlite_control_plane_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane_draft.md)
  - [`decisions/0042-devcoord-control-plane-beads-ssot-with-dev-docs-projection.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0042-devcoord-control-plane-beads-ssot-with-dev-docs-projection.md)
  - [`decisions/0043-devcoord-direct-script-entrypoint-instead-of-just-wrapper.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0043-devcoord-direct-script-entrypoint-instead-of-just-wrapper.md)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)

## Context

当前 `devcoord` 的核心问题已经从“是否需要结构化控制面”转变为“控制面是否应继续复用 issue/backlog 系统”。

现状有三个明显症状：

- `bd list --status open` 同时混入真实 backlog 任务和 `coord` milestone / phase / gate / agent / event 对象，open 视图失去直接可读性。
- `coord` 对象很多真实状态存在 metadata 中，例如 `gate_state`、`phase_state`、`agent_state`，而不是 `issue.status`，这与 issue tracker 的直觉语义天然冲突。
- `milestone-close` 或 `render/audit` 稍有遗漏，就会让 control-plane 对象继续显示为 open，进一步污染工作视图。

这说明：

- `devcoord` 仍然需要 deterministic runtime；
- 但 `beads` 更适合 backlog / issue graph，而不适合作为控制协议状态机的长期宿主。

因此本计划的任务不是重新设计协议，而是：

- 保留 `AGENTTEAMS.md` 的 Gate / ACK / recovery / audit 语义；
- 将 `devcoord` 从 `beads` store 解耦；
- 迁移到一个更小、更专用的 `.devcoord/control.db`；
- 同时把 `coord.py` 从扁平命令面收敛到更可读的 grouped surface。

## Core Decision

`P2-Devcoord` 采用**协议语义保持不变、存储后端与命令面收敛**的迁移策略，而不是重写整个协作控制系统：

1. `beads / bd` 回到 backlog / issue graph / Jira 面，不再承载 control-plane 对象。
2. `scripts/devcoord` 继续作为唯一协议语义实现层。
3. 新增 `.devcoord/control.db` 作为 SQLite control-plane SSOT。
4. `dev_docs/logs/<phase>/...` 与 `dev_docs/progress/project_progress.md` 继续保持 projection 角色，不恢复手写。
5. `coord.py` 保留 `apply` 作为 machine-first 入口，同时把人类可见 CLI 收敛为 `init / gate / command / event / projection / milestone / apply`。
6. 迁移期保留旧 flat commands 作为 compatibility aliases，避免一次性打断现有 PM / teammate 提示词与 skill。

这意味着本计划优先解决两件事：

- 存储语义去混淆
- 命令面去噪

而不是：

- 修改 `AGENTTEAMS.md` 协议本身
- 扩大 `devcoord` 能力边界

## Goals

- 将 `devcoord` 的控制面真源从 `beads` 切换为 SQLite。
- 保持 `AGENTTEAMS.md` 的关键协议语义不回归：
  - `GATE_OPEN` 需 ACK 才生效
  - `target_commit` pin
  - `RECOVERY_CHECK / STATE_SYNC_OK`
  - `render -> audit -> GATE_CLOSE`
- 让 `bd list --status open` 回到 backlog / work issue 视角，不再被 control-plane 对象污染。
- 为 `.devcoord/control.db` 建立最小且稳定的数据模型。
- 为 `coord.py` 建立更小的 grouped command surface，并保留兼容期。
- 保持 projection 输出类别不变，避免打断现有审阅和证据链。

## Non-Goals

- 不改变产品运行时 PostgreSQL 17 基线。
- 不把产品数据、memory 数据或运行时 user data 写入 SQLite。
- 不修改 `AGENTTEAMS.md` 的协议规则本身。
- 不把 `devcoord` 降级回文档驱动。
- 不在本计划内引入多机分布式协调。
- 不在本计划内重新设计 PM / backend / tester skill 的全部内容，只做必要适配。
- 不在本计划内把 `coord.py` 扩张成通用 workflow engine。

## Proposed Architecture

### 1. Store Boundary

新的责任边界如下：

- `beads / bd`
  - backlog / work issue / epic / review follow-up
- `scripts/devcoord`
  - protocol rules
  - validation
  - ordering
  - reconciliation
  - projection generation
- `.devcoord/control.db`
  - control-plane persistence / query / audit history
- `dev_docs/logs/*` + `project_progress.md`
  - projection / evidence only

关键原则：

- `scripts/devcoord` 仍是协议语义唯一实现层。
- SQLite 只是控制面存储后端。
- `dev_docs` 不是控制面真源。

### 2. SQLite Data Model

最小对象面固定为 6 类：

- `milestones`
- `phases`
- `gates`
- `roles`
- `messages`
- `events`

其中三类最关键：

- `messages`
  - 保存需要 ACK 的 command，例如 `GATE_OPEN`、`PING`
- `events`
  - append-only 审计事件流
- `gates`
  - 聚合后的授权窗口

本计划内不追求通用 schema 平台，只追求当前 `devcoord` 协议的最小闭环。

### 3. Transaction Semantics

SQLite 可接受的前提是写入规则明确：

- 使用 `sqlite3` 标准库
- 开启 WAL mode
- 所有命令写入在单事务内完成
- `event_seq` 分配与聚合状态更新同事务提交
- `ACK`、`gate-close`、`milestone-close` 都必须保留 fail-closed 行为

### 4. Command Surface

机器入口：

- `coord.py apply ...`

人类 / 调试入口：

- `coord.py init`
- `coord.py gate ...`
- `coord.py command ...`
- `coord.py event ...`
- `coord.py projection ...`
- `coord.py milestone ...`
- `coord.py apply ...`

关键口径：

- 精简的是 CLI 形状，不是协议事件语义。
- `open-gate`、`ack`、`heartbeat` 等旧命令在迁移期继续存在，但降级为 alias。

### 5. Projection Compatibility

继续生成：

- `heartbeat_events.jsonl`
- `gate_state.md`
- `watchdog_status.md`
- `project_progress.md`

迁移要求：

- projection 文件格式尽量保持兼容
- `render -> audit -> read projection` 顺序不变
- 旧审阅文档、旧 PM action plans 不需要为了 projection 格式变化重写

## Delivery Strategy

本计划复杂度判断为**高**，但它不是“高代码量”，而是“高协议回归风险”。

难点在于：

- 要替换控制面真源，但不能破坏既有协议语义
- 要让 `beads` 退回 backlog 角色，但不能丢失审计链
- 要精简命令面，但不能一次性打断现有 prompts / skills / runbooks

因此不建议一次性大切换，建议拆成 4 个窄阶段：

1. `Stage A` Store abstraction
2. `Stage B` SQLite backend + render/audit cutover
3. `Stage C` Grouped CLI surface + compatibility aliases
4. `Stage D` Beads cutover + closeout workflow hardening

每个阶段都必须独立可验证，且都应有 fail-closed 回退点。

## Implementation Shape

### Stage A: Store Abstraction

目标：

- 在不改变外部行为的前提下，为 `CoordService` 引入 `CoordStore` 抽象

建议文件：

- `scripts/devcoord/store.py`（新）
- `scripts/devcoord/service.py`
- `scripts/devcoord/model.py`

产出：

- `CoordStore` interface
- 当前 `beads` 路径的 store adapter 明确化
- service 不再直接依赖 `bd ... --json` 细节

验收：

- 现有命令面行为不变
- 现有 tests / projection 输出不回归

### Stage B: SQLite Backend

目标：

- 新增 `.devcoord/control.db`
- 实现 `SQLiteCoordStore`
- 让 `render/audit` 读 SQLite 而不是 `beads`

建议文件：

- `scripts/devcoord/sqlite_store.py`（新）
- `scripts/devcoord/service.py`
- `scripts/devcoord/coord.py`
- `.gitignore`（如需要）

产出：

- schema bootstrap
- transaction helper
- SQLite-backed query/write path
- `render/audit` 读 SQLite

验收：

- `gate open -> ack -> review -> close` 在不写 `beads` 的情况下成立
- `audit.reconciled` 行为与当前一致
- projection 仍能生成

### Stage C: Grouped CLI Surface

目标：

- 将 `coord.py` 顶层命令面收敛为 grouped surface
- 保留旧 flat commands 作为 aliases

建议文件：

- `scripts/devcoord/coord.py`
- `dev_docs/devcoord/...`（后续文档适配）

产出：

- `gate / command / event / projection / milestone` 分组命令
- `apply` 保持 machine-first 入口
- 旧命令映射表与 deprecation 注记

验收：

- grouped commands 可执行
- 旧 flat commands 仍可执行
- PM / teammate 提示词和 skill 不会被一次性打断

### Stage D: Beads Cutover and Closeout Hardening

目标：

- 停止将 control-plane 对象写入 `beads`
- 固化新的 closeout 顺序

建议文件：

- `scripts/devcoord/*`
- `AGENTTEAMS.md`
- `AGENTS.md`
- `CLAUDE.md`
- `dev_docs/devcoord/...`

产出：

- `beads` 仅保留 backlog/work issue
- closeout 流程改为 SQLite store closeout
- `milestone-close` 语义对齐新后端

验收：

- `bd list --status open` 不再被 `coord` 对象污染
- `devcoord` closeout 不再依赖 beads sync
- 旧 control-plane 文档被 supersede 或归档说明清楚

## Risks

| # | 风险 | 影响 | 概率 | 缓解 |
| --- | --- | --- | --- | --- |
| R1 | store 切换导致协议语义回归 | Gate / ACK / recovery 失真 | 中 | 先做 abstraction，再做 backend cutover；保留旧命令与 projection 验证 |
| R2 | SQLite 锁语义处理不当 | 多 worktree 写入冲突、假死 | 中 | WAL + busy timeout + 单事务写入；针对 ACK / gate-close 增加并发测试 |
| R3 | grouped CLI 一次性切换过猛 | skill / prompt / PM runbook 失效 | 中 | 保留 aliases；分阶段切换文档和 skill |
| R4 | beads cutover 不彻底 | backlog 与 control-plane 继续混杂 | 高 | Stage D 明确 hard cutover；closeout 后检查 `bd open` 视图 |
| R5 | projection 与真源不一致 | 审计链失真 | 中 | `render -> audit` 继续作为 gate-close 前硬条件 |
| R6 | 把 devcoord 过度做成平台 | 范围蔓延、延误主线开发 | 中 | 只服务当前协议，不为未来多机/通用调度预建设计 |

## Acceptance Criteria

- [ ] `devcoord` 不再依赖 `beads` 作为 control-plane store
- [ ] `beads / bd` 重新只承载 backlog / issue graph 语义
- [ ] `.devcoord/control.db` 成为新的控制面真源
- [ ] `AGENTTEAMS.md` 协议关键路径不回归：
  - [ ] `GATE_OPEN` 需 ACK 才生效
  - [ ] `RECOVERY_CHECK / STATE_SYNC_OK` 继续成立
  - [ ] `render -> audit -> GATE_CLOSE` 继续成立
- [ ] projection 继续生成 `heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md`、`project_progress.md`
- [ ] `coord.py` 顶层命令面比当前更少、更易记
- [ ] 旧 flat commands 在兼容期内继续可用
- [ ] `bd list --status open` 不再被 `coord` 对象污染

## Open Questions

- `.devcoord/control.db` 是否需要进入 `.gitignore`，还是已有 ignore 规则已足够表达“本地控制面状态不入库”？
- `schema_version` 只保留在 SQLite metadata 中，还是保留 sidecar `schema_version.json` 方便人工检查？
- `gate open` 是先作为 alias 过渡，还是直接成为替代 `open-gate` 的 canonical path？
