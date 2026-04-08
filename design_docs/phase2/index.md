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

5. `design_docs/phase2/p2_m2_self_evolution_demo.md`
   - `P2` 首个重要 demo 方案：在 `P2-M2b` 之后，用 CLI + beads + git worktree 驱动受治理的自我演进闭环。

6. `design_docs/phase2/p2_m3_architecture.md`
   - `P2-M3`：身份认证、用户连续性、Shared Companion / relationship shared space、记忆质量与 memory applications。

7. `design_docs/phase2/p2_m4_architecture.md`
   - `P2-M4`：外部协作表面、Slack、浏览器/外部平台读写边界。

## 2. 跨阶段设计（按需）

- `design_docs/procedure_runtime.md`
  - `P2-M2` 的底层 runtime control object 正式设计文档。
- `design_docs/skill_objects_runtime.md`
  - `P2-M1` 的 skill object 运行时经验层正式设计文档。
- `design_docs/memory_architecture_v2.md`
  - `P2-M3` 的 memory kernel / applications 长期原则。
- `decisions/0059-shared-companion-relationship-space-boundary.md`
  - Shared Companion 的 relationship/shared-space 边界：`P2-M2` 预留 runtime context，`P2-M3` 落地 identity / membership / consent-scoped memory，`P2-M4` 承接协作表面。

## 3. 当前状态

- 所有 `P2-M*` architecture 文档当前状态均为 `planned`。
- 这些文档只定义目标、当前基线、目标架构、边界、验收与建议拆分。
- 具体 task 拆分、owner、工期和测试文件清单应进入 `dev_docs/plans/phase2/`。
