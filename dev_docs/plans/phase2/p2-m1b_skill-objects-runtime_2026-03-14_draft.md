# P2-M1b 实施计划：Skill Objects Runtime

- Date: 2026-03-14
- Status: draft
- Scope: `P2-M1b` only; deliver minimum viable skill object runtime with PromptBuilder & AgentLoop integration
- Basis:
  - [`design_docs/phase2/p2_m1_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m1_architecture.md)
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/skill_objects_runtime.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/skill_objects_runtime.md)
  - [`decisions/0048-skill-objects-as-runtime-experience-layer.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0048-skill-objects-as-runtime-experience-layer.md)
  - [`decisions/0049-growth-governance-kernel-adapter-first.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/decisions/0049-growth-governance-kernel-adapter-first.md)
  - [`dev_docs/plans/phase2/p2-m1a_growth-governance-kernel_2026-03-06.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1a_growth-governance-kernel_2026-03-06.md) (predecessor)

## Context

`P2-M1a` 已经交付了治理内核：`GrowthGovernanceEngine`、`PolicyRegistry`、adapter contract 和 `SoulGovernedObjectAdapter`。当前只有 `soul` 是 onboarded kind，其余（`skill_spec`、`wrapper_tool`、`procedure_spec`、`memory_application_spec`）均为 reserved。

`P2-M1b` 的核心任务是把 `skill_spec` 从 reserved 提升为 onboarded，并交付支撑 Use Case D（"agent 能把一次新学到的任务经验沉淀为 skill object，并在相似任务中优先复用"）的最小可用 runtime。

当前代码基线：

- `src/growth/`: 治理内核就绪，`GrowthObjectKind.skill_spec` 已注册为 reserved
- `src/agent/prompt_builder.py`: `_layer_skills()` 返回空字符串（placeholder）
- `src/agent/message_flow.py`: `_initialize_request_state()` 中没有 TaskFrame 或 skill resolution 步骤
- `design_docs/skill_objects_runtime.md`: 已 approved，定义了 `SkillSpec`、`SkillEvidence`、`ResolvedSkillView`、`TaskFrame`、5 个运行时部件和 3 个 join points
- DB: 无 skill 相关表

## Core Decision

`P2-M1b` 聚焦 **skill object runtime 的端到端最小闭环**，包含持久化、检索、投影、学习和治理接入，但不包含 builder 任务模式和 beads work memory：

1. 新增 `src/skills/` 作为 skill object 的运行时模块，与 `src/growth/` 的治理编排层分离
2. Skill object 持久化走 PostgreSQL 17（项目基线），新增 `skill_specs` + `skill_evidence` 两张表
3. 将 `skill_spec` 在 `PolicyRegistry` 中从 reserved 升格为 onboarded，新增 `SkillGovernedObjectAdapter`
4. 在 `AgentLoop` 中引入 `pre-plan` 和 `post-run-learning` 两个 join point，不引入独立 planner 模块
5. `PromptBuilder._layer_skills()` 从 placeholder 升级为消费 `ResolvedSkillView.llm_delta`
6. `pre-procedure` join point 在 P2-M1b 中作为空操作占位——当前无 procedure runtime 消费者
7. Skill creation 走治理路径：用户教学或 post-run 提案 → `GrowthProposal` → `GrowthGovernanceEngine`
8. V1 resolution 采用规则匹配（activation_tags + capability + preconditions），不引入 embedding

### 关于 Builder Runtime 和 Beads Work Memory

Architecture doc §3 将 P2-M1b 定义为"Skill Objects + Builder Runtime"。经评估，完整 builder 任务模式依赖 procedure runtime 的 deterministic state/guard/transition 契约（P2-M2 交付物），在 P2-M2 之前强行引入 builder 模式会形成半成品。

本计划的处理方式：

