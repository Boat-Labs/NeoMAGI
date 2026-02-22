from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import structlog

if TYPE_CHECKING:
    from src.config.settings import CompactionSettings

logger = structlog.get_logger()

# Per-message overhead in OpenAI chat format (~4 tokens per message header).
_MSG_OVERHEAD_TOKENS = 4
# Reply priming tokens added once per request.
_REPLY_PRIMING_TOKENS = 3


class TokenCounter:
    """Token counter with tiktoken precision and chars/4 fallback.

    Binds to a specific model at construction time. Automatically
    resolves tiktoken encoding; falls back to estimate mode if
    encoding is unavailable (non-OpenAI models).
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._encoding = None
        self._mode: Literal["exact", "estimate"] = "estimate"

        try:
            import tiktoken

            self._encoding = tiktoken.encoding_for_model(model)
            self._mode = "exact"
        except Exception:
            logger.warning("tokenizer_fallback", model=model, mode="estimate")

    @property
    def tokenizer_mode(self) -> Literal["exact", "estimate"]:
        """Current counting mode (aligned with ADR 0029)."""
        return self._mode

    def count_text(self, text: str) -> int:
        """Count tokens for a plain text string."""
        if not text:
            return 0
        if self._encoding is not None:
            return len(self._encoding.encode(text))
        return math.ceil(len(text) / 4)

    def count_messages(self, messages: list[dict]) -> int:
        """Count tokens for a list of chat messages (OpenAI format).

        Includes per-message overhead tokens (~4 tokens/message header).
        Handles all roles: system, user, assistant, tool.
        """
        total = 0
        for msg in messages:
            total += _MSG_OVERHEAD_TOKENS
            if content := msg.get("content"):
                total += self.count_text(str(content))
            if role := msg.get("role"):
                total += self.count_text(role)
            if name := msg.get("name"):
                total += self.count_text(name)
            if tool_calls := msg.get("tool_calls"):
                total += self.count_text(json.dumps(tool_calls))
            if tool_call_id := msg.get("tool_call_id"):
                total += self.count_text(tool_call_id)
        total += _REPLY_PRIMING_TOKENS
        return total

    def count_tools_schema(self, tools: list[dict]) -> int:
        """Count tokens for tools/function schema definitions."""
        if not tools:
            return 0
        return self.count_text(json.dumps(tools))


@dataclass(frozen=True)
class BudgetStatus:
    """Result of a budget check."""

    status: Literal["ok", "warn", "compact_needed"]
    current_tokens: int
    usable_budget: int
    warn_threshold: int
    compact_threshold: int
    tokenizer_mode: str  # "exact" | "estimate" (ADR 0029)


class BudgetTracker:
    """Tracks token budget against configurable thresholds."""

    def __init__(self, settings: CompactionSettings, model: str) -> None:
        self._counter = TokenCounter(model)
        usable = (
            settings.context_limit
            - settings.reserved_output_tokens
            - settings.safety_margin_tokens
        )
        self._usable_budget = usable
        self._warn_threshold = int(usable * settings.warn_ratio)
        self._compact_threshold = int(usable * settings.compact_ratio)

    @property
    def counter(self) -> TokenCounter:
        return self._counter

    def check(self, current_tokens: int) -> BudgetStatus:
        """Evaluate current token usage against thresholds."""
        if current_tokens >= self._compact_threshold:
            status: Literal["ok", "warn", "compact_needed"] = "compact_needed"
        elif current_tokens >= self._warn_threshold:
            status = "warn"
        else:
            status = "ok"

        return BudgetStatus(
            status=status,
            current_tokens=current_tokens,
            usable_budget=self._usable_budget,
            warn_threshold=self._warn_threshold,
            compact_threshold=self._compact_threshold,
            tokenizer_mode=self._counter.tokenizer_mode,
        )
