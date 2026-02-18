# Reviews 命名规则

`dev_docs/reviews/` 用于保存里程碑实现评审与阶段性审查结果。

## 文件命名

- 文件名：`{milestone}_{review-target}_{YYYY-MM-DD}.md`
- 修订版：在日期后追加 `_v2`、`_v3` 等后缀，不覆盖历史版本
- 推荐完整路径：`dev_docs/reviews/{milestone}_{review-target}_{YYYY-MM-DD}.md`

## 命名约束

- 日期固定为 `YYYY-MM-DD`（本地时区）。
- `milestone`、`review-target` 使用小写英文与连字符（kebab-case）。
- `review-target` 需能直接表达评审对象（如 `implementation-review`、`architecture-review`）。

## 示例

- `dev_docs/reviews/m1.1_implementation-review_2026-02-17.md`
- `dev_docs/reviews/m1.1_implementation-review_2026-02-17_v2.md`
- `dev_docs/reviews/m2.0_memory-hybrid-search-review_2026-03-01.md`

## 兼容说明

- 历史评审文件可保留原命名，不强制重命名；新文件按本规则执行。
