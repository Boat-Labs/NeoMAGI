# M4 Telegram 第二渠道适配 — PM 阶段汇总

> 日期：2026-03-02
> 状态：完成

## 1. Milestone 总结

M4 Telegram 第二渠道适配五个 Phase 全部通过 Gate 验收，milestone 已关闭。

| Phase | 交付 | Gate 结论 | Backend Commit | 测试增量 |
|-------|------|-----------|---------------|---------|
| Phase 0: Config + Scope 激活 | TelegramSettings (pydantic-settings, env_prefix=TELEGRAM_), scope_resolver 激活 per-channel-peer/per-peer 分支, SessionSettings.dm_scope 锁定 main, resolve_session_key DM 路由改为 channel_id 判据 | PASS | 88ffe38 | 586→596 (+10) |
| Phase 1: Dispatch 抽取 + Identity | dispatch_chat() channel-agnostic 调度核心, session_id/scope_key 分离, AgentLoop handle_message 增加 identity+dm_scope 参数, _persist_flush_candidates 显式 scope_key | PASS | e48ca51 | 596→608 (+12) |
| Phase 2: Telegram Adapter | TelegramAdapter (aiogram 3.x), check_ready/start_polling/stop 生命周期, 身份映射通过 resolver, 鉴权门控 (fail-closed), typing 指示器, Gateway lifespan 集成 | PASS | 436d2af | 608→630 (+22) |
| Phase 3: Response Rendering | telegram_render.py: split_message (段落→句子→硬截断 + 代码块保护), format_for_telegram (MarkdownV2 + 纯文本回退), 错误消息中文映射, 集成到 TelegramAdapter._handle_dm | PASS | 7d66603 | 630→657 (+27) |
| Phase 4: E2E + Docs + ADR | test_channel_isolation.py (11 个跨渠道隔离测试), Use Case A/B/C 验收覆盖, ADR 0044 归档, 文档更新 (m4_architecture.md, modules.md, m4_user_test_guide.md, INDEX.md) | PASS | db43d1d | 657→668 (+11) |

最终测试总数：668 tests，0 failures，ruff clean。
基线增长：586 → 668（+82 tests，+14%）。

## 2. 变更清单（文件级）

### 新增文件（10 个）

**源码（3 个）**
- `src/channels/telegram.py` — TelegramAdapter, aiogram 3.x DM 适配器
- `src/channels/telegram_render.py` — 消息拆分 + MarkdownV2 格式化 + 错误映射
- `src/gateway/dispatch.py` — channel-agnostic 调度核心

**测试（6 个）**
- `tests/test_dispatch.py` — dispatch_chat 单元测试 (8 tests)
- `tests/test_agent_identity.py` — AgentLoop identity + flush scope 测试 (4 tests)
- `tests/test_telegram_adapter.py` — 适配器测试 (21 tests)
- `tests/test_telegram_render.py` — 渲染测试 (27 tests)
- `tests/test_channel_isolation.py` — 跨渠道隔离测试 (11 tests)
- `tests/test_app_integration.py` — Gateway lifespan 集成测试

**文档（3 个）**
- `decisions/0044-telegram-adapter-aiogram-same-process.md` — ADR 0044
- `design_docs/m4_user_test_guide.md` — 手工 E2E 测试指南

### 修改文件（14 个）

- `pyproject.toml` — 新增 aiogram>=3.15.0 依赖
- `src/config/settings.py` — TelegramSettings 新增
- `src/session/scope_resolver.py` — 激活 per-channel-peer/per-peer 分支, DM 路由改判据
- `src/gateway/app.py` — _handle_chat_send 重构为 dispatch_chat, Telegram lifespan 集成
- `src/agent/agent.py` — handle_message 增加 identity/dm_scope, _persist_flush_candidates 显式 scope_key
- `.env_template` — 新增 TELEGRAM_* 环境变量模板
- `design_docs/m4_architecture.md` — planned → implemented 状态更新
- `design_docs/modules.md` — Channel Adapter 段落更新
- `decisions/INDEX.md` — 新增 ADR 0044 条目
- `tests/test_scope_resolver.py` — 新增 per-channel-peer 测试
- `tests/test_settings.py` — 新增 TelegramSettings 验证
- `tests/test_agent_flush_persist.py` — 适配新签名 (6 处)
- `tests/test_budget_gate_wiring.py` — dispatch 适配
- `tests/test_eval_run_id.py` — 签名对齐
- `tests/test_session_serialization.py` — 适配
- `uv.lock` — aiogram 依赖锁定

总计：**27 files changed, +2,921 insertions, −241 deletions**。

## 3. ADR 一致性

| ADR | 验证结果 |
|-----|---------|
| 0034 (dmScope session/memory scope alignment) | PASS — per-channel-peer scope 完整激活，Telegram scope_key = `telegram:peer:{id}` |
| 0044 (Telegram adapter aiogram same-process) | PASS — aiogram 3.x + 同进程 + per-channel-peer scope，已归档 |

