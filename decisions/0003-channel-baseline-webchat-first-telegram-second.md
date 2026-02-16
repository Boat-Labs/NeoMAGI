# 0003-channel-baseline-webchat-first-telegram-second

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 第一渠道定义为 WebChat（简洁网页聊天入口）。
- 第二渠道定义为 Telegram（后续适配渠道）。

## 为什么
- WebChat 作为第一渠道，启动成本更低，便于快速验证核心交互闭环。
- Telegram 作为第二渠道，更适合作为扩展验证“跨渠道一致性”而不是首期阻塞项。
- 该顺序与当前精简路线一致：先完成主链路，再扩展渠道。

## 放弃了什么
- 方案 A：Telegram 作为第一渠道。
  - 放弃原因：初期外部平台依赖更强，不利于快速收敛主链路问题。
- 方案 B：同时并行做 WebChat 与 Telegram。
  - 放弃原因：范围扩大，阶段目标容易失焦。

## 影响
- M1-M3 阶段默认在 WebChat 上验证核心能力。
- M4 阶段的主要目标明确为 Telegram 适配并验证跨渠道一致性。
