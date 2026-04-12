---
doc_id: 019d8154-f2d2-7bf3-9a5e-a8ec17b1ce2a
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-12T12:55:21+02:00
---
# P2-M3c 实现计划：Retrieval Quality & Federation-Compatible Policy Hook

> 状态：approved
> 日期：2026-04-12
> 输入：`design_docs/phase2/p2_m3_architecture.md` Section 3 (P2-M3c)
> 架构基础：ADR 0034 (dmScope alignment), ADR 0059 (Shared Companion boundary, amended), ADR 0060 (Memory Source Ledger), ADR 0061 (Phase 2 Scope Collapse)
> 前置完成：P2-M3a (Auth & Principal Kernel), P2-M3b (Memory Ledger & Visibility Policy)
> 实现对齐：已按 `dev_docs/logs/phase2/p2-m3a_auth-principal-kernel_2026-04-12.md` 与 `dev_docs/logs/phase2/p2-m3b_memory-ledger-visibility-policy_2026-04-12.md` 的实际交付修订。

## 0. 目标

用已知 miss case 建立轻量 retrieval regression，提升检索命中率；建立统一的 visibility policy checkpoint，为未来 federation-compatible Shared Companion 留下最小安全地基。

回答的问题：**memory 检索为什么没命中？共享边界为什么允许/拒绝？**

完成后：
- 已知自然语言检索 miss case 有稳定回归测试，至少部分被消除
- 检索质量具备可衡量的 lexical 增强（CJK 分词 + query normalization）；vector retrieval 产出数据驱动的启用/不启用决策（不在 M3c 实现）
- 统一 visibility policy checkpoint `can_read` / `can_write` 存在并默认 fail-closed；M3b 已有的 searcher / PromptBuilder / writer ad-hoc 规则被收敛到同一语义
- `shared_in_space` 为 reserved / deny-by-default；所有 shared-space 读写返回明确拒绝原因，且 `metadata.shared_space_id` 不能绕过 policy
- 所有 retrieval 先通过 scope / principal / visibility filter，再进入 ranking；searcher 与 PromptBuilder daily notes 的过滤语义保持一致
- policy hook 设计兼容联邦式、hosted shared-space 和混合模式，不深度绑定单实例多 principal

## 1. 当前基线

> P2-M3a 和 P2-M3b 已完成。以下基线按两份实现日志与当前代码对齐。

| 组件 | P2-M3b 后实际状态 |
|------|-------------------|
| `MemorySearcher` (`src/memory/searcher.py`) | tsvector + `ts_rank`，`plainto_tsquery('simple', ...)`；scope_key 必选；已接收 `principal_id` 并做 visibility allowlist |
| `memory_entries.search_vector` | `setweight(title, 'A') \|\| setweight(content, 'B')` trigger；无 embedding 列 |
| `memory_source_ledger` | 含 `principal_id`, `visibility` 列（P2-M3b 新增） |
| `memory_entries` | 含 `principal_id`, `visibility` 列（P2-M3b 新增，从 ledger 传播） |
| Visibility filtering | searcher、PromptBuilder daily notes、memory write 已有 ad-hoc principal + visibility 过滤；尚无统一 `can_read` / `can_write` checkpoint |
| `MemoryWriter` / `MemoryLedgerWriter` | `append_daily_note()` 与 ledger `append()` 已接收并传播 `principal_id` / `visibility`；`MemoryWriter` 已拒绝 unknown / `shared_in_space`，ledger 本身尚无 policy guard |
| `MemoryIndexer` / restore / CLI reindex | 已有 `reindex_from_ledger()`；CLI 与 restore 在 ledger 非空时走 ledger-based 全 scope 重建；已传播 `principal_id` / `visibility` |
| `ToolContext` | 含 `principal_id`（P2-M3a）；M3b 已由 memory_append / memory_search 透传 |
| `shared_in_space` | reserved 概念；M3b 已在 writer/searcher/PromptBuilder 中 deny-by-default，但没有统一拒绝 reason，也没有 `metadata.shared_space_id` guard |
| Retrieval 回归 | 无；已知 miss case 未系统收集 |
| Vector / embedding | pgvector 扩展已安装（tech stack）；无 embedding 列、无 embedding 生成管线 |

### M3b / M3c Ownership 划分

为避免两个子阶段改同一批文件且验收语义不一致，明确划分如下：

| 关注面 | M3b 拥有 | M3c 拥有 |
|--------|----------|----------|
| **Schema** | ledger + memory_entries 增加 `principal_id`/`visibility` 列 | memory_entries 增加 `search_text` 列（CJK 分词） |
| **Write path** | ledger writer 接收 `principal_id`/`visibility`；`MemoryWriter` 传播并做基础 fail-closed；indexer 传播到 memory_entries | 抽取 `can_write()` policy checkpoint；替换 ad-hoc writer 常量；增加 ledger-level defense、`metadata.shared_space_id` guard、统一 reason/error/log |
| **Read path** | searcher 已接收 `principal_id` 并做 `visibility IN (...)` + principal filter；PromptBuilder daily notes 已做等价过滤 | `can_read()` policy 规则定义；将 searcher 与 PromptBuilder 过滤收紧为同一 V1 语义（COALESCE defensive NULL、`shareable_summary` same-principal-only）；direct-read 若出现则复用 `can_read()`；retrieval quality 增强 |
| **Reindex** | `reindex_from_ledger()`、restore、CLI ledger-based rebuild 已存在，并传播 principal_id/visibility | 在现有 ledger reindex / direct index / workspace parse 路径中补 `search_text` CJK 分词；不重新定义 reindex source 策略 |
| **Policy 函数** | ad-hoc 常量与分支逻辑散落在 writer/searcher/PromptBuilder/tests | `can_read()`/`can_write()` 定义、实现、集成、测试；暴露 policy version 常量用于日志与 SQL/函数一致性测试 |
| **Retrieval quality** | 不涉及 | query normalization、Jieba 分词、regression fixture、可选 vector |

M3b 在 searcher 中实现了基础过滤：
- 认证请求：`principal_id = :pid OR principal_id IS NULL`
- 匿名请求：`principal_id IS NULL`
- visibility allowlist：`visibility IN ('private_to_principal', 'shareable_summary')`

这意味着当前 SQL 对认证请求仍可能返回 `principal_id IS NULL AND visibility='shareable_summary'` 的 legacy/anonymous summary；PromptBuilder daily notes 的 no-principal metadata 也有同类语义。M3c 在 read path 收紧为：
- `COALESCE(visibility, 'private_to_principal')` 处理 legacy NULL 行
- `shareable_summary` 收紧为 `principal_id = :ctx_principal_id`（same-principal only）
- no-principal legacy 条目仅按 `private_to_principal` 兼容处理，不自动成为匿名或跨 principal summary

规则 0（shared_space_id guard）**不进入 SQL WHERE**：`memory_entries` 没有 `shared_space_id` 列，不做 JSON 投影或加列。规则 0 只存在于 `can_read()`/`can_write()` 纯函数中，覆盖 write 路径和 direct-read 路径。Search SQL 路径天然不返回 `shared_in_space` 行（被 visibility whitelist 排除），无需额外 shared_space_id 过滤。

