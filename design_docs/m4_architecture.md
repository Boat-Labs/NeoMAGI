# M4 Architecture（已实现）

> 状态：implemented
> 对应里程碑：M4 第二渠道适配（Telegram）
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0003、ADR 0034、ADR 0044

## 1. 目标
- 在 WebChat 之外新增 Telegram 入口，保持核心能力和行为策略一致。

## 2. 基线（输入）
- WebChat 已打通 Gateway -> Agent -> Session -> Tool 的完整闭环。
- `channels` 包已实现 Telegram 适配器。

## 3. 实现架构

### 3.1 Telegram Adapter (`src/channels/telegram.py`)
- 使用 aiogram 3.x 原生 async long-polling 接收消息。
- 与 FastAPI 同进程运行，共享 registry/session_manager/budget_gate。
- 仅处理私聊文本消息，群组消息静默忽略。
- `allowed_user_ids` 白名单鉴权，空白名单 fail-closed。

### 3.2 Dispatch 复用 (`src/gateway/dispatch.py`)
- `dispatch_chat()` 从 WebSocket handler 提取为独立函数，Telegram 和 WebChat 共用。
- 渠道层仅负责协议转换（消息收发、身份映射），不复制核心业务逻辑。

### 3.3 Scope 隔离 (`src/session/scope_resolver.py`)
- Telegram DM 默认 `dm_scope="per-channel-peer"` → scope_key = `telegram:peer:{user_id}`
- WebChat 默认 `dm_scope="main"` → scope_key = `main`
- Session key 与 memory scope 同源，保证 flush 和 recall 使用相同 scope_key (ADR 0034)。

### 3.4 Response Rendering (`src/channels/telegram_render.py`)
- 消息分割：按 code block → 段落 → 句子 → 硬切，不超 4096 字符。
- MarkdownV2 格式化：检测 markdown 模式后转义。
- 错误映射：`GatewayError.code` → 用户友好中文提示。

### 3.5 配置 (`src/config/settings.py`)
- `TelegramSettings`: `bot_token`, `dm_scope`, `allowed_user_ids`, `message_max_length`。
- env prefix: `TELEGRAM_`，与 pydantic-settings 统一。

## 4. 边界
- In:
  - Telegram 单渠道打通（DM 文本消息）。
  - 与 WebChat 一致的核心行为边界（工具模式、风险级别、记忆作用域）。
- Out:
  - 不扩展到多平台并行适配。
  - 不引入渠道运营功能。
  - 不处理 Telegram 群组消息、媒体消息。

## 5. 验收对齐（已通过）
- Telegram 可独立完成核心任务流程 (Use Case A)。
- 渠道切换不改变核心能力边界与安全策略 (Use Case B)。
- 跨渠道隔离行为与 dmScope 一致 (Use Case C)。
- 跨渠道隔离测试覆盖：`tests/test_channel_isolation.py`。

## 6. 关键实现文件
- `src/channels/telegram.py` — Telegram adapter
- `src/channels/telegram_render.py` — response rendering
- `src/gateway/dispatch.py` — shared dispatch core
- `src/session/scope_resolver.py` — scope resolution
- `src/config/settings.py` — TelegramSettings
- `tests/test_channel_isolation.py` — cross-channel isolation tests
- `decisions/0044-telegram-adapter-aiogram-same-process.md` — architecture decision
