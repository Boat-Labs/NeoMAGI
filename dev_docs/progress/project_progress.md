---
doc_id: 019d66f0-d728-79df-bb73-8c7284a1603c
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-07T09:55:53+02:00
---
# Project Progress

## 2026-02-16 18:34 (local) | M0
- Status: done
- Done: 治理框架落地，完成 17 项 ADR 决策记录和索引
- Evidence: commit 7bc1588..496eb90, decisions/INDEX.md
- Next: 进入 M1.1 基础交互闭环实现
- Risk: 无

## 2026-02-16 20:27 (local) | M1.1
- Status: in_progress
- Done: 基础交互闭环实现完成 — Gateway WebSocket RPC + Agent Runtime (PromptBuilder/ModelClient/AgentLoop) + Session 内存管理 + WebChat 前端 (WS 连接/消息流式渲染)
- Evidence: commit 7c808a7..77b0348, merge commits 103bffb (backend) + ef96164 (frontend)
- Next: 进入 M1.2 任务完成闭环
- Risk: 无

## 2026-02-16 20:45 (local) | M1.2
- Status: in_progress
- Done: 任务完成闭环实现完成 — BaseTool ABC + ToolRegistry + 3 个内置工具 (current_time/memory_search/read_file) + Tool Call Loop + Prompt Files 优先级加载 + 前端 Tool Call UI 折叠展示 + 多轮 UX 增强
- Evidence: commit 5234051..be6c18b, merge commits a8eb254 (backend) + aeb8bfb (frontend)
- Next: 进入 M1.3 稳定性闭环
- Risk: 无

## 2026-02-16 21:24 (local) | M1.3
- Status: in_progress
- Done: 稳定性闭环实现完成 — structlog 日志 + 自定义异常层次 + LLM 指数退避重试 + 网关统一错误响应 + PostgreSQL Session 持久化 (SQLAlchemy async + Alembic) + 前端断线重连 + 错误 Toast + 历史消息加载
- Evidence: commit e8357b5..8f2c6ad, merge commits bbf3359 (backend) + f3dd51f (frontend)
- Next: M1 实现全部完成，进入 review 阶段
- Risk: 无

## 2026-02-17 (local) | M1.1
- Status: in_progress
- Done: M1.1 实现评审完成，结论为"条件通过，有阻塞待修"；发现 3 项问题：F1 DB schema 配置冲突(HIGH)、F2 tool call 参数解析缺保护(HIGH)、F3 历史消息未过滤 system/tool(MEDIUM)
- Evidence: dev_docs/reviews/phase1/m1.1_implementation-review_2026-02-17.md
- Next: 按优先级修复 F1 → F2 → F3
- Risk: F1 导致默认配置下 DB 静默退化为内存模式，影响持久化稳定性

## 2026-02-18 08:44 (local) | M1.1
- Status: in_progress
- Done: 评审问题修复 v3 完成 — F1: DB_SCHEMA 常量统一 + validator + fail-fast + ADR 0017 对齐；F2: _safe_parse_args 双层 dict 校验；F3: chat.history 过滤 system/tool 消息；含 3 个测试文件
- Evidence: commit 01b1085 (F1) + 472a6a1 (F2) + 3103e1c (F3), dev_docs/plans/phase1/m1.1_review-fixes_2026-02-17_v3.md
- Next: v3 实现的 code review
- Risk: 无

## 2026-02-18 13:12 (local) | M1.1
- Status: in_progress
- Done: v3 code review 发现 2 项问题并修复 — R1(HIGH): 日志行 `tc.function.arguments[:200]` 对 None 崩溃，用 `str(...)[:200]` 修复；R2(LOW): 5 个测试文件未使用导入清理
- Evidence: commit 2cf91a1 (R1) + b94a6d3 (R2), dev_docs/plans/phase1/m1.1_review-fixes_2026-02-18_v4.md
- Next: M1.1 待最终确认通过；M1.2/M1.3 待评审
- Risk: M1.2 和 M1.3 尚未经过独立评审

## 2026-02-18 22:30 (local) | M1.1
- Status: done
- Done: M1.1 focused re-review 通过 — F1/F2/F3 及 v4 回归修复验证完毕，28 tests passed，ruff clean；附带发现 .gitignore P1 已独立修复（docs/ → dev_docs/ 迁移，commit 2bd614a）
- Evidence: 28 tests passed (uv run pytest tests/test_config_schema.py tests/test_agent_tool_parse.py tests/test_session_history_filter.py -v), commit 2bd614a
- Next: M1.2 评审
- Risk: 无

## 2026-02-18 23:50 (local) | M1.2
- Status: done
- Done: M1.2 深审完成，6 项发现（F1-F6）；F2/F3 已在 M1.1 修复周期中解决；本轮修复 F1(P1) read_file `startswith` 边界绕过（换 `is_relative_to` + 类型校验）、F5(P2) model_client `choices[]` 空防护（`_first_choice` + stream 跳过）、F6a(P3) 补齐安全测试（10 组 read_file + 5 组 model_client）；F4(P2) 流式回退和 F6b(P3) 集成测试延至 M1.4（owner=backend, due=2026-03-04）
- Evidence: commit 1e48eed (F1) + 5360c80 (F5) + 3078a69 (F6a-1) + 00b0bfb (F6a-2), 43 tests passed, ruff clean, dev_docs/plans/phase1/m1.2_audit-fixes_2026-02-18.md
- Next: M1.3 评审
- Risk: F4 流式回退为已知体验回退，已排入 M1.4 跟踪

## 2026-02-18 (local) | M1.3
- Status: done
- Done: M1.3 评审修复完成并合入 main
- Detail:
  - R1(P1): DB-level atomic seq allocation (`INSERT ON CONFLICT RETURNING`)；persist-first-then-memory 模式防 ghost messages；session lease lock (UUID lock_token + configurable TTL)；SESSION_BUSY RPC 错误；force reload for cross-worker handoff
  - R2(P1): 移除 `allow_memory_fallback` escape hatch，DB 为硬依赖 (Decision 0020)
  - R5(P2): 前端全量替换替代 dedup merge；`isHistoryLoading` 状态 + 3 路清理；`sendMessage` 返回 boolean 控制输入框
  - R6(P3): 21 个新测试 — persistence(5) + serialization(9) + history contract(3) + config validation(4)
  - Review round 1 fixes: chat.history 强制 DB reload (P1)；迁移加约束前去重 (P1)；AsyncMock 协程警告消除 (P3)
  - Review round 2 fixes: ruff lint 全绿 (E501/I001/F401/F841)
- Evidence: PR `feat/session-m1.3-review-fixes` merged to main, 64 tests passed, 0 warnings, ruff clean, pnpm build 通过
- Plan: dev_docs/plans/phase1/m1.3_review-fixes_2026-02-18.md
- Decisions: ADR 0019 + 0020 + 0021 + 0022
- Next: 进入 M1.4（审计修复收尾）
- Risk: P2 follow-up 待 M1.4 前置完成 — conftest.py PG fixture + 3 条集成测试 (claim/release, seq 原子分配, force reload) + CI PostgreSQL job

## 2026-02-19 (local) | M1.4
- Status: done
- Done: 审计修复收尾 + 测试基础设施，7 项 task 全部完成
- Detail:
  - T1: PG 集成测试基础设施 — testcontainers-python session-scoped fixture + conftest + 6 组集成测试 (CRUD, seq atomic, claim/release, TTL reclaim, force reload, fencing)
  - T2: CI 落地 — GitHub Actions workflow (unit + integration + frontend) + justfile test commands
  - T3: 前端 vitest 基础设施 — vitest + jsdom + zustand store 10 组测试
  - T4/R1: history 请求超时兜底 — 10s setTimeout guard + 2 组 fake timer 测试
  - T5/R2: lock fencing — SessionFencingError + `_persist_message` ON CONFLICT WHERE 原子 token 校验
  - T6/F4: 全程流式 — ContentDelta/ToolCallsComplete/StreamEvent 类型 + chat_stream_with_tools 替代 chat_completion + delta 聚合
  - T7/F6b: WebSocket + tool loop flow 集成测试 — 12 组 (streaming chat, history, tool loop, SESSION_BUSY, unknown method, invalid JSON, single/multi-round tool calls, mixed content, tool failure, max iterations, fencing mid-loop)
  - Review fixes: SESSION_BUSY 实测、unawaited coroutine 消除、PARSE_ERROR 语义对齐
- Evidence: 8 commits on feat/m1.4-audit-test-infra, 82 tests passed (64 unit + 18 integration), 10 frontend tests passed, ruff clean
- Plan: dev_docs/plans/phase1/m1.4_audit-test-infra_2026-02-18.md
- Next: M1 审计全部完成，进入 M2 规划
- Risk: 无

