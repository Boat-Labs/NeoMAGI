---
doc_id: 019d6c29-19b9-734e-b57c-90811e4659a7
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-08T10:15:26+02:00
---
# P2-M2 用户测试指导

> 版本：P2-M2 完成态（M2a Procedure Runtime Core + M2b Multi-Agent Runtime）  
> 日期：2026-04-08  
> 目标：利用人类用户的真实交互操作，发现 P2-M2 Procedure Runtime 和多 Agent 协作中 automated tests 无法覆盖的架构缝隙。

## 0. 核心定位

**本指导不是让用户重跑 `pytest`**。P2-M2 已有完整单元测试和集成测试覆盖。

本指导的价值在于：
- 验证 Procedure 状态机在**真实 Gateway → AgentLoop → ProcedureRuntime 全链路**下的行为
- 验证多 Agent delegation/publish 在**真实 LLM 调用**下的表现
- 探测 Procedure 与现有模块（memory, skill, compaction, prompt）之间的**架构缝隙**
- 模拟类似 P2-M1 OI-01（skill proposal 被 memory 短路）的**跨模块交互缺陷**

## 1. 完成度结论

P2-M2 已完成，可进入用户验收。

完成依据：
- `P2-M2a` Procedure Runtime Core：`dev_docs/progress/project_progress.md` 中有对应 closeout 记录
- `P2-M2b` Multi-Agent Runtime：`dev_docs/logs/phase2/` 中有 PM 总结

关键事实：
- P2-M2 基础设施已完整接入 Gateway（`_build_procedure_runtime()`、`_register_procedure_tools()`）
- AgentLoop 已集成 procedure bridge（`resolve_procedure_for_request`、`rebuild_procedure_checkpoint`）
- **当前未注册任何内置 ProcedureSpec**——Runtime 已就绪但"菜单为空"
- 本测试指导提供测试用 ProcedureSpec，用于验证全链路

## 2. 适用范围

本指导覆盖：
- `ProcedureRuntime` 的状态机闭环：enter → action → transition → terminal
- CAS 乐观锁与 single-active 约束
- 多 Agent 协作：HandoffPacket → WorkerExecutor → staging → publish
- Procedure 与 prompt/memory/skill 的交互
- Worker 工具隔离与有界执行

不在本指导范围内：
- Growth governance adapter for `procedure_spec`（延期到 P2-M3+）
- Shared Companion memory / membership（不在 P2 scope）
- 并发 procedure per session（设计上禁止）
- Workflow DSL / DAG 调度

## 3. 测试分层

P2-M2 的核心产出是**不直接暴露给最终用户的运行时基础设施**（不同于 P2-M1 有 skill reuse 闭环），因此测试分为三层：

| 层 | 方式 | 目标 |
|----|------|------|
| A 层 | CLI 脚本直驱 Runtime | 确认状态机、CAS、store 持久化在真实 PG 下正确运行 |
| B 层 | WebChat + operator patch | 确认全链路（prompt 注入、virtual action schema、model 使用、state refresh）能跑起来 |
| C 层 | 人工观察 + 边界探测 | 发现 automated tests 遗漏的架构缝隙 |

建议顺序：A → B → C。A 层不通过则 B/C 层无意义。

## 4. 环境准备

### 4.0 首次 vs 重新执行

如果是**重新执行**：

```bash
podman start neomagi-pg
just reset-user-db YES
rm -rf workspace && just init-workspace
just init-soul
```

### 4.1 安装依赖

```bash
uv sync --extra dev
just install-frontend
```

### 4.2 准备 `.env`

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

### 4.3 启动 PostgreSQL 17

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

### 4.4 初始化 workspace

```bash
just init-workspace
just init-soul
```

### 4.5 确认 `active_procedures` 表存在

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
        rows = (await conn.execute(
            text(f"SELECT count(*) FROM information_schema.tables WHERE table_schema='{schema}' AND table_name='active_procedures'")
        )).scalar()
        print(f"active_procedures table exists: {rows > 0}")
    await engine.dispose()

