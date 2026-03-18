"""Tests for GrowthGovernanceEngine (growth governance kernel).

Covers: construction injection, propose/evaluate/apply/rollback/veto/get_active
delegation, list_supported_kinds, un-onboarded kind rejection, adapter-less kind
rejection.

Uses mock adapters (AsyncMock) — no real DB required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock

import pytest

from src.growth.adapters.base import GovernedObjectAdapter, UnsupportedGrowthObjectError
from src.growth.engine import GrowthGovernanceEngine
from src.growth.policies import PolicyRegistry
from src.growth.types import (
    GrowthEvalResult,
    GrowthObjectKind,
    GrowthProposal,
)


def _make_proposal(kind: GrowthObjectKind = GrowthObjectKind.soul) -> GrowthProposal:
    return GrowthProposal(
        object_kind=kind,
        object_id="soul-1",
        intent="Update identity",
        risk_notes="None",
        diff_summary="Changed identity text",
        payload={"new_content": "# Soul\nI am Magi."},
    )


def _make_mock_adapter(kind: GrowthObjectKind = GrowthObjectKind.soul) -> AsyncMock:
    """Create a mock adapter satisfying GovernedObjectAdapter."""
    adapter = AsyncMock(spec=GovernedObjectAdapter)
    type(adapter).kind = PropertyMock(return_value=kind)
    adapter.propose = AsyncMock(return_value=1)
    adapter.evaluate = AsyncMock(
        return_value=GrowthEvalResult(passed=True, summary="All checks passed")
    )
    adapter.apply = AsyncMock(return_value=None)
    adapter.rollback = AsyncMock(return_value=2)
    adapter.veto = AsyncMock(return_value=None)
    adapter.get_active = AsyncMock(return_value=None)
    return adapter


@pytest.fixture()
def registry() -> PolicyRegistry:
    return PolicyRegistry()


@pytest.fixture()
def soul_adapter() -> AsyncMock:
    return _make_mock_adapter(GrowthObjectKind.soul)


@pytest.fixture()
def engine(soul_adapter: AsyncMock, registry: PolicyRegistry) -> GrowthGovernanceEngine:
    return GrowthGovernanceEngine(
        adapters={GrowthObjectKind.soul: soul_adapter},
        policy_registry=registry,
    )


class TestConstruction:
    def test_accepts_adapter_dict_and_registry(
        self, soul_adapter: AsyncMock, registry: PolicyRegistry
    ) -> None:
        eng = GrowthGovernanceEngine(
            adapters={GrowthObjectKind.soul: soul_adapter},
            policy_registry=registry,
        )
        assert eng is not None


class TestPropose:
    @pytest.mark.asyncio
    async def test_delegates_to_adapter(
        self, engine: GrowthGovernanceEngine, soul_adapter: AsyncMock
    ) -> None:
        proposal = _make_proposal()
        version = await engine.propose(GrowthObjectKind.soul, proposal)
        assert version == 1
        soul_adapter.propose.assert_awaited_once_with(proposal)

    @pytest.mark.asyncio
    async def test_cross_kind_mismatch_raises(self, engine: GrowthGovernanceEngine) -> None:
        proposal = _make_proposal(GrowthObjectKind.wrapper_tool)
        with pytest.raises(UnsupportedGrowthObjectError, match="does not match"):
            await engine.propose(GrowthObjectKind.soul, proposal)


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_delegates_to_adapter(
        self, engine: GrowthGovernanceEngine, soul_adapter: AsyncMock
    ) -> None:
        result = await engine.evaluate(GrowthObjectKind.soul, 1)
        assert isinstance(result, GrowthEvalResult)
        assert result.passed is True
        soul_adapter.evaluate.assert_awaited_once_with(1)


class TestApply:
    @pytest.mark.asyncio
    async def test_delegates_to_adapter(
        self, engine: GrowthGovernanceEngine, soul_adapter: AsyncMock
    ) -> None:
        await engine.apply(GrowthObjectKind.soul, 1)
        soul_adapter.apply.assert_awaited_once_with(1)


class TestRollback:
    @pytest.mark.asyncio
    async def test_delegates_to_adapter(
        self, engine: GrowthGovernanceEngine, soul_adapter: AsyncMock
    ) -> None:
        new_version = await engine.rollback(GrowthObjectKind.soul, to_version=0)
        assert new_version == 2
        soul_adapter.rollback.assert_awaited_once_with(to_version=0)


class TestVeto:
    @pytest.mark.asyncio
    async def test_delegates_to_adapter(
        self, engine: GrowthGovernanceEngine, soul_adapter: AsyncMock
    ) -> None:
        await engine.veto(GrowthObjectKind.soul, 1)
        soul_adapter.veto.assert_awaited_once_with(1)


class TestGetActive:
    @pytest.mark.asyncio
    async def test_delegates_to_adapter(
        self, engine: GrowthGovernanceEngine, soul_adapter: AsyncMock
    ) -> None:
        result = await engine.get_active(GrowthObjectKind.soul)
        assert result is None
        soul_adapter.get_active.assert_awaited_once()


class TestListSupportedKinds:
    def test_returns_registry_list(
        self, engine: GrowthGovernanceEngine, registry: PolicyRegistry
    ) -> None:
        kinds = engine.list_supported_kinds()
        assert kinds == registry.list_kinds()
        assert len(kinds) == 5


class TestUnsupportedKindReserved:
    """Reserved kinds (no adapter, not onboarded) must raise UnsupportedGrowthObjectError.

    Note: skill_spec is onboarded as of P2-M1b; it is tested under
    TestOnboardedButNoAdapter instead.
    """

    _RESERVED_KINDS = [
        GrowthObjectKind.wrapper_tool,
        GrowthObjectKind.procedure_spec,
        GrowthObjectKind.memory_application_spec,
    ]

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    @pytest.mark.asyncio
    async def test_propose_raises(
        self, engine: GrowthGovernanceEngine, kind: GrowthObjectKind
    ) -> None:
        proposal = _make_proposal(kind)
        with pytest.raises(UnsupportedGrowthObjectError, match="not onboarded"):
            await engine.propose(kind, proposal)

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    @pytest.mark.asyncio
    async def test_evaluate_raises(
        self, engine: GrowthGovernanceEngine, kind: GrowthObjectKind
    ) -> None:
        with pytest.raises(UnsupportedGrowthObjectError, match="not onboarded"):
            await engine.evaluate(kind, 1)

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    @pytest.mark.asyncio
    async def test_apply_raises(
        self, engine: GrowthGovernanceEngine, kind: GrowthObjectKind
    ) -> None:
        with pytest.raises(UnsupportedGrowthObjectError, match="not onboarded"):
            await engine.apply(kind, 1)

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    @pytest.mark.asyncio
    async def test_rollback_raises(
        self, engine: GrowthGovernanceEngine, kind: GrowthObjectKind
    ) -> None:
        with pytest.raises(UnsupportedGrowthObjectError, match="not onboarded"):
            await engine.rollback(kind)

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    @pytest.mark.asyncio
    async def test_veto_raises(
        self, engine: GrowthGovernanceEngine, kind: GrowthObjectKind
    ) -> None:
        with pytest.raises(UnsupportedGrowthObjectError, match="not onboarded"):
            await engine.veto(kind, 1)

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    @pytest.mark.asyncio
    async def test_get_active_raises(
        self, engine: GrowthGovernanceEngine, kind: GrowthObjectKind
    ) -> None:
        with pytest.raises(UnsupportedGrowthObjectError, match="not onboarded"):
            await engine.get_active(kind)


class TestOnboardedButNoAdapter:
    """Onboarded kind with missing adapter must raise UnsupportedGrowthObjectError."""

    @pytest.mark.asyncio
    async def test_soul_raises_when_adapter_missing(self, registry: PolicyRegistry) -> None:
        """Engine with empty adapter dict but soul is onboarded."""
        eng = GrowthGovernanceEngine(adapters={}, policy_registry=registry)
        with pytest.raises(UnsupportedGrowthObjectError, match="No adapter registered"):
            await eng.propose(GrowthObjectKind.soul, _make_proposal())

    @pytest.mark.asyncio
    async def test_skill_spec_raises_when_adapter_missing(
        self, engine: GrowthGovernanceEngine
    ) -> None:
        """skill_spec is onboarded (P2-M1b) but engine only has soul adapter."""
        with pytest.raises(UnsupportedGrowthObjectError, match="No adapter registered"):
            await engine.evaluate(GrowthObjectKind.skill_spec, 1)
