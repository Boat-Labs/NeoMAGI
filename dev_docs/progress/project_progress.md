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
