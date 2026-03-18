# P2-M1c 实施计划：Growth Cases 与 Capability Promotion

- Date: 2026-03-18
- Status: approved
- Scope: `P2-M1c` only; deliver the minimum audited closed loop for growth cases, builder work memory, and `skill_spec -> wrapper_tool` capability promotion on top of the completed `P2-M1b` skill runtime
- Basis:
  - [`design_docs/phase2/p2_m1_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m1_architecture.md)
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/skill_objects_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/skill_objects_runtime.md)
  - [`design_docs/GLOSSARY.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/GLOSSARY.md)
  - [`decisions/0048-skill-objects-as-runtime-experience-layer.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0048-skill-objects-as-runtime-experience-layer.md)
  - [`decisions/0049-growth-governance-kernel-adapter-first.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0049-growth-governance-kernel-adapter-first.md)
  - [`decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0050-devcoord-decouple-from-beads-and-use-sqlite-control-plane-store.md)
  - [`decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md)
  - [`decisions/0054-growth-eval-contracts-immutable-and-object-scoped.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0054-growth-eval-contracts-immutable-and-object-scoped.md)
  - [`dev_docs/plans/phase2/p2-m1a_growth-governance-kernel_2026-03-06.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1a_growth-governance-kernel_2026-03-06.md) (predecessor)
  - [`dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14.md) (predecessor)
  - [`dev_docs/plans/phase2/p2-m1b-prep_growth-eval-contract_2026-03-15.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1b-prep_growth-eval-contract_2026-03-15.md) (predecessor)
  - [`dev_docs/progress/project_progress.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/progress/project_progress.md)

## Context

`P2-M1a` 和 `P2-M1b` 已经把 `P2-M1` 的前两层基座搭起来了：

- `P2-M1a` 已交付统一 growth governance kernel：`GrowthGovernanceEngine`、`PolicyRegistry`、adapter contract、`SoulGovernedObjectAdapter`
- `P2-M1b` 已把 `skill_spec` 从 reserved 升格为 onboarded，并交付了 `SkillStore`、`SkillGovernedObjectAdapter`、`SkillResolver`、`SkillProjector`、`SkillLearner` 以及 `PromptBuilder` / `AgentLoop` join points
- 2026-03-18 的 `project_progress` 已记录 `P2-M1b closeout` 及三轮 post-review fixes，说明 M1c 的起点不是“继续补 skill runtime 本体”，而是基于已可用的 skill runtime 去完成 `P2-M1` 剩余的产品验收项

当前仍未闭环的部分，恰好对应 [`design_docs/phase2/p2_m1_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m1_architecture.md) 中的 `P2-M1c` 定义：

- 还没有正式的 `growth case` 目录、运行入口和证据闭环
- 还没有把较长的 coding / builder 任务中间状态沉淀到 `beads / bd` 工作记忆
- `wrapper_tool` 仍是 reserved kind，`skill_spec -> wrapper_tool` promote 还只有 policy schema 和 contract skeleton，没有 runtime
- 还没有至少一个“先学成 skill、再复用、再 promote 为稳定 capability 单元”的最小闭环

还有一个新边界必须显式写清：ADR 0050 之后，`devcoord` 已完全从 `beads` 解耦，`.devcoord/control.db` 才是协作控制面真源。  
因此 `P2-M1c` 里的 `beads work memory` 只能是：

- backlog / issue graph / builder task memory 的结构化载体
- 中间决策、blocker、验证结果和产物索引的工作记忆层

而不能重新回到：

- gate / ACK / heartbeat / closeout 的 control-plane 语义

同时，`P2-M2` 的 `Procedure Runtime` 还未开始，因此 `P2-M1c` 不能假装自己已经拥有：

- deterministic state / guard / transition
- cross-turn recoverable procedure instance
- 通用 workflow DSL

这意味着 `P2-M1c` 必须聚焦在一个更窄、但能真正验收的闭环上：

**先用少量受管 growth cases，把 skill reuse、builder work memory 和 `wrapper_tool` promote 跑通；procedure 级复杂流程继续留给 `P2-M2`。**

## Core Decision

`P2-M1c` 采用一个**case-driven、promotion-first、work-memory-backed** 的最小闭环，而不是开放式“自治代码搜索”或半成品 builder 模式：

