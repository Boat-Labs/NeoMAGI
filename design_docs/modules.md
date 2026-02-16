# 最小化实现需要的模块

## 1. Gateway（控制平面）
核心是一个 WebSocket 服务器（默认 ws://127.0.0.1:19789），负责消息路由和调度。
最小化只需实现：消息接收 → Session 路由 → 分发给 Agent Runtime → 返回响应。
OpenClaw 用的是 RPC over WebSocket 协议。这里借鉴这个方案

## 2. Agent Runtime / Pi Agent
这是最核心的部分。openclaw里src/agents/agent-pi.ts 里的 PiEmbeddedRunner 负责整个 agent loop：

System Prompt 组装：从 workspace 读取 AGENTS.md、SOUL.md、TOOLS.md，注入 Skills，查询 Memory
Model Provider 调用：通过 pi-ai SDK 调用 LLM（Anthropic/OpenAI/本地模型）
Tool Call 拦截与执行：监听模型返回的 tool calls，执行后把结果流式返回模型
Model Resolver：管理多 provider failover，key 轮换和退避策略

Pi agent 原始repo链接
https://github.com/badlogic/pi-mono

## 3. Session 管理
Session 系统决定了对话隔离和上下文连续性：

每个 DM 合并到一个共享的 main session
每个 group chat 有独立 session
Session 数据存为 ~/.magi/agents/<agentId>/sessions/*.jsonl
包含 transcript 存储和 auto-compaction（context overflow 时自动压缩）

## 4. Memory 系统
这里的magi 参考 OpenClaw 的 Memory 分两层：

短期记忆：memory/YYYY-MM-DD.md 每日 append-only 日志，启动时加载今天+昨天
长期记忆搜索：Hybrid Search = Vector Search (70% 权重, cosine similarity, SQLite + sqlite-vec) + BM25 Keyword Search (30% 权重, SQLite FTS5)，用 union 而非 intersection 合并结果

最小化实现可以先用 BM25-only fallback（不需要 embedding），然后再加 vector search。

## 5. Tool Registry
参考 openclaw 需要在 src/tools/registry.ts 管理所有可用工具。最小化至少需要：

exec（执行 shell 命令）
read / write / edit（文件操作）
memory_search / memory_get（记忆检索）
Tool Policy Resolution（控制哪些工具允许/禁止）

## 6. Channel Adapter（聊天软件接口）

每个 Channel 把平台消息标准化为统一格式。最小化建议只实现一个，比如 Telegram（用 grammY 库）或者 WebChat。OpenClaw 支持 12+ 平台，但学习目的一个就够了。

## 7. Config 系统 
参考 openclaw 的 openclaw.json（JSON5 格式）+ Zod schema 验证 + 热加载。这是把整个系统串起来的粘合剂，也是 magi 设计哲学的关键 — 一切都是声明式配置。