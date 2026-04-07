---
doc_id: 019d68f2-6384-7d56-a65d-eb0d4de3c82a
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-07T19:16:48.900+02:00
---
# P2-M2a 实施计划：Procedure Runtime Core（草案）

- Date: 2026-04-07
- Status: draft
- Scope: 将 `Procedure Runtime` 从设计草案推进为可运行、可持久化、可恢复的最小 deterministic runtime control layer
- Basis:
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/phase2/p2_m2_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m2_architecture.md)
  - [`design_docs/procedure_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/procedure_runtime.md)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)
  - [`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md)
  - [`AGENTS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTS.md)
  - [`decisions/0047-neomagi-multi-agent-single-soul-execution-units.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0047-neomagi-multi-agent-single-soul-execution-units.md)
  - [`decisions/0048-skill-objects-as-runtime-experience-layer.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0048-skill-objects-as-runtime-experience-layer.md)
  - [`decisions/0054-growth-eval-contracts-immutable-and-object-scoped.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0054-growth-eval-contracts-immutable-and-object-scoped.md)
  - [`decisions/0056-wrapper-tool-onboarding-and-runtime-boundary.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0056-wrapper-tool-onboarding-and-runtime-boundary.md)
  - [`decisions/0059-shared-companion-relationship-space-boundary.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0059-shared-companion-relationship-space-boundary.md)
  - [`src/agent/message_flow.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/message_flow.py)
  - [`src/agent/tool_runner.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/tool_runner.py)
  - [`src/tools/base.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/base.py)
  - [`src/tools/registry.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/registry.py)
  - [`src/session/models.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/models.py)
  - [`src/growth/contracts.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/growth/contracts.py)

## Goal

交付一个最小但真实可用的 `Procedure Runtime Core`：

- `ProcedureSpec` 可被代码注册、静态校验并投影给 prompt。
- `ActiveProcedure` 可在 PostgreSQL 中持久化，并以 session 为边界保证单活。
- `ProcedureRuntime` 可执行 `enter -> guard -> execute -> context_patch -> transition -> CAS` 主链路。
- active procedure 可跨 turn / 重启恢复，不依赖模型重新理解历史。
- `AgentLoop` 只负责发现 active procedure 与委托执行，不把状态机逻辑继续堆进 `message_flow`。

一句话边界：

**本轮只把“不能错的状态推进核”下沉到代码层；不建设通用 workflow engine，也不做 P2-M2b 的 multi-agent handoff / publish / merge。**

## Current Baseline

- 当前 `AgentLoop` 以单 agent loop + tool calling 为主，工具执行路径集中在 `message_flow`、`tool_concurrency` 与 `tool_runner`。
- `BaseTool.execute()` 仍返回裸 `dict`，没有正式 `ToolResult.context_patch` typed surface。
- `ToolRegistry` 只按 mode 返回 ambient tools，不支持 procedure state 派生的 action tool schema。
- session 持久化已有 PostgreSQL `sessions` / `messages`，并具备 session claim、lock token、seq fencing 与 compaction state。
- `wrapper_tool` 已在 P2-M1c onboarded；`procedure_spec` 仍是 `GrowthObjectKind` 中的 reserved kind，并只有 eval contract skeleton。
- `Procedure Runtime` 设计已 approved，但当前没有 `src/procedures/` package、active procedure store、runtime executor 或 prompt view。
- Shared Companion 的 relationship/shared-space 方向已由 ADR 0059 固定，但 P2-M2a 只预留 execution context 余量，不实现关系记忆、membership、consent policy 或 shared-space retrieval。

## Core Decision

### D1. 先做 runtime core，不先做 growth governance adapter

`P2-M2a` 第一轮 hard scope 是 code-defined procedure runtime。

- 做：`ProcedureSpec` 类型、registry、validator、runtime、store、AgentLoop 集成与测试。
- 暂不做：把 `procedure_spec` 从 growth policy 的 `reserved` 直接升为 `onboarded`，也不实现 proposal -> evaluate -> apply -> rollback adapter。

理由：

- runtime 的状态机、CAS、prompt view 与 tool action routing 必须先稳定。
- 若同时 onboarding `procedure_spec` governance adapter，会把本轮扩大到 spec 版本账本、apply/rollback、active instance migration 与 no-active-consumers 等治理问题。
- `src/growth/contracts.py` 中的 `procedure_spec_skeleton_v1` 保留为后续 adapter 的验收边界，本轮只确保 runtime 产物不会与该 contract 冲突。

