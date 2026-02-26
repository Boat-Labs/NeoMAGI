# M6 模型迁移验证 实现计划

> 状态：approved
> 日期：2026-02-25
> 依据：`design_docs/m6_architecture.md`、`design_docs/roadmap_milestones_v3.md`、ADR 0002/0016/0038/0039/0040/0041

## 1. 目标

用户不被单一模型供应商绑定，关键任务可迁移且可回退。

核心交付：
- **Multi-Provider 配置层**：支持 OpenAI + Gemini 并存配置，启动时为每个已配置 provider 预建常驻 AgentLoop。
- **Agent-run 级 provider 绑定**（ADR 0040）：`ChatSendParams` 新增可选 `provider` 字段（向后兼容），每次 `chat.send` 开始时通过 AgentLoopRegistry 路由到对应 provider 的 AgentLoop，本次 run 内 provider 不变。路由优先级：`params.provider > PROVIDER_ACTIVE`。同一会话相邻请求可使用不同 provider。
- **模型策略分层**：在线主链路（chat / tool loop / compaction）跟随 provider 路由结果（`params.provider > PROVIDER_ACTIVE`）；离线 curation 独立配置，默认低成本模型。
- **成本治理**（ADR 0041）：全 provider 统一预算池（€20 warn / €25 stop），任一 provider 调用前原子预占、调用后对账，基于 PostgreSQL 多 worker 安全。审计记录同时保留全局累计与 provider 分项。
- **Gemini 兼容性适配**：通过 OpenAI-compatible endpoint 验证 Gemini 在核心链路（chat、streaming、tool calls）上的行为一致性，识别并处理差异。
- **代表性任务评测**：同一组任务在 OpenAI (`gpt-5-mini`) 和 Gemini (`gemini-2.5-flash`) 上均可完成，产出可复现的迁移结论。
- **切换与回退策略**：per-run 通过 `provider` 字段切换；全局默认通过 `PROVIDER_ACTIVE` + 重启切换。质量下降时可快速回退。

## 2. 设计决策汇总

| 决策项 | 选择 | ADR |
|--------|------|-----|
| 默认模型路线 | OpenAI default, Gemini 做迁移验证 | 0002 |
| SDK 策略 | 统一 `openai` SDK，OpenAI/Gemini/Ollama 通过 OpenAI-compat 接入 | 0016 |
| Gemini 主验证模型 | `gemini-2.5-flash`（smoke 可选 `gemini-2.5-flash-lite`） | 0038 |
| OpenAI 开发测试模型 | `gpt-5-mini` | 0039 |
| 验证预算上限 | €30（硬约束：€25 stop / €20 warn） | 0038 |
| Provider 绑定粒度 | Agent-run 级绑定：`ChatSendParams.provider`（可选）> `PROVIDER_ACTIVE`（默认） | 0040 |
| 模型策略分层 | 在线主链路跟随 provider 路由结果；离线 curation 独立配置 | 本计划 |
| 成本治理 | 全 provider 统一预算池 + PG 原子预占/对账 + 全局累计与 provider 分项 | 0038/0041 |
| 配置管理 | `pydantic-settings` + `.env`，每个 provider 独立 env prefix | 0013 |

## 3. 当前基线分析

### 3.1 可直接复用

- `ModelClient` ABC 与 `OpenAICompatModelClient` 已支持 `base_url` 覆盖，Gemini OpenAI-compat endpoint 可直接接入。
- Token 计数 `TokenCounter` 对未知模型自动回退 `chars/4` 估算，Gemini 场景可用。
- `src/gateway/protocol.py` RPC 协议完全 provider-agnostic。
- `AgentLoop` 通过构造函数注入 `ModelClient`，天然支持 provider 切换。

### 3.2 需要适配

| 问题 | 严重度 | 位置 | 说明 |
|------|--------|------|------|
| ChatSendParams 无 provider 字段 | HIGH | `src/gateway/protocol.py` | 不支持 per-run provider 选择 |
| 配置层仅支持单 provider | HIGH | `src/config/settings.py` | 仅有 `OpenAISettings`，无 Gemini 独立配置 |
| Gateway 硬编码单 client | HIGH | `src/gateway/app.py:70-73` | `lifespan` 只创建一个 `OpenAICompatModelClient` |
| AgentLoop model 默认值 | MEDIUM | `src/agent/agent.py:184` | 默认 `model="gpt-4o-mini"` |
| Curator 硬编码 model | MEDIUM | `src/memory/curator.py:168` | `model="gpt-4o-mini"` 不接受外部参数 |
| Curator temperature 参数 | LOW | `src/memory/curator.py:169` | 传了 `temperature` 但 `chat_stream_with_tools` 签名不接受此参数（潜在 bug） |
| Streaming tool_calls 格式 | MEDIUM | `src/agent/model_client.py:283-295` | 依赖 OpenAI 的 `delta.tool_calls[].index/function` 格式，Gemini 可能有差异 |

### 3.3 无需变更

- `EvolutionEngine`：纯 DB 操作，不调用 ModelClient。
- `MemorySearcher` / `MemoryIndexer`：SQL 查询，无模型依赖。
- `MemoryWriter`：文件操作，无模型依赖。
- `ToolRegistry` / `BaseTool`：工具执行框架，provider-agnostic。

## 4. Phase 0：Multi-Provider 配置层

### 4.0 Gate
- Phase 0 完成后：`just test` 全绿，新配置可加载，provider 枚举可验证。

### 4.1 新增 `GeminiSettings`

```python
# src/config/settings.py

class GeminiSettings(BaseSettings):
    """Gemini API settings via OpenAI-compatible endpoint. Env vars prefixed with GEMINI_."""

    model_config = SettingsConfigDict(env_prefix="GEMINI_")

    api_key: str = ""  # empty = provider disabled
    model: str = "gemini-2.5-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
```

