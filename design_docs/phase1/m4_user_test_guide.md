# M4 用户测试流程（Telegram 第二渠道）

> 版本：M4 改进后实现
> 日期：2026-03-02
> 目标：指导用户从零启动系统，并按步骤验证 M4 的 Telegram 第二渠道、跨渠道隔离与 post-review 修复项。

---

## 1. 适用范围

本流程覆盖以下能力：
- Telegram 私聊文本消息可打通 `Gateway -> Agent -> Session -> Tool` 主链路。
- Telegram 与 WebChat 的核心能力边界保持一致。
- 跨渠道会话与记忆隔离符合 `dm_scope` 配置。
- WebSocket 不能伪造 Telegram / peer session（post-review F1）。
- Telegram 超长代码块可被拆分，不再因单行超长直接拒发（post-review F2）。
- Telegram 配置项 `TELEGRAM_MESSAGE_MAX_LENGTH` 具备基本边界校验（post-review F4）。

不在本流程范围内：
- 模型迁移与 provider 路由验证（M6）。
- 运营可靠性、恢复与观测建设（M5）。
- Telegram 群组消息、媒体消息、复杂运营功能。

自动化测试参考：
- `tests/test_channel_isolation.py`
- `tests/test_dispatch.py`
- `tests/test_telegram_render.py`
- `tests/test_settings.py`

---

## 2. 环境准备（一次性）

### 2.1 安装依赖

在仓库根目录执行：

```bash
uv sync --extra dev
just install-frontend
```

### 2.2 准备 `.env`

```bash
cp .env_template .env
```

至少确认以下字段：

```dotenv
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_USER=neomagi
DATABASE_PASSWORD=neomagi
DATABASE_NAME=neomagi
DATABASE_SCHEMA=neomagi

OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>

PROVIDER_ACTIVE=openai

TELEGRAM_BOT_TOKEN=<YOUR_TELEGRAM_BOT_TOKEN>
TELEGRAM_ALLOWED_USER_IDS=<YOUR_TELEGRAM_USER_ID>
TELEGRAM_DM_SCOPE=per-channel-peer
TELEGRAM_MESSAGE_MAX_LENGTH=4096
```

说明：
- `TELEGRAM_ALLOWED_USER_IDS` 支持逗号分隔多个用户 ID。
- 用户 ID 可通过 `@userinfobot` 获取。
- 建议保持 `TELEGRAM_MESSAGE_MAX_LENGTH=4096`，不要手动压到极小值做线上验证。

### 2.3 启动 PostgreSQL 17（示例：podman）

```bash
podman run --name neomagi-pg \
  -e POSTGRES_USER=neomagi \
  -e POSTGRES_PASSWORD=neomagi \
  -e POSTGRES_DB=neomagi \
  -p 5432:5432 \
  -d postgres:17
```

如果容器已存在：

```bash
podman start neomagi-pg
```

### 2.4 执行数据库 migration

```bash
uv run alembic upgrade head
```

### 2.5 初始化 workspace

```bash
just init-workspace
```

---

## 3. 启动系统（每次测试）

开 2 个终端窗口：

终端 A（后端 Gateway + Telegram polling）：

```bash
just dev
```

终端 B（前端 WebChat）：

```bash
just dev-frontend
```

健康检查：

```bash
curl http://localhost:19789/health
```

应返回：

```json
{"status":"ok"}
```

浏览器打开 `http://localhost:5173`，确认 WebChat 已连接。

启动日志中应看到：
- `telegram_bot_ready`
- `telegram_polling_started`
- `gateway_started`

---

## 4. 快速手工测试（用户视角）

说明：
- 示例输入不要求逐字一致。
- 预期关注“行为是否发生”，不是模型文案逐字匹配。

### T01 Telegram 基础问答可用

- 前置：Telegram Bot 已启动，当前账号在白名单中。
- Telegram 示例输入：
  - `你好，请用一句话介绍你自己。`
- 预期：
  - Bot 正常回复。
  - 没有 `当前正在处理中，请稍后重试`、`模型服务暂不可用` 等错误提示。

### T02 Telegram 工具调用链路可用

- Telegram 示例输入：
  - `现在几点？`
- 预期：
  - Bot 返回当前时间相关回答。
  - Gateway 日志中可看到一次正常的 agent run。

### T03 Telegram 与 WebChat 能力边界一致

- 操作：
  1. 保持 Telegram 会话。
  2. 浏览器打开 WebChat。
- Telegram 示例输入：
  - `帮我总结一下你能做什么。`
- WebChat 示例输入：
  - `帮我总结一下你能做什么。`
- 预期：
  - 两个渠道都能正常完成请求。
  - 不会出现 Telegram 可用但 WebChat 不可用，或反过来的明显能力漂移。

### T04 跨渠道记忆隔离（Use Case C）

- Telegram 中发送：
  - `请记住我喜欢蓝色。`
- 然后在 WebChat 中发送：
  - `我喜欢什么颜色？`
- 预期：
  - WebChat 不应稳定召回 Telegram 私聊里的该条记忆。

