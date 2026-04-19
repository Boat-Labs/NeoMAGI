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

## OI-M3-03 前端 WS auth 失败后死循环重连，不跳转登录页（已修复）

- 发现于：P2-M3 用户测试 T08（Login UI + JWT 认证）
- 现象：后端重启后（jwt_secret 重新生成），前端持有 localStorage 中的旧 JWT token，浏览器显示 "Connection lost. Reconnecting..."，不显示登录页面，后端日志出现大量 WS connect/close 循环
- 复现路径：
  1. Auth 模式下正常登录成功（JWT 存入 localStorage）
  2. 重启后端（jwt_secret 重新生成，`.env` 中未固定 `AUTH_JWT_SECRET`）
  3. 前端带旧 token 发送 auth RPC → 后端返回 `AUTH_FAILED` + close(4001)
  4. 前端 `onclose` 触发 `attemptReconnect` → 无限重连
- root cause（3 层）：
  1. **后端 `_authenticate_ws` 未 catch `WebSocketDisconnect`**：pre-auth 阶段客户端断连冒泡为 uvicorn ERROR traceback（已修复：`12a8ed5`）
  2. **前端 `WebSocketClient` auth 失败后未停止重连**：`onAuthFailed` 回调被调用，但 `onclose` 也触发了 `attemptReconnect()`，因为 `intentionalClose` 仍为 `false`（已修复：`dcc4e9a`，auth 失败时先 `this.close()` 再回调）
  3. **前端 `onAuthFailed` 回调使用 `require()` 动态导入 store 在 Vite ESM 环境下不可靠**：`chat.ts` 中 `const { useAuthStore } = require("@/stores/auth")` 的 CommonJS `require` 在 Vite ESM 模式下可能静默失败，导致 `logout()` 未执行、token 未清除、页面未跳转（已修复：改为 `localStorage.removeItem()` + `window.location.reload()`）
- 涉及代码：
  - `src/gateway/app.py` `_authenticate_ws()`：catch `WebSocketDisconnect`
  - `src/lib/websocket.ts` `WebSocketClient`：auth 失败时 `close()` 停止重连
  - `src/stores/chat.ts` `onAuthFailed` 回调：清 token + 刷新页面
- Vite proxy 配置（`src/frontend/vite.config.ts`）：auth 模式下前端 dev server 需要 proxy `/auth` 和 `/ws` 到后端，否则 login 和 WS 鉴权均不可达

## OI-M3-04 Lexical search 无法匹配语义查询，recall 不及预期

- 发现于：P2-M3 用户测试 T09（Owner 记忆 prompt 注入验证）
- 现象：用户问"请回忆一下，我之前提到过哪些技术话题？"，`memory_search` 返回 0 条结果。DB 中有 4 条相关记忆（tsvector 全文搜索、embedding 向量检索、Python asyncio、最喜欢的编程语言），但内容中均不包含"技术"或"话题"这两个词
- agent 仍能提到部分记忆，是因为 PromptBuilder 将当天 daily notes 加载到了 system prompt 中（非 search 路径）
- root cause：
  - model 传给 `memory_search` 的查询词为"技术话题"，Jieba 切分为 `技术` + `话题`
  - `plainto_tsquery('simple', '技术 话题')` 使用 AND 语义，要求两个 token 同时出现
  - 4 条记忆内容中均不含"技术"或"话题"的字面文本 → 0 命中
  - 这是 lexical search（tsvector）的固有限制：无法桥接"技术话题" → "tsvector / embedding / asyncio"的语义关联
- 与 OI-M3-01 的关系：OI-M3-01 是分词器对多语种的覆盖问题；OI-M3-04 是更根本的 lexical vs semantic 差距——即使分词器完美，纯词法匹配也无法处理同义/上下位概念查询
- 影响：用户使用抽象/概括性查询（"技术话题"、"之前的讨论"、"学习计划"）时，memory_search 大概率返回空，只能依赖 daily notes 的全量加载作为 fallback
- 可选修复方向：
  - A. 引入 embedding 向量检索（Ollama 优先 → OpenAI fallback），lexical + semantic 双路合并
  - B. 查询扩展：model 生成多组关键词 → 多次 search → 合并去重
  - C. 当 search 返回 0 条时，prompt 提示 model 用更具体的关键词重试
- 涉及代码：`src/memory/searcher.py`、`src/agent/prompt_builder.py`（daily notes fallback）
- 优先级：非阻塞（daily notes 加载提供了部分补偿），但显著影响 memory search 的用户感知可靠性
