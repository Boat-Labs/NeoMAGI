---
doc_id: 019d928e-7bfa-73fb-8996-0f55dfeac496
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-15T21:11:44+02:00
---
# P2-M3 用户测试指导

> 版本：P2-M3 完成态（M3a Auth + M3b Ledger Visibility + M3c Retrieval Quality & Policy Hook）
> 日期：2026-04-15
> 目标：利用人类用户的真实交互操作，发现 P2-M3 Auth / 记忆可见性 / 检索质量中 automated tests 无法覆盖的架构缝隙。

## 0. 核心定位

**本指导不是让用户重跑 `pytest`**。P2-M3 已有 1941 单元 + 46 集成测试覆盖。

本指导的价值在于：
- 验证**真实浏览器**中的登录 → JWT → WebSocket 鉴权闭环
- 验证**真实 LLM 调用**下 principal-filtered 记忆是否正确注入 prompt 且不泄漏跨用户数据
- 验证**中文真实对话**中 Jieba 分词 + tsvector 检索是否命中用户期望的条目
- 探测 Auth / Memory / PromptBuilder / Compaction 之间的**架构缝隙**
- 确认 no-auth → auth 模式切换时**已有数据**（legacy entries）的可见性表现符合预期

## 1. 测试分层

| 层 | 方式 | 目标 |
|----|------|------|
| A | CLI 脚本 + DB 直查 | 确认 auth 模式切换、principal 绑定、memory visibility 过滤在真实 PG 下表现正确 |
| B | WebChat 浏览器交互 | 确认全链路：Login UI → JWT → WS auth → agent prompt → memory search → 结果注入 → LLM 回复 |
| C | 人工观察 + 边界探测 | 发现 automated tests 遗漏的跨模块交互缺陷 |

建议顺序：A → B → C。A 层不通过则 B/C 层无意义。

## 2. 环境准备

### 2.0 首次 vs 重新执行

重新执行时：

```bash
podman start neomagi-pg
just reset-user-db YES
rm -rf workspace && just init-workspace
just init-soul
```

### 2.1 安装依赖

```bash
uv sync --extra dev
just install-frontend
```

### 2.2 准备 `.env`

```bash
cp .env_template .env
```

至少配置：

```dotenv
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_USER=neomagi
DATABASE_PASSWORD=neomagi
DATABASE_NAME=neomagi
DATABASE_SCHEMA=neomagi

OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
```

**暂不设置** `AUTH_PASSWORD_HASH`——A 层先以 no-auth 模式运行。

### 2.3 启动 PostgreSQL 17

```bash
podman run --name neomagi-pg \
  -e POSTGRES_USER=neomagi \
  -e POSTGRES_PASSWORD=neomagi \
  -e POSTGRES_DB=neomagi \
  -p 5432:5432 \
  -d postgres:17
```

如容器已存在：`podman start neomagi-pg`

执行 migration：

```bash
uv run alembic upgrade head
```

### 2.4 初始化 workspace

```bash
just init-workspace
just init-soul
```

### 2.5 确认 M3 schema 就绪

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from src.config.settings import get_settings
from src.session.database import create_db_engine

async def main():
    settings = get_settings()
    schema = settings.database.schema_
    engine = await create_db_engine(settings.database)
    async with engine.connect() as conn:
        result = await conn.execute(text(
            f"SELECT column_name FROM information_schema.columns"
            f" WHERE table_schema = '{schema}'"
            f" AND table_name = 'memory_entries'"
            f" ORDER BY ordinal_position"
        ))
        cols = [r[0] for r in result]
        checks = {
            "principal_id": "principal_id" in cols,
            "visibility": "visibility" in cols,
            "search_text": "search_text" in cols,
        }
        for k, v in checks.items():
            print(f"  {k}: {'OK' if v else 'MISSING'}")

        result2 = await conn.execute(text(
            f"SELECT EXISTS (SELECT 1 FROM information_schema.tables"
            f" WHERE table_schema = '{schema}'"
            f" AND table_name = 'principals')"
        ))
        print(f"  principals table: {'OK' if result2.scalar() else 'MISSING'}")
    await engine.dispose()

