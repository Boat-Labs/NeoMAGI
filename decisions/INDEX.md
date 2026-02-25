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
| 0017 | Database schema default: neomagi (DB_SCHEMA) | accepted | 2026-02-17 | `decisions/0017-database-schema-default-neomagi.md` |
| 0018 | Roadmap versioning with progress tracking | accepted | 2026-02-18 | `decisions/0018-roadmap-versioning-with-progress-tracking.md` |
| 0019 | chat.history display semantics boundary | accepted | 2026-02-18 | `decisions/0019-chat-history-display-semantics-boundary.md` |
| 0020 | Database hard dependency with fail-fast startup | accepted | 2026-02-18 | `decisions/0020-database-hard-dependency-fail-fast.md` |
| 0021 | Multi-worker session ordering and no-silent-drop persistence semantics | accepted | 2026-02-18 | `decisions/0021-multi-worker-session-ordering-and-no-silent-drop.md` |
| 0022 | M1.3 soft session serialization via lock token and TTL | accepted | 2026-02-18 | `decisions/0022-m1.3-soft-session-serialization-token-ttl.md` |
| 0023 | Roadmap product-oriented boundary | accepted | 2026-02-19 | `decisions/0023-roadmap-product-oriented-boundary.md` |
| 0024 | M1.5 tool modes and priority reorder | accepted | 2026-02-19 | `decisions/0024-m1.5-tool-modes-and-priority-reorder.md` |
| 0025 | Mode switching: user-controlled with chat_safe default | accepted | 2026-02-20 | `decisions/0025-mode-switching-user-controlled-chat-safe-default.md` |
| 0026 | Session mode storage and propagation | accepted | 2026-02-20 | `decisions/0026-session-mode-storage-and-propagation.md` |
| 0027 | Partner-agent self-evolution guardrails | accepted | 2026-02-21 | `decisions/0027-partner-agent-self-evolution-guardrails.md` |
| 0028 | Compaction summary model strategy for M2 | accepted | 2026-02-21 | `decisions/0028-compaction-summary-model-strategy-for-m2.md` |
| 0029 | Token counting strategy: tiktoken first, estimate fallback | accepted | 2026-02-21 | `decisions/0029-token-counting-strategy-tiktoken-first-fallback-estimate.md` |
| 0030 | M2 anti-drift baseline scope: compaction preserves anchors | accepted | 2026-02-21 | `decisions/0030-m2-anti-drift-baseline-scope-compaction-preserves-anchors.md` |
| 0031 | Compaction history rebuild semantics: watermark | accepted | 2026-02-21 | `decisions/0031-compaction-history-rebuild-semantics-watermark.md` |
| 0032 | Memory flush ownership: AgentLoop orchestrates, CompactionEngine generates | accepted | 2026-02-21 | `decisions/0032-memory-flush-ownership-agentloop-orchestrates-compactionengine-generates.md` |
| 0033 | M2 anti-drift Probe baseline adjustment: 6 in M2, 20+ moved to M3 | accepted | 2026-02-22 | `decisions/0033-m2-anti-drift-probe-baseline-adjustment-to-6.md` |
| 0034 | OpenClaw dmScope session and memory scope alignment | accepted | 2026-02-22 | `decisions/0034-openclaw-dmscope-session-and-memory-scope-alignment.md` |
| 0035 | Runtime anti-drift guardrail hardening and risk-gated fail-closed | accepted | 2026-02-23 | `decisions/0035-runtime-anti-drift-guardrail-hardening-and-risk-gated-fail-closed.md` |
| 0036 | Evolution consistency: DB as SSOT, SOUL.md as projection | accepted | 2026-02-24 | `decisions/0036-evolution-consistency-db-as-ssot-and-soulmd-as-projection.md` |
| 0037 | Workspace path single source of truth with startup validation | accepted | 2026-02-24 | `decisions/0037-workspace-path-single-source-of-truth-and-startup-validation.md` |
| 0038 | M6 primary Gemini validation model and budget guardrail | accepted | 2026-02-24 | `decisions/0038-m6-primary-gemini-validation-model-and-budget-guardrail.md` |
| 0039 | OpenAI primary development test model: gpt-5-mini | accepted | 2026-02-24 | `decisions/0039-openai-primary-development-test-model-gpt-5-mini.md` |
| 0040 | M6 provider routing granularity: agent-run boundary | accepted | 2026-02-25 | `decisions/0040-m6-provider-routing-granularity-agent-run-boundary.md` |

## 记录规则
- 每个关键决策一个文件，命名：`NNNN-short-title.md`。
- 决策文件必须包含：`选了什么`、`为什么`、`放弃了什么`。
- 发生变更时更新状态（`proposed` / `accepted` / `superseded` / `rejected`）。
