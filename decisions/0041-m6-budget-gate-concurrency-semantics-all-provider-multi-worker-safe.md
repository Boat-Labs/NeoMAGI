# 0041-m6-budget-gate-concurrency-semantics-all-provider-multi-worker-safe

- Status: accepted
- Date: 2026-02-25

## 选了什么
- M6 预算闸门的正确性前提对齐多 worker 语义：**不依赖单进程内存锁作为最终正确性机制**。
- 预算闸门采用**全 provider 统一预算池**：OpenAI、Gemini 及后续 provider 统一走同一预算闸门，不允许 provider 绕过。
- 统一预算闸门采用 PostgreSQL 原子机制（预占 + 对账），最小规则保持三条：
  - `warn/stop` 两阈值：`€20` warn，`€25` stop；
  - 任一 provider 调用前必须原子预占预算，失败直接返回 `BUDGET_EXCEEDED`；
  - 调用结束必须对账（多退少补）并写审计记录。
- provider 路由与预算闸门组合语义：先完成 provider 绑定（ADR 0040），再统一执行预算闸门。
- 成本记录要求同时保留：全局累计预算（闸门依据）与按 provider 分项统计（评测分析依据）。

## 为什么
- ADR 0021 已明确系统部署目标支持多 worker，不以单 worker 作为正确性前提；预算闸门必须与该前提一致。
- M6 引入 per-run provider 路由（ADR 0040）后，若按 provider 分裂预算闸门，容易出现“切 provider 绕过预算”的口径歧义。
- “一套预算管所有 provider”能给用户与运维提供单一、清晰、可预测的成本行为。
- PostgreSQL 已是硬依赖（ADR 0006/0020），在现有栈内实现原子预占成本最低，避免新增 Redis 等运维面。

## 放弃了什么
- 方案 A：单进程 `asyncio.Lock` + 内存累计作为预算闸门。
  - 放弃原因：仅在单 worker 有效，与 ADR 0021 冲突，多 worker 下存在并发超支窗口。
- 方案 B：仅对 Gemini 启用预算闸门，OpenAI 不受约束。
  - 放弃原因：会形成多套成本语义，用户可通过切换 provider 规避闸门，不符合“一套预算”目标。
- 方案 C：仅后置记账（调用后再统计成本），不做预占。
  - 放弃原因：无法阻断超预算调用，不满足“hard stop”语义。
- 方案 D：M6 引入 Redis 分布式锁/计数器。
  - 放弃原因：会新增基础设施复杂度，不符合“最小可用闭环”。

## 影响
- 需要新增预算状态与预占流水持久化结构（基于 PostgreSQL），支持原子预占与幂等对账。
- `chat.send` 的所有 provider 路径都需实现 `try_reserve -> call -> settle(finally)`，避免预占泄漏。
- 预算相关错误码需稳定化：`BUDGET_EXCEEDED`（拒绝）、`BUDGET_WARNING`（告警日志/事件）。
- 测试与验收需覆盖跨 provider 并发预占场景（多 worker/并发请求），验证不超卖与对账一致性。
- M6 报告需同时输出：全局预算累计、Gemini 分项成本、OpenAI 分项成本（满足迁移分析可解释性）。
- 若后续扩展为“多预算池/按用户配额/跨项目配额”，应新增 ADR 或 supersede 本决议。
