---
doc_id: 019d7d5f-0bfc-7a3c-9997-c8a9aa27340f
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-11T18:27:54+02:00
---
# P2-M2c 实现计划：ProcedureSpec Governance Adapter

> 状态：draft v2（review findings 已采纳）  
> 日期：2026-04-11  
> 输入：`design_docs/phase2/p2_m2_post_self_evolution_staged_plan.md` Section 3  
> 参照模式：`SkillGovernedObjectAdapter` + `WrapperToolGovernedObjectAdapter`

## 0. 目标

让 `procedure_spec` 从 `reserved` kind 进入正式治理路径：

```
propose → evaluate → apply → rollback / veto → audit
```

验收后，NeoMAGI 能安全地修改自己的流程定义，且每次变更可解释、可回滚、可审计。

## 1. 当前基线

| 组件 | 状态 |
|------|------|
| `GrowthObjectKind.procedure_spec` | enum 已存在 |
| `PolicyRegistry` | `procedure_spec: reserved`，无 adapter |
| `PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON` | 5 checks 已命名，实现为空 |
| `ProcedureSpecRegistry` | 内存 registry，有 `register()` + `validate_procedure_spec()` 静态校验 |
| `ProcedureStore` | 只管 `active_procedures`（运行实例），不管 spec governance |
| `_build_governance_engine()` | 已接 soul / skill_spec / wrapper_tool，无 procedure_spec |
| `_build_memory_and_tools()` | 先 governance → 后 procedure_runtime；registries 封装在 `_build_procedure_runtime()` 内部 |

## 2. 实现切片

### Slice A：ProcedureSpecGovernanceStore（DB 层）

新增 spec governance 的持久化层，复用 SkillStore 的 current-state + ledger 双表模式。

**新增 DB 表**（alembic migration + `ensure_schema()` idempotent DDL）：

`procedure_spec_definitions`（current-state）：
| 列 | 类型 | 说明 |
|----|------|------|
| id | TEXT PK | spec_id |
| version | INTEGER | spec version |
| payload | JSONB | ProcedureSpec.model_dump() |
| disabled | BOOLEAN default FALSE | rollback 后标记禁用 |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

`procedure_spec_governance`（append-only ledger）：
| 列 | 类型 | 说明 |
|----|------|------|
| governance_version | SERIAL PK | 自增序列 |
| procedure_spec_id | TEXT | spec_id |
| status | TEXT | proposed / active / rolled_back / vetoed / superseded |
| proposal | JSONB | GrowthProposal payload |
| eval_result | JSONB | GrowthEvalResult |
| created_by | TEXT | |
| created_at | TIMESTAMPTZ | |
| applied_at | TIMESTAMPTZ | nullable |
| rolled_back_from | INTEGER | nullable，指向被回滚的 governance_version |

**新增文件**：`src/procedures/governance_store.py`

方法清单（参照 SkillStore）：
- `create_proposal(proposal) → int`
- `get_proposal(governance_version) → record | None`
- `store_eval_result(governance_version, result)`
- `update_proposal_status(governance_version, status, *, applied_at?, rolled_back_from?, session?)`
- `upsert_active(spec_payload, session?)`
- `disable(spec_id, session?)`
- `find_last_applied(spec_id) → record | None`
- `find_previous_applied(spec_id, before) → record | None`
- `list_active() → list[record]`
- `transaction() → AsyncContextManager[AsyncSession]`

**`src/session/database.py`**：
- 新增 `_create_procedure_spec_governance_tables(conn, schema)` — 与 `_create_skill_tables()` 和 `_create_procedure_tables()` 同级的 idempotent DDL
- 在 `ensure_schema()` 中调用，覆盖 fresh DB 跳过 alembic migration 的场景

### Slice B：Eval Contract V1（5 个确定性检查）

将 `PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON` 升级为 `PROCEDURE_SPEC_EVAL_CONTRACT_V1`。

**保留 skeleton 原有 5 个 check 名称**，给出 V1 最小确定性实现：

