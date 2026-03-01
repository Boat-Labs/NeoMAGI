# M7 DevCoord 重构计划

> 状态：superseded by `dev_docs/plans/m7_devcoord-refactor_2026-02-28_v2.md`
> 说明：本版保留为历史记录；devcoord 运行时入口口径已由 ADR 0043 与 v2 计划修订为 `scripts/devcoord` 直接入口。
> 原始状态：approved
> 原始日期：2026-02-28
> 性质：内部治理 / 执行层重构，不属于产品 roadmap 新功能里程碑
> 依据：`AGENTTEAMS.md`、`AGENTS.md`、ADR 0005/0006/0023/0036/0042、`dev_docs/devcoord/beads_control_plane.md`

## 1. 目标

将当前依赖 prompt + 手工文档维护的多 agent 协作控制流程，重构为：

- `beads + Dolt` 作为开发协作控制面 SSOT；
- `just -> scripts/devcoord` 作为唯一运行时写入口；
- `dev_docs/logs/` 与 `dev_docs/progress/` 降级为兼容投影层；
- M7 只实现“最小协调内核”，不试图在本阶段做成覆盖全部执行路径的 workflow engine；
- 在不改变战略层文档与产品运行时数据库路线的前提下，提升协作状态机的可追溯性、可验证性与 deterministic 程度。

## 2. 范围

### 2.1 In Scope
- PM / backend / tester 的协作控制状态：
  - Gate open / close
  - ACK 生效
  - heartbeat / watchdog
  - recovery / state sync
  - phase complete / gate review / stale detection
- 统一命令面：
  - `just coord-*`
  - `scripts/devcoord/*`
  - 对应 skill
- 兼容投影：
  - `dev_docs/logs/{milestone}_{date}/heartbeat_events.jsonl`
  - `dev_docs/logs/{milestone}_{date}/gate_state.md`
  - `dev_docs/logs/{milestone}_{date}/watchdog_status.md`
  - `dev_docs/progress/project_progress.md`

### 2.2 Out of Scope
- 产品运行时 PostgreSQL 数据面
- `decisions/`、`design_docs/`、`dev_docs/reviews/`、`dev_docs/reports/` 的主存储语义
- beads 远程 sync / federation / `beads-sync` 分支工作流
- 自定义 beads schema 或 fork beads
- 将 beads 原生业务语义（如原生 gate / wisp 生命周期）直接等同于 NeoMAGI 协作协议语义

## 3. 当前问题

- 协作状态机真实执行依赖 PM 手工维护文档，文件是控制面而非投影。
- append-first、ACK 生效、恢复握手、日志对账无法通过程序强制。
- 同一规则同时分散在 `AGENTTEAMS.md`、`AGENTS.md`、PM action plan、phase prompt 与日志文件里，维护成本高。
- agent 当前缺少稳定的“控制面 API”，导致协议细节过度暴露给模型上下文。
- 当前项目规模较小，短期内会低利用 `beads / Dolt` 的部分高级特性；M7 选择它是对未来协作能力的前置投资，而不是假设当前阶段已经需要吃满其规模优势。

## 4. 目标架构

### 4.1 分层
- LLM：判断动作、补全参数、产出分析与结论。
- Just：对 agent 暴露稳定命令面。
- `scripts/devcoord`：参数校验、状态机约束、beads 适配、投影生成、对账与幂等；是协议语义唯一实现层。
- Beads/Dolt：执行层 persistence / query / history SSOT，不承担状态机语义。
- `dev_docs`：兼容投影层。

补充说明：
- skill 仍然存在，但只作为行为约束与调用规范，不作为运行时系统分层。
- projector 不单列为系统层，作为 `scripts/devcoord` 的内部职责存在。

### 4.2 控制面对象
- milestone bead
- phase bead
- gate bead
- agent bead
- message bead（承载 `GATE_OPEN` / `STOP` / `WAIT` / `RESUME` / `PING`）
- append-only event bead（承载 heartbeat / ACK / recovery / phase_complete / stale / respawn）

