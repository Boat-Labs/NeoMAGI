# M3 会话外持久记忆 实现计划

> 状态：approved
> 日期：2026-02-22（rev7 审批 2026-02-23）
> 依据：`design_docs/m3_architecture.md`、`design_docs/memory_architecture.md`、`design_docs/roadmap_milestones_v3.md`、ADR 0006/0014/0027/0032/0034/0035
> rev2 变更：修复 ToolContext 主链路、upsert 策略、Evolution 状态机、rollback 工具入口、dmScope 配置归属、recall 参数契约、Use Case E 分层验收
> rev3 变更：修复 ToolContext 并发串话风险（改为局部参数传递）、定义 scope_resolver 落地模块、统一 flush scope 映射口径、补旧数据兼容规则、硬化 M3-E 验收措辞
> rev4 变更：dm_scope M3 硬限制为 main（fail-fast）、SessionIdentity 补 channel_id 字段并修正 session key 语义、flush scope 改为显式 ResolvedFlushCandidate wrapper
> rev5 变更：统一 ResolvedFlushCandidate 契约（MemoryWriter 签名 + 清除残留动态属性描述）、ResolvedFlushCandidate 归属 memory 领域 contracts 模块、SessionIdentity channel_id 来源标注、M3-E 验收口径完整化（接口契约测试 + fail-fast guardrail 测试）
> rev6 变更：contracts.py 消除对 agent 层的反向依赖（ResolvedFlushCandidate 改为 memory 侧自有 DTO，AgentLoop 负责映射）、Phase 3 调用链示意修正为 SessionIdentity 签名
> rev7 变更：引入 ADR 0035，M3 Phase 0 增加 runtime 最小防护 gate（Core Safety Contract + 工具级 risk_level + pre-tool fail-closed）；BaseTool 新增 RiskLevel 枚举替代 ToolGroup 分组判定；pre-LLM guard 仅检测+记录不阻断、阻断逻辑收敛到 pre-tool guard；guard_state 生命周期改为每轮 LLM 调用；contract 刷新策略统一为启动加载+惰性 hash 刷新；补充 Use Case F、guardrail 测试矩阵与文件位点；Out of Scope 精确化运行时漂移表述

## 1. 目标

建立"可沉淀、可检索、可治理"的会话外记忆闭环，以及"可验证、可回滚、可审计"的自我进化最小闭环。

核心交付：
- **Memory Write Path**：`memory_append` 工具 + compaction flush 候选自动落盘，日常笔记跨天可用。
- **Memory Index & Search**：ParadeDB `pg_search` BM25 全文检索，`memory_search` 工具从占位升级为可用。
- **Prompt Memory Recall**：`_layer_memory_recall()` 注入检索结果，agent 每次 turn 可自动获取相关记忆。
- **Memory Curation**：daily notes → MEMORY.md 策展流程，短期与长期记忆分层管理。
- **Evolution Loop**：`SOUL.md` 提案 → eval → 生效 → 回滚完整管线，AI-only 写入 + 用户 veto/rollback。
- **Runtime Guardrail Baseline**：Core Safety Contract 定义 + 工具级 `risk_level` 风险标签 + pre-LLM 检测预警 + pre-tool fail-closed 执行闸门（ADR 0035，Phase 0 交付）。

## 2. 当前基线（M2 输出）

### 2.1 已有接口（M3 直接消费）

| 组件 | 位置 | 接口 | 状态 |
|------|------|------|------|
| Memory flush 候选 | `src/agent/memory_flush.py` | `MemoryFlushCandidate` (candidate_id, source_session_id, candidate_text, constraint_tags, confidence) | M2 产出，JSONB 存于 `sessions.memory_flush_candidates` |
| Compaction 结果 | `src/agent/compaction.py` | `CompactionResult.memory_flush_candidates` | M2 唯一输出通道 (ADR 0032) |
| Session 存储 | `src/session/models.py` | `SessionRecord.memory_flush_candidates` (JSONB) | M2 写入，M3 读取 |
| Workspace 文件 | `src/infra/init_workspace.py` | `memory/` 目录 + `MEMORY.md` 模板 | 已初始化，未被写入 |
| Prompt 注入 | `src/agent/prompt_builder.py` | `_layer_memory_recall()` | 占位（返回空字符串） |
| memory_search 工具 | `src/tools/builtins/memory_search.py` | `execute(query)` → `{"results": [], "message": "..."}` | 占位实现 |
| Tool 系统 | `src/tools/base.py` | `ToolGroup.memory` + `ToolMode.chat_safe` | 已定义，可注册新工具 |
| Tool 执行签名 | `src/tools/base.py` | `BaseTool.execute(arguments: dict) -> dict` | **无 context 参数**，M3 须改造 |
| Tool 调用点 | `src/agent/agent.py` | `_execute_tool()` 仅传 `arguments` | **无 ToolContext 注入**，M3 须改造 |
| Prompt build 签名 | `src/agent/prompt_builder.py` | `build(session_id, mode, compacted_context)` | **无 scope_key 参数**，M3 须扩展 |
| Session 配置 | `src/config/settings.py` | `SessionSettings(default_mode)` | **无 dm_scope 字段**，M3 须新增 |

### 2.2 未实现（M3 须交付）

- **ToolContext 注入链路改造**：`BaseTool.execute` 签名扩展 + `_execute_tool` 构造 context + `PromptBuilder.build` scope_key 传入。
- **dmScope 配置落地**：`SessionSettings` 新增 `dm_scope` 字段。
- **Runtime Guardrail Baseline（ADR 0035，M2 风险回补）**：
  - 锚点探针强度不足：现有 M2 锚点可见性检查为最小存在性探针，需升级为 Core Safety Contract 校验。
  - guard 失败高风险仍 fail-open：M2 guard 失效时默认 fail-open，高风险工具执行无最后防线。
  - 离线验收与运行时口径断层：M2 验收口径为离线证据，未落地为运行时执行门槛。
- `memory_append` 工具：受控追加写入 `memory/YYYY-MM-DD.md`。
- `memory_search` 实际检索逻辑（BM25）。
- 自动加载"今天+昨天"daily notes 到 prompt。
- Flush 候选 → daily notes 落盘管线。
- `memory_entries` 表 + pg_search BM25 索引。
- MEMORY.md 策展（从 daily notes 归纳更新）。
- SOUL.md 版本快照、提案/eval/回滚管线。

## 3. 设计决策汇总

### 3.1 已确定

| 决策项 | 选择 | 依据 |
|--------|------|------|
| 记忆数据面 | PostgreSQL 16 + pg_search + pgvector | ADR 0006 |
| 分词策略 | ICU 主召回 + Jieba 中文补充 | ADR 0014 |
| 检索路径 | 先 BM25，后 Hybrid（BM25 + vector 融合） | memory_architecture.md |
| 记忆源数据 | 文件导向（daily notes + MEMORY.md），DB 仅做检索索引 | memory_architecture.md |
| flush 候选来源 | CompactionEngine 唯一生成 (ADR 0032) | ADR 0032 |
| SOUL.md 写入权 | AI-only（bootstrap v0-seed 例外） | ADR 0027 |
| 自我进化前置条件 | 必须可验证、可回滚 | ADR 0027 |
| memory_append 用途 | 仅用于记忆文件，不用于 SOUL.md | memory_architecture.md |
| dmScope 作用域契约 | session_resolver 产出 scope_key，注入 tool context 和 recall 层 | ADR 0034 + m3_architecture.md |
| scope_key 配置 | M3 全局默认 `main`，`dm_scope` 归属 `SessionSettings`（非 MemorySettings）；M4 扩展 per-channel | ADR 0034 |
| scope 传播路径 | session_resolver → tool_context.scope_key → memory 工具/recall 消费；禁止二次推导 | m3_architecture.md §3.1 |
| Runtime anti-drift guardrail | Core Safety Contract + 工具级 risk_level（非 ToolGroup）+ pre-LLM 检测预警 + pre-tool fail-closed 执行闸门；guard_state 生命周期 = 每轮 LLM 调用；contract 启动加载 + 惰性 hash 刷新，Phase 0 交付 | ADR 0035 |
| SOUL.md 版本存储 | DB 表（soul_versions）；git 依赖外部状态，DB 表自包含、可查询、可回滚 | Phase 4 落地 |
| Evolution eval 策略 | 规则约束检查为基线（锚点可见性 + 约束不违反）；Probe 问答集作为可选增强（不阻塞 M3） | Phase 4 落地 |
| Flush 候选落盘时机 | compaction 后立即写入（最简路径，无需额外基础设施） | Phase 1 落地 |
| 记忆去重策略 | M3 不做精确去重，依赖 MEMORY.md 策展阶段合并 | Phase 3 落地 |
| daily notes 自动加载范围 | 今天+昨天（与 system_prompt.md 对齐） | Phase 1 落地 |
| Hybrid Search 时机 | M3 后单独迭代（BM25 已满足验收，Hybrid 是质量增强不是门槛） | Out of Scope |
| memory_entries 索引幂等策略 | delete-reinsert by source_path（文件是源数据，DB 仅做索引） | Phase 2 落地 |
| ToolContext 注入方式 | 改 execute 签名（显式参数比隐式属性更安全、可测试） | Phase 0 落地 |

### 3.2 待讨论

> 以下决策项在实施阶段可能需要根据实际情况调整，但当前无阻塞性分歧。

（已清空：原 3.2 全部条目已在各 Phase 落地方案中收敛为已确定，移入 3.1。如后续实施中发现需要重新讨论，在此处新增条目。）