## 2026-02-20 (local) | M1.5
- Status: in_progress
- Done: roadmap v3 与决议拆分完成（ADR 0023 + 0024），并完成 architecture 文档体系重组（M1 总结 + M1.5~M6 计划 + design_docs/index.md）；新增 ADR 0025 明确 mode 切换权与 M1.5 固定 `chat_safe` 边界
- Evidence: commit 912bac7, `design_docs/phase1/roadmap_milestones_v3.md`, `decisions/0023-roadmap-product-oriented-boundary.md`, `decisions/0024-m1.5-tool-modes-and-priority-reorder.md`, `decisions/0025-mode-switching-user-controlled-chat-safe-default.md`
- Next: 按 ADR 0025 推进 M1.5（Tool Modes）详细方案与实现（固定 `chat_safe`，`coding` 预留）
- Risk: 无

## 2026-02-20 (local) | M1.5
- Status: in_progress
- Done: M1.5 Tool Modes 主体实现交付 — dual-gate mode 授权框架（暴露闸门 + 执行闸门）、ToolGroup/ToolMode enum、BaseTool fail-closed defaults、ToolRegistry mode-aware filtering/override/check_mode、3 个 builtin 工具 metadata 声明、AgentLoop 执行闸门 ToolDenied 路径、PromptBuilder mode-filtered tooling + safety layer、SessionManager.get_mode fail-closed + M1.5 guardrail、SessionSettings config validation、前端 tool_denied WebSocket 消息处理与 UI
- Evidence: commit e0759b2..0a555b1, 123 unit tests + 24 integration tests + 13 frontend tests passed, ruff clean
- Plan: dev_docs/plans/phase1/m1.5_tool-modes_2026-02-20.md
- Decisions: ADR 0025 + 0026
- Next: code review 后修复发现的问题
- Risk: code review 发现 4 项问题待修

## 2026-02-21 (local) | M1.5
- Status: done
- Done: M1.5 review-fixes 验收通过 — 修复 code review 发现的 4 项问题
- Detail:
  - P1: 前端 tool_denied 双状态 — tool_denied handler 从 append 改为 call_id findIndex update-or-insert；done handler 对 denied 状态做 preserve 而非覆写 complete
  - P1: 未注册工具误分类 — agent.py gate 条件增加 `registry.get(name) is not None` 前置检查，未知工具跳过 mode gate 直接走 `_execute_tool` 的 UNKNOWN_TOOL 路径
  - P2: structlog 测试恒真断言 — 从 caplog + `len(caplog.records) >= 0` 换为 `structlog.testing.capture_logs`
  - P3: M1.5 milestone 日志 — 创建 `dev_docs/logs/phase1/m1.5_2026-02-21/developer.md`
- Evidence: commit cb3c4d3..5e53407 (merge), 123 unit tests + 26 integration tests + 16 frontend tests passed, ruff clean
- Plan: dev_docs/plans/phase1/m1.5_review-fixes_2026-02-21.md
- Next: 进入 M2（会话内连续性）规划
- Risk: 无

## 2026-02-22 (local) | M2
- Status: in_progress
- Done: M2 会话内连续性主体实现交付 — TokenCounter (tiktoken exact + fallback estimate) + BudgetTracker (三区间: ok/warn/compact_needed) + CompactionEngine (rolling summary + anchor validation ADR 0030 + memory flush ADR 0032) + AgentLoop compaction 集成 (budget check → compact → store → rebuild) + SessionManager watermark-aware get_effective_history
- Evidence: commit 48b60d1..34867c5, merged to main
- Next: post-review fixes
- Risk: 无

## 2026-02-22 (local) | M2
- Status: in_progress
- Done: M2 post-review fixes 完成 — 6 项 Finding + 2 项 P2 follow-up 全部修复并验证
- Detail:
  - F1: Post-compaction budget recheck + overflow → emergency trim → fail-open chain
  - F2: Overflow 集成测试覆盖 recheck/emergency trim/store-rebuild-recheck/fail-open 路径
  - F3: Wire summary_temperature from CompactionSettings into LLM call
  - F4: Add compact_timeout_s / flush_timeout_s to CompactionSettings with Pydantic validation
  - F5: UTF-8 byte-safe truncation in memory flush (CJK 无中断截断)
  - F6: Strict 30% summary token cap + degraded path for small inputs
  - P2-1: Remove early-exit when min_preserved_turns=1, always attempt emergency trim
  - P2-2: Strengthen F2 test assertions with spy verification + model-not-called checks
- Evidence: PR #3 (fix/m2-post-review-fixes), 258 tests passed, ruff clean
- Plan: dev_docs/plans/phase1/m2_post-review-fixes_2026-02-22.md
- Next: PR merge 后进入 M2 验收或 M3 规划
- Risk: 无

## 2026-02-22 (local) | M2
- Status: done
- Done: M2 会话内连续性全部完成 — PR #3 merged to main，主体实现 + post-review fixes 合入
- Evidence: commit 6c33bfa (merge), 258 tests passed, ruff clean
- Next: 进入 M3（持久记忆）规划
- Risk: 无

## 2026-02-23 (local) | M3
- Status: in_progress (planning done, implementation pending)
- Done: M3 持久记忆实现计划完成并审批（rev6）— 5 Phase 拆分（Phase 0: ToolContext + dmScope 基础设施 → Phase 1: Memory Write Path → Phase 2: BM25 Index & Search → Phase 3: Memory Curation + Prompt Recall → Phase 4: Evolution Loop）；dmScope 策略对齐 roadmap 与 architecture；ADR 0034 落地
- Evidence: commit f2a69b8, `dev_docs/plans/phase1/m3_persistent-memory_2026-02-22.md` (status: approved)
- Plan: dev_docs/plans/phase1/m3_persistent-memory_2026-02-22.md
- Decisions: ADR 0034 (dmScope)
- Next: 按 Phase 0 → 1 → 2 → 3 → 4 顺序推进 M3 实现；Phase 2 前需完成 ParadeDB pg_search spike 验证
- Risk: ParadeDB pg_search tokenizer 兼容性待 spike 验证

## 2026-02-23 (local) | M3
- Status: in_progress (Phase 0 guardrail hardening gate)
- Done: 启动 M2 风险回补并形成决议草案 ADR 0035（运行时最小反漂移防护）；同步更新 roadmap + m2/m3 architecture，将”Core Safety Contract guard + 风险分级 fail-closed”设为 M3 Phase 0 前置门槛
- Reopened:
  - R1(P1): 现有 compaction 锚点校验强度不足（首行探针级），无法覆盖关键约束失真场景；从 M2 结项残余风险重新开放，转入 M3 Phase 0 必修
  - R2(P1): guard 失败后高风险路径仍可能沿 fail-open 继续执行；重新开放为执行闸门问题（高风险工具需 fail-closed）
  - R3(P2): 反漂移证据以离线评估为主，缺少运行时强制防护；重新开放为”验收口径与运行时口径对齐”任务
- Evidence: working tree updates — `decisions/0035-runtime-anti-drift-guardrail-hardening-and-risk-gated-fail-closed.md`, `decisions/INDEX.md`, `design_docs/phase1/roadmap_milestones_v3.md`, `design_docs/phase1/m2_architecture.md`, `design_docs/phase1/m3_architecture.md`
- Plan: dev_docs/plans/phase1/m3_persistent-memory_2026-02-22.md（Phase 0 增补 ADR 0035 最小防护任务）
- Decisions: ADR 0035 (proposed)
- Next: 按 Phase 0~4 推进 M3 实现
- Risk: 若 Phase 0 未先完成该防护，M3 后续记忆写入/召回链会放大误执行风险并增加返工成本

## 2026-02-24 (local) | M3
- Status: in_progress
- Done: M3 持久记忆 5 Phase 全部通过 Gate 验收（Agent Teams PM 协调）
- Detail:
  - Phase 0: ToolContext + dmScope + Guardrail — CoreSafetyContract, RiskLevel enum, pre-LLM/pre-tool 检查
  - Phase 1: Memory Write Path — MemoryWriter, MemoryAppendTool, daily notes auto-load, flush persist
  - Phase 2: Memory Index & Search — Alembic migration, tsvector + GIN index, MemoryIndexer, MemorySearcher
  - Phase 3: Memory Curation + Prompt Recall — MemoryCurator (LLM-assisted), recall layer, keyword extraction
  - Phase 4: Evolution Loop — soul_versions table, EvolutionEngine (propose/evaluate/apply/rollback/veto/bootstrap/audit), Soul tools
