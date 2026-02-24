# M3 Post-Review 修正计划 (rev3)

> 日期：2026-02-24
> 状态：**已审批并执行** (commit `28d54f1`)
> 触发：用户审阅 M3 交付物 + 计划 rev1/rev2 审阅反馈
> ADR 依赖：0036 (Evolution DB-SSOT + 投影对账), 0037 (workspace_path 单一真源)

## Context

M3 模块测试全绿（468 tests），但审阅发现网关装配未接线（P0）、搜索触发器部署缺口（P1）、Evolution 非原子写入（P1）、Curator 空输出防护缺失（P1）、装配测试不足（P2）、PM 报告错误（P3）。rev1 审阅发现 Evolution 补偿不闭合、多语句 execute 风险、专项测试缺失、验证命令与 just 不一致、workspace_path 漂移风险。rev2 审阅发现补偿失败日志缺口、装配测试与旧 patch 冲突、路径比较需规范化、trigger DDL 隐式 metadata 依赖。本 rev3 纳入全部反馈。

## 修改文件清单

| # | 文件 | 改动性质 |
|---|------|---------|
| 1 | `src/tools/builtins/__init__.py` | 扩展 `register_builtins` 签名，注册全部 7 个工具 |
| 2 | `src/gateway/app.py` | lifespan 构建 Memory 依赖链 + ADR 0037 路径校验 + ADR 0036 启动对账 |
| 3 | `src/session/database.py` | `ensure_schema` 显式导入 memory models + 追加 search trigger DDL（拆独立 execute） |
| 4 | `src/memory/evolution.py` | ADR 0036: 双层补偿语义 + `reconcile_soul_projection()` |
| 5 | `src/memory/curator.py` | 空 proposal 防护 |
| 6 | `tests/test_app_integration.py` | 装配测试：工具注册 + 依赖注入 + 路径校验（不 patch register_builtins） |
| 7 | `tests/test_evolution.py` | 专项：commit 失败补偿 + 补偿失败 + 对账修复 |
| 8 | `tests/test_memory_curator.py` | 专项：空输出不覆盖 MEMORY.md |
| 9 | `tests/test_ensure_schema.py`（新建） | 专项：trigger 幂等 + search_vector 填充 |
| 10 | `dev_docs/logs/m3_2026-02-24/pm.md` | 修正测试计数和 RiskLevel 描述 |

## Step 1 — P0: 网关接线

### 1a. `src/tools/builtins/__init__.py`

```python
from src.tools.builtins.memory_append import MemoryAppendTool
from src.tools.builtins.soul_propose import SoulProposeTool
from src.tools.builtins.soul_rollback import SoulRollbackTool
from src.tools.builtins.soul_status import SoulStatusTool

def register_builtins(
    registry: ToolRegistry,
    workspace_dir: Path,
    *,
    memory_writer: MemoryWriter | None = None,
    memory_searcher: MemorySearcher | None = None,
    evolution_engine: EvolutionEngine | None = None,
) -> None:
    registry.register(CurrentTimeTool())
    registry.register(MemorySearchTool(searcher=memory_searcher))
    registry.register(ReadFileTool(workspace_dir))
    if memory_writer is not None:
        registry.register(MemoryAppendTool(writer=memory_writer))
    if evolution_engine is not None:
        registry.register(SoulProposeTool(engine=evolution_engine))
        registry.register(SoulStatusTool(engine=evolution_engine))
        registry.register(SoulRollbackTool(engine=evolution_engine))
```

- `MemorySearchTool` 始终注册（已有 `searcher=None` 降级逻辑）
- 其余工具需非 None 依赖才注册
- 全部新参数 keyword-only + Optional，向后兼容

### 1b. `src/gateway/app.py` lifespan

ADR 0037 路径校验（在构建依赖前，使用 `.resolve()` 规范化避免语义同路径误判）：
```python
if settings.memory.workspace_path.resolve() != settings.workspace_dir.resolve():
    raise RuntimeError(
        f"Config mismatch: memory.workspace_path={settings.memory.workspace_path} "
        f"!= workspace_dir={settings.workspace_dir}. "
        "See ADR 0037."
    )
```

构建 Memory 依赖链：
```python
memory_indexer = MemoryIndexer(db_session_factory, settings.memory)
memory_searcher = MemorySearcher(db_session_factory, settings.memory)
memory_writer = MemoryWriter(settings.workspace_dir, settings.memory, indexer=memory_indexer)
evolution_engine = EvolutionEngine(db_session_factory, settings.workspace_dir, settings.memory)
```

ADR 0036 启动对账（在 AgentLoop 构造前）：
```python
await evolution_engine.reconcile_soul_projection()
```

传入 `register_builtins`：
```python
register_builtins(
    tool_registry, settings.workspace_dir,
    memory_writer=memory_writer,
    memory_searcher=memory_searcher,
    evolution_engine=evolution_engine,
)
```

传入 `AgentLoop`：
```python
agent_loop = AgentLoop(
    ...,
    memory_settings=settings.memory,
    memory_searcher=memory_searcher,
    evolution_engine=evolution_engine,
)
```

## Step 2 — P1: 搜索触发器（`database.py`）

在 `ensure_schema()` 的 `create_all` 前，显式导入 memory models 注册到 `Base.metadata`（消除隐式依赖）：

```python
import src.memory.models  # noqa: F401  — register memory tables in Base.metadata
```

`create_all` 后，追加**三次独立 execute**：

