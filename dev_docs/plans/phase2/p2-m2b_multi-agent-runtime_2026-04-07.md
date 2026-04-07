---
doc_id: 019d699a-970d-7e46-9abe-ff219a9371a6
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-07T22:20:32+02:00
---
# P2-M2b 实施计划：Multi-Agent Runtime

- Date: 2026-04-07
- Status: approved
- Scope: 在 P2-M2a Procedure Runtime Core 基础上，交付最小可用的 execution-oriented multi-agent runtime
- Basis:
  - [`design_docs/phase2/p2_m2_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m2_architecture.md)
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/procedure_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/procedure_runtime.md)
  - [`decisions/0047-neomagi-multi-agent-single-soul-execution-units.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0047-neomagi-multi-agent-single-soul-execution-units.md)
  - [`decisions/0059-shared-companion-relationship-space-boundary.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0059-shared-companion-relationship-space-boundary.md)
  - [`dev_docs/plans/phase2/p2-m2a_procedure-runtime-core_2026-04-07.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m2a_procedure-runtime-core_2026-04-07.md)
  - [`AGENTTEAMS.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/AGENTTEAMS.md)
  - [`CLAUDE.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/CLAUDE.md)
  - [`src/procedures/types.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/procedures/types.py)
  - [`src/procedures/runtime.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/procedures/runtime.py)
  - [`src/procedures/store.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/procedures/store.py)
  - [`src/agent/agent.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/agent.py)
  - [`src/agent/message_flow.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/message_flow.py)
  - [`src/agent/tool_concurrency.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/tool_concurrency.py)
  - [`src/agent/procedure_bridge.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/procedure_bridge.py)
  - [`src/agent/compaction.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/compaction.py)
  - [`src/tools/base.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/base.py)
  - [`src/tools/context.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/context.py)
  - [`src/tools/registry.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/tools/registry.py)
  - [`src/agent/compaction_flow.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/agent/compaction_flow.py)

## Goal

在 P2-M2a Procedure Runtime Core 基础上交付最小可用的 multi-agent runtime：

- 定义 agent role 类型（primary / worker / reviewer）与 role contract。
- 实现 bounded handoff packet 作为 agent 间唯一上下文交换面。
- 实现 lightweight worker executor（不是完整 AgentLoop），执行受限局部任务。
- 实现 explicit publish / merge 协议：worker 结果默认 private，只有 primary 显式 publish 才进入用户级连续性。
- 将 purposeful compact 集成到 procedure context，使 compaction 在 active procedure 存在时保留结构化 task state。

一句话边界：

**本轮把 multi-agent 的角色分工、bounded handoff 与 publish/merge 在 procedure runtime 之上实现为最小闭环；不引入独立 orchestrator，不把 worker 变成完整 AgentLoop，不触及 Shared Companion 的关系记忆或 membership。**

## Current Baseline（P2-M2a 交付后）

- `ProcedureSpec` / `ActiveProcedure` / `ProcedureRuntime` / `ProcedureStore` 已可运行。
- PostgreSQL `active_procedures` 表 + CAS + single-active-per-session 约束已稳定。
- `ToolResult.context_patch` 是 procedure context 唯一写入面。
- `ProcedureExecutionMetadata` 已声明 `actor`、`principal_id`、`publish_target`、`visibility_intent`、`shared_space_id`，但 V1 不解释这些字段。
- `AgentLoop` 通过 `procedure_bridge` 委托 procedure action 执行，不内联状态机。
- Virtual action tool schema 将 procedure action 暴露为 OpenAI function calling surface。
- Procedure action 一律 barrier 串行（D4）。
- Checkpoint-level steering / resume 可用。
- 现有 `CompactionEngine` 生成 rolling summary，尚无 task-state-oriented compact。
- 当前 `ToolContext` 只包含 `scope_key` / `session_id`，不携带 actor / role 信息。

## Core Decisions

### D1. Worker 是轻量 executor，不是完整 AgentLoop

Worker 不需要：session persistence、memory access、compaction、自己的 procedure runtime。

Worker 只需要：handoff packet 作为上下文、filtered tool access、bounded model iterations、structured result return。

理由：

- ADR 0047 明确子 agent 不拥有独立长期记忆与独立长期身份。
- Worker 是 task-local executor，生命周期不超过一次 delegation action。
- 避免管理多个 session / message history / memory scope 的复杂度。
- Worker 的多 turn 对话（model call → tool → model call → ...）只存在于内存，不落库。

### D2. Delegation 是一个 procedure action，不是独立编排层

从 ProcedureRuntime 视角，delegation 只是一个 action，其底层 tool 是 `DelegationTool`。

```text
Primary AgentLoop
  → procedure action "delegate_to_worker"
    → DelegationTool.execute(handoff_spec, worker_role, constraints)
      → WorkerExecutor(handoff_packet, model_client, filtered_tools)
        → model calls + tool executions (bounded)
        → returns WorkerResult
      → ToolResult(data=worker_dump, context_patch={"_pending_handoffs": {id: dump}})
    → ProcedureRuntime: merge staging into context, transition state
  → procedure action "request_review"
    → ReviewTool → ReviewerExecutor → ReviewResult
    → staging: _review_results[handoff_id]
  → procedure action "publish_result"
    → PublishTool: read staging → merge to visible context + MemoryFlushCandidate
```

理由：

- 复用现有 CAS / guard / context_patch / transition 机制，不引入独立 orchestrator。
- ProcedureRuntime 仍是唯一状态推进核。
- 从 state machine 视角，delegation 与其他 action 无本质区别。

### D3. Handoff packet 是唯一 agent 间上下文交换面

Agent 间不共享：raw conversation history、private memory、full procedure state graph、ambient tool context。

Handoff packet 有严格 schema、bounded size、显式声明内容。Packet 结构预留 `source_actor`、`execution_metadata`，与 M2a 的 `ProcedureExecutionMetadata` 对齐。

理由：

- P2-M2 架构文档 §4.3 明确要求"agent 间默认只交换 bounded packet"。
- ADR 0047：子 agent 使用 task-local 上下文，不复制主 agent 完整上下文。

### D4. Publish 是显式的，由 primary 控制

Worker result 默认 private，不进入 user-level continuity。

