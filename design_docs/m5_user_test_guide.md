# M5 用户测试流程（运营可靠性）

> 版本：M5 完成态（含 post-review 修复）  
> 日期：2026-03-04  
> 目标：指导用户从零启动系统，并按步骤验证 M5 的启动自检、运行诊断、健康探针和恢复闭环。

---

## 1. 适用范围

本流程覆盖以下能力：
- 启动前统一自检（preflight）与 fail-fast 阻断。
- 健康探针分层：`/health`、`/health/live`、`/health/ready`。
- 运行期诊断入口：`just doctor` / `just doctor-deep`。
- 数据保护与恢复闭环：`just backup` / `just restore` / `just reindex` / `just reconcile`。
- M5 post-review 关键修复项验证：
  - C4 同时校验 `workspace` 与 `workspace/memory` 可写。
  - restore 在解压前清理 workspace memory 残留文件。
  - provider 运行时健康按 provider 维度记录并反映到 readiness。

不在本流程范围内：
- Telegram 第二渠道验收（M4）。
- 模型质量对比与迁移评测（M6）。
- 企业级运维平台建设（Prometheus/Grafana/Sentry/K8s）。

自动化测试参考：
- `tests/test_preflight.py`
- `tests/test_health_endpoints.py`
- `tests/test_doctor.py`
- `tests/test_backup.py`
- `tests/test_restore.py`
- `tests/test_model_client_health_tracker.py`

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

# 可选（用于多 provider readiness 验证）
# GEMINI_API_KEY=<YOUR_GEMINI_API_KEY>
```

说明：
- M5 的恢复测试会修改数据库和 `workspace/` 文件；建议在本地测试库执行，不要直接用生产数据。
- 若要验证“路径一致性守卫”，可临时用环境变量覆盖 `WORKSPACE_DIR` / `MEMORY_WORKSPACE_PATH`。

### 2.3 启动 PostgreSQL 17（示例：podman）

```bash
podman run --name neomagi-pg \
  -e POSTGRES_USER=neomagi \
  -e POSTGRES_PASSWORD=neomagi \
  -e POSTGRES_DB=neomagi \
  -p 5432:5432 \
  -d postgres:17
```

如果容器已存在：

```bash
podman start neomagi-pg
```

### 2.4 执行 migration

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

终端 B（前端 WebChat，可选）：

```bash
just dev-frontend
```

基础探针检查：

```bash
curl -s http://localhost:19789/health
curl -s http://localhost:19789/health/live
curl -s http://localhost:19789/health/ready
```

预期：
- `/health` 返回 `{"status":"ok"}`。
- `/health/live` 返回 `{"status":"alive"}`。
- `/health/ready` 在依赖健康时返回 `status=ready`，并含 `checks` 详情。

---

## 4. 快速手工测试（用户视角）

说明：
- 示例命令不要求逐字一致。
- 重点看行为是否符合预期，而不是日志文案逐字匹配。

### T01 启动后 readiness 可用

- 操作：
  1. 正常执行 `just dev`。
  2. 请求 `/health/ready`。
- 预期：
  - 返回 `status=ready`。
  - `checks` 至少包含 `db_connection`、`schema_tables`、`active_provider`、`workspace_dirs`。

### T02 preflight：`workspace/memory` 缺失会阻断启动

```bash
mv workspace/memory workspace/memory.bak
just dev
```

预期：
- 进程启动失败（不会进入长期运行）。
- 日志/异常中可看到 preflight 失败，检查项为 `workspace_dirs`。

恢复：

```bash
mv workspace/memory.bak workspace/memory
```

### T03 preflight：`workspace/memory` 只读会阻断启动（C4 修复验证）

```bash
chmod a-w workspace/memory
just dev
```

预期：
- 启动失败，失败项为 `workspace_dirs`。
- 证据包含 `memory/ subdirectory not writable` 或等价语义。

恢复权限（至少恢复当前用户写权限）：

```bash
chmod u+w workspace/memory
```

### T04 doctor 标准模式

```bash
just doctor
```

预期：
- 输出包含 `doctor PASS` 或 `doctor FAIL` 总结行。
- 检查项包含 `soul_consistency`、`memory_index_health`、`budget_status`、`session_activity`。
- 输出不应包含 API key、token、DB 密码等敏感信息。

### T05 doctor 深度模式

```bash
just doctor-deep
```

预期：
- 在标准检查基础上，增加 `provider_connectivity`、`memory_reindex_dryrun`（Telegram 启用时还会有 `telegram_deep`）。
- deep 检查失败时会返回非 0，便于 CI/脚本捕获。

---

## 5. 备份与恢复闭环验证（M5 核心）

### T06 备份产物完整性

```bash
BACKUP_DIR=./tmp/m5_backups
mkdir -p "$BACKUP_DIR"
just backup --output-dir "$BACKUP_DIR"
ls -1 "$BACKUP_DIR"
```

预期：
- 生成 3 类文件：
  - `neomagi_YYYYMMDD_HHMMSS.dump`
  - `workspace_memory_YYYYMMDD_HHMMSS.tar.gz`
  - `manifest_YYYYMMDD_HHMMSS.txt`

### T07 restore 8 步流程 + 残留文件清理验证

注意：该步骤会覆盖当前数据库真源和 workspace memory 文件，仅在可重建测试环境执行。
前置：先停止 `just dev`（避免恢复过程与在线服务并发改写同一批数据）。

```bash
BACKUP_DIR=./tmp/m5_backups
DUMP=$(ls -t "$BACKUP_DIR"/neomagi_*.dump | head -n1)
ARCHIVE=$(ls -t "$BACKUP_DIR"/workspace_memory_*.tar.gz | head -n1)

