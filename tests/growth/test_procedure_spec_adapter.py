"""Tests for ProcedureSpecGovernedObjectAdapter (growth governance kernel, P2-M2c).

Covers: kind property, Protocol conformance, propose/evaluate/apply/rollback/veto/get_active,
eval checks (transition_determinism, guard_completeness, interrupt_resume_safety,
checkpoint_recoverability, scope_claim_consistency), payload validation, error paths,
compensation semantics, active instance safety, and update flow.

Uses mock ProcedureSpecGovernanceStore (AsyncMock) — no real DB required.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.growth.adapters.base import GovernedObjectAdapter
from src.growth.adapters.procedure_spec import (
    ProcedureSpecGovernedObjectAdapter,
    _check_checkpoint_recoverability,
    _check_guard_completeness,
    _check_interrupt_resume_safety,
    _check_scope_claim_consistency,
    _check_transition_determinism,
)
from src.growth.contracts import PROCEDURE_SPEC_EVAL_CONTRACT_V1
from src.growth.types import (
    GrowthEvalResult,
    GrowthLifecycleStatus,
    GrowthObjectKind,
    GrowthProposal,
)
from src.procedures.governance_store import ProcedureSpecProposalRecord
from src.procedures.registry import (
    ProcedureContextRegistry,
    ProcedureGuardRegistry,
    ProcedureSpecRegistry,
)
from src.procedures.types import ActionSpec, ProcedureSpec, StateSpec
from src.tools.base import ToolMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(**overrides: object) -> ProcedureSpec:
    defaults: dict = {
        "id": "proc-001",
        "version": 1,
        "summary": "Test procedure",
        "entry_policy": "explicit",
        "allowed_modes": frozenset({ToolMode.chat_safe}),
        "context_model": "test_context",
        "initial_state": "start",
        "states": {
            "start": StateSpec(
                actions={
                    "do_work": ActionSpec(tool="test_tool", to="done"),
                }
            ),
            "done": StateSpec(actions={}),
        },
    }
    defaults.update(overrides)
    return ProcedureSpec(**defaults)


def _make_spec_payload(**overrides: object) -> dict:
    spec = _make_spec(**overrides)
    return spec.model_dump(mode="json")


def _make_proposal(**overrides: object) -> GrowthProposal:
    defaults: dict = {
        "object_kind": GrowthObjectKind.procedure_spec,
        "object_id": "proc-001",
        "intent": "Create procedure spec",
        "risk_notes": "Low risk",
        "diff_summary": "New procedure spec",
        "payload": {
            "procedure_spec": _make_spec_payload(),
        },
    }
    defaults.update(overrides)
    return GrowthProposal(**defaults)


def _make_proposal_record(
    *,
    governance_version: int = 1,
    status: str = "proposed",
    eval_passed: bool | None = None,
    spec_payload: dict | None = None,
) -> ProcedureSpecProposalRecord:
    sp = spec_payload or _make_spec_payload()
    eval_result = None
    if eval_passed is not None:
        eval_result = {"passed": eval_passed}
    return ProcedureSpecProposalRecord(
        governance_version=governance_version,
        procedure_spec_id=sp["id"],
        status=status,
        proposal={
            "intent": "Create procedure spec",
            "payload": {"procedure_spec": sp},
        },
        eval_result=eval_result,
        created_by="agent",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        applied_at=None,
        rolled_back_from=None,
    )


@pytest.fixture()
def mock_governance_store() -> AsyncMock:
    store = AsyncMock()
    store.create_proposal = AsyncMock(return_value=1)
    store.get_proposal = AsyncMock(return_value=_make_proposal_record())
    store.store_eval_result = AsyncMock()
    store.update_proposal_status = AsyncMock()
    store.upsert_active = AsyncMock()
    store.disable = AsyncMock()
    store.find_last_applied = AsyncMock(return_value=None)
    store.list_active = AsyncMock(return_value=[])

    mock_session = MagicMock(name="mock_db_session")

    @asynccontextmanager
    async def _fake_transaction():
        yield mock_session

    store.transaction = _fake_transaction
    store._mock_session = mock_session
    return store


@pytest.fixture()
def mock_tool_registry() -> MagicMock:
    registry = MagicMock()
    # test_tool exists in the registry
    registry.get = MagicMock(side_effect=lambda name: MagicMock() if name == "test_tool" else None)
    registry.list_tools = MagicMock(return_value=[])
    return registry


@pytest.fixture()
def mock_context_registry() -> ProcedureContextRegistry:
    from pydantic import BaseModel

    class TestContext(BaseModel):
        field: str = ""

    reg = ProcedureContextRegistry()
    reg.register("test_context", TestContext)
    return reg


@pytest.fixture()
def mock_guard_registry() -> ProcedureGuardRegistry:
    return ProcedureGuardRegistry()


@pytest.fixture()
def mock_spec_registry(
    mock_tool_registry, mock_context_registry, mock_guard_registry,
) -> ProcedureSpecRegistry:
    return ProcedureSpecRegistry(
        mock_tool_registry, mock_context_registry, mock_guard_registry,
    )


@pytest.fixture()
def mock_procedure_store() -> AsyncMock:
    store = AsyncMock()
    store.has_active_for_spec = AsyncMock(return_value=False)
    return store


@pytest.fixture()
def adapter(
    mock_governance_store,
    mock_spec_registry,
    mock_tool_registry,
    mock_context_registry,
    mock_guard_registry,
    mock_procedure_store,
) -> ProcedureSpecGovernedObjectAdapter:
    return ProcedureSpecGovernedObjectAdapter(
        governance_store=mock_governance_store,
        spec_registry=mock_spec_registry,
        tool_registry=mock_tool_registry,
        context_registry=mock_context_registry,
        guard_registry=mock_guard_registry,
        procedure_store=mock_procedure_store,
    )


# ---------------------------------------------------------------------------
# Kind + Protocol
# ---------------------------------------------------------------------------


class TestKind:
    def test_kind_is_procedure_spec(self, adapter):
        assert adapter.kind == GrowthObjectKind.procedure_spec


class TestProtocolConformance:
    def test_isinstance_governed_object_adapter(self, adapter):
        assert isinstance(adapter, GovernedObjectAdapter)


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------


class TestPropose:
    @pytest.mark.asyncio
    async def test_propose_valid_spec(self, adapter, mock_governance_store):
        proposal = _make_proposal()
        gv = await adapter.propose(proposal)
        assert gv == 1
        mock_governance_store.create_proposal.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propose_invalid_payload(self, adapter):
        proposal = _make_proposal(payload={})
        with pytest.raises(ValueError, match="procedure_spec"):
            await adapter.propose(proposal)

    @pytest.mark.asyncio
    async def test_propose_non_dict_payload(self, adapter):
        proposal = _make_proposal(payload={"procedure_spec": "not-a-dict"})
        with pytest.raises(ValueError, match="procedure_spec"):
            await adapter.propose(proposal)

    @pytest.mark.asyncio
    async def test_propose_object_id_mismatch_rejected(self, adapter):
        """[P1] proposal.object_id must match spec.id."""
        proposal = _make_proposal(object_id="wrong-id")
        with pytest.raises(ValueError, match="does not match"):
            await adapter.propose(proposal)

    @pytest.mark.asyncio
    async def test_propose_normalizes_payload_to_json(self, adapter, mock_governance_store):
        """[P2] propose() must normalize payload via model_dump(mode='json')."""
        proposal = _make_proposal()
        await adapter.propose(proposal)
        # The stored proposal should have JSON-safe payload
        call_args = mock_governance_store.create_proposal.call_args[0][0]
        stored_spec = call_args.payload["procedure_spec"]
        # allowed_modes should be a list (not frozenset) after JSON normalization
        assert isinstance(stored_spec["allowed_modes"], list)


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_all_checks_pass(self, adapter, mock_governance_store):
        result = await adapter.evaluate(1)
        assert isinstance(result, GrowthEvalResult)
        assert result.passed is True
        assert result.contract_id == "procedure_spec_v1"
        assert result.contract_version == 1
        assert len(result.checks) == 5
        mock_governance_store.store_eval_result.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_evaluate_not_found(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(return_value=None)
        result = await adapter.evaluate(999)
        assert result.passed is False
        assert "not found" in result.summary

    @pytest.mark.asyncio
    async def test_evaluate_wrong_status(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(status="active")
        )
        result = await adapter.evaluate(1)
        assert result.passed is False
        assert "not 'proposed'" in result.summary

    @pytest.mark.asyncio
    async def test_evaluate_bad_payload(self, adapter, mock_governance_store):
        bad_record = ProcedureSpecProposalRecord(
            governance_version=1,
            procedure_spec_id="proc-001",
            status="proposed",
            proposal={"payload": {"procedure_spec": {"invalid": True}}},
            eval_result=None,
            created_by="agent",
            created_at=None,
            applied_at=None,
            rolled_back_from=None,
        )
        mock_governance_store.get_proposal = AsyncMock(return_value=bad_record)
        result = await adapter.evaluate(1)
        assert result.passed is False
        assert "parse error" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_evaluate_transition_determinism_fail(self, adapter, mock_governance_store):
        """action.to points to non-existent state."""
        bad_spec = _make_spec_payload(
            states={
                "start": StateSpec(
                    actions={"do_work": ActionSpec(tool="test_tool", to="nonexistent")}
                ),
                "done": StateSpec(actions={}),
            }
        )
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(spec_payload=bad_spec)
        )
        result = await adapter.evaluate(1)
        assert result.passed is False
        failed = [c for c in result.checks if not c["passed"]]
        assert any("transition_determinism" == c["name"] for c in failed)

    @pytest.mark.asyncio
    async def test_evaluate_guard_completeness_fail(
        self, adapter, mock_governance_store,
    ):
        """guard not in registry."""
        bad_spec = _make_spec_payload(
            states={
                "start": StateSpec(
                    actions={
                        "do_work": ActionSpec(
                            tool="test_tool", to="done", guard="unknown_guard"
                        )
                    }
                ),
                "done": StateSpec(actions={}),
            }
        )
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(spec_payload=bad_spec)
        )
        result = await adapter.evaluate(1)
        assert result.passed is False
        failed = [c for c in result.checks if not c["passed"]]
        assert any("guard_completeness" == c["name"] for c in failed)

    @pytest.mark.asyncio
    async def test_evaluate_checkpoint_recoverability_fail(
        self, adapter, mock_governance_store, mock_tool_registry,
    ):
        """tool not in ToolRegistry."""
        bad_spec = _make_spec_payload(
            states={
                "start": StateSpec(
                    actions={
                        "do_work": ActionSpec(tool="missing_tool", to="done")
                    }
                ),
                "done": StateSpec(actions={}),
            }
        )
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(spec_payload=bad_spec)
        )
        result = await adapter.evaluate(1)
        assert result.passed is False
        failed = [c for c in result.checks if not c["passed"]]
        assert any("checkpoint_recoverability" == c["name"] for c in failed)

    @pytest.mark.asyncio
    async def test_evaluate_scope_claim_fail(self, adapter, mock_governance_store):
        """context_model not resolvable."""
        bad_spec = _make_spec_payload(context_model="unknown_context")
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(spec_payload=bad_spec)
        )
        result = await adapter.evaluate(1)
        assert result.passed is False
        failed = [c for c in result.checks if not c["passed"]]
        assert any("scope_claim_consistency" == c["name"] for c in failed)

    @pytest.mark.asyncio
    async def test_evaluate_uses_context_guard_registry(
        self, adapter, mock_governance_store, mock_guard_registry,
    ):
        """guard_completeness and scope_claim_consistency use injected registries."""
        # Register a guard so guard_completeness passes when used
        mock_guard_registry.register("my_guard", lambda *a, **k: None)
        spec_payload = _make_spec_payload(
            enter_guard="my_guard",
            states={
                "start": StateSpec(
                    actions={
                        "do_work": ActionSpec(tool="test_tool", to="done", guard="my_guard")
                    }
                ),
                "done": StateSpec(actions={}),
            },
        )
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(spec_payload=spec_payload)
        )
        result = await adapter.evaluate(1)
        assert result.passed is True
        guard_check = [c for c in result.checks if c["name"] == "guard_completeness"][0]
        assert guard_check["passed"] is True


# ---------------------------------------------------------------------------
# Eval check functions (unit tests)
# ---------------------------------------------------------------------------


class TestCheckTransitionDeterminism:
    def test_valid(self):
        spec = _make_spec()
        result = _check_transition_determinism(spec)
        assert result["passed"] is True

    def test_initial_state_missing(self):
        spec = _make_spec(initial_state="nonexistent")
        result = _check_transition_determinism(spec)
        assert result["passed"] is False
        assert "initial_state" in result["detail"]

    def test_action_target_missing(self):
        spec = _make_spec(
            states={
                "start": StateSpec(
                    actions={"go": ActionSpec(tool="test_tool", to="nowhere")}
                ),
                "done": StateSpec(actions={}),
            }
        )
        result = _check_transition_determinism(spec)
        assert result["passed"] is False
        assert "nowhere" in result["detail"]


class TestCheckGuardCompleteness:
    def test_no_guards_passes(self):
        spec = _make_spec()
        guard_reg = ProcedureGuardRegistry()
        result = _check_guard_completeness(spec, guard_reg)
        assert result["passed"] is True

    def test_missing_enter_guard(self):
        spec = _make_spec(enter_guard="missing")
        guard_reg = ProcedureGuardRegistry()
        result = _check_guard_completeness(spec, guard_reg)
        assert result["passed"] is False
        assert "enter_guard" in result["detail"]

    def test_missing_action_guard(self):
        spec = _make_spec(
            states={
                "start": StateSpec(
                    actions={
                        "do_work": ActionSpec(tool="test_tool", to="done", guard="missing")
                    }
                ),
                "done": StateSpec(actions={}),
            }
        )
        guard_reg = ProcedureGuardRegistry()
        result = _check_guard_completeness(spec, guard_reg)
        assert result["passed"] is False
        assert "missing" in result["detail"]


class TestCheckInterruptResumeSafety:
    def test_valid(self):
        spec = _make_spec()
        result = _check_interrupt_resume_safety(spec)
        assert result["passed"] is True

    def test_no_terminal_state(self):
        spec = _make_spec(
            states={
                "start": StateSpec(
                    actions={"loop": ActionSpec(tool="test_tool", to="start")}
                ),
            }
        )
        result = _check_interrupt_resume_safety(spec)
        assert result["passed"] is False
        assert "terminal" in result["detail"].lower()


class TestCheckCheckpointRecoverability:
    def test_valid(self):
        spec = _make_spec()
        tool_reg = MagicMock()
        tool_reg.get = MagicMock(return_value=MagicMock())
        tool_reg.list_tools = MagicMock(return_value=[])
        result = _check_checkpoint_recoverability(spec, tool_reg)
        assert result["passed"] is True

    def test_missing_tool(self):
        spec = _make_spec()
        tool_reg = MagicMock()
        tool_reg.get = MagicMock(return_value=None)
        tool_reg.list_tools = MagicMock(return_value=[])
        result = _check_checkpoint_recoverability(spec, tool_reg)
        assert result["passed"] is False
        assert "not found" in result["detail"]

    def test_reserved_action_id(self):
        spec = _make_spec(
            states={
                "start": StateSpec(
                    actions={
                        "procedure_enter": ActionSpec(tool="test_tool", to="done")
                    }
                ),
                "done": StateSpec(actions={}),
            }
        )
        tool_reg = MagicMock()
        tool_reg.get = MagicMock(return_value=MagicMock())
        tool_reg.list_tools = MagicMock(return_value=[])
        result = _check_checkpoint_recoverability(spec, tool_reg)
        assert result["passed"] is False
        assert "reserved" in result["detail"].lower()

    def test_ambient_tool_collision(self):
        """[P2] action_id colliding with ambient tool name must fail."""
        spec = _make_spec(
            states={
                "start": StateSpec(
                    actions={
                        "memory_search": ActionSpec(tool="test_tool", to="done")
                    }
                ),
                "done": StateSpec(actions={}),
            }
        )
        ambient_tool = MagicMock()
        ambient_tool.name = "memory_search"
        tool_reg = MagicMock()
        tool_reg.get = MagicMock(return_value=MagicMock())
        tool_reg.list_tools = MagicMock(return_value=[ambient_tool])
        result = _check_checkpoint_recoverability(spec, tool_reg)
        assert result["passed"] is False
        assert "ambient" in result["detail"].lower()


class TestCheckScopeClaimConsistency:
    def test_valid(self):
        spec = _make_spec()
        ctx_reg = ProcedureContextRegistry()
        from pydantic import BaseModel

        class M(BaseModel):
            pass

        ctx_reg.register("test_context", M)
        result = _check_scope_claim_consistency(spec, ctx_reg)
        assert result["passed"] is True

    def test_missing_context_model(self):
        spec = _make_spec(context_model="unknown")
        ctx_reg = ProcedureContextRegistry()
        result = _check_scope_claim_consistency(spec, ctx_reg)
        assert result["passed"] is False
        assert "context_model" in result["detail"]

    def test_empty_allowed_modes(self):
        spec = _make_spec(allowed_modes=frozenset())
        ctx_reg = ProcedureContextRegistry()
        from pydantic import BaseModel

        class M(BaseModel):
            pass

        ctx_reg.register("test_context", M)
        result = _check_scope_claim_consistency(spec, ctx_reg)
        assert result["passed"] is False
        assert "allowed_modes" in result["detail"]


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


class TestApply:
    @pytest.mark.asyncio
    async def test_apply_success(self, adapter, mock_governance_store, mock_spec_registry):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(eval_passed=True)
        )
        await adapter.apply(1)
        mock_governance_store.upsert_active.assert_awaited_once()
        mock_governance_store.update_proposal_status.assert_awaited_once()
        # spec should be in registry
        assert mock_spec_registry.get("proc-001") is not None

    @pytest.mark.asyncio
    async def test_apply_already_active_rejected(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(eval_passed=True)
        )
        existing = _make_proposal_record(governance_version=5, status="active")
        mock_governance_store.find_last_applied = AsyncMock(return_value=existing)
        with pytest.raises(ValueError, match="already has active version"):
            await adapter.apply(1)
        mock_governance_store.upsert_active.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_apply_with_active_instance_rejected(
        self, adapter, mock_governance_store, mock_procedure_store,
    ):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(eval_passed=True)
        )
        mock_procedure_store.has_active_for_spec = AsyncMock(return_value=True)
        with pytest.raises(ValueError, match="active procedure instance"):
            await adapter.apply(1)

    @pytest.mark.asyncio
    async def test_apply_not_found(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await adapter.apply(999)

    @pytest.mark.asyncio
    async def test_apply_wrong_status(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(status="active")
        )
        with pytest.raises(ValueError, match="status is"):
            await adapter.apply(1)

    @pytest.mark.asyncio
    async def test_apply_eval_not_passed(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(eval_passed=False)
        )
        with pytest.raises(ValueError, match="eval not passed"):
            await adapter.apply(1)

    @pytest.mark.asyncio
    async def test_apply_ledger_key_mismatch_rejected(self, adapter, mock_governance_store):
        """[P1] ledger procedure_spec_id != payload spec.id → reject."""
        bad_record = ProcedureSpecProposalRecord(
            governance_version=1,
            procedure_spec_id="wrong-id",
            status="proposed",
            proposal={
                "intent": "test",
                "payload": {"procedure_spec": _make_spec_payload()},
            },
            eval_result={"passed": True},
            created_by="agent",
            created_at=None,
            applied_at=None,
            rolled_back_from=None,
        )
        mock_governance_store.get_proposal = AsyncMock(return_value=bad_record)
        with pytest.raises(ValueError, match="data integrity violation"):
            await adapter.apply(1)

    @pytest.mark.asyncio
    async def test_apply_db_first_then_registry(self, adapter, mock_governance_store):
        """DB writes must complete before registry mutation."""
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(eval_passed=True)
        )
        call_order: list[str] = []
        orig_upsert = mock_governance_store.upsert_active

        async def track_upsert(*a, **kw):
            call_order.append("db_upsert")
            return await orig_upsert(*a, **kw)

        mock_governance_store.upsert_active = track_upsert

        orig_register = adapter._spec_registry.register

        def track_register(spec):
            call_order.append("registry_register")
            return orig_register(spec)

        adapter._spec_registry.register = track_register
        await adapter.apply(1)
        assert call_order.index("db_upsert") < call_order.index("registry_register")

    @pytest.mark.asyncio
    async def test_apply_registry_failure_triggers_compensation(
        self, adapter, mock_governance_store,
    ):
        """Registry failure after DB commit must compensate DB."""
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(eval_passed=True)
        )
        adapter._spec_registry.register = MagicMock(
            side_effect=RuntimeError("registry boom")
        )
        with pytest.raises(RuntimeError, match="registry boom"):
            await adapter.apply(1)
        # Compensation: disable should be called
        assert mock_governance_store.disable.await_count >= 1


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


class TestRollback:
    @pytest.mark.asyncio
    async def test_rollback_disables_and_unregisters(
        self, adapter, mock_governance_store, mock_spec_registry,
    ):
        """Rollback disables in store and unregisters from registry."""
        current = _make_proposal_record(governance_version=2, status="active")
        mock_governance_store.find_last_applied = AsyncMock(return_value=current)
        # Pre-register so unregister succeeds
        spec = _make_spec()
        mock_spec_registry._specs["proc-001"] = spec

        gv = await adapter.rollback(procedure_spec_id="proc-001")
        assert isinstance(gv, int)
        mock_governance_store.disable.assert_awaited_once()
        # Should be unregistered
        assert mock_spec_registry.get("proc-001") is None

    @pytest.mark.asyncio
    async def test_rollback_with_active_instance_rejected(
        self, adapter, mock_governance_store, mock_procedure_store,
    ):
        mock_procedure_store.has_active_for_spec = AsyncMock(return_value=True)
        with pytest.raises(ValueError, match="active procedure instance"):
            await adapter.rollback(procedure_spec_id="proc-001")

    @pytest.mark.asyncio
    async def test_rollback_no_applied_rejected(self, adapter, mock_governance_store):
        mock_governance_store.find_last_applied = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="no applied version"):
            await adapter.rollback(procedure_spec_id="proc-001")

    @pytest.mark.asyncio
    async def test_rollback_missing_kwarg_raises(self, adapter):
        with pytest.raises(ValueError, match="procedure_spec_id"):
            await adapter.rollback()

    @pytest.mark.asyncio
    async def test_rollback_unregister_skip_when_not_in_registry(
        self, adapter, mock_governance_store,
    ):
        """Rollback when spec not in registry: unregister fails gracefully."""
        current = _make_proposal_record(governance_version=2, status="active")
        mock_governance_store.find_last_applied = AsyncMock(return_value=current)
        gv = await adapter.rollback(procedure_spec_id="proc-001")
        assert isinstance(gv, int)


# ---------------------------------------------------------------------------
# Veto
# ---------------------------------------------------------------------------


class TestVeto:
    @pytest.mark.asyncio
    async def test_veto_proposed(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(status="proposed")
        )
        await adapter.veto(1)
        mock_governance_store.update_proposal_status.assert_awaited_once()
        call_args = mock_governance_store.update_proposal_status.call_args
        assert call_args[0][1] == GrowthLifecycleStatus.vetoed

    @pytest.mark.asyncio
    async def test_veto_active_delegates_to_rollback(
        self, adapter, mock_governance_store,
    ):
        active_record = _make_proposal_record(governance_version=1, status="active")
        mock_governance_store.get_proposal = AsyncMock(return_value=active_record)
        mock_governance_store.find_last_applied = AsyncMock(return_value=active_record)
        await adapter.veto(1)
        mock_governance_store.disable.assert_awaited()

    @pytest.mark.asyncio
    async def test_veto_not_found_raises(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="not found"):
            await adapter.veto(999)

    @pytest.mark.asyncio
    async def test_veto_wrong_status_raises(self, adapter, mock_governance_store):
        mock_governance_store.get_proposal = AsyncMock(
            return_value=_make_proposal_record(status="rolled_back")
        )
        with pytest.raises(ValueError, match="Cannot veto"):
            await adapter.veto(1)


# ---------------------------------------------------------------------------
# GetActive
# ---------------------------------------------------------------------------


class TestGetActive:
    @pytest.mark.asyncio
    async def test_get_active(self, adapter, mock_spec_registry):
        spec = _make_spec()
        mock_spec_registry._specs["proc-001"] = spec
        result = await adapter.get_active()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].id == "proc-001"

    @pytest.mark.asyncio
    async def test_get_active_empty(self, adapter):
        result = await adapter.get_active()
        assert result == []


# ---------------------------------------------------------------------------
# Update flow: rollback → new propose → evaluate → apply
# ---------------------------------------------------------------------------


class TestUpdateFlow:
    @pytest.mark.asyncio
    async def test_update_flow_rollback_then_new_apply(
        self, adapter, mock_governance_store, mock_spec_registry,
    ):
        """Full update flow: rollback → new propose → evaluate → apply."""
        # Setup: v1 is active
        v1_record = _make_proposal_record(governance_version=1, status="active")
        mock_governance_store.find_last_applied = AsyncMock(return_value=v1_record)
        spec = _make_spec()
        mock_spec_registry._specs["proc-001"] = spec

        # Step 1: Rollback v1
        mock_governance_store.create_proposal = AsyncMock(return_value=2)
        await adapter.rollback(procedure_spec_id="proc-001")
        assert mock_spec_registry.get("proc-001") is None

        # Step 2: Propose v2
        mock_governance_store.create_proposal = AsyncMock(return_value=3)
        v2_spec = _make_spec_payload(version=2)
        v2_proposal = _make_proposal(
            payload={"procedure_spec": v2_spec},
        )
        gv = await adapter.propose(v2_proposal)
        assert gv == 3

        # Step 3: Evaluate v2
        v2_record = _make_proposal_record(
            governance_version=3, status="proposed", spec_payload=v2_spec,
        )
        mock_governance_store.get_proposal = AsyncMock(return_value=v2_record)
        result = await adapter.evaluate(3)
        assert result.passed is True

        # Step 4: Apply v2
        v2_record_eval = _make_proposal_record(
            governance_version=3, status="proposed", eval_passed=True,
            spec_payload=v2_spec,
        )
        mock_governance_store.get_proposal = AsyncMock(return_value=v2_record_eval)
        mock_governance_store.find_last_applied = AsyncMock(return_value=None)
        await adapter.apply(3)
        assert mock_spec_registry.get("proc-001") is not None


# ---------------------------------------------------------------------------
# Policy + Contract wiring
# ---------------------------------------------------------------------------


class TestPolicyAndContract:
    def test_policy_procedure_spec_onboarded(self):
        from src.growth.policies import PolicyRegistry
        from src.growth.types import GrowthOnboardingState

        registry = PolicyRegistry()
        policy = registry.get_kind_policy(GrowthObjectKind.procedure_spec)
        assert policy.onboarding_state == GrowthOnboardingState.onboarded
        assert policy.adapter_name == "procedure_spec"

    def test_contract_v1_required_checks(self):
        contract = PROCEDURE_SPEC_EVAL_CONTRACT_V1
        assert contract.contract_id == "procedure_spec_v1"
        assert contract.required_checks == (
            "transition_determinism",
            "guard_completeness",
            "interrupt_resume_safety",
            "checkpoint_recoverability",
            "scope_claim_consistency",
        )

    def test_contract_registry_returns_v1(self):
        from src.growth.contracts import get_contract

        contract = get_contract(GrowthObjectKind.procedure_spec)
        assert contract.contract_id == "procedure_spec_v1"


# ---------------------------------------------------------------------------
# Startup restore
# ---------------------------------------------------------------------------


class TestRestoreActiveProcedureSpecs:
    @pytest.mark.asyncio
    async def test_restore_active_procedure_specs(self):
        from src.gateway.app import _restore_active_procedure_specs

        spec_payload = _make_spec_payload()
        mock_gov_store = AsyncMock()
        mock_gov_store.list_active = AsyncMock(return_value=[spec_payload])
        spec_registry = ProcedureSpecRegistry()

        count = await _restore_active_procedure_specs(mock_gov_store, spec_registry)
        assert count == 1
        assert spec_registry.get("proc-001") is not None

    @pytest.mark.asyncio
    async def test_restore_empty_store(self):
        from src.gateway.app import _restore_active_procedure_specs

        mock_gov_store = AsyncMock()
        mock_gov_store.list_active = AsyncMock(return_value=[])
        spec_registry = ProcedureSpecRegistry()

        count = await _restore_active_procedure_specs(mock_gov_store, spec_registry)
        assert count == 0
        assert spec_registry.list_specs() == []

    @pytest.mark.asyncio
    async def test_restore_bad_payload_skipped(self):
        from src.gateway.app import _restore_active_procedure_specs

        mock_gov_store = AsyncMock()
        mock_gov_store.list_active = AsyncMock(return_value=[{"invalid": True}])
        spec_registry = ProcedureSpecRegistry()

        count = await _restore_active_procedure_specs(mock_gov_store, spec_registry)
        assert count == 0
