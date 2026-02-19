# 0023-roadmap-product-oriented-boundary

- Status: accepted
- Date: 2026-02-19

## 选了什么
- 将 roadmap 的主文档口径调整为“产品导向”：只定义目标、边界、验收和优先级，不包含实现级任务拆分。
- 技术实现细节统一下沉到 architecture 文档与 `decisions/`，roadmap 不做 micro management。

## 为什么
- roadmap 过度技术化会导致频繁改动和沟通成本上升，不利于阶段目标对齐和验收。
- 以 use case 为中心定义验收标准，更符合产品迭代节奏，也便于非实现角色参与评审。
- 将“产品路线图”和“技术方案”分层，可以降低文档职责重叠与维护熵增。

## 放弃了什么
- 方案 A：继续在 roadmap 中维护任务级实现细节（owner、due、代码项）。
  - 放弃原因：roadmap 与 architecture/plan 职责重叠，增加维护噪音与上下文切换成本。
- 方案 B：仅保留技术 roadmap，不维护产品向 roadmap。
  - 放弃原因：不利于非实现角色统一理解阶段目标与验收标准。

## 影响
- 后续 roadmap 评审应以“用户价值 + 验收用例”作为主检查项。
- 里程碑内的技术选型与实现路径，必须写入 architecture 文档或 ADR，不再堆叠到 roadmap。
- 进度跟踪继续沿用 `dev_docs/progress/project_progress.md`，并以当前 roadmap 生效版本作为阶段依据。