- **TaskFrame 纳入 P2-M1b**：作为 skill resolution 的输入契约，TaskFrame 是 skill runtime 的必要组件，不需要完整 builder 模式即可成立
- **Builder 任务模式推迟**：正式的受治理任务模式（task brief、中间决策、TODO/blockers 产出）推迟到 P2-M2 procedure runtime 可用后
- **Beads work memory 推迟**：beads 的工作记忆扩展与 growth cases（P2-M1c）更紧密相关，且当前 beads 主要承担 devcoord 控制面职责，扩展用途需要独立设计决策

这使 P2-M1b 保持聚焦，同时为 P2-M1c（growth cases + promotion）提供可用的 skill runtime 基座。

## Goals

- 将 `skill_spec` 从 reserved 升格为 onboarded，完成第二类 growth object 的端到端治理接入
- 交付 `SkillSpec` + `SkillEvidence` 的 PostgreSQL 持久化和数据访问层
- 交付 `SkillResolver` + `SkillProjector`，实现规则驱动的 skill 候选检索与投影
- 交付 `SkillLearner`，实现 task 结束后的 evidence 更新（仅 deterministic 信号）
- 将 `PromptBuilder._layer_skills()` 从 placeholder 升级为消费真实 skill delta
- 在 `AgentLoop` 中集成 `pre-plan` 和 `post-run-learning` join points
- 提供 skill 创建入口：用户教学 → proposal → governance → active skill

## Non-Goals

- 不在 P2-M1b 内实现 builder 任务模式（task brief / structured artifacts 产出）
- 不在 P2-M1b 内扩展 beads 为 work memory
- 不在 P2-M1b 内实现 promote / demote 的端到端执行（P2-M1c）
- 不在 P2-M1b 内引入 embedding / 向量召回——V1 仅规则匹配
- 不在 P2-M1b 内实现 procedure runtime（P2-M2）
- 不在 P2-M1b 内实现 skill 导入/导出/跨 agent 交换
- 不在 P2-M1b 内实现自动 promote / 自动 patch / 自动 disable
- 不在 P2-M1b 内引入额外 LLM 调用进行 TaskFrame 提取——V1 仅规则抽取

## Proposed Architecture

### 1. Skill Object Domain Model

直接采用 `design_docs/skill_objects_runtime.md` §6 的 V1 schema，使用 Pydantic v2 `BaseModel`（数据验证，非 settings）。

```
src/skills/
├── __init__.py
├── types.py          # SkillSpec, SkillEvidence, ResolvedSkillView, TaskFrame
├── store.py          # SkillStore (PostgreSQL-backed SkillRegistry impl)
├── resolver.py       # SkillResolver
├── projector.py      # SkillProjector
└── learner.py        # SkillLearner
```

关键类型：

- `SkillSpec`：frozen Pydantic model，持久化到 `skill_specs` 表
- `SkillEvidence`：frozen Pydantic model，持久化到 `skill_evidence` 表，更新时 replace（不原地修改）
- `ResolvedSkillView`：turn-local 投影，不持久化
- `TaskFrame`：turn-local 任务上下文，不持久化

### 2. PostgreSQL Tables

```sql
-- skill_specs: 可交换可插拔的最小封装单元
CREATE TABLE neomagi.skill_specs (
    id          TEXT PRIMARY KEY,
    capability  TEXT NOT NULL,
    version     INTEGER NOT NULL DEFAULT 1,
    summary     TEXT NOT NULL,
    activation  TEXT NOT NULL,
    activation_tags  JSONB NOT NULL DEFAULT '[]',
    preconditions    JSONB NOT NULL DEFAULT '[]',
    delta            JSONB NOT NULL DEFAULT '[]',
    tool_preferences JSONB NOT NULL DEFAULT '[]',
    escalation_rules JSONB NOT NULL DEFAULT '[]',
    exchange_policy  TEXT NOT NULL DEFAULT 'local_only',
    disabled    BOOLEAN NOT NULL DEFAULT FALSE,
    status      TEXT NOT NULL DEFAULT 'proposed',    -- GrowthLifecycleStatus
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(id, version)
);

-- skill_evidence: 运行时可学习部分
CREATE TABLE neomagi.skill_evidence (
    skill_id    TEXT NOT NULL REFERENCES neomagi.skill_specs(id),
    source      TEXT NOT NULL,
    success_count    INTEGER NOT NULL DEFAULT 0,
    failure_count    INTEGER NOT NULL DEFAULT 0,
    last_validated_at TIMESTAMPTZ,
    positive_patterns JSONB NOT NULL DEFAULT '[]',
    negative_patterns JSONB NOT NULL DEFAULT '[]',
    known_breakages   JSONB NOT NULL DEFAULT '[]',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (skill_id)
);
```

