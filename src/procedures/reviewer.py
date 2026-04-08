"""ReviewerExecutor + ReviewTool for multi-agent runtime (P2-M2b Slice D).

ReviewerExecutor: single model call, structured prompt → ReviewResult.
ReviewTool: procedure-only BaseTool wrapper that reads staging area.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from src.procedures.handoff import ReviewResult
from src.tools.base import BaseTool, ToolMode

if TYPE_CHECKING:
    from src.agent.model_client import ModelClient
    from src.tools.context import ToolContext

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# ReviewerExecutor
# ---------------------------------------------------------------------------

_REVIEW_PROMPT = """\
You are a reviewer agent. Evaluate the work product below against the criteria.

## Work Product
{work_product}

## Criteria
{criteria}

## Evidence
{evidence}

Respond with ONLY a JSON object:
{{"approved": true/false, "concerns": ["..."], "suggestions": ["..."], "evidence": ["..."]}}
"""


class ReviewerExecutor:
    """Lightweight executor for review tasks — single model call."""

    def __init__(self, model_client: ModelClient, model: str = "gpt-4o-mini") -> None:
        self._model_client = model_client
        self._model = model

    async def review(
        self,
        work_product: dict[str, Any],
        criteria: tuple[str, ...],
        evidence: tuple[str, ...] = (),
    ) -> ReviewResult:
        """Execute a single-call review. Parse failure → fail-closed."""
        prompt = _REVIEW_PROMPT.format(
            work_product=json.dumps(work_product, ensure_ascii=False, default=str),
            criteria="\n".join(f"- {c}" for c in criteria) or "None",
            evidence="\n".join(f"- {e}" for e in evidence) or "None",
        )
        try:
            response = await self._model_client.chat(
                [{"role": "user", "content": prompt}],
                self._model,
            )
            return _parse_review(response)
        except Exception as exc:
            logger.warning("review_model_failed", error=str(exc))
            return ReviewResult(
                approved=False,
                concerns=("review_model_failure",),
            )


def _parse_review(text: str) -> ReviewResult:
    """Parse model response into ReviewResult. Fail-closed on parse error."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if len(lines) > 2 else lines)
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Expected JSON object")
        return ReviewResult(
            approved=bool(data.get("approved", False)),
            concerns=tuple(data.get("concerns", ())),
            suggestions=tuple(data.get("suggestions", ())),
            evidence=tuple(data.get("evidence", ())),
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("review_parse_failure", error=str(exc), raw_text=text[:200])
        return ReviewResult(
            approved=False,
            concerns=("review_parse_failure",),
        )


# ---------------------------------------------------------------------------
# ReviewTool (procedure-only BaseTool wrapper)
# ---------------------------------------------------------------------------


class ReviewTool(BaseTool):
    """Procedure-only tool that wraps ReviewerExecutor.

    Reads worker result from staging area ``_pending_handoffs[handoff_id]``,
    creates ReviewerExecutor on-the-fly from ProcedureActionDeps (D8),
    and writes review result to ``_review_results[handoff_id]``.
    """

    @property
    def name(self) -> str:
        return "procedure_review"

    @property
    def description(self) -> str:
        return "Review a worker result against criteria"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "handoff_id": {"type": "string", "description": "ID of the handoff to review"},
                "criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Review criteria",
                },
            },
            "required": ["handoff_id"],
        }

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset()

    @property
    def is_procedure_only(self) -> bool:
        return True

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        if context is None or context.procedure_deps is None:
            return {"ok": False, "error_code": "REVIEW_NO_PROCEDURE_DEPS"}

        deps = context.procedure_deps
        handoff_id = arguments.get("handoff_id", "")
        criteria = tuple(arguments.get("criteria", ()))

        # Read worker result from staging
        pending = deps.active_procedure.context.get("_pending_handoffs", {})
        worker_data = pending.get(handoff_id)
        if worker_data is None:
            return {
                "ok": False,
                "error_code": "REVIEW_HANDOFF_NOT_FOUND",
                "detail": f"No pending handoff with id '{handoff_id}'",
            }

        # Create executor on-the-fly (D8)
        executor = ReviewerExecutor(deps.model_client, deps.model)
        review = await executor.review(
            work_product=worker_data.get("result", worker_data),
            criteria=criteria,
        )

        logger.info(
            "review_completed",
            handoff_id=handoff_id,
            approved=review.approved,
            concerns_count=len(review.concerns),
        )

        # Read-modify-write _review_results (P2-5r2: full dict for shallow merge)
        current_reviews = dict(deps.active_procedure.context.get("_review_results", {}))
        current_reviews[handoff_id] = review.model_dump()

        return {
            "ok": True,
            "approved": review.approved,
            "concerns": list(review.concerns),
            "suggestions": list(review.suggestions),
            "context_patch": {"_review_results": current_reviews},
        }
