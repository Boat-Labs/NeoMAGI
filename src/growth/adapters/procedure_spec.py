"""Procedure spec governed-object adapter: connects procedure_spec to the growth kernel.

Implements :class:`GovernedObjectAdapter` protocol for
:attr:`GrowthObjectKind.procedure_spec`.

Pins ``PROCEDURE_SPEC_EVAL_CONTRACT_V1`` before every evaluation (ADR 0054 §1a).
All 5 eval checks are deterministic — no LLM calls, no network access.
``apply()`` and ``rollback()`` use compensating semantics: DB writes first,
then ProcedureSpecRegistry mutation; if registry mutation fails, DB is rolled back.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from src.growth.contracts import PROCEDURE_SPEC_EVAL_CONTRACT_V1
from src.growth.types import (
    GrowthEvalResult,
    GrowthLifecycleStatus,
    GrowthObjectKind,
    GrowthProposal,
)
from src.procedures.types import RESERVED_ACTION_IDS, ProcedureSpec, _validate_function_name

if TYPE_CHECKING:
    from src.procedures.governance_store import ProcedureSpecGovernanceStore
    from src.procedures.registry import (
        ProcedureContextRegistry,
        ProcedureGuardRegistry,
        ProcedureSpecRegistry,
    )
    from src.procedures.store import ProcedureStore
    from src.tools.registry import ToolRegistry

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Eval check helpers
# ---------------------------------------------------------------------------


def _check_transition_determinism(spec: ProcedureSpec) -> dict:
    """Check 1: deterministic transitions.

    - initial_state exists in states
    - every action.to target exists in states
    - each state+action pair has exactly one target (guaranteed by dict keys)
    - entry_policy must be 'explicit'
    """
    errors: list[str] = []
    if spec.initial_state not in spec.states:
        errors.append(f"initial_state '{spec.initial_state}' not in states")

    if spec.entry_policy != "explicit":
        errors.append(f"entry_policy must be 'explicit', got '{spec.entry_policy}'")

    for state_name, state_spec in spec.states.items():
        for action_id, action in state_spec.actions.items():
            if action.to not in spec.states:
                errors.append(
                    f"state '{state_name}', action '{action_id}': "
                    f"target '{action.to}' not in states"
                )
    passed = len(errors) == 0
    return {
        "name": "transition_determinism",
        "passed": passed,
        "detail": "Transitions deterministic" if passed else "; ".join(errors),
    }


def _check_guard_completeness(
    spec: ProcedureSpec,
    guard_registry: ProcedureGuardRegistry,
) -> dict:
    """Check 2: all referenced guards are resolvable.

    - enter_guard (if set) must resolve in guard_registry
    - every action.guard (if set) must resolve in guard_registry
    """
    errors: list[str] = []
    if spec.enter_guard and guard_registry.resolve(spec.enter_guard) is None:
        errors.append(f"enter_guard '{spec.enter_guard}' not found in guard registry")

    for state_name, state_spec in spec.states.items():
        for action_id, action in state_spec.actions.items():
            if action.guard and guard_registry.resolve(action.guard) is None:
                errors.append(
                    f"state '{state_name}', action '{action_id}': "
                    f"guard '{action.guard}' not found in guard registry"
                )
    passed = len(errors) == 0
    return {
        "name": "guard_completeness",
        "passed": passed,
        "detail": "Guards complete" if passed else "; ".join(errors),
    }


def _check_interrupt_resume_safety(spec: ProcedureSpec) -> dict:
    """Check 3: state machine has proper terminal and non-terminal states.

    - At least one terminal state (actions is empty)
    - All non-terminal states have at least one action
    """
    errors: list[str] = []
    terminal_count = 0
    for state_name, state_spec in spec.states.items():
        if not state_spec.actions:
            terminal_count += 1
        # Non-terminal states always have actions (dict non-empty) — no extra check needed

    if terminal_count == 0:
        errors.append("No terminal state found (at least one state must have no actions)")
    passed = len(errors) == 0
    return {
        "name": "interrupt_resume_safety",
        "passed": passed,
        "detail": "Interrupt/resume safe" if passed else "; ".join(errors),
    }


def _collect_ambient_tool_names(tool_registry: ToolRegistry) -> set[str]:
    """Collect all registered ambient tool names for collision detection."""
    from src.tools.base import ToolMode as _ToolMode

    names: set[str] = set()
    for mode in _ToolMode:
        for tool in tool_registry.list_tools(mode):
            names.add(tool.name)
    return names


def _check_checkpoint_recoverability(
    spec: ProcedureSpec,
    tool_registry: ToolRegistry,
) -> dict:
    """Check 4: every action references a valid tool and has a valid action_id.

    - action.tool exists in ToolRegistry
    - action_id satisfies OpenAI function name constraint
    - action_id does not collide with RESERVED_ACTION_IDS
    - action_id does not collide with ambient tool names
    """
    errors: list[str] = []
    ambient_names = _collect_ambient_tool_names(tool_registry)

    for state_name, state_spec in spec.states.items():
        for action_id, action in state_spec.actions.items():
            prefix = f"state '{state_name}', action '{action_id}'"
            # Tool exists
            if tool_registry.get(action.tool) is None:
                errors.append(f"{prefix}: tool '{action.tool}' not found in tool registry")
            # Function name constraint
            fn_error = _validate_function_name(action_id)
            if fn_error:
                errors.append(f"{prefix}: {fn_error}")
            # Reserved name collision
            if action_id in RESERVED_ACTION_IDS:
                errors.append(f"{prefix}: collides with reserved name")
            # Ambient tool name collision
            if action_id in ambient_names:
                errors.append(
                    f"{prefix}: action id collides with registered ambient tool name"
                )
    passed = len(errors) == 0
    return {
        "name": "checkpoint_recoverability",
        "passed": passed,
        "detail": "Checkpoint recoverable" if passed else "; ".join(errors),
    }


def _check_scope_claim_consistency(
    spec: ProcedureSpec,
    context_registry: ProcedureContextRegistry,
) -> dict:
    """Check 5: context_model resolvable and allowed_modes non-empty.

    - context_model resolvable in ProcedureContextRegistry
    - allowed_modes is non-empty
    """
    errors: list[str] = []
    if context_registry.resolve(spec.context_model) is None:
        errors.append(
            f"context_model '{spec.context_model}' not found in context registry"
        )
    if not spec.allowed_modes:
        errors.append("allowed_modes is empty")
    passed = len(errors) == 0
    return {
        "name": "scope_claim_consistency",
        "passed": passed,
        "detail": "Scope claim consistent" if passed else "; ".join(errors),
    }


# ---------------------------------------------------------------------------
# ProcedureSpecGovernedObjectAdapter
# ---------------------------------------------------------------------------


class ProcedureSpecGovernedObjectAdapter:
    """Adapter connecting procedure_spec to the growth governance kernel.

    Implements :class:`GovernedObjectAdapter` protocol for
    :attr:`GrowthObjectKind.procedure_spec`.
    """

    def __init__(
        self,
        governance_store: ProcedureSpecGovernanceStore,
        spec_registry: ProcedureSpecRegistry,
        tool_registry: ToolRegistry,
        context_registry: ProcedureContextRegistry,
        guard_registry: ProcedureGuardRegistry,
        procedure_store: ProcedureStore,
    ) -> None:
        self._store = governance_store
        self._spec_registry = spec_registry
        self._tool_registry = tool_registry
        self._context_registry = context_registry
        self._guard_registry = guard_registry
        self._procedure_store = procedure_store
        self._contract = PROCEDURE_SPEC_EVAL_CONTRACT_V1

    @property
    def kind(self) -> GrowthObjectKind:
        return GrowthObjectKind.procedure_spec

    async def propose(self, proposal: GrowthProposal) -> int:
        """Create a governance proposal from payload.

        Requires ``proposal.payload["procedure_spec"]`` (dict).
        Returns the governance_version.
        """
        raw_spec = proposal.payload.get("procedure_spec")
        if not isinstance(raw_spec, dict):
            raise ValueError(
                "ProcedureSpecGovernedObjectAdapter.propose() requires "
                'proposal.payload["procedure_spec"] to be a dict'
            )
        # Validate that it parses as a domain object (fail fast)
        spec = ProcedureSpec.model_validate(raw_spec)

        # [P1] object_id must match spec.id to prevent ledger/store key divergence
        if proposal.object_id != spec.id:
            raise ValueError(
                f"proposal.object_id={proposal.object_id!r} does not match "
                f"spec.id={spec.id!r}; they must be identical"
            )

        # Normalize payload to JSON-safe form before storing (frozenset, ToolMode, etc.)
        normalized_payload = dict(proposal.payload)
        normalized_payload["procedure_spec"] = spec.model_dump(mode="json")
        proposal = GrowthProposal(
            object_kind=proposal.object_kind,
            object_id=proposal.object_id,
            intent=proposal.intent,
            risk_notes=proposal.risk_notes,
            diff_summary=proposal.diff_summary,
            evidence_refs=proposal.evidence_refs,
            payload=normalized_payload,
            proposed_by=proposal.proposed_by,
        )

        gv = await self._store.create_proposal(proposal)
        logger.info(
            "procedure_spec_adapter_proposed",
            governance_version=gv,
            procedure_spec_id=proposal.object_id,
        )
        return gv

    async def evaluate(self, version: int) -> GrowthEvalResult:
        """Run 5 deterministic eval checks against a proposed procedure spec.

        Pins ``PROCEDURE_SPEC_EVAL_CONTRACT_V1`` (ADR 0054 §1a).
        """
        contract = self._contract
        record = await self._store.get_proposal(version)

        # Early exit: missing or not proposed
        if record is None:
            return GrowthEvalResult(
                passed=False,
                summary=f"Governance version {version} not found",
                contract_id=contract.contract_id,
                contract_version=contract.version,
            )
        if record.status != GrowthLifecycleStatus.proposed:
            return GrowthEvalResult(
                passed=False,
                summary=f"Version {version} is '{record.status}', not 'proposed'",
                contract_id=contract.contract_id,
                contract_version=contract.version,
            )

        # Parse ProcedureSpec from payload
        payload = record.proposal.get("payload", {})
        try:
            spec = ProcedureSpec.model_validate(payload.get("procedure_spec", {}))
        except Exception as exc:
            return GrowthEvalResult(
                passed=False,
                checks=[
                    {
                        "name": "transition_determinism",
                        "passed": False,
                        "detail": f"Payload parse error: {exc}",
                    }
                ],
                summary=f"Payload parse error: {exc}",
                contract_id=contract.contract_id,
                contract_version=contract.version,
            )

        # Run 5 deterministic checks
        checks = [
            _check_transition_determinism(spec),
            _check_guard_completeness(spec, self._guard_registry),
            _check_interrupt_resume_safety(spec),
            _check_checkpoint_recoverability(spec, self._tool_registry),
            _check_scope_claim_consistency(spec, self._context_registry),
        ]
        passed = all(c["passed"] for c in checks)
        summary = (
            "All checks passed"
            if passed
            else "Failed: " + ", ".join(c["name"] for c in checks if not c["passed"])
        )
        result = GrowthEvalResult(
            passed=passed,
            checks=checks,
            summary=summary,
            contract_id=contract.contract_id,
            contract_version=contract.version,
        )
        await self._store.store_eval_result(version, result)
        logger.info(
            "procedure_spec_adapter_evaluated",
            governance_version=version,
            passed=result.passed,
        )
        return result

    async def apply(self, version: int) -> None:
        """Materialize a passed proposal to procedure_spec_definitions + ProcedureSpecRegistry.

        P2-M2c does not support in-place upgrade; rollback/disable first.
        DB writes commit first, then registry mutation with compensation.
        """
        spec = await self._validate_apply_preconditions(version)

        # Step 1: DB writes (atomic)
        spec_payload = spec.model_dump(mode="json")
        async with self._store.transaction() as session:
            await self._store.upsert_active(spec_payload, session=session)
            await self._store.update_proposal_status(
                version,
                GrowthLifecycleStatus.active,
                applied_at=datetime.now(UTC),
                session=session,
            )

        # Step 2: registry mutation (after DB commit)
        try:
            self._spec_registry.register(spec)
        except Exception:
            logger.error(
                "procedure_spec_registry_failed_compensating",
                governance_version=version,
                procedure_spec_id=spec.id,
            )
            await self._compensate_failed_apply(version, spec)
            raise

        logger.info(
            "procedure_spec_adapter_applied",
            governance_version=version,
            procedure_spec_id=spec.id,
        )

    async def _validate_apply_preconditions(self, version: int) -> ProcedureSpec:
        """Validate preconditions for apply and return parsed ProcedureSpec."""
        record = await self._store.get_proposal(version)
        if record is None:
            raise ValueError(f"Governance version {version} not found")
        if record.status != GrowthLifecycleStatus.proposed:
            raise ValueError(f"Cannot apply version {version}: status is '{record.status}'")
        if not record.eval_result or not record.eval_result.get("passed"):
            raise ValueError(f"Cannot apply version {version}: eval not passed")

        payload = record.proposal.get("payload", {})
        spec = ProcedureSpec.model_validate(payload["procedure_spec"])

        # [P1] fail-closed: ledger key must match payload spec id
        if record.procedure_spec_id != spec.id:
            raise ValueError(
                f"Ledger procedure_spec_id={record.procedure_spec_id!r} does not match "
                f"payload spec.id={spec.id!r}; data integrity violation"
            )

        # No in-place upgrade
        existing = await self._store.find_last_applied(spec.id)
        if existing is not None:
            raise ValueError(
                f"Cannot apply version {version}: procedure_spec '{spec.id}' already has "
                f"active version {existing.governance_version}; "
                "in-place upgrade not supported in P2-M2c — rollback/disable first"
            )

        # Active instance check
        has_active = await self._procedure_store.has_active_for_spec(spec.id)
        if has_active:
            raise ValueError(
                f"Cannot apply version {version}: procedure_spec '{spec.id}' has "
                "active procedure instance(s) running"
            )

        return spec

    async def _compensate_failed_apply(self, version: int, spec: ProcedureSpec) -> None:
        """Undo DB writes when registry registration fails after DB commit."""
        try:
            async with self._store.transaction() as session:
                await self._store.disable(spec.id, session=session)
                await self._store.update_proposal_status(
                    version,
                    GrowthLifecycleStatus.proposed,
                    session=session,
                )
        except Exception:
            logger.exception(
                "procedure_spec_compensate_failed",
                governance_version=version,
                procedure_spec_id=spec.id,
            )

    async def rollback(self, **kwargs: object) -> int:
        """Rollback: disable current active spec and unregister.

        Returns the governance_version of the rollback ledger entry.
        Rollback = disable-only (no restore of previous version).
        """
        procedure_spec_id = kwargs.get("procedure_spec_id")
        if not isinstance(procedure_spec_id, str):
            raise ValueError("rollback() requires procedure_spec_id as keyword argument")

        # Active instance check
        has_active = await self._procedure_store.has_active_for_spec(procedure_spec_id)
        if has_active:
            raise ValueError(
                f"Cannot rollback procedure_spec '{procedure_spec_id}': "
                "active procedure instance(s) running"
            )

        current = await self._store.find_last_applied(procedure_spec_id)
        if current is None:
            raise ValueError(
                f"Cannot rollback procedure_spec '{procedure_spec_id}': no applied version found"
            )

        # Step 1: DB writes (atomic)
        async with self._store.transaction() as session:
            await self._store.disable(procedure_spec_id, session=session)
            await self._store.update_proposal_status(
                current.governance_version,
                GrowthLifecycleStatus.rolled_back,
                session=session,
            )
            gv = await self._create_rollback_entry(current, procedure_spec_id, session)

        # Step 2: registry cleanup (after DB commit)
        self._unregister_spec(procedure_spec_id)

        logger.info(
            "procedure_spec_adapter_rolled_back",
            governance_version=gv,
            procedure_spec_id=procedure_spec_id,
        )
        return gv

    def _unregister_spec(self, procedure_spec_id: str) -> None:
        """Best-effort unregister from ProcedureSpecRegistry."""
        try:
            self._spec_registry.unregister(procedure_spec_id)
        except KeyError:
            logger.debug(
                "procedure_spec_unregister_skip",
                procedure_spec_id=procedure_spec_id,
                reason="not in registry",
            )

    async def _create_rollback_entry(
        self, last_applied, procedure_spec_id: str, session
    ) -> int:
        """Create a rollback ledger entry and return its governance_version."""
        rollback_proposal = GrowthProposal(
            object_kind=GrowthObjectKind.procedure_spec,
            object_id=procedure_spec_id,
            intent="rollback",
            risk_notes="System rollback",
            diff_summary="Rollback to previous version or disable",
            proposed_by="system",
        )
        gv = await self._store.create_proposal(rollback_proposal, session=session)
        await self._store.update_proposal_status(
            gv,
            GrowthLifecycleStatus.rolled_back,
            rolled_back_from=last_applied.governance_version,
            session=session,
        )
        return gv

    async def veto(self, version: int) -> None:
        """Veto a specific governance version.

        - ``proposed`` -> mark vetoed (no side effects).
        - ``active`` -> delegate to rollback.
        - Any other status -> rejects.
        """
        record = await self._store.get_proposal(version)
        if record is None:
            raise ValueError(f"Governance version {version} not found")

        if record.status == GrowthLifecycleStatus.proposed:
            await self._store.update_proposal_status(version, GrowthLifecycleStatus.vetoed)
            logger.info(
                "procedure_spec_adapter_vetoed",
                governance_version=version,
                was_status="proposed",
            )
        elif record.status == GrowthLifecycleStatus.active:
            current = await self._store.find_last_applied(record.procedure_spec_id)
            if current is None or current.governance_version != version:
                raise ValueError(
                    f"Cannot veto version {version}: it is marked active but is "
                    f"not the current active version for '{record.procedure_spec_id}'"
                )
            await self.rollback(procedure_spec_id=record.procedure_spec_id)
            logger.info(
                "procedure_spec_adapter_vetoed",
                governance_version=version,
                was_status="active",
            )
        else:
            raise ValueError(f"Cannot veto version {version}: status is '{record.status}'")

    async def get_active(self) -> list[ProcedureSpec]:
        """Return all active (non-disabled) procedure specs. Collection semantics."""
        return self._spec_registry.list_specs()
