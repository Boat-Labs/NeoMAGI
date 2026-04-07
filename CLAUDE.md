# NeoMAGI

开源 personal agent harness，拥有持久记忆、代表用户信息利益。
受 OpenClaw 架构启发，Python 重写，适配个人基础设施。

## Core Principles

- 考虑充分，实现极简。
- 先做最小可用闭环，不做过度工程。
- 默认给出可执行结果（代码/命令/文件改动），少空谈。
- 以"对抗熵增"为核心设计目标：在满足需求的前提下，优先选择更少概念、更少依赖、更短路径的实现。
- 所有实现在提交前增加一轮"极简审阅"：删除非必要抽象、重复逻辑和可合并配置，以换取长期成长性。

## 项目结构

```
neomagi/
├── CLAUDE.md / AGENTTEAMS.md / README.md / pyproject.toml
├── .env / .env_template          # 环境变量（不提交真实凭据）
├── decisions/                    # ADR-lite 决策追踪
├── design_docs/                  # 只读参考（入口：index.md）
├── dev_docs/                     # 计划、日志
├── alembic/                      # DB migrations
├── src/
│   ├── backend/ frontend/        # 后端入口 + WebChat 前端
│   ├── gateway/ agent/ session/ memory/ tools/ channels/
│   ├── config/ infra/            # 配置加载 + 日志/错误/工具函数
│   └── constants.py
└── tests/
```

## 技术栈

**核心**：Python 3.12+ (async/await) · uv · pnpm (frontend) · just（常规开发任务） · FastAPI + WebSocket · `openai` SDK
**存储**：PostgreSQL 17 + `pgvector` + ParadeDB `pg_search` (BM25, ICU + Jieba) · Embedding: Ollama 优先 → OpenAI fallback
**工具链**：pytest + pytest-asyncio · ruff · Podman · `pydantic-settings` + `.env`

> **重要**：不使用 SQLite，所有持久化走 PostgreSQL 17。数据库连接读 `.env`，模板在 `.env_template`。配置优先级：环境变量 > `.env` > 默认值。容器命令一律 podman，不用 docker。

## 文档 ID 规则

- `design_docs/` 和 `dev_docs/` 下的所有 `.md` 文件必须有 UUIDv7 frontmatter：
  ```yaml
  ---
  doc_id: 019d6457-9290-7dda-...
  doc_id_format: uuidv7
  doc_id_assigned_at: 2026-04-07T00:00:00+02:00
  ---
  ```
- 新建文档时手动添加，或用 `uv run python scripts/assign_doc_ids.py --apply` 批量补录。
- UUIDv7 的时间戳分量编码文件的 git 最后修改时间（新文件即当前时间）。
- `doc_id_assigned_at` 必须从 `doc_id` 的 UUIDv7 前 48 bit 解码得到，不得手动估算。解码方式：将前 48 bit 解释为 unix 毫秒时间戳，转换为本地时区 ISO 8601。
- 已有 `doc_id` 的文件不会被覆盖（脚本幂等）。

## 编码规范

### 风格
- ruff 格式化，行宽 100
- Type hints everywhere，使用 `from __future__ import annotations`
- Pydantic v2：BaseModel 用于数据验证；BaseSettings（pydantic-settings）用于配置加载与 env_prefix，禁止混用。
- 优先使用 `pathlib.Path`，不用 `os.path`
- 日志使用 `structlog`，不用 `print` 或 `logging`

### 异步
- 所有 I/O 操作必须 async：数据库查询、HTTP 请求、文件读写（aiofiles）、LLM 调用
- 使用 `asyncio.TaskGroup` 管理并发任务（Python 3.11+）
- 禁止在 async 函数中调用同步阻塞 I/O

### 错误处理
- 自定义异常层次：`NeoMAGIError` → `GatewayError`, `AgentError`, `MemoryError`, `ChannelError`
- LLM 调用必须有 retry + exponential backoff
- 外部服务调用（数据库、Telegram API）必须有 timeout
- 绝不吞异常：最低限度也要 `logger.exception()`

### 测试
- 每个模块必须有对应的 `tests/test_<module>.py`
- LLM 调用使用 mock，不在测试中消耗 API quota
- 数据库测试使用 fixture 管理 test schema，测试后清理
- 目标覆盖率：核心模块（agent, memory, session）> 80%

### Git
- Commit message 格式：`<type>(<scope>): <description>`
  - type: feat, fix, refactor, docs, test, chore
  - scope: gateway, agent, memory, session, tools, channel, config
  - 例: `feat(memory): implement BM25 search with pg_search`
