"""Append-only writer for memory source ledger (ADR 0060, P2-M2d).

DB memory truth: each memory write appends an event to memory_source_ledger.
Workspace daily notes and memory_entries are projections, not truth.

V1 only supports 'append' event type. Future: correction, retraction, contested.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

from src.constants import DB_SCHEMA
from src.infra.errors import LedgerWriteError, VisibilityPolicyError
from src.memory.visibility import MemoryPolicyEntry, PolicyContext, can_write
from src.memory.writer import _uuid7

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger()


class MemoryLedgerWriter:
    """Append-only writer for memory source ledger (ADR 0060)."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._db_factory = db_session_factory

    async def append(
        self,
        *,
        entry_id: str,
        content: str,
        scope_key: str = "main",
        source: str = "user",
        source_session_id: str | None = None,
        metadata: dict | None = None,
        principal_id: str | None = None,
        visibility: str = "private_to_principal",
    ) -> bool:
        """Append a single 'append' event to the source ledger.

        Generates event_id (UUIDv7) internally.
        Uses INSERT ... ON CONFLICT DO NOTHING on the partial unique index.

        P2-M3c: runs can_write() policy check before DB insert.
        Extracts shared_space_id from metadata for rule 0 guard.

        Returns: True if inserted, False if idempotent no-op (duplicate entry_id append).
        Raises: VisibilityPolicyError if policy denies the write.
        Raises: LedgerWriteError on DB failure.
        """
        # V1: owner = requester
        meta = metadata or {}
        policy_entry = MemoryPolicyEntry(
            entry_id=entry_id,
            owner_principal_id=principal_id,
            visibility=visibility,
            scope_key=scope_key,
            shared_space_id=meta.get("shared_space_id"),
        )
        ctx = PolicyContext(principal_id=principal_id, scope_key=scope_key)
        decision = can_write(ctx, policy_entry)
        if not decision.allowed:
            logger.info(
                "visibility_policy_denied",
                entry_id=entry_id, principal_id=principal_id,
                visibility=visibility, reason=decision.reason,
            )
            raise VisibilityPolicyError(decision.reason)

        event_id = str(_uuid7())
        metadata_json = json.dumps(meta)

        sql = text(f"""
            INSERT INTO {DB_SCHEMA}.memory_source_ledger
                (event_id, entry_id, event_type, scope_key, source,
                 source_session_id, content, metadata, principal_id, visibility)
            VALUES
                (:event_id, :entry_id, 'append', :scope_key, :source,
                 :source_session_id, :content, CAST(:metadata AS jsonb),
                 :principal_id, :visibility)
            ON CONFLICT (entry_id) WHERE event_type = 'append'
            DO NOTHING
            RETURNING event_id
        """)

        try:
            async with self._db_factory() as db:
                result = await db.execute(sql, {
                    "event_id": event_id,
                    "entry_id": entry_id,
                    "scope_key": scope_key,
                    "source": source,
                    "source_session_id": source_session_id,
                    "content": content,
                    "metadata": metadata_json,
                    "principal_id": principal_id,
                    "visibility": visibility,
                })
                row = result.fetchone()
                await db.commit()

            inserted = row is not None
            if inserted:
                logger.info(
                    "ledger_append", event_id=event_id, entry_id=entry_id,
                    scope_key=scope_key, source=source,
                )
            else:
                logger.debug("ledger_append_noop", entry_id=entry_id)
            return inserted
        except Exception as exc:
            raise LedgerWriteError(
                f"Failed to append to memory source ledger: {exc}"
            ) from exc

    async def count(self, *, scope_key: str | None = None) -> int:
        """Count ledger entries, optionally filtered by scope_key."""
        if scope_key is not None:
            sql = text(
                f"SELECT count(*) FROM {DB_SCHEMA}.memory_source_ledger"
                f" WHERE scope_key = :scope_key"
            )
            params: dict = {"scope_key": scope_key}
        else:
            sql = text(f"SELECT count(*) FROM {DB_SCHEMA}.memory_source_ledger")
            params = {}

        async with self._db_factory() as db:
            result = await db.execute(sql, params)
            return result.scalar() or 0

    async def list_entry_ids(
        self,
        *,
        scope_key: str | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        """List distinct entry_ids with event_type='append' in ledger."""
        conditions = ["event_type = 'append'"]
        params: dict = {}

        if scope_key is not None:
            conditions.append("scope_key = :scope_key")
            params["scope_key"] = scope_key
        if since is not None:
            conditions.append("created_at >= :since")
            params["since"] = since

        where = " AND ".join(conditions)
        sql = text(
            f"SELECT DISTINCT entry_id FROM {DB_SCHEMA}.memory_source_ledger"
            f" WHERE {where} ORDER BY entry_id"
        )

        async with self._db_factory() as db:
            result = await db.execute(sql, params)
            return [row[0] for row in result]

    async def get_current_view(
        self, *, scope_key: str | None = None,
    ) -> list[dict]:
        """Return all append events with full fields for reindex.

        Returns list of dicts with: entry_id, content, scope_key, source,
        source_session_id, principal_id, visibility, created_at.
        """
        conditions = ["event_type = 'append'"]
        params: dict = {}

        if scope_key is not None:
            conditions.append("scope_key = :scope_key")
            params["scope_key"] = scope_key

        where = " AND ".join(conditions)
        sql = text(
            f"SELECT entry_id, content, scope_key, source, source_session_id,"
            f" principal_id, visibility, created_at"
            f" FROM {DB_SCHEMA}.memory_source_ledger"
            f" WHERE {where}"
            f" ORDER BY created_at"
        )

        async with self._db_factory() as db:
            result = await db.execute(sql, params)
            return [
                {
                    "entry_id": row.entry_id,
                    "content": row.content,
                    "scope_key": row.scope_key,
                    "source": row.source,
                    "source_session_id": row.source_session_id,
                    "principal_id": row.principal_id,
                    "visibility": row.visibility,
                    "created_at": row.created_at,
                }
                for row in result
            ]

    async def get_entries_for_parity(
        self,
        *,
        scope_key: str | None = None,
    ) -> dict[str, dict]:
        """Return {entry_id: {content, scope_key, source, source_session_id, ...}} for parity.

        Only returns 'append' events (V1 primary records).
        Includes principal_id + visibility for P2-M3b parity comparison.
        """
        conditions = ["event_type = 'append'"]
        params: dict = {}

        if scope_key is not None:
            conditions.append("scope_key = :scope_key")
            params["scope_key"] = scope_key

        where = " AND ".join(conditions)
        sql = text(
            f"SELECT entry_id, content, scope_key, source, source_session_id,"
            f" principal_id, visibility"
            f" FROM {DB_SCHEMA}.memory_source_ledger"
            f" WHERE {where}"
        )

        async with self._db_factory() as db:
            result = await db.execute(sql, params)
            return {
                row.entry_id: {
                    "content": row.content,
                    "scope_key": row.scope_key,
                    "source": row.source,
                    "source_session_id": row.source_session_id,
                    "principal_id": row.principal_id,
                    "visibility": row.visibility,
                }
                for row in result
            }