asyncio.run(main())
PY
```

预期输出 `True`。若 `False`，检查 migration 是否成功。

## 5. A 层：Runtime 确定性验证（CLI-driven）

本层直接通过 Python 脚本操作 `ProcedureRuntime`，不经过 WebChat。
目的是在 WebChat 全链路介入前，先确认运行时核心在真实 PG 下行为正确。

### Operator 辅助脚本：创建测试 Runtime + 注册测试 Spec

以下脚本创建一个完整的 `ProcedureRuntime`，注册一个 4 状态测试 Spec：

```
planning → (delegate_work) → delegated → (publish_result) → done (terminal)
planning → (cancel) → cancelled (terminal)
```

后续 A 层所有测试都基于此脚本的输出。

```bash
uv run python - <<'PY'
import asyncio
from pydantic import BaseModel, ConfigDict

from src.config.settings import get_settings
from src.procedures.registry import (
    ProcedureContextRegistry,
    ProcedureGuardRegistry,
    ProcedureSpecRegistry,
)
from src.procedures.runtime import ProcedureRuntime
from src.procedures.store import ProcedureStore
from src.procedures.types import ActionSpec, ProcedureSpec, StateSpec
from src.session.database import create_db_engine, make_session_factory
from src.tools.base import ToolMode
from src.tools.registry import ToolRegistry


class TestCtx(BaseModel):
    model_config = ConfigDict(extra="allow")
    topic: str = ""


TEST_SPEC = ProcedureSpec(
    id="test.research",
    version=1,
    summary="P2-M2 user test: bounded research procedure",
    entry_policy="explicit",
    allowed_modes=frozenset({ToolMode.chat_safe, ToolMode.coding}),
    context_model="TestCtx",
    initial_state="planning",
    states={
        "planning": StateSpec(actions={
            "delegate_work": ActionSpec(tool="procedure_delegate", to="delegated"),
            "cancel": ActionSpec(tool="procedure_delegate", to="cancelled"),
        }),
        "delegated": StateSpec(actions={
            "publish_result": ActionSpec(tool="procedure_publish", to="done"),
        }),
        "done": StateSpec(),
        "cancelled": StateSpec(),
    },
    soft_policies=(
        "Always provide a clear task_brief when delegating.",
        "Only publish after reviewing worker output.",
    ),
)


async def build_test_runtime():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    session_factory = make_session_factory(engine)

    tool_registry = ToolRegistry()
    # Register procedure-only tools
    from src.procedures.delegation import DelegationTool
    from src.procedures.publish import PublishTool
    from src.procedures.reviewer import ReviewTool
    for tool_cls in (DelegationTool, ReviewTool, PublishTool):
        tool = tool_cls(tool_registry) if tool_cls is DelegationTool else tool_cls()
        tool_registry.register(tool)

    ctx_registry = ProcedureContextRegistry()
    guard_registry = ProcedureGuardRegistry()
    spec_registry = ProcedureSpecRegistry(tool_registry, ctx_registry, guard_registry)

    ctx_registry.register("TestCtx", TestCtx)
    spec_registry.register(TEST_SPEC)

    store = ProcedureStore(session_factory)
    runtime = ProcedureRuntime(spec_registry, ctx_registry, guard_registry, store, tool_registry)
    return runtime, engine