| 检查（保留原名） | 输入 | V1 判定 |
|------|------|------|
| `transition_determinism` | ProcedureSpec | 每个 state 的每个 action 只有一个 target；initial_state 存在于 states；所有 action.to target 存在 |
| `guard_completeness` | ProcedureSpec + GuardRegistry | enter_guard 和每个 action.guard 在 registry 中可解析（或为 None） |
| `interrupt_resume_safety` | ProcedureSpec | 至少有一个 terminal state（actions 为空）；non-terminal states 都有至少一个 action |
| `checkpoint_recoverability` | ProcedureSpec + ToolRegistry | 每个 action.tool 存在于 ToolRegistry；action_id 满足 OpenAI function name 约束；不与 RESERVED_ACTION_IDS 冲突 |
| `scope_claim_consistency` | ProcedureSpec + ContextRegistry | context_model 在 ProcedureContextRegistry 中可解析；allowed_modes 非空 |

说明：
- **不重映射 check 名称**。skeleton 的 5 个 required_checks 保留原名，V1 给每个名称提供可确定性执行的最小实现。`checkpoint_recoverability` 在 V1 聚焦 tool binding validity（checkpoint 能恢复的前提是 action tool 可用），`scope_claim_consistency` 在 V1 聚焦 context model + allowed_modes 一致性。后续版本可扩展检查深度，不需要改名。
- 所有检查纯确定性，无 LLM 调用。
- 复用 `validate_procedure_spec()` 已有的静态校验逻辑，避免重复实现。

**新增文件**：`src/growth/adapters/procedure_spec.py`（检查函数 + adapter 类）

### Slice C：ProcedureSpecGovernedObjectAdapter（7 个协议方法）

在 `src/growth/adapters/procedure_spec.py` 中实现完整 adapter：

```
kind = GrowthObjectKind.procedure_spec

propose(proposal) → int
  - 校验 payload 包含 procedure_spec dict
  - 解析 ProcedureSpec（fail-fast）
  - store.create_proposal() → governance_version

evaluate(version) → GrowthEvalResult
  - early-exit: missing / not proposed
  - 解析 ProcedureSpec from payload
  - 运行 5 个 checks（Slice B）
  - store.store_eval_result()
  - 返回 composite result

apply(version)
  - 校验: exists, proposed, eval passed
  - 解析 ProcedureSpec
  - active instance 检查: has_active_for_spec(spec_id) → 拒绝
  - already-applied 检查: find_last_applied(spec_id) 存在 → 拒绝 (no in-place upgrade)
  - ATOMIC:
    - upsert_active(spec_payload)
    - update_proposal_status(version, active, applied_at=now())
  - spec_registry.register(spec)（DB-first + compensating）

rollback(**kwargs)
  - kwarg: procedure_spec_id: str
  - active instance 检查: has_active_for_spec(spec_id) → 拒绝
  - find current applied (find_last_applied)
  - find previous applied (find_previous_applied)
  - ATOMIC:
    - if previous: upsert_active(previous); supersede current
    - else: disable(spec_id); supersede current
    - update current status → rolled_back
    - create rollback ledger entry → new governance_version
  - 更新 spec_registry（replace 或 unregister）
  - 返回 new governance_version

veto(version)
  - proposed → mark vetoed
  - active → delegate to rollback

get_active() → list[ProcedureSpec]
  - 从 spec_registry.list_specs() 返回
```

**No in-place upgrade（P1 修正）**：
- `apply()` 在 eval passed 之后、ATOMIC 之前，调用 `find_last_applied(spec_id)`
- 如果存在已 applied 的同 spec_id 定义 → 返回错误 `PROCEDURE_SPEC_ALREADY_ACTIVE`
- 要更新 spec 定义，必须先 rollback 旧版本，再 apply 新提案
- 这与 WrapperToolGovernedObjectAdapter 的行为一致

**Active instance 安全检查**：
- 需要在现有 `ProcedureStore` 中新增 `has_active_for_spec(spec_id) → bool`
- 查询 `active_procedures WHERE spec_id = ? AND completed_at IS NULL`

