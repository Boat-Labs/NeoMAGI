# P2-M1 实施计划：Growth Eval Contract 与对象边界

- Date: 2026-03-15
- Status: approved
- Scope: `P2-M1` only; define immutable growth eval contracts and object boundaries for explicit growth, without building a full autonomous code-search system
- Basis:
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/phase2/p2_m1_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m1_architecture.md)
  - [`design_docs/skill_objects_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/skill_objects_runtime.md)
  - [`design_docs/system_prompt.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/system_prompt.md)
  - [`decisions/0048-skill-objects-as-runtime-experience-layer.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0048-skill-objects-as-runtime-experience-layer.md)
  - [`decisions/0049-growth-governance-kernel-adapter-first.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0049-growth-governance-kernel-adapter-first.md)
  - [`decisions/0054-growth-eval-contracts-immutable-and-object-scoped.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0054-growth-eval-contracts-immutable-and-object-scoped.md)
  - [`dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14_draft.md)
  - Discussion input: [`tmp/autoresearch_claude_report.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/tmp/autoresearch_claude_report.md)

## Context

`P2-M1a` 已完成 growth governance kernel，已经固定了：

- growth object kinds
- `propose -> evaluate -> apply -> rollback` 统一治理入口
- fail-closed 语义：reserved kind 不得偷偷进入运行时

但当前仓库仍缺一层更高阶的设计收敛：

- `evaluate()` 的统一接口已经存在，但 eval contract 仍主要停留在“每个 adapter 自己决定做什么”
- `SOUL` 已有 deterministic eval，但这还不是 `P2-M1` 的跨对象增长评测口径
- `P2-M1b` draft 已提出 `skill_spec` onboarding，但其 V1 eval 目前只写到 `schema 校验 + preconditions 检查`

在 milestone 时序上，这份计划是 `P2-M1b` 实施前的前置设计收敛：先冻结 eval contract 语义，再把最小结论回填到 `P2-M1b`，其余 growth case 约束延续到 `P2-M1c`。

现在又有一层新的上位约束已经固定：ADR 0054 已接受 `GrowthEvalContract` 的核心语义，包括：

- `GrowthEvalContract` 是一等治理对象
- contract 必须 `object-scoped`、`versioned`、`immutable`
- proposal 不能与 judge / harness 一起修改
- `raw code patch` 不是 `P2-M1` 的一等 growth object
- canonical eval 结构固定为四层：`Boundary gates / Effect evidence / Scope claim / Efficiency metrics`

Karpathy `autoresearch` 的启示不是单个指标 `val_bpb` 本身，而是：

- 可变搜索面与不可变评测契约分离
- 被优化对象不能同时修改自己的 judge / harness
- keep / revert 规则必须先于持续改进 loop 固定

NeoMAGI 不适合照搬“单文件 + 单标量指标”模式，因为它面对的是多种成长对象、运行时副作用边界与代码实现面。  
因此更合理的目标是：

**为每类 growth object 定义 versioned、immutable、object-specific 的 eval contract。**

## Core Decision

本计划只做设计收敛，不做完整自治代码搜索系统。

它在时序上服务于 `P2-M1b`，并为 `P2-M1c` 提供统一评测口径；不是与 `P2-M1b` 平行竞争的另一条实现线。

本轮要先固定三件事：

1. 什么是一等 growth object，什么不是
2. 每种 object 的可变面是什么
3. 每种 object 的 immutable eval contract 至少应包含哪些部分

本轮明确不做：

- 不做类似 `autoresearch` 的 repo-wide 无限实验循环
- 不把“任意代码 patch”直接提升为新的 growth object kind
- 不在 `P2-M1` 内引入通用 reward model 或统一单分数函数
- 不为 `P2-M1b` 引入重型 benchmark harness 或分布式实验基础设施

## Proposed Model

### 1. `GrowthEvalContract` 作为设计对象

对每个已 onboard 或准备 onboard 的 growth object kind，定义一个独立的 `GrowthEvalContract`。  
它不是被 agent 自由修改的 proposal payload，而是治理层拥有的约束对象。

V1 建议最小字段：

```python
class GrowthEvalContract:
    contract_id: str
    object_kind: GrowthObjectKind
    version: int
    mutable_surface: tuple[str, ...]
    immutable_harness: tuple[str, ...]
    required_checks: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    pass_rule_kind: str  # "all_required" | "hard_pass_and_threshold"
    pass_rule_params: tuple[str, ...]
    veto_conditions: tuple[str, ...]
    rollback_preconditions: tuple[str, ...]
    budget_limits: tuple[str, ...]
```

语义约束：

- `mutable_surface`
  - 被提案允许改变的对象面或实现面
- `immutable_harness`
  - 本次评测引用的脚本、case corpus、judge 规则、golden cases、deny-list
- `required_checks`
  - 必过的 deterministic / scenario / regression / safety checks
- `required_artifacts`
  - 提案必须附带的 diff summary、evidence refs、测试摘要、回滚目标
- `pass_rule_kind`
  - V1 只允许有限枚举，例如 `all_required` 或 `hard_pass_and_threshold`
- `pass_rule_params`
  - pass rule 的参数，例如目标 check group 与阈值
- `veto_conditions`
  - 哪些失败一票否决
- `rollback_preconditions`
  - rollback 可执行的前提，例如存在可恢复的前一版本、apply 产物可逆、回滚目标已知
- `budget_limits`
  - 成本、时延、上下文预算、风险级别等硬约束

### 1a. `GrowthEvalContract` 到 `GrowthEvalResult` 的语义桥接

`GrowthEvalContract` 负责定义“该检查什么”；现有 `adapter.evaluate()` 继续负责“实际执行检查并产出结果”。

V1 约定：

- `adapter.evaluate()` 在运行前先 pin `contract_id` 与 `contract_version`
- adapter 按 `required_checks` 和 `immutable_harness` 执行对应检查
- `GrowthEvalResult.checks` 中的每个 check 都应能回溯到 contract 中的 check 名称或 check group
- `GrowthEvalResult.passed` 由 `pass_rule_kind`、`pass_rule_params` 与 `veto_conditions` 共同判定
- `GrowthEvalResult.summary` 至少应带上 `contract_id`、`contract_version` 与主要 veto / rollback 结论

换句话说：

- contract 定义评测契约
- adapter 执行契约
- `GrowthEvalResult` 是一次 pinned contract run 的结果投影

### 2. Four-Layer Contract Structure

按 ADR 0054，`P2-M1` 的 eval contract 不把所有维度混成单一 pass/fail。  
V1 先拆成 4 层：

- `Boundary gates`
  - 可回滚
  - 接口清晰
  - 依赖显式
  - 不引入隐藏耦合
- `Effect evidence`
  - 固定 before/after cases
  - 必要时做轻量 ablation
- `Scope claim`
  - `local` / `reusable` / `promotable`
  - claim 越强，证据要求越高
- `Efficiency metrics`
  - `tokens_per_success`
  - `latency_per_success`
  - `cost_per_success`

约束：

- `Boundary gates` 是 hard gate
- `Effect evidence` 是最小有效性证明
- `Scope claim` 决定额外证据门槛
- `Efficiency metrics` 只在质量达标后比较，不单独决定 apply

### 3. Immutability Invariants

按 ADR 0054，不可变评估契约在 `P2-M1` 中至少体现为以下 5 条硬约束：

1. `Judge isolation`
   - proposal 可以改对象，不能改它依赖的 judge / harness
2. `Contract pinning`
   - 每次评测必须绑定固定的 `contract_id`、`contract_version`、judge assets、pass rule、budget limits
3. `Non-retroactivity`
   - contract 升级只影响未来 proposal，不回写历史结论
4. `Ownership split`
   - object change 与 contract change 不能混在同一 proposal 中
5. `Fixed keep/revert semantics`
   - keep / veto / rollback 规则必须在评测前固定，不能看结果后改口径

工程化表达：

**proposal 可以改对象，不能改裁判；改裁判必须走单独、版本化、可审计的治理路径。**

字段映射：

- keep semantics 由 `pass_rule_kind`、`pass_rule_params` 与 `veto_conditions` 固定
- revert semantics 由 `rollback_preconditions` 固定

### 4. Code Patch 不是一等 growth kind

按 ADR 0054，`P2-M1` 中的 raw code patch 不作为独立 growth object kind。  
它只可能是以下三种角色之一：

- 某个 growth object proposal 的实现产物
- eval 所需的实验性实现分支或工作树产物
- promote 候选的 supporting evidence

这意味着：

- agent 可以通过原子能力和代码编辑去“实现一个提案”
- 但最终被治理和被 apply 的对象仍然是 `soul` / `skill_spec` / `wrapper_tool` / `procedure_spec`
- 不能以“代码改好了”为理由绕过对象级 eval contract

### 5. Eval Contract 不随被评对象一起漂移

设计上必须固定：

- proposal 不能修改自己所依赖的 eval harness
- 被评对象与 judge assets 必须在存储和 ownership 上分离
- contract version 升级本身也是治理动作，不与普通 growth proposal 混提

这部分是本计划最想借鉴 `autoresearch` 的地方。

### 6. `GrowthEvalContract` 的落地形态

`P2-M1` 先采用 `doc + code declaration` 双重声明，不引入 DB / registry object。

- 文档层：用本计划和相关 design docs 固定 contract profile 与对象边界
- 代码层：由 adapter 侧类型、常量或声明式配置 pin `contract_id`、`version`、check names、pass rule
- `P2-M2+` 再视需要评估是否引入 registry / DB object；`P2-M1` 不承担这一步

## Object Boundary Matrix

| Object kind | P2 status | Mutable surface | Immutable eval contract | This round |
| --- | --- | --- | --- | --- |
| `soul` | onboarded | `SOUL.md` 内容、`soul_versions` proposal/eval payload | deterministic content checks、apply guard、rollback/veto、audit completeness | refine contract only |
| `skill_spec` | `P2-M1b` onboarding | `SkillSpec` / `SkillEvidence`、resolver/projector 消费面 | schema checks、precondition checks、resolution/projection regression、negative evidence semantics | define now |
| `wrapper_tool` | reserved | wrapper schema、tool binding、实现代码、deny behavior | typed I/O、permission boundary、dry-run/smoke、tool deny/error semantics | template now, runtime later |
| `procedure_spec` | reserved | procedure spec、state/guard/transition 定义 | deterministic transition suite、interrupt/resume、checkpoint recoverability | template now, implement in `P2-M2` |
| `memory_application_spec` | tentative reserved | memory application spec only | retrieval/share boundary、quality eval、scope correctness | defer to `P2-M3` |
| raw code patch | not a growth object | repo diff / branch artifact | never judged standalone in `P2-M1` | keep as evidence only |

## Minimum Contract Profiles

### A. `soul`

沿用现有最小 contract，不改生命周期：

四层映射：

- `Boundary gates`
  - 现有 `soul` checks 与 apply guards 全部落在这一层
- `Effect evidence`
  - `soul` V1 不适用
- `Scope claim`
  - `soul` V1 不适用
- `Efficiency metrics`
  - `soul` V1 不适用

- hard checks:
  - non-empty / coherence
  - size limit
  - diff sanity
- hard guards:
  - eval passed before apply
  - veto / rollback always available
- required artifacts:
  - intent
  - risk notes
  - diff summary
  - evidence refs

目标不是强化 `soul` 评测复杂度，而是把它正式表述为第一个 contract profile。

### B. `skill_spec`

`P2-M1b` 需要的最小 contract 应明确分成四层：

四层映射：

- `Boundary gates`
  - schema validity
  - activation correctness
  - projection safety
- `Effect evidence`
  - learning discipline
- `Scope claim`
  - scope claim
- `Efficiency metrics`
  - efficiency metrics

具体展开：

- `Boundary gates`
  - `SkillSpec` / `SkillEvidence` 字段完整、类型正确、版本语义合法
  - `activation_tags`、`preconditions`、`escalation_rules` 不自相矛盾
  - delta 预算、prompt 注入层位、与 active procedure / memory 事实冲突时的降级语义
- `Effect evidence`
  - 负经验只接受 deterministic signal
  - 正经验不能因为“用户没反对”自动成立
- `Scope claim`
  - 该 skill 只是 `local`，还是已经声称 `reusable` / `promotable`
- `Efficiency metrics`
  - 只在质量达标后比较 token / latency / cost 改善

换句话说，`skill_spec.evaluate()` 不能只看 schema，也要至少覆盖：

- resolve 得出来吗
- project 后会不会污染 prompt
- failure signal 的写入规则是否可审计
- claim 的普适性是否与证据等级匹配

### C. `wrapper_tool` / `procedure_spec`

本轮只定义 contract 模板，不做端到端实现。

模板目的：

- 防止 `P2-M1c` / `P2-M2` 再回头重谈“评测到底看什么”
- 提前明确 promote 的目标不是“会跑就行”，而是“有固定 harness”

## Work Packages

### WP1. Contract Vocabulary Freeze

固化以下术语：

- growth object
- mutable surface
- immutable harness
- hard checks
- scenario checks
- veto condition
- supporting evidence
- implementation artifact

输出：一页术语表 + object matrix。

产出文件：

- [`design_docs/GLOSSARY.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/GLOSSARY.md)
- [`dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md)

### WP2. `soul` Contract Profile

把现有 `SOUL` 演化链正式提升为第一份 contract profile。

输出：

- `soul` 的 mutable surface 定义
- `soul` 的 required artifacts
- `soul` 的 hard pass / veto / rollback 规则

产出文件：

- [`dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md)
- 下游消费：[`src/memory/evolution.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/memory/evolution.py)

### WP3. `skill_spec` Contract Profile

为 `P2-M1b` 给出可执行的最小 contract profile。

输出：

- `skill_spec` 的 checks taxonomy
- `skill_spec` 的 evidence discipline
- `skill_spec` 的 prompt pollution guard
- `skill_spec` 与 `TaskFrame` / resolver / projector 的评测边界
- `skill_spec` 的 scope claim 分级
- `skill_spec` 的 efficiency metrics 使用边界

产出文件：

- [`dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md)
- [`dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14_draft.md)

### WP4. Reserved Kind Templates

先给 `wrapper_tool`、`procedure_spec`、`memory_application_spec` 写出 contract skeleton。

输出：

- 每类对象未来至少要接受什么检查
- 哪些检查推迟到后续 milestone

产出文件：

- [`dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1_growth-eval-contract_2026-03-15.md)
- 下游消费：[`design_docs/procedure_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/procedure_runtime.md)
- 下游消费：[`design_docs/phase2/p2_m2_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m2_architecture.md)

