# 0053-memory-entry-ids-and-projection-only-content-hashes

- Status: accepted
- Date: 2026-03-16
- Note: 本 ADR 只定义 memory 文件稳定身份与数据库 projection 的边界，不在本轮引入 multi-hop retrieval 的运行时实现。

## 背景

- 当前 memory 真源明确在 workspace 文件，而不是数据库；数据库承担检索、过滤、排序和重建加速层。[`design_docs/memory_architecture_v2.md`](../design_docs/memory_architecture_v2.md)
- 现有 daily note 形态是 `workspace/memory/YYYY-MM-DD.md` 作为“日级容器文件”，文件内通过 `---` 分隔多条 append-only entry，而不是“一文件一条记忆”。
- 现有写入、prompt 注入和 reindex 都依赖这个简单协议：
  - writer 直接向 daily note 追加一段 entry。
  - prompt builder 按 `---` 分隔并基于 `scope` 过滤条目。
  - indexer 按 `---` 分隔重建 `memory_entries` 检索面。
- 因此，若直接把 YAML frontmatter 放到 daily note 文件头，会与当前 `---` 语义发生冲突，并把文件头误解析成一条 memory entry。
- 同时，路径不是稳定身份：
  - 文件名是日期容器，不足以稳定标识具体记忆条目。
  - 未来若需要基于单条记忆做 recall、graph edge、curation evidence 或跨文件引用，需要路径无关的稳定对象 ID。
- 另一个目标是为未来数据库层的 graph-style retrieval 预留基础，但不把图谱真源放进数据库：
  - 节点和边可以从文件真源反复重建。
  - edge 允许被 curator/后处理修正，而不污染 memory 真相裁决层。

## 选了什么

- 采用“先 entry_id、后 graph projection”的最小演进路径。
- v1 只为 daily note 的单条 entry 引入稳定身份 `entry_id`，不强制为 daily note 容器文件引入 `doc_id`。
- `entry_id` 使用足够长且标准化的随机/时序 ID，默认采用 `UUIDv7`；不使用短截断 ID。
- `entry_id` 属于文件真源的一部分，应直接写入 daily note 的 entry 元数据行，而不是仅存在数据库中。
- daily note 继续保持 append-only 容器协议，不引入 YAML frontmatter；`entry_id` 作为现有元数据行的扩展字段写入，例如：

```md
---
[22:47] (entry_id: 0195d9d7-6f5e-7d9b-a2d3-8a4d4f3d2c11, source: user, scope: main)
用户喜欢蓝色。
```

- `content_sha256` 不写回 source markdown；它只作为数据库 projection / reindex state 的派生字段存在。
- 文件级 `content_sha256` 由 indexer 或同步任务基于源文件内容计算，并写入数据库中的 source-state / graph projection 表，用于：
  - 检测文件是否变更
  - 支持增量 reindex
  - 为未来节点/边重建提供一致性校验
- 未来若引入 `doc_nodes` / `doc_edges` 等 graph projection 表，默认遵循：
  - 文件仍是真源
  - DB 表只是 projection，可全量重建
  - graph retrieval 是 memory search 的后续增强，不替代现有 lexical retrieval 基线
- multi-hop retrieval 暂不在本 ADR 中落地；当前只为其预留稳定对象身份和 projection 边界。
- 所有未来的节点、边和 traversal 都必须保留 `scope_key` 过滤约束，不能绕过现有 memory scope 隔离。

## 为什么

- 记忆条目的稳定身份应落在“条目级”，而不是先落在“日期文件级”：
  - 当前 daily note 是容器文件，一天内有多条独立记忆。
  - 后续 recall、evidence、curation 和 graph edge 更自然地以单条 entry 为对象。
- `entry_id` 进入文件真源，才能在路径变化、重命名、重建数据库时仍保留稳定身份。
- 不采用 YAML frontmatter，是为了避免破坏现有 append-only 写入协议和 `---` 分隔约定，降低迁移成本。
- `content_sha256` 留在数据库 projection，而不写回源文件，可以避免两类问题：
  - 自引用冲突：若 hash 写回被 hash 的源内容，会使 hash 语义复杂化。
  - 写入复杂化：daily note 当前是纯追加写入；若文件头包含 hash，每次追加都需要回写文件头并处理并发与竞态。
- `UUIDv7` 兼顾低碰撞风险、标准生态和时间有序性，比短 ID 或手工编码更稳健。
- 先引入稳定身份，再逐步叠加 graph projection，是符合当前 memory kernel 边界的最小闭环：
  - 文件 schema 改动最小。
  - 数据库层可自由迭代、重建和纠错。
  - 不会把某一版检索或图谱结构误固化成 memory 本体。
- 延后 multi-hop retrieval，有助于先验证：
  - `entry_id` 写入与旧数据兼容
  - reindex 与 prompt 加载不被破坏
  - graph edge 的质量治理机制成立

## 放弃了什么

- 方案 A：直接给 daily note 文件头加 YAML frontmatter，并在其中维护 `id`、`refs`、`content_sha256`。
  - 放弃原因：会与当前 `---` 分隔协议冲突，并把 daily note 从 append-only 容器变成需要回写文件头的复杂结构。
- 方案 B：只给 daily note 容器文件加 `doc_id`，不做条目级 `entry_id`。
  - 放弃原因：无法稳定标识单条记忆对象，不利于后续 recall、evidence、graph edge 和精细化 curating。
- 方案 C：把 `content_sha256` 直接写回源文件。
  - 放弃原因：会引入自引用/回写复杂度，破坏当前简单写入模型；而 hash 本质上是派生状态，不是 memory 真源。
- 方案 D：立即实现 `doc_nodes` / `doc_edges` + multi-hop retrieval，并把 graph retrieval 提升为主检索路径。
  - 放弃原因：当前阶段目标是先建立稳定对象身份和可重建 projection 边界，不需要把 retrieval 策略升级与源格式升级绑成一次迁移。
- 方案 E：仅在数据库表中给每条检索记录分配对象 ID，不写入源文件。
  - 放弃原因：数据库重建后 ID 会漂移，不能满足“路径变化或重建后仍稳定引用同一条记忆”的目标。

## 影响

- daily note writer 后续需要为新写入条目生成并持久化 `entry_id`，同时保持旧格式读取兼容。
- prompt builder 与 indexer 后续需要支持解析扩展后的元数据行，但仍应兼容没有 `entry_id` 的历史条目。
- 未来增量 reindex / source-state 表应记录文件级派生状态，例如：
  - `source_path`
  - `byte_size`
  - `mtime_ns`
  - `content_sha256`
  - `last_indexed_at`
- 未来若新增 graph projection 表，节点 identity 应优先复用 `entry_id`，而不是依赖路径或数据库自增主键。
- graph edge 的生成与维护应遵守“宁缺毋滥”的 curator 策略，避免垃圾 edge 污染 retrieval 质量。
- multi-hop retrieval 的后续设计应建立在：
  - lexical seed retrieval
  - scope-safe expansion
  - depth / fanout 限制
  - edge quality / confidence / recency rerank
 之上，而不是直接无约束图遍历。