1. `P2-M1c` 的主线交付不是再造一个更大的 runtime，而是固定 2~3 个 curated growth cases，要求每个 case 都能产出：
   - case brief
   - evidence refs
   - proposal handle
   - eval result
   - apply / rollback 结论
   - 下一轮复用或 promote 结果
2. `P2-M1c` 正式把 `bd / beads` 扩展为 builder work memory，但只承担：
   - task brief
   - progress snapshots
   - blockers
   - validation summary
   - artifact index
   - promote candidate index
   而不承担 control-plane 职责，也不成为长期 product memory truth
3. `P2-M1c` 只 onboarding 一个新的 growth object kind：
   - `wrapper_tool` = onboarded
   - `procedure_spec` 继续 reserved，推迟到 `P2-M2`
   - `memory_application_spec` 继续 reserved，推迟到 `P2-M3`
4. `wrapper_tool` 的 V1 只做**单 turn、typed、可 smoke-test、无跨 turn 状态机**的稳定能力封装：
   - 可以是 checked-in code-backed wrapper
   - 可以绑定现有 atomic tools
   - 但不能偷偷长成 procedure runtime
5. `skill_spec -> wrapper_tool` promote 继续走 `GrowthGovernanceEngine`：
   - proposal / eval / apply / rollback 全都保留
   - `raw code patch` 仍只是 implementation artifact / supporting evidence，不是 growth object
   - 普通 proposal 不能顺手修改自己的 judge / harness
6. `P2-M1c` 的 builder 语义保持“有结构化产物的任务模式”，但不冒充 `P2-M2` procedure：
   - 先解决“留下什么证据”和“何时 promote”
   - 不解决 deterministic transition / interrupt / resume

## Goals

- `G1.` 交付 `P2-M1c` 的 curated growth case catalog 与最小 case runner / orchestration 语义
- `G2.` 把 `bd / beads` 明确扩展成 coding / builder task 的工作记忆层，满足 roadmap 用例 B
- `G3.` 将 `wrapper_tool` 从 reserved 升格为 onboarded，成为 `P2-M1` 的第三类正式 growth object
- `G4.` 跑通至少一条 `skill_spec -> wrapper_tool` 的 promote 闭环，满足 roadmap 用例 C
- `G5.` 跑通至少一条“用户教授 / 外部经验导入 -> skill proposal -> skill reuse”的 case，并在相似任务中优先复用，满足 roadmap 用例 D
- `G6.` 让至少一条 growth case 完成 `propose -> evaluate -> apply`，满足 roadmap 用例 E
- `G7.` 让失败 case 明确落到 `veto` / `rollback` / `candidate only`，满足 roadmap 用例 F
- `G8.` 让 agent 可以回答一次成长“改了什么、为什么改、怎么验证、如何回滚”，满足 roadmap 用例 A

## Non-Goals

- 不在 `P2-M1c` 内实现 `Procedure Runtime`
- 不在 `P2-M1c` 内 onboarding `procedure_spec`
- 不在 `P2-M1c` 内 onboarding `memory_application_spec`
- 不在 `P2-M1c` 内把 `raw code patch` 提升为独立 growth object
- 不在 `P2-M1c` 内做开放式自治代码搜索、无限实验循环或 repo-wide autopatch
- 不在 `P2-M1c` 内实现自动 promote / 自动 apply / 自动 disable
- 不在 `P2-M1c` 内开放外部账号代发或高风险写动作
- 不在 `P2-M1c` 内把 `bd / beads` 重新拉回 devcoord control-plane
- 不在 `P2-M1c` 内引入通用 workflow DSL、跨 turn state graph 或复杂 branching wrapper
- 不在 `P2-M1c` 内重新设计 `SkillResolver` / `SkillLearner` 的主体算法；M1b 的 runtime 视为前提

## Proposed Architecture

### 1. Growth Case Plane

`P2-M1c` 先固定一层可审计的 case 目录，而不是“想学什么就临时跑什么”。

建议新增两个对象：

- `GrowthCaseSpec`
  - `case_id`
  - `title`
  - `source_kind`
  - `target_kind`
  - `contract_id`
  - `contract_version`
  - `entry_conditions`
  - `required_artifacts`
  - `success_rule`
  - `rollback_rule`
- `GrowthCaseRun`
  - `run_id`
  - `case_id`
  - `linked_bead_ids`
  - `status`
  - `proposal_refs`
  - `eval_refs`
  - `apply_refs`
  - `rollback_refs`
  - `artifact_refs`
  - `summary`

关键约束：

