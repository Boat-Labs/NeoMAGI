# AGENTS.md

## Mission
NeoMAGI 是一个开源 personal agent：有持久记忆、代表用户信息利益、可从商业 API 平滑迁移到本地模型。

## Core Principles
- 考虑充分，实现极简。
- 先做最小可用闭环，不做过度工程。
- 默认给出可执行结果（代码/命令/文件改动），少空谈。

## M0 Governance (Decision Log)
- 关键技术选型、架构边界变更、优先级调整，必须写入 `decisions/`。
- 一条决策一个文件：`decisions/NNNN-short-title.md`。
- 每条决策至少写清楚三件事：选了什么、为什么、放弃了什么。
- 写入或更新决策时，同步维护 `decisions/INDEX.md`。
- 没有实质性取舍时，不新增决策文件，避免噪音。

## Baseline Decisions (Must Follow)
- 数据库统一使用 PostgreSQL（`pgvector`），不使用 SQLite。
- 数据库连接信息读取本地 `.env`，共享模板使用 `.env_template`（不提交真实凭据）。
- Python 包管理器使用 `uv`。
- 命令入口统一使用 `just`。
- 模型路线：OpenAI 为默认运行路径，Gemini 用于迁移验证。
- 渠道路线：WebChat first，Telegram second。

## Build Order (MVP)
1. Gateway（WebSocket 路由与调度）
2. Agent Runtime（system prompt 组装 + model 调用 + tool loop）
3. Session（main/group 隔离 + transcript + compaction）
4. Memory（先 BM25，再 Hybrid Search）
5. Tool Registry（exec/read/write/edit/memory_search）
6. One Channel Adapter（先单平台打通）
7. Config（JSON5 + schema 验证 + 热加载）

## Memory Rules
- 记忆基于文件，不依赖模型参数记忆。
- 短期记忆：`memory/YYYY-MM-DD.md`，append-only。
- 长期记忆：`MEMORY.md`（策展后的稳定信息）。
- 自动加载今天+昨天的 daily notes。
- 仅在 main session 注入 `MEMORY.md`；群聊不注入。
- 接近 context 上限时先做 memory flush，再 compaction。

## Prompt Files (When Present)
每次 turn 优先读取并遵循：
- `AGENTS.md`（行为契约）
- `SOUL.md`（人格/语气）
- `USER.md`（用户偏好）
- `IDENTITY.md`（身份展示）
- `TOOLS.md`（工具与环境备忘）

按需加载：
- `MEMORY.md`（仅 main session）
- `HEARTBEAT.md`（心跳轮询）
- `BOOTSTRAP.md` / `BOOT.md`（初始化/启动）

冲突时优先级：Safety > AGENTS.md > USER.md > SOUL.md > IDENTITY.md

## Safety Boundaries
- Never exfiltrate private data.
- 禁止未经确认执行破坏性操作（删库、批量删除、危险系统命令）。
- 对高风险操作先说明影响，再请求确认。

## Style
- 回复简洁、技术导向、可复制执行。
- 明确假设和限制；不确定时先查证再回答。
- 优先中文，保留必要英文技术术语。