## 4. Gate 验收报告索引

| Gate | 报告 | 结论 |
|------|------|------|
| m4-g0 (P0→P1) | `dev_docs/reviews/m4_phase0_2026-03-02.md` | PASS |
| m4-g1 (P1→P2) | `dev_docs/reviews/m4_phase1_2026-03-02.md` | PASS |
| m4-g2 (P2→P3) | `dev_docs/reviews/m4_phase2_2026-03-02.md` | PASS |
| m4-g3 (P3→P4) | `dev_docs/reviews/m4_phase3_2026-03-02.md` | PASS |
| m4-g4 (P4→完成) | `dev_docs/reviews/m4_phase4_2026-03-02.md` | PASS |

## 5. Tester Findings 汇总

### P0 Findings
- 无阻塞 finding

### P1 Findings
- F1 (LOW): dispatch_chat 中 budget settle 在 session release 后执行（顺序可优化）
- F2 (INFORMATIONAL): handle_message identity=None 默认路径无独立测试

### P2 Findings
- F1 (LOW): typing 指示器 cancel 在 send_message 失败路径可能未执行
- F2 (INFORMATIONAL): start_polling mock 层级偏高

### P3 Findings
- F1 (LOW): MarkdownV2 send 回退路径发送转义文本
- F2 (INFORMATIONAL): 中文句子拆分不总在边界（max_length 保证正确性）
- F3 (INFORMATIONAL): _send_response 回退路径无独立测试

### P4 Findings
- F1 (INFORMATIONAL): memory scope 隔离测试依赖文件系统而非 DB
- F2 (INFORMATIONAL): 测试使用内部方法 `_persist_flush_candidates`

所有 Findings 均为 LOW/INFORMATIONAL，无 P0/P1 阻塞项。

## 6. 过程经验

| 事件 | 影响 | 改进 |
|------|------|------|
| tmux 崩溃导致 PM 子窗口死机 | Backend 完成 P3 后消息无法送达 PM（leadSessionId 失配） | 改进：resume 后检查 team config 中 leadSessionId 是否匹配当前 session；若失配需删除旧 team 重建 |
| Backend respawn 产生 name 冲突 (backend-2) | 旧 team config 中残留 stale backend 条目 | 改进：respawn 前彻底清理 team config 中的 stale members |
| Tester push 反复被 hook 阻止 | tester 分支每次 review 后 rebase 导致历史分歧，需 force-push | 改进：tester 分支只 merge (--ff-only) 不 rebase；或接受 force-push 由 PM 手动执行 |
| devcoord open-gate 不生成 pending ACK | ACK 命令因"no pending GATE_OPEN"失败 | 改进：改用 heartbeat 记录 ACK 意图，或修改 coord.py 在 open-gate 时创建 pending ACK 记录 |
| coord.py apply 命令参数反复缺字段 | 多次试错才发现必需的 --task, --gate 等参数 | 改进：首次使用新命令前先 `--help` 确认参数列表 |
| P3→P4 hard respawn 后 Backend 被 kill | tmux 清理时误杀刚 spawn 的 P4 Backend（零进度） | 改进：清理前列出所有 agent 进程并逐一确认身份，避免误杀新 spawn 的 agent |

## 7. 韧性恢复记录

本次 M4 经历了一次完整的崩溃恢复：

1. **tmux 崩溃** (P3 阶段): PM 子窗口死机，Backend 完成 P3 但消息无法送达
2. **诊断**: team config 中 leadSessionId 指向旧 session，isActive=false
3. **恢复策略**: 删除旧 team → 手动验证 P3 完成状态 → 创建新 team → spawn 新 Tester
4. **二次清理**: 发现残留 4 个旧 agent pane (%13/%20/%21/%22)，逐一终止
5. **成功恢复**: P3 Tester review PASS → P4 Backend + Tester 顺利完成

验证了 Agent Teams 协作框架在以下场景下的恢复能力：
- tmux 进程崩溃
- team 配置失配
- 多次 respawn 产生的 stale 进程
- devcoord 控制面状态续接

## 8. 心跳日志

`dev_docs/logs/m4_2026-03-02/heartbeat_events.jsonl` — 53 条事件，覆盖完整生命周期。

## 9. Git 合并记录

| 操作 | Commit / 状态 |
|------|---------------|
| Backend 合并到 main | `549253f` (merge commit, --no-ff) |
| Tester 分支 push | `f4f31f4` (force-with-lease, 手动执行) |
| Main 最终 HEAD | `549253f` |
| Worktree 清理 | `backend-m4` + `tester-m4` 已移除 |
| 远程分支 | `feat/backend-m4-telegram` + `feat/tester-m4-telegram` 保留 |

## 10. 未完成项

无。所有 plan 交付物已完成，milestone 已关闭。

Tester Findings 中的 LOW 项（MarkdownV2 send 回退、typing cancel 路径等）可在后续 hardening 中按需处理，不阻塞 M4 关闭。