async def main():
    runtime, engine = await build_test_runtime()
    print("✓ Test runtime created with spec 'test.research'")
    print(f"  States: planning → delegated → done | planning → cancelled")
    print(f"  Actions: delegate_work, cancel, publish_result")

    # --- T01: Enter procedure ---
    result = await runtime.enter_procedure(
        session_id="test-session-001",
        spec_id="test.research",
        initial_context={"topic": "P2-M2 architecture review"},
    )
    if hasattr(result, "instance_id"):
        print(f"\n[T01] ✓ enter_procedure success")
        print(f"  instance_id: {result.instance_id}")
        print(f"  state: {result.state}")
        print(f"  revision: {result.revision}")
        instance_id = result.instance_id
    else:
        print(f"\n[T01] ✗ enter_procedure failed: {result}")
        await engine.dispose()
        return

    # --- T02: Single-active enforcement ---
    dup_result = await runtime.enter_procedure(
        session_id="test-session-001",
        spec_id="test.research",
    )
    if isinstance(dup_result, dict) and dup_result.get("error_code") == "PROCEDURE_CONFLICT":
        print(f"\n[T02] ✓ single-active enforcement: second enter rejected")
        print(f"  error: {dup_result.get('message', '')[:80]}")
    else:
        print(f"\n[T02] ✗ single-active NOT enforced: {dup_result}")

    # --- T03: Invalid action in current state ---
    bad_action = await runtime.apply_action(
        instance_id=instance_id,
        action_id="publish_result",  # not allowed in "planning" state
        args_json="{}",
        expected_revision=0,
    )
    if bad_action.get("error_code") == "PROCEDURE_ACTION_DENIED":
        print(f"\n[T03] ✓ invalid action rejected in 'planning' state")
    else:
        print(f"\n[T03] ✗ invalid action NOT rejected: {bad_action}")

    # --- T04: CAS conflict ---
    cas_result = await runtime.apply_action(
        instance_id=instance_id,
        action_id="cancel",
        args_json='{"task_brief": "test"}',
        expected_revision=999,  # wrong revision
    )
    if cas_result.get("error_code") == "PROCEDURE_CAS_CONFLICT":
        print(f"\n[T04] ✓ CAS conflict detected (expected=999, actual=0)")
    else:
        print(f"\n[T04] ✗ CAS conflict NOT detected: {cas_result}")

    # --- T05: Valid action → state transition ---
    # cancel: planning → cancelled (terminal)
    cancel_result = await runtime.apply_action(
        instance_id=instance_id,
        action_id="cancel",
        args_json='{"task_brief": "Cancellation test — no real delegation needed"}',
        expected_revision=0,
    )
    if cancel_result.get("ok"):
        print(f"\n[T05] ✓ cancel action → state transition")
        print(f"  from: {cancel_result.get('from_state')} → to: {cancel_result.get('to_state')}")
        print(f"  revision: {cancel_result.get('revision')}")
        print(f"  completed: {cancel_result.get('completed')}")
    else:
        print(f"\n[T05] ✗ cancel action failed: {cancel_result}")
        # 注意: cancel 使用 procedure_delegate tool，worker 可能报错
        # 这是预期中可能暴露的问题 — 见 C 层 T14

    # --- T06: Terminal state → single-active released ---
    reenter = await runtime.enter_procedure(
        session_id="test-session-001",
        spec_id="test.research",
        initial_context={"topic": "Second procedure after terminal"},
    )
    if hasattr(reenter, "instance_id"):
        print(f"\n[T06] ✓ re-enter after terminal: new instance created")
        print(f"  new instance_id: {reenter.instance_id}")
    else:
        print(f"\n[T06] ✗ re-enter after terminal failed: {reenter}")

    await engine.dispose()
    print("\n--- A 层 CLI 验证完成 ---")

asyncio.run(main())
PY
```

### 预期结果

所有 `T01`~`T06` 应显示 `✓`。

注意事项：
- `T05` 中 `cancel` 绑定了 `procedure_delegate` tool。DelegationTool 会尝试创建 WorkerExecutor 并调用 LLM。如果 cancel 语义只是取消流程而非真正委派工作，这里可能暴露出**"取消"动作不应走 delegation 路径**的设计问题——这正是 user testing 要发现的。
- 如果 `T05` 因 Worker LLM 调用失败而 `ok=False`，这**不一定是 runtime bug**，而是 test spec 设计问题。记录下来作为观察点。

### Operator 辅助脚本：查看 `active_procedures` 表

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
        rows = (await conn.execute(text(
            f"SELECT instance_id, session_id, spec_id, state, revision, completed_at FROM {schema}.active_procedures ORDER BY created_at DESC LIMIT 10"
        ))).fetchall()
        for r in rows:
            print({
                "instance_id": r.instance_id[:20],
                "session_id": r.session_id,
                "state": r.state,
                "revision": r.revision,
                "completed": r.completed_at is not None,
            })
    await engine.dispose()

asyncio.run(main())
PY
```

## 6. B 层：WebChat 全链路验证

本层的目标是确认 Procedure Runtime 在**真实 Gateway → AgentLoop → Prompt → Model → Virtual Action → Runtime → State Refresh** 全链路下能跑起来。

### 6.0 注册测试 Spec 到 Gateway（临时 patch）

当前 Gateway 以"空菜单"启动（`V1 ships with no built-in specs`）。为了让 WebChat 能看到 procedure，需要临时修改 `src/gateway/app.py`。

在 `_build_procedure_runtime()` 函数末尾 `return` 之前，添加以下代码：

