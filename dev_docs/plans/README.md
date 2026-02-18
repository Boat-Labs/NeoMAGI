# Plans 目录说明

`dev_docs/plans/` 用于持久化保存经用户审批后的最终版计划，作为项目长期记忆。

## 命名规则

- 文件名：`{milestone}_{目标简述}_{YYYY-MM-DD}.md`
- 修订版：在日期后追加 `_v2`、`_v3` 等后缀，不覆盖历史版本

## 命名示例

- `m1.2_gateway-connection-stability_2026-02-17.md`
- `m1.2_gateway-connection-stability_2026-02-17_v2.md`
- `m2.0_memory-hybrid-search_2026-03-01.md`

## 使用约定

- 仅保存用户已审批版本，不保存未审批草稿；计划变更并再次获批时，新增 `_v2`、`_v3` 文件，不覆盖历史。
- PM 重启后应优先读取本目录内最新版本 plan 继续执行。
