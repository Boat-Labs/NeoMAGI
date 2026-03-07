# Skill Objects

> 状态：approved  
> 日期：2026-03-07  
> 依据：`design_docs/phase2/roadmap_milestones_v1.md`、`design_docs/procedure_runtime.md`、`design_docs/system_prompt.md`、ADR 0027、ADR 0048

## 1. 这份设计解决什么问题

NeoMAGI 已经明确两件事：
- 能力扩展应优先走稳定原子工具路线；
- 自我进化不能总是从 0 开始。

如果没有中间层，这两个目标会冲突：
- 只靠 atomic tools，系统会把站点经验、任务套路和用户教授方法过早塞进底层能力层；
- 只靠 prompt / markdown skill / 一次性脚本，经验又会变得过软、不可交换、不可插拔、难以审计。

因此，需要一层很薄的 runtime experience object：
- 它不是 tool；
- 它不是 procedure；
- 它不是人格；
- 它不是 hook 本身；
- 它只负责承载“这类任务基于已有经验通常该怎么做得更稳”的可复用 delta。

本文档将这层命名为 `Skill Object`。

## 2. 核心判断

### 2.1 `2+1` 结构

- `Atomic Tools`
  - 稳定、typed、可审计的能力底座。
  - 回答“系统最底层能做什么”。
- `Skill Objects`
  - 可学习、可复用、可失效检测的经验对象。
  - 回答“这类任务通常怎么更稳、更少从 0 开始”。
- `Governance / Runtime`
  - procedure、approval、eval、rollback、publish / merge。
  - 回答“什么时候允许做、怎么验证、怎么生效”。

### 2.2 内外语义分离

- `skill object` 是内部真实对象。
- `capability` 是对外稳定名字或能力簇。
- 一个 capability 可以由一个或多个 skill object 支撑。

例：
- capability: `reddit_assist`
- skill:
  - `reddit_research_via_actionbook`
  - `reddit_draft_post`
  - `reddit_submit_with_approval`

## 3. 非目标

- 不定义 Claude Code / Codex 的 project skills 或 slash skills。
- 不把 skill object 变成新的 workflow engine。
- 不要求所有任务都必须命中 skill object。
- 不让 skill object 直接执行副作用。
- 不在本阶段规定唯一表结构、registry layout 或物理存储布局。
- 不引入 SQLite；最终持久化仍遵循项目 PostgreSQL 17 基线。

## 4. 设计原则

### 4.1 Skill 存的是 delta，不是全量 SOP

skill object 不应重复基础模型已经知道的大段通用流程。  
它只保存“这套系统额外学到、值得复用的差异化经验”。

### 4.2 Skill 可以是不完整的

skill object 不需要覆盖整个任务的全部步骤。  
只要它能在关键节点减少重新探索、减少误判、减少高风险误操作，就已经有价值。

### 4.3 Skill 必须同时学习正经验和负经验

skill object 不只记录“怎么成功”，还要记录：
- 什么情况下容易失败；
- 哪些页面状态不可信；
- 什么信号出现时应停下；
- 何时必须升级到 procedure 或 approval。

### 4.4 Skill 只拥有激活权、建议权和升级权

skill object 默认不直接拥有执行权：
- 不直接运行 tool；
- 不直接落副作用；
- 不直接绕过 procedure / approval。

执行仍由：
- atomic tools；
- procedure runtime；
- approval / governance
负责。

### 4.5 Skill 必须具有最小封装边界

skill object 至少要做到：
- 可命名；
- 可版本化；
- 可导入导出；
- 可禁用 / 替换；
- 可被 agent 之间有限交换；
- 不依赖某一段固定 prompt 文本才能存在。

## 5. 为什么它不是 hook 层

hook 擅长的是：
- 在固定事件前后插逻辑；
- 做拦截、日志、验证。

但 skill object 的核心不是“拦截事件”，而是：
- 在任务开始前提供经验偏置；
- 在任务执行中提供局部策略；
- 在任务结束后吸收正/负经验。

因此更合适的理解是：

**skill object = 结构化经验对象 + 程序化激活/投影机制**