## 4. Phase 拆分

### 总览

```
Phase 0: ToolContext + dmScope + Runtime Guardrail 基础设施（主链路改造 + 最小防护，所有后续 Phase 的前置）
  ↓
Phase 1: Memory Write Path（记忆写入 + 跨天可用）
  ↓
Phase 2: Memory Index & Search（BM25 检索闭环）
  ↓
Phase 3: Memory Curation（策展 + prompt 自动注入）
  ↓
Phase 4: Evolution Loop（SOUL.md 自我进化治理）
```

每个 Phase 独立可验证、可提交。Phase 间存在依赖但解耦度高。
**Phase 0 是 M3 新增前置步骤**，因为 Phase 1 起所有工具均依赖 `ToolContext.scope_key`。

---

### Phase 0：ToolContext + dmScope + Runtime Guardrail 基础设施

#### 4.0.1 目标

改造工具执行主链路，使所有工具可通过 `ToolContext` 获取 `scope_key` 等运行时上下文。同时在 `SessionSettings` 落地 `dm_scope` 配置，在 `session` 领域落地 `scope_resolver` 模块。**此外，落地最小运行时反漂移防护（ADR 0035）**：定义 Core Safety Contract（启动加载 + 惰性 hash 刷新）、LLM 调用前 guard 检测（仅检测记录，不阻断）、工具级 `risk_level` 属性 + pre-tool guard 执行闸门（`risk_level=high` 时 fail-closed）、结构化错误码与审计日志。Phase 0 完成后，Phase 1 起的所有工具和 prompt 层可直接消费 `scope_key`，且高风险执行路径受 guardrail 保护。

#### 4.0.2 新增 `src/tools/context.py`

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    """Runtime context injected into tool execution by AgentLoop.

    scope_key: resolved by session_resolver (ADR 0034). Tools MUST NOT
    re-derive scope from session_id; they consume this value directly.
    session_id: current session identifier (for audit/logging).
    """

    scope_key: str = "main"
    session_id: str = "main"
```

#### 4.0.3 新增 `src/session/scope_resolver.py`

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionIdentity:
    """Minimal identity for scope resolution.

    M3: session_id + channel_type + channel_id are the active fields.
    M4: peer_id, account_id become active for per-peer/per-account scopes.
    """

    session_id: str
    channel_type: str = "dm"       # "dm" | "telegram" | ...
    channel_id: str | None = None  # group chat channel ID (for session key routing)
    peer_id: str | None = None     # M4 预留: per-peer isolation
    account_id: str | None = None  # M4 预留: per-account isolation


def resolve_scope_key(identity: SessionIdentity, dm_scope: str = "main") -> str:
    """Pure function: identity + dm_scope → scope_key.

    M3: dm_scope is guaranteed to be 'main' by SessionSettings validator.
    Non-main values raise ValueError (fail-fast, no silent fallback).

    M4 extension points (after SessionSettings validator is relaxed):
    - 'per-peer' → f"peer:{identity.peer_id}"
    - 'per-channel-peer' → f"{identity.channel_type}:peer:{identity.peer_id}"
    - 'per-account-channel-peer' → f"{identity.account_id}:{identity.channel_type}:peer:{identity.peer_id}"
    """
    if dm_scope == "main":
        return "main"
    # M3: this branch should never be reached (guarded by SessionSettings validator).
    # Fail-fast if it does — never silently degrade to main.
    raise ValueError(
        f"dm_scope '{dm_scope}' is not supported in M3. "
        "Only 'main' is allowed. See ADR 0034."
    )


def resolve_session_key(identity: SessionIdentity, dm_scope: str = "main") -> str:
    """Pure function: identity + dm_scope → session storage key.

    Key semantics (aligned with existing manager.py:resolve_session):
    - DM → scope_key (from resolve_scope_key)
    - Group → 'group:{channel_id}' (channel_id from identity, NOT session_id)
    """
    if identity.channel_type == "dm":
        return resolve_scope_key(identity, dm_scope)
    if identity.channel_id is None:
        raise ValueError("channel_id is required for non-DM session key resolution")
    return f"group:{identity.channel_id}"
```

**设计决策**：
- **归属 session 领域**（非 agent 层）：dmScope 本质是会话隔离策略，M4 渠道适配也要复用。放 agent 会造成反向依赖。
- **纯函数**：输入 identity + dm_scope，输出 scope_key/session_key。确定性强，易测。
- **fail-fast，无 silent fallback**：M3 阶段 dm_scope 只允许 `main`（由 SessionSettings 校验器保证）。`resolve_scope_key` 对非 main 值直接 raise，不静默降级。M4 放开时修改校验器 allowed set 即可。
- **session key 语义对齐**：`resolve_session_key` 的 group 分支使用 `identity.channel_id`（与现有 `manager.py:resolve_session` 用 `channel_id` 一致），而非 `session_id`。避免 M4 迁移时 key 漂移。
- **与现有 `resolve_session` 的关系**：`manager.py:resolve_session()` 保持不动，M4 迁移时再统一。`scope_resolver` 是 dmScope 扩展，不替代现有 session routing。
- **请求入口解析一次**：AgentLoop 在 `_handle_request` 入口调用 `resolve_scope_key`，结果通过 ToolContext 和 `build(scope_key=...)` 透传，**下游禁止重算**。

#### 4.0.4 修改 `src/tools/base.py`

```python
class RiskLevel(StrEnum):
    """Tool-level risk classification for guardrail gating (ADR 0035).

    Guard only checks risk_level, NOT ToolGroup.
    ToolGroup retains its original role as domain classification.
    Undeclared tools default to 'high' (fail-closed).
    """

    low = "low"
    high = "high"


class BaseTool(ABC):
    # ... existing properties unchanged ...

    @property
    def risk_level(self) -> RiskLevel:
        """Risk classification for guardrail gating (ADR 0035).

        Fail-closed default: high. Tools that are read-only or have no
        external side effects should explicitly declare low.
        """
        return RiskLevel.high

    @abstractmethod
    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        """Execute the tool with given arguments and optional runtime context.

        context is None for backward compatibility with existing tools that
        don't need scope_key. New M3+ tools should always expect context.
        """
        ...
```

**向后兼容**：`context` 参数默认为 `None`。现有工具（如占位 memory_search）无需立即改动签名，但 M3 新增/升级工具应声明 `context: ToolContext` 并使用。

**risk_level 声明规则**（ADR 0035）：
- `RiskLevel.high`：具有写入、执行或外部副作用的工具。
- `RiskLevel.low`：只读或无副作用的工具。
- **所有工具必须显式声明 `risk_level`**，不得依赖 `BaseTool` 的默认值。`BaseTool.risk_level` 保留 `high` 默认值仅作为安全网（防止第三方扩展遗漏），项目内工具禁止依赖。

**强制 risk_level 映射表**（Phase 0 退出门槛之一，每个工具必须显式声明）：

| 工具 | risk_level | 理由 |
|------|-----------|------|
| `memory_search` | **low** | 只读 BM25 检索，无副作用 |
| `memory_append` | **high** | 写入 daily notes 文件 |
| `soul_status` | **low** | 只读版本查询，无副作用 |
| `soul_propose` | **high** | 链式 propose→eval→apply，写入 SOUL.md + DB |
| `soul_rollback` | **high** | 回滚/否决 SOUL.md 版本，写入文件 + DB |

> Phase 0 实施时须盘点 `src/tools/builtins/*.py` 全部工具，每个工具显式声明 `risk_level`。未出现在上表的 M1/M2 现有工具在 Phase 0 实施阶段逐一审查并补入此表。此表纳入 Phase 0 退出门槛检查。

#### 4.0.5 修改 `src/agent/agent.py`：ToolContext 局部传递（并发安全）

```python
async def _handle_request(self, session_id: str, ...) -> ...:
    """Request entry point. Resolve scope_key ONCE as local variable."""
    # channel_id 来源：gateway 层请求元数据（WebSocket connect params / channel adapter context）。
    # M3 主路径为 DM (channel_type='dm')，channel_id 不参与 resolve_scope_key。
    # M4 多渠道接入时，gateway 须在 connect handshake 中携带 channel_id。
    identity = SessionIdentity(
        session_id=session_id,
        channel_type=request.channel_type,   # from gateway request metadata
        channel_id=request.channel_id,       # from gateway request metadata (None for DM)
    )
    scope_key = resolve_scope_key(identity, dm_scope=settings.session.dm_scope)

    # Pass as local argument throughout — NO instance field storage
    prompt = self._prompt_builder.build(
        session_id, mode, compacted_context,
        scope_key=scope_key,
    )
    # ... LLM call ...
    # ... tool calls: pass scope_key to _execute_tool ...

async def _execute_tool(
    self, tool_name: str, arguments_json: str,
    *, scope_key: str, session_id: str,  # explicit parameters, NOT from self
) -> dict:
    # ... existing validation unchanged ...

    # Construct ToolContext from caller-provided local variables (NOT instance fields)
    context = ToolContext(scope_key=scope_key, session_id=session_id)

    try:
        result = await tool.execute(arguments, context)
        # ...
```

**并发安全**：`scope_key` 和 `session_id` 作为 `_execute_tool` 的**显式参数**传入，不存储在 `self` 实例字段上。在共享 AgentLoop 实例的多会话并发场景下，每个请求的 scope_key 是独立的栈变量，不会串话。

#### 4.0.6 修改 `src/config/settings.py`：SessionSettings 新增 dm_scope

