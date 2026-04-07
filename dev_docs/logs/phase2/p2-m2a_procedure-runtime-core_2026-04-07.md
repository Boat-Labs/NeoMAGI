---
doc_id: 019d6981-ebc9-7a1e-ae91-0f23058577ee
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-07T19:53:35+00:00
---
# P2-M2a: Procedure Runtime Core — Implementation Summary

- Date: 2026-04-07
- Plan: `dev_docs/plans/phase2/p2-m2a_procedure-runtime-core_2026-04-07.md`
- Status: **accepted**

## Delivery Summary

17 files changed/created (+2200 new lines), 1709 tests passed (incl. 96 new), 6 PG integration tests.

## Plan Acceptance Checklist

| Acceptance Item | Status |
|-----------------|--------|
| ProcedureSpec registry + static validation fail-closed | pass |
| PostgreSQL single-active per session (partial unique index) | pass |
| enter_procedure() creates active instance, rejects duplicate | pass |
| apply_action() executes tool + context_patch + state transition + revision+1 | pass |
| guard deny / tool failure / invalid patch / CAS conflict do not modify state | pass |
| terminal state sets completed_at, same session can re-enter | pass |
| PromptBuilder injects ProcedureView when active | pass |
| AgentLoop exposes virtual action tool schema | pass |
| procedure action treated as serial barrier (no parallel) | pass |
| active procedure loadable across requests (checkpoint resume) | pass |
| invalid JSON args rejected fail-closed (PROCEDURE_INVALID_ARGS) | pass |
| gateway composition root wires ProcedureRuntime into AgentLoop | pass |
| procedure guard denial classified as guard_denied (not tool_failure) | pass |
| all new src/ files pass complexity hard gate | pass |

## Review Findings and Fixes

3 rounds of review, 7 findings total (2 P1 + 5 P2), all resolved.

**Round 1** (5 findings):

1. **[P1] Stale prompt/schema after procedure transition** — `_refresh_procedure_state` only updated active_procedure/view/action_map but not system_prompt or tools_schema. Fixed: extracted `_rebuild_procedure_checkpoint` in `procedure_bridge.py` that rebuilds prompt + re-merges tool schemas after every successful action.
2. **[P1] Invalid args silently converted to {}** — `_parse_args()` returned empty dict on bad JSON, allowing tool execution with no args. Fixed: changed to return `(dict, error_msg)` tuple; `apply_action` step 4 validates args before guards/execution, returns `PROCEDURE_INVALID_ARGS`.
3. **[P2] Gateway never wires ProcedureRuntime** — Added `_build_procedure_runtime()` in gateway/app.py; `_build_memory_and_tools` returns procedure_runtime; `_make_agent_loop` passes it to AgentLoop constructor. Smoke test added.
4. **[P2] Store semantics untested against real PG** — Created `tests/integration/test_procedure_store.py` (6 tests). Added `_create_procedure_tables()` to `ensure_schema()` for fresh-DB startup path.
5. **[P2] Malformed context_patch raises instead of structured error** — Wrapped `normalize_tool_result()` call in try/except; returns `PROCEDURE_INVALID_PATCH` on failure.

**Round 2** (2 findings):

6. **[P2] ProcedureSpec registry not fail-closed** — `ProcedureSpecRegistry` constructor now accepts tool/context/guard registries; `register()` runs `validate_procedure_spec()` and raises `ValueError` on any error. Gateway composition root passes all three registries.
7. **[P2] Procedure guard denials misclassified as tool_failure** — Added `_PROCEDURE_DENY_CODES` set; `_run_procedure_action` maps `PROCEDURE_ACTION_DENIED`/`PROCEDURE_CONFLICT` to `guard_denied:` signal, mirroring ambient tool path.

**Round 3** — 0 findings, residual risk noted (test-only zero-arg constructor), addressed with docstring annotation.

## New Files

| File | Purpose | Lines |
|------|---------|-------|
| `src/procedures/__init__.py` | Package init | 1 |
| `src/procedures/types.py` | ProcedureSpec, ActiveProcedure, ActionSpec, StateSpec, GuardDecision, CasConflict, ProcedureExecutionMetadata, ProcedureView, builders | ~170 |
| `src/procedures/result.py` | ToolResult, normalize_tool_result | ~42 |
| `src/procedures/registry.py` | ProcedureSpecRegistry (fail-closed), ProcedureContextRegistry, ProcedureGuardRegistry, validate_procedure_spec | ~165 |
| `src/procedures/runtime.py` | ProcedureRuntime (enter_procedure, apply_action, load_active) | ~310 |
| `src/procedures/store.py` | ProcedureStore (PostgreSQL, CAS, single-active) | ~175 |
| `src/agent/procedure_bridge.py` | Bridge functions: resolve/build/rebuild procedure checkpoint | ~95 |
| `alembic/versions/c0d1e2f3a4b5_...` | active_procedures table migration | ~65 |
| `tests/procedures/test_types.py` | 25 tests | ~200 |
| `tests/procedures/test_result.py` | 7 tests | ~55 |
| `tests/procedures/test_registry.py` | 22 tests | ~240 |
| `tests/procedures/test_runtime.py` | 29 tests | ~530 |
| `tests/procedures/test_prompt_view.py` | 3 tests | ~40 |
| `tests/procedures/test_concurrency.py` | 4 tests | ~80 |
| `tests/integration/test_procedure_store.py` | 6 PG integration tests | ~190 |

## Modified Files

| File | Change |
|------|--------|
| `src/agent/agent.py` | `procedure_runtime` constructor param |
| `src/agent/message_flow.py` | RequestState procedure fields, procedure loading, schema merge, prompt passthrough |
| `src/agent/prompt_builder.py` | `_layer_procedure()`, `procedure_view` param |
| `src/agent/tool_concurrency.py` | Procedure action barrier, `_run_procedure_action`, `_PROCEDURE_DENY_CODES` |
| `src/gateway/app.py` | `_build_procedure_runtime()`, composition root wiring |
| `src/session/database.py` | `_create_procedure_tables()` for fresh-DB |
| `tests/conftest.py` | `active_procedures` cleanup |
| `tests/test_app_integration.py` | `test_agent_loop_has_procedure_runtime` |

## Architecture Decisions

- **D1**: Runtime core first, governance adapter deferred — `procedure_spec` remains `reserved` in growth policies
- **D2**: PostgreSQL active instance store with partial unique index for single-active
- **D3**: Virtual action tool schema — function name = action_id, runtime maps to underlying tool
- **D4**: Procedure actions use barrier serialization, never parallel
- **D5**: ProcedureExecutionMetadata reserved fields (actor, principal_id, etc.) — validated but not interpreted

## Clean Handoff Boundary

P2-M2a delivers:
- `src/procedures/` runtime core (types, registry, store, runtime)
- PostgreSQL active_procedures table + CAS
- ToolResult.context_patch surface
- AgentLoop procedure view + virtual action routing
- Checkpoint-level resume

P2-M2b can build on top:
- Multi-agent roles, handoff packet, publish/merge
- Purposeful compact for task state
- procedure_spec governance adapter (suggested as P2-M2a-post)
