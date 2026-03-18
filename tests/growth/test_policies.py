"""Tests for PolicyRegistry (growth governance kernel).

Covers: list_kinds, get_kind_policy for all 5 kinds, get_promotion_policies,
promotion policy schema-level validation, invalid kind handling.
"""

from __future__ import annotations

import pytest

from src.growth.policies import PolicyRegistry
from src.growth.types import (
    GrowthKindPolicy,
    GrowthObjectKind,
    GrowthOnboardingState,
    PromotionPolicy,
)


@pytest.fixture()
def registry() -> PolicyRegistry:
    return PolicyRegistry()


class TestListKinds:
    def test_returns_five_kinds(self, registry: PolicyRegistry) -> None:
        kinds = registry.list_kinds()
        assert len(kinds) == 5
        assert all(isinstance(k, GrowthKindPolicy) for k in kinds)

    def test_all_enum_members_covered(self, registry: PolicyRegistry) -> None:
        """Every GrowthObjectKind enum member has a registered policy."""
        registered = {p.kind for p in registry.list_kinds()}
        expected = set(GrowthObjectKind)
        assert registered == expected


class TestGetKindPolicySoul:
    def test_soul_is_onboarded(self, registry: PolicyRegistry) -> None:
        policy = registry.get_kind_policy(GrowthObjectKind.soul)
        assert policy.onboarding_state == GrowthOnboardingState.onboarded

    def test_soul_adapter_name(self, registry: PolicyRegistry) -> None:
        policy = registry.get_kind_policy(GrowthObjectKind.soul)
        assert policy.adapter_name == "soul"

    def test_soul_no_explicit_approval(self, registry: PolicyRegistry) -> None:
        policy = registry.get_kind_policy(GrowthObjectKind.soul)
        assert policy.requires_explicit_approval is False


class TestGetKindPolicySkillSpec:
    """skill_spec is onboarded as of P2-M1b."""

    def test_skill_spec_is_onboarded(self, registry: PolicyRegistry) -> None:
        policy = registry.get_kind_policy(GrowthObjectKind.skill_spec)
        assert policy.onboarding_state == GrowthOnboardingState.onboarded

    def test_skill_spec_adapter_name(self, registry: PolicyRegistry) -> None:
        policy = registry.get_kind_policy(GrowthObjectKind.skill_spec)
        assert policy.adapter_name == "skill"

    def test_skill_spec_requires_approval(self, registry: PolicyRegistry) -> None:
        policy = registry.get_kind_policy(GrowthObjectKind.skill_spec)
        assert policy.requires_explicit_approval is True


class TestGetKindPolicyReserved:
    _RESERVED_KINDS = [
        GrowthObjectKind.wrapper_tool,
        GrowthObjectKind.procedure_spec,
        GrowthObjectKind.memory_application_spec,
    ]

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    def test_reserved_state(self, registry: PolicyRegistry, kind: GrowthObjectKind) -> None:
        policy = registry.get_kind_policy(kind)
        assert policy.onboarding_state == GrowthOnboardingState.reserved

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    def test_reserved_adapter_none(self, registry: PolicyRegistry, kind: GrowthObjectKind) -> None:
        policy = registry.get_kind_policy(kind)
        assert policy.adapter_name is None

    @pytest.mark.parametrize("kind", _RESERVED_KINDS)
    def test_reserved_requires_approval(
        self, registry: PolicyRegistry, kind: GrowthObjectKind
    ) -> None:
        policy = registry.get_kind_policy(kind)
        assert policy.requires_explicit_approval is True


class TestGetKindPolicyInvalid:
    def test_unknown_kind_raises_key_error(self, registry: PolicyRegistry) -> None:
        with pytest.raises(KeyError):
            registry.get_kind_policy("nonexistent_kind")  # type: ignore[arg-type]


class TestPromotionPolicies:
    def test_returns_list(self, registry: PolicyRegistry) -> None:
        policies = registry.get_promotion_policies()
        assert isinstance(policies, list)
        assert len(policies) == 2

    def test_all_promotion_policy_type(self, registry: PolicyRegistry) -> None:
        for p in registry.get_promotion_policies():
            assert isinstance(p, PromotionPolicy)

    def test_promotion_policy_fields(self, registry: PolicyRegistry) -> None:
        """Promotion policies have expected field types (schema-level)."""
        for p in registry.get_promotion_policies():
            assert isinstance(p.from_kind, GrowthObjectKind)
            assert isinstance(p.to_kind, GrowthObjectKind)
            assert isinstance(p.required_evidence, list)
            assert isinstance(p.required_tests, list)
            assert isinstance(p.risk_gate, str)

    def test_promotion_policy_serialises(self, registry: PolicyRegistry) -> None:
        """PromotionPolicy dataclass can be round-tripped via __dict__-like access."""
        for p in registry.get_promotion_policies():
            # dataclasses.asdict equivalent: all fields are basic types
            assert p.from_kind in GrowthObjectKind
            assert p.to_kind in GrowthObjectKind
            assert all(isinstance(e, str) for e in p.required_evidence)
            assert all(isinstance(t, str) for t in p.required_tests)

    def test_returns_copy(self, registry: PolicyRegistry) -> None:
        """Mutating returned list does not affect internal state."""
        policies = registry.get_promotion_policies()
        policies.clear()
        assert len(registry.get_promotion_policies()) == 2
