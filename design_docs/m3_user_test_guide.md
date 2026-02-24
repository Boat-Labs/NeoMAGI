# M3 用户测试指导（WebChat）

> 版本：M3 完成态（含 post-review 修正）  
> 日期：2026-02-24  
> 目标：指导用户从零启动系统，并按步骤验证 M1.5 + M2 + M3 的可用功能。

## 1. 适用范围

本指导覆盖以下能力：
- WebChat 单渠道使用（`session_id=main`）
- `chat_safe` 模式下的工具调用与拒绝行为
- 会话内连续性（历史恢复、长对话可继续）
- 会话外持久记忆（写入、检索、召回）
- SOUL 进化最小闭环（status / propose / rollback）

不在本指导范围内：
- `coding` 模式（M3 阶段未开放）
- Telegram 第二渠道（M4）
- 多 provider 模型迁移对比（M6）

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

至少配置以下字段（示例）：

```dotenv
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_USER=neomagi
DATABASE_PASSWORD=neomagi
DATABASE_NAME=neomagi
DATABASE_SCHEMA=neomagi

OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
```

### 2.3 启动 PostgreSQL 16（示例：podman）

```bash
podman run --name neomagi-pg \
  -e POSTGRES_USER=neomagi \
  -e POSTGRES_PASSWORD=neomagi \
  -e POSTGRES_DB=neomagi \
  -p 5432:5432 \
  -d postgres:16
```

如果容器已存在：

```bash
podman start neomagi-pg
```

### 2.4 初始化 workspace

```bash
just init-workspace
```

## 3. 启动系统（每次测试）

开 2 个终端窗口：

终端 A（后端网关）：

```bash
just dev
```

终端 B（前端）：

```bash
just dev-frontend
```

浏览器打开 `http://localhost:5173`，确认顶部状态为已连接（Connected）。

## 4. 功能测试用例（截至 M3）

说明：
- 示例输入是建议文案，不要求逐字一致。
- 预期答案关注“要点”，不要求模型输出完全一致。

### T01 基础对话与流式输出

- 示例输入：
  - `你好，请用一句话介绍你自己。`
- 预期：
  - 能收到 assistant 回复。
  - 回复是流式逐步出现，最终完成。

### T02 `chat_safe` 模式拒绝代码工具

- 示例输入：
  - `请读取 workspace/AGENTS.md 的完整内容。`
- 预期：
  - assistant 不直接返回文件内容。
  - 说明当前为 `chat_safe`，代码/文件系统工具不可用。
  - 若模型触发了工具调用，消息中可看到 `read_file denied` 提示。

### T03 `current_time` 工具可用

- 示例输入：
  - `请调用 current_time，查询 Asia/Shanghai 当前时间，并给我 ISO 时间。`
- 预期：
  - 返回时间、时区（`Asia/Shanghai`）和 ISO 字段信息。
  - 工具调用显示为成功完成（非 denied）。

### T04 `memory_append` 写入每日记忆

- 示例输入：
  - `请把这条偏好写入记忆：我希望你先给结论，再给 3 条要点。`
- 预期：
  - assistant 明确表示写入成功（通常会提到保存到当天记忆文件）。
  - 工具调用显示 `memory_append` 完成。
- 可选终端验证：

```bash
tail -n 40 workspace/memory/$(date +%F).md
```

应看到新增条目，包含写入文本与 `scope: main` 元数据。

### T05 `memory_search` 检索记忆

- 示例输入：
  - `请用 memory_search 搜索“先给结论”，告诉我检索结果。`
- 预期：
  - assistant 返回至少一条相关命中（包含你在 T04 写入的偏好）。
  - 工具调用显示 `memory_search` 完成。

### T06 记忆召回（不重复输入偏好）

- 前置：已完成 T04。
- 示例输入：
  - `以后你应该怎么组织回答结构？`
- 预期：
  - assistant 能主动提到“先给结论，再给要点”这一偏好。
  - 不需要你再次完整重复原偏好文本。

### T07 会话历史恢复（刷新页面）

- 操作：
  1. 先完成至少 2 轮问答。
  2. 浏览器刷新页面。
- 预期：
  - 历史消息会自动回放出来（不是空白新会话）。
  - 可继续在原 `main` 会话上聊天。

### T08 `soul_status` 查看当前版本

- 示例输入：
  - `请调用 soul_status，包含最近 3 条历史。`
- 预期：
  - 返回当前 active 版本信息（版本号、状态等）。
  - 首次运行通常可看到 bootstrap 导入的基础版本（如 v0）。

### T09 `soul_propose` 提案并尝试生效

- 示例输入：
  - `请调用 soul_propose，把 SOUL.md 更新为更偏“简洁、结构化中文优先”的风格。`
- 预期：
  - 返回 `applied` 或 `rejected` 状态。
  - 若 `applied`：会给出版本号，`soul_status` 可看到 active 版本变化。
  - 若 `rejected`：会给出 eval 失败摘要（例如内容不合规/与当前版本重复等）。

### T10 `soul_rollback` 回滚

- 前置：T09 出现可回滚版本。
- 示例输入：
  - `请调用 soul_rollback，执行 rollback。`
- 预期：
  - 返回 `rolled_back`，并给出新的 active 版本号。
  - 之后再调用 `soul_status`，应看到版本链变化。

### T11 重启后持久性验证

- 操作：
  1. 停止后端（终端 A `Ctrl+C`）。
  2. 再次执行 `just dev` 启动。
  3. 页面刷新后继续对话。
- 示例输入：
  - `请搜索我之前记录的回答偏好。`
- 预期：
  - 仍可检索到重启前写入的记忆内容（DB + workspace 持久化生效）。

## 5. 预期产物检查点

完成用例后，可检查以下产物：

- `workspace/memory/YYYY-MM-DD.md`
  - 应包含 `memory_append` 写入条目。
- `workspace/SOUL.md`
  - 在 `soul_propose` / `soul_rollback` 后可能发生更新。
- `neomagi.memory_entries` / `neomagi.soul_versions`（数据库）
  - 检索索引与版本审计链应存在对应记录。

## 6. 常见问题与处理

### 6.1 页面一直显示连接中

- 检查后端是否已启动：`just dev`
- 检查端口是否被占用：`19789`（后端）和 `5173`（前端）

### 6.2 启动即报数据库错误

- 确认 PostgreSQL 容器在运行：`podman ps`
- 确认 `.env` 的 DB 配置与容器一致

### 6.3 工具没有按预期触发

- 在输入中明确写“请调用 `<tool_name>`”
- 先确认该工具在 M3 可用（`read_file` 在 `chat_safe` 中会被拒绝）

### 6.4 高风险工具被 guard 阻断

- 确认 `workspace/AGENTS.md`、`workspace/USER.md`、`workspace/SOUL.md` 存在且非空
- 可重新执行：`just init-workspace`

## 7. 退出与清理

- 停止前后端：对应终端 `Ctrl+C`
- 可选停止数据库容器：

```bash
podman stop neomagi-pg
```
