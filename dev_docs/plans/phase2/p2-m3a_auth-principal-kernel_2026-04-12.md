---
doc_id: 019d814e-42c1-7518-b0d1-cc21e7c87c39
doc_id_format: uuidv7
doc_id_assigned_at: 2026-04-12T12:48:03+02:00
---
# P2-M3a 实现计划：Auth & Principal Kernel

> 状态：approved
> 日期：2026-04-12
> 输入：`design_docs/phase2/p2_m3_architecture.md` Section 3 (P2-M3a)
> 架构基础：ADR 0034 (dmScope alignment), ADR 0059 (Shared Companion boundary), ADR 0060 (Memory Source Ledger)
> 前置完成：P2-M2d (Memory Source Ledger Prep)

## 0. 目标

为 WebChat 引入可验证身份，建立 canonical principal 与 binding 模型，使"同一个已认证用户"的跨渠道连续性具备产品基础。

回答的问题：**谁在使用 NeoMAGI，系统怎么知道两个渠道 identity 是同一个用户？**

完成后：
- WebChat 支持密码登录，session 绑定到可验证 principal
- 匿名路径继续可用（只限未被 claim 的 session），但不进入用户级连续性
- canonical `principals` + `principal_bindings` 表存在并可运行
- Telegram 渠道的 peer_id 可通过 binding 关联到 principal
- 未验证 binding 默认不合并
- `SessionIdentity` 携带 `principal_id`，全链路传播到 dispatch / agent / tool context / procedure metadata
- `ProcedureExecutionMetadata.principal_id` 从 reserved 变为 populated（runtime API 层；端到端 WebSocket→enter 需等 procedure entry surface 建立）

## 1. 当前基线

| 组件 | 状态 |
|------|------|
| `SessionIdentity` (`src/session/scope_resolver.py`) | frozen dataclass；`peer_id`/`account_id` 声明存在但标记 M4 |
| WebSocket `/ws` (`src/gateway/app.py:561`) | 无认证，`accept()` 后直接处理 RPC |
| `ChatSendParams` (`src/gateway/protocol.py`) | `session_id` 默认 `"main"`；`CHANNEL_EXCLUSIVE_PREFIXES` 拦截 `telegram:`/`peer:` |
| `SessionRecord` (`src/session/models.py`) | 无 `principal_id` 列 |
| `SessionManager.try_claim_session()` (`src/session/manager.py:141`) | `INSERT ON CONFLICT DO UPDATE` 原子 claim；不传 principal_id |
| `dispatch_chat()` (`src/gateway/dispatch.py:164`) | claim → load → budget → handle → settle → release；identity 透传但 claim 层不使用 |
| Telegram adapter (`src/channels/telegram.py`) | 提供 `peer_id`，whitelist 鉴权；`dm_scope` 默认 `per-channel-peer` |
| `TelegramSettings.dm_scope` (`src/config/settings.py:175`) | 默认 `"per-channel-peer"`，scope_key = `"telegram:peer:{id}"` |
| `SessionSettings.dm_scope` (`src/config/settings.py:68`) | WebChat 固定 `"main"`，validator 阻止非 main |
| `RequestState` (`src/agent/message_flow.py:24`) | 无 `principal_id` 字段；identity 不存储在 state 中 |
| `ToolContext` (`src/tools/context.py:12`) | `scope_key` + `session_id` + `actor` + `handoff_id` + `procedure_deps`；无 `principal_id` |
| `AgentLoop._execute_tool()` (`src/agent/agent.py:244`) | 签名 `(tool_name, args_json, *, scope_key, session_id, guard_state)` → 调用 `tool_runner.execute_tool()` |
| `tool_runner.execute_tool()` (`src/agent/tool_runner.py:17`) | 签名 `(loop, tool_name, args_json, *, scope_key, session_id, guard_state)` → 内部构造 `ToolContext(scope_key=, session_id=)` |
| `tool_concurrency._run_single_tool()` (`src/agent/tool_concurrency.py:202`) | 普通 tool：调 `loop._execute_tool(..., scope_key=state.scope_key, session_id=state.session_id)` |
| `tool_concurrency._run_procedure_action()` (`src/agent/tool_concurrency.py:232`) | 构造 `ToolContext(scope_key=, session_id=, actor=, procedure_deps=)` |
| `ProcedureExecutionMetadata` (`src/procedures/types.py:107`) | `principal_id`/`visibility_intent`/`publish_target` 为 reserved None |
| `procedure_bridge.resolve_procedure_for_request()` (`src/agent/procedure_bridge.py`) | **只 load active procedure**，不调用 `enter_procedure()`；无 metadata 构造 |
| `ProcedureRuntime.enter_procedure()` (`src/procedures/runtime.py:82`) | 接受 `execution_metadata` 参数；**当前无生产调用方**（仅 runtime + 测试） |
| `memory_source_ledger` (P2-M2d) | `scope_key` 列存在；无 `principal_id` 列 |
| 前端 (`src/frontend/`) | 纯 WebSocket，`onConnected` 立刻 `loadHistory()`（`chat.ts:258`），无 login UI，无 auth state |
| CORS (`src/gateway/app.py:495`) | `app.add_middleware(CORSMiddleware, allow_origins=["*"])` — **模块级执行**，在 lifespan 之前 |
| 依赖 (`pyproject.toml`) | 无 bcrypt / PyJWT 库 |

## 2. 核心决策

### D1：Auth 机制 — 密码 + JWT，极简单用户

NeoMAGI 是个人 agent harness，当前阶段只有 owner 一个真实用户。Auth 目标不是多租户系统，而是：
1. 确认 WebChat 连接方是 owner（而非匿名访客）
2. 提供可验证的 principal identity

选择：
- **密码认证**：环境变量 `AUTH_PASSWORD_HASH` 配置 owner 密码的 bcrypt hash（`$2b$` 前缀）。前端登录表单提交明文密码，后端 `bcrypt.checkpw()` 验证后返回 JWT。
- **JWT token**：HS256 签名（PyJWT），payload 含 `principal_id` + `exp`；token 存储在 localStorage，WebSocket 连接时通过首条 `auth` RPC 消息传递。
- **无外部 OAuth / SSO**：单用户场景不需要；后续若加 guest principal 再评估。
- **新增依赖**：`bcrypt`（密码验证）、`PyJWT`（token 签发/验证）— 加入 `pyproject.toml`。

放弃：
- HTTP cookie session：WebSocket 不天然携带 cookie，需要额外握手复杂度。
- WebSocket URL query param token：token 会出现在服务器日志，安全风险。
- mTLS / client cert：运维复杂度过高。
- 接受明文密码 env var：`.env` 泄漏即密码泄漏；hash-only 更安全。

**密码 hash 生成辅助**：新增 `just hash-password` 任务（调用 `python -c "import bcrypt; ..."`），让用户无需记住 bcrypt CLI。

### D2：Principal 模型 — 单表 `principals`

```
principals
├── id: VARCHAR(36) PK          # UUIDv7
├── name: VARCHAR(128) NOT NULL  # 显示名 (owner 的名字)
├── password_hash: VARCHAR(256)  # bcrypt hash; NULL = 不允许密码登录
├── role: VARCHAR(16) NOT NULL   # 'owner' (V1 只有 owner)
├── created_at: TIMESTAMPTZ
└── updated_at: TIMESTAMPTZ

PARTIAL UNIQUE INDEX: uq_principals_single_owner ON principals(role) WHERE role = 'owner'
```

