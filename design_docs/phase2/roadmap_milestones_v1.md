---
doc_id: 019cc914-bf10-7012-b4e0-47ab95f0fb1e
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-07T17:15:06+01:00
---
# NeoMAGI Phase 2 路线图 v1（Product-Oriented）

> 状态：draft
> 日期：2026-03-05
> 基于：`design_docs/phase1/roadmap_milestones_v3.md`、`design_docs/procedure_runtime.md`、`design_docs/memory_architecture_v2.md`

## 1. 这份 roadmap 管什么，不管什么

### 管什么（产品层）
- Phase 2 的产品目标与阶段主题。
- 每个阶段对用户真正产生的价值。
- 阶段边界：做什么 / 不做什么。
- 验收标准与推荐顺序。

### 不管什么（实现层）
- 不写 task 拆分、owner、工期和代码级步骤。
- 不规定具体 schema、类设计、脚本入口和测试文件名。
- 不把 architecture / plan / ADR 细节堆回 roadmap。

---

## 2. 为什么需要 Phase 2

Phase 1 已经基本完成基础闭环建设：
- 交互、任务闭环、稳定性、测试与 CI 基线；
- 会话内连续性；
- 会话外持久记忆；
- 模型迁移验证；
- 多代理开发治理；
- 第二渠道接入；
- 运营可靠性。

Phase 2 不再以“补基础设施空白”为主，而转向一个更明确的新主题：

**让 NeoMAGI 从“已有基础能力的 agent harness”进入“可显式成长、可验证进化、可协调多 agent 执行”的产品阶段。**

一句话说：

**Phase 1 解决基础闭环，Phase 2 解决显式成长。**

---

## 2.1 Phase 2 继承的不可退让约束

以下约束直接继承自 Phase 1，不在 Phase 2 重新讨论：

- 用户利益优先：任何成长与自我进化不得偏离“代表用户信息利益”。
- 自我进化必须可验证、可回滚、可审计：没有 eval / rollback / audit，不算有效进化。
- 高风险路径保持 fail-closed。
- 会话隔离与记忆召回继续同源治理，不允许 scope 泄漏。
- Memory truth 继续在 workspace；数据库继续承担 retrieval plane，而不是 memory truth。
- 能力扩展优先走稳定原子能力与受治理的组合层，不走无边界自发膨胀。

---

## 2.2 从 Phase 1 带入 Phase 2 的真实遗留

Phase 2 的起点不是“重新发明 Phase 1”，而是承接以下几个已明确存在的遗留问题：

- `coding / builder` 能力还没有成为清晰的产品能力，Phase 1 只完成了受控执行边界的基础准备。
- 原子工具覆盖仍不完整；当前不少能力增长仍容易退化为一次性脚本、prompt 拼装或临时 wrapper，而不是稳定可复用的能力沉淀。
- `skill object` 这一层尚未进入正式 runtime：系统还缺少一个可学习、可复用、可交换、可插拔的经验对象层来承载“不要总从 0 开始”的能力增长。
- `beads` 目前主要承担 issue 与协作日志职责，还没有充分升级为面向编程/构建任务的结构化工作记忆层。
- `Procedure Runtime` 仍处于 draft；高约束、多步骤、跨 turn 的流程还没有稳定 runtime contract。
- 用户级连续性仍受 Web 认证与跨渠道身份绑定前置条件阻塞；现阶段 `per-peer` / `account_id` 仍只是实现侧中间语义，尚未收敛为稳定的产品口径。
- 记忆命中率与 hybrid search 仍有明确提升空间。
- 多 agent 协作虽然在开发治理层已经成立，但在产品运行时层还缺少稳定的 steering、handoff 和 context exchange 契约。
- Shared Companion 需求要求 NeoMAGI 不只是孤立个人助手，还能在明确授权的关系空间中成为共同社交节点；这需要 relationship/shared-space identity、consent-scoped memory 与协作表面分阶段落地，而不能直接等同于群聊或多人格 agent。