```python
    # --- BEGIN P2-M2 USER TEST PATCH ---
    from pydantic import BaseModel as _BM, ConfigDict as _CD
    from src.procedures.types import ActionSpec as _AS, StateSpec as _SS

    class _TestCtx(_BM):
        model_config = _CD(extra="allow")
        topic: str = ""

    _test_spec = ProcedureSpec(
        id="test.research",
        version=1,
        summary="P2-M2 user test: delegate research to worker, then publish",
        entry_policy="explicit",
        allowed_modes=frozenset({ToolMode.chat_safe, ToolMode.coding}),
        context_model="_TestCtx",
        initial_state="planning",
        states={
            "planning": _SS(actions={
                "delegate_work": _AS(tool="procedure_delegate", to="delegated"),
            }),
            "delegated": _SS(actions={
                "publish_result": _AS(tool="procedure_publish", to="done"),
            }),
            "done": _SS(),
        },
        soft_policies=(
            "Delegate a clear research task to the worker agent.",
            "After delegation, review the staged result before publishing.",
        ),
    )

    from src.procedures.types import ProcedureSpec  # noqa: already imported above

    context_registry.register("_TestCtx", _TestCtx)
    spec_registry.register(_test_spec)
    # --- END P2-M2 USER TEST PATCH ---
```

**重要：测试完成后务必撤销此 patch。**

### 6.1 启动系统

终端 A：`just dev`  
终端 B：`just dev-frontend`  
浏览器打开 `http://localhost:5173`，确认 `Connected`。

### 6.2 进入 Procedure（operator CLI）

WebChat 当前没有 RPC 入口进入 procedure。需要 operator 手动为用户会话创建 active procedure。

**步骤 1**：获取当前 session_id

在 WebChat 中随便发一条消息（如"你好"），然后查询最近 session：

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
        rows = (await conn.execute(text(
            f"SELECT session_id, scope_key, mode FROM {schema}.sessions ORDER BY created_at DESC LIMIT 3"
        ))).fetchall()
        for r in rows:
            print({"session_id": r.session_id, "scope_key": r.scope_key, "mode": r.mode})
    await engine.dispose()

asyncio.run(main())
PY
```

记下 `session_id`。

**步骤 2**：为该 session 进入 procedure

```bash
uv run python - <<'PY'
import asyncio
from src.config.settings import get_settings
from src.procedures.store import ProcedureStore
from src.procedures.types import ActiveProcedure
from src.session.database import create_db_engine, make_session_factory
from uuid import uuid4

SESSION_ID = "<粘贴上一步得到的 session_id>"

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    session_factory = make_session_factory(engine)
    store = ProcedureStore(session_factory)

    active = ActiveProcedure(
        instance_id=f"proc_{uuid4().hex}",
        session_id=SESSION_ID,
        spec_id="test.research",
        spec_version=1,
        state="planning",
        context={"topic": "用户交互测试：探索 NeoMAGI 架构设计"},
        revision=0,
    )
    created = await store.create(active)
    print(f"✓ Procedure entered for session {SESSION_ID}")
    print(f"  instance_id: {created.instance_id}")
    print(f"  state: {created.state}")
    await engine.dispose()

