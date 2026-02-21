# M2 会话内连续性 实现计划

> 状态：draft
> 日期：2026-02-21
> 依据：`design_docs/m2_architecture.md`、`design_docs/memory_architecture.md`、`design_docs/roadmap_milestones_v3.md`、ADR 0021/0022/0026/0027/0028/0029/0030/0031/0032

## 1. 目标

在单会话长链路中维持上下文连续性，避免"长对话失忆"和角色漂移。

核心交付：
- Token 预算管理：精确计数（tiktoken, mode=`exact`）+ fallback 估算（mode=`estimate`），实时判断 context 使用率。
- 会话压缩（Compaction）：超阈值时生成结构化 rolling summary，保留锚点约束，事后校验 + 重试一次。
- Pre-compaction memory flush：由 CompactionEngine 唯一负责提取候选记忆条目（ADR 0032 单一职责）。
- Agent Loop 集成：预算检查 → compact（含 flush） → store → 水位线重建（ADR 0031）完整闭环。
- 降级与容错：fail-open + emergency trim + overflow 重试，保证会话不中断。
- 反漂移验收：压缩前后锚点保留率 >= 95%，Probe 一致率 >= 90%。

## 2. 设计决策汇总

| 决策项 | 选择 | ADR |
|--------|------|-----|
| Compaction 模型 | 当前会话同一模型，低温度（0~0.2） | 0028 |
| Token 计数 | tiktoken (exact) + chars/4 (estimate) fallback | 0029 |
| 计数模式标记 | `exact` / `estimate` | 0029 |
| 摘要输出结构 | `facts/decisions/open_todos/user_prefs/timeline` | 0028 |
| 反漂移基线 | 最终 prompt 可见性校验 + retry once + Probe 验收 | 0030 |
| Noop 处理 | 跳过 store，仅日志，不写 DB | 0031 |
| 锚点校验对象 | 最终模型上下文（system_prompt + summary + history），非单独摘要文本 | 0030 |
| ModelClient 依赖 | 复用现有 `OpenAICompatModelClient`，不新增抽象 | — |
| store lock_token | 必填 `str`，无 None 路径，测试用 claim fixture | 0021 |
| History 重建 | 水位线语义：`seq > last_compaction_seq` | 0031 |
| Memory flush 职责 | CompactionEngine 唯一生成，AgentLoop 仅编排 | 0032 |
| 实现拆分 | 3 phases + 每阶段烟雾端到端 | — |
| 摘要策略 | Rolling summary（非拼接） | — |
| Timeout 单位 | 秒（`_s`） | — |
| 配置前缀 | 全程统一 `COMPACTION_` | — |

## 3. Phase 1：Token Budget 基础设施

### 3.1 目标

建立精确的 token 计数能力和预算管理框架。在 agent loop 中可判断"当前 context 是否接近上限"。Phase 1 不触发任何 compaction，仅观测和记录。

### 3.2 新增依赖

- `tiktoken`：添加到 `pyproject.toml`

### 3.3 新增模块 `src/agent/token_budget.py`

#### TokenCounter

```python
class TokenCounter:
    """Token counter with tiktoken precision and chars/4 fallback.

    Binds to a specific model at construction time. Automatically
    resolves tiktoken encoding; falls back to estimate mode if
    encoding is unavailable (non-OpenAI models).
    """

    def __init__(self, model: str) -> None:
        """Bind to model, auto-resolve tiktoken encoding."""

    @property
    def tokenizer_mode(self) -> Literal["exact", "estimate"]:
        """Current counting mode (aligned with ADR 0029)."""

    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens for a list of chat messages (OpenAI format).
        Includes per-message overhead tokens (~4 tokens/message header).
        Handles all roles: system, user, assistant, tool.
        """

    def count_text(self, text: str) -> int:
        """Count tokens for a plain text string."""

    def count_tools_schema(self, tools: list[dict]) -> int:
        """Count tokens for tools/function schema definitions."""
```

设计约束：
- 构造时绑定 model，自动匹配 tiktoken encoding。
- 非 OpenAI 模型或 encoding 缺失时 fallback 到 `ceil(len(text) / 4)`。
- fallback 时记录 `structlog.warning("tokenizer_fallback", model=model, mode="estimate")`。
- `count_messages()` 需计入 OpenAI chat format 的 per-message overhead（每条消息约 4 tokens header），支持所有 role（含 system）。

#### BudgetTracker

