# P2-M1 Architecture（计划）

> 状态：planned  
> 对应里程碑：`P2-M1` 显式成长与 Builder 治理  
> 依据：`design_docs/phase2/roadmap_milestones_v1.md`、`design_docs/skill_objects_runtime.md`、ADR 0027、ADR 0036、ADR 0048

## 1. 目标

- 建立 NeoMAGI 的显式成长治理内核：哪些对象允许成长、如何提案、如何评测、如何生效、如何回滚。
- 将 `coding / builder` 从“基础工具边界预留”提升为正式产品能力。
- 固化 `2+1` 能力结构：
  - `Atomic Tools`
  - `Skill Objects`
  - `Governance / Runtime`
- 补齐自我进化所需的最小原子能力基座，并明确 promote 规则。
- 让 `beads` 从 issue / control-plane 数据面的一部分扩展为编程任务的结构化工作记忆层。
- 用少量 growth cases 验证“不要总从 0 开始”的真实闭环。

## 2. 当前基线（输入）

- `Tool Registry`、tool modes、双闸门授权框架已存在，提供受控执行边界。
- `coding` 相关基础能力尚未作为正式产品能力对外收口。
- `SOUL` 已有受治理的提案 / eval / apply / rollback 语义，但治理对象仍偏窄，尚未扩展到更一般的 capability growth。
- `PromptBuilder` 中的 skills 仍是 placeholder，尚无正式 `skill object` runtime。
- `beads` 已用于 devcoord control plane / issue 数据，但尚未承载普通构建任务的 work memory。
- 当前不少高层能力增长仍依赖一次性脚本、prompt 拼装或临时 wrapper，尚未形成稳定 promote 路径。

实现参考：
- `src/tools/registry.py`
- `src/tools/base.py`
- `src/agent/prompt_builder.py`
- `src/memory/evolution.py`
- `scripts/devcoord/`

## 3. 复杂度评估与建议拆分

`P2-M1` 复杂度：**很高**。  
原因：它同时覆盖成长治理、builder runtime、atomic tools 补全、skill objects、work memory 和 growth eval。

建议拆成 3 个内部子阶段：

### P2-M1a：Growth Governance Kernel
- 固定允许成长的对象类型。
- 固定 proposal / eval / apply / rollback / audit 语义。
- 固定 skill -> wrapper tool -> atomic tool 的 promote / demote 规则。

### P2-M1b：Skill Objects + Builder Runtime
- 建立 `skill object` runtime。
- 建立 builder 的任务模式与工作记忆闭环。
- 让 builder 输出可审计产物，而不是临时对话痕迹。

### P2-M1c：Growth Cases 与 Capability Promotion
- 跑少量 growth cases。
- 验证人类教授经验 / 外部经验导入。
- 验证 skill 复用与 promote 的最小闭环。

## 4. 目标架构（高层）

### 4.1 Growth Governance Plane

- 治理对象建议至少包括：
  - `SkillSpec / SkillEvidence`
  - wrapper tool 提案
  - procedure spec 提案
  - memory application spec
  - `SOUL` 相关受治理更新
- 所有成长对象统一走：
  - `propose -> eval -> apply -> rollback`
- 明确禁止：
  - 无评测静默生效
  - 直接 prompt 漂移代替成长对象生效
  - builder 直接绕过治理层写入长期能力面

### 4.2 Capability Layering

- `Atomic Tools`
  - 稳定、typed、可审计、跨场景复用。
- `Skill Objects`
  - 承载可学习 delta、可正负经验更新、可交换 / 可插拔。
- `Capability`
  - 对外暴露的稳定能力簇；一个 capability 可由多个 skill object 支撑。
- promote 原则：
  - 新经验先沉淀为 `skill object`
  - 只有高频、稳定、边界清晰、跨场景复用的部分才继续下沉

### 4.3 Builder Execution Plane

- builder 不是普通 chat 对话的自然外溢，而应作为：
  - 专门的任务模式；或
  - 受治理的 procedure entry
- builder 运行时至少应产出：
  - 任务 brief
  - 中间决策
  - TODO / blockers
  - 代码或配置改动
  - 测试与验证结果
  - promote 候选

### 4.4 Work Memory / Evidence Plane

- `beads` 在本里程碑中应扩展出“构建任务工作记忆”用途：
  - 当前目标
  - 已完成步骤
  - 中间失败
  - 证据与产物索引
  - 后续建议
- 该层不是长期用户记忆真源，而是成长过程证据层。

### 4.5 Growth Cases Plane

- 建议采用“只读 -> 草稿 -> 写入审批”递进案例。
- 外部经验（如用户教授经验、Actionbook、互联网经验）默认先转成 skill object。
- 成功标准不是“任务偶然做成”，而是：
  - 学到的经验形成可命名对象
  - 相似任务优先复用
  - 失败时能定位 skill 失效或 promote 不成立

## 5. 边界

- In:
  - 显式成长治理对象与状态机。
  - builder 产品化。
  - atomic tools 补全策略与 promote 规则。
  - `skill object` runtime 接入。
  - `beads` work-memory 用途扩展。
  - 少量 growth cases。
- Out:
  - 不做无边界自我修改。
  - 不让 builder 直接等于“无限自我改造”。
  - 不在本里程碑默认开放外部账号代发。
  - 不把所有学习都直接下沉成 atomic tool。
  - 不把 `beads` 直接升级为长期 memory truth。

## 6. 验收对齐（来自 roadmap）

- agent 能说明一次成长“改了什么、为什么改、怎么验证、如何回滚”。
- 构建任务中间状态能沉淀到 `beads` work memory。
- 至少一类新能力能沉淀为可复用的 atomic tool / wrapper tool / 等价治理单元。
- 至少一类新经验能先沉淀为 `skill object`，并在相似任务中优先复用。
- 至少一类 growth case 能完成 propose -> eval -> apply 的闭环。
- 失败时系统能回到上一个稳定状态。

## 7. P2-M1 Post Works（P2-M2 前补充计划）

以下内容不是原始 `P2-M1` 主线验收的一部分，而是 `P2-M1` closeout 后、`P2-M2` 开工前，为降低后续实现复杂度而追加的 post-works 规划。

总览索引：

- [`p2-m1-post-works_pre-p2-m2-session-concurrency-atomic-tools_2026-04-06_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works_pre-p2-m2-session-concurrency-atomic-tools_2026-04-06_draft.md)

拆分计划：

- [`p2-m1-post-works-p1_multi-session-threads_2026-04-06_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p1_multi-session-threads_2026-04-06_draft.md)
  - 目标：补齐 Codex 风格左侧 `threads` rail，支持 multi-session 切换与后台运行完成信号
- [`p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p2_tool-concurrency-metadata_2026-04-06_draft.md)
  - 目标：在 tool interface 上同时引入 `is_read_only` 与 `is_concurrency_safe`，并实现同一 turn 内连续只读批次的有界并行
- [`p2-m1-post-works-p3_atomic-coding-tools_2026-04-06_draft.md`](/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/dev_docs/plans/phase2/p2-m1-post-works-p3_atomic-coding-tools_2026-04-06_draft.md)
  - 目标：按风险分层补齐 coding atomic tools，先 `glob / grep`，再 `write_file / edit_file`，`bash` 作为条件性 follow-up

当前收敛结论：

- multi-session V1 采用左侧 `threads` rail，不做文件夹。
- tool concurrency 采用双元数据，而不是只保留 `is_read_only`。
- atomic tools 按三层落地，其中 `bash` 当前不作为第一轮硬验收项。