**Registry 副作用**（DB-first + compensating semantics）：
1. 先 commit DB
2. 再操作 spec_registry
3. registry 失败则补偿回滚 DB

**`ProcedureSpecRegistry` 需补充**：
- `unregister(spec_id)` — 移除 spec（rollback/veto 使用）
- `replace(spec)` — 替换已注册 spec（restore previous 使用）

### Slice D：Wiring + Startup Restore + Policy 升级

**Composition root 重构（P1 修正）**：

当前 `_build_memory_and_tools()` 的构造顺序是先 governance → 后 procedure_runtime，且 registries 封装在 `_build_procedure_runtime()` 内部。P2-M2c 需要 registries 同时传给 ProcedureRuntime 和 adapter。

方案：将 procedure registries/store 抽成独立构造步骤：

```python
async def _build_memory_and_tools(settings, db_session_factory):
    # ... existing code until tool_registry ...

    # 1. 先构造 procedure registries (shared between runtime and governance)
    procedure_registries = _build_procedure_registries(tool_registry)

    # 2. 构造 governance engine (需要 procedure_registries.spec_registry)
    governance_engine, wrapper_tool_store = _build_governance_engine(
        db_session_factory, evolution_engine, skill_store, tool_registry,
        procedure_spec_registry=procedure_registries.spec_registry,
    )

    # 3. 恢复 active procedure specs (从 governance store → spec_registry)
    await _restore_active_procedure_specs(governance_store, procedure_registries)

    # 4. 恢复 active wrappers
    await _restore_active_wrappers(wrapper_tool_store, tool_registry)

    # 5. 构造 procedure runtime (使用已恢复的 registries)
    procedure_runtime = _build_procedure_runtime_from_registries(
        procedure_registries, db_session_factory, tool_registry,
    )

    return (...)
```

`_build_procedure_registries(tool_registry)` 返回一个 dataclass/namedtuple 包含 `spec_registry`, `context_registry`, `guard_registry`。

`_build_procedure_runtime()` 拆为两步：
- `_build_procedure_registries()` — 构造 registries（可共享）
- `_build_procedure_runtime_from_registries()` — 构造 ProcedureRuntime + ProcedureStore + 注册 procedure-only tools

**`src/growth/policies.py`**：
- `procedure_spec` 从 `reserved` → `onboarded`

**`src/growth/contracts.py`**：
- `PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON` → `PROCEDURE_SPEC_EVAL_CONTRACT_V1`
- 保留原有 5 个 `required_checks` 名称不变

**启动恢复**：
- 新增 `_restore_active_procedure_specs(governance_store, procedure_registries)`
- 从 `governance_store.list_active()` 加载已 apply 的 spec → `spec_registry.register(spec)`
- 在 `_build_governance_engine()` 之后、`_build_procedure_runtime_from_registries()` 之前调用

**Fresh DB DDL（P1 修正）**：
- `src/session/database.py` 新增 `_create_procedure_spec_governance_tables(conn, schema)`
- 在 `ensure_schema()` 中调用（与 `_create_skill_tables()`、`_create_procedure_tables()` 同级）
- 包含 `procedure_spec_definitions` 和 `procedure_spec_governance` 两张表的 `CREATE TABLE IF NOT EXISTS`

### Slice E：测试 + 端到端验证

**单元测试**（`tests/growth/test_procedure_spec_adapter.py`）：
- `test_propose_valid_spec` — 合法 spec payload → 返回 governance_version
- `test_propose_invalid_payload` — 缺少必需字段 → 拒绝
- `test_evaluate_all_checks_pass` — 合法 spec → passed=True, 5 checks 全通过
- `test_evaluate_transition_determinism_fail` — action.to 指向不存在 state → 检查失败
- `test_evaluate_guard_completeness_fail` — guard 不在 registry → 检查失败
- `test_evaluate_checkpoint_recoverability_fail` — tool 不在 ToolRegistry → 检查失败
- `test_evaluate_scope_claim_fail` — context_model 不可解析 → 检查失败
- `test_apply_success` — eval passed → upsert + registry.register()
- `test_apply_already_active_rejected` — 同 spec_id 已有 applied → `PROCEDURE_SPEC_ALREADY_ACTIVE`
- `test_apply_with_active_instance_rejected` — 有 active procedure → 拒绝
- `test_rollback_restores_previous` — rollback → previous spec restored in registry
- `test_rollback_disables_when_no_previous` — 无 previous → disable + unregister
- `test_rollback_with_active_instance_rejected` — 有 active procedure → 拒绝 rollback
- `test_veto_proposed` — proposed → vetoed
- `test_veto_active_delegates_to_rollback` — active → rollback
- `test_get_active` — 返回已注册 specs