设计要点：

- `skill_specs.status` 存储 `GrowthLifecycleStatus`，与 `soul_versions.status` 语义一致
- tuple 类型字段（`activation_tags`, `delta` 等）在 DB 中用 JSONB array 存储，Python 层转换
- `skill_evidence` 与 `skill_specs` 为 1:1 关系，`skill_id` 是 PK 也是 FK
- 不做版本化历史表——V1 只保留当前 evidence 快照，旧 evidence 通过 `SkillLearner` 的 audit log 记录
- `version` 字段用于未来导入/导出/兼容控制，V1 中 monotonic 递增

### 3. SkillStore

实现 `design_docs/skill_objects_runtime.md` §7.2 的 `SkillRegistry` protocol。

```python
class SkillStore:
    """PostgreSQL-backed skill registry and store."""

    def __init__(self, db_session_factory: async_sessionmaker) -> None: ...

    # ── SkillRegistry protocol ──
    async def list_active(self) -> list[SkillSpec]: ...
    async def get_evidence(self, skill_ids: tuple[str, ...]) -> dict[str, SkillEvidence]: ...

    # ── CRUD for governance adapter ──
    async def create(self, spec: SkillSpec, evidence: SkillEvidence) -> None: ...
    async def update_status(self, skill_id: str, status: GrowthLifecycleStatus) -> None: ...
    async def update_evidence(self, skill_id: str, evidence: SkillEvidence) -> None: ...
    async def get_by_id(self, skill_id: str) -> SkillSpec | None: ...
```

- `list_active()` 只返回 `status='active' AND disabled=False` 的 skill
- 所有 DB 操作 async，使用 `sqlalchemy.ext.asyncio`

### 4. SkillGovernedObjectAdapter

接入 `src/growth/` 治理内核，将 `skill_spec` 从 reserved 升格为 onboarded。

```python
class SkillGovernedObjectAdapter:
    """Adapter connecting skill_spec to the growth governance kernel."""

    kind = GrowthObjectKind.skill_spec

    def __init__(self, store: SkillStore) -> None: ...

    async def propose(self, proposal: GrowthProposal) -> int: ...
    async def evaluate(self, version: int) -> GrowthEvalResult: ...
    async def apply(self, version: int) -> None: ...
    async def rollback(self, **kwargs) -> int: ...
    async def veto(self, version: int) -> None: ...
    async def get_active(self) -> list[SkillSpec]: ...
```

治理语义：

- `propose()`：从 `proposal.payload` 解析 `SkillSpec` + `SkillEvidence`，以 `status=proposed` 写入 DB
- `evaluate()`：pin `SKILL_SPEC_EVAL_CONTRACT_V1`（contract_id + version），按四层 contract 执行检查，返回带 `contract_id` / `contract_version` 的 `GrowthEvalResult`
  - **Boundary gates**: schema validity, activation correctness, projection safety
  - **Effect evidence**: learning discipline（负经验只接受 deterministic signal）
  - **Scope claim**: scope claim consistency（local / reusable / promotable 与证据等级匹配）
  - **Efficiency metrics**: V1 不启用，预留给 P2-M1c
  - Veto conditions: schema_invalid, activation_tags_contradictory, preconditions_self_contradictory, prompt_injection_risk, negative_evidence_from_non_deterministic_signal
  - Budget limits: delta_budget_per_skill_max_3, total_delta_budget_max_9
  - 参考实现：`src/growth/contracts.SKILL_SPEC_EVAL_CONTRACT_V1`
