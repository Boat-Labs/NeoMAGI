---
doc_id: 019d6c2c-f782-74fe-84e7-da09b8248d66
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-08T08:19:39+00:00
---
# P2 Self-Evolution CLI Demo（提案）

> 状态：proposed  
> 建议启动时点：`P2-M2b` 验收通过之后，`P2-M3` 开工之前  
> 首个建议承载 scope：`P2-M2a-post` `procedure_spec governance adapter`

## 1. 结论先行

NeoMAGI 的第一个重要“自我演进” demo，不建议放在 `P2-M1` 收口后立刻做，也不建议等到 `P2-M3`。

建议时点：

- **正式 demo：放在 `P2-M2b` 之后**
- **目标 scope：优先选择 `P2-M2a-post procedure_spec governance adapter`**

原因很直接：

- `P2-M1` 已经解决“什么允许成长、如何治理成长”，但还没有稳定的长流程 runtime。
- `P2-M2a` 解决了 procedure checkpoint / resume，但还没有把多执行单元分工、handoff、publish/merge 做实。
- `P2-M2b` 已经补齐了 bounded handoff、worker/reviewer、purposeful compact，刚好对应你现在手工执行的“计划起草 -> 审阅修订 -> 实现 -> 审阅修订 -> 产物收尾”流程。
- `P2-M3` 的身份认证、shared space、memory visibility 对这个 demo 不是前置；等到 `P2-M3` 再做，只会无谓推迟验证。

一句话：

**第一个 demo 不该证明 NeoMAGI “已经会一切”，而应证明它已经能用受治理、可审计、可恢复的方式，驱动一次自己的工程演进闭环。**

## 2. 为什么首个 demo 不放在更早或更晚

### 2.1 不建议放在 `P2-M1` 后

`P2-M1` 具备成长治理、skill object、wrapper tool、builder work memory，但缺少：

- 可恢复的长流程状态机；
- checkpoint 级 steering / resume；
- bounded handoff；
- explicit publish / merge。

如果在这个阶段做 demo，本质上仍然会退化为“长 prompt 编舞 + copy paste 自动化”，不是稳定 runtime。

### 2.2 不建议只等到 `P2-M2a`

`P2-M2a` 已经能支撑单 procedure 状态推进，但你的真实流程不是单执行单元：

- Claude Code 起草计划；
- Codex 审阅计划；
- Claude Code 修订；
- Claude Code 实现；
- Codex 再审阅；
- 最后补日志、进度、用户测试说明。

这天然就是 bounded multi-agent workflow。只做 `P2-M2a` 还不够自然。

### 2.3 不建议等到 `P2-M3`

这个 demo 的核心问题是“自我演进工程闭环”，不是“用户身份 / shared memory / relationship space”。

把它拖到 `P2-M3` 只会把验证目标掺杂进更高风险、更重的身份与隐私议题。

## 3. 推荐的首个 demo 定义

### Demo 名称

`Self-Evolution CLI Demo V1`

### Demo 核心叙述

给 NeoMAGI 一个已经批准范围的 `sub-milestone`，例如 `P2-M2a-post procedure_spec governance adapter`，它应能通过 CLI 驱动外部 coding agents，在受治理边界内完成以下闭环：

1. 建立 beads issue 与执行上下文。
2. 创建隔离的 git branch / worktree。
3. 让 Claude Code CLI 起草实现计划。
4. 让 Codex CLI 审阅实现计划并给出 `P1/P2` 级问题。
5. 驱动 Claude Code CLI 修订，最多循环 3-5 轮，直到没有 `P1/P2`。
6. 让 Claude Code CLI 在隔离 worktree 中实现代码。
7. 让 Codex CLI 审阅实现结果并推动修订，最多循环 3-5 轮，直到没有 `P1/P2`。
8. 生成实现总结、进度更新、用户测试说明书、开放问题文档。
9. 把结果停在“等待人类用户按 guide 做真实交互验收”的状态，而不是自动宣布完成。

这才是 Phase 2 语义下足够像样的“开始自我演进”。

## 4. 为什么首个 scope 选 `P2-M2a-post`

首个 demo 最合适的承载任务，不是直接冲 `P2-M3a`，而是：

- `P2-M2a-post procedure_spec governance adapter`

原因：

- 它正好位于当前主线之后，路径最短。
- 它同时锚定 `P2-M1` 的成长治理和 `P2-M2` 的 procedure runtime，是“系统开始治理自己的流程对象”的第一步。
- 它属于高价值但可控的内生能力增强，比 Web auth、shared-space memory 这类用户面高风险议题更适合作为首个自动化闭环。
- 即使 demo 失败，损失主要集中在工程控制面，不会直接触及用户身份、隐私或外部平台写操作。

