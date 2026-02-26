"""AgentLoopRegistry: per-provider AgentLoop instances.

Created at startup; holds pre-initialized, stateful AgentLoops.
Thread-safe for read (no mutation after init).
Gateway does per-request lookup via get(params.provider) for agent-run level routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.agent import AgentLoop


@dataclass
class ProviderEntry:
    """A registered model provider with its fully initialized AgentLoop."""

    name: str
    agent_loop: AgentLoop
    model: str  # provider default model (for logging/reporting)


class AgentLoopRegistry:
    """Registry of per-provider AgentLoop instances.

    Created at startup; holds pre-initialized, stateful AgentLoops.
    Thread-safe for read (no mutation after init).
    Gateway does per-request lookup via get(params.provider) for agent-run level routing.
    """

    def __init__(self, default_provider: str) -> None:
        self._providers: dict[str, ProviderEntry] = {}
        self._default = default_provider

    def register(self, name: str, agent_loop: AgentLoop, model: str) -> None:
        self._providers[name] = ProviderEntry(
            name=name, agent_loop=agent_loop, model=model,
        )

    def get(self, name: str | None = None) -> ProviderEntry:
        """Get provider by name, or default if None.

        Raises KeyError if not found or not configured.
        """
        key = name or self._default
        if key not in self._providers:
            msg = f"Provider '{key}' not registered or not configured"
            raise KeyError(msg)
        return self._providers[key]

    @property
    def default_name(self) -> str:
        return self._default

    def available_providers(self) -> list[str]:
        return list(self._providers.keys())
