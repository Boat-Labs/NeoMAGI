# Skill Objects（草案）

> 状态：draft  
> 日期：2026-03-06  
> 依据：`design_docs/phase2/roadmap_milestones_v1.md`、`design_docs/procedure_runtime_draft.md`、`design_docs/system_prompt.md`、ADR 0027、ADR 0048

## 1. 这份草案解决什么问题

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

本草案将这层命名为 `Skill Object`。

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
- 不在本阶段规定唯一持久化后端。

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

## 6. 最小对象模型（V1 草案）

### 6.1 SkillSpec

`SkillSpec` 是可交换、可插拔的最小封装单元，偏静态。

```python
@dataclass(frozen=True)
class SkillSpec:
    id: str
    capability: str
    version: int
    summary: str
    activation: str
    preconditions: tuple[str, ...] = ()
    delta: tuple[str, ...] = ()
    tool_preferences: tuple[str, ...] = ()
    escalation_rules: tuple[str, ...] = ()
    exchange_policy: str = "local_only"
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
  - 何时应考虑激活。
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

### 6.2 SkillEvidence

`SkillEvidence` 是运行时可学习部分，偏动态。

```python
@dataclass
class SkillEvidence:
    source: str
    success_count: int = 0
    failure_count: int = 0
    last_validated_at: str | None = None
    positive_patterns: tuple[str, ...] = ()
    negative_patterns: tuple[str, ...] = ()
    known_breakages: tuple[str, ...] = ()
    confidence: float = 0.0
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
- `confidence`
  - 不是事实真值，而是当前复用置信度。

### 6.3 ResolvedSkillView

`ResolvedSkillView` 是 turn-local 投影，不持久化。

```python
@dataclass(frozen=True)
class ResolvedSkillView:
    llm_delta: tuple[str, ...]
    runtime_hints: tuple[str, ...]
    escalation_signals: tuple[str, ...]
```

设计意图：
- `llm_delta`
  - 给 prompt builder 的最小经验摘要。
- `runtime_hints`
  - 给 planner / executor / procedure 的结构化提示。
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

### 7.2 SkillResolver

职责：
- 在意图解析后检索候选 skill；
- 默认只返回少量候选（建议 top 1~3）；
- 宁缺毋滥，避免 skill 污染上下文。

### 7.3 SkillProjector

职责：
- 将候选 skill 投影为 `ResolvedSkillView`；
- 分别生成：
  - `LLM View`
  - `Runtime View`
- 只注入 delta，不注入全量 SOP。

### 7.4 SkillLearner

职责：
- 在运行后记录哪些经验有效、哪些失效；
- 更新 evidence；
- 必要时提出 patch / promote / disable 建议；
- 不默认静默改写高层治理对象。

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

## 9. 与现有模块的关系

### 9.1 PromptBuilder

当前 `src/agent/prompt_builder.py` 中 `Skills` 仍是 placeholder。  
后续建议改为：
- `PromptBuilder` 只消费 `ResolvedSkillView.llm_delta`；
- 不自己负责解析 skill；
- 不读取 skill 本体存储。

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

### 10.3 什么时候 demote / disable

当出现以下信号时，应考虑降级或禁用 skill：
- 页面结构变化导致连续失败；
- 负经验显著多于正经验；
- 关键前提已不再成立；
- 新的底层 tool / procedure 已覆盖其主要价值。

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
  - activation: 用户明确批准发布
  - delta: 提交前再次检查 subreddit 规则、账号状态与内容一致性

### 11.3 关键验收点

- 第一次任务后，应生成可命名、可复用、可回滚的 skill object。
- 第二次相似任务时，应优先复用 skill，而不是重新探索。
- skill 必须能记录负经验，例如：
  - 登录态异常时不要继续；
  - subreddit 规则未读取时不要发帖；
  - 页面结构异常时报告 skill 失效而不是瞎试。

## 12. 当前未决问题

- `SkillSpec` 与 `SkillEvidence` 的最终持久化位置是否分离。
- skill 导入时如何处理本地覆盖与冲突。
- `SkillResolver` 的匹配分数模型是否只靠规则，还是允许引入向量 / embedding 辅助。
- 什么时候允许自动 patch skill，什么时候必须走 proposal / eval。
- `beads` 是否承担 skill evidence 的部分结构化记录职责。