而不是：

**skill object = 一组回调函数**

NeoMAGI 不需要传统 hook 机制作为本体；它只需要少量固定 runtime join points 来解析和投影 skill object。

## 6. 最小对象模型（V1）

### 6.1 SkillSpec

`SkillSpec` 是可交换、可插拔的最小封装单元，偏静态。

```python
from pydantic import BaseModel, ConfigDict


class SkillSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    capability: str
    version: int
    summary: str
    activation: str
    activation_tags: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    delta: tuple[str, ...] = ()
    tool_preferences: tuple[str, ...] = ()
    escalation_rules: tuple[str, ...] = ()
    exchange_policy: str = "local_only"
    disabled: bool = False
```

字段语义：
- `id`
  - 稳定标识符。
- `capability`
  - 对外能力簇名。
- `version`
  - 用于导入导出、回滚和兼容控制。
- `summary`
  - 一句话说明 skill 做什么。
- `activation`
  - 面向人类与审计的一句话激活说明。
- `activation_tags`
  - V1 的轻量结构化匹配提示，如 `reddit` / `research` / `drafting` / `approval_required`。
  - V1 默认先靠规则与 tag 过滤，不要求引入 embedding。
- `preconditions`
  - 前提条件；不满足时应跳过或升级。
- `delta`
  - 这类任务中最值得复用的经验差异。
- `tool_preferences`
  - 推荐组合哪些原子工具，不等于唯一流程。
- `escalation_rules`
  - 何时必须升级到 procedure / approval。
- `exchange_policy`
  - 是否允许导出、共享、导入覆盖。
- `disabled`
  - 本地禁用开关；被禁用的 skill 不参与 resolution / projection。

### 6.2 SkillEvidence

`SkillEvidence` 是运行时可学习部分，偏动态。

```python
class SkillEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    success_count: int = 0
    failure_count: int = 0
    last_validated_at: str | None = None
    positive_patterns: tuple[str, ...] = ()
    negative_patterns: tuple[str, ...] = ()
    known_breakages: tuple[str, ...] = ()
```

字段语义：
- `source`
  - 来源，如 human-taught / imported / internet-learned / self-discovered。
- `positive_patterns`
  - 被验证过有效的经验。
- `negative_patterns`
  - 被验证过应避免的经验。
- `known_breakages`
  - 已知失效条件。

`SkillEvidence` 在 V1 中保持 frozen。  
更新时通过创建新实例替换旧实例（如 `model_copy(update=...)`），而不是原地修改；  
旧实例可直接作为 diff / audit 对照物。

V1 不把 `confidence` 作为持久化字段。  
如需运行时排序分数，可由 `success_count / (success_count + failure_count + 1)`  
或等价规则临时推导，避免出现“字段存在但无人维护”的双写漂移。

### 6.3 ResolvedSkillView

`ResolvedSkillView` 是 turn-local 投影，不持久化。

```python
class ResolvedSkillView(BaseModel):
    model_config = ConfigDict(frozen=True)

    llm_delta: tuple[str, ...]
    runtime_hints: tuple[str, ...] = ()
    escalation_signals: tuple[str, ...] = ()
```

设计意图：
- `llm_delta`
  - 给 prompt builder 的最小经验摘要。
- `runtime_hints`
  - V1 只给 `AgentLoop` 的 `pre-procedure` 判断消费，不假设存在独立 planner 模块。
- `escalation_signals`
  - 用于触发 approval、procedure 或人工确认。

## 7. 最小运行时部件

### 7.1 TaskFrame

在进入 prompt 组装前，先抽取一个薄的任务框架：
- task type
- target outcome
- risk
- channel
- current mode
- current procedure
- available tools

`TaskFrame` 不是计划，不是 procedure，只是 skill resolution 的稳定输入。

```python
class TaskFrame(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_type: str | None = None
    target_outcome: str | None = None
    risk: str | None = None
    channel: str | None = None
    current_mode: str
    current_procedure: str | None = None
    available_tools: tuple[str, ...] = ()
```

