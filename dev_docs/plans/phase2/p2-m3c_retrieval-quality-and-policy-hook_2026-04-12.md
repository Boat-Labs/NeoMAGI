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

## 0. 目标

用已知 miss case 建立轻量 retrieval regression，提升检索命中率；建立统一的 visibility policy checkpoint，为未来 federation-compatible Shared Companion 留下最小安全地基。

回答的问题：**memory 检索为什么没命中？共享边界为什么允许/拒绝？**

完成后：
- 已知自然语言检索 miss case 有稳定回归测试，至少部分被消除
- 检索质量具备可衡量的 lexical 增强（CJK 分词 + query normalization）；vector retrieval 产出数据驱动的启用/不启用决策（不在 M3c 实现）
- 统一 visibility policy checkpoint `can_read` / `can_write` 存在并默认 fail-closed
- `shared_in_space` 为 reserved / deny-by-default；所有 shared-space 读写返回明确拒绝原因
- 所有 retrieval 先通过 scope / principal / visibility filter，再进入 ranking
- policy hook 设计兼容联邦式、hosted shared-space 和混合模式，不深度绑定单实例多 principal

## 1. 当前基线

> 假设 P2-M3a 和 P2-M3b 已完成。以下基线同时标注"P2-M3b 后预期状态"。

| 组件 | P2-M3b 后预期状态 |
|------|-------------------|
| `MemorySearcher` (`src/memory/searcher.py`) | tsvector + `ts_rank`，`plainto_tsquery('simple', ...)`；scope_key 必选过滤 |
| `memory_entries.search_vector` | `setweight(title, 'A') \|\| setweight(content, 'B')` trigger；无 embedding 列 |
| `memory_source_ledger` | 含 `principal_id`, `visibility` 列（P2-M3b 新增） |
| `memory_entries` | 含 `principal_id`, `visibility` 列（P2-M3b 新增，从 ledger 传播） |
| Visibility filtering | P2-M3b 引入 principal-aware memory **write** path；`visibility` 列可区分 `private_to_principal` / `shareable_summary` |
| `ToolContext` | 含 `principal_id`（P2-M3a）；P2-M3b 可能新增 visibility 相关上下文 |
| `shared_in_space` | reserved 概念，无运行时实现；无 policy checkpoint 函数 |
| Retrieval 回归 | 无；已知 miss case 未系统收集 |
| Vector / embedding | pgvector 扩展已安装（tech stack）；无 embedding 列、无 embedding 生成管线 |

### M3b / M3c Ownership 划分

为避免两个子阶段改同一批文件且验收语义不一致，明确划分如下：

| 关注面 | M3b 拥有 | M3c 拥有 |
|--------|----------|----------|
| **Schema** | ledger + memory_entries 增加 `principal_id`/`visibility` 列 | memory_entries 增加 `search_text` 列（CJK 分词） |
| **Write path** | ledger writer 写入 `principal_id`/`visibility`；indexer 传播到 memory_entries | `can_write()` policy checkpoint 集成到写入路径 |
| **Read path** | searcher 增加 `principal_id` 参数透传 + 基础 visibility IN 过滤（排除 shared_in_space） | `can_read()` policy 规则定义；将 M3b 的基础 SQL 扩展为完整 visibility WHERE（COALESCE legacy NULL、shareable_summary same-principal-only）；`can_read()` 含规则 0 shared_space_id guard（用于 direct-read 路径，不进 SQL）；retrieval quality 增强 |
| **Reindex** | 确保 ledger→memory_entries 传播 principal_id/visibility 的基本 reindex 路径可用 | `reindex_from_ledger()` 含 CJK 分词、visibility 传播的完整重建；reindex CLI 默认切换到 ledger source |
| **Policy 函数** | 不涉及 | `can_read()`/`can_write()` 定义、实现、集成、测试 |
| **Retrieval quality** | 不涉及 | query normalization、Jieba 分词、regression fixture、可选 vector |