- `apply()`：将 `status` 从 `proposed` 更新为 `active`
- `rollback()`：将 `status` 设为 `rolled_back`，若有前一版本则恢复
- `veto()`：将 `status` 设为 `vetoed`

### 5. TaskFrame Extraction

在 `AgentLoop` 的 `_initialize_request_state()` 中，于 mode/scope/tool 解析之后、`_build_system_prompt()` 之前生成 `TaskFrame`。

V1 规则抽取策略（无额外 LLM 调用）：

- `task_type`：从最近用户消息中的关键词匹配推导（如包含"搜索/查找" → "research"；包含"写/创建" → "create"）
- `target_outcome`：最近一条用户消息的前 200 字符
- `risk`：默认 "low"；若消息包含高风险信号词则升为 "high"
- `channel`：从 `identity.channel_type` 获取
- `current_mode`：从 session mode 获取
- `current_procedure`：V1 固定为 `None`（无 procedure runtime）
- `available_tools`：从 `ToolRegistry.list_tools(mode)` 获取工具名列表

### 6. SkillResolver

```python
class SkillResolver:
    """Resolves candidate skills for a given TaskFrame."""

    def __init__(self, store: SkillStore, max_candidates: int = 3) -> None: ...

    async def resolve(self, frame: TaskFrame) -> list[tuple[SkillSpec, SkillEvidence | None]]: ...
```

V1 resolution 算法：

1. `store.list_active()` 获取所有 active 非 disabled 的 skill
2. 过滤：`preconditions` 不满足的 skill 被剔除（规则匹配）
3. 评分：基于 `activation_tags` 与 TaskFrame 的 `task_type` / `target_outcome` 的关键词重叠度
4. 排序：
   - 优先级 1：`escalation_rules` 非空且当前 turn 涉及高风险信号的 skill
   - 优先级 2：evidence 中 `known_breakages` 更少、`last_validated_at` 更近的 skill
   - 优先级 3：`delta` 更短、更局部的 skill
5. 取 top 1~3

### 7. SkillProjector

```python
class SkillProjector:
    """Projects resolved skills into ResolvedSkillView for prompt/runtime consumption."""

    def __init__(self, max_delta_per_skill: int = 3) -> None: ...

    def project(
        self,
        candidates: list[tuple[SkillSpec, SkillEvidence | None]],
        frame: TaskFrame,
    ) -> ResolvedSkillView: ...
```

投影规则：

- `llm_delta`：每个 skill 最多取 `max_delta_per_skill` 条 delta，拼接所有候选
- `runtime_hints`：从 `tool_preferences` 提取，V1 仅作为 debug 信息
- `escalation_signals`：从 `escalation_rules` 提取，V1 仅记录日志，不触发实际 escalation
- 若某个 skill 的 `preconditions` 与当前上下文矛盾（由 resolver 已过滤），不应出现在投影中
- 总 `llm_delta` 条目硬上限：9 条（3 skills × 3 delta）

### 8. SkillLearner

```python
class SkillLearner:
    """Post-task evidence updater and skill creation proposer."""

    def __init__(
        self,
        store: SkillStore,
        governance_engine: GrowthGovernanceEngine,
    ) -> None: ...

    async def record_outcome(
        self,
        resolved_skills: list[SkillSpec],
        outcome: TaskOutcome,
    ) -> None: ...

    async def propose_new_skill(
        self,
        spec_draft: SkillSpec,
        evidence_draft: SkillEvidence,
        *,
        proposed_by: str = "agent",
    ) -> int: ...
```

V1 学习边界（严格遵循设计文档 §7.5）：

- `record_outcome()` 仅在 task 结束时触发（不是每个 tool call 后）
- 负经验只来自 deterministic 信号：tool 返回结构化失败、guard deny
- 正经验不因"用户没纠正"而自动成立；V1 仅在用户显式确认时写入正经验
- `propose_new_skill()` 生成 `GrowthProposal` 提交到 `GrowthGovernanceEngine`，不直接 apply