- case 是 curated catalog，不是临时 prompt 描述
- 每次 run 都必须 pin：
  - `target kind`
  - `contract_id`
  - `contract_version`
  - required artifacts
  - keep / rollback semantics
- growth case 的目标不是“偶然成功一次”，而是：
  - 生成可命名、可解释的 growth object
  - 在下一次相似任务中优先复用
  - 满足 promote 条件时进入更稳定的 capability 单元

持久化策略在 `P2-M1c` 中保持极简：

- `GrowthCaseSpec` 作为 hardcoded curated catalog，放在 `src/growth/cases.py`
- `GrowthCaseRun` 不进入 PostgreSQL；使用 workspace artifact（如 `dev_docs/cases/<case_id>/<run_id>.md`）保存，并通过 bead comments / artifact refs 建索引
- V1 case runner 的程序化访问通过 `dev_docs/cases/<case_id>/` 扫描和 bead refs 完成，不额外引入独立 run store / query API
- 原因：`P2-M1c` 只有 2~3 条 curated case，不值得为 case catalog / run records 新增 DB schema 与 migration

建议 `P2-M1c` 先只跑 2~3 条 case：

1. `GC-1 human_taught_skill_reuse`
   - 输入：用户明确教学
   - 产物：`skill_spec` proposal -> apply -> 相似任务 reuse
   - 目标：验证 D 用例，不要求 promote
2. `GC-2 skill_to_wrapper_tool_promotion`
   - 输入：已有 active skill + 多次成功 evidence + 明确 typed I/O 边界
   - 产物：`wrapper_tool` proposal -> evaluate -> apply -> registry 可见
   - 目标：验证 C/E/F 用例
3. `GC-3 external_readonly_experience_import`（可选）
   - 输入：外部经验源，如 Actionbook 或用户提供 SOP
   - 前提：先冻结 import 协议；若协议未冻结，则本 case 推迟，不纳入 `P2-M1c` 硬验收
   - 产物：先 skill，再在第二轮任务中复用；若边界稳定，再留下 promote candidate
   - 目标：验证“不要总从 0 开始”的外部经验闭环

### 2. Builder Work Memory Plane

`P2-M1c` 不引入新的 control-plane，而是把 `bd / beads` 用回它真正擅长的地方：

- issue graph
- append-only comments
- lightweight state
- artifact linking

V1 建议把 builder work memory 固定为“两层表达”：

1. `bd` issue 作为**任务索引与状态入口**
   - 标题 = 当前 builder task / growth case
   - labels / state = `planned | working | blocked | validating | done`
   - dependencies = 前置任务 / case 依赖 / follow-up
2. workspace artifact 作为**详细快照**
   - `dev_docs/builder_runs/<run_id>.md`
   - 或 `dev_docs/cases/<case_id>/<run_id>.md`
   - 记录结构化正文；`bd comments` 只引用路径和摘要

建议定义一个最小 `BuilderTaskRecord` 语义：

- `run_id`
- `bead_id`
- `task_brief`
- `scope`
- `decision_snapshots`
- `todo_items`
- `blockers`
- `artifact_refs`
- `validation_summary`
- `promote_candidates`
- `next_recommended_action`

这里的 `BuilderTaskRecord` 是逻辑对象，不要求 1:1 映射到 `bd` 的 metadata。  
V1 的 canonical record 应保存在 workspace artifact；`bd` 只承担最小索引层：

- issue title / description
- labels / state
- 关键 artifact refs
- progress / blocker / validation comments
- related proposal ids 或 promote candidate 摘要

关键边界：

- `bd / beads` 是 work memory / evidence index，不是 PostgreSQL product memory
- `bd / beads` 也是 builder/coding 的 task memory，不是 devcoord gate/ACK 语义
- 长文本和详细证据不强塞进 bead metadata；优先写 workspace artifact，再在 bead 中索引
- 任何 promote / apply / rollback 相关证据都必须能从 bead 跳到 workspace artifact 或 test output

`WP-B` 开始前必须先做一个 feasibility spike，确认当前 `bd` 至少满足：

- issue create / update
- comments append
- labels / state 表达
- artifact path 的可接受引用方式

若 spike 发现 `bd` 的 comment / label / state 面不足以承担最小索引层，则 fallback 固定为：

- `artifact-first`
- bead 只保留任务 envelope 和 artifact pointer
- 不在 `P2-M1c` 里为了 work memory 再造一层复杂 `bd` adapter

