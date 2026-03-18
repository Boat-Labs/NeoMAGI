"""Skill projector: projects resolved skills into ResolvedSkillView (P2-M1b-P2).

Deterministic projection, no LLM calls. Enforces per-skill and total delta budgets.
"""

from __future__ import annotations

from src.skills.types import ResolvedSkillView, SkillEvidence, SkillSpec, TaskFrame

_TOTAL_DELTA_BUDGET = 9


class SkillProjector:
    """Projects resolved skills into ResolvedSkillView."""

    def __init__(self, max_delta_per_skill: int = 3) -> None:
        self._max_delta_per_skill = max_delta_per_skill

    def project(
        self,
        candidates: list[tuple[SkillSpec, SkillEvidence | None]],
        frame: TaskFrame,
    ) -> ResolvedSkillView:
        """Build a ResolvedSkillView from resolver output.

        Rules:
        - Each skill contributes at most ``max_delta_per_skill`` delta entries.
        - Total ``llm_delta`` hard cap: 9 entries.
        - ``runtime_hints`` extracted from ``tool_preferences``.
        - ``escalation_signals`` extracted from ``escalation_rules``.
        - Empty candidates → empty view.
        """
        if not candidates:
            return ResolvedSkillView()

        all_delta: list[str] = []
        all_hints: list[str] = []
        all_escalation: list[str] = []

        for spec, _evidence in candidates:
            # Delta: per-skill cap
            skill_delta = spec.delta[: self._max_delta_per_skill]
            remaining = _TOTAL_DELTA_BUDGET - len(all_delta)
            if remaining <= 0:
                break
            all_delta.extend(skill_delta[:remaining])

            # Runtime hints from tool_preferences
            all_hints.extend(spec.tool_preferences)

            # Escalation signals from escalation_rules
            all_escalation.extend(spec.escalation_rules)

        return ResolvedSkillView(
            llm_delta=tuple(all_delta),
            runtime_hints=tuple(all_hints),
            escalation_signals=tuple(all_escalation),
        )