asyncio.run(main())
PY
```

预期全部 `OK`。若有 `MISSING`，检查 migration 是否成功。

## 3. A 层：No-Auth 基线 + Auth 切换

本层在 CLI 环境中验证 auth 模式切换和 memory visibility 过滤，不经过 WebChat。

### T01 No-Auth 模式：写入 legacy 记忆

```bash
uv run python - <<'PY'
import asyncio
from pathlib import Path
from src.config.settings import get_settings
from src.memory.writer import MemoryWriter
from src.memory.indexer import MemoryIndexer
from src.memory.ledger import MemoryLedgerWriter
from src.session.database import create_db_engine, make_session_factory

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    factory = make_session_factory(engine)
    ledger = MemoryLedgerWriter(factory)
    indexer = MemoryIndexer(factory, settings.memory)
    writer = MemoryWriter(
        settings.memory.workspace_path, settings.memory,
        indexer=indexer, ledger=ledger,
    )

    # 写入 3 条 legacy（无 principal）记忆
    for text in [
        "今天学习了 PostgreSQL 的 tsvector 全文搜索机制",
        "明天计划研究 embedding 和向量检索的集成方案",
        "NeoMAGI project uses Python asyncio for all I/O operations",
    ]:
        result = await writer.append_daily_note(text, scope_key="main")
        status = "ledger+proj" if result.ledger_written else "proj-only"
        print(f"  [{status}] {text[:40]}...")

    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：3 条 entry 写入，每条 `[ledger+proj]`
- 验证：`cat workspace/memory/$(date +%Y-%m-%d).md` 应包含 3 条带 metadata 行的条目，**无 `principal:` 字段**（no-auth 模式下 principal_id=None 不渲染）

### T02 No-Auth 模式：CJK 搜索

