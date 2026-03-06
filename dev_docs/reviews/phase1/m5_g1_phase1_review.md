# M5 Gate 1 Review: Phase 1 Health Endpoints + Doctor CLI

> Gate: m5-g1 | Target commit: 5836d8e | Reviewer: tester | Date: 2026-03-04

## 结论: PASS_WITH_RISK

Phase 1 整体实现质量高，健康接口分层和 Doctor CLI 架构正确，只读保证严格执行。
发现 1 个 P1 统计口径不一致问题（D2/DD3 对 MEMORY.md 的计数逻辑），需在后续修复。

---

## Findings

### P1-01: D2/DD3 对 MEMORY.md 的统计口径与 MemoryIndexer 不一致

**位置**: `src/infra/doctor.py:211-216` (D2), `src/infra/doctor.py:487-494` (DD3)

**问题**: D2 `_check_memory_index_health()` 和 DD3 `_check_memory_reindex_dryrun()` 对 MEMORY.md 使用 `re.split(r"^---$", content, flags=re.MULTILINE)` 按 `---` 分隔符统计 section 数量。但 `MemoryIndexer.index_curated_memory()` 使用 `_split_by_headers()` 按 `## ` markdown headers 分隔，每个 `##` section 成为一个 `memory_entries` 行。

**影响**: 对包含 `## ` headers 但不含 `---` 分隔符的 MEMORY.md：
- D2 计数 = 1（整个文件为 1 section，无 `---` 分隔符）
- DB 实际 entries = N（N 个 `##` headers 各生成 1 行）
- D2 会误报 WARN "Index mismatch: DB has N entries, files have 1 entries"
- DD3 per-file 对比同理误报

**修复建议**: D2/DD3 对 MEMORY.md 应复用 `MemoryIndexer._split_by_headers()` 逻辑统计 section 数，而非 `---` 分隔符。可提取公共 helper 或直接引用 indexer 的静态方法。

**测试盲区**: 现有 D2 测试 `TestCheckMemoryIndexHealth` 未构造含 MEMORY.md 的场景（仅测试 memory/ 目录下的 daily notes），未能发现此不一致。

### P2-01: D1 缺少 status chain 异常检查

**位置**: `src/infra/doctor.py:99-181`

**问题**: 计划 §1.3 D1 描述包含"检查状态链（proposed/active/rolled_back/vetoed）是否异常"，但实现只检查：
1. active 版本数量（0/1/多个）
2. DB 内容与文件内容对比

未检查 `soul_versions` 表中是否存在异常状态链（如多个 proposed 无 active、active 被意外标记为 rolled_back 等）。

**影响**: 低。核心一致性检查（DB vs 文件）已实现，状态链异常在实际运行中极为罕见。

**修复建议**: 可在后续迭代中补充，作为 D1 的增强项。

### P3-01: D1 evidence 暴露完整文件路径

**位置**: `src/infra/doctor.py:144`

**问题**: 当 SOUL.md 不存在时，evidence 为 `f"SOUL.md not found at {soul_path}"`，包含完整文件系统路径。

**影响**: 极低。Doctor 输出仅在 CLI stdout，不进入公开日志或网络传输。但与架构文档 §8.2 脱敏原则存在微小紧张。

**修复建议**: 可简化为 `"SOUL.md not found in workspace"`，不暴露绝对路径。

---

## 代码审查详情

### 1. 健康接口 (`src/gateway/app.py:255-282`)

| 检查项 | 结果 | 备注 |
|--------|------|------|
| `/health` 保持原样 | PASS | `{"status": "ok"}`，无改动 |
| `/health/live` 极简 | PASS | `{"status": "alive"}`，仅表示进程存活 |
| `/health/ready` 综合 preflight | PASS | 从 app.state 读取 PreflightReport，包含 checks 详情 |
| 不暴露 secret | PASS | 仅输出 check status + evidence（脱敏） |

### 2. Doctor 核心 (`src/infra/doctor.py`)

| 检查项 | 结果 | 备注 |
|--------|------|------|
| 复用 preflight C2-C10 | PASS | 直接 import 8 个 check 函数 |
| 不含 C11 reconcile | PASS | `_check_soul_reconcile` 未被 import，严格只读 |
| D1 SOUL 只读 | PASS | 只 SELECT + 文件读取 + diff，不调 reconcile |
| D2 memory index | FAIL | 对 MEMORY.md 使用 `---` 分隔，与 indexer `##` headers 不一致（P1-01） |
| D3 budget status | PASS | 正确对比阈值 WARN/STOP，边界处理完整 |
| D4 session activity | PASS | 检测 10 分钟以上挂起 session，ID 截断显示 |
| DD1 provider connectivity | PASS | timeout 15s，不暴露 API key |
| DD2 Telegram deep | PASS | 复用 Bot.get_me()，finally close session |
| DD3 memory reindex dryrun | FAIL | 同 P1-01，MEMORY.md 统计口径不一致 |
| 只读保证 | PASS | 所有 DB 操作为 SELECT，有专门测试验证 |
| 输出脱敏 | PASS | 不含 API key、token、password、DSN |