只有 primary 通过 explicit publish action 才能把结果写入 memory / session 连续性。Unpublished worker result 在 procedure 完成后 discard。

理由：

- P2-M2 架构文档 §4.3："没有 publish / merge 的结果，不进入用户级连续性。"
- 防止 worker 的中间产物污染主上下文。
- primary 保持最终裁决权（ADR 0047）。

### D5. Purposeful compact 保留 task state，不只是 chat summary

Active procedure 存在时，compaction 优先提取结构化 task state：objectives、TODOs、blockers、last_valid_result、pending_approvals。

Task state 可直接用于 handoff packet 生成，也服务 checkpoint-level resume。

理由：

- P2-M2 架构文档 §4.5 明确 compact 目标从"摘要聊天"升级为"保留任务状态"。
- 该层同时服务长任务恢复与 multi-agent handoff。

### D6. V1 delegation 是同步阻塞的

V1 的 delegation action 在 primary agent loop 内同步执行，worker 完成前 primary 被阻塞。

理由：

- 异步 delegation 引入 polling、超时、partial-result 与 steering-during-delegation 等复杂度。
- V1 先验证 handoff / execute / merge 的正确性，后续版本再支持 async delegation + checkpoint。
- 与 M2a D4（procedure action barrier 串行）保持一致。

### D7. Procedure-only tools 使用 `is_procedure_only` 显式标记 + 对应 bypass

`DelegationTool`、`PublishTool`、`ReviewTool` 必须在 `ToolRegistry` 中注册（M2a 静态校验要求 `ActionSpec.tool` 可解析），但不得暴露为 ambient tool。

**显式标记**：`BaseTool` 新增 property：

```python
@property
def is_procedure_only(self) -> bool:
    """Whether this tool is exclusively for procedure actions.

    Fail-closed default: False. Procedure-only tools must explicitly
    override to return True.
    """
    return False
```

DelegationTool / ReviewTool / PublishTool override 此 property 返回 `True`，同时声明 `allowed_modes = frozenset()`。

**ambient 隐藏**：`allowed_modes = frozenset()` 使这些 tool 不出现在 `ToolRegistry.list_tools(mode)` / `get_tools_schema(mode)` 中。

**procedure 执行 bypass**：`ProcedureRuntime.apply_action()` 步骤 5（`runtime.py:225`）的 mode check 修改为：

- **当且仅当** `tool.is_procedure_only` 为 `True` 时，跳过 ambient mode check。
- 对 `is_procedure_only == False` 的普通 tool（如 `write_file`），mode check 正常执行，即使它出现在 procedure action 声明中。
- Risk guard（步骤 6）对所有 tool 一律执行，不受 bypass 影响。

不使用 `allowed_modes == frozenset()` 作为 bypass 判据，因为 `BaseTool.allowed_modes` 默认就是空集（fail-closed），这会让遗漏 mode 声明的普通 tool 意外获得 bypass。`is_procedure_only` 是显式 opt-in，不与 fail-closed 默认值混淆。

**worker schema 排除**：`WorkerExecutor` 在构造 tool schema 时必须排除 `is_procedure_only == True` 的 tool，即使它们的 `group` 在 `role_spec.allowed_tool_groups` 中。

理由：

- `is_procedure_only` 是显式 opt-in，与 `allowed_modes` 的 fail-closed 默认值无歧义。
- 一个忘记声明 modes 的普通 tool 不会因为 `allowed_modes == frozenset()` 而意外获得 procedure bypass。
- Worker executor schema 与 ambient schema 一致排除 procedure-only tools。

### D8. Procedure-only tools 通过 `ProcedureActionDeps` 接收上下文

Procedure-only tools（DelegationTool / ReviewTool / PublishTool）需要读取 active procedure context（staging area）、model_client（创建 worker/reviewer executor）和 ProcedureSpec。但当前 `BaseTool.execute(args, tool_context)` 的 `ToolContext` 只携带 `scope_key` / `session_id`。

机制：新增 `src/procedures/deps.py`，包含 frozen dataclass `ProcedureActionDeps`：

```python
# src/procedures/deps.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.procedures.types import ActiveProcedure, ProcedureSpec

@dataclass(frozen=True)
class ProcedureActionDeps:
    active_procedure: ActiveProcedure   # 当前实例 (state + context snapshot)
    spec: ProcedureSpec                 # read-only spec
    model_client: Any                   # ModelClient for worker/reviewer
    model: str                          # model name for worker/reviewer
```

ToolContext 新增一个可选字段：

```python
procedure_deps: ProcedureActionDeps | None = None
```

注入点：`tool_concurrency._run_procedure_action()`（`tool_concurrency.py:220`）在构造 `ToolContext` 时：

1. 从 `loop._model_client`、`loop._model` 和 `state.active_procedure` / resolved spec 构造 `ProcedureActionDeps`。
2. **同时设置 `ToolContext.actor = AgentRole.primary`**。V1 中 primary AgentLoop 是唯一调用 `_run_procedure_action()` 的路径，因此 actor 固定为 primary。Worker/reviewer executor 在自己的执行循环中直接调用 tool，不经过 ProcedureRuntime，不涉及此路径。
3. `ProcedureExecutionMetadata.actor`（`str | None`）与 `ToolContext.actor`（`AgentRole | None`）的映射：enter_procedure 时写入 `execution_metadata.actor = actor.value`（string），guard 读 `ToolContext.actor`（enum）。两者通过 `AgentRole(str_value)` 互转。

`ProcedureRuntime.apply_action()` 不需要改变——它已在步骤 8 把 ToolContext 原样传给 `tool.execute()`。Role guard 从 `tool_context.actor` 读取当前角色。

Procedure-only tools 从 `context.procedure_deps` 读取所需信息：

- `DelegationTool`：从 `deps.active_procedure.context` 构造 HandoffPacket，用 `deps.model_client`/`deps.model` 创建 WorkerExecutor。
- `ReviewTool`：从 `deps.active_procedure.context["_pending_handoffs"]` 读取 worker result，用 `deps.model_client`/`deps.model` 创建 ReviewerExecutor。
- `PublishTool`：从 `deps.active_procedure.context["_pending_handoffs"]` 读取 raw result，执行 merge。

非 procedure tools 的 `context.procedure_deps` 为 `None`，行为不变。