```python
@dataclass(frozen=True)
class BudgetStatus:
    """Result of a budget check."""
    status: Literal["ok", "warn", "compact_needed"]
    current_tokens: int
    usable_budget: int
    warn_threshold: int
    compact_threshold: int
    tokenizer_mode: str  # "exact" | "estimate" (ADR 0029)

class BudgetTracker:
    """Tracks token budget against configurable thresholds."""

    def __init__(self, settings: CompactionSettings, model: str) -> None:
        """Compute derived thresholds from settings."""

    def check(self, current_tokens: int) -> BudgetStatus:
        """Evaluate current token usage against thresholds."""
```

预算公式（与 `m2_architecture.md` 3.1 节对齐）：
- `usable_input_budget = context_limit - reserved_output_tokens - safety_margin_tokens`
- `warn_threshold = usable_input_budget * warn_ratio`
- `compact_threshold = usable_input_budget * compact_ratio`

### 3.4 配置 `src/config/settings.py`

新增 `CompactionSettings`（Phase 1 仅放已使用字段，Phase 2 追加字段，**前缀从头到尾统一为 `COMPACTION_`**）：

```python
class CompactionSettings(BaseSettings):
    """Compaction and token budget settings.
    Phase 1: budget fields only. Phase 2: adds compaction-specific fields.
    Env prefix: COMPACTION_ (stable across all phases).
    """
    model_config = SettingsConfigDict(env_prefix="COMPACTION_")

    context_limit: int = 128_000
    warn_ratio: float = 0.80
    compact_ratio: float = 0.90
    reserved_output_tokens: int = 2048
    safety_margin_tokens: int = 1024

    @model_validator(mode="after")
    def _validate_ratios(self) -> Self:
        """Enforce:
        - 0 < warn_ratio < compact_ratio < 1
        - usable_input_budget > 0
        """
```

Root Settings 新增 `compaction: CompactionSettings` 字段。

### 3.5 Agent Loop 烟雾集成（只观测不改变行为）

在 `agent.py` 每次模型调用前增加 budget check log。

**关键修正**：统一按完整 message 列表计数（含 system），避免漏掉 system message 的 chat-format overhead。

```python
# 构建完整 message 列表（含 system）
system_prompt = prompt_builder.build(session_id, mode=mode)
history = session_manager.get_history(session_id)
full_messages = [{"role": "system", "content": system_prompt}] + history

# 统一计数
total = counter.count_messages(full_messages) + counter.count_tools_schema(tools)

# 只观测不触发
budget_status = tracker.check(total)
logger.info(
    "budget_check",
    session_id=session_id,
    model=model,
    iteration=iteration,
    current_tokens=budget_status.current_tokens,
    status=budget_status.status,
    usable_budget=budget_status.usable_budget,
    warn_threshold=budget_status.warn_threshold,
    compact_threshold=budget_status.compact_threshold,
    tokenizer_mode=budget_status.tokenizer_mode,
)
```

### 3.6 测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_token_budget.py` | TokenCounter: 多 encoding、中英文混合、fallback 触发（mode=estimate）、tools schema 计数、per-message overhead、system message overhead |
| `tests/test_token_budget.py` | BudgetTracker: 边界值（刚好 warn/compact）、配置覆盖、BudgetStatus 全字段完整性 |
| `tests/test_token_budget.py` | CompactionSettings: ratio 校验（warn >= compact 报错、负值报错、usable <= 0 报错） |
| `tests/test_agent_budget_smoke.py` | Agent loop 烟雾：mock model + 验证 budget_check 日志输出字段完整（含 tokenizer_mode=exact/estimate） |

### 3.7 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `pyproject.toml` | 修改：新增 tiktoken 依赖 |
| `src/agent/token_budget.py` | 新增 |
| `src/config/settings.py` | 修改：新增 CompactionSettings + Root Settings 组合 |
| `src/agent/agent.py` | 修改：注入 budget check log（只观测） |
| `tests/test_token_budget.py` | 新增 |
| `tests/test_agent_budget_smoke.py` | 新增 |

---

## 4. Phase 2：Compaction 核心引擎

### 4.1 目标

实现"有质量的压缩"——将超阈值的旧 turns 生成结构化 rolling summary，产出 memory flush 候选条目。作为独立模块开发，不碰 agent loop 主链路。

### 4.2 DB Migration（Alembic）

SessionRecord 新增字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `compacted_context` | `Text, nullable` | 最近一次压缩产物（注入 system prompt） |
| `compaction_metadata` | `JSONB, nullable` | 压缩报告（含 schema_version, status 等） |
| `last_compaction_seq` | `Integer, nullable` | 到哪个 seq 已被压缩（水位线，ADR 0031） |
| `memory_flush_candidates` | `JSONB, nullable` | 候选条目数组（仅保留最近一次，最大 20 条） |

`estimated_input_tokens` 不存 DB（每次实时计算）。

