"""Memory search tool: BM25/tsvector search against indexed memory entries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.memory.searcher import MemorySearcher
    from src.tools.context import ToolContext


class MemorySearchTool(BaseTool):
    """Search through long-term memory using full-text search.

    scope_key is read from context.scope_key (injected by session_resolver).
    Tool does NOT derive scope on its own (ADR 0034).
    """

    def __init__(self, searcher: MemorySearcher | None = None) -> None:
        self._searcher = searcher

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return "Search through long-term memory for relevant information."

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.memory

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe, ToolMode.coding})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.low

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results (default 10).",
                },
            },
            "required": ["query"],
        }

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        if self._searcher is None:
            return {"results": [], "message": "Memory search not yet configured"}

        query = arguments.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return {
                "error_code": "INVALID_ARGS",
                "message": "query must be a non-empty string.",
            }

        limit = arguments.get("limit", 10)
        if not isinstance(limit, int) or limit < 1:
            limit = 10

        scope_key = context.scope_key if context else "main"

        results = await self._searcher.search(
            query=query.strip(),
            scope_key=scope_key,
            limit=limit,
        )

        return {
            "results": [
                {
                    "title": r.title,
                    "content": r.content[:500],  # truncate for context
                    "source": r.source_path or "curated",
                    "score": r.score,
                    "tags": r.tags,
                }
                for r in results
            ],
            "total": len(results),
        }
