---
doc_id: 019da594-dd6e-7e53-a429-c1cd45202cec
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-19T13:51:29+02:00
---
# 0062-phase3-daily-use-capability-completion

- Status: accepted
- Date: 2026-04-19
- Supersedes: Phase 3 direction in ADR 0061
- Related: `design_docs/phase3/p3_daily_use_roadmap_draft.md`, `design_docs/phase3/p3_daily_use_architecture_draft.md`

## 背景

ADR 0061 将完整 self-evolution workflow 从 P2 移入 Phase 3 候选方向。后续复盘发现，NeoMAGI 当前最大风险不是缺少更多治理，而是 daily-use 工具面不足、真实使用案例不足、governance velocity 明显领先 usage velocity。

NeoMAGI 的核心使命仍是 personal agent：持续记忆、代表用户信息利益、可从商业 API 平滑迁移到本地模型。要验证这个使命，P3 首先需要让系统成为用户每天愿意打开的工作入口，而不是继续扩大自我演进和协作治理机制。

## 选了什么

- Phase 3 主线调整为 `Daily Use Capability Completion`。
- P3 优先补齐 daily use 所需能力：
  - provider / model 选择；
  - web search / fetch；
  - memory search 实用性；
  - Postgres memory truth 与 workspace projection；
  - artifacts / runs；
  - 受控 CLI wrapper；
  - 文件 / 图片 / Python execution 等按真实需求扩展的工具面。
- 完整 self-evolution workflow 从 P3 主线降级为未来候选或 `growth_lab` 实验，不作为 P3 默认 roadmap。
- P3 默认运行形态为 daily profile：`core loop + memory + tools + provider routing`。
- Procedure Runtime、Skill Learner、Growth Governance、devcoord 在 P3 daily path 中冻结或后台化。
- P3-M1 必须产出至少 30 条真实 daily cases，并满足最低覆盖门槛，作为后续扩展依据。

## 为什么

- 当前真实 runtime case 密度不足，继续扩治理会让设计缺少实践校准。
- 用户 daily use 是检验 NeoMAGI mission 的最短闭环：如果用户不会每天使用，self-evolution workflow 即使可运行也缺少产品牵引。
- 治理能力应该服务真实任务，而不是先于真实任务继续扩张。
- daily tools、memory、provider routing、artifact surface 是替代部分 claude.ai / ChatGPT 使用的前置能力。
- 复杂治理默认关闭可以降低日常路径的不确定性，让真实使用数据更干净。

## 放弃了什么

- 方案 A：继续把 P3 定义为 Governed Self-Evolution Workflow。
  - 放弃原因：它是组合型工程演进能力，依赖稳定 tools、memory、artifact、provider、approval 与真实 case；当前直接推进会继续放大治理领先产品的问题。
- 方案 B：P3 同时推进 daily use 与完整 self-evolution。
  - 放弃原因：范围过大，会混淆验收；daily use 问题应先独立收口。
- 方案 C：保持 P3 只有候选方向，不做明确主线调整。
  - 放弃原因：会让文档入口继续把 self-evolution 误读为默认下一阶段。

## 影响

- `design_docs/phase3/` 当前入口改为 daily-use roadmap / architecture 草稿。
- `design_docs/phase2/p2_m2_post_self_evolution_staged_plan.md` 改为 historical / superseded 提案口径。
- P3 implementation plan 应围绕 daily use 拆分，而不是围绕 self-evolution workflow 拆分。
- Procedure Runtime 和 Skill Learner 不删除，但进入 maintenance / observe-only 状态。
- 后续若重启 self-evolution workflow，必须以新的计划和验收重新批准，不得沿用旧 P3 候选口径直接开工。