### D2. V1 使用 PostgreSQL active instance store

`ActiveProcedure` 是 runtime state，不放进 `sessions.compaction_metadata` 或内存 map。

V1 新增 `active_procedures` 表，语义上必须支持：

- 同一 `session_id` 至多一个未完成 procedure。
- `revision` optimistic CAS。
- terminal 后释放该 session 的 active uniqueness。
- restart 后可按 `session_id` 重新加载 active procedure。

### D3. V1 使用 virtual procedure action tool schema

OpenAI function calling 只给出 tool name，不给出单独的 `action_id`。

因此 V1 不直接把 `ActionSpec.tool` 暴露为函数名，而是生成 virtual action tool schema：

- function name = `action_id`
- description / parameters = underlying `ActionSpec.tool` 的 schema
- runtime 执行时再从 `action_id -> ActionSpec.tool` 解析到底层 tool

这样可以保留 `ActionSpec` 的状态机语义，避免多个 action 绑定同一个 tool 时产生歧义。

### D4. Procedure action 是并发 barrier

P2-M1 post-works 已支持同 turn 只读工具并发，但 `ProcedureRuntime` 第一轮不参与自动并发。

- procedure action tool call 一律按 barrier 串行执行。
- 同一 turn 内多个 procedure action 需要按原序逐个 CAS。
- 若 revision 已变化，后续 action 返回 retryable conflict，不做部分提交。

理由：

- `ActiveProcedure.revision` 是状态推进边界。
- 并发执行多个 procedure action 会把 CAS retry、tool side effect 与 transcript 顺序耦在一起。
- P2-M2a 的目标是 deterministic core，不是 procedure-level scheduler。

### D5. Execution context 预留但不判定 shared-space policy

V1 继续以 `session_id` 作为 lifecycle 边界，但新增一个轻量 `ProcedureExecutionContext` / `execution_metadata` surface，允许后续显式携带：

- `actor`
- `principal_id`
- `publish_target`
- `visibility_intent`
- future `shared_space_id`

P2-M2a 不解释这些字段，不据此做 memory visibility、membership 或 consent 判定。

## Non-Goals

- 不建设通用 workflow engine。
- 不引入 DAG / DSL / 表达式语言 / 可视化编排。
- 不实现 P2-M2b 的 multi-agent roles、handoff packet、publish / merge。
- 不实现 Shared Companion 的关系记忆、shared-space membership、consent policy 或 shared-space retrieval。
- 不让子 agent 获得独立长期记忆或独立长期身份。
- 不把所有 tool call 强制塞进 procedure；ambient tools 仍按当前 mode gate 可见。
- 不在第一轮实现并发 procedure、procedure-level scheduler 或 long-running background job manager。

## Runtime Contract

### ProcedureSpec

V1 类型放在 `src/procedures/types.py`，对齐 `design_docs/procedure_runtime.md`：

- `ActionSpec(tool: str, to: str, guard: str | None = None)`
- `StateSpec(actions: dict[str, ActionSpec])`
- `ProcedureSpec(id, version, summary, entry_policy, allowed_modes, context_model, initial_state, states, enter_guard, soft_policies)`

静态校验必须 fail-fast：

- `initial_state in states`
- 所有 `ActionSpec.to in states`
- `context_model` 可由 `ProcedureContextRegistry` 解析
- `enter_guard` / `action.guard` 可由 `ProcedureGuardRegistry` 解析
- `ActionSpec.tool` 可由 `ToolRegistry` 解析
- `entry_policy` V1 只接受 `explicit`
- action id 不得与 synthetic runtime tool 名冲突，例如 `procedure_enter`
- action id 必须满足 OpenAI function name 约束，并在 virtual action schema 中保持唯一

### ToolResult

新增 `src/tools/result.py`：

- `ToolResult(ok: bool = True, data: dict = Field(default_factory=dict), context_patch: dict = Field(default_factory=dict))`
- `normalize_tool_result(raw: dict | ToolResult) -> ToolResult`

归一化规则：

- 现有工具无需改签名，仍可返回裸 `dict`。
- 若裸 `dict` 含 `context_patch`，executor 提取到 `ToolResult.context_patch`。
- 若裸 `dict` 没有 `data` 包装，保留原 dict 去除 `context_patch` 后的内容作为 `data`，避免破坏当前工具消费路径。
- `context_patch` 只允许 procedure executor 写回 `ActiveProcedure.context`，非 procedure tool loop 不解释它。

