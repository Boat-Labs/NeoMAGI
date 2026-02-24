"""Memory searcher: BM25/tsvector search against memory_entries.

All searches are scope-aware: results are filtered by scope_key (ADR 0034).
Current implementation: PostgreSQL native tsvector + ts_rank.
Future: swap to ParadeDB pg_search BM25 when available.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import DB_SCHEMA

if TYPE_CHECKING:
    from src.config.settings import MemorySettings

logger = structlog.get_logger()


@dataclass
class MemorySearchResult:
    """Single search result from memory_entries."""

    entry_id: int
    scope_key: str
    source_type: str
    source_path: str | None
    title: str
    content: str
    score: float
    tags: list[str]
    created_at: datetime


class MemorySearcher:
    """tsvector search against memory_entries (tsvector fallback for pg_search BM25).

    All searches are scope-aware: results are filtered by scope_key (ADR 0034).
    Scope filtering is mandatory — no bypass path exists.
    """

    def __init__(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        settings: MemorySettings,
    ) -> None:
        self._db_factory = db_session_factory
        self._settings = settings

    async def search(
        self,
        query: str,
        *,
        scope_key: str = "main",
        limit: int = 10,
        min_score: float = 0.0,
        source_types: list[str] | None = None,
    ) -> list[MemorySearchResult]:
        """Execute tsvector search with scope-aware filtering.

        Scope filtering: WHERE scope_key = :scope_key (mandatory, no bypass).

        Scoring uses ts_rank with weights:
        - title (weight A): highest priority
        - content (weight B): standard priority

        Returns results sorted by score DESC.
        """
        if not query or not query.strip():
            return []

        # Build tsquery from user input
        # Use plainto_tsquery for natural language input
        search_sql = f"""
            SELECT
                id, scope_key, source_type, source_path, title, content,
                ts_rank(search_vector, query) AS score,
                tags, created_at
            FROM {DB_SCHEMA}.memory_entries,
                 plainto_tsquery('simple', :query) AS query
            WHERE scope_key = :scope_key
              AND search_vector @@ query
        """

        params: dict = {
            "query": query.strip(),
            "scope_key": scope_key,
        }

        if source_types:
            search_sql += " AND source_type = ANY(:source_types)"
            params["source_types"] = source_types

        if min_score > 0:
            search_sql += " AND ts_rank(search_vector, query) >= :min_score"
            params["min_score"] = min_score

        search_sql += " ORDER BY score DESC LIMIT :limit"
        params["limit"] = limit

        results: list[MemorySearchResult] = []

        async with self._db_factory() as db:
            rows = await db.execute(text(search_sql), params)
            for row in rows:
                results.append(
                    MemorySearchResult(
                        entry_id=row.id,
                        scope_key=row.scope_key,
                        source_type=row.source_type,
                        source_path=row.source_path,
                        title=row.title,
                        content=row.content,
                        score=row.score,
                        tags=row.tags or [],
                        created_at=row.created_at,
                    )
                )

        logger.info(
            "memory_search",
            query=query[:50],
            scope_key=scope_key,
            results=len(results),
        )
        return results
