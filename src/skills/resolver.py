"""Skill resolver: selects candidate skills for a given TaskFrame (P2-M1b-P2).

Deterministic, rule-based resolution. No LLM calls, no embedding, no cache.
Depends only on the ``SkillRegistry`` protocol — not on ``SkillStore`` directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.skills.types import SkillEvidence, SkillRegistry, SkillSpec, TaskFrame


class SkillResolver:
    """Resolves candidate skills for a given TaskFrame."""

    def __init__(self, registry: SkillRegistry, max_candidates: int = 3) -> None:
        self._registry = registry
        self._max_candidates = max_candidates

    async def resolve(
        self, frame: TaskFrame
    ) -> list[tuple[SkillSpec, SkillEvidence | None]]:
        """Return up to *max_candidates* (spec, evidence|None) pairs.

        Algorithm (V1):
        1. list_active() — already filters disabled
        2. Filter out skills whose preconditions are not satisfied
        3. Fetch evidence for ALL eligible skills (before scoring)
        4. Score each skill using spec + evidence signals
        5. Sort by composite score, truncate to top-K
        """
        active = await self._registry.list_active()
        if not active:
            return []

        eligible = [s for s in active if _preconditions_met(s, frame)]
        if not eligible:
            return []

        # Fetch evidence BEFORE scoring so evidence signals affect ranking
        skill_ids = tuple(s.id for s in eligible)
        evidence_map = await self._registry.get_evidence(skill_ids)

        scored = sorted(
            eligible,
            key=lambda s: _score(s, frame, evidence_map.get(s.id)),
            reverse=True,
        )

        top_k = scored[: self._max_candidates]
        return [(s, evidence_map.get(s.id)) for s in top_k]


# ---------------------------------------------------------------------------
# Scoring helpers (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _frame_keywords(frame: TaskFrame) -> set[str]:
    """Extract a lowered keyword set from the frame."""
    tokens: set[str] = set()
    if frame.task_type:
        tokens.add(frame.task_type.value.lower())
    if frame.target_outcome:
        tokens.update(frame.target_outcome.lower().split())
    return tokens


def _tag_overlap(spec: SkillSpec, frame_kw: set[str]) -> int:
    """Count how many activation_tags overlap with frame keywords."""
    return sum(1 for t in spec.activation_tags if t.lower() in frame_kw)


def _capability_overlap(spec: SkillSpec, frame_kw: set[str]) -> int:
    """Count keyword overlap between capability and frame keywords."""
    cap_tokens = set(spec.capability.lower().split())
    return len(cap_tokens & frame_kw)


def _score_escalation(spec: SkillSpec, frame: TaskFrame) -> float:
    """Priority 1: escalation bonus for high-risk tasks."""
    return 10.0 if spec.escalation_rules and frame.risk == "high" else 0.0


def _score_evidence(evidence: SkillEvidence | None) -> float:
    """Priority 2: evidence quality (fewer breakages, more recent validation)."""
    if evidence is None:
        return 0.0
    score = -len(evidence.known_breakages) * 2.0
    if evidence.last_validated_at is not None:
        age_hours = (datetime.now(UTC) - evidence.last_validated_at).total_seconds() / 3600
        if age_hours < 24:
            score += 1.0
        elif age_hours < 168:  # 1 week
            score += 0.5
    return score


def _score(
    spec: SkillSpec, frame: TaskFrame, evidence: SkillEvidence | None = None,
) -> float:
    """Composite scoring (higher = better).

    Priority order:
    1. escalation_rules present AND risk=high  → +10.0
    2. evidence quality (fewer breakages, more recent validation)
    3. tag + capability overlap with frame
    4. shorter delta preferred (inverted: -len * 0.1)
    """
    frame_kw = _frame_keywords(frame)
    overlap = float(_tag_overlap(spec, frame_kw) + _capability_overlap(spec, frame_kw))

    return (
        _score_escalation(spec, frame)
        + _score_evidence(evidence)
        + overlap
        - len(spec.delta) * 0.1
    )


# ---------------------------------------------------------------------------
# Precondition check
# ---------------------------------------------------------------------------


def _check_channel(normalised: str, frame: TaskFrame) -> bool:
    required = normalised[len("channel:"):].strip()
    return (frame.channel or "").lower() == required


def _check_mode(normalised: str, frame: TaskFrame) -> bool:
    required = normalised[len("mode:"):].strip()
    return frame.current_mode.lower() == required


def _check_tool(normalised: str, frame: TaskFrame) -> bool:
    required = normalised[len("tool:"):].strip()
    return required in {t.lower() for t in frame.available_tools}


_PRECONDITION_CHECKERS: dict[str, callable] = {
    "channel:": _check_channel,
    "mode:": _check_mode,
    "tool:": _check_tool,
}


def _single_precondition_met(normalised: str, frame: TaskFrame) -> bool:
    """Check a single normalised precondition against frame. True = pass."""
    for prefix, checker in _PRECONDITION_CHECKERS.items():
        if normalised.startswith(prefix):
            return checker(normalised, frame)
    return True  # "not:" and unrecognised → pass through


def _preconditions_met(spec: SkillSpec, frame: TaskFrame) -> bool:
    """V1 precondition check: static keyword matching.

    Supported: ``channel:<name>``, ``mode:<name>``, ``tool:<name>``,
    ``not:<tag>`` (pass-through), unrecognised (pass-through).
    """
    return all(
        _single_precondition_met(pre.strip().lower(), frame)
        for pre in spec.preconditions
    )
