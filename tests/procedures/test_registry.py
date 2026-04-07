"""Tests for src/procedures/registry.py — registries and static validation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.procedures.registry import (
    ProcedureContextRegistry,
    ProcedureGuardRegistry,
    ProcedureSpecRegistry,
    validate_procedure_spec,
)
from src.procedures.types import (
    ActionSpec,
    GuardDecision,
    ProcedureSpec,
    StateSpec,
)
from src.tools.base import BaseTool, ToolMode
from src.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _DummyContext(BaseModel):
    value: str = ""


def _dummy_guard(ctx: dict, tool_ctx=None) -> GuardDecision:
    return GuardDecision(allowed=True)


class _FakeTool(BaseTool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake tool {self._name}"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    async def execute(self, arguments: dict, context=None) -> dict:
        return {"ok": True}


def _make_valid_spec(
    spec_id: str = "test",
    tool_name: str = "fake_tool",
    guard_name: str | None = None,
) -> ProcedureSpec:
    return ProcedureSpec(
        id=spec_id,
        version=1,
        summary="A test procedure",
        entry_policy="explicit",
        allowed_modes=frozenset({ToolMode.coding}),
        context_model="TestCtx",
        initial_state="draft",
        states={
            "draft": StateSpec(actions={
                "submit": ActionSpec(tool=tool_name, to="done", guard=guard_name),
            }),
            "done": StateSpec(),
        },
    )


# ---------------------------------------------------------------------------
# ProcedureContextRegistry
# ---------------------------------------------------------------------------


class TestProcedureContextRegistry:
    def test_register_and_resolve(self) -> None:
        reg = ProcedureContextRegistry()
        reg.register("TestCtx", _DummyContext)
        assert reg.resolve("TestCtx") is _DummyContext

    def test_resolve_missing(self) -> None:
        reg = ProcedureContextRegistry()
        assert reg.resolve("missing") is None

    def test_duplicate_raises(self) -> None:
        reg = ProcedureContextRegistry()
        reg.register("TestCtx", _DummyContext)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("TestCtx", _DummyContext)


# ---------------------------------------------------------------------------
# ProcedureGuardRegistry
# ---------------------------------------------------------------------------


class TestProcedureGuardRegistry:
    def test_register_and_resolve(self) -> None:
        reg = ProcedureGuardRegistry()
        reg.register("check", _dummy_guard)
        assert reg.resolve("check") is _dummy_guard

    def test_resolve_missing(self) -> None:
        reg = ProcedureGuardRegistry()
        assert reg.resolve("missing") is None

    def test_duplicate_raises(self) -> None:
        reg = ProcedureGuardRegistry()
        reg.register("check", _dummy_guard)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("check", _dummy_guard)


# ---------------------------------------------------------------------------
# ProcedureSpecRegistry
# ---------------------------------------------------------------------------


class TestProcedureSpecRegistry:
    def test_register_and_get(self) -> None:
        reg = ProcedureSpecRegistry()
        spec = _make_valid_spec()
        reg.register(spec)
        assert reg.get("test") is spec

    def test_get_missing(self) -> None:
        reg = ProcedureSpecRegistry()
        assert reg.get("missing") is None

    def test_duplicate_raises(self) -> None:
        reg = ProcedureSpecRegistry()
        spec = _make_valid_spec()
        reg.register(spec)
        with pytest.raises(ValueError, match="already registered"):
            reg.register(spec)

    def test_list_specs(self) -> None:
        reg = ProcedureSpecRegistry()
        s1 = _make_valid_spec("a")
        s2 = _make_valid_spec("b")
        reg.register(s1)
        reg.register(s2)
        specs = reg.list_specs()
        assert len(specs) == 2

    def test_register_with_validation_rejects_invalid(self) -> None:
        """When registries are provided, register() validates and rejects bad specs."""
        tool_reg = ToolRegistry()
        tool_reg.register(_FakeTool("fake_tool"))
        ctx_reg = ProcedureContextRegistry()
        ctx_reg.register("TestCtx", _DummyContext)
        guard_reg = ProcedureGuardRegistry()

        reg = ProcedureSpecRegistry(tool_reg, ctx_reg, guard_reg)
        bad_spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad spec",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="MissingCtx",
            initial_state="missing",
            states={"start": StateSpec()},
        )
        with pytest.raises(ValueError, match="failed validation"):
            reg.register(bad_spec)
        assert reg.get("bad") is None

    def test_register_with_validation_accepts_valid(self) -> None:
        """Valid spec passes validation and is registered."""
        tool_reg = ToolRegistry()
        tool_reg.register(_FakeTool("fake_tool"))
        ctx_reg = ProcedureContextRegistry()
        ctx_reg.register("TestCtx", _DummyContext)
        guard_reg = ProcedureGuardRegistry()

        reg = ProcedureSpecRegistry(tool_reg, ctx_reg, guard_reg)
        spec = _make_valid_spec()
        reg.register(spec)
        assert reg.get("test") is spec


# ---------------------------------------------------------------------------
# validate_procedure_spec
# ---------------------------------------------------------------------------


class TestValidateProcedureSpec:
    def _build_registries(
        self,
    ) -> tuple[ToolRegistry, ProcedureContextRegistry, ProcedureGuardRegistry]:
        tool_reg = ToolRegistry()
        tool_reg.register(_FakeTool("fake_tool"))
        ctx_reg = ProcedureContextRegistry()
        ctx_reg.register("TestCtx", _DummyContext)
        guard_reg = ProcedureGuardRegistry()
        guard_reg.register("check", _dummy_guard)
        return tool_reg, ctx_reg, guard_reg

    def test_valid_spec_no_errors(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = _make_valid_spec()
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert errors == []

    def test_initial_state_missing(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="TestCtx",
            initial_state="nonexistent",
            states={"start": StateSpec()},
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("initial_state" in e for e in errors)

    def test_context_model_missing(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="NonexistentCtx",
            initial_state="start",
            states={"start": StateSpec()},
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("context_model" in e for e in errors)

    def test_target_state_missing(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="TestCtx",
            initial_state="start",
            states={
                "start": StateSpec(actions={
                    "go": ActionSpec(tool="fake_tool", to="missing"),
                }),
            },
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("target state" in e for e in errors)

    def test_tool_missing(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="TestCtx",
            initial_state="start",
            states={
                "start": StateSpec(actions={
                    "go": ActionSpec(tool="missing_tool", to="start"),
                }),
            },
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("tool" in e and "not found" in e for e in errors)

    def test_guard_missing(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = _make_valid_spec(guard_name="missing_guard")
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("guard" in e and "not found" in e for e in errors)

    def test_enter_guard_missing(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="TestCtx",
            initial_state="start",
            enter_guard="missing_enter_guard",
            states={"start": StateSpec()},
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("enter_guard" in e for e in errors)

    def test_action_id_collides_with_reserved(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="TestCtx",
            initial_state="start",
            states={
                "start": StateSpec(actions={
                    "procedure_enter": ActionSpec(tool="fake_tool", to="start"),
                }),
            },
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("reserved" in e for e in errors)

    def test_action_id_collides_with_ambient_tool(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="TestCtx",
            initial_state="start",
            states={
                "start": StateSpec(actions={
                    "fake_tool": ActionSpec(tool="fake_tool", to="start"),
                }),
            },
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("ambient tool" in e for e in errors)

    def test_action_id_invalid_function_name(self) -> None:
        tool_reg, ctx_reg, guard_reg = self._build_registries()
        spec = ProcedureSpec(
            id="bad",
            version=1,
            summary="Bad",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="TestCtx",
            initial_state="start",
            states={
                "start": StateSpec(actions={
                    "has space": ActionSpec(tool="fake_tool", to="start"),
                }),
            },
        )
        errors = validate_procedure_spec(spec, tool_reg, ctx_reg, guard_reg)
        assert any("function name" in e for e in errors)