asyncio.run(main())
PY
```

### T07 WebChat 中看到 ProcedureView

- 用户步骤：在**同一个 session** 中发送一条新消息：
  - `当前有什么 procedure 在运行？请描述你看到的状态。`
- 预期：
  - 如果 prompt 注入正常，model 应该能描述出当前有一个 `test.research` procedure
  - 应提到 `planning` 状态和可用的 `delegate_work` action
  - 后端日志中应能看到 `procedure_bridge` 相关日志

**关键观察点**：
- 如果 model 完全不知道有 procedure 在运行 → `_resolve_procedure_for_request()` 可能没有正确加载 → B 层阻塞
- 如果 model 知道但描述错误 → ProcedureView 注入的 prompt 格式可能有问题

### T08 用户触发 delegation

- 前置：T07 确认 model 已知 procedure 存在
- 用户步骤：
  - `请执行 delegate_work，让 worker 调研 NeoMAGI 的 memory 架构设计有哪些关键取舍，task_brief 写清楚。`
- 预期：
  - model 应调用 `delegate_work` virtual action
  - 后端日志应出现 `delegation_started` 和 `delegation_completed`
  - 后端日志应出现 `procedure_action_transitioned`（从 `planning` → `delegated`）
  - 下一轮 model 应能看到新的 state = `delegated` 和新的 allowed action = `publish_result`

**关键观察点**：
- Worker 使用的是 `gpt-4o-mini`（默认），执行的是 lightweight multi-turn executor
- Worker 没有 memory 访问、没有 session 持久化——如果 task_brief 不够清楚，worker 可能返回空结果
- 观察 `iterations_used`：如果 worker 用满了 `max_iterations=5`，说明任务可能过于复杂或 prompt 不够好
- Worker tool 隔离：worker 只能访问 `code` 和 `world` group 的工具，且排除 `is_procedure_only` 和 `RiskLevel.high`

### T09 用户触发 publish

- 前置：T08 成功完成，state 已转到 `delegated`
- 用户步骤：
  - `请查看 worker 的结果，如果合理就 publish_result。`
- 预期（理想路径）：
  - model 调用 `publish_result` virtual action，提供 `handoff_id` 和 `merge_keys`
  - 后端日志出现 `publish_executed`
  - state 转到 `done`（terminal）
  - 后端日志出现 `procedure_completed`
- 预期（可能失败路径）：
  - model 不知道 `handoff_id` 是什么 → ProcedureView 中没有把 staging 区信息暴露给 model
  - 这是**预期中可能暴露的架构缝隙**：model 需要从上一轮 delegation 的返回结果中记住 `handoff_id`，但 compaction 或 prompt 截断可能丢失这个信息

**关键观察点**：
- `handoff_id` 是 delegation 返回的 UUID，model 必须在后续轮次中引用它
- 如果会话中间发生了 compaction，`handoff_id` 可能丢失 → **这是 C 层 T12 的前置发现**
- PublishTool 需要 `context.actor == AgentRole.primary` 的 role guard — 如果 `ToolContext.actor` 未正确设置，publish 会被拒绝

### T10 Terminal 后验证

- 前置：T09 成功，procedure 已到 `done` state
- 用户步骤：
  - `当前还有 procedure 在运行吗？`
- 预期：
  - model 应报告没有 active procedure
  - 查看 `active_procedures` 表，`completed_at` 应已填充
- operator 验证：
  - 运行"查看 `active_procedures` 表"脚本
  - 最近记录应显示 `state=done`, `completed=True`

## 7. C 层：架构缝隙探测

本层是 P2-M2 用户测试的核心价值。以下每个测试用例都瞄准一个**automated tests 可能遗漏的架构交互**。

### T11 Worker 工具隔离验证

- 目标：确认 WorkerExecutor 的 triple filter（tool group + procedure-only exclusion + high-risk exclusion）在真实 ToolRegistry 下正确工作
- 方法：

```bash
uv run python - <<'PY'
import asyncio
from src.config.settings import get_settings
from src.gateway.app import _build_procedure_runtime, _register_procedure_tools
from src.procedures.roles import DEFAULT_ROLE_SPECS, AgentRole
from src.session.database import create_db_engine, make_session_factory
from src.tools.registry import ToolRegistry

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    session_factory = make_session_factory(engine)

    # 使用真实 gateway 的 tool_registry 构建流程
    from src.gateway.app import _build_memory_and_tools
    (memory_searcher, evolution_engine, tool_registry,
     skill_resolver, skill_projector, skill_learner,
     procedure_runtime) = await _build_memory_and_tools(settings, session_factory)

    # 模拟 worker 的 tool filtering
    from src.tools.base import RiskLevel, ToolMode

    worker_spec = DEFAULT_ROLE_SPECS[AgentRole.worker]
    all_tools = {}
    for group in worker_spec.allowed_tool_groups:
        for mode in ("chat_safe", "coding"):
            for tool in tool_registry.list_tools(ToolMode(mode)):
                if (
                    tool.group == group
                    and not tool.is_procedure_only
                    and tool.risk_level != RiskLevel.high
                ):
                    all_tools[tool.name] = tool

    print(f"Worker 可访问的工具数量: {len(all_tools)}")
    for name, tool in sorted(all_tools.items()):
        print(f"  {name:30s} group={tool.group} risk={tool.risk_level}")

    # 检查不应出现的工具
    forbidden = []
    for mode in ("chat_safe", "coding"):
        for tool in tool_registry.list_tools(ToolMode(mode)):
            if tool.is_procedure_only:
                if tool.name in all_tools:
                    forbidden.append(f"LEAK: procedure-only tool '{tool.name}' in worker set")
            if tool.risk_level == RiskLevel.high:
                if tool.name in all_tools:
                    forbidden.append(f"LEAK: high-risk tool '{tool.name}' in worker set")

    if forbidden:
        print(f"\n✗ 工具隔离泄漏:")
        for f in forbidden:
            print(f"  {f}")
    else:
        print(f"\n✓ 工具隔离正确: 无 procedure-only 或 high-risk 工具泄漏")

    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：无泄漏。如果出现泄漏 → 记入 open issues。

