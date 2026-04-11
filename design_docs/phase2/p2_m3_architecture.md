---
doc_id: 019cc262-8b20-74d2-bc66-4a4ea19f3839
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-06T10:02:44+01:00
---
# P2-M3 Architecture（计划）

> 状态：planned
> 对应里程碑：`P2-M3` Principal & Memory Safety
> 依据：`design_docs/phase2/roadmap_milestones_v1.md`、`design_docs/memory_architecture_v2.md`、ADR 0034、ADR 0044、ADR 0048、ADR 0059、ADR 0060、ADR 0061

## 1. 目标

- 为 WebChat 引入可验证身份，使“同一个已认证用户”的连续性具备产品基础。
- 固化 canonical `user / principal` 语义，并保留 `account_id` / `peer_id` 作为绑定证据。
- 在身份前提稳定后，建立受控的跨渠道连续性与跨 agent 上下文共享规则。
- 将 `P2-M2d` 的 DB append-only memory source ledger 接到 principal / visibility policy，并让 read / reindex truth 具备可切换路径。
- 修复 Phase 1 / Phase 2 已知的自然语言 memory miss case，形成轻量 retrieval regression。
- 为未来 Shared Companion 只保留安全地基：`shared_space_id` / membership / visibility 字段、默认拒绝策略与审计语义；不交付完整 Shared Companion 产品 demo。

## 2. 当前基线（输入）

- WebChat 当前仍是匿名会话，默认 `main`。
- Telegram 已实现 `per-channel-peer`，但仅 Telegram 侧具备受控 identity 输入。
- `SessionIdentity` 已为 `peer_id` / `account_id` 预留字段，但尚未成为完整 principal / binding 模型。
- ADR 0060 已将机器写入 memory truth 调整为 DB append-only source ledger；workspace memory 文件保留为 projection / export surface。
- `P2-M2d` 只负责 ledger prep；`P2-M3` 才把 identity / visibility policy 接入 memory read / reindex 语义。
- 检索质量已有基础能力，但缺少面向已知 miss case 的稳定回归。
- 当前没有可用的 `shared_space_id` / membership / consent policy；因此不能安全支持“同一个 NeoMAGI 同时作为多方共同朋友”的完整 Shared Companion 场景。

实现参考：
- `src/session/scope_resolver.py`
- `src/channels/telegram.py`
- `src/tools/builtins/memory_search.py`
- `design_docs/memory_architecture_v2.md`
- `decisions/0034-openclaw-dmscope-session-and-memory-scope-alignment.md`
- `decisions/0060-memory-source-ledger-db-with-workspace-projections.md`

## 3. 复杂度评估与建议拆分

`P2-M3` 复杂度：**高**。
原因：仍同时跨越 identity、session scope、channel binding、memory ledger/read path 与 retrieval quality；但 ADR 0061 已删除完整 Shared Companion、外部协作表面与 self-evolution workflow，避免在 P2 形成产品扩展大包。

建议拆成 3 个内部子阶段：

### P2-M3a：Auth & Principal Kernel
- WebChat 认证登录。
- canonical principal。
- binding 模型。
- 未验证 binding 默认不合并。
- 匿名路径继续可用，但不进入用户级连续性。

### P2-M3b：Memory Ledger & Visibility Policy
- 接收 `P2-M2d` 的 DB source ledger prep。
- ledger 中 `principal_id` / visibility 字段具备正式 policy 语义。
- `memory_append` / projection / read / reindex 路径可解释。
- private memory、published summary 与 future shared-space visibility 默认 fail-closed。

### P2-M3c：Retrieval Quality & Shared-Space Safety Skeleton
- 用已知 miss case 建立轻量 retrieval regression。
- 允许 lexical / vector / hybrid 的最小质量增强，但不做重型知识图谱工程。
- 保留 `shared_space_id` / membership / visibility 字段和 guard skeleton。
- 未完成确认语义时，所有 shared-space read / write 默认拒绝。

## 4. 目标架构（高层）

### 4.1 Principal / Binding Plane

- 产品层目标语义是 `per-user continuity`。
- 实现层建议区分：
  - `principal_id`
  - `account_id`
  - `peer_id`
  - `channel_type`
  - `verified`
