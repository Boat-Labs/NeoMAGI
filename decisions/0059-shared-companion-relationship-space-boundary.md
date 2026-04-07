# 0059-shared-companion-relationship-space-boundary

- Status: accepted
- Date: 2026-04-07
- Related: ADR 0034, ADR 0047, ADR 0048, ADR 0053

## 选了什么

- 将 `Shared Companion` 定义为 NeoMAGI 的一个长期产品能力方向：同一个 agent 可以在明确授权的共享关系空间中服务多个已认证 principal，维护关系记忆，并给出对关系本身有建设性的建议。
- NeoMAGI 在这里不是“中立人格”或无立场裁判，而是一个受治理的 AI 社会角色：它仍有自己的 `SOUL`、长期原则和行为风格，但在 shared space 中承担关系网络节点 / 共同朋友式的建设性角色。
- `Shared Companion` 的核心对象不是“群聊”或“多人格 agent”，而是显式的 `relationship/shared space`：
  - `principal_id` 表示单个已认证用户。
  - `shared_space_id` 表示双方或多方明确加入的共享关系空间。
  - `membership` 表示哪些 principal 属于该 shared space，以及成员状态与角色。
  - `visibility` 表示 memory 是否为 `private_to_principal`、`shared_in_space` 或 `shareable_summary`。
- 关系记忆必须是 consent-scoped：默认不把某一方私聊记忆暴露给另一方；只有经明确授权或在 shared space 中产生的内容，才可进入 shared memory。
- 关系记忆仍遵循 memory kernel 原则：workspace 是真源，PostgreSQL 是 retrieval/projection plane；shared memory 是 memory application 层的组织方式，不把数据库 schema 提升为真源。
- `P2-M2` 只为该能力预留 procedure execution context 余量，例如 actor/principal/shared_space/visibility，而不在 Procedure Runtime Core 中实现关系记忆产品能力。
- `P2-M3` 承担 Shared Companion 的最小可验收语义：authenticated principals、shared space membership、consent-scoped memory visibility、scope-safe retrieval。
- `P2-M4` 承担 shared space 的外部协作表面，例如 Slack / 群聊 / channel adapter；这些表面不得绕过 `P2-M3` 的 identity 与 memory sharing policy。

## 为什么

- 现有主流 chatbot / agent 默认是孤立社交节点：它只代表当前说话者，很容易在亲密关系、家庭、团队等场景中放大单方视角，给出“保护当前用户但伤害关系”的建议。
- NeoMAGI 的 personal agent 方向不应退化成“帮当前用户赢得争论”；在明确授权的 shared space 中，它应能帮助多方降低误解、改善关系、形成可追溯的共同记忆。
- 这个方向为 NeoMAGI 增加了 AI 的社会角色：它不只是个人工具，也可以在 consent-scoped 边界内成为人类关系网络中的真实节点，帮助用户承载社交关系。
- 这不是模型能力问题，而是 runtime role 与 memory scope 问题：必须把“单方私有记忆”和“多方共享关系记忆”分开治理。
- 如果先从群聊或 Slack 表面切入，会把问题误解成渠道适配；真正前置条件是 principal、membership、consent、scope-safe retrieval。
- 如果直接把一方的私有 NeoMAGI 记忆用于另一方咨询，会形成隐性泄漏与偏置，违背当前 scope-aware memory 边界。

## 放弃了什么

- 方案 A：把 Shared Companion 放进 `P2-M2` 第一个 demo。
  - 放弃原因：`P2-M2a` 的任务是 Procedure Runtime Core；过早引入多方关系记忆会把 runtime、identity、consent 和 retrieval policy 混成一个大包。
- 方案 B：把 Shared Companion 等同于 Slack / 群聊。
  - 放弃原因：群聊只是表面；没有 identity 和 memory sharing policy，群聊会成为新的隐私泄漏面。
- 方案 C：让 NeoMAGI 在 B 咨询时直接使用 A 的私有记忆来“纠偏”。
  - 放弃原因：这会把 shared companion 变成私有信息泄漏通道，也会制造隐性偏置。
- 方案 D：把 Shared Companion 推迟到 Phase 3 才开始设计。
  - 放弃原因：`P2-M2` / `P2-M3` / `P2-M4` 正在冻结 runtime、identity、memory 和 channel 契约；若不现在预留 shared-space 语义，后续补救成本高。
- 方案 E：把多方视角实现成多个长期 SOUL / 多人格系统。
  - 放弃原因：这会与 ADR 0047 的单一用户利益 / 单一 SOUL 下受治理执行单元方向冲突；Shared Companion 需要的是多 principal 与共享关系空间，不是多自我。

## 影响

- `design_docs/phase2/p2_m2_architecture.md` 应明确：Procedure Runtime 的 execution context 不应永久等同于单 session / 单 principal；但 `P2-M2a` 不实现 shared relationship memory。
- `design_docs/phase2/p2_m3_architecture.md` 应把 `shared_space_id`、membership、visibility 与 consent-scoped memory 作为 identity / sharing policy 的设计余量。
- `design_docs/phase2/p2_m4_architecture.md` 应把群聊 / Slack 定位为 shared space 的表面之一，而不是 shared memory 的真源或 policy 决策点。
- 后续实现 memory application 时，应允许 relationship memory 作为一类应用，但必须保持 workspace truth、scope filtering、provenance 与 reindexability。

## 后续必须回答的问题

- `SOUL in shared context`：`SOUL` 仍是 NeoMAGI 的受治理原则与行为基线，不因 shared space 变成中立人格或多 SOUL；但 `P2-M3` 必须定义 shared context 中如何避免某一方私聊 rapport、偏好或私有记忆污染共同空间。
- `shareable_summary`：它是从私有内容派生出的可分享摘要，不等于公开原始 private memory；V1 至少要求来源 principal 明确确认，涉及共同事实时是否需要多方确认留给 `P2-M3` 设计。
- relationship lifecycle：shared space 需要 leave / revoke / freeze / archive / dissolve / contested-memory correction 语义；关系结束后 shared memory 的保留、召回和更正必须显式定义。
- threat model：必须覆盖 relationship memory poisoning、短时密集写入影响建议、"不要告诉对方" 请求、参与度不对称导致视角偏移、争议事实被误当共同事实等风险。
- demo fidelity：`P2-M2` 可做无持久 shared memory 的 procedure demo；产品级 Shared Companion demo 必须等 `P2-M3` 的 identity / membership / visibility policy 最小闭环可用。若提前做 seeded-context 展示，必须明确标注为 fixture，不得作为 runtime 验收。
