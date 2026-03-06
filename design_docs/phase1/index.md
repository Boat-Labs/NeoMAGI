# Phase 1 Archive Index

> 目的：归档已完成的 Phase 1 设计文档，避免它们继续占据后续阶段的默认上下文。  
> 使用原则：只有在做历史回溯、回归排查、已完成里程碑对账时才默认读取本目录。

## 1. 何时读取

- 需要追溯 Phase 1 的产品边界、验收口径和已完成 milestone 顺序。
- 需要核对某个历史设计决策当时对应的 architecture 文档。
- 需要查看 `P1-M3` ~ `P1-M6` 的手工验收步骤或复现既有行为。

说明：
- 为避免与后续 Phase 2 里程碑混淆，本目录中的 milestone 正文统一按 `P1-M*` 理解。

## 2. 产品路线图（归档）

- `design_docs/phase1/roadmap_milestones.md`：Phase 1 roadmap v1
- `design_docs/phase1/roadmap_milestones_v2.md`：Phase 1 roadmap v2
- `design_docs/phase1/roadmap_milestones_v3.md`：Phase 1 收口时的最终产品路线图（`P1-M1.5` ~ `P1-M7`）

## 3. Milestone Architecture（归档）

- `design_docs/phase1/m1_architecture.md`：`P1-M1`
- `design_docs/phase1/m1_5_architecture.md`：`P1-M1.5`
- `design_docs/phase1/m2_architecture.md`：`P1-M2`
- `design_docs/phase1/m3_architecture.md`：`P1-M3`
- `design_docs/phase1/m4_architecture.md`：`P1-M4`
- `design_docs/phase1/m5_architecture.md`：`P1-M5`
- `design_docs/phase1/m6_architecture.md`：`P1-M6`

说明：
- `P1-M7` 无独立 architecture 文档；如需回溯，查看 `dev_docs/plans/phase1/m7_devcoord-refactor_2026-02-28_v2.md`、`dev_docs/reviews/m7_summary_2026-03-01.md`、`dev_docs/devcoord/beads_control_plane.md`。

## 4. 用户验收与手工测试（归档）

- `design_docs/phase1/m3_user_test_guide.md`：`P1-M3`
- `design_docs/phase1/m4_user_test_guide.md`：`P1-M4`
- `design_docs/phase1/m5_user_test_guide.md`：`P1-M5`
- `design_docs/phase1/m6_user_test_guide.md`：`P1-M6`

## 5. 早期历史文档（归档）

- `design_docs/phase1/memory_architecture.md`：早期 memory 规划文档

说明：
- 当前长期 memory 原则以根目录的 `design_docs/memory_architecture_v2.md` 为准。
