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

## Proposed State Model

- `activeSessionId: string`
- `sessionOrder: string[]`
- `sessionsById: Record<sessionId, SessionViewState>`
- `requestToSession: Record<requestId, sessionId>`

建议最小 `SessionViewState`：

- `sessionId`
- `messages`
- `isHistoryLoading`
- `isStreaming`
- `lastActivityAt`
- `lastAssistantPreview`
- `hasUnreadCompletion`
- `lastError`

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

## Data Flow

### New Thread

1. 生成新的 `session_id`
2. 初始化空的 `SessionViewState`
3. 插入 `sessionOrder`
4. 切换为新的 active thread

### Switch Thread

1. 更新 `activeSessionId`
2. 如果本地还没有 history，则触发该 session 的 `chat.history`
3. 不影响其他 thread 的 streaming

### Background Completion

1. 某个非 active thread 的 request 完成
2. 更新该 thread 的 `isStreaming = false`
3. 标记 `hasUnreadCompletion = true`
4. 左侧 cell 显示 done / unread completion 标记

## Suggested Implementation Slices

### Slice A. Store Refactor

- 把全局 `messages` 拆成 per-session
- 把全局 `isStreaming` / `isHistoryLoading` 拆成 per-session
- 引入 `requestToSession`

### Slice B. Rail UI

- 新增左侧 thread rail 组件
- 新增 `New Thread`
- 新增当前 thread 高亮与状态标记

### Slice C. Event Routing And Tests

- streaming / tool_call / error / response 事件按 session 路由
- 新增多 session store tests
- 新增后台完成信号测试

## Acceptance

- 可以从前端创建至少两个不同的 thread。
- 在 `thread-A` streaming 期间切到 `thread-B` 不会取消 `thread-A`。
- `thread-A` 后台完成后，左侧 rail 有可见完成信号。
- 两个 thread 的 history 与 tool calls 不互相污染。
- 当前 active thread 可以继续发送消息，不被其他 thread 的全局 streaming 锁死。

## Risks

### R1. 仅用前端本地列表无法覆盖其他设备或旧页面创建的 session

这是 V1 接受的边界。  
如果未来需要真正的全量 thread center，再补后端 list API。

### R2. 如果 rail 只加 UI，不重构 store，就会出现伪多 session

也就是列表能切，但后台 session 语义无法成立。  
本计划默认把 store refactor 视为必做项。

### R3. History loading guard 当前是全局闭包变量

`pendingHistoryId` 需要变成 per-session 或 per-request 跟踪，否则多 thread 时会互相踩。

## Clean Handoff Boundary

Claude Code 实现 `P1` 时，默认不要顺手做：

- tool concurrency
- coding tools
- server-side session list

`P1` 的任务目标很窄：  
把 multi-session threads 做成真实可用、可验收的前端闭环。
