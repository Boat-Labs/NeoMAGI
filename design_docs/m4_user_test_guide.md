# M4 手工端到端测试指南

> 本指南用于在本地环境手工验证 Telegram 渠道的端到端功能。
> 自动化测试覆盖见 `tests/test_channel_isolation.py`。

## 前置条件

1. PostgreSQL 16 实例运行中
2. `.env` 中配置好数据库连接
3. 通过 @BotFather 创建 Telegram Bot，获取 bot token
4. 获取你的 Telegram user ID（可通过 @userinfobot 查看）

## 环境配置

在 `.env` 中添加：

```
TELEGRAM_BOT_TOKEN=<your-bot-token>
TELEGRAM_ALLOWED_USER_IDS=<your-user-id>
TELEGRAM_DM_SCOPE=per-channel-peer
```

## 测试步骤

### Use Case A: Telegram 核心任务

1. 启动服务：`just dev`
2. 在 Telegram 中向 Bot 发送消息：`你好`
3. 验证 Bot 回复了有意义的响应（非错误）
4. 发送包含工具触发的消息（如 `现在几点`）
5. 验证工具调用正常执行并返回结果

### Use Case B: 渠道一致性

1. 保持 Telegram 会话
2. 在浏览器打开 WebChat（`http://localhost:19789`）
3. 在 WebChat 中发送相同的消息
4. 验证两个渠道的功能行为一致（工具可用性、响应质量）

### Use Case C: 跨渠道隔离

1. 在 Telegram 中发送：`请记住我喜欢蓝色`
2. 在 WebChat 中询问：`我喜欢什么颜色？`
3. 验证 WebChat 无法召回 Telegram 会话中的记忆
4. 反向测试：在 WebChat 中记忆信息，验证 Telegram 无法召回

### 错误场景

1. 配置空白 `TELEGRAM_ALLOWED_USER_IDS`，验证所有用户被拒绝
2. 使用非白名单用户发消息，验证 Bot 不响应
3. 在群组中 @Bot，验证群组消息被忽略

## 日志查看

启动日志中应看到：
- `telegram_bot_ready` — Bot token 验证成功
- `telegram_polling_started` — long polling 开始

消息处理日志：
- `agent_run_provider_bound` — provider 路由
- `telegram_dispatch_error` — 错误（如有）
