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

        role_check = require_role(context.actor, AgentRole.primary)
        if not role_check.allowed:
            return {
                "ok": False,
                "error_code": "PUBLISH_ROLE_DENIED",
                "detail": role_check.detail,
            }

        proc_ctx = context.procedure_deps.active_procedure.context
        handoff_id = arguments.get("handoff_id", "")
        merge_keys = tuple(arguments.get("merge_keys", ()))

        err = self._resolve_handoff(proc_ctx, handoff_id)
        if err is not None:
            return err

        worker_data: dict[str, Any] = proc_ctx["_pending_handoffs"][handoff_id]
        err = self._validate_and_merge(worker_data, merge_keys, handoff_id)
        if err is not None:
            return err

        visible_patch = merge_worker_result(worker_data, merge_keys, proc_ctx)
        return self._build_success_result(proc_ctx, handoff_id, merge_keys, visible_patch)

    # -- private helpers (pure logic, no async needed) -------------------------

    def _resolve_handoff(
        self, proc_ctx: dict[str, Any], handoff_id: str
    ) -> dict[str, Any] | None:
        """Validate handoff exists and review (if any) is approved. Returns error dict or None."""
        pending = proc_ctx.get("_pending_handoffs", {})
        if handoff_id not in pending:
            return {
                "ok": False,
                "error_code": "PUBLISH_HANDOFF_NOT_FOUND",
                "detail": f"No pending handoff with id '{handoff_id}'",
            }

        review = proc_ctx.get("_review_results", {}).get(handoff_id)
        if review is not None and not review.get("approved", False):
            return {
                "ok": False,
                "error_code": "PUBLISH_REVIEW_REJECTED",
                "detail": f"Review for handoff '{handoff_id}' not approved",
            }
        return None

    def _validate_and_merge(
        self,
        worker_data: dict[str, Any],
        merge_keys: tuple[str, ...],
        handoff_id: str,
    ) -> dict[str, Any] | None:
        """Check merge_keys are non-empty and at least one matches. Returns error dict or None."""
        source = worker_data.get("result", worker_data)
        available_keys = list(source.keys()) if isinstance(source, dict) else []

        if not merge_keys:
            return {
                "ok": False,
                "error_code": "PUBLISH_EMPTY_MERGE_KEYS",
                "detail": "merge_keys must not be empty",
                "available_keys": available_keys,
                "handoff_id": handoff_id,
            }

        # Check that at least one key will match before committing to merge
        has_match = any(k in source for k in merge_keys) if isinstance(source, dict) else False
        if not has_match:
            return {
                "ok": False,
                "error_code": "PUBLISH_NO_KEYS_MATCHED",
                "detail": f"None of {list(merge_keys)} found in worker result",
                "available_keys": available_keys,
                "handoff_id": handoff_id,
            }
        return None

    def _build_success_result(
        self,
        proc_ctx: dict[str, Any],
        handoff_id: str,
        merge_keys: tuple[str, ...],
        visible_patch: dict[str, Any],
    ) -> dict[str, Any]:
        """Clean staging, build context_patch, and format the success response."""
        pending = dict(proc_ctx.get("_pending_handoffs", {}))
        del pending[handoff_id]

        current_reviews = dict(proc_ctx.get("_review_results", {}))
        current_reviews.pop(handoff_id, None)

        context_patch: dict[str, Any] = {
            **visible_patch,
            "_pending_handoffs": pending,
            "_review_results": current_reviews,
        }

        summary = json.dumps(visible_patch, ensure_ascii=False, default=str)
        flush_texts = [f"Published worker result: {summary[:1000]}"]

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