- Evidence: 468 tests passed, ruff clean; PM 报告 `dev_docs/logs/phase1/m3_2026-02-24/pm.md`
- Decisions: ADR 0034, 0035
- Next: 用户审阅后进入 post-review 修正
- Risk: 网关接线、搜索触发器、Evolution 一致性等审阅发现待修

## 2026-02-24 (local) | M3
- Status: done
- Done: M3 post-review 3 轮修正全部闭合，milestone 关闭
- Detail:
  - Round 1 (28d54f1): P0 网关接线（7 工具注册 + 依赖注入）、P1 搜索触发器 DDL、P1 Evolution commit 失败补偿、P1 Curator 空输出防护、P2 装配测试、P3 PM 报告修正
  - Round 2 (7836a50): P1 ensure_schema 显式导入 memory models、P1 补偿覆盖全部 DB 异常、P2 双层 try/except 结构化日志、P3 路径 .resolve() 规范化
  - Round 3 (8585be2): P2 补偿日志断言（mock logger 验证）、P2 rollback 对称失败路径测试
- Evidence: 481 tests passed, ruff clean; commit 2cbd3c4 (closure)
- Plan: dev_docs/plans/phase1/m3_post-review-fix_2026-02-24.md (approved + executed)
- Decisions: ADR 0036 (Evolution DB-SSOT + 投影对账), 0037 (workspace_path 单一真源)
- Next: 进入 M6（模型迁移验证）
- Risk: 无；ParadeDB pg_search BM25 为已知 R1 风险，当前 tsvector + GIN fallback 功能等价

## 2026-02-25 00:40 (local) | M3
- Status: done
- Done: M3 收尾后完成两项紧急稳定性修补——修复 legacy DB `sessions` 缺列导致启动失败、修复历史中断裂 tool_call 链导致 OpenAI 400（`tool_calls must be followed by tool messages`）
- Evidence: `src/session/database.py`, `src/agent/agent.py`, `tests/test_ensure_schema.py`, `tests/test_agent_tool_parse.py`, `tests/test_compaction_degradation.py`, `uv run pytest tests/test_agent_tool_parse.py tests/test_compaction_degradation.py -q` (23 passed), `uv run pytest tests/test_ensure_schema.py -q -m integration` (2 passed)
- Next: 复测 M3 用户测试指导中的 T03/T04/T05 长链路并确认不再复现 400
- Risk: 历史污染会话在旧版本残留场景下仍可能需要新会话复测一次；新版本已在发送前做 tool_call 历史清洗兜底

## 2026-02-25 13:56 (local) | M3
- Status: done
- Done: 进度口径消歧义完成——M3 修复项已完成并验证；T03/T04/T05 复测定义为收尾验收任务，不阻塞 M6 规划启动
- Evidence: commit 0935834, commit 178882c, `dev_docs/plans/phase1/m3_post-review-fix_2026-02-24.md`, `dev_docs/cases/runtime_casebook.md`
- Next: 启动 M6 规划，同时并行完成 T03/T04/T05 手工复测并回填结果
- Risk: 若复测出现回归，需先完成 M3 hotfix 闭环再进入 M6 实施

## 2026-02-25 14:04 (local) | M3
- Status: done
- Done: 用户手工复测完成——T03/T04/T05 全部通过；仅保留 1 条已知检索未命中 case（记忆数据存在但自然语句词法检索 miss）
- Evidence: `design_docs/phase1/m3_user_test_guide.md` (T03/T04/T05), `dev_docs/cases/runtime_casebook.md` (RC-2026-02-25-001, status=deferred), 用户手工验证结果
- Next: 启动 M6 规划（模型迁移验证），并在后续统一检索能力优化中处理 RC-2026-02-25-001
- Risk: 已知 case RC-2026-02-25-001 当前为 deferred，不影响 M6 规划启动

## 2026-02-25 (local) | M6
- Status: in_progress
- Done: M6 模型迁移验证主体实现交付（Agent Teams PM 协调，4 Phase Gate 全通过）— GeminiSettings + ProviderSettings 配置体系、AgentLoopRegistry per-provider 预建、BudgetGate PG atomic reserve/settle、per-run provider routing (ChatSendParams.provider)、Curator model 参数化、eval 脚本 WebSocket 全链路验证
- Detail:
  - Phase 0: 配置 + ADR — GeminiSettings, ProviderSettings (pydantic-settings), ADR 0038/0040/0041
  - Phase 1: AgentLoopRegistry + provider routing — 启动时预建 per-provider AgentLoop, params.provider > PROVIDER_ACTIVE
  - Phase 2: BudgetGate + Gemini smoke test — PG atomic reserve/settle, CAS idempotent, Gemini API 端到端验证
  - Phase 3: Eval 脚本 + 迁移结论 — WebSocket 客户端走 Gateway 全链路, T10-T16 双 provider 评测
- Evidence: commit fbae58d..d76d0d0, 540 tests passed, ruff clean; eval results: OpenAI 7/7 PASS, Gemini 6/7 PASS (T13 长上下文 FAIL)
- Plan: `dev_docs/plans/phase1/m6_model-migration-validation_2026-02-25.md`
- Decisions: ADR 0038, 0040, 0041
- Next: 用户审阅发现 2 项 P1 问题待修
- Risk: P1-1 BudgetGate 未接入 chat.send 主链路; P1-2 eval T11/T12 判定过宽（false positive）

## 2026-02-26 (local) | M6
- Status: done
- Done: M6 P1 修复完成并合入 main — 2 项 P1 问题修复 + eval 全量重跑 + 迁移结论更新
- Detail:
  - P1-1: ADR 0041 BudgetGate 接入 chat.send 主链路 — try_reserve (固定 €0.05) after session claim, settle in finally block, BUDGET_EXCEEDED error code, settle 失败结构化日志
  - P1-2: eval T11/T12 判定口径收紧 — 未触发目标工具即 FAIL（消除 false positive）
  - 测试基础设施: StubBudgetGate 适配 3 个现有集成测试 fixture; 新增 17 unit tests (test_budget_gate_wiring.py) + 5 E2E tests (test_budget_gate_e2e.py) + 6 eval judgment tests (test_eval_judgment.py)
  - Eval 全量重跑: OpenAI 7/7 PASS, Gemini 6/7 PASS (T13 不变); 预算审计 52 reservations, 全部 settled, €2.60 cumulative
- Evidence: PR #4 (feat/m6-p1-fix) merged to main, commit bc303e7; 562 tests passed, ruff clean
- Plan: `dev_docs/plans/phase1/m6_p1-fix-budget-gate-and-eval_2026-02-25.md`
- Reports: `dev_docs/reports/phase1/m6_eval_openai_1772064537.json`, `dev_docs/reports/phase1/m6_eval_gemini_1772064602.json`, `dev_docs/reports/phase1/m6_migration_conclusion.md`
- Next: M6 关闭，参考 roadmap_milestones_v3.md 确定下一阶段
- Risk: Gemini T13 长上下文 + 工具历史场景 400 INVALID_ARGUMENT 为已知限制，可通过 compaction 阈值调优缓解

## 2026-03-01 (generated) | M7
- Status: done
- Done: 最新 gate G-M7-P6-POS 为 closed (PASS)；backend=working, tester=done
- Evidence: `dev_docs/logs/phase1/m7_2026-03-01/gate_state.md`, `dev_docs/logs/phase1/m7_2026-03-01/watchdog_status.md`, `dev_docs/reviews/phase1/m7_phase6_2026-03-01.md` (0a300c2)
- Next: G-M7-P6-POS 已关闭，等待 M7 下一条 gate
- Risk: 无

## 2026-03-01 (local) | M7
- Status: done
- Done: M7 收尾完成并关闭——beads control plane、`scripts/devcoord` 直接入口、Claude Code project skills、PM-first / teammate cutover、幂等护栏、`target_commit` preflight 与 canonical full SHA 规范已全部落地；`coord.py` 已按职责机械拆分为 `model.py` / `service.py` / 薄入口 `coord.py`
- Evidence: `dev_docs/reviews/phase1/m7_summary_2026-03-01.md`, `dev_docs/reviews/phase1/m7_phase6_2026-03-01.md`, `dev_docs/logs/phase1/m7_2026-03-01/gate_state.md`
- Next: 按 `design_docs/phase1/roadmap_milestones_v3.md` 进入 M4（第二渠道适配）；M5 继续保持触发式进入
- Risk: M7 residual risks 已记录于 `dev_docs/reviews/phase1/m7_summary_2026-03-01.md`，不阻塞关闭

