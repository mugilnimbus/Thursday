from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..context import ToolContext


TOOL_NAME = "get_current_datetime_location"
TOOL_ORDER = 52
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Get the current local date, local time, local datetime, weekday, timezone, UTC datetime, "
            "and configured location. Use for interpreting now, today, tomorrow, local weather, reminders, "
            "schedules, and time-sensitive tasks."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    timezone_name = context.config.reminder_timezone or "UTC"
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "UTC"
        local_zone = timezone.utc

    local_now = datetime.now(local_zone)
    utc_now = datetime.now(timezone.utc)
    return {
        "ok": True,
        "location": context.config.current_location,
        "timezone": timezone_name,
        "weekday": local_now.strftime("%A"),
        "local_date": local_now.strftime("%Y-%m-%d"),
        "local_time": local_now.strftime("%H:%M:%S"),
        "local_datetime": local_now.isoformat(timespec="seconds"),
        "utc_datetime": utc_now.isoformat(timespec="seconds"),
    }
