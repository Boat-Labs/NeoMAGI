# M6 Architecture（计划）

> 状态：planned  
> 对应里程碑：M6 模型迁移验证  
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0002/0016、当前 model client 实现

## 1. 目标
- 维持 OpenAI 默认路径，同时形成 Gemini 可验证迁移与可回退策略。

## 2. 当前基线（输入）
- 已有统一 `ModelClient` 抽象与 `OpenAICompatModelClient` 实现。
- 当前配置侧以 OpenAI 配置为主，`base_url` 可用于 OpenAI-compatible 接入。

实现参考：
- `src/agent/model_client.py`
- `src/config/settings.py`
- `src/gateway/app.py`

## 3. 目标架构（高层）
- 保持统一模型调用接口，不在业务层分叉 provider 逻辑。
- 在同一调用协议下完成 OpenAI 与 Gemini 的等价任务验证。
- 形成清晰切换与回退路径，避免迁移过程破坏可用性。

## 4. 边界
- In:
  - OpenAI 默认 + Gemini 验证。
  - 可执行的迁移结论与回退策略。
- Out:
  - 不扩展到过多 provider 并行治理。
  - Anthropic 不纳入 v1 主兼容范围。

## 5. 验收对齐（来自 roadmap）
- 同一批代表性任务在 OpenAI 与 Gemini 上均可完成。
- 切换后若质量或稳定性下降，可快速回退到默认路径。