理由：

- ToolContext 只增加一个可选 field，不为每个依赖项单独扩展字段。
- 注入点在 `_run_procedure_action()`，AgentLoop 已有所有必要引用。
- 解决 gateway wiring 冲突：executors 不需要在 gateway 层构建，而是由 tool 在 execute() 时按需创建。
- ProcedureRuntime 保持无 provider 依赖。

### D9. Publish flush 走 result 信号，不走直接 MemoryWriter 调用

PublishTool 生成 `MemoryFlushCandidate` 但不直接调用 `MemoryWriter`。持久化责任保留在 AgentLoop 层。

机制：

1. `PublishTool.execute()` 返回 `ToolResult(data={"_publish_flush_texts": [text1, text2, ...]}, ...)`。
2. `ProcedureRuntime.apply_action()` 在成功时用 `**result.data` 展开到扁平返回 dict（`runtime.py:365`），因此 `_publish_flush_texts` 成为顶层 key。
3. `tool_concurrency._run_procedure_action()` 在 action 成功后检查 `result.get("_publish_flush_texts")`。
4. 若存在，构造 `MemoryFlushCandidate` 对象，显式设置以下字段：
   - `source_session_id = state.session_id`
   - `candidate_text = flush_text`（每条文本一个 candidate）
   - `constraint_tags = ["published_result"]`
   - `confidence = 1.0`（explicit publish 是用户确认的最高 confidence，超过 `flush_min_confidence` 默认阈值 0.5）
5. 调用 `loop._persist_flush_candidates(candidates, session_id, scope_key=scope_key)`。
6. 复用现有 `compaction_flow._persist_flush_candidates()` → `MemoryWriter.process_flush_candidates()` 管线。

注意：`confidence = 1.0` 确保 publish candidate 不会被 `MemoryWriter.process_flush_candidates(min_confidence=0.5)` 过滤掉。默认 `confidence = 0.0` 的 candidate 会被静默丢弃。

理由：

- 不给 tool 直接的 MemoryWriter 引用；持久化路径统一在 AgentLoop/compaction_flow。
- 复用现有管线，不需要新的持久化路径。
- `_publish_flush_texts` 是 serializable string list，保持 result dict 的可序列化性。

## Non-Goals

- 不实现 worker 的 session persistence 或 long-term memory。
- 不实现 parallel worker execution（V1 只支持串行 delegation）。
- 不实现 worker autonomy（worker 不能自行 delegate 或 publish）。
- 不实现 dynamic role negotiation（V1 角色固定为 primary / worker / reviewer）。
- 不引入 multi-agent scheduler / queue / priority system。
- 不实现 Shared Companion 的 relationship memory、shared-space membership 或 consent policy。
- 不建设通用 workflow engine 或 DAG。
- 不实现 procedure_spec growth adapter onboarding（留给 P2-M2a-post）。
- 不实现 cross-procedure delegation（worker 不能启动自己的 procedure）。
- 不实现 delegation 期间的用户 steering（V1 需等 worker 完成后在 primary 侧 steering）。

## Runtime Contract

### AgentRole

```python
class AgentRole(StrEnum):
    primary = "primary"
    worker = "worker"
    reviewer = "reviewer"
```

V1 语义：

- `primary`：直接代表用户，持有最终对齐与决策权，可执行所有 action，可 publish。
- `worker`：执行局部任务，使用 task-local 上下文，不能 publish 或 delegate。
- `reviewer`：校验、对比、审阅与风险检查，不能写副作用，不能 publish 或 delegate。

### RoleSpec

```python
class RoleSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: AgentRole
    allowed_tool_groups: frozenset[ToolGroup]
    can_publish: bool = False
    can_delegate: bool = False
    max_iterations: int = 5
```

V1 默认预设：

- `primary`：全部 tool group、`can_publish=True`、`can_delegate=True`、`max_iterations=MAX_TOOL_ITERATIONS`。
- `worker`：`code` + `world` tool group、`can_publish=False`、`can_delegate=False`、`max_iterations=5`。
- `reviewer`：只有 read-only tools、`can_publish=False`、`can_delegate=False`、`max_iterations=3`。

### HandoffPacket

Frozen model，bounded，schema-validated：

```python
class HandoffPacket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    handoff_id: str                          # UUIDv7, links audit trail
    source_actor: AgentRole                  # who delegated
    target_role: AgentRole                   # who should execute
    task_brief: str                          # what the worker should do
    constraints: tuple[str, ...] = ()        # what the worker must NOT do
    current_state: dict[str, Any] = Field(   # relevant procedure context subset
        default_factory=dict
    )
    evidence: tuple[str, ...] = ()           # facts from primary's context
    open_questions: tuple[str, ...] = ()     # what the worker should figure out
    execution_metadata: ProcedureExecutionMetadata = Field(
        default_factory=ProcedureExecutionMetadata
    )
```

约束：

- `extra="forbid"` 拒绝未知字段。
- `task_brief` 不得为空。
- `current_state` 由 `HandoffPacketBuilder` 从 procedure context 中提取，不是全量 dump。
- V1 总 packet 序列化上限 32 KB（`json.dumps(packet.model_dump())` UTF-8 字节数）；超限 `HandoffPacketBuilder` fail-fast 而不是静默截断。
- 单条 `task_brief` 限 4000 字符；`constraints` / `evidence` / `open_questions` 各条目限 500 字符。
- Validator 在 `HandoffPacketBuilder.build()` 出口处统一执行，不在 Pydantic model 内联（避免 model_validator 中做 JSON 序列化）。

### WorkerResult

```python
class WorkerResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    ok: bool
    result: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()           # what the worker found/verified
    open_questions: tuple[str, ...] = ()     # unresolved issues
    iterations_used: int = 0
    error_code: str = ""
    error_detail: str = ""
```

Worker executor 返回 `WorkerResult`；DelegationTool 将其映射为 `ToolResult`。

**关键语义：delegation 不直接 merge worker result 到 visible context，只写入 private staging area。**

