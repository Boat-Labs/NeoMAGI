"""Rule-based TaskFrame extraction from user content (P2-M1b-P2).

Pure function, no LLM calls, no network access.
V1 uses keyword matching for task_type classification and risk detection.
"""

from __future__ import annotations

import re

from src.skills.types import TaskFrame, TaskType

# ---------------------------------------------------------------------------
# V1 keyword → TaskType mapping
# ---------------------------------------------------------------------------

_RE_RESEARCH = re.compile(r"搜索|查找|调研|分析|search|find|research|analyze", re.IGNORECASE)
_RE_EDIT = re.compile(r"修改|重写|更新|edit|modify|update|refactor", re.IGNORECASE)
_RE_CREATE = re.compile(r"写|创建|起草|write|create|draft", re.IGNORECASE)
_RE_DEBUG = re.compile(r"报错|修复|排查|debug|fix|error|bug", re.IGNORECASE)

# edit before create: "重写" contains "写", so edit patterns must match first
_TASK_TYPE_RULES: tuple[tuple[re.Pattern[str], TaskType], ...] = (
    (_RE_RESEARCH, TaskType.research),
    (_RE_EDIT, TaskType.edit),
    (_RE_CREATE, TaskType.create),
    (_RE_DEBUG, TaskType.debug),
)

_HIGH_RISK_PATTERN: re.Pattern[str] = re.compile(
    r"删除|drop|\brm\s|force|--hard", re.IGNORECASE
)

_TARGET_OUTCOME_MAX_LEN = 200


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_task_frame(
    content: str,
    *,
    mode: str = "chat_safe",
    channel: str | None = None,
    available_tools: tuple[str, ...] = (),
) -> TaskFrame:
    """Rule-based TaskFrame extraction. No LLM call.

    V1 strategy:
    - task_type: first matching keyword pattern wins; ``chat`` if content
      looks conversational; ``unknown`` otherwise.
    - target_outcome: first 200 chars of *content*.
    - risk: ``"high"`` if high-risk signal words are present, else ``"low"``.
    """
    task_type = _classify_task_type(content)
    risk = "high" if _HIGH_RISK_PATTERN.search(content) else "low"
    target_outcome = content[:_TARGET_OUTCOME_MAX_LEN] if content else None

    return TaskFrame(
        task_type=task_type,
        target_outcome=target_outcome,
        risk=risk,
        channel=channel,
        current_mode=mode,
        current_procedure=None,
        available_tools=available_tools,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_task_type(content: str) -> TaskType:
    """Return the first matching TaskType, or chat/unknown."""
    if not content or not content.strip():
        return TaskType.unknown

    for pattern, task_type in _TASK_TYPE_RULES:
        if pattern.search(content):
            return task_type

    # Heuristic: short content without clear intent → chat
    # (question marks, greetings, single-sentence without action verbs)
    stripped = content.strip()
    if len(stripped) < 100 and ("?" in stripped or "？" in stripped):
        return TaskType.chat

    return TaskType.unknown
