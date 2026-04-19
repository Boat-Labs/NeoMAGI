---
doc_id: 019da594-dd6e-7afc-9168-228ebb5e4450
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-19T13:51:29+02:00
---
# 0064-artifact-and-run-metadata-boundary

- Status: accepted
- Date: 2026-04-19
- Related: `design_docs/phase3/p3_daily_use_architecture_draft.md`, ADR 0055

## 背景

P3 daily-use 会引入更多文件与执行产物：用户上传文件、网页抓取内容、搜索结果、PDF extraction、图片、plot、CLI logs、sandbox output、tool manifests。它们不能全部写入 memory，也不能只靠一个混杂文件夹管理。

现有 builder artifact 只覆盖 builder work memory，范围较窄；P3 daily artifacts 需要面向用户日常文件资产和工具执行 provenance，且要兼顾用户手动浏览与 agent 可追溯引用。

## 选了什么

- Workspace 文件系统分为：
  - `workspace/artifacts/`：长期可引用文件，默认保留；
  - `workspace/runs/<run_id>/`：一次工具执行过程，可后续清理。
- `artifacts/` 使用浅层类型目录：

```text
workspace/artifacts/
  uploads/{images,pdf,docs,data,other}
  web/{pages,search_results}
  generated/{images,plots,data,text,docs}
  logs/{cli,sandbox}
```

- `runs/` 使用固定过程目录：

```text
workspace/runs/<run_id>/
  input/
  output/
  logs/
  manifest.json
```

- Artifact metadata 使用独立 `artifacts` 表，不塞进 memory 表。
- Tool execution metadata 使用轻量 `tool_runs` 表。
- 文件内容保留在 workspace，DB 只保存 metadata、path、hash、provenance 和状态。
- Artifact canonical path 一旦写入 index，不轻易移动。
- Artifact 不自动写入长期 memory；memory 只记录“这个文件对用户长期有什么意义”。
- P3 初版 memory 可通过 `memory_source_ledger.metadata.artifact_ids` 引用 artifact；该 metadata 列已是 `JSONB NOT NULL DEFAULT '{}'`，不需要新增 ledger 列。
- P3-M1 需要打通受控写入路径与引用校验：写入 memory 前确认 artifact 存在，且 principal / visibility 允许引用。
- `artifact_ids` 初版只服务 memory -> artifact 引用，暂不新增 `memory_artifact_links` 表。
- `sha256` 在 artifact 写入或 finalize 时记录，用于 doctor / explicit integrity check；普通 artifact 读取默认不重新 hash。
- Artifact integrity drift 由 doctor 报告，修复或接受当前 hash 必须通过独立显式命令完成。
- 既有 builder artifact 不在 P3-M1 强制迁移；未来可登记进 P3 `artifacts` 表，并以 `metadata.origin = "builder"` 或等价字段标记。
- ADR 0055 的 builder work memory 语义继续有效；P3 `artifacts` 表是更宽的 daily-use artifact index，不把 builder work memory 替换成另一套语义。

## 为什么

- Artifact 与 memory 生命周期不同：artifact 是文件资产，memory 是长期语义。
- Artifact 与 run 生命周期也不同：artifact 默认保留，run 目录可清理。
- 单独表能支持稳定引用、文件完整性检查、来源追溯、前端展示和后续清理策略。
- 浅层类型目录符合用户手动找文件的习惯，也避免过早建设复杂知识分类。
- 不把 artifact 自动写入 memory 可以避免临时文件、日志、网页抓取内容污染长期记忆。

## 放弃了什么

- 方案 A：只按文件系统目录管理 artifacts，不建 DB metadata。
  - 放弃原因：缺少稳定 id、provenance、hash、visibility、前端查询与清理状态。
- 方案 B：把 artifact metadata 塞进 `memory_entries` 或 `memory_source_ledger`。
  - 放弃原因：混淆文件资产和长期 memory 语义，会污染 recall。
- 方案 C：只按日期目录组织 artifacts。
  - 放弃原因：用户手动浏览时难以按类型查找；文件来源和用途不清晰。
- 方案 D：立即建设完整 artifact graph / link table。
  - 放弃原因：P3 初版只需要稳定引用；复杂关系可以等真实反查需求出现后再加。

## 影响

- 需要新增 `artifacts` 表，至少记录 `artifact_id`、`kind`、`bucket`、`path`、`mime_type`、`size_bytes`、`sha256`、`summary`、`source_run_id`、`source_session_id`、`principal_id`、`visibility`、`origin_url`、`status`、`integrity_status`、`last_verified_at`、`metadata`。
- 需要新增 `tool_runs` 表，记录 `run_id`、`tool_name`、`status`、`session_id`、`principal_id`、`cwd`、`started_at`、`finished_at`、`timeout_sec`、`exit_code`、`log_path`、`stdout_tail`、`stderr_tail`、`summary`、`metadata`。
- `artifacts.source_run_id` 与 `tool_runs.run_id` 初版可做逻辑关联，不强制外键。
- 前端 artifact 展示、上传、tool log、long-running status 应以这些表和目录为基础。
- P3 应新增 doctor artifact integrity check：默认做 path / metadata / optional sampling，`doctor --deep` 才做全量 retained artifact hash 校验。
- P3 artifact store 与既有 builder artifact 并行过渡；新增 daily-use artifact 进入新表，已有 builder 产物按需要逐步登记，不做一次性迁移。
- 后续如需要频繁反查 artifact -> memory 关系，再评估 `memory_artifact_links`。