一句话：

**首个 demo 先让 NeoMAGI 学会推进“procedure 自己的治理接入”，比直接碰 `P2-M3` 更像正确的自我演进起点。**

## 5. 成功标准

### 5.1 必须证明的事

- NeoMAGI 能把一个 `sub-milestone` 拆成 plan / implement / review / closeout 几个可恢复 checkpoint。
- NeoMAGI 能协调至少两个外部执行单元：
  - `Claude Code CLI` 作为 planner / implementer
  - `Codex CLI` 作为 reviewer
- 每一轮执行都有 beads 状态、git 隔离和仓库内可见产物。
- 失败或中断后，NeoMAGI 能从上一个 checkpoint 恢复，而不是让人类重新 copy paste 全部上下文。
- 最终能产出真实的人类验收材料，而不是只给一段“实现已完成”的摘要。

### 5.2 不要求证明的事

- 不要求自动 merge 到 `main`。
- 不要求自动判定用户验收通过。
- 不要求开放无审批外部写操作。
- 不要求在首个 demo 中解决 `P2-M3` 的 identity / memory visibility 问题。

## 6. Demo 的系统边界

### In

- beads issue 驱动的子里程碑执行。
- git branch / worktree 隔离。
- `Claude Code CLI` 与 `Codex CLI` 的 bounded orchestration。
- 计划审阅循环。
- 实现审阅循环。
- 实现总结 / progress 更新 / user test guide / open issues 产出。
- 人类审批 checkpoint。

### Out

- 自动 merge / 自动发版。
- 自动关闭所有 follow-up issue。
- 让 worker 直接获得通用高风险写工具。
- 把 demo 说成“已经具备完整自治自改能力”。

## 7. 推荐架构

### 7.1 控制平面

由 NeoMAGI primary agent 驱动一个固定 procedure，例如：

- `self_evolve_submilestone_v1`

它负责：

- 维护 procedure state；
- 读取 / 更新 beads issue；
- 管理 worktree 和 branch；
- 控制审批 checkpoint；
- 触发外部 CLI executor；
- 汇总 review finding 与 closeout artifacts。

### 7.2 外部执行单元

首个 demo 不建议让当前 `P2-M2b` 的 worker 直接拿通用写工具做代码改动，因为当前 runtime 明确排除了 worker 对高风险工具的直接访问。

因此 V1 更合理的做法是走 **受治理 wrapper tool**：

- `claude_code_plan_runner`
- `claude_code_impl_runner`
- `codex_review_runner`
- `bd_issue_runner`
- `git_worktree_runner`

也就是说：

- **代码写入由外部 CLI wrapper 完成**
- **procedure runtime 负责 orchestration，不直接把 generic write delegation 放给 worker**

这与 `P2-M1c` 已经建立的 wrapper tool 路径是一致的。

### 7.3 角色映射

- `primary`
  - NeoMAGI orchestrator，自身不写大段实现代码，负责 checkpoint、审批、状态推进、产物归档。
- `worker`
  - 通过 `Claude Code CLI` 承接 plan drafting 和 code implementation。
- `reviewer`
  - 通过 `Codex CLI` 承接 plan review 和 implementation review。
- `human`
  - 只在少数 gate 上介入：scope 确认、计划批准、最终用户测试、是否接受 residual risk。

## 8. 建议 procedure 状态机

### 状态

- `intake`
- `workspace_ready`
- `plan_drafting`
- `plan_reviewing`
- `plan_approved`
- `implementing`
- `implementation_reviewing`
- `closeout_artifacts`
- `human_uat_pending`
- `done`
- `aborted`

### 核心动作

- `claim_issue`
- `prepare_worktree`
- `draft_plan_with_claude`
- `review_plan_with_codex`
- `revise_plan_with_claude`
- `approve_plan`
- `implement_with_claude`
- `review_impl_with_codex`
- `revise_impl_with_claude`
- `write_closeout_artifacts`
- `request_human_uat`
- `finish_after_human_uat`

### 循环约束

- `plan review` 最多 5 轮。
- `implementation review` 最多 5 轮。
- 若仍存在未消除的 `P1/P2`，不得自动进入下一个 checkpoint。
- 若超过最大轮次，必须进入 `blocked_for_human_decision` 风格的等待语义，而不是假装通过。

## 9. beads 任务模型

V1 不要把 beads 只当成“最后记一下完成了什么”，而要把它作为运行中的 task ledger。

建议结构：

- 1 个父 issue：对应本次 `sub-milestone`
- 3~5 个子 issue：
  - `plan`
  - `implementation`
  - `review`
  - `closeout`
  - 必要时 `user-test-doc`
