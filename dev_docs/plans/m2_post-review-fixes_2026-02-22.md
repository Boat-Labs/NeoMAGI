# M2 Post-Review Fixes 实现计划

> 状态：approved
> 日期：2026-02-22
> 触发：用户对 M2 交付代码的审阅（6 个 findings: 1×P0, 3×P1, 2×P2）
> 变更记录：
> - v2: 修正了 F2 参数传递、F6 降级路径、F3 锚点规则决策、测试路径、budget 重校验完整性
> - v3: F2 补完整闭环（store→rebuild→recheck）；去掉不存在的 test_websocket.py 引用；F2 _emergency_trim 保持同步函数
> - v4: F2 明确 store 所有权（_try_compact 内部 store 正常结果，overflow retry 仅 store emergency 结果）；F1 恢复 integration/test_websocket.py（修正路径）；F2 伪代码对齐 get_effective_history 真实接口（同步 + last_compaction_seq 参数）
> - v5: F2 overflow retry store/rebuild 加 try/except 兜底（fail-open 一致性）；F1 测试语义纠正（test_app_integration 验真实装配，test_websocket 回归验证）
> - v6: F2 except 分支修正 — 已知超预算 context 不送模型，统一走 fail-open 返回错误消息
> - v7: F3 补遗漏的 test_compaction_smoke.py 适配（CompactionEngine 构造签名变更）
> - v8: F4 补 chat() 替身签名适配（3 个 integration 测试）；F2 reduced_turns 公式修正（保证单调减少）

## 1. Findings 汇总

| # | 严重度 | 描述 | 位置 |
|---|--------|------|------|
| F1 | P0 | app.py 未注入 compaction_settings，M2 功能在真实运行路径失效 | `src/gateway/app.py:65` |
| F2 | P1 | 压缩后未重校验预算，缺失 overflow 重试链路 | `src/agent/agent.py:197→211` |
| F3 | P1 | 锚点校验仅做 `len(system_prompt) > 50`，ADR 0030 未落地 | `src/agent/compaction.py:313-327` |
| F4 | P1 | summary_temperature 配置未生效，model_client 不支持 temperature | `src/agent/compaction.py:310`, `src/agent/model_client.py:156` |
| F5 | P2 | max_candidate_text_bytes 按字符截断，中文场景超限 | `src/agent/memory_flush.py:114` |
| F6 | P2 | 摘要 30% 上限被 `max(..., 200)` 放宽 | `src/agent/compaction.py:223` |

## 2. 修复方案

### F1 (P0): app.py 注入 compaction_settings

**改动文件**: `src/gateway/app.py`
**改动位置**: lifespan 函数中 AgentLoop 构造处（line 65）

**修复**:
```python
agent_loop = AgentLoop(
    model_client=model_client,
    session_manager=session_manager,
    workspace_dir=settings.workspace_dir,
    model=settings.openai.model,
    tool_registry=tool_registry,
    compaction_settings=settings.compaction,  # 新增
)
```

**验证**:
- 新增测试 `tests/test_app_integration.py`：构造真实 app lifespan（走 app.py 路径），断言 `agent_loop._settings is not None`（**真实装配路径验证**）
- 修改 `tests/integration/test_websocket.py`：注入 compaction_settings，确保 WebSocket 集成路径不因新参数回退（**回归验证**）

---

### F2 (P1): 压缩后重校验预算 + overflow 重试

**改动文件**: `src/agent/agent.py`
**改动位置**: compaction 完成后的重建逻辑（line 197 之后）

**修复**: compaction 后回到 budget check，重算 token（messages + tools schema 一并计数，与主路径一致）。若仍超限：
1. 第一次重试：通过参数传递缩减后的 `min_preserved_turns`（如减半），调用 `_emergency_trim(..., min_preserved_turns_override=...)`
2. emergency_trim 成功：**必须完成完整闭环** — store_compaction_result → 重建 context → 再次 budget check
3. **统一 fail-open 出口**（以下三种情况均不调模型，直接返回可读错误消息给用户，会话状态保留）：
   - emergency_trim 返回 None（无法进一步裁剪）
   - 完整闭环后仍超预算（两轮压缩不够）
   - store/rebuild 过程异常（SessionFencingError、DB 等）