反向测试：
- WebChat 中发送：
  - `请记住我喜欢绿色。`
- 然后在 Telegram 中发送：
  - `我喜欢什么颜色？`
- 预期：
  - Telegram 不应稳定召回 WebChat 的该条记忆。

### T05 同一 Telegram 用户会话连续性

- 在同一个 Telegram 私聊中连续发送两条相关消息：
  1. `我叫小周。`
  2. `我刚才告诉过你我叫什么？`
- 预期：
  - 第二条能基于当前 Telegram 会话回答“小周”或等价答案。
  - 说明 Telegram 自己的 session continuity 正常。

---

## 5. Post-Review 修复项验证

### T06 WebSocket 不允许伪造 Telegram / peer session（F1）

执行以下一次性脚本：

```bash
uv run python - <<'PY'
import asyncio
import json
import uuid
import websockets

WS = "ws://localhost:19789/ws"

async def send(ws, method, params):
    rid = str(uuid.uuid4())
    req = {
        "type": "request",
        "id": rid,
        "method": method,
        "params": params,
    }
    await ws.send(json.dumps(req, ensure_ascii=False))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") != rid:
            continue
        print(method, params["session_id"], msg)
        return

async def main():
    async with websockets.connect(WS) as ws:
        await send(ws, "chat.send", {"session_id": "telegram:peer:123", "content": "hi"})
        await send(ws, "chat.send", {"session_id": "peer:123", "content": "hi"})
        await send(ws, "chat.history", {"session_id": "telegram:peer:123"})
        await send(ws, "chat.history", {"session_id": "peer:123"})

asyncio.run(main())
PY
```

预期：
- 四次请求都返回 `type="error"`。
- `error.code` 为 `INVALID_PARAMS`。
- 错误消息中包含 `channel-exclusive prefix`。

### T07 超长代码块拆分验证（F2）

执行以下本地脚本：

```bash
uv run python - <<'PY'
from src.channels.telegram_render import split_message

text = "```\\n" + ("A" * 5000) + "\\n```"
parts = split_message(text, max_length=4096)

print("chunks:", len(parts))
print("max_len:", max(len(p) for p in parts))
print("all_valid:", all(len(p) <= 4096 for p in parts))
PY
```

预期：
- `chunks` 大于 `1`
- `max_len` 小于等于 `4096`
- `all_valid` 为 `True`

### T08 `TELEGRAM_MESSAGE_MAX_LENGTH` 边界校验（F4）

执行以下本地脚本：

```bash
uv run python - <<'PY'
from pydantic import ValidationError
from src.config.settings import TelegramSettings

for value in [0, -1, 5000, 2048]:
    try:
        s = TelegramSettings(message_max_length=value)
        print(value, "OK", s.message_max_length)
    except ValidationError:
        print(value, "INVALID")
PY
```

预期：
- `0 INVALID`
- `-1 INVALID`
- `5000 INVALID`
- `2048 OK 2048`

---

## 6. 错误场景与边界验证

### T09 空白白名单 fail-closed

- 修改 `.env`：

```dotenv
TELEGRAM_ALLOWED_USER_IDS=
```

- 重启 Gateway。
- 用原 Telegram 账号向 Bot 发送任意消息。
- 预期：
  - Bot 不响应。
  - 日志中可看到 `telegram_user_denied`。

### T10 非白名单用户被拒绝

- 将当前账号移出 `TELEGRAM_ALLOWED_USER_IDS`，或使用另一 Telegram 账号。
- 发送任意消息。
- 预期：
  - Bot 不响应。
  - 不会触发正常 agent run。

### T11 群组消息静默忽略

- 把 Bot 拉进一个 Telegram 群组。
- 在群里直接发送消息或 `@Bot`。
- 预期：
  - Bot 不响应。
  - 群消息不会进入正常私聊处理链路。

### T12 错误 token 启动 fail-fast

- 将 `.env` 中的 `TELEGRAM_BOT_TOKEN` 改成无效值。
- 重启 Gateway：

```bash
just dev
```

- 预期：
  - 启动失败。
  - 日志包含 Telegram 认证失败信息，不会进入“表面健康但 Telegram 已死”的状态。

---

## 7. 验收结论模板

完成后可按以下口径记录结果：

- Use Case A：Telegram 核心任务流程 `PASS / FAIL`
- Use Case B：Telegram 与 WebChat 一致性 `PASS / FAIL`
- Use Case C：跨渠道隔离 `PASS / FAIL`
- F1 WebSocket session guard `PASS / FAIL`
- F2 长代码块拆分 `PASS / FAIL`
- F4 message_max_length 校验 `PASS / FAIL`

如失败，至少记录：
- 输入步骤
- 实际现象
- 预期行为
- 相关日志片段

---

## 8. 常用日志关注点

启动阶段：
- `telegram_bot_ready`
- `telegram_polling_started`
- `gateway_started`

正常处理：
- `agent_run_provider_bound`

异常路径：
- `telegram_user_denied`
- `telegram_dispatch_error`
- `telegram_polling_fatal`
