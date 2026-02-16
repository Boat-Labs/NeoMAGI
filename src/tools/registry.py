from __future__ import annotations

import logging

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry for agent tools. Provides lookup and OpenAI function calling schema."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool. Raises ValueError if name already registered."""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        logger.info("Registered tool: %s", tool.name)

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name. Returns None if not found."""
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_tools_schema(self) -> list[dict]:
        """Return tools in OpenAI function calling format.

        Output format:
        [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]
