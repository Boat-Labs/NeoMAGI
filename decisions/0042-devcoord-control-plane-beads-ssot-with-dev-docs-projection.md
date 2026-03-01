# 0042-devcoord-control-plane-beads-ssot-with-dev-docs-projection

- Status: proposed
- Date: 2026-02-28

## 选了什么
- 将开发协作控制面（PM / backend / tester 的战术执行层）重构为：`beads + Dolt` 作为结构化 SSOT，`dev_docs/logs/` 与 `dev_docs/progress/` 作为兼容投影层。
- 将 M7 定位为“最小协调内核（minimal coordination kernel）”建设，而不是全流程 workflow engine；治理状态机只约束推进权限、审计、恢复与对账边界，不规定任务必须按唯一执行路径完成。
- 保持战略层文档不变：`decisions/`、`design_docs/`、`dev_docs/plans/`、`dev_docs/reviews/`、`dev_docs/reports/` 继续作为人工审阅与长期记忆的主载体。
- 将运行时调用边界固定为：LLM 负责判断与参数提取；`just` 负责稳定入口；`scripts/devcoord` 负责 deterministic 执行；agent 不直接写 `dev_docs` 日志文件，不自由拼装 `bd` 命令细节。skill 仅作为行为约束与调用规范存在，不作为额外运行时分层。
- 明确责任边界：`scripts/devcoord` 是协作协议与状态机语义的唯一实现层；`beads + Dolt` 只承担 persistence / query / history，不承担 ACK、生效、恢复握手、超时判定、对账等协议语义。
- 执行层默认采用 lease / handoff 协作方式，而不是把任务永久锁死在单一路径；后续允许在少量适合的任务上引入 `open_competition`，但不改变 Gate / ACK 等治理边界。
- 初期采用单机共享控制面：所有 worktree 共享一个 `BEADS_DIR` 与同一套 Dolt server 状态，不引入远程 `beads-sync` 或产品运行时数据面改造。
- 迁移方式采用 `shadow mode -> PM first -> teammate cutover`，先生成兼容投影，待稳定后再停止人工维护旧日志文件。

## 为什么
- 当前协作控制依赖 prompt 约束 PM 手工维护 `heartbeat_events.jsonl`、`gate_state.md`、`watchdog_status.md` 等文件，真实状态与投影文件之间缺乏结构化约束，append-first、ACK 生效、恢复握手、对账等规则过度依赖人工执行。
- `beads` 已具备适合协作控制面的基础能力：结构化 metadata、agent state、message/thread、版本化历史与查询能力，适合承载控制面对象的结构化存储与查询；但协议状态机仍需由 `scripts/devcoord` 显式实现。
- 把确定性流程从 prompt 下沉到脚本，可以显著降低上下文噪声与协议漂移风险，使状态机、幂等、投影生成与回归测试可程序化验证。
- 当前主要问题是治理边界失真，而不是任务执行路径不够刚性；如果把完整执行路径一并写死在状态机里，会削弱 handoff、rescue 与并行探索能力。
- 借鉴 `beads / Gas Town` 的重点是共享状态、异步 handoff 与活性监控，而不是照搬其对象名词或把所有任务做成固定流程。
- 该方案与项目现有“DB 作为 SSOT、文件作为 projection”的治理方向一致，能够复用已有概念，不增加新的长期认知负担。
- 本次改造聚焦开发协作控制面，不触碰产品运行时 PostgreSQL 数据面，能在不影响用户功能的前提下改善 Agent Teams 的执行可靠性。

## 放弃了什么
- 方案 A：继续以 `dev_docs/logs/*.md` + `*.jsonl` 为唯一控制面，仅优化 prompt 约束。
  - 放弃原因：核心问题不在文案，而在状态机缺少结构化存储与程序化校验；继续堆 prompt 只能提高复杂度。
- 方案 B：让 agent 直接自由使用 `bd` / `beads` 命令。
  - 放弃原因：命令拼装、metadata key、状态机顺序、投影刷新都会再次回到模型不稳定区间，无法满足 deterministic 目标。
- 方案 C：自研一套新的 PostgreSQL 协作控制面。
  - 放弃原因：虽然与产品数据库栈一致，但会重复建设任务图、审计历史、状态查询与多写者能力，当前阶段性价比不足。

## 影响
- 新增内部治理里程碑 `M7`，其性质为“开发协作控制面重构”，不属于产品 roadmap 新功能里程碑，不改变 `design_docs/roadmap_milestones_v3.md` 的产品排序。
- 将新增 `scripts/devcoord/`、`just coord-*` 命令面与对应 skill 规范，所有协作状态写入统一走 wrapper，不允许直接改 `dev_docs/logs/` 文件。
- 将新增 `dev_docs/devcoord/beads_control_plane.md` 作为控制面架构文档，定义对象模型、命令面、投影规则、共享 `BEADS_DIR` 拓扑与迁移顺序。
- M7 Phase 1 将固定通过 `bd ... --json` CLI shell-out 与 beads 交互；不直接写 Dolt SQL，不通过 MCP server。
- M7 的状态机实现应保持“治理层强约束、执行层弱路径”：前者负责权限与审计，后者保留 lease / handoff / rescue / competition 的弹性空间。
- `dev_docs/logs/README.md`、PM Action Plan、teammate tactical prompt 后续需要改写为“调用控制面接口”，而非“直接维护日志文件”。
- 需要为“产品运行时 PostgreSQL SSOT”与“开发协作控制面 beads SSOT”做边界澄清：前者仍是产品数据面唯一基线，后者仅是内部治理与执行态控制面。
