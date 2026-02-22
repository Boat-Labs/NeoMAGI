# M2 Anti-Drift Baseline Evaluation Record

**ADR**: 0030 — Compaction Preserves Anchors
**Date**: 2026-02-22
**Evaluator**: tester
**Scope**: M2 Session Continuity — anchor preservation during compaction

---

## 1. Evaluation Objective

验证 M2 compaction 实现是否满足 ADR 0030 的反漂移基线要求：

- AGENTS/SOUL/USER 三类锚点在 compaction 后不丢失
- 锚点校验以"最终 prompt 可见性"为口径
- 校验失败时 retry → degrade 降级路径完整
- 产出 Probe 评估结果作为阶段验收证据

---

## 2. Anchor Preservation Mechanism Analysis

### 2.1 Architecture

锚点保护通过以下架构实现：

```
System Prompt (PromptBuilder.build)
├── Layer 1: Identity (hardcoded)
├── Layer 2: Tooling
├── Layer 3: Safety
├── Layer 4: Skills
├── Layer 5: Workspace Context ← AGENTS.md, USER.md, SOUL.md, IDENTITY.md
├── Layer 6: Compacted Context ← rolling summary (会话摘要)
├── Layer 7: Memory Recall
└── Layer 8: DateTime
```

**关键设计**: 锚点文件 (AGENTS/SOUL/USER) 在 Layer 5 独立注入，compacted_context 在 Layer 6 单独注入。两者互不覆盖，锚点不进入可压缩区间。

### 2.2 Compaction Scope

`CompactionEngine.compact()` 仅压缩 **message history** 中的完成 turn。System prompt（包含锚点）不在压缩范围内。

- 压缩输入: `compressible_turns` (message history 中已完成、非当前、非保留的 turn)
- 压缩输出: `compacted_context` (rolling summary JSON)
- 锚点来源: `system_prompt` (每次 LLM 调用前由 PromptBuilder 重新组装)

### 2.3 Anchor Validation Implementation

`_validate_anchors(system_prompt, compacted_context, effective_history_text)` — compaction.py:371-397

当前实现为锚点短语匹配校验：从 workspace 锚点文件（AGENTS.md, SOUL.md, USER.md）提取首个非空行作为 probe phrase，验证每个 phrase 在最终 model context（system_prompt + compacted_context + preserved_text）中可见。

**评估**: 这是 ADR 0030 要求的"锚点可见性校验"的完整实现，覆盖了锚点内容级匹配（非仅长度检查）。若任一锚点短语缺失，触发 retry → degrade 降级路径。

---

## 3. Probe Set & Evaluation Results

### 3.1 Probe Design

基于 ADR 0030 要求，设计以下 Probe 场景：

| Probe ID | 场景 | 验证目标 |
|----------|------|----------|
| P1 | 正常 compaction (30+ turns) | 锚点在 prompt 中保持可见 |
| P2 | Rolling compaction (second pass) | 多次 compaction 后锚点不丢失 |
| P3 | Degraded compaction (LLM timeout) | 降级路径锚点完整 |
| P4 | Emergency trim | 紧急截断后锚点保持 |
| P5 | Compaction with pre-existing state | 从已有 compaction 状态恢复后锚点完整 |
| P6 | No compaction (backward compat) | 无 compaction 设置时锚点行为不变 |

### 3.2 Evaluation Results

