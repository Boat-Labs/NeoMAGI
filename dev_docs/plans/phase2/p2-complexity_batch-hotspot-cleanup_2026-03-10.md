# P2 Complexity Batch Hotspot Cleanup

- Date: 2026-03-10
- Status: approved
- Scope: Phase 2 并行治理/质量收敛 track；一次性清理当前 `src.infra.complexity_guard` 报告中的全部 block 级复杂度热点
- Track Type: parallel governance / quality track; outside the `P2-M*` product milestone series
- Driver: 当前 block 级热点仍有 31 个文件 / 64 条 finding；逐文件零散修补过慢，且持续拖累日常开发与 Phase 2 交付速度
- Execution Issue: `NeoMAGI-aaa`
- Basis:
  - [`AGENTTEAMS.md`](../../../AGENTTEAMS.md)
  - [`AGENTS.md`](../../../AGENTS.md)
  - [`CLAUDE.md`](../../../CLAUDE.md)
  - [`design_docs/index.md`](../../../design_docs/index.md)
  - [`design_docs/phase2/roadmap_milestones_v1.md`](../../../design_docs/phase2/roadmap_milestones_v1.md)
  - [`decisions/0051-adopt-code-complexity-budgets-and-ratchet-governance.md`](../../../decisions/0051-adopt-code-complexity-budgets-and-ratchet-governance.md)
  - [`src/infra/complexity_guard.py`](../../../src/infra/complexity_guard.py)
  - [`tests/test_complexity_guard.py`](../../../tests/test_complexity_guard.py)

## Context

当前仓库复杂度治理已经具备以下基线：

- `src.infra.complexity_guard` 的扫描范围已经固定：
  - 只扫描 git tracked 的 `src/`、`scripts/` 与测试路径下的 `*.py/*.ts/*.tsx/*.js/*.jsx`
  - `alembic/versions/` 与其他路径默认忽略
- `.complexity-overrides.json` 只提供局部覆盖：
  - 当前仅支持 `skip_file_lines`
  - 它不是 inclusion list，也不负责扩展扫描范围
  - 它不会关闭 Python 的 `function_lines` / `function_branches` / `function_nesting`
- 当前 block 热点几乎全部是 Python 函数级问题，而不是前端文件长度问题

因此，本次工作不能靠“调阈值”“扩 override”“缩扫描范围”解决；必须通过真实的代码拆分、提取 helper、去分支和去嵌套完成。

## Goal

在一个 Claude Code 批量清理轮次内完成以下目标：

1. 消除当前 31 个 block 文件对应的 64 条 block finding。
2. 不放宽 ADR 0051 既有阈值，不修改扫描范围语义。
3. 不滥用 `.complexity-overrides.json` 掩盖 Python / tests 热点。
4. 保持既有行为、接口和测试语义不变。
5. 在完成后刷新 `.complexity-baseline.json`，把已清掉的存量债务从 ratchet 台账中移除。

## Non-Goals

- 不为这轮治理引入新的复杂度配置系统。
- 不将 `.complexity-overrides.json` 扩展成通用规则语言。
- 不把 `src.infra.complexity_guard` 再改回“按组/按范围切换扫描”的重设计。
- 不借机做无关的架构重写。
- 不为了“过复杂度门禁”牺牲行为可读性、测试可维护性或审计路径。

## Hard Constraints

- 不修改 ADR 0051 的 block 阈值：
  - `src/`、`scripts/`：`file_lines > 800`、`function_lines > 50`、`branches > 6`、`nesting > 3` 为 block
  - `tests/`：文件级 `<= 1200`，函数级 block 仍按 `50 / 6 / 3`
- 不新增“忽略整个文件”的复杂度逃生口。
- 不把当前 block 级 Python / tests 热点改成通过 override 规避。
- 任何行为变更必须有测试证据；若现有测试不足，先补测试再重构。
- 长函数优先拆 helper / 子对象 / 明确阶段函数，不接受仅靠注释或压行“伪收敛”。

## Current Baseline

基于 `2026-03-10` 的 `uv run python -m src.infra.complexity_guard report --json`：

- Files scanned: `193`
- Block findings: `64`
- Block files: `31`
- Target findings: `249`
- Target files: `77`

当前需要处理的 block 文件分布：

- `src/`：17 个
- `tests/`：14 个

## Execution Strategy

本任务要求“一次性完成”，但不等于“一个巨型提交胡乱堆完”。推荐做法是：

1. 先清理产品/runtime 核心代码：
   - `src/agent/`
   - `src/gateway/`
   - `src/infra/`
   - `src/memory/`
   - `src/session/`
2. 再清理测试热点：
   - 优先拆大型 fixture builder / app factory / integration helper
   - 再拆超长测试函数
3. 每完成一组模块就跑受影响测试，最后跑全量回归和复杂度检查。

推荐分组如下：

### Wave A: Runtime Critical Path

