"""Domain types for the growth governance kernel.

Defines the vocabulary layer: object kinds, lifecycle statuses,
proposals, eval results, and policy structures.

GrowthLifecycleStatus MUST stay aligned with
``src.memory.evolution.VALID_STATUSES``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GrowthObjectKind(StrEnum):
    """Enumeration of all recognised growth object kinds.

    P2-M1a: only ``soul`` is onboarded; the rest are reserved.
    """

    soul = "soul"
    skill_spec = "skill_spec"
    wrapper_tool = "wrapper_tool"
    procedure_spec = "procedure_spec"
    memory_application_spec = "memory_application_spec"


class GrowthOnboardingState(StrEnum):
    """Whether a growth object kind has a live adapter."""

    onboarded = "onboarded"
    reserved = "reserved"


class GrowthLifecycleStatus(StrEnum):
    """Lifecycle statuses for governed growth objects.

    Aligned with ``src.memory.evolution.VALID_STATUSES``:
    ``{"active", "proposed", "superseded", "rolled_back", "vetoed"}``.
    """

    proposed = "proposed"
    active = "active"
    superseded = "superseded"
    rolled_back = "rolled_back"
    vetoed = "vetoed"


@dataclass(frozen=True)
class GrowthProposal:
    """A proposal to mutate a governed growth object."""

    object_kind: GrowthObjectKind
    object_id: str
    intent: str
    risk_notes: str
    diff_summary: str
    evidence_refs: list[str] = field(default_factory=list)
    proposed_by: str = "agent"


@dataclass(frozen=True)
class GrowthEvalResult:
    """Result of evaluating a growth proposal."""

    passed: bool
    checks: list[dict] = field(default_factory=list)
    summary: str = ""


@dataclass(frozen=True)
class GrowthKindPolicy:
    """Per-kind governance metadata."""

    kind: GrowthObjectKind
    onboarding_state: GrowthOnboardingState
    requires_explicit_approval: bool
    adapter_name: str | None
    notes: str = ""


@dataclass(frozen=True)
class PromotionPolicy:
    """Cross-kind promotion rule (schema only in P2-M1a)."""

    from_kind: GrowthObjectKind
    to_kind: GrowthObjectKind
    required_evidence: list[str] = field(default_factory=list)
    required_tests: list[str] = field(default_factory=list)
    risk_gate: str = ""