**关键约束**：
- 缩减 `min_preserved_turns` 通过参数传递给 `_emergency_trim`，禁止修改共享的 `self._settings`，防止并发请求间状态污染
- **store 所有权**：`_try_compact` 内部负责正常 compaction 结果的 `store_compaction_result`（agent.py:365-368）；overflow retry 代码仅在 emergency_trim 成功时执行一次新 store（新 CompactionResult，新水位线，不冲突）

```python
# 伪代码：在 _try_compact 返回后（现有 agent.py:187-204 rebuild 逻辑之后）
# 注意：_try_compact 内部已完成 store_compaction_result，此处不重复 store

# ── Step 1: 现有 rebuild 逻辑（agent.py:188-204，已实现）──
last_compaction_seq = compact_result.new_compaction_seq
compacted_context = compact_result.compacted_context
system_prompt = self._prompt_builder.build(session_id, mode, compacted_context=compacted_context)
effective_msgs = self._session_manager.get_effective_history(  # 同步，非 async
    session_id, last_compaction_seq
)
messages = [
    {"role": "system", "content": system_prompt},
    *_messages_with_seq_to_openai(effective_msgs),
]

# ── Step 2 (F2 新增): Post-compaction budget recheck ──
rebuilt_tokens = (
    self._budget_tracker.counter.count_messages(messages)
    + self._budget_tracker.counter.count_tools_schema(tools_schema)
)
post_status = self._budget_tracker.check(rebuilt_tokens)

if post_status.status == "compact_needed":
    # ── Step 3: Overflow emergency trim（参数传递，不改全局 settings）──
    original_turns = self._settings.min_preserved_turns
    reduced_turns = max(original_turns // 2, 1)
    if reduced_turns >= original_turns:
        # 已无法进一步缩减（original_turns <= 1），直接 fail-open
        logger.error(
            "cannot_reduce_preserved_turns",
            original_preserved=original_turns,
        )
        # 返回错误消息给用户，不调模型
        ...
    else:
        logger.warning(
            "post_compaction_still_over_budget",
            original_preserved=original_turns,
            reduced_preserved=reduced_turns,
        )
        emergency_result = self._emergency_trim(
            session_id=session_id,
            current_user_seq=current_user_seq,
            min_preserved_turns_override=reduced_turns,
        )
        if emergency_result is None:
            # fail-open: 返回错误消息给用户，不调模型
            ...
        else:
            # ── Step 4: emergency trim 成功 → store + rebuild + recheck ──
            # 这是本链路中唯一的 store（_try_compact 的 store 已在 Step 1 之前完成）
            # 整段包裹 try/except：若 store/rebuild 失败，走 fail-open 返回错误消息。
            # （Step 1 context 已确认超预算，不能送模型；正常 compaction 已持久化，会话状态安全。）
            try:
                await self._session_manager.store_compaction_result(
                    session_id, emergency_result, lock_token=lock_token
                )
                last_compaction_seq = emergency_result.new_compaction_seq
                compacted_context = emergency_result.compacted_context
                system_prompt = self._prompt_builder.build(
                    session_id, mode, compacted_context=compacted_context
                )
                effective_msgs = self._session_manager.get_effective_history(  # 同步
                    session_id, last_compaction_seq
                )
                messages = [
                    {"role": "system", "content": system_prompt},
                    *_messages_with_seq_to_openai(effective_msgs),
                ]
                final_tokens = (
                    self._budget_tracker.counter.count_messages(messages)
                    + self._budget_tracker.counter.count_tools_schema(tools_schema)
                )
                final_status = self._budget_tracker.check(final_tokens)
                if final_status.status == "compact_needed":
                    # 两轮压缩后仍超限 → fail-open
                    logger.error(
                        "emergency_trim_still_over_budget",
                        tokens=final_tokens,
                    )
                    # 返回错误消息给用户，不调模型
                    ...
            except Exception:
                # store/rebuild 异常（SessionFencingError、DB 等）→ fail-open
                # Step 1 context 已确认超预算，不能送模型（会触发 provider overflow）
                # 正常 compaction 已持久化，会话状态安全；下次请求可重新尝试
                logger.exception(
                    "overflow_emergency_store_failed",
                    session_id=session_id,
                )
                # 与其他 fail-open 出口统一：返回错误消息给用户，不调模型
                ...
```