`TaskOutcome` 数据模型：

```python
@dataclass(frozen=True)
class TaskOutcome:
    """Terminal state of a task, consumed by SkillLearner."""
    success: bool
    tool_results: tuple[ToolResult, ...] = ()
    user_confirmed: bool = False
    failure_signals: tuple[str, ...] = ()
```

### 9. PromptBuilder Integration

`_layer_skills()` 从 placeholder 升级：

```python
def _layer_skills(self, skill_view: ResolvedSkillView | None = None) -> str:
    if not skill_view or not skill_view.llm_delta:
        return ""
    lines = ["## Skill Experience", ""]
    for delta in skill_view.llm_delta:
        lines.append(f"- {delta}")
    return "\n".join(lines)
```

层位固定为 Safety 之后、Workspace context 之前（与设计文档 §9.1 一致）。

`build()` 方法签名变更：

```python
def build(
    self,
    session_id: str,
    mode: ToolMode,
    compacted_context: str | None = None,
    *,
    scope_key: str = "main",
    recent_messages: list[str] | None = None,
    recall_results: list[MemorySearchResult] | None = None,
    skill_view: ResolvedSkillView | None = None,  # 新增
) -> str:
```

### 10. AgentLoop Integration

在 `message_flow.py` 的 `_initialize_request_state()` 中新增 pre-plan join point：

```python
# 在 mode/scope/tools 解析之后, _build_system_prompt() 之前:
task_frame = _extract_task_frame(loop, mode, scope_key, identity, content)
skill_view = await _resolve_skills(loop, task_frame)
system_prompt = _build_system_prompt(loop, ..., skill_view=skill_view)
```

在 `_complete_assistant_response()` 中新增 post-run-learning join point：

```python
# 在 assistant response 完成后:
await _post_run_learning(loop, state, collected_text)
```

`AgentLoop.__init__()` 新增依赖：

```python
skill_store: SkillStore | None = None,
skill_resolver: SkillResolver | None = None,   # 由 composition root 注入
skill_learner: SkillLearner | None = None,
```

### 11. Composition Root Wiring

在 `src/backend/app.py` 或等价 composition root 中：

```python
skill_store = SkillStore(db_session_factory)
skill_adapter = SkillGovernedObjectAdapter(skill_store)
# PolicyRegistry: skill_spec → onboarded
governance_engine = GrowthGovernanceEngine(
    adapters={
        GrowthObjectKind.soul: soul_adapter,
        GrowthObjectKind.skill_spec: skill_adapter,   # 新增
    },
    policy_registry=policy_registry,
)
skill_resolver = SkillResolver(skill_store)
skill_learner = SkillLearner(skill_store, governance_engine)
agent_loop = AgentLoop(
    ...,
    skill_store=skill_store,
    skill_resolver=skill_resolver,
    skill_learner=skill_learner,
)
```

## Delivery Strategy

`P2-M1b` 复杂度：**中高**。
主要难点：

- 需要同时建立持久化、运行时检索和 prompt 集成三个方面的端到端闭环
- 需要在不破坏现有 `AgentLoop` 和 `PromptBuilder` 行为的前提下注入新的 join points
- `SkillGovernedObjectAdapter` 必须与现有 `GrowthGovernanceEngine` 无缝集成

建议拆成 5 个顺序 work packages：

## Implementation Shape

### Work Package A: Domain Types & DB Migration

新增 skill object 领域模型和 PostgreSQL 表。

文件：

- `src/skills/__init__.py`
- `src/skills/types.py`（SkillSpec, SkillEvidence, ResolvedSkillView, TaskFrame, TaskOutcome）
- `alembic/versions/xxxx_create_skill_tables.py`

产出：

- Pydantic v2 BaseModel 定义，frozen=True
- Alembic migration 创建 `skill_specs` + `skill_evidence` 表
- `ensure_schema()` 中显式导入 skill model 模块（M3 post-review 经验）

