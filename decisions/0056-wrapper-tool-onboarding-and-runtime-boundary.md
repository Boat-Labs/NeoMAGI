# 0056-wrapper-tool-onboarding-and-runtime-boundary

- Status: proposed
- Date: 2026-03-18
- Note: 本 ADR 只定义 `wrapper_tool` 在 `P2-M1c` 的 onboarding 决策与运行时边界；不在本轮引入 procedure runtime、generic workflow DSL 或自动 promote/apply。

## 背景

- ADR 0048 已明确：新经验应先沉淀为 `skill object`，只有高频、稳定、边界清晰的部分才继续下沉为更稳定的 capability 单元。
- ADR 0049 已建立 growth governance kernel 与 adapter-first 接入方式，允许新的 growth object kind 进入统一的 `propose -> evaluate -> apply -> rollback` 路径。
- ADR 0054 已固定：growth eval contract 必须 object-scoped、versioned、immutable；普通 proposal 不能与自己的 judge / harness 一起修改。
- `P2-M1c` 的核心闭环之一，是把“先学成 skill、再复用、再 promote 成稳定 capability 单元”跑通；当前最小缺口不是 `procedure_spec`，而是缺少一个比 skill 更稳定、但又明显小于 procedure 的正式对象。
- `wrapper_tool`、`procedure_spec`、`memory_application_spec` 当前都还没有在治理层完成正式 onboarding，因此必须先明确边界，再进入实施。

## 选了什么

- `P2-M1c` 正式 onboarding `wrapper_tool` 作为下一个 growth object kind。
- `procedure_spec` 不在 `P2-M1c` onboarding，明确推迟到 `P2-M2`。
- `memory_application_spec` 不在 `P2-M1c` onboarding，明确推迟到 `P2-M3`。
- `wrapper_tool` V1 的边界固定为：
  - single-turn
  - typed input / output
  - explicit deny semantics
  - smoke-testable
  - code-backed / registry-backed capability unit
- `wrapper_tool` V1 明确不承担：
  - cross-turn state
  - branching workflow
  - checkpoint / resume
  - generic workflow DSL
  - procedure runtime 语义
- `implementation_ref` 的 canonical 语义固定为 Python entrypoint：
  - `<module_path>:<factory_name>`
  - `factory_name` 返回 `BaseTool` 实例，或返回可立即注册的 `BaseTool` 子类
- `wrapper_tool` 的 runtime 注册必须具备 replace / remove 语义，以支持：
  - apply
  - rollback / disable
  - supersede
- `wrapper_tool` contract 的升级路径必须遵守 ADR 0054：
  - 不直接修改 skeleton 常量
  - 新建新的 versioned contract
  - runtime 引用切到新版本

## 为什么

- `wrapper_tool` 正好补上 `skill -> stable capability unit` 的最小 promote 闭环，而不会像 `procedure_spec` 那样把 `P2-M1c` 直接推向更大的 runtime 问题。
- 将 `wrapper_tool` 固定为 single-turn typed capability，能保持它与 `procedure_spec` 的可审计边界，避免两者在实现中相互渗透。
- `<module>:<factory>` 比 code blob、动态生成代码或模糊 file path 更可执行、可测试、可审阅，也更适合受治理 apply / rollback 路径。
- apply / rollback / supersede 都要求 registry 能显式 replace / remove；只有 add 语义无法支撑稳定回滚，也会制造 current-state 与 runtime registry 漂移。
- contract 升级必须新建版本，才能满足 ADR 0054 的 immutable 原则，并保证历史 proposal / eval 仍能回答“当时按哪一版 contract 被判断”。

## 放弃了什么

- 方案 A：在 `P2-M1c` 直接 onboarding `procedure_spec`。
  - 放弃原因：会把本轮从 capability promotion 闭环扩大到 procedure runtime 边界、状态机和 recoverability，复杂度过高。
- 方案 B：让 `wrapper_tool` V1 直接支持 branching workflow / stateful graph。
  - 放弃原因：这会实质滑入 `procedure_spec` 范畴，破坏两类对象的治理边界。
- 方案 C：将 `implementation_ref` 设计为 file path、code blob 或动态生成代码。
  - 放弃原因：这些形式都不如 Python entrypoint 明确，apply / rollback / test 语义更模糊。
- 方案 D：只给 `ToolRegistry` add 语义，靠覆盖或重启解决 rollback / supersede。
  - 放弃原因：会掩盖 name collision，并让 current-state、ledger 与 runtime registry 难以一致。
- 方案 E：把 `wrapper_tool` 的上线与自动 promote / 自动 apply 绑在一起。
  - 放弃原因：本轮目标是受治理 promote 闭环，不是放开自动演化。

## 影响

- `P2-M1c` 实施可以在不扩张到 `procedure_spec` 的前提下，完成 `skill_spec -> wrapper_tool` 的最小 promote 闭环。
- `src/growth/policies.py` 后续应体现：
  - `wrapper_tool` = onboarded
  - `procedure_spec` = reserved for `P2-M2`
  - `memory_application_spec` = reserved for `P2-M3`
- `ToolRegistry` 或其上层 wrapper runtime manager 后续必须提供 replace / remove 路径，不能只保留 register-only 语义。
- `wrapper_tool` 的具体表结构、migration 列名、adapter 检查项与 smoke harness 仍属于实施层，不由本 ADR 固定。
- `GC-2` 在 `P2-M1c` 中成为有效验收路径，但其 promote 阈值仍沿用既有 policy schema，而不是由本 ADR 重新定义。