```python
class SessionSettings(BaseSettings):
    """Session mode settings. Env vars prefixed with SESSION_."""

    model_config = SettingsConfigDict(env_prefix="SESSION_")

    default_mode: str = "chat_safe"
    dm_scope: str = "main"  # ADR 0034: M3 硬限制为 main，M4 放开

    @field_validator("dm_scope")
    @classmethod
    def _validate_dm_scope(cls, v: str) -> str:
        # M3 guardrail: only 'main' is allowed. Fail-fast on any other value.
        # This mirrors the M1.5 default_mode guardrail pattern (line 74).
        # M4 will expand allowed set to {"main", "per-peer", "per-channel-peer", ...}.
        if v != "main":
            raise ValueError(
                f"SESSION_DM_SCOPE must be 'main' in M3 (got '{v}'). "
                "Non-main scopes will be enabled in M4. See ADR 0034."
            )
        return v
```

#### 4.0.7 修改 `src/agent/prompt_builder.py`：build() 签名扩展

```python
def build(
    self,
    session_id: str,
    mode: ToolMode,
    compacted_context: str | None = None,
    *,
    scope_key: str = "main",              # ADR 0034: from session_resolver
    recent_messages: list[str] | None = None,  # Phase 3: for memory recall keyword extraction
) -> str:
    layers = [
        self._layer_identity(),
        self._layer_tooling(mode),
        self._layer_safety(mode),
        self._layer_skills(),
        self._layer_workspace(session_id, scope_key=scope_key),  # scope-aware daily notes
        self._layer_compacted_context(compacted_context),
        self._layer_memory_recall(scope_key=scope_key, recent_messages=recent_messages),
        self._layer_datetime(),
    ]
    return "\n\n".join(layer for layer in layers if layer)
```

**说明**：Phase 0 仅扩展签名并透传 `scope_key`。`_layer_memory_recall` 仍返回空字符串（Phase 3 实现）。`_layer_workspace` 在 Phase 1 接入 daily notes 加载时消费 `scope_key`。

#### 4.0.8 现有工具向后兼容处理

现有 M1/M2 工具（如占位 `memory_search`）的 `execute(self, arguments: dict)` 签名暂不强制修改。`_execute_tool` 传入 context 后，Python 调用不匹配的签名会报错，因此需要二选一：
- **方案 A（推荐）**：统一更新所有现有工具签名为 `execute(arguments, context=None)`，一次性完成。工具数量少（M2 时 ≤ 5 个），改动量可控。
- **方案 B**：`_execute_tool` 中做 `inspect.signature` 检查，有 context 参数才传。增加运行时复杂度，不推荐。

#### 4.0.9 Runtime Guardrail：Core Safety Contract 定义（ADR 0035）

Core Safety Contract 是一组从 `AGENTS.md`/`USER.md`/`SOUL.md` 中提取的**不可退让约束**清单，用于运行时 guard 校验。

```python
# src/agent/guardrail.py

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CoreSafetyContract:
    """Immutable set of anchors that MUST remain visible in execution context.

    Source: extracted from AGENTS.md / USER.md / SOUL.md.
    Lifecycle: loaded at agent startup, lazily refreshed when source_hash changes.
    """

    anchors: tuple[str, ...]          # key phrases that must be visible
    constraints: tuple[str, ...]      # rules that must not be violated
    source_hash: str = ""             # content hash for cache invalidation


@dataclass
class GuardCheckResult:
    """Result of a single guard checkpoint execution."""

    passed: bool
    missing_anchors: list[str] = field(default_factory=list)
    violated_constraints: list[str] = field(default_factory=list)
    error_code: str = ""              # structured error code for audit
    detail: str = ""
```

**设计决策——Contract 刷新策略**：
- **启动时加载**：agent 启动时从工作区文件（AGENTS.md / USER.md / SOUL.md）提取锚点和约束，构建 `CoreSafetyContract` 实例并缓存。
- **惰性 hash 刷新**：每次 guard 检查前比对文件 content hash 与缓存的 `source_hash`，hash 变化时重新提取并替换缓存实例。不使用 file watcher，避免引入异步监听复杂度。
- **刷新时机**：与 SOUL.md Evolution 生效（Phase 4 `apply()`）天然对齐——apply 写入新 SOUL.md 内容后，下一次 guard 检查发现 hash 变化即刷新。

#### 4.0.10 Runtime Guardrail：LLM 调用前 guard 检测（ADR 0035）

在 `AgentLoop` 的主循环中，**每次** LLM 调用前执行 guard 校验。pre-LLM guard **仅检测与记录，不阻断 LLM 调用**——阻断逻辑全部收敛到 pre-tool guard。

```python
# src/agent/guardrail.py (续)

async def check_pre_llm_guard(
    contract: CoreSafetyContract,
    execution_context: str,  # system prompt + compacted context + effective history
) -> GuardCheckResult:
    """Verify all contract anchors are visible in the LLM execution context.

    Called BEFORE every LLM API call (each iteration of the tool loop).
    This is a DETECTION checkpoint, NOT a blocking gate:
    - Always returns GuardCheckResult (passed or failed).
    - On failure: logs guardrail_warning audit event, does NOT block LLM call.
    - The returned guard_state is consumed by check_pre_tool_guard for
      actual fail-closed gating on high-risk tools.

    Rationale: at pre-LLM stage we cannot determine whether the LLM will
    invoke high-risk tools, so blocking here would cause indiscriminate
    denial of service for pure conversation paths.
    """
```

**集成点**：
- `_handle_request` 主循环中，每次 `prompt = prompt_builder.build(...)` 之后、LLM 调用之前执行。
- **每轮 LLM 调用都重新执行**（非复用首轮结果），因为多轮 tool loop 中 context 会随工具结果追加而变化。
- 返回的 `guard_state` 作为当轮所有 `_execute_tool` 调用的显式参数传入。

#### 4.0.11 Runtime Guardrail：高风险工具执行前 guard 检查与错误码（ADR 0035）

在 `AgentLoop._execute_tool()` 中，根据工具的 `risk_level` 属性决定是否阻断执行：

```python
# src/agent/guardrail.py (续)

# 结构化错误码
GUARDRAIL_ERROR_CODES = {
    "GUARD_ANCHOR_MISSING": "Core anchor(s) not visible in execution context",
    "GUARD_CONSTRAINT_VIOLATED": "Safety constraint violation detected",
    "GUARD_CONTRACT_UNAVAILABLE": "Core Safety Contract could not be loaded",
}


async def check_pre_tool_guard(
    guard_state: GuardCheckResult,  # from current iteration's pre-LLM check
    tool_name: str,
    tool_risk_level: RiskLevel,     # from tool.risk_level (NOT tool.group)
) -> GuardCheckResult:
    """Gate tool execution based on guard state and tool risk level.

    guard_state comes from the CURRENT tool-loop iteration's
    check_pre_llm_guard() call (refreshed each iteration, not carried
    from a previous iteration).

    If tool_risk_level is HIGH and guard_state.passed is False:
    → Return fail-closed result with structured error code.
    → Tool execution is BLOCKED. Error returned to LLM as tool result.

    If tool_risk_level is LOW:
    → Allow execution even if guard_state.passed is False (degraded mode).
    → Log guardrail_degraded audit event.
    """
```

**设计决策——risk_level 替代 ToolGroup 判定**：
- Guard 判定只看 `tool.risk_level`（`RiskLevel.high` / `RiskLevel.low`），**不看 `ToolGroup`**。
- `ToolGroup` 保持原有语义（领域分类：code / memory / world），不承担安全职责。
- 这避免了同一 ToolGroup 内只读与写入工具混合导致的误伤/漏拦（如 `memory_search` vs `memory_append`）。

**guard_state 生命周期**：
- `guard_state` 的生命周期 = **一轮 LLM 调用**（而非整个请求）。
- 每次 LLM 返回新的 tool_calls 批次时，已由当轮 `check_pre_llm_guard` 产生新的 `guard_state`。
- 同一轮内多个工具共享当轮 `guard_state` 是安全的（上下文相同）。
- 跨轮不复用，因为前一轮工具结果会追加到 context，改变锚点可见性。

**集成点**：`_execute_tool` 在构造 `ToolContext` 之后、调用 `tool.execute()` 之前插入 guard 检查。`guard_state` 从当轮 `_handle_request` 主循环的 pre-LLM 检查结果透传（作为 `_execute_tool` 的显式参数，与 scope_key 相同模式）。

#### 4.0.12 Phase 0 退出门槛（ADR 0035）

Phase 0 退出（进入 Phase 1）的 **硬门槛**：

1. **ToolContext + dmScope 主链路**：所有现有测试通过 + Phase 0 新增测试通过。
2. **Core Safety Contract 可加载**：agent 启动时从工作区文件提取 contract，无异常；惰性 hash 刷新逻辑工作正常。
3. **Pre-LLM guard 检测正确**：在正常启动条件下，pre-LLM guard 检查不产生 false negative（锚点可见时 passed=True）；检查失败时记录 `guardrail_warning` 审计日志但**不阻断 LLM 调用**。
4. **高风险工具 guard 阻断验证**：`risk_level=high` 的工具在 guard_state.passed=False 时被阻断（测试覆盖）；`risk_level=low` 的工具在相同条件下可降级执行。
5. **审计日志可查**：`guardrail_blocked` / `guardrail_degraded` / `guardrail_warning` 事件在 structlog 输出中可见。
6. **guard_state 每轮刷新验证**：多轮 tool loop 场景下，每次 LLM 调用前都产生新的 guard_state（测试覆盖）。
7. **risk_level 映射表完整**：`src/tools/builtins/*.py` 中每个工具都显式声明了 `risk_level`，与 4.0.4 强制映射表一致（无遗漏、无默认值依赖）。

