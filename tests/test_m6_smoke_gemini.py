from __future__ import annotations

import json

from scripts.m6_smoke_gemini import (
    _append_tool_interaction,
    _collect_content_text,
    _extract_first_tool_call,
    _is_weather_followup_answer,
)
from src.agent.model_client import ContentDelta, ToolCallsComplete


def test_extract_first_tool_call_returns_first_completed_call() -> None:
    tool_call = {"id": "call-1", "name": "get_weather", "arguments": "{\"location\":\"Paris\"}"}

    result = _extract_first_tool_call([
        ContentDelta(text="thinking"),
        ToolCallsComplete(tool_calls=[tool_call]),
    ])

    assert result == tool_call


def test_extract_first_tool_call_returns_none_without_completed_calls() -> None:
    result = _extract_first_tool_call([ContentDelta(text="no tool here")])

    assert result is None


def test_append_tool_interaction_appends_assistant_and_tool_messages() -> None:
    messages = [{"role": "user", "content": "What is the weather in Paris?"}]
    tool_call = {"id": "call-1", "name": "get_weather", "arguments": "{\"location\":\"Paris\"}"}

    _append_tool_interaction(
        messages,
        tool_call,
        {"temperature": "18°C", "condition": "Cloudy"},
    )

    assert messages[1] == {
        "role": "assistant",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "get_weather",
                "arguments": "{\"location\":\"Paris\"}",
            },
        }],
    }
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "call-1"
    assert json.loads(messages[2]["content"]) == {"temperature": "18°C", "condition": "Cloudy"}


def test_collect_content_text_joins_only_content_delta_events() -> None:
    text = _collect_content_text([
        ContentDelta(text="Paris "),
        ToolCallsComplete(tool_calls=[{"id": "call-1", "name": "get_weather", "arguments": "{}"}]),
        ContentDelta(text="is cloudy."),
    ])

    assert text == "Paris is cloudy."


def test_is_weather_followup_answer_matches_expected_keywords() -> None:
    assert _is_weather_followup_answer("Paris is cloudy and 18°C today.")
    assert not _is_weather_followup_answer("I cannot help with that.")