这样 `P2-M1c` 就能满足 roadmap 中“任务中间状态沉淀到 beads work memory”的要求，同时不重演 ADR 0050 已经关闭的语义混淆。

### 3. Wrapper Tool Plane

`wrapper_tool` 是 `P2-M1c` 要 onboarding 的新 growth object。

依据 [`design_docs/GLOSSARY.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/GLOSSARY.md) 的定义，V1 wrapper tool 必须满足：

- 对上层表现为一个稳定原子动作
- 可以组合底层动作，但不暴露成 procedure graph
- 超过单 turn typed capability 的复杂度，就必须升级到 `procedure_spec`

建议新增 `src/wrappers/` 领域模块，最小对象模型可为：

- `WrapperToolSpec`
  - `id`
  - `capability`
  - `version`
  - `summary`
  - `input_schema`
  - `output_schema`
  - `bound_atomic_tools`
  - `implementation_ref`
  - `deny_semantics`
  - `scope_claim`
  - `disabled`
- `WrapperToolProposalRecord`
  - governance ledger row 的 store-internal 结构

V1 明确只支持：

- checked-in implementation artifact
- typed input / output
- explicit permission / deny semantics
- single-turn callable behavior
- dry-run 或 smoke-test 可验证

V1 明确不支持：

- 跨 turn 状态迁移
- graph / branching / checkpoint recoverability
- 模糊的“帮我跑完一串流程”
- 在 apply 时偷偷生成新的 judge / harness

`implementation_ref` 的语义必须在 `WP-A` 冻结，避免 `WP-C` 再次漂移。  
`P2-M1c` 中建议固定为：

- Python entrypoint string：`<module_path>:<factory_name>`
- `factory_name` 返回 `BaseTool` 实例，或返回可立即实例化 / 注册的 `BaseTool` 子类
- `apply()` 时由 wrapper runtime resolver 解析该 entrypoint，再显式注入 `ToolRegistry`

V1 不接受：

- code blob
- 动态生成代码
- 仅有 checked-in file path 但无可执行工厂入口的模糊引用

### 4. Wrapper Tool Governance

`P2-M1c` 需要把 `wrapper_tool` 从 reserved 提升为 onboarded，并给它一个 object-scoped contract。

当前已有：

- `PolicyRegistry` 中的 reserved `wrapper_tool`
- `WRAPPER_TOOL_EVAL_CONTRACT_SKELETON`

`P2-M1c` 需要补齐：

- `WrapperToolGovernedObjectAdapter`
- `wrapper_tool` current-state store
- `wrapper_tool_versions` governance ledger
- contract V1 的 concrete checks 与 runner

V1 建议沿用 skeleton 的四层结构，但把执行语义具体化：

- `Boundary gates`
  - typed I/O validation
  - permission boundary
  - deny semantics
  - reversible registration / deregistration
- `Effect evidence`
  - before/after 或 first-run/replay case
  - smoke test summary
- `Scope claim`
  - `local` / `reusable` / `promotable`
  - claim 越强，需要越强 artifact
- `Efficiency metrics`
  - `P2-M1c` 只做记录，不作为主 gate

`apply()` 约束：

- 先要求 eval passed
- 再 materialize current-state store
- 再注册到 runtime `ToolRegistry`
- 任一步失败全部回滚

`rollback()` 约束：

- 必须能恢复到前一 active wrapper tool snapshot，或明确 disable 当前 wrapper
- registry 回滚和 ledger 写入必须同事务或同一可补偿语义下完成

### 5. Promotion Plane

`P2-M1c` 的 promote 不是“skill 学得差不多就自动升级”，而是：

**先由 builder work memory 和 growth case 形成 promote candidate，再由 governance 明确判断是否值得升格。**

默认 promote 入口：

- active skill 在多个相似任务中稳定成功
- work memory 中出现清晰、重复的 typed I/O 模式
- 该能力已不再强依赖长段自然语言 delta
- failure modes 与 deny semantics 已可结构化表达

建议直接对齐当前 `PolicyRegistry` 中已有的 `skill_spec -> wrapper_tool` promotion schema：

- `usage_count >= 3`
- `success_rate >= 0.8`
- `unit_test_pass`
- `integration_smoke`
- `risk_gate = low`

但 `P2-M1c` 的执行口径要更明确：

- 不满足阈值：只记录 `promote_candidate`，不进入 apply
- 满足阈值但 target kind 未 onboard：只生成 proposal / recommendation
- 满足阈值且 `wrapper_tool` 已 onboard：允许 propose -> evaluate -> apply

这里的关键不是“尽快 promote”，而是证明：

- skill 真的是过渡层
- 能沉淀成更稳定的 capability 单元时，系统有一条受治理的路径可以走

### 6. Builder Runtime Boundary in P2-M1c

`P2-M1c` 仍然不能假装自己已经拥有完整 builder runtime。  
但它可以先固定 builder 的**最小可审计产物合同**：

- 任务 brief
- 中间决策
- TODO / blockers
- 代码或配置改动索引
- 测试与验证结果
- promote candidate

这层合同的消费者主要是：

- 人类审阅者
- growth case runner
- promote evaluator

而不是 procedure engine。  
换句话说，`P2-M1c` 先解决 builder 的“证据闭环”，把 “deterministic execution contract” 留到 `P2-M2`。

## Delivery Strategy

`P2-M1c` 复杂度：**高**。  
难点不在单个模块，而在三件事必须一起闭环：

- `bd / beads` 的 builder work memory 不能和 devcoord control-plane 重新混淆
- `wrapper_tool` 必须真正成为 onboarded object，而不是只新增几张表
- growth case 必须产出 before/after 证据，而不是再退回人工口述“这次好像学会了”

建议拆成 5 个顺序 work packages。依赖链需要显式写清：

- `WP-A` 先冻结边界、存储和 runtime entrypoint 语义
- `WP-B` 与 `WP-C` 都在 `WP-A` 之后执行，写集分离时可并行推进
- `WP-B` 先完成 `bd` feasibility spike，再进入 builder work memory substrate
- `WP-C` 完成 `wrapper_tool` onboarding
- `WP-D` 的 case 依赖分层：
  - `GC-1` 可在 `WP-B` 后独立验证
  - `GC-2` 的 core apply path 依赖 `WP-C`
  - `GC-2` 的完整闭环依赖 `WP-B + WP-C`
  - `GC-3` 只有在 `WP-A` 先冻结 import 协议时才进入 `P2-M1c`
- `WP-E` 只在前面至少完成 `GC-1 + GC-2` 后再进入 closeout

## Implementation Shape

### Work Package A: ADR + Vocabulary Freeze

先把 `P2-M1c` 的几个关键边界冻结：

- `bd / beads` 作为 builder work memory index 的语义
- `wrapper_tool` 的 V1 对象边界与 contract
- `GrowthCaseSpec / GrowthCaseRun` 的持久化策略
- `implementation_ref` 的 runtime entrypoint 语义
- `GC-3` 是否进入本 milestone 的 import 协议门槛

建议文件：

- `decisions/0055-builder-work-memory-via-bd-and-workspace-artifacts.md`（建议新增）
- `decisions/0056-wrapper-tool-onboarding-and-runtime-boundary.md`（建议新增）
- `design_docs/GLOSSARY.md`
- `src/growth/contracts.py`

产出：

- work memory 与 control-plane 语义分离
- 新建 `WRAPPER_TOOL_EVAL_CONTRACT_V1` 常量，并将 `_CONTRACTS` 的 runtime 引用从 skeleton 切到 `V1`
- `WRAPPER_TOOL_EVAL_CONTRACT_SKELETON` 可保留为历史/草案常量，但不再被 runtime 使用
- `GrowthCaseSpec = hardcoded catalog`，`GrowthCaseRun = workspace artifact`
- `implementation_ref = Python entrypoint string <module>:<factory>`
- `scope_claim`、`implementation_ref`、`deny_semantics` 等术语定型
- `bd` feasibility checklist 与 fallback 固化

验证：

- glossary / ADR / contract 字段一致
- `tests/growth/test_contracts.py` 覆盖新的 wrapper tool contract profile

### Work Package B: Builder Work Memory Substrate

建立 builder task 的 bead + artifact 双层结构。

建议文件：

- `src/builder/types.py`
- `src/builder/work_memory.py`
- `dev_docs/cases/` 或 `dev_docs/builder_runs/` 目录约定
- 视需要增加 `scripts/` 下的轻量 helper（例如 artifact bootstrap）

产出：

- `BuilderTaskRecord` 类型
- bead state / comment / artifact link 约定
- builder run artifact 模板
- growth case 与 bead 的双向索引
- `bd` feasibility spike 结果与 fallback 决策

验证：

- 新建 builder task 时能生成 bead 和 artifact
- progress snapshot / blocker / validation summary 能 append 到 bead comments 并回链 artifact
- 不触碰 `.devcoord/control.db` 语义
- 若 `bd` 能力不足，能退化到 `artifact-first + bead-pointer-only`

### Work Package C: Wrapper Tool Store + Adapter + Runtime Wiring

让 `wrapper_tool` 真正进入 runtime。

建议文件：

- `src/wrappers/types.py`
- `src/wrappers/store.py`
- `src/growth/adapters/wrapper_tool.py`
- `alembic/versions/xxxx_create_wrapper_tool_tables.py`
- 修改 `src/growth/policies.py`（`wrapper_tool` -> onboarded）
- 修改 `src/tools/registry.py`
- 修改 composition root / gateway wiring

产出：

- `wrapper_tools` current-state store
- `wrapper_tool_versions` governance ledger
- `WrapperToolGovernedObjectAdapter`
- Alembic migration：`wrapper_tools` + `wrapper_tool_versions`
- `ToolRegistry` 的 wrapper replace/remove 路径：
  - 支持 rollback / disable 时移除 active wrapper tool
  - 支持 supersede 时替换同名 wrapper tool
  - 相关 mode override 必须与 deregistration 一起清理，避免 registry 漂移
- `src/growth/policies.py` 中 `wrapper_tool.notes` 与 `procedure_spec.notes` 同步更新，避免 milestone 口径残留歧义
- `wrapper_tool` V1 eval

验证：

- `GrowthGovernanceEngine` 对 `wrapper_tool` 的 propose -> evaluate -> apply -> rollback 闭环
- registry 中 active wrapper tool 可见且可调用
- apply / rollback 与 registry 写入不漂移
- `tests/growth/` 与 `tests/tools/` 回归不退化

### Work Package D: Growth Case Catalog + Runner

把 curated growth cases 写成固定入口，而不是文档口述。

建议文件：

- `src/growth/cases.py`
- `src/growth/case_runner.py`
- `dev_docs/cases/p2-m1c_growth-cases.md`
- `tests/integration/test_growth_cases_e2e.py`

产出：

- `GrowthCaseSpec` catalog
- `GrowthCaseRun` artifact record
- `GC-1`、`GC-2`、`GC-3` 至少两条可运行 case
- case runner 与 bead / artifact / proposal handle 的串接

验证：

- case run 能生成 bead + artifact + proposal refs
- case 失败时能产出 `candidate_only` / `veto` / `rollback` 结论
- case 成功时能产出 apply 后 replay 证据
- `GC-2` 的 core apply path 只有在 `wrapper_tool` 已 onboard 且已接入 `ToolRegistry` 后才进入 apply 测试
- `GC-2` 的完整闭环只有在 `WP-B + WP-C` 都完成后才计入 acceptance
- `GC-3` 若 import 协议未冻结，则不纳入本轮 acceptance

### Work Package E: Acceptance Closeout

用 roadmap 用例 A~F 对 `P2-M1c` 做最终闭环验证。

建议文件：

- `dev_docs/reviews/phase2/p2-m1c_*.md`
- `dev_docs/logs/phase2/p2-m1c_*/`
- 相关测试文件：
  - `tests/builder/`
  - `tests/wrappers/`
  - `tests/growth/`
  - `tests/integration/`

产出：

- 至少一条 skill reuse case
- 至少一条 skill -> wrapper_tool promote case
- 至少一条失败 rollback / veto case
- 完整 evidence packet

验证：

- `just lint`
- `just test`
- 必要的 targeted integration / smoke
- `bd` 中 builder task / case issue 有完整 work memory 轨迹

## Boundaries

### In

- `bd / beads` builder work memory 语义与 artifact index
- `BuilderTaskRecord` 与 builder run artifact 模板
- `wrapper_tool` onboarding
- `WrapperToolGovernedObjectAdapter`
- `wrapper_tool` current-state store + governance ledger
- `GrowthCaseSpec` / `GrowthCaseRun`
- curated growth case catalog
- `skill_spec -> wrapper_tool` promote 闭环
- before/after 或 first-run/replay 证据
- rollback / veto / candidate-only 结论路径

### Out

- `procedure_spec` onboarding
- `Procedure Runtime`
- `memory_application_spec`
- raw code patch governance object 化
- 自动 promote / 自动 apply / 自动 disable
- 开放式自治搜索或无限 self-improvement loop
- 外部写动作默认放开
- 重新把 `beads` 用作 devcoord control-plane
- 通用 workflow DSL
- 多 agent runtime handoff / steering / resume

## Risks

1. **Beads overload**：若把过多正文直接写进 issue metadata/comment，builder work memory 会迅速退化为难以维护的长贴
2. **Boundary blur**：若 wrapper tool 支持太多 branching / state，会立刻和 `procedure_spec` 混边界
3. **Weak evidence**：若 growth case 只证明“这次做成了”，没有 before/after 或 replay，对 promote 不具说服力
4. **Judge coupling**：若 proposal 同时改 implementation 和 eval harness，会破坏 ADR 0054 的 immutable contract 前提
5. **Registry drift**：若 wrapper tool store 和 runtime registry 不是同一 apply/rollback 语义，容易出现 active record 与实际可调用工具不一致
6. **Issue noise**：若每个小动作都开 bead，会把 work memory 和 backlog 再次混成一团
7. **Premature generalization**：若为了 future-proof 过早引入 generic wrapper DSL，会把 `P2-M1c` 复杂度抬到 `P2-M2` 水平

## Mitigations

1. **双层表达**：bead 只存索引、摘要和状态；详细内容进 workspace artifact
2. **Wrapper hard boundary**：V1 wrapper tool 明确限制为 single-turn typed capability；超出直接转 `procedure_spec` backlog
3. **Curated cases only**：先跑固定 2~3 条 case，避免开放式范围膨胀
4. **Contract pinning**：所有 case / eval 都要带 `contract_id` + `contract_version`
5. **Atomic apply/rollback**：wrapper tool materialization 与 registry registration 必须共成败
6. **Issue hygiene**：builder work memory 以“每个真实 task / case 一个 bead”为原则，小步骤走 comments，不额外开 issue
7. **Promotion thresholds stay conservative**：不满足 evidence / tests / risk gate，只记 candidate，不 apply

## Acceptance

- `A1 (G1/G8).` 至少一条 growth case 能回答“改了什么、为什么改、怎么验证、如何回滚”，且证据可从 bead 跳转到 artifact / proposal / test summary
- `A2 (G2).` 至少一条较长 builder task 在 `bd / beads` 中留下 `brief + decisions + blockers + validation + artifact refs + promote candidate` 的 work memory；若 `bd` 能力不足，则至少达到 `artifact-first + bead-pointer-only` 的 fallback 形态
- `A3 (G3).` `wrapper_tool` 在 `PolicyRegistry` 中由 reserved 变为 onboarded；`procedure_spec` 继续 reserved，并明确推迟到 `P2-M2`
- `A4 (G4).` 至少一类能力能从 active skill promote 成 active `wrapper_tool`，并注册到 runtime `ToolRegistry`
- `A5 (G5).` 至少一条 skill 在 promote 前先被第二次相似任务成功复用，而不是跳过 reuse 直接升格
- `A6 (G6).` 至少一条 curated growth case 完成 `propose -> evaluate -> apply`
- `A7 (G7).` 至少一条失败 case 完成 `veto` 或 `rollback`，系统恢复到上一个稳定 capability 状态
- `A8 (G1/G6/G7).` `GC-1 + GC-2` 均有 replay 级证据；`GC-3` 只有在 import 协议已冻结时才计入本 milestone
- `A9.` `just lint` clean、`just test` 全量 green；相关 integration / smoke 通过

## Resolved Draft Positions

1. `P2-M1c` 先 onboarding `wrapper_tool`，不抢跑 `procedure_spec`
   - 原因：这正好补上 `skill -> stable capability unit` 的 promote 闭环，又不会越界到 `P2-M2`
2. `bd / beads` 只做 builder work memory index，不做 control-plane
   - 原因：ADR 0050 已经把 control-plane 从 beads 彻底切走
3. growth case 必须是 curated catalog，不接受开放式“随便找个任务试试看”
   - 原因：没有固定 case，就没有 pinned contract、before/after evidence 和可重复验收
4. `wrapper_tool` V1 应优先是 code-backed / registry-backed capability，而不是 generic workflow DSL
   - 原因：generic DSL 会把 `P2-M1c` 过早推成 `P2-M2`
5. promote 的最小单位不是“代码 patch 成功”，而是“受治理的 wrapper tool proposal 成功”
   - 原因：raw diff 仍只是 evidence，不是 growth object