**不通过 Phase 0 退出门槛，不得进入 Phase 1。**

#### 4.0.13 测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_tool_context.py` | ToolContext: 创建 / 默认值 / frozen 不可变 |
| `tests/test_scope_resolver.py` | resolve_scope_key: main → 'main' / identity 字段预留 / dm_scope 验证 / resolve_session_key 与 resolve_session 行为对齐 |
| `tests/test_base_tool.py` | BaseTool: execute 新签名 / context=None 向后兼容 |
| `tests/test_agent_tool_context.py` | _execute_tool: 构造 ToolContext / scope_key 传播 / 现有工具兼容 / **并发隔离**（模拟两个并发请求，验证 scope_key 不串话） |
| `tests/test_prompt_builder.py` | build(): scope_key 参数透传 / 默认 main 行为不变 |
| `tests/test_settings.py` | SessionSettings: dm_scope 验证 / 默认值 / 非法值拒绝 |
| `tests/test_guardrail.py` | CoreSafetyContract: 创建 / frozen 不可变 / 锚点提取 / 惰性 hash 刷新（文件变化后下次检查自动更新 contract） |
| `tests/test_guardrail.py` | check_pre_llm_guard: 全部锚点可见 → passed / 锚点缺失 → failed + missing_anchors 列表 / 空 contract → GUARD_CONTRACT_UNAVAILABLE / **失败时不阻断（仅记录 guardrail_warning）** |
| `tests/test_guardrail.py` | check_pre_tool_guard: `risk_level=high` + guard failed → 阻断 / `risk_level=low` + guard failed → 降级继续 / 结构化错误码正确 |
| `tests/test_guardrail.py` | guardrail_blocked / guardrail_degraded / guardrail_warning 审计日志字段断言（structlog capture） |
| `tests/test_guardrail.py` | guard_state 每轮刷新：模拟多轮 tool loop，验证每次 LLM 调用前产生新 guard_state（非复用前轮） |
| `tests/test_base_tool.py` | RiskLevel: 枚举值 / BaseTool 默认 risk_level=high / 声明 low 的工具正确返回 |

#### 4.0.14 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `src/session/scope_resolver.py` | 新增：SessionIdentity + resolve_scope_key + resolve_session_key |
| `src/tools/context.py` | 新增：ToolContext dataclass |
| `src/tools/base.py` | 修改：execute 签名扩展 + 新增 RiskLevel 枚举 + BaseTool.risk_level 属性 |
| `src/tools/builtins/*.py` | 修改：所有现有工具 execute 签名对齐（方案 A）+ 每个工具显式声明 risk_level（禁止依赖默认值） |
| `src/agent/agent.py` | 修改：_handle_request 主循环每轮调用 pre-LLM guard + _execute_tool 接收 guard_state 显式参数 + pre-tool guard |
| `src/agent/guardrail.py` | 新增：CoreSafetyContract（惰性 hash 刷新）+ GuardCheckResult + check_pre_llm_guard（检测不阻断）+ check_pre_tool_guard（risk_level 闸门）+ GUARDRAIL_ERROR_CODES |
| `src/agent/prompt_builder.py` | 修改：build() 签名扩展（scope_key + recent_messages） |
| `src/config/settings.py` | 修改：SessionSettings 新增 dm_scope |
| 测试文件（6 + 2 个） | 新增 / 修改（含 `tests/test_guardrail.py` + `tests/test_base_tool.py` RiskLevel 部分） |

---

### Phase 1：Memory Write Path

#### 4.1.1 目标

实现记忆写入能力，使 agent 可以将信息持久化到 daily notes，并且跨天对话时自动加载近期记忆。Phase 1 完成后，Use Case A（用户偏好跨天生效）的文件层路径已通。

#### 4.1.2 新增模块 `src/memory/contracts.py`

Memory 领域共享契约类型。Memory 层自有 DTO，**不依赖 `src.agent.*`**。AgentLoop 负责从 `MemoryFlushCandidate` 映射到此 DTO。

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedFlushCandidate:
    """Memory-side DTO for scope-resolved flush candidates.

    Constructed by AgentLoop (mapping from agent-layer MemoryFlushCandidate).
    Consumed by MemoryWriter.process_flush_candidates().

    Memory layer does NOT import or depend on src.agent.* types.
    AgentLoop performs the one-time mapping at the boundary:
        MemoryFlushCandidate → ResolvedFlushCandidate
    """

    candidate_text: str
    scope_key: str
    source_session_id: str
    confidence: float = 0.0
    constraint_tags: tuple[str, ...] = ()
```

**归属决策**：
- `ResolvedFlushCandidate` 是 memory 侧自有 DTO，**零 agent 层 import**。
- 字段是 `MemoryFlushCandidate` 的投影子集（仅 Writer 需要的字段），而非包装整个 agent 对象。
- AgentLoop 在边界做一次映射（`MemoryFlushCandidate` → `ResolvedFlushCandidate`），之后 memory 层全程自包含。
- 这保证了依赖方向单向：`agent → memory`，不存在 `memory → agent` 的反向路径。

#### 4.1.3 新增模块 `src/memory/writer.py`

```python
class MemoryWriter:
    """Write memory entries to workspace daily notes files.

    Responsibilities:
    - Append text to memory/YYYY-MM-DD.md (create if not exist)
    - Process flush candidates from compaction → daily notes
    - Enforce file size limits
    - UTF-8 safe writes (CJK compatible)
    - Carry scope_key metadata on every write (ADR 0034)
    """

    def __init__(self, workspace_path: Path, settings: MemorySettings) -> None: ...

    async def append_daily_note(
        self,
        text: str,
        *,
        scope_key: str = "main",  # from tool_context, ADR 0034
        source: str = "user",  # "user" | "compaction_flush" | "system"
        date: date | None = None,  # default: today
    ) -> Path:
        """Append a timestamped entry to daily note file.

        Format:
        ---
        [HH:MM] (source: {source}, scope: {scope_key})
        {text}

        Returns: path to the written file.
        Raises: MemoryWriteError if file exceeds max size.
        """

    async def process_flush_candidates(
        self,
        candidates: list[ResolvedFlushCandidate],
        *,
        min_confidence: float = 0.5,
    ) -> int:
        """Filter and persist flush candidates to today's daily note.

        Receives ResolvedFlushCandidate (from src/memory/contracts.py),
        a memory-side DTO with flat fields. AgentLoop maps from
        MemoryFlushCandidate at the boundary; Writer never imports agent types.

        Filters:
        - candidate.confidence >= min_confidence
        - candidate.candidate_text non-empty

        Returns: number of candidates written.
        """
```

#### 4.1.4 新增工具 `src/tools/builtins/memory_append.py`

```python
class MemoryAppendTool(BaseTool):
    name = "memory_append"
    risk_level = RiskLevel.high  # writes to daily notes files (ADR 0035)
    description = "Save a memory note to today's daily notes file"
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The memory content to save"},
        },
        "required": ["text"],
    }
    group = ToolGroup.memory
    allowed_modes = frozenset({ToolMode.chat_safe, ToolMode.coding})

    async def execute(self, arguments: dict, context: ToolContext) -> dict:
        """Write to memory/YYYY-MM-DD.md via MemoryWriter.

        scope_key is read from context.scope_key (injected by session_resolver).
        Tool does NOT derive scope on its own (ADR 0034).
        """
        scope_key = context.scope_key  # injected by session_resolver
        await self.writer.append_daily_note(
            text=arguments["text"],
            scope_key=scope_key,
            source="user",
        )
```

#### 4.1.5 Prompt Builder 扩展：daily notes 自动加载

`_layer_workspace()` 中新增 daily notes 加载逻辑（按 scope_key 过滤）：

```python
def _load_daily_notes(self, workspace_path: Path, scope_key: str) -> str:
    """Load today + yesterday daily notes, filtered by scope_key.

    scope_key comes from session_resolver (ADR 0034).
    Only entries matching the current scope_key are included.
    M3 default: scope_key='main', equivalent to previous "main session only" behavior.

    **旧数据兼容规则**：无 scope 元数据的历史条目视为 scope_key='main'。
    理由：M3 之前写入的所有 daily notes 都是在 main session 下产生的。

    Format injected:
    [Recent Daily Notes]
    === 2026-02-22 ===
    {content of memory/2026-02-22.md, filtered by scope}
    === 2026-02-21 ===
    {content of memory/2026-02-21.md, filtered by scope}

    Truncation: each file max 4000 tokens (configurable).
    """
```

#### 4.1.6 AgentLoop 集成：flush 候选自动落盘

在 `agent.py` 的 compaction 成功路径中，增加 flush 候选落盘：

```python
from src.memory.contracts import ResolvedFlushCandidate

# After successful compaction store
if result.memory_flush_candidates:
    # Boundary mapping: agent-layer MemoryFlushCandidate → memory-side ResolvedFlushCandidate.
    # scope_key resolved per candidate using candidate.source_session_id (NOT current session_id).
    # M3: source_session_id == session_id in practice, but contract must be correct for M4+.
    resolved = [
        ResolvedFlushCandidate(
            candidate_text=c.candidate_text,
            scope_key=resolve_scope_key(
                SessionIdentity(session_id=c.source_session_id),
                dm_scope=settings.session.dm_scope,
            ),
            source_session_id=c.source_session_id,
            confidence=c.confidence,
            constraint_tags=tuple(c.constraint_tags),
        )
        for c in result.memory_flush_candidates
    ]
    written = await memory_writer.process_flush_candidates(
        resolved,
        min_confidence=settings.memory.flush_min_confidence,
    )
    logger.info("memory_flush_persisted", count=written, session_id=session_id)