设计要点：
- `api_key` 默认空字符串：空则表示该 provider 未启用，启动不报错。
- `base_url` 默认 Google OpenAI-compat endpoint，可覆盖（如用第三方中转）。
- `model` 默认 `gemini-2.5-flash`，与 ADR 0038 一致。

### 4.2 新增 `ProviderSettings`

```python
class ProviderSettings(BaseSettings):
    """Provider routing settings. Env vars prefixed with PROVIDER_."""

    model_config = SettingsConfigDict(env_prefix="PROVIDER_")

    active: str = "openai"  # fallback when ChatSendParams.provider is not specified

    @field_validator("active")
    @classmethod
    def _validate_active(cls, v: str) -> str:
        allowed = {"openai", "gemini"}
        if v not in allowed:
            raise ValueError(
                f"PROVIDER_ACTIVE must be one of {allowed} (got '{v}')"
            )
        return v
```

### 4.3 更新 `Settings` root

```python
class Settings(BaseSettings):
    # ... 现有字段 ...
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
```

### 4.4 更新 `.env_template`

```bash
# Provider routing
PROVIDER_ACTIVE=openai  # "openai" | "gemini"

# Gemini (OpenAI-compatible)
GEMINI_API_KEY=
# GEMINI_MODEL=gemini-2.5-flash
# GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

### 4.5 测试

- `tests/test_config_provider.py`：
  - 默认 active=openai 合法
  - active=gemini 合法
  - active=unknown 抛 ValidationError
  - GeminiSettings 空 api_key 不报错
  - GeminiSettings 有 api_key 正常加载

## 5. Phase 1：AgentLoopRegistry & Agent-run 级路由

### 5.0 Gate
- Phase 1 完成后：Gateway 为每个已配置 provider 预建常驻 AgentLoop；`chat.send` 支持 `provider` 字段实现 per-run 路由（相邻请求可使用不同 provider），未指定时回退 `PROVIDER_ACTIVE`；所有 provider 路由统一经 PG 原子预算闸门（ADR 0041）；全量测试绿。

### 5.1 新增 `AgentLoopRegistry`

Registry 持有预建的 AgentLoop 实例（而非裸 ModelClient），每个已配置 provider 在启动时完成完整初始化（bootstrap、contract load、budget tracker），消除运行时状态分叉。Gateway 在每次 `chat.send` 时通过 `registry.get(params.provider)` 做 agent-run 级路由（`params.provider` 非空 → 指定 provider；`None` → `PROVIDER_ACTIVE` 默认值）。

```python
# src/agent/provider_registry.py

@dataclass
class ProviderEntry:
    """A registered model provider with its fully initialized AgentLoop."""
    name: str
    agent_loop: AgentLoop
    model: str  # provider default model (for logging/reporting)

class AgentLoopRegistry:
    """Registry of per-provider AgentLoop instances.

    Created at startup; holds pre-initialized, stateful AgentLoops.
    Thread-safe for read (no mutation after init).
    Gateway does per-request lookup via get(params.provider) for agent-run level routing.
    """

    def __init__(self, default_provider: str) -> None:
        self._providers: dict[str, ProviderEntry] = {}
        self._default = default_provider

    def register(self, name: str, agent_loop: AgentLoop, model: str) -> None:
        self._providers[name] = ProviderEntry(
            name=name, agent_loop=agent_loop, model=model
        )

    def get(self, name: str | None = None) -> ProviderEntry:
        """Get provider by name, or default if None.
        Raises KeyError if not found or not configured.
        """
        key = name or self._default
        if key not in self._providers:
            raise KeyError(f"Provider '{key}' not registered or not configured")
        return self._providers[key]

    @property
    def default_name(self) -> str:
        return self._default

    def available_providers(self) -> list[str]:
        return list(self._providers.keys())
```

### 5.2 更新 Gateway `lifespan`

启动时为每个已配置 provider 预建常驻 AgentLoop，共享 provider-agnostic 依赖。Gateway 不缓存单个 agent_loop，改为持有 registry，每次 `chat.send` 通过 registry 做 agent-run 级 lookup。

```python
# src/gateway/app.py lifespan 改动

# Provider-agnostic shared deps（与现有逻辑一致）
session_manager = SessionManager(...)
tool_registry = ToolRegistry()
register_builtins(tool_registry, ...)
memory_searcher = MemorySearcher(...)
evolution_engine = EvolutionEngine(...)

# Helper: create AgentLoop for a given provider
def _make_agent_loop(client: OpenAICompatModelClient, model: str) -> AgentLoop:
    return AgentLoop(
        model_client=client,
        session_manager=session_manager,
        workspace_dir=settings.workspace_dir,
        model=model,
        tool_registry=tool_registry,
        compaction_settings=settings.compaction,
        session_settings=settings.session,
        memory_settings=settings.memory,
        memory_searcher=memory_searcher,
        evolution_engine=evolution_engine,
    )

# Build registry
registry = AgentLoopRegistry(default_provider=settings.provider.active)

# OpenAI（始终注册，api_key 为 required）
openai_client = OpenAICompatModelClient(
    api_key=settings.openai.api_key,
    base_url=settings.openai.base_url,
)
registry.register("openai", _make_agent_loop(openai_client, settings.openai.model),
                   settings.openai.model)

# Gemini（仅在 api_key 非空时）
if settings.gemini.api_key:
    gemini_client = OpenAICompatModelClient(
        api_key=settings.gemini.api_key,
        base_url=settings.gemini.base_url,
    )
    registry.register("gemini", _make_agent_loop(gemini_client, settings.gemini.model),
                       settings.gemini.model)
    logger.info("gemini_provider_registered", model=settings.gemini.model)

