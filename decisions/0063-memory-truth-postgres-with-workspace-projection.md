---
doc_id: 019da594-dd6e-723b-937d-d4c0c1578d14
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-19T13:51:29+02:00
---
# 0063-memory-truth-closure-postgres-with-workspace-projection

- Status: accepted
- Date: 2026-04-19
- Refines: ADR 0060
- Related: `design_docs/phase3/p3_daily_use_architecture_draft.md`

## 背景

ADR 0060 已将机器写入 memory truth 从 workspace Markdown 调整为 DB append-only source ledger，并把 workspace 文件定位为 projection / export surface。当前代码已经进入 ledger-wired production path：`memory_append` 先写 Postgres ledger，workspace Markdown 是 best-effort projection，projection 失败不回滚 DB。

因此，P3 不再把 memory truth 当作待迁移事项。P3 的职责是把 projection / export 边界补硬：标注、重建、检查和文档清理，而不是继续建设 DB / Markdown 双主同步或长期双写迁移系统。

## 选了什么

- Postgres memory ledger 已经是生产 daily path 中机器写入 memory 的唯一真源。
- Workspace Markdown memory 文件是 human-readable projection / export，不是写入真源。
- `memory_append` 写入顺序为：
  1. 先写 DB；
  2. DB 成功后同步 append 到 Markdown projection；
  3. projection 失败不回滚 DB。
- `memory_entries` 是 retrieval projection；增量索引由 ledger write 驱动。
- Markdown projection 可以从 DB 重建。
- Projection 文件必须标注：`This file is auto-generated. Manual edits will be lost.`
- P3 不做 memory truth 迁移，不做 DB 与 Markdown 双主同步。
- no-ledger writer fallback 只能作为 legacy / test-only 路径；daily profile 必须使用 ledger-wired writer。
- 手工文件编辑如需进入 memory truth，必须通过显式 import / reconcile 命令，不自动生效。

## 为什么

- 单一真源已经降低了 P3 daily-use 的实现和排障成本；继续把它写成“待迁移”会误导后续计划。
- DB 更适合承载 stable id、scope、principal、visibility、provenance、metadata 和审计。
- Workspace 文件的核心价值是可读、可导出、可重建，而不是承载并发写入和授权裁决。
- 文件与 DB 双主会引入复杂 reconcile 语义；P3 当前更需要稳定使用，而不是双向同步系统。
- Projection 失败不影响 DB truth，能避免用户可读面的问题破坏记忆写入闭环。

## 放弃了什么

- 方案 A：长期保持 DB 与 Markdown 双写双主。
  - 放弃原因：冲突语义不闭合，且会让用户直接文件编辑绕过授权和审计。
- 方案 B：DB 写入失败时仍写 Markdown。
  - 放弃原因：会重新制造文件真源，破坏单一 truth。
- 方案 C：把 P3 继续定义为 memory truth migration。
  - 放弃原因：truth path 已经收口；P3 剩余价值在 projection / export hardening。
- 方案 D：Markdown projection 失败时回滚 DB。
  - 放弃原因：projection 是可重建展示面，不应阻断真源写入。
- 方案 E：立即迁移所有历史 Markdown memory。
  - 放弃原因：P3 当前重点是 daily-use 新写入闭环；历史迁移会扩大范围，除非真实使用证明需要。

## 影响

- `memory_append` 与后续 memory writer 继续以 DB 写入成功作为 truth 判定。
- P3 需要补齐 projection / export hardening：
  - Workspace memory 文件必须被文案和文件头标记为 auto-generated projection。
  - 提供手动 projection rebuild / reconcile 命令，从 ledger 重建 workspace Markdown。
  - doctor / parity check 只报告 drift，不自动修复；必要时补 checksum 输出。
  - 修正文档中仍把 workspace Markdown 写成 truth 的旧表述。
- `memory_entries`、embedding、search index、workspace Markdown 都应视为可重建 projection，不应裁决 memory truth。
- ADR 0060 中的 dual-write prep 口径在 P3 中已经收敛为 DB truth + best-effort projection，而不是长期迁移状态。
