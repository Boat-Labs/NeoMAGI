# P2-M1 Post Works 总览（草案）

- Date: 2026-04-06
- Status: draft
- Scope: `P2-M1` closeout 后、`P2-M2` 开工前的一组窄范围 enablement works；作为总览索引，不直接承担实现细节
- Basis:
  - [`design_docs/phase2/roadmap_milestones_v1.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/roadmap_milestones_v1.md)
  - [`design_docs/phase2/p2_m2_architecture.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/design_docs/phase2/p2_m2_architecture.md)
  - [`dev_docs/plans/phase2/p2-m1c_growth-cases-capability-promotion_2026-03-18.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1c_growth-cases-capability-promotion_2026-03-18.md)

## Why This Tranche Exists

`P2-M1` 已经交付 growth governance、`skill_spec` runtime 与 `wrapper_tool` onboarding，但在进入 `P2-M2` 前，还存在 3 个会直接干扰后续实现与验收的缺口：

1. WebChat 还没有真实可用的 multi-session UX。
2. tool runtime 还缺少轻量级的只读并发调度。
3. coding atomic tools 仍不完整，后续 coding capability 测试缺少最小操作面。

这 3 件事都不是 `Procedure Runtime` 本体，但如果不先拆掉，`P2-M2` 很容易被混成“同时补 UI、补工具面、补调度语义”的大包。

## Split Plan

本轮 post-works 已拆成 3 份独立计划，便于 Claude Code 按单一工作面实现与验收：

1. [`p2-m1-post-works-p1_multi-session-threads_2026-04-06_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p1_multi-session-threads_2026-04-06_draft.md)
2. [`p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06_draft.md)
3. [`p2-m1-post-works-p3_atomic-coding-tools_2026-04-06_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p3_atomic-coding-tools_2026-04-06_draft.md)

## Resolved Decisions

### D1. Multi-session UI 方向

采用你确认的 Codex app 风格“左侧 `threads` rail”作为 V1 方向，但只取其最小有用部分：

- 需要：
  - `New Thread`
  - thread 列表
  - 当前 thread 高亮
  - running / done / unread completion 状态
- 不需要：
  - 文件夹
  - plugins / automations / settings 菜单
  - 完整聊天产品级会话中心

### D2. Tool concurrency 元数据

不只增加 `is_read_only`，而是同时保留：

- `is_read_only`
- `is_concurrency_safe`

两者都采用 fail-closed 默认值。
只有同时声明为 `True` 的工具，才允许在同一 turn 内进入自动并行。

### D3. Atomic tools 落地顺序

按三层落地：

1. `glob` / `grep`
2. `write_file` / `edit_file`
3. `bash`

其中第三层 `bash` 当前建议**不作为第一轮硬验收项**，而是保留为 `P3` 内的条件性 Stage C。
理由：

- 它的风险与实现复杂度明显高于前两层。
- 当前 coding mode 入口仍未冻结，过早把 `bash` 纳入硬 scope 容易扩大 blast radius。
- 先完成 repo inspection 与 deterministic file mutation，已经足以支撑一轮更干净的 coding capability 验收。

## Recommended Order

1. `P1` multi-session threads
2. `P2` tool concurrency metadata
3. `P3` atomic coding tools

其中：

- `P1` 与 `P2` 可以并行开发
- `P3` 最好在 `P2` 之后衔接，这样 `glob` / `grep` 一上线就能受益于并发调度

## Non-Goals For This Tranche

- 不提前实现 `Procedure Runtime`
- 不做 server-synced session center
- 不做通用 tool DAG scheduler
- 不默认向 `chat_safe` 路径开放高风险 coding tools

## Handoff Note

后续如果让 Claude Code 按单个工作包推进，建议一次只拿一份计划：

- 做 `P1` 时，不顺手碰 tool runtime
- 做 `P2` 时，不顺手补 atomic tools
- 做 `P3` 时，先完成 `P3-Stage A/B`，再单独判断是否放开 `bash`
