# 0029-token-counting-strategy-tiktoken-first-fallback-estimate

- Status: accepted
- Date: 2026-02-21

## 选了什么
- M2 的 token 计数策略采用 `tiktoken` 作为默认精确计数实现。
- 当模型无法映射到可用 tokenizer（如非 OpenAI 或未知模型）时，回退为 `chars/4` 估算。
- 统一通过单一计数接口对外提供结果，并显式标记计数模式（`exact` / `estimate`）。
- OpenAI API 返回的 `usage.prompt_tokens` 在 M2 仅用于观测与监控，不参与当轮预算决策回写。

## 为什么
- 设计约束要求“优先精确 tokenizer，缺少时估算”，`tiktoken` 与该约束直接一致。
- 相比仅用 `chars/4`，`tiktoken` 对中英文混合和结构化消息计数更稳定，预算偏差更小。
- 相比“tiktoken + 事后 usage 校正”，M2 先保持实现路径简洁，避免引入额外状态同步和回写复杂度。
- 在 M2 阶段优先保证预算判断稳定性与实现可维护性，符合“最小可用闭环”。

## 放弃了什么
- 方案 A：统一仅使用 `chars/4` 估算。
  - 放弃原因：估算误差较大，中文场景偏差更明显，容易导致 compaction/截断时机不稳定。
- 方案 B：`tiktoken` + `usage.prompt_tokens` 事后校正闭环。
  - 放弃原因：实现复杂度高，且校正天然滞后，无法改善当轮请求前的预算决策。
  - 处理方式：后续版本可在观测数据充分后评估是否引入离线校准机制。

## 影响
- 新增 `tiktoken` 依赖，并需要维护模型到 tokenizer 的映射与 fallback 逻辑。
- Token 预算决策将更稳定，降低误触发 compaction 或过早裁剪的风险。
- 非 OpenAI 模型仍可运行，但计数精度取决于估算策略。
- 需增加观测指标：估算占比、估算误差（若可对照 usage）、触发阈值命中率。
