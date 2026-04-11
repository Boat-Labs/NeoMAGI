---
doc_id: 019d7d4c-1b68-78d4-b78c-ef1463ddf5ce
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-11T18:07:13+02:00
---
# P3 Governed Self-Evolution Workflow（候选）

> 状态：candidate
> 来源：原 `P2-M5` 方向，依据 ADR 0061 移出 Phase 2。
> 注意：本文不是 active implementation plan；P2-M3 收口后再决定是否进入 P3 roadmap。

## 1. 目标

- 将 NeoMAGI 的 self-evolution 从“人类手工编排多个 coding agent”推进到“系统受治理地协调一次工程演进闭环”。
- 通过固定 procedure、beads、git worktree、coding agent runner、review loop 与 human gate，完成一个真实 sub-milestone。
- 保持可审计、可恢复、可停手；不自动 merge，不自动判定 UAT 通过，不绕过人类审批。

一句话：

**P3 self-evolution workflow 证明 NeoMAGI 能受治理地推进自己的工程演进，但仍不宣称具备无边界自治自改能力。**

## 2. 前置条件

进入正式 P3 计划前，至少需要：

- `P2-M2c`：`procedure_spec` 已进入 governance adapter，可被 propose / evaluate / apply / rollback。
- `P2-M2d`：memory source ledger prep 已完成，新写入至少能双写 DB ledger 与 workspace projection。
- `P2-M3`：principal / binding / visibility policy 已稳定，human gate 能关联明确 principal 与 approval audit。

不要求：

- Slack / 群聊。
- 浏览器自动化。
- 外部平台写动作。
- 完整 Shared Companion 产品 demo。

## 3. 边界

### In

- local repo / local git worktree。
- beads task ledger。
- fixed self-evolution procedure。
- runner contract / fixture runner / 最多一个真实 coding agent runner。
- plan review 与 implementation review loop。
- scope gate / plan gate / UAT gate。
- closeout artifacts：
  - approved plan
  - review report
  - implementation summary
  - progress update
  - user test guide
  - open issues

### Out

- 不自动 merge 到 `main`。
- 不自动关闭 parent issue。
- 不自动判定 UAT 通过。
- 不接 Slack / 群聊。
- 不接浏览器或外部平台写动作。
- 不让 worker 获得无限通用写权限。
- 不把一次成功 workflow 宣称为完整自治自改能力。

## 4. 候选拆分

### P3-SE-1：Runner Contract & Fixture

- 定义 runner typed I/O contract。
- 实现 fixture runner 或 dry-run runner。
- 验证 timeout、partial output、non-zero exit、interrupted run 的错误分类。

### P3-SE-2：Self-Evolution ProcedureSpec

- 落 `self_evolve_submilestone_v1`。
- 固定 state、checkpoint、gate、round limit 与 artifact path contract。
- 验证中断 / resume / blocked-for-human-decision。

### P3-SE-3：Review Loop & Artifacts

- 接入一个真实 runner 或继续使用 fixture runner。
- 首轮限制为 1 轮 plan review 与 1 轮 implementation review。
- 产出 plan、review report、summary、progress、user test guide、open issues。

### P3-SE-4：First Real Local Run

- 选择一个低风险、已批准 sub-milestone。
- 使用 fresh branch / worktree。
- 跑完整 scope gate -> plan gate -> implementation/review -> UAT pending。
- 不自动 merge。

## 5. 验收

- 能推进：真实 sub-milestone 被推进到 UAT pending。
- 能收敛：review loop 在轮次上限内消除 P1/P2；不能消除时停手。
- 能留痕：beads、git、docs、progress、approval audit 均可追溯。
- 能恢复：中断后从最近 checkpoint 恢复，不要求人类重建上下文。
- 能停手：scope 未批准、plan 未批准、runner 失败、review 未收敛、UAT 未完成时，不继续推进。
