"""Soul status tool: query current SOUL.md version and audit trail."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.memory.evolution import EvolutionEngine
    from src.tools.context import ToolContext


class SoulStatusTool(BaseTool):
    """Query current SOUL.md version and pending proposals."""

    def __init__(self, engine: EvolutionEngine | None = None) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "soul_status"

    @property
    def description(self) -> str:
        return "Query current SOUL.md version, status, and recent history."

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
                "include_history": {
                    "type": "boolean",
                    "description": "Include recent version history (default false).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max history entries (default 5).",
                },
            },
            "required": [],
        }

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        if self._engine is None:
            return {"error_code": "NOT_CONFIGURED", "message": "Evolution engine not configured"}

        current = await self._engine.get_current_version()
        result: dict = {
            "has_active_version": current is not None,
        }

        if current:
            result["current"] = {
                "version": current.version,
                "status": current.status,
                "created_by": current.created_by,
                "content_length": len(current.content),
            }

        if arguments.get("include_history"):
            limit = arguments.get("limit", 5)
            if not isinstance(limit, int) or limit < 1:
                limit = 5
            trail = await self._engine.get_audit_trail(limit=limit)
            result["history"] = [
                {
                    "version": v.version,
                    "status": v.status,
                    "created_by": v.created_by,
                }
                for v in trail
            ]

        return result
