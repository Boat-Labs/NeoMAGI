# 0036-evolution-consistency-db-as-ssot-and-soulmd-as-projection

- Status: accepted
- Date: 2026-02-24

## 选了什么
- 将 Evolution 一致性语义固定为：`soul_versions`（DB）是唯一 SSOT，`workspace/SOUL.md` 是运行时投影文件。
- `apply()` / `rollback()` 实施补偿语义：
  - 先读取并保留旧文件内容；
  - 写新文件后若 `commit` 失败，立即尝试回写旧内容并记录告警日志；
  - 任一补偿失败必须抛错并输出结构化日志，禁止静默成功。
- 启动阶段增加对账修复：
  - 若 DB active version 与 `SOUL.md` 不一致，以 DB 为准重写 `SOUL.md`；
  - 记录 `soul_projection_reconciled` 审计日志。
- 明确 `ensure_bootstrap()` 只处理“DB 无 active 且文件存在”的冷启动导入，不承担漂移修复职责。

## 为什么
- Evolution 的状态机、审计链路、回滚目标都在 DB，控制面天然应以 DB 为准。
- 仅把文件写入提前到 `commit` 前，仍会留下“文件领先 DB”的失败窗口；需要补偿与启动对账闭环。
- 统一 SSOT 后，错误语义可预测，回归测试可稳定覆盖。

## 放弃了什么
- 方案 A：以 `SOUL.md` 文件为 SSOT，DB 作为缓存。
  - 放弃原因：无法提供可靠的版本状态机与审计链路，回滚和并发语义复杂。
- 方案 B：保持当前弱一致性，仅调整写入顺序，不做补偿与对账。
  - 放弃原因：仍存在漂移窗口，故障后恢复依赖人工介入。
- 方案 C：双主（DB 与文件任一可写即视为成功）。
  - 放弃原因：一致性定义不闭合，冲突不可判定。

## 影响
- `src/memory/evolution.py` 需增加 commit 失败补偿与日志。
- 启动流程需增加 SOUL 投影对账步骤（网关生命周期）。
- 测试需新增三类用例：
  - `commit` 失败触发回写补偿；
  - 对账修复可将文件恢复到 DB active 内容；
  - `ensure_bootstrap()` 不处理已有 active 的漂移场景。
