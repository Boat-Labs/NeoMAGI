# M5 Post-Review Fix Plan (v3)

> 日期：2026-03-04
> 状态：draft v3，待用户审阅
> 关联：`dev_docs/logs/m5_2026-03-04/pm.md` §4 用户 Post-Review 汇总
> v2 变更：F1 三层 readiness 语义重写、F3 tar 布局统一、测试命令修正
> v3 变更：F1c provider 健康接线落实到 ModelClient._retry_call()、F3 增加脚本入口路径守卫 + restore Step 5/7 全链路统一 workspace_dir
> v4 变更：F1c 流式调用接线修正——_retry_call defer_health 分离创建/迭代阶段，failure 始终立即记录

## 1. Findings 清单

| ID | 级别 | 摘要 | 涉及文件 |
|----|------|------|----------|
| P1-01 | P1 | /health/ready 只反映启动快照，运行期依赖掉线仍返回 ready | `src/gateway/app.py`, `src/infra/preflight.py`, `src/infra/health.py`, `src/agent/model_client.py` |
| P1-02 | P1 | restore 解压前不清空 workspace，残留文件被重新索引 | `scripts/restore.py` |
| P1-03 | P1 | backup/restore workspace 路径写死 `Path("workspace")`，不尊重配置 | `scripts/backup.py`, `scripts/restore.py` |
| P2-01 | P2 | D2/DD3 daily notes 计数不过滤纯 metadata 条目 | `src/infra/doctor.py` |
| P2-02 | P2 | C4 只验证 workspace 根可写，未验证 workspace/memory 子目录 | `src/infra/preflight.py` |
| T-01 | Test | 3 个 CLI smoke test 硬编码 worktree 绝对路径 | `tests/test_backup.py`, `tests/test_restore.py` |

## 2. 架构约束

### 2.1 路径单一真源

依据 ADR 0037：**`Settings.workspace_dir` 是唯一真源路径**。
`memory.workspace_path` 只是给 MemoryIndexer 等旧接口的兼容传参字段，不承载独立根目录语义。

所有 F2/F3 的文件系统操作统一基于 `settings.workspace_dir.resolve()`。

**注意**：backup.py / restore.py 是独立 CLI，不经过 preflight。因此不能假设 C3 已校验路径一致性——脚本自身必须在入口处显式校验 `workspace_dir == memory.workspace_path`，不一致则 fail-fast。

### 2.2 Readiness 语义（三层模型）

依据 `m5_architecture.md` §6.3：readiness 表达"是否适合接流量"，综合 DB、provider、connector、startup reconcile。

本次定义 readiness = 本地实时检查 + 启动锁存结果 + 进程内状态：

| 层 | 内容 | 执行时机 | 实现方式 |
|----|------|----------|----------|
| 本地实时检查 | DB 连接、schema tables、budget tables、soul_versions_readable、workspace_path_consistency、workspace_dirs | 每次 `/health/ready` 请求 | 复用 preflight C3~C9（排除 C2 静态配置检查） |
| 启动锁存结果 | C2 active_provider 配置、C11 reconcile | 启动时执行一次，结果存 app.state | 已有 `preflight_report`，只读 |
| 进程内状态 | Telegram adapter/polling 健康、provider 最近连续失败计数 | 读进程内变量 | 新增，详见 F1c |

不做 per-request 外部探测（不每次打 getMe / model API）。
不做后台定时探针线程。
不完全依赖 fail-fast（进程退出前 readiness 应先翻红）。

## 3. 修复方案

### F1: P1-01 — /health/ready 三层 readiness

**问题**：`/health/ready` 只读启动快照 `app.state.preflight_report`，运行期 DB 掉线、provider 连续失败、Telegram polling 崩溃均不反映。

**方案**：三层 readiness 模型——本地实时 + 启动锁存 + 进程内状态。

#### F1a: 本地实时检查

**改动 `src/infra/preflight.py`**：
- 新增 `run_readiness_checks(settings, engine)` 函数，执行以下无副作用检查：
  - C3 workspace_path_consistency
  - C4 workspace_dirs
  - C5 db_connection
  - C6 schema_tables
  - C7 search_trigger
  - C8 budget_tables
  - C9 soul_versions_readable
- 不包含 C2（纯静态配置检查，启动后不会变）、C10（外部 API）、C11（有写副作用）。
- 返回 `PreflightReport`（复用已有类型）。