M3b 在 searcher 中实现了基础过滤：`principal_id = :pid OR principal_id IS NULL` + `visibility IN ('private_to_principal', 'shareable_summary')`。M3c 在 SQL 层扩展为：
- `COALESCE(visibility, 'private_to_principal')` 处理 legacy NULL 行
- `shareable_summary` 收紧为 `principal_id = :ctx_principal_id`（same-principal only）

规则 0（shared_space_id guard）**不进入 SQL WHERE**：`memory_entries` 没有 `shared_space_id` 列，不做 JSON 投影或加列。规则 0 只存在于 `can_read()`/`can_write()` 纯函数中，覆盖 write 路径和 direct-read 路径。Search SQL 路径天然不返回 `shared_in_space` 行（被 visibility whitelist 排除），无需额外 shared_space_id 过滤。

M3c 不从零重建 searcher 的过滤，而是在 M3b 的 SQL WHERE 基础上增量替换。

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

**D2b：Index-time CJK 分词**（必做）
- 修改 `search_vector` trigger：对 content 先做 Jieba 分词再构造 tsvector。
- 实现方式：PostgreSQL trigger 调用 `to_tsvector('simple', jieba_segmented_text)`，其中 jieba 分词在 Python 写入层完成（indexer / ledger writer），存储分词后的 `search_text` 列。
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

架构文档要求"统一 visibility policy checkpoint"，实现为简单函数，不做策略注册表或 adapter 模式。

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

### D5：Searcher 集成 visibility filter — filter-then-rank

所有 retrieval 必须先通过 visibility filter，再进入 ranking。

**实现位置**：`MemorySearcher`

**两种实现策略**：

**策略 A（SQL WHERE 过滤，首选）**：
- 在 `_build_search_sql()` 中增加 visibility 相关 WHERE 条件
- 利用 P2-M3b 在 `memory_entries` 上新增的 `principal_id` / `visibility` 列
- SQL 层过滤效率最高；但需要把 policy 规则翻译为 SQL 条件

**策略 B（Python 层 post-filter）**：
- SQL 查询返回候选集后，对每个 result 调用 `can_read()` 过滤
- 灵活但有效率问题（需要 over-fetch）

**选择策略 A**：
- V1 policy 规则足够简单，可直接翻译为 SQL WHERE
- 避免 over-fetch 和额外 round-trip
- `can_read()` 函数仍然存在，用于非 SQL 路径（如按 entry_id 直接读取 ledger、API / CLI 级别的权限检查）；search 路径不调用 `can_read()`，因为 SQL WHERE 已等价实现相同规则
- **SQL 规则与 `can_read()` 规则一致性**：两者必须基于相同的 policy version 常量；Slice D 测试同时覆盖 SQL 路径和 `can_read()` 路径对所有 visibility 值的一致性

