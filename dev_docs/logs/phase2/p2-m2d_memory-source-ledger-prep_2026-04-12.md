---
doc_id: 019d812c-a203-7530-a23e-c8c96ca984a9
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-12T12:11:19+02:00
---
# P2-M2d 实现日志：Memory Source Ledger Prep for P2-M3

> 日期：2026-04-12
> 计划：`dev_docs/plans/phase2/p2-m2d_memory-source-ledger-prep_2026-04-11.md`

## 实现总结

按 ADR 0060 迁移步骤 1-2，为 P2-M3 准备 DB memory source ledger 写入地基。新 memory 写入同时出现在 DB ledger（truth）与 workspace daily note（projection）；parity checker 可报告两者不一致。

### 新增文件 (6)

| 文件 | 说明 |
|------|------|
| `src/memory/ledger.py` | MemoryLedgerWriter — append-only truth writer (raw SQL, CAST jsonb, ON CONFLICT DO NOTHING) |
| `src/memory/parity.py` | MemoryParityChecker + ParityReport (ID + content + metadata 两层比对) |
| `alembic/versions/e2f3a4b5c6d7_create_memory_source_ledger.py` | Migration: 1 张表 + 3 索引 + partial unique index |
| `design_docs/data_models/postgresql/memory_source_ledger.md` | 逐表文档 |
| `tests/test_memory_ledger.py` | 9 个单元测试 (mock session factory) |
| `tests/test_memory_parity.py` | 9 个单元测试 |

### 修改文件 (20)

| 文件 | 变更 |
|------|------|
| `src/memory/writer.py` | 双模式写入 (ledger-wired truth-first / no-ledger fallback); MemoryWriteResult 返回值; _try_write_projection() best-effort; mkdir 移入各模式分支 |
| `src/memory/indexer.py` | `_parse_entry_metadata()` 扩展 `source` 字段提取 |
| `src/agent/agent.py` | AgentLoop 新增 `memory_writer` 可选参数，优先使用外部注入 |
| `src/tools/builtins/memory_append.py` | execute() 返回结构适配 MemoryWriteResult (ok/entry_id/ledger_written/projection_written/path) |
| `src/gateway/app.py` | Wiring: MemoryLedgerWriter → MemoryWriter → MemoryAppendTool + AgentLoop |
| `src/session/database.py` | +`_create_memory_source_ledger_table()` idempotent DDL (含 partial unique index DO block) |
| `src/infra/errors.py` | +`LedgerWriteError` |
| `src/infra/doctor.py` | +D5 memory ledger parity check; 异常路径改为 WARN |
| `src/infra/preflight.py` | `_REQUIRED_TABLES` 新增 `memory_source_ledger` |
| `scripts/backup.py` | `TRUTH_TABLES` 新增 `neomagi.memory_source_ledger` |
| `scripts/restore.py` | Step 7.5: fail-fast on missing ledger truth table |
| `design_docs/data_models/postgresql/index.md` | 新增 Memory Truth 分组 + 表清单入口 |
| `tests/conftest.py` | Integration truncation allow-list 新增 `memory_source_ledger` |
| `tests/test_memory_writer.py` | 现有断言迁移 (Path→MemoryWriteResult) + 6 个 ledger-wired 模式测试 |
| `tests/test_memory_indexer.py` | 2 个 source 字段提取测试 + 现有测试加 source 断言 |
| `tests/test_memory_append_tool.py` | 返回结构断言适配 |
| `tests/test_backup.py` | TRUTH_TABLES 数量和成员断言更新 |
| `tests/test_doctor.py` | 3 个 D5 parity 测试 |
| `tests/test_preflight.py` | 默认 tables fixture 加入 memory_source_ledger |
| `tests/test_restore.py` | 3 个 step 7.5 分支测试 + composite test sequence 更新 |

### DB 表

