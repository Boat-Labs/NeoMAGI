"""Tests for chat_stream_with_tools tool-call delta accumulation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.model_client import OpenAICompatModelClient, ToolCallsComplete


@pytest.fixture()
def client():
    c = OpenAICompatModelClient(api_key="test-key", max_retries=0)
    c._client = MagicMock()
    return c


def _tc_delta(
    *,
    index: int | None,
    call_id: str | None = None,
    name: str | None = None,
    args: str | None = None,
):
    fn = None
    if name is not None or args is not None:
        fn = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=call_id, function=fn)


def _chunk(*, tool_calls=None, content=None):
    chunk = MagicMock()
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(delta=delta)
    chunk.choices = [choice]
    return chunk


def _stream_from(chunks):
    async def _gen():
        for c in chunks:
            yield c

    return _gen()


class TestToolCallAccumulation:
    @pytest.mark.asyncio()
    async def test_openai_index_fragments_accumulate(self, client):
        chunks = [
            _chunk(tool_calls=[
                _tc_delta(index=0, call_id="call_1", name="memory_search", args='{"query":"'),
                _tc_delta(index=1, call_id="call_2", name="memory_search", args='{"query":"city"}'),
            ]),
            _chunk(tool_calls=[_tc_delta(index=0, args='alice"}')]),
        ]
        client._client.chat.completions.create = AsyncMock(
            return_value=_stream_from(chunks)
        )

        events = []
        async for event in client.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}], "test-model", tools=[{"type": "function"}]
        ):
            events.append(event)

        tool_event = next(e for e in events if isinstance(e, ToolCallsComplete))
        assert len(tool_event.tool_calls) == 2
        assert tool_event.tool_calls[0]["id"] == "call_1"
        assert tool_event.tool_calls[0]["name"] == "memory_search"
        assert tool_event.tool_calls[0]["arguments"] == '{"query":"alice"}'
        assert tool_event.tool_calls[1]["id"] == "call_2"
        assert tool_event.tool_calls[1]["arguments"] == '{"query":"city"}'

    @pytest.mark.asyncio()
    async def test_gemini_null_index_multi_calls_do_not_concat(self, client):
        chunks = [
            _chunk(tool_calls=[
                _tc_delta(
                    index=None,
                    call_id="function-call-1",
                    name="memory_search",
                    args='{"query":"user name"}',
                ),
                _tc_delta(
                    index=None,
                    call_id="function-call-2",
                    name="memory_search",
                    args='{"query":"city"}',
                ),
                _tc_delta(
                    index=None,
                    call_id="function-call-3",
                    name="memory_search",
                    args='{"query":"cat"}',
                ),
                _tc_delta(
                    index=None,
                    call_id="function-call-4",
                    name="memory_search",
                    args='{"query":"book"}',
                ),
            ]),
        ]
        client._client.chat.completions.create = AsyncMock(
            return_value=_stream_from(chunks)
        )

        events = []
        async for event in client.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}], "test-model", tools=[{"type": "function"}]
        ):
            events.append(event)

        tool_event = next(e for e in events if isinstance(e, ToolCallsComplete))
        assert len(tool_event.tool_calls) == 4
        assert [tc["id"] for tc in tool_event.tool_calls] == [
            "function-call-1",
            "function-call-2",
            "function-call-3",
            "function-call-4",
        ]
        assert [tc["arguments"] for tc in tool_event.tool_calls] == [
            '{"query":"user name"}',
            '{"query":"city"}',
            '{"query":"cat"}',
            '{"query":"book"}',
        ]