### WP5. Plan Integration

将本计划的结论回填到：

- `P2-M1b` draft 中的 `SkillGovernedObjectAdapter.evaluate()`
- `P2-M1c` 的 growth case 设计
- future promote / demote policy 解释

产出文件：

- [`dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14_draft.md)
- future `dev_docs/plans/phase2/p2-m1c_*.md`
- future [`src/growth/policies.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/growth/policies.py) or successor policy doc

## Non-Goals

- 不在本计划中决定最终代码存储布局或 runtime runner 细节
- 不在本计划中引入新的 `GrowthObjectKind`
- 不在本计划中定义统一单指标分数
- 不在本计划中规定 agent 必须使用 Git ratchet 或无限循环实验
- 不在本计划中把 builder runtime 一并实现

## Risks

1. **过早框架化**：若把 contract 抽象做得过重，会拖慢 `P2-M1b`
2. **对象边界漂移**：若把 raw code patch 也当成一等对象，会放大治理面
3. **评测空心化**：若 contract 只有字段，没有 hard checks，最终仍会退回“各自发挥”
4. **评测过重**：若对 `skill_spec` 一开始就上全套 benchmark，会让 onboarding 卡死

## Mitigations

1. 先只为 `soul` 和 `skill_spec` 提供完整 profile
2. reserved kinds 只写 skeleton，不写重型实现
3. contract V1 只接受 deterministic checks + 小规模 scenario suite
4. 将 code patch 明确降级为 evidence / artifact，而非 object

