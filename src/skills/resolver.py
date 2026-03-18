"""Skill resolver: selects candidate skills for a given TaskFrame (P2-M1b-P2).

Deterministic, rule-based resolution. No LLM calls, no embedding, no cache.
Depends only on the ``SkillRegistry`` protocol — not on ``SkillStore`` directly.
"""

from __future__ import annotations

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
        3. Score each skill by keyword overlap + contextual signals
        4. Sort by composite priority
        5. Fetch evidence for the top-K
        """
        active = await self._registry.list_active()
        if not active:
            return []

        # Step 2: filter preconditions
        eligible = [s for s in active if _preconditions_met(s, frame)]
        if not eligible:
            return []

        # Step 3+4: score and sort
        scored = sorted(
            eligible,
            key=lambda s: _score(s, frame),
            reverse=True,
        )

        # Step 5: top-K + evidence lookup
        top_k = scored[: self._max_candidates]
        skill_ids = tuple(s.id for s in top_k)
        evidence_map = await self._registry.get_evidence(skill_ids)

        return [(s, evidence_map.get(s.id)) for s in top_k]


# ---------------------------------------------------------------------------
# Scoring helpers (deterministic, no LLM)
# ---------------------------------------------------------------------------

# Words extracted from frame for matching
_FRAME_KEYWORDS_FIELDS = ("task_type", "target_outcome")


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


def _score(spec: SkillSpec, frame: TaskFrame) -> tuple[int, int, int, int]:
    """Composite scoring tuple (higher = better).

    Priority order:
    1. escalation_rules present AND risk=high  → +1
    2. fewer known_breakages is better (inverted: use negative placeholder, 0 here)
    3. tag + capability overlap with frame
    4. shorter delta preferred (inverted: -len)

    We return a tuple so Python's tuple comparison gives lexicographic priority.
    Evidence-based sorting (known_breakages, last_validated_at) is done
    post-evidence-fetch in ``_rescore_with_evidence``, but V1 keeps it simple:
    the evidence fetch happens *after* top-K selection, so the initial sort
    uses only spec-level signals. This is intentional for V1 simplicity.
    """
    frame_kw = _frame_keywords(frame)

    escalation_bonus = (
        1 if spec.escalation_rules and frame.risk == "high" else 0
    )
    overlap = _tag_overlap(spec, frame_kw) + _capability_overlap(spec, frame_kw)
    delta_penalty = -len(spec.delta)  # shorter delta → higher (less negative)

    return (escalation_bonus, 0, overlap, delta_penalty)


# ---------------------------------------------------------------------------
# Precondition check
# ---------------------------------------------------------------------------


def _preconditions_met(spec: SkillSpec, frame: TaskFrame) -> bool:
    """V1 precondition check: static keyword matching.

    Supported precondition formats:
    - ``"channel:<name>"`` — requires frame.channel == <name>
    - ``"mode:<name>"`` — requires frame.current_mode == <name>
    - ``"tool:<name>"`` — requires <name> in frame.available_tools
    - ``"not:<tag>"`` — always passes at runtime (checked at eval time)
    - unrecognised → passes (open-world assumption for V1)
    """
    for pre in spec.preconditions:
        normalised = pre.strip().lower()
        if normalised.startswith("channel:"):
            required = normalised[len("channel:") :].strip()
            if (frame.channel or "").lower() != required:
                return False
        elif normalised.startswith("mode:"):
            required = normalised[len("mode:") :].strip()
            if frame.current_mode.lower() != required:
                return False
        elif normalised.startswith("tool:"):
            required = normalised[len("tool:") :].strip()
            tool_names = {t.lower() for t in frame.available_tools}
            if required not in tool_names:
                return False
        # "not:" and unrecognised → pass through
    return True