V1 约束：
- `TaskFrame` 由 `AgentLoop` 在单次 turn 内生成，不单独持久化。
- 生成时机：收到用户消息后、完成 mode / scope / tool registry / active procedure 解析后、进入 `PromptBuilder.build()` 前。
- 生成方式：V1 仅使用规则抽取，不新增额外 LLM 调用。
- 输入来源限于：
  - 当前用户消息；
  - 当前 session mode / scope；
  - 当前 active procedure（若存在）；
  - 当前可见工具集合；
  - 最近少量对话与已解析出的结构化上下文。

### 7.2 SkillRegistry / SkillStore

V1 需要一个极小的数据源契约，供 `SkillResolver` 读取当前可用 skill；  
不要求固定 preload 还是按需查库，但要求 `SkillResolver` 依赖抽象接口，而不是直接耦合底层存储。

```python
from typing import Protocol


class SkillRegistry(Protocol):
    async def list_active(self) -> list[SkillSpec]: ...

    async def get_evidence(
        self,
        skill_ids: tuple[str, ...],
    ) -> dict[str, SkillEvidence]: ...
```

V1 约束：
- backing store 可以是 PostgreSQL + cache，或等价实现，但对 `SkillResolver` 暴露统一 registry/store 接口。
- `list_active()` 只返回当前可参与 resolution 的 skill；禁用、撤回、或不兼容版本的 skill 不应出现在结果中。
- `get_evidence()` 只负责按 `skill_id` 取当前 evidence 快照；如何 join、cache、预热属于实现细节。
- 是否启动时全量加载，还是 turn 内按需读取，是实现选择；文档只固定 resolver 的消费契约。

### 7.3 SkillResolver

职责：
- 在意图解析后检索候选 skill；
- 默认只返回少量候选（建议 top 1~3）；
- 宁缺毋滥，避免 skill 污染上下文。

V1 约束：
- 先过滤 `disabled=True` 的 skill。
- 先做 `activation_tags + capability + preconditions` 的规则级过滤，再做轻量排序。
- 当前 turn 命中多个 skill 时允许同时激活，但默认只保留 top 1~3。
- 若多个 skill 彼此冲突，优先级按：
  - 当前 procedure / approval 相关 skill
  - evidence 更新更近、已知 breakage 更少的 skill
  - delta 更短、更局部的 skill
- V1 不要求 embedding；是否引入向量召回留到后续版本。

### 7.4 SkillProjector

职责：
- 将候选 skill 投影为 `ResolvedSkillView`；
- 分别生成：
  - `LLM View`
  - `Runtime View`
- 只注入 delta，不注入全量 SOP。

V1 约束：
- `SkillProjector` 必须执行上下文预算裁剪，避免 skill 重新退化为 prompt 污染层。
- 建议默认上限：
  - 候选 skill 总数：1~3
  - 每个 skill 注入的 `llm_delta`：最多 2~3 条
  - 仅保留与当前 `TaskFrame` 直接相关的 escalation / breakage 摘要
- 若当前 context、memory recall 或 active procedure 已明确否定某个 skill 的前提，应直接丢弃其 `llm_delta`，并将其视为 stale candidate，而不是强行注入 prompt。

### 7.5 SkillLearner

职责：
- 在运行后记录哪些经验有效、哪些失效；
- 更新 evidence；
- 必要时提出 patch / promote / disable 建议；
- 不默认静默改写高层治理对象。

V1 学习边界：
- 更新频率按 task 结束触发，而不是每次 tool call 后都更新。
- 自动写入的负经验只来自 deterministic 信号，例如：
  - precondition / guard deny
  - tool 返回结构化失败
  - active procedure / approval gate 明确拒绝
  - 页面结构异常、登录态异常等可机器判定 breakage
- 正经验不因“用户没纠正”而自动成立；V1 只在以下场景写入正经验：
  - 用户显式确认结果可复用；
  - 同一 skill 在受控场景下重复成功，且成功边界可结构化判断。
