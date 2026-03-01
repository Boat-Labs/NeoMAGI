# Logs 目录说明

`dev_docs/logs/` 用于保存 milestone 级协作日志目录，但 M7 起协作控制三件套已经降级为 projection。

## Projection 文件

以下文件由 `uv run python scripts/devcoord/coord.py render` 生成，不再作为人工主写入口：

- `heartbeat_events.jsonl`
- `gate_state.md`
- `watchdog_status.md`

控制面 SSOT 在 repo 根 `.beads/`；若需要追加协作状态，必须先写 control plane，再 `render` 出这些文件。

## 可选人工日志

各 role 的经验性日志仍可保留为 `dev_docs/logs/{milestone}_{YYYY-MM-DD}/{role}.md`，但它们不是 gate / ACK / heartbeat / recovery 的真源。

## 路径命名

- 目录命名：`{milestone}_{YYYY-MM-DD}`
- role 经验日志文件命名：`{role}.md`
- 完整路径：`dev_docs/logs/{milestone}_{YYYY-MM-DD}/{role}.md`

## 命名约束

- 日期固定为 `YYYY-MM-DD`（本地时区）。
- `milestone`、`role` 使用小写英文与连字符（kebab-case）。

## 示例

- `dev_docs/logs/m7_2026-03-01/heartbeat_events.jsonl`
- `dev_docs/logs/m7_2026-03-01/gate_state.md`
- `dev_docs/logs/m7_2026-03-01/watchdog_status.md`
- `dev_docs/logs/m1.1_2026-02-17/backend.md`
- `dev_docs/logs/m1.1_2026-02-17/frontend.md`
- `dev_docs/logs/m1.1_2026-02-17/pm.md`
