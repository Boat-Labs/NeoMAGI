# M5 实现计划：运营可靠性

> 状态：approved
> 日期：2026-03-04
> 依据：`design_docs/roadmap_milestones_v3.md`（M5）、`design_docs/m5_architecture.md`

## Context

NeoMAGI 已完成 M0~M4 + M6 + M7 全部里程碑（686 tests，ruff clean）。当前启动阶段已具备若干分散的 fail-fast 检查（workspace_path 一致性、DB 连接、provider 注册、Telegram check_ready、SOUL.md 对账），但缺少统一的启动自检视图、运行期诊断入口、健康接口分层和数据恢复路径。

**输入基线**：
- `/health` 仅返回静态 `{"status": "ok"}`，不反映实际依赖健康状态
- 启动检查分散在 `src/gateway/app.py` lifespan 中，无结构化证据输出
- 无运行期可重复执行的诊断命令
- 无区分 liveness/readiness 的健康探针
- 无面向维护者的备份/恢复标准路径
- `memory_entries` 可由 workspace 文件重建（`MemoryIndexer.reindex_all()`），但无 CLI 入口
- `SOUL.md` reconcile 已实现（`EvolutionEngine.reconcile_soul_projection()`），但仅在启动时执行

**M5 不做**：
- 不建设 Prometheus/Grafana/Sentry 等重型平台
- 不把 doctor 暴露为聊天工具
- 不实现通用 repair 调度器或"一键自动修复"
- 不混入模型质量/检索效果优化

### rev2 变更摘要（审阅修正）

| Finding | 级别 | 修正 |
|---------|------|------|
| F1: doctor 复用 C11 reconcile 违反只读边界 | P1 | doctor D1 改为只读对比（读 DB + 读文件 + diff），不调用 reconcile；C11 仅在 preflight 执行 |
| F2: pg_dump --schema 会连 memory_entries 一起备份 | P1 | 改用 `--table` 显式指定 5 张真源表，排除 memory_entries |
| F3: restore 未完成派生数据重建，Use Case D 未闭环 | P1 | restore 脚本默认执行完整恢复序列：真源恢复 → reconcile → reindex → doctor 验证 |
| F4: budget tables 降级 WARN 会让首个请求崩溃 | P1 | C8 从 WARN 提升为 FAIL |
| F5: C1 配置校验在 preflight 之前已被 pydantic 拦截 | P2 | 移除 C1；在 lifespan 中为 `get_settings()` 增加 ValidationError 结构化日志包装 |

### rev3 变更摘要（审阅修正）

| Finding | 级别 | 修正 |
|---------|------|------|
| F6: restore CLI/runbook/justfile 参数传递不对齐 | — | justfile 使用 `*ARGS` 透传参数；runbook 示例与 CLI 签名对齐 |
| F7: /health redirect 破坏现有 `{"status":"ok"}` 契约 | — | 保留 `GET /health` 原样不动，新增 `/health/live` 和 `/health/ready` 作为并行端点 |
| F8: restore 缺少与启动路径对齐的 preflight 验证 | — | restore 末尾增加 step 6：调用 `run_preflight()` 验证恢复后状态可启动 |

### rev4 变更摘要（审阅修正）

| Finding | 级别 | 修正 |
|---------|------|------|
| F9: fresh DB 下 memory_entries 表/trigger 不存在，reindex 会崩 | — | restore step 2 之后插入 `ensure_schema()`，保证所有表和 trigger 存在 |
| F10: reindex_all() 不清旧索引，残留孤儿条目 | — | reindex 前显式 `TRUNCATE memory_entries`，从干净状态全量重建 |

### rev5 变更摘要（审阅修正）

| Finding | 级别 | 修正 |
|---------|------|------|
| F11: runbook/验收仍引用旧的"6 步"表述 | P2 | 全文统一为 8 步恢复流程 |
| F12: 独立 reindex 入口不清孤儿条目，无法真正修复不一致 | P1 | reindex CLI 改为"TRUNCATE + reindex_all()"语义，名副其实的全量重建 |