M3c 不从零重建 searcher / writer / reindex，而是在 M3b 已交付路径上做增量替换与补强。

## 2. 核心决策

### D1：Retrieval Regression — miss case 优先，fixture-driven

已知检索质量问题的根源：
1. `plainto_tsquery('simple', ...)` 对中文分词能力弱：不做 Jieba 分词，只靠空格和标点切分
2. 无同义词/近义词扩展：用户用不同措辞搜索同一概念会 miss
3. 无 embedding / 语义搜索：纯词匹配无法捕获语义相似性
4. title 权重 (A) vs content 权重 (B) 是固定的，无法针对不同查询类型调优

策略：
- **先收集 miss case，后修复**。在 `tests/fixtures/retrieval_regression/` 建立 JSON fixture，每个 case 包含 `indexed_entries` + `query` + `expected_entry_ids`（至少包含）。使用 JSON 而非 YAML，避免引入 PyYAML 依赖。
- fixture 来源：手动整理 Phase 1 / Phase 2 实际使用中的 miss case + 构造的中英文边界 case。
- 测试用 `MemorySearcher` 真实执行，不 mock（需要 PostgreSQL test fixture）。
- V1 目标：≥ 10 个 miss case fixture，至少 70% 命中（pass）。
- 每个 miss case 必须标注 `category`（如 `cjk_tokenization`, `synonym`, `semantic_gap`, `partial_match`）以追踪根因分布。

放弃：
- 不做端到端 LLM-judged retrieval eval（复杂度过高，且不确定性大）
- 不做 benchmark 排行榜或 NDCG/MRR 指标框架（V1 只需 hit/miss 二元判定）

### D2：Lexical 增强 — query normalization + Jieba CJK 分词

当前 `plainto_tsquery('simple', ...)` 把查询按空格/标点拆分，对中文几乎无效。

增强路径（按优先级）：

**D2a：Query normalization**（必做）
- 在 `MemorySearcher.search()` 入口增加 query preprocessor：
  - 去除多余空白和标点噪声
  - 中文关键词提取（Jieba `cut_for_search`）→ 用空格连接 → 构造更好的 tsquery
  - 英文 lowercase + 基础 stemming（可选，V1 可不做）
- Query preprocessor 是纯函数，不依赖外部服务。
- 若 Jieba 不在当前依赖中，新增 `jieba` 到 `pyproject.toml`（轻量纯 Python 包）。
- Jieba 首次 `cut_for_search()` 会触发字典加载，可能带来约 1-2 秒冷启动；`query_processor.py` 应提供 `warmup_jieba()`（内部调用 `jieba.initialize()`），并在 gateway lifespan / reindex CLI 启动时预热，避免首次用户搜索承担加载延迟。测试可直接调用纯函数，不依赖预热。

**D2b：Index-time CJK 分词**（必做）
- 修改 `search_vector` trigger：对 content 先做 Jieba 分词再构造 tsvector。
- 实现方式：PostgreSQL trigger 调用 `to_tsvector('simple', jieba_segmented_text)`，其中 Jieba 分词在 Python projection/index 层完成（`MemoryIndexer` direct index、ledger reindex、workspace fallback parse），存储分词后的 `search_text` 列。
- **不在 PostgreSQL 内部调用 Jieba**（避免 PL/Python 依赖）：分词在 Python 层完成，存入 `search_text` 列，trigger 用 `search_text` 替代 `content` 生成 tsvector。
- 新增 `memory_entries.search_text` 列（TEXT, nullable）：存储分词后的文本，供 trigger 使用。

**D2c：Query expansion — tsquery OR 组合**（可选，视 miss case 回归结果决定）
- 对 query 中的关键词生成同义词候选，用 `|`（OR）组合为 tsquery。
- **注意**：OR expansion 不能继续用 `plainto_tsquery`（不支持 `|` 操作符），必须切换到 `to_tsquery` 并对用户输入严格 escape（防注入），或使用 `websearch_to_tsquery`（PG 11+，支持 `OR` 关键字）。
- V1 同义词表可以是硬编码 dict（不做外部服务）。
- 只在 miss case 回归显示 synonym gap 是主要问题时才实施。

放弃：
- ParadeDB `pg_search` BM25：仍在 tech stack 路线中但 V1 不依赖（安装复杂度、版本兼容性风险）
- 自建 ICU tokenizer：运维复杂度过高
- 更换 PostgreSQL text search config 为自定义 config：V1 继续用 `simple` + Python 层分词

### D3：Vector Retrieval — M3c 产出决策，不在 M3c 内实现

策略：**Vector retrieval 不在 M3c Gate 中实现**。M3c Gate 2 的产出是基于 regression 数据的"启用/不启用决策 + follow-up issue"。

原因：
- 10 个 miss case 不足以可靠判定 `semantic_gap > 30%` 阈值
- Vector retrieval 引入 embedding dimension 选择、异步生成管线、Ollama/OpenAI model 依赖、hybrid rank normalization 等复杂度，不适合作为条件分支"启用即实现"
- 若 vector SQL 也必须先带 scope/principal/visibility WHERE 再做相似度排序（不能先全局 top-N 再过滤），需要额外的 query planner 考量

**M3c Gate 2 产出**：
- 统计 regression fixture 中各 category 分布（`cjk_tokenization`, `synonym`, `semantic_gap`, `partial_match`）
- 若 `semantic_gap` 类 miss 占比显著，产出 follow-up issue/plan 描述 vector retrieval 方案（embedding model、dimension、index type、hybrid ranking、visibility filter 的 SQL 集成）
- 该 follow-up 可作为 P3 候选项或 P2-M3 post-review fix，不阻塞 M3c 验收

**放弃**：在 M3c 中直接实现 embedding 列、embedding 生成管线和 hybrid ranking。

### D4：Visibility Policy Checkpoint — `can_read` / `can_write` 简单函数

架构文档要求"统一 visibility policy checkpoint"，实现为简单函数，不做策略注册表或 adapter 模式。M3b 已在 writer/searcher/PromptBuilder 中实现 ad-hoc 分支；M3c 的重点是把这些语义抽取为单一 policy surface，并补上统一 reason、日志字段和 `metadata.shared_space_id` guard。

**Policy version**：

```python
MEMORY_VISIBILITY_POLICY_VERSION = "v1"
```

该常量定义在 `src/memory/visibility.py`，由 `MemorySearcher`、`PromptBuilder` 和测试导入。SQL WHERE、PromptBuilder 过滤、`can_read()` / `can_write()` 测试和 audit 日志必须显式使用或断言同一个 policy version，避免后续规则漂移。

**接口定义**：

```python
@dataclass(frozen=True)
class PolicyContext:
    """调用 visibility policy 时的上下文。"""
    principal_id: str | None          # 当前请求者的 principal
    scope_key: str                    # 当前 scope
    # 未来可扩展: shared_space_id, role, etc.

@dataclass(frozen=True)
class PolicyDecision:
    """Policy 判定结果。"""
    allowed: bool
    reason: str                       # 机器可读的拒绝/允许原因
    # 允许原因示例: "owner_private_access", "same_principal"
    # 拒绝原因示例: "shared_space_policy_not_implemented",
    #              "membership_unavailable", "confirmation_missing",
    #              "visibility_mismatch", "principal_mismatch"

def can_read(context: PolicyContext, entry: MemoryPolicyEntry) -> PolicyDecision:
    """判断 context 下的请求者能否读取 entry。"""
    ...

def can_write(context: PolicyContext, proposed: MemoryPolicyEntry) -> PolicyDecision:
    """判断 context 下的请求者能否写入 proposed entry。"""
    ...
```

