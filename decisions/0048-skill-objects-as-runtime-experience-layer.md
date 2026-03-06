# 0048-skill-objects-as-runtime-experience-layer

- Status: accepted
- Date: 2026-03-06

## 选了什么
- 将 NeoMAGI 的能力结构固定为 `2+1`：
  - `Atomic Tools`：稳定、typed、可审计的能力底座；
  - `Skill Objects`：可学习、可复用的运行时经验对象；
  - `Governance / Runtime`：横切的 procedure、approval、eval、rollback 与 publish/merge 约束层。
- 明确 `skill object` 是内部真实对象；`capability` 只是对外稳定名字或能力簇，不额外构成新架构层。
- 明确 `skill object` 不是 hook 机制、也不是纯 prompt 片段，而是可被程序化解析、检索、投影和学习的 first-class runtime object。
- 固定 `skill object` 的三条原则：
  - skill 存的是 `delta`，不是全量 SOP；
  - skill 可以是不完整的；
  - skill 必须同时学习正经验和负经验。
- 明确 `skill object` 必须具有最小封装边界，使其可交换、可导入导出、可插拔，而不要求与某个固定 prompt 模板或单一存储后端强绑定。
- 明确 `skill object` 默认不直接拥有执行权，只拥有激活、建议和升级（escalation）能力；真正执行仍通过 atomic tools 与受治理 runtime 完成。

## 为什么
- NeoMAGI 的长期方向要求“不要总从 0 开始”，但又不能让经验学习退化为不可审计的 prompt 魔法；需要在原子工具之上补一层结构化经验对象。
- 若把所有学习都压回原子工具层，会导致工具层过早承载站点经验、任务套路和局部偏好，破坏工具层的稳定边界。
- 若继续只靠 prompt / markdown skills / hook 风格注入，经验对象会缺少结构化检索、最小封装与可交换性，难以形成稳定演化路径。
- 将 skill 定义为独立 runtime object，可以让系统在不增加过多层级的前提下，同时满足：
  - 复用过去经验；
  - 保持 atomic tools 简洁；
  - 通过 procedure / approval 控制高风险路径；
  - 将学来的经验逐步 promote 为更稳定的能力单元。
- capability 作为对外能力簇而非内部真实对象，可以减少产品表面复杂度，同时保留内部 skill 演化空间。
- 保留 skill 的最小封装边界，有助于未来进行：
  - 用户教授经验导入；
  - agent 之间有限交换；
  - 公开经验模板复用；
  - 本地禁用 / 替换 / 覆盖某个 skill 实现。

## 放弃了什么
- 方案 A：只保留 `Atomic Tools + Procedure`，不引入独立 skill 层。
  - 放弃原因：系统会持续在高层任务上从 0 开始，且站点经验与任务套路只能散落在对话、prompt 或一次性脚本里。
- 方案 B：把 skill 实现为纯 hook 机制或纯 prompt 注入片段。
  - 放弃原因：这不利于形成 first-class object，缺少结构化检索、交换、插拔与 evidence 演化能力。
- 方案 C：将 wrapper tool、procedure、capability、skill 各自升成完整独立层。
  - 放弃原因：层级过多会增加认知与实现复杂度，不符合 NeoMAGI 的降熵原则。

## 影响
- 技术落地草案收敛到 `design_docs/skill_objects_runtime_draft.md`，用于定义 `skill object` 的最小结构、runtime join points、evidence 语义以及与 prompt / procedure / tools 的交互边界。
- 当前 `PromptBuilder` 中的 skills placeholder 后续应演进为“程序化 skill 投影层”，而不是继续维持空占位或拼接式文本层。
- `P2-M1` 中“不要总从 0 开始”的能力建设，后续应优先通过 skill object 落地；只有足够稳定、清晰、跨场景复用的部分，才允许 promote 到更底层的能力单元。
- 后续若引入用户教授经验、Actionbook 等外部经验源，应首先沉淀为 skill object，而不是直接下沉为 atomic tool。

## 潜在风险
- 若 `SkillResolver` 过度命中或缺少质量门，skill object 可能重新退化为高层 prompt 污染层，增加上下文噪声而不是减少从 0 开始的成本。
- 若 skill object 的最小封装边界没有被严格维持，后续很容易滑向“半工具、半 procedure、半 prompt”的混合层，重新制造架构歧义。
- 若系统只积累正经验而不持续维护负经验，skill object 会越来越乐观，最终放大误操作、错误泛化和高风险路径误触发。
- 若负经验记录过强、过早固化，系统也可能走向另一端：因为历史失败而过度保守，降低探索能力和能力扩展速度。
- 若导入/交换机制缺少来源、版本和信任边界，外部 skill object 可能成为经验污染或供应链污染入口。
- 若 promote / demote 规则不清晰，skill object 可能长期堆积，既不上升为稳定能力，也不被淘汰，形成新的熵增层。

## 潜在限制
- 本 ADR 只确定 skill object 的治理定位，不决定其最终持久化后端、序列化格式或检索实现。
- 本 ADR 不解决 deterministic execution；高约束、多副作用流程仍需 `Procedure Runtime` 承担硬边界。
- 本 ADR 不保证所有任务都需要或都适合 skill object；简单任务仍应优先直接使用 atomic tools。
- 本 ADR 不定义自动 patch、自动 promote、自动 disable 的阈值；这些仍属于后续技术草案和评测策略范围。
- 本 ADR 只要求“可交换、可插拔”的最小封装能力，不等于承诺早期就提供完整的市场、分发网络或跨实例标准协议。
- 本次补充只增加风险视角与实现边界，不改变本 ADR 已接受的 `2+1` 结构和 skill object 定位。