这些遗留问题将直接决定 Phase 2 的里程碑顺序。

---

## 3. 里程碑（产品视角）

### P2-M1：显式成长与 Builder 治理
**用户价值**
- 用户不只是获得一个“会说话、会记忆”的 agent，而是获得一个能在边界内持续增长能力、且每次成长都可解释、可验证、可回滚的系统。

**关系说明**
- 产品层对外暴露的是稳定 capability；内部真实演化对象则应优先是 `skill object`，用于承载可学习、可复用的任务经验。
- 新经验默认先沉淀到 `skill object`，而不是直接把所有能力增长都下沉成 atomic tool；只有足够稳定、清晰、跨场景复用的部分才继续 promote。
- `skill object` 默认只拥有激活、建议与升级能力，不直接替代 atomic tools 或 procedure runtime。

**边界**
- In:
  - 明确 NeoMAGI 的“显式自我进化”对象：哪些东西允许成长，哪些东西不允许直接漂移。
  - 将 `coding / builder` 能力产品化为受治理能力，而不是停留在基础工具权限预留。
  - 补全自我进化所需的最小原子能力基座，明确哪些高频能力必须先沉淀为稳定原子工具 / wrapper tool，才允许进入上层 builder、procedure 或 memory application 演化。
  - 建立 `skill object` 这一层，作为 atomic tools 与 governance / runtime 之间的运行时经验层，用于承载“不要总从 0 开始”的可学习 delta。
  - 深化 `beads` 系统，使其从“只有标题级信息的 issue / 日志载体”升级为服务编程任务的工作记忆层，能承载任务状态、决策、todo、验证结果与关键产物索引。
  - 把“独立编码自我成长测试案例”纳入正式验收口径，而不是零散实验。
  - 深化 `SOUL` 初始化与成长原则，但其位置从属于“显式进化治理”，不单独膨胀成新的独立体系。
  - 明确 NeoMAGI 与 OpenClaw 风格的 implicit self-evolution 的区别：NeoMAGI 优先显式产物、显式评测、显式生效与显式回滚。
- Out:
  - 不允许无评测的静默自我修改直接生效。
  - 不把“会写代码”直接等同于“可无限自我改造”。
  - 不在本阶段交付无审批的外部账号代发能力。

**验收标准（Use Case）**
- 用例 A：agent 能说明一次能力成长“改了什么、为什么改、怎么验证、如何回滚”。
- 用例 B：在一次较长的编程/构建任务中，agent 能把中间状态沉淀到 `beads` 工作记忆，而不是只留下 issue 标题或散落在对话里的描述。
- 用例 C：agent 能把一次新能力构建沉淀为可复用的原子工具、wrapper tool 或等价受治理能力单元，而不是只完成一次性任务脚本。
- 用例 D：agent 能把一次新学到的任务经验沉淀为 `skill object`，并在相似任务中优先复用，而不是每次从 0 开始重新探索。
- 用例 E：至少一类 growth case 能以受治理方式完成从提案到生效的闭环，例如语音能力原型或外部信息读取能力原型。
- 用例 F：成长失败时，系统能回到上一个稳定状态，而不是把半成品变更静默留在运行路径里。

---

### P2-M2：Procedure Runtime 与多 Agent 执行
**用户价值**
- 用户可以把更长、更复杂、更需要中途校正的任务交给 NeoMAGI，而不是依赖 prompt 和记忆勉强维持流程正确性。

