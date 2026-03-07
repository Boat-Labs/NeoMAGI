# P2-Devcoord Stage B 实施计划（草稿）：SQLite 后端与 Render/Audit 切换

- Date: 2026-03-07
- Status: draft
- Scope: `P2-Devcoord Stage B` only; implement `SQLiteCoordStore`, bootstrap `.devcoord/control.db`, and make `render/audit` run against SQLite
- Track Type: parallel development-process repair track; outside the `P2-M*` product milestone series
- Basis:
  - [`dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07.md)
  - [`dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md)
  - [`design_docs/devcoord_sqlite_control_plane_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane_draft.md)
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)

## Context

`Stage A` 解决的是 store boundary：`CoordService` 不再直接绑定 issue-oriented store。

`Stage B` 的任务才是把这个抽象接到真正的 SQLite control-plane store 上，并让以下能力在**不依赖 `beads` control-plane 数据**的情况下成立：

- fresh milestone bootstrap
- `gate open -> ack -> review -> close`
- `render`
- `audit`
- `close_milestone`

这里有一个关键边界需要再次写清：

- `Stage B` 不做历史 control-plane 数据迁移
- 旧 `coord` beads 记录已经作为实验历史被关闭/归档
- SQLite 只服务 fresh milestone

因此 `Stage B` 的核心不是“兼容旧数据”，而是“让新控制面从空库启动就能完整工作”。

## Precondition

`Stage B` 启动前必须满足：

- [`p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md) 已实现并合入
- `CoordStore`、`BeadsCoordStore`、`MemoryCoordStore` 已存在
- `CoordService` 已不再直接依赖 `IssueStore` / `IssueRecord`

如果 `Stage A` 还未落地，`Stage B` 不应提前开工。

## Core Decision

`Stage B` 采用**SQLite 真源落地，但不做 final cleanup** 的策略：

1. 新增 `.devcoord/control.db` 作为新的 control-plane SSOT。
2. 实现 `SQLiteCoordStore`，覆盖 milestone / phase / gate / role / message / event 六类逻辑对象。
3. `render` 和 `audit` 切到 SQLite 数据源。
4. `coord.py` 保持现有命令集，不做 grouped CLI；必要时只允许做最小 backend wiring。
5. `Stage B` 不做 dual-write：
   - 选中的 runtime backend 只写一份真源
   - SQLite 路径不再镜像写 `beads`
6. `BeadsCoordStore` 在 `Stage B` 仍保留为兼容/回退适配器，但不再是目标架构。
7. `_locked()` 文件锁机制保持不变。

这意味着 `Stage B` 要交付的是“SQLite 路径可完整运行”，不是“所有旧路径和旧文档都清理完”。

## Goals

- 引入 `.devcoord/control.db`
- 实现 `SQLiteCoordStore`
- 支持从空 SQLite store 启动 fresh milestone
- 让 `render/audit` 读 SQLite
- 固定多 worktree 共用同一 `.devcoord/control.db` 的路径解析
- 保持 `AGENTTEAMS.md` 协议关键语义不回归

## Non-Goals

- 不做历史 `beads` control-plane 数据迁移
- 不做 grouped CLI / alias 收敛
- 不移除 `BeadsCoordStore`
- 不删除 `--beads-dir` / `--bd-bin` / `--dolt-bin`
- 不修改 `AGENTTEAMS.md` 协议规则本身
- 不做 `Stage D` 的文档总清理、参数退役和最终 cutover
- 不引入 typed records 大重构
- 不改 `_locked()` 的并发模型

## Current Baseline

`Stage B` 之前，系统应处于如下状态：

- `CoordStore` 已存在
- `BeadsCoordStore` 是默认真实后端
- `MemoryCoordStore` 是测试 double
- `CoordPaths` 仍主要围绕 `beads` 路径命名
- `render/audit` 仍读取现有后端记录

因此 `Stage B` 的真实难点有三个：

- 让 SQLite schema 与现有 control-plane 语义对齐
- 让多 worktree 共享路径稳定
- 让 `render/audit` 在 SQLite 上继续给出与当前同类的证据

还需要显式承认一个实现事实：

- `Stage A` 的 `CoordRecord` seam 是过渡形态，不是 SQLite 时代的最终领域模型
- `Stage B` 不会把整个系统重写成 typed records，但会允许 store/query seam 和 service helper 向 typed/fielded SQLite 模型靠拢
- 因此 `Stage B` 的工作量不只是新增一个 adapter；它包含一部分 service 读写路径改造

## Target Architecture

### 1. Store Boundary

`Stage B` 后的新边界应是：

- `CoordService`
  - protocol rules
  - validation
  - ordering
  - projection generation
- `SQLiteCoordStore`
  - SQLite persistence
  - query helpers
  - transactional writes
- `BeadsCoordStore`
  - 兼容/回退路径
  - 不再是目标默认控制面

关键约束：