- `ToolResult.data = worker_result.model_dump()`（返回给模型阅读）
- `ToolResult.context_patch` 由 DelegationTool 执行 read-modify-write：
  1. 从 `context.procedure_deps.active_procedure.context` 读取当前 `_pending_handoffs` dict（默认 `{}`）
  2. 在其中添加 `{handoff_id: worker_result.model_dump()}`
  3. 返回 `{"_pending_handoffs": updated_full_dict}`
  - 因为 ProcedureRuntime 只做 top-level shallow merge，必须返回完整的 `_pending_handoffs` dict，不能只返回增量
  - ReviewTool 对 `_review_results` 的写入同理
- `ToolResult.ok = worker_result.ok`

Publish action 从 `context["_pending_handoffs"][handoff_id]` 读取 raw worker result，执行真正的 merge 与 memory flush。Delegation 本身不触碰 visible context keys。

这保证了 D4（publish 是唯一进入 user-level continuity 的路径）：
- Worker result 在 delegation 后暂存于 procedure context 的 private namespace
- Primary 可在 publish 前审阅、review、甚至 discard
- 只有 PublishTool 才把指定 keys 从 staging area 提升到 visible context + memory flush

### ReviewResult

```python
class ReviewResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    approved: bool
    concerns: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
```

### ToolContext Extension

```python
# src/tools/context.py — 使用 TYPE_CHECKING guard 引用 procedures 类型
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.procedures.deps import ProcedureActionDeps
    from src.procedures.roles import AgentRole

@dataclass(frozen=True)
class ToolContext:
    scope_key: str = "main"
    session_id: str = "main"
    actor: AgentRole | None = None           # D8: 由 _run_procedure_action() 注入
    handoff_id: str | None = None
    procedure_deps: ProcedureActionDeps | None = None  # D8
```

注入点：`tool_concurrency._run_procedure_action()` 从 `loop._model_client`、`loop._model`、`state.active_procedure` 和 resolved spec 构造 `ProcedureActionDeps`，放入 ToolContext。

V1 只在 procedure action 路径中填充这些字段；ambient tool 路径全部为 `None`，行为不变。

### TaskStateSnapshot（Purposeful Compact）

```python
class TaskStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    objectives: tuple[str, ...] = ()
    todos: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    last_valid_result: dict[str, Any] = Field(default_factory=dict)
    pending_approvals: tuple[str, ...] = ()
```

Compaction 在 active procedure 存在时，在现有 rolling summary 之外追加 task state extraction。

### WorkerExecutor

```python
class WorkerExecutor:
    def __init__(
        self,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        role_spec: RoleSpec,
        model: str = "gpt-4o-mini",
    ) -> None: ...

    async def execute(self, packet: HandoffPacket) -> WorkerResult: ...
```

`model_client` 和 `model` 由 DelegationTool 在 execute() 时从 `context.procedure_deps` 获取，按需创建 WorkerExecutor 实例（D8）。V1 使用与当前 AgentLoop 相同的 model_client / model。后续版本可支持 per-role model selector（例如 worker 使用更便宜的 model）。

执行流程：

1. 从 `packet` 构造 bounded system prompt（task brief + constraints + evidence + current state）。
2. 初始 user message = `packet.task_brief`。
3. 循环调用 model，处理 tool calls，直到 model 给出 final answer 或达到 `role_spec.max_iterations`。
4. Tool calls 只允许 `role_spec.allowed_tool_groups` 中的 tools。
5. 所有对话只在内存中，不持久化。
6. 若达到 iteration 上限仍未完成，`WorkerResult.ok = False`、`error_code = "WORKER_ITERATION_LIMIT"`。

### ReviewerExecutor

```python
class ReviewerExecutor:
    def __init__(
        self,
        model_client: ModelClient,
        model: str = "gpt-4o-mini",
    ) -> None: ...

    async def review(
        self,
        work_product: dict[str, Any],
        criteria: tuple[str, ...],
        evidence: tuple[str, ...] = (),
    ) -> ReviewResult: ...
```

`model_client` 和 `model` 同理，由 ReviewTool 在 execute() 时从 `context.procedure_deps` 按需注入（D8）。

执行流程：

1. 构造 review prompt（work product + criteria + evidence）。
2. 单次 model call，不执行 tool。
3. 解析 model 输出为 `ReviewResult`。
4. 若解析失败，`ReviewResult.approved = False`、`concerns = ("review_parse_failure",)`。

### Publish / Merge

V1 的 data flow：

```text
Delegation → _pending_handoffs[handoff_id] (staging)
Review     → _review_results[handoff_id] (staging)
Publish    → read staging → merge to visible context + MemoryFlushCandidate
```

V1 的 publish 语义：

- Publish action 是一个 procedure action，guard 检查 `actor == primary`。
- PublishTool 从 `_pending_handoffs[handoff_id]` 读取 raw worker result，按 `merge_keys` 提取到 visible context keys。
- 同时从 staging area 移除已 publish 的 handoff_id。
- 生成一条 `MemoryFlushCandidate`（best-effort），使 published result 有机会进入长期记忆。
- V1 只支持 `publish_target = "session_continuity"`，不支持 shared space。

Private staging convention：

- `_pending_handoffs: dict[str, dict]`：handoff_id → raw WorkerResult dump。
- `_review_results: dict[str, dict]`：handoff_id → raw ReviewResult dump。
- 以 `_` 前缀标记为 runtime-internal keys，prompt view 不投影这些 keys。
- Procedure 完成时，未 publish 的 staging data 随 procedure context 归档，不进入 user-level 连续性。

## Implementation Slices

### Slice A. Role Types + ToolContext Extension + ProcedureActionDeps + BaseTool marker

