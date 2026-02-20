from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum


class ToolGroup(StrEnum):
    code = "code"
    memory = "memory"
    world = "world"


class ToolMode(StrEnum):
    chat_safe = "chat_safe"
    coding = "coding"


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

    @abstractmethod
    async def execute(self, arguments: dict) -> dict:
        """Execute the tool with given arguments and return a result dict."""
        ...