**`_emergency_trim` 签名调整**（同步函数，与当前实现一致）:
```python
def _emergency_trim(
    self,
    ...,
    min_preserved_turns_override: int | None = None,
) -> CompactionResult | None:
    """Emergency trim fallback (同步，纯内存裁剪，无 I/O).

    min_preserved_turns_override: 本次调用专用值，不修改 self._settings。
    None 时使用 self._settings.min_preserved_turns。
    """
    effective_preserved = min_preserved_turns_override or self._settings.min_preserved_turns
    ...
```

**与 plan 对齐**: Section 5.4 降级层次第 5 点。

**验证**:
- 新增测试：mock compaction 返回但 token 仍超限（含 tools schema）→ 触发 emergency trim（验证 reduced_turns 参数传递）
- 新增测试：emergency trim 成功 → 验证 store_compaction_result 被调用、context 被重建、budget 被 recheck
- 新增测试：emergency trim 成功但 store 抛 SessionFencingError → fail-open（返回错误消息，不调模型）
- 新增测试：emergency trim 后仍超限 → fail-open 返回错误
- 新增测试：确认 `self._settings.min_preserved_turns` 未被修改（并发安全）

---

### F3 (P1): 锚点校验落地（ADR 0030）

**改动文件**: `src/agent/compaction.py`
**改动位置**: `_validate_anchors` 方法（line 313-327）

**锚点提取规则（已决策，不再是开放问题）**:
- 从 workspace 文件（AGENTS.md / SOUL.md / USER.md）提取**第一个非空行**（标题行）作为锚点 probe
- 理由：最小实现、不需要修改 workspace 文件格式、可测试、可预测
- 如果某文件不存在或为空，跳过该文件的锚点（不阻塞 compaction）
- 由于 system prompt 始终包含完整 workspace context，正常路径下此校验始终通过，仅在 prompt 组装异常时触发

**实现**:
```python
# 锚点文件列表（固定）
_ANCHOR_FILES = ("AGENTS.md", "SOUL.md", "USER.md")

def _extract_anchor_phrases(self) -> list[str]:
    """Extract first non-empty line from each workspace anchor file."""
    anchors = []
    for filename in _ANCHOR_FILES:
        filepath = self._workspace_dir / filename
        if not filepath.exists():
            continue
        try:
            text = filepath.read_text(encoding="utf-8")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped:
                    anchors.append(stripped)
                    break
        except OSError:
            logger.warning("anchor_file_read_error", file=filename)
    return anchors

def _validate_anchors(
    self,
    system_prompt: str,
    compacted_context: str | None,
    effective_history_text: str,
) -> bool:
    """Validate anchor visibility in final model context (ADR 0030).

    Checks that first non-empty line from AGENTS/SOUL/USER files
    is present in the final context sent to the model.
    校验对象：最终 prompt 可见性（system_prompt + compacted_context + effective_history）。
    """
    if not system_prompt:
        return False

    final_context = system_prompt + (compacted_context or "") + effective_history_text

    anchor_phrases = self._extract_anchor_phrases()
    if not anchor_phrases:
        # 无锚点可校验时视为通过（不阻塞 compaction）
        return True

    for phrase in anchor_phrases:
        if phrase not in final_context:
            logger.warning("anchor_missing", phrase=phrase[:80])
            return False

    return True
```

**CompactionEngine 构造函数新增 `workspace_dir` 参数**：
```python
def __init__(
    self,
    model_client: OpenAICompatModelClient,
    token_counter: TokenCounter,
    settings: CompactionSettings,
    workspace_dir: Path,  # 新增
) -> None:
    ...
    self._workspace_dir = workspace_dir
```

调用方（agent.py）传入 `workspace_dir`。

