"""Tests for P3-M1 Slice B: AnthropicModelClient + ModelMessage.

Covers:
- Message format conversion (system extraction, tool_use/tool_result mapping)
- ModelMessage from OpenAI and Anthropic responses
- Tool definition conversion
- Streaming event normalization (ContentDelta, ToolCallsComplete)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.model_client import (
    AnthropicModelClient,
    ContentDelta,
    ModelMessage,
    ToolCallsComplete,
    _anthropic_response_to_model_message,
    _convert_messages_for_anthropic,
    _convert_tools_for_anthropic,
    _openai_message_to_model_message,
)

# ── Message conversion tests ──


class TestConvertMessagesForAnthropic:
    def test_system_extracted(self) -> None:
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, converted = _convert_messages_for_anthropic(messages)
        assert system == "You are helpful."
        assert len(converted) == 1
        assert converted[0]["role"] == "user"

    def test_multiple_system_joined(self) -> None:
        messages = [
            {"role": "system", "content": "Part 1"},
            {"role": "system", "content": "Part 2"},
            {"role": "user", "content": "Hi"},
        ]
        system, converted = _convert_messages_for_anthropic(messages)
        assert system == "Part 1\n\nPart 2"
        assert len(converted) == 1

    def test_no_system(self) -> None:
        messages = [{"role": "user", "content": "Hello"}]
        system, converted = _convert_messages_for_anthropic(messages)
        assert system is None
        assert len(converted) == 1

    def test_assistant_with_tool_calls(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [
                    {"id": "tc1", "name": "web_search", "arguments": '{"query": "test"}'},
                ],
            },
        ]
        _, converted = _convert_messages_for_anthropic(messages)
        assert len(converted) == 1
        blocks = converted[0]["content"]
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "Let me search."
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["id"] == "tc1"
        assert blocks[1]["name"] == "web_search"
        assert blocks[1]["input"] == {"query": "test"}

    def test_tool_result_message(self) -> None:
        messages = [
            {"role": "tool", "tool_call_id": "tc1", "content": '{"result": "ok"}'},
        ]
        _, converted = _convert_messages_for_anthropic(messages)
        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        blocks = converted[0]["content"]
        assert blocks[0]["type"] == "tool_result"
        assert blocks[0]["tool_use_id"] == "tc1"

    def test_consecutive_tool_results_merged(self) -> None:
        """Multiple tool results after assistant should merge into same user message."""
        messages = [
            {"role": "tool", "tool_call_id": "tc1", "content": "result1"},
            {"role": "tool", "tool_call_id": "tc2", "content": "result2"},
        ]
        _, converted = _convert_messages_for_anthropic(messages)
        # Both should be in the same user message
        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        assert len(converted[0]["content"]) == 2


class TestConvertToolsForAnthropic:
    def test_openai_format_converted(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather info",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ]
        result = _convert_tools_for_anthropic(tools)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "get_weather"
        assert result[0]["description"] == "Get weather info"
        assert "properties" in result[0]["input_schema"]

    def test_none_returns_none(self) -> None:
        assert _convert_tools_for_anthropic(None) is None

    def test_empty_returns_none(self) -> None:
        assert _convert_tools_for_anthropic([]) is None


# ── ModelMessage conversion tests ──


class TestOpenAIMessageToModelMessage:
    def test_text_only(self) -> None:
        msg = MagicMock()
        msg.content = "Hello world"
        msg.tool_calls = None
        result = _openai_message_to_model_message(msg)
        assert isinstance(result, ModelMessage)
        assert result.content == "Hello world"
        assert result.tool_calls == []

    def test_with_tool_calls(self) -> None:
        tc = MagicMock()
        tc.id = "call_123"
        tc.function.name = "search"
        tc.function.arguments = '{"q": "test"}'
        msg = MagicMock()
        msg.content = None
        msg.tool_calls = [tc]
        result = _openai_message_to_model_message(msg)
        assert result.content is None
        assert len(result.tool_calls) == 1
        expected = {"id": "call_123", "name": "search", "arguments": '{"q": "test"}'}
        assert result.tool_calls[0] == expected


class TestAnthropicResponseToModelMessage:
    def test_text_only(self) -> None:
        block = MagicMock()
        block.type = "text"
        block.text = "Hello from Claude"
        response = MagicMock()
        response.content = [block]
        result = _anthropic_response_to_model_message(response)
        assert result.content == "Hello from Claude"
        assert result.tool_calls == []

    def test_with_tool_use(self) -> None:
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = ""
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "tu_123"
        tool_block.name = "get_weather"
        tool_block.input = {"city": "Berlin"}
        response = MagicMock()
        response.content = [text_block, tool_block]
        result = _anthropic_response_to_model_message(response)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["id"] == "tu_123"
        assert result.tool_calls[0]["name"] == "get_weather"
        assert '"city"' in result.tool_calls[0]["arguments"]


# ── AnthropicModelClient streaming tests ──


@dataclass
class _FakeStreamEvent:
    type: str
    content_block: Any = None
    delta: Any = None


@dataclass
class _FakeTextDelta:
    type: str = "text_delta"
    text: str = ""


@dataclass
class _FakeInputJsonDelta:
    type: str = "input_json_delta"
    partial_json: str = ""


@dataclass
class _FakeContentBlock:
    type: str = "text"
    id: str = ""
    name: str = ""


class _FakeAsyncStream:
    """Simulates Anthropic async stream."""

    def __init__(self, events: list[_FakeStreamEvent]) -> None:
        self._events = events

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


class TestAnthropicStreamWithTools:
    @pytest.mark.asyncio
    async def test_text_streaming(self) -> None:
        events = [
            _FakeStreamEvent(type="content_block_delta", delta=_FakeTextDelta(text="Hello ")),
            _FakeStreamEvent(type="content_block_delta", delta=_FakeTextDelta(text="world")),
        ]
        client = AnthropicModelClient.__new__(AnthropicModelClient)
        client._client = AsyncMock()
        client._max_tokens = 1024
        client._max_retries = 0
        client._base_delay = 0
        client._health_tracker = None
        client._provider_name = "claude"
        client._client.messages.create = AsyncMock(return_value=_FakeAsyncStream(events))

        collected: list[ContentDelta | ToolCallsComplete] = []
        async for event in client.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}], "claude-test",
        ):
            collected.append(event)

        assert len(collected) == 2
        assert isinstance(collected[0], ContentDelta)
        assert collected[0].text == "Hello "
        assert isinstance(collected[1], ContentDelta)
        assert collected[1].text == "world"

    @pytest.mark.asyncio
    async def test_tool_call_streaming(self) -> None:
        events = [
            _FakeStreamEvent(
                type="content_block_start",
                content_block=_FakeContentBlock(type="tool_use", id="tu_1", name="search"),
            ),
            _FakeStreamEvent(
                type="content_block_delta",
                delta=_FakeInputJsonDelta(partial_json='{"q":'),
            ),
            _FakeStreamEvent(
                type="content_block_delta",
                delta=_FakeInputJsonDelta(partial_json='"test"}'),
            ),
            _FakeStreamEvent(type="content_block_stop"),
        ]
        client = AnthropicModelClient.__new__(AnthropicModelClient)
        client._client = AsyncMock()
        client._max_tokens = 1024
        client._max_retries = 0
        client._base_delay = 0
        client._health_tracker = None
        client._provider_name = "claude"
        client._client.messages.create = AsyncMock(return_value=_FakeAsyncStream(events))

        collected: list[ContentDelta | ToolCallsComplete] = []
        async for event in client.chat_stream_with_tools(
            [{"role": "user", "content": "search"}], "claude-test",
        ):
            collected.append(event)

        assert len(collected) == 1
        assert isinstance(collected[0], ToolCallsComplete)
        assert len(collected[0].tool_calls) == 1
        tc = collected[0].tool_calls[0]
        assert tc["id"] == "tu_1"
        assert tc["name"] == "search"
        assert tc["arguments"] == '{"q":"test"}'

    @pytest.mark.asyncio
    async def test_mixed_text_and_tools(self) -> None:
        events = [
            _FakeStreamEvent(type="content_block_delta", delta=_FakeTextDelta(text="Searching...")),
            _FakeStreamEvent(
                type="content_block_start",
                content_block=_FakeContentBlock(type="tool_use", id="tu_2", name="fetch"),
            ),
            _FakeStreamEvent(
                type="content_block_delta",
                delta=_FakeInputJsonDelta(partial_json='{"url":"https://x.com"}'),
            ),
            _FakeStreamEvent(type="content_block_stop"),
        ]
        client = AnthropicModelClient.__new__(AnthropicModelClient)
        client._client = AsyncMock()
        client._max_tokens = 1024
        client._max_retries = 0
        client._base_delay = 0
        client._health_tracker = None
        client._provider_name = "claude"
        client._client.messages.create = AsyncMock(return_value=_FakeAsyncStream(events))

        collected: list[ContentDelta | ToolCallsComplete] = []
        async for event in client.chat_stream_with_tools(
            [{"role": "user", "content": "fetch"}], "claude-test",
        ):
            collected.append(event)

        assert len(collected) == 2
        assert isinstance(collected[0], ContentDelta)
        assert collected[0].text == "Searching..."
        assert isinstance(collected[1], ToolCallsComplete)
        assert collected[1].tool_calls[0]["name"] == "fetch"