### ActiveProcedure Store

V1 建议表结构：

```text
active_procedures
- instance_id TEXT primary key
- session_id TEXT not null
- spec_id TEXT not null
- spec_version INTEGER not null
- state TEXT not null
- context JSONB not null default '{}'
- execution_metadata JSONB not null default '{}'
- revision INTEGER not null default 0
- created_at TIMESTAMPTZ not null default now()
- updated_at TIMESTAMPTZ not null default now()
- completed_at TIMESTAMPTZ null
```

索引与不变量：

- `UNIQUE (session_id) WHERE completed_at IS NULL`
- CAS update 使用 `WHERE instance_id = :instance_id AND revision = :expected_revision AND completed_at IS NULL`
- terminal state 写入后同步设置 `completed_at = now()`
- `spec_id/spec_version` 创建后不可变

`completed_at` 是 persistence-only 字段，用于 enforce single active invariant；domain projection 仍保持 `ActiveProcedure` 的最小字段。

### Enter Semantics

`enter_procedure(session_id, spec_id, initial_context, execution_metadata, mode)` 固定顺序：

1. 校验当前 mode 满足 `spec.allowed_modes`。
2. 检查该 session 没有未完成 `ActiveProcedure`。
3. 静态解析 spec、context model 与 enter guard。
4. 校验 `initial_context`。
5. 跑 `enter_guard`。
6. 创建 `ActiveProcedure`，`state = spec.initial_state`，`revision = 0`。

进入失败不写入任何 procedure state。

### Apply Semantics

`apply_action(instance_id, action_id, args_json, expected_revision, tool_context, guard_state, mode)` 固定顺序：

1. 读取当前实例与 spec。
2. 校验 `expected_revision == revision`。
3. 校验当前 `state` 允许 `action_id`。
4. 校验底层 tool 存在且当前 mode 允许。
5. 跑现有 mode / risk guard。
6. 跑 procedure action guard。
7. 调用底层 tool executor。
8. 归一化 `dict | ToolResult`。
9. 若 `ok == False`，停留原 state，不写 `context_patch`。
10. 对 `context_patch` 做 top-level shallow merge。
11. 用 `context_model` 校验合并后的 context。
12. 用 CAS 写回 `state = ActionSpec.to`、`context`、`revision + 1`。
13. 若目标 state 无 actions，设置 `completed_at`。

失败语义：

- guard deny：结构化 deny，state/context/revision 不变。
- tool failure：state/context/revision 不变。
- invalid patch：state/context/revision 不变。
- CAS conflict：返回 retryable conflict，不做部分提交。

### Prompt View

`PromptBuilder` 只注入 `ProcedureView`，不是 rule engine。

V1 view 包含：

- `id`
- `version`
- `summary`
- `state`
- `revision`
- `allowed_actions`
- `soft_policies`

不在 prompt 中存：

- 完整 state graph
- guard 代码
- tool schema 全量 JSON
- 可由 `spec + state + context` 推导的 runtime 真相

每次 procedure action 成功迁移后，`RequestState.procedure_view` 与 `system_prompt` 必须刷新，确保下一轮模型看到最新 checkpoint。

### Steering / Resume

P2-M2a 的 steering / resume 只做 checkpoint 级最小闭环：

- Steering：用户下一 turn 的新消息进入同一 session 后，runtime 重新加载 active procedure，prompt view 展示当前 checkpoint；具体是否允许改变方向由当前 state 的 action guard 决定。
- Resume：进程重启或新的请求进入同一 session 后，从 `active_procedures` 加载未完成实例，而不是依赖聊天历史重新推断。

不做：

- 长后台任务暂停 / 恢复。
- 多 agent handoff resume。
- 自动从自由文本生成新 state graph。

## Implementation Shape

新增 package：

- `src/procedures/__init__.py`
- `src/procedures/types.py`
- `src/procedures/registry.py`
- `src/procedures/store.py`
- `src/procedures/runtime.py`
- `src/procedures/view.py`

建议不把 procedure store 塞进 `SessionManager`。`SessionManager` 继续负责 session/message/compaction；procedure lifecycle 由 `ProcedureStore` / `ProcedureRuntime` 管。

`AgentLoop` 新增可选依赖：

- `procedure_runtime: ProcedureRuntime | None`

如果未提供，现有行为完全不变。

## Suggested Implementation Slices

### Slice A. Core Types + Registries

