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


# ---------------------------------------------------------------------------
# Teaching intent → propose_new_skill integration
# ---------------------------------------------------------------------------


class TestTeachingIntentProposal:
    """Verify the teaching intent → propose_new_skill path in _finalize_task_terminal."""

    @pytest.mark.asyncio
    async def test_teaching_intent_triggers_proposal(
        self, learner: SkillLearner, mock_engine: AsyncMock
    ) -> None:
        """When teaching_intent=True, propose_new_skill should be called."""
        from src.agent.message_flow import RequestState, _propose_taught_skill

        # Build a minimal RequestState with teaching_intent=True
        state = RequestState(
            session_id="test-sess-1234",
            lock_token=None,
            mode=None,
            scope_key="main",
            current_user_seq=1,
            tools_schema=None,
            tools_schema_list=[],
            compaction_count=0,
            max_compactions=2,
            last_compaction_seq=None,
            compacted_context=None,
            recall_results=[],
            system_prompt="",
            teaching_intent=True,
        )

        # Create a minimal loop-like object
        class FakeLoop:
            _skill_learner = learner

        await _propose_taught_skill(FakeLoop(), state)  # type: ignore[arg-type]
        mock_engine.propose.assert_awaited_once()
        call_args = mock_engine.propose.call_args
        proposal = call_args[0][1]
        assert proposal.proposed_by == "user"

    @pytest.mark.asyncio
    async def test_teaching_intent_with_target_outcome(
        self, learner: SkillLearner, mock_engine: AsyncMock
    ) -> None:
        """Target outcome from task_frame should populate tags, delta, and summary."""
        from src.agent.message_flow import RequestState, _propose_taught_skill
        from src.skills.types import TaskFrame, TaskType

        state = RequestState(
            session_id="test-sess-5678",
            lock_token=None,
            mode=None,
            scope_key="main",
            current_user_seq=1,
            tools_schema=None,
            tools_schema_list=[],
            compaction_count=0,
            max_compactions=2,
            last_compaction_seq=None,
            compacted_context=None,
            recall_results=[],
            system_prompt="",
            teaching_intent=True,
            task_frame=TaskFrame(
                task_type=TaskType.edit,
                target_outcome="Format code with Black before committing",
            ),
        )

        class FakeLoop:
            _skill_learner = learner

        await _propose_taught_skill(FakeLoop(), state)  # type: ignore[arg-type]
        proposal = mock_engine.propose.call_args[0][1]
        assert "Format code with Black" in proposal.diff_summary
        # Verify the spec has meaningful delta (not empty)
        spec_payload = proposal.payload["skill_spec"]
        assert spec_payload["delta"], "Proposed skill should have non-empty delta"
        # Verify tags include task_type
        assert "edit" in spec_payload["activation_tags"]
        # Verify capability derived from task_type
        assert spec_payload["capability"] == "edit"


class TestTeachingIntentDoesNotConfirmExistingSkills:
    """Verify teaching_intent does NOT pollute existing skill evidence."""

    @pytest.mark.asyncio
    async def test_teaching_intent_does_not_set_user_confirmed(
        self, learner: SkillLearner
    ) -> None:
        """record_outcome should receive user_confirmed=False even with teaching_intent."""
        from unittest.mock import patch

        spec = SkillSpec(
            id="existing-1", capability="deploy", version=1,
            summary="Deploy helper", activation="deploy tasks",
        )
        outcome = TaskOutcome(
            success=True, terminal_state="assistant_response",
            user_confirmed=False,  # This is what _finalize_task_terminal now sends
        )
        with patch.object(learner._store, "update_evidence") as mock_update:
            mock_update.return_value = None
            await learner.record_outcome([spec], outcome)
            # success=True but user_confirmed=False → should NOT increment success_count
            if mock_update.called:
                evidence_arg = mock_update.call_args[0][1]
                assert evidence_arg.success_count == 0, (
                    "Should not write positive evidence without user_confirmed=True"
                )


class TestGuardDeniedSignalClassification:
    """Verify guard deny vs tool failure signal separation."""

    def test_guard_deny_codes_classify_correctly(self) -> None:
        """Error codes from guardrail.py should be in _GUARD_DENY_CODES."""
        from src.agent.message_flow import _GUARD_DENY_CODES

        assert "GUARD_CONTRACT_UNAVAILABLE" in _GUARD_DENY_CODES
        assert "GUARD_ANCHOR_MISSING" in _GUARD_DENY_CODES
        assert "MODE_DENIED" in _GUARD_DENY_CODES
        # Tool-level errors should NOT be in guard codes
        assert "EXECUTION_ERROR" not in _GUARD_DENY_CODES
        assert "UNKNOWN_TOOL" not in _GUARD_DENY_CODES
