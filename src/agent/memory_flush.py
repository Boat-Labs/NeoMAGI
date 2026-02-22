"""Pre-compaction memory flush candidate generation.

Called exclusively by CompactionEngine (ADR 0032).
AgentLoop MUST NOT call this directly.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from src.agent.compaction import Turn
    from src.config.settings import CompactionSettings

logger = structlog.get_logger()

# Patterns for user explicit declarations (high confidence)
_EXPLICIT_PATTERNS = [
    re.compile(r"记住|请记住|以后|我喜欢|我不喜欢|我偏好|我讨厌|永远不要|总是", re.IGNORECASE),
    re.compile(
        r"\b(remember|always|never|prefer|i like|i don'?t like|i hate|from now on)\b",
        re.IGNORECASE,
    ),
]

# Patterns for decisions/facts (medium confidence)
_DECISION_PATTERNS = [
    re.compile(r"我们决定|确认|最终|选定|敲定|同意", re.IGNORECASE),
    re.compile(
        r"\b(we decided|confirmed|finalized|agreed|settled on|chosen)\b",
        re.IGNORECASE,
    ),
]

# Patterns for casual/skip (low signal)
_SKIP_PATTERNS = [
    re.compile(r"^(ok|好的?|嗯|是的?|对|谢谢|thanks|sure|got it|明白)$", re.IGNORECASE),
]


@dataclass
class MemoryFlushCandidate:
    """Pre-compaction memory candidate (aligned with m2_architecture.md 3.3)."""

    candidate_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_session_id: str = ""
    source_message_ids: list[str] = field(default_factory=list)
    candidate_text: str = ""
    constraint_tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


class MemoryFlushGenerator:
    """Extract memory candidates from compressible turns.

    Called exclusively by CompactionEngine (ADR 0032).
    AgentLoop MUST NOT call this directly.

    Rule-based extraction (no LLM in Phase 2):
    - User explicit declarations -> confidence 0.8-1.0
    - Confirmed decisions/facts -> confidence 0.5-0.7
    - General conversation -> confidence 0.2-0.4
    - Casual chat/acknowledgments -> skip
    - Max 20 candidates per flush (configurable)
    - Single candidate max 2KB text (configurable)
    - confidence in [0.0, 1.0] (hard constraint)
    """

    def __init__(self, settings: CompactionSettings) -> None:
        self._max_candidates = settings.max_flush_candidates
        self._max_text_bytes = settings.max_candidate_text_bytes

    def generate(
        self,
        compressible_turns: list[Turn],
        session_id: str,
    ) -> list[MemoryFlushCandidate]:
        """Extract memory candidates from turns."""
        candidates: list[MemoryFlushCandidate] = []

        for turn in compressible_turns:
            if len(candidates) >= self._max_candidates:
                break

            # Extract user messages for analysis
            user_msgs = [m for m in turn.messages if m.role == "user" and m.content]
            if not user_msgs:
                continue

            for msg in user_msgs:
                if len(candidates) >= self._max_candidates:
                    break

                content = msg.content or ""
                stripped = content.strip()

                # Skip casual/acknowledgment messages
                if any(p.match(stripped) for p in _SKIP_PATTERNS):
                    continue

                # Classify and extract
                tags, confidence = self._classify(stripped)
                if confidence < 0.1:
                    continue

                # Truncate to max bytes (UTF-8 safe)
                encoded = stripped.encode("utf-8")
                if len(encoded) > self._max_text_bytes:
                    text = encoded[: self._max_text_bytes].decode("utf-8", errors="ignore")
                else:
                    text = stripped

                candidates.append(
                    MemoryFlushCandidate(
                        source_session_id=session_id,
                        source_message_ids=[str(msg.seq)],
                        candidate_text=text,
                        constraint_tags=tags,
                        confidence=min(max(confidence, 0.0), 1.0),
                    )
                )

        logger.info(
            "memory_flush_generated",
            session_id=session_id,
            candidate_count=len(candidates),
            turn_count=len(compressible_turns),
        )
        return candidates

    def _classify(self, text: str) -> tuple[list[str], float]:
        """Classify text and return (tags, confidence)."""
        # Check explicit user declarations first (highest priority)
        if any(p.search(text) for p in _EXPLICIT_PATTERNS):
            tags = ["user_preference"]
            # Determine sub-tags
            if re.search(r"永远不要|never|不要|禁止", text, re.IGNORECASE):
                tags.append("safety_boundary")
            return tags, 0.9

        # Check decisions/facts
        if any(p.search(text) for p in _DECISION_PATTERNS):
            return ["fact"], 0.6

        # General conversation — low confidence
        if len(text) > 20:
            return ["fact"], 0.3

        return [], 0.0
