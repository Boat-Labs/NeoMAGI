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

前置条件：

- 对应 [`design_docs/devcoord_sqlite_control_plane_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane_draft.md) 的 `Stage 0`，ADR 0050 必须先从 `proposed` 进入 `accepted`，再启动本计划的实施阶段。

## Goals

- 将 `devcoord` 的控制面真源从 `beads` 切换为 SQLite。
- 保持 `AGENTTEAMS.md` 的关键协议语义不回归：
  - 所有需 ACK 的指令 `STOP / WAIT / RESUME / GATE_OPEN / PING` 仍需 ACK 才生效
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
- 不迁移既有 `beads` control-plane 历史数据到 SQLite；切换时直接从空的 `.devcoord/control.db` 开始。
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

### 2. Store Abstraction Strategy

`Stage A` 不应再把 `CoordService` 继续绑定在 `IssueStore` 语义上。

新的过渡口径：

- `CoordStore` 成为 `CoordService` 的正式依赖接口。
- 现有 [`IssueStore`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py#L121) 仅作为迁移期遗留概念，不再继续扩张。
- 迁移期提供两个适配器：
  - `BeadsCoordStore`
    - 取代当前 `CliIssueStore`
    - 负责把 `beads` 上的 control-plane 对象翻译为 `CoordStore` 所需的持久化/查询操作
  - `MemoryCoordStore`
    - 取代当前 `MemoryIssueStore`
    - 继续服务快速单元测试
- `SQLiteCoordStore` 是 `CoordStore` 的目标后端，不再复用 issue-oriented 记录模型。

关键约束：

- service 层不再依赖 `IssueRecord` 风格的数据结构。
- 任何 `bd ... --json` 细节都必须被收拢在 `BeadsCoordStore` 内部，而不是继续泄漏到 service 层。
- `BeadsCoordStore` 的作用仅限于代码层渐进切换，不承担历史 control-plane 数据导入职责。

### 3. SQLite Data Model

最小对象面固定为 6 类：

- `milestones`
- `phases`
- `gates`
- `roles`
- `messages`
- `events`

其中三类最关键：

- `messages`
  - 保存需要 ACK 的 command，例如 `STOP`、`WAIT`、`RESUME`、`GATE_OPEN`、`PING`
- `events`
  - append-only 审计事件流
- `gates`
  - 聚合后的授权窗口

本计划内不追求通用 schema 平台，只追求当前 `devcoord` 协议的最小闭环。

### 4. Transaction Semantics

SQLite 可接受的前提是写入规则明确：

- 使用 `sqlite3` 标准库
- 开启 WAL mode
- `busy_timeout` 固定为 `5000ms`
- 所有命令写入在单事务内完成
- `event_seq` 分配与聚合状态更新同事务提交
- `ACK`、`gate-close`、`milestone-close` 都必须保留 fail-closed 行为
- 遇到 `SQLITE_BUSY` 时，仅允许一次短退避重试（例如 `250ms`）；再次失败则 fail-closed 并要求操作者重试，避免静默吞掉写冲突

### 5. Shared Path Resolution

`.devcoord/control.db` 的共享路径解析必须显式基于 `git-common-dir` 对应的共享仓库根，而不是依赖当前 worktree 的 `--show-toplevel` 结果。

约束：

- 多 worktree 必须指向同一个 shared root `.devcoord/control.db`
- `render` 输出仍写入当前共享仓库下的 `dev_docs/`
- `CoordPaths` 后续应从 `beads_dir` 迁移到 `control_root` / `control_db` 一类更直接的字段命名

### 6. Command Surface

`apply` 是并行保留的 machine-first 入口，不属于 grouped subcommands。

机器入口：

- `coord.py apply ...`

人类 / 调试入口：

- `coord.py init`
- `coord.py gate ...`
- `coord.py command ...`
- `coord.py event ...`
- `coord.py projection ...`
- `coord.py milestone ...`

关键口径：

- 精简的是 CLI 形状，不是协议事件语义。
- `open-gate`、`ack`、`heartbeat` 等旧命令在迁移期继续存在，但降级为 alias。

### 7. Projection Compatibility

继续生成：

- `dev_docs/logs/<phase>/<milestone>_<run-date>/heartbeat_events.jsonl`
- `dev_docs/logs/<phase>/<milestone>_<run-date>/gate_state.md`
- `dev_docs/logs/<phase>/<milestone>_<run-date>/watchdog_status.md`
- `dev_docs/progress/project_progress.md`

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

这里的“渐进切换”只指代码与命令面的渐进切换，不指历史 control-plane 数据迁移。历史 `coord` 记录在 cutover 时直接关闭/归档，不导入 SQLite。

因此不建议一次性大切换。对应设计草案中的 `Stage 0` 是 “ADR 0050 accepted”，本计划只覆盖其后的 4 个实施阶段：

1. `Stage A` Store abstraction
2. `Stage B` SQLite backend + render/audit cutover
3. `Stage C` Grouped CLI surface + compatibility aliases
4. `Stage D` Beads cutover + closeout workflow hardening

每个阶段都必须独立可验证，且都应有 fail-closed 回退点。

## Implementation Shape

### Stage A: Store Abstraction

目标：

- 在不改变外部行为的前提下，为 `CoordService` 引入 `CoordStore` 抽象
- 将 `CoordService` 对 [`IssueStore`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py#L121) 的依赖替换为 `CoordStore`

建议文件：

- `scripts/devcoord/store.py`（新）
- `scripts/devcoord/service.py`
- `scripts/devcoord/model.py`
- `tests/devcoord/*`

产出：

- `CoordStore` interface
- `BeadsCoordStore`
  - 作为 `CliIssueStore` 的迁移替代
- `MemoryCoordStore`
  - 作为 `MemoryIssueStore` 的迁移替代
- service 不再直接依赖 `bd ... --json` 细节
- service 层不再直接依赖 `IssueRecord` 风格数据结构

测试方法：

- 现有 `MemoryIssueStore` 路径的测试迁移为 `MemoryCoordStore`
- 保留 fast unit tests，验证 service 行为在 store 抽象切换后不回归

验收：

- 现有命令面行为不变
- 现有 tests / projection 输出不回归
- `CoordStore` 与 `BeadsCoordStore / MemoryCoordStore` 关系清晰，后续 Stage B 不需要继续触碰 `IssueStore`

### Stage B: SQLite Backend

目标：

- 新增 `.devcoord/control.db`
- 实现 `SQLiteCoordStore`
- 让 `render/audit` 读 SQLite 而不是 `beads`
- 固定共享路径解析：所有 worktree 都经由 shared root 指向同一 `.devcoord/control.db`

建议文件：

- `scripts/devcoord/sqlite_store.py`（新）
- `scripts/devcoord/service.py`
- `scripts/devcoord/coord.py`
- `.gitignore`（如需要）
- `tests/devcoord/*`
- `tests/integration/*`

产出：

- schema bootstrap
- transaction helper
- SQLite-backed query/write path
- `render/audit` 读 SQLite
- `.devcoord/` 本地状态目录落位

测试方法：

- 新增 SQLite 单元测试
  - schema bootstrap
  - 单事务写入
  - `ACK` / `gate-close` / `milestone-close` fail-closed
- 新增 SQLite 集成测试
  - `gate open -> ack -> review -> close`
  - `render -> audit`
- 增加至少一个多 worktree / shared-root 路径 smoke test
- 增加至少一个写冲突 smoke test，验证 `busy_timeout + retry-once + fail-closed`
- 验证空 SQLite store bootstrap 可直接支撑 fresh milestone，不依赖旧 `beads` control-plane 数据预热

验收：

- `gate open -> ack -> review -> close` 在不写 `beads` 的情况下成立
- `audit.reconciled` 行为与当前一致
- projection 仍能生成
- 所有需 ACK 的指令 `STOP / WAIT / RESUME / GATE_OPEN / PING` 在 SQLite 后端上仍保持 “pending -> effective” 语义
- `target_commit` 写入、读取、projection 与 audit 结果一致
- 多 worktree 指向同一 shared `.devcoord/control.db`

### Stage C: Grouped CLI Surface

目标：

- 将 `coord.py` 顶层命令面收敛为 grouped surface
- 保留旧 flat commands 作为 aliases

建议文件：

- `scripts/devcoord/coord.py`
- `dev_docs/devcoord/...`（后续文档适配）
- `.claude/skills/devcoord-pm/SKILL.md`
- `.claude/skills/devcoord-backend/SKILL.md`
- `.claude/skills/devcoord-tester/SKILL.md`

产出：

- `gate / command / event / projection / milestone` 分组命令
- `apply` 保持 machine-first 入口
- 旧命令映射表与 deprecation 注记
- `gate open` 作为 canonical path
- `open-gate` 仅保留兼容 alias

测试方法：

- CLI smoke tests 覆盖 grouped commands
- compatibility smoke tests 覆盖旧 flat aliases
- 以最小 PM/backend/tester prompt 样例验证 skill 不需要一次性重写

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
- `.claude/skills/devcoord-pm/SKILL.md`
- `.claude/skills/devcoord-backend/SKILL.md`
- `.claude/skills/devcoord-tester/SKILL.md`

产出：

- `beads` 仅保留 backlog/work issue
- closeout 流程改为 SQLite store closeout
- `milestone-close` 语义对齐新后端
- 历史 `coord` beads 记录统一关闭或归档，不导入 `.devcoord/control.db`
- `CoordPaths.beads_dir` 退役或重命名
- `--beads-dir` / `--bd-bin` / `--dolt-bin` 进入 deprecated -> remove 流程
- 文档中的 “repo 根 `.beads` 为 SSOT” 全部改为 `.devcoord/control.db`
- `beads_control_plane.md` 被 supersede 或归档说明清楚

测试方法：

- 端到端 closeout smoke test
- 手动检查 `bd list --status open`
- 手动检查 `render -> audit -> milestone-close`

验收：

- `bd list --status open` 不再被 `coord` 对象污染
- `devcoord` closeout 不再依赖 beads sync
- 旧 control-plane 文档被 supersede 或归档说明清楚
- `AGENTTEAMS.md`、`AGENTS.md`、`CLAUDE.md` 与 3 个 devcoord skills 已切换到 SQLite control-plane 口径

## Risks

| # | 风险 | 影响 | 概率 | 缓解 |
| --- | --- | --- | --- | --- |
| R1 | store 切换导致协议语义回归 | Gate / ACK / recovery 失真 | 中 | 先做 abstraction，再做 backend cutover；保留旧命令与 projection 验证 |
| R2 | SQLite 锁语义处理不当 | 多 worktree 写入冲突、假死 | 中 | WAL + `busy_timeout=5000ms` + 单事务写入；遇到 `SQLITE_BUSY` 仅重试一次后 fail-closed；针对 ACK / gate-close 增加并发测试 |
| R3 | grouped CLI 一次性切换过猛 | skill / prompt / PM runbook 失效 | 中 | 保留 aliases；分阶段切换文档和 skill |
| R4 | beads cutover 不彻底 | backlog 与 control-plane 继续混杂 | 中 | Stage D 明确 hard cutover checklist；移除 beads 参数/路径后再检查 `bd open` 视图 |
| R5 | projection 与真源不一致 | 审计链失真 | 中 | `render -> audit` 继续作为 gate-close 前硬条件 |
| R6 | 把 devcoord 过度做成平台 | 范围蔓延、延误主线开发 | 中 | 只服务当前协议，不为未来多机/通用调度预建设计 |

## Acceptance Criteria

- [ ] `devcoord` 不再依赖 `beads` 作为 control-plane store
- [ ] `beads / bd` 重新只承载 backlog / issue graph 语义
- [ ] `.devcoord/control.db` 成为新的控制面真源
- [ ] `AGENTTEAMS.md` 协议关键路径不回归：
  - [ ] `STOP / WAIT / RESUME / GATE_OPEN / PING` 均需 ACK 才生效
  - [ ] `target_commit` 写入、读取、projection 与 audit 一致
  - [ ] `RECOVERY_CHECK / STATE_SYNC_OK` 继续成立
  - [ ] `render -> audit -> GATE_CLOSE` 继续成立
- [ ] projection 继续生成 `dev_docs/logs/<phase>/<milestone>_<run-date>/heartbeat_events.jsonl`、`dev_docs/logs/<phase>/<milestone>_<run-date>/gate_state.md`、`dev_docs/logs/<phase>/<milestone>_<run-date>/watchdog_status.md`、`dev_docs/progress/project_progress.md`
- [ ] `coord.py` 顶层命令面比当前更少、更易记
- [ ] 旧 flat commands 在兼容期内继续可用
- [ ] `bd list --status open` 不再被 `coord` 对象污染

## Resolved Positions

- `.devcoord/` 进入 `.gitignore`
  - 理由：它是本地控制面状态，不应进入仓库历史
- `schema_version` 只保留在 SQLite metadata / schema 内部
  - 理由：sidecar `json` 会重复表达同一事实，增加维护成本
- `gate open` 作为 canonical path
  - 理由：Stage C 的目标就是收敛命令面，`open-gate` 仅作为兼容 alias 保留一段过渡期