V1 约束：
- `role = 'owner'` 的 partial unique index 硬性保证最多一个 owner（防竞态/手工数据导致多 owner）。
- 系统启动时，若 `principals` 表无 owner 且 `AUTH_PASSWORD_HASH` 非空，自动创建 owner principal。
- `role` 枚举 V1 只有 `owner`；`guest` 为 reserved，P3+ Shared Companion 时激活。

### D3：Binding 模型 — `principal_bindings` 表

```
principal_bindings
├── id: VARCHAR(36) PK            # UUIDv7
├── principal_id: VARCHAR(36) FK  # → principals.id ON DELETE RESTRICT
├── channel_type: VARCHAR(32)     # 'webchat' | 'telegram'
├── channel_identity: VARCHAR(256) # 渠道侧 unique identity (peer_id / login username)
├── verified: BOOLEAN NOT NULL    # 是否已验证
├── created_at: TIMESTAMPTZ
└── updated_at: TIMESTAMPTZ

UNIQUE (channel_type, channel_identity)
```

语义：
- **verified = true**：该渠道身份已通过可信途径（密码登录 / Telegram whitelist）关联到 principal。
- **verified = false**：关联存在但未验证（V1 不产生未验证 binding；reserved for future self-claim flow）。
- **未验证 binding 不合并**：scope resolver 只接受 verified binding 产出的 principal_id。
- **冲突处理**：`ensure_binding()` 若发现已有 binding 指向**不同** principal_id，raise `BindingConflictError`（fail-closed），不静默返回。

### D4：WebSocket Auth 协议 — `auth` RPC method + ready 状态分离

当前 `/ws` endpoint 无条件 `accept()`。改为：

1. 服务端仍无条件 `accept()`（WebSocket 协议不支持在握手前做复杂 auth）。
2. 连接建立后进入 **pre-auth 阶段**：服务端只接受 `auth` method 的 RPC，其他 method 一律返回 `AUTH_REQUIRED` error。
3. 客户端发送 `auth` RPC：
   ```json
   {"type": "request", "id": "...", "method": "auth", "params": {"token": "<jwt>"}}
   ```
4. 服务端验证 JWT → 提取 `principal_id` → 连接进入 **authenticated 阶段**：后续所有 RPC 请求自动携带该连接的 principal identity。
5. Auth 失败（token 无效/过期）→ 返回 `AUTH_FAILED` error + close connection。
6. 10s 内未收到 `auth` RPC → 返回 `AUTH_TIMEOUT` error + close connection。

**匿名回退路径**：
- 若 `AUTH_PASSWORD_HASH` 未配置，系统运行在 **no-auth mode**：跳过 pre-auth 阶段，连接后立刻进入 ready 状态。
- Anonymous 连接 `principal_id = None`，不进入用户级连续性。
- 这保证了开发/测试阶段的零配置可用性。

**前端对齐（critical — 解决 onConnected 竞态）**：
- 当前前端 `onConnected` 回调立刻调用 `loadHistory()`（`chat.ts:258`），会在 auth 完成前发送 `chat.history` RPC，导致被 pre-auth guard 拒绝。
- 改为：WebSocket `onopen` → 若 auth mode 则发送 `auth` RPC → 收到 auth success response → 触发 `onAuthenticated` 回调 → 在此回调中调用 `loadHistory()`。
- No-auth mode：`onopen` 直接触发原有的 `onConnected` 行为（无变更）。
- auth 失败或超时：触发 `onAuthFailed` 回调 → 前端切换到 LoginForm。

### D5：Login HTTP endpoint — `POST /auth/login`

WebSocket 之前需要先获取 JWT。新增 HTTP endpoint：

```
POST /auth/login
Content-Type: application/json
{"password": "..."}

→ 200: {"token": "<jwt>", "principal_id": "...", "name": "...", "expires_at": "..."}
→ 401: {"error": {"code": "AUTH_FAILED", "message": "Invalid password"}}
→ 429: {"error": {"code": "AUTH_RATE_LIMITED", "message": "Too many attempts"}}
```

**Rate limiter（顺序 critical）**：
1. **先检查 lockout**：若 IP 在冷却期内 → 429
2. **验证密码**：`bcrypt.checkpw()`
3. **失败 → 记录计数**：5 failures/min → 5 min lockout
4. **成功 → 清零计数** → 签发 JWT → 200

内存 dict `{ip: (fail_count, window_start)}`。不持久化（重启清零可接受）。

**Auth status endpoint**：
```
GET /auth/status
→ 200: {"auth_required": true/false}
```
前端启动时查询此 endpoint 确定是否需要显示 LoginForm。

### D6：SessionIdentity 扩展 — 加入 `principal_id`

```python
@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    channel_type: str = "dm"
    channel_id: str | None = None
    peer_id: str | None = None
    account_id: str | None = None
    principal_id: str | None = None  # P2-M3a: authenticated principal
```

- WebChat authenticated path: `principal_id` 从 JWT 填充。
- Telegram path: 通过 `principal_bindings` 查找 `(channel_type="telegram", channel_identity=peer_id, verified=true)` → 填充 `principal_id`。
- Anonymous path: `principal_id = None`。

### D7：Session ownership — 原子 claim 路径

**核心问题**：当前 `SessionManager` 没有 `create_or_load_session` 入口。真实链路是：
1. `dispatch_chat()` 调用 `try_claim_session(session_id, ttl_seconds)` — 原子 `INSERT ON CONFLICT DO UPDATE`
2. `load_session_from_db(session_id)` — hydrate cache
3. `append_message()` — 持久化消息

v2 计划的 claim-on-first-auth 放在 "create_or_load" 层会导致 authorize 与 claim 之间有竞态。必须把 principal 授权与 session lock 获取放在同一个原子 SQL 中。

**方案：扩展 `try_claim_session` 为 `claim_session_for_principal`**

新增 `SessionManager.claim_session_for_principal()` 方法，签名：

```python
async def claim_session_for_principal(
    self,
    session_id: str,
    *,
    principal_id: str | None,
    auth_mode: bool,
    ttl_seconds: int = 300,
) -> ClaimResult:
```

`ClaimResult`：
```python
@dataclass(frozen=True)
class ClaimResult:
    lock_token: str | None   # None = claim 失败
    error_code: str | None   # None = 成功；SESSION_BUSY / SESSION_AUTH_REQUIRED / SESSION_OWNER_MISMATCH
```

**原子 SQL 行为**（单 statement + RETURNING）：

```sql
-- Step 1: Upsert with claim attempt
INSERT INTO sessions (id, principal_id, lock_token, processing_since, next_seq)
VALUES (:sid, :principal_id, :token, now(), 0)
ON CONFLICT (id) DO UPDATE SET
    lock_token = :token,
    processing_since = now(),
    -- Claim NULL→principal (only if currently NULL and caller is authenticated):
    principal_id = CASE
        WHEN sessions.principal_id IS NULL THEN :principal_id
        ELSE sessions.principal_id  -- preserve existing owner
    END
WHERE
    -- Lock available (normal claim logic):
    (sessions.processing_since IS NULL
     OR sessions.processing_since < now() - make_interval(secs => :ttl_seconds))
RETURNING id, principal_id;
```

