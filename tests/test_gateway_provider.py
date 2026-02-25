"""Tests for ChatSendParams.provider routing (M6 Phase 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.gateway.protocol import ChatSendParams


class TestChatSendParamsProvider:
    def test_provider_none_default(self) -> None:
        params = ChatSendParams(content="hi")
        assert params.provider is None

    def test_provider_explicit_openai(self) -> None:
        params = ChatSendParams(content="hi", provider="openai")
        assert params.provider == "openai"

    def test_provider_explicit_gemini(self) -> None:
        params = ChatSendParams(content="hi", provider="gemini")
        assert params.provider == "gemini"

    def test_provider_normalize_uppercase(self) -> None:
        params = ChatSendParams(content="hi", provider="Gemini")
        assert params.provider == "gemini"

    def test_provider_normalize_mixed_case(self) -> None:
        params = ChatSendParams(content="hi", provider="  OpenAI  ")
        assert params.provider == "openai"

    def test_provider_empty_string_becomes_none(self) -> None:
        params = ChatSendParams(content="hi", provider="")
        assert params.provider is None

    def test_provider_whitespace_becomes_none(self) -> None:
        params = ChatSendParams(content="hi", provider="   ")
        assert params.provider is None

    def test_provider_non_string_raises(self) -> None:
        with pytest.raises(ValidationError, match="provider must be a string"):
            ChatSendParams(content="hi", provider=123)

    def test_provider_omitted_backward_compat(self) -> None:
        """Omitting provider entirely is backward compatible."""
        params = ChatSendParams.model_validate({"content": "hi"})
        assert params.provider is None
        assert params.session_id == "main"

    def test_provider_unknown_passes_validation(self) -> None:
        """ChatSendParams only normalizes; registry does availability check."""
        params = ChatSendParams(content="hi", provider="unknown")
        assert params.provider == "unknown"