Compaction metadata schema：

```json
{
  "schema_version": 1,
  "status": "success | degraded | failed | noop",
  "preserved_count": 8,
  "summarized_count": 15,
  "trimmed_count": 3,
  "flush_skipped": false,
  "anchor_validation_passed": true,
  "anchor_retry_used": false,
  "triggered_at": "2026-02-21T12:00:00Z",
  "compacted_context_tokens": 450,
  "rolling_summary_input_tokens": 2100
}
```

### 4.3 SessionManager 历史读取扩展

新增带 seq 的历史读取接口 + 水位线查询接口（ADR 0031）：

```python
@dataclass(frozen=True)
class MessageWithSeq:
    seq: int
    message_id: str
    role: str
    content: str | None
    tool_calls: list[dict] | None
    tool_call_id: str | None

class SessionManager:
    async def get_history_with_seq(self, session_id: str) -> list[MessageWithSeq]:
        """Return all messages with seq.
        Prioritizes in-memory cache (extend Message to carry seq).
        """

    async def get_effective_history(
        self, session_id: str, last_compaction_seq: int | None
    ) -> list[MessageWithSeq]:
        """唯一重建入口 (ADR 0031).
        Returns messages WHERE seq > last_compaction_seq (or all if None).
        """
```

扩展内存中的 Message 结构以携带 seq，避免每次额外 DB 往返。

### 4.4 新增模块 `src/agent/compaction.py`

#### Turn 切分规则

固定语义：以 `user` role message 为 turn 起点，后续所有 `assistant`（含 tool_calls 空内容）/`tool` 归属该 turn，直到下一个 `user`。

```python
@dataclass
class Turn:
    """A conversation turn: user message + all subsequent assistant/tool messages."""
    start_seq: int
    end_seq: int
    messages: list[MessageWithSeq]

def split_turns(messages: list[MessageWithSeq]) -> list[Turn]:
    """Split messages into turns by user-message boundaries."""
```

#### CompactionEngine（含 flush 生成，ADR 0032）

```python
@dataclass
class CompactionResult:
    status: Literal["success", "degraded", "failed", "noop"]
    compacted_context: str | None         # rolling summary（注入 system prompt）
    compaction_metadata: dict             # 统计报告（含 schema_version=1）
    new_compaction_seq: int               # 新水位线（ADR 0031）
    memory_flush_candidates: list[dict]   # M2/M3 衔接候选（ADR 0032: 唯一输出通道）
    preserved_messages: list[MessageWithSeq]  # 调试辅助，非主路径真相来源

class CompactionEngine:
    """Core compaction logic: rolling summary + anchor preservation + flush generation.

    Memory flush 由本模块唯一负责（ADR 0032），AgentLoop 仅做编排。
    """

    def __init__(
        self,
        model_client: OpenAICompatModelClient,  # 复用现有实现，M6 再引入抽象
        token_counter: TokenCounter,
        settings: CompactionSettings,
        flush_generator: MemoryFlushGenerator,  # 私有 collaborator
    ) -> None: ...

    async def compact(
        self,
        messages: list[MessageWithSeq],
        system_prompt: str,
        tools_schema: list[dict],
        budget_status: BudgetStatus,
        last_compaction_seq: int | None,
        previous_compacted_context: str | None,
        current_user_seq: int,  # 当前未完成 turn 的 seq（排除用）
    ) -> CompactionResult:
        """Execute compaction pipeline.

        Steps:
        1. Split messages into turns
        2. 排除当前未完成 turn（seq >= current_user_seq）
        3. Identify compressible range (after last_compaction_seq, before preserved zone)
        4. If no compressible range → return status=noop, new_compaction_seq unchanged
        5. Generate memory flush candidates from compressible turns (内部调用 flush_generator)
           - 超时保护: asyncio.wait_for(flush_timeout_s)
           - 失败: 继续 compact, 标记 flush_skipped=true
        6. Build rolling summary:
           - 输入: previous_compacted_context (如有) + compressible turns 原文
           - LLM 调用: 低温度 (0~0.2), 明确输出 token 上限 (ADR 0028)
           - 输出: 结构化摘要 (facts/decisions/open_todos/user_prefs/timeline)
           - Token 上限: 摘要不超过可压缩 turns 原文 token 数的 30%
        7. 锚点可见性校验 (ADR 0030):
           - 校验对象: 最终模型上下文 = system_prompt + compacted_context + effective_history
           - 校验内容: 从 AGENTS/SOUL/USER 提取的关键锚点声明在最终上下文中可见
           - 说明: 由于 system prompt 始终包含 workspace context，锚点在正常路径下总是可见。
             此校验是 safety net（防止 prompt 组装异常），不是频繁触发的质量门控。
           - 校验失败（极端情况）→ retry once → 仍失败 → degraded path
        8. Return CompactionResult

        Invariants:
        - new_compaction_seq 单调递增，不超过 current_user_seq - 1
        - noop when no compressible range (水位线不变)
        - 超时: asyncio.wait_for(compact_timeout_s)
        - LLM 失败 → degraded path (trim only, 仍产出新水位线)
        """
```