- `SQLiteCoordStore` 不向 `beads` 写 mirror 数据
- `CoordService` 不关心底层是 SQLite 还是 beads

### 2. SQLite Schema

`SQLiteCoordStore` 应覆盖 6 类逻辑对象：

- `milestones`
- `phases`
- `gates`
- `roles`
- `messages`
- `events`

必须保持的语义：

- `messages`
  - 覆盖所有需 ACK 的指令
- `events`
  - append-only
- `gates`
  - `pending / effective / closed`
- `target_commit`
  - 必须作为一等字段存在，不得只藏在弱类型 payload 里

`schema_version` 的决议在本阶段固定为：

- 只保留在 SQLite 内部 metadata / schema 中
- 不引入 sidecar `schema_version.json`
- 遇到不兼容 schema version 时直接 fail-closed
  - 不做 forward migration
  - 不做兼容降级
  - 操作者删除本地 `.devcoord/` 后重新 `init`

### 3. Fresh Bootstrap

SQLite 路径必须支持：

- 空 `.devcoord/`
- 空 `control.db`
- 第一次执行 `init`
- 随后完整跑通一个 fresh milestone

明确禁止：

- 从旧 `beads` control-plane 导入数据
- 为了兼容旧 milestone 而设计 bootstrap 分支

### 4. Shared Path Resolution

Stage B 必须固定共享路径解析：

- 所有 worktree 指向同一个 shared root `.devcoord/control.db`
- 共享根必须基于 `git-common-dir` 解析
- 不能依赖当前 worktree 的 `--show-toplevel` 作为唯一依据

同时需要落地两个实现结果：

- `CoordPaths` 命名从 `beads_dir` 向 `control_root` / `control_db` 一类语义迁移
- `.devcoord/` 加入 `.gitignore`
- `beads_dir` 在 `Stage B` 期间可暂时保留为兼容字段，但不再作为 SQLite 路径真源

### 5. Concurrency and Transactions

本阶段继续保留文件锁模型，但 SQLite 自身也必须满足：

- WAL mode
- `busy_timeout = 5000ms`
- 单事务写入
- `event_seq` 与聚合状态更新同事务提交
- 遇到 `SQLITE_BUSY` 时只允许一次短退避重试，之后 fail-closed

也就是说：

- 文件锁仍是外层串行保护
- SQLite 事务是内层一致性保护

### 6. Render / Audit Cutover

`render` 和 `audit` 是 `Stage B` 的真正验收面。

必须保证：

- SQLite 数据能生成与当前同类的 projection：
  - `heartbeat_events.jsonl`
  - `gate_state.md`
  - `watchdog_status.md`
  - `project_progress.md`
- `audit.reconciled`
  - 语义不回归
- `pending_ack_messages`
  - 仍能正确识别
- `open_gates`
  - 仍能正确识别

### 7. Service Adaptation Boundary

`Stage B` 不只是“换掉 store 实现”，还必须允许一定深度的 service 改造。

明确 in-scope：

- 把当前依赖 metadata bag 的部分 helper 改成通过 `SQLiteCoordStore` query helpers 读取 typed/fielded 数据
- 把当前 “先组装 metadata dict，再 create/update record” 的部分路径改成更贴近 SQLite schema 的写入方式
- 让 `render/audit/close_milestone` 直接建立在 SQLite query 结果上

明确 out-of-scope：

- 一次性把整个 runtime 改写成全 typed domain object 系统
- 在 `Stage B` 重写全部协议语义或命令面

### 8. Runtime Selection

`Stage B` 不做命令面精简，但需要决定 SQLite 如何进入运行时。

本计划倾向：

- 允许最小 backend wiring，让 `coord.py` 可以运行在 SQLite 后端上
- 不新增大规模命令树
- 不在本阶段设计长期多后端切换产品面

更保守的实现原则：

- 优先保持现有命令名称
- 如确需 backend 选择，只允许一个最小、临时、可后续移除的选择机制
- 不在 `Stage B` 引入新的操作心智负担

## Complexity Assessment

`Stage B` 复杂度评估为**中高**，明显高于 `Stage A`。

主要原因：

- 它第一次把抽象接口接到真实新后端
- 它同时涉及路径、schema、事务、projection、audit
- 它必须在 fresh-start 前提下完整跑通 control-plane

因此 `Stage B` 不应作为单块实现，建议至少拆成 3 个切片：

1. `B1` Paths and schema bootstrap
2. `B2` SQLite runtime write/read path
3. `B3` Render/audit cutover and integration validation

## Implementation Shape

### Slice B1: Paths and Schema Bootstrap

目标：

- 引入 `.devcoord/control.db`
- 落地 shared-root 路径解析
- 完成 schema bootstrap
- 固定 `.gitignore`

建议文件：

- `scripts/devcoord/model.py`
- `scripts/devcoord/sqlite_store.py`（新）
- `scripts/devcoord/coord.py`
- `.gitignore`

产出：

