"""Unit tests for src.skills.projector — SkillProjector (P2-M1b-P2).

Validates:
- Per-skill delta cap (max 3 by default)
- Total delta budget (hard cap 9)
- Empty candidates → empty view
- runtime_hints from tool_preferences
- escalation_signals from escalation_rules
- Multiple candidates aggregation
"""

from __future__ import annotations

from src.skills.projector import SkillProjector
from src.skills.types import (
    ResolvedSkillView,
    SkillEvidence,
    SkillSpec,
    TaskFrame,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    id: str = "sk-1",
    delta: tuple[str, ...] = (),
    tool_preferences: tuple[str, ...] = (),
    escalation_rules: tuple[str, ...] = (),
    **kw: object,
) -> SkillSpec:
    defaults = {
        "capability": "general",
        "version": 1,
        "summary": f"Skill {id}",
        "activation": "test",
        "activation_tags": ("test",),
    }
    defaults.update(kw)
    return SkillSpec(
        id=id,
        delta=delta,
        tool_preferences=tool_preferences,
        escalation_rules=escalation_rules,
        **defaults,  # type: ignore[arg-type]
    )


def _evidence(source: str = "test") -> SkillEvidence:
    return SkillEvidence(source=source)


def _frame(**kw: object) -> TaskFrame:
    return TaskFrame(**kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_candidates_returns_empty_view(self) -> None:
        proj = SkillProjector()
        result = proj.project([], _frame())
        assert result == ResolvedSkillView()
        assert result.llm_delta == ()
        assert result.runtime_hints == ()
        assert result.escalation_signals == ()


class TestPerSkillDeltaCap:
    def test_delta_capped_at_3_by_default(self) -> None:
        spec = _spec(delta=("d1", "d2", "d3", "d4", "d5"))
        proj = SkillProjector()
        result = proj.project([(spec, None)], _frame())
        assert len(result.llm_delta) == 3
        assert result.llm_delta == ("d1", "d2", "d3")

    def test_custom_delta_cap(self) -> None:
        spec = _spec(delta=("d1", "d2", "d3", "d4"))
        proj = SkillProjector(max_delta_per_skill=2)
        result = proj.project([(spec, None)], _frame())
        assert len(result.llm_delta) == 2
        assert result.llm_delta == ("d1", "d2")

    def test_fewer_than_cap_passes_all(self) -> None:
        spec = _spec(delta=("d1",))
        proj = SkillProjector()
        result = proj.project([(spec, None)], _frame())
        assert result.llm_delta == ("d1",)


class TestTotalDeltaBudget:
    def test_total_budget_9(self) -> None:
        """3 skills x 3 delta = 9 max."""
        specs = [
            _spec(id=f"sk-{i}", delta=(f"d{i}-1", f"d{i}-2", f"d{i}-3"))
            for i in range(3)
        ]
        proj = SkillProjector()
        candidates = [(s, None) for s in specs]
        result = proj.project(candidates, _frame())
        assert len(result.llm_delta) == 9

    def test_total_budget_not_exceeded(self) -> None:
        """4 skills x 3 delta = 12, but capped at 9."""
        specs = [
            _spec(id=f"sk-{i}", delta=(f"d{i}-1", f"d{i}-2", f"d{i}-3"))
            for i in range(4)
        ]
        proj = SkillProjector()
        candidates = [(s, None) for s in specs]
        result = proj.project(candidates, _frame())
        assert len(result.llm_delta) == 9

    def test_partial_skill_included_at_boundary(self) -> None:
        """If budget runs out mid-skill, partial delta is included."""
        s1 = _spec(id="sk-1", delta=("a1", "a2", "a3"))
        s2 = _spec(id="sk-2", delta=("b1", "b2", "b3"))
        s3 = _spec(id="sk-3", delta=("c1", "c2", "c3"))
        s4 = _spec(id="sk-4", delta=("d1", "d2", "d3"))
        proj = SkillProjector()
        candidates = [(s1, None), (s2, None), (s3, None), (s4, None)]
        result = proj.project(candidates, _frame())
        # First 3 skills fill the budget (9), 4th is excluded
        assert len(result.llm_delta) == 9
        assert result.llm_delta[-1] == "c3"


class TestRuntimeHints:
    def test_hints_from_tool_preferences(self) -> None:
        spec = _spec(tool_preferences=("prefer_read_file", "avoid_shell"))
        proj = SkillProjector()
        result = proj.project([(spec, None)], _frame())
        assert result.runtime_hints == ("prefer_read_file", "avoid_shell")

    def test_hints_aggregated_from_multiple_skills(self) -> None:
        s1 = _spec(id="s1", tool_preferences=("hint1",))
        s2 = _spec(id="s2", tool_preferences=("hint2",))
        proj = SkillProjector()
        result = proj.project([(s1, None), (s2, None)], _frame())
        assert result.runtime_hints == ("hint1", "hint2")

    def test_no_hints_when_empty(self) -> None:
        spec = _spec()
        proj = SkillProjector()
        result = proj.project([(spec, None)], _frame())
        assert result.runtime_hints == ()


class TestEscalationSignals:
    def test_signals_from_escalation_rules(self) -> None:
        spec = _spec(escalation_rules=("stop on failure", "notify admin"))
        proj = SkillProjector()
        result = proj.project([(spec, None)], _frame())
        assert result.escalation_signals == ("stop on failure", "notify admin")

    def test_signals_aggregated(self) -> None:
        s1 = _spec(id="s1", escalation_rules=("rule1",))
        s2 = _spec(id="s2", escalation_rules=("rule2",))
        proj = SkillProjector()
        result = proj.project([(s1, None), (s2, None)], _frame())
        assert result.escalation_signals == ("rule1", "rule2")

    def test_no_signals_when_empty(self) -> None:
        spec = _spec()
        proj = SkillProjector()
        result = proj.project([(spec, None)], _frame())
        assert result.escalation_signals == ()


class TestWithEvidence:
    def test_evidence_does_not_affect_projection(self) -> None:
        """V1 projector does not use evidence for projection logic."""
        spec = _spec(delta=("d1",))
        ev = _evidence(source="manual")
        proj = SkillProjector()
        result = proj.project([(spec, ev)], _frame())
        assert result.llm_delta == ("d1",)