```

**scope 映射口径**：
- flush 候选的 scope_key 以候选条目的 `source_session_id` 为准（非当前 `session_id`）。
- AgentLoop 在此处完成 `MemoryFlushCandidate → ResolvedFlushCandidate` 的边界映射。Memory 层全程不感知 `src.agent.*` 类型。

#### 4.1.7 配置 `src/config/settings.py`

新增 `MemorySettings`：

```python
class MemorySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEMORY_")

    workspace_path: Path = Path("workspace")
    max_daily_note_bytes: int = 32_768        # 32KB per daily note
    daily_notes_load_days: int = 2            # today + yesterday
    daily_notes_max_tokens: int = 4000        # per file injection limit
    flush_min_confidence: float = 0.5         # filter low-confidence candidates
```

#### 4.1.8 测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_memory_writer.py` | MemoryWriter: append_daily_note 正常写入 / 文件创建 / 追加模式 / 超限保护 / UTF-8 CJK / 日期覆盖 / scope_key 写入元数据 |
| `tests/test_memory_writer.py` | process_flush_candidates: confidence 过滤 / 空列表 / 上限 / 格式化输出 / scope_key 传播 |
| `tests/test_memory_append_tool.py` | MemoryAppendTool: execute 正常 / 空 text 拒绝 / ToolGroup/ToolMode 正确 / scope_key 从 context 获取 |
| `tests/test_prompt_daily_notes.py` | _load_daily_notes: 正常加载 / 只有今天 / 无文件 / 超长截断 / scope_key 过滤（默认 main 等价旧行为） / **旧数据兼容**（无 scope 标记条目视为 main） |
| `tests/test_agent_flush_persist.py` | AgentLoop flush 集成: compaction 后自动落盘 / 无候选时跳过 / 落盘失败不影响主流程 / **scope_key 从 candidate.source_session_id 解析**（非当前 session_id） |

#### 4.1.9 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `src/memory/__init__.py` | 修改：导出 |
| `src/memory/contracts.py` | 新增：ResolvedFlushCandidate 共享契约类型 |
| `src/memory/writer.py` | 新增 |
| `src/tools/builtins/memory_append.py` | 新增 |
| `src/agent/prompt_builder.py` | 修改：daily notes 加载 |
| `src/agent/agent.py` | 修改：flush 候选落盘集成 |
| `src/config/settings.py` | 修改：新增 MemorySettings |
| 测试文件（5 个） | 新增 |

---

### Phase 2：Memory Index & Search（BM25 检索闭环）

#### 4.2.1 目标

建立 PostgreSQL BM25 全文检索能力，使 `memory_search` 从占位升级为可用检索。Phase 2 完成后，Use Case B（历史决策可追溯）达标。

#### 4.2.2 DB Migration：memory_entries 表

```sql
-- Alembic migration: create memory_entries table
CREATE TABLE neomagi.memory_entries (
    id          SERIAL PRIMARY KEY,
    scope_key   VARCHAR(128) NOT NULL DEFAULT 'main',  -- ADR 0034: dmScope key from session_resolver
    source_type VARCHAR(16) NOT NULL,  -- 'daily_note' | 'curated' | 'flush_candidate'
    source_path VARCHAR(256),          -- relative path: 'memory/2026-02-22.md'
    source_date DATE,                  -- date of the daily note (nullable for curated)
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    tags        TEXT[] DEFAULT '{}',    -- constraint_tags from flush candidates
    confidence  FLOAT,                 -- from flush candidate (nullable)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for scope-aware filtering (ADR 0034)
CREATE INDEX idx_memory_entries_scope ON neomagi.memory_entries (scope_key);

-- ParadeDB pg_search BM25 index (ADR 0014)
CALL paradedb.create_bm25_index(
    index_name => 'memory_entries_search_idx',
    schema_name => 'neomagi',
    table_name => 'memory_entries',
    key_field => 'id',
    text_fields => paradedb.field('title', tokenizer => paradedb.tokenizer('icu')) ||
                   paradedb.field('content', tokenizer => paradedb.tokenizer('icu')) ||
                   paradedb.field('content', tokenizer => paradedb.tokenizer('chinese_lindera'), alias => 'content_cjk')
);
```

**说明**：
- pg_search BM25 索引 API 以 ParadeDB 实际版本为准，上述为意向伪代码。
- Jieba tokenizer 若 ParadeDB 当前版本不直接支持，回退到 `chinese_lindera` 或 ICU 单通道。
- 实现前须在目标 PostgreSQL 实例上验证 ParadeDB 扩展版本与可用 tokenizer。
- 最终 DDL 写入 Alembic migration 前需先做 spike 验证。

#### 4.2.3 新增模块 `src/memory/indexer.py`

```python
class MemoryIndexer:
    """Sync memory files to PostgreSQL search index.

    Source of truth: files (daily notes + MEMORY.md)
    Index: memory_entries table (for search only)
    All indexed entries carry scope_key for scope-aware filtering (ADR 0034).
    """

    def __init__(self, db_session_factory, settings: MemorySettings) -> None: ...

    async def index_daily_note(self, file_path: Path, *, scope_key: str = "main") -> int:
        """Parse and index a daily note file.

        Strategy:
        - DELETE existing rows WHERE source_path = file_path (idempotent reindex)
        - Split file into entries by '---' separator
        - Each entry → one memory_entries row (with scope_key)
        - Parse scope from entry metadata if present; otherwise use passed scope_key
        - **旧数据兼容**：无 scope 元数据的条目视为 scope_key='main'
        - Batch INSERT all entries

        Rationale: files are source of truth, DB is just an index.
        Delete-reinsert is simpler and avoids entry_offset drift issues.

        Returns: number of entries indexed.
        """

    async def index_curated_memory(self, file_path: Path, *, scope_key: str = "main") -> int:
        """Parse and index MEMORY.md.

        Strategy:
        - Split by markdown headers (##)
        - Each section → one memory_entries row (source_type='curated', scope_key)
        """

    async def reindex_all(self, *, scope_key: str = "main") -> int:
        """Full reindex: scan workspace/memory/ + MEMORY.md."""
```

#### 4.2.4 新增模块 `src/memory/searcher.py`

```python
@dataclass
class MemorySearchResult:
    entry_id: int
    scope_key: str          # ADR 0034
    source_type: str
    source_path: str | None
    title: str
    content: str
    score: float
    tags: list[str]
    created_at: datetime

class MemorySearcher:
    """BM25 search against memory_entries using pg_search.

    All searches are scope-aware: results are filtered by scope_key (ADR 0034).
    """

    def __init__(self, db_session_factory, settings: MemorySettings) -> None: ...

    async def search(
        self,
        query: str,
        *,
        scope_key: str = "main",  # ADR 0034: from tool_context or prompt_builder
        limit: int = 10,
        min_score: float = 0.0,
        source_types: list[str] | None = None,  # filter by source_type
    ) -> list[MemorySearchResult]:
        """Execute BM25 search with ICU + CJK dual tokenizer scoring.

        Scope filtering: WHERE scope_key = :scope_key (mandatory, no bypass).

        Scoring weights (ADR 0014 baseline):
        - title (ICU): 2.0
        - content (ICU): 1.0
        - content_cjk: 0.7

        Returns results sorted by score DESC.
        """
```

#### 4.2.5 memory_search 工具升级

```python
# src/tools/builtins/memory_search.py (升级)
class MemorySearchTool(BaseTool):
    risk_level = RiskLevel.low  # read-only, no side effects (ADR 0035)

    async def execute(self, arguments: dict, context: ToolContext) -> dict:
        """Upgraded: delegate to MemorySearcher for BM25 search.

        scope_key is read from context.scope_key (ADR 0034).
        Tool does NOT derive scope on its own.
        """
        scope_key = context.scope_key
        results = await self.searcher.search(
            query=arguments["query"],
            scope_key=scope_key,
            limit=arguments.get("limit", 10),
        )
        return {
            "results": [
                {
                    "title": r.title,
                    "content": r.content[:500],  # truncate for context
                    "source": r.source_path or "curated",
                    "score": r.score,
                    "tags": r.tags,
                }
                for r in results
            ],
            "total": len(results),
        }
```

#### 4.2.6 MemoryWriter 扩展：写入后自动索引

在 `append_daily_note` 成功后触发增量索引：

```python
async def append_daily_note(self, text: str, *, scope_key: str = "main", ...) -> Path:
    path = await self._write_to_file(text, ...)
    # Incremental index: best-effort, failure must not block write path
    try:
        await self._index_entry(text, path, source_type="daily_note", scope_key=scope_key)
    except Exception:
        logger.warning("memory_index_failed", path=str(path), scope_key=scope_key)
    return path
```

#### 4.2.7 测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_memory_indexer.py` | index_daily_note: 正常索引 / 分段解析 / delete-reinsert 幂等（重复索引同一文件行数不增长） / 空文件 / scope_key 写入 / **旧数据兼容**（无 scope 元数据条目索引为 main） |
| `tests/test_memory_indexer.py` | index_curated_memory: markdown header 分段 / 空 MEMORY.md / scope_key 写入 |
| `tests/test_memory_indexer.py` | reindex_all: 全量重建 / 无文件时空操作 |
| `tests/test_memory_searcher.py` | search: 正常查询 / 中英混合 / 空结果 / limit / min_score 过滤 / source_type 过滤 / scope_key 过滤（不同 scope 不互见） |
| `tests/integration/test_memory_bm25.py` | BM25 集成测试（需 PG + pg_search）：写入 → 索引 → 搜索完整闭环 / scope 隔离验证 |
| `tests/test_memory_search_tool.py` | MemorySearchTool: 升级后 execute / 结果截断 / 空查询 / scope_key 从 context 获取 |