```bash
uv run python - <<'PY'
import asyncio
from src.config.settings import get_settings
from src.memory.searcher import MemorySearcher
from src.session.database import create_db_engine, make_session_factory

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    factory = make_session_factory(engine)
    searcher = MemorySearcher(factory, settings.memory)

    queries = ["全文搜索", "向量检索", "asyncio", "PostgreSQL"]
    for q in queries:
        results = await searcher.search(q, scope_key="main", principal_id=None)
        ids = [f"{r.entry_id}" for r in results]
        print(f"  '{q}' → {len(results)} hits {ids}")

    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：
  - `全文搜索` → 1 hit（"PostgreSQL 的 tsvector 全文搜索"）
  - `向量检索` → 1 hit（"embedding 和向量检索"）
  - `asyncio` → 1 hit（"Python asyncio"）
  - `PostgreSQL` → 至少 1 hit
- 关键观察：如果 CJK 查询返回 0 hit → Jieba 分词或 search_text 填充有问题 → 运行 `uv run python -m src.backend.cli reindex` 后重试

### T03 切换到 Auth 模式

```bash
# 生成密码 hash（交互式输入密码，如 "test123"）
just hash-password
```

将输出的 `$2b$...` hash 写入 `.env`：

```dotenv
AUTH_PASSWORD_HASH=$2b$12$<hash>
AUTH_OWNER_NAME=TestOwner
GATEWAY_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
```

**不需要重启**——此 env 变更在下次 Gateway 启动时生效。

### T04 Auth 模式：Principal 创建

Owner principal 的创建只在 Gateway lifespan 中通过 `PrincipalStore.ensure_owner()` 执行，`ensure_schema()` 不负责此步。以下脚本显式调用该方法：

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from src.config.settings import get_settings
from src.auth.store import PrincipalStore
from src.session.database import create_db_engine, ensure_schema, make_session_factory

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    await ensure_schema(engine, settings.database.schema_)
    factory = make_session_factory(engine)

    # 显式创建 owner principal（与 Gateway lifespan 同一逻辑）
    if settings.auth.password_hash is None:
        print("✗ AUTH_PASSWORD_HASH 未设置 — 先完成 T03")
        await engine.dispose()
        return

    store = PrincipalStore(factory)
    await store.ensure_owner(
        name=settings.auth.owner_name,
        password_hash=settings.auth.password_hash,
    )

    schema = settings.database.schema_
    async with engine.connect() as conn:
        result = await conn.execute(text(
            f"SELECT id, name, role FROM {schema}.principals"
        ))
        rows = result.fetchall()
        print(f"Principals ({len(rows)}):")
        for r in rows:
            print(f"  id={r.id[:20]}... name={r.name} role={r.role}")
    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：1 个 principal，role=`owner`，name=`TestOwner`
- 如果已存在则不重复创建（幂等）

### T05 Auth 模式：写入 owner 记忆 + visibility 过滤

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from src.config.settings import get_settings
from src.memory.writer import MemoryWriter
from src.memory.indexer import MemoryIndexer
from src.memory.ledger import MemoryLedgerWriter
from src.memory.searcher import MemorySearcher
from src.session.database import create_db_engine, make_session_factory

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    factory = make_session_factory(engine)
    schema = settings.database.schema_

    # 获取 owner principal_id
    async with engine.connect() as conn:
        result = await conn.execute(text(
            f"SELECT id FROM {schema}.principals WHERE role = 'owner'"
        ))
        row = result.fetchone()
        if not row:
            print("✗ 无 owner principal — 先运行 T04")
            await engine.dispose()
            return
        owner_id = row.id
        print(f"Owner principal: {owner_id[:20]}...")

    ledger = MemoryLedgerWriter(factory)
    indexer = MemoryIndexer(factory, settings.memory)
    writer = MemoryWriter(
        settings.memory.workspace_path, settings.memory,
        indexer=indexer, ledger=ledger,
    )
    searcher = MemorySearcher(factory, settings.memory)

    # 写入 owner 私有记忆
    result = await writer.append_daily_note(
        "我最喜欢的编程语言是 Python，尤其是 async 生态",
        scope_key="main", principal_id=owner_id,
    )
    print(f"\nOwner 写入: ledger={result.ledger_written}")

    # owner 搜索 — 应该看到 legacy + own
    r1 = await searcher.search("编程语言", scope_key="main", principal_id=owner_id)
    print(f"\n[Owner 搜索 '编程语言'] {len(r1)} hits")
    for r in r1:
        vis = "LEGACY" if r.principal_id is None else f"pid={r.principal_id[:12]}"
        print(f"  [{vis}] {r.content[:60]}...")

    # 匿名搜索 — 应该只看到 legacy
    r2 = await searcher.search("编程语言", scope_key="main", principal_id=None)
    print(f"\n[匿名搜索 '编程语言'] {len(r2)} hits")
    for r in r2:
        vis = "LEGACY" if r.principal_id is None else f"pid={r.principal_id[:12]}"
        print(f"  [{vis}] {r.content[:60]}...")

    # 关键断言
    if len(r1) > len(r2):
        print("\n✓ Visibility 过滤正确: owner 看到更多条目")
    else:
        print("\n✗ Visibility 可能未过滤: owner 和匿名结果数相同")

    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：
  - Owner 搜索 `编程语言` → 至少 1 hit（自己写的条目），可能还包含 legacy
  - 匿名搜索 `编程语言` → 0 hit（owner 写的条目被过滤），除非 legacy 也含此关键词
  - `✓ Visibility 过滤正确`

### T06 Auth 模式：shared_in_space 写入被拒

```bash
uv run python - <<'PY'
import asyncio
from src.config.settings import get_settings
from src.memory.writer import MemoryWriter
from src.memory.indexer import MemoryIndexer
from src.memory.ledger import MemoryLedgerWriter
from src.session.database import create_db_engine, make_session_factory
from src.infra.errors import VisibilityPolicyError

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    factory = make_session_factory(engine)
    writer = MemoryWriter(
        settings.memory.workspace_path, settings.memory,
        indexer=MemoryIndexer(factory, settings.memory),
        ledger=MemoryLedgerWriter(factory),
    )

    # 尝试 shared_in_space — 应被拒
    try:
        await writer.append_daily_note("shared data", visibility="shared_in_space")
        print("✗ shared_in_space 写入未被拒")
    except VisibilityPolicyError as e:
        print(f"✓ shared_in_space 拒绝: {e}")

    # 尝试带 shared_space_id 的 metadata — 应被规则 0 拒
    ledger = MemoryLedgerWriter(factory)
    try:
        await ledger.append(
            entry_id="test-rule0", content="sneaky",
            metadata={"shared_space_id": "space-1"},
        )
        print("✗ shared_space_id metadata 未被拒")
    except VisibilityPolicyError as e:
        print(f"✓ shared_space_id 规则 0 拒绝: {e}")

    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：两个 `✓`

