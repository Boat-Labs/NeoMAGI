# P2-M1a 实施计划草案：Growth Governance Kernel

- Date: 2026-03-06
- Status: draft
- Scope: `P2-M1a` only; establish the minimal governance kernel for explicit growth
- Basis:
  - [`design_docs/phase2/p2_m1_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m1_architecture.md)
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/skill_objects_runtime_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/skill_objects_runtime_draft.md)
  - [`decisions/0027-partner-agent-self-evolution-guardrails.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0027-partner-agent-self-evolution-guardrails.md)
  - [`decisions/0036-evolution-consistency-db-as-ssot-and-soulmd-as-projection.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0036-evolution-consistency-db-as-ssot-and-soulmd-as-projection.md)
  - [`decisions/0048-skill-objects-as-runtime-experience-layer.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0048-skill-objects-as-runtime-experience-layer.md)

## Context

Phase 2 已经明确把“显式成长”放到第一优先级，但当前代码里的治理能力仍然主要集中在 `SOUL.md`：

- 现有 [`EvolutionEngine`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py) 已实现 `propose -> evaluate -> apply -> rollback`
- 该生命周期只覆盖 `SOUL.md`
- `PromptBuilder` 的 skills 仍是 placeholder，[`PromptBuilder._layer_skills()`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/prompt_builder.py#L110) 还没有正式 runtime 对象可接
- tool registry 已有稳定底座，但还没有“学习经验如何升格为稳定能力单元”的统一治理路径

因此 `P2-M1a` 的任务不是直接做完整自我进化系统，而是先把“什么允许成长、如何受治理、如何禁止绕过治理”固定下来，作为 `P2-M1b` 和 `P2-M1c` 的前置内核。

## Core Decision

`P2-M1a` 采用一个**最小通用治理内核**，而不是一次性重写所有能力对象：

1. 保留现有 `SOUL.md` 治理实现作为第一类已接入对象
2. 抽出跨对象统一的生命周期语义、状态机、政策与审计字段
3. 为 `skill object`、wrapper tool、procedure spec、memory application spec 预留注册位，但不在 `P2-M1a` 内实现完整运行时
4. 明确任何未来成长对象都必须经过统一治理入口，禁止“直接 prompt 漂移”或“builder 直接写长期能力面”

这意味着 `P2-M1a` 要先解决**治理语义统一**，而不是立刻解决**所有成长对象的执行落地**。

## Goals

- 固定 NeoMAGI Phase 2 的显式成长对象清单与生命周期语义。
- 将当前 `SOUL.md` 专用演化路径上移成通用 governance kernel 的第一个已接入对象。
- 固定跨对象的 `propose / eval / apply / rollback / audit` 契约。
- 固定 `skill -> wrapper tool -> atomic tool` 的 promote / demote 政策表。
- 提供明确的“未接入对象”错误语义，避免 roadmap 先写了但 runtime 里默默漂移。

## Non-Goals

- 不在 `P2-M1a` 内实现 `skill object` runtime 检索、投影与学习。
- 不在 `P2-M1a` 内做 builder 产品化。
- 不在 `P2-M1a` 内扩展 `beads` work memory。
- 不在 `P2-M1a` 内跑 Reddit / Actionbook 等 growth cases。
- 不做“统一大表重构”，不为了通用性立即迁移现有 `soul_versions` 数据。
- 不做自动 promote / 自动 patch / 自动 self-modification。

## Proposed Architecture

### 1. Governance Vocabulary Layer

新增一组最小领域对象，专门表达“受治理成长”：

- `GrowthObjectKind`
  - `soul`
  - `skill_spec`
  - `wrapper_tool`
  - `procedure_spec`
  - `memory_application_spec`
- `GrowthLifecycleStatus`
  - `proposed`
  - `evaluated`
  - `active`
  - `superseded`
  - `rolled_back`
  - `rejected`
  - `vetoed`
- `GrowthProposal`
  - `object_kind`
  - `object_id`
  - `intent`
  - `risk_notes`
  - `diff_summary`
  - `evidence_refs`
  - `proposed_by`
- `GrowthEvalResult`
  - `passed`
  - `checks`
  - `summary`
- `PromotionPolicy`
  - `from_kind`
  - `to_kind`
  - `required_evidence`
  - `required_tests`
  - `risk_gate`

这层只提供统一语义，不承担对象本体存储。

### 2. Governance Engine Layer

引入一个轻量 orchestration service，例如：

- `GrowthGovernanceEngine`
  - `propose()`
  - `evaluate()`
  - `apply()`
  - `rollback()`
  - `get_active()`
  - `list_supported_kinds()`

关键约束：

- engine 只负责编排生命周期，不直接知道各对象如何存储
- 各对象通过 adapter 接入
- 对未接入对象必须 fail-closed，返回明确 `UNSUPPORTED_GROWTH_OBJECT`

### 3. Adapter Layer

定义统一 adapter contract，例如：

- `GovernedObjectAdapter`
  - `kind`
  - `propose()`
  - `evaluate()`
  - `apply()`
  - `rollback()`
  - `get_active()`

`P2-M1a` 只强制实现一个正式 adapter：

- `SoulGovernedObjectAdapter`
  - 包装现有 [`EvolutionEngine`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py)
  - 继续使用 `soul_versions` 作为 SSOT
  - 不迁移旧数据

其余对象在 `P2-M1a` 中只需要：

- 完成 kind 注册
- 完成政策注册
- 明确标注“not onboarded”

### 4. Policy Layer

单独维护 machine-readable 政策，而不是散在 prompt 或自然语言里：

- 允许成长的对象白名单
- 每类对象是否已 onboarded
- promote / demote 规则
- 最低证据门槛
- 最低评测门槛
- 高风险对象是否需要额外 approval

最小 promote 口径建议：

- `skill_spec -> wrapper_tool`
  - 需要重复成功证据
  - 需要 typed input/output 草案
  - 需要最小回归测试
- `wrapper_tool -> atomic tool`
  - 需要跨场景复用证据
  - 需要清晰边界与风险评估
  - 需要正式 registry 接入与测试

### 5. Audit Boundary

所有治理动作至少要带：

- `object_kind`
- `object_id`
- `proposal_id` 或 `version`
- `actor`
- `intent`
- `evidence_refs`
- `eval_summary`
- `applied_at` / `rolled_back_at`

`P2-M1a` 不要求统一审计后端，但要求统一审计字段语义。

## Delivery Strategy

`P2-M1a` 本身复杂度仍然是**中高**。  
主要难点不在代码量，而在于：

- 既要抽出通用治理语义，又不能过早重构所有对象存储
- 既要让 `SOUL.md` 接入新内核，又不能破坏已有补偿与回滚语义
- 既要为 `skill object` 等 future object 预留位置，又不能提前把 `P2-M1b` 内容混进来

因此不建议把 `P2-M1a` 做成一个大提交，建议至少拆成 3 个窄切片：

1. 领域类型与政策表
2. engine / adapter contract
3. `SOUL.md` 接入 + 测试回归

每个切片都应独立可审阅，不依赖“大量未落地 future object 实现”。

## Implementation Shape

### Work Package A: Domain Types and Policy Registry

新增最小治理领域模型与政策表。

建议文件：

- `src/growth/types.py`
- `src/growth/policies.py`

产出：

- 枚举、dataclass、错误码
- onboarded / reserved object kind 清单
- promote / demote 政策表

### Work Package B: Governance Engine and Adapter Contract

新增通用 engine 与 adapter protocol。

建议文件：

- `src/growth/engine.py`
- `src/growth/adapters/base.py`

产出：

- engine 编排流程
- fail-closed unsupported error
- adapter registry

### Work Package C: Soul Adapter Migration

把当前 `EvolutionEngine` 接到新内核上，但尽量不破坏已有行为。

建议文件：

- `src/growth/adapters/soul.py`
- 视需要最小调整 [`src/memory/evolution.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py)

关键策略：

- 复用 `soul_versions`
- 不做 DB 大迁移
- 对外暴露统一 governance API

### Work Package D: Tests and Guardrails

建议测试覆盖：

- `tests/growth/test_policies.py`
- `tests/growth/test_engine.py`
- `tests/growth/test_soul_adapter.py`

至少覆盖：

- 已接入对象可完成 `propose -> evaluate -> apply -> rollback`
- 未接入对象明确拒绝
- policy 不允许的 promote 直接拒绝
- 旧 `SOUL.md` 路径行为不回归

## Boundaries

### In

- Growth object kind 白名单
- 通用生命周期语义
- governance engine
- adapter contract
- `SOUL.md` 接入新治理内核
- policy registry
- 基础测试

### Out

- `skill object` runtime 本体
- builder runtime
- `beads` work memory
- Actionbook / Reddit growth case
- 统一多对象存储重构
- 自动进化调度器

## Risks

- 过度抽象：为了 future-proof 设计出一个过重的 generic framework，反而拖慢 `P2-M1b`
- 假通用：如果只包一层 `SOUL.md` 壳，后续对象仍然接不进来
- 存储过早统一：如果在本阶段就强行把所有对象迁到同一表，风险和迁移噪音都过高
- 兼容性回归：若 `SOUL.md` 现有 apply / rollback 路径被抽坏，会破坏已存在能力

## Mitigations

- 先抽“语义统一 + adapter contract”，不抽“统一大后端”
- `SOUL.md` 继续沿用现有 SSOT 和补偿语义
- 所有未接入对象显式 fail-closed
- 只有当第二类对象真实接入时，才决定是否需要新的持久化统一层

## Acceptance

- 系统能列出允许成长的对象类型，以及哪些已接入、哪些仅预留。
- `SOUL.md` 通过新的 governance kernel 仍可完成 `propose -> evaluate -> apply -> rollback`。
- governance kernel 对未接入对象给出明确拒绝，而不是静默降级到 prompt 漂移。
- promote / demote 规则至少以 machine-readable 政策表存在，而不是只写在设计文档里。
- 现有 `SOUL.md` 回归测试不退化。

## Proposed Execution Order

1. 定义治理领域对象与错误语义
2. 落政策注册表
3. 落 engine 与 adapter protocol
4. 接入 `SoulGovernedObjectAdapter`
5. 补测试与回归验证

## Open Questions

- `GrowthLifecycleStatus` 是否需要区分 `evaluated_passed` 与 `evaluated_failed`，还是只把 eval 结果放在 payload 中
- future object 的 persisted SSOT 是否应统一到新表，还是允许 adapter 自带存储
- promote / demote policy 最终应纯代码声明，还是允许 workspace policy projection

## Output of This Draft

如果这版方向确认，下一步应产出正式 plan，并进入一个窄范围实现：

- 以 `SOUL.md` 为唯一正式接入对象
- 以 `growth kernel + policy registry + adapter contract` 为主线
- 不抢跑 `skill runtime`、builder、work memory 和 growth cases
