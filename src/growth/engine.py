"""Growth governance engine: unified lifecycle orchestration.

Delegates all object-specific logic to :class:`GovernedObjectAdapter`
implementations.  Enforces fail-closed semantics for un-onboarded or
adapter-less kinds.
"""

from __future__ import annotations

import structlog

from src.growth.adapters.base import GovernedObjectAdapter, UnsupportedGrowthObjectError
from src.growth.policies import PolicyRegistry
from src.growth.types import (
    GrowthEvalResult,
    GrowthKindPolicy,
    GrowthObjectKind,
    GrowthOnboardingState,
    GrowthProposal,
)

logger = structlog.get_logger()


class GrowthGovernanceEngine:
    """Orchestrates governed lifecycle operations across object kinds.

    Construction injection only — no config-driven registry, service
    locator, or auto-discovery.

    Parameters
    ----------
    adapters:
        Mapping from :class:`GrowthObjectKind` to its adapter.
    policy_registry:
        The :class:`PolicyRegistry` to consult for kind policies.
    """

    def __init__(
        self,
        adapters: dict[GrowthObjectKind, GovernedObjectAdapter],
        policy_registry: PolicyRegistry,
    ) -> None:
        self._adapters = adapters
        self._policy_registry = policy_registry

    # ── public API ──

    async def propose(self, kind: GrowthObjectKind, proposal: GrowthProposal) -> int:
        """Create a governed proposal.  Returns the proposal version number."""
        adapter = self._require_adapter(kind)
        version = await adapter.propose(proposal)
        logger.info("growth_proposed", kind=kind, version=version, intent=proposal.intent[:80])
        return version

    async def evaluate(self, kind: GrowthObjectKind, version: int) -> GrowthEvalResult:
        """Evaluate a proposal.  Returns the evaluation result."""
        adapter = self._require_adapter(kind)
        result = await adapter.evaluate(version)
        logger.info("growth_evaluated", kind=kind, version=version, passed=result.passed)
        return result

    async def apply(self, kind: GrowthObjectKind, version: int) -> None:
        """Apply a proposal that passed evaluation."""
        adapter = self._require_adapter(kind)
        await adapter.apply(version)
        logger.info("growth_applied", kind=kind, version=version)

    async def rollback(self, kind: GrowthObjectKind, **kwargs: object) -> int:
        """Rollback to a previous version.  Returns the new active version."""
        adapter = self._require_adapter(kind)
        new_version = await adapter.rollback(**kwargs)
        logger.info("growth_rolled_back", kind=kind, new_version=new_version)
        return new_version

    async def veto(self, kind: GrowthObjectKind, version: int) -> None:
        """Veto a proposed or active version."""
        adapter = self._require_adapter(kind)
        await adapter.veto(version)
        logger.info("growth_vetoed", kind=kind, version=version)

    async def get_active(self, kind: GrowthObjectKind) -> object | None:
        """Return the currently active object for *kind*, or ``None``."""
        adapter = self._require_adapter(kind)
        return await adapter.get_active()

    def list_supported_kinds(self) -> list[GrowthKindPolicy]:
        """Return all registered kind policies (onboarded + reserved)."""
        return self._policy_registry.list_kinds()

    # ── internal helpers ──

    def _require_adapter(self, kind: GrowthObjectKind) -> GovernedObjectAdapter:
        """Fail-closed guard: kind must be onboarded AND have an adapter."""
        policy = self._policy_registry.get_kind_policy(kind)

        if policy.onboarding_state != GrowthOnboardingState.onboarded:
            raise UnsupportedGrowthObjectError(
                f"Growth object kind '{kind}' is '{policy.onboarding_state}', not onboarded"
            )

        adapter = self._adapters.get(kind)
        if adapter is None:
            raise UnsupportedGrowthObjectError(
                f"No adapter registered for onboarded kind '{kind}'"
            )

        return adapter
