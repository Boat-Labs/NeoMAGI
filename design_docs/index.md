---
doc_id: 019d6457-9290-71ce-b9a6-7146cffcd2d2
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-06T21:49:14+02:00
---
# Design Docs Index

> 目的：为 agent 提供“渐进式披露”的稳定入口，先读必要文档，再按任务补充读取。  
> 维护原则：根目录只保留跨阶段仍默认需要的设计文档；已完成的 Phase 1 文档归档到 `design_docs/phase1/`。

## 1. 当前状态

- Phase 1 milestone 设计文档已整体归档到 `design_docs/phase1/`。
- Phase 2 文档已开始进入 `design_docs/phase2/`。
- Phase 3 当前仅有候选方向入口 `design_docs/phase3/`，不作为 active roadmap。
- 根目录当前只保留跨阶段文档与当前 phase 的高层入口，目的是减少默认上下文负担。
- 为避免跨 phase 歧义，跨阶段文档中的 milestone 编号默认采用 `P1-M*` / `P2-M*` 形式。
- 若任务明确需要历史 milestone 细节、旧验收口径或回归考古，再进入 `design_docs/phase1/`。

## 2. 默认读取顺序（最小上下文）

1. `design_docs/phase2/roadmap_milestones_v1.md`
   - 当前生效中的 Phase 2 产品路线图草案。

2. `design_docs/phase2/index.md`
   - Phase 2 技术架构索引：当前范围收敛为 `P2-M1 ~ P2-M3`，并保留 `P2-M2c` / `P2-M2d` 后置地基。

3. `design_docs/GLOSSARY.md`
   - 轻量级 Domain Ontology：核心术语、别名、定义与关系。

4. `design_docs/modules.md`
   - 当前系统模块边界、平台基线与主要实现入口。

5. `design_docs/system_prompt.md`
   - 运行时 prompt 文件加载顺序、优先级与 workspace context 分层。

6. `design_docs/memory_architecture_v2.md`
   - 长期 memory 原则：DB append-only source ledger、workspace projection / export、retrieval plane、kernel / applications 分层。

7. `design_docs/procedure_runtime.md`
   - Phase 2 方向的 deterministic procedure / runtime control 正式设计文档。

8. `design_docs/skill_objects_runtime.md`
   - `2+1` 中第二层：skill object 的结构、程序化投影与学习正式设计文档。

9. `design_docs/devcoord_sqlite_control_plane.md`
   - `devcoord` 从 `beads` 解耦后的 SQLite control-plane store 与精简命令面设计。

10. `design_docs/devcoord_sqlite_control_plane_product.md`
   - `devcoord` SQLite control plane 的产品口径说明：为什么存在、怎么理解、和 `bd` / PostgreSQL / `dev_docs` 的边界。

11. `design_docs/phase1/index.md`
   - 只有在需要 Phase 1 历史设计细节时再进入。

12. `design_docs/phase3/index.md`
   - 只有在需要 Phase 3 候选方向时再进入；当前不作为 active implementation plan。

## 3. 当前默认激活文档

- `design_docs/phase2/roadmap_milestones_v1.md`
- `design_docs/phase2/index.md`
- `design_docs/GLOSSARY.md`
- `design_docs/modules.md`
- `design_docs/system_prompt.md`
- `design_docs/memory_architecture_v2.md`
- `design_docs/procedure_runtime.md`
- `design_docs/skill_objects_runtime.md`
- `design_docs/devcoord_sqlite_control_plane.md`（仅在需要 devcoord / 协作控制面重构时）
- `design_docs/devcoord_sqlite_control_plane_product.md`（仅在需要产品口径或高层心智模型时）

## 4. Phase 1 归档入口

- `design_docs/phase1/index.md`
- `design_docs/phase1/roadmap_milestones_v3.md`
- `design_docs/phase1/m1_architecture.md`
- `design_docs/phase1/m1_5_architecture.md`
- `design_docs/phase1/m2_architecture.md` ~ `design_docs/phase1/m6_architecture.md`
- `design_docs/phase1/m3_user_test_guide.md` ~ `design_docs/phase1/m6_user_test_guide.md`
- `design_docs/phase1/memory_architecture.md`

说明：
- `design_docs/phase1/roadmap_milestones_v3.md` 是 Phase 1 收口时的最终产品路线图，不再作为后续阶段默认入口。
- 当前 memory 原则文档以根目录 `design_docs/memory_architecture_v2.md` 为准。

## 5. 与其他目录的关系

- 产品与阶段进度：`dev_docs/progress/project_progress.md`
- 计划执行细节：当前阶段优先看 `dev_docs/plans/phase2/`；Phase 1 归档计划看 `dev_docs/plans/phase1/`
- 关键技术取舍：`decisions/`（含 `decisions/INDEX.md`）

## 6. 数据模型入口（按需）

- `design_docs/data_models/postgresql/index.md`
  - 应用 PostgreSQL 表的数据模型总索引；按功能分组到逐表说明文件。
- 适用场景：
  - 读库结构、手写 SQL、看验收数据面
  - 做 migration / schema patch
  - 判断 current-state 表与 governance ledger 表的边界
  - 追踪某张表和设计文档/实现代码之间的双向链接

说明：
- 该入口默认不是“最小上下文”必读文档；只有在数据库、持久化、治理账本、检索投影相关任务里再加载。
- 逐表文档会同时链接回总索引以及相关代码 / migration 来源，方便后续 schema 变更时同步维护。

## 7. 外部参考（按需）

- `OpenClaw`: [https://github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
  - 主要架构参考方向：`src/agents/`、`src/memory/`、`src/gateway/`
- `pi-mono`: [https://github.com/badlogic/pi-mono](https://github.com/badlogic/pi-mono)
- `OpenClaw DeepWiki`: [https://deepwiki.com/openclaw/openclaw](https://deepwiki.com/openclaw/openclaw)
- `design_docs/templates/SOUL.default.md`
  - `SOUL.md` 默认参考模板；只作为文档模板，不会自动进入 runtime，除非被复制到 workspace 根目录命名为 `SOUL.md`。

建议读取策略：
- 先看本索引 -> 再按默认顺序读取 -> 遇到具体主题再加载场景文档 -> 需要取舍依据时查 ADR。
