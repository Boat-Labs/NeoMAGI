from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tools.context import ToolContext


class ToolGroup(StrEnum):
    code = "code"
    memory = "memory"
    world = "world"


class ToolMode(StrEnum):
    chat_safe = "chat_safe"
    coding = "coding"


class RiskLevel(StrEnum):
    """Tool-level risk classification for guardrail gating (ADR 0035).

    Guard only checks risk_level, NOT ToolGroup.
    ToolGroup retains its original role as domain classification.
    Undeclared tools default to 'high' (fail-closed).
    """

    low = "low"
    high = "high"


class BaseTool(ABC):
    """Abstract base class for agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name used in function calling."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what the tool does."""
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict:
        """JSON Schema describing the tool's input parameters."""
        ...

    @property
    def group(self) -> ToolGroup:
        """Tool group classification. Conservative default: code."""
        return ToolGroup.code

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        """Modes in which this tool is available. Fail-closed: empty by default."""
        return frozenset()

    @property
    def risk_level(self) -> RiskLevel:
        """Risk classification for guardrail gating (ADR 0035).

        Fail-closed default: high. Tools that are read-only or have no
        external side effects should explicitly declare low.
        """
        return RiskLevel.high

    @abstractmethod
    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        """Execute the tool with given arguments and optional runtime context.

        context is injected by AgentLoop with scope_key and session_id.
        """
        ...