## 2026-03-02 (local) | M4
- Status: done
- Done: M4 Telegram 第二渠道适配全部完成（Agent Teams PM 协调，5 Phase Gate 全通过）— TelegramSettings + scope_resolver 激活、dispatch_chat channel-agnostic 调度核心、TelegramAdapter (aiogram 3.x) DM 适配、telegram_render 消息拆分/格式化/错误映射、跨渠道隔离测试 + Use Case A/B/C 验收 + ADR 0044 归档
- Detail:
  - Phase 0: Config + Scope 激活 — TelegramSettings (pydantic-settings), scope_resolver per-channel-peer 分支, SessionSettings.dm_scope 锁定 main
  - Phase 1: Dispatch 抽取 + Identity — dispatch_chat(), session_id/scope_key 分离, AgentLoop identity+dm_scope 参数
  - Phase 2: Telegram Adapter — aiogram 3.x, check_ready/start_polling/stop, 鉴权门控 (fail-closed), Gateway lifespan 集成
  - Phase 3: Response Rendering — split_message (代码块保护), format_for_telegram (MarkdownV2 + 回退), 错误消息中文映射
  - Phase 4: E2E + Docs + ADR — test_channel_isolation.py (11 tests), ADR 0044, 文档更新
- Evidence: 668 tests passed, ruff clean; merge commit 549253f; PM 报告 `dev_docs/logs/phase1/m4_2026-03-02/pm.md`; 验收报告 `dev_docs/reviews/phase1/m4_phase{0-4}_2026-03-02.md`
- Plan: `dev_docs/plans/phase1/m4_telegram-channel-integration_2026-03-02.md`
- Decisions: ADR 0044 (Telegram adapter aiogram same-process)
- Resilience: 经历 tmux 崩溃 + team 重建 + agent respawn 恢复，验证了 Agent Teams 韧性
- Next: 用户审阅发现 4 项问题 (2×P1, 1×P2, 1×P3) 待修
- Risk: F1 跨渠道 session 隔离非强约束; F2 超长代码块 Telegram 拒发

## 2026-03-02 (local) | M4 post-review fix
- Status: done
- Done: M4 post-review 4 项 Finding 全部修复，用户端到端测试全部通过
- Detail:
  - F1 [P1]: WS session_id channel prefix 隔离 — `CHANNEL_EXCLUSIVE_PREFIXES` 拦截 `telegram:` 和 `peer:` 前缀；ChatSendParams/ChatHistoryParams Pydantic validator；`_handle_chat_history` 补 ValidationError→GatewayError 映射
  - F2 [P1]: code block 超长单行 hard cut — `_split_code_block` 对 `line_len > effective` 的行做 `range(0, len(line), effective)` 分段，每段重新包围 fences
  - F3 [P2]: polling 异常触发进程退出 — `_on_polling_done` 提取为模块级函数，fatal error 时 `os.kill(SIGTERM)` fail-fast（非静默降级）
  - F4 [P3]: `message_max_length` 边界校验 — `Field(ge=1, le=4096)` 启动时 fail-fast
- Evidence: 686 tests passed, ruff clean; 用户 Telegram 端到端测试通过
- Plan: `dev_docs/plans/phase1/m4_post-review-fix_2026-03-02.md` (approved + executed)
- Resilience: M4 主体实施期间经历 tmux 崩溃导致全部 agent 进程丢失；借助 beads control plane 事件记录（gate state、phase progress、teammate ack），PM 在新会话中完成断点重建（recovery-check + state-sync-ok），所有 teammate 在新 worktree 恢复工作并顺利完成剩余 Phase，验证了 M7 devcoord 协作控制在灾难恢复场景下的实际有效性
- Next: M4 全部关闭；按 `design_docs/phase1/roadmap_milestones_v3.md` 确定下一阶段（M5 触发式进入）
- Risk: 无

<!-- devcoord:begin milestone=m5 -->
## 2026-03-04 (generated) | M5
- Status: done
- Done: 最新 gate m5-g2 为 closed (PASS)；backend=done, tester=done
- Evidence: `dev_docs/logs/phase1/m5_2026-03-04/gate_state.md`, `dev_docs/logs/phase1/m5_2026-03-04/watchdog_status.md`, `dev_docs/reviews/phase1/m5_g2_phase2_review.md` (e71ba67)
- Next: m5-g2 已关闭，等待 M5 下一条 gate
- Risk: 无
<!-- devcoord:end milestone=m5 -->

## 2026-03-05 (local) | M5 closeout
- Status: done
- Done: M5 收尾完成并关闭，post-review findings 全部修复并完成手工验证；同时新增 ADR 0046，将数据库基线从 PostgreSQL 16 升级到 PostgreSQL 17，并同步更新 CI、治理文档、设计文档与测试夹具
- Detail:
  - 可靠性修复：preflight 追加 `workspace/memory` 可写性检查；restore 在解压前清空 workspace、按配置路径执行恢复；`pg_restore` 已知跨主版本兼容噪音降级为 warning
  - 健康检查修复：provider runtime health 改为按 provider 独立追踪，覆盖非流式、流式创建阶段和 stream-phase 失败；`/health/ready` 输出 provider 级 unhealthy 项
  - 运维与文档修复：`just restore` / `just backup` 参数示例去掉错误的额外 `--`；补充 `design_docs/phase1/m5_user_test_guide.md`；M5 gate/log/progress 投影已入库
  - 基线升级：ADR 0046 生效，`AGENTS.md` / `CLAUDE.md` / `.github/workflows/ci.yml` / design docs / tests 全部切到 PostgreSQL 17
- Evidence: commit `1019b55`; `dev_docs/plans/phase1/m5_post-review-fix_2026-03-04.md`; `design_docs/phase1/m5_user_test_guide.md`; `decisions/0046-upgrade-database-baseline-to-postgresql-17.md`
- Validation: `just lint`; `just test` (845 passed, 3 existing warnings); `just test-frontend` (16 passed); 用户手工完成 M5 restore/preflight 验证并通过
- Next: M5 全部关闭；后续里程碑默认按 PostgreSQL 17 基线推进
- Risk: 无阻塞风险；全量测试仍有 3 个既有 RuntimeWarning，未由本轮引入

## 2026-03-06 (local) | Phase Transition
- Status: done
- Done: Phase 1 已归档收口，Phase 2 的产品 roadmap、技术架构和首个执行计划已建立；`project_progress.md` 继续保留为全局 append-only 总账，不按 phase 拆分或重命名
- Evidence: `design_docs/phase1/index.md`, `design_docs/phase2/index.md`, `dev_docs/plans/phase2/p2-m1a_growth-governance-kernel_2026-03-06.md`
- Next: Phase 2 默认先读取 `design_docs/phase2/` 与 `dev_docs/plans/phase2/`，再按需回溯全局进度账本
- Risk: 若把 `project_progress.md` 当作 Phase 2 默认必读，会重新引入 Phase 1 上下文污染

<!-- devcoord:begin milestone=p2-m1a -->
## 2026-03-06 (generated) | P2-M1A
- Status: done
- Done: 最新 gate p2m1a-g0 为 closed (PASS)；backend=done, tester=done
- Evidence: `dev_docs/logs/phase2/p2-m1a_2026-03-06/gate_state.md`, `dev_docs/logs/phase2/p2-m1a_2026-03-06/watchdog_status.md`, `dev_docs/reviews/phase2/p2-m1a_phase0+1_2026-03-06.md` (fb5ecefaef5e32a358bb2448ba1bb940bdb5dae9)
- Next: p2m1a-g0 已关闭，等待 P2-M1A 下一条 gate
- Risk: 无
<!-- devcoord:end milestone=p2-m1a -->

## 2026-03-07 (local) | P2-M1a closeout
- Status: done
- Done: P2-M1a 显式成长治理内核全部完成（Agent Teams PM 协调，3 Phase Gate 全通过）— GrowthObjectKind/GrowthProposal 类型体系、PolicyRegistry (soul=onboarded, 4 reserved)、GrowthGovernanceEngine fail-closed 编排、GovernedObjectAdapter Protocol + SoulGovernedObjectAdapter thin wrapper、ADR 0049 adapter-first 决策
- Detail:
  - Phase 0+1: 类型 + 策略 + 引擎 + 适配器契约 — GrowthObjectKind, GrowthLifecycleStatus, GrowthProposal, PolicyRegistry, GrowthGovernanceEngine, GovernedObjectAdapter Protocol, UnsupportedGrowthObjectError
  - Phase 2: Soul 适配器 — SoulGovernedObjectAdapter thin wrapper over EvolutionEngine, GrowthProposal→SoulProposal 转换, EvalResult→GrowthEvalResult 转换
  - Phase 3: 集成测试 + ADR — 68 tests 全覆盖, ADR 0049 adapter-first 决策
  - Post-review fixes: cross-kind mismatch guard (UnsupportedGrowthObjectError), proposed_by→created_by 穿透审计链路, SoulProposal 新增 created_by 字段 (默认 "agent" 向后兼容)