### T07 Doctor D6 检查

```bash
uv run python -m src.backend.cli doctor
```

- 预期：`visibility_policy` 检查为 `OK`（无 shared_in_space 条目，search_text 已填充）
- 如果显示 `WARN: search_text IS NULL` → 运行 `uv run python -m src.backend.cli reindex`

## 4. B 层：WebChat 全链路验证

本层的目标：确认真实浏览器 → Login → JWT → WebSocket → Agent → principal-filtered memory → LLM 回复。

### 4.1 配置 Vite proxy（一次性）

前端 auth store 使用同源请求（`/auth/status`、`/auth/login`），Vite dev server 需要将 `/auth` 和 `/ws` 代理到后端 `localhost:19789`。

编辑 `src/frontend/vite.config.ts`，在 `server` 块中添加 proxy：

```ts
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/auth": "http://localhost:19789",
      "/ws": { target: "ws://localhost:19789", ws: true },
    },
  },
```

**测试完成后可保留此 proxy（不影响生产构建）。**

### 4.2 启动系统（Auth 模式）

确保 `.env` 已配置 `AUTH_PASSWORD_HASH`（T03）。

终端 A：`just dev`
终端 B：`just dev-frontend`
浏览器打开 `http://localhost:5173`

### T08 Login UI + JWT 认证

1. 浏览器应显示 Login 表单（因 `AUTH_PASSWORD_HASH` 已配置）
2. 输入错误密码 → 应显示 "Invalid password."
3. 连续输入 5 次错误密码（每次都显示 "Invalid password."）→ 第 6 次应显示 "Too many attempts. Please wait."（`max_failures=5`，第 5 次记录后锁定，第 6 次命中 `is_locked`）
4. 等 5 分钟或重启后端重置限流 → 输入正确密码
5. 登录成功 → 跳转到 Chat 界面，左上角应显示 `Connected`

**关键观察点**：
- 后端日志应出现 `auth_mode_enabled`（启动时）→ WebSocket 连接后 `ws_connected`（含 `principal_id` 非空）
- 若 Login 后 Chat 界面卡在 `Connecting...` → 检查 WebSocket pre-auth 流程（前端是否发送 `method: "auth"` RPC）
- 若浏览器控制台出现 CORS 错误 → 检查 `GATEWAY_ALLOWED_ORIGINS` 是否包含 `http://localhost:5173`

### T09 Owner 记忆 prompt 注入验证

> 前置：T01 写入了 3 条 legacy 记忆，T05 写入了 1 条 owner 记忆。

在 Chat 中发送：

> 请回忆一下，我之前提到过哪些技术话题？你看到了哪些记忆条目？

**预期**：
- Agent 应提到 tsvector/全文搜索、embedding/向量检索、Python asyncio（legacy 条目）
- Agent 还应提到"最喜欢的编程语言是 Python"（owner 条目）
- 后端日志应出现 `daily_notes_loaded`
- 后端日志应出现 `memory_search_filtered`（含 `principal_id=<owner_id>`, `visibility_policy_version=v1`）

**关键观察点**：
- 如果 Agent 完全不提 legacy 条目 → PromptBuilder `_filter_entries` 对 no-principal legacy 数据的兼容有问题
- 如果 Agent 只提 legacy、不提 owner 条目 → search 的 principal_id 传播链可能断裂