Rolling summary prompt 指令（发给 LLM，对齐 ADR 0028）：
- 输入：上次的 `compacted_context`（如有） + 本次可压缩 turns 原文
- 输出要求：结构化摘要，必须包含以下字段：
  - `facts`：已确认的事实
  - `decisions`：已做出的决策
  - `open_todos`：未完成事项
  - `user_prefs`：用户偏好声明
  - `timeline`：关键时间线事件
- 生成参数：`temperature=0.1`，明确输出 token 上限
- 约束：优先保留与后续任务执行相关的锚点信息（权威校验以最终上下文可见性为准，摘要不要求逐字复写全部锚点）
- Token 上限：摘要不超过可压缩 turns 原文 token 数的 30%（防膨胀）

降级路径：
- flush 失败 → 继续 compact，标记 `flush_skipped=true`
- LLM 超时/失败 → degraded（仅裁剪保留最近 N turns，仍产出新水位线），标记 `status=degraded`
- 锚点校验失败 + 重试仍失败 → degraded path
- 无可压缩区间 → `status=noop`，`new_compaction_seq` 不变

### 4.5 Memory flush 生成（`src/agent/memory_flush.py`，CompactionEngine 内部使用）

```python
@dataclass
class MemoryFlushCandidate:
    """Pre-compaction memory candidate (aligned with m2_architecture.md 3.3)."""
    candidate_id: str          # uuid4
    source_session_id: str     # "main" | "group:*"
    source_message_ids: list[str]
    candidate_text: str        # max 2KB
    constraint_tags: list[str]  # ["user_preference", "long_term_goal", "safety_boundary", "fact"]
    confidence: float           # [0.0, 1.0]
    created_at: str             # ISO 8601

class MemoryFlushGenerator:
    """Extract memory candidates from compressible turns.

    Called exclusively by CompactionEngine (ADR 0032).
    AgentLoop MUST NOT call this directly.
    """

    def generate(
        self,
        compressible_turns: list[Turn],
        session_id: str,
    ) -> list[MemoryFlushCandidate]:
        """Rule-based extraction (no LLM in Phase 2):
        - User explicit declarations ("我喜欢...", "记住...", "以后...") → confidence 0.8-1.0
        - Confirmed decisions/facts → confidence 0.5-0.7
        - General conversation → confidence 0.2-0.4
        - Casual chat/acknowledgments → skip
        - Max 20 candidates per flush
        - Single candidate max 2KB text
        - confidence ∈ [0.0, 1.0] (hard constraint)
        """
```

### 4.6 配置扩展

`CompactionSettings` 追加 Phase 2 字段：

```python
class CompactionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COMPACTION_")

    # Token budget (Phase 1)
    context_limit: int = 128_000
    warn_ratio: float = 0.80
    compact_ratio: float = 0.90
    reserved_output_tokens: int = 2048
    safety_margin_tokens: int = 1024

    # Compaction (Phase 2 new)
    min_preserved_turns: int = 8
    flush_timeout_s: float = 30.0
    compact_timeout_s: float = 30.0
    fail_open: bool = True
    max_flush_candidates: int = 20
    max_candidate_text_bytes: int = 2048
    max_compactions_per_request: int = 2
    summary_temperature: float = 0.1
    anchor_retry_enabled: bool = True

    @model_validator(mode="after")
    def _validate(self) -> Self:
        """Enforce:
        - 0 < warn_ratio < compact_ratio < 1
        - usable_input_budget > 0
        - summary_temperature in [0.0, 1.0]
        """
```

Root Settings 字段名保持 `compaction: CompactionSettings`（Phase 1-3 不变）。

### 4.7 Phase 2 烟雾测试

集成测试验证完整 compaction 链路：
- 构造超阈值的 mock 历史（30+ turns）
- 触发 `CompactionEngine.compact()`
- 验证：返回结构完整、摘要非空且结构对齐 ADR 0028（facts/decisions/open_todos/user_prefs/timeline）、保留 turns = min_preserved_turns、水位线前进、候选条目格式合法、锚点校验通过

### 4.8 测试

