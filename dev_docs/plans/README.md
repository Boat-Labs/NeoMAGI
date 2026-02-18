# Plans 目录说明

`dev_docs/plans/` 用于持久化保存计划文档（讨论草稿 + 经用户审批的最终版），作为项目长期记忆。

## 命名规则

- 草稿：`{milestone}_{目标简述}_{YYYY-MM-DD}_draft.md`
- 正稿：`{milestone}_{目标简述}_{YYYY-MM-DD}.md`
- 修订版正稿：在日期后追加 `_v2`、`_v3` 等后缀，不覆盖历史版本

## 命名示例

- `m1.2_gateway-connection-stability_2026-02-17.md`
- `m1.2_gateway-connection-stability_2026-02-17_v2.md`
- `m1.2_gateway-connection-stability_2026-02-17_draft.md`
- `m2.0_memory-hybrid-search_2026-03-01.md`

## 使用约定

- 讨论阶段只维护同一个 `_draft` 文件，反复覆盖更新；禁止把讨论轮次写成 `_v2`、`_v3`。
- 用户批准后，必须按正确正稿命名生成计划文件，并删除对应 `_draft` 文件。
- `_v2`、`_v3` 仅用于同一 scope 下“上一版已审批且已执行”后的再次获批修订，不用于未执行计划的讨论迭代。
- PM 重启后应优先读取本目录内最新版本 plan 继续执行。
