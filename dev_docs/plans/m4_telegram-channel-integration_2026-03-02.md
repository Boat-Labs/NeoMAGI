# M4 实现计划：Telegram 第二渠道适配

> 状态：approved (rev5)
> 日期：2026-03-02
> 依据：`design_docs/roadmap_milestones_v3.md`（M4）、ADR 0003、ADR 0034

## Context

NeoMAGI 当前只有 WebChat 一个入口渠道。M4 的目标是新增 Telegram 作为第二入口，在保持核心能力和安全策略一致的前提下，激活并验证 `per-channel-peer` dmScope 策略，实现跨渠道会话和记忆的完整隔离。

**输入基线**：562 tests 全绿，ruff clean；`src/channels/` 为空；scope_resolver 已预留 M4 扩展点。

**用户选型**：
- **Bot 库**：aiogram 3.x（轻量、原生 async、与 FastAPI 风格一致）
- **范围**：仅 DM（私聊），不含群组聊天
- **Scope**：Telegram DM 使用 `per-channel-peer`；全局 `SessionSettings.dm_scope` 维持 `main`（WebChat 安全基线不动）

**关键改动面评估**（rev2 修正）：

AgentLoop 并非"近乎 channel-agnostic"——以下路径硬编码了 scope 重算，需要 M4 修正：

| 位置 | 问题 | 修正 |
|------|------|------|
| `agent.py:250` `handle_message` | 硬编码 `SessionIdentity(session_id, channel_type="dm")` + `self._session_settings.dm_scope` | 接受外部 identity + dm_scope 参数 |
| `agent.py:828` `_persist_flush_candidates` | 用裸 `SessionIdentity(session_id=c.source_session_id)` + `self._session_settings.dm_scope` 重算 scope | 改为接收显式 `scope_key` 参数（和 `_execute_tool` 同模式） |
| `scope_resolver.py:50` `resolve_session_key` | 用 `channel_type == "dm"` 判断 DM 路径，Telegram 私聊 (`channel_type="telegram"`) 会落入 group 分支报错 | 改为基于 `channel_id is None` 判断 DM-like 路径 |

**session_id 与 scope_key 分离**（rev3 修正）：

两者是不同概念，不能混用：
- **session_id**（存储键）：用于 session claim/release、budget gate、消息存储、eval_run_id 提取。WebChat 为客户端透传值（如 `"my-session"`, `"m6_eval_openai_T10_12345"`），Telegram 由 resolver 派生。
- **scope_key**（记忆隔离键）：用于 memory recall/search/flush。由 `resolve_scope_key(identity, dm_scope)` 统一产出。

session_id 的确定是 **adapter 层职责**，dispatch 层接收已确定的值：
- WebChat adapter：`session_id = parsed.session_id`（透传，不走 resolver）
- Telegram adapter：`session_id = resolve_session_key(identity, dm_scope)`（通过 resolver 派生）

---

## Phase 0：Config + Scope 激活

**目标**：新增 Telegram 配置；在 scope resolver 纯函数层实现全量 scope 分支；保持 `SessionSettings.dm_scope` 锁定 `main`。

### 任务

**0.1 新增 TelegramSettings** — `src/config/settings.py`（修改）
- 新增 `TelegramSettings(BaseSettings)`, env_prefix=`TELEGRAM_`
  - `bot_token: str = ""`（空 = 禁用）
  - `dm_scope: str = "per-channel-peer"`（独立于全局 SessionSettings，有自己的 validator）
  - `allowed_user_ids: str = ""`（逗号分隔 Telegram user ID 白名单）
  - `message_max_length: int = 4096`
- `TelegramSettings._validate_dm_scope`: 接受 `{"per-channel-peer", "per-peer", "main"}`
- 在 `Settings` 根类中增加 `telegram: TelegramSettings`

**0.2 SessionSettings.dm_scope 保持锁定** — `src/config/settings.py`（不改）
- `SessionSettings._validate_dm_scope` 继续只接受 `"main"`
- 理由：WebChat 默认 identity 没有 peer_id，若误配 per-channel-peer 会运行时 ValueError
- Telegram 的 dm_scope 从 `TelegramSettings.dm_scope` 读取，两条路径独立