- 新增 `src/procedures/roles.py`：`AgentRole`、`RoleSpec`、`DEFAULT_ROLE_SPECS`。
- 新增 `src/procedures/deps.py`：`ProcedureActionDeps` frozen dataclass。此文件使用 `from __future__ import annotations` + `TYPE_CHECKING` guard 引用 `ActiveProcedure` / `ProcedureSpec`，避免与 `tools/context.py` 循环导入（当前 import graph：`tools/context` ← `procedures/types` ← `tools/base`；`deps.py` 只在类型检查时引用 `procedures/types`，运行时无环）。`tools/context.py` 同理用 `TYPE_CHECKING` guard 引用 `ProcedureActionDeps` 和 `AgentRole`。
- 扩展 `src/tools/context.py` `ToolContext`：新增 `actor: AgentRole | None = None`、`handoff_id: str | None = None`、`procedure_deps: ProcedureActionDeps | None = None`。
- 扩展 `src/tools/base.py` `BaseTool`：新增 `is_procedure_only` property（默认 `False`），DelegationTool / ReviewTool / PublishTool 在各自实现中 override 返回 `True`（D7）。
- 扩展 `src/procedures/types.py`：`ProcedureExecutionMetadata.actor` 的语义文档更新（V1 开始使用，string 值与 `AgentRole` enum 通过 `.value` 互转）。
- 单元测试覆盖 role types、role spec defaults、ToolContext 新字段的 backward compatibility（所有新字段为 `None` 时无行为变化）、ProcedureActionDeps 构造与 frozen 约束、`BaseTool.is_procedure_only` 默认 False。
- **Import smoke test**：验证 `from src.procedures.deps import ProcedureActionDeps` 和 `from src.tools.context import ToolContext` 可在同一进程中无循环导入地共存。

### Slice B. Handoff Packet + Worker Result + Review Result

- 新增 `src/procedures/handoff.py`：`HandoffPacket`、`WorkerResult`、`ReviewResult`、`TaskStateSnapshot`。
- 新增 `HandoffPacketBuilder`：从 `ActiveProcedure.context` + `ProcedureSpec` 提取 bounded packet。
  - Builder 接受 `include_keys: tuple[str, ...]` 配置，只提取指定 context keys 到 `current_state`。
  - Builder 自动填充 `handoff_id`（UUIDv7）、`source_actor`、`execution_metadata`。
- Validation：`task_brief` 非空、constraints / evidence / open_questions 条目长度限制。
- 单元测试覆盖 packet 构造、unknown field rejection、empty brief rejection。

### Slice C. Worker Executor

- 新增 `src/procedures/worker.py`：`WorkerExecutor`。
- 执行循环：prompt 构造 → model call → tool routing → result collection → bounded iterations。
- Tool 过滤（双重）：
  1. 只允许 `role_spec.allowed_tool_groups` 中的 tools。
  2. **排除 `tool.is_procedure_only == True` 的 tools**，即使其 group 在 allowed list 中（D7）。
  - 拒绝的 tool call 返回结构化错误。
- 不持久化任何对话。
- 错误处理：model timeout → `WorkerResult(ok=False, error_code="WORKER_MODEL_TIMEOUT")`；tool failure → 记录到 evidence、继续循环。
- 单元测试使用 mock model_client + fake tools，覆盖正常完成、iteration limit、tool rejection、**procedure-only tool 被 worker 拒绝**。

### Slice D. Reviewer Executor + ReviewTool

- 新增 `src/procedures/reviewer.py`：`ReviewerExecutor` + `ReviewTool`（继承 `BaseTool`）。
- `ReviewerExecutor`：单次 model call，结构化 prompt → JSON 解析 → `ReviewResult`。解析失败时 fail-closed：`approved=False`。model_client 和 model 在 execute() 时从 `context.procedure_deps` 获取，按需创建 executor 实例（D8）。
- `ReviewTool`（继承 `BaseTool`）：作为 procedure action 的底层 tool wrapper。
  - `ReviewTool.execute()`：
    1. 从 `context.procedure_deps.active_procedure.context["_pending_handoffs"][handoff_id]` 读取 worker result。
    2. 从 `context.procedure_deps.model_client` / `.model` 创建 `ReviewerExecutor`。
    3. 调用 `ReviewerExecutor.review()`。
    4. read-modify-write `_review_results`：读取当前 dict，添加 `{handoff_id: review_result.model_dump()}`，返回完整 dict 作为 `context_patch`。
  - `allowed_modes = frozenset()`（procedure-only tool，D7）。
- 单元测试覆盖 approve、reject、parse failure、缺失 handoff_id 时的 error handling、`procedure_deps is None` 时 fail-closed。

### Slice E. Delegation Tool + Role-Aware Guards + Mode Bypass

- 新增 `src/procedures/delegation.py`：`DelegationTool`（继承 `BaseTool`，`allowed_modes = frozenset()`）。
  - `DelegationTool.execute()`：
    1. 从 `context.procedure_deps` 读取 `active_procedure.context`、`spec`、`model_client`、`model`。
    2. 用 `HandoffPacketBuilder` 从 procedure context 构造 `HandoffPacket`。
    3. 按需创建 `WorkerExecutor(model_client, tool_registry, role_spec, model)`。
    4. 调用 `worker.execute(packet) -> WorkerResult`。
    5. Read-modify-write `_pending_handoffs`：读取当前 dict，添加新 handoff，返回完整 dict 作为 `context_patch`。
  - `procedure_deps is None` → fail-closed（return error result）。
- 新增 role-aware guard helpers：
  - `require_role(actor: AgentRole, required: AgentRole) -> GuardDecision`
  - 可作为 `ProcedureActionGuard` 的组合构件。
- 注册 `DelegationTool` 到 `ToolRegistry`（`allowed_modes = frozenset()`，D7），ambient schema 不可见。
- 扩展 `ProcedureRuntime.apply_action()` 步骤 5（`runtime.py:225`）：
  - 当且仅当 `tool.is_procedure_only` 为 `True` 时跳过 ambient mode check（D7 bypass）。
  - 对 `is_procedure_only == False` 的普通 tool，mode check 照常执行。
  - Risk guard（步骤 6）对所有 tool 一律执行。
- 集成测试：
  - procedure action → DelegationTool → worker → staging area → state transition。
  - 验证 ambient schema 中 DelegationTool 不出现。
  - 验证普通 tool 通过 procedure action 调用时 mode check 仍然生效。
  - **验证一个遗漏 modes 声明（`allowed_modes = frozenset()`）但 `is_procedure_only = False` 的普通 tool，在 procedure action 中仍被 mode check deny。**

### Slice F. Publish / Merge Protocol

