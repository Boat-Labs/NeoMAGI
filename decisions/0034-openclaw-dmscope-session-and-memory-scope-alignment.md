# 0034-openclaw-dmscope-session-and-memory-scope-alignment

- Status: accepted
- Date: 2026-02-22

## 选了什么
- 在治理与架构层对齐 OpenClaw 的 `dmScope` 策略，并作为 NeoMAGI 后续会话隔离的统一起点。
- `dmScope` 采用统一枚举：`main`、`per-peer`、`per-channel-peer`、`per-account-channel-peer`。
- 会话隔离与记忆隔离使用同一作用域真源：
  - Session key 解析遵循 `dmScope`。
  - 记忆写入保留来源作用域元数据。
  - 记忆检索与 prompt recall 必须按作用域过滤。
- 里程碑分工：
  - M3 负责作用域契约、数据面字段与检索/注入过滤口径。
  - M4 负责在 Telegram 等多渠道场景激活非 `main` 作用域并完成渠道映射。
- 默认启动策略保持保守：WebChat 单用户环境默认 `dmScope=main`，但架构与验收按全量 `dmScope` 模型定义。

## 为什么
- 当前“main/group”命名虽可用，但不足以支撑多渠道同人识别与可控共享，后续扩展会产生语义漂移。
- 若 Session 与 Memory 各自维护作用域规则，极易出现“会话隔离正确但记忆泄漏”或反向问题。
- 先在 M3 固化统一契约，可避免 M4 渠道接入时大规模返工。
- 保持默认 `main` 可控制当前交付风险，同时不牺牲未来扩展路径。

## 放弃了什么
- 方案 A：继续使用“DM=main / group=group:*”作为长期模型，不引入 `dmScope`。
  - 放弃原因：无法表达多渠道同人隔离策略，扩展到 Telegram/多账户时边界不清。
- 方案 B：采用强隔离 main-only（仅 main 可写/可检索记忆）。
  - 放弃原因：过度绑定单渠道单会话模型，与 OpenClaw 路线不兼容，后续迁移成本高。
- 方案 C：等 M4 再引入 `dmScope`。
  - 放弃原因：会导致 M3 的 memory schema 与检索接口二次重构，违背“最短演进路径”。

## 影响
- 需同步更新以下文档口径：
  - `design_docs/roadmap_milestones_v3.md`
  - `design_docs/m3_architecture.md`
  - `design_docs/memory_architecture.md`
  - `design_docs/modules.md`（会话模块说明）
- M3 验收需新增作用域正确性检查：不同 `dmScope` 下，不得发生未授权跨作用域记忆召回。
- M4 验收需新增渠道映射正确性检查：同一用户在不同渠道的会话隔离行为符合配置策略。
