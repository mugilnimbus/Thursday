from __future__ import annotations

from typing import Any

from ..context import ToolContext

TOOL_NAME = "list_reminders"
TOOL_ORDER = 100
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "List reminders stored by Thursday, including their ids, schedules, next run times, status, and prompts. Use before updating or deleting reminders, and when the user asks what reminders exist.",
        "parameters": {
            "type": "object",
            "properties": {
                "include_disabled": {"type": "boolean", "description": "Include completed or disabled reminders.", "default": True},
            },
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if not context.reminder_store:
        return {"ok": False, "error": "Reminder store is not configured."}
    include_disabled = bool(args.get("include_disabled", True))
    reminders = [reminder.public() for reminder in context.reminder_store.list_reminders(include_disabled=include_disabled)]
    return {"ok": True, "reminders": reminders, "count": len(reminders)}
