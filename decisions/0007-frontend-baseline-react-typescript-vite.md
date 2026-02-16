# 0007-frontend-baseline-react-typescript-vite

- Status: accepted
- Date: 2026-02-16

## 选了什么
- WebChat 前端基线采用 `React + TypeScript + Vite`。

## 为什么
- 该组合在 AI 协作编码中的生成质量和可维护性表现最好。
- TypeScript 提升接口边界清晰度，降低前后端协作误差。
- Vite 启动和构建速度快，配置简洁，适合当前快速迭代阶段。

## 放弃了什么
- 方案 A：继续使用服务端模板渲染（Jinja/HTMX）作为主前端方案。
  - 放弃原因：复杂交互与组件复用能力不足，不利于后续扩展。
- 方案 B：选择 Next.js 作为首期框架。
  - 放弃原因：当前阶段功能边界明确，Next.js 引入的约束和复杂度偏高。

## 影响
- WebChat 渠道的前端实现默认按 SPA 路线推进。
- 后续前端组件、状态和路由设计以 TypeScript 类型边界为基线。