**`MemoryPolicyEntry`** — policy 判定所需的 entry 最小视图：

```python
@dataclass(frozen=True)
class MemoryPolicyEntry:
    """Visibility policy 判定所需的 memory entry 最小视图。"""
    entry_id: str
    owner_principal_id: str | None    # entry 的 owner（读时=写入者; 写时=proposed owner）
    visibility: str                   # "private_to_principal" | "shareable_summary" | "shared_in_space"
    scope_key: str
    shared_space_id: str | None = None  # reserved, 当前始终 None
```

**语义区分**：`context.principal_id` 是请求者（requester），`entry.owner_principal_id` 是条目 owner。两者必须独立，避免自证通过。

**V1 Policy 规则**（硬编码，不做配置化）：

**规则 0（最高优先级前置 deny）**：`shared_space_id` guard
- `can_read`：若 `entry.shared_space_id is not None` → **始终拒绝**，reason = `"membership_unavailable"`
- `can_write`：若 `entry.shared_space_id is not None` → **始终拒绝**，reason = `"membership_unavailable"`
- 此规则在任何 visibility 分支之前执行，防止调用方用 `visibility='private_to_principal'` + `shared_space_id` 绕过 shared-space deny

1. **`private_to_principal`**（默认 visibility）：
   - `can_read`：只允许 `context.principal_id == entry.owner_principal_id` 或 `entry.owner_principal_id is None`（匿名/legacy 数据）
   - `can_write`：
     - 匿名请求者（`context.principal_id is None`）只允许写入 `entry.owner_principal_id is None` 的条目
     - 认证请求者只允许写入 `entry.owner_principal_id == context.principal_id` 的条目
     - **不允许**认证请求者写入 owner 为其他 principal 的条目，也不允许匿名请求者写入有 owner 的条目

2. **`shareable_summary`**：
   - `can_read`：只允许 `context.principal_id == entry.owner_principal_id`（V1 same-principal 可读；跨 principal 读取需要 confirmed/published metadata，留给 P3+，与 ADR 0059 "V1 至少要求来源 principal 明确确认" 对齐）
   - `can_write`：只允许 `context.principal_id == entry.owner_principal_id`（只有 owner 可创建自己的 summary）
   - 匿名请求者不能读写 `shareable_summary`

3. **`shared_in_space`**（reserved / deny-by-default）：
   - `can_read`：**始终拒绝**，reason = `"shared_space_policy_not_implemented"`
   - `can_write`：**始终拒绝**，reason = `"shared_space_policy_not_implemented"`

4. **未知 visibility 值**：fail-closed，reason = `"unknown_visibility_value"`

5. **`visibility is None`（defensive）**：treated as `"private_to_principal"`，使用规则 1。M3b schema 为 `NOT NULL DEFAULT 'private_to_principal'`，正常 DB 路径不产生 NULL；此分支仅作 defensive 保护。

6. **匿名请求者总结**（`context.principal_id is None`）：
   - 只能读写 `visibility = "private_to_principal"` 且 `entry.owner_principal_id is None` 的条目
   - 不能读写 `shareable_summary`（没有 principal identity 无法验证 ownership）

**放弃**：
- 策略注册表 / PolicyRegistry pattern：当前只有一种 policy，不做抽象
- RBAC / permission model：V1 只有 owner 角色
- 可配置 policy 文件：硬编码规则更易审计
- Adapter 模式：不做 pluggable policy framework
- 在 policy 纯函数内写日志或访问 DB：避免 I/O 副作用和 import cycle

### D5：Searcher 集成 visibility filter — filter-then-rank

所有 retrieval 必须先通过 visibility filter，再进入 ranking。

**实现位置**：`MemorySearcher` + `PromptBuilder._filter_entries()`。

M3b 已经在两个路径都实现了基础过滤；M3c 不新增 `principal_id` 参数或 `MemorySearchResult.visibility` 字段，而是替换现有 WHERE / metadata 过滤条件，使 SQL search 与 daily notes injection 都符合同一 V1 policy。

**两种实现策略**：

**策略 A（SQL WHERE 过滤，首选）**：
- 在 `_build_search_sql()` 中替换 M3b 的基础 visibility WHERE 条件
- 利用 P2-M3b 在 `memory_entries` 上新增的 `principal_id` / `visibility` 列
- SQL 层过滤效率最高；但需要把 policy 规则翻译为 SQL 条件

**策略 B（Python 层 post-filter）**：
- SQL 查询返回候选集后，对每个 result 调用 `can_read()` 过滤
- 灵活但有效率问题（需要 over-fetch）

**选择策略 A**：
- V1 policy 规则足够简单，可直接翻译为 SQL WHERE
- 避免 over-fetch 和额外 round-trip
- `can_read()` 函数仍然存在，用于非 SQL 路径（如按 entry_id 直接读取 ledger、API / CLI 级别的权限检查）；search 路径不调用 `can_read()`，因为 SQL WHERE 已等价实现相同规则
- **SQL / PromptBuilder / `can_read()` 规则一致性**：三者必须基于相同的 policy version 常量；Slice D 测试同时覆盖 SQL 路径、workspace metadata 路径和 `can_read()` 路径对所有 visibility 值的一致性

SQL 层过滤示例：
```sql
-- COALESCE 处理 legacy 数据（visibility IS NULL → treated as 'private_to_principal'）
-- :ctx_principal_id may be NULL for anonymous requests. The equality checks
-- intentionally rely on SQL NULL semantics (= NULL → UNKNOWN, not TRUE):
-- anonymous callers only pass the explicit principal_id IS NULL legacy branch,
-- and never match shareable_summary. Do not replace with IS NOT DISTINCT FROM.
AND (
    (COALESCE(visibility, 'private_to_principal') = 'private_to_principal'
     AND (principal_id = :ctx_principal_id OR principal_id IS NULL))
    OR
    (visibility = 'shareable_summary'
     AND principal_id = :ctx_principal_id)  -- V1: same-principal only
)
-- shared_in_space / unknown visibility: 不出现在 OR 分支中 → 自动排除
-- visibility IS NULL: COALESCE 归入 private_to_principal 分支
```

**Defensive NULL 处理**：M3b schema 为 `visibility NOT NULL DEFAULT 'private_to_principal'`，migration 会 backfill 现有行，正常 DB 路径不产生 NULL。SQL 中的 `COALESCE` 和 `can_read()` 中的 NULL 分支仅作 defensive 保护（防止非 migration 路径手动插入或 schema 降级）。Slice D 测试中 visibility=NULL case 放在 `can_read()` 纯函数单元测试中覆盖，**不**作为 DB integration fixture（NOT NULL 约束会阻止插入）。

### D6：Retrieval reindex 从 ledger current view 重建

