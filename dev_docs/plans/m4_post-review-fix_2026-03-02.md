# M4 Post-Review 修正计划

> 日期：2026-03-02
> 状态：**approved**
> 触发：用户审阅 M4 交付物，4 项 Findings (2×P1, 1×P2, 1×P3)
> 基线：668 tests，main HEAD `549253f`

## Context

M4 Telegram 第二渠道适配模块测试全绿（668 tests），但审阅发现：
- F1 [P1]: WebSocket 侧 session_id 无隔离校验，可直读直写 Telegram 会话
- F2 [P1]: 代码块内超长单行无 hard cut，Telegram 拒发导致用户收不到回复
- F3 [P2]: Telegram polling 致命异常静默吞，渠道死而不报
- F4 [P3]: `TELEGRAM_MESSAGE_MAX_LENGTH` 无边界校验，0 或 >4096 均触发运行时错误

## Findings 索引

| # | 严重性 | 摘要 | 文件 |
|---|--------|------|------|
| F1 | P1 | WS session_id 可访问 Telegram 会话，跨渠道隔离非强约束 | `app.py:266,326` `protocol.py:10` |
| F2 | P1 | code block 超长单行未 hard cut，chunk 超限 Telegram 拒发 | `telegram_render.py:90` |
| F3 | P2 | polling done_callback 只打日志，渠道静默不可用 | `app.py:176` |
| F4 | P3 | message_max_length 无 validator，0/负数/超限均不安全 | `settings.py:178` |

## 修改文件清单

| # | 文件 | 改动性质 | Finding |
|---|------|---------|---------|
| 1 | `src/gateway/protocol.py` | `ChatSendParams` / `ChatHistoryParams` 增加 session_id validator | F1 |
| 2 | `src/gateway/app.py` | `_handle_chat_history` 补 ValidationError→GatewayError 映射；`_on_polling_done` 提取为模块级 + SIGTERM | F1, F3 |
| 3 | `src/channels/telegram_render.py` | `_split_code_block` 增加单行 hard cut | F2 |
| 4 | `src/config/settings.py` | `TelegramSettings.message_max_length` 增加 `Field(ge=1, le=4096)` | F4 |
| 5 | `tests/test_dispatch.py` | 新增 channel prefix 拒绝测试 | F1 |
| 6 | `tests/test_telegram_render.py` | 新增超长单行 code block 测试 | F2 |
| 7 | `tests/test_settings.py` | 新增 message_max_length 边界校验测试 | F4 |
| 8 | `tests/test_app_integration.py` | 新增 polling 异常触发退出测试 + chat.history INVALID_PARAMS 测试 | F1, F3 |

## Step 1 — F1 [P1]: WS session_id channel prefix 隔离

### 设计选择

**选项 A**: 在 `ChatSendParams` / `ChatHistoryParams` 的 Pydantic validator 中拒绝渠道独占前缀
**选项 B**: 在 `_handle_chat_send` / `_handle_chat_history` 业务层校验
**选项 C**: 引入 session ownership 模型（每个连接绑定可访问的 session 列表）

**选择 A**：
- 最轻量，Pydantic 层拦截，不需要连接状态
- 个人 agent 场景只需阻止"WS 不能假冒渠道独占 session"，不需要完整 RBAC
- 未来扩展新渠道只需在 `CHANNEL_EXCLUSIVE_PREFIXES` 列表中追加前缀
- 选项 C 过度工程，选项 B 校验逻辑分散

**放弃 B**：校验在 Pydantic 层即可完成，不需要在 handler 层重复。
**放弃 C**：引入 session ownership 需要连接级状态管理和鉴权基础设施，个人 agent 不需要。

### per-peer scope 与前缀拦截的关系

scope_resolver 支持三种 dm_scope（scope_resolver.py:21-39）：

| dm_scope | session_id 格式 | 语义 |
|----------|----------------|------|
| `main` | `"main"` | 全局共享 |
| `per-channel-peer` (默认) | `"telegram:peer:{id}"` | 渠道独占——不同渠道的 peer 隔离 |
| `per-peer` | `"peer:{id}"` | 跨渠道共享——同一 peer 跨渠道命中同一 session |

