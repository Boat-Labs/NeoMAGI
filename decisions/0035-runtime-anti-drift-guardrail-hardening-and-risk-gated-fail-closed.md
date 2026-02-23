# 0035-runtime-anti-drift-guardrail-hardening-and-risk-gated-fail-closed

- Status: proposed
- Date: 2026-02-23

## 选了什么
- 在 ADR 0030 的基础上补充运行时防护：将 compaction 相关锚点校验从“最小存在性探针”提升为“核心约束清单（Core Safety Contract）校验”。
- 统一 guard 校验口径为“最终执行上下文可见性”，并在两个关键点执行：
  - LLM 调用前（system prompt + compacted context + effective history）。
  - 高风险工具执行前（复用同一 guard 状态做执行闸门）。
- 失败语义采用风险分级：
  - 低风险/纯对话路径：允许降级继续，保持会话可用性。
  - 高风险路径（具写入、执行或外部副作用能力）：fail-closed 阻断，并返回结构化错误码与日志事件。
- 里程碑落位采用“**M3 Phase 0 实施，作为 M2 风险回补**”策略：不重开 M2 实施范围，但将防护前置为 M3 主链路改造的硬门槛。

## 为什么
- 现有 M2 机制已建立“锚点可见性 + retry/degrade”基线，但在 guard 失效时默认 fail-open，无法覆盖高风险执行场景。
- 将“核心约束是否可见”从验收时离线证据升级为运行时执行门槛，可以直接降低长对话压缩导致的越权风险。
- 在 M3 Phase 0 落地可复用其既有主链路改造窗口（ToolContext、PromptBuilder、SessionSettings），避免重复改线和额外返工。
- 风险分级可同时满足两类目标：会话连续性（不断对话）与安全边界（不做高风险误执行）。

## 放弃了什么
- 方案 A：保持 ADR 0030 当前实现，不新增运行时执行闸门。
  - 放弃原因：对“高风险动作误执行”缺少最后防线，风险不可接受。
- 方案 B：guard 失败时统一全量 fail-closed（所有回复与工具均阻断）。
  - 放弃原因：可用性损失过大，与 M2/M3 的连续性目标冲突。
- 方案 C：将该防护延后到 M3 全部完成后再统一处理。
  - 放弃原因：会把风险暴露窗口延长到多个 phase，且后置改造成本更高。

## 影响
- 需新增并维护 Core Safety Contract 的来源与版本策略（至少覆盖 AGENTS/USER/SOUL 中不可退让约束）。
- 需在 AgentLoop 增加“guard 状态 -> 工具执行闸门”的统一判定路径，并区分高风险/低风险工具组。
- 需补充测试矩阵：
  - guard 缺失/损坏时高风险工具被阻断；
  - guard 失败时纯对话可降级继续；
  - 关键日志与错误码可审计。
- 需更新文档口径（M3 计划与相关 architecture）：明确该项为 M3 Phase 0 的前置防护，不再仅作为离线评估项。

