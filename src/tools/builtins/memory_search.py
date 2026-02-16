from __future__ import annotations

from src.tools.base import BaseTool


class MemorySearchTool(BaseTool):
    """Placeholder for memory search. Returns empty results until implemented."""

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return "Search through long-term memory for relevant information."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, arguments: dict) -> dict:
        return {"results": [], "message": "Memory search not yet implemented"}