`per-peer` 的设计意图是"同一用户跨渠道共享 session"，但这要求**两端都有身份验证**。Telegram 侧有 `TELEGRAM_ALLOWED_USER_IDS` 门控，WebSocket 侧**没有任何身份概念**。任何 WS 客户端都能填 `session_id="peer:456"` 读写其他 Telegram 用户的会话。因此：
- `telegram:` 前缀 → **拦截**（渠道独占）
- `peer:` 前缀 → **同样拦截**（WS 无认证，不能证明是同一 peer）

`CHANNEL_EXCLUSIVE_PREFIXES = ("telegram:", "peer:")` 拦截所有 resolver 生成的 session_id 格式。WS 客户端只能访问 `main` 或自定义 session_id（不以保留前缀开头）。

**影响**：`per-peer` 的"跨渠道共享"在 WS 没有认证之前实际不可用。这是正确的安全边界——未来 WS 引入认证后，可按验证后的身份放行特定 `peer:` 前缀。

### 1a. `src/gateway/protocol.py`

```python
# resolver 生成的 session_id 前缀——WS 无认证，不得访问这些 session
# 涵盖 per-channel-peer ("telegram:peer:{id}") 和 per-peer ("peer:{id}")
CHANNEL_EXCLUSIVE_PREFIXES = ("telegram:", "peer:")

class ChatSendParams(BaseModel):
    content: str
    session_id: str = "main"
    provider: str | None = None

    @field_validator("session_id", mode="after")
    @classmethod
    def _reject_channel_exclusive_prefix(cls, v: str) -> str:
        for prefix in CHANNEL_EXCLUSIVE_PREFIXES:
            if v.startswith(prefix):
                msg = f"session_id with channel-exclusive prefix '{prefix}' cannot be accessed via WebSocket"
                raise ValueError(msg)
        return v

    # ... existing provider validator ...

class ChatHistoryParams(BaseModel):
    session_id: str = "main"

    @field_validator("session_id", mode="after")
    @classmethod
    def _reject_channel_exclusive_prefix(cls, v: str) -> str:
        for prefix in CHANNEL_EXCLUSIVE_PREFIXES:
            if v.startswith(prefix):
                msg = f"session_id with channel-exclusive prefix '{prefix}' cannot be accessed via WebSocket"
                raise ValueError(msg)
        return v
```

- 校验在 Pydantic 层，`_handle_chat_send` 的 `model_validate` 自动触发，无效 session_id 走现有 `GatewayError(code="INVALID_PARAMS")` 路径
- `CHANNEL_EXCLUSIVE_PREFIXES` 作为元组，方便未来扩展新渠道
- 两个 Params 类分别校验（不抽取公共 mixin，避免过度抽象；两处代码完全相同可接受）

### 1b. `src/gateway/app.py` — `_handle_chat_history` 异常映射

当前 `_handle_chat_history` (app.py:326) 裸调 `ChatHistoryParams.model_validate(params)`，ValidationError 冒泡到 `_handle_rpc_message` 的 `except Exception` → `INTERNAL_ERROR`。需要与 `_handle_chat_send` 保持一致：

```python
async def _handle_chat_history(
    websocket: WebSocket, request_id: str, params: dict
) -> None:
    """Handle chat.history: return session message history."""
    try:
        parsed = ChatHistoryParams.model_validate(params)
    except ValidationError as e:
        raise GatewayError(str(e), code="INVALID_PARAMS") from e
    # ... rest unchanged ...
```

这确保 WS 客户端传入 `session_id="telegram:peer:123"` 时收到 `INVALID_PARAMS` 而非 `INTERNAL_ERROR`。

### 1c. `tests/test_dispatch.py`

新增测试：

