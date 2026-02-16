"""Structured logging configuration using structlog.

Call setup_logging() once at application startup before any log calls.
"""

from __future__ import annotations

import structlog


def setup_logging(*, json_output: bool = True, log_level: str = "INFO") -> None:
    """Configure structlog for the application.

    Args:
        json_output: If True, render logs as JSON. If False, use dev-friendly console output.
        log_level: Minimum log level to emit (DEBUG, INFO, WARNING, ERROR).
    """
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_output:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
