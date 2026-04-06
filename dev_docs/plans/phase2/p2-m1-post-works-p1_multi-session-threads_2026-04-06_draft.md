# P2-M1 Post Works P1：Multi-Session Threads（草案）

- Date: 2026-04-06
- Status: draft
- Scope: 为 WebChat 增加 Codex 风格的左侧 `threads` rail，使用户可以创建、切换多个 session，并允许非当前激活 session 在后台继续运行
- Basis:
  - [`src/frontend/src/stores/chat.ts`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/frontend/src/stores/chat.ts)
  - [`src/frontend/src/components/chat/ChatPage.tsx`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/frontend/src/components/chat/ChatPage.tsx)
  - [`src/frontend/src/components/chat/MessageInput.tsx`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/frontend/src/components/chat/MessageInput.tsx)
  - [`src/gateway/dispatch.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/gateway/dispatch.py)
  - [`src/session/manager.py`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/src/session/manager.py)

## Goal

交付一个最小但真实可用的 multi-session UX：

- 左侧显示 threads 列表
- 可以 `New Thread`
- 可以切换 active thread
- 切换时不取消其他 thread 的后台运行
- 后台完成后有清晰信号

## Current Baseline

- 前端当前把 `chat.history` 与 `chat.send` 的 `session_id` 都写死为 `"main"`。
- 前端当前的 `messages`、`isStreaming`、`isHistoryLoading` 是全局状态。
- 前端当前的 `pendingHistoryId` 也是全局闭包变量，同时承担 history response 匹配与发送前 guard。
- 后端协议已经支持任意 `session_id`。
- 后端 dispatch 是按 `session_id` claim / release，因此“同一 session 串行、不同 session 可并发”在语义上已成立。
- 当前没有后端 `session list` API。

## Product Direction

V1 直接采用左侧 `threads` rail，而不是只做顶部下拉切换器。

### In Scope

- 左侧 rail
- `New Thread`
- thread 列表
- 当前 thread 高亮
- running / done / unread completion 指示
- 最近活动排序
- `sessionOrder + activeSessionId + 轻量 title` 的 `localStorage` 持久化

### Out Of Scope

- 文件夹
- plugins / automations / settings 侧栏入口
- server-synced 全量 session center
- 自动标题生成
- 刷新页面后恢复 streaming 中间态

## Core Decision

必须显式区分：

- `active thread`
- `running request`

前者表示当前用户正在看的 thread，后者表示某个 `session_id` 下有请求仍在 streaming。  
如果继续沿用全局 `isStreaming` / `isHistoryLoading`，就无法支持“切走后后台继续跑”。

同时，为了兼容现有 WebChat 历史：

- 首个 thread 固定使用 `session_id = "main"`
- 后续新建 thread 使用 `web:{uuid}` 格式

这样用户进入新 UI 后，仍能看到原本积累在 `main` 下的历史。

## Proposed State Model

- `activeSessionId: string`
- `sessionOrder: string[]`
- `sessionsById: Record<sessionId, SessionViewState>`
- `requestToSession: Record<requestId, sessionId>`

建议最小 `SessionViewState`：

- `sessionId`
- `messages`
- `pendingHistoryId`
- `isHistoryLoading`
- `isStreaming`
- `lastActivityAt`
- `title`
- `lastAssistantPreview`
- `hasUnreadCompletion`
- `lastError`

关键约束：

- `pendingHistoryId` 必须 per-session 化
- `sendMessage` 只检查 active thread 的 pending history guard
- `requestToSession` 必须在 request terminal state 时清理，不能无限增长

## UI Layout

### Left Rail

- 顶部固定 `New Thread`
- 下方是 threads 列表
- 每个 thread cell 至少显示：
  - title placeholder 或首条摘要
  - 最近活动时间
  - running indicator 或 completion dot

### Main Pane

- 保持当前聊天主视图结构
- 当前 active thread 的消息显示在主 pane
- 发送框只作用于当前 active thread

## Runtime Semantics

- 单 WebSocket 连接即可，不需要为每个 thread 单独建连接。
- 每个 thread 最多允许一个 active request。
- 不同 thread 可以同时存在 active request。
- streaming 事件继续按 `request_id` 关联，前端通过 `requestToSession` 做路由。
- `requestToSession` 的 cleanup 时机固定为：
  - `stream_chunk.done = true`
  - request 级 `error`
- connection 级断线 / 重连不会恢复 streaming 中间态，但会保留 thread 列表与 active 选择。

## Data Flow

### New Thread

1. 若当前还没有任何 thread，使用 `session_id = "main"`
2. 否则生成 `session_id = "web:{uuid}"`
3. 初始化空的 `SessionViewState`
4. 插入 `sessionOrder`
5. 切换为新的 active thread

### App Bootstrap / Refresh

1. 从 `localStorage` 恢复 `sessionOrder`、`activeSessionId` 与轻量 title
2. 若无持久化数据，则创建默认 thread：`main`
3. `onConnected` 后只 lazy load 当前 active thread 的 history
4. 非 active thread 保持未加载，直到用户切换过去

### Switch Thread

1. 更新 `activeSessionId`
2. 如果该 thread 的 history 尚未加载，则触发该 session 的 `chat.history`
3. 不影响其他 thread 的 streaming

### History Load

1. 为目标 thread 设置 `pendingHistoryId`
2. 发送该 session 的 `chat.history`
3. 只有匹配该 thread `pendingHistoryId` 的 `response/error` 才能清除此 guard

### Send Message

1. 只检查 active thread 的 `pendingHistoryId`
2. 为当前 request 建立 `requestToSession[requestId] = activeSessionId`
3. 只把当前 active thread 的 `isStreaming` 置为 `true`

### Background Completion

1. 某个非 active thread 的 request 完成
2. 更新该 thread 的 `isStreaming = false`
3. 清理该 request 的 `requestToSession`
4. 标记 `hasUnreadCompletion = true`
5. 左侧 cell 显示 done / unread completion 标记

### Request Error

1. 依据 `requestToSession` 找到所属 thread
2. 只更新该 thread 的 `isStreaming` / `lastError`
3. 清理该 request 的 `requestToSession`
4. 若该错误对应 session history load，则只清该 thread 的 `pendingHistoryId`

### Thread Title

V1 不做 LLM 标题生成。
建议直接采用：

- 首条 user message 前 30 个字符
- 若还没有 user message，则使用固定 placeholder

## Suggested Implementation Slices

### Slice A. Store Refactor

- 把全局 `messages` 拆成 per-session
- 把全局 `isStreaming` / `isHistoryLoading` / `pendingHistoryId` 拆成 per-session
- 引入 `activeSessionId`、`sessionOrder`、`sessionsById`、`requestToSession`
- 明确默认 thread 为 `main`，新增 thread 为 `web:{uuid}`
- 增加 `localStorage` persistence for `sessionOrder + activeSessionId + title`

### Slice B. Rail UI

- 新增左侧 thread rail 组件
- 新增 `New Thread`
- 新增当前 thread 高亮与状态标记
- 用首条 user message 前 30 个字符作为 title

### Slice C. Event Routing And Tests

- 重写 `_handleServerMessage` 的分支路由，至少覆盖：
  - `stream_chunk`
  - `stream_chunk.done`
  - `error`
  - `tool_call`
  - `tool_denied`
  - `response`
    - 注意：`response`（history 返回）不通过 `requestToSession` 路由，而是通过各 thread 的 `pendingHistoryId` 匹配
- 重写 `_setConnectionStatus`，明确连接级故障下的 pending history 清理语义
  - 连接级断线 / 重连是全局事件，应清理所有 thread 的 `pendingHistoryId`，而不是只清 active thread
- 定义 `requestToSession` cleanup 规则
- 新增多 session store tests
- 新增后台完成信号测试
- 新增刷新后恢复 thread 列表但不恢复 streaming 中间态的测试
- 显式覆盖 tool calls 不跨 thread 污染

### Slice Dependency

- `Slice A` 先行
- `Slice B` 与 `Slice C` 可并行

## Acceptance

- 可以从前端创建至少两个不同的 thread。
- 在 `thread-A` streaming 期间切到 `thread-B` 不会取消 `thread-A`。
- `thread-A` 后台完成后，左侧 rail 有可见完成信号。
- 两个 thread 的 history 与 tool calls 不互相污染。
- 当前 active thread 可以继续发送消息，不被其他 thread 的全局 streaming 锁死。
- 首次进入新 UI 时仍能看到 `main` 下既有历史。
- 页面刷新后，thread 列表与 active 选择会恢复；但不要求恢复 streaming 中间态。

## Risks

### R1. 仅用前端本地列表无法覆盖其他设备或旧页面创建的 session

这是 V1 接受的边界。  
如果未来需要真正的全量 thread center，再补后端 list API。

### R2. 如果 rail 只加 UI，不重构 store，就会出现伪多 session

也就是列表能切，但后台 session 语义无法成立。  
本计划默认把 store refactor 视为必做项。

### R3. History loading guard 当前是全局闭包变量

`pendingHistoryId` 必须变成 per-session 字段；否则 history response 匹配与发送 guard 会互相踩。

### R4. 若不定义 request terminal cleanup，`requestToSession` 会持续增长

因此必须明确在 `stream_chunk.done` 与 request 级 `error` 时删除映射。

### R5. 若不保留 `main` 作为默认 thread，用户旧历史会在新 UI 中“消失”

这不是数据丢失，而是 session 入口口径变化导致的兼容性问题。
因此默认 thread 必须继续指向 `main`。

## Clean Handoff Boundary

Claude Code 实现 `P1` 时，默认不要顺手做：

- tool concurrency
- coding tools
- server-side session list

`P1` 的任务目标很窄：  
把 multi-session threads 做成真实可用、可验收的前端闭环。