- Evidence: 916 tests passed, ruff clean; commit 711465a (post-review fix); PM 报告 `dev_docs/logs/phase2/p2-m1a_2026-03-06/pm.md`; gate 记录 `dev_docs/logs/phase2/p2-m1a_2026-03-06/gate_state.md`
- Plan: `dev_docs/plans/phase2/p2-m1a_growth-governance-kernel_2026-03-06.md`
- Decisions: ADR 0049 (growth-governance-kernel-adapter-first)
- Baseline: 845 → 916 tests (+71, +8.4%); 11 new files, ~1,050 insertions
- Next: P2-M1a 全部关闭；按 `design_docs/phase2/roadmap_milestones_v1.md` 确定下一阶段
- Risk: 无

## 2026-03-07 (local) | P2-Devcoord Stage A
- Status: done
- Done: Devcoord Stage A（`CoordStore` 抽象层）已完成、验收通过并落地 — `CoordStore` seam 建立、`BeadsCoordStore / MemoryCoordStore` 落位、`CoordService` 脱离 `IssueStore / IssueRecord` 中心语义、CLI 对外行为保持不变
- Track: 并行开发流程修复轨；不属于 `P2-M*` 产品里程碑序列
- Evidence: `dev_docs/plans/phase2/p2-devcoord-stage-a_coordstore-abstraction_2026-03-07.md`
- Next: 进入 `Stage B` 计划审阅与开工准备
- Risk: 无

## 2026-03-07 (local) | P2-Devcoord Stage B
- Status: done
- Done: Devcoord Stage B（SQLite 后端与 Render/Audit 切换）已完成并验收通过 — `.devcoord/control.db` 落地为 control-plane 真源，`SQLiteCoordStore` 覆盖 milestone / phase / gate / role / message / event 六类记录，CLI 支持 `sqlite` / `beads` / `auto` 选路，`render` / `audit` 在 SQLite fresh-start 路径下可完整运行，`close_milestone` 契约回归已修复
- Track: 并行开发流程修复轨；不属于 `P2-M*` 产品里程碑序列
- Evidence: 相关提交 `341b0da` / `75bfbeb` / `9427bb6`；`uv run pytest -q tests/test_devcoord.py` 61 passed；全量 955 passed；计划 `dev_docs/plans/phase2/p2-devcoord-stage-b_sqlite-backend_2026-03-07.md`
- Next: 进入 `Stage C` 命令面精简的计划与实施准备
- Risk: 保留 fresh-start-only 的 SQLite schema 约束；已有 v1 `.devcoord/control.db` 需删除后按 v2 重新初始化

## 2026-03-07 (local) | P2-Devcoord Stage C
- Status: done
- Done: Devcoord Stage C（Grouped CLI Surface）已完成并验收通过 — 17 个 flat 顶层命令收敛为 `init / gate / command / event / projection / milestone / apply` 分组结构，argv normalization 实现 flat alias 透明兼容，`_PAYLOAD_BUILDERS` 替代 200+ 行 if/elif dispatch，`command send --name PING` 成为 ping 的 canonical grouped path，三个 devcoord skill narrative 切到 grouped CLI，payloads.md 保持 apply 机器入口不变
- Track: 并行开发流程修复轨；不属于 `P2-M*` 产品里程碑序列
- Evidence: commit `7b3a60a`；merge `eaa56dd`；`uv run pytest -q tests/test_devcoord.py` 96 passed；全量 990 passed；计划 `dev_docs/plans/phase2/p2-devcoord-stage-c_grouped-cli-surface_2026-03-07.md`
- Next: 进入 `Stage D` beads cutover / closeout hardening 的计划与实施准备
- Risk: 无

## 2026-03-07 (local) | P2-Devcoord Stage D
- Status: done
- Done: Devcoord Stage D（beads cutover / closeout hardening）已完成并验收通过 — `scripts/devcoord` steady-state runtime 已 hard cutover 到 SQLite-only，`BeadsCoordStore`、legacy beads path/flags 与 dual-backend 心智全部退役；`.devcoord/control.db` 成为唯一 control-plane SSOT，active governance docs / skills / runbook 已统一切到 SQLite 口径，devcoord control-plane 写入不再触发 beads sync
- Track: 并行开发流程修复轨；不属于 `P2-M*` 产品里程碑序列
- Evidence: implementation `00d09ae`；merge `25bd2c1`；计划 `dev_docs/plans/phase2/p2-devcoord-stage-d_beads-cutover-closeout-hardening_2026-03-07.md`；runbook `dev_docs/devcoord/sqlite_control_plane_runtime.md`
- Validation: 全量 984 tests passed；retired flags fail-fast、split-brain guard、SQLite-only closeout 路径与 active doc cutover 已覆盖
- Next: 执行独立的 SQLite control-plane 达成性验证，确认协议语义、事务原子性与 smoke evidence 全部闭环
- Risk: 无阻断风险；设计达成性仍需单独验证出结论

## 2026-03-08 (local) | P2-Devcoord SQLite Validation
- Status: done
- Done: Devcoord SQLite control-plane 达成性验证完成，结论为 `ACHIEVED` — 补齐 `PROTO-07` 的 `STOP / WAIT / RESUME / PING` command-send 覆盖、`PROTO-08` 的 ACK / gate-close 单事务原子性、`STA-03` 的 `journal_mode=WAL` 与 `busy_timeout` 校验、`PROJ-04` projection 篡改后重建验证，以及 `CLI-05` runtime docs 对 17 个 grouped commands 的 help smoke
- Track: 并行验证轨；不属于 `P2-M*` 产品里程碑序列
- Evidence: commit `b256d23`；计划 `dev_docs/plans/phase2/p2-devcoord_sqlite-control-plane-validation_2026-03-07.md`
- Validation: `tests/test_devcoord.py` 126 passed（93 existing + 33 new）；临时 clone 中的 `E2E-01` / `E2E-02` / `CUT-01` / `CUT-02` / `CUT-03` smoke 全部通过
- Next: Devcoord SQLite cutover 轨道可视为 achieved 并转入常规维护
- Risk: 无

## 2026-03-08 (local) | Complexity Governance
- Status: done
- Done: 复杂度治理正式落地 — ADR 0051 accepted，仓库引入 target/block 双层复杂度预算与 ratchet 机制，`.complexity-baseline.json` 成为 block 级技术债台账，`just lint` 新增复杂度回退保护，同时补充 `just complexity-report` / `just complexity-baseline` 命令；随后完成 `scripts/devcoord/service.py` 与 agent loop helpers 的拆分，并修复合并后超线热点以满足新预算
- Evidence: commits `1ce3255` / `5cc7ba0` / `eda2680` / `b1e5393`；决议 `decisions/0051-adopt-code-complexity-budgets-and-ratchet-governance.md`
- Next: 后续改动按 ratchet 规则逐步压低 baseline，优先继续治理 devcoord / agent 热点文件
- Risk: 现有 baseline 仍保留部分历史复杂度债务，但新增或恶化的 block 问题已被门禁拦截

## 2026-03-09 (local) | Beads Git-JSONL Backup Migration
- Status: done
- Done: beads 项目级备份路径已从 Dolt remote sync 迁移到 Git-tracked JSONL exports — 计划获批并执行，ADR 0052 accepted；`AGENTS.md` / `CLAUDE.md` / `.beads/README.md` / `justfile` 已统一到 `just beads-backup -> git push` 的 canonical workflow，`beads-pull` / `beads-push` 进入 deprecation 过渡，`.beads/config.yaml` 中旧 `sync.git-remote` 已注释废弃
- Evidence: 计划 `dev_docs/plans/phase2/p2-beads_git-jsonl-backup-migration_2026-03-08.md`；决议 `decisions/0052-project-beads-backup-git-tracked-jsonl-exports.md`；恢复演练 `dev_docs/logs/phase2/p2-beads-migration_restore-drill_2026-03-09.md`；关键提交 `27edb96` / `00f161a` / `a51c6a1` / `8497f4f` / `6316abd`
- Validation: Phase A 在干净环境、无 `.beads/dolt/` 前提下完成 118/118/118 三方计数一致恢复；Phase B 验证 `bd create/update/close` 后 `bd backup --force` 生成 post-mutation backup commit `776bd0e`，其中 `issues.jsonl` +1、`events.jsonl` +3；workflow review findings 全部闭环
- Next: 稳定运行 2 周后再评估是否推进 `no-db: true`
- Risk: 当前 workflow 依赖现版 `bd backup --force` auto-commit 语义；若未来 `bd` 行为变更，已文档化手工 `git add/commit` fallback

