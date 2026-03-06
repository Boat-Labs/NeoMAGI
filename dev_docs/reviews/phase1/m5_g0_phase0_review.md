# M5 Gate 0 Review: Phase 0 Preflight 统一框架

> Reviewer: tester
> Date: 2026-03-04
> Target commit: 11cf795
> Branch: feat/backend-m5-reliability

## 结论：PASS

Phase 0 实现完整、正确，与计划和架构文档高度对齐。所有 50 个 Phase 0 相关测试通过，lint 无错误。无 P0/P1 级问题。

## Findings

### P0 (Blocker)

无。

### P1 (Must Fix)

无。

### P2 (Should Fix)

**F1: C2 unknown provider 分支未被测试覆盖**
- 文件：`tests/test_preflight.py` `TestCheckActiveProvider`
- `_check_active_provider()` 有一个 `else` 分支处理 `settings.provider.active` 既非 `"openai"` 也非 `"gemini"` 的情况（返回 FAIL + "Unknown provider"），但测试只覆盖了 openai/gemini 两种路径。
- 影响：如果 provider 配置错误设为不支持的值，该路径的行为未被回归保护。
- 建议：在 `TestCheckActiveProvider` 中增加 `test_unknown_provider` 用例。

### P3 (Nice to Have)

**F2: C4/C3 同步文件 I/O 在 async 上下文中调用**
- 文件：`src/infra/preflight.py:143` (`_check_workspace_dirs`)、`src/infra/preflight.py:119` (`_check_workspace_path_consistency`)
- 两个检查函数是同步的（`def` 非 `async def`），内部使用 `Path.is_dir()`、`Path.resolve()`、`tempfile.NamedTemporaryFile()` 等同步文件 I/O，从 async `run_preflight()` 中直接调用。
- 影响：这些操作耗时在微秒级，实际不会阻塞事件循环。在启动阶段执行，不影响运行时性能。
- 不建议改动，仅记录与编码规范的偏差。

**F3: C10 + adapter check_ready() 双重 Telegram getMe 调用**
- 文件：`src/infra/preflight.py:365` (C10)、`src/gateway/app.py:226` (check_ready)
- C10 创建临时 Bot 调用 `get_me()` 验证 token，lifespan 中 TelegramAdapter 又调用 `check_ready()` 再次 `get_me()`。
- 影响：启动时多一次 Telegram API 调用，不影响功能。app.py 注释已说明两者职责不同（C10 验证 token，check_ready 初始化 adapter 内部状态）。
- 可在后续 Phase 考虑让 C10 将 bot_username 缓存传递给 adapter，避免重复调用。

**F4: test_workspace_path_mismatch_fails 产生 RuntimeWarning**
- 文件：`tests/test_app_integration.py:181`
- 当 workspace path 不一致导致 preflight FAIL 时，mock engine 的 `connect()` 返回的 coroutine 未被 await（因为 DB 依赖检查被跳过前 engine mock 已被创建）。
- 影响：测试正常通过，warning 不影响结果。可通过调整 mock 设置消除。

## 测试结果

### Phase 0 测试（50 tests）

| 测试文件 | 数量 | 结果 |
|----------|------|------|
| tests/test_health_models.py | 11 | 全部 PASSED |
| tests/test_preflight.py | 20 | 全部 PASSED |
| tests/test_app_integration.py | 19 | 全部 PASSED |
| **总计** | **50** | **50 PASSED, 1 warning** |

### 全量回归测试

| 范围 | 结果 |
|------|------|
| Unit tests (exclude integration) | 667 passed, 2 errors (pre-existing testcontainers 依赖) |
| Lint (Phase 0 files) | All checks passed |
| Lint (全量) | 9 errors (全在 pre-existing 文件中，非 Phase 0 交付物) |

### 测试覆盖分析

