---
doc_id: 019da565-26ae-76cd-9ec4-10f00a51c99f
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-19T12:59:22+02:00
---
# P3 Daily Use Architecture（草稿）

> 状态：draft
> 日期：2026-04-19
> 产品口径：见 `design_docs/phase3/p3_daily_use_roadmap_draft.md`。
> 用途：记录 P3 daily-use 补完的 high-level 技术决定，避免在 roadmap 中混入实现细节。

## 1. 架构原则

- P3 优先 daily path，不扩大 self-evolution / governance surface。
- 默认运行面是 `core loop + memory + tools + provider routing`。
- Postgres 是 memory truth；workspace 文件是 projection / export。
- Artifact metadata 用 DB 管，文件内容保留在 workspace。
- 目录结构要兼顾 agent provenance 和用户手动浏览。
- 不暴露通用 bash；对模型暴露语义化、白名单 Python tools。
- P3 禁止新增抽象，除非至少两个工具重复实现同样逻辑，且抽象能删除代码或减少权限面。

## 2. Runtime Profile

### 2.1 `daily`

默认日常模式。开启：

- `model_client`
- `session_manager`
- `tool_registry`
- `memory_searcher`
- `memory_writer`
- compaction / recall（仅在当前实现稳定时开启）

关闭：

- `evolution_engine`
- `skill_learner`
- `procedure_runtime`
- `skill_resolver` / `skill_projector`，除非已有人工 curated skills 需要 read-only 使用

### 2.2 `growth_lab`

仅用于专门实验。开启：

- `evolution_engine`
- `skill_resolver` / `skill_projector`
- `skill_learner`
- `procedure_runtime`
- wrapper / procedure governance

`growth_lab` 不作为 P3 daily use 默认路径。

## 3. Provider / Model Selection

P3 添加 Claude provider，并把前端 provider / model 选择补齐。用户需要能选择：

- provider：`openai` / `gemini` / `claude`
- provider 内可用模型或 model profile

技术决定：

- 记忆由数据库持续保存，provider 只是计算环节。
- provider / model 选择是 agent-run 级绑定：每次 `chat.send` 开始时确定，本次 run 内不切换。
- session / memory 不绑定 provider 或 model；同一 session 的相邻 runs 可以选择不同 provider / model。
- `.env` / server config 定义可用 provider、model profiles 与默认值。
- 前端只展示后端返回的 enabled profiles，不允许自由输入任意 model string。
- `chat.send` 可携带 provider 与 model profile；未携带时使用后端默认 profile。
- 记忆提取、prompt recall、session scope、tool schema 组装是跨 provider 测试重点。
- Claude provider 采用 native Anthropic SDK；统一性放在 NeoMAGI `ModelClient` adapter boundary，而不是强制所有 provider 走 OpenAI-compatible API。
- Claude provider 初期可先支持普通 chat / streaming；tool / image / structured output 由 P3-M1 具体计划按 capability 分阶段打开。
- tool calling、image support、max context 等能力必须显式标注 capability。
- provider / model 不支持当前任务所需能力时，必须显式降级或拒绝，不允许静默失败。

Provider 层次边界：

```text
Frontend
  只选择 provider / model_profile / capability，不理解 SDK 形态

Gateway / ProviderRegistry
  绑定 agent-run 的 provider + model_profile
  记录 provider / model / cost / capability
  不做 Anthropic / OpenAI 格式转换

AgentLoop
  只调用 ModelClient
  不知道后面是 OpenAI、Gemini、Claude 还是 local model

ModelClient provider adapter
  OpenAICompatModelClient
  AnthropicModelClient
  未来 GeminiNativeModelClient / LocalModelClient
  在这里做 message / tool / image / streaming 转换

SDK layer
  openai.AsyncOpenAI
  anthropic.AsyncAnthropic
```

候选请求形态：

```json
{
  "content": "...",
  "session_id": "main",
  "provider": "openai",
  "model_profile": "openai-default"
}
```

profile 返回形态可包含：

```json
{
  "providers": [
    {
      "name": "openai",
      "default_model_profile": "openai-default",
      "models": [
        {
          "profile": "openai-default",
          "model": "gpt-5-mini",
          "label": "GPT-5 Mini",
          "supports_tools": true,
          "supports_images": true,
          "max_context_tokens": null
        }
      ]
    }
  ]
}
```

约束：

