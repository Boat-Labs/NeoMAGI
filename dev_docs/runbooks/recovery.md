# NeoMAGI Recovery Runbook

## Prerequisites

- PostgreSQL client tools (`pg_dump`, `pg_restore`) installed
- `.env` configured with valid DB connection
- `uv` available for running Python scripts

---

## S1: DB 不可用

**Symptoms**: 服务启动失败，preflight 报 `db_connection` FAIL；或 DB 数据损坏

**Impact**: 服务完全不可用

**Recovery**:
```bash
just restore --db-dump ./backups/neomagi_YYYYMMDD_HHMMSS.dump \
                --workspace-archive ./backups/workspace_memory_YYYYMMDD_HHMMSS.tar.gz
```

脚本自动完成 8 步恢复：
1. 检查 pg_restore 可用性
2. pg_restore 恢复 DB 真源（--clean）
3. ensure_schema — 保证 memory_entries 表和 trigger 存在
4. 解压 workspace memory 文件
5. reconcile_soul_projection 重建 SOUL.md
6. TRUNCATE memory_entries
7. reindex_all 重建 memory_entries
8. run_preflight 验证恢复后状态

**Verification**: 脚本输出 preflight PASS；`just doctor` 确认全项 OK

---

## S2: Workspace 文件丢失

**Symptoms**: `just doctor` 报 D2 memory index 不一致；workspace/memory/ 目录缺文件

**Impact**: 记忆检索结果不完整

**Recovery**:
```bash
# 手动恢复 workspace archive
tar xzf ./backups/workspace_memory_YYYYMMDD_HHMMSS.tar.gz

# 重建 memory_entries 索引
just reindex
```

**Verification**: `just doctor` D2 显示 OK

---

## S3: SOUL.md 与 DB 不一致

**Symptoms**: `just doctor` 报 D1 SOUL consistency WARN，diff 不为空

**Impact**: 系统 prompt 注入的 identity 与 DB 真源不同步

**Recovery**:
```bash
just reconcile
```

**Verification**: `just doctor` D1 显示 OK

---

## S4: memory_entries 缺失或不一致

**Symptoms**: `just doctor` 报 D2 WARN（条目数 mismatch）；搜索结果遗漏或多余

**Impact**: 记忆搜索不完整或包含孤儿条目

**Recovery**:
```bash
just reindex
```

执行 TRUNCATE + reindex_all：先清除全部旧条目（包括已不存在文件的孤儿条目），再从 workspace 文件全量重建。

**Verification**: `just doctor` D2 显示 OK

---

## S5: 全量恢复

**Symptoms**: DB + workspace 同时不可用；灾难性故障

**Impact**: 服务完全不可用

**Recovery**:
```bash
# 完整 8 步恢复序列
just restore --db-dump ./backups/neomagi_YYYYMMDD_HHMMSS.dump \
                --workspace-archive ./backups/workspace_memory_YYYYMMDD_HHMMSS.tar.gz

# 可选：深度检查补充验证
just doctor-deep
```

**Verification**: restore 输出 preflight PASS；`just doctor --deep` 全项 OK

---

## Backup 创建

定期执行备份：
```bash
just backup
# 或指定输出目录
just backup --output-dir /path/to/backup/dir
```

备份产物：
- `neomagi_YYYYMMDD_HHMMSS.dump` — DB 真源表（5 张，不含 memory_entries）
- `workspace_memory_YYYYMMDD_HHMMSS.tar.gz` — workspace memory 文件
- `manifest_YYYYMMDD_HHMMSS.txt` — 文件清单 + SHA-256 校验和
