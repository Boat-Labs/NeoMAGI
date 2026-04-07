---
doc_id: 019cc262-8b20-74d2-bc66-4a4ea19f3839
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-06T10:02:44+01:00
---
# P2-M3 Architecture（计划）

> 状态：planned  
> 对应里程碑：`P2-M3` 身份认证、用户连续性与记忆质量  
> 依据：`design_docs/phase2/roadmap_milestones_v1.md`、`design_docs/memory_architecture_v2.md`、ADR 0034、ADR 0044、ADR 0048、ADR 0059

## 1. 目标

- 为 WebChat 引入可验证身份，使“同一个已认证用户”的连续性具备产品基础。
- 固化 canonical `user / principal` 语义，并保留 `account_id` / `peer_id` 作为绑定证据。
- 在身份前提稳定后，建立受控的跨渠道连续性与跨 agent 上下文共享规则。
- 提升记忆检索质量，并让 memory applications 开始进入正式演化入口。
- 为 Shared Companion 建立最小 relationship/shared-space 语义：多个已认证 principal 可显式加入同一 shared space，并在 consent-scoped visibility 下共享关系记忆。

## 2. 当前基线（输入）

- WebChat 当前仍是匿名会话，默认 `main`。
- Telegram 已实现 `per-channel-peer`，但仅 Telegram 侧具备受控 identity 输入。
- `SessionIdentity` 已为 `peer_id` / `account_id` 预留字段，但尚未成为完整 principal / binding 模型。
- 记忆真源已明确在 workspace，DB 作为 retrieval plane。
- 检索质量已有基础能力，但 hybrid search、memory applications、跨渠道共享规则仍未正式落地。
- 当前没有 `shared_space_id` / membership / memory visibility policy；因此不能安全支持“同一个 NeoMAGI 同时作为多方共同朋友”的 Shared Companion 场景。

实现参考：
- `src/session/scope_resolver.py`
- `src/channels/telegram.py`
- `src/tools/builtins/memory_search.py`
- `design_docs/memory_architecture_v2.md`
- `decisions/0034-openclaw-dmscope-session-and-memory-scope-alignment.md`

## 3. 复杂度评估与建议拆分

`P2-M3` 复杂度：**很高**。  
原因：同时跨越 identity、session scope、channel binding、relationship shared-space、search quality、memory applications。

建议拆成 4 个内部子阶段，避免把跨渠道同一用户连续性和多 principal relationship space 混成一个实施包：

### P2-M3a：Auth & Principal Kernel
- WebChat 认证登录
- canonical principal
- binding 模型
- 为 shared-space membership 预留 principal-stable identity

### P2-M3b：User Continuity & Sharing Policy
- verified binding 之后的 per-user continuity
- 跨渠道 / 跨 agent 分享规则
- private memory 与 published summary 的基础 visibility 边界
- fail-closed 默认边界

### P2-M3c：Relationship Space & Consent-Scoped Memory
- `shared_space_id` / membership / consent-scoped visibility
- private memory 与 shared relationship memory 的硬隔离
- relationship lifecycle 最小语义
- Shared Companion threat model

### P2-M3d：Retrieval Quality & Memory Applications
- hybrid search
- recall quality eval
- memory application spec / manifest
- relationship memory application skeleton

## 4. 目标架构（高层）

### 4.1 Principal / Binding Plane

- 产品层目标语义是：
  - `per-user continuity`
- 实现层建议区分：
  - `principal_id`
  - `account_id`
  - `peer_id`
  - `channel_type`
  - `verified`
- Shared Companion 额外需要：
  - `shared_space_id`
  - `membership`
  - `visibility`
  - `provenance`
- 未验证 binding 不允许合并到同一用户连续体。
- 未验证 principal 不允许加入 shared space；未确认 membership 不允许读取 shared-space memory。

### 4.2 Auth Plane

- WebChat 不再只依赖匿名 `session_id`。
- 登录后 session 应绑定到可验证 principal。
- 匿名路径继续存在时，默认 fail-closed，不进入用户级连续性。

### 4.3 Continuity / Sharing Plane

- 跨渠道连续性不再表达为产品层的 `per-peer`。
- 最终产品语义是“同一个已认证用户”。
- 默认共享应保持保守：
  - 不默认跨渠道共享全部上下文
  - 不默认跨 agent 共享全部记忆
  - 只允许按 policy publish / merge
- 对 Shared Companion，sharing policy 必须显式区分：
  - `private_to_principal`
  - `shared_in_space`
  - `shareable_summary`
- 私聊中产生的内容默认是 private；除非用户明确发布或确认，否则不得进入 relationship shared memory。
- `shareable_summary` 是从私有内容派生出的可分享摘要，不等于公开原始 private memory；V1 至少要求来源 principal 明确确认，涉及共同事实时是否需要多方确认作为本阶段 open issue 处理。

