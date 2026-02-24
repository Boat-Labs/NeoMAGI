"""Soul rollback tool: user-triggered rollback or veto of SOUL.md changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.memory.evolution import EvolutionEngine
    from src.tools.context import ToolContext


class SoulRollbackTool(BaseTool):
    """User-triggered rollback or veto of SOUL.md changes.

    Agent calls this tool when user expresses rollback/undo intent.
    This is the sole runtime entry point for rollback/veto (ADR 0027).
    """

    def __init__(self, engine: EvolutionEngine | None = None) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "soul_rollback"

    @property
    def description(self) -> str:
        return "Rollback or veto a SOUL.md change."

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.memory

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe, ToolMode.coding})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.high

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["rollback", "veto"],
                    "description": "rollback: restore previous; veto: reject a version",
                },
                "version": {
                    "type": "integer",
                    "description": "Target version (optional for rollback, required for veto).",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        if self._engine is None:
            return {"error_code": "NOT_CONFIGURED", "message": "Evolution engine not configured"}

        action = arguments.get("action")
        version = arguments.get("version")

        if action not in ("rollback", "veto"):
            return {
                "error_code": "INVALID_ARGS",
                "message": "action must be 'rollback' or 'veto'",
            }

        try:
            if action == "veto":
                if version is None:
                    return {
                        "error_code": "INVALID_ARGS",
                        "message": "version is required for veto action",
                    }
                await self._engine.veto(version)
                return {"status": "vetoed", "version": version}

            # rollback
            new_version = await self._engine.rollback(to_version=version)
            return {"status": "rolled_back", "new_active_version": new_version}

        except Exception as e:
            return {"error_code": "EVOLUTION_ERROR", "message": str(e)}
