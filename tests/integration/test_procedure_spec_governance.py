"""PostgreSQL integration tests for ProcedureSpec governance lifecycle (P2-M2c).

Covers:
- End-to-end propose → evaluate → apply → rollback on real PG
- Duplicate apply rejected (application layer + DB partial unique index)
- Rollback → new propose → apply recovery path
- Governance ledger completeness (proposed → active → rolled_back → proposed → active)
- JSONB round-trip: frozenset, nested frozen models, multi-state actions
- ensure_schema creates governance tables + partial unique index
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import DB_SCHEMA
from src.growth.adapters.procedure_spec import ProcedureSpecGovernedObjectAdapter
from src.growth.types import GrowthLifecycleStatus, GrowthObjectKind, GrowthProposal
from src.procedures.governance_store import ProcedureSpecGovernanceStore
from src.procedures.registry import (
    ProcedureContextRegistry,
    ProcedureGuardRegistry,
    ProcedureSpecRegistry,
)
from src.procedures.store import ProcedureStore
from src.procedures.types import ActionSpec, ProcedureSpec, StateSpec
from src.session.database import ensure_schema
from src.tools.base import BaseTool, ToolMode
from src.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeTool(BaseTool):
    """Minimal BaseTool for registry population."""

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
        return frozenset()

    async def execute(self, arguments: dict, **kwargs) -> str:
        return "ok"


class _TestContext(BaseModel):
    field: str = ""


def _build_registries(tool_registry: ToolRegistry):
    """Build procedure registries with test context model registered."""
    ctx_reg = ProcedureContextRegistry()
    ctx_reg.register("test_context", _TestContext)
    guard_reg = ProcedureGuardRegistry()
    spec_reg = ProcedureSpecRegistry(tool_registry, ctx_reg, guard_reg)
    return spec_reg, ctx_reg, guard_reg


def _make_spec(
    spec_id: str = "proc-integ-001",
    version: int = 1,
    **overrides,
) -> ProcedureSpec:
    defaults: dict = {
        "id": spec_id,
        "version": version,
        "summary": "Integration test procedure",
        "entry_policy": "explicit",
        "allowed_modes": frozenset({ToolMode.chat_safe, ToolMode.coding}),
        "context_model": "test_context",
        "initial_state": "start",
        "states": {
            "start": StateSpec(
                actions={
                    "do_work": ActionSpec(tool="fake_tool", to="review"),
                }
            ),
            "review": StateSpec(
                actions={
                    "approve": ActionSpec(tool="fake_tool", to="done"),
                    "reject": ActionSpec(tool="fake_tool", to="start"),
                }
            ),
            "done": StateSpec(actions={}),
        },
    }
    defaults.update(overrides)
    return ProcedureSpec(**defaults)


def _make_governance_proposal(spec: ProcedureSpec) -> GrowthProposal:
    return GrowthProposal(
        object_kind=GrowthObjectKind.procedure_spec,
        object_id=spec.id,
        intent="Test governance lifecycle",
        risk_notes="Integration test",
        diff_summary="New procedure spec for testing",
        payload={"procedure_spec": spec.model_dump(mode="json")},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_propose_evaluate_apply_rollback(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Full lifecycle: propose → evaluate → apply → rollback."""
    await ensure_schema(db_engine, DB_SCHEMA)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("fake_tool"))
    spec_reg, ctx_reg, guard_reg = _build_registries(tool_registry)
    gov_store = ProcedureSpecGovernanceStore(db_session_factory)
    proc_store = ProcedureStore(db_session_factory)

    adapter = ProcedureSpecGovernedObjectAdapter(
        governance_store=gov_store,
        spec_registry=spec_reg,
        tool_registry=tool_registry,
        context_registry=ctx_reg,
        guard_registry=guard_reg,
        procedure_store=proc_store,
    )

    spec = _make_spec()
    proposal = _make_governance_proposal(spec)

    # Propose
    gv = await adapter.propose(proposal)
    assert gv >= 1
    record = await gov_store.get_proposal(gv)
    assert record is not None
    assert record.status == "proposed"

    # Evaluate
    result = await adapter.evaluate(gv)
    assert result.passed is True
    assert len(result.checks) == 5

    # Apply
    await adapter.apply(gv)
    assert spec_reg.get("proc-integ-001") is not None
    applied_record = await gov_store.get_proposal(gv)
    assert applied_record is not None
    assert applied_record.status == "active"

    # Rollback
    rollback_gv = await adapter.rollback(procedure_spec_id="proc-integ-001")
    assert rollback_gv > gv
    assert spec_reg.get("proc-integ-001") is None
    rolled_back_record = await gov_store.get_proposal(gv)
    assert rolled_back_record is not None
    assert rolled_back_record.status == "rolled_back"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_apply_same_spec_twice_rejected(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """apply same spec_id twice → second rejected (app layer + DB unique index)."""
    await ensure_schema(db_engine, DB_SCHEMA)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("fake_tool"))
    spec_reg, ctx_reg, guard_reg = _build_registries(tool_registry)
    gov_store = ProcedureSpecGovernanceStore(db_session_factory)
    proc_store = ProcedureStore(db_session_factory)

    adapter = ProcedureSpecGovernedObjectAdapter(
        governance_store=gov_store,
        spec_registry=spec_reg,
        tool_registry=tool_registry,
        context_registry=ctx_reg,
        guard_registry=guard_reg,
        procedure_store=proc_store,
    )

    spec = _make_spec(spec_id="proc-dup-test")
    proposal = _make_governance_proposal(spec)

    # First apply
    gv1 = await adapter.propose(proposal)
    await adapter.evaluate(gv1)
    await adapter.apply(gv1)

    # Second proposal for same spec
    gv2 = await adapter.propose(proposal)
    await adapter.evaluate(gv2)
    with pytest.raises(ValueError, match="already has active version"):
        await adapter.apply(gv2)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rollback_then_new_apply_recovery(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """rollback → new propose → apply → confirms recovery path."""
    await ensure_schema(db_engine, DB_SCHEMA)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("fake_tool"))
    spec_reg, ctx_reg, guard_reg = _build_registries(tool_registry)
    gov_store = ProcedureSpecGovernanceStore(db_session_factory)
    proc_store = ProcedureStore(db_session_factory)

    adapter = ProcedureSpecGovernedObjectAdapter(
        governance_store=gov_store,
        spec_registry=spec_reg,
        tool_registry=tool_registry,
        context_registry=ctx_reg,
        guard_registry=guard_reg,
        procedure_store=proc_store,
    )

    spec_v1 = _make_spec(spec_id="proc-recovery", version=1)
    p1 = _make_governance_proposal(spec_v1)
    gv1 = await adapter.propose(p1)
    await adapter.evaluate(gv1)
    await adapter.apply(gv1)
    assert spec_reg.get("proc-recovery") is not None

    # Rollback v1
    await adapter.rollback(procedure_spec_id="proc-recovery")
    assert spec_reg.get("proc-recovery") is None

    # Propose + eval + apply v2
    spec_v2 = _make_spec(spec_id="proc-recovery", version=2)
    p2 = _make_governance_proposal(spec_v2)
    gv2 = await adapter.propose(p2)
    await adapter.evaluate(gv2)
    await adapter.apply(gv2)
    restored = spec_reg.get("proc-recovery")
    assert restored is not None
    assert restored.version == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_governance_ledger_completeness(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Ledger records: proposed → active → rolled_back → proposed → active."""
    await ensure_schema(db_engine, DB_SCHEMA)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("fake_tool"))
    spec_reg, ctx_reg, guard_reg = _build_registries(tool_registry)
    gov_store = ProcedureSpecGovernanceStore(db_session_factory)
    proc_store = ProcedureStore(db_session_factory)

    adapter = ProcedureSpecGovernedObjectAdapter(
        governance_store=gov_store,
        spec_registry=spec_reg,
        tool_registry=tool_registry,
        context_registry=ctx_reg,
        guard_registry=guard_reg,
        procedure_store=proc_store,
    )

    spec_id = "proc-ledger"
    spec_v1 = _make_spec(spec_id=spec_id, version=1)
    p1 = _make_governance_proposal(spec_v1)

    # proposed → active
    gv1 = await adapter.propose(p1)
    await adapter.evaluate(gv1)
    await adapter.apply(gv1)

    r1 = await gov_store.get_proposal(gv1)
    assert r1 is not None
    assert r1.status == "active"

    # active → rolled_back
    rollback_gv = await adapter.rollback(procedure_spec_id=spec_id)
    r1_after = await gov_store.get_proposal(gv1)
    assert r1_after is not None
    assert r1_after.status == "rolled_back"

    rollback_record = await gov_store.get_proposal(rollback_gv)
    assert rollback_record is not None
    assert rollback_record.status == "rolled_back"
    assert rollback_record.rolled_back_from == gv1

    # new proposed → active
    spec_v2 = _make_spec(spec_id=spec_id, version=2)
    p2 = _make_governance_proposal(spec_v2)
    gv2 = await adapter.propose(p2)
    await adapter.evaluate(gv2)
    await adapter.apply(gv2)

    r2 = await gov_store.get_proposal(gv2)
    assert r2 is not None
    assert r2.status == "active"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_jsonb_round_trip(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """JSONB round-trip: frozenset, nested frozen models, multi-state actions."""
    await ensure_schema(db_engine, DB_SCHEMA)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("fake_tool"))
    tool_registry.register(_FakeTool("review_tool"))
    spec_reg, ctx_reg, guard_reg = _build_registries(tool_registry)
    gov_store = ProcedureSpecGovernanceStore(db_session_factory)
    proc_store = ProcedureStore(db_session_factory)

    adapter = ProcedureSpecGovernedObjectAdapter(
        governance_store=gov_store,
        spec_registry=spec_reg,
        tool_registry=tool_registry,
        context_registry=ctx_reg,
        guard_registry=guard_reg,
        procedure_store=proc_store,
    )

    spec = _make_spec(
        spec_id="proc-jsonb-rt",
        allowed_modes=frozenset({ToolMode.chat_safe, ToolMode.coding}),
        states={
            "start": StateSpec(
                actions={
                    "submit": ActionSpec(tool="fake_tool", to="review"),
                }
            ),
            "review": StateSpec(
                actions={
                    "approve": ActionSpec(tool="review_tool", to="done"),
                    "reject": ActionSpec(tool="review_tool", to="start"),
                }
            ),
            "done": StateSpec(actions={}),
        },
    )
    proposal = _make_governance_proposal(spec)

    gv = await adapter.propose(proposal)
    await adapter.evaluate(gv)
    await adapter.apply(gv)

    # Read back from DB and validate round-trip
    payloads = await gov_store.list_active()
    assert len(payloads) >= 1
    restored_payload = next(p for p in payloads if p["id"] == "proc-jsonb-rt")
    restored = ProcedureSpec.model_validate(restored_payload)

    assert restored.id == spec.id
    assert restored.version == spec.version
    assert restored.allowed_modes == spec.allowed_modes
    assert set(restored.states.keys()) == set(spec.states.keys())
    assert restored.states["review"].actions["approve"].tool == "review_tool"
    assert restored.initial_state == "start"
    assert restored.context_model == "test_context"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_compensate_clears_applied_at(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Compensation back to proposed must clear applied_at (no audit ghost)."""
    await ensure_schema(db_engine, DB_SCHEMA)

    gov_store = ProcedureSpecGovernanceStore(db_session_factory)

    spec = _make_spec(spec_id="proc-compensate-test")
    proposal = GrowthProposal(
        object_kind=GrowthObjectKind.procedure_spec,
        object_id=spec.id,
        intent="test compensate",
        risk_notes="none",
        diff_summary="test",
        payload={"procedure_spec": spec.model_dump(mode="json")},
    )
    gv = await gov_store.create_proposal(proposal)

    # Simulate apply: set active + applied_at
    await gov_store.update_proposal_status(
        gv, GrowthLifecycleStatus.active, applied_at=datetime.now(UTC),
    )
    record = await gov_store.get_proposal(gv)
    assert record is not None
    assert record.status == "active"
    assert record.applied_at is not None

    # Simulate compensation: revert to proposed
    await gov_store.update_proposal_status(gv, GrowthLifecycleStatus.proposed)
    record_after = await gov_store.get_proposal(gv)
    assert record_after is not None
    assert record_after.status == "proposed"
    assert record_after.applied_at is None  # must be cleared


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rollback_preserves_applied_at(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """[P2] rollback must preserve applied_at timestamp on the ledger row."""
    await ensure_schema(db_engine, DB_SCHEMA)

    tool_registry = ToolRegistry()
    tool_registry.register(_FakeTool("fake_tool"))
    spec_reg, ctx_reg, guard_reg = _build_registries(tool_registry)
    gov_store = ProcedureSpecGovernanceStore(db_session_factory)
    proc_store = ProcedureStore(db_session_factory)

    adapter = ProcedureSpecGovernedObjectAdapter(
        governance_store=gov_store,
        spec_registry=spec_reg,
        tool_registry=tool_registry,
        context_registry=ctx_reg,
        guard_registry=guard_reg,
        procedure_store=proc_store,
    )

    spec = _make_spec(spec_id="proc-applied-at-test")
    proposal = _make_governance_proposal(spec)

    gv = await adapter.propose(proposal)
    await adapter.evaluate(gv)
    await adapter.apply(gv)

    # Record applied_at before rollback
    record_before = await gov_store.get_proposal(gv)
    assert record_before is not None
    assert record_before.applied_at is not None
    original_applied_at = record_before.applied_at

    # Rollback
    await adapter.rollback(procedure_spec_id="proc-applied-at-test")

    # Check applied_at is preserved
    record_after = await gov_store.get_proposal(gv)
    assert record_after is not None
    assert record_after.status == "rolled_back"
    assert record_after.applied_at == original_applied_at


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ensure_schema_creates_governance_tables(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """ensure_schema creates both governance tables + partial unique index."""
    await ensure_schema(db_engine, DB_SCHEMA)

    async with db_session_factory() as session:
        # Check procedure_spec_definitions exists
        result = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema = '{DB_SCHEMA}' "
                "AND table_name = 'procedure_spec_definitions'"
            )
        )
        assert result.first() is not None

        # Check procedure_spec_governance exists
        result = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                f"WHERE table_schema = '{DB_SCHEMA}' "
                "AND table_name = 'procedure_spec_governance'"
            )
        )
        assert result.first() is not None

        # Check partial unique index exists
        result = await session.execute(
            text(
                "SELECT 1 FROM pg_indexes "
                f"WHERE schemaname = '{DB_SCHEMA}' "
                "AND indexname = 'uq_procedure_spec_governance_single_active'"
            )
        )
        assert result.first() is not None
