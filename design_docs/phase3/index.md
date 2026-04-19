---
doc_id: 019d7d4c-1780-73f4-b2bb-32be6c89d374
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-11T18:07:12+02:00
---
# Phase 3 Design Index

> 状态：draft
> 说明：Phase 3 当前切换为 daily use 补完方向草稿，不作为已批准实施计划。正式执行前仍需进入对应 `dev_docs/plans/phase3/` 计划文件并完成审批。

## 1. 当前草稿

1. `design_docs/phase3/p3_daily_use_roadmap_draft.md`
   - P3 daily use roadmap 草稿：只记录用户产品口径、目标、milestone 与验收。

2. `design_docs/phase3/p3_daily_use_architecture_draft.md`
   - P3 daily use architecture 草稿：记录 runtime profile、provider、memory、web、artifact/run、CLI wrapper、部署与前端的 high-level 技术决定。

## 2. Related Decisions

- ADR 0062：P3 主线调整为 daily-use capability completion。
- ADR 0063：Memory truth 已收口为 Postgres ledger，P3 只补 projection / export hardening。
- ADR 0064：Artifact / run metadata 边界。
- ADR 0065：Run-level provider / model selection。

## 3. 降级方向

- 受治理 self-evolution workflow 暂不作为 P3 主线。
- Procedure Runtime、Skill Learner、Growth Governance、devcoord 在 P3 daily path 中默认冻结或后台化。

## 4. 明确不规划

- Slack / 群聊暂不进入已规划 milestone。
- 外部平台写动作暂不进入默认产品路径。
- 完整 Shared Companion 产品 demo 暂不进入 P2，是否进入 P3 需另行评估。
