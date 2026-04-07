---
doc_id: 019d6457-9290-7ef5-be6b-40618a07a865
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-06T21:49:14+02:00
---
# Glossary

> 目的：为 NeoMAGI 提供轻量级、可持续维护的 Domain Ontology。  
> 口径优先级：`decisions/` > `design_docs/` > `dev_docs/`。  
> 范围：只收录跨文档反复出现、且容易混淆的核心术语；不追求枚举所有代码符号。

## 使用原则

- 术语冲突时，先以已接受 ADR 为准。
- 同一概念尽量只保留一个主词；历史别名只作为 `Aliases` 保留。
- 本文件解释“概念是什么”，不替代具体实现文档、计划或测试用例。

## 阅读导航

- `Identity & Prompt Context`：`SOUL`、`SOUL.md`、`USER.md`、`IDENTITY.md`、`Principal`、`Shared Companion`、`Relationship Space`
- `Governance & Evaluation`：growth object、proposal、contract、checks、veto、evidence
- `Capability Layer`：tool、skill、wrapper、procedure、memory application
- `Builder & Workspace Artifacts`：builder task、growth case、artifact identity
- `Implementation Artifacts`：code diff、implementation artifact

## Identity & Prompt Context

### SOUL
- **Category**：Identity / Governance Object
- **Aliases**：`soul`
- **Definition**：NeoMAGI 的受治理“自我/原则/价值观”对象。它回答“agent 是谁、按什么原则代表用户”，而不是“具体任务怎么做”。
- **Relations**：
  - `projected-as` → [SOUL.md](#soulmd)
  - `aligned-with` → [Principal](#principal)
  - `is-a` → [Growth Object](#growth-object)
  - `evaluated-by` → [GrowthEvalContract](#growthevalcontract)

### SOUL.md
- **Category**：Workspace Projection
- **Aliases**：`workspace/SOUL.md`
- **Definition**：当前 active `SOUL` 的运行时投影文件，不是最终真源。项目语义上以 DB 中 active soul version 为准，`SOUL.md` 负责工作区可见性和 prompt 注入。它回答“agent 是谁、按什么原则代表用户、采用什么内在人格/语气”，而不是“用户偏好是什么”或“外部展示名片是什么”。
- **Notes**：
  - post-bootstrap 常态下，`SOUL.md` 的变更必须走 `propose -> evaluate -> apply -> rollback` 治理路径；人类保留 veto/rollback 权限，但不直接改常态文本。
  - 在 workspace context 语义上，`SOUL.md` 的约束优先级低于 [USER.md](#usermd)，高于 [IDENTITY.md](#identitymd)。
- **Relations**：
  - `projection-of` → [SOUL](#soul)
  - `lower-priority-than` → [USER.md](#usermd)
  - `higher-priority-than` → [IDENTITY.md](#identitymd)

### USER.md
- **Category**：Workspace Context / User Preference
- **Aliases**：`workspace/USER.md`
- **Definition**：当前 workspace 中描述“这个 agent 正在为谁服务，以及应该如何个性化适配”的文件。它承载用户侧信息，如语言、时区、沟通风格、技术栈、长期偏好和不希望被违背的回答习惯，回答“服务对象是谁、应该怎样配合这个用户”，而不是“agent 自己是谁”或“agent 对外叫什么名字”。
- **Notes**：
  - 当前设计中，`USER.md` 是每 turn 直接注入的 workspace context 文件，不是 DB-backed projection，也不是当前 growth governance 的对象。
  - 语义上它属于“用户侧约束 / 个性化层”，冲突时优先级高于 [SOUL.md](#soulmd) 和 [IDENTITY.md](#identitymd)。
  - `USER.md` 不等同于 `principal`、认证后的 canonical user identity、`account_id` 或 `peer_id`；这些身份绑定语义属于 `P2-M3` 的 principal / binding 模型，而不是当前的偏好文件。
- **Relations**：
  - `customizes` → [Principal](#principal)
  - `higher-priority-than` → [SOUL.md](#soulmd)
  - `higher-priority-than` → [IDENTITY.md](#identitymd)

### IDENTITY.md
- **Category**：Workspace Context / Presentation
- **Aliases**：`workspace/IDENTITY.md`
- **Definition**：当前 workspace 中描述 agent 外在身份名片的结构化文件，用于展示层和表面呈现，例如 name、role、voice 或类似 metadata。它回答“外界看到你叫什么、你以什么角色出现”，而不是“你的内在原则是什么”或“当前服务的用户偏好是什么”。
- **Notes**：
  - `IDENTITY.md` 是展示层语义，不是 `SOUL` 的治理对象，不承载 `propose/evaluate/apply/rollback` 生命周期。
  - 它也不等同于 `principal`、认证身份、`account_id`、`peer_id` 或渠道绑定证据；那些是运行时 identity / binding 语义，不是展示名片。
  - 在 workspace context 语义上，`IDENTITY.md` 优先级低于 [USER.md](#usermd) 和 [SOUL.md](#soulmd)。
- **Relations**：
  - `presentation-layer-for` → [SOUL](#soul)
  - `lower-priority-than` → [USER.md](#usermd)
  - `lower-priority-than` → [SOUL.md](#soulmd)
  - `not-the-same-as` → [Principal](#principal)

### Principal
- **Category**：Identity / Runtime
- **Aliases**：user-interest line
- **Definition**：NeoMAGI 在运行时所代表的“同一个用户利益”身份轴。多 agent 默认共享同一个 `SOUL / principal`，而不是各自拥有独立长期人格。
- **Relations**：
  - `co-defined-by` → [SOUL](#soul)

### Shared Companion
- **Category**：Product Direction / Identity & Memory
- **Aliases**：shared AI companion, common companion
- **Definition**：NeoMAGI 在明确授权的共享关系空间中服务多个已认证 principal 的产品能力方向。它不是群聊本身，也不是多长期人格；核心是 consent-scoped relationship memory、membership、provenance 与对关系本身有建设性的建议。
- **Relations**：
  - `uses` → [Relationship Space](#relationship-space)
  - `serves` → [Principal](#principal)
  - `requires` → [Memory Visibility](#memory-visibility)

### Relationship Space
- **Category**：Identity / Memory Scope
- **Aliases**：shared space, relationship shared space, `shared_space_id`
- **Definition**：多个已认证 principal 明确加入的共享关系空间，例如情侣、家庭或团队。它回答“哪些人共同拥有这一段关系上下文、哪些记忆可在此召回”，不是 channel、Slack 群或 Telegram 群本身。
- **Relations**：
  - `contains` → [Principal](#principal)
  - `bounds` → [Memory Visibility](#memory-visibility)
  - `enables` → [Shared Companion](#shared-companion)

### Memory Visibility
- **Category**：Memory / Sharing Policy
- **Aliases**：visibility policy, consent-scoped memory visibility
- **Definition**：记忆条目的可见性声明，用于区分 `private_to_principal`、`shared_in_space` 与 `shareable_summary` 等边界。它决定一条记忆能否在某个 principal 或 relationship space 中被检索、召回或发布。
- **Relations**：
  - `bounds` → [Relationship Space](#relationship-space)
  - `protects` → [Principal](#principal)

## Governance & Evaluation

### Growth Object
- **Category**：Governance
- **Aliases**：governed growth object
- **Definition**：被允许进入显式成长生命周期的第一等对象。只有 growth object 才能进入 `propose -> evaluate -> apply -> rollback` 路径；普通实现产物或代码 diff 不自动成为 growth object。
- **Relations**：
  - `typed-by` → [Growth Object Kind](#growth-object-kind)
  - `mutated-by` → [Growth Proposal](#growth-proposal)
  - `evaluated-by` → [GrowthEvalContract](#growthevalcontract)

### Growth Object Kind
- **Category**：Governance
- **Aliases**：`GrowthObjectKind`, growth kind
- **Definition**：growth governance kernel 识别的对象类型枚举。当前核心 kind 包括 `soul`、`skill_spec`、`wrapper_tool`、`procedure_spec`、`memory_application_spec`。
- **Relations**：
  - `includes` → [SOUL](#soul)
  - `includes` → [SkillSpec](#skillspec)
  - `includes` → [Wrapper Tool](#wrapper-tool)
  - `includes` → [ProcedureSpec](#procedurespec)
  - `includes` → [Memory Application Spec](#memory-application-spec)

### Growth Proposal
- **Category**：Governance
- **Aliases**：`GrowthProposal`, proposal
- **Definition**：对某个 growth object 的可审计变更提案。它记录 intent、risk notes、diff summary、evidence refs 和对象相关 payload，但不应包含它自己的 judge / harness 变更。
- **Relations**：
  - `targets` → [Growth Object](#growth-object)
  - `evaluated-under` → [GrowthEvalContract](#growthevalcontract)
  - `may-attach` → [Raw Code Patch](#raw-code-patch)

### GrowthEvalContract
- **Category**：Governance / Evaluation
- **Aliases**：eval contract
- **Definition**：针对某类 growth object 的一等治理对象。它必须是 `object-scoped`、`versioned`、`immutable`，并定义什么可变、什么 judge 资产固定、以及 pass / veto / rollback 如何判定。
- **Relations**：
  - `applies-to` → [Growth Object](#growth-object)
  - `contains` → [Boundary Gates](#boundary-gates)
  - `contains` → [Effect Evidence](#effect-evidence)
  - `contains` → [Scope Claim](#scope-claim)
  - `contains` → [Efficiency Metrics](#efficiency-metrics)

### GrowthEvalResult
- **Category**：Governance / Evaluation
- **Aliases**：eval result
- **Definition**：一次 pinned contract run 的结果投影。它记录 pass/fail 判定、逐项 check 结果、以及所 pin 的 `contract_id` 和 `contract_version`，支持审计和非回溯性。
- **Relations**：
  - `produced-by` → adapter `evaluate()` under [GrowthEvalContract](#growthevalcontract)
  - `pins` → [Contract Pinning](#contract-pinning)

### Mutable Surface
- **Category**：Governance / Evaluation
- **Aliases**：mutable face
- **Definition**：一个 growth object 中，被提案允许改变的面。例如 `soul` 的 mutable surface 是 `SOUL.md` 内容和 `soul_versions` payload；`skill_spec` 的 mutable surface 是 `SkillSpec` / `SkillEvidence` 字段。与 immutable harness 分离是 eval contract 的核心不变量。
- **Relations**：
  - `defined-by` → [GrowthEvalContract](#growthevalcontract)
  - `mutated-by` → [Growth Proposal](#growth-proposal)

### Immutable Harness
- **Category**：Governance / Evaluation
- **Aliases**：eval harness, judge assets
- **Definition**：评测所依赖的脚本、case corpus、judge 规则、golden cases、deny-list 等资产集合。proposal 可以改对象，不能改裁判；改裁判必须走单独、版本化、可审计的治理路径。
- **Relations**：
  - `defined-by` → [GrowthEvalContract](#growthevalcontract)
  - `consumed-by` → adapter `evaluate()` implementation

### Contract Pinning
- **Category**：Governance / Evaluation
- **Aliases**：eval pinning
- **Definition**：每次评测必须绑定固定的 `contract_id`、`contract_version`、judge assets、pass rule、budget limits。这是 ADR 0054 不变量 2 的工程表达。pinning 保证同一 proposal 不会因为 contract 变更而改变判定结果。
- **Relations**：
  - `enforces` → [GrowthEvalContract](#growthevalcontract)
  - `recorded-in` → [GrowthEvalResult](#growthevalresult)

### Judge Isolation
- **Category**：Governance / Evaluation
- **Aliases**：judge separation
- **Definition**：proposal 可以改对象，不能改它依赖的 judge / harness。这是 ADR 0054 不变量 1 的核心原则，防止"通过修改裁判来赢"。
- **Relations**：
  - `protects` → [Immutable Harness](#immutable-harness)
  - `constrains` → [Growth Proposal](#growth-proposal)

### Boundary Gates
- **Category**：Evaluation
- **Aliases**：hard gates
- **Definition**：评估契约中的硬门槛，优先判断边界是否安全、架构是否清晰、回滚是否成立、依赖是否显式。未通过时，不进入后续效果或效率比较。
- **Relations**：
  - `layer-of` → [GrowthEvalContract](#growthevalcontract)
  - `precedes` → [Effect Evidence](#effect-evidence)

### Hard Checks
- **Category**：Evaluation
- **Aliases**：deterministic checks, boundary checks
- **Definition**：必须通过才能进入后续评测层的 deterministic 验证项。通常落在 Boundary gates 层，例如 schema 校验、非空检查、diff 合理性检查。与 scenario checks 的区别在于：hard checks 是纯 deterministic 的，不依赖外部 case corpus 或运行时环境。
- **Relations**：
  - `subset-of` → [Boundary Gates](#boundary-gates)
  - `required-by` → [GrowthEvalContract](#growthevalcontract)

### Scenario Checks
- **Category**：Evaluation
- **Aliases**：scenario tests, regression checks
- **Definition**：使用固定 case corpus 或回归测试套件验证对象行为的检查项。通常落在 Effect evidence 或 Boundary gates 层。与 hard checks 的区别在于：scenario checks 可能依赖外部 test suite 或 before/after case 对比。
- **Relations**：
  - `layer-of` → [Effect Evidence](#effect-evidence) or [Boundary Gates](#boundary-gates)
  - `defined-by` → [GrowthEvalContract](#growthevalcontract)

### Effect Evidence
- **Category**：Evaluation
- **Aliases**：before/after evidence, ablation evidence
- **Definition**：证明“这个改动确实带来改善”的证据层。默认应至少有固定 before / after cases；当改动声称是更普适的改进时，可增加轻量 ablation。
- **Relations**：
  - `layer-of` → [GrowthEvalContract](#growthevalcontract)
  - `precedes` → [Scope Claim](#scope-claim)

### Scope Claim
- **Category**：Evaluation
- **Aliases**：applicability claim
- **Definition**：对改进适用范围的明确声明，例如 `local`、`reusable`、`promotable`。claim 越强，所需证据和回归要求越高。
- **Relations**：
  - `layer-of` → [GrowthEvalContract](#growthevalcontract)
  - `precedes` → [Efficiency Metrics](#efficiency-metrics)

### Efficiency Metrics
- **Category**：Evaluation
- **Aliases**：efficiency signals
- **Definition**：在质量已达标后，用于比较效率收益的指标层，例如 `tokens_per_success`、`latency_per_success`、`cost_per_success`。它不单独决定是否 apply。
- **Relations**：
  - `layer-of` → [GrowthEvalContract](#growthevalcontract)

### PassRuleKind
- **Category**：Governance / Evaluation
- **Aliases**：pass rule
- **Definition**：eval contract 中定义 pass/fail 判定策略的有限枚举。V1 只允许 `all_required`（全部 required check 必须通过）和 `hard_pass_and_threshold`（hard check 必须全过，soft check 达阈值即可）。
- **Relations**：
  - `used-by` → [GrowthEvalContract](#growthevalcontract)

### Veto Condition
- **Category**：Governance / Evaluation
- **Aliases**：veto rule, one-ticket-reject
- **Definition**：一票否决条件。当 eval 结果中出现 veto condition 匹配时，无论其他 check 结果如何，整体判定为 FAIL。veto conditions 必须在 eval 前固定（ADR 0054 不变量 5），不能看结果后改口径。
- **Relations**：
  - `defined-by` → [GrowthEvalContract](#growthevalcontract)

### Supporting Evidence
- **Category**：Governance
- **Aliases**：evidence ref
- **Definition**：附着在 proposal 上的辅助证据引用，如对话 ID、测试结果摘要、外部来源引用等。它本身不是 growth object，不接受独立治理生命周期，只作为 proposal 的可审计附件。
- **Relations**：
  - `attached-to` → [Growth Proposal](#growth-proposal)

## Capability Layer

### Capability
- **Category**：Product Surface
- **Aliases**：capability cluster
- **Definition**：对外稳定暴露的能力名或能力簇。它不是内部真实成长对象；内部实际支撑物通常是一个或多个 skill object。
- **Relations**：
  - `supported-by` → [Skill Object](#skill-object)

### Atomic Tool
- **Category**：Capability Layer
- **Aliases**：typed tool, atomic capability
- **Definition**：稳定、typed、可审计的底层能力单元。它回答“系统最底层能做什么”，而不回答“某类任务通常怎么更稳”。
- **Relations**：
  - `underlies` → [Wrapper Tool](#wrapper-tool)
  - `preferred-by` → [SkillSpec](#skillspec)
  - `used-by` → [Procedure Runtime](#procedure-runtime)

### Skill Object
- **Category**：Capability Layer
- **Aliases**：skill
- **Definition**：结构化的运行时经验对象，用来承载“这类任务基于已有经验通常该怎么更稳”的可复用 delta。它不是 tool，不是 procedure，也不是人格。
- **Relations**：
  - `specified-by` → [SkillSpec](#skillspec)
  - `supports` → [Capability](#capability)
  - `may-promote-to` → [Wrapper Tool](#wrapper-tool)
  - `may-suggest-entry-to` → [ProcedureSpec](#procedurespec)

### SkillSpec
- **Category**：Capability Layer / Governance Object
- **Aliases**：`skill_spec`
- **Definition**：一个 skill object 的静态规格。它定义 summary、activation、activation tags、preconditions、delta、tool preferences 和 escalation rules，是 `P2-M1b` 计划接入的第二类正式 growth object。
- **Relations**：
  - `specifies` → [Skill Object](#skill-object)
  - `paired-with` → [SkillEvidence](#skillevidence)
  - `supports` → [Capability](#capability)
  - `may-promote-to` → [Wrapper Tool](#wrapper-tool)
  - `is-a` → [Growth Object](#growth-object)
  - `evaluated-by` → [GrowthEvalContract](#growthevalcontract)

### SkillEvidence
- **Category**：Capability Layer / Runtime Evidence
- **Aliases**：skill runtime evidence
- **Definition**：附着在一个 `SkillSpec` 上的动态经验层，记录 success / failure、positive / negative patterns、known breakages 等运行时证据。它不是单独的 capability 层，而是 skill 的可学习部分。
- **Relations**：
  - `attached-to` → [SkillSpec](#skillspec)

### Wrapper Tool
- **Category**：Capability Layer / Governance Object
- **Aliases**：`wrapper_tool`
- **Definition**：single-turn governed capability unit。它内部可以组合多个底层动作，但对上层仍表现为一个原子动作。可被受治理地 apply / rollback / supersede，不承载 cross-turn state（ADR 0056）。适合"多命令但仍原子"的场景，复杂到需要 state / guard / recovery 时就不再适合停留在 wrapper tool。
- **Relations**：
  - `built-from` → [Atomic Tool](#atomic-tool)
  - `may-be-promoted-from` → [SkillSpec](#skillspec)
  - `may-promote-to` → [ProcedureSpec](#procedurespec)
  - `is-a` → [Growth Object](#growth-object)
  - `has` → [implementation_ref](#implementation_ref)
  - `declares` → [scope_claim (wrapper tool)](#scope_claim-wrapper-tool)
  - `declares` → [deny_semantics](#deny_semantics)

### ProcedureSpec
- **Category**：Runtime Control / Governance Object
- **Aliases**：`procedure_spec`
- **Definition**：多步流程的静态协议定义。它描述状态、guard、transition 和允许动作，但它本身不是运行时实例。适用于多步、跨 turn、有顺序依赖、有副作用、需要 approval / recovery 的任务。
- **Relations**：
  - `executed-by` → [Procedure Runtime](#procedure-runtime)
  - `may-be-entered-from` → [Skill Object](#skill-object)
  - `may-be-promoted-from` → [Wrapper Tool](#wrapper-tool)
  - `is-a` → [Growth Object](#growth-object)

### Procedure Runtime
- **Category**：Runtime Control
- **Aliases**：procedure engine
- **Definition**：执行 `ProcedureSpec` 的 deterministic runtime control layer。它负责 guard、execute、context patch、transition、checkpoint、resume 和 side-effect boundary，而不是承担高层经验学习。
- **Relations**：
  - `consumes` → [ProcedureSpec](#procedurespec)
  - `manages` → [Active Procedure](#active-procedure)
  - `uses` → [Atomic Tool](#atomic-tool)

### Active Procedure
- **Category**：Runtime Control
- **Aliases**：procedure instance
- **Definition**：某个 `ProcedureSpec` 在运行时的会话内实例。它记录当前 state、上下文和推进状态，是 spec 的动态执行态而不是静态定义。
- **Relations**：
  - `instance-of` → [ProcedureSpec](#procedurespec)
  - `managed-by` → [Procedure Runtime](#procedure-runtime)

### Memory Application Spec
- **Category**：Memory / Governance Object
- **Aliases**：`memory_application_spec`
- **Definition**：建立在稳定 memory kernel 之上的声明式 memory application 规格。当前在 `P2-M1` 中仍是 tentative reserved kind，正式定义推迟到 `P2-M3`。
- **Relations**：
  - `is-a` → [Growth Object](#growth-object)

### implementation_ref
- **Category**：Capability Layer / Implementation
- **Aliases**：`implementation_ref`
- **Definition**：Python entrypoint string，格式为 `<module_path>:<factory_name>`，factory 返回 `BaseTool` 实例。这是 V1 implementation choice，不是 ADR 冻结项。
- **Relations**：
  - `used-by` → [Wrapper Tool](#wrapper-tool)

### scope_claim (wrapper tool)
- **Category**：Capability Layer / Governance
- **Aliases**：wrapper scope claim
- **Definition**：wrapper tool 的作用域声明。取值为 `local`（仅当前上下文有效）、`reusable`（跨会话可复用）、`promotable`（可进一步提升为更稳定对象）。claim 越强，所需证据和回归要求越高。与 eval 层的通用 [Scope Claim](#scope-claim) 概念一致，但此处特指 wrapper tool 对象上的声明值。
- **Relations**：
  - `declared-by` → [Wrapper Tool](#wrapper-tool)
  - `verified-by` → `scope_claim_consistency` check in [GrowthEvalContract](#growthevalcontract)

### deny_semantics
- **Category**：Capability Layer / Governance
- **Aliases**：deny behavior, deny rules
- **Definition**：wrapper tool 的拒绝语义定义，声明何种输入条件或运行时状态下 wrapper 应主动拒绝执行（而非静默失败或产生不安全输出）。是 wrapper tool eval contract 的 veto condition 之一（`deny_semantics_broken`）。
- **Relations**：
  - `declared-by` → [Wrapper Tool](#wrapper-tool)
  - `verified-by` → [GrowthEvalContract](#growthevalcontract)

## Builder & Workspace Artifacts

### BuilderTaskRecord
- **Category**：Builder / Work Memory
- **Aliases**：builder task record
- **Definition**：builder work memory 的逻辑对象，记录 builder 任务的 brief、progress、blockers、validation summary、artifact refs 和 promote candidate refs。canonical record 在 `workspace/artifacts/` 下的 workspace artifact 中（ADR 0055），`bd / beads` 只承担索引职责。
- **Relations**：
  - `persisted-in` → `workspace/artifacts/`
  - `indexed-by` → `bd / beads` issues
  - `references` → [artifact_id](#artifact_id)

### GrowthCaseSpec
- **Category**：Growth / Case
- **Aliases**：growth case spec, case spec
- **Definition**：curated growth case 的规格定义。在 V1 中为 hardcoded catalog（ADR 0057 指导原则：具体形状属于 implementation-level choice）。每个 case 定义验收场景、预期行为和评估标准。
- **Relations**：
  - `instantiated-as` → [GrowthCaseRun](#growthcaserun)
  - `is-not-a` → [Growth Object](#growth-object)

### GrowthCaseRun
- **Category**：Growth / Case / Runtime
- **Aliases**：growth case run, case run
- **Definition**：growth case 的一次执行记录。workspace artifact 持久化（`workspace/artifacts/growth_cases/<case_id>/<run_id>.md`），不进 PostgreSQL。记录执行输入、输出、判定结果和 evidence refs。
- **Relations**：
  - `instance-of` → [GrowthCaseSpec](#growthcasespec)
  - `persisted-in` → `workspace/artifacts/`
  - `identified-by` → [artifact_id](#artifact_id)

### artifact_id
- **Category**：Identity / Workspace
- **Aliases**：`artifact_id`
- **Definition**：workspace artifact 的稳定标识符，采用 UUIDv7（ADR 0053 / 0055）。必须写入 artifact 真源元数据，而不是只存在 bead 索引或 projection 中。保证 artifact 在重命名、移动或重建索引后仍保持身份稳定。
- **Relations**：
  - `identifies` → workspace artifacts in `workspace/artifacts/`
  - `referenced-by` → [BuilderTaskRecord](#buildertaskrecord)
  - `referenced-by` → [GrowthCaseRun](#growthcaserun)

## Implementation Artifacts

### Implementation Artifact
- **Category**：Implementation
- **Aliases**：code artifact
- **Definition**：实现某个 proposal 或 eval 所需的非一等产物，例如代码 diff、实验分支产物、build 输出等。在 P2-M1 中，implementation artifact 不是 growth object。
- **Relations**：
  - `produced-by` → [Growth Proposal](#growth-proposal) or eval process
  - `is-not-a` → [Growth Object](#growth-object)

### Raw Code Patch
- **Category**：Implementation Artifact
- **Aliases**：repo diff, code diff
- **Definition**：实现某个 proposal 时产生的代码改动产物。在 `P2-M1` 中，它本身不是 growth object，只是 implementation artifact 或 supporting evidence。
- **Relations**：
  - `supports` → [Growth Proposal](#growth-proposal)
  - `is-not-a` → [Growth Object](#growth-object)
