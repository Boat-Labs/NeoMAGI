# 0008-frontend-ui-system-tailwind-shadcn

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 前端 UI 体系采用 `Tailwind CSS + shadcn/ui`。

## 为什么
- `shadcn/ui` 组件质量高，且 AI 对其模式与最佳实践熟悉度高。
- Tailwind 能快速表达设计意图，减少自定义样式样板代码。
- 组合后可兼顾一致性与可定制性，适合快速试错与迭代。

## 放弃了什么
- 方案 A：纯手写 CSS + 自建组件库。
  - 放弃原因：首期投入过大，不利于快速交付可用界面。
- 方案 B：Material UI / Ant Design 作为默认 UI 体系。
  - 放弃原因：默认视觉风格约束较强，定制成本与产物体量更高。

## 影响
- 新增界面优先复用 `shadcn/ui` 组件并使用 Tailwind 原子类实现样式。
- 视觉规范与组件规范将围绕该体系逐步沉淀。