SQL 层过滤示例：
```sql
-- COALESCE 处理 legacy 数据（visibility IS NULL → treated as 'private_to_principal'）
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

P2-M3b 已将 memory read path 从 workspace 文件切换到 DB ledger。M3c 确保 reindex 路径也从 ledger current view 重建 `memory_entries`。

**`reindex_from_ledger()`**：
- 从 `memory_source_ledger` 读取 current view（最新 append events per entry_id）
- 对每条 entry 做 Jieba 分词 → 写入 `memory_entries`（含 `search_text`, `principal_id`, `visibility`）
- **只替代** `reindex_all()` 中 daily_note / source-ledger 来源的部分
- **curated memory（MEMORY.md）仍按现有 workspace 路径重建**：`index_curated_memory()` 继续从 workspace 文件读取。理由：curated memory 是人工编辑的结构化文件，不经过 ledger 写入路径；若删除 curated rows 会导致 MEMORY.md 内容从搜索结果中消失
- 默认 `reindex` CLI 调用 `reindex_from_ledger()` + `index_curated_memory()`（组合模式）
- `reindex --source workspace` 保留完整 legacy 路径（daily notes + curated 均从文件）

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
- `MemoryWriter` / `MemoryLedgerWriter` 写入前调用 `can_write()` 验证
- 若 `can_write().allowed == False` → 拒绝写入，返回错误（不静默丢弃）
- 默认所有写入的 visibility 为 `"private_to_principal"`
- V1 不支持用户在 tool 调用中指定 visibility（避免权限提升）

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
- **`ensure_schema()` 同步更新**：`src/session/database.py` 中 `memory_entries` 的 idempotent DDL 必须包含 `search_text` 列和更新后的 trigger（fresh DB 路径）
- **Preflight 检查更新**：`src/infra/preflight.py` 验证 `search_text` 列存在且 trigger 版本正确
- **Restore 后回填**：`scripts/restore.py` 恢复序列中，在 reindex 步骤调用 `reindex_from_ledger()`（或 `reindex_all()`），确保 `search_text` 列被正确填充；不做单独的 backfill migration（reindex 即回填）
- **现有行回填策略**：已有 `memory_entries` 行的 `search_text` 为 NULL → trigger fallback 到 `content` 保证搜索不中断；下次 `reindex` 执行时统一回填 CJK 分词后的 `search_text`

**测试**：
- `tests/test_query_processor.py`：CJK 分词、英文 lowercase、混合语言、空输入、标点处理
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

### Slice D：Searcher Visibility Filter 集成

**修改文件**：
- `src/memory/searcher.py`：`_build_search_sql()` 增加 visibility WHERE 条件；`search()` 接收 `principal_id` 参数
- `src/tools/builtins/memory_search.py`：`execute()` 从 `ToolContext` 传递 `principal_id` 到 searcher

**`MemorySearcher.search()` 签名变更**：

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

**SQL WHERE 新增**：
```sql
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

**MemorySearchResult 扩展**：
- 新增 `visibility: str` 字段（供调用方了解结果的 visibility 属性）

**测试**：
- `tests/test_searcher_visibility.py`（DB integration）：
  - owner 搜索只返回自己的 private + 自己的 summary
  - 匿名搜索只返回 principal_id=NULL 的 private 条目
  - shared_in_space entry 不出现在任何搜索结果中
  - 不同 principal 的 private entry 不交叉可见
  - **跨 principal summary 不可见**：principal A 的 shareable_summary 对 principal B 不可见（V1）
  - 注意：`visibility=NULL` 无法通过 NOT NULL schema 插入，该 defensive case 在 `test_visibility_policy.py` 的 `can_read()` 纯函数单元测试中覆盖

### Slice E：Memory Write Path 集成 `can_write`

**修改文件**：
- `src/memory/ledger.py`：`append()` 增加 `principal_id`, `visibility` 参数；写入前调用 `can_write()`
- `src/memory/writer.py`：写入路径传递 `principal_id` 和 `visibility`
- `src/memory/indexer.py`：`index_entry_direct()` 接收并存储 `principal_id`, `visibility`

**API 设计与 DB 映射**：

外部 API（`MemoryLedgerWriter.append()`）**只接收一个 `principal_id` 参数**，语义是"当前操作者"（来自 `SessionIdentity.principal_id`）。`owner_principal_id` 是 policy 检查层的内部概念，不暴露给外部调用者。

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
- `src/infra/errors.py`：`VisibilityPolicyError(NeoMAGIError)` — policy 拒绝写入

**测试**：
- `tests/test_ledger_visibility.py`（只覆盖 public API 可达路径）：
  - 认证者写自己的 `private_to_principal` → success
  - 匿名写入 `private_to_principal` → success（owner 由 API 内部推导为 None，legacy 兼容）
  - 尝试写入 `shared_in_space` visibility → `VisibilityPolicyError`
  - **metadata shared_space_id guard**：`visibility='private_to_principal'` + `metadata={'shared_space_id': 'space-1'}` → `VisibilityPolicyError(membership_unavailable)`
  - **metadata shared_space_id guard**：`visibility='shareable_summary'` + `metadata={'shared_space_id': 'space-1'}` → `VisibilityPolicyError(membership_unavailable)`
  - `metadata={}` 或 `metadata=None` → shared_space_id=None → 规则 0 不触发，正常进入 visibility 分支
