# P2-M1a 显式成长治理内核 — PM 阶段汇总

> 日期：2026-03-06 ~ 2026-03-07
> 状态：已合并，post-review 修复完成，验收通过

## 1. Milestone 总结

P2-M1a 交付显式成长治理内核（Growth Governance Kernel），采用 adapter-first 策略。三个 Phase 全部通过 Gate 验收，代码合并到 main。用户 post-review 发现 2 个 Finding，已修复并验收通过。

| Phase | 交付 | Gate 结论 | Backend Commit | 测试增量 |
|-------|------|-----------|---------------|---------|
| Phase 0+1: 类型 + 策略 + 引擎 + 适配器契约 | GrowthObjectKind/GrowthLifecycleStatus/GrowthProposal 类型, PolicyRegistry (soul=onboarded, 4 reserved), GrowthGovernanceEngine fail-closed 编排, GovernedObjectAdapter Protocol + UnsupportedGrowthObjectError | PASS | a823c1e + bf77a8c | 845→863 (+18) |
| Phase 2: Soul 适配器 | SoulGovernedObjectAdapter thin wrapper, GrowthProposal→SoulProposal 转换, EvalResult→GrowthEvalResult 转换 | PASS | 9d3f737 + 11cb705 | 863→896 (+33) |
| Phase 3: 集成测试 + ADR | 68 tests 全覆盖, ADR 0049 adapter-first 决策 | PASS | 898b513 + 9133efb | 896→913 (+17) |

Post-review 修复后最终测试总数：916 tests，0 failures，ruff clean。
基线增长：845 → 916（+71 tests，+8.4%）。

## 2. 变更清单（文件级）

### 新增文件（11 个）

**源码（7 个）**
- `src/growth/__init__.py` — 包初始化
- `src/growth/types.py` — GrowthObjectKind, GrowthLifecycleStatus, GrowthProposal, GrowthEvalResult, GrowthKindPolicy, PromotionPolicy
- `src/growth/policies.py` — PolicyRegistry: soul=onboarded, 4 reserved kinds, 2 promotion policies (schema only)
- `src/growth/engine.py` — GrowthGovernanceEngine: propose/evaluate/apply/rollback/veto/get_active + fail-closed guard
- `src/growth/adapters/__init__.py` — 包初始化
- `src/growth/adapters/base.py` — GovernedObjectAdapter Protocol + UnsupportedGrowthObjectError
- `src/growth/adapters/soul.py` — SoulGovernedObjectAdapter: thin wrapper over EvolutionEngine

**测试（4 个）**
- `tests/growth/__init__.py` — 包初始化
- `tests/growth/test_engine.py` — 34 tests: 构造注入, 6 op 委托, cross-kind mismatch, reserved/adapter-less 拒绝
- `tests/growth/test_policies.py` — 18 tests: kind 注册, soul onboarded, reserved 策略, promotion schema
- `tests/growth/test_soul_adapter.py` — 19 tests: kind 属性, Protocol 一致性, propose 转换 + proposed_by 穿透, payload 验证, eval/apply/rollback/veto/get_active 委托

### 修改文件（2 个）

- `src/memory/evolution.py` — SoulProposal 新增 `created_by: str = "agent"` 字段; EvolutionEngine.propose() 使用 `proposal.created_by` 替代硬编码
- `decisions/0049-growth-governance-kernel-adapter-first.md` — adapter-first 架构决策

总计：**13 files changed, +1,050 insertions**。

## 3. Gate 验收报告索引

| Gate | Phase | Status | Result | Target Commit |
|------|-------|--------|--------|---------------|
| p2m1a-g0 | 0+1 | closed | PASS | fb5ecef |
| p2m1a-g1 | 2 | closed | PASS | 19bba5b |
| p2m1a-g2 | 3 | closed | PASS | b9800c4 |

## 4. 用户 Post-Review Findings

| # | 严重度 | 文件 | 问题 | 修复 |
|---|--------|------|------|------|
| F1 | P1 | `src/growth/engine.py:51` | `propose()` 只按入参 `kind` 路由，不校验 `proposal.object_kind`，cross-kind 请求静默落到错误 adapter | 新增 `UnsupportedGrowthObjectError` 前置校验 + 测试 |
| F2 | P1 | `src/growth/adapters/soul.py:49` | `GrowthProposal.proposed_by` 被丢弃，`EvolutionEngine` 硬编码 `created_by="agent"`，actor 语义断链 | `SoulProposal` 新增 `created_by` 字段（默认 `"agent"` 保持向后兼容），adapter 传 `proposal.proposed_by` + 测试 |
| F1-r2 | P2 | `src/growth/engine.py:51` | cross-kind 校验用了裸 `ValueError`，不走 NeoMAGIError 异常链 | 改为 `UnsupportedGrowthObjectError` + 测试更新 |

## 5. 架构决策

| ADR | 标题 | 要点 |
|-----|------|------|
| 0049 | growth-governance-kernel-adapter-first | `src/growth/` 作为治理编排层，不替代 EvolutionEngine；soul=onboarded, 其余 reserved；thin wrapper 策略；EvolutionEngine 公开 API 不变 |

## 6. 设计约束遵守情况

| 约束 | 状态 |
|------|------|
| EvolutionEngine 公开 API 方法签名不变 | 已遵守 — 仅新增 SoulProposal 可选字段，默认值保持现有行为 |
| soul_versions 保持 SSOT (ADR 0036) | 已遵守 — adapter 不做 file I/O 或补偿 |
| GrowthLifecycleStatus 与 VALID_STATUSES 对齐 | 已遵守 — proposed/active/superseded/rolled_back/vetoed |
| 构造注入，无 service locator | 已遵守 — GrowthGovernanceEngine 接收 dict + PolicyRegistry |
| Fail-closed 语义 | 已遵守 — reserved kind + adapter-less kind + cross-kind mismatch 全部拒绝 |

## 7. 过程经验

| 事件 | 影响 | 改进 |
|------|------|------|
| coord.py `--json` flag 不存在 | GATE_OPEN 命令执行失败 | 改用 positional args 格式 |
| `phase-complete` 要求 `--task` | 命令格式与文档不一致 | 先查 `--help` 确认语法 |
| `gate-close` 要求先 `gate-review` | 流程步骤缺失 | PM 流程加入 gate-review → gate-close 序列 |
| `git add` 忽略 `dev_docs/prompts` | .gitignore 规则阻断 | 排除 prompts 目录，只提交代码和 review 文件 |
| Tester 在 worktree 找不到 beads 目录 | beads issue close 失败 | 从 main worktree 执行 beads 操作 |

## 8. 心跳日志

`dev_docs/logs/phase2/p2-m1a_2026-03-06/heartbeat_events.jsonl` — 覆盖 3 Gate 完整生命周期。

## 9. Git 合并记录

| 操作 | Commit |
|------|--------|
| Phase 0+1 backend | a823c1e, bf77a8c |
| Phase 2 backend | 9d3f737, 11cb705 |
| Phase 3 backend | 898b513, 9133efb |
| Tester review g0 | fb5ecef |
| Tester review g1 | 19bba5b |
| Tester review g2 | b9800c4 |
| Control plane projections | f2e4fd7 |
| Main 最终 HEAD | 待本次 commit |

## 10. 未完成项

无。所有 Findings 已修复并验收通过。
