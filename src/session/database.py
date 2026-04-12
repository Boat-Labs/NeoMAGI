"""Async database engine and session factory for PostgreSQL persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import src.memory.models  # noqa: F401 — register memory tables in Base.metadata
from src.constants import DB_SCHEMA
from src.session.models import Base

if TYPE_CHECKING:
    from src.config.settings import DatabaseSettings

logger = structlog.get_logger()


async def create_db_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create an async SQLAlchemy engine from DatabaseSettings."""
    url = (
        f"postgresql+asyncpg://{settings.user}:{settings.password}"
        f"@{settings.host}:{settings.port}/{settings.name}"
    )
    engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        connect_args={"server_settings": {"search_path": f"{settings.schema_}, public"}},
    )
    logger.info("db_engine_created", host=settings.host, database=settings.name)
    return engine


async def ensure_schema(engine: AsyncEngine, schema: str = DB_SCHEMA) -> None:
    """Ensure the target schema exists, then create all tables and search triggers."""
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await conn.run_sync(Base.metadata.create_all)
        await _add_legacy_columns(conn, schema)
        await _add_memory_entry_columns(conn, schema)
        await _create_search_trigger(conn, schema)
        await _create_skill_tables(conn, schema)
        await _create_procedure_tables(conn, schema)
        await _create_procedure_spec_governance_tables(conn, schema)
        await _create_memory_source_ledger_table(conn, schema)
        await _create_principal_tables(conn, schema)
        await _add_principal_id_to_sessions(conn, schema)
        await _add_principal_visibility_to_memory(conn, schema)

    logger.info("db_schema_ensured", schema=schema)


async def _add_legacy_columns(conn, schema: str) -> None:
    """Add columns for backwards compatibility with older DB schemas."""
    columns = [
        "next_seq INTEGER NOT NULL DEFAULT 0",
        "lock_token VARCHAR(36)",
        "processing_since TIMESTAMPTZ",
        "mode VARCHAR(16) NOT NULL DEFAULT 'chat_safe'",
        "compacted_context TEXT",
        "compaction_metadata JSONB",
        "last_compaction_seq INTEGER",
        "memory_flush_candidates JSONB",
    ]
    for col_def in columns:
        await conn.execute(
            text(f"ALTER TABLE {schema}.sessions ADD COLUMN IF NOT EXISTS {col_def}")
        )


async def _add_memory_entry_columns(conn, schema: str) -> None:
    """Add ADR 0053 provenance columns to memory_entries (idempotent)."""
    columns = [
        "entry_id VARCHAR(36)",
        "source_session_id VARCHAR(256)",
    ]
    for col_def in columns:
        await conn.execute(
            text(f"ALTER TABLE {schema}.memory_entries ADD COLUMN IF NOT EXISTS {col_def}")
        )


