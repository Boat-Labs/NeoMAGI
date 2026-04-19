---
doc_id: 019da552-06d8-720f-a9fe-1e2317ae53b2
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-19T12:38:29+02:00
---
# P3 Daily Use Roadmap（草稿）

> 状态：draft
> 日期：2026-04-19
> 目标：把 NeoMAGI 从“治理与自进化实验平台”收敛到“每天可用的 personal agent”，优先补齐可替代部分 claude.ai / ChatGPT 日常使用的能力。
> 技术口径：见 `design_docs/phase3/p3_daily_use_architecture_draft.md`。

## 1. 产品判断

P3 不以 self-evolution 为主线。P3 的主线是 daily use：

- 用户每天愿意打开 NeoMAGI。
- NeoMAGI 能处理一部分原本交给 claude.ai、ChatGPT、搜索引擎、本地 shell、文件工具的任务。
- 真实使用持续产生 runtime cases，反过来校正 memory、tools、provider routing 与后续治理设计。

一句话：

**P3 先让 NeoMAGI 成为可用的日常工作入口，再决定哪些经验值得进入 skill / procedure / governance。**

## 2. 用户价值目标

P3 面向单用户 personal agent，不追求公网 SaaS、多用户协作或完整自治自改。

P3 要解决的用户问题：

- 日常问答可以在 NeoMAGI 内完成，并持续利用长期记忆。
- 查网页、查资料、打开 URL 时，答案带来源，可回溯。
- 可以在前端选择 OpenAI / Gemini / Claude 等 provider 与可用模型，而不丢失 session 与 memory 连续性。
- 文件、图片、网页、plot、CLI 输出等产物能被保存和引用，不堆成一个不可管理的杂物箱。
- coding 类任务可以委派给受控外部 coding agent CLI，但不暴露通用 bash。
- 复杂治理能力退到后台，不干扰 daily path。

## 3. 产品范围

### 3.1 In

- 日常模式：默认只保留聊天、记忆、工具和模型选择所需能力。
- Web search / fetch / URL fetching，且从第一天带来源。
- Claude provider 最小接入，前端可选择 provider 与服务端配置好的模型。
- Memory search 实用性增强，让真实记忆更容易被找回。
- 记忆写入使用现有 Postgres truth path 持续保存，P3 补齐 workspace projection 的标注、重建和一致性检查体验。
- 上传文件、网页来源、生成文件、日志和 sandbox 输出有稳定引用位置。
- coding 类任务可受控委派给 Claude Code CLI / Codex CLI 等外部 coding agent。
- 前端最小补齐，支撑文件上传、artifact 展示、tool log 和长任务状态。
- Daily casebook，用真实任务验证产品价值。

### 3.2 Out

- 不把 P3 定义为 self-evolution milestone。
- 不继续扩展 devcoord。
- 不继续扩展 Procedure Runtime。
- 不继续扩展 Skill Learner 自动成长路径。
- 不做通用 bash tool。
- 不做面向多用户公网 SaaS 的完整安全模型。
- 不把 PDF / image / python sandbox 一次性做成完整平台；按真实需求逐步开启。

## 4. 冻结与降级

### 4.1 Devcoord

P3 冻结 devcoord。已有协作控制能力保留，但不继续扩展，不作为 daily runtime 目标。

### 4.2 Procedure Runtime

Procedure Runtime 进入 maintenance freeze：

- 没有真实 ProcedureSpec 和真实运行 transcript 前，不新增 runtime 能力。
- P3 daily tools 不先走 Procedure Runtime。
- 等真实 daily cases 证明某类任务需要固定 state / gate / resume，再考虑提升为 ProcedureSpec。

### 4.3 Skill Learner

Skill Learner 不继续做自动成长，先改为 observe-only：

- 不自动 propose / eval / apply skill。
- 可记录 teaching / preference / workflow candidate。
- 人工 curated skill 可在 `growth_lab` 或 read-only path 中验证。
- 解冻前需要真实 teaching cases 与复用证据。

## 5. Milestones

### 5.1 P3a：Daily MVP

目标：支持日常对话聊天与基础信息获取，开始替代部分 claude.ai / ChatGPT 使用。

用户可见能力：

- 使用日常模式，复杂治理默认不干扰日常聊天。
- 在前端选择 OpenAI / Gemini / Claude 和可用模型进行基础对话。
- 同一 session 中切换 provider 后，仍能利用同一套 memory。
- `memory_search` 更容易命中真实记忆。
- `web_search` / `web_fetch` 能带来源回答网页问题。
- memory 写入沿用现有 Postgres ledger truth path，workspace projection 有明确边界并可由用户查看。
- artifacts / runs 目录约定落地，上传、网页来源和生成文件有稳定引用位置。
- daily casebook 开始积累真实使用案例。

验收：

- 日常模式下，核心聊天、memory、tools 和模型选择可用。
- OpenAI / Gemini / Claude 至少能完成基础对话验证。
- 同一 session 切换 provider / 模型后，memory recall 与 scope 行为一致。
- `web_search` / `web_fetch` 返回带来源的结果。
- memory truth 已由 Postgres ledger 承担；workspace projection 的自动生成标注、重建路径与一致性检查可验证。
- P3a 必须产出至少 30 条真实 daily cases。

### 5.2 P3b：Daily Expansion

目标：支持带文件、图片、代码外包与轻量执行的日常任务。

用户可见能力：

- 上传 PDF / 图片后，NeoMAGI 能引用文件内容参与对话。
- 生成 plot、文本、数据文件后，前端能展示或提供稳定引用。
- coding 任务可委派给 Claude Code CLI；Codex CLI 是否同期进入由 P3a cases 决定。
- Python execution / plotting 在 sandbox 中运行，并把输出作为 artifacts。
- 长任务有基本状态，不要求用户盯着空白对话等待。

验收：

- 文件上传、artifact 展示、tool log 折叠、长任务状态在前端可用。
- `claude_code` 在白名单、cwd、env、timeout、log 约束下可运行。
- `python_execute` 只在 sandbox run dir 内产生 artifacts。
- PDF / image 输入不默认写入长期 memory。
- 新增能力至少各自产生真实 daily cases，再决定是否进入 procedure / skill / governance。

## 6. 成功指标

P3 的核心成功指标不是新增抽象数量，而是真实使用强度：

- 14 天内持续使用 NeoMAGI。
- P3a 至少 30 条真实 daily cases。
- case 记录包含：任务、成功/失败、是否逃回 claude.ai / ChatGPT、缺失能力、是否产生 memory、使用的 provider / tools。
- 记忆第二天可找回。
- Web 答案能追溯来源。
- provider / 模型切换不破坏 session / memory 行为。
- daily mode 下关闭 skill / procedure / evolution 后，体验不退化。

## 7. Open Questions

- Brave Search API 的成本、限额和结果质量是否满足 personal daily use。
- Claude provider 是否在 P3a 支持 tool calling，还是先仅支持 chat / streaming。
- P3a 是否直接暴露具体 model id，还是只暴露服务端配置的 model profile。
- `memory_search` 的 PG-native topic inventory 与 per-entry retrieval tags 是否足以覆盖 P3a daily recall cases。
- embedding 是否值得作为 optional index projection，而不是 raw memory truth。
- docling 是否值得作为默认 PDF parser，还是只在复杂 PDF / OCR case 出现后引入。
- Codex CLI wrapper 是否与 Claude Code CLI 同期进入 P3b。
