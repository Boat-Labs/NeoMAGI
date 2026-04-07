---
doc_id: 019cc262-8b20-79c5-bbc5-f093d543d965
doc_id_format: uuidv7
doc_id_assigned_at: 2026-03-06T10:02:44+01:00
---
# P2-M4 Architecture（计划）

> 状态：planned  
> 对应里程碑：`P2-M4` 外部协作与动作表面扩展  
> 依据：`design_docs/phase2/roadmap_milestones_v1.md`、ADR 0047、ADR 0048、ADR 0059

## 1. 目标

- 在不改变 NeoMAGI 核心身份与 runtime 契约的前提下，扩展外部协作表面与外部动作表面。
- 将 Slack / 群聊定位为协作与审批表面，而不是多 agent 的成立理由。
- 将浏览器 / 外部平台能力按“先读后写、先受控后放开”的顺序接入。
- 为后续真实工作流接入保留统一 channel adapter 与 approval / audit 语义。
- 将 Shared Companion 的外部表面限定为 `P2-M3` shared-space policy 的投影，不让渠道本身决定关系记忆权限。

## 2. 当前基线（输入）

- 当前已有 WebChat 与 Telegram 两个渠道。
- 多 agent 的 runtime 定义已明确为 execution-oriented，而不是多人格协作。
- 浏览器 / 外部平台操作尚未进入正式产品 runtime；Actionbook 目前更适合作为外部经验源。
- 对外部写动作，当前只有一般性的高风险边界，没有专门的产品级审批表面。
- 当前还没有 relationship/shared-space-aware channel binding；任何群聊或 Slack thread 都不能被默认视为 shared memory space。

实现参考：
- `src/gateway/`
- `src/channels/telegram.py`
- `decisions/0003-channel-baseline-webchat-first-telegram-second.md`
- `decisions/0044-telegram-adapter-aiogram-same-process.md`

## 3. 复杂度评估与建议拆分

`P2-M4` 复杂度：**中高**。  
原因：它依赖 `P2-M1~M3` 的能力契约稳定，但自身更偏表面和审批集成。

建议拆成 2 个内部子阶段：

### P2-M4a：Read Surfaces & Collaboration Channels
- Slack / 类 Slack 表面
- 外部平台只读采集
- 通知 / 审批 / 状态可见性
- 将 channel / thread 映射到既有 `shared_space_id` 的候选表面，但不在本层创建 memory policy

### P2-M4b：Approved Write Surfaces
- 发帖 / 回复 / 外部写动作
- 显式审批
- 审计、停用、回滚路径
- shared-space 输出的 publish / draft / write 必须保留可见性与授权证据

## 4. 目标架构（高层）

### 4.1 Channel Surface Plane

- 新渠道应继续采用 adapter 思路，而不是复制核心业务逻辑。
- Slack / 群聊价值优先体现在：
  - thread 协作
  - 状态同步
  - 审批 / 确认
  - 通知
- channel / thread 只能绑定到已经由 `P2-M3` 建立的 principal / membership / `shared_space_id`；不得从“同在一个群里”推断 shared memory 权限。
- 在 Shared Companion 场景中，channel 是交互表面，relationship memory 的真源与检索权限仍由 memory kernel + shared-space policy 决定。

### 4.2 External Action Surface Plane

- 外部动作建议按 3 级分类：
  - `read`
  - `draft`
  - `write`
- 其中：
  - `read` 可优先进入低风险路径
  - `draft` 作为用户审阅中间层
  - `write` 必须进入 approval / audit

### 4.3 Browser Skill Plane

- 浏览器能力不建议直接定义为新的 runtime primitive。
- 外部经验源（如 Actionbook）应优先进入：
  - browser skill object
  - capability-level surface
- 只有特别稳定、边界清晰的部分，才再继续 promote。

### 4.4 Approval / Audit Plane

- 所有外部写动作必须具备：
  - 显式用户授权
  - 审计记录
  - 停用 / 撤销路径
- 这层不能只依赖聊天语义，应接到 `Procedure Runtime` 或等价治理路径上。
- 若外部写动作发生在 shared space 中，审计记录必须能说明代表哪个 principal / shared space、使用了哪些 visibility 级别的材料，以及谁批准了 publish。

### 4.5 Group Collaboration Plane

- 若进入群聊场景，重点是：
  - primary agent 与 worker 状态可见
  - 审批点可见
  - 结果可发布回主线程
- 不是“让多个长期人格在群里讨论”。
- Shared Companion 的群聊体验应优先表达共同确认、分歧澄清和关系修复，而不是把当前发言者的私有上下文自动带入群聊。
- 群聊中出现单方敏感信息时，默认作为该 channel 的当前消息处理；是否沉淀为 shared memory 仍需显式确认或 policy 允许。

## 5. 边界

- In:
  - Slack / 等价协作表面候选。
  - 外部平台只读与草稿能力。
  - 外部写动作审批表面。
  - 浏览器 skill object 的外部经验接入。
  - 已存在 shared space 的 channel / thread 绑定与可见性展示。
- Out:
  - 不做广义 social automation。
  - 不做无审批自动发帖 / 自动运营 / 自动拉群。
  - 不把 Slack 群聊作为多人格产品方向的默认载体。
  - 不在前置 identity / procedure / memory 契约未稳定前铺太多新渠道。
  - 不在渠道层重新发明 relationship memory policy。
  - 不允许未绑定 shared_space 的群聊默认读取任一成员的私有记忆。

## 6. 验收对齐（来自 roadmap）

- 用户可以在 Slack 或等价协作渠道里与多个 agent 进行受控协作。
- 外部平台的信息读取能力遵循与主系统一致的治理边界。
- 任意外部写动作都要求明确授权、可审计记录和清晰停用路径。
- Shared Companion 表面只能展示或使用 `P2-M3` 已授权的 shared-space memory，并能解释其 channel binding 与 visibility 来源。
