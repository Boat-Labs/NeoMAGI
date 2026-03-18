# 0056-wrapper-tool-onboarding-and-runtime-boundary

- Status: accepted
- Date: 2026-03-18
- Note: 本 ADR 只定义 `wrapper_tool` 在 `P2-M1c` 的 onboarding 决策与治理边界；不在本轮冻结具体接线方式、存储形状或 runtime API。

## 背景

- ADR 0048 已明确：新经验应先沉淀为 `skill object`，只有高频、稳定、边界清晰的部分才继续下沉为更稳定的 capability 单元。
- ADR 0049 已建立 growth governance kernel 与 adapter-first 接入方式，允许新的 growth object kind 进入统一的 `propose -> evaluate -> apply -> rollback` 路径。
- ADR 0054 已固定：growth eval contract 必须 object-scoped、versioned、immutable；普通 proposal 不能与自己的 judge / harness 一起修改。
- `P2-M1c` 的核心闭环之一，是把“先学成 skill、再复用、再 promote 成稳定 capability 单元”跑通；当前最小缺口不是 `procedure_spec`，而是缺少一个比 skill 更稳定、但又明显小于 procedure 的正式对象。
- `wrapper_tool` 与 `procedure_spec` 当前都还没有在治理层完成正式 onboarding，因此必须先明确边界，再进入实施。

## 选了什么

- `P2-M1c` 正式 onboarding `wrapper_tool` 作为下一个 growth object kind。
- `procedure_spec` 不在 `P2-M1c` onboarding，明确推迟到 `P2-M2`。
- `wrapper_tool` V1 的边界固定为：
  - single-turn
  - governed capability unit
  - clear input / output contract
  - can participate in `propose -> evaluate -> apply -> rollback`
- `wrapper_tool` V1 明确不承担：
  - cross-turn state
  - branching workflow
  - checkpoint / resume
  - generic workflow DSL
  - procedure runtime 语义
- `wrapper_tool` 的治理要求是：
  - 可被受治理地 apply
  - 可被受治理地 rollback / disable
  - 后续可被更稳定版本 supersede
- `wrapper_tool` contract 的演进必须遵守 ADR 0054 的 object-scoped、versioned、immutable 原则。
- 本 ADR 不冻结：
  - `implementation_ref` 的精确语法
  - registry / runtime manager 的具体 API 形状
  - 存储表结构与 migration 命名
  - smoke harness 与 adapter 细节

## 为什么

- `wrapper_tool` 正好补上 `skill -> stable capability unit` 的最小 promote 闭环，而不会像 `procedure_spec` 那样把 `P2-M1c` 直接推向更大的 runtime 问题。
- 将 `wrapper_tool` 固定为 single-turn governed capability，能保持它与 `procedure_spec` 的可审计边界，避免两者在实现中相互渗透。
- 对 `wrapper_tool` 先冻结 onboarding 与对象边界，而不冻结接线方式，符合 ADR 0057 的路线原则：先固定难以后补的地基，再把 projection / orchestration 层的具体形状留给实施与后续演化。
- apply / rollback / supersede 属于治理闭环需要回答的对象能力边界；但这些边界并不要求 ADR 现在就规定具体 runtime API 叫什么、如何接线。
- contract 演进继续受 ADR 0054 约束，可以确保历史 proposal / eval 仍能回答“当时按哪一版 contract 被判断”。

## 放弃了什么

- 方案 A：在 `P2-M1c` 直接 onboarding `procedure_spec`。
  - 放弃原因：会把本轮从 capability promotion 闭环扩大到 procedure runtime 边界、状态机和 recoverability，复杂度过高。
- 方案 B：让 `wrapper_tool` V1 直接支持 branching workflow / stateful graph。
  - 放弃原因：这会实质滑入 `procedure_spec` 范畴，破坏两类对象的治理边界。
- 方案 C：在本 ADR 中一并冻结 `implementation_ref`、registry API、表结构与 harness 细节。
  - 放弃原因：这些都更适合作为 design / implementation choice，而不是长期治理约束。
- 方案 D：把 `wrapper_tool` 的上线与自动 promote / 自动 apply 绑在一起。
  - 放弃原因：本轮目标是受治理 promote 闭环，不是放开自动演化。

## 影响

- `P2-M1c` 实施可以在不扩张到 `procedure_spec` 的前提下，完成 `skill_spec -> wrapper_tool` 的最小 promote 闭环。
- `src/growth/policies.py` 后续应体现：
  - `wrapper_tool` = onboarded
  - `procedure_spec` = reserved for `P2-M2`
- `wrapper_tool` 的具体接线方式、registry API、表结构、migration 命名、adapter 检查项与 smoke harness 仍属于实施层，不由本 ADR 固定。
- `GC-2` 在 `P2-M1c` 中成为有效验收路径，但其 promote 阈值仍沿用既有 policy schema，而不是由本 ADR 重新定义。
- 若后续需要引入 stateful / branching / recoverable 的 capability unit，应通过新的 ADR 处理，而不是在 `wrapper_tool` 语义上持续扩张。
