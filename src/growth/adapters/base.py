"""Base adapter contract for governed growth objects.

Every onboarded growth object kind must provide an adapter implementing
:class:`GovernedObjectAdapter`.  The adapter owns the object-specific
storage and lifecycle logic; the governance engine only orchestrates.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.growth.types import GrowthEvalResult, GrowthObjectKind, GrowthProposal
from src.infra.errors import NeoMAGIError


class UnsupportedGrowthObjectError(NeoMAGIError):
    """Raised when a governance operation targets an un-onboarded or adapter-less kind."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="UNSUPPORTED_GROWTH_OBJECT")


@runtime_checkable
class GovernedObjectAdapter(Protocol):
    """Protocol that every onboarded growth-object adapter must satisfy."""

    @property
    def kind(self) -> GrowthObjectKind: ...

    async def propose(self, proposal: GrowthProposal) -> int: ...

    async def evaluate(self, version: int) -> GrowthEvalResult: ...

    async def apply(self, version: int) -> None: ...

    async def rollback(self, **kwargs: object) -> int: ...

    async def veto(self, version: int) -> None: ...

    async def get_active(self) -> object | None: ...