### T12 Publish 前 staging 不可见性

- 目标：确认 delegation 产生的 worker result 存放在 `_pending_handoffs` 中，在 publish 之前不会被 prompt 或 memory 路径意外暴露
- 方法（需要 B 层 T08 成功后，在 `delegated` 状态下执行）：
  - 在 T08 成功后、T09 publish 之前，检查 procedure context：

```bash
uv run python - <<'PY'
import asyncio, json
from sqlalchemy import text
from src.config.settings import get_settings
from src.session.database import create_db_engine

async def main():
    settings = get_settings()
    schema = settings.database.schema_
    engine = await create_db_engine(settings.database)
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            f"SELECT instance_id, state, context FROM {schema}.active_procedures WHERE completed_at IS NULL ORDER BY created_at DESC LIMIT 1"
        ))).fetchone()
        if row:
            ctx = json.loads(row.context) if isinstance(row.context, str) else row.context
            pending = ctx.get("_pending_handoffs", {})
            print(f"state: {row.state}")
            print(f"_pending_handoffs 条目数: {len(pending)}")
            for hid, data in pending.items():
                print(f"  handoff_id: {hid}")
                print(f"  worker_ok: {data.get('ok')}")
                print(f"  result keys: {list(data.get('result', {}).keys())}")
        else:
            print("无 active procedure")
    await engine.dispose()

asyncio.run(main())
PY
```

- 预期：
  - `_pending_handoffs` 中有 worker result，但这些数据**不应出现在 ProcedureView 的 prompt 注入中**
  - 检查 `src/procedures/types.py:build_procedure_view()` —— ProcedureView 只包含 `allowed_actions` 和 `soft_policies`，不包含 context → 正确
  - 但如果模型在之前的轮次中看到了 delegation 返回的 `handoff_id`，它**可以通过上下文记忆**知道这个值 → 这是合理的
  - **如果 worker result 的内容被意外注入到 system prompt** → 架构缝隙

### T13 Compaction 与 procedure checkpoint 交互

- 目标：验证会话压缩（compaction）不会丢失正在运行的 procedure 的关键状态
- 方法：
  1. 用 B 层流程进入 procedure 到 `delegated` 状态
  2. 在 WebChat 中大量对话（触发 compaction 阈值）
  3. Compaction 发生后，发送新消息触发 `_resolve_procedure_for_request()`
  4. 确认 model 仍然知道 procedure 在 `delegated` 状态

- 关键观察点：
  - Procedure state 持久化在 PG `active_procedures` 表，**不依赖会话历史**
  - 但 `handoff_id`（publish 所需）存在于之前的对话消息中，**compaction 可能丢失它**
  - 如果 compaction 后 model 无法 publish（因为不知道 handoff_id）→ 这是一个**设计层面的问题**：procedure context 中的 `_pending_handoffs` 的 key 应该在 ProcedureView 中暴露，或者在 purposeful compact 的 `TaskStateSnapshot` 中保留
  - 检查 `src/procedures/compact.py` 是否在 compaction 时保留了 pending handoff IDs

### T14 HandoffPacket 超限检查

- 目标：确认 32KB 和 per-field 限制在真实 payload 下正确拒绝

