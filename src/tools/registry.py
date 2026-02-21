from __future__ import annotations

import structlog

from src.tools.base import BaseTool, ToolMode

logger = structlog.get_logger()


class ToolRegistry:
    """Registry for agent tools. Provides lookup and mode-aware filtering."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._mode_overrides: dict[str, frozenset[ToolMode]] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool. Raises ValueError if name already registered."""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        if not tool.allowed_modes:
            logger.warning(
                "tool_registered_without_modes",
                tool_name=tool.name,
                msg="Tool has empty allowed_modes (fail-closed default); "
                "it will not be available in any mode.",
            )
        self._tools[tool.name] = tool
        logger.info("tool_registered", tool_name=tool.name)

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name. Returns None if not found."""
        return self._tools.get(name)

    def set_mode_override(self, tool_name: str, modes: frozenset[ToolMode]) -> None:
        """Override effective modes for a tool. Can only restrict, not expand.

        Raises ValueError if modes includes values outside the tool's allowed_modes.
        Raises KeyError if tool is not registered.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Tool not registered: {tool_name}")
        extra = modes - tool.allowed_modes
        if extra:
            raise ValueError(
                f"Cannot expand modes for '{tool_name}': "
                f"{extra} not in allowed_modes {tool.allowed_modes}"
            )
        self._mode_overrides[tool_name] = modes

    def get_effective_modes(self, tool_name: str) -> frozenset[ToolMode]:
        """Return effective modes: allowed_modes intersected with any override."""
        tool = self._tools.get(tool_name)
        if tool is None:
            return frozenset()
        override = self._mode_overrides.get(tool_name)
        if override is None:
            return tool.allowed_modes
        return tool.allowed_modes & override

    def check_mode(self, tool_name: str, mode: ToolMode) -> bool:
        """Check if a tool is available in the given mode. False for unknown tools."""
        return mode in self.get_effective_modes(tool_name)

    def list_tools(self, mode: ToolMode) -> list[BaseTool]:
        """Return tools available in the given mode."""
        return [
            tool for tool in self._tools.values()
            if mode in self.get_effective_modes(tool.name)
        ]

    def get_tools_schema(self, mode: ToolMode) -> list[dict]:
        """Return tools in OpenAI function calling format, filtered by mode.

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
            for tool in self.list_tools(mode)
        ]