## 2026-03-10 (local) | Complexity Batch Sweep Closeout
- Status: done
- Done: `P2 Complexity Batch Hotspot Cleanup` 已完成并收口 — 计划 `dev_docs/plans/phase2/p2-complexity_batch-hotspot-cleanup_2026-03-10.md` 中列出的 31 个 block 文件全部清零，仓库当前 `block_findings=0`，`.complexity-baseline.json` 已刷新为空基线；后续 review 暴露的 compaction fail-open 回归已修复，复杂度治理 epic `NeoMAGI-ih2` 与执行任务 `NeoMAGI-aaa` 已在 beads 中关闭
- Track: 并行治理 / 质量收敛轨；不属于 `P2-M*` 产品里程碑序列
- Evidence: 计划 `dev_docs/plans/phase2/p2-complexity_batch-hotspot-cleanup_2026-03-10.md`；关键提交 `c239f7f` / `e984c77` / `cc6bd93` / `d1da446` / `1cec1cf` / `dd04e88`；beads backup commit `dd6662d`
- Validation: `just lint` 通过；`just test` 1033 passed；`uv run python -m src.infra.complexity_guard report --json` 显示 `block_findings=[]`；`uv run python -m src.infra.complexity_guard check` 显示 `Regressions: 0`
- Next: 将复杂度治理从 block 清债切换到 target 级持续收敛，按 ratchet 规则逐步压低 `scripts/`、`src/` 与 `tests/` 的 target findings
- Risk: 当前全量测试仍有 3 条既存 `RuntimeWarning: coroutine ... was never awaited`，不阻塞本轮 closeout，但应单独治理

## 2026-03-11 (local) | Phase 2 Mainline Pointer
- Status: ready
- Done: `P2-M1a` 显式成长治理内核已经完成并关闭；并行治理轨上的 devcoord SQLite cutover、beads Git-JSONL backup migration 与 complexity batch sweep 也都已完成收口，因此当前没有已知的横切治理 blocker 阻止主线继续推进
- Evidence: `dev_docs/plans/phase2/p2-m1a_growth-governance-kernel_2026-03-06.md`；`design_docs/phase2/roadmap_milestones_v1.md`；`design_docs/phase2/p2_m1_architecture.md`；`dev_docs/progress/project_progress.md` 中 `P2-M1a closeout` / `Complexity Batch Sweep Closeout` 条目
- Next: 主线下一步应进入 `P2-M1b`，而不是跳到 `P2-M2`；建议先产出 `dev_docs/plans/phase2/` 下的 `P2-M1b` draft，范围聚焦 `skill object runtime + builder runtime + beads work memory` 的最小可用闭环，明天从该 draft 开始继续
- Risk: `P2-M1b` 仍是高复杂度阶段，若不先压成最小闭环，容易把 `skill object`、builder 产品化、work memory 与 promote 规则一次性耦合过深

## 2026-03-17 (local) | P2-M1b Prep: Growth Eval Contract
- Status: done
- Done: P2-M1b 前置设计收敛完成 — GrowthEvalContract 作为一等治理对象落地（ADR 0054），四层 eval 结构 (Boundary gates / Effect evidence / Scope claim / Efficiency metrics) 固化，5 个 object-scoped contract profiles 实现（soul V1 + skill_spec V1 + 3 reserved skeletons），SoulGovernedObjectAdapter.evaluate() contract pinning 接入，GLOSSARY.md 新增 12 条评测词汇
- Detail:
  - WP1: Contract Vocabulary Freeze — `design_docs/GLOSSARY.md` 12 条术语
  - WP2: `soul` Contract Profile — SOUL_EVAL_CONTRACT_V1，pin `contract_id` + `contract_version` 到 eval result
  - WP3: `skill_spec` Contract Profile — SKILL_SPEC_EVAL_CONTRACT_V1，5 required checks (schema_validity, activation_correctness, projection_safety, learning_discipline, scope_claim_consistency)
  - WP4: Reserved Kind Templates — wrapper_tool / procedure_spec / memory_application_spec contract skeletons
  - WP5: Plan Integration — 结论回填到 P2-M1b draft 的 SkillGovernedObjectAdapter.evaluate() 设计
- Evidence: commit `07d1539`; 45 new tests in test_contracts.py; `src/growth/contracts.py` 5 profiles; `src/growth/types.py` GrowthEvalContract + PassRuleKind
- Plan: `dev_docs/plans/phase2/p2-m1b-prep_growth-eval-contract_2026-03-15.md`
- Decisions: ADR 0054 (growth eval contracts immutable and object-scoped)
- Next: 进入 P2-M1b 实施
- Risk: 无

## 2026-03-18 (local) | P2-M1b closeout
- Status: done
- Done: P2-M1b Skill Objects Runtime 全部完成（Agent Teams PM 协调，5 Phase + 1 P1-fix，5 Gate 全通过）— skill object domain model、PostgreSQL 持久化（current-state + governance ledger）、SkillStore + SkillGovernedObjectAdapter（5 deterministic eval checks, atomic apply/rollback）、SkillResolver + SkillProjector（规则驱动, delta budget 3/9）、SkillLearner（deterministic evidence 更新 + governance 路径 proposal）、PromptBuilder + AgentLoop pre-plan/post-run-learning join point 集成、composition root 完整 wiring
- Detail:
  - Phase 0: Domain Types + DB Migration — SkillSpec/SkillEvidence/ResolvedSkillView/TaskFrame/TaskOutcome/SkillRegistry(Protocol), Alembic migration (skill_specs + skill_evidence + skill_spec_versions)
  - Phase 1: SkillStore + Governance Adapter — PostgreSQL raw SQL store, SkillGovernedObjectAdapter (pins SKILL_SPEC_EVAL_CONTRACT_V1), PolicyRegistry skill_spec→onboarded; **P1-fix**: apply()/rollback() 事务原子性修复 (transaction() context manager + session injection)
  - Phase 2: TaskFrame + Resolver + Projector — extract_task_frame() 规则抽取 (CN+EN keywords), SkillResolver (SkillRegistry protocol, keyword overlap scoring, top 1-3), SkillProjector (delta cap 3/9)
  - Phase 3: PromptBuilder + AgentLoop Integration — _layer_skills(skill_view) 升级, pre-plan join point (TaskFrame→resolve→project), _finalize_task_terminal() post-run-learning, _detect_teaching_intent(), composition root partial wiring
  - Phase 4: SkillLearner + Creation Path + Tests Closeout — SkillLearner (record_outcome deterministic only, propose_new_skill→governance), GrowthGovernanceEngine full wiring (soul+skill adapters), e2e integration tests
- Evidence: merge commit `960555c`; 25 files changed, ~4,500 insertions; 1311 tests passed (1246 unit + 65 DB-dependent skipped), ruff clean; 5 tester review reports in `dev_docs/reviews/phase2/p2-m1b_p{0-4}_2026-03-18.md`
- Plan: `dev_docs/plans/phase2/p2-m1b_skill-objects-runtime_2026-03-14.md`
- Decisions: ADR 0054 (growth eval contracts immutable and object-scoped)
- Baseline: 986 → 1311 tests (+325, +33%); 25 new/modified files; skill_spec onboarded as 2nd growth object kind
- Residual risks:
  - (LOW) _HIGH_RISK_PATTERN lacks \b on `drop`/`force` — minimal scoring impact
- Next: P2-M1b 全部关闭；按 `design_docs/phase2/roadmap_milestones_v1.md` 进入 P2-M1c (Growth Cases + Capability Promotion)
- Risk: 无阻塞风险