### T10 CJK 检索质量（真实对话）

在 Chat 中先存储一些中文信息：

> 帮我记住：下周三和李明开会讨论数据库迁移方案

等 Agent 确认记住后（应调用 `memory_append`），再问：

> 我和谁要开会？开会讨论什么？

**预期**：
- Agent 应搜索到刚才的条目并正确回答"李明"和"数据库迁移"
- 后端日志应出现 `memory_search_filtered`

**追加测试**：

> 搜索"数据库"相关的记忆

- 应返回"数据库迁移方案"条目（T10 刚写入的）
- T01 写入的"PostgreSQL tsvector"条目**不一定命中**（lexical search 不做 PostgreSQL→数据库 语义扩展；这是 synonym gap，属于已知限制）

**关键观察点**：
- 如果中文查询 0 结果 → 检查 Jieba warmup 是否在 gateway lifespan 中执行（后端启动日志应有 `Building prefix dict`）
- 如果英文能搜到但中文搜不到 → search_text 列可能未填充 → 运行 `reindex`

### T11 匿名不泄漏 owner 记忆（CLI 确定性验证 + WebChat）

> **注意**：T08/T10 的 auth 模式会 stamp session（`principal_id` 绑定到 session）。切到 no-auth 后，同一 session 会被 `authorize_and_stamp_session` 拒绝（`SESSION_AUTH_REQUIRED`）。因此必须使用**新 session**。

**步骤 1（CLI 确定性验证 — 核心断言）**：

```bash
uv run python - <<'PY'
import asyncio
from src.config.settings import get_settings
from src.memory.searcher import MemorySearcher
from src.session.database import create_db_engine, make_session_factory

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    factory = make_session_factory(engine)
    searcher = MemorySearcher(factory, settings.memory)

    # 匿名搜索（principal_id=None）
    results = await searcher.search("Python", scope_key="main", principal_id=None)
    print(f"匿名搜索 'Python': {len(results)} hits")
    for r in results:
        tag = "LEGACY" if r.principal_id is None else f"OWNER({r.principal_id[:12]})"
        print(f"  [{tag}] {r.content[:60]}...")

    has_owner_leak = any(r.principal_id is not None for r in results)
    if has_owner_leak:
        print("\n✗ 隔离失败: 匿名搜索返回了 owner 条目")
    else:
        print("\n✓ 隔离正确: 匿名搜索仅返回 legacy 条目")
    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：`✓ 隔离正确`；所有结果的 principal_id 均为 None
- 如果出现 `✗ 隔离失败` → SQL WHERE 的 principal 过滤有缝隙 → **阻塞**

**步骤 2（WebChat 辅助验证 — 可选）**：

1. 关闭 Gateway（Ctrl+C）
2. 注释掉 `.env` 中的 `AUTH_PASSWORD_HASH`
3. 重启 `just dev`
4. 打开**新的浏览器隐身窗口**（或清空 localStorage 后刷新），使用新 session
5. 后端日志应出现 `auth_mode_disabled`
6. 在 Chat 中发送 `搜索所有关于 Python 的记忆`
7. Agent 只应提到 legacy 条目（T01 写入的），不应提到 T05/T10 写入的 owner 条目

## 5. C 层：架构缝隙探测

### T12 Daily notes visibility 与 workspace 文件的一致性

目标：确认 PromptBuilder 从 workspace 文件加载的 daily notes 正确过滤掉不可见条目。

**步骤 1**：手动在今天的 daily note 文件中注入一条不可见的第二 principal 条目：

```bash
cat >> workspace/memory/$(date +%Y-%m-%d).md <<'ENTRY'
---
[23:59] (entry_id: fake-other-user, source: user, scope: main, principal: other-user-id-12345, visibility: private_to_principal)
这是另一个用户的私密笔记，当前 owner 不应看到此内容
ENTRY
```

**步骤 2（确定性脚本验证 — 核心断言）**：

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from pathlib import Path
from src.config.settings import get_settings
from src.agent.prompt_builder import PromptBuilder
from src.session.database import create_db_engine, make_session_factory

FAKE_MARKER = "另一个用户的私密笔记"

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    schema = settings.database.schema_

    # 获取 owner principal_id
    async with engine.connect() as conn:
        result = await conn.execute(text(
            f"SELECT id FROM {schema}.principals WHERE role = 'owner'"
        ))
        row = result.fetchone()
        owner_id = row.id if row else None
    await engine.dispose()

    ws = settings.memory.workspace_path
    builder = PromptBuilder(
        workspace_dir=ws, memory_settings=settings.memory, tool_registry=None,
    )

    # Owner 视角
    owner_notes = builder._load_daily_notes(
        scope_key="main", principal_id=owner_id,
    )
    if FAKE_MARKER in owner_notes:
        print("✗ Owner 视角泄漏: fake 条目出现在 daily notes 中")
    else:
        print("✓ Owner 视角: fake 条目已被过滤")

    # 匿名视角
    anon_notes = builder._load_daily_notes(
        scope_key="main", principal_id=None,
    )
    if FAKE_MARKER in anon_notes:
        print("✗ 匿名视角泄漏: fake 条目出现在 daily notes 中")
    else:
        print("✓ 匿名视角: fake 条目已被过滤")

asyncio.run(main())
PY
```