**关系说明**
- `Procedure Runtime` 是底层执行协议层，可单独服务单 agent 的高约束、多步骤、跨 turn 任务。
- 多 agent 执行是其重要上层消费者之一，但不是 `Procedure Runtime` 成立的前提条件。
- 没有严格 handoff / steering / resume 要求的松耦合多 agent 分工，可以先于完整 `Procedure` 接入存在；但严肃的运行时协作最终应收敛到同一 contract 上。
- NeoMAGI 的多 agent 默认定义为：在同一个用户利益与同一个 `SOUL / principal` 约束下的多个受治理执行单元，而不是多个长期并存的人格体。
- `P2-M2` 只为未来 Shared Companion 预留 actor / principal / shared-space execution context 余量；不在 Procedure Runtime Core 中实现关系记忆或多方身份治理。

**边界**
- In:
  - 先将 `Procedure Runtime` 从草案推进到可用产品能力，覆盖少量高约束、多步骤、跨 turn、带副作用的流程。
  - 再让多个 agent 的运行时协作接入这一层 contract，而不只是在开发流程里做多代理。
  - 建立中途 steering / interrupt / resume 的产品语义。
  - 将 purposeful compact 纳入产品范围：压缩时提炼任务状态、todo、核心约束，而不是简单总结。
  - 将 agent 之间“哪些上下文值得交换”上升为正式治理问题，而不是依赖人手过滤。
  - 预留 procedure / handoff packet 中表达 actor、principal、publish target 与未来 `shared_space_id` 的能力，避免把 runtime 永久写死为单 session / 单 principal。
- Out:
  - 不建设通用 workflow engine。
  - 不引入重型 DAG / 可视化编排 / 复杂表达式 DSL。
  - 不追求无边界 agent society。
  - 不把“多人格一起聊天”作为默认产品方向。
  - 不在本阶段落地 Shared Companion 的关系记忆、shared-space membership 或 consent policy。

**验收标准（Use Case）**
- 用例 A：用户发起一个多阶段任务后，可以在任务进行中途追加 steering，并在定义好的 checkpoint 生效。
- 用例 B：同一任务可以由多个 agent 分工推进，handoff 时只交换必要上下文，而不是整段对话全文复制。
- 用例 C：流程中断后，可从明确状态恢复，而不是完全依赖模型“重新理解发生过什么”。

**实施顺序建议**
- 先交付可单独成立的 `Procedure Runtime` 最小闭环，再把需要严格 handoff / steering / resume 的多 agent 场景接到这层 runtime 上。

---

### P2-M3：身份认证、用户连续性与记忆质量
**用户价值**
- 用户得到更高命中率的记忆召回，以及围绕“同一个已认证用户”建立的受控连续性，同时继续保持严格隔离；在明确授权的共享关系空间中，NeoMAGI 开始具备 Shared Companion 的最小记忆与建议基础。

**关系说明**
- 产品层以“同一个已认证用户”作为最终语义，优先表达 `per-user continuity`，而不是直接把 `per-peer` / `per-account` 暴露为核心产品概念。
- 实现层继续保留 `account_id`、`peer_id` 等 identity binding 证据，用于认证、映射、审计、解绑和故障排查。
- 只有经过验证的身份绑定，才允许多个渠道 identity 收敛到同一个用户连续体。
- Shared Companion 在 `P2-M3` 中以 `shared_space_id + membership + consent-scoped memory visibility` 作为最小语义，而不是以群聊 UI 或某一方私有记忆的隐式共享作为起点。

**边界**
- In:
  - 为 WebChat 引入认证登录，使 Web 路径具备可验证用户身份，而不再停留在匿名会话。
  - 建立 canonical user identity / principal 语义，把渠道 identity 的最终目标从 `per-peer` 提升为 `per-user continuity`。
  - 保留实现层的 `account_id` / `peer_id` 绑定材料，用于判断“为什么这两个渠道身份可以被视为同一个用户”。
  - 提升记忆检索与 recall 命中率，包括 hybrid search 等质量增强路径。
  - 让记忆层从稳定 kernel 进一步过渡到更明确的 memory applications 演化入口。
  - 建立跨 agent / 跨渠道上下文共享的筛选规则。
  - 建立 relationship/shared-space 的最小边界：成员关系、可解释共享范围、私有记忆与共享关系记忆的硬隔离。