- `SkillLearner` 只能提出 `patch / promote / disable` 建议，不直接 apply 治理对象变更。
- 这是有意的保守策略：V1 宁可少学正经验，也不让 skill layer 因误学而膨胀。
- 因此 V1 evidence 的主要价值更偏向：
  - breakage 检测
  - stale skill 识别
  - patch / disable / promote 候选生成
  而不是快速自动累积大量正经验。

### 7.6 Skill Creation Path

V1 需要一个明确的从 0 到 1 创建路径，但不允许静默直接生效。

最小创建入口：
- 用户显式教授，例如“记住这个方法”“以后这类任务按这个做”；
- `post-run-learning` 中检测到高置信的可复用 delta，并形成结构化提案草稿。

最小创建流程：
1. `SkillLearner` 或用户教学入口生成候选 `SkillSpec + SkillEvidence` 草稿；
2. 形成 `GrowthProposal` 或等价 proposal record，附带：
   - summary
   - activation / activation_tags
   - delta
   - 初始 evidence
   - 来源与证据引用
3. 进入治理路径，再决定是否 apply 为 active skill。

治理边界：
- 创建新 skill 本身属于治理动作，不应绕过 proposal / eval / apply。
- 若 `skill_spec` kind 尚未 onboard，V1 允许只生成 proposal / draft，不要求自动 apply。
- 第一次任务后“应生成可命名、可复用、可回滚的 skill object”的最小含义是：
  - 至少生成可审计的 skill proposal / draft
  - 而不是把经验只留在一次性对话文本里

## 8. 固定 runtime join points

V1 只建议保留 3 个固定 join points：

1. `pre-plan`
- 在用户意图进入 planner 前解析 candidate skills。

2. `pre-procedure`
- 当任务可能升级为 procedure / approval 时，用 skill 的 escalation 规则辅助判断。

3. `post-run-learning`
- 任务完成或失败后，更新 skill evidence。

不建议在 V1 引入大量细碎 hook 点。  
join point 越多，系统越容易重新走向高熵的事件回调网络。

与当前 `AgentLoop` 的最小集成方案：
- `pre-plan`
  - 在 `AgentLoop.handle_message()` 中，完成 mode / tool schema / active procedure 解析后、调用 `PromptBuilder.build()` 前触发。
  - 产物：`TaskFrame` + `ResolvedSkillView`。
- `pre-procedure`
  - 在任务尝试进入 procedure / approval 路径前触发。
  - 消费：`ResolvedSkillView.runtime_hints` 与 `escalation_signals`。
  - 若当前任务未涉及 procedure / approval，该 join point 允许为空操作。
- `post-run-learning`
  - 在一次 task 形成终态后触发：终态可以是最终 assistant 回复、结构化失败、或 procedure terminal outcome。
  - 对多 tool 的单个 task，只产生一次 task-level learning event。

## 9. 与现有模块的关系

### 9.1 PromptBuilder

当前 `src/agent/prompt_builder.py` 中 `Skills` 仍是 placeholder。  
后续建议改为：
- `PromptBuilder` 只消费 `ResolvedSkillView.llm_delta`；
- 不自己负责解析 skill；
- 不读取 skill 本体存储。
- `llm_delta` 作为独立 `Skills layer` 注入，层位固定为：
  - `Safety` 之后
  - `Workspace context` 之前
- 语义优先级固定为：
  - `Safety > AGENTS.md > USER.md > skill delta > SOUL.md > IDENTITY.md`
- skill delta 只能提供经验偏置，不能覆盖：
  - `AGENTS.md` 的行为契约
  - `USER.md` 的用户偏好
  - 当前 turn 已经确定的 procedure / approval 硬边界
- skill delta 只应包含任务经验、工具偏好、失败信号与升级条件；
  不应包含语气、人格、身份展示层面的指导。
- 若 recalled memory、当前上下文事实或 active procedure 明确与某个 skill 冲突，应优先降级 / 丢弃该 skill 投影，而不是让 prompt 内部自行“投票解决”。

### 9.2 Procedure Runtime

skill object 不替代 procedure：
- skill 提供经验偏置和 escalation 信号；
- procedure 仍负责 deterministic state / guard / transition。