- 新增 `src/procedures/publish.py`：`PublishTool`（继承 `BaseTool`，`allowed_modes = frozenset()`）、`merge_worker_result()`。
  - `PublishTool.execute()`：
    1. 从参数中取 `handoff_id` + `merge_keys`。
    2. 从 `context.procedure_deps.active_procedure.context["_pending_handoffs"][handoff_id]` 读取 raw worker result（D8）。
    3. 可选检查 `_review_results[handoff_id].approved`；若 review 存在且 `approved=False`，返回 deny。
    4. 调用 `merge_worker_result()` 提取 `merge_keys` 指定的 keys 到 `context_patch`。
    5. Read-modify-write `_pending_handoffs`：移除已 publish 的 handoff_id，返回完整 dict。
    6. 在 `ToolResult.data` 中放入 `{"_publish_flush_texts": [text, ...]}`。`apply_action()` 会用 `**result.data` 将其展开到扁平返回 dict，`_run_procedure_action()` 通过 `result.get("_publish_flush_texts")` 读取（D9）。
  - `procedure_deps is None` → fail-closed。
  - Guard：`require_role(actor, AgentRole.primary)`。
- `merge_worker_result(raw_result: dict, merge_keys: tuple[str, ...], current_context: dict) -> dict`：从 raw result 提取指定 keys，shallow merge 到 context_patch。
- Memory flush 路径（D9）：
  - PublishTool 在 `result.data["_publish_flush_texts"]` 中放入待 flush 文本。
  - `tool_concurrency._run_procedure_action()` 在 action 成功后检查此 key。
  - 若存在，构造 `MemoryFlushCandidate` 并调用 `loop._persist_flush_candidates()`。
  - 复用现有 `compaction_flow` 持久化管线，不引入新路径。
- 测试覆盖：primary publish 成功、worker publish 被 guard deny、缺失 handoff_id 时 error、review reject 后 publish deny、merge key 提取正确性、`_publish_flush_texts` 正确生成。

### Slice G. Purposeful Compact

- 新增 `src/procedures/compact.py`：`extract_task_state()`。
  - `extract_task_state(active_procedure, spec) -> TaskStateSnapshot`：从 context 中提取 objectives / TODOs / blockers / last_valid_result / pending_approvals。
  - V1 的提取策略：从 context 中按约定 key 名提取（`_objectives`、`_todos`、`_blockers`、`_last_result`、`_pending`），缺失 key 返回空。
- 扩展 compaction 调用链（具体 hook 点）：
  1. `compaction_flow.try_compact()` 新增可选参数 `active_procedure: ActiveProcedure | None = None`、`procedure_spec: ProcedureSpec | None = None`。
  2. `message_flow` 在调用 `try_compact()` 时从 `RequestState` 传入当前 active procedure 与 spec。
  3. `try_compact()` 内部在调用 `CompactionEngine.compact()` 前，若 `active_procedure is not None`，调用 `extract_task_state()` 生成 `TaskStateSnapshot`。
  4. `CompactionEngine.compact()` 新增可选参数 `task_state_snapshot: TaskStateSnapshot | None = None`。
  5. `CompactionEngine._run_summary()` 在构造 `_SUMMARY_PROMPT` 时，若 snapshot 存在，追加 `"Active procedure task state:\n{snapshot_text}"` section 到 `{conversation}` 之后。
  6. `CompactionResult.compaction_metadata` 增加 `task_state_snapshot` key（若非 None）。
  - 无 active procedure 时，`task_state_snapshot = None`，所有路径行为完全不变。
- `HandoffPacketBuilder` 可选使用 `TaskStateSnapshot` 作为 `current_state` 的补充来源。
- 测试覆盖：有/无 active procedure 时 compaction 行为、task state 提取正确性、`_SUMMARY_PROMPT` 中 task state section 的存在/缺失。

### Slice H. AgentLoop + Gateway Integration

- `tool_concurrency._run_procedure_action()` 扩展：
  - 构造 `ProcedureActionDeps` 并注入 `ToolContext`（D8）。deps 从 `loop._model_client`、`loop._model`、`state.active_procedure`、resolved spec 构建。
  - Action 成功后检查 `result.get("_publish_flush_texts")`（`apply_action()` 用 `**result.data` 展开，flush key 已是顶层），若存在则构造 `MemoryFlushCandidate`（`confidence=1.0`、`constraint_tags=["published_result"]`）并调用 `loop._persist_flush_candidates()`（D9）。
- `procedure_bridge.build_virtual_action_schemas()` 对 delegation / review / publish action 生成正确 schema。
- Gateway `_build_procedure_runtime()` 注册 `DelegationTool` / `ReviewTool` / `PublishTool` 到 `ToolRegistry`（`allowed_modes = frozenset()`，D7）。这些 tool 是无状态 shell，不持有 model_client 引用。
- AgentLoop constructor **不**新增 executor 依赖。Worker/reviewer executor 由 procedure-only tools 在 execute() 时从 `ProcedureActionDeps` 按需创建。这解决 gateway 的 shared runtime vs per-provider model_client 矛盾（D8）。
- 确保无 active procedure 时现有行为完全不变。

### Slice I. End-to-End Test Fixture

- 新增测试内 fixture procedure spec：
  - `id = "test.delegation_flow"`
  - `initial_state = "planning"`
  - Action `delegate`：调用 DelegationTool，迁移到 `delegated`
  - Action `review`：调用 ReviewTool（底层编排 ReviewerExecutor），迁移到 `reviewed`
  - Action `publish`：调用 PublishTool，迁移到 `published`（terminal）
  - Action `revise`：回到 `planning`（review 不通过时）
- 端到端测试：primary enters → delegates to worker → worker executes → primary reviews → primary publishes。
- 覆盖 failure paths：worker fails → primary stays in state → worker retry。
- 覆盖 review reject → revise → re-delegate → re-review → publish。

### Slice J. Observability

- 新增结构化日志：
  - `delegation_started`（handoff_id, target_role, task_brief summary）
  - `delegation_completed`（handoff_id, ok, iterations_used）
  - `worker_tool_rejected`（tool_name, reason）
  - `review_completed`（approved, concerns count）
  - `publish_executed`（merge_keys, memory_flush_candidate created）
  - `purposeful_compact_extracted`（objectives count, todos count, blockers count）
- Error codes（补充 M2a）：
  - `DELEGATION_WORKER_FAILED`
  - `DELEGATION_WORKER_TIMEOUT`
  - `PUBLISH_ROLE_DENIED`
  - `REVIEW_PARSE_FAILURE`

## Slice Dependencies