| 检查项 | Unit Test | 组合测试 | 状态 |
|--------|-----------|----------|------|
| C2 active_provider | openai OK/FAIL, gemini OK/FAIL | run_preflight fail_blocks | 覆盖（P2: 缺 unknown provider） |
| C3 workspace_path | consistent/inconsistent | — | 覆盖 |
| C4 workspace_dirs | all_ok/ws_missing/memory_missing | — | 覆盖（缺 not-writable） |
| C5 db_connection | ok/fail | db_fail_skips_dependent | 覆盖 |
| C6 schema_tables | all_present/missing/exception | — | 覆盖 |
| C7 search_trigger | exists/missing/exception→WARN | warn_does_not_block | 覆盖 |
| C8 budget_tables | present/missing/exception→FAIL | — | 覆盖 |
| C9 soul_versions | readable/fail | — | 覆盖 |
| C10 telegram | ok/fail | — | 覆盖 |
| C11 soul_reconcile | ok/warn_on_error | — | 覆盖 |
| run_preflight | — | all_ok, fail_blocks, db_skips, warn_ok | 覆盖 |
| ValidationError | — | AST 结构验证 | 覆盖 |
| lifespan 集成 | — | preflight mock + workspace mismatch fail | 覆盖 |

## 计划对齐检查

| 计划要求 | 实现状态 | 备注 |
|----------|---------|------|
| 0.1 CheckStatus/CheckResult/PreflightReport | ✅ 完全匹配 | health.py: enum + frozen dataclass + report |
| 0.2 run_preflight(settings, db_engine) | ✅ 完全匹配 | preflight.py: 10 个检查项 (C2-C11) |
| C2 active provider (FAIL) | ✅ | openai/gemini/unknown 三路 |
| C3 workspace_path consistency (FAIL) | ✅ | .resolve() 比较 |
| C4 workspace dirs (FAIL) | ✅ | exist + writable + memory/ |
| C5 DB connection (FAIL) | ✅ | SELECT 1 |
| C6 schema tables (FAIL) | ✅ | information_schema introspection |
| C7 search trigger (WARN) | ✅ | trigger 检查，不阻断 |
| C8 budget tables (FAIL) | ✅ | 符合 F4 修正要求 |
| C9 soul_versions readable (FAIL) | ✅ | SELECT 1 LIMIT 1 |
| C10 Telegram connector (FAIL, conditional) | ✅ | bot_token 有值时才检查 |
| C11 SOUL.md reconcile (WARN) | ✅ | 启动阶段允许写文件 |
| 0.3 ValidationError 结构化日志 | ✅ | try/except + structlog.error |
| 0.3 lifespan ensure_schema 在 preflight 前 | ✅ | app.py:112 |
| 0.3 preflight_report 存入 app.state | ✅ | app.py:118 |
| 0.3 FAIL → RuntimeError 阻断启动 | ✅ | app.py:121-126 |
| 0.3 WARN → 日志不阻断 | ✅ | preflight.py:72-73 |

### 架构文档对齐 (m5_architecture.md §6.1)

| 架构要求 | 实现状态 |
|----------|---------|
| 配置完整性（必填 env/key） | ✅ C2 |
| workspace_dir / memory.workspace_path 一致 | ✅ C3 |
| 必要目录存在且可访问 | ✅ C4 |
| DB 可连接 | ✅ C5 |
| schema 正确 + 必要表存在 | ✅ C6 |
| search trigger / budget tables / soul tables | ✅ C7/C8/C9 |
| connector readiness (Telegram) | ✅ C10 |
| 启动对账 (SOUL.md reconcile) | ✅ C11 |

## 编码规范合规性

| 规范 | 状态 |
|------|------|
| `from __future__ import annotations` | ✅ 全部文件 |
| Type hints | ✅ |
| structlog (非 print/logging) | ✅ |
| ruff clean (Phase 0 文件) | ✅ |
| Pydantic v2 (dataclass 用于纯数据) | ✅ |
| 异常不静默吞 | ✅ 所有 except 块有 return 或 re-raise |
| async I/O (DB 操作) | ✅ |
| 同步 FS 操作 | ⚠️ P3 F2 (微秒级，可接受) |
