---
doc_id: 019d8dca-c4f1-74e2-8fac-1004850b9fb8
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-14T20:59:29+00:00
---
# P2 Closeout：North Star Real-Use Loop

> 状态：approved
> 日期：2026-04-14
> 输入：P2-M3c 完成后的第三方过度设计评估、`design_docs/phase2/roadmap_milestones_v1.md`、ADR 0061、`design_docs/skill_objects_runtime.md`
> 定位：P2 收口后的真实使用闭环实施计划；不是完整 P3 self-evolution workflow。

## 0. 目标

把下一阶段目标从“继续扩展治理框架”切换为“证明一个真实使用闭环已经工作”。

北极星闭环：

**用户教 NeoMAGI 一个可复用方法 -> 系统形成可审计 skill proposal -> 用户确认后生效 -> 下一次相似任务自动复用 -> 结果反馈更新 evidence -> 用户可查看、解释、回滚。**

本计划回答一个问题：

**NeoMAGI 是否已经能把一次真实互动变成下次可见的能力增长，而不是只把增长描述成治理对象？**

完成后应具备：
- 用户教学不再停在日志或未生效 proposal。
- 至少一个真实 user-taught skill 能从 proposal 走到 active。
- 下一次相似任务能命中该 skill，并在 prompt skill layer 中注入有边界的 delta。
- 用户能查询“你记住了什么 / 为什么这次按这个做 / 怎么撤回”。
- evidence 能被明确反馈更新；没有用户确认时不自动写正经验。
- `GrowthEvalContract` 至少在 `skill_spec` 北极星链路中被运行时真实消费，避免继续作为纯文档结构。

## 1. 当前基线

P2-M3c 后，基础地基已经足够支撑真实闭环：

| 面 | 当前状态 |
| --- | --- |
| Skill runtime | `TaskFrame -> SkillResolver -> SkillProjector -> PromptBuilder` 已接入 AgentLoop |
| User teaching | `_detect_teaching_intent()` 能识别显式教学语句 |
| Skill proposal | `_propose_taught_skill()` 会调用 `SkillLearner.propose_new_skill()` 生成 `skill_spec` governance proposal |
| Skill governance | `SkillGovernedObjectAdapter` 已支持 propose / evaluate / apply / rollback / veto |
| Skill storage | `SkillStore` 已有 active skill、evidence 与 governance ledger |
| Runtime gap | proposal 没有用户可见 review / apply surface，容易永久停在 proposed |
| Feedback gap | `TaskOutcome.user_confirmed` 默认 false，正经验没有自然写入路径 |
| Explainability gap | prompt 里只有 delta，用户缺少“哪些 skill 生效了”的查询面 |
| Governance debt | `GrowthEvalContract.required_checks` / `pass_rule_kind` 等字段主要仍是声明，没有约束 adapter 行为 |
| Procedure | runtime 和 governance 已建好，但没有真实 user-facing procedure entry；本计划不继续扩展 procedure |

关键断点：

1. 教学能生成 proposal，但 proposal 不能自然变 active。
2. active skill 能被 resolver 命中，但用户很难查询和确认它。
3. evidence 负反馈路径存在，正反馈路径基本缺席。
4. contract 声明没有约束实际 eval check 覆盖。

## 2. 非目标与冻结规则

本计划刻意缩小 scope，避免继续扩大抽象面。

非目标：
- 不 onboard `memory_application_spec`。
- 不实现完整 P3 self-evolution workflow。
- 不新增 ProcedureSpec 作为演示，除非后续真实用户路径证明必须。
- 不实现 PromotionPolicy runtime。
- 不引入 skill 向量召回、skill graph、skill ontology 或新的 planner。
- 不做通用 growth admin console。
- 不做 Slack / 群聊 / Shared Companion 产品 demo。
- 不把 `skill object` 变成 workflow engine。

冻结规则：
- 任何新增抽象必须直接服务北极星闭环中的一个断点。
- 新增代码优先落到现有模块：`skills`、`growth.adapters.skill`、`tools.builtins`、`agent.message_flow`。
- 不为未来 P3 预写空接口。
- 不新增新的 governance kind。
- 不新增新的 lifecycle 状态。
- 不新增新的跨 agent 协作协议。

## 3. 用户闭环

### 3.1 Happy Path

用户第一次说：

> 以后遇到 retrieval regression 的问题，先看 fixture category 分布，再决定是 CJK、synonym 还是 semantic gap，不要直接上 vector。