- 注意：cross-principal 写入（认证者写他人 entry）和匿名写有 owner entry 无法通过 public API 构造（API 只接收 `principal_id`，owner 由内部推导为 requester）。这些场景由 `tests/test_visibility_policy.py` 的 `can_write()` 单元测试覆盖（见 Slice C）

### Slice F：Ledger-Based Reindex

**修改文件**：
- `src/memory/indexer.py`：新增 `reindex_from_ledger()` 方法
- `src/backend/cli.py`：`reindex` CLI 默认走 ledger 路径

**`reindex_from_ledger()` 逻辑**：
1. 删除 `memory_entries` 中 `source_type IN ('daily_note', 'flush_candidate')` 的行（只清理 ledger 来源）
2. 从 `memory_source_ledger` 读取 current view（latest append event per entry_id）
3. 对每条 entry 做 Jieba 分词 → 生成 `search_text`
4. 批量 insert 到 `memory_entries`（含 `search_text`, `principal_id`, `visibility`）
5. 完成后记录 structlog 日志（entry count, elapsed time）

**注意**：不清理 `source_type = 'curated'` 的行；curated 由 `index_curated_memory()` 独立管理。

**reindex CLI 变更**：
- `reindex --source ledger`（默认）：`reindex_from_ledger()` + `index_curated_memory()`
- `reindex --source workspace`：保留完整 legacy 路径（daily notes + curated 均从文件）

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
- `scripts/restore.py`：恢复序列中，确保 `reindex` 步骤调用 `reindex_from_ledger()`（含 CJK 分词 + visibility/principal_id 传播），从 truth tables 完整重建 `memory_entries`（含 `search_text`）
- `src/infra/doctor.py`：新增 D6 检查项 — visibility policy 一致性：`memory_entries` 中不应存在 `shared_in_space` 可见条目；`search_text` 非空率检查（warn if < 90%，提示需要 reindex）
- `src/infra/preflight.py`：确认 `search_text` 列存在且 trigger 版本正确（已在 Slice B 中覆盖）

**不修改**：
- `scripts/backup.py`：`memory_entries` 仍被排除（derived index），`search_text`/`embedding` 都是可重建缓存；truth tables（`memory_source_ledger`）已在 M3b 加入 backup

**测试**：
- doctor D6 检查通过
- restore → reindex 后 memory_entries 包含正确的 search_text、principal_id、visibility

## 4. 实现顺序

```
Slice A (retrieval regression fixture)
  └─→ Slice B (query normalization + Jieba + search_text schema)
        ← A 的 cjk miss case 用来验证 B

Slice C (visibility policy checkpoint) — 可与 A/B 并行
  ├─→ Slice D (searcher visibility filter)
  └─→ Slice E (memory write path integration)

Slice F (ledger-based reindex) — 依赖 B + C
  └─→ Slice G (vector retrieval 决策) — 依赖 A 的 regression 统计
  └─→ Slice H (restore reindex + doctor) — 最后收尾
```

建议分 3 个 gate：
- **Gate 0**：Slice A + B + C — retrieval regression + lexical 增强 + visibility policy 基础
- **Gate 1**：Slice D + E + F — searcher/writer 集成 + ledger reindex
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
4. **Retrieval filter-then-rank**：所有搜索结果已通过 visibility filter；不同 principal 的 private entry 不交叉可见。
5. **Memory write policy**：尝试写入 `shared_in_space` visibility 的条目被拒绝并返回 `VisibilityPolicyError`；认证者不能以其他 principal 身份写入。
6. **Ledger-based reindex**：`reindex --source ledger` 从 DB ledger 重建 `memory_entries`，结果包含正确的 `search_text`、`principal_id`、`visibility`。
7. **Audit trail**：write/direct-read 的 policy 拒绝事件在 structlog 中可查（`visibility_policy_denied`）；search 完成后记录 `memory_search_filtered` 事件（含 principal_id + policy version + result count）。

### 不变性验收