## Acceptance

- 已接受 ADR 0054 与本计划无冲突，且本计划只向下细化，不回退其原则边界
- 明确列出 `P2-M1` 内的一等 growth object 边界，以及非对象清单
- 明确列出 growth eval contract 的四层结构及各自职责
- 明确列出 `Immutability Invariants`
- 明确 `GrowthEvalContract` 与 `GrowthEvalResult` 的语义桥接
- 明确 `soul` 的正式 eval contract profile
- 明确 `skill_spec` 的正式 eval contract profile，足够支撑 `P2-M1b`
- 明确 reserved kinds 的 contract skeleton，足够指导 `P2-M1c` / `P2-M2`
- 明确 proposal、code patch、eval harness 三者的 ownership separation
- 明确本计划在 `P2-M1a` / `P2-M1b` / `P2-M1c` 时序中的位置
- 明确哪些内容现在定义，哪些明确推迟

## Open Questions

1. `skill_spec` 的 scenario checks 应该放在治理层还是 runtime test suite？
   - 倾向：治理层只定义 contract；实际执行可由 runtime test suite 完成
2. promote 到 `wrapper_tool` 时，代码实现 diff 应算 proposal payload 还是 artifact ref？
   - 倾向：artifact ref，避免 payload 膨胀成“全代码镜像”

## Recommended Next Step

先以本计划作为已批准的设计基线，然后只把与 `skill_spec` onboarding 直接相关的结论回填到 `P2-M1b`。  
完整的 growth case、Git ratchet、keep/revert loop 设计推迟到 `P2-M1c` 讨论。
