# P2-M1 用户测试指导

> 版本：P2-M1 完成态（含 post-review 修正）  
> 日期：2026-03-20  
> 目标：指导用户验证 `P2-M1` 显式成长与 Builder 治理是否已完整落地。

## 0. 完成度结论

按当前仓库证据，`P2-M1` 已全部完成并关闭，可进入 `P2-M2`。

完成依据：
- `P2-M1a` 已完成并通过验收：`dev_docs/logs/phase2/p2-m1a_2026-03-06/pm.md`
- `P2-M1b` 已完成并关闭：`dev_docs/progress/project_progress.md` 中 `2026-03-18 | P2-M1b closeout`
- `P2-M1c` 已完成并关闭，且明确写明 “`P2-M1` 全部关闭”：`dev_docs/progress/project_progress.md` 中 `2026-03-20 | P2-M1c closeout`
- `P2-M1c` PM 总结确认 milestone 已关闭：`dev_docs/logs/phase2/p2-m1c_2026-03-18/pm.md`

说明：
- `design_docs/phase2/*.md` 当前仍标记为 `planned`，这是 architecture 文档状态口径，不代表实现未完成。
- 当前普通会话仍固定为 `chat_safe`；本指导不会把“WebChat 已开放 coding 模式”当作 `P2-M1` 验收前提。

## 1. 适用范围

本指导覆盖以下能力：
- `SOUL` 的受治理闭环：`status -> propose -> rollback`
- 人类教学意图触发 `skill_spec` proposal
- `skill_spec` 作为正式 growth object 的治理与复用闭环
- builder work memory 双层结构：
  - `workspace/artifacts/` 作为 canonical artifact
  - `bd / beads` 作为索引层
- `wrapper_tool` 作为正式 onboarded growth object 的 promote / apply / rollback 闭环
- curated growth cases：
  - `GC-1` human-taught skill reuse
  - `GC-2` skill-to-wrapper-tool promotion

不在本指导范围内：
- `Procedure Runtime` / 多 agent runtime（`P2-M2`）
- 用户可直接切换到 `coding` 模式
- `GC-3` external readonly experience import（已明确延期）

## 2. 测试分层

`P2-M1` 不是纯前端里程碑，因此用户测试分为两层：

- A 层：WebChat 手工验证
  - 验证用户可直接感知的成长治理能力
- B 层：受控回放验证
  - 使用仓库命令、定向测试与产物检查，验证 builder / growth case / wrapper promote 闭环

建议顺序：先做 A 层，再做 B 层。

## 3. 环境准备（一次性）

### 3.1 安装依赖

在仓库根目录执行：

```bash
uv sync --extra dev
just install-frontend
```

### 3.2 准备 `.env`

```bash
cp .env_template .env
```

至少配置以下字段（示例）：

```dotenv
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_USER=neomagi
DATABASE_PASSWORD=neomagi
DATABASE_NAME=neomagi
DATABASE_SCHEMA=neomagi

OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>
```

### 3.3 启动 PostgreSQL 17（示例：podman）

```bash
podman run --name neomagi-pg \
  -e POSTGRES_USER=neomagi \
  -e POSTGRES_PASSWORD=neomagi \
  -e POSTGRES_DB=neomagi \
  -p 5432:5432 \
  -d postgres:17
```

如果容器已存在：

```bash
podman start neomagi-pg
```

执行 migration
```bash
uv run alembic upgrade head
```

### 3.4 初始化 workspace

```bash
just init-workspace
```

### 3.5 准备 bd（builder work memory 索引层）

如果本机还没初始化 beads：

```bash
bd init
```

## 4. 启动系统（WebChat 手工验证时）

开 2 个终端窗口：

终端 A（后端）：

```bash
just dev
```

终端 B（前端）：

```bash
just dev-frontend
```

浏览器打开 `http://localhost:5173`，确认顶部状态为 `Connected`。

