"""Tests for src.skills.learner (P2-M1b-P4).

Covers: record_outcome evidence update rules, propose_new_skill governance
path, error handling, and V1 conservative strategy constraints.

Uses mock SkillStore + mock GrowthGovernanceEngine -- no real DB required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from src.growth.types import GrowthObjectKind, GrowthProposal
from src.skills.learner import SkillLearner
from src.skills.types import SkillEvidence, SkillSpec, TaskOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(**overrides: object) -> SkillSpec:
    defaults = {
        "id": "sk-001",
        "capability": "code_review",
        "version": 1,
        "summary": "Reviews code changes",
        "activation": "When user asks for code review",
        "activation_tags": ("review", "code"),
    }
    defaults.update(overrides)
    return SkillSpec(**defaults)  # type: ignore[arg-type]


def _make_evidence(**overrides: object) -> SkillEvidence:
    defaults = {"source": "test", "success_count": 3, "failure_count": 1}
    defaults.update(overrides)
    return SkillEvidence(**defaults)  # type: ignore[arg-type]


def _make_outcome(**overrides: object) -> TaskOutcome:
    defaults = {
        "success": True,
        "terminal_state": "assistant_response",
        "user_confirmed": False,
    }
    defaults.update(overrides)
    return TaskOutcome(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def mock_store() -> AsyncMock:
    store = AsyncMock()
    store.get_evidence = AsyncMock(
        return_value={"sk-001": _make_evidence()},
    )
    store.update_evidence = AsyncMock()
    return store


@pytest.fixture()
def mock_engine() -> AsyncMock:
    engine = AsyncMock()
    engine.propose = AsyncMock(return_value=42)
    return engine


@pytest.fixture()
def learner(mock_store: AsyncMock, mock_engine: AsyncMock) -> SkillLearner:
    return SkillLearner(mock_store, mock_engine)


# ---------------------------------------------------------------------------
# record_outcome: success + user_confirmed
# ---------------------------------------------------------------------------


class TestRecordOutcomeSuccess:
    @pytest.mark.asyncio
    async def test_user_confirmed_increments_success(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        outcome = _make_outcome(success=True, user_confirmed=True)
        await learner.record_outcome([_make_spec()], outcome)
        mock_store.update_evidence.assert_awaited_once()
        updated = mock_store.update_evidence.call_args[0][1]
        assert updated.success_count == 4  # was 3
        assert updated.last_validated_at is not None

    @pytest.mark.asyncio
    async def test_user_confirmed_preserves_failure_count(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        outcome = _make_outcome(success=True, user_confirmed=True)
        await learner.record_outcome([_make_spec()], outcome)
        updated = mock_store.update_evidence.call_args[0][1]
        assert updated.failure_count == 1  # unchanged


# ---------------------------------------------------------------------------
# record_outcome: success + NOT user_confirmed (V1 conservative)
# ---------------------------------------------------------------------------


class TestRecordOutcomeNoConfirmation:
    @pytest.mark.asyncio
    async def test_no_confirmation_does_not_write(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        """V1 conservative: success without user_confirmed -> no write."""
        outcome = _make_outcome(success=True, user_confirmed=False)
        await learner.record_outcome([_make_spec()], outcome)
        mock_store.update_evidence.assert_not_awaited()


# ---------------------------------------------------------------------------
# record_outcome: tool_failure -> failure_count + negative_patterns
# ---------------------------------------------------------------------------


class TestRecordOutcomeToolFailure:
    @pytest.mark.asyncio
    async def test_tool_failure_increments_failure(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        outcome = _make_outcome(
            success=False,
            terminal_state="tool_failure",
            failure_signals=("timeout_on_search",),
        )
        await learner.record_outcome([_make_spec()], outcome)
        mock_store.update_evidence.assert_awaited_once()
        updated = mock_store.update_evidence.call_args[0][1]
        assert updated.failure_count == 2  # was 1
        assert "timeout_on_search" in updated.negative_patterns


# ---------------------------------------------------------------------------
# record_outcome: guard_denied -> failure_count
# ---------------------------------------------------------------------------


class TestRecordOutcomeGuardDenied:
    @pytest.mark.asyncio
    async def test_guard_denied_increments_failure(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        outcome = _make_outcome(
            success=False,
            terminal_state="guard_denied",
        )
        await learner.record_outcome([_make_spec()], outcome)
        updated = mock_store.update_evidence.call_args[0][1]
        assert updated.failure_count == 2


# ---------------------------------------------------------------------------
# record_outcome: max_iterations -> failure_count
# ---------------------------------------------------------------------------


class TestRecordOutcomeMaxIterations:
    @pytest.mark.asyncio
    async def test_max_iterations_increments_failure(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        outcome = _make_outcome(
            success=False,
            terminal_state="max_iterations",
        )
        await learner.record_outcome([_make_spec()], outcome)
        updated = mock_store.update_evidence.call_args[0][1]
        assert updated.failure_count == 2


# ---------------------------------------------------------------------------
# record_outcome: multiple resolved skills
# ---------------------------------------------------------------------------


class TestRecordOutcomeMultipleSkills:
    @pytest.mark.asyncio
    async def test_updates_each_skill(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        spec_a = _make_spec(id="sk-a")
        spec_b = _make_spec(id="sk-b")
        mock_store.get_evidence = AsyncMock(return_value={
            "sk-a": _make_evidence(),
            "sk-b": _make_evidence(success_count=0, failure_count=0),
        })
        outcome = _make_outcome(success=True, user_confirmed=True)
        await learner.record_outcome([spec_a, spec_b], outcome)
        assert mock_store.update_evidence.await_count == 2


# ---------------------------------------------------------------------------
# record_outcome: store exception does not propagate
# ---------------------------------------------------------------------------


class TestRecordOutcomeErrorHandling:
    @pytest.mark.asyncio
    async def test_store_error_does_not_propagate(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        mock_store.update_evidence = AsyncMock(side_effect=RuntimeError("DB down"))
        outcome = _make_outcome(success=True, user_confirmed=True)
        # Should not raise
        await learner.record_outcome([_make_spec()], outcome)

    @pytest.mark.asyncio
    async def test_store_error_logs_exception(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        mock_store.update_evidence = AsyncMock(side_effect=RuntimeError("DB down"))
        outcome = _make_outcome(success=True, user_confirmed=True)
        with patch("src.skills.learner.logger") as mock_logger:
            await learner.record_outcome([_make_spec()], outcome)
            mock_logger.exception.assert_called_once()
            assert "learner_update_evidence_failed" in mock_logger.exception.call_args[0]


# ---------------------------------------------------------------------------
# record_outcome: missing evidence for a skill (warning, skip)
# ---------------------------------------------------------------------------


class TestRecordOutcomeMissingEvidence:
    @pytest.mark.asyncio
    async def test_missing_evidence_logs_warning(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        mock_store.get_evidence = AsyncMock(return_value={})
        outcome = _make_outcome(success=True, user_confirmed=True)
        with patch("src.skills.learner.logger") as mock_logger:
            await learner.record_outcome([_make_spec()], outcome)
            mock_logger.warning.assert_called_once()
        mock_store.update_evidence.assert_not_awaited()


# ---------------------------------------------------------------------------
# record_outcome: empty resolved_skills -> noop
# ---------------------------------------------------------------------------


class TestRecordOutcomeEmptySkills:
    @pytest.mark.asyncio
    async def test_empty_skills_is_noop(
        self, learner: SkillLearner, mock_store: AsyncMock
    ) -> None:
        outcome = _make_outcome(success=True, user_confirmed=True)
        await learner.record_outcome([], outcome)
        mock_store.get_evidence.assert_not_awaited()


# ---------------------------------------------------------------------------
# propose_new_skill
# ---------------------------------------------------------------------------


class TestProposeNewSkill:
    @pytest.mark.asyncio
    async def test_returns_governance_version(
        self, learner: SkillLearner, mock_engine: AsyncMock
    ) -> None:
        spec = _make_spec()
        evidence = _make_evidence()
        version = await learner.propose_new_skill(spec, evidence)
        assert version == 42

    @pytest.mark.asyncio
    async def test_proposal_contains_payload(
        self, learner: SkillLearner, mock_engine: AsyncMock
    ) -> None:
        spec = _make_spec()
        evidence = _make_evidence()
        await learner.propose_new_skill(spec, evidence)
        mock_engine.propose.assert_awaited_once()
        call_args = mock_engine.propose.call_args
        kind_arg = call_args[0][0]
        proposal_arg: GrowthProposal = call_args[0][1]
        assert kind_arg == GrowthObjectKind.skill_spec
        assert "skill_spec" in proposal_arg.payload
        assert "skill_evidence" in proposal_arg.payload

    @pytest.mark.asyncio
    async def test_proposal_object_kind_is_skill_spec(
        self, learner: SkillLearner, mock_engine: AsyncMock
    ) -> None:
        spec = _make_spec()
        evidence = _make_evidence()
        await learner.propose_new_skill(spec, evidence)
        proposal_arg: GrowthProposal = mock_engine.propose.call_args[0][1]
        assert proposal_arg.object_kind == GrowthObjectKind.skill_spec
        assert proposal_arg.object_id == spec.id

    @pytest.mark.asyncio
    async def test_custom_proposed_by(
        self, learner: SkillLearner, mock_engine: AsyncMock
    ) -> None:
        spec = _make_spec()
        evidence = _make_evidence()
        await learner.propose_new_skill(spec, evidence, proposed_by="user")
        proposal_arg: GrowthProposal = mock_engine.propose.call_args[0][1]
        assert proposal_arg.proposed_by == "user"


# ---------------------------------------------------------------------------
# _compute_updated_evidence (static method, internal)
# ---------------------------------------------------------------------------


class TestComputeUpdatedEvidence:
    def test_procedure_terminal_returns_none(self) -> None:
        """Non-deterministic terminal state -> no write."""
        existing = _make_evidence()
        outcome = _make_outcome(
            success=False,
            terminal_state="procedure_terminal",
        )
        result = SkillLearner._compute_updated_evidence(
            existing, outcome, datetime.now(UTC)
        )
        assert result is None

    def test_failure_with_signals_appends(self) -> None:
        existing = _make_evidence(negative_patterns=("old_signal",))
        outcome = _make_outcome(
            success=False,
            terminal_state="tool_failure",
            failure_signals=("new_signal",),
        )
        result = SkillLearner._compute_updated_evidence(
            existing, outcome, datetime.now(UTC)
        )
        assert result is not None
        assert "old_signal" in result.negative_patterns
        assert "new_signal" in result.negative_patterns