P2-M3b 已交付 `MemoryIndexer.reindex_from_ledger()`，并且 CLI / restore 在 ledger 非空时使用 ledger-based 全 scope 重建。M3c 不重新创建这条路径，只在现有 reindex / direct index / workspace parse 路径中补 `search_text` CJK 分词与触发器兼容。

**现有 `reindex_from_ledger()` 扩展要求**：
- 从 `memory_source_ledger` current view 读取现有字段（已包含 `principal_id`, `visibility`）
- 对每条 entry 做 Jieba 分词 → 写入 `memory_entries.search_text`
- 保持 M3b 已验证的 `scope_key=None` 全 scope 语义，避免恢复或 CLI reindex 丢弃非 main scope
- 保持 curated memory（`MEMORY.md`）从 workspace 文件重建；curated memory 是人工编辑的结构化文件，不经过 ledger 写入路径
- 不新增 `reindex --source` CLI 参数，除非另有独立 CLI 计划；当前 CLI 语义是 ledger 非空则 ledger-based，否则 workspace fallback

### D7：`shared_in_space` reserved 语义实现

**metadata 预留**：
- `memory_source_ledger.metadata` JSONB 中可包含 `shared_space_id` 字段（reserved）
- 不做 `shared_space_id` 列（不做规范化关系模型）
- 任何写入带有 `shared_space_id` 的 entry 都被 `can_write()` 拒绝

**Tool 层拦截**：
- `MemorySearchTool.execute()` 结果中不应出现 `shared_in_space` entry（SQL WHERE 已排除）
- 若通过 API / CLI 直接查询到 `shared_in_space` entry，返回时附带 policy decision reason

**Audit trail**（区分两种 read 路径）：

- **Search 路径**（`MemorySearcher`）：SQL WHERE 直接排除不可见行，不产生逐条 `PolicyDecision`。Search 完成后记录一条 `memory_search_filtered` structlog 事件（info 级别），包含 `query`（截断）、`scope_key`、`principal_id`、`result_count`、`visibility_policy_version="v1"`。这足以审计"谁搜了什么、用了哪版过滤规则、返回多少结果"，但不逐条记录被排除的行（效率和隐私考量）。
- **Direct-read 路径**（API / CLI 按 entry_id 直接读取 / ledger 直接查询）：调用 `can_read()` 验证后，若 `allowed=False`，调用方产生 `visibility_policy_denied` structlog 事件（info 级别），包含 `entry_id`、`principal_id`、`visibility`、`reason`。
- **Write 路径**（`MemoryLedgerWriter`）：`can_write()` 返回 `allowed=False` 后，产生 `visibility_policy_denied` structlog 事件（与 direct-read 格式一致）。
- `can_read()`/`can_write()` 本身是纯函数，不直接写日志（见 Slice C 实现说明）。
- 不做持久化 audit 表（V1 日志即审计）。

### D8：Memory write path 集成 `can_write` — 写入前 policy check

**修改路径**：
- `MemoryWriter` 已有基础 fail-closed 检查；M3c 将其替换为 `can_write()`，保持 public 行为兼容
- `MemoryLedgerWriter.append()` 已接收 `principal_id` / `visibility`；M3c 在 ledger 层也调用 `can_write()` 作为 defense-in-depth，防止绕过 `MemoryWriter`
- 若 `can_write().allowed == False` → 拒绝写入，返回错误（不静默丢弃）
- 默认所有写入的 visibility 为 `"private_to_principal"`
- V1 不支持用户在 tool 调用中指定 visibility（避免权限提升）
- `metadata.shared_space_id` 必须在 ledger 层参与 policy 判定；即使 `visibility='private_to_principal'` 也要拒绝带 shared space metadata 的写入

## 3. 实现切片

### Slice A：Retrieval Regression Fixture 框架

**新增文件**：
- `tests/fixtures/retrieval_regression/README.md`：fixture 格式说明
- `tests/fixtures/retrieval_regression/cases.json`：≥ 10 个 miss case（使用 JSON 格式，无需额外依赖；当前 `pyproject.toml` 无 PyYAML）
- `tests/test_retrieval_regression.py`：pytest parametrize 读取 fixture，对每个 case 执行 real search

**Fixture 格式**（JSON）：

```json
{
  "cases": [
    {
      "id": "cjk_basic_01",
      "category": "cjk_tokenization",
      "description": "中文关键词 '记忆架构' 搜索应命中含该词的条目",
      "indexed_entries": [
        {
          "entry_id": "test-001",
          "content": "NeoMAGI 的记忆架构基于 PostgreSQL 的 tsvector 全文搜索",
          "title": "Memory Architecture",
          "scope_key": "main"
        }
      ],
      "query": "记忆架构",
      "expected_entry_ids": ["test-001"],
      "min_score": 0.0
    },
    {
      "id": "synonym_01",
      "category": "synonym",
      "description": "搜索 '数据库' 应能命中含 'PostgreSQL' 的条目"
    },
    {
      "id": "semantic_gap_01",
      "category": "semantic_gap",
      "description": "搜索 '怎么存储用户信息' 应命中 principal/session 相关条目"
    }
  ]
}
```

**测试结构**：
- 每个 test 用真实 PostgreSQL fixture（`db_session_factory` conftest）
- 先插入 `indexed_entries` → 执行 `MemorySearcher.search()` → 断言 `expected_entry_ids` 全部命中
- 标记 `@pytest.mark.retrieval_regression` **和** `@pytest.mark.integration`（后者确保 conftest 的自动 truncate cleanup 生效）
- 每个 parametrized case 开头做 `DELETE FROM memory_entries` 表级清理，防止 case 之间交叉污染（不依赖 conftest 的 fixture 级别清理时序）
- 初始运行时允许部分 case `xfail`（标注为 known miss），修复后移除 xfail

**依赖 & 配置**：
- `pyproject.toml`：在 `[tool.pytest.ini_options]` markers 中注册 `retrieval_regression`，避免 unknown marker warning
- 无额外依赖（JSON 标准库 `json` 即可）

**测试**：
- 框架本身的 meta-test：验证 fixture loader 正确解析 JSON、parametrize IDs 正确

### Slice B：Query Normalization + Jieba 分词

**新增文件**：
- `src/memory/query_processor.py`：query preprocessing 纯函数

**修改文件**：
- `pyproject.toml`：新增 `jieba` 依赖；`[tool.pytest.ini_options]` markers 注册 `retrieval_regression: "Memory retrieval regression tests"`
- `src/memory/searcher.py`：`search()` 入口调用 query processor
- `src/memory/indexer.py`：`index_entry_direct()` / `_persist_entries()` 写入时做 Jieba 分词，填充 `search_text`
- `src/gateway/app.py`：lifespan 中调用 `warmup_jieba()`，避免首次 WebChat 搜索承担 Jieba 字典加载
- `src/backend/cli.py`：`reindex` 执行前调用 `warmup_jieba()`，让 reindex 启动阶段显式承担分词器预热

**`query_processor.py` API**：