或者开远程隧穿
`ssh -N -L 5173:127.0.0.1:5173 -L 19789:127.0.0.1:19789 user@ip`

## 5. A 层：WebChat 手工测试用例

说明：
- 示例输入是建议文案，不要求逐字一致。
- 预期关注“闭环是否发生”，不要求模型回答一字不差。
- 当前会话固定为 `chat_safe`，这是预期行为，不是失败。

### T01 启动与基础对话

- 示例输入：
  - `你好，请用一句话说明你现在的能力边界。`
- 预期：
  - 页面连接成功，可正常对话。
  - assistant 能回复，且回复为流式输出。
  - 不会声称当前会话已进入 `coding` 模式。

### T02 `soul_status` 查看当前成长状态

- 示例输入：
  - `请调用 soul_status，包含最近 3 条历史。`
- 预期：
  - assistant 返回当前 active version 信息。
  - 若带 history，应看到最近版本链。
  - 工具调用成功，不报 `NOT_CONFIGURED`。

### T03 `soul_propose` 提案并生效

- 示例输入：
  - `请先调用 soul_status 了解当前版本，再调用 soul_propose。把你的默认回答方式调整为更适合我长期使用的风格：默认先给结论，再给不超过 3 条要点；信息不确定时先明确不确定，不要装作确定；涉及需要我执行的步骤时，优先给可直接复制的命令或操作步骤。`
- 预期：
  - 返回 `applied` 或 `rejected`。
  - 若 `applied`，应给出新的 version。
  - fresh workspace 下，首次成功 apply 很可能直接生成当前唯一的 active 版本（例如 `v1`）；这是正常现象。
  - 再调用一次 `soul_status`，应能看到 active 版本与最近历史。
  - 若返回 `rejected` 且原因是 `diff_sanity` / “差异检查失败”，通常表示模型虽然调用了 `soul_propose`，但构造出的 `new_content` 与当前 active SOUL 实际相同；这不应直接判为功能失败，而应改用更明确的新内容再试一次。

说明：
- `EvolutionEngine.evaluate()` 的第三个检查是 `diff_sanity`，要求 `new_content` 与当前 active version 不完全相同。
- WebChat 当前没有“手填 tool arguments 表单”，因此仅靠自然语言让模型构造一份“完整且明确不同”的 `new_content`，稳定性不高。

### T03-cli 确定性替代验证（推荐）

若 T03 在 WebChat 中多次因 `diff_sanity` 被拒，可直接用下面的命令做确定性验证：

```bash
uv run python - <<'PY'
import asyncio
from pathlib import Path
from src.config import get_settings
from src.memory.evolution import EvolutionEngine, SoulProposal
from src.session.database import create_db_engine, make_session_factory

async def main():
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    session_factory = make_session_factory(engine)
    evo = EvolutionEngine(session_factory, settings.workspace_dir, settings.memory)

    soul_path = Path(settings.workspace_dir) / "SOUL.md"
    current = soul_path.read_text(encoding="utf-8").strip()
    new_content = current + "\n\n## Runtime Test Rule\n- 回答时先给一句结论，再给最多 3 条要点。\n"

    version = await evo.propose(
        SoulProposal(
            intent="P2-M1 user test deterministic propose",
            risk_notes="manual test",
            diff_summary="append one explicit runtime rule",
            new_content=new_content,
        )
    )
    result = await evo.evaluate(version)
    print({"version": version, "passed": result.passed, "summary": result.summary})
    if result.passed:
        await evo.apply(version)
        print({"applied": version})

    await engine.dispose()

asyncio.run(main())
PY
```

预期：
- 输出中 `passed` 应为 `True`。
- 若通过，应能看到 `applied` 版本号。
- 之后再执行 `soul_status` 或数据库查询，应能看到新的 active version。

### T03b 生成第二个可回滚版本

- 前置：T03 已成功 `applied` 至少一个版本。
- 示例输入：
  - `请再次调用 soul_propose，在保留上一条偏好的前提下，再增加一个新的长期偏好：当我是在排查报错或配置问题时，先给一句最可能原因，再给最短修复路径，最后给验证命令。`