**0.3 激活 scope resolver** — `src/session/scope_resolver.py`（修改）
- `resolve_scope_key()` 实现全量分支（纯函数，不受 config 约束）：
  ```python
  if dm_scope == "main":
      return "main"
  if dm_scope == "per-channel-peer":
      if identity.peer_id is None:
          raise ValueError("peer_id required for per-channel-peer")
      return f"{identity.channel_type}:peer:{identity.peer_id}"
  if dm_scope == "per-peer":
      if identity.peer_id is None:
          raise ValueError("peer_id required for per-peer")
      return f"peer:{identity.peer_id}"
  raise ValueError(f"Unsupported dm_scope: '{dm_scope}'")
  ```
- `resolve_session_key()` 修正 DM/group 路由判据（**Finding 3 修正**）：
  ```python
  def resolve_session_key(identity, dm_scope="main"):
      if identity.channel_id is None:
          # DM-like: WebChat DM, Telegram DM
          return resolve_scope_key(identity, dm_scope)
      # Group: session key is group-scoped
      return f"group:{identity.channel_id}"
  ```
  - 向后兼容：WebChat DM (channel_id=None) → 走 scope 路径；group (channel_id 非 None) → 走 group 路径
  - Telegram DM (channel_type="telegram", channel_id=None) → 正确走 scope 路径

**0.4 更新 `.env_template`** — 增加 Telegram 配置块
```
# Telegram Bot
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=
# TELEGRAM_DM_SCOPE=per-channel-peer
```

### 测试
- `tests/test_settings.py`：
  - TelegramSettings 默认值 (dm_scope="per-channel-peer", bot_token="")
  - TelegramSettings.dm_scope 接受 per-channel-peer / per-peer / main
  - TelegramSettings.dm_scope 拒绝无效值
  - SessionSettings.dm_scope 仍然只接受 "main"（**不改**）
- `tests/test_scope_resolver.py`：
  - per-channel-peer 解析：`telegram:peer:123`
  - per-peer 解析：`peer:123`
  - 缺 peer_id 时 ValueError
  - 未知 scope ValueError
  - `resolve_session_key` Telegram DM (channel_id=None) 走 scope 路径
  - 更新 `test_non_main_raises` → 测试未知 scope（如 `"invalid_scope"`）报错

### 完成标准
全量回归绿。SessionSettings.dm_scope 仍锁定 main。resolve_scope_key + resolve_session_key 支持 per-channel-peer。

---

## Phase 1：Dispatch 抽取 + AgentLoop 改动

**目标**：将 `_handle_chat_send` 中的共享调度逻辑抽取为 channel-agnostic 核心函数；修正 AgentLoop 中所有 scope 硬编码路径。

### 任务

**1.1 新建 `src/gateway/dispatch.py`** — 核心调度
```python
async def dispatch_chat(
    content: str,
    session_id: str,       # 已确定的存储键（adapter 负责确定，dispatch 不做 resolve）
    identity: SessionIdentity,  # scope 解析用（传入 AgentLoop）
    dm_scope: str,              # scope 解析用（传入 AgentLoop）
    *,
    registry: AgentLoopRegistry,
    session_manager: SessionManager,
    budget_gate: BudgetGate,
    gateway_settings: GatewaySettings,
    provider: str | None = None,
) -> AsyncIterator[AgentEvent]:
```
封装完整流程：
1. Provider routing（从 AgentLoopRegistry）
2. Session claim（用 `session_id`）+ force reload
3. Budget reserve（用 `session_id`，保留 eval_run_id 提取逻辑）
4. `agent_loop.handle_message(session_id, content, identity=identity, dm_scope=dm_scope, lock_token=lock_token)` 迭代 yield 事件
5. Session release + budget settle（finally block）

`session_id` 用于所有存储操作（claim/release/budget/history）。`identity` + `dm_scope` 仅传入 AgentLoop 用于 scope_key 解析（memory recall + tool exec + flush）。两者分离。

