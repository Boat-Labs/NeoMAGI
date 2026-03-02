# ADR 0044: Telegram Adapter — aiogram 同进程 + per-channel-peer scope

**状态**: accepted
**日期**: 2026-03-02
**里程碑**: M4

## 背景

M4 需要在 WebChat 之外新增 Telegram 渠道，作为第二渠道验证多渠道架构。
需要选择 Telegram SDK、进程模型和 scope 隔离策略。

## 决策

### 选了什么

- **aiogram 3.x** 作为 Telegram Bot SDK
- **同进程协同** — Telegram long polling 与 FastAPI 在同一进程内运行
- **per-channel-peer scope** — `dm_scope="per-channel-peer"` 产生 `telegram:peer:{user_id}` 的 scope_key，实现渠道内按用户隔离

### 为什么

- **aiogram 3.x**: 原生 async/await，与项目技术栈一致；API 简洁，比 python-telegram-bot 更轻量
- **同进程**: 单用户场景无需进程隔离；减少部署复杂度；共享 registry/session_manager/budget_gate 实例，无需 IPC
- **per-channel-peer**: 完整的渠道隔离 — Telegram 用户 A 的会话/记忆与用户 B 隔离，也与 WebChat (`main` scope) 隔离；符合 ADR 0034 dmScope 设计

### 放弃了什么

- **python-telegram-bot**: API 更 verbose，decorator 模式较重，async 支持不如 aiogram 原生
- **独立进程部署**: 过度工程 — 需要 IPC 或消息队列同步状态，单用户场景无收益
- **per-peer scope** (`peer:{user_id}`): 跨渠道共享同一用户会话，隔离不足 — Telegram 和 WebChat 的使用场景不同，强制合并会话不符合实际需求

## 约束

- Telegram long polling 要求单 worker 部署（多 worker 会导致 update 竞争）
- 空白 `allowed_user_ids` 时 fail-closed（拒绝所有用户）
- 仅处理私聊文本消息，群组消息静默忽略

## 关联

- ADR 0034: dmScope session/memory scope alignment
- ADR 0003: channel baseline — WebChat first, Telegram second
