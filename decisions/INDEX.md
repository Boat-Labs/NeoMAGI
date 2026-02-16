# Decision Index

M0 使用轻量决策日志（ADR-lite）：关键取舍可追溯，文档保持简短。

| ID | Title | Status | Date | File |
| --- | --- | --- | --- | --- |
| 0001 | Adopt ADR-lite decision log | accepted | 2026-02-16 | `decisions/0001-adopt-adr-lite-decision-log.md` |
| 0002 | Default model route: OpenAI with Gemini migration validation | accepted | 2026-02-16 | `decisions/0002-default-openai-with-gemini-migration-validation.md` |
| 0003 | Channel baseline: WebChat first, Telegram second | accepted | 2026-02-16 | `decisions/0003-channel-baseline-webchat-first-telegram-second.md` |
| 0004 | Use uv as Python package manager | accepted | 2026-02-16 | `decisions/0004-use-uv-as-python-package-manager.md` |
| 0005 | Use just as command runner | accepted | 2026-02-16 | `decisions/0005-use-just-as-command-runner.md` |
| 0006 | Use PostgreSQL (pgvector) instead of SQLite | accepted | 2026-02-16 | `decisions/0006-use-postgresql-pgvector-instead-of-sqlite.md` |
| 0007 | Frontend baseline: React + TypeScript + Vite | accepted | 2026-02-16 | `decisions/0007-frontend-baseline-react-typescript-vite.md` |
| 0008 | Frontend UI system: Tailwind + shadcn/ui | accepted | 2026-02-16 | `decisions/0008-frontend-ui-system-tailwind-shadcn.md` |
| 0009 | Frontend state management: zustand | accepted | 2026-02-16 | `decisions/0009-frontend-state-management-zustand.md` |
| 0010 | Realtime transport: native WebSocket API | accepted | 2026-02-16 | `decisions/0010-realtime-transport-native-websocket-api.md` |
| 0011 | Frontend package manager: pnpm (with just entrypoints) | accepted | 2026-02-16 | `decisions/0011-frontend-package-manager-pnpm-with-just-entrypoints.md` |
| 0012 | Backend framework: FastAPI + Uvicorn | accepted | 2026-02-16 | `decisions/0012-backend-framework-fastapi-uvicorn.md` |
| 0013 | Backend configuration: pydantic-settings | accepted | 2026-02-16 | `decisions/0013-backend-configuration-pydantic-settings.md` |
| 0014 | ParadeDB tokenization strategy: ICU primary + Jieba fallback | accepted | 2026-02-16 | `decisions/0014-paradedb-tokenization-icu-primary-jieba-fallback.md` |
| 0015 | ORM strategy: SQLAlchemy 2.0 async with SQL-first search paths | accepted | 2026-02-16 | `decisions/0015-orm-strategy-sqlalchemy-async-with-sql-first-search.md` |
| 0016 | Model SDK strategy: OpenAI SDK unified interface for v1 | accepted | 2026-02-16 | `decisions/0016-model-sdk-strategy-openai-sdk-unified-v1.md` |

## 记录规则
- 每个关键决策一个文件，命名：`NNNN-short-title.md`。
- 决策文件必须包含：`选了什么`、`为什么`、`放弃了什么`。
- 发生变更时更新状态（`proposed` / `accepted` / `superseded` / `rejected`）。
