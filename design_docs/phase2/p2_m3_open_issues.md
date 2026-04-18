---
doc_id: 019da228-7e90-79ef-b974-bb76b7623de4
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-18T21:54:16+02:00
---
# P2-M3 Open Issues

> 说明：本文件记录 P2-M3 用户测试中发现的设计/架构层面 root cause，避免问题背景散落在聊天记录中。

## OI-M3-01 Jieba 分词器对非 CJK 语言完全失效，需语言自适应策略

- 发现于：P2-M3 用户测试期间（手动 ParadeDB 分词器对比实验）
- 现象：中德混排文本在当前 Jieba 分词下完全无法正确切分；中英混排存在空 token
- 当前实现：
  - 写入时：`query_processor.segment_for_index()` 固定调用 `jieba.cut_for_search()`，结果存入 `search_text` 列
  - DB trigger：`to_tsvector('simple', COALESCE(search_text, content))` → `search_vector`
  - 查询时：`query_processor.normalize_query()` 同样固定调用 `jieba.cut_for_search()`，再通过 `plainto_tsquery('simple', ...)` 匹配
  - 不可配置，无 fallback
- 用户实测分词器对比（ParadeDB tokenizer）：

  | 场景 | Jieba | ICU | chinese_lindera |
  |------|-------|-----|-----------------|
  | 长中文文本 | 非常好 | 不行 | 不行 |
  | 中英混排 | 有空 token | 非常好 | 可以 |
  | 中英专业术语密集 | — | 非常好 | — |
  | 中德混排 | 完全不行 | 非常好 | — |
  | 无空格中英紧挨混排 | 非常好 | 非常好 | — |

- root cause：
  - Jieba 是纯中文词典分词器，不识别德语等非 CJK 语言，会把德语单词按字符错切
  - ICU 是 Unicode-aware 通用分词，天然处理多语种 word boundary，但对中文缺乏词典级精度
  - 单一分词器无法同时覆盖"纯中文长文本精度"和"多语种混排兼容性"
- 影响：用户日常使用中德英三语混排，当前方案在非 CJK 语言场景下 memory search 可靠性不足
- 可选修复方向（未排序）：
  - A. 语言检测 + 分词器路由：检测文本主要语言，CJK-heavy 用 Jieba，multilingual 用 ICU
  - B. 双路索引：Jieba + ICU 各建一份 search_text，查询时合并结果
  - C. 迁移到 ParadeDB pg_search BM25 后利用其原生 ICU tokenizer 配置
  - D. 混合方案：CJK 部分用 Jieba 切分，非 CJK 部分保留原文交给 ICU/simple
- 涉及代码：`src/memory/query_processor.py`、migration `b2c3d4e5f6a7`
- 优先级：非阻塞（当前中文场景可用），但影响多语种用户体验