- 新增 `src/tools/result.py`。
- 新增 `src/procedures/types.py`，包含 spec、active instance、guard decision、execution metadata 与 view 类型。
- 新增 `ProcedureSpecRegistry`、`ProcedureContextRegistry`、`ProcedureGuardRegistry`。
- 实现 `validate_procedure_spec(spec, tool_registry, context_registry, guard_registry)`。
- 单元测试覆盖 invalid state、missing context model、missing guard、missing tool、entry_policy 非 explicit。

### Slice B. PostgreSQL Store + Migration

- 新增 Alembic migration `create_active_procedures_table`。
- 新增 `ProcedureStore`，使用 async SQLAlchemy raw `text()` 风格，与 `src/wrappers/store.py` 保持一致。
- Store API 最少包括：
  - `create(active: ActiveProcedure) -> ActiveProcedure`
  - `get_active(session_id: str) -> ActiveProcedure | None`
  - `get(instance_id: str) -> ActiveProcedure | None`
  - `cas_update(instance_id, expected_revision, state, context, completed_at) -> ActiveProcedure | Conflict`
- 测试覆盖 single-active unique、terminal 后可再次 enter、CAS conflict。

### Slice C. Runtime Executor

- 新增 `ProcedureRuntime.enter_procedure()`。
- 新增 `ProcedureRuntime.apply_action()`。
- 实现 guard sync / async 归一化，使用 `inspect.isawaitable()`。
- 实现 top-level shallow merge，不做 deep merge。
- 实现 `ToolResult` 归一化。
- 确保 procedure guard 在现有 mode / risk gate 之后执行，职责不混。

### Slice D. AgentLoop Integration

- `AgentLoop` constructor 接受 `procedure_runtime`。
- `message_flow.RequestState` 增加 active procedure、procedure view、procedure action map。
- `_initialize_request_state()` 加载 active procedure 并构造 `ProcedureView`。
- `_resolve_tools_schema()` 合并 ambient tool schema 与当前 procedure virtual action tool schema，按 function name 去重。
- `tool_concurrency` 将 virtual procedure action 视为 serial barrier。
- `_run_single_tool()` 对 procedure action 分支调用 `ProcedureRuntime.apply_action()`，非 procedure action 继续走现有 `loop._execute_tool()`。
- procedure action 成功后刷新 active procedure、procedure view 与 system prompt。

### Slice E. Prompt View

- `PromptBuilder.build()` 增加可选 `procedure_view` 参数。
- 新增 `_layer_procedure()`，只投影当前 active procedure 摘要和 allowed actions。
- 更新 prompt builder 测试，确保无 active procedure 时输出不变，有 active procedure 时仅增加最小 view。

### Slice F. Minimal Runtime Fixture

第一轮需要一个最小真实 procedure 用于验证 runtime，而不是只测孤立函数。

建议使用测试内 fixture spec：

- `id = "test.checkpoint_task"`
- `initial_state = "draft"`
- action `submit` 调用一个 fake async tool，返回 `context_patch`
- action `confirm` 迁移到 terminal state

如果需要 repo 内置 demo，再单独评估轻量 `relationship_checkin`，但必须标注为 no shared memory fixture，不作为 Shared Companion 产品验收。

### Slice G. Observability + Errors

- 新增结构化日志：
  - `procedure_entered`
  - `procedure_action_started`
  - `procedure_action_denied`
  - `procedure_action_failed`
  - `procedure_action_transitioned`
  - `procedure_cas_conflict`
  - `procedure_completed`
- 错误返回使用结构化 `error_code`：
  - `PROCEDURE_CONFLICT`
  - `PROCEDURE_UNKNOWN`
  - `PROCEDURE_ACTION_DENIED`
  - `PROCEDURE_CAS_CONFLICT`
  - `PROCEDURE_INVALID_PATCH`
  - `PROCEDURE_TOOL_UNAVAILABLE`

## Acceptance

Hard acceptance for P2-M2a first implementation:

- `ProcedureSpec` registry 与静态校验可运行，并 fail-closed。
- PostgreSQL 中同一 session 只能有一个未完成 `ActiveProcedure`。
- `enter_procedure()` 能创建 active instance，并拒绝同 session 重复进入。
- `apply_action()` 能执行底层 tool、提取 `context_patch`、校验 context、迁移 state 并 `revision + 1`。
- guard deny、tool failure、invalid patch、CAS conflict 都不修改 state/context/revision。
- terminal state 设置 `completed_at`，同 session 后续可以进入新 procedure。
- `PromptBuilder` 在 active procedure 存在时注入最小 `ProcedureView`。
- `AgentLoop` 能把当前 state 的 action 暴露为 virtual action tool schema，并把 action tool call 路由到 `ProcedureRuntime`。
- procedure action 不进入 P2-M1 tool parallel group。
- active procedure 可跨请求重新加载，支持 checkpoint-level resume。
- 所有新增 `src/` / `scripts/` 文件满足复杂度硬门禁，不引入大对象神类。