async def _create_search_trigger(conn, schema: str) -> None:
    """Create or replace the search vector trigger for memory_entries."""
    await conn.execute(
        text(f"""
        CREATE OR REPLACE FUNCTION {schema}.memory_entries_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
                setweight(to_tsvector('simple', coalesce(NEW.content, '')), 'B');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    )
    await conn.execute(
        text(
            f"DROP TRIGGER IF EXISTS trg_memory_entries_search_vector"
            f" ON {schema}.memory_entries"
        )
    )
    await conn.execute(
        text(f"""
        CREATE TRIGGER trg_memory_entries_search_vector
        BEFORE INSERT OR UPDATE ON {schema}.memory_entries
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.memory_entries_search_vector_update()
    """)
    )


async def _create_skill_tables(conn, schema: str) -> None:
    """Create skill runtime tables (IF NOT EXISTS) for fresh-DB startup path.

    These tables are normally created by Alembic migration a8b9c0d1e2f3,
    but ensure_schema() must also cover fresh DBs that skip migrations.
    """
    for ddl in _skill_table_ddl(schema):
        await conn.execute(text(ddl))


def _skill_table_ddl(schema: str) -> list[str]:
    """Return idempotent DDL statements for the skill runtime tables."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.skill_specs (
            id TEXT PRIMARY KEY,
            capability TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            summary TEXT NOT NULL,
            activation TEXT NOT NULL,
            activation_tags JSONB NOT NULL DEFAULT '[]',
            preconditions JSONB NOT NULL DEFAULT '[]',
            delta JSONB NOT NULL DEFAULT '[]',
            tool_preferences JSONB NOT NULL DEFAULT '[]',
            escalation_rules JSONB NOT NULL DEFAULT '[]',
            exchange_policy TEXT NOT NULL DEFAULT 'local_only',
            disabled BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.skill_evidence (
            skill_id TEXT PRIMARY KEY REFERENCES {schema}.skill_specs(id),
            source TEXT NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_validated_at TIMESTAMPTZ,
            positive_patterns JSONB NOT NULL DEFAULT '[]',
            negative_patterns JSONB NOT NULL DEFAULT '[]',
            known_breakages JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.skill_spec_versions (
            governance_version BIGSERIAL PRIMARY KEY,
            skill_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            proposal JSONB NOT NULL,
            eval_result JSONB,
            created_by TEXT NOT NULL DEFAULT 'agent',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            applied_at TIMESTAMPTZ,
            rolled_back_from BIGINT
                REFERENCES {schema}.skill_spec_versions(governance_version)
        )""",
        f"""CREATE INDEX IF NOT EXISTS idx_skill_spec_versions_skill_id
            ON {schema}.skill_spec_versions (skill_id)""",
        f"""CREATE INDEX IF NOT EXISTS idx_skill_spec_versions_status
            ON {schema}.skill_spec_versions (status)""",
    ]


async def _create_procedure_tables(conn, schema: str) -> None:
    """Create active_procedures table (IF NOT EXISTS) for fresh-DB startup path.

    Normally created by Alembic migration c0d1e2f3a4b5.
    """
    for ddl in _procedure_table_ddl(schema):
        await conn.execute(text(ddl))


def _procedure_table_ddl(schema: str) -> list[str]:
    """Return idempotent DDL for the active_procedures table."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.active_procedures (
            instance_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            spec_id TEXT NOT NULL,
            spec_version INTEGER NOT NULL,
            state TEXT NOT NULL,
            context JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            execution_metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            revision INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ
        )""",
        f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_active_procedures_session_single_active
            ON {schema}.active_procedures (session_id)
            WHERE completed_at IS NULL""",
        f"""CREATE INDEX IF NOT EXISTS idx_active_procedures_session_id
            ON {schema}.active_procedures (session_id)""",
    ]


async def _create_procedure_spec_governance_tables(conn, schema: str) -> None:
    """Create procedure spec governance tables (IF NOT EXISTS) for fresh-DB startup path.

    Normally created by Alembic migration d1e2f3a4b5c6.
    """
    for ddl in _procedure_spec_governance_ddl(schema):
        await conn.execute(text(ddl))


def _procedure_spec_governance_ddl(schema: str) -> list[str]:
    """Return idempotent DDL for procedure spec governance tables."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.procedure_spec_definitions (
            id TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            payload JSONB NOT NULL,
            disabled BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.procedure_spec_governance (
            governance_version BIGSERIAL PRIMARY KEY,
            procedure_spec_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            proposal JSONB NOT NULL,
            eval_result JSONB,
            created_by TEXT NOT NULL DEFAULT 'agent',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            applied_at TIMESTAMPTZ,
            rolled_back_from BIGINT
                REFERENCES {schema}.procedure_spec_governance(governance_version)
        )""",
        f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_procedure_spec_governance_single_active
            ON {schema}.procedure_spec_governance (procedure_spec_id)
            WHERE status = 'active'""",
        f"""CREATE INDEX IF NOT EXISTS idx_procedure_spec_governance_spec_id
            ON {schema}.procedure_spec_governance (procedure_spec_id)""",
        f"""CREATE INDEX IF NOT EXISTS idx_procedure_spec_governance_status
            ON {schema}.procedure_spec_governance (status)""",
    ]


async def _create_memory_source_ledger_table(conn, schema: str) -> None:
    """Create memory_source_ledger table (IF NOT EXISTS) for fresh-DB startup path.

    Normally created by Alembic migration e2f3a4b5c6d7.
    Append-only DB memory truth (ADR 0060, P2-M2d).
    """
    for ddl in _memory_source_ledger_ddl(schema):
        await conn.execute(text(ddl))


def _memory_source_ledger_ddl(schema: str) -> list[str]:
    """Return idempotent DDL for the memory_source_ledger table."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.memory_source_ledger (
            event_id VARCHAR(36) PRIMARY KEY,
            entry_id VARCHAR(36) NOT NULL,
            event_type VARCHAR(16) NOT NULL DEFAULT 'append',
            scope_key VARCHAR(128) NOT NULL DEFAULT 'main',
            source VARCHAR(32) NOT NULL,
            source_session_id VARCHAR(256),
            content TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            principal_id VARCHAR(36),
            visibility VARCHAR(32) NOT NULL DEFAULT 'private_to_principal',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""CREATE INDEX IF NOT EXISTS idx_memory_source_ledger_entry_id
            ON {schema}.memory_source_ledger (entry_id)""",
        f"""CREATE INDEX IF NOT EXISTS idx_memory_source_ledger_scope
            ON {schema}.memory_source_ledger (scope_key)""",
        f"""CREATE INDEX IF NOT EXISTS idx_memory_source_ledger_created_at
            ON {schema}.memory_source_ledger (created_at)""",
        f"""DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = '{schema}'
                  AND indexname = 'uq_memory_source_ledger_entry_append'
            ) THEN
                CREATE UNIQUE INDEX uq_memory_source_ledger_entry_append
                    ON {schema}.memory_source_ledger (entry_id)
                    WHERE event_type = 'append';
            END IF;
        END $$""",
        f"""CREATE INDEX IF NOT EXISTS idx_memory_source_ledger_principal
            ON {schema}.memory_source_ledger (principal_id)""",
    ]


async def _create_principal_tables(conn, schema: str) -> None:
    """Create principal and binding tables (IF NOT EXISTS) for fresh-DB startup path.

    Normally created by Alembic migration f3a4b5c6d7e8.
    """
    for ddl in _principal_table_ddl(schema):
        await conn.execute(text(ddl))


def _principal_table_ddl(schema: str) -> list[str]:
    """Return idempotent DDL for principals and principal_bindings tables."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.principals (
            id VARCHAR(36) PRIMARY KEY,
            name VARCHAR(128) NOT NULL,
            password_hash VARCHAR(256),
            role VARCHAR(16) NOT NULL DEFAULT 'owner',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = '{schema}'
                  AND indexname = 'uq_principals_single_owner'
            ) THEN
                CREATE UNIQUE INDEX uq_principals_single_owner
                    ON {schema}.principals (role)
                    WHERE role = 'owner';
            END IF;
        END $$""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.principal_bindings (
            id VARCHAR(36) PRIMARY KEY,
            principal_id VARCHAR(36) NOT NULL
                REFERENCES {schema}.principals(id) ON DELETE RESTRICT,
            channel_type VARCHAR(32) NOT NULL,
            channel_identity VARCHAR(256) NOT NULL,
            verified BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""CREATE UNIQUE INDEX IF NOT EXISTS uq_principal_bindings_channel
            ON {schema}.principal_bindings (channel_type, channel_identity)""",
        f"""CREATE INDEX IF NOT EXISTS idx_principal_bindings_principal
            ON {schema}.principal_bindings (principal_id)""",
    ]


async def _add_principal_id_to_sessions(conn, schema: str) -> None:
    """Add principal_id column + FK to sessions table (idempotent, P2-M3a)."""
    await conn.execute(
        text(f"ALTER TABLE {schema}.sessions ADD COLUMN IF NOT EXISTS principal_id VARCHAR(36)")
    )
    await conn.execute(text(f"""DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'fk_sessions_principal_id'
              AND table_schema = '{schema}'
        ) THEN
            ALTER TABLE {schema}.sessions
                ADD CONSTRAINT fk_sessions_principal_id
                FOREIGN KEY (principal_id) REFERENCES {schema}.principals(id)
                ON DELETE RESTRICT;
        END IF;
    END $$"""))


async def _add_principal_visibility_to_memory(conn, schema: str) -> None:
    """Add principal_id + visibility columns to memory tables (idempotent, P2-M3b)."""
    # memory_source_ledger
    await conn.execute(text(
        f"ALTER TABLE {schema}.memory_source_ledger"
        f" ADD COLUMN IF NOT EXISTS principal_id VARCHAR(36)"
    ))
    await conn.execute(text(
        f"ALTER TABLE {schema}.memory_source_ledger"
        f" ADD COLUMN IF NOT EXISTS visibility VARCHAR(32) NOT NULL"
        f" DEFAULT 'private_to_principal'"
    ))
    # CHECK constraint (idempotent via IF NOT EXISTS pattern)
    await conn.execute(text(f"""DO $$ BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints
            WHERE constraint_name = 'ck_memory_source_ledger_visibility'
              AND table_schema = '{schema}'
        ) THEN
            ALTER TABLE {schema}.memory_source_ledger
                ADD CONSTRAINT ck_memory_source_ledger_visibility
                CHECK (visibility IN (
                    'private_to_principal', 'shareable_summary', 'shared_in_space'
                ));
        END IF;
    END $$"""))

    # memory_entries
    await conn.execute(text(
        f"ALTER TABLE {schema}.memory_entries"
        f" ADD COLUMN IF NOT EXISTS principal_id VARCHAR(36)"
    ))
    await conn.execute(text(
        f"ALTER TABLE {schema}.memory_entries"
        f" ADD COLUMN IF NOT EXISTS visibility VARCHAR(32) NOT NULL"
        f" DEFAULT 'private_to_principal'"
    ))
    await conn.execute(text(
        f"CREATE INDEX IF NOT EXISTS idx_memory_entries_principal"
        f" ON {schema}.memory_entries (principal_id)"
    ))
    await conn.execute(text(
        f"CREATE INDEX IF NOT EXISTS idx_memory_entries_visibility"
        f" ON {schema}.memory_entries (visibility)"
    ))


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