```python
class TestSessionIdPrefixGuard:
    """F1: WS cannot access channel-exclusive sessions."""

    def test_chat_send_rejects_telegram_prefix(self):
        with pytest.raises(ValidationError, match="channel-exclusive prefix"):
            ChatSendParams(content="hi", session_id="telegram:peer:12345")

    def test_chat_send_allows_normal_session(self):
        p = ChatSendParams(content="hi", session_id="my-session")
        assert p.session_id == "my-session"

    def test_chat_send_allows_main_default(self):
        p = ChatSendParams(content="hi")
        assert p.session_id == "main"

    def test_chat_send_rejects_peer_prefix(self):
        """per-peer scope: WS has no identity, cannot access peer sessions."""
        with pytest.raises(ValidationError, match="channel-exclusive prefix"):
            ChatSendParams(content="hi", session_id="peer:12345")

    def test_chat_history_rejects_telegram_prefix(self):
        with pytest.raises(ValidationError, match="channel-exclusive prefix"):
            ChatHistoryParams(session_id="telegram:peer:12345")

    def test_chat_history_rejects_peer_prefix(self):
        with pytest.raises(ValidationError, match="channel-exclusive prefix"):
            ChatHistoryParams(session_id="peer:12345")

    def test_chat_history_allows_normal_session(self):
        p = ChatHistoryParams(session_id="my-session")
        assert p.session_id == "my-session"
```

## Step 2 — F2 [P1]: code block 超长单行 hard cut

### 2a. `src/channels/telegram_render.py` — `_split_code_block`

在行迭代中，当单行 `len(line) > effective` 时，对该行做 hard cut，每段作为独立 chunk 输出（重新包围 fences）：

```python
def _split_code_block(block: str, max_length: int) -> list[str]:
    # ... 现有 header/body/footer 解析不变 ...

    for line in body_lines:
        line_len = len(line) + 1
        if line_len > effective:
            # 超长单行: 先 flush current，然后 hard cut 该行
            if current:
                chunks.append(header + "\n" + "\n".join(current) + "\n" + footer)
                current = []
                current_len = 0
            for i in range(0, len(line), effective):
                chunk_line = line[i : i + effective]
                chunks.append(header + "\n" + chunk_line + "\n" + footer)
            continue
        if current_len + line_len > effective and current:
            chunks.append(header + "\n" + "\n".join(current) + "\n" + footer)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len

    # ... 现有 tail flush 不变 ...
```

- hard cut 的每段都用 fences 包围，保持代码块格式
- `range(0, len(line), effective)` 保证每段 ≤ effective
- 完整 chunk 长度 = `len(header) + 1 + effective + 1 + len(footer)` = overhead + effective = max_length

### 2b. `tests/test_telegram_render.py`

新增测试：

```python
class TestCodeBlockLongLine:
    """F2: ultra-long single line in code block must be hard-cut."""

    def test_single_long_line_split(self):
        """A 5000-char single line in a code block should produce multiple valid chunks."""
        long_line = "x" * 5000
        block = f"```\n{long_line}\n```"
        chunks = split_message(block, max_length=200)
        for chunk in chunks:
            assert len(chunk) <= 200
            assert chunk.startswith("```")
            assert chunk.endswith("```")

    def test_mixed_normal_and_long_lines(self):
        """Normal lines and a long line in the same code block."""
        lines = ["short line 1", "a" * 3000, "short line 2"]
        block = "```python\n" + "\n".join(lines) + "\n```"
        chunks = split_message(block, max_length=500)
        for chunk in chunks:
            assert len(chunk) <= 500

    def test_long_line_preserves_content(self):
        """All content from the long line is present across chunks."""
        long_line = "".join(str(i % 10) for i in range(1000))
        block = f"```\n{long_line}\n```"
        chunks = split_message(block, max_length=200)
        # Extract content between fences
        content = ""
        for chunk in chunks:
            inner = chunk.removeprefix("```\n").removesuffix("\n```")
            content += inner
        assert content == long_line