| 测试文件 | 覆盖范围 |
|----------|----------|
| `tests/test_compaction.py` | Turn 分割：user 边界、tool-only 归属、空历史、当前 turn 排除 |
| `tests/test_compaction.py` | CompactionEngine: 正常 compaction（mock LLM）、rolling summary 结构验证（ADR 0028 字段）、水位线单调递增 |
| `tests/test_compaction.py` | 锚点可见性校验：正常通过（system prompt 含锚点）/ 模拟 prompt 异常 → 失败 → retry 成功 / retry 仍失败 → degraded（ADR 0030） |
| `tests/test_compaction.py` | Noop 语义：无可压缩区间返回 noop，水位线不变 |
| `tests/test_compaction.py` | 重复 compaction 幂等：无新消息时返回 noop |
| `tests/test_compaction.py` | 降级路径：LLM 超时 → degraded + flush_skipped/status 元数据正确 |
| `tests/test_compaction.py` | 多轮 compaction：rolling summary token 非线性增长 |
| `tests/test_compaction.py` | 水位线不超过 current_user_seq - 1 |
| `tests/test_compaction.py` | CompactionSettings 校验 |
| `tests/test_memory_flush.py` | MemoryFlushGenerator: 候选提取、标签分类、confidence ∈ [0,1]、空输入、上限约束 |
| `tests/test_memory_flush.py` | 候选结构对齐 m2_architecture.md 3.3（candidate_id/source_session_id/source_message_ids/candidate_text/constraint_tags/confidence/created_at 全部存在） |
| `tests/test_compaction_smoke.py` | 端到端烟雾：30+ turns → compact → 结构完整 + 水位线前进 + 锚点保留 |

### 4.9 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `alembic/versions/xxx_add_compaction_fields.py` | 新增：session 表 4 个新字段 |
| `src/session/models.py` | 修改：SessionRecord 新增 compaction 字段 |
| `src/session/manager.py` | 修改：新增 `get_history_with_seq()`、`get_effective_history()`；内存 Message 扩展带 seq |
| `src/agent/compaction.py` | 新增：Turn/CompactionResult/CompactionEngine（依赖现有 OpenAICompatModelClient，含 flush 生成） |
| `src/agent/memory_flush.py` | 新增：MemoryFlushCandidate/MemoryFlushGenerator（CompactionEngine 内部使用） |
| `src/config/settings.py` | 修改：CompactionSettings 追加 Phase 2 字段 |
| `tests/test_compaction.py` | 新增 |
| `tests/test_memory_flush.py` | 新增 |
| `tests/test_compaction_smoke.py` | 新增 |

---

## 5. Phase 3：Agent Loop 集成 + 端到端验证

### 5.1 目标

将 Phase 1-2 的独立模块接入 agent loop 主链路，形成完整的"预算检查 → compact → store → 水位线重建"闭环。

### 5.2 AgentLoop 集成 (`src/agent/agent.py`)

集成位置：每次模型调用前。

**编排职责边界（ADR 0032）**：AgentLoop 仅负责编排触发顺序与失败策略。不直接调用 MemoryFlushGenerator。

**History 重建语义（ADR 0031）**：以 `last_compaction_seq` 为唯一裁剪水位线。`preserved_messages` 不参与主路径。

触发流程：

```
每次模型调用前（统一路径）:
1. 获取 compaction state:
   compaction_state = await session_manager.get_compaction_state(session_id)
   last_compaction_seq = compaction_state.last_compaction_seq if compaction_state else None
   compacted_context = compaction_state.compacted_context if compaction_state else None

2. 构建 system prompt（含 compacted_context if any）:
   system_prompt = prompt_builder.build(session_id, mode=mode, compacted_context=compacted_context)

3. 获取有效历史（水位线重建，ADR 0031）:
   effective_history = await session_manager.get_effective_history(session_id, last_compaction_seq)

4. 统一 token 计数（含 system message overhead）:
   full_messages = [{"role": "system", "content": system_prompt}] + to_openai_format(effective_history)
   total = counter.count_messages(full_messages) + counter.count_tools_schema(tools)

5. 预算检查:
   budget_status = tracker.check(total)

6. if budget_status.status == "compact_needed":
   → 执行 compaction 流程（见 5.3）
   → 成功后用新水位线重建（回到步骤 1-4 的语义，但用更新后的 state）

7. if budget_status.status == "warn":
   → 仅 log warning，不触发 compaction

8. if budget_status.status == "ok":
   → 正常路径

9. 发送模型请求:
   → [system_prompt] + effective_history（最终确定版）
```

### 5.3 Compaction 执行流程

