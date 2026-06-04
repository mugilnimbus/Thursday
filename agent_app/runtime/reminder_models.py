from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Reminder:
    id: str
    title: str
    prompt: str
    recurrence: str
    time_of_day: str
    timezone_name: str
    date_value: str
    weekdays: list[str]
    interval_minutes: int
    day_of_month: int
    enabled: bool
    created_at: str
    updated_at: str
    next_run_at: str
    last_run_at: str
    run_count: int
    last_status: str
    last_error: str

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "recurrence": self.recurrence,
            "schedule": self.recurrence,
            "time": self.time_of_day,
            "timezone": self.timezone_name,
            "date": self.date_value,
            "weekdays": self.weekdays,
            "interval_minutes": self.interval_minutes,
            "day_of_month": self.day_of_month,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "run_count": self.run_count,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }
