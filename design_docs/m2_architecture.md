# M2 Architecture（计划）

> 状态：planned  
> 对应里程碑：M2 会话内连续性  
> 依据：`design_docs/roadmap_milestones_v3.md`、现有 Session/Agent 实现、既有记忆规则讨论

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

## 3. 目标架构（高层）
- 在 `PromptBuilder + AgentLoop` 之间引入会话上下文预算控制。
- 当会话接近窗口上限时，触发“先保留关键信息，再压缩上下文”的流程。
- 压缩后的会话仍保留核心任务约束、角色约束和用户偏好。

## 4. 边界
- In:
  - 会话内连续性治理（context window 内问题）。
  - 压缩前后任务语义与角色一致性保障。
- Out:
  - 不处理跨天/跨会话持久记忆召回（属于 M3）。

## 5. 验收对齐（来自 roadmap）
- 长轮次任务下，关键约束持续有效。
- 会话压缩后继续提问，任务可连续推进，无明显语义断层。