- 预期：两个 `✓`
- 如果出现 `✗` → `_filter_entries` 对跨 principal 条目过滤失效 → **阻塞**

**步骤 3（WebChat 辅助验证 — 可选）**：在 auth 模式 WebChat 中发送：

> 请逐条列出你在今天 daily notes 中看到的内容摘要

- 预期：Agent **不应**提到"另一个用户的私密笔记"内容

**清理**：测试后删除注入的 fake 条目（用编辑器删除 `---` + `[23:59]...另一个用户...` 块）。

### T13 Compaction 对 principal-tagged 记忆的影响

目标：验证会话压缩（compaction）后，Agent 仍能通过 memory search 找到 principal-filtered 条目。

**环境准备**（降低 compaction 阈值使其可触发）：

在 `.env` 中临时设置较低的 context limit（测试后恢复）：

```dotenv
COMPACTION_CONTEXT_LIMIT=4000
```

恢复 Auth 模式（取消注释 `AUTH_PASSWORD_HASH`），重启 Gateway。

**步骤**：

1. 在 auth 模式 WebChat 中进行对话，每轮发送较长文本（如让 Agent 解释技术概念），直到后端日志出现 `budget_check` 且 `status=compact_needed`，随后出现 `compaction_complete`
2. Compaction 后，发送：

> 请搜索我之前关于数据库的记忆

3. 预期：Agent 仍能搜索到 owner 条目（memory search 从 DB 查询，独立于对话历史压缩）
4. 关键观察：Compaction 不丢失 memory_entries 数据（它只压缩对话消息），但 compaction 摘要中是否保留了 principal attribution（如果 Agent 之前把搜索结果放入对话）

**测试后恢复**：删除或注释 `COMPACTION_CONTEXT_LIMIT`（恢复默认 128000）。

### T14 Reindex 后 principal + visibility 保持

目标：验证 `reindex` 从 ledger 重建 memory_entries 时保留 principal_id 和 visibility。