---

## Phase 0：Preflight 统一框架

**目标**：将分散的启动检查提取为统一的 preflight 模块，输出结构化检查结果，替代当前 lifespan 中的散装逻辑。

### 数据模型

**0.1 CheckResult 数据模型** — `src/infra/health.py`（新建）
- `CheckStatus` enum: `OK`, `WARN`, `FAIL`
- `CheckResult` dataclass:
  - `name: str` — 检查项名称（如 `"db_connection"`, `"active_provider"`）
  - `status: CheckStatus`
  - `evidence: str` — 诊断证据（脱敏，不含 secret）
  - `impact: str` — 失败影响描述
  - `next_action: str` — 建议修复动作
- `PreflightReport` dataclass:
  - `checks: list[CheckResult]`
  - `passed: bool` — 任一 FAIL 项则为 False
  - `summary() -> str` — 格式化输出

### Preflight 检查项

**0.2 preflight runner** — `src/infra/preflight.py`（新建）
- `async run_preflight(settings, db_engine) -> PreflightReport`
- 检查项按架构文档 §6.1 实现，每项产出 `CheckResult`：

| # | 检查项 | 分级 | 来源 |
|---|--------|------|------|
| C2 | active provider 配置闭合 | FAIL | 提取自 lifespan:179-186 |
| C3 | workspace_dir / memory.workspace_path 一致 | FAIL | 提取自 lifespan:98-105 |
| C4 | workspace 必要目录存在且可写 | FAIL | 新增：`workspace/memory/` 可访问 |
| C5 | DB 可连接 | FAIL | 提取自 lifespan:107-111 |
| C6 | schema 正确 + 必要表存在 | FAIL | 新增：introspect `information_schema.tables` |
| C7 | search trigger 存在 | WARN | 新增：检查 `memory_entries_search_vector_update` trigger |
| C8 | budget tables 存在 | FAIL | 新增：检查 `budget_state` + `budget_reservations` |
| C9 | soul_versions 表可读 | FAIL | 新增：`SELECT 1 FROM neomagi.soul_versions LIMIT 1` |
| C10 | Telegram connector（启用时）认证通过 | FAIL | 提取自 lifespan:204-225 |
| C11 | SOUL.md projection reconcile | WARN | 调用 `reconcile_soul_projection()`，漂移为 WARN 非 FAIL |

> **C1 已移除**：配置完整性校验由 pydantic-settings 在 `get_settings()` 时完成，早于 preflight 执行。最常见的配置缺失（如 API key 未设置）在 settings 构造阶段即 ValidationError 抛出。见 0.3 中的结构化日志包装。

> **C8 为 FAIL 而非 WARN**：`BudgetGate.try_reserve()` 在每个请求的 `dispatch_chat()` 中被调用（`src/gateway/dispatch.py:96`），直接读写 `budget_state` + `budget_reservations`。表不存在时首个请求必崩，不属于可降级场景。

> **C11 中 reconcile 写文件是否违反 preflight 只读**：不违反。Preflight 是启动阶段的阻断式检查，其职责包含"启动对账"（见 `m5_architecture.md` §6.1 最后一项）。写入权限限定在启动上下文中，服务尚未接流量。Doctor 才受"默认只读"约束。

**0.3 lifespan 重构** — `src/gateway/app.py`（修改）
- 在 `get_settings()` 调用处增加 `try/except ValidationError` 包装：捕获后输出结构化错误日志（`structlog` 记录缺失字段、无效值、env var 名称），然后 re-raise。确保最常见的配置错误也有结构化证据输出，即使不经过 preflight。
- lifespan 函数内的散装检查替换为 `run_preflight()` 调用
- `ensure_schema()` 保持在 preflight 之前执行（schema 初始化不属于检查逻辑）
- preflight 结果存入 app state，供 readiness 端点读取
- 任一 FAIL → `RuntimeError` 阻断启动（保持现有 fail-fast 语义）
- WARN 项记录结构化日志但不阻断

