# Devcoord SQLite Control Plane：产品口径说明

> 状态：approved
> 日期：2026-03-08
> 适用范围：NeoMAGI 的开发协作能力说明；不是产品运行时数据面
> 相关文档：
> - [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md)
> - [`dev_docs/devcoord/sqlite_control_plane_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/sqlite_control_plane_runtime.md)
> - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)

## 1. 一句话定义

SQLite control plane 是 NeoMAGI 的“协作控制塔台”。

它不管理产品数据，不管理长期 memory，也不替代 issue tracker；它只做一件事：把多代理开发协作中的**授权、生效、进度、恢复、审计、关单**状态，稳定地放进一个共享真源里。

这个真源就是 repo-root 下的：

```text
.devcoord/control.db
```

## 2. 为什么需要它

NeoMAGI 在 builder / coding 场景里，不只是一个“会回答问题的 chat agent”，而是一个要和人类、多个 worktree、多个角色一起完成真实工程任务的系统。

这类场景天然会遇到 4 个问题：

1. 谁现在被允许推进下一步
2. 某条指令到底有没有真正生效
3. 会话重启以后怎么从正确位置恢复
4. 事后怎么证明这个 Gate 是按规则关掉的

如果这些状态只存在于聊天上下文、临时记忆或手写日志里，系统会很快退化成：

- 指令有没有 ACK 不清楚
- worktree 之间状态不一致
- 一次重启之后没人知道当前 phase 到哪了
- 关 Gate 时只能“感觉应该差不多了”

SQLite control plane 的产品价值，就是把这些本来容易丢失、容易漂移的协作状态，变成**确定性的、可恢复的、可审计的**能力。

## 3. 它解决的不是“数据库问题”，而是“协作确定性问题”

从产品口径看，SQLite 只是实现手段，不是目标。

真正要解决的是：

- NeoMAGI 如何在多角色协作时保持统一状态
- NeoMAGI 如何在中断后恢复，而不是靠 PM 重新读整段历史猜状态
- NeoMAGI 如何把“我说过了”和“系统真生效了”区分开
- NeoMAGI 如何在收尾时给出一份可以核对的证据链

所以这个系统的核心收益不是“用了 SQLite”，而是：

- `GATE_OPEN` 有 pending 和 effective 的区别
- `ACK` 有明确生效语义
- `RECOVERY_CHECK -> STATE_SYNC_OK` 有固定握手
- `render -> audit -> close` 有明确守卫
- `bd` 和 devcoord 不再语义混杂

## 4. 它不是什么

为了避免误用，这个口径要非常清楚：

- 它不是产品运行时数据库
  - 产品运行时仍然以 PostgreSQL 为准。
- 它不是 NeoMAGI 的长期 memory store
  - memory 的真源、召回、写入策略在 memory 架构文档中单独定义。
- 它不是 backlog / issue 系统
  - backlog 仍由 `bd` 管理。
- 它不是人工日志文件系统
  - `dev_docs/logs/*` 是 projection，不是真源。
- 它不是分布式调度器
  - 当前设计目标是单机、多 worktree 共享控制面。

## 5. 它和 `bd`、`coord.py`、`dev_docs` 的关系

把它理解成三层最清楚：

| 层 | 角色 | 解决什么问题 |
| --- | --- | --- |
| `bd / beads` | backlog / issue graph | 记录“还有哪些工作项” |
| `scripts/devcoord/coord.py` + `.devcoord/control.db` | coordination runtime + SSOT | 记录“协作当前发生到哪一步” |
| `dev_docs/logs/*` + `project_progress.md` | human-readable projection | 记录“人类现在如何看懂当前状态” |

一句话区分：

- `bd` 管任务
- SQLite control plane 管协作状态机
- `dev_docs` 管可读证据

## 6. 产品级心智模型

从 PM / teammate 的视角，不需要先理解表结构，先记住下面 6 个对象就够了：

| 对象 | 产品含义 |
| --- | --- |
| `milestone` | 一次完整的协作目标，例如某个阶段性工程闭环 |
| `phase` | milestone 内的推进阶段 |
| `gate` | 一段被明确授权的执行窗口 |
| `role` | 当前参与协作的角色状态，如 `pm`、`backend`、`tester` |
| `message` | 一条要求 ACK 的正式指令 |
| `event` | append-only 的审计事件流 |

如果把整个系统类比成机场塔台：

- `milestone` 是当前航班任务
- `phase` 是飞行阶段
- `gate` 是放行窗口
- `message` 是塔台下发的正式指令
- `ACK` 是机组确认收到并执行
- `event` 是黑匣子式记录

这也是为什么它更像“control plane”，而不是“task tracker”。

## 7. 它是怎么运作的

### 7.1 初始化

PM 先初始化一个 milestone。

系统会建立：

- milestone 的顶层记录
- 默认角色记录
- 当前 run-date 和 schema metadata

这一步的产品意义是：协作正式开始，控制面进入“可记录、可恢复”的状态。

### 7.2 Gate 放行

当 PM 决定允许某个角色进入下一阶段时，会发出 `gate open`。

这一步不会直接把 Gate 视为“已生效”，而是会先生成：

- 一个 `gate`
- 一条 pending `GATE_OPEN` message
- 一条 `GATE_OPEN_SENT` event

产品意义：

- “我发了指令”和“对方真的开始执行”不是一回事
- 系统强制把这两步分开

### 7.3 ACK 生效

被授权角色收到后，需要 `command ack`。

只有 ACK 成功后，系统才把这条指令从 pending 变成 effective，并把 Gate 状态推进到 `open`。

产品意义：

- 解决“指令已经说了但没人确认”的灰色地带
- 明确当前到底是谁被允许继续写代码或开始验收

### 7.4 执行中状态同步

角色工作时会不断写入：

- `event heartbeat`
- `event phase-complete`
- `command send --name PING`
- `event stale-detected`
- `event log-pending`
- `event unconfirmed-instruction`

这些不是“聊天记录”，而是控制面的活性信号。

产品意义：

- PM 能知道当前是 working、blocked、stuck 还是 done
- watchdog 有东西可看
- 长任务和中断不会变成黑盒

### 7.5 中断与恢复

当 teammate context 压缩、会话重启或长时间中断后，不允许直接猜状态继续干活，而要走：

```text
RECOVERY_CHECK -> STATE_SYNC_OK
```

产品意义：

- 恢复过程不靠回忆
- 恢复后的继续执行有正式握手
- “上次看到哪”与“当前真源状态”有对齐动作

### 7.6 投影与对账

控制面真源在 SQLite，但人类不会直接读数据库。

所以系统提供两步：

1. `projection render`
   - 从 SQLite 重建：
     - `heartbeat_events.jsonl`
     - `gate_state.md`
     - `watchdog_status.md`
     - `project_progress.md`
2. `projection audit`
   - 检查 projection 是否和 SQLite 当前状态一致

产品意义：

- 真源保持结构化
- 人类看到的是可读文件
- 文件即使删了、乱了，也能从真源重建

### 7.7 审阅与关 Gate

Gate 不能“口头关闭”。

标准路径是：

```text
gate review
-> projection render
-> projection audit
-> gate close
-> projection render
-> projection audit
-> milestone close
```

这里的关键不是命令顺序本身，而是产品约束：

- 没有 review evidence，不能关 Gate
- 没有 `audit.reconciled=true`，不能关 Gate
- 关 Gate 后要再 render / audit 一轮，确认 projection 反映的是关后状态
- 只有所有 Gate 都完成 closeout，milestone 才能真正关闭

这让 NeoMAGI 的协作收尾不再是“感觉差不多”，而是有证据闭环。

## 8. 为什么是 SQLite，而不是 PostgreSQL 或 `bd`

### 8.1 为什么不是 PostgreSQL

因为这不是产品数据面。

如果把 devcoord 直接耦合进产品 PostgreSQL，会产生几个坏结果：

- 内部协作控制要依赖产品运行时环境
- `.env`、连接配置、迁移链路会和产品运行时绑死
- “产品真源”和“开发协作真源”边界会再次混淆

产品口径上，这不划算。

### 8.2 为什么不是 `bd`

因为 `bd` 解决的是“工作项图谱”，不是“协作状态机”。

如果继续把 Gate / ACK / event 也塞进 issue 系统，会把两类问题混在一起：

- 还有哪些事要做
- 当前协作协议发生到了哪一步

SQLite control plane 的价值，就是把这两个问题拆开。

## 9. 为什么它对产品能力重要

虽然这个系统不直接暴露给终端用户，但它决定了 NeoMAGI 作为 personal agent / builder agent 的一项底层能力：

**在真实工程协作里保持连续性。**

如果没有这个层，NeoMAGI 更像一个强一点的聊天助手。

有了这个层，NeoMAGI 才更接近一个真正能参与交付的 agent：

- 有明确授权边界
- 有恢复机制
- 有可核对证据
- 有收尾闭环
- 不会因为一次会话丢失就整段状态蒸发

这就是它的产品意义。

## 10. 对操作者的稳定口径

如果后续只保留一组最稳定的操作心智，应当是下面 5 条：

1. `.devcoord/control.db` 是真源
2. 所有控制面写入只能通过 `scripts/devcoord/coord.py`
3. `dev_docs/logs/*` 和 `project_progress.md` 是 projection，不是真源
4. `bd` 负责 backlog，不负责当前协作状态机
5. `render -> audit -> close` 是关 Gate / milestone 的固定闭环

## 11. 什么叫“它工作正常”

从产品口径看，SQLite control plane 工作正常，不是指“数据库文件存在”，而是指下面这些现象同时成立：

- `gate open` 和 `command ack` 能把授权窗口清晰分成 pending / effective
- 重启后能通过 recovery handshake 恢复
- `projection audit` 能稳定回到 `reconciled=true`
- `bd list --status open` 不再混入 live control-plane 对象
- `milestone close` 可以在不依赖 beads backend 的情况下完成
- 多 worktree 读到的是同一个共享控制面状态

## 12. 常见误解

### 误解 1：日志文件才是控制面的真源

不是。日志文件只是 projection，SQLite 才是 SSOT。

### 误解 2：这是产品 runtime 的一部分

不是。它服务的是开发协作，不是最终用户数据面。

### 误解 3：只要有 issue tracker，就不需要 control plane

不对。issue tracker 管“做什么”，control plane 管“当前协作状态机到哪了”。

### 误解 4：SQLite 只是临时过渡方案

按当前口径不是。它是为了单机、多 worktree、低依赖协作场景刻意选择的长期边界。

## 13. 与技术设计文档的分工

阅读建议：

- 如果你想理解“它为什么存在、产品上有什么价值、应该怎么理解它”，先读本文。
- 如果你想理解“表结构、事务规则、命令面、迁移策略、验收标准”，读 [`design_docs/devcoord_sqlite_control_plane.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/devcoord_sqlite_control_plane.md)。
- 如果你想知道“日常怎么用命令操作它”，读 [`dev_docs/devcoord/sqlite_control_plane_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/devcoord/sqlite_control_plane_runtime.md)。