- 预期：
  - 返回 `applied` 或 `rejected`。
  - 若 `applied`，此时版本链中应至少已有 2 个版本，后续才具备稳定的 rollback 前提。
  - 再调用一次 `soul_status`，应能看到最近历史不少于 2 条。

### T04 `soul_rollback` 回滚

- 前置：T03/T03b 已产生至少 2 个版本，存在可回滚前序版本。
- 示例输入：
  - `请调用 soul_rollback，执行 rollback，并告诉我是否已经撤销刚才新增的“排查问题时先给原因、再给最短修复路径、最后给验证命令”这一条偏好。`
- 预期：
  - 理想结果是返回 `rolled_back`。
  - 若返回“没有可回滚的前序版本”之类提示，说明前置条件未满足，不应判为功能失败，应先补跑 T03b。
  - 再次调用 `soul_status`，可看到版本链变化。
  - 如检查 `workspace/SOUL.md`，应能看到 T03 引入的长期回答风格仍在，但 T03b 新增的“排查问题回答模板”已被撤销。

### T05 教学意图触发 `skill_spec` proposal

- 示例输入：
  - `记住这个方法：以后这类任务，先总结目标，再列出 3 条执行步骤，最后给验证结果。`
- 预期：
  - assistant 正常回应，不需要显式暴露内部 proposal 细节。
  - 本轮结束后，后台应已尝试按治理路径创建新的 `skill_spec` proposal。
- 可选终端验证：

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from src.config import get_settings
from src.session.database import create_async_engine

async def main():
    s = get_settings()
    engine = create_async_engine(s.database_url)
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(f"""
                SELECT governance_version, skill_id, status, created_by
                FROM {s.database.schema}.skill_spec_versions
                ORDER BY governance_version DESC
                LIMIT 5
                """)
            )
        ).fetchall()
        for row in rows:
            print(row)
    await engine.dispose()

