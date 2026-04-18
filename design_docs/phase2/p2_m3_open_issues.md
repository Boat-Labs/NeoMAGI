---
doc_id: 019da228-7e90-79ef-b974-bb76b7623de4
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-18T21:54:16+02:00
---
# P2-M3 Open Issues

> 说明：本文件记录 P2-M3 用户测试中发现的设计/架构层面 root cause，避免问题背景散落在聊天记录中。

## OI-M3-01 Jieba 分词器对非 CJK 语言完全失效，需语言自适应策略

- 发现于：P2-M3 用户测试期间（手动 ParadeDB 分词器对比实验）
- 现象：中德混排文本在当前 Jieba 分词下完全无法正确切分；中英混排存在空 token
- 当前实现：
  - 写入时：`query_processor.segment_for_index()` 固定调用 `jieba.cut_for_search()`，结果存入 `search_text` 列
  - DB trigger：`to_tsvector('simple', COALESCE(search_text, content))` → `search_vector`
  - 查询时：`query_processor.normalize_query()` 同样固定调用 `jieba.cut_for_search()`，再通过 `plainto_tsquery('simple', ...)` 匹配
  - 不可配置，无 fallback
- 用户实测分词器对比（ParadeDB tokenizer）：

  | 场景 | Jieba | ICU | chinese_lindera |
  |------|-------|-----|-----------------|
  | 长中文文本 | 非常好 | 不行 | 不行 |
  | 中英混排 | 有空 token | 非常好 | 可以 |
  | 中英专业术语密集 | — | 非常好 | — |
  | 中德混排 | 完全不行 | 非常好 | — |
  | 无空格中英紧挨混排 | 非常好 | 非常好 | — |

- root cause：
  - Jieba 是纯中文词典分词器，不识别德语等非 CJK 语言，会把德语单词按字符错切
  - ICU 是 Unicode-aware 通用分词，天然处理多语种 word boundary，但对中文缺乏词典级精度
  - 单一分词器无法同时覆盖"纯中文长文本精度"和"多语种混排兼容性"
- 影响：用户日常使用中德英三语混排，当前方案在非 CJK 语言场景下 memory search 可靠性不足
- 可选修复方向（未排序）：
  - A. 语言检测 + 分词器路由：检测文本主要语言，CJK-heavy 用 Jieba，multilingual 用 ICU
  - B. 双路索引：Jieba + ICU 各建一份 search_text，查询时合并结果
  - C. 迁移到 ParadeDB pg_search BM25 后利用其原生 ICU tokenizer 配置
  - D. 混合方案：CJK 部分用 Jieba 切分，非 CJK 部分保留原文交给 ICU/simple
- 涉及代码：`src/memory/query_processor.py`、migration `b2c3d4e5f6a7`
- 优先级：非阻塞（当前中文场景可用），但影响多语种用户体验

## OI-M3-02 `.env` 污染导致 test_app_integration 和 AuthSettings 测试失败

- 发现于：P2-M3 用户测试期间（本地运行全量测试）
- 现象：11 个既有测试失败，分属两类根因
- 失败清单：
  - `test_app_integration.py::test_agent_loop_has_compaction_settings`
  - `test_app_integration.py::test_m3_tools_registered_and_wired`
  - `test_app_integration.py::test_empty_bot_token_skips_telegram`
  - `test_app_integration.py::test_agent_loop_has_procedure_runtime`
  - `test_principal_store.py::test_auth_settings_defaults`
  - `test_principal_schema.py::test_single_owner_partial_unique_index`
  - `test_principal_schema.py::test_binding_unique_constraint`
  - `test_principal_schema.py::test_principal_fk_on_delete_restrict_binding`
  - `test_principal_schema.py::test_session_principal_id_fk_on_delete_restrict`
  - `test_principal_store.py::test_ensure_owner_creates_principal`
  - `test_principal_store.py::test_get_owner_returns_none_when_empty`

### 根因 A：`.env` 污染 AuthSettings（5 个 app_integration + 1 个 auth_settings）

- `src/config/settings.py` 在 import 时调用 `load_dotenv()`，将 `.env` 中的 `AUTH_PASSWORD_HASH` 和 `AUTH_OWNER_NAME=TestOwner` 注入 `os.environ`
- `_make_mock_settings()` 第 53 行 `settings.auth = AuthSettings()` 注释标注 "no-auth mode"，但 `AuthSettings` 作为 `pydantic-settings` 的 `BaseSettings` 自动读取环境变量，实际进入 auth mode
- `lifespan()` 在 auth mode 下调用 `PrincipalStore.ensure_owner()`，但测试传入的 `fake_session_factory` 是 `MagicMock` → `coroutine object has no attribute 'password_hash'`
- `test_auth_settings_defaults` 同理：期望 `owner_name == "Owner"`（默认值），实际读到 `.env` 中的 `"TestOwner"`
- 修复：显式传参 `AuthSettings(password_hash=None, owner_name="Owner")` 覆盖 env，或 `monkeypatch.delenv()` 清除相关变量

### 根因 B：principal schema 测试隔离失败（5 个 principal_schema/store）

- `test_single_owner_partial_unique_index` 插入 `role='owner'` 的 principal，后续测试也尝试插入 owner → 碰到 `uq_principals_single_owner` 唯一约束
- `_integration_cleanup` fixture（conftest.py）在每个 integration 测试后 TRUNCATE 所有表，但 principal schema 测试之间存在残留：前一个测试的 owner 未被清理即进入下一个测试
- 单独运行每个测试均能通过，批量运行则失败 — 典型的测试隔离问题
- 修复：确认 `_integration_cleanup` 的 TRUNCATE 顺序是否考虑了 FK 依赖（先 bindings/sessions 再 principals），或在 principal schema 测试中显式清理