### 3. 数据模型 (`src/infra/health.py`)

| 检查项 | 结果 | 备注 |
|--------|------|------|
| DoctorReport 新增 | PASS | `checks` + `deep` + `passed` + `summary()` |
| PreflightReport 不变 | PASS | 与 Phase 0 一致 |
| CheckResult frozen | PASS | `@dataclass(frozen=True)` |
| summary() 格式正确 | PASS | 包含 mode=standard/deep 标识 |

### 4. CLI 入口 (`src/backend/cli.py`)

| 检查项 | 结果 | 备注 |
|--------|------|------|
| argparse（不引入新依赖） | PASS | 极简实现 |
| `doctor [--deep]` 参数 | PASS | 正确传递 deep 标志 |
| 退出码 0/1 | PASS | passed → 0, failed → 1 |
| structlog 日志 | PASS | `doctor_cli_done` 事件 |
| engine.dispose() | PASS | finally 块确保资源释放 |

### 5. justfile 集成

| 检查项 | 结果 | 备注 |
|--------|------|------|
| `just doctor` | PASS | `uv run python -m src.backend.cli doctor` |
| `just doctor-deep` | PASS | `uv run python -m src.backend.cli doctor --deep` |

### 6. 编码规范

| 检查项 | 结果 | 备注 |
|--------|------|------|
| `from __future__ import annotations` | PASS | 所有新文件 |
| structlog | PASS | 不用 print/logging |
| type hints | PASS | 完整标注 |
| async I/O | PASS | 所有 DB 操作 async |
| 异常不吞没 | PASS | 每个 check 的 except 块返回 WARN/FAIL + evidence |
| ruff clean | PASS | `uv run ruff check src/` 无错误 |

---

## 测试验证

### 测试运行结果

```
Phase 1 相关测试: 76 passed (test_doctor.py + test_health_models.py + test_preflight.py)
全量回归 (unit): 714 passed, 64 deselected (integration), 0 failed
ruff lint: All checks passed
```

### 测试覆盖评估

| 模块 | 测试覆盖 | 评估 |
|------|----------|------|
| D1 soul_consistency | 6 cases | 充分：no active, multiple active, file not found, consistent, drift, exception |
| D2 memory_index_health | 4 cases | 部分：缺少 MEMORY.md 场景（与 P1-01 相关） |
| D3 budget_status | 5 cases | 充分：OK, WARN, FAIL(stop), no row, exception |
| D4 session_activity | 3 cases | 充分：no hung, hung found, exception |
| DD1 provider_connectivity | 5 cases | 充分：OK, unknown provider, empty key, timeout, API error |
| DD2 telegram_deep | 2 cases | 充分 |
| DD3 memory_reindex_dryrun | 3 cases | 部分：缺少 MEMORY.md 场景 |
| run_doctor composite | 4 cases | 充分：standard, deep, DB fail skip, read-only guarantee |
| DoctorReport model | 3 cases | 充分 |
| health endpoints | — | 无独立端点测试（通过 preflight report mock 间接覆盖） |
| CLI | — | 无 CLI smoke test（`--help`） |

---

## 计划对齐

| 计划项 | 实现 | 对齐 |
|--------|------|------|
| 1.1 保留旧端点 + 新增 liveness | /health 不动 + /health/live | PASS |
| 1.2 readiness 端点 | /health/ready 读 preflight report | PASS |
| 1.3 doctor 检查基础设施 | run_doctor() + D1-D4 + DD1-DD3 | PASS (P1-01 除外) |
| 1.3 D1 只读保证 | SELECT + file read + diff, 不调 reconcile | PASS |
| 1.3 D2 统计口径一致 | 与 indexer 不一致 | FAIL (P1-01) |
| 1.3 D3/D4 | 正确实现 | PASS |
| 1.3 DD1-DD3 deep opt-in | --deep 标志控制 | PASS |
| 1.3 输出脱敏 | 不含 secret | PASS |
| 1.4 CLI 入口 | argparse, doctor [--deep] | PASS |
| 1.4 justfile 集成 | doctor / doctor-deep | PASS |

### 架构文档对齐 (`design_docs/phase1/m5_architecture.md`)

| 文档节 | 要求 | 对齐 |
|--------|------|------|
| §6.2 默认只读 | doctor 不写 DB/文件 | PASS |
| §6.3 liveness/readiness/doctor 分层 | 三层实现 | PASS |
| §8.2 输出脱敏 | 不回显 key/token/password/DSN | PASS |
| §8.3 默认不改文件 | doctor 不执行 reconcile/reindex | PASS |
| §8.4 外部探测带 timeout | DD1 15s, DD2/DD3 依赖 DB timeout | PASS |

---

## 风险评估

| 风险 | 级别 | 缓解 |
|------|------|------|
| P1-01 D2/DD3 MEMORY.md 误报 | 中 | 不影响服务运行，仅 doctor 诊断结果有误；修复简单 |
| 无 CLI smoke test | 低 | CLI 逻辑简单，可在 Phase 2 补充 |
| 无健康端点独立测试 | 低 | 端点逻辑极简，间接覆盖足够 |
