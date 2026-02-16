# 0015-orm-strategy-sqlalchemy-async-with-sql-first-search

- Status: accepted
- Date: 2026-02-16

## 选了什么
- 数据访问层默认采用 `SQLAlchemy 2.0` 的 async 模式。
- 数据库 schema 迁移采用 `Alembic`。
- 搜索相关查询（ParadeDB `pg_search`、`pgvector`、融合排序）允许优先使用 SQLAlchemy Core 或原生 SQL。

## 为什么
- 与 FastAPI + async I/O 路线一致，具备成熟生态和可维护性。
- 常规业务数据访问使用 ORM 可提升开发效率与类型边界清晰度。
- 搜索查询本身更偏 SQL 特性，保留 SQL-first 路径可避免 ORM 误用导致复杂度上升。

## 放弃了什么
- 方案 A：全部纯原生 SQL，不使用 ORM。
  - 放弃原因：常规 CRUD 开发成本更高，重复样板代码较多。
- 方案 B：所有查询强制 ORM 化（包括复杂搜索 SQL）。
  - 放弃原因：会导致不必要抽象和调试成本，属于过度工程化风险。
- 方案 C：更换为 SQLModel/Tortoise 等替代 ORM 作为主栈。
  - 放弃原因：当前阶段收益不明显，不如沿用 SQLAlchemy 生态稳定。

## 影响
- 常规表读写默认走 SQLAlchemy async ORM。
- 搜索与相关性排序查询默认以 SQL-first 实现并保持可审查 SQL 语义。
- 不引入重型 Repository/DDD 抽象层，保持薄数据访问边界。