#### F1b: 启动锁存结果

**改动 `src/gateway/app.py`**：
- lifespan 中将 `engine` 和 `settings` 存入 `app.state.db_engine` 和 `app.state.settings`。
- `app.state.preflight_report` 保留，作为启动锁存结果。

#### F1c: 进程内健康状态

**新增 `src/infra/health.py` `ComponentHealthTracker` 类**：
```python
class ComponentHealthTracker:
    """In-process health state for readiness evaluation.

    Updated by model_client (provider) and app lifespan (telegram).
    Read by /health/ready endpoint. No locks needed: single-process asyncio.
    """
    def __init__(self) -> None:
        self.telegram_healthy: bool = True
        self.telegram_error: str | None = None
        self.provider_consecutive_failures: int = 0

    @property
    def provider_healthy(self) -> bool:
        return self.provider_consecutive_failures < 5

    def record_provider_success(self) -> None:
        self.provider_consecutive_failures = 0

    def record_provider_failure(self) -> None:
        self.provider_consecutive_failures += 1

    def record_telegram_failure(self, error: str) -> None:
        self.telegram_healthy = False
        self.telegram_error = error
```

**Provider 健康接线——改动 `src/agent/model_client.py`**：

接线点分两层：
- **非流式调用** (chat / chat_completion)：`_retry_call()` 是完整调用出口，在此记录成功/失败。
- **流式调用** (chat_stream / chat_stream_with_tools)：`_retry_call()` 只包裹 stream 创建（HTTP 握手），真正的数据传输在 `async for chunk in stream` 阶段。如果 stream 创建成功但迭代中途断开（网络中断、服务端错误），`_retry_call` 已经返回，tracker 不会被更新。且由于创建阶段误记 success 会重置计数，mid-stream 失败永远攒不到阈值。

因此 `_retry_call()` 增加 `defer_health: bool` 参数：
- 非流式调用 `defer_health=False`（默认）：`_retry_call` 内部记录 success/failure。
- 流式调用 `defer_health=True`：`_retry_call` 不记录。由流式方法自行在迭代完成时 `record_provider_success()`、迭代异常时 `record_provider_failure()`。

ModelClient 在 lifespan 中构造，此时 health_tracker 已可用。
不需要改 dispatch_chat / AgentLoop / Telegram adapter 的接口。

具体改动：
```python
class OpenAICompatModelClient(ModelClient):
    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        health_tracker: ComponentHealthTracker | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._health_tracker = health_tracker

    async def _retry_call(
        self, coro_factory, *, context="", defer_health: bool = False,
    ) -> T:
        for attempt in range(self._max_retries + 1):
            try:
                result = await coro_factory()
                # ✅ 非流式：记录成功；流式：延迟到迭代完成
                if not defer_health and self._health_tracker:
                    self._health_tracker.record_provider_success()
                return result
            except _RETRYABLE as e:
                if attempt == self._max_retries:
                    if not defer_health and self._health_tracker:
                        self._health_tracker.record_provider_failure()
                    raise LLMError(...) from e
                ...
            except APIStatusError as e:
                if not defer_health and self._health_tracker:
                    self._health_tracker.record_provider_failure()
                raise LLMError(...) from e
```

**流式方法接线**（chat_stream 和 chat_stream_with_tools 同理）：
```python
async def chat_stream_with_tools(self, ...) -> AsyncIterator[StreamEvent]:
    stream = await self._retry_call(
        lambda: self._client.chat.completions.create(..., stream=True),
        context="chat_stream_with_tools",
        defer_health=True,  # ← 延迟：不在创建阶段记录
    )
    try:
        async for chunk in stream:
            ...  # yield ContentDelta / accumulate tool_calls
        # ✅ 流式完成：记录成功
        if self._health_tracker:
            self._health_tracker.record_provider_success()
    except Exception:
        # ❌ 流式迭代失败：记录失败，re-raise
        if self._health_tracker:
            self._health_tracker.record_provider_failure()
        raise

    if pending_tool_calls:
        yield ToolCallsComplete(...)
```

chat_stream 同理：将 `async for chunk in stream` 包裹在 try/except 中，完成时 record_success，异常时 record_failure + re-raise。