Explicitly not required in first implementation:

- `procedure_spec` growth adapter onboarding。
- multi-agent worker / reviewer runtime。
- handoff packet、publish / merge。
- purposeful compact for task state。
- UI procedure manager。
- Shared Companion product demo。
- procedure-level parallelism。

## Test Plan

受影响测试：

```bash
uv run pytest tests/procedures -q
uv run pytest tests/test_prompt_builder.py tests/test_tool_concurrency.py -q
uv run pytest tests/integration/test_tool_loop_flow.py -q
```

合并前质量门禁：

```bash
just lint
just test
```

若实现触碰前端或 WebSocket 协议，再追加：

```bash
just test-frontend
uv run pytest tests/integration/test_websocket.py -q
```

## Collaboration / Gate Notes

若使用 Agent Teams 推进：

- PM 为 backend 和 tester 分别创建独立 worktree，不共享 working directory。
- Backend 分支建议：`feat/backend-p2-m2a-procedure-runtime-core`。
- Tester review branch 每个 Gate 使用 fresh branch，例如 `feat/tester-p2-m2a-g0`、`feat/tester-p2-m2a-g0-r2`。
- 每个 Gate 必须使用 `GATE_OPEN ... target_commit=<sha>` 放行。
- Backend phase 完成后必须 `commit + push`，回传 `PHASE_COMPLETE role=backend phase=<N> commit=<sha>`。
- Tester 启动前必须 `git fetch --all --prune`、`git merge --ff-only origin/<backend-branch>`、`git rev-parse HEAD`。
- Tester 报告必须 `commit + push`，PM 关闭 Gate 前必须确认报告在主仓库可见。

## Risks

### R1. Procedure action 与 ambient tool 语义混淆

风险：如果直接用底层 tool name 作为 action name，多个 action 绑定同一 tool 时会歧义。

缓解：V1 使用 virtual action tool schema，function name 固定为 `action_id`，runtime 再映射到底层 tool。

### R2. 过早把 procedure 变成 workflow DSL

风险：为了“灵活”，把表达式、条件分支、重试、并发和模板都塞进 `ProcedureSpec`。

缓解：V1 只保留 state、action、guard、transition、context_model。复杂编排推迟，wrapper tool 继续承接 single-turn capability。

### R3. Procedure action 并发导致状态漂移

风险：同一 turn 中多个 procedure action 并发执行，状态迁移顺序与 transcript 顺序不一致。

缓解：procedure action 一律 barrier 串行，CAS conflict fail-fast。

### R4. 把 Shared Companion 提前混进 M2a

风险：借 execution metadata 预留，提前实现 shared memory 或多 principal retrieval。

缓解：P2-M2a 只保留字段余量，不解释 policy；产品级 Shared Companion 验收必须等 P2-M3 identity / membership / visibility policy。

### R5. Runtime 逻辑堆进 AgentLoop

风险：把状态机、guard、CAS 和 context patch 直接写进 `message_flow`，导致 AgentLoop 继续膨胀。

缓解：`message_flow` 只做请求编排和委托；核心逻辑放进 `ProcedureRuntime` / `ProcedureStore`。

## Clean Handoff Boundary

P2-M2a 完成后，P2-M2b 可以在其上继续：

- multi-agent roles
- handoff packet
- bounded context exchange
- publish / merge
- purposeful compact

但 P2-M2a 不应提前实现这些上层语义。

本计划的干净交付物是：

- `src/procedures/` runtime core
- PostgreSQL active procedure store
- `ToolResult.context_patch` surface
- AgentLoop procedure view / virtual action routing
- 最小 checkpoint / resume 验收测试

## Open Questions For Draft Review

- Q1. 第一轮是否需要内置一个用户可触发的 demo procedure，还是先只保留测试 fixture spec？
- Q2. `procedure_enter` 是否应作为 synthetic runtime tool 暴露给模型，还是先通过内部 API / UI 显式入口触发？
- Q3. `procedure_spec` growth adapter 是否作为 P2-M2a 后半段单独计划，还是并入 P2-M2b 前的 follow-up？
