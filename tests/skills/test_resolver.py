"""Unit tests for src.skills.resolver — SkillResolver (P2-M1b-P2).

Validates:
- Basic resolution with matching skills
- Sorting by tag/capability overlap
- Escalation priority when risk=high
- Precondition filtering (channel, mode, tool)
- Top-K truncation
- Empty registry → empty result
- Evidence fetched for top-K only
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.skills.resolver import SkillResolver
from src.skills.types import SkillEvidence, SkillSpec, TaskFrame, TaskType

# ---------------------------------------------------------------------------
# Stub registry
# ---------------------------------------------------------------------------


class StubRegistry:
    """In-memory SkillRegistry for testing."""

    def __init__(
        self,
        specs: list[SkillSpec] | None = None,
        evidence: dict[str, SkillEvidence] | None = None,
    ) -> None:
        self._specs = specs or []
        self._evidence = evidence or {}

    async def list_active(self) -> list[SkillSpec]:
        return list(self._specs)

    async def get_evidence(self, skill_ids: tuple[str, ...]) -> dict[str, SkillEvidence]:
        return {sid: self._evidence[sid] for sid in skill_ids if sid in self._evidence}


def _spec(
    id: str = "sk-1",
    capability: str = "general",
    activation_tags: tuple[str, ...] = (),
    preconditions: tuple[str, ...] = (),
    delta: tuple[str, ...] = (),
    escalation_rules: tuple[str, ...] = (),
    **kw: object,
) -> SkillSpec:
    defaults = {
        "version": 1,
        "summary": f"Skill {id}",
        "activation": "test activation",
    }
    defaults.update(kw)
    return SkillSpec(
        id=id,
        capability=capability,
        activation_tags=activation_tags,
        preconditions=preconditions,
        delta=delta,
        escalation_rules=escalation_rules,
        **defaults,  # type: ignore[arg-type]
    )


def _evidence(
    source: str = "test",
    known_breakages: tuple[str, ...] = (),
    last_validated_at: datetime | None = None,
) -> SkillEvidence:
    return SkillEvidence(
        source=source,
        known_breakages=known_breakages,
        last_validated_at=last_validated_at,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmptyRegistry:
    @pytest.mark.asyncio
    async def test_empty_registry_returns_empty(self) -> None:
        resolver = SkillResolver(StubRegistry())
        result = await resolver.resolve(TaskFrame())
        assert result == []


class TestBasicResolution:
    @pytest.mark.asyncio
    async def test_single_skill_returned(self) -> None:
        spec = _spec(id="sk-1", activation_tags=("research",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame(task_type=TaskType.research))
        assert len(result) == 1
        assert result[0][0].id == "sk-1"

    @pytest.mark.asyncio
    async def test_evidence_included_when_available(self) -> None:
        spec = _spec(id="sk-1", activation_tags=("research",))
        ev = _evidence(source="manual")
        registry = StubRegistry(specs=[spec], evidence={"sk-1": ev})
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame(task_type=TaskType.research))
        assert result[0][1] is not None
        assert result[0][1].source == "manual"

    @pytest.mark.asyncio
    async def test_evidence_none_when_missing(self) -> None:
        spec = _spec(id="sk-1", activation_tags=("research",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame(task_type=TaskType.research))
        assert result[0][1] is None


class TestSorting:
    @pytest.mark.asyncio
    async def test_higher_overlap_ranked_first(self) -> None:
        """Skill with more matching tags should rank higher."""
        low = _spec(id="low", activation_tags=("unrelated",))
        high = _spec(id="high", activation_tags=("research", "analyze"))
        registry = StubRegistry(specs=[low, high])
        resolver = SkillResolver(registry)

        frame = TaskFrame(
            task_type=TaskType.research,
            target_outcome="analyze the data",
        )
        result = await resolver.resolve(frame)
        assert result[0][0].id == "high"

    @pytest.mark.asyncio
    async def test_shorter_delta_preferred(self) -> None:
        """Among equal-overlap skills, shorter delta is preferred."""
        long_delta = _spec(
            id="long", activation_tags=("research",), delta=("a", "b", "c")
        )
        short_delta = _spec(
            id="short", activation_tags=("research",), delta=("a",)
        )
        registry = StubRegistry(specs=[long_delta, short_delta])
        resolver = SkillResolver(registry)

        frame = TaskFrame(task_type=TaskType.research)
        result = await resolver.resolve(frame)
        assert result[0][0].id == "short"

    @pytest.mark.asyncio
    async def test_escalation_priority_when_risk_high(self) -> None:
        """Skill with escalation_rules should rank first when risk=high."""
        normal = _spec(id="normal", activation_tags=("debug",))
        escalated = _spec(
            id="escalated",
            activation_tags=("debug",),
            escalation_rules=("stop on failure",),
        )
        registry = StubRegistry(specs=[normal, escalated])
        resolver = SkillResolver(registry)

        frame = TaskFrame(task_type=TaskType.debug, risk="high")
        result = await resolver.resolve(frame)
        assert result[0][0].id == "escalated"

    @pytest.mark.asyncio
    async def test_escalation_no_bonus_when_risk_low(self) -> None:
        """Escalation rules should not boost when risk is low."""
        normal = _spec(id="normal", activation_tags=("debug",), delta=())
        escalated = _spec(
            id="escalated",
            activation_tags=("debug",),
            escalation_rules=("stop on failure",),
            delta=("a", "b"),
        )
        registry = StubRegistry(specs=[normal, escalated])
        resolver = SkillResolver(registry)

        frame = TaskFrame(task_type=TaskType.debug, risk="low")
        result = await resolver.resolve(frame)
        # Both have same tag overlap; normal has shorter delta
        assert result[0][0].id == "normal"


class TestPreconditionFiltering:
    @pytest.mark.asyncio
    async def test_channel_precondition_filters(self) -> None:
        spec = _spec(id="tg-only", preconditions=("channel:telegram",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        # Matching channel → included
        result = await resolver.resolve(TaskFrame(channel="telegram"))
        assert len(result) == 1

        # Wrong channel → filtered out
        result = await resolver.resolve(TaskFrame(channel="web"))
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_mode_precondition_filters(self) -> None:
        spec = _spec(id="agent-only", preconditions=("mode:agent_mode",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame(current_mode="agent_mode"))
        assert len(result) == 1

        result = await resolver.resolve(TaskFrame(current_mode="chat_safe"))
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_tool_precondition_filters(self) -> None:
        spec = _spec(id="needs-read", preconditions=("tool:read_file",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        result = await resolver.resolve(
            TaskFrame(available_tools=("read_file", "write_file"))
        )
        assert len(result) == 1

        result = await resolver.resolve(TaskFrame(available_tools=("write_file",)))
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_not_precondition_passes(self) -> None:
        """'not:' preconditions are checked at eval time, not at resolve time."""
        spec = _spec(id="sk-not", preconditions=("not:dangerous",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame())
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_unknown_precondition_passes(self) -> None:
        """Unrecognised preconditions pass (open-world)."""
        spec = _spec(id="sk-unk", preconditions=("custom:whatever",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame())
        assert len(result) == 1


class TestTopKTruncation:
    @pytest.mark.asyncio
    async def test_default_max_3(self) -> None:
        specs = [_spec(id=f"sk-{i}") for i in range(5)]
        registry = StubRegistry(specs=specs)
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame())
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_custom_max_candidates(self) -> None:
        specs = [_spec(id=f"sk-{i}") for i in range(5)]
        registry = StubRegistry(specs=specs)
        resolver = SkillResolver(registry, max_candidates=2)

        result = await resolver.resolve(TaskFrame())
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fewer_than_max_returns_all(self) -> None:
        specs = [_spec(id="sk-only")]
        registry = StubRegistry(specs=specs)
        resolver = SkillResolver(registry, max_candidates=5)

        result = await resolver.resolve(TaskFrame())
        assert len(result) == 1


class TestAllFilteredOut:
    @pytest.mark.asyncio
    async def test_all_preconditions_fail(self) -> None:
        spec = _spec(id="tg-only", preconditions=("channel:telegram",))
        registry = StubRegistry(specs=[spec])
        resolver = SkillResolver(registry)

        result = await resolver.resolve(TaskFrame(channel="web"))
        assert result == []