- 一个 commit 做一件事，不要混合不相关的变更
- 开始改动前固定执行：`pwd && git branch --show-current && git status --short`
- 未经确认禁止执行破坏性操作（强制覆盖、批量删除、历史重写）
- **Agent Teams worktree 规则**：PM 负责维护，teammate 必须遵守。必须使用 git worktree 隔离并行开发，每个 teammate 独立 worktree，PM 负责创建/合并/清理。分支命名：`feat/<role>-<milestone>-<owner-or-task>`。切换 worktree 后先确认变更已迁移到目标分支。完整治理协议见 `AGENTTEAMS.md`。
- **Tester review branch 规则**：遵循 `one gate one review branch`。tester 的 review branch 一旦 push 即视为不可变审阅产物；同一 Gate 的 re-review 也必须新开 fresh tester worktree + branch，如 `feat/tester-m4-g0`、`feat/tester-m4-g0-r2`。Claude Code tester 不执行 `git push --force-with-lease`；若 push 需要 force，必须立即停下并回报 `blocked`，由 PM 提供新的 review branch/worktree。

## Agent Teams 治理

> Agent Teams 协作控制规则以 `AGENTTEAMS.md` 为 SSOT。`CLAUDE.md`（Claude Code）与 `AGENTS.md`（其他系统）为一致性镜像入口，三者必须保持一致。PM 角色 spawn teammate 时加载 `AGENTTEAMS.md`。
> 使用 devcoord 协作控制时，Claude Code 角色额外加载 `.claude/skills/devcoord-pm/SKILL.md`、`.claude/skills/devcoord-backend/SKILL.md`、`.claude/skills/devcoord-tester/SKILL.md` 中对应角色的 project skill。
> 对 Claude Code 的 devcoord 关键流程，优先使用 slash skill 形式（如 `/devcoord-backend`、`/devcoord-tester`），并用 CLI debug 的 `processPromptSlashCommand` / `SkillTool returning` 校验实际命中。
> 对 Claude Code 的 teammate devcoord 写操作，先校验 `git rev-parse HEAD == target_commit`；若不一致，只回报阻塞，不写控制面。

## 架构信息分层

- 全局强制基线（所有 agent 默认遵守）只保留在本文件：
  - Python 实现（非 TypeScript）
  - PostgreSQL 17（非 SQLite）
  - `pydantic-settings` + `.env` 配置
  - Podman 容器命令（非 Docker）
- 设计细节与外部参考按需读取，不在本文件展开：
  - 统一入口：`design_docs/index.md`
  - Prompt 组装：`design_docs/system_prompt.md`
  - Memory 架构：`design_docs/memory_architecture_v2.md`
  - Session/模块边界：`design_docs/modules.md`
  - Phase 1 归档：`design_docs/phase1/index.md`
  - Milestone 命名规则：跨 phase 文档统一使用 `P1-M*` / `P2-M*`，避免裸 `M*` 歧义

## 开发约定

- 使用中文交流，技术术语保持英文
- 遵循 Linus 哲学：先让最小版本跑起来，再迭代
- 不要一次性生成大量代码。每次实现一个模块，写测试，验证通过后再继续
- 设计文档在 `design_docs/` 中，实现前先阅读对应文档
- 不确定的设计决策，先写 TODO 注释标记，不要自行决定
- 常用开发任务优先通过 `just` 执行，避免散落命令；devcoord 控制面协议写操作除外，统一直接调用 `uv run python scripts/devcoord/coord.py`
- devcoord 协作控制的 append-first 落点是 `.devcoord/control.db`；`dev_docs/logs/phase1/*`、`dev_docs/logs/phase2/*` 和 `dev_docs/progress/project_progress.md` 只作为 `render` 生成的 projection，不直接手写。
- beads 备份统一使用 `just beads-backup`（等价于 `bd backup --force`），它会自动创建本地 backup commit，之后正常 `git push` 即可。若某次 `bd` 版本未自动生成 backup commit，手工 `git add .beads/backup/ && git commit -m "bd: backup <date>"`。不要使用 `bd sync` / `bd dolt pull` / `bd dolt push` / `just beads-pull` / `just beads-push`（Dolt remote 已废弃，见 ADR 0052）。
- 仅当本轮实际修改了 beads / bd issue 数据时，才需要运行 beads backup；devcoord control-plane 写入不再触发 beads backup 要求（SSOT 已迁至 `.devcoord/control.db`）；纯代码 / 文档 / 测试改动无需运行。
- beads 恢复路径：`bd init && bd backup restore`。

## M0 决策追踪（多管道统一）

