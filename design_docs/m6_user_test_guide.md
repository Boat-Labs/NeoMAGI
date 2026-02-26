# M6 用户测试指导（模型迁移验证 + 预算闸门）

> 版本：M6 完成态（含 P1 修复）  
> 日期：2026-02-26  
> 目标：指导用户从零启动系统，并按步骤验证 M6 关键能力。

---

## 1. 适用范围

本指导覆盖以下能力：
- OpenAI（默认）与 Gemini 的可切换与可回退验证。
- `chat.send` 的 agent-run 级 provider 路由（每次请求独立选 provider）。
- BudgetGate 在线预算闸门（`BUDGET_EXCEEDED` 拒绝路径）。
- M6 评测任务 T10-T16（`scripts/m6_eval.py`）执行与报告核对。
- T11/T12 严格判定口径：未触发目标工具即 FAIL。

不在本指导范围内：
- Telegram 第二渠道（M4）。
- 运营可靠性建设（M5）。
- 精确按 token 计费结算（M6 当前为固定预占 €0.05/请求）。

---

## 2. 环境准备（一次性）

### 2.1 安装依赖

在仓库根目录执行：

```bash
uv sync --extra dev
just install-frontend
```

### 2.2 准备 `.env`

```bash
cp .env_template .env
```

至少确认以下字段：

```dotenv
DATABASE_HOST=localhost
DATABASE_PORT=5432
DATABASE_USER=neomagi
DATABASE_PASSWORD=neomagi
DATABASE_NAME=neomagi
DATABASE_SCHEMA=neomagi

OPENAI_API_KEY=<YOUR_OPENAI_API_KEY>

PROVIDER_ACTIVE=openai
GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>  # 需要测试 Gemini 时必须配置
# GEMINI_MODEL=gemini-2.5-flash
# GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
```

### 2.3 启动 PostgreSQL 16（示例：podman）

```bash
podman run --name neomagi-pg \
  -e POSTGRES_USER=neomagi \
  -e POSTGRES_PASSWORD=neomagi \
  -e POSTGRES_DB=neomagi \
  -p 5432:5432 \
  -d postgres:16
```

如果容器已存在：

```bash
podman start neomagi-pg
```

### 2.4 执行数据库 migration（M6 必须）

> M6 新增了 `budget_state` / `budget_reservations` 表，不执行 migration 会导致预算闸门路径报错。

```bash
uv run alembic upgrade head
```

### 2.5 初始化 workspace

```bash
just init-workspace
```

---

## 3. 启动系统（每次测试）

开 2 个终端窗口：

终端 A（后端 Gateway）：

```bash
just dev
```

终端 B（前端）：

```bash
just dev-frontend
```

可选健康检查：

```bash
curl http://localhost:19789/health
```

应返回：

```json
{"status":"ok"}
```

浏览器打开 `http://localhost:5173`，确认连接状态为 `Connected`。

---

## 4. 快速手工测试（用户视角）

说明：
- 示例输入不要求逐字一致。
- 预期关注“行为是否发生”，不是模型文案逐字匹配。

### T01 默认 provider（OpenAI）可用

- 前置：`.env` 中 `PROVIDER_ACTIVE=openai`，并重启 Gateway。
- 示例输入：
  - `请用一句话介绍你自己。`
- 预期：
  - 正常返回 assistant 回复，流式完成，无错误 toast。

### T02 默认 provider 可切到 Gemini（回退路径可行）

- 操作：
  1. 修改 `.env`：`PROVIDER_ACTIVE=gemini`。
  2. 确认 `GEMINI_API_KEY` 已配置。
  3. 重启 Gateway（终端 A `Ctrl+C` 后重新 `just dev`）。
- 示例输入：
  - `请用中文一句话概括你能做什么。`
- 预期：
  - 可正常回复，系统可在不改业务代码情况下切换默认 provider。

### T03 同一会话相邻两次请求可指定不同 provider（agent-run 级路由）

