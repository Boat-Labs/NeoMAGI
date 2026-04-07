---
doc_id: 019cc914-bf10-78d8-92e6-2767abf72556
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-07T17:15:06+01:00
---
# Procedure Runtime

> 状态：approved
> 日期：2026-03-07
> 依据：`design_docs/system_prompt.md`、`design_docs/modules.md`、`design_docs/phase1/m1_5_architecture.md`、`design_docs/phase1/roadmap_milestones_v3.md`、ADR 0024、ADR 0042、ADR 0043、ADR 0048、ADR 0059

## 1. 为什么需要这份设计

NeoMAGI 当前已经有两类运行时约束：
- prompt 侧的行为契约与上下文分层；
- tool 侧的 mode gate / execution gate。

这两层已经足以约束“单步原子动作”，但对“高约束、多步骤、带状态推进”的流程仍有一个空档：
- 只靠 prompt / skill 约束，命中不稳定，且正确性主要依赖模型记忆与遵守；
- 只靠底层原子工具，模型仍可能自由拼装顺序，导致状态机语义漂移；
- 直接上通用 workflow engine（如 n8n / DAG JSON）会把表达力引入过多，超出当前问题所需。

当前缺的是一层很薄的 runtime control object：
- 它不替代 tool；
- 它不等于 prompt；
- 它只负责把“不能错的那一小核”下沉到代码层。

本草案把这层命名为 `Procedure Runtime`。

## 2. 设计目标

- 为少量高约束流程提供 deterministic runtime contract。
- 保留现实世界中的裁量空间，不把完整 SOP 全部写死。
- 保持渐进式披露：模型只看到当前流程摘要或当前 state 的局部信息。
- 与现有 Tool Registry / PromptBuilder / AgentLoop 对齐，不引入新的重型编排系统。
- 贯彻“对抗熵增”：能推导的不存，能复用的不重定义，能写代码的不发明 DSL。

## 3. 非目标

- 不是通用 workflow engine。
- 不引入 DAG、节点图、表达式语言、并行编排、重试语义、可视化编辑器。
- 不把 `Procedure` 变成另一套 prompt 技巧。
- 不要求所有工具调用都进入 `Procedure`。
- 不在本阶段处理组织级 actor auth、RBAC 或复杂审批中心。

## 4. 问题判断

### 4.1 只靠 skills / prompt 不够

`skills` 的优点是：
- metadata 可短摘要注入；
- 命中后再展开正文，具备渐进式披露特性。

但它的局限也很明确：
- 入口依赖语义匹配，触发不稳定；
- 内容是文本，最终仍由模型自己“照着做”；
- 对高约束流程，正确性仍主要存在模型脑子里，而不是系统代码里。

因此，`skills` 更适合作为行为说明或路由提示，不适合作为 deterministic control plane。

### 4.2 只靠 wrapper script 也不够

wrapper script 很适合把多个底层命令收敛成一个原子业务动作，但它不能单独解决：
- 当前处于哪个流程阶段；
- 当前允许哪些动作；
- 哪些前置条件尚未满足；
- 跨 turn / 跨副作用的状态推进如何保存。

因此，wrapper script 是 `Procedure` 的组成部分，不是替代品。

### 4.3 直接采用 n8n / workflow JSON 过重

通用 workflow JSON 的问题不在“不能表达”，而在“表达得太多”：
- 节点图、条件分支、表达式、并发、重试、触发器等语义远超当前需要；
- 如果不运行其 runtime，就要自己实现一个解释器子集；
- 如果运行其 runtime，就会把 NeoMAGI 核心控制面绑到外部编排系统。

当前问题缺的是约束力，不是表达力。因此不应把通用 workflow JSON 作为 NeoMAGI 内部 canonical runtime protocol。

## 5. 核心决策

- 引入 `Procedure` 作为 NeoMAGI runtime 中与 prompt 并列的一类结构化控制上下文。
- `Procedure` 只约束硬边界与状态迁移，不承载完整 SOP 文本。
- prompt 只展示 `Procedure` 的投影视图；真正的 source of truth 在 runtime object。
- deterministic 正确性依赖 `validator + executor + transition`，不依赖 prompt。
- 只在“多步、高约束、可能跨 turn 或带明显状态推进”的流程中使用 `Procedure`。