- 关键技术选型、架构边界变更、优先级调整，必须写入 `decisions/`。
- 一条决策一个文件：`decisions/NNNN-short-title.md`。
- 每条决策至少写清楚三件事：选了什么、为什么、放弃了什么。
- 写入或更新决策时，同步维护 `decisions/INDEX.md`。
- 没有实质性取舍时，不新增决策文件，避免噪音。

## Plan 持久化

- 计划文件统一放在 `dev_docs/plans/` 体系下；实际计划按 phase 写入 `dev_docs/plans/phase1/`、`dev_docs/plans/phase2/`，禁止直接写到根目录。
- 草稿命名：`{milestone}_{目标简述}_{YYYY-MM-DD}_draft.md`；讨论阶段持续更新同一 `_draft` 文件。
- 用户批准后生成正稿：`{milestone}_{目标简述}_{YYYY-MM-DD}.md`，并删除 `_draft`。`_v2`/`_v3` 仅用于上一版已审批且已执行后的再次修订。
- 这是项目的持久记忆；后续 PM 重启时先读取当前 active phase 子目录中的最新 plan，再按需回溯其他 phase。
- 产出计划前先对齐 `AGENTTEAMS.md`、`AGENTS.md`、`CLAUDE.md`、`decisions/`、`design_docs/` 约束。

## Progress 持久化

- `dev_docs/progress/project_progress.md` 保持为全局 append-only 项目总账，不按 phase 拆分，也不重命名为 `phase1_*` / `phase2_*`。
- phase 边界通过同一文件中的 transition / closeout 记录表达，而不是通过新建 phase 专属 progress 文件表达。
- 进入新 phase 时，优先读取当前 active phase 的 `design_docs/phase*/`、`dev_docs/plans/phase*/`；`project_progress.md` 只作为全局时间线与证据索引，避免把整段历史误当成当前默认上下文。

## 评审与迭代协议

- 对 design/plan/fix 文档，默认执行"先约束清单、后草稿、再自检、最后提交"。
- 提交前必须完成一次自检：命名一致、路径正确、实现步骤与测试策略一致、无内部矛盾、无静默吞异常。
- 每轮评审回复必须包含：本轮修改项、已解决问题、未解决问题/风险。
- 信息不足时先列缺失上下文并请求补充，禁止臆测关键架构决策。

## 测试执行基线

- 开发过程中先跑受影响测试；提交前必须跑全量回归。
- 后端测试使用 `just test`，前端测试使用 `just test-frontend`，静态检查使用 `just lint`（必要时 `just format`）。
- devcoord 控制面写操作不走 `just`，统一使用 `uv run python scripts/devcoord/coord.py`，优先走结构化 payload。
- 关 gate 前固定执行 `render` + `audit`；只有 `audit.reconciled=true` 才允许 `gate-close`。
- 新 worktree 先完成环境检查（`.env`、依赖安装）再运行测试。
- 事件名/字段名必须以代码真实定义为准，禁止按猜测编写测试。

## 复杂度治理

- 默认目标：`src/`、`scripts/` 追求 `单文件 <= 500`、`单函数 <= 30`、`嵌套 <= 3`、`分支 <= 3`。
- 硬门禁：`src/`、`scripts/` 出现 `单文件 > 800`、`单函数 > 50`、`嵌套 > 3`、`分支 > 6` 即视为 block。
- `tests/` 文件级阈值放宽到 `<= 1200`，但函数级红线仍按 `50 / 3 / 6` 执行；`alembic/versions/` 不纳入自动文件长度治理。
- 采用 ratchet 治理：`.complexity-baseline.json` 记录当前 block 级存量债务，`just lint` 只阻止新增或恶化的 block 问题，不要求一次性清零历史债。
- 当前自动检查覆盖所有 tracked `*.py/*.ts/*.tsx/*.js/*.jsx` 的文件长度，以及 Python 的函数长度/分支/嵌套；前端函数级自动化后续补齐。
- `src.infra.complexity_guard` 的扫描范围固定为 git tracked 的 `src/`、`scripts/` 与测试路径；`alembic/versions/` 和其他路径默认忽略，`.complexity-overrides.json` 不负责扩展扫描范围。
- `.complexity-overrides.json` 只做局部覆盖；当前仅支持 `skip_file_lines`，按 repo 相对路径跳过 `file_lines` 检查，不关闭 Python 的函数级检查。
- 常用命令：`just complexity-report` 查看全仓快照，`just complexity-baseline` 在完成一轮明确治理后刷新 baseline。
- 任何触碰现有超线热点的改动，默认要求“至少不再变坏”；若顺手能拆一层，优先拆分而不是继续堆叠。
