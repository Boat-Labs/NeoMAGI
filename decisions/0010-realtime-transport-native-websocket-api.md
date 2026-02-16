# 0010-realtime-transport-native-websocket-api

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 前端实时通信采用浏览器原生 `WebSocket API`。
- 不引入 `socket.io` 作为首期实时通信层。

## 为什么
- 原生 WebSocket 协议直接、依赖少、调试路径短。
- 与后端既定 WebSocket 路线一致，可减少中间抽象层复杂度。
- 当前场景不需要 `socket.io` 提供的附加能力（如房间语义和回退传输）。

## 放弃了什么
- 方案 A：使用 `socket.io`。
  - 放弃原因：额外协议层与依赖成本不符合当前精简原则。
- 方案 B：仅用轮询/长轮询实现实时更新。
  - 放弃原因：交互时效性与体验不如 WebSocket。

## 影响
- 前后端实时协议按 WebSocket 消息约定统一设计。
- 连接管理、重连和心跳策略由应用层明确规范。