```text
Slice A ──┬──→ Slice B ──→ Slice C ──→ Slice E ──→ Slice F ──→ Slice H
          │                                                       │
          ├──→ Slice D ──→ Slice E                                ▼
          │                                                   Slice I
          └──→ Slice G (可与 E/F 并行)

Slice J 贯穿全程。
```

- `Slice A` 是所有后续的前置。
- `Slice B` 依赖 A（HandoffPacket 使用 AgentRole）。
- `Slice C` 依赖 B（WorkerExecutor 消费 HandoffPacket）。
- `Slice D` 依赖 A（ReviewerExecutor 需要 role types），可与 B/C 并行。
- `Slice E` 依赖 A + C（DelegationTool 编排 WorkerExecutor）。
- `Slice F` 依赖 E（PublishTool 处理 delegation 产物）。
- `Slice G` 依赖 A + B（compact 使用 TaskStateSnapshot），可与 E/F 并行。
- `Slice H` 依赖 E + F + G（gateway wiring 需要全部组件）。
- `Slice I` 依赖 H（端到端测试需要完整 wiring）。

## Acceptance

Hard acceptance for P2-M2b:

- `AgentRole` 类型与 `RoleSpec` 已定义，`ToolContext` 新增 `actor` 字段。
- `HandoffPacket` bounded schema 可构造、可校验，总序列化上限 32 KB，超限 fail-fast。
- `WorkerExecutor` 可接收 handoff packet，执行 bounded iterations，返回结构化 `WorkerResult`。
- Worker 只能使用 `role_spec.allowed_tool_groups` 中的 tools，其他 tool call 被 reject。
- Worker 不持久化对话，不访问 memory，不能 publish 或 delegate。
- `DelegationTool` 作为 procedure action：构造 packet → worker → staging area (`_pending_handoffs`) → state transition。
- `DelegationTool` / `ReviewTool` / `PublishTool` 的 `is_procedure_only = True` + `allowed_modes = frozenset()`，不出现在 ambient 或 worker tool schema 中。
- `ProcedureRuntime.apply_action()` 对 `is_procedure_only == True` 的 tool 跳过 ambient mode check（D7）；对 `is_procedure_only == False` 的普通 tool，mode check 照常执行。一个遗漏 modes 声明的普通 tool 不获得 bypass。
- `ToolContext.actor` 由 `_run_procedure_action()` 注入为 `AgentRole.primary`；role guard 从此字段读取当前角色。
- Procedure-only tools 通过 `context.procedure_deps` 读取 active procedure context、model_client、model（D8）。
- Publish flush 通过扁平 result dict 的 `result.get("_publish_flush_texts")` 信号（`apply_action()` 用 `**result.data` 展开），由 `tool_concurrency` 构造 `MemoryFlushCandidate(confidence=1.0)` 并调用 `loop._persist_flush_candidates()`（D9）。
- `ReviewTool` 可执行 review，返回 `ReviewResult`，解析失败时 fail-closed。
- Role-aware guards：worker 不能 publish，reviewer 不能写。
- Publish 是显式的：只有 primary 通过 publish action 才把 staging area 中的 worker result 提升到 visible context + memory flush。
- Unpublished worker results 保留在 `_pending_handoffs` staging area，不进入 user-level 连续性。
- Purposeful compact 在 active procedure 存在时提取 `TaskStateSnapshot`。
- Compaction 在无 active procedure 时行为完全不变。
- End-to-end fixture：primary delegates → worker executes → primary reviews → primary publishes。
- 所有新增 `src/` 文件满足复杂度硬门禁。

Explicitly not required:

- Parallel worker execution。
- Async delegation with mid-delegation steering。
- Worker session persistence 或 memory access。
- Dynamic role negotiation。
- Cross-procedure delegation。
- Shared Companion 的 relationship memory / membership / consent policy。
- `procedure_spec` growth adapter onboarding。
- UI for multi-agent management。

## Test Plan

受影响测试：

```bash
uv run pytest tests/procedures -q
uv run pytest tests/integration/test_procedure_store.py -q
uv run pytest tests/test_prompt_builder.py tests/test_tool_concurrency.py -q
uv run pytest tests/test_compaction.py -q
uv run pytest tests/integration/test_tool_loop_flow.py -q
```

新增测试：

```bash
uv run pytest tests/procedures/test_roles.py -q
uv run pytest tests/procedures/test_handoff.py -q
uv run pytest tests/procedures/test_worker.py -q
uv run pytest tests/procedures/test_reviewer.py -q
uv run pytest tests/procedures/test_delegation.py -q
uv run pytest tests/procedures/test_publish.py -q
uv run pytest tests/procedures/test_compact.py -q
uv run pytest tests/integration/test_multi_agent_flow.py -q
```

其中 `tests/integration/test_multi_agent_flow.py` 使用 mock model_client + fake tools 完成端到端 delegation → review → publish 流程验证。

合并前质量门禁：

```bash
just lint
just test
```

## Collaboration / Gate Notes

若使用 Agent Teams 推进：

- PM 为 backend 和 tester 分别创建独立 worktree，不共享 working directory。
- Backend 分支建议：`feat/backend-p2-m2b-multi-agent-runtime`。
- Tester review branch 每个 Gate 使用 fresh branch，例如 `feat/tester-p2-m2b-g0`、`feat/tester-p2-m2b-g0-r2`。
- 建议按 Slice 分 gate：
  - G0：Slice A + B（types + handoff packet）
  - G1：Slice C + D（worker + reviewer executors + ReviewTool）
  - G2：Slice E + F（delegation + publish + mode bypass）
  - G3：Slice G + H + I + J（compact + integration + E2E + observability）
- 每个 Gate 必须使用 `GATE_OPEN ... target_commit=<sha>` 放行。
- Backend phase 完成后必须 `commit + push`，回传 `PHASE_COMPLETE role=backend phase=<N> commit=<sha>`。
- Tester 启动前必须 `git fetch --all --prune`、`git merge --ff-only origin/<backend-branch>`、`git rev-parse HEAD`。
- Tester 报告必须 `commit + push`，PM 关闭 Gate 前必须确认报告在主仓库可见。
- 关 gate 前固定执行 `render` + `audit`；只有 `audit.reconciled=true` 才允许 `gate-close`。

## Risks

### R1. Worker 上下文太贫瘠导致无法完成任务

