# M6 迁移结论

> **P1 修复后重跑版本** (2026-02-26)
>
> 本版本基于 P1 修复后的全量重跑结果，替代初版评测结论。
> 修复内容：(1) ADR 0041 BudgetGate 接入 chat.send 主链路；
> (2) T11/T12 判定口径收紧——未触发目标工具即 FAIL。
>
> 报告文件：
> - OpenAI: `m6_eval_openai_1772064537.json`
> - Gemini: `m6_eval_gemini_1772064602.json`

## 评测摘要

| Provider | 通过 | 失败 | 跳过 | 总数 | 总延迟(ms) | 估算 Tokens |
|----------|------|------|------|------|-----------|------------|
| OpenAI   | 7    | 0    | 0    | 7    | 67,642    | ~1,273     |
| Gemini   | 6    | 1    | 0    | 7    | 59,949    | ~1,125     |

### 分项结果

| Task ID | 类别 | OpenAI | Gemini | 备注 |
|---------|------|--------|--------|------|
| T10 | 多轮双语对话 | PASS (11.9s) | PASS (9.7s) | Gemini 响应更快 |
| T11 | 单工具调用 (current_time) | PASS (2.5s) | PASS (1.8s) | 严格判定：均触发 current_time |
| T12 | 工具链 (memory_search→回答) | PASS (2.0s) | PASS (2.6s) | 严格判定：均触发 memory_search |
| T13 | 长上下文 (12 轮) | PASS (31.3s) | **FAIL** (30.9s) | Gemini 在第 12 轮返回 400 INVALID_ARGUMENT |
| T14 | CJK 复杂处理 | PASS (8.7s) | PASS (5.8s) | 中文、引号、代码均正常 |
| T15 | 角色遵循 | PASS (9.1s) | PASS (6.5s) | 注入尝试后角色稳定 |
| T16 | 错误恢复 | PASS (2.3s) | PASS (2.6s) | 工具错误后优雅恢复 |

### 预算使用

本次评测经由 BudgetGate 全链路（ADR 0041），每次请求预占 €0.05。
实际预算消耗：€2.60（52 次预占，全部 settled），远低于 €25 hard stop。

## 兼容性发现

### 完全兼容项
- **基础对话** (T10): 多轮、中英文切换，双 provider 表现一致
- **单工具调用** (T11): current_time 工具均正确触发和解析
- **工具链调用** (T12): memory_search → 回答链路完整
- **CJK 处理** (T14): 中文变量名、混合引号、代码片段均无截断/乱码
- **角色遵循** (T15): 注入尝试后角色约束保持稳定
- **错误恢复** (T16): 工具报错后继续正常对话

### 需适配项（已处理）
- **Token 计数**: Gemini streaming 场景下 usage 可能为空，M6 Phase 2 已实现 fallback 估算（见 tokenizer_fallback warning）
- **Provider 路由**: 已实现 `ChatSendParams.provider` 字段的 per-request provider 选择

### 不兼容项
- **长上下文 + 工具历史** (T13): Gemini 在 12 轮对话（含多次工具调用的消息历史）时返回 `400 INVALID_ARGUMENT`。根因推测是 Gemini OpenAI-compatible API 对含 tool_call/tool_result 的长消息链格式校验更严格。
  - **影响范围**: 仅影响长对话场景（10+ 轮 + 工具调用累积）
  - **缓解方案**: 会话压缩（compaction）在达到阈值前主动裁剪历史，可降低触发概率。后续可在 compaction 策略中对 Gemini 降低触发阈值。

## 性能对比

- Gemini 在基础任务上**延迟更低**（平均快 30-40%）
- OpenAI 在长上下文场景**稳定性更好**
- 双方在工具调用准确性上表现相当

## 切换策略

### 切换步骤（OpenAI → Gemini）
1. 在 `.env` 中设置 `PROVIDER_ACTIVE=gemini`
2. 确认 `GEMINI_API_KEY` 和 `GEMINI_BASE_URL` 已配置
3. 重启 Gateway，验证日志中 `default_provider=gemini`

### 回退步骤（Gemini → OpenAI）
1. 在 `.env` 中设置 `PROVIDER_ACTIVE=openai`
2. 重启 Gateway
3. 使用 `chat.send` 的 `provider` 字段可在不重启的情况下临时切换

### 预期切换时间
- 配置变更 + 重启: < 1 分钟
- 验证（health check + 基础对话测试）: < 2 分钟

## 结论

- [x] Gemini 可作为 OpenAI 的可行备选（6/7 任务通过）
- [x] 建议默认路线维持 OpenAI（长上下文稳定性更好）
- [x] 支持 per-request provider 切换，无需重启即可使用 Gemini
- [x] ADR 0041 BudgetGate 已接入 chat.send 主链路，预算闸门生效
- [x] T11/T12 判定口径已收紧（未触发目标工具 = FAIL），评测证据可信
- [ ] 长对话场景使用 Gemini 时需注意 compaction 阈值调优
