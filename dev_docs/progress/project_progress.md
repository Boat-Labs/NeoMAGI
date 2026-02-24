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
- Evidence: dev_docs/reviews/m1.1_implementation-review_2026-02-17.md
- Next: 按优先级修复 F1 → F2 → F3
- Risk: F1 导致默认配置下 DB 静默退化为内存模式，影响持久化稳定性

## 2026-02-18 08:44 (local) | M1.1
- Status: in_progress
- Done: 评审问题修复 v3 完成 — F1: DB_SCHEMA 常量统一 + validator + fail-fast + ADR 0017 对齐；F2: _safe_parse_args 双层 dict 校验；F3: chat.history 过滤 system/tool 消息；含 3 个测试文件
- Evidence: commit 01b1085 (F1) + 472a6a1 (F2) + 3103e1c (F3), dev_docs/plans/m1.1_review-fixes_2026-02-17_v3.md
- Next: v3 实现的 code review
- Risk: 无

## 2026-02-18 13:12 (local) | M1.1
- Status: in_progress
- Done: v3 code review 发现 2 项问题并修复 — R1(HIGH): 日志行 `tc.function.arguments[:200]` 对 None 崩溃，用 `str(...)[:200]` 修复；R2(LOW): 5 个测试文件未使用导入清理
- Evidence: commit 2cf91a1 (R1) + b94a6d3 (R2), dev_docs/plans/m1.1_review-fixes_2026-02-18_v4.md
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
- Evidence: commit 1e48eed (F1) + 5360c80 (F5) + 3078a69 (F6a-1) + 00b0bfb (F6a-2), 43 tests passed, ruff clean, dev_docs/plans/m1.2_audit-fixes_2026-02-18.md
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
- Plan: dev_docs/plans/m1.3_review-fixes_2026-02-18.md
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
- Plan: dev_docs/plans/m1.4_audit-test-infra_2026-02-18.md
- Next: M1 审计全部完成，进入 M2 规划
- Risk: 无

## 2026-02-20 (local) | M1.5
- Status: in_progress
- Done: roadmap v3 与决议拆分完成（ADR 0023 + 0024），并完成 architecture 文档体系重组（M1 总结 + M1.5~M6 计划 + design_docs/index.md）；新增 ADR 0025 明确 mode 切换权与 M1.5 固定 `chat_safe` 边界
- Evidence: commit 912bac7, `design_docs/roadmap_milestones_v3.md`, `decisions/0023-roadmap-product-oriented-boundary.md`, `decisions/0024-m1.5-tool-modes-and-priority-reorder.md`, `decisions/0025-mode-switching-user-controlled-chat-safe-default.md`
- Next: 按 ADR 0025 推进 M1.5（Tool Modes）详细方案与实现（固定 `chat_safe`，`coding` 预留）
- Risk: 无

## 2026-02-20 (local) | M1.5
- Status: in_progress
- Done: M1.5 Tool Modes 主体实现交付 — dual-gate mode 授权框架（暴露闸门 + 执行闸门）、ToolGroup/ToolMode enum、BaseTool fail-closed defaults、ToolRegistry mode-aware filtering/override/check_mode、3 个 builtin 工具 metadata 声明、AgentLoop 执行闸门 ToolDenied 路径、PromptBuilder mode-filtered tooling + safety layer、SessionManager.get_mode fail-closed + M1.5 guardrail、SessionSettings config validation、前端 tool_denied WebSocket 消息处理与 UI
- Evidence: commit e0759b2..0a555b1, 123 unit tests + 24 integration tests + 13 frontend tests passed, ruff clean
- Plan: dev_docs/plans/m1.5_tool-modes_2026-02-19.md
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
  - P3: M1.5 milestone 日志 — 创建 `dev_docs/logs/m1.5_2026-02-21/developer.md`
- Evidence: commit cb3c4d3..5e53407 (merge), 123 unit tests + 26 integration tests + 16 frontend tests passed, ruff clean
- Plan: dev_docs/plans/m1.5_review-fixes_2026-02-21.md
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
- Plan: dev_docs/plans/m2_post-review-fixes_2026-02-22.md
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
- Evidence: commit f2a69b8, `dev_docs/plans/m3_persistent-memory_2026-02-22.md` (status: approved)
- Plan: dev_docs/plans/m3_persistent-memory_2026-02-22.md
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
- Evidence: working tree updates — `decisions/0035-runtime-anti-drift-guardrail-hardening-and-risk-gated-fail-closed.md`, `decisions/INDEX.md`, `design_docs/roadmap_milestones_v3.md`, `design_docs/m2_architecture.md`, `design_docs/m3_architecture.md`
- Plan: dev_docs/plans/m3_persistent-memory_2026-02-22.md（Phase 0 增补 ADR 0035 最小防护任务）
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
- Evidence: 468 tests passed, ruff clean; PM 报告 `dev_docs/logs/m3_2026-02-24/pm.md`
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
- Plan: dev_docs/plans/m3_post-review-fix_2026-02-24.md (approved + executed)
- Decisions: ADR 0036 (Evolution DB-SSOT + 投影对账), 0037 (workspace_path 单一真源)
- Next: 进入 M6（模型迁移验证）
- Risk: 无；ParadeDB pg_search BM25 为已知 R1 风险，当前 tsvector + GIN fallback 功能等价

## 2026-02-25 00:40 (local) | M3
- Status: done
- Done: M3 收尾后完成两项紧急稳定性修补——修复 legacy DB `sessions` 缺列导致启动失败、修复历史中断裂 tool_call 链导致 OpenAI 400（`tool_calls must be followed by tool messages`）
- Evidence: `src/session/database.py`, `src/agent/agent.py`, `tests/test_ensure_schema.py`, `tests/test_agent_tool_parse.py`, `tests/test_compaction_degradation.py`, `uv run pytest tests/test_agent_tool_parse.py tests/test_compaction_degradation.py -q` (23 passed), `uv run pytest tests/test_ensure_schema.py -q -m integration` (2 passed)
- Next: 复测 M3 用户测试指导中的 T03/T04/T05 长链路并确认不再复现 400
- Risk: 历史污染会话在旧版本残留场景下仍可能需要新会话复测一次；新版本已在发送前做 tool_call 历史清洗兜底