## 2026-03-18 (local) | P2-M1b post-review fix
- Status: done
- Done: 用户独立审阅发现 4 项问题（2×P1, 2×P2），全部修复并合入 main
- Detail:
  - F1 [P1]: Fresh DB 启动路径缺 skill 表 — `ensure_schema()` 新增 `_create_skill_tables()` / `_skill_table_ddl()`，使用 `CREATE TABLE/INDEX IF NOT EXISTS` 幂等创建 skill_specs + skill_evidence + skill_spec_versions
  - F2 [P1]: 教学/学习闭环未接通 — `RequestState` 新增 `accumulated_failure_signals`；tool denial / tool execution 失败时记录 signals；`_complete_assistant_response()` 根据 signals 判定 terminal_state（guard_denied / tool_failure）；`user_confirmed = teaching_intent`；新增 `_propose_taught_skill()` 调用 `propose_new_skill()`
  - F3 [P2]: Resolver evidence 排序虚设 — `resolve()` 改为先 fetch 全部 eligible evidence 再评分；`_score()` 接受 evidence 参数，新增 `_score_evidence()`（breakages penalty + recency bonus）和 `_score_escalation()` 子函数
  - F4 [P2]: 7 个 complexity regressions — 拆分 `_initialize_request_state` / `_handle_tool_calls` / `evaluate` / `apply` / `rollback` / `_score` / `upsert_active` 等超线函数为独立 helper
  - F5 [补充]: 11 个新测试 — prompt_builder skill 注入 (4)、resolver evidence 排序 (3)、learner 教学意图 proposal (2)、learner 提案验证 (2)
- Evidence: commit `02a58b3`; `just lint` PASS (0 regressions); `just test` 1320 passed; 8 files changed, 679 insertions, 301 deletions
- Next: R2 语义修复
- Risk: 教学闭环语义实现仍有 3 处问题

## 2026-03-18 (local) | P2-M1b post-review fix R2
- Status: done
- Done: 用户第二轮审阅发现 3 项教学/学习语义问题，全部修复并合入 main
- Detail:
  - F2a [P1]: 教学 proposal 产出 dead skill — `_propose_taught_skill()` 重写为 `_extract_skill_draft_from_context()`，从 task_frame 提取 capability（= task_type）、activation_tags（= task_type + content 关键词 top 5）、delta（= 用户指令文本），使 proposed skill 可被 resolver 命中且有实际 prompt 注入
  - F2b [P2]: teaching_intent 污染已有 skill evidence — `_finalize_task_terminal()` 不再把 `teaching_intent` 映射为 `user_confirmed`；existing skills 的 `record_outcome` 始终 `user_confirmed=False`，教学意图只触发新 skill proposal
  - F2c [P2]: guard deny 被误记为 tool_failure — 新增 `_GUARD_DENY_CODES` frozenset（GUARD_CONTRACT_UNAVAILABLE / GUARD_ANCHOR_MISSING / MODE_DENIED），`_execute_single_tool()` 按 error_code 分流 guard_denied vs tool_failure
- Evidence: commit `4e7e4b1`; 328 tests passed (skills + growth + prompt_builder); complexity 0 regressions; 3 new tests (teaching semantics + guard classification)
- Next: R3 ID 冲突修复
- Risk: 教学 proposal ID 同 session 内冲突

## 2026-03-18 (local) | P2-M1b post-review fix R3
- Status: done
- Done: 用户第三轮审阅发现教学 proposal ID 同 session 冲突（1×P1），修复并合入 main；用户验收通过
- Detail:
  - [P1]: `_propose_taught_skill()` 中 `id=f"user-taught-{session_id[:8]}"` 在同 session 内固定，导致后一次教学 ON CONFLICT upsert 覆盖前一次 — 改为 `uuid4().hex[:12]`，每次 proposal 生成唯一 ID
- Evidence: commit `b5c9156`; 21 learner tests passed; lint clean
- Next: P2-M1b 验收通过，全部关闭
- Risk: 无

<!-- devcoord:begin milestone=p2-m1c -->
## 2026-03-18 (generated) | P2-M1C
- Status: done
- Done: 最新 gate g4-review 为 closed (PASS)；backend=closed, tester=closed
- Evidence: `dev_docs/logs/phase2/p2-m1c_2026-03-18/gate_state.md`, `dev_docs/logs/phase2/p2-m1c_2026-03-18/watchdog_status.md`, `dev_docs/reviews/phase2/p2-m1c_p4_2026-03-18.md` (e6e59cabbbf005dd0a681e04c0986efda7a5f99b)
- Next: g4-review 已关闭，等待 P2-M1C 下一条 gate
- Risk: 无
<!-- devcoord:end milestone=p2-m1c -->

## 2026-03-20 (local) | P2-M1c closeout
- Status: done
- Done: P2-M1c Growth Cases + Capability Promotion 全部完成（Agent Teams PM 协调，5 Phase + 3 轮 post-review 修复，5 Gate + 5 Review Gate 全通过，milestone 已关闭）— wrapper_tool 正式 onboarded，GC-1 human_taught_skill_reuse + GC-2 skill_to_wrapper_tool_promotion 两条 curated growth case 完整闭环，builder work memory 双层结构 (workspace/artifacts + bd index)，CaseRunner + GrowthCaseSpec catalog + GrowthCaseRun artifacts
- Detail:
  - Phase 0: Contract V1 + GLOSSARY + GC-3 Decision
  - Phase 1: Builder Work Memory Substrate (BuilderTaskRecord + bd feasibility spike 7/7)
  - Phase 2: Wrapper Tool Store + Adapter + Runtime Wiring (WrapperToolSpec + WrapperToolStore + WrapperToolGovernedObjectAdapter + Alembic migration + ToolRegistry unregister/replace + policies onboarding + gateway wiring)
  - Phase 3: Growth Case Catalog + Runner (GrowthCaseSpec catalog + CaseRunner + GC-1/GC-2 integration tests)
  - Phase 4: Acceptance Closeout (A1~A9 全满足, 1495 tests)
  - Post-review R1: rollback disable-on-rollback, tool.name==spec.id guard, DB-first compensating semantics
  - Post-review R2: reject in-place upgrade, version-aware veto, partial unique index
  - Post-review R3: startup restore of active wrappers to ToolRegistry
- Evidence: merge to main; PM 总结 `dev_docs/logs/phase2/p2-m1c_2026-03-18/pm.md`; ADR 0055/0056/0057
- Plan: `dev_docs/plans/phase2/p2-m1c_growth-cases-capability-promotion_2026-03-18.md`
- Baseline: ~1311 → 1503 tests (+192); 37 new files, ~5200 insertions; wrapper_tool onboarded as 3rd growth object kind
- Residual risks:
  - (LOW) uuid4 临时方案，后续升级 UUIDv7
  - (LOW) promote entry condition 仅测试验证，无生产 runtime enforcement
  - (LOW) 启动恢复失败时 log+skip，后续可补 degraded health signal
- Next: P2-M1 全部关闭；按 `design_docs/phase2/roadmap_milestones_v1.md` 进入 P2-M2 (Procedure Runtime 与多 Agent 执行)
- Risk: 无阻塞风险

## 2026-04-06 (local) | P2-M1 user acceptance testing
- Status: done
- Done: 用户按 `design_docs/phase2/p2_m1_user_test_guide.md` 完成全量手工验收 — A 层 7/7 PASS (T03-webchat SKIP), B 层 5/5 PASS, 产物检查 3/3 PASS
- Detail:
  - T04 期间发现 rolled-back skill 未在 runtime 禁用 — Codex 协助修复 (`517dce3` fix(agent): disable rolled-back skills in runtime, 5 files, +157/-17)
  - T05/T08 观测到 OI-01: teaching_intent 同时写入 skill_spec_versions 和 memory/daily notes，导致 skill 与 memory 复用路径串线，无法仅凭行为区分 — 记录为已知设计缺口，见 `design_docs/phase2/p2_m1_open_issues.md`
- Evidence: `dev_docs/logs/phase2/p2-m1_user_acceptance.md`, commit `517dce3`, `fa174e1`
- Next: P2-M1 验收通过，进入 post-works
- Risk: OI-01 不阻塞，后续 skill/memory 边界明确化时处理

## 2026-04-07 (local) | P2-M1 Post Works P1: Multi-Session Threads
- Status: done
- Done: WebChat 多 session 线程功能实现 — 左侧 thread rail, 创建/切换线程, 后台并发 streaming, 断线恢复, localStorage 持久化; 4 commits, 7 文件变更 (+1637/-455), 41 frontend tests; 3 轮 review 修复 5 个 findings (断线回收/活动排序/terminal state/aborted 终态)
- Detail:
  - Store 重构: `sessionsById` per-session 状态, `requestToSession` 事件路由, `pendingHistoryId` per-session history 匹配
  - ThreadRail: 左侧 rail, New Thread, active 高亮, streaming/unread/aborted 指示, `lastActivityAt` 排序
  - 兼容性: 默认 `main` thread 保留旧历史, 新 thread `web:{uuid}`
  - 断线恢复: `_setConnectionStatus` 清空 `requestToSession`, 重置 `isStreaming`, message→error, tool→aborted
