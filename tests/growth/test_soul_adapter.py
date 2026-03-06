"""Tests for SoulGovernedObjectAdapter (growth governance kernel).

Covers: kind property, propose/evaluate/apply/rollback/veto/get_active delegation,
payload validation, Protocol conformance.

Uses mock EvolutionEngine (AsyncMock) — no real DB required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.growth.adapters.base import GovernedObjectAdapter
from src.growth.adapters.soul import SoulGovernedObjectAdapter
from src.growth.types import (
    GrowthEvalResult,
    GrowthObjectKind,
    GrowthProposal,
)
from src.memory.evolution import EvalCheck, EvalResult, SoulVersion


def _make_growth_proposal(
    *,
    new_content: str | None = "# Soul\nI am Magi.",
    include_payload: bool = True,
) -> GrowthProposal:
    payload: dict[str, object] = {}
    if include_payload and new_content is not None:
        payload["new_content"] = new_content
    return GrowthProposal(
        object_kind=GrowthObjectKind.soul,
        object_id="soul-1",
        intent="Update identity",
        risk_notes="None",
        diff_summary="Changed identity text",
        payload=payload,
    )


@pytest.fixture()
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.propose = AsyncMock(return_value=1)
    engine.evaluate = AsyncMock(
        return_value=EvalResult(
            passed=True,
            checks=[EvalCheck(name="content_coherence", passed=True, detail="ok")],
            summary="All checks passed",
        )
    )
    engine.apply = AsyncMock(return_value=None)
    engine.rollback = AsyncMock(return_value=2)
    engine.veto = AsyncMock(return_value=None)
    engine.get_current_version = AsyncMock(
        return_value=SoulVersion(
            id=1,
            version=1,
            content="# Soul\nI am Magi.",
            status="active",
            proposal=None,
            eval_result=None,
            created_by="agent",
            created_at=None,
        )
    )
    return engine


@pytest.fixture()
def adapter(mock_engine: AsyncMock) -> SoulGovernedObjectAdapter:
    return SoulGovernedObjectAdapter(mock_engine)


class TestKind:
    def test_kind_is_soul(self, adapter: SoulGovernedObjectAdapter) -> None:
        assert adapter.kind == GrowthObjectKind.soul


class TestProtocolConformance:
    def test_isinstance_governed_object_adapter(
        self, adapter: SoulGovernedObjectAdapter
    ) -> None:
        assert isinstance(adapter, GovernedObjectAdapter)


class TestPropose:
    @pytest.mark.asyncio
    async def test_converts_to_soul_proposal(
        self, adapter: SoulGovernedObjectAdapter, mock_engine: AsyncMock
    ) -> None:
        proposal = _make_growth_proposal(new_content="# New Soul\nUpdated.")
        version = await adapter.propose(proposal)
        assert version == 1

        # Verify the SoulProposal was constructed correctly
        mock_engine.propose.assert_awaited_once()
        soul_proposal = mock_engine.propose.call_args[0][0]
        assert soul_proposal.intent == "Update identity"
        assert soul_proposal.new_content == "# New Soul\nUpdated."
        assert soul_proposal.risk_notes == "None"
        assert soul_proposal.diff_summary == "Changed identity text"

    @pytest.mark.asyncio
    async def test_missing_new_content_raises(
        self, adapter: SoulGovernedObjectAdapter
    ) -> None:
        proposal = _make_growth_proposal(include_payload=False)
        with pytest.raises(ValueError, match="new_content"):
            await adapter.propose(proposal)

    @pytest.mark.asyncio
    async def test_non_string_new_content_raises(
        self, adapter: SoulGovernedObjectAdapter
    ) -> None:
        proposal = GrowthProposal(
            object_kind=GrowthObjectKind.soul,
            object_id="soul-1",
            intent="Update",
            risk_notes="None",
            diff_summary="Changed",
            payload={"new_content": 123},
        )
        with pytest.raises(ValueError, match="new_content"):
            await adapter.propose(proposal)

    @pytest.mark.asyncio
    async def test_evidence_refs_forwarded(
        self, adapter: SoulGovernedObjectAdapter, mock_engine: AsyncMock
    ) -> None:
        proposal = GrowthProposal(
            object_kind=GrowthObjectKind.soul,
            object_id="soul-1",
            intent="Update",
            risk_notes="None",
            diff_summary="Changed",
            payload={"new_content": "# Soul"},
            evidence_refs=["conv-123", "conv-456"],
        )
        await adapter.propose(proposal)
        soul_proposal = mock_engine.propose.call_args[0][0]
        assert soul_proposal.evidence_refs == ["conv-123", "conv-456"]


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_converts_eval_result(
        self, adapter: SoulGovernedObjectAdapter, mock_engine: AsyncMock
    ) -> None:
        result = await adapter.evaluate(1)
        assert isinstance(result, GrowthEvalResult)
        assert result.passed is True
        assert result.summary == "All checks passed"
        assert len(result.checks) == 1
        assert result.checks[0]["name"] == "content_coherence"
        assert result.checks[0]["passed"] is True
        mock_engine.evaluate.assert_awaited_once_with(1)


class TestApply:
    @pytest.mark.asyncio
    async def test_delegates(
        self, adapter: SoulGovernedObjectAdapter, mock_engine: AsyncMock
    ) -> None:
        await adapter.apply(1)
        mock_engine.apply.assert_awaited_once_with(1)


class TestRollback:
    @pytest.mark.asyncio
    async def test_delegates(
        self, adapter: SoulGovernedObjectAdapter, mock_engine: AsyncMock
    ) -> None:
        new_version = await adapter.rollback(to_version=0)
        assert new_version == 2
        mock_engine.rollback.assert_awaited_once_with(to_version=0)


class TestVeto:
    @pytest.mark.asyncio
    async def test_delegates(
        self, adapter: SoulGovernedObjectAdapter, mock_engine: AsyncMock
    ) -> None:
        await adapter.veto(1)
        mock_engine.veto.assert_awaited_once_with(1)


class TestGetActive:
    @pytest.mark.asyncio
    async def test_delegates(
        self, adapter: SoulGovernedObjectAdapter, mock_engine: AsyncMock
    ) -> None:
        result = await adapter.get_active()
        assert isinstance(result, SoulVersion)
        assert result.version == 1
        mock_engine.get_current_version.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_active(
        self, mock_engine: AsyncMock
    ) -> None:
        mock_engine.get_current_version = AsyncMock(return_value=None)
        adapter = SoulGovernedObjectAdapter(mock_engine)
        result = await adapter.get_active()
        assert result is None
