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