**Wiring + restore 测试（P2 修正）**：
- `test_policy_procedure_spec_onboarded` — PolicyRegistry 中 procedure_spec 状态为 onboarded
- `test_contract_v1_required_checks` — PROCEDURE_SPEC_EVAL_CONTRACT_V1 包含 5 个原名 checks
- `test_build_governance_engine_includes_procedure_spec` — mock gateway wiring 确认 adapter 在 engine 中
- `test_restore_active_procedure_specs` — governance_store 有 active record → spec_registry 已注册
- `test_restore_empty_store` — 空 store → spec_registry 不变
- `test_ensure_schema_creates_governance_tables` — fresh DB ensure_schema 后两张表存在

**集成测试**（`tests/integration/test_procedure_spec_governance.py`）：
- 端到端 propose → evaluate → apply → rollback 在真实 PG 上跑通
- apply 同 spec_id 两次 → 第二次被拒绝
- governance ledger 记录完整（proposed → active → rolled_back）

## 3. 执行顺序

```
Slice A (DB store + DDL)  →  Slice B (eval checks)  →  Slice C (adapter)  →  Slice D (wiring)  →  Slice E (测试)
```

A→B→C 是严格依赖；D 依赖 C；E 覆盖全部。

## 4. 影响范围

| 位置 | 变更类型 |
|------|---------|
| `src/procedures/governance_store.py` | **新增** |
| `src/growth/adapters/procedure_spec.py` | **新增** |
| `alembic/versions/` | **新增** migration |
| `src/session/database.py` | 新增 `_create_procedure_spec_governance_tables()` + `ensure_schema()` 调用 |
| `src/procedures/registry.py` | 补充 `unregister()` / `replace()` |
| `src/procedures/store.py` | 补充 `has_active_for_spec()` |
| `src/growth/policies.py` | `procedure_spec: reserved → onboarded` |
| `src/growth/contracts.py` | skeleton → V1（保留原名） |
| `src/gateway/app.py` | composition root 重构 + wiring + startup restore |
| `tests/growth/test_procedure_spec_adapter.py` | **新增** |
| `tests/integration/test_procedure_spec_governance.py` | **新增** |

## 5. 不做的事

- 不做 Claude Code CLI / Codex CLI wrapper
- 不做 git worktree 编排
- 不做 self-evolution workflow
- 不做 memory source ledger（P2-M2d）
- 不做 in-place upgrade（apply 时同 spec_id 已有 applied → 拒绝）
- 不做 spec 的 WebChat 用户入口（propose 仍通过 CLI 或 operator 脚本）
- 不改 ProcedureRuntime 核心逻辑（enter/apply_action 不变）
- 不重映射 eval contract check 名称（保留 skeleton 原名）

## 6. 风险

| 风险 | 缓解 |
|------|------|
| Registry 内存副作用不受 DB transaction 保护 | DB-first + compensating semantics（P2-M1c 已验证） |
| apply 时同 spec_id 已有 applied 导致 ledger 混乱 | find_last_applied 存在 → 拒绝（no in-place upgrade） |
| apply 时有 active instance 导致运行时冲突 | has_active_for_spec() → 拒绝 |
| 启动恢复时 registries 未就绪 | 抽成 bundle 先构造，restore 在 governance 和 runtime 构造之间 |
| fresh DB 缺表 | ensure_schema() 中加 idempotent DDL |
| Eval 检查 V1 不够深 | 保留原名 + 最小实现，后续可加深；V1 先确保核心安全约束 |