> **实现备注**：SQL 示例为伪代码。实现时使用 SQLAlchemy 安全构造（如 `func.make_interval(secs=ttl_seconds)` 或沿用现有 `text(f"interval '{ttl_seconds} seconds'")` + 先校验 `ttl_seconds` 为 int），不得直接拼接用户输入。

**Entry guard（函数入口 fail-closed）**：

```python
# auth_mode=True 要求必须有 principal_id；防止实现 bug 在 auth mode 下创建匿名 session
if auth_mode and principal_id is None:
    return ClaimResult(lock_token=None, error_code="SESSION_AUTH_REQUIRED")
```

**Post-claim validation（在同一事务中，claim SQL 返回后立刻检查）**：

```python
if not claimed:
    return ClaimResult(lock_token=None, error_code="SESSION_BUSY")

# claimed, now check ownership
stored_principal = result.principal_id

if auth_mode:
    # entry guard 已保证 principal_id is not None
    if stored_principal is not None and stored_principal != principal_id:
        await _release_lock(...)
        return ClaimResult(lock_token=None, error_code="SESSION_OWNER_MISMATCH")

if not auth_mode:
    if stored_principal is not None:
        # no-auth mode trying to access a claimed session -> deny
        await _release_lock(...)
        return ClaimResult(lock_token=None, error_code="SESSION_AUTH_REQUIRED")

return ClaimResult(lock_token=lock_token, error_code=None)
```

**Session ownership 规则矩阵**：

| DB session 状态 | auth_mode | caller principal_id | 行为 | 结果 |
|---|---|---|---|---|
| (any) | true | NULL | **entry guard reject** | SESSION_AUTH_REQUIRED |
| 不存在 | true | P | INSERT(principal_id=P) | OK, claimed & bound |
| 不存在 | false | NULL | INSERT(principal_id=NULL) | OK, anonymous session |
| `principal_id=NULL` | true | P | UPSERT -> SET principal_id=P (claim) | OK, session claimed by P |
| `principal_id=NULL` | false | NULL | UPSERT -> preserve NULL | OK, anonymous session |
| `principal_id=P` | true | P (same) | UPSERT -> preserve P | OK |
| `principal_id=P` | true | Q (diff) | UPSERT -> preserve P | **REJECT**: SESSION_OWNER_MISMATCH |
| `principal_id=P` | false | NULL | UPSERT -> preserve P | **REJECT**: SESSION_AUTH_REQUIRED |

**关键安全属性**：
- **no-auth 不泄漏已 claim session**：no-auth mode 下，若 DB 存在 `principal_id IS NOT NULL` 的 session，匿名连接 claim 该 session 会被 post-claim 拒绝（`SESSION_AUTH_REQUIRED`）。这防止了误删 `AUTH_PASSWORD_HASH` 后匿名访问 owner 历史。
- **claim 是原子的**：principal_id 的赋值与 lock 获取在同一 INSERT ON CONFLICT 中完成，无竞态窗口。
- **不做 session merge**：不把不同 principal 的 session 历史合并。

**Dispatch 集成**：`dispatch_chat()` 中 `_claim_session_or_raise` 替换为调用 `claim_session_for_principal()`，传入 `identity.principal_id` 和 `auth_mode`。

**Preflight 检查**：no-auth mode 启动时扫描 `sessions` 表，若存在 `principal_id IS NOT NULL` 的行 → preflight WARN（提示可能存在不可访问的 session 数据）。

### D8：Scope resolver 扩展 — principal 不参与 scope_key 生成

P2-M3a 不改变既有 dm_scope 行为：

- **WebChat**：`SessionSettings.dm_scope = "main"` → scope_key = `"main"`（不变）。
- **Telegram**：`TelegramSettings.dm_scope = "per-channel-peer"`（默认）→ scope_key = `"telegram:peer:{peer_id}"`（不变）。
- `principal_id` 作为**附加的访问控制维度**正确传播，但**不参与 scope_key 值的生成**。
- P2-M3b visibility policy 会在 scope_key 之上叠加 principal-based 过滤。

放弃方案：在 P2-M3a 就引入 `per-principal` scope_key — 会破坏现有 `scope_key = "main"` 和 `scope_key = "telegram:peer:*"` 的 session 与 memory 数据。

### D9：Telegram binding 自动创建

当 Telegram adapter 处理消息时，若 owner principal 存在：
- 查询 `principal_bindings` 是否存在 `(channel_type="telegram", channel_identity=peer_id)`。
- **not_found**：该 `peer_id` 在 `TELEGRAM_ALLOWED_USER_IDS` whitelist 中 → 自动创建 **verified** binding（whitelist 即验证证据）。
- **verified + 指向当前 owner** → 直接使用关联的 `principal_id`。
- **unverified + 同 owner** → 通过 `verify_binding()` 显式升级为 verified，然后使用。
- **unverified + 不同 principal 或无 owner** → fail-closed，不返回 `principal_id`。
- **verified/unverified + 指向不同 principal** → fail-closed（`BindingConflictError`），不静默使用。

这使 Telegram 渠道无需额外登录流程即可获得 principal identity。unverified binding 必须显式验证或拒绝，不会被静默合并。

### D10：Settings — `AuthSettings`

```python
class AuthSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTH_")

    password_hash: str | None = None   # bcrypt hash ($2b$ prefix); None = no-auth mode
    jwt_secret: str | None = None      # auto-generated if None
    jwt_expire_hours: int = 24
    owner_name: str = "Owner"          # display name for auto-created principal
```

- `AUTH_PASSWORD_HASH` 未设置 → no-auth mode，系统以匿名模式运行。
- `AUTH_PASSWORD_HASH` 必须是 bcrypt hash（`$2b$` 前缀），启动时 validator 检查格式。
- `AUTH_JWT_SECRET` 未设置 → 启动时自动生成随机 secret（重启后旧 token 失效，单用户场景可接受）。

**密码轮换**：`ensure_owner()` 每次启动比较 `AUTH_PASSWORD_HASH` 与 DB 中的 `password_hash`。若不同（用户改了密码），UPDATE 生效。若相同或 DB 中已有 owner，幂等。

### D11：RPC 授权 — authorize-and-stamp 统一 session 访问控制

当前 `chat.send`、`chat.history`、`session.set_mode` 三个 RPC handler 都接受 client-controlled `session_id`。引入 auth 后，必须校验连接的 `principal_id` 是否有权访问目标 session。

**核心问题（v3 遗留）**：v3 的 `authorize_session_access()` 只做读检查、不做 stamp。前端认证成功后首个 RPC 是 `chat.history`（`loadHistory()`），不经过 `dispatch_chat()` / `claim_session_for_principal()`。结果 legacy `"main"` session 的 `principal_id` 保持 NULL，误切 no-auth 后仍可匿名读到 owner 历史。

**方案：`authorize_and_stamp_session()` — 授权 + 轻量 stamp 合一**

