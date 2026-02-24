# 0037-workspace-path-single-source-of-truth-and-startup-validation

- Status: accepted
- Date: 2026-02-24

## 选了什么
- 将工作区路径统一为单一真源：`Settings.workspace_dir`。
- `MemorySettings.workspace_path` 在 M3 修复阶段保留兼容，但不再作为独立根目录语义：
  - 启动时必须校验 `memory.workspace_path == workspace_dir`；
  - 不一致时 fail-fast，拒绝启动并输出明确错误。
- 后续清理阶段将 `MemorySettings.workspace_path` 收敛为派生配置或移除，避免双路径并存。

## 为什么
- 当前写入、索引、读取由不同组件负责，双路径会造成“写 A 读 B”的隐性一致性故障。
- 路径作为基础设施级配置，应在启动即失败，不应在运行期隐式降级。
- 单一真源可减少排障复杂度，降低后续演进成本。

## 放弃了什么
- 方案 A：继续允许 `workspace_dir` 与 `memory.workspace_path` 独立配置。
  - 放弃原因：制造配置歧义，故障表象随机，测试难以覆盖。
- 方案 B：运行时自动容错（不一致时静默回退到某一路径）。
  - 放弃原因：会掩盖配置错误，导致数据写入位置不可预测。
- 方案 C：立即删除 `MemorySettings.workspace_path`（一次性破坏式变更）。
  - 放弃原因：兼容性风险较高，不适合本轮修复窗口。

## 影响
- `src/gateway/app.py` 启动流程需新增路径一致性校验。
- 相关构造点（Indexer/Writer/Curator）应统一消费 `workspace_dir` 语义。
- 测试需新增：
  - 路径一致时正常启动；
  - 路径不一致时抛出明确错误并阻断启动。
