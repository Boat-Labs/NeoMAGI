"""Soul propose tool: agent proposes SOUL.md changes with eval gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.memory.evolution import EvolutionEngine
    from src.tools.context import ToolContext


class SoulProposeTool(BaseTool):
    """Agent proposes a SOUL.md change with intent and evidence.

    Chain: propose → evaluate → (if passed) apply.
    EvolutionEngine.propose() only creates 'proposed' record;
    tool layer orchestrates the eval+apply chain.
    """

    def __init__(self, engine: EvolutionEngine | None = None) -> None:
        self._engine = engine

    @property
    def name(self) -> str:
        return "soul_propose"

    @property
    def description(self) -> str:
        return "Propose a change to SOUL.md (identity/personality definition)."

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
                "intent": {
                    "type": "string",
                    "description": "What the change aims to achieve.",
                },
                "new_content": {
                    "type": "string",
                    "description": "The proposed full SOUL.md content.",
                },
                "risk_notes": {
                    "type": "string",
                    "description": "Potential risks of this change.",
                },
                "diff_summary": {
                    "type": "string",
                    "description": "Human-readable summary of what changed.",
                },
            },
            "required": ["intent", "new_content"],
        }

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        if self._engine is None:
            return {"error_code": "NOT_CONFIGURED", "message": "Evolution engine not configured"}

        intent = arguments.get("intent", "")
        new_content = arguments.get("new_content", "")
        if not intent or not new_content:
            return {"error_code": "INVALID_ARGS", "message": "intent and new_content required"}

        from src.memory.evolution import SoulProposal

        proposal = SoulProposal(
            intent=intent,
            risk_notes=arguments.get("risk_notes", ""),
            diff_summary=arguments.get("diff_summary", ""),
            new_content=new_content,
        )

        version = await self._engine.propose(proposal)
        eval_result = await self._engine.evaluate(version)

        if eval_result.passed:
            await self._engine.apply(version)
            return {
                "status": "applied",
                "version": version,
                "eval": eval_result.summary,
            }

        return {
            "status": "rejected",
            "version": version,
            "eval": eval_result.summary,
        }
