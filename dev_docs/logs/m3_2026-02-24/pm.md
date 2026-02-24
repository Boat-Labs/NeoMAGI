# M3 Persistent Memory — PM 阶段汇总

> 日期：2026-02-24
> 状态：完成

## 1. Milestone 总结

M3 Persistent Memory 五个 Phase 全部通过 Gate 验收，milestone 可关闭。

| Phase | 交付 | Gate 结论 | Backend Commit | 修复轮次 | 测试增量 |
|-------|------|-----------|---------------|----------|---------|
| Phase 0: ToolContext + dmScope + Guardrail | ToolContext frozen dataclass, SessionIdentity + scope_resolver, RiskLevel enum, BaseTool.execute 签名扩展, CoreSafetyContract (pre-LLM warn-only, pre-tool fail-closed) | PASS | 2de958d | 1 | 213→316 (+103) |
| Phase 1: Memory Write Path | ResolvedFlushCandidate DTO, MemoryWriter (daily notes), MemoryAppendTool (risk=high), daily notes auto-load, flush candidate auto-persist | PASS | cc24a04 | 0 | 316→362 (+46) |
| Phase 2: Memory Index & Search | Alembic migration (memory_entries), tsvector + GIN index (ParadeDB fallback), MemoryIndexer (delete-reinsert), MemorySearcher (scope-aware), memory_search tool upgrade | PASS | 4ba2221 | 1 | 362→402 (+40) |
| Phase 3: Memory Curation + Prompt Recall | `_layer_memory_recall()`, `extract_recall_query()` keyword extraction, MemoryCurator (LLM-assisted), recall_results 注入 prompt_builder | PASS | 914a785 | 0 | 402→424 (+22) |
| Phase 4: Evolution Loop | soul_versions table + migration, EvolutionEngine (propose/evaluate/apply/rollback/veto/bootstrap/audit), SoulProposeTool/SoulStatusTool/SoulRollbackTool, AgentLoop bootstrap integration, e2e tests | PASS | 7c5e6c3 | 1 | 424→468 (+44) |

最终测试总数：468 tests，0 failures，ruff clean。
基线增长：213 → 468（+255 tests，+120%）。

## 2. 变更清单（文件级）

### 新增文件（37 个）

**源码（17 个）**
- `src/agent/guardrail.py` — CoreSafetyContract, pre-LLM/pre-tool 检查
- `src/memory/__init__.py` — memory 包入口
- `src/memory/contracts.py` — MemoryWriteContract 接口
- `src/memory/curator.py` — MemoryCurator, LLM-assisted curation
- `src/memory/evolution.py` — EvolutionEngine, SoulProposal, 完整 CRUD + audit
- `src/memory/indexer.py` — MemoryIndexer, tsvector 索引, delete-reinsert
- `src/memory/models.py` — MemoryEntry, SoulVersion 数据模型
- `src/memory/searcher.py` — MemorySearcher, scope-aware 搜索
- `src/memory/writer.py` — MemoryWriter, daily notes 写入
- `src/session/scope_resolver.py` — SessionIdentity, dmScope 解析
- `src/tools/builtins/memory_append.py` — MemoryAppendTool (risk=high)
- `src/tools/builtins/soul_propose.py` — SoulProposeTool
- `src/tools/builtins/soul_rollback.py` — SoulRollbackTool
- `src/tools/builtins/soul_status.py` — SoulStatusTool
- `src/tools/context.py` — ToolContext frozen dataclass
- `alembic/versions/d5e6f7a8b9c0_create_memory_entries.py` — memory_entries 表
- `alembic/versions/e6f7a8b9c0d1_create_soul_versions.py` — soul_versions 表

**测试（20 个）**
- `tests/test_agent_flush_persist.py` — flush candidate 持久化
- `tests/test_agent_tool_context.py` — ToolContext 传递
- `tests/test_base_tool.py` — BaseTool execute 签名
- `tests/test_evolution.py` — EvolutionEngine 单元测试
- `tests/test_guardrail.py` — CoreSafetyContract 单元测试
- `tests/test_memory_append_tool.py` — MemoryAppendTool
- `tests/test_memory_contracts.py` — MemoryWriteContract
- `tests/test_memory_curator.py` — MemoryCurator
- `tests/test_memory_indexer.py` — MemoryIndexer
- `tests/test_memory_models.py` — 数据模型验证
- `tests/test_memory_search_tool.py` — memory_search tool
- `tests/test_memory_searcher.py` — MemorySearcher
- `tests/test_memory_writer.py` — MemoryWriter
- `tests/test_prompt_builder.py` — prompt_builder 扩展
- `tests/test_prompt_daily_notes.py` — daily notes 注入
- `tests/test_prompt_memory_recall.py` — memory recall 注入
- `tests/test_scope_resolver.py` — scope_resolver
- `tests/test_settings.py` — MemorySettings 验证
- `tests/test_soul_tools.py` — Soul 工具集
- `tests/test_tool_context.py` — ToolContext 单元测试
- `tests/integration/test_evolution_e2e.py` — Evolution 端到端集成测试
- `tests/integration/test_memory_bm25.py` — BM25 搜索集成测试

