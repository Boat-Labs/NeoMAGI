---
doc_id: 019d7dbc-c6a1-7bd2-8233-468fcc1e5f7a
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-11T20:10:16+02:00
---
# P2-M2c 实现日志：ProcedureSpec Governance Adapter

> 日期：2026-04-11
> 计划：`dev_docs/plans/phase2/p2-m2c_procedure-spec-governance-adapter_2026-04-11.md`

## 实现总结

让 `procedure_spec` 从 `reserved` kind 进入正式治理路径 (propose → evaluate → apply → rollback / veto → audit)。

### 新增文件 (5)

| 文件 | 说明 |
|------|------|
| `src/procedures/governance_store.py` | ProcedureSpecGovernanceStore — current-state + governance ledger CRUD |
| `src/growth/adapters/procedure_spec.py` | Adapter (7 协议方法) + 5 个 eval check 函数 |
| `alembic/versions/d1e2f3a4b5c6_create_procedure_spec_governance_tables.py` | Migration: 2 张表 + partial unique index |
| `tests/growth/test_procedure_spec_adapter.py` | 58 个单元测试 |
| `tests/integration/test_procedure_spec_governance.py` | 8 个 PG 集成测试 |

### 修改文件 (8)

| 文件 | 变更 |
|------|------|
| `src/session/database.py` | +`_create_procedure_spec_governance_tables()` idempotent DDL |
| `src/procedures/registry.py` | +`unregister()` 方法 |
| `src/procedures/store.py` | +`has_active_for_spec()` 方法 |
| `src/growth/contracts.py` | +`PROCEDURE_SPEC_EVAL_CONTRACT_V1`, registry 从 skeleton→V1 |
| `src/growth/policies.py` | `procedure_spec`: reserved → onboarded |
| `src/gateway/app.py` | Composition root 重构: shared registries, governance store 注入, startup restore |
| `tests/growth/test_engine.py` | `procedure_spec` 从 reserved → onboarded-no-adapter 测试 |
| `tests/growth/test_policies.py` | `procedure_spec` 从 reserved list 移除 |

### DB 表

- `procedure_spec_definitions` (current-state): id, version, payload(JSONB), disabled
- `procedure_spec_governance` (append-only ledger): governance_version(BIGSERIAL), procedure_spec_id, status, proposal(JSONB), eval_result(JSONB), applied_at, rolled_back_from
- Partial unique index `uq_procedure_spec_governance_single_active` on `(procedure_spec_id) WHERE status = 'active'`

### Eval Contract V1 (5 checks)

| 检查 | 覆盖内容 |
|------|---------|
| `transition_determinism` | initial_state 存在, action.to target 存在, entry_policy == "explicit" |
| `guard_completeness` | enter_guard + action.guard 在 guard registry 可解析 |
| `interrupt_resume_safety` | 至少一个 terminal state |
| `checkpoint_recoverability` | tool 存在, action_id 合法, 不冲突 RESERVED/ambient |
| `scope_claim_consistency` | context_model 可解析, allowed_modes 非空 |

### 关键设计决策

- **No in-place upgrade**: apply 时同 spec_id 已有 applied → 拒绝
- **Rollback = disable-only**: 不恢复 previous version，与 WrapperToolGovernedObjectAdapter 一致
- **Active instance safety**: apply/rollback 前 `has_active_for_spec()` 检查
- **DB-first + compensating**: 先 commit DB，再操作 registry；registry 失败则补偿回滚 DB
- **JSONB 归一化**: propose() 入口统一 `model_dump(mode="json")`，避免 frozenset/ToolMode 写入失败
- **Status-aware applied_at**: proposed/vetoed 清空, rolled_back 保留, active 写入

## Review Findings & Fixes

### P1: proposal.object_id 未绑定 ProcedureSpec.id
- **问题**: store 用 proposal.object_id 写 ledger，apply/registry 用 payload 中的 spec.id，两者不一致可绕过 single-active
- **修复**: propose() 解析后校验 `proposal.object_id == spec.id`; apply() 校验 `record.procedure_spec_id == spec.id`

### P2: create_proposal 未做 JSON mode 归一化
- **问题**: payload 原样写入 JSONB，frozenset/ToolMode 等非 JSON 原生类型可能导致写入失败
- **修复**: propose() 中替换 payload 为 `spec.model_dump(mode="json")` 后落库

### P2: evaluate 没复用 registry 静态校验
- **问题**: 5 个 check 未覆盖 ambient tool name collision 和 entry_policy 校验
- **修复**: transition_determinism 新增 entry_policy 校验; checkpoint_recoverability 新增 ambient tool collision

### P2: rollback 会清空 applied_at 审计字段
- **问题**: update_proposal_status() 总是写 applied_at=NULL，rollback 时丢失应用时间
- **修复**: applied_at 改为 status-aware (proposed/vetoed→NULL, rolled_back→COALESCE保留, active→写入)

### P2: compensate 路径无法清空 applied_at
- **问题**: COALESCE 修复后，补偿回 proposed 时 applied_at 也被保留
- **修复**: status-aware 逻辑，proposed 强制 NULL

## 测试

- 新增 **66 tests** (58 unit + 8 integration)
- 全量回归: **1860 passed**, 0 failed