# 验证 active provider 已注册（fail-fast）
try:
    registry.get()  # validate default is available
except KeyError as e:
    raise RuntimeError(
        f"Active provider '{settings.provider.active}' is not configured. "
        "Check API key settings."
    ) from e

# Gateway 持有 registry，不缓存单个 agent_loop
app.state.agent_loop_registry = registry
app.state.session_manager = session_manager
```

**`_handle_chat_send` 路由改动**（agent-run 级绑定 + 错误映射 + 预算闸门）：

```python
# src/gateway/app.py _handle_chat_send 改动

# 1. 参数校验 — ValidationError → GatewayError(INVALID_PARAMS)
try:
    parsed = ChatSendParams.model_validate(params)
except ValidationError as e:
    raise GatewayError(str(e), code="INVALID_PARAMS") from e

# 2. Agent-run 级 provider 路由 — KeyError → GatewayError(PROVIDER_NOT_AVAILABLE)
registry: AgentLoopRegistry = websocket.app.state.agent_loop_registry
try:
    entry = registry.get(parsed.provider)
except KeyError:
    raise GatewayError(
        f"Provider '{parsed.provider}' is not available. "
        f"Configured: {registry.available_providers()}",
        code="PROVIDER_NOT_AVAILABLE",
    )

# 3. eval_run_id 推导 — session_id 前缀约定（不扩展 RPC 协议）
#    eval 脚本使用 session_id = "m6_eval_{provider}_{task}_{ts}"
#    网关从前缀提取 eval_run_id；普通请求为空串
eval_run_id = _extract_eval_run_id(parsed.session_id)

# 4. 统一预算闸门 — 全 provider 原子预占（ADR 0041，详见 §5.4）
budget_gate: BudgetGate = websocket.app.state.budget_gate
reservation = await budget_gate.try_reserve(
    provider=entry.name, model=entry.model,
    estimated_cost_eur=estimated_cost,
    session_id=parsed.session_id,
    eval_run_id=eval_run_id,
)
if reservation.denied:
    raise GatewayError(reservation.message, code="BUDGET_EXCEEDED")

agent_loop = entry.agent_loop
logger.info("agent_run_provider_bound",
            provider=entry.name, model=entry.model,
            source="request" if parsed.provider else "default")

# 后续使用 agent_loop 处理本次 run
# parsed.content（非 message）传入 agent_loop.handle_message
```

**`eval_run_id` 推导约定**（session_id 前缀，不扩展 RPC 协议）：

```python
# src/gateway/app.py

_EVAL_SESSION_PREFIX = "m6_eval_"

def _extract_eval_run_id(session_id: str) -> str:
    """Derive eval_run_id from session_id prefix convention.

    Eval script uses session_id = "m6_eval_{provider}_{task}_{timestamp}".
    Timestamp is always the last '_'-separated segment (numeric epoch).
    Provider is always the 3rd segment (index 2).
    Extract "m6_eval_{provider}_{timestamp}" as eval_run_id.
    Online requests (session_id = "main" or other) return empty string.
    """
    if not session_id.startswith(_EVAL_SESSION_PREFIX):
        return ""
    # "m6_eval_{provider}_{task…}_{timestamp}" — timestamp is always last segment
    parts = session_id.split("_")
    if len(parts) >= 5:
        provider = parts[2]       # always 3rd segment
        timestamp = parts[-1]     # always last segment (robust to _ in task_id)
        return f"m6_eval_{provider}_{timestamp}"
    return session_id  # fallback: use full session_id as run_id
```

设计要点：
- **不扩展 RPC 协议**：`eval_run_id` 是评测基础设施内部概念，不属于用户面。通过 session_id 前缀约定在网关层推导，避免 `ChatSendParams` 增加仅评测用的字段。
- **eval 脚本已有约定**：每个 task 使用 `session_id = "m6_eval_{provider}_{task_id}_{timestamp}"`，同一次 eval run 内所有 task 共享相同的 `{provider}_{timestamp}` 组合。
- **在线路径不受影响**：session_id 不以 `m6_eval_` 开头时返回空串，与现有行为一致。

**错误映射闭环**：`_handle_chat_send` 内的 `GatewayError` 被外层 `except NeoMAGIError` 捕获（`app.py:165`），直接映射为对应 `e.code` 的 RPC error。无异常逃逸到 `except Exception` → 不会产生误导性的 `INTERNAL_ERROR`。

### 5.3 Agent-run 级路由：`ChatSendParams.provider` 字段

**`ChatSendParams` 新增可选 `provider` 字段**（向后兼容的协议扩展）：

```python
# src/gateway/protocol.py ChatSendParams 新增字段（保留现有 content + session_id）

class ChatSendParams(BaseModel):
    content: str
    session_id: str = "main"
    provider: str | None = None  # optional: route to specific provider

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError(f"provider must be a string (got {type(v).__name__})")
        v = v.strip().lower()
        if not v:
            return None  # empty string → use default
        return v
