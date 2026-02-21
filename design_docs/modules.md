# 模块架构（当前实现 + 后续边界）

> 本文按“已实现 / 计划中”描述模块状态，作为 roadmap 的技术补充。  
> 产品目标与优先级请看 `design_docs/roadmap_milestones_v3.md`。

## 1. Gateway（控制平面）
- 状态：M1 已实现
- 现状：
  - FastAPI + WebSocket (`/ws`)。
  - RPC 方法：`chat.send`、`chat.history`。
  - 统一错误响应与会话并发串行化入口。

实现参考：
- `src/gateway/app.py`
- `src/gateway/protocol.py`

## 2. Agent Runtime
- 状态：M1 已实现（后续继续演进）
- 现状：
  - Prompt 组装（workspace context + tooling + datetime）。
  - Model 调用走 OpenAI SDK 统一接口（OpenAI-compatible）。
  - Tool loop 支持流式 content 与 tool_calls 聚合。
- 规划边界：
  - M2：增加长会话反漂移基线（压缩前后保持用户利益约束与角色边界）。
  - M3：增加自我进化治理控制流（提案 -> eval -> 生效 -> 回滚），不允许未评测变更直接生效。

实现参考：
- `src/agent/agent.py`
- `src/agent/prompt_builder.py`
- `src/agent/model_client.py`

## 3. Session
- 状态：M1 已实现（M2 继续扩展）
- 现状：
  - 会话持久化统一 PostgreSQL（非 SQLite）。
  - DM -> `main`，group -> `group:{channel_id}`。
  - 具备顺序语义、claim/release、TTL、fencing。

实现参考：
- `src/session/manager.py`
- `src/session/models.py`
- `decisions/0021-multi-worker-session-ordering-and-no-silent-drop.md`
- `decisions/0022-m1.3-soft-session-serialization-token-ttl.md`

## 4. Memory
- 状态：部分实现（M3 计划中）
- 现状：
  - `MEMORY.md` 在 main session 注入。
  - `memory_search` 已注册但仍是占位实现。
  - `memory_append` 尚未实现（当前缺少受控记忆写入原子）。
- 规划边界：
  - 记忆数据层对齐 PostgreSQL 16 + `pg_search` + `pgvector`。
  - 按阶段推进：先 BM25，再 Hybrid Search。
  - 引入记忆原子操作分工：`memory_search`（检索）+ `memory_append`（追加写入）。
  - 里程碑边界：M1.5 仅做 Memory 组授权框架预留，`memory_append` 实现归 M3。
  - 与进化治理边界：Memory 负责证据数据面，`SOUL.md` 进化控制流不在本模块直接实现。

实现与决议参考：
- `src/agent/prompt_builder.py`
- `src/tools/builtins/memory_search.py`
- `decisions/0006-use-postgresql-pgvector-instead-of-sqlite.md`
- `decisions/0014-paradedb-tokenization-icu-primary-jieba-fallback.md`

## 5. Tool Registry
- 状态：基础能力已实现（M1.5 计划中）
- 现状：
  - 具备工具注册、schema 生成与执行主链路。
  - 当前内置工具：`current_time`、`read_file`、`memory_search`（占位）。
- 规划边界：
  - 进入模式化授权框架（`chat_safe` 生效，`coding` 预留）。
  - 在可控边界下扩展 `read/write/edit/bash` 代码闭环能力。
  - 在模式层为 `memory_append` 预留授权接口；实际工具落地与记忆闭环归 M3。
  - M3 新增进化治理相关原子接口（提案/评测/生效/回滚），遵循“可验证、可回滚、可审计”。

实现参考：
- `src/tools/base.py`
- `src/tools/registry.py`
- `src/tools/builtins/*.py`
- `design_docs/m1_5_architecture.md`

## 6. Channel Adapter
- 状态：WebChat 已实现，Telegram 计划中（M4）
- 现状：
  - WebChat 已作为第一渠道打通。
  - `channels` 包尚无第二渠道实现。

实现参考：
- `src/frontend/`
- `src/channels/`
- `decisions/0003-channel-baseline-webchat-first-telegram-second.md`

## 7. Config
- 状态：M1 已实现（M6 继续扩展）
- 现状：
  - `pydantic-settings` + `.env` / `.env_template`。
  - DB schema、gateway、openai 配置已落地并做 fail-fast 校验。
- 规划边界：
  - 保持 OpenAI 默认路径，Gemini 在 M6 做迁移验证。

实现参考：
- `src/config/settings.py`
- `decisions/0013-backend-configuration-pydantic-settings.md`
- `decisions/0016-model-sdk-strategy-openai-sdk-unified-v1.md`