SESSION_BUSY 和 BUDGET_EXCEEDED 通过 raise `GatewayError` 传播，由调用方转换为渠道特定格式。

**1.2 扩展 AgentLoop.handle_message 签名** — `src/agent/agent.py`（修改 L223-253）
- 新增可选参数：`identity: SessionIdentity | None = None`, `dm_scope: str | None = None`
- `identity` 为 None → 保持现有行为（构造 `SessionIdentity(session_id, channel_type="dm")`）
- `dm_scope` 为 None → 回退到 `self._session_settings.dm_scope`
- 修改 L249-253 使用传入参数：
  ```python
  effective_identity = identity or SessionIdentity(session_id=session_id, channel_type="dm")
  effective_dm_scope = dm_scope or self._session_settings.dm_scope
  scope_key = resolve_scope_key(effective_identity, dm_scope=effective_dm_scope)
  ```

**1.3 修正 `_persist_flush_candidates` scope 传播** — `src/agent/agent.py`（**Finding 1 修正**）
- 签名新增 `scope_key: str` 参数
- 移除内部 scope 重算，直接使用传入的 scope_key：
  ```python
  async def _persist_flush_candidates(
      self, candidates: list[Any], session_id: str, *, scope_key: str,
  ) -> None:
      resolved = [
          ResolvedFlushCandidate(
              candidate_text=c.candidate_text,
              scope_key=scope_key,  # 使用传入值，不重算
              source_session_id=c.source_session_id,
              confidence=c.confidence,
              constraint_tags=tuple(c.constraint_tags),
          )
          for c in candidates
      ]
  ```
- 调用方 `_run_compaction`（L663）传入 `scope_key=scope_key`
- 这与 `_execute_tool(scope_key=scope_key, ...)` 的模式完全一致（ADR 0034 显式参数传播）

**1.3a 适配既有测试** — `tests/test_agent_flush_persist.py`（修改，**Finding 7 修正**）
- 该文件 6 处调用旧签名 `_persist_flush_candidates(candidates, session_id)` 需全部增加 `scope_key=` 关键字参数：
  - L58, L79, L86, L113, L158: 传入 `scope_key="main"`
  - L130: `test_scope_key_from_candidate_session_id` 改为传入 `scope_key="main"` 并验证 scope 来自显式参数（非重算）
- `test_scope_key_from_candidate_session_id`（L116-138）需重写：
  - 旧行为断言"scope 从 candidate.source_session_id 重算"
  - 新行为断言"scope 来自传入的 scope_key 参数，与 candidate.source_session_id 无关"
  - 修改为：传入 `scope_key="custom-scope"`，验证写入文件包含 `scope: custom-scope`
- 模块 docstring（L7 `scope_key resolved from candidate.source_session_id`）改为 `scope_key passed as explicit parameter (ADR 0034)`

**1.4 重构 `_handle_chat_send`** — `src/gateway/app.py`（修改 L254-387）
- 改为调用 `dispatch_chat()` + 将 yielded 事件包装为 WebSocket RPC 消息
- WebSocket 路径传入（**session_id 透传，不走 resolver**，rev3 修正）：
  ```python
  session_id = parsed.session_id  # 客户端透传，保持现有语义
  identity = SessionIdentity(session_id=session_id, channel_type="dm")
  dm_scope = settings.session.dm_scope  # "main"
  # 不调用 resolve_session_key —— WebChat session_id 由客户端决定
  # scope_key 在 AgentLoop 内部通过 resolve_scope_key(identity, "main") 产出 "main"
  await dispatch_chat(content=parsed.content, session_id=session_id,
                      identity=identity, dm_scope=dm_scope, ...)
  ```
- 保证向后兼容：`session_id="my-session"` 透传不变；`m6_eval_*` 前缀解析不受影响
- 纯重构，行为不变

### 测试
- `tests/test_dispatch.py`（新建）：
  - 正常 dispatch yield 事件
  - SESSION_BUSY、BUDGET_EXCEEDED、provider 不存在
  - **session_id 透传**：dispatch_chat 用传入的 session_id 调用 session claim 和 budget reserve（不折叠为 "main"）