- 不做会话中途 hot switch。
- 不做双 provider 并行在线竞速。
- 不让前端绕过服务端白名单直接指定未配置模型。
- 不把 LiteLLM / OpenAI-compatible proxy 作为 P3-M1 Claude 主路径；它可以作为未来扩展或 smoke path 评估。
- 每次 run 必须记录 provider / model profile / model，用于成本、debug、复现与 casebook。

## 4. Memory

### 4.1 Memory Truth

状态：已达成。生产 daily path 的机器写入 memory 已经采用 Postgres `memory_source_ledger` 作为 truth；workspace Markdown 是 best-effort human-readable projection；`memory_entries` 是 retrieval projection。

当前机制：

- `memory_append` 先写 Postgres ledger。
- DB 写入成功后，同步 append 到 workspace Markdown projection。
- DB ledger 是 truth；Markdown projection 失败不回滚 DB。
- `memory_entries` 是检索投影；增量索引由 ledger write 驱动。
- 全量 `reindex` 在 ledger 有数据时从 ledger current view 重建 `memory_entries`。

P3 不再做 memory truth 迁移。P3 只补齐 projection / export hardening：

- projection 文件必须标注：`This file is auto-generated. Manual edits will be lost.`
- 新增手动 memory projection rebuild 命令，从 ledger 重新渲染 workspace Markdown。
- 保留 doctor / parity check，必要时补 checksum 输出；检查只报告 drift，不自动修复。
- 修正文档中仍把 workspace Markdown 写成 truth 的旧表述。
- 将 no-ledger writer fallback 限定为 legacy / test-only；daily profile 必须使用 ledger-wired writer。
- 暂不需要定时任务。
- 不做 DB 与 Markdown 双主同步。
- 不把直接编辑 workspace Markdown 自动导入长期 memory。

### 4.2 Memory Search Query Expansion

P3-M1 `memory_search` query expansion 优先采用 PG-native、corpus-grounded 路线，不在每次 search hot path 默认调用 LLM 扩关键词。

默认顺序：

1. Primary tsvector search：继续使用 scope / principal / visibility 过滤后的 `memory_entries.search_vector`。
2. Low-recall OR fallback：当 primary 结果为空或明显不足时，把分词后的 query 改为 OR 语义，解决“关键词都在但没有同时出现在一条 memory”。
3. Corpus-grounded topic inventory：仍低召回且 query 是宽泛类别时，从用户自己的 memory corpus 生成 topic / keyword inventory，例如基于 `ts_stat` / `search_vector` 统计实际出现的主题词，再返回候选主题或用这些真实词做二次搜索。
4. Per-entry retrieval keywords / tags：P3-M1 可利用现有 `memory_entries.tags` 存放 indexer 阶段提取的 top-N 高信号关键词；这些 tags 是 retrieval projection，不是 memory truth，也不是完整 taxonomy。
5. LLM query expansion：只作为 explicit fallback 或 `growth_lab` 实验项，不作为 daily mode 默认路径。

约束：

- 扩展词必须尽量来自用户自己的 memory corpus，而不是模型临场猜测。
- 返回结果应包含触发的 query mode、query variants / topic candidates 和命中来源，方便调试 recall。
- `pg_trgm` 可作为 typo / 近似字符串匹配的 optional enhancement，不作为 P3-M1 主方案。
- 不把“技术”“投资”“健康”等宽泛词自动硬映射到一套全局 taxonomy；宽泛查询应优先返回用户记忆库中的实际候选主题。

Embedding 可以预留，但暂不作为 P3-M1 必选项：

- embedding 入库会把检索质量部分绑定到具体 embedding model。
- raw memory 应保持模型无关。
- embedding 的实用性、存储成本与迁移策略需要在真实 daily cases 后再决定。

## 5. Web Search / Fetch

初步选择 Brave Search API 作为 `web_search` 后端，`web_fetch` / `url_fetch` 使用受控 HTTP fetch。

`web_search` / `web_fetch` 从第一天必须携带 provenance：

- URL
- title
- fetched_at
- excerpt
- 原文证据与 model summary 明确区分

边界：

- `web_search` 返回候选来源，不把 snippet 当全文证据。
- `web_fetch` 抓取页面正文后才能生成基于来源的 summary。
- `web_fetch` 必须限制 timeout、response size、mime type。
- `web_fetch` 必须防 SSRF：禁止 `localhost`、内网 IP、metadata IP、`file://` 等非预期目标。
- fetched content 是否落盘、保存全文还是 excerpt，需要在 artifact/source index 中显式记录。

## 6. Artifact / Run Surface

### 6.1 文件系统目录

workspace 下分为 `artifacts/` 和 `runs/`。

`artifacts/` 保存长期可引用文件，默认保留：