- `src/agent/compaction.py`
- `src/agent/model_client.py`
- `src/channels/telegram.py`
- `src/gateway/app.py`
- `src/gateway/budget_gate.py`

目标：

- 优先降低用户请求主路径中的长函数、深嵌套和高分支风险
- 将 orchestration 逻辑提取为清晰的阶段函数/子 helper

### Wave B: Infra / Memory / Session

- `src/infra/doctor.py`
- `src/infra/preflight.py`
- `src/memory/curator.py`
- `src/memory/evolution.py`
- `src/memory/indexer.py`
- `src/memory/searcher.py`
- `src/memory/writer.py`
- `src/session/database.py`
- `src/session/manager.py`
- `src/agent/memory_flush.py`
- `src/agent/prompt_builder.py`
- `src/agent/guardrail.py`

目标：

- 把“多阶段检查 / 组装 / persistence”逻辑拆成小函数
- 将条件分支和后处理收敛到 helper，而不是继续把责任堆在入口函数

### Wave C: Test Hotspots

- `tests/conftest.py`
- `tests/integration/test_budget_gate_e2e.py`
- `tests/integration/test_tool_loop_flow.py`
- `tests/integration/test_tool_modes_integration.py`
- `tests/integration/test_websocket.py`
- `tests/test_agent_budget_smoke.py`
- `tests/test_agent_compaction_integration.py`
- `tests/test_compaction_smoke.py`
- `tests/test_doctor.py`
- `tests/test_ensure_schema.py`
- `tests/test_evolution.py`
- `tests/test_model_client_tool_stream.py`
- `tests/test_preflight.py`
- `tests/test_restore.py`

目标：

- 将超长 app factory / patch builder / setup helper 提取到测试 helper
- 将一个函数里的多阶段断言拆成若干命名良好的局部 helper
- 避免为了过复杂度而损害测试可读性或丢掉场景覆盖

## File Inventory

以下列表是当前 batch cleanup 的完整处理清单。

### Runtime / Product Code

| File | Current block hotspots |
| --- | --- |
| `src/agent/compaction.py` | `CompactionEngine.compact` branches `12>6` / lines `185>50`; `CompactionEngine._extract_anchor_phrases` nesting `4>3` |
| `src/agent/guardrail.py` | `_extract_anchors_from_content` nesting `4>3` |
| `src/agent/memory_flush.py` | `MemoryFlushGenerator.generate` branches `8>6`; lines `57>50` |
| `src/agent/model_client.py` | `OpenAICompatModelClient.chat_stream_with_tools` branches `18>6` / lines `96>50` / nesting `8>3`; `_retry_call` nesting `4>3` |
| `src/agent/prompt_builder.py` | `PromptBuilder._layer_workspace` branches `7>6`; `PromptBuilder._load_daily_notes` branches `9>6` / lines `57>50` |
| `src/channels/telegram.py` | `TelegramAdapter._handle_dm` branches `9>6`; lines `89>50` |
| `src/gateway/app.py` | `lifespan` lines `161>50`; `health_ready` lines `59>50`; `_handle_chat_send` lines `56>50` / nesting `4>3` |
| `src/gateway/budget_gate.py` | `BudgetGate.try_reserve` lines `73>50` |
| `src/infra/doctor.py` | `run_doctor` branches `9>6` / lines `55>50`; `_check_soul_consistency` lines `83>50`; `_check_memory_index_health` branches `7>6` / lines `57>50` / nesting `4>3`; `_check_budget_status` lines `66>50`; `_check_provider_connectivity` lines `69>50`; `_check_memory_reindex_dryrun` branches `12>6` / lines `78>50` / nesting `4>3` |
| `src/infra/preflight.py` | `_check_workspace_dirs` lines `54>50` |
| `src/memory/curator.py` | `MemoryCurator.curate` branches `7>6`; lines `64>50` |
| `src/memory/evolution.py` | `EvolutionEngine.evaluate` lines `83>50`; `apply` lines `69>50`; `rollback` lines `76>50` |
| `src/memory/indexer.py` | `MemoryIndexer.index_daily_note` lines `69>50` |
| `src/memory/searcher.py` | `MemorySearcher.search` lines `77>50` |
| `src/memory/writer.py` | `MemoryWriter.append_daily_note` lines `74>50` |
| `src/session/database.py` | `ensure_schema` lines `84>50` |
| `src/session/manager.py` | `store_compaction_result` lines `54>50`; `load_session_from_db` lines `71>50`; `_persist_message` lines `61>50` |

### Test Code