#### 4.2.8 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `alembic/versions/xxx_create_memory_entries.py` | 新增：memory_entries 表 + BM25 索引 |
| `src/memory/indexer.py` | 新增 |
| `src/memory/searcher.py` | 新增 |
| `src/memory/models.py` | 新增：MemoryEntry SQLAlchemy model |
| `src/memory/writer.py` | 修改：写入后增量索引 |
| `src/tools/builtins/memory_search.py` | 修改：升级为 BM25 检索 |
| `src/config/settings.py` | 修改：MemorySettings 追加检索相关字段 |
| 测试文件（6 个） | 新增 |

---

### Phase 3：Memory Curation + Prompt Recall

#### 4.3.1 目标

- 实现 `_layer_memory_recall()` 自动注入：agent 每次 turn 自动获取与当前对话相关的记忆。
- 实现 MEMORY.md 策展流程：从 daily notes 归纳持久知识。
- Phase 3 完成后，Use Case A（偏好跨天生效）和 B（历史追溯）完全达标。

#### 4.3.2 Prompt Builder：Memory Recall 注入

```python
def _layer_memory_recall(
    self,
    *,
    scope_key: str = "main",
    recent_messages: list[str] | None = None,
) -> str:
    """Inject relevant memory search results into system prompt.

    scope_key comes from build(scope_key=...) which comes from session_resolver
    (ADR 0034). Recall layer does NOT re-derive scope from session_id.

    recent_messages comes from build(recent_messages=...) which AgentLoop
    extracts from the last 3 user turns before calling build().

    Strategy:
    - Extract key terms from recent_messages (simple rule-based, no LLM call)
    - Execute MemorySearcher.search(query, scope_key=scope_key)
    - Format top results as context block

    Format:
    [Recalled Memories]
    - (2026-02-21, daily_note) User prefers concise responses...
    - (2026-02-20, curated) Project uses PostgreSQL 16...

    Constraints:
    - Max injection: memory_recall_max_tokens (default 2000)
    - Scope filtering: only entries matching scope_key are recalled (ADR 0034)
    - Skip if no results or all scores below threshold
    """
```

**调用链闭合**（Phase 0 签名 → Phase 3 实现）：

```
AgentLoop._handle_request()
  ├── identity = SessionIdentity(session_id=session_id, channel_type=..., channel_id=...)
  ├── scope_key = resolve_scope_key(identity, dm_scope=settings.session.dm_scope)
  ├── recent_msgs = [m.content for m in last_3_user_messages]
  └── prompt = prompt_builder.build(
          session_id, mode, compacted_context,
          scope_key=scope_key,              # Phase 0 已预留
          recent_messages=recent_msgs,      # Phase 0 已预留
      )
        └── _layer_memory_recall(scope_key=scope_key, recent_messages=recent_msgs)
              └── MemorySearcher.search(query, scope_key=scope_key)  # Phase 2 已可用
```

**关键设计点**：
- 提取策略先用简单规则（最近 3 轮 user 消息取关键词），不引入额外 LLM 调用。
- 注入量受 token 上限控制，避免挤占 context budget。
- Scope-aware 注入：仅召回匹配当前 scope_key 的记忆（ADR 0034）。M3 默认 `main`，行为等价之前的"仅 main session"。

#### 4.3.3 Memory Curator（策展）

```python
# src/memory/curator.py
class MemoryCurator:
    """Review daily notes and update MEMORY.md with lasting knowledge.

    Triggered by:
    - Heartbeat task (daily, per HEARTBEAT.md spec)
    - Agent explicit decision during conversation

    Workflow:
    1. Read recent daily notes (past 7 days)
    2. Identify patterns: repeated preferences, confirmed facts, key decisions
    3. Compare against current MEMORY.md content
    4. Generate update proposal (additions + removals)
    5. Apply updates to MEMORY.md
    6. Reindex MEMORY.md

    Constraints:
    - MEMORY.md max size: 4000 tokens (configurable)
    - Additions: only high-confidence, repeated patterns
    - Removals: outdated or contradicted information
    """

    async def curate(
        self,
        workspace_path: Path,
        *,
        lookback_days: int = 7,
    ) -> CurationResult:
        """Execute curation pass. Returns summary of changes."""

    async def propose_updates(
        self,
        daily_entries: list[MemoryEntry],
        current_curated: str,
    ) -> CurationProposal:
        """Generate update proposal (LLM-assisted).

        Uses low temperature for factual accuracy.
        Returns additions and removals with justifications.
        """
```

#### 4.3.4 配置扩展

```python
class MemorySettings(BaseSettings):
    # ... Phase 1 fields ...

    # Phase 3 additions
    memory_recall_max_tokens: int = 2000
    memory_recall_min_score: float = 1.0     # BM25 score threshold
    memory_recall_max_results: int = 5
    curated_max_tokens: int = 4000           # MEMORY.md size limit
    curation_lookback_days: int = 7
    curation_temperature: float = 0.1
```

#### 4.3.5 测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_prompt_memory_recall.py` | _layer_memory_recall: 正常注入 / 无结果跳过 / token 截断 / scope_key 过滤（不同 scope 不召回跨域记忆） |
| `tests/test_memory_curator.py` | curate: 正常策展 / 空 daily notes / MEMORY.md 不存在 / 超限裁剪 |
| `tests/test_memory_curator.py` | propose_updates: LLM mock / 新增+删除混合 / 无变更 |

#### 4.3.6 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `src/agent/prompt_builder.py` | 修改：`_layer_memory_recall()` 实现（scope-aware，消费 Phase 0 预留的 scope_key + recent_messages 参数） |
| `src/agent/agent.py` | 修改：build() 调用处传入 recent_messages（从最近 3 轮 user 消息提取） |
| `src/memory/curator.py` | 新增 |
| `src/config/settings.py` | 修改：MemorySettings 追加 Phase 3 字段 |
| 测试文件（2 个） | 新增 |

---

### Phase 4：Evolution Loop（SOUL.md 自我进化治理）

#### 4.4.1 目标

建立 SOUL.md "提案 → eval → 生效 → 回滚" 的完整管线。Phase 4 完成后，Use Case C（eval 通过才生效）和 D（用户 veto/rollback）达标。

#### 4.4.2 SOUL.md 版本存储

新增 DB 表：

```sql
CREATE TABLE neomagi.soul_versions (
    id          SERIAL PRIMARY KEY,
    version     INTEGER NOT NULL,          -- monotonic version number
    content     TEXT NOT NULL,              -- full SOUL.md content snapshot
    status      VARCHAR(16) NOT NULL,      -- 'active' | 'proposed' | 'superseded' | 'rolled_back' | 'vetoed'
    proposal    JSONB,                     -- proposal metadata (intent, risk, diff)
    eval_result JSONB,                     -- evaluation results
    created_by  VARCHAR(32) NOT NULL,      -- 'agent' | 'bootstrap' | 'system'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT unique_version UNIQUE (version)
);
```

#### 4.4.3 新增模块 `src/memory/evolution.py`

```python
@dataclass
class SoulProposal:
    intent: str              # what the change aims to achieve
    risk_notes: str          # potential risks
    diff_summary: str        # human-readable diff
    new_content: str         # proposed full SOUL.md content
    evidence_refs: list[str] # references to memory entries / conversation turns

@dataclass
class EvalResult:
    passed: bool
    checks: list[EvalCheck]  # individual check results
    summary: str             # human-readable eval summary

@dataclass
class EvalCheck:
    name: str
    passed: bool
    detail: str

class EvolutionEngine:
    """Manages SOUL.md lifecycle: propose → eval → apply → rollback.

    Governance rules (ADR 0027):
    - Only agent can write SOUL.md content (post-bootstrap)
    - All changes must pass eval before taking effect
    - User retains veto/rollback at any time
    - Full audit trail in soul_versions table
    """

    async def get_current_version(self) -> SoulVersion | None:
        """Get the currently active SOUL.md version from DB."""

    async def propose(self, proposal: SoulProposal) -> int:
        """Record a proposed change. Returns proposal version number.

        Does NOT apply the change. Status = 'proposed'.
        """

    async def evaluate(self, version: int) -> EvalResult:
        """Run eval checks against a proposed version.

        Checks (baseline):
        1. Anchor preservation: key anchors from AGENTS/USER still visible
        2. Constraint compliance: no violation of "user interest first"
        3. Content coherence: new content is well-formed markdown
        4. Size limit: within configured max tokens
        5. Diff sanity: changes are proportional (no full rewrite without justification)

        Returns EvalResult with per-check details.
        """

    async def apply(self, version: int) -> None:
        """Apply a proposed version that passed eval.

        Steps:
        1. Verify status == 'proposed' and eval passed
        2. Write new content to workspace/SOUL.md
        3. Update DB: new version status = 'active', old active → 'superseded'
        4. Log audit event

        Raises: EvolutionError if eval not passed or version conflict.
        """

    async def rollback(self, *, to_version: int | None = None) -> int:
        """Rollback to a previous version.

        If to_version is None, rollback to the most recent active version
        before the current one.

        Steps:
        1. Find target version
        2. Write target content to workspace/SOUL.md
        3. Create new version entry (status='active', created_by='system')
        4. Mark rolled-back version as 'rolled_back'
        5. Log audit event

        Returns: new active version number.
        """

    async def veto(self, version: int) -> None:
        """User vetoes a proposed or active version.

        If vetoed version is active → triggers rollback to previous.
        If vetoed version is proposed → marks as 'vetoed'.
        """

    async def get_audit_trail(
        self, *, limit: int = 20
    ) -> list[SoulVersion]:
        """Get version history for audit/review."""
```