系统行为：

1. 本 turn 正常回答用户。
2. post-run-learning 识别 teaching intent。
3. 生成 pending `SkillSpec + SkillEvidence` proposal。
4. 用户随后问“你刚才记住了什么？”系统通过 `skill_status` 展示 pending proposal。
5. 用户明确说“确认应用这个 skill”。
6. 系统 evaluate + apply proposal，成为 active skill。
7. 下一次用户问类似 retrieval 问题时，resolver 命中该 skill，PromptBuilder 注入有限 delta。
8. 任务完成后，用户说“这个方法有效，以后继续这样”，系统写入正 evidence。
9. 用户可以问“为什么这次先看 category？”系统能指出命中的 skill。
10. 用户可以撤回该 skill，rollback 后下一次不再注入。

### 3.2 Fail-Closed Path

- 用户没有确认 pending proposal：skill 不生效。
- proposal eval 失败：不 apply，返回 check summary。
- 用户教学内容包含 prompt injection / 越权规则：eval fail 或 policy reject。
- 用户要求跨 principal / shared-space 技能共享：本计划不支持，保持 owner-local。
- 用户让系统自动 promote 成 wrapper/procedure：不执行，只能生成后续 issue / plan。

## 4. 建议命名与阶段边界

建议命名：

`P2-Closeout: North Star Real-Use Loop`

原因：
- ADR 0061 已把 P2 收口到 M3；这个目标不应假装是新的 P2-M4。
- 它也不等于 P3 self-evolution；它是进入 P3 前的产品现实校准。
- 如果后续需要正式进入 P3，可以把本计划验收结果作为 P3 roadmap 的输入。

文档位置：
- 本计划批准为 `P2 Closeout`，正稿位于 `dev_docs/plans/phase2/p2-closeout_north-star-real-use-loop_2026-04-14.md`。
- P3 roadmap 是否启动取决于本计划 UAT 结果；本计划不直接创建 P3 roadmap。

## 5. 实施切片

### Slice A：现状锁定与回归用例

目标：先把当前断点固定成测试，不先改实现。

内容：
- 增加一个 failing/xfail-style 的 skill north-star 测试，覆盖：
  - teaching intent 生成 proposal；
  - proposal 默认不 active；
  - apply 后 list_active 可见；
  - 下一次相似 TaskFrame 命中 skill；
  - PromptBuilder skill layer 包含 delta。
- 使用现有 `FakeSkillStore` / integration fixture，优先复用 `tests/integration/test_skill_runtime_e2e.py`。
- 不引入新测试框架。

验收：
- 当前缺口能被一个清晰测试表达出来。
- 测试名字对应用户路径，不对应内部组件名堆叠。

### Slice B：Skill Lifecycle Tool Surface

目标：给用户/agent 一个最小可见面，让 pending skill 能被查看、确认、撤回。

新增最小工具面：

| Tool | 风险 | 作用 |
| --- | --- | --- |
| `skill_status` | low / read-only / concurrency-safe | 查询 active skills、pending proposals、recent governance history |
| `skill_manage` | high | 对明确 `governance_version` 或 `skill_id` 执行 apply / veto / rollback / record_feedback |

设计约束：
- 只服务 `skill_spec`，不做 generic growth admin。
- 不新增 service layer，除非 tool 内重复逻辑明显失控。
- `skill_status` 依赖 `SkillStore` 新增的 narrow query helper，例如 `list_proposals(status, limit)`。
- `skill_manage.apply_pending` 调用现有 `GrowthGovernanceEngine.evaluate()` 与 `apply()`。
- `skill_manage.rollback_active` 调用现有 `GrowthGovernanceEngine.rollback(skill_id=...)`。
- `skill_manage.record_feedback` 只在用户显式确认时更新 evidence。
- 写操作必须要求明确参数，不从自然语言猜测 `governance_version`。
- 在 auth mode 下，操作必须带 `context.principal_id`；no-auth dev mode 允许但 audit 标明 principal 为 null。

Composition root：
- 当前 `register_builtins()` 在 governance engine 构建前执行。
- 本轮不重排全部 gateway 初始化。
- 在 `_build_procedure_stack()` 返回 `governance_engine` 后，增加一个窄函数注册 skill lifecycle tools，例如 `_register_skill_tools(tool_registry, skill_store, governance_engine)`。