| Probe | Method | Result | Evidence |
|-------|--------|--------|----------|
| **P1** | `test_full_pipeline_30_turns` + code review | **PASS** | `anchor_validation_passed=True` in metadata; system_prompt 由 PromptBuilder 独立组装，不受 compaction 影响 |
| **P2** | `test_second_compaction_advances_watermark` + code review | **PASS** | Rolling compaction 更新 watermark 和 compacted_context，但 system_prompt 每次重新 build (agent.py:193) |
| **P3** | `test_llm_timeout_produces_degraded` + `test_degraded_compaction_still_advances_watermark` | **PASS** | Degraded status 仍生成 metadata with `anchor_validation_passed`; system_prompt 不受影响 |
| **P4** | `test_emergency_trim_metadata_fields` + code review | **PASS** | Emergency trim 设 `compacted_context=None` 和 `anchor_validation_passed=True`; system_prompt 在下一次 LLM 调用前重新 build |
| **P5** | `test_prompt_builder_receives_compacted_context` + `test_effective_history_uses_watermark` | **PASS** | 已有 compaction state 加载后，PromptBuilder 接收 compacted_context 参数，workspace 锚点独立注入 |
| **P6** | `test_backward_compat_without_settings` | **PASS** | 无 compaction 设置时 PromptBuilder 不注入 Layer 6，锚点行为与 M1 一致 |

### 3.3 Hit Rate Summary

| 指标 | 结果 |
|------|------|
| Probe 总数 | 6 |
| 通过 | 6 |
| 失败 | 0 |
| **命中率** | **100%** |

### 3.4 丢失项分析

**无丢失项**。锚点通过架构隔离保护：

1. 锚点文件 (AGENTS/SOUL/USER) 在 `PromptBuilder._layer_workspace()` 中读取
2. Compaction 仅操作 message history，不触及 system prompt 层
3. 每次 LLM 调用前 system prompt 重新组装，确保最新锚点内容

### 3.5 失败样例

**无失败样例**。所有 6 个 Probe 场景均通过。

---

## 4. Retry & Degradation Path Evaluation

### 4.1 Retry Path

Code: `compaction.py:247-266`

```
_validate_anchors fails
  → anchor_retry_enabled=True
    → retry _generate_summary (same params)
    → _validate_anchors again
      → pass: status="success"
      → fail: status="degraded", anchor_retry_used=True
```

**评估**: retry 路径存在且测试覆盖。当前实现中 retry 使用相同参数重新生成 summary，未修改 prompt 以强化锚点保护。这是可接受的最小实现。

### 4.2 Degradation Path

| 触发条件 | 行为 | 测试覆盖 |
|----------|------|----------|
| Anchor validation fails after retry | `status="degraded"`, watermark advances | `test_degraded_on_llm_timeout` |
| LLM timeout | `status="degraded"`, no summary | `test_llm_timeout_produces_degraded` |
| Total failure | Emergency trim, `compacted_context=None` | `test_compaction_exception_triggers_emergency_trim` |
| DB store failure | Returns None, session continues | `test_fail_open_on_all_paths` |

**评估**: 所有降级路径都保证 session 继续运行 (fail-open)，且锚点不受影响（因为锚点在 system_prompt 层独立维护）。

---

## 5. Known Limitations

1. **`_validate_anchors` 已升级为锚点短语匹配**: 从 AGENTS/SOUL/USER 文件提取首行作为 probe phrase，在最终 context 中验证可见性。当前覆盖 M2 反漂移需求，后续 milestone 可按需扩展 probe 粒度。

2. **Probe 为代码级验证**: 当前 Probe 通过单元测试和代码审查执行，未使用真实 LLM 调用。真实 LLM 返回的 summary 质量不在本次评估范围内（属于 M3 阶段质量评估）。

3. **无自动化评估框架**: 按 ADR 0030 决策，M2 不建设评估平台或 CI 级自动化框架，相关工作推迟到 M2.x/M3。

---

## 6. Conclusion

M2 反漂移基线验收 **PASS**。

- 锚点通过架构隔离保护（PromptBuilder 层独立注入，不进入压缩范围）
- 锚点校验已实现短语级匹配（从 AGENTS/SOUL/USER 提取 probe phrase，验证 final context 可见性）
- Retry → degrade 降级路径完整且测试覆盖
- 所有 6 个 Probe 场景通过，命中率 100%
- 无丢失项，无失败样例
- Probe 数量降阶决策见 ADR 0033：M2 基线 6 Probe，20 Probe 推迟至 M3

本评估记录满足 ADR 0030 要求的"离线评估记录（命中率、丢失项、失败样例）"产出。
