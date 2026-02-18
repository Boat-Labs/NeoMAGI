"""Tests for empty choices guard in OpenAICompatModelClient.

Covers: chat() and chat_completion() raise LLMError on empty choices,
        chat_stream() safely skips empty chunk choices.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.model_client import OpenAICompatModelClient
from src.infra.errors import LLMError


@pytest.fixture()
def client():
    return OpenAICompatModelClient(api_key="test-key", max_retries=0)


def _make_response(*, choices=None):
    """Build a mock completion response."""
    resp = MagicMock()
    resp.choices = choices if choices is not None else []
    return resp


def _make_choice(content="hello"):
    """Build a mock choice with message."""
    choice = MagicMock()
    choice.message.content = content
    choice.message.tool_calls = None
    return choice


class TestChatEmptyChoices:
    @pytest.mark.asyncio()
    async def test_empty_choices_raises_llm_error(self, client):
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_response(choices=[])
        )
        with pytest.raises(LLMError, match="Empty choices"):
            await client.chat([{"role": "user", "content": "hi"}], "test-model")

    @pytest.mark.asyncio()
    async def test_normal_choices_returns_content(self, client):
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_response(choices=[_make_choice("world")])
        )
        result = await client.chat([{"role": "user", "content": "hi"}], "test-model")
        assert result == "world"


class TestChatCompletionEmptyChoices:
    @pytest.mark.asyncio()
    async def test_empty_choices_raises_llm_error(self, client):
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_response(choices=[])
        )
        with pytest.raises(LLMError, match="Empty choices"):
            await client.chat_completion(
                [{"role": "user", "content": "hi"}], "test-model"
            )

    @pytest.mark.asyncio()
    async def test_normal_choices_returns_message(self, client):
        choice = _make_choice("response text")
        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(
            return_value=_make_response(choices=[choice])
        )
        message = await client.chat_completion(
            [{"role": "user", "content": "hi"}], "test-model"
        )
        assert message.content == "response text"


class TestChatStreamEmptyChunkChoices:
    @pytest.mark.asyncio()
    async def test_empty_chunk_choices_skipped(self, client):
        """Stream chunks with empty choices should be silently skipped."""
        empty_chunk = MagicMock()
        empty_chunk.choices = []

        normal_chunk = MagicMock()
        normal_chunk.choices = [MagicMock()]
        normal_chunk.choices[0].delta.content = "token"

        async def mock_stream():
            yield empty_chunk
            yield normal_chunk

        client._client = MagicMock()
        client._client.chat.completions.create = AsyncMock(return_value=mock_stream())

        tokens = []
        async for t in client.chat_stream(
            [{"role": "user", "content": "hi"}], "test-model"
        ):
            tokens.append(t)

        assert tokens == ["token"]