验收：
- 用户可问“有什么待确认的技能草稿？”并得到 pending proposal。
- 用户可明确确认一个 proposal，并看到 eval/apply 结果。
- 用户可撤回一个 active skill。

### Slice C：Teaching Draft 质量与去重

目标：让自动生成的 skill draft 足够可复用，避免制造垃圾 skill。

改动点：
- 改进 `_extract_skill_draft_from_context()`：
  - 去除教学信号前缀，如“以后这类任务”“remember this”“always do”。
  - 保留中文短句，不只依赖英文空格 split。
  - `summary` 必须是可读的人类句子，不只是 task type。
  - `activation_tags` 控制在 3-6 个，避免长句污染 resolver。
  - `delta` 控制在 1-3 条，优先保存用户明确教授的差异化动作。
- 增加 pending/active skill 去重：
  - 同 capability + 高重合 activation_tags + 相似 delta 时，不新建第二个 proposal。
  - 返回 existing proposal / active skill 信息给日志和 `skill_status`。
- 不引入 embedding 或 LLM extraction。

验收：
- 一个中文教学句能生成可读 skill draft。
- 重复教学不会生成多个几乎相同 proposal。
- delta 不超过现有 SkillProjector 预算。

### Slice D：Reuse 与可解释性

目标：第二次相似任务真的优先复用，不只是数据库里有 active skill。

改动点：
- `SkillProjector` 注入 delta 时附带最小来源语义，例如 `"<summary>: <delta>"`，让模型能解释为什么这样做。
- `message_flow._resolve_skills_for_request()` 记录 `skills_resolved` 日志，包含 skill ids、capability、proposal/version 信息。
- `skill_status` 支持按 `skill_id` 查询：
  - summary
  - activation
  - delta
  - evidence counts
  - recent governance status
- 不默认在每次回复里向用户宣布“我使用了某某 skill”；只在用户询问或需要解释时使用。

验收：
- 相似 TaskFrame 下 resolver 命中已 apply skill。
- PromptBuilder skill layer 中出现该 skill 的 delta。
- 用户问“为什么这样做？”时，agent 可通过工具或当前 prompt 信息解释。

### Slice E：Feedback 写入路径

目标：正经验必须来自明确确认；负经验继续来自 deterministic failure。

改动点：
- `skill_manage.record_feedback` 支持：
  - `skill_id`
  - `outcome`: `success` / `failure`
  - `note`
  - `failure_signal` 可选
- success 只在用户明确要求“记为有效 / 以后继续这样 / this worked”时由模型调用 tool。
- failure 可写入 `negative_patterns` 或 `known_breakages`，但必须保留用户 note。
- 不实现隐式情绪判断或 LLM 自判成功。

验收：
- 用户明确确认后，`success_count` +1，`last_validated_at` 更新。
- 用户明确指出失效后，`failure_count` +1，negative pattern 可见。
- 没有显式确认时，普通 assistant response 不增加 success_count。

### Slice F：GrowthEvalContract 最小去死结构化

目标：不在本轮大改 governance，但至少让北极星对象的 contract 约束真实 eval。

改动点：
- 仅针对 `skill_spec` adapter：
  - `_run_eval_checks()` 的 check name 必须覆盖 `SKILL_SPEC_EVAL_CONTRACT_V1.required_checks`。
  - 若缺 check 或多出未声明 check，eval fail-closed，并返回清楚 summary。
  - `passed` 由 `contract.pass_rule_kind` 决定；V1 只支持 `all_required`。
  - 遇到未支持的 `PassRuleKind`，fail-closed。
- 不实现跨 kind 通用 contract runner。
- 不处理 `mutable_surface`、`immutable_harness`、`rollback_preconditions`、`budget_limits` 的通用执行。
- 在计划评审后决定是否新建 ADR 修订：把未消费字段降级为文档，或逐步给每个字段找真实 runtime consumer。

验收：
- `skill_spec` contract 的 `required_checks` 和 `pass_rule_kind` 至少在 runtime path 中被读取。
- 现有 contract 结构测试减少对“字段存在”的依赖，增加对“声明与实际 eval 一致”的验证。

### Slice G：P2 Closeout UAT 与后续 issue

目标：用真实操作证明闭环，而不是只通过单元测试。

UAT 场景：
- 使用 WebChat auth 模式登录 owner principal。
- 用户教授一个真实工程习惯，例如 retrieval triage 或 review plan 方法。
- 查询 pending skill。
- 用户确认 apply。
- 开启一个相似问题，确认 skill 被复用。
- 用户确认该方法有效，evidence 更新。
- 用户 rollback，确认下一次不再注入。

