"""Soul governed-object adapter: thin wrapper around EvolutionEngine.

Delegates all governance operations to :class:`EvolutionEngine`.
Does NOT replace EvolutionEngine, does NOT do file I/O or compensation.
``soul_versions`` remains SSOT (ADR 0036).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from src.growth.types import GrowthEvalResult, GrowthObjectKind, GrowthProposal
from src.memory.evolution import SoulProposal

if TYPE_CHECKING:
    from src.memory.evolution import EvolutionEngine, SoulVersion

logger = structlog.get_logger()


class SoulGovernedObjectAdapter:
    """Thin wrapper: delegates governance ops to EvolutionEngine.

    Does NOT replace EvolutionEngine. Does NOT do file I/O or compensation.
    soul_versions remains SSOT (ADR 0036).
    """

    def __init__(self, engine: EvolutionEngine) -> None:
        self._engine = engine

    @property
    def kind(self) -> GrowthObjectKind:
        return GrowthObjectKind.soul

    async def propose(self, proposal: GrowthProposal) -> int:
        """Convert GrowthProposal to SoulProposal and delegate to EvolutionEngine.

        Requires ``proposal.payload["new_content"]`` — the full new SOUL.md content.
        """
        new_content = proposal.payload.get("new_content")
        if not isinstance(new_content, str):
            raise ValueError(
                "SoulGovernedObjectAdapter.propose() requires "
                'proposal.payload["new_content"] to be a non-empty string'
            )

        soul_proposal = SoulProposal(
            intent=proposal.intent,
            risk_notes=proposal.risk_notes,
            diff_summary=proposal.diff_summary,
            new_content=new_content,
            evidence_refs=list(proposal.evidence_refs),
            created_by=proposal.proposed_by,
        )
        version = await self._engine.propose(soul_proposal)
        logger.info(
            "soul_adapter_proposed",
            version=version,
            intent=proposal.intent[:80],
        )
        return version

    async def evaluate(self, version: int) -> GrowthEvalResult:
        """Delegate to EvolutionEngine and convert EvalResult to GrowthEvalResult."""
        result = await self._engine.evaluate(version)
        return GrowthEvalResult(
            passed=result.passed,
            checks=[
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in result.checks
            ],
            summary=result.summary,
        )

    async def apply(self, version: int) -> None:
        """Delegate to EvolutionEngine."""
        await self._engine.apply(version)

    async def rollback(self, **kwargs: object) -> int:
        """Delegate to EvolutionEngine."""
        return await self._engine.rollback(**kwargs)

    async def veto(self, version: int) -> None:
        """Delegate to EvolutionEngine."""
        await self._engine.veto(version)

    async def get_active(self) -> SoulVersion | None:
        """Delegate to EvolutionEngine."""
        return await self._engine.get_current_version()
