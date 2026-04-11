# NeoMAGI

[English](README.md) | [Deutsch](README_de.md)

NeoMAGI 是一个开源 personal agent 项目。

它的产品想法很直接：做一个能够跨时间保留记忆、代表用户信息利益、并且可以从商业模型 API 逐步迁移到更本地化、更可控模型栈的 agent。

## 产品定位

NeoMAGI 不想做一个通用的 chatbot 外壳。

它的目标方向是一个长期协作型的伙伴式 AI，核心特征是：
- 能持续记住有用的上下文
- 以用户利益为中心，而不是平台激励为中心
- 能在可控、可审计的前提下扩展能力
- 保留从商业 API 平滑迁移到本地模型的现实路径

## 原则

- 考虑充分，实现极简。
- 优先做最小可用闭环。
- 避免不必要的抽象和依赖膨胀。
- 把治理、回滚、作用域边界当作产品能力，而不只是工程细节。

## 已建能力

- **多渠道交互**：WebSocket (WebChat) + Telegram，渠道无关调度
- **持久记忆**：PostgreSQL 混合检索（向量 + 关键词），会话感知 scope 解析，反漂移压缩
- **成长治理**：显式、可验证、可回滚的进化——每次能力变更都经过提案、评测、生效与审计
- **Skill 对象**：运行时经验层，沉淀可复用的任务知识，避免每次从零开始
- **Procedure 运行时**：确定性多步执行，支持中途校正、检查点与恢复
- **多 Agent 执行**：在同一 principal 下的受控 handoff，治理化上下文交换
- **多 Provider 模型**：OpenAI + Gemini，per-run 路由 + 原子预算门控
- **运维可靠性**：启动 preflight 检查、运行时诊断、结构化备份与恢复

## 当前状态

Phase 1（基础设施）已全部完成：会话连续性、持久记忆、模型迁移验证、Telegram 渠道、运维可靠性、开发治理，共 7 个里程碑。

Phase 2（显式成长与可验证进化）正在积极建设中：
- **P2-M1**（显式成长与 Builder 治理）：已完成——成长治理内核、Skill 对象运行时、Wrapper Tools、Growth Cases
- **P2-M2**（Procedure Runtime 与多 Agent 执行）：核心完成——Procedure 运行时、多 Agent handoff、ProcedureSpec 治理适配器
- **P2-M2d**（Memory Source Ledger Prep）：下一步——DB append-only 写入器、双写 + parity 检查
- **P2-M3**（Principal & Memory Safety）：规划中——WebChat 认证、canonical 用户身份、记忆可见性策略、shared-space 安全骨架

Phase 3 候选方向（尚未激活）：受治理的自我进化工作流。

## 技术栈

- **语言**：Python 3.12+ (async/await)
- **后端**：FastAPI + WebSocket
- **存储**：PostgreSQL 17 + pgvector
- **LLM**：OpenAI SDK、Gemini——per-run provider 路由
- **Embedding**：Ollama（优先）→ OpenAI（fallback）
- **工具链**：uv、pnpm（前端）、just、ruff、pytest

## 文档入口

- 设计文档入口：`design_docs/index.md`
- Phase 2 路线图：`design_docs/phase2/roadmap_milestones_v1.md`
- Phase 2 架构索引：`design_docs/phase2/index.md`
- 领域术语表：`design_docs/GLOSSARY.md`
- 模块边界：`design_docs/modules.md`
- 运行时 prompt 模型：`design_docs/system_prompt.md`
- 记忆架构：`design_docs/memory_architecture_v2.md`
- Procedure 运行时：`design_docs/procedure_runtime.md`
- Skill 对象：`design_docs/skill_objects_runtime.md`
- Phase 1 归档：`design_docs/phase1/index.md`
- 仓库治理：`AGENTS.md`、`CLAUDE.md`、`AGENTTEAMS.md`

## 说明

项目仍在持续迭代中。

随着产品方向进一步收敛，以及更多系统能力经过真实使用验证，名称、边界和实现细节都还可能继续调整。