风险：HandoffPacket 太 bounded，worker 缺乏完成任务所需信息。

缓解：`HandoffPacketBuilder` 从 procedure context 提取 configurable keys；`evidence` 列表承载必要事实；V1 先用 fixture 测试验证信息充分性；后续版本可扩展 packet schema。

### R2. Delegation tool 阻塞 primary agent loop

风险：Worker execution 耗时，primary agent 被阻塞无法响应 steering。

缓解：V1 接受此限制（D6）；worker 有 `max_iterations` 上限 + model call timeout；后续版本支持 async delegation + checkpoint。

### R3. 把 multi-agent 变成 multi-personality

风险：Worker 获得太多自主权，偏离 single-SOUL 约束。

缓解：Worker 不拥有 memory、不能 publish、不能 delegate；role guard 强制执行；worker prompt 不包含 SOUL context。

### R4. Purposeful compact 与现有 compaction 冲突

风险：Procedure-aware compaction 破坏现有 long-session compaction 行为。

缓解：Purposeful compact 只在 active procedure 存在时激活；无 active procedure 时现有行为完全不变；task state extraction 是追加信息，不替换 rolling summary。

### R5. Scope creep 到 Shared Companion

风险：借 publish / actor / principal 预留，提前实现 shared memory 或 multi-principal。

缓解：V1 的 publish 只进入同一 session 的 continuity；actor 只有 primary / worker / reviewer；principal 仍是单一用户；shared_space_id 仍不被解释。

### R6. DelegationTool 内部复杂度过高

风险：DelegationTool 内联 worker lifecycle management，变成神类。

缓解：DelegationTool 只编排 HandoffPacketBuilder → WorkerExecutor → result mapping；worker 的 model interaction loop 在 WorkerExecutor 内部；保持 delegation.py 只做 glue。

## Clean Handoff Boundary

P2-M2b 完成后，后续可继续：

- Async / parallel worker execution（delegation 期间不阻塞 primary）。
- Worker with limited memory access（read-only recall）。
- Cross-procedure delegation（worker 可启动自己的 procedure）。
- `procedure_spec` growth adapter onboarding（P2-M2a-post）。
- P2-M3 的 shared-space publish target 扩展（publish 到 shared space 而不只是 session）。
- 多 principal 场景下的 delegation 权限治理。

P2-M2b 的干净交付物：

- `src/procedures/roles.py`：role types + specs
- `src/procedures/handoff.py`：handoff packet + worker/review result + task state snapshot
- `src/procedures/worker.py`：worker executor
- `src/procedures/reviewer.py`：reviewer executor + ReviewTool
- `src/procedures/delegation.py`：delegation tool
- `src/procedures/publish.py`：publish / merge protocol
- `src/procedures/compact.py`：purposeful compact extraction
- `src/tools/context.py` ToolContext extension（actor + procedure_deps）
- `src/procedures/deps.py` ProcedureActionDeps（D8，TYPE_CHECKING guard 避免循环导入）
- `src/tools/base.py` BaseTool.is_procedure_only property（D7）
- `src/procedures/runtime.py` procedure-only tool mode bypass（D7）
- `src/agent/tool_concurrency.py` ProcedureActionDeps 注入 + publish flush 信号路由（D8/D9）
- Role-aware guard helpers
- Gateway wiring（3 个无状态 procedure-only tools 注册 + tool_concurrency deps 注入 + flush 路由）
- End-to-end multi-agent test fixture

## Draft Review Resolution

### Round 1 — Resolved (6 findings, all superseded by subsequent rounds)

Round 1 的 6 个 finding 均已在主契约中修复，部分在 Round 2/3 中被进一步修正。以下为最终有效状态：

- P1-1 → D7（`is_procedure_only` 标记，Round 3 最终版）
- P1-2 → `_pending_handoffs` staging area（Round 1 引入，Round 2 补齐 read-modify-write）
- P1-3 → ReviewTool（Round 1 引入，Round 2 补齐 `procedure_deps` 上下文读取）
- P2-4 → 32 KB 总上限 + per-field 字符限制
- P2-5 → `model: str` 参数（Round 2 进一步改为 D8 按需创建）
- P2-6 → compaction hook 链路完整指定

### Round 2 — Resolved (5 findings, partially superseded by Round 3)

- P1-1r2 → D8 `ProcedureActionDeps`（Round 4 确定放 `src/procedures/deps.py`）
- P1-2r2 → D7 bypass（Round 3 从 `allowed_modes` 改为 `is_procedure_only`）
- P1-3r2 → D8 根本性解决 gateway wiring
- P1-4r2 → D9 result 信号 + `confidence=1.0`（Round 3 补齐字段）
- P2-5r2 → read-modify-write pattern

### Round 3 — Resolved (4 findings)

- **P1-1r3**：D7 改为 `BaseTool.is_procedure_only` 显式 property，bypass 不依赖 `allowed_modes` 默认值。
- **P1-2r3**：D8 注入点 `_run_procedure_action()` 同时设置 `ToolContext.actor = AgentRole.primary`。
- **P2-3r3**：D9 构造 `MemoryFlushCandidate` 时 `confidence=1.0`、`constraint_tags=["published_result"]`。
- **P2-4r3**：WorkerExecutor 排除 `is_procedure_only == True` 的 tools。

### Round 4 — Resolved (1 finding + consistency cleanup)

- **P2-1r4**：`ProcedureActionDeps` 固定放 `src/procedures/deps.py`，使用 `from __future__ import annotations` + `TYPE_CHECKING` guard 引用 `ActiveProcedure` / `ProcedureSpec`；`tools/context.py` 同理用 `TYPE_CHECKING` guard 引用 `ProcedureActionDeps` / `AgentRole`。运行时无循环导入。Slice A 新增 import smoke test。
- 历史 Resolution 条目整理为最终有效状态索引，移除过时的中间说法。

### Round 5 — Resolved (D9 返回形状对齐)

- **D9 flush signal 返回形状**：`apply_action()` 成功时用 `**result.data` 展开到扁平返回 dict（`runtime.py:365`），因此 `_publish_flush_texts` 是顶层 key。统一 D9、Slice F、Slice H、Acceptance 中的表述为 `result.get("_publish_flush_texts")`，不再引用 `result.data["..."]`。