### 测试策略

- 每个 check 项一个 unit test（mock 依赖，验证 OK/WARN/FAIL 三态）
- `run_preflight` 组合测试：全 OK、混合 WARN、含 FAIL 阻断
- lifespan 集成测试：验证 preflight 失败时服务不进入 ready
- ValidationError 包装测试：验证缺失配置时结构化日志包含字段名

---

## Phase 1：健康接口分层 + Doctor CLI

**目标**：拆分 `/health` 为 liveness/readiness；新增本地 CLI `doctor` 诊断命令。

### 健康接口

**1.1 保留旧端点 + 新增 liveness** — `src/gateway/app.py`（修改）
- `GET /health` 保持原样：`{"status": "ok"}`，不改动（保护现有契约）
- `GET /health/live` → `{"status": "alive"}`（新增，极简，仅表示进程存活）

> **为什么不做 redirect**：现有 `GET /health` 返回 `{"status": "ok"}`。Redirect 会改变 HTTP 状态码（200 → 3xx）和响应体（`"ok"` → `"alive"`），任何依赖 `response.json()["status"] == "ok"` 的客户端（包括 CI health check）都会断裂。保留原端点不动，新端点并行提供。

**1.2 readiness 端点** — `src/gateway/app.py`（修改）
- `GET /health/ready` → `{"status": "ready"|"not_ready", "checks": {...}}`
- 从 app state 读取 preflight report
- 必须综合 DB、provider、connector 状态
- 不暴露 secret（证据字段脱敏）
- 仅在 preflight 全部通过后返回 ready

### Doctor CLI

**1.3 doctor 检查基础设施** — `src/infra/doctor.py`（新建）
- `async run_doctor(settings, db_engine, deep=False) -> DoctorReport`
- 复用 `CheckResult` 数据模型
- **常规检查**（默认 `doctor`）：
  - 复用 preflight C2-C10 检查项（不含 C11 reconcile，doctor 只读不写）
  - D1: SOUL 一致性详查（**只读**）— 读取 DB 中 active `soul_versions` 记录 + 读取 `workspace/SOUL.md` 文件 → 内容对比 diff；检查 active 版本唯一性；检查状态链（proposed/active/rolled_back/vetoed）是否异常。发现漂移输出 WARN + evidence（具体 diff 摘要），不执行 reconcile。修复需手动执行 `just reconcile`。
  - D2: memory index 健康 — `memory_entries` 表行数 vs workspace 文件中实际条目数（按 `---` 分隔符拆分的 section 数，非文件数）对比（只读查询）。统计口径与 `MemoryIndexer.index_daily_note()` 的拆分逻辑一致，避免因单文件多条目导致误报。
  - D3: budget 累计状态 — 当前 `cumulative_eur` vs `BUDGET_WARN_EUR` / `BUDGET_STOP_EUR` 阈值距离
  - D4: 最近会话活跃度 — 最近 session 的 `processing_since` 是否异常挂起
- **深度检查**（`doctor --deep`，显式 opt-in）：
  - DD1: provider 连通性 — 用最小请求测试 active provider API 可达（带 timeout）
  - DD2: Telegram connector 就绪 — `check_ready()` 重新执行（仅 Telegram 启用时）
  - DD3: memory reindex dry-run — 扫描 workspace 文件并与 `memory_entries` 逐条对比（只读，不写入）
- 每个检查项输出 `status / evidence / impact / next_action`
- 所有输出脱敏：不含 API key、token、password、完整 DSN

> **Doctor 只读保证**：doctor 不复用 C11（reconcile 会写 SOUL.md），改为 D1 只读对比。所有 D1-D4 和 DD1-DD3 均为纯读操作，满足 `m5_architecture.md` §6.2 "只报告，不修复"和 §8.3 "默认不改文件"的约束。发现问题后由独立命令（`just reconcile` / `just reindex`）显式修复。