- 未验证 binding 不允许合并到同一用户连续体。
- 任何 shared-space 相关操作都必须依赖 verified principal；P2-M3 不实现完整 membership lifecycle。

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
- P2-M3 只要求能解释基础 visibility：
  - `private_to_principal`
  - `shareable_summary`
  - reserved `shared_in_space`
- 私聊中产生的内容默认是 private；除非用户明确发布或确认，否则不得成为可跨上下文使用的 summary。
- `shared_in_space` 在 P2-M3 是 reserved / deny-by-default 状态，不作为可用产品能力。

### 4.4 Memory Ledger Plane

- DB append-only source ledger 是机器写入 memory truth。
- workspace memory 文件只作为 projection / export surface，不作为机器写入真源。
- `memory_entries` read / reindex 应能从 ledger current view 重建。
- projection 与 read path 切换必须可审计、可回滚，并能解释 source event、principal、visibility 与 projection 版本。

### 4.5 Shared-Space Safety Skeleton

- `relationship/shared space` 是未来 Shared Companion 的产品对象，不是 Slack channel 或 group chat 的同义词。
- P2-M3 只做安全地基：
  - 字段预留
  - membership skeleton
  - visibility enum / guard hook
  - 默认拒绝未确认 shared-space read / write
  - 审计解释中能说明为什么拒绝
- P2-M3 不定义完整 relationship lifecycle，不实现共同确认流程，不沉淀 relationship memory application。
- 信息不对称时必须 fail-safe：说明当前没有授权共享上下文，而不是使用未授权私有记忆暗中纠偏。

### 4.6 Retrieval Quality Plane

- 检索质量先服务已知 miss case，而不是追求通用语义搜索大改造。
- 可评估的增强路径包括：
  - lexical query normalization
  - vector retrieval
  - hybrid ranking template
- 所有 retrieval 都必须先通过 scope / principal / visibility filter，再进入 ranking。
- 不通过 graph expansion、memory app 或 summary projection 绕过 visibility policy。

### 4.7 Deferred Shared Companion Issues

以下议题保留为 P3+ 或独立计划，不在 P2-M3 交付：
- `join` / `leave` / `revoke` / `dissolve` lifecycle。
- contested memory、relationship memory correction 与 retention。
- 多方确认的 `shareable_summary` / `shared_in_space` 生效规则。
- relationship memory poisoning、timing attack、secrecy request、参与度不对称等完整 threat model。
- 完整 Shared Companion UX、产品 demo 与 relationship memory application。

## 5. 边界

- In:
  - WebChat 认证登录。
  - principal / binding 模型。
  - verified continuity。
  - cross-channel / cross-agent sharing policy。
  - DB source ledger current view 接入 read / reindex path。
  - 已知 retrieval miss case 的轻量回归与修复。
  - shared-space metadata / guard skeleton 与 deny-by-default policy。
- Out:
  - 不在匿名 Web 路径上开放用户级连续性。
  - 不把未经验证 identity 直接合并。
  - 不做重型知识图谱工程。
  - 不 onboard `memory_application_spec`。
  - 不默认全局共享全部上下文。
  - 不交付完整 Shared Companion 产品 demo、shared-space UX 或 relationship lifecycle。
  - 不把某个 principal 的私有记忆隐式用于另一个 principal 的咨询。
  - 不把群聊 channel 当作 shared-space identity 或 memory policy 的真源。

## 6. 验收对齐（来自 roadmap）

- 在有身份与绑定前提时，同一用户可获得受控的跨渠道连续性。
- 在无身份前提时，系统继续正确拒绝危险共享。
- 系统能解释为什么某个渠道身份被视为同一个用户，或为什么没有被合并。
- 已知自然语句检索 miss 至少有一部分被稳定消除，并进入 regression。
- 记忆共享范围始终可解释。
- `memory_entries` read / reindex 可从 DB ledger current view 重建，workspace projection 不是机器写入真源。
- `shared_space_id` / membership / visibility 字段存在并默认 fail-closed；未完成确认语义时不能读取或写入 shared-space memory。
- 系统能解释一条 shared-space 相关请求为什么被拒绝：principal、membership、visibility、confirmation 或 policy 缺失。