```text
workspace/artifacts/
  uploads/
    images/
    pdf/
    docs/
    data/
    other/
  web/
    pages/
    search_results/
  generated/
    images/
    plots/
    data/
    text/
    docs/
  logs/
    cli/
    sandbox/
```

`runs/` 保存一次工具执行过程，可后续清理：

```text
workspace/runs/<run_id>/
  input/
  output/
  logs/
  manifest.json
```

目录设计口径：

- 按类型浅分目录，避免一个文件夹混入所有文件。
- 文件名使用时间前缀 + 短 id + 人可读描述。
- 暂不按 `YYYY/MM` 建深层时间目录；等 leaf 目录文件数明显过大再引入。
- artifact canonical path 一旦写入 index，不轻易移动。
- run 目录可以清理；artifact 目录默认保留。

文件名示例：

```text
20260419_124530_a1b2_original-report.pdf
20260419_125010_c9d0_translation-draft.md
20260419_130200_f31a_plot-memory-recall.png
```

### 6.2 Artifact Metadata Table

Artifact metadata 应该放单独表，不塞进 `memory_entries` 或 `memory_source_ledger`。

建议表：`artifacts`

```text
artifact_id        text primary key
kind               text        -- upload | web | generated | log
bucket             text        -- uploads/images | web/pages | generated/plots
path               text unique -- workspace-relative canonical path
original_filename  text null
display_name       text
mime_type          text
size_bytes         bigint
sha256             text
summary            text
source_run_id      text null
source_session_id  text null
principal_id       text null
visibility         text        -- private_to_principal etc.
origin_url         text null
origin_title       text null
created_at         timestamptz
updated_at         timestamptz
status             text        -- active | deleted | archived
integrity_status   text        -- ok | missing | modified | unreadable | unverified
last_verified_at   timestamptz null
metadata           jsonb
```

决定：

- 文件内容不进 DB。
- `path` 使用 workspace-relative path，不用绝对路径。
- `artifact_id` 是机器主键，文件名只服务人工浏览。
- `sha256` 在 artifact 写入或 finalize 时计算并记录，作为完整性 baseline。
- 普通 artifact 读取默认不重新计算 `sha256`，避免把日常展示 / 引用路径变成高 I/O 操作。
- 普通读取只做廉价检查：DB 记录存在、`status` 可用、path 存在；高风险输入可显式要求校验。
- `doctor` 新增 artifact integrity check；默认检查 metadata 表、path 存在性和可选抽样，`doctor --deep` 做 retained artifacts 的全量 `sha256` 校验。
- `integrity_status` 与 `last_verified_at` 由 doctor / explicit verify 更新，用于标记 missing / modified / unreadable。
- doctor 只报告 drift，不自动 repair；接受当前文件 hash、标记 missing 或清理 metadata 必须走独立命令。
- `status` 表示 artifact 生命周期，不静默删除 metadata。
- P3 daily artifacts 范围比已有 builder work memory 更宽，使用独立 `artifacts` 表 / store；不复用 builder artifact 作为总表。

显式完整性命令候选：

```bash
neomagi artifacts verify --deep
neomagi artifacts reconcile --mark-missing
neomagi artifacts accept-current-hash <artifact_id>
```

### 6.3 Tool Run Table

建议增加轻量 `tool_runs` 表，用于 long-running CLI、python sandbox、web fetch 等状态与 provenance。

```text
run_id        text primary key
tool_name     text
status        text        -- running | succeeded | failed | timed_out | cancelled
session_id    text null
principal_id  text null
cwd           text null
started_at    timestamptz
finished_at   timestamptz null
timeout_sec   int null
exit_code     int null
log_path      text null
stdout_tail   text null
stderr_tail   text null
summary       text null
metadata      jsonb
```

关系：

- `artifacts.source_run_id` 逻辑关联 `tool_runs.run_id`。
- P3 初版可先不加硬外键，避免清理 run 时破坏 artifact metadata。

### 6.4 Artifact 与 Memory 的边界

长期 memory 不等于 artifact index：

- 所有文件进入 `artifacts` 表。
- 文件进入 artifacts 不代表写入长期 memory。
- memory 只存“这个文件对用户长期有什么意义”。
- 用户明确要求保存长期意义时，才写 `memory_source_ledger`。
- `memory_source_ledger.metadata` 当前已经是 `JSONB NOT NULL DEFAULT '{}'`，schema 层支持 `artifact_ids`，不需要新增 ledger 列。
- P3-M1 需要打通受控写入路径：`memory_append` / memory writer 可接收经过校验的 `artifact_ids`，写入 ledger metadata。
- 写入前必须校验 artifact id 存在，且 principal / visibility 允许当前 memory 引用该 artifact。
- memory metadata 可带 `artifact_ids`，例如：

