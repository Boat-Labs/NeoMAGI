---
doc_id: 019d6c1e-4c77-74d6-adf8-545950369a2f
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-08T10:03:38+02:00
---
# P2-M2b: Multi-Agent Runtime — Implementation Summary

- Date: 2026-04-08
- Plan: `dev_docs/plans/phase2/p2-m2b_multi-agent-runtime_2026-04-07.md`
- Status: **accepted**

## Delivery Summary

24 files changed/created in first commit (+2792 new lines), 2 follow-up fix commits (+198 lines).
Final: 1794 tests passed (incl. 89 new procedure tests + 4 E2E multi-agent flow tests).

## Plan Acceptance Checklist

| Acceptance Item | Status |
|-----------------|--------|
| AgentRole + RoleSpec types defined | pass |
| ToolContext extended (actor, handoff_id, procedure_deps) | pass |
| BaseTool.is_procedure_only property (default False) | pass |
| ProcedureActionDeps in src/procedures/deps.py (TYPE_CHECKING guard) | pass |
| Import smoke test — no circular dependency | pass |
| HandoffPacket bounded schema, 32KB total, per-field limits, fail-fast | pass |
| WorkerExecutor bounded iterations, structured WorkerResult | pass |
| Worker triple tool filter: group + is_procedure_only + RiskLevel.high | pass |
| Worker injects ToolContext(scope_key, session_id) to tool calls | pass |
| DelegationTool as procedure-only BaseTool, staging to _pending_handoffs | pass |
| Failed delegation: no staging, clean error for model retry | pass |
| Worker extracts inner "result" from prompt-compliant response | pass |
| ReviewTool reads staging, writes _review_results, fail-closed on parse | pass |
| PublishTool: role guard (primary only), read staging, merge to visible | pass |
| Publish review check: rejects if review exists and not approved | pass |
| D7: is_procedure_only bypass in ProcedureRuntime.apply_action() | pass |
| D7: empty allowed_modes normal tool NOT bypassed | pass |
| D8: ProcedureActionDeps injected by tool_concurrency._run_procedure_action() | pass |
| D8: ToolContext.actor set to AgentRole.primary | pass |
| D9: _publish_flush_texts via flat result dict | pass |
| D9: MemoryFlushCandidate(confidence=1.0, constraint_tags=["published_result"]) | pass |
| Purposeful compact: extract_task_state + CompactionEngine task_state_text | pass |
| compaction_flow.try_compact() + _run_compaction() accept task_state_text | pass |
| Gateway registers DelegationTool/ReviewTool/PublishTool | pass |
| Procedure-only tools excluded from ambient + worker schemas | pass |
| E2E: delegate → review → publish happy path | pass |
| E2E: worker failure → model retry (no staging) | pass |
| E2E: review reject → publish denied | pass |
| E2E: worker role cannot publish | pass |
| All new src/ files pass complexity hard gate | pass |

## New Files

| File | Content |
|------|---------|
| `src/procedures/roles.py` | AgentRole, RoleSpec, DEFAULT_ROLE_SPECS |
| `src/procedures/deps.py` | ProcedureActionDeps (TYPE_CHECKING guard) |
| `src/procedures/handoff.py` | HandoffPacket, WorkerResult, ReviewResult, TaskStateSnapshot, HandoffPacketBuilder |
| `src/procedures/worker.py` | WorkerExecutor (multi-turn, bounded, triple filter) |
| `src/procedures/reviewer.py` | ReviewerExecutor + ReviewTool |
| `src/procedures/delegation.py` | DelegationTool + require_role guard |
| `src/procedures/publish.py` | PublishTool + merge_worker_result |
| `src/procedures/compact.py` | extract_task_state + render_task_state_text |

## Modified Files

| File | Change |
|------|--------|
| `src/tools/base.py` | +is_procedure_only property |
| `src/tools/context.py` | +actor, handoff_id, procedure_deps fields |
| `src/procedures/types.py` | ProcedureExecutionMetadata actor doc update |
| `src/procedures/runtime.py` | D7 is_procedure_only mode bypass |
| `src/agent/tool_concurrency.py` | D8 deps injection + D9 flush routing + actor |
| `src/agent/compaction.py` | task_state_text parameter |
| `src/agent/compaction_flow.py` | task_state_text passthrough |
| `src/gateway/app.py` | _register_procedure_tools() |

## Review Findings and Fixes

### Plan Review: 5 rounds, 16 findings (7 P1, 9 P2)

Key decisions refined through review:
- **D7**: evolved from `allowed_modes==frozenset()` → narrowed bypass → `is_procedure_only` explicit marker
- **D8**: `ProcedureActionDeps` fixed to `src/procedures/deps.py` with TYPE_CHECKING; actor injection specified
- **D9**: flush signal shape aligned with `apply_action()` `**result.data` expansion
- **Staging**: `_pending_handoffs` / `_review_results` use read-modify-write for shallow merge compatibility

### Implementation Review: 3 rounds, 4 findings (3 high, 1 medium)

**Round 1** (3 findings):

1. **[High] Worker output contract mismatch** — Worker prompt asks for `{"result": {...}, "evidence": [...]}` but `_try_parse_json` put entire response into `WorkerResult.result`, causing double nesting. `merge_worker_result` couldn't find merge_keys. Fix: worker now extracts inner `result` dict.
2. **[High] Failed delegation staging inconsistency** — DelegationTool staged failed worker results via `context_patch`, but M2a's `apply_action` ignores `context_patch` on `ok=False`. Fix: DelegationTool returns clean error without `context_patch` on worker failure; model retries by re-invoking delegate action.
3. **[Medium] Worker bypasses pre-tool guardrail** — Worker called `tool.execute(args)` without ToolContext, bypassing session-scoped read state. Fix: WorkerExecutor now injects `ToolContext(scope_key, session_id)` to every tool call.

**Round 2** (1 finding):

4. **[High] Worker still accesses high-risk tools** — ToolContext injection doesn't replace `check_pre_tool_guard()`. Workers could access `write_file`/`edit_file` unguarded. Fix: `_build_allowed_tools()` now applies triple filter including `RiskLevel.high` exclusion.

## Commits

1. `faf8f98` feat(procedures): implement P2-M2b multi-agent runtime (24 files, +2792)
2. `a7bc361` fix(procedures): fix worker output contract, failed delegation staging, and tool context injection (5 files, +123/-12)
3. `4aa00a9` fix(procedures): exclude high-risk tools from worker executor (2 files, +75/-4)
