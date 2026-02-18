# Project Progress README

用 `dev_docs/progress/project_progress.md`（Append-only）追踪“项目当前推进到什么阶段”。文件只做增量记录，不做历史改写。

## 记录要求

- `append-only`：只能在文件末尾追加，禁止修改或删除历史记录。
- 纠错也追加：若历史记录有误，新增一条 `Correction of <timestamp>` 说明修正，不回写旧条目。
- 触发时机：仅在阶段状态变化或关键里程碑完成时追加，避免流水账噪音。
- 每条必须可追溯：`Evidence` 至少包含一个可核对证据（commit、plan、decision、test 命令结果）。
- 保持极简：每个字段一句话，优先“结果与下一步”，不写长篇过程。
- 脱敏：禁止写入密钥、token、隐私原文。

## 统一格式（每次追加一条）

```md
## <YYYY-MM-DD HH:mm> (local) | <Milestone>
- Status: in_progress | done | blocked
- Done: <本次推进结果，一句话>
- Evidence: <commit/plan/decision/test，逗号分隔>
- Next: <下一步唯一重点>
- Risk: <无 | 一句话风险>
```

## 示范（仅示范格式，不是实际进展）

```md
## 2026-02-18 21:30 (local) | Mx.y
- Status: in_progress
- Done: 完成 X 模块最小闭环并通过基础测试
- Evidence: commit abc1234, dev_docs/plans/mx.y_xxx_2026-02-18.md, uv run pytest tests/test_x.py -v
- Next: 合并 Y 模块并补齐回归测试
- Risk: 无
```
