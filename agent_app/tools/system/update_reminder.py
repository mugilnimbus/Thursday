from __future__ import annotations

from typing import Any

from ..context import ToolContext

TOOL_NAME = "update_reminder"
TOOL_ORDER = 110
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Update a reminder by id. Use list_reminders first if the id is unknown. Accepts the same scheduling fields as create_reminder; only provide fields that should change.",
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Reminder id from list_reminders."},
                "title": {"type": "string", "description": "New short reminder name."},
                "prompt": {"type": "string", "description": "New reminder task prompt."},
                "schedule": {"type": "string", "enum": ["once", "every_minutes", "hourly", "daily", "weekly", "monthly"], "description": "New schedule mode."},
                "time": {"type": "string", "description": "New local time, or minute for hourly reminders."},
                "date": {"type": "string", "description": "New YYYY-MM-DD date for one-time reminders."},
                "weekdays": {"type": "array", "items": {"type": "string"}, "description": "New weekdays for weekly reminders."},
                "interval_minutes": {"type": "integer", "description": "New interval for every_minutes reminders."},
                "day_of_month": {"type": "integer", "description": "New day of month for monthly reminders."},
                "enabled": {"type": "boolean", "description": "Whether the reminder is active."},
            },
            "required": ["reminder_id"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if not context.reminder_store:
        return {"ok": False, "error": "Reminder store is not configured."}
    reminder_id = str(args["reminder_id"])
    updates = {key: value for key, value in args.items() if key != "reminder_id"}
    reminder = context.reminder_store.update_reminder(reminder_id, updates)
    return {"ok": True, "reminder": reminder.public()}
