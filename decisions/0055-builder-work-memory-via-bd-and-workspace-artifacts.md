# 0055-builder-work-memory-via-bd-and-workspace-artifacts

- Status: proposed
- Date: 2026-03-18
- Note: 本 ADR 只定义 `P2-M1c` builder work memory 的边界与真源分层；不在本轮引入新的 control-plane、产品数据库表或通用 builder runtime。

## 背景

- `P2-M1` 架构已明确要求：较长的 coding / builder 任务应留下结构化 work memory，而不是只留下对话痕迹。见 `design_docs/phase2/p2_m1_architecture.md`。
- ADR 0050 已将 `devcoord` 从 `beads` 解耦，`.devcoord/control.db` 是协作控制面的唯一真源；因此 `beads` 不能再承载 gate / ACK / heartbeat / closeout 等控制面语义。
- ADR 0052 已将 `beads` 的项目级远端恢复路径固定为 Git 跟踪的 JSONL backup，而不是 Dolt remote sync；这进一步说明 `beads` 在本项目中的定位应收敛为 issue tracking 与相关辅助层，而不是新的产品数据面。
- `P2-M1c` 需要一个最小可审计闭环来承载：
  - builder task brief
  - progress / blockers
  - validation summary
  - artifact refs
  - promote candidate refs
- 当前 workspace 已有 `workspace/memory` 作为对话与显式记忆真源；builder 过程产物不应混入这条真源。
- `dev_docs/` 的职责是治理、计划、review、progress 等项目文档，而不是 agent 运行时 artifact 真源。
- 但 `bd / beads` 的 issue metadata / comments 更适合作为索引层，不适合作为长文本、结构化快照和详细证据的 canonical store。

## 选了什么

- `P2-M1c` 采用 builder work memory 的双层表达：
  - `workspace/artifacts/` = canonical artifact record
  - `bd / beads` = task index、状态入口与 evidence pointer
- `workspace/memory/` 继续作为对话与显式记忆真源，不承载 builder 过程 artifact。
- builder work memory 的 canonical artifact 记录保存在 `workspace/artifacts/` 下，例如：
  - `workspace/artifacts/builder_runs/<run_id>.md`
  - `workspace/artifacts/growth_cases/<case_id>/<run_id>.md`
- `bd / beads` 在 `P2-M1c` 中只承担最小索引职责：
  - issue title / description
  - lightweight state
  - progress / blocker / validation comments
  - artifact refs
  - promote candidate 摘要
- `bd / beads` 明确不承担：
  - devcoord control-plane
  - product memory truth
  - builder 详细正文的唯一真源
- `dev_docs/` 明确不承担 agent 运行时 artifact 真源；它只保留治理、计划、review、progress 等项目文档。
- `P2-M1c` 不为 builder work memory 新增 PostgreSQL 表；builder work memory 不是产品运行时数据面。
- 若 `bd` 的 labels / state / comments 能力不足以承载最小索引层，固定 fallback 为：
  - `artifact-first`
  - `bead-pointer-only`

## 为什么

- 这延续了 ADR 0050 的边界：`beads` 可以承载真实任务与工作记忆索引，但不能回到控制面状态机。
- `workspace/memory` 与 builder artifact 应分层：
  - 前者是真实对话 / 显式记忆
  - 后者是过程证据 / 工作产物
- `workspace/artifacts/` 更适合长文本、结构化快照和运行时工作产物；`bd` 更适合 issue graph、依赖、状态和评论追加。
- 将 canonical artifact record 放在 `workspace/artifacts/`，可以避免把 bead metadata / comments 继续膨胀成新的高熵正文存储层，也能避免把过程证据误塞进 `dev_docs/`。
- 不新增 PostgreSQL 表，能保持 `P2-M1c` 复杂度聚焦在 growth case 与 promotion 闭环，而不是再造一层 builder 数据面。
- 先固定 `artifact-first + bead-index-only` 的方向，可以让后续 feasibility spike 只决定“索引承载多少”，而不是重新争论真源分层。

## 放弃了什么

- 方案 A：把 `bd / beads` 直接做成 builder work memory 的完整 canonical store。
  - 放弃原因：这会把长文本、结构化快照和详细证据压回 issue/comment 语义，长期可维护性差。
- 方案 B：重新让 `beads` 同时承担 builder work memory 与 devcoord control-plane。
  - 放弃原因：这与 ADR 0050 冲突，会再次混淆 backlog 语义和协议状态机语义。
- 方案 C：为 `P2-M1c` 新增 PostgreSQL builder work memory 表。
  - 放弃原因：builder work memory 在本轮的目标是证据层，不是产品运行时真源；新增数据面收益不足。
- 方案 D：在 `P2-M1c` 里再引入新的 builder memory service / adapter 层。
  - 放弃原因：会扩大实现面，不符合“先闭环证据，再做 runtime”的阶段目标。

## 影响

- `P2-M1c` 的 builder / case 实现必须同时产出：
  - bead issue / comment / state 索引
  - 可回链的 `workspace/artifacts/` 记录
- `P2-M1c` 的验收证据应能从 bead 直接跳到：
  - artifact
  - proposal / eval / apply / rollback refs
  - test / validation summary
- feasibility spike 仍然需要，但它的职责仅限于确认：
  - bead 索引层的最小可行表达
  - 是否需要退化到 `artifact-first + bead-pointer-only`
- 本 ADR 不固定：
  - 具体 label / state 枚举名字
  - 具体 artifact 模板字段
  - 具体 helper / script 入口
- “builder work memory” 在本 ADR 中是功能语义，不等同于物理写入 `workspace/memory/` 目录。
