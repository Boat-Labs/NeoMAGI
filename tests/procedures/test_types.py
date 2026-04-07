"""Tests for src/procedures/types.py — core type definitions and helpers."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.procedures.types import (
    RESERVED_ACTION_IDS,
    ActionSpec,
    ActiveProcedure,
    CasConflict,
    GuardDecision,
    ProcedureExecutionMetadata,
    ProcedureSpec,
    StateSpec,
    _validate_function_name,
    build_procedure_view,
    build_virtual_action_schema,
)
from src.tools.base import ToolMode

# ---------------------------------------------------------------------------
# ActionSpec / StateSpec / ProcedureSpec — frozen and well-formed
# ---------------------------------------------------------------------------


class TestActionSpec:
    def test_frozen(self) -> None:
        a = ActionSpec(tool="t", to="s")
        with pytest.raises(ValidationError):
            a.tool = "x"  # type: ignore[misc]

    def test_optional_guard(self) -> None:
        a = ActionSpec(tool="t", to="s")
        assert a.guard is None

    def test_with_guard(self) -> None:
        a = ActionSpec(tool="t", to="s", guard="g")
        assert a.guard == "g"


class TestStateSpec:
    def test_empty_actions_is_terminal(self) -> None:
        s = StateSpec()
        assert s.actions == {}

    def test_frozen(self) -> None:
        s = StateSpec(actions={"go": ActionSpec(tool="t", to="s")})
        with pytest.raises(ValidationError):
            s.actions = {}  # type: ignore[misc]


class TestProcedureSpec:
    def test_minimal_valid(self) -> None:
        spec = ProcedureSpec(
            id="test",
            version=1,
            summary="A test",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="Ctx",
            initial_state="start",
            states={"start": StateSpec(), "end": StateSpec()},
        )
        assert spec.id == "test"
        assert spec.soft_policies == ()

    def test_frozen(self) -> None:
        spec = ProcedureSpec(
            id="test",
            version=1,
            summary="A test",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="Ctx",
            initial_state="start",
            states={"start": StateSpec()},
        )
        with pytest.raises(ValidationError):
            spec.id = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProcedureExecutionMetadata — extra='forbid'
# ---------------------------------------------------------------------------


class TestProcedureExecutionMetadata:
    def test_defaults_all_none(self) -> None:
        m = ProcedureExecutionMetadata()
        assert m.actor is None
        assert m.shared_space_id is None

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ProcedureExecutionMetadata(actor="a", unknown_field="bad")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        m = ProcedureExecutionMetadata(actor="a")
        with pytest.raises(ValidationError):
            m.actor = "b"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ActiveProcedure
# ---------------------------------------------------------------------------


class TestActiveProcedure:
    def test_defaults(self) -> None:
        a = ActiveProcedure(
            instance_id="p1",
            session_id="s1",
            spec_id="sp",
            spec_version=1,
            state="draft",
        )
        assert a.revision == 0
        assert a.context == {}
        assert a.execution_metadata.actor is None

    def test_frozen(self) -> None:
        a = ActiveProcedure(
            instance_id="p1",
            session_id="s1",
            spec_id="sp",
            spec_version=1,
            state="draft",
        )
        with pytest.raises(ValidationError):
            a.state = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# GuardDecision / CasConflict
# ---------------------------------------------------------------------------


class TestGuardDecision:
    def test_allowed(self) -> None:
        d = GuardDecision(allowed=True)
        assert d.allowed is True
        assert d.code == ""

    def test_denied(self) -> None:
        d = GuardDecision(allowed=False, code="DENY", detail="nope")
        assert d.allowed is False
        assert d.code == "DENY"

    def test_frozen(self) -> None:
        d = GuardDecision(allowed=True)
        with pytest.raises(AttributeError):
            d.allowed = False  # type: ignore[misc]


class TestCasConflict:
    def test_with_actual(self) -> None:
        c = CasConflict(instance_id="i1", expected_revision=5, actual_revision=3)
        assert c.actual_revision == 3

    def test_without_actual(self) -> None:
        c = CasConflict(instance_id="i1", expected_revision=5)
        assert c.actual_revision is None


# ---------------------------------------------------------------------------
# _validate_function_name
# ---------------------------------------------------------------------------


class TestValidateFunctionName:
    def test_valid_names(self) -> None:
        assert _validate_function_name("submit") is None
        assert _validate_function_name("do_thing") is None
        assert _validate_function_name("action-1") is None
        assert _validate_function_name("A" * 64) is None

    def test_too_long(self) -> None:
        result = _validate_function_name("a" * 65)
        assert result is not None

    def test_invalid_chars(self) -> None:
        assert _validate_function_name("has space") is not None
        assert _validate_function_name("has.dot") is not None
        assert _validate_function_name("") is not None


# ---------------------------------------------------------------------------
# build_virtual_action_schema
# ---------------------------------------------------------------------------


class TestBuildVirtualActionSchema:
    def test_structure(self) -> None:
        action = ActionSpec(tool="my_tool", to="done")
        schema = build_virtual_action_schema(
            "submit", action, "Submit something", {"type": "object"},
        )
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] == "submit"
        assert "[Procedure action]" in fn["description"]
        assert fn["parameters"] == {"type": "object"}


# ---------------------------------------------------------------------------
# build_procedure_view
# ---------------------------------------------------------------------------


class TestBuildProcedureView:
    def test_with_actions(self) -> None:
        spec = ProcedureSpec(
            id="test",
            version=2,
            summary="A test procedure",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="Ctx",
            initial_state="draft",
            states={
                "draft": StateSpec(actions={
                    "submit": ActionSpec(tool="t", to="review"),
                    "cancel": ActionSpec(tool="t", to="cancelled"),
                }),
                "review": StateSpec(),
                "cancelled": StateSpec(),
            },
            soft_policies=("be_nice",),
        )
        active = ActiveProcedure(
            instance_id="p1", session_id="s1", spec_id="test",
            spec_version=2, state="draft", revision=3,
        )
        view = build_procedure_view(spec, active)
        assert view.id == "test"
        assert view.version == 2
        assert view.state == "draft"
        assert view.revision == 3
        assert set(view.allowed_actions) == {"submit", "cancel"}
        assert view.soft_policies == ("be_nice",)

    def test_terminal_state(self) -> None:
        spec = ProcedureSpec(
            id="test",
            version=1,
            summary="Test",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="Ctx",
            initial_state="start",
            states={"start": StateSpec(), "end": StateSpec()},
        )
        active = ActiveProcedure(
            instance_id="p1", session_id="s1", spec_id="test",
            spec_version=1, state="start", revision=0,
        )
        view = build_procedure_view(spec, active)
        assert view.allowed_actions == ()


# ---------------------------------------------------------------------------
# RESERVED_ACTION_IDS
# ---------------------------------------------------------------------------


class TestReservedActionIds:
    def test_procedure_enter_reserved(self) -> None:
        assert "procedure_enter" in RESERVED_ACTION_IDS