#### 4.4.4 Evolution 原子工具

提供给 agent 的工具接口（遵循"原子工具路线"，ADR 0027）：

```python
# src/tools/builtins/soul_propose.py
class SoulProposeTool(BaseTool):
    """Agent proposes a SOUL.md change with intent and evidence.

    Tool 内部链式执行：propose() → evaluate() → (if passed) apply()。
    即 propose 方法本身只写 'proposed' 状态，但工具层自动串联 eval+apply。
    这样 EvolutionEngine.propose() 保持纯粹（不触发副作用），
    而工具层提供了"一键提案到生效"的便利。
    """
    name = "soul_propose"
    group = ToolGroup.memory  # 复用 memory 组
    risk_level = RiskLevel.high  # propose→eval→apply writes SOUL.md + DB (ADR 0035)
    allowed_modes = frozenset({ToolMode.chat_safe, ToolMode.coding})

    async def execute(self, arguments: dict, context: ToolContext) -> dict:
        version = await self.engine.propose(SoulProposal(...))
        eval_result = await self.engine.evaluate(version)
        if eval_result.passed:
            await self.engine.apply(version)
            return {"status": "applied", "version": version, "eval": eval_result.summary}
        return {"status": "rejected", "version": version, "eval": eval_result.summary}

# src/tools/builtins/soul_status.py
class SoulStatusTool(BaseTool):
    """Query current SOUL.md version and pending proposals."""
    name = "soul_status"
    group = ToolGroup.memory
    risk_level = RiskLevel.low  # read-only query, no side effects (ADR 0035)
    allowed_modes = frozenset({ToolMode.chat_safe, ToolMode.coding})

# src/tools/builtins/soul_rollback.py
class SoulRollbackTool(BaseTool):
    """User-triggered rollback or veto of SOUL.md changes.

    Agent 在对话中识别用户意图后调用此工具。
    这是 rollback/veto 的唯一运行时入口（ADR 0027: 用户通过对话控制）。
    """
    name = "soul_rollback"
    group = ToolGroup.memory
    risk_level = RiskLevel.high  # rollback/veto writes SOUL.md + DB (ADR 0035)
    allowed_modes = frozenset({ToolMode.chat_safe, ToolMode.coding})
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["rollback", "veto"],
                "description": "rollback: restore previous version; veto: reject a specific version",
            },
            "version": {
                "type": "integer",
                "description": "Target version number (optional for rollback, required for veto)",
            },
        },
        "required": ["action"],
    }

    async def execute(self, arguments: dict, context: ToolContext) -> dict:
        action = arguments["action"]
        version = arguments.get("version")
        if action == "veto":
            if version is None:
                return {"error": "version is required for veto action"}
            await self.engine.veto(version)
            return {"status": "vetoed", "version": version}
        else:  # rollback
            new_version = await self.engine.rollback(to_version=version)
            return {"status": "rolled_back", "new_active_version": new_version}
```

**注意**：
- `soul_propose` 工具内部链式执行 propose → eval → apply，但 `EvolutionEngine.propose()` 方法本身只创建 `proposed` 记录，不触发后续流程。链式调用由工具层负责。
- `soul_rollback` 是 rollback/veto 的运行时入口。用户在对话中表达"回滚"/"撤销"意图时，agent 调用此工具。
- `soul_status` 用于查询当前版本和待处理提案。
- 三个工具覆盖完整 CRUD 语义：propose（创建+生效）、status（读取）、rollback（回退+否决）。

#### 4.4.5 Bootstrap 协议

```python
async def ensure_bootstrap(self, workspace_path: Path) -> None:
    """Handle SOUL.md bootstrap (ADR 0027).

    If SOUL.md exists in workspace but no DB version:
    - Import current file as v0-seed (created_by='bootstrap')
    - Mark as active

    If SOUL.md does not exist:
    - Allow human to write v0-seed (this is NOT handled by code;
      user manually creates the file, next session imports it)

    After bootstrap, all changes go through propose → eval → apply.
    """
```

#### 4.4.6 测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_evolution.py` | propose: 创建提案 / 版本号递增 / 状态正确 |
| `tests/test_evolution.py` | evaluate: 全部通过 / 锚点缺失失败 / 约束违反失败 / 内容过大失败 |
| `tests/test_evolution.py` | apply: eval 通过后 apply / eval 未通过拒绝 / 文件写入验证 / 版本状态流转 |
| `tests/test_evolution.py` | rollback: 回到上一版本 / 指定版本回滚 / 无可回滚版本 |
| `tests/test_evolution.py` | veto: 对 active 版本 veto 触发 rollback / 对 proposed 版本直接标记 |
| `tests/test_evolution.py` | bootstrap: 有文件无 DB → 导入 v0-seed / 已有 DB 版本跳过 |
| `tests/test_evolution.py` | audit_trail: 完整版本链路可追溯 |
| `tests/test_evolution.py` | superseded 状态: apply 后旧 active 变为 superseded / superseded 版本不可 apply |
| `tests/test_soul_tools.py` | SoulProposeTool: 链式 propose→eval→apply / eval 失败不 apply |
| `tests/test_soul_tools.py` | SoulStatusTool: execute / 参数校验 |
| `tests/test_soul_tools.py` | SoulRollbackTool: rollback 正常 / veto 正常 / veto 缺 version 报错 |
| `tests/integration/test_evolution_e2e.py` | 端到端：propose → eval → apply → rollback 全链路（需 PG） |

#### 4.4.7 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `alembic/versions/xxx_create_soul_versions.py` | 新增：soul_versions 表 |
| `src/memory/evolution.py` | 新增 |
| `src/memory/models.py` | 修改：新增 SoulVersionRecord |
| `src/tools/builtins/soul_propose.py` | 新增 |
| `src/tools/builtins/soul_status.py` | 新增 |
| `src/tools/builtins/soul_rollback.py` | 新增：rollback/veto 用户触发入口 |
| `src/agent/agent.py` | 修改：bootstrap 检查 |
| 测试文件（3 个） | 新增 |

---

## 5. 涉及文件变更总览

| 文件 | Phase | 变更类型 | 说明 |
|------|-------|----------|------|
| `src/session/scope_resolver.py` | 0 | 新增 | SessionIdentity + resolve_scope_key + resolve_session_key |
| `src/tools/context.py` | 0 | 新增 | ToolContext dataclass |
| `src/tools/base.py` | 0 | 修改 | execute 签名扩展（context 参数）+ RiskLevel 枚举 + BaseTool.risk_level 属性（ADR 0035） |
| `src/tools/builtins/*.py` | 0 | 修改 | 现有工具 execute 签名对齐 + 每个工具显式声明 risk_level（禁止依赖默认值，ADR 0035） |
| `src/config/settings.py` | 0→3 | 修改 | SessionSettings 新增 dm_scope + MemorySettings (分 phase 追加字段) |
| `src/agent/agent.py` | 0→4 | 修改 | scope_resolver 调用 + _execute_tool 局部参数传递（并发安全）+ 主循环每轮 pre-LLM guard + pre-tool guard（risk_level 闸门）+ flush 落盘 + build() 传参 + bootstrap |
| `src/agent/guardrail.py` | 0 | 新增 | CoreSafetyContract（惰性 hash 刷新）+ GuardCheckResult + check_pre_llm_guard（检测不阻断）+ check_pre_tool_guard（risk_level 闸门）+ GUARDRAIL_ERROR_CODES（ADR 0035） |
| `src/agent/prompt_builder.py` | 0→3 | 修改 | build() 签名扩展 + daily notes 加载 + memory recall 注入 |
| `src/memory/__init__.py` | 1 | 修改 | 导出 |
| `src/memory/contracts.py` | 1 | 新增 | ResolvedFlushCandidate 共享契约（Agent 层构造、Memory 层消费） |
| `src/memory/writer.py` | 1→2 | 新增 | 记忆写入 + 增量索引（scope-aware） |
| `src/memory/indexer.py` | 2 | 新增 | 文件 → DB 索引同步（delete-reinsert 策略） |
| `src/memory/searcher.py` | 2 | 新增 | BM25 检索（scope-aware） |
| `src/memory/models.py` | 2→4 | 新增 | MemoryEntry + SoulVersionRecord |
| `src/memory/curator.py` | 3 | 新增 | daily notes → MEMORY.md 策展 |
| `src/memory/evolution.py` | 4 | 新增 | SOUL.md 进化管线 |
| `src/tools/builtins/memory_append.py` | 1 | 新增 | 记忆写入工具 |
| `src/tools/builtins/memory_search.py` | 2 | 修改 | 升级为 BM25（scope-aware via context） |
| `src/tools/builtins/soul_propose.py` | 4 | 新增 | SOUL 提案工具（链式 propose→eval→apply） |
| `src/tools/builtins/soul_status.py` | 4 | 新增 | SOUL 状态查询工具 |
| `src/tools/builtins/soul_rollback.py` | 4 | 新增 | SOUL rollback/veto 用户触发入口 |
| `alembic/versions/xxx_create_memory_entries.py` | 2 | 新增 | memory_entries 表 + BM25 索引 |
| `alembic/versions/xxx_create_soul_versions.py` | 4 | 新增 | soul_versions 表（status 含 superseded） |
| `tests/test_guardrail.py` | 0 | 新增 | CoreSafetyContract（含惰性刷新）/ pre-LLM guard（检测不阻断）/ pre-tool guard（risk_level 闸门）/ guard_state 每轮刷新 / 审计日志断言 |
| 测试文件 (~24 个) | 0-4 | 新增 | 详见各 Phase |