```bash
uv run python - <<'PY'
from src.procedures.handoff import HandoffPacket, HandoffPacketBuilder, MAX_PACKET_BYTES, MAX_TASK_BRIEF_CHARS, MAX_ITEM_CHARS
from src.procedures.roles import AgentRole
from src.procedures.types import ActiveProcedure

active = ActiveProcedure(
    instance_id="test", session_id="test", spec_id="test",
    spec_version=1, state="planning", context={"topic": "x" * 10000},
)

# Spec 不被 builder 直接使用但 type hint 要求
from src.procedures.types import ProcedureSpec, StateSpec
from src.tools.base import ToolMode
spec = ProcedureSpec(
    id="test", version=1, summary="t", entry_policy="explicit",
    allowed_modes=frozenset({ToolMode.chat_safe}),
    context_model="t", initial_state="planning",
    states={"planning": StateSpec()},
)

builder = HandoffPacketBuilder(include_keys=("topic",))

# Test 1: task_brief 超限
try:
    builder.build(
        active=active, spec=spec, target_role=AgentRole.worker,
        task_brief="x" * (MAX_TASK_BRIEF_CHARS + 1),
    )
    print("[T14-1] ✗ task_brief 超限未被拒绝")
except ValueError as e:
    print(f"[T14-1] ✓ task_brief 超限正确拒绝: {str(e)[:60]}")

# Test 2: item 超限
try:
    builder.build(
        active=active, spec=spec, target_role=AgentRole.worker,
        task_brief="test", constraints=("x" * (MAX_ITEM_CHARS + 1),),
    )
    print("[T14-2] ✗ item 超限未被拒绝")
except ValueError as e:
    print(f"[T14-2] ✓ item 超限正确拒绝: {str(e)[:60]}")

# Test 3: 总大小超限
try:
    builder2 = HandoffPacketBuilder(include_keys=("topic",))
    # context 中 topic="x"*10000，加上其他字段可能接近 32KB
    builder2.build(
        active=ActiveProcedure(
            instance_id="test", session_id="test", spec_id="test",
            spec_version=1, state="planning",
            context={"topic": "x" * 30000},
        ),
        spec=spec, target_role=AgentRole.worker,
        task_brief="research task",
    )
    print("[T14-3] ✗ 总大小超限未被拒绝（可能 context 未超 32KB）")
except ValueError as e:
    print(f"[T14-3] ✓ 总大小超限正确拒绝: {str(e)[:60]}")
PY
```

### T15 Cancel 动作与 delegation 工具的语义错配

- 目标：验证将"取消"语义的 action 绑定到 `procedure_delegate` tool 时会发生什么
- 背景：在 A 层 T05 的测试 spec 中，`cancel` action 绑定了 `procedure_delegate` tool。这意味着执行 cancel 会触发一次 worker delegation——这在语义上是错误的
- 方法：检查 A 层 T05 的结果：
  - 如果 `ok=True`：delegation 成功但产生了无用的 worker result → 语义浪费
  - 如果 `ok=False` 且 `error_code=PROCEDURE_TOOL_FAILURE`：worker 失败，state 未转换 → cancel 被 delegation 失败阻塞
  - 无论哪种：**这暴露了 ProcedureSpec 缺乏"无工具动作"（noop/direct transition）的能力**

- 结论：当前 `ActionSpec` 强制绑定 `tool`，没有 `tool=None` 的直接转换路径。如果 P2-M2 要支持"取消"或"跳过"类语义，需要增加 noop tool 或允许 `tool` 为 optional。
  记入 open issues。

### T16 Procedure + Skill 交互

- 目标：确认在 procedure active 期间，skill projection 是否继续工作
- 方法（需要 P2-M1 的 skill 已 active）：
  1. 确认有一个 active skill（通过 `just check-governance-tables`）
  2. 通过 B 层流程进入 procedure
  3. 在 procedure active 期间，发送一条命中 skill 关键词的消息
  4. 观察 model 回复是否同时反映 skill experience 和 procedure state
- 预期：两者不冲突。如果 skill projection 在 procedure active 期间被抑制或异常 → 记入 open issues。

### T17 Unknown spec_id 在 DB 中的 active procedure

- 目标：确认 Gateway 重启后，如果 DB 中有一个 active procedure 但 spec 未注册（例如 patch 被撤销），系统是否 fail-closed
- 方法：
  1. 通过 B 层流程创建一个 active procedure
  2. 撤销 gateway patch（删除测试 spec 注册代码）
  3. 重启 backend
  4. 在 WebChat 中发送消息
- 预期：
  - `resolve_procedure_for_request()` 会返回 `(active, None, {})` — spec 为 None
  - ProcedureView 不会被注入 prompt
  - 系统不崩溃，用户可以正常聊天
  - 但 active procedure 在 DB 中成为"孤儿"——没有清理机制
  - 如果系统 crash 或 500 → **P1 级架构缺陷**

## 8. 观察点汇总

完成上述测试后，汇总以下信息：

### 8.1 Runtime 核心

| 项目 | 结果 | 备注 |
|------|------|------|
| enter_procedure() | PASS/FAIL | |
| single-active enforcement | PASS/FAIL | |
| invalid action rejection | PASS/FAIL | |
| CAS conflict detection | PASS/FAIL | |
| state transition + revision bump | PASS/FAIL | |
| terminal → re-enter | PASS/FAIL | |

### 8.2 WebChat 全链路

