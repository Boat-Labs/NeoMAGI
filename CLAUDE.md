# NeoMAGI

开源 personal agent harness，拥有持久记忆、代表用户信息利益。
受 OpenClaw 架构启发，Python 重写，适配个人基础设施。

## Core Principles

- 考虑充分，实现极简。
- 先做最小可用闭环，不做过度工程。
- 默认给出可执行结果（代码/命令/文件改动），少空谈。
- 以"对抗熵增"为核心设计目标：在满足需求的前提下，优先选择更少概念、更少依赖、更短路径的实现。
- 所有实现在提交前增加一轮"极简审阅"：删除非必要抽象、重复逻辑和可合并配置，以换取长期成长性。

## 项目状态

**当前阶段：M3 已完成（含 post-review 修正），下一阶段 M6（模型迁移验证）。**

当前基线：481 tests 全绿，ruff clean，CI 已落地。
下一阶段优先级以 `design_docs/roadmap_milestones_v3.md` 为准。

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
**存储**：PostgreSQL 16 + `pgvector` + ParadeDB `pg_search` (BM25, ICU + Jieba) · Embedding: Ollama 优先 → OpenAI fallback
**工具链**：pytest + pytest-asyncio · ruff · Podman · `pydantic-settings` + `.env`

> **重要**：不使用 SQLite，所有持久化走 PostgreSQL 16。数据库连接读 `.env`，模板在 `.env_template`。配置优先级：环境变量 > `.env` > 默认值。容器命令一律 podman，不用 docker。

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

## Agent Teams 治理

> Agent Teams 协作控制规则以 `AGENTTEAMS.md` 为 SSOT。`CLAUDE.md`（Claude Code）与 `AGENTS.md`（其他系统）为一致性镜像入口，三者必须保持一致。PM 角色 spawn teammate 时加载 `AGENTTEAMS.md`。
> 使用 devcoord 协作控制时，Claude Code 角色额外加载 `.claude/skills/devcoord-pm/SKILL.md`、`.claude/skills/devcoord-backend/SKILL.md`、`.claude/skills/devcoord-tester/SKILL.md` 中对应角色的 project skill。
> 对 Claude Code 的 devcoord 关键流程，优先使用 slash skill 形式（如 `/devcoord-backend`、`/devcoord-tester`），并用 CLI debug 的 `processPromptSlashCommand` / `SkillTool returning` 校验实际命中。
> 对 Claude Code 的 teammate devcoord 写操作，先校验 `git rev-parse HEAD == target_commit`；若不一致，只回报阻塞，不写控制面。

## 架构信息分层

- 全局强制基线（所有 agent 默认遵守）只保留在本文件：
  - Python 实现（非 TypeScript）
  - PostgreSQL 16（非 SQLite）
  - `pydantic-settings` + `.env` 配置
  - Podman 容器命令（非 Docker）
- 设计细节与外部参考按需读取，不在本文件展开：
  - 统一入口：`design_docs/index.md`
  - Prompt 组装：`design_docs/system_prompt.md`
  - Memory 架构：`design_docs/memory_architecture.md`
  - Session/模块边界：`design_docs/modules.md`

## 开发约定

- 使用中文交流，技术术语保持英文
- 遵循 Linus 哲学：先让最小版本跑起来，再迭代
- 不要一次性生成大量代码。每次实现一个模块，写测试，验证通过后再继续
- 设计文档在 `design_docs/` 中，实现前先阅读对应文档
- 不确定的设计决策，先写 TODO 注释标记，不要自行决定
- 常用开发任务优先通过 `just` 执行，避免散落命令；devcoord 控制面协议写操作除外，统一直接调用 `uv run python scripts/devcoord/coord.py`
- devcoord 协作控制的 append-first 落点是 beads 事件；`dev_docs/logs/*` 和 `dev_docs/progress/project_progress.md` 只作为 `render` 生成的 projection，不直接手写。

## M0 决策追踪（多管道统一）

- 关键技术选型、架构边界变更、优先级调整，必须写入 `decisions/`。
- 一条决策一个文件：`decisions/NNNN-short-title.md`。
- 每条决策至少写清楚三件事：选了什么、为什么、放弃了什么。
- 写入或更新决策时，同步维护 `decisions/INDEX.md`。
- 没有实质性取舍时，不新增决策文件，避免噪音。

## Plan 持久化

- 计划文件统一放 `dev_docs/plans/`，禁止写入其他路径。
- 草稿命名：`{milestone}_{目标简述}_{YYYY-MM-DD}_draft.md`；讨论阶段持续更新同一 `_draft` 文件。
- 用户批准后生成正稿：`{milestone}_{目标简述}_{YYYY-MM-DD}.md`，并删除 `_draft`。`_v2`/`_v3` 仅用于上一版已审批且已执行后的再次修订。
- 这是项目的持久记忆，后续 PM 重启时首先读取最新 plan。
- 产出计划前先对齐 `AGENTTEAMS.md`、`AGENTS.md`、`CLAUDE.md`、`decisions/`、`design_docs/` 约束。

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