```
当 budget_status.status == "compact_needed":

a. 确定当前 turn seq:
   current_user_seq = 当前请求刚写入的 user message seq

b. 调用 CompactionEngine.compact():
   result = await compaction_engine.compact(
       messages=await session_manager.get_history_with_seq(session_id),
       system_prompt=system_prompt,
       tools_schema=tools_schema,
       budget_status=budget_status,
       last_compaction_seq=last_compaction_seq,
       previous_compacted_context=compacted_context,
       current_user_seq=current_user_seq,
   )
   - CompactionEngine 内部处理: turn 切分、flush 生成、rolling summary、锚点可见性校验+重试
   - 超时保护: asyncio.wait_for(compact_timeout_s)

c. Noop 处理（合法边界：保留区已占满，无可压缩区间）:
   if result.status == "noop":
       logger.info("compaction_noop", session_id=session_id,
                    last_compaction_seq=last_compaction_seq)
       # 不调用 store_compaction_result，不写 DB，不重建 prompt
       # 直接继续正常流程（用当前 effective_history 发送模型请求）
       → 跳到步骤 9（发送模型请求）

d. 持久化 compaction 结果（仅 success/degraded/failed，ADR 0031 + ADR 0021 fencing）:
   await session_manager.store_compaction_result(
       session_id, result, lock_token=lock_token
   )
   - lock_token 必填（调用方必须持有 session lock）
   - Fencing update: WHERE lock_token=...
   - 单调保护: new_last_compaction_seq > old_last_compaction_seq

e. 用新水位线重建:
   - 更新 compaction_state（new_compaction_seq, new_compacted_context）
   - 重建 system_prompt（含新 compacted_context）
   - 重建 effective_history = messages WHERE seq > new_compaction_seq
   - 重算 token 计数

e. 当前 turn 安全性（P0 修正，ADR 0031）:
   - 因为 compaction 排除了 seq >= current_user_seq
   - 且重建后 effective_history = seq > new_compaction_seq（其中 new_compaction_seq < current_user_seq）
   - 所以当前 user turn 自然包含在 effective_history 中，不会丢失
```

### 5.4 降级保护

```
降级层次（从优雅到紧急）:

1. flush 失败:
   → CompactionEngine 内部处理: 跳过 flush, 继续 compact
   → 标记 flush_skipped=true

2. compaction LLM 失败:
   → CompactionEngine 内部处理: degraded path
   → 仅裁剪（仍产出新水位线: end_seq of last compressible turn）
   → 标记 status=degraded

3. 锚点可见性校验失败（极端情况: prompt 组装异常）:
   → CompactionEngine 内部处理: retry once (ADR 0030)
   → 二次失败 → degraded path

4. compaction 整体超时/异常:
   → AgentLoop 处理: emergency trim
   → 锚点始终可见（system prompt 中的 AGENTS/SOUL/USER 内容不受 compaction 影响）
   → 强制水位线推进到 (max_seq - min_preserved_turns 对应的 seq)
   → 标记 status=failed

5. emergency trim 后仍 overflow:
   → 当轮重试 1 次（进一步缩减 min_preserved_turns）
   → 再失败 → fail-open 返回错误消息给用户

6. 所有降级路径:
   → fail-open: 会话继续，不中断
   → 记录结构化错误日志
```

### 5.5 重入保护

- 单次 agent loop iteration 内最多触发一次 compaction。
- 单个用户请求（`handle_message`）内最多触发 `max_compactions_per_request` 次（默认 2），覆盖 tool loop 内多轮 iteration 场景。
- Compaction 进行中设置 flag，防止并发触发。

### 5.6 PromptBuilder 扩展 (`src/agent/prompt_builder.py`)

`build()` 方法接受可选的 `compacted_context: str | None`：

```python
def build(
    self,
    session_id: str,
    mode: ToolMode,
    compacted_context: str | None = None,
) -> str:
```

- 有 `compacted_context` 时：在 workspace context 之后、memory recall 之前注入 `[会话摘要]` 块。
- 摘要块为 compaction 保留项。
- 无 `compacted_context` 时：行为不变（向后兼容）。
- **仅在 compaction 发生后才重建 prompt**（正常路径每轮只 build 一次）。

### 5.7 SessionManager 扩展 (`src/session/manager.py`)

新增方法：

