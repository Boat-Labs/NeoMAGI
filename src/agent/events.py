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


AgentEvent = TextChunk | ToolCallInfo