- `memory_source_ledger` (append-only): event_id(VARCHAR PK), entry_id, event_type, scope_key, source, source_session_id, content, metadata(JSONB), created_at
- Partial unique index `uq_memory_source_ledger_entry_append` on `(entry_id) WHERE event_type = 'append'`
- 3 regular indexes: entry_id, scope_key, created_at

### 关键设计决策

- **event_id 与 entry_id 分离**: 每行有独立 event_id (UUIDv7 PK)，entry_id 标识被操作的 memory 条目；partial unique 只约束 append 事件，不锁死未来 correction/retraction 路径
- **双模式 MemoryWriter**: ledger-wired → truth-first (ledger 失败阻断, projection best-effort); no-ledger fallback → projection mandatory (保留 pre-M2d 行为)
- **Idempotent no-op early return**: ledger append 返回 False → 跳过 projection，不制造 drift
- **Clean-start baseline**: 系统未上线，不做历史迁移；验收用空 ledger + 空 workspace
- **Projection mkdir 不阻断 truth**: ledger-wired 模式下 mkdir 移入 `_try_write_projection()` 内部 best-effort
- **CAST(:metadata AS jsonb)**: 避免 SQLAlchemy 误解析 `:metadata::jsonb` bind parameter

## Review Findings & Fixes (5 rounds, 21 findings)

### Plan Review (Round 1-5, pre-implementation)

| Round | 发现 | 关键修正 |
|-------|------|---------|
| R1 (4P1+1P2+2低) | entry_id UNIQUE 锁死未来; AgentLoop 自建 writer; parity 仅比 ID; backup 缺 truth table | event_id PK + partial unique; AgentLoop 外部注入; content-level parity; TRUTH_TABLES 更新 |
| R2 (1P1+1P2+2方向性+1低) | 系统未上线无需迁移; 写入顺序应翻转; parser 缺 source | D7 clean-start; D3 truth-first; parser 扩展 |
| R3 (1P1+2P2) | size check 阻断 truth; 返回值误导; reindex 丢 ledger-only | _try_write_projection 内化 size check; MemoryWriteResult; 风险表记录 |
| R4 (2P1+2P2) | 返回值迁移未覆盖; no-ledger fallback 丢 mandatory; _write_ledger 丢 bool; flush 计数不明 | 双模式表格; 测试迁移段; 直接传播 bool; 计数规则更新 |
| R5 (2P2) | idempotent no-op 仍写 projection; tool result 结构不明 | early return; 结构化返回字段 |

### Implementation Review (2 rounds, post-implementation)

| # | 级别 | 问题 | 修复 |
|---|------|------|------|
| 1 | P1 | `:metadata::jsonb` SQLAlchemy bind 解析错误 | 改为 `CAST(:metadata AS jsonb)` |
| 2 | P1 | `memory_dir.mkdir()` 在 ledger 写入前执行阻断 truth | mkdir 移入各模式分支 |
| 3 | P2 | `_REQUIRED_TABLES` 缺 `memory_source_ledger` | 加入 preflight + 更新测试 |
| 4 | P2 | Doctor 异常路径返回 OK 掩盖故障 | 改为 WARN |
| 5 | P2 | Scoped parity 不过滤 workspace scope | `_scan_workspace(scope_key=...)` |
| 6 | P2 | `elif` 导致 content+metadata mismatch 互斥 | 改为独立 `if` |
| 7 | P2 | Restore 7.5 缺表返回 OK | 改为 `_fail_restore_step()` |
| 8 | P2 | conftest truncation 缺 ledger table | 加入 allow-list |
| 9 | P3 | Composite restore test 缺 connect() mock | 补 ledger_execute + step7_5_ledger 断言 |

## 测试

- 新增 **~30 tests** (9 ledger + 9 parity + 6 writer dual-mode + 2 indexer + 3 doctor + 3 restore + ...)
- 现有测试迁移: test_memory_writer (Path→MemoryWriteResult), test_memory_append_tool, test_backup, test_preflight
- 全量回归: **1803 unit passed** + **81 integration passed**, 0 failed