8. **现有测试全绿**：不破坏 P2-M3a、P2-M3b 及之前的所有测试。
9. **匿名路径兼容**：未配置 auth 时（no-auth mode），所有现有 memory 读写行为不变。M3b migration 会 backfill `visibility = 'private_to_principal'`，现有数据不会有 NULL。`can_read()` 的 NULL 分支和 SQL COALESCE 仅作 defensive 保护。
10. **Scope_key 语义不变**：visibility filter 是 scope filter 的补充，不替代。
11. **Workspace projection 路径不删除**：`reindex --source workspace` 仍可用作 fallback。

### 测试覆盖

12. 新增测试文件（预估）：7 个（retrieval regression, query processor, search_text schema, visibility policy, searcher visibility, ledger visibility, reindex ledger）+ doctor 检查项
13. 新增测试数量（预估）：50-70 个（含 ≥ 10 个 regression fixture case）

## 6. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| Jieba 分词质量不满足专业术语 | 中 | V1 可接受；后续可自定义词典 (`jieba.load_userdict()`) |
| `search_text` 列增加存储和 trigger 复杂度 | 低 | 列为 nullable，trigger 有 fallback；reindex 可重建；ensure_schema 同步 |
| Visibility SQL WHERE（COALESCE）与 `can_read()` 逻辑不一致 | 高 | 共享 policy 规则常量；Slice D 测试同时验证 SQL 路径和函数路径对所有 visibility 值（含 NULL）的一致性 |
| P2-M3b 的 `principal_id` / `visibility` 列设计与 M3c 预期不一致 | 中 | M3c draft 明确列出预期接口（Section 8）；M3b 实现时对齐；M3c 实现时做增量适配 |
| Retrieval regression fixture 数量不足以代表真实 miss 分布 | 低 | fixture 是 append-only，后续可持续补充；vector retrieval 决策作为 follow-up 而非 in-gate 实现 |
| `can_read`/`can_write` 规则变更时 SQL WHERE 和函数需同步更新 | 中 | V1 规则简单，变更频率低；测试双重覆盖 |
| Restore 后 search_text 未回填导致 CJK 搜索退化 | 中 | restore 流程强制调用 `reindex_from_ledger()` 重建 search_text；doctor 检查 search_text 非空率 |

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
| `PrincipalStore.resolve_principal_id()` | 验证 principal 存在性（必要时） |
| `principals` / `principal_bindings` 表 | principal identity 真源 |

### 从 P2-M3b 继承（预期）

| 接口 | 用途 | M3b 实际 schema |
|------|------|----------------|
| `memory_source_ledger.principal_id` | ledger entry 的 owner principal | VARCHAR(36), nullable, FK → principals.id |
| `memory_source_ledger.visibility` | entry 的可见性声明 | VARCHAR(32), **NOT NULL**, DEFAULT 'private_to_principal' |
| `memory_entries.principal_id` | search index 中的 principal 维度 | VARCHAR(36), nullable |
| `memory_entries.visibility` | search index 中的 visibility 维度 | VARCHAR(32), **NOT NULL**, DEFAULT 'private_to_principal' |
| Memory read path 切换 | `memory_entries` 读取从 ledger current view 驱动 | reindex 路径可从 ledger 重建 |

> M3b 的 visibility 列为 NOT NULL + DEFAULT，migration 会 backfill 现有行。因此正常 DB 路径不会产生 `visibility=NULL` 行。M3c 的 COALESCE 和 `can_read()` NULL 处理作为 **defensive 保护**保留（防止非 migration 路径手动插入或 schema 降级），但不作为正常 DB integration fixture case（见下方 NULL 处理说明）。

### M3b / M3c Ownership 同步状态

M3b 交接段已同步（2026-04-12 修订），ownership 边界如下：

- **M3b 拥有**：基础 visibility allowlist（`IN ('private_to_principal', 'shareable_summary')`，排除 shared_in_space）+ 基础 principal filter（`principal_id = :pid OR principal_id IS NULL`）
- **M3c 收紧**：COALESCE defensive NULL 处理 + shareable_summary same-principal-only + `can_read()`/`can_write()` policy hook + audit trail

M3c 实现者应先读取 M3b 正稿确认实际交付范围，再增量扩展 SQL WHERE。
