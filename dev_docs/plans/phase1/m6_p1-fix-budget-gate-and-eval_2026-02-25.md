# M6 P1 Fix: Budget Gate Integration & Eval Judgment Correction

**Status**: APPROVED
**Date**: 2026-02-25
**Context**: M6 评审结论从"可关闭"调整为 REOPEN FOR FIX，存在 2 个 P1 必修项。

---

## 背景

M6 模型迁移验证已完成功能实现和评测，但评审发现两个 P1 级遗漏：

- **P1-1**: ADR 0041 在线预算闸门已实现（BudgetGate 类完整），但未接入 `chat.send` 主链路。闸门形同虚设，无法触发 `BUDGET_EXCEEDED`。
- **P1-2**: Eval T11/T12（工具任务）判定口径过宽——模型未触发目标工具但有文本响应时仍标记 PASS，存在假阳性。

## P1-1: BudgetGate 接入 chat.send 主链路

### 改动范围

| 文件 | 改动类型 |
|------|---------|
| `src/gateway/app.py` (`_handle_chat_send`) | 核心修复 |
| 现有 gateway 集成测试 fixture | 兼容修复 |
| 新增测试文件 | 新增 |

### 修复后调用链

```
params validate
  → provider bind
    → try_claim_session
      → [SESSION_BUSY? → 返回错误，无预占发生]
      → load_session(force=True)
        → try_reserve(provider, model, session_id, eval_run_id, €0.05)
          → [denied?] raise GatewayError("BUDGET_EXCEEDED") → finally 释放 session
          → handle_message(stream events)
        → finally: settle(reservation_id, €0.05)  [best-effort, 异常不上抛]
      → finally: release_session
```

### 设计决策

**1. try_reserve 位置：session claim 之后**

- SESSION_BUSY 请求不会执行模型，不该消耗预占
- reservation 和 settle 在 session try/finally 内部，生命周期自然嵌套
- 无"预占后 claim 失败"的回滚复杂度

**2. 成本策略：固定预占（Phase 1）**

- `DEFAULT_RESERVE_EUR = 0.05`，try_reserve 和 settle 使用同值
- 不引入 per-model 定价表，不改 `handle_message` 返回值
- 精确结算（接入 OpenAI usage response + per-model pricing）留后续 Milestone

**3. try_reserve 完整参数**

```python
reservation = await budget_gate.try_reserve(
    provider=entry.name,
    model=entry.model,
    estimated_cost_eur=DEFAULT_RESERVE_EUR,
    session_id=parsed.session_id,
    eval_run_id=_extract_eval_run_id(parsed.session_id),
)
```

- `session_id` 和 `eval_run_id` 必须传入，保证预算审计链完整
- `_extract_eval_run_id`: 从 session_id 前缀提取 eval 标识（非 eval 场景返回空串）

**4. settle 日志要求**

settle best-effort 不抛异常，但日志必须包含：
- `reservation_id`
- `session_id`
- `provider`
- `model`

**5. 错误码**

`BUDGET_EXCEEDED` 通过 `GatewayError(message, code="BUDGET_EXCEEDED")` 发出，复用现有 NeoMAGIError → RPCError 映射链路，不新增异常类。

### 现有测试兼容

现有 gateway 集成测试自建 app 缺少 `app.state.budget_gate`。修复方式：在 test fixture 中挂 stub BudgetGate（永远 approve、settle no-op），确保现有测试不因缺少 budget_gate 而崩。

---

## P1-2: Eval T11/T12 判定逻辑修复

### 改动范围

| 文件 | 改动类型 |
|------|---------|
| `scripts/m6_eval.py` (run_t11, run_t12) | 核心修复 |
| `dev_docs/reports/m6_eval_openai_<new_ts>.json` | 新生成（脚本按时间戳命名） |
| `dev_docs/reports/m6_eval_gemini_<new_ts>.json` | 新生成（脚本按时间戳命名） |
| `dev_docs/reports/m6_migration_conclusion.md` | 更新，显式引用新报告文件名和时间戳 |

### 修复内容

T11 (`run_t11`, line 238-241) 和 T12 (`run_t12`, line 280-283) 的 fallback 分支：

```python
# 修复前（假阳性）
elif resp["text"]:
    r.status = "PASS"
    r.detail = "Got response (no tool_call event seen)..."

# 修复后
elif resp["text"]:
    r.status = "FAIL"
    r.detail = "Model responded without triggering <target_tool> tool..."
```

其他 task（T10, T13-T16）判定逻辑不变。

---

## 测试策略

### BudgetGate 单元测试