- `tests/test_agent_identity.py`（新建）：
  - handle_message 传入 Telegram identity + per-channel-peer → scope_key 正确
  - handle_message 不传 identity → 向后兼容 (channel_type="dm", dm_scope="main")
  - _persist_flush_candidates 使用传入的 scope_key（不重算）
- 现有测试回归：`test_budget_gate_wiring.py` session_id="my-session" 透传断言不变

### 完成标准
现有测试全绿（含 budget gate wiring 透传断言）。dispatch_chat 可被独立调用。session_id 与 scope_key 分离：session_id 用于存储操作，scope_key 用于记忆隔离。

---

## Phase 2：Telegram Adapter

**目标**：实现 aiogram 适配器，将 Telegram DM 消息映射到 `dispatch_chat()` 调用。

### 任务

**2.1 添加依赖** — `pyproject.toml`（修改）
- `"aiogram>=3.15.0"` 加入 dependencies

**2.2 实现 `src/channels/telegram.py`**（新建）
```python
class TelegramAdapter:
    """Bridges Telegram DM messages to NeoMAGI dispatch core."""

    def __init__(self, bot_token, telegram_settings, registry,
                 session_manager, budget_gate, gateway_settings): ...

    async def check_ready(self):    # bot.get_me() 验证 token + 连通性，fail-fast
    async def start_polling(self):  # 启动 long polling（阻塞协程，由 create_task 包装）
    async def stop(self):           # 优雅停止 polling
    async def _handle_dm(self, message: Message):
        # 1. 鉴权：message.from_user.id in allowed_users
        # 2. 通过 resolver 构建身份和 session key
        # 3. 启动 typing 指示器
        # 4. 调用 dispatch_chat，buffer 全部事件
        # 5. 格式化 + 发送响应
```

**身份映射（通过 resolver，非手工拼接）**（**Finding 3 修正**）：
```python
peer_id = str(message.from_user.id)
identity = SessionIdentity(
    session_id="",  # placeholder, will be resolved
    channel_type="telegram",
    peer_id=peer_id,
)
dm_scope = self._settings.dm_scope  # "per-channel-peer"
session_id = resolve_session_key(identity, dm_scope)
# session_id = "telegram:peer:123" (derived by resolver)
identity = SessionIdentity(
    session_id=session_id, channel_type="telegram", peer_id=peer_id,
)
```
session_key 和 scope_key 全部由 resolver 产出，单一真源（ADR 0034）。

**设计要点**：
- Telegram 不支持流式输出 → buffer 所有 TextChunk 事件，拼接后一次性发送
- Typing 指示器：后台 task 每 4 秒发送 `ChatAction.TYPING`，处理完成后 cancel
- 同进程协同：aiogram Dispatcher 通过 `asyncio.create_task(dp.start_polling(bot))` 在 FastAPI event loop 中运行

**2.3 鉴权门控**
- `TELEGRAM_ALLOWED_USER_IDS` 为空 → 拒绝所有消息（fail-closed，个人 agent 安全优先）
- 非白名单用户 → log warning + 忽略（不回复）

**2.4 生命周期语义**（**Finding 4 + Finding 8 修正**）
- 三个公开方法，职责分离：
  - `check_ready()`：`await self._bot.get_me()` 验证 token + 连通性；失败抛异常阻止启动（fail-fast）；成功后记录 `self.bot_username`
  - `start_polling()`：启动 `self._dp.start_polling(self._bot)`（阻塞协程，由 lifespan 用 `create_task` 包装）
  - `stop()`：调用 `self._dp.stop_polling()` 优雅停止
- Polling task 通过 `task.add_done_callback` 监听 fatal error 并 log
- 文档化约束：Telegram long polling 模式不支持多 worker 并行部署（单 worker 限制）

