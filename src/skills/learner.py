"""Post-task evidence updater and skill creation proposer (P2-M1b-P4).

SkillLearner is the post-run-learning entry point:
- ``record_outcome()`` updates evidence for resolved skills after task completion.
- ``propose_new_skill()`` creates a governance proposal for a new skill draft.

V1 constraints:
- Positive evidence only written when ``user_confirmed=True``.
- Negative evidence only from deterministic signals (tool_failure, guard_denied, max_iterations).
- No auto-apply / auto-promote / auto-disable.
- ``propose_new_skill()`` always goes through governance; never directly applied.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from src.growth.types import GrowthObjectKind, GrowthProposal
from src.skills.types import SkillEvidence, SkillSpec, TaskOutcome

if TYPE_CHECKING:
    from src.growth.engine import GrowthGovernanceEngine
    from src.skills.store import SkillStore

logger = structlog.get_logger()

# Deterministic terminal states that allow writing negative evidence.
_DETERMINISTIC_FAILURE_STATES = frozenset({"tool_failure", "guard_denied", "max_iterations"})


class SkillLearner:
    """Post-task evidence updater and skill creation proposer."""

    def __init__(self, store: SkillStore, governance_engine: GrowthGovernanceEngine) -> None:
        self._store = store
        self._governance_engine = governance_engine

    async def record_outcome(
        self,
        resolved_skills: list[SkillSpec],
        outcome: TaskOutcome,
    ) -> None:
        """Update evidence for each resolved skill based on the task outcome.

        Rules (V1 conservative strategy):
        - success + user_confirmed  -> increment success_count, update last_validated_at
        - success + NOT user_confirmed -> no write (V1 conservative)
        - tool_failure / guard_denied -> increment failure_count, append failure_signals
        - max_iterations -> increment failure_count only
        - Errors are logged but never propagated (fire-and-forget).
        """
        if not resolved_skills:
            return

        now = datetime.now(UTC)
        evidence_map = await self._store.get_evidence(
            tuple(s.id for s in resolved_skills),
        )

        for skill in resolved_skills:
            existing = evidence_map.get(skill.id)
            if existing is None:
                logger.warning("learner_no_evidence", skill_id=skill.id)
                continue

            updated = self._compute_updated_evidence(existing, outcome, now)
            if updated is None:
                continue

            try:
                await self._store.update_evidence(skill.id, updated)
            except Exception:
                logger.exception(
                    "learner_update_evidence_failed",
                    skill_id=skill.id,
                )

    async def propose_new_skill(
        self,
        spec_draft: SkillSpec,
        evidence_draft: SkillEvidence,
        *,
        proposed_by: str = "agent",
    ) -> int:
        """Create a governance proposal for a new skill.

        Returns the governance_version assigned by the governance engine.
        Does NOT directly apply -- must go through evaluate + apply.
        """
        proposal = GrowthProposal(
            object_kind=GrowthObjectKind.skill_spec,
            object_id=spec_draft.id,
            intent=f"Create skill: {spec_draft.capability}",
            risk_notes="New skill proposal via SkillLearner",
            diff_summary=spec_draft.summary,
            payload={
                "skill_spec": spec_draft.model_dump(),
                "skill_evidence": evidence_draft.model_dump(),
            },
            proposed_by=proposed_by,
        )
        version = await self._governance_engine.propose(
            GrowthObjectKind.skill_spec,
            proposal,
        )
        logger.info(
            "learner_skill_proposed",
            skill_id=spec_draft.id,
            governance_version=version,
        )
        return version

    # ── internal helpers ──

    @staticmethod
    def _compute_updated_evidence(
        existing: SkillEvidence,
        outcome: TaskOutcome,
        now: datetime,
    ) -> SkillEvidence | None:
        """Compute updated evidence from outcome. Returns None if no write needed."""
        if outcome.success and outcome.user_confirmed:
            return SkillEvidence(
                source=existing.source,
                success_count=existing.success_count + 1,
                failure_count=existing.failure_count,
                last_validated_at=now,
                positive_patterns=existing.positive_patterns,
                negative_patterns=existing.negative_patterns,
                known_breakages=existing.known_breakages,
            )

        if outcome.success and not outcome.user_confirmed:
            # V1 conservative: do not write positive evidence without confirmation
            return None

        if outcome.terminal_state in _DETERMINISTIC_FAILURE_STATES:
            new_negatives = existing.negative_patterns
            if outcome.failure_signals:
                merged = list(existing.negative_patterns) + list(outcome.failure_signals)
                new_negatives = tuple(merged)
            return SkillEvidence(
                source=existing.source,
                success_count=existing.success_count,
                failure_count=existing.failure_count + 1,
                last_validated_at=now,
                positive_patterns=existing.positive_patterns,
                negative_patterns=new_negatives,
                known_breakages=existing.known_breakages,
            )

        return None
