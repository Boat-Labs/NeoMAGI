# Runtime Casebook

> 记录规范与模板见 [README.md](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/cases/README.md)。

## RC-2026-02-25-001 | memory_search 自然语句未命中（数据已存在）

- Date: 2026-02-25 12:06 CET
- Status: deferred
- Severity: P2
- Milestone: M3（post-review closure 后运行验证）
- Scenario: 重启后验证“记忆持续性搜索”时，用户看见 `memory_search` 工具被调用，但 assistant 返回“未找到相关记录”。

### Reproduction

1. 在 WebChat 通过 `memory_append` 写入偏好：`用户希望我在回答时，先给结论，再给 3 条要点。`
2. 重启后端（`just dev` 重新启动）。
3. 发送：`请搜索我之前记录的回答偏好。`
4. 观察到工具入参 query 为：`我希望你先给结论，再给 3 条要点`，assistant 回复“未找到相关记录”。

### Expected

assistant 能检索并引用已写入偏好，回答中体现“先给结论，再给 3 条要点”。

### Actual

assistant 报告未找到；但底层数据中该偏好记录实际存在。

### Evidence

- DB 中有记录：

```sql
select count(*) from neomagi.memory_entries;
-- 1
```

```sql
select scope_key, source_type, source_path, content
from neomagi.memory_entries
order by id desc
limit 1;
-- scope_key=main, source_type=daily_note, source_path=memory/2026-02-25.md
-- content=用户希望我在回答时，先给结论，再给 3 条要点。
```

- 词法查询结果对比：

```sql
-- 与工具入参近似的整句查询
select id
from neomagi.memory_entries, plainto_tsquery('simple', '我希望你先给结论，再给 3 条要点') as query
where scope_key='main' and search_vector @@ query;
-- 0 rows
```

```sql
-- 关键词查询
select id, ts_rank(search_vector, query) as score
from neomagi.memory_entries, plainto_tsquery('simple', '先给结论') as query
where scope_key='main' and search_vector @@ query;
-- 命中 1 行（score ~ 0.24）
```

### Analysis

- 该 case 主要是检索策略边界，不是数据丢失。
- 当前实现为单路词法检索（`tsvector + plainto_tsquery('simple')`），尚未启用混合检索（BM25 + vector）。
- query 语义等价但词形不一致时，中文场景存在 miss 风险。

### Decision

- 暂不立即修复，纳入后续“检索能力整体优化”统一处理（deferred）。
- 当前建议测试输入采用关键词式 query（如 `先给结论 3条要点`）以验证数据持续性。

### Follow-up

- 候选改进方向（后续统一评估）：
  1. query 重写与 0 结果降级重试（去虚词、关键词 fallback）。
  2. recall 阈值参数回顾（`memory_recall_min_score`）。
  3. 引入混合检索路径（词法 + 向量）并制定评估集。
- Owner: TBD（建议在 M6 验证阶段前确定）
