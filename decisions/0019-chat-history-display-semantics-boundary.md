# 0019-chat-history-display-semantics-boundary

- Status: accepted
- Date: 2026-02-18

## 选了什么
- 明确 `chat.history` 的语义为“UI 展示历史”，不是“Agent 内部上下文历史”。
- `chat.history` 仅返回 display-safe 消息：`user` / `assistant`，且 `content` 非空；可包含 `timestamp`。
- Agent 运行所需的内部消息（如 `system`、`tool`、`assistant.tool_calls`）仅用于模型上下文，不通过 `chat.history` 对外返回。

## 为什么
- 避免将内部推理状态、工具调用细节暴露到前端 UI。
- 降低前端状态处理复杂度，避免空 assistant/tool 消息导致的渲染噪音和错误。
- 让协议边界稳定：`chat.history` 负责可读历史，Agent 内部格式可独立演进。
- 符合“极简闭环”目标：一条接口只做一件事，减少歧义和回归风险。

## 放弃了什么
- 方案 A：`chat.history` 返回 OpenAI 全量消息格式（含 `system/tool/tool_calls`）。
  - 放弃原因：会暴露内部状态，增加前端分支处理和安全风险。
- 方案 B：前端拿到全量历史后自行过滤。
  - 放弃原因：边界下沉到客户端，协议不收敛，易出现多端不一致。
- 方案 C：不定义明确语义，按实现演进。
  - 放弃原因：评审、测试和联调口径不一致，长期维护成本高。

## 影响
- Gateway 与 Session 层应保证 `chat.history` 返回 display-safe 格式。
- 前端 `history` 合并逻辑可基于 `user/assistant` 语义实现，不再承担内部消息清洗职责。
- 后续若需要导出内部上下文，应新增独立接口并明确权限与用途，不复用 `chat.history`。