- 新发现问题一律通过 `discovered-from:<parent-id>` 建立 follow-up

约束：

- 不再使用 markdown TODO 追踪流程。
- 每个 checkpoint 完成后都要同步 beads 状态。
- 人类验收前，父 issue 不关闭。

## 10. git branch / worktree 策略

首个 demo 必须把隔离做实，否则只是“会调用 CLI”，还不是可信的自我演进流程。

### 最小要求

- orchestration 控制面留在当前主 workspace。
- 实现工作在 fresh implementation worktree 中完成。
- review 至少基于明确的 branch snapshot，而不是对未隔离的脏目录直接点评。

### 推荐做法

- implementation branch:
  - `feat/auto-<submilestone>-impl`
- implementation worktree:
  - `../wt/<submilestone>-impl`
- review branch:
  - `feat/review-<submilestone>-rN`
- review worktree:
  - `../wt/<submilestone>-review-rN`

说明：

- 如果 review 只做只读审阅，可先不强制每轮新 review worktree。
- 如果 review 需要落审阅产物或修订建议，仍建议采用 fresh review branch/worktree，保持“one review round, one snapshot”。

## 11. 必须产出的仓库内证据

对于每个自动推进的 `sub-milestone`，至少要有：

- `dev_docs/plans/phase2/...`
  - 最终批准版实现计划
- `dev_docs/reviews/phase2/...`
  - Codex review 结论与问题清单
- `dev_docs/logs/phase2/...`
  - Claude Code 起草的实现总结
- `dev_docs/progress/project_progress.md`
  - append-only milestone 进度记录
- `design_docs/phase2/<milestone>_user_test_guide.md`
  - 人类交互层测试说明
- `design_docs/phase2/<milestone>_open_issues.md`
  - 人类测试中发现的深层设计缺口

评价原则：

**没有仓库内可见产物，就不算 demo 成功。**

## 12. 人类介入点

V1 必须保留三个硬 gate：

1. `scope gate`
   - 人类确认本次 `sub-milestone` 范围与非目标
2. `plan gate`
   - 只有 plan review 没有 `P1/P2` 后，人类才批准进入实现
3. `uat gate`
   - 只有人类按 user test guide 跑完真实交互验收后，才可视为 milestone accepted

这三个 gate 不能省。否则“自我演进 demo”会滑向“模型自动写代码 demo”。

## 13. 与当前已知问题的关系

当前 beads ready 中的 issue：

- `NeoMAGI-9aa` `Define hard arbitration between skill reuse and memory recall`

它说明 `P2-M1` 里 skill / memory 复用边界仍有串线风险。

对本 demo 的影响：

- 如果首个 demo 要把“skill reuse 导致的改进行为”作为核心卖点，`NeoMAGI-9aa` 会成为阻塞项。
- 如果首个 demo 聚焦在 **procedure orchestration + external CLI automation + audit chain**，则它不是硬 blocker，但应在风险里明确标注，避免把 improvement attribution 说得过头。

因此，V1 建议：

- **不要把“系统因历史记忆自动变聪明”作为首个 demo 主卖点**
- **把卖点收敛为“系统能受治理地推进自己的工程演进闭环”**

## 14. 推荐实施切片

### Slice 0：External CLI Wrapper Tools

- 为 Claude Code CLI / Codex CLI / bd / git worktree 提供 typed wrapper surface
- 明确输入输出 contract
- 禁止退回自由 `bash` 编舞

### Slice 1：ProcedureSpec + Prompt View

- 落 `self_evolve_submilestone_v1`
- 固定 checkpoint、gate、round limit、artifact path contract

### Slice 2：Artifact Writers

- 自动写入：
  - plan draft / approved plan
  - review docs
  - implementation summary
  - progress ledger
  - user test guide
  - open issues

### Slice 3：Human Gates + Resume

- 支持：
  - 中断恢复
  - 人类批准后继续
  - round counter 与 blocked state

### Slice 4：首个真实跑通 case

- 推荐 case：`P2-M2a-post procedure_spec governance adapter`

## 15. 最终验收口径

首个 demo 达标，不是因为它“自动 merge 了一个 PR”，而是因为它同时满足下面四件事：

- **能推进**：确实跑通一个真实 `sub-milestone`
- **能收敛**：review 循环能把 `P1/P2` 压下去
- **能留痕**：beads、git、docs、progress 都形成证据链
- **能停手**：在人类 gate、失败上限、未解决风险面前会停，而不是装作完成

如果这四件事成立，就可以说：

**NeoMAGI 在 `P2-M2b` 之后，已经具备开始“受治理自我演进”的第一个像样 demo。**
