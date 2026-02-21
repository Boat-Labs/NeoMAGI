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