验证：

- Migration up/down 可逆
- Type hints 和 model validation 单元测试

### Work Package B: SkillStore & Governance Adapter

新增持久化层和治理接入。

文件：

- `src/skills/store.py`
- `src/growth/adapters/skill.py`
- 修改 `src/growth/policies.py`（skill_spec → onboarded）

产出：

- `SkillStore`：CRUD + SkillRegistry protocol 实现
- `SkillGovernedObjectAdapter`：propose/evaluate/apply/rollback/veto
- `PolicyRegistry`：`skill_spec` 从 reserved 改为 onboarded

验证：

- `SkillStore` 单元测试（mock DB session）
- Adapter 单元测试
- `GrowthGovernanceEngine` 集成测试：skill_spec 的 propose→evaluate→apply 闭环
- 现有 `tests/growth/` 回归不退化

### Work Package C: TaskFrame, Resolver & Projector

新增任务上下文提取和 skill 检索/投影。

文件：

- `src/skills/resolver.py`
- `src/skills/projector.py`
- TaskFrame 提取逻辑（可在 `src/skills/types.py` 中增加 factory method，或在 `message_flow.py` 中增加 helper）

产出：

- 规则驱动的 TaskFrame 提取
- `SkillResolver.resolve()`：tag 过滤 + 评分 + top-K
- `SkillProjector.project()`：delta 裁剪 + 上下文预算控制

验证：

- Resolver 排序行为的单元测试（多 skill 场景、空 skill 场景、precondition 过滤）
- Projector 输出格式和上限裁剪测试
- TaskFrame 提取的边界用例测试

### Work Package D: PromptBuilder & AgentLoop Integration

将 skill runtime 注入现有 prompt 组装和消息处理流程。

文件：

- 修改 `src/agent/prompt_builder.py`（`_layer_skills` 升级，`build()` 签名新增 `skill_view`）
- 修改 `src/agent/message_flow.py`（pre-plan 和 post-run-learning join points）
- 修改 `src/agent/agent.py`（constructor 新增 skill 依赖）

产出：

- `_layer_skills()` 消费 `ResolvedSkillView.llm_delta`
- `_initialize_request_state()` 中 TaskFrame 提取 + skill resolution
- `_complete_assistant_response()` 中 post-run-learning 触发
- 层位：Safety > Skills > Workspace context

验证：

- `PromptBuilder` 单元测试：有/无 skill_view 的 prompt 输出对比
- `AgentLoop` 集成测试：mock SkillStore 验证 skill delta 出现在 system prompt 中
- 现有 `tests/test_prompt_builder.py` 回归不退化
- 现有 `tests/test_agent_loop.py` 回归不退化

### Work Package E: SkillLearner & Creation Path + Final Tests

新增学习引擎、创建入口和综合测试。

文件：

- `src/skills/learner.py`
- 测试文件：
  - `tests/skills/test_types.py`
  - `tests/skills/test_store.py`
  - `tests/skills/test_resolver.py`
  - `tests/skills/test_projector.py`
  - `tests/skills/test_learner.py`
  - `tests/skills/test_governance_adapter.py`
  - `tests/integration/test_skill_runtime_e2e.py`

产出：

- `SkillLearner.record_outcome()`：evidence 更新（deterministic 信号 only）
- `SkillLearner.propose_new_skill()`：用户教学 → GrowthProposal → governance path
- 端到端集成测试：create skill → resolve → project into prompt → learn evidence

验证：

- SkillLearner 负经验只来自结构化失败信号
- SkillLearner 正经验只在 user_confirmed=True 时写入
- 端到端：skill create → governance apply → next turn resolve → prompt injection
- 全量回归 green

## Boundaries

### In