**1.4 doctor CLI 入口** — `src/backend/cli.py`（新建）
- 基于 `click` 或直接 `argparse`（极简选择，不引入新依赖）
- `python -m src.backend.cli doctor [--deep]`
- 输出格式化表格到 stdout
- 记录审计日志（谁、何时执行了 doctor）
- justfile 集成：`just doctor` / `just doctor-deep`

### 测试策略

- liveness/readiness 端点 unit test（mock preflight report）
- doctor 每个检查项 unit test（验证只读：mock DB session，断言无 INSERT/UPDATE/DELETE 调用）
- D1 SOUL 一致性测试：DB 与文件一致 → OK；DB 与文件不一致 → WARN + diff evidence；文件不存在 → WARN
- doctor deep 模式 mock 测试（不真实调用外部 API）
- CLI 入口 smoke test（`--help` 正常输出）

---

## Phase 2：备份与恢复闭环

**目标**：为关键真源数据提供最小备份/恢复路径；确保派生数据可重建；恢复脚本默认完成完整恢复序列。

### 资产分层确认

按 `m5_architecture.md` §5 定义（此处不做改动，仅确认实现边界）：

| 分类 | 资产 | 保护策略 |
|------|------|----------|
| 真源 | PostgreSQL: sessions, messages, soul_versions, budget_state, budget_reservations | pg_dump 逻辑备份（按表指定） |
| 真源 | workspace/memory/*.md, workspace/MEMORY.md | 文件级备份（tar/cp） |
| 派生 | workspace/SOUL.md | reconcile 重建 |
| 派生 | memory_entries | reindex 重建 |

### 备份

**2.1 backup 脚本** — `scripts/backup.py`（新建）
- `python scripts/backup.py [--output-dir ./backups]`
- 步骤：
  1. 检查 `pg_dump` 工具可用性（不可用则 fail-fast + 提示安装）
  2. 按表备份真源数据（显式排除派生表 `memory_entries`）：
     ```
     pg_dump --table=neomagi.sessions \
             --table=neomagi.messages \
             --table=neomagi.soul_versions \
             --table=neomagi.budget_state \
             --table=neomagi.budget_reservations \
             --format=custom \
             -f {output_dir}/neomagi_{timestamp}.dump
     ```
  3. `tar czf {output_dir}/workspace_memory_{timestamp}.tar.gz workspace/memory/ workspace/MEMORY.md`
  4. 输出 manifest（备份文件清单 + 校验和）
- 读 `.env` 获取 DB 连接信息
- justfile 集成：`just backup`

> **为什么不用 `--schema=neomagi`**：该 schema 包含 `memory_entries`（搜索索引，派生数据），备份它违反资产分层定义。恢复后 `memory_entries` 为空表，由 reindex 从 workspace 文件重建——这正是 `m5_architecture.md` §5.3 的恢复原则。

### 恢复

**2.2 restore 脚本** — `scripts/restore.py`（新建）
- `python scripts/restore.py --db-dump <path> --workspace-archive <path>`
- 恢复顺序（严格按 `m5_architecture.md` §6.4，8 步全部由脚本完成）：
  1. 检查 `pg_restore` 工具可用性（不可用则 fail-fast + 提示安装）
  2. `pg_restore` 恢复 DB 真源（`--clean` 先删后建，仅覆盖备份中包含的表）
  3. `ensure_schema()` — 保证所有表（含 `memory_entries`）和 trigger 存在。backup 按表导出排除了 `memory_entries`，`pg_restore` 不会创建它；fresh DB 或 `--clean` 场景下该表和 tsvector trigger 可能不存在。`ensure_schema()` 使用 `CREATE TABLE IF NOT EXISTS` + `CREATE TRIGGER IF NOT EXISTS`，对已存在对象幂等无害。
  4. 解压 workspace memory 文件到 workspace 目录
  5. 执行 `reconcile_soul_projection()` 重建 `SOUL.md`（DB 为真源）
  6. `TRUNCATE neomagi.memory_entries` — 清除可能残留的旧索引条目。`reindex_all()` 按文件做 DELETE-REINSERT（per-file 幂等），但不会清除 workspace 中已不存在的文件对应的孤儿条目。先 TRUNCATE 确保从干净状态全量重建。
  7. 执行 `MemoryIndexer.reindex_all()` 重建 `memory_entries`（workspace 文件为真源）
  8. 执行 `run_preflight()` 验证恢复后状态（复用 Phase 0 preflight runner，与服务启动路径同一套检查）
- 输出恢复摘要：每步结果（成功/失败）+ reconcile 是否有变更 + reindex 条目数 + preflight report
- step 8 preflight 全部 OK/WARN → 恢复成功，服务可安全启动
- step 8 preflight 含 FAIL → 输出失败项 + 建议修复动作，不启动服务

> **为什么 step 3 需要 `ensure_schema()`**：backup 显式按表导出真源（排除 `memory_entries`），所以 `pg_restore` 只恢复 5 张真源表。在 fresh DB 或 schema 被 `--clean` 清除的场景下，`memory_entries` 表和 `memory_entries_search_vector_update` trigger 不会被创建。Step 7 的 `reindex_all()` 需要向 `memory_entries` INSERT，表不存在即崩溃。`ensure_schema()` 保证所有表和 trigger 就位，且对已存在对象幂等。

> **为什么 step 6 需要 TRUNCATE**：`reindex_all()` 内部对每个 workspace 文件执行 `DELETE WHERE source_path = :path` + INSERT（per-file 幂等）。但它不感知「workspace 中已不存在的文件」——如果 `memory_entries` 中有旧文件的条目（来自 restore 前的残留数据），这些孤儿条目不会被清除。先 TRUNCATE 再 reindex 确保索引与当前 workspace 文件精确一致。

> **为什么 restore 末尾跑 preflight**：恢复真源和派生数据后，仍需验证整体状态满足启动条件（DB 可连接、schema 完整、provider 配置闭合、workspace 路径一致等）。复用 `run_preflight()` 确保 restore 的验证口径与服务启动路径完全一致，而不是依赖维护者手动启动服务才能发现问题。

> **为什么 restore 可以调用 reconcile、truncate 和 reindex**：restore 是显式的、单用途恢复命令（等同于 `m5_architecture.md` §6.2 所述的"独立、显式、单用途的恢复命令"），不受 doctor 只读约束。它的职责就是完成从真源到可用状态的完整重建，包括派生数据。

### 派生数据重建（独立入口）

**2.3 reindex CLI 入口** — `src/backend/cli.py`（扩展）
- `python -m src.backend.cli reindex [--scope main]`
- 执行流程：`TRUNCATE neomagi.memory_entries` → `MemoryIndexer.reindex_all(scope_key)`
- 输出：清除条目数、重建条目数、耗时
- justfile 集成：`just reindex`

> **为什么 reindex 默认先 TRUNCATE**：`reindex_all()` 内部按文件做 DELETE-REINSERT（per-file 幂等），但不会清除 workspace 中已不存在的文件对应的孤儿条目。作为"全量重建索引"的命令，先 TRUNCATE 确保结果与当前 workspace 文件精确一致，避免维护者执行后仍残留脏数据。restore 流程中的 TRUNCATE + reindex 与此语义一致。

**2.4 reconcile CLI 入口** — `src/backend/cli.py`（扩展）
- `python -m src.backend.cli reconcile`
- 调用 `EvolutionEngine.reconcile_soul_projection()`
- 输出：是否有变更、diff 摘要
- justfile 集成：`just reconcile`

### 恢复 Runbook

**2.5 恢复 runbook** — `dev_docs/runbooks/recovery.md`（新建）
- 场景覆盖：
  - S1: DB 不可用 → `just restore --db-dump <path> --workspace-archive <path>` → 脚本自动完成 8 步恢复（含 ensure_schema、TRUNCATE、reindex、preflight 验证）
  - S2: workspace 文件丢失 → 手动恢复 workspace archive → `just reindex`（TRUNCATE + 全量重建）
  - S3: SOUL.md 与 DB 不一致 → `just doctor` 发现 D1 WARN → `just reconcile` 修复
  - S4: memory_entries 缺失或不一致 → `just doctor` 发现 D2 WARN → `just reindex` 修复（TRUNCATE + 全量重建，清除孤儿条目）
  - S5: 全量恢复 → `just restore --db-dump <path> --workspace-archive <path>`（完整 8 步序列，含 preflight 验证）→ 可选 `just doctor --deep` 补充深度检查
- 每个场景包含：症状、影响、恢复步骤、验证方法

### 测试策略

- backup 脚本 unit test：mock subprocess 调用，验证 `--table` 参数精确列出 5 张真源表（不含 memory_entries）、manifest 输出
- restore 脚本 unit test：mock subprocess + mock ensure_schema/reconcile/reindex/preflight，验证 8 步恢复顺序正确执行、ensure_schema 在 pg_restore 后 reindex 前调用、TRUNCATE 在 reindex 前执行、preflight FAIL 时输出失败项
- reindex CLI test：mock DB session + mock MemoryIndexer，验证 TRUNCATE 在 reindex_all() 之前执行、输出包含清除和重建条目数
- reconcile CLI test：mock EvolutionEngine，验证调用和输出
- 集成测试（如条件允许）：backup → 清空 → restore → doctor 验证全链路

---

## 验收对照

| Use Case | 覆盖 Phase | 验证方式 |
|----------|-----------|---------|
| A: 配置/依赖/契约不满足 → 启动阻断 + 定位信息 | Phase 0 | preflight FAIL 测试 + ValidationError 结构化日志测试 |
| B: 运行中统一诊断入口 + 不暴露 secret | Phase 1 | doctor 输出脱敏测试 + 只读保证测试 |
| C: 故障后按固定路径恢复核心服务 | Phase 2 | restore 8 步序列测试（含 ensure_schema、TRUNCATE、reindex、preflight 验证） |
| D: 真源恢复后重建派生数据 | Phase 2 | restore 自动执行 ensure_schema + TRUNCATE + reconcile + reindex + preflight 测试；独立 reindex TRUNCATE + 全量重建测试 |

---

## 依赖与风险

| # | 风险 | 缓解 |
|---|------|------|
| R1 | pg_dump/pg_restore 需要 PostgreSQL 客户端工具 | backup/restore 脚本启动时检查工具可用性 + fail-fast；runbook 说明前置依赖 |
| R2 | deep doctor 的 provider smoke check 可能消耗 API quota | 默认不启用，`--deep` 显式 opt-in；用最小 prompt（如空消息或 ping 等价请求） |
| R3 | 现有 lifespan 重构可能引入回归 | Phase 0 保持行为等价，先提取再替换；回归测试覆盖 |

---

## 实施节奏

- Phase 0 → Phase 1 → Phase 2 顺序推进
- 每个 Phase 完成后跑全量测试（`just test`）确认无回归
- 预计不需要 ADR（M5 技术选型已在架构文档中确定，无新取舍需要记录）
- 若实施中发现需要架构级取舍，再补 ADR（编号从 0046 起）

---

## justfile 新增命令汇总

```makefile
doctor:                uv run python -m src.backend.cli doctor
doctor-deep:           uv run python -m src.backend.cli doctor --deep
backup *ARGS:          uv run python scripts/backup.py {{ARGS}}
restore *ARGS:         uv run python scripts/restore.py {{ARGS}}
reindex *ARGS:         uv run python -m src.backend.cli reindex {{ARGS}}
reconcile:             uv run python -m src.backend.cli reconcile
```

> **justfile 参数透传**：`backup`、`restore`、`reindex` 使用 just 的 `*ARGS` 语法，将命令行参数原样透传给底层脚本。示例：`just restore --db-dump ./backups/neomagi_20260304.dump --workspace-archive ./backups/workspace_memory_20260304.tar.gz`。当前 justfile 下无需额外 `--` 分隔符。