- Evidence: commits `a578556`..`31f8563`; `dev_docs/logs/phase2/p2-m1-post-works-p1_acceptance_2026-04-07.md`; 41 tests passed; build clean
- Plan: `dev_docs/plans/phase2/p2-m1-post-works-p1_multi-session-threads_2026-04-06.md`
- Next: P2-M1 Post Works P2 (tool concurrency metadata) / P3 (atomic coding tools), 均为 draft 待批准
- Risk: 无

## 2026-04-07 (local) | P2-M1 final closeout
- Status: done
- Done: P2-M1 显式成长治理内核全部关闭 — 含 M1a (governance kernel) + M1b (skill objects runtime) + M1c (growth cases + capability promotion) + 用户验收 + Post Works P1 (multi-session threads)
- Milestone totals: P2-M1a~M1c 实现 1503 tests → 用户验收修复 +157 → Post Works P1 +41 frontend tests; OI-01 作为已知设计缺口延后处理
- Next: P2-M1 Post Works 剩余项 (P2 tool-concurrency-metadata / P3 atomic-coding-tools, 均 draft); 之后按 roadmap 进入 P2-M2
- Risk: 无阻塞风险

## 2026-04-07 (local) | P2-M1 Post Works P2: Tool Concurrency Metadata
- Status: done
- Done: BaseTool 增加 is_read_only + is_concurrency_safe 双标记元数据 (fail-closed 默认); runtime 自动将同 turn 连续只读并发安全工具组为 bounded parallel group (max 3, asyncio.TaskGroup); 保持 ToolCallInfo/transcript 确定性顺序; 2 轮 review 修复 3 个 findings (文件拆分/ToolCallInfo 时序/tool_index 偏移)
- Detail:
  - `src/agent/tool_concurrency.py`: 新模块, group builder + parallel executor + observability (274 行)
  - `src/agent/message_flow.py`: 三阶段编排 (通知→执行→落盘), 从 960 行降至 710 行
  - V1 标注: current_time, memory_search, soul_status → True+True; read_file → read_only only; 写入型工具保持 False+False
  - 37 新增测试覆盖 metadata/grouping/parallel overlap/timing/failure signals/regression
- Evidence: commit `ab51a69`; `dev_docs/logs/phase2/p2-m1-post-works-p2_acceptance_2026-04-07.md`; 1553 tests passed
- Plan: `dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06.md`
- Next: P2-M1 Post Works P3 (atomic coding tools, draft); 之后按 roadmap 进入 P2-M2
- Risk: 无

## 2026-04-07 (local) | P2-M1 Post Works P3: Atomic Coding Tools (Stage A/B)
- Status: done
- Done: 补齐最小 text/coding file transaction surface — coding entry (ADR 0058), read state infrastructure, 5 个 atomic coding tools (read_file upgrade, glob, grep, write_file, edit_file); 3 commits, 24 文件变更 (含 8 新文件); 3 轮 review 修复 7 个 findings (boolean coercion P1, TS build P1, complexity P1, frontend routing P2, glob sort P2, UI entry P2)
- Detail:
  - Slice A — Coding Entry: 移除 M1.5 guardrail, 新增 `SessionManager.set_mode()`, WebSocket RPC `session.set_mode`, `ModeToggle.tsx` 最小 UI 入口
  - Slice B1 — read_file Upgrade: `file_path`/`path` alias, `offset`/`limit`, output truncation, process-local read state tracking, newline-safe I/O
  - Read State Infrastructure: `ReadState`/`ReadScope`/`ReadStateStore` + `validate_workspace_path` + `coerce_bool` + `resolve_search_dir` (共享模块 `src/tools/read_state.py`)
  - Slice B2 — glob + grep: `asyncio.to_thread` 非阻塞, `is_concurrency_safe=True`, 有界收集
  - Slice C — write_file + edit_file: create-only 默认, read-before-write, staleness check (mtime_ns+size), full-read enforcement, exact string match + replace_all
  - Stage C (bash) 按计划保留为 follow-up
- Evidence: commits `77f536f`, `3a1650f`, `695e6e7`; `dev_docs/logs/phase2/p2-m1-post-works-p3_acceptance_2026-04-07.md`; 1615 backend + 41 frontend tests passed; `pnpm build` passed
- Plan: `dev_docs/plans/phase2/p2-m1-post-works-p3_atomic-coding-tools_2026-04-06.md`
- Decisions: ADR 0058 (coding mode open conditions)
- Next: 评估 Stage C (bash) follow-up; 之后按 roadmap 进入 P2-M2
- Risk: ModeToggle UI 路径无专用测试，不阻塞

## 2026-04-07 (local) | P2-M2a: Procedure Runtime Core
- Status: done
- Done: 交付最小可用 Procedure Runtime Core — ProcedureSpec 静态校验 + ProcedureStore (PostgreSQL CAS + single-active) + ProcedureRuntime (enter/apply/resume) + AgentLoop 集成 (virtual action schema + barrier 串行 + prompt view) + gateway composition root wiring; 3 轮 review 修复 7 个 findings (stale prompt P1, invalid args P1, gateway wiring P2, PG integration test P2, context_patch error P2, registry fail-closed P2, deny signal classification P2)
- Detail:
  - `src/procedures/`: 6 个新模块 (types, result, registry, runtime, store, __init__)
  - `src/agent/procedure_bridge.py`: bridge 函数 (resolve/build/rebuild checkpoint)
  - `alembic/versions/c0d1e2f3a4b5`: active_procedures 表 (partial unique index, CAS)
  - `src/session/database.py`: `_create_procedure_tables()` fresh-DB 启动路径
  - AgentLoop 新增 `procedure_runtime` 可选依赖; RequestState 新增 procedure 字段
  - PromptBuilder 新增 `_layer_procedure()` + `procedure_view` 参数
  - tool_concurrency 新增 procedure action barrier + `_PROCEDURE_DENY_CODES` guard_denied 分类
  - ProcedureSpecRegistry 带 registry 构造时 register() 自动 validate (fail-closed)
  - 96 新增测试 (87 unit + 6 PG integration + 1 gateway wiring + 2 concurrency)
- Evidence: `dev_docs/logs/phase2/p2-m2a_procedure-runtime-core_2026-04-07.md`; 1709 tests passed
- Plan: `dev_docs/plans/phase2/p2-m2a_procedure-runtime-core_2026-04-07.md`
- Next: P2-M2a-post (procedure_spec governance adapter); 之后按 roadmap 进入 P2-M2b (multi-agent handoff)
- Risk: procedure_spec 仍为 growth reserved kind，governance adapter 需要独立 follow-up

## 2026-04-08 (local) | P2-M2b: Multi-Agent Runtime
- Status: done
- Done: 交付最小可用 Multi-Agent Runtime — AgentRole/RoleSpec 类型 + ProcedureActionDeps (TYPE_CHECKING guard) + ToolContext 扩展 (actor/procedure_deps) + BaseTool.is_procedure_only + HandoffPacket (32KB bounded) + WorkerExecutor (多 turn, 三重 tool 过滤: group + procedure_only + risk_level) + ReviewerExecutor/ReviewTool + DelegationTool + PublishTool (staging → publish → memory flush D9) + purposeful compact (task state extraction) + gateway 注册; 5 轮计划审阅修复 16 findings + 3 轮实现审阅修复 4 findings (worker 输出契约, failed delegation staging, guardrail bypass, high-risk tool exclusion)
- Detail:
  - `src/procedures/`: 8 个新模块 (roles, deps, handoff, worker, reviewer, delegation, publish, compact)
  - `src/tools/`: context.py 扩展 (actor, procedure_deps), base.py 新增 is_procedure_only
  - `src/procedures/runtime.py`: D7 is_procedure_only mode bypass
  - `src/agent/tool_concurrency.py`: D8 ProcedureActionDeps 注入 + D9 publish flush 路由
  - `src/agent/compaction.py` + `compaction_flow.py`: task_state_text 参数传递
  - `src/gateway/app.py`: _register_procedure_tools() 注册 3 个 procedure-only tools
  - 93 新增测试 (89 unit + 4 E2E multi-agent flow)
- Evidence: `dev_docs/logs/phase2/p2-m2b_multi-agent-runtime_2026-04-08.md`; 1794 tests passed
- Plan: `dev_docs/plans/phase2/p2-m2b_multi-agent-runtime_2026-04-07.md`
- Next: P2-M2a-post (procedure_spec governance adapter); 之后按 roadmap 考虑 P2-M3 (身份认证/记忆质量)
- Risk: worker 当前排除所有 high-risk tools，后续可能需要受控的 write 委托路径
