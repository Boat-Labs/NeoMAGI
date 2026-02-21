# M1.5 Tool Modes — Developer Log

**Milestone:** M1.5 Tool Modes (review-fixes)
**Date:** 2026-02-21
**Role:** Developer (solo implementation)

## Skills / Tools Used

| Tool/Skill | Calls | 典型场景 | 效果评估 | 可核对依据 |
|---|---|---|---|---|
| Edit (frontend) | 5 | chat.ts tool_denied update-or-insert, done handler fix, test updates | 精准替换，无误 | cb3c4d3 |
| Edit (backend) | 2 | agent.py gate 条件修改, test_tool_modes.py 新增测试 | 单行条件改动，影响面最小 | 77ed1b4 |
| Edit (test) | 2 | structlog capture_logs 替换, integration test 新增 | 消除了永真断言 | cab0374 |
| pytest (unit) | 6 | TDD red-green 验证, 全量回归 | 123 tests 全部通过 | `uv run pytest tests/ -m "not integration"` |
| pytest (integration) | 2 | 新增 UNKNOWN_TOOL 集成测试 | 26 tests 全部通过 | `uv run pytest tests/ -m integration` |
| vitest (frontend) | 3 | TDD red-green 验证, 全量回归 | 16 tests 全部通过 | `cd src/frontend && pnpm test -- --run` |
| ruff | 1 | lint 全量检查 | All checks passed | `uv run ruff check src/ tests/` |

## Key Decisions

1. **tool_denied 语义边界**：`tool_denied` 事件仅限 mode gate 拒绝。未知工具不产出 ToolDenied，而是走 `_execute_tool` 已有的 `UNKNOWN_TOOL` 路径。协议语义保持清晰。
2. **Frontend update-or-insert**：tool_denied handler 由 append 改为 findIndex + update，找不到时 fallback insert（orphan safety）。done handler 对 denied 状态做 preserve 而非覆写。
3. **structlog 测试策略**：从 caplog + stdlib 配置切换到 `structlog.testing.capture_logs`，消除对 logging backend 的依赖。

## Issues Encountered

1. Worktree 中 `node_modules` 和 `.venv` 需要重新安装（worktree 不共享这些目录）。
2. `uv run pytest` 在新建 worktree 时首次运行触发 venv 创建，但未安装 dev dependencies，需要额外 `uv pip install -e ".[dev]"`。

## Recommendations for Next Milestones

1. 考虑在 `justfile` 中添加 worktree setup recipe，自动处理依赖安装。
2. M2 如果引入 coding 模式切换，需要同步更新 agent.py gate 的 `denial_next` 消息（当前硬编码了"未来版本将支持"）。
3. Frontend 的 toolCalls 状态机可考虑提取为独立 reducer，降低 `_handleServerMessage` 的复杂度。