覆盖 BudgetGate 自身语义：

| 用例 | 验证内容 |
|------|---------|
| `test_budget_exceeded_returns_denied` | cumulative 接近 stop 时，try_reserve 返回 denied |
| `test_budget_settle_adjusts_cumulative` | settle 后 cumulative 正确变化 |
| `test_settle_idempotent` | 重复 settle 不报错不重复扣 |

### 网关接线测试（gateway 层，mock agent_loop）

覆盖 BudgetGate 在 `_handle_chat_send` 中的接线正确性：

| 用例 | 验证内容 |
|------|---------|
| `test_denied_does_not_call_handle_message` | try_reserve denied 时，handle_message 不被调用 |
| `test_denied_does_not_call_settle` | denied 路径不调用 settle（无 reservation 需结算） |
| `test_handle_message_exception_still_settles` | handle_message 抛异常时，settle 仍在 finally 执行 |
| `test_settle_logs_required_fields` | settle 日志包含 reservation_id/session_id/provider/model |
| `test_try_reserve_receives_session_id` | try_reserve 收到正确的 session_id（在线场景） |
| `test_try_reserve_receives_eval_run_id` | try_reserve 收到正确的 eval_run_id（eval session_id 前缀场景） |
| `test_try_reserve_eval_run_id_empty_for_online` | 非 eval session_id 时 eval_run_id 为空串 |
| `test_session_busy_no_reservation` | 预先 claim 会话后发 chat.send，断言 SESSION_BUSY 且 budget_reservations 无新增 |

### E2E 集成测试（WebSocket → Gateway 全链路）

1. 将 `budget_state.cumulative_eur` 置为 24.98
2. 发送 `chat.send` RPC（预占 €0.05，24.98 + 0.05 > 25.00 stop）
3. 断言返回 `BUDGET_EXCEEDED` 错误码
4. 断言 handle_message 未被调用（无 stream event 产生）
5. 断言 `budget_state.cumulative_eur` 仍为 24.98（预占未增加）

### Eval 判定逻辑测试

Mock 一个无 tool_call 但有 text 的 response，断言 T11/T12 返回 FAIL。

### 全量回归

`just test` + `just lint` 通过后才进入 eval 重跑。

---

## 验收标准

| # | 验收项 | 验证方式 |
|---|--------|---------|
| 1 | 预算触顶时返回 `BUDGET_EXCEEDED` | E2E 集成测试 |
| 2 | 拒绝路径不调用 handle_message | E2E 断言无 stream event |
| 3 | 成功路径一定 settle（异常路径也走 finally） | 单元测试 + settle 日志断言 |
| 4 | SESSION_BUSY 路径不发生预占 | 集成测试：预先 claim 后发 chat.send，断言 SESSION_BUSY 且 budget_reservations 无新增 |
| 5 | settle 日志带 reservation_id/session_id/provider/model | 日志格式断言 |
| 6 | try_reserve 传完整参数（含 session_id, eval_run_id） | 代码 review + 单元测试 |
| 7 | 现有集成测试不因 budget_gate 缺失而崩 | CI 全量回归 (`just test`) |
| 8 | T11/T12 未触发目标工具 → FAIL | eval 逻辑单元测试 |
| 9 | Eval 重跑前预算基线化（清零或记录基线） | 执行日志确认 |
| 10 | 全量重跑 T10-T16，生成新时间戳报告 + 结论文档引用新文件名 | 产出物检查 |

---

## 执行顺序

1. **P1-1 代码修复**：`src/gateway/app.py` 接入 BudgetGate
2. **现有测试兼容修复**：gateway 集成测试 fixture 挂 stub BudgetGate
3. **P1-2 代码修复**：`scripts/m6_eval.py` T11/T12 判定逻辑
4. **新增测试**：单元测试 + E2E 集成测试
5. **全量回归**：`just test` + `just lint`
6. **Eval 预算基线化**：重跑前将 `budget_state.cumulative_eur` 清零（或记录当前基线值），确保 T10-T16 不因历史累积误触 BUDGET_EXCEEDED
7. **Eval 重跑**：全量 T10-T16（OpenAI → Gemini）
8. **报告归档**：生成新时间戳 JSON 报告（不覆盖旧文件），更新迁移结论文档显式引用新报告文件名（标注"P1 修复后重跑"）

---

## 不在本轮 scope 的事项

- Per-model 定价表 + 精确成本计算（后续 M）
- `handle_message` 返回 token usage（后续 M）
- 预算阈值调优（当前 €20/€25 保持不变）
- Eval 新增 task（T17+ 等）
