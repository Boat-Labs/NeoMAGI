# 记忆架构 v2（原则版）

> 状态：current  
> 日期：2026-03-04  
> 用途：作为 memory 设计的长期原则文档，替代早期偏 `P1-M2` / `P1-M3` 规划口径的 `design_docs/phase1/memory_architecture.md`

## 1. 核心判断
- Memory 的真源保持在 workspace，而不是数据库。
- PostgreSQL 主要承担检索、过滤、排序和派生加速，不承担“记忆事实裁决层”角色。
- 平台只提供稳定原子操作；更高层的记忆组织方式允许 agent 按用户场景演化。
- 人类定义 `memory kernel`。
- agent 在 `memory kernel` 之上组合、声明、演化 `memory applications`。
- workspace 持有原始材料与 spec。
- 数据库持有索引、缓存、派生结构和查询加速层。

核心句：

**Memory truth lives in workspace. Retrieval lives in PostgreSQL. Memory applications evolve above stable primitives.**

## 2. 为什么 memory 真源在 workspace
- 符合 personal agent 的工作区模型：记忆首先是可累积、可检查、可迁移的材料。
- daily notes 和 `MEMORY.md` 天然适合承载“原始沉淀 + 长期策展”。
- 检索层损坏时，可从文件真源重建，不会把索引误当真相。
- 允许未来脱离当前检索实现继续保留记忆资产。

## 3. 为什么数据库不是 memory 真源
- 数据库擅长索引、召回、过滤、排序和并发查询。
- 但 memory 在本项目中不是先验定义好的固定数据模型，而是会随用户任务逐步分化。
- 如果过早把数据库 schema 当作记忆真源，就会把“当前实现的检索模型”误固化成“长期记忆模型”。

## 4. 平台层与进化层的分工

### 4.1 Memory Kernel（平台固定层）
- workspace memory files
  - `workspace/memory/YYYY-MM-DD.md`
  - `workspace/MEMORY.md`
- memory specs
  - agent 可声明的 memory application spec/manifest
  - 这些 spec 与原始记忆材料同样保存在 workspace，而不是直接把 DB schema 当真源
- scope-aware 原子操作
  - append
  - search
  - recall
  - reindex
  - archive / curation
- 基础检索模板
  - lexical / BM25
  - hybrid search 作为通用初始模板
- 安全与运维约束
  - scope filtering
  - backup / restore
  - doctor / reindex

### 4.2 Memory Applications（agent 演化层）
- agent 可基于 daily notes、长期互动和用户目标，逐步形成领域化记忆组织方式。
- 这些记忆应用不是平台预写死的一套统一 schema。
- agent 优先演化 memory application 的声明与组合方式，而不是无约束地直接改造 memory kernel。
- 例如：
  - 金融分析用户：watchlist、thesis ledger、earnings memory、source credibility memory
  - 自媒体作者：style memory、topic backlog、argument bank、audience feedback memory
  - 软件项目用户：decision memory、bug pattern memory、review checklist memory

## 5. 长期原则
- 不预设唯一的“标准 memory schema”适用于所有用户。
- 先提供最少必要原子操作，再允许 agent 在其上构建更适配用户的 memory applications。
- agent 不应每次从零开始；应优先学习已有公开经验、案例和模式，再做本地适配。
- hybrid search 适合作为通用任务的默认模板，但不是长期唯一形态。

## 6. 关键边界
- 平台不应把某一版数据库结构误当作 memory 的永久本体。
- agent 可以演化记忆应用层，但不应随意破坏 memory kernel 的稳定契约。
- workspace 中保存的是记忆原始材料、长期沉淀以及 memory specs；数据库中保存的是索引、缓存、派生结构与查询加速层。
- `SOUL` 不属于此文档范畴：`SOUL` 是受治理对象，真源在 DB，`SOUL.md` 是 projection。

## 7. 对当前实现的含义
- `memory_entries` 继续视为检索数据面，而非真源。
- daily notes 与 `MEMORY.md` 继续视为记忆真源。
- reindex 是正常恢复手段，不是异常补丁。
- `P1-M5` 的 `doctor/preflight` 应显式区分：
  - memory truth: workspace
  - memory retrieval plane: PostgreSQL

## 8. 非目标
- 不追求由人类预先设计一套覆盖所有用户场景的统一记忆体系。
- 不允许 agent 无约束地直接演化底层存储内核。
- 不把当前某种检索实现等同于长期记忆本体。
