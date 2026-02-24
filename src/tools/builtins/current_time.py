from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.tools.context import ToolContext


class CurrentTimeTool(BaseTool):
    """Returns the current date and time."""

    @property
    def name(self) -> str:
        return "current_time"

    @property
    def description(self) -> str:
        return "Get the current date and time, optionally in a specific timezone."

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.world

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe, ToolMode.coding})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.low

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": (
                        "IANA timezone name, e.g. 'Asia/Shanghai'. "
                        "Defaults to UTC."
                    ),
                },
            },
            "required": [],
        }

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        tz_name = arguments.get("timezone", "UTC")
        try:
            if tz_name == "UTC":
                tz = UTC
            else:
                tz = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, KeyError):
            return {"error_code": "INVALID_TIMEZONE", "message": f"Unknown timezone: {tz_name}"}

        now = datetime.now(tz)
        return {
            "time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": tz_name,
            "iso": now.isoformat(),
        }
