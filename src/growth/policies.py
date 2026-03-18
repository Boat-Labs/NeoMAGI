"""Policy registry for the growth governance kernel.

Hardcoded policies — composition root style, no config-driven discovery.
"""

from __future__ import annotations

from src.growth.types import (
    GrowthKindPolicy,
    GrowthObjectKind,
    GrowthOnboardingState,
    PromotionPolicy,
)

# ── hardcoded kind policies ──

_KIND_POLICIES: dict[GrowthObjectKind, GrowthKindPolicy] = {
    GrowthObjectKind.soul: GrowthKindPolicy(
        kind=GrowthObjectKind.soul,
        onboarding_state=GrowthOnboardingState.onboarded,
        requires_explicit_approval=False,
        adapter_name="soul",
        notes="SOUL.md lifecycle via EvolutionEngine",
    ),
    GrowthObjectKind.skill_spec: GrowthKindPolicy(
        kind=GrowthObjectKind.skill_spec,
        onboarding_state=GrowthOnboardingState.onboarded,
        requires_explicit_approval=True,
        adapter_name="skill",
        notes="Onboarded in P2-M1b",
    ),
    GrowthObjectKind.wrapper_tool: GrowthKindPolicy(
        kind=GrowthObjectKind.wrapper_tool,
        onboarding_state=GrowthOnboardingState.reserved,
        requires_explicit_approval=True,
        adapter_name=None,
        notes="Reserved for P2-M1b+",
    ),
    GrowthObjectKind.procedure_spec: GrowthKindPolicy(
        kind=GrowthObjectKind.procedure_spec,
        onboarding_state=GrowthOnboardingState.reserved,
        requires_explicit_approval=True,
        adapter_name=None,
        notes="Reserved for P2-M1c+",
    ),
    GrowthObjectKind.memory_application_spec: GrowthKindPolicy(
        kind=GrowthObjectKind.memory_application_spec,
        onboarding_state=GrowthOnboardingState.reserved,
        requires_explicit_approval=True,
        adapter_name=None,
        notes="Tentative; formal definition deferred to P2-M3",
    ),
}

# ── hardcoded promotion policies (schema only, not runtime consumed) ──

_PROMOTION_POLICIES: list[PromotionPolicy] = [
    PromotionPolicy(
        from_kind=GrowthObjectKind.skill_spec,
        to_kind=GrowthObjectKind.wrapper_tool,
        required_evidence=["usage_count >= 3", "success_rate >= 0.8"],
        required_tests=["unit_test_pass", "integration_smoke"],
        risk_gate="low",
    ),
    PromotionPolicy(
        from_kind=GrowthObjectKind.wrapper_tool,
        to_kind=GrowthObjectKind.procedure_spec,
        required_evidence=["usage_count >= 10", "no_regression"],
        required_tests=["unit_test_pass", "e2e_scenario"],
        risk_gate="medium",
    ),
]


class PolicyRegistry:
    """Machine-readable policy store for growth object governance.

    All policies are hardcoded at construction time — no config-driven
    discovery or service locator patterns.
    """

    def __init__(self) -> None:
        self._kind_policies = dict(_KIND_POLICIES)
        self._promotion_policies = list(_PROMOTION_POLICIES)

    def get_kind_policy(self, kind: GrowthObjectKind) -> GrowthKindPolicy:
        """Return the governance policy for *kind*.

        Raises ``KeyError`` if *kind* is not registered (should not happen
        given the exhaustive enum, but fail-closed is better than silent).
        """
        return self._kind_policies[kind]

    def list_kinds(self) -> list[GrowthKindPolicy]:
        """Return all registered kind policies."""
        return list(self._kind_policies.values())

    def get_promotion_policies(self) -> list[PromotionPolicy]:
        """Return all registered promotion policies (schema only in P2-M1a)."""
        return list(self._promotion_policies)
