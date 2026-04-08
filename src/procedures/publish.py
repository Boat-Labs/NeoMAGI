"""PublishTool + merge helpers for multi-agent runtime (P2-M2b Slice F).

PublishTool is a procedure-only BaseTool. It reads worker results from the
``_pending_handoffs`` staging area, merges specified keys into visible context,
and signals memory flush via ``_publish_flush_texts`` in result data (D9).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from src.procedures.delegation import require_role
from src.procedures.roles import AgentRole
from src.tools.base import BaseTool, ToolMode

if TYPE_CHECKING:
    from src.tools.context import ToolContext

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# merge helper
# ---------------------------------------------------------------------------


def merge_worker_result(
    raw_result: dict[str, Any],
    merge_keys: tuple[str, ...],
    current_context: dict[str, Any],
) -> dict[str, Any]:
    """Extract ``merge_keys`` from raw worker result and shallow-merge into context_patch.

    Only keys listed in ``merge_keys`` are promoted to visible context.
    Keys not found in raw_result are silently skipped.
    """
    patch: dict[str, Any] = {}
    source = raw_result.get("result", raw_result)
    for key in merge_keys:
        if key in source:
            patch[key] = source[key]
    return patch


# ---------------------------------------------------------------------------
# PublishTool
# ---------------------------------------------------------------------------


class PublishTool(BaseTool):
    """Procedure-only tool that publishes worker results to visible context.

    Reads from ``_pending_handoffs[handoff_id]``, optionally checks
    ``_review_results[handoff_id].approved``, merges specified keys,
    and signals memory flush via ``_publish_flush_texts`` (D9).
    """

    @property
    def name(self) -> str:
        return "procedure_publish"

    @property
    def description(self) -> str:
        return "Publish a worker result to visible context and memory"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "handoff_id": {
                    "type": "string",
                    "description": "ID of the handoff to publish",
                },
                "merge_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keys from worker result to promote to visible context",
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
            return {"ok": False, "error_code": "PUBLISH_NO_PROCEDURE_DEPS"}

        # Role guard: only primary can publish (D4)
        role_check = require_role(context.actor, AgentRole.primary)
        if not role_check.allowed:
            return {
                "ok": False,
                "error_code": "PUBLISH_ROLE_DENIED",
                "detail": role_check.detail,
            }

        deps = context.procedure_deps
        proc_ctx = deps.active_procedure.context
        handoff_id = arguments.get("handoff_id", "")
        merge_keys = tuple(arguments.get("merge_keys", ()))

        # Read from staging
        pending = dict(proc_ctx.get("_pending_handoffs", {}))
        worker_data = pending.get(handoff_id)
        if worker_data is None:
            return {
                "ok": False,
                "error_code": "PUBLISH_HANDOFF_NOT_FOUND",
                "detail": f"No pending handoff with id '{handoff_id}'",
            }

        # Optional review check
        reviews = proc_ctx.get("_review_results", {})
        review = reviews.get(handoff_id)
        if review is not None and not review.get("approved", False):
            return {
                "ok": False,
                "error_code": "PUBLISH_REVIEW_REJECTED",
                "detail": f"Review for handoff '{handoff_id}' not approved",
            }

        # Merge worker result keys into visible context
        visible_patch = merge_worker_result(worker_data, merge_keys, proc_ctx)

        # Remove published handoff from staging (read-modify-write)
        del pending[handoff_id]

        # Also clean review if present
        current_reviews = dict(proc_ctx.get("_review_results", {}))
        current_reviews.pop(handoff_id, None)

        # Build context_patch: visible keys + cleaned staging
        context_patch: dict[str, Any] = {
            **visible_patch,
            "_pending_handoffs": pending,
            "_review_results": current_reviews,
        }

        # Build flush texts for D9 signal
        flush_texts: list[str] = []
        if visible_patch:
            summary = json.dumps(visible_patch, ensure_ascii=False, default=str)
            flush_texts.append(f"Published worker result: {summary[:1000]}")

        logger.info(
            "publish_executed",
            handoff_id=handoff_id,
            merge_keys=list(merge_keys),
            flush_candidate_count=len(flush_texts),
        )

        return {
            "ok": True,
            "published_keys": list(visible_patch.keys()),
            "_publish_flush_texts": flush_texts,
            "context_patch": context_patch,
        }