```python
def normalize_query(query: str) -> str:
    """Query normalization: CJK segmentation + noise removal.

    1. Strip excess whitespace and punctuation noise
    2. Detect CJK characters → Jieba cut_for_search → join with space
    3. Non-CJK parts: lowercase, preserve as-is
    4. Return normalized query string for plainto_tsquery
    """

def segment_for_index(text: str) -> str:
    """Index-time segmentation: produce space-separated tokens for tsvector.

    Used to populate memory_entries.search_text column.
    """

def warmup_jieba() -> None:
    """Preload Jieba dictionary to avoid first-search latency."""
```

**DB schema 变更**：
- `memory_entries` 新增 `search_text` 列（TEXT, nullable）：存储 content 经 Jieba 分词后的文本
- 修改 trigger 目标表达式，保留现有 title A 权重：
  ```sql
  NEW.search_vector :=
      setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
      setweight(to_tsvector('simple', coalesce(NEW.search_text, NEW.content, '')), 'B');
  ```
  - title 仍为 A 权重（不做分词；标题通常短且已是可匹配关键词）
  - search_text 为 B 权重（CJK 分词后的 content）；若 search_text 为 NULL 则 fallback 到原始 content
  - 这确保 CJK 分词增强不会导致标题命中和排序回退
- Migration：`alembic/versions/<hash>_add_search_text_column.py`
- **Trigger/function 更新策略**：
  - PostgreSQL 支持 `CREATE OR REPLACE FUNCTION`，不支持 `CREATE OR REPLACE TRIGGER`。
  - Migration 中保持 alembic 既有 trigger function 名称（当前为 `{SCHEMA}.memory_entries_search_trigger()`），用 `CREATE OR REPLACE FUNCTION` 更新函数体，让既有 trigger 继续绑定到同名函数。
  - `ensure_schema()` 中保持 fresh DB 路径使用的函数名（当前为 `{schema}.memory_entries_search_vector_update()`），同样用 `CREATE OR REPLACE FUNCTION` 更新函数体。
  - 只要函数名不变，不需要 `DROP TRIGGER`；只有在确实更换 trigger 名称或绑定函数时，才使用 `DROP TRIGGER IF EXISTS ...` + `CREATE TRIGGER`。
- **`ensure_schema()` 同步更新**：`src/session/database.py` 中 `memory_entries` 的 idempotent DDL 必须包含 `search_text` 列和更新后的 trigger（fresh DB 路径）
- **Preflight 检查更新**：`src/infra/preflight.py` 验证 `search_text` 列存在且 trigger 版本正确
- **Restore 后回填**：`scripts/restore.py` 恢复序列中，在 reindex 步骤调用 `reindex_from_ledger()`（或 `reindex_all()`），确保 `search_text` 列被正确填充；不做单独的 backfill migration（reindex 即回填）
- **现有行回填策略**：已有 `memory_entries` 行的 `search_text` 为 NULL → trigger fallback 到 `content` 保证搜索不中断；下次 `reindex` 执行时统一回填 CJK 分词后的 `search_text`

**测试**：
- `tests/test_query_processor.py`：CJK 分词、英文 lowercase、混合语言、空输入、标点处理
- `tests/test_query_processor.py` 或启动路径测试：`warmup_jieba()` 可重复调用且无副作用
- `tests/test_search_text_schema.py`：fresh DB (ensure_schema) 包含 `search_text` 列和更新后 trigger
- Retrieval regression 中 `cjk_tokenization` 类 case 从 xfail 变为 pass

### Slice C：Visibility Policy Checkpoint

**新增文件**：
- `src/memory/visibility.py`：`PolicyContext`, `PolicyDecision`, `MemoryPolicyEntry`, `can_read()`, `can_write()`

**实现**：
- **纯函数**，无 I/O，无外部依赖，**无日志副作用**
- 每个 visibility 值一个分支，unknown 值 fail-closed
- `shared_in_space` 始终返回 `PolicyDecision(allowed=False, reason="shared_space_policy_not_implemented")`
- `shared_space_id is not None` 在任何 visibility 分支之前 fail-closed（规则 0）
- **日志由调用方负责**，按路径区分：
  - `MemorySearcher`（search 路径）：SQL WHERE 直接过滤，不调用 `can_read()`，search 完成后记录 `memory_search_filtered`（含 query、principal_id、policy_version、result_count）
  - Direct-read 调用方（API / CLI 按 entry_id 读取）：调用 `can_read()` 后若 `allowed=False`，记录 `visibility_policy_denied`（含 entry_id、principal_id、visibility、reason）
  - `MemoryLedgerWriter`（write 路径）：调用 `can_write()` 后若 `allowed=False`，记录 `visibility_policy_denied`
  - policy 函数本身不引入 structlog 依赖

**测试**：
- `tests/test_visibility_policy.py`：覆盖所有 visibility 值 × principal 组合
  - **规则 0 guard**：`private_to_principal` + `shared_space_id` → deny（`membership_unavailable`）；`shareable_summary` + `shared_space_id` → deny；任意 visibility + `shared_space_id` 都先被规则 0 拦截
  - `private_to_principal`: owner 读自己 → allow; 匿名读 legacy(owner=None) → allow; 其他 principal 读 → deny
  - `private_to_principal` write: 认证者写自己 → allow; 认证者写他人 → deny; 匿名写 owner=None → allow; 匿名写有 owner → deny
  - `shareable_summary`: same-principal 读 → allow; 跨 principal 读 → deny; 匿名读 → deny; 非 owner write → deny
  - `shared_in_space`: 始终 deny + correct reason
  - unknown visibility: fail-closed
  - `visibility=None` (defensive): treated as `private_to_principal`，适用规则 1（M3b schema 为 NOT NULL，此 case 仅在纯函数单元测试中覆盖）
  - requester/owner 独立性: `context.principal_id != entry.owner_principal_id` 的组合全覆盖

### Slice D：Read Path Visibility Filter 收紧

**修改文件**：
- `src/memory/searcher.py`：替换 M3b `_build_search_sql()` 的基础 visibility WHERE；保留现有 `search(principal_id=...)` 签名
- `src/agent/prompt_builder.py`：将 `_filter_entries()` 的 metadata 过滤收紧到同一 policy 语义，避免 daily notes injection 与 searcher 结果不一致
- `src/tools/builtins/memory_search.py`：仅在现有 principal 透传被破坏时修改；M3b 已完成 `ToolContext.principal_id` → searcher 传递
- 替换完成后删除 `PromptBuilder` 中的 `_PROMPT_ALLOWED_VISIBILITY` ad-hoc 常量，避免 policy 规则出现第二真源

**`MemorySearcher.search()` 签名状态**：

```python
async def search(
    self,
    query: str,
    *,
    scope_key: str = "main",
    principal_id: str | None = None,  # P2-M3c: visibility filter
    limit: int = 10,
    min_score: float = 0.0,
    source_types: list[str] | None = None,
) -> list[MemorySearchResult]:
```

该签名和 `MemorySearchResult.principal_id` / `MemorySearchResult.visibility` 已由 M3b 实现，M3c 不重复变更。