- Out:
  - 不在无认证 Web 路径上启用用户级连续性。
  - 不把未经验证的渠道 `account_id` / `peer_id` 直接合并成同一个用户。
  - 不把当前阶段变成重型知识图谱工程。
  - 不允许默认跨渠道、跨 agent 自动共享全部上下文。
  - 不把一方私聊记忆用于另一方咨询，除非该内容已经被明确发布为 shared-space memory 或 shareable summary。

**验收标准（Use Case）**
- 用例 A：在有身份与绑定前提时，同一用户可获得受控的跨渠道连续性；在无身份前提时，系统继续正确拒绝危险共享。
- 用例 B：系统能解释“为什么某个 Web / Telegram / 其他渠道身份被视为同一个用户，或为什么没有被合并”。
- 用例 C：Phase 1 中已知的自然语句检索 miss 至少有一部分被稳定消除。
- 用例 D：记忆共享范围始终可解释，用户能知道“为什么这段记忆被允许共享 / 不允许共享”。
- 用例 E：两个已认证 principal 可显式加入同一个 shared space，并在不泄漏彼此私有记忆的前提下，写入、召回和解释一条共同确认的关系记忆。

---

### P2-M4：外部协作与动作表面扩展
**用户价值**
- 用户可以在更真实的协作环境里使用 NeoMAGI，例如 team chat、多 agent 群协作，以及受控的外部平台读写能力。

**关系说明**
- Slack / 群聊在 NeoMAGI 中首先是协作表面、审批表面或通知表面，而不是多 agent 成立的前提。
- 若未来进入群聊场景，其意义应主要来自真实工作流接入，而不是把内部多 agent 过程做成“多人格讨论秀”。
- 对 Shared Companion 而言，`P2-M4` 只负责把 `P2-M3` 已建立的 shared space 暴露到外部渠道；渠道不得成为 shared memory 的真源，也不得绕过 consent policy。

**边界**
- In:
  - Slack 作为新的协作型渠道进入候选范围。
  - 支撑在群环境中与多个 agent 交互，而不是只支持单用户单 agent。
  - 支撑已存在 shared space 的协作表面映射，例如把 channel / thread 绑定到某个 `shared_space_id`。
  - 把浏览器动作与外部平台操作视为“能力表面扩展”，而不是核心 runtime primitive。
  - 对外部平台能力采取“先读后写、先受控后放开”的顺序。
- Out:
  - 不做广义 social automation。
  - 不做无审批的自动发帖、自动运营、自动拉群。
  - 不在前置身份 / procedure / memory 契约未稳定前过早铺太多新渠道。
  - 不把 Slack 群聊作为多人格产品方向的默认载体。
  - 不通过群聊参与事实本身推断谁有权读取谁的私有记忆；权限必须来自 `P2-M3` 的 principal / membership / visibility policy。

**验收标准（Use Case）**
- 用例 A：用户可以在 Slack 或等价协作渠道里与多个 agent 进行受控协作。
- 用例 B：外部平台的信息读取能力遵循与主系统一致的治理边界。
- 用例 C：任何外部写动作都要求明确授权、可审计记录和清晰回滚/停用路径。

---

## 4. 推荐顺序（v1）
1. `P2-M1`（显式成长与 Builder 治理）
2. `P2-M2`（Procedure Runtime 与多 Agent 执行）
3. `P2-M3`（身份认证、用户连续性与记忆质量）
4. `P2-M4`（外部协作与动作表面扩展）