## 6. 不做什么（Out of Scope）

- **Hybrid Search (pgvector)**：BM25 可满足 M3 验收。pgvector 向量检索作为后续质量增强迭代，不阻塞 M3 完成。
- **重型知识图谱**：不做实体抽取、关系建模。
- **全自动记忆去重**：M3 依赖策展阶段人工/LLM 合并，不做写入时精确去重。
- **多 session 记忆合并**：M3 运行时默认 scope_key='main'，不处理跨 scope 记忆迁移或合并（归 M4 渠道联调）。
- **非 main 作用域激活**：M3 接口层已 scope-aware（ADR 0034），但运行时仅使用默认 `main`；非 `main` scope 的激活与联调归 M4。
- **Scope-local 策展层**：M3 的 MEMORY.md 策展仅操作全局层（因为 M3 只激活 `main`）。目标模型为"分层策展"：全局层（MEMORY.md，跨 scope 通用）+ 作用域层（scope-local curated）。作用域层的落地归 M4 非 main scope 激活时实现。
- **运行时漂移检测（实时 Probe 评测平台）**：不做实时 Probe 评测平台（M5 触发式运营能力范畴）。但**做最小 runtime guardrail**（Core Safety Contract + 风险分级 fail-closed），以覆盖 M2 风险回补（ADR 0035）。
- **SOUL.md 人类直接编辑路径**：ADR 0027 明确 AI-only 写入，人类通过 veto/rollback 控制。
- **复杂权限 RBAC**：Evolution eval 使用规则检查，不引入多角色审批。

## 7. 验收标准对齐（来自 roadmap）

- **用例 A**：用户前一天明确的偏好，第二天无需重复输入即可生效。
  - 验证路径：Phase 1 daily notes 持久化 + Phase 1 daily notes 自动加载 + Phase 2 memory_search 检索 + Phase 3 prompt recall 自动注入。
  - 测试：跨 session 偏好持久化端到端测试。

- **用例 B**：用户追问历史决策原因，agent 能基于记忆给出可追溯回答。
  - 验证路径：Phase 2 BM25 检索 + Phase 3 prompt recall。
  - 测试：写入决策 → 新 session → memory_search 命中 + prompt 注入验证。

- **用例 C**：agent 提出 SOUL.md 更新后，只有通过评测的变更才会生效；失败可自动回滚。
  - 验证路径：Phase 4 propose → eval → apply（eval 失败 → 不生效）。
  - 测试：eval 通过 → apply + eval 失败 → reject 端到端测试。

- **用例 D**：用户可对已生效变更执行 veto/rollback，系统恢复到上一个稳定版本并保留审计记录。
  - 验证路径：Phase 4 veto/rollback + audit trail。
  - 测试：apply → user veto → rollback → audit trail 验证。

- **用例 E（M3-E 接口/数据面级验收）**：scope 契约正确且 fail-fast 防护生效。
  - **E-1 接口契约测试（scope fixture 注入，非生产配置）**：
    - 写入 scope_key='main' 的记忆 → 以 scope_key='peer:alice' 检索 → 不命中；以 scope_key='main' 检索 → 命中。
    - 验证 scope_key 过滤在 memory_search、recall、index **三处一致生效**。
    - **注入方式**：测试通过 fixture 直接构造 `ToolContext(scope_key='peer:alice')` 和对应 `memory_entries` 行，**绕过 scope_resolver**。这是数据面契约测试，不是运行配置测试。
  - **E-2 fail-fast guardrail 测试**：
    - `SESSION_DM_SCOPE=per-peer` 启动 → `SessionSettings` validator 抛 `ValueError`，进程启动失败。
    - `resolve_scope_key(identity, dm_scope='per-peer')` → 抛 `ValueError`。
    - 验证非 main 值在 M3 **无法穿透**到运行时。
  - **强约束**：M3-E **不证明**非 main 作用域已在生产路径激活；仅证明作用域契约正确 + fail-fast 防护生效。
  - **完整口径**：M3 = scope 契约 + fail-fast 防护。M4 = 非 main 在真实渠道路径的激活与 E2E 隔离。
  - **M4-E（渠道级 E2E 验收，归 M4）**：非 main 激活与跨渠道映射仅在 M4-E 判定完成。Telegram/多渠道下 identity → scope 的真实映射和隔离行为端到端验证。M3 不做。

- **用例 F（Runtime Guardrail 验收，ADR 0035）**：`risk_level=high` 的工具在 guard 失败时被阻断，`risk_level=low` 的工具可降级继续，审计日志可查。
  - 验证路径：Phase 0 guardrail 实现 + Phase 0 退出门槛。
  - **F-1 高风险阻断**：guard 失败时，`risk_level=high` 的工具（如 `memory_append`、`soul_propose`、`soul_rollback`）执行被阻断，返回结构化错误码 `GUARD_ANCHOR_MISSING` 或 `GUARD_CONTRACT_UNAVAILABLE`。
  - **F-2 低风险降级**：guard 失败时，`risk_level=low` 的工具（如 `memory_search`、`soul_status`）及纯对话路径可降级继续，记录 `guardrail_degraded` 审计日志。
  - **F-3 审计可查**：`guardrail_blocked`、`guardrail_degraded` 和 `guardrail_warning` 事件在 structlog 输出中可见，包含 error_code、missing_anchors、tool_name、risk_level 等关键字段。
  - **F-4 每轮刷新**：多轮 tool loop 场景下，每次 LLM 调用前产生新 guard_state，不复用前轮结果。
  - 测试：`tests/test_guardrail.py` 覆盖 F-1/F-2/F-3/F-4 全部场景。

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| ParadeDB pg_search 版本/tokenizer 兼容性 | BM25 索引不可用 | Phase 2 前做 spike 验证；不可用时 fallback 到 PostgreSQL 原生 `tsvector` + `to_tsvector('simple', ...)` |
| Daily notes 文件累积过大 | prompt 注入 token 膨胀 | 按文件 truncation + 策展机制定期归纳 |
| Memory recall 质量低（BM25 召回不准） | 注入无关信息浪费 context | min_score 阈值 + max_results 限制 + 后续 Hybrid 增强 |
| Evolution eval 规则过简，漏放有害变更 | SOUL.md 质量退化 | 保守策略：规则检查为必要条件；用户保留 veto/rollback 作为最终防线 |
| SOUL.md bootstrap 与常态写入边界模糊 | 治理混乱 | 明确 bootstrap 一次性语义 + DB 版本号跟踪 |
| Flush 候选质量低（M2 rule-based 提取） | 记忆噪音多 | confidence 阈值过滤（默认 0.5）+ 策展阶段清理 |
| ToolContext 签名改造影响现有测试 | 现有工具测试需同步更新 | Phase 0 一次性对齐所有工具签名（数量少，≤ 5 个），同步修复测试 |
| 并发场景下 scope_key 串话 | 会话 A 工具拿到会话 B 的 scope | scope_key 作为 _execute_tool 显式参数传递，不存实例字段；Phase 0 补并发隔离测试 |
| scope_resolver 实现缺失导致集成断裂 | 全部依赖 scope_key 的模块无法闭合 | Phase 0 明确 scope_resolver 模块定义、文件位置、输入输出签名 |
| Evolution 状态机复杂度（5 种状态） | 状态流转 bug | 严格测试状态流转矩阵 + audit trail 完整覆盖 |
| Guardrail 误拦截（false positive） | 正常高风险工具被错误阻断，影响可用性 | 锚点清单从工作区文件动态提取（非硬编码），可随 SOUL.md 演进调整；risk_level 工具级声明避免按组一刀切误伤；Phase 0 退出门槛含 false positive 回归测试 |
| Guardrail 漏拦截（false negative） | 高风险工具在 guard 失效时仍被执行 | 高风险路径 fail-closed（ADR 0035）；contract 不可用时直接阻断（GUARD_CONTRACT_UNAVAILABLE）；审计日志全量记录供事后追溯 |

## 9. 前置条件与 Spike 清单

在正式实施前需确认：

1. **现有工具清单**：盘点当前 `src/tools/builtins/*.py` 中所有已实现工具，确认 Phase 0 签名改造的完整影响范围。
2. **ParadeDB pg_search spike**：在目标 PG 实例验证 pg_search 版本、可用 tokenizer 列表、BM25 索引创建语法。
3. **Workspace 写入权限**：确认 agent 运行时对 `workspace/memory/` 有写入权限。
4. **pgvector 扩展状态**：确认 pgvector 已安装（M3 不使用但需确认可用性，为后续 Hybrid 预留）。
5. **SOUL.md 当前内容**：确认当前 workspace 的 SOUL.md 是否已有 v0-seed 内容。

## 10. M3/M6 衔接说明

M3 完成后，检索路径已通过 BM25 闭环。后续衔接：
- **Hybrid Search（M3 后续迭代或独立优化）**：引入 pgvector 向量检索，与 BM25 做 weighted score fusion。需要 embedding 生成能力（Ollama 本地 → OpenAI fallback）。
- **M6 模型迁移**：Memory recall 中的 keyword 提取和 curation 的 LLM 调用需要在 M6 做模型迁移验证。