一句话定义：

`Procedure` 是一个命名的、可进入的、有限状态的执行协议；模型只看当前窗口，系统负责顺序、校验和副作用落地。

## 6. 设计原则

### 6.1 只把不能错的部分写死

`Procedure` 应只约束以下四类内容：
- 安全边界；
- 不可逆副作用；
- 外部契约；
- 关键状态迁移。

不应试图规定完整执行路径。

### 6.2 Checkpoint，不是 choreography

`Procedure` 的状态机应描述：
- 当前处于哪个 checkpoint；
- 进入下一个 checkpoint 需要满足哪些 invariant；
- 哪些动作在当前 state 合法。

它不应描述“每一步必须按唯一顺序完成”的完整编舞。

### 6.3 Deterministic core, discretionary shell

系统需要明确区分：
- `hard guard`：违反就 deny；
- `soft policy`：违反只 warning；
- `free space`：系统不管，由 agent 自主探索。

这能避免流程过死而频繁卡住。

### 6.4 能推导的不存

以下信息不应持久化到 runtime object：
- `allowed_tools`
- `missing_inputs`
- `next_actions`
- prompt 文本

这些都应由 `spec + state + context + mode` 在每轮运行时推导。

### 6.5 不发明 DSL

`ProcedureSpec` 只做静态声明与代码索引：
- `context_model` 应引用代码里的 Pydantic model registry key；
- `guard` 应引用 procedure guard registry 中的校验器名字；
- `tool` 应引用现有 Tool Registry 中的工具；
- 副作用仍落到脚本 / tool executor。
- procedure guard 与现有 workspace safety guardrail 分层，不共用同一职责面：前者负责流程 invariant，后者负责 mode / risk safety。

不引入表达式语言、模板引擎或工作流脚本 DSL。

## 7. 抽象边界

### 7.1 Tool

`Tool` 负责“能做什么”：
- 原子能力；
- 参数 schema；
- 真正执行副作用；
- mode / risk gate。

### 7.2 Procedure

`Procedure` 负责“什么时候能做、按什么边界做”：
- 当前在哪个流程；
- 当前 state 合法动作集合；
- 当前硬前置条件；
- 当前状态迁移目标。

### 7.3 Prompt View

prompt 只负责把 runtime 状态翻译给模型：
- 当前处于哪个 procedure / state；
- 当前缺哪些硬输入；
- 当前允许的动作是什么。

prompt 不是 source of truth。

### 7.4 Skill Object

根据 ADR 0048 的 `2+1` 结构：
- skill object 负责经验偏置、建议和 escalation signal；
- procedure 负责 deterministic state / guard / transition；
- skill 可以建议“何时进入某个 procedure”，但不替代 procedure 的硬边界。

## 8. 何时使用 Procedure

满足以下至少两条时，考虑引入 `Procedure`：
- 多步；
- 有顺序依赖；
- 跨 turn；
- 带外部副作用；
- 有 gate / approval / recovery 语义；
- 失败后恢复路径不明显。

否则应优先退回：
- 单步确定动作：typed tool；
- 多命令但仍原子：wrapper tool。

## 9. ProcedureSpec（V1 草案）

`ProcedureSpec` 只定义静态协议，不定义运行时实例。

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ActionSpec(BaseModel):
    """Single allowed action from one procedure state."""

    model_config = ConfigDict(frozen=True)

    tool: str
    to: str
    guard: str | None = None


class StateSpec(BaseModel):
    """Action map for one procedure state."""

    model_config = ConfigDict(frozen=True)

    actions: dict[str, ActionSpec]


