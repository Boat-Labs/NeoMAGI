# Phase 2 Design Index

> 目的：为 Phase 2 提供技术架构入口。  
> 使用原则：先看产品路线图，再按里程碑读取对应 architecture 文档；实现细节不足时再进入跨阶段草案。

## 1. 推荐读取顺序

1. `design_docs/phase2/roadmap_milestones_v1.md`
   - Phase 2 产品口径、推荐顺序与验收。

2. `design_docs/phase2/p2_m1_architecture.md`
   - `P2-M1`：显式成长、builder 治理、atomic tools / skill objects / work memory。

3. `design_docs/phase2/p2_m2_architecture.md`
   - `P2-M2`：Procedure Runtime、多 agent runtime、handoff / steering / resume。

4. `design_docs/phase2/p2_m3_architecture.md`
   - `P2-M3`：身份认证、用户连续性、记忆质量与 memory applications。

5. `design_docs/phase2/p2_m4_architecture.md`
   - `P2-M4`：外部协作表面、Slack、浏览器/外部平台读写边界。

## 2. 跨阶段草案（按需）

- `design_docs/procedure_runtime_draft.md`
  - `P2-M2` 的底层 runtime control object 草案。
- `design_docs/skill_objects_runtime_draft.md`
  - `P2-M1` 的 skill object 运行时经验层草案。
- `design_docs/memory_architecture_v2.md`
  - `P2-M3` 的 memory kernel / applications 长期原则。

## 3. 当前状态

- 所有 `P2-M*` architecture 文档当前状态均为 `planned`。
- 这些文档只定义目标、当前基线、目标架构、边界、验收与建议拆分。
- 具体 task 拆分、owner、工期和测试文件清单应进入 `dev_docs/plans/phase2/`。
