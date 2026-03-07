# P2-Devcoord Stage A 实施计划：CoordStore 抽象层

- Date: 2026-03-07
- Status: approved
- Scope: `P2-Devcoord Stage A` only; introduce `CoordStore` and remove `CoordService`'s direct dependency on issue-oriented storage
- Basis:
  - [`dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane_2026-03-07_draft.md)
  - [`design_docs/devcoord_sqlite_control_plane_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane_draft.md)
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)

## Context

`P2-Devcoord` 总方案已经接受：`devcoord` 要从 `beads` 语义解耦，未来切到 SQLite control-plane store。

但当前代码里最紧的耦合点还在 service 层：

- [`CoordService`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/service.py) 直接依赖 [`IssueStore`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py#L121)
- service 内部大量逻辑以 [`IssueRecord`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py#L53) 为基本操作对象
- CLI 默认构造 [`CliIssueStore`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py#L152)
- 现有测试几乎全部依赖 [`MemoryIssueStore`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py#L290) 和 [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)

如果不先把这一层抽出来，后续 `Stage B` 每次替换 store 都会继续穿透到 service 和测试面，回归成本会越来越高。

因此 `Stage A` 的任务不是“引入 SQLite”，而是先把：

- protocol runtime
- persistence contract
- tests double

这三层拆开。

## Core Decision

`Stage A` 采用**抽象先行、行为不变**的策略：

1. 新增 `CoordStore` 作为 `CoordService` 的正式依赖接口。
2. 现有 `IssueStore`、`CliIssueStore`、`MemoryIssueStore` 不再作为长期概念继续扩张。
3. 迁移期提供两个显式 adapter：
   - `BeadsCoordStore`
   - `MemoryCoordStore`
4. `CoordService` 在 `Stage A` 结束后不再直接依赖 `IssueStore` / `IssueRecord` 命名。
5. `Stage A` 不引入 `.devcoord/control.db`，也不改 `render/audit` 的真源。

这里的目标是给 `Stage B` 准备一个稳定插槽，而不是提前实现新后端。

## Goals

- 为 `CoordService` 建立稳定的 `CoordStore` 依赖边界。
- 把 `beads` 专属持久化细节收拢到 `BeadsCoordStore` 内部。
- 把测试 double 从 `MemoryIssueStore` 升级成 `MemoryCoordStore`。
- 保持现有 CLI 行为、协议语义、projection 输出不回归。
- 让 `Stage B` 可以新增 `SQLiteCoordStore`，而不再大面积触碰 service。

## Non-Goals

- 不引入 `.devcoord/control.db`
- 不实现 `SQLiteCoordStore`
- 不改变 `render/audit` 的数据来源
- 不修改 `AGENTTEAMS.md` 协议
- 不引入 grouped CLI
- 不删除 `--beads-dir` / `--bd-bin` / `--dolt-bin`
- 不迁移任何历史 control-plane 数据
- 不改变 `coord.py apply` 或现有 flat commands 的对外行为

## Current Baseline

当前最重要的实现事实：

- [`CoordService.store`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/service.py#L29) 直接声明为 `IssueStore`
- service 内部大量 helper 都以 `Sequence[IssueRecord]` 为输入输出
- [`coord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/coord.py#L417) 默认直接构造 `CliIssueStore`
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py) 是主要回归面，当前绑定 `MemoryIssueStore`

因此 `Stage A` 的关键难点不是协议，而是**在不改行为的前提下改内部语言**。

## Target Architecture

### 1. Interface Boundary

新增一个面向 control-plane 语义的接口，例如：

- `CoordStore`
  - `init_store()`
  - `list_records(milestone, kind=None, status=None)`
  - `get_record(milestone, kind, **matches)`
  - `create_record(...) -> CoordRecord`
  - `update_record(...) -> CoordRecord`

这里不要求名字必须完全如此，但要求语义不再以 “issue” 为中心，也不能只是把 CRUD 换个名字。

`Stage A` 的最小要求是：

- store 查询必须至少支持 `milestone` 下推
- store 查询必须至少支持 `kind` 过滤
- service 不再依赖 “先全量 load，再在 Python 里到处过滤” 这一模式作为唯一契约
- `create/update` 最好直接返回完整 record，减少 service 内部手工构造 record 的需要

约束：

- `CoordService` 只依赖 `CoordStore`
- service 不再 import `IssueStore`
- service 不再以 `IssueRecord` 作为公开的内部中心名词
- `Stage A` 不追求为每一种 query 建完整 repository API，但至少要为 `Stage B` 留出 SQL 可下推的接口形状

### 2. Adapter Mapping

迁移期保留两个 store adapter：

- `BeadsCoordStore`
  - 封装现有 `bd ... --json` 读写
  - 继续作为默认真实后端
- `MemoryCoordStore`
  - 保留测试快速性
  - 作为 `tests/test_devcoord.py` 的主要 double

关键约束：

- `BeadsCoordStore` 只负责把 `beads` 记录翻译成 `CoordStore` 语义
- 不在 `Stage A` 中为 `beads` 增加任何新 control-plane 功能
- 不引入历史数据迁移逻辑
- `BeadsCoordStore` 是明确的过渡 adapter：
  - `Stage A` 引入
  - `Stage B/C` 继续作为兼容后端存在
  - 目标在 `Stage D` cutover 后移除

### 3. Record Model

`Stage A` 不必立刻完成 SQLite 时代的最终 record model，但必须明确当前选择。

本计划明确选择：

- `Stage A` 保留单一 `CoordRecord`
- 不在 `Stage A` 引入 `MilestoneRecord / GateRecord / EventRecord` 等 typed records
- typed records 是否引入，推迟到 `Stage B` 或之后再决策

- service 层不再依赖 `IssueRecord` 这个名字
- adapter 层可各自决定内部记录如何映射
- future `SQLiteCoordStore` 不需要为了复用 `IssueRecord` 而扭曲 schema

这里的约束是：

- 保留当前记录字段形状
- 但将其提升为更中性的 `CoordRecord` / `CoordEntity` 概念
- service 内部不应再大面积手工拼装旧 `IssueRecord`
- 当 store 完成 `create/update` 后，应优先返回完整 `CoordRecord`，减少 service 自行回填字段的模式

也就是说，`Stage A` 解决的是：

- 去掉 `Issue*` 语言
- 给未来 typed store 留接口空间

而不是：

- 一次性完成最终 domain model

### 4. Concurrency Boundary

`Stage A` 不修改并发控制机制。

明确保持不变：

- [`CoordService._locked()`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/service.py) 的文件锁语义
- [`CoordPaths.lock_file`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/scripts/devcoord/model.py#L29) 的使用方式

原因：

- 当前任务只切 store 抽象，不切锁模型
- store 边界和锁边界同时改，会让回归面过大

### 5. CLI Composition

`coord.py` 在 `Stage A` 的唯一改动应是：

- 默认构造 `BeadsCoordStore`
- 对测试注入保持兼容

明确不做：

- grouped subcommands
- alias 收敛
- 参数退役

### 6. Test Strategy

`Stage A` 的测试重点不是协议新增，而是确认抽象替换不改行为。

必须覆盖：

- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)
  - 作为主要回归面
- 新增或调整的 store-focused tests
  - 验证 `BeadsCoordStore`
  - 验证 `MemoryCoordStore`

建议保持：

- 绝大多数测试仍跑内存 store
- 不在 `Stage A` 引入 SQLite 集成测试

## Complexity Assessment

`Stage A` 复杂度评估为**中等**。

它已经明显比总方案窄，理论上适合作为一次实现任务；但为了降低回归风险，仍建议内部拆成 3 个切片：

1. `A1` Types and store contract
2. `A2` Service refactor
3. `A3` CLI wiring and tests

这 3 个切片可以在同一 PR 内完成，但实现顺序不应打乱。

## Implementation Shape

### Slice A1: Types and Store Contract

目标：

- 新增 `CoordStore`
- 明确 `BeadsCoordStore` / `MemoryCoordStore` 的接口实现
- 收敛 `IssueStore` 为遗留兼容层或直接替换

建议文件：

- `scripts/devcoord/store.py`（新）
- `scripts/devcoord/model.py`

产出：

- `CoordStore`
- 中性 record 概念
- `BeadsCoordStore`
- `MemoryCoordStore`
- 至少支持 `milestone` / `kind` 过滤的查询接口
- `create/update` 返回完整 record

验收：

- service 还未改动前，store 层可单独导入和实例化
- 无新增行为变化

### Slice A2: Service Refactor

目标：

- `CoordService` 从 `IssueStore` / `IssueRecord` 迁移到 `CoordStore` / 中性 record 概念

建议文件：

- `scripts/devcoord/service.py`

产出：

- service 不再 import `IssueStore`
- service 不再把 `IssueRecord` 作为中心语义
- service 不再默认依赖 “load all then filter everything”
- 所有 helper 保持现有业务行为

验收：

- 全部公开方法行为不变：
  - `init_control_plane`
  - `open_gate`
  - `ack`
  - `heartbeat`
  - `phase_complete`
  - `recovery_check`
  - `state_sync_ok`
  - `stale_detected`
  - `ping`
  - `unconfirmed_instruction`
  - `log_pending`
  - `gate_review`
  - `gate_close`
  - `render`
  - `audit`
  - `close_milestone`

### Slice A3: CLI Wiring and Tests

目标：

- `coord.py` 改为默认构造 `BeadsCoordStore`
- 测试迁移到 `MemoryCoordStore`

建议文件：

- `scripts/devcoord/coord.py`
- [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py)

产出：

- CLI 仍保持现有命令面
- 测试命名和注入更新
- 回归范围明确

验收：

- `tests/test_devcoord.py` 全绿
- `coord.py` 现有 flat commands 行为不变
- 对外 CLI 参数不变化

## Risks

| # | 风险 | 影响 | 概率 | 缓解 |
| --- | --- | --- | --- | --- |
| R1 | 抽象重命名过度，实际行为被连带改坏 | `render/audit` 或 gate 行为回归 | 中 | 明确 `Stage A` 只换依赖边界，不换协议语义 |
| R2 | service 仍残留 issue-oriented 细节或全量加载模式 | `Stage B` 继续需要穿透 service | 中 | 以 import/type usage 检查 `IssueStore` / `IssueRecord` 残留，并检查 `milestone/kind` 查询是否已下推到 store 契约 |
| R3 | 测试 double 改动过大 | 测试成本上升，回归难定位 | 中 | 保持 `MemoryCoordStore` 极薄，不提前模拟 SQLite |
| R4 | 顺手改 CLI | 后续审阅面扩大 | 低 | 明确 grouped CLI 和参数退役全部 out of scope |

## Acceptance Criteria

- [ ] `CoordService` 正式依赖 `CoordStore`，不再依赖 `IssueStore`
- [ ] `BeadsCoordStore` 成为默认真实后端
- [ ] `MemoryCoordStore` 成为测试 double
- [ ] service 层不再以 `IssueRecord` 作为中心命名
- [ ] [`tests/test_devcoord.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tests/test_devcoord.py) 通过
- [ ] `coord.py` 对外命令面无行为变化
- [ ] `render/audit` 输出不回归
- [ ] 不引入 `.devcoord/control.db`
- [ ] 不引入历史数据迁移逻辑

## Resolved Positions

- `Stage A` 不追求“最终抽象最优”，只追求给 `Stage B` 留出稳定插槽。
- `Stage A` 的成功标准是“行为不变 + 依赖边界改变”，不是“新功能出现”。
- `Stage A` 明确保留单一 `CoordRecord`；typed records 推迟到 `Stage B` 或以后再决定。
- `Stage A` 完成后，下一步才进入 `SQLiteCoordStore` 的实现，而不是继续在 `beads` 语义上叠补丁。