# 人为制造“恢复前残留文件”
echo "stale file for restore test" > workspace/memory/_m5_stale_should_be_removed.md

just restore --db-dump "$DUMP" --workspace-archive "$ARCHIVE"
test ! -f workspace/memory/_m5_stale_should_be_removed.md && echo "OK: stale file removed"
```

预期：
- restore 输出 `=== Restore Summary ===`，包含 8 步结果。
- 第 8 步 preflight 为 `PASS`。
- `_m5_stale_should_be_removed.md` 不存在（说明解压前清理逻辑生效）。

### T08 独立修复命令可执行

```bash
just reindex
just reconcile
just doctor
```

预期：
- `just reindex` 输出 `Reindex complete: cleared ... rebuilt ... entries`。
- `just reconcile` 输出 `Reconcile complete: SOUL.md synchronized with DB`。
- `just doctor` 最终回到可接受状态（PASS 或仅预期 WARN）。

---

## 6. Post-Review 修复项定向验证

### T09 backup 路径一致性守卫（ADR 0037）

```bash
WORKSPACE_DIR=workspace MEMORY_WORKSPACE_PATH=workspace_alt \
  just backup --output-dir ./tmp/m5_backups
```

预期：
- 命令快速失败并报错：
  - `workspace_dir (...) != memory.workspace_path (...)`
- 不会继续进入备份主流程。

### T10 provider 运行时健康按 provider 隔离（可选）

前置：
- 同时配置 OpenAI 与 Gemini。
- 需要能稳定制造某一个 provider 的连续失败（例如临时无效 key 或网络策略）。

示例步骤（以 Gemini 连续失败为例）：

1. 保持 `PROVIDER_ACTIVE=openai`，并让 Gemini 调用稳定失败（例如临时设置无效 `GEMINI_API_KEY` 后重启服务）。
2. 执行一次性脚本，连续发送 5 次 Gemini 请求：

```bash
uv run python - <<'PY'
import asyncio
import json
import uuid
import websockets

WS = "ws://localhost:19789/ws"
SESSION = "m5_provider_health_demo"

async def send_once(ws, provider, content):
    rid = str(uuid.uuid4())
    req = {
        "type": "request",
        "id": rid,
        "method": "chat.send",
        "params": {"session_id": SESSION, "content": content, "provider": provider},
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
        for i in range(5):
            await send_once(ws, "gemini", f"m5 readiness probe #{i}")
        await send_once(ws, "openai", "请回复 ok")

asyncio.run(main())
PY
```

3. 检查 readiness：

```bash
curl -s http://localhost:19789/health/ready
```

预期：
- 若 Gemini 已连续失败达到阈值，`checks` 出现 `provider_runtime_gemini`。
- OpenAI 请求成功不会清空 Gemini 的失败计数。

---

## 7. 常见问题与处理

### 7.1 `/health/ready` 返回 `not_ready`

- 直接查看返回体 `checks`，定位 `status=fail` 的检查项。
- 常见项：
  - `db_connection`：数据库连接异常。
  - `workspace_dirs`：目录缺失或不可写。
  - `provider_runtime_*`：运行期 provider 连续失败。
  - `telegram_runtime`：Telegram polling 异常退出。

### 7.2 `just doctor` 返回非 0

- 先看 summary 中 FAIL 项，再按 `next_action` 处理。
- 常见修复路径：
  - SOUL 漂移：`just reconcile`
  - memory 索引不一致：`just reindex`

### 7.3 `just backup` / `just restore` 报 `pg_dump` 或 `pg_restore` 不存在

- 安装 PostgreSQL 客户端工具后重试。
- macOS 示例：
  - `brew install libpq && brew link --force libpq`

### 7.4 restore 后 preflight 仍失败

- restore 脚本会输出失败检查项名称。
- 先按失败项修复环境，再重新执行 restore 或直接修复后重启服务验证。

---

## 8. 推荐自动化回归命令

```bash
uv run pytest -q tests/test_preflight.py
uv run pytest -q tests/test_health_endpoints.py
uv run pytest -q tests/test_doctor.py
uv run pytest -q tests/test_backup.py tests/test_restore.py
uv run pytest -q tests/test_model_client_health_tracker.py
```

---

## 9. 退出与清理

- 停止前后端：对应终端 `Ctrl+C`。
- 可选停止数据库容器：

```bash
podman stop neomagi-pg
```

- 若做过权限测试，确认 `workspace/memory` 已恢复可写。
