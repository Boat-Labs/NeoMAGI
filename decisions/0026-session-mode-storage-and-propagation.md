# 0026-session-mode-storage-and-propagation

- Status: accepted
- Date: 2026-02-20
- Related: ADR 0024, ADR 0025

## 背景
- M1.5 固定 `chat_safe`，但后续需要支持会话级 mode 切换。
- mode 必须有稳定的存储位置和一致的传递链路，避免“可见能力”和“可执行能力”漂移。

## 选了什么
- mode 作为会话状态，存储在 SessionManager（每个 session 一个 mode 字段）。
- 默认值由配置提供（`DEFAULT_SESSION_MODE`，默认 `chat_safe`），仅用于新 session 初始化。
- AgentLoop 每轮从 SessionManager 读取 mode，并传入 ToolRegistry：
  - 暴露闸门：按 mode 返回 `tools_schema`。
  - 执行闸门：按 mode 校验工具是否可执行。
- M1.5 阶段 mode 只读，不开放外部写接口。

## 为什么
- mode 是会话级状态，不应硬编码在 AgentLoop，也不应使用全局单值配置。
- 会话级存储支持后续用户显式切换、审计回放和问题定位。
- 双闸门使用同一 mode 来源，可保证行为一致性。
- 与 ADR 0025 保持一致：默认 `chat_safe`，模型无权自行切换。

## 放弃了什么
- 方案 A：AgentLoop 硬编码 mode。
  - 放弃原因：无法承载会话级差异，后续扩展返工大。
- 方案 B：配置文件全局 mode。
  - 放弃原因：粒度错误，无法表达不同会话状态。
- 方案 C：请求级 mode 参数即时生效。
  - 放弃原因：状态不稳定，审计复杂，易引发一轮内语义不一致。

## 影响
- 会话模型新增 mode 字段并以 `chat_safe` 为默认值。
- chat 主链路新增 mode 读取与透传。
- 工具暴露与执行统一按 mode 判定；缺失或异常时 fail-closed 到 `chat_safe`。
- M1.5 验收中，mode 对外不可写，且全量会话固定在 `chat_safe`。