> 当前 WebChat UI 未暴露 `provider` 字段，建议用一次性脚本验证请求级路由。

```bash
uv run python - <<'PY'
import asyncio
import json
import uuid
import websockets

WS = "ws://localhost:19789/ws"
SESSION = "m6_user_route_demo"

async def send_once(ws, provider, content):
    rid = str(uuid.uuid4())
    req = {
        "type": "request",
        "id": rid,
        "method": "chat.send",
        "params": {
            "session_id": SESSION,
            "content": content,
            "provider": provider,
        },
    }
    await ws.send(json.dumps(req, ensure_ascii=False))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") != rid:
            continue
        if msg["type"] == "error":
            print(provider, "ERROR", msg["error"]["code"])
            return
        if msg["type"] == "stream_chunk" and msg["data"].get("done"):
            print(provider, "DONE")
            return

async def main():
    async with websockets.connect(WS) as ws:
        await send_once(ws, "openai", "请回答：2+2 等于几？")
        await send_once(ws, "gemini", "请回答：2+2 等于几？")

asyncio.run(main())
PY
```

- 预期：
  - 脚本输出 `openai DONE`、`gemini DONE`。
  - Gateway 日志可看到两条 `agent_run_provider_bound`，provider 分别为 `openai`、`gemini`。

---

## 5. 全量评测（M6 主验收）

### 5.1 Dry-run（先看任务与成本估算）

```bash
uv run python scripts/m6_eval.py --dry-run
```

### 5.2 运行 OpenAI 全量评测（T10-T16）

```bash
uv run python scripts/m6_eval.py --provider openai
```

预期：
- 总结行为 `7/7 passed`。
- 生成报告：`dev_docs/reports/m6_eval_openai_<timestamp>.json`。

### 5.3 运行 Gemini 全量评测（T10-T16）

```bash
uv run python scripts/m6_eval.py --provider gemini
```

预期（当前已知基线）：
- `6/7 passed`，`T13` 可能 FAIL（长上下文 + 工具历史场景）。
- 生成报告：`dev_docs/reports/m6_eval_gemini_<timestamp>.json`。
- 注意：脚本只要存在 FAIL/ERROR 就返回非 0，这里属于“已知限制场景”，需要结合任务明细判读。

### 5.4 只重跑工具判定关键项（T11/T12）

```bash
uv run python scripts/m6_eval.py --provider openai --tasks T11,T12
uv run python scripts/m6_eval.py --provider gemini --tasks T11,T12
```

预期：
- T11 必须触发 `current_time` 才 PASS。
- T12 必须触发 `memory_search` 才 PASS。
- 若仅文本回答、未触发目标工具，必须 FAIL（严格口径）。

---

## 6. 预算闸门验证（ADR 0041）

### 6.1 重跑前预算基线化（建议）

```bash
set -a; source .env; set +a
PGPASSWORD="${DATABASE_PASSWORD:-}" psql \
  -h "$DATABASE_HOST" \
  -p "$DATABASE_PORT" \
  -U "$DATABASE_USER" \
  -d "$DATABASE_NAME" \
  -c "UPDATE ${DATABASE_SCHEMA}.budget_state SET cumulative_eur=0, updated_at=NOW() WHERE id='global';"
```

### 6.2 触发拒绝路径（`BUDGET_EXCEEDED`）

1. 先把累计预算置到阈值边缘（`24.98`）：

```bash
set -a; source .env; set +a
PGPASSWORD="${DATABASE_PASSWORD:-}" psql \
  -h "$DATABASE_HOST" \
  -p "$DATABASE_PORT" \
  -U "$DATABASE_USER" \
  -d "$DATABASE_NAME" \
  -c "UPDATE ${DATABASE_SCHEMA}.budget_state SET cumulative_eur=24.98, updated_at=NOW() WHERE id='global';"
```

2. 发起一次 `chat.send`（任意内容，OpenAI/Gemini 均可）。
3. 预期：
  - 返回错误码 `BUDGET_EXCEEDED`。
  - 请求被拒绝，不进入模型调用主链路。