**SQL WHERE 替换**：
```sql
-- :ctx_principal_id may be NULL for anonymous requests. This deliberately
-- uses SQL NULL comparison semantics; do not rewrite as IS NOT DISTINCT FROM.
AND (
    (COALESCE(visibility, 'private_to_principal') = 'private_to_principal'
     AND (principal_id = :ctx_principal_id OR principal_id IS NULL))
    OR
    (visibility = 'shareable_summary'
     AND principal_id = :ctx_principal_id)  -- V1: same-principal only
)
-- visibility IS NULL → COALESCE 归入 private_to_principal
-- shared_in_space / unknown → 不匹配任何分支 → 自动排除
```

**PromptBuilder metadata 等价规则**：
- 无 `visibility` metadata：legacy，treated as `private_to_principal`
- `visibility=private_to_principal`：owner 或 legacy no-principal 可见；匿名只看 no-principal
- `visibility=shareable_summary`：必须存在 `principal` metadata 且等于当前 `principal_id`
- `visibility=shared_in_space` / unknown：deny
- 无 `principal` metadata 的 `shareable_summary` 不再作为 legacy visible entry 注入 prompt

**测试**：
- `tests/test_searcher_visibility.py`（DB integration）：
  - owner 搜索只返回自己的 private + 自己的 summary
  - 匿名搜索只返回 principal_id=NULL 的 private 条目
  - shared_in_space entry 不出现在任何搜索结果中
  - 不同 principal 的 private entry 不交叉可见
  - **跨 principal summary 不可见**：principal A 的 shareable_summary 对 principal B 不可见（V1）
  - 注意：`visibility=NULL` 无法通过 NOT NULL schema 插入，该 defensive case 在 `test_visibility_policy.py` 的 `can_read()` 纯函数单元测试中覆盖
- `tests/test_m3b_visibility.py` 或新 `tests/test_prompt_visibility.py`：
  - no-principal `shareable_summary` 不再注入给认证或匿名请求
  - PromptBuilder 对 private / summary / shared / unknown 的行为与 `can_read()` fixtures 保持一致

### Slice E：Memory Write Path 集成 `can_write`

**修改文件**：
- `src/memory/ledger.py`：`append()` 已有 `principal_id`, `visibility` 参数；M3c 在写入前调用 `can_write()` 并检查 `metadata.shared_space_id`
- `src/memory/writer.py`：写入路径已传递 `principal_id` 和 `visibility`；M3c 用 `can_write()` 替换 `_ALLOWED_VISIBILITY` / `_WRITABLE_VISIBILITY` ad-hoc 检查，保持错误语义兼容
- `src/memory/indexer.py`：`index_entry_direct()` 已接收并存储 `principal_id`, `visibility`；M3c 只补 `search_text` 时才改动该路径
- 替换完成后删除 `writer.py` 中的 `_ALLOWED_VISIBILITY` / `_WRITABLE_VISIBILITY` ad-hoc 常量，避免 dead code 和规则漂移

**API 设计与 DB 映射**：

外部 API（`MemoryLedgerWriter.append()`）当前只接收一个 `principal_id` 参数，语义是"当前操作者"（来自 `SessionIdentity.principal_id`）。`owner_principal_id` 是 policy 检查层的内部概念，不暴露给外部调用者。

```python
async def append(self, *, entry_id, content, scope_key="main",
                 source="user", source_session_id=None, metadata=None,
                 principal_id=None,              # 当前操作者 (from SessionIdentity)
                 visibility="private_to_principal"):
    # V1: owner = requester（当前操作者就是 entry owner）
    owner_principal_id = principal_id

    # 从 metadata 提取 reserved shared_space_id（规则 0 guard）
    meta = metadata or {}
    shared_space_id = meta.get("shared_space_id")

    policy_entry = MemoryPolicyEntry(
        entry_id=entry_id, owner_principal_id=owner_principal_id,
        visibility=visibility, scope_key=scope_key,
        shared_space_id=shared_space_id,          # 必须传入，否则规则 0 被绕过
    )
    ctx = PolicyContext(principal_id=principal_id, scope_key=scope_key)
    decision = can_write(ctx, policy_entry)
    if not decision.allowed:
        logger.info("ledger_write_denied", entry_id=entry_id,
                    principal_id=principal_id, reason=decision.reason)
        raise VisibilityPolicyError(decision.reason)

    # DB INSERT: 列名 principal_id = owner_principal_id
    # INSERT INTO memory_source_ledger (..., principal_id, visibility)
    # VALUES (..., :principal_id, :visibility)
    ...
```

**实现要求**：`MemoryPolicyEntry.shared_space_id` 必须从 `metadata` dict 提取，不能依赖 dataclass 默认值 `None` 跳过检查。若 `metadata` 中包含 `shared_space_id`（无论 visibility 是什么），规则 0 必须拦截。

**DB 列 → Python 映射**：

| DB 列名 | 含义 | Python policy 对应 | API 参数名 |
|----------|------|-------------------|-----------|
| `memory_source_ledger.principal_id` | entry owner | `MemoryPolicyEntry.owner_principal_id` | `append(principal_id=...)` |
| `memory_entries.principal_id` | entry owner (投影) | SQL WHERE `principal_id = :ctx_principal_id` | `search(principal_id=...)` |

**语义说明**：
- 外部只传 `principal_id`（当前操作者）；V1 中 owner 始终等于 requester，无需外部控制两个 id
- Policy 检查层内部用 `MemoryPolicyEntry.owner_principal_id` 与 `PolicyContext.principal_id` 独立比较
- DB 写入列名不变，仍为 `principal_id`，值来自 policy 检查后确认的 `owner_principal_id`
- 上游调用者（`MemoryWriter`、agent loop）必须从 `SessionIdentity.principal_id` 传入，不得由 tool 参数控制

**新增异常**：
- `src/infra/errors.py`：`VisibilityPolicyError(MemoryWriteError)` — policy 拒绝写入。继承 `MemoryWriteError` 是为了保持 `process_flush_candidates()` 等既有 catch 逻辑兼容，同时保留独立 error code（如 `MEMORY_VISIBILITY_DENIED`）。
- M3c 必须检查所有 `MemoryLedgerWriter.append()` call-site：若调用方只捕获 `LedgerWriteError`，需同步捕获 `VisibilityPolicyError` / `MemoryWriteError`，避免 policy 拒绝从 ledger 直连路径意外逃逸。

**测试**：
- `tests/test_ledger_visibility.py` 或扩展 `tests/test_m3b_visibility.py`（只覆盖 public API 可达路径）：
  - 认证者写自己的 `private_to_principal` → success
  - 匿名写入 `private_to_principal` → success（owner 由 API 内部推导为 None，legacy 兼容）
  - 尝试写入 `shared_in_space` visibility → `VisibilityPolicyError`（或上层仍按 `MemoryWriteError` 捕获）
  - **metadata shared_space_id guard**：`visibility='private_to_principal'` + `metadata={'shared_space_id': 'space-1'}` → `VisibilityPolicyError(membership_unavailable)`
  - **metadata shared_space_id guard**：`visibility='shareable_summary'` + `metadata={'shared_space_id': 'space-1'}` → `VisibilityPolicyError(membership_unavailable)`
  - `metadata={}` 或 `metadata=None` → shared_space_id=None → 规则 0 不触发，正常进入 visibility 分支