```python
from sqlalchemy import text

# 1. Function (idempotent)
await conn.execute(text(f"""
    CREATE OR REPLACE FUNCTION {schema}.memory_entries_search_trigger()
    RETURNS trigger AS $$
    BEGIN
        NEW.search_vector :=
            setweight(to_tsvector('simple', COALESCE(NEW.title, '')), 'A') ||
            setweight(to_tsvector('simple', COALESCE(NEW.content, '')), 'B');
        NEW.updated_at := now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""))
# 2. Drop old trigger (idempotent, 独立 execute)
await conn.execute(text(
    f"DROP TRIGGER IF EXISTS trg_memory_entries_search ON {schema}.memory_entries"
))
# 3. Create trigger (独立 execute)
await conn.execute(text(f"""
    CREATE TRIGGER trg_memory_entries_search
        BEFORE INSERT OR UPDATE ON {schema}.memory_entries
        FOR EACH ROW EXECUTE FUNCTION {schema}.memory_entries_search_trigger()
"""))
```

## Step 3 — P1: Evolution 一致性（ADR 0036）（`evolution.py`）

### 3a. apply() — 双层补偿语义

ADR 0036 要求：补偿失败必须输出结构化日志，禁止静默成功。实现双层 try/except：

```python
async def apply(self, version: int) -> None:
    async with self._db_factory() as db:
        record = await self._get_version(db, version)
        # ... 验证 status/eval ...
        # Supersede + activate
        await db.execute(update(...).values(status="superseded"))
        await db.execute(update(...).values(status="active"))

        # 写文件（commit 前）— 写失败则 session 自动 rollback
        soul_path = self._workspace_path / "SOUL.md"
        old_content = soul_path.read_text(encoding="utf-8") if soul_path.exists() else None
        soul_path.write_text(record.content, encoding="utf-8")

        try:
            await db.commit()
        except Exception:
            # 补偿：回写旧文件内容
            try:
                if old_content is not None:
                    soul_path.write_text(old_content, encoding="utf-8")
                else:
                    soul_path.unlink(missing_ok=True)
                logger.error("soul_apply_commit_failed_compensated", version=version)
            except Exception:
                logger.error(
                    "soul_apply_compensation_failed", version=version,
                    msg="DB commit failed AND file rollback failed; manual intervention required",
                )
            raise

    logger.info("soul_applied", version=version)
```

### 3b. rollback() — 同样双层补偿

逻辑同 apply()：先存旧内容，写新内容，commit 失败则双层补偿回写。

### 3c. reconcile_soul_projection() — 启动对账

新增方法，启动时由 `app.py` 调用：

```python
async def reconcile_soul_projection(self) -> None:
    """启动对账：若 DB active version 与 SOUL.md 不一致，以 DB 为准重写。"""
    current = await self.get_current_version()
    if current is None:
        return  # 无 active 版本，留给 ensure_bootstrap

    soul_path = self._workspace_path / "SOUL.md"
    file_content = soul_path.read_text(encoding="utf-8").strip() if soul_path.exists() else ""

    if file_content == current.content.strip():
        return  # 一致，无需修复

    soul_path.write_text(current.content, encoding="utf-8")
    logger.warning(
        "soul_projection_reconciled",
        version=current.version,
        msg="SOUL.md rewritten from DB active version",
    )
```

## Step 4 — P1: Curator 空输出防护（`curator.py`）

在 `curate()` 方法第 3 步后插入：

```python
# 3.5 Guard: reject empty proposals (LLM anomaly)
if not proposal.new_content.strip():
    logger.warning("curation_empty_proposal_rejected")
    return CurationResult(status="no_changes")
```

## Step 5 — P2: 测试

### 5a. `tests/test_app_integration.py` — 装配测试

新增 `test_m3_tools_registered_and_wired`：
- **不 patch `register_builtins`**（与现有用例不同），让其真实执行
- mock `create_db_engine` / `ensure_schema` / `make_session_factory`（同旧用例）
- mock `EvolutionEngine.reconcile_soul_projection` 为 async no-op（避免连真实 DB）
- 验证 7 工具注册、`MemorySearchTool._searcher` 非 None、AgentLoop 有 memory 依赖

新增 `test_workspace_path_mismatch_fails`：
- mock settings 使 `memory.workspace_path.resolve() != workspace_dir.resolve()`
- 验证 lifespan 抛 RuntimeError

### 5b. `tests/test_evolution.py` — Evolution 专项

新增 `test_apply_commit_failure_compensates_file`：
- mock `db.commit` 抛异常
- 验证 SOUL.md 恢复到旧内容

新增 `test_apply_compensation_failure_logs_and_raises`：
- mock `db.commit` 抛异常 + mock `soul_path.write_text` 也抛异常
- 验证 `soul_apply_compensation_failed` 日志被记录
- 验证原始异常仍被 re-raise

新增 `test_reconcile_soul_projection_fixes_drift`：
- 写入与 DB active 不一致的 SOUL.md
- 调用 `reconcile_soul_projection()`
- 验证文件被重写为 DB 内容

新增 `test_reconcile_no_active_skips`：
- 无 active 版本时 reconcile 不做任何操作

### 5c. `tests/test_memory_curator.py` — Curator 专项

新增 `test_curate_empty_llm_output_preserves_memory`：
- mock LLM 返回空串
- 验证 MEMORY.md 未被修改
- 验证返回 `status="no_changes"`

### 5d. `tests/test_ensure_schema.py`（新建）— 触发器专项

新增 `test_ensure_schema_creates_trigger`（integration mark）：
- 调用 `ensure_schema()` 两次（验证幂等）
- INSERT 一行 memory_entries
- 验证 `search_vector IS NOT NULL`

## Step 6 — P3: PM 报告修正（`pm.md`）

1. "测试（20个）" → "测试（22 个）"
2. "RiskLevel 三级分层" → "RiskLevel 两级分层 (low/high)"

## 验证方案

1. `just lint` — ruff 通过
2. `just test` — 全量回归通过（468+ tests，含新增专项）
3. 手工复查 `pm.md` 修正内容
