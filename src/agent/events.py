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
    mode: str
    error_code: str = "MODE_DENIED"
    message: str = ""
    next_action: str = ""


AgentEvent = TextChunk | ToolCallInfo | ToolDenied