```

## Step 3 — F3 [P2]: polling 异常触发进程退出

### 3a. `src/gateway/app.py` — `_on_polling_done`

首先在模块顶层新增导入（当前 app.py 没有 `import os` 和 `import signal`）：

```python
import os
import signal
```

然后定义模块级函数：

```python
def _on_polling_done(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("telegram_polling_fatal", error=str(exc))
        # fail-fast: 个人 agent 不应静默降级
        os.kill(os.getpid(), signal.SIGTERM)
```

`os` / `signal` 必须在模块顶层导入，不能在函数体内 import。原因：测试使用 `patch("src.gateway.app.os.kill")` 补丁模块属性，函数体内 import 会绕过 patch 导致测试无效。

**设计选择**：

- **选择 SIGTERM**（非 SIGKILL）：允许 FastAPI/uvicorn 优雅退出（shutdown hooks、连接清理）
- **不选 health flag**：个人 agent 没有外部健康探针消费者，标记 `telegram_healthy=False` 没有实际收益，反而增加"半死"状态
- **不选 loop.stop()**：在 done_callback 中调用 `loop.stop()` 可能导致未完成的 coroutine 被丢弃，SIGTERM 更安全
- `check_ready()` (line 174) 继续保持 fail-fast 覆盖启动阶段 token 错误，`_on_polling_done` 覆盖运行时崩溃

### 3b. `tests/test_app_integration.py`

测试分两层：单元测试验证函数行为，集成测试验证 lifespan 接线。

**单元测试**（验证 `_on_polling_done` 函数本身）：

```python
class TestPollingDoneCallback:
    """F3 unit: _on_polling_done function behavior."""

    def test_sends_sigterm_on_exception(self):
        task = asyncio.Future()
        task.set_exception(RuntimeError("polling crashed"))

        with patch("src.gateway.app.os.kill") as mock_kill:
            _on_polling_done(task)
            mock_kill.assert_called_once()
            _, sig = mock_kill.call_args[0]
            assert sig == signal.SIGTERM

    def test_ignores_cancelled_task(self):
        task = asyncio.Future()
        task.cancel()

        with patch("src.gateway.app.os.kill") as mock_kill:
            _on_polling_done(task)
            mock_kill.assert_not_called()
```

**集成测试**（验证 lifespan 接线——callback 被注册且触发）：

```python
class TestTelegramPollingLifespanWiring:
    """F3 integration: lifespan correctly wires done_callback for polling task."""

    @pytest.mark.asyncio
    async def test_polling_crash_triggers_sigterm_via_lifespan(self):
        """Full wiring: adapter.start_polling raises → done_callback → SIGTERM."""
        mock_adapter = AsyncMock()
        mock_adapter.check_ready = AsyncMock()
        mock_adapter.start_polling = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )
        mock_adapter.stop = AsyncMock()

        with (
            patch("src.gateway.app.get_settings") as mock_settings,
            patch("src.channels.telegram.TelegramAdapter", return_value=mock_adapter),
            patch("src.gateway.app.os.kill") as mock_kill,
            # ... mock other lifespan dependencies (engine, registry, etc.) ...
        ):
            # Configure settings to enable Telegram
            mock_settings.return_value.telegram.bot_token = "fake:token"
            # ... setup other required settings ...

            # Run lifespan — start_polling will be create_task'd and immediately fail
            async with lifespan(app):
                # Give event loop a tick to let the task fail + callback fire
                await asyncio.sleep(0.1)

            # Verify the full chain: create_task → fail → done_callback → SIGTERM
            mock_kill.assert_called_once()
            _, sig = mock_kill.call_args[0]
            assert sig == signal.SIGTERM
