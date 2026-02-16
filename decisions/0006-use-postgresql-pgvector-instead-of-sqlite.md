# 0006-use-postgresql-pgvector-instead-of-sqlite

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 项目数据库统一使用 PostgreSQL（含 `pgvector` 扩展）。
- 明确不使用 SQLite 作为持久化数据库方案。

## 为什么
- PostgreSQL 更适合承载会话、记忆检索和后续扩展的统一数据面。
- `pgvector` 能直接支撑向量检索能力，避免早期数据层分裂。
- 与当前项目文档方向一致，可降低后续迁移与重构成本。

## 放弃了什么
- 方案 A：SQLite（含本地文件数据库）作为主存储。
  - 放弃原因：扩展性与协作场景受限，不符合中期演进目标。
- 方案 B：PostgreSQL + SQLite 双存储并行。
  - 放弃原因：增加系统复杂度与数据一致性负担，不符合“实现极简”。

## 影响
- 数据库连接信息以本地 `.env` 为准，不提交真实凭据到仓库。
- 变量模板维护在 `.env_template`，当前包含：
  - `DATABASE_HOST`
  - `DATABASE_PORT`
  - `DATABASE_USER`
  - `DATABASE_PASSWORD`
  - `DATABASE_NAME`
  - `DATABASE_SCHEMA`
- 后续涉及数据库的文档与实现默认按 PostgreSQL（pgvector）路线推进。
