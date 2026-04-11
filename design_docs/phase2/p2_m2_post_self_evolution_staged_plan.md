---
doc_id: 019d6c2c-f782-74fe-84e7-da09b8248d66
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-08T08:19:39+00:00
---
# P2-M2 Post / P3 Self-Evolution 分阶段计划（提案）

> 状态：proposed
> 当前结论：完整 `Self-Evolution CLI Demo V1` 不再排在 `P2-M3` 前；`P2-M3` 前只补两个窄前置：`P2-M2c` 与 `P2-M2d`。
> 完整 workflow 迁移到 Phase 3 候选方向，不再作为 P2 milestone。

## 1. 结论先行

原提案中“受治理自我演进”的方向正确，但把两类不同层级的工作混在了一起：

- 内核补洞：让 `procedure_spec` 与 memory source ledger 具备后续演进所需的治理和数据地基。
- 完整 demo：用 CLI、beads、git worktree、外部 coding agent、review loop 与 human gate 推进一次真实工程演进。

新的推荐排期为：

```text
P2-M2c: ProcedureSpec Governance Adapter
P2-M2d: Memory Source Ledger Prep for P2-M3
P2-M3: Identity / Principal / Visibility / Memory Policy
P3: Governed Self-Evolution Workflow
```

因此：

- `P2-M2c` 只让 NeoMAGI 获得“安全修改流程定义”的能力。
- `P2-M2d` 只按 ADR 0060 为 `P2-M3` 准备最薄的 DB memory source ledger 写入地基。
- 完整 `Self-Evolution CLI Demo V1` 移出 P2，作为 Phase 3 候选工作流方向，而不是插在 `P2-M2` 和 `P2-M3` 之间的 demo。

## 2. 为什么拆分

完整 self-evolution CLI 闭环至少包含：

- external CLI runner：Claude Code CLI / Codex CLI 的受控调用、超时、部分输出、失败恢复。
- worktree orchestration：branch / worktree / review snapshot / artifact visibility。
- multi-round review：计划审阅、实现审阅、P1/P2 finding 收敛。
- durable human gates：scope / plan / UAT approval 的 principal、审计和 resume 语义。
- closeout artifact writers：计划、审阅、进度、用户测试说明、open issues。

这些不是 `P2-M2` runtime 的小 demo，而是一个完整工作流产品能力。若现在推进，会同时放大 P2-M2 用户测试暴露的缺口：

- `available_keys` 仍可能在 compaction 后丢失，ProcedureView 尚不暴露 staging 结构。
- `ActionSpec` 还没有原生 noop / direct transition。
- terminal 后无 completion signal，当前只靠请求级写工具断路器止血。
- 外部 CLI wrapper 的错误模式远复杂于单 turn `BaseTool.execute()`。

拆分后，`P2-M2c` 和 `P2-M2d` 各自保持可验收的小边界，完整 workflow 等 `P2-M3` 的 principal、visibility、memory ledger 边界稳定后，再进入 Phase 3 评估。

## 3. P2-M2c：ProcedureSpec Governance Adapter

### 目标

让 `procedure_spec` 从 reserved kind 进入正式治理路径：

```text
proposal -> eval -> apply -> rollback / veto -> audit
```

它回答的问题是：

> NeoMAGI 能不能安全地修改自己的流程定义？

### In

- 将 `procedure_spec` policy 从 `reserved` 调整为 `onboarded`，并绑定明确 adapter。
- 将 `PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON` 升级为正式 `PROCEDURE_SPEC_EVAL_CONTRACT_V1`。
- 新增 `ProcedureSpecGovernedObjectAdapter`：
  - `propose`
  - `evaluate`
  - `apply`
  - `rollback`
  - `veto`
  - `get_active`
- 复用现有 `ProcedureSpecRegistry` 与静态校验：
  - transition determinism
  - guard completeness
  - action/tool binding validity
  - interrupt/resume safety
  - checkpoint recoverability
- 定义 apply / rollback 的安全边界：
  - 不允许覆盖正在运行的 active instances。
  - rollback 必须能移除或禁用已注册 spec。
  - in-place upgrade 默认禁止，除非后续有明确 migration protocol。
- 使用一个 3-5 状态的小型真实 spec 跑通治理闭环。

### Out

- 不做 Claude Code CLI / Codex CLI wrapper。
- 不做 git worktree 自动编排。
- 不做完整 self-evolution workflow。
- 不做 memory source ledger。
- 不做 identity / shared-space / consent policy。
- 不做外部平台写动作。

### 验收口径

- 一个 `procedure_spec` 提案能被评估、应用、出现在 `ProcedureSpecRegistry` 中并被 runtime 使用。
- 不合法 spec fail-closed，不能进入 registry。
- 有 active instance 时，破坏性 rollback / replacement 被拒绝。
- rollback / veto 后，新请求不能再进入被撤销 spec。
- 证据写入 growth governance ledger 与仓库内测试报告。