**受影响的现有实例化点**（均需适配新签名，补传 `workspace_dir`）：
- `src/agent/agent.py:84` — 生产调用方
- `tests/test_compaction.py:68` — fixture
- `tests/test_compaction_smoke.py:65, 128` — 直接实例化

**验证**:
- 新增测试：正常 system_prompt（含 workspace 首行）→ 校验通过
- 新增测试：system_prompt 缺失 AGENTS.md 首行 → 校验失败 → retry → degraded
- 新增测试：workspace 文件不存在 → 无锚点 → 视为通过

---

### F4 (P1): model_client 支持 temperature + compaction 传入

**改动文件**:
- `src/agent/model_client.py`（扩展 chat 接口）
- `src/agent/compaction.py`（传入 temperature）

**修复 model_client.py**:
```python
async def chat(
    self,
    messages: list[dict[str, Any]],
    model: str,
    temperature: float | None = None,  # 新增，可选
) -> str:
    response = await self._retry_call(
        lambda: self._client.chat.completions.create(
            model=model,
            messages=messages,
            **({"temperature": temperature} if temperature is not None else {}),
        ),
        context="chat",
    )
    ...
```

**修复 compaction.py**:
```python
# _generate_summary 中
response = await self._model_client.chat(
    messages, model, temperature=self._settings.summary_temperature
)
```

**向后兼容**: temperature 默认 None，不传时使用 OpenAI 默认值，不影响其他调用方。

**受影响的测试替身**（`chat()` 签名需同步补 `temperature: float | None = None`）：
- `tests/integration/test_websocket.py:44`
- `tests/integration/test_tool_modes_integration.py:51`
- `tests/integration/test_tool_loop_flow.py:47`

**验证**:
- 新增测试：mock model_client，断言 compaction 调用时 temperature=0.1
- 新增测试：chat() 不传 temperature 时行为不变

---

### F5 (P2): max_candidate_text_bytes 按 UTF-8 字节截断

**改动文件**: `src/agent/memory_flush.py`
**改动位置**: line 114

**修复**:
```python
# 替换: text = stripped[:self._max_text_bytes]
# 改为:
encoded = stripped.encode("utf-8")
if len(encoded) > self._max_text_bytes:
    text = encoded[:self._max_text_bytes].decode("utf-8", errors="ignore")
else:
    text = stripped
```

**验证**:
- 新增测试：中文文本超过 2048 字节时正确截断（不在字符中间断开）
- 新增测试：ASCII 文本行为不变

---

### F6 (P2): 严格遵守 30% 上限，小输入走 degraded（非 noop）

**改动文件**: `src/agent/compaction.py`
**改动位置**: line 223

**修复**:
```python
# 替换: max_summary_tokens = max(int(input_tokens * 0.3), 200)
# 改为:
max_summary_tokens = int(input_tokens * 0.3)
if max_summary_tokens < 100:
    # 输入过小，摘要无意义
    # 走 degraded 路径：trim-only，不生成摘要，但仍推进 watermark
    logger.info(
        "input_too_small_for_summary",
        input_tokens=input_tokens,
        max_summary_tokens=max_summary_tokens,
    )
    return CompactionResult(
        status="degraded",
        compacted_context=previous_compacted_context,  # 保留上次摘要（如有）
        compaction_metadata={
            "schema_version": 1,
            "status": "degraded",
            "reason": "input_too_small_for_summary",
            "flush_skipped": flush_skipped,
            ...
        },
        new_compaction_seq=compressible_end_seq,  # 推进水位线
        memory_flush_candidates=flush_candidates,
        preserved_messages=preserved,
    )
```

**关键修正**（来自审阅反馈）：小输入场景**不能走 noop**，因为：
- 当前状态已是 `compact_needed`，noop 不推进 watermark 也不落库
- 同请求内会反复触发但不减压，导致无进展死循环

改为走 **degraded 路径**：不生成摘要，但仍推进 watermark（裁剪可压缩区间），保证减压有进展。

**验证**:
- 新增测试：小输入（input_tokens < 334）→ status=degraded，watermark 前进，不走 noop
- 修改现有测试：验证任何输入下摘要 token 不超过 `input_tokens * 0.3`