产物：
- UAT 报告写入 `dev_docs/logs/phase2/`。
- 若批准执行且产生后续工作，使用 `bd create ... --json` 记录，不在 markdown 中维护 TODO。
- 如果发现 Procedure 必须介入，创建独立 follow-up，不把 Procedure 强塞进本计划。

## 6. 验收标准

必须满足：
- `teaching intent -> proposal -> apply -> reuse -> feedback -> rollback` 有一条自动化测试链路。
- 至少一个真实 user-taught skill 在本地 UAT 中完成上述闭环。
- `skill_status` 能展示 pending、active、evidence 与最近治理状态。
- 写操作只能基于明确 ID / version；不能让模型模糊 apply。
- `GrowthEvalContract.required_checks` / `pass_rule_kind` 在 `skill_spec` eval 中真实参与判定。
- 新增测试优先是行为测试，不再新增纯字段结构测试。
- 不新增 growth kind、procedure spec、promotion runtime、memory application spec。

建议满足：
- 至少沉淀 2 个真实 active SkillSpec：一个通过手动/fixture seed，一个通过用户教学链路。
- `SkillProjector` 输出能解释来源，但不明显增加 prompt 噪声。
- `just lint` 与受影响测试通过；若触及 frontend，再跑 `just test-frontend`。

## 7. 测试策略

目标测试：
- `tests/skills/test_store.py`
  - proposal listing / filtering helper。
- `tests/tools/test_skill_tools.py` 或相邻现有路径
  - `skill_status`
  - `skill_manage.apply_pending`
  - `skill_manage.rollback_active`
  - `skill_manage.record_feedback`
- `tests/skills/test_learner.py`
  - teaching extraction quality
  - duplicate proposal prevention
  - explicit positive feedback only
- `tests/growth/test_skill_adapter.py`
  - `required_checks` coverage enforcement
  - `pass_rule_kind=all_required`
  - unsupported pass rule fail-closed
- `tests/integration/test_skill_runtime_e2e.py`
  - full north-star chain with fake model/store where possible
  - PostgreSQL-backed apply/reuse path where needed

质量门：
- 受影响测试先跑。
- 合并前跑 `just lint` 与 `just test`。
- 若只改后端与 docs，不跑 frontend。
- 如果添加工具 schema，增加至少一个 schema/required args 测试，避免 LLM tool call 参数面漂移。

## 8. 风险与处理

| 风险 | 处理 |
| --- | --- |
| skill tool 变成 generic governance admin | 工具只支持 `skill_spec`，命名和参数都围绕 skill |
| 用户教学产生低质量 skill | draft 质量规则 + 去重 + pending review，不自动 apply |
| 模型误 apply pending proposal | `skill_manage` 要求明确 `governance_version` 和 action；高风险 tool 受 guardrail |
| prompt 被 skill delta 污染 | 继续沿用 SkillProjector budget；每 skill 最多 3 条，总数最多 9 条 |
| evidence 被误学 | 正经验只来自显式 feedback；普通成功不写 |
| contract runner scope 膨胀 | 只修 `skill_spec` 链路，不做跨 kind 框架 |
| P2 继续拖尾 | 本计划定义为 closeout / P3-0，批准时必须同时决定是否关闭 P2 |

## 9. 已批准决策

1. 目标命名为 `P2 Closeout`。
2. `skill_manage.apply_pending` 允许在 `chat_safe` 中出现；它仍是 high-risk tool，必须要求明确 `governance_version` 与 action，并受 guardrail 约束。
3. user-taught skill 暂按 owner-global 处理；多 principal skill scope 留给 Shared Companion 之后。
4. 本轮只让 `skill_spec` contract 真实消费 `required_checks/pass_rule_kind`，不同时清理 wrapper/procedure 的 contract 字段。
5. UAT 以 WebChat auth 为准，Telegram 只做非阻塞 smoke。

## 10. 执行入口

批准后执行：
- 创建 bd parent issue 与按 slice 拆分的子 issue。
- 执行后更新 `dev_docs/progress/project_progress.md`。
- 根据 UAT 结果决定进入 P3 self-evolution workflow，或先做一次 governance diet。

执行中若北极星闭环验证不成立：
- 暂停继续扩展治理层。
- 降级为只实现 `skill_status + skill_apply`，先让 existing proposal 能生效。
