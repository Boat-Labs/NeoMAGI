# NeoMAGI

开源 personal agent harness，拥有持久记忆、代表用户信息利益。
受 OpenClaw 架构启发，Python 重写，适配个人基础设施。

## 项目状态

**当前阶段：v0.1 设计中，尚无可运行代码。**

优先实现最小可用版本：Gateway + Single Agent + WebChat（first）+ Telegram（second）+ file-based memory。
不要跳步实现后续功能（hybrid search、multi-agent、heartbeat 等）。

## 项目结构

```
neomagi/
├── CLAUDE.md                 # 本文件：AI assistant 操作手册
├── README.md                 # 项目介绍和运行说明
├── pyproject.toml            # 依赖管理 (uv)
├── .env                      # 环境变量和API Key
├── .env_template             # 环境变量模板（不含真实凭据）
├── decisions/                # 决策追踪（ADR-lite，关键变更需更新）
├── design_docs/              # 设计文档（只读参考，不要修改）
│   ├── architecture.md       # 整体架构
│   ├── memory.md             # 记忆系统设计
│   └── system_prompt.md      # System prompt 文件体系
├── workspace/                # Agent workspace（bootstrap 文件）
│   ├── AGENTS.md             # 行为 SOP
│   ├── SOUL.md               # 人格与价值观
│   ├── USER.md               # 用户偏好
│   ├── IDENTITY.md           # Agent 身份
│   ├── TOOLS.md              # 工具使用备忘
│   ├── MEMORY.md             # 长期记忆（仅私聊加载）
│   ├── HEARTBEAT.md          # 心跳巡检任务
│   └── memory/               # 每日笔记 (YYYY-MM-DD.md)
├── src/
│   ├── gateway/              # WebSocket RPC 服务器
│   ├── agent/                # Agent runtime, system prompt 组装
│   ├── session/              # Session 管理, transcript 存储
│   ├── memory/               # Memory 索引, 搜索
│   ├── tools/                # Tool registry, 内置工具
│   ├── channels/             # Channel adapters (webchat first, telegram second)
│   ├── config/               # 配置加载, 验证, 热加载
│   └── infra/                # 日志, 错误处理, 工具函数
├── tests/
└── config/
    └── neomagi.toml          # 用户配置文件
```

## 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| Language | Python 3.12+ | 全面使用 async/await |
| Package manager | uv | `pyproject.toml` 管理依赖 |
| Frontend package manager | pnpm | WebChat 前端依赖管理 |
| Command runner | just | 统一开发命令入口 |
| Gateway | FastAPI + WebSocket | uvicorn 运行 |
| LLM SDK | `openai` | 统一模型调用入口；OpenAI 默认，Gemini/Ollama 走 OpenAI-compatible 接口 |
| Telegram | `python-telegram-bot` | async 版 |
| Database | PostgreSQL 16 + `pgvector` + ParadeDB `pg_search` | 已有实例运行在 A6000 服务器 |
| Full-text search | ParadeDB `pg_search` (BM25) | 支持 ICU + Jieba 组合分词策略 |
| Vector search | pgvector | 替代 OpenClaw 的 sqlite-vec |
| Config | `pydantic-settings` + `.env` | 启动期统一校验，失败即 fail fast |
| Embedding | 本地模型 via Ollama (优先) → OpenAI fallback |
| Container | Podman (不是 Docker) | |
| Testing | pytest + pytest-asyncio | |
| Linting | ruff | |

**重要：不要使用 SQLite。本项目所有持久化都走 PostgreSQL 16。**
**重要：数据库连接信息读取本地 `.env`，模板维护在 `.env_template`。**
**重要：运行时配置优先级为 环境变量 > `.env` > 默认值；`config/neomagi.toml` 仅用于非敏感默认配置。**
**重要：容器相关命令一律使用 podman，不是 docker。**

## 编码规范

### 风格
- ruff 格式化，行宽 100
- Type hints everywhere，使用 `from __future__ import annotations`
- Pydantic v2 BaseModel 做数据验证和配置
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
- Agent Teams 必须使用 git worktree 隔离并行开发
- 每个 teammate 在独立 worktree 中工作，禁止多人共享同一 working directory
- PM 负责在 spawn 前创建 worktree，在阶段完成后合并和清理
- 分支命名：feat/<role>-<milestone>-<owner-or-task>（如 feat/backend-m1.1-agent-loop, feat/frontend-m1.1-webchat-ui）

## 核心设计决策

### 与 OpenClaw 的关键差异
1. **Python 而非 TypeScript** — 所有架构概念从 OpenClaw 借鉴，但实现完全重写
2. **PostgreSQL 16 + 扩展而非 SQLite** — memory index、session transcript、config state 全部存 PG，检索扩展使用 `pgvector` + ParadeDB `pg_search`
3. **TOML + Pydantic 而非 JSON5 + Zod** — Python 生态的声明式配置验证
4. **Podman 而非 Docker** — sandbox 执行环境使用 podman

### System Prompt 组装顺序
每次 agent turn，按以下顺序拼接 system prompt：
1. Base identity（硬编码的最小身份声明）
2. Tooling（当前可用工具列表 + 简述）
3. Safety（安全护栏）
4. Skills（可用技能列表，如有）
5. Workspace context（从 workspace/ 注入的 bootstrap 文件）
6. Memory recall（memory_search 结果，如有）
7. Date/Time + timezone

### Memory 实现路径（渐进式）
- **v0.1**: File-based only — MEMORY.md + daily notes 直接注入 context，无搜索
- **v0.2**: BM25 search — ParadeDB `pg_search` 全文检索（ICU 主召回 + Jieba 补充）
- **v0.3**: Hybrid search — `pg_search` + `pgvector` 融合排序（weighted score fusion）

### Session 策略
- DM 消息 → 合并到 `main` session
- Group chat → 每个 group 独立 session
- Transcript 存储为 PostgreSQL 表（不是 JSONL 文件）
- Auto-compaction：接近 context limit 时触发 memory flush → 压缩

## 参考项目

阅读顺序推荐：
1. [OpenClaw](https://github.com/openclaw/openclaw) — 主要架构参考，重点看 `src/agents/`, `src/memory/`, `src/gateway/`
2. [pi-mono](https://github.com/badlogic/pi-mono) — Pi agent 的精简实现，理解 agent loop
3. [OpenClaw DeepWiki](https://deepwiki.com/openclaw/openclaw) — 架构图和模块文档

## 开发约定

- 使用中文交流，技术术语保持英文
- 遵循 Linus 哲学：先让最小版本跑起来，再迭代
- 不要一次性生成大量代码。每次实现一个模块，写测试，验证通过后再继续
- 设计文档在 `design_docs/` 中，实现前先阅读对应文档
- 不确定的设计决策，先写 TODO 注释标记，不要自行决定
- 常用开发任务优先通过 `just` 执行，避免散落命令

## M0 决策追踪（多管道统一）

- 关键技术选型、架构边界变更、优先级调整，必须写入 `decisions/`。
- 一条决策一个文件：`decisions/NNNN-short-title.md`。
- 每条决策至少写清楚三件事：选了什么、为什么、放弃了什么。
- 写入或更新决策时，同步维护 `decisions/INDEX.md`。
- 没有实质性取舍时，不新增决策文件，避免噪音。
