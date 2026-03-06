# 0049-growth-governance-kernel-adapter-first

- Status: accepted
- Date: 2026-03-06

## 选了什么

- 为 `P2-M1a` 引入 `src/growth/` 作为显式成长治理内核的编排层。
- `src/growth/` 只负责治理类型、policy registry、governance engine 与 adapter registry；不负责对象本体存储，也不吞并 `src/memory/` 或未来对象自己的领域模块。
- `P2-M1a` 保留现有 `SOUL.md` 生命周期语义：
  - status 继续使用 `proposed`、`active`、`superseded`、`rolled_back`、`vetoed`
  - eval 结果继续写入 proposal payload，而不是引入 `evaluated` / `rejected` 新状态
- `P2-M1a` 只正式接入一个成长对象：`soul`。`skill_spec`、`wrapper_tool`、`procedure_spec`、`memory_application_spec` 仅保留 reserved registration。
- `SoulGovernedObjectAdapter` 采用 thin wrapper 策略包装现有 `EvolutionEngine`；`EvolutionEngine` 的公开 API 与当前可观察行为保持不变。
- 跨 kind promote / demote policy 在 `P2-M1a` 中只保留 machine-readable schema 与 registry 形状，不要求端到端执行。

## 为什么

- 当前仓库唯一成熟的显式成长对象仍是 `SOUL.md`。在 `P2-M1a` 里直接扩张状态机或统一存储，会把“治理语义统一”变成“历史行为重写”，回归风险过高。
- 将治理编排从 `src/memory/` 中抽出到 `src/growth/`，可以把“跨对象治理”与“具体对象存储”分离，避免后续 `skill object`、procedure spec 等新对象仍被迫挂靠在 memory 语义下。
- 保留现有 `EvolutionEngine` 状态机，能最大限度复用已存在的测试、补偿逻辑和 `soul_versions` 数据，不把 `P2-M1a` 变成一次隐性迁移。
- 采用 thin wrapper 而不是 replace/refactor，可以把 `P2-M1a` 限定为“先建立统一治理入口”，而不是“先重写唯一已稳定的成长对象实现”。
- 先把其他对象标成 reserved，可在不抢跑 `P2-M1b/P2-M1c/P2-M3` 的前提下，固定 NeoMAGI 对显式成长对象的治理口径。

## 放弃了什么

- 方案 A：继续在 `src/memory/evolution.py` 原地扩展，把 `EvolutionEngine` 直接做成跨对象通用治理内核。
  - 放弃原因：会把 `SOUL.md` 的历史实现细节和 memory 语义继续扩散到未来所有成长对象上，边界不清。
- 方案 B：在 `P2-M1a` 直接引入 `evaluated` / `rejected` 新状态，或顺手做统一生命周期表。
  - 放弃原因：这会在没有第二类对象正式接入之前，提前改变现有 `SOUL.md` 状态机与存储语义，收益不足以覆盖回归风险。
- 方案 C：让新的 governance engine 直接替代 `EvolutionEngine`。
  - 放弃原因：replace 策略虽然表面更“干净”，但会把 `P2-M1a` 变成行为重写；当前阶段更需要薄编排层，而不是重写唯一已稳定的演化实现。