排序说明：
- `P2-M1` 先于一切，是因为“什么允许成长、如何验证成长”必须先定，否则后面的多 agent、外部动作和渠道扩展都会失去治理锚点。
- `P2-M2` 先于 `P2-M4`，是因为真正的多 agent 产品运行时能力要先于多渠道扩展稳定下来。
- `P2-M3` 排在 `P2-M2` 之后，是因为身份、用户连续性与记忆质量虽然重要，但它们服务于更大的运行时协作模型；同时用户级连续性的真正放开依赖认证与身份绑定前提，而不是单独配置一个 `dmScope`。
- `P2-M4` 最后，是因为它最依赖前面三项能力的稳定契约；Shared Companion 也必须先有 `P2-M3` 的 shared-space identity / memory policy，再进入外部协作表面。

---

## 5. 阶段切换规则

- 未达到当前阶段验收标准，不进入下一阶段。
- 新需求优先归类到当前阶段；如果只是质量增强或横切治理，优先并入当前阶段，不轻易新开 milestone。
- `P2-M1` 未定义显式成长对象与 growth eval 口径前，不进入 `P2-M2` 的正式实施。
- `P2-M3` 中与用户级连续性相关的能力，未满足身份认证与绑定前提时不得以“先开再补”方式落地。
- `P2-M4` 中任何外部写动作，未满足授权与审计要求前不得进入默认产品路径。

---

## 6. 横切议题（不单列 milestone）

以下议题重要，但当前不单独升格为独立 milestone：

- 安全漏洞扫描：
  - 作为持续质量门存在，而不是单独占用一个产品阶段。
- Actionbook / 浏览器能力定位：
  - 作为浏览器 skill object 的外部经验源与能力表面扩展的一部分处理，不作为核心 runtime primitive。
- compact 的目的性：
  - 归入 `P2-M2`，与 Procedure / steering / context exchange 一起治理。
- 子 agent 之间的通信价值判断：
  - 归入 `P2-M2` 与 `P2-M3` 的交叉议题，而不是单独拆新 milestone。
- Shared Companion / relationship space：
  - 作为跨 milestone 产品方向处理：`P2-M2` 预留 execution context，`P2-M3` 先完成 per-user continuity，再落地 relationship-space membership / consent-scoped memory，`P2-M4` 承接外部协作表面；不单独新开 Phase 2 milestone。

---

## 7. 版本变更记录

| 版本 | 日期 | 变更 | 依据 |
| --- | --- | --- | --- |
| v1 | 2026-03-05 | Phase 2 初稿：定义新阶段主题、`P2-M1 ~ P2-M4`、推荐顺序与切换规则 | 讨论收敛 + Phase 1 收口状态 |
| v1 | 2026-03-05 | 补充 `beads` 深化方向：作为 `P2-M1` 中 builder/coding 的结构化工作记忆能力表达 | 新增 Phase 2 产品口径讨论 |
| v1 | 2026-03-05 | 澄清 `P2-M2`：`Procedure Runtime` 是底层执行协议，多 agent 执行是重要上层消费者而非前置定义条件 | 新增 Phase 2 roadmap 歧义澄清 |
| v1 | 2026-03-05 | 重写 `P2-M3` 口径：产品层以 `per-user continuity` 为目标，实现层保留 `account_id` / `peer_id` 绑定语义 | 新增 Phase 2 身份模型讨论 |
| v1 | 2026-03-05 | 同步多 agent 核心定义：单一用户利益 / 单一 SOUL 下的执行单元；Slack 群聊降级为可选协作表面 | 新增 Phase 2 多 agent 治理讨论 |
| v1 | 2026-03-06 | 明确原子工具补全是自我进化前提：从背景原则提升为 `P2-M1` 的显式前置能力要求 | 新增 Phase 2 builder / atomic tools 讨论 |
| v1 | 2026-03-06 | 补充 `skill object` 口径：作为 capability 背后的内部经验层，承载“不要总从 0 开始”的学习与复用 | 新增 Phase 2 skill objects 讨论 |
| v1 | 2026-04-07 | 补充 Shared Companion 口径：以 relationship/shared-space 为核心，M2 预留 runtime context，M3 落地 shared-space memory policy，M4 承接协作表面 | ADR 0059 + 用户需求收敛 |
