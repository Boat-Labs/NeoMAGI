# 0038-m6-primary-gemini-validation-model-and-budget-guardrail

- Status: accepted
- Date: 2026-02-24

## 选了什么
- M6 模型迁移验证阶段，Gemini 主验证模型固定为 `gemini-2.5-flash`（标准文本路径）。
- M6 验证预算设置硬上限：总 API 成本不超过 `€30`（目标尽量低于该上限）。
- 为控制预算与风险，采用两层执行策略：
  - Layer 1（可选）：`gemini-2.5-flash-lite` 做 smoke/冒烟；
  - Layer 2（必选）：`gemini-2.5-flash` 做代表性任务正式评测并产出迁移结论。
- 本 ADR 不改变既有默认生产路径：OpenAI 仍是默认运行路线（与 ADR 0002 一致）。

## 为什么
- `gemini-2.5-flash` 在能力与成本之间更均衡，适合 M6 “够用且省钱”的迁移验证目标。
- 相比 `gemini-2.5-pro`，`2.5-flash` 成本显著更低，更容易在 `€30` 预算内完成可复现验证。
- 相比 `gemini-2.5-flash-lite`，`2.5-flash` 在复杂任务上的稳健性更高，能降低“过度省钱导致结论失真”的风险。
- 使用稳定模型名（非 preview）可降低版本漂移与退役风险，符合里程碑验收可追溯性要求。

## 放弃了什么
- 方案 A：M6 主验证直接使用 `gemini-2.5-pro`。
  - 放弃原因：成本过高，预算压力大，不符合“开发成本尽量低”的约束。
- 方案 B：M6 仅使用 `gemini-2.5-flash-lite`。
  - 放弃原因：虽更便宜，但复杂任务验证的代表性与稳定性不足，存在误判迁移可行性的风险。
- 方案 C：使用 preview 或 latest 别名作为主验证模型（如 `gemini-flash-latest`）。
  - 放弃原因：版本热切换会降低评测可重复性与结果可追溯性。

## 影响
- M6 计划与报告需显式记录：
  - 使用的 Gemini 模型 ID（固定为 `gemini-2.5-flash`）；
  - 成本统计口径与累计费用；
  - 预算闸门（达到阈值时停止扩跑或降级到 smoke）。
- 评测执行需优先避免非必要增项（如 grounding/search、超大上下文、无界输出），防止预算失控。
- 若后续需要改主验证模型或调整预算上限，应新增决策或将本决策标记为 superseded。
