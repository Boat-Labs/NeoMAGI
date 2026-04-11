---
doc_id: 019d0ff2-0ed8-729c-8051-9cb58aa855c4
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-21T11:30:15+01:00
---
# Phase 2 Design Index

> 目的：为 Phase 2 提供技术架构入口。  
> 使用原则：先看产品路线图，再按里程碑读取对应 architecture 文档；实现细节不足时再进入跨阶段设计文档。

## 1. 推荐读取顺序

1. `design_docs/phase2/roadmap_milestones_v1.md`
   - Phase 2 产品口径、推荐顺序与验收。

2. `design_docs/phase2/p2_m1_architecture.md`
   - `P2-M1`：显式成长、builder 治理、atomic tools / skill objects / work memory。

3. `design_docs/phase2/p2_m1_user_test_guide.md`
   - `P2-M1`：完成态用户测试方案，覆盖 WebChat 手工验证与 growth case 回放验证。

4. `design_docs/phase2/p2_m2_architecture.md`
   - `P2-M2`：Procedure Runtime、多 agent runtime、handoff / steering / resume。

5. `design_docs/phase2/p2_m2_post_self_evolution_staged_plan.md`
   - `P2-M2` 后置地基与 P3 self-evolution 候选说明：`P2-M2c` / `P2-M2d` 作为前置地基，完整 CLI workflow 移出 P2、迁入 Phase 3 候选方向。

6. `design_docs/phase2/p2_m3_architecture.md`
   - `P2-M3`：Principal & Memory Safety；不交付完整 Shared Companion，shared-space 默认为 deny-by-default 地基。

## 2. 跨阶段设计（按需）

- `design_docs/procedure_runtime.md`
  - `P2-M2` 的底层 runtime control object 正式设计文档。
- `design_docs/skill_objects_runtime.md`
  - `P2-M1` 的 skill object 运行时经验层正式设计文档。
- `design_docs/memory_architecture_v2.md`
  - `P2-M3` 的 memory kernel / applications 长期原则。
- `decisions/0059-shared-companion-relationship-space-boundary.md`
  - Shared Companion 的 relationship/shared-space 边界：`P2-M2` 预留 runtime context，`P2-M3` 只落地 principal / visibility / deny-by-default 地基。
- `decisions/0060-memory-source-ledger-db-with-workspace-projections.md`
  - Memory truth 调整为 DB append-only ledger，workspace memory 文件作为 projection / export；`P2-M2d` 只做 schema / writer 双写预备，完整 read / reindex 切换归入 `P2-M3`。
- `decisions/0061-phase2-scope-collapse-and-p3-self-evolution.md`
  - P2 范围收缩到 `P2-M3`；原 P2-M4/P2-M5 移出 P2，self-evolution workflow 迁入 Phase 3 候选方向，Slack 暂不规划。

## 3. 当前状态

- `P2-M1` / `P2-M2` 已实施；`P2-M2c` / `P2-M2d` / `P2-M3` 是当前剩余 Phase 2 规划范围。
- 这些文档只定义目标、当前基线、目标架构、边界、验收与建议拆分。
- 具体 task 拆分、owner、工期和测试文件清单应进入 `dev_docs/plans/phase2/`。