```python
@dataclass(frozen=True)
class CompactionState:
    compacted_context: str | None
    last_compaction_seq: int | None
    compaction_metadata: dict | None

class SessionManager:
    async def get_effective_history(
        self, session_id: str, last_compaction_seq: int | None
    ) -> list[MessageWithSeq]:
        """唯一重建入口 (ADR 0031).
        Returns messages WHERE seq > last_compaction_seq (or all if None).
        In-memory cache preferred.
        """

    async def store_compaction_result(
        self,
        session_id: str,
        result: CompactionResult,
        lock_token: str,
    ) -> None:
        """Persist compaction state (ADR 0031 + ADR 0021 fencing).

        MUST NOT be called when result.status == "noop" (caller responsibility).

        lock_token is REQUIRED (no None, no default). Callers must hold a valid
        session lock. Tests use session claim fixtures to obtain lock_token.
        Consistent with append_message fencing semantics.

        Atomic UPDATE:
          SET compacted_context=..., compaction_metadata=...,
              last_compaction_seq=..., memory_flush_candidates=...
          WHERE session_id=... AND lock_token=...
            AND (last_compaction_seq IS NULL OR last_compaction_seq < new_seq)
        Raises SessionFencingError on token mismatch.
        Raises ValueError if new_seq <= old_seq (monotonic protection).
        """

    async def get_compaction_state(self, session_id: str) -> CompactionState | None:
        """Load compacted_context + last_compaction_seq + compaction_metadata.
        Returns None if session has no compaction history.
        """
```

### 5.8 端到端集成测试

| 场景 | 验证点 |
|------|--------|
| 短对话（< warn） | 无 compaction 触发，正常响应 |
| 中对话（warn 区间） | 日志有 warn，无 compaction，正常响应 |
| 长对话（> compact） | 触发 compaction，后续轮次能继续任务 |
| **compaction 后当前 turn 保留** | **compaction 后 effective_history 包含当前 user message（P0 验证）** |
| 压缩后继续 5 轮 | 任务连续性，关键约束保持 |
| 二次 compaction | rolling summary 正确，水位线前进 |
| LLM 超时降级 | degraded，仍产出水位线，会话继续 |
| 锚点可见性校验失败（模拟 prompt 异常） | retry 成功 → success；retry 失败 → degraded |
| Emergency trim | 保留锚点 + 最近 N turns，status=failed |
| Overflow + 重试 | emergency trim 后重试一次 |
| **noop: 无可压缩区间** | **status=noop，不调用 store_compaction_result，不写 DB，不重建 prompt** |
| 重复 compaction（无新消息） | 幂等，noop，无 store 调用 |
| last_compaction_seq 单调递增 | 多次 compaction 后断言 seq 递增，不超过 current_user_seq - 1 |
| degraded 分支 metadata | flush_skipped/status/anchor_validation 字段正确 |
| AgentLoop 持久化次数 | compact 后仅一次 store_compaction_result 调用（ADR 0032） |

测试策略：调小阈值（如 context_limit=2000）做确定性触发。

### 5.9 反漂移验收（手动/半自动，ADR 0030）

- 准备 20 条 Probe 问题（覆盖安全、偏好、任务连续性三类场景）。
- 在长对话（30+ turns）+ compaction 后逐条提问。
- 人工判定是否满足：
  - 锚点保留率 >= 95%
  - Probe 一致率 >= 90%
  - 压缩后连续 3 轮任务推进无明显断层
  - 安全边界违规数 = 0
- 产出一次离线评估记录（命中率、丢失项、失败样例）。
- 验收结果记录在 `dev_docs/reviews/m2_anti-drift-evaluation_YYYY-MM-DD.md` 中。

### 5.10 涉及文件

| 文件 | 变更类型 |
|------|----------|
| `src/agent/agent.py` | 修改：集成 budget check → compact → store → 水位线重建完整流程 |
| `src/agent/prompt_builder.py` | 修改：`build()` 新增 `compacted_context` 参数，条件注入摘要块 |
| `src/session/manager.py` | 修改：新增 `get_effective_history`（ADR 0031）、`store_compaction_result`（fenced + 单调保护）、`get_compaction_state` |
| `tests/test_agent_compaction_integration.py` | 新增：端到端集成测试（含 P0 当前 turn 保留验证） |
| `tests/test_compaction_degradation.py` | 新增：降级路径全覆盖（含 overflow 重试） |

---

## 6. 涉及文件变更总览

