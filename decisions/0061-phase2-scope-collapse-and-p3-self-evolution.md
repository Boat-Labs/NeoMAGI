---
doc_id: 019d7d4c-1398-7ebb-a552-836db604046c
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-11T18:07:11+02:00
---
# 0061-phase2-scope-collapse-and-p3-self-evolution

- Status: accepted
- Date: 2026-04-11
- Amends: `design_docs/phase2/roadmap_milestones_v1.md`, ADR 0047, ADR 0059

## 背景

P2 已经承担了 growth governance、procedure runtime、multi-agent execution、memory ledger migration prep、identity / visibility policy 等多条高风险基础线。

原路线图把外部协作表面 (`P2-M4`) 与完整 self-evolution workflow (`P2-M5`) 也纳入 P2。复盘后，这会让 P2 从“冻结不能返工的地基”膨胀成“同时交付多个产品扩展面”。

同时，Slack / 群聊当前不是 NeoMAGI 的核心价值验证路径。它只有在真实协作、审批、通知或外部工作流入口明确后才有价值；现在规划会提前引入渠道复杂度。

## 选了什么

- Phase 2 收敛为：
  - `P2-M1`：Growth Governance & Builder Foundations
  - `P2-M2`：Procedure Runtime & Multi-Agent Execution
  - `P2-M2c`：ProcedureSpec Governance Adapter
  - `P2-M2d`：Memory Source Ledger Prep for P2-M3
  - `P2-M3`：Principal & Memory Safety
- 从 P2 删除原 `P2-M4` 外部协作与动作表面扩展。
- 从 P2 删除原 `P2-M5` 受治理自我演进工作流。
- 原 `P2-M5` 方向迁移到 Phase 3，作为 `P3 Governed Self-Evolution Workflow` 候选方向。
- Slack / 群聊暂不进入已规划 milestone；只保留为未来可选 interaction surface，不作为 P2 或 P3 默认计划。
- `P2-M3` 不交付完整 Shared Companion，只保留 principal、visibility、memory ledger 与 shared-space deny-by-default 地基。

## 为什么

- P2 的最高价值是冻结后续难返工的基础契约：growth governance、procedure governance、memory truth、principal / visibility。
- 外部协作渠道、外部平台动作和完整 self-evolution workflow 都是这些基础契约之上的产品扩展，不应阻塞 P2 收口。
- 完整 self-evolution workflow 依赖 stable procedure spec、memory ledger、principal / approval audit 和 runner contract；把它放在 P2 会把多个未稳定面混成一个大验收。
- Slack / 群聊优先级低，且容易把 Shared Companion 误解成渠道功能；当前更应先保证 shared-space policy 不泄漏私有记忆。

## 放弃了什么

- 方案 A：保留 `P2-M4` 作为 Slack / 外部动作表面 milestone。
  - 放弃原因：它不是 P2 地基能力，会扩大渠道和外部动作风险。
- 方案 B：保留 `P2-M5` 作为 P2 的完整 self-evolution demo。
  - 放弃原因：它是组合验收，适合 Phase 3；若作为 P2 关闭条件，会拖慢 P2 收口。
- 方案 C：把 Slack 作为 P3 的明确计划项。
  - 放弃原因：目前价值不够明确，暂不占用 roadmap slot；未来有真实协作需求后再评估。
- 方案 D：在 P2-M3 交付完整 Shared Companion。
  - 放弃原因：P2-M3 先冻结 principal / visibility / ledger 地基，完整 relationship UX 和 lifecycle 推迟。

## 影响

- `design_docs/phase2/roadmap_milestones_v1.md` 应只规划到 `P2-M3`，并保留 `P2-M2c` / `P2-M2d` 作为 M2 后置地基。
- `design_docs/phase2/p2_m4_architecture.md` 删除，不再作为 active/future P2 plan。
- `design_docs/phase2/p2_m5_architecture.md` 迁移到 Phase 3 文档。
- `design_docs/phase2/p2_m3_architecture.md` 应明确：Shared Companion 在 P2-M3 仅做 deny-by-default 地基，不做完整产品 demo。
- ADR 0047 / ADR 0059 中关于 Slack / P2-M4 的表述应改为未来可选，而不是 P2 planned milestone。