| File | Current block hotspots |
| --- | --- |
| `tests/conftest.py` | `_integration_cleanup` lines `57>50` |
| `tests/integration/test_budget_gate_e2e.py` | `_make_budget_app` lines `82>50`; `_make_budget_app.lifespan` lines `61>50` |
| `tests/integration/test_tool_loop_flow.py` | `_make_app` lines `60>50`; `TestFencingMidLoop.test_fencing_error_returns_session_fenced` lines `51>50` |
| `tests/integration/test_tool_modes_integration.py` | `_make_app` lines `59>50` |
| `tests/integration/test_websocket.py` | `_make_app` lines `68>50` |
| `tests/test_agent_budget_smoke.py` | `TestAgentBudgetSmoke.test_budget_check_log_emitted` lines `53>50` |
| `tests/test_agent_compaction_integration.py` | `TestAgentCompactionIntegration.test_current_turn_preserved_after_compaction` lines `59>50`; `TestPostCompactionOverflow.test_overflow_store_exception_failopen` lines `59>50` |
| `tests/test_compaction_smoke.py` | `TestCompactionSmoke.test_full_pipeline_30_turns` lines `68>50`; `TestCompactionSmoke.test_second_compaction_advances_watermark` lines `64>50` |
| `tests/test_doctor.py` | `_mock_engine_all_ok._execute` branches `9>6` / nesting `8>3`; `_mock_engine_with_responses._execute` nesting `4>3` |
| `tests/test_ensure_schema.py` | `test_ensure_schema_backfills_legacy_sessions_columns` lines `62>50` |
| `tests/test_evolution.py` | `TestRollbackCompensation.test_rollback_compensation_failure_logs` lines `51>50` |
| `tests/test_model_client_tool_stream.py` | `TestToolCallAccumulation.test_gemini_null_index_multi_calls_do_not_concat` lines `53>50` |
| `tests/test_preflight.py` | `TestValidationErrorWrapping.test_lifespan_wraps_validation_error` nesting `4>3` |
| `tests/test_restore.py` | `_make_restore_patches` lines `126>50` |

## Implementation Tactics

Claude Code 执行时应优先使用以下收敛手法：

- 长 orchestration 函数：
  - 提取“阶段函数”而不是无意义小 helper
  - 用命名函数表达 `prepare / validate / execute / persist / emit`
- 高分支函数：
  - 提取 guard / dispatch / normalization helper
  - 把分支分解为数据驱动表或职责单一的子路径
- 高嵌套函数：
  - 先做 early return / fail-fast
  - 再拆掉内层条件块
- 测试长函数：
  - 把 setup、patch、assertion bundle 提取到测试 helper
  - 保留测试叙事，不要把断言藏成不透明魔法

明确禁止：

- 通过放宽阈值收尾
- 把 Python block 热点塞进 `.complexity-overrides.json`
- 用超大 helper 文件替换超大原文件，制造新的热点迁移
- 为追求复杂度数字而改坏现有日志、回滚、补偿、fail-closed 语义

## Verification Plan

执行过程中：

1. 每处理一组模块，先跑受影响测试。
2. 每轮重构后跑：
   - `uv run ruff check src/ tests/`
   - `uv run python -m src.infra.complexity_guard check`

全部完成后，必须跑：

1. `just lint`
2. `just test`
3. `uv run python -m src.infra.complexity_guard report --json`
4. `just complexity-baseline`
5. 再次执行 `uv run python -m src.infra.complexity_guard check`

说明：

- `just complexity-baseline` 只能在 block 热点真正被清理后执行，用于把 ratchet baseline 下调到新状态。
- 若全量 `just test` 过重或遇到环境阻塞，必须在最终报告中明确写出哪些 suites 已跑、哪些未跑、为什么未跑。

## Acceptance

本任务完成需同时满足：

- `src.infra.complexity_guard report --json` 中当前 31 个 block 文件全部清零。
- 理想状态：全仓 `block_findings == 0`。
- 最低门槛：这份文档列出的 31 个文件不再出现在 block 清单中。
- `.complexity-baseline.json` 已刷新，反映新的更低债务水平。
- `just lint` 通过。
- `just test` 通过，或有明确、可复现、可接受的环境性阻塞说明。
- 没有新增 override 来掩盖 Python / tests 函数级热点。
- 没有改变固定扫描范围语义。

## Handoff Notes for Claude Code

给 Claude Code 的执行口径：

- 这是一次 repo 质量治理 sweep，不是单文件 spot-fix。
- 允许在同一轮里连续处理多组文件，但不要把所有改动压成无法审阅的随机大补丁。
- 可以使用多个 focused commit；不要求“一切只在一个 commit 里结束”。
- 每完成一个 wave 都应更新复杂度报告，避免在最后才发现仍有剩余 block。
- 若发现某个热点其实暴露更深层设计问题，可以做最小必要抽象，但必须证明它同时降低复杂度和提升可读性，而不是重新制造熵增。

## Deferred Items

这轮不纳入：

- `target` 级但未到 block 的 77 个文件的全面收敛
- `Finding.fingerprint` 仍包含 `line` 带来的 ratchet 脆弱性调整
- 前端函数级自动化扫描
- 更细粒度的复杂度 trend 可视化或 dashboard
