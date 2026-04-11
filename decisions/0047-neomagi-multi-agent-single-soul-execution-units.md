---
doc_id: 019cc032-e598-7892-a9b6-9808e435fabd
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-05T23:51:27+01:00
---
# 0047-neomagi-multi-agent-single-soul-execution-units

- Status: accepted
- Date: 2026-03-05
- Amended by: ADR 0061

## 选了什么
- 将 NeoMAGI 的多 agent 定义固定为：在**同一个用户利益**与**同一个 SOUL / principal**约束下的多个受治理执行单元，而不是默认的“多自我 / 多人格”系统。
- 将多 agent 的主要动机固定为：
  - 隔离不同任务或 subtask 的上下文；
  - 隔离工具权限与副作用边界；
  - 支持并行执行与独立校验；
  - 为 builder / review / eval / rollback 等流程提供结构化分工。
- 将默认角色语义固定为执行分工，而不是人格分工：
  - `primary agent`：直接代表用户，持有最终对齐与决策权；
  - `worker agent`：执行局部任务，使用 task-local 上下文；
  - `reviewer / critic agent`：做校验、对比、审阅与风险检查。
- 明确多视角（例如科学家 / 历史学家 / 母亲等）仅作为**未来可选接口**预留，不作为当前默认产品语义，也不要求所有用户接受同一套视角设计。
- 明确多视角的未来形态应优先是：
  - 同一 SOUL 下的 `stance` / `perspective` / `review mode`；
  - 用户可选、可配置、可关闭；
  - 默认不具备独立长期身份与独立长期记忆。
- 将 Slack 群聊 / 多 agent 群聊定位为可选交互表面，不作为多 agent 成立的前置理由；在 runtime、identity、procedure 契约未稳定前，不优先推进。

## 为什么
- NeoMAGI 的基石是 personal agent：代表用户信息利益、对抗信息熵增、在稳定原子工具之上逐步自我进化。若默认走多人格路线，会同时增加身份语义、记忆边界、责任归属与上下文管理复杂度，偏离当前核心目标。
- 对 personal agent 来说，真正需要的不是“多个自我一起聊天”，而是把复杂任务拆成多个受治理执行单元，降低单一长上下文的污染与错误耦合。
- 多 agent 在本项目中的主要价值是工程性的：上下文隔离、并行推进、独立校验、权限分层、可恢复 handoff，而不是表演性的群聊体验。
- 保持单一 SOUL / principal，可确保所有子 agent 最终仍代表同一个用户利益，避免出现“谁真正代表用户”这一产品歧义。
- 多视角讨论是有价值的，但它更适合作为同一身份下的可选思考模式，而不是一组长期并存、各自成长的人格体。这样既能为未来保留辩论式思考接口，又不会提前把系统复杂度锁死。
- 用户对于“有用的视角”会随时间演进，也不应强加给所有 NeoMAGI 用户同一套视角框架。因此，多视角必须是可选扩展，不是核心人格假设。
- Slack / 群聊只有在其作为协作面、审批面、通知面或外部工作流入口时才有明确价值；若只是为了展示多个 agent 对话，会放大复杂度而不显著提升个人助手价值。

## 放弃了什么
- 方案 A：将多 agent 默认定义为多个长期人格 / 多个 SOUL 并存，并通过群聊或讨论方式协作。
  - 放弃原因：这会放大熵增，模糊用户代表权、记忆归属和治理边界，不符合当前 personal agent 方向。
- 方案 B：完全不引入多 agent，继续让单 agent 承担所有任务拆解、执行、校验和恢复。
  - 放弃原因：复杂任务会持续污染主上下文，builder / reviewer / evaluator 等职责难以分离，不利于后续 runtime 与自我进化治理。
- 方案 C：将 Slack 群聊视为多 agent 的主要产品驱动力。
  - 放弃原因：渠道形态不是核心问题，真正的问题是 runtime contract、identity 对齐和上下文治理；过早以群聊为中心会把产品重心带偏。

## 影响
- `P2-M2` 应将多 agent 首先实现为 execution-oriented runtime structure，而不是多人格产品层。
- 子 agent 默认不拥有独立长期记忆；只有被显式 publish / merge 的结果，才允许进入用户级连续记忆。
- agent 间默认不共享整段原始上下文，应优先交换 task brief、constraints、intermediate results、evidence 与 open questions。
- 高风险动作仍应通过主 agent 或显式 procedure checkpoint 触发，不因引入多 agent 而分散最终责任。
- 若未来实现多视角接口，应视为同一 SOUL 下的 stance / perspective 扩展层；可由用户自定义，但不默认进入核心 prompt 身份结构。
- Slack / 群聊暂不进入已规划 milestone；若未来进入路线图，也应作为可选协作 / 审批 / 通知表面，而不是作为“多人格讨论场”的默认实现。