| 文件 | Phase | 变更类型 | 说明 |
|------|-------|----------|------|
| `pyproject.toml` | 1 | 修改 | 新增 tiktoken 依赖 |
| `src/agent/token_budget.py` | 1 | 新增 | TokenCounter + BudgetTracker + BudgetStatus |
| `src/config/settings.py` | 1→2 | 修改 | Phase 1: CompactionSettings (budget fields); Phase 2: 追加 compaction fields |
| `src/agent/agent.py` | 1→3 | 修改 | Phase 1: budget log; Phase 3: 完整集成 |
| `alembic/versions/xxx_add_compaction_fields.py` | 2 | 新增 | session 表 4 个新字段 |
| `src/session/models.py` | 2 | 修改 | SessionRecord 新增 compaction 字段 |
| `src/session/manager.py` | 2→3 | 修改 | Phase 2: get_history_with_seq + get_effective_history; Phase 3: store/get compaction state |
| `src/agent/compaction.py` | 2 | 新增 | Turn/CompactionResult/CompactionEngine（依赖现有 OpenAICompatModelClient，含 flush 调用） |
| `src/agent/memory_flush.py` | 2 | 新增 | MemoryFlushCandidate/MemoryFlushGenerator（CompactionEngine 内部使用） |
| `src/agent/prompt_builder.py` | 3 | 修改 | build() 新增 compacted_context 参数 |
| `tests/test_token_budget.py` | 1 | 新增 | token 计数 + budget 管理测试 |
| `tests/test_agent_budget_smoke.py` | 1 | 新增 | agent loop budget log 烟雾测试 |
| `tests/test_compaction.py` | 2 | 新增 | compaction engine 全套测试（含锚点校验 + 重试） |
| `tests/test_memory_flush.py` | 2 | 新增 | memory flush 候选生成测试 |
| `tests/test_compaction_smoke.py` | 2 | 新增 | Phase 2 端到端烟雾测试 |
| `tests/test_agent_compaction_integration.py` | 3 | 新增 | 端到端集成测试（含当前 turn 保留） |
| `tests/test_compaction_degradation.py` | 3 | 新增 | 降级路径测试（含 overflow 重试） |

## 7. 不做什么（Out of Scope）

- 不解决跨天/跨会话记忆召回（M3）。
- 不实现 `memory_append` 持久化写入工具（M3）。
- 不实现 `SOUL.md` 自我进化闭环（M3）。
- 不建自动化反漂移评估框架（手动/半自动验收即可，ADR 0030）。
- 不实现运行时漂移检测。
- 不处理多模型 tokenizer 精确匹配（非 OpenAI 模型统一 fallback，ADR 0029）。
- 不在 M2 内扩展 prompt builder 的 memory recall 层（仍为占位）。
- AgentLoop 不直接调用 MemoryFlushGenerator（ADR 0032）。

## 8. 验收标准对齐（来自 roadmap）

- **用例 A**：用户连续进行 30+ 轮任务后，关键约束仍被正确遵守。
  - 验证：Phase 3 E2E 测试 + 反漂移 Probe。
- **用例 B**：会话压缩后继续提问，关键上下文可延续，任务不中断。
  - 验证：Phase 3 "压缩后继续 5 轮"测试 + 当前 turn 保留验证。
- **用例 C**：在长轮次与压缩后场景中，用户利益约束和角色边界不漂移。
  - 验证：反漂移基线验收（锚点保留率 >= 95%，Probe 一致率 >= 90%，安全违规 = 0），离线评估记录存档。

## 9. M2/M3 衔接契约

M2 保证以下接口语义在 M3 阶段稳定：

- `MemoryFlushCandidate` 字段定义（M3 可增字段，不可重定义已有字段语义）。
- `CompactionResult.memory_flush_candidates` 作为 M3 的唯一输入源（ADR 0032）。
- `confidence` 取值范围 `[0.0, 1.0]`。
- `compaction_metadata.schema_version = 1`（M3 可升级版本，但须兼容旧版本读取）。
- `last_compaction_seq` 水位线语义（ADR 0031）：M3 的记忆落盘不改变此重建接口。

M3 接管：
- 通过 `memory_append` 将候选落盘到 `memory/YYYY-MM-DD.md`。
- 将记忆纳入检索闭环（pg_search + pgvector）。
- 进化评测可消费记忆候选与历史记录作为证据输入。

## 10. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Rolling summary 质量不足导致关键信息丢失 | 任务连续性下降 | 最终 prompt 可见性校验 + retry (ADR 0030)；Probe 验收门槛 90%；保留最近 8 turns 原文 |
| tiktoken encoding 与模型不匹配 | token 计数偏差大 | fallback 到 chars/4 (estimate) + 日志告警 (ADR 0029) |
| Compaction LLM 调用额外延迟 | 用户体验 | 仅在超阈值时触发（低频）；timeout 保护 |
| DB migration 与现有数据不兼容 | 部署风险 | 新字段全部 nullable，不影响现有记录 |
| Emergency trim 丢失大量上下文 | 严重降级 | 仅作为最后手段；优先走 degraded path |
| 水位线并发覆盖 | 数据不一致 | lock_token fencing + 单调保护 (ADR 0031/0021) |
| Compaction 后丢失当前 turn | 行为回归 | 水位线重建语义保证 (ADR 0031)；E2E 测试显式验证 |
