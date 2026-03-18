# 0057-freeze-only-hard-to-remediate-foundations

- Status: accepted
- Date: 2026-03-18
- Note: 本 ADR 是路线指导原则；用于约束后续 ADR 与 milestone plan 应冻结什么、不应冻结什么，不直接定义新的运行时对象或实现机制。

## 背景

- NeoMAGI 的长期目标之一，是为 agent 保留尽可能大的自我进化空间，而不是把今天的人类认知提前固化成长期上限。
- 但完全不冻结基础约束，会让后续演化失去稳定身份、可追溯性、可回滚性与真源边界，最终反而限制系统成长。
- 近几轮设计已经反复出现同一类判断：
  - ADR 0053 固定稳定 ID，而不固定 projection 结构
  - ADR 0055 固定 artifact 真源分层与 `artifact_id`，而不提前定义知识图谱
  - ADR 0056 固定 `wrapper_tool` 的运行时边界，而不提前定义更大的 procedure runtime
- 因此需要一条更上层的路线原则，统一回答：哪些东西应该在治理层尽早冻结，哪些东西应留给 agent 未来在 projection / orchestration 层自主演化。

## 选了什么

- 后续 ADR 默认只冻结未来不可轻易补救的基础约束，主要包括：
  - stable identity
  - scope boundary
  - provenance
  - source-of-truth vs projection boundary
  - reversibility / rollback boundary
  - safety / governance boundary
- 后续 ADR 默认不冻结未来可由 agent 在 projection / orchestration 层自主演化的结构，主要包括：
  - ontology shape
  - graph / edge taxonomy
  - traversal / retrieval strategy
  - multi-thought organization
  - intermediate object layering
  - higher-order planning / orchestration structure
- 当系统需要为未来演化预留空间时，优先冻结“最小可长期稳定的地基”，例如：
  - stable ID system
  - object type boundary
  - minimal source metadata
  - projection-only extension points
- 任何新的 ADR 若要冻结上层结构，必须额外说明：
  - 为什么该结构属于难以事后补救的基础约束
  - 为什么不能先只冻结更小的 substrate
- milestone plan 可以为 V1 实施给出具体形状，但除非已被 ADR 明确提升为治理约束，否则这些具体形状默认视为 implementation-level choice，而不是长期制度。

## 为什么

- 未来最难补救的通常不是“今天图谱长什么样”，而是身份是否稳定、边界是否清楚、真源是否可追溯、变更是否可回滚。
- 先冻结最小地基，可以让 agent 后续在不破坏真源与治理边界的前提下，自主发明更好的 projection、memory organization 和 orchestration 结构。
- 这能避免人类把当前理解误当成长期最优结构，过早锁死系统成长路径。
- 同时，这也防止“为了开放演化”而把基础约束全部留空，导致未来任何扩展都需要迁移真源或重写治理边界。

## 放弃了什么

- 方案 A：在基础阶段预先定义较完整的知识图谱、记忆分层和 orchestration 结构。
  - 放弃原因：会把今天的人类设计假设过早制度化，压缩 agent 自主演化空间。
- 方案 B：尽量不做治理冻结，后续再按需要补。
  - 放弃原因：stable identity、真源边界、回滚边界等基础约束若缺失，后续补救成本很高。
- 方案 C：把 V1 实施细节直接写成 ADR，以提高短期明确性。
  - 放弃原因：会让治理层承担过多实现细节，增加未来调整阻力。

## 影响

- 后续 ADR 在新增约束时，应优先回答“这是不是未来难以补救的基础约束”，而不是优先回答“这是不是当前最方便描述的结构”。
- 后续 milestone plan 可以为实现提供具体 V1 方案，但应避免把可替换的 projection / orchestration 结构误写成长期治理承诺。
- 当需要为未来能力预留空间时，优先增加稳定身份、真源元数据和清晰边界，而不是提前设计完整上层模型。
- 本 ADR 是路线指导原则，不替代已有已接受 ADR 的具体约束；若与既有 accepted ADR 冲突，应通过正式 supersede / amend 处理，而不是隐式覆盖。
