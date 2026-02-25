# 0040-m6-provider-routing-granularity-agent-run-boundary

- Status: proposed
- Date: 2026-02-25

## 选了什么
- M6 的 provider 选择粒度定义为 `agent-run`（请求级）绑定：每次 `chat.send` 开始时确定 provider/model，本次 run 执行期间保持不变；下一次 `chat.send` 可重新选择。
- M6 明确不做会话中途 hot switch，也不做双 provider 并行在线路由。
- 术语边界（防歧义，作为本 ADR 的约束定义）：
  - `任务（product task）`：用户目标，可跨多轮对话推进（见 `design_docs/roadmap_milestones_v3.md` 中 M2 “长对话、多轮任务”）。
  - `turn`：压缩语义中的对话轮次（`user` 消息 + 后续 `assistant/tool` 消息），见 `design_docs/m2_architecture.md` 与 `src/agent/compaction.py`。
  - `agent-run`：一次 `chat.send` 请求触发的一次完整执行闭环（session claim -> `AgentLoop.handle_message` -> release），见 `src/gateway/app.py`。
- 为满足成本分级，允许在 run 启动时根据“任务分类/agent 类型/预算策略”选择 provider；该分类仅作为 run 入口路由信号，不改变 `product task` 定义。

## 为什么
- 与 M6 边界一致：M6 目标是“可切换、可回退”，不引入高复杂度运行时切换机制（见 `design_docs/m6_architecture.md`）。
- 保持既有模型路线一致性：延续 ADR 0002（OpenAI 默认 + Gemini 验证）与 ADR 0016（OpenAI SDK 统一接口）。
- 对 24h harness 场景可直接降本：快任务走低成本 provider，复杂代码/调度任务走高能力 provider；同时避免 hot switch 带来的上下文一致性与可复现性风险。
- 改动集中在调度入口与配置层，不侵入 tool loop、compaction、session 关键链路。

## 放弃了什么
- 方案 A：仅做配置级全局切换（`.env + 重启`，进程级单 provider）。
  - 放弃原因：虽最简，但无法支持同一运行期内按任务分级控成本。
- 方案 B：会话中途热切换 provider。
  - 放弃原因：需要处理上下文对齐、工具链状态一致性、重试与审计可追溯，复杂度超出 M6。
- 方案 C：双 provider 并行在线路由/竞速。
  - 放弃原因：测试矩阵与故障面显著扩大，属于 M6+ 议题。

## 影响
- 需要新增 run 级 provider 路由配置（可静态映射），并在执行日志记录 provider/model/预算命中结果。
- M6 验收口径补充：
  - 同一会话中相邻两次 `chat.send` 可使用不同 provider；
  - 单次 `agent-run` 内 provider 不发生变化。
- 文档与代码出现 `task` 时应显式标注语义（`product task` 或 `agent-run`）；默认推荐使用 `agent-run` 术语避免歧义。
- 若后续进入 M7 再评估 hot switch/并行路由，并以新 ADR supersede 本决策。
