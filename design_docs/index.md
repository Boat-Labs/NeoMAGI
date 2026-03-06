# Design Docs Index

> 目的：为 agent 提供“渐进式披露”的稳定入口，先读必要文档，再按任务补充读取。  
> 维护原则：根目录只保留跨阶段仍默认需要的设计文档；已完成的 Phase 1 文档归档到 `design_docs/phase1/`。

## 1. 当前状态

- Phase 1 milestone 设计文档已整体归档到 `design_docs/phase1/`。
- Phase 2 文档已开始进入 `design_docs/phase2/`。
- 根目录当前只保留跨阶段文档与当前 phase 的高层入口，目的是减少默认上下文负担。
- 为避免跨 phase 歧义，跨阶段文档中的 milestone 编号默认采用 `P1-M*` / `P2-M*` 形式。
- 若任务明确需要历史 milestone 细节、旧验收口径或回归考古，再进入 `design_docs/phase1/`。

## 2. 默认读取顺序（最小上下文）

1. `design_docs/phase2/roadmap_milestones_v1.md`
   - 当前生效中的 Phase 2 产品路线图草案。

2. `design_docs/phase2/index.md`
   - Phase 2 技术架构索引：`P2-M1 ~ P2-M4` 的 architecture 文档入口。

3. `design_docs/modules.md`
   - 当前系统模块边界、平台基线与主要实现入口。

4. `design_docs/system_prompt.md`
   - 运行时 prompt 文件加载顺序、优先级与 workspace context 分层。

5. `design_docs/memory_architecture_v2.md`
   - 长期 memory 原则：workspace 真源、retrieval plane、kernel / applications 分层。

6. `design_docs/procedure_runtime_draft.md`
   - Phase 2 方向的 deterministic procedure / runtime control 草案。

7. `design_docs/skill_objects_runtime_draft.md`
   - `2+1` 中第二层：skill object 的结构、程序化投影与学习草案。

8. `design_docs/phase1/index.md`
   - 只有在需要 Phase 1 历史设计细节时再进入。

## 3. 当前默认激活文档

- `design_docs/phase2/roadmap_milestones_v1.md`
- `design_docs/phase2/index.md`
- `design_docs/modules.md`
- `design_docs/system_prompt.md`
- `design_docs/memory_architecture_v2.md`
- `design_docs/procedure_runtime_draft.md`
- `design_docs/skill_objects_runtime_draft.md`

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

## 6. 外部参考（按需）

- `OpenClaw`: [https://github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
  - 主要架构参考方向：`src/agents/`、`src/memory/`、`src/gateway/`
- `pi-mono`: [https://github.com/badlogic/pi-mono](https://github.com/badlogic/pi-mono)
- `OpenClaw DeepWiki`: [https://deepwiki.com/openclaw/openclaw](https://deepwiki.com/openclaw/openclaw)

建议读取策略：
- 先看本索引 -> 再按默认顺序读取 -> 遇到具体主题再加载场景文档 -> 需要取舍依据时查 ADR。