```python
async def authorize_and_stamp_session(
    session_manager: SessionManager,
    session_id: str,
    principal_id: str | None,
    auth_mode: bool,
) -> None:
    """校验 principal 是否可访问 session，并在必要时 stamp ownership。
    Raises GatewayError on denial.

    Entry guard (与 claim_session_for_principal 一致):
    - auth_mode=True and principal_id is None → REJECT (SESSION_AUTH_REQUIRED)

    Step 1 — Authorize:
    - Session 不存在 → allow（后续 claim/create 处理）
    - Session.principal_id = caller.principal_id → allow
    - Session.principal_id IS NOT NULL, caller is NULL → REJECT (SESSION_AUTH_REQUIRED)
      (同时适用于 no-auth mode：不因 auth_mode=False 就放行已 claim 的 session)
    - Session.principal_id != caller.principal_id (both non-NULL) → REJECT (SESSION_OWNER_MISMATCH)

    Step 2 — Stamp (only if authorized):
    - Session.principal_id IS NULL, caller.principal_id IS NOT NULL →
      UPDATE sessions SET principal_id = :pid WHERE id = :sid AND principal_id IS NULL
      (conditional UPDATE, no lock needed, idempotent — 竞态安全：两个相同 principal 并发 stamp 结果一致)
    - Session.principal_id IS NULL, caller is NULL → no stamp (anonymous session stays anonymous)
    """
```

**Entry guard 一致性**：`claim_session_for_principal()`（D7）和 `authorize_and_stamp_session()` 共享同一条 fail-closed 规则：`auth_mode=True and principal_id is None → reject`。这确保读路径和写路径对 auth mode 匿名 caller 的拒绝行为一致，防止非 WebSocket call site 绕过握手。

**Stamp 与 claim_session_for_principal 的关系**：
- `claim_session_for_principal()`（D7）= 写路径（`chat.send`），获取 lock + stamp + 创建 session。
- `authorize_and_stamp_session()`（D11）= 读/轻量写路径（`chat.history`、`session.set_mode`），不获取 lock，只做 stamp。
- 两者对 NULL→principal stamp 的行为一致（conditional UPDATE WHERE principal_id IS NULL）。
- `chat.send` 经过两层：先 `authorize_and_stamp_session()`，再 `claim_session_for_principal()` — defense-in-depth。

**安全属性**：
- 首次认证 `loadHistory("main")` → stamp 将 `"main"` session 的 `principal_id` 从 NULL 改为 owner → 后续 no-auth 访问被拒绝。
- no-auth mode 对 NULL session 放行（不 stamp），对 non-NULL session 拒绝。
- auth_mode=True + principal_id=None → 读路径和写路径均 reject，无例外。

三个 RPC handler (`_handle_chat_send`, `_handle_chat_history`, `_handle_session_set_mode`) 在执行前均调用此 helper。

**`session.set_mode` 额外约束**：当前 `SessionManager.set_mode()` 使用 `pg_insert().on_conflict_do_update()` 会在 session 不存在时创建 `principal_id=NULL` 的 session（`manager.py:128`）。这在 auth mode 下会绕过 principal binding。修改方案：
- `set_mode()` 签名扩展为 `set_mode(session_id, mode, *, principal_id=None, auth_mode=False)`。
- Upsert 的 `.values()` 加入 `principal_id=principal_id`。
- Post-upsert 检查与 `claim_session_for_principal()` 一致：auth_mode=True 且 principal_id=None → reject（entry guard 已处理）；创建新 session 时 principal_id 从 caller 填入；已存在 session 不更新 principal_id（由 stamp 和 authorize 负责）。
- `_handle_session_set_mode()` 传入 `principal_id` 和 `auth_mode`。

### D12：Identity 全链路传播 — 精确改动清单

当前 principal_id 不存在于 `RequestState`、`ToolContext` 或 tool 调用签名中。以下是从 WebSocket auth 到 tool execution 的完整改动链。

**1. `SessionIdentity`** → D6 已定义。

**2. `dispatch_chat()`** (`src/gateway/dispatch.py:164`)
- 已接收 `identity: SessionIdentity | None`，改为提取 `identity.principal_id` 传入 `claim_session_for_principal()`。

**3. `_handle_chat_send()`** (`src/gateway/app.py:637`)
- 构造 `SessionIdentity(session_id=parsed.session_id, principal_id=principal_id)` 传给 `dispatch_chat()`。

**4. `RequestState`** (`src/agent/message_flow.py:24`)
- 增加字段 `principal_id: str | None = None`。
- `_initialize_request_state()` 从参数 `identity.principal_id` 填充。

**5. `ToolContext`** (`src/tools/context.py:12`)
- 增加字段 `principal_id: str | None = None`。

**6. `tool_runner.execute_tool()`** (`src/agent/tool_runner.py:17`)
- 签名新增 `principal_id: str | None = None`。
- 构造 `ToolContext(scope_key=scope_key, session_id=session_id, principal_id=principal_id)`。

**7. `AgentLoop._execute_tool()`** (`src/agent/agent.py:244`)
- 签名新增 `principal_id: str | None = None`，透传到 `tool_runner.execute_tool()`。

**8. `tool_concurrency._run_single_tool()`** (`src/agent/tool_concurrency.py:202`)
- 普通 tool 路径（line 221）：`loop._execute_tool(..., principal_id=state.principal_id)`。

**9. `tool_concurrency._run_procedure_action()`** (`src/agent/tool_concurrency.py:232`)
- ToolContext 构造（line 251）：`ToolContext(scope_key=, session_id=, actor=, procedure_deps=, principal_id=state.principal_id)`。

**10. `ProcedureRuntime.enter_procedure()`** (`src/procedures/runtime.py:82`)
- Runtime API 已接受 `execution_metadata: ProcedureExecutionMetadata`，其中含 `principal_id` 字段。
- **当前无生产调用方**（`procedure_bridge.resolve_procedure_for_request()` 只 load，不 enter）。
- P2-M3a 保证：API 层 `enter_procedure()` 接收并持久化 `principal_id`；`apply_action()` 的 tool context 包含 `principal_id`。
- **不宣称** WebSocket auth → `enter_procedure()` 的端到端填充，因为 procedure entry surface（LLM 决策触发 enter）尚未建立。这属于后续 procedure UX 工作。

**兼容性**：所有新增参数均为 `= None` 默认值。现有 tool/agent 测试不需修改签名即可通过。

### D13：Auth mode 网络边界 — 静态 CORS + WebSocket Origin guard

当前 `app.add_middleware(CORSMiddleware, allow_origins=["*"])` 在**模块级**执行（`app.py:495`），lifespan 中才加载 settings。在 lifespan 中动态修改 CORS 配置不可靠（middleware 已绑定）。

**方案：保持 CORS 静态，WebSocket Origin guard 作为真正执行边界**

放弃动态 CORS 方案。改为：

1. **CORS 保持 `allow_origins=["*"]`**：不改变模块级 middleware。CORS 只约束浏览器跨源 AJAX（`POST /auth/login`），不约束 WebSocket。
2. **WebSocket Origin guard**（auth mode only）：
   - `websocket_endpoint()` 在 `accept()` 前检查 `websocket.headers.get("origin")`。
   - `GATEWAY_ALLOWED_ORIGINS` 设置（逗号分隔）控制 allowed list。
   - Auth mode + Origin 不在 allowed list → reject（close before accept）。
   - No-auth mode 或 `GATEWAY_ALLOWED_ORIGINS` 未设置 → 不检查。
