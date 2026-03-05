# M1 Architecture（已完成总结）

> 状态：done  
> 截至日期：2026-02-19  
> 对应里程碑：M1.1 ~ M1.4

## 1. 范围
- 本文总结 M1 已落地的技术基线，不定义后续阶段实现细节。

## 2. 已完成架构能力

### 2.1 Gateway（WebSocket RPC）
- 基于 FastAPI + 原生 WebSocket 提供 `/ws` 接口。
- 已实现 `chat.send` 与 `chat.history` 两个核心 RPC。
- 错误通过统一 error frame 返回，支持 `PARSE_ERROR`、`METHOD_NOT_FOUND`、`SESSION_BUSY` 等语义。

实现参考：
- `src/gateway/app.py`
- `src/gateway/protocol.py`

### 2.2 Agent Runtime（Prompt + Model + Tool Loop）
- `AgentLoop` 支持多轮 tool call loop 与流式输出。
- `OpenAICompatModelClient` 基于 OpenAI SDK，支持 OpenAI-compatible 路径（OpenAI 默认，兼容 Gemini/Ollama 接入形式）。
- 流式路径支持 content delta 与 tool_calls delta 聚合。

实现参考：
- `src/agent/agent.py`
- `src/agent/model_client.py`
- `src/agent/prompt_builder.py`

### 2.3 Session（持久化与一致性）
- 会话持久化已统一到 PostgreSQL（不再使用内存降级作为默认路径）。
- 具备会话 claim/release、TTL 回收、fencing 保护与 DB 原子序号分配。
- `chat.history` 使用 display-safe 语义（只返回 user/assistant）。

实现参考：
- `src/session/manager.py`
- `src/session/models.py`
- `src/session/database.py`
- `alembic/versions/*.py`

### 2.4 Tool Registry（基础可扩展）
- 建立 `BaseTool` 抽象与 `ToolRegistry`。
- 当前内置工具：`current_time`、`read_file`、`memory_search`（占位实现）。
- `read_file` 已具备 workspace 边界安全校验。

实现参考：
- `src/tools/base.py`
- `src/tools/registry.py`
- `src/tools/builtins/*.py`

### 2.5 WebChat（主入口）
- 前端状态管理为 zustand。
- 实现流式消息展示、tool call 可视化、断线重连、history 加载守卫与超时兜底。

实现参考：
- `src/frontend/src/stores/chat.ts`
- `src/frontend/src/lib/websocket.ts`
- `src/frontend/src/components/chat/*.tsx`

### 2.6 测试与 CI 基线
- 后端测试：unit + integration 已建立并稳定通过。
- 前端测试：vitest 基线与关键 store 测试已建立。
- CI：GitHub Actions 已覆盖 migration + lint + test + frontend build/test。

实现参考：
- `tests/`
- `src/frontend/src/stores/__tests__/chat.test.ts`
- `.github/workflows/ci.yml`

## 3. M1 结束后的已知边界
- `memory_search` 仍为占位实现，尚未形成记忆检索闭环。
- `memory_append` 尚未实现，记忆写入缺少受控原子工具接口。
- 代码执行类工具（`write/edit/bash`）尚未纳入默认能力面。
- 会话内 compaction / pre-compaction flush 尚未实现。
- 第二渠道（Telegram）尚未实现。

## 4. 结论
- M1 已完成“能用且稳定”的系统基线，后续架构重点从“可运行”转向“可控执行与长期连续性”。