## 4. P2-M2d：Memory Source Ledger Prep for P2-M3

### 目标

按 ADR 0060 先完成最薄的 DB ledger 写入预备，降低 `P2-M3` identity / visibility / shared-space safety skeleton 的迁移风险。

它回答的问题是：

> 后续 memory visibility policy 的事实落点在哪里？

### In

- 新增 append-only DB source ledger schema。
- 新增只追加 writer API。
- `memory_append` 双写：
  - DB ledger
  - 现有 daily note projection
- 增加 parity / reconcile 检查：
  - 发现 DB ledger 与 workspace projection 不一致时报告。
  - 不做复杂自动修复。
- 保持现有 read path 不变：
  - `memory_search` 仍走现有 projection / index 路径。
  - `memory_entries` reindex 暂不切到 DB ledger。

### Out

- 不切换 memory read path。
- 不关闭 Markdown daily note projection。
- 不做历史 memory 全量迁移。
- 不实现 shared-space memory。
- 不实现 consent-scoped visibility policy。
- 不 onboard `memory_application_spec`。
- 不实现完整 `render/export/import/reconcile` 命令体系。
- 不改变 `MEMORY.md` prompt 注入语义。

### 验收口径

- 新 memory 写入会同时出现在 DB ledger 与 workspace projection。
- ledger 使用 append-only 语义，普通修正 / 撤回 / 争议标记不得静默覆盖历史。
- parity check 能报告双写不一致。
- 所有现有 memory recall / search 行为保持兼容。

## 5. P2-M3 与 Phase 3 的前置关系

`P2-M3` 承接 identity、principal、visibility 与 memory policy：

- `principal_id` / binding / verified identity
- per-user continuity
- private / shareable summary / shared-space deny-by-default visibility
- relationship shared-space metadata skeleton
- DB ledger current view / reindex 切换

完整 self-evolution workflow 进入 Phase 3 后，仍不得假设已有通用外部动作面。首轮应收窄为：

- local repo / local git worktree
- explicit human gates
- runner contract / fixture 或最多一个真实 runner
- 不接 Slack、浏览器或外部平台写动作

也就是说，Phase 3 可以重新评估受治理自我演进工作流，但不把 Slack / 群聊 / 外部平台动作作为默认前置。

## 6. Phase 3：Governed Self-Evolution Workflow

完整 `Self-Evolution CLI Demo V1` 迁移到 Phase 3 候选方向。

目标叙述：

> 给 NeoMAGI 一个已经批准范围的 sub-milestone，它能通过受治理的外部 coding agent、beads、git worktree、review loop 和 human gate，推进一次可恢复、可审计、可停手的工程演进闭环。

### In

- beads issue 驱动的 sub-milestone execution ledger。
- fresh branch / worktree 隔离。
- runner contract / fixture 或一个真实 coding agent runner。
- 计划审阅循环，首轮可限制为 1 轮。
- 实现审阅循环，首轮可限制为 1 轮或仅做 dry-run rehearsal。
- scope gate / plan gate / UAT gate。
- closeout artifacts：
  - approved plan
  - review report
  - implementation summary
  - progress ledger update
  - user test guide
  - open issues
- failure / timeout / partial output / rate limit 的可解释停手语义。

### Out

- 不自动 merge 到 `main`。
- 不自动判定 UAT 通过。
- 不接 Slack / 群聊 / 浏览器 / 外部平台写动作，除非未来另行规划 external action surface。
- 不把单次成功 demo 宣称为无边界自治自改能力。

### 验收口径

- 能推进：真实 sub-milestone 从 scope 到 UAT pending 被推进。
- 能收敛：P1/P2 review findings 能通过有限轮次收敛，不能收敛时停手。
- 能留痕：beads、git、docs、progress、approval audit 都有仓库内或 DB 内可追溯证据。
- 能恢复：中断后可从上一个 checkpoint 恢复。
- 能停手：human gate、失败上限、未解决风险都会阻止继续推进。

## 7. 与原提案的关系

原提案保留为方向判断，但排期修正如下：

- 原 `P2-M2b` 后立即做完整 CLI demo：撤回。
- 原首选 scope `procedure_spec governance adapter`：保留，并提升为 `P2-M2c`。
- ADR 0060 的最小 schema / writer 预备：新增为 `P2-M2d`。
- 原 `P2-M5`：移出 P2，迁移为 Phase 3 候选方向。
- Slack / 群聊 / 外部协作表面：暂不规划。

这样保留“受治理自我演进”的长期方向，同时避免在 P2-M2 hotfix 后立刻引入 external CLI、human gate、memory migration 和 worktree orchestration 的混合风险。