3. **Login endpoint Origin check**：`POST /auth/login` handler 内检查 `Origin` header（auth mode only）。
4. **Preflight warning**：auth mode + `GATEWAY_HOST = 0.0.0.0` + `GATEWAY_ALLOWED_ORIGINS` 未显式配置 → preflight warning。

这比动态 CORS 更简单、更可靠，且真正的安全边界（WebSocket auth + Origin check）不依赖 CORS middleware。

### D14：Backup/restore 覆盖新表

`scripts/backup.py` 的 `TRUTH_TABLES` 必须加入 `principals` 和 `principal_bindings`。

## 3. 实现切片

### Slice A：DB Schema — `principals` + `principal_bindings` 表 + `sessions.principal_id` 列

**新增文件**：
- `alembic/versions/<hash>_create_principals_and_bindings.py`

**修改文件**：
- `src/session/models.py`：新增 `PrincipalRecord`、`PrincipalBindingRecord` ORM 模型
- `src/session/models.py`：`SessionRecord` 增加 `principal_id` 列（nullable VARCHAR(36), FK → principals.id ON DELETE RESTRICT）
- `src/session/database.py`：`ensure_schema()` 增加新表的 idempotent DDL

**`principals` 表**：
| 列 | 类型 | 约束 |
|----|------|------|
| `id` | VARCHAR(36) | PK |
| `name` | VARCHAR(128) | NOT NULL |
| `password_hash` | VARCHAR(256) | NULL |
| `role` | VARCHAR(16) | NOT NULL, DEFAULT 'owner' |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

索引：`uq_principals_single_owner` PARTIAL UNIQUE on `(role) WHERE role = 'owner'`

**`principal_bindings` 表**：
| 列 | 类型 | 约束 |
|----|------|------|
| `id` | VARCHAR(36) | PK |
| `principal_id` | VARCHAR(36) | NOT NULL, FK → principals.id ON DELETE RESTRICT |
| `channel_type` | VARCHAR(32) | NOT NULL |
| `channel_identity` | VARCHAR(256) | NOT NULL |
| `verified` | BOOLEAN | NOT NULL, DEFAULT false |
| `created_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |
| `updated_at` | TIMESTAMPTZ | NOT NULL, DEFAULT now() |

索引：
- `uq_principal_bindings_channel` UNIQUE on `(channel_type, channel_identity)`
- `idx_principal_bindings_principal` on `principal_id`

**`sessions` 表修改**：
- 新增列 `principal_id VARCHAR(36) NULL`，FK → `principals.id ON DELETE RESTRICT`

**测试**：
- `tests/test_principal_schema.py`：migration up/down、idempotent ensure_schema、model CRUD、single-owner partial unique 约束、FK ON DELETE RESTRICT 行为

### Slice B：Auth Settings + Principal Store + 依赖

**新增文件**：
- `src/auth/__init__.py`
- `src/auth/settings.py`：`AuthSettings(BaseSettings)`（D10）
- `src/auth/store.py`：`PrincipalStore` — principal 与 binding 的 CRUD 封装
- `src/auth/errors.py`：`BindingConflictError(NeoMAGIError)`

**新增依赖**（`pyproject.toml`）：`bcrypt`、`PyJWT`

**`PrincipalStore` API**：

```python
class PrincipalStore:
    def __init__(self, db_session_factory: async_sessionmaker) -> None: ...

    async def get_owner(self) -> PrincipalRecord | None:
        """返回 role='owner' 的 principal，不存在返回 None。"""

    async def ensure_owner(
        self, *, name: str, password_hash: str,
    ) -> PrincipalRecord:
        """幂等创建 owner principal。

        若已存在且 password_hash 不同 → UPDATE（密码轮换）。
        若已存在且 password_hash 相同 → 返回现有记录。
        若不存在 → INSERT。
        """

    async def verify_password(self, password: str) -> PrincipalRecord | None:
        """bcrypt.checkpw() 验证。成功返回 principal，失败返回 None。"""

    async def get_binding(
        self, *, channel_type: str, channel_identity: str,
    ) -> PrincipalBindingRecord | None:
        """按渠道查找 binding。"""

    async def ensure_binding(
        self, *, principal_id: str, channel_type: str,
        channel_identity: str, verified: bool = False,
    ) -> PrincipalBindingRecord:
        """幂等创建 binding。

        若已存在且 principal_id 相同 → 返回现有记录。
        若已存在但 principal_id 不同 → raise BindingConflictError。
        """

    async def resolve_binding(
        self, *, channel_type: str, channel_identity: str,
    ) -> BindingResolution:
        """按渠道 identity 查找 binding。

        Returns BindingResolution(principal_id, status),
        status: 'verified' | 'unverified' | 'not_found'
        """

    async def verify_binding(
        self, *, channel_type: str, channel_identity: str,
    ) -> bool:
        """将 unverified binding 升级为 verified。

        Returns True if updated, False if not found or already verified.
        """
```

**修改文件**：
- `src/config/settings.py`：新增 `AuthSettings` 集成到 `get_settings()`
- `.env_template`：新增 `AUTH_PASSWORD_HASH`, `AUTH_JWT_SECRET`, `AUTH_OWNER_NAME` 模板

**测试**：
- `tests/test_principal_store.py`：ensure_owner 幂等、密码轮换 UPDATE、verify_password 正确/错误、binding CRUD、ensure_binding 冲突 raise、resolve_binding verified/unverified/not_found、verify_binding 升级/幂等/not_found

### Slice C：JWT 签发 + Login Endpoint + Rate limiter

**新增文件**：
- `src/auth/jwt.py`：`create_token(principal_id, secret, expire_hours)` → str、`verify_token(token, secret)` → payload dict | None
- `src/auth/rate_limiter.py`：`LoginRateLimiter` — 内存 IP rate limit

**修改文件**：
- `src/gateway/app.py`：新增 `POST /auth/login`、`GET /auth/status` endpoints
- `src/gateway/app.py`：lifespan 中构造 `PrincipalStore` + 调用 `ensure_owner()`
- `justfile`：新增 `hash-password` 任务

**Login endpoint 逻辑**（D5 — 顺序 critical）：
1. 若 no-auth mode → 返回 405 Method Not Allowed
2. **先检查 lockout** → 429 if locked
3. 解析 `{"password": "..."}`
4. `PrincipalStore.verify_password()` → principal or None
5. **失败 → 记录计数** → 401
6. **成功 → 清零计数** → 签发 JWT → 200

**测试**：
- `tests/test_auth_jwt.py`：create/verify token、expired token、invalid signature
- `tests/test_auth_login.py`：httpx AsyncClient 测试 login 成功/失败/rate-limit 顺序/lockout/no-auth-mode/auth-status
- `tests/test_rate_limiter.py`：计数/lockout/重置/过期窗口

### Slice D：WebSocket Auth 握手 + RPC 授权 guard

**新增文件**：
- `src/gateway/auth_guard.py`：`authorize_and_stamp_session()` helper（D11 — 授权 + 轻量 stamp 合一）

**修改文件**：
- `src/gateway/app.py`：`websocket_endpoint()` 增加 pre-auth 阶段 + Origin check
- `src/gateway/app.py`：三个 RPC handler 加入 `authorize_and_stamp_session()` 调用
- `src/gateway/app.py`：`_handle_rpc_message()` 签名增加 `principal_id: str | None`、`auth_mode: bool`
- `src/gateway/protocol.py`：新增 `AuthParams`、`RPCAuthResponse` 模型

**WebSocket auth 流程**（D4）：

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    # D13: Origin check (auth mode only, before accept)
    auth_settings: AuthSettings = app.state.auth_settings
    auth_mode = auth_settings.password_hash is not None
    if auth_mode and not _check_ws_origin(websocket, app.state.allowed_origins):
        await websocket.close(code=4003, reason="Origin not allowed")
        return

    await websocket.accept()
    principal_id: str | None = None

    if auth_mode:
        principal_id = await _authenticate_ws(websocket, app.state)
        if principal_id is None:
            return  # connection closed after auth failure

    logger.info("ws_connected", principal_id=principal_id, auth_mode=auth_mode)
    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_rpc_message(
                websocket, raw,
                principal_id=principal_id, auth_mode=auth_mode,
            )
    except WebSocketDisconnect:
        logger.info("ws_disconnected", principal_id=principal_id)
```