**2.5 Gateway lifespan 集成** — `src/gateway/app.py`（修改 lifespan）
```python
telegram_adapter = None
polling_task = None
if settings.telegram.bot_token:
    from src.channels.telegram import TelegramAdapter
    telegram_adapter = TelegramAdapter(...)
    await telegram_adapter.check_ready()  # fail-fast: validate token
    polling_task = asyncio.create_task(
        telegram_adapter.start_polling(),
        name="telegram_polling",
    )
    polling_task.add_done_callback(_on_polling_done)  # log fatal errors
    logger.info("telegram_adapter_ready", bot_username=telegram_adapter.bot_username)

yield

if telegram_adapter:
    await telegram_adapter.stop()
if polling_task and not polling_task.done():
    polling_task.cancel()
```

### 测试
- `tests/test_telegram_adapter.py`（新建）：
  - 身份映射通过 resolver（session_id = resolve_session_key 产出值）
  - 鉴权过滤：白名单内通过、白名单外拒绝、空白名单全拒
  - DM 触发 dispatch_chat（mock dispatch）
  - 非 DM（group）消息被忽略
  - readiness check 失败 → 异常
- `tests/test_app_integration.py`（更新）：bot_token 为空时不启动 adapter

### 完成标准
Telegram DM 消息 → agent 处理 → 返回响应。非授权用户被拒绝。空 bot_token 时不启动。invalid token 时 fail-fast 阻止启动。

---

## Phase 3：Response Rendering

**目标**：处理 Telegram 特有的输出约束（4096 字符消息限制、Markdown 格式）。

### 任务

**3.1 新建 `src/channels/telegram_render.py`**

