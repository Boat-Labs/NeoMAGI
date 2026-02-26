from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ChatSendParams(BaseModel):
    content: str
    session_id: str = "main"
    provider: str | None = None  # optional: route to specific provider

    @field_validator("provider", mode="before")
    @classmethod
    def _normalize_provider(cls, v: Any) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            msg = f"provider must be a string (got {type(v).__name__})"
            raise ValueError(msg)
        v = v.strip().lower()
        if not v:
            return None  # empty string → use default
        return v


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


class ToolDeniedData(BaseModel):
    call_id: str
    tool_name: str
    mode: str
    error_code: str
    message: str
    next_action: str


class RPCToolDenied(BaseModel):
    type: Literal["tool_denied"] = "tool_denied"
    id: str
    data: ToolDeniedData


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
