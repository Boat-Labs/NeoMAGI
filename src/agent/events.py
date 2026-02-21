from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextChunk:
    """A chunk of text content from the LLM response."""

    content: str


@dataclass
class ToolCallInfo:
    """Notification that a tool is being called."""

    tool_name: str
    arguments: dict
    call_id: str


@dataclass
class ToolDenied:
    """Notification that a tool call was denied due to mode restriction."""

    tool_name: str
    call_id: str
    current_mode: str
    reason: str


AgentEvent = TextChunk | ToolCallInfo | ToolDenied
