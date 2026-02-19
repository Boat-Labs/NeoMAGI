from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatSendParams(BaseModel):
    content: str
    session_id: str = "main"


class ChatHistoryParams(BaseModel):
    session_id: str = "main"


class RPCRequest(BaseModel):
    """Generic RPC request. method determines which params to expect."""

    type: Literal["request"] = "request"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


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


class RPCHistoryResponseData(BaseModel):
    messages: list[dict[str, Any]]


class RPCHistoryResponse(BaseModel):
    type: Literal["response"] = "response"
    id: str
    data: RPCHistoryResponseData


def parse_rpc_request(raw: str) -> RPCRequest:
    """Parse a raw JSON string into an RPCRequest.

    Raises GatewayError(code="PARSE_ERROR") on invalid JSON or schema mismatch.
    """
    from src.infra.errors import GatewayError

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise GatewayError(f"Invalid JSON: {e}", code="PARSE_ERROR") from e
    try:
        return RPCRequest.model_validate(data)
    except Exception as e:
        raise GatewayError(f"Invalid RPC request: {e}", code="PARSE_ERROR") from e