**关键语义**：
- 流式创建失败（_retry_call 中 defer_health=True 的异常）：_retry_call 不记录，但 LLMError 会向上传播到 AgentLoop，下次 readiness 检查时 failure count 可能仍为 0。需要在流式 _retry_call 的异常路径也记录失败——修正：即使 defer_health=True，_retry_call 内部的**最终失败**（重试耗尽 / 不可重试错误）仍应 record_failure，因为此时不会有迭代阶段执行。只有**成功返回**时才 defer。

修正后的 _retry_call 语义：
- `defer_health=True` 仅 defer **success** 记录，failure 始终立即记录。
- 这避免了流式创建失败时 tracker 完全不更新的盲区。

```python
    async def _retry_call(
        self, coro_factory, *, context="", defer_health: bool = False,
    ) -> T:
        for attempt in range(self._max_retries + 1):
            try:
                result = await coro_factory()
                if not defer_health and self._health_tracker:
                    self._health_tracker.record_provider_success()
                return result
            except _RETRYABLE as e:
                if attempt == self._max_retries:
                    # ❌ failure 始终立即记录（不受 defer_health 影响）
                    if self._health_tracker:
                        self._health_tracker.record_provider_failure()
                    raise LLMError(...) from e
                ...
            except APIStatusError as e:
                # ❌ failure 始终立即记录
                if self._health_tracker:
                    self._health_tracker.record_provider_failure()
                raise LLMError(...) from e
```

**Telegram 健康接线——改动 `src/gateway/app.py`**：
```python
# lifespan 中
health_tracker = ComponentHealthTracker()
app.state.health_tracker = health_tracker

# 构造 model clients 时注入 tracker
openai_client = OpenAICompatModelClient(
    api_key=..., base_url=..., health_tracker=health_tracker,
)
# gemini_client 同理

# _on_polling_done 回调（已有）增加 tracker 更新
def _on_polling_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        # ✅ 新增：翻红 readiness（在 SIGTERM 之前）
        health_tracker.record_telegram_failure(str(exc))
        logger.error("telegram_polling_fatal", error=str(exc))
        os.kill(os.getpid(), signal.SIGTERM)
```

注意：`_on_polling_done` 是模块级函数，需要通过闭包或 `functools.partial` 捕获 `health_tracker` 引用。改为在 lifespan 内定义闭包：
```python
def _make_polling_done_callback(tracker: ComponentHealthTracker):
    def _on_polling_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            tracker.record_telegram_failure(str(exc))
            logger.error("telegram_polling_fatal", error=str(exc))
            os.kill(os.getpid(), signal.SIGTERM)
    return _on_polling_done
```

#### F1d: /health/ready 端点整合

**改动 `src/gateway/app.py` `/health/ready`**：
```python
async def health_ready(request: Request):
    settings = request.app.state.settings
    engine = request.app.state.db_engine
    tracker = request.app.state.health_tracker
    startup_report = request.app.state.preflight_report

    # Layer 1: 本地实时检查
    realtime = await run_readiness_checks(settings, engine)

    # Layer 2: 启动锁存（从 startup_report 提取 C2 + C11 结果）
    startup_checks = [
        c for c in startup_report.checks
        if c.name in ("active_provider", "soul_reconcile")
    ]

    # Layer 3: 进程内状态 → 合成 CheckResult
    component_checks = []
    if not tracker.telegram_healthy:
        component_checks.append(CheckResult(
            name="telegram_runtime", status=CheckStatus.FAIL,
            evidence=f"Polling fatal: {tracker.telegram_error}",
            impact="Telegram channel down", next_action="Restart service",
        ))
    if not tracker.provider_healthy:
        component_checks.append(CheckResult(
            name="provider_runtime", status=CheckStatus.FAIL,
            evidence=f"{tracker.provider_consecutive_failures} consecutive LLM failures",
            impact="LLM requests failing", next_action="Check provider status",
        ))

    # 综合
    all_checks = realtime.checks + startup_checks + component_checks
    has_fail = any(c.status == CheckStatus.FAIL for c in all_checks)
    status = "not_ready" if has_fail else "ready"

    return {
        "status": status,
        "checks": {
            c.name: {"status": c.status.value, "evidence": c.evidence}
            for c in all_checks
        },
    }
```

