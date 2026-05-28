from __future__ import annotations

from typing import Any

from .context import ToolContext

TOOL_NAME = "create_reminder"
TOOL_ORDER = 90
REQUIRES_CONTAINER = False
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Create a scheduled reminder, to-do, recurring task, or future agent job. "
            "Every due reminder starts a normal Thursday LLM turn; it is never just a plain notification. "
            "Use schedule='once' for a one-time date+time, 'every_minutes' for every N minutes, "
            "'hourly' for every hour at a minute, 'daily' for every day at a time, "
            "'weekly' for selected weekdays at a time, and 'monthly' for a day-of-month at a time. "
            "For relative requests like 'in 20 minutes', compute the exact date and local time first, then use schedule='once'. "
            "Use the system timezone automatically; do not ask for or pass timezone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short reminder name."},
                "prompt": {"type": "string", "description": "The exact task Thursday should perform when due, e.g. 'Check today's Dublin weather and tell me before work'."},
                "schedule": {
                    "type": "string",
                    "description": "Schedule mode: once, every_minutes, hourly, daily, weekly, or monthly.",
                    "enum": ["once", "every_minutes", "hourly", "daily", "weekly", "monthly"],
                    "default": "daily",
                },
                "time": {
                    "type": "string",
                    "description": "Local time for once/daily/weekly/monthly, such as 10:00, 11:30, or 9pm. For hourly, use the minute as '15' or '00:15' to run at :15 each hour.",
                    "default": "09:00",
                },
                "date": {"type": "string", "description": "YYYY-MM-DD date. Required only for schedule='once'.", "default": ""},
                "weekdays": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]},
                    "description": "Required only for schedule='weekly'. Example: ['monday','wednesday','friday'].",
                    "default": [],
                },
                "interval_minutes": {
                    "type": "integer",
                    "description": "Required only for schedule='every_minutes'. Example: 30 means every 30 minutes.",
                    "default": 0,
                },
                "day_of_month": {
                    "type": "integer",
                    "description": "Required only for schedule='monthly'. 1-31; months with fewer days use that month's last day.",
                    "default": 0,
                },
            },
            "required": ["title", "prompt", "schedule"],
        },
    },
}


def run(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    if not context.reminder_store:
        return {"ok": False, "error": "Reminder store is not configured."}
    reminder = context.reminder_store.create_reminder(
        title=str(args["title"]),
        prompt=str(args["prompt"]),
        recurrence=str(args.get("schedule", args.get("recurrence", "daily"))),
        time_of_day=str(args.get("time", "09:00")),
        timezone_name=context.config.reminder_timezone,
        date_value=str(args.get("date", "")),
        weekdays=list(args.get("weekdays") or []),
        interval_minutes=int(args.get("interval_minutes", 0) or 0),
        day_of_month=int(args.get("day_of_month", 0) or 0),
        enabled=True,
    )
    return {"ok": True, "reminder": reminder.public()}