```

集成测试的关键点：
- Mock `start_polling` 使其立即抛异常（模拟运行时崩溃）
- 跑真实 lifespan，不跳过 Telegram 分支
- `await asyncio.sleep(0.1)` 让 event loop 执行 done_callback
- 验证 `os.kill` 被调用 — 证明接线完整

注意：lifespan 有较多依赖（engine, registry, session_manager 等），集成测试需要 mock 完整依赖链。如果 mock 层级过深导致脆弱，可考虑只 mock Telegram 相关部分，其余走现有 test fixture。具体 mock 范围在实现时按实际依赖调整。

### 3c. 重构：提取 `_on_polling_done` 为模块级函数

当前 `_on_polling_done` 定义在 `lifespan()` 闭包内（line 176），无法直接测试。提取为模块级函数：

3a 中已给出完整的模块级函数定义（含顶层 `import os` / `import signal`）。lifespan 中删除闭包定义，改为引用模块级 `_on_polling_done`：`polling_task.add_done_callback(_on_polling_done)`（无行为变化，纯提取重构）。

## Step 4 — F4 [P3]: message_max_length 边界校验

### 4a. `src/config/settings.py`

```python
from pydantic import Field

class TelegramSettings(BaseSettings):
    """Telegram channel settings. Env vars prefixed with TELEGRAM_."""

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    bot_token: str = ""
    dm_scope: str = "per-channel-peer"
    allowed_user_ids: str = ""
    message_max_length: int = Field(default=4096, ge=1, le=4096)
```

- `ge=1` 防止 0 和负数
- `le=4096` 防止超过 Telegram 平台限制
- 启动时 Pydantic 校验失败即 fail-fast，不会进入运行时

### 4b. `tests/test_settings.py`

新增测试：

```python
class TestTelegramMessageMaxLength:
    """F4: message_max_length boundary validation."""

    def test_default_value(self):
        s = TelegramSettings()
        assert s.message_max_length == 4096

    def test_zero_rejected(self):
        with pytest.raises(ValidationError):
            TelegramSettings(message_max_length=0)

    def test_negative_rejected(self):
        with pytest.raises(ValidationError):
            TelegramSettings(message_max_length=-1)

    def test_exceeds_telegram_limit_rejected(self):
        with pytest.raises(ValidationError):
            TelegramSettings(message_max_length=5000)

    def test_valid_custom_value(self):
        s = TelegramSettings(message_max_length=2048)
        assert s.message_max_length == 2048
```

## 执行顺序

1. F4 (最简单，独立) → 验证 `just test` 绿
2. F2 (独立，纯 render 逻辑) → 验证 `just test` 绿
3. F1 (需要改 protocol + 测试) → 验证 `just test` 绿
4. F3 (需要重构提取 + 测试) → 验证 `just test` + `just lint` 绿
5. 全量回归确认

## 风险

| # | 风险 | 缓解 |
|---|------|------|
| R1 | F1 prefix 校验可能破坏现有测试中使用 `telegram:` 前缀的 session_id | 搜索全量测试确认；test_channel_isolation.py 通过 scope_resolver 直接测试，不走 ChatSendParams |
| R2 | F3 SIGTERM 在测试环境中可能真的杀进程 | mock `os.kill`，不实际发信号 |
| R3 | F2 hard cut 破坏代码块可读性 | 可接受的降级——超长单行本身不可读，拆分后至少能发出去 |

## 审阅修订记录

| 轮次 | 修订内容 |
|------|---------|
| Rev 1 (draft) | 初稿 |
| Rev 2 | [P1] F1 `RESERVED_CHANNEL_PREFIXES` → `CHANNEL_EXCLUSIVE_PREFIXES`，补充 per-peer scope 设计论证；[P2] 补 `_handle_chat_history` ValidationError→GatewayError 异常映射 + 测试；修改文件清单统一口径 |
| Rev 3 | [P1] F1 `peer:` 前缀也必须拦截——WS 无认证不能证明是同一 peer，`CHANNEL_EXCLUSIVE_PREFIXES` 扩展为 `("telegram:", "peer:")`，测试改为 rejects_peer_prefix；[P2] F3 补充 lifespan 接线集成测试——mock crashing adapter 跑真实 lifespan 验证完整 callback 注册链 |
| Rev 4 | [P2] F3 `_on_polling_done` 中 `os`/`signal` 从函数体内 import 改为模块顶层导入，与 `patch("src.gateway.app.os.kill")` 补丁路径一致；3a 和 3c 代码示例统一 |
