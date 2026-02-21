"""Custom exception hierarchy for NeoMAGI.

All application-specific exceptions inherit from NeoMAGIError,
which carries an error code for RPC error frame mapping.
"""

from __future__ import annotations


class NeoMAGIError(Exception):
    """Base exception for all NeoMAGI errors."""

    def __init__(self, message: str, *, code: str = "INTERNAL_ERROR") -> None:
        super().__init__(message)
        self.code = code


class GatewayError(NeoMAGIError):
    """Errors in the Gateway / WebSocket layer."""

    def __init__(self, message: str, *, code: str = "GATEWAY_ERROR") -> None:
        super().__init__(message, code=code)


class AgentError(NeoMAGIError):
    """Errors in the Agent runtime."""

    def __init__(self, message: str, *, code: str = "AGENT_ERROR") -> None:
        super().__init__(message, code=code)


class SessionError(NeoMAGIError):
    """Errors in Session management."""

    def __init__(self, message: str, *, code: str = "SESSION_ERROR") -> None:
        super().__init__(message, code=code)


class ChannelError(NeoMAGIError):
    """Errors in Channel adapters (Telegram, etc.)."""

    def __init__(self, message: str, *, code: str = "CHANNEL_ERROR") -> None:
        super().__init__(message, code=code)


class LLMError(AgentError):
    """Errors from LLM API calls (timeouts, rate limits, failures)."""

    def __init__(self, message: str, *, code: str = "LLM_ERROR") -> None:
        super().__init__(message, code=code)


class ToolError(AgentError):
    """Errors during tool execution."""

    def __init__(self, message: str, *, code: str = "TOOL_ERROR") -> None:
        super().__init__(message, code=code)


class ToolModeError(ToolError):
    """Tool call denied due to session mode restriction."""

    def __init__(self, message: str = "Tool not available in current mode") -> None:
        super().__init__(message, code="TOOL_DENIED")


class SessionFencingError(GatewayError):
    """Raised when a stale worker tries to write after lock takeover."""

    def __init__(self, message: str = "Session lock fencing check failed") -> None:
        super().__init__(message, code="SESSION_FENCED")