**设计取舍**：
- C2 (active_provider) 放启动锁存层不放实时层：配置和环境变量启动后不变，每次重跑无意义。
- provider 健康用连续失败计数而非单次失败：避免偶发网络抖动导致 readiness 翻红。阈值 5 是保守值。
- Telegram 健康直接用 bool：`_on_polling_done` 已是 fatal 判定，一旦触发即表示不可恢复。
- `ComponentHealthTracker` 不加锁：单进程 + asyncio 单线程，int/bool 赋值是原子的。
- Provider tracker 注入在 ModelClient 构造时，不需要改 dispatch_chat / AgentLoop / TelegramAdapter 的任何接口签名。

### F3: P1-03 — backup/restore 统一 workspace 路径

> 注意：F3 排在 F2 前面，因为 F2 依赖 F3 确定的路径语义。

**问题**：`backup.py:91` 硬编码 `Path("workspace")`；`restore.py` Step 4 tar 解压到 CWD，Step 5 用 `settings.memory.workspace_path`。两套路径不一致。且脚本是独立 CLI，不经过 preflight，C3 路径一致性校验不适用。

**方案**：

1. **脚本入口路径守卫**：两个脚本在获取 settings 后、执行主逻辑前，显式校验路径一致性并 fail-fast。
2. **全链路统一 workspace_dir**：tar / extract / reconcile / reindex 全部基于 `settings.workspace_dir.resolve()`。
3. **tar 布局统一**：archive 内部结构固定为 workspace_dir 的相对子路径。

#### F3a: 路径守卫（backup.py + restore.py 共用）

新增共用校验函数（放在 backup.py 和 restore.py 中各自 inline，或提取到 `src/infra/` 的辅助模块——因为只有两处使用，inline 更简单）：
```python
def _assert_workspace_path_consistency(settings: Settings) -> Path:
    """Fail-fast guard: workspace_dir must equal memory.workspace_path.

    ADR 0037: workspace_dir is the single source of truth.
    backup/restore are standalone CLIs that don't run preflight C3,
    so they must self-check.
    """
    ws = settings.workspace_dir.resolve()
    mem_ws = settings.memory.workspace_path.resolve()
    if ws != mem_ws:
        logger.error(
            "workspace_path_mismatch",
            workspace_dir=str(ws),
            memory_workspace_path=str(mem_ws),
        )
        print(
            f"ERROR: workspace_dir ({ws}) != memory.workspace_path ({mem_ws}).\n"
            f"Fix configuration. See ADR 0037.",
            file=sys.stderr,
        )
        sys.exit(1)
    return ws
```

在 `run_backup()` 和 `run_restore()` 开头调用，获取已验证的 workspace path。

#### F3b: backup.py 改动

- 移除 `workspace = Path("workspace")`。
- `run_backup()` 开头：
  ```python
  settings = get_settings()
  workspace = _assert_workspace_path_consistency(settings)
  ```
- tar 打包改为 `-C workspace`：
  ```python
  tar_sources = []
  if (workspace / "memory").is_dir():
      tar_sources.append("memory")
  if (workspace / "MEMORY.md").is_file():
      tar_sources.append("MEMORY.md")
  cmd = ["tar", "czf", str(archive_file), "-C", str(workspace), *tar_sources]
  ```
- archive 内部结构始终为 `memory/...` 和 `MEMORY.md`（相对于 workspace_dir）。

#### F3c: restore.py 全链路改动

- `run_restore()` 在 Step 3 ensure_schema 之前（获取 settings 之后）：
  ```python
  settings = get_settings()
  workspace = _assert_workspace_path_consistency(settings)
  ```
- **Step 4** tar 解压：
  ```python
  cmd = ["tar", "xzf", str(workspace_archive), "-C", str(workspace)]
  ```
- **Step 5** reconcile：
  ```python
  # 原：workspace_path = settings.memory.workspace_path
  # 改：
  evolution = EvolutionEngine(session_factory, workspace, settings.memory)
  ```
  EvolutionEngine 第二个参数是 `workspace_dir`，此处直接用已验证的 `workspace`。
- **Step 7** reindex：
  MemoryIndexer 构造为 `MemoryIndexer(session_factory, settings.memory)`。indexer 内部使用 `self._settings.workspace_path`。因为入口处已验证 `workspace_dir == memory.workspace_path`，indexer 读到的路径保证正确。
  这里**不改** MemoryIndexer 接口——路径守卫已确保一致性，改接口属于超出 scope 的重构。

### F2: P1-02 — restore 解压前清空目标 memory 文件

**问题**：Step 4 直接 tar 解压，workspace 中残留的旧 daily notes 不会被覆盖（只会新增），在 Step 7 reindex 时被索引。