### 9.3 Atomic Tools

skill object 不替代 tool：
- tool 回答“能做什么”；
- skill 回答“这类任务通常怎么更稳地使用这些 tool”。

## 10. Promotion 与 Demotion

### 10.1 什么时候留在 skill 层

以下情况优先留在 skill 层：
- 站点经验；
- 用户教授的操作习惯；
- 页面状态判断；
- 失败恢复经验；
- 任务套路但复用边界尚不稳定。

### 10.2 什么时候 promote

以下情况才考虑向下 promote：
- 高频复用；
- 跨场景稳定；
- 输入输出边界清晰；
- 可 typed；
- 可测试；
- 不再强依赖某个具体站点经验文本。

promote 目标可以是：
- 更稳定的 wrapper tool；
- 更稳定的 atomic tool；
- 更明确的 procedure entry。

promote 在治理上不是 skill 自己直接生效，而是 `GrowthProposal`：
- 向下 promote 为 wrapper tool / atomic tool / procedure entry 时，应进入 `GrowthGovernanceEngine` 的 `propose -> eval -> apply` 路径。
- 跨 kind promote 受 `GrowthKindPolicy / PromotionPolicy` 约束，而不是由 `SkillLearner` 直接写入。
- 若目标 kind 尚未 onboard，仅允许生成 proposal / recommendation，不直接 apply。

### 10.3 什么时候 demote / disable

当出现以下信号时，应考虑降级或禁用 skill：
- 页面结构变化导致连续失败；
- 负经验显著多于正经验；
- 关键前提已不再成立；
- 新的底层 tool / procedure 已覆盖其主要价值。

demote / disable 同样属于治理动作：
- 可以由 `SkillLearner` 或评测管线提出。
- 是否生效由治理层决定；高风险 disable 不应静默越过审计面。

## 11. Actionbook / Reddit 示例

这类 case 的目标不是“学会 Reddit”，而是：

**学会吸收用户教授的 Actionbook 经验，并将其沉淀为可复用 skill object，而不是每次从 0 开始探索网页。**

### 11.1 示例 capability

- capability: `reddit_assist`

### 11.2 示例 skills

- `reddit_research_via_actionbook`
  - activation: 用户要求去 Reddit 搜集信息
  - delta: 优先利用 Actionbook 的现成操作经验，不要从 0 探索页面
  - tool_preferences: 浏览器能力 + Actionbook manual 读取
  - escalation_rules: 只读采集可直接做；发帖/回复必须升级审批
- `reddit_draft_post`
  - activation: 用户需要基于采集结果准备草稿
  - delta: 先生成草稿，不直接提交
- `reddit_submit_with_approval`
  - activation: 已命中发布 approval / procedure entry，且用户明确批准发布
  - delta: 提交前再次检查 subreddit 规则、账号状态与内容一致性
  - escalation_rules: 若 approval gate 未打开，只提出进入 procedure / approval 的建议，不直接提交

### 11.3 关键验收点

- 第一次任务后，应生成可命名、可复用、可回滚的 skill object。
- 第二次相似任务时，应优先复用 skill，而不是重新探索。
- skill 必须能记录负经验，例如：
  - 登录态异常时不要继续；
  - subreddit 规则未读取时不要发帖；
  - 页面结构异常时报告 skill 失效而不是瞎试。

## 12. 当前未决问题

- `SkillSpec` 与 `SkillEvidence` 的最终 PostgreSQL 表结构是否分离，还是共用 registry / store。
- skill 导入时如何处理本地覆盖与冲突。
- V2 是否需要在规则匹配之外引入向量 / embedding 辅助召回。
- 什么时候允许自动 patch skill，什么时候必须走 proposal / eval。
- `beads` 是否承担 skill evidence 的部分结构化记录职责。
- 多个 capability / skill 同时命中时，是否需要更明确的冲突仲裁策略。
- skill delta 的全局 token budget 是否需要独立于 memory recall 单独配额。
- skill evidence 与 memory 系统中的经验条目如何去重与分层，避免同一条经验同时沉淀到两个平面。