| 项目 | 结果 | 备注 |
|------|------|------|
| ProcedureView prompt 注入 | PASS/FAIL | model 能否描述 procedure state |
| virtual action schema 可用 | PASS/FAIL | model 能否调用 delegate_work |
| delegation 完成 + state transition | PASS/FAIL | |
| publish 完成 + terminal | PASS/FAIL | |
| state refresh 后 model 看到新 state | PASS/FAIL | |

### 8.3 架构缝隙

| 探测项 | 发现 | 严重度 | 备注 |
|--------|------|--------|------|
| Worker 工具隔离 | | | T11 |
| Staging 不可见性 | | | T12 |
| Compaction 后 handoff_id 丢失 | | | T13 |
| HandoffPacket 超限拒绝 | | | T14 |
| Cancel/noop 语义错配 | | | T15 |
| Procedure + Skill 交互 | | | T16 |
| 孤儿 procedure 重启行为 | | | T17 |

## 9. 通过标准

| 层 | 标准 |
|----|------|
| A 层 | T01~T06 全部 `✓`。T05 如因 worker LLM 报错而非 runtime 错误，标记为 `PARTIAL`，不阻塞 B 层 |
| B 层 | T07~T10 全链路至少一次走通。如果 publish 因 handoff_id 丢失而失败，标记为 `KNOWN_ISSUE`，不阻塞 C 层 |
| C 层 | 所有发现按严重度分级记入 open issues。无 P0（系统 crash/数据损坏）级发现 |

总体通过条件：
- A 层全通
- B 层全链路至少一次完整走通
- 无 P0 级架构缺陷
- 所有 P1/P2 发现已记录到 `design_docs/phase2/p2_m2_open_issues.md`

## 10. 常见问题与处理

### 10.1 B 层 T07：model 完全不知道有 procedure

- 确认 gateway patch 已正确添加且无语法错误
- 确认 operator 的"进入 procedure"脚本使用了正确的 `session_id`
- 检查后端日志中 `_resolve_procedure_for_request` 是否被调用
- 如果 `spec_registry.get("test.research")` 返回 None → patch 未生效

### 10.2 B 层 T08：delegation 失败

- 检查 `OPENAI_API_KEY` 是否有效（worker 需要调用 LLM）
- 检查后端日志中 `worker_model_timeout` 或 `worker_tool_failed`
- Worker 默认使用 `gpt-4o-mini`；如果 key 只支持特定模型，可能需要调整

### 10.3 B 层 T09：model 不知道 handoff_id

- 这是**预期中最可能暴露的架构问题**
- `handoff_id` 存在于前轮的 tool result 中，model 需要从上下文中提取
- 如果上下文过长触发了 compaction → handoff_id 丢失
- Workaround：检查 `active_procedures.context._pending_handoffs` 的 key，手动告诉 model

### 10.4 C 层 T17：撤销 patch 后系统 crash

- 这是 P1 级 finding
- 预期行为是 graceful degradation（无 spec → 无 ProcedureView → 正常聊天）
- 如果 crash → 说明 `procedure_bridge.py` 的 None 处理有缺陷

### 10.5 想清空测试数据

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
        await conn.execute(text(f"DELETE FROM {schema}.active_procedures"))
        await conn.commit()
        print("✓ active_procedures 已清空")
    await engine.dispose()

asyncio.run(main())
PY
```

## 11. 执行记录

每次执行验收测试时，在 `dev_docs/logs/phase2/p2-m2_user_acceptance.md` 中记录结果。

```markdown
| 用例 | 状态 | 日期 | 备注 |
|------|------|------|------|
| T01  | PASS | 4/08 | - |
| T05  | PARTIAL | 4/08 | worker LLM 报错，非 runtime 错误 |
| T08  | PASS | 4/08 | delegation 成功，worker 用了 3 iterations |
| T13  | KNOWN_ISSUE | 4/08 | compaction 后 handoff_id 丢失 |
```

## 12. 附录：开发回归命令（不计入手工用户验收）

### 12.1 Procedure Runtime 单元测试

```bash
uv run pytest tests/procedures/ -q
```

### 12.2 Procedure Store 集成测试

```bash
uv run pytest tests/integration/test_procedure_store.py -q
```

### 12.3 全量回归

```bash
just test
```

## 13. 退出与清理

1. **撤销 gateway patch**：删除 `src/gateway/app.py` 中 `--- BEGIN/END P2-M2 USER TEST PATCH ---` 之间的代码
2. 清空测试数据（见 10.5）
3. 停止前后端：`Ctrl+C`
4. 可选停止 PG：`podman stop neomagi-pg`