**测试**：
- `tests/test_ws_auth.py`：auth 成功、auth 失败关闭、non-auth RPC in pre-auth rejected、no-auth mode 跳过、timeout、Origin rejected
- `tests/test_rpc_authorization.py`：session access 矩阵（anonymous → claimed session rejected 含 no-auth mode、principal mismatch rejected、owner → own session OK、NULL principal session → anonymous OK）+ stamp 行为测试（authenticated chat.history 对 NULL session 执行 stamp、stamp 后 no-auth 访问被拒）

### Slice E：Session ownership + Identity 传播

**修改文件**：
- `src/session/scope_resolver.py`：`SessionIdentity` 增加 `principal_id: str | None = None`
- `src/session/manager.py`：新增 `claim_session_for_principal()` 方法（D7）；新增 `ClaimResult` dataclass
- `src/gateway/dispatch.py`：`dispatch_chat()` 签名新增 `auth_mode: bool = False`；`_claim_session_or_raise` 改为调用 `claim_session_for_principal()`，传入 `identity.principal_id` 和 `auth_mode`
- `src/gateway/app.py`：`_handle_chat_send()` 构造 `SessionIdentity(principal_id=principal_id, ...)`、传递 `auth_mode = auth_settings.password_hash is not None`
- `src/agent/message_flow.py`：`RequestState` 增加 `principal_id`；`_initialize_request_state()` 从 `identity.principal_id` 填充
- `src/tools/context.py`：`ToolContext` 增加 `principal_id: str | None = None`
- `src/agent/tool_runner.py`：`execute_tool()` 签名增加 `principal_id: str | None = None`
- `src/agent/agent.py`：`AgentLoop._execute_tool()` 签名增加 `principal_id: str | None = None`
- `src/agent/tool_concurrency.py`：`_run_single_tool()` 普通 tool 路径传 `principal_id=state.principal_id`；`_run_procedure_action()` ToolContext 加 `principal_id=state.principal_id`
- `src/session/manager.py`：`set_mode()` 签名扩展为 `set_mode(session_id, mode, *, principal_id=None, auth_mode=False)`；upsert 中加入 `principal_id`；post-upsert 检查与 `claim_session_for_principal()` entry guard 一致
- `src/gateway/app.py`：`_handle_session_set_mode()` 传入 `principal_id` 和 `auth_mode`
- `src/infra/preflight.py`：no-auth mode 扫描 sessions 表中 `principal_id IS NOT NULL` → WARN

**测试**：
- `tests/test_session_ownership.py`：D7 矩阵 8 个场景、claim 原子性、no-auth 访问已 claim session 被拒、preflight warning、set_mode 创建 session 时写入 principal_id、set_mode 对已 claimed session 的 owner mismatch reject
- `tests/test_identity_propagation.py`：principal_id 从 WebSocket auth → dispatch → RequestState → ToolContext (普通 tool: `tool_runner.execute_tool`) → ToolContext (procedure action: `_run_procedure_action`) 全链路传播
- 兼容性：现有 tool / agent 测试不修改即通过（新参数默认 None）

### Slice F：Telegram binding 集成 + dispatch_chat auth_mode 传递

**`dispatch_chat()` 新签名**（Slice E 中改动，此处说明 Telegram call site）：
```python
async def dispatch_chat(
    *, ...,
    identity: SessionIdentity | None = None,
    dm_scope: str | None = None,
    auth_mode: bool = False,           # 新增
    session_claim_ttl_seconds: int = 300,
) -> AsyncIterator[AgentEvent]:
```

**所有 call site 的 auth_mode 传递**：
- `_handle_chat_send()`（WebChat）：`auth_mode = app.state.auth_settings.password_hash is not None`
- `TelegramAdapter._dispatch_and_reply()`：`auth_mode = self._principal_store is not None`（binding 存在时 identity 已带 principal_id，auth_mode=True 保证 session 被正确 claim；binding 缺失 / no-auth 时 auth_mode=False，正常匿名行为）

**修改文件**：
- `src/channels/telegram.py`：`TelegramAdapter.__init__()` 接受 `PrincipalStore` 依赖
- `src/channels/telegram.py`：新增 `_enrich_identity_with_principal()` async helper（在 `_handle_message` async 上下文中调用，保持 `_resolve_identity` 同步不变）
- `src/channels/telegram.py`：`_dispatch_and_reply()` 调用 `dispatch_chat()` 时传入 `auth_mode = self._principal_store is not None`
- `src/gateway/app.py`：`_start_telegram()` 传入 `PrincipalStore`

**逻辑**：

```python
async def _enrich_identity_with_principal(
    self, identity: SessionIdentity, peer_id: str,
) -> SessionIdentity:
    """Lookup or auto-create verified binding, return enriched identity.

    Only verified bindings produce principal_id. Unverified bindings are
    explicitly skipped — they must not silently merge into user continuity.
    """
    if self._principal_store is None:
        return identity  # no-auth mode

    resolution = await self._principal_store.resolve_binding(
        channel_type="telegram", channel_identity=str(peer_id),
    )

    if resolution.status == "verified":
        return SessionIdentity(
            session_id=identity.session_id,
            channel_type=identity.channel_type,
            peer_id=identity.peer_id,
            principal_id=resolution.principal_id,
        )

    if resolution.status == "unverified":
        # Unverified binding exists but must NOT produce principal_id.
        # Telegram whitelist is sufficient verification evidence →
        # upgrade to verified via explicit verify_binding() call.
        owner = await self._principal_store.get_owner()
        if owner is not None and resolution.principal_id == owner.id:
            await self._principal_store.verify_binding(
                channel_type="telegram", channel_identity=str(peer_id),
            )
            return SessionIdentity(
                session_id=identity.session_id,
                channel_type=identity.channel_type,
                peer_id=identity.peer_id,
                principal_id=owner.id,
            )
        # unverified + different principal or no owner → do NOT merge
        return identity

    # status == "not_found": auto-create verified binding
    owner = await self._principal_store.get_owner()
    if owner is not None:
        await self._principal_store.ensure_binding(
            principal_id=owner.id,
            channel_type="telegram",
            channel_identity=str(peer_id),
            verified=True,
        )
        return SessionIdentity(
            session_id=identity.session_id,
            channel_type=identity.channel_type,
            peer_id=identity.peer_id,
            principal_id=owner.id,
        )

    return identity  # no owner exists
```

