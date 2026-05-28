from __future__ import annotations

from typing import Any

from .context import ToolContext

TOOL_NAME = "delete_reminder"
TOOL_ORDER = 120
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": "Delete a reminder by id. Use list_reminders first if the id is unknown. This permanently removes the scheduled job.",
        "parameters": {
            "type": "object",
            "properties": {
                "reminder_id": {"type": "string", "description": "Reminder id from list_reminders."},
            },
            "required": ["reminder_id"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if not context.reminder_store:
        return {"ok": False, "error": "Reminder store is not configured."}
    reminder_id = str(args["reminder_id"])
    deleted = context.reminder_store.delete_reminder(reminder_id)
    return {"ok": deleted, "deleted": reminder_id if deleted else "", "error": "" if deleted else f"Reminder not found: {reminder_id}"}