class ProcedureSpec(BaseModel):
    """Static contract for a named procedure."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: int
    summary: str
    entry_policy: Literal["explicit"]
    allowed_modes: frozenset[ToolMode]
    context_model: str
    initial_state: str
    states: dict[str, StateSpec]
    enter_guard: str | None = None
    soft_policies: tuple[str, ...] = ()
```

V1 约定 `ProcedureSpec` 在加载时至少完成以下静态校验：
- `initial_state in states`
- 所有 `ActionSpec.to in states`
- `context_model` 可在 `ProcedureContextRegistry` 解析
- `enter_guard` / `action.guard` 可在 `ProcedureGuardRegistry` 解析
- `tool` 可在现有 `ToolRegistry` 解析

### 9.1 字段说明

- `id`
  - 稳定标识符。
- `version`
  - 防止 spec 漂移后旧实例与新规则错配。
- `summary`
  - 给 prompt/view 使用的一句话摘要。
- `entry_policy`
  - V1 固定为 `explicit`；自动进入 / trigger-based entry 留到后续版本。
- `allowed_modes`
  - 复用现有 mode gate；V1 只做 procedure 级 gating，不做 state 级 mode gating。
- `context_model`
  - 指向 `ProcedureContextRegistry` 中已注册的 Pydantic `BaseModel` 名字，给 runtime `context` 提供强类型边界。
- `initial_state`
  - 进入后的起始状态。
- `states`
  - 唯一流程骨架；`initial_state` 与所有 `to` 目标都必须能解析到其中某个 state。
- `ActionSpec.to`
  - 目标 state。V1 允许 `to == current_state` 的自环，用于 heartbeat、幂等更新或补充上下文。
- `enter_guard`
  - 进入流程前的硬校验。
- `soft_policies`
  - 推荐路径、提醒、最佳实践，只做 warning。

### 9.2 V1 明确不包含

- `visible_tools`
  - 可由 `actions[].tool` 推导。
- `final_states`
  - 无 action 的 state 自然是终态。
- prompt 模板
- shell 命令
- DAG / graph / edge metadata
- state-level mode gate
- retry / timeout / parallelism
- 表达式 DSL

### 9.3 V1 最小 registry / executor 契约

为保证 spec 在代码侧可闭合，V1 明确以下最小接口：

```python
import inspect
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    code: str = ""
    detail: str = ""


ProcedureEnterGuard = Callable[
    [dict[str, Any], ToolContext | None],
    GuardDecision | Awaitable[GuardDecision],
]

ProcedureActionGuard = Callable[
    [ActiveProcedure, dict[str, Any], ToolContext | None],
    GuardDecision | Awaitable[GuardDecision],
]


class ToolResult(BaseModel):
    ok: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    context_patch: dict[str, Any] = Field(default_factory=dict)
```

- `ProcedureContextRegistry` 负责把 `context_model: str` 解析到具体的 Pydantic model class。
- `ProcedureGuardRegistry` 负责把 `enter_guard` / `action.guard` 解析到具体 guard callable。
- V1 保持现有 `BaseTool.execute(...) -> dict` 契约不变；`ProcedureRuntime / ProcedureExecutor` 负责把 `dict | ToolResult` 归一化为 `ToolResult`。
- 若 tool 返回裸 `dict`，executor 约定提取其中的 `context_patch` 键并映射到 `ToolResult.context_patch`；非 procedure 场景仍继续消费裸 `dict`，不强制升级到 `ToolResult`。
- procedure guard 与现有 workspace safety guardrail 分层：先过 mode / risk gate，再跑 procedure guard。
- V1 允许 sync / async 两种 procedure guard，但 executor 必须用 `inspect.isawaitable()` 统一归一化；新 guard 默认推荐 `async def`，避免遗漏 `await`。
- 当前 `ToolContext` 只包含 `scope_key/session_id`；若后续需要 role-aware guard、principal-aware guard 或 Shared Companion 的 shared-space guard，应扩展 execution context 或单独解析 actor / principal / shared_space 来源。
- `ToolResult.context_patch` 是唯一允许写回 `ActiveProcedure.context` 的 patch 面。

## 10. ActiveProcedure（V1 最小运行时对象）

```python
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ActiveProcedure(BaseModel):
    model_config = ConfigDict(frozen=True)

    instance_id: str
    session_id: str
    spec_id: str
    spec_version: int
    state: str
    context: dict[str, Any] = Field(default_factory=dict)
    revision: int
```

其中 `context` 的外层只保持 `dict[str, Any]`；真正的 schema 边界由 `ProcedureSpec.context_model` 指向的 Pydantic model 提供。

```json
{
  "instance_id": "proc_01JX...",
  "session_id": "sess_01JX...",
  "spec_id": "devcoord.pm.gate",
  "spec_version": 1,
  "state": "opened",
  "context": {
    "gate_id": "m5-g1",
    "target_commit": "abc123",
    "allowed_role": "tester",
    "report_path": null,
    "report_commit": null
  },
  "revision": 3
}
```

### 10.1 最小字段

- `instance_id`
  - 本次流程实例唯一 ID，用于幂等、审计、事件关联。
- `session_id`
  - 该实例归属的 session。V1 的 procedure 生命周期以 session 为边界。
- `spec_id`
  - 绑定哪份 `ProcedureSpec`。
- `spec_version`
  - 绑定 spec 版本。
- `state`
  - 当前状态，是流程推进的唯一真相源。
- `context`
  - 结构化上下文，是 validator / executor 读写的数据面。
- `revision`
  - 递增版本号，用于 compare-and-swap。

V1 将 `ActiveProcedure` 视为 session-scoped runtime record。
持久化后端可以是 PostgreSQL 独立表或等价 store，但语义上必须能 enforce：
- 同一 session 至多一个非终态 `ActiveProcedure`
- `revision` 的 optimistic compare-and-swap

### 10.2 不变量

- `spec_id/spec_version` 创建后不可变。
- 同一 session 最多一个非终态 `ActiveProcedure`。
- `state` 只能经 transition function 修改。
- `context` 只能由 `ToolResult.context_patch` 产生 patch 后、经 schema 校验再落库。
- 每次成功迁移必须 `revision + 1`。

## 11. 运行时语义

### 11.1 进入流程

`enter_procedure(session_id, spec_id, initial_context)` 负责：
- 检查该 session 是否已有非终态 `ActiveProcedure`；若有则返回 conflict / deny；
- 读取 spec；
- 解析 `context_model` 与 `enter_guard` registry key；
- 跑 `enter_guard`；
- 校验 `initial_context` 是否符合 `context_model`；
- 创建 `ActiveProcedure` 实例。

### 11.2 暴露给模型的工具

工具可见性由两层共同决定：
- ambient tools：当前 mode 下允许的通用工具；V1 不因 active procedure 额外按 `risk_level` 收紧这部分集合；
- procedure tools：当前 state 下由 `actions[].tool` 推导出的工具集合。

即：

```text
visible_tools = ambient_tools(mode) U procedure_tools(spec, state)
```

### 11.3 每次 action 的执行顺序

`apply_action(instance_id, action_id, args, expected_revision)` 固定执行：
1. 读取当前实例与对应 spec；
2. 校验 `expected_revision == revision`；
3. 校验当前 state 是否允许该 action；
4. 校验 tool args；
5. 跑 procedure action guard；
6. 调用具体 tool / script executor，并将其返回的 `dict | ToolResult` 归一化为 `ToolResult`；
7. 对 `ToolResult.context_patch` 做 top-level shallow merge；
8. 再次校验 `context_model`；
9. 状态迁移到 `to`；
10. 用 optimistic CAS 写回 `revision + 1`。

V1 patch 语义固定为：
- 只对 top-level keys 做 replace-on-write
- 未出现在 `context_patch` 中的 key 保持原值
- 不做 deep merge

### 11.4 失败与并发语义

V1 明确采用 `fail-stays + optimistic CAS`：
- guard deny：返回结构化 deny，不修改 `state` / `context` / `revision`
- executor 失败或 `ToolResult.ok == False`：停留在原 state，不写入 `context_patch`
- patch 合并后的 `context_model` 校验失败：将其视为 invalid patch，不迁移状态
- CAS 冲突：不产生部分提交，返回 retryable conflict，由调用方重新读取后重试
- `revision` 表示乐观并发边界；具体存储可以是 PostgreSQL `UPDATE ... WHERE revision = ?` 或等价语义

prompt 不参与最终裁决。

## 12. 示例：`devcoord.pm.gate`

### 12.1 示例 spec

为保持示例最小化，下述 spec 只演示 `open -> phase/report 双向汇聚 -> close`。
`ping`、`state_sync` 等不迁移动作被省略；V1 可用自环 action 表达这类 heartbeat / refresh。

```json
{
  "id": "devcoord.pm.gate",
  "version": 1,
  "summary": "Manage gate lifecycle for a pinned backend commit.",
  "entry_policy": "explicit",
  "allowed_modes": ["coding"],
  "context_model": "GateContextV1",
  "initial_state": "draft",
  "enter_guard": "validate_gate_entry",
  "soft_policies": ["warn_if_report_missing_too_long"],
  "states": {
    "draft": {
      "actions": {
        "open_gate": {
          "tool": "coord_open_gate",
          "to": "opened",
          "guard": "validate_open_gate"
        }
      }
    },
    "opened": {
      "actions": {
        "phase_complete": {
          "tool": "coord_phase_complete",
          "to": "phase_done",
          "guard": "validate_phase_complete"
        },
        "attach_report": {
          "tool": "coord_attach_report",
          "to": "report_attached",
          "guard": "validate_report_ref"
        }
      }
    },
    "phase_done": {
      "actions": {
        "attach_report": {
          "tool": "coord_attach_report",
          "to": "ready_to_close",
          "guard": "validate_report_ref"
        }
      }
    },
    "report_attached": {
      "actions": {
        "phase_complete": {
          "tool": "coord_phase_complete",
          "to": "ready_to_close",
          "guard": "validate_phase_complete"
        }
      }
    },
    "ready_to_close": {
      "actions": {
        "close_gate": {
          "tool": "coord_close_gate",
          "to": "closed",
          "guard": "validate_close_gate"
        }
      }
    },
    "closed": {
      "actions": {}
    }
  }
}
```

### 12.2 这个 spec 如何指导 agent

假设当前 runtime object 为：

```json
{
  "instance_id": "proc_01JX...",
  "session_id": "sess_01JX...",
  "spec_id": "devcoord.pm.gate",
  "spec_version": 1,
  "state": "opened",
  "context": {
    "gate_id": "m5-g1",
    "target_commit": "abc123",
    "allowed_role": "tester",
    "report_path": null,
    "report_commit": null
  },
  "revision": 3
}
```

当前 state 为 `opened`，因此：
- 运行时只允许 `phase_complete`、`attach_report` 两个 action；
- `coord_close_gate` 不可见，也不可执行；
- `context.allowed_role == "tester"`，若执行上下文中的 role 不符，`validate_phase_complete` 应 deny；
- `context.report_path == null`，因此即使模型想 close，也无法通过 `validate_close_gate`。

这个示例刻意建模了“先 phase_complete 再 attach_report”与“先 attach_report 再 phase_complete”两条路径在 `ready_to_close` 汇聚。
需要注意：当前 `ToolContext` 只携带 `scope_key/session_id`，并不包含 actor role；这里的 role 校验只是在示意 guard 意图。真正的 actor 来源仍属于 §16 的开放问题。

这里真正生效的是 runtime object 和 guard，不是 prompt 文案。

prompt 只需要看到类似投影：

```text
[Active Procedure]
id: devcoord.pm.gate
state: opened
allowed actions: phase_complete, attach_report
missing hard inputs: report_path, report_commit
```

## 13. 与现有架构的关系

### 13.1 PromptBuilder

`PromptBuilder` 应只注入 `ProcedureView`：
- 当前激活的 procedure 摘要；
- 当前 state；
- 当前缺失硬输入；
- 当前允许动作。

这层是 view，不是 rule engine。

### 13.2 Tool Registry

`ToolRegistry` 继续作为原子能力注册表。

`Procedure` 不重做工具 schema，不替代：
- mode gate；
- risk gate；
- tool executor。

### 13.3 AgentLoop

`AgentLoop` 不应内联完整 procedure state machine。它负责：
- 读取当前 session 是否存在 `ActiveProcedure`；
- 根据 `mode + procedure state` 计算当前工具可见性；
- 将 `enter_procedure` / `apply_action` 委托给专门的 `ProcedureRuntime` / `ProcedureExecutor`。

`ProcedureRuntime` / `ProcedureExecutor` 负责：
- 解析 `ProcedureSpec`、`ProcedureContextRegistry` 与 `ProcedureGuardRegistry`
- 跑 procedure guard
- 调用 tool executor，归一化 `dict | ToolResult` 返回值，并应用 `ToolResult.context_patch`
- 执行 transition 与 CAS 持久化

这样可以避免把流程状态机、guard 组合和并发语义继续堆进 `AgentLoop`。

### 13.4 与 Skill Objects / ADR 0048 的关系

- skill object 提供经验偏置、建议和 escalation signal
- procedure 提供 deterministic runtime 边界
- 二者不互斥：skill 可以帮助命中或建议进入 procedure，procedure 负责最终裁决

### 13.5 与 `P1-M7` devcoord 的关系

本设计与 ADR 0042 / 0043 一致：
- 仍坚持“确定性流程下沉到脚本”；
- 不把状态机全文塞回 prompt；
- 不把 devcoord 扩展成通用 workflow engine。

需要强调：
- `Procedure` 是运行时抽象；
- `scripts/devcoord` 是具体 deterministic executor；
- 二者并不冲突。

## 14. 放弃了什么

- 方案 A：继续强化 prompt / skill 约束。
  - 放弃原因：命中与执行仍依赖模型稳定性，不能作为 deterministic source of truth。

- 方案 B：把所有流程都封装成 wrapper tool。
  - 放弃原因：只能收敛单次动作，无法表达跨 turn 状态推进和 gate 语义。

- 方案 C：直接采用 n8n / DAG workflow JSON。
  - 放弃原因：表达力过宽、实现与运行时适配成本过高，不符合当前极简原则。

- 方案 D：先发明 declarative DSL。
  - 放弃原因：会把问题从“流程边界”升级成“语言设计”，与当前阶段不匹配。

## 15. 预期收益

- 对少量高约束流程，正确性从 prompt 迁移到代码层。
- 保留 `skills` 的渐进式披露优点，但去掉其模糊触发依赖。
- 保留现实中的裁量空间，不把完整任务执行路径写死。
- 与现有 Tool Modes、Prompt layering、script-based deterministic executor 保持一致。
- 降低协议漂移、命令拼装错误和跨 session 讨论成本。

## 16. 残余风险与开放问题

- 当前草案尚未定义 actor/subject 分离。若未来需要代码层角色权限，应单独设计 actor 来源，以及它与 `ToolContext` / execution context 的关系。
- 若未来需要 Shared Companion 场景中的多 principal / shared space，可在 `ToolContext` / execution context 外扩展 principal、shared_space、visibility intent，而不把这些语义塞进 `ActiveProcedure.context` 的任意 dict 中。
- 当前草案尚未定义 deviation / waiver 的正式模型；V1 可先靠 `soft_policies` + warning 保持弹性。
- V1 已固定为 session-scoped single active procedure；若未来需要并发 procedure，需要单独界定 session 语义、冲突策略与可见性规则。
- 当前草案尚未决定 `ActiveProcedure` 的持久化表结构与清理策略，但已固定 optimistic CAS 语义。

## 17. 建议的下一步

- 先按本文口径落地 `ProcedureSpec`、`ActiveProcedure`、`ProcedureContextRegistry`、`ProcedureGuardRegistry`、`ToolResult.context_patch` 与独立的 `ProcedureRuntime / ProcedureExecutor`；实现时避免把 execution context 永久写死为单 actor / 单 principal。
- 再选一个最小真实案例验证，优先考虑：
  - `devcoord.pm.gate`
  - 或一个更轻量但具状态推进的内部流程。
- 在真实案例稳定前，不扩展到通用 workflow 语义；优先验证 `fail-stays`、optimistic CAS 与 session single-active 语义。
