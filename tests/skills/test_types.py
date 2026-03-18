"""Unit tests for src.skills.types domain types (P2-M1b-P0).

Validates:
- SkillSpec frozen immutability + no ``status`` field
- SkillEvidence.last_validated_at datetime type alignment
- TaskFrame.task_type finite enum validation
- TaskOutcome terminal_state literal enforcement
- SkillSpec.version vs governance_version conceptual separation
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from src.skills.types import (
    ResolvedSkillView,
    SkillEvidence,
    SkillSpec,
    TaskFrame,
    TaskOutcome,
    TaskType,
    ToolResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_spec(**overrides: object) -> SkillSpec:
    defaults = {
        "id": "sk-test-001",
        "capability": "code_review",
        "version": 1,
        "summary": "Reviews code changes",
        "activation": "When user asks for code review",
    }
    defaults.update(overrides)
    return SkillSpec(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SkillSpec
# ---------------------------------------------------------------------------


class TestSkillSpec:
    def test_frozen_immutability(self) -> None:
        spec = _make_skill_spec()
        with pytest.raises(ValidationError):
            spec.id = "modified"  # type: ignore[misc]

    def test_no_status_field(self) -> None:
        """SkillSpec must NOT have a status field -- lifecycle is in the ledger."""
        assert "status" not in SkillSpec.model_fields

    def test_defaults(self) -> None:
        spec = _make_skill_spec()
        assert spec.activation_tags == ()
        assert spec.preconditions == ()
        assert spec.delta == ()
        assert spec.tool_preferences == ()
        assert spec.escalation_rules == ()
        assert spec.exchange_policy == "local_only"
        assert spec.disabled is False

    def test_tuple_fields_from_list(self) -> None:
        """Lists passed in should be coerced to tuples."""
        spec = _make_skill_spec(
            activation_tags=["tag1", "tag2"],
            delta=["add logging"],
        )
        assert spec.activation_tags == ("tag1", "tag2")
        assert spec.delta == ("add logging",)

    def test_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            SkillSpec()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# SkillEvidence
# ---------------------------------------------------------------------------


class TestSkillEvidence:
    def test_last_validated_at_is_datetime(self) -> None:
        now = datetime.now(tz=UTC)
        ev = SkillEvidence(source="manual", last_validated_at=now)
        assert isinstance(ev.last_validated_at, datetime)
        assert ev.last_validated_at == now

    def test_last_validated_at_none_by_default(self) -> None:
        ev = SkillEvidence(source="auto")
        assert ev.last_validated_at is None

    def test_frozen(self) -> None:
        ev = SkillEvidence(source="auto")
        with pytest.raises(ValidationError):
            ev.source = "changed"  # type: ignore[misc]

    def test_counter_defaults(self) -> None:
        ev = SkillEvidence(source="test")
        assert ev.success_count == 0
        assert ev.failure_count == 0


# ---------------------------------------------------------------------------
# TaskFrame
# ---------------------------------------------------------------------------


class TestTaskFrame:
    def test_task_type_finite_enum(self) -> None:
        valid_values = {"research", "create", "edit", "debug", "chat", "unknown"}
        assert {t.value for t in TaskType} == valid_values

    def test_default_task_type(self) -> None:
        frame = TaskFrame()
        assert frame.task_type == TaskType.unknown

    def test_invalid_task_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TaskFrame(task_type="nonexistent")  # type: ignore[arg-type]

    def test_frozen(self) -> None:
        frame = TaskFrame()
        with pytest.raises(ValidationError):
            frame.task_type = TaskType.chat  # type: ignore[misc]

    def test_all_defaults(self) -> None:
        frame = TaskFrame()
        assert frame.target_outcome is None
        assert frame.risk is None
        assert frame.channel is None
        assert frame.current_mode == "chat_safe"
        assert frame.current_procedure is None
        assert frame.available_tools == ()


# ---------------------------------------------------------------------------
# TaskOutcome
# ---------------------------------------------------------------------------


class TestTaskOutcome:
    def test_valid_terminal_states(self) -> None:
        for state in (
            "assistant_response",
            "tool_failure",
            "guard_denied",
            "procedure_terminal",
            "max_iterations",
        ):
            outcome = TaskOutcome(success=True, terminal_state=state)  # type: ignore[arg-type]
            assert outcome.terminal_state == state

    def test_frozen(self) -> None:
        outcome = TaskOutcome(success=True, terminal_state="assistant_response")
        with pytest.raises(AttributeError):
            outcome.success = False  # type: ignore[misc]

    def test_defaults(self) -> None:
        outcome = TaskOutcome(success=False, terminal_state="tool_failure")
        assert outcome.tool_results == ()
        assert outcome.user_confirmed is False
        assert outcome.failure_signals == ()

    def test_with_tool_results(self) -> None:
        tr = ToolResult(tool_name="read_file", success=True, output="content")
        outcome = TaskOutcome(
            success=True,
            terminal_state="assistant_response",
            tool_results=(tr,),
        )
        assert len(outcome.tool_results) == 1
        assert outcome.tool_results[0].tool_name == "read_file"


# ---------------------------------------------------------------------------
# ResolvedSkillView
# ---------------------------------------------------------------------------


class TestResolvedSkillView:
    def test_defaults(self) -> None:
        view = ResolvedSkillView()
        assert view.llm_delta == ()
        assert view.runtime_hints == ()
        assert view.escalation_signals == ()

    def test_frozen(self) -> None:
        view = ResolvedSkillView()
        with pytest.raises(ValidationError):
            view.llm_delta = ("x",)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Conceptual: SkillSpec.version vs governance_version
# ---------------------------------------------------------------------------


class TestVersionSemantics:
    """Documentary tests: SkillSpec.version (spec revision) is distinct from
    governance_version (ledger sequence number).

    SkillSpec.version tracks the logical revision of the spec content.
    governance_version is a monotonic sequence in the ledger table
    (BIGSERIAL), independent of spec content version.
    """

    def test_spec_version_is_content_version(self) -> None:
        v1 = _make_skill_spec(version=1)
        v2 = _make_skill_spec(version=2)
        assert v1.version != v2.version
        # Both are valid SkillSpecs; version is a simple integer field
        assert isinstance(v1.version, int)

    def test_spec_has_no_governance_version_field(self) -> None:
        """SkillSpec must not leak governance_version into the current-state model."""
        assert "governance_version" not in SkillSpec.model_fields
