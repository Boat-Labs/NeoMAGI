from __future__ import annotations

from abc import ABC, abstractmethod


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

    @abstractmethod
    async def execute(self, arguments: dict) -> dict:
        """Execute the tool with given arguments and return a result dict."""
        ...
