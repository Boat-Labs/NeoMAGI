# Logs 命名规则

`dev_docs/logs/` 用于保存各 agent 的 milestone 工作日志。

## 路径命名

- 目录命名：`{milestone}_{YYYY-MM-DD}`
- 文件命名：`{role}.md`
- 完整路径：`dev_docs/logs/{milestone}_{YYYY-MM-DD}/{role}.md`

## 命名约束

- 日期固定为 `YYYY-MM-DD`（本地时区）。
- `milestone`、`role` 使用小写英文与连字符（kebab-case）。

## 示例

- `dev_docs/logs/m1.1_2026-02-17/backend.md`
- `dev_docs/logs/m1.1_2026-02-17/frontend.md`
- `dev_docs/logs/m1.1_2026-02-17/pm.md`