```

**路由优先级（全文统一）**：`params.provider > PROVIDER_ACTIVE`

| 场景 | `params.provider` | 路由结果 |
|------|-------------------|----------|
| 未指定（None / 空串 / 省略） | `None` | `PROVIDER_ACTIVE` 默认 provider |
| 指定已注册 provider | `"gemini"` | gemini AgentLoop |
| 指定未注册 provider | `"claude"` | RPC error `PROVIDER_NOT_AVAILABLE` |

**`provider` 字段校验契约**：

| 输入 | 处理 | 错误码 |
|------|------|--------|
| `None` / 省略 / `""` | 回退 `PROVIDER_ACTIVE` | — |
| 合法小写字符串（`"openai"`, `"gemini"`） | 正常路由 | — |
| 大小写混合（`"Gemini"`） | normalize 为小写后路由 | — |
| 已注册但 api_key 为空（未配置） | 启动时未注册 | `PROVIDER_NOT_AVAILABLE`（附 available list） |
| 未注册字符串 | `registry.get()` → `KeyError` | `PROVIDER_NOT_AVAILABLE`（附 available list） |
| 非字符串类型 | Pydantic validator 拦截 | `INVALID_PARAMS` |

**错误码 → RPC 映射路径**：`_handle_chat_send` 显式 `try/except` 将 `ValidationError` → `GatewayError(code="INVALID_PARAMS")`、`KeyError` → `GatewayError(code="PROVIDER_NOT_AVAILABLE")`。外层 `except NeoMAGIError` 将 `e.code` 直接写入 `RPCError.error.code`。不会落到 `except Exception` 的 `INTERNAL_ERROR`（见 §5.2 路由代码）。

**Provider 默认值切换**：改 `PROVIDER_ACTIVE` env var + 重启 Gateway，影响所有未携带 `provider` 的请求。

**M6 不实现的能力**：
- 不实现基于任务分类/预算策略的自动路由逻辑（后续按需在 `_handle_chat_send` 入口扩展）。

### 5.4 统一预算闸门（ADR 0041：全 provider + PG 原子 + 多 worker 安全）

所有 provider 路由统一经过预算闸门，不允许任何 provider 绕过（ADR 0041）。

**5.4.1 核心规则（仅三条）**

1. **warn + stop 两个阈值**：€20 warn / €25 stop。
2. **任一 provider 调用前原子预占预算**：预占成功才放行，预占失败直接 `BUDGET_EXCEEDED`。
3. **调用结束做对账**：多退少补 + 审计记录（含 provider 分项）。

**5.4.2 PostgreSQL Schema**

```sql
-- alembic migration: add budget tables
-- Note: gen_random_uuid() is a core built-in (no pgcrypto needed).
-- Project baseline: PostgreSQL 16 (ADR 0006).