- `split_message(text: str, max_length: int = 4096) -> list[str]`
  - 拆分优先级：段落 (`\n\n`) → 句子 (`. `) → 硬截断
  - 保护代码块 (`` ``` ``)：尽量不在代码块中间拆分

- `format_for_telegram(text: str) -> tuple[str, str | None]`
  - 尝试 MarkdownV2 转换（Telegram MarkdownV2 需要特殊转义）
  - 失败 → 回退纯文本（parse_mode=None）
  - 返回 `(formatted_text, parse_mode)`

**3.2 错误消息用户友好化**
- GatewayError 码 → 中文消息映射
  - `SESSION_BUSY` → "当前正在处理中，请稍后重试"
  - `BUDGET_EXCEEDED` → "预算额度已用完"
  - `PROVIDER_NOT_AVAILABLE` → "模型服务暂不可用"

**3.3 Tool call 指示**
- 不单独发送 tool call 消息（typing 指示器已覆盖"正在处理"语义）
- 保持极简

### 测试
- `tests/test_telegram_render.py`（新建）：拆分遵守长度限制、段落边界、代码块保护、MarkdownV2 转换、纯文本回退

### 完成标准
长响应正确拆分发送；Markdown 渲染或优雅降级；错误消息用户友好。

---

## Phase 4：端到端测试 + 验收

**目标**：验证跨渠道隔离、全量回归、验收标准、文档归档。

### 任务

**4.1 跨渠道隔离测试** — `tests/test_channel_isolation.py`（新建）
- Telegram DM scope_key = `telegram:peer:{id}`，WebChat scope_key = `main`
- Telegram 写入的记忆在 WebChat session 中不可召回（反之亦然）
- 同一 Telegram 用户总是命中同一 session
- 不同 Telegram 用户命中不同 session
- 两个渠道的 tool mode / risk gating / guardrail 行为一致
- **flush persist scope 一致性**：Telegram session 的 compaction flush 写入的 scope_key 与 recall scope_key 相同

**4.2 验收标准覆盖**
- **Use Case A**：Telegram 核心任务与 WebChat 体验一致（同样的工具可用、同样的记忆访问模式）
- **Use Case B**：渠道切换不改变能力边界和安全策略（mode filtering + risk gating 一致）
- **Use Case C**：跨渠道隔离行为与 dmScope 一致（memory recall scope 隔离 + flush scope 隔离）

**4.3 ADR 落地**
- `decisions/0044-telegram-adapter-aiogram-same-process.md`
  - 选了什么：aiogram 3.x + 同进程协同 + per-channel-peer scope
  - 为什么：轻量原生 async、单用户无需进程隔离、完整渠道隔离
  - 放弃了什么：python-telegram-bot（更 verbose）、独立进程（过度工程）、per-peer scope（隔离不足）
  - 约束：Telegram long polling 限单 worker 部署

**4.4 文档更新**
- `design_docs/m4_user_test_guide.md`（新建）：手工端到端测试步骤
- `design_docs/m4_architecture.md`：从 `planned` 更新为实现状态
- `design_docs/modules.md`：Channel Adapter 状态更新
- `decisions/INDEX.md`：新增 ADR 0044

**4.5 全量回归**
- `just test` + `just lint` 全绿

### 完成标准
全量测试绿；M4 验收标准 A/B/C 覆盖；ADR 归档；文档更新。

---

## 文件变更汇总

### 新建文件
| 文件 | Phase | 用途 |
|------|-------|------|
| `src/gateway/dispatch.py` | P1 | Channel-agnostic 调度核心 |
| `src/channels/telegram.py` | P2 | aiogram 适配器 |
| `src/channels/telegram_render.py` | P3 | 消息拆分 + 格式化 |
| `tests/test_dispatch.py` | P1 | 调度核心测试 |
| `tests/test_telegram_adapter.py` | P2 | 适配器测试 |
| `tests/test_telegram_render.py` | P3 | 渲染测试 |
| `tests/test_channel_isolation.py` | P4 | 跨渠道隔离测试 |
| `tests/test_agent_identity.py` | P1 | AgentLoop identity + flush scope 测试 |
| `decisions/0044-telegram-adapter-aiogram-same-process.md` | P4 | ADR |
| `design_docs/m4_user_test_guide.md` | P4 | 手工测试指导 |

### 修改文件
| 文件 | Phase | 变更 |
|------|-------|------|
| `src/config/settings.py` | P0 | +TelegramSettings（**不改** SessionSettings.dm_scope validator） |
| `src/session/scope_resolver.py` | P0 | +per-channel-peer / per-peer 分支; resolve_session_key DM 判据修正 |
| `src/agent/agent.py` | P1 | handle_message +identity +dm_scope; _persist_flush_candidates +scope_key; _run_compaction 传播 scope_key |
| `tests/test_agent_flush_persist.py` | P1 | 6 处旧签名调用 +scope_key; test_scope_key_from_candidate_session_id 重写; docstring 更新 |
| `src/gateway/app.py` | P1+P2 | dispatch 抽取 + Telegram lifespan（含 readiness check） |
| `pyproject.toml` | P2 | +aiogram 依赖 |
| `.env_template` | P0 | +Telegram 配置段 |
| `tests/test_settings.py` | P0 | +TelegramSettings 测试（SessionSettings 测试不变） |
| `tests/test_scope_resolver.py` | P0 | +新 scope 测试; 更新 test_non_main_raises → 测试未知 scope 报错 |
| `decisions/INDEX.md` | P4 | +ADR 0044 |
| `design_docs/m4_architecture.md` | P4 | planned → implemented |
| `design_docs/modules.md` | P4 | Channel Adapter 状态更新 |

### 无需变更（已验证）
| 文件 | 原因 |
|------|------|
| `src/gateway/protocol.py` | WebSocket 专属，Telegram 有自己的输出路径 |
| `src/tools/context.py` | 已 scope-aware (`ToolContext`) |
| `src/tools/registry.py` | 已 channel-agnostic |
| `src/memory/searcher.py` | 已接收 scope_key 参数 |
| `src/memory/writer.py` | 已通过 `ResolvedFlushCandidate` 传 scope_key |
| `src/gateway/budget_gate.py` | provider-agnostic，适用任何渠道 |

---

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| aiogram polling + FastAPI event loop 冲突 | aiogram 3.x 原生 asyncio，`create_task` 共享 loop |
| Telegram API rate limit | 单用户 bot 远低于限制（~30 msg/s） |
| bot_token 安全 | `.env` 已在 `.gitignore`，`.env_template` 为空值 |
| 并发请求消息序 | SessionManager claim/release 机制已覆盖 |
| polling 启动后静默失败 | readiness check fail-fast + done_callback 监听（Finding 4） |
| 多 worker 部署冲突 | 文档化单 worker 约束；webhook 模式为后续演进路径 |
| flush persist scope 与 recall scope 不一致 | _persist_flush_candidates 接收显式 scope_key（Finding 1） |
| 全局 dm_scope 误配导致 WebChat 崩溃 | SessionSettings.dm_scope 保持锁定 main（Finding 2） |
| Telegram session_id 与 resolver 漂移 | Telegram session_id 由 resolve_session_key 产出（Finding 3）；WebChat session_id 透传不走 resolver（Finding 6） |
| 旧测试 test_non_main_raises 需适配 | Phase 0 中更新为测试未知 scope 报错 |
| test_agent_flush_persist.py 旧签名 + 旧行为断言 | Phase 1.3a 中适配新签名并重写 scope 断言（Finding 7） |

---

## Review Round 1 修正记录

| # | 问题 | 严重性 | 修正 |
|---|------|--------|------|
| F1 | `_persist_flush_candidates` 用裸 identity 重算 scope，Telegram flush 记忆写入错误 scope | **HIGH** | Phase 1.3: 改为接收显式 scope_key 参数 |
| F2 | 全局 `SessionSettings.dm_scope` 放开后 WebChat 运行时炸 | **HIGH** | Phase 0.2: 保持锁定 main，Telegram 用独立 TelegramSettings.dm_scope |
| F3 | 手工 session_id 违反 ADR 0034 单一真源；`resolve_session_key` 路由判据不兼容 Telegram DM | **HIGH** | Phase 0.3: 改为 channel_id 判据; Phase 2.2: 通过 resolver 产出 session_id |
| F4 | polling 启动语义太松，invalid token / 网络失败变成静默故障 | **MEDIUM** | Phase 2.4: readiness check + done_callback + 单 worker 文档化 |
| F5 | 手工验证启动命令错误 | **LOW** | 修正为 `just dev` |

## Review Round 2 修正记录

| # | 问题 | 严重性 | 修正 |
|---|------|--------|------|
| F6 | WebSocket 路径走 `resolve_session_key` 会把客户端 session_id 折叠为 `"main"`，破坏 budget gate 透传断言、M6 eval_run_id 提取、多 session 并存语义 | **HIGH** | Phase 1.4: WebSocket 路径 session_id 透传（不走 resolver）; Context 章节补充 session_id / scope_key 分离设计; dispatch_chat 签名注释明确 session_id 为已确定的存储键 |

## Review Round 3 修正记录

| # | 问题 | 严重性 | 修正 |
|---|------|--------|------|
| F7 | `tests/test_agent_flush_persist.py` 6 处旧签名调用 + `test_scope_key_from_candidate_session_id` 断言旧"重算"行为，Phase 1.3 改签名后全量回归会先红 | **MEDIUM** | Phase 1.3a: 6 处调用增加 `scope_key=`; 重写 `test_scope_key_from_candidate_session_id` 为显式参数断言; 更新 docstring |

## Review Round 4 修正记录

| # | 问题 | 严重性 | 修正 |
|---|------|--------|------|
| F8 | TelegramAdapter 生命周期接口在 Phase 2.2/2.4/2.5 三处定义不一致（start/stop vs start+readiness vs check_ready+start_polling+stop） | **MEDIUM** | 统一为 `check_ready()` + `start_polling()` + `stop()` 三方法拆分；Phase 2.2 类签名、Phase 2.4 语义说明、Phase 2.5 lifespan 集成三处对齐 |

---

## 验证方式

### 自动化
```bash
just test        # 全量后端测试
just lint        # ruff 静态检查
```

### 手工验证（Phase 4 指南覆盖）
1. 配置 `.env`：设置 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_ALLOWED_USER_IDS`
2. 启动 gateway：`just dev`
3. 通过 Telegram 向 bot 发送消息，验证响应正确
4. 通过 WebChat 发送消息，验证 Telegram 记忆不泄漏
5. 用非白名单 Telegram 用户发送消息，验证被忽略
6. 发送触发长响应的消息，验证消息拆分正确
7. 配置无效 bot_token，验证启动 fail-fast