**方案**：Step 4 前清空 `workspace_dir/memory/` 和 `workspace_dir/MEMORY.md`。

**改动 `scripts/restore.py`**：
- Step 3 (ensure_schema) 之后、Step 4 (tar extract) 之前，新增 Step 3.5：
  ```python
  # --- Step 3.5: Clear workspace memory files before extract ---
  memory_dir = workspace / "memory"
  if memory_dir.is_dir():
      shutil.rmtree(memory_dir)
  memory_md = workspace / "MEMORY.md"
  if memory_md.is_file():
      memory_md.unlink()
  logger.info("restore_step_3_5_done", cleared_dir=str(workspace))
  results.append(("3.5. Clear workspace memory", "OK"))
  ```
- `workspace` 来自 F3a 的 `_assert_workspace_path_consistency()` 返回值。
- 只清 memory 相关文件，不清 SOUL.md（由 Step 5 reconcile 处理）和其他 workspace 内容。

### F4: P2-01 — D2/DD3 daily notes 过滤 metadata-only 条目

**问题**：`doctor.py:239-240` 计数 daily notes 用 `sum(1 for s in sections if s.strip())`，indexer 会跳过仅含 `[HH:MM]...` metadata 行的条目（`_extract_entry_text()` 返回空）。导致 doctor 计数偏高。

**方案**：doctor 计数时复用 indexer 的 `_extract_entry_text()` 过滤逻辑。

**改动 `src/infra/doctor.py`**：
- 顶部新增 `from src.memory.indexer import MemoryIndexer`。
- D2 `_check_memory_index_health()` (line 239-240)：
  ```python
  # 原：file_count += sum(1 for s in sections if s.strip())
  # 改：
  file_count += sum(
      1 for s in sections
      if s.strip() and MemoryIndexer._extract_entry_text(s.strip())
  )
  ```
- DD3 `_check_memory_reindex_dryrun()` (line 511-512) 同理：
  ```python
  # 原：count = sum(1 for s in sections if s.strip())
  # 改：
  count = sum(
      1 for s in sections
      if s.strip() and MemoryIndexer._extract_entry_text(s.strip())
  )
  ```

### F5: P2-02 — C4 验证 workspace/memory 子目录可写

**问题**：`preflight.py:157-167` C4 只对 workspace 根做 tempfile 写入测试，memory 子目录不可写时不报错。

**方案**：memory_dir 存在性检查通过后，增加 tempfile 可写测试。

**改动 `src/infra/preflight.py` `_check_workspace_dirs()`**：
- 在 `if not memory_dir.is_dir()` 检查之后、return OK 之前，增加：
  ```python
  try:
      with tempfile.NamedTemporaryFile(dir=memory_dir, delete=True):
          pass
  except OSError as e:
      return CheckResult(
          name="workspace_dirs",
          status=CheckStatus.FAIL,
          evidence=f"memory/ subdirectory not writable: {e}",
          impact="Cannot write daily notes to memory directory",
          next_action="Fix filesystem permissions on workspace/memory/ directory",
      )
  ```

### F6: T-01 — 修复 CLI smoke test 硬编码路径

**问题**：3 个测试硬编码已删除的 worktree 路径 `/Users/zhiliangzhou/.../worktrees/backend-m5`。

**改动**：
- `tests/test_backup.py:153`、`tests/test_restore.py:253,272`：
  - 将 cwd 改为动态获取项目根：`cwd=str(Path(__file__).resolve().parent.parent)`。

## 4. 实施顺序

按依赖关系排序：

1. **F6 (T-01)** — 先修测试路径，确保现有测试通过，建立基线。
2. **F5 (P2-02)** — C4 增加 memory 子目录可写检查（独立）。
3. **F4 (P2-01)** — D2/DD3 daily notes 过滤对齐（独立）。
4. **F3 (P1-03)** — backup/restore 路径统一为 settings.workspace_dir + 入口守卫（F2 依赖此项）。
5. **F2 (P1-02)** — restore 解压前清空 memory 文件（依赖 F3）。
6. **F1 (P1-01)** — /health/ready 三层 readiness（改动最大，放最后）。

## 5. 测试策略

