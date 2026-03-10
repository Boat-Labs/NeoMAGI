"""Compaction engine: rolling summary + anchor preservation + flush generation.

Memory flush is generated exclusively by this module (ADR 0032).
AgentLoop only orchestrates — it MUST NOT call MemoryFlushGenerator directly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import structlog

from src.agent.memory_flush import MemoryFlushCandidate, MemoryFlushGenerator
from src.agent.token_budget import BudgetStatus, TokenCounter
from src.session.manager import MessageWithSeq

if TYPE_CHECKING:
    from src.agent.model_client import ModelClient
    from src.config.settings import CompactionSettings

logger = structlog.get_logger()

# Prompt template for rolling summary generation (ADR 0028)
_SUMMARY_PROMPT = """\
You are a conversation compactor. Produce a structured JSON summary of the conversation below.

Previous summary (if any):
{previous_summary}

Conversation to compress:
{conversation}

Output a JSON object with exactly these keys:
- "facts": list of confirmed facts
- "decisions": list of decisions made
- "open_todos": list of unfinished items
- "user_prefs": list of user preference declarations
- "timeline": list of key events with timestamps or order

Rules:
- Be concise. Each item should be one sentence.
- Preserve information critical for task continuity.
- Do NOT include casual greetings or acknowledgments.
- Output ONLY the JSON object, no markdown fencing.
- Total output must be within {max_output_tokens} tokens.
"""


@dataclass
class Turn:
    """A conversation turn: user message + all subsequent assistant/tool messages."""

    start_seq: int
    end_seq: int
    messages: list[MessageWithSeq]


@dataclass
class CompactionResult:
    """Result of a compaction operation."""

    status: Literal["success", "degraded", "failed", "noop"]
    compacted_context: str | None = None
    compaction_metadata: dict = field(default_factory=dict)
    new_compaction_seq: int = 0
    memory_flush_candidates: list[MemoryFlushCandidate] = field(default_factory=list)
    preserved_messages: list[MessageWithSeq] = field(default_factory=list)


def split_turns(messages: list[MessageWithSeq]) -> list[Turn]:
    """Split messages into turns by user-message boundaries.

    A turn starts at each 'user' role message and includes all subsequent
    assistant/tool messages until the next user message.
    """
    if not messages:
        return []

    turns: list[Turn] = []
    current_msgs: list[MessageWithSeq] = []

    for msg in messages:
        if msg.role == "user" and current_msgs:
            # End previous turn, start new one
            turns.append(
                Turn(
                    start_seq=current_msgs[0].seq,
                    end_seq=current_msgs[-1].seq,
                    messages=current_msgs,
                )
            )
            current_msgs = []
        current_msgs.append(msg)

    # Last turn
    if current_msgs:
        turns.append(
            Turn(
                start_seq=current_msgs[0].seq,
                end_seq=current_msgs[-1].seq,
                messages=current_msgs,
            )
        )

    return turns


class CompactionEngine:
    """Core compaction logic: rolling summary + anchor preservation + flush generation.

    Memory flush is generated exclusively by this module (ADR 0032).
    AgentLoop only orchestrates.
    """

    def __init__(
        self,
        model_client: ModelClient,
        token_counter: TokenCounter,
        settings: CompactionSettings,
        workspace_dir: Path | None = None,
    ) -> None:
        self._model_client = model_client
        self._counter = token_counter
        self._settings = settings
        self._workspace_dir = workspace_dir
        self._flush_generator = MemoryFlushGenerator(settings)

    async def compact(
        self,
        messages: list[MessageWithSeq],
        system_prompt: str,
        tools_schema: list[dict],
        budget_status: BudgetStatus,
        last_compaction_seq: int | None,
        previous_compacted_context: str | None,
        current_user_seq: int,
        model: str,
        session_id: str = "",
    ) -> CompactionResult:
        """Execute compaction pipeline: split → flush → summarise → anchor-check."""
        zones = self._identify_zones(messages, current_user_seq, last_compaction_seq)
        if zones is None:
            return self._noop(last_compaction_seq)

        preserved_turns, compressible_turns, preserved_msgs = zones
        if not compressible_turns:
            return self._noop(last_compaction_seq, preserved_msgs)

        new_seq = min(compressible_turns[-1].end_seq, current_user_seq - 1)
        flush_candidates, flush_skipped = await self._run_flush(compressible_turns, session_id)

        summary_text, status = await self._run_summary(
            compressible_turns, previous_compacted_context, model, session_id,
        )
        if status == "degraded_small_input":
            return self._build_result(
                "degraded", previous_compacted_context, new_seq,
                flush_candidates, preserved_msgs, preserved_turns,
                compressible_turns, flush_skipped=flush_skipped,
            )

        anchor_passed, anchor_retry_used, summary_text, status = (
            await self._run_anchor_validation(
                summary_text, status, system_prompt, preserved_turns,
                previous_compacted_context, compressible_turns, model, session_id,
            )
        )
        return self._build_result(
            status, summary_text, new_seq, flush_candidates,
            preserved_msgs, preserved_turns, compressible_turns,
            flush_skipped=flush_skipped, anchor_validation_passed=anchor_passed,
            anchor_retry_used=anchor_retry_used,
        )

    def _noop(
        self, last_compaction_seq: int | None,
        preserved_messages: list[MessageWithSeq] | None = None,
    ) -> CompactionResult:
        return CompactionResult(
            status="noop", new_compaction_seq=last_compaction_seq or 0,
            compaction_metadata=self._make_metadata("noop"),
            preserved_messages=preserved_messages or [],
        )

    # ------------------------------------------------------------------
    # compact sub-steps
    # ------------------------------------------------------------------

    def _identify_zones(
        self,
        messages: list[MessageWithSeq],
        current_user_seq: int,
        last_compaction_seq: int | None,
    ) -> tuple[list[Turn], list[Turn], list[MessageWithSeq]] | None:
        """Return (preserved_turns, compressible_turns, preserved_msgs) or None for noop."""
        all_turns = split_turns(messages)
        if not all_turns:
            return None

        completed = [t for t in all_turns if t.start_seq < current_user_seq]
        if not completed:
            return None

        min_preserved = self._settings.min_preserved_turns
        if len(completed) <= min_preserved:
            return None

        preserved = completed[-min_preserved:]
        compressible = completed[:-min_preserved]
        if last_compaction_seq is not None:
            compressible = [t for t in compressible if t.end_seq > last_compaction_seq]

        preserved_msgs = [m for t in preserved for m in t.messages]
        return preserved, compressible, preserved_msgs

    async def _run_flush(
        self, compressible_turns: list[Turn], session_id: str,
    ) -> tuple[list[MemoryFlushCandidate], bool]:
        """Generate memory flush candidates with timeout protection."""
        try:
            candidates = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None, self._flush_generator.generate, compressible_turns, session_id,
                ),
                timeout=self._settings.flush_timeout_s,
            )
            return candidates, False
        except (TimeoutError, Exception):
            logger.warning("flush_timeout_or_error", session_id=session_id)
            return [], True

    async def _run_summary(
        self,
        compressible_turns: list[Turn],
        previous_compacted_context: str | None,
        model: str,
        session_id: str,
    ) -> tuple[str | None, Literal["success", "degraded", "degraded_small_input"]]:
        """Generate rolling summary via LLM. Returns (text, status)."""
        conversation_text = self._turns_to_text(compressible_turns)
        input_tokens = self._counter.count_text(conversation_text)
        max_summary_tokens = int(input_tokens * 0.3)

        if max_summary_tokens < 100:
            logger.info(
                "input_too_small_for_summary",
                input_tokens=input_tokens,
                max_summary_tokens=max_summary_tokens,
            )
            return None, "degraded_small_input"

        try:
            text = await asyncio.wait_for(
                self._generate_summary(
                    previous_compacted_context, conversation_text,
                    max_summary_tokens, model,
                ),
                timeout=self._settings.compact_timeout_s,
            )
            return text, "success"
        except (TimeoutError, Exception) as e:
            logger.warning("compaction_llm_failed", error=str(e), session_id=session_id)
            return None, "degraded"

    async def _run_anchor_validation(
        self,
        summary_text: str | None,
        status: str,
        system_prompt: str,
        preserved_turns: list[Turn],
        previous_compacted_context: str | None,
        compressible_turns: list[Turn],
        model: str,
        session_id: str,
    ) -> tuple[bool, bool, str | None, Literal["success", "degraded", "failed", "noop"]]:
        """Anchor visibility validation (ADR 0030). Returns (passed, retry_used, text, status)."""
        if not summary_text or status != "success":
            return True, False, summary_text, status  # type: ignore[return-value]

        preserved_text = self._turns_to_text(preserved_turns)
        passed = self._validate_anchors(system_prompt, summary_text, preserved_text)

        if passed or not self._settings.anchor_retry_enabled:
            final_status: Literal["success", "degraded"] = "success" if passed else "degraded"
            return passed, False, summary_text, final_status

        logger.info("anchor_retry", session_id=session_id)
        conversation_text = self._turns_to_text(compressible_turns)
        input_tokens = self._counter.count_text(conversation_text)
        max_summary_tokens = int(input_tokens * 0.3)
        try:
            summary_text = await asyncio.wait_for(
                self._generate_summary(
                    previous_compacted_context, conversation_text,
                    max_summary_tokens, model,
                ),
                timeout=self._settings.compact_timeout_s,
            )
            passed = self._validate_anchors(system_prompt, summary_text, preserved_text)
        except (TimeoutError, Exception):
            passed = False

        if not passed:
            logger.warning("anchor_validation_failed_after_retry", session_id=session_id)
        return passed, True, summary_text, "success" if passed else "degraded"

    def _build_result(
        self,
        status: str,
        compacted_context: str | None,
        new_compaction_seq: int,
        flush_candidates: list[MemoryFlushCandidate],
        preserved_msgs: list[MessageWithSeq],
        preserved_turns: list[Turn],
        compressible_turns: list[Turn],
        *,
        flush_skipped: bool = False,
        anchor_validation_passed: bool = True,
        anchor_retry_used: bool = False,
    ) -> CompactionResult:
        metadata = self._make_metadata(
            status,
            preserved_count=len(preserved_turns),
            summarized_count=len(compressible_turns),
            flush_skipped=flush_skipped,
            anchor_validation_passed=anchor_validation_passed,
            anchor_retry_used=anchor_retry_used,
            compacted_context_tokens=(
                self._counter.count_text(compacted_context) if compacted_context else 0
            ),
            rolling_summary_input_tokens=(
                self._counter.count_text(self._turns_to_text(compressible_turns))
            ),
        )
        return CompactionResult(
            status=status,  # type: ignore[arg-type]
            compacted_context=compacted_context,
            compaction_metadata=metadata,
            new_compaction_seq=new_compaction_seq,
            memory_flush_candidates=flush_candidates,
            preserved_messages=preserved_msgs,
        )

    async def _generate_summary(
        self,
        previous_context: str | None,
        conversation_text: str,
        max_output_tokens: int,
        model: str,
    ) -> str:
        """Generate rolling summary via LLM call."""
        prompt = _SUMMARY_PROMPT.format(
            previous_summary=previous_context or "(none)",
            conversation=conversation_text,
            max_output_tokens=max_output_tokens,
        )

        messages = [
            {"role": "system", "content": "You are a precise conversation summarizer."},
            {"role": "user", "content": prompt},
        ]

        response = await self._model_client.chat(
            messages, model, temperature=self._settings.summary_temperature
        )
        return response.strip()

    # Anchor files: first non-empty line used as probe (ADR 0030)
    _ANCHOR_FILES = ("AGENTS.md", "SOUL.md", "USER.md")

    def _extract_anchor_phrases(self) -> list[str]:
        """Extract first non-empty line from each workspace anchor file."""
        if self._workspace_dir is None:
            return []
        anchors: list[str] = []
        for filename in self._ANCHOR_FILES:
            phrase = self._read_first_line(self._workspace_dir / filename)
            if phrase is not None:
                anchors.append(phrase)
        return anchors

    @staticmethod
    def _read_first_line(filepath: Path) -> str | None:
        """Return first non-empty line from *filepath*, or None."""
        if not filepath.exists():
            return None
        try:
            text = filepath.read_text(encoding="utf-8")
        except OSError:
            logger.warning("anchor_file_read_error", file=filepath.name)
            return None
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return None

    def _validate_anchors(
        self,
        system_prompt: str,
        compacted_context: str | None,
        effective_history_text: str = "",
    ) -> bool:
        """Validate anchor visibility in final model context (ADR 0030).

        Checks that first non-empty line from AGENTS/SOUL/USER files
        is present in the final context sent to the model.
        """
        if not system_prompt:
            return False

        final_context = system_prompt + (compacted_context or "") + effective_history_text

        anchor_phrases = self._extract_anchor_phrases()
        if not anchor_phrases:
            # No anchors to validate → pass (don't block compaction)
            return True

        for phrase in anchor_phrases:
            if phrase not in final_context:
                logger.warning("anchor_missing", phrase=phrase[:80])
                return False

        return True

    def _turns_to_text(self, turns: list[Turn]) -> str:
        """Convert turns to plain text for summary input."""
        lines: list[str] = []
        for turn in turns:
            for msg in turn.messages:
                content = msg.content or ""
                if content:
                    lines.append(f"[{msg.role}]: {content}")
        return "\n".join(lines)

    def _make_metadata(
        self,
        status: str,
        preserved_count: int = 0,
        summarized_count: int = 0,
        trimmed_count: int = 0,
        flush_skipped: bool = False,
        anchor_validation_passed: bool = True,
        anchor_retry_used: bool = False,
        compacted_context_tokens: int = 0,
        rolling_summary_input_tokens: int = 0,
    ) -> dict:
        return {
            "schema_version": 1,
            "status": status,
            "preserved_count": preserved_count,
            "summarized_count": summarized_count,
            "trimmed_count": trimmed_count,
            "flush_skipped": flush_skipped,
            "anchor_validation_passed": anchor_validation_passed,
            "anchor_retry_used": anchor_retry_used,
            "triggered_at": datetime.now(UTC).isoformat(),
            "compacted_context_tokens": compacted_context_tokens,
            "rolling_summary_input_tokens": rolling_summary_input_tokens,
        }
