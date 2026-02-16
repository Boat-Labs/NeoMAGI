# 0009-frontend-state-management-zustand

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 前端状态管理采用 `zustand`。

## 为什么
- API 简洁、样板代码少，适合当前项目规模与迭代节奏。
- 与 React 心智模型兼容度高，便于 AI 生成后快速人工校正。
- 可逐步按领域拆分 store，避免过早引入重型状态框架。

## 放弃了什么
- 方案 A：Redux Toolkit 作为默认状态管理。
  - 放弃原因：模板和样板代码更多，当前阶段收益不明显。
- 方案 B：仅使用 React Context 管理所有全局状态。
  - 放弃原因：随着功能增长容易导致状态耦合与性能问题。

## 影响
- 全局共享状态默认通过 `zustand` store 管理。
- 状态边界和更新逻辑以“小而清晰的 store”方式组织。
