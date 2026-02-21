# AGENTS.md

## 定义与范围
- `AGENTS.md` 是仓库级、多代理协作的治理契约；不是产品运行时规范。
- 本文件只定义协作流程、职责边界、交付质量、风险控制与验收门槛。
- 运行时 prompt/memory/heartbeat 等实现细节不在此维护。

## Mission
NeoMAGI 是一个开源 personal agent：有持久记忆、代表用户信息利益、可从商业 API 平滑迁移到本地模型。

## Core Principles
- 考虑充分，实现极简。
- 先做最小可用闭环，不做过度工程。
- 默认给出可执行结果（代码/命令/文件改动），少空谈。
- 以“对抗熵增”为核心设计目标：在满足需求的前提下，优先选择更少概念、更少依赖、更短路径的实现。
- 所有实现在提交前增加一轮“极简审阅”：删除非必要抽象、重复逻辑和可合并配置，以换取长期成长性。

## Safety Boundaries
- Never exfiltrate private data.
- 禁止未经确认执行破坏性操作（删库、批量删除、危险系统命令、历史重写）。
- 对高风险操作先说明影响，再请求确认。

## 协作职责
- PM 负责：任务拆解、worktree 预创建、角色分派、合并顺序、阶段验收。
- Teammate 负责：仅在分配的 worktree 内开发、自测通过后再提交、阻塞及时上报。
- 禁止多人共享同一 working directory。
- 任何阻塞反馈必须包含：现象、影响、已尝试动作、下一步建议。

## Git 与分支策略
- Commit message 格式：`<type>(<scope>): <description>`
  - type: feat, fix, refactor, docs, test, chore
  - scope: gateway, agent, memory, session, tools, channel, config
  - 例: `feat(memory): implement BM25 search with pg_search`
- 一个 commit 做一件事，不要混合不相关的变更。
- Agent Teams 必须使用 git worktree 隔离并行开发。
- 每个 teammate 在独立 worktree 中工作，禁止多人共享同一 working directory。
- PM 负责在 spawn 前创建 worktree，在阶段完成后合并和清理。
- 分支命名：`feat/<role>-<milestone>-<owner-or-task>`（如 `feat/backend-m1.1-agent-loop`）。
- 开始改动前固定执行：`pwd && git branch --show-current && git status --short`。
- 清理或切换 worktree 后，先确认变更已迁移到目标分支，再继续开发或测试。

## 实施基线（治理层）
- 数据库统一使用 PostgreSQL 16（`pgvector` + ParadeDB `pg_search`），不使用 SQLite。
- 数据库连接信息读取本地 `.env`，共享模板使用 `.env_template`（不提交真实凭据）。
- Python 包管理器使用 `uv`。
- Frontend 包管理器使用 `pnpm`。
- 命令入口统一使用 `just`。

## 决策与计划治理

### M0 Governance (Decision Log)
- 关键技术选型、架构边界变更、优先级调整，必须写入 `decisions/`。
- 一条决策一个文件：`decisions/NNNN-short-title.md`。
- 每条决策至少写清楚三件事：选了什么、为什么、放弃了什么。
- 写入或更新决策时，同步维护 `decisions/INDEX.md`。
- 没有实质性取舍时，不新增决策文件，避免噪音。

### Plan 持久化
- `dev_docs/plans/` 不分目录：允许存在一个讨论中的草稿文件和已审批正稿文件。
- 草稿命名：`{milestone}_{目标简述}_{YYYY-MM-DD}_draft.md`。
- 讨论阶段必须持续更新同一个 `_draft` 文件；禁止因讨论轮次新开 `_v2`、`_v3`。
- 用户批准后，使用正确正稿文件名生成计划：`{milestone}_{目标简述}_{YYYY-MM-DD}.md`（或满足条件时 `_v2`、`_v3`），并删除对应 `_draft` 文件。
- `_v2`、`_v3` 仅用于“同一 scope 下，上一版已审批且已执行”后的再次获批修订；不得用于未执行的讨论迭代。
- 这是项目的持久记忆，后续 PM 重启时首先读取最新 plan。

## 质量与验收
- 开发过程先跑受影响测试；里程碑合并前必须跑全量回归。
- 统一命令入口：后端 `just test`，前端 `just test-frontend`，静态检查 `just lint`（必要时 `just format`）。
- 修复/重构任务提交时需附验证结果摘要（命令与通过概况）。

## Agent 工作日志（临时降级策略）
- 状态：M1.5 阶段临时降级为“非阻塞”。
- 原因：Agent Teams 当前存在指令未稳定透传到 agent 层的问题。
- 执行：保留 `dev_docs/logs/{milestone}_{YYYY-MM-DD}/` 目录；由 PM 提交阶段汇总日志，各 role 日志改为尽力提供。
- 验收：当前阶段不因缺少某个 role 日志而阻塞。
- 恢复条件：并行流程连续 3 次无透传丢失后，恢复为强制门槛。
- 如提交 role 日志，仍建议包含：技能/工具名称、调用次数、典型场景、效果评估。
- 如提交 role 日志，需保持脱敏，禁止记录密钥、token、隐私原文。

## Style
- 回复简洁、技术导向、可复制执行。
- 明确假设和限制；不确定时先查证再回答。
- 优先中文，保留必要英文技术术语。

## 规范引用（SSOT）
- 运行时 prompt 文件加载顺序、按需加载与优先级：`design_docs/system_prompt.md`
- Memory 架构与策略：`design_docs/memory_architecture.md`
- 里程碑顺序与产品实现路线：`design_docs/roadmap_milestones_v3.md`
- 详细开发手册与技术栈摘要：`CLAUDE.md`