CREATE TABLE budget_state (
    id TEXT PRIMARY KEY DEFAULT 'global',
    cumulative_eur NUMERIC(10,4) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO budget_state (id) VALUES ('global') ON CONFLICT DO NOTHING;

CREATE TABLE budget_reservations (
    reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    eval_run_id TEXT NOT NULL DEFAULT '',   -- non-empty for eval runs, empty for online
    reserved_eur NUMERIC(10,4) NOT NULL,
    actual_eur NUMERIC(10,4),              -- NULL until settled
    status TEXT NOT NULL DEFAULT 'reserved',  -- 'reserved' | 'settled'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    settled_at TIMESTAMPTZ
);
CREATE INDEX idx_budget_reservations_status ON budget_reservations (status)
    WHERE status = 'reserved';
```

**5.4.3 BudgetGate 实现（PostgreSQL 原子语义）**

```python
# src/gateway/budget_gate.py

BUDGET_WARN_EUR = 20.0
BUDGET_STOP_EUR = 25.0

@dataclass
class Reservation:
    denied: bool
    message: str = ""
    reservation_id: str = ""
    reserved_eur: float = 0.0

class BudgetGate:
    """All-provider budget gate with PostgreSQL atomic semantics (ADR 0041).

    Multi-worker safe: uses PG row-level locking, not in-memory locks.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def try_reserve(
        self, *, provider: str, model: str,
        estimated_cost_eur: float, session_id: str = "",
        eval_run_id: str = "",
    ) -> Reservation:
        """Atomic: check global budget + reserve estimated cost in one PG transaction.

        PG READ COMMITTED + row-level lock on budget_state ensures
        concurrent requests are serialized and cannot oversell.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Atomic: UPDATE with WHERE guard; PG row lock serializes concurrent attempts
                row = await conn.fetchrow("""
                    UPDATE budget_state
                    SET cumulative_eur = cumulative_eur + $1, updated_at = NOW()
                    WHERE id = 'global'
                      AND cumulative_eur + $1 < $2
                    RETURNING cumulative_eur
                """, estimated_cost_eur, BUDGET_STOP_EUR)

                if row is None:
                    # Budget exceeded — read current for error message
                    current = await conn.fetchval(
                        "SELECT cumulative_eur FROM budget_state WHERE id = 'global'"
                    )
                    return Reservation(
                        denied=True,
                        message=f"Budget exceeded (cumulative €{current:.2f} "
                                f"+ estimated €{estimated_cost_eur:.2f} "
                                f">= stop €{BUDGET_STOP_EUR}).",
                    )

                # Record reservation
                rid = await conn.fetchval("""
                    INSERT INTO budget_reservations
                        (provider, model, session_id, eval_run_id, reserved_eur, status)
                    VALUES ($1, $2, $3, $4, $5, 'reserved')
                    RETURNING reservation_id
                """, provider, model, session_id, eval_run_id, estimated_cost_eur)

                cumulative = float(row["cumulative_eur"])
                if cumulative >= BUDGET_WARN_EUR:
                    logger.warning("budget_warning",
                                   cumulative_eur=cumulative,
                                   provider=provider)

                return Reservation(
                    denied=False,
                    reservation_id=str(rid),
                    reserved_eur=estimated_cost_eur,
                )

    async def settle(
        self, *, reservation_id: str,
        actual_cost_eur: float,
        input_tokens: int = 0, output_tokens: int = 0,
    ) -> None:
        """Idempotent post-call reconciliation: CAS flip reservation first,
        only adjust budget_state if flip succeeds. Duplicate settle is a no-op.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # CAS: atomically flip reserved → settled; returns row only on first call
                settled_row = await conn.fetchrow("""
                    UPDATE budget_reservations
                    SET actual_eur = $1, status = 'settled', settled_at = NOW()
                    WHERE reservation_id = $2 AND status = 'reserved'
                    RETURNING reserved_eur
                """, actual_cost_eur, reservation_id)

                if settled_row is None:
                    # Already settled or unknown — idempotent no-op
                    return

                # Only adjust cumulative when CAS succeeded (多退少补)
                diff = actual_cost_eur - float(settled_row["reserved_eur"])
                await conn.execute("""
                    UPDATE budget_state
                    SET cumulative_eur = cumulative_eur + $1, updated_at = NOW()
                    WHERE id = 'global'
                """, diff)
```

**调用时序**（在 `_handle_chat_send` 中，finally 保证 settle）：
```
1. reservation = try_reserve(provider, model, estimated_cost)  ← PG 原子预占
2. try:
       agent_loop.handle_message(...)                          ← 实际 API 调用
   finally:
       settle(reservation_id, actual_cost)                     ← 对账：多退少补（reserved_eur 从 DB 读取）
```

**多 worker 安全保证**：`UPDATE budget_state ... WHERE cumulative_eur + $1 < $2` 在 PG READ COMMITTED 下天然串行化 — 并发事务竞争同一行时，后到者等前者 commit 后重新评估 WHERE 条件。不依赖进程内锁，多 worker 部署下行为一致（ADR 0041 + ADR 0021）。

### 5.5 修复 Curator 硬编码 model + Curation 模型独立配置

Curator 当前硬编码 `model="gpt-4o-mini"`。改为参数化，并支持通过 `MemorySettings` 独立配置 curation 模型，不绑定 provider 路由。

```python
# src/memory/curator.py
# 改动：__init__ 增加 model 参数

def __init__(
    self,
    model_client: ModelClient,
    settings: MemorySettings,
    indexer: MemoryIndexer | None = None,
    *,
    model: str | None = None,  # 新增：None 则读 settings.curation_model
) -> None:
    self._model_client = model_client
    self._settings = settings
    self._indexer = indexer
    self._model = model or settings.curation_model

# curate 方法中使用 self._model 替代硬编码
```

`MemorySettings` 新增：

```python
# src/config/settings.py MemorySettings 新增字段
curation_model: str = "gpt-4o-mini"  # 离线 curation 模型，独立于 provider 路由
```

**模型策略分层原则**：

| 路径 | 策略 | 原因 |
|------|------|------|
| 在线主链路（chat / tool loop） | 跟随 provider 路由结果（`params.provider > PROVIDER_ACTIVE`） | 用户可选，评测结论干净 |
| Compaction（摘要生成） | 跟随 provider 路由结果 | 属于在线链路，与 agent-run 同 provider |
| Curation（离线策展） | `MEMORY_CURATION_MODEL` 独立配置 | 后台批处理，不影响实时评测，优先控成本 |

### 5.6 ModelClient 接口补全：`temperature` 参数

`ModelClient.chat_stream_with_tools` 当前不接受 `temperature` 参数，但 `chat()` 已有。这是接口完整性遗漏（非 provider-specific 扩展），Curator 调用 `chat_stream_with_tools(... temperature=...)` 会触发 TypeError。

**修正**：在 `ModelClient` ABC 和 `OpenAICompatModelClient` 的 `chat_stream_with_tools` 签名中补全 `temperature: float | None = None`，与 `chat()` 对齐。同步补全 `chat_stream()` 和 `chat_completion()` 的 temperature 参数（保持接口一致性）。

> 注：此为接口补全，不改变抽象层次。Phase 2 中"不新增 provider-specific 方法"是指不加 `gemini_grounding()` 之类的 provider 特有方法。

### 5.7 测试

- `tests/test_provider_registry.py`：
  - 注册与获取 provider（含 AgentLoop）
  - 获取默认 provider
  - 获取未注册 provider → KeyError
  - available_providers 列表
- `tests/test_gateway_provider.py`：
  - 默认 provider 启动正常
  - Gemini 未配置时仅 OpenAI 可用
  - active provider 不可用时 fail-fast
  - `provider=None` → 使用 PROVIDER_ACTIVE 默认 loop
  - `provider="gemini"` → 使用 gemini loop
  - `provider="Gemini"` → normalize 为小写后路由
  - `provider=""` → 等同 None，使用默认
  - `provider="unknown"` → RPC error `PROVIDER_NOT_AVAILABLE`（附 available list）
  - `provider=123`（非字符串） → RPC error `INVALID_PARAMS`
  - 相邻两次 `chat.send` 传不同 `provider` → 各自路由到对应 loop（ADR 0040 验收）
- `tests/test_eval_run_id.py`：
  - `_extract_eval_run_id("main")` → `""`
  - `_extract_eval_run_id("m6_eval_gemini_T10_1740000000")` → `"m6_eval_gemini_1740000000"`
  - `_extract_eval_run_id("m6_eval_openai_T12_1740000000")` → `"m6_eval_openai_1740000000"`
  - task_id 含下划线：`_extract_eval_run_id("m6_eval_gemini_T10_retry_1740000000")` → `"m6_eval_gemini_1740000000"`（从右取 timestamp）
  - 同一 eval run 内不同 task 的 session_id → 提取出相同 eval_run_id
  - 非 eval session_id（无 `m6_eval_` 前缀） → `""`
- `tests/test_budget_gate.py`：
  - try_reserve（OpenAI）累计 < €20 → Reservation(denied=False)
  - try_reserve（Gemini）累计 < €20 → Reservation(denied=False)
  - try_reserve 累计 €20~€25 → denied=False + 日志 warning
  - try_reserve 累计 + 预估 ≥ €25 → Reservation(denied=True)（全 provider 统一闸门）
  - settle 多退少补：actual < reserved → cumulative 调减
  - settle 多退少补：actual > reserved → cumulative 调增
  - settle 幂等：重复 settle(同一 reservation_id) → no-op，cumulative 不变
  - settle 未知：settle(不存在的 reservation_id) → no-op，cumulative 不变
  - 并发 try_reserve 不超卖（PG 行锁串行化，两个请求同时预占，仅一个成功当余量不足）
  - 跨 provider 累计共享（OpenAI + Gemini 交替预占，全局累计正确）
  - budget_reservations 按 provider 分项可查
  - try_reserve(session_id="main") → reservation 记录的 session_id = "main"
  - try_reserve(session_id="m6_eval_gemini_T10_17400") → session_id 正确写入
  - try_reserve(eval_run_id="run_1") → reservation 记录的 eval_run_id = "run_1"
  - try_reserve(eval_run_id="") → reservation 记录的 eval_run_id = ""（在线路径）
  - 按 eval_run_id 过滤查询仅返回对应 run 的记录
  - 按 session_id 过滤查询可回溯单任务成本
  - 多 worker 安全：模拟两个连接并发 try_reserve，验证 PG 串行语义
- `tests/test_curator_model.py`：
  - Curator 使用传入 model
  - Curator 默认读 settings.curation_model
  - temperature 参数正确传递到 model_client

## 6. Phase 2：Gemini 兼容性验证与适配

### 6.0 Gate
- Phase 2 完成后：基础 chat + streaming + tool calls 在 Gemini 上通过 smoke test。

### 6.1 兼容性排查清单

| 检查项 | 方法 | 预期结果 |
|--------|------|----------|
| 基础 chat（非流式） | 脚本调用 `model_client.chat()` | 正常返回文本 |
| 流式 chat | 脚本调用 `chat_stream()` | chunk 逐步 yield |
| 流式 tool calls | 脚本调用 `chat_stream_with_tools()` | ContentDelta + ToolCallsComplete 正确聚合 |
| 多轮 tool loop | 通过 AgentLoop 发起工具链 | 工具调用→结果→继续对话 |
| CJK 内容 | 中文对话 | 正常响应，无截断/乱码 |
| Error 重试 | 模拟 429/500 | 指数退避重试正常工作 |
| Token 计数 fallback | 检查 `tokenizer_mode` 日志 | 显示 `estimate`（非 `exact`） |

### 6.2 已知风险：Streaming Tool Calls 格式差异

OpenAI 流式 tool_calls 格式：
```json
{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_xxx", "function": {"name": "fn", "arguments": ""}}]}}]}
```

Gemini OpenAI-compat 可能返回略有不同的 delta 结构。处理策略：

1. **先验证**：用实际 API 调用确认格式是否一致。
2. **若一致**：无需改动。
3. **若有差异**：在 `chat_stream_with_tools` 中增加防御性处理（null-safe access, 格式归一化），而非分叉 provider 逻辑。

### 6.3 适配原则

- **不分叉**：不写 `if provider == "gemini"` 分支逻辑。改为防御性兼容（null-safe, fallback defaults）。
- **不新增 provider-specific 方法**：`ModelClient` ABC 不加 `gemini_grounding()` 之类的 provider 特有方法。差异在 `OpenAICompatModelClient` 内部以防御性编码吸收。（Phase 1 的 temperature 补全属于接口完整性修正，非 provider-specific 扩展。）
- **记录差异**：所有发现的兼容性差异写入 `decisions/` 作为 ADR 追踪。

### 6.4 Smoke 脚本（直接 API 调用）

新增 `scripts/m6_smoke_gemini.py`（不进入 src/，不作为生产代码）：
- **直接创建 `OpenAICompatModelClient`** + Gemini 配置，不经过 Gateway
- 运行 6.1 清单中的每一项
- 输出 PASS/FAIL + 耗时 + token 估算
- 用于验证 HTTP 级请求/响应格式兼容性，不涉及 session 语义
- 不进入 CI

> 注：Phase 2 smoke 有意绕过 Gateway，因为目标是隔离验证 ModelClient 与 Gemini API 的兼容性。全链路验证在 Phase 3 通过 WebSocket 客户端完成。

## 7. Phase 3：代表性任务评测与迁移结论

### 7.0 Gate
- Phase 3 完成后：评测报告完成，迁移结论明确，M6 可关闭。

### 7.1 代表性任务集

按 ADR 0038 两层策略设计：

**Layer 1 Smoke（可选, `gemini-2.5-flash-lite`）**：
- T01: 单轮中文问答
- T02: 单轮英文问答
- T03: 基本流式输出

**Layer 2 正式评测（必选, `gemini-2.5-flash` vs `gpt-5-mini`）**：

| Task ID¹ | 类别 | 描述 | 评判维度 |
|---------|------|------|----------|
| T10 | 基础对话 | 多轮中英文混合对话（5 轮） | 连贯性、语言切换 |
| T11 | 工具调用 | 单工具调用（current_time） | 正确触发、结果解析 |
| T12 | 工具链 | 多步工具调用（memory_search → 回答） | 链路完整性 |
| T13 | 长上下文 | 20+ 轮后继续对话 | 上下文保持 |
| T14 | CJK 处理 | 纯中文复杂指令（含引号、标点、代码） | 无截断、编码正确 |
| T15 | 角色遵循 | 系统 prompt 约束遵守 | 约束不漂移 |
| T16 | 错误恢复 | 工具返回错误后继续对话 | 优雅降级 |

> ¹ 此处 Task ID（T01–T16）= eval case ID（评测用例标识），不等于 ADR 0040 定义的 product task 或 agent-run。

### 7.2 评测执行脚本（WebSocket 客户端走 Gateway 全链路）

新增 `scripts/m6_eval.py`：

```
用法: python scripts/m6_eval.py [--tasks T10,T11,...] [--dry-run]
```

**核心设计**：评测脚本作为 **headless WebSocket 客户端**，通过 `ws://localhost:19789/ws` 连接运行中的 Gateway，走完整的 `chat.send` → session claim → AgentLoop → tool loop → session release 链路。这保证评测路径 = 生产路径，T11-T16 的结论具有代表性。

**Provider 切换**：通过 `ChatSendParams.provider` 字段实现 per-run 选择，无需停启 Gateway：
1. 启动 Gateway（确保 OpenAI + Gemini 均已配置）
2. 运行 `m6_eval.py --provider openai` → 每个 `chat.send` 携带 `provider: "openai"` → 记录结果
3. 运行 `m6_eval.py --provider gemini` → 每个 `chat.send` 携带 `provider: "gemini"` → 记录结果
4. 对比两份结果 → 产出迁移结论

**功能**：
- 连接运行中的 Gateway WebSocket endpoint
- 按 task ID 发送 `chat.send` RPC（附带 `provider` 字段），收集 streaming 响应（text chunks + tool calls + done）
- 每个 task 使用独立 session_id（`m6_eval_{provider}_{task_id}_{timestamp}`），避免跨任务和跨 provider 污染。同一次 eval run 内所有 task 共享相同的 `{timestamp}`，网关通过 `_extract_eval_run_id()` 从 session_id 前缀推导出 `eval_run_id = "m6_eval_{provider}_{timestamp}"`（详见 §5.2），无需在 `ChatSendParams` 中额外传递
- 记录：响应内容、延迟(ms)、token 估算、是否通过
- 输出 JSON 结果文件到 `dev_docs/reports/m6_eval_{provider}_{timestamp}.json`
- `--dry-run` 模式：仅打印任务列表和预估 token，不连接 Gateway

**与 Phase 2 smoke 的区别**：

| 维度 | Phase 2 Smoke | Phase 3 Eval |
|------|---------------|--------------|
| 路径 | 直接 ModelClient API 调用 | WebSocket → Gateway → AgentLoop 全链路 |
| 目的 | HTTP 级兼容性验证 | 代表性任务端到端验证 |
| Session 保护 | 无（不涉及 session） | 有（claim/release/fencing） |
| Provider 切换 | 脚本内指定 Gemini 配置 | `ChatSendParams.provider` per-run 选择，无需重启 |

### 7.3 成本追踪与预算硬约束

**预算数据源**：PostgreSQL `budget_state` + `budget_reservations` 表（详见 §5.4）。

**全 provider 统一预算池**（ADR 0041）：所有 provider 的 agent-run 共享同一全局累计值（`budget_state.cumulative_eur`）。`budget_reservations` 表按 provider 分项记录，支持迁移分析。

**报表输出**：eval 脚本从 `budget_reservations` 表查询（`WHERE eval_run_id = $1`，仅含当次 run 的记录），输出到 `dev_docs/reports/m6_eval_{provider}_{timestamp}.json`，包含：
- 全局预算累计（闸门依据，读 `budget_state`）
- 当次 eval run 的 Gemini 分项成本（迁移验证分析，按 `eval_run_id + provider` 过滤）
- 当次 eval run 的 OpenAI 分项成本（基线对照，同上）

**预算闸门（PG 原子预占，详见 §5.4）**：

| 阈值 | 行为 |
|------|------|
| €20 (warn) | 预占成功 + 日志 `budget_warning` |
| €25 (stop) | 预占拒绝 → `BUDGET_EXCEEDED` RPC error |
| €30 (ADR 0038 上限) | 留 €5 buffer 给并发预占和定价波动 |

**执行前预估**：`--dry-run` 模式读取账本累计值 + 本次 run 预估增量成本，不过闸门不调 API。

**定价参考**（执行时以公开定价为准，首次 run 前录入账本 header）：
- Gemini 2.5 Flash: ~$0.15/1M input, ~$0.60/1M output
- gpt-5-mini: 参考当时公开定价

### 7.4 迁移结论模板

评测完成后生成 `dev_docs/reports/m6_migration_conclusion.md`：

```markdown
# M6 迁移结论

## 评测摘要
- OpenAI 通过任务数 / 总数
- Gemini 通过任务数 / 总数
- 全局预算累计 / €30 上限
- OpenAI 分项成本
- Gemini 分项成本

## 兼容性发现
- 完全兼容项
- 需适配项（已处理）
- 不兼容项（若有）

## 切换策略
- 切换步骤（1-2-3）
- 回退步骤（1-2-3）
- 预期切换时间

## 结论
- [ ] Gemini 可作为 OpenAI 的可行备选
- [ ] 建议的默认路线维持/变更
```

## 8. 验收标准（对齐 roadmap）

| 用例 | 验收条件 | Phase |
|------|----------|-------|
| A: 等价任务完成 | Layer 2 的 T10-T16 在 OpenAI 和 Gemini 上均通过 | Phase 3 |
| B: 快速回退 | 改 `PROVIDER_ACTIVE=openai` + 重启后恢复正常，< 2 min | Phase 1 |
| C: 预算控制 | 全局 API 成本 < €30（全 provider 累计），PG 账本可追溯 + provider 分项可查 | Phase 3 |
| D: 回归无损 | 现有 481+ 测试全绿，`ChatSendParams.provider` 为可选字段（向后兼容） | 全程 |
| E: Agent-run 级路由 | 同一 Gateway 进程内，相邻两次 `chat.send` 通过 `provider` 字段路由到不同 provider 的 AgentLoop（ADR 0040 验收口径） | Phase 1 |
| F: 成本治理 | 所有 provider agent-run 统一经 PG 原子预算闸门；全局累计 ≥€25 时拒绝任何 provider 路由；支持 provider 分项查询（ADR 0041） | Phase 1 |

## 9. 测试策略

### 9.1 单元测试（不消耗 API）
- Phase 0: 配置加载、校验、默认值
- Phase 1: AgentLoopRegistry CRUD、默认 provider 路由、`ChatSendParams.provider` 路由 + 校验契约 + 错误码映射、BudgetGate PG 原子预占/settle/全 provider 统一/多 worker 并发、fail-fast 校验
- Phase 1: Curator model 参数化、curation_model 配置、temperature 传递
- Mock-based: 模拟 Gemini 响应格式验证 tool call 聚合

### 9.2 集成测试（消耗 API，手动触发）
- Phase 2: Smoke 脚本（`scripts/m6_smoke_gemini.py`）— 直接 ModelClient 调用，验证 HTTP 级兼容性
- Phase 3: 评测脚本（`scripts/m6_eval.py`）— WebSocket 客户端走 Gateway 全链路，验证端到端行为
- 这些脚本不进入 `just test` 自动执行，避免意外消耗 API quota
- Phase 3 评测需先启动 Gateway（对应 provider），脚本连接本地 WebSocket endpoint

### 9.3 回归测试
- 每个 Phase 完成后 `just test` 全量通过
- 现有 481+ 测试不受影响（新代码为 additive，不修改现有 mock 结构）

## 10. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Gemini OpenAI-compat streaming tool_calls 格式不一致 | 中 | 工具调用链断裂 | Phase 2 smoke 提前发现；防御性解析 |
| Gemini 不支持某些 OpenAI 参数（如 `NOT_GIVEN`） | 低 | 请求报错 | 参数传递前做 provider-aware 清理 |
| 全局预算超支 | 低 | 超出 €30 限额 | 全 provider 统一 PG 原子预占（€25 stop）+ €5 buffer + dry-run 预估 |
| Token 估算 vs 实际计费偏差 | 中 | 闸门不够保守 | €25 stop（非 €30），留 buffer；settle 对账多退少补；首次 run 后校准系数 |
| Token 估算模式下 budget tracker 精度下降 | 低 | 过早/过晚触发 compaction | M6 验证场景下可接受；长期可引入 Gemini tokenizer |
| Gemini API 可用性不稳定（preview/region 限制） | 中 | 评测中断 | 使用稳定模型名（非 preview）；记录重试次数 |
| 预算并发竞态导致超支 | 低 | 累计超 €25 | PG 行锁串行化 try_reserve（多 worker 安全）；settle 对账多退少补；€25 stop + €5 buffer |
| 预占泄漏（crash 后未 settle） | 低 | 预算虚高 | settle 在 finally 中执行；€5 buffer 可吸收少量泄漏；后续可加定时清理未结算预占 |

## 11. 实现顺序与依赖

```
Phase 0 (配置层) ──→ Phase 1 (AgentLoopRegistry + agent-run 路由 + PG 预算闸门 + Curator fix) ──→ Phase 2 (兼容性) ──→ Phase 3 (评测)
```

- Phase 0 → 1 严格顺序（1 依赖 0 的配置类）
- Phase 1 包含 Curator fix 和 temperature 补全（均为 provider 基础设施的一部分）
- Phase 2 依赖 Phase 1（需要 AgentLoopRegistry 才能切换到 Gemini）
- Phase 3 依赖 Phase 2（兼容性确认后才跑正式评测）

## 12. 文件变更预览

| 文件 | 变更类型 | Phase |
|------|----------|-------|
| `src/config/settings.py` | 新增 GeminiSettings, ProviderSettings; MemorySettings 增加 curation_model; 更新 Settings | 0 |
| `.env_template` | 新增 PROVIDER_/GEMINI_ 配置 | 0 |
| `src/gateway/protocol.py` | ChatSendParams 新增可选 `provider` 字段 + validator | 1 |
| `src/agent/provider_registry.py` | 新文件：AgentLoopRegistry | 1 |
| `src/gateway/budget_gate.py` | 新文件：BudgetGate（PG 原子预占 + settle 对账，全 provider 统一） | 1 |
| `alembic/versions/xxxx_add_budget_tables.py` | 新文件：budget_state + budget_reservations 表 | 1 |
| `src/gateway/app.py` | 更新 lifespan: per-provider AgentLoop 预建 + registry + BudgetGate；`_handle_chat_send`: agent-run 路由 + eval_run_id 推导 + 错误映射 + 预算闸门 | 1 |
| `src/memory/curator.py` | model 参数化 + 读 settings.curation_model | 1 |
| `src/agent/model_client.py` | ModelClient ABC + OpenAICompatModelClient: temperature 参数补全 | 1 |
| `scripts/m6_smoke_gemini.py` | 新文件：Gemini smoke 脚本 | 2 |
| `scripts/m6_eval.py` | 新文件：评测脚本（读 PG budget_reservations 生成报表） | 3 |
| `tests/test_config_provider.py` | 新文件 | 0 |
| `tests/test_provider_registry.py` | 新文件 | 1 |
| `tests/test_gateway_provider.py` | 新文件 | 1 |
| `tests/test_budget_gate.py` | 新文件：PG 原子预占 + settle 对账 + settle 幂等 + eval_run_id 写入/过滤 + 全 provider 统一 + 多 worker 并发 | 1 |
| `tests/test_eval_run_id.py` | 新文件：`_extract_eval_run_id` 推导逻辑（前缀匹配、task 段剥离、在线路径空串） | 1 |
| `tests/test_curator_model.py` | 新文件 | 1 |

> 注：`src/gateway/protocol.py` 新增 `ChatSendParams.provider` 可选字段（向后兼容）。
> ADR 0040/0041 已独立落地，不在本计划文件变更范围内。
