# 0046-upgrade-database-baseline-to-postgresql-17

- Status: accepted
- Date: 2026-03-05

## 选了什么
- 将 NeoMAGI 的数据库运行基线从 PostgreSQL 16 升级为 PostgreSQL 17。
- 继续保持单库路线：`PostgreSQL + pgvector + ParadeDB pg_search`，不引入第二套搜索或持久化数据面。
- SQLite 仍不作为项目持久化数据库方案。

## 为什么
- ParadeDB 当前已支持 PostgreSQL 17，数据库主版本升级不再受扩展可用性阻塞。
- 当前项目代码仍主要运行在 PostgreSQL 原生 `tsvector + GIN` fallback 路径上，升级到 PostgreSQL 17 的应用层改动面较小，主要是基础设施与验证成本。
- 提前将基线对齐到 PostgreSQL 17，可以减少后续继续维护 PostgreSQL 16 镜像、客户端工具与运行环境的版本错配成本。
- 当前服务器端数据库以测试用途为主，没有需要保留的生产真源数据，适合在此时完成基线切换，降低迁移风险。

## 放弃了什么
- 方案 A：继续将 PostgreSQL 16 作为长期项目基线。
  - 放弃原因：会继续保留客户端/服务端版本错配与双版本维护成本，不利于后续收敛。
- 方案 B：先维持 PostgreSQL 16，等项目真正切换到 ParadeDB `pg_search` 主检索路径后再升级。
  - 放弃原因：数据库主版本升级与搜索实现切换是两类不同风险，绑定推进只会扩大变更面。
- 方案 C：同时升级到 PostgreSQL 17 并立即把检索从 `tsvector` 切到 ParadeDB `pg_search`。
  - 放弃原因：会把基础设施升级和搜索语义变更耦合在同一窗口，增加回归定位与回滚复杂度。

## 影响
- 项目文档、运行环境、CI 和运维口径应逐步从“PostgreSQL 16”更新为“PostgreSQL 17”。
- 旧的 PostgreSQL 16 测试实例可直接重建，不需要走 `pg_upgrade` 或数据迁移流程。
- NeoMAGI 当前检索实现可继续使用 `tsvector + GIN` fallback 正常运行；是否切换到 ParadeDB `pg_search` 主路径，另行决策与实施。
- 本决议 supersedes ADR 0006 中“PostgreSQL 16”为默认数据库版本的表述；“不使用 SQLite、保持单库路线”的原则继续保留。