---

## 3. 修复顺序

| 步骤 | Finding | 依赖 | 说明 |
|------|---------|------|------|
| 1 | F1 (P0) | 无 | app.py 一行修复，立即解锁真实路径 |
| 2 | F4 (P1) | 无 | model_client 扩展，独立修复 |
| 3 | F3 (P1) | 无 | 锚点校验，需新增 workspace_dir 参数 |
| 4 | F2 (P1) | F1 | overflow 重试，依赖 compaction 实际可触发 |
| 5 | F5 (P2) | 无 | 独立修复 |
| 6 | F6 (P2) | 无 | 独立修复 |

## 4. 测试策略

### 新增/修改测试

| 测试文件 | 覆盖 | 标记 |
|----------|------|------|
| `tests/test_app_integration.py`（新增） | F1: 真实 app lifespan 装配验证（走 app.py 路径） | `@pytest.mark.integration` |
| `tests/integration/test_websocket.py`（修改） | F1: 回归验证 — 注入 compaction_settings，确保 WebSocket 路径不因新参数回退 | 已有 |
| `tests/test_agent_compaction_integration.py`（追加） | F2: 压缩后仍超限（含 tools schema）→ emergency trim（验证参数传递 + settings 未变）→ store→rebuild→recheck 闭环 → fail-open | 已有 |
| `tests/test_compaction.py`（追加） | F3: 锚点校验正常通过 / 缺失→失败→retry / workspace 文件不存在→通过 | 已有 |
| `tests/test_compaction_smoke.py`（修改） | F3: 适配 CompactionEngine 构造签名（补传 workspace_dir） | 已有 |
| `tests/test_compaction.py`（追加） | F4: temperature 传递验证 | 已有 |
| `tests/integration/test_websocket.py`（修改） | F4: chat() 替身签名适配（补 temperature 参数） | 已有 |
| `tests/integration/test_tool_modes_integration.py`（修改） | F4: chat() 替身签名适配 | 已有 |
| `tests/integration/test_tool_loop_flow.py`（修改） | F4: chat() 替身签名适配 | 已有 |
| `tests/test_memory_flush.py`（追加） | F5: UTF-8 字节截断（中文 + ASCII） | 已有 |
| `tests/test_compaction.py`（修改） | F6: 小输入→degraded（非 noop）+ watermark 前进 + 30% 严格遵守 | 已有 |

### 回归
- `just test` 全量通过（含 unit + integration 标记的测试）
- `just lint` 通过

## 5. 涉及文件总览

| 文件 | 变更类型 | Finding |
|------|----------|---------|
| `src/gateway/app.py` | 修改（1 行） | F1 |
| `src/agent/agent.py` | 修改（~30 行） | F2 |
| `src/agent/compaction.py` | 修改（~60 行） | F3, F6 |
| `src/agent/model_client.py` | 修改（~5 行） | F4 |
| `src/agent/memory_flush.py` | 修改（~5 行） | F5 |
| `tests/test_app_integration.py` | 新增 | F1 |
| `tests/integration/test_websocket.py` | 修改 | F1, F4 |
| `tests/test_agent_compaction_integration.py` | 修改 | F2 |
| `tests/test_compaction.py` | 修改 | F3, F4, F6 |
| `tests/test_compaction_smoke.py` | 修改 | F3 |
| `tests/integration/test_tool_modes_integration.py` | 修改 | F4 |
| `tests/integration/test_tool_loop_flow.py` | 修改 | F4 |
| `tests/test_memory_flush.py` | 修改 | F5 |

## 6. 已关闭的开放问题

| # | 问题 | 决策 | 理由 |
|---|------|------|------|
| OQ1 | 锚点提取规则 | 从 AGENTS.md / SOUL.md / USER.md 提取第一个非空行（标题行）作为锚点 probe | 最小实现、不需改 workspace 文件格式、可测试 |
| OQ2 | F6 小输入场景 | 走 degraded 路径（trim-only + 推进 watermark），不走 noop | noop 不推进 watermark，会导致无进展死循环 |