4. 校验累计值未继续增加：

```bash
set -a; source .env; set +a
PGPASSWORD="${DATABASE_PASSWORD:-}" psql \
  -h "$DATABASE_HOST" \
  -p "$DATABASE_PORT" \
  -U "$DATABASE_USER" \
  -d "$DATABASE_NAME" \
  -c "SELECT id, cumulative_eur FROM ${DATABASE_SCHEMA}.budget_state WHERE id='global';"
```

应仍为 `24.98`（或与你设置值一致）。

### 6.3 恢复预算基线（避免影响后续评测）

```bash
set -a; source .env; set +a
PGPASSWORD="${DATABASE_PASSWORD:-}" psql \
  -h "$DATABASE_HOST" \
  -p "$DATABASE_PORT" \
  -U "$DATABASE_USER" \
  -d "$DATABASE_NAME" \
  -c "UPDATE ${DATABASE_SCHEMA}.budget_state SET cumulative_eur=0, updated_at=NOW() WHERE id='global';"
```

### 6.4 预算结果参考值（全量双 provider 跑完一次）

如果从 `0` 基线开始，且仅执行一次 OpenAI + Gemini 全量 T10-T16：
- 总 `chat.send` 次数约 `52`（每 provider `26` 次）。
- 固定预占 `€0.05/次`，累计约 `€2.60`。

可用 SQL 粗核对：

```bash
set -a; source .env; set +a
PGPASSWORD="${DATABASE_PASSWORD:-}" psql \
  -h "$DATABASE_HOST" \
  -p "$DATABASE_PORT" \
  -U "$DATABASE_USER" \
  -d "$DATABASE_NAME" <<SQL
SELECT id, cumulative_eur FROM ${DATABASE_SCHEMA}.budget_state WHERE id='global';
SELECT provider, COUNT(*) AS calls, SUM(reserved_eur) AS reserved
FROM ${DATABASE_SCHEMA}.budget_reservations
GROUP BY provider
ORDER BY provider;
SQL
```

---

## 7. 预期产物检查点

完成后可检查：

- `dev_docs/reports/m6_eval_openai_<timestamp>.json`
- `dev_docs/reports/m6_eval_gemini_<timestamp>.json`
- `${DATABASE_SCHEMA}.budget_state`（全局累计）
- `${DATABASE_SCHEMA}.budget_reservations`（provider 分项、审计流水）

---

## 8. 常见问题与处理

### 8.1 执行 `eval` 没反应

- 原因：仓库没有 `eval` 这个命令入口。
- 正确命令：

```bash
uv run python scripts/m6_eval.py --provider openai
```

### 8.2 报错 `Cannot connect to Gateway`

- 确认后端在运行：`just dev`
- 确认端口：`19789`

### 8.3 报错 `PROVIDER_NOT_AVAILABLE`

- 请求里指定了未注册 provider，或 Gemini 未配置 `GEMINI_API_KEY`。
- 检查 `.env` 与 Gateway 启动日志中的 `providers` 列表。

### 8.4 报错 `relation ... budget_state does not exist`

- M6 预算表未创建。
- 执行：

```bash
uv run alembic upgrade head
```

### 8.5 Gemini 出现 T13 FAIL，是否一定是回归？

- 不一定。当前已知基线里，Gemini 在长上下文 + 工具历史场景可能触发 `400 INVALID_ARGUMENT`。
- 先确认 T10/T11/T12/T14/T15/T16 是否保持通过，再判断是否超出已知限制范围。

### 8.6 评测中频繁出现 `BUDGET_EXCEEDED`

- 说明累计预算已接近或到达 stop 阈值。
- 重跑前执行“预算基线化”（见 6.1/6.3）。

---

## 9. 退出与清理

- 停止前后端：对应终端 `Ctrl+C`
- 可选停止数据库容器：

```bash
podman stop neomagi-pg
```
