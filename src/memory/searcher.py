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
from src.memory.query_processor import normalize_query
from src.memory.visibility import MEMORY_VISIBILITY_POLICY_VERSION

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
    principal_id: str | None = None  # P2-M3b
    visibility: str = "private_to_principal"  # P2-M3b


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

    @staticmethod
    def _build_search_sql(
        query: str, *, scope_key: str, limit: int,
        min_score: float, source_types: list[str] | None,
        principal_id: str | None,
    ) -> tuple[str, dict]:
        """Build tsvector search SQL with V1 visibility policy (P2-M3c, D5).

        Unified SQL for both authenticated and anonymous callers:
        - private_to_principal: own entries + legacy (principal_id IS NULL)
        - shareable_summary: same-principal only (V1)
        - shared_in_space / unknown: excluded (not in any OR branch)

        Anonymous callers (ctx_principal_id=NULL): SQL NULL comparison semantics
        ensure = NULL → UNKNOWN → no match, so only the explicit
        'principal_id IS NULL' branch passes. Do not rewrite as IS NOT DISTINCT FROM.
        """
        search_sql = f"""
            SELECT
                id, scope_key, source_type, source_path, title, content,
                ts_rank(search_vector, query) AS score,
                tags, created_at, principal_id, visibility
            FROM {DB_SCHEMA}.memory_entries,
                 plainto_tsquery('simple', :query) AS query
            WHERE scope_key = :scope_key
              AND search_vector @@ query
              AND (
                  (COALESCE(visibility, 'private_to_principal') = 'private_to_principal'
                   AND (principal_id = :ctx_principal_id OR principal_id IS NULL))
                  OR
                  (visibility = 'shareable_summary'
                   AND principal_id = :ctx_principal_id)
              )
        """
        params: dict = {
            "query": query.strip(),
            "scope_key": scope_key,
            "ctx_principal_id": principal_id,
        }
        if source_types:
            search_sql += " AND source_type = ANY(:source_types)"
            params["source_types"] = source_types
        if min_score > 0:
            search_sql += " AND ts_rank(search_vector, query) >= :min_score"
            params["min_score"] = min_score
        search_sql += " ORDER BY score DESC LIMIT :limit"
        params["limit"] = limit
        return search_sql, params

    async def _execute_search(
        self, search_sql: str, params: dict,
    ) -> list[MemorySearchResult]:
        """Execute search SQL and map rows to result objects."""
        results: list[MemorySearchResult] = []
        async with self._db_factory() as db:
            rows = await db.execute(text(search_sql), params)
            for row in rows:
                results.append(MemorySearchResult(
                    entry_id=row.id, scope_key=row.scope_key,
                    source_type=row.source_type, source_path=row.source_path,
                    title=row.title, content=row.content, score=row.score,
                    tags=row.tags or [], created_at=row.created_at,
                    principal_id=row.principal_id, visibility=row.visibility,
                ))
        return results

    async def search(
        self,
        query: str,
        *,
        scope_key: str = "main",
        limit: int = 10,
        min_score: float = 0.0,
        source_types: list[str] | None = None,
        principal_id: str | None = None,
    ) -> list[MemorySearchResult]:
        """Execute tsvector search with scope + principal + visibility filtering.

        P2-M3c: applies Jieba CJK segmentation via normalize_query before tsquery.
        """
        if not query or not query.strip():
            return []

        normalized = normalize_query(query)
        if not normalized:
            return []

        search_sql, params = self._build_search_sql(
            normalized, scope_key=scope_key, limit=limit,
            min_score=min_score, source_types=source_types,
            principal_id=principal_id,
        )
        results = await self._execute_search(search_sql, params)

        logger.info(
            "memory_search_filtered",
            query=query[:50], scope_key=scope_key,
            principal_id=principal_id,
            visibility_policy_version=MEMORY_VISIBILITY_POLICY_VERSION,
            result_count=len(results),
        )
        return results