| Fix | 新增/修改测试 | 验证命令 |
|-----|--------------|----------|
| F1 | `tests/test_health_endpoints.py` — 新增：mock DB 掉线时 /health/ready 返回 not_ready；mock tracker.telegram_healthy=False 时返回 not_ready；mock provider 连续失败 ≥5 时返回 not_ready；mock 正常状态返回 ready | `uv run pytest tests/test_health_endpoints.py -v` |
| F1 | `tests/test_model_client.py`（若无则新增） — 验证：(1) 非流式 _retry_call 成功/失败时 record_success/failure 被调用；(2) 流式 _retry_call(defer_health=True) 创建成功时不调用 record_success；(3) 流式迭代完成后 record_success 被调用；(4) 流式迭代中断时 record_failure 被调用；(5) 流式创建失败时 record_failure 仍被调用 | `uv run pytest tests/test_model_client.py -v` |
| F2 | `tests/test_restore.py` — 新增：验证 Step 3.5 清空逻辑，tmp_path 中预置残留文件，执行后确认被删除 | `uv run pytest tests/test_restore.py -v` |
| F3 | `tests/test_backup.py` — 验证 tar -C 参数使用 settings.workspace_dir；新增测试验证路径不一致时 sys.exit(1) | `uv run pytest tests/test_backup.py -v` |
| F3 | `tests/test_restore.py` — 验证 tar -C 参数使用 settings.workspace_dir；验证路径守卫 | `uv run pytest tests/test_restore.py -v` |
| F4 | `tests/test_doctor.py` — 新增/修改 D2/DD3 测试：构造含 metadata-only 条目的 daily note，验证不计入 file_count | `uv run pytest tests/test_doctor.py -v` |
| F5 | `tests/test_preflight.py` — 新增：mock memory_dir 不可写，验证 C4 返回 FAIL | `uv run pytest tests/test_preflight.py -v` |
| F6 | 修复现有 3 个测试的 cwd | `uv run pytest tests/test_backup.py tests/test_restore.py -v` |

**最终验证**：`just test` (全量 810+ tests) + `just lint`。

## 6. 改动文件汇总

| 文件 | Fix | 改动类型 |
|------|-----|----------|
| `src/infra/health.py` | F1 | 新增 ComponentHealthTracker |
| `src/infra/preflight.py` | F1, F5 | 新增 run_readiness_checks()；C4 增加 memory_dir 可写检查 |
| `src/agent/model_client.py` | F1 | OpenAICompatModelClient.__init__ 增加 health_tracker 参数；_retry_call 接线 |
| `src/gateway/app.py` | F1 | lifespan 存 engine/settings/tracker 到 app.state；model client 构造注入 tracker；_on_polling_done 闭包化；/health/ready 三层整合 |
| `scripts/backup.py` | F3 | 移除硬编码 Path("workspace")；新增路径守卫；tar -C workspace_dir |
| `scripts/restore.py` | F2, F3 | 新增路径守卫；Step 3.5 清空 memory；Step 4 tar -C workspace_dir；Step 5 用 workspace_dir |
| `src/infra/doctor.py` | F4 | D2/DD3 计数复用 _extract_entry_text 过滤 |
| `tests/test_backup.py` | F3, F6 | cwd 动态化；新增路径守卫测试 |
| `tests/test_restore.py` | F2, F3, F6 | cwd 动态化；新增清空/路径守卫测试 |
| `tests/test_health_endpoints.py` | F1 | 新增三层 readiness 测试 |
| `tests/test_model_client.py` | F1 | 新增/修改 tracker 接线测试 |
| `tests/test_preflight.py` | F5 | 新增 memory_dir 不可写测试 |
| `tests/test_doctor.py` | F4 | 新增 metadata-only 过滤测试 |

## 7. 不做的事情

- 不引入后台健康探针轮询线程（过度工程）。
- 不做 per-request 外部探测（不每次 readiness 都打 getMe / model API）。
- 不重构 backup/restore 为 async（脚本场景，subprocess 为主）。
- 不改动 MEMORY.md 计数逻辑（`_count_curated_sections` 在 9eb7946 已修正）。
- 不引入 provider 熔断器/降级策略（超出 M5 scope，连续失败计数已足够表达 readiness）。
- 不改 MemoryIndexer 接口签名（路径守卫已确保一致性，改接口属于超 scope 重构）。
- 不改 dispatch_chat / AgentLoop / TelegramAdapter 接口签名（tracker 在 ModelClient 层注入，不需要传播到上层）。