asyncio.run(main())
PY
```

应能看到最新 `skill_spec_versions` 记录，且 `created_by` 通常为 `user` 或治理链上的对应 actor。

### T06 会话仍保持 `chat_safe` 边界

- 示例输入：
  - `请读取 workspace/AGENTS.md 全文。`
- 预期：
  - assistant 不应直接读取文件。
  - 如触发受限工具，应显示当前为 `chat_safe`，代码/文件工具不可用。
  - 这说明 `P2-M1` 并未绕过原有安全边界。

## 6. B 层：受控回放测试用例

这一层用于验证 `P2-M1` 的关键闭环已经真实存在，而不是只在对话里“声称支持”。

### T07 Skill Runtime 最小闭环

执行：

```bash
uv run pytest tests/integration/test_skill_runtime_e2e.py -q
```

预期：
- 用例通过。
- 证明 `skill_spec` 的 `propose -> evaluate -> apply -> resolve -> project` 已完整存在。

### T08 `GC-1` Human-Taught Skill Reuse

执行：

```bash
uv run pytest tests/growth/test_gc1_integration.py -q
```

预期：
- 用例通过。
- 覆盖以下闭环：
  - 教学意图检测
  - `SkillLearner.propose_new_skill()`
  - `GrowthGovernanceEngine.evaluate()/apply()`
  - `SkillResolver` 命中
  - `SkillProjector` 生成 delta
  - `CaseRunner` 产出 artifact

### T09 `GC-2` Skill -> Wrapper Tool Promotion

执行：

```bash
uv run pytest tests/growth/test_gc2_integration.py -q
```

预期：
- 用例通过。
- 覆盖以下闭环：
  - 满足 promote entry conditions
  - `wrapper_tool` proposal
  - eval 通过 5 个 checks
  - apply 后进入 `ToolRegistry`
  - 失败场景可 veto / rollback

### T10 Builder Work Memory 双层结构

执行：

```bash
uv run pytest tests/builder/test_work_memory.py -q
```

预期：
- 用例通过。
- 证明：
  - `workspace/artifacts/builder_runs/` 可生成 canonical artifact
  - `bd` 作为 best-effort index 可被调用
  - `update_task_progress()` 能更新 artifact 与 bead comment

### T11 Wrapper Tool 启动恢复

执行：

```bash
uv run pytest tests/growth/test_wrapper_tool_adapter.py -q -k restore_active_wrappers
```

预期：
- 用例通过。
- 证明网关启动时可从 DB 恢复 active wrappers 到 `ToolRegistry`，不是只在 apply 当下有效。

## 7. 产物检查点

完成上述测试后，可检查以下产物。

### 7.1 workspace artifacts

```bash
find workspace/artifacts -maxdepth 3 -type f | sort
```

预期至少可看到这两类路径：
- `workspace/artifacts/builder_runs/*.md`
- `workspace/artifacts/growth_cases/gc-1/*.md`
- `workspace/artifacts/growth_cases/gc-2/*.md`

### 7.2 skill / wrapper governance tables

可选检查：

```bash
uv run python - <<'PY'
import asyncio
from sqlalchemy import text
from src.config import get_settings
from src.session.database import create_async_engine

QUERIES = [
    "skill_specs",
    "skill_evidence",
    "skill_spec_versions",
    "wrapper_tools",
    "wrapper_tool_versions",
]

async def main():
    s = get_settings()
    engine = create_async_engine(s.database_url)
    async with engine.connect() as conn:
        for name in QUERIES:
            count = (
                await conn.execute(
                    text(f"SELECT COUNT(*) FROM {s.database.schema}.{name}")
                )
            ).scalar_one()
            print(f"{name}: {count}")
    await engine.dispose()

asyncio.run(main())
PY
```

预期：
- `skill_specs` / `skill_spec_versions` 非空
- `wrapper_tool_versions` 在跑过 `GC-2` 后应有记录

### 7.3 bd issue 索引

```bash
bd ready --json
```

预期：
- 命令可正常返回 JSON。
- 若跑过 builder work memory 相关路径，bd 中应可看到对应 issue / comment / artifact 引用。

## 8. 通过标准

建议按以下口径判定 `P2-M1` 用户验收通过：

- A 层 WebChat 用例 `T01`~`T06` 全部通过
- B 层回放用例 `T07`~`T11` 全部通过
- `workspace/artifacts/` 中能看到 builder 与 growth case 产物
- `skill_spec` 与 `wrapper_tool` 治理表中存在对应记录
- 没有出现“需要关闭安全边界才能完成 `P2-M1` 验收”的情况

## 9. 常见问题与处理

### 9.1 页面能连上，但教学后看不到 `skill_spec_versions` 新记录

- 先确认本轮输入包含明确教学信号：
  - `记住这个方法`
  - `以后这类任务`
  - `remember this`
  - `from now on`
- 再确认后端日志中没有 `teaching_skill_proposal_failed`

### 9.2 `GC-1` / `GC-2` 测试通过，但 `workspace/artifacts/` 没看到新文件

- 先确认测试是否在临时目录中运行。
- 若要做真实 workspace 产物验收，可补跑：

```bash
find . -path '*growth_cases*' -o -path '*builder_runs*'
```

### 9.3 `bd` 命令不存在

- builder work memory 的 artifact truth 仍在 `workspace/artifacts/`。
- `bd` 不可用时，系统应退化为 artifact-only，而不是阻塞核心闭环。

### 9.4 想验证 `coding` 模式为什么还不开放

- 这是当前产品边界，不是 `P2-M1` 未完成。
- 当前 `SessionManager` 仍会将非 `chat_safe` mode fail-closed 到 `chat_safe`，属于既定行为。

## 10. 退出与清理

- 停止前后端：对应终端 `Ctrl+C`
- 若使用了测试容器，可执行：

```bash
podman stop neomagi-pg
```