**`PrincipalStore.verify_binding()` 新增 API**：
```python
async def verify_binding(
    self, *, channel_type: str, channel_identity: str,
) -> bool:
    """将已有 unverified binding 升级为 verified。

    UPDATE principal_bindings SET verified=true
    WHERE channel_type=:ct AND channel_identity=:ci AND verified=false
    Returns True if updated, False if not found or already verified.
    """
```

此 API 与 `ensure_binding()` 职责分离：`ensure_binding()` 负责创建 binding（verified 或 unverified），`verify_binding()` 负责升级已有 unverified binding。两者语义不重叠。

**测试**：
- `tests/test_telegram_principal.py`：auto-binding 创建（not_found → verified）、已有 verified binding 复用、unverified binding 升级（verify_binding）、unverified + 不同 principal 不合并、binding 冲突 fail-closed、no-auth mode 跳过、no owner 跳过（enrich 不返回 principal_id → dispatch_chat(auth_mode=True, principal_id=None) 按 D7 entry guard fail-closed）

### Slice G：前端 Login UI + Auth 状态管理

**新增文件**：
- `src/frontend/src/stores/auth.ts`：Zustand auth store（token, principal_id, authRequired, login/logout）
- `src/frontend/src/components/LoginForm.tsx`：密码输入 + 登录按钮

**修改文件**：
- `src/frontend/src/lib/websocket.ts`：
  - 拆分 `onopen` 与 `onAuthenticated`
  - Auth mode: `onopen` → send `auth` RPC → wait for response → `onAuthenticated` → 上层调用 `loadHistory()`
  - No-auth mode: `onopen` → 直接触发 `onAuthenticated`（行为不变）
  - Auth failure: `onAuthFailed` 回调 → 前端显示 LoginForm
- `src/frontend/src/App.tsx`：启动时 `GET /auth/status` → 根据 `auth_required` + 本地 token 决定显示 LoginForm 或 Chat
- `src/frontend/src/stores/chat.ts`：
  - `onConnected` 回调改接 `onAuthenticated`
  - `disconnect` 时清空 auth state（if logout）

**UI 行为**：
1. App mount → `GET /auth/status`
2. `auth_required = false` → 直接 connect WebSocket → onAuthenticated → loadHistory（行为不变）
3. `auth_required = true` + 有 localStorage token → connect WebSocket → send auth RPC → success → onAuthenticated → loadHistory
4. `auth_required = true` + 无 token → 显示 LoginForm
5. 用户输入密码 → `POST /auth/login` → 存储 token → connect WebSocket + auth
6. Token 过期 → WebSocket auth 失败 → `onAuthFailed` → 显示 LoginForm
7. Logout → 清除 token + disconnect WebSocket

**测试**：
- `src/frontend/src/test/auth.test.ts`：auth store 状态管理、login/logout 流程

### Slice H：Backup/restore + 网络边界配置

**修改文件**：
- `scripts/backup.py`：`TRUTH_TABLES` 加入 `principals`, `principal_bindings`
- `scripts/restore.py`：恢复序列中包含新表
- `src/config/settings.py`：`GatewaySettings` 新增 `allowed_origins: str = ""` 字段
- `src/infra/preflight.py`：auth mode + `GATEWAY_HOST = 0.0.0.0` + `GATEWAY_ALLOWED_ORIGINS` 未配置 → preflight warning

**测试**：
- 验证 backup 输出包含新表数据
- `tests/test_auth_network_boundary.py`：Origin 拒绝测试、preflight warning 触发

## 4. 实现顺序

```
Slice A (DB schema)
  └─→ Slice B (settings + store + deps)
        ├─→ Slice C (JWT + login + rate limiter)
        │     └─→ Slice D (WebSocket auth + RPC authorization + Origin guard)
        │           └─→ Slice E (session ownership + identity propagation)
        │                 └─→ Slice F (Telegram binding)
        └─→ Slice G (frontend login UI) — 可与 D/E 并行
  └─→ Slice H (backup + network boundary) — 可与 B 之后任意时机并行
```

建议分 3 个 gate：
- **Gate 0**：Slice A + B — schema + store + 依赖基础
- **Gate 1**：Slice C + D + E — 后端 auth 全链路 + 原子 session ownership + identity 传播
- **Gate 2**：Slice F + G + H — 渠道集成 + 前端 + 运维 + 安全边界

## 5. 验收标准

### 功能验收

1. **WebChat 登录**：配置 `AUTH_PASSWORD_HASH` 后，WebChat 要求密码登录；登录后 session 通过 claim/stamp 路径绑定到 owner principal（首次认证动作可能是 `chat.history`，由 `authorize_and_stamp_session()` 完成 stamp；`chat.send` 由 `claim_session_for_principal()` 完成 claim）。
2. **匿名回退**：未配置 `AUTH_PASSWORD_HASH` 时，WebChat 行为与 P2-M2d 完全相同（访问未 claim 的 session）。
3. **no-auth 不泄漏已 claim/stamp session**：no-auth mode 下，匿名连接访问 `principal_id IS NOT NULL` 的 session 被拒绝（`SESSION_AUTH_REQUIRED`）。首次认证的 `chat.history` 已通过 stamp 将 NULL session 绑定为 owner。
4. **JWT lifecycle**：token 过期后 WebSocket auth 失败，前端自动跳转 LoginForm。
5. **Telegram binding**：Telegram 消息自动关联 owner principal（通过 whitelist auto-binding）。
6. **Identity 全链路传播**：`principal_id` 从 auth → dispatch → `RequestState` → `ToolContext`（`tool_runner.execute_tool()` 普通 tool 路径 + `_run_procedure_action()` procedure action 路径）。
7. **ProcedureRuntime API**：`enter_procedure(execution_metadata=ProcedureExecutionMetadata(principal_id=...))` 接收并持久化 principal_id；`apply_action()` 的 tool context 包含 principal_id。不宣称 WebSocket→enter 端到端（entry surface 未建立）。
8. **未验证 binding 不合并**：`resolve_binding()` 对 `verified = false` 返回 `status = "unverified"`，不产出 `principal_id`。
9. **Binding 冲突 fail-closed**：`ensure_binding()` 对已有不同 principal 的 binding raise `BindingConflictError`。
10. **RPC 授权一致性**：`chat.send`、`chat.history`、`session.set_mode` 三个 handler 均通过 `authorize_and_stamp_session()` 校验。
11. **Session ownership 矩阵**：D7 表格 8 个场景全部有测试覆盖。
12. **单 owner 约束**：`uq_principals_single_owner` partial unique index 阻止多 owner 创建。

### 不变性验收

