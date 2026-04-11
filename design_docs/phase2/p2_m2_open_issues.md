---
doc_id: 019d7bb9-ba65-74fa-8fdd-5f5a12f8d6df
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-11T10:47:42+02:00
---
# P2-M2 Open Issues

> 说明：本文件记录 P2-M2 用户测试中发现的设计/架构层面 root cause，避免问题背景散落在聊天记录中。

## OI-M2-01 WorkerExecutor 未归一化 OpenAI SDK tool call 对象

- 发现于：B层 T08（首次 delegation 调用）
- 现象：`delegate_work` 调用后 worker 立即崩溃，`AttributeError: 'ChatCompletionMessageFunctionToolCall' object has no attribute 'get'`
- root cause：
  - 主 AgentLoop 的 streaming 路径通过 `_ToolCallAccumulator.collect()` 将 SDK Pydantic 对象归一化为 `dict[str, str]`
  - WorkerExecutor 使用非 streaming 的 `chat_completion()`，返回原始 `ChatCompletionMessage`，其 `tool_calls` 是 `ChatCompletionMessageToolCall` Pydantic 对象
  - `worker.py` 的 `_assistant_message()` 和 `execute()` 中 4 处用 `tc.get("id")` / `tc["name"]` 等 dict 风格访问，对 Pydantic 对象 AttributeError
- 影响：所有 worker delegation 在真实 LLM 调用下必然失败；unit test 因 mock 返回 dict 而未暴露
- 修复：`44296c3` — 新增 `_normalize_tool_calls()` 在 tool call 循环入口归一化
- 教训：
  - mock 使用手写 dict 替代 SDK 真实类型，编码了错误假设
  - streaming 与 non-streaming 两条路径的归一化责任不一致，是 boundary 缝隙的典型来源
  - 后续应在 `ModelClient.chat_completion()` 边界统一归一化，并要求 test fixture 使用 SDK 真实类型

## OI-M2-02 Publish merge_keys 对 model 不透明，导致空合并

- 发现于：B层 T09（publish_result 调用）
- 现象：`publish_result` 执行成功，state 从 `delegated` 转到 `done`（terminal），但 `flush_candidate_count=0`，**没有任何数据被实际合并到 visible context**
- root cause：
  - PublishTool 要求 model 提供 `merge_keys`（从 worker result 中选取哪些 key 提升到 visible context）
  - model 猜测了 `merge_keys: ["results", "summary", "open_questions"]`
  - 实际 worker result 的内部结构由 `_try_parse_json()` 解析 worker 的自然语言回复产生，key 不一定匹配 model 的猜测
  - `merge_worker_result()` 在 `source` 中找不到匹配 key → `visible_patch` 为空
  - state 仍然转换（publish 本身 `ok=True`），但合并结果为空
- 影响：
  - 用户视角：procedure 完成了，但 worker 的研究结果实际上丢失了
  - 本质是**信息不对称**：model 不知道 worker result 的实际 key 结构，只能盲猜 merge_keys
- 根源分析：
  - ProcedureView 只暴露 `allowed_actions` 和 `soft_policies`，不暴露 `_pending_handoffs` 的内容或结构
  - delegation 返回结果中只告知 `handoff_id` 和 `worker_ok`，不告知 worker result 的可用 key
  - model 需要从前轮 delegation 的上下文中"记住"worker 返回了什么，但如果发生 compaction，这些信息会丢失（与 test guide C层 T13 预测一致）
- 可选修复方向：
  - A. DelegationTool 返回中增加 `available_keys: list[str]`，告知 model worker result 有哪些 key
  - B. ProcedureView 中暴露 `_pending_handoffs` 的 key 列表（不暴露内容）
  - C. PublishTool 在 `merge_keys` 为空或全部未命中时，fallback 合并整个 `result` dict
  - D. Worker system prompt 强制要求 JSON 输出包含固定 key（如 `findings`, `summary`），并在 ProcedureSpec 的 soft_policies 中告知 model 这些 key

## OI-M2-03 ActionSpec 缺乏 noop/direct transition 支持

- 发现于：A层 T05 设计阶段（test spec 编写时暴露）
- 现象：`ActionSpec.tool` 是必填 `str`，没有 `None` 选项。"取消"或"跳过"语义的 action 必须绑定一个真实 tool
- root cause：
  - `ActionSpec` 设计为 `tool: str`（非 optional），每次 state transition 必须执行一个 tool
  - 如果绑定 `procedure_delegate` 做 cancel → 触发不必要的 worker LLM 调用
  - 如果绑定 `procedure_publish` 做 cancel → 因 `_pending_handoffs` 为空报错
  - A层 user test 不得不引入一个专用 `noop_echo` tool 规避
- 影响：
  - 真实 production spec 无法原生表达"直接转换状态，不执行任何 tool"的语义
  - 增加了 spec 定义的复杂度（每个需要 noop 的地方都要注册一个 dummy tool）
- 可选修复方向：
  - A. 允许 `ActionSpec(tool=None, to="cancelled")` 直接转换
  - B. 在 runtime 中内置 `procedure_noop` tool，所有 spec 共享