```json
{
  "artifact_ids": ["01HX..."],
  "relation": "supporting_source"
}
```

`artifact_ids` 初版只服务从 memory 指向 artifact。暂不新增 `memory_artifact_links` 表；等需要频繁反查“哪些 memory 引用了某 artifact”时再加。

## 7. White-Listed CLI Wrappers

P3 不提供通用 bash tool。对模型暴露受控语义工具，例如 `claude_code` / `codex_cli`，由 Python tool 把语义参数转换成真实 CLI args。

原则：

- `shell=False`
- command 必须在白名单
- cwd 必须在允许 workspace 下
- timeout 必须有默认值和上限
- stdout / stderr 截断
- env 默认清洗，不继承项目 `.env` 和 API keys
- 不允许重定向、管道、`;`、`&&`、subshell
- 写操作命令需要更高风险等级或显式确认
- 记录 command、cwd、exit code、duration、输出摘要、log path

CLI 认证策略：

- 不读取项目 `.env`。
- 不继承全量环境变量。
- 只允许显式 env allowlist。
- Claude Code / Codex CLI 优先使用各自 CLI 登录状态或专用配置，不从项目密钥泄漏。

示例：

```python
# src/tools/builtins/claude_cli.py
class ClaudeCliTool(BaseTool):
    name = "claude_code"
    description = "Delegate a coding task to Claude Code CLI."
    schema = {
        "task": str,
        "cwd": str | None,
        "allowed_tools": list[str] | None,
        "max_turns": int,
        "timeout_sec": int,
        "context_files": list[str] | None,
    }
```

`context_files` 约束：

- 必须位于允许 workspace root 下。
- resolve 后不得越界。
- 禁止 `.env`、token、credential、key 等敏感文件。
- 限制文件数量和总大小。
- 调用前记录 manifest。

允许的最小共享层：多个 CLI / sandbox 工具需要的 `SafeProcessRunner`。不提前建设通用 command runtime。

## 8. File, Image, PDF, Python

这些能力是 daily use 的关键面，但按真实需求分阶段实现。

- 图片输入：OpenAI SDK 支持只是 provider 层前提；产品层仍需前端上传、artifact 存储、multimodal protocol、provider capability 判断、历史消息引用与 memory 边界。
- PDF parser：docling 可作为候选，但先按真实需求决定是否引入；简单 text PDF 可先做轻量 extraction。
- `python_execute`：目标是 podman sandbox MVP，而不是 notebook / 多语言执行平台；按需求实现。
- plotting：输出图片进入 artifacts，由前端展示。

## 9. Privacy 与部署边界

P3 是 personal-only 阶段，不做复杂 DLP。

短期假设：

- 外部 API 处理风险由用户自行接受。
- 涉及医疗、证件、财务、高度隐私材料时，由用户手动判断是否发送给外部 API。
- 不把 provider retention 当作架构安全保证；在扩大使用前重新核对 Claude / OpenAI / Gemini 当前条款。

部署方案：

- A6000 server 作为主要运行环境。
- WebChat 不直接暴露公网。
- 本地使用 SSH channel 转发 HTTP WebChat。
- 远程使用 Tailscale 登录。
- 可考虑让 FastAPI 绑定到 Tailscale interface，默认访问方式为 `http://<tailscale-hostname>:19789`。
- Gateway 端口以 `settings.gateway.port` / `GATEWAY_PORT` 为 SSOT；如覆盖端口，部署文档和访问地址必须以实际配置为准。

## 10. Frontend 最小面

前端至少需要：

- image artifact 展示
- PDF 上传入口
- tool log 可折叠
- long-running CLI 状态
- generated plot 图片展示

P3 不追求复杂 UI。目标是让新增工具真正可用，而不是只在后端存在。

## 11. Open Questions

- artifact index 使用独立 SQLAlchemy model + Alembic migration，还是先 idempotent startup DDL 后补 migration。
- `tool_runs` 是否需要硬外键，还是 P3 初版只做逻辑关联。
- `artifacts/logs/*` 与 `runs/<run_id>/logs` 是否需要复制，还是只在 artifacts 表里引用 run log。
- artifact 清理策略何时引入，以及是否需要 `status=archived`。
- memory metadata 中的 `artifact_ids` 是否足够，还是需要 `memory_artifact_links` 表。