- `control_root` / `control_db` 路径语义
- schema bootstrap
- SQLite metadata / schema version
- 空库可初始化
- schema version mismatch fail-closed

验收：

- 新仓库或空 `.devcoord/` 可自动 bootstrap
- `.devcoord/control.db` 不进 Git
- 多 worktree 指向同一 shared root

### Slice B2: SQLite Runtime Write/Read Path

目标：

- 实现 `SQLiteCoordStore`
- 跑通完整控制面读写
- 让 `CoordService` 在 SQLite 后端上可工作

建议文件：

- `scripts/devcoord/sqlite_store.py`
- `scripts/devcoord/service.py`
- `scripts/devcoord/coord.py`

产出：

- milestone / phase / gate / role / message / event persistence
- ACK 生效路径
- `target_commit` persistence
- `close_milestone` 可在 SQLite 上运行
- service 层必要的 query/write helper 改造
  - 允许从 metadata bag 风格访问转向 store query helpers
  - 不要求引入完整 typed records 公共模型

验收：

- `init -> open_gate -> ack -> phase_complete -> gate_review -> gate_close -> close_milestone` 成立
- 所有需 ACK 指令 `STOP / WAIT / RESUME / GATE_OPEN / PING` 的 `pending -> effective` 语义成立
- 不写 `beads` control-plane 数据

### Slice B3: Render/Audit Cutover and Integration Validation

目标：

- `render/audit` 改读 SQLite
- 验证 projection 与协议审计不回归
- 验证 fresh-start 和并发 smoke cases

建议文件：

- `scripts/devcoord/service.py`
- `tests/test_devcoord.py`
- `tests/integration/*`

产出：

- SQLite-backed render
- SQLite-backed audit
- integration coverage

验收：

- projection 继续生成：
  - `dev_docs/logs/<phase>/<milestone>_<run-date>/heartbeat_events.jsonl`
  - `dev_docs/logs/<phase>/<milestone>_<run-date>/gate_state.md`
  - `dev_docs/logs/<phase>/<milestone>_<run-date>/watchdog_status.md`
  - `dev_docs/progress/project_progress.md`
- `audit.reconciled` 行为与当前一致
- 至少 1 个多 worktree 路径 smoke test
- 至少 1 个写冲突 smoke test
- fresh-start milestone 无需旧数据预热

## Test Strategy

`Stage B` 必须新增真实 SQLite 测试，不再只靠内存 store 回归。

建议测试面：

- 单元测试
  - schema bootstrap
  - record create/update/query
  - transaction fail-closed
- 集成测试
  - gate lifecycle
  - `render -> audit`
  - `close_milestone`
- smoke tests
  - multi-worktree shared path
  - `SQLITE_BUSY` 写冲突

明确不要求：

- Stage B 就完成 CLI regrouping 测试
- Stage B 就覆盖 Stage D 的文档/skill cutover

## Risks

| # | 风险 | 影响 | 概率 | 缓解 |
| --- | --- | --- | --- | --- |
| R1 | SQLite schema 与协议语义错位 | gate / ack / audit 回归 | 中 | 先做 schema bootstrap，再做 runtime write/read，再做 render/audit |
| R2 | shared-root 路径解析出错 | 多 worktree 各写各的 control.db | 中 | 强制基于 `git-common-dir` 解析，并加 smoke test |
| R3 | transaction / busy handling 不稳 | 偶发假死或写入丢失 | 中 | WAL + `busy_timeout=5000ms` + retry-once + fail-closed |
| R4 | Stage B 顺手开始做 CLI regrouping | 范围蔓延 | 低 | 明确 grouped CLI 完全 out of scope |
| R5 | 仍然试图兼容旧 beads 数据 | 实现复杂度上升 | 低 | 明确 fresh-start only，不设计导入链路 |

## Acceptance Criteria

- [ ] `.devcoord/control.db` 成功落地
- [ ] `.devcoord/` 进入 `.gitignore`
- [ ] `SQLiteCoordStore` 可用
- [ ] fresh-start milestone 可从空 SQLite store 跑通
- [ ] `render/audit` 读 SQLite
- [ ] 所有需 ACK 的指令在 SQLite 后端上仍保持正确生效语义
- [ ] `target_commit` 写入、读取、projection、audit 一致
- [ ] `close_milestone` 可在 SQLite 后端上完成
- [ ] 不写 `beads` control-plane 数据
- [ ] 不引入历史数据迁移逻辑
- [ ] 不引入 grouped CLI

## Resolved Positions

- `Stage B` 的成功标准是“SQLite 路径可独立运行”，不是“beads 兼容路径彻底消失”。
- `Stage B` 不做 dual-write；一个运行中的 milestone 只应有一个 control-plane 真源。
- `Stage B` 只服务 fresh-start，不为旧 `coord` 历史设计导入链路。
- `Stage B` 完成后，下一步才进入 `Stage C` 的命令面精简，而不是在这里顺手改命令集。