13. **现有测试全绿**：不破坏 P2-M2d 及之前的所有测试。新增签名参数均有默认值。
14. **No-auth mode 零配置**：不设置 `AUTH_PASSWORD_HASH` 时，完全不触发 auth 逻辑（对未 claim session）。
15. **Scope_key 不变**：P2-M3a 不改变 scope_key 的值和生成逻辑；WebChat 仍默认 `"main"`，Telegram 仍保留 `"per-channel-peer"` 默认行为。
16. **Memory 读写不变**：P2-M3a 不改变 memory 写入、搜索、recall 行为。
17. **前端 no-auth 行为不变**：no-auth mode 下 `onopen` → `onAuthenticated` → `loadHistory()` 时序不变。

### 测试覆盖

18. 新增测试文件（预估）：8-10 个。
19. 新增测试数量（预估）：50-70 个。

## 6. 风险 & 缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| `claim_session_for_principal()` 原子 SQL 复杂度 | 中 | 用 RETURNING + post-claim Python 检查替代单 SQL 全覆盖；claim + check 在同一个 async with db_session 事务中 |
| WebSocket auth 握手增加首次连接延迟 | 低 | auth RPC 轻量（<10ms）；no-auth mode 零开销 |
| JWT secret 重启后变更导致 token 失效 | 低 | 单用户可接受；生产配置固定 `AUTH_JWT_SECRET` |
| Telegram `_handle_message` 中增加 async DB lookup | 低 | 单次 SELECT；失败 graceful fallback |
| 前端 auth/ready 状态拆分引入新的时序复杂度 | 中 | 严格 `onopen` → `onAuthenticated` 分阶段；auth store 单一状态源 |
| no-auth mode 启动时 preflight warning 可能困扰开发者 | 低 | 只在 DB 存在 non-NULL principal session 时 WARN；纯 dev 环境无此数据 |
| `sessions.principal_id` FK ON DELETE RESTRICT 阻止 principal 删除 | 低 | V1 不做 principal 删除功能；RESTRICT 是正确的安全行为 |

## 7. 不做的事

- 不引入 `per-principal` scope_key（留给 P2-M3b visibility policy）
- 不做多用户注册/管理界面
- 不做 OAuth / SSO / OIDC
- 不改变 memory 的读写语义
- 不做 `shared_in_space` 任何实现（P2-M3c）
- 不做 visibility policy hook（P2-M3b）
- 不做 session merge（不把不同 principal 的 session 历史合并）
- 不做 membership 表或 shared_space_id 规范化
- 不做 WebSocket→enter_procedure 端到端 principal 填充（entry surface 未建立）
- 不做动态 CORS middleware 改造（保持静态 CORS + WebSocket Origin guard）

## 8. Review findings 追踪

### v4→v5 findings

| # | Finding | 严重度 | 解决方案 | 位置 |
|---|---------|--------|---------|------|
| v5-1 | unverified Telegram binding 被静默合并 | P1 | `_enrich_identity_with_principal()` 显式处理 unverified 分支：同 owner 则通过 `verify_binding()` 升级，不同 owner 则 fail-closed 不返回 principal_id | Slice F |
| v5-2 | session.set_mode upsert 创建 NULL principal session | P1 | `set_mode()` 签名扩展为 `(session_id, mode, *, principal_id, auth_mode)`；upsert 写入 principal_id；entry guard 一致 | D11, Slice E |
| v5-3 | authorize_and_stamp 缺 auth_mode+None entry guard | P2 | 与 claim_session_for_principal 共享同一条规则：auth_mode=True and principal_id=None → reject | D11 |
| v5-4 | SQL interval 伪代码不可直接使用 | P3 | 加实现备注：使用 SQLAlchemy 安全构造 | D7 |

### v3→v4 findings

| # | Finding | 严重度 | 解决方案 | 位置 |
|---|---------|--------|---------|------|
| v4-1 | 首次认证后 chat.history 不会 claim session | P1 | `authorize_session_access()` 升级为 `authorize_and_stamp_session()` | D11 |
| v4-2 | auth_mode=True + principal_id=None 可创建匿名 session | P1 | `claim_session_for_principal()` 入口 fail-closed | D7 |
| v4-3 | Telegram dispatch_chat auth_mode 传递未落地 | P2 | Slice F 明确新签名和两个 call site | Slice F |
| v4-4 | 文档 U+FFFD replacement characters | P3 | 清理 11 处编码损坏字符 | 全文 |

### v2→v3 findings

| # | Finding | 严重度 | 解决方案 | 位置 |
|---|---------|--------|---------|------|
| v3-1 | no-auth 重新打开已 claim session | P1 | authorize + stamp + claim 三层均拒绝匿名访问 non-NULL principal session；preflight WARN | D7, D11 |
| v3-2 | claim-on-first-auth 需要落到 session claim 原子路径 | P1 | 新增 `claim_session_for_principal()` 替代 `try_claim_session()`；原子 SQL INSERT ON CONFLICT + post-claim validation 在同一事务 | D7, Slice E |
| v3-3 | tool_runner 签名不匹配 | P2 | 列出 `execute_tool()` / `_execute_tool()` / `_run_single_tool()` 三处签名变更 | D12 step 6-8 |
| v3-4 | ProcedureExecutionMetadata 实际创建入口不成立 | P2 | 缩回验收：只保证 runtime API + apply_action ToolContext 层；不宣称 WebSocket→enter 端到端 | D12 step 10, 验收 #7 |
| v3-5 | 动态 CORS 与 app 模块级构造冲突 | P2 | 放弃动态 CORS；保持静态 CORS + WebSocket Origin guard + login Origin check | D13 |
| v3-6 | sessions.principal_id 建议保留 FK | 建议 | 改为 FK → principals.id ON DELETE RESTRICT | Slice A |

### v1→v2 findings（保留追踪）

| # | Finding | 严重度 | v2 解决方案 | v3 状态 |
|---|---------|--------|-----------|---------|
| v2-1 | Session 绑定语义矛盾 | 必须 | claim-on-first-auth + 6 场景矩阵 | v3 升级为原子 claim + 8 场景矩阵 |
| v2-2 | WebSocket auth vs onConnected 竞态 | 必须 | pre-auth 阶段 + onAuthenticated 分离 | 保持 |
| v2-3 | scope_key "main 唯一" 与 Telegram 矛盾 | 必须 | D8 改为"不改变既有行为" | 保持 |
| v2-4 | principal_id 传播链不够具体 | 必须 | 列出各环节 | v3 补齐 tool_runner/agent 签名变更 |
| v2-5 | 密码策略不清 | 必须 | AUTH_PASSWORD_HASH hash-only + 密码轮换 | 保持 |
| v2-6 | RPC 授权覆盖不全 | 建议 | authorize_and_stamp_session() 统一 helper | v3 加入 no-auth 安全语义 |
| v2-7 | principal/binding 约束不够硬 | 建议 | owner partial unique + BindingConflictError | 保持 |
| v2-8 | Auth mode 网络边界 | 建议 | GATEWAY_ALLOWED_ORIGINS + Origin check | v3 改为静态 CORS + WS Origin guard |
| v2-9 | Rate limit 顺序错误 | 建议 | 先 lockout → verify → 记录/清零 | 保持 |
| v2-10 | 7 vs 8 Slices | 小 | 修正为 8 | 保持 |
