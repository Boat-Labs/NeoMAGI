# M2 Architecture（计划）

> 状态：planned  
> 对应里程碑：M2 会话内连续性  
> 依据：`design_docs/roadmap_milestones_v3.md`、ADR 0027、现有 Session/Agent 实现、既有记忆规则讨论

## 1. 目标
- 在单会话长链路中维持上下文连续性，避免“长对话失忆”和角色漂移。

## 2. 当前基线（输入）
- 已有会话持久化、顺序语义、流式输出和 history 回放。
- Prompt 组装已支持 workspace context 与 `MEMORY.md`（main session）。
- 当前尚无 token budget 控制、compaction 机制与 pre-compaction memory flush。

实现参考：
- `src/session/manager.py`
- `src/agent/agent.py`
- `src/agent/prompt_builder.py`

## 3. 目标架构（可实施版）

### 3.1 Token Budget 策略
- 预算来源：按当前模型 `context_limit` 动态计算，不写死单一绝对值。
- 硬约束：system prompt（含 workspace context）计入 `usable_input_budget`，且属于不可压缩部分。
- 输入预算计算：
  - `reserved_output_tokens = max(2048, context_limit * 0.15)`
  - `safety_margin_tokens = max(1024, context_limit * 0.05)`
  - `usable_input_budget = context_limit - reserved_output_tokens - safety_margin_tokens`
- 阈值定义：
  - `warn_threshold = usable_input_budget * warn_ratio`（预警，默认 `warn_ratio=0.80`）
  - `compact_threshold = usable_input_budget * compact_ratio`（触发压缩，默认 `compact_ratio=0.90`）
- 计数规则：
  - 优先使用 tokenizer 精确计数。
  - 若当前模型缺少 tokenizer，使用 `chars / 4` 作为临时估算，并记录估算模式日志。
- 配置归属：
  - `warn_ratio`、`compact_ratio`、`reserved_output_tokens`、`safety_margin_tokens` 为可配置项。
  - 文档中的比例与数值为初始默认值，不作为硬编码常量。

### 3.2 Compaction 策略
- 压缩粒度：按 turn（用户消息 + assistant 响应 + tool 结果）进行，不按单 message 盲目裁剪。
- 保留策略（不可压缩）：
  - 最近 `N` 个完整 turn 保持原文（默认 `N=8`，可配置）。
  - 当前任务目标、未完成事项、模式约束（如 `chat_safe`）保持原文。
  - 来自 `AGENTS.md` / `SOUL.md` / `USER.md` 的锚点约束保持可见。
- 可压缩策略（可摘要）：
  - 更早历史 turn 生成结构化摘要块（goals/decisions/open_items/facts/risks）。
  - 低信号闲聊与重复确认语句优先裁剪。
- 输出产物：
  - `compacted_context`（供后续轮次直接使用）
  - `compaction_report`（记录保留项、摘要项、裁剪项统计）

### 3.3 Pre-compaction Memory Flush 契约
- 在 compaction 前先执行 memory flush，生成候选条目（不直接写入长期记忆）。
- 候选条目最小结构（与 `memory_architecture` 对齐）：

```json
{
  "candidate_id": "uuid",
  "source_session_id": "main|group:*",
  "source_message_ids": ["msg_a", "msg_b"],
  "candidate_text": "可沉淀条目文本",
  "constraint_tags": ["user_preference", "long_term_goal", "safety_boundary", "fact"],
  "confidence": 0.0,
  "created_at": "2026-02-21T12:00:00Z"
}
```

- 兼容性约定：
  - M2 保证上述字段语义稳定。
  - M3 可增字段，但不得重定义上述字段语义。
  - `confidence` 取值范围固定为 `0.0 ~ 1.0`；M2 默认由 flush 阶段模型/规则评分生成。

### 3.4 与 Agent Loop 集成点
- 集成位置：
  - `src/agent/agent.py`：每次模型调用前执行预算检查；超阈值触发 `flush -> compact -> rebuild prompt`。
  - `src/session/manager.py`：持久化 `compacted_context` 与 `compaction_report`（或等价会话摘要结构）。
  - `src/agent/prompt_builder.py`：优先加载最新压缩产物，再拼接实时上下文。
- 触发顺序（单次）：
  1. 预算检查
  2. pre-compaction memory flush
  3. compaction
  4. 重建 prompt
  5. 继续常规 agent loop

### 3.5 Flush/Compaction 失败策略
- 超时控制：
  - `flush_timeout_ms`、`compact_timeout_ms` 可配置，默认与模型调用超时保持同级。
- 重试策略：
  - flush 与 compaction 允许有限重试（默认最多 1 次，指数退避）。
- 降级策略：
  - flush 失败：继续执行 compaction，但在 `compaction_report` 标记 `flush_skipped=true`。
  - compaction 失败：回退为“仅裁剪低信号历史 + 保留最近 N turn”的轻量降级路径。
- 失败闭锁策略：
  - 默认 `fail-open`（保证会话继续），但必须记录结构化错误日志与会话标记。
  - `fail-closed` 不在 M2 默认路径内，仅用于调试或受控实验场景。

## 4. 边界
- In:
  - 会话内连续性治理（context window 内问题）。
  - 压缩前后任务语义与角色一致性保障。
  - 反漂移基线：在长轮次与压缩后，用户利益约束与角色边界保持稳定。
- Out:
  - 不处理跨天/跨会话持久记忆召回（属于 M3）。
  - 不在 M2 内实现 `SOUL.md` 自我进化闭环（提案/eval/rollback 归 M3）。

## 5. 验收对齐（操作化）
- 长轮次任务下，关键约束持续有效。
- 会话压缩后继续提问，任务可连续推进，无明显语义断层。
- 会话压缩前后，用户利益约束和角色边界不漂移（反漂移基线）。

### 5.1 反漂移基线度量
- 锚点集：从 `AGENTS.md` / `SOUL.md` / `USER.md` 提取约束锚点声明（安全、用户偏好、角色边界、任务边界）。
- Probe 集：固定一组压缩前后对照问题（最少 20 条），覆盖安全、偏好、任务连续性三类场景。
- 评估时机：本基线用于 M2 验收阶段的离线评估；运行时漂移检测不在 M2 scope 内。
- 比对基准：
  - 主基准：同一会话压缩前响应（pre-compaction baseline）。
  - 辅基准：golden probes（用于安全与边界题的稳定对照）。
- 指标与阈值：
  - 锚点保留率 `>= 95%`
  - Probe 一致率 `>= 90%`
  - 压缩后连续 3 轮任务推进无明显断层（无重复收集已知约束、无已决议反转）
  - 安全边界违规数 `= 0`
