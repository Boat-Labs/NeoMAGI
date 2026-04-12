"""Auth-specific exceptions."""

from __future__ import annotations

from src.infra.errors import NeoMAGIError


class BindingConflictError(NeoMAGIError):
    """Raised when a binding already exists for a different principal."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="BINDING_CONFLICT")