### 4.3 协作原则
- 强治理，弱流程：状态机只约束权限、审计、恢复、对账，不锁死任务的唯一执行路径。
- `phase` / `gate` 是授权窗口，不是强制所有任务按单一路径流转的 workflow state。
- 执行所有权采用 lease 思路而非永久锁定；handoff / rescue 是正常协作路径，不视为异常补丁。
- 仅在适合的任务上引入 `open_competition`；默认仍以可追溯的单持有执行为主，避免无约束并发。

## 5. 执行阶段

### Phase 0：文档与模型冻结
- 产物：
  - ADR 0042
  - `dev_docs/devcoord/beads_control_plane.md`
  - M7 草稿计划
- Gate：
  - 明确边界：战略层不动，执行层改造
  - 明确共享 `BEADS_DIR` 与本机单实例 Dolt server 方案

### Phase 1：控制面骨架
- 产物：
  - `scripts/devcoord/coord.py`
  - `just coord-*` 基础命令
- 要求：
  - 保持“state machine for governance, not for single-path execution”的设计边界
  - 固定与 beads 的交互方式为 `bd ... --json` CLI shell-out
  - 明确不直接写 Dolt SQL，不经由 MCP server
  - 先覆盖最小事件集：`coord-init`、`coord-open-gate`、`coord-ack`、`coord-heartbeat`、`coord-phase-complete`、`coord-render`
  - 所有写操作统一从 wrapper 进入

### Phase 2：Shadow Mode
- 产物：
  - M6 真实日志回放脚本
  - beads -> `dev_docs` 投影输出
- 要求：
  - 基于既有 M6 日志回放生成控制面状态
  - 核对投影结果与现有日志文件口径是否一致
  - 验证幂等：重复回放不产生错误漂移

### Phase 3：PM First
- 产物：
  - PM 专用 skill
  - 更新后的 PM tactical prompt
- 要求：
  - PM 停止手写控制日志
  - PM 全部改为调用 `just coord-*`
  - teammate 暂时维持旧消息格式

### Phase 4：Teammate Cutover
- 产物：
  - teammate skill
  - backend/tester tactical prompt 更新
- 要求：
  - `ACK`、`HEARTBEAT`、`PHASE_COMPLETE`、`RECOVERY_CHECK` 统一走 wrapper
  - 禁止直接编辑 `dev_docs/logs/*`

### Phase 5：Projection-Only 收口
- 产物：
  - 更新后的 `dev_docs/logs/README.md`
  - 更新后的协作文档说明
- 要求：
  - `dev_docs/logs/*` 明确为 projection
  - 人工维护入口彻底收敛到 control plane wrapper

## 6. 验收标准

- 任一协作状态变更都能在 beads 中查询到结构化记录。
- `dev_docs/logs/*` 可由 `scripts/devcoord` 完整重建，不依赖人工补写。
- skill 不直接修改日志文件，不直接自由拼装 `bd` 命令。
- 共享 `BEADS_DIR` 下，多 worktree 可以基于同一控制面协作，不出现各自独立状态。
- 使用 M6 回放时，能生成与既有 `heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md` 语义一致的投影。
- M7 实施后，控制面仍允许 handoff / rescue 等开放协作路径，不因单一 agent 卡住而把任务锁死在唯一路径中。

## 7. 测试策略

### 7.1 控制面单元测试
- 状态对象与命令参数校验测试
- 协议不变量测试：未 ACK 不生效、append-first、缺事件禁止 gate close
- `event_seq` 分配测试：共享控制面下全局单调递增

### 7.2 适配层测试
- `bd ... --json` CLI 成功路径
- CLI 失败、超时、非预期 JSON 的错误路径
- beads 不可达时 fail-closed 行为