### 4.4 Relationship Space Plane

- `relationship/shared space` 是 Shared Companion 的最小产品对象，不是 Slack channel 或 group chat 的同义词。
- shared space 至少需要表达：
  - 哪些 principal 是成员
  - 成员是否 verified / active
  - 哪些 memory entries 可在该 shared space 中召回
  - 哪些 summaries 是双方或多方确认可共享的
- NeoMAGI 在 shared space 中的建议目标不是帮助当前说话者赢得争论，而是改善共同关系、降低误解、明确边界与下一步沟通。
- 信息不对称时应 fail-safe：说明当前只有单方视角，建议邀请另一方补充，而不是使用未授权私有记忆暗中纠偏。
- NeoMAGI 在 shared space 中是受治理的 AI 社会角色 / 关系网络节点，不是无立场裁判，也不是某一方私有 agent 的隐形延伸。
- `SOUL` 在 shared context 中仍提供 NeoMAGI 的原则与风格基线；它不变成独立的“中立人格”，也不复制成多个 SOUL。私聊中形成的 rapport、个性化偏好或 private memory 不得自动成为 shared-space 行为依据。

### 4.4.1 Relationship Lifecycle Open Issues

- `join` / `leave`：成员加入和退出 shared space 后，哪些 shared memory 仍可见、哪些需要 freeze。
- `revoke` / `dissolve`：一方撤回授权或关系结束时，shared space 是否冻结、归档或进入只读历史模式。
- `contested memory`：一方认为某条 shared memory 不准确时，优先采用 append-only correction / dispute marker，而不是静默改写历史。
- `retention`：关系结束后 shared memory 的保留、导出、删除或 tombstone 策略必须可解释。

### 4.4.2 Shared Companion Threat Model

- relationship memory poisoning：一方故意写入偏置内容来影响 NeoMAGI 对另一方的建议。
- timing attack：在另一方咨询前密集写入 shared context 以扭曲建议。
- secrecy request：在 shared space 中要求 NeoMAGI “不要告诉对方我说了这个”。
- participation imbalance：一方频繁使用 shared space，另一方很少参与，导致建议视角偏移。
- contested facts：未经确认的单方叙述被误当成共同事实。

V1 应以 fail-safe 为默认：遇到上述风险时，优先标记单方视角、请求确认或暂停写入 shared memory，而不是继续自动沉淀。

### 4.5 Retrieval Quality Plane

- 检索路径建议从单一 lexical/BM25 升级为：
  - lexical
  - vector
  - hybrid template
- 记忆质量必须通过已知 miss case 回归评测，而不是只看主观体感。
- relationship-space retrieval 必须先做 visibility / membership filter，再做 lexical / vector / hybrid 检索；不得通过 graph expansion 绕过 scope。

### 4.6 Memory Applications Plane

- 在 memory kernel 之上允许更明确的 application spec 进入正式路径。
- 这层不改变：
  - workspace truth
  - DB retrieval plane
- 这层只增加：
  - 领域化组织方式
  - 作用域与共享策略
  - capability / skill 对 memory app 的可见性
- relationship memory 应作为 memory application 的候选，而不是 memory kernel 的硬编码 schema；其 source-of-truth 仍应是 workspace 中可重建、可审计、可解释的材料。

## 5. 边界

- In:
  - WebChat 认证登录。
  - principal / binding 模型。
  - verified continuity。
  - cross-channel / cross-agent sharing policy。
  - relationship/shared-space membership 与 consent-scoped memory visibility。
  - hybrid search 与 retrieval quality eval。
  - memory application 入口。
- Out:
  - 不在匿名 Web 路径上开放用户级连续性。
  - 不把未经验证 identity 直接合并。
  - 不做重型知识图谱工程。
  - 不默认全局共享全部上下文。
  - 不把某个 principal 的私有记忆隐式用于另一个 principal 的咨询。
  - 不把群聊 channel 当作 shared-space identity 或 memory policy 的真源。

## 6. 验收对齐（来自 roadmap）

- 在有身份与绑定前提时，同一用户可获得受控的跨渠道连续性。
- 在无身份前提时，系统继续正确拒绝危险共享。
- 系统能解释为什么某个渠道身份被视为同一个用户，或为什么没有被合并。
- 已知自然语句检索 miss 至少有一部分被稳定消除。
- 记忆共享范围始终可解释。
- 两个已认证 principal 可显式加入同一个 shared space；共同确认的 relationship memory 可在 shared context 中召回，任一方私有记忆不会被另一方隐式召回。
- 系统能解释一条关系记忆为什么可见：来源 principal、产生上下文、shared_space membership、visibility policy 与是否经过确认。
- Shared Companion demo 若发生在 `P2-M2`，只能验证 procedure / checkpoint，不得声称已具备真实关系记忆；产品级 demo 必须等 `P2-M3c` 的 shared-space identity 与 visibility policy 可用。