- 注意：cross-principal 写入（认证者写他人 entry）和匿名写有 owner entry 无法通过 public API 构造（API 只接收 `principal_id`，owner 由内部推导为 requester）。这些场景由 `tests/test_visibility_policy.py` 的 `can_write()` 单元测试覆盖（见 Slice C）

### Slice F：Ledger-Based Reindex 的 `search_text` 扩展

**修改文件**：
- `src/memory/indexer.py`：扩展现有 `reindex_from_ledger()`、`index_entry_direct()`、workspace parse/persist 路径，写入 `search_text`
- `src/backend/cli.py`：不新增 source 参数；保留 M3b 已交付的 ledger 非空则全 scope ledger-based rebuild、否则 workspace fallback
- `scripts/restore.py`：不改变 restore source 选择，只依赖扩展后的 `reindex_all(scope_key=None, ledger=ledger)` 填充 `search_text`

**现有 `reindex_from_ledger()` 扩展逻辑**：
1. 保持 M3b 现有删除策略：按 `source_type='daily_note'` 清理 ledger-derived projection，且 `scope_key=None` 时覆盖全 scope
2. 从 `memory_source_ledger` 读取 current view（当前 V1 append events）
3. 对每条 entry 做 Jieba 分词 → 生成 `search_text`
4. 批量 insert 到 `memory_entries`（含 `search_text`, `principal_id`, `visibility`）
5. 完成后记录 structlog 日志（entry count, elapsed time）

**注意**：不清理 `source_type = 'curated'` 的行；curated 由 `index_curated_memory()` 独立管理。

**reindex CLI 状态**：
- 当前 `python -m src.backend.cli reindex [--scope main]` 已在 ledger 非空时调用 `reindex_all(scope_key=None, ledger=ledger)`
- M3c 只要求该路径重建 `search_text`；不把 `--source ledger/workspace` 加入本阶段，避免扩大 CLI 表面
- workspace fallback 路径仍可用：ledger 为空时从 workspace daily notes + curated 文件重建，并同样填充 `search_text`

**测试**：
- `tests/test_reindex_ledger.py`：
  - ledger 有 3 条 entry + workspace 有 curated MEMORY.md → reindex → memory_entries 含 daily_note + curated 行
  - reindex 幂等：重复执行结果一致
  - visibility 和 principal_id 正确传播
  - curated rows 不被 ledger reindex 清理或覆盖

### Slice G：Vector Retrieval 决策产出

> 此 Slice 不实现 vector retrieval，只产出数据驱动的启用/不启用决策。

**产出物**：
- Retrieval regression fixture 按 category 的 pass/fail 统计报告
- 若 `semantic_gap` 类 miss 占比显著（且 lexical 增强无法覆盖），产出 follow-up issue/plan：
  - embedding model 选择（Ollama local vs OpenAI）+ dimension
  - `memory_entries.embedding` 列 + pgvector hnsw index
  - hybrid ranking（lexical + vector 加权）+ score normalization
  - vector SQL 必须先通过 scope/principal/visibility WHERE，再做 `ORDER BY embedding <=> query_embedding`（不能先全局 top-N 再 post-filter）
  - embedding 生成管线（写入路径异步、reindex 批量）
- 若 `semantic_gap` 类 miss 可忽略，记录决策为"V1 不启用 vector retrieval"

**不新增代码**：无 `embedder.py`、无 `embedding` 列、无 hybrid ranking 实现

### Slice H：Restore Reindex + Doctor 更新

`memory_entries` 是 derived retrieval projection（ADR 0060），不是 truth table。Backup 只覆盖 truth tables（`memory_source_ledger`, `principals`, `principal_bindings` 等），不包含 `memory_entries`。

**修改文件**：
- `scripts/restore.py`：M3b 已在恢复序列中优先执行 ledger-based `reindex_all(scope_key=None, ledger=ledger)`；M3c 只需确保该现有调用在扩展后填充 `search_text`
- `src/infra/doctor.py`：新增 D6 检查项 — visibility policy 一致性：`memory_entries` 中不应存在 `shared_in_space` 可见条目；若 `memory_entries` 总数 > 0 且 `search_text IS NULL` 行数 > 0，则 warn 并提示运行 `reindex` 填充 CJK `search_text`
- `src/infra/preflight.py`：确认 `search_text` 列存在且 trigger 版本正确（已在 Slice B 中覆盖）

**不修改**：
- `scripts/backup.py`：`memory_entries` 仍被排除（derived index），`search_text`/`embedding` 都是可重建缓存；truth tables（`memory_source_ledger`）已在 M3b 加入 backup

**测试**：
- doctor D6 检查通过
- doctor D6 在 `memory_entries` 非空且存在 `search_text IS NULL` 行时给出 actionable warn；空表不 warn
- restore → reindex 后 memory_entries 包含正确的 search_text、principal_id、visibility

## 4. 实现顺序

```
Slice A (retrieval regression fixture)
  └─→ Slice B (query normalization + Jieba + search_text schema)
        ← A 的 cjk miss case 用来验证 B

Slice C (visibility policy checkpoint) — 可与 A/B 并行
  ├─→ Slice D (read path visibility filter hardening)
  └─→ Slice E (memory write path integration)

Slice F (ledger-based reindex search_text extension) — 依赖 B + C
  └─→ Slice G (vector retrieval 决策) — 依赖 A 的 regression 统计
  └─→ Slice H (restore reindex + doctor) — 最后收尾
```

建议分 3 个 gate：
- **Gate 0**：Slice A + B + C — retrieval regression + lexical 增强 + visibility policy 基础
- **Gate 1**：Slice D + E + F — read/write policy 收敛 + 现有 ledger reindex 的 search_text 扩展
- **Gate 2**：Slice G + H — vector retrieval 启用决策产出（不实现） + 运维收尾

## 5. 验收标准

### 功能验收（对应 roadmap Use Case）

1. **Use Case C — 检索 miss 消除**：retrieval regression 中 ≥ 70% 的 miss case pass（至少 7/10）。`cjk_tokenization` 类 case 全部 pass。
2. **Use Case D — 记忆共享范围可解释**：`can_read` / `can_write` 对每个 visibility 值返回明确的 allow/deny + reason。
3. **Use Case E — visibility policy hook fail-closed**：
   - `shared_in_space` 读写始终被拒绝，reason = `"shared_space_policy_not_implemented"`
   - 未知 visibility 值被拒绝，reason = `"unknown_visibility_value"`
   - `visibility=NULL` defensive 分支 treated as `private_to_principal`（M3b schema 为 NOT NULL，正常不产生 NULL；纯函数单元测试覆盖）
   - 匿名请求不能读 `shareable_summary`
   - 跨 principal 不能读对方的 `shareable_summary`（V1 same-principal only）
4. **Retrieval filter-then-rank**：所有搜索结果已通过 visibility filter；不同 principal 的 private entry 不交叉可见；PromptBuilder daily notes 注入与 searcher 过滤语义一致。
5. **Memory write policy**：尝试写入 `shared_in_space` visibility 或带 `metadata.shared_space_id` 的条目被拒绝并返回 `VisibilityPolicyError`（兼容 `MemoryWriteError` 捕获）；认证者不能以其他 principal 身份写入。
6. **Ledger-based reindex**：现有 `reindex` CLI / restore 的 ledger-based rebuild 从 DB ledger 重建 `memory_entries`，结果包含正确的 `search_text`、`principal_id`、`visibility`。
7. **Audit trail**：write/direct-read 的 policy 拒绝事件在 structlog 中可查（`visibility_policy_denied`）；search 完成后记录 `memory_search_filtered` 事件（含 principal_id + policy version + result count）。

