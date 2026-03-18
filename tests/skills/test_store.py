"""Unit tests for src.skills.store (P2-M1b-P1).

Tests SkillStore with a mock async session factory.
Validates: row mappers, list_active, get_evidence, upsert_active,
update_evidence, get_by_id, disable, governance ledger helpers.

Uses mock DB sessions — no real PostgreSQL required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.growth.types import (
    GrowthEvalResult,
    GrowthLifecycleStatus,
    GrowthObjectKind,
    GrowthProposal,
)
from src.skills.store import (
    SkillProposalRecord,
    SkillStore,
    _row_to_evidence,
    _row_to_proposal_record,
    _row_to_spec,
)
from src.skills.types import SkillEvidence, SkillSpec

# ---------------------------------------------------------------------------
# Helpers: fake DB rows as SimpleNamespace
# ---------------------------------------------------------------------------


def _fake_spec_row(**overrides: object) -> SimpleNamespace:
    defaults = {
        "id": "sk-001",
        "capability": "code_review",
        "version": 1,
        "summary": "Review code",
        "activation": "When asked to review",
        "activation_tags": ["review", "code"],
        "preconditions": [],
        "delta": ["add review notes"],
        "tool_preferences": [],
        "escalation_rules": [],
        "exchange_policy": "local_only",
        "disabled": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_evidence_row(**overrides: object) -> SimpleNamespace:
    defaults = {
        "skill_id": "sk-001",
        "source": "test",
        "success_count": 3,
        "failure_count": 0,
        "last_validated_at": datetime(2026, 1, 1, tzinfo=UTC),
        "positive_patterns": ["pattern-a"],
        "negative_patterns": [],
        "known_breakages": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _fake_version_row(**overrides: object) -> SimpleNamespace:
    defaults = {
        "governance_version": 1,
        "skill_id": "sk-001",
        "status": "proposed",
        "proposal": {"intent": "test", "payload": {}},
        "eval_result": None,
        "created_by": "agent",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "applied_at": None,
        "rolled_back_from": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_skill_spec(**overrides: object) -> SkillSpec:
    defaults = {
        "id": "sk-001",
        "capability": "code_review",
        "version": 1,
        "summary": "Review code",
        "activation": "When asked to review",
        "activation_tags": ("review", "code"),
    }
    defaults.update(overrides)
    return SkillSpec(**defaults)  # type: ignore[arg-type]


def _make_evidence(**overrides: object) -> SkillEvidence:
    defaults = {"source": "test", "success_count": 3}
    defaults.update(overrides)
    return SkillEvidence(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Row mapper tests
# ---------------------------------------------------------------------------


class TestRowToSpec:
    def test_converts_basic_fields(self) -> None:
        row = _fake_spec_row()
        spec = _row_to_spec(row)
        assert isinstance(spec, SkillSpec)
        assert spec.id == "sk-001"
        assert spec.capability == "code_review"
        assert spec.version == 1

    def test_converts_list_to_tuple(self) -> None:
        row = _fake_spec_row(activation_tags=["a", "b"], delta=["d1"])
        spec = _row_to_spec(row)
        assert spec.activation_tags == ("a", "b")
        assert spec.delta == ("d1",)

    def test_handles_none_jsonb(self) -> None:
        row = _fake_spec_row(activation_tags=None, preconditions=None, delta=None)
        spec = _row_to_spec(row)
        assert spec.activation_tags == ()
        assert spec.preconditions == ()
        assert spec.delta == ()


class TestRowToEvidence:
    def test_converts_basic_fields(self) -> None:
        row = _fake_evidence_row()
        ev = _row_to_evidence(row)
        assert isinstance(ev, SkillEvidence)
        assert ev.source == "test"
        assert ev.success_count == 3

    def test_converts_list_to_tuple(self) -> None:
        row = _fake_evidence_row(positive_patterns=["p1", "p2"])
        ev = _row_to_evidence(row)
        assert ev.positive_patterns == ("p1", "p2")

    def test_handles_none_datetime(self) -> None:
        row = _fake_evidence_row(last_validated_at=None)
        ev = _row_to_evidence(row)
        assert ev.last_validated_at is None


class TestRowToProposalRecord:
    def test_converts_all_fields(self) -> None:
        row = _fake_version_row()
        rec = _row_to_proposal_record(row)
        assert isinstance(rec, SkillProposalRecord)
        assert rec.governance_version == 1
        assert rec.skill_id == "sk-001"
        assert rec.status == "proposed"


# ---------------------------------------------------------------------------
# Mock session factory
# ---------------------------------------------------------------------------


def _make_mock_session_factory():
    """Create a mock async session factory that yields a mock session."""
    session = AsyncMock()
    # Default: execute returns empty result
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([]))
    mock_result.first.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    session.commit = AsyncMock()

    factory = MagicMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    factory.return_value = ctx

    return factory, session


# ---------------------------------------------------------------------------
# SkillStore tests
# ---------------------------------------------------------------------------


class TestListActive:
    @pytest.mark.asyncio
    async def test_returns_specs(self) -> None:
        factory, session = _make_mock_session_factory()
        rows = [_fake_spec_row(id="sk-001"), _fake_spec_row(id="sk-002")]
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter(rows))
        session.execute = AsyncMock(return_value=mock_result)

        store = SkillStore(factory)
        specs = await store.list_active()
        assert len(specs) == 2
        assert specs[0].id == "sk-001"
        assert specs[1].id == "sk-002"

    @pytest.mark.asyncio
    async def test_empty_result(self) -> None:
        factory, session = _make_mock_session_factory()
        store = SkillStore(factory)
        specs = await store.list_active()
        assert specs == []


class TestGetEvidence:
    @pytest.mark.asyncio
    async def test_returns_evidence_dict(self) -> None:
        factory, session = _make_mock_session_factory()
        rows = [_fake_evidence_row(skill_id="sk-001")]
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter(rows))
        session.execute = AsyncMock(return_value=mock_result)

        store = SkillStore(factory)
        ev_map = await store.get_evidence(("sk-001",))
        assert "sk-001" in ev_map
        assert ev_map["sk-001"].source == "test"

    @pytest.mark.asyncio
    async def test_empty_ids(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)
        result = await store.get_evidence(())
        assert result == {}


class TestGetById:
    @pytest.mark.asyncio
    async def test_found(self) -> None:
        factory, session = _make_mock_session_factory()
        row = _fake_spec_row(id="sk-001")
        mock_result = MagicMock()
        mock_result.first.return_value = row
        session.execute = AsyncMock(return_value=mock_result)

        store = SkillStore(factory)
        spec = await store.get_by_id("sk-001")
        assert spec is not None
        assert spec.id == "sk-001"

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)
        spec = await store.get_by_id("nonexistent")
        assert spec is None


class TestUpsertActive:
    @pytest.mark.asyncio
    async def test_calls_execute_and_commit(self) -> None:
        factory, session = _make_mock_session_factory()
        store = SkillStore(factory)

        spec = _make_skill_spec()
        evidence = _make_evidence()
        await store.upsert_active(spec, evidence)

        # Should have called execute twice (spec + evidence) and commit once
        assert session.execute.await_count == 2
        session.commit.assert_awaited_once()


class TestUpdateEvidence:
    @pytest.mark.asyncio
    async def test_calls_execute_and_commit(self) -> None:
        factory, session = _make_mock_session_factory()
        store = SkillStore(factory)

        evidence = _make_evidence()
        await store.update_evidence("sk-001", evidence)

        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()


class TestDisable:
    @pytest.mark.asyncio
    async def test_calls_execute_and_commit(self) -> None:
        factory, session = _make_mock_session_factory()
        store = SkillStore(factory)

        await store.disable("sk-001")

        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()


class TestCreateProposal:
    @pytest.mark.asyncio
    async def test_returns_governance_version(self) -> None:
        factory, session = _make_mock_session_factory()
        # RETURNING governance_version
        returning_row = SimpleNamespace(governance_version=42)
        mock_result = MagicMock()
        mock_result.first.return_value = returning_row
        session.execute = AsyncMock(return_value=mock_result)

        store = SkillStore(factory)
        proposal = GrowthProposal(
            object_kind=GrowthObjectKind.skill_spec,
            object_id="sk-001",
            intent="Create skill",
            risk_notes="Low",
            diff_summary="New skill",
        )
        gv = await store.create_proposal(proposal)
        assert gv == 42


class TestGetProposal:
    @pytest.mark.asyncio
    async def test_found(self) -> None:
        factory, session = _make_mock_session_factory()
        row = _fake_version_row(governance_version=5)
        mock_result = MagicMock()
        mock_result.first.return_value = row
        session.execute = AsyncMock(return_value=mock_result)

        store = SkillStore(factory)
        rec = await store.get_proposal(5)
        assert rec is not None
        assert rec.governance_version == 5

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)
        rec = await store.get_proposal(999)
        assert rec is None


class TestStoreEvalResult:
    @pytest.mark.asyncio
    async def test_calls_execute_and_commit(self) -> None:
        factory, session = _make_mock_session_factory()
        store = SkillStore(factory)

        result = GrowthEvalResult(
            passed=True,
            checks=[{"name": "test", "passed": True, "detail": "ok"}],
            summary="All passed",
            contract_id="skill_spec_v1",
            contract_version=1,
        )
        await store.store_eval_result(1, result)
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()


class TestUpdateProposalStatus:
    @pytest.mark.asyncio
    async def test_updates_status(self) -> None:
        factory, session = _make_mock_session_factory()
        store = SkillStore(factory)

        now = datetime.now(UTC)
        await store.update_proposal_status(
            1, GrowthLifecycleStatus.active, applied_at=now
        )
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()


class TestFindLastApplied:
    @pytest.mark.asyncio
    async def test_found(self) -> None:
        factory, session = _make_mock_session_factory()
        row = _fake_version_row(governance_version=3, status="active")
        mock_result = MagicMock()
        mock_result.first.return_value = row
        session.execute = AsyncMock(return_value=mock_result)

        store = SkillStore(factory)
        rec = await store.find_last_applied("sk-001")
        assert rec is not None
        assert rec.governance_version == 3

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)
        rec = await store.find_last_applied("sk-nonexistent")
        assert rec is None


# ---------------------------------------------------------------------------
# External-session support (transaction + session= kwarg)
# ---------------------------------------------------------------------------


class TestUpsertActiveWithSession:
    @pytest.mark.asyncio
    async def test_uses_provided_session(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)

        ext_session = AsyncMock()
        ext_session.execute = AsyncMock()
        spec = _make_skill_spec()
        evidence = _make_evidence()
        await store.upsert_active(spec, evidence, session=ext_session)

        # Should have called execute on the external session, not the factory session
        assert ext_session.execute.await_count == 2
        # Factory session should NOT have been used
        assert factory.call_count == 0


class TestUpdateProposalStatusWithSession:
    @pytest.mark.asyncio
    async def test_uses_provided_session(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)

        ext_session = AsyncMock()
        ext_session.execute = AsyncMock()
        now = datetime.now(UTC)
        await store.update_proposal_status(
            1, GrowthLifecycleStatus.active, applied_at=now, session=ext_session
        )
        ext_session.execute.assert_awaited_once()
        assert factory.call_count == 0


class TestDisableWithSession:
    @pytest.mark.asyncio
    async def test_uses_provided_session(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)

        ext_session = AsyncMock()
        ext_session.execute = AsyncMock()
        await store.disable("sk-001", session=ext_session)
        ext_session.execute.assert_awaited_once()
        assert factory.call_count == 0


class TestCreateProposalWithSession:
    @pytest.mark.asyncio
    async def test_uses_provided_session(self) -> None:
        factory, _ = _make_mock_session_factory()
        store = SkillStore(factory)

        ext_session = AsyncMock()
        returning_row = SimpleNamespace(governance_version=99)
        mock_result = MagicMock()
        mock_result.first.return_value = returning_row
        ext_session.execute = AsyncMock(return_value=mock_result)

        proposal = GrowthProposal(
            object_kind=GrowthObjectKind.skill_spec,
            object_id="sk-001",
            intent="Create",
            risk_notes="Low",
            diff_summary="New",
        )
        gv = await store.create_proposal(proposal, session=ext_session)
        assert gv == 99
        ext_session.execute.assert_awaited_once()
        assert factory.call_count == 0


class TestTransaction:
    @pytest.mark.asyncio
    async def test_yields_session(self) -> None:
        factory, session = _make_mock_session_factory()

        # Mock session.begin() as an async context manager
        begin_ctx = AsyncMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=None)
        begin_ctx.__aexit__ = AsyncMock(return_value=None)
        session.begin = MagicMock(return_value=begin_ctx)

        store = SkillStore(factory)
        async with store.transaction() as txn_session:
            assert txn_session is session
        session.begin.assert_called_once()