```bash
# 记录当前 memory_entries 状态
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from src.config.settings import get_settings
from src.session.database import create_db_engine

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    schema = settings.database.schema_
    async with engine.connect() as conn:
        result = await conn.execute(text(
            f"SELECT COUNT(*) AS total,"
            f" COUNT(principal_id) AS with_principal,"
            f" COUNT(search_text) AS with_search_text"
            f" FROM {schema}.memory_entries"
        ))
        row = result.fetchone()
        print(f"Before reindex: total={row.total}"
              f" with_principal={row.with_principal}"
              f" with_search_text={row.with_search_text}")
    await engine.dispose()

asyncio.run(main())
PY

# 执行 reindex
uv run python -m src.backend.cli reindex

# 重新查看
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from src.config.settings import get_settings
from src.session.database import create_db_engine

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    schema = settings.database.schema_
    async with engine.connect() as conn:
        result = await conn.execute(text(
            f"SELECT COUNT(*) AS total,"
            f" COUNT(principal_id) AS with_principal,"
            f" COUNT(search_text) AS with_search_text"
            f" FROM {schema}.memory_entries"
        ))
        row = result.fetchone()
        print(f"After reindex: total={row.total}"
              f" with_principal={row.with_principal}"
              f" with_search_text={row.with_search_text}")
    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：`with_principal` 在 reindex 前后一致；`with_search_text` 在 reindex 后 == `total`
- 如果 `with_principal` 减少 → ledger → memory_entries 的 principal_id 传播有问题
- 如果 `with_search_text` < `total` → CJK 分词路径缺失

### T15 Auth Rate Limiter 真实行为

> 前置：确保 `.env` 中 `AUTH_PASSWORD_HASH` 已设置（T13 恢复了 auth 模式），Gateway 正在运行且后端日志显示 `auth_mode_enabled`。若不确定，重启 `just dev` 并检查。

目标：确认 rate limiter 在真实 HTTP 交互中正确工作（in-memory 限流，不是 mock）。

```bash
# 快速发 6 次错误密码登录（后端端口 19789）
for i in $(seq 1 6); do
  CODE=$(curl -s -X POST http://localhost:19789/auth/login \
    -H "Content-Type: application/json" \
    -H "Origin: http://localhost:5173" \
    -d '{"password":"wrong"}' | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('error', {}).get('code', '?'))
except: print('parse_error')
")
  echo "  [$i] $CODE"
done
```

- 预期：前 5 次 → `AUTH_FAILED` (401)；第 6 次 → `AUTH_RATE_LIMITED` (429)
- 若无限流 → `LoginRateLimiter` 未正确接入 `/auth/login` route

### T16 Origin 检查（Auth 模式）

```bash
# 从不在白名单中的 origin 发起请求（后端端口 19789）
curl -s -X POST http://localhost:19789/auth/login \
  -H "Content-Type: application/json" \
  -H "Origin: http://evil.example.com" \
  -d '{"password":"anything"}' | python3 -m json.tool
```

- 预期：403 + `{"error": {"code": "ORIGIN_DENIED", ...}}`
- 如果返回 401（`AUTH_FAILED` 而非 `ORIGIN_DENIED`）→ origin 检查在 auth/login 路径上失效

## 6. 发现记录模板

每发现一个问题，记录以下信息：

```
### OI-M3-NN: <简述>
- 发现于：T<编号>
- 严重性：P1(阻塞) / P2(需修) / P3(观察)
- 复现步骤：...
- 实际行为：...
- 预期行为：...
- 影响范围：Auth / Memory Visibility / Retrieval / PromptBuilder / Compaction
- 根因猜测：...
```

## 7. 验收清单

| # | 验收项 | 对应测试 | 通过标准 |
|---|--------|---------|---------|
| 1 | No-auth legacy 记忆可读写 | T01, T02 | 写入成功 + CJK 搜索命中 |
| 2 | Auth 模式 owner 创建 | T04 | principals 表有且仅有 1 个 owner |
| 3 | Owner 私有记忆隔离 | T05 | owner 搜索 > 匿名搜索结果数 |
| 4 | shared_in_space 写入拒绝 | T06 | VisibilityPolicyError × 2 |
| 5 | Doctor D6 通过 | T07 | visibility_policy OK |
| 6 | Login UI + JWT 闭环 | T08 | 登录成功进入 Chat |
| 7 | LLM 使用 filtered memory 回复 | T09 | 回复引用 legacy + owner 条目 |
| 8 | CJK 真实对话检索 | T10 | 中文关键词命中 |
| 9 | No-auth 不泄漏 owner 记忆 | T11 | 匿名搜索不返回 owner 条目 |
| 10 | Reindex 保持 principal + search_text | T14 | 前后 count 一致 |
