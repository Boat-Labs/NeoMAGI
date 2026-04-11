---
doc_id: 019d7d27-de10-7582-a7ba-77d9e43aac52
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-11T17:27:38+02:00
---
# 0060-memory-source-ledger-db-with-workspace-projections

- Status: accepted
- Date: 2026-04-11
- Amends: ADR 0053, ADR 0059, `design_docs/memory_architecture_v2.md`

## 背景

- 当前 memory 原则是：workspace memory files 是真源，PostgreSQL 是 retrieval / projection plane。
- 该原则避免了一个重要风险：不要把当前检索 schema 或 graph projection 误固化成长期 memory 本体。
- 但随着 PostgreSQL 已成为产品运行时 hard dependency，且 `SOUL`、sessions、growth governance 已经使用 DB 保存治理状态，继续把机器写入的 memory 真源放在 Markdown 文件中会带来新的复杂度：
  - provenance、scope、identity、security scan、redaction 等元数据需要通过文本协议承载；
  - direct file edits 会绕过授权、审计与一致性检查；
  - DB reindex 依赖 Markdown parser，长期会积累兼容债；
  - Shared Companion / consent-scoped memory 需要更强的来源、可见性与修正语义。
- Hermes Agent 的公开设计提供了相邻参考：它显式采用有状态存储承载 session history / search，同时保留本地 memory 文件作为可注入的 curated memory 表面（参考：https://hermes-agent.nousresearch.com/docs/user-guide/features/memory 与 https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage/）。这说明“无状态 agent + 有状态外部存储 + 可读文件投影”是合理方向，但 NeoMAGI 不照搬其具体存储切分。

## 选了什么

- 将 NeoMAGI 的机器写入 memory 真源，从 workspace Markdown 文件迁移为 PostgreSQL 中的 append-only source ledger。
- Workspace 中的 `memory/*.md` 与 `MEMORY.md` 改为人类可读、可导出、可重建的 projection / export surface，不再作为机器写入 memory 的最终裁决真源。
- DB source ledger 必须保持极薄，不定义完整 memory ontology：
  - 记录稳定 identity、provenance、scope、visibility、来源、正文与最小治理元数据；
  - 不把 thread、graph edge、ranking、embedding、summary cluster 等派生组织结构提升为真源；
  - retrieval 表、向量索引、graph projection、memory application 视图仍然是可重建 projection。
- 写入语义采用逻辑 append-only：
  - 普通新增、修正、撤回、争议标记、策展都追加事件或新版本；
  - 不用 in-place UPDATE 静默改写历史；
  - 隐私删除、合规删除和用户明确要求的 hard erase 是例外，必须保留可解释 tombstone 或审计记录，避免继续召回已删除内容。
- Projection 语义对齐 `SOUL.md`：
  - DB ledger 是 truth；
  - workspace Markdown 是运行时 / 人类阅读 / export projection；
  - projection 与 DB 不一致时，默认以 DB 为准重建 projection；
  - 若用户希望手工编辑文件，应通过显式 import / reconcile 命令进入 ledger，而不是让直接文件改动自动成为真源。
- `scope_key` 继续回答“谁可以检索到这条记忆”；`source_session_id` / principal / shared space metadata 只提供来源与审计信息，不得绕过 visibility filter。
- `MEMORY.md` 的最终形态分阶段处理：
  - 第一阶段继续作为 curated projection，保持 prompt 注入兼容；
  - 后续可评估是否把长期策展条目也纳入同一个 ledger；
  - 在未完成该评估前，禁止把 `MEMORY.md` 的手工编辑自动视为无审计真源。

## 为什么

- 这保留了原设计最重要的克制：memory application、thread、graph 和 retrieval 仍可演化，不把某一版检索实现固化为长期本体。
- DB append-only ledger 比 Markdown 元数据行更适合承载稳定身份、来源、作用域、授权、redaction、contested memory 和 shared-space visibility。
- NeoMAGI 已经要求 PostgreSQL 17，DB 不再是额外依赖；把机器写入真源放入 DB 可以复用事务、并发控制、schema 约束、审计与备份路径。
- 用户可读性仍由 workspace projection / export 保留；用户不能直接无审计改写 DB truth 也成为安全边界，能降低未授权篡改和 prompt injection 污染真源的风险。
- P2-M3 的 identity、membership、consent-scoped memory 需要更严谨的 provenance 与 visibility 语义；DB ledger 更适合作为这些策略的稳定承载层。
- 与 `SOUL` 的区别仍然清楚：
  - `SOUL` 是低频、高风险、版本化治理对象；
  - memory 是高频、事实型、隐私敏感对象；
  - 两者都可以采用 DB truth + workspace projection，但状态机和删除/修正语义不同。

## 放弃了什么

- 方案 A：继续保持 workspace Markdown 是机器写入 memory 真源。
  - 放弃原因：可读性强，但 provenance、scope、redaction、shared-space visibility 和 parser 兼容债会持续上升；直接文件编辑也容易绕过授权与审计。
- 方案 B：把 `memory_entries` 检索表直接升级为真源。
  - 放弃原因：会混淆 truth 与 retrieval projection；ranking、tokenization、embedding 和 search-specific 字段不应裁决事实。
- 方案 C：设计一套完整数据库 memory ontology / graph schema 作为真源。
  - 放弃原因：过早冻结上层记忆组织方式，违背 memory application 可演化原则。
- 方案 D：文件与数据库双主。
  - 放弃原因：冲突语义不闭合；一旦两边都可写，就需要复杂 reconcile，且无法稳定判断哪边代表授权事实。
- 方案 E：立即在当前 active milestone 中重写 memory 写入链路。
  - 放弃原因：会把数据迁移、检索重建、identity / visibility policy 与 P2-M3 产品语义绑成一个大变更，扩大风险。

## 影响

- 本 ADR supersedes 旧核心句 `Memory truth lives in workspace. Retrieval lives in PostgreSQL.` 中的 truth 位置。
- 新核心句为：

```text
Memory truth lives in an append-only user-owned PostgreSQL ledger.
Workspace files are readable projections and exports.
Retrieval and memory applications remain rebuildable projections above stable primitives.
```

- ADR 0053 中关于 `entry_id`、`scope_key`、`source_session_id` 的稳定身份与 provenance 要求继续保留，但这些字段后续应优先进入 DB source ledger；Markdown projection 可以渲染这些字段，但不再是最终裁决位置。
- ADR 0059 与 `design_docs/phase2/p2_m3_architecture.md` 中关于 relationship memory 的 workspace truth 表述需要在 P2-M3 计划更新时改为 DB ledger truth + workspace projection。
- 后续实现应按独立迁移切片推进：
  1. 新增 DB source ledger schema 与只追加写入 API，不改变现有读路径。
  2. 新写入先双写 DB ledger 与现有 daily note projection，并增加 parity / reconcile 检查。
  3. 将 `memory_entries` reindex 来源从 Markdown parser 切换为 DB ledger current view。
  4. 增加 `memory render/export/import/reconcile` 命令，确保用户可读 projection 可重建，显式 import 可审计。
  5. 稳定后关闭 Markdown 真源写入路径，并更新 `memory_architecture_v2.md`、P2 roadmap 与 P2-M3 architecture。
- 实现时机：
  - 决策现在生效，作为 P2-M3 之前的架构输入；
  - 不建议抢在 P2-M3 主线中直接完成全量迁移；
  - 推荐在 P2-M3 之前或 P2-M3a 期间完成 schema / writer 双写预备，在 P2-M3 identity / visibility policy 稳定后再切换 read / reindex truth。
