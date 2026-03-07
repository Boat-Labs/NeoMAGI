# P2-M1a 实施计划：Growth Governance Kernel

- Date: 2026-03-06
- Status: approved
- Scope: `P2-M1a` only; establish the minimal governance kernel for explicit growth
- Basis:
  - [`design_docs/phase2/p2_m1_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m1_architecture.md)
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/skill_objects_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/skill_objects_runtime.md)
  - [`decisions/0027-partner-agent-self-evolution-guardrails.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0027-partner-agent-self-evolution-guardrails.md)
  - [`decisions/0036-evolution-consistency-db-as-ssot-and-soulmd-as-projection.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0036-evolution-consistency-db-as-ssot-and-soulmd-as-projection.md)
  - [`decisions/0048-skill-objects-as-runtime-experience-layer.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0048-skill-objects-as-runtime-experience-layer.md)
  - [`decisions/0049-growth-governance-kernel-adapter-first.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0049-growth-governance-kernel-adapter-first.md)

## Context

本计划对应 [`design_docs/phase2/p2_m1_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m1_architecture.md) §3 建议拆分中的 `P2-M1a` 子阶段。

Phase 2 已经明确把“显式成长”放到第一优先级，但当前代码里的治理能力仍然主要集中在 `SOUL.md`：

- 现有 [`EvolutionEngine`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py) 已实现 `propose -> evaluate -> apply -> rollback`
- 该生命周期只覆盖 `SOUL.md`
- `evaluate()` 当前只把 `eval_result` 写回 proposal payload，status 仍停留在 `proposed`
- `PromptBuilder` 的 skills 仍是 placeholder，[`PromptBuilder._layer_skills()`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/prompt_builder.py#L123) 还没有正式 runtime 对象可接
- tool registry 已有稳定底座，但还没有“学习经验如何升格为稳定能力单元”的统一治理路径

因此 `P2-M1a` 的任务不是直接做完整自我进化系统，而是先把“什么允许成长、如何受治理、如何禁止绕过治理”固定下来，作为 `P2-M1b` 和 `P2-M1c` 的前置内核。

## Core Decision

`P2-M1a` 采用一个**adapter-first 的最小通用治理内核**，而不是一次性重写所有能力对象：

1. 新增 `src/growth/` 作为治理编排层，只负责治理类型、政策、engine 与 adapter registry；不接管对象本体存储。
2. 保留现有 `SOUL.md` 生命周期与可观察行为：
   - status 继续使用 `proposed | active | superseded | rolled_back | vetoed`
   - eval 结果继续记录在 `proposed` 状态的 payload 中
   - `P2-M1a` 不引入 `evaluated` 或 `rejected` 新状态
3. `P2-M1a` 只正式接入一个对象：
   - `soul` = onboarded
   - 其余 kind 只做注册与 reserved 标记，不提供运行时能力
4. `SoulGovernedObjectAdapter` 采用 thin wrapper 策略包装现有 [`EvolutionEngine`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py)，不替换、不重写其公开 API。
5. `PromptBuilder`、`skill object runtime`、builder runtime 和 growth cases 明确推迟到 `P2-M1b`/`P2-M1c`。

这意味着 `P2-M1a` 先解决**治理语义统一**，而不是立刻解决**所有成长对象的执行落地**。

## Goals

- 固定 NeoMAGI Phase 2 的显式成长对象清单与最小生命周期语义。
- 将当前 `SOUL.md` 专用演化路径上移成通用 governance kernel 的第一个已接入对象。
- 固定跨对象的 `propose / evaluate / apply / rollback / audit` 契约。
- 提供 machine-readable 的 kind policy registry，并为后续 promote / demote policy 预留结构化 schema。
- 提供明确的“未接入对象”错误语义，避免 roadmap 先写了但 runtime 里默默漂移。

## Non-Goals

- 不在 `P2-M1a` 内实现 `skill object` runtime 检索、投影与学习。
- 不在 `P2-M1a` 内做 builder 产品化。
- 不在 `P2-M1a` 内扩展 `beads` work memory。
- 不在 `P2-M1a` 内跑 Reddit / Actionbook 等 growth cases。
- 不在 `P2-M1a` 内集成 `PromptBuilder` 与 governance kernel。
- 不引入 `evaluated` / `rejected` 新生命周期状态。
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
- `GrowthOnboardingState`
  - `onboarded`
  - `reserved`
- `GrowthLifecycleStatus`
  - `proposed`
  - `active`
  - `superseded`
  - `rolled_back`
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
- `GrowthKindPolicy`
  - `kind`
  - `onboarding_state`
  - `requires_explicit_approval`
  - `adapter_name`
  - `notes`
- `PromotionPolicy`
  - `from_kind`
  - `to_kind`
  - `required_evidence`
  - `required_tests`
  - `risk_gate`

关键语义：

- `GrowthLifecycleStatus` 在 `P2-M1a` 中刻意与现有 `SOUL.md` 状态机对齐，不额外扩张。
- evaluation outcome 在 `P2-M1a` 中仍然是 proposal payload 语义，不是独立 lifecycle status。
- `memory_application_spec` 在 `P2-M1a` 中仅作为 tentative reserved kind：
  - 指在稳定 memory kernel 之上运行的声明式 memory application 规格
  - 正式对象定义推迟到 `P2-M3`

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
- `src/growth/` 只管治理编排，不管对象存储；对象存储仍留在 `src/memory/` 或未来对象自己的领域模块
- `P2-M1a` 采用最简 adapter 注册方式：
  - composition root 显式构造 `GrowthGovernanceEngine(adapters=[...])`
  - 当前只显式注入 `SoulGovernedObjectAdapter`
  - 不引入 config-driven registry、service locator 或自动发现机制

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
  - thin wrapper 包装现有 [`EvolutionEngine`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py)
  - 继续使用 `soul_versions` 作为 SSOT
  - 不迁移旧数据
  - 不改变 `EvolutionEngine` 公开 API 和当前可观察行为

其余对象在 `P2-M1a` 中只需要：

- 完成 kind 注册
- 完成 reserved policy 注册
- 明确标注“not onboarded”

### 4. Policy Layer

单独维护 machine-readable 政策，而不是散在 prompt 或自然语言里：

- 允许成长的对象白名单
- 每类对象是否已 onboarded
- 哪些对象当前只保留 reserved registration
- 高风险对象是否需要额外 approval
- future promote / demote policy 的 schema 结构

关系说明：

- `GrowthKindPolicy` 是 per-kind 元数据载体：
  - 负责表达某个 kind 是否 `onboarded` / `reserved`
  - 是否需要额外 approval
  - 当前应绑定哪个 adapter
- `PromotionPolicy` 是跨 kind 的规则载体：
  - 负责表达从一个 kind 升格到另一个 kind 时，需要哪些 evidence / tests / risk gate

`P2-M1a` 中真正会被 runtime 消费的 policy 只有：

- kind 白名单
- onboarding state
- unsupported / reserved 错误语义
- `soul` 的治理路径元数据

`skill_spec -> wrapper_tool`、`wrapper_tool -> atomic tool` 等跨 kind promote policy 在 `P2-M1a` 中只保留 schema/registry 形状，不要求端到端执行。

### 5. Audit Boundary

所有治理动作都应能映射到统一审计语义：

- `object_kind`
- `object_id`
- `proposal_id` 或 `version`
- `actor`
- `intent`
- `evidence_refs`
- `eval_summary`
- `applied_at` / `rolled_back_at`

`P2-M1a` 不要求统一审计后端，但要求统一审计字段语义。对现有 `soul_versions` 的最小映射如下：

| Unified audit semantic | `soul_versions` mapping in `P2-M1a` | Note |
| --- | --- | --- |
| `object_kind` | adapter constant = `soul` | adapter 补齐 |
| `object_id` | adapter constant = `SOUL.md` | adapter 补齐 |
| `proposal_id` / `version` | `version` | 直接可用 |
| `actor` | `created_by` | 直接可用 |
| `intent` | `proposal.intent` | JSON payload 映射 |
| `evidence_refs` | `proposal.evidence_refs` | JSON payload 映射 |
| `eval_summary` | `eval_result.summary` | JSON payload 映射 |
| `applied_at` / `rolled_back_at` | adapter-derived / nullable | 现表无原生列，`P2-M1a` 不为此做 schema 迁移 |

## Delivery Strategy

`P2-M1a` 本身复杂度仍然是**中高**。  
主要难点不在代码量，而在于：

- 既要抽出通用治理语义，又不能暗中改掉现有 `SOUL.md` 状态机
- 既要让 `SOUL.md` 接入新内核，又不能破坏已有补偿与回滚语义
- 既要为 `skill object` 等 future object 预留位置，又不能提前把 `P2-M1b` 内容混进来

因此不建议把 `P2-M1a` 做成一个大提交，建议至少拆成 4 个窄切片：

1. ADR, Domain Types, and Policy Registry
2. Governance Engine and Adapter Contract
3. Soul Adapter Migration
4. Tests and Guardrails

每个切片都应独立可审阅，不依赖“大量未落地 future object 实现”。

## Implementation Shape

### Work Package A: ADR, Domain Types, and Policy Registry

新增最小治理领域模型与政策表。

建议文件：

- `decisions/0049-growth-governance-kernel-adapter-first.md`
- `src/growth/types.py`
- `src/growth/policies.py`

产出：

- 枚举、dataclass、错误码
- 与现有 `SOUL.md` 状态机对齐的 lifecycle 语义
- onboarded / reserved object kind 清单
- reserved promotion policy schema

### Work Package B: Governance Engine and Adapter Contract

新增通用 engine 与 adapter protocol。

建议文件：

- `src/growth/engine.py`
- `src/growth/adapters/base.py`

产出：

- engine 编排流程
- fail-closed unsupported error
- adapter registry
- `src/growth/` 职责边界固定为治理编排层，不承接对象存储

### Work Package C: Soul Adapter Migration

把当前 `EvolutionEngine` 接到新内核上，但尽量不破坏已有行为。

建议文件：

- `src/growth/adapters/soul.py`
- 视需要最小调整 [`src/memory/evolution.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py)

关键策略：

- thin wrapper only
- 复用 `soul_versions`
- 不做 DB 大迁移
- 不改变 `EvolutionEngine` 公开 API
- 对外暴露统一 governance API

### Work Package D: Tests and Guardrails

建议测试覆盖：

- `tests/growth/test_policies.py`
- `tests/growth/test_engine.py`
- `tests/growth/test_soul_adapter.py`

必须保持 green 的现有回归测试：

- `tests/test_evolution.py`
- `tests/integration/test_evolution_e2e.py`
- `tests/test_soul_tools.py`

至少覆盖：

- 已接入对象可完成 `propose -> evaluate -> apply -> rollback`
- 未接入对象明确拒绝
- reserved promotion policy 可反序列化 / 校验
- 旧 `SOUL.md` 路径行为不回归

## Boundaries

### In

- Growth object kind 白名单
- 与现有 `SOUL.md` 对齐的通用生命周期语义
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
- `PromptBuilder` 与 governance kernel 集成
- 统一多对象存储重构
- 自动进化调度器
- 跨 kind promote 的端到端执行

## Risks

- 过度抽象：为了 future-proof 设计出一个过重的 generic framework，反而拖慢 `P2-M1b`
- 假通用：如果只包一层 `SOUL.md` 壳，后续对象仍然接不进来
- 隐性状态机漂移：若在 `P2-M1a` 里偷偷把 `evaluate()` 变成独立 lifecycle status，会制造不必要回归
- 兼容性回归：若 `SOUL.md` 现有 apply / rollback 路径被抽坏，会破坏已存在能力

## Mitigations

- 先抽“语义统一 + adapter contract”，不抽“统一大后端”
- `SOUL.md` 继续沿用现有 SSOT 和补偿语义
- `evaluated` / `rejected` 明确不在 `P2-M1a` 范围
- 所有未接入对象显式 fail-closed
- 只有当第二类对象真实接入时，才决定是否需要新的持久化统一层

## Acceptance

- 系统能列出允许成长的对象类型，以及哪些已接入、哪些仅预留。
- `SOUL.md` 通过新的 governance kernel 仍可完成 `propose -> evaluate -> apply -> rollback`，且 observable semantics 不变。
- governance kernel 对未接入对象给出明确拒绝，而不是静默降级到 prompt 漂移。
- reserved promotion policy 至少以 machine-readable schema 存在，并通过 schema 级校验；`P2-M1a` 不要求跨 kind promote 的端到端行为测试（见下文 Resolved Positions: promotion / demotion policy projection）。
- 现有 `SOUL.md` 回归测试不退化。

## Resolved Positions

- `evaluated_passed` vs `evaluated_failed`
  - `P2-M1a` 保留现有做法：eval 结果写入 payload，不引入新 lifecycle status。
- future object SSOT
  - `P2-M1a` 明确允许 adapter 自带存储；是否需要统一新表，推迟到第二类对象真正接入时再决策。
- promotion / demotion policy projection
  - `P2-M1a` 只要求纯代码声明与 schema 级校验，workspace projection 推迟到有运行时消费者时再决定。

## Approved Execution Focus

本计划批准后，执行范围应保持收敛：

- 以 `SOUL.md` 为唯一正式接入对象
- 以 `growth kernel + policy registry + adapter contract` 为主线
- 不抢跑 `skill runtime`、builder、work memory 和 growth cases
