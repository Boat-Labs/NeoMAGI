from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Resolver-generated session_id prefixes — WS has no auth, must not access these sessions.
# Covers per-channel-peer ("telegram:peer:{id}") and per-peer ("peer:{id}").
CHANNEL_EXCLUSIVE_PREFIXES = ("telegram:", "peer:")


class ChatSendParams(BaseModel):
    content: str
    session_id: str = "main"
    provider: str | None = None  # optional: route to specific provider

    @field_validator("session_id", mode="after")
    @classmethod
    def _reject_channel_exclusive_prefix(cls, v: str) -> str:
        for prefix in CHANNEL_EXCLUSIVE_PREFIXES:
            if v.startswith(prefix):
                msg = (
                    f"session_id with channel-exclusive prefix '{prefix}'"
                    " cannot be accessed via WebSocket"
                )
                raise ValueError(msg)
        return v

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

    @field_validator("session_id", mode="after")
    @classmethod
    def _reject_channel_exclusive_prefix(cls, v: str) -> str:
        for prefix in CHANNEL_EXCLUSIVE_PREFIXES:
            if v.startswith(prefix):
                msg = (
                    f"session_id with channel-exclusive prefix '{prefix}'"
                    " cannot be accessed via WebSocket"
                )
                raise ValueError(msg)
        return v


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


class SessionSetModeParams(BaseModel):
    session_id: str = "main"
    mode: str

    @field_validator("session_id", mode="after")
    @classmethod
    def _reject_channel_exclusive_prefix(cls, v: str) -> str:
        for prefix in CHANNEL_EXCLUSIVE_PREFIXES:
            if v.startswith(prefix):
                msg = (
                    f"session_id with channel-exclusive prefix '{prefix}'"
                    " cannot be accessed via WebSocket"
                )
                raise ValueError(msg)
        return v


class SessionModeData(BaseModel):
    session_id: str
    mode: str


class RPCSessionModeResponse(BaseModel):
    type: Literal["response"] = "response"
    id: str
    data: SessionModeData


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