### 修改文件（13 个）

- `src/agent/agent.py` — budget→compact→store→recall→bootstrap 完整集成
- `src/agent/prompt_builder.py` — daily_notes + recall_results + compacted_context 注入
- `src/config/settings.py` — MemorySettings 新增字段
- `src/infra/errors.py` — EvolutionError 异常类
- `src/tools/base.py` — BaseTool.execute 签名扩展 (context + risk_level)
- `src/tools/builtins/current_time.py` — context 参数适配
- `src/tools/builtins/memory_search.py` — scope-aware 搜索升级
- `src/tools/builtins/read_file.py` — context 参数适配
- `tests/conftest.py` — integration fixture 增强 (memory_entries truncate)
- `tests/integration/test_tool_loop_flow.py` — EchoTool/FailingTool 签名对齐
- `tests/test_agent_tool_parse.py` — 适配新签名

总计：**50 files changed, 5,978 insertions(+), 40 deletions(−)**。

## 3. ADR 一致性

| ADR | 验证结果 |
|-----|---------|
| 0034 (dmScope session/memory scope alignment) | PASS — SessionIdentity + scope_resolver 完整实现，DM 与 group 范围隔离 |
| 0035 (runtime guardrail hardening + risk-gated fail-closed) | PASS — CoreSafetyContract pre-LLM warn-only + pre-tool fail-closed，RiskLevel 三级分层 |

## 4. Gate 验收报告索引

| Gate | 报告 | 首次结论 | 最终结论 |
|------|------|---------|---------|
| G-M3-P0 | `dev_docs/reviews/m3_phase0_2026-02-24.md` | FAIL (EchoTool 签名) | PASS (8/8) |
| G-M3-P1 | `dev_docs/reviews/m3_phase1_2026-02-24.md` | PASS (5/5) | PASS |
| G-M3-P2 | `dev_docs/reviews/m3_phase2_2026-02-24.md` | FAIL (conftest teardown) | PASS (7/7) |
| G-M3-P3 | `dev_docs/reviews/m3_phase3_2026-02-24.md` | PASS (7/7) | PASS |
| G-M3-P4 | `dev_docs/reviews/m3_phase4_2026-02-24.md` | PASS (10/10) → supplemental FAIL (lint) | PASS |
| G-M3-FINAL | PM 自检 | PASS | PASS (468 tests, ruff clean) |

## 5. 未完成项

无。所有 plan 交付物已完成。

ParadeDB `pg_search` BM25 索引为计划中的 R1 已知风险，当前使用 tsvector + GIN 作为 fallback，功能等价。后续 ParadeDB 就绪后可无缝切换（代码已预留接口）。

## 6. 过程经验

| 事件 | 影响 | 改进 |
|------|------|------|
| Phase 0 EchoTool/FailingTool 未适配新签名 | Tester 首次 review FAIL，增加一轮修复 | 规则：BaseTool 签名变更时，必须 grep 全量 fixture/test tools 并同步更新 |
| Phase 2 conftest teardown 引入 RuntimeError | 12 个 integration test 报错，Tester FAIL | 原因：`_integration_cleanup` autouse fixture 中 `getfixturevalue` 在 async 上下文调用同步 runner。修复：try/except 包裹 + 新增 memory_entries truncate |
| Phase 4 Backend 提交 supplemental commit 后 Tester review 时序交叉 | Tester 先完成 e02f3a3 review (PASS)，再 review 020a71e (FAIL on lint) | 改进：supplemental commit 应在 Tester 开始 review 前完成，避免 target 变更导致审查分裂 |
| Phase 4 lint 修复由 PM 直接完成 | Backend 已 shutdown，PM 在 worktree 直接修复 3 个 ruff 错误 | 可接受：trivial lint fix 不需要重新 spawn teammate |
| G-M3-FINAL 在 supplemental re-review 前过早关闭 | 需要重新打开 FINAL 验证 | 规则：FINAL gate 必须等待所有 supplemental commit 的 review 结果返回后才能关闭 |
| 合并 tester 分支时 test_evolution_e2e.py 冲突 | Tester 分支同步了 backend 代码导致 add/add 冲突 | 规则：Tester 分支只包含 review 报告，不应同步实现代码；或合并前明确以 backend 为准 |

## 7. 心跳日志

`dev_docs/logs/m3_2026-02-24/heartbeat_events.jsonl` — 47 条事件，覆盖完整生命周期。

## 8. Git 合并记录

| 操作 | Commit |
|------|--------|
| Backend 合并到 main | `41e12ef` (merge commit, --no-ff) |
| Tester 合并到 main | `0753046` (merge commit, --no-ff, 解决 1 个 add/add 冲突) |
| Main 最终 HEAD | `0753046` |
| 远程分支清理 | `feat/backend-m3-impl` + `feat/tester-m3-review` 已删除 |
| Worktree 清理 | `NeoMAGI-wt-backend-m3` + `NeoMAGI-wt-tester-m3` 已删除 |
