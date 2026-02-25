"""Tests for AgentLoopRegistry (M6 Phase 1)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.provider_registry import AgentLoopRegistry


def _mock_agent_loop() -> MagicMock:
    return MagicMock(name="AgentLoop")


class TestAgentLoopRegistry:
    def test_register_and_get(self) -> None:
        registry = AgentLoopRegistry(default_provider="openai")
        loop = _mock_agent_loop()
        registry.register("openai", loop, "gpt-5-mini")

        entry = registry.get("openai")
        assert entry.name == "openai"
        assert entry.agent_loop is loop
        assert entry.model == "gpt-5-mini"

    def test_get_default(self) -> None:
        registry = AgentLoopRegistry(default_provider="openai")
        loop = _mock_agent_loop()
        registry.register("openai", loop, "gpt-5-mini")

        entry = registry.get(None)
        assert entry.name == "openai"
        assert entry.agent_loop is loop

    def test_get_unregistered_raises(self) -> None:
        registry = AgentLoopRegistry(default_provider="openai")
        registry.register("openai", _mock_agent_loop(), "gpt-5-mini")

        with pytest.raises(KeyError, match="not registered"):
            registry.get("claude")

    def test_default_not_registered_raises(self) -> None:
        registry = AgentLoopRegistry(default_provider="gemini")
        registry.register("openai", _mock_agent_loop(), "gpt-5-mini")

        with pytest.raises(KeyError, match="not registered"):
            registry.get()

    def test_available_providers(self) -> None:
        registry = AgentLoopRegistry(default_provider="openai")
        registry.register("openai", _mock_agent_loop(), "gpt-5-mini")
        registry.register("gemini", _mock_agent_loop(), "gemini-2.5-flash")

        available = registry.available_providers()
        assert set(available) == {"openai", "gemini"}

    def test_default_name(self) -> None:
        registry = AgentLoopRegistry(default_provider="gemini")
        assert registry.default_name == "gemini"

    def test_multiple_providers(self) -> None:
        registry = AgentLoopRegistry(default_provider="openai")
        openai_loop = _mock_agent_loop()
        gemini_loop = _mock_agent_loop()
        registry.register("openai", openai_loop, "gpt-5-mini")
        registry.register("gemini", gemini_loop, "gemini-2.5-flash")

        assert registry.get("openai").agent_loop is openai_loop
        assert registry.get("gemini").agent_loop is gemini_loop
        assert registry.get(None).agent_loop is openai_loop
