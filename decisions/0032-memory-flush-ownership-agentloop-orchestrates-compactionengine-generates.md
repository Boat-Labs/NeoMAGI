# 0032-memory-flush-ownership-agentloop-orchestrates-compactionengine-generates

- Status: accepted
- Date: 2026-02-21

## 选了什么
- M2 的 memory flush 候选生成采用单一职责边界：
  - `AgentLoop` 仅负责编排触发顺序与失败策略（budget check -> compact -> store -> rebuild）。
  - `CompactionEngine` 唯一负责从可压缩区间生成 `memory_flush_candidates`（可内部调用 `MemoryFlushGenerator`）。
  - `SessionManager.store_compaction_result(...)` 负责一次性原子持久化 compaction 产物与 flush 候选。
- `AgentLoop` 不再单独调用 `MemoryFlushGenerator`，禁止在编排层再实现一条独立 flush 生成路径。

## 为什么
- 避免“双源生成”导致候选条目不一致、重复提取和回归难定位。
- 保持编排层与算法层职责分离，符合极简闭环原则，便于测试与演进。
- 与 Pi 的模式一致：外层负责触发与重试，内层 compaction 模块负责提取与摘要逻辑。
- 为 M3 接管记忆落盘提供稳定输入契约，减少跨里程碑语义漂移。

## 放弃了什么
- 方案 A：`AgentLoop` 与 `CompactionEngine` 同时具备 flush 生成能力（双路径并存）。
  - 放弃原因：输出来源不唯一，出现分歧时难以判定真相来源，维护成本高。
- 方案 B：全部由 `AgentLoop` 直接处理 flush 候选提取，`CompactionEngine` 仅做摘要。
  - 放弃原因：编排层承担算法细节，耦合上升，不利于后续优化与复用。

## 影响
- `CompactionResult` 继续作为 flush 候选唯一输出通道，`memory_flush_candidates` 字段语义保持稳定。
- 测试策略分层：
  - `compaction.py` 覆盖候选提取、分类、上限、降级。
  - `agent.py` 仅验证调用顺序与持久化次数，不校验候选提取细节。
- 后续若调整候选生成规则，仅需修改 compaction 模块，不影响 agent loop 编排逻辑。