- `SkillSpec` + `SkillEvidence` PostgreSQL 持久化
- `SkillStore`（SkillRegistry protocol 实现）
- `SkillGovernedObjectAdapter`（skill_spec onboarding）
- `TaskFrame` 规则提取
- `SkillResolver`（规则匹配，top 1~3）
- `SkillProjector`（delta 裁剪，上下文预算）
- `SkillLearner`（deterministic evidence 更新 + skill creation proposal）
- `PromptBuilder._layer_skills()` 升级
- `AgentLoop` pre-plan + post-run-learning join points
- 基础测试和端到端验证

### Out

- Builder 任务模式（task brief / structured artifacts）
- Beads work memory 扩展
- Promote / demote 端到端执行（P2-M1c）
- Embedding / 向量召回
- Skill 导入/导出/跨 agent 交换
- 自动 promote / patch / disable
- Procedure runtime
- `pre-procedure` join point 的实际实现（空操作占位）

## Risks

1. **Over-resolution 噪声**：若 resolver 过度命中，skill delta 会退化为 prompt 污染层，增加上下文噪声
2. **Evidence 误累积**：若 deterministic 信号定义过宽，会导致 evidence 质量下降
3. **AgentLoop 侵入性**：新 join points 可能破坏现有消息处理流程的简洁性
4. **DB migration 冲突**：与可能并行的其他 migration 产生顺序问题
5. **Performance**：`list_active()` 在 skill 数量增长后可能成为热点
6. **治理路径摩擦**：若 skill creation 必须经历完整 governance 流程，early adopter 体验可能过重

## Mitigations

1. **Resolution 硬上限**：top 3 candidates，每 skill 最多 3 delta，总 delta ≤ 9 条——超过直接裁剪
2. **Learner 保守策略**：V1 只接受 deterministic 失败信号作为负经验，正经验需 user_confirmed；宁可少学也不乱学
3. **Join point 最小侵入**：pre-plan 和 post-run-learning 分别只在 `_initialize_request_state()` 和 `_complete_assistant_response()` 末尾添加一步调用，不改变现有函数内部逻辑
4. **Migration 独立**：新表不依赖现有表（除 schema 级别），down migration 可安全回退
5. **Performance 缓解**：V1 skill 数量预计极少（<20），暂不需要缓存；若增长超预期，后续加 in-memory cache
6. **治理轻量化**：V1 的 `evaluate()` 按 `SKILL_SPEC_EVAL_CONTRACT_V1` 四层 contract 执行 deterministic checks（schema validity, activation correctness, projection safety, learning discipline, scope claim），不引入重型 benchmark 或分布式评测

## Acceptance

- `skill_spec` 在 `PolicyRegistry` 中为 onboarded，`GrowthGovernanceEngine` 可对其执行 propose→evaluate→apply→rollback
- 至少一个 skill object 可通过 governance 路径创建并变为 active
- Active skill 在 next turn 能被 `SkillResolver` 命中并通过 `PromptBuilder` 投影到 system prompt 中
- `SkillLearner` 能在 task 结束后更新 evidence（至少 success_count/failure_count）
- `TaskFrame` 能从 AgentLoop 的当前上下文中规则提取
- `PromptBuilder` 的 skill delta 注入层位正确：Safety 之后、Workspace context 之前
- 现有回归测试（`tests/test_prompt_builder.py`、`tests/test_agent_loop.py`、`tests/growth/`、`tests/test_evolution.py`）不退化
- `just lint` clean、`just test` 全量 green

## Open Questions

1. `skill_specs` 表是否需要 `proposal` / `eval_result` JSONB 列（类似 `soul_versions`），还是只依赖 `SkillGovernedObjectAdapter` 在治理层记录这些信息？
   - 倾向：V1 不加——治理元数据由 governance engine 管理，store 只管对象本体
2. `SkillEvidence` 更新时是否需要保留历史版本用于 audit？
   - 倾向：V1 不保留——audit trail 通过 `SkillLearner` 的结构化日志记录
3. Skill creation 是否应该有专用 tool（如 `create_skill`），还是仅通过 `SkillLearner.propose_new_skill()` 编程式创建？
   - 倾向：V1 先编程式创建，后续评估是否需要 user-facing tool
