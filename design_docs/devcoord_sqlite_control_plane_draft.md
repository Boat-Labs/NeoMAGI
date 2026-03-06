# Devcoord SQLite 控制面草案

> 状态：draft
> 日期：2026-03-07
> 适用范围：仅用于 NeoMAGI 开发协作，不用于产品运行时
> 相关决议：[`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)

## 1. 目的

本文定义 `devcoord` 从 `beads` 解耦后的 SQLite 控制面技术形态。

目标是把以下三层边界明确下来：

- `beads / bd` = backlog / issue graph / work tracking
- `scripts/devcoord` = coordination protocol runtime
- `dev_docs/logs/*` + `project_progress.md` = projection / human-readable evidence

本文聚焦两件事：

- 最小化的 `.devcoord/control.db` 数据模型
- 精简后的 `coord.py` 命令面，在不削弱协议语义的前提下减少 CLI 噪声

## 2. 非目标

- 不改变产品运行时 PostgreSQL 基线。
- 不把产品数据或 memory 数据迁入 SQLite。
- 不削弱 `AGENTTEAMS.md` 的协议要求。
- 不重新引入手写 `dev_docs` 控制状态的做法。
- 不把 `devcoord` 再降级回 prompt-only 流程。
- 本阶段不为多机分布式协作做设计。

## 3. 设计原则

- 单机共享控制面。
- 标准库优先：使用 Python `sqlite3`，不用 ORM，不引入新服务。
- 所有确定性写入都通过 `scripts/devcoord`，不允许 ad-hoc shell / database access。
- append-only 审计事件继续是一等对象。
- 聚合状态通过事务更新，不通过文档正文反推。
- projection 继续是可重建、可丢弃的。
- 人类可见 CLI 应尽量小；机器入口可以继续保持结构化和显式。

## 4. 存储布局

### 4.1 目录

在仓库根目录使用专用控制面目录：

```text
.devcoord/
  control.db
  schema_version.json (optional)
```

规则：

- 所有 worktree 都指向同一个 repo-root `.devcoord/control.db`。
- `.devcoord/` 是内部协作状态，不是产品运行时状态。
- `dev_docs/logs/<phase>/...` 和 `dev_docs/progress/project_progress.md` 继续作为 `render` 生成的 projection。

### 4.2 为什么使用单个 SQLite 文件

- 不引入新的 server dependency。
- 适合单机、多 worktree 共享控制面。
- 备份、检查和重置都更简单。
- 让 `devcoord` 同时独立于 `beads` 和产品 PostgreSQL。

## 5. 高层架构

```text
LLM / skill
  -> scripts/devcoord/coord.py
    -> SQLite store (.devcoord/control.db)
    -> dev_docs/logs/<phase>/*
    -> dev_docs/progress/project_progress.md

beads / bd
  -> backlog / issues / epics / review follow-up only
```

关键规则：

- `scripts/devcoord` 仍然是唯一的协议运行时。
- SQLite 只是协调状态的持久化层。
- `beads` 不再被查询或写入任何 control-plane 状态。

## 6. 最小数据模型

schema 应刻意保持克制。这个 store 只需要 6 类逻辑对象：

1. `milestones`
2. `phases`
3. `gates`
4. `roles`
5. `messages`
6. `events`

### 6.1 `milestones`

目的：

- 顶层协作单元
- 一条记录对应一个 milestone，例如 `p2-m1a`

建议字段：

```sql
milestone_id TEXT PRIMARY KEY
run_date TEXT NOT NULL
status TEXT NOT NULL
schema_version INTEGER NOT NULL
created_at TEXT NOT NULL
closed_at TEXT
```

说明：

- `status` 只保留粗粒度状态：`active` 或 `closed`
- 这一层替代原来 milestone bead 根对象

### 6.2 `phases`

目的：

- 保存每个 milestone phase 的聚合状态

建议字段：

```sql
milestone_id TEXT NOT NULL
phase_id TEXT NOT NULL
phase_state TEXT NOT NULL
last_commit TEXT
opened_at TEXT
closed_at TEXT
PRIMARY KEY (milestone_id, phase_id)
```

说明：

- `phase_state` 表示 `pending`、`active`、`closed` 之类的聚合状态
- `last_commit` 用于 projection 和 resume 判断

### 6.3 `gates`

目的：

- 每个 gate 一条记录，表达聚合后的授权窗口

建议字段：

```sql
milestone_id TEXT NOT NULL
gate_id TEXT NOT NULL
phase_id TEXT NOT NULL
allowed_role TEXT NOT NULL
target_commit TEXT NOT NULL
gate_state TEXT NOT NULL
result TEXT
report_path TEXT
report_commit TEXT
opened_at TEXT
closed_at TEXT
PRIMARY KEY (milestone_id, gate_id)
```

说明：

- `gate_state` 是权威聚合状态：`pending`、`effective`、`closed`
- `report_path` 和 `report_commit` 继续保留为关 gate 时的证据字段

### 6.4 `roles`

目的：

- 保存每个 role 的聚合活性和当前位置

建议字段：

```sql
milestone_id TEXT NOT NULL
role TEXT NOT NULL
agent_state TEXT NOT NULL
action TEXT
current_task TEXT
current_phase TEXT
current_gate TEXT
last_activity TEXT
stale_risk TEXT
PRIMARY KEY (milestone_id, role)
```

说明：

- 这一层替代原来的 agent bead 状态
- 主要服务 watchdog 和 projection 输出

### 6.5 `messages`

目的：

- 保存所有需要 ACK 语义的 command

建议字段：

```sql
message_id INTEGER PRIMARY KEY AUTOINCREMENT
message_key TEXT NOT NULL UNIQUE
milestone_id TEXT NOT NULL
gate_id TEXT
phase_id TEXT
command_name TEXT NOT NULL
target_role TEXT NOT NULL
target_commit TEXT
requires_ack INTEGER NOT NULL
effective INTEGER NOT NULL
sent_at TEXT NOT NULL
acked_at TEXT
ack_role TEXT
ack_commit TEXT
source_message_key TEXT
payload_json TEXT NOT NULL
```

说明：

- 该表承载 `GATE_OPEN`、`STOP`、`WAIT`、`RESUME`、`PING`
- `effective=0` 表示尚未 ACK
- `payload_json` 用于窄扩展，避免过快膨胀顶层 schema

### 6.6 `events`

目的：

- append-only 审计事件流

建议字段：

```sql
event_id INTEGER PRIMARY KEY AUTOINCREMENT
milestone_id TEXT NOT NULL
event_seq INTEGER NOT NULL
phase_id TEXT
gate_id TEXT
role TEXT
event_type TEXT NOT NULL
status TEXT
task TEXT NOT NULL
target_commit TEXT
result TEXT
report_path TEXT
report_commit TEXT
branch TEXT
eta_min INTEGER
source_message_key TEXT
payload_json TEXT NOT NULL
created_at TEXT NOT NULL
UNIQUE (milestone_id, event_seq)
```

说明：

- `event_seq` 继续保持 milestone 级单调递增，用于 audit / render 排序
- 所有协议证据都落在这里
- 这张表替代的是 append-only event beads，不是 projection JSONL

## 7. 事务规则

SQLite 只有在写入规则足够明确时才成立：

- 开启 WAL mode。
- 设置 busy timeout。
- 每条命令都在单个事务里完成。
- `event_seq` 分配和聚合状态更新必须在同一个事务里完成。
- `ACK` 必须原子地完成：
  - 校验 pending message
  - 将其标记为 effective
  - 追加 `ACK` 事件
  - 如有需要，追加对应的 effective 事件
- `gate-close` 必须原子地完成：
  - 校验 review evidence
  - 校验 `audit.reconciled=true`
  - 更新 gate 行
  - 追加 close 事件

这样才能保留当前的 `append-first` 和 `fail-closed` 语义。

## 8. Projection 模型

保留现有生成文件不变：

- `dev_docs/logs/<phase>/{milestone}_{date}/heartbeat_events.jsonl`
- `dev_docs/logs/<phase>/{milestone}_{date}/gate_state.md`
- `dev_docs/logs/<phase>/{milestone}_{date}/watchdog_status.md`
- `dev_docs/progress/project_progress.md`

规则：

- Projection 从 SQLite 全量重建，不做手工增量维护。
- `audit` 比较 SQLite 状态与生成后的 projection。
- `render -> audit -> read projection` 继续作为 gate close 前的固定顺序。

## 9. 精简后的 `coord.py` 命令面

## 9.1 当前问题

当前 `coord.py` 暴露了太多扁平的顶层命令。问题不在于协议事件本身没必要，而在于每个协议事件都变成了一个平级 CLI 概念。

这会制造两类噪声：

- 人要记忆的命令词太多
- CLI 形状无法反映协议的语义分组

## 9.2 目标形态

保留协议语义，但把顶层命令面收敛成分组命令。

目标：

- 当前扁平顶层命令面：16 个命令
- 目标顶层命令面：7 个命令

### Machine-first 入口

保留一个结构化的机器入口：

```text
uv run python scripts/devcoord/coord.py apply ...
```

`apply` 应成为默认的 agent-facing 入口。

### Human/debug 入口

对人暴露更小的分组 CLI：

```text
coord.py init
coord.py gate ...
coord.py command ...
coord.py event ...
coord.py projection ...
coord.py milestone ...
coord.py apply ...
```

这样可以在不损失协议表达力的前提下，显著减少顶层命令数。

## 9.3 建议的分组命令

### `init`

职责：

- 初始化 milestone 状态
- 初始化 roles
- 初始化 schema metadata

### `gate`

子命令：

- `gate open`
- `gate review`
- `gate close`

语义：

- `gate open` 是发出 `GATE_OPEN` 的人类友好包装
- `gate review` 负责记录 review evidence
- `gate close` 负责执行带 guard 的关 gate

### `command`

子命令：

- `command ack`
- `command send`

`command send` 覆盖：

- `STOP`
- `WAIT`
- `RESUME`
- `PING`

说明：

- `GATE_OPEN` 刻意放在 `gate open` 下，而不是 generic `command send`，因为它是最常见、也最关键的路径

### `event`

子命令：

- `event heartbeat`
- `event phase-complete`
- `event recovery-check`
- `event state-sync-ok`
- `event stale-detected`
- `event log-pending`
- `event unconfirmed-instruction`

这些仍然是独立协议事件，但不再需要作为扁平顶层命令暴露。

### `projection`

子命令：

- `projection render`
- `projection audit`

### `milestone`

子命令：

- `milestone close`

## 9.4 旧命令映射

| 当前扁平命令 | 建议分组命令 |
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

## 9.5 兼容策略

不要一次性打断现有 prompt。

迁移顺序：

1. 先保留现有扁平命令作为 compatibility aliases
2. 再把分组命令实现成 canonical form
3. 然后把 skills、runbooks、PM action plans 切到分组形式
4. 最后再逐步废弃旧的 flat aliases

这样可以在缩小公开命令面的同时，保持现有 `devcoord` 使用路径稳定。

## 10. Service 层形态

当前 `CoordService` 的语义方法大体可以保留。

主要重构点应该是：

- 引入 `CoordStore` 抽象
- 把 `beads` 专属的持久化逻辑移出 service
- 新增 `SQLiteCoordStore`

建议分层：

- `CoordService`
  - protocol rules
  - validation
  - ordering
  - projection generation
- `CoordStore`
  - milestone / phase / gate / role / message / event persistence
  - transactional writes
  - audit / render query helpers
- `SQLiteCoordStore`
  - 基于 `sqlite3` 的 `.devcoord/control.db` 具体实现

这样可以在只替换 store adapter 的前提下，保持协议语义稳定。

## 11. 迁移策略

### Stage 0: ADR accepted

- 接受 ADR 0050
- 冻结 `devcoord` 对 `beads` 的新增专属扩张

### Stage 1: Store abstraction

- 引入 `CoordStore` 接口
- 保持当前行为不变

### Stage 2: SQLite implementation

- 新增 `.devcoord/control.db`
- 实现 `SQLiteCoordStore`
- 让 `render/audit` 改为从 SQLite 读取

### Stage 3: Command regrouping

- 新增 grouped CLI commands
- 保留旧 flat aliases
- 将 PM/backend/tester skills 切到 grouped 或 `apply` 形式

### Stage 4: Cutover

- 停止把 control-plane 对象写入 `beads`
- `beads` 只保留 backlog tasks
- 归档或 supersede `beads_control_plane.md`

## 12. 验收

- `bd list --status open` 不再被 `coord` control-plane 对象污染。
- `AGENTTEAMS.md` 的协议语义继续可执行。
- `render` 和 `audit` 仍能产出和现在同类的证据。
- `gate open -> ack -> review -> close` 不依赖 `beads` 也能成立。
- restart / resume handshake 继续成立。
- `milestone close` 可在不触碰 backlog issue 的前提下完成 closeout。
- `coord.py` 顶层命令分组比当前扁平命令面更少、更容易记忆。

## 13. 开放问题

- `.devcoord/control.db` 应该被 git ignore，还是依赖现有 ignore 规则和 projection 证据让它保持本地态？
- `schema_version` 应只存在 SQLite metadata 中，还是额外保留 sidecar `schema_version.json` 便于人工检查？
- `gate open` 应先作为语义 alias 存在，还是立即成为替代 `open-gate` 的 canonical path？
