---
doc_id: 019da594-dd6e-7c30-b233-87c0f8568c29
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-19T13:51:29+02:00
---
# 0065-run-level-provider-model-selection

- Status: accepted
- Date: 2026-04-19
- Refines: ADR 0040
- Related: ADR 0016, `design_docs/phase3/p3_daily_use_architecture_draft.md`

## 背景

ADR 0040 已定义 provider 选择粒度为 agent-run：每次 `chat.send` 开始时绑定 provider / model，本次 run 内保持不变，下一次 `chat.send` 可重新选择。

当前前端尚未暴露 provider / model 选择，主要依赖 `.env` 设置默认 provider 和默认 model。这与 NeoMAGI 的长期理念不完全匹配：session、memory、artifact 与数据库是持续载体，API / model 是每次 run 的计算资源。P3 daily-use 需要让用户能按任务选择计算资源，同时保持选择面受控、可审计、可复现。

## 选了什么

- 前端应支持 provider / model 选择。
- provider / model 选择仍是 agent-run 级绑定：
  - 每次 `chat.send` 开始时确定；
  - 本次 run 内不切换；
  - 同一 session 的相邻 runs 可以使用不同 provider / model。
- Session、memory、artifact 不绑定 provider 或 model。
- 前端不能自由输入任意 model string。
- 后端通过 `.env` / server config 定义 enabled providers、model profiles、默认 profile 和 capability。
- 前端只展示后端返回的 enabled model profiles。
- `chat.send` 可携带 `provider` 与 `model_profile`；未携带时使用后端默认 profile。
- 每次 run 必须记录 provider、model profile、实际 model，用于成本、debug、复现与 daily casebook。
- Claude provider 使用 native Anthropic SDK；NeoMAGI 在 `ModelClient` provider adapter 层保持统一接口，不通过 OpenAI-compatible proxy 强行抹平 provider 差异。
- Gateway / ProviderRegistry 只负责 run-level 绑定、profile/capability 暴露、日志与预算，不做 Anthropic / OpenAI message 格式转换。
- AgentLoop 只依赖 `ModelClient`，不感知底层 SDK。
- Anthropic Messages API 的 tool call 形态不同于 OpenAI：Anthropic 使用 `tool_use` content block 与 `tool_result`，OpenAI 使用 `tool_calls` / tool role message；具体 mapping 是 `AnthropicModelClient` adapter 的职责，留给 P3-M1 implementation plan。
- Provider / model capability 至少应能表达：
  - tools support；
  - image support；
  - streaming support；
  - max context（可未知）；
  - provider / model label。

## 为什么

- 用户 daily use 需要按任务选择成本、能力、速度和供应商。
- NeoMAGI 的连续性在 DB / session / memory，而不是某个 API provider。
- 受控 model profile 比前端自由输入 model id 更安全，也更利于 capability 校验和预算审计。
- Agent-run 级选择延续 ADR 0040，避免 hot switch 带来的上下文一致性、tool state、预算和复现问题。
- Provider capability matrix 可以让前端和后端在不支持 tools / images 的模型上显式降级，而不是静默失败。
- Native Claude adapter 能保留 Anthropic Messages API 的真实语义，避免 OpenAI-compatible 兼容层在 tools、system prompt、streaming、image 和 usage 上隐藏差异。

## 放弃了什么

- 方案 A：继续只通过 `.env` 设置 provider / model。
  - 放弃原因：无法支持用户按任务选择计算资源，也不符合 P3 daily-use 的交互需求。
- 方案 B：让前端自由输入任意 model string。
  - 放弃原因：绕过 capability、预算、健康检查和配置审计。
- 方案 C：把 provider / model 作为 session 永久属性。
  - 放弃原因：session / memory 应该跨 provider 持续；模型只是 run 级计算步骤。
- 方案 D：单次 run 中途 hot switch。
  - 放弃原因：ADR 0040 已明确放弃；复杂度和一致性风险仍不适合 P3。
- 方案 E：双 provider 并行在线竞速。
  - 放弃原因：测试矩阵、成本和故障面显著扩大，不属于 P3-M1 daily MVP。
- 方案 F：P3-M1 Claude 主路径走 LiteLLM 或 OpenAI-compatible proxy。
  - 放弃原因：可以降低短期接入成本，但会把 provider 差异推迟到 tools、image、streaming 和 structured output 阶段爆发；P3-M1 应把统一边界放在 NeoMAGI 自己的 adapter 层。

## 影响

- Gateway RPC 需要扩展 provider / model profile 查询接口，供前端获取 enabled profiles。
- `ChatSendParams` 需要在现有 `provider` 基础上增加受控 `model_profile` 或等价字段。
- Provider registry 需要支持同一 provider 下多个 model profiles，或等价的 profile-to-AgentLoop / model 绑定。
- 需要新增 Anthropic provider config 与 `AnthropicModelClient` adapter；具体 message/tool/image 转换细节留给 P3-M1 implementation plan。
- Budget reservation、日志和 daily casebook 需要记录 provider、model profile、实际 model。
- UI 需要提供 provider / model selector，并在当前 run 中显示已绑定的计算资源。
- Provider capability 不足时，应返回明确错误或降级提示。
