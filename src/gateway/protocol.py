from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class ChatSendParams(BaseModel):
    content: str
    session_id: str = "main"


class RPCRequest(BaseModel):
    type: Literal["request"] = "request"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    method: Literal["chat.send"] = "chat.send"
    params: ChatSendParams


class StreamChunkData(BaseModel):
    content: str
    done: bool


class RPCStreamChunk(BaseModel):
    type: Literal["stream_chunk"] = "stream_chunk"
    id: str
    data: StreamChunkData


class ToolCallData(BaseModel):
    tool_name: str
    arguments: dict
    call_id: str


class RPCToolCall(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    data: ToolCallData


class RPCErrorData(BaseModel):
    code: str
    message: str


class RPCError(BaseModel):
    type: Literal["error"] = "error"
    id: str
    error: RPCErrorData


def parse_rpc_request(raw: str) -> RPCRequest:
    """Parse a raw JSON string into an RPCRequest. Raises ValidationError on invalid input."""
    return RPCRequest.model_validate_json(raw)