### 不变性验收

8. **现有测试全绿**：不破坏 P2-M3a、P2-M3b 及之前的所有测试。
9. **匿名路径兼容**：未配置 auth 时（no-auth mode），所有现有 memory 读写行为不变。M3b migration 会 backfill `visibility = 'private_to_principal'`，现有数据不会有 NULL。`can_read()` 的 NULL 分支和 SQL COALESCE 仅作 defensive 保护。
10. **Scope_key 语义不变**：visibility filter 是 scope filter 的补充，不替代。
11. **Workspace projection 路径不删除**：ledger 为空时的 workspace fallback reindex 仍可用，并同样填充 `search_text`。

### 测试覆盖

12. 新增或扩展测试文件（预估）：7 个左右（retrieval regression, query processor, search_text schema, visibility policy, searcher/prompt visibility, ledger visibility, reindex ledger）+ doctor 检查项
13. 新增测试数量（预估）：50-70 个（含 ≥ 10 个 regression fixture case）

## 6. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Jieba 分词质量不满足专业术语 | 中 | V1 可接受；后续可自定义词典 (`jieba.load_userdict()`) |
| `search_text` 列增加存储和 trigger 复杂度 | 低 | 列为 nullable，trigger 有 fallback；reindex 可重建；ensure_schema 同步 |
| Visibility SQL WHERE / PromptBuilder metadata filter 与 `can_read()` 逻辑不一致 | 高 | 共享 policy version；Slice D 测试同时验证 SQL 路径、PromptBuilder 路径和函数路径对所有 visibility 值（含 NULL defensive case）的一致性 |
| P2-M3b 实际交付已覆盖部分 M3c 原计划，导致重复实现或 CLI 表面扩张 | 中 | M3c 只做增量适配：不重建 `reindex_from_ledger()`，不新增 `reindex --source`，不重复添加 `principal_id`/`visibility` 参数 |
| Retrieval regression fixture 数量不足以代表真实 miss 分布 | 低 | fixture 是 append-only，后续可持续补充；vector retrieval 决策作为 follow-up 而非 in-gate 实现 |
| `can_read`/`can_write` 规则变更时 SQL WHERE、PromptBuilder 和函数需同步更新 | 中 | V1 规则简单，变更频率低；测试三路覆盖 |
| Restore 后 search_text 未回填导致 CJK 搜索退化 | 中 | M3b restore 已调用 ledger-based `reindex_all()`；M3c 扩展现有 reindex 填充 search_text，并由 doctor 在非空表存在 NULL search_text 时给出 reindex 提示 |

## 7. 不做的事

- 不做 membership 表或 `shared_space_id` 规范化关系模型（ADR 0059）
- 不做 federation protocol skeleton（远端身份、消息格式、信任握手）
- 不做 shared memory lifecycle（join/leave/revoke/dissolve）
- 不做产品级 Shared Companion demo
- 不做策略注册表、adapter 模式或 pluggable policy framework
- 不做 vector retrieval / embedding 管线（M3c 只产出启用决策 + follow-up issue）
- 不做 LLM-judged retrieval eval 或 NDCG/MRR benchmark
- 不做 ParadeDB `pg_search` BM25 集成（路线图保留，V1 不依赖）
- 不做重型知识图谱工程
- 不 onboard `memory_application_spec`
- 不做多方确认的 `shareable_summary` 生效流程（P3+）
- 不做 contested memory / relationship memory correction
- 不把群聊 / Slack channel 当作 shared-space identity 或 memory policy 真源

## 8. 与 P2-M3a / P2-M3b 的接口契约

### 从 P2-M3a 继承（已完成）

| 接口 | 用途 |
|------|------|
| `SessionIdentity.principal_id` | 传播到 `PolicyContext.principal_id` |
| `ToolContext.principal_id` | `MemorySearchTool` 传递给 searcher |
| `PrincipalStore.get_owner()` / `resolve_binding()` 等实际方法 | principal / binding 真源；M3c policy V1 不依赖不存在的 `resolve_principal_id()` 方法 |
| `principals` / `principal_bindings` 表 | principal identity 真源 |

### 从 P2-M3b 继承（已完成）

| 接口 | 用途 | M3b 实际 schema |
|------|------|----------------|
| `memory_source_ledger.principal_id` | ledger entry 的 owner principal | VARCHAR(36), nullable, FK → principals.id |
| `memory_source_ledger.visibility` | entry 的可见性声明 | VARCHAR(32), **NOT NULL**, DEFAULT 'private_to_principal' |
| `memory_entries.principal_id` | search index 中的 principal 维度 | VARCHAR(36), nullable |
| `memory_entries.visibility` | search index 中的 visibility 维度 | VARCHAR(32), **NOT NULL**, DEFAULT 'private_to_principal' |
| `MemorySearcher.search(principal_id=...)` | search read path principal-aware 过滤 | 已实现，M3c 只替换 visibility WHERE |
| `MemorySearchResult.principal_id` / `visibility` | 搜索结果携带 owner/visibility | 已实现 |
| `MemoryWriter.append_daily_note(principal_id=..., visibility=...)` | memory write path 传播 owner/visibility | 已实现基础 fail-closed |
| `MemoryLedgerWriter.append(principal_id=..., visibility=...)` | ledger truth 写入 owner/visibility | 已实现参数和 DB 写入，M3c 补 policy guard |
| `MemoryIndexer.reindex_from_ledger()` | ledger current view → `memory_entries` projection | 已实现，CLI/restore 已在 ledger 非空时使用；M3c 补 `search_text` |

> M3b 的 visibility 列为 NOT NULL + DEFAULT，migration 会 backfill 现有行。因此正常 DB 路径不会产生 `visibility=NULL` 行。M3c 的 COALESCE 和 `can_read()` NULL 处理作为 **defensive 保护**保留（防止非 migration 路径手动插入或 schema 降级），但不作为正常 DB integration fixture case（见下方 NULL 处理说明）。

### M3b / M3c Ownership 同步状态

M3b 交接段已按实现日志同步（2026-04-12 修订），ownership 边界如下：

- **M3b 拥有**：基础 visibility allowlist（`IN ('private_to_principal', 'shareable_summary')`，排除 shared_in_space）+ principal filter（认证请求 own + legacy，匿名请求 legacy-only）+ PromptBuilder daily notes 同类过滤 + writer 基础 fail-closed + ledger-based reindex
- **M3c 收紧**：COALESCE defensive NULL 处理 + `shareable_summary` same-principal-only（包括 no-principal summary 不再 legacy-visible）+ `can_read()`/`can_write()` policy hook + `metadata.shared_space_id` guard + audit trail + `search_text` reindex 扩展

M3c 实现者应先读取 M3b 实现日志和当前代码确认实际交付范围，再增量扩展 SQL WHERE、PromptBuilder metadata filter 与 writer/ledger policy guard。