### 7.3 Projector 测试
- golden tests：固定输入事件流，验证 `heartbeat_events.jsonl` / `gate_state.md` / `watchdog_status.md` 输出
- schema 转换测试：确保 projection 字段与当前日志口径一致

### 7.4 Shadow Replay 覆盖
- 基于既有 M6 日志的回放
- 补充人工构造的边界场景：
  - ACK 缺失
  - recovery 握手
  - stale / respawn
  - gate review fail -> re-review pass

### 7.5 Exit Criteria 验证
- 记录 Dolt server 启动、连接、健康检查与恢复操作的实际运维成本
- 验证是否出现 shadow database、stale server、lock contention、空库误连等高摩擦问题
- 评估 `beads` 是否在当前阶段提供了超出“重型存储壳”的实际收益

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| beads 文档与实现存在版本漂移 | 初始化和命令使用方式误判 | 固定版本；实现以源码和实际命令行为为准 |
| 多 worktree 各自初始化 `.beads` | 控制面分裂 | 明确共享 `BEADS_DIR`；wrapper 启动时校验路径 |
| agent 仍绕过 wrapper 直接写文件 | 状态分叉 | skill 与 prompt 明确禁止；`scripts/devcoord` 生成文件时覆盖旧内容 |
| 把过多协议逻辑留在 skill | 难测试，仍依赖模型稳定性 | skill 只负责调用，状态机逻辑全部下沉脚本 |
| 把 beads 当作状态机引擎 | 语义错配，设计失真 | 明确 `scripts/devcoord` 是协议语义唯一实现层 |
| 审计事件被当作临时对象删除 | 审计链断裂 | 审计事件只使用 append-only event bead，不使用 wisp |
| 远程 sync 过早引入 | 额外复杂度 | M7 初期只做本机单控制面 |
| 把执行路径一并写死在状态机 | handoff / rescue / 并行探索能力下降 | 将状态机限制在治理边界；把执行路径保持为 lease / handoff 风格 |
| Dolt 运维成本超出当前规模承受范围 | 控制面收益被运维噪声抵消 | 在 Shadow Mode 记录实际成本；若超过 exit criteria，则替换存储适配层而保留 `just -> scripts/devcoord` 接口 |

## 9. 回滚策略

- 若 beads 控制面未稳定，可回退到“wrapper 停用 + 继续人工维护 `dev_docs/logs/*`”。
- `dev_docs` 文件保持兼容输出，确保回滚不需要恢复旧格式。
- M7 任一阶段失败，不影响产品主链路功能与产品数据面。

### 9.1 Exit Criteria
满足以下任一情况时，应评估停止将 `beads / Dolt` 作为 M7 的默认存储后端，并切换到更轻量的适配实现：

- Shadow Mode 或 PM First 阶段反复出现 shadow database、stale server、锁冲突、空库误连等问题，且无法通过 wrapper 和运维约束稳定消除。
- 运行期需要频繁人工介入 Dolt server 生命周期、锁清理或数据同步诊断，运维心智负担超过当前项目规模可接受范围。
- Phase 1 / 2 结束后，`beads / Dolt` 未显著提升共享状态查询、历史回放或协作可见性，仅充当高成本存储壳。

切换要求：
- 保持 `just -> scripts/devcoord` 命令面不变。
- 仅替换存储适配层，不回退到 prompt 直接维护日志文件。
- `dev_docs` projection 输出格式继续保持兼容。

### 9.2 未来分级存储
当前阶段统一采用 append-only 审计事件。

若未来协调事件量显著增长，可再评估分级策略：
- 审计级事件：持久保存
- 操作级临时协调消息：允许采用更轻量、可压缩或临时生命周期的承载方式

该优化不属于 M7 当前范围。

## 10. 下一步建议

1. 确认 ADR 0042 范围与边界。
2. 冻结 `dev_docs/devcoord/beads_control_plane.md` 中的数据模型与命令面。
3. 开始实现 `scripts/devcoord/coord.py` 与 `just coord-*` skeleton。
