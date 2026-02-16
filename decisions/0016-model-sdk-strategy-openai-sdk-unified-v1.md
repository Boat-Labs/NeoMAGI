# 0016-model-sdk-strategy-openai-sdk-unified-v1

- Status: accepted
- Date: 2026-02-16

## 选了什么
- v1 版本统一使用 `openai` SDK 作为模型调用入口。
- v1 支持的模型后端限定为：OpenAI、Gemini、Ollama（通过 OpenAI-compatible 接口接入）。
- Anthropic 不纳入 v1 主兼容范围。

## 为什么
- 单 SDK 路线可显著降低首版集成复杂度和维护成本。
- OpenAI、Gemini、Ollama 三者可通过兼容接口满足当前主要场景。
- v1 目标是先验证主链路，不提前扩展到多 SDK 并行维护。

## 放弃了什么
- 方案 A：为每家 provider 维护独立 SDK 适配层。
  - 放弃原因：首版会显著增加接口差异处理和测试成本。
- 方案 B：v1 同时完整支持 Anthropic 独立接口。
  - 放弃原因：当前优先级不高，不符合“最小可用闭环”原则。

## 影响
- 模型调用抽象以 OpenAI 风格请求/响应为统一契约。
- v1 仅承诺最小公共能力集合（基础对话、流式输出、基础工具调用）。
- provider 特有能力采用可选能力开关管理，不强求跨 provider 完全一致。
